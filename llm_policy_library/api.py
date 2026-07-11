"""FastAPI service exposing the policy pipeline over HTTP.

`POST /query` is the one route that matters: it runs a question through the
same `PolicyPipeline` the CLI drives, so there is exactly one code path from a
question to a grounded answer. Settings are loaded and the Azure clients opened
once, in `lifespan`, not per request — a request handler that called
`load_settings()` itself would do blocking file I/O on the event loop and pay
for a fresh Azure client on every call.

Two guarantees hold at the boundary a client actually sees:

- A malformed request body (missing or blank `query`) never reaches the
  pipeline; FastAPI's own body validation answers with 422 first.
- A failure inside the pipeline — a planner or response error, or the Azure
  SDKs surfacing a live outage — is logged in full server-side and answered
  with a fixed 502 message. The client never sees the exception text or a
  stack trace, which could otherwise leak upstream service details.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Final

from fastapi import Depends, FastAPI, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from llm_policy_library.config import load_settings
from llm_policy_library.logging_setup import configure_logging, correlation_context
from llm_policy_library.models import QueryPlan, RetrievedDocument
from llm_policy_library.orchestrator import PolicyPipeline, open_pipeline

logger = logging.getLogger(__name__)

# Fixed and generic: an Azure error message can name internal resource IDs or
# endpoints, which must not reach the client.
SAFE_UPSTREAM_ERROR_MESSAGE: Final = (
    "The policy library is temporarily unavailable. Please try again."
)


class QueryRequest(BaseModel):
    """The body of `POST /query`.

    Attributes:
        query: The policy question to answer.
    """

    model_config = ConfigDict(frozen=True)

    # Stripped before the length check: a whitespace-only body must fail
    # validation here rather than reach the planner as an empty-looking query.
    query: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1),
        Field(description="The policy question to answer."),
    ]


class QueryResponse(BaseModel):
    """The body returned by `POST /query`.

    Attributes:
        answer: The grounded answer, or the fixed safe-fallback message.
        citations: Control IDs cited in `answer` that were actually retrieved.
        is_fallback: True when no chat model was called because nothing
            relevant was retrieved.
        plan: The Planner's decomposition of the question.
        retrieved: The deduplicated grounding set the answer was built from.
        latency_ms: End-to-end pipeline latency for this request.
    """

    model_config = ConfigDict(frozen=True)

    answer: str
    citations: list[str]
    is_fallback: bool
    plan: QueryPlan
    retrieved: list[RetrievedDocument]
    latency_ms: float


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Open the pipeline once per process and close it on shutdown.

    Args:
        app: The FastAPI application being started.

    Yields:
        Control, while the pipeline lives in `app.state.pipeline`.
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    async with open_pipeline(settings) as pipeline:
        app.state.pipeline = pipeline
        yield


app = FastAPI(title="llm-policy-library", lifespan=lifespan)


async def get_pipeline() -> PolicyPipeline:
    """Return the pipeline `lifespan` opened at startup.

    A thin indirection so tests can substitute a mock pipeline with
    `app.dependency_overrides` instead of running `lifespan`, which opens real
    Azure clients. Declared `async def`, not `def`: FastAPI runs a sync
    dependency in a worker thread pool, which is unnecessary overhead on every
    request for a plain attribute read.

    Returns:
        The process-wide pipeline.
    """
    return app.state.pipeline


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report liveness for container orchestration probes.

    Returns:
        A fixed status payload.
    """
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    response: Response,
    pipeline: Annotated[PolicyPipeline, Depends(get_pipeline)],
) -> QueryResponse:
    """Answer one policy question through the full agent pipeline.

    Args:
        request: The user's question.
        response: The outgoing response, used to echo the correlation ID.
        pipeline: The process-wide pipeline, injected so tests can substitute
            one without a real Azure connection.

    Returns:
        The grounded answer, its evidence, and the plan that produced it.

    Raises:
        HTTPException: 502 if the pipeline fails. The response body carries
            `SAFE_UPSTREAM_ERROR_MESSAGE`, never the underlying exception.
    """
    with correlation_context() as correlation_id:
        response.headers["X-Correlation-ID"] = correlation_id
        logger.info("request received", extra={"query": request.query})
        started = time.perf_counter()
        try:
            result = await pipeline.answer_query(request.query)
        except Exception:
            logger.exception("upstream pipeline failure", extra={"query": request.query})
            # `response.headers` only reaches the client on a normal return; an
            # HTTPException builds its own response, so the header is repeated
            # here to keep the correlation ID traceable on a failed request too.
            raise HTTPException(
                status_code=502,
                detail=SAFE_UPSTREAM_ERROR_MESSAGE,
                headers={"X-Correlation-ID": correlation_id},
            ) from None
        return QueryResponse(
            answer=result.response.answer,
            citations=result.response.citations,
            is_fallback=result.response.is_fallback,
            plan=result.plan,
            retrieved=result.documents,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
        )
