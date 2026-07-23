"""The typed messages the agents exchange.

These models are the contracts of the pipeline: the Planner emits a `QueryPlan`,
the Retrieval Agent turns it into a `RetrievalOutcome`, and the Response Agent
answers with a `GroundedResponse`, which the orchestrator's final executor packs
into a `PipelineResult`. Every hop is a validated Pydantic model rather than
free text, which is what makes the orchestration inspectable — the audit log and
the evaluation harness both read these objects, not prose.

The Planner's chat model does not emit a `QueryPlan` directly. Its structured
output is a `PlannerOutput` — the searches, and nothing else — which the Planner
combines with the known question to build the `QueryPlan`. Keeping the two apart
means the model never has to reproduce the question only for the Planner to
discard its copy.

`PlannerOutput` and `PlanStep` are therefore prompt surface, and **their class
docstrings are prompt surface too, not just their field descriptions**. Measured
2026-07-22 against the SDK path that builds the request: a pydantic model's
docstring is emitted as the JSON schema's `description`, whole, including its
`Attributes:` block — so a note about how the Planner post-processes a field is
read by the model as guidance. Both classes keep one-line docstrings for that
reason, with the rationale in comments, which are not emitted. Nothing in either
class may contradict the `planner_instructions` prompt
(`llm_policy_library.prompts`) — and because the schema is shared by both arms of
the `PLANNER_CORPUS_MAP` A/B while the family list is not, "not contradict" means
in *both* arms: no description may assume the instructions list the families.
`PlannerOutput` deliberately carries no `min_length`/`max_length`: the step count
is an invariant of the Planner, not of the wire format, and enforcing it here
would turn an over-eager model response into a `ValidationError` deep inside the
chat client instead of a value the Planner can clamp.

`PlanStep.category` and `PlannerOutput.out_of_domain` are the corpus map's two
outputs, and both are the model's *proposals* rather than decisions: the Planner
honours them only when `PLANNER_CORPUS_MAP` is on, because the map is what puts
the family list in front of the model in the first place. Asked without it, the
model answers anyway and answers badly — measured 2026-07-22, it proposed the
families "Access Control (AC)" and "SC", neither of which is a name the catalog
uses — which is why the Planner validates rather than trusts.

The two fields are additive on the wire: a stored plan or API payload written
before them still validates, `category` defaulting to None and
`QueryPlan.out_of_domain` to False.
"""

from pydantic import BaseModel, ConfigDict, Field


class PlanStep(BaseModel):
    """One search to run against the policy index."""

    # Engineering notes live in comments, not in this docstring: see the module
    # docstring on why every line of it is prompt surface.
    #
    # `category` is the control family to restrict this search to, or None to
    # search the whole index. It is only ever a family the corpus map lists —
    # `agents.planner.validated_categories` clears anything else before the value
    # reaches an OData filter — so retrieval may treat it as a fixed vocabulary.
    # `purpose` is recorded for the audit trail; nothing downstream branches on it.

    model_config = ConfigDict(frozen=True)

    search_query: str = Field(
        description=(
            "A short natural-language phrase naming the security topic to search "
            "the control catalog for, or a single control ID."
        )
    )
    purpose: str = Field(description="What this step is meant to find, in one sentence.")
    # `default=None` never reaches the model. Measured 2026-07-22 against the SDK
    # path that builds the request (`openai.lib._parsing._responses`): structured
    # outputs are compiled with `strict: true`, which rewrites `required` to *every*
    # property regardless of pydantic defaults, so `category` is a required field on
    # the wire. What makes null a legal answer is the `anyOf: [string, null]` union,
    # not the default. The default is what the *Planner* gets when a stored plan
    # predates this field.
    #
    # The description must read correctly in both arms of the corpus-map A/B, since
    # the schema is shared: with the map off there is no family list in the
    # instructions, and this then tells the model to leave the field alone.
    category: str | None = Field(
        default=None,
        description=(
            "The one control family to search, spelled exactly as the list in "
            "your instructions spells it. Null searches every family; leave it "
            "null unless your instructions list the families to choose from."
        ),
    )


class PlannerOutput(BaseModel):
    """The plan: the searches to run, and whether the corpus covers the question."""

    # This class is the Planner's `response_format`, so — like `PlanStep` — every
    # line of the docstring above is sent to the model. The engineering rationale
    # therefore lives here instead:
    #
    # The schema omits the user's question. That is a known input the Planner
    # supplies itself, and requiring the model to echo it would spend output tokens
    # on a value the Planner immediately discards.
    #
    # `out_of_domain` carries no default, unlike `PlanStep.category`. That is a
    # statement to readers of this file rather than to the model — strict mode
    # makes both fields required on the wire either way (see `PlanStep.category`)
    # — but a judgement the model declined to make is not the same as "in domain",
    # and a default here would invite exactly that conflation in Python.

    model_config = ConfigDict(frozen=True)

    # No step count in the description: the `planner_instructions` prompt is the
    # one prompt surface that states the limit, interpolated from `MAX_PLAN_STEPS`
    # by `agents.planner.build_planner`. A number here would be a second source
    # that drifts from it.
    steps: list[PlanStep] = Field(
        description="The searches that together answer the question."
    )
    # Phrased, like `PlanStep.category`, to be true in both arms of the A/B: the
    # schema is shared, so with the corpus map off this must not ask the model to
    # judge against a list it was never given.
    out_of_domain: bool = Field(
        description=(
            "True when your instructions list the control families and none of "
            "them covers the question, so the corpus cannot answer it. False "
            "when your instructions list no families."
        )
    )


class QueryPlan(BaseModel):
    """A user question decomposed into search steps.

    Built by the Planner from the question and a `PlannerOutput`; not the chat
    model's own output, so its fields are data, not prompt surface.

    Attributes:
        original_query: The user's question, verbatim — the true input, set by
            the Planner rather than echoed by the model.
        steps: The searches to run, after clamping to the Planner's limit. The
            Planner empties this when it sets `out_of_domain`, which is the one
            case a stepless plan is valid; the pairing is a Planner invariant,
            not one this model enforces, and retrieval skips an out-of-domain
            plan whether or not steps came with it.
        out_of_domain: The corpus holds no family that covers the question, so
            retrieval is skipped entirely and the safe fallback is served. False
            for every plan built without the corpus map.
    """

    model_config = ConfigDict(frozen=True)

    original_query: str
    steps: list[PlanStep]
    out_of_domain: bool = False


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
