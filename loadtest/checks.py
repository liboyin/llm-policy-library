"""The load test's query mix and its pass/fail rules.

Separate from `locustfile.py` for one hard reason and one good one.

The hard reason: importing `locust` monkey-patches `ssl` via gevent, which raises
`RecursionError` inside a pytest process that has already imported `ssl`. Any
logic that needs a unit test therefore cannot live in a module that imports
locust. Keeping it here is what makes it testable at all.

The good one: these predicates decide whether a request counts as a failure, and
they are what produce the run's headline numbers. An inverted condition here
would turn a broken run green — the same class of error as a memo that contradicts
its own data. They are pure functions over the response body so a plain unit test
can drive every branch.
"""

import gzip
import json
from pathlib import Path
from typing import Any, Final

from llm_policy_library.evaluation import load_golden_set

_REPO_ROOT: Final = Path(__file__).resolve().parent.parent
GOLDEN_SET_PATH: Final = _REPO_ROOT / "evaluation" / "golden_set.json"

# The load mix is the evaluation's own golden set, not a second list invented
# here: the queries whose answers were graded are the queries whose latency is
# measured, so difficulty is the same in both, and TASK.md's four questions are
# covered by construction.
_GOLDEN_SET: Final = load_golden_set(GOLDEN_SET_PATH)
ON_TOPIC_QUERIES: Final = [query.query for query in _GOLDEN_SET if not query.expect_fallback]
OUT_OF_DOMAIN_QUERIES: Final = [query.query for query in _GOLDEN_SET if query.expect_fallback]

# Locust keys its statistics by request *name*, not URL. Without two names the
# on-topic and out-of-domain traffic land in one distribution, and they are not
# one population: an on-topic question runs Planner -> Retrieval -> Response and
# costs seconds, while an out-of-domain one is answered by the safe fallback in a
# fraction of that. Blending them yields a median that describes neither, and the
# SLA is about answering a policy question.
ON_TOPIC_NAME: Final = "/query [on-topic]"
OUT_OF_DOMAIN_NAME: Final = "/query [out-of-domain]"


def on_topic_defect(body: dict[str, Any], query: str) -> str | None:
    """Judge whether a 200 response to an on-topic question is actually degraded.

    An HTTP 200 is not success. Under throttling or a retrieval regression the
    pipeline still answers 200 while falling back or citing nothing — a degraded
    answer that a status-code-only check reports as a healthy request.

    Args:
        body: The decoded `POST /query` response.
        query: The question that was asked, for the failure message.

    Returns:
        A failure message, or None if the answer is sound.
    """
    if body["is_fallback"]:
        return f"degraded: safe fallback returned for on-topic query {query!r}"
    if not body["citations"]:
        return f"degraded: answer cited no controls for on-topic query {query!r}"
    return None


def out_of_domain_defect(body: dict[str, Any], query: str) -> str | None:
    """Judge whether a 200 response to an out-of-domain question is degraded.

    `is_fallback` is false when the Planner's search phrase happened to retrieve
    something above the relevance floor, so the deterministic template never fired
    and the Response Agent answered instead. The answer is still grounded — it
    refuses in prose — so this is **not** a hallucination, and the message must not
    claim one. It is the known non-deterministic fallback, counted here so its rate
    is measured under load rather than assumed.

    Args:
        body: The decoded `POST /query` response.
        query: The question that was asked, for the failure message.

    Returns:
        A failure message, or None if the fallback fired as it should.
    """
    if not body["is_fallback"]:
        return f"fallback did not fire for out-of-domain query {query!r}"
    return None


def summarize_run(log_path: Path, golden_set_path: Path = GOLDEN_SET_PATH) -> dict[str, Any]:
    """Aggregate one load run's audit trail into the numbers the memo reports.

    Requests are classed by **which golden-set query was asked**, not by the
    `is_fallback` flag on the response. Those are not the same partition: an
    out-of-domain request that failed to fall back carries `is_fallback=false`,
    and classing by the flag would file it — and its full two-call pipeline cost —
    under "on-topic", inflating that class's token and latency figures.

    Args:
        log_path: The server's JSON audit trail (plain or gzipped).
        golden_set_path: The golden set the run was driven from.

    Returns:
        Per-class means (`chat_tokens`, `chat_calls`, `searches`, `embedding_tokens`,
        `n`) under keys `on_topic`, `out_of_domain`, and `blended`, plus the
        `fallback_misses` count and the run's `total` request count.
    """
    golden = load_golden_set(golden_set_path)
    on_topic = {query.query for query in golden if not query.expect_fallback}

    opener = gzip.open if log_path.suffix == ".gz" else open
    requests: dict[str, dict[str, Any]] = {}
    with opener(log_path, "rt") as handle:
        for line in handle:
            if not line.startswith("{"):
                continue
            record = json.loads(line)
            correlation_id = record.get("correlation_id")
            if not correlation_id:
                continue
            entry = requests.setdefault(
                correlation_id,
                {"chat_tokens": 0, "chat_calls": 0, "searches": 0, "embedding_tokens": 0},
            )
            message = record.get("message")
            if message in ("query planned", "answer generated"):
                entry["chat_tokens"] += record.get("input_tokens", 0)
                entry["chat_tokens"] += record.get("output_tokens", 0)
                entry["chat_calls"] += 1
            elif message == "step retrieved":
                entry["searches"] += 1
                entry["embedding_tokens"] += record.get("embedding_tokens", 0)
            elif message == "query answered":
                entry["query"] = record.get("query")
                entry["is_fallback"] = record.get("is_fallback")

    complete = [entry for entry in requests.values() if "query" in entry]
    classes = {
        "on_topic": [entry for entry in complete if entry["query"] in on_topic],
        "out_of_domain": [entry for entry in complete if entry["query"] not in on_topic],
        "blended": complete,
    }
    fields = ("chat_tokens", "chat_calls", "searches", "embedding_tokens")
    summary: dict[str, Any] = {
        name: {
            "n": len(rows),
            **{
                field: (sum(row[field] for row in rows) / len(rows) if rows else 0.0)
                for field in fields
            },
        }
        for name, rows in classes.items()
    }
    summary["fallback_misses"] = sum(
        1 for entry in classes["out_of_domain"] if not entry["is_fallback"]
    )
    summary["total"] = len(complete)
    return summary
