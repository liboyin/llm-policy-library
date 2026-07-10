"""The NIST SP 800-53 Rev 5 control catalog, fetched and parsed into records.

TASK.md asks for at least 500 policy records with a title, description, and
category. The suggested Hugging Face CSF dataset carries only 91, so this
project ingests the official NIST OSCAL catalog instead (decision D1 in
TODO.md): 1,014 live controls and control enhancements, each mapping cleanly
onto the required fields.

The catalog is pinned to an exact commit of `usnistgov/oscal-content` so that
re-running ingestion cannot silently change the corpus underneath the index or
the evaluation golden set.

Two properties of the source data drive the parsing code:

* **Statement prose carries parameter placeholders** such as
  `{{ insert: param, ac-1_prm_1 }}`. Left as-is they are noise to both the
  embedding model and the reader, so they are rendered into the bracketed
  `[Assignment: ...]` / `[Selection: ...]` prose NIST publishes.
* **Parameters resolve across control boundaries.** Seventeen placeholders
  reference a parameter defined on a *different* control, so resolution uses a
  catalog-wide map rather than the enclosing control's own parameters.

Withdrawn controls are excluded. They are superseded, carry no requirement (180
of the 182 have no statement at all), and citing one as if it applied would be a
grounding error in a compliance answer.

Network access is confined to `fetch_catalog`; every other function here is
pure, so parsing is tested against a checked-in fixture with no Azure or
GitHub calls.
"""

import re
from collections.abc import Iterator
from typing import Any, Final

import httpx
from pydantic import BaseModel, ConfigDict

# Pinned to the commit that published the Rev 5 catalog artifacts on 2026-05-13.
# Bumping this changes the corpus, so it must be a deliberate, reviewed edit.
CATALOG_COMMIT: Final = "78650f02ad9321bb7b817846f8fbd4f2bcd620de"
CATALOG_URL: Final = (
    "https://raw.githubusercontent.com/usnistgov/oscal-content/"
    f"{CATALOG_COMMIT}/nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json"
)

FETCH_TIMEOUT_SECONDS: Final = 60.0

# TASK.md: "Ingest at least 500 records into your solution."
MIN_EXPECTED_RECORDS: Final = 500

# `{{ insert: param, ac-1_prm_1 }}` -> `ac-1_prm_1`.
_PLACEHOLDER: Final = re.compile(r"\{\{\s*insert:\s*param,\s*([^}\s]+)\s*\}\}")

# A `select` parameter's choices may themselves contain placeholders. The bound
# stops a malformed catalog with a parameter cycle from recursing forever.
_MAX_PLACEHOLDER_DEPTH: Final = 8

_INDENT: Final = "  "


class DatasetError(RuntimeError):
    """Raised when the catalog is missing, malformed, or unexpectedly small."""


class PolicyRecord(BaseModel):
    """One NIST SP 800-53 control or control enhancement.

    Attributes:
        id: Lower-case OSCAL control ID, e.g. `ac-2` or `ac-2.1`. Used verbatim
            as the citation token and as the golden-set label in evaluation.
        title: The control's short name, e.g. "Account Management".
        description: The control statement, with nested items flattened into
            labelled lines and parameter placeholders rendered readably.
        category: The control family taken from the OSCAL group, e.g.
            "Access Control".
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str
    category: str


def fetch_catalog(
    url: str = CATALOG_URL, timeout: float = FETCH_TIMEOUT_SECONDS
) -> dict[str, Any]:
    """Download the OSCAL catalog JSON.

    The only function in this module that touches the network.

    Args:
        url: Location of the catalog. Defaults to the pinned commit.
        timeout: Per-request timeout in seconds; the document is ~10 MB.

    Returns:
        The decoded OSCAL document, i.e. `{"catalog": {...}}`.

    Raises:
        httpx.HTTPError: If the download fails or returns a non-2xx status.
    """
    response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    catalog: dict[str, Any] = response.json()
    return catalog


def iter_controls(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every control beneath a group or control, depth first.

    Control enhancements are nested inside their parent control's `controls`
    list, so the same traversal reaches both. TASK.md counts each as a record.

    Args:
        node: An OSCAL group or control.

    Yields:
        Each descendant control, parents before their enhancements.
    """
    for control in node.get("controls", []):
        yield control
        yield from iter_controls(control)


