"""Unit tests for `llm_policy_library.evaluation`."""

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

import llm_policy_library.evaluation as testee
from llm_policy_library.models import (
    GroundedResponse,
    PipelineResult,
    PlanStep,
    QueryPlan,
    RetrievalResult,
    RetrievedDocument,
)


def make_document(control_id: str, score: float = 2.5) -> RetrievedDocument:
    """Build a retrieved document.

    Args:
        control_id: The control's OSCAL ID.
        score: Its relevance score.

    Returns:
        The document.
    """
    return RetrievedDocument(
        id=control_id,
        title=f"Title of {control_id}",
        description=f"Statement of {control_id}",
        category="Access Control",
        score=score,
    )


def make_result(
    query: str,
    documents: list[RetrievedDocument],
    answer: str,
    citations: list[str],
    is_fallback: bool = False,
) -> PipelineResult:
    """Build a pipeline result the fake pipeline returns.

    Args:
        query: The user's question.
        documents: The grounding set.
        answer: The answer prose.
        citations: The grounded citations.
        is_fallback: Whether the safe fallback fired.

    Returns:
        The result.
    """
    plan = QueryPlan(
        original_query=query, steps=[PlanStep(search_query=query, purpose="find controls")]
    )
    return PipelineResult(
        plan=plan,
        results=[RetrievalResult(step=plan.steps[0], documents=documents)],
        documents=documents,
        response=GroundedResponse(answer=answer, citations=citations, is_fallback=is_fallback),
    )


class FakePipeline:
    """A pipeline that returns a canned result per query."""

    def __init__(self, results: dict[str, PipelineResult]) -> None:
        """Record the query-to-result map and the order queries arrive in.

        Args:
            results: The result to return for each query.
        """
        self._results = results
        self.queries: list[str] = []

    async def answer_query(self, query: str) -> PipelineResult:
        """Return the canned result for `query`.

        Args:
            query: The user's question.

        Returns:
            The canned result.
        """
        self.queries.append(query)
        return self._results[query]


def make_doc_eval(
    ndcg: float = 0.9, xdcg: float = 80.0, fidelity: float = 0.85, holes: int = 1
) -> testee.DocumentRetrievalEval:
    """Build a fake document-retrieval evaluator returning fixed graded metrics.

    Args:
        ndcg: NDCG@3 to report.
        xdcg: XDCG@3 to report.
        fidelity: Fidelity to report.
        holes: Holes to report.

    Returns:
        The fake evaluator.
    """

    def _eval(
        *, retrieval_ground_truth: list[dict[str, Any]], retrieved_documents: list[dict[str, Any]]
    ) -> Mapping[str, Any]:
        return {
            "document_retrieval_properties": {
                "ndcg@3": ndcg,
                "xdcg@3": xdcg,
                "fidelity": fidelity,
                "holes": holes,
            }
        }

    return _eval


def make_groundedness(score: float = 5.0) -> testee.GroundednessEval:
    """Build a fake groundedness judge returning a fixed score.

    Args:
        score: The score to report.

    Returns:
        The fake judge.
    """

    def _eval(*, query: str, response: str, context: str) -> Mapping[str, Any]:
        return {"groundedness": score}

    return _eval


def make_relevance(score: float = 4.0) -> testee.RelevanceEval:
    """Build a fake relevance judge returning a fixed score.

    Args:
        score: The score to report.

    Returns:
        The fake judge.
    """

    def _eval(*, query: str, response: str) -> Mapping[str, Any]:
        return {"relevance": score}

    return _eval


def exploding_doc_eval(
    *, retrieval_ground_truth: list[dict[str, Any]], retrieved_documents: list[dict[str, Any]]
) -> Mapping[str, Any]:
    """A document-retrieval evaluator that fails if called at all."""
    raise AssertionError("doc_eval must not be called when nothing was retrieved")


def write_golden(path: Path, payload: Any) -> Path:
    """Write a golden-set payload to a file and return its path.

    Args:
        path: The file to write.
        payload: The JSON-serialisable content.

    Returns:
        The written path.
    """
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_golden_query_rejects_a_fallback_case_that_lists_controls() -> None:
    """A fallback query has no relevant controls; listing some is a labelling contradiction."""
    with pytest.raises(ValueError, match="must not list relevant controls"):
        testee.GoldenQuery(query="q", relevant={"ac-2": 2}, expect_fallback=True)


def test_golden_query_rejects_an_on_topic_case_with_no_controls() -> None:
    """An on-topic query with no qrels can neither be scored for recall nor labelled at all."""
    with pytest.raises(ValueError, match="at least one relevant control"):
        testee.GoldenQuery(query="q")


