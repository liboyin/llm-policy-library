# Architecture

A multi-agent RAG system that answers enterprise security-policy questions from the NIST
SP 800-53 Rev 5 control catalog, grounded and auditable end to end. This is the canonical
current-state system design: components and boundaries, dataflow and agent interaction,
design rationale and invariants, determinism and grounding, security, scalability,
governance, and operability.

Implementation history and superseded decisions live in [TODO.md](../TODO.md). Provisioning
lives in [azure-setup.md](azure-setup.md), and setup and operating commands live in the
[README](../README.md). The detailed empirical records are the
[evaluation report](../samples/evaluation_report.md) and
[load-test report](../samples/loadtest_results.md); this specification uses their findings
only where they affect the design.

## System design

```text
Browser (static page) ──┐
Any HTTP client ────────┴─► FastAPI ──┐
                                      ├─► PolicyPipeline ──────────────────────────┐
CLI ──────────────────────────────────┘       │                                     │
                                              ▼                                     ▼
                                Planner ──► Retrieval ──► Response         Azure OpenAI
                                (chat)      (no LLM)      (chat)         gpt-5-mini (pinned)
                                               │                       text-embedding-3-small
                                               ▼
                                       Azure AI Search
                                  (hybrid + semantic rerank)
```

One code path answers every question: the API and CLI both drive the same `PolicyPipeline`;
the browser is a client of the API. Azure clients are opened once per process, never per
request. The pipeline instance holds no per-run workflow state — it builds a fresh
Microsoft Agent Framework workflow and executors per query — so one instance serves
concurrent queries.
Deployment is a plain ASGI app — locally under `uvicorn`, and on Azure App Service via the
GitHub Actions workflow in [.github/workflows/](../.github/workflows/).

## Agent interaction flow

The agents run on the **Microsoft Agent Framework** (MAF). They communicate through typed
Pydantic messages, not conversation — each edge is validated, so a stage cannot reinterpret
what the previous one produced, and the chain of messages **is** the audit record:

1. `str` → **Planner Agent** → `QueryPlan`. A structured-output chat call decomposes the
   question into 1–3 natural-language search steps (`PlanStep{search_query, purpose}`);
   the step count is clamped in code, and `original_query` is set from the true input, not
   echoed by the model.
