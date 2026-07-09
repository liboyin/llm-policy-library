# Execution Plan

This plan implements [TASK.md](TASK.md). All work MUST follow [CLAUDE.md](CLAUDE.md).
Phases are ordered by dependency; each phase ends in a commit (see "Definition of done per phase" at the bottom).

## Resolved Decisions (agreed with user, 2026-07-08)

| # | Ambiguity | Decision |
|---|-----------|----------|
| D1 | Suggested HF dataset (`AYI-NEDJIMI/nist-csf-en`) has only **91 records**, below the 500 minimum | Use the **NIST SP 800-53 Rev 5 control catalog** (official NIST OSCAL JSON, ~1,190 controls incl. enhancements; ID/title/statement/family map to title/description/category) |
| D2 | ~5M-word production corpus (additional req 1) | **Design & capacity analysis only** in the architecture doc; the demo ingests the real ~1,190-record catalog |
| D3 | 50-user p90≤15s / p99≤30s SLA (additional req 2) | **Scaled load test** (~10 concurrent users) + **analytical extrapolation** to 50 users |
| D4 | No Azure resources exist (`.env` empty) | **User provisions manually**; we deliver step-by-step instructions incl. tier/model options. Code must work with either Free-tier Search (no semantic ranker) or Basic+ (semantic ranker), toggled by env var |
| D5 | Evaluation rigor (user wants precision/recall) | **Azure AI Evaluation SDK** (`azure-ai-evaluation`): `DocumentRetrievalEvaluator` fed by a hand-labeled golden set (gives precision/recall/NDCG) + `GroundednessEvaluator`/`RelevanceEvaluator` for final answers |
| D6 | Serving surface | **FastAPI service** (async `/query`, load-testable) + **thin CLI** wrapping the same pipeline |
| D7 | All non-reasoning Azure OpenAI chat models became **undeployable** by provisioning time (gpt-4.1/gpt-4o family **deprecated** — no new deployments; `*-chat` variants **retired**), and every deployable OpenAI chat model is now a **reasoning model that rejects `temperature`/`top_p`/`seed`** (verified against the Microsoft Foundry model-retirement schedule, 2026-07-08) | Chat model = **`gpt-5-mini`** (option: `gpt-5.1` with `reasoning_effort=none` for lowest latency). **Determinism is redefined** as grounding-enforced rather than sampling-based: structured outputs (JSON schema) + pinned model version + minimized `reasoning_effort` + citation-enforced grounding + safe fallback. `temperature`/`seed`/`top_p` are dropped (unsupported). Embeddings unchanged (`text-embedding-3-small`, still GA). |

Decisions made by the agent (uncontroversial, state-and-proceed):

- **Agent framework**: `agent-framework` (Microsoft Agent Framework, Python) with a sequential **Workflow**: Planner → Retrieval → Response executors, communicating via typed Pydantic messages.
- **Models** (superseded by D7): chat = **`gpt-5-mini`** (mini-class GA reasoning model; `gpt-5.1` with `reasoning_effort=none` is the low-latency option); embeddings = `text-embedding-3-small` (GA until 2027-04-15). The originally-planned `gpt-4.1-mini` and all non-reasoning chat models are no longer deployable — see D7. Chat availability skews to **Global Standard**; embeddings available under both deployment types. Re-check the Foundry deploy dialog at provisioning time.
- **Region**: all resources in **Australia East (Sydney)** — the only Australian region with Azure OpenAI model deployments and semantic-ranker-capable AI Search. Data Zone deployments do not exist for APAC, so the deployment-type choice is regional Standard (inference stays in Australia) vs Global Standard (may be processed in any Azure region).
- **Search mode**: hybrid (vector + BM25), with semantic reranking layered on when `AZURE_SEARCH_SEMANTIC_RANKER=true`.
- **Determinism** (revised per D7): deployable Azure OpenAI models are all reasoning models that reject `temperature`/`top_p`/`seed`, so determinism is **grounding-enforced, not sampling-based** — structured outputs (JSON schema) for the Planner, a **pinned model version**, minimized `reasoning_effort` (`LLM_REASONING_EFFORT`, default `minimal`), and hard citation-enforced grounding + safe fallback. Document that this prevents hallucination more robustly than temperature knobs ever did, and that reasoning models are not bit-exact reproducible.
- **Grounding/fallback**: Response agent may only cite retrieved controls (inline `[AC-2]`-style citations); if retrieval returns nothing above `MIN_RELEVANCE_SCORE`, return the fixed safe-fallback message without calling the Response agent.
- **Logging**: stdlib `logging` with a JSON formatter; every request logs query, plan, retrieved doc IDs + scores, response, latency, correlation ID.
- **Auth**: API keys via env vars for the demo; Entra ID / managed identity discussed in the architecture doc's security section.

