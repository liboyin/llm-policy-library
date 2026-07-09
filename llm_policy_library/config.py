"""Application configuration, read from the environment or a `.env` file.

The demo authenticates to Azure with API keys supplied through the environment.
`docs/azure-setup.md` explains how to provision the resources and where each
value is found in the Azure Portal; `.env.example` is the committed template.

Configuration is validated once, at startup, so that a missing or malformed
variable fails immediately with an actionable message rather than surfacing as
an opaque Azure SDK error on the first request.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_ENV_FILE = ".env"

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
            ranker needs Basic tier or above; set false on the Free tier.
        retrieval_top_k: Documents retrieved per plan step.
        min_relevance_score: Retrieved documents scoring below this are dropped,
            which is what triggers the safe fallback when nothing is relevant.
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

    retrieval_top_k: int = Field(default=5, ge=1)
    min_relevance_score: float = Field(default=0.02, ge=0.0)
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
