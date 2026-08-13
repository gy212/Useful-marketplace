---
name: spec-acceptance
description: Explicit activation only. Internal final step of a user-invoked spec-workflow. Select only after the active branch hands off, or when the user explicitly names spec-acceptance and thereby opts into the plugin; otherwise do not select.
---

# Spec workflow Acceptance

Use this after every approved task in `<specs_dir>/tasks.md` is complete or explicitly skipped. Resumable review state lives in `<specs_dir>/acceptance_state.json`; acceptance repairs live in `<specs_dir>/acceptance-fixes.md`. Do not create a parallel `acceptance.md` or append repair tasks to the frozen task plan.

## Activation Boundary

This skill may run only inside a user-initiated spec-workflow run. If named directly, enter through the `spec-workflow` router and its resume gates first. A generic request to review or verify code must not activate this plugin.

The user's original opt-in to the current Spec workflow covers the review agents needed to finish that same workflow. Do not pause for a second, ritual authorization before acceptance. Stop only if orchestration would materially expand scope, use a newly sensitive resource, incur an unapproved cost, or perform another separately consequential action.

## Required Announcement

If the branch skill has not already printed the announcement, print:

```markdown
我读到了Spec-acceptance技能。
```

## Hard Rules

- Enter only when `<specs_dir>/tasks.md` has no unchecked tasks and every completed or skipped task has evidence.
- Run local pre-acceptance first, but never report that readiness check as final acceptance.
- Do not report the whole Spec workflow complete until `acceptance-finish` succeeds.
- Use fresh independent reviewers. A reviewer must not edit the files it reviews.
- If the environment cannot orchestrate the agents required by the selected acceptance mode, report that final acceptance is blocked; do not replace them with self-review.
- Read `acceptance-status` before launching agents whenever the state file exists. Resume planned/running work instead of rebuilding it from chat history.
- Record every issue with severity, evidence, affected task IDs, and the reporting agent ID. Unsupported opinions are notes, not issues.
- Return `ACTIONABLE_ISSUES` only for evidence-backed `P0`, `P1`, or `P2` findings. Record `P3`/`P4` advisory findings, but return `PASS` when no P0-P2 finding remains.
- Only P0-P2 findings enter the automatic fix queue. P3/P4 are deferred immediately.
- Run at most two automatic fix-and-re-review cycles. If a new or unresolved P0-P2 finding remains after that limit, this ledger ends blocked: a human may stop without acceptance, or authorize repair/reapproval and a fresh acceptance ledger. Do not reopen or reinterpret the exhausted ledger.
- Re-review only affected units. Previously passing, unchanged units remain sealed.
- A fresh global integration review must pass after all required unit reviews and repairs.
- Never modify the approved baseline or refresh hashes in order to make acceptance pass.

## State A: Preconditions

Read the approved primary artifacts, `tasks.md`, relevant diffs, and recorded verification. Detect the branch from its artifacts:

- Requirements-First: `product.md`, `architecture.md`, `tasks.md`
- Design-First: `design.md`, `requirements.md`, `tasks.md`
- Bugfix: `bugfix.md`, `design.md`, `tasks.md`

Run:

```bash
python <plugin-root>/scripts/validate_spec.py <specs_dir> --pre-acceptance
```

If it fails, return to controlled implementation and repair the reported readiness issue. If state already exists, resume it with:

```bash
python <plugin-root>/scripts/spec_progress.py acceptance-status <specs_dir>
```

Treat the returned mode, agent IDs, affected units, issues, and fixes as authoritative.

## State B: Choose And Initialize The Review Mode

For a new acceptance ledger, select one of these modes:

- `adaptive` (default): one independent first-wave reviewer per unit; add an adversarial reviewer only for a high-risk unit or when the first reviewer reports P0-P2 issues.
- `quick`: low-risk work only; skip per-unit reviewers and run one independent global integration review after the machine gate. Use only when the user explicitly chooses the lightweight acceptance path. The state tool rejects high-risk tasks.
- `full`: first-wave plus adversarial review for every unit. Use when explicitly requested or when broad, critical coupling warrants the extra cost.

Initialize once; the selected mode is frozen for that ledger:

```bash
python <plugin-root>/scripts/spec_progress.py acceptance-init <specs_dir> --mode adaptive
```

Omitting `--mode` selects `adaptive`. The tool freezes the original task IDs and task text, builds contiguous review units of at most three low-risk tasks, isolates high-risk tasks, and plans only the reviewers required by the selected mode. An unfinished legacy 0.2.x ledger resumes as `full`; an already accepted legacy ledger remains accepted.

## State C: Independent Unit Review

Skip this state in `quick` mode. Otherwise, launch only planned `first_wave` agents. Before each launch, call `acceptance-start-agent` (or MCP `spec_acceptance_start_agent`). Each agent owns exactly one unit.

Every prompt must include:

- workflow type and approved artifacts
- assigned task IDs and their frozen task text
- relevant changed files, diffs, tests, logs, and verification evidence
- checks for completeness, spec adherence, overbroad fallback, missing verification, regression risk, and unapproved behavior
- the P0-P4 evidence standard and the PASS/ACTIONABLE_ISSUES rule above

Expected report:

```markdown
## Review Unit
- Unit: [task IDs]
- Status: PASS | ACTIONABLE_ISSUES
- Completion: [complete / incomplete with evidence]
- Spec Adherence: [strict / deviation with evidence]
- Verification: [sufficient / missing with evidence]
- Issues: [P0-P4 findings with evidence or n/a]
```

For each agent, use this order:

1. Start the agent in the ledger.
2. Receive the report.
3. Record every finding with `acceptance-record-issue --agent <agent-id>` while the agent is running. P3/P4 findings are still recorded.
4. Complete the agent with `PASS` or `ACTIONABLE_ISSUES`.

The ledger refuses to finish when an `ACTIONABLE_ISSUES` result is not bound to at least one recorded issue.

## State D: Conditional Adversarial Review

Launch only adversarial agents that appear as planned in `acceptance-status`. The state machine enforces that the matching first-wave reviewer finishes first.

An adversarial agent reviews the same unit, first-wave report, and evidence, and tries to disprove the apparent pass by checking missed work, spec drift, weak tests, hidden regressions, unsafe fallback, and unsupported assumptions. Record and complete its result in the same order as State C.

Mode behavior:

- `adaptive`: high-risk units receive this reviewer immediately; a low-risk unit receives one only after its first-wave reviewer reports P0-P2 issues.
- `full`: every unit receives one.
- `quick`: none are planned.

## State E: Fix And Delta Re-review

After all currently planned review agents finish, run:

```bash
python <plugin-root>/scripts/spec_progress.py acceptance-plan-fixes <specs_dir>
```

The command updates `<specs_dir>/acceptance-fixes.md` without changing `tasks.md`:

- P0-P2 findings become evidence-backed fixes.
- P3/P4 findings become deferred advisories.
- after two automatic fix batches, any further P0-P2 finding permanently blocks this ledger; the human must stop without accepting it or choose repair/reapproval followed by a fresh ledger.

Start and complete each planned fix with evidence. In a Git workflow, commit the repaired code and its acceptance-fix ledger update before re-review; the global reviewer must inspect a clean, committed business-code state. Then run:

```bash
python <plugin-root>/scripts/spec_progress.py acceptance-next-round <specs_dir>
```

For `adaptive` and `full`, only affected units are reset and re-reviewed; unchanged passing units stay sealed. For `quick`, the next round runs a new global integration review. Repeat only while the automatic-fix limit permits it.

## State F: Global Integration Review

When all required unit reviewers pass and no issue, fix, or affected unit remains, the ledger plans one `GLOBAL` integration agent. In `quick` mode this is the only reviewer.

The integration agent receives the approved artifacts, full task plan, combined diff, unit-review conclusions, and verification evidence. It checks cross-unit behavior, end-to-end spec coverage, interaction regressions, release/rollback risk, and whether the combined result still matches the approved baseline.

Start and complete it through the same ledger commands. Record integration findings against unit `GLOBAL` and include the affected task IDs. A P0-P2 result returns to State E; after its targeted repairs and unit re-review, a new integration agent must pass.

Finally run:

```bash
python <plugin-root>/scripts/spec_progress.py acceptance-finish <specs_dir>
```

The finish command fails closed unless all of these are true:

- local pre-acceptance still passes and the approved baseline has not drifted
- every mode-required unit reviewer passed in the unit's latest review round
- every ACTIONABLE_ISSUES result is bound to recorded issue IDs
- no agent or fix is pending/running
- no issue is unresolved and no affected unit awaits re-review
- the current global integration agent passed
- the primary artifacts, `tasks.md`, `spec.yml`, reviewed Git commit, and non-ledger worktree content still match the integration review snapshot

The stored snapshot represents the reviewed pre-finish state. `acceptance-finish` writes final ledger metadata only after this comparison succeeds.

## State G: Final Branch

If `acceptance-finish` succeeds, summarize the workflow type, approved specs, completed/skipped tasks, key verification, selected acceptance mode, and final result. Do not dump raw review transcripts.

When the run is inside a git repository, close the delivery chain from `../spec-workflow/SKILL.md`:

- ensure repair commits were already created before their re-review; after finish, commit the final acceptance-state-only update and push `spec/<run-id>`
- mark the PR ready and post one concise completion summary
- print merge and worktree-cleanup commands for the human; never merge or remove the worktree autonomously
- if no remote or `gh` exists, keep the branch local and provide the appropriate local handoff

If acceptance remains blocked, report only the evidence-backed P0-P2 findings, affected task IDs, automatic-fix rounds used, and the concrete decision needed next.
