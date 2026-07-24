All agents MUST follow this file.

CAPITALIZED requirement words have the meanings defined by BCP 14 (RFC 2119 and RFC 8174).

# Meta Guidelines

- If not running in Docker, you MUST stop and confirm with the user.
- Before editing, you MUST read the relevant code and documentation and plan your actions carefully.
- State assumptions explicitly. When you notice ambiguities that materially affect scope, architecture, dataflow, correctness, or security, you MUST stop and confirm with the user.
- Isolated subtasks (tasks that require little or no additional context from the main conversation and produce a small, well-bounded result for follow-up work) SHOULD be executed in subagents to keep the main context window clean.
- Clean up any background process you start. Resolve PIDs beforehand and use `kill`; NEVER use `pkill -f` as it matches your own tool execution and will terminate your agent.
- Claims that an action succeeded MUST use a check whose output would differ on failures. Treat non-zero exit codes as failures until explained. Verify absence directly instead of inferring it.
- Before considering a task done, you MUST re-check compliance with this file.

# Documentation Guidelines

- Each document SHOULD be the unique owner of its assigned topic. Other docs SHOULD link or summarize without becoming competing sources of truth.
- Documentation MUST be updated as soon as its content no longer reflects the latest state of the project.
- Design decisions & assumptions MUST be documented, and SHOULD record the reasoning behind them.
- Every empirical claim in a document, status block, docstring, or comment MUST be verified immediately before writing down.
- Document ownership:
    - `TASK.md` describes the end goals of this project.
    - `TODO.md` is the phased execution plan and log. Its status blocks record what was actually done and decided.
    - `README.md` describes project structure, architecture, dataflow, and build & test procedures.
    - `docs/architecture.md` is the system design spec — agent interaction flow, determinism & grounding, security, and scalability.
    - `docs/azure-setup.md` is the Azure provisioning guide.
- New or changed non-test functions and methods MUST have Google-style docstrings; unit-test functions MUST have one-line docstrings.

# Implementation Guidelines

- Implement only what was asked with small, surgical changes. Do not add features or unrelated refactors unless explicitly asked to.
- Prefer the simplest implementation. Each function/class/module MUST have a single responsibility and a well-defined interface; other SOLID principles MAY be relaxed in favor of simplicity.
- Code MUST be testable with minimal mocking; prefer pure logic and isolated side effects.
- Code SHOULD use up-to-date features from languages, libraries, frameworks, and external services. Because these change quickly, you SHOULD verify behavior empirically or against the version-appropriate documentation before planning or building on them.
- Before deleting a seemingly redundant layer, identify everything unique it carries (copy, error shape, ordering, timing, diagnostics) and give each item another home.

# Test Guidelines

- Tests MUST encode WHY behavior matters, not just WHAT it does. A test that does not fail when business logic changes is wrong.
- Mutation-test every new assertion. Include at least one mutant toward a future regression, not only a revert, and test the trade-off introduced by a constraint.
- Where measurable, statement and branch coverage MUST each be at least 80% per file and project-wide. Because `--cov-fail-under=80` gates only the total, manually review `--cov-report=term-missing`.
- Thin wiring/glue modules with no branching logic MAY be excluded from coverage measurement with a documented rationale and an end-to-end smoke test.
- See `pyproject.toml` for test configs. Note that tests run in random order (`pytest-randomly`).
- Order test functions to match the source file's function order.
- Import the module under test as `import llm_policy_library.my_module as testee`; call functions as `testee.function_name` and mock attributes via `patch.object(testee, 'attribute', ...)`.
- After every code change, all gates MUST pass:

```bash
pytest
mypy .
ruff check .
```

# Review Guidelines

Every non-trivial code, test, or configuration change MUST pass the adversarial review (`.agents/skills/adversarial-review/SKILL.md`) procedure before commit. Documentation-only changes are exempt.

The adversarial review skill owns the operational procedure: the caller context it requires, the questions it scrutinizes with, and its report format. Call it as a function on the current dirty tree and expect a verified, triaged findings report without code change.

The main agent remains accountable for the execute-review loop:

1. Run the review skill on the dirty tree or the specified revision.
2. Based on the review report, create execution plans to fix blocking findings and trivial non-blocking findings.
3. Implement the plan step-by-step.
4. Repeat step 1 - 3 until no blocking findings remain. Do not shorten the loop.
5. Ask the user whether to fix, defer, or ignore remaining non-blocking findings.

Undoing a code change re-enters the loop at step 1. Verify clean rollbacks against `HEAD` with `git diff`; do not rely on your recollection of the delta.

# Version Control Guidelines

- Commit each functionally independent change once fully implemented, tested, and documented.
- Stage explicit paths and inspect `git status` immediately before committing. NEVER use `git add -A` or `git commit -a`.
- Use the template below for commit messages. Do not add a `Co-Authored-By` line:

```text
<Claude/Codex/Antigravity/...>: <one-line summary>

<One or more paragraphs describing the change in detail.>
```
