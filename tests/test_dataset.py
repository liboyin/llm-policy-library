"""Unit tests for `llm_policy_library.dataset`."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import ValidationError

import llm_policy_library.dataset as testee

FIXTURE = Path(__file__).parent / "fixtures" / "oscal_catalog_excerpt.json"


@pytest.fixture
def catalog() -> dict[str, Any]:
    """Load the checked-in OSCAL excerpt."""
    document: dict[str, Any] = json.loads(FIXTURE.read_text())
    return document


@pytest.fixture
def parameters(catalog: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Build the catalog-wide parameter map for the excerpt."""
    return testee.build_parameter_map(catalog["catalog"])


def test_catalog_url_pins_an_exact_commit() -> None:
    """An unpinned URL would let the corpus drift under the index and golden set."""
    assert len(testee.CATALOG_COMMIT) == 40, "expected a full git SHA"
    assert testee.CATALOG_COMMIT in testee.CATALOG_URL
    assert "/main/" not in testee.CATALOG_URL, "a branch ref is not a pin"


def test_policy_record_is_immutable(catalog: dict[str, Any]) -> None:
    """Records are the ingestion contract; nothing downstream may rewrite them."""
    record = testee.parse_catalog(catalog)[0]

    with pytest.raises(ValidationError):
        record.id = "tampered"


@respx.mock
def test_fetch_catalog_returns_the_decoded_document() -> None:
    """Ingestion needs the parsed OSCAL document, not a raw response."""
    respx.get(testee.CATALOG_URL).mock(
        return_value=httpx.Response(200, json={"catalog": {"groups": []}})
    )

    assert testee.fetch_catalog() == {"catalog": {"groups": []}}


