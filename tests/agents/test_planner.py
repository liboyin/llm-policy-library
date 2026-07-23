"""Unit tests for `llm_policy_library.agents.planner`."""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, ValidationError

import llm_policy_library.agents.planner as testee
from llm_policy_library.corpus_map import family_names, render_map
from llm_policy_library.models import PlannerOutput, PlanStep
from llm_policy_library.prompts import get_prompt


def make_step(search_query: str, category: str | None = None) -> PlanStep:
    """Build a plan step.

    Args:
        search_query: The step's search query.
        category: The control family the step names, if any.

    Returns:
        The step.
    """
    return PlanStep(search_query=search_query, purpose=f"find {search_query}", category=category)


def make_output(*steps: PlanStep, out_of_domain: bool = False) -> PlannerOutput:
    """Build the model's structured output.

    Args:
        *steps: The searches the model proposed.
        out_of_domain: The model's judgement that no family covers the question.

    Returns:
        The output.
    """
    return PlannerOutput(steps=list(steps), out_of_domain=out_of_domain)


def planner_returning(value: Any, input_tokens: int = 800, output_tokens: int = 50) -> MagicMock:
    """Stub a Planner Agent whose structured output is `value`.

    Args:
        value: What `AgentResponse.value` yields, or an exception to raise.
        input_tokens: The prompt tokens the run billed.
        output_tokens: The completion tokens the run billed.

    Returns:
        The stub agent.
    """
    response = MagicMock()
    if isinstance(value, Exception):
        type(response).value = property(lambda _: (_ for _ in ()).throw(value))
    else:
        response.value = value
    # A real `UsageDetails` mapping, not a MagicMock attribute: `plan_query` logs
    # these as the measured token cost, and a MagicMock would let a non-int reach
    # the audit trail without any test noticing.
    response.usage_details = {
        "input_token_count": input_tokens,
        "output_token_count": output_tokens,
    }
    agent = MagicMock()
    agent.run = AsyncMock(return_value=response)
    return agent


def rendered_instructions(corpus_map: bool) -> str:
    """Return the system prompt a planner built with this flag would send.

    Args:
        corpus_map: Whether to build the agent with the corpus map.

    Returns:
        The instructions, read back from where the Agent Framework keeps them.
    """
    agent = testee.build_planner(MagicMock(), "minimal", corpus_map)
    return str(agent.default_options["instructions"])


def test_build_planner_requests_the_searches_only_schema_and_configured_effort() -> None:
    """The model's schema is PlannerOutput (searches only), so it never echoes the question."""
    chat_client = MagicMock()

    agent = testee.build_planner(chat_client, "minimal", False)

    options = agent.default_options
    assert options["response_format"] is PlannerOutput
    assert options["reasoning"] == {"effort": "minimal"}


def test_build_planner_without_the_map_renders_the_pre_map_instructions_byte_for_byte() -> None:
    """The map-off arm's prompt must still be the pre-map prompt, to the byte."""
    # Pinned as a literal, copied from `git show 99491dc:llm_policy_library/prompts.json`
    # (the commit before the corpus map was wired in). Deriving it from `get_prompt`
    # instead would compare the implementation with itself: editing
    # `planner_instructions` would then change both sides and the test would pass
    # while the A/B lever it guards had silently moved. This is the baseline arm of
    # the Phase 10 comparison, so drift here does not fail the run -- it quietly
    # changes what D12's gate is measuring against.
    pre_map = (
        "You plan searches over a corpus of NIST SP 800-53 Rev 5 security control "
        "statements, held in an Azure AI Search index. Each document is one control or "
        "control enhancement: its ID (such as AC-2 or AC-2.1), title, family, and the "
        "text of its requirement.\n\nDecompose the user's question into 1 to 3 search "
        "steps. Use one step unless the question genuinely spans separate topics; add a "
        "step only when it would surface controls the other steps would miss.\n\nEach "
        "`search_query` is sent to that index, not to a web search engine. The index is "
        "searched semantically, so write a short natural-language phrase naming the "
        "security topic, the way a control statement would describe the requirement. A "
        "control ID on its own is also a good query. Do not pile up synonyms: a long "
        "keyword list scores measurably worse than a focused phrase. Never use "
        "search-engine operators such as `site:`, quotes, `OR`, or `AND`.\n\nEach "
        "`purpose` states in one sentence what the step is meant to find."
    )

    assert rendered_instructions(corpus_map=False) == pre_map


