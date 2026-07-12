"""Unit tests for `llm_policy_library.evaluation`."""

import json
import math
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

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

# Most tests truncate NDCG at the pipeline's default top-k.
K = 5


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


def make_faithfulness(score: int = 5) -> testee.FaithfulnessJudge:
    """Build a fake faithfulness judge returning a fixed score.

    Args:
        score: The score to report.

    Returns:
        The fake judge.
    """

    async def _judge(*, question: str, answer: str, context: str) -> int:
        return score

    return _judge


def make_answer_relevancy(score: int = 4) -> testee.AnswerRelevancyJudge:
    """Build a fake answer-relevancy judge returning a fixed score.

    Args:
        score: The score to report.

    Returns:
        The fake judge.
    """

    async def _judge(*, question: str, answer: str) -> int:
        return score

    return _judge


async def exploding_faithfulness(*, question: str, answer: str, context: str) -> int:
    """A faithfulness judge that fails if called at all."""
    raise AssertionError("the faithfulness judge must not be called on a fallback")


async def exploding_answer_relevancy(*, question: str, answer: str) -> int:
    """An answer-relevancy judge that fails if called at all."""
    raise AssertionError("the answer-relevancy judge must not be called on a fallback")


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


def test_golden_query_rejects_case_duplicate_control_ids() -> None:
    """The metrics lower-case IDs; case-duplicate qrels would silently drop one label."""
    with pytest.raises(ValueError, match="unique ignoring case"):
        testee.GoldenQuery(query="q", relevant={"AC-2": 2, "ac-2": 1})


def test_answer_quality_rejects_a_score_off_the_judges_scale() -> None:
    """An injected judge returning 0 or 42 must fail loudly, not silently skew the means."""
    with pytest.raises(ValueError):
        testee.AnswerQuality(faithfulness=0, answer_relevancy=4)
    with pytest.raises(ValueError):
        testee.AnswerQuality(faithfulness=5, answer_relevancy=42)


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


def test_exact_recall_scores_a_partial_hit() -> None:
    """Recall gates answer quality: it says whether the grounding set held the needed controls."""
    recall = testee.exact_recall({"ac-2", "ac-3", "ac-6"}, ["ac-2", "ac-3", "pm-1", "pm-2"])

    assert recall == pytest.approx(2 / 3)  # 2 of 3 relevant were retrieved


def test_exact_recall_is_case_insensitive() -> None:
    """The index stores lower-case IDs; a case gap must not misreport a hit as a miss."""
    assert testee.exact_recall({"AC-2"}, ["ac-2"]) == 1.0


def test_exact_recall_handles_an_empty_retrieval() -> None:
    """A query that retrieved nothing scores zero, not a divide-by-zero crash."""
    assert testee.exact_recall({"ac-2"}, []) == 0.0


def test_base_control_id_strips_the_enhancement_suffix() -> None:
    """A control enhancement belongs to its base control; the suffix must drop cleanly."""
    assert testee.base_control_id("AC-2.1") == "ac-2"
    assert testee.base_control_id("ac-2") == "ac-2"


def test_base_family_recall_credits_a_retrieved_enhancement() -> None:
    """Retrieving ia-2.6 answers the 'ia-2' need, so it must count where exact-ID does not."""
    relevant = {"ia-2", "ia-5"}
    retrieved = ["ia-2.6", "ia-5.16", "ac-7.4"]

    assert testee.exact_recall(relevant, retrieved) == 0.0
    assert testee.base_family_recall(relevant, retrieved) == pytest.approx(1.0)


def test_base_family_recall_handles_an_empty_retrieval() -> None:
    """A family match must survive an empty retrieval as a clean zero, not a crash."""
    assert testee.base_family_recall({"ia-2"}, []) == 0.0


def test_ndcg_at_k_is_one_for_a_perfectly_ordered_retrieval() -> None:
    """A run that ranks the strongly-relevant controls first must get the top score."""
    assert testee.ndcg_at_k({"ac-2": 2, "ac-3": 1}, ["ac-2", "ac-3"], K) == pytest.approx(1.0)


def test_ndcg_at_k_penalizes_ranking_a_weak_control_above_a_strong_one() -> None:
    """NDCG exists to reward ranking quality; swapping grades must cost score, unlike recall."""
    relevant = {"ac-2": 2, "ac-3": 1}

    reversed_order = testee.ndcg_at_k(relevant, ["ac-3", "ac-2"], K)

    # Hand-computed: DCG = 1/log2(2) + 2/log2(3); ideal DCG = 2/log2(2) + 1/log2(3).
    expected = (1 + 2 / math.log2(3)) / (2 + 1 / math.log2(3))
    assert reversed_order == pytest.approx(expected)
    assert reversed_order < 1.0


