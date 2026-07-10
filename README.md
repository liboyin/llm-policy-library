# llm-policy-library

A multi-agent AI system that answers questions about enterprise security policies by
retrieving from the NIST SP 800-53 Rev 5 control catalog indexed in Azure AI Search,
planning a query with Microsoft Agent Framework agents, and generating a grounded,
citation-bearing answer with Azure OpenAI.

See [TASK.md](TASK.md) for the goals this project implements and
[TODO.md](TODO.md) for the phased execution plan and the resolved design decisions.

> **Status:** scaffolding (Phase 1). Configuration and structured logging are in place;
> ingestion, the agents, the API, and evaluation land in later phases.

## Project structure

| Path | Purpose |
|---|---|
| [llm_policy_library/config.py](llm_policy_library/config.py) | Environment-driven settings, validated once at startup |
| [llm_policy_library/logging_setup.py](llm_policy_library/logging_setup.py) | JSON log formatter and per-request correlation IDs |
| [tests/](tests/) | Unit tests; no test performs a live Azure call |
| [docs/azure-setup.md](docs/azure-setup.md) | How to provision the Azure resources this project reads |
| [.env.example](.env.example) | Template for the environment variables below |

## Setup

Requires Python 3.14 (the devcontainer in [.devcontainer/](.devcontainer/) provides it).

```bash
pip install -e ".[dev]"
cp .env.example .env    # then fill in the values
```

Provision the Azure resources first — [docs/azure-setup.md](docs/azure-setup.md) walks
through the portal steps, the tier trade-offs, and where to find each endpoint and key.
`.env` is gitignored and must never be committed.

## Configuration

All configuration comes from environment variables, read from the process environment or
from `.env` (the process environment wins). [.env.example](.env.example) documents every
variable; [config.py](llm_policy_library/config.py) validates them. A missing or malformed
variable aborts startup with a message naming each variable to fix, rather than failing
later inside an Azure SDK call.

Two settings carry design weight:

- `AZURE_SEARCH_SEMANTIC_RANKER` — semantic reranking needs Basic tier or above. Set it to
  `false` on the Free tier; retrieval will then use hybrid vector + BM25 search only.
- `LLM_REASONING_EFFORT` — every currently deployable Azure OpenAI chat model is a
  reasoning model that rejects `temperature`, `top_p`, and `seed`. Determinism is
  therefore enforced through grounding (structured outputs, a pinned model version,
  citation checks, and a safe fallback) rather than sampling controls. See decision D7 in
  [TODO.md](TODO.md).

## Logging

Every log line is a single JSON object written to stdout. Fields passed via the stdlib
`extra=` keyword are merged into the payload, and the correlation ID of the enclosing
request is attached automatically:

```python
import logging

from llm_policy_library.logging_setup import configure_logging, correlation_context

configure_logging(level="INFO")
with correlation_context() as correlation_id:
    logging.getLogger(__name__).info("query received", extra={"query": query})
```

The correlation ID lives in a `ContextVar`, so it survives `await` boundaries and stays
isolated between concurrently served requests.

The Azure SDK logs a full request/response header dump on every HTTP call, and `httpx` a
line per request, both at INFO. They are pinned to WARNING so they cannot bury the audit
trail; setting `LOG_LEVEL=DEBUG` restores them for troubleshooting.

## Tests and static analysis

All three must pass before any commit:

```bash
pytest              # unit tests + coverage (>=80% per file and overall)
mypy .
ruff check .
```

Tests run in random order (`pytest-randomly`) and are hermetic: they never read the
developer's real `.env` and never call Azure.