def test_build_planner_interpolates_every_placeholder_in_both_arms() -> None:
    """A surviving `{...}` would reach the model as literal template syntax."""
    for corpus_map in (False, True):
        assert "{" not in rendered_instructions(corpus_map=corpus_map)


def test_build_planner_separates_the_map_block_from_the_instructions_by_a_blank_line() -> None:
    """The map must read as its own section, not run on from the last instruction."""
    # The junction is built in Python, not in the prompt file, precisely so the
    # map-off rendering can end at the pre-map final byte -- so it is asserted here.
    instructions = rendered_instructions(corpus_map=True)
    tail = "Each `purpose` states in one sentence what the step is meant to find."
    block = get_prompt("planner_corpus_map_block", families=render_map())

    assert instructions == f"{rendered_instructions(corpus_map=False)}\n\n{block}"
    assert f"{tail}\n\n" in instructions


def test_build_planner_with_the_map_shows_the_model_every_family_it_may_name() -> None:
    """Every family the model may filter on must appear in the prompt."""
    # A family missing here is one the model cannot route to and will not filter
    # on, so a truncated list silently loses coverage of that slice of the corpus.
    instructions = rendered_instructions(corpus_map=True)

    for family in family_names():
        assert family in instructions, f"{family} is filterable but was never shown"
    assert render_map() in instructions


def test_build_planner_with_the_map_states_the_rules_for_both_new_fields() -> None:
    """The map block must state the rules for both new fields, not just list names."""
    # A list of names alone never tells the model it may filter, may decline to
    # filter, or may refuse -- which is the entire behavior this phase buys.
    instructions = rendered_instructions(corpus_map=True)

    assert "`category`" in instructions
    assert "`out_of_domain`" in instructions


def test_planner_instructions_forbid_web_search_operators() -> None:
    """The search text hits an Azure AI Search index; `site:` syntax retrieves nothing."""
    instructions = get_prompt(
        "planner_instructions", max_plan_steps=testee.MAX_PLAN_STEPS, corpus_map=""
    )
    assert "site:" in instructions
    assert "Never use search-engine operators" in instructions


def test_usable_steps_drops_a_step_with_a_blank_search_query() -> None:
    """An empty query cannot be embedded; retrieval would fail with an opaque HTTP 400."""
    steps = [make_step("access control"), PlanStep(search_query="   ", purpose="p")]

    kept = testee.usable_steps(steps)

    assert [step.search_query for step in kept] == ["access control"]


def test_validated_categories_keeps_a_category_naming_a_real_family() -> None:
    """The whole point of the map: a family the catalog holds must survive into the filter."""
    steps = [make_step("account management", "Access Control")]

    checked, cleared = testee.validated_categories(steps, ["Access Control", "Media Protection"])

    assert [step.category for step in checked] == ["Access Control"]
    assert cleared == []


def test_validated_categories_clears_a_category_naming_no_family_and_reports_it() -> None:
    """An unknown family must degrade to an unfiltered search, and be reported."""
    # Measured 2026-07-22: a filter matching no document returns 0 rows with no
    # error, so an unknown family is a silent zero-result step rather than a loud
    # failure. Clearing it costs one unfiltered search; keeping it costs the answer.
    steps = [make_step("data in transit", "Access Control (AC)")]

    checked, cleared = testee.validated_categories(steps, ["Access Control"])

    assert [step.category for step in checked] == [None]
    assert cleared == ["Access Control (AC)"]
    assert checked[0].search_query == "data in transit", "only the category may be cleared"


def test_validated_categories_clears_every_category_when_no_family_is_known() -> None:
    """An empty vocabulary must clear every category, however real the name looks."""
    # This is the map-off arm's contract. If a guessed-but-real family survived
    # here, the baseline would silently inherit the feature and the Phase 10 A/B
    # would be comparing the map against itself.
    steps = [make_step("q", "Access Control"), make_step("r", "Media Protection")]

    checked, cleared = testee.validated_categories(steps, ())

    assert [step.category for step in checked] == [None, None]
    assert cleared == ["Access Control", "Media Protection"]


