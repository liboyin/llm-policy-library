"""Capture the two measurements the load test is judged against, as a committed artifact.

The load-test percentiles mean nothing without these two baselines, and both were
previously taken by hand and typed into the memo — which is exactly how the
discarded first attempt at this phase ended up quoting a quota it never read and
a latency floor that appeared in no artifact.

1. **Deployment quota.** Azure returns the deployment's TPM/RPM ceiling in the
   `x-ratelimit-*` response headers. It is the ceiling every capacity number in
   `samples/loadtest_results.md` is judged against.
2. **Single-request latency floor.** Requests issued strictly one at a time, so
   nothing queues and nothing throttles. No amount of quota or scaling makes a
   request faster than this.

Run it against a uvicorn already serving the app, and commit its output:

    python -m loadtest.baseline > samples/loadtest_baseline.txt
"""

import asyncio
import json
import time
import urllib.request
from typing import Final

import httpx

from llm_policy_library.config import load_settings
from loadtest.checks import ON_TOPIC_QUERIES, OUT_OF_DOMAIN_QUERIES

API_URL: Final = "http://127.0.0.1:8000/query"

# The quota probe reads the deployment's `x-ratelimit-limit-*` headers off the
# classic `chat/completions`/`embeddings` routes. Those routes need a *dated*
# api-version — the serving path's rolling `preview` alias belongs to the v1
# Responses API and 404s the classic routes (no deployment context, no headers).
# The rate limit is a property of the deployment, shared across every inference
# route, so a GA dated version reads the same ceiling the Responses API is subject
# to; this is a probe of infrastructure, not of the serving contract.
QUOTA_PROBE_API_VERSION: Final = "2024-10-21"

# Enough to see the spread without spending real money on a baseline.
ON_TOPIC_SAMPLES: Final = 6


async def report_quota(endpoint: str, api_key: str, deployment: str, payload: dict) -> None:
    """Print one deployment's rate-limit headers.

    Args:
        endpoint: Azure OpenAI endpoint.
        api_key: Data-plane key.
        deployment: The deployment to probe.
        payload: A minimal request body the deployment accepts.
    """
    route = "chat/completions" if "messages" in payload else "embeddings"
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{endpoint.rstrip('/')}/openai/deployments/{deployment}/{route}",
            params={"api-version": QUOTA_PROBE_API_VERSION},
            headers={"api-key": api_key},
            json=payload,
        )
    print(f"  {deployment} -> HTTP {response.status_code}")
    limits = sorted(key for key in response.headers if key.lower().startswith("x-ratelimit-limit"))
    for key in limits:
        print(f"    {key}: {response.headers[key]}")
    if not limits:
        print("    (deployment returned no x-ratelimit-limit-* headers)")


def time_query(query: str) -> tuple[float, bool]:
    """Send one query and time it end to end.

    Args:
        query: The question to ask.

    Returns:
        Wall-clock milliseconds, and whether the safe fallback fired.
    """
    request = urllib.request.Request(
        API_URL,
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=180) as response:
        body = json.load(response)
    return (time.perf_counter() - started) * 1000, bool(body["is_fallback"])


async def main() -> None:
    """Print the quota headers, then the serial latency floor."""
    settings = load_settings()
    api_key = settings.azure_openai_api_key.get_secret_value()

    print("=== deployment quota (x-ratelimit-limit-* headers, read live) ===")
    await report_quota(
        settings.azure_openai_endpoint,
        api_key,
        settings.azure_openai_chat_deployment,
        {"messages": [{"role": "user", "content": "ping"}], "max_completion_tokens": 16},
    )
    await report_quota(
        settings.azure_openai_endpoint,
        api_key,
        settings.azure_openai_embedding_deployment,
        {"input": ["ping"]},
    )

    print("\n=== single-request latency floor (strictly serial: no queueing, no throttling) ===")
    for index, query in enumerate(ON_TOPIC_QUERIES[:ON_TOPIC_SAMPLES]):
        elapsed_ms, fallback = time_query(query)
        # The first request pays the process's cold start; the rest are warm.
        label = "cold" if index == 0 else "warm"
        print(f"  [{label}] {elapsed_ms:8.0f} ms  fallback={fallback!s:5}  {query}")
    for query in OUT_OF_DOMAIN_QUERIES:
        elapsed_ms, fallback = time_query(query)
        print(f"  [warm] {elapsed_ms:8.0f} ms  fallback={fallback!s:5}  {query}")


if __name__ == "__main__":
    asyncio.run(main())
