"""The typed messages the agents exchange.

These models are the contracts of the pipeline: the Planner emits a `QueryPlan`,
the Retrieval Agent turns it into a `RetrievalOutcome`, and the Response Agent
answers with a `GroundedResponse`, which the orchestrator packs into a
`PipelineResult`. Every hop is a validated Pydantic model rather than
free text, which is what makes the orchestration inspectable — the audit log and
the evaluation harness both read these objects, not prose.

The Planner's chat model does not emit a `QueryPlan` directly. Its structured
output is a `PlannerOutput` — the searches, and nothing else — which the Planner
combines with the known question to build the `QueryPlan`. Keeping the two apart
means the model never has to reproduce the question only for the Planner to
discard its copy. `PlannerOutput.steps`' field descriptions are therefore the
prompt surface, and must not contradict `agents.planner.PLANNER_INSTRUCTIONS`.
`PlannerOutput` deliberately carries no `min_length`/`max_length`: the step count
is an invariant of the Planner, not of the wire format, and enforcing it here
would turn an over-eager model response into a `ValidationError` deep inside the
chat client instead of a value the Planner can clamp.
"""

from pydantic import BaseModel, ConfigDict, Field


class PlanStep(BaseModel):
    """One search to run against the policy index.

    Attributes:
        search_query: The text issued to Azure AI Search for this step.
        purpose: Why this step exists, in the Planner's own words. Recorded for
            the audit trail; nothing downstream branches on it.
    """

    model_config = ConfigDict(frozen=True)

    search_query: str = Field(
        description=(
            "A short natural-language phrase naming the security topic to search "
            "the control catalog for, or a single control ID."
        )
    )
    purpose: str = Field(description="What this step is meant to find, in one sentence.")


class PlannerOutput(BaseModel):
    """The Planner chat model's structured output: the searches, nothing else.

    This is the Planner's `response_format` JSON schema, so `steps`' field
    description is prompt surface the model reads. It omits the user's question:
    that is a known input the Planner supplies itself, and requiring the model to
    echo it would spend output tokens on a value the Planner immediately discards.

    Attributes:
        steps: The searches the model proposes, before the Planner clamps them.
    """

    model_config = ConfigDict(frozen=True)

    # No step count in the description: `agents.planner.PLANNER_INSTRUCTIONS` is
    # the one prompt surface that states the limit, interpolated from
    # `MAX_PLAN_STEPS`. A number here would be a second source that drifts from it.
    steps: list[PlanStep] = Field(
        description="The searches that together answer the question."
    )


class QueryPlan(BaseModel):
    """A user question decomposed into search steps.

    Built by the Planner from the question and a `PlannerOutput`; not the chat
    model's own output, so its fields are data, not prompt surface.

    Attributes:
        original_query: The user's question, verbatim — the true input, set by
            the Planner rather than echoed by the model.
        steps: The searches to run, after clamping to the Planner's limit.
    """

    model_config = ConfigDict(frozen=True)

    original_query: str
    steps: list[PlanStep]


class RetrievedDocument(BaseModel):
    """One control retrieved from the index.

    Attributes:
        id: The OSCAL control ID, e.g. `ac-2` or `ac-2.1`. This is the token the
            Response Agent may cite, and the label the golden set scores against.
        title: The control's short name.
        description: The control statement, which is the only text the Response
            Agent is allowed to ground an answer in.
        category: The control family.
        score: Relevance, on whichever scale the active search mode ranks by.
            Comparable within one pipeline run, never across runs of different
            modes; see `llm_policy_library.agents.retrieval`.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    description: str
    category: str
    score: float


class RetrievalResult(BaseModel):
    """What one plan step found.

    Attributes:
        step: The step that produced these documents.
        documents: Controls that cleared the relevance floor, best first.
    """

    model_config = ConfigDict(frozen=True)

    step: PlanStep
    documents: list[RetrievedDocument]


class RetrievalOutcome(BaseModel):
    """Everything retrieval learned, passed whole to the Response Agent.

    `results` and `documents` are two views of one retrieval: the first is
    per-step and auditable, the second is the deduplicated set the answer must
    be grounded in. The Response Agent reads only `documents`; `results` exists
    so a reader of the audit trail can see which step surfaced which control.

    Attributes:
        plan: The plan that was executed.
        results: One entry per plan step, in plan order.
        documents: The union of `results`' documents, deduplicated by ID and
            ordered best first. Empty means nothing relevant was found, which
            is the sole trigger for the safe fallback.
    """

    model_config = ConfigDict(frozen=True)

    plan: QueryPlan
    results: list[RetrievalResult]
    documents: list[RetrievedDocument]


class GroundedResponse(BaseModel):
    """The final answer, with the controls it is grounded in.

    Attributes:
        answer: Prose answering the question, citing controls inline as `[ac-2]`.
        citations: The control IDs cited in `answer` that were actually
            retrieved, first-mention order. A citation the model invented is
            never listed here.
        is_fallback: True when retrieval found nothing relevant and no chat
            model was called, so `answer` is the fixed safe-fallback message.
    """

    model_config = ConfigDict(frozen=True)

    answer: str
    citations: list[str]
    is_fallback: bool


class PipelineResult(BaseModel):
    """One end-to-end run of the pipeline, for serving, logging, and evaluation.

    Attributes:
        plan: The Planner's decomposition. `plan.original_query` is the input.
        results: Per-step retrieval, in plan order.
        documents: The deduplicated grounding set the answer was built from.
        response: The final grounded answer.
    """

    model_config = ConfigDict(frozen=True)

    plan: QueryPlan
    results: list[RetrievalResult]
    documents: list[RetrievedDocument]
    response: GroundedResponse
