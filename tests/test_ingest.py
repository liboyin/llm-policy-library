"""Unit tests for `llm_policy_library.ingest`."""

from types import SimpleNamespace
from typing import Any, NamedTuple
from unittest.mock import MagicMock, patch

import pytest

import llm_policy_library.ingest as testee
from llm_policy_library.config import Settings
from llm_policy_library.dataset import DatasetError, PolicyRecord

RECORD = PolicyRecord(
    id="ac-2.1",
    title="Automated System Account Management",
    description="Support the management of system accounts using [Assignment: mechanisms].",
    category="Access Control",
)


def make_settings(*, semantic_ranker: bool = True) -> Settings:
    """Build a fully-specified settings object without touching the environment.

    Args:
        semantic_ranker: Value for the semantic-ranker toggle.

    Returns:
        Validated settings.
    """
    return Settings(
        azure_openai_endpoint="https://oai.example.com/",
        azure_openai_api_key="oai-key",  # type: ignore[arg-type]
        azure_openai_chat_deployment="gpt-5-mini",
        azure_openai_embedding_deployment="text-embedding-3-small",
        azure_search_endpoint="https://search.example.net",
        azure_search_api_key="search-key",  # type: ignore[arg-type]
        azure_search_index_name="nist-800-53-controls",
        azure_search_semantic_ranker=semantic_ranker,
    )


def embedding_response(count: int, width: int) -> SimpleNamespace:
    """Mimic an Azure OpenAI embeddings response, deliberately out of index order.

    Args:
        count: Number of embeddings to return.
        width: Dimensions per embedding.

    Returns:
        An object shaped like the SDK's response.
    """
    data = [
        SimpleNamespace(index=position, embedding=[float(position)] * width)
        for position in reversed(range(count))
    ]
    return SimpleNamespace(data=data)


def make_openai_client(width: int = testee.EMBEDDING_DIMENSIONS) -> MagicMock:
    """Build a mock Azure OpenAI client whose embeddings echo the batch size.

    Args:
        width: Dimensions of each returned vector.

    Returns:
        The mock client.
    """
    client = MagicMock()
    client.embeddings.create.side_effect = lambda model, input: embedding_response(
        len(input), width
    )
    return client


class Clients(NamedTuple):
    """The three Azure clients `run_ingestion` takes, in positional order."""

    openai_client: MagicMock
    index_client: MagicMock
    search_client: MagicMock


def make_clients(width: int = testee.EMBEDDING_DIMENSIONS) -> Clients:
    """Build mock Azure clients whose happy path succeeds.

    Args:
        width: Dimensions of each vector the embedding client returns.

    Returns:
        The three clients, ready to splat into `run_ingestion`.
    """
    index_client = MagicMock()
    index_client.list_index_names.return_value = []
    search_client = MagicMock()
    search_client.upload_documents.side_effect = lambda documents: [
        SimpleNamespace(succeeded=True, key=document["key"], error_message=None)
        for document in documents
    ]
    return Clients(make_openai_client(width), index_client, search_client)


def test_embedding_text_carries_the_control_id_for_keyword_matching() -> None:
    """A user asking about "AC-2" must match lexically; BM25 only sees `content`."""
    text = testee.embedding_text(RECORD)

    assert text.startswith("AC-2.1: Automated System Account Management (Access Control)")
    assert RECORD.description in text


def test_to_search_document_stores_the_exact_text_that_was_embedded() -> None:
    """If `content` drifts from the embedded text, the audit trail is a lie."""
    document = testee.to_search_document(RECORD, [0.5] * 3)

    assert document["content"] == testee.embedding_text(RECORD)


def test_to_search_document_separates_the_encoded_key_from_the_citable_id() -> None:
    """Azure rejects a dotted key, but citations and golden labels need the exact ID."""
    document = testee.to_search_document(RECORD, [0.5] * 3)

    assert document["key"] == "ac-2_1"
    assert document["id"] == "ac-2.1"


def test_to_search_document_populates_every_field_the_index_declares() -> None:
    """A field missing here is a field that silently never gets indexed."""
    document = testee.to_search_document(RECORD, [0.5] * 3)

    assert set(document) == {"key", "id", "title", "description", "category", "content", "embedding"}