### Known risks (check early, escalate to user if hit)

- **Python 3.14**: ~~resolved~~ — a full `pip install --dry-run` of every planned dependency resolved cleanly on Python 3.14 (verified 2026-07-08), so the devcontainer's interpreter stays. The user has confirmed 3.14 is not a hard requirement: if a runtime (not install-time) incompatibility surfaces later, downgrade `requires-python` to the newest version all deps support — no need to stop and ask.
- **Model availability churn**: the chat-model landscape shifted materially between planning and provisioning (see D7) — the entire GPT-4.x/non-reasoning generation went undeployable and only gpt-5-family reasoning models remain. Availability tables change monthly; re-check the Foundry deploy dialog at provisioning time and re-confirm the chosen `gpt-5-mini`/`gpt-5.1` is still deployable in Australia East.
- **Semantic ranker**: requires Basic tier or above. Code path must degrade gracefully to hybrid-only.

---

## Phase 0 — Azure provisioning guide (unblocks user; do first)

Deliverable: `docs/azure-setup.md` + `.env.example`. The user provisions while later phases proceed (everything through Phase 3 is testable with mocks).

- [x] Write `docs/azure-setup.md` instructing the user to create, via Azure Portal (all in **Australia East**):
  1. Resource group (suggest `rg-llm-policy-library`, region `australiaeast`).
  2. Azure OpenAI resource with two deployments, choosing a **deployment type** first (no APAC Data Zone option exists):
     - **Global Standard** (recommended for this assessment): higher default TPM quota (helps the load test); prompts/responses may be processed in any Azure region, data at rest stays in the Australia geography.
     - **Regional Standard**: all inference stays in Australia East — the right choice if simulating strict AU data-residency; lower default quota, and gpt-5-family chat availability is narrower regionally (confirm in the deploy dialog).
     - chat: `gpt-5-mini` — current GA mini-class model (options: `gpt-5.1` = higher quality + `reasoning_effort=none` low-latency mode; `gpt-5` = highest quality). The GPT-4.x/`*-chat` non-reasoning models are deprecated/retired and no longer deployable (see D7); pin an explicit version, avoid the floating `gpt-chat-latest` alias. Request ≥100K TPM if quota allows (needed for load test).
     - embeddings: `text-embedding-3-small` — available in Australia East under both types (option: `-3-large` for quality, also available; 6× embedding cost).
  3. Azure AI Search service in `australiaeast`, with the tier trade-off spelled out:
     - **Free**: $0, 50 MB, 3 indexes, **no semantic ranker** → set `AZURE_SEARCH_SEMANTIC_RANKER=false`.
     - **Basic** (recommended): ~US$75/mo prorated hourly (a few dollars if deleted after the assessment); semantic ranker is confirmed available in Australia East → `true`.
  4. Where to find each endpoint/key, and a teardown checklist (delete the resource group).
- [x] Write `.env.example` with every variable the code will read:
  `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_CHAT_DEPLOYMENT`, `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_API_KEY`, `AZURE_SEARCH_INDEX_NAME`, `AZURE_SEARCH_SEMANTIC_RANKER`, `MIN_RELEVANCE_SCORE`, `RETRIEVAL_TOP_K` (default 5), `LLM_REASONING_EFFORT` (default `minimal`; per D7, replaces the unusable `LLM_SEED`), `LOG_LEVEL`.
- [x] Notify the user to provision and fill in `.env`.

