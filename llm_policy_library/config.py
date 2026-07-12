"""Application configuration, read from the environment or a `.env` file.

The demo authenticates to Azure with API keys supplied through the environment.
`docs/azure-setup.md` explains how to provision the resources and where each
value is found in the Azure Portal; `.env.example` is the committed template.

Configuration is validated once, at startup, so that a missing or malformed
variable fails immediately with an actionable message rather than surfacing as
an opaque Azure SDK error on the first request.
"""

from pathlib import Path
from typing import Final, Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved once, relative to this file rather than the process's working
# directory. `load_settings()` is called from `api.py`'s `lifespan` and
# `cli.py`'s `run()`, both entered exactly once per process, but a relative
# ".env" would still silently read the wrong file (or none) if uvicorn or the
# CLI is launched from outside the repo root.
DEFAULT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

# The Azure OpenAI API versions this project talks. Pinned: a query vector is
# only comparable to the document vectors already in the index if both came from
# the same embeddings API contract, so ingestion and serving read one value.
AZURE_OPENAI_EMBEDDING_API_VERSION: Final = "2024-10-21"

# Dated: PydanticAI drives the chat model through the official OpenAI SDK's
# deployments-based Azure endpoint, which accepts only dated API versions. The
# rolling `preview` alias belongs to the v1 surface the Agent Framework client
# used to target. What actually decides an answer — the model version — is
# pinned on the deployment itself, not here (decision D7).
AZURE_OPENAI_CHAT_API_VERSION: Final = "2024-12-01-preview"

# Mirrors the values accepted by the Azure OpenAI `reasoning_effort` parameter
# (`openai.types.shared.ReasoningEffort`), minus its `None` member. `none` is
# only valid on gpt-5.1; `minimal` is the lowest setting gpt-5-mini accepts.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or a value is invalid."""


class Settings(BaseSettings):
    """Validated runtime configuration for the policy-library system.

    Fields without a default are required: omitting one aborts startup. Secrets
    are held as `SecretStr` so that they are redacted if a settings object is
    ever caught up in a log line or traceback.

    Attributes:
        azure_openai_endpoint: Base URL of the Azure OpenAI resource.
        azure_openai_api_key: Admin key for the Azure OpenAI resource.
        azure_openai_chat_deployment: Deployment name of the chat model.
        azure_openai_embedding_deployment: Deployment name of the embedding model.
        azure_search_endpoint: Base URL of the Azure AI Search service.
        azure_search_api_key: Admin key for the Azure AI Search service.
        azure_search_index_name: Index holding the policy records.
        azure_search_semantic_ranker: Whether to apply semantic reranking. The
            ranker needs Basic tier or above; set false on the Free tier. It
            also selects which of the two relevance thresholds below applies.
        retrieval_top_k: Documents retrieved per plan step.
        min_reranker_score: Relevance floor on `@search.rerankerScore`, used
            when `azure_search_semantic_ranker` is true.
        min_vector_score: Relevance floor on the vector search `@search.score`,
            used when `azure_search_semantic_ranker` is false.
        llm_reasoning_effort: Reasoning effort for the chat model. Deployable
            Azure OpenAI chat models reject `temperature`/`top_p`/`seed`, so
            grounding and low reasoning effort stand in for sampling controls.
        log_level: Root log level.
    """

    # The dotenv path is not set here: `load_settings` always passes it, and two
    # sources for the same default would eventually disagree.
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        # Treat `FOO=` as unset so a blank value reports "Field required"
        # rather than silently configuring an empty endpoint or key.
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
    )

    azure_openai_endpoint: str
    azure_openai_api_key: SecretStr
    azure_openai_chat_deployment: str
    azure_openai_embedding_deployment: str

    azure_search_endpoint: str
    azure_search_api_key: SecretStr
    azure_search_index_name: str
    azure_search_semantic_ranker: bool = True

    # Capped at the candidate window retrieval fetches and the semantic ranker
    # reranks (`agents.retrieval.VECTOR_CANDIDATE_COUNT`). Asking for more would
    # silently return fewer, since rows outside that window carry no reranker
    # score and are dropped as unscored. The bound is a literal because importing
    # that constant here would cycle (retrieval imports config); a test in
    # test_retrieval pins the two equal so they cannot silently diverge.
    retrieval_top_k: int = Field(default=5, ge=1, le=50)

    # Two thresholds, because the ranker toggle changes which score retrieval
    # ranks on, and the two live on different scales. `llm_policy_library.agents.
    # retrieval` explains why neither may be replaced by a single number.
    #
    # Each default sits between the two score bands measured against the live
    # index on 2026-07-10. Relevant questions scored 2.00-3.26 on the reranker
    # and 0.635-0.776 on the vector scale; off-topic ones reached only 1.44 and
    # 0.576. The floors are set nearer the relevant band because a compliance
    # system should rather refuse than answer from a control it half-matched.
    min_reranker_score: float = Field(default=1.8, ge=0.0, le=4.0)
    min_vector_score: float = Field(default=0.60, ge=0.0, le=1.0)

    llm_reasoning_effort: ReasoningEffort = "minimal"

    log_level: LogLevel = "INFO"


def _format_validation_error(error: ValidationError) -> str:
    """Render a pydantic validation error as a list of environment variables.

    Field names map to environment variables by upper-casing, so reporting the
    upper-cased location tells the reader exactly which variable to fix.

    Args:
        error: The validation error raised while constructing `Settings`.

    Returns:
        A multi-line, human-readable description of every invalid variable.
    """
    problems = "\n".join(
        f"  - {'.'.join(str(part) for part in item['loc']).upper() or '<config>'}: {item['msg']}"
        for item in error.errors()
    )
    return (
        "Invalid configuration. Fix these environment variables "
        f"(see .env.example and docs/azure-setup.md):\n{problems}"
    )


def load_settings(env_file: str | Path | None = DEFAULT_ENV_FILE) -> Settings:
    """Build a `Settings` object, failing fast on missing or invalid variables.

    Args:
        env_file: Dotenv file consulted for variables absent from the process
            environment. Pass `None` to read the process environment only.

    Returns:
        The validated settings.

    Raises:
        ConfigError: If a required variable is missing or a value is invalid.
    """
    try:
        # pydantic's PEP 681 `dataclass_transform` makes mypy synthesize `__init__`
        # from the fields alone, hiding the `_env_file` argument that
        # `BaseSettings.__init__` really accepts.
        return Settings(_env_file=env_file)  # type: ignore[call-arg]
    except ValidationError as error:
        raise ConfigError(_format_validation_error(error)) from error
