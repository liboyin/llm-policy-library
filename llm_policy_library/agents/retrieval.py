"""Retrieval Agent: run a plan's searches against Azure AI Search.

No chat model is involved. The agent embeds each `PlanStep`'s search query,
searches the index, drops everything below the relevance floor, and hands the
survivors on. The floor is what makes the safe fallback possible: an empty
document set, not a chat model's judgement, is what says "nothing relevant".

Two search modes, two score scales
----------------------------------
Which score a result carries depends on how it was retrieved, and the two scales
are not comparable. Both were measured against the live 1,014-control index on
2026-07-10, over six on-topic questions and four deliberately off-topic ones:

===============================  ==============  ==============  =============
Score                            on-topic        off-topic       separable?
===============================  ==============  ==============  =============
`@search.rerankerScore`          2.00 - 3.26     0.54 - 1.44     yes, cleanly
vector-only `@search.score`      0.635 - 0.776   0.517 - 0.576   yes, narrowly
hybrid `@search.score` (RRF)     0.028 - 0.032   0.024 - 0.032   **no**
===============================  ==============  ==============  =============

The last row is why this module does not simply run hybrid search everywhere.
A hybrid query's `@search.score` is a Reciprocal Rank Fusion score: it is
computed from each document's *rank* in the vector and BM25 result lists, never
from how well it matches. Some document always ranks first, so "What is the
capital of France?" scored 0.0323 — matching the best on-topic question. No
threshold on an RRF score can distinguish a relevant control from an irrelevant
one, so no threshold on it can drive the safe fallback.

So the agent ranks on a calibrated score, and gates on the same score it ranked
on. When the semantic ranker is available it searches hybrid and reads
`@search.rerankerScore`, which is a genuine 0-4 relevance judgement. When it is
not (Azure AI Search Free tier), it drops `search_text` and searches vectors
only, which makes `@search.score` a cosine similarity rescaled to 0-1. The Free
tier therefore gives up BM25 keyword matching; measured against this corpus that
costs little, because each document's embedded text is prefixed with its control
ID, so `AC-2` still retrieves `ac-2` at rank one.

A step may also name a control family, which becomes an OData `filter` on the
same query. That is constrained search, not lookup: the mode, the floor, and the
`kept`/`dropped` audit logging are untouched, so a filtered step is gated on the
same score scale as an unfiltered one. Probed live on 2026-07-22 against the
1,014-control index (azure-search-documents 12.0.0, Basic tier), which is what
that claim rests on: filters bind before ranking, every hit still carries
`@search.rerankerScore`, and a filter matching no document returns no rows rather
than an error. The floor still does its work under a filter — restricting
"account management and least privilege" to Media Protection left only one of
five hits above 1.8, versus 2.90 for the unfiltered top hit — but that one
survivor is also why the Planner is told to leave the filter off when it is
unsure: a filter cannot rescue a control it has excluded.
"""

import asyncio
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import AsyncAzureOpenAI

from llm_policy_library.config import Settings
from llm_policy_library.models import PlanStep, QueryPlan, RetrievalResult, RetrievedDocument
from llm_policy_library.search_index import SEMANTIC_CONFIGURATION_NAME

logger = logging.getLogger(__name__)

# Everything the Response Agent and the audit trail need. `content` duplicates
# `description`, and `embedding` is 1,536 floats nobody reads back.
SELECT_FIELDS: Final[tuple[str, ...]] = ("id", "title", "description", "category")

VECTOR_FIELD: Final = "embedding"

# Approximate-nearest-neighbour candidates fetched before `top` slices the
# result. Also the pool the semantic ranker reranks, which it caps at 50.
VECTOR_CANDIDATE_COUNT: Final = 50

RERANKER_SCORE_FIELD: Final = "@search.rerankerScore"
SEARCH_SCORE_FIELD: Final = "@search.score"


@dataclass(frozen=True)
class ScoringMode:
    """How one search run retrieves, scores, and gates its results.

    Bundling the three together is the point: the score a mode ranks by, the
    field that carries it, and the floor it is compared against always change
    together, and splitting them is how a threshold ends up applied to the wrong
    scale.

    Attributes:
        semantic: Whether to search hybrid and apply the semantic ranker. False
            means a vector-only search.
        score_field: The result field holding this mode's relevance score.
        threshold: Documents scoring below this are dropped.
    """

    semantic: bool
    score_field: str
    threshold: float


