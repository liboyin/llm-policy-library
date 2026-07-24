---
name: adversarial-review
description: Read-only, multi-model review and triage of a non-trivial code, test, or configuration change.
---

# Adversarial Review

Return a verified adversarial review report without editing, fixing, stashing, formatting, or otherwise writing to the repository. Reviewers MAY propose remedies but MUST NOT implement them.

## Input

The caller provides the purpose; target (dirty tree, commit, or range); in-scope paths; unrelated dirty paths to preserve and ignore; accepted out-of-scope findings not to re-raise; and any prior mutation evidence. Ask only if target or scope is unclear; report other assumptions.

## Procedure

1. **Snapshot and read.** Capture `git status`; SHA-256 hashes of in-scope and dirty tracked files; path/status metadata (not content) of ignored untracked files; and baseline PIDs/listeners if a command might start a service. Read the full target diff (staged changes and in-scope untracked files included for worktrees), relevant source/tests, `README.md`, `TODO.md`, and `docs/`. Do not read ignored untracked content.

2. **Dispatch reviewers in parallel.** Give the complete change and identical review prompt—apart from identity and separate scratch paths—to two isolated reviewers from different model families; never split files. Select the first reviewer from the main agent:

   - **Codex:** `gpt-5.6-sol` high reasoning via tool use.
   - **Claude Code:** `claude-fable-5` high reasoning via tool use.

   Use **Gemini 3.1 Pro High** as the second reviewer via Antigravity CLI:

   ```bash
   agy --mode plan --sandbox --model gemini-3.1-pro-high --print-timeout 10m \
     -p "$(cat <prompt-file>)" < /dev/null
   ```

   Never pass `--dangerously-skip-permissions`; request senior review, not “vulnerability hunting.” Any wrapper timeout MUST exceed `--print-timeout` (exit 124 belongs to the wrapper). Retry one internal response timeout with the bounded-command rule restated. If either required reviewer or model family is unavailable or remains unreachable after that retry, you MUST stop and ask the user what to do; do not substitute, proceed with one reviewer, or issue a verdict without their direction. Record the user's decision in the report.

   The prompt MUST include all caller input, repository and scratch paths, the report fields, and these rules:

   - Keep commands foreground and bounded; change neither the repository nor another reviewer's scratch. Compare repository status and hashes at exit.
   - Judge purpose, correctness, regressions, simplicity, documentation consistency, design/operational anti-patterns, and testability. Check that assertions can fail for their stated reason rather than being tautological. Measure factual claims; induce failures only in a safe, controlled setup.
   - You MAY propose remedies, but MUST NOT implement them. Present each remedy as a proposal for the main-agent to review.
   - For changed code/tests, mutation-test new assertions only in scratch. Discover and copy every imported/runtime-read path; the current minimum is `llm_policy_library/`, `tests/`, `loadtest/`, `evaluation/`, `static/`, and `pyproject.toml`. First pass the baseline suite; then make a loud mutant fail to prove the copy shadows editable installs. Restore it and test an applicable revert mutant unless evidence was supplied, plus a future-regression mutant; report kills and survivors.

   Freeze repository edits until both reviewers return. Serialize repository-root coverage runs because they share `.coverage`; distinct scratch roots may test concurrently.

3. **Reclaim and verify.** Compare exit status and hashes with entry. Remove and verify only scratch artifacts and processes/listeners proven from the baseline and recorded PIDs to belong to this review; MUST NOT use `pkill -f`. Any unexplained repository change blocks the review.

4. **Verify, triage, and report.** Treat reviewer claims as hypotheses: validate each diagnosis against the diff/source and, when safe, by measurement in scratch; check third-party behavior in installed source or version-matched official docs. Review each proposed remedy separately from the diagnosis before accepting, implementing, or including it in the final report. Classify each verified finding once: **Blocking** for bugs, broken tests, requirements/contracts/security failures, misleading claims, or `AGENTS.md` MUST violations; **Non-blocking** for deferrable improvements; **Nit** for cheap style/taste only. Record rejected hypotheses under **Dismissed**.

## Report

Return this structure without rewritten code. Every finding MUST cite a path and, when possible, a line; write “None” for empty sections.

```markdown
## Adversarial Review Report
**Purpose:** ...
**Target:** ...
**Reviewers:** <models/tools; substitutions/failures>
**Assumptions:** ...
**Verification:** <commands and results>

### Blocking
1. `path:line` — <defect>. Impact: ... Proposed remedy (optional): ...
### Non-blocking
...
### Nits
...
### Dismissed
<hypothesis and measured reason>
### Verdict
<No blocking findings | N blocking findings — fix and re-review | Review blocked — model availability or repository integrity>
```
