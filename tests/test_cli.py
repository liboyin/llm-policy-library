"""Unit tests for `llm_policy_library.cli`."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import llm_policy_library.cli as testee
from llm_policy_library.models import (
    GroundedResponse,
    PipelineResult,
    PlanStep,
    QueryPlan,
    RetrievalResult,
    RetrievedDocument,
)


def make_result() -> PipelineResult:
    """Build a pipeline result with one retrieved control and a grounded answer.

    Returns:
        The result.
    """
    plan = QueryPlan(
        original_query="What controls apply to API security?",
        steps=[PlanStep(search_query="api security", purpose="find controls")],
    )
    documents = [
        RetrievedDocument(
            id="ac-2", title="Account Management", description="Statement", category="Access Control", score=2.2
        )
    ]
    answer = GroundedResponse(answer="Per [ac-2].", citations=["ac-2"], is_fallback=False)
    return PipelineResult(
        plan=plan,
        results=[RetrievalResult(step=plan.steps[0], documents=documents)],
        documents=documents,
        response=answer,
    )


def test_parse_args_reads_the_positional_query() -> None:
    """The whole CLI surface is one positional argument: the question."""
    args = testee.parse_args(["What controls apply to API security?"])

    assert args.query == "What controls apply to API security?"


def test_parse_args_requires_a_query() -> None:
    """Running the CLI with no question must fail fast, not hang or crash later."""
    with pytest.raises(SystemExit):
        testee.parse_args([])


def test_format_result_lists_every_plan_step_with_its_purpose() -> None:
    """The plan is the audit trail's first line; a reader must see why each step ran."""
    result = make_result()

    report = testee.format_result(result)

    assert "api security" in report
    assert "find controls" in report


def test_format_result_lists_retrieved_controls_with_their_scores() -> None:
    """The evidence the answer was grounded in must be visible, not just asserted."""
    result = make_result()

    report = testee.format_result(result)

    assert "[ac-2]" in report
    assert "score=2.200" in report


def test_format_result_reports_no_controls_when_the_grounding_set_is_empty() -> None:
    """A fallback answer must not silently print an empty, unlabeled section."""
    plan = QueryPlan(
        original_query="What is the capital of France?",
        steps=[PlanStep(search_query="capital of France", purpose="find controls")],
    )
    answer = GroundedResponse(answer="fallback message", citations=[], is_fallback=True)
    result = PipelineResult(
        plan=plan,
        results=[RetrievalResult(step=plan.steps[0], documents=[])],
        documents=[],
        response=answer,
    )

    report = testee.format_result(result)

    assert "(none)" in report
    assert "Citations:" not in report


def test_format_result_includes_the_answer_and_its_citations() -> None:
    """The answer is the point of the report; citations must be traceable alongside it."""
    result = make_result()

    report = testee.format_result(result)

    assert "Per [ac-2]." in report
    assert "Citations: ac-2" in report


async def test_run_loads_settings_once_and_answers_through_the_pipeline() -> None:
    """The CLI drives the same pipeline the API serves, logging to stderr so the report stays clean."""
    result = make_result()
    settings = MagicMock()
    pipeline = MagicMock()
    pipeline.answer_query = AsyncMock(return_value=result)

    @asynccontextmanager
    async def fake_open_pipeline(_settings: Any) -> AsyncIterator[MagicMock]:
        yield pipeline

    with (
        patch.object(testee, "load_settings", return_value=settings) as load_settings,
        patch.object(testee, "configure_logging") as configure_logging,
        patch.object(testee, "open_pipeline", fake_open_pipeline),
    ):
        report = await testee.run("What controls apply to API security?")

    load_settings.assert_called_once_with()
    configure_logging.assert_called_once_with(settings.log_level, stream=testee.sys.stderr)
    pipeline.answer_query.assert_awaited_once_with("What controls apply to API security?")
    assert "Per [ac-2]." in report


def test_main_prints_the_formatted_report(capsys: pytest.CaptureFixture[str]) -> None:
    """The one thing a CLI invocation must do: print the report to stdout."""
    with patch.object(testee, "run", AsyncMock(return_value="formatted report")):
        exit_code = testee.main(["What controls apply to API security?"])

    assert exit_code == 0
    assert capsys.readouterr().out == "formatted report\n"
