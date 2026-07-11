"""Unit tests for `llm_policy_library.agents.planner`."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

import llm_policy_library.agents.planner as testee
from llm_policy_library.models import PlanStep, QueryPlan


def make_step(search_query: str) -> PlanStep:
    """Build a plan step.

    Args:
        search_query: The step's search query.

    Returns:
        The step.
    """
    return PlanStep(search_query=search_query, purpose=f"find {search_query}")


def planner_returning(value: Any) -> MagicMock:
    """Stub a Planner Agent whose structured output is `value`.

    Args:
        value: What `AgentResponse.value` yields, or an exception to raise.

    Returns:
        The stub agent.
    """
    response = MagicMock()
    if isinstance(value, Exception):
        type(response).value = property(lambda _: (_ for _ in ()).throw(value))
    else:
        response.value = value
    agent = MagicMock()
    agent.run = AsyncMock(return_value=response)
    return agent


def test_build_planner_requests_the_query_plan_schema_and_configured_effort() -> None:
    """Structured output is what replaces the temperature/seed knobs reasoning models reject."""
    chat_client = MagicMock()

    agent = testee.build_planner(chat_client, "minimal")

    options = agent.default_options
    assert options["response_format"] is QueryPlan
    assert options["reasoning"] == {"effort": "minimal"}


def test_planner_instructions_forbid_web_search_operators() -> None:
    """The search text hits an Azure AI Search index; `site:` syntax retrieves nothing."""
    assert "site:" in testee.PLANNER_INSTRUCTIONS
    assert "Never use search-engine operators" in testee.PLANNER_INSTRUCTIONS


def test_usable_steps_drops_a_step_with_a_blank_search_query() -> None:
    """An empty query cannot be embedded; retrieval would fail with an opaque HTTP 400."""
    steps = [make_step("access control"), PlanStep(search_query="   ", purpose="p")]

    kept = testee.usable_steps(steps)

    assert [step.search_query for step in kept] == ["access control"]


def test_clamp_steps_drops_steps_beyond_the_limit() -> None:
    """Every extra step costs an embedding and a search against the latency budget."""
    steps = [make_step(f"q{index}") for index in range(5)]

    kept = testee.clamp_steps(steps, limit=3)

    assert [step.search_query for step in kept] == ["q0", "q1", "q2"]


def test_clamp_steps_keeps_a_plan_already_within_the_limit() -> None:
    """A one-step plan is the common case and must pass through untouched."""
    steps = [make_step("access control")]

    assert testee.clamp_steps(steps, limit=3) == steps


async def test_plan_query_overwrites_the_models_echo_of_the_question() -> None:
    """A paraphrased `original_query` would misstate what the audit trail says was asked."""
    agent = planner_returning(
        QueryPlan(original_query="a paraphrase the model invented", steps=[make_step("q")])
    )

    plan = await testee.plan_query(agent, "What controls apply to API security?")

    assert plan.original_query == "What controls apply to API security?"


async def test_plan_query_clamps_an_over_eager_plan() -> None:
    """The model is asked for 1-3 steps; the limit is enforced here, not trusted to it."""
    agent = planner_returning(
        QueryPlan(original_query="q", steps=[make_step(f"q{index}") for index in range(6)])
    )

    plan = await testee.plan_query(agent, "q")

    assert len(plan.steps) == testee.MAX_PLAN_STEPS


async def test_plan_query_passes_the_users_question_to_the_agent() -> None:
    """The Planner reasons about the question itself, not a preprocessed form of it."""
    agent = planner_returning(QueryPlan(original_query="q", steps=[make_step("q")]))

    await testee.plan_query(agent, "How is sensitive data protected?")

    agent.run.assert_awaited_once_with("How is sensitive data protected?")


async def test_plan_query_raises_when_the_model_returns_no_plan() -> None:
    """Answering from an absent plan would search on nothing and ground on nothing."""
    agent = planner_returning(None)

    with pytest.raises(testee.PlannerError, match="no structured plan"):
        await testee.plan_query(agent, "q")


async def test_plan_query_raises_when_the_plan_has_no_steps() -> None:
    """A stepless plan retrieves nothing, which would silently look like a safe fallback."""
    agent = planner_returning(QueryPlan(original_query="q", steps=[]))

    with pytest.raises(testee.PlannerError, match="no steps"):
        await testee.plan_query(agent, "q")


async def test_plan_query_raises_when_every_step_has_a_blank_search_query() -> None:
    """A plan of blank queries is a planning failure, not an embeddings HTTP 400."""
    agent = planner_returning(
        QueryPlan(original_query="q", steps=[PlanStep(search_query=" ", purpose="p")])
    )

    with pytest.raises(testee.PlannerError, match="no steps"):
        await testee.plan_query(agent, "q")


async def test_plan_query_warns_when_the_model_exceeds_the_step_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silently clamping would hide a prompt or model regression, as an invented citation would."""
    agent = planner_returning(
        QueryPlan(original_query="q", steps=[make_step(f"q{index}") for index in range(5)])
    )

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        await testee.plan_query(agent, "q")

    assert any(record.levelno == logging.WARNING for record in caplog.records)


async def test_plan_query_wraps_a_schema_violation_as_a_planner_error() -> None:
    """A malformed structured output is a Planner failure, not an opaque pydantic error."""

    class _Other(BaseModel):
        value: int

    with pytest.raises(ValidationError) as schema_violation:
        _Other(value="not an int")  # type: ignore[arg-type]
    agent = planner_returning(schema_violation.value)

    with pytest.raises(testee.PlannerError, match="failed validation"):
        await testee.plan_query(agent, "q")
