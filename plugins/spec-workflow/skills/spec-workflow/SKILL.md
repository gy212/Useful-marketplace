---
name: spec-workflow
description: Explicit activation only. 仅当用户明确点名 spec-workflow、Spec workflow，或明确要求使用、走或启动该插件时选择；不得根据请求内容或任务类型推断触发。作为用户显式启动后的插件入口，负责目录选择、intake、分支路由与 acceptance。
---

# Spec workflow Router

This is the lightweight entrypoint for the Spec workflow plugin. Keep this file small: select an isolated workflow directory first, run intake, classify the request, announce the route, hand off to the branch skill, then run pre-acceptance and final acceptance when branch tasks are complete.

## Explicit Activation Gate

- Activate only when the user explicitly names `spec-workflow` / `Spec workflow`, explicitly asks to use or start this plugin, or continues a workflow that they previously started explicitly.
- Do not infer activation from task shape or keywords. Requests such as "write a spec", "clarify requirements", "design first", "fix this bug", "review this change", or "handle a high-risk refactor" are not activation by themselves.
- Once the gate is satisfied, router-to-branch handoffs may use the internal skills without asking the user to repeat the plugin name.
- If the gate is not satisfied, do not print the workflow announcement, create workflow artifacts, call progress MCP tools, or ask workflow-specific intake questions.

## Required Announcement

Once this skill is read and selected, print:

```markdown
我读到了Spec workflow技能。
我会先按照“spec-intake”完成需求澄清。
```

After intake produces an `Intake Handoff / 澄清交接摘要` with `Status: complete` or `Status: assumptions-accepted`, print the route decision:

```markdown
## Spec 路由决定

- 路径：Feature / Requirements-First | Feature / Design-First | Bugfix | 待澄清
- Design-First 粒度：n/a | High Level Design | Low Level Design
- Intake 状态：complete | assumptions-accepted | blocked | 需要反问
- 原因：[一句话说明判断依据]
- 下一步：[Intake 反问 | 分流澄清 | 需求澄清 | Design-First 澄清 | Bug 分析澄清 | 规范生成]
```

Immediately after the route block, print one branch line:

```markdown
我会按照“Feature / Requirements-First”分支来完成。
```

or:

```markdown
我会按照“Feature / Design-First”分支来完成。
```

or:

```markdown
我会按照“Bugfix”分支来完成。
```

If the route is still unclear:

```markdown
我会先完成分流澄清，再进入对应分支。
```

## Hard Rules

- No business source code before a human explicitly approves the spec artifacts.
- Before intake, discover workflow directories under `docs/specs/` and select a single `<specs_dir>` for this run. Do not resume an old workflow automatically when the user is starting a new one.
- Clarify first, classify second, then generate spec artifacts.
- Do not invent missing boundaries, compatibility rules, security constraints, or failure modes. Ask or record them as assumptions.
- Planning artifacts must be written to the selected `<specs_dir>`; chat-only plans are not the source of truth.
- New specs must create an isolated directory under `docs/specs/<run-id>/` and include Markdown artifacts plus `<specs_dir>/spec.yml` and `<specs_dir>/progress.md`. `tasks.md` is the human-readable task source, `progress.md` is the resume entrypoint, and `spec.yml` is the Kiro-compatible machine index.
- Do not mix Feature and Bugfix. If a fix adds new user-visible capability or changes product scope, reroute to Feature.
- Do not mix Requirements-First and Design-First. Use Design-First only when fixed technical design, architecture, ADRs, or technical constraints are the primary starting point; ordinary stack, compatibility, or schema constraints can be recorded in Requirements-First specs.
- The preferred approval phrase for all branches is `批准规范，启动执行`; branch-specific legacy phrases remain valid where documented for compatibility.
- After a human approval phrase is received, freeze the approved baseline with MCP `spec_approve` or `python <plugin-root>/scripts/spec_progress.py approve <specs_dir> --evidence "<approval phrase/context>"` before implementation. `start` must fail if this step has not happened.
- After approval is frozen, primary spec artifacts and the task plan are immutable during implementation. Only progress fields may change through the Spec Progress MCP tools or `scripts/spec_progress.py`: checkbox/status/evidence/completed_at/notes/blocker, top-level progress summary, completion log, `progress.md`, and current-task/index state.
- If implementation reveals that approved requirements, design, root cause, or task plan must change, stop code work, run `sync-check --write` to mark `reapproval-required`, update specs, and wait for another accepted approval phrase plus `approve`. Do not silently rewrite approved spec content mid-task.
- During implementation, task state must be changed through the Spec Progress MCP tools or `scripts/spec_progress.py`. Do not hand-edit task completion in chat only.

## Workflow Directory Selection

Before intake, run or request the equivalent of:

