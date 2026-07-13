"""FastAPI service exposing the policy pipeline over HTTP.

`POST /query` is the one route that matters: it runs a question through the
same `PolicyPipeline` the CLI drives, so there is exactly one code path from a
question to a grounded answer. Settings are loaded and the Azure clients opened
once, in `lifespan`, not per request — a request handler that called
`load_settings()` itself would do blocking file I/O on the event loop and pay
for a fresh Azure client on every call.

`GET /` serves the browser frontend, a single static page that calls `/query`
like any other client. It is a view over the same JSON, not a third code path.

Four guarantees hold at the boundary a client actually sees:

- A caller over its request budget is rejected with 429 *before* any Azure call,
  so an anonymous public endpoint cannot be turned into someone else's bill. See
  `llm_policy_library.rate_limit`; only `POST /query` is metered, because only it
  spends money — throttling the page or the health probe would protect nothing
  and break the platform's own liveness checks.
- A malformed request body (missing, blank, or over-long `query`) never reaches
  the pipeline; FastAPI's body validation answers with 422. Note the order: the
  rate-limit dependency runs *before* that validation, so a malformed request
  from an over-budget caller is answered 429, and a malformed request from a
  caller in good standing still spends a token. Both are intended — garbage is
  traffic, and traffic is what the budget is there to bound.
- A pipeline that runs too long is abandoned at `REQUEST_TIMEOUT_SECONDS` and
  answered with 504, so one stuck upstream call cannot hold a worker open
  indefinitely.
- A failure inside the pipeline — a planner or response error, or the Azure
  SDKs surfacing a live outage — is logged in full server-side and answered
  with a fixed 502 message. The client never sees the exception text or a
  stack trace, which could otherwise leak upstream service details.
"""

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Final

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from llm_policy_library.config import load_settings
from llm_policy_library.logging_setup import configure_logging, correlation_context
from llm_policy_library.models import QueryPlan, RetrievedDocument
from llm_policy_library.orchestrator import PolicyPipeline, open_pipeline
from llm_policy_library.rate_limit import RateLimiter, client_key

logger = logging.getLogger(__name__)

# Fixed and generic: an Azure error message can name internal resource IDs or
# endpoints, which must not reach the client.
SAFE_UPSTREAM_ERROR_MESSAGE: Final = (
    "The policy library is temporarily unavailable. Please try again."
)

SAFE_TIMEOUT_MESSAGE: Final = "The policy library took too long to answer. Please try again."

RATE_LIMITED_MESSAGE: Final = "Too many requests. Please wait a moment and try again."

# A policy question is a sentence, not a payload. The cap is what stops one caller
# from having a megabyte embedded and sent to a chat model at the owner's expense;
# the body is still read before validation, so this bounds cost, not ingress (a
# reverse proxy's body-size limit is what bounds that).
MAX_QUERY_LENGTH: Final = 2_000

# Resolved from the package, not the working directory — the same reasoning as
# `config.DEFAULT_ENV_FILE`: `uvicorn` launched from anywhere must find the page.
FRONTEND_PATH: Final = Path(__file__).resolve().parent.parent / "static" / "index.html"


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
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_QUERY_LENGTH),
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
        # One limiter per process, so its buckets are shared across every request
        # this worker serves. Building it per request would give each caller a
        # fresh budget, which is no budget at all.
        app.state.rate_limiter = RateLimiter(
            per_client_per_minute=settings.rate_limit_per_ip_per_minute,
            global_per_minute=settings.rate_limit_global_per_minute,
        )
        app.state.request_timeout_seconds = settings.request_timeout_seconds
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


async def get_rate_limiter() -> RateLimiter:
    """Return the limiter `lifespan` built at startup.

    Returns:
        The process-wide rate limiter.
    """
    return app.state.rate_limiter


