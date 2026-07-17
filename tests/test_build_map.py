"""Unit tests for `llm_policy_library.build_map`."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from agent_framework import UsageDetails

import llm_policy_library.build_map as testee
from llm_policy_library.config import Settings
from llm_policy_library.corpus_map import ABSTRACT_CHAR_CAP, CorpusMap, CorpusSource, FamilyEntry
from llm_policy_library.dataset import CATALOG_COMMIT, CATALOG_URL, PolicyRecord
from llm_policy_library.prompts import get_prompt


def make_settings() -> Settings:
    """Build a fully-specified settings object without touching the environment.

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
    )


def make_record(control_id: str, category: str, description: str = "Statement.") -> PolicyRecord:
    """Build a policy record in the given family.

    Args:
        control_id: The OSCAL control ID.
        category: The control family.
        description: The control statement.

    Returns:
        The record.
    """
    return PolicyRecord(
        id=control_id, title=f"Title of {control_id}", description=description, category=category
    )


def response_with(abstract: str | None, usage: UsageDetails | None = None) -> MagicMock:
    """Stub one `AgentResponse` carrying a structured abstract.

    Args:
        abstract: The abstract text, or None for a response the model failed to
            shape.
        usage: The call's token usage, or None when the model reported none.

    Returns:
        The stub response.
    """
    value = None if abstract is None else testee.FamilyAbstract(abstract=abstract)
    return MagicMock(value=value, usage_details=usage)


def agent_returning(*responses: Any) -> MagicMock:
    """Stub an agent whose successive runs yield `responses`.

    Args:
        responses: What each `run` call returns, or an exception it raises.

    Returns:
        The stub agent.
    """
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=list(responses))
    return agent


def test_char_aims_start_at_the_cap_and_descend() -> None:
    """Aiming at the cap first is what buys maximum routing detail; a rung above it can never fit."""
    assert testee.ABSTRACT_CHAR_AIMS[0] == ABSTRACT_CHAR_CAP
    assert list(testee.ABSTRACT_CHAR_AIMS) == sorted(testee.ABSTRACT_CHAR_AIMS, reverse=True)
    assert all(aim <= ABSTRACT_CHAR_CAP for aim in testee.ABSTRACT_CHAR_AIMS)


def test_family_abstract_schema_carries_no_length_cap() -> None:
    """A schema cap is compiled into the grammar and truncates mid-word; the cap is checked, not decoded."""
    schema = testee.FamilyAbstract.model_json_schema()

    assert "maxLength" not in schema["properties"]["abstract"]


def test_build_abstract_agent_requests_the_abstract_schema_and_effort() -> None:
    """The abstract's shape is enforced by structured outputs, not parsed out of prose."""
    agent = testee.build_abstract_agent(MagicMock(), "minimal")

    options = agent.default_options
    assert options["response_format"] is testee.FamilyAbstract
    assert options["reasoning"] == {"effort": "minimal"}


def test_corpus_map_instructions_scope_the_abstract_to_routing() -> None:
    """An abstract that describes a sibling's topic routes questions to the wrong family."""
    instructions = get_prompt("corpus_map_instructions", max_sentences=testee.ABSTRACT_MAX_SENTENCES)

    assert "Describe only the family you were given" in instructions
    assert "distinguishable from every sibling" in instructions
    assert "Never write a sibling family's name" in instructions
    assert f"at most {testee.ABSTRACT_MAX_SENTENCES} sentences" in instructions


def test_group_by_family_preserves_catalog_order() -> None:
    """Catalog order is the map's order, which is what keeps a regenerated map a readable diff."""
    records = [
        make_record("ac-1", "Access Control"),
        make_record("mp-1", "Media Protection"),
        make_record("ac-2", "Access Control"),
    ]

    families = testee.group_by_family(records)

    assert list(families) == ["Access Control", "Media Protection"]
    assert [record.id for record in families["Access Control"]] == ["ac-1", "ac-2"]


def test_sibling_names_excludes_the_family_itself() -> None:
    """A family told to distinguish itself from itself would be asked for an impossible abstract."""
    names = ["Access Control", "Media Protection", "Incident Response"]

    assert testee.sibling_names("Media Protection", names) == [
        "Access Control",
        "Incident Response",
    ]


def test_format_family_controls_carries_every_control_id_title_and_statement() -> None:
    """The abstract can only name topics that actually reach the prompt."""
    records = [
        make_record("ac-1", "Access Control", "Develop an access control policy."),
        make_record("ac-2", "Access Control", "Manage system accounts."),
    ]

    block = testee.format_family_controls(records)

    assert "AC-1: Title of ac-1" in block
    assert "Develop an access control policy." in block
    assert "AC-2: Title of ac-2" in block
    assert "Manage system accounts." in block


