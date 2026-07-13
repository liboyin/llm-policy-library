This document contains guidelines that all AI agents MUST follow.

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, NOT RECOMMENDED, MAY, and OPTIONAL in this document are to be interpreted as described in BCP 14 (IETF RFC 2119 and RFC 8174) when, and only when, they appear in all capitals, as shown here.

# Meta Guidelines

- If not running in a Docker container, you MUST stop and confirm with the user before continuing.
- You MUST read relevant code & documentation, and plan your actions before making a file change.
- State assumptions explicitly. When you notice an ambiguity that materially affects the project (e.g. scope, architecture, dataflow, correctness, or security), you MUST confirm with the user before continuing.
- Isolated subtasks (tasks that require little or no additional context from the main conversation and produce a small, well-bounded result for follow-up work) SHOULD be executed in subagents to keep the main context window clean.
- Before considering a task done, you MUST re-check that all instructions in this file are followed.

# Documentation Guidelines

- Each document SHOULD own its assigned topic, and other docs SHOULD link or summarize without becoming competing sources of truth.
- Documentation MUST be updated as soon as its content no longer reflects the latest state of the project.
- `TASK.md` describes the end goals of this project.
- `TODO.md` is the phased execution plan and log. Its status blocks record what was actually done and decided.
- `README.md` describes project structure, architecture, dataflow, and build & test procedures.
- Design decisions & assumptions MUST be documented in whichever document fits best (e.g. README, a design doc, or the task's execution log), and SHOULD record the reasoning behind them.
- New or modified functions/methods in non-test scripts MUST have Google-style docstrings; unit test functions MUST have a one-line docstring.

# Implementation Guidelines

- Implement only what was asked with small, surgical changes. Do not add features or unrelated refactors unless explicitly asked to.
- Prefer the simplest implementation. Each function/class/module MUST have a single responsibility and a well-defined interface; other SOLID principles MAY be relaxed in favor of simplicity.
- Implementations MUST be easy to test with minimal mocking. Pure functions are preferred, and side effects SHOULD be isolated.
- Code SHOULD use up-to-date features from languages, libraries, frameworks, and external services. These change quickly and assumptions about them go stale, most dangerously at planning time. You SHOULD verify behavior empirically or against the documentation for the version in use before planning or building on them.

# Test Guidelines

- Tests MUST encode WHY behavior matters, not just WHAT it does. A test that does not fail when business logic changes is wrong.
- Whenever measurable, line, statement, and branch coverage MUST each be ≥80% for each file and at the project level.
- See `pyproject.toml` for test configs. Note that tests run in random order (`pytest-randomly`).
- You MUST manually review per-file test coverage aided by `--cov-report=term-missing` to meet the ≥80% requirement.
- Order test functions to match the source file's function order.
- Import the module under test as `import llm_policy_library.my_module as testee`; call functions as `testee.function_name` and mock attributes via `patch.object(testee, 'attribute', ...)`.
- After any code change, all of the following unit tests and static analysis MUST pass before sending the change for review:

```
pytest
mypy .
ruff check .
```

# Review Guidelines

All non-trivial changes that touch code, test, or configuration MUST go through adversarial reviews before commit. (Documentation-only changes are exempted.)

Use the `/adversarial-review` skill (`.claude/skills/adversarial-review/`) as a function call: provide the context of the change — its purpose, the in-scope paths, known-unrelated dirty paths, and out-of-scope items the user has already accepted — and expect a triaged review report without code changes.

The reviewer scrutinizes the change with the following questions:

- Does it achieve the intended purpose?
- Is it bug-free?
- Can it be simplified?
- Is it consistent with the documentation?
- Are there design flaws or anti-patterns?
- Are there design choices that make testing or validation unnecessarily difficult?
- Anything else a senior reviewer would push back on? (Use judgment)

The main agent remains accountable for the execute-review loop:

1. Implement the requested change.
2. Call the review skill on the current dirty tree.
3. Fix blocking findings, plus any non-blocking findings whose fix is trivial.
4. Repeat from step 2 until the skill reports no blocking findings.
5. Ask the user to decide the remaining non-blocking findings, if any: fix, defer, or ignore.

# Version Control Guidelines

- Commit each functionally independent change once fully implemented, tested, and documented.
- Commit messages MUST follow this template. Do not add "Co-Authored-By" line:

```
<Your name: Claude/Codex/Antigravity/...>: <one-line summary>

<One paragraph describing the change in detail. If more than one paragraph is necessary to explain the change, the commit SHOULD be broken down.>
```
