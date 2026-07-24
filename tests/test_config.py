"""Unit tests for `llm_policy_library.config`."""

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import llm_policy_library.config as testee

# The minimum set of variables an operator must supply; everything else has a
# default. Tests pass `env_file=None` so the developer's real `.env` (present in
# the repo root) can never make a "missing variable" test pass by accident.
REQUIRED_ENV = {
    "AZURE_OPENAI_ENDPOINT": "https://oai.example.com/",
    "AZURE_OPENAI_API_KEY": "oai-secret-key",
    "AZURE_OPENAI_CHAT_DEPLOYMENT": "gpt-5-mini",
    "AZURE_OPENAI_EMBEDDING_DEPLOYMENT": "text-embedding-3-small",
    "AZURE_SEARCH_ENDPOINT": "https://search.example.net",
    "AZURE_SEARCH_API_KEY": "search-secret-key",
    "AZURE_SEARCH_INDEX_NAME": "nist-800-53-controls",
}


@pytest.fixture
def required_env() -> Iterator[None]:
    """Provide exactly the required variables, hiding the ambient environment."""
    with patch.dict(os.environ, REQUIRED_ENV, clear=True):
        yield


def write_env_file(directory: Path, values: dict[str, str]) -> Path:
    """Write a dotenv file and return its path.

    Args:
        directory: Directory to write into.
        values: Variables to serialize.

    Returns:
        Path of the written file.
    """
    path = directory / ".env"
    path.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    return path


def test_default_env_file_is_resolved_relative_to_the_repo_not_the_cwd() -> None:
    """A relative ".env" would silently read the wrong file if launched from elsewhere."""
    assert testee.DEFAULT_ENV_FILE.is_absolute()
    assert (testee.DEFAULT_ENV_FILE.parent / "pyproject.toml").is_file()


def test_settings_defaults_encode_the_task_requirements(required_env: None) -> None:
    """Defaults retrieve 3-5 docs, rank semantically, and minimize reasoning effort."""
    settings = testee.load_settings(env_file=None)

    assert 3 <= settings.retrieval_top_k <= 5, "TASK.md requires the top 3-5 results"
    assert settings.min_reranker_score > 0.0, "a zero floor can never trigger the fallback"
    assert settings.min_vector_score > 0.0, "a zero floor can never trigger the fallback"
    assert settings.azure_search_semantic_ranker is True
    assert settings.llm_reasoning_effort == "minimal"
    # The corpus map ships ON. This is the A/B's verdict (TODO.md decision D12,
    # three evaluation runs per setting), and what makes the safe fallback
    # structural rather than a lucky miss on the relevance floor -- TASK.md
    # requirement 3. Without this assertion the default silently reverts.
    assert settings.planner_corpus_map is True
    assert settings.log_level == "INFO"


def test_env_example_documents_the_shipped_corpus_map_default() -> None:
    """The template an operator copies must not quietly disable the shipped default."""
    # `cp .env.example .env` is the documented setup step, so the template is a
    # second, equally live source of the default: a `false` here would override
    # the code default for every operator who followed the README, and no test of
    # `Settings` alone would notice.
    template = (testee.DEFAULT_ENV_FILE.parent / ".env.example").read_text(encoding="utf-8")

    assert "PLANNER_CORPUS_MAP=true" in template
    assert "PLANNER_CORPUS_MAP=false" not in template


def test_corpus_map_can_still_be_disabled_explicitly(required_env: None) -> None:
    """The A/B lever must survive shipping: the pre-map pipeline stays reachable."""
    with patch.dict(os.environ, {"PLANNER_CORPUS_MAP": "false"}):
        settings = testee.load_settings(env_file=None)

    assert settings.planner_corpus_map is False


def test_settings_redacts_secrets_when_rendered(required_env: None) -> None:
    """API keys must not leak through a repr caught in a log line or traceback."""
    settings = testee.load_settings(env_file=None)

    assert "oai-secret-key" not in repr(settings)
    assert "search-secret-key" not in repr(settings)
    assert settings.azure_openai_api_key.get_secret_value() == "oai-secret-key"
    assert settings.azure_search_api_key.get_secret_value() == "search-secret-key"


def test_settings_is_immutable(required_env: None) -> None:
    """Config is frozen so no request handler can mutate it mid-flight."""
    settings = testee.load_settings(env_file=None)

    with pytest.raises(ValidationError):
        settings.retrieval_top_k = 99