def test_validated_categories_leaves_an_absent_category_absent_and_unreported() -> None:
    """Null is the model declining to filter, not a mistake; reporting it would cry wolf."""
    checked, cleared = testee.validated_categories([make_step("q")], ["Access Control"])

    assert [step.category for step in checked] == [None]
    assert cleared == []


def test_validated_categories_rejects_a_family_name_differing_only_in_case() -> None:
    """Matching must stay exact, not case-folded."""
    # Measured 2026-07-22: Azure's `category eq '...'` is case-SENSITIVE, and a
    # literal that matches nothing returns 0 rows with no error. So a lenient match
    # here would not be a harmless kindness -- it would send a filter that silently
    # retrieves nothing, which is precisely the failure this function exists to stop.
    checked, cleared = testee.validated_categories(
        [make_step("q", "access control")], ["Access Control"]
    )

    assert [step.category for step in checked] == [None]
    assert cleared == ["access control"]


def test_clamp_steps_drops_steps_beyond_the_limit() -> None:
    """Every extra step costs an embedding and a search against the latency budget."""
    steps = [make_step(f"q{index}") for index in range(5)]

    kept = testee.clamp_steps(steps, limit=3)

    assert [step.search_query for step in kept] == ["q0", "q1", "q2"]


def test_clamp_steps_keeps_a_plan_already_within_the_limit() -> None:
    """A one-step plan is the common case and must pass through untouched."""
    steps = [make_step("access control")]

    assert testee.clamp_steps(steps, limit=3) == steps


async def test_plan_query_sets_original_query_to_the_true_input() -> None:
    """The model never returns the question, so the Planner sets it from the real input."""
    agent = planner_returning(make_output(make_step("q")))

    plan = await testee.plan_query(agent, "What controls apply to API security?", False)

    assert plan.original_query == "What controls apply to API security?"


async def test_plan_query_clamps_an_over_eager_plan() -> None:
    """The model is asked for 1-3 steps; the limit is enforced here, not trusted to it."""
    agent = planner_returning(make_output(*(make_step(f"q{index}") for index in range(6))))

    plan = await testee.plan_query(agent, "q", False)

    assert len(plan.steps) == testee.MAX_PLAN_STEPS


