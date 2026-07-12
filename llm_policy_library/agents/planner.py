"""Planner Agent: decompose a policy question into searches.

The Planner is the only agent whose output shape is enforced by the model itself
— it answers with a `PlannerOutput` JSON schema (Azure OpenAI structured outputs)
rather than prose that would have to be parsed. Together with a pinned
deployment version and the lowest reasoning effort the model accepts, that is
what "deterministic configuration" means here: the deployable Azure OpenAI chat
models are reasoning models and reject `temperature`, `top_p`, and `seed`
outright, so the plan's *shape* is guaranteed even though its wording is not.

The model returns only the searches; the Planner supplies the question itself
when it builds the `QueryPlan`. That is both cheaper — the model spends no output
tokens echoing a question the Planner already has — and safer, since there is no
model-written copy of the query that could paraphrase what the audit trail says
was asked. The one thing the model can still overdo is the step count, so the
plan is clamped to `MAX_PLAN_STEPS`: every extra step is a search and an
embedding call against the p90 latency budget.
"""

import logging
from typing import Final

from pydantic_ai import Agent, NativeOutput, UnexpectedModelBehavior
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings

from llm_policy_library.config import ReasoningEffort
from llm_policy_library.models import PlannerOutput, PlanStep, QueryPlan
from llm_policy_library.prompts import get_prompt

logger = logging.getLogger(__name__)

# One step answers most questions; three covers a question spanning several
# control families ("access control *and* logging"). Beyond that the steps start
# retrieving the same controls while still costing a round trip each.
MAX_PLAN_STEPS: Final = 3

# The Planner is the only agent that constrains the model's output shape — its
# `output_type` is `PlannerOutput`, the searches alone, not the full `QueryPlan`.
PlannerAgent = Agent[None, PlannerOutput]


class PlannerError(RuntimeError):
    """Raised when the chat model returns no usable plan."""


def build_planner(model: OpenAIChatModel, reasoning_effort: ReasoningEffort) -> PlannerAgent:
    """Construct the Planner Agent over a PydanticAI chat model.

    Args:
        model: Model bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.

    Returns:
        The configured agent.
    """
    instructions = get_prompt("planner_instructions", max_plan_steps=MAX_PLAN_STEPS)
    return Agent(
        model,
        instructions=instructions,
        # `NativeOutput` selects the provider's native structured outputs
        # (`response_format` with the JSON schema), the enforcement the module
        # docstring promises. PydanticAI's default mode would instead wrap the
        # schema in a synthetic tool call.
        output_type=NativeOutput(PlannerOutput),
        # `openai_reasoning_effort`, not `reasoning_effort`: PydanticAI ignores
        # settings keys it does not know, so the misnamed key would silently
        # run every call at the model's default effort.
        model_settings=OpenAIChatModelSettings(openai_reasoning_effort=reasoning_effort),
        name="planner",
    )


def usable_steps(steps: list[PlanStep]) -> list[PlanStep]:
    """Drop steps whose search query is blank.

    An empty search query cannot be embedded — the embeddings API rejects it —
    so it would surface as an opaque HTTP 400 from inside retrieval rather than
    as the planning failure it is.

    Args:
        steps: The steps the model returned.

    Returns:
        The steps carrying a non-blank search query, in the model's order.
    """
    return [step for step in steps if step.search_query.strip()]


def clamp_steps(steps: list[PlanStep], limit: int = MAX_PLAN_STEPS) -> list[PlanStep]:
    """Drop any steps the model planned beyond the allowed count.

    Args:
        steps: The steps the model returned, in its own order.
        limit: The most steps to keep.

    Returns:
        The first `limit` steps.
    """
    return steps[:limit]


async def plan_query(agent: PlannerAgent, query: str) -> QueryPlan:
    """Decompose a user question into at most `MAX_PLAN_STEPS` searches.

    Args:
        agent: The Planner Agent.
        query: The user's question.

    Returns:
        The plan, with `original_query` set to `query` verbatim.

    Raises:
        PlannerError: If the model could not produce a plan matching the schema
            (PydanticAI raises `UnexpectedModelBehavior` once its own output
            retries are exhausted), or if the plan has no usable steps. Neither
            is retryable in place: the caller surfaces the failure rather than
            answering from an empty plan.
    """
    try:
        response = await agent.run(query)
    except UnexpectedModelBehavior as error:
        raise PlannerError(f"planner failed to produce a valid plan: {error}") from error
    planned = response.output
    usable = usable_steps(planned.steps)
    if not usable:
        raise PlannerError("planner returned a plan with no steps carrying a search query")

    if len(usable) > MAX_PLAN_STEPS:
        # The instructions ask for at most MAX_PLAN_STEPS. Exceeding them is a
        # signal that the prompt or the model has drifted, the same class of
        # event as the Response Agent citing a control it was never given.
        logger.warning(
            "planner exceeded the step limit",
            # `usable_steps`, not `planned_steps`: this counts the steps carrying
            # a query, whereas the "query planned" line's `planned_steps` is the
            # raw model count. Distinct quantities must not share a log key.
            extra={"query": query, "usable_steps": len(usable), "limit": MAX_PLAN_STEPS},
        )

    plan = QueryPlan(original_query=query, steps=clamp_steps(usable))
    logger.info(
        "query planned",
        extra={
            "query": query,
            "planned_steps": len(planned.steps),
            "kept_steps": len(plan.steps),
            # Both fields of each kept step: the query drives retrieval, and the
            # purpose is the model's stated reason for it — the only record of
            # *why* a search ran, which `PlanStep.purpose` exists to preserve.
            "steps": [
                {"search_query": step.search_query, "purpose": step.purpose}
                for step in plan.steps
            ],
        },
    )
    return plan
