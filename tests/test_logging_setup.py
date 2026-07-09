"""Unit tests for `llm_policy_library.logging_setup`."""

import asyncio
import io
import json
import logging
import sys
from collections.abc import Iterator
from typing import Any

import pytest

import llm_policy_library.logging_setup as testee


@pytest.fixture
def isolated_root_logger() -> Iterator[logging.Logger]:
    """Restore the root logger's handlers and level after a test reconfigures it."""
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield root
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def make_record(message: str = "hello %s", **extra: Any) -> logging.LogRecord:
    """Build a log record carrying the given `extra=` fields.

    Args:
        message: Format string for the record.
        **extra: Caller-supplied fields, as the stdlib `extra=` mapping would add.

    Returns:
        A record equivalent to one produced by `Logger.info(..., extra=...)`.
    """
    record = logging.LogRecord(
        name="llm_policy_library.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=("world",),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_get_correlation_id_is_empty_outside_a_request() -> None:
    """Log lines emitted at startup must not inherit a stale request's ID."""
    assert testee.get_correlation_id() == ""


def test_correlation_context_generates_an_id_when_none_is_supplied() -> None:
    """An inbound request without a trace header still gets a traceable ID."""
    with testee.correlation_context() as correlation_id:
        assert correlation_id
        assert testee.get_correlation_id() == correlation_id


def test_correlation_context_binds_a_caller_supplied_id() -> None:
    """An upstream trace ID is honoured so logs stitch across service boundaries."""
    with testee.correlation_context("trace-abc") as correlation_id:
        assert correlation_id == "trace-abc"
        assert testee.get_correlation_id() == "trace-abc"


def test_correlation_context_restores_the_previous_id_on_exit() -> None:
    """Nested contexts must not clobber the enclosing request's ID."""
    with testee.correlation_context("outer"):
        with testee.correlation_context("inner"):
            assert testee.get_correlation_id() == "inner"
        assert testee.get_correlation_id() == "outer"

    assert testee.get_correlation_id() == ""


def test_correlation_context_resets_after_an_exception() -> None:
    """A failed request must not leak its correlation ID into the next one."""
    with pytest.raises(RuntimeError):
        with testee.correlation_context("doomed"):
            raise RuntimeError("boom")

    assert testee.get_correlation_id() == ""


def test_correlation_ids_are_isolated_between_concurrent_tasks() -> None:
    """Concurrently served requests must never see each other's correlation ID."""

    async def bound_id(name: str) -> str:
        with testee.correlation_context(name):
            await asyncio.sleep(0)  # yield, letting the sibling task interleave
            return testee.get_correlation_id()

    async def run_both() -> list[str]:
        # `gather` returns a list at runtime; typeshed narrows it to a tuple.
        return list(await asyncio.gather(bound_id("request-a"), bound_id("request-b")))

    assert asyncio.run(run_both()) == ["request-a", "request-b"]


def test_extra_fields_returns_only_caller_supplied_fields() -> None:
    """Stdlib record attributes must not pollute the audit payload."""
    record = make_record(query="access control", latency_ms=12.5)

    assert testee.extra_fields(record) == {"query": "access control", "latency_ms": 12.5}


def test_extra_fields_is_empty_for_a_plain_record() -> None:
    """A record with no `extra=` contributes nothing beyond the core fields."""
    assert testee.extra_fields(make_record()) == {}


def test_json_formatter_emits_a_single_json_line_with_core_fields() -> None:
    """Every line must be one parsable JSON object for log ingestion to work."""
    formatted = testee.JsonFormatter().format(make_record())

    assert "\n" not in formatted
    payload = json.loads(formatted)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "llm_policy_library.test"
    assert payload["message"] == "hello world", "args must be interpolated"
    assert payload["timestamp"].endswith("+00:00"), "timestamps must be UTC"


def test_json_formatter_omits_the_correlation_id_outside_a_request() -> None:
    """An unbound ID is absent rather than an empty string masquerading as one."""
    payload = json.loads(testee.JsonFormatter().format(make_record()))

    assert "correlation_id" not in payload


def test_json_formatter_attaches_the_bound_correlation_id() -> None:
    """Correlating a query with its response is the point of the audit trail."""
    with testee.correlation_context("trace-abc"):
        payload = json.loads(testee.JsonFormatter().format(make_record()))

    assert payload["correlation_id"] == "trace-abc"


def test_json_formatter_merges_extra_fields_into_the_payload() -> None:
    """Retrieved document IDs and scores must survive as structured JSON, not text."""
    record = make_record(retrieved=[{"id": "ac-2", "score": 0.031}], latency_ms=42)

    payload = json.loads(testee.JsonFormatter().format(record))

    assert payload["retrieved"] == [{"id": "ac-2", "score": 0.031}]
    assert payload["latency_ms"] == 42


def test_json_formatter_refuses_to_let_extra_fields_forge_the_audit_trail() -> None:
    """A caller's `extra=` must never overwrite the bound ID, level, or timestamp."""
    record = make_record(correlation_id="forged", level="TRACE", timestamp="1970-01-01")

    with testee.correlation_context("real-trace-id"):
        payload = json.loads(testee.JsonFormatter().format(record))

    assert payload["correlation_id"] == "real-trace-id"
    assert payload["level"] == "INFO"
    assert payload["timestamp"].startswith("20")


def test_json_formatter_stringifies_unserializable_values() -> None:
    """A logging call must never break a request by raising on an odd value."""

    class Opaque:
        def __repr__(self) -> str:
            return "<opaque>"

    payload = json.loads(testee.JsonFormatter().format(make_record(plan=Opaque())))

    assert payload["plan"] == "<opaque>"


def test_json_formatter_includes_the_exception_traceback() -> None:
    """Upstream Azure failures are diagnosed from the logged traceback."""
    try:
        raise ValueError("upstream exploded")
    except ValueError:
        record = make_record()
        record.exc_info = sys.exc_info()

    payload = json.loads(testee.JsonFormatter().format(record))

    assert "ValueError: upstream exploded" in payload["exception"]


def test_configure_logging_writes_json_lines_to_the_stream(
    isolated_root_logger: logging.Logger,
) -> None:
    """The end-to-end path from `logger.info(extra=...)` to a JSON line must work."""
    stream = io.StringIO()
    testee.configure_logging(level="INFO", stream=stream)

    with testee.correlation_context("trace-abc"):
        logging.getLogger("llm_policy_library.test").info("query", extra={"top_k": 5})

    payload = json.loads(stream.getvalue().strip())
    assert payload["message"] == "query"
    assert payload["top_k"] == 5
    assert payload["correlation_id"] == "trace-abc"


def test_configure_logging_honours_the_configured_level(
    isolated_root_logger: logging.Logger,
) -> None:
    """LOG_LEVEL must actually suppress lower-severity lines."""
    stream = io.StringIO()
    testee.configure_logging(level="WARNING", stream=stream)

    logger = logging.getLogger("llm_policy_library.test")
    logger.info("suppressed")
    logger.warning("emitted")

    assert "suppressed" not in stream.getvalue()
    assert "emitted" in stream.getvalue()


def test_configure_logging_is_idempotent(isolated_root_logger: logging.Logger) -> None:
    """A reloading server calls this repeatedly; log lines must not duplicate."""
    stream = io.StringIO()
    testee.configure_logging(level="INFO", stream=stream)
    testee.configure_logging(level="INFO", stream=stream)

    logging.getLogger("llm_policy_library.test").info("once")

    assert len(isolated_root_logger.handlers) == 1
    assert stream.getvalue().count('"message": "once"') == 1


def test_configure_logging_defaults_to_stdout(isolated_root_logger: logging.Logger) -> None:
    """Container log collectors read application logs from stdout."""
    testee.configure_logging()

    handler = isolated_root_logger.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    assert handler.stream is sys.stdout
