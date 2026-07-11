"""Unit tests for `llm_policy_library.agents.retrieval`."""

import logging
import os
from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import llm_policy_library.agents.retrieval as testee
from llm_policy_library.config import Settings
from llm_policy_library.models import PlanStep, QueryPlan, RetrievalResult, RetrievedDocument

SETTINGS_ENV = {
    "azure_openai_endpoint": "https://oai.example.com/",
    "azure_openai_api_key": "oai-key",
    "azure_openai_chat_deployment": "gpt-5-mini",
    "azure_openai_embedding_deployment": "text-embedding-3-small",
    "azure_search_endpoint": "https://search.example.net",
    "azure_search_api_key": "search-key",
    "azure_search_index_name": "nist-800-53-controls",
}


def make_settings(**overrides: Any) -> Settings:
    """Build a Settings object from literal values, bypassing the environment.

    Args:
        **overrides: Fields to override.

    Returns:
        The settings.
    """
    return Settings(**{**SETTINGS_ENV, **overrides})


@pytest.fixture(autouse=True)
def isolated_env() -> Iterator[None]:
    """Ambient AZURE_*/MIN_*/RETRIEVAL_* variables must not leak into settings built here."""
    with patch.dict(os.environ, {}, clear=True):
        yield


def search_row(control_id: str, **scores: float) -> dict[str, Any]:
    """Build a raw Azure AI Search result row.

    Args:
        control_id: The control's OSCAL ID.
        **scores: Score fields, keyed without the `@search.` prefix.

    Returns:
        A row shaped like the service's.
    """
    row: dict[str, Any] = {
        "id": control_id,
        "title": f"Title of {control_id}",
        "description": f"Statement of {control_id}",
        "category": "Access Control",
    }
    row |= {f"@search.{name}": value for name, value in scores.items()}
    return row


def make_document(control_id: str, score: float) -> RetrievedDocument:
    """Build a retrieved document.

    Args:
        control_id: The control's OSCAL ID.
        score: Its relevance score.

    Returns:
        The document.
    """
    return RetrievedDocument(
        id=control_id,
        title=f"Title of {control_id}",
        description=f"Statement of {control_id}",
        category="Access Control",
        score=score,
    )


def make_result(step_query: str, *documents: RetrievedDocument) -> RetrievalResult:
    """Build one step's retrieval result.

    Args:
        step_query: The step's search query.
        *documents: The documents it retrieved.

    Returns:
        The result.
    """
    return RetrievalResult(
        step=PlanStep(search_query=step_query, purpose="p"), documents=list(documents)
    )


def async_pager(rows: list[dict[str, Any]]) -> Any:
    """Wrap rows in the async-iterable the search client returns.

    Args:
        rows: The rows to yield.

    Returns:
        An async iterable over `rows`.
    """

    class _Pager:
        def __aiter__(self) -> Any:
            async def _gen() -> Any:
                for row in rows:
                    yield row

            return _gen()

    return _Pager()


def test_scoring_mode_gates_on_the_reranker_score_when_the_ranker_is_on() -> None:
    """With the ranker on, only `@search.rerankerScore` measures relevance."""
    settings = make_settings(azure_search_semantic_ranker=True, min_reranker_score=1.8)

    mode = testee.scoring_mode(settings)

    assert mode.semantic is True
    assert mode.score_field == "@search.rerankerScore"
    assert mode.threshold == 1.8


def test_scoring_mode_gates_on_the_vector_score_when_the_ranker_is_off() -> None:
    """With the ranker off the search is vector-only, so `@search.score` is a cosine."""
    settings = make_settings(azure_search_semantic_ranker=False, min_vector_score=0.6)

    mode = testee.scoring_mode(settings)

    assert mode.semantic is False
    assert mode.score_field == "@search.score"
    assert mode.threshold == 0.6


def test_scoring_mode_never_gates_on_a_hybrid_rrf_score() -> None:
    """An RRF score ranks by position, not relevance, so no threshold on it can work."""
    for ranker in (True, False):
        mode = testee.scoring_mode(make_settings(azure_search_semantic_ranker=ranker))

        searches_hybrid = mode.semantic
        gates_on_search_score = mode.score_field == testee.SEARCH_SCORE_FIELD
        assert not (searches_hybrid and gates_on_search_score), (
            "gating a hybrid search on @search.score reads an RRF rank as a relevance score"
        )


