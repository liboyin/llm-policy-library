"""Unit tests for `llm_policy_library.orchestrator`."""

import asyncio
import logging
import os
from collections.abc import Iterator
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

import llm_policy_library.orchestrator as testee
from llm_policy_library.config import Settings
from llm_policy_library.prompts import get_prompt
from llm_policy_library.models import (
    GroundedResponse,
    PlanStep,
    QueryPlan,
    RetrievalResult,
    RetrievedDocument,
)

SETTINGS_ENV = {
    "azure_openai_endpoint": "https://oai.example.com/",
    "azure_openai_api_key": "oai-key",
    "azure_openai_chat_deployment": "gpt-5-mini",
    "azure_openai_embedding_deployment": "text-embedding-3-small",
    "azure_search_endpoint": "https://search.example.net",
    "azure_search_api_key": "search-key",
    "azure_search_index_name": "nist-800-53-controls",
    "azure_search_semantic_ranker": True,
}


def make_settings(**overrides: Any) -> Settings:
    """Build a Settings object from literal values, bypassing the environment.

    Args:
        **overrides: Fields to override.

    Returns:
        The settings.
    """
    return Settings(**{**SETTINGS_ENV, **overrides})


@pytest.fixture(autouse=True)
def isolated_env() -> Iterator[None]:
    """Ambient AZURE_*/MIN_*/RETRIEVAL_* variables must not leak into settings built here."""
    with patch.dict(os.environ, {}, clear=True):
        yield


def make_chat_model() -> OpenAIChatModel:
    """Build a stand-in chat model the agents can be constructed over.

    `Agent` rejects a plain `MagicMock` at construction ("Unknown model"), so
    the stand-in is PydanticAI's own `TestModel`, cast to the declared type.

    Returns:
        The stand-in model.
    """
    return cast(OpenAIChatModel, TestModel())


def make_document(control_id: str, score: float = 2.2) -> RetrievedDocument:
    """Build a retrieved document.

    Args:
        control_id: The control's OSCAL ID.
        score: Its relevance score.

    Returns:
        The document.
    """
    return RetrievedDocument(
        id=control_id,
        title=f"Title of {control_id}",
        description=f"Statement of {control_id}",
        category="Access Control",
        score=score,
    )


def make_plan(query: str = "What controls apply to API security?") -> QueryPlan:
    """Build a one-step plan.

    Args:
        query: The user's question.

    Returns:
        The plan.
    """
    return QueryPlan(
        original_query=query, steps=[PlanStep(search_query="api security", purpose="find controls")]
    )


async def test_answer_query_runs_the_three_agents_in_order_and_returns_the_result() -> None:
    """The whole point of the pipeline: plan, then retrieve, then answer -- never reordered."""
    calls: list[str] = []
    plan = make_plan()
    documents = [make_document("ac-2")]
    answer = GroundedResponse(answer="Per [ac-2].", citations=["ac-2"], is_fallback=False)

    async def fake_plan_query(_agent: Any, _query: str) -> QueryPlan:
        calls.append("plan")
        return plan

    async def fake_retrieve_plan(*_args: Any) -> list[RetrievalResult]:
        calls.append("retrieve")
        return [RetrievalResult(step=plan.steps[0], documents=documents)]

    async def fake_generate_response(*_args: Any) -> GroundedResponse:
        calls.append("respond")
        return answer

    with (
        patch.object(testee, "plan_query", fake_plan_query),
        patch.object(testee, "retrieve_plan", fake_retrieve_plan),
        patch.object(testee, "generate_response", fake_generate_response),
    ):
        pipeline = testee.build_pipeline(make_settings(), make_chat_model(), MagicMock(), MagicMock())
        result = await pipeline.answer_query("What controls apply to API security?")

    assert calls == ["plan", "retrieve", "respond"]
    assert result.plan == plan
    assert result.documents == documents
    assert result.response == answer


