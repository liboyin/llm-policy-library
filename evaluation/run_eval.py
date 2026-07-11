"""Run the evaluation harness against the live pipeline and Azure OpenAI judges.

Invoke from the repository root::

    python evaluation/run_eval.py

The run loads the golden set, opens a live pipeline, and constructs the three
Azure AI Evaluation evaluators, then writes two artifacts to ``samples/``:

* ``evaluation_report.md`` — the human-readable report (TASK.md's "sample
  execution outputs" deliverable): an aggregate table plus a per-query section.
* ``evaluation_transcripts.json`` — the full machine-readable per-query record,
  including each plan, its retrieved controls with scores, every metric, and the
  answer.

All the logic lives in ``llm_policy_library.evaluation``; this file is only the
live wiring — settings, clients, evaluators, and file output — so it holds no
branching worth unit-testing, mirroring ``llm_policy_library.ingest``'s ``main``.
The LLM-judge evaluators are built with ``is_reasoning_model=True`` because the
deployed chat model is a reasoning model that rejects the sampling parameters the
default judge configuration would send (decision D7).
"""

import asyncio
import logging
from pathlib import Path
from typing import Final, cast

from azure.ai.evaluation import (
    AzureOpenAIModelConfiguration,
    DocumentRetrievalEvaluator,
    GroundednessEvaluator,
    RelevanceEvaluator,
)

from llm_policy_library.config import load_settings
from llm_policy_library.evaluation import (
    DocumentRetrievalEval,
    build_markdown_report,
    load_golden_set,
    run_evaluation,
)
from llm_policy_library.logging_setup import configure_logging, correlation_context
from llm_policy_library.orchestrator import open_pipeline

logger = logging.getLogger("evaluation.run_eval")

_REPO_ROOT: Final = Path(__file__).resolve().parent.parent
GOLDEN_SET_PATH: Final = _REPO_ROOT / "evaluation" / "golden_set.json"
SAMPLES_DIR: Final = _REPO_ROOT / "samples"
REPORT_PATH: Final = SAMPLES_DIR / "evaluation_report.md"
TRANSCRIPTS_PATH: Final = SAMPLES_DIR / "evaluation_transcripts.json"

# The chat deployment is a gpt-5-family reasoning model reached through the
# Responses API; this preview api-version exposes the judge's reasoning path.
EVAL_MODEL_API_VERSION: Final = "2024-12-01-preview"


async def _evaluate() -> None:
    """Load, run, and write the evaluation, opening the live pipeline once."""
    settings = load_settings()
    configure_logging(settings.log_level)
    golden_set = load_golden_set(GOLDEN_SET_PATH)

    model_config = AzureOpenAIModelConfiguration(
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        azure_deployment=settings.azure_openai_chat_deployment,
        api_version=EVAL_MODEL_API_VERSION,
    )
    # The evaluator satisfies `DocumentRetrievalEval` at runtime; its stub types
    # the arguments as narrower TypedDicts, so a cast bridges the nominal gap
    # without importing the SDK's types into the decoupled harness module.
    doc_eval = cast(DocumentRetrievalEval, DocumentRetrievalEvaluator())
    groundedness_eval = GroundednessEvaluator(model_config, is_reasoning_model=True)
    relevance_eval = RelevanceEvaluator(model_config, is_reasoning_model=True)

    with correlation_context() as run_id:
        logger.info(
            "evaluation started",
            extra={"run_id": run_id, "queries": len(golden_set)},
        )
        async with open_pipeline(settings) as pipeline:
            report = await run_evaluation(
                pipeline, doc_eval, groundedness_eval, relevance_eval, golden_set
            )
        logger.info(
            "evaluation complete",
            extra={
                "on_topic": report.aggregate.on_topic_count,
                "mean_precision": report.aggregate.mean_precision,
                "mean_recall": report.aggregate.mean_recall,
                "fallback_passed": report.aggregate.fallback_passed,
                "invented_citations": report.aggregate.total_invented_citations,
            },
        )

    SAMPLES_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text(build_markdown_report(report), encoding="utf-8")
    TRANSCRIPTS_PATH.write_text(report.model_dump_json(indent=2), encoding="utf-8")


def main() -> int:
    """Run the evaluation and write the report and transcripts.

    Returns:
        A process exit code; 0 on success.
    """
    asyncio.run(_evaluate())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