def test_build_family_prompt_offers_the_budget_and_shows_the_siblings() -> None:
    """The budget is the only length lever that works, and siblings are what make entries distinct."""
    records = [make_record("ac-1", "Access Control", "Develop an access control policy.")]

    prompt = testee.build_family_prompt("Access Control", ["Media Protection"], records, 175)

    assert "at most 175 characters" in prompt
    assert "Media Protection" in prompt
    assert "Develop an access control policy." in prompt
    assert "The 1 controls in Access Control" in prompt


def test_build_family_prompt_survives_braces_in_control_text() -> None:
    """Control statements carry literal braces; they must not be read as template fields."""
    records = [make_record("ac-1", "Access Control", "Set {timeout} per policy.")]

    prompt = testee.build_family_prompt("Access Control", [], records, 200)

    assert "Set {timeout} per policy." in prompt


async def test_request_abstract_returns_the_abstract_and_its_usage() -> None:
    """Usage is what the phase's token arithmetic is re-derived from; it must survive the call."""
    usage = UsageDetails(input_token_count=7309, output_token_count=60)
    agent = agent_returning(response_with("  Accounts and privileges.  ", usage))

    abstract, reported = await testee.request_abstract(agent, "prompt")

    assert abstract == "Accounts and privileges."
    assert reported == usage


async def test_request_abstract_defaults_usage_when_the_model_reports_none() -> None:
    """The token log keys are always ints; a None usage must not reach them as a None."""
    agent = agent_returning(response_with("Accounts.", None))

    _, usage = await testee.request_abstract(agent, "prompt")

    assert usage.get("input_token_count") is None
    assert (usage.get("input_token_count") or 0) == 0


async def test_request_abstract_retries_a_transient_failure() -> None:
    """A rate limit mid-build must cost a retry, not the whole twenty-call run."""
    agent = agent_returning(RuntimeError("429 rate limit"), response_with("Accounts."))

    with patch.object(testee.asyncio, "sleep", AsyncMock()):
        abstract, _ = await testee.request_abstract(agent, "prompt")

    assert abstract == "Accounts."
    assert agent.run.await_count == 2


async def test_request_abstract_retries_a_blank_abstract() -> None:
    """A blank abstract is a failed call, not a short one; unretried it dies at FamilyEntry."""
    agent = agent_returning(response_with("   "), response_with("Accounts."))

    with patch.object(testee.asyncio, "sleep", AsyncMock()):
        abstract, _ = await testee.request_abstract(agent, "prompt")

    assert abstract == "Accounts."
    assert agent.run.await_count == 2


async def test_request_abstract_raises_when_the_model_never_returns_an_abstract() -> None:
    """A map with a hole is not worth committing, so the final failure must propagate."""
    agent = agent_returning(*[response_with(None)] * testee._CHAT_ATTEMPTS)

    with patch.object(testee.asyncio, "sleep", AsyncMock()):
        with pytest.raises(testee.BuildMapError, match="no structured abstract"):
            await testee.request_abstract(agent, "prompt")

    assert agent.run.await_count == testee._CHAT_ATTEMPTS


async def test_summarize_family_keeps_an_abstract_that_fits_the_first_budget() -> None:
    """Most families fit at the full budget; that path must cost exactly one call."""
    agent = agent_returning(response_with("Accounts, privileges, sessions."))

    abstract = await testee.summarize_family(
        agent, "Access Control", ["Media Protection"], [make_record("ac-1", "Access Control")]
    )

    assert abstract == "Accounts, privileges, sessions."
    assert agent.run.await_count == 1
    assert f"at most {testee.ABSTRACT_CHAR_AIMS[0]} characters" in agent.run.await_args_list[0].args[0]


async def test_summarize_family_re_requests_on_a_smaller_budget_rather_than_trimming() -> None:
    """Trimming would lop off the last clause; the model must re-summarise to fit instead."""
    agent = agent_returning(response_with("A" * (ABSTRACT_CHAR_CAP + 1)), response_with("B" * 150))

    abstract = await testee.summarize_family(
        agent, "Access Control", [], [make_record("ac-1", "Access Control")]
    )

    assert abstract == "B" * 150, "the overlong first attempt must be replaced, not cut to length"
    assert agent.run.await_count == 2
    budgets = [call.args[0] for call in agent.run.await_args_list]
    assert f"at most {testee.ABSTRACT_CHAR_AIMS[0]} characters" in budgets[0]
    assert f"at most {testee.ABSTRACT_CHAR_AIMS[1]} characters" in budgets[1]