async def test_answer_query_deduplicates_across_steps_before_grounding() -> None:
    """The Response Agent must see each control once, whichever steps surfaced it."""
    plan = make_plan()
    results = [
        RetrievalResult(step=plan.steps[0], documents=[make_document("ac-2", 2.1)]),
        RetrievalResult(step=plan.steps[0], documents=[make_document("ac-2", 2.4)]),
    ]
    answer = GroundedResponse(answer="Per [ac-2].", citations=["ac-2"], is_fallback=False)
    generate = AsyncMock(return_value=answer)

    with (
        patch.object(testee, "plan_query", AsyncMock(return_value=plan)),
        patch.object(testee, "retrieve_plan", AsyncMock(return_value=results)),
        patch.object(testee, "generate_response", generate),
    ):
        pipeline = testee.build_pipeline(make_settings(), make_chat_model(), MagicMock(), MagicMock())
        result = await pipeline.answer_query("What controls apply to API security?")

    grounding = generate.await_args_list[0].args[2]
    assert [(document.id, document.score) for document in grounding] == [("ac-2", 2.4)]
    assert result.results == results, "the per-step audit trail is kept alongside the merge"


async def test_answer_query_asks_the_response_agent_the_users_original_question() -> None:
    """The Planner's search terms are for the index; the model must answer the real question."""
    plan = make_plan()
    results = [RetrievalResult(step=plan.steps[0], documents=[make_document("ac-2")])]
    generate = AsyncMock(
        return_value=GroundedResponse(answer="a", citations=["ac-2"], is_fallback=False)
    )

    with (
        patch.object(testee, "plan_query", AsyncMock(return_value=plan)),
        patch.object(testee, "retrieve_plan", AsyncMock(return_value=results)),
        patch.object(testee, "generate_response", generate),
    ):
        pipeline = testee.build_pipeline(make_settings(), make_chat_model(), MagicMock(), MagicMock())
        await pipeline.answer_query("What controls apply to API security?")

    generate.assert_awaited_once()
    assert generate.await_args_list[0].args[1] == "What controls apply to API security?"


