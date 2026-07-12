"""The pipeline that wires the three agents together.

The pipeline is a chain, and each link is a typed Pydantic message rather than a
conversation:

    str
      -> Planner   -> QueryPlan
      -> Retrieval -> RetrievalOutcome
      -> Response  -> PipelineResult

Because the edges are types, a stage cannot quietly reinterpret what the last
one produced. This is also the whole audit trail: a `PipelineResult` carries the
plan, every step's hits and scores, the grounding set, and the answer with its
citations, so an auditor can replay how a compliance answer was reached without
re-running it.

The three stages are plain sequential awaits: a straight-line chain needs no
workflow engine, and PydanticAI agents hold no per-run state, so one pipeline
serves concurrent queries. Client lifetimes belong to `open_pipeline`, which is
the only place in the serving path that opens a socket and is entered once per
process, not once per request; `build_pipeline` accepts an already-built model
and already-open clients so the whole pipeline can be exercised with mocks.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from openai import AsyncAzureOpenAI
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider

from llm_policy_library.agents.planner import PlannerAgent, build_planner, plan_query
from llm_policy_library.agents.response import ResponseAgent, build_response_agent, generate_response
from llm_policy_library.agents.retrieval import dedupe_documents, retrieve_plan
from llm_policy_library.config import (
    AZURE_OPENAI_CHAT_API_VERSION,
    AZURE_OPENAI_EMBEDDING_API_VERSION,
    Settings,
)
from llm_policy_library.models import PipelineResult, RetrievalOutcome

logger = logging.getLogger(__name__)


class PolicyPipeline:
    """Answers policy questions by running the three agents in sequence."""

    def __init__(
        self,
        planner: PlannerAgent,
        response: ResponseAgent,
        search_client: SearchClient,
        openai_client: AsyncAzureOpenAI,
        settings: Settings,
    ) -> None:
        """Hold the agents and clients every query runs against.

        Args:
            planner: The Planner Agent.
            response: The Response Agent.
            search_client: Client bound to the policy index.
            openai_client: Client for the embedding deployment.
            settings: Validated runtime configuration.
        """
        self._planner = planner
        self._response = response
        self._search_client = search_client
        self._openai_client = openai_client
        self._settings = settings

    async def answer_query(self, query: str) -> PipelineResult:
        """Run the full pipeline for one question.

        The agents and clients hold no per-run state, so one pipeline serves
        concurrent queries without interference.

        Args:
            query: The user's question.

        Returns:
            The plan, the retrieved controls, and the grounded answer.

        Raises:
            PlannerError: If planning failed; see `plan_query`.
            ResponseError: If the model returned no answer text; see
                `generate_response`.
        """
        started = time.perf_counter()

        plan = await plan_query(self._planner, query)

        results = await retrieve_plan(
            self._search_client, self._openai_client, self._settings, plan
        )
        documents = dedupe_documents(results)
        logger.info(
            "plan retrieved",
            extra={
                # Named so no key holds a count in one audit line and a list in
                # another: `step_count`/`document_count` are always ints, and
                # the `documents` list of IDs is the only `documents` key.
                "step_count": len(results),
                "documents": [document.id for document in documents],
            },
        )
        outcome = RetrievalOutcome(plan=plan, results=results, documents=documents)

        response = await generate_response(
            self._response, outcome.plan.original_query, outcome.documents
        )
        result = PipelineResult(
            plan=outcome.plan,
            results=outcome.results,
            documents=outcome.documents,
            response=response,
        )

        elapsed_ms = (time.perf_counter() - started) * 1000
        # The one line that pairs the request's input with its full output, which
        # is the audit record TASK.md requires. The per-stage lines above carry
        # the plan and the retrieval scores; this carries the answer text itself.
        logger.info(
            "query answered",
            extra={
                "query": query,
                "answer": result.response.answer,
                "latency_ms": round(elapsed_ms, 1),
                "document_count": len(result.documents),
                "citations": result.response.citations,
                "is_fallback": result.response.is_fallback,
            },
        )
        return result


def build_pipeline(
    settings: Settings,
    chat_model: OpenAIChatModel,
    openai_client: AsyncAzureOpenAI,
    search_client: SearchClient,
) -> PolicyPipeline:
    """Assemble the agents into a pipeline over open Azure clients.

    Both chat agents share one model instance; they differ only in instructions
    and output type, which live on the agents themselves.

    Args:
        settings: Validated runtime configuration.
        chat_model: PydanticAI model bound to the chat deployment.
        openai_client: Client for the embedding deployment.
        search_client: Client bound to the policy index.

    Returns:
        A pipeline ready to answer queries.
    """
    effort = settings.llm_reasoning_effort
    return PolicyPipeline(
        planner=build_planner(chat_model, effort),
        response=build_response_agent(chat_model, effort),
        search_client=search_client,
        openai_client=openai_client,
        settings=settings,
    )


@asynccontextmanager
async def open_pipeline(settings: Settings) -> AsyncIterator[PolicyPipeline]:
    """Open the Azure clients, yield a pipeline, and close them afterwards.

    Chat and embeddings get separate Azure OpenAI clients because they are
    pinned to different API versions. Both are context-managed here, so the
    sockets they own are closed on exit; the PydanticAI model is a thin wrapper
    over the chat client and owns no transport of its own.

    Args:
        settings: Validated runtime configuration.

    Yields:
        A pipeline bound to freshly opened clients.
    """
    async with (
        AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=AZURE_OPENAI_CHAT_API_VERSION,
        ) as chat_client,
        AsyncAzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key.get_secret_value(),
            api_version=AZURE_OPENAI_EMBEDDING_API_VERSION,
        ) as openai_client,
        SearchClient(
            settings.azure_search_endpoint,
            settings.azure_search_index_name,
            AzureKeyCredential(settings.azure_search_api_key.get_secret_value()),
        ) as search_client,
    ):
        chat_model = OpenAIChatModel(
            settings.azure_openai_chat_deployment,
            provider=AzureProvider(openai_client=chat_client),
        )
        yield build_pipeline(settings, chat_model, openai_client, search_client)