def test_golden_query_rejects_a_label_out_of_range() -> None:
    """A label outside 1..4 is a typo that would silently distort NDCG if admitted."""
    with pytest.raises(ValueError, match="outside 1..4"):
        testee.GoldenQuery(query="q", relevant={"ac-2": 5})


def test_load_golden_set_parses_queries_in_file_order(tmp_path: Path) -> None:
    """The golden set defines the evaluation cases; their order is the report's order."""
    path = write_golden(
        tmp_path / "golden.json",
        {
            "description": "ignored documentation field",
            "queries": [
                {"query": "access control", "relevant": {"ac-2": 2, "ac-3": 1}},
                {"query": "off topic", "expect_fallback": True},
            ],
        },
    )

    queries = testee.load_golden_set(path)

    assert [item.query for item in queries] == ["access control", "off topic"]
    assert queries[0].relevant == {"ac-2": 2, "ac-3": 1}
    assert queries[1].expect_fallback is True


def test_load_golden_set_reports_a_missing_file(tmp_path: Path) -> None:
    """A missing golden set must fail with a clear error, not an opaque OSError."""
    with pytest.raises(testee.EvaluationError, match="not found"):
        testee.load_golden_set(tmp_path / "absent.json")


def test_load_golden_set_reports_invalid_json(tmp_path: Path) -> None:
    """A truncated or malformed file must be named as bad JSON, not crash mid-parse."""
    path = tmp_path / "golden.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(testee.EvaluationError, match="not valid JSON"):
        testee.load_golden_set(path)


def test_load_golden_set_reports_a_schema_violation(tmp_path: Path) -> None:
    """A case that breaks the schema must be rejected up front, not deep in the run."""
    path = write_golden(tmp_path / "golden.json", {"queries": [{"query": "no labels"}]})

    with pytest.raises(testee.EvaluationError, match="invalid"):
        testee.load_golden_set(path)


def test_precision_recall_f1_scores_a_partial_hit() -> None:
    """Precision, recall, and F1 are the user's headline retrieval requirement (D5)."""
    precision, recall, f1 = testee.precision_recall_f1(
        {"ac-2", "ac-3", "ac-6"}, ["ac-2", "ac-3", "pm-1", "pm-2"]
    )

    assert precision == pytest.approx(0.5)  # 2 of 4 retrieved are relevant
    assert recall == pytest.approx(2 / 3)  # 2 of 3 relevant were retrieved
    assert f1 == pytest.approx(2 * 0.5 * (2 / 3) / (0.5 + 2 / 3))


def test_precision_recall_f1_is_case_insensitive() -> None:
    """The index stores lower-case IDs; a case gap must not misreport a hit as a miss."""
    precision, recall, _ = testee.precision_recall_f1({"AC-2"}, ["ac-2"])

    assert (precision, recall) == (1.0, 1.0)


def test_precision_recall_f1_handles_an_empty_retrieval() -> None:
    """A query that retrieved nothing scores zero, not a divide-by-zero crash."""
    assert testee.precision_recall_f1({"ac-2"}, []) == (0.0, 0.0, 0.0)


def test_precision_recall_f1_is_zero_when_nothing_matches() -> None:
    """No overlap means F1 is defined as 0, not an indeterminate 0/0."""
    assert testee.precision_recall_f1({"ac-2"}, ["pm-1"]) == (0.0, 0.0, 0.0)


def test_base_control_id_strips_the_enhancement_suffix() -> None:
    """A control enhancement belongs to its base control; the suffix must drop cleanly."""
    assert testee.base_control_id("AC-2.1") == "ac-2"
    assert testee.base_control_id("ac-2") == "ac-2"


def test_base_family_precision_recall_f1_credits_a_retrieved_enhancement() -> None:
    """Retrieving ia-2.6 answers the 'ia-2' need, so it must count where exact-ID does not."""
    relevant = {"ia-2", "ia-5"}
    retrieved = ["ia-2.6", "ia-5.16", "ac-7.4"]

    assert testee.precision_recall_f1(relevant, retrieved) == (0.0, 0.0, 0.0)
    precision, recall, f1 = testee.base_family_precision_recall_f1(relevant, retrieved)
    assert precision == pytest.approx(2 / 3)  # ia-2.6, ia-5.16 hit; ac-7.4 does not
    assert recall == pytest.approx(1.0)  # both ia-2 and ia-5 families are covered
    assert f1 == pytest.approx(2 * (2 / 3) * 1.0 / (2 / 3 + 1.0))