**Status (verified live 2026-07-09):** Phase 0 complete, all services verified. Azure OpenAI endpoint + key work; both deployments respond (`gpt-5-mini`, pinned version `2025-08-07`, and `text-embedding-3-small`). Azure AI Search authenticates with the admin key (an initial copy-paste slip in `.env` was fixed by the user); the service is **Basic tier** (15-index/15 GB quota), so `AZURE_SEARCH_SEMANTIC_RANKER=true` is correct. No indexes exist yet — Phase 2 ingestion creates `nist-800-53-controls`. Note: the legacy `GET /openai/deployments` listing route 404s on current api-versions (it still answers on pre-2023-05 ones) — harmless; inference and `/openai/v1/models` routes work, and Phase 1+ code should use the standard SDK clients, not that listing route.

## Phase 1 — Project scaffolding

- [x] Add runtime deps to `pyproject.toml`: `agent-framework-core` (NOT the `agent-framework` meta-package — it pulls ~40 optional integrations like Bedrock/Ollama/Redis; core includes the Azure OpenAI clients and workflow engine), `azure-search-documents`, `openai`, `azure-ai-evaluation`, `fastapi`, `uvicorn`, `pydantic`, `pydantic-settings`, `python-dotenv`, `httpx`. Dev deps: add `locust`, `respx` (or equivalent for HTTP mocking).
- [x] Install and import-smoke-test the deps on Python 3.14 (dry-run resolution already verified 2026-07-08; per the risk note above, downgrade Python only if a real runtime incompatibility appears — user pre-approved).
- [x] `llm_policy_library/config.py`: `pydantic-settings` `Settings` class reading the env vars above; fail fast with a clear error on missing required vars.
- [x] `llm_policy_library/logging_setup.py`: JSON-formatted structured logging; helper to bind a per-request correlation ID.
- [x] Unit tests for both modules; `pytest`, `mypy .`, `ruff check .` green.

**Status (2026-07-09):** Phase 1 complete. 31 tests, 100% line/branch coverage on both modules; `mypy .` and `ruff check .` clean. Notes for later phases:

- **`azure-search-documents` resolved to 12.0.0**, a new major (not the 11.5.x assumed at planning time). Phase 2 must be written against the 12.x index/vector API.
- **Python 3.14 needs no downgrade** — every dependency installed and imported cleanly. Two cosmetic quirks: `locust` must be imported before anything that touches `ssl` (gevent monkey-patching), and it prints a harmless `greenlet is being finalized` traceback at interpreter shutdown. Neither affects Phase 6, which runs `locust` as its own process.
- **`_env_file` needs `# type: ignore[call-arg]`**: pydantic's PEP 681 `dataclass_transform` makes mypy synthesize `Settings.__init__` from the fields alone, hiding the argument `BaseSettings.__init__` really accepts.
- **A pinned chat-model version is not yet wired into config** — D7 calls for pinning, and the deployment (`gpt-5-mini`, version `2025-08-07`) is pinned in Azure rather than in code. Revisit when the Phase 3 agents construct the chat client.

Carried forward from the Phase 1 review (deliberately out of Phase 1 scope):

- **Phase 3 — `MIN_RELEVANCE_SCORE` straddles two score scales.** The threshold is compared against `@search.score` (RRF, ~0.0x) when `AZURE_SEARCH_SEMANTIC_RANKER=false` but against `@search.rerankerScore` (0–4) when it is `true`. One fixed `0.02` default cannot suit both: it must be scale-aware, or a second threshold is needed, otherwise the ranker toggle silently over- or under-filters and the safe fallback misfires.
- **Phase 4 — `load_settings()` is uncached and its `.env` path is CWD-relative.** Calling it per request would do blocking file I/O on the event loop, and launching `uvicorn`/the CLI from outside the repo root silently reads a different `.env` (or none). Load settings once at startup, or add a cached accessor resolving the dotenv path absolutely.

## Phase 2 — Data ingestion (Azure AI Search index)

