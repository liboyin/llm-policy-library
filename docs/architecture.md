# Architecture

A multi-agent RAG system that answers enterprise security-policy questions from the NIST
SP 800-53 Rev 5 control catalog, grounded and auditable end to end. This document covers
system design, the agent interaction flow, determinism & grounding, security, scalability
(including the 5M-word production design and the 50-user SLA analysis), governance, and
operability. Setup lives in [azure-setup.md](azure-setup.md); running it lives in the
[README](../README.md).

## System design

```
Browser (static page)  ─┐
CLI (same pipeline)     ├─► FastAPI  ──►  PolicyPipeline ──────────────────────────┐
Any HTTP client        ─┘   POST /query     │                                      │
                                            ▼                                      ▼
                              Planner ──► Retrieval ──► Response          Azure OpenAI
                              (chat)      (no LLM)      (chat)          gpt-5-mini (pinned)
                                             │                        text-embedding-3-small
                                             ▼
                                     Azure AI Search
                                (hybrid + semantic rerank)
```

One code path answers every question: the browser page and the CLI are both clients of the
same `PolicyPipeline` the API wraps. Azure clients are opened once per process, never per
request; the pipeline instance holds no per-run state — it builds a fresh MAF workflow and
executors per query — so one instance serves concurrent queries.
Deployment is a plain ASGI app — locally under `uvicorn`, and on Azure App Service via the
GitHub Actions workflow in [.github/workflows/](../.github/workflows/).

## Agent interaction flow

The agents run on the **Microsoft Agent Framework** (MAF) — the framework TASK.md names.
The project started on it, took a Phase 5.5 detour to PydanticAI, and returned to it in
Phase 9 (see [TODO.md](../TODO.md)). They communicate through typed Pydantic messages, not
conversation — each edge is validated, so a stage cannot reinterpret what the previous one
produced, and the chain of messages **is** the audit record:

1. `str` → **Planner Agent** → `QueryPlan`. A structured-output chat call decomposes the
   question into 1–3 natural-language search steps (`PlanStep{search_query, purpose}`) — or into
   **zero** steps, the one valid stepless plan, when it marks the question `out_of_domain`;
   the step count is clamped in code, and `original_query` is set from the true input, not
   echoed by the model. The call is prefixed with the **corpus map** (below), which lets the
   Planner mark a question `out_of_domain` — a refusal that skips retrieval entirely. A model
   that returns no usable step at all is not an error: the question is searched as asked, so a
   refusal the Planner cannot verify costs a worse search rather than the answer.
