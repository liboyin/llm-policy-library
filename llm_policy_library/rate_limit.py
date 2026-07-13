"""Token-bucket rate limiting for the public `POST /query` endpoint.

The service is deployed publicly and deliberately has no authentication, so the
only thing standing between an anonymous caller and the Azure OpenAI bill is a
request budget. A query costs roughly two chat calls, and the chat deployment's
quota binds on requests per minute rather than tokens (see
`samples/loadtest_results.md`), so the budget is expressed in requests per
minute and enforced *before* the pipeline runs — a rejected request must not
reach a model.

Two budgets apply, and a request must clear both:

- **Per client.** Bounds any single abuser, and is what keeps one caller from
  starving everyone else.
- **Global, per process.** Bounds the worst case the per-client budget cannot:
  many distinct callers (a scanner, a shared NAT, a botnet) each individually
  under their limit. This is the budget that actually caps the spend.

Sustained rate vs burst
-----------------------
A limit of N per minute is a *sustained* rate, not a ceiling on any 60 seconds. A
bucket holds one bucketful (N) in reserve, so a process that has been idle admits
its full burst and *then* earns another N over the following minute: the worst
case in any 60s window is **2N**. This is deliberate — a burst is what lets a
person click three example questions in a row without being punished — but it
means the budgets must be sized against 2N. The defaults in `config.py` are.

Identifying the caller
----------------------
The caller is read from `X-Client-IP`, which the Azure App Service front end
**sets and overwrites** on every inbound request, so a client on the deployed
service cannot forge it. `X-Forwarded-For` is deliberately *not* used: App Service
appends to whatever the client sent, so its leftmost entry — the conventional
"real client" position — is attacker-controlled, and a limiter keyed on it could
be bypassed by rotating a header.

The header is trusted wherever it appears, which is sound **only behind a proxy
that overwrites it**. Off App Service — a local `uvicorn`, or any future host
without such a front end — a caller who sends the header themselves mints a fresh
per-client budget at will. That is an accepted risk rather than an oversight: this
service is deployed on App Service, a local run is not exposed, and the *global*
budget still bounds the spend even if the per-client one is evaded. A host without
an overwriting front end must strip the header at its edge.

State is per process, and the defaults assume **one worker process**. There is no
headroom for a second: the worst case already spends ~120 of the chat
deployment's 150 RPM, so N workers multiply the ceiling by N and blow the quota.
Run the service with one worker, or divide the budgets by the worker count. A
shared counter (Redis) is what a genuinely multi-instance deployment would need,
and is deliberately not built here.
"""

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

logger = logging.getLogger(__name__)

# Set and overwritten by the App Service front end, hence unforgeable. See the
# module docstring on why `X-Forwarded-For` is not used.
CLIENT_IP_HEADER: Final = "x-client-ip"

# Used when neither the header nor a socket peer identifies the caller. Such
# requests share one bucket, which is the safe direction: unattributable traffic
# is throttled together rather than each instance getting a fresh budget.
UNKNOWN_CLIENT: Final = "unknown"

# Above this many tracked clients, buckets that have refilled to capacity are
# dropped. A full bucket is indistinguishable from one that was never created,
# so evicting it changes no decision — it only reclaims the memory a long-lived
# public endpoint would otherwise leak, one entry per distinct caller.
MAX_TRACKED_CLIENTS: Final = 10_000

SECONDS_PER_MINUTE: Final = 60.0


@dataclass(frozen=True)
class Bucket:
    """A token bucket's state at one instant.

    Attributes:
        tokens: Requests still available to spend.
        updated: The `time.monotonic()` reading `tokens` was computed at.
    """

    tokens: float
    updated: float


def take(bucket: Bucket, now: float, capacity: int, per_second: float) -> tuple[Bucket, float]:
    """Refill a bucket for the elapsed time, then spend one token.

    Pure: `now` is passed in rather than read, so the whole refill-and-spend rule
    is tested at exact times without patching a clock.

    Args:
        bucket: The bucket's previous state.
        now: The current `time.monotonic()` reading.
        capacity: The most tokens the bucket may hold — the burst allowance.
        per_second: Tokens regained per second.

    Returns:
        `(bucket, retry_after)`. `retry_after` is `0.0` when the request is
        allowed, and otherwise the seconds until one token exists. The returned
        bucket has the token deducted only when the request is allowed; a
        rejected request must not spend what it was not given, or a caller who
        keeps hammering a closed door would never be let back in.
    """
    elapsed = max(0.0, now - bucket.updated)
    tokens = min(float(capacity), bucket.tokens + elapsed * per_second)
    if tokens >= 1.0:
        return Bucket(tokens=tokens - 1.0, updated=now), 0.0
    return Bucket(tokens=tokens, updated=now), (1.0 - tokens) / per_second


