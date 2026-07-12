"""LLM-judge agents for the evaluation harness: faithfulness and answer relevancy.

Each judge is a small PydanticAI agent over the same chat deployment the
pipeline uses, replacing the `azure-ai-evaluation` Groundedness/Relevance
evaluators without pulling in a second LLM framework. Both answer with a
`JudgeVerdict` JSON schema — a one-sentence reason and an integer score — so a
malformed verdict is a validation failure PydanticAI retries, never a prose
blob the harness would have to parse.

The two judges deliberately score different things and see different evidence:

* **Faithfulness** sees the question, the answer, and the exact control texts
  the Response Agent was given, and scores only whether the answer's claims are
  supported by that text. It is the LLM complement to the harness's exact
  citation check: the check catches an invented control ID, the judge catches a
  claim the cited controls do not actually make.
* **Answer relevancy** sees the question and the answer only — no controls —
  and scores only whether the answer addresses what was asked. Withholding the
  context is intentional: a judge shown the evidence starts scoring grounding
  again, and the two metrics collapse into one.

Both keep the 1-5 integer scale of the evaluators they replace, so reports stay
comparable with the old groundedness/relevance columns.
"""

import logging

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings

from llm_policy_library.config import ReasoningEffort
from llm_policy_library.prompts import get_prompt

logger = logging.getLogger(__name__)


class JudgeVerdict(BaseModel):
    """One judge's structured verdict on one answer.

    This is the judges' `response_format` JSON schema, so the field descriptions
    are prompt surface the model reads. `reasoning` is declared before `score`
    on purpose: the model emits fields in schema order, so it must commit to a
    reason before it picks the number, not rationalise a number it already chose.

    Attributes:
        reasoning: The judge's one-sentence justification for the score.
        score: The verdict on the 1-5 scale defined in the judge's instructions.
    """

    model_config = ConfigDict(frozen=True)

    reasoning: str = Field(description="One sentence justifying the score.")
    score: int = Field(
        ge=1,
        le=5,
        description="The integer score from 1 (worst) to 5 (best), per the instructions.",
    )


# Both judges constrain the model's output shape to a `JudgeVerdict`; they
# differ only in instructions and in what evidence their prompt carries.
JudgeAgent = Agent[None, JudgeVerdict]


def _build_judge(
    model: OpenAIChatModel, reasoning_effort: ReasoningEffort, instructions_key: str, name: str
) -> JudgeAgent:
    """Construct one judge agent over a PydanticAI chat model.

    Args:
        model: Model bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.
        instructions_key: Prompt-store key of the judge's instructions.
        name: The agent's name, for logs and traces.

    Returns:
        The configured agent.
    """
    return Agent(
        model,
        instructions=get_prompt(instructions_key),
        # `NativeOutput` selects the provider's native structured outputs, as in
        # `planner.build_planner`; the default mode would wrap the schema in a
        # synthetic tool call.
        output_type=NativeOutput(JudgeVerdict),
        # See `planner.build_planner` on why the key must be exactly
        # `openai_reasoning_effort`: PydanticAI silently ignores unknown keys.
        model_settings=OpenAIChatModelSettings(openai_reasoning_effort=reasoning_effort),
        name=name,
    )


def build_faithfulness_judge(
    model: OpenAIChatModel, reasoning_effort: ReasoningEffort
) -> JudgeAgent:
    """Construct the faithfulness (groundedness) judge.

    Args:
        model: Model bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.

    Returns:
        The configured agent.
    """
    return _build_judge(model, reasoning_effort, "faithfulness_judge_instructions", "faithfulness")


def build_answer_relevancy_judge(
    model: OpenAIChatModel, reasoning_effort: ReasoningEffort
) -> JudgeAgent:
    """Construct the answer-relevancy judge.

    Args:
        model: Model bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.

    Returns:
        The configured agent.
    """
    return _build_judge(
        model, reasoning_effort, "answer_relevancy_judge_instructions", "answer_relevancy"
    )


async def judge_faithfulness(
    agent: JudgeAgent, *, question: str, answer: str, context: str
) -> int:
    """Score how faithfully an answer sticks to the retrieved control texts.

    Failures propagate: the caller (the evaluation harness) owns the retry
    policy and the record-None-on-failure semantics, so wrapping errors here
    would double-handle them.

    Args:
        agent: The faithfulness judge.
        question: The user's question.
        answer: The pipeline's answer prose.
        context: The control texts the answer was allowed to use, as rendered
            by `agents.response.format_documents` — the judge grounds against
            exactly what the Response Agent saw.

    Returns:
        The integer 1-5 faithfulness score.
    """
    prompt = get_prompt(
        "faithfulness_judge_prompt", question=question, answer=answer, context=context
    )
    verdict = (await agent.run(prompt)).output
    logger.info(
        "faithfulness judged",
        extra={"question": question, "score": verdict.score, "reasoning": verdict.reasoning},
    )
    return verdict.score


async def judge_answer_relevancy(agent: JudgeAgent, *, question: str, answer: str) -> int:
    """Score how well an answer addresses the question that was asked.

    Failures propagate; see `judge_faithfulness`.

    Args:
        agent: The answer-relevancy judge.
        question: The user's question.
        answer: The pipeline's answer prose.

    Returns:
        The integer 1-5 answer-relevancy score.
    """
    prompt = get_prompt("answer_relevancy_judge_prompt", question=question, answer=answer)
    verdict = (await agent.run(prompt)).output
    logger.info(
        "answer relevancy judged",
        extra={"question": question, "score": verdict.score, "reasoning": verdict.reasoning},
    )
    return verdict.score
