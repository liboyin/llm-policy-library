---
name: adversarial-review
description: Run the AGENTS.md adversarial review of pending changes past several independent LLM reviewers (Claude Fable 5, Gemini via the Antigravity CLI) in parallel, then verify and triage their findings. Use before committing any change that touches code, tests, or configuration. Skip for documentation-only changes, which the user reviews.
---

# Adversarial review

Runs the review that `AGENTS.md` § Review Guidelines mandates before a commit. Several
models from **different families** review the same change independently; you then verify
every finding against the source and triage it.

Reviewers disagree, and that is the point. A model reviewing its own family's output shares
its blind spots — on Phase 2, the Gemini reviewer alone caught a bare `KeyError` escaping
`build_parameter_map` that the Claude reviewer missed, and the Claude reviewer alone caught
that the index was dropped before `zip(strict=True)` could reject a short embedding
response. Neither found both.

Reviewer order does not matter. Launch them all, then merge.

---

## 1. Decide whether a review is required

`AGENTS.md` requires a review only for a change that involves **code**. A
documentation-only change is the user's to review — say so and stop.

Run the gate over **the paths you are about to commit**, not the whole dirty tree. A
working tree can carry unrelated dirty files — this repo does — and judging by the tree
would drag them into the decision.

```bash
# What you are committing: staged paths, or the explicit paths you will pass to `git commit`.
git diff --cached --name-only
```

The change is **documentation-only** if every one of those paths matches `\.md$`, `^docs/`,
or `LICENSE`. Anything else counts as code — source, tests, `pyproject.toml`,
`.devcontainer/`, CI config, lockfiles. Configuration is not documentation: a changed
dependency pin can break the build.

```bash
git diff --cached --name-only | grep -vE '(\.md$|^docs/|^LICENSE$)' \
  || echo "documentation-only — the user reviews this; do not launch reviewers"
```

If nothing is staged yet, substitute the list of paths you plan to commit. If the change is
documentation-only, tell the user it needs their review and stop.

## 2. Fix the scope

Collect the changed source files and, for each, its test file. Reviewers read the working
tree, so give them **paths**, not a pasted diff — a diff hides the surrounding code that
makes a finding right or wrong. Keep a diff on hand for your own triage:

```bash
git diff HEAD > "$SCRATCHPAD/review.diff"
```

## 3. Build one prompt, shared by every reviewer

Read `AGENTS.md` § Review Guidelines and copy its question bullets **verbatim** into the
prompt. `AGENTS.md` owns that list; restating it here would create a second copy that
silently drifts from the first.

Fill this template:

```
You are a senior <language> reviewer. Read <source file> and <its test file>.

Answer each question below, citing file:line for every claim.

<the question bullets, copied from AGENTS.md at run time>

For the documentation-consistency question, check README.md, TODO.md, and the module
docstrings: does any of them now assert something untrue of the code, or does the code do
something significant that no document mentions?

VERIFIED FACTS — do not re-litigate:
  <e.g. pytest N passed, 100% branch coverage; mypy clean; ruff clean; live run indexed
   1,014 documents>

OUT OF SCOPE — already tracked; do not report:
  <e.g. deferred items, later phases>

OUTPUT: numbered findings. Each: SEVERITY (BLOCKER/MAJOR/MINOR/NIT), file:line, the defect
in one sentence, and a concrete fix. Write "no findings" for any question with none.
Do not modify any files. Be terse. Max 20 lines.
```

Always supply the verified facts and the out-of-scope list. Without them a reviewer burns
its budget re-deriving what you already know, and reports deferred decisions as fresh bugs.

Give every reviewer the **same** prompt. Never show one reviewer another's findings — that
destroys the independence you are paying for.

## 4. Run the reviewers, in parallel

| ID | Model family | Invocation |
|---|---|---|
| `fable` | Claude Fable 5 | `Agent` tool, `subagent_type: general-purpose`, `model: "fable"` |
| `agy` | Gemini (Antigravity CLI) | `agy -p "<prompt>" < /dev/null` — one source file + its test per call |

Launch the `Agent` reviewers first (they run in the background), then run the CLI reviewers
with `Bash` while those work. Collect everything before triaging.

### Antigravity CLI (`agy`) — learned the hard way, 2026-07-10

- **Always append `< /dev/null`.** Otherwise `agy` blocks forever on a stdin read (`State:
  S`, `WCHAN: unix_stream_read_gen`, `fd 0 -> socket`, 0% CPU, no children). It re-points
  its own stdout and stderr into `~/.gemini/antigravity-cli/log/cli-*.log`, so whatever
  prompt it waits on is invisible. With stdin closed it answers in ~30s.
- **`--print-timeout` does not bound the run.** A run with `--print-timeout 20s` was still
  alive minutes later.
- **`agy` never exits 124.** `124` is GNU `timeout`'s own status for "the command overran, I
  killed it". If you wrap `agy` in `timeout`, do not report 124 as an `agy` error code, and
  allow at least 240s.
- **Gemini refuses security-flavoured requests.** "List every bug", "bug hunting",
  "vulnerability scanning", or "security analysis" on named files gets a flat refusal
  recommending static-analysis tools. Phrase it as an ordinary senior code review; it then
  complies with specific, useful findings.
- Scope each call to one source file plus its test file. Multi-file reviews are slow.
- `--mode plan` and `--sandbox` both read files without prompting, if you want a read-only
  guarantee.
- **Never** pass `--dangerously-skip-permissions`. It is not the fix for any of the above.

### Adding a reviewer

Add a row to the table. A reviewer needs a non-interactive invocation that can read the repo,
a way to close its stdin, and — the point of the exercise — a model from a **family the
other reviewers do not share**. Record its refusal triggers and output quirks beside it, the
way `agy`'s are recorded above. Two Claude models are one reviewer, not two.

## 5. Verify every finding before you act on it

A reviewer's finding is a hypothesis. Confirm it against the real code first; a confidently
wrong fix is worse than no fix.

- Read the cited `file:line`. Does the defect exist as described?
- For any claim about a **third-party library's behaviour**, read the installed source
  rather than trusting the model or your own memory:
  `python -c "import inspect, openai._base_client as m; print(inspect.getsource(m))"`.
  On Phase 2 this is how "the `openai` SDK logs its retries at INFO" was confirmed before
  acting on it.
- A finding can be true but already tracked, or true but out of the commit's scope.

Label each finding `CONFIRMED`, `FALSE POSITIVE`, `DUPLICATE`, or `OUT OF SCOPE`. Findings
raised by two independent reviewers carry more weight, but a single-reviewer finding that
verifies is real — most of the Phase 2 defects were found by exactly one reviewer.

## 6. Triage

Per `AGENTS.md`: *"Fix trivial issues. For others, stop and confirm with the user."*

**Trivial — fix now.** Inaccurate docstrings and comments; a missing guard that matches a
pattern already used elsewhere in the file; a test whose name or docstring misdescribes what
it actually exercises; a redundant assertion; dead code.

**Substantive — stop and ask.** Public interfaces; module decomposition; new dependencies;
any change to behaviour or contract; anything that widens the commit's scope. Present the
trade-off and a recommendation, then let the user choose.

## 7. Close the loop

- Re-run the full gate: `pytest`, `mypy .`, `ruff check .`.
- If the change has a runtime surface, exercise it again. Fixes made after a live run mean
  the code that ships is no longer the code that was verified.
- Record the outcome in the commit body: which reviewers ran, `no findings` or the findings
  fixed, and which substantive findings the user decided.
