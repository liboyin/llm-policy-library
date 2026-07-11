---
name: adversarial-review
description: Review and triage a dirty tree for a non-trivial code, test, or configuration change. Given the change context, run independent plan-mode reviews on two different model families, verify findings against the source, and return a triaged report. Do not modify code or drive the fix loop.
---

# Adversarial Review

This skill is a read-only review function: the caller provides the context of a change sitting in the dirty tree, and the skill returns a triaged review report. Never modify the tree — no edits, fixes, stashes, formatters, or test commands that rewrite files.

## Scope

Work from the caller's context: purpose of the change, paths in scope, known-unrelated dirty paths to ignore, and out-of-scope items the user has already accepted. Inspect the dirty and staged diffs read-only. If it is unclear which dirty paths belong to the change, ask the caller rather than reviewing the whole tree by default.

## Reviewers

Run at least two independent reviewers from different model families in parallel, each with the same prompt and no sight of the other's output:

- Claude Fable 5, via a plan-mode subagent.
- Antigravity (Gemini), via the CLI:

  ```bash
  agy --mode plan --sandbox -p "$(cat <prompt-file>)" < /dev/null
  ```

Every reviewer must see the entire change in a single pass. Splitting files across reviewers hides cross-file defects — a contract set in one file and misread in another falls in the gap between reviewers who each saw only half the change.

The prompt must let a reviewer with no other context do a senior review: purpose of the change, repository path, in-scope paths, dirty paths to ignore, the review questions from `AGENTS.md`, and known out-of-scope items. Ask for findings with severity, `file:line`, a one-sentence defect, and a concrete fix.

Operational notes for `agy`:

- Always close stdin with `< /dev/null`; otherwise it may wait indefinitely.
- Do not use `--dangerously-skip-permissions`.
- Ask for senior code review, not "vulnerability hunting" — security-scanner wording degrades results.
- Reviewing a whole change in one call is slow (~10 minutes); if wrapping with `timeout`, allow for that, and treat exit `124` as timeout's status, not Antigravity's.

Substitute a reviewer only when the requested one is unavailable, keeping two model families. If two-family review is impossible, report the review as blocked and let the caller decide whether to proceed with reduced coverage.

## Verify and Report

Reviewer output is hypotheses, not facts. Confirm each finding against the actual diff and surrounding source before reporting it; for claims about third-party behavior, check installed source or official documentation. A finding raised by both reviewers deserves extra attention, but a single verified finding is enough to report.

Report confirmed findings triaged into blocking (correctness, safety, data loss, test failures, broken contracts, anything that would make the commit misleading) and non-blocking (deferrable cleanup or improvement). Briefly note dismissed findings and any reviewer failures or substitutions. End with a clear verdict: blocking issues found, only non-blocking issues, no confirmed issues, or review blocked.
