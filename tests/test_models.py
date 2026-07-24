"""Unit tests for `llm_policy_library.models`."""

import llm_policy_library.models as testee

# Everything in `PlannerOutput` — both class docstrings and every field
# description, including the nested `PlanStep`'s — is sent to the chat model: the
# Planner passes the class as a structured-output `response_format`, and the SDK
# serialises all of it into the strict JSON schema on the wire. Measured
# 2026-07-22 and re-measured 2026-07-24 via
# `openai.lib._parsing._responses.type_to_text_format_param(PlannerOutput)`:
# seven description strings reach the model.
#
# That makes these strings prompt surface, and Phase 10's ground rules say prompt
# surface must not contradict `planner_instructions`. It has contradicted it
# twice: once when the class docstrings told the model the Planner would discard
# its answers, and once when `category`'s description said "leave it null unless
# your instructions list the families to choose from" while the map-on
# instructions did exactly that. Both cost tokens, and the second cost measured
# recall.
#
# The prompt *file* is pinned in `tests/agents/test_planner.py`; these tests pin
# every one of the seven strings on the schema half. They assert equality rather
# than substrings because mutants repeatedly walked around substring assertions
# on the other surface: an `unless` clause inside the sentence, an exception
# appended after it, and a hedge bullet prepended before it. Equality catches
# every shape at once. Test order follows the declaration order in `models.py`.


def test_plan_step_docstring_does_not_offer_the_model_a_filter() -> None:
    """The class docstring is prompt surface; it must not contradict the null rule."""
    # Mutation-verified: rewriting this to "…filtered to one family when one fits"
    # reinstates, in text the model reads, exactly the filtering the A/B measured
    # at -0.093 exact-ID recall -- and left the whole suite green before this test.
    assert testee.PlanStep.__doc__ == "One search to run against the policy index."


def test_plan_step_search_query_description_asks_for_a_topic_phrase() -> None:
    """The keyword-soup lesson lives in this description as well as in the prompt."""
    # Phase 3 measured that keyword lists score worse on the semantic reranker than
    # natural language (1.90-2.00 vs 2.12-2.28), and Phase 9 found this very field
    # still asking for "keywords and topic phrases", half-undoing the prompt fix.
    assert testee.PlanStep.model_fields["search_query"].description == (
        "A short natural-language phrase naming the security topic to search "
        "the control catalog for, or a single control ID."
    )


def test_plan_step_purpose_description_asks_for_a_sentence_not_keywords() -> None:
    """`purpose` is the audit trail's record of *why* a search ran, in prose."""
    # Nothing branches on it, so a regression here is invisible to behaviour tests
    # while still changing what the model writes into every plan's audit line.
    assert testee.PlanStep.model_fields["purpose"].description == (
        "What this step is meant to find, in one sentence."
    )


def test_plan_step_category_description_asks_for_null_without_exception() -> None:
    """The schema must not invite a filter the pipeline throws away."""
    # `plan_query` clears every category, so any value the model proposes is spent
    # output tokens and a drift warning. Mutation-verified: both restoring the old
    # conditional wording and appending "Set it when one family plainly owns the
    # step." to this description leave the whole suite green without this test.
    assert testee.PlanStep.model_fields["category"].description == (
        "Always null. This system searches every control family."
    )


def test_planner_output_docstring_states_what_the_model_must_return() -> None:
    """The other class docstring the model reads, pinned for the same reason."""
    assert testee.PlannerOutput.__doc__ == (
        "The plan: the searches to run, and whether the corpus covers the question."
    )


def test_planner_output_steps_description_carries_no_step_count() -> None:
    """The step limit has exactly one prompt-surface home: `planner_instructions`."""
    # A number here would be a second source that drifts from the interpolated
    # `MAX_PLAN_STEPS` -- the drift Phase 3 fixed by removing it from this field.
    assert testee.PlannerOutput.model_fields["steps"].description == (
        "The searches that together answer the question."
    )


def test_planner_output_out_of_domain_description_states_both_verdicts() -> None:
    """The refusal this phase ships for must be asked of the model unambiguously."""
    # The false branch is spelled out because the schema is shared by both settings:
    # with the corpus map off there is no family list, and the model must answer
    # False rather than guess. Mutation-verified: softening this to "Optional. Set
    # it when..." leaves the suite green without this test.
    assert testee.PlannerOutput.model_fields["out_of_domain"].description == (
        "True when your instructions list the control families and none of "
        "them covers the question, so the corpus cannot answer it. False "
        "when your instructions list no families."
    )