async def test_plan_query_logs_each_step_with_its_purpose(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`PlanStep.purpose` is recorded for the audit trail, so it must reach the log line."""
    agent = planner_returning(
        make_output(PlanStep(search_query="access control", purpose="find the AC family"))
    )

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.plan_query(agent, "q", False)

    record = next(record for record in caplog.records if record.message == "query planned")
    assert getattr(record, "steps") == [
        {"search_query": "access control", "purpose": "find the AC family", "category": None}
    ]


async def test_plan_query_passes_the_users_question_to_the_agent() -> None:
    """The Planner reasons about the question itself, not a preprocessed form of it."""
    agent = planner_returning(make_output(make_step("q")))

    await testee.plan_query(agent, "How is sensitive data protected?", False)

    agent.run.assert_awaited_once_with("How is sensitive data protected?")


async def test_plan_query_raises_when_the_model_returns_no_plan() -> None:
    """Answering from an absent plan would search on nothing and ground on nothing."""
    agent = planner_returning(None)

    with pytest.raises(testee.PlannerError, match="no structured plan"):
        await testee.plan_query(agent, "q", False)


async def test_plan_query_raises_when_the_plan_has_no_steps() -> None:
    """A stepless plan retrieves nothing, which would silently look like a safe fallback."""
    agent = planner_returning(make_output())

    with pytest.raises(testee.PlannerError, match="no steps"):
        await testee.plan_query(agent, "q", False)


async def test_plan_query_raises_when_every_step_has_a_blank_search_query() -> None:
    """A plan of blank queries is a planning failure, not an embeddings HTTP 400."""
    agent = planner_returning(make_output(PlanStep(search_query=" ", purpose="p")))

    with pytest.raises(testee.PlannerError, match="no steps"):
        await testee.plan_query(agent, "q", False)


async def test_plan_query_accepts_a_stepless_plan_that_declares_itself_out_of_domain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A clean stepless out-of-domain plan is valid and warns about nothing."""
    # The out-of-domain check must run BEFORE the no-steps guard: "no family covers
    # this" is a complete plan that has nothing to search, and the pre-existing
    # guard would reject exactly the plan this feature exists to produce. The
    # absence of a warning is asserted too: the "planned steps anyway" warning is a
    # drift signal, and firing it on every clean refusal -- the phase's headline
    # path -- would train a reader to ignore it.
    agent = planner_returning(make_output(out_of_domain=True))

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        plan = await testee.plan_query(agent, "What is the capital of France?", True)

    assert plan.out_of_domain is True
    assert plan.steps == []
    assert plan.original_query == "What is the capital of France?"
    assert [record for record in caplog.records if record.levelno == logging.WARNING] == []


async def test_plan_query_discards_steps_planned_alongside_an_out_of_domain_flag(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The out-of-domain flag wins over any steps the model emitted alongside it."""
    # Not hypothetical: measured live 2026-07-22, asked an off-topic question the
    # model set the flag AND planned searches. Honouring both would send the index
    # a query for cake recipes and bill an embedding for it.
    agent = planner_returning(
        make_output(make_step("cake baking recipe"), out_of_domain=True)
    )

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        plan = await testee.plan_query(agent, "How do I bake a chocolate cake?", True)

    assert plan.out_of_domain is True
    assert plan.steps == []
    warning = next(record for record in caplog.records if record.levelno == logging.WARNING)
    assert getattr(warning, "discarded_steps") == 1


async def test_plan_query_ignores_out_of_domain_when_the_map_is_disabled() -> None:
    """With the map off the model's out-of-domain claim must be discarded, not honoured."""
    # Measured live 2026-07-22: the model sets this flag even with no family list in
    # the prompt. Honouring it there would hand the baseline arm the deterministic
    # fallback the map is being evaluated for, so D12's gate would credit the map
    # with a win it did not produce.
    agent = planner_returning(make_output(make_step("cake baking"), out_of_domain=True))

    plan = await testee.plan_query(agent, "How do I bake a chocolate cake?", False)

    assert plan.out_of_domain is False
    assert [step.search_query for step in plan.steps] == ["cake baking"]


async def test_plan_query_still_raises_on_a_stepless_plan_when_the_map_is_disabled() -> None:
    """With the flag ignored, a stepless plan is once again just a broken plan."""
    agent = planner_returning(make_output(out_of_domain=True))

    with pytest.raises(testee.PlannerError, match="no steps"):
        await testee.plan_query(agent, "q", False)


async def test_plan_query_clamps_an_over_eager_plan_with_the_map_on() -> None:
    """The step limit must survive on the map-on path too -- the one Commit 3 ships."""
    # The clamp sits above the map-on/map-off branch for this reason: every other
    # clamp assertion runs with the map off, so a clamp lost from the shipping
    # branch alone would cost six embeddings and six searches per query, unnoticed.
    family = family_names()[0]
    agent = planner_returning(
        make_output(*(make_step(f"q{index}", family) for index in range(6)))
    )

    plan = await testee.plan_query(agent, "q", True)

    assert len(plan.steps) == testee.MAX_PLAN_STEPS
    assert [step.category for step in plan.steps] == [family] * testee.MAX_PLAN_STEPS


async def test_plan_query_keeps_a_category_that_names_a_real_control_family() -> None:
    """The routing win of the map: a step aimed at a family reaches retrieval as a filter."""
    family = family_names()[0]
    agent = planner_returning(make_output(make_step("account management", family)))

    plan = await testee.plan_query(agent, "q", True)

    assert [step.category for step in plan.steps] == [family]


async def test_plan_query_clears_an_unknown_category_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A category naming no family is cleared, and warned about, when the map is on."""
    # Being given the list and still writing something else is model drift, the same
    # class of signal as exceeding the step limit. The step survives as an
    # unfiltered search rather than as a filter that can only match nothing.
    agent = planner_returning(make_output(make_step("data in transit", "System Protection")))

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        plan = await testee.plan_query(agent, "q", True)

    assert [step.category for step in plan.steps] == [None]
    warning = next(record for record in caplog.records if record.levelno == logging.WARNING)
    assert getattr(warning, "unknown_categories") == ["System Protection"]


async def test_plan_query_drops_a_valid_category_when_the_map_is_disabled(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the map off, even a real family name is dropped -- and silently."""
    # It must not filter the baseline arm's search, and it is not drift either: the
    # model was never shown a list to copy from, so warning here would cry wolf on
    # every single baseline query.
    agent = planner_returning(make_output(make_step("account management", family_names()[0])))

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        plan = await testee.plan_query(agent, "q", False)

    assert [step.category for step in plan.steps] == [None]
    assert caplog.records == [], "the model guessing without a list is expected, not a warning"


async def test_plan_query_warns_when_the_model_exceeds_the_step_limit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Silently clamping would hide a prompt or model regression, as an invented citation would."""
    agent = planner_returning(make_output(*(make_step(f"q{index}") for index in range(5))))

    with caplog.at_level(logging.WARNING, logger=testee.__name__):
        await testee.plan_query(agent, "q", False)

    assert any(record.levelno == logging.WARNING for record in caplog.records)


async def test_plan_query_wraps_a_schema_violation_as_a_planner_error() -> None:
    """A malformed structured output is a Planner failure, not an opaque pydantic error."""

    class _Other(BaseModel):
        value: int

    with pytest.raises(ValidationError) as schema_violation:
        _Other(value="not an int")  # type: ignore[arg-type]
    agent = planner_returning(schema_violation.value)

    with pytest.raises(testee.PlannerError, match="failed validation"):
        await testee.plan_query(agent, "q", False)


async def test_plan_query_logs_the_chat_tokens_the_run_billed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Capacity planning needs tokens per request measured, and this is where they are recorded."""
    agent = planner_returning(
        make_output(make_step("access control")), input_tokens=812, output_tokens=44
    )

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.plan_query(agent, "q", False)

    record = next(record for record in caplog.records if record.message == "query planned")
    assert getattr(record, "input_tokens") == 812
    assert getattr(record, "output_tokens") == 44


async def test_plan_query_logs_zero_tokens_when_usage_is_absent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`loadtest.checks.summarize_run` sums these keys, so a missing usage must log 0, not None."""
    agent = planner_returning(make_output(make_step("q")))
    # A run whose usage the client did not populate: `usage_details` is None.
    agent.run.return_value.usage_details = None

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.plan_query(agent, "q", False)

    record = next(record for record in caplog.records if record.message == "query planned")
    assert getattr(record, "input_tokens") == 0
    assert getattr(record, "output_tokens") == 0


async def test_plan_query_logs_the_out_of_domain_verdict_and_the_filter_each_step_ran_under(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The audit line records the out-of-domain verdict and each step's filter."""
    # A refusal that never searched and a search that found nothing are different
    # events behind the same user-visible answer, and a narrowed search only reads
    # as narrowed if its filter is recorded. This line is where both distinctions live.
    family = family_names()[0]
    agent = planner_returning(make_output(make_step("account management", family)))

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.plan_query(agent, "q", True)

    record = next(record for record in caplog.records if record.message == "query planned")
    assert getattr(record, "out_of_domain") is False
    assert getattr(record, "steps")[0]["category"] == family


async def test_plan_query_logs_the_verdict_it_honoured_not_the_one_the_model_proposed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the map off, a model that claims out-of-domain must still log False."""
    # This is the exact case measured live on 2026-07-22 -- the model sets the flag
    # even with no family list in the prompt -- and it is the only case where the
    # honoured verdict and the raw one differ. D12 gate (b) is read from this key,
    # so logging the model's claim instead of the Planner's decision would report a
    # deterministic refusal the pipeline never actually made.
    agent = planner_returning(make_output(make_step("cake baking"), out_of_domain=True))

    with caplog.at_level(logging.INFO, logger=testee.__name__):
        await testee.plan_query(agent, "How do I bake a chocolate cake?", False)

    record = next(record for record in caplog.records if record.message == "query planned")
    assert getattr(record, "out_of_domain") is False