2. `QueryPlan` → **Retrieval Agent** → `RetrievalOutcome`. No LLM. Each step is embedded
   and searched concurrently (hybrid vector+BM25 with semantic reranking on Basic tier;
   vector-only on Free). Results below a calibrated relevance floor are dropped — the gate
   is always the same score the mode ranked by (`MIN_RERANKER_SCORE` / `MIN_VECTOR_SCORE`;
   the measured score bands are in `agents/retrieval.py`'s docstring) — and survivors are
   deduplicated into one grounding set.

   `RETRIEVAL_TOP_K` (default 5) is TASK.md's top-3–5 window, and it applies **per search
   step**, not per question. A multi-step plan therefore grounds in more than five
   controls: across the committed evaluation's 13 on-topic queries the merged set — after
   the relevance floor and deduplication — ran from 5 controls (single-step plans) to 10
   (a two-step plan), and 4 of the 13 exceeded 5. That is decomposition working as
   intended rather than a widened retrieval window: each search returns its own top 5, and
   a question spanning access control *and* logging legitimately needs both families. The
   per-step window is what bounds cost and latency; the union is what the answer is
   grounded in, and every control in it is cited or discarded on the evidence of the
   audit trail.
3. `RetrievalOutcome` → **Response Agent** → `PipelineResult`. If the grounding set is
   empty, the fixed safe-fallback message is returned **without calling a chat model**.
   Otherwise a chat call answers strictly from the supplied controls with inline `[ac-2]`
   citations, and every citation is checked against the retrieved IDs after the call.

### The corpus map: a routing index for the Planner

Without it the Planner plans **blind** — it writes search queries having never seen what the corpus
contains, so the only way it can discover that a question is out of domain is to search for it and
find nothing. The map (`PLANNER_CORPUS_MAP`, on by default) prefixes every planner call with all
twenty control families and what each covers — ~899 tokens, or ~1,053 measured with its two routing
rules included:
[corpus_map.json](../llm_policy_library/corpus_map.json), generated by
`python -m llm_policy_library.build_map` from the pinned catalog and version-controlled as a
reviewable artifact — it records the catalog URL and commit it was written from, so map-versus-corpus
drift fails a test rather than going unnoticed.

An abstract's goal is **routing discrimination, not compression**: each is written knowing its
sibling families' names so the entries stay mutually distinguishable, capped at 200 characters
(enforced on load, not by the model's output schema — a schema cap truncates mid-word instead of
rejecting), and judged by the golden-set harness rather than by any intrinsic ratio.

What it buys is a **structural refusal path**. When the Planner judges that no family covers the
question, retrieval is skipped entirely and the fixed fallback is served: measured over three
evaluation runs per setting (2026-07-24), the map took that path for both out-of-domain golden
queries in **3/3 runs**, where the map-off baseline refused them only incidentally — by searching
and happening to find nothing above the relevance floor — in 6 of 6 query-instances. Those requests
cost zero searches and zero embedding calls. Every figure in this section is derived from those nine
runs and committed in [samples/corpus_map_ab.md](../samples/corpus_map_ab.md).

Read that precisely. The *judgement* is a model's, on two golden queries, so it is evidence of a
reliable fast path and not proof of a classifier that never misses; a question the model reads as
family-adjacent is still searched, and then refused on the relevance floor exactly as before. What
the map makes deterministic is everything downstream of the verdict — no search, no Response call,
one fixed template — which is why TASK.md requirement 3's safe fallback is better served than it
was, without being *guaranteed*.

The map was also built to let the Planner filter a step to one family. **That half is measured and
switched off.** In the same A/B the model filtered 91 % of steps, and the twelve queries it
filtered lost recall (−0.093 exact-ID, −0.071 base-family), failing the acceptance gate: restricting a search to one
family fills the top-k with that family's *enhancements* rather than its base controls, and a
question spanning several families cannot reach the rest. The retrieval layer keeps the OData filter
it was built and probed for; nothing on the serving path sets one. Enabling it again is one argument
in `plan_query`, which is where a future ID-constrained retrieval design would start.

### The Microsoft Agent Framework

TASK.md requirement 2 names the Microsoft Agent Framework outright, and "agent framework
usage" is 25 % of the assessment — so MAF is a compliance requirement, not a free choice.
The system was built on it through Phase 3, took a Phase 5.5 detour to PydanticAI, and was
migrated back in Phase 9. Orchestration is a MAF `WorkflowBuilder` wiring
Planner → Retrieval → Response `Executor`s that exchange the typed messages above; that
`Workflow` is what "demonstrate clear orchestration" (TASK.md's wording) is demonstrated
with. The three concerns that drove the Phase 5.5 detour, and how the return handles them:

- **The dependency conflict is gone.** `agent-framework-core` + `agent-framework-openai`
  (not the `agent-framework` meta-package, which drags in ~40 unused integrations) resolve
  cleanly against `azure-search-documents` 12.0.0 and the rest of the runtime deps — the
  version conflict that existed at Phase 5.5 no longer does (re-verified at migration time).
- **Per-run state is handled by building fresh per query.** A MAF `Workflow` carries one
  run's state and rejects a concurrent second run, and each `Executor` serializes its
  handler behind a per-instance `asyncio.Lock`. `PolicyPipeline.answer_query` therefore
  builds a fresh workflow **and** fresh executors on each call (~0.4 ms), over the two
  agents and the Azure clients, which are per-call stateless and shared safely — so
  overlapping requests never contend on a shared lock. (Sharing the executors, as the
  pre-detour design did, was found under review to serialize concurrent queries stage by
  stage; the load test would have shed 504s. It was never load-tested on MAF before, so the
  defect was latent — see [TODO.md](../TODO.md) Phase 9.)
- **The Planner still enforces its output shape at the model.** MAF's structured outputs
  (`response_format` on the Planner's options) give the same model-side JSON-schema
  enforcement, so a plan's *shape* is guaranteed by the provider, not parsed out of prose.

What TASK.md specifies of the agent layer is unchanged across every framework the project
has used: a Planner, a Retrieval, and a Response agent, communicating through structured
data under clear orchestration. Every edge is a validated Pydantic message, so the contract
is enforced by the message types regardless of the library carrying them.

## Determinism & grounding

Every currently deployable Azure OpenAI chat model is a reasoning model that rejects
`temperature`, `top_p`, and `seed` (decision D7 in [TODO.md](../TODO.md)), so determinism
is **grounding-enforced rather than sampling-based**:

- **Structured outputs** — the Planner returns a JSON schema, not prose to parse.
- **Pinned versions** — the model version is pinned on the deployment (`gpt-5-mini`
  `2025-08-07`), the corpus at an exact upstream commit, the prompts in a version-controlled
  store ([prompts.json](../llm_policy_library/prompts.json)).
- **Minimized `reasoning_effort`** (`minimal`) — the least output variance the model offers.
- **Citation enforcement** — an answer may only cite retrieved controls; an ID that was not
  retrieved is excluded from `citations` and logged as a grounding violation, so an invented
  control can never be reported as a source.
- **Safe fallback** — an empty grounding set short-circuits to a fixed template. Two distinct
  paths reach it, and the audit trail's `reason` says which. The *retrieval* fallback is fully
  deterministic: a threshold on a calibrated relevance score, no model involved. The *structural*
  fallback the corpus map added is **the Planner model judging** the question against the family
  list — deterministic in mechanism (it always skips retrieval, and the answer is the same fixed
  template) but a model judgement nonetheless, so it is not proven exhaustive. The guarantee is
  that neither path can invent a control: both answer from a template, never from the model.

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
  **110 MB**, total index well under 1 GB with BM25 postings — comfortably inside Basic
  (15 GB) and nowhere near S1. Partitions are not the lever at this size; one is enough.
- **Replicas** are the lever, for QPS and availability: 2 for Search's read SLA, scaling
  with measured QPS (below).
- **Embedding cost.** `text-embedding-3-small` at ~$0.02/1M tokens ≈ **$0.14 per full
  re-index** — rebuilding the index is effectively free; the OSCAL fetch and upload dominate.

### The 50-user SLA (measured at 10, extrapolated to 50)

Full analysis with artifacts: [samples/loadtest_results.md](../samples/loadtest_results.md).
The SLA target is 50 concurrent users, p90 ≤ 15 s, p99 ≤ 30 s (decision D3: scaled test +
extrapolation).

- **Measured at 10 users** (6 min, zero HTTP errors, 2026-07-16): on-topic p90 **9.4 s**, p99
  **12.0 s** — met with headroom. The percentiles predate the corpus map and were not re-measured
  for it (decision D14): an on-topic request still makes the same two chat calls, so the map is
  expected to move cost rather than latency — an expectation, not a measurement.
- **The binding constraint at 50 users is chat TPM.** A calibrated closed-loop model puts 50 users
  at ≈5.3 RPS ⇒ **545 chat RPM against a 150 RPM quota**, and — with the corpus map on — ≈805K TPM
  against a 150K quota. The deployment grants 1 RPM per 1,000 TPM, so 545 RPM is covered by 545K
  TPM while the tokens themselves need more: provision **≈810K TPM**. (Before the map the same
  workload needed ≈600K and *RPM* was what bound; the map's ~1,053-token planner prefix is what
  flipped it. Prompt caching discounts the bill, not the quota, and the ≈805K is measured
  throughput rather than a quota allocation — Azure admits against its own up-front estimate.)
  Search reaches ≈6.1 QPS ⇒ add replicas (start at 2) and re-measure.
- **Whether p90/p99 still hold at 50 users is deliberately left open**: the extrapolation
  assumes constant response time, so it cannot prove it, and the current quota caps any
  honest run at **~9 users** on the shipped configuration — 150K TPM ÷ 2,533 tokens/request,
  and itself an upper bound, since Azure admits against an up-front estimate. (Pre-map the
  binding ceiling was ~14 users by RPM.) Re-measure once quota exists.
- **Levers**: skip the Planner on simple queries via a cheap pre-classifier (50% of plans
  are single-step; the Planner is 24% of latency and half of an on-topic request's RPM — a
  gate skipping it on that traffic cuts RPM demand ~30%, 1.73→~1.2 calls/request); PTU if
  p99 must be contractual; streaming helps perceived latency only.

## Governance controls

- **Audit trail.** Every request logs one JSON line per hop — query, plan, each step's
  `kept` *and* `dropped` documents with scores, the answer text, citations, token counts,
  latency — all sharing a correlation ID that is also echoed to the client as
  `X-Correlation-ID`. A fallback is auditable: the trail shows what was rejected and by how
  much (and thresholds are retuned against it).
- **Citation-enforced grounding** and the **safe fallback**, as above. The corpus map made the
  fallback structural for questions the catalog does not cover: the Planner refuses them without
  searching, in 3/3 evaluation runs. The honest residual is that this is the model judging a
  question against a list of twenty families, so it is *deterministic in mechanism, not proven
  exhaustive* — a question that resembles a listed family closely enough will still be searched,
  and then refused only if retrieval returns nothing above the relevance floor, which is the
  pre-map probabilistic path (measured at a low single-digit percent miss rate under load,
  2026-07-16). Both routes are distinguishable in the audit trail by the `reason` on the
  "safe fallback returned" line.
- **Evaluation gate.** A 15-query hand-labeled golden set scores retrieval with
  deterministic recall/NDCG@5 and answers with two LLM judges (faithfulness, relevancy),
  plus a hard citation-validity check and fallback checks. Committed run: faithfulness
  5.0/5, relevancy 5.0/5, zero invented citations, 2/2 fallbacks. Prompts live in the
  version-controlled store, so a prompt change is a reviewable diff that can be re-scored
  against the golden set before it ships — which is how the corpus map was decided: three
  runs per setting, an acceptance gate agreed in advance, and a feature shipped at half its
  designed scope because the other half measurably cost recall.

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
