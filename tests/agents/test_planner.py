"""Unit tests for `llm_policy_library.agents.planner`."""

import logging
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import NativeOutput, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

import llm_policy_library.agents.planner as testee
from llm_policy_library.models import PlannerOutput, PlanStep
from llm_policy_library.prompts import get_prompt


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
        value: What `AgentRunResult.output` holds, or an exception `run` raises.

    Returns:
        The stub agent.
    """
    agent = MagicMock()
    if isinstance(value, Exception):
        agent.run = AsyncMock(side_effect=value)
    else:
        agent.run = AsyncMock(return_value=MagicMock(output=value))
    return agent


def test_build_planner_requests_the_searches_only_schema_and_configured_effort() -> None:
    """The model's schema is PlannerOutput (searches only), so it never echoes the question."""
    agent = testee.build_planner(cast(OpenAIChatModel, TestModel()), "minimal")

    # `NativeOutput`, not the bare model: the default output mode would move
    # schema enforcement into a synthetic tool call instead of the provider's
    # native structured outputs.
    assert isinstance(agent.output_type, NativeOutput)
    assert agent.output_type.outputs is PlannerOutput
    # The exact key matters: PydanticAI silently drops settings keys it does
    # not recognize, so a misspelt key would run at the model's default effort.
    assert agent.model_settings == {"openai_reasoning_effort": "minimal"}


def test_planner_instructions_forbid_web_search_operators() -> None:
    """The search text hits an Azure AI Search index; `site:` syntax retrieves nothing."""
    instructions = get_prompt("planner_instructions", max_plan_steps=testee.MAX_PLAN_STEPS)
    assert "site:" in instructions
    assert "Never use search-engine operators" in instructions


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


async def test_plan_query_sets_original_query_to_the_true_input() -> None:
    """The model never returns the question, so the Planner sets it from the real input."""
    agent = planner_returning(PlannerOutput(steps=[make_step("q")]))

    plan = await testee.plan_query(agent, "What controls apply to API security?")

    assert plan.original_query == "What controls apply to API security?"


async def test_plan_query_clamps_an_over_eager_plan() -> None:
    """The model is asked for 1-3 steps; the limit is enforced here, not trusted to it."""
    agent = planner_returning(
        PlannerOutput(steps=[make_step(f"q{index}") for index in range(6)])
    )

    plan = await testee.plan_query(agent, "q")

    assert len(plan.steps) == testee.MAX_PLAN_STEPS


async def test_plan_query_logs_each_step_with_its_purpose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`PlanStep.purpose` is recorded for the audit trail, so it must reach the log line."""
    agent = planner_returning(
        PlannerOutput(
            steps=[PlanStep(search_query="access control", purpose="find the AC family")]
        )
    )

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.plan_query(agent, "q")

    record = next(record for record in caplog.records if record.message == "query planned")
    assert getattr(record, "steps") == [
        {"search_query": "access control", "purpose": "find the AC family"}
    ]


async def test_plan_query_passes_the_users_question_to_the_agent() -> None:
    """The Planner reasons about the question itself, not a preprocessed form of it."""
    agent = planner_returning(PlannerOutput(steps=[make_step("q")]))

    await testee.plan_query(agent, "How is sensitive data protected?")

    agent.run.assert_awaited_once_with("How is sensitive data protected?")


async def test_plan_query_wraps_a_model_misbehavior_as_a_planner_error() -> None:
    """A model that cannot produce the schema is a Planner failure, not a library error."""
    agent = planner_returning(UnexpectedModelBehavior("Exceeded maximum retries"))

    with pytest.raises(testee.PlannerError, match="valid plan"):
        await testee.plan_query(agent, "q")


async def test_plan_query_raises_when_the_plan_has_no_steps() -> None:
    """A stepless plan retrieves nothing, which would silently look like a safe fallback."""
    agent = planner_returning(PlannerOutput(steps=[]))

    with pytest.raises(testee.PlannerError, match="no steps"):
        await testee.plan_query(agent, "q")


async def test_plan_query_raises_when_every_step_has_a_blank_search_query() -> None:
    """A plan of blank queries is a planning failure, not an embeddings HTTP 400."""
    agent = planner_returning(
        PlannerOutput(steps=[PlanStep(search_query=" ", purpose="p")])
    )

    with pytest.raises(testee.PlannerError, match="no steps"):
        await testee.plan_query(agent, "q")


async def test_plan_query_warns_when_the_model_exceeds_the_step_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silently clamping would hide a prompt or model regression, as an invented citation would."""
    agent = planner_returning(
        PlannerOutput(steps=[make_step(f"q{index}") for index in range(5)])
    )

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        await testee.plan_query(agent, "q")

    assert any(record.levelno == logging.WARNING for record in caplog.records)