```bash
python <plugin-root>/scripts/spec_progress.py discover docs/specs/
```

- If `open_workflows` is empty, create a new isolated directory with `python <plugin-root>/scripts/spec_progress.py new docs/specs/ --slug "<short-slug>"`, then use the returned `specs_dir` as `<specs_dir>` for the whole run.
- If `open_workflows` is not empty, list each candidate's `specs_dir`, workflow, status, approval, and current task. Ask whether to continue one of them or create a new isolated workflow. Do not run resume or read old task progress until the human chooses a candidate.
- If the human chooses a new workflow, create it with `new docs/specs/ --slug "<short-slug>"` and do not treat older `progress.md`/`tasks.md` files as the source of truth for this request.
- Legacy root-level `docs/specs/tasks.md` is a valid candidate for resuming old runs, but new runs must write to `docs/specs/<run-id>/`.
- When creating any new workflow inside a git repository, pick the `<run-id>` first, then create the isolated worktree and branch as described in `## Git Delivery Chain`, then run `new --run-id <run-id>` from inside that worktree so the spec directory matches the branch and artifacts land on `spec/<run-id>`. In a non-git repository, skip the worktree step and run `new` in place (no `--run-id`).

## Git Delivery Chain

When the run happens inside a git repository, wrap the whole workflow in a branch-and-PR delivery chain so the main working tree stays clean and each task lands as a reviewable commit. This chain is anchored at the workflow level, not per task: one branch, one draft PR, one final human merge. Check the degradation ladder before every git action and never block the workflow when a rung is unavailable.

Degradation ladder (evaluate before each step):

- Not a git repository → skip the entire git chain; behavior is identical to running without it.
- Git repository but `git worktree add` is unavailable or fails → degrade to `git switch -c spec/<run-id>` in the current working tree and continue with commits and PR steps.
- No remote, or `gh` missing / `gh auth status` fails → keep the local worktree and branch, skip every PR step, and note in the output that PR steps were skipped.

Chain steps:

1. **Create.** After the human chooses to start a new workflow, first pick the `<run-id>` yourself: `<timestamp>-<short-slug>`, e.g. `20260707-153000-add-auth`. This id names the worktree, the branch, and the spec directory, so decide it once up front. Then create the isolated worktree and branch from the current HEAD:

   ```bash
   git worktree add ../<repo-name>--spec-<run-id> -b spec/<run-id>
   ```

   Run every later command (`new`, intake, artifact generation, `approve`, implementation, acceptance) from inside that worktree directory. Then run `spec_progress.py new docs/specs/ --run-id <run-id>` inside the worktree so the spec directory matches the branch name exactly and artifacts are created on the branch. In a non-git repository you skip this step, so omit `--run-id` and let `new` generate the id itself.

2. **On approval.** After `approve` freezes the baseline (see `## Approval Freeze`):

   ```bash
   git add docs/specs/<run-id> && git commit -m "docs(spec): add <run-id> spec artifacts"
   git push -u origin spec/<run-id>
   gh pr create --draft
   ```

   Generate the draft PR body from `tasks.md`: a one-line goal, a `- [ ]` checklist mirroring the tasks, a link to the spec directory, the high-risk warning when applicable, and `Closes #N` only when a tracking issue already exists.

3. **Per task.** After `complete` records evidence, commit the business code and the progress files together using the branch skill's commit message suggestion (this satisfies the pre-commit progress guard), push, then regenerate the PR body checklist from `tasks.md` with `gh pr edit --body-file`. The PR body is a one-way projection of `tasks.md`; `tasks.md` stays the single source of truth.

4. **PR rediscovery.** Locate the PR at any time from the branch name with `gh pr list --head spec/<run-id>`. Do not persist the PR URL; progress files are re-rendered by the scripts and hand-added fields are overwritten.

5. **Issue (on demand).** Only when the human explicitly asks, create a tracking issue with `gh issue create` (body from the spec summary) and add `Closes #N` to the PR body. Do not create issues by default.

6. **Merge and cleanup.** After final acceptance passes and the PR is marked ready (see `../spec-acceptance/SKILL.md`), print suggested commands for the human to run — for example `gh pr merge --squash --delete-branch` and `git worktree remove <path>`. Never merge or remove the worktree autonomously.

## Resume Existing Workflow

After a human chooses an existing `<specs_dir>`:

1. Run or request the equivalent of `python <plugin-root>/scripts/spec_progress.py resume <specs_dir>`.
2. Read `<specs_dir>/progress.md`, `<specs_dir>/spec.yml`, `<specs_dir>/tasks.md`, and the branch spec artifacts.
3. If resume reports `interrupted`, inspect the diff and verification evidence before continuing. Do not mark any task complete until evidence is recorded.
4. If approval is `reapproval-required` or resume reports frozen-baseline drift, stop implementation and ask for human reapproval after syncing specs.
5. If resume is clean, continue from the current task instead of restarting intake.