@respx.mock
def test_fetch_catalog_raises_on_an_error_response() -> None:
    """A moved or deleted pinned URL must abort ingestion, not index an error page."""
    respx.get(testee.CATALOG_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(httpx.HTTPStatusError):
        testee.fetch_catalog()


def test_iter_controls_yields_enhancements_nested_in_their_parent(
    catalog: dict[str, Any],
) -> None:
    """Enhancements are records too; a shallow walk would lose 872 of 1,014."""
    access_control = catalog["catalog"]["groups"][0]

    ids = [control["id"] for control in testee.iter_controls(access_control)]

    assert ids == ["ac-1", "ac-2", "ac-2.1", "ac-2.10", "ac-4"]


def test_is_withdrawn_detects_the_status_property(catalog: dict[str, Any]) -> None:
    """Withdrawn controls are superseded and must not be cited as requirements."""
    controls = {
        control["id"]: control
        for control in testee.iter_controls(catalog["catalog"]["groups"][0])
    }

    assert testee.is_withdrawn(controls["ac-2.10"]) is True
    assert testee.is_withdrawn(controls["ac-1"]) is False


def test_build_parameter_map_spans_the_whole_catalog(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """Placeholders reference parameters declared on other controls."""
    assert "ac-1_prm_1" in parameters  # declared on ac-1
    assert "ac-02.01_odp" in parameters  # declared on the ac-2.1 enhancement
    assert parameters["ac-2_prm_frequency"]["label"] == "frequency"


def test_render_parameter_formats_an_assignment(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """Assignment parameters read as the bracketed slots SP 800-53 publishes."""
    rendered = testee.render_parameter(parameters["ac-1_prm_1"], parameters)

    assert rendered == "[Assignment: organization-defined personnel or roles]"


def test_render_parameter_formats_a_multiple_choice_selection(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """`one-or-more` must be visible: it changes what the control demands."""
    rendered = testee.render_parameter(parameters["ac-01_odp.03"], parameters)

    assert rendered == "[Selection (one or more): organization-level; system-level]"


def test_render_parameter_resolves_placeholders_nested_in_a_choice(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """A choice may itself contain a placeholder; leaving it raw leaks OSCAL syntax."""
    rendered = testee.render_parameter(parameters["ac-2_prm_disposition"], parameters)

    assert rendered == "[Selection: remove; disable within [Assignment: frequency]]"


def test_render_parameter_omits_the_qualifier_for_a_single_choice_selection(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """OSCAL omits `how-many` to mean exactly one; inventing "(one)" would misstate it."""
    rendered = testee.render_parameter(parameters["ac-2_prm_disposition"], parameters)

    assert rendered.startswith("[Selection: ")


def test_render_parameter_names_a_parameter_that_has_neither_label_nor_choices(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """Dropping the slot would hide that the control expects a value here."""
    rendered = testee.render_parameter(parameters["ac-2_prm_unlabelled"], parameters)

    assert rendered == "[ac-2_prm_unlabelled]"


def test_resolve_placeholders_substitutes_every_occurrence(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """Prose routinely carries more than one placeholder."""
    text = "Review {{ insert: param, ac-2_prm_frequency }} and {{ insert: param, ac-1_prm_1 }}."

    resolved = testee.resolve_placeholders(text, parameters)

    assert resolved == (
        "Review [Assignment: frequency] and "
        "[Assignment: organization-defined personnel or roles]."
    )


def test_resolve_placeholders_degrades_to_the_id_for_an_unknown_parameter(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """One dangling reference must not discard an otherwise valid control."""
    resolved = testee.resolve_placeholders("Record {{ insert: param, nope }}.", parameters)

    assert resolved == "Record [nope]."


def test_resolve_placeholders_terminates_on_a_self_referential_parameter() -> None:
    """A parameter cycle in a malformed catalog must not hang or overflow the stack."""
    cyclic = {"p": {"id": "p", "select": {"choice": ["{{ insert: param, p }}"]}}}

    resolved = testee.resolve_placeholders("{{ insert: param, p }}", cyclic)

    assert "[p]" in resolved


def test_render_statement_indents_nested_items_under_their_lead_in(
    catalog: dict[str, Any], parameters: dict[str, dict[str, Any]]
) -> None:
    """Labels and indentation carry the hierarchy that scopes each requirement."""
    ac_1 = catalog["catalog"]["groups"][0]["controls"][0]
    statement = ac_1["parts"][0]

    lines = testee.render_statement(statement, parameters)

    assert lines == [
        "Develop and disseminate the following:",
        "  a. Disseminate to [Assignment: organization-defined personnel or roles]:",
        "    1. [Selection (one or more): organization-level; system-level] "
        "access control policy; and",
        "  b. Review the policy [Assignment: frequency].",
    ]


def test_render_statement_keeps_items_flush_when_there_is_no_lead_in(
    catalog: dict[str, Any], parameters: dict[str, dict[str, Any]]
) -> None:
    """218 controls open straight into "a."; indenting them would imply a missing parent."""
    ac_2 = catalog["catalog"]["groups"][0]["controls"][1]
    statement = ac_2["parts"][0]

    lines = testee.render_statement(statement, parameters)

    assert lines[0].startswith("a. Automatically")


def test_control_description_excludes_guidance_and_assessment_parts(
    catalog: dict[str, Any], parameters: dict[str, dict[str, Any]]
) -> None:
    """Only the statement is the requirement; commentary would dilute the embedding."""
    ac_1 = catalog["catalog"]["groups"][0]["controls"][0]

    description = testee.control_description(ac_1, parameters)

    assert "Guidance prose" not in description
    assert "Assessment prose" not in description
    assert description.startswith("Develop and disseminate the following:")


def test_control_description_rejects_a_control_without_a_statement(
    parameters: dict[str, dict[str, Any]],
) -> None:
    """A live control with no requirement text signals a corpus change to review."""
    with pytest.raises(testee.DatasetError, match="xx-1"):
        testee.control_description({"id": "xx-1", "parts": []}, parameters)


def test_parse_catalog_emits_a_record_per_live_control_and_enhancement(
    catalog: dict[str, Any],
) -> None:
    """Both controls and their enhancements are records; TASK.md counts each."""
    records = testee.parse_catalog(catalog)

    assert [record.id for record in records] == ["ac-1", "ac-2", "ac-2.1", "au-1"]


def test_parse_catalog_drops_withdrawn_controls_even_when_they_keep_a_statement(
    catalog: dict[str, Any],
) -> None:
    """Citing a superseded control as if it applied is a grounding error."""
    records = testee.parse_catalog(catalog)

    ids = {record.id for record in records}
    assert "ac-2.10" not in ids, "withdrawn enhancement without a statement"
    assert "ac-4" not in ids, "withdrawn control that still carries statement prose"
    assert all("Superseded requirement" not in record.description for record in records)


def test_parse_catalog_takes_the_category_from_the_control_family(
    catalog: dict[str, Any],
) -> None:
    """TASK.md requires a category per record; the OSCAL group is the control family."""
    records = {record.id: record for record in testee.parse_catalog(catalog)}

    assert records["ac-2.1"].category == "Access Control", "enhancements inherit the family"
    assert records["au-1"].category == "Audit and Accountability"


def test_parse_catalog_resolves_a_placeholder_across_control_boundaries(
    catalog: dict[str, Any],
) -> None:
    """17 real placeholders name a parameter defined on another control."""
    records = {record.id: record for record in testee.parse_catalog(catalog)}

    # `ac-1` references `ac-2_prm_frequency`, which `ac-2` declares.
    assert "[Assignment: frequency]" in records["ac-1"].description
    assert "{{" not in records["ac-1"].description


def test_parse_catalog_rejects_a_document_that_is_not_a_catalog() -> None:
    """A truncated or redirected download must fail loudly, not yield zero records."""
    with pytest.raises(testee.DatasetError, match="not an OSCAL catalog"):
        testee.parse_catalog({"not-a-catalog": {}})


def test_parse_catalog_reports_a_missing_control_field_as_a_schema_change() -> None:
    """Bumping the pinned commit onto a reshaped catalog must not surface as a bare KeyError."""
    catalog = {"catalog": {"groups": [{"title": "Access Control", "controls": [{"id": "ac-1"}]}]}}

    with pytest.raises(testee.DatasetError, match="schema may have changed"):
        testee.parse_catalog(catalog)


def test_parse_catalog_reports_a_missing_parameter_id_as_a_schema_change() -> None:
    """Parameters are indexed before any control is read; that step needs the same guard."""
    catalog = {
        "catalog": {
            "groups": [
                {
                    "title": "Access Control",
                    "controls": [{"id": "ac-1", "title": "T", "params": [{"label": "frequency"}]}],
                }
            ]
        }
    }

    with pytest.raises(testee.DatasetError, match="schema may have changed"):
        testee.parse_catalog(catalog)


def test_validate_record_count_accepts_a_catalog_meeting_the_task_minimum() -> None:
    """TASK.md requires at least 500 ingested records."""
    records = [
        testee.PolicyRecord(id=f"ac-{n}", title="t", description="d", category="c")
        for n in range(testee.MIN_EXPECTED_RECORDS)
    ]

    testee.validate_record_count(records)


def test_validate_record_count_rejects_a_short_catalog(catalog: dict[str, Any]) -> None:
    """A truncated download or schema change must stop before it reaches the index."""
    records = testee.parse_catalog(catalog)

    with pytest.raises(testee.DatasetError, match="at least 500"):
        testee.validate_record_count(records)