def test_embed_texts_preserves_input_order_across_batches() -> None:
    """Vectors are zipped back onto records by position; a reorder mislabels every doc."""
    client = make_openai_client(width=2)

    vectors = testee.embed_texts(client, "embed", ["a", "b", "c", "d", "e"], batch_size=2)

    # `embedding_response` returns each batch reversed, so order is only correct
    # if `embed_texts` sorts on the response index.
    assert vectors == [[0.0, 0.0], [1.0, 1.0], [0.0, 0.0], [1.0, 1.0], [0.0, 0.0]]


def test_embed_texts_batches_requests_rather_than_sending_one_per_record() -> None:
    """1,014 single-text requests would be slow and burn the deployment's rate limit."""
    client = make_openai_client(width=2)

    testee.embed_texts(client, "embed", ["a", "b", "c", "d", "e"], batch_size=2)

    assert client.embeddings.create.call_count == 3
    assert [len(call.kwargs["input"]) for call in client.embeddings.create.call_args_list] == [2, 2, 1]


def test_embed_texts_targets_the_configured_deployment() -> None:
    """Azure routes on the deployment name, not the model name."""
    client = make_openai_client(width=2)

    testee.embed_texts(client, "my-embedding-deployment", ["a"], batch_size=8)

    assert client.embeddings.create.call_args.kwargs["model"] == "my-embedding-deployment"


def test_validate_embedding_dimensions_accepts_vectors_matching_the_schema() -> None:
    """The happy path must not raise."""
    testee.validate_embedding_dimensions([[0.0] * 4, [1.0] * 4], expected=4)


def test_validate_embedding_dimensions_rejects_the_wrong_embedding_model() -> None:
    """`-3-large` returns 3,072 dims; the index would reject every document."""
    with pytest.raises(testee.IngestError, match="AZURE_OPENAI_EMBEDDING_DEPLOYMENT"):
        testee.validate_embedding_dimensions([[0.0] * 3072], expected=1536)


def test_validate_embedding_dimensions_accepts_an_empty_result() -> None:
    """No vectors means no width to disagree with; the record-count check owns that case."""
    testee.validate_embedding_dimensions([], expected=1536)


def test_upload_documents_batches_and_counts_every_document() -> None:
    """A 1,536-float vector per doc means one giant request would exceed 16 MB."""
    client = MagicMock()
    client.upload_documents.side_effect = lambda documents: [
        SimpleNamespace(succeeded=True, key=document["key"], error_message=None)
        for document in documents
    ]
    documents: list[dict[str, Any]] = [{"key": f"ac-{n}"} for n in range(5)]

    uploaded = testee.upload_documents(client, documents, batch_size=2)

    assert uploaded == 5
    assert client.upload_documents.call_count == 3


def test_upload_documents_raises_when_the_service_rejects_a_document() -> None:
    """A half-populated index silently drops controls from every future answer."""
    client = MagicMock()
    client.upload_documents.return_value = [
        SimpleNamespace(succeeded=True, key="ac-1", error_message=None),
        SimpleNamespace(succeeded=False, key="ac-2", error_message="key is invalid"),
    ]

    with pytest.raises(testee.IngestError, match="ac-2"):
        testee.upload_documents(client, [{"key": "ac-1"}, {"key": "ac-2"}], batch_size=10)


def test_run_ingestion_indexes_every_parsed_record() -> None:
    """The pipeline must upload exactly the catalog it parsed, in order."""
    records = [RECORD, RECORD.model_copy(update={"id": "ac-3"})]
    clients = make_clients()

    with (
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=records),
        patch.object(testee, "validate_record_count") as validate_count,
    ):
        uploaded = testee.run_ingestion(make_settings(), *clients)

    assert uploaded == 2
    validate_count.assert_called_once_with(records)
    clients.index_client.create_index.assert_called_once()
    documents = [
        document
        for call in clients.search_client.upload_documents.call_args_list
        for document in call.kwargs["documents"]
    ]
    assert [document["id"] for document in documents] == ["ac-2.1", "ac-3"]


def test_run_ingestion_builds_the_index_with_the_configured_ranker_setting() -> None:
    """A Free-tier service rejects a semantic configuration, so the flag must reach the schema."""
    clients = make_clients()

    with (
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=[RECORD]),
        patch.object(testee, "validate_record_count"),
    ):
        testee.run_ingestion(make_settings(semantic_ranker=False), *clients)

    created_index = clients.index_client.create_index.call_args.args[0]
    assert created_index.semantic_search is None