async def test_summarize_family_raises_when_every_budget_overruns() -> None:
    """A family that will not fit fails the build; a truncated entry must never be committed."""
    over = [response_with("A" * (ABSTRACT_CHAR_CAP + 1))] * len(testee.ABSTRACT_CHAR_AIMS)
    agent = agent_returning(*over)

    with pytest.raises(testee.BuildMapError, match="Access Control"):
        await testee.summarize_family(
            agent, "Access Control", [], [make_record("ac-1", "Access Control")]
        )

    assert agent.run.await_count == len(testee.ABSTRACT_CHAR_AIMS)


async def test_build_corpus_map_pins_the_catalog_it_summarised() -> None:
    """Without provenance the map cannot be told apart from one written for another corpus."""
    agent = agent_returning(response_with("Accounts."))

    corpus_map = await testee.build_corpus_map(agent, [make_record("ac-1", "Access Control")])

    assert corpus_map.source.sha == CATALOG_COMMIT
    assert corpus_map.source.url == CATALOG_URL


async def test_build_corpus_map_summarises_every_family_against_its_siblings() -> None:
    """Each entry must cover one family and know the others exist, or the entries overlap."""
    records = [
        make_record("ac-1", "Access Control"),
        make_record("ac-2", "Access Control"),
        make_record("mp-1", "Media Protection"),
    ]
    agent = agent_returning(response_with("Accounts."), response_with("Media."))

    corpus_map = await testee.build_corpus_map(agent, records)

    assert [(entry.name, entry.control_count) for entry in corpus_map.families] == [
        ("Access Control", 2),
        ("Media Protection", 1),
    ]
    access_prompt = agent.run.await_args_list[0].args[0]
    assert "Media Protection" in access_prompt, "the Access Control entry must see its sibling"
    assert "Family to abstract: Access Control" in access_prompt


def test_main_writes_the_map_where_the_loader_reads_it(tmp_path: Path) -> None:
    """A map written anywhere else is a map the Planner never sees."""
    records = [make_record("ac-1", "Access Control")]
    corpus_map = CorpusMap(
        source=CorpusSource(url=CATALOG_URL, sha=CATALOG_COMMIT),
        families=[FamilyEntry(name="Access Control", control_count=1, abstract="Accounts.")],
    )
    output = tmp_path / "corpus_map.json"

    with (
        patch.object(testee, "load_settings", return_value=make_settings()),
        patch.object(testee, "configure_logging"),
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=records),
        patch.object(testee, "validate_record_count") as validate,
        patch.object(testee, "OpenAIChatClient"),
        patch.object(testee, "build_corpus_map", AsyncMock(return_value=corpus_map)),
        patch.object(testee, "MAP_OUTPUT_PATH", output),
    ):
        assert testee.main() == 0

    written = json.loads(output.read_text())
    assert written["source"]["sha"] == CATALOG_COMMIT
    assert written["families"] == [
        {"name": "Access Control", "control_count": 1, "abstract": "Accounts."}
    ]
    # A truncated download would otherwise yield a map of a partial corpus.
    validate.assert_called_once_with(records)


def test_main_targets_the_configured_chat_deployment() -> None:
    """A misrouted client would summarise the catalog with the wrong model, or not at all."""
    settings = make_settings()
    corpus_map = CorpusMap(
        source=CorpusSource(url=CATALOG_URL, sha=CATALOG_COMMIT),
        families=[FamilyEntry(name="Access Control", control_count=1, abstract="Accounts.")],
    )

    with (
        patch.object(testee, "load_settings", return_value=settings),
        patch.object(testee, "configure_logging"),
        patch.object(testee, "fetch_catalog", return_value={"catalog": {}}),
        patch.object(testee, "parse_catalog", return_value=[make_record("ac-1", "Access Control")]),
        patch.object(testee, "validate_record_count"),
        patch.object(testee, "OpenAIChatClient") as client_class,
        patch.object(testee, "build_corpus_map", AsyncMock(return_value=corpus_map)),
        patch.object(testee, "MAP_OUTPUT_PATH", MagicMock()),
    ):
        testee.main()

    assert client_class.call_args.kwargs["model"] == settings.azure_openai_chat_deployment
    assert client_class.call_args.kwargs["azure_endpoint"] == settings.azure_openai_endpoint
