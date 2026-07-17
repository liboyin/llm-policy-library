"""Generate the corpus map from the pinned NIST SP 800-53 catalog.

Run as `python -m llm_policy_library.build_map`. The run fetches the pinned OSCAL
catalog, groups its controls into the twenty families, asks the chat model for
one routing abstract per family, and writes `corpus_map.json` into the package.
It is an offline tool: the map is a version-controlled artifact reviewed as a
commit diff, not something the serving path ever rebuilds.

Each family gets one call carrying every control title and statement it holds,
plus the names of its siblings. The siblings are what make the abstracts
mutually distinguishable — an abstract written without knowing that "Audit and
Accountability" exists will happily claim logging for "System and Information
Integrity", and a map whose entries overlap routes nothing. Sibling *names* are
shown rather than sibling abstracts: it keeps the twenty calls independent of
each other's output and therefore order-independent, and the extrinsic goal is
the golden-set harness rather than any intrinsic separation measure.

**The cap is never enforced by the model's output schema.** A `maxLength` on
`FamilyAbstract.abstract` is accepted by Azure OpenAI, but it is compiled into
the structured-output grammar, which enforces it by ending the string the moment
the limit is reached: the abstract comes back chopped mid-word, with no signal
that anything was lost (measured 2026-07-17 — a 200-character cap returned
exactly 200 characters ending in "...and removing user accounts and privileges").
A silently truncated entry is worse than a rejected one, so `FamilyAbstract`
declares no cap; the model is *asked* for a length and `summarize_family` checks
what comes back. `corpus_map.FamilyEntry` then re-checks it, because the cap is
ultimately the artifact's contract rather than this generator's — see
`corpus_map.ABSTRACT_CHAR_CAP`.

**The budget the model tracks is characters, not sentences** (measured 2026-07-17
over the real families). Asked for "at most N sentences", it answers a large
family with one long list of comma-separated topics and lands at 188-216
characters whatever N is — and not even monotonically: System and Services
Acquisition fit the cap at three sentences (194) but overran it at two (206) and
one (206). Asked for a character budget it tracks it closely, overshooting by
5-10%: at 200 the same families land at 193-220, at 175 at 168-187, at 150 at
143-172. So the sentence limit is a fixed readability guardrail
(`ABSTRACT_MAX_SENTENCES`) and the *character* budget is what the retry ladder
lowers (`ABSTRACT_CHAR_AIMS`).

The first rung is the cap itself, because the goal is to spend the whole budget
on routing detail; the overshoot is what makes the lower rungs necessary rather
than merely defensive. An over-long abstract is re-requested, never trimmed, so
the model re-summarises within the smaller budget instead of losing its last
clause. A family that overruns every rung fails the build, because a map is only
worth committing if every entry is whole.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Final

from agent_framework import Agent, UsageDetails
from agent_framework.openai import OpenAIChatClient, OpenAIChatOptions
from pydantic import BaseModel, ConfigDict, Field
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from llm_policy_library.config import (
    AZURE_OPENAI_CHAT_API_VERSION,
    ReasoningEffort,
    load_settings,
)
from llm_policy_library.corpus_map import (
    ABSTRACT_CHAR_CAP,
    MAP_FILENAME,
    CorpusMap,
    CorpusSource,
    FamilyEntry,
)
from llm_policy_library.dataset import (
    CATALOG_COMMIT,
    CATALOG_URL,
    PolicyRecord,
    fetch_catalog,
    parse_catalog,
    validate_record_count,
)
from llm_policy_library.logging_setup import configure_logging, correlation_context
from llm_policy_library.prompts import get_prompt

logger = logging.getLogger(__name__)

# Where the generated artifact is written. `corpus_map` reads it back through the
# resource API, which cannot write; this is the same file in the source tree,
# which is the only place regenerating it makes sense — the map is committed.
MAP_OUTPUT_PATH: Final = Path(__file__).resolve().parent / MAP_FILENAME

# A readability guardrail, not a length control: the model packs a large family
# into one long list of topics whatever this is, so it is fixed rather than
# laddered. It stops a small family sprawling into a paragraph.
ABSTRACT_MAX_SENTENCES: Final = 3

# Character budgets, offered in order until one comes back inside the cap. The
# first rung is the cap itself: the goal is maximum routing detail, so ask for
# the whole budget and let the ~10% overshoot fall to a lower rung rather than
# lowballing every family to make the verbose ones safe.
ABSTRACT_CHAR_AIMS: Final = (ABSTRACT_CHAR_CAP, 175, 150)

# One initial try plus two retries per call, mirroring the judges': transient
# Azure OpenAI failures (rate limits, network blips) usually clear within that.
_CHAT_ATTEMPTS: Final = 3


class BuildMapError(RuntimeError):
    """Raised when a family's abstract cannot be produced within the cap."""


