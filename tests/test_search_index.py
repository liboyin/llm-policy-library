"""Unit tests for `llm_policy_library.search_index`."""

from unittest.mock import MagicMock

from azure.search.documents.indexes.models import SearchField, SearchIndex

import llm_policy_library.search_index as testee

INDEX_NAME = "nist-800-53-controls"


def fields_of(index: SearchIndex) -> dict[str, SearchField]:
    """Map an index's field names to the field objects.

    Args:
        index: The index definition.

    Returns:
        Field name to field.
    """
    return {field.name: field for field in index.fields}


def test_document_key_escapes_the_dot_in_an_enhancement_id() -> None:
    """Azure rejects document keys containing a dot, and `ac-2.1` has one."""
    assert testee.document_key("ac-2.1") == "ac-2_1"


def test_document_key_leaves_a_base_control_id_untouched() -> None:
    """Base control IDs are already legal keys; rewriting them would obscure the ID."""
    assert testee.document_key("ac-2") == "ac-2"


def test_document_key_is_collision_free_across_control_ids() -> None:
    """Merely stripping the dot would map enhancement `ac-2.1` onto control `ac-21`."""
    ids = ["ac-2", "ac-2.1", "ac-2.10", "ac-21", "ac-21.0"]
    assert all("_" not in control_id for control_id in ids), "the encoding's premise"

    assert len({testee.document_key(control_id) for control_id in ids}) == len(ids)


def test_build_index_keys_on_the_encoded_id_not_the_control_id() -> None:
    """The key must be the dot-free encoding; `id` keeps the exact citable ID."""
    fields = fields_of(testee.build_index(INDEX_NAME, semantic_ranker=True))

    assert fields["key"].key is True
    assert fields["id"].key is not True
    assert fields["id"].filterable is True, "evaluation filters on the exact control ID"


def test_build_index_makes_content_searchable_for_the_keyword_half_of_hybrid_search() -> None:
    """Hybrid retrieval needs BM25 over the same text the vector was built from."""
    fields = fields_of(testee.build_index(INDEX_NAME, semantic_ranker=True))

    assert fields["content"].searchable is True


def test_build_index_declares_a_vector_field_bound_to_the_hnsw_profile() -> None:
    """Without a profile resolving to a real algorithm, the field is not vector-searchable."""
    index = testee.build_index(INDEX_NAME, semantic_ranker=True)
    embedding = fields_of(index)["embedding"]
    vector_search = index.vector_search
    assert vector_search is not None
    assert vector_search.profiles is not None
    assert vector_search.algorithms is not None

    assert embedding.vector_search_dimensions == testee.EMBEDDING_DIMENSIONS
    profiles = {profile.name: profile for profile in vector_search.profiles}
    assert embedding.vector_search_profile_name in profiles
    algorithm_names = {algorithm.name for algorithm in vector_search.algorithms}
    referenced = profiles[str(embedding.vector_search_profile_name)]
    assert referenced.algorithm_configuration_name in algorithm_names


def test_build_index_does_not_return_the_embedding_vector() -> None:
    """1,536 floats per hit would dwarf the answer; nothing downstream reads them."""
    embedding = fields_of(testee.build_index(INDEX_NAME, semantic_ranker=True))["embedding"]

    assert embedding.retrievable is False


def test_build_index_honours_a_non_default_embedding_width() -> None:
    """A `-3-large` deployment emits 3,072 dimensions and the schema must follow."""
    index = testee.build_index(INDEX_NAME, semantic_ranker=True, dimensions=3072)

    assert fields_of(index)["embedding"].vector_search_dimensions == 3072


def test_build_index_attaches_a_semantic_configuration_when_the_ranker_is_enabled() -> None:
    """Retrieval only requests semantic reranking against a named configuration."""
    index = testee.build_index(INDEX_NAME, semantic_ranker=True)

    assert index.semantic_search is not None
    assert index.semantic_search.configurations is not None
    names = {config.name for config in index.semantic_search.configurations}
    assert names == {testee.SEMANTIC_CONFIGURATION_NAME}


def test_build_index_omits_the_semantic_configuration_on_the_free_tier() -> None:
    """The Free tier's API rejects a semantic configuration, so creation would fail."""
    index = testee.build_index(INDEX_NAME, semantic_ranker=False)

    assert index.semantic_search is None


def test_recreate_index_drops_an_existing_index_before_creating_it() -> None:
    """Re-ingesting must leave only the current catalog, never orphaned documents."""
    client = MagicMock()
    client.list_index_names.return_value = [INDEX_NAME, "other-index"]
    index = testee.build_index(INDEX_NAME, semantic_ranker=True)

    replaced = testee.recreate_index(client, index)

    assert replaced is True
    client.delete_index.assert_called_once_with(INDEX_NAME)
    client.create_index.assert_called_once_with(index)


def test_recreate_index_creates_a_missing_index_without_deleting() -> None:
    """A first run has nothing to drop; deleting would raise."""
    client = MagicMock()
    client.list_index_names.return_value = ["other-index"]
    index = testee.build_index(INDEX_NAME, semantic_ranker=True)

    replaced = testee.recreate_index(client, index)

    assert replaced is False
    client.delete_index.assert_not_called()
    client.create_index.assert_called_once_with(index)
