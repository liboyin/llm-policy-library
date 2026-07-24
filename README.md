# llm-policy-library

A multi-agent system that answers enterprise security-policy questions from the
NIST SP 800-53 Rev 5 control catalog. It plans one or more searches, retrieves
controls from Azure AI Search, and uses Azure OpenAI to produce a grounded answer
with checked inline citations.

The current system is implemented end to end. [TODO.md](TODO.md) records what was
delivered, the decisions made along the way, and proposed follow-up work.

## Documentation

- [TASK.md](TASK.md) — project goals and assessment requirements
- [TODO.md](TODO.md) — execution history and resolved decisions
- [docs/architecture.md](docs/architecture.md) — canonical current-state system
  design, rationale, security, scalability, governance, and operability
- [docs/azure-setup.md](docs/azure-setup.md) — Azure provisioning
- [samples/evaluation_report.md](samples/evaluation_report.md) — evaluation
  methodology and detailed quality results
- [samples/loadtest_results.md](samples/loadtest_results.md) — load-test
  methodology, artifacts, capacity model, and limitations

## Architecture at a glance

```text
Browser / HTTP client ──► FastAPI ──┐
                                    ├─► PolicyPipeline
CLI ────────────────────────────────┘       │
                                            ▼
                              Planner ──► Retrieval ──► Response
                                chat       Azure AI       chat
                                            Search
```

The Planner produces one to three search steps as structured data. Retrieval
embeds and searches each step, removes results below the active relevance floor,
and deduplicates the survivors. The Response agent answers only from that
grounding set; when retrieval finds nothing relevant, the pipeline returns a
fixed fallback without making the response chat call.

All stages exchange validated Pydantic messages under a Microsoft Agent Framework
workflow. The [architecture specification](docs/architecture.md) owns the full
interaction flow, concurrency model, grounding guarantees, and design trade-offs.

## Project structure

| Path | Purpose |
|---|---|
| [llm_policy_library/agents/](llm_policy_library/agents/) | Planner, Retrieval, Response, and evaluation-judge agents |
| [llm_policy_library/orchestrator.py](llm_policy_library/orchestrator.py) | Microsoft Agent Framework workflow and `PolicyPipeline` |
| [llm_policy_library/dataset.py](llm_policy_library/dataset.py) | Fetches and parses the pinned NIST OSCAL catalog |
| [llm_policy_library/ingest.py](llm_policy_library/ingest.py) | Builds the Azure AI Search index |
| [llm_policy_library/api.py](llm_policy_library/api.py) | FastAPI service and frontend routes |
| [llm_policy_library/cli.py](llm_policy_library/cli.py) | Command-line client for the same pipeline |
| [llm_policy_library/evaluation.py](llm_policy_library/evaluation.py) | Golden-set evaluation harness |
| [evaluation/](evaluation/) | Golden set and evaluation runner |
| [loadtest/](loadtest/) | Locust workload and tested result aggregation |
| [static/index.html](static/index.html) | Dependency-free browser frontend |
| [samples/](samples/) | Committed evaluation, smoke-test, and load-test evidence |
| [tests/](tests/) | Hermetic unit tests; none make live Azure calls |

## Setup

Python 3.14 or later is required. The devcontainer in
[.devcontainer/](.devcontainer/) provides the expected environment.

```bash
pip install -e ".[dev]"
cp .env.example .env
```

Fill in `.env` after provisioning the resources described in
[docs/azure-setup.md](docs/azure-setup.md). The file is gitignored and must not be
committed. Process environment variables override values in `.env`.

## Ingest the catalog

The demo corpus is the official NIST SP 800-53 Rev 5 OSCAL catalog at a pinned
upstream commit. Ingestion produces one search record per control or control
enhancement.

```bash
python -m llm_policy_library.ingest
```

