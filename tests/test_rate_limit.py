"""Unit tests for `llm_policy_library.rate_limit`."""

from unittest.mock import patch

import llm_policy_library.rate_limit as testee

# A monotonic reading has an arbitrary origin, so the tests pick one and move
# forward from it explicitly rather than reading, or patching, a clock.
NOW = 10_000.0


def test_take_spends_a_token_when_the_bucket_has_one() -> None:
    """A caller inside its budget must be served, and must be billed for it."""
    bucket = testee.Bucket(tokens=3.0, updated=NOW)

    updated, retry_after = testee.take(bucket, NOW, capacity=10, per_second=1.0)

    assert retry_after == 0.0
    assert updated.tokens == 2.0


def test_take_refills_the_bucket_for_the_elapsed_time() -> None:
    """The budget must recover as time passes, or a caller would be throttled forever."""
    bucket = testee.Bucket(tokens=0.0, updated=NOW)

    updated, retry_after = testee.take(bucket, NOW + 5.0, capacity=10, per_second=1.0)

    assert retry_after == 0.0
    # 5 seconds at 1/s refills 5 tokens, one of which this request then spends.
    assert updated.tokens == 4.0


def test_take_caps_the_refill_at_capacity() -> None:
    """An idle caller must not bank an unbounded burst; capacity is the burst ceiling."""
    bucket = testee.Bucket(tokens=0.0, updated=NOW)

    updated, _ = testee.take(bucket, NOW + 3600.0, capacity=10, per_second=1.0)

    assert updated.tokens == 9.0, "an hour idle still buys only one bucketful"


def test_take_denies_an_empty_bucket_and_says_when_to_retry() -> None:
    """A caller over budget must be told how long to wait, not left to guess."""
    bucket = testee.Bucket(tokens=0.0, updated=NOW)

    _, retry_after = testee.take(bucket, NOW, capacity=10, per_second=0.5)

    assert retry_after == 2.0, "one token at 0.5/s is two seconds away"


def test_take_charges_nothing_for_a_denied_request() -> None:
    """A refused caller must not be billed, or hammering a closed door would hold it shut."""
    bucket = testee.Bucket(tokens=0.0, updated=NOW)

    once, first_wait = testee.take(bucket, NOW + 1.0, capacity=10, per_second=0.5)
    twice, second_wait = testee.take(once, NOW + 1.0, capacity=10, per_second=0.5)

    assert once.tokens == twice.tokens == 0.5, "neither refusal spent the part-token it found"
    assert first_wait == second_wait == 1.0, "so the wait stays honest instead of resetting"


def test_client_key_prefers_the_platform_header() -> None:
    """App Service overwrites `X-Client-IP`, making it the one identity a caller cannot forge."""
    key = testee.client_key({testee.CLIENT_IP_HEADER: "203.0.113.7"}, peer="10.0.0.1")

    assert key == "203.0.113.7"


def test_client_key_ignores_a_forwarded_for_header() -> None:
    """App Service appends to `X-Forwarded-For`, so trusting it would let a caller rotate identities."""
    key = testee.client_key({"x-forwarded-for": "1.2.3.4"}, peer="10.0.0.1")

    assert key == "10.0.0.1", "the socket peer beats a header the caller controls"


def test_client_key_falls_back_to_the_socket_peer() -> None:
    """With no platform header, the socket peer is all that names the caller."""
    assert testee.client_key({}, peer="198.51.100.9") == "198.51.100.9"


def test_client_key_buckets_an_unidentifiable_caller() -> None:
    """An unattributable request must share one bucket, not be handed a fresh unlimited one."""
    assert testee.client_key({}, peer=None) == testee.UNKNOWN_CLIENT


def test_check_allows_a_caller_inside_both_budgets() -> None:
    """The limiter exists to shape abuse, not to stand in the way of ordinary use."""
    limiter = testee.RateLimiter(per_client_per_minute=10, global_per_minute=60)

    assert limiter.check("a", NOW) == 0.0


def test_check_throttles_a_caller_that_exhausts_its_own_budget() -> None:
    """A caller spending its minute's worth in one breath must be made to wait."""
    limiter = testee.RateLimiter(per_client_per_minute=2, global_per_minute=0)

    assert limiter.check("a", NOW) == 0.0
    assert limiter.check("a", NOW) == 0.0

    assert limiter.check("a", NOW) > 0.0, "the third request in the same instant is over budget"


def test_check_isolates_callers_from_each_other() -> None:
    """One abuser must not deny service to everyone else — that is what a per-caller budget is for."""
    limiter = testee.RateLimiter(per_client_per_minute=1, global_per_minute=0)

    limiter.check("abuser", NOW)

    assert limiter.check("abuser", NOW) > 0.0, "the abuser is throttled"
    assert limiter.check("bystander", NOW) == 0.0, "the bystander is untouched"