When the run is inside a git repository and `progress.md` records a `spec/<run-id>` branch, continue inside that branch's worktree: if it still appears in `git worktree list`, switch into that directory before resuming; if the worktree is gone but the branch exists, recreate it with `git worktree add ../<repo-name>--spec-<run-id> spec/<run-id>`. Follow the `## Git Delivery Chain` degradation ladder when git or the worktree is unavailable.

Print a short resume summary before continuing.

## Intake First

Before routing, read and follow `../spec-intake/SKILL.md` unless the current conversation already includes an `Intake Handoff / 澄清交接摘要` with `Status: complete` or `Status: assumptions-accepted`.

If intake asks questions, stop after the questions and wait for the human answer. Do not generate spec artifacts or enter a branch workflow yet.

When intake produces a `complete` or `assumptions-accepted` handoff, use those conclusions as routing input and carry them into the selected branch's primary spec artifact. If intake is `blocked`, ask for the missing decision and do not route.

## Analyze Requirements

For Requirements-First and Design-First, the selected branch must perform Kiro-style Analyze Requirements before finalizing specs. Record the result inside `product.md` or `requirements.md`; do not create a standalone analysis file unless the user explicitly asks.

The analysis must check ambiguity, undefined concepts, conflicting constraints, missing boundary cases, failure paths, permissions, concurrency, data consistency, and safety/security risks. Quick Plan may skip the full analysis only for low-risk work with explicit human authorization, and the skip reason must be recorded.

## Approval Freeze

After artifacts are generated and structurally validated, the human may approve with `批准规范，启动执行`. On receiving approval:

1. Run `python <plugin-root>/scripts/spec_progress.py approve <specs_dir> --evidence "<approval phrase/context>"` or MCP `spec_approve`.
2. Confirm `spec.yml` shows `approval: approved`, `artifact_hashes`, and `task_plan_hash`.
3. In a git repository, commit the frozen spec artifacts, push the `spec/<run-id>` branch, and open the draft PR as described in `## Git Delivery Chain`. Follow the degradation ladder when the worktree, remote, or `gh` is unavailable.
4. Start implementation only through `spec_start_task` or `python <plugin-root>/scripts/spec_progress.py start <specs_dir> <task-id>`.

`artifact_hashes` freeze primary branch artifacts. `task_plan_hash` freezes task IDs, task titles, files, verify criteria, dependencies, risk, coverage, and parallelization. Progress updates are allowed; spec/task-plan edits require `sync-check --write` and reapproval.

## Routing Rules

After intake is complete, check in this order and stop at the first match:

1. `Bugfix`: the user wants to fix, investigate, reproduce, roll back, or recover existing expected behavior.
2. `Feature / Design-First`: the user asks for design-first, tech design first, high-level design, low-level design, architecture, ADR, or gives a technical plan or primary technical constraints that must drive requirements.
3. `Feature / Requirements-First`: the user asks for a new feature, capability, workflow, scaffold, complex refactor, or product outcome without a fixed technical design starting point.
4. `待澄清`: ask one question only: `这次主要是 A. 恢复既有预期行为 / 修复缺陷，B. 从业务需求新增或调整能力，还是 C. 从技术设计 / 架构约束推进？`

## Branch Handoff

Only hand off after `spec-intake` is `complete` or `assumptions-accepted`.

- Requirements-First: read and follow `../spec-requirements-first/SKILL.md`.
- Design-First: read and follow `../spec-design-first/SKILL.md`.
- Bugfix: read and follow `../spec-bugfix/SKILL.md`.

After handoff, keep only the selected branch in scope until the user changes direction.

## Final Acceptance

After the selected branch has no unchecked tasks in `<specs_dir>/tasks.md`, first run local pre-acceptance with `python <plugin-root>/scripts/validate_spec.py <specs_dir> --pre-acceptance`, then read and follow `../spec-acceptance/SKILL.md`. The current Spec workflow opt-in covers the review agents required by that skill; do not ask for a second orchestration authorization.

Do not report the whole Spec workflow complete until final acceptance passes. New runs use adaptive acceptance by default: independent unit review, risk- or issue-triggered adversarial review, targeted delta re-review, and one final global integration review. Record acceptance repairs in `acceptance-fixes.md`; do not append them to the original task plan or restart the entire review when only affected units changed.

## High-Risk Warning

If the task involves authentication, authorization, payments, billing, database schema changes, data repair, distributed consistency, cache consistency, secrets, encryption, sensitive data, incident mitigation, rollback, or hotfix work, include:

```markdown
> [!WARNING]
> 高风险变更警告：当前任务涉及核心系统或高影响范围区域，必须进行人类深度审查，切勿草率合并。
```