class FamilyAbstract(BaseModel):
    """The chat model's structured output for one family.

    This is the `response_format` JSON schema, so the field description is prompt
    surface. It carries no `max_length`: the cap is checked after the fact
    because a schema cap truncates mid-word (see the module docstring).

    Attributes:
        abstract: What the family covers, written for routing.
    """

    model_config = ConfigDict(frozen=True)

    abstract: str = Field(
        description="What this control family covers, written to route questions to it."
    )


AbstractOptions = OpenAIChatOptions[FamilyAbstract]
AbstractAgent = Agent[AbstractOptions]


def build_abstract_agent(
    chat_client: OpenAIChatClient[AbstractOptions], reasoning_effort: ReasoningEffort
) -> AbstractAgent:
    """Construct the agent that writes one family abstract per call.

    Args:
        chat_client: Client bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.

    Returns:
        The configured agent.
    """
    # See `agents.planner.build_planner` on why the effort value is typed loosely.
    reasoning: Any = {"effort": reasoning_effort}
    options: AbstractOptions = {"response_format": FamilyAbstract, "reasoning": reasoning}
    # The character budget is not here: it changes per attempt, so it belongs in
    # the prompt. These instructions hold only what every attempt shares.
    instructions = get_prompt("corpus_map_instructions", max_sentences=ABSTRACT_MAX_SENTENCES)
    return Agent(chat_client, instructions, name="corpus_map", default_options=options)


def group_by_family(records: list[PolicyRecord]) -> dict[str, list[PolicyRecord]]:
    """Group policy records by control family, preserving catalog order.

    Args:
        records: Every parsed record.

    Returns:
        A mapping from family name to its records, families in the order the
        catalog first mentions them and records in catalog order within each.
    """
    families: dict[str, list[PolicyRecord]] = {}
    for record in records:
        families.setdefault(record.category, []).append(record)
    return families


def sibling_names(family: str, all_families: list[str]) -> list[str]:
    """Return every family name except the one being summarised.

    Args:
        family: The family being summarised.
        all_families: Every family name in the catalog.

    Returns:
        The other families' names, in catalog order.
    """
    return [name for name in all_families if name != family]


def format_family_controls(records: list[PolicyRecord]) -> str:
    """Render a family's controls as the prompt's evidence block.

    Args:
        records: The family's records, in catalog order.

    Returns:
        One labelled block per control, carrying its ID, title, and statement.
    """
    return "\n\n".join(
        f"{record.id.upper()}: {record.title}\n{record.description}" for record in records
    )


def build_family_prompt(
    family: str, siblings: list[str], records: list[PolicyRecord], char_aim: int
) -> str:
    """Build the per-family prompt for one attempt at an abstract.

    Args:
        family: The family being summarised.
        siblings: The other families' names, which the abstract must be
            distinguishable from.
        records: The family's records, in catalog order.
        char_aim: The character budget this attempt offers the model.

    Returns:
        The prompt text.
    """
    return get_prompt(
        "corpus_map_prompt",
        family=family,
        siblings="\n".join(siblings),
        control_count=len(records),
        controls=format_family_controls(records),
        char_aim=char_aim,
    )


async def request_abstract(agent: AbstractAgent, prompt: str) -> tuple[str, UsageDetails]:
    """Ask the model for one abstract, retrying transient failures.

    Args:
        agent: The abstract-writing agent.
        prompt: The per-family prompt.

    Returns:
        The abstract, stripped and non-empty, and the call's token usage.

    Raises:
        BuildMapError: If the model kept returning no structured abstract, or
            kept returning a blank one.
        ValidationError: If the model's output kept failing schema validation.
            All propagate after the final attempt: this is an offline generator
            whose artifact is committed, so failing loudly beats writing a map
            with a hole in it.
    """
    abstract = ""
    usage = UsageDetails()
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(_CHAT_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    ):
        with attempt:
            response = await agent.run(prompt)
            value = response.value
            if value is None:
                raise BuildMapError("the model returned no structured abstract")
            abstract = value.abstract.strip()
            # A blank abstract is a failed call, not a short one: `FamilyAbstract`
            # sets no minimum, so without this the empty string sails past the cap
            # check and only dies at `FamilyEntry`, after every other family has
            # already been paid for, as a pydantic error naming neither the family
            # nor the cause. Raising here spends a retry on it instead.
            if not abstract:
                raise BuildMapError("the model returned a blank abstract")
            usage = response.usage_details or UsageDetails()
    return abstract, usage


