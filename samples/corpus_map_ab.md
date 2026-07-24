# Corpus map A/B — the evidence behind Phase 10's decisions

Nine live evaluation runs over the 15-query golden set (`evaluation/golden_set.json`), three
per configuration, run 2026-07-24 against the live Azure deployments. This artifact exists so
that every Phase 10 figure quoted in `TODO.md`, `README.md`, `docs/architecture.md` and
`loadtest_results.md` can be checked against the runs that produced it. It is generated from
the run logs and transcripts, not transcribed by hand.

The raw per-run logs and transcripts are **not** committed: they are tens of megabytes and
contain full answer prose. What is committed is every aggregate those files were reduced to.

Three configurations were measured, in this order. The middle one is why the shipped one exists.

## map off (baseline) — `map_false`

| Run | Recall exact-ID | Recall base-family | NDCG@5 | Faithfulness | Answer relevancy | Invented | Fallbacks |
|---|---|---|---|---|---|---|---|
| 1 | 0.4949 | 0.6474 | 0.5414 | 5.0000 | 5.0000 | 0 | 2/2 |
| 2 | 0.4628 | 0.7051 | 0.4478 | 5.0000 | 5.0000 | 0 | 2/2 |
| 3 | 0.4218 | 0.6833 | 0.4365 | 5.0000 | 5.0000 | 0 | 2/2 |
| **mean** | **0.4598** | **0.6786** | **0.4752** | **5.0000** | **5.0000** | | |
| *spread* | *0.0731* | *0.0577* | *0.1048* | *0.0000* | *0.0000* | | |

Steps carrying a category filter: **0/62**.

Out-of-domain routing, per run:

- Run 1: *What is the capital of France?* → **incidental**; *How do I bake a chocolate cake?* → **incidental**
- Run 2: *What is the capital of France?* → **incidental**; *How do I bake a chocolate cake?* → **incidental**
- Run 3: *What is the capital of France?* → **incidental**; *How do I bake a chocolate cake?* → **incidental**

## map on, category filtering ON — `map_true`

| Run | Recall exact-ID | Recall base-family | NDCG@5 | Faithfulness | Answer relevancy | Invented | Fallbacks |
|---|---|---|---|---|---|---|---|
| 1 | 0.4103 | 0.6513 | 0.4609 | 5.0000 | 5.0000 | 0 | 2/2 |
| 2 | 0.3718 | 0.5808 | 0.4365 | 5.0000 | 5.0000 | 0 | 2/2 |
| 3 | 0.3795 | 0.6077 | 0.4932 | 5.0000 | 5.0000 | 0 | 2/2 |
| **mean** | **0.3872** | **0.6132** | **0.4635** | **5.0000** | **5.0000** | | |
| *spread* | *0.0385* | *0.0705* | *0.0566* | *0.0000* | *0.0000* | | |

Steps carrying a category filter: **50/55**.

Out-of-domain routing, per run:

- Run 1: *What is the capital of France?* → **structural**; *How do I bake a chocolate cake?* → **structural**
- Run 2: *What is the capital of France?* → **structural**; *How do I bake a chocolate cake?* → **structural**
- Run 3: *What is the capital of France?* → **structural**; *How do I bake a chocolate cake?* → **structural**

## map on, filtering OFF (shipped) — `map_nofilter`

| Run | Recall exact-ID | Recall base-family | NDCG@5 | Faithfulness | Answer relevancy | Invented | Fallbacks |
|---|---|---|---|---|---|---|---|
| 1 | 0.4487 | 0.6449 | 0.4826 | 5.0000 | 5.0000 | 0 | 2/2 |
| 2 | 0.4346 | 0.6513 | 0.4871 | 5.0000 | 5.0000 | 0 | 2/2 |
| 3 | 0.4346 | 0.6474 | 0.4756 | 5.0000 | 5.0000 | 0 | 2/2 |
| **mean** | **0.4393** | **0.6479** | **0.4818** | **5.0000** | **5.0000** | | |
| *spread* | *0.0141* | *0.0064* | *0.0115* | *0.0000* | *0.0000* | | |

Steps carrying a category filter: **0/49**.

Out-of-domain routing, per run:

- Run 1: *What is the capital of France?* → **structural**; *How do I bake a chocolate cake?* → **structural**
- Run 2: *What is the capital of France?* → **structural**; *How do I bake a chocolate cake?* → **structural**
- Run 3: *What is the capital of France?* → **structural**; *How do I bake a chocolate cake?* → **structural**

## The D12 gate

Gate (a): each map-on mean must be at or above the baseline mean minus the baseline's own
run-to-run spread — "within noise", with the noise measured rather than assumed. Gate (b):
both out-of-domain queries must reach the fallback through the `out_of_domain` flag in all
three runs.

