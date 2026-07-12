"""Offline evaluation of the policy pipeline against a hand-labeled golden set.

The golden set (`evaluation/golden_set.json`) pairs each query with the control
IDs a compliance reviewer would expect back, graded 2 (directly on point) or 1
(supporting). Two deliberately out-of-domain queries must return the safe
fallback. This module turns that golden set and a live pipeline into an
`EvaluationReport`, and renders it as Markdown.

Four things are measured, and only one of them needs a chat model:

* **Retrieval, set-based** — recall of the retrieved grounding set against the
  golden qrels, in two granularities (exact-ID and base-family). Pure set
  arithmetic. Recall is the metric that gates answer quality: an answer can only
  be grounded in controls the grounding set actually contains. Precision is
  deliberately not reported — at a top-k of 5 over a hierarchical catalog it
  penalizes retrieving related enhancements and is uninformative (user decision,
  2026-07-12).
* **Retrieval, graded** — NDCG@k computed here, directly from the graded qrels:
  gain is the qrel label, discount is `1/log2(rank+1)`, and the ideal ranking is
  the query's own labels sorted descending, truncated at k. It rewards a run
  that ranks the strongly-relevant controls above the weakly-relevant, with no
  external evaluator involved.
* **Answer quality** — faithfulness and answer relevancy from two injected
  LLM judges (concretely, the PydanticAI agents in `agents.judges`, run through
  the same Azure OpenAI deployment the pipeline uses), each an integer 1-5.
* **Citation validity** — every inline `[control-id]` in the answer must be a
  control that was actually retrieved. This reuses the Response Agent's own
  citation parser, so the check matches what the pipeline itself enforced; an
  invented citation here is a grounding regression.

The judges are injected as `Protocol`s rather than imported, so the whole
harness is exercised in unit tests with plain fakes; the one agent-stack import
is the Response Agent's citation parser and document formatter, reused so the
checks score exactly what the pipeline itself enforced and produced. Each judge call is retried with exponential backoff; if
a judge still fails, its score is recorded as `None` (rendered as "—") rather
than crashing the run. `evaluation/run_eval.py` is the thin runner that
constructs the real judges and a live pipeline and calls in here.
"""

import json
import logging
import math
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from llm_policy_library.agents.response import extract_citations, format_documents
from llm_policy_library.models import PipelineResult, PlanStep, RetrievedDocument

logger = logging.getLogger(__name__)

# The graded-relevance labels the golden set may assign. 0 is excluded on
# purpose: a control worth listing is at least weakly relevant, so a 0 would be a
# labelling mistake, not a judgement.
_MIN_LABEL = 1
_MAX_LABEL = 4

# One initial try plus two retries per judge call; transient Azure OpenAI
# failures (rate limits, network blips) usually clear within that.
_JUDGE_ATTEMPTS: Final = 3

# The judges' score scale, mirroring `agents.judges.JudgeVerdict`. Re-declared
# rather than imported so the harness enforces its own boundary even against an
# injected judge that bypasses `JudgeVerdict`.
_MIN_SCORE: Final = 1
_MAX_SCORE: Final = 5


class EvaluationError(RuntimeError):
    """Raised when the golden set cannot be loaded or parsed."""


class GoldenQuery(BaseModel):
    """One labelled evaluation case.

    Attributes:
        query: The user question to run through the pipeline.
        relevant: Control ID to graded-relevance label (`_MIN_LABEL`..`_MAX_LABEL`)
            for an on-topic query; empty for a fallback query.
        expect_fallback: True for a deliberately out-of-domain query that must
            return the safe fallback and has no relevant controls.
    """

    model_config = ConfigDict(frozen=True)

    query: str
    relevant: dict[str, int] = Field(default_factory=dict)
    expect_fallback: bool = False

    @model_validator(mode="after")
    def _check_shape(self) -> "GoldenQuery":
        """Enforce that a case is either on-topic-with-labels or fallback-without."""
        if self.expect_fallback and self.relevant:
            raise ValueError("a fallback query must not list relevant controls")
        if not self.expect_fallback and not self.relevant:
            raise ValueError("an on-topic query must list at least one relevant control")
        if len({control_id.lower() for control_id in self.relevant}) != len(self.relevant):
            # The metrics lower-case IDs, so case-duplicate keys would silently
            # collapse to one arbitrary label instead of failing the labeller.
            raise ValueError("relevant control IDs must be unique ignoring case")
        for control_id, label in self.relevant.items():
            if not _MIN_LABEL <= label <= _MAX_LABEL:
                raise ValueError(
                    f"relevance label for {control_id!r} is {label}, "
                    f"outside {_MIN_LABEL}..{_MAX_LABEL}"
                )
        return self


