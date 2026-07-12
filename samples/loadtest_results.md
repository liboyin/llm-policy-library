# Load test and SLA extrapolation

Target SLA (TODO.md, D3): **50 concurrent users, p90 ≤ 15 s, p99 ≤ 30 s.** D3 scopes this to a
scaled load test (~10 concurrent users) plus an analytical extrapolation to 50; the 50-user figures
below are therefore **modelled, not measured**, and say so wherever they appear.

Every number here is traceable to an artifact committed beside it, and all of them come from **one
run of the code as committed**:

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

Run on `main`, 2026-07-12, against the live Azure deployments in Australia East.

```
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

The chat deployment's ratio is **1 RPM per 1,000 TPM**. That matters: the workload spends about
905 chat tokens per call, *below* the 1,000-token allowance each request carries, so it exhausts
**RPM before TPM**. Raising TPM is what raises RPM — they cannot be bought separately.

## Single-request latency floor

Serial requests, one at a time, so nothing queues or throttles (`loadtest_baseline.txt`). This is
the floor the SLA is built on: no amount of quota or scaling makes a request faster than this.

| | Latency |
|---|---|
| On-topic, cold (first request of the process) | 8.6 s |
| On-topic, warm | 5.8 – 10.4 s (median 6.6 s, n = 5) |
| Out-of-domain, fallback fired | 2.0 s |
| Out-of-domain, fallback did not fire | 5.4 s |

Two things this table shows that the aggregate percentiles cannot:

- **An out-of-domain query is not free on `main`.** It still costs a Planner call plus one search:
  the deterministic domain guard prototyped on `dev` is not part of this pipeline (Phase 5.5,
  deferred), so nothing short-circuits before the Planner.
- **The baseline caught the non-deterministic fallback in the act.** In that capture "How do I bake
  a chocolate cake?" did *not* fall back, and answering it cost 5.4 s instead of 2.0 s — the same
  effect that produced the flagged requests in the load run below, on an independent sample.

The warm sample is 5 requests. It bounds the floor; it is not a distribution.

## Results — 10 concurrent users, 6 minutes

376 requests, **2 failures (0.53 %)**, 1.05 RPS sustained, over a single 356-second session.

| Class | Requests | Failures | p50 | p90 | p99 | true max |
|---|---:|---:|---:|---:|---:|---:|
| `/query [on-topic]` | 269 | **0** | 7.4 s | **9.4 s** | **11.0 s** | 11.5 s |
| `/query [out-of-domain]` | 107 | 2 | 2.0 s | 2.5 s | 13.0 s | 12.7 s |
| Aggregated | 376 | 2 | 6.6 s | 9.1 s | 11.0 s | 12.7 s |

Percentiles are locust's histogram-bucketed values; "true max" is its exact `Max Response Time`
field. The out-of-domain p99 (13.0 s) is the bucketing of a single slow outlier — one of the two
requests below, which fell through to the full pipeline instead of the fallback.

**At 10 users the SLA is met with headroom: p90 9.4 s against a 15 s budget, p99 11.0 s against
30 s, on the class the SLA is about.** No HTTP error of any kind occurred — the server log contains
zero 502s, zero 429s, and zero SDK retries.

Three request counts appear across the artifacts and all three are consistent: locust recorded
**376** (it stops counting at its 6-minute cutoff), uvicorn's access log shows **377** fully-sent
200s, and the application log shows **382** pipelines that ran to completion. The tail is requests
still in flight when the clock stopped.

### The 2 failures are not hallucinations

Both are out-of-domain requests where the **deterministic fallback did not fire**. The Planner's
search phrase happened to retrieve controls above the 1.8 reranker floor, so the pipeline did not
short-circuit and the Response Agent answered instead — correctly refusing in prose ("I cannot
answer that question using the supplied controls"). Nothing was invented; grounding held. What
failed is the *guarantee*: `is_fallback` was false, so a caller relying on that flag would not know
the question was out of domain.

This is the known non-deterministic fallback (TODO.md Phase 5.5, deferred by decision), now measured
under load. **It is not a fixed rate.** This run: 2 of 107 (1.9 %). An earlier run of identical code
measured 6 of 101 (5.9 %), and the committed `loadtest_baseline.txt` — an independent serial sample —
shows one of its two out-of-domain queries also failing to fall back. Treat it as a low
single-digit percentage that varies run to run, not a constant. The safe fallback is probabilistic,
not guaranteed. If it must be guaranteed, the deferred domain-check step is the fix.

## Measured cost per request

From `loadtest.checks.summarize_run` over the 382 requests the server completed. Requests are classed
by **which golden-set query was asked**, not by the `is_fallback` flag on the response. Those are not
the same partition: the out-of-domain requests that did not fall back carry `is_fallback = false`,
and classing by the flag files them — and their full two-call pipeline cost — under "on-topic",
inflating that row.

| | n | Chat tokens | Chat calls | Searches | Embedding tokens |
|---|---:|---:|---:|---:|---:|
| On-topic | 274 | 1,370 in + 573 out = **1,943** | 2.00 | 2.04 | 12.8 |
| Out-of-domain | 108 | 518 in + 58 out = **576** | 1.02 | 1.00 | 5.4 |
| **Blended (3:1 mix)** | 382 | **1,557** | **1.72** | **1.74** | 10.7 |

The out-of-domain row's chat-call count is 1.02 rather than 1.00 because it averages two behaviours:
a request that falls back costs exactly 1 chat call (the Planner, then the deterministic template —
no Response Agent), while the 2 that did not fall back ran the full 2-call pipeline.

Searches per request are 2.04 for an on-topic query, not 1 — the Planner emits 1–3 steps and
`retrieve_plan` runs one search *and* one embedding call per step.

### What 10 users actually consumed

| Resource | Used | Quota | Utilisation |
|---|---:|---:|---:|
| Chat TPM | 100,220 | 150,000 | 67 % |
| Chat RPM | 111 | 150 | **74 %** |
| Embeddings TPM | ~690 | 150,000 | < 1 % |
| Search QPS | 1.87 | — | — |

The run finished inside quota, but the margin on **RPM is thin — 74 % of the ceiling at one fifth of
the target load.** Embeddings are negligible and will not constrain any plausible scale.

## Where the latency goes

Stage split across the 274 on-topic requests, from the log timestamps:

| Stage | Median | Share |
|---|---:|---:|
| Planner (chat call 1) | 1.97 s | 27 % |
| Retrieval + Response (chat call 2) | 5.32 s | 73 % |
| **Total** | **7.39 s** | |

The Planner costs a quarter of the latency budget and **half of every request's RPM** — the resource
that binds first.

## Extrapolation to 50 concurrent users

Closed-loop model: throughput `X = N / (R + Z)`, with `R` = mean response time (5.94 s aggregate) and
`Z` = 3.5 s mean think time (the mean of the locustfile's `between(2, 5)` — not a rounder assumed
number). At N = 10 the model predicts 1.06 RPS against 1.05 RPS measured, so it is calibrated on this
workload before being extended.

At **N = 50**, holding R constant: **X ≈ 5.3 RPS ≈ 318 requests/min.**

| Resource | Needed at 50 users | Current quota | Verdict |
|---|---:|---:|---|
| Chat RPM | 318 × 1.72 = **547** | 150 | **3.6 × over** |
| Chat TPM | 318 × 1,557 = **494,000** | 150,000 | **3.3 × over** |
| Embeddings TPM | ≈ 3,400 | 150,000 | fine |
| Search QPS | 5.3 × 1.74 = **9.2** | 1.87 measured | needs replicas |

**The binding constraint is chat RPM, not TPM, and not latency.** At the deployment's 1 RPM per
1,000 TPM ratio, covering 547 RPM means provisioning **≈ 600,000 TPM** (which yields 600 RPM and also
clears the 494 K TPM requirement with margin).

### Does the SLA hold at 50 users?

**Not established. It cannot be, from this test.** The honest answer, stated plainly because the
temptation to overclaim here is exactly what ruined the previous attempt at this phase:

- What *is* measured: at 10 users the SLA is met with room to spare (p90 9.4 s of a 15 s budget,
  p99 11.0 s of 30 s).
- What *is* established by extrapolation: at 50 users the deployment is **3.6× short of the RPM it
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

**The quota is also what prevents testing it.** At 150 RPM and 1.72 chat calls per request the
ceiling is 87 requests/min ≈ 1.45 RPS ≈ **14 concurrent users** at this workload's service and think
times. Any run above ~14 users throttles, so 25 or 50 users cannot be measured today *at all* — not
for want of effort, but because the quota forbids it. The tightest statement the evidence supports:

> The SLA is met at 10 users. Reaching 50 requires ≈ 600 K TPM. Whether it is *still* met at 50 must
> be re-measured once that quota exists — until then it is an open question, not a yes.

The failure mode if quota is not raised: requests exceed 150 RPM → Azure returns 429 → the OpenAI SDK
retries with backoff → latency inflates past the SLA → retries exhaust → the API maps the exception
to a 502. That is not hypothetical. It is what destroyed the previous attempt (commit `05a89cd` on
`dev`), which never checked the quota and recorded 176 × 502 and a dead server.

### Levers, if quota cannot be raised — or for margin

1. **Avoid the Planner call on simple queries.** It is 27 % of latency and 50 % of RPM — it attacks
   the binding constraint directly. Measured over this run's 382 plans, **46.6 % came back with a
   single step** (32.5 % two, 20.9 % three), so on nearly half of all traffic the Planner spends a
   chat call and ~2 s to decide "search the question as asked".

   The catch, stated because it is easy to hand-wave past: **you cannot know the step count without
   running the Planner** — the count *is* its output. So this is not a routing change but one of two
   real designs: (a) a cheap pre-classifier (an embedding-similarity or small-model gate) that
   decides *whether* to plan, or (b) dropping the Planner entirely and searching the raw question,
   accepting worse recall on the ~53 % of multi-family questions. Both are architecture changes, not
   configuration, and belong in Phase 7's design discussion — but (a) would roughly halve RPM demand
   (1.72 → ~1.0 chat calls/request), the difference between needing 600 K TPM and 300 K.
2. **PTU (Provisioned Throughput Units)** for a deterministic tail instead of shared-capacity
   variance — the right answer if p99 must be contractual.
3. `reasoning_effort` is already `minimal`, the lowest `gpt-5-mini` accepts. No headroom there.
4. **Streaming** would improve *perceived* latency but not the p90 of a complete response, which is
   what the SLA measures.

### Azure AI Search

Basic tier, 1 replica, served 1.87 QPS with no errors and no visible latency contribution. 50 users
implies **9.2 QPS**, roughly 5× that.

Microsoft does not publish a per-replica QPS figure for Basic, so this memo will not invent one. The
defensible statement: add replicas and re-measure. **2 replicas** are required in any case for
Search's read SLA, and that is the place to start.

## Caveats

- **The 50-user numbers are modelled, not measured.** Per D3 this was always the plan; it remains an
  extrapolation until a 50-user run confirms it — which the current quota makes impossible.
- **Global Standard routing.** Inference may be processed outside Australia East, so p99 carries more
  network variance than a regional deployment would. This widens the tail specifically, which is why
  the p99 headroom (19 s) matters more than the p90 headroom.
- **One 6-minute run.** 269 on-topic samples put p99 at roughly the third-worst observation — a p99
  read from this sample is indicative, not tight. p50/p90 are well supported.
- **Reasoning models are not bit-exact.** Re-running produces different plans, step counts, and
  therefore different latencies and fallback rates. These are one representative run, not a fixed
  point; the fallback-miss rate above is the clearest example.