def client_key(headers: Mapping[str, str], peer: str | None) -> str:
    """Identify the caller a request is billed to.

    Trusts `X-Client-IP` whenever it is present, which is sound only behind a
    front end that overwrites it — see the module docstring on why that holds on
    App Service and what it costs anywhere else.

    Args:
        headers: The request's headers. Looked up case-insensitively when the
            mapping is Starlette's; the constant is already lower-case.
        peer: The socket peer address, used when no platform header is present.

    Returns:
        The caller's identity, or `UNKNOWN_CLIENT` if neither source names one.
    """
    forwarded = headers.get(CLIENT_IP_HEADER)
    if forwarded and forwarded.strip():
        return forwarded.strip()
    return peer or UNKNOWN_CLIENT


class RateLimiter:
    """Per-client and global request budgets over one process.

    Each budget is a token bucket whose capacity equals its per-minute limit, so
    the limit is a sustained rate and the worst case in any 60s window is twice
    it — see the module docstring.

    A limit of `0` disables that budget, which is what the load test needs: it
    drives far more traffic from one address than any human, so measuring the
    pipeline means measuring it without the guard in front.
    """

    def __init__(
        self, per_client_per_minute: int, global_per_minute: int, now: float | None = None
    ) -> None:
        """Set both budgets, each starting with a full bucket.

        Args:
            per_client_per_minute: Requests allowed per caller per minute; `0`
                disables the per-client budget.
            global_per_minute: Requests allowed per process per minute; `0`
                disables the global budget.
            now: The `time.monotonic()` reading to start the global bucket from.
                Defaults to the current one; passed explicitly by tests, so that
                the clock is a parameter here exactly as it is everywhere else in
                this module.
        """
        self._client_capacity = per_client_per_minute
        self._global_capacity = global_per_minute
        self._client_rate = per_client_per_minute / SECONDS_PER_MINUTE
        self._global_rate = global_per_minute / SECONDS_PER_MINUTE
        self._clients: dict[str, Bucket] = {}
        self._global = Bucket(
            tokens=float(global_per_minute),
            updated=time.monotonic() if now is None else now,
        )

    def check(self, client: str, now: float) -> float:
        """Spend one request from both budgets, if both can afford it.

        The budgets commit together or not at all. Spending from one while the
        other rejects would bill a caller for a request the service never ran.

        Args:
            client: The caller, from `client_key`.
            now: The current `time.monotonic()` reading.

        Returns:
            `0.0` when the request is allowed, otherwise the seconds after which
            it is worth retrying — the longer wait of the two budgets.
        """
        client_bucket, client_retry = self._peek_client(client, now)
        global_bucket, global_retry = self._peek_global(now)

        retry_after = max(client_retry, global_retry)
        if retry_after > 0.0:
            return retry_after

        if self._client_capacity > 0:
            self._clients[client] = client_bucket
            self._prune(now)
        if self._global_capacity > 0:
            self._global = global_bucket
        return 0.0

    def _peek_client(self, client: str, now: float) -> tuple[Bucket, float]:
        """Compute the per-client budget's verdict without committing it.

        Args:
            client: The caller, from `client_key`.
            now: The current `time.monotonic()` reading.

        Returns:
            `(bucket, retry_after)` as `take` defines them. A disabled budget
            always allows.
        """
        if self._client_capacity <= 0:
            return Bucket(tokens=0.0, updated=now), 0.0
        capacity = self._client_capacity
        bucket = self._clients.get(client, Bucket(tokens=float(capacity), updated=now))
        return take(bucket, now, capacity, self._client_rate)

    def _peek_global(self, now: float) -> tuple[Bucket, float]:
        """Compute the global budget's verdict without committing it.

        Args:
            now: The current `time.monotonic()` reading.

        Returns:
            `(bucket, retry_after)` as `take` defines them. A disabled budget
            always allows.
        """
        if self._global_capacity <= 0:
            return Bucket(tokens=0.0, updated=now), 0.0
        return take(self._global, now, self._global_capacity, self._global_rate)

    def _prune(self, now: float) -> None:
        """Drop tracked clients whose budget has fully refilled.

        Args:
            now: The current `time.monotonic()` reading.
        """
        if len(self._clients) <= MAX_TRACKED_CLIENTS:
            return
        capacity = float(self._client_capacity)
        self._clients = {
            client: bucket
            for client, bucket in self._clients.items()
            if min(capacity, bucket.tokens + (now - bucket.updated) * self._client_rate) < capacity
        }
