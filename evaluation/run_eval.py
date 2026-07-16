"""Run the evaluation harness against the live pipeline and Azure OpenAI judges.

Invoke from the repository root::

    python evaluation/run_eval.py

The run loads the golden set, opens a live pipeline, and builds the two
Microsoft Agent Framework judge agents (faithfulness and answer relevancy) over
the same chat deployment the pipeline uses, then writes two artifacts to
``samples/``:

* ``evaluation_report.md`` — the human-readable report (TASK.md's "sample
  execution outputs" deliverable): an aggregate table plus a per-query section.
* ``evaluation_transcripts.json`` — the full machine-readable per-query record,
  including each plan, its retrieved controls with scores, every metric, and the
  answer.

All the logic lives in ``llm_policy_library.evaluation``; this file is only the
live wiring — settings, clients, judge agents, and file output — so it holds no
branching worth unit-testing, mirroring ``llm_policy_library.ingest``'s ``main``.
The judges share the pipeline's chat API version and reasoning-effort setting
(the deployed model is a reasoning model that rejects ``temperature``/``top_p``/
``seed``, decision D7), and the retrieval metrics' NDCG truncation depth is the
pipeline's own ``RETRIEVAL_TOP_K``.
"""

import asyncio
import logging
from functools import partial
from pathlib import Path
from typing import Final

from agent_framework.openai import OpenAIChatClient

from llm_policy_library.agents.judges import (
    JudgeOptions,
    build_answer_relevancy_judge,
    build_faithfulness_judge,
    judge_answer_relevancy,
    judge_faithfulness,
)
from llm_policy_library.config import AZURE_OPENAI_CHAT_API_VERSION, load_settings
from llm_policy_library.evaluation import build_markdown_report, load_golden_set, run_evaluation
from llm_policy_library.logging_setup import configure_logging, correlation_context
from llm_policy_library.orchestrator import open_pipeline

logger = logging.getLogger("evaluation.run_eval")

_REPO_ROOT: Final = Path(__file__).resolve().parent.parent
GOLDEN_SET_PATH: Final = _REPO_ROOT / "evaluation" / "golden_set.json"
SAMPLES_DIR: Final = _REPO_ROOT / "samples"
REPORT_PATH: Final = SAMPLES_DIR / "evaluation_report.md"
TRANSCRIPTS_PATH: Final = SAMPLES_DIR / "evaluation_transcripts.json"


async def _evaluate() -> None:
    """Load, run, and write the evaluation, opening the live pipeline once."""
    settings = load_settings()
    configure_logging(settings.log_level)
    golden_set = load_golden_set(GOLDEN_SET_PATH)

    with correlation_context() as run_id:
        logger.info(
            "evaluation started",
            extra={"run_id": run_id, "queries": len(golden_set)},
        )
        # The judges get their own chat client, distinct from the pipeline's, so
        # judge traffic is never confused with a served request; it is pinned to
        # the same API version the pipeline's chat client uses (`config.py` is the
        # single source for versions). The Agent Framework client owns its own
        # transport and exposes no close, so it needs no context manager.
        judge_client: OpenAIChatClient[JudgeOptions] = OpenAIChatClient(
            model=settings.azure_openai_chat_deployment,
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=AZURE_OPENAI_CHAT_API_VERSION,
        )
        effort = settings.llm_reasoning_effort
        faithfulness_judge = partial(
            judge_faithfulness, build_faithfulness_judge(judge_client, effort)
        )
        answer_relevancy_judge = partial(
            judge_answer_relevancy, build_answer_relevancy_judge(judge_client, effort)
        )
        async with open_pipeline(settings) as pipeline:
            report = await run_evaluation(
                pipeline,
                faithfulness_judge,
                answer_relevancy_judge,
                golden_set,
                ndcg_k=settings.retrieval_top_k,
            )
        logger.info(
            "evaluation complete",
            extra={
                "on_topic": report.aggregate.on_topic_count,
                "mean_recall": report.aggregate.mean_recall,
                "mean_family_recall": report.aggregate.mean_family_recall,
                "mean_ndcg": report.aggregate.mean_ndcg,
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
