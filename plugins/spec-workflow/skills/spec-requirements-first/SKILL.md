---
name: spec-requirements-first
description: Explicit activation only. Internal branch of a user-invoked spec-workflow. Select only after the active router hands off, or when the user explicitly names spec-requirements-first and thereby opts into the plugin; otherwise do not select.
---

# Spec workflow Requirements-First

Use this branch for feature work driven by requirements rather than a fixed technical design.

## Activation Boundary

This branch may run only inside a user-initiated spec-workflow run. If named directly, enter through the `spec-workflow` router and intake gates first. Generic product or feature requests must not activate it.

If the entry router has not already printed the announcement, print:

```markdown
我读到了Spec workflow技能。
我会按照“Feature / Requirements-First”分支来完成。
```

## Hard Rules

- Do not write business source code until the human explicitly replies the preferred approval phrase `批准规范，启动执行`.
- Generate or update spec artifacts in the selected `<specs_dir>`; they are the source of truth.
- New specs must also generate `<specs_dir>/spec.yml` and `<specs_dir>/progress.md` from the templates in `../../assets/templates/`.
- Record Kiro-style Analyze Requirements conclusions inside `product.md` before finalizing `architecture.md` and `tasks.md`.
- Carry the `Intake Handoff / 澄清交接摘要` into `product.md`; do not leave intake conclusions only in chat.
- Keep this branch for new capabilities, user workflows, scaffolding, complex refactors, and product-driven work.
- If the user switches to architecture-first or ADR-first work, reroute to `../spec-design-first/SKILL.md`.
- If the work is actually restoring existing expected behavior, reroute to `../spec-bugfix/SKILL.md`.

## High-Risk Warning

If the task involves authentication, authorization, payments, billing, database schema changes, data repair, distributed consistency, cache consistency, secrets, encryption, sensitive data, incident mitigation, rollback, or hotfix work, include this warning even when the router was skipped:

```markdown
> [!WARNING]
> 高风险变更警告：当前任务涉及核心系统或高影响范围区域，必须进行人类深度审查，切勿草率合并。
```

## Intake Precondition

Before State A, if the current conversation does not already include an `Intake Handoff / 澄清交接摘要` with `Status: complete` or `Status: assumptions-accepted`, read and follow `../spec-intake/SKILL.md`. If intake asks questions or is blocked, stop and wait for the human answer before generating specs.

## State A: Requirements Clarification

Before writing specs, inspect available project context such as `constitution.md`, `CONVENTIONS.md`, the selected `<specs_dir>`, and stack manifests like `package.json`, `pyproject.toml`, `Cargo.toml`, or similar files.

Clarify all gaps that materially affect the spec:

- user goals, user stories, and success criteria
- EARS/GWT acceptance scenarios and verification evidence
- functional and non-functional requirements
- boundaries, non-goals, compatibility, migration, rollback, and public-contract constraints
- permissions, safety, security, and privacy expectations
- performance, concurrency, consistency, and operational constraints
- affected modules, APIs, schemas, or integrations
- decision boundaries: what the agent may decide and what needs human approval

If clarification is needed, continue the multi-round intake loop instead of asking one batch and generating specs. If the user says to proceed with assumptions, record unknowns as assumptions and risks in `product.md`.

## State B: Spec Artifact Generation

Use the plugin templates from `../../assets/templates/`:

- `product_template.md`
- `architecture_template.md`
- `tasks_template.md`
- `progress_template.md`
- `spec_index_template.yml`

Generate:

- `<specs_dir>/product.md`: intake handoff, Analyze Requirements, user stories, acceptance criteria using GIVEN / WHEN / THEN, constraints, assumptions, and non-goals
- `<specs_dir>/architecture.md`: implementation blueprint, component boundaries, data model, API/interface shape, dependencies, error handling, security, and performance boundaries
- `<specs_dir>/tasks.md`: ordered atomic tasks using `- [ ]`, with structured fields: status, files, verify, evidence, depends_on, risk, covers, and parallelizable
- `<specs_dir>/progress.md`: resume entrypoint with workflow status, current task, approval state, branch, commit, blockers, and recovery notes
- `<specs_dir>/spec.yml`: Kiro-compatible machine index with workflow, mode, approval, risk level, artifact paths, requirement IDs, task graph, current task, and checkpoint

Default mode is `strict`. Use `quick` only when the user explicitly authorizes Quick Plan and the risk level is low; record the Quick Plan reason in `product.md` and `spec.yml`.

Before review, replace all template placeholders with concrete content. If a template section does not apply, state that explicitly with the reason instead of leaving placeholder text.

Task plan quality bar:

