---
name: adversarial-review
description: Use when a non-trivial change touching code, tests, or configuration is ready for commit — runs independent reviews across two model families, verifies findings against the tree, and returns a triaged report without making code changes.
---

# Adversarial Review

Act as a function call: the caller supplies the context below, and you return a triaged review report. You MUST NOT modify any file — no edits, fixes, stashes, or formatters. If run in the main conversation, dispatch this process to a subagent to keep the main context clean.

## Required context from the caller

- **Purpose:** what the change is supposed to achieve.
- **In-scope paths:** the files/directories under review.
- **Known-unrelated dirty paths:** changes in the tree to ignore.
- **Accepted out-of-scope items:** findings the user has already decided not to act on — do not re-raise them.

If it is unclear which dirty paths belong to the change, ask the caller rather than reviewing the whole tree by default. If other context is missing, state your assumption for it in the report header rather than blocking.

## Procedure

1. **Gather context:** `git status`, `git diff` (staged and unstaged), new untracked files in full, and enough surrounding code, tests, and documentation (`README.md`, `TODO.md`, `docs/`) to judge the change in context.

2. **Dispatch two reviewers in parallel,** from different model families, with the identical prompt and no sight of each other's output. Each must see the entire change in one pass — splitting files across reviewers hides cross-file defects.
   - **Reviewer 1:** Claude Fable 5 via a plan-mode subagent (fall back to Claude Opus 4.8).
   - **Reviewer 2:** Antigravity (Gemini) via `agy --mode plan --sandbox -p "$(cat <prompt-file>)" < /dev/null`. The `< /dev/null` prevents hanging; never use `--dangerously-skip-permissions`. Ask for a senior code review, not "vulnerability hunting" (degrades results). Allow ~10 minutes; `timeout` exit `124` is a timeout, not an `agy` failure; on agy's internal `Error: timeout waiting for response`, retry restating the bounded-commands rule below.

   The prompt MUST contain the purpose, repository path, in-scope paths, paths to ignore, accepted out-of-scope items, a scratch directory assigned to that reviewer, and these ground rules: do not modify the repo or anyone else's scratchpad; mutation-test only on a scratch copy outside the repo — copy `llm_policy_library/`, `tests/`, and `pyproject.toml`, run `pytest` from the scratch root, and prove the copy shadows the repo's editable install by planting a loud mutant before trusting any survivor; keep every command foreground and bounded; confirm `git status` shows the same dirty set at exit as at entry.

   **Questions to evaluate:**
   - Does it achieve the intended purpose? Is it bug-free? Any gaps or regressions?
   - Can it be simplified? Is it consistent with the documentation?
   - Are there design flaws, anti-patterns, or choices that make testing difficult?
   - Can each new assertion fail for the reason it claims, or is it tautological given the mechanism that satisfies it? Build a mutant toward a *future* regression (the caller has already tried the revert) and report which mutants were killed.
   - Do the change's own factual claims — docstrings, comments, the `TODO.md` status block — hold under measurement rather than reading? For claims about failure behavior, induce the failure.

   **Freeze the tree until both reviewers report:** fingerprint the in-scope files (`md5sum`) before dispatch. The freeze binds the caller too — fixes wait, and the caller's own mutation runs use a scratch copy outside the repo, never in-place edits.

   **Serialize gate measurements:** `pytest` runs share the repo-root `.coverage` data file, so concurrent coverage runs corrupt each other. One runner on the tree at a time, reviewers included.

3. **Reclaim the tree:** check for stray files *and* stray listeners — a leftover `uvicorn` still holds its port and answers later manual checks with stale code. Kill by PID and verify per the cleanup rule in AGENTS.md; never `pkill -f`.

4. **Verify findings:** reviewer output is hypotheses, not facts. Confirm each finding against the diff and surrounding source, and empirically where cheap via the project's read-only test and lint commands (see AGENTS.md).

5. **Triage** every *verified* finding into exactly one severity:
   - **Blocking:** bugs, broken tests, unmet requirements, security problems, broken contracts, violations of AGENTS.md MUSTs.
   - **Non-blocking:** should be fixed, but the change is shippable without it.
   - **Nit:** style or taste; mention only if cheap to fix.

## Report format

Return exactly this structure, and no code edits:

```markdown
## Adversarial Review Report

**Purpose reviewed against:** <one line>
**Reviewers:** <e.g., Fable 5 (subagent), Antigravity (agy)>
**Verification run:** <commands run and their pass/fail results; note any reviewer timeouts or failures>

### Blocking
1. `path/to/file.py:42` — <finding>. Why it matters: <one line>. Suggested direction: <one line, no code>.

### Non-blocking
...same shape...

### Nits
...same shape...

### Verdict
<"No blocking findings" | "N blocking findings — fix and re-review" | "Review blocked — insufficient model availability">
```

If a section has no findings, write "None." Findings MUST cite a file path (with line where possible) and MUST NOT include rewritten code — describe the direction, let the implementer implement.