def test_ndcg_at_k_gives_no_gain_to_an_unlabelled_control() -> None:
    """An unlabelled retrieval pushes labelled ones down the discount; it must not add gain."""
    with_hole = testee.ndcg_at_k({"ac-2": 2}, ["pm-1", "ac-2"], K)

    assert with_hole == pytest.approx((2 / math.log2(3)) / 2)


def test_ndcg_at_k_ignores_a_relevant_control_ranked_beyond_k() -> None:
    """@k means @k: a hit below the cutoff is a recall problem, invisible to a truncated NDCG."""
    assert testee.ndcg_at_k({"ac-2": 2}, ["pm-1", "pm-2", "ac-2"], 2) == 0.0


def test_ndcg_at_k_truncates_the_ideal_ranking_too() -> None:
    """With more qrels than k, a full top-k of top-grade controls must still score 1.0."""
    relevant = {"ac-2": 2, "ac-3": 2, "ac-6": 2}

    assert testee.ndcg_at_k(relevant, ["ac-2", "ac-3"], 2) == pytest.approx(1.0)


def test_ndcg_at_k_handles_an_empty_retrieval() -> None:
    """A query that retrieved nothing scores zero, not a divide-by-zero crash."""
    assert testee.ndcg_at_k({"ac-2": 2}, [], K) == 0.0


def test_ndcg_at_k_is_case_insensitive() -> None:
    """The index stores lower-case IDs; a case gap must not zero the graded metric."""
    assert testee.ndcg_at_k({"AC-2": 2}, ["ac-2"], K) == pytest.approx(1.0)


def test_citation_check_separates_grounded_from_invented() -> None:
    """A cited control that was never retrieved is a grounding violation, caught here."""
    check = testee.citation_check("Per [ac-2] and [pm-1].", ["ac-2", "ac-3"])

    assert check.grounded == ["ac-2"]
    assert check.invented == ["pm-1"]


async def test_evaluate_query_scores_an_on_topic_query_end_to_end() -> None:
    """The on-topic path must combine recall, NDCG, both judges, and the citation check."""
    query = "Summarise requirements for access control"
    documents = [make_document("ac-2", 3.0), make_document("ac-3", 2.5)]
    result = make_result(query, documents, "Use [ac-2] and [ac-3].", ["ac-2", "ac-3"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2, "ac-3": 1, "ac-6": 2})

    evaluation = await testee.evaluate_query(
        pipeline, make_faithfulness(5), make_answer_relevancy(4), golden, K
    )

    assert evaluation.retrieval is not None
    assert evaluation.retrieval.recall == pytest.approx(2 / 3)
    assert evaluation.retrieval.family_recall == pytest.approx(2 / 3)
    # ac-2(2) then ac-3(1), ac-6 missing: DCG = 2 + 1/log2(3); the ideal
    # front-loads both grade-2 controls: 2 + 2/log2(3) + 1/log2(4).
    expected_ndcg = (2 + 1 / math.log2(3)) / (2 + 2 / math.log2(3) + 1 / 2)
    assert evaluation.retrieval.ndcg == pytest.approx(expected_ndcg)
    assert evaluation.answer_quality == testee.AnswerQuality(faithfulness=5, answer_relevancy=4)
    assert evaluation.citations is not None and evaluation.citations.invented == []
    assert evaluation.passed is True


async def test_evaluate_query_grounds_the_judge_in_the_retrieved_control_text() -> None:
    """A faithfulness judge fed the wrong context judges nothing; it must see the real controls."""
    query = "access control"
    documents = [make_document("ac-2")]
    result = make_result(query, documents, "Use [ac-2].", ["ac-2"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})
    seen: dict[str, str] = {}

    async def capturing_faithfulness(*, question: str, answer: str, context: str) -> int:
        seen["context"] = context
        seen["answer"] = answer
        return 5

    await testee.evaluate_query(
        pipeline, capturing_faithfulness, make_answer_relevancy(), golden, K
    )

    assert "Statement of ac-2" in seen["context"], "the judge saw the retrieved control's text"
    assert seen["answer"] == "Use [ac-2].", "the judge scored the pipeline's actual answer"


