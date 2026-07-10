"""Rebuild the Azure AI Search index from the NIST SP 800-53 catalog.

Run as `python -m llm_policy_library.ingest`. The run fetches the pinned OSCAL
catalog, parses it into policy records, embeds each one, recreates the index,
and uploads the documents in batches.

The order matters: everything that can fail on bad input — fetching, parsing, the
record-count floor, embedding, and the vector-width check — happens *before* the
index is dropped, so such a failure leaves the previously-ingested index serving
queries rather than deleting it and then failing. Recreating the index makes the
whole command idempotent — re-running it yields exactly the controls in the
pinned catalog, never a mixture with an earlier parse.

The one window that is not protected is the upload itself: a rejected batch
leaves a freshly-recreated, partially-populated index. Re-running the command
repairs it. Closing that window for real needs a build-then-alias-swap rebuild,
whose design belongs with the rest of the operability story in
`docs/architecture.md` rather than in this demo's ingestion path.

Transient failures are retried by the SDKs themselves: `AzureOpenAI` is
constructed with `max_retries`, and the Azure Search client applies its own
exponential-backoff retry policy.
"""

import logging
from itertools import batched
from typing import Any, Final

from azure.core.credentials import AzureKeyCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import AzureOpenAI

from llm_policy_library.config import Settings, load_settings
from llm_policy_library.dataset import (
    CATALOG_URL,
    PolicyRecord,
    fetch_catalog,
    parse_catalog,
    validate_record_count,
)
from llm_policy_library.logging_setup import configure_logging, correlation_context
from llm_policy_library.search_index import (
    EMBEDDING_DIMENSIONS,
    build_index,
    document_key,
    recreate_index,
)

logger = logging.getLogger(__name__)

# Pinned: the embeddings request/response shape must not change under us.
AZURE_OPENAI_API_VERSION: Final = "2024-10-21"

# Azure OpenAI accepts up to 2,048 inputs per embeddings call, but caps the
# request by total tokens too. Sixty-four control statements stay well inside
# both limits while keeping the whole catalog to a couple of dozen requests.
EMBEDDING_BATCH_SIZE: Final = 64

# A 1,536-float vector serialises to roughly 30 KB of JSON, so 100 documents per
# upload sits far below the service's 16 MB request ceiling.
UPLOAD_BATCH_SIZE: Final = 100

MAX_RETRIES: Final = 5


class IngestError(RuntimeError):
    """Raised when embedding or uploading the catalog fails."""


def embedding_text(record: PolicyRecord) -> str:
    """Render the text that is embedded and stored in the `content` field.

    The control ID and family are folded in so that a keyword query naming a
    control ("what does AC-2 require") can match lexically through BM25, and so
    that the vector carries the control's topic and not only its statement.

    Args:
        record: The policy record.

    Returns:
        The text to embed.
    """
    return f"{record.id.upper()}: {record.title} ({record.category})\n\n{record.description}"


def to_search_document(record: PolicyRecord, embedding: list[float]) -> dict[str, Any]:
    """Build the search document uploaded for one policy record.

    Args:
        record: The policy record.
        embedding: The vector of `embedding_text(record)`.

    Returns:
        A document matching the index schema.
    """
    return {
        "key": document_key(record.id),
        "id": record.id,
        "title": record.title,
        "description": record.description,
        "category": record.category,
        "content": embedding_text(record),
        "embedding": embedding,
    }


def embed_texts(
    client: AzureOpenAI,
    deployment: str,
    texts: list[str],
    batch_size: int = EMBEDDING_BATCH_SIZE,
) -> list[list[float]]:
    """Embed every text, in batches, preserving input order.

    Args:
        client: Azure OpenAI client.
        deployment: Name of the embedding deployment.
        texts: Texts to embed.
        batch_size: Texts per request.

    Returns:
        One vector per input text, in the same order.
    """
    vectors: list[list[float]] = []
    for batch in batched(texts, batch_size):
        response = client.embeddings.create(model=deployment, input=list(batch))
        # The API documents `data` as input-ordered, but it also carries an
        # explicit index; sorting on it costs nothing and removes the doubt.
        vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        logger.info(
            "embedded batch", extra={"embedded": len(vectors), "total": len(texts)}
        )
    return vectors


