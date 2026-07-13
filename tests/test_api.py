"""Unit tests for `llm_policy_library.api`."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
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
from llm_policy_library.rate_limit import RateLimiter


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
    # The rate limiter is off unless a test asks for it: every other test is
    # about the pipeline, and a budget silently throttling them would make the
    # suite's failures depend on how many requests a test happened to send.
    # One instance, not one per call — an override runs per request, so building
    # a limiter inside the lambda would hand every request a fresh budget.
    disabled = RateLimiter(per_client_per_minute=0, global_per_minute=0)
    testee.app.dependency_overrides[testee.get_rate_limiter] = lambda: disabled
    testee.app.dependency_overrides[testee.get_request_timeout] = lambda: 60.0
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
    # Values distinct from every default, so the assertions below fail if the
    # limiter is built from anything other than the settings that were loaded.
    settings = MagicMock(
        rate_limit_per_ip_per_minute=7,
        rate_limit_global_per_minute=21,
        request_timeout_seconds=12.5,
    )
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
            # One limiter for the process, holding the buckets every request of
            # this worker is counted against — and built from the configured
            # budgets, not from hardcoded ones. Asserting only its type would
            # pass even if `lifespan` ignored the settings entirely.
            limiter = testee.app.state.rate_limiter
            assert isinstance(limiter, RateLimiter)
            assert limiter._client_capacity == 7
            assert limiter._global_capacity == 21
            assert testee.app.state.request_timeout_seconds == 12.5

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


async def test_get_rate_limiter_returns_the_limiter_lifespan_stored_on_app_state() -> None:
    """The budget must be the process-wide one; a per-request limiter is no budget at all."""
    sentinel = MagicMock()
    testee.app.state.rate_limiter = sentinel
    try:
        assert await testee.get_rate_limiter() is sentinel
    finally:
        del testee.app.state.rate_limiter


async def test_get_request_timeout_returns_the_configured_timeout() -> None:
    """The wait must come from configuration, so it can be tuned without a code change."""
    testee.app.state.request_timeout_seconds = 12.5
    try:
        assert await testee.get_request_timeout() == 12.5
    finally:
        del testee.app.state.request_timeout_seconds


def test_enforce_rate_limit_throttles_a_caller_over_its_budget(
    client: TestClient, pipeline: MagicMock
) -> None:
    """The endpoint is public and every call spends money, so an over-budget caller must be refused."""
    pipeline.answer_query.return_value = make_result()
    limiter = RateLimiter(per_client_per_minute=1, global_per_minute=0)
    testee.app.dependency_overrides[testee.get_rate_limiter] = lambda: limiter
    body = {"query": "What controls apply to API security?"}

    first = client.post("/query", json=body)
    second = client.post("/query", json=body)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": testee.RATE_LIMITED_MESSAGE}
    # The whole point: the refused request never reached a model, so it cost nothing.
    pipeline.answer_query.assert_awaited_once()


def test_enforce_rate_limit_tells_a_throttled_caller_when_to_come_back(
    client: TestClient, pipeline: MagicMock
) -> None:
    """Without `Retry-After` a well-behaved client can only guess, and guessing means retrying too early."""
    pipeline.answer_query.return_value = make_result()
    limiter = RateLimiter(per_client_per_minute=1, global_per_minute=0)
    testee.app.dependency_overrides[testee.get_rate_limiter] = lambda: limiter
    body = {"query": "What controls apply to API security?"}

    client.post("/query", json=body)
    throttled = client.post("/query", json=body)

    assert int(throttled.headers["Retry-After"]) > 0


def test_frontend_path_resolves_to_the_shipped_page() -> None:
    """The page must be found from the package, so `uvicorn` serves it from any directory."""
    assert testee.FRONTEND_PATH.is_absolute()
    assert testee.FRONTEND_PATH.is_file()


def test_frontend_serves_the_page(client: TestClient, tmp_path: Path) -> None:
    """The root route must serve the page itself: it is how an end user reaches the system."""
    page = tmp_path / "index.html"
    page.write_text("<!DOCTYPE html><title>Policy Library</title>")

    with patch.object(testee, "FRONTEND_PATH", page):
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.text == "<!DOCTYPE html><title>Policy Library</title>"
    # `FileResponse` answers no conditional request, so without this a browser may
    # heuristically cache the page and serve a stale one after an upgrade.
    assert response.headers["cache-control"] == "no-cache"


def test_frontend_reports_404_when_the_page_is_absent(client: TestClient, tmp_path: Path) -> None:
    """An uninstalled page must be an explicit 404, not an opaque 500 from the file layer."""
    with patch.object(testee, "FRONTEND_PATH", tmp_path / "absent.html"):
        response = client.get("/")

    assert response.status_code == 404


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


def test_query_maps_a_stuck_pipeline_to_504(client: TestClient, pipeline: MagicMock) -> None:
    """A hung Azure call must not hold a worker open until the client gives up; the server quits first."""

    async def never_finishes(_query: str) -> PipelineResult:
        await asyncio.sleep(30)
        raise AssertionError("the timeout should have fired long before this")

    pipeline.answer_query.side_effect = never_finishes
    testee.app.dependency_overrides[testee.get_request_timeout] = lambda: 0.01

    response = client.post("/query", json={"query": "What controls apply to API security?"})

    assert response.status_code == 504
    assert response.json() == {"detail": testee.SAFE_TIMEOUT_MESSAGE}
    assert response.headers["X-Correlation-ID"], "a timed-out request must stay traceable too"


def test_query_maps_a_timeout_from_inside_the_pipeline_to_502_not_504(
    client: TestClient, pipeline: MagicMock
) -> None:
    """An SDK read timeout is an upstream failure, not our deadline: calling it 504 would send an auditor after the wrong bug."""
    pipeline.answer_query.side_effect = TimeoutError("azure read timed out")

    response = client.post("/query", json={"query": "What controls apply to API security?"})

    assert response.status_code == 502
    assert response.json() == {"detail": testee.SAFE_UPSTREAM_ERROR_MESSAGE}


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


def test_query_rejects_an_over_long_query_with_422(
    client: TestClient, pipeline: MagicMock
) -> None:
    """A policy question is a sentence; a megabyte of text must be refused before it is embedded and billed."""
    response = client.post("/query", json={"query": "x" * (testee.MAX_QUERY_LENGTH + 1)})

    assert response.status_code == 422
    pipeline.answer_query.assert_not_awaited()


def test_query_accepts_a_query_at_the_length_limit(
    client: TestClient, pipeline: MagicMock
) -> None:
    """The cap must sit above any real question, so it never refuses a legitimate one."""
    pipeline.answer_query.return_value = make_result()

    response = client.post("/query", json={"query": "x" * testee.MAX_QUERY_LENGTH})

    assert response.status_code == 200


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
