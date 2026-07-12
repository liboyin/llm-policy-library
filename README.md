# llm-policy-library

A multi-agent AI system that answers questions about enterprise security policies by
retrieving from the NIST SP 800-53 Rev 5 control catalog indexed in Azure AI Search,
planning a query with PydanticAI agents, and generating a grounded,
citation-bearing answer with Azure OpenAI.

See [TASK.md](TASK.md) for the goals this project implements and
[TODO.md](TODO.md) for the phased execution plan and the resolved design decisions.

> **Status:** evaluated (Phase 5). Ingestion, the three agents, the orchestration pipeline, the
> HTTP API, the CLI, and the evaluation harness are in place; the load test and the architecture
> doc land in later phases.

## Project structure

| Path | Purpose |
|---|---|
| [llm_policy_library/config.py](llm_policy_library/config.py) | Environment-driven settings, validated once at startup |
| [llm_policy_library/logging_setup.py](llm_policy_library/logging_setup.py) | JSON log formatter and per-request correlation IDs |
| [llm_policy_library/dataset.py](llm_policy_library/dataset.py) | Fetches and parses the NIST SP 800-53 OSCAL catalog into `PolicyRecord`s |
| [llm_policy_library/search_index.py](llm_policy_library/search_index.py) | Azure AI Search index schema (hybrid + semantic) |
| [llm_policy_library/ingest.py](llm_policy_library/ingest.py) | `python -m llm_policy_library.ingest` — rebuilds the index |
| [llm_policy_library/models.py](llm_policy_library/models.py) | The typed messages the agents exchange |
| [llm_policy_library/agents/planner.py](llm_policy_library/agents/planner.py) | Planner Agent — decomposes a question into 1–3 searches |
| [llm_policy_library/agents/retrieval.py](llm_policy_library/agents/retrieval.py) | Retrieval Agent — searches the index and applies the relevance floor |
| [llm_policy_library/agents/response.py](llm_policy_library/agents/response.py) | Response Agent — writes the grounded answer, or the safe fallback |
| [llm_policy_library/orchestrator.py](llm_policy_library/orchestrator.py) | The pipeline wiring the three agents together |
| [llm_policy_library/api.py](llm_policy_library/api.py) | FastAPI service — `POST /query`, `GET /healthz` |
| [llm_policy_library/cli.py](llm_policy_library/cli.py) | `python -m llm_policy_library.cli "question"` — same pipeline, pretty-printed |
| [llm_policy_library/evaluation.py](llm_policy_library/evaluation.py) | Evaluation harness: golden-set metrics, citation check, Markdown report |
| [evaluation/](evaluation/) | The golden set (`golden_set.json`) and the runner (`run_eval.py`) |
| [samples/](samples/) | Sample execution output: raw pipeline results, audit logs, and the evaluation report |
| [tests/](tests/) | Unit tests; no test performs a live Azure call |
| [docs/azure-setup.md](docs/azure-setup.md) | How to provision the Azure resources this project reads |
| [.env.example](.env.example) | Template for the environment variables below |

## Dataset and ingestion