class GoldenSet(BaseModel):
    """The golden-set file: a list of cases plus any documentary fields."""

    model_config = ConfigDict(extra="ignore")

    queries: list[GoldenQuery]


class RetrievalMetrics(BaseModel):
    """Retrieval quality for one on-topic query.

    Recall is reported on two matching granularities. The *exact-ID* view
    credits only a retrieved control whose ID equals a labelled one. The
    *base-family* view also credits a retrieved enhancement whose base control
    was labelled — `ia-2.6` counts toward the `ia-2` need — because in NIST SP
    800-53 an enhancement is a more specific form of its base control, so
    retrieving it genuinely answers the information need. The exact-ID view is a
    strict lower bound; the base-family view is the fairer measure for this
    hierarchical catalog. Both are shown so the granularity effect is explicit,
    not hidden. NDCG stays exact-ID.

    Attributes:
        recall: Exact-ID recall of the grounding set against the qrels.
        family_recall: Base-family recall.
        ndcg: Normalized discounted cumulative gain at the report's `ndcg_k`,
            from the graded exact-ID qrels; rewards ranking strongly-relevant
            controls first.
    """

    model_config = ConfigDict(frozen=True)

    recall: float
    family_recall: float
    ndcg: float


class AnswerQuality(BaseModel):
    """LLM-judge scores for one answer, each an integer on a 1-5 scale.

    A field is None when that judge failed even after retries; the aggregate
    means skip None scores rather than treating them as zero.

    Attributes:
        faithfulness: How well the answer's claims are supported by the
            retrieved control texts (groundedness), or None if unjudged.
        answer_relevancy: How well the answer addresses the question, or None
            if unjudged.
    """

    model_config = ConfigDict(frozen=True)

    # The bounds re-enforce the judges' 1-5 scale at the harness's own boundary:
    # an injected judge that returned 0 or 42 would otherwise silently skew the
    # means that `JudgeVerdict`'s validation was supposed to protect.
    faithfulness: int | None = Field(ge=_MIN_SCORE, le=_MAX_SCORE)
    answer_relevancy: int | None = Field(ge=_MIN_SCORE, le=_MAX_SCORE)


class CitationCheck(BaseModel):
    """Whether an answer's citations were all actually retrieved.

    Attributes:
        grounded: Cited control IDs that were retrieved.
        invented: Cited control IDs that were not retrieved. Must be empty; a
            non-empty list is a grounding violation.
    """

    model_config = ConfigDict(frozen=True)

    grounded: list[str]
    invented: list[str]


class QueryEvaluation(BaseModel):
    """Everything measured for one golden query.

    Attributes:
        query: The question that was run.
        expect_fallback: Whether the golden set expected the safe fallback.
        is_fallback: Whether the pipeline actually returned the safe fallback.
        passed: For a fallback query, whether the fallback fired; for an on-topic
            query, whether it answered without inventing any citation.
        plan_steps: The Planner's search steps, for the transcript.
        retrieved: The deduplicated grounding set, best first.
        relevant: The golden qrels for this query (empty for a fallback query).
        retrieval: Retrieval metrics, or None for a fallback query.
        answer_quality: LLM-judge scores, or None when no answer was judged (a
            fallback query, or an on-topic query that itself fell back).
        citations: The citation check, or None for a fallback query.
        answer: The final answer text.
    """

    model_config = ConfigDict(frozen=True)

    query: str
    expect_fallback: bool
    is_fallback: bool
    passed: bool
    plan_steps: list[PlanStep]
    retrieved: list[RetrievedDocument]
    relevant: dict[str, int]
    retrieval: RetrievalMetrics | None
    answer_quality: AnswerQuality | None
    citations: CitationCheck | None
    answer: str


