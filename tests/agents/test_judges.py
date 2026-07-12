"""Unit tests for `llm_policy_library.agents.judges`."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError
from pydantic_ai import NativeOutput, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.models.test import TestModel

import llm_policy_library.agents.judges as testee
from llm_policy_library.prompts import get_prompt


def judge_returning(value: Any) -> MagicMock:
    """Stub a judge agent whose structured output is `value`.

    Args:
        value: What `AgentRunResult.output` holds, or an exception `run` raises.

    Returns:
        The stub agent.
    """
    agent = MagicMock()
    if isinstance(value, Exception):
        agent.run = AsyncMock(side_effect=value)
    else:
        agent.run = AsyncMock(return_value=MagicMock(output=value))
    return agent


def test_judge_verdict_rejects_a_score_off_the_scale() -> None:
    """An out-of-scale verdict must fail validation (and be retried), not skew the means."""
    with pytest.raises(ValidationError):
        testee.JudgeVerdict(reasoning="r", score=0)
    with pytest.raises(ValidationError):
        testee.JudgeVerdict(reasoning="r", score=6)


def test_judge_verdict_declares_reasoning_before_score() -> None:
    """The model emits fields in schema order: it must commit to a reason before the number."""
    assert list(testee.JudgeVerdict.model_fields) == ["reasoning", "score"]


def test_build_faithfulness_judge_requests_the_verdict_schema_and_effort() -> None:
    """The verdict shape is enforced by the model's native structured outputs, not parsed."""
    agent = testee.build_faithfulness_judge(cast(OpenAIChatModel, TestModel()), "minimal")

    assert isinstance(agent.output_type, NativeOutput)
    assert agent.output_type.outputs is testee.JudgeVerdict
    # See test_planner on why the exact settings key is load-bearing.
    assert agent.model_settings == {"openai_reasoning_effort": "minimal"}


def test_faithfulness_instructions_scope_the_judge_to_the_supplied_evidence() -> None:
    """A faithfulness judge that accepts outside NIST knowledge would bless hallucinations."""
    instructions = get_prompt("faithfulness_judge_instructions")
    assert "supported by the supplied control statements" in instructions
    assert "outside knowledge" in instructions
    assert "1 to 5" in instructions


def test_build_answer_relevancy_judge_requests_the_verdict_schema_and_effort() -> None:
    """Both judges share one verdict schema so their scores land on one comparable scale."""
    agent = testee.build_answer_relevancy_judge(cast(OpenAIChatModel, TestModel()), "minimal")

    assert isinstance(agent.output_type, NativeOutput)
    assert agent.output_type.outputs is testee.JudgeVerdict
    assert agent.model_settings == {"openai_reasoning_effort": "minimal"}


def test_answer_relevancy_instructions_exclude_grounding() -> None:
    """A relevancy judge shown grounding rules would re-score faithfulness, collapsing the metrics."""
    instructions = get_prompt("answer_relevancy_judge_instructions")
    assert "Do not judge whether the answer is factually correct" in instructions
    assert "1 to 5" in instructions


async def test_judge_faithfulness_shows_the_question_answer_and_context() -> None:
    """The judge can only ground its verdict in evidence that actually reaches its prompt."""
    agent = judge_returning(testee.JudgeVerdict(reasoning="fully supported", score=5))

    score = await testee.judge_faithfulness(
        agent, question="access control?", answer="Use [ac-2].", context="[ac-2] Account Management"
    )

    assert score == 5
    prompt = agent.run.await_args.args[0]
    assert "access control?" in prompt
    assert "Use [ac-2]." in prompt
    assert "[ac-2] Account Management" in prompt


async def test_judge_faithfulness_survives_braces_in_the_judged_text() -> None:
    """Control statements and answers may contain literal braces; they must not break templating."""
    agent = judge_returning(testee.JudgeVerdict(reasoning="ok", score=4))

    score = await testee.judge_faithfulness(
        agent, question="q", answer="Set {timeout} per [ac-12].", context="[ac-12] uses {value}"
    )

    assert score == 4
    prompt = agent.run.await_args.args[0]
    assert "Set {timeout} per [ac-12]." in prompt


async def test_judge_faithfulness_propagates_a_model_failure() -> None:
    """The harness owns retries and None-recording; swallowing errors here would defeat both."""
    agent = judge_returning(UnexpectedModelBehavior("Exceeded maximum retries"))

    with pytest.raises(UnexpectedModelBehavior):
        await testee.judge_faithfulness(agent, question="q", answer="a", context="c")


async def test_judge_answer_relevancy_shows_the_question_and_answer_only() -> None:
    """Withholding the controls keeps this judge scoring relevancy, not grounding again."""
    agent = judge_returning(testee.JudgeVerdict(reasoning="on point", score=4))

    score = await testee.judge_answer_relevancy(
        agent, question="access control?", answer="Use [ac-2]."
    )

    assert score == 4
    prompt = agent.run.await_args.args[0]
    assert "access control?" in prompt
    assert "Use [ac-2]." in prompt
    assert "control statements" not in prompt


async def test_judge_answer_relevancy_propagates_a_model_failure() -> None:
    """The harness owns retries and None-recording; see the faithfulness twin."""
    agent = judge_returning(UnexpectedModelBehavior("Exceeded maximum retries"))

    with pytest.raises(UnexpectedModelBehavior):
        await testee.judge_answer_relevancy(agent, question="q", answer="a")