| Metric | baseline mean | baseline spread | floor | filtering arm | shipped arm |
|---|---:|---:|---:|---|---|
| Recall exact-ID | 0.4598 | 0.0731 | 0.3868 | 0.3872 PASS | 0.4393 PASS |
| Recall base-family | 0.6786 | 0.0577 | 0.6209 | 0.6132 **FAIL** | 0.6479 PASS |
| NDCG@5 | 0.4752 | 0.1048 | 0.3704 | 0.4635 PASS | 0.4818 PASS |
| Faithfulness | 5.0000 | 0.0000 | 5.0000 | 5.0000 PASS | 5.0000 PASS |
| Answer relevancy | 5.0000 | 0.0000 | 5.0000 | 5.0000 PASS | 5.0000 PASS |

**The filtering arm failed** on base-family recall (0.6132 against a 0.6209 floor) and cleared
the exact-ID floor by 0.0004, which is a coin flip rather than evidence of no harm. **The
shipped arm passes every metric**, with NDCG@5 above the baseline mean, and is markedly more
stable: its exact-ID spread is 0.0141 against the baseline's 0.0731.

## Why filtering was disabled

Per-query recall change, filtering arm minus baseline, averaged over each query's own runs and
split by whether the planner filtered that query in the majority of runs:

- **Filtered by a category** (12 queries): mean exact-ID -0.093, mean base-family -0.071
- **Left unfiltered** (1 query): mean exact-ID +0.167, mean base-family +0.000

| Query (filtered) | exact-ID Δ | base-family Δ |
|---|---:|---:|
| Summarise requirements for access control | -0.333 | -0.267 |
| What policies relate to logging and monitoring? | -0.222 | -0.222 |
| What controls address vulnerability scanning and flaw remediation? | -0.222 | -0.111 |
| What are the contingency planning and system backup requirements? | -0.222 | -0.333 |
| How is least privilege enforced and separation of duties maintained? | -0.222 | -0.333 |
| What are the requirements for multi-factor authentication? | -0.167 | +0.167 |
| How should removable media and portable storage devices be controlled? | -0.083 | -0.083 |
| What controls apply to API security? | +0.000 | +0.333 |
| What are the requirements for security awareness and role-based training? | +0.000 | +0.000 |
| What controls apply to configuration management and baseline configurations? | +0.000 | +0.000 |
| How should cryptographic keys be established and managed? | +0.111 | +0.000 |
| What controls govern incident response and reporting? | +0.250 | +0.000 |

The phase's predicted headline winner, *Summarise requirements for access control*, regressed
hardest. Filtering to one family fills the top-k with that family's enhancements instead of its
base controls, and a question spanning several families cannot reach the rest.

## D14 inputs: what the map costs per request

Means over each arm's three runs. These are the inputs to the capacity re-derivation in
`loadtest_results.md`, which anchors on the 10-user load run and applies these deltas to it —
but not uniformly, because the two request classes changed in different ways. **On-topic:** all
four token deltas are applied (planner in/out and response in/out), netting +968.2 tokens per
request. **Out-of-domain:** only the planner tokens are projected; searches, embedding tokens,
the Response call and its tokens are *overridden to the measured zeros* rather than projected,
because a structural refusal removes those stages instead of shifting their cost.

| | map off | shipped (map on, no filter) | delta |
|---|---:|---:|---:|
| Planner input tokens, on-topic | 507.4 | 1,560.4 | +1,053.0 |
| Planner input tokens, out-of-domain | 505.5 | 1,558.5 | +1,053.0 |
| Planner output tokens, on-topic | 119.7 | 110.2 | -9.5 |
| Planner output tokens, out-of-domain | 70.8 | 33.7 | -37.2 |
| Response input tokens, on-topic | 728.5 | 671.2 | -57.4 |
| Response input tokens, out-of-domain | 0.0 | 0.0 | +0.0 |
| Response output tokens, on-topic | 409.8 | 391.8 | -17.9 |
| Response output tokens, out-of-domain | 0.0 | 0.0 | +0.0 |
| Chat calls, on-topic | 2.000 | 2.000 | +0.000 |
| Chat calls, out-of-domain | 1.000 | 1.000 | +0.000 |
| Searches, on-topic | 1.436 | 1.256 | -0.179 |
| Searches, out-of-domain | 1.000 | 0.000 | -1.000 |
| Embedding tokens, on-topic | 14.5 | 14.4 | -0.1 |
| Embedding tokens, out-of-domain | 4.8 | 0.0 | -4.8 |

Sample sizes: 39 on-topic and 6 out-of-domain requests per arm.

The single number that drives the capacity conclusion is the planner-input delta of
**+1,053.0 tokens per call**. That is the *complete corpus-map prompt block*: the twenty rendered
entries (~899 tokens) plus the two routing rules wrapped around them. It is charged
identically on every planner call in either request class, which is why the
out-of-domain row pays it in full for its one and only call.