- [ ] `llm_policy_library/dataset.py`: download the NIST SP 800-53 Rev 5 OSCAL JSON catalog (pin the exact URL/commit from the `usnistgov/oscal-content` GitHub repo); parse controls **and control enhancements** into a `PolicyRecord` Pydantic model: `id` (e.g. `ac-2.1`), `title`, `description` (resolved statement prose, with OSCAL parameter placeholders like `{{ insert }}` rendered readably), `category` (control family, e.g. "Access Control"). Pure parsing functions; network fetch isolated. Assert ≥500 records after parsing (expect ~1,190).
- [ ] `llm_policy_library/search_index.py`: create/recreate the index — fields: `id` (key), `title`, `description`, `category` (filterable/facetable), `content` (searchable concatenation), `embedding` (vector, HNSW); vector search profile; semantic configuration (created unconditionally — harmless on Basic, skipped on Free where the API rejects it, so guard by the flag).
- [ ] `llm_policy_library/ingest.py`: CLI entry point (`python -m llm_policy_library.ingest`) — fetch → parse → batch-embed (respect batch size limits, retry with backoff) → upload in batches → log counts. Idempotent (re-running rebuilds cleanly).
- [ ] Unit tests: parsing on a bundled OSCAL fixture (small excerpt checked into `tests/fixtures/`), index-schema construction, batching logic. No live Azure calls in tests.
- [ ] Once user's `.env` is ready: run ingestion for real, record document count in the phase commit message.

## Phase 3 — Multi-agent system (Microsoft Agent Framework)

Structured messages first — these are the contracts (in `llm_policy_library/models.py`):

- `QueryPlan`: `original_query: str`, `steps: list[PlanStep]` where `PlanStep` = `{search_query: str, purpose: str}` (1–3 steps).
- `RetrievalResult`: `step: PlanStep`, `documents: list[RetrievedDocument]` where `RetrievedDocument` = `{id, title, description, category, score}`.
- `GroundedResponse`: `answer: str`, `citations: list[str]` (control IDs), `is_fallback: bool`.

Agents (each its own module, single responsibility):

- [ ] `llm_policy_library/agents/planner.py` — **Planner Agent**: chat agent with structured output (`QueryPlan` JSON schema) and minimized `reasoning_effort` (per D7; no `temperature`/`seed`). Decomposes the user query into 1–3 search steps.
- [ ] `llm_policy_library/agents/retrieval.py` — **Retrieval Agent**: no LLM; executes each `PlanStep` against Azure AI Search (hybrid vector+BM25, semantic reranking if enabled), returns top `RETRIEVAL_TOP_K` (3–5) docs per step, drops results below `MIN_RELEVANCE_SCORE`, deduplicates across steps.
- [ ] `llm_policy_library/agents/response.py` — **Response Agent**: chat agent with minimized `reasoning_effort` (per D7; no `temperature`/`seed`); system prompt mandates answering **only** from the provided documents with `[control-id]` citations; if the document set is empty → workflow short-circuits to the safe fallback (`GroundedResponse(is_fallback=True)`) without an LLM call.
- [ ] `llm_policy_library/orchestrator.py`: Microsoft Agent Framework **Workflow** (`WorkflowBuilder`) wiring Planner → Retrieval → Response executors with the typed messages above; exposes `async def answer_query(query: str) -> PipelineResult` (plan + retrieved docs + response, for logging/eval). Structured logging at each hop.
- [ ] Unit tests: each agent in isolation (mock chat client / mock search client via `patch.object`), orchestration flow with all agents mocked, fallback path, citation extraction. Coverage ≥80% per file.
- [ ] Smoke-test live with 1–2 queries once Azure is up; save raw output to `samples/`.

## Phase 4 — Serving: FastAPI + CLI

- [ ] `llm_policy_library/api.py`: async FastAPI app — `POST /query` `{query: str}` → `{answer, citations, is_fallback, plan, retrieved: [...], latency_ms}`; `GET /healthz`. Error handling: upstream Azure failures → 502 with a safe message (never a stack trace); validation errors → 422. Per-request structured log lines (input and output — a TASK requirement).
- [ ] `llm_policy_library/cli.py`: `python -m llm_policy_library.cli "What controls apply to API security?"` → pretty-prints plan, retrieved docs, final answer. Same pipeline, no duplicated logic.
- [ ] Unit tests: API via `TestClient` with the orchestrator mocked (success, fallback, upstream-failure → 502); CLI output formatting.

## Phase 5 — Evaluation

