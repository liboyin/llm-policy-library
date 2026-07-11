"""Planner Agent: decompose a policy question into searches.

The Planner is the only agent whose output shape is enforced by the model itself
— it answers with a `QueryPlan` JSON schema (Azure OpenAI structured outputs)
rather than prose that would have to be parsed. Together with a pinned
deployment version and the lowest reasoning effort the model accepts, that is
what "deterministic configuration" means here: the deployable Azure OpenAI chat
models are reasoning models and reject `temperature`, `top_p`, and `seed`
outright, so the plan's *shape* is guaranteed even though its wording is not.

Two invariants are enforced in code rather than trusted to the model:

* `original_query` is overwritten with the user's verbatim question. A model
  that paraphrases it would silently change what the audit trail says was asked.
* The plan is clamped to `MAX_PLAN_STEPS`. Every extra step is a search and an
  embedding call against the p90 latency budget, and an over-eager plan is a far
  more likely failure than a malformed one.
"""

import logging
from typing import Any, Final

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient, OpenAIChatOptions
from pydantic import ValidationError

from llm_policy_library.config import ReasoningEffort
from llm_policy_library.models import PlanStep, QueryPlan

logger = logging.getLogger(__name__)

# One step answers most questions; three covers a question spanning several
# control families ("access control *and* logging"). Beyond that the steps start
# retrieving the same controls while still costing a round trip each.
MAX_PLAN_STEPS: Final = 3

PLANNER_INSTRUCTIONS: Final = f"""\
You plan searches over a corpus of NIST SP 800-53 Rev 5 security control \
statements, held in an Azure AI Search index. Each document is one control or \
control enhancement: its ID (such as AC-2 or AC-2.1), title, family, and the \
text of its requirement.

Decompose the user's question into 1 to {MAX_PLAN_STEPS} search steps. Use one \
step unless the question genuinely spans separate topics; add a step only when \
it would surface controls the other steps would miss.

Each `search_query` is sent to that index, not to a web search engine. The index \
is searched semantically, so write a short natural-language phrase naming the \
security topic, the way a control statement would describe the requirement. A \
control ID on its own is also a good query. Do not pile up synonyms: a long \
keyword list scores measurably worse than a focused phrase. Never use \
search-engine operators such as `site:`, quotes, `OR`, or `AND`.

Each `purpose` states in one sentence what the step is meant to find.
"""

# The Planner is the only agent that constrains the model's output shape, so it
# is the only one whose options carry a `response_format`.
PlannerOptions = OpenAIChatOptions[QueryPlan]
PlannerAgent = Agent[PlannerOptions]


class PlannerError(RuntimeError):
    """Raised when the chat model returns no usable plan."""


def build_planner(
    chat_client: OpenAIChatClient[PlannerOptions], reasoning_effort: ReasoningEffort
) -> PlannerAgent:
    """Construct the Planner Agent over an Azure OpenAI chat client.

    Args:
        chat_client: Client bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.

    Returns:
        The configured agent.
    """
    # agent-framework's `ReasoningOptions.effort` literal omits "minimal", which
    # is the lowest effort gpt-5-mini accepts (it rejects "none" with a 400).
    # Typing the value loosely beats vendoring a literal that is missing a member.
    reasoning: Any = {"effort": reasoning_effort}
    options: PlannerOptions = {"response_format": QueryPlan, "reasoning": reasoning}
    return Agent(chat_client, PLANNER_INSTRUCTIONS, name="planner", default_options=options)


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
        PlannerError: If the model returned nothing, an unparseable plan, or a
            plan with no usable steps. None of these is retryable in place: the
            caller surfaces the failure rather than answering from an empty plan.
    """
    response = await agent.run(query)
    try:
        planned = response.value
    except ValidationError as error:
        raise PlannerError(f"planner returned a plan that failed validation: {error}") from error
    if planned is None:
        raise PlannerError("planner returned no structured plan")
    usable = usable_steps(planned.steps)
    if not usable:
        raise PlannerError("planner returned a plan with no steps carrying a search query")

    if len(usable) > MAX_PLAN_STEPS:
        # The instructions ask for at most MAX_PLAN_STEPS. Exceeding them is a
        # signal that the prompt or the model has drifted, the same class of
        # event as the Response Agent citing a control it was never given.
        logger.warning(
            "planner exceeded the step limit",
            extra={"query": query, "planned_steps": len(usable), "limit": MAX_PLAN_STEPS},
        )

    plan = QueryPlan(original_query=query, steps=clamp_steps(usable))
    logger.info(
        "query planned",
        extra={
            "query": query,
            "planned_steps": len(planned.steps),
            "kept_steps": len(plan.steps),
            "search_queries": [step.search_query for step in plan.steps],
        },
    )
    return plan
