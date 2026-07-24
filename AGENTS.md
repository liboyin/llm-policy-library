This document contains guidelines that all AI agents MUST follow.

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, NOT RECOMMENDED, MAY, and OPTIONAL in this document are to be interpreted as described in BCP 14 (IETF RFC 2119 and RFC 8174) when, and only when, they appear in all capitals, as shown here.

# Meta Guidelines

- If not running in a Docker container, you MUST stop and confirm with the user before continuing.
- You MUST read relevant code & documentation, and plan your actions before making a file change.
- State assumptions explicitly. When you notice an ambiguity that materially affects the project (e.g. scope, architecture, dataflow, correctness, or security), you MUST confirm with the user before continuing.
- Isolated subtasks (tasks that require little or no additional context from the main conversation and produce a small, well-bounded result for follow-up work) SHOULD be executed in subagents to keep the main context window clean.
- Claiming an action took effect (process killed, file removed, gate passed) MUST rest on a check whose output would differ had it failed, read for what it says rather than the result you expect — assert the negative (a `ps`/`ss` query returning nothing) instead of scanning a listing for an absence. A non-zero exit status is a failure until explained.
- Clean up any process you start and verify the cleanup — or start it with the Bash tool's `run_in_background` so the harness owns its lifecycle. Never use `pkill -f <pattern>`: it matches the tool's own `bash -c` command line and kills its own shell (exit 144), skipping the rest of the line. `kill` by PIDs resolved beforehand, from a command not containing the pattern, then confirm the port is free.
- Before considering a task done, you MUST re-check that all instructions in this file are followed.

# Documentation Guidelines

- Each document SHOULD own its assigned topic, and other docs SHOULD link or summarize without becoming competing sources of truth.
- Documentation MUST be updated as soon as its content no longer reflects the latest state of the project.
- `TASK.md` describes the end goals of this project.
- `TODO.md` is the phased execution plan and log. Its status blocks record what was actually done and decided.
- `README.md` describes project structure, architecture, dataflow, and build & test procedures.
- `docs/architecture.md` is the system design spec — agent interaction flow, determinism & grounding, security, and scalability. `docs/azure-setup.md` is the Azure provisioning guide.
- Design decisions & assumptions MUST be documented in whichever document fits best (e.g. README, a design doc, or the task's execution log), and SHOULD record the reasoning behind them.
- Every empirical claim written into a document, status block, docstring, or code comment MUST be measured right before it is written — not reasoned about, and not carried forward from an earlier draft. Descriptions of failure behavior count: induce the failure and look at it. Record the setup a measurement depended on so it can be re-taken, and mark knowingly unverified claims as such.
- New or modified functions/methods in non-test scripts MUST have Google-style docstrings; unit test functions MUST have a one-line docstring.

# Implementation Guidelines

- Implement only what was asked with small, surgical changes. Do not add features or unrelated refactors unless explicitly asked to.
- Before deleting a layer that looks redundant, enumerate what *only* that layer carries — user-facing copy, error shape, ordering, timing, diagnostics — and confirm each has another home. Duplication is rarely pure.
- Prefer the simplest implementation. Each function/class/module MUST have a single responsibility and a well-defined interface; other SOLID principles MAY be relaxed in favor of simplicity.
- Implementations MUST be easy to test with minimal mocking. Pure functions are preferred, and side effects SHOULD be isolated.
- Code SHOULD use up-to-date features from languages, libraries, frameworks, and external services. These change quickly and assumptions about them go stale, most dangerously at planning time. You SHOULD verify behavior empirically or against the documentation for the version in use before planning or building on them.

# Test Guidelines

- Tests MUST encode WHY behavior matters, not just WHAT it does. A test that does not fail when business logic changes is wrong.
- Every new assertion MUST be mutation-tested, with at least one mutant pushing toward a *future* regression rather than only reverting the fixed bug — a revert cannot expose an assertion the new implementation has made tautological. When a constraint satisfies a requirement, also assert the property it trades away (e.g. a schema `maxLength` guarantees the cap but permits mid-word truncation).
- Whenever measurable, statement and branch coverage MUST each be ≥80% for each file and at the project level. `--cov-fail-under=80` only gates the project total; per-file coverage MUST be reviewed manually, aided by `--cov-report=term-missing`.
- Thin wiring/glue modules with no branching logic — e.g. the `static/index.html` frontend script or a server startup shim — MAY be excluded from coverage measurement when the exclusion and its rationale are documented; their behavior MUST instead be covered by an end-to-end smoke test.
- See `pyproject.toml` for test configs. Note that tests run in random order (`pytest-randomly`).
- Order test functions to match the source file's function order.
- Import the module under test as `import llm_policy_library.my_module as testee`; call functions as `testee.function_name` and mock attributes via `patch.object(testee, 'attribute', ...)`.
- After any code change, all of the following unit tests and static analysis MUST pass:

```bash
pytest
mypy .
ruff check .
```

# Review Guidelines

All non-trivial changes that touch code, test, or configuration MUST go through adversarial reviews before commit. (Documentation-only changes are exempted.)

A finding's diagnosis and its proposed remedy MUST be validated separately, wherever the finding came from — a correct diagnosis does not imply a correct fix, and suggested one-liners have repeatedly been worse than the defect they named. Reproduce the problem, then measure the remedy against it; accepting a remedy is authoring it. Declined or superseded remedies need a measured reason, recorded.

The `/adversarial-review` skill (`.agents/skills/adversarial-review/SKILL.md`) owns the review procedure: the caller context it requires, the questions it scrutinizes with, and its report format. Call it as a function on the current dirty tree and expect a triaged findings report without code changes.

The main agent remains accountable for the execute-review loop:

1. Implement the requested change.
2. Call the review skill on the current dirty tree.
3. Fix blocking findings, plus any non-blocking findings whose fix is trivial.
4. Repeat from step 2 until the skill reports no blocking findings. Do not shorten the loop because the remaining delta looks trivial.
5. Ask the user to decide the remaining non-blocking findings, if any: fix, defer, or ignore.

Undoing a decision (a revert, a rolled-back approach) is itself a change and re-enters the loop at step 2. Verify its completeness against `HEAD`, not from recollection: `git diff HEAD -- <affected paths>` must show nothing beyond the intended delta, in both directions — the pieces most easily orphaned are the ones applied *while* the undone decision was in force.

# Version Control Guidelines

- Commit each functionally independent change once fully implemented, tested, and documented.
- Stage by explicit path, and read `git status` immediately before committing — review agents and tooling leave stray artefacts that `git add -A` or `git commit -a` would carry into history.
- Commit messages MUST follow this template. Do not add "Co-Authored-By" line:

```
<Your name: Claude/Codex/Antigravity/...>: <one-line summary>

<One paragraph describing the change in detail. If more than one paragraph is necessary to explain the change, the commit SHOULD be broken down.>
```
