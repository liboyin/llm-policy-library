"""Unit tests for `llm_policy_library.orchestrator`."""

import asyncio
import logging
import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import llm_policy_library.orchestrator as testee
from llm_policy_library.agents.response import SAFE_FALLBACK_MESSAGE
from llm_policy_library.config import Settings
from llm_policy_library.models import (
    GroundedResponse,
    PipelineResult,
    PlanStep,
    QueryPlan,
    RetrievalOutcome,
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


def make_outcome(*documents: RetrievedDocument) -> RetrievalOutcome:
    """Build a retrieval outcome carrying `documents` as the grounding set.

    Args:
        *documents: The deduplicated grounding set.

    Returns:
        The outcome.
    """
    plan = make_plan()
    return RetrievalOutcome(
        plan=plan,
        results=[RetrievalResult(step=plan.steps[0], documents=list(documents))],
        documents=list(documents),
    )


class CapturingContext:
    """Records what an executor sends or yields, in place of a WorkflowContext."""

    def __init__(self) -> None:
        """Start with nothing captured."""
        self.sent: list[Any] = []
        self.yielded: list[Any] = []

    async def send_message(self, message: Any) -> None:
        """Capture a message sent to the next executor.

        Args:
            message: The message.
        """
        self.sent.append(message)

    async def yield_output(self, output: Any) -> None:
        """Capture the workflow's final output.

        Args:
            output: The output.
        """
        self.yielded.append(output)


async def test_planner_executor_forwards_the_plan_it_was_given() -> None:
    """The Planner executor adapts the agent to the workflow; it must not reinterpret it."""
    plan = make_plan()
    executor = testee.PlannerExecutor(MagicMock())
    context = CapturingContext()

    with patch.object(testee, "plan_query", AsyncMock(return_value=plan)):
        await executor.run("What controls apply to API security?", context)  # type: ignore[arg-type]

    assert context.sent == [plan]


async def test_retrieval_executor_deduplicates_across_steps_before_grounding() -> None:
    """The Response Agent must see each control once, whichever steps surfaced it."""
    plan = make_plan()
    results = [
        RetrievalResult(step=plan.steps[0], documents=[make_document("ac-2", 2.1)]),
        RetrievalResult(step=plan.steps[0], documents=[make_document("ac-2", 2.4)]),
    ]
    executor = testee.RetrievalExecutor(MagicMock(), MagicMock(), make_settings())
    context = CapturingContext()

    with patch.object(testee, "retrieve_plan", AsyncMock(return_value=results)):
        await executor.run(plan, context)  # type: ignore[arg-type]

    outcome = context.sent[0]
    assert [(document.id, document.score) for document in outcome.documents] == [("ac-2", 2.4)]
    assert outcome.results == results, "the per-step audit trail is kept alongside the merge"


async def test_response_executor_yields_the_plan_and_evidence_with_the_answer() -> None:
    """A compliance answer is auditable only if the plan and its evidence travel with it."""
    outcome = make_outcome(make_document("ac-2"))
    answer = GroundedResponse(answer="Per [ac-2].", citations=["ac-2"], is_fallback=False)
    executor = testee.ResponseExecutor(MagicMock())
    context = CapturingContext()

    with patch.object(testee, "generate_response", AsyncMock(return_value=answer)):
        await executor.run(outcome, context)  # type: ignore[arg-type]

    result = context.yielded[0]
    assert result == PipelineResult(
        plan=outcome.plan,
        results=outcome.results,
        documents=outcome.documents,
        response=answer,
    )


async def test_response_executor_asks_the_agent_the_users_original_question() -> None:
    """The Planner's search terms are for the index; the model must answer the real question."""
    outcome = make_outcome(make_document("ac-2"))
    generate = AsyncMock(
        return_value=GroundedResponse(answer="a", citations=["ac-2"], is_fallback=False)
    )
    executor = testee.ResponseExecutor(MagicMock())

    with patch.object(testee, "generate_response", generate):
        await executor.run(outcome, CapturingContext())  # type: ignore[arg-type]

    generate.assert_awaited_once()
    assert generate.await_args_list[0].args[1] == "What controls apply to API security?"


def test_build_workflow_starts_at_the_planner() -> None:
    """Retrieval before planning would search on the raw question and skip decomposition."""
    planner = testee.PlannerExecutor(MagicMock())
    retrieval = testee.RetrievalExecutor(MagicMock(), MagicMock(), make_settings())
    response = testee.ResponseExecutor(MagicMock())

    workflow = testee.build_workflow(planner, retrieval, response)

    assert workflow.get_start_executor().id == "planner"


async def test_answer_query_runs_the_three_agents_in_order_and_returns_the_result() -> None:
    """The whole point of the workflow: plan, then retrieve, then answer -- never reordered."""
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
        pipeline = testee.build_pipeline(make_settings(), MagicMock(), MagicMock(), MagicMock())
        result = await pipeline.answer_query("What controls apply to API security?")

    assert calls == ["plan", "retrieve", "respond"]
    assert result.plan == plan
    assert result.documents == documents
    assert result.response == answer


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
        pipeline = testee.build_pipeline(make_settings(), MagicMock(), MagicMock(), MagicMock())
        await pipeline.answer_query("What controls apply to API security?")

    record = next(record for record in caplog.records if record.message == "query answered")
    assert getattr(record, "query") == "What controls apply to API security?"
    assert getattr(record, "answer") == "Per [ac-2]."


async def test_answer_query_serves_concurrent_queries_on_one_pipeline() -> None:
    """A Workflow rejects a second concurrent run, so answer_query must build one per query."""
    answer = GroundedResponse(answer="Per [ac-2].", citations=["ac-2"], is_fallback=False)

    async def slow_plan_query(_agent: Any, query: str) -> QueryPlan:
        # Hold the workflow open so the second query overlaps the first.
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
        pipeline = testee.build_pipeline(make_settings(), MagicMock(), MagicMock(), MagicMock())
        results = await asyncio.gather(
            pipeline.answer_query("first question"),
            pipeline.answer_query("second question"),
        )

    assert [result.plan.original_query for result in results] == [
        "first question",
        "second question",
    ], "each concurrent query must get its own workflow run, not another's result"


async def test_answer_query_returns_the_safe_fallback_when_nothing_clears_the_floor() -> None:
    """An off-topic question must reach the user as a refusal, not an invented control."""
    plan = make_plan("What is the capital of France?")

    async def fake_plan_query(_agent: Any, _query: str) -> QueryPlan:
        return plan

    async def fake_retrieve_plan(*_args: Any) -> list[RetrievalResult]:
        return [RetrievalResult(step=plan.steps[0], documents=[])]

    chat_agent = MagicMock()
    chat_agent.run = AsyncMock()

    # `generate_response` is deliberately left real: this asserts the whole
    # pipeline short-circuits, not merely that a mocked stage says it did.
    with (
        patch.object(testee, "plan_query", fake_plan_query),
        patch.object(testee, "retrieve_plan", fake_retrieve_plan),
        patch.object(testee, "build_response_agent", lambda *_: chat_agent),
    ):
        pipeline = testee.build_pipeline(make_settings(), MagicMock(), MagicMock(), MagicMock())
        result = await pipeline.answer_query("What is the capital of France?")

    assert result.response.is_fallback is True
    assert result.response.answer == SAFE_FALLBACK_MESSAGE
    assert result.documents == []
    chat_agent.run.assert_not_awaited()


async def test_answer_query_raises_when_the_workflow_yields_no_result() -> None:
    """A workflow that stops early must fail loudly, not return an empty answer."""
    workflow = MagicMock()
    workflow.run = AsyncMock(return_value=MagicMock(get_outputs=MagicMock(return_value=[])))
    pipeline = testee.PolicyPipeline(MagicMock(), MagicMock(), MagicMock())

    with patch.object(testee, "build_workflow", MagicMock(return_value=workflow)):
        with pytest.raises(testee.OrchestrationError, match="no result"):
            await pipeline.answer_query("q")


async def test_open_pipeline_pins_chat_and_embeddings_to_their_own_api_versions() -> None:
    """One shared client cannot serve both: the Responses API and the embeddings API differ."""
    openai_client = MagicMock()
    openai_client.__aenter__ = AsyncMock(return_value=openai_client)
    openai_client.__aexit__ = AsyncMock(return_value=None)
    search_client = MagicMock()
    search_client.__aenter__ = AsyncMock(return_value=search_client)
    search_client.__aexit__ = AsyncMock(return_value=None)
    chat_client = MagicMock()

    with (
        patch.object(testee, "OpenAIChatClient", MagicMock(return_value=chat_client)) as chat,
        patch.object(testee, "AsyncAzureOpenAI", MagicMock(return_value=openai_client)) as embed,
        patch.object(testee, "SearchClient", MagicMock(return_value=search_client)),
    ):
        async with testee.open_pipeline(make_settings()) as pipeline:
            assert isinstance(pipeline, testee.PolicyPipeline)

    assert chat.call_args.kwargs["api_version"] == testee.AZURE_OPENAI_CHAT_API_VERSION
    assert embed.call_args.kwargs["api_version"] == testee.AZURE_OPENAI_EMBEDDING_API_VERSION


async def test_open_pipeline_closes_the_clients_it_opened() -> None:
    """Entered once per process, it still must not leak the sockets it owns on exit."""
    openai_client = MagicMock()
    openai_client.__aenter__ = AsyncMock(return_value=openai_client)
    openai_client.__aexit__ = AsyncMock(return_value=None)
    search_client = MagicMock()
    search_client.__aenter__ = AsyncMock(return_value=search_client)
    search_client.__aexit__ = AsyncMock(return_value=None)

    with (
        patch.object(testee, "OpenAIChatClient", MagicMock()),
        patch.object(testee, "AsyncAzureOpenAI", MagicMock(return_value=openai_client)),
        patch.object(testee, "SearchClient", MagicMock(return_value=search_client)),
    ):
        async with testee.open_pipeline(make_settings()):
            pass

    openai_client.__aexit__.assert_awaited_once()
    search_client.__aexit__.assert_awaited_once()