def test_build_search_kwargs_sends_the_query_text_only_in_semantic_mode() -> None:
    """Dropping `search_text` is what turns hybrid into vector-only, rescaling the score."""
    mode = testee.ScoringMode(semantic=True, score_field="@search.rerankerScore", threshold=1.8)

    kwargs = testee.build_search_kwargs("access control", [0.1, 0.2], mode, top_k=5)

    assert kwargs["search_text"] == "access control"
    assert kwargs["query_type"] == "semantic"
    assert kwargs["semantic_configuration_name"] == testee.SEMANTIC_CONFIGURATION_NAME


def test_build_search_kwargs_omits_the_query_text_in_vector_mode() -> None:
    """A vector-only search is the only way `@search.score` becomes a cosine similarity."""
    mode = testee.ScoringMode(semantic=False, score_field="@search.score", threshold=0.6)

    kwargs = testee.build_search_kwargs("access control", [0.1, 0.2], mode, top_k=3)

    assert kwargs["search_text"] is None
    assert "query_type" not in kwargs
    assert "semantic_configuration_name" not in kwargs
    assert kwargs["top"] == 3


def test_build_search_kwargs_never_selects_the_embedding_field() -> None:
    """Returning 1,536 floats per hit would dominate the response for no reader."""
    mode = testee.scoring_mode(make_settings(azure_search_semantic_ranker=True))

    kwargs = testee.build_search_kwargs("q", [0.1], mode, top_k=5)

    assert testee.VECTOR_FIELD not in kwargs["select"]
    assert kwargs["vector_queries"][0].fields == testee.VECTOR_FIELD


def test_relevant_documents_drops_results_below_the_floor() -> None:
    """The floor is the only thing that can report "nothing relevant was found"."""
    mode = testee.ScoringMode(semantic=True, score_field="@search.rerankerScore", threshold=1.8)
    rows = [search_row("ac-2", rerankerScore=2.3), search_row("pe-2", rerankerScore=1.1)]

    documents = testee.relevant_documents(rows, mode)

    assert [document.id for document in documents] == ["ac-2"]
    assert documents[0].score == pytest.approx(2.3)


def test_relevant_documents_keeps_a_result_exactly_on_the_floor() -> None:
    """The floor is inclusive, so a document at threshold is relevant, not borderline-dropped."""
    mode = testee.ScoringMode(semantic=True, score_field="@search.rerankerScore", threshold=1.8)

    documents = testee.relevant_documents([search_row("ac-2", rerankerScore=1.8)], mode)

    assert [document.id for document in documents] == ["ac-2"]


def test_relevant_documents_reads_the_score_field_its_mode_names() -> None:
    """Reading the wrong field would compare a 0-4 reranker score against a 0-1 cosine."""
    mode = testee.ScoringMode(semantic=False, score_field="@search.score", threshold=0.6)
    rows = [search_row("ac-2", score=0.66, rerankerScore=0.1)]

    documents = testee.relevant_documents(rows, mode)

    assert [document.score for document in documents] == [pytest.approx(0.66)]


def test_relevant_documents_drops_a_row_missing_its_score_field() -> None:
    """A missing score means the search ran in another mode; inventing one would hide that."""
    mode = testee.ScoringMode(semantic=True, score_field="@search.rerankerScore", threshold=1.8)

    documents = testee.relevant_documents([search_row("ac-2", score=0.9)], mode)

    assert documents == []


def test_dedupe_documents_keeps_a_control_once_at_its_best_score() -> None:
    """Overlapping steps must not show the Response Agent the same control twice."""
    results = [
        make_result("access control", make_document("ac-2", 2.1)),
        make_result("authentication", make_document("ac-2", 2.4), make_document("ia-2", 2.2)),
    ]

    documents = testee.dedupe_documents(results)

    assert [(document.id, document.score) for document in documents] == [
        ("ac-2", 2.4),
        ("ia-2", 2.2),
    ]


def test_dedupe_documents_keeps_the_best_score_regardless_of_step_order() -> None:
    """A later, weaker hit must not overwrite the score that earned the control its rank."""
    results = [
        make_result("access control", make_document("ac-2", 2.4)),
        make_result("authentication", make_document("ac-2", 2.1)),
    ]

    documents = testee.dedupe_documents(results)

    assert [(document.id, document.score) for document in documents] == [("ac-2", 2.4)]


