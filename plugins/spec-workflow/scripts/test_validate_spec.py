#!/usr/bin/env python3
"""Regression tests for the Spec workflow validator."""

from __future__ import annotations

import os
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPT_DIR.parent
TEMPLATES = PLUGIN_ROOT / "assets" / "templates"
VALIDATOR = SCRIPT_DIR / "validate_spec.py"
PROGRESS = SCRIPT_DIR / "spec_progress.py"
MCP_SERVER = PLUGIN_ROOT / "mcp" / "spec_progress_server.py"


def run_validator(specs_dir: Path, workflow: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(VALIDATOR), str(specs_dir), "--workflow", workflow],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=merged_env,
    )


def run_progress(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROGRESS), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def write(path: Path, content: str) -> None:
    path.write_text(content.strip() + "\n", encoding="utf-8")


def valid_product() -> str:
    return """
    # 产品规范

    ## 用户故事与验收标准

    ## Analyze Requirements / 需求分析结论

    - 歧义检查：术语和验收对象已定义。
    - 冲突检查：未发现互斥约束。
    - 失败路径：Security and validation failures are covered.
    - 并发 / 权限 / 数据风险：Authorization is required and no data migration is needed.

    ### US-001: Publish comments

    #### 验收标准

    - **GIVEN** a signed-in reader is viewing a post
    - **WHEN** the reader submits a non-empty comment
    - **THEN** the comment is saved and shown in the comment list

    ## 非功能性需求

    | ID | 类别 | 描述 | 标准 |
    |:---|:---|:---|:---|
    | NFR-001 | 安全 | Comments require authentication | Auth is enforced |
    """


def valid_architecture() -> str:
    return """
    # 技术架构

    ## 数据模型

    The Comment entity stores post id, author id, body, and timestamps.

    ## API / 接口

    POST /comments creates a comment for an authenticated user.

    ## Dependencies

    No new dependency is required.

    ## Error Handling

    Invalid input returns a validation error.

    ## Security

    Authorization requires a signed-in user.

    ```mermaid
    flowchart TD
        Request --> Service
        Service --> Store
    ```
    """


def valid_requirements() -> str:
    return """
    # 需求规范

    > **来源设计：** docs/specs/design.md

    ## 功能需求与验收标准

    ## Analyze Requirements / 需求分析结论

    - 歧义检查：设计来源和行为边界已定义。
    - 冲突检查：requirements.md 与 design.md 未冲突。
    - 失败路径：Retry failures and idempotency risks are covered.
    - 并发 / 权限 / 数据风险：No new authorization surface is introduced.

    ### REQ-001: Publish outbox event

    #### 验收标准

    - **GIVEN** an order has been created
    - **WHEN** the outbox worker processes pending records
    - **THEN** an order-created event is published

    ## 非功能性需求

    | ID | 类别 | 描述 | 来源设计约束 |
    |:---|:---|:---|:---|
    | NFR-001 | 可靠性 | Events are retried | Derived from design.md |

    ## 设计映射

    REQ-001 is derived from design.md section 4.
    """


def valid_design(level: str = "High Level Design", include_lld: bool = True) -> str:
    lld = ""
    if include_lld:
        lld = """
        ## Low Level Design 细节

        - **模块 / 类职责：** Worker coordinates event publishing.
        - **函数签名与契约：** publish_event(order_id) returns a publish result.
        - **算法流程：** Load pending event, publish it, then mark it complete.
        - **状态转换：** Pending moves to Published after successful delivery.
        - **详细数据结构：** Outbox record contains id, payload, status, and retry count.
        """

    return f"""
    # 技术设计规范

    > **设计粒度：** {level}

    ## 设计起点与约束

    Existing order creation must keep its interface stable.

    ## 目标系统边界

    The changed 组件 are the order service and outbox worker.

    ## 方案设计

    The API writes an outbox row and the worker publishes it through the message bus.

    ```mermaid
    flowchart TD
        API --> Store
        Store --> Worker
        Worker --> Broker
    ```

    ## 备选方案与取舍

    Alternative direct publish was rejected because transaction consistency is weaker.

    ## 风险与验证策略

    Risk is duplicate delivery; Validation covers retry and idempotency behavior.

    {lld}
    """


def valid_design_tasks() -> str:
    return """
    # Design-First Tasks

    ## 执行规则

    1. Inline examples like `- [ ]`, `- [x]`, and `- [~]` are not tasks.

    ## 阶段 1

    - [ ] **T-001:** Implement the design.md persistence boundary
      - 状态: pending
      - 涉及文件: `src/outbox.py`
      - 验证命令: pytest tests/test_outbox.py
      - 验证证据: pending
      - 依赖: 无
      - 风险: low
      - 覆盖: REQ-001
      - 可并行: 否
      - 验证标准: Design constraints are covered by tests
    """


def valid_feature_tasks() -> str:
    return """
    # Tasks

    > **状态：** Draft
    > **当前任务：** T-001
    > **进度：** 2 / 3 已完成
    > **最后更新：** 2026-01-01

    ## 执行规则

    1. Inline examples like `- [ ]`, `- [x]`, and `- [~]` are not tasks.

    ## 阶段 1

    - [ ] **T-001:** Implement comment storage
      - 状态: pending
      - 涉及文件: `src/comments.py`
      - 验证命令: pytest tests/test_comments.py
      - 验证证据: pending
      - 依赖: 无
      - 风险: low
      - 覆盖: US-001, AC-001.1
      - 可并行: 否
      - 验证标准: Unit tests pass

    - [x] **T-002:** Record completed setup
      - 状态: done
      - 涉及文件: `docs/specs/tasks.md`
      - 验证命令: python scripts/check.py
      - 验证证据: pytest passed
      - 依赖: T-001
      - 风险: low
      - 覆盖: NFR-001
      - 可并行: 是
      - 验证标准: Completion evidence is recorded

    - [~] **T-003:** Human-approved skip for optional export
      - 状态: skipped
      - 涉及文件: `src/export.py`
      - 验证命令: n/a
      - 验证证据: user approved skip
      - 依赖: T-002
      - 风险: low
      - 覆盖: n/a
      - 可并行: 否
      - 验证标准: Skip approval is recorded
    """


def valid_bugfix() -> str:
    return """
    # Bugfix Spec

    ## 证据与复现

    Evidence shows duplicate processing.

    ## 当前错误行为

    ### BUG-001: Duplicate deduction

    - **WHEN** the same order message is processed twice
    - **THEN** current behavior deducts inventory twice

    ## 修复后的期望行为

    ### FIX-001: Idempotent deduction

    - **WHEN** the same order message is processed twice
    - **THEN** inventory is deducted once

    ## 必须保持不变的行为

    ### SAFE-001: Normal order behavior

    - **WHEN** one valid order is submitted
    - **THEN** the original response contract remains unchanged

    ## 范围与约束

    The fix is limited to the inventory deduction path.
    """