async def get_request_timeout() -> float:
    """Return the pipeline timeout `lifespan` read from settings.

    The value, not the whole `Settings` object: the handler needs one number, and
    a narrower dependency is a narrower thing for a test to substitute.

    Returns:
        Seconds `POST /query` waits for the pipeline.
    """
    return app.state.request_timeout_seconds


async def enforce_rate_limit(
    request: Request,
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Reject a caller that is over its request budget.

    Runs as a route dependency, so it is checked before the handler body and
    therefore before any Azure call: a throttled request must cost nothing.

    Args:
        request: The incoming request, for the caller's identity.
        limiter: The process-wide rate limiter.

    Raises:
        HTTPException: 429 when either the per-caller or the global budget is
            exhausted, carrying `Retry-After` so a well-behaved client backs off
            for the right amount of time rather than guessing.
    """
    client = client_key(request.headers, request.client.host if request.client else None)
    retry_after = limiter.check(client, time.monotonic())
    if retry_after <= 0.0:
        return

    logger.warning(
        "rate limit exceeded",
        extra={"client": client, "retry_after_seconds": round(retry_after, 1)},
    )
    raise HTTPException(
        status_code=429,
        detail=RATE_LIMITED_MESSAGE,
        # Whole seconds, rounded up: `Retry-After` is an integer, and rounding
        # down would invite a retry that is still a fraction of a second early.
        headers={"Retry-After": str(math.ceil(retry_after))},
    )


@app.get("/", response_class=FileResponse)
async def frontend() -> FileResponse:
    """Serve the browser frontend.

    Returns:
        The static page, which calls `POST /query` from the browser.

    Raises:
        HTTPException: 404 if the page is absent. `static/` sits beside the
            package rather than inside it, so a non-editable install ships
            without it; an explicit 404 beats an opaque 500 from the file layer.
    """
    if not FRONTEND_PATH.is_file():
        raise HTTPException(status_code=404, detail="The frontend page is not installed.")
    # `FileResponse` sets an ETag but does not answer conditional requests, so without this
    # a browser may heuristically cache the page and serve a stale one after an upgrade.
    return FileResponse(FRONTEND_PATH, headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Report liveness for container orchestration probes.

    Returns:
        A fixed status payload.
    """
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse, dependencies=[Depends(enforce_rate_limit)])
async def query(
    request: QueryRequest,
    response: Response,
    pipeline: Annotated[PolicyPipeline, Depends(get_pipeline)],
    timeout_seconds: Annotated[float, Depends(get_request_timeout)],
) -> QueryResponse:
    """Answer one policy question through the full agent pipeline.

    Args:
        request: The user's question.
        response: The outgoing response, used to echo the correlation ID.
        pipeline: The process-wide pipeline, injected so tests can substitute
            one without a real Azure connection.
        timeout_seconds: How long to wait for the pipeline before giving up.

    Returns:
        The grounded answer, its evidence, and the plan that produced it.

    Raises:
        HTTPException: 504 if the pipeline outran `timeout_seconds`, 502 if it
            failed. Both bodies carry a fixed safe message, never the underlying
            exception.
    """
    with correlation_context() as correlation_id:
        response.headers["X-Correlation-ID"] = correlation_id
        logger.info("request received", extra={"query": request.query})
        started = time.perf_counter()
        deadline = asyncio.timeout(timeout_seconds)
        try:
            async with deadline:
                result = await pipeline.answer_query(request.query)
        except Exception as error:
            # Only *our own* deadline firing is a 504. An SDK read timeout raised
            # from inside the pipeline is a `TimeoutError` too, but it is an
            # upstream failure; calling it "the pipeline outran timeout_seconds"
            # would send whoever reads the audit trail after the wrong bug.
            if isinstance(error, TimeoutError) and deadline.expired():
                logger.warning(
                    "pipeline timed out",
                    extra={"query": request.query, "timeout_seconds": timeout_seconds},
                )
                raise HTTPException(
                    status_code=504,
                    detail=SAFE_TIMEOUT_MESSAGE,
                    headers={"X-Correlation-ID": correlation_id},
                ) from None
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
