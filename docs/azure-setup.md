# Azure Setup Guide

This guide walks you through provisioning every Azure resource the system needs,
**all in the Australia East (Sydney) region**. Follow it once, fill in `.env`
(copy from [`.env.example`](../.env.example)), and the rest of the pipeline works
against your subscription.

You provision manually via the [Azure Portal](https://portal.azure.com). The code
reads all connection details from environment variables, so no resource IDs are
hard-coded.

> **Why Australia East?** It is the only Australian region that offers both Azure
> OpenAI model deployments and semantic-ranker-capable Azure AI Search. Azure OpenAI
> **Data Zone** deployments do not exist for APAC, so your data-residency lever is
> the deployment *type* (Regional vs Global Standard), covered below.

> **Cost note:** With the recommended tiers, the only non-trivial charge is Azure AI
> Search Basic (~US$75/month, billed hourly — a few dollars if you delete it right
> after the assessment). Azure OpenAI is pay-per-token. Follow the
> [teardown checklist](#5-teardown-checklist) when you are done to stop all charges.

---

## 1. Resource group

1. Portal → **Resource groups** → **Create**.
2. **Subscription:** your personal subscription.
3. **Resource group:** `rg-llm-policy-library` (suggested).
4. **Region:** `Australia East`.
5. **Review + create** → **Create**.

Putting everything in one resource group makes teardown a single delete.

---

## 2. Azure OpenAI resource + deployments

### 2a. Create the resource

1. Portal → **Create a resource** → search **Azure OpenAI** → **Create**.
2. **Resource group:** `rg-llm-policy-library`.
3. **Region:** `Australia East`.
4. **Name:** e.g. `oai-llm-policy-library` (must be globally unique).
5. **Pricing tier:** Standard S0.
6. **Review + create** → **Create**. Provisioning takes a few minutes.

### 2b. Choose a deployment type

The deployment type is selected **per deployment** in the Foundry deploy dialog
(§2c) — you could even mix types across the chat and embedding deployments — but
decide your intent now, because it drives the data-residency and quota trade-off.
There is **no APAC Data Zone option**, so choose between:

| Deployment type | Data residency | Quota | Choose when |
|---|---|---|---|
| **Global Standard** (recommended) | Data at rest stays in the Australia geography; prompts/responses may be **processed in any Azure region** | Higher default TPM — helps the load test | Default for this assessment |
| **Regional Standard** | All inference stays **in Australia East** | Lower default quota | Simulating strict AU data-residency |

> **Global Standard is recommended** for this assessment. The current GA chat
> models (the gpt-5 family, see §2c) are offered primarily via Global Standard;
> regional Standard availability is narrower, so confirm in the deploy dialog if you
> require strict AU-only inference.

### 2c. Deploy the two models

Open the resource → **Azure AI Foundry portal** → **Deployments** → **Deploy model**.
Create **two** deployments. The **deployment name** you type here is what goes into
`.env` (it can differ from the model name; keeping them identical is simplest).

**Chat model** (deployment name → `AZURE_OPENAI_CHAT_DEPLOYMENT`):

| Model | Notes |
|---|---|
| **`gpt-5-mini`** (recommended) | Current GA mini-class model. Cost-effective, capable enough for planning + grounded answering. |
| `gpt-5.1` | Higher quality; supports `reasoning_effort=none`, so it can run in a fast near-instant mode — the best choice if the 50-user latency SLA is tight. Higher cost. |
| `gpt-5` | Full-size, highest quality, highest cost/latency. |

> **The GPT-4.x and `*-chat` models are gone.** As of 2026, `gpt-4.1-mini`,
> `gpt-4.1`, `gpt-4o`, and `gpt-4o-mini` are **deprecated** (existing deployments run,
> but you can **no longer create new ones**), and the `gpt-5-chat` / `gpt-5.1-chat`
> variants are **retired**. The only newly-deployable OpenAI chat models are the
> **gpt-5 reasoning family**.
>
> **Determinism note:** these reasoning models do **not** accept `temperature`,
> `top_p`, or `seed`. This project therefore achieves determinism and no-hallucination
> through **structured outputs**, a **pinned model version**, minimized
> `reasoning_effort`, and **citation-enforced grounding + safe fallback** — not
> temperature knobs. See [architecture.md](architecture.md) for the full
> rationale. Set `reasoning_effort` low (`minimal` on gpt-5-mini, `none` on gpt-5.1)
> to reduce both latency and output variance.

When deploying the chat model, **pin an explicit model version** (do not use a
floating alias like `gpt-chat-latest`, whose behavior drifts over time) and request
**≥ 100K TPM** if your quota allows — the 50-user load test (Phase 6) needs the
headroom.

**Embedding model** (deployment name → `AZURE_OPENAI_EMBEDDING_DEPLOYMENT`):

| Model | Notes |
|---|---|
| **`text-embedding-3-small`** (recommended) | Available under both deployment types. |
| `text-embedding-3-large` | Higher quality, ~6× the embedding cost, also available. |

> If you change either model, update the corresponding deployment-name variable in
> `.env`. If you switch the embedding model, the embedding **dimension** changes, so
> the search index must be recreated (Phase 2 handles this — just re-run ingestion).

### 2d. Grab the endpoint and key

Resource → **Keys and Endpoint**:

- **Endpoint** → `AZURE_OPENAI_ENDPOINT` (e.g. `https://oai-llm-policy-library.openai.azure.com/`).
- **KEY 1** → `AZURE_OPENAI_API_KEY`.

> **Availability caveat:** model/region availability tables change monthly. These
> choices were verified for Australia East on 2026-07-08; re-check in the Foundry
> deployment dialog at provisioning time and pick an available alternative from the
> tables above if needed.

---

## 3. Azure AI Search service

1. Portal → **Create a resource** → search **Azure AI Search** → **Create**.
2. **Resource group:** `rg-llm-policy-library`.
3. **Service name:** e.g. `srch-llm-policy-library` (globally unique) →
   endpoint becomes `https://srch-llm-policy-library.search.windows.net`.
4. **Region:** `Australia East`.
5. **Pricing tier** — choose per the trade-off below (click **Change Pricing Tier**):

| Tier | Cost | Limits | Semantic ranker | `.env` setting |
|---|---|---|---|---|
| **Free** | $0 | 50 MB, 3 indexes | **Not available** | `AZURE_SEARCH_SEMANTIC_RANKER=false` |
| **Basic** (recommended) | ~US$75/mo, billed hourly | ≥2 GB, 15 indexes | **Available** in Australia East | `AZURE_SEARCH_SEMANTIC_RANKER=true` |

> Basic per-service storage was raised in a 2024 capacity update; the dialog shows the
> current figure at provisioning time. Either way it far exceeds the 1,014-record demo.

> The code path degrades gracefully, but the flag changes the **search mode**, not just
> whether reranking is layered on. On **Basic**, set it to `true`: hybrid (vector + BM25)
> search with semantic reranking, gated on `MIN_RERANKER_SCORE`. On **Free**, set it to
> `false`: a vector-only search (the flag drops the keyword half), gated on
> `MIN_VECTOR_SCORE` — because a hybrid search's fused score cannot gate the safe fallback.
> See the retrieval design in [architecture.md](architecture.md), and the measured score
> bands in `llm_policy_library/agents/retrieval.py`'s docstring. The 1,014-record demo
> catalog fits comfortably in the Free tier's 50 MB.

6. **Review + create** → **Create**.

### Grab the endpoint and key

- Service **Overview** → **Url** → `AZURE_SEARCH_ENDPOINT`.
- Service → **Settings → Keys** → a **Primary admin key** → `AZURE_SEARCH_API_KEY`
  (an admin key is required because ingestion creates the index and uploads
  documents).

The index named by `AZURE_SEARCH_INDEX_NAME` is created by the Phase 2 ingestion script —
you do not create it in the portal. The variable is required (it has no default in code);
`.env.example` pre-fills it with `nist-800-53-controls`.

---

## 4. Fill in `.env`

```bash
cp .env.example .env
```

Then edit `.env` with the values collected above:

| Variable | Source |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | §2d Keys and Endpoint |
| `AZURE_OPENAI_API_KEY` | §2d Keys and Endpoint |
| `AZURE_OPENAI_CHAT_DEPLOYMENT` | §2c chat deployment name |
| `AZURE_OPENAI_EMBEDDING_DEPLOYMENT` | §2c embedding deployment name |
| `AZURE_SEARCH_ENDPOINT` | §3 service Overview URL |
| `AZURE_SEARCH_API_KEY` | §3 primary admin key |
| `AZURE_SEARCH_INDEX_NAME` | required; keep the `.env.example` value `nist-800-53-controls` |
| `AZURE_SEARCH_SEMANTIC_RANKER` | `true` on Basic, `false` on Free |
| `RETRIEVAL_TOP_K` | keep default `5` |
| `MIN_RERANKER_SCORE` | keep default `1.8`; applies only when the ranker is `true` |
| `MIN_VECTOR_SCORE` | keep default `0.60`; applies only when the ranker is `false` |
| `LLM_REASONING_EFFORT` | keep default `minimal` (use `none` if you deployed `gpt-5.1`) |
| `LOG_LEVEL` | keep default `INFO` |

> `.env` is gitignored — **never commit real keys**. If a key is ever exposed,
> rotate it: Azure OpenAI **Keys and Endpoint → Regenerate**, Search **Keys →
> Regenerate**.

---

## 5. Teardown checklist

To stop all charges after the assessment:

1. Portal → **Resource groups** → `rg-llm-policy-library` → **Delete resource group**.
2. Type the group name to confirm → **Delete**. This removes the OpenAI resource,
   its deployments, and the Search service in one action.
3. (Optional) Azure OpenAI soft-deletes resources; to purge immediately, go to
   **Azure OpenAI → Manage deleted resources** and purge.

---

## Notes on auth

For this assessment the code authenticates with **API keys** via the environment
variables above. A production hardening path — Entra ID / managed identity instead of
keys — is discussed in the architecture document's security section.