2. `QueryPlan` → **Retrieval Agent** → `RetrievalOutcome`. No LLM. Each step is embedded
   and searched concurrently (hybrid vector+BM25 with semantic reranking on Basic tier;
   vector-only on Free). Results below a calibrated relevance floor are dropped — the gate
   is always the same score the mode ranked by (`MIN_RERANKER_SCORE` / `MIN_VECTOR_SCORE`;
   the measured score bands are in `agents/retrieval.py`'s docstring) — and survivors are
   deduplicated into one grounding set.

   `RETRIEVAL_TOP_K` (default 5) applies **per search step**, not per question. A
   multi-step plan can therefore ground in more than five controls: the per-step window
   bounds each search's cost and latency, while the merged set preserves evidence from
   each concern in a multi-family question.
3. `RetrievalOutcome` → **Response Agent** → `PipelineResult`. If the grounding set is
   empty, the fixed safe-fallback message is returned **without calling a chat model**.
   Otherwise a chat call answers strictly from the supplied controls with inline `[ac-2]`
   citations, and every citation is checked against the retrieved IDs after the call.

### Retrieval modes and relevance invariant

`AZURE_SEARCH_SEMANTIC_RANKER` selects a complete retrieval mode, not just an optional
post-processing step:

- `true` uses hybrid vector + BM25 retrieval with semantic reranking and gates results on
  `@search.rerankerScore` via `MIN_RERANKER_SCORE`.
- `false` uses vector-only retrieval and gates results on vector `@search.score` via
  `MIN_VECTOR_SCORE`.

The invariant is that retrieval filters on the same score scale that determined the
ranking. The two scores are not comparable, so their thresholds cannot be shared. The
Free-tier path is vector-only rather than hybrid-without-reranking because Azure's fused
RRF score is rank-relative and does not provide a stable absolute relevance floor. This
keeps the empty-grounding fallback meaningful in either mode.

### Workflow and concurrency

A MAF `WorkflowBuilder` wires Planner → Retrieval → Response `Executor`s. The project
depends on `agent-framework-core` and `agent-framework-openai`, not the integration-heavy
meta-package.

A `Workflow` carries one run's state, and each `Executor` serializes its handler behind a
per-instance lock. `PolicyPipeline.answer_query` therefore builds a fresh workflow **and**
fresh executors for every query. The agents and Azure clients they wrap are per-call
stateless and shared safely, so overlapping requests do not contend on a shared executor.
Client lifetime remains process-scoped.

The Planner uses MAF structured output (`response_format`) to enforce the plan's JSON
schema at the model boundary. Pydantic validation then enforces the same contract between
workflow stages.

## Determinism & grounding

The configured `gpt-5-mini` deployment does not use `temperature`, `top_p`, or `seed`
(decision D7 in [TODO.md](../TODO.md)), so determinism is **grounding-enforced rather
than sampling-based**:

- **Structured outputs** — the Planner returns a JSON schema, not prose to parse.
- **Pinned versions** — the model version is pinned on the deployment (`gpt-5-mini`
  `2025-08-07`), the corpus at an exact upstream commit, the prompts in a version-controlled
  store ([prompts.json](../llm_policy_library/prompts.json)).
- **Minimized `reasoning_effort`** (`minimal`) — the least output variance the model offers.
- **Citation enforcement** — an answer may only cite retrieved controls; an ID that was not
  retrieved is excluded from `citations` and logged as a grounding violation, so an invented
  control can never be reported as a source.
- **Safe fallback** — an empty grounding set short-circuits to a fixed template; the
  decision is a threshold on a calibrated relevance score, not a model's judgement.

What is guaranteed is the *shape* of behavior (plan schema, citation validity, fallback on
empty retrieval), not bit-exact reproducibility — reasoning models re-plan slightly
differently run to run. This prevents hallucination more robustly than temperature knobs
did: the model is never asked a question without evidence, and its claims are checked
against that evidence after the fact.

## Security

**Secrets.** API keys arrive only through environment variables (App Service app settings
in the cloud; a gitignored `.env` locally) and are held as `SecretStr`, so they are redacted
from logs and tracebacks. A pipeline failure answers with a fixed 502 message — Azure
exception text, which can name internal endpoints, never reaches a client.

**Production identity path.** Keys are the demo trade-off. The hardening path is Entra ID
with a managed identity on the App Service: `DefaultAzureCredential` in place of key
credentials, RBAC-scoped roles (`Cognitive Services OpenAI User` for inference;
`Search Index Data Reader` for serving, with the ingestion job alone holding
`Search Index Data Contributor`), and local/key auth disabled on both resources. That
removes rotatable secrets entirely and splits read from write privileges.

**Network isolation.** The demo uses public endpoints over TLS. Production: Private
Endpoints on Azure OpenAI and AI Search, the App Service VNet-integrated, and public network
access disabled on both back ends — the model and the index then only answer from inside
the VNet. Data residency is a deployment-type choice (Regional vs Global Standard; see
[azure-setup.md](azure-setup.md)).

**Prompt retention.** The Agent Framework chat client targets the Azure OpenAI Responses API,
where `store` defaults to **true**, so every prompt and answer is retained server-side in the
Azure OpenAI resource. This is left on **for the demo only** — it aids debugging and replay. In
a regulated production environment it **MUST be turned off**: set `store: False` in the chat
agents' `default_options` (`agents.planner.build_planner` and `agents.response.build_response_agent`,
and the two judges in `agents.judges`) so the platform persists no policy question or grounded
answer. Grounding and audit do not depend on it — the application's own JSON audit trail
(citation-checked, correlation-ID'd) is the record of what was asked and answered.

**Inbound exposure: the endpoint is open by design, so the budget is the control.**
`POST /query` is deliberately unauthenticated — the demo is meant to be opened and used —
which means an anonymous caller can spend the owner's Azure OpenAI quota, roughly two chat
calls per request against a 150 RPM deployment. Authentication is the wrong lever for a
public demo; a request budget is the right one, so the API meters `/query` (and only
`/query` — throttling the page or the health probe would protect nothing and break the
platform's liveness checks) with two token buckets, in
[rate_limit.py](../llm_policy_library/rate_limit.py):

- **Per caller** (`RATE_LIMIT_PER_IP_PER_MINUTE`, default 10/min) bounds any single abuser
  and keeps one caller from starving the rest. A query takes ~7 s, so a person sustains at
  most ~8/min; 10 leaves a human room to think and stops a script cold.
- **Global, per process** (`RATE_LIMIT_GLOBAL_PER_MINUTE`, default 30/min) bounds the case
  the per-caller budget cannot — many distinct callers each individually under their limit.
  This is the budget that actually caps the bill.

Both numbers are **sustained rates, not window ceilings**. A token bucket holds a full
bucketful in reserve, so an idle process admits its burst *and then* earns another minute's
worth: the worst case in any 60 s window is **2×** the configured rate. The burst is
deliberate — it is what lets someone click three example questions in a row without being
punished — so the defaults are sized against 2×, not 1×: 30/min sustained is 60 req/min
worst-case ≈ 120 chat calls, comfortably inside the 150 RPM quota that ~75 req/min would
exhaust. (A test pins this 2×, so the quota reasoning cannot drift away from the code.)

A rejected request is refused *before* the pipeline runs, so it reaches no model and costs
nothing, and it carries `Retry-After` so a well-behaved client backs off correctly. The
caller is identified by `X-Client-IP`, which the App Service front end sets and **overwrites**
on every request; `X-Forwarded-For` is deliberately not trusted, because App Service appends
to whatever the client sent, making its leftmost entry attacker-controlled — a limiter keyed
on it could be bypassed by rotating a header. Two companion limits bound per-request cost
rather than request count: a 2,000-character cap on the question (a megabyte of text would
otherwise be embedded and sent to a chat model at the owner's expense) and a
`REQUEST_TIMEOUT_SECONDS` budget on the pipeline, after which the request is abandoned with
a 504 rather than holding a worker open.

Residual risks, stated plainly rather than papered over:

- **The buckets are per process, and the defaults assume exactly one worker.** There is no
  headroom for a second: the worst case already spends ~120 of the deployment's 150 RPM, so
  *N* workers multiply the ceiling by *N* and overrun the quota. Run one worker, or divide
  the budgets by the worker count. A shared counter (Redis) is what a genuinely
  multi-instance deployment would need, and is deliberately not built for a demo.
- **"Inside quota" is a per-minute statement, and Azure enforces RPM over sub-minute
  windows.** The deliberate burst can put dozens of near-simultaneous planner calls on the
  wire in a few seconds, which can still draw an upstream 429 — surfacing to the client as a
  502 — even while the *spend* stays bounded. Bounding cost is what this mechanism is for;
  smoothing traffic is not.
- **A caller off App Service can forge `X-Client-IP`** and mint fresh per-client budgets,
  because the header is trusted wherever it appears. On the deployed service the front end
  overwrites it, so this is not exploitable there; the global budget bounds the spend
  regardless. Any host without an overwriting front end must strip the header at its edge.

A distributed flood large enough to exhaust the global budget degrades the demo into 429s
rather than an unbounded bill, which is the intended failure direction. Azure Front Door's
WAF (per-IP rate limiting at the edge) is the platform-level hardening path if the endpoint
ever needs to survive deliberate attack rather than merely bound its cost.

**Prompt-injection surface.** Two inputs reach a chat model: the user's question and the
retrieved control text. The corpus is trusted today (official NIST content at a pinned
commit), but the design assumes neither input is safe: the Planner emits only a constrained
JSON schema (clamped to 3 steps — a hijacked plan can waste a search, not exfiltrate); the
Response Agent's output is checked against the citation allow-list; and the browser
frontend writes model output to the DOM exclusively via `textContent`, never `innerHTML`,
so neither a crafted question nor a poisoned corpus line can become script in a reader's
browser. The agents have no tools — nothing an injected instruction could invoke. Residual
risk: a future multi-source corpus (the 5M-word case) makes document text attacker-adjacent;
ingestion-time content screening and per-source provenance fields become required, and the
citation check plus the audit trail are the detection layer.

## Scalability

### The 5M-word production corpus (design & capacity)

The demo ingests the real 1,014-control catalog; a ~5M-word corpus (~7M tokens at ~1.33
tokens/word) changes ingestion, not the serving architecture:

- **Chunking.** Controls are already retrieval-sized (one requirement per document). A
  heterogeneous policy corpus is split on document structure first (section/clause), capped
  at ~400 tokens with ~15% overlap only where a section must be cut mid-flow. Each chunk
  keeps a stable citable ID plus `title`/`category`/source-document fields — the citation
  unit is the retrieval unit, which is what citation-enforced grounding requires.
- **Index sizing.** ~7M tokens / 400 ≈ **18K chunks**; vectors at 1,536 × 4 bytes ≈
  **110 MB** before index overhead and BM25 postings. Partition storage is not the
  limiting dimension at this size.
- **Replicas** are the serving-side lever for query throughput and availability; scale
  them from measured QPS.
- **Embedding work.** A full rebuild embeds approximately 7M tokens. Its monetary cost
  should be recalculated from the deployment's current price rather than embedded as a
  long-lived architecture constant.

### Capacity implications for the 50-user target

The canonical measurements, workload, arithmetic, and limitations are in the
[load-test report](../samples/loadtest_results.md). The design target is 50 concurrent
users at p90 ≤ 15 s and p99 ≤ 30 s (decision D3: scaled test + extrapolation).

- **The 10-user evidence meets the latency target**: the committed run measured on-topic
  p90 **9.4 s** and p99 **12.0 s**, with no HTTP errors.
- **The binding constraint at 50 users is chat RPM, not TPM and not latency.** The
  report's calibrated model puts 50 users at ≈5.3 RPS and **550 chat RPM** against the
  measured deployment quota of 150 RPM. At that deployment's TPM-to-RPM ratio, the design
  needs **≈600K TPM**. Search reaches ≈8.3 QPS, so replicas are the initial scaling lever.
- **Whether p90/p99 still hold at 50 users is deliberately left open**: the extrapolation
  assumes constant response time, so it cannot prove it, and the current quota caps any
  honest run at ~14 users. Re-measure once quota exists.
- **Capacity levers**: avoid the Planner call on safely classified simple queries to reduce
  the binding RPM demand; use provisioned throughput if the tail must be contractual;
  stream responses only for perceived latency, not as a change to complete-response p90.

## Governance controls

- **Audit trail.** Every request logs one JSON line per hop — query, plan, each step's
  `kept` *and* `dropped` documents with scores, the answer text, citations, token counts,
  latency — all sharing a correlation ID that is also echoed to the client as
  `X-Correlation-ID`. A fallback is auditable: the trail shows what was rejected and by how
  much (and thresholds are retuned against it).
- **Citation-enforced grounding** and the **safe fallback**, as above. The current fallback
  is triggered by an empty grounding set, not an independent domain decision. The load
  evidence therefore includes out-of-domain questions whose generated search happened to
  clear the relevance floor: the Response Agent refused in prose, but
  `is_fallback=false`. The detailed observations live in the
  [load-test report](../samples/loadtest_results.md); a Planner-level structural domain
  decision is proposed, but not implemented, in [TODO.md](../TODO.md).
- **Evaluation gate.** A 15-query hand-labeled golden set scores retrieval with
  deterministic recall/NDCG@5 and answers with two LLM judges (faithfulness, relevancy),
  plus a hard citation-validity check and fallback checks. Prompts live in the
  version-controlled store, so a prompt change is a reviewable diff that can be re-scored
  against the golden set before it ships. The
  [evaluation report](../samples/evaluation_report.md) owns the current results.

## Operability: zero-downtime reindex

`ingest.py` today drops and recreates the index. Everything that can fail on bad input
(fetch, parse, the 500-record floor, embedding, vector-width check) runs *before* the drop,
so those failures leave the old index serving — but a rejected **upload batch** after the
drop leaves the index partially populated until the idempotent command is re-run. The
production design is **build-then-alias-swap**: ingest into `<index>-<timestamp>`, validate
the document count, repoint a stable alias the query path reads, then delete the
predecessor. Queries never see a partial index. Trade-offs: one extra index against the
tier's index quota during the swap, and orphaned-index cleanup if a run dies between build
and swap.
