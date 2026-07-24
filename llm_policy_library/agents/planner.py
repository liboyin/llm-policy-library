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

The corpus map (`PLANNER_CORPUS_MAP`) is what stops the Planner planning blind.
With it, the instructions list all twenty control families and the model can say
that *no* family covers the question at all — which turns the safe fallback from
an accident of retrieval (searching an off-topic question and happening to find
nothing above the floor) into a structural refusal that never touches the index.

The map was built to buy a second thing as well: a `category` naming the family
a step should be filtered to. **That half is measured and switched off.** Over
three eval runs per arm on 2026-07-24 the model filtered 91% of steps, and those
steps lost recall — -0.093 exact-ID and -0.071 base-family against the unfiltered
baseline — enough to fail this phase's own acceptance gate, because restricting a
search to one family fills the top-k with that family's *enhancements* instead of
the base controls, and leaves a question spanning several families unable to
reach the rest. The instructions now ask for null, and `plan_query` clears the
field regardless; `retrieve_step` keeps the filter it was built and probed for,
against the Phase 11 tier-2 decision. See TODO.md Phase 10 Commit 3.

Both answers are treated as proposals. The category is always cleared, and the
out-of-domain flag is discarded as well when the map is off, because a model
asked to pick from a list it was never shown will still pick
something: measured 2026-07-22, it proposed the families "Access Control (AC)"
and "SC", neither of which the catalog uses. Discarding rather than trusting is
what makes the flag an A/B lever: with the map off, this module *plans and
refuses* exactly as it did before the map existed, and a broken or missing
`corpus_map.json` cannot affect that arm because it is never read there. A model
that refuses by planning *nothing* is degraded the same way — the question is
searched as asked (`question_as_step`), which is what the pre-map system did
with an off-topic question — so discarding the model's judgement can never cost
the caller their answer.