def test_run_ingestion_leaves_the_live_index_intact_when_the_embedding_width_is_wrong() -> None:
    """Dropping the index before the vectors are validated would take retrieval down."""
    # A deployment pointed at `-3-large`: wrong width, caught before indexing.
    clients = make_clients(width=3072)

    with (
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=[RECORD]),
        patch.object(testee, "validate_record_count"),
    ):
        with pytest.raises(testee.IngestError):
            testee.run_ingestion(make_settings(), *clients)

    clients.index_client.delete_index.assert_not_called()
    clients.index_client.create_index.assert_not_called()


def test_run_ingestion_leaves_the_live_index_intact_when_the_embeddings_call_raises() -> None:
    """An outage or exhausted quota at Azure OpenAI must not cost us the served index."""
    clients = make_clients()
    clients.openai_client.embeddings.create.side_effect = RuntimeError("deployment unavailable")

    with (
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=[RECORD]),
        patch.object(testee, "validate_record_count"),
    ):
        with pytest.raises(RuntimeError, match="deployment unavailable"):
            testee.run_ingestion(make_settings(), *clients)

    clients.index_client.create_index.assert_not_called()


def test_run_ingestion_leaves_the_live_index_intact_when_embeddings_are_missing_a_record() -> None:
    """A short embeddings response must fail before the drop, not mislabel every document."""
    clients = make_clients()
    # One vector short: zipping it onto the records would silently shift every
    # embedding onto the wrong control if `strict=True` were dropped.
    clients.openai_client.embeddings.create.side_effect = lambda model, input: embedding_response(
        len(input) - 1, testee.EMBEDDING_DIMENSIONS
    )

    with (
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=[RECORD, RECORD]),
        patch.object(testee, "validate_record_count"),
    ):
        with pytest.raises(ValueError):
            testee.run_ingestion(make_settings(), *clients)

    clients.index_client.create_index.assert_not_called()


def test_run_ingestion_aborts_before_embedding_when_the_catalog_is_too_small() -> None:
    """The 500-record floor must gate the index, not merely warn after it is rebuilt."""
    clients = make_clients()

    with (
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=[RECORD]),
        patch.object(testee, "validate_record_count", side_effect=DatasetError("too few")),
    ):
        with pytest.raises(DatasetError):
            testee.run_ingestion(make_settings(), *clients)

    clients.openai_client.embeddings.create.assert_not_called()
    clients.index_client.create_index.assert_not_called()


def test_main_opens_the_azure_clients_and_closes_them_after_ingestion() -> None:
    """A leaked client would hold a connection pool open for the life of the process."""
    settings = make_settings()

    with (
        patch.object(testee, "load_settings", return_value=settings),
        patch.object(testee, "configure_logging") as configure,
        patch.object(testee, "run_ingestion", return_value=1014) as run,
        patch.object(testee, "AzureOpenAI") as openai_class,
        patch.object(testee, "SearchIndexClient") as index_class,
        patch.object(testee, "SearchClient") as search_class,
    ):
        assert testee.main() == 0

    configure.assert_called_once_with(settings.log_level)
    run.assert_called_once_with(
        settings,
        openai_class.return_value.__enter__.return_value,
        index_class.return_value.__enter__.return_value,
        search_class.return_value.__enter__.return_value,
    )
    for client_class in (openai_class, index_class, search_class):
        client_class.return_value.__exit__.assert_called_once()


def test_main_targets_the_configured_deployment_and_index() -> None:
    """Misrouted clients would silently ingest into the wrong resource."""
    settings = make_settings()

    with (
        patch.object(testee, "load_settings", return_value=settings),
        patch.object(testee, "configure_logging"),
        patch.object(testee, "run_ingestion", return_value=1014),
        patch.object(testee, "AzureOpenAI") as openai_class,
        patch.object(testee, "SearchIndexClient") as index_class,
        patch.object(testee, "SearchClient") as search_class,
    ):
        testee.main()

    assert openai_class.call_args.kwargs["azure_endpoint"] == settings.azure_openai_endpoint
    assert openai_class.call_args.kwargs["max_retries"] == testee.MAX_RETRIES
    assert index_class.call_args.args[0] == settings.azure_search_endpoint
    assert search_class.call_args.args[:2] == (
        settings.azure_search_endpoint,
        settings.azure_search_index_name,
    )
