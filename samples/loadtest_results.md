# Load test and SLA extrapolation

Target SLA (TODO.md, D3): **50 concurrent users, p90 ≤ 15 s, p99 ≤ 30 s.** D3 scopes this to a
scaled load test (~10 concurrent users) plus an analytical extrapolation to 50; the 50-user figures
below are therefore **modelled, not measured**, and say so wherever they appear.

> **Dated note, 2026-07-24 (Phase 10).** Every measured figure below — latency percentiles, error
> rates, throughput — comes from the 2026-07-16 run, made **before** the Planner's corpus map was
> switched on, and is **historical**: it describes the pre-map pipeline, and no percentile here has
> been re-measured since. Decision D14 scoped this phase to an analytic update, so what the map does
> to latency is *not established*. An **on-topic** request still makes the same two chat calls it
> always did, which is the reason to expect little movement on the class the SLA is about — but that
> is an expectation, not a measurement, and the Caveats say so. (The *blended* call count does move,
> 1.73 → 1.71, because a structural refusal drops its Response call; that changes cost, not the
> latency of an answered question.) What the map
> demonstrably changes is the token arithmetic, and that is re-derived from measurement in
> [The corpus map's effect on cost](#the-corpus-maps-effect-on-cost-analytic-2026-07-24) — including
> a conclusion that moves: **the binding constraint at 50 users flips from RPM to TPM.** Read the
> percentile tables as a pre-map baseline, and the pre-map token tables as superseded by that
> section.

Every number in the *measured* sections below is traceable to an artifact committed beside it, and
all of them come from **one run of the code as committed** — the Microsoft Agent Framework build
(Phase 9). The Phase 10 cost section added later has its own evidence base, nine live evaluation
runs, whose every aggregate is committed as
[`corpus_map_ab.md`](corpus_map_ab.md); the raw logs of those runs are not committed (tens of
megabytes of answer prose), so that file, not this one, is where those figures are checkable.

| Artifact | What it holds |
|---|---|
| `loadtest_baseline.txt` | Deployment quota, and the serial latency floor (`python -m loadtest.baseline`) |
| `loadtest_stats.csv` | Per-class request counts, failures, percentiles |
| `loadtest_stats_history.csv` | The run's timeline (one session; timestamps are monotonic) |
| `loadtest_failures.csv` | Every failed request, with the reason |
| `loadtest_server.log.gz` | The server's JSON audit trail — where the token counts come from |

The per-request token and call figures are computed by `loadtest.checks.summarize_run`, which is
unit-tested, rather than by an ad-hoc script.

## How it was run

Run on `main`, 2026-07-16, on the **Microsoft Agent Framework** pipeline, against the live Azure
deployments in Australia East.

```
RATE_LIMIT_PER_IP_PER_MINUTE=0 RATE_LIMIT_GLOBAL_PER_MINUTE=0 \
    uvicorn llm_policy_library.api:app --host 127.0.0.1 --port 8000 --workers 4

locust -f loadtest/locustfile.py --headless -u 10 -r 2 --run-time 6m \
    --host http://127.0.0.1:8000 --csv samples/loadtest --csv-full-history
```

The query mix is `evaluation/golden_set.json` itself — the 13 on-topic queries (including TASK.md's
four) and the 2 out-of-domain ones — weighted 3:1, with 2–5 s of think time between requests.
Reusing the golden set means the queries whose answers were graded in Phase 5 are the queries whose
latency is measured here.

Locust reports the two classes as separate rows. They are not one population: an on-topic question
runs Planner → Retrieval → Response, while an out-of-domain one is answered by the safe fallback in
roughly a quarter of the time. Blending them produces a median that describes neither, and the SLA
is about answering a policy question.

## Deployment quota (measured, not assumed)

Read live from the deployments' `x-ratelimit-limit-*` response headers — captured in
`loadtest_baseline.txt`, not typed in from memory. This is the ceiling every capacity number below
is judged against:

| Deployment | Model | TPM | RPM |
|---|---|---:|---:|
| chat | `gpt-5-mini` (2025-08-07) | 150,000 | 150 |
| embeddings | `text-embedding-3-small` | 150,000 | (not returned) |

The quota probe reads these headers off the classic `chat/completions`/`embeddings` routes, which
need a *dated* api-version; the serving path itself uses the rolling `preview` Responses API the MAF
client targets. The rate limit is a property of the deployment, shared across every inference route,
so the ceiling read here is the one the Responses API is subject to.

The chat deployment's ratio is **1 RPM per 1,000 TPM**. That matters: a chat call spends about
900 tokens on the blended workload (~965 on the heavier on-topic class), *below* the 1,000-token
allowance each call carries, so it exhausts **RPM before TPM**. Raising TPM is what raises RPM —
they cannot be bought separately.

## Single-request latency floor

Serial requests, one at a time, so nothing queues or throttles (`loadtest_baseline.txt`). This is
the floor the SLA is built on: no amount of quota or scaling makes a request faster than this.

| | Latency |
|---|---|
| On-topic, cold (first request of the process) | 8.6 s |
| On-topic, warm | 4.7 – 8.4 s (median 7.6 s, n = 5) |
| Out-of-domain, fallback fired | 1.8 s (both out-of-domain queries fell back) |

An out-of-domain query is not free on `main`: it still costs a Planner call plus one search — the
deterministic domain guard prototyped on `dev` is not part of this pipeline (Phase 5.5, deferred),
so nothing short-circuits before the Planner. Both out-of-domain queries fell back in this baseline
capture; under load (below) some did not, which is the known non-deterministic fallback. The warm
sample is 5 requests: it bounds the floor, it is not a distribution.

## Results — 10 concurrent users, 6 minutes

379 requests (locust), **7 failures (1.85 %)**, 1.05 RPS sustained, over a single ~360-second session.

| Class | Requests | Failures | p50 | p90 | p99 | true max |
|---|---:|---:|---:|---:|---:|---:|
| `/query [on-topic]` | 271 | 1 | 7.4 s | **9.4 s** | **12.0 s** | 12.4 s |
| `/query [out-of-domain]` | 108 | 6 | 2.0 s | 2.5 s | 4.9 s | 5.0 s |
| Aggregated | 379 | 7 | 6.6 s | 9.0 s | 11.0 s | 12.4 s |

Percentiles are locust's histogram-bucketed values; "true max" is its exact `Max Response Time`
field. The aggregated p99 (11.0 s) is *lower* than the on-topic p99 (12.0 s) because it is diluted by
the fast out-of-domain requests — which is exactly why the two classes are held apart, and why the
on-topic row is the one the SLA is about.

**At 10 users the SLA is met with headroom: p90 9.4 s against a 15 s budget, p99 12.0 s against
30 s, on the class the SLA is about.** No HTTP error of any kind occurred — the server log contains
zero 502s, zero 429s, and zero SDK retries. The 7 "failures" are all quality-gate failures on
HTTP 200 responses, not transport errors (next section).

Two request counts appear across the artifacts and both are consistent: locust recorded **379** (it
stops counting at its 6-minute cutoff) and the application log shows **380** pipelines that ran to
completion. The tail is a request still in flight when the clock stopped.

### The 7 failures are quality-gate failures, not hallucinations

An HTTP 200 is not success: the load test marks an on-topic answer that falls back or cites nothing,
and an out-of-domain request that does *not* fall back, as failed. All 7 are of that kind.

- **6 out-of-domain requests where the deterministic fallback did not fire.** The Planner's search
  phrase happened to retrieve controls above the 1.8 reranker floor, so the pipeline did not
  short-circuit and the Response Agent answered instead — correctly refusing in prose. Nothing was
  invented; grounding held. What failed is the `is_fallback` *guarantee*.
- **1 on-topic answer that cited no control** ("What policies relate to logging and monitoring?").
  The Response Agent produced prose but no inline `[control-id]`, so the answer, while served, is not
  traceable to a specific control. It is not a hallucination — nothing was invented — but it fails
  the grounding bar the load test asserts. 1 of 271 (0.4 %); a reasoning-model prompt-adherence lapse
  in the same family as the non-deterministic fallback.

This is the known non-deterministic fallback (TODO.md Phase 5.5, deferred by decision), now measured
under load. **It is not a fixed rate.** This run: 6 of 108 out-of-domain (5.6 %), plus the one
on-topic lapse. Earlier runs of the pipeline measured 1.9 % and 5.9 %, and this run's own serial
baseline had *both* out-of-domain queries fall back (0 misses). Treat it as a low single-digit
percentage that varies run to run, not a constant. The safe fallback is probabilistic, not
guaranteed; if it must be guaranteed, the deferred domain-check step is the fix.

## Measured cost per request

From `loadtest.checks.summarize_run` over the 380 requests the server completed. Requests are classed
by **which golden-set query was asked**, not by the `is_fallback` flag on the response. Those are not
the same partition: the out-of-domain requests that did not fall back carry `is_fallback = false`,
and classing by the flag files them — and their full two-call pipeline cost — under "on-topic",
inflating that row.

| | n | Chat tokens | Chat calls | Searches | Embedding tokens |
|---|---:|---:|---:|---:|---:|
| On-topic | 271 | 1,357 in + 573 out = **1,931** | 2.00 | 1.79 | 20.1 |
| Out-of-domain | 109 | 561 in + 79 out = **639** | 1.06 | 1.00 | 8.3 |
| **Blended (3:1 mix)** | 380 | **1,560** | **1.73** | **1.56** | 16.7 |

Each figure is `summarize_run`'s mean rounded independently, so an `in + out` pair may not visibly
sum to its rounded total (e.g. on-topic 1,357.3 + 573.3 = 1,930.6, shown as 1,357 + 573 = 1,931).

The out-of-domain row's chat-call count is 1.06 rather than 1.00 because it averages two behaviours:
a request that falls back costs exactly 1 chat call (the Planner, then the deterministic template —
no Response Agent), while the 6 that did not fall back ran the full 2-call pipeline.

Searches per request are 1.79 for an on-topic query, not 1 — the Planner emits 1–3 steps and
`retrieve_plan` runs one search *and* one embedding call per step. Across this run's 380 plans,
**50.3 % came back single-step** (43.4 % two, 6.3 % three).

### What 10 users actually consumed

| Resource | Used | Quota | Utilisation |
|---|---:|---:|---:|
| Chat TPM | ~98,300 | 150,000 | 66 % |
| Chat RPM | 109 | 150 | **73 %** |
| Embeddings TPM | ~1,050 | 150,000 | < 1 % |
| Search QPS | 1.64 | — | — |

The run finished inside quota, but the margin on **RPM is thin — 73 % of the ceiling at one fifth of
the target load.** Embeddings are negligible and will not constrain any plausible scale.

## Where the latency goes

Stage split across the 271 on-topic requests, from the log timestamps (Planner = total latency minus
the retrieval-plus-response span; the rest is everything after the plan):

| Stage | Median | Share |
|---|---:|---:|
| Planner (chat call 1) | 1.75 s | 24 % |
| Retrieval + Response (chat call 2) | 5.42 s | 73 % |
| **Total** | **7.41 s** | |

The Planner costs a quarter of the latency budget and is **one of every on-topic request's two chat
calls — half its RPM**, the resource that binds first.

## Extrapolation to 50 concurrent users

Closed-loop model: throughput `X = N / (R + Z)`, with `R` = mean response time (5.93 s aggregate) and
`Z` = 3.5 s mean think time (the mean of the locustfile's `between(2, 5)` — not a rounder assumed
number). At N = 10 the model predicts 1.06 RPS against 1.05 RPS measured, so it is calibrated on this
workload before being extended.

At **N = 50**, holding R constant: **X ≈ 5.3 RPS ≈ 318 requests/min.**

| Resource | Needed at 50 users | Current quota | Verdict |
|---|---:|---:|---|
| Chat RPM | 318 × 1.73 = **550** | 150 | **3.7 × over** |
| Chat TPM | 318 × 1,560 = **496,000** | 150,000 | **3.3 × over** |
| Embeddings TPM | 318 × 16.7 = ≈ **5,300** | 150,000 | fine |
| Search QPS | 5.3 × 1.56 = **8.3** | 1.64 measured | needs replicas |

**The binding constraint is chat RPM, not TPM, and not latency.** At the deployment's 1 RPM per
1,000 TPM ratio, covering 550 RPM means provisioning **≈ 600,000 TPM** (which yields 600 RPM and also
clears the 496 K TPM requirement with margin).

> **Superseded for the shipped configuration (2026-07-24).** The paragraph and table above describe
> the pre-map pipeline. With `PLANNER_CORPUS_MAP` on — the default since Phase 10 — RPM eases
> slightly to 545 while TPM rises to ≈805 K, so **TPM becomes the binding constraint and the target
> is ≈810 K TPM**. See
> [The corpus map's effect on cost](#the-corpus-maps-effect-on-cost-analytic-2026-07-24).

### Does the SLA hold at 50 users?

**Not established. It cannot be, from this test.** The honest answer, stated plainly because the
temptation to overclaim here is exactly what ruined the first attempt at this phase:

- What *is* measured: at 10 users the SLA is met with room to spare (p90 9.4 s of a 15 s budget,
  p99 12.0 s of 30 s).
- What *is* established by extrapolation: at 50 users the deployment is **3.7× short of the RPM it
  would need**. That conclusion is safe because it depends only on arithmetic — requests/min ×
  chat-calls/request — not on any claim about latency.
- What is **not** established: that p90/p99 hold at 50 users. The closed-loop model *assumes* the
  response time `R` is constant in order to compute throughput; it therefore cannot also be used as
  evidence that `R` stays constant. That would be circular, and this memo will not do it.

There is a plausible mechanism for `R` holding — per-request latency here is set by model inference
time, not by queueing on our side (the app is async and I/O-bound; 4 uvicorn workers were nowhere
near saturated at 1.05 RPS), and Azure OpenAI Global Standard is a managed multi-tenant service that
does not slow down merely because *our* concurrency rose. **But that is a hypothesis, not a
measurement**, and it is offered as one.

**The quota is also what prevents testing it.** Pre-map, RPM was the ceiling: at 150 RPM and 1.73
chat calls per request that is 87 requests/min ≈ 1.45 RPS ≈ **14 concurrent users** at this
workload's service and think times. **On the shipped corpus-map configuration the ceiling is lower
and set by tokens**: 150 K TPM ÷ 2,533 tokens per request ≈ 59 requests/min ≈ 0.99 RPS ≈ **9
concurrent users** (against ~14 by RPM, which no longer binds). And 9 is itself an upper bound on
observed tokens — Azure admits against its own up-front estimate, so the real throttling point is at
or below it and is not knowable from this trail. Either way 25 or 50 users cannot be measured today
*at all* — not for want of effort, but because the quota forbids it. The tightest statement the
evidence supports:

> The SLA is met at 10 users. Reaching 50 requires ≈ 600 K TPM — **≈ 810 K on the shipped
> corpus-map configuration**. Whether it is *still* met at 50 must be re-measured once that quota
> exists — until then it is an open question, not a yes.

The failure mode if quota is not raised: requests exceed 150 RPM → Azure returns 429 → the OpenAI SDK
retries with backoff → latency inflates past the SLA → retries exhaust → the API maps the exception
to a 502. That is not hypothetical. It is what destroyed the first attempt at this phase (commit
`05a89cd` on `dev`), which never checked the quota and recorded 176 × 502 and a dead server.

### Levers, if quota cannot be raised — or for margin

1. **Avoid the Planner call on simple queries.** It is 24 % of latency and half of an on-topic
   request's RPM — it attacks the binding constraint directly. Measured over this run's 380 plans,
   **50.3 % came back with a single step** (43.4 % two, 6.3 % three), so on half of all traffic the
   Planner spends a chat call and ~1.8 s to decide "search the question as asked".

   The catch, stated because it is easy to hand-wave past: **you cannot know the step count without
   running the Planner** — the count *is* its output. So this is not a routing change but one of two
   real designs: (a) a cheap pre-classifier (an embedding-similarity or small-model gate) that
   decides *whether* to plan, or (b) dropping the Planner entirely and searching the raw question,
   accepting worse recall on multi-family questions. Both are architecture changes, not
   configuration. A perfect gate that skipped the Planner on exactly the 50.3 % single-step traffic
   would cut RPM demand by ~30 % (1.73 → ~1.2 chat calls/request), the difference between needing
   600 K TPM and ≈ 400 K; reaching ~1.0 calls/request means eliminating the Planner on essentially
   all traffic — option (b).
2. **PTU (Provisioned Throughput Units)** for a deterministic tail instead of shared-capacity
   variance — the right answer if p99 must be contractual.
3. `reasoning_effort` is already `minimal`, the lowest `gpt-5-mini` accepts. No headroom there.
4. **Streaming** would improve *perceived* latency but not the p90 of a complete response, which is
   what the SLA measures.

### The corpus map's effect on cost (analytic, 2026-07-24)

Phase 10 turned on `PLANNER_CORPUS_MAP`, which prefixes every Planner call with the twenty control
families and what each covers — ~899 tokens of map, or ~1,053 for the block once its two routing
rules are counted. Per decision D14 this is an **analytic update, not a re-run**: the load
test was not repeated, and no latency number above changes.

**Method.** The 10-user run stays the anchor for per-request cost — it is the only measurement taken
under this memo's own traffic mix. The map's cost is measured as the **map-on minus map-off delta**
over the same 13 on-topic golden queries, from Phase 10's A/B (3 evaluation runs per setting,
2026-07-24, `PLANNER_CORPUS_MAP` false vs true), and then added to that anchor. Measuring a delta
rather than substituting the A/B totals keeps mix, concurrency, and sampling identical on both sides,
so the only thing that moves is the map. The assumption it rests on — that the map's cost is additive
across the two regimes — is exactly true for the planner prefix, which is the same bytes on every
call, and approximate for the retrieval and response terms that follow from it.

| Measured, per planner call | map off | map on | delta |
|---|---:|---:|---:|
| Planner input tokens | 507.4 | 1,560.4 | **+1,053.0** |

That is the complete corpus-map prompt block — the twenty rendered entries (~899 tokens) plus the
two routing rules — a fixed prefix charged identically on every planner call in either request
class. It dominates but does not account for the whole projection: three smaller deltas ride along
with it on the on-topic class (planner output −9.5, response input −57.4, response output −17.9,
netting **+968.2**), because a map-on plan came back slightly shorter and so grounded its answer in
slightly less text. All four per-class deltas are tabulated in
[`corpus_map_ab.md`](corpus_map_ab.md); applying them to the anchor gives:

| Per request | before (measured) | after (projected) |
|---|---:|---:|
| On-topic chat tokens | 1,931 | **2,899** |
| Out-of-domain chat tokens | 639 | **1,623** |
| Blended chat tokens | 1,560 | **2,533** |
| Blended chat calls | 1.73 | **1.71** |
| Blended searches | 1.56 | **1.15** |
| Blended embedding tokens | 16.7 | **14.3** |

**The out-of-domain row is overridden, not projected.** A structural refusal is not a cost that
shifted — it is a stage that no longer runs, so the delta method (which adds a *difference* to the
anchor) does not apply to it. The shipped pipeline spends exactly **one Planner call and nothing
else** on such a request: no search, no embedding, and no Response call. The anchor's out-of-domain
row carries the residue of the pre-map world — 1.06 chat calls and ~32 Response tokens, because 6 of
its 109 out-of-domain requests failed to fall back and ran the full pipeline — and projecting that
forward would credit the shipped system with costs it cannot incur. Searches, embedding tokens, the
Response call, and its tokens are therefore all set to their measured map-on values of **zero**,
which is what the map-on arm recorded. Only the Planner's own tokens are projected, since that call
does still happen and only got bigger.

**What the override assumes, stated plainly:** that every out-of-domain request refuses
structurally. The evidence for it is 3/3 runs on the two golden out-of-domain queries — strong for
those two questions, not a proof that the model never misses. The direction of any error is known,
though: a miss adds back a Response call and its tokens, so the projected out-of-domain row is a
**best case**, ≈805K TPM is a **floor**, and RPM sits between the projected 545 and the pre-map 550.
The TPM-binds conclusion only strengthens if the miss rate is above zero.

One second-order effect is carried through by the delta method proper: on-topic plans came back
slightly shorter with the map (1.44 → 1.26 searches per request across 39 requests per arm), a small
sample reported as measured rather than claimed as a mechanism.

**The conclusion that moves — RPM no longer binds; TPM does.**

| Resource at 50 users | before | after | Quota | Verdict |
|---|---:|---:|---:|---|
| Chat RPM | 550 | **545** | 150 | slightly *lower* — a refusal makes one call, not two |
| Chat TPM | 496,000 | **805,000** | 150,000 | **5.4× over** |
| Embeddings TPM | 5,300 | 4,600 | 150,000 | fine |
| Search QPS | 8.3 | **6.1** | 1.64 measured | still needs replicas |

At the deployment's 1 RPM per 1,000 TPM ratio, 545 RPM is covered by 545K TPM — but the workload now
*needs* ≈805K TPM. **So the requirement rises from ≈600K TPM to ≈805K (provision ≈810K), and the
resource that runs out first is tokens, not requests.** Before the map, the memo's "buy TPM to get
RPM" framing held; now TPM is what is actually consumed, and the earlier ~1.3× headroom the 600K
figure carried is gone.

D14 estimated this at ≈650K TPM. The measurement is ≈805K: the estimate counted the map against the
planner call alone and did not carry it into the blended per-request figure, where an out-of-domain
request also pays the full prefix for its one and only call.

**These are observed-token figures, and Azure's TPM limit is not enforced on observed tokens.** For
a Standard deployment, Azure estimates each request's cost up front from the prompt plus the
requested maximum completion length, and admits or throttles against *that* estimate; the tokens
actually processed are reconciled afterwards. These agents set no `max_output_tokens`, so the
estimate the limiter applies is not something this memo can derive from the audit trail. Treat
≈805K as **a measured throughput requirement, not a quota allocation**: it is the floor the workload
demonstrably consumes, and the quota needed to run it without throttling is at least that and
determinable only by capping output explicitly or by reading the `x-ratelimit-*` headers under a
controlled run at the target rate.

**Prompt caching does not lower it either.** The instruction prefix is well over the 1,024-token
threshold and is byte-identical across calls, so Azure OpenAI discounts the repeated prefix on the
*bill*. That is a billing discount, not a quota one, so the arithmetic above uses full token counts.

### Azure AI Search

Basic tier, 1 replica, served 1.64 QPS with no errors and no visible latency contribution. 50 users
implies **8.3 QPS** pre-map (**6.1 QPS** with the map on, above), roughly 4–5× that.

Microsoft does not publish a per-replica QPS figure for Basic, so this memo will not invent one. The
defensible statement: add replicas and re-measure. **2 replicas** are required in any case for
Search's read SLA, and that is the place to start.

## Caveats

- **The 50-user numbers are modelled, not measured.** Per D3 this was always the plan; it remains an
  extrapolation until a 50-user run confirms it — which the current quota makes impossible.
- **Global Standard routing.** Inference may be processed outside Australia East, so p99 carries more
  network variance than a regional deployment would. This widens the tail specifically, which is why
  the p99 headroom (18 s) matters more than the p90 headroom.
- **One 6-minute run.** 271 on-topic samples put p99 at roughly the third-worst observation — a p99
  read from this sample is indicative, not tight. p50/p90 are well supported.
- **The measured tables predate the corpus map.** Every latency and error figure here is from
  2026-07-16, before `PLANNER_CORPUS_MAP` was switched on; the token and capacity figures for the
  shipped configuration are the analytic update above, not a fresh measurement. A re-run is what
  would settle whether the map's larger prompt moves latency at all — the expectation is that it
  does not materially (input tokens are processed far faster than they are generated, and an
  on-topic request makes the same two calls it always did), but that expectation is untested.
- **Reasoning models are not bit-exact.** Re-running produces different plans, step counts, and
  therefore different latencies and fallback rates. These are one representative run, not a fixed
  point; the fallback-miss rate above is the clearest example. (The MAF build's per-request token and
  latency figures land within a few percent of the earlier PydanticAI-era run — 1,931 vs 1,943
  on-topic chat tokens, p90 9.4 s in both — so the capacity conclusion is unchanged by the migration.)