async def test_evaluate_query_fails_a_query_whose_answer_invents_a_citation() -> None:
    """An invented citation is a grounding failure even if retrieval and judges look fine."""
    query = "access control"
    documents = [make_document("ac-2")]
    result = make_result(query, documents, "Use [ac-2] and [pm-1].", ["ac-2"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})

    evaluation = await testee.evaluate_query(
        pipeline, make_faithfulness(), make_answer_relevancy(), golden, K
    )

    assert evaluation.citations is not None and evaluation.citations.invented == ["pm-1"]
    assert evaluation.passed is False


async def test_evaluate_query_skips_judges_when_an_on_topic_query_falls_back() -> None:
    """A fallback has no answer to ground, so neither LLM judge may spend a model call."""
    query = "access control"
    result = make_result(query, [], "I could not find any relevant control.", [], True)
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})

    evaluation = await testee.evaluate_query(
        pipeline, exploding_faithfulness, exploding_answer_relevancy, golden, K
    )

    assert evaluation.answer_quality is None, "no answer to judge"
    assert evaluation.retrieval is not None and evaluation.retrieval.recall == 0.0
    assert evaluation.retrieval.ndcg == 0.0
    assert evaluation.passed is False


async def test_evaluate_query_retries_a_judge_through_a_transient_failure() -> None:
    """The retries exist so one rate-limit blip cannot blank a score the judge would have given."""
    query = "access control"
    documents = [make_document("ac-2")]
    result = make_result(query, documents, "Use [ac-2].", ["ac-2"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})
    calls = {"count": 0}

    async def flaky_faithfulness(*, question: str, answer: str, context: str) -> int:
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("transient rate limit")
        return 5

    with patch("asyncio.sleep", AsyncMock()):
        evaluation = await testee.evaluate_query(
            pipeline, flaky_faithfulness, make_answer_relevancy(4), golden, K
        )

    assert calls["count"] == 2, "the first failure was retried, not recorded"
    assert evaluation.answer_quality == testee.AnswerQuality(faithfulness=5, answer_relevancy=4)


async def test_evaluate_query_records_none_for_only_the_failing_judge(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If one judge fails persistently, its metric is None but the other's score survives."""
    query = "access control"
    documents = [make_document("ac-2")]
    result = make_result(query, documents, "Use [ac-2].", ["ac-2"])
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, relevant={"ac-2": 2})

    async def failing_faithfulness(*, question: str, answer: str, context: str) -> int:
        raise ValueError("judge failed")

    with patch("asyncio.sleep", AsyncMock()):
        evaluation = await testee.evaluate_query(
            pipeline, failing_faithfulness, make_answer_relevancy(4), golden, K
        )

    assert evaluation.answer_quality == testee.AnswerQuality(
        faithfulness=None, answer_relevancy=4
    )
    assert "LLM judge failed after retries" in caplog.text
    assert evaluation.retrieval is not None
    assert evaluation.citations is not None and evaluation.citations.invented == []