async def test_answer_query_logs_the_question_and_its_full_answer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TASK.md requires an audit line pairing each input with its output text, not just a length."""
    plan = make_plan()
    answer = GroundedResponse(answer="Per [ac-2].", citations=["ac-2"], is_fallback=False)

    async def fake_plan_query(_agent: Any, _query: str) -> QueryPlan:
        return plan

    async def fake_retrieve_plan(*_args: Any) -> list[RetrievalResult]:
        return [RetrievalResult(step=plan.steps[0], documents=[make_document("ac-2")])]

    async def fake_generate_response(*_args: Any) -> GroundedResponse:
        return answer

    with (
        patch.object(testee, "plan_query", fake_plan_query),
        patch.object(testee, "retrieve_plan", fake_retrieve_plan),
        patch.object(testee, "generate_response", fake_generate_response),
        caplog.at_level(logging.INFO, logger=testee.__name__),
    ):
        pipeline = testee.build_pipeline(make_settings(), make_chat_model(), MagicMock(), MagicMock())
        await pipeline.answer_query("What controls apply to API security?")

    record = next(record for record in caplog.records if record.message == "query answered")
    assert getattr(record, "query") == "What controls apply to API security?"
    assert getattr(record, "answer") == "Per [ac-2]."


async def test_answer_query_serves_concurrent_queries_on_one_pipeline() -> None:
    """The agents hold no per-run state, so one pipeline must serve overlapping queries."""
    answer = GroundedResponse(answer="Per [ac-2].", citations=["ac-2"], is_fallback=False)

    async def slow_plan_query(_agent: Any, query: str) -> QueryPlan:
        # Hold the first query open so the second one overlaps it.
        await asyncio.sleep(0.05)
        return make_plan(query)

    async def fake_retrieve_plan(
        _search: Any, _openai: Any, _settings: Any, plan: QueryPlan
    ) -> list[RetrievalResult]:
        return [RetrievalResult(step=plan.steps[0], documents=[make_document("ac-2")])]

    async def fake_generate_response(*_args: Any) -> GroundedResponse:
        return answer

    with (
        patch.object(testee, "plan_query", slow_plan_query),
        patch.object(testee, "retrieve_plan", fake_retrieve_plan),
        patch.object(testee, "generate_response", fake_generate_response),
    ):
        pipeline = testee.build_pipeline(make_settings(), make_chat_model(), MagicMock(), MagicMock())
        results = await asyncio.gather(
            pipeline.answer_query("first question"),
            pipeline.answer_query("second question"),
        )

    assert [result.plan.original_query for result in results] == [
        "first question",
        "second question",
    ], "each concurrent query must get its own result, not another's"


async def test_answer_query_returns_the_safe_fallback_when_nothing_clears_the_floor() -> None:
    """An off-topic question must reach the user as a refusal, not an invented control."""
    plan = make_plan("What is the capital of France?")

    async def fake_plan_query(_agent: Any, _query: str) -> QueryPlan:
        return plan

    async def fake_retrieve_plan(*_args: Any) -> list[RetrievalResult]:
        return [RetrievalResult(step=plan.steps[0], documents=[])]

    response_agent = MagicMock()
    response_agent.run = AsyncMock()

    # `generate_response` is deliberately left real: this asserts the whole
    # pipeline short-circuits, not merely that a mocked stage says it did.
    with (
        patch.object(testee, "plan_query", fake_plan_query),
        patch.object(testee, "retrieve_plan", fake_retrieve_plan),
        patch.object(testee, "build_response_agent", lambda *_: response_agent),
    ):
        pipeline = testee.build_pipeline(make_settings(), make_chat_model(), MagicMock(), MagicMock())
        result = await pipeline.answer_query("What is the capital of France?")

    assert result.response.is_fallback is True
    assert result.response.answer == get_prompt("safe_fallback_message")
    assert result.documents == []
    response_agent.run.assert_not_awaited()


async def test_open_pipeline_pins_chat_and_embeddings_to_their_own_api_versions() -> None:
    """One shared client cannot serve both: the chat and embeddings API contracts differ."""
    openai_client = MagicMock()
    openai_client.__aenter__ = AsyncMock(return_value=openai_client)
    openai_client.__aexit__ = AsyncMock(return_value=None)
    search_client = MagicMock()
    search_client.__aenter__ = AsyncMock(return_value=search_client)
    search_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(testee, "OpenAIChatModel", MagicMock(return_value=TestModel())),
        patch.object(testee, "AsyncAzureOpenAI", MagicMock(return_value=openai_client)) as azure,
        patch.object(testee, "SearchClient", MagicMock(return_value=search_client)),
    ):
        async with testee.open_pipeline(make_settings()) as pipeline:
            assert isinstance(pipeline, testee.PolicyPipeline)

    # The chat client is the first one `open_pipeline` opens, embeddings the second.
    assert azure.call_args_list[0].kwargs["api_version"] == testee.AZURE_OPENAI_CHAT_API_VERSION
    assert azure.call_args_list[1].kwargs["api_version"] == testee.AZURE_OPENAI_EMBEDDING_API_VERSION


async def test_open_pipeline_closes_the_clients_it_opened() -> None:
    """Entered once per process, it still must not leak the sockets it owns on exit."""
    openai_client = MagicMock()
    openai_client.__aenter__ = AsyncMock(return_value=openai_client)
    openai_client.__aexit__ = AsyncMock(return_value=None)
    search_client = MagicMock()
    search_client.__aenter__ = AsyncMock(return_value=search_client)
    search_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(testee, "OpenAIChatModel", MagicMock(return_value=TestModel())),
        patch.object(testee, "AsyncAzureOpenAI", MagicMock(return_value=openai_client)),
        patch.object(testee, "SearchClient", MagicMock(return_value=search_client)),
    ):
        async with testee.open_pipeline(make_settings()):
            pass

    # Both Azure OpenAI clients — chat and embeddings — share this mock.
    assert openai_client.__aexit__.await_count == 2
    search_client.__aexit__.assert_awaited_once()
