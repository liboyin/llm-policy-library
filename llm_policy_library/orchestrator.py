"""The Microsoft Agent Framework workflow that wires the three agents together.

The pipeline is a chain, and each link is a typed Pydantic message rather than a
conversation:

    str
      -> Planner   -> QueryPlan
      -> Retrieval -> RetrievalOutcome
      -> Response  -> PipelineResult

Because the edges are types, a stage cannot quietly reinterpret what the last
one produced, and the workflow refuses to build if two stages disagree. This is
also the whole audit trail: a `PipelineResult` carries the plan, every step's
hits and scores, the grounding set, and the answer with its citations, so an
auditor can replay how a compliance answer was reached without re-running it.

Two things in the Agent Framework hold per-run state, so both are rebuilt per
query. A `Workflow` refuses a second concurrent run ("Workflow is already
running"); and an `Executor` serializes its handler behind a per-instance
`asyncio.Lock` (it is designed to process one message at a time), so a shared
executor would funnel every concurrent query through that one lock and undo the
concurrency the per-query workflow exists to provide. `answer_query` therefore
builds fresh executors *and* a fresh workflow each time — ~0.4 ms against a
multi-second pipeline. The agents and clients the executors wrap hold no per-run
state and are shared across those runs.

The executors build nothing. Client lifetimes belong to `open_pipeline`, which
is the only place in the serving path that opens a socket and is entered once
per process, not once per request; `build_pipeline` accepts already-open clients
so the whole workflow can be exercised with mocks.
"""

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Never

from agent_framework import Executor, Workflow, WorkflowBuilder, WorkflowContext, handler
from agent_framework.openai import OpenAIChatClient
from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from openai import AsyncAzureOpenAI

from llm_policy_library.agents.planner import PlannerAgent, build_planner, plan_query
from llm_policy_library.agents.response import ResponseAgent, build_response_agent, generate_response
from llm_policy_library.agents.retrieval import dedupe_documents, retrieve_plan
from llm_policy_library.config import (
    AZURE_OPENAI_CHAT_API_VERSION,
    AZURE_OPENAI_EMBEDDING_API_VERSION,
    Settings,
)
from llm_policy_library.models import PipelineResult, QueryPlan, RetrievalOutcome

logger = logging.getLogger(__name__)


class OrchestrationError(RuntimeError):
    """Raised when the workflow completes without producing a result."""


class PlannerExecutor(Executor):
    """Turns the user's question into a `QueryPlan`."""

    def __init__(self, agent: PlannerAgent, corpus_map: bool) -> None:
        """Bind the executor to a Planner Agent.

        Args:
            agent: The Planner Agent.
            corpus_map: Whether the agent was built with the corpus map, which
                also decides whether its `category`/`out_of_domain` answers are
                honoured. It travels with the agent because it describes the
                agent: the two must not disagree.
        """
        super().__init__(id="planner")
        self._agent = agent
        self._corpus_map = corpus_map

    @handler
    async def run(self, query: str, ctx: WorkflowContext[QueryPlan]) -> None:
        """Plan the searches for one question.

        Args:
            query: The user's question.
            ctx: Workflow context carrying the plan onward.
        """
        await ctx.send_message(await plan_query(self._agent, query, self._corpus_map))


class RetrievalExecutor(Executor):
    """Executes a plan's searches and assembles the grounding set."""

    def __init__(
        self,
        search_client: SearchClient,
        openai_client: AsyncAzureOpenAI,
        settings: Settings,
    ) -> None:
        """Bind the executor to the search and embedding clients.

        Args:
            search_client: Client bound to the policy index.
            openai_client: Client for the embedding deployment.
            settings: Validated runtime configuration.
        """
        super().__init__(id="retrieval")
        self._search_client = search_client
        self._openai_client = openai_client
        self._settings = settings

    @handler
    async def run(self, plan: QueryPlan, ctx: WorkflowContext[RetrievalOutcome]) -> None:
        """Retrieve for every plan step and deduplicate the hits.

        Args:
            plan: The Planner's decomposition.
            ctx: Workflow context carrying the outcome onward.
        """
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
        await ctx.send_message(
            RetrievalOutcome(plan=plan, results=results, documents=documents)
        )