async def test_evaluate_query_passes_an_out_of_domain_query_that_falls_back() -> None:
    """A fallback query is scored only on whether the safe fallback actually fired."""
    query = "What is the capital of France?"
    result = make_result(query, [], "I could not find any control.", [], True)
    pipeline = FakePipeline({query: result})
    golden = testee.GoldenQuery(query=query, expect_fallback=True)

    evaluation = await testee.evaluate_query(
        pipeline, exploding_faithfulness, exploding_answer_relevancy, golden, K
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
        pipeline, make_faithfulness(), make_answer_relevancy(), golden, K
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
        retrieval=testee.RetrievalMetrics(recall=0.5, family_recall=0.75, ndcg=0.9),
        answer_quality=testee.AnswerQuality(faithfulness=5, answer_relevancy=4),
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
    assert aggregate.mean_recall == pytest.approx(0.5)
    assert aggregate.mean_family_recall == pytest.approx(0.75)
    assert aggregate.mean_ndcg == pytest.approx(0.9)
    assert aggregate.mean_faithfulness == pytest.approx(5.0)
    assert aggregate.mean_answer_relevancy == pytest.approx(4.0)
    assert aggregate.fallback_count == 1 and aggregate.fallback_passed == 1
    assert aggregate.total_invented_citations == 0


def test_aggregate_report_skips_a_none_score_instead_of_zeroing_the_mean() -> None:
    """A failed judge must not drag the mean down as a zero; its query simply is not counted."""

    def on_topic(name: str, quality: testee.AnswerQuality) -> testee.QueryEvaluation:
        return testee.QueryEvaluation(
            query=name,
            expect_fallback=False,
            is_fallback=False,
            passed=True,
            plan_steps=[],
            retrieved=[make_document("ac-2")],
            relevant={"ac-2": 2},
            retrieval=testee.RetrievalMetrics(recall=1.0, family_recall=1.0, ndcg=1.0),
            answer_quality=quality,
            citations=testee.CitationCheck(grounded=["ac-2"], invented=[]),
            answer="a",
        )

    aggregate = testee.aggregate_report(
        [
            on_topic("q1", testee.AnswerQuality(faithfulness=None, answer_relevancy=4)),
            on_topic("q2", testee.AnswerQuality(faithfulness=5, answer_relevancy=2)),
        ]
    )

    assert aggregate.mean_faithfulness == pytest.approx(5.0), "the None was skipped, not zeroed"
    assert aggregate.mean_answer_relevancy == pytest.approx(3.0)


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

    assert aggregate.mean_faithfulness is None
    assert aggregate.mean_answer_relevancy is None
    assert aggregate.mean_recall == 0.0, "no on-topic queries averages to zero, not a crash"


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
        pipeline, make_faithfulness(), make_answer_relevancy(), golden_set, K
    )

    assert pipeline.queries == [on_topic, off_topic], "queries run in golden-set order"
    assert [item.query for item in report.queries] == [on_topic, off_topic]
    assert report.ndcg_k == K, "the transcripts must record what k the NDCG was computed at"
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
        retrieval=testee.RetrievalMetrics(recall=1.0, family_recall=1.0, ndcg=0.95),
        answer_quality=testee.AnswerQuality(faithfulness=5, answer_relevancy=4),
        citations=testee.CitationCheck(grounded=["ac-2"], invented=[]),
        answer="Use [ac-2].",
    )
    fallback = make_evaluation(
        "capital of France", expect_fallback=True, is_fallback=True, answer="no control"
    )
    report = testee.EvaluationReport(
        ndcg_k=K,
        queries=[on_topic, fallback],
        aggregate=testee.aggregate_report([on_topic, fallback]),
    )

    markdown = testee.build_markdown_report(report)

    assert "# Evaluation report" in markdown
    assert "not comparable" in markdown, "the intro must warn the new NDCG differs from the old"
    assert "| Recall | 1.000 | 1.000 |" in markdown
    assert f"| NDCG@{K} | 0.950 | — |" in markdown
    assert "| Faithfulness (1-5) | 5.00 | — |" in markdown
    assert "| Answer relevancy (1-5) | 4.00 | — |" in markdown
    assert "### Q1: access control" in markdown
    assert "**Recall:** 1.000 exact-ID / 1.000 base-family" in markdown
    assert f"**NDCG@{K}:** 0.950" in markdown
    assert "**Faithfulness:** 5/5 · **Answer relevancy:** 4/5" in markdown
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
        retrieval=testee.RetrievalMetrics(recall=0.0, family_recall=0.0, ndcg=0.0),
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
        ndcg_k=K,
        queries=[unanswered, broken_fallback],
        aggregate=testee.aggregate_report([unanswered, broken_fallback]),
    )

    markdown = testee.build_markdown_report(report)

    assert "| Faithfulness (1-5) | — | — |" in markdown
    assert "(none)" in markdown, "an empty retrieval renders as (none), not a blank"
    assert "did NOT fall back ✗" in markdown
    assert "pm-1 (2.50)" in markdown, "a breached fallback must show what it wrongly retrieved"


def test_build_markdown_report_renders_a_single_failed_judge_as_a_dash() -> None:
    """A per-query None score must render as —, distinct from a real low score."""
    partial = make_evaluation(
        "access control",
        retrieved=[make_document("ac-2")],
        relevant={"ac-2": 2},
        retrieval=testee.RetrievalMetrics(recall=1.0, family_recall=1.0, ndcg=1.0),
        answer_quality=testee.AnswerQuality(faithfulness=None, answer_relevancy=4),
        citations=testee.CitationCheck(grounded=["ac-2"], invented=[]),
        answer="Use [ac-2].",
    )
    report = testee.EvaluationReport(
        ndcg_k=K, queries=[partial], aggregate=testee.aggregate_report([partial])
    )

    markdown = testee.build_markdown_report(report)

    assert "**Faithfulness:** — · **Answer relevancy:** 4/5" in markdown
    assert (
        "| Faithfulness (1-5) | — | — |" in markdown
    ), "no judged faithfulness leaves the mean unmeasured"
    assert "| Answer relevancy (1-5) | 4.00 | — |" in markdown
