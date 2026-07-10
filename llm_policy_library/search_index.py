"""The Azure AI Search index that backs retrieval.

The index supports hybrid search: `content` is analysed for BM25 keyword
matching while `embedding` holds its vector, and a query issues both and fuses
the rankings. Semantic reranking is layered on top when the service tier
provides it (Basic or above); on the Free tier the semantic configuration must
be omitted entirely, because the API rejects it.

Two fields carry the control's identity. Azure AI Search restricts document key
*values* to letters, digits, dash, underscore, and equal sign, so an enhancement
ID like `ac-2.1` cannot be a key. `key` therefore holds a dot-free encoding
(`ac-2_1`) while `id` keeps the exact OSCAL ID that answers cite and that the
evaluation golden set labels. Control IDs never contain an underscore, so the
encoding is unambiguous.
"""

from typing import Final

from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchAlgorithmMetric,
    VectorSearchProfile,
)

# Output width of `text-embedding-3-small`. Ingestion checks the vectors it gets
# back against this, so pointing the deployment at `-3-large` (3,072) fails
# loudly instead of being rejected field-by-field at upload time.
EMBEDDING_DIMENSIONS: Final = 1536

SEMANTIC_CONFIGURATION_NAME: Final = "policy-semantic"

_HNSW_ALGORITHM_NAME: Final = "policy-hnsw"
_VECTOR_PROFILE_NAME: Final = "policy-vector-profile"


def document_key(control_id: str) -> str:
    """Encode an OSCAL control ID as a legal Azure AI Search document key.

    Only the dot needs escaping, and control IDs never contain an underscore, so
    `ac-2.1` maps to `ac-2_1` without ever colliding with another control.

    Args:
        control_id: An OSCAL control ID, e.g. `ac-2.1`.

    Returns:
        The document key.
    """
    return control_id.replace(".", "_")


def build_index(
    name: str, *, semantic_ranker: bool, dimensions: int = EMBEDDING_DIMENSIONS
) -> SearchIndex:
    """Construct the index definition for the policy catalog.

    Pure: it builds the schema object without contacting the service, so the
    schema is asserted in unit tests.

    Args:
        name: Index name.
        semantic_ranker: Whether to attach a semantic configuration. Must be
            False on the Free tier, whose API rejects semantic configurations.
        dimensions: Width of the embedding vectors.

    Returns:
        The index definition.
    """
    fields = [
        # Dot-free encoding of `id`; see the module docstring.
        SimpleField(name="key", type=SearchFieldDataType.STRING, key=True),
        # Filterable so evaluation can fetch a labelled control by its exact ID.
        SimpleField(name="id", type=SearchFieldDataType.STRING, filterable=True, sortable=True),
        SearchableField(name="title", type=SearchFieldDataType.STRING),
        SearchableField(name="description", type=SearchFieldDataType.STRING),
        SearchableField(
            name="category",
            type=SearchFieldDataType.STRING,
            filterable=True,
            facetable=True,
        ),
        # The exact text that was embedded, and the BM25 half of hybrid search.
        SearchableField(name="content", type=SearchFieldDataType.STRING),
        SearchField(
            name="embedding",
            # `Collection` is a helper function hung off the enum class, which
            # mypy reads as a (non-callable) enum member.
            type=SearchFieldDataType.Collection(SearchFieldDataType.SINGLE),  # type: ignore[operator]
            searchable=True,
            # Never read back: answers are grounded in `description`, and
            # returning 1,536 floats per hit would dominate the response.
            retrievable=False,
            stored=False,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=_VECTOR_PROFILE_NAME,
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[
            HnswAlgorithmConfiguration(
                name=_HNSW_ALGORITHM_NAME,
                # `text-embedding-3-*` vectors are normalised, so cosine is the
                # metric the model was trained against.
                parameters=HnswParameters(metric=VectorSearchAlgorithmMetric.COSINE),
            )
        ],
        profiles=[
            VectorSearchProfile(
                name=_VECTOR_PROFILE_NAME,
                algorithm_configuration_name=_HNSW_ALGORITHM_NAME,
            )
        ],
    )

    semantic_search = None
    if semantic_ranker:
        semantic_search = SemanticSearch(
            configurations=[
                SemanticConfiguration(
                    name=SEMANTIC_CONFIGURATION_NAME,
                    # The reranker reads `description`, not `content`: `content`
                    # repeats the title and category as a prefix for BM25's
                    # benefit, which would only crowd the reranker's input.
                    prioritized_fields=SemanticPrioritizedFields(
                        title_field=SemanticField(field_name="title"),
                        content_fields=[SemanticField(field_name="description")],
                        keywords_fields=[SemanticField(field_name="category")],
                    ),
                )
            ]
        )

    return SearchIndex(
        name=name,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )


def recreate_index(client: SearchIndexClient, index: SearchIndex) -> bool:
    """Drop the index if it exists, then create it from `index`.

    Dropping rather than updating makes ingestion idempotent: a re-run leaves
    exactly the documents in the current catalog, with no orphans from a
    previous parse, and it side-steps the schema changes Azure AI Search refuses
    to apply in place.

    Args:
        client: Index-management client for the search service.
        index: The index definition to create.

    Returns:
        True if an existing index was dropped first.
    """
    existed = index.name in client.list_index_names()
    if existed:
        client.delete_index(index.name)
    client.create_index(index)
    return existed
