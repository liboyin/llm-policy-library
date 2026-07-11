"""Response Agent: write an answer grounded in the retrieved controls.

Grounding is enforced twice, and neither check trusts the model.

*Before* the call: if retrieval found nothing above its relevance floor, the
fixed safe-fallback message is returned and no chat model runs. A model given no
documents and told to use only documents has nothing to say, and asking it
anyway is how a system invents a plausible control.

*After* the call: the answer's inline `[ac-2]` citations are matched against the
IDs actually retrieved. Only the matches become `GroundedResponse.citations`, so
an invented control ID can never be reported as a source. It is logged as a
grounding violation, which is the signal that the prompt or the model regressed.

The answer itself is free prose rather than a JSON schema. The Planner needs a
schema because a plan is data; an answer is text, and a `response_format` here
would only wrap prose in a JSON envelope while costing the model tokens.
"""

import logging
import re
from collections.abc import Iterable
from typing import Any, Final

from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient, OpenAIChatOptions

from llm_policy_library.config import ReasoningEffort
from llm_policy_library.models import GroundedResponse, RetrievedDocument

logger = logging.getLogger(__name__)

SAFE_FALLBACK_MESSAGE: Final = (
    "I could not find any NIST SP 800-53 control relevant to that question in "
    "the policy library, so I cannot answer it. Try rephrasing the question in "
    "terms of a security control, a control family, or a control ID such as AC-2."
)

RESPONSE_INSTRUCTIONS: Final = """\
You answer questions about enterprise security policy using only the NIST SP \
800-53 Rev 5 controls supplied with each question.

Rules, in order of precedence:

1. Use only the supplied controls. Never rely on knowledge of NIST SP 800-53, or \
of any other standard, that is not present in the text you were given.
2. Cite every control you use inline, in square brackets, by its exact ID as \
supplied: [ac-2], [ac-2.1]. Cite it where you use it, not in a list at the end.
3. Never cite a control that was not supplied to you, and never invent an ID.
4. If the supplied controls do not answer the question, say so plainly and \
describe what they do cover. Do not fill the gap.

Answer in prose, briefly, at the level of detail the question asks for.
"""

# Control IDs are a two-letter family, a number, and optionally an enhancement
# number: `ac-2`, `ac-2.1`. Matching the shape rather than a fixed family list
# means a citation of a real-looking but unretrieved control is caught by the
# allow-list below, not silently skipped by the regex.
_CITATION_PATTERN: Final = re.compile(r"\[([a-z]{2}-\d+(?:\.\d+)?)\]", re.IGNORECASE)

ResponseOptions = OpenAIChatOptions[None]
ResponseAgent = Agent[ResponseOptions]


class ResponseError(RuntimeError):
    """Raised when the chat model returns no answer text."""


def build_response_agent(
    chat_client: OpenAIChatClient[ResponseOptions], reasoning_effort: ReasoningEffort
) -> ResponseAgent:
    """Construct the Response Agent over an Azure OpenAI chat client.

    Args:
        chat_client: Client bound to the chat deployment.
        reasoning_effort: Reasoning effort to request on every call.

    Returns:
        The configured agent.
    """
    # See `planner.build_planner` on why the effort value is typed loosely.
    reasoning: Any = {"effort": reasoning_effort}
    options: ResponseOptions = {"reasoning": reasoning}
    return Agent(chat_client, RESPONSE_INSTRUCTIONS, name="response", default_options=options)


def format_documents(documents: Iterable[RetrievedDocument]) -> str:
    """Render the retrieved controls as the prompt's evidence block.

    The ID is repeated in the exact form the answer must cite it in, so that
    citing correctly is a copy rather than a transformation.

    Args:
        documents: The grounding set, best first.

    Returns:
        One labelled block per control.
    """
    return "\n\n".join(
        f"[{document.id}] {document.title} ({document.category})\n{document.description}"
        for document in documents
    )


def extract_citations(answer: str, retrieved_ids: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split an answer's inline citations into the grounded and the invented.

    Args:
        answer: The model's prose.
        retrieved_ids: IDs of the controls the model was given.

    Returns:
        `(grounded, invented)`. Both are lower-cased and deduplicated, in order
        of first mention. `invented` holds every cited ID that was not retrieved.
    """
    # Both sides are case-folded: the index stores lower-case IDs, but a
    # comparison that assumed so would misfile a valid citation as invented.
    allowed = {control_id.lower() for control_id in retrieved_ids}
    grounded: list[str] = []
    invented: list[str] = []
    for match in _CITATION_PATTERN.finditer(answer):
        control_id = match.group(1).lower()
        bucket = grounded if control_id in allowed else invented
        if control_id not in bucket:
            bucket.append(control_id)
    return grounded, invented


def safe_fallback() -> GroundedResponse:
    """Build the response returned when nothing relevant was retrieved.

    Returns:
        The fixed fallback message, citing nothing.
    """
    return GroundedResponse(answer=SAFE_FALLBACK_MESSAGE, citations=[], is_fallback=True)


async def generate_response(
    agent: ResponseAgent, query: str, documents: list[RetrievedDocument]
) -> GroundedResponse:
    """Answer a question from the retrieved controls, or fall back safely.

    Args:
        agent: The Response Agent.
        query: The user's question.
        documents: The grounding set. Empty means nothing relevant was found.

    Returns:
        The grounded answer, or the safe fallback when `documents` is empty, in
        which case no chat model is called.

    Raises:
        ResponseError: If the model returned no answer text. That is an upstream
            failure, not a refusal: serving it as an empty answer would look like
            a grounded one, and serving the fallback would claim, untruthfully,
            that no relevant control was found.
    """
    if not documents:
        logger.info("safe fallback returned", extra={"query": query, "reason": "no documents"})
        return safe_fallback()

    prompt = f"Question: {query}\n\nControls:\n\n{format_documents(documents)}"
    response = await agent.run(prompt)
    answer = response.text.strip()
    if not answer:
        raise ResponseError(f"response agent returned an empty answer for query {query!r}")

    grounded, invented = extract_citations(answer, (document.id for document in documents))
    if invented:
        logger.warning(
            "answer cited controls that were not retrieved",
            extra={"query": query, "invented_citations": invented},
        )
    logger.info(
        "answer generated",
        extra={"query": query, "citations": grounded, "answer_chars": len(answer)},
    )
    return GroundedResponse(answer=answer, citations=grounded, is_fallback=False)
