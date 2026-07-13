# llm-policy-library

A multi-agent AI system that answers questions about enterprise security policies by
retrieving from the NIST SP 800-53 Rev 5 control catalog indexed in Azure AI Search,
planning a query with PydanticAI agents, and generating a grounded,
citation-bearing answer with Azure OpenAI.

- [TASK.md](TASK.md) — the goals this project implements.
- [TODO.md](TODO.md) — the phased execution plan, the resolved design decisions
  (D1–D7), and each phase's verified status.
- [docs/architecture.md](docs/architecture.md) — system design, agent interaction flow,
  determinism & grounding, security, scalability (the 5M-word design and the 50-user
  SLA analysis), governance, and the zero-downtime reindex design.

> **Status:** complete. Phases 0–7 are delivered; Phase 8 (alternative approaches) was
> deliberately not executed. A demo deployment runs on Azure App Service at
> <https://llm-policy-library.azurewebsites.net/> (until the assessment resources are
> torn down).

## Project structure

| Path | Purpose |
|---|---|
| [llm_policy_library/config.py](llm_policy_library/config.py) | Environment-driven settings, validated once at startup |
| [llm_policy_library/logging_setup.py](llm_policy_library/logging_setup.py) | JSON log formatter and per-request correlation IDs |
| [llm_policy_library/dataset.py](llm_policy_library/dataset.py) | Fetches and parses the NIST SP 800-53 OSCAL catalog into `PolicyRecord`s |
| [llm_policy_library/search_index.py](llm_policy_library/search_index.py) | Azure AI Search index schema (hybrid + semantic) |
| [llm_policy_library/ingest.py](llm_policy_library/ingest.py) | `python -m llm_policy_library.ingest` — rebuilds the index |
| [llm_policy_library/models.py](llm_policy_library/models.py) | The typed messages the agents exchange |
| [llm_policy_library/agents/](llm_policy_library/agents/) | Planner, Retrieval, and Response agents, plus the two LLM judges |
| [llm_policy_library/prompts.json](llm_policy_library/prompts.json) | The version-controlled prompt store ([prompts.py](llm_policy_library/prompts.py) loads it) |
| [llm_policy_library/orchestrator.py](llm_policy_library/orchestrator.py) | `PolicyPipeline` — wires the three agents together |
| [llm_policy_library/api.py](llm_policy_library/api.py) | FastAPI service — `POST /query`, `GET /healthz`, `GET /` (the frontend) |
| [llm_policy_library/rate_limit.py](llm_policy_library/rate_limit.py) | Token-bucket request budgets — what bounds the bill on a public, unauthenticated endpoint |
| [llm_policy_library/cli.py](llm_policy_library/cli.py) | `python -m llm_policy_library.cli "question"` — same pipeline, pretty-printed |
| [static/index.html](static/index.html) | The browser frontend — one dependency-free page served at `GET /` |
| [llm_policy_library/evaluation.py](llm_policy_library/evaluation.py) | Evaluation harness: golden-set metrics, citation check, Markdown report |
| [evaluation/](evaluation/) | The golden set (`golden_set.json`) and the runner (`run_eval.py`) |
| [loadtest/](loadtest/) | Locust load test (`locustfile.py`), serial baseline, and the tested pass/fail logic (`checks.py`) |
| [samples/](samples/) | Sample execution outputs: smoke test, evaluation report, load-test artifacts |
| [tests/](tests/) | Unit tests; no test performs a live Azure call |
| [docs/](docs/) | [azure-setup.md](docs/azure-setup.md) (provisioning) and [architecture.md](docs/architecture.md) |
| [.github/workflows/](.github/workflows/) | CI/CD — deploys `main` to Azure App Service |
| [.env.example](.env.example) | Template for the environment variables |

## Setup

Requires Python 3.14 (the devcontainer in [.devcontainer/](.devcontainer/) provides it).