def test_settings_parses_semantic_ranker_toggle_from_a_string(required_env: None) -> None:
    """Free-tier operators disable the semantic ranker via a plain string env var."""
    with patch.dict(os.environ, {"AZURE_SEARCH_SEMANTIC_RANKER": "false"}):
        settings = testee.load_settings(env_file=None)

    assert settings.azure_search_semantic_ranker is False


def test_settings_rejects_a_non_positive_top_k(required_env: None) -> None:
    """Retrieving zero documents would silently force the fallback on every query."""
    with patch.dict(os.environ, {"RETRIEVAL_TOP_K": "0"}):
        with pytest.raises(testee.ConfigError, match="RETRIEVAL_TOP_K"):
            testee.load_settings(env_file=None)


def test_settings_rejects_a_negative_reranker_floor(required_env: None) -> None:
    """A negative floor would admit every document and defeat the grounding guard."""
    with patch.dict(os.environ, {"MIN_RERANKER_SCORE": "-0.5"}):
        with pytest.raises(testee.ConfigError, match="MIN_RERANKER_SCORE"):
            testee.load_settings(env_file=None)


def test_settings_rejects_a_reranker_floor_above_the_reranker_scale(required_env: None) -> None:
    """`@search.rerankerScore` tops out at 4, so a larger floor rejects every document."""
    with patch.dict(os.environ, {"MIN_RERANKER_SCORE": "4.5"}):
        with pytest.raises(testee.ConfigError, match="MIN_RERANKER_SCORE"):
            testee.load_settings(env_file=None)


def test_settings_rejects_a_vector_floor_on_the_reranker_scale(required_env: None) -> None:
    """1.8 is a valid reranker floor but an impossible cosine one; confusing them must fail."""
    with patch.dict(os.environ, {"MIN_VECTOR_SCORE": "1.8"}):
        with pytest.raises(testee.ConfigError, match="MIN_VECTOR_SCORE"):
            testee.load_settings(env_file=None)


def test_settings_rejects_an_unsupported_reasoning_effort(required_env: None) -> None:
    """An effort the model rejects must fail at startup, not on the first request."""
    with patch.dict(os.environ, {"LLM_REASONING_EFFORT": "turbo"}):
        with pytest.raises(testee.ConfigError, match="LLM_REASONING_EFFORT"):
            testee.load_settings(env_file=None)


def test_settings_treats_a_blank_value_as_missing(required_env: None) -> None:
    """A blank key must fail loudly rather than configure an empty credential."""
    with patch.dict(os.environ, {"AZURE_OPENAI_API_KEY": ""}):
        with pytest.raises(testee.ConfigError, match="AZURE_OPENAI_API_KEY"):
            testee.load_settings(env_file=None)


def test_settings_ignores_unrelated_environment_variables(required_env: None) -> None:
    """The process environment carries unrelated variables that must not break startup."""
    with patch.dict(os.environ, {"UNRELATED_VARIABLE": "noise"}):
        settings = testee.load_settings(env_file=None)

    assert settings.azure_search_index_name == "nist-800-53-controls"


def test_load_settings_names_every_missing_variable() -> None:
    """A first-run operator needs the full list of variables to set, not just the first."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(testee.ConfigError) as caught:
            testee.load_settings(env_file=None)

    message = str(caught.value)
    for name in REQUIRED_ENV:
        assert name in message
    assert ".env.example" in message
    assert "docs/azure-setup.md" in message


def test_load_settings_chains_the_underlying_validation_error() -> None:
    """The pydantic error is preserved for debugging, not swallowed."""
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(testee.ConfigError) as caught:
            testee.load_settings(env_file=None)

    assert isinstance(caught.value.__cause__, ValidationError)


def test_load_settings_reads_the_dotenv_file(tmp_path: Path) -> None:
    """The documented setup flow puts credentials in `.env`, not the shell."""
    env_file = write_env_file(tmp_path, REQUIRED_ENV)

    with patch.dict(os.environ, {}, clear=True):
        settings = testee.load_settings(env_file=env_file)

    assert settings.azure_openai_chat_deployment == "gpt-5-mini"


def test_load_settings_prefers_the_process_environment_over_the_dotenv_file(
    tmp_path: Path,
) -> None:
    """A container's injected variables must override a stale checked-out `.env`."""
    env_file = write_env_file(tmp_path, REQUIRED_ENV | {"RETRIEVAL_TOP_K": "3"})

    with patch.dict(os.environ, {"RETRIEVAL_TOP_K": "4"}, clear=True):
        settings = testee.load_settings(env_file=env_file)

    assert settings.retrieval_top_k == 4
