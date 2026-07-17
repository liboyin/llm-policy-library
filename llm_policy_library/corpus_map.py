"""The corpus map: one routing abstract per NIST SP 800-53 control family.

The Planner otherwise plans blind. It writes search queries without ever having
seen what the corpus contains, which is why it cannot filter a search to a family
that exists, and why it can only discover that a question is out of domain by
searching for it and finding nothing. The map is the context that fixes both: a
~1K-token block naming all twenty control families and what each one covers,
small enough to prefix every planner call.

An entry's goal is **routing discrimination, not compression**. It succeeds when
questions that belong to its family reach it and questions that do not stay away;
it is not trying to summarise the family's ~14K characters faithfully, and no
compression ratio is a target. The extrinsic goal function is the golden-set
evaluation harness, not any property of the text itself.

This module owns the artifact's schema and read access; `build_map` generates it
and is the only writer. The schema is shared by both so that a map which loads is
a map that was written by the current generator.

`source` records the catalog URL and commit the abstracts were written from, so
map-versus-corpus drift is detectable: bumping `dataset.CATALOG_COMMIT` without
regenerating the map leaves a map describing a corpus that is no longer served,
and a test pins the two together.
"""

import importlib.resources
import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

# The artifact's name inside the package. `build_map` writes it into the source
# tree; this module reads it back through the resource API, which also works when
# the package is installed rather than run from a checkout.
MAP_FILENAME: Final = "corpus_map.json"

# An abstract's hard ceiling, and the guarantee this artifact carries. At ~200
# characters (~50 tokens) the twenty entries cost the Planner ~1K tokens of
# context on every call, which is the budget this phase was sized against.
# Enforced here, on the schema, rather than only in the generator: the cap is a
# property of the map the Planner reads, so a map edited by hand instead of
# regenerated must fail to load rather than quietly widen every planner prompt.
ABSTRACT_CHAR_CAP: Final = 200


class CorpusSource(BaseModel):
    """The catalog the map's abstracts were written from.

    Attributes:
        url: The pinned catalog URL passed to `dataset.fetch_catalog`.
        sha: The `usnistgov/oscal-content` commit the catalog was read at,
            mirroring `dataset.CATALOG_COMMIT`. This is what makes map-versus-
            corpus drift detectable rather than silent.
    """

    model_config = ConfigDict(frozen=True)

    url: str = Field(min_length=1)
    sha: str = Field(min_length=1)


class FamilyEntry(BaseModel):
    """One control family's entry in the map.

    Attributes:
        name: The family name, exactly as the catalog's OSCAL group titles it —
            which is also exactly what the index's `category` field holds, so the
            Planner can name it in a filter and have the filter match.
        control_count: How many live controls and enhancements the family holds.
            Rendered into the map so the Planner can see a family's weight.
        abstract: What the family covers, written for routing: one line, within
            `ABSTRACT_CHAR_CAP`.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    control_count: int = Field(gt=0)
    # Single-line, because `render_map` gives each family exactly one line: an
    # embedded newline would split one entry into two, the second of them
    # nameless, and the Planner reads that block as its whole view of the corpus.
    # Schema-enforced for the same reason the length is — the artifact's own
    # contract, holding whether it was regenerated or hand-edited.
    abstract: str = Field(min_length=1, max_length=ABSTRACT_CHAR_CAP, pattern=r"^[^\r\n]+$")


class CorpusMap(BaseModel):
    """The whole map: every family in the catalog, plus its provenance.

    Attributes:
        source: The catalog the abstracts were written from.
        families: One entry per family, in catalog order.
    """

    model_config = ConfigDict(frozen=True)

    source: CorpusSource
    families: list[FamilyEntry] = Field(min_length=1)


_MAP_CACHE: CorpusMap | None = None


def load_map() -> CorpusMap:
    """Load and validate the corpus map, reading the file only once.

    Returns:
        The validated map.

    Raises:
        ValidationError: If the committed artifact does not match the schema,
            which means the generator and the loader have diverged.
    """
    global _MAP_CACHE
    if _MAP_CACHE is None:
        file_path = importlib.resources.files("llm_policy_library").joinpath(MAP_FILENAME)
        _MAP_CACHE = CorpusMap.model_validate(json.loads(file_path.read_text(encoding="utf-8")))
    return _MAP_CACHE


def family_names() -> list[str]:
    """Return every family name in the map, in catalog order.

    This is the Planner's fixed vocabulary: a category it proposes is valid only
    if it appears here, which is what keeps a hallucinated family name out of an
    Azure AI Search filter.

    Returns:
        The family names.
    """
    return [family.name for family in load_map().families]


def render_map() -> str:
    """Render the map as the text block the Planner's instructions embed.

    Returns:
        One line per family: `name (n controls): abstract`.
    """
    return "\n".join(
        f"{family.name} ({family.control_count} controls): {family.abstract}"
        for family in load_map().families
    )
