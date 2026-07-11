"""Unit tests for `llm_policy_library.api`."""

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import llm_policy_library.api as testee
from llm_policy_library.models import (
    GroundedResponse,
    PipelineResult,
    PlanStep,
    QueryPlan,
    RetrievalResult,
    RetrievedDocument,
)


def make_document(control_id: str = "ac-2", score: float = 2.2) -> RetrievedDocument:
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


def make_result(*, is_fallback: bool = False) -> PipelineResult:
    """Build a pipeline result for a successful or a fallback answer.

    Args:
        is_fallback: Whether to build the safe-fallback shape.

    Returns:
        The result.
    """
    plan = QueryPlan(
        original_query="What controls apply to API security?",
        steps=[PlanStep(search_query="api security", purpose="find controls")],
    )
    documents = [] if is_fallback else [make_document()]
    answer = GroundedResponse(
        answer="fallback message" if is_fallback else "Per [ac-2].",
        citations=[] if is_fallback else ["ac-2"],
        is_fallback=is_fallback,
    )
    return PipelineResult(
        plan=plan,
        results=[RetrievalResult(step=plan.steps[0], documents=documents)],
        documents=documents,
        response=answer,
    )


@pytest.fixture
def pipeline() -> Iterator[MagicMock]:
    """Install a mock pipeline as the `/query` dependency for one test.

    Overriding the dependency, rather than running `lifespan`, keeps these
    tests from needing real Azure credentials or opening a socket.
    """
    mock = MagicMock()
    mock.answer_query = AsyncMock()
    testee.app.dependency_overrides[testee.get_pipeline] = lambda: mock
    yield mock
    testee.app.dependency_overrides.clear()


@pytest.fixture
def client(pipeline: MagicMock) -> TestClient:
    """A TestClient wired to the mock pipeline fixture.

    Args:
        pipeline: The mock pipeline dependency-overridden for this test.

    Returns:
        The client.
    """
    return TestClient(testee.app)


async def test_lifespan_opens_the_pipeline_once_and_exposes_it_on_app_state() -> None:
    """Settings and Azure clients must be set up once at startup, never per request."""
    settings = MagicMock()
    fake_pipeline = MagicMock()

    @asynccontextmanager
    async def fake_open_pipeline(_settings: Any) -> AsyncIterator[MagicMock]:
        yield fake_pipeline

    with (
        patch.object(testee, "load_settings", return_value=settings) as load_settings,
        patch.object(testee, "configure_logging") as configure_logging,
        patch.object(testee, "open_pipeline", fake_open_pipeline),
    ):
        async with testee.lifespan(testee.app):
            assert testee.app.state.pipeline is fake_pipeline

    load_settings.assert_called_once_with()
    configure_logging.assert_called_once_with(settings.log_level)


async def test_get_pipeline_returns_the_pipeline_lifespan_stored_on_app_state() -> None:
    """The dependency must read the one pipeline `lifespan` opened, not build its own."""
    sentinel = MagicMock()
    testee.app.state.pipeline = sentinel
    try:
        assert await testee.get_pipeline() is sentinel
    finally:
        del testee.app.state.pipeline


def test_healthz_reports_ok(client: TestClient) -> None:
    """A container orchestration probe must succeed without touching the pipeline."""
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_query_returns_the_grounded_answer_and_its_evidence(
    client: TestClient, pipeline: MagicMock
) -> None:
    """A successful query must surface the answer, citations, plan, and evidence used."""
    pipeline.answer_query.return_value = make_result()

    response = client.post("/query", json={"query": "What controls apply to API security?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Per [ac-2]."
    assert body["citations"] == ["ac-2"]
    assert body["is_fallback"] is False
    assert body["plan"]["original_query"] == "What controls apply to API security?"
    assert [document["id"] for document in body["retrieved"]] == ["ac-2"]
    assert body["latency_ms"] >= 0
    pipeline.answer_query.assert_awaited_once_with("What controls apply to API security?")


def test_query_echoes_the_correlation_id_as_a_response_header(
    client: TestClient, pipeline: MagicMock
) -> None:
    """A client reporting a problem needs the ID to quote back for the audit trail to find it."""
    pipeline.answer_query.return_value = make_result()

    response = client.post("/query", json={"query": "What controls apply to API security?"})

    assert response.headers["X-Correlation-ID"]


def test_query_returns_the_safe_fallback_as_a_200_not_an_error(
    client: TestClient, pipeline: MagicMock
) -> None:
    """An off-topic question is a successful response with is_fallback=True, not a failure."""
    pipeline.answer_query.return_value = make_result(is_fallback=True)

    response = client.post("/query", json={"query": "What is the capital of France?"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_fallback"] is True
    assert body["retrieved"] == []


def test_query_maps_an_upstream_failure_to_502_without_leaking_its_detail(
    client: TestClient, pipeline: MagicMock
) -> None:
    """An Azure outage must reach the client as a safe fixed message, never the raw exception."""
    pipeline.answer_query.side_effect = RuntimeError("endpoint https://internal.example/ down")

    response = client.post("/query", json={"query": "What controls apply to API security?"})

    assert response.status_code == 502
    assert response.json() == {"detail": testee.SAFE_UPSTREAM_ERROR_MESSAGE}
    assert "internal.example" not in response.text
    assert response.headers["X-Correlation-ID"], "a failed request must stay traceable too"


def test_query_rejects_a_blank_query_with_422(client: TestClient, pipeline: MagicMock) -> None:
    """An empty question cannot be planned or searched, so it must never reach the pipeline."""
    response = client.post("/query", json={"query": ""})

    assert response.status_code == 422
    pipeline.answer_query.assert_not_awaited()


def test_query_rejects_a_whitespace_only_query_with_422(
    client: TestClient, pipeline: MagicMock
) -> None:
    """A query of only spaces is blank in every way that matters, not a real question."""
    response = client.post("/query", json={"query": "   "})

    assert response.status_code == 422
    pipeline.answer_query.assert_not_awaited()


def test_query_logs_the_incoming_request(
    client: TestClient, pipeline: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """TASK.md requires every request's input to be logged, ahead of the pipeline call."""
    pipeline.answer_query.return_value = make_result()

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        client.post("/query", json={"query": "What controls apply to API security?"})

    record = next(record for record in caplog.records if record.message == "request received")
    assert getattr(record, "query") == "What controls apply to API security?"


def test_query_logs_the_upstream_failure_with_a_traceback(
    client: TestClient, pipeline: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    """The full failure must be recoverable server-side even though the client sees only 502."""
    pipeline.answer_query.side_effect = RuntimeError("boom")

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        client.post("/query", json={"query": "What controls apply to API security?"})

    record = next(
        record for record in caplog.records if record.message == "upstream pipeline failure"
    )
    assert record.levelname == "ERROR"
    assert record.exc_info is not None