def validate_embedding_dimensions(
    vectors: list[list[float]], expected: int = EMBEDDING_DIMENSIONS
) -> None:
    """Check every vector matches the width the index was built for.

    Catches an embedding deployment pointed at the wrong model (for example
    `text-embedding-3-large`, at 3,072 dimensions) before the index is dropped.

    Args:
        vectors: The embedding vectors.
        expected: The width declared by the index schema.

    Raises:
        IngestError: If any vector has a different width.
    """
    widths = {len(vector) for vector in vectors}
    if widths - {expected}:
        raise IngestError(
            f"embedding deployment returned {sorted(widths)}-dimensional vectors but the "
            f"index expects {expected}; check AZURE_OPENAI_EMBEDDING_DEPLOYMENT"
        )


def upload_documents(
    client: SearchClient, documents: list[dict[str, Any]], batch_size: int = UPLOAD_BATCH_SIZE
) -> int:
    """Upload documents in batches, failing if the service rejects any of them.

    A partially-populated index would silently degrade retrieval — the missing
    control simply never comes back — so a rejected document aborts the run.

    Args:
        client: Search client bound to the target index.
        documents: Documents to upload.
        batch_size: Documents per request.

    Returns:
        The number of documents uploaded.

    Raises:
        IngestError: If the service rejects any document.
    """
    uploaded = 0
    for batch in batched(documents, batch_size):
        results = client.upload_documents(documents=list(batch))
        failures = [result for result in results if not result.succeeded]
        if failures:
            raise IngestError(
                f"{len(failures)} of {len(results)} documents were rejected, "
                f"first: {failures[0].key} ({failures[0].error_message})"
            )
        uploaded += len(results)
        logger.info("uploaded batch", extra={"uploaded": uploaded, "total": len(documents)})
    return uploaded


def run_ingestion(
    settings: Settings,
    openai_client: AzureOpenAI,
    index_client: SearchIndexClient,
    search_client: SearchClient,
) -> int:
    """Fetch, parse, embed, and index the control catalog using the given clients.

    Taking the three clients as arguments keeps every Azure side effect at the
    caller's boundary, so this — the part with the ordering invariants worth
    testing — is exercised with plain mocks rather than by patching the module.

    Args:
        settings: Validated runtime configuration.
        openai_client: Client for the embedding deployment.
        index_client: Index-management client for the search service.
        search_client: Client bound to the target index.

    Returns:
        The number of documents uploaded.

    Raises:
        DatasetError: If the catalog is malformed or yields too few records.
        IngestError: If the embeddings have the wrong width, or a document is
            rejected by the service.
    """
    records = parse_catalog(fetch_catalog())
    validate_record_count(records)
    logger.info(
        "catalog parsed",
        extra={
            "records": len(records),
            "categories": len({record.category for record in records}),
        },
    )

    vectors = embed_texts(
        openai_client,
        settings.azure_openai_embedding_deployment,
        [embedding_text(record) for record in records],
    )
    validate_embedding_dimensions(vectors)

    index = build_index(
        settings.azure_search_index_name,
        semantic_ranker=settings.azure_search_semantic_ranker,
    )
    # Built before the index is dropped: `strict=True` turns a short embedding
    # response into a failure that leaves the live index untouched.
    documents = [
        to_search_document(record, vector)
        for record, vector in zip(records, vectors, strict=True)
    ]

    replaced = recreate_index(index_client, index)
    logger.info("index created", extra={"index": index.name, "replaced": replaced})

    uploaded = upload_documents(search_client, documents)
    logger.info("ingestion complete", extra={"documents": uploaded})
    return uploaded


def main() -> int:
    """Load configuration, open the Azure clients, and run ingestion.

    Returns:
        A process exit code; 0 on success.
    """
    settings = load_settings()
    configure_logging(settings.log_level)
    credential = AzureKeyCredential(settings.azure_search_api_key.get_secret_value())

    with correlation_context() as run_id:
        logger.info(
            "ingestion started",
            extra={
                "run_id": run_id,
                "catalog_url": CATALOG_URL,
                "index": settings.azure_search_index_name,
                "semantic_ranker": settings.azure_search_semantic_ranker,
            },
        )
        with (
            AzureOpenAI(
                azure_endpoint=settings.azure_openai_endpoint,
                api_key=settings.azure_openai_api_key.get_secret_value(),
                api_version=AZURE_OPENAI_API_VERSION,
                max_retries=MAX_RETRIES,
            ) as openai_client,
            SearchIndexClient(settings.azure_search_endpoint, credential) as index_client,
            SearchClient(
                settings.azure_search_endpoint, settings.azure_search_index_name, credential
            ) as search_client,
        ):
            run_ingestion(settings, openai_client, index_client, search_client)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
