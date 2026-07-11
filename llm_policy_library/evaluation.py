"""Offline evaluation of the policy pipeline against a hand-labeled golden set.

The golden set (`evaluation/golden_set.json`) pairs each query with the control
IDs a compliance reviewer would expect back, graded 2 (directly on point) or 1
(supporting). Two deliberately out-of-domain queries must return the safe
fallback. This module turns that golden set and a live pipeline into an
`EvaluationReport`, and renders it as Markdown.

Four things are measured, and only one of them needs a chat model:

* **Retrieval, set-based** — precision, recall, and F1 over the retrieved
  grounding set against the golden qrels. These are pure set arithmetic. The
  user asked for precision/recall specifically (decision D5); the Azure AI
  Evaluation `DocumentRetrievalEvaluator` does *not* compute them — it reports
  NDCG/XDCG/fidelity/holes — so they are computed here directly.
* **Retrieval, graded** — NDCG@3, XDCG@3, fidelity, and holes from the injected
  `DocumentRetrievalEvaluator`, which is pure local math over the graded qrels
  (no network), rewarding a run that ranks the strongly-relevant controls above
  the weakly-relevant and the irrelevant.
* **Answer quality** — groundedness and relevance from LLM-judge evaluators, run
  through the same Azure OpenAI deployment the pipeline uses.
* **Citation validity** — every inline `[control-id]` in the answer must be a
  control that was actually retrieved. This reuses the Response Agent's own
  citation parser, so the check matches what the pipeline itself enforced; an
  invented citation here is a grounding regression.

The evaluators are injected as `Protocol`s rather than imported, so this module
stays independent of `azure-ai-evaluation` (a heavy import) and the whole harness
is exercised in unit tests with plain fakes. `evaluation/run_eval.py` is the thin
runner that constructs the real evaluators and a live pipeline and calls in here.
"""

import json
import logging
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from llm_policy_library.agents.response import extract_citations, format_documents
from llm_policy_library.models import PipelineResult, PlanStep, RetrievedDocument

logger = logging.getLogger(__name__)

# DocumentRetrievalEvaluator's k is fixed at 3, so its metric keys are suffixed
# `@3`. Named here so the extraction and the report agree on one source.
DOC_RETRIEVAL_K = 3
_NDCG_KEY = f"ndcg@{DOC_RETRIEVAL_K}"
_XDCG_KEY = f"xdcg@{DOC_RETRIEVAL_K}"

# The graded-relevance labels the golden set may assign. 0 is excluded on
# purpose: a control worth listing is at least weakly relevant, so a 0 would be a
# labelling mistake, not a judgement.
_MIN_LABEL = 1
_MAX_LABEL = 4


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
    """Retrieval quality for one on-topic query, on two matching granularities.

    Precision, recall, and F1 are reported twice. The *exact-ID* view credits
    only a retrieved control whose ID equals a labelled one. The *base-family*
    view also credits a retrieved enhancement whose base control was labelled —
    `ia-2.6` counts toward the `ia-2` need — because in NIST SP 800-53 an
    enhancement is a more specific form of its base control, so retrieving it
    genuinely answers the information need. The exact-ID view is a strict lower
    bound; the base-family view is the fairer measure for this hierarchical
    catalog. Both are shown so the granularity effect is explicit, not hidden.

    Attributes:
        precision: Exact-ID precision.
        recall: Exact-ID recall.
        f1: Exact-ID F1.
        family_precision: Base-family precision.
        family_recall: Base-family recall.
        family_f1: Base-family F1.
        ndcg_at_3: Normalized discounted cumulative gain at 3, from the graded
            exact-ID qrels; rewards ranking strongly-relevant controls first.
        xdcg_at_3: Extended DCG at 3. The evaluator's scale runs to 100 only when
            graded labels reach its maximum of 4; with this golden set's 1-2
            labels the achievable ceiling is about 50 (a perfectly-ranked query
            scores ~50, not 100), so read it against that, not against 100.
        fidelity: The evaluator's fidelity metric over the full result set.
        holes: Retrieved controls carrying no ground-truth label at all.
    """

    model_config = ConfigDict(frozen=True)

    precision: float
    recall: float
    f1: float
    family_precision: float
    family_recall: float
    family_f1: float
    ndcg_at_3: float
    xdcg_at_3: float
    fidelity: float
    holes: int