What the flag does **not** revert is the request's JSON schema, which
`PlannerOutput` fixes for both arms. The model is asked for `category` and
`out_of_domain` either way and their descriptions are prompt surface either way
— which is why both are worded to hold when no family list is present. Measured
2026-07-22 on identical instruction text, the schema alone moves a planner call
from 392 to 505 input tokens. So the map-off arm is the right control for "does
the map help", but it is not a token-for-token replay of the pre-map system, and
Phase 10's capacity arithmetic must take its planner-input baseline from a
measured map-off run rather than from the pre-map figure.
"""

import logging
from collections.abc import Collection
from typing import Any, Final

from agent_framework import Agent, UsageDetails
from agent_framework.openai import OpenAIChatClient, OpenAIChatOptions
from pydantic import ValidationError

from llm_policy_library.config import ReasoningEffort
from llm_policy_library.corpus_map import render_map
from llm_policy_library.models import PlannerOutput, PlanStep, QueryPlan
from llm_policy_library.prompts import get_prompt

logger = logging.getLogger(__name__)

# One step answers most questions; three covers a question spanning several
# control families ("access control *and* logging"). Beyond that the steps start
# retrieving the same controls while still costing a round trip each.
MAX_PLAN_STEPS: Final = 3

# The Planner is the only agent that constrains the model's output shape, so it
# is the only one whose options carry a `response_format` — and that schema is
# `PlannerOutput`, the searches alone, not the full `QueryPlan`.
PlannerOptions = OpenAIChatOptions[PlannerOutput]
PlannerAgent = Agent[PlannerOptions]


class PlannerError(RuntimeError):
    """Raised when the model's answer leaves nothing that can be planned at all.

    Not raised merely because the model planned no usable step: that degrades to
    searching the question as asked (`question_as_step`). It fires when the model
    returned nothing or something unparseable, or when the substitute step would
    itself be blank because the question is.
    """


def build_planner(
    chat_client: OpenAIChatClient[PlannerOptions],
    reasoning_effort: ReasoningEffort,
    corpus_map: bool,
) -> PlannerAgent:
    """Construct the Planner Agent over an Azure OpenAI chat client.

    Args:
        chat_client: Client bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.
        corpus_map: Whether to append the corpus map and its routing rules to the
            instructions. When false the rendered instructions are byte-identical
            to the pre-map ones, which is what makes the flag an A/B lever rather
            than a rewording.

    Returns:
        The configured agent.
    """
    # agent-framework's `ReasoningOptions.effort` literal omits "minimal", which
    # is the lowest effort gpt-5-mini accepts (it rejects "none" with a 400).
    # Typing the value loosely beats vendoring a literal that is missing a member.
    reasoning: Any = {"effort": reasoning_effort}
    options: PlannerOptions = {"response_format": PlannerOutput, "reasoning": reasoning}
    # The block carries its own leading blank line rather than the prompt file
    # carrying a trailing one: `planner_instructions` must render unchanged when
    # this is empty, down to the last byte.
    map_block = (
        "\n\n" + get_prompt("planner_corpus_map_block", families=render_map())
        if corpus_map
        else ""
    )
    instructions = get_prompt(
        "planner_instructions", max_plan_steps=MAX_PLAN_STEPS, corpus_map=map_block
    )
    return Agent(chat_client, instructions, name="planner", default_options=options)


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


def validated_categories(
    steps: list[PlanStep], known: Collection[str]
) -> tuple[list[PlanStep], list[str]]:
    """Clear every step category that does not name a family in `known`.

    A category becomes an OData filter, so an unknown one would either surface as
    an opaque Azure 400 or — worse, since the name is only ever wrong by a word —
    silently filter the search down to nothing. Clearing it degrades the step to
    an unfiltered search, which is what the step would have been without a map.

    Args:
        steps: The steps the model returned.
        known: The family names a category may name. An empty collection clears
            every category, which is what every caller passes today — the
            filtering half of the corpus map is measured off (see the module
            docstring). It stays a parameter because re-enabling filtering, for
            the Phase 11 tier-2 work, means passing `corpus_map.family_names()`
            here and nothing else.

    Returns:
        The steps with unusable categories cleared, and the cleared names in step
        order.
    """
    checked: list[PlanStep] = []
    cleared: list[str] = []
    for step in steps:
        if step.category is None or step.category in known:
            checked.append(step)
            continue
        cleared.append(step.category)
        checked.append(step.model_copy(update={"category": None}))
    return checked, cleared


def question_as_step(query: str) -> PlanStep:
    """Build the one step to run when the model planned nothing usable.

    A model that returns no usable step has refused to plan rather than failed.
    Measured live 2026-07-24: asked an out-of-domain question with the corpus map
    off, `gpt-5-mini` set `out_of_domain` and omitted steps entirely in 1 of 6
    direct probe calls — a small sample, and a sufficient one: the same behaviour
    aborted a live evaluation run. That judgement is discarded on this path, since
    the model was shown no family list to judge against, which used to leave
    nothing to search and raised `PlannerError` — surfacing an ordinary off-topic
    question to the caller as an HTTP 502.

    Searching the question as asked is what the pre-map system did with such a
    question, every time: the relevance floor then rejects what comes back and
    the safe fallback is served on the evidence of a search, not on the model's
    unverified say-so.

    Args:
        query: The user's question.

    Returns:
        A single unfiltered step searching the question verbatim.

    Raises:
        PlannerError: If the question is itself blank, since the substitute step
            would then carry a blank search query into the embeddings API — the
            opaque HTTP 400 `usable_steps` exists to prevent.
    """
    if not query.strip():
        raise PlannerError("planner returned no usable step, and the question is blank")
    return PlanStep(
        search_query=query,
        purpose="Search the question as asked: the planner returned no usable step.",
    )


def clamp_steps(steps: list[PlanStep], limit: int = MAX_PLAN_STEPS) -> list[PlanStep]:
    """Drop any steps the model planned beyond the allowed count.

    Args:
        steps: The steps the model returned, in its own order.
        limit: The most steps to keep.

    Returns:
        The first `limit` steps.
    """
    return steps[:limit]


async def plan_query(agent: PlannerAgent, query: str, corpus_map: bool) -> QueryPlan:
    """Decompose a user question into at most `MAX_PLAN_STEPS` searches.

    Args:
        agent: The Planner Agent.
        query: The user's question.
        corpus_map: Whether the agent was built with the corpus map. It gates the
            prompt and the `out_of_domain` verdict: without the map the model has
            no family list to judge the question against, so its refusal is
            discarded and the plan is exactly what it would have been before the
            map existed. It does **not** gate `category`, which is cleared on
            every path in either setting (see the module docstring).

    Returns:
        The plan, with `original_query` set to `query` verbatim. A plan marked
        out of domain carries no steps, and is the one stepless plan that is not
        an error. On any other path the plan carries at least one step: a model
        that planned nothing usable degrades to searching the question as asked
        (`question_as_step`) rather than failing the request.

    Raises:
        PlannerError: If the model returned nothing, an unparseable plan, or no
            usable step for a question that is itself blank. None of these is
            retryable in place: the caller surfaces the failure rather than
            answering from an empty plan.
    """
    response = await agent.run(query)
    try:
        planned = response.value
    except ValidationError as error:
        raise PlannerError(f"planner returned a plan that failed validation: {error}") from error
    if planned is None:
        raise PlannerError("planner returned no structured plan")

    out_of_domain = corpus_map and planned.out_of_domain
    if out_of_domain:
        # Checked before the no-usable-steps guard below, which would otherwise
        # reject the very plan this feature exists to produce: "no family covers
        # this" is a complete answer, and it has nothing to search for.
        steps: list[PlanStep] = []
        if planned.steps:
            # The flag wins, but not quietly. A model that says both things at
            # once has misread the instructions, which is the same class of drift
            # signal as exceeding the step limit.
            logger.warning(
                "planner declared the question out of domain but planned steps anyway",
                extra={"query": query, "discarded_steps": len(planned.steps)},
            )
    else:
        usable = usable_steps(planned.steps)
        if not usable:
            # A refusal this path does not trust, not a failure: degrade to
            # searching the question rather than raising (see `question_as_step`).
            usable = [question_as_step(query)]
            logger.warning(
                "planner returned no usable step; searching the question as asked",
                extra={"query": query, "planned_steps": len(planned.steps)},
            )

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

        # Clamped once, above the branch: the limit is not a property of either
        # arm, and a second call site is a second place for it to go missing from.
        capped = clamp_steps(usable)
        # Every category is cleared, in both arms, so every search runs unfiltered.
        # This is a measured decision, not an oversight: over three eval runs per
        # arm (2026-07-24) the map's *filtering* half cost recall on the twelve
        # queries it filtered — -0.093 exact-ID and -0.071 base-family — and failed
        # the phase's own acceptance gate, while its out-of-domain half passed
        # cleanly. Restricting a search to one family does not surface that
        # family's base controls; it fills the top-k with that family's
        # enhancements, and a question spanning several families can no longer
        # reach the others at all. `retrieve_step` keeps the filter it was built
        # and probed for, against the Phase 11 tier-2 decision — nothing on the
        # serving path sets one today. See TODO.md Phase 10 Commit 3.
        steps, proposed = validated_categories(capped, ())
        if corpus_map and proposed:
            # Drift only when the model was told what to do: the map block asks
            # for null outright, so a category here means the instruction missed.
            # With the map off the model was told nothing, and guessing is
            # expected — warning there would fire on ordinary baseline traffic.
            logger.warning(
                "planner set a category although the instructions ask for none",
                extra={"query": query, "discarded_categories": proposed},
            )

    plan = QueryPlan(original_query=query, steps=steps, out_of_domain=out_of_domain)
    usage = response.usage_details or UsageDetails()
    logger.info(
        "query planned",
        extra={
            "query": query,
            "planned_steps": len(planned.steps),
            "kept_steps": len(plan.steps),
            # Always a bool, on every plan: a reader counting refusals must be
            # able to tell "the corpus covers nothing here" from a retrieval that
            # merely came back empty, and only this line records the difference.
            "out_of_domain": plan.out_of_domain,
            # The chat tokens this stage really spent, from the usage the Agent
            # Framework surfaces as `usage_details`. Capacity planning needs
            # tokens per request measured, not estimated: the Azure OpenAI TPM
            # quota is what a concurrent workload exhausts first, and the audit
            # trail is the only place the real number exists.
            "input_tokens": usage.get("input_token_count") or 0,
            "output_tokens": usage.get("output_token_count") or 0,
            # Every field of each kept step: the query drives retrieval, the
            # purpose is the model's stated reason for it — the only record of
            # *why* a search ran, which `PlanStep.purpose` exists to preserve —
            # and the category is the filter the search really ran under, which
            # today is always null because filtering is disabled. It is logged
            # anyway: it records what was *executed* rather than what the model
            # proposed, so a reader can tell an unfiltered search from a filtered
            # one without knowing which release they are looking at.
            "steps": [
                {
                    "search_query": step.search_query,
                    "purpose": step.purpose,
                    "category": step.category,
                }
                for step in plan.steps
            ],
        },
    )
    return plan
