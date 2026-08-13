---
name: spec-bugfix
description: Explicit activation only. Internal branch of a user-invoked spec-workflow. Select only after the active router hands off, or when the user explicitly names spec-bugfix and thereby opts into the plugin; otherwise do not select.
---

# Spec workflow Bugfix

Use this branch to restore existing expected behavior with evidence, root-cause analysis, and minimal safe implementation.

## Activation Boundary

This branch may run only inside a user-initiated spec-workflow run. If named directly, enter through the `spec-workflow` router and intake gates first. Generic bug reports and fix requests must not activate it.

If the entry router has not already printed the announcement, print:

```markdown
我读到了Spec workflow技能。
我会按照“Bugfix”分支来完成。
```

## Hard Rules

- Do not write business source code until the human explicitly replies the preferred approval phrase `批准规范，启动执行`.
- For compatibility, also accept the legacy Bugfix approval phrase `批准 bugfix 规范，启动执行`.
- Generate or update spec artifacts in the selected `<specs_dir>`; they are the source of truth.
- New specs must also generate `<specs_dir>/spec.yml` and `<specs_dir>/progress.md` from the templates in `../../assets/templates/`.
- Carry the `Intake Handoff / 澄清交接摘要` into `bugfix.md`; do not leave intake conclusions only in chat.
- Do not hide root cause behind symptom-only patches.
- Do not delete failing tests, weaken assertions, or disable warnings just to pass validation.
- Keep the fix minimal and avoid unrelated refactors.
- If the bug requires new user-visible capability or product scope change, stop and reroute to Feature.
- If the bug evidence comes from final acceptance and `<specs_dir>/acceptance_state.json` exists, do not append repair tasks to the original `<specs_dir>/tasks.md`. Use `<specs_dir>/acceptance-fixes.md` and the acceptance progress tools instead, then return to `../spec-acceptance/SKILL.md`.

## High-Risk Warning

If the task involves authentication, authorization, payments, billing, database schema changes, data repair, distributed consistency, cache consistency, secrets, encryption, sensitive data, incident mitigation, rollback, or hotfix work, include this warning even when the router was skipped:

```markdown
> [!WARNING]
> 高风险变更警告：当前任务涉及核心系统或高影响范围区域，必须进行人类深度审查，切勿草率合并。
```

## Intake Precondition

Before State A, if the current conversation does not already include an `Intake Handoff / 澄清交接摘要` with `Status: complete` or `Status: assumptions-accepted`, read and follow `../spec-intake/SKILL.md`. If intake asks questions or is blocked, stop and wait for the human answer before generating specs.

## State A: Bug Analysis Clarification

Inspect available evidence before asking questions: failing tests, logs, screenshots, issue text, alerts, recent changes, existing specs, manifests, and relevant code paths.

Clarify all bug-critical gaps:

- current incorrect behavior and evidence
- expected behavior
- behaviors that must remain unchanged
- reproduction steps, frequency, and affected inputs
- environment, version, and deployment context
- suspected root cause, root-cause confidence, and recent related changes
- release, rollback, monitoring, and data-repair constraints when relevant

If clarification is needed, continue the multi-round intake loop focused on reproduction, evidence, scope, unchanged behavior, and regression constraints. Unknowns may be recorded as assumptions or risks only if the user explicitly accepts that.

## Root-Cause Investigation Discipline

No fix proposal before the root cause is understood. Follow this order before writing `design.md`:

1. 根因调查: read the full error and stack trace, reproduce the failure stably, check recent changes (`git diff`, dependencies, config), and trace the data flow from the failure point back to its source. In multi-component paths, add boundary diagnostics to locate the failing layer first. Fix at the source, not at the symptom.
2. 模式分析: find similar working code in the same codebase, read it completely, and list every difference between the working and failing paths without dismissing any as irrelevant.
3. 单一假设: write down one specific hypothesis and design the smallest change that tests it — one variable at a time. If it fails, form a new hypothesis; do not stack a new fix on top of a failed one.

The root-cause analysis in `design.md` must cite investigation evidence: the data-flow trace, the reference implementation compared against, and the hypothesis that was tested. A speculative root cause without evidence is not ready for approval.