- [ ] `evaluation/golden_set.json`: ~15 queries (must include the 4 from TASK.md) each hand-labeled with the relevant 800-53 control IDs (qrels). Label by reading the catalog — controls are well-defined, so this is tractable.
- [ ] `evaluation/run_eval.py`: for each query, run the full pipeline, then:
  - retrieval quality: `azure-ai-evaluation` `DocumentRetrievalEvaluator` with the golden qrels → precision/recall/NDCG/XDCG@k (addresses user's precision/recall requirement);
  - answer quality: `GroundednessEvaluator` + `RelevanceEvaluator` (LLM-judge via the same Azure OpenAI deployment);
  - citation validity: assert every cited ID was actually retrieved (hard grounding check, no LLM needed);
  - fallback check: 1–2 deliberately out-of-domain queries (e.g. "What is the capital of France?") must return the safe fallback.
- [ ] Emit `samples/evaluation_report.md` (per-query retrieval hits, answers, scores, aggregate table) — this is the TASK "sample execution outputs" deliverable, plus per-query transcripts in `samples/`.
- [ ] Unit tests for the report-building/citation-check logic (evaluators themselves are not unit-tested; they're mocked).

## Phase 6 — Load test + SLA extrapolation

- [ ] `loadtest/locustfile.py`: drives `POST /query` with a realistic query mix; run at ~10 concurrent users for ≥5 minutes against uvicorn (multiple workers).
- [ ] Record p50/p90/p99 latency + error rate → `samples/loadtest_results.md`.
- [ ] Extrapolation memo (feeds the architecture doc): tokens per request (measured) × 50 users vs deployment TPM/RPM quota; Search QPS vs replica count; conclude what quota/replicas/PTU the 50-user p90≤15s / p99≤30s SLA needs. State measured single-request latency as the floor. If using a Global Standard deployment, note that inference may route outside Australia East, so latency has higher variance than a regional deployment — call this out when interpreting p99.

## Phase 7 — Documentation

- [ ] `docs/architecture.md` (1–2 pages): system diagram, agent interaction flow (sequence of typed messages), determinism & grounding design, security (key handling, Entra ID/managed identity path, network isolation, prompt-injection surface), scalability (**the 5M-word design**: chunking strategy, index sizing ~7M tokens ≈ well within Basic/S1 limits, partition/replica math, embedding cost; plus the SLA extrapolation from Phase 6), governance controls (logging/audit trail, citation-enforced grounding, safe fallback, eval gate).
- [ ] `README.md`: project structure, setup (link to `docs/azure-setup.md`), how to ingest / run API / run CLI / run tests / run eval / run load test; design decisions & assumptions (mirror the Resolved Decisions table). Per CLAUDE.md, README owns structure/architecture-summary; it links to `docs/architecture.md` rather than duplicating it.
- [ ] Verify every doc reflects final reality (CLAUDE.md documentation rule).

## Phase 8 — Alternative approaches (additional req 4; decision point)

Only after phases 0–7 are done and committed. Present these candidates to the user and let them pick (or skip):

1. **Azure AI Search agentic retrieval** (knowledge agents): the Search service itself does LLM query planning — contrast with our hand-rolled Planner; least code to demo.
2. **Single-agent RAG with function tools**: one agent with a `search` tool replaces the 3-agent pipeline — a simplicity/latency baseline that would strengthen the SLA story.
3. **GraphRAG over the control catalog**: exploits 800-53's rich `related-controls` links for multi-hop questions.

- [ ] STOP and confirm with the user which (if any) to build before writing code.

---

## Definition of done, per phase (from CLAUDE.md — non-negotiable)

1. `pytest` (coverage ≥80% per file and overall, review with `--cov-report=term-missing`), `mypy .`, `ruff check .` all pass.
2. Google-style docstrings on all new functions; one-liners on tests; tests ordered to match source order; module under test imported as `testee`.
3. Docs updated in the same commit if behavior/structure changed.
4. Adversarial review **in a subagent** before committing; fix trivial findings, escalate the rest.
5. **Gap review with Fable 5** (all phases except Phase 0): after the phase is otherwise done, run a review in a subagent on the `claude-fable-5` model (Agent tool, `model: "fable"`) that checks for completeness rather than bugs — every checklist item of the phase actually delivered, the phase's TASK.md requirements fully met, no silently skipped or half-done steps, and docs/tests reflecting the final state. Fix gaps before committing; record "no gaps found" (or the gaps fixed) in the commit body.
6. Commit message template: `Claude: <one-line summary>` + one detail paragraph; no Co-Authored-By line.
