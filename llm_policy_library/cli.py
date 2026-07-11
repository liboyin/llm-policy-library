"""CLI entry point: `python -m llm_policy_library.cli "question"`.

Pretty-prints the plan, retrieved controls, and grounded answer for one
question. It wraps the same `PolicyPipeline` the API serves — `run` differs
from `api.query` only in how the question arrives and the result is rendered,
never in how it is answered.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence

from llm_policy_library.config import load_settings
from llm_policy_library.logging_setup import configure_logging, correlation_context
from llm_policy_library.models import PipelineResult
from llm_policy_library.orchestrator import open_pipeline


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command-line arguments.

    Args:
        argv: Arguments to parse. Defaults to `sys.argv[1:]`.

    Returns:
        The parsed namespace, carrying `query`.
    """
    parser = argparse.ArgumentParser(
        prog="python -m llm_policy_library.cli",
        description="Answer one policy question through the multi-agent pipeline.",
    )
    parser.add_argument("query", help="The policy question to ask.")
    return parser.parse_args(argv)


def format_result(result: PipelineResult) -> str:
    """Render a pipeline result as a human-readable report.

    Args:
        result: The plan, retrieved controls, and grounded answer.

    Returns:
        The formatted report.
    """
    lines = ["Plan:"]
    for index, step in enumerate(result.plan.steps, start=1):
        lines.append(f"  {index}. {step.search_query}  -- {step.purpose}")

    lines.append("")
    lines.append("Retrieved controls:")
    if result.documents:
        for document in result.documents:
            lines.append(f"  [{document.id}] {document.title} (score={document.score:.3f})")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Answer:")
    lines.append(result.response.answer)
    if result.response.citations:
        lines.append("")
        lines.append(f"Citations: {', '.join(result.response.citations)}")
    return "\n".join(lines)


async def run(query: str) -> str:
    """Answer one question through the pipeline and format the result.

    Args:
        query: The user's question.

    Returns:
        The formatted report.
    """
    settings = load_settings()
    # stderr, not the default stdout: main() prints the formatted report to
    # stdout, and interleaving JSON log lines with it would corrupt both.
    configure_logging(settings.log_level, stream=sys.stderr)
    with correlation_context():
        async with open_pipeline(settings) as pipeline:
            result = await pipeline.answer_query(query)
    return format_result(result)


def main(argv: Sequence[str] | None = None) -> int:
    """Parse arguments, run the pipeline, and print the formatted result.

    Args:
        argv: Arguments to parse. Defaults to `sys.argv[1:]`.

    Returns:
        A process exit code; 0 on success.
    """
    args = parse_args(argv)
    print(asyncio.run(run(args.query)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