def scoring_mode(settings: Settings) -> ScoringMode:
    """Choose the search mode the configured search tier supports.

    Args:
        settings: Validated runtime configuration.

    Returns:
        The mode implied by `azure_search_semantic_ranker`.
    """
    if settings.azure_search_semantic_ranker:
        return ScoringMode(
            semantic=True,
            score_field=RERANKER_SCORE_FIELD,
            threshold=settings.min_reranker_score,
        )
    return ScoringMode(
        semantic=False,
        score_field=SEARCH_SCORE_FIELD,
        threshold=settings.min_vector_score,
    )


def build_search_kwargs(
    search_query: str,
    vector: list[float],
    mode: ScoringMode,
    top_k: int,
    category: str | None = None,
) -> dict[str, Any]:
    """Build the keyword arguments for one `SearchClient.search` call.

    Pure, so the semantic and vector-only query shapes are asserted in unit tests
    without a search service.

    Args:
        search_query: The plan step's search text.
        vector: The embedding of `search_query`.
        mode: The active scoring mode.
        top_k: Documents to return.
        category: A control family to restrict the search to, or None to search
            the whole index. Must be a name `agents.planner.validated_categories`
            admitted — the value is interpolated into an OData literal, and it is
            a fixed twenty-name vocabulary rather than user text, so it is
            validated at the boundary instead of escaped here.

    Returns:
        Keyword arguments for `SearchClient.search`.
    """
    kwargs: dict[str, Any] = {
        # Omitting the search text is what turns hybrid into a vector-only
        # search, and `@search.score` from an RRF rank into a cosine similarity.
        "search_text": search_query if mode.semantic else None,
        "vector_queries": [
            VectorizedQuery(
                vector=vector,
                k_nearest_neighbors=VECTOR_CANDIDATE_COUNT,
                fields=VECTOR_FIELD,
            )
        ],
        "top": top_k,
        "select": list(SELECT_FIELDS),
    }
    if category is not None:
        # Doubling the apostrophe is OData's own escaping. The Planner already
        # restricts the value to twenty catalog family names, none of which
        # contains one (measured 2026-07-22), so this changes nothing today — but
        # that vocabulary is model-generated and regenerated whenever the catalog
        # commit moves, and `corpus_map.FamilyEntry.name` constrains its
        # characters no further. The failure it forecloses is not a loud one: a
        # stray apostrophe makes the service reject the syntax, and a crafted one
        # returns HTTP 200 with the filter silently widened. Escaping here keeps
        # that from depending on a promise made in another module.
        kwargs["filter"] = "category eq '{}'".format(category.replace("'", "''"))
    if mode.semantic:
        kwargs["query_type"] = "semantic"
        kwargs["semantic_configuration_name"] = SEMANTIC_CONFIGURATION_NAME
    return kwargs


def relevant_documents(
    rows: Iterable[Mapping[str, Any]], mode: ScoringMode
) -> list[RetrievedDocument]:
    """Keep the search results that clear the mode's relevance floor.

    A row whose score field is absent is dropped rather than defaulted. The field
    is always present for the mode that asked for it, so its absence means the
    search ran in a different mode than the caller believes — admitting the row
    with an invented score would hide that.

    Args:
        rows: Raw search results.
        mode: The mode the search ran in.

    Returns:
        The surviving documents, in the order the service ranked them.
    """
    documents = []
    for row in rows:
        score = row.get(mode.score_field)
        if score is None or score < mode.threshold:
            continue
        documents.append(
            RetrievedDocument(
                id=row["id"],
                title=row["title"],
                description=row["description"],
                category=row["category"],
                score=float(score),
            )
        )
    return documents


def dedupe_documents(results: Iterable[RetrievalResult]) -> list[RetrievedDocument]:
    """Merge every step's documents into one grounding set.

    Steps overlap by design — asking about "access control" and "authentication"
    both surface `ac-2` — and the Response Agent must see each control once. A
    control kept its best score across the steps that found it.

    Ties break on the control ID so that the same retrieval always produces the
    same ordering, which the answer's citation order in turn depends on.

    Args:
        results: Per-step retrieval results.

    Returns:
        The deduplicated documents, best score first.
    """
    best: dict[str, RetrievedDocument] = {}
    for result in results:
        for document in result.documents:
            incumbent = best.get(document.id)
            if incumbent is None or document.score > incumbent.score:
                best[document.id] = document
    return sorted(best.values(), key=lambda document: (-document.score, document.id))