class AggregateMetrics(BaseModel):
    """Means and totals across the golden set.

    Attributes:
        on_topic_count: Number of on-topic queries.
        mean_recall: Mean exact-ID recall over on-topic queries.
        mean_family_recall: Mean base-family recall over on-topic queries.
        mean_ndcg: Mean NDCG over on-topic queries, at the report's `ndcg_k`.
        mean_faithfulness: Mean faithfulness over judged answers, or None if
            none were judged.
        mean_answer_relevancy: Mean answer relevancy over judged answers, or
            None if none were judged.
        total_invented_citations: Invented citations summed over on-topic queries.
        fallback_count: Number of fallback queries.
        fallback_passed: How many fallback queries returned the safe fallback.
    """

    model_config = ConfigDict(frozen=True)

    on_topic_count: int
    mean_recall: float
    mean_family_recall: float
    mean_ndcg: float
    mean_faithfulness: float | None
    mean_answer_relevancy: float | None
    total_invented_citations: int
    fallback_count: int
    fallback_passed: int


class EvaluationReport(BaseModel):
    """The full evaluation outcome: per-query results and their aggregate.

    Attributes:
        ndcg_k: The truncation depth every NDCG value in this report was
            computed at (the pipeline's `RETRIEVAL_TOP_K`), recorded so the
            transcripts stay self-describing.
        queries: The per-query evaluations, in golden-set order.
        aggregate: The rolled-up metrics.
    """

    model_config = ConfigDict(frozen=True)

    ndcg_k: int
    queries: list[QueryEvaluation]
    aggregate: AggregateMetrics


class PipelineLike(Protocol):
    """The one pipeline method the harness calls."""

    async def answer_query(self, query: str) -> PipelineResult:
        """Run the full pipeline for one question."""
        ...


class FaithfulnessJudge(Protocol):
    """An async judge of how faithfully an answer sticks to its evidence."""

    async def __call__(self, *, question: str, answer: str, context: str) -> int:
        """Score the answer 1-5 against the retrieved control texts."""
        ...


class AnswerRelevancyJudge(Protocol):
    """An async judge of how well an answer addresses the question."""

    async def __call__(self, *, question: str, answer: str) -> int:
        """Score the answer 1-5 against the question."""
        ...


