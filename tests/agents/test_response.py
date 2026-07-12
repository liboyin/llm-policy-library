"""Unit tests for `llm_policy_library.agents.response`."""

import logging
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import llm_policy_library.agents.response as testee
from llm_policy_library.models import RetrievedDocument
from llm_policy_library.prompts import get_prompt


def make_document(control_id: str, score: float = 2.2) -> RetrievedDocument:
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


def agent_answering(answer: str, input_tokens: int = 3500, output_tokens: int = 250) -> MagicMock:
    """Stub a Response Agent that replies with `answer`.

    Args:
        answer: The prose the model returns.
        input_tokens: The prompt tokens the run billed.
        output_tokens: The completion tokens the run billed.

    Returns:
        The stub agent.
    """
    agent = MagicMock()
    # A real `RunUsage`, not a MagicMock attribute: `generate_response` logs these
    # as the measured token cost, and a MagicMock would let a non-int reach the
    # audit trail without any test noticing.
    agent.run = AsyncMock(
        return_value=MagicMock(
            output=answer,
            usage=RunUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        )
    )
    return agent


def test_build_response_agent_sets_the_configured_effort_and_prose_output() -> None:
    """An answer is prose; a JSON envelope would cost tokens and buy nothing."""
    agent = testee.build_response_agent(cast(OpenAIChatModel, TestModel()), "minimal")

    # See test_planner on why the exact settings key is load-bearing.
    assert agent.model_settings == {"openai_reasoning_effort": "minimal"}
    assert agent.output_type is str


def test_response_instructions_forbid_uncited_and_invented_controls() -> None:
    """The prompt is the first grounding defence; citation checking is the second."""
    instructions = get_prompt("response_instructions")
    assert "Use only the supplied controls" in instructions
    assert "never invent an ID" in instructions


def test_format_documents_labels_each_control_with_the_id_to_cite() -> None:
    """Citing correctly must be a copy of the label, not a transformation of it."""
    block = testee.format_documents([make_document("ac-2.1")])

    assert block.startswith("[ac-2.1] Title of ac-2.1 (Access Control)")
    assert "Statement of ac-2.1" in block


def test_format_documents_separates_controls_so_statements_cannot_run_together() -> None:
    """Two adjacent statements would read as one requirement to the model."""
    block = testee.format_documents([make_document("ac-2"), make_document("au-6")])

    assert "\n\n[au-6]" in block


def test_extract_citations_returns_retrieved_ids_in_first_mention_order() -> None:
    """Citation order follows the answer, which is what a reader checks against."""
    answer = "Start with [au-6], then [ac-2], and again [au-6]."

    grounded, invented = testee.extract_citations(answer, ["ac-2", "au-6"])

    assert grounded == ["au-6", "ac-2"]
    assert invented == []


def test_extract_citations_separates_a_control_that_was_never_retrieved() -> None:
    """An invented ID must never reach `citations`; that is the hard grounding check."""
    answer = "See [ac-2] and also [zz-99]."

    grounded, invented = testee.extract_citations(answer, ["ac-2"])

    assert grounded == ["ac-2"]
    assert invented == ["zz-99"]


def test_extract_citations_matches_enhancement_ids() -> None:
    """`ac-2.1` is a distinct control from `ac-2` and must be citable in its own right."""
    grounded, _ = testee.extract_citations("Per [ac-2.1].", ["ac-2.1"])

    assert grounded == ["ac-2.1"]


def test_extract_citations_is_case_insensitive_and_normalizes_to_the_index_form() -> None:
    """Models write `[AC-2]`; the index, the golden set, and the citation list use `ac-2`."""
    grounded, invented = testee.extract_citations("Per [AC-2].", ["ac-2"])

    assert grounded == ["ac-2"]
    assert invented == []


