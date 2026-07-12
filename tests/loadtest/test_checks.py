"""Unit tests for `loadtest.checks`."""

import gzip
import json
from pathlib import Path
from typing import Any

import loadtest.checks as testee

TASK_MD_QUERIES = {
    "What controls apply to API security?",
    "How should sensitive data be protected in cloud systems?",
    "Summarise requirements for access control",
    "What policies relate to logging and monitoring?",
}


def make_body(is_fallback: bool = False, citations: list[str] | None = None) -> dict[str, Any]:
    """Build a `POST /query` response body.

    Args:
        is_fallback: Whether the safe fallback fired.
        citations: The control IDs the answer cited.

    Returns:
        The body.
    """
    return {"is_fallback": is_fallback, "citations": citations or []}


def write_log(path: Path, records: list[dict[str, Any]]) -> Path:
    """Write records as the gzipped JSON-lines audit trail the server emits.

    Args:
        path: Where to write.
        records: The log records, in order.

    Returns:
        `path`.
    """
    with gzip.open(path, "wt") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def request_records(
    correlation_id: str,
    query: str,
    is_fallback: bool,
    chat_calls: int,
    input_tokens: int = 100,
    output_tokens: int = 10,
    searches: int = 1,
    embedding_tokens: int = 5,
) -> list[dict[str, Any]]:
    """Build the audit-trail records one request emits.

    Args:
        correlation_id: The request's correlation ID.
        query: The question asked.
        is_fallback: Whether the safe fallback fired.
        chat_calls: How many chat calls the request made.
        input_tokens: Prompt tokens per chat call.
        output_tokens: Completion tokens per chat call.
        searches: How many plan steps ran.
        embedding_tokens: Embedding tokens per step.

    Returns:
        The records.
    """
    records = [
        {
            "correlation_id": correlation_id,
            "message": "query planned" if index == 0 else "answer generated",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        for index in range(chat_calls)
    ]
    records += [
        {
            "correlation_id": correlation_id,
            "message": "step retrieved",
            "embedding_tokens": embedding_tokens,
        }
        for _ in range(searches)
    ]
    records.append(
        {
            "correlation_id": correlation_id,
            "message": "query answered",
            "query": query,
            "is_fallback": is_fallback,
        }
    )
    return records


def test_golden_set_splits_into_two_non_empty_disjoint_classes() -> None:
    """The load mix is the graded golden set, so both classes must actually be populated."""
    on_topic = set(testee.ON_TOPIC_QUERIES)
    out_of_domain = set(testee.OUT_OF_DOMAIN_QUERIES)

    assert on_topic and out_of_domain
    assert not on_topic & out_of_domain


def test_load_mix_covers_every_query_task_md_asks_about() -> None:
    """A load test that never sends the assessment's own four questions measures the wrong thing."""
    assert TASK_MD_QUERIES <= set(testee.ON_TOPIC_QUERIES)


def test_the_two_classes_are_reported_under_different_locust_names() -> None:
    """Locust keys stats by name; one name blends two populations into an unreadable median."""
    assert testee.ON_TOPIC_NAME != testee.OUT_OF_DOMAIN_NAME


def test_on_topic_defect_passes_a_grounded_cited_answer() -> None:
    """The healthy case must not be flagged, or the failure count means nothing."""
    assert testee.on_topic_defect(make_body(citations=["ac-2"]), "q") is None


def test_on_topic_defect_fails_a_fallback_to_a_policy_question() -> None:
    """A 200 carrying the safe fallback is a retrieval collapse a status check would call healthy."""
    defect = testee.on_topic_defect(make_body(is_fallback=True), "access control?")

    assert defect is not None
    assert "safe fallback" in defect


def test_on_topic_defect_fails_an_answer_that_cites_nothing() -> None:
    """An uncited answer is ungrounded by definition, however plausible its prose."""
    defect = testee.on_topic_defect(make_body(citations=[]), "access control?")

    assert defect is not None
    assert "cited no controls" in defect


def test_out_of_domain_defect_passes_when_the_fallback_fired() -> None:
    """The fallback firing is the correct outcome and must not be counted as a failure."""
    assert testee.out_of_domain_defect(make_body(is_fallback=True), "q") is None


def test_out_of_domain_defect_fails_when_the_fallback_did_not_fire() -> None:
    """The deterministic fallback is a guarantee; a request bypassing it is the thing being counted."""
    defect = testee.out_of_domain_defect(make_body(is_fallback=False), "capital of France?")

    assert defect is not None
    assert "fallback did not fire" in defect


def test_out_of_domain_defect_does_not_claim_a_hallucination() -> None:
    """The model refuses in prose when the template misses, so calling it a grounding failure
    overstates the defect — the exact mislabelling this phase exists to stop."""
    defect = testee.out_of_domain_defect(make_body(is_fallback=False), "q")

    assert defect is not None
    assert "grounding" not in defect.lower()
    assert "hallucinat" not in defect.lower()


def test_summarize_run_classes_a_non_fallback_out_of_domain_request_by_its_query(
    tmp_path: Path,
) -> None:
    """Classing by `is_fallback` files an out-of-domain request that answered under "on-topic",
    inflating that class with a full two-call pipeline it never ran — the bug this pins."""
    on_topic_query = testee.ON_TOPIC_QUERIES[0]
    out_of_domain_query = testee.OUT_OF_DOMAIN_QUERIES[0]
    log = write_log(
        tmp_path / "run.log.gz",
        request_records("a", on_topic_query, is_fallback=False, chat_calls=2)
        # Out of domain, but the fallback did not fire: `is_fallback` is False and
        # it ran the full two-call pipeline, exactly like an on-topic request.
        + request_records("b", out_of_domain_query, is_fallback=False, chat_calls=2)
        + request_records("c", out_of_domain_query, is_fallback=True, chat_calls=1),
    )

    summary = testee.summarize_run(log)

    assert summary["on_topic"]["n"] == 1
    assert summary["out_of_domain"]["n"] == 2
    assert summary["fallback_misses"] == 1


def test_summarize_run_sums_tokens_and_calls_per_request(tmp_path: Path) -> None:
    """The memo's capacity numbers are these means; a mis-sum here misprices the whole SLA."""
    log = write_log(
        tmp_path / "run.log.gz",
        request_records(
            "a",
            testee.ON_TOPIC_QUERIES[0],
            is_fallback=False,
            chat_calls=2,
            input_tokens=600,
            output_tokens=250,
            searches=2,
            embedding_tokens=6,
        ),
    )

    summary = testee.summarize_run(log)

    assert summary["on_topic"]["chat_tokens"] == 1700  # 2 calls x (600 + 250)
    assert summary["on_topic"]["chat_calls"] == 2
    assert summary["on_topic"]["searches"] == 2
    assert summary["on_topic"]["embedding_tokens"] == 12
    assert summary["total"] == 1


def test_summarize_run_ignores_a_request_with_no_completion_record(tmp_path: Path) -> None:
    """A request still in flight at the cutoff has no answer, and averaging it in would understate cost."""
    log = write_log(
        tmp_path / "run.log.gz",
        request_records("a", testee.ON_TOPIC_QUERIES[0], is_fallback=False, chat_calls=2)
        + [{"correlation_id": "b", "message": "query planned", "input_tokens": 500}],
    )

    summary = testee.summarize_run(log)

    assert summary["total"] == 1