class AnswerQuality(BaseModel):
    """LLM-judge scores for one answer, each on a 1-5 scale.

    Attributes:
        groundedness: How well the answer is supported by the retrieved controls.
        relevance: How well the answer addresses the question.
    """

    model_config = ConfigDict(frozen=True)

    groundedness: float
    relevance: float


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
        mean_precision: Mean exact-ID precision over on-topic queries.
        mean_recall: Mean exact-ID recall over on-topic queries.
        mean_f1: Mean exact-ID F1 over on-topic queries.
        mean_family_precision: Mean base-family precision over on-topic queries.
        mean_family_recall: Mean base-family recall over on-topic queries.
        mean_family_f1: Mean base-family F1 over on-topic queries.
        mean_ndcg_at_3: Mean NDCG@3 over on-topic queries.
        mean_xdcg_at_3: Mean XDCG@3 over on-topic queries.
        mean_fidelity: Mean fidelity over on-topic queries.
        mean_groundedness: Mean groundedness over judged answers, or None if
            none were judged.
        mean_relevance: Mean relevance over judged answers, or None if none
            were judged.
        total_invented_citations: Invented citations summed over on-topic queries.
        fallback_count: Number of fallback queries.
        fallback_passed: How many fallback queries returned the safe fallback.
    """

    model_config = ConfigDict(frozen=True)

    on_topic_count: int
    mean_precision: float
    mean_recall: float
    mean_f1: float
    mean_family_precision: float
    mean_family_recall: float
    mean_family_f1: float
    mean_ndcg_at_3: float
    mean_xdcg_at_3: float
    mean_fidelity: float
    mean_groundedness: float | None
    mean_relevance: float | None
    total_invented_citations: int
    fallback_count: int
    fallback_passed: int


class EvaluationReport(BaseModel):
    """The full evaluation outcome: per-query results and their aggregate."""

    model_config = ConfigDict(frozen=True)

    queries: list[QueryEvaluation]
    aggregate: AggregateMetrics


class PipelineLike(Protocol):
    """The one pipeline method the harness calls."""

    async def answer_query(self, query: str) -> PipelineResult:
        """Run the full pipeline for one question."""
        ...


class DocumentRetrievalEval(Protocol):
    """An Azure AI `DocumentRetrievalEvaluator`-shaped callable."""

    def __call__(
        self,
        *,
        retrieval_ground_truth: list[dict[str, Any]],
        retrieved_documents: list[dict[str, Any]],
    ) -> Mapping[str, Any]:
        """Score retrieved documents against graded ground-truth judgements."""
        ...


class GroundednessEval(Protocol):
    """An Azure AI `GroundednessEvaluator`-shaped callable."""

    def __call__(self, *, query: str, response: str, context: str) -> Mapping[str, Any]:
        """Judge how well a response is grounded in the given context."""
        ...


class RelevanceEval(Protocol):
    """An Azure AI `RelevanceEvaluator`-shaped callable."""

    def __call__(self, *, query: str, response: str) -> Mapping[str, Any]:
        """Judge how well a response addresses the query."""
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


def precision_recall_f1(
    relevant: Collection[str], retrieved: Sequence[str]
) -> tuple[float, float, float]:
    """Compute precision, recall, and F1 of a retrieved set against the qrels.

    All IDs are lower-cased before comparison so a case difference between the
    golden set and the index cannot misreport a hit as a miss. `retrieved` is
    treated as a set of distinct IDs — the caller passes the already-deduplicated
    grounding set, so this only guards against an accidental repeat, whereas
    `base_family_precision_recall_f1` deliberately counts each retrieved document.

    Args:
        relevant: The relevant control IDs (the golden qrels).
        retrieved: The retrieved control IDs (the deduplicated grounding set).

    Returns:
        `(precision, recall, f1)`. Precision is 0 when nothing was retrieved,
        recall is 0 when nothing is relevant, and F1 is 0 when either is 0.
    """
    relevant_set = {control_id.lower() for control_id in relevant}
    retrieved_set = {control_id.lower() for control_id in retrieved}
    hits = len(relevant_set & retrieved_set)
    precision = hits / len(retrieved_set) if retrieved_set else 0.0
    recall = hits / len(relevant_set) if relevant_set else 0.0
    return precision, recall, _f1(precision, recall)


def base_control_id(control_id: str) -> str:
    """Reduce a control ID to its base control, dropping any enhancement suffix.

    Args:
        control_id: A control or control-enhancement ID, e.g. `ac-2` or `ac-2.1`.

    Returns:
        The base control ID, lower-cased, e.g. `ac-2`.
    """
    return control_id.lower().split(".", 1)[0]


def base_family_precision_recall_f1(
    relevant: Collection[str], retrieved: Sequence[str]
) -> tuple[float, float, float]:
    """Compute precision, recall, and F1 at base-control granularity.

    Every ID is reduced to its base control first, so a retrieved enhancement
    counts toward its base control's relevance. Precision judges each retrieved
    document (so returning several enhancements of one relevant base is credited
    once each); recall is over the distinct relevant base controls covered.

    Args:
        relevant: The relevant control IDs (the golden qrels).
        retrieved: The retrieved control IDs.

    Returns:
        `(precision, recall, f1)` at base-control granularity.
    """
    relevant_bases = {base_control_id(control_id) for control_id in relevant}
    retrieved_bases = [base_control_id(control_id) for control_id in retrieved]
    matched = [base for base in retrieved_bases if base in relevant_bases]
    precision = len(matched) / len(retrieved_bases) if retrieved_bases else 0.0
    recall = len(set(matched)) / len(relevant_bases) if relevant_bases else 0.0
    return precision, recall, _f1(precision, recall)


def _f1(precision: float, recall: float) -> float:
    """Harmonic mean of precision and recall, defined as 0 when both are 0.

    Args:
        precision: The precision.
        recall: The recall.

    Returns:
        The F1 score.
    """
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def to_ground_truth(relevant: Mapping[str, int]) -> list[dict[str, Any]]:
    """Render the golden qrels as `DocumentRetrievalEvaluator` ground truth.

    Args:
        relevant: Control ID to graded-relevance label.

    Returns:
        One `{document_id, query_relevance_label}` entry per relevant control.
        IDs are lower-cased to match `to_retrieved_documents`, since the
        evaluator pairs ground truth to results by exact `document_id` string.
    """
    return [
        {"document_id": control_id.lower(), "query_relevance_label": label}
        for control_id, label in relevant.items()
    ]


def to_retrieved_documents(documents: Sequence[RetrievedDocument]) -> list[dict[str, Any]]:
    """Render the grounding set as `DocumentRetrievalEvaluator` input.

    The evaluator uses `relevance_score` only to order the documents, so the
    pipeline's own retrieval score serves directly as the ranking key.

    Args:
        documents: The retrieved controls, best first.

    Returns:
        One `{document_id, relevance_score}` entry per retrieved control. IDs are
        lower-cased so ground-truth pairing is case-insensitive, matching the
        set metrics' explicit case-folding.
    """
    return [{"document_id": doc.id.lower(), "relevance_score": doc.score} for doc in documents]


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
    documents: Sequence[RetrievedDocument],
    relevant: Mapping[str, int],
    doc_eval: DocumentRetrievalEval,
) -> RetrievalMetrics:
    """Compute the exact-ID, base-family, and graded retrieval metrics.

    The graded evaluator is skipped when nothing was retrieved, since its graded
    metrics are all zero in that case and it need not be called.

    Args:
        documents: The retrieved grounding set.
        relevant: The golden qrels.
        doc_eval: The graded document-retrieval evaluator.

    Returns:
        The combined retrieval metrics.
    """
    retrieved_ids = [document.id for document in documents]
    precision, recall, f1 = precision_recall_f1(relevant, retrieved_ids)
    family_precision, family_recall, family_f1 = base_family_precision_recall_f1(
        relevant, retrieved_ids
    )
    ndcg_at_3, xdcg_at_3, fidelity, holes = _graded_metrics(documents, relevant, doc_eval)
    return RetrievalMetrics(
        precision=precision,
        recall=recall,
        f1=f1,
        family_precision=family_precision,
        family_recall=family_recall,
        family_f1=family_f1,
        ndcg_at_3=ndcg_at_3,
        xdcg_at_3=xdcg_at_3,
        fidelity=fidelity,
        holes=holes,
    )


def _graded_metrics(
    documents: Sequence[RetrievedDocument],
    relevant: Mapping[str, int],
    doc_eval: DocumentRetrievalEval,
) -> tuple[float, float, float, int]:
    """Read NDCG@3, XDCG@3, fidelity, and holes from the graded evaluator.

    Skipped when nothing was retrieved: the evaluator's graded metrics are all
    zero then, and an empty result set need not be scored. When it *is* called,
    its keys are read strictly — a future `azure-ai-evaluation` key rename must
    fail loudly here rather than silently zero every graded metric, which would
    be the worst failure mode for an evaluation harness.

    Args:
        documents: The retrieved grounding set.
        relevant: The golden qrels.
        doc_eval: The graded document-retrieval evaluator.

    Returns:
        `(ndcg_at_3, xdcg_at_3, fidelity, holes)`.
    """
    if not documents:
        return 0.0, 0.0, 0.0, 0
    result = doc_eval(
        retrieval_ground_truth=to_ground_truth(relevant),
        retrieved_documents=to_retrieved_documents(documents),
    )
    properties = result["document_retrieval_properties"]
    return (
        float(properties[_NDCG_KEY]),
        float(properties[_XDCG_KEY]),
        float(properties["fidelity"]),
        int(properties["holes"]),
    )


async def evaluate_query(
    pipeline: PipelineLike,
    doc_eval: DocumentRetrievalEval,
    groundedness_eval: GroundednessEval,
    relevance_eval: RelevanceEval,
    golden: GoldenQuery,
) -> QueryEvaluation:
    """Run one golden query through the pipeline and score the outcome.

    A fallback query is scored only on whether the safe fallback fired; no
    retrieval or answer-quality metric applies. An on-topic query is scored on
    retrieval (set and graded), citation validity, and — unless it itself fell
    back, leaving no answer to judge — the two LLM-judge metrics.

    Args:
        pipeline: The policy pipeline.
        doc_eval: The graded document-retrieval evaluator.
        groundedness_eval: The groundedness LLM-judge.
        relevance_eval: The relevance LLM-judge.
        golden: The labelled query to evaluate.

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

    retrieval = _retrieval_metrics(result.documents, golden.relevant, doc_eval)
    citations = citation_check(
        response.answer, [document.id for document in result.documents]
    )

    answer_quality: AnswerQuality | None = None
    if not response.is_fallback and result.documents:
        context = format_documents(result.documents)
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True
            ):
                with attempt:
                    groundedness = groundedness_eval(
                        query=golden.query, response=response.answer, context=context
                    )
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True
            ):
                with attempt:
                    relevance = relevance_eval(query=golden.query, response=response.answer)
            answer_quality = AnswerQuality(
                groundedness=float(groundedness["groundedness"]),
                relevance=float(relevance["relevance"]),
            )
        except Exception as e:
            logger.warning(
                "LLM judge failed after retries",
                extra={"query": golden.query, "error": str(e)},
            )
            answer_quality = None

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
    tallies cover fallback queries only. Answer-quality means are None when no
    answer was judged, so an empty run reports "not measured" rather than 0.

    Args:
        evaluations: The per-query evaluations.

    Returns:
        The aggregate metrics.
    """
    on_topic = [item for item in evaluations if not item.expect_fallback]
    fallback = [item for item in evaluations if item.expect_fallback]
    retrieval = [item.retrieval for item in on_topic if item.retrieval is not None]
    quality = [item.answer_quality for item in on_topic if item.answer_quality is not None]
    return AggregateMetrics(
        on_topic_count=len(on_topic),
        mean_precision=_mean([metrics.precision for metrics in retrieval]),
        mean_recall=_mean([metrics.recall for metrics in retrieval]),
        mean_f1=_mean([metrics.f1 for metrics in retrieval]),
        mean_family_precision=_mean([metrics.family_precision for metrics in retrieval]),
        mean_family_recall=_mean([metrics.family_recall for metrics in retrieval]),
        mean_family_f1=_mean([metrics.family_f1 for metrics in retrieval]),
        mean_ndcg_at_3=_mean([metrics.ndcg_at_3 for metrics in retrieval]),
        mean_xdcg_at_3=_mean([metrics.xdcg_at_3 for metrics in retrieval]),
        mean_fidelity=_mean([metrics.fidelity for metrics in retrieval]),
        mean_groundedness=(
            _mean([item.groundedness for item in quality]) if quality else None
        ),
        mean_relevance=_mean([item.relevance for item in quality]) if quality else None,
        total_invented_citations=sum(
            len(item.citations.invented) for item in on_topic if item.citations is not None
        ),
        fallback_count=len(fallback),
        fallback_passed=sum(1 for item in fallback if item.is_fallback),
    )


async def run_evaluation(
    pipeline: PipelineLike,
    doc_eval: DocumentRetrievalEval,
    groundedness_eval: GroundednessEval,
    relevance_eval: RelevanceEval,
    golden_set: Sequence[GoldenQuery],
) -> EvaluationReport:
    """Evaluate every golden query and aggregate the results.

    Queries run one at a time: the run is a batch job, not a latency-sensitive
    path, and serialising keeps the audit log a clean per-query trace.

    Args:
        pipeline: The policy pipeline.
        doc_eval: The graded document-retrieval evaluator.
        groundedness_eval: The groundedness LLM-judge.
        relevance_eval: The relevance LLM-judge.
        golden_set: The labelled queries to evaluate.

    Returns:
        The full evaluation report.
    """
    evaluations = [
        await evaluate_query(pipeline, doc_eval, groundedness_eval, relevance_eval, golden)
        for golden in golden_set
    ]
    return EvaluationReport(queries=evaluations, aggregate=aggregate_report(evaluations))


def _format_optional(value: float | None, digits: int) -> str:
    """Render an optional metric, or an em dash when it was not measured.

    Args:
        value: The metric value, or None.
        digits: Decimal places to show.

    Returns:
        The rounded value, or "—".
    """
    return f"{value:.{digits}f}" if value is not None else "—"


def _aggregate_table(aggregate: AggregateMetrics) -> list[str]:
    """Render the aggregate metrics as a Markdown table.

    Args:
        aggregate: The rolled-up metrics.

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
        f"| Precision | {aggregate.mean_precision:.3f} | "
        f"{aggregate.mean_family_precision:.3f} |",
        f"| Recall | {aggregate.mean_recall:.3f} | {aggregate.mean_family_recall:.3f} |",
        f"| F1 | {aggregate.mean_f1:.3f} | {aggregate.mean_family_f1:.3f} |",
        f"| NDCG@{DOC_RETRIEVAL_K} | {aggregate.mean_ndcg_at_3:.3f} | — |",
        f"| XDCG@{DOC_RETRIEVAL_K} | {aggregate.mean_xdcg_at_3:.1f} | — |",
        f"| Fidelity | {aggregate.mean_fidelity:.3f} | — |",
        f"| Groundedness (1-5) | {_format_optional(aggregate.mean_groundedness, 2)} | — |",
        f"| Relevance (1-5) | {_format_optional(aggregate.mean_relevance, 2)} | — |",
    ]