async def summarize_family(
    agent: AbstractAgent, family: str, siblings: list[str], records: list[PolicyRecord]
) -> str:
    """Write one family's abstract, re-summarising until it fits the cap.

    Args:
        agent: The abstract-writing agent.
        family: The family being summarised.
        siblings: The other families' names.
        records: The family's records, in catalog order.

    Returns:
        The abstract, at most `ABSTRACT_CHAR_CAP` characters.

    Raises:
        BuildMapError: If every budget in `ABSTRACT_CHAR_AIMS` still overran the
            cap. Committing a truncated entry would be worse. Also propagates
            `request_abstract`'s BuildMapError for a missing or blank abstract.
        ValidationError: Propagated from `request_abstract` when the model's
            output kept failing schema validation.
    """
    for char_aim in ABSTRACT_CHAR_AIMS:
        prompt = build_family_prompt(family, siblings, records, char_aim)
        abstract, usage = await request_abstract(agent, prompt)
        if len(abstract) > ABSTRACT_CHAR_CAP:
            logger.warning(
                "abstract overran the cap; re-summarising on a smaller budget",
                extra={
                    "family": family,
                    "char_aim": char_aim,
                    "abstract_chars": len(abstract),
                    "cap": ABSTRACT_CHAR_CAP,
                },
            )
            continue
        logger.info(
            "family summarised",
            extra={
                "family": family,
                "control_count": len(records),
                "char_aim": char_aim,
                # Source-to-abstract sizes, in both units. Diagnostic only: the
                # goal is routing discrimination, so a ratio here proves nothing
                # about the map — it only shows what the entry cost to make and
                # what it will cost the Planner to read.
                "source_chars": len(prompt),
                "abstract_chars": len(abstract),
                # The same always-int keys the Planner and Response Agent log.
                "input_tokens": usage.get("input_token_count") or 0,
                "output_tokens": usage.get("output_token_count") or 0,
            },
        )
        return abstract
    raise BuildMapError(
        f"family {family!r} exceeded the {ABSTRACT_CHAR_CAP}-character cap on every "
        f"budget in {ABSTRACT_CHAR_AIMS}"
    )


async def build_corpus_map(agent: AbstractAgent, records: list[PolicyRecord]) -> CorpusMap:
    """Summarise every family into a corpus map.

    Families are summarised one at a time. Twenty calls take about a minute,
    which is not worth the concurrency: this runs by hand, and a serial run keeps
    the log readable in catalog order.

    Args:
        agent: The abstract-writing agent.
        records: Every parsed record.

    Returns:
        The map, its `source` pinned to the catalog the records came from.

    Raises:
        BuildMapError: If any family could not be summarised within the cap, or
            the model returned no usable abstract for one.
        ValidationError: Propagated from `summarize_family` when the model's
            output kept failing schema validation.
    """
    families = group_by_family(records)
    names = list(families)
    entries: list[FamilyEntry] = []
    for family, family_records in families.items():
        abstract = await summarize_family(
            agent, family, sibling_names(family, names), family_records
        )
        entries.append(
            FamilyEntry(name=family, control_count=len(family_records), abstract=abstract)
        )
    return CorpusMap(
        source=CorpusSource(url=CATALOG_URL, sha=CATALOG_COMMIT), families=entries
    )


async def _build() -> None:
    """Fetch the catalog, summarise every family, and write the map."""
    settings = load_settings()
    configure_logging(settings.log_level)

    with correlation_context() as run_id:
        logger.info("map build started", extra={"run_id": run_id, "catalog_url": CATALOG_URL})
        records = parse_catalog(fetch_catalog())
        validate_record_count(records)
        # The Agent Framework client owns its own transport and exposes no close,
        # so it needs no context manager (the same trade `evaluation/run_eval.py`
        # accepts for its judge client).
        chat_client: OpenAIChatClient[AbstractOptions] = OpenAIChatClient(
            model=settings.azure_openai_chat_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=AZURE_OPENAI_CHAT_API_VERSION,
        )
        agent = build_abstract_agent(chat_client, settings.llm_reasoning_effort)
        corpus_map = await build_corpus_map(agent, records)
        MAP_OUTPUT_PATH.write_text(corpus_map.model_dump_json(indent=2) + "\n", encoding="utf-8")
        logger.info(
            "map build complete",
            extra={"families": len(corpus_map.families), "path": str(MAP_OUTPUT_PATH)},
        )


def main() -> int:
    """Build the corpus map and write it into the package.

    Returns:
        A process exit code; 0 on success.
    """
    asyncio.run(_build())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