async def embed_query(
    client: AsyncAzureOpenAI, deployment: str, text: str
) -> tuple[list[float], int]:
    """Embed one search query with the same model the index was built with.

    Args:
        client: Azure OpenAI client.
        deployment: Name of the embedding deployment.
        text: The text to embed.

    Returns:
        The embedding vector, and the tokens the embeddings deployment billed for
        it. The count is returned rather than dropped because embeddings carry
        their own TPM quota, separate from the chat deployment's, and a plan of
        three steps spends it three times over in one request.
    """
    response = await client.embeddings.create(model=deployment, input=[text])
    return response.data[0].embedding, response.usage.prompt_tokens


async def retrieve_step(
    search_client: SearchClient,
    openai_client: AsyncAzureOpenAI,
    settings: Settings,
    step: PlanStep,
) -> RetrievalResult:
    """Execute one plan step against the index.

    Args:
        search_client: Client bound to the policy index.
        openai_client: Client for the embedding deployment.
        settings: Validated runtime configuration.
        step: The step to execute.

    Returns:
        The step's documents that cleared the relevance floor.
    """
    mode = scoring_mode(settings)
    vector, embedding_tokens = await embed_query(
        openai_client, settings.azure_openai_embedding_deployment, step.search_query
    )
    pager = await search_client.search(
        **build_search_kwargs(
            step.search_query, vector, mode, settings.retrieval_top_k, step.category
        )
    )
    rows = [row async for row in pager]
    documents = relevant_documents(rows, mode)

    # Both sides of the floor are logged. When a query falls back, "nothing was
    # relevant" is only auditable if the trail says what was rejected and by how
    # far; it is also the evidence a threshold is retuned against.
    kept_ids = {document.id for document in documents}
    logger.info(
        "step retrieved",
        extra={
            "search_query": step.search_query,
            "semantic": mode.semantic,
            "threshold": mode.threshold,
            # Null when the step searched every family. Without it the `dropped`
            # list below reads as the whole corpus's verdict on the query, when a
            # filtered step only ever saw one family of it.
            "category": step.category,
            # One line per step, so summing this key across a correlation ID gives
            # the request's embeddings cost. It is billed against a different
            # quota than the chat tokens the Planner and Response Agent log.
            "embedding_tokens": embedding_tokens,
            "kept": [{"id": document.id, "score": document.score} for document in documents],
            "dropped": [
                {"id": row["id"], "score": row.get(mode.score_field)}
                for row in rows
                if row["id"] not in kept_ids
            ],
        },
    )
    return RetrievalResult(step=step, documents=documents)


async def retrieve_plan(
    search_client: SearchClient,
    openai_client: AsyncAzureOpenAI,
    settings: Settings,
    plan: QueryPlan,
) -> list[RetrievalResult]:
    """Execute every step of a plan concurrently.

    The steps are independent, and a plan of three costs three embeddings and
    three searches. Running them together keeps retrieval latency at roughly one
    step's, which is what the end-to-end latency budget is spent on.

    A plan the Planner marked out of domain is not executed at all. The Planner
    empties such a plan's steps, so the fan-out below would usually return `[]`
    for it anyway; checking the flag rather than relying on that guarantees the
    *embedding* call never happens either — the whole cost saving of refusing
    structurally rather than by retrieving nothing — and holds even for a plan
    that reaches here carrying both the flag and steps.

    Args:
        search_client: Client bound to the policy index.
        openai_client: Client for the embedding deployment.
        settings: Validated runtime configuration.
        plan: The plan to execute.

    Returns:
        One result per step, in plan order. Empty for an out-of-domain plan,
        which the Response Agent turns into the safe fallback.
    """
    if plan.out_of_domain:
        return []
    return await asyncio.gather(
        *(retrieve_step(search_client, openai_client, settings, step) for step in plan.steps)
    )