def valid_bugfix_design() -> str:
    return """
    # Bugfix Design

    ## 根因分析 / Root Cause

    The retry consumer lacks an idempotency check.

    ## 代码路径与影响面

    The affected Surface is the retry consumer and inventory repository.

    ## 修复策略

    The Fix Strategy is to guard deduction by order id.

    ## 测试与验证策略

    Regression tests cover duplicate and normal single-order behavior.

    ## 风险与发布计划

    Rollout risk is low and rollback removes the guard.

    ```mermaid
    flowchart TD
        Retry --> Guard
        Guard --> Inventory
    ```
    """


def valid_bugfix_tasks(prefix: str = "B") -> str:
    return f"""
    # Bugfix Tasks

    ## 阶段 1

    - [ ] **{prefix}-001:** 建立复现失败证明
      - 状态: pending
      - 涉及文件: `tests/test_inventory.py`
      - 验证命令: pytest tests/test_inventory.py
      - 验证证据: pending
      - 依赖: 无
      - 风险: high
      - 覆盖: BUG-001
      - 可并行: 否
      - 验证标准: 复现测试稳定失败

    - [ ] **{prefix}-002:** 实现最小修复
      - 状态: pending
      - 涉及文件: `src/inventory.py`
      - 验证命令: pytest tests/test_inventory.py
      - 验证证据: pending
      - 依赖: {prefix}-001
      - 风险: high
      - 覆盖: FIX-001
      - 可并行: 否
      - 验证标准: 复现测试转为通过

    - [ ] **{prefix}-003:** 补充回归防护
      - 状态: pending
      - 涉及文件: `tests/test_inventory.py`
      - 验证命令: pytest tests/test_inventory.py
      - 验证证据: pending
      - 依赖: {prefix}-002
      - 风险: medium
      - 覆盖: SAFE-001
      - 可并行: 否
      - 验证标准: 回归测试证明不变行为未破坏
    """


def make_requirements_first(specs_dir: Path, product: str | None = None, tasks: str | None = None) -> None:
    write(specs_dir / "product.md", product or valid_product())
    write(specs_dir / "architecture.md", valid_architecture())
    write(specs_dir / "tasks.md", tasks or valid_feature_tasks())


def init_progress(specs_dir: Path) -> None:
    result = run_progress("init", str(specs_dir))
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)


def approve_progress(specs_dir: Path, evidence: str = "批准规范，启动执行") -> None:
    result = run_progress("approve", str(specs_dir), "--evidence", evidence)
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)


def completed_feature_tasks(first_risk: str = "low") -> str:
    tasks = valid_feature_tasks().replace("- [ ] **T-001:**", "- [x] **T-001:**")
    tasks = tasks.replace("- 状态: pending", "- 状态: done", 1)
    tasks = tasks.replace("- 验证证据: pending", "- 验证证据: pytest passed", 1)
    return tasks.replace("- 风险: low", f"- 风险: {first_risk}", 1)


def prepare_acceptance(specs_dir: Path, first_risk: str = "low") -> None:
    make_requirements_first(specs_dir, tasks=completed_feature_tasks(first_risk))
    init_progress(specs_dir)
    approve_progress(specs_dir)


def load_acceptance_state(specs_dir: Path) -> dict[str, object]:
    return json.loads((specs_dir / "acceptance_state.json").read_text(encoding="utf-8"))


def complete_acceptance_agent(
    specs_dir: Path,
    agent_id: str,
    result: str = "PASS",
) -> None:
    start_acceptance_agent(specs_dir, agent_id)
    finish_acceptance_agent(specs_dir, agent_id, result)


def start_acceptance_agent(specs_dir: Path, agent_id: str) -> None:
    started = run_progress("acceptance-start-agent", str(specs_dir), agent_id)
    if started.returncode != 0:
        raise AssertionError(started.stdout + started.stderr)


def finish_acceptance_agent(
    specs_dir: Path,
    agent_id: str,
    result: str = "PASS",
) -> None:
    completed = run_progress(
        "acceptance-complete-agent",
        str(specs_dir),
        agent_id,
        "--result",
        result,
        "--report",
        f"{result} report",
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)


def complete_actionable_agent(
    specs_dir: Path,
    agent_id: str,
    unit_id: str,
    *,
    title: str = "Blocking review issue",
    evidence: str = "evidence-backed P2 finding",
) -> None:
    start_acceptance_agent(specs_dir, agent_id)
    recorded = run_progress(
        "acceptance-record-issue",
        str(specs_dir),
        "--unit",
        unit_id,
        "--severity",
        "P2",
        "--title",
        title,
        "--evidence",
        evidence,
        "--agent",
        agent_id,
    )
    if recorded.returncode != 0:
        raise AssertionError(recorded.stdout + recorded.stderr)
    finish_acceptance_agent(specs_dir, agent_id, "ACTIONABLE_ISSUES")


def make_design_first(specs_dir: Path, design: str | None = None) -> None:
    write(specs_dir / "design.md", design or valid_design())
    write(specs_dir / "requirements.md", valid_requirements())
    write(specs_dir / "tasks.md", valid_design_tasks())


def make_bugfix(specs_dir: Path, tasks: str | None = None) -> None:
    write(specs_dir / "bugfix.md", valid_bugfix())
    write(specs_dir / "design.md", valid_bugfix_design())
    write(specs_dir / "tasks.md", tasks or valid_bugfix_tasks())