```bash
pip install -e ".[dev]"
cp .env.example .env    # then fill in the values
```

Provision the Azure resources first — [docs/azure-setup.md](docs/azure-setup.md) walks
through the portal steps, the tier trade-offs, and where to find each endpoint and key.
`.env` is gitignored and must never be committed; it is resolved relative to the repo
root rather than the working directory, so launching the API or the CLI from elsewhere
still finds it. (The eval and load-test commands take repo-relative paths — run those
from the repo root.)

## Dataset and ingestion

The corpus is the **NIST SP 800-53 Rev 5 control catalog** — the official
[OSCAL JSON](https://github.com/usnistgov/oscal-content) at a **pinned commit**, so the
corpus cannot drift under the index or the evaluation golden set. It yields **1,014**
records (one per control or control enhancement: `id`, `title`, `description`,
`category`), against TASK.md's 500-record minimum (decision D1 in [TODO.md](TODO.md)).

```bash
python -m llm_policy_library.ingest   # fetch -> parse -> embed -> recreate index -> upload
```

Ingestion is **idempotent**, and everything that can fail on bad input runs *before* the
old index is dropped, so a failure leaves the previous index serving. The parsing
decisions (withdrawn controls excluded, statement-only descriptions, parameter
placeholders rendered verbatim) and the `key`/`id` field split are documented in
[dataset.py](llm_policy_library/dataset.py) and
[search_index.py](llm_policy_library/search_index.py).

## The multi-agent pipeline

Three PydanticAI agents run as a sequential pipeline; every edge is a validated Pydantic
message, so a stage cannot quietly reinterpret what the previous one produced:

**Planner** (chat, structured output: 1–3 search steps) → **Retrieval** (no LLM: embed,
search, apply the relevance floor, deduplicate) → **Response** (chat, prose with inline
`[ac-2]` citations — or the safe fallback, without a chat call, when nothing relevant was
retrieved).

`RETRIEVAL_TOP_K` (default 5) is TASK.md's top-3–5 window and applies **per search step**,
so a multi-step plan grounds the answer in more than five controls — 5 to 14 across the
committed evaluation's on-topic queries. Each search returns its own top 5, and a question
spanning access control *and* logging needs both families; see
[docs/architecture.md](docs/architecture.md).

TASK.md names the Microsoft Agent Framework, which the project used through Phase 3 and
migrated off in Phase 5.5 — its pins conflict with `azure-search-documents`, its `Workflow`
holds per-run state that breaks on concurrent requests, and a straight-line three-stage
chain needs no workflow engine. The agent contract TASK.md specifies is unchanged; the
reasoning is in [docs/architecture.md](docs/architecture.md).

The full flow, the grounding guarantees, and the two-search-modes/two-score-scales design
(why `AZURE_SEARCH_SEMANTIC_RANKER` selects both the search mode *and* the relevance
floor) are in [docs/architecture.md](docs/architecture.md); the measured score bands
behind the floors are in
[agents/retrieval.py](llm_policy_library/agents/retrieval.py)'s docstring.

## Running the service, the frontend, and the CLI

The service and the CLI both wrap the same `PolicyPipeline`, and the frontend is a client
of the service — one code path from a question to a grounded answer.

```bash
uvicorn llm_policy_library.api:app   # GET / (frontend), POST /query, GET /healthz
```

`POST /query` returns `{answer, citations, is_fallback, plan, retrieved, latency_ms}` and
echoes an `X-Correlation-ID` response header. A pipeline failure answers `502` with a fixed
safe message, never a stack trace; a pipeline that outruns `REQUEST_TIMEOUT_SECONDS` answers
`504`; a missing, blank, or over-2,000-character `query` answers `422`; and a caller over its
request budget answers `429` with `Retry-After`, before any Azure call is made.

The service is public and unauthenticated on purpose, so a **request budget**, not a login,
is what bounds the Azure OpenAI bill — per caller and per process, on `POST /query` only.
See [Configuration](#configuration) for the knobs and
[docs/architecture.md](docs/architecture.md) for why the caller is identified by
`X-Client-IP` rather than `X-Forwarded-For`.

`GET /` serves [static/index.html](static/index.html) — open <http://127.0.0.1:8000> and
ask a question in the browser. It is a single page with inline CSS/JS (no build step, no
CDN) that shows the whole audit trail the API returns: the answer, the plan, and every
retrieved control with its score, cited ones marked. Model output reaches the DOM as
**text, never HTML** (`textContent`), so neither the query nor the corpus can become a
script-injection path.

```bash
python -m llm_policy_library.cli "What controls apply to API security?"
```

prints the plan, the retrieved controls with scores, and the cited answer. Its logs go to
stderr so stdout carries only the report.

Pushing to `main` deploys the service to **Azure App Service** via
[.github/workflows/main_llm-policy-library.yml](.github/workflows/main_llm-policy-library.yml)
(configuration arrives as App Service app settings instead of `.env`).

## Evaluation

```bash
python evaluation/run_eval.py   # runs the golden set through the live pipeline + judges
```

The harness runs the 15-query hand-labeled golden set
([evaluation/golden_set.json](evaluation/golden_set.json), including TASK.md's four
queries) through the pipeline and scores:

- **Retrieval** — recall and graded NDCG@5, computed directly from the qrels
  (deterministic math, no external evaluator). Recall is reported **exact-ID** (strict)
  and **base-family** (a retrieved enhancement credits its labelled base control — the
  fairer measure for a hierarchical catalog). Precision/F1 are deliberately not reported:
  at top-k 5 they penalize retrieving related enhancements and are uninformative.
- **Answer quality** — faithfulness and answer relevancy (integer 1–5) from two PydanticAI
  judge agents, prompts in the version-controlled store, retried with backoff, `None` on
  persistent failure.
- **Citation validity** — every inline citation must have been retrieved (hard check).
- **Safe fallback** — the two out-of-domain queries must fall back.

It writes [samples/evaluation_report.md](samples/evaluation_report.md) and
`samples/evaluation_transcripts.json`. Committed run: **faithfulness 5.0/5, relevancy
5.0/5, zero invented citations** across all 13 on-topic queries, 2/2 fallbacks; recall
0.46 exact-ID / 0.62 base-family, NDCG@5 0.49. The pipeline is a reasoning model, so
re-running shifts the numbers slightly. [samples/](samples/) also holds a three-query
smoke test (`smoke_test.json` + its audit trail `smoke_test.log`).

## Designed scale and load test

The system is **designed for** a ~5M-word corpus and 50 concurrent users at
p90 ≤ 15 s / p99 ≤ 30 s; the **demo** ingests the 1,014-control catalog and was
load-tested at 10 users with an analytical extrapolation to 50 (decisions D2/D3).

The rate limiter must be **off** for the run: the load test drives ~63 requests/minute from
one address, so with the default budgets in place it would measure the limiter rather than
the pipeline.

```bash
RATE_LIMIT_PER_IP_PER_MINUTE=0 RATE_LIMIT_GLOBAL_PER_MINUTE=0 \
    uvicorn llm_policy_library.api:app --workers 4   # then, in another shell:
locust -f loadtest/locustfile.py --headless -u 10 -r 2 --run-time 6m \
    --host http://127.0.0.1:8000
```

The load mix is the evaluation golden set itself (on-topic : out-of-domain 3:1), so the
queries whose answers were graded are the queries whose latency is measured. An HTTP 200
is not enough to pass: an on-topic answer that falls back or cites nothing is a failure.

Measured (6 min, 10 users, zero HTTP errors —
[samples/loadtest_results.md](samples/loadtest_results.md) traces every number to a
committed artifact):

| | p50 | p90 | p99 | SLA |
|---|---:|---:|---:|---|
| On-topic `POST /query` | 7.4 s | **9.4 s** | **11.0 s** | met, with headroom |

Extrapolated to 50 users: the binding constraint is **chat RPM, not TPM and not
latency** — ≈547 RPM needed against a 150 RPM quota, so the deployment needs **≈600K TPM**
(the quota grants 1 RPM per 1,000 TPM), and Azure AI Search needs replicas (start at 2).
Whether the latency SLA still holds at 50 users is deliberately left open — the current
quota caps any measurable run at ~14 users. The full quota math, the stage-level latency
split, and the mitigation levers are in the memo and summarized in
[docs/architecture.md](docs/architecture.md); the 5M-word index sizing (≈18K chunks,
≈110 MB of vectors, ≈$0.14 to embed) is in the architecture doc's scalability section.

## Configuration

All configuration is environment variables, read from the process environment or `.env`
(the process environment wins). [.env.example](.env.example) documents every variable;
[config.py](llm_policy_library/config.py) validates them at startup and fails fast with
a message naming each variable to fix. Three settings carry design weight:

- `AZURE_SEARCH_SEMANTIC_RANKER` — `true` needs Basic tier or above (hybrid search +
  semantic reranking, gated on `MIN_RERANKER_SCORE`); `false` (Free tier) switches to a
  vector-only search gated on `MIN_VECTOR_SCORE`, because a hybrid query's fused RRF score
  cannot gate relevance at any threshold.
- `MIN_RERANKER_SCORE` (default `1.8`) / `MIN_VECTOR_SCORE` (default `0.60`) — the
  relevance floors; exactly one applies. Each default sits between the measured on-topic
  and off-topic score bands, nearer the on-topic band: a compliance system should rather
  refuse than answer from a control it half-matched.
- `LLM_REASONING_EFFORT` — deployable Azure OpenAI chat models reject
  `temperature`/`top_p`/`seed`, so determinism is grounding-enforced instead (decision D7;
  see [docs/architecture.md](docs/architecture.md)).
- `RATE_LIMIT_PER_IP_PER_MINUTE` (default `10`) / `RATE_LIMIT_GLOBAL_PER_MINUTE` (default
  `30`) — the two request budgets on `POST /query`. The per-caller budget bounds one abuser;
  the global one bounds what the per-caller budget cannot (many callers each under their own
  limit), and is what actually caps the bill — ~75 req/min would exhaust the 150 RPM chat
  quota. Both are **sustained** rates: a token bucket holds a bucketful in reserve, so the
  worst case in any 60 s window is **twice** the number set, and the defaults are sized
  against that (30 sustained ⇒ 60 req/min worst case ⇒ ~120 chat calls, inside quota). `0`
  disables a budget, which is what the load test needs.
- `REQUEST_TIMEOUT_SECONDS` (default `60`) — how long `POST /query` waits for the pipeline
  before answering `504`. Above the measured p99 (~11 s), below the frontend's 120 s abort,
  so the server gives up first and says why.

## Logging

Every log line is one JSON object (stdout everywhere; stderr in the CLI). Each request
writes one line per hop — the query, the plan, each retrieval step with both the `kept`
**and** `dropped` documents and their scores, the answer with its citations and token
counts, and the end-to-end latency — all sharing a correlation ID that the API echoes back
as `X-Correlation-ID`. A safe fallback is thereby auditable: the trail shows what was
rejected and by how far. Noisy third-party loggers (`azure`, `httpx`, and friends) are
pinned to WARNING unless `LOG_LEVEL=DEBUG`. See
[logging_setup.py](llm_policy_library/logging_setup.py) for the API;
[samples/smoke_test.log](samples/smoke_test.log) is a real trail.

## Tests and static analysis

All three must pass before any commit:

```bash
pytest              # unit tests + coverage (>=80% per file and overall)
mypy .
ruff check .
```

Tests run in random order (`pytest-randomly`) and are hermetic: they never read the
developer's real `.env` and never call Azure.