The corpus is the **NIST SP 800-53 Rev 5 control catalog**, taken from the official
[OSCAL JSON](https://github.com/usnistgov/oscal-content) at a **pinned commit** so the
corpus cannot drift under the index or the evaluation golden set. TASK.md suggests a NIST
CSF dataset, but that one holds only 91 records against a 500-record minimum; the 800-53
catalog yields **1,014** and maps cleanly onto the required title / description / category
(see decision D1 in [TODO.md](TODO.md)).

```bash
python -m llm_policy_library.ingest   # fetch -> parse -> embed -> recreate index -> upload
```

Each record is one control or control enhancement: `id` (`ac-2.1`), `title`, `description`
(the control statement), and `category` (the control family). Three parsing decisions are
worth knowing:

- **Withdrawn controls are excluded.** They are superseded and 180 of the 182 carry no
  requirement text at all; citing one as if it applied would be a grounding error.
- **Only the `statement` part becomes the description.** The sibling `guidance` and
  SP 800-53A assessment parts are commentary, and would dilute the embedding of what the
  control actually mandates.
- **Parameter placeholders are rendered, not stripped.** `{{ insert: param, ... }}` becomes
  `[Assignment: organization-defined frequency]` or `[Selection (one or more): a; b]`.
  Labels are used verbatim rather than reconstructed into NIST's printed phrasing, so the
  description never contains words its source does not.

Ingestion is **idempotent**: it recreates the index, leaving exactly the controls in the
pinned catalog. Everything that can fail on bad input — fetching, parsing, the 500-record
floor, embedding, and the vector-width check — runs *before* the index is dropped, so such
a failure leaves the previous index still serving queries. Azure AI Search reports its
document count with a few seconds' lag, so a count taken the instant ingestion finishes may
read low.

The index stores the control ID twice. Azure AI Search restricts document key *values* to
letters, digits, dash, underscore, and equal sign, so the enhancement ID `ac-2.1` cannot be
a key; `key` holds the dot-free encoding `ac-2_1` while `id` keeps the exact ID that
answers cite and the golden set labels.

## The multi-agent pipeline

Three agents run as a sequential pipeline over PydanticAI. Each edge is a validated
Pydantic model, not a conversation, so a stage cannot quietly reinterpret what the previous
one produced:

```
"What controls apply to API security?"
        │
        ▼  Planner Agent      chat model, PlannerOutput structured output
   QueryPlan(original_query, steps=[PlanStep(search_query, purpose), ...])
        │
        ▼  Retrieval Agent    no chat model; embed -> search -> apply relevance floor
   RetrievalOutcome(plan, results=[RetrievalResult, ...], documents=[RetrievedDocument, ...])
        │
        ▼  Response Agent     chat model, prose with inline [ac-2] citations
   PipelineResult(plan, results, documents, response=GroundedResponse(...))
```

- **Planner** answers with a `PlannerOutput` JSON schema (the searches alone) rather than
  prose that would have to be parsed, and the Planner assembles the `QueryPlan` from that
  and the known question. It may plan 1–3 steps; the step count is enforced in code, and
  `original_query` is set from the true input rather than echoed by the model.
- **Retrieval** runs the steps concurrently, so a three-step plan costs roughly one step's
  latency. Results below the relevance floor are dropped, and the survivors are
  deduplicated across steps, keeping each control's best score.
- **Response** may only cite controls it was given. An empty grounding set short-circuits to
  the safe fallback *without calling a chat model*, and any citation in the answer that was
  not retrieved is excluded from `citations` and logged as a grounding violation.

The three stages are plain sequential awaits — a straight-line chain needs no workflow
engine — and PydanticAI agents hold no per-run state, so one pipeline serves concurrent
queries. The Azure clients are opened once per process by `open_pipeline`, never per
request.

### Retrieval: two search modes, two score scales

`AZURE_SEARCH_SEMANTIC_RANKER` chooses how retrieval searches *and* which score it gates on.
These cannot be chosen independently, because Azure AI Search's scores are not comparable.
Measured against the live 1,014-control index over six on-topic and four off-topic questions:

| Score | on-topic | off-topic | usable as a relevance floor? |
|---|---|---|---|
| `@search.rerankerScore` (semantic) | 2.00 – 3.26 | 0.54 – 1.44 | yes, cleanly |
| `@search.score`, vector-only (rescaled cosine) | 0.635 – 0.776 | 0.517 – 0.576 | yes, narrowly |
| `@search.score`, hybrid (RRF) | 0.028 – 0.032 | 0.024 – 0.032 | **no** |

A hybrid query's `@search.score` is a **Reciprocal Rank Fusion** score, computed from each
document's *rank* in the vector and BM25 lists rather than from how well it matches. Some
document always ranks first, so "What is the capital of France?" scored 0.0323 — matching
the best on-topic question. **No threshold on an RRF score can drive the safe fallback.**

So the agent always gates on the score it ranked by:

- **Ranker on** (Basic tier or above) — hybrid vector + BM25 search with semantic reranking;
  gate on `@search.rerankerScore` ≥ `MIN_RERANKER_SCORE`.
- **Ranker off** (Free tier) — `search_text` is dropped, making it a vector-only search, which
  turns `@search.score` into a cosine similarity; gate on `@search.score` ≥ `MIN_VECTOR_SCORE`.

The Free tier therefore gives up BM25 keyword matching. Measured against this corpus that
costs little, because each document's embedded text is prefixed with its control ID, so
`AC-2` still retrieves `ac-2` at rank one.

## Sample output

[samples/](samples/) holds a smoke test of three questions — two on-topic, one deliberately not:

- `smoke_test.json` — the raw `PipelineResult` for each: plan, per-step hits with scores,
  the deduplicated grounding set, and the answer with its citations.
- `smoke_test.log` — the JSON audit trail those three queries produced, one object per line.

"What is the capital of France?" retrieves nothing above the floor and returns the safe
fallback without ever reaching a chat model.

It also holds the evaluation output over the 15-query golden set (see below):

- `evaluation_report.md` — the aggregate table and a per-query section (plan, retrieved
  controls, both metric views, judge scores, citations, answer).
- `evaluation_transcripts.json` — the same, machine-readable, for every query.

In the committed run, answer quality is strong — groundedness 5.0/5, relevance 5.0/5, and
**zero invented citations** across all 13 on-topic queries — and both out-of-domain queries
returned the safe fallback. Retrieval scored ~0.54 precision / ~0.71 recall on the base-family
view (~0.27 / ~0.51 exact-ID); the gap is the retriever surfacing specific control enhancements
above the broad base controls the golden set labels. Because the pipeline is a reasoning model,
re-running produces slightly different numbers.

## Setup

Requires Python 3.14 (the devcontainer in [.devcontainer/](.devcontainer/) provides it).

```bash
pip install -e ".[dev]"
cp .env.example .env    # then fill in the values
```

Provision the Azure resources first — [docs/azure-setup.md](docs/azure-setup.md) walks
through the portal steps, the tier trade-offs, and where to find each endpoint and key.
`.env` is gitignored and must never be committed. `.env` is resolved relative to the repo
root, not the process's working directory, so both commands below work from anywhere.

## Running the service and CLI

Both wrap the same `PolicyPipeline` (`llm_policy_library.orchestrator`) — there is exactly
one code path from a question to a grounded answer, whether it arrives over HTTP or the CLI.

```bash
uvicorn llm_policy_library.api:app   # POST /query {"query": "..."}, GET /healthz
```

`POST /query` returns `{answer, citations, is_fallback, plan, retrieved, latency_ms}` and
echoes the request's correlation ID as an `X-Correlation-ID` response header. A pipeline
failure (a planner/response error, or an Azure outage) answers with `502` and a fixed safe
message, never a stack trace; a missing or blank `query` answers with `422`.

```bash
python -m llm_policy_library.cli "What controls apply to API security?"
```

prints the plan, the retrieved controls with their scores, and the final answer with its
citations. Its structured logs go to stderr, so stdout carries only the report.

## Running the evaluation

```bash
python evaluation/run_eval.py   # runs the golden set through the live pipeline + judges
```

The harness runs every query in [evaluation/golden_set.json](evaluation/golden_set.json)
through the pipeline and scores four things: retrieval (precision / recall / F1, plus
NDCG@3 / XDCG@3 / fidelity from the Azure AI `DocumentRetrievalEvaluator`), answer quality
(groundedness and relevance from LLM-judge evaluators on the same chat deployment), citation
validity (every inline citation must have been retrieved), and the safe fallback on
out-of-domain queries. It writes [samples/evaluation_report.md](samples/evaluation_report.md)
and `samples/evaluation_transcripts.json`.

Precision/recall/F1 are reported two ways. **Exact-ID** credits only a retrieved control
whose ID matches a labelled one. **Base-family** also credits a retrieved *enhancement* whose
base control was labelled — `ia-2.6` counts toward the `ia-2` need — because in NIST SP 800-53
an enhancement is a more specific form of its base control. The exact-ID column is a strict
lower bound; the base-family column is the fairer measure for this hierarchical catalog, since
the semantic retriever ranks specific enhancements above the broad base control.

## Configuration

All configuration comes from environment variables, read from the process environment or
from `.env` (the process environment wins). [.env.example](.env.example) documents every
variable; [config.py](llm_policy_library/config.py) validates them. A missing or malformed
variable aborts startup with a message naming each variable to fix, rather than failing
later inside an Azure SDK call.

Three settings carry design weight:

- `AZURE_SEARCH_SEMANTIC_RANKER` — semantic reranking needs Basic tier or above. Set it to
  `false` on the Free tier, which switches retrieval to a vector-only search. It also
  selects which relevance floor applies; see the two score scales above.
- `MIN_RERANKER_SCORE` (default `1.8`, scale 0–4) and `MIN_VECTOR_SCORE` (default `0.60`,
  a rescaled cosine) — the relevance floors. **Exactly one applies**, chosen by the flag
  above. Each default sits between the measured on-topic and off-topic score bands, nearer
  the on-topic band, because a compliance system should rather refuse than answer from a
  control it half-matched. A floor of `0` can never trigger the fallback.
- `LLM_REASONING_EFFORT` — every currently deployable Azure OpenAI chat model is a
  reasoning model that rejects `temperature`, `top_p`, and `seed`. Determinism is
  therefore enforced through grounding (structured outputs, a pinned model version,
  citation checks, and a safe fallback) rather than sampling controls. See decision D7 in
  [TODO.md](TODO.md). Note that reasoning models are not bit-exact reproducible: the plan's
  *shape* is guaranteed, its wording is not.

## Logging

Every log line is a single JSON object written to stdout. Fields passed via the stdlib
`extra=` keyword are merged into the payload, and the correlation ID of the enclosing
request is attached automatically:

```python
import logging

from llm_policy_library.logging_setup import configure_logging, correlation_context

configure_logging(level="INFO")
with correlation_context() as correlation_id:
    logging.getLogger(__name__).info("query received", extra={"query": query})
```

The correlation ID lives in a `ContextVar`, so it survives `await` boundaries and stays
isolated between concurrently served requests. The API also echoes it back as an
`X-Correlation-ID` response header, so a caller reporting a problem can quote the ID that
locates it in the log. Logs go to stdout everywhere except the CLI, which writes them to
stderr so they don't interleave with the report it prints to stdout.

Every query writes one line per hop — plan, each retrieval step, the answer with its
citations, and the end-to-end latency — all sharing the request's correlation ID.
`samples/smoke_test.log` is a real example.

Each retrieval step logs both sides of the relevance floor, `kept` and `dropped`, with
scores. A safe fallback is only auditable if the trail says what was rejected and by how
far: for "What is the capital of France?" the best rejected control scored `1.222` against
the `1.8` floor.

The Azure SDK logs a full request/response header dump on every HTTP call, and `httpx` a
line per request, both at INFO. They are pinned to WARNING so they cannot bury the audit
trail; setting `LOG_LEVEL=DEBUG` restores them for troubleshooting.

## Tests and static analysis

All three must pass before any commit:

```bash
pytest              # unit tests + coverage (>=80% per file and overall)
mypy .
ruff check .
```

Tests run in random order (`pytest-randomly`) and are hermetic: they never read the
developer's real `.env` and never call Azure.