def test_check_throttles_fresh_callers_once_the_global_budget_is_spent() -> None:
    """Per-caller budgets cannot bound the bill alone: many callers, each within their limit, still drain the quota."""
    limiter = testee.RateLimiter(per_client_per_minute=10, global_per_minute=2)

    assert limiter.check("a", NOW) == 0.0
    assert limiter.check("b", NOW) == 0.0

    assert limiter.check("c", NOW) > 0.0, "a caller never seen before is still refused"


def test_check_charges_neither_budget_for_a_denied_request() -> None:
    """A caller refused by its own budget must not also drain the global one, or one abuser starves the service."""
    limiter = testee.RateLimiter(per_client_per_minute=1, global_per_minute=10)

    limiter.check("abuser", NOW)
    for _ in range(20):
        limiter.check("abuser", NOW)

    assert limiter.check("bystander", NOW) == 0.0, "the global budget survived the abuser's storm"


def test_check_does_not_charge_the_client_budget_when_the_global_one_refuses() -> None:
    """The mirror rule: a caller turned away by a busy minute must keep their own tokens, or someone else's traffic silently spends their budget."""
    # The client budget refills slowly (1/min) and the global one quickly (1/s),
    # so a token wrongly taken from the client bucket is still missing a second
    # later — which is what makes the wrongful charge observable at all.
    limiter = testee.RateLimiter(per_client_per_minute=1, global_per_minute=60, now=NOW)

    for other in range(60):
        limiter.check(f"other{other}", NOW)  # drains the global budget

    assert limiter.check("a", NOW) > 0.0, "'a' is refused by the global budget, not by its own"

    # A second later the global budget holds a token again. 'a' has never been
    # served, so its own budget must still be untouched and it must get in. Had
    # the refusal charged 'a', its 1/min bucket would be empty for another minute.
    assert limiter.check("a", NOW + 1.0) == 0.0, "'a' kept the token the refusal never took"


def test_check_admits_twice_the_limit_across_a_full_window() -> None:
    """A bucket starts full, so a minute admits burst + refill: the quota math depends on this 2x, and must not drift unnoticed."""
    limiter = testee.RateLimiter(per_client_per_minute=0, global_per_minute=30, now=NOW)

    # Hammer for one minute; the budget is the only thing turning requests away.
    admitted = sum(
        1 for step in range(601) if limiter.check(f"c{step}", NOW + step * 0.1) == 0.0
    )

    assert admitted == 60, "30/min sustained is 60 in the worst 60s window, not 30"


def test_check_reports_the_longer_of_the_two_waits() -> None:
    """Retrying when only the shorter budget has recovered would just earn a second 429."""
    limiter = testee.RateLimiter(per_client_per_minute=60, global_per_minute=6, now=NOW)

    for _ in range(6):
        limiter.check("a", NOW)

    # The per-client budget (60/min) refills a token in 1s; the global one
    # (6/min), now empty, needs 10s. The caller must be told the 10.
    assert limiter.check("a", NOW) == 10.0


def test_check_recovers_after_the_wait_it_reported() -> None:
    """A throttled caller must be let back in, or one burst would ban them for good."""
    limiter = testee.RateLimiter(per_client_per_minute=1, global_per_minute=0)

    limiter.check("a", NOW)
    retry_after = limiter.check("a", NOW)

    assert retry_after == 60.0, "one token at 1/min is a minute away"
    assert limiter.check("a", NOW + retry_after) == 0.0


def test_check_disables_a_budget_set_to_zero() -> None:
    """The load test drives one address far past any human rate, so it must be able to turn the guard off."""
    limiter = testee.RateLimiter(per_client_per_minute=0, global_per_minute=0)

    assert all(limiter.check("a", NOW) == 0.0 for _ in range(1_000))


def test_check_forgets_callers_whose_budget_has_fully_refilled() -> None:
    """A public endpoint holds a bucket per caller, so recovered ones must be dropped or memory grows forever."""
    limiter = testee.RateLimiter(per_client_per_minute=10, global_per_minute=0)

    with patch.object(testee, "MAX_TRACKED_CLIENTS", 1):
        limiter.check("idle", NOW)
        limiter.check("active", NOW + 3600.0)

    # Reaching into `_clients` is the only way to see this: a refilled bucket
    # decides exactly as an absent one does, so pruning is deliberately invisible
    # from `check`. What must hold is that only the spent bucket is still held.
    assert "idle" not in limiter._clients, "an hour on, its budget is full again and worth nothing"
    assert "active" in limiter._clients, "a caller still mid-refill is the one worth remembering"