The command fetches, parses, embeds, recreates, and uploads the index. Input
validation and embedding complete before the existing index is dropped. The
current ingestion behavior and production reindex design are documented in
[architecture.md](docs/architecture.md#operability-zero-downtime-reindex).

## Run the project

Start the API and browser frontend:

```bash
uvicorn llm_policy_library.api:app
```

Then open <http://127.0.0.1:8000>. The service exposes:

- `GET /` — browser frontend
- `GET /healthz` — liveness probe
- `POST /query` — grounded-answer API

`POST /query` returns
`{answer, citations, is_fallback, plan, retrieved, latency_ms}` and echoes the
correlation ID in `X-Correlation-ID`. Invalid queries return `422`; exhausted
request budgets return `429`; pipeline timeouts return `504`; and upstream
failures return a fixed `502` response without exposing exception details.

Run the same pipeline directly from the CLI:

```bash
python -m llm_policy_library.cli "What controls apply to API security?"
```

The CLI prints the plan, retrieved controls, citations, and answer. Its structured
logs go to stderr so stdout contains only the report.

Pushing `main` deploys the service to Azure App Service through
[.github/workflows/main_llm-policy-library.yml](.github/workflows/main_llm-policy-library.yml).

## Evaluate

Run the 15-query hand-labelled golden set through the live pipeline and two
Microsoft Agent Framework judge agents:

```bash
python evaluation/run_eval.py
```

The run reports exact-ID and base-family recall, NDCG@5, faithfulness, answer
relevancy, citation validity, and out-of-domain fallback behavior. It writes
`samples/evaluation_report.md` and `samples/evaluation_transcripts.json`.

## Load test

Disable both request budgets so the test measures the pipeline rather than the
public-endpoint limiter:

```bash
RATE_LIMIT_PER_IP_PER_MINUTE=0 RATE_LIMIT_GLOBAL_PER_MINUTE=0 \
    uvicorn llm_policy_library.api:app --workers 4
```

In another shell:

```bash
locust -f loadtest/locustfile.py --headless -u 10 -r 2 --run-time 6m \
    --host http://127.0.0.1:8000
```

The workload reuses the evaluation golden set. Its quality gates treat an
on-topic fallback or an answer without citations as a failure even when the HTTP
response is `200`.

## Verified results

These are summaries of the committed evidence, not additional result records.
Follow the report links for the workload, methodology, artifacts, and caveats.

| Quality metric | Committed evaluation |
|---|---:|
| Recall, exact-ID / base-family | 0.451 / 0.653 |
| NDCG@5 | 0.490 |
| Faithfulness / answer relevancy | 5.00 / 5.00 |
| Invented citations | 0 |
| Out-of-domain fallbacks | 2/2 |

Source: [evaluation report](samples/evaluation_report.md).

The committed six-minute load test ran on 2026-07-16 with 10 concurrent users:

| On-topic `POST /query` | p50 | p90 | p99 |
|---|---:|---:|---:|
| Latency | 7.4 s | 9.4 s | 12.0 s |

The run recorded no HTTP errors. One on-topic response failed the test's quality
gate because it contained no control citation. The analytical 50-user model
identifies chat RPM as the binding constraint and estimates that the deployment
would need approximately 600K TPM; it does not establish that the latency target
will hold at 50 users. Source:
[load-test and SLA report](samples/loadtest_results.md).

## Configuration

[.env.example](.env.example) is the canonical list of variables and defaults.
[config.py](llm_policy_library/config.py) validates them once at startup.
Operationally important controls include:

| Variable | Default | Purpose |
|---|---:|---|
| `AZURE_SEARCH_SEMANTIC_RANKER` | `true` | Select semantic hybrid search or vector-only search |
| `RETRIEVAL_TOP_K` | `5` | Documents retrieved per Planner step |
| `MIN_RERANKER_SCORE` | `1.8` | Relevance floor with semantic reranking |
| `MIN_VECTOR_SCORE` | `0.60` | Relevance floor with vector-only search |
| `LLM_REASONING_EFFORT` | `minimal` | Chat-model reasoning effort |
| `RATE_LIMIT_PER_IP_PER_MINUTE` | `10` | Per-caller sustained request budget |
| `RATE_LIMIT_GLOBAL_PER_MINUTE` | `30` | Per-process sustained request budget |
| `REQUEST_TIMEOUT_SECONDS` | `60` | API pipeline timeout |
| `LOG_LEVEL` | `INFO` | Application log level |

The architectural reasons for the search modes, relevance floors, grounding
controls, and request budgets are in
[docs/architecture.md](docs/architecture.md).

## Logging

The API writes structured JSON logs to stdout; the CLI writes them to stderr.
All hops share a correlation ID, including the query, plan, kept and dropped
retrieval results, answer, citations, token use, and latency. The API returns the
same ID in `X-Correlation-ID`.

## Tests and static analysis

```bash
pytest
mypy .
ruff check .
```

Tests run in random order and do not read the developer’s real `.env` or call
Azure.