def test_extract_citations_case_folds_the_allow_list_too() -> None:
    """Comparing a folded citation against an unfolded ID would misfile it as invented."""
    grounded, invented = testee.extract_citations("Per [ac-2].", ["AC-2"])

    assert grounded == ["ac-2"]
    assert invented == []


def test_extract_citations_ignores_bracketed_text_that_is_not_a_control_id() -> None:
    """Prose brackets such as `[see below]` must not be reported as sources."""
    grounded, invented = testee.extract_citations("As noted [see below], use [ac-2].", ["ac-2"])

    assert grounded == ["ac-2"]
    assert invented == []


def test_safe_fallback_cites_nothing_and_marks_itself() -> None:
    """A caller must be able to tell a refusal from an answer without parsing prose."""
    response = testee.safe_fallback()

    assert response.is_fallback is True
    assert response.citations == []
    assert response.answer == get_prompt("safe_fallback_message")


async def test_generate_response_returns_the_fallback_without_calling_the_model() -> None:
    """A model given no documents and told to use only documents will invent one."""
    agent = agent_answering("should never run")

    response = await testee.generate_response(agent, "What is the capital of France?", [])

    assert response.is_fallback is True
    agent.run.assert_not_awaited()


async def test_generate_response_grounds_the_prompt_in_the_retrieved_controls() -> None:
    """The model may only see the controls retrieval approved, plus the question."""
    agent = agent_answering("Per [ac-2], accounts are managed.")

    await testee.generate_response(agent, "access control?", [make_document("ac-2")])

    prompt = agent.run.await_args.args[0]
    assert "Question: access control?" in prompt
    assert "[ac-2] Title of ac-2" in prompt


async def test_generate_response_reports_only_citations_that_were_retrieved() -> None:
    """An invented control must not be presented to the user as a source."""
    agent = agent_answering("Per [ac-2] and [zz-99].")

    response = await testee.generate_response(agent, "q", [make_document("ac-2")])

    assert response.citations == ["ac-2"]
    assert response.is_fallback is False


async def test_generate_response_logs_an_invented_citation_as_a_grounding_violation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silently dropping an invented ID would hide a prompt or model regression."""
    agent = agent_answering("Per [zz-99].")

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        response = await testee.generate_response(agent, "q", [make_document("ac-2")])

    assert response.citations == []
    assert any(
        getattr(record, "invented_citations", None) == ["zz-99"] for record in caplog.records
    )


async def test_generate_response_keeps_the_answer_verbatim() -> None:
    """The user reads the model's prose; only the citation list is filtered."""
    agent = agent_answering("  Per [ac-2], accounts are managed.  ")

    response = await testee.generate_response(agent, "q", [make_document("ac-2")])

    assert response.answer == "Per [ac-2], accounts are managed."


async def test_generate_response_raises_on_an_empty_answer() -> None:
    """Serving an empty answer would look grounded; the fallback would misreport why."""
    agent = agent_answering("   ")

    with pytest.raises(testee.ResponseError, match="empty answer"):
        await testee.generate_response(agent, "q", [make_document("ac-2")])


async def test_generate_response_wraps_a_model_misbehavior_as_a_response_error() -> None:
    """A model that gives up answering is a Response failure, not an opaque library error."""
    agent = MagicMock()
    agent.run = AsyncMock(side_effect=UnexpectedModelBehavior("Exceeded maximum retries"))

    with pytest.raises(testee.ResponseError, match="failed to produce"):
        await testee.generate_response(agent, "q", [make_document("ac-2")])


async def test_generate_response_logs_the_chat_tokens_the_run_billed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The Response Agent dominates the token bill, so a TPM budget is only real if it is logged."""
    agent = agent_answering("See [ac-2].", input_tokens=3612, output_tokens=241)

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.generate_response(agent, "q", [make_document("ac-2")])

    record = next(record for record in caplog.records if record.message == "answer generated")
    assert getattr(record, "input_tokens") == 3612
    assert getattr(record, "output_tokens") == 241
