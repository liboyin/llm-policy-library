"""Unit tests for `llm_policy_library.corpus_map`."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

import llm_policy_library.corpus_map as testee
from llm_policy_library.dataset import CATALOG_COMMIT, CATALOG_URL, parse_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "oscal_catalog_excerpt.json"

# Every control family in the pinned NIST SP 800-53 Rev 5 catalog, in catalog
# order. Pinned as a literal because the map's family names are the Planner's
# whole category vocabulary: a name here that the index's `category` field does
# not hold is a filter that silently matches nothing. Regenerating the map from
# the same catalog must not move this list; bumping the catalog must, and then
# this is the reminder that the map has to be rebuilt with it.
CATALOG_FAMILIES = [
    "Access Control",
    "Awareness and Training",
    "Audit and Accountability",
    "Assessment, Authorization, and Monitoring",
    "Configuration Management",
    "Contingency Planning",
    "Identification and Authentication",
    "Incident Response",
    "Maintenance",
    "Media Protection",
    "Physical and Environmental Protection",
    "Planning",
    "Program Management",
    "Personnel Security",
    "Personally Identifiable Information Processing and Transparency",
    "Risk Assessment",
    "System and Services Acquisition",
    "System and Communications Protection",
    "System and Information Integrity",
    "Supply Chain Risk Management",
]


def payload_of(*families: tuple[str, int, str]) -> dict[str, Any]:
    """Build a valid map payload from `(name, control_count, abstract)` triples.

    Args:
        families: The entries to include, in order.

    Returns:
        The payload.
    """
    return {
        "source": {"url": "https://example.test/catalog.json", "sha": "abc123"},
        "families": [
            {"name": name, "control_count": count, "abstract": abstract}
            for name, count, abstract in families
        ],
    }


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Keep the module-level map cache from leaking between tests."""
    testee._MAP_CACHE = None
    yield
    testee._MAP_CACHE = None


def test_corpus_source_requires_a_url_and_sha() -> None:
    """Provenance is what makes map-versus-corpus drift detectable; blank defeats it."""
    with pytest.raises(ValidationError):
        testee.CorpusSource(url="", sha="abc123")
    with pytest.raises(ValidationError):
        testee.CorpusSource(url="https://example.test/c.json", sha="")


def test_family_entry_rejects_an_abstract_over_the_cap() -> None:
    """The cap is the Planner's token budget: a hand-widened entry must fail to load."""
    with pytest.raises(ValidationError):
        testee.FamilyEntry(
            name="Access Control",
            control_count=131,
            abstract="x" * (testee.ABSTRACT_CHAR_CAP + 1),
        )


def test_family_entry_accepts_an_abstract_at_the_cap() -> None:
    """The cap is inclusive; an entry that exactly spends its budget is valid."""
    entry = testee.FamilyEntry(
        name="Access Control", control_count=131, abstract="x" * testee.ABSTRACT_CHAR_CAP
    )

    assert len(entry.abstract) == testee.ABSTRACT_CHAR_CAP


def test_family_entry_rejects_an_empty_abstract() -> None:
    """An entry with no abstract routes nothing: a broken map, not an empty one."""
    with pytest.raises(ValidationError):
        testee.FamilyEntry(name="Access Control", control_count=131, abstract="")


def test_family_entry_rejects_a_multi_line_abstract() -> None:
    """render_map gives each family one line; a newline would split an entry into a nameless second."""
    with pytest.raises(ValidationError):
        testee.FamilyEntry(
            name="Access Control", control_count=131, abstract="Accounts.\nPrivileges."
        )


def test_family_entry_rejects_a_family_with_no_controls() -> None:
    """A zero-control family means the grouping broke; the count must not read as valid."""
    with pytest.raises(ValidationError):
        testee.FamilyEntry(name="Access Control", control_count=0, abstract="Accounts.")


def test_corpus_map_rejects_an_empty_family_list() -> None:
    """An empty map hands the Planner an empty vocabulary and routes every query nowhere."""
    with pytest.raises(ValidationError):
        testee.CorpusMap(
            source=testee.CorpusSource(url="https://example.test/c.json", sha="abc"), families=[]
        )


def test_load_map_reads_the_file_only_once() -> None:
    """The map is read on the planner's path; re-reading per call would be file I/O per query."""
    with patch.object(testee.json, "loads", return_value=payload_of(("Access Control", 131, "Accounts."))) as loads:
        testee.load_map()
        testee.load_map()

    loads.assert_called_once()


def test_load_map_rejects_an_artifact_that_does_not_match_the_schema() -> None:
    """A map the loader cannot validate means generator and loader diverged; it must not load."""
    with patch.object(testee.json, "loads", return_value={"families": [{"name": "Access Control"}]}):
        with pytest.raises(ValidationError):
            testee.load_map()


def test_load_map_validates_the_committed_artifact() -> None:
    """The map that ships is the one thing this loader must actually be able to read."""
    corpus_map = testee.load_map()

    assert len(corpus_map.families) == len(CATALOG_FAMILIES)
    assert all(family.abstract.strip() for family in corpus_map.families)


def test_committed_map_pins_the_catalog_it_was_written_from() -> None:
    """Bumping the catalog without rebuilding leaves the map describing a corpus nobody serves."""
    source = testee.load_map().source

    assert source.sha == CATALOG_COMMIT, "corpus_map.json is stale; re-run build_map"
    assert source.url == CATALOG_URL


def test_family_names_lists_every_family_in_catalog_order() -> None:
    """Catalog order is what makes a regenerated map a readable diff rather than a reshuffle."""
    payload = payload_of(("Access Control", 131, "Accounts."), ("Media Protection", 20, "Media."))

    with patch.object(testee.json, "loads", return_value=payload):
        assert testee.family_names() == ["Access Control", "Media Protection"]


def test_committed_map_lists_every_catalog_family() -> None:
    """The map's names are the Planner's vocabulary; a wrong one filters to nothing."""
    assert testee.family_names() == CATALOG_FAMILIES


def test_committed_map_control_counts_sum_to_the_pinned_corpus() -> None:
    """control_count is Planner-visible weight; only the schema's `> 0` guards a mis-grouped edit."""
    total = sum(family.control_count for family in testee.load_map().families)

    # TODO.md's verified corpus shape: 300 base controls + 714 enhancements.
    # A hand-edited or mis-grouped count survives every other gate but this one.
    assert total == 1014, "corpus_map.json control counts drifted from the pinned catalog"


def test_committed_map_covers_the_families_the_parser_really_emits() -> None:
    """Ties the map's names to parse_catalog's own output, not just to a hand-written list."""
    fixture_families = {
        record.category for record in parse_catalog(json.loads(FIXTURE.read_text()))
    }

    assert fixture_families <= set(testee.family_names())


def test_render_map_shows_each_family_with_its_count_and_abstract() -> None:
    """This block is the Planner's entire view of the corpus: name, weight, and what it covers."""
    payload = payload_of(
        ("Access Control", 131, "Accounts, privileges."), ("Media Protection", 20, "Media handling.")
    )

    with patch.object(testee.json, "loads", return_value=payload):
        rendered = testee.render_map()

    assert rendered == (
        "Access Control (131 controls): Accounts, privileges.\n"
        "Media Protection (20 controls): Media handling."
    )