def test_dedupe_documents_breaks_score_ties_deterministically() -> None:
    """The answer's citation order follows this order, so equal scores must not reorder."""
    results = [make_result("q", make_document("sc-8", 2.0), make_document("ac-2", 2.0))]

    documents = testee.dedupe_documents(results)

    assert [document.id for document in documents] == ["ac-2", "sc-8"]


def test_dedupe_documents_returns_nothing_when_every_step_was_filtered_out() -> None:
    """An empty grounding set is the signal the Response Agent turns into the fallback."""
    assert testee.dedupe_documents([make_result("q"), make_result("r")]) == []


async def test_embed_query_uses_the_configured_embedding_deployment() -> None:
    """A query vector only matches the index if it comes from the model that built it."""
    client = MagicMock()
    client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=[0.5, 0.25])])
    )

    vector = await testee.embed_query(client, "text-embedding-3-small", "access control")

    assert vector == [0.5, 0.25]
    client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-small", input=["access control"]
    )


async def test_retrieve_step_embeds_the_step_query_not_the_users_question() -> None:
    """The Planner rewrote the question into search terms; embedding the original wastes that."""
    settings = make_settings(azure_search_semantic_ranker=True)
    openai_client = MagicMock()
    openai_client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=[0.1])])
    )
    search_client = MagicMock()
    search_client.search = AsyncMock(
        return_value=async_pager([search_row("ac-2", rerankerScore=2.2)])
    )
    step = PlanStep(search_query="account management controls", purpose="find AC family")

    result = await testee.retrieve_step(search_client, openai_client, settings, step)

    assert openai_client.embeddings.create.await_args.kwargs["input"] == [
        "account management controls"
    ]
    assert search_client.search.await_args.kwargs["search_text"] == "account management controls"
    assert result.step == step
    assert [document.id for document in result.documents] == ["ac-2"]


async def test_retrieve_step_applies_the_floor_of_the_configured_mode() -> None:
    """A Free-tier deployment must gate on its cosine floor, not the reranker's."""
    settings = make_settings(azure_search_semantic_ranker=False, min_vector_score=0.6)
    openai_client = MagicMock()
    openai_client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=[0.1])])
    )
    search_client = MagicMock()
    search_client.search = AsyncMock(
        return_value=async_pager([search_row("ac-2", score=0.66), search_row("pe-2", score=0.54)])
    )
    step = PlanStep(search_query="q", purpose="p")

    result = await testee.retrieve_step(search_client, openai_client, settings, step)

    assert [document.id for document in result.documents] == ["ac-2"]


async def test_retrieve_step_logs_the_rejected_documents_and_their_scores(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fallback is auditable only if the trail says what was rejected, and by how far."""
    settings = make_settings(azure_search_semantic_ranker=True, min_reranker_score=1.8)
    openai_client = MagicMock()
    openai_client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=[0.1])])
    )
    search_client = MagicMock()
    search_client.search = AsyncMock(
        return_value=async_pager(
            [search_row("ac-2", rerankerScore=2.2), search_row("pe-2", rerankerScore=1.1)]
        )
    )

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.retrieve_step(
            search_client, openai_client, settings, PlanStep(search_query="q", purpose="p")
        )

    record = next(record for record in caplog.records if record.message == "step retrieved")
    assert getattr(record, "dropped") == [{"id": "pe-2", "score": 1.1}]
    assert getattr(record, "threshold") == 1.8


async def test_retrieve_plan_runs_every_step_and_preserves_plan_order() -> None:
    """Steps run concurrently, but the audit trail must read in the order the plan states."""
    settings = make_settings(azure_search_semantic_ranker=True)
    openai_client = MagicMock()
    openai_client.embeddings.create = AsyncMock(
        return_value=MagicMock(data=[MagicMock(embedding=[0.1])])
    )
    search_client = MagicMock()
    search_client.search = AsyncMock(
        side_effect=lambda **_: async_pager([search_row("ac-2", rerankerScore=2.2)])
    )
    plan = QueryPlan(
        original_query="q",
        steps=[
            PlanStep(search_query="first", purpose="p1"),
            PlanStep(search_query="second", purpose="p2"),
        ],
    )

    results = await testee.retrieve_plan(search_client, openai_client, settings, plan)

    assert [result.step.search_query for result in results] == ["first", "second"]
    assert search_client.search.await_count == 2