Time pressure does not waive this discipline. If investigation confirms the problem is genuinely environmental or timing-based, record the investigation, design reasonable handling (retry, timeout, monitoring), and say so in `design.md`.

## State B: Bugfix Spec Artifact Generation

If this is an acceptance repair:

- Treat `<specs_dir>/acceptance_state.json` as the source of truth for round, issue severity, affected units, and pending fixes.
- Treat `<specs_dir>/acceptance-fixes.md` as the repair task list.
- Do not regenerate `<specs_dir>/tasks.md`, do not append `B-xxx` tasks to it, and do not change the frozen original task IDs.
- Use `python <plugin-root>/scripts/spec_progress.py acceptance-fix-start <specs_dir> F-xxx` before editing code and `acceptance-fix-complete` with evidence after verification.
- When all planned fixes are done, resume `../spec-acceptance/SKILL.md` via `acceptance-status` / `acceptance-next-round`.

For ordinary bugfixes, continue with the normal artifact flow below.

Use the plugin templates from `../../assets/templates/`:

- `bugfix_template.md`
- `bugfix_design_template.md`
- `bugfix_tasks_template.md`
- `progress_template.md`
- `spec_index_template.yml`

Generate:

- `<specs_dir>/bugfix.md`: intake handoff, defect summary, impact, environment, reproduction evidence, automated-reproduction status, substitute evidence when needed, current behavior, expected behavior, unchanged behavior, scope boundaries, and non-goals
- `<specs_dir>/design.md`: root-cause analysis, code-path trace, minimal fix strategy, alternatives, affected surface, explicitly untouched areas, test strategy, and non-automated verification risks when applicable
- `<specs_dir>/tasks.md`: ordered atomic tasks using `- [ ]`, starting with reproduction or strongest available evidence, then minimal fix, regression protection, and verification. Each task must include status, files, verify, evidence, depends_on, risk, covers, and parallelizable.
- `<specs_dir>/progress.md`: resume entrypoint with workflow status, current task, approval state, branch, commit, blockers, and recovery notes
- `<specs_dir>/spec.yml`: Kiro-compatible machine index with workflow, mode, approval, risk level, artifact paths, requirement IDs, task graph, current task, and checkpoint

Bugfix defaults to `strict`. Do not use Quick Plan for P0/P1, production incidents, data repair, auth, payment, schema, consistency, secrets, encryption, or sensitive-data work.

Before review, replace all template placeholders with concrete content. If a template section does not apply, state that explicitly with the reason instead of leaving placeholder text.

Task plan quality bar:

- Every task must fill the `接口` field: `消费` lists the exact signatures, contracts, or failure-proof entry points taken from upstream tasks, `产出` lists what downstream tasks rely on; write `无` explicitly when empty.
- Placeholder text such as `TBD`, `待定`, `处理边界情况`, or `类似 B-xxx` inside a task is a plan failure, not an acceptable draft. Every task needs concrete file paths, real commands, and expected verification output.
- Before requesting approval, self-review the plan in place: every BUG/FIX/SAFE ID is covered by at least one task's `覆盖`, no placeholders remain, and interface references are consistent across tasks. Fix findings directly instead of reporting them.

If automated reproduction cannot be created, record why in `bugfix.md`, describe substitute evidence strength and limits, and use the strongest available verification substitute.

The preferred approval phrase for implementation is:

```text
批准规范，启动执行
```

The legacy Bugfix phrase remains valid for compatibility:

```text
批准 bugfix 规范，启动执行
```

Suggested validation:

```bash
python <plugin-root>/scripts/validate_spec.py <specs_dir> --workflow bugfix
python <plugin-root>/scripts/spec_progress.py init <specs_dir>
python <plugin-root>/scripts/validate_spec.py <specs_dir> --resume
```

This is a structural integrity check only. It does not prove root-cause quality, minimal-fix scope, unchanged-behavior coverage, substitute reproduction strength, rollback safety, or monitoring sufficiency; review those semantics before implementation. Passing validation does not approve implementation; implementation still requires an accepted approval phrase. After the approval phrase is received, run:

```bash
python <plugin-root>/scripts/spec_progress.py approve <specs_dir> --evidence "批准规范，启动执行"
```

In a git repository, after `approve` freezes the baseline, commit the spec artifacts, push the `spec/<run-id>` branch, and open the draft PR as described in the router's `## Git Delivery Chain` in `../spec-workflow/SKILL.md`. Skip the PR steps when no remote or `gh` is available, and skip the whole git chain outside a git repository.

## State C: Controlled Implementation

Only enter this state after explicit approval.

When the approval phrase is received, freeze the baseline with `spec_approve` or `spec_progress.py approve` before implementation. Do not start a task until `spec.yml` shows `approval: approved`, `artifact_hashes`, and `task_plan_hash`.

Implementation rules:

- Read `<specs_dir>/bugfix.md`, `<specs_dir>/design.md`, and `<specs_dir>/tasks.md`.
- Run `spec_resume` or `spec_progress.py resume <specs_dir>` and stop if it reports frozen-baseline drift.
- Select only the first unchecked task in `tasks.md`.
- Before editing business code for that task, call `spec_start_task` through MCP or run `python <plugin-root>/scripts/spec_progress.py start <specs_dir> B-xxx`.
- Implement only that task and keep the change tied to the recorded root cause.
- Prefer proof order: reproduce the bug, prove the fix, prove unchanged behavior.
- If implementation reveals that the recorded root cause is wrong, the fix scope must change, or `bugfix.md`, `design.md`, or the task plan in `tasks.md` must change, stop code work. Run `sync-check --write` to mark `reapproval-required`, return to State B, update specs, and wait for an accepted approval phrase plus a fresh `approve` before continuing. Progress fields may still be updated through the tools.
- Run verification and perform at most three self-healing loops. Each loop is one hypothesis attempt under the Root-Cause Investigation Discipline: one written hypothesis, one minimal change, one variable at a time. After the third failed loop, do not try a fourth — block the task, question whether the recorded root cause or design is wrong, run `sync-check --write` if specs must change, and discuss with the human.
- After verification passes and before `complete`, run a lightweight two-phase self-check of the task diff: (1) 规格符合性 — exactly the selected task and its `覆盖` IDs; (2) 代码质量 — minimal change tied to the proven root cause, real assertions, no weakened or deleted failing tests. Do not spawn a separate reviewer by default; final acceptance supplies fresh independent review. Use a fresh task-level reviewer only when this check exposes material uncertainty or the task has a critical risk that cannot safely wait for the final unit review. Critical or Important findings block `complete`; record the verdict in completion evidence.
- After passing the selected task's verification criteria, call `spec_complete_task` through MCP or run `python <plugin-root>/scripts/spec_progress.py complete <specs_dir> B-xxx --evidence "<verification evidence>"`. For a reproduction task, passing means the failure proof behaves as expected on unfixed code or the substitute evidence is recorded and strong enough to constrain the fix.
- If blocked, call `spec_block_task` or `python <plugin-root>/scripts/spec_progress.py block <specs_dir> B-xxx --reason "<reason>"`.
- If skipped, call `spec_skip_task` or `python <plugin-root>/scripts/spec_progress.py skip <specs_dir> B-xxx --approval "<human approval evidence>"`.
- Provide a commit message suggestion in this form:

```text
fix(scope): short description

Implements task: [task description]
Spec: <specs_dir>/tasks.md
```

  In a git repository, after `complete`, commit the business code and the progress files together with this message (this satisfies the pre-commit progress guard), push the `spec/<run-id>` branch, and refresh the PR checklist from `tasks.md` per the router's `## Git Delivery Chain` in `../spec-workflow/SKILL.md`. When the git chain is not enabled, just surface the suggested message as before.

If unchecked tasks remain, continue to the next executable task under the existing workflow approval. Pause only for a real blocker, a required reapproval, or a decision that materially changes scope or outcome.

If no unchecked tasks remain in `<specs_dir>/tasks.md`, read and follow `../spec-acceptance/SKILL.md` before reporting the whole workflow complete.