def test_base_family_precision_recall_f1_handles_an_empty_retrieval() -> None:
    """A family match must survive an empty retrieval as a clean zero, not a crash."""
    assert testee.base_family_precision_recall_f1({"ia-2"}, []) == (0.0, 0.0, 0.0)


def test_to_ground_truth_pairs_each_control_with_a_lower_cased_id() -> None:
    """The evaluator pairs ground truth to results by exact id, so both sides must lower-case."""
    assert testee.to_ground_truth({"AC-2": 2, "ac-3": 1}) == [
        {"document_id": "ac-2", "query_relevance_label": 2},
        {"document_id": "ac-3", "query_relevance_label": 1},
    ]


def test_to_retrieved_documents_lower_cases_the_id_and_keeps_the_score_as_rank_key() -> None:
    """The evaluator orders by `relevance_score` and matches ids exactly, so ids must lower-case."""
    documents = [make_document("AC-2", 3.1), make_document("ac-3", 2.0)]

    assert testee.to_retrieved_documents(documents) == [
        {"document_id": "ac-2", "relevance_score": 3.1},
        {"document_id": "ac-3", "relevance_score": 2.0},
    ]


def test_citation_check_separates_grounded_from_invented() -> None:
    """A cited control that was never retrieved is a grounding violation, caught here."""
    check = testee.citation_check("Per [ac-2] and [pm-1].", ["ac-2", "ac-3"])

    assert check.grounded == ["ac-2"]
    assert check.invented == ["pm-1"]


async def test_evaluate_query_scores_an_on_topic_query_end_to_end() -> None:
    """The on-topic path must combine set metrics, graded metrics, judges, and citations."""
    query = "Summarise requirements for access control"
    documents = [make_document("ac-2", 3.0), make_document("ac-3", 2.5)]
    result = make_result(query, documents, "Use [ac-2] and [ac-3].", ["ac-2", "ac-3"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2, "ac-3": 1, "ac-6": 2})

    evaluation = await testee.evaluate_query(
        pipeline, make_doc_eval(), make_groundedness(5.0), make_relevance(4.0), golden
    )

    assert evaluation.retrieval is not None
    assert evaluation.retrieval.precision == pytest.approx(1.0)
    assert evaluation.retrieval.recall == pytest.approx(2 / 3)
    assert evaluation.retrieval.family_recall == pytest.approx(2 / 3)
    assert evaluation.retrieval.ndcg_at_3 == pytest.approx(0.9)
    assert evaluation.answer_quality == testee.AnswerQuality(groundedness=5.0, relevance=4.0)
    assert evaluation.citations is not None and evaluation.citations.invented == []
    assert evaluation.passed is True


async def test_evaluate_query_grounds_the_judge_in_the_retrieved_control_text() -> None:
    """A groundedness judge fed the wrong context judges nothing; it must see the real controls."""
    query = "access control"
    documents = [make_document("ac-2")]
    result = make_result(query, documents, "Use [ac-2].", ["ac-2"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})
    seen: dict[str, str] = {}

    def capturing_groundedness(*, query: str, response: str, context: str) -> Mapping[str, Any]:
        seen["context"] = context
        seen["response"] = response
        return {"groundedness": 5.0}

    await testee.evaluate_query(
        pipeline, make_doc_eval(), capturing_groundedness, make_relevance(), golden
    )

    assert "Statement of ac-2" in seen["context"], "the judge saw the retrieved control's text"
    assert seen["response"] == "Use [ac-2].", "the judge scored the pipeline's actual answer"


async def test_evaluate_query_fails_a_query_whose_answer_invents_a_citation() -> None:
    """An invented citation is a grounding failure even if retrieval and judges look fine."""
    query = "access control"
    documents = [make_document("ac-2")]
    result = make_result(query, documents, "Use [ac-2] and [pm-1].", ["ac-2"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})

    evaluation = await testee.evaluate_query(
        pipeline, make_doc_eval(), make_groundedness(), make_relevance(), golden
    )

    assert evaluation.citations is not None and evaluation.citations.invented == ["pm-1"]
    assert evaluation.passed is False


async def test_evaluate_query_skips_judges_when_an_on_topic_query_falls_back() -> None:
    """A fallback has no documents to ground against, so the LLM judges must not run."""
    query = "access control"
    result = make_result(query, [], "I could not find any relevant control.", [], True)
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})

    evaluation = await testee.evaluate_query(
        pipeline, exploding_doc_eval, make_groundedness(), make_relevance(), golden
    )

    assert evaluation.answer_quality is None, "no answer to judge"
    assert evaluation.retrieval is not None and evaluation.retrieval.recall == 0.0
    assert evaluation.retrieval.holes == 0, "the graded evaluator was skipped, not called on nothing"
    assert evaluation.passed is False