def _format_retrieved(documents: Sequence[RetrievedDocument]) -> str:
    """Render a retrieved grounding set as `id (score)` pairs, or `(none)`.

    Args:
        documents: The retrieved controls, best first.

    Returns:
        A comma-separated list, or "(none)" when nothing was retrieved.
    """
    return ", ".join(f"{doc.id} ({doc.score:.2f})" for doc in documents) or "(none)"


def _query_section(index: int, item: QueryEvaluation) -> list[str]:
    """Render one query's evaluation as a Markdown section.

    Args:
        index: 1-based position of the query in the golden set.
        item: The query's evaluation.

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
    qrels = ", ".join(
        f"{control_id}({label})" for control_id, label in item.relevant.items()
    )
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
            f"- **P/R/F1 (exact-ID):** {metrics.precision:.3f} / "
            f"{metrics.recall:.3f} / {metrics.f1:.3f}"
        )
        lines.append(
            f"- **P/R/F1 (base-family):** {metrics.family_precision:.3f} / "
            f"{metrics.family_recall:.3f} / {metrics.family_f1:.3f}"
        )
        lines.append(
            f"- **NDCG@{DOC_RETRIEVAL_K}:** {metrics.ndcg_at_3:.3f} · "
            f"**XDCG@{DOC_RETRIEVAL_K}:** {metrics.xdcg_at_3:.1f} · "
            f"**Fidelity:** {metrics.fidelity:.3f} · **Holes:** {metrics.holes}"
        )
    if item.answer_quality is not None:
        quality = item.answer_quality
        lines.append(
            f"- **Groundedness:** {quality.groundedness:.1f}/5 · "
            f"**Relevance:** {quality.relevance:.1f}/5"
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
    lines = [
        "# Evaluation report",
        "",
        "Retrieval and answer quality of the NIST SP 800-53 policy pipeline over "
        "the hand-labeled golden set (`evaluation/golden_set.json`). Precision, "
        "recall, and F1 are computed directly from the qrels; NDCG/XDCG/fidelity "
        "come from the Azure AI `DocumentRetrievalEvaluator`; groundedness and "
        "relevance are LLM-judge scores from the same Azure OpenAI deployment the "
        "pipeline uses.",
        "",
        "Precision/recall/F1 are shown two ways. **Exact-ID** credits only a "
        "retrieved control whose ID matches a labelled one. **Base-family** also "
        "credits a retrieved enhancement whose base control was labelled — "
        "`ia-2.6` counts toward the `ia-2` need — because a NIST SP 800-53 "
        "enhancement is a more specific form of its base control, so retrieving "
        "it genuinely answers the need. The exact-ID column is a strict lower "
        "bound; the base-family column is the fairer measure for this hierarchical "
        "catalog. The graded NDCG/XDCG/fidelity stay exact-ID. Note the golden "
        "set grades relevance 1-2, so XDCG@3's achievable ceiling is ~50, not "
        "100 (a perfectly-ranked query scores ~50).",
        "",
        *_aggregate_table(report.aggregate),
        "",
        "## Per-query results",
    ]
    for index, item in enumerate(report.queries, start=1):
        lines.append("")
        lines.extend(_query_section(index, item))
    return "\n".join(lines) + "\n"
