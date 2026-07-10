"""Structured JSON logging with per-request correlation IDs.

Every log line is emitted as a single JSON object so that queries, retrieved
document IDs and scores, responses, and latencies are machine-readable in an
audit trail. Anything passed via the stdlib `extra=` keyword is merged into the
JSON payload, and the correlation ID of the enclosing request is attached
automatically.

The correlation ID lives in a `ContextVar`, so it propagates through `await`
boundaries and stays isolated between concurrently served requests.
"""

import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any, Final, TextIO

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")

# Attributes the stdlib puts on every LogRecord. Anything else on a record came
# from a caller's `extra=` mapping. Derived from a throwaway record rather than
# hard-coded so that new stdlib attributes never leak into the JSON payload.
_STANDARD_RECORD_ATTRS: Final[frozenset[str]] = frozenset(
    logging.LogRecord(
        name="", level=logging.NOTSET, pathname="", lineno=0, msg="", args=(), exc_info=None
    ).__dict__
) | {"message", "asctime"}

# Keys the formatter owns. The stdlib only stops `extra=` from shadowing real
# LogRecord attributes, so without this a caller could pass
# `extra={"correlation_id": ...}` and forge the audit trail.
_RESERVED_PAYLOAD_KEYS: Final[frozenset[str]] = frozenset(
    {"timestamp", "level", "logger", "message", "correlation_id", "exception"}
)

# Libraries that log a line — or, for the Azure SDK, a full request and response
# header dump — on every HTTP call they make. At INFO they emit an order of
# magnitude more lines than the application does, burying the request audit
# trail this module exists to produce. Their warnings and errors still surface.
#
# `openai` is deliberately absent: its per-request chatter is DEBUG (and its
# HTTP traffic surfaces through `httpx` anyway), while "Retrying request to ..."
# is INFO. Silencing it would hide a run that only succeeded after retrying.
_NOISY_LIBRARY_LOGGERS: Final[tuple[str, ...]] = ("azure", "httpx")


def get_correlation_id() -> str:
    """Return the correlation ID bound to the current context.

    Returns:
        The active correlation ID, or an empty string outside any request.
    """
    return _correlation_id.get()


@contextmanager
def correlation_context(correlation_id: str | None = None) -> Iterator[str]:
    """Bind a correlation ID for the duration of the block.

    Args:
        correlation_id: ID to bind. A random UUID4 is generated when omitted,
            which is the usual case for an inbound request without a trace header.

    Yields:
        The bound correlation ID, so callers can echo it back to the client.
    """
    bound = correlation_id or str(uuid.uuid4())
    token = _correlation_id.set(bound)
    try:
        yield bound
    finally:
        _correlation_id.reset(token)


def extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    """Extract the caller-supplied `extra=` fields from a log record.

    Args:
        record: The record being formatted.

    Returns:
        Every non-standard attribute on the record.
    """
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _STANDARD_RECORD_ATTRS
    }


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Render a record as a JSON object.

        Caller-supplied `extra=` fields are merged in, except for the reserved
        keys the formatter owns, which cannot be overridden. Values that are not
        JSON-serializable are stringified rather than raising, so a logging call
        can never take down a request.

        Args:
            record: The record to render.

        Returns:
            A single-line JSON string.
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        correlation_id = get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        payload.update(
            {
                key: value
                for key, value in extra_fields(record).items()
                if key not in _RESERVED_PAYLOAD_KEYS
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Install the JSON formatter on the root logger.

    Replaces any existing root handlers, so calling this more than once (for
    example under a reloading server) does not duplicate log lines.

    Chatty HTTP libraries are pinned to WARNING unless the root level is DEBUG,
    in which case an operator has explicitly asked to see everything.

    Args:
        level: Root log level name, e.g. "INFO".
        stream: Destination for log lines. Defaults to stdout, the conventional
            sink for application logs in a container.
    """
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # NOTSET restores inheritance from the root logger, so switching back to
    # DEBUG on a later call re-enables the libraries this silenced.
    library_level = logging.NOTSET if root.level <= logging.DEBUG else logging.WARNING
    for name in _NOISY_LIBRARY_LOGGERS:
        logging.getLogger(name).setLevel(library_level)