async def test_evaluate_query_passes_an_out_of_domain_query_that_falls_back() -> None:
    """A fallback query is scored only on whether the safe fallback actually fired."""
    query = "What is the capital of France?"
    result = make_result(query, [], "I could not find any control.", [], True)
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, expect_fallback=True)

    evaluation = await testee.evaluate_query(
        pipeline, exploding_doc_eval, make_groundedness(), make_relevance(), golden
    )

    assert evaluation.retrieval is None and evaluation.citations is None
    assert evaluation.passed is True


async def test_evaluate_query_fails_an_out_of_domain_query_that_answers() -> None:
    """If an off-topic query does not fall back, the safety guarantee has been breached."""
    query = "What is the capital of France?"
    result = make_result(query, [make_document("pm-1")], "Paris, per [pm-1].", ["pm-1"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, expect_fallback=True)

    evaluation = await testee.evaluate_query(
        pipeline, make_doc_eval(), make_groundedness(), make_relevance(), golden
    )

    assert evaluation.is_fallback is False
    assert evaluation.passed is False


def test_aggregate_report_averages_on_topic_and_tallies_fallbacks() -> None:
    """The aggregate is the report's headline; it must separate the two query kinds."""
    on_topic = testee.QueryEvaluation(
        query="q1",
        expect_fallback=False,
        is_fallback=False,
        passed=True,
        plan_steps=[],
        retrieved=[make_document("ac-2")],
        relevant={"ac-2": 2},
        retrieval=testee.RetrievalMetrics(
            precision=1.0,
            recall=0.5,
            f1=0.667,
            family_precision=1.0,
            family_recall=0.75,
            family_f1=0.857,
            ndcg_at_3=0.9,
            xdcg_at_3=80.0,
            fidelity=0.8,
            holes=0,
        ),
        answer_quality=testee.AnswerQuality(groundedness=5.0, relevance=4.0),
        citations=testee.CitationCheck(grounded=["ac-2"], invented=[]),
        answer="a",
    )
    fallback = testee.QueryEvaluation(
        query="q2",
        expect_fallback=True,
        is_fallback=True,
        passed=True,
        plan_steps=[],
        retrieved=[],
        relevant={},
        retrieval=None,
        answer_quality=None,
        citations=None,
        answer="fallback",
    )

    aggregate = testee.aggregate_report([on_topic, fallback])

    assert aggregate.on_topic_count == 1
    assert aggregate.mean_precision == pytest.approx(1.0)
    assert aggregate.mean_groundedness == pytest.approx(5.0)
    assert aggregate.fallback_count == 1 and aggregate.fallback_passed == 1
    assert aggregate.total_invented_citations == 0


def test_aggregate_report_reports_unmeasured_quality_as_none() -> None:
    """With no judged answer, the mean must read 'not measured', not a misleading 0.0."""
    fallback = testee.QueryEvaluation(
        query="q",
        expect_fallback=True,
        is_fallback=True,
        passed=True,
        plan_steps=[],
        retrieved=[],
        relevant={},
        retrieval=None,
        answer_quality=None,
        citations=None,
        answer="fallback",
    )

    aggregate = testee.aggregate_report([fallback])

    assert aggregate.mean_groundedness is None
    assert aggregate.mean_relevance is None
    assert aggregate.mean_precision == 0.0, "no on-topic queries averages to zero, not a crash"


async def test_run_evaluation_evaluates_every_query_and_aggregates() -> None:
    """The harness runs the whole golden set in order and rolls the results up once."""
    on_topic = "access control"
    off_topic = "capital of France"
    results = {
        on_topic: make_result(on_topic, [make_document("ac-2")], "Use [ac-2].", ["ac-2"]),
        off_topic: make_result(off_topic, [], "no control", [], True),
    }
    pipeline = FakePipeline(results)
    golden_set = [
        testee.GoldenQuery(query=on_topic, relevant={"ac-2": 2}),
        testee.GoldenQuery(query=off_topic, expect_fallback=True),
    ]

    report = await testee.run_evaluation(
        pipeline, make_doc_eval(), make_groundedness(), make_relevance(), golden_set
    )

    assert pipeline.queries == [on_topic, off_topic], "queries run in golden-set order"
    assert [item.query for item in report.queries] == [on_topic, off_topic]
    assert report.aggregate.on_topic_count == 1
    assert report.aggregate.fallback_passed == 1


def make_evaluation(
    query: str,
    *,
    expect_fallback: bool = False,
    is_fallback: bool = False,
    retrieved: list[RetrievedDocument] | None = None,
    relevant: dict[str, int] | None = None,
    retrieval: testee.RetrievalMetrics | None = None,
    answer_quality: testee.AnswerQuality | None = None,
    citations: testee.CitationCheck | None = None,
    answer: str = "an answer",
    passed: bool = True,
) -> testee.QueryEvaluation:
    """Build a query evaluation for report-rendering tests.

    Args:
        query: The question.
        expect_fallback: Whether the golden set expected a fallback.
        is_fallback: Whether the pipeline fell back.
        retrieved: The grounding set.
        relevant: The qrels.
        retrieval: Retrieval metrics.
        answer_quality: Judge scores.
        citations: The citation check.
        answer: The answer text.
        passed: The pass flag.

    Returns:
        The evaluation.
    """
    return testee.QueryEvaluation(
        query=query,
        expect_fallback=expect_fallback,
        is_fallback=is_fallback,
        passed=passed,
        plan_steps=[PlanStep(search_query=query, purpose="find")],
        retrieved=retrieved if retrieved is not None else [],
        relevant=relevant if relevant is not None else {},
        retrieval=retrieval,
        answer_quality=answer_quality,
        citations=citations,
        answer=answer,
    )


def test_build_markdown_report_renders_aggregate_and_every_query() -> None:
    """The Markdown report is the TASK deliverable; it must carry the numbers and each answer."""
    on_topic = make_evaluation(
        "access control",
        retrieved=[make_document("ac-2", 3.0)],
        relevant={"ac-2": 2},
        retrieval=testee.RetrievalMetrics(
            precision=1.0,
            recall=1.0,
            f1=1.0,
            family_precision=1.0,
            family_recall=1.0,
            family_f1=1.0,
            ndcg_at_3=0.95,
            xdcg_at_3=90.0,
            fidelity=0.9,
            holes=0,
        ),
        answer_quality=testee.AnswerQuality(groundedness=5.0, relevance=4.0),
        citations=testee.CitationCheck(grounded=["ac-2"], invented=[]),
        answer="Use [ac-2].",
    )
    fallback = make_evaluation(
        "capital of France", expect_fallback=True, is_fallback=True, answer="no control"
    )
    report = testee.EvaluationReport(
        queries=[on_topic, fallback], aggregate=testee.aggregate_report([on_topic, fallback])
    )

    markdown = testee.build_markdown_report(report)

    assert "# Evaluation report" in markdown
    assert "| Precision | 1.000 | 1.000 |" in markdown
    assert "| Groundedness (1-5) | 5.00 | — |" in markdown
    assert "### Q1: access control" in markdown
    assert "**P/R/F1 (exact-ID):** 1.000 / 1.000 / 1.000" in markdown
    assert "**P/R/F1 (base-family):** 1.000 / 1.000 / 1.000" in markdown
    assert "ac-2 (3.00)" in markdown
    assert "Use [ac-2]." in markdown
    assert "### Q2: capital of France (out-of-domain)" in markdown
    assert "returned the safe fallback ✓" in markdown


def test_build_markdown_report_marks_unmeasured_quality_and_a_missed_fallback() -> None:
    """A run with no judged answers and a broken fallback must render honestly, not blank."""
    unanswered = make_evaluation(
        "access control",
        is_fallback=True,
        retrieved=[],
        relevant={"ac-2": 2},
        retrieval=testee.RetrievalMetrics(
            precision=0.0,
            recall=0.0,
            f1=0.0,
            family_precision=0.0,
            family_recall=0.0,
            family_f1=0.0,
            ndcg_at_3=0.0,
            xdcg_at_3=0.0,
            fidelity=0.0,
            holes=0,
        ),
        citations=testee.CitationCheck(grounded=[], invented=[]),
        answer="fallback",
        passed=False,
    )
    broken_fallback = make_evaluation(
        "capital of France",
        expect_fallback=True,
        is_fallback=False,
        retrieved=[make_document("pm-1", 2.5)],
        answer="Paris",
        passed=False,
    )
    report = testee.EvaluationReport(
        queries=[unanswered, broken_fallback],
        aggregate=testee.aggregate_report([unanswered, broken_fallback]),
    )

    markdown = testee.build_markdown_report(report)

    assert "| Groundedness (1-5) | — | — |" in markdown
    assert "(none)" in markdown, "an empty retrieval renders as (none), not a blank"
    assert "did NOT fall back ✗" in markdown
    assert "pm-1 (2.50)" in markdown, "a breached fallback must show what it wrongly retrieved"