class ResponseExecutor(Executor):
    """Writes the grounded answer, or yields the safe fallback."""

    def __init__(self, agent: ResponseAgent) -> None:
        """Bind the executor to a Response Agent.

        Args:
            agent: The Response Agent.
        """
        super().__init__(id="response")
        self._agent = agent

    @handler
    async def run(
        self, outcome: RetrievalOutcome, ctx: WorkflowContext[Never, PipelineResult]
    ) -> None:
        """Answer the question from the grounding set.

        Args:
            outcome: Everything retrieval found.
            ctx: Workflow context, which this stage yields the final result to.
        """
        response = await generate_response(
            self._agent,
            outcome.plan.original_query,
            outcome.documents,
            outcome.plan.out_of_domain,
        )
        await ctx.yield_output(
            PipelineResult(
                plan=outcome.plan,
                results=outcome.results,
                documents=outcome.documents,
                response=response,
            )
        )


def build_workflow(
    planner: PlannerExecutor, retrieval: RetrievalExecutor, response: ResponseExecutor
) -> Workflow:
    """Chain the three executors into a workflow.

    Args:
        planner: The Planner executor, which starts the chain.
        retrieval: The Retrieval executor.
        response: The Response executor, whose output is the workflow's.

    Returns:
        The built workflow.
    """
    return (
        WorkflowBuilder(name="policy-library", start_executor=planner, output_from=[response])
        .add_chain([planner, retrieval, response])
        .build()
    )


class PolicyPipeline:
    """Answers policy questions by running a fresh per-query workflow."""

    def __init__(
        self,
        planner: PlannerAgent,
        response: ResponseAgent,
        search_client: SearchClient,
        openai_client: AsyncAzureOpenAI,
        settings: Settings,
    ) -> None:
        """Hold the agents and clients a per-query workflow is built from.

        The executors are not held here: each carries a per-instance lock, so a
        shared instance would serialize concurrent queries through its stage.
        They are cheap to build and are constructed fresh in `answer_query`.

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

        Both the workflow and its executors are built here, per query: a
        `Workflow` carries one run's state and a MAF `Executor` serializes its
        handler behind a per-instance lock, so sharing either across concurrent
        queries would serialize them. The agents and clients the executors wrap
        hold no per-run state and are shared safely.

        Args:
            query: The user's question.

        Returns:
            The plan, the retrieved controls, and the grounded answer.

        Raises:
            OrchestrationError: If the workflow yielded no `PipelineResult`,
                which means an executor stopped early without raising.
        """
        workflow = build_workflow(
            PlannerExecutor(self._planner, self._settings.planner_corpus_map),
            RetrievalExecutor(self._search_client, self._openai_client, self._settings),
            ResponseExecutor(self._response),
        )
        started = time.perf_counter()
        run = await workflow.run(query)
        elapsed_ms = (time.perf_counter() - started) * 1000

        outputs = [output for output in run.get_outputs() if isinstance(output, PipelineResult)]
        if not outputs:
            raise OrchestrationError(f"workflow produced no result for query {query!r}")
        result = outputs[-1]

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
    chat_client: OpenAIChatClient[Any],
    openai_client: AsyncAzureOpenAI,
    search_client: SearchClient,
) -> PolicyPipeline:
    """Assemble the agents and clients into a pipeline over open Azure clients.

    Both chat agents share one client. Its type parameter names the *default*
    options an agent is built with, and the two agents differ there — only the
    Planner sets a `response_format` — so the client is deliberately unbound. The
    executors and the workflow are not built here: they carry per-run state and
    are constructed per query in `answer_query`.

    Args:
        settings: Validated runtime configuration.
        chat_client: Agent Framework client bound to the chat deployment.
        openai_client: Client for the embedding deployment.
        search_client: Client bound to the policy index.

    Returns:
        A pipeline ready to answer queries.
    """
    effort = settings.llm_reasoning_effort
    return PolicyPipeline(
        build_planner(chat_client, effort, settings.planner_corpus_map),
        build_response_agent(chat_client, effort),
        search_client,
        openai_client,
        settings,
    )


@asynccontextmanager
async def open_pipeline(settings: Settings) -> AsyncIterator[PolicyPipeline]:
    """Open the Azure clients, yield a pipeline, and close them afterwards.

    Chat and embeddings get separate Azure OpenAI clients because they are pinned
    to different API versions: the chat model is reached through the Responses
    API, the embedding model through the stable version ingestion used. The
    Agent Framework chat client owns its own transport and exposes no close, so
    it is created per pipeline, not per request.

    Args:
        settings: Validated runtime configuration.

    Yields:
        A pipeline bound to freshly opened clients.
    """
    chat_client: OpenAIChatClient[Any] = OpenAIChatClient(
        model=settings.azure_openai_chat_deployment,
        azure_endpoint=settings.azure_openai_endpoint,
        api_key=settings.azure_openai_api_key.get_secret_value(),
        api_version=AZURE_OPENAI_CHAT_API_VERSION,
    )
    async with (
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
        yield build_pipeline(settings, chat_client, openai_client, search_client)