def is_withdrawn(control: dict[str, Any]) -> bool:
    """Report whether a control has been withdrawn from the catalog.

    Args:
        control: An OSCAL control.

    Returns:
        True if the control carries the `status: withdrawn` property.
    """
    return any(
        prop.get("name") == "status" and prop.get("value") == "withdrawn"
        for prop in control.get("props", [])
    )


def build_parameter_map(catalog_body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Index every parameter in the catalog by its globally unique ID.

    Placeholders are resolved against this catalog-wide map because seventeen of
    them reference a parameter declared on another control; a per-control map
    would leave those unresolved.

    Args:
        catalog_body: The `catalog` object of the OSCAL document.

    Returns:
        A mapping from parameter ID to the parameter object.
    """
    return {
        parameter["id"]: parameter
        for group in catalog_body.get("groups", [])
        for control in iter_controls(group)
        for parameter in control.get("params", [])
    }


def render_parameter(
    parameter: dict[str, Any], parameters: dict[str, dict[str, Any]], depth: int = 0
) -> str:
    """Render one OSCAL parameter as bracketed prose.

    Assignment parameters become `[Assignment: <label>]` and selection
    parameters `[Selection (one or more): a; b]`, following the bracket
    convention of the published SP 800-53. Labels are used exactly as the
    catalog states them: the printed document expands most of them to
    "organization-defined <label>", but reconstructing that would put words into
    a control that its source does not contain, which is the wrong trade in a
    system whose answers must be grounded in the catalog.

    Args:
        parameter: The parameter object to render.
        parameters: Catalog-wide parameter map, for placeholders nested inside
            a selection's choices.
        depth: Current placeholder-resolution depth.

    Returns:
        The rendered parameter text.
    """
    selection = parameter.get("select")
    if selection is not None:
        choices = "; ".join(
            resolve_placeholders(choice, parameters, depth + 1)
            for choice in selection.get("choice", [])
        )
        # OSCAL omits `how-many` to mean "exactly one".
        how_many = selection.get("how-many")
        qualifier = f" ({how_many.replace('-', ' ')})" if how_many else ""
        return f"[Selection{qualifier}: {choices}]"
    label = parameter.get("label")
    if label:
        return f"[Assignment: {label}]"
    # No label and no choices: name the parameter rather than drop the slot,
    # so the reader can still see that the control expects a value here.
    return f"[{parameter['id']}]"


def resolve_placeholders(
    text: str, parameters: dict[str, dict[str, Any]], depth: int = 0
) -> str:
    """Replace every `{{ insert: param, ... }}` placeholder in a prose string.

    Args:
        text: Prose that may contain placeholders.
        parameters: Catalog-wide parameter map.
        depth: Current resolution depth, incremented through nested selections.

    Returns:
        The prose with every placeholder rendered. An unknown parameter ID, or
        one nested past `_MAX_PLACEHOLDER_DEPTH`, degrades to `[<id>]` rather
        than raising: a single odd placeholder must not drop a whole control.
    """
    if depth >= _MAX_PLACEHOLDER_DEPTH:
        return _PLACEHOLDER.sub(lambda match: f"[{match.group(1)}]", text)

    def substitute(match: re.Match[str]) -> str:
        parameter_id = match.group(1)
        parameter = parameters.get(parameter_id)
        if parameter is None:
            return f"[{parameter_id}]"
        return render_parameter(parameter, parameters, depth)

    return _PLACEHOLDER.sub(substitute, text)


def _label_of(part: dict[str, Any]) -> str:
    """Return a statement item's label, e.g. `a.` or `(1)`.

    Args:
        part: An OSCAL part.

    Returns:
        The label property's value, or an empty string when unlabelled.
    """
    return next(
        (prop["value"] for prop in part.get("props", []) if prop.get("name") == "label"),
        "",
    )


def render_statement(
    part: dict[str, Any], parameters: dict[str, dict[str, Any]], depth: int = 0
) -> list[str]:
    """Flatten a statement part and its nested items into indented lines.

    A control statement is either a single prose sentence, a tree of labelled
    items ("a.", "1.", "(a)"), or a lead-in sentence followed by such a tree.
    Indentation and labels preserve the hierarchy that gives each item its
    meaning; discarding them would merge independent requirements into one
    run-on paragraph.

    Args:
        part: The statement part, or one of its nested item parts.
        parameters: Catalog-wide parameter map.
        depth: Indentation level of `part`.

    Returns:
        One line per prose-bearing part, in document order.
    """
    lines: list[str] = []
    prose = part.get("prose")
    if prose is not None:
        label = _label_of(part)
        prefix = f"{label} " if label else ""
        lines.append(f"{_INDENT * depth}{prefix}{resolve_placeholders(prose, parameters)}")
    # The statement part itself is a container: its items start at depth 0 when
    # it has no lead-in prose, and are indented under one when it does.
    child_depth = depth + 1 if prose is not None else depth
    for child in part.get("parts", []):
        lines.extend(render_statement(child, parameters, child_depth))
    return lines


def control_description(
    control: dict[str, Any], parameters: dict[str, dict[str, Any]]
) -> str:
    """Build a control's description from its statement part.

    Only the `statement` part is used. The sibling `guidance` and
    `assessment-objective` parts are commentary and SP 800-53A assessment
    procedure, not the requirement itself, and including them would dilute the
    embedding of what the control actually mandates.

    Args:
        control: An OSCAL control, expected to be live rather than withdrawn.
        parameters: Catalog-wide parameter map.

    Returns:
        The rendered statement.

    Raises:
        DatasetError: If the control has no statement part. Every live control
            in the pinned catalog has one, so this signals a corpus change that
            must be reviewed rather than silently ingested.
    """
    statement = next(
        (part for part in control.get("parts", []) if part.get("name") == "statement"),
        None,
    )
    if statement is None:
        raise DatasetError(f"control {control['id']!r} has no statement part")
    return "\n".join(render_statement(statement, parameters))


def parse_catalog(catalog: dict[str, Any]) -> list[PolicyRecord]:
    """Parse the OSCAL document into policy records.

    Args:
        catalog: The decoded OSCAL document, i.e. `{"catalog": {...}}`.

    Returns:
        One record per live control and control enhancement, in catalog order.

    Raises:
        DatasetError: If the document is not an OSCAL catalog, a live control
            lacks a statement, or a required field is absent.
    """
    try:
        body = catalog["catalog"]
    except KeyError as error:
        raise DatasetError("document has no 'catalog' object; not an OSCAL catalog") from error

    parameters = build_parameter_map(body)
    try:
        return [
            PolicyRecord(
                id=control["id"],
                title=control["title"],
                description=control_description(control, parameters),
                category=group["title"],
            )
            for group in body.get("groups", [])
            for control in iter_controls(group)
            if not is_withdrawn(control)
        ]
    except KeyError as error:
        # A bare KeyError names the field but not the corpus; report it the same
        # way a missing statement is reported, so one signal means "schema moved".
        raise DatasetError(
            f"catalog entry is missing the required field {error}; "
            "the pinned catalog's schema may have changed"
        ) from error


def validate_record_count(
    records: list[PolicyRecord], minimum: int = MIN_EXPECTED_RECORDS
) -> None:
    """Fail ingestion if the catalog yielded fewer records than TASK.md requires.

    Guards against a silently truncated download or an upstream schema change
    that makes the parser skip most controls.

    Args:
        records: The parsed records.
        minimum: Smallest acceptable record count.

    Raises:
        DatasetError: If fewer than `minimum` records were parsed.
    """
    if len(records) < minimum:
        raise DatasetError(
            f"parsed {len(records)} records but at least {minimum} are required; "
            "the catalog download or its schema may have changed"
        )