- Every task must fill the `接口` field: `消费` lists the exact signatures or contracts taken from upstream tasks, `产出` lists the exact functions, interfaces, or data structures downstream tasks rely on; write `无` explicitly when empty. Assume the task executor sees only its own task, so each task must be self-contained.
- Placeholder text such as `TBD`, `待定`, `处理边界情况`, or `类似 T-xxx` inside a task is a plan failure, not an acceptable draft. Every task needs concrete file paths, real commands, and expected verification output.
- Before requesting approval, self-review the plan in place: every requirement ID is covered by at least one task's `覆盖`, no placeholders remain, and interface signatures and naming are consistent across tasks. Fix findings directly instead of reporting them.

After generation, ask the human to review the artifacts. The preferred approval phrase for implementation is:

```text
批准规范，启动执行
```

Suggested validation:

```bash
python <plugin-root>/scripts/validate_spec.py <specs_dir> --workflow requirements-first
python <plugin-root>/scripts/spec_progress.py init <specs_dir>
python <plugin-root>/scripts/validate_spec.py <specs_dir> --resume
```

This is a structural integrity check only. Passing validation does not approve implementation; implementation still requires the exact approval phrase. After the approval phrase is received, run:

```bash
python <plugin-root>/scripts/spec_progress.py approve <specs_dir> --evidence "批准规范，启动执行"
```

In a git repository, after `approve` freezes the baseline, commit the spec artifacts, push the `spec/<run-id>` branch, and open the draft PR as described in the router's `## Git Delivery Chain` in `../spec-workflow/SKILL.md`. Skip the PR steps when no remote or `gh` is available, and skip the whole git chain outside a git repository.

## State C: Controlled Implementation

Only enter this state after explicit approval.

When the approval phrase is received, freeze the baseline with `spec_approve` or `spec_progress.py approve` before implementation. Do not start a task until `spec.yml` shows `approval: approved`, `artifact_hashes`, and `task_plan_hash`.

Implementation rules:

- Read `<specs_dir>/product.md`, `<specs_dir>/architecture.md`, and `<specs_dir>/tasks.md`.
- Run `spec_resume` or `spec_progress.py resume <specs_dir>` and stop if it reports frozen-baseline drift.
- Select only the first unchecked task in `tasks.md`.
- Before editing business code for that task, call `spec_start_task` through MCP or run `python <plugin-root>/scripts/spec_progress.py start <specs_dir> T-xxx`.
- Implement only that task.
- Satisfy the selected task's verification criteria. Add or update acceptance tests when the task touches user-visible behavior; all approved acceptance criteria must be covered before the feature is complete.
- If implementation reveals that `product.md`, `architecture.md`, or the task plan in `tasks.md` must change, stop code work. Run `sync-check --write` to mark `reapproval-required`, return to State B, update specs, and wait for `批准规范，启动执行` plus a fresh `approve` before continuing. Progress fields may still be updated through the tools.
- Run verification and perform at most three self-healing loops.
- After verification passes and before `complete`, run a lightweight two-phase self-check of the task diff: (1) 规格符合性 — exactly the selected task and its `覆盖` IDs; (2) 代码质量 — minimal change, real assertions, no weakened tests, consistent with the approved spec. Do not spawn a separate reviewer by default; final acceptance supplies fresh independent review. Use a fresh task-level reviewer only when this check exposes material uncertainty or the task has a critical risk that cannot safely wait for the final unit review. Critical or Important findings block `complete`; record the verdict in completion evidence.
- After passing verification, call `spec_complete_task` through MCP or run `python <plugin-root>/scripts/spec_progress.py complete <specs_dir> T-xxx --evidence "<verification evidence>"`. Do not manually mark `- [x]` without recorded evidence.
- If blocked, call `spec_block_task` or `python <plugin-root>/scripts/spec_progress.py block <specs_dir> T-xxx --reason "<reason>"`.
- If skipped, call `spec_skip_task` or `python <plugin-root>/scripts/spec_progress.py skip <specs_dir> T-xxx --approval "<human approval evidence>"`.
- Provide a commit message suggestion in this form:

```text
feat(scope): short description

Implements task: [task description]
Spec: <specs_dir>/tasks.md
```

  In a git repository, after `complete`, commit the business code and the progress files together with this message (this satisfies the pre-commit progress guard), push the `spec/<run-id>` branch, and refresh the PR checklist from `tasks.md` per the router's `## Git Delivery Chain` in `../spec-workflow/SKILL.md`. When the git chain is not enabled, just surface the suggested message as before.

If unchecked tasks remain, continue to the next executable task under the existing workflow approval. Pause only for a real blocker, a required reapproval, or a decision that materially changes scope or outcome.

If no unchecked tasks remain in `<specs_dir>/tasks.md`, read and follow `../spec-acceptance/SKILL.md` before reporting the whole workflow complete.