class ValidatorRegressionTests(unittest.TestCase):
    def test_raw_templates_fail_for_all_workflows(self) -> None:
        cases = [
            (
                "requirements-first",
                {
                    "product_template.md": "product.md",
                    "architecture_template.md": "architecture.md",
                    "tasks_template.md": "tasks.md",
                },
            ),
            (
                "design-first",
                {
                    "design_first_design_template.md": "design.md",
                    "requirements_template.md": "requirements.md",
                    "design_first_tasks_template.md": "tasks.md",
                },
            ),
            (
                "bugfix",
                {
                    "bugfix_template.md": "bugfix.md",
                    "bugfix_design_template.md": "design.md",
                    "bugfix_tasks_template.md": "tasks.md",
                },
            ),
        ]

        for workflow, mapping in cases:
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as tmp:
                specs_dir = Path(tmp)
                for source, target in mapping.items():
                    shutil.copyfile(TEMPLATES / source, specs_dir / target)

                result = run_validator(specs_dir, workflow)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("占位符", result.stdout)
                self.assertRegex(result.stdout, r"\.md:\d+")

    def test_prose_gwt_words_do_not_count_as_acceptance_criteria(self) -> None:
        prose_product = """
        # Product

        ## 用户故事

        ### US-001: Prose only

        This prose says given enough time users decide when to act and then expect output.

        ## 非功能性需求

        NFR-001: Fast enough.
        """
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir, product=prose_product)

            result = run_validator(specs_dir, "requirements-first")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("缺少正式 GWT 行", result.stdout)

    def test_task_count_ignores_inline_checkbox_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)

            result = run_validator(specs_dir, "requirements-first")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("tasks.md 包含 3 个任务 (待完成: 1, 已完成: 1, 已跳过: 1)", result.stdout)

    def test_bugfix_rejects_t_prefixed_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_bugfix(specs_dir, tasks=valid_bugfix_tasks(prefix="T"))

            result = run_validator(specs_dir, "bugfix")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("B-xxx", result.stdout)

    def test_lld_markers_trigger_lld_depth_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_design_first(specs_dir, design=valid_design(level="详细设计", include_lld=False))

            result = run_validator(specs_dir, "design-first")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Low Level Design 模块", result.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_design_first(specs_dir, design=valid_design(level="High Level Design", include_lld=False))

            result = run_validator(specs_dir, "design-first")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_valid_minimal_workflows_pass(self) -> None:
        makers = [
            ("requirements-first", make_requirements_first),
            ("design-first", make_design_first),
            ("bugfix", make_bugfix),
        ]

        for workflow, maker in makers:
            with self.subTest(workflow=workflow), tempfile.TemporaryDirectory() as tmp:
                specs_dir = Path(tmp)
                maker(specs_dir)

                result = run_validator(specs_dir, workflow)

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_invalid_utf8_fails_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            (specs_dir / "product.md").write_bytes(b"\xff\xfe\x00\x00")

            result = run_validator(specs_dir, "requirements-first")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("UTF-8", result.stdout)
            self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_no_color_environment_suppresses_ansi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)

            result = run_validator(specs_dir, "requirements-first", env={"NO_COLOR": "1"})

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("\x1b[", result.stdout)

    def test_new_progress_files_enable_resume_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)

            result = run_validator(specs_dir, "requirements-first")
            resume = subprocess.run(
                [sys.executable, str(VALIDATOR), str(specs_dir), "--resume"],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(resume.returncode, 0, resume.stdout + resume.stderr)
            self.assertIn("spec.yml", (specs_dir / "spec.yml").read_text(encoding="utf-8"))

    def test_start_requires_explicit_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)

            result = run_progress("start", str(specs_dir), "T-001")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not approved", result.stderr)

    def test_approve_records_frozen_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approved = run_progress("approve", str(specs_dir), "--evidence", "批准规范，启动执行")

            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            progress = (specs_dir / "progress.md").read_text(encoding="utf-8")
            index = (specs_dir / "spec.yml").read_text(encoding="utf-8")
            self.assertIn("> **Approval:** approved", progress)
            self.assertIn("approval: approved", index)
            self.assertIn("artifact_hashes:", index)
            self.assertIn("task_plan_hash:", index)

    def test_approved_primary_artifact_drift_blocks_sync_and_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approve_progress(specs_dir)
            (specs_dir / "product.md").write_text(
                valid_product() + "\n## Extra section\nNew content.\n",
                encoding="utf-8",
            )

            sync = run_progress("sync-check", str(specs_dir))
            start = run_progress("start", str(specs_dir), "T-001")

            self.assertNotEqual(sync.returncode, 0)
            self.assertIn("product.md changed", sync.stdout)
            self.assertNotEqual(start.returncode, 0)
            self.assertIn("baseline drift", start.stderr)

    def test_approved_hashes_ignore_checkout_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approve_progress(specs_dir)
            for name in ("product.md", "architecture.md"):
                path = specs_dir / name
                normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                path.write_bytes(normalized.replace(b"\n", b"\r\n"))

            sync = run_progress("sync-check", str(specs_dir))

            self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)

    def test_sync_check_accepts_legacy_raw_crlf_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            for name in ("product.md", "architecture.md"):
                path = specs_dir / name
                normalized = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
                path.write_bytes(normalized.replace(b"\n", b"\r\n"))
            approve_progress(specs_dir)
            index = (specs_dir / "spec.yml").read_text(encoding="utf-8")
            raw_hashes = ", ".join(
                f"{name}={hashlib.sha256((specs_dir / name).read_bytes()).hexdigest()[:12]}"
                for name in ("product.md", "architecture.md")
            )
            index = re.sub(
                r"^artifact_hashes:.*$",
                f"artifact_hashes: {raw_hashes}",
                index,
                flags=re.MULTILINE,
            )
            (specs_dir / "spec.yml").write_text(index, encoding="utf-8")

            sync = run_progress("sync-check", str(specs_dir))

            self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)

    def test_approved_task_plan_drift_blocks_sync_and_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approve_progress(specs_dir)
            tasks = (specs_dir / "tasks.md").read_text(encoding="utf-8")
            tasks = tasks.replace("Implement comment storage", "Implement contract-backed comment storage")
            (specs_dir / "tasks.md").write_text(tasks, encoding="utf-8")

            sync = run_progress("sync-check", str(specs_dir))
            start = run_progress("start", str(specs_dir), "T-001")

            self.assertNotEqual(sync.returncode, 0)
            self.assertIn("tasks.md plan changed", sync.stdout)
            self.assertNotEqual(start.returncode, 0)
            self.assertIn("tasks.md plan changed", start.stderr)

    def test_sync_check_write_marks_reapproval_without_refreshing_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approve_progress(specs_dir)
            before = (specs_dir / "spec.yml").read_text(encoding="utf-8")
            tasks = (specs_dir / "tasks.md").read_text(encoding="utf-8")
            tasks = tasks.replace("Implement comment storage", "Implement contract-backed comment storage")
            (specs_dir / "tasks.md").write_text(tasks, encoding="utf-8")

            written = run_progress("sync-check", str(specs_dir), "--write")
            after = (specs_dir / "spec.yml").read_text(encoding="utf-8")

            self.assertNotEqual(written.returncode, 0)
            self.assertIn("approval: reapproval-required", after)
            self.assertIn("> **Approval:** reapproval-required", (specs_dir / "progress.md").read_text(encoding="utf-8"))
            before_hash = next(line for line in before.splitlines() if line.startswith("task_plan_hash:"))
            after_hash = next(line for line in after.splitlines() if line.startswith("task_plan_hash:"))
            self.assertEqual(before_hash, after_hash)

    def test_complete_requires_evidence_and_updates_all_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)

            no_evidence = run_progress("complete", str(specs_dir), "T-001", "--evidence", "")
            self.assertNotEqual(no_evidence.returncode, 0)
            self.assertIn("evidence", no_evidence.stderr)

            approve_progress(specs_dir)
            started = run_progress("start", str(specs_dir), "T-001")
            completed = run_progress(
                "complete",
                str(specs_dir),
                "T-001",
                "--evidence",
                "pytest tests/test_comments.py passed",
            )

            self.assertEqual(started.returncode, 0, started.stdout + started.stderr)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("- [x] **T-001:**", (specs_dir / "tasks.md").read_text(encoding="utf-8"))
            self.assertIn("pytest tests/test_comments.py passed", (specs_dir / "progress.md").read_text(encoding="utf-8"))
            self.assertIn("current_task:", (specs_dir / "spec.yml").read_text(encoding="utf-8"))
            self.assertIn("> **进度：** 3 / 3 已完成", (specs_dir / "tasks.md").read_text(encoding="utf-8"))
            self.assertIn("| T-001 |", (specs_dir / "tasks.md").read_text(encoding="utf-8"))

            sync = run_progress("sync-check", str(specs_dir))
            self.assertEqual(sync.returncode, 0, sync.stdout + sync.stderr)

    def test_validator_flags_stale_tasks_progress_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            tasks = valid_feature_tasks().replace("> **进度：** 2 / 3 已完成", "> **进度：** 0 / 3 已完成")
            make_requirements_first(specs_dir, tasks=tasks)

            result = run_validator(specs_dir, "requirements-first")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("顶部进度与复选框状态不一致", result.stdout)

    def test_acceptance_state_recovers_pending_agents_after_partial_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)

            init = run_progress("acceptance-init", str(specs_dir))
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            state = load_acceptance_state(specs_dir)
            first_agent = state["agents"][0]["agent_id"]
            complete_acceptance_agent(specs_dir, first_agent)
            status = run_progress("acceptance-status", str(specs_dir))

            self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
            summary = json.loads(status.stdout)
            self.assertNotIn(first_agent, summary["agents"]["pending_or_running"])
            self.assertGreater(len(summary["agents"]["pending_or_running"]), 0)
            self.assertEqual(summary["integration_review"]["status"], "planned")

    def test_acceptance_modes_plan_only_required_agents(self) -> None:
        cases = [
            (None, "adaptive", {"first_wave"}),
            ("quick", "quick", {"integration"}),
            ("adaptive", "adaptive", {"first_wave"}),
            ("full", "full", {"first_wave", "adversarial"}),
        ]
        for requested, expected, roles in cases:
            with self.subTest(mode=requested or "default"), tempfile.TemporaryDirectory() as tmp:
                specs_dir = Path(tmp)
                prepare_acceptance(specs_dir)
                args = ["acceptance-init", str(specs_dir)]
                if requested:
                    args.extend(["--mode", requested])

                initialized = run_progress(*args)
                state = load_acceptance_state(specs_dir)

                self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
                self.assertEqual(state["schema_version"], 2)
                self.assertEqual(state["acceptance_mode"], expected)
                self.assertEqual({agent["role"] for agent in state["agents"]}, roles)
                self.assertEqual(state["max_auto_fix_rounds"], 2)

    def test_legacy_v1_acceptance_resumes_as_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(
                run_progress("acceptance-init", str(specs_dir), "--mode", "full").returncode,
                0,
            )
            state = load_acceptance_state(specs_dir)
            state["schema_version"] = 1
            state.pop("acceptance_mode")
            state.pop("max_auto_fix_rounds")
            state.pop("auto_fix_rounds")
            for unit in state["review_units"]:
                unit.pop("requires_adversarial")
            (specs_dir / "acceptance_state.json").write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )

            status = run_progress("acceptance-status", str(specs_dir))

            self.assertEqual(status.returncode, 0, status.stdout + status.stderr)
            self.assertEqual(json.loads(status.stdout)["acceptance_mode"], "full")

    def test_adaptive_uses_adversarial_review_for_risk_or_primary_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir, first_risk="high")
            initialized = run_progress("acceptance-init", str(specs_dir), "--mode", "adaptive")
            state = load_acceptance_state(specs_dir)

            self.assertEqual(initialized.returncode, 0, initialized.stdout + initialized.stderr)
            high_unit = next(unit for unit in state["review_units"] if unit["high_risk"])
            low_unit = next(unit for unit in state["review_units"] if not unit["high_risk"])
            high_roles = {
                agent["role"] for agent in state["agents"] if agent["unit_id"] == high_unit["unit_id"]
            }
            low_roles = {
                agent["role"] for agent in state["agents"] if agent["unit_id"] == low_unit["unit_id"]
            }
            self.assertEqual(high_roles, {"first_wave", "adversarial"})
            self.assertEqual(low_roles, {"first_wave"})

        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            state = load_acceptance_state(specs_dir)
            primary = state["agents"][0]["agent_id"]

            complete_actionable_agent(specs_dir, primary, "U-001")
            state = load_acceptance_state(specs_dir)

            self.assertEqual(
                [agent["role"] for agent in state["agents"]],
                ["first_wave", "adversarial"],
            )

    def test_quick_acceptance_rejects_high_risk_workflows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir, first_risk="high")

            result = run_progress("acceptance-init", str(specs_dir), "--mode", "quick")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("limited to low-risk", result.stderr)

    def test_acceptance_fixes_do_not_append_original_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            state = load_acceptance_state(specs_dir)
            primary = state["agents"][0]["agent_id"]
            before = (specs_dir / "tasks.md").read_text(encoding="utf-8")
            complete_actionable_agent(
                specs_dir,
                primary,
                "U-001",
                title="Missing regression proof",
                evidence="review report showed missing regression proof",
            )
            state = load_acceptance_state(specs_dir)
            adversarial = next(agent["agent_id"] for agent in state["agents"] if agent["role"] == "adversarial")
            complete_acceptance_agent(specs_dir, adversarial)
            planned = run_progress("acceptance-plan-fixes", str(specs_dir))
            after = (specs_dir / "tasks.md").read_text(encoding="utf-8")

            self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
            self.assertEqual(before, after)
            self.assertTrue((specs_dir / "acceptance-fixes.md").is_file())
            self.assertIn("F-001", (specs_dir / "acceptance-fixes.md").read_text(encoding="utf-8"))

    def test_acceptance_rechecks_only_affected_units_after_fix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir, first_risk="high")
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            state = load_acceptance_state(specs_dir)
            high_unit = next(unit for unit in state["review_units"] if unit["high_risk"])
            low_unit = next(unit for unit in state["review_units"] if not unit["high_risk"])
            high_primary = next(
                agent["agent_id"] for agent in state["agents"]
                if agent["unit_id"] == high_unit["unit_id"] and agent["role"] == "first_wave"
            )
            high_adversarial = next(
                agent["agent_id"] for agent in state["agents"]
                if agent["unit_id"] == high_unit["unit_id"] and agent["role"] == "adversarial"
            )
            low_primary = next(
                agent["agent_id"] for agent in state["agents"]
                if agent["unit_id"] == low_unit["unit_id"] and agent["role"] == "first_wave"
            )
            complete_acceptance_agent(specs_dir, high_primary)
            complete_acceptance_agent(specs_dir, high_adversarial)
            complete_actionable_agent(
                specs_dir,
                low_primary,
                low_unit["unit_id"],
                title="Low unit regression",
                evidence="focused test fails",
            )
            state = load_acceptance_state(specs_dir)
            low_adversarial = next(
                agent["agent_id"] for agent in state["agents"]
                if agent["unit_id"] == low_unit["unit_id"] and agent["role"] == "adversarial"
            )
            complete_acceptance_agent(specs_dir, low_adversarial)
            self.assertEqual(run_progress("acceptance-plan-fixes", str(specs_dir)).returncode, 0)
            fix_id = load_acceptance_state(specs_dir)["fixes"][0]["fix_id"]
            self.assertEqual(run_progress("acceptance-fix-start", str(specs_dir), fix_id).returncode, 0)
            self.assertEqual(run_progress(
                "acceptance-fix-complete",
                str(specs_dir),
                fix_id,
                "--evidence",
                "focused test passed",
            ).returncode, 0)

            next_round = run_progress("acceptance-next-round", str(specs_dir))
            state = load_acceptance_state(specs_dir)
            round_two = [agent for agent in state["agents"] if agent["round"] == 2]

            self.assertEqual(next_round.returncode, 0, next_round.stdout + next_round.stderr)
            self.assertEqual({agent["unit_id"] for agent in round_two}, {low_unit["unit_id"]})
            self.assertEqual({agent["role"] for agent in round_two}, {"first_wave", "adversarial"})
            updated_high = next(unit for unit in state["review_units"] if unit["unit_id"] == high_unit["unit_id"])
            self.assertEqual(updated_high["round_started"], 1)

    def test_acceptance_defers_p3_and_stops_after_two_automatic_fix_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            state = load_acceptance_state(specs_dir)
            primary = state["agents"][0]["agent_id"]
            complete_acceptance_agent(specs_dir, primary)
            self.assertEqual(run_progress(
                "acceptance-record-issue",
                str(specs_dir),
                "--unit",
                "U-001",
                "--severity",
                "P3",
                "--title",
                "Non-blocking polish",
                "--evidence",
                "evidence for p3",
                "--agent",
                primary,
            ).returncode, 0)
            planned = run_progress("acceptance-plan-fixes", str(specs_dir))

            self.assertEqual(planned.returncode, 0, planned.stdout + planned.stderr)
            state = load_acceptance_state(specs_dir)
            self.assertEqual(state["fixes"], [])
            self.assertEqual([issue["severity"] for issue in state["deferred_issues"]], ["P3"])

        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            state = load_acceptance_state(specs_dir)
            primary = state["agents"][0]["agent_id"]
            complete_actionable_agent(
                specs_dir,
                primary,
                "U-001",
                title="Blocking issue",
                evidence="evidence for p2",
            )
            state = load_acceptance_state(specs_dir)
            adversarial = next(agent["agent_id"] for agent in state["agents"] if agent["role"] == "adversarial")
            complete_acceptance_agent(specs_dir, adversarial)
            state = load_acceptance_state(specs_dir)
            state["auto_fix_rounds"] = 2
            (specs_dir / "acceptance_state.json").write_text(
                json.dumps(state, ensure_ascii=False),
                encoding="utf-8",
            )

            stopped = run_progress("acceptance-plan-fixes", str(specs_dir))
            state = load_acceptance_state(specs_dir)

            self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
            self.assertEqual(state["status"], "blocked")
            self.assertEqual(state["fixes"], [])

    def test_task_graph_blocks_unmet_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            tasks = valid_feature_tasks().replace("- [x] **T-002:**", "- [ ] **T-002:**")
            tasks = tasks.replace("- 状态: done", "- 状态: pending", 1)
            make_requirements_first(specs_dir, tasks=tasks)
            init_progress(specs_dir)
            approve_progress(specs_dir)

            result = run_progress("start", str(specs_dir), "T-002")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unmet dependencies", result.stderr)

    def test_resume_marks_active_dirty_work_as_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approve_progress(specs_dir)
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            self.assertEqual(run_progress("start", str(specs_dir), "T-001", cwd=repo).returncode, 0)
            (repo / "src.py").write_text("print('changed')\n", encoding="utf-8")
            result = run_progress("resume", str(specs_dir), cwd=repo)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("interrupted", result.stdout)

    def test_guard_commit_blocks_business_code_without_progress_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            (repo / "src.py").write_text("print('changed')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src.py"], cwd=repo, check=True, capture_output=True)

            blocked = run_progress("guard-commit", str(specs_dir), cwd=repo)
            subprocess.run(["git", "add", "docs/specs/tasks.md"], cwd=repo, check=True, capture_output=True)
            allowed = run_progress("guard-commit", str(specs_dir), cwd=repo)

            self.assertNotEqual(blocked.returncode, 0)
            self.assertIn("Business-code changes", blocked.stdout)
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

    def test_pre_acceptance_is_not_final_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            result = run_progress("pre-acceptance", str(specs_dir))

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("adaptive final acceptance is still required", result.stdout)

    def test_guard_commit_honors_non_default_specs_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            # Specs live outside docs/specs/ — guard must still detect them.
            specs_dir = repo / "spec"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            (repo / "src.py").write_text("print('changed')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src.py"], cwd=repo, check=True, capture_output=True)

            blocked = run_progress("guard-commit", str(specs_dir), cwd=repo)
            subprocess.run(["git", "add", "spec/tasks.md"], cwd=repo, check=True, capture_output=True)
            allowed = run_progress("guard-commit", str(specs_dir), cwd=repo)

            self.assertNotEqual(blocked.returncode, 0, blocked.stdout)
            self.assertIn("spec/tasks.md", blocked.stdout)
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

    def test_dirty_paths_preserves_porcelain_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
            (specs_dir / "tasks.md").write_text(valid_feature_tasks() + "\n", encoding="utf-8")

            result = run_progress("resume", str(specs_dir), cwd=repo)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("ocs/specs", result.stdout)

    def test_dirty_paths_handles_staged_unstaged_renamed_and_untracked(self) -> None:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import spec_progress

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            (repo / "src.py").write_text("print('base')\n", encoding="utf-8")
            (repo / "old.py").write_text("print('old')\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            (repo / "src.py").write_text("print('changed')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src.py"], cwd=repo, check=True, capture_output=True)
            (specs_dir / "tasks.md").write_text(valid_feature_tasks() + "\n# unstaged\n", encoding="utf-8")
            subprocess.run(["git", "mv", "old.py", "new.py"], cwd=repo, check=True, capture_output=True)
            (repo / "notes.txt").write_text("scratch\n", encoding="utf-8")

            unstaged = spec_progress.dirty_paths(specs_dir, staged=False)
            staged = spec_progress.dirty_paths(specs_dir, staged=True)

            self.assertIn("docs/specs/tasks.md", unstaged)
            self.assertIn("notes.txt", unstaged)
            self.assertIn("new.py", unstaged)
            self.assertNotIn("old.py", unstaged)
            self.assertIn("src.py", staged)
            self.assertIn("new.py", staged)
            self.assertNotIn("old.py", staged)

    def test_explicit_low_risk_overrides_high_risk_keywords(self) -> None:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import spec_progress

        task = spec_progress.Task(
            task_id="T-001",
            title="Clean cache files",
            mark=" ",
            start=0,
            end=1,
            fields={"risk": "低，仅清理缓存"},
        )

        self.assertEqual(task.risk, "低")
        self.assertFalse(task.is_high_risk)

    def test_guard_commit_fails_closed_outside_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp) / "docs" / "specs"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)

            result = run_progress("guard-commit", str(specs_dir), cwd=Path(tmp))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("git is unavailable", result.stdout)

    def test_discover_keeps_done_but_unaccepted_workflows_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            specs_root = repo / "docs" / "specs"
            open_dir = specs_root / "20260615-100000-open"
            done_dir = specs_root / "20260615-100001-done"
            accepted_dir = specs_root / "20260615-100002-accepted"
            open_dir.mkdir(parents=True)
            done_dir.mkdir(parents=True)
            accepted_dir.mkdir(parents=True)
            make_requirements_first(open_dir)
            init_progress(open_dir)
            prepare_acceptance(done_dir)
            prepare_acceptance(accepted_dir)
            self.assertEqual(
                run_progress("acceptance-init", str(accepted_dir), "--mode", "quick").returncode,
                0,
            )
            integration = load_acceptance_state(accepted_dir)["agents"][0]["agent_id"]
            complete_acceptance_agent(accepted_dir, integration)
            self.assertEqual(run_progress("acceptance-finish", str(accepted_dir)).returncode, 0)

            result = run_progress("discover", "docs/specs", cwd=repo)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["workflow_count"], 3)
            self.assertEqual(payload["open_count"], 2)
            records = {item["run_id"]: item for item in payload["workflows"]}
            self.assertTrue(records["20260615-100000-open"]["open"])
            self.assertTrue(records["20260615-100001-done"]["open"])
            self.assertFalse(records["20260615-100002-accepted"]["open"])

    def test_new_workflow_creates_unique_sanitized_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            first = run_progress("new", "docs/specs", "--slug", "Bad / Slug 中文!!!", cwd=repo)
            second = run_progress("new", "docs/specs", "--slug", "Bad / Slug 中文!!!", cwd=repo)

            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            self.assertIn("bad-slug", first_payload["run_id"])
            self.assertNotIn("中文", first_payload["run_id"])
            self.assertNotEqual(first_payload["specs_dir"], second_payload["specs_dir"])
            self.assertTrue((repo / first_payload["specs_dir"]).is_dir())
            self.assertTrue((repo / second_payload["specs_dir"]).is_dir())

    def test_guard_all_honors_isolated_workflow_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs" / "20260615-100000-guard"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            (repo / "src.py").write_text("print('changed')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src.py"], cwd=repo, check=True, capture_output=True)

            blocked = run_progress("guard-all", "docs/specs", cwd=repo)
            subprocess.run(["git", "add", "docs/specs/20260615-100000-guard/tasks.md"], cwd=repo, check=True, capture_output=True)
            allowed = run_progress("guard-all", "docs/specs", cwd=repo)

            self.assertNotEqual(blocked.returncode, 0, blocked.stdout)
            self.assertIn("docs/specs/20260615-100000-guard/tasks.md", blocked.stdout)
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

    def test_guard_all_keeps_legacy_root_workflow_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs"
            specs_dir.mkdir(parents=True)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            (repo / "src.py").write_text("print('changed')\n", encoding="utf-8")
            subprocess.run(["git", "add", "src.py"], cwd=repo, check=True, capture_output=True)

            blocked = run_progress("guard-all", "docs/specs", cwd=repo)
            subprocess.run(["git", "add", "docs/specs/tasks.md"], cwd=repo, check=True, capture_output=True)
            allowed = run_progress("guard-all", "docs/specs", cwd=repo)

            self.assertNotEqual(blocked.returncode, 0, blocked.stdout)
            self.assertIn("docs/specs/tasks.md", blocked.stdout)
            self.assertEqual(allowed.returncode, 0, allowed.stdout + allowed.stderr)

    def test_router_requires_directory_selection_before_resume(self) -> None:
        router = (PLUGIN_ROOT / "skills" / "spec-workflow" / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("discover workflow directories", router)
        self.assertIn("Do not resume an old workflow automatically", router)
        self.assertIn("open_workflows", router)

    def test_all_workflow_skills_require_explicit_activation(self) -> None:
        skill_files = sorted((PLUGIN_ROOT / "skills").glob("*/SKILL.md"))
        self.assertEqual(len(skill_files), 7)
        for skill_file in skill_files:
            with self.subTest(skill=skill_file.parent.name):
                text = skill_file.read_text(encoding="utf-8")
                frontmatter = text.split("---", 2)[1]
                self.assertIn("Explicit activation only.", frontmatter)
                if skill_file.parent.name == "spec-workflow":
                    self.assertIn("不得根据请求内容或任务类型推断触发", frontmatter)
                else:
                    self.assertIn("otherwise do not select", frontmatter)

        router = (PLUGIN_ROOT / "skills" / "spec-workflow" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## Explicit Activation Gate", router)
        self.assertIn("Do not infer activation from task shape or keywords", router)
        self.assertIn("do not print the workflow announcement", router)

        codex_manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        claude_manifest = json.loads(
            (PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(codex_manifest["version"], "0.3.0")
        self.assertEqual(claude_manifest["version"], "0.3.0")
        repo_root = PLUGIN_ROOT.parents[1]
        agents_marketplace = json.loads(
            (repo_root / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        claude_marketplace = json.loads(
            (repo_root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(agents_marketplace["plugins"][0]["version"], "0.3.0")
        self.assertEqual(claude_marketplace["plugins"][0]["version"], "0.3.0")
        self.assertIn("explicit", codex_manifest["description"].lower())
        self.assertTrue(
            all(prompt.startswith("Use spec-workflow") for prompt in codex_manifest["interface"]["defaultPrompt"])
        )

    def test_block_records_blocker_without_completing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approve_progress(specs_dir)

            no_reason = run_progress("block", str(specs_dir), "T-001", "--reason", "")
            blocked = run_progress("block", str(specs_dir), "T-001", "--reason", "waiting on upstream API")

            self.assertNotEqual(no_reason.returncode, 0)
            self.assertEqual(blocked.returncode, 0, blocked.stdout + blocked.stderr)
            tasks_text = (specs_dir / "tasks.md").read_text(encoding="utf-8")
            self.assertIn("- [ ] **T-001:**", tasks_text)
            self.assertIn("waiting on upstream API", tasks_text)
            self.assertIn("Blocked", (specs_dir / "progress.md").read_text(encoding="utf-8"))

    def test_waves_detects_circular_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            tasks = valid_feature_tasks()
            tasks = tasks.replace("- [x] **T-002:**", "- [ ] **T-002:**")
            tasks = tasks.replace("- [~] **T-003:**", "- [ ] **T-003:**")
            tasks = tasks.replace("- 状态: done", "- 状态: pending")
            tasks = tasks.replace("- 状态: skipped", "- 状态: pending")
            # Introduce a cycle: T-001 depends on T-003, T-003 depends on T-002, T-002 on T-001.
            tasks = tasks.replace("- 依赖: 无", "- 依赖: T-003", 1)
            make_requirements_first(specs_dir, tasks=tasks)

            result = run_progress("waves", str(specs_dir))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Circular", result.stderr)

    def test_validator_accepts_completed_task_graph_without_pending_waves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            tasks = valid_feature_tasks()
            tasks = tasks.replace("- [ ] **T-001:**", "- [x] **T-001:**")
            tasks = tasks.replace("- 状态: pending", "- 状态: done", 1)
            tasks = tasks.replace("- 验证证据: pending", "- 验证证据: pytest passed", 1)
            tasks = tasks.replace("> **进度：** 2 / 3 已完成", "> **进度：** 3 / 3 已完成")
            make_requirements_first(specs_dir, tasks=tasks)
            init_progress(specs_dir)
            result = run_validator(specs_dir, "requirements-first")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("所有任务已完成", result.stdout)

    def test_sync_check_flags_changed_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            # init writes spec.yml with current artifact hashes; mutate product.md.
            run_progress("sync-check", str(specs_dir), "--write")
            (specs_dir / "product.md").write_text(
                valid_product() + "\n## Extra section\nNew content.\n", encoding="utf-8"
            )

            result = run_progress("sync-check", str(specs_dir))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("product.md changed", result.stdout)

    def test_verify_command_and_criteria_are_distinct_hash_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approved = run_progress("approve", str(specs_dir), "--evidence", "approved")
            self.assertEqual(approved.returncode, 0, approved.stdout + approved.stderr)
            tasks = (specs_dir / "tasks.md").read_text(encoding="utf-8")
            self.assertIn("验证命令: pytest tests/test_comments.py", tasks)
            self.assertIn("验证标准: Unit tests pass", tasks)
            tasks = tasks.replace("验证标准: Unit tests pass", "验证标准: Mutation only changes criteria")
            (specs_dir / "tasks.md").write_text(tasks, encoding="utf-8")
            result = run_progress("sync-check", str(specs_dir))

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("tasks.md plan changed since last approved index", result.stdout)

    def test_validator_rejects_invalid_task_field_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            tasks = valid_feature_tasks().replace("- 风险: low", "- 风险: low | medium | high", 1)
            tasks = tasks.replace("- 可并行: 否", "- 可并行: 是 | 否", 1)
            make_requirements_first(specs_dir, tasks=tasks)
            init_progress(specs_dir)

            result = run_validator(specs_dir, "requirements-first")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("未替换风险等级选项", result.stdout)
            self.assertIn("未替换并行选项", result.stdout)

    def test_progress_writes_escape_markdown_table_cells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            make_requirements_first(specs_dir)
            init_progress(specs_dir)
            approval = run_progress("approve", str(specs_dir), "--evidence", "approved")
            self.assertEqual(approval.returncode, 0, approval.stdout + approval.stderr)

            result = run_progress(
                "complete",
                str(specs_dir),
                "T-001",
                "--evidence",
                "pytest passed | injected\nnew row",
                "--notes",
                "note | extra\nline",
            )
            progress = (specs_dir / "progress.md").read_text(encoding="utf-8")

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("pytest passed \\| injected<br>new row", progress)
            self.assertIn("note \\| extra<br>line", progress)

    def test_specs_path_rejects_traversal_outside_base(self) -> None:
        if str(SCRIPT_DIR) not in sys.path:
            sys.path.insert(0, str(SCRIPT_DIR))
        import spec_progress

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "repo"
            (base / "docs" / "specs").mkdir(parents=True)
            # Inside the base is allowed.
            self.assertTrue(
                spec_progress.specs_path(base / "docs" / "specs", base_dir=base)
            )
            self.assertEqual(
                spec_progress.specs_path("docs/specs", base_dir=base),
                (base / "docs" / "specs").resolve(),
            )
            # ../ traversal outside the base is rejected.
            with self.assertRaises(spec_progress.SpecProgressError):
                spec_progress.specs_path(base / ".." / "outside", base_dir=base)
            with self.assertRaises(spec_progress.SpecProgressError):
                spec_progress.specs_path("../outside", base_dir=base)

    def test_mcp_tools_list_exposes_spec_approve_and_directory_tools(self) -> None:
        requests = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                "",
            ]
        )
        result = subprocess.run(
            [sys.executable, str(MCP_SERVER)],
            input=requests,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        initialize_reply = next(reply for reply in replies if reply.get("id") == 1)
        tools_reply = next(reply for reply in replies if reply.get("id") == 2)
        tools = tools_reply["result"]["tools"]
        names = {tool["name"] for tool in tools}
        self.assertIn("spec_approve", names)
        self.assertIn("spec_discover_workflows", names)
        self.assertIn("spec_new_workflow", names)
        acceptance_init = next(tool for tool in tools if tool["name"] == "spec_acceptance_init")
        self.assertEqual(
            acceptance_init["inputSchema"]["properties"]["mode"]["enum"],
            ["quick", "adaptive", "full"],
        )
        self.assertEqual(initialize_reply["result"]["serverInfo"]["version"], "0.3.0")

    def test_plugin_manifests_register_mcp_idle_timeout(self) -> None:
        codex_manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        claude_config = json.loads((PLUGIN_ROOT / ".mcp.json").read_text(encoding="utf-8"))

        codex_server = codex_manifest["mcpServers"]["spec_workflow_progress"]
        claude_server = claude_config["mcpServers"]["spec_workflow_progress"]
        self.assertEqual(codex_server["args"], ["./mcp/spec_progress_server.py"])
        self.assertEqual(codex_server["cwd"], ".")
        self.assertEqual(codex_server["env"]["SPEC_WORKFLOW_MCP_IDLE_TIMEOUT_SECONDS"], "300")
        self.assertEqual(claude_server["env"]["SPEC_WORKFLOW_MCP_IDLE_TIMEOUT_SECONDS"], "300")

    def test_mcp_exits_after_idle_timeout_with_open_stdin(self) -> None:
        env = os.environ.copy()
        env["SPEC_WORKFLOW_MCP_IDLE_TIMEOUT_SECONDS"] = "0.2"
        process = subprocess.Popen(
            [sys.executable, str(MCP_SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        try:
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def test_mcp_request_resets_idle_timeout(self) -> None:
        env = os.environ.copy()
        env["SPEC_WORKFLOW_MCP_IDLE_TIMEOUT_SECONDS"] = "0.6"
        process = subprocess.Popen(
            [sys.executable, str(MCP_SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        try:
            time.sleep(0.35)
            self.assertIsNotNone(process.stdin)
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
            process.stdin.flush()
            self.assertIsNotNone(process.stdout)
            reply = json.loads(process.stdout.readline())
            self.assertEqual(reply["id"], 1)

            time.sleep(0.35)
            self.assertIsNone(process.poll())
            self.assertEqual(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def test_mcp_directory_tools_smoke(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            for launch_from_repo in (True, False):
                with self.subTest(launch_from_repo=launch_from_repo):
                    requests = "\n".join(
                        [
                            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": 2,
                                    "method": "tools/call",
                                    "params": {
                                        "name": "spec_new_workflow",
                                        "arguments": {"specs_root": "docs/specs", "slug": "MCP Smoke"},
                                    },
                                }
                            ),
                            json.dumps(
                                {
                                    "jsonrpc": "2.0",
                                    "id": 3,
                                    "method": "tools/call",
                                    "params": {
                                        "name": "spec_discover_workflows",
                                        "arguments": {"specs_root": "docs/specs"},
                                    },
                                }
                            ),
                            "",
                        ]
                    )
                    env = os.environ.copy()
                    cwd = repo
                    if not launch_from_repo:
                        cwd = SCRIPT_DIR
                        env["SPEC_WORKFLOW_BASE_DIR"] = str(repo)
                    result = subprocess.run(
                        [sys.executable, str(MCP_SERVER)],
                        cwd=cwd,
                        input=requests,
                        check=False,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                    )

                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
                    new_reply = next(reply for reply in replies if reply.get("id") == 2)
                    discover_reply = next(reply for reply in replies if reply.get("id") == 3)
                    created = json.loads(new_reply["result"]["content"][0]["text"])
                    discovered = json.loads(discover_reply["result"]["content"][0]["text"])
                    created_path = Path(created["specs_dir"])
                    if not created_path.is_absolute():
                        created_path = repo / created_path
                    self.assertTrue(created_path.is_dir())
                    self.assertIn("mcp-smoke", created["run_id"])
                    self.assertEqual(Path(discovered["specs_root"]), (repo / "docs" / "specs").resolve())

    def test_mcp_missing_required_argument_returns_request_error(self) -> None:
        requests = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": "spec_approve", "arguments": {"specs_dir": "docs/specs"}},
                    }
                ),
                "",
            ]
        )
        result = subprocess.run(
            [sys.executable, str(MCP_SERVER)],
            input=requests,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        error_reply = next(reply for reply in replies if reply.get("id") == 2)
        self.assertEqual(error_reply["error"]["code"], -32000)
        self.assertIn("evidence is required", error_reply["error"]["message"])
        self.assertNotIn('"id": null', result.stdout)

    def test_acceptance_adaptive_happy_path_requires_global_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            init = run_progress("acceptance-init", str(specs_dir))
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            state = load_acceptance_state(specs_dir)

            for agent in list(state["agents"]):
                complete_acceptance_agent(specs_dir, agent["agent_id"])

            state = load_acceptance_state(specs_dir)
            integration = next(agent for agent in state["agents"] if agent["role"] == "integration")
            early_finish = run_progress("acceptance-finish", str(specs_dir))
            self.assertNotEqual(early_finish.returncode, 0)
            self.assertIn("global integration review has not passed", early_finish.stderr)

            complete_acceptance_agent(specs_dir, integration["agent_id"])
            finish = run_progress("acceptance-finish", str(specs_dir))
            resumed = run_progress("acceptance-status", str(specs_dir))
            state = load_acceptance_state(specs_dir)

            self.assertEqual(finish.returncode, 0, finish.stdout + finish.stderr)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertEqual(state["status"], "accepted")
            self.assertIn("Accepted", (specs_dir / "progress.md").read_text(encoding="utf-8"))
            after_finish = run_progress("acceptance-next-round", str(specs_dir))
            self.assertNotEqual(after_finish.returncode, 0)
            self.assertEqual(load_acceptance_state(specs_dir)["status"], "accepted")

    def test_acceptance_agent_rejects_unbound_actionable_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            state = load_acceptance_state(specs_dir)
            primary = state["agents"][0]["agent_id"]
            start_acceptance_agent(specs_dir, primary)
            completed = run_progress(
                "acceptance-complete-agent",
                str(specs_dir),
                primary,
                "--result",
                "ACTIONABLE_ISSUES",
                "--report",
                "claims an issue but records none",
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("requires at least one recorded P0-P2", completed.stderr)
            self.assertEqual(load_acceptance_state(specs_dir)["agents"][0]["status"], "running")

    def test_acceptance_actionable_result_rejects_p3_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            primary = load_acceptance_state(specs_dir)["agents"][0]["agent_id"]
            start_acceptance_agent(specs_dir, primary)
            self.assertEqual(run_progress(
                "acceptance-record-issue",
                str(specs_dir),
                "--unit",
                "U-001",
                "--severity",
                "P3",
                "--title",
                "Advisory polish",
                "--evidence",
                "non-blocking evidence",
                "--agent",
                primary,
            ).returncode, 0)

            completed = run_progress(
                "acceptance-complete-agent",
                str(specs_dir),
                primary,
                "--result",
                "ACTIONABLE_ISSUES",
                "--report",
                "only a P3 advisory",
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("P3-P4 advisories", completed.stderr)

    def test_acceptance_finish_rechecks_reviewed_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            specs_dir = Path(tmp)
            prepare_acceptance(specs_dir)
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            primary = load_acceptance_state(specs_dir)["agents"][0]["agent_id"]
            complete_acceptance_agent(specs_dir, primary)
            integration = next(
                agent["agent_id"]
                for agent in load_acceptance_state(specs_dir)["agents"]
                if agent["role"] == "integration"
            )
            complete_acceptance_agent(specs_dir, integration)
            frozen_index = (specs_dir / "spec.yml").read_text(encoding="utf-8")
            write(specs_dir / "product.md", valid_product() + "\nChanged after review")

            finish = run_progress("acceptance-finish", str(specs_dir))

            self.assertNotEqual(finish.returncode, 0)
            self.assertIn("changed since last approved index", finish.stderr)
            self.assertEqual((specs_dir / "spec.yml").read_text(encoding="utf-8"), frozen_index)

    def test_acceptance_finish_rejects_a_clean_commit_after_integration_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs" / "run"
            specs_dir.mkdir(parents=True)
            prepare_acceptance(specs_dir)
            write(repo / "app.py", "print('reviewed')")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Spec Test", "-c", "user.email=spec@example.invalid", "commit", "-m", "baseline"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            self.assertEqual(run_progress("acceptance-init", str(specs_dir)).returncode, 0)
            primary = load_acceptance_state(specs_dir)["agents"][0]["agent_id"]
            complete_acceptance_agent(specs_dir, primary)
            integration = next(
                agent["agent_id"]
                for agent in load_acceptance_state(specs_dir)["agents"]
                if agent["role"] == "integration"
            )
            complete_acceptance_agent(specs_dir, integration)
            write(repo / "app.py", "print('changed after review')")
            subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Spec Test", "-c", "user.email=spec@example.invalid", "commit", "-m", "late change"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            finish = run_progress("acceptance-finish", str(specs_dir))

            self.assertNotEqual(finish.returncode, 0)
            self.assertIn("changed after global integration review", finish.stderr)

    def test_global_integration_rejects_dirty_business_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs" / "run"
            specs_dir.mkdir(parents=True)
            prepare_acceptance(specs_dir)
            write(repo / "app.py", "print('baseline')")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Spec Test", "-c", "user.email=spec@example.invalid", "commit", "-m", "baseline"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                run_progress("acceptance-init", str(specs_dir), "--mode", "quick").returncode,
                0,
            )
            integration = load_acceptance_state(specs_dir)["agents"][0]["agent_id"]
            write(repo / "app.py", "print('dirty')")

            started = run_progress("acceptance-start-agent", str(specs_dir), integration)

            self.assertNotEqual(started.returncode, 0)
            self.assertIn("clean pre-acceptance state", started.stderr)

    def test_acceptance_finish_rejects_dirty_docs_after_integration_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            specs_dir = repo / "docs" / "specs" / "run"
            specs_dir.mkdir(parents=True)
            prepare_acceptance(specs_dir)
            write(repo / "README.md", "reviewed documentation")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.name=Spec Test", "-c", "user.email=spec@example.invalid", "commit", "-m", "baseline"],
                cwd=repo,
                check=True,
                capture_output=True,
            )
            self.assertEqual(
                run_progress("acceptance-init", str(specs_dir), "--mode", "quick").returncode,
                0,
            )
            integration = load_acceptance_state(specs_dir)["agents"][0]["agent_id"]
            complete_acceptance_agent(specs_dir, integration)
            write(repo / "README.md", "changed after review")

            finish = run_progress("acceptance-finish", str(specs_dir))

            self.assertNotEqual(finish.returncode, 0)
            self.assertIn("changed after global integration review", finish.stderr)


if __name__ == "__main__":
    unittest.main()