def load_golden_set(path: Path) -> list[GoldenQuery]:
    """Load and validate the golden-set file.

    Args:
        path: Path to the golden-set JSON.

    Returns:
        The parsed queries, in file order.

    Raises:
        EvaluationError: If the file is missing, is not valid JSON, or does not
            match the golden-set schema.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise EvaluationError(f"golden set not found at {path}") from error
    except json.JSONDecodeError as error:
        raise EvaluationError(f"golden set at {path} is not valid JSON: {error}") from error
    try:
        return GoldenSet.model_validate(raw).queries
    except ValidationError as error:
        raise EvaluationError(f"golden set at {path} is invalid: {error}") from error


def exact_recall(relevant: Collection[str], retrieved: Iterable[str]) -> float:
    """Compute recall of a retrieved set against the qrels, matching IDs exactly.

    All IDs are lower-cased before comparison so a case difference between the
    golden set and the index cannot misreport a hit as a miss.

    Args:
        relevant: The relevant control IDs (the golden qrels).
        retrieved: The retrieved control IDs (the deduplicated grounding set).

    Returns:
        The fraction of relevant controls retrieved, or 0.0 when nothing is
        relevant.
    """
    relevant_set = {control_id.lower() for control_id in relevant}
    retrieved_set = {control_id.lower() for control_id in retrieved}
    return len(relevant_set & retrieved_set) / len(relevant_set) if relevant_set else 0.0


def base_control_id(control_id: str) -> str:
    """Reduce a control ID to its base control, dropping any enhancement suffix.

    Args:
        control_id: A control or control-enhancement ID, e.g. `ac-2` or `ac-2.1`.

    Returns:
        The base control ID, lower-cased, e.g. `ac-2`.
    """
    return control_id.lower().split(".", 1)[0]


def base_family_recall(relevant: Collection[str], retrieved: Iterable[str]) -> float:
    """Compute recall at base-control granularity.

    Every ID is reduced to its base control first, so a retrieved enhancement
    counts toward its base control's relevance: recall is over the distinct
    relevant base controls covered.

    Args:
        relevant: The relevant control IDs (the golden qrels).
        retrieved: The retrieved control IDs.

    Returns:
        The fraction of relevant base controls covered, or 0.0 when nothing is
        relevant.
    """
    relevant_bases = {base_control_id(control_id) for control_id in relevant}
    retrieved_bases = {base_control_id(control_id) for control_id in retrieved}
    return (
        len(relevant_bases & retrieved_bases) / len(relevant_bases) if relevant_bases else 0.0
    )


def ndcg_at_k(relevant: Mapping[str, int], retrieved: Sequence[str], k: int) -> float:
    """Compute graded NDCG@k of a ranking against the qrels, matching IDs exactly.

    Standard graded NDCG: the gain of the document at (1-based) rank `r` is its
    qrel label (0 if unlabelled), discounted by `1/log2(r+1)`; the ideal DCG
    ranks the query's own labels in descending order. Both rankings are
    truncated at `k`. IDs are lower-cased on both sides, matching the recall
    metrics. The caller passes the deduplicated grounding set, so a repeated ID
    cannot collect its gain twice.

    Args:
        relevant: Control ID to graded-relevance label (the golden qrels).
        retrieved: The retrieved control IDs, best first.
        k: The truncation depth.

    Returns:
        NDCG@k in [0, 1]; 0.0 when nothing was retrieved or nothing is relevant.
    """
    labels = {control_id.lower(): label for control_id, label in relevant.items()}
    gains = [labels.get(control_id.lower(), 0) for control_id in retrieved[:k]]
    ideal_gains = sorted(labels.values(), reverse=True)[:k]
    ideal_dcg = _dcg(ideal_gains)
    return _dcg(gains) / ideal_dcg if ideal_dcg else 0.0


def _dcg(gains: Sequence[int]) -> float:
    """Discounted cumulative gain of a gain vector, best first.

    Args:
        gains: The graded gain at each rank, rank 1 first.

    Returns:
        The DCG.
    """
    return sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))


def citation_check(answer: str, retrieved_ids: Iterable[str]) -> CitationCheck:
    """Split an answer's citations into those retrieved and those invented.

    Delegates to the Response Agent's own citation parser so the check matches
    the pipeline's own enforcement exactly.

    Args:
        answer: The answer prose.
        retrieved_ids: IDs of the controls the answer was allowed to cite.

    Returns:
        The grounded and invented citation lists.
    """
    grounded, invented = extract_citations(answer, retrieved_ids)
    return CitationCheck(grounded=grounded, invented=invented)


def _retrieval_metrics(
    documents: Sequence[RetrievedDocument], relevant: Mapping[str, int], ndcg_k: int
) -> RetrievalMetrics:
    """Compute the exact-ID, base-family, and graded retrieval metrics.

    Args:
        documents: The retrieved grounding set, best first.
        relevant: The golden qrels.
        ndcg_k: The NDCG truncation depth.

    Returns:
        The combined retrieval metrics.
    """
    retrieved_ids = [document.id for document in documents]
    return RetrievalMetrics(
        recall=exact_recall(relevant, retrieved_ids),
        family_recall=base_family_recall(relevant, retrieved_ids),
        ndcg=ndcg_at_k(relevant, retrieved_ids, ndcg_k),
    )


async def _score_with_retries(
    call: Callable[[], Awaitable[int]], metric: str, query: str
) -> int | None:
    """Run one judge call with retries, recording None if it keeps failing.

    Retries with exponential backoff absorb transient Azure OpenAI failures
    (rate limits, network blips). A judge that still fails must not crash the
    whole evaluation run: the failure is logged and its score recorded as None,
    which the aggregate means skip.

    Args:
        call: A no-argument coroutine factory performing the judge call.
        metric: The metric name, for the failure log line.
        query: The query being judged, for the failure log line.

    Returns:
        The judge's score, or None after the final failure.
    """
    score: int | None = None
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(_JUDGE_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=2, max=10),
            reraise=True,
        ):
            with attempt:
                score = await call()
    except Exception as error:
        # `exc_info` keeps the traceback: a run whose whole column comes back
        # "—" (say, a mis-deployed judge) must be diagnosable from the log.
        logger.warning(
            "LLM judge failed after retries",
            extra={"metric": metric, "query": query, "error": str(error)},
            exc_info=error,
        )
        return None
    return score


async def evaluate_query(
    pipeline: PipelineLike,
    faithfulness_judge: FaithfulnessJudge,
    answer_relevancy_judge: AnswerRelevancyJudge,
    golden: GoldenQuery,
    ndcg_k: int,
) -> QueryEvaluation:
    """Run one golden query through the pipeline and score the outcome.

    A fallback query is scored only on whether the safe fallback fired; no
    retrieval or answer-quality metric applies. An on-topic query is scored on
    retrieval (recall and NDCG), citation validity, and — unless it itself fell
    back, leaving no answer to judge — the two LLM-judge metrics. Each judge is
    retried independently and records None for its own metric on final failure,
    so one flaky judge cannot blank the other's score.

    Args:
        pipeline: The policy pipeline.
        faithfulness_judge: The faithfulness LLM judge.
        answer_relevancy_judge: The answer-relevancy LLM judge.
        golden: The labelled query to evaluate.
        ndcg_k: The NDCG truncation depth.

    Returns:
        The per-query evaluation.
    """
    result = await pipeline.answer_query(golden.query)
    response = result.response

    if golden.expect_fallback:
        return QueryEvaluation(
            query=golden.query,
            expect_fallback=True,
            is_fallback=response.is_fallback,
            passed=response.is_fallback,
            plan_steps=list(result.plan.steps),
            retrieved=list(result.documents),
            relevant={},
            retrieval=None,
            answer_quality=None,
            citations=None,
            answer=response.answer,
        )

    retrieval = _retrieval_metrics(result.documents, golden.relevant, ndcg_k)
    citations = citation_check(response.answer, [document.id for document in result.documents])

    answer_quality: AnswerQuality | None = None
    if not response.is_fallback and result.documents:
        context = format_documents(result.documents)
        answer_quality = AnswerQuality(
            faithfulness=await _score_with_retries(
                lambda: faithfulness_judge(
                    question=golden.query, answer=response.answer, context=context
                ),
                "faithfulness",
                golden.query,
            ),
            answer_relevancy=await _score_with_retries(
                lambda: answer_relevancy_judge(question=golden.query, answer=response.answer),
                "answer_relevancy",
                golden.query,
            ),
        )

    return QueryEvaluation(
        query=golden.query,
        expect_fallback=False,
        is_fallback=response.is_fallback,
        passed=not response.is_fallback and not citations.invented,
        plan_steps=list(result.plan.steps),
        retrieved=list(result.documents),
        relevant=dict(golden.relevant),
        retrieval=retrieval,
        answer_quality=answer_quality,
        citations=citations,
        answer=response.answer,
    )


def _mean(values: Sequence[float]) -> float:
    """Return the arithmetic mean, or 0.0 for an empty sequence.

    Args:
        values: The numbers to average.

    Returns:
        The mean, or 0.0 when there are no values.
    """
    return sum(values) / len(values) if values else 0.0


def aggregate_report(evaluations: Sequence[QueryEvaluation]) -> AggregateMetrics:
    """Roll up per-query evaluations into means and totals.

    Retrieval and answer-quality means cover on-topic queries only; the fallback
    tallies cover fallback queries only. Each answer-quality mean skips queries
    where that judge recorded None, and is itself None when no answer carried
    that score, so an unjudged run reports "not measured" rather than 0.

    Args:
        evaluations: The per-query evaluations.

    Returns:
        The aggregate metrics.
    """
    on_topic = [item for item in evaluations if not item.expect_fallback]
    fallback = [item for item in evaluations if item.expect_fallback]
    retrieval = [item.retrieval for item in on_topic if item.retrieval is not None]
    quality = [item.answer_quality for item in on_topic if item.answer_quality is not None]
    faithfulness = [item.faithfulness for item in quality if item.faithfulness is not None]
    answer_relevancy = [
        item.answer_relevancy for item in quality if item.answer_relevancy is not None
    ]
    return AggregateMetrics(
        on_topic_count=len(on_topic),
        mean_recall=_mean([metrics.recall for metrics in retrieval]),
        mean_family_recall=_mean([metrics.family_recall for metrics in retrieval]),
        mean_ndcg=_mean([metrics.ndcg for metrics in retrieval]),
        mean_faithfulness=_mean(faithfulness) if faithfulness else None,
        mean_answer_relevancy=_mean(answer_relevancy) if answer_relevancy else None,
        total_invented_citations=sum(
            len(item.citations.invented) for item in on_topic if item.citations is not None
        ),
        fallback_count=len(fallback),
        fallback_passed=sum(1 for item in fallback if item.is_fallback),
    )


async def run_evaluation(
    pipeline: PipelineLike,
    faithfulness_judge: FaithfulnessJudge,
    answer_relevancy_judge: AnswerRelevancyJudge,
    golden_set: Sequence[GoldenQuery],
    ndcg_k: int,
) -> EvaluationReport:
    """Evaluate every golden query and aggregate the results.

    Queries run one at a time: the run is a batch job, not a latency-sensitive
    path, and serialising keeps the audit log a clean per-query trace.

    Args:
        pipeline: The policy pipeline.
        faithfulness_judge: The faithfulness LLM judge.
        answer_relevancy_judge: The answer-relevancy LLM judge.
        golden_set: The labelled queries to evaluate.
        ndcg_k: The NDCG truncation depth, normally the pipeline's
            `RETRIEVAL_TOP_K`.

    Returns:
        The full evaluation report.
    """
    evaluations = [
        await evaluate_query(pipeline, faithfulness_judge, answer_relevancy_judge, golden, ndcg_k)
        for golden in golden_set
    ]
    return EvaluationReport(
        ndcg_k=ndcg_k, queries=evaluations, aggregate=aggregate_report(evaluations)
    )


def _format_optional(value: float | None, digits: int) -> str:
    """Render an optional metric, or an em dash when it was not measured.

    Args:
        value: The metric value, or None.
        digits: Decimal places to show.

    Returns:
        The rounded value, or "—".
    """
    return f"{value:.{digits}f}" if value is not None else "—"


def _format_score(score: int | None) -> str:
    """Render an optional judge score out of 5, or an em dash when unjudged.

    Args:
        score: The 1-5 score, or None.

    Returns:
        `"<score>/5"`, or "—".
    """
    return f"{score}/5" if score is not None else "—"


def _aggregate_table(aggregate: AggregateMetrics, ndcg_k: int) -> list[str]:
    """Render the aggregate metrics as a Markdown table.

    Args:
        aggregate: The rolled-up metrics.
        ndcg_k: The NDCG truncation depth, for the row label.

    Returns:
        The table's lines.
    """
    return [
        "## Aggregate",
        "",
        f"On-topic queries: {aggregate.on_topic_count} · "
        f"Fallback queries: {aggregate.fallback_passed}/{aggregate.fallback_count} "
        "returned the safe fallback · "
        f"Invented citations: {aggregate.total_invented_citations}",
        "",
        "| Metric | Exact-ID | Base-family |",
        "|---|---|---|",
        f"| Recall | {aggregate.mean_recall:.3f} | {aggregate.mean_family_recall:.3f} |",
        f"| NDCG@{ndcg_k} | {aggregate.mean_ndcg:.3f} | — |",
        f"| Faithfulness (1-5) | {_format_optional(aggregate.mean_faithfulness, 2)} | — |",
        "| Answer relevancy (1-5) | "
        f"{_format_optional(aggregate.mean_answer_relevancy, 2)} | — |",
    ]


def _format_retrieved(documents: Sequence[RetrievedDocument]) -> str:
    """Render a retrieved grounding set as `id (score)` pairs, or `(none)`.

    Args:
        documents: The retrieved controls, best first.

    Returns:
        A comma-separated list, or "(none)" when nothing was retrieved.
    """
    return ", ".join(f"{doc.id} ({doc.score:.2f})" for doc in documents) or "(none)"


def _query_section(index: int, item: QueryEvaluation, ndcg_k: int) -> list[str]:
    """Render one query's evaluation as a Markdown section.

    Args:
        index: 1-based position of the query in the golden set.
        item: The query's evaluation.
        ndcg_k: The NDCG truncation depth, for the metric label.

    Returns:
        The section's lines.
    """
    if item.expect_fallback:
        verdict = "returned the safe fallback ✓" if item.is_fallback else "did NOT fall back ✗"
        lines = [
            f"### Q{index}: {item.query} (out-of-domain)",
            "",
            f"- **Expected:** safe fallback — {verdict}",
        ]
        if not item.is_fallback:
            # A breached fallback answered from controls; show which ones it
            # wrongly retrieved so the failure is auditable, not merely flagged.
            lines.append(
                f"- **Retrieved ({len(item.retrieved)}):** {_format_retrieved(item.retrieved)}"
            )
        lines.append(f"- **Answer:** {item.answer}")
        return lines

    plan = "; ".join(step.search_query for step in item.plan_steps)
    retrieved = _format_retrieved(item.retrieved)
    qrels = ", ".join(f"{control_id}({label})" for control_id, label in item.relevant.items())
    lines = [
        f"### Q{index}: {item.query}",
        "",
        f"- **Plan:** {plan}",
        f"- **Retrieved ({len(item.retrieved)}):** {retrieved}",
        f"- **Relevant (qrels):** {qrels}",
    ]
    if item.retrieval is not None:
        metrics = item.retrieval
        lines.append(
            f"- **Recall:** {metrics.recall:.3f} exact-ID / "
            f"{metrics.family_recall:.3f} base-family · "
            f"**NDCG@{ndcg_k}:** {metrics.ndcg:.3f}"
        )
    if item.answer_quality is not None:
        quality = item.answer_quality
        lines.append(
            f"- **Faithfulness:** {_format_score(quality.faithfulness)} · "
            f"**Answer relevancy:** {_format_score(quality.answer_relevancy)}"
        )
    if item.citations is not None:
        grounded = ", ".join(item.citations.grounded) or "none"
        invented = ", ".join(item.citations.invented) or "none"
        lines.append(f"- **Citations:** grounded [{grounded}]; invented [{invented}]")
    lines.append(f"- **Answer:** {item.answer}")
    return lines


def build_markdown_report(report: EvaluationReport) -> str:
    """Render an evaluation report as a Markdown document.

    Pure and deterministic — it carries no timestamp — so the committed sample
    report diffs only when a metric changes, and the renderer is unit-testable.

    Args:
        report: The evaluation report.

    Returns:
        The Markdown document.
    """
    k = report.ndcg_k
    lines = [
        "# Evaluation report",
        "",
        "Retrieval and answer quality of the NIST SP 800-53 policy pipeline over "
        "the hand-labeled golden set (`evaluation/golden_set.json`). Recall and "
        f"graded NDCG@{k} are computed directly from the golden qrels — pure, "
        "deterministic math with no external evaluator. Faithfulness and answer "
        "relevancy are LLM-judge scores (integer 1-5) from two PydanticAI judge "
        "agents on the same Azure OpenAI deployment the pipeline uses.",
        "",
        "Recall is shown two ways. **Exact-ID** credits only a retrieved control "
        "whose ID matches a labelled one. **Base-family** also credits a "
        "retrieved enhancement whose base control was labelled — `ia-2.6` counts "
        "toward the `ia-2` need — because a NIST SP 800-53 enhancement is a more "
        "specific form of its base control, so retrieving it genuinely answers "
        "the need. The exact-ID column is a strict lower bound; the base-family "
        f"column is the fairer measure for this hierarchical catalog. NDCG@{k} "
        "stays exact-ID, truncated at the pipeline's own top-k; it is computed "
        "in-house and is **not comparable** to the NDCG@3 the Azure AI "
        "`DocumentRetrievalEvaluator` reported in earlier committed runs.",
        "",
        *_aggregate_table(report.aggregate, k),
        "",
        "## Per-query results",
    ]
    for index, item in enumerate(report.queries, start=1):
        lines.append("")
        lines.extend(_query_section(index, item, k))
    return "\n".join(lines) + "\n"
