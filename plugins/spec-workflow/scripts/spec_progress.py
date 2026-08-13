#!/usr/bin/env python3
"""Spec workflow progress, resume, and task-state utilities.

This module is intentionally stdlib-only. The CLI, MCP wrapper, validator,
and git-hook template all share this implementation so progress enforcement
does not split into several subtly different rule sets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TASK_RE = re.compile(
    r"^(?P<indent>\s*)-\s+\[(?P<mark>[ xX~])\]\s+(?:\*\*)?"
    r"(?P<task_id>[TB]-\d+)\s*[:：](?:\*\*)?\s*(?P<title>.+?)\s*$"
)
FIELD_RE = re.compile(r"^\s*-\s+(?P<key>[^:：]+?)\s*[:：]\s*(?P<value>.*)$")
TOP_FIELD_RE = re.compile(r"^>\s+\*\*(?P<key>[^*]+):\*\*\s*(?P<value>.*)$")
TASK_TOP_FIELD_RE = re.compile(
    r"^(?P<indent>\s*)(?P<prefix>>\s+\*\*)(?P<label>[^*：:]+)(?P<colon>[：:])(?P<suffix>\*\*\s*)(?P<value>.*)$"
)
CHECKBOX_RE = re.compile(r"-\s+\[[ xX~]\]")
# English risk terms use word boundaries so "cache" does not match inside
# "caching layer" and "incident" does not match unrelated prose. CJK terms have
# no word boundaries, so they are matched directly.
_HIGH_RISK_EN = (
    r"auth|authorization|authentication|payment|billing|database|migration|"
    r"data[\s-]?repair|concurrency|distributed|cache|secret|encryption|"
    r"sensitive|incident|rollback|hotfix|privacy|security|transaction|"
    r"lock[\s-]?free|deadlock|sla|credential|token|permission|access[\s-]?control"
)
_HIGH_RISK_CJK = (
    r"鉴权|认证|授权|支付|计费|数据库|迁移|数据修复|并发|分布式|缓存|密钥|"
    r"凭证|令牌|加密|敏感|事故|回滚|热修复|隐私|安全|事务|死锁|权限|访问控制"
)
HIGH_RISK_RE = re.compile(
    rf"(?:\b(?:{_HIGH_RISK_EN})\b)|(?:{_HIGH_RISK_CJK})",
    re.IGNORECASE,
)
EXPLICIT_RISK_RE = re.compile(
    r"^\s*(?P<risk>critical|high|medium|low|高风险|中风险|低风险|高|中|低)\b",
    re.IGNORECASE,
)


FIELD_ALIASES = {
    "status": {"status", "状态"},
    "files": {"files", "涉及文件", "file", "path"},
    "verify": {"verify", "verification", "验证命令", "test", "测试"},
    "verify_criteria": {"verify_criteria", "verification_criteria", "验证标准", "验收标准", "acceptance criteria"},
    "evidence": {"evidence", "验证证据", "证据"},
    "depends_on": {"depends_on", "dependencies", "依赖", "depends on"},
    "risk": {"risk", "风险", "风险等级"},
    "covers": {"covers", "coverage", "覆盖", "覆盖需求"},
    "parallelizable": {"parallelizable", "并行", "可并行"},
    "blocker": {"blocker", "阻塞原因", "blocked by"},
    "completed_at": {"completed_at", "完成时间"},
    "notes": {"notes", "备注"},
}

FIELD_LABELS = {
    "status": "状态",
    "files": "涉及文件",
    "verify": "验证命令",
    "verify_criteria": "验证标准",
    "evidence": "验证证据",
    "depends_on": "依赖",
    "risk": "风险",
    "covers": "覆盖",
    "parallelizable": "可并行",
    "blocker": "阻塞原因",
    "completed_at": "完成时间",
    "notes": "备注",
}

VALID_TASK_STATES = {"pending", "active", "blocked", "done", "skipped", "interrupted"}
VALID_PROGRESS_STATES = {"Draft", "Approved", "In Progress", "Blocked", "Completed", "Accepted"}
VALID_APPROVAL_STATES = {"pending", "approved", "reapproval-required"}
DEFAULT_SPECS_ROOT = Path("docs") / "specs"
ACCEPTANCE_STATE_FILE = "acceptance_state.json"
ACCEPTANCE_FIXES_FILE = "acceptance-fixes.md"
ACCEPTANCE_AGENT_RESULTS = {"PASS", "ACTIONABLE_ISSUES"}
ACCEPTANCE_SEVERITIES = {"P0", "P1", "P2", "P3", "P4"}
ACCEPTANCE_BLOCKING_SEVERITIES = {"P0", "P1", "P2"}
ACCEPTANCE_MODES = {"quick", "adaptive", "full"}
ACCEPTANCE_DEFAULT_MODE = "adaptive"
ACCEPTANCE_MAX_AUTO_FIX_ROUNDS = 2
ACCEPTANCE_GLOBAL_UNIT = "GLOBAL"
GIT_UNAVAILABLE = "__GIT_UNAVAILABLE__"


class SpecProgressError(Exception):
    """Expected user-facing progress error."""


@dataclass
class Task:
    task_id: str
    title: str
    mark: str
    start: int
    end: int
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def checkbox_state(self) -> str:
        if self.mark.lower() == "x":
            return "done"
        if self.mark == "~":
            return "skipped"
        return "pending"

    @property
    def state(self) -> str:
        explicit = self.fields.get("status", "").strip().lower()
        if explicit in VALID_TASK_STATES and self.checkbox_state == "pending":
            return explicit
        return self.checkbox_state

    @property
    def depends_on(self) -> list[str]:
        value = self.fields.get("depends_on", "")
        if not value or value.lower() in {"none", "n/a"} or value in {"无", "暂无"}:
            return []
        return [
            item.strip()
            for item in re.split(r"[,，、\s]+", value)
            if re.match(r"^[TB]-\d+$", item.strip())
        ]

    @property
    def risk(self) -> str:
        value = self.fields.get("risk", "").strip().lower()
        if value:
            match = EXPLICIT_RISK_RE.match(value)
            if match:
                return match.group("risk").lower()
            return value
        if HIGH_RISK_RE.search(self.title + " " + " ".join(self.fields.values())):
            return "high"
        return "low"

    @property
    def is_high_risk(self) -> bool:
        return self.risk in {"high", "critical", "高", "高风险"} or bool(
            HIGH_RISK_RE.search(self.risk)
        )

    @property
    def parallelizable(self) -> bool:
        value = self.fields.get("parallelizable", "").strip().lower()
        return value in {"true", "yes", "y", "1", "是", "可", "parallel"}


@dataclass
class Progress:
    workflow: str = "unknown"
    mode: str = "strict"
    status: str = "Draft"
    current_task: str = "n/a"
    approval: str = "pending"
    last_checkpoint: str = "n/a"
    branch: str = "n/a"
    last_known_commit: str = "n/a"
    resume_summary: dict[str, str] = field(default_factory=dict)
    active_state: dict[str, str] = field(default_factory=dict)
    completed_rows: list[str] = field(default_factory=list)
    recovery_notes: list[str] = field(default_factory=list)


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def run_id_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def sanitize_slug(raw: str | None) -> str:
    """Return a filesystem-safe, readable workflow slug."""
    cleaned = (raw or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", cleaned)
    cleaned = cleaned.strip("-")
    return cleaned or "spec-workflow"


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SpecProgressError(
            f"{path} is not valid UTF-8 (decode error at byte {exc.start}); "
            "re-save the file as UTF-8 and retry"
        ) from exc


def write_text(path: Path, content: str) -> None:
    write_bytes_atomic(path, (content.rstrip() + "\n").encode("utf-8"))


def read_json(path: Path) -> dict[str, object]:
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise SpecProgressError(f"{path.name} is not valid JSON: {exc}") from exc


def write_json(path: Path, data: dict[str, object]) -> None:
    write_text(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Atomically replace a state file with flushed bytes written in-place."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        if hasattr(os, "O_DIRECTORY"):
            try:
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            except OSError:
                directory_fd = None
            if directory_fd is not None:
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
    except OSError as exc:
        if temp_name:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
        raise SpecProgressError(f"Failed to write {path}: {exc}") from exc


def normalize_field_key(raw: str) -> str | None:
    cleaned = re.sub(r"^[^\w\u4e00-\u9fff]+", "", raw.strip())
    cleaned = cleaned.strip().lower().replace("-", "_")
    for canonical, aliases in FIELD_ALIASES.items():
        if cleaned in {alias.lower().replace("-", "_") for alias in aliases}:
            return canonical
    return None


def specs_path(specs_dir: str | Path, base_dir: str | Path | None = None) -> Path:
    """Resolve specs_dir to an absolute path.

    When base_dir is provided, the resolved path must stay inside it; this
    blocks ``../`` traversal from untrusted callers (e.g. the MCP server) that
    would otherwise read or write files outside the intended repository.
    """
    if base_dir is not None:
        base = Path(base_dir).resolve()
        raw_path = Path(specs_dir)
        resolved = (base / raw_path).resolve() if not raw_path.is_absolute() else raw_path.resolve()
        if resolved != base and base not in resolved.parents:
            raise SpecProgressError(
                f"specs_dir must stay within {base}; refusing path {resolved}"
            )
    else:
        resolved = Path(specs_dir).resolve()
    return resolved


def workflow_matches(specs_dir: str | Path) -> list[str]:
    """All workflows whose required artifacts are present.

    Single source of truth for workflow detection. detect_workflow picks one
    (bugfix takes priority because design.md is shared); the validator uses the
    full list to flag the ambiguous multi-match case.
    """
    root = specs_path(specs_dir)
    matches: list[str] = []
    if (root / "bugfix.md").is_file() and (root / "design.md").is_file():
        matches.append("bugfix")
    if (root / "design.md").is_file() and (root / "requirements.md").is_file():
        matches.append("design-first")
    if (root / "product.md").is_file() and (root / "architecture.md").is_file():
        matches.append("requirements-first")
    return matches


def detect_workflow(specs_dir: str | Path) -> str:
    matches = workflow_matches(specs_dir)
    return matches[0] if matches else "unknown"


def primary_artifacts(workflow: str) -> list[str]:
    return {
        "requirements-first": ["product.md", "architecture.md"],
        "design-first": ["design.md", "requirements.md"],
        "bugfix": ["bugfix.md", "design.md"],
    }.get(workflow, [])


def expected_prefix(workflow: str) -> str:
    return "B" if workflow == "bugfix" else "T"


def parse_tasks(specs_dir: str | Path) -> list[Task]:
    path = specs_path(specs_dir) / "tasks.md"
    if not path.is_file():
        raise SpecProgressError("tasks.md is missing")
    lines = read_text(path).splitlines()
    starts: list[tuple[int, re.Match[str]]] = []
    for index, line in enumerate(lines):
        match = TASK_RE.match(line)
        if match:
            starts.append((index, match))

    tasks: list[Task] = []
    for pos, (start, match) in enumerate(starts):
        end = starts[pos + 1][0] if pos + 1 < len(starts) else len(lines)
        fields: dict[str, str] = {}
        for line in lines[start + 1 : end]:
            field_match = FIELD_RE.match(line)
            if not field_match:
                continue
            key = normalize_field_key(field_match.group("key"))
            if key:
                fields[key] = field_match.group("value").strip()
        tasks.append(
            Task(
                task_id=match.group("task_id"),
                title=match.group("title").strip(),
                mark=match.group("mark"),
                start=start,
                end=end,
                fields=fields,
            )
        )
    return tasks


def get_task(specs_dir: str | Path, task_id: str) -> Task:
    for task in parse_tasks(specs_dir):
        if task.task_id == task_id:
            return task
    raise SpecProgressError(f"Task not found: {task_id}")


def task_stats(tasks: list[Task]) -> dict[str, int]:
    stats = {state: 0 for state in VALID_TASK_STATES}
    for task in tasks:
        stats[task.state] = stats.get(task.state, 0) + 1
    return stats


def completed_ids(tasks: list[Task]) -> set[str]:
    return {task.task_id for task in tasks if task.state in {"done", "skipped"}}


def next_executable_tasks(tasks: list[Task]) -> list[Task]:
    done = completed_ids(tasks)
    active = [task for task in tasks if task.state in {"active", "blocked", "interrupted"}]
    if active:
        return active[:1]
    ready = [
        task
        for task in tasks
        if task.state == "pending" and all(dep in done for dep in task.depends_on)
    ]
    if not ready:
        return []
    first_high = next((task for task in ready if task.is_high_risk), None)
    if first_high:
        return [first_high]
    parallel = [task for task in ready if task.parallelizable and not task.is_high_risk]
    return parallel or ready[:1]


def task_sort_key(task_id: str) -> tuple[str, int, str]:
    """Numeric-aware sort key so B-2 precedes B-10 (not lexicographic)."""
    match = re.match(r"^([A-Za-z]+)-(\d+)$", task_id)
    if match:
        return (match.group(1), int(match.group(2)), "")
    return (task_id, 0, task_id)


def execution_waves(tasks: list[Task]) -> list[list[str]]:
    remaining = {task.task_id: task for task in tasks if task.state == "pending"}
    done = completed_ids(tasks)
    waves: list[list[str]] = []
    while remaining:
        ready = [
            task
            for task in remaining.values()
            if all(dep in done or dep not in remaining for dep in task.depends_on)
        ]
        if not ready:
            cycle = sorted(remaining, key=task_sort_key)
            raise SpecProgressError(
                "Circular or unresolvable task dependency detected among: "
                + ", ".join(cycle)
            )
        high = [task for task in ready if task.is_high_risk]
        if high:
            wave = [sorted(high, key=lambda task: task_sort_key(task.task_id))[0].task_id]
        else:
            parallel = [task for task in ready if task.parallelizable]
            chosen = parallel or [sorted(ready, key=lambda task: task_sort_key(task.task_id))[0]]
            wave = sorted((task.task_id for task in chosen), key=task_sort_key)
        waves.append(wave)
        for task_id in wave:
            done.add(task_id)
            remaining.pop(task_id, None)
    return waves


def task_progress_values(tasks: list[Task]) -> tuple[int, int, str]:
    completed = sum(1 for task in tasks if task.state in {"done", "skipped"})
    total = len(tasks)
    return completed, total, f"{completed} / {total} 已完成"


def workflow_status_for_tasks(tasks: list[Task], preferred: str | None = None) -> str:
    if preferred in {"Draft", "Approved", "Accepted"}:
        return preferred
    if any(task.state == "blocked" for task in tasks):
        return "Blocked"
    if any(task.state in {"pending", "active", "interrupted"} for task in tasks):
        return "In Progress" if any(task.state in {"active", "done", "skipped", "interrupted"} for task in tasks) else "Approved"
    return "Completed"


def current_task_for_tasks(tasks: list[Task]) -> str:
    next_task = next_executable_tasks(tasks)
    return next_task[0].task_id if next_task else "n/a"


def update_tasks_metadata(
    specs_dir: str | Path,
    *,
    status: str | None = None,
    current_task: str | None = None,
    log_row: str | None = None,
) -> None:
    """Synchronize the human-readable tasks.md summary with task states."""
    path = specs_path(specs_dir) / "tasks.md"
    if not path.is_file():
        return
    tasks = parse_tasks(specs_dir)
    completed, total, progress = task_progress_values(tasks)
    derived_status = status or workflow_status_for_tasks(tasks)
    derived_current = current_task if current_task is not None else current_task_for_tasks(tasks)
    replacements = {
        "状态": derived_status,
        "status": derived_status,
        "当前任务": derived_current,
        "current task": derived_current,
        "进度": progress,
        "progress": f"{completed}/{total}",
        "最后更新": now(),
        "last updated": now(),
    }
    lines = read_text(path).splitlines()
    updated: list[str] = []
    for line in lines:
        match = TASK_TOP_FIELD_RE.match(line)
        if match:
            key = match.group("label").strip().lower()
            value = replacements.get(key)
            if value is not None:
                line = (
                    f"{match.group('indent')}{match.group('prefix')}{match.group('label')}{match.group('colon')}"
                    f"{match.group('suffix')}{value}"
                )
        updated.append(line)

    if log_row:
        insert_at: int | None = None
        placeholder_at: int | None = None
        in_log = False
        for index, line in enumerate(updated):
            stripped = line.strip()
            if stripped in {"## 完成日志", "## Completed Work Log"}:
                in_log = True
                continue
            if in_log and stripped.startswith("## ") and stripped not in {"## 完成日志", "## Completed Work Log"}:
                insert_at = index
                break
            if in_log and stripped.startswith("|"):
                if "暂无完成任务" in stripped or stripped.startswith("| —") or stripped.startswith("| - | - |"):
                    placeholder_at = index
                elif "---" not in stripped and "Task ID" not in stripped and "任务 ID" not in stripped:
                    insert_at = index + 1
        if in_log:
            if placeholder_at is not None:
                updated[placeholder_at] = log_row
            else:
                updated.insert(insert_at if insert_at is not None else len(updated), log_row)
        else:
            updated.extend(
                [
                    "",
                    "## 完成日志",
                    "",
                    "| 任务 ID | 完成时间 | Commit Hash | 验证证据 | 备注 |",
                    "|:---|:---|:---|:---|:---|",
                    log_row,
                ]
            )
    write_text(path, "\n".join(updated))


def update_task_fields(
    specs_dir: str | Path,
    task_id: str,
    mark: str | None,
    updates: dict[str, str],
) -> None:
    path = specs_path(specs_dir) / "tasks.md"
    lines = read_text(path).splitlines()
    task = get_task(specs_dir, task_id)
    block = lines[task.start : task.end]
    task_line = block[0]
    if mark is not None:
        task_line = CHECKBOX_RE.sub(f"- [{mark}]", task_line, count=1)

    update_keys = set(updates)
    kept_body: list[str] = []
    for line in block[1:]:
        match = FIELD_RE.match(line)
        if match and normalize_field_key(match.group("key")) in update_keys:
            continue
        kept_body.append(line)

    inserted = [f"  - {FIELD_LABELS[key]}: {value}" for key, value in updates.items()]
    new_block = [task_line] + inserted + kept_body
    write_text(path, "\n".join(lines[: task.start] + new_block + lines[task.end :]))


def task_digest(tasks: list[Task]) -> str:
    payload = "\n".join(
        json.dumps(
            {
                "task_id": task.task_id,
                "title": task.title,
                "mark": task.mark,
                "fields": dict(sorted(task.fields.items())),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for task in tasks
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def task_plan_digest(tasks: list[Task]) -> str:
    """Hash only the approved task plan, excluding progress fields."""
    plan_fields = ("files", "verify", "verify_criteria", "depends_on", "risk", "covers", "parallelizable")
    payload = "\n".join(
        json.dumps(
            {
                "task_id": task.task_id,
                "title": task.title,
                "fields": {key: task.fields.get(key, "") for key in plan_fields},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for task in tasks
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def acceptance_tasks_file_hash(specs_dir: str | Path) -> str:
    """Hash tasks.md content while ignoring mutable top-level progress metadata."""
    path = specs_path(specs_dir) / "tasks.md"
    mutable_labels = {
        "状态", "status", "当前任务", "current task",
        "进度", "progress", "最后更新", "last updated",
    }
    stable_lines: list[str] = []
    before_first_task = True
    for line in read_text(path).splitlines():
        if TASK_RE.match(line):
            before_first_task = False
        match = TASK_TOP_FIELD_RE.match(line)
        if before_first_task and match and match.group("label").strip().lower() in mutable_labels:
            line = (
                f"{match.group('indent')}{match.group('prefix')}{match.group('label')}"
                f"{match.group('colon')}{match.group('suffix')}<mutable>"
            )
        stable_lines.append(line)
    payload = "\n".join(stable_lines).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def task_phase_for_line(lines: list[str], start: int) -> str:
    phase = "Unphased"
    for line in lines[: start + 1]:
        stripped = line.strip()
        if stripped.startswith("## "):
            phase = stripped.lstrip("#").strip()
    return phase


def build_review_units(specs_dir: str | Path, task_ids: list[str] | None = None) -> list[dict[str, object]]:
    root = specs_path(specs_dir)
    tasks = parse_tasks(root)
    include = set(task_ids or [task.task_id for task in tasks])
    lines = read_text(root / "tasks.md").splitlines()
    units: list[dict[str, object]] = []
    group: list[Task] = []
    group_phase = ""
    for task in tasks:
        if task.task_id not in include:
            continue
        phase = task_phase_for_line(lines, task.start)
        force_standalone = task.is_high_risk
        if force_standalone:
            if group:
                units.append(review_unit_payload(len(units) + 1, group, group_phase))
                group = []
                group_phase = ""
            units.append(review_unit_payload(len(units) + 1, [task], phase))
            continue
        if group and (phase != group_phase or len(group) >= 3):
            units.append(review_unit_payload(len(units) + 1, group, group_phase))
            group = []
        group.append(task)
        group_phase = phase
    if group:
        units.append(review_unit_payload(len(units) + 1, group, group_phase))
    return units


def review_unit_payload(index: int, tasks: list[Task], phase: str) -> dict[str, object]:
    return {
        "unit_id": f"U-{index:03d}",
        "task_ids": [task.task_id for task in tasks],
        "phase": phase,
        "high_risk": any(task.is_high_risk for task in tasks),
        "requires_adversarial": any(task.is_high_risk for task in tasks),
        "status": "pending",
        "review_status": "pending",
        "adversarial_status": "pending",
        "round_started": 1,
        "last_result": None,
    }


def markdown_table_cell(value: object) -> str:
    text = str(value if value is not None else "")
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    text = re.sub(r"[\r\n]+", "<br>", text)
    return text


def acceptance_path(specs_dir: str | Path) -> Path:
    return specs_path(specs_dir) / ACCEPTANCE_STATE_FILE


def acceptance_fixes_path(specs_dir: str | Path) -> Path:
    return specs_path(specs_dir) / ACCEPTANCE_FIXES_FILE


def normalize_acceptance_mode(mode: str | None) -> str:
    normalized = (mode or ACCEPTANCE_DEFAULT_MODE).strip().lower()
    if normalized not in ACCEPTANCE_MODES:
        raise SpecProgressError(
            f"acceptance mode must be one of {', '.join(sorted(ACCEPTANCE_MODES))}"
        )
    return normalized


def default_acceptance_state(
    specs_dir: str | Path,
    acceptance_mode: str = ACCEPTANCE_DEFAULT_MODE,
) -> dict[str, object]:
    root = specs_path(specs_dir)
    tasks = parse_tasks(root)
    mode = normalize_acceptance_mode(acceptance_mode)
    if mode == "quick" and any(task.risk not in {"low", "低", "低风险"} for task in tasks):
        raise SpecProgressError(
            "Quick acceptance is limited to low-risk workflows; use adaptive or full"
        )
    units = build_review_units(root)
    if mode == "full":
        for unit in units:
            unit["requires_adversarial"] = True
    return {
        "schema_version": 2,
        "workflow": detect_workflow(root),
        "status": "initialized",
        "acceptance_mode": mode,
        "round": 1,
        "max_auto_fix_rounds": ACCEPTANCE_MAX_AUTO_FIX_ROUNDS,
        "auto_fix_rounds": 0,
        "policy": (
            "P0-P2 trigger fixes and affected-unit re-review; P3-P4 are deferred; "
            "at most two automatic fix rounds; one final global integration review"
        ),
        "original_task_ids": [task.task_id for task in tasks],
        "original_task_digest": task_digest(tasks),
        "original_tasks_file_hash": acceptance_tasks_file_hash(root),
        "task_count": len(tasks),
        "review_units": units,
        "agents": [],
        "issues": [],
        "fixes": [],
        "deferred_issues": [],
        "affected_units": [],
        "created_at": now(),
        "updated_at": now(),
        "completed_at": None,
        "notes": [],
    }


def load_acceptance_state(specs_dir: str | Path) -> dict[str, object]:
    path = acceptance_path(specs_dir)
    if not path.is_file():
        raise SpecProgressError(f"{ACCEPTANCE_STATE_FILE} is missing; run acceptance-init first")
    data = read_json(path)
    schema_version = data.get("schema_version")
    if schema_version not in {1, 2}:
        raise SpecProgressError(f"Unsupported {ACCEPTANCE_STATE_FILE} schema_version: {data.get('schema_version')}")
    if schema_version == 1:
        # Keep unfinished 0.2.x runs strict: every unit still requires both
        # historical reviewers, while the new integration gate is added before
        # they may finish. Already accepted ledgers remain accepted.
        data["schema_version"] = 2
        data["legacy_schema_version"] = 1
        data.setdefault("acceptance_mode", "full")
        data.setdefault("max_auto_fix_rounds", ACCEPTANCE_MAX_AUTO_FIX_ROUNDS)
        historical_fix_rounds = {
            int(fix.get("round", 1)) for fix in data.get("fixes", [])
        }
        data.setdefault(
            "auto_fix_rounds",
            min(len(historical_fix_rounds), ACCEPTANCE_MAX_AUTO_FIX_ROUNDS),
        )
        data["policy"] = (
            "legacy full review; P0-P2 trigger fixes and affected-unit re-review; "
            "P3-P4 are deferred; one final global integration review"
        )
        for unit in data.get("review_units", []):
            unit.setdefault("high_risk", False)
            unit.setdefault("requires_adversarial", True)
            unit.setdefault("round_started", 1)
        bound_agents = {
            str(issue.get("agent_id")) for issue in data.get("issues", [])
            if issue.get("agent_id") not in {None, "", "n/a"}
        }
        for agent in data.get("agents", []):
            if (
                agent.get("result") == "ACTIONABLE_ISSUES"
                and str(agent.get("agent_id")) not in bound_agents
            ):
                agent["legacy_unbound_allowed"] = True
    mode = normalize_acceptance_mode(str(data.get("acceptance_mode", "full")))
    data["acceptance_mode"] = mode
    for unit in data.get("review_units", []):
        unit.setdefault("high_risk", False)
        unit.setdefault(
            "requires_adversarial",
            mode == "full" or bool(unit.get("high_risk")),
        )
        unit.setdefault("round_started", 1)
    data.setdefault("max_auto_fix_rounds", ACCEPTANCE_MAX_AUTO_FIX_ROUNDS)
    data.setdefault("auto_fix_rounds", 0)
    return data


def save_acceptance_state(specs_dir: str | Path, state: dict[str, object]) -> None:
    state["updated_at"] = now()
    write_json(acceptance_path(specs_dir), state)


def acceptance_summary(state: dict[str, object]) -> dict[str, object]:
    agents = list(state.get("agents", []))
    units = list(state.get("review_units", []))
    issues = list(state.get("issues", []))
    fixes = list(state.get("fixes", []))
    current_round = int(state.get("round", 1))
    round_agents = [
        agent for agent in agents
        if int(agent.get("round", 0)) == current_round
    ]
    mode = str(state.get("acceptance_mode", "full"))
    pending_units = [] if mode == "quick" else [
        unit["unit_id"] for unit in units if unit.get("status") != "pass"
    ]
    pending_agents = [
        agent["agent_id"] for agent in round_agents
        if agent.get("status") in {"planned", "running"}
    ]
    return {
        "status": state.get("status"),
        "acceptance_mode": mode,
        "round": current_round,
        "policy": state.get("policy"),
        "automatic_fix_rounds": {
            "used": int(state.get("auto_fix_rounds", 0)),
            "maximum": int(state.get("max_auto_fix_rounds", ACCEPTANCE_MAX_AUTO_FIX_ROUNDS)),
        },
        "task_count": state.get("task_count"),
        "units": len(units),
        "pending_units": pending_units,
        "agents": {
            "total": len(agents),
            "current_round": len(round_agents),
            "pending_or_running": pending_agents,
            "completed": [
                agent["agent_id"] for agent in round_agents
                if agent.get("status") == "completed"
            ],
        },
        "issues": {
            "total": len(issues),
            "open": [
                issue["issue_id"] for issue in issues
                if issue.get("status") in {"open", "planned"}
            ],
            "deferred": [
                issue.get("issue_id") for issue in state.get("deferred_issues", [])
            ],
        },
        "fixes": {
            "total": len(fixes),
            "pending": [
                fix["fix_id"] for fix in fixes
                if fix.get("status") in {"pending", "active"}
            ],
        },
        "affected_units": state.get("affected_units", []),
        "integration_review": integration_review_status(state),
    }


def validate_original_tasks_unchanged(specs_dir: str | Path, state: dict[str, object]) -> None:
    tasks = parse_tasks(specs_dir)
    current_ids = [task.task_id for task in tasks]
    original_ids = list(state.get("original_task_ids", []))
    if current_ids != original_ids:
        raise SpecProgressError(
            "Original tasks.md task IDs changed during acceptance; "
            "acceptance fixes must use acceptance-fixes.md instead of appending to tasks.md"
        )
    current_digest = task_digest(tasks)
    if current_digest != state.get("original_task_digest"):
        raise SpecProgressError(
            "Original tasks.md task text changed during acceptance; update specs and reapprove before final acceptance"
        )
    original_file_hash = state.get("original_tasks_file_hash")
    if original_file_hash and acceptance_tasks_file_hash(specs_dir) != original_file_hash:
        raise SpecProgressError(
            "Original tasks.md file changed during acceptance; use acceptance-fixes.md for repair work"
        )


def agent_id_for(round_number: int, role: str, unit_id: str) -> str:
    short_role = {"first_wave": "R", "adversarial": "A", "integration": "I"}[role]
    return f"round-{round_number}-{short_role}-{unit_id}"


def agent_payload(
    round_number: int,
    role: str,
    unit_id: str,
    task_ids: list[str],
) -> dict[str, object]:
    return {
        "agent_id": agent_id_for(round_number, role, unit_id),
        "round": round_number,
        "role": role,
        "unit_id": unit_id,
        "task_ids": task_ids,
        "status": "planned",
        "result": None,
        "started_at": None,
        "completed_at": None,
        "report": "",
    }


def planned_agents_for_units(
    round_number: int,
    units: list[dict[str, object]],
    acceptance_mode: str,
) -> list[dict[str, object]]:
    agents: list[dict[str, object]] = []
    if acceptance_mode == "quick":
        return agents
    for unit in units:
        roles = ["first_wave"]
        if acceptance_mode == "full" or unit.get("requires_adversarial"):
            roles.append("adversarial")
        for role in roles:
            agents.append(agent_payload(
                round_number,
                role,
                str(unit["unit_id"]),
                list(unit["task_ids"]),
            ))
    return agents


def issue_should_fix(issue: dict[str, object]) -> bool:
    if issue.get("status") in {"fixed", "deferred"}:
        return False
    return str(issue.get("severity", "")).upper() in ACCEPTANCE_BLOCKING_SEVERITIES


def find_unit(state: dict[str, object], unit_id: str) -> dict[str, object]:
    for unit in state.get("review_units", []):
        if unit.get("unit_id") == unit_id:
            return unit
    raise SpecProgressError(f"Review unit not found: {unit_id}")


def find_agent(state: dict[str, object], agent_id: str) -> dict[str, object]:
    for agent in state.get("agents", []):
        if agent.get("agent_id") == agent_id:
            return agent
    raise SpecProgressError(f"Acceptance agent not found: {agent_id}")


def find_issue(state: dict[str, object], issue_id: str) -> dict[str, object]:
    for issue in state.get("issues", []):
        if issue.get("issue_id") == issue_id:
            return issue
    raise SpecProgressError(f"Acceptance issue not found: {issue_id}")


def find_fix(state: dict[str, object], fix_id: str) -> dict[str, object]:
    for fix in state.get("fixes", []):
        if fix.get("fix_id") == fix_id:
            return fix
    raise SpecProgressError(f"Acceptance fix not found: {fix_id}")


def required_roles(state: dict[str, object], unit: dict[str, object]) -> list[str]:
    mode = str(state.get("acceptance_mode", "full"))
    if mode == "quick":
        return []
    roles = ["first_wave"]
    if mode == "full" or unit.get("requires_adversarial") or unit.get("high_risk"):
        roles.append("adversarial")
    return roles


def refresh_unit_status(state: dict[str, object], unit: dict[str, object]) -> None:
    status_fields = {
        "first_wave": "review_status",
        "adversarial": "adversarial_status",
    }
    statuses = [unit.get(status_fields[role], "pending") for role in required_roles(state, unit)]
    if statuses and all(status == "pass" for status in statuses):
        unit["status"] = "pass"
        unit["last_result"] = "PASS"
    elif "issues" in statuses:
        unit["status"] = "issues"
        unit["last_result"] = "ACTIONABLE_ISSUES"
    else:
        unit["status"] = "pending"
        unit["last_result"] = None


def ensure_adversarial_agent(state: dict[str, object], unit: dict[str, object]) -> bool:
    if state.get("acceptance_mode") == "quick":
        return False
    unit["requires_adversarial"] = True
    round_number = int(state.get("round", 1))
    exists = any(
        agent.get("role") == "adversarial"
        and agent.get("unit_id") == unit.get("unit_id")
        and int(agent.get("round", 0)) == round_number
        for agent in state.get("agents", [])
    )
    if exists:
        return False
    state.setdefault("agents", []).append(agent_payload(
        round_number,
        "adversarial",
        str(unit["unit_id"]),
        list(unit.get("task_ids", [])),
    ))
    return True


def current_round_agents(state: dict[str, object]) -> list[dict[str, object]]:
    round_number = int(state.get("round", 1))
    return [
        agent for agent in state.get("agents", [])
        if int(agent.get("round", 0)) == round_number
    ]


def current_integration_agent(state: dict[str, object]) -> dict[str, object] | None:
    return next(
        (
            agent for agent in reversed(list(state.get("agents", [])))
            if agent.get("role") == "integration"
            and int(agent.get("round", 0)) == int(state.get("round", 1))
        ),
        None,
    )


def integration_review_status(state: dict[str, object]) -> dict[str, object]:
    agent = current_integration_agent(state)
    if agent is None and state.get("status") == "accepted" and state.get("legacy_schema_version") == 1:
        return {"status": "pass", "agent_id": "legacy-accepted", "result": "PASS"}
    if agent is None:
        return {"status": "not-planned", "agent_id": None, "result": None}
    return {
        "status": agent.get("status"),
        "agent_id": agent.get("agent_id"),
        "result": agent.get("result"),
    }


def unbound_actionable_agents(state: dict[str, object]) -> list[dict[str, object]]:
    bound = {
        str(issue.get("agent_id"))
        for issue in state.get("issues", [])
        if issue.get("agent_id") not in {None, "", "n/a"}
    }
    return [
        agent for agent in state.get("agents", [])
        if agent.get("result") == "ACTIONABLE_ISSUES"
        and str(agent.get("agent_id")) not in bound
        and not agent.get("legacy_unbound_allowed")
    ]


def unresolved_acceptance_issues(state: dict[str, object]) -> list[dict[str, object]]:
    return [
        issue for issue in state.get("issues", [])
        if issue.get("status") not in {"fixed", "deferred"}
    ]


def pending_acceptance_fixes(state: dict[str, object]) -> list[dict[str, object]]:
    return [
        fix for fix in state.get("fixes", [])
        if fix.get("status") in {"pending", "active"}
    ]


def plan_integration_if_ready(state: dict[str, object]) -> bool:
    if state.get("status") in {"accepted", "blocked"} or current_integration_agent(state):
        return False
    if unbound_actionable_agents(state) or unresolved_acceptance_issues(state):
        return False
    if pending_acceptance_fixes(state) or state.get("affected_units"):
        return False
    if state.get("acceptance_mode") != "quick" and any(
        unit.get("status") != "pass" for unit in state.get("review_units", [])
    ):
        return False
    state.setdefault("agents", []).append(agent_payload(
        int(state.get("round", 1)),
        "integration",
        ACCEPTANCE_GLOBAL_UNIT,
        list(state.get("original_task_ids", [])),
    ))
    state["status"] = "integration-planned"
    return True


def unit_ids_for_tasks(state: dict[str, object], task_ids: list[str]) -> list[str]:
    selected = set(task_ids)
    return sorted(
        str(unit["unit_id"])
        for unit in state.get("review_units", [])
        if selected.intersection(unit.get("task_ids", []))
    )


def unit_review_failures(state: dict[str, object]) -> list[str]:
    failures: list[str] = []
    for unit in state.get("review_units", []):
        round_started = int(unit.get("round_started") or 1)
        for role in required_roles(state, unit):
            agent = next(
                (
                    item for item in reversed(list(state.get("agents", [])))
                    if item.get("unit_id") == unit.get("unit_id")
                    and item.get("role") == role
                    and int(item.get("round", 0)) == round_started
                ),
                None,
            )
            if not agent or agent.get("status") != "completed" or agent.get("result") != "PASS":
                failures.append(f"{unit.get('unit_id')}:{role}")
    return failures


def review_worktree_digest(specs_dir: str | Path) -> str:
    root = repo_root_for(specs_dir)
    ignored = progress_file_paths(specs_dir)
    entries: list[tuple[str, str]] = []
    for relative in sorted(dirty_paths(specs_dir)):
        normalized = relative.replace("\\", "/")
        if normalized in ignored:
            continue
        target = root / Path(normalized)
        if target.is_file():
            entries.append((normalized, hashlib.sha256(target.read_bytes()).hexdigest()))
        elif target.is_dir():
            for child in sorted(path for path in target.rglob("*") if path.is_file()):
                child_name = child.relative_to(root).as_posix()
                if child_name in ignored:
                    continue
                entries.append((child_name, hashlib.sha256(child.read_bytes()).hexdigest()))
        else:
            entries.append((normalized, "missing"))
    payload = json.dumps(entries, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def acceptance_review_snapshot(specs_dir: str | Path, state: dict[str, object]) -> dict[str, object]:
    root = specs_path(specs_dir)
    workflow = str(state.get("workflow") or detect_workflow(root))
    names = primary_artifacts(workflow) + ["tasks.md", "spec.yml"]
    snapshot: dict[str, object] = {
        "files": {name: sha256_text_file(root / name) for name in names},
    }
    if git_available(root):
        snapshot["git_commit"] = current_commit(root)
        snapshot["git_worktree_digest"] = review_worktree_digest(root)
    return snapshot


def acceptance_immutable_snapshot(specs_dir: str | Path, state: dict[str, object]) -> dict[str, object]:
    """Review snapshot excluding task/index metadata changed only by finalization."""
    snapshot = acceptance_review_snapshot(specs_dir, state)
    files = dict(snapshot.get("files", {}))
    files.pop("tasks.md", None)
    files.pop("spec.yml", None)
    snapshot["files"] = files
    return snapshot


def reset_integration_after_drift(
    specs_dir: str | Path,
    state: dict[str, object],
    agent: dict[str, object],
) -> None:
    agent.update({
        "status": "planned",
        "result": None,
        "started_at": None,
        "completed_at": None,
        "report": "",
    })
    agent.pop("review_snapshot", None)
    state.pop("finalization_snapshot", None)
    state.pop("accepted_snapshot", None)
    state["status"] = "integration-planned"
    note = "Reviewed inputs changed; a fresh global integration review is required"
    if note not in state.setdefault("notes", []):
        state["notes"].append(note)
    save_acceptance_state(specs_dir, state)


def create_acceptance_fixes_file(specs_dir: str | Path, state: dict[str, object]) -> None:
    source = (specs_path(specs_dir) / ACCEPTANCE_STATE_FILE).as_posix()
    fixes = list(state.get("fixes", []))
    rows: list[str] = []
    if fixes:
        for fix in fixes:
            rows.append(
                "| {fix_id} | {issue_ids} | {severity} | {unit_ids} | {status} | {evidence} |".format(
                    fix_id=markdown_table_cell(fix.get("fix_id", "")),
                    issue_ids=markdown_table_cell(", ".join(fix.get("issue_ids", []))),
                    severity=markdown_table_cell(fix.get("severity", "")),
                    unit_ids=markdown_table_cell(", ".join(fix.get("unit_ids", []))),
                    status=markdown_table_cell(fix.get("status", "")),
                    evidence=markdown_table_cell(fix.get("evidence", "pending")),
                )
            )
    else:
        rows.append("| - | - | - | - | - | - |")
    deferred = list(state.get("deferred_issues", []))
    deferred_rows = []
    if deferred:
        for issue in deferred:
            deferred_rows.append(
                "- {issue_id} ({severity}): {title} - {reason}".format(
                    issue_id=markdown_table_cell(issue.get("issue_id", "")),
                    severity=markdown_table_cell(issue.get("severity", "")),
                    title=markdown_table_cell(issue.get("title", "")),
                    reason=markdown_table_cell(issue.get("reason", "")),
                )
            )
    else:
        deferred_rows.append("- n/a")
    content = f"""# Acceptance Fixes

> **Source:** {source}
> **Round:** {state.get('round')}
> **Policy:** {state.get('policy')}
> **Original tasks:** {state.get('task_count')} frozen tasks; do not append acceptance fixes to tasks.md

## Fix Queue

| Fix ID | Issue IDs | Severity | Units | Status | Evidence |
|:---|:---|:---|:---|:---|:---|
{chr(10).join(rows)}

## Deferred Issues

{chr(10).join(deferred_rows)}
"""
    write_text(acceptance_fixes_path(specs_dir), content)


def git_output(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return GIT_UNAVAILABLE
    if result.returncode != 0:
        return GIT_UNAVAILABLE
    return result.stdout.rstrip("\r\n")


def git_available(specs_dir: str | Path) -> bool:
    """True when git is installed and specs_dir sits inside a work tree.

    dirty_paths returns [] both when nothing changed and when git is
    unavailable; callers that enforce safety (guard-commit, resume) use this to
    avoid treating "cannot tell" as "clean".
    """
    return git_output(["rev-parse", "--is-inside-work-tree"], specs_path(specs_dir)) == "true"


def repo_root_for(specs_dir: str | Path) -> Path:
    specs = specs_path(specs_dir)
    root = git_output(["rev-parse", "--show-toplevel"], specs)
    if root != GIT_UNAVAILABLE and root:
        return Path(root)
    return specs


def current_branch(specs_dir: str | Path) -> str:
    value = git_output(["rev-parse", "--abbrev-ref", "HEAD"], repo_root_for(specs_dir))
    return "n/a" if value == GIT_UNAVAILABLE or not value else value


def current_commit(specs_dir: str | Path) -> str:
    value = git_output(["rev-parse", "--short", "HEAD"], repo_root_for(specs_dir))
    return "n/a" if value == GIT_UNAVAILABLE or not value else value


def dirty_paths(specs_dir: str | Path, staged: bool = False) -> list[str]:
    root = repo_root_for(specs_dir)
    args = ["diff", "--cached", "--name-only"] if staged else ["status", "--porcelain"]
    output = git_output(args, root)
    if output == GIT_UNAVAILABLE:
        raise SpecProgressError("git is unavailable or failed; cannot inspect worktree state safely")
    if not output:
        return []
    paths: list[str] = []
    for line in output.splitlines():
        value = line[3:] if not staged and len(line) >= 3 else line
        if " -> " in value:
            value = value.rsplit(" -> ", 1)[1]
        value = value.strip()
        if not value:
            continue
        paths.append(value.replace("\\", "/"))
    return paths


def business_paths(paths: list[str]) -> list[str]:
    ignored_prefixes = {
        "docs/specs/",
        "docs/",
        "README",
        "plugins/spec-workflow/assets/templates/",
        "plugins/spec-workflow/skills/",
    }
    result = []
    for path in paths:
        if any(path.startswith(prefix) for prefix in ignored_prefixes):
            continue
        result.append(path)
    return result


def exclude_workflow_paths(paths: list[str], specs_dir: str | Path) -> list[str]:
    """Remove the current spec directory, including collapsed untracked parents."""
    root = repo_root_for(specs_dir)
    try:
        relative = specs_path(specs_dir).relative_to(root).as_posix().rstrip("/")
    except ValueError:
        return paths
    if relative in {"", "."}:
        ignored = progress_file_paths(specs_dir)
        return [path for path in paths if path.replace("\\", "/") not in ignored]
    prefix = relative + "/"
    result: list[str] = []
    for path in paths:
        normalized = path.replace("\\", "/").rstrip("/")
        if normalized == relative or normalized.startswith(prefix):
            continue
        if relative.startswith(normalized + "/"):
            continue
        result.append(path)
    return result


def _parse_bullet(line: str) -> tuple[str, str] | None:
    match = re.match(r"^-\s+(?P<key>[^:：]+?)\s*[:：]\s*(?P<value>.*)$", line)
    if not match:
        return None
    return match.group("key").strip(), match.group("value").strip()


def parse_progress(specs_dir: str | Path) -> Progress:
    path = specs_path(specs_dir) / "progress.md"
    if not path.is_file():
        return Progress()
    lines = read_text(path).splitlines()
    progress = Progress()
    section = ""
    for line in lines:
        top = TOP_FIELD_RE.match(line)
        if top:
            key = top.group("key").strip().lower().replace(" ", "_")
            value = top.group("value").strip()
            if key == "workflow":
                progress.workflow = value
            elif key == "mode":
                progress.mode = value
            elif key == "status":
                progress.status = value
            elif key == "current_task":
                progress.current_task = value
            elif key == "approval":
                progress.approval = value
            elif key == "last_checkpoint":
                progress.last_checkpoint = value
            elif key == "branch":
                progress.branch = value
            elif key == "last_known_commit":
                progress.last_known_commit = value
            continue
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section == "Resume Summary" and line.startswith("- "):
            parsed = _parse_bullet(line)
            if parsed:
                progress.resume_summary[parsed[0]] = parsed[1]
        elif section == "Active Task State" and line.startswith("- "):
            parsed = _parse_bullet(line)
            if parsed:
                progress.active_state[parsed[0]] = parsed[1]
        elif section == "Completed Work Log" and line.startswith("|"):
            if "---" not in line and "Task ID" not in line:
                progress.completed_rows.append(line)
        elif section == "Recovery Notes" and line.startswith("- "):
            progress.recovery_notes.append(line)
    return progress


def render_progress(
    specs_dir: str | Path,
    workflow: str,
    status: str,
    current_task: str,
    approval: str,
    active_status: str,
    verification: str,
    blockers: str,
    note: str,
    append_log: str | None = None,
    goal: str | None = None,
    files_expected: str | None = None,
) -> str:
    previous = parse_progress(specs_dir)
    rows = list(previous.completed_rows)
    if append_log:
        rows.append(append_log)
    if not rows:
        rows = ["| - | - | - | - | - |"]
    checkpoint = now()
    branch = current_branch(specs_dir)
    commit = current_commit(specs_dir)
    next_action = "Run spec_status, then continue the current task."
    if status == "Completed":
        next_action = "Run pre-acceptance, then final acceptance."
    elif status == "Blocked":
        next_action = "Resolve blocker or revise specs before coding."
    elif active_status == "interrupted":
        next_action = "Inspect diff and verification evidence before continuing."

    # Preserve carried-over state across writes; explicit args win, otherwise
    # reuse what the previous progress.md recorded so resume context survives.
    if goal is None:
        goal = previous.resume_summary.get("Goal", "n/a")
    if files_expected is None:
        files_expected = previous.active_state.get("Files expected to change", "n/a")
    verification_text = verification or previous.active_state.get("Verification needed", "") or "n/a"

    return f"""# Spec workflow Progress

> **Workflow:** {workflow}
> **Mode:** {previous.mode if previous.mode != 'unknown' else 'strict'}
> **Status:** {status}
> **Current Task:** {current_task}
> **Approval:** {approval}
> **Last Checkpoint:** {checkpoint}
> **Branch:** {branch}
> **Last Known Commit:** {commit}

## Resume Summary
- Goal: {goal or 'n/a'}
- Approved specs: {', '.join(primary_artifacts(workflow) + ['tasks.md'])}
- Current task: {current_task}
- Next safe action: {next_action}
- Blockers: {blockers or 'n/a'}

## Active Task State
- Task ID: {current_task}
- Status: {active_status}
- Started at: {checkpoint if active_status == 'active' else 'n/a'}
- Verification needed: {verification_text}
- Files expected to change: {files_expected or 'n/a'}

## Completed Work Log
| Task ID | Time | Commit/State | Verification | Notes |
|:---|:---|:---|:---|:---|
{chr(10).join(rows)}

## Recovery Notes
- {note or 'n/a'}
"""


def write_progress(
    specs_dir: str | Path,
    workflow: str,
    status: str,
    current_task: str,
    approval: str,
    active_status: str,
    verification: str = "",
    blockers: str = "",
    note: str = "",
    append_log: str | None = None,
    goal: str | None = None,
    files_expected: str | None = None,
) -> None:
    path = specs_path(specs_dir) / "progress.md"
    write_text(
        path,
        render_progress(
            specs_dir,
            workflow,
            status,
            current_task,
            approval,
            active_status,
            verification,
            blockers,
            note,
            append_log,
            goal=goal,
            files_expected=files_expected,
        ),
    )


def parse_flat_yml(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data: dict[str, str] = {}
    for line in read_text(path).splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        if not key:
            continue
        data[key] = value.strip()
    return data


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def sha256_text_file(path: Path) -> str:
    if not path.is_file():
        return "missing"
    data = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(data).hexdigest()[:12]


def sha256_text_variants(path: Path) -> set[str]:
    """Hashes for raw, LF, and CRLF forms of the same UTF-8 text."""
    if not path.is_file():
        return {"missing"}
    raw = path.read_bytes()
    normalized = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    crlf = normalized.replace(b"\n", b"\r\n")
    return {
        hashlib.sha256(content).hexdigest()[:12]
        for content in (raw, normalized, crlf)
    }


def extract_requirement_ids(specs_dir: str | Path, workflow: str) -> list[str]:
    root = specs_path(specs_dir)
    ids: set[str] = set()
    pattern = re.compile(r"\b(?:US|REQ|AC|NFR|BUG|FIX|SAFE)-\d+(?:\.\d+)?\b")
    for name in primary_artifacts(workflow):
        path = root / name
        if path.is_file():
            ids.update(pattern.findall(read_text(path)))
    return sorted(ids)


def task_graph_value(tasks: list[Task]) -> str:
    edges: list[str] = []
    for task in tasks:
        if not task.depends_on:
            edges.append(task.task_id)
        for dep in task.depends_on:
            edges.append(f"{dep}->{task.task_id}")
    return ", ".join(edges) if edges else "n/a"


def write_spec_index(
    specs_dir: str | Path,
    workflow: str,
    current_task: str,
    approval: str,
    mode: str | None = None,
    risk_level: str | None = None,
    preserve_hashes: bool = False,
    preserve_task_plan_hash: bool = False,
) -> None:
    root = specs_path(specs_dir)
    tasks = parse_tasks(root)
    existing = parse_flat_yml(root / "spec.yml")
    if mode is None:
        mode = existing.get("mode", "strict")
    if risk_level is None:
        risk_level = "high" if any(task.is_high_risk for task in tasks) else "low"
    artifact_names = primary_artifacts(workflow) + ["tasks.md", "progress.md", "spec.yml"]
    if preserve_hashes and existing.get("artifact_hashes"):
        hashes = existing["artifact_hashes"]
    else:
        hashes = ", ".join(
            f"{name}={sha256_text_file(root / name)}" for name in primary_artifacts(workflow)
        )
    if preserve_task_plan_hash:
        task_plan_hash = existing.get("task_plan_hash", "n/a")
    else:
        task_plan_hash = task_plan_digest(tasks)
    content = "\n".join(
        [
            "schema_version: 1",
            f"workflow: {workflow}",
            f"mode: {mode}",
            f"approval: {approval}",
            f"risk_level: {risk_level}",
            f"current_task: {current_task}",
            f"last_checkpoint: {now()}",
            f"artifacts: {', '.join(artifact_names)}",
            f"requirements: {', '.join(extract_requirement_ids(root, workflow)) or 'n/a'}",
            f"task_ids: {', '.join(task.task_id for task in tasks) or 'n/a'}",
            f"task_graph: {task_graph_value(tasks)}",
            f"artifact_hashes: {hashes or 'n/a'}",
            f"task_plan_hash: {task_plan_hash}",
        ]
    )
    write_text(root / "spec.yml", content)


def ensure_progress_files(specs_dir: str | Path, workflow: str) -> None:
    root = specs_path(specs_dir)
    tasks = parse_tasks(root)
    current = next_executable_tasks(tasks)
    current_task = current[0].task_id if current else "n/a"
    if not (root / "progress.md").is_file():
        write_progress(root, workflow, "Draft", current_task, "pending", "pending")
    if not (root / "spec.yml").is_file():
        write_spec_index(root, workflow, current_task, "pending")
    update_tasks_metadata(root, status="Draft", current_task=current_task)


def command_status(specs_dir: str | Path) -> dict[str, object]:
    workflow = detect_workflow(specs_dir)
    tasks = parse_tasks(specs_dir)
    stats = task_stats(tasks)
    ready = [task.task_id for task in next_executable_tasks(tasks)]
    progress = parse_progress(specs_dir)
    waves = execution_waves(tasks)
    sync = command_sync_check(specs_dir)
    return {
        "workflow": workflow,
        "progress_status": progress.status,
        "approval": progress.approval,
        "current_task": progress.current_task,
        "tasks": stats,
        "next_executable": ready,
        "execution_waves": waves,
        "freeze": {
            "ok": not sync["issues"],
            "issues": sync["issues"],
        },
    }


def command_approve(specs_dir: str | Path, evidence: str) -> str:
    if not evidence.strip():
        raise SpecProgressError("Approving specs requires evidence, such as the approval phrase/context")
    root = specs_path(specs_dir)
    workflow = detect_workflow(root)
    if workflow == "unknown":
        raise SpecProgressError("Cannot approve unknown workflow; required spec artifacts are missing")
    tasks = parse_tasks(root)
    current = next_executable_tasks(tasks)
    current_task = current[0].task_id if current else "n/a"
    update_tasks_metadata(root, status="Approved", current_task=current_task)
    write_progress(
        root,
        workflow,
        "Approved",
        current_task,
        "approved",
        "pending",
        verification=evidence,
        note=f"Approved specs and froze baseline: {evidence}",
    )
    write_spec_index(root, workflow, current_task, "approved")
    return f"Approved {workflow} specs; frozen baseline recorded"


def command_acceptance_init(
    specs_dir: str | Path,
    acceptance_mode: str | None = None,
) -> dict[str, object]:
    pre = command_pre_acceptance(specs_dir)
    if not pre["ok"]:
        raise SpecProgressError("Pre-acceptance must pass before final acceptance: " + "; ".join(pre["issues"]))
    path = acceptance_path(specs_dir)
    if path.is_file():
        state = load_acceptance_state(specs_dir)
        validate_original_tasks_unchanged(specs_dir, state)
        if acceptance_mode is not None:
            requested = normalize_acceptance_mode(acceptance_mode)
            if requested != state.get("acceptance_mode"):
                raise SpecProgressError(
                    "Acceptance mode is frozen after initialization: "
                    f"{state.get('acceptance_mode')} (requested {requested})"
                )
        return acceptance_summary(state)
    state = default_acceptance_state(
        specs_dir,
        normalize_acceptance_mode(acceptance_mode),
    )
    state["agents"] = planned_agents_for_units(
        int(state["round"]),
        list(state["review_units"]),
        str(state["acceptance_mode"]),
    )
    state["status"] = "agents-planned"
    plan_integration_if_ready(state)
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_status(specs_dir: str | Path) -> dict[str, object]:
    state = load_acceptance_state(specs_dir)
    validate_original_tasks_unchanged(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_start_agent(specs_dir: str | Path, agent_id: str) -> dict[str, object]:
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        raise SpecProgressError("Acceptance is already accepted and cannot be modified")
    validate_original_tasks_unchanged(specs_dir, state)
    agent = find_agent(state, agent_id)
    if int(agent.get("round", 0)) != int(state.get("round", 1)):
        raise SpecProgressError(f"Acceptance agent belongs to an earlier round: {agent_id}")
    if agent.get("status") != "planned":
        raise SpecProgressError(
            f"Acceptance agent must be planned before start: {agent_id} ({agent.get('status')})"
        )
    if agent.get("role") == "adversarial":
        primary = next(
            (
                item for item in state.get("agents", [])
                if item.get("unit_id") == agent.get("unit_id")
                and item.get("role") == "first_wave"
                and int(item.get("round", 0)) == int(agent.get("round", 0))
            ),
            None,
        )
        if not primary or primary.get("status") != "completed":
            raise SpecProgressError(
                f"Adversarial review must wait for first-wave completion: {agent_id}"
            )
    if agent.get("role") == "integration":
        pre = command_pre_acceptance(specs_dir)
        if not pre["ok"]:
            raise SpecProgressError(
                "Global integration review requires a clean pre-acceptance state: "
                + "; ".join(pre["issues"])
            )
        if (
            unbound_actionable_agents(state)
            or unresolved_acceptance_issues(state)
            or pending_acceptance_fixes(state)
            or state.get("affected_units")
            or unit_review_failures(state)
        ):
            raise SpecProgressError("Global integration review is not ready to start")
        agent["review_snapshot"] = acceptance_review_snapshot(specs_dir, state)
    agent["status"] = "running"
    agent["started_at"] = now()
    state["status"] = "integration-running" if agent.get("role") == "integration" else "agents-running"
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_complete_agent(
    specs_dir: str | Path,
    agent_id: str,
    result: str,
    report: str,
) -> dict[str, object]:
    normalized = result.upper()
    if normalized not in ACCEPTANCE_AGENT_RESULTS:
        raise SpecProgressError(
            f"result must be one of {', '.join(sorted(ACCEPTANCE_AGENT_RESULTS))}"
        )
    if not report.strip():
        raise SpecProgressError("Completing an acceptance agent requires a review report")
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        raise SpecProgressError("Acceptance is already accepted and cannot be modified")
    validate_original_tasks_unchanged(specs_dir, state)
    agent = find_agent(state, agent_id)
    if int(agent.get("round", 0)) != int(state.get("round", 1)):
        raise SpecProgressError(f"Acceptance agent belongs to an earlier round: {agent_id}")
    if agent.get("status") != "running":
        raise SpecProgressError(
            f"Acceptance agent must be running before completion: {agent_id} ({agent.get('status')})"
        )
    if normalized == "ACTIONABLE_ISSUES":
        blocking = [
            issue for issue in state.get("issues", [])
            if issue.get("agent_id") == agent_id
            and str(issue.get("severity", "")).upper() in ACCEPTANCE_BLOCKING_SEVERITIES
        ]
        if not blocking:
            raise SpecProgressError(
                "ACTIONABLE_ISSUES requires at least one recorded P0-P2 issue for this agent; "
                "record P3-P4 advisories and complete the agent with PASS"
            )
    if agent.get("role") == "integration" and normalized == "PASS":
        pre = command_pre_acceptance(specs_dir)
        if not pre["ok"]:
            raise SpecProgressError(
                "Global integration PASS requires a clean pre-acceptance state: "
                + "; ".join(pre["issues"])
            )
        if agent.get("review_snapshot") != acceptance_review_snapshot(specs_dir, state):
            reset_integration_after_drift(specs_dir, state, agent)
            raise SpecProgressError(
                "Reviewed inputs changed while the global integration review was running; "
                "start a fresh global integration review"
            )
    agent["status"] = "completed"
    agent["result"] = normalized
    agent["completed_at"] = now()
    agent["report"] = report.strip()
    if agent["role"] == "integration":
        if normalized == "PASS":
            state["status"] = "ready-to-finish"
        else:
            state["status"] = "review-complete"
    else:
        unit = find_unit(state, str(agent["unit_id"]))
        if agent["role"] == "first_wave":
            unit["review_status"] = "pass" if normalized == "PASS" else "issues"
            if normalized == "ACTIONABLE_ISSUES" and state.get("acceptance_mode") == "adaptive":
                ensure_adversarial_agent(state, unit)
        else:
            unit["adversarial_status"] = "pass" if normalized == "PASS" else "issues"
        refresh_unit_status(state, unit)

    current_agents = current_round_agents(state)
    if agent["role"] != "integration":
        if all(item.get("status") == "completed" for item in current_agents):
            state["status"] = "review-complete"
        plan_integration_if_ready(state)
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_record_issue(
    specs_dir: str | Path,
    unit_id: str,
    severity: str,
    title: str,
    evidence: str,
    task_ids: str = "",
    agent_id: str = "",
) -> dict[str, object]:
    normalized = severity.upper()
    if normalized not in ACCEPTANCE_SEVERITIES:
        raise SpecProgressError(f"severity must be one of {', '.join(sorted(ACCEPTANCE_SEVERITIES))}")
    if not title.strip() or not evidence.strip():
        raise SpecProgressError("Acceptance issue requires title and evidence")
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        raise SpecProgressError("Acceptance is already accepted and cannot be modified")
    validate_original_tasks_unchanged(specs_dir, state)
    unit = None if unit_id == ACCEPTANCE_GLOBAL_UNIT else find_unit(state, unit_id)
    integration = current_integration_agent(state)
    if integration and integration.get("status") == "planned":
        state["agents"].remove(integration)
        state["status"] = "review-complete"
    elif unit is not None and integration:
        raise SpecProgressError(
            "Record unit review issues before starting the global integration review"
        )
    if unit_id == ACCEPTANCE_GLOBAL_UNIT:
        allowed_tasks = set(state.get("original_task_ids", []))
    else:
        allowed_tasks = set(unit.get("task_ids", []))
    issue_number = len(state.get("issues", [])) + 1
    issue_id = f"I-{issue_number:03d}"
    if task_ids.strip():
        selected_tasks = [item.strip() for item in re.split(r"[,，、\s]+", task_ids) if item.strip()]
    else:
        selected_tasks = list(state.get("original_task_ids", [])) if unit is None else list(unit.get("task_ids", []))
    unknown = sorted(set(selected_tasks) - allowed_tasks, key=task_sort_key)
    if unknown:
        raise SpecProgressError(
            f"Issue task IDs must belong to {unit_id}; unexpected: {', '.join(unknown)}"
        )
    if agent_id:
        source_agent = find_agent(state, agent_id)
        if source_agent.get("status") not in {"running", "completed"}:
            raise SpecProgressError(f"Issue agent has not started: {agent_id}")
        if source_agent.get("unit_id") != unit_id:
            raise SpecProgressError(
                f"Issue unit {unit_id} does not match agent {agent_id} unit {source_agent.get('unit_id')}"
            )
        if int(source_agent.get("round", 0)) != int(state.get("round", 1)):
            raise SpecProgressError(f"Issue agent belongs to an earlier acceptance round: {agent_id}")
    affected_unit_ids = (
        unit_ids_for_tasks(state, selected_tasks)
        if unit is None
        else [unit_id]
    )
    if unit is None and not affected_unit_ids:
        affected_unit_ids = [str(item["unit_id"]) for item in state.get("review_units", [])]
    issue = {
        "issue_id": issue_id,
        "round": int(state.get("round", 1)),
        "unit_id": unit_id,
        "task_ids": selected_tasks,
        "severity": normalized,
        "title": title.strip(),
        "evidence": evidence.strip(),
        "agent_id": agent_id or "n/a",
        "affected_unit_ids": affected_unit_ids,
        "status": "open",
        "created_at": now(),
        "fix_id": None,
    }
    state.setdefault("issues", []).append(issue)
    if normalized in ACCEPTANCE_BLOCKING_SEVERITIES:
        if unit is not None:
            unit["status"] = "issues"
            unit["last_result"] = "ACTIONABLE_ISSUES"
            if state.get("acceptance_mode") == "adaptive":
                ensure_adversarial_agent(state, unit)
        affected = set(state.get("affected_units", []))
        affected.update(affected_unit_ids)
        state["affected_units"] = sorted(affected)
        state["status"] = "review-complete"
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_plan_fixes(specs_dir: str | Path) -> dict[str, object]:
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        raise SpecProgressError("Acceptance is already accepted and cannot be modified")
    validate_original_tasks_unchanged(specs_dir, state)
    round_number = int(state.get("round", 1))
    pending_agents = [
        agent for agent in current_round_agents(state)
        if agent.get("status") in {"planned", "running"}
        and (agent.get("role") != "integration" or agent.get("status") == "running")
    ]
    if pending_agents:
        raise SpecProgressError(
            "Complete the current review agents before planning fixes: "
            + ", ".join(str(agent["agent_id"]) for agent in pending_agents)
        )
    unbound = unbound_actionable_agents(state)
    if unbound:
        raise SpecProgressError(
            "ACTIONABLE_ISSUES results must be bound to recorded issue IDs: "
            + ", ".join(str(agent["agent_id"]) for agent in unbound)
        )
    existing_issue_ids = {
        issue_id
        for fix in state.get("fixes", [])
        for issue_id in fix.get("issue_ids", [])
    }
    deferred_ids = {issue.get("issue_id") for issue in state.get("deferred_issues", [])}
    to_fix: list[dict[str, object]] = []
    for issue in state.get("issues", []):
        issue_id = str(issue["issue_id"])
        if issue_id in existing_issue_ids or issue_id in deferred_ids:
            continue
        if issue_should_fix(issue):
            to_fix.append(issue)
        else:
            deferred = dict(issue)
            deferred["reason"] = "P3-P4 are advisory and are not auto-fixed"
            issue["status"] = "deferred"
            state.setdefault("deferred_issues", []).append(deferred)

    if to_fix and int(state.get("auto_fix_rounds", 0)) >= int(
        state.get("max_auto_fix_rounds", ACCEPTANCE_MAX_AUTO_FIX_ROUNDS)
    ):
        state["status"] = "blocked"
        state["decision_required"] = {
            "reason": "automatic-fix-limit",
            "issue_ids": [str(issue["issue_id"]) for issue in to_fix],
            "allowed_actions": [
                "repair outside automatic acceptance, update/reapprove specs if needed, then start a new acceptance ledger",
                "stop the workflow without accepting it",
            ],
        }
        note = (
            "Automatic acceptance fix limit reached with unresolved P0-P2 issues; "
            "human decision is required"
        )
        if note not in state.setdefault("notes", []):
            state["notes"].append(note)
    elif to_fix:
        for issue in to_fix:
            fix_id = f"F-{len(state.get('fixes', [])) + 1:03d}"
            fix = {
                "fix_id": fix_id,
                "round": round_number,
                "issue_ids": [issue["issue_id"]],
                "unit_ids": list(issue.get("affected_unit_ids") or [issue["unit_id"]]),
                "task_ids": list(issue.get("task_ids", [])),
                "severity": issue["severity"],
                "title": issue["title"],
                "status": "pending",
                "evidence": "pending",
                "created_at": now(),
                "completed_at": None,
            }
            issue["status"] = "planned"
            issue["fix_id"] = fix_id
            state.setdefault("fixes", []).append(fix)
        state["auto_fix_rounds"] = int(state.get("auto_fix_rounds", 0)) + 1
        state["status"] = "fixes-planned"
    elif pending_acceptance_fixes(state):
        state["status"] = "fixes-planned"
    else:
        plan_integration_if_ready(state)
    create_acceptance_fixes_file(specs_dir, state)
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_fix_start(specs_dir: str | Path, fix_id: str) -> dict[str, object]:
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        raise SpecProgressError("Acceptance is already accepted and cannot be modified")
    validate_original_tasks_unchanged(specs_dir, state)
    fix = find_fix(state, fix_id)
    if fix.get("status") == "done":
        raise SpecProgressError(f"Acceptance fix is already done: {fix_id}")
    fix["status"] = "active"
    fix["started_at"] = now()
    state["status"] = "fixes-running"
    create_acceptance_fixes_file(specs_dir, state)
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_fix_complete(
    specs_dir: str | Path,
    fix_id: str,
    evidence: str,
) -> dict[str, object]:
    if not evidence.strip():
        raise SpecProgressError("Completing an acceptance fix requires evidence")
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        raise SpecProgressError("Acceptance is already accepted and cannot be modified")
    validate_original_tasks_unchanged(specs_dir, state)
    fix = find_fix(state, fix_id)
    fix["status"] = "done"
    fix["evidence"] = evidence.strip()
    fix["completed_at"] = now()
    for issue_id in fix.get("issue_ids", []):
        issue = find_issue(state, issue_id)
        issue["status"] = "fixed"
    affected = set(state.get("affected_units", []))
    affected.update(fix.get("unit_ids", []))
    state["affected_units"] = sorted(affected)
    if not any(item.get("status") in {"pending", "active"} for item in state.get("fixes", [])):
        state["status"] = "fixes-complete"
    create_acceptance_fixes_file(specs_dir, state)
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def command_acceptance_next_round(specs_dir: str | Path) -> dict[str, object]:
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        raise SpecProgressError("Acceptance is already accepted and cannot be modified")
    validate_original_tasks_unchanged(specs_dir, state)
    if state.get("status") == "blocked":
        raise SpecProgressError(
            "Acceptance is blocked after the automatic-fix limit. Stop without accepting, "
            "or repair/reapprove as needed and archive this ledger before starting a new acceptance ledger."
        )
    pending_agents = [
        agent for agent in current_round_agents(state)
        if agent.get("status") in {"planned", "running"}
    ]
    if pending_agents:
        raise SpecProgressError("Current acceptance review agents are still pending")
    if unbound_actionable_agents(state):
        raise SpecProgressError("ACTIONABLE_ISSUES results are missing recorded issue IDs")
    if pending_acceptance_fixes(state):
        raise SpecProgressError("Pending acceptance fixes remain; complete or defer them before next round")
    unresolved = unresolved_acceptance_issues(state)
    if unresolved:
        raise SpecProgressError(
            "Unresolved acceptance issues remain: "
            + ", ".join(str(issue["issue_id"]) for issue in unresolved)
        )
    affected = list(state.get("affected_units", []))
    if not affected:
        integration = current_integration_agent(state)
        if integration and integration.get("result") == "PASS":
            state["status"] = "ready-to-finish"
        else:
            plan_integration_if_ready(state)
        save_acceptance_state(specs_dir, state)
        return acceptance_summary(state)
    round_number = int(state.get("round", 1))
    state["round"] = round_number + 1
    state["affected_units"] = []
    if state.get("acceptance_mode") == "quick":
        state["status"] = "integration-planned"
        plan_integration_if_ready(state)
    else:
        for unit in state.get("review_units", []):
            if unit.get("unit_id") in affected:
                unit["status"] = "pending"
                unit["review_status"] = "pending"
                unit["adversarial_status"] = "pending"
                unit["round_started"] = state["round"]
        review_units = [
            unit for unit in state.get("review_units", [])
            if unit.get("unit_id") in affected
        ]
        state.setdefault("agents", []).extend(planned_agents_for_units(
            int(state["round"]),
            review_units,
            str(state.get("acceptance_mode", "full")),
        ))
        state["status"] = "agents-planned"
    save_acceptance_state(specs_dir, state)
    return acceptance_summary(state)


def write_acceptance_terminal_files(specs_dir: str | Path, workflow: str) -> None:
    write_progress(
        specs_dir,
        workflow,
        "Accepted",
        "n/a",
        "approved",
        "done",
        verification=f"Final acceptance passed through {ACCEPTANCE_STATE_FILE}",
        note="Final acceptance accepted",
    )
    update_tasks_metadata(specs_dir, status="Accepted", current_task="n/a")
    write_spec_index(
        specs_dir,
        workflow,
        "n/a",
        "approved",
        preserve_hashes=True,
        preserve_task_plan_hash=True,
    )


def command_acceptance_finish(specs_dir: str | Path) -> dict[str, object]:
    state = load_acceptance_state(specs_dir)
    if state.get("status") == "accepted":
        workflow = str(state.get("workflow") or detect_workflow(specs_dir))
        write_acceptance_terminal_files(specs_dir, workflow)
        validate_original_tasks_unchanged(specs_dir, state)
        pre = command_pre_acceptance(specs_dir)
        if not pre["ok"]:
            raise SpecProgressError(
                "Accepted ledger could not reconcile terminal files: " + "; ".join(pre["issues"])
            )
        state.setdefault("completed_at", now())
        save_acceptance_state(specs_dir, state)
        return acceptance_summary(state)
    validate_original_tasks_unchanged(specs_dir, state)
    pre = command_pre_acceptance(specs_dir)
    if not pre["ok"]:
        raise SpecProgressError(
            "Acceptance cannot finish; pre-acceptance failed: " + "; ".join(pre["issues"])
        )
    unresolved = unresolved_acceptance_issues(state)
    pending_agents = [
        agent for agent in state.get("agents", [])
        if agent.get("status") in {"planned", "running"}
    ]
    pending_fixes = pending_acceptance_fixes(state)
    unbound = unbound_actionable_agents(state)
    unit_failures = unit_review_failures(state)
    integration = current_integration_agent(state)
    integration_ok = bool(
        integration
        and integration.get("status") == "completed"
        and integration.get("result") == "PASS"
    )
    if state.get("status") == "finalizing":
        snapshot_changed = bool(
            integration_ok
            and state.get("finalization_snapshot")
            != acceptance_immutable_snapshot(specs_dir, state)
        )
    else:
        snapshot_changed = bool(
            integration_ok
            and integration.get("review_snapshot") != acceptance_review_snapshot(specs_dir, state)
        )
    if snapshot_changed and integration is not None:
        reset_integration_after_drift(specs_dir, state, integration)
        raise SpecProgressError(
            "Acceptance cannot finish; reviewed artifacts changed after global integration review; "
            "start a fresh global integration review"
        )
    if (
        pending_agents
        or pending_fixes
        or unresolved
        or unbound
        or unit_failures
        or not integration_ok
        or state.get("affected_units")
    ):
        details = []
        if pending_agents:
            details.append("pending agents: " + ", ".join(agent["agent_id"] for agent in pending_agents))
        if pending_fixes:
            details.append("pending fixes: " + ", ".join(fix["fix_id"] for fix in pending_fixes))
        if unresolved:
            details.append("unresolved issues: " + ", ".join(issue["issue_id"] for issue in unresolved))
        if unbound:
            details.append("unbound ACTIONABLE_ISSUES: " + ", ".join(agent["agent_id"] for agent in unbound))
        if unit_failures:
            details.append("required unit reviews not passing: " + ", ".join(unit_failures))
        if not integration_ok:
            details.append("global integration review has not passed")
        if state.get("affected_units"):
            details.append("affected units still require re-review: " + ", ".join(state["affected_units"]))
        raise SpecProgressError("Acceptance cannot finish; " + "; ".join(details))
    workflow = str(state.get("workflow") or detect_workflow(specs_dir))
    if state.get("status") != "finalizing":
        state["status"] = "finalizing"
        state["finalization_snapshot"] = acceptance_immutable_snapshot(specs_dir, state)
        save_acceptance_state(specs_dir, state)

    write_acceptance_terminal_files(specs_dir, workflow)
    validate_original_tasks_unchanged(specs_dir, state)
    post = command_pre_acceptance(specs_dir)
    if not post["ok"]:
        raise SpecProgressError(
            "Acceptance finalization could not verify terminal files: " + "; ".join(post["issues"])
        )
    post_snapshot = acceptance_immutable_snapshot(specs_dir, state)
    if post_snapshot != state.get("finalization_snapshot"):
        if integration is not None:
            reset_integration_after_drift(specs_dir, state, integration)
        raise SpecProgressError(
            "Acceptance finalization detected reviewed input drift; start a fresh global integration review"
        )
    state["status"] = "accepted"
    state["completed_at"] = now()
    state["accepted_snapshot"] = post_snapshot
    state.pop("finalization_snapshot", None)
    save_acceptance_state(specs_dir, state)
    if post_snapshot != acceptance_immutable_snapshot(specs_dir, state):
        if integration is not None:
            reset_integration_after_drift(specs_dir, state, integration)
        raise SpecProgressError(
            "Acceptance finalization detected reviewed input drift; start a fresh global integration review"
        )
    return acceptance_summary(state)


def assert_can_start(specs_dir: str | Path, task_id: str) -> Task:
    tasks = parse_tasks(specs_dir)
    task = next((candidate for candidate in tasks if candidate.task_id == task_id), None)
    if not task:
        raise SpecProgressError(f"Task not found: {task_id}")
    if task.state != "pending":
        raise SpecProgressError(f"Task {task_id} is not pending (state: {task.state})")
    done = completed_ids(tasks)
    missing = [dep for dep in task.depends_on if dep not in done]
    if missing:
        raise SpecProgressError(f"Task {task_id} has unmet dependencies: {', '.join(missing)}")
    ready = [candidate.task_id for candidate in next_executable_tasks(tasks)]
    if ready and task_id not in ready:
        raise SpecProgressError(
            f"Task {task_id} is not in the next executable wave: {', '.join(ready)}"
        )
    return task


def command_start(specs_dir: str | Path, task_id: str) -> str:
    assert_approved_baseline(specs_dir)
    workflow = detect_workflow(specs_dir)
    task = assert_can_start(specs_dir, task_id)
    update_task_fields(specs_dir, task_id, None, {"status": "active"})
    update_tasks_metadata(specs_dir, status="In Progress", current_task=task_id)
    write_progress(
        specs_dir,
        workflow,
        "In Progress",
        task_id,
        "approved",
        "active",
        verification=task.fields.get("verify", ""),
        note=f"Started {task_id}",
        goal=task.title,
        files_expected=task.fields.get("files", "") or "n/a",
    )
    write_spec_index(specs_dir, workflow, task_id, "approved", preserve_hashes=True, preserve_task_plan_hash=True)
    return f"Started {task_id}"


def command_complete(specs_dir: str | Path, task_id: str, evidence: str, notes: str = "") -> str:
    if not evidence.strip():
        raise SpecProgressError("Completion requires verification evidence")
    assert_approved_baseline(specs_dir)
    workflow = detect_workflow(specs_dir)
    task = get_task(specs_dir, task_id)
    if task.state not in {"pending", "active", "interrupted"}:
        raise SpecProgressError(f"Task {task_id} cannot be completed from state {task.state}")
    update_task_fields(
        specs_dir,
        task_id,
        "x",
        {
            "status": "done",
            "evidence": evidence,
            "completed_at": now(),
            "notes": notes or "n/a",
        },
    )
    tasks = parse_tasks(specs_dir)
    remaining = [candidate for candidate in tasks if candidate.state in {"pending", "active", "blocked", "interrupted"}]
    status = "Completed" if not remaining else "In Progress"
    next_task = next_executable_tasks(tasks)
    current = "n/a" if status == "Completed" else (next_task[0].task_id if next_task else "n/a")
    commit = current_commit(specs_dir)
    log_row = (
        f"| {markdown_table_cell(task_id)} | {markdown_table_cell(now())} | "
        f"{markdown_table_cell(commit)} | {markdown_table_cell(evidence)} | "
        f"{markdown_table_cell(notes or 'n/a')} |"
    )
    update_tasks_metadata(specs_dir, status=status, current_task=current, log_row=log_row)
    write_progress(
        specs_dir,
        workflow,
        status,
        current,
        "approved",
        "done" if status == "Completed" else "pending",
        verification=evidence,
        note=f"Completed {task_id}",
        append_log=log_row,
    )
    write_spec_index(specs_dir, workflow, current, "approved", preserve_hashes=True, preserve_task_plan_hash=True)
    return f"Completed {task_id}; workflow status: {status}"


def command_block(specs_dir: str | Path, task_id: str, reason: str) -> str:
    if not reason.strip():
        raise SpecProgressError("Blocking a task requires a reason")
    assert_approved_baseline(specs_dir)
    workflow = detect_workflow(specs_dir)
    update_task_fields(specs_dir, task_id, None, {"status": "blocked", "blocker": reason})
    update_tasks_metadata(specs_dir, status="Blocked", current_task=task_id)
    write_progress(
        specs_dir,
        workflow,
        "Blocked",
        task_id,
        "approved",
        "blocked",
        blockers=reason,
        note=f"Blocked {task_id}",
    )
    write_spec_index(specs_dir, workflow, task_id, "approved", preserve_hashes=True, preserve_task_plan_hash=True)
    return f"Blocked {task_id}"


def command_skip(specs_dir: str | Path, task_id: str, approval: str) -> str:
    if not approval.strip():
        raise SpecProgressError("Skipping a task requires explicit human approval evidence")
    assert_approved_baseline(specs_dir)
    workflow = detect_workflow(specs_dir)
    update_task_fields(
        specs_dir,
        task_id,
        "~",
        {"status": "skipped", "evidence": approval, "completed_at": now()},
    )
    tasks = parse_tasks(specs_dir)
    remaining = [task for task in tasks if task.state in {"pending", "active", "blocked", "interrupted"}]
    status = "Completed" if not remaining else "In Progress"
    next_task = next_executable_tasks(tasks)
    current = "n/a" if status == "Completed" else (next_task[0].task_id if next_task else "n/a")
    log_row = (
        f"| {markdown_table_cell(task_id)} | {markdown_table_cell(now())} | skipped | "
        f"{markdown_table_cell(approval)} | human-approved skip |"
    )
    update_tasks_metadata(specs_dir, status=status, current_task=current, log_row=log_row)
    write_progress(
        specs_dir,
        workflow,
        status,
        current,
        "approved",
        "skipped",
        verification=approval,
        note=f"Skipped {task_id}",
        append_log=log_row,
    )
    write_spec_index(specs_dir, workflow, current, "approved", preserve_hashes=True, preserve_task_plan_hash=True)
    return f"Skipped {task_id}; workflow status: {status}"


def command_resume(specs_dir: str | Path) -> dict[str, object]:
    workflow = detect_workflow(specs_dir)
    root = specs_path(specs_dir)
    issues: list[str] = []
    warnings: list[str] = []
    try:
        tasks = parse_tasks(root)
    except SpecProgressError as exc:
        # tasks.md missing or unreadable: report instead of crashing so the
        # caller still gets a structured, actionable resume payload.
        return {
            "workflow": workflow,
            "status": "blocked",
            "issues": [str(exc)],
            "warnings": [],
            "current_task": "n/a",
            "next_executable": [],
        }
    progress = parse_progress(root)
    index = parse_flat_yml(root / "spec.yml")
    if not (root / "progress.md").is_file():
        issues.append("progress.md is missing")
    if not (root / "spec.yml").is_file():
        issues.append("spec.yml is missing")
    if progress.workflow not in {"unknown", workflow}:
        issues.append(f"progress.md workflow {progress.workflow} does not match {workflow}")
    if index.get("workflow") and index.get("workflow") != workflow:
        issues.append(f"spec.yml workflow {index.get('workflow')} does not match {workflow}")
    task_ids = {task.task_id for task in tasks}
    if progress.current_task not in task_ids and progress.current_task != "n/a":
        issues.append(f"progress.md current task does not exist: {progress.current_task}")
    if index.get("current_task") not in task_ids and index.get("current_task") not in {None, "n/a"}:
        issues.append(f"spec.yml current task does not exist: {index.get('current_task')}")
    freeze = command_sync_check(root)
    if progress.approval == "approved" or index.get("approval") == "approved":
        issues.extend(freeze["issues"])
    active = [task for task in tasks if task.state == "active"]
    interrupted = False
    git_ok = git_available(root)
    if not git_ok:
        warnings.append("git is unavailable; cannot detect dirty business-code changes")
    dirty_business: list[str] = []
    if active and git_ok:
        try:
            dirty_business = business_paths(dirty_paths(root))
        except SpecProgressError as exc:
            warnings.append(str(exc))
    if active and dirty_business:
        interrupted = True
        warnings.append(
            f"active task {active[0].task_id} has dirty business-code changes; treat as interrupted"
        )
    status = "interrupted" if interrupted else ("blocked" if any(task.state == "blocked" for task in tasks) else "ready")
    return {
        "workflow": workflow,
        "status": status,
        "issues": issues,
        "warnings": warnings,
        "current_task": progress.current_task,
        "next_executable": [task.task_id for task in next_executable_tasks(tasks)],
        "freeze": {
            "ok": not freeze["issues"],
            "issues": freeze["issues"],
        },
    }


def parse_hashes(value: str) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for item in value.split(","):
        if "=" not in item:
            continue
        key, digest = item.split("=", 1)
        hashes[key.strip()] = digest.strip()
    return hashes


def command_sync_check(specs_dir: str | Path, write: bool = False) -> dict[str, object]:
    root = specs_path(specs_dir)
    workflow = detect_workflow(root)
    index = parse_flat_yml(root / "spec.yml")
    progress = parse_progress(root)
    issues: list[str] = []
    suggestions: list[str] = []
    tasks = parse_tasks(root)
    task_ids = ", ".join(task.task_id for task in tasks) or "n/a"
    if index.get("task_ids") and index.get("task_ids") != task_ids:
        issues.append("spec.yml task_ids drift from tasks.md")
    old_hashes = parse_hashes(index.get("artifact_hashes", ""))
    have_baseline = bool(old_hashes)
    for artifact in primary_artifacts(workflow):
        old = old_hashes.get(artifact)
        normalized = sha256_text_file(root / artifact)
        new = normalized
        if new == "missing":
            issues.append(f"{artifact} is missing but referenced by the spec index")
        elif old is not None and old not in sha256_text_variants(root / artifact):
            issues.append(f"{artifact} changed since last approved index")
        elif old is None and have_baseline:
            # Baseline exists but this artifact was never hashed: a newly added
            # spec file that bypassed reapproval.
            issues.append(f"{artifact} is new and missing from the approved index")
    old_task_plan_hash = index.get("task_plan_hash", "")
    if old_task_plan_hash:
        new_task_plan_hash = task_plan_digest(tasks)
        if old_task_plan_hash != new_task_plan_hash:
            issues.append("tasks.md plan changed since last approved index")
    elif index.get("approval") == "approved" or progress.approval == "approved":
        issues.append("task_plan_hash is missing from the approved index")
    if issues:
        suggestions.append("Review spec changes, rebuild tasks if needed, then request reapproval.")
        if write:
            current_task = index.get("current_task", "n/a")
            write_spec_index(
                root,
                workflow,
                current_task,
                "reapproval-required",
                mode=index.get("mode", "strict"),
                risk_level=index.get("risk_level", "medium"),
                preserve_hashes=True,
                preserve_task_plan_hash=True,
            )
            write_progress(
                root,
                workflow,
                "Blocked",
                current_task,
                "reapproval-required",
                "blocked",
                blockers="; ".join(issues),
                note="Spec baseline drift detected; reapproval required",
            )
    return {"issues": issues, "suggestions": suggestions}


def assert_approved_baseline(specs_dir: str | Path) -> None:
    root = specs_path(specs_dir)
    progress = parse_progress(root)
    index = parse_flat_yml(root / "spec.yml")
    approval = progress.approval if progress.approval != "pending" else index.get("approval", "pending")
    if approval != "approved":
        raise SpecProgressError(
            "Spec artifacts are not approved. Run "
            'python <plugin-root>/scripts/spec_progress.py approve <specs_dir> --evidence "<approval phrase/context>" '
            "after human approval before implementation."
        )
    sync = command_sync_check(root)
    if sync["issues"]:
        raise SpecProgressError(
            "Approved spec baseline drift detected: "
            + "; ".join(str(issue) for issue in sync["issues"])
            + ". Stop implementation, run sync-check --write to mark reapproval-required, "
            "then obtain a new approval before continuing."
        )


def progress_file_paths(specs_dir: str | Path) -> set[str]:
    """Progress files as paths relative to the repo root (forward slashes).

    Derived from the supplied specs_dir so the guard works regardless of where
    the specs live, instead of assuming docs/specs/.
    """
    root = repo_root_for(specs_dir)
    specs = specs_path(specs_dir)
    paths: set[str] = set()
    for name in ("tasks.md", "progress.md", "spec.yml", ACCEPTANCE_STATE_FILE, ACCEPTANCE_FIXES_FILE):
        target = specs / name
        try:
            relative = target.relative_to(root)
        except ValueError:
            relative = Path(name)
        paths.add(relative.as_posix())
    return paths


def command_guard_commit(specs_dir: str | Path) -> dict[str, object]:
    if not git_available(specs_dir):
        return {
            "ok": False,
            "message": "git is unavailable or specs_dir is not inside a git worktree; refusing to pass commit guard",
            "business_paths": [],
        }
    try:
        paths = dirty_paths(specs_dir, staged=True)
    except SpecProgressError as exc:
        return {"ok": False, "message": str(exc), "business_paths": []}
    business = business_paths(paths)
    progress_paths = progress_file_paths(specs_dir)
    progress_changed = any(path.replace("\\", "/") in progress_paths for path in paths)
    hint = ", ".join(sorted(progress_paths))
    if business and not progress_changed:
        return {
            "ok": False,
            "message": (
                "Business-code changes are staged while spec progress files are unchanged. "
                f"Update {hint} before committing."
            ),
            "business_paths": business,
        }
    return {"ok": True, "message": "Spec progress guard passed", "business_paths": business}


def command_guard_all(specs_root: str | Path = DEFAULT_SPECS_ROOT) -> dict[str, object]:
    if not git_available(specs_root):
        return {
            "ok": False,
            "specs_root": specs_path(specs_root).as_posix(),
            "checked": [],
            "failures": [
                {
                    "specs_dir": specs_path(specs_root).as_posix(),
                    "ok": False,
                    "message": "git is unavailable or specs_root is not inside a git worktree; refusing to pass commit guard",
                    "business_paths": [],
                }
            ],
            "message": "git is unavailable or specs_root is not inside a git worktree; refusing to pass commit guard",
        }
    discovery = command_discover(specs_root)
    checks: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for workflow in discovery["open_workflows"]:
        specs_dir = workflow["specs_dir"]
        result = command_guard_commit(specs_dir)
        record = {
            "specs_dir": specs_dir,
            "ok": result["ok"],
            "message": result["message"],
            "business_paths": result["business_paths"],
        }
        checks.append(record)
        if not result["ok"]:
            failures.append(record)
    return {
        "ok": not failures,
        "specs_root": discovery["specs_root"],
        "checked": checks,
        "failures": failures,
        "message": (
            "Spec progress guard passed for all open workflows"
            if not failures
            else "One or more open workflows require a progress update before committing"
        ),
    }


def command_pre_acceptance(specs_dir: str | Path) -> dict[str, object]:
    tasks = parse_tasks(specs_dir)
    resume = command_resume(specs_dir)
    issues = list(resume["issues"])
    warnings = list(resume.get("warnings", []))
    progress = parse_progress(specs_dir)
    index = parse_flat_yml(specs_path(specs_dir) / "spec.yml")
    approval = progress.approval if progress.approval != "pending" else index.get("approval", "pending")
    if approval != "approved":
        issues.append("Spec artifacts are not approved")
    unchecked = [task.task_id for task in tasks if task.state in {"pending", "active", "blocked", "interrupted"}]
    missing_evidence = [
        task.task_id
        for task in tasks
        if task.state in {"done", "skipped"} and not task.fields.get("evidence")
    ]
    if unchecked:
        issues.append(f"Unchecked or unresolved tasks remain: {', '.join(unchecked)}")
    if missing_evidence:
        issues.append(f"Completed/skipped tasks missing evidence: {', '.join(missing_evidence)}")
    try:
        dirty_business = (
            business_paths(exclude_workflow_paths(dirty_paths(specs_dir), specs_dir))
            if git_available(specs_dir)
            else []
        )
    except SpecProgressError as exc:
        dirty_business = []
        warnings.append(str(exc))
    if dirty_business:
        issues.append("Dirty business-code changes remain in the worktree")
    return {
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "message": (
            "Pre-acceptance passed; adaptive final acceptance is still required."
            if not issues
            else "Pre-acceptance found issues; final acceptance must not start yet."
        ),
    }


def workflow_dirs(specs_root: str | Path) -> list[Path]:
    root = specs_path(specs_root)
    if not root.exists():
        return []
    dirs: list[Path] = []
    if (root / "tasks.md").is_file():
        dirs.append(root)
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "tasks.md").is_file():
            dirs.append(child)
    return dirs


def workflow_complete(specs_dir: Path, progress: Progress) -> bool:
    if (
        progress.status != "Accepted"
        or progress.current_task != "n/a"
        or progress.approval != "approved"
    ):
        return False
    try:
        state = load_acceptance_state(specs_dir)
    except SpecProgressError:
        return False
    if state.get("status") != "accepted":
        return False
    index = parse_flat_yml(specs_dir / "spec.yml")
    if index.get("current_task") != "n/a" or index.get("approval") != "approved":
        return False
    top: dict[str, str] = {}
    for line in read_text(specs_dir / "tasks.md").splitlines():
        if TASK_RE.match(line):
            break
        match = TASK_TOP_FIELD_RE.match(line)
        if match:
            top[match.group("label").strip().lower()] = match.group("value").strip()
    task_status = top.get("状态", top.get("status"))
    current_task = top.get("当前任务", top.get("current task"))
    return (
        task_status in {None, "Accepted"}
        and current_task in {None, "n/a"}
    )


def workflow_record(specs_dir: Path, specs_root: Path) -> dict[str, object]:
    workflow = detect_workflow(specs_dir)
    progress = parse_progress(specs_dir)
    tasks = parse_tasks(specs_dir)
    stats = task_stats(tasks)
    complete = workflow_complete(specs_dir, progress)
    accepted = complete
    try:
        resume = command_resume(specs_dir)
        resume_status = str(resume.get("status", "unknown"))
        warnings = list(resume.get("warnings", []))
        issues = list(resume.get("issues", []))
    except SpecProgressError as exc:
        resume_status = "blocked"
        warnings = []
        issues = [str(exc)]
    try:
        relative = specs_dir.relative_to(Path.cwd()).as_posix()
    except ValueError:
        relative = specs_dir.as_posix()
    try:
        root_relative = specs_dir.relative_to(specs_root).as_posix()
    except ValueError:
        root_relative = "."
    return {
        "specs_dir": relative,
        "run_id": "legacy-root" if specs_dir == specs_root else root_relative,
        "workflow": workflow,
        "status": progress.status,
        "approval": progress.approval,
        "current_task": progress.current_task,
        "resume_status": resume_status,
        "completed": complete,
        "accepted": accepted,
        "open": not complete,
        "tasks": stats,
        "next_executable": [task.task_id for task in next_executable_tasks(tasks)],
        "issues": issues,
        "warnings": warnings,
    }


def command_discover(specs_root: str | Path = DEFAULT_SPECS_ROOT) -> dict[str, object]:
    root = specs_path(specs_root)
    workflows: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    for directory in workflow_dirs(root):
        try:
            workflows.append(workflow_record(directory, root))
        except SpecProgressError as exc:
            try:
                relative = directory.relative_to(Path.cwd()).as_posix()
            except ValueError:
                relative = directory.as_posix()
            errors.append({"specs_dir": relative, "error": str(exc)})
    open_workflows = [item for item in workflows if item["open"]]
    return {
        "specs_root": root.as_posix(),
        "workflow_count": len(workflows),
        "open_count": len(open_workflows),
        "open_workflows": open_workflows,
        "workflows": workflows,
        "errors": errors,
        "next_action": (
            "choose-existing-or-create-new"
            if open_workflows
            else "create-new-workflow"
        ),
    }


def command_new_workflow(
    specs_root: str | Path = DEFAULT_SPECS_ROOT,
    slug: str | None = None,
    run_id: str | None = None,
) -> dict[str, object]:
    root = specs_path(specs_root)
    root.mkdir(parents=True, exist_ok=True)
    if run_id:
        # Caller pre-decided the run id (e.g. to name a spec/<run-id> worktree
        # and branch before `new` runs). Use it verbatim; do not auto-suffix,
        # because the branch already commits to this exact id.
        base_name = sanitize_slug(run_id)
        candidate = root / base_name
        if candidate.exists():
            raise SpecProgressError(
                f"run id '{base_name}' already exists at {candidate.as_posix()}; "
                "choose a different --run-id or resume the existing workflow"
            )
        candidate.mkdir(parents=True)
    else:
        base_name = f"{run_id_timestamp()}-{sanitize_slug(slug)}"
        candidate = root / base_name
        suffix = 2
        while candidate.exists():
            candidate = root / f"{base_name}-{suffix}"
            suffix += 1
        candidate.mkdir(parents=True)
    try:
        relative = candidate.relative_to(Path.cwd()).as_posix()
    except ValueError:
        relative = candidate.as_posix()
    return {
        "specs_dir": relative,
        "run_id": candidate.name,
        "created": True,
        "message": (
            f"Created isolated Spec workflow directory: {relative}. "
            "Generate this workflow's artifacts inside that directory and pass it as <specs_dir>."
        ),
    }


def format_json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Spec workflow progress and resume utilities")
    sub = parser.add_subparsers(dest="command", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("specs_root", nargs="?", default=str(DEFAULT_SPECS_ROOT))
    new = sub.add_parser("new")
    new.add_argument("specs_root", nargs="?", default=str(DEFAULT_SPECS_ROOT))
    new.add_argument("--slug", default="spec-workflow")
    new.add_argument(
        "--run-id",
        default=None,
        help="Use this exact run id as the workflow directory name instead of "
        "generating <timestamp>-<slug>. Lets a spec/<run-id> worktree be created "
        "before `new` runs.",
    )
    guard_all = sub.add_parser("guard-all")
    guard_all.add_argument("specs_root", nargs="?", default=str(DEFAULT_SPECS_ROOT))
    for name in (
        "status",
        "resume",
        "waves",
        "sync-check",
        "guard-commit",
        "pre-acceptance",
        "init",
        "acceptance-status",
        "acceptance-plan-fixes",
        "acceptance-next-round",
        "acceptance-finish",
    ):
        cmd = sub.add_parser(name)
        cmd.add_argument("specs_dir")
    acceptance_init = sub.add_parser("acceptance-init")
    acceptance_init.add_argument("specs_dir")
    acceptance_init.add_argument(
        "--mode",
        choices=sorted(ACCEPTANCE_MODES),
        default=None,
        help="Acceptance policy: quick, adaptive (default), or full",
    )
    start = sub.add_parser("start")
    start.add_argument("specs_dir")
    start.add_argument("task_id")
    approve = sub.add_parser("approve")
    approve.add_argument("specs_dir")
    approve.add_argument("--evidence", required=True)
    complete = sub.add_parser("complete")
    complete.add_argument("specs_dir")
    complete.add_argument("task_id")
    complete.add_argument("--evidence", required=True)
    complete.add_argument("--notes", default="")
    block = sub.add_parser("block")
    block.add_argument("specs_dir")
    block.add_argument("task_id")
    block.add_argument("--reason", required=True)
    skip = sub.add_parser("skip")
    skip.add_argument("specs_dir")
    skip.add_argument("task_id")
    skip.add_argument("--approval", required=True)
    sync = sub.choices["sync-check"]
    sync.add_argument("--write", action="store_true")
    acceptance_start = sub.add_parser("acceptance-start-agent")
    acceptance_start.add_argument("specs_dir")
    acceptance_start.add_argument("agent_id")
    acceptance_complete = sub.add_parser("acceptance-complete-agent")
    acceptance_complete.add_argument("specs_dir")
    acceptance_complete.add_argument("agent_id")
    acceptance_complete.add_argument("--result", required=True)
    acceptance_complete.add_argument("--report", required=True)
    acceptance_issue = sub.add_parser("acceptance-record-issue")
    acceptance_issue.add_argument("specs_dir")
    acceptance_issue.add_argument("--unit", required=True)
    acceptance_issue.add_argument("--severity", required=True)
    acceptance_issue.add_argument("--title", required=True)
    acceptance_issue.add_argument("--evidence", required=True)
    acceptance_issue.add_argument("--tasks", default="")
    acceptance_issue.add_argument("--agent", default="")
    acceptance_fix_start = sub.add_parser("acceptance-fix-start")
    acceptance_fix_start.add_argument("specs_dir")
    acceptance_fix_start.add_argument("fix_id")
    acceptance_fix_complete = sub.add_parser("acceptance-fix-complete")
    acceptance_fix_complete.add_argument("specs_dir")
    acceptance_fix_complete.add_argument("fix_id")
    acceptance_fix_complete.add_argument("--evidence", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "discover":
            print(format_json(command_discover(args.specs_root)))
            return 0
        if args.command == "new":
            print(format_json(command_new_workflow(args.specs_root, args.slug, args.run_id)))
            return 0
        if args.command == "guard-all":
            result = command_guard_all(args.specs_root)
            print(format_json(result))
            return 0 if result["ok"] else 1
        if args.command == "init":
            workflow = detect_workflow(args.specs_dir)
            ensure_progress_files(args.specs_dir, workflow)
            print(f"Initialized progress files for {workflow}")
            return 0
        if args.command == "status":
            print(format_json(command_status(args.specs_dir)))
            return 0
        if args.command == "resume":
            result = command_resume(args.specs_dir)
            print(format_json(result))
            return 1 if result["issues"] else 0
        if args.command == "waves":
            print(format_json({"execution_waves": execution_waves(parse_tasks(args.specs_dir))}))
            return 0
        if args.command == "start":
            print(command_start(args.specs_dir, args.task_id))
            return 0
        if args.command == "approve":
            print(command_approve(args.specs_dir, args.evidence))
            return 0
        if args.command == "complete":
            print(command_complete(args.specs_dir, args.task_id, args.evidence, args.notes))
            return 0
        if args.command == "block":
            print(command_block(args.specs_dir, args.task_id, args.reason))
            return 0
        if args.command == "skip":
            print(command_skip(args.specs_dir, args.task_id, args.approval))
            return 0
        if args.command == "sync-check":
            result = command_sync_check(args.specs_dir, write=args.write)
            print(format_json(result))
            return 1 if result["issues"] else 0
        if args.command == "guard-commit":
            result = command_guard_commit(args.specs_dir)
            print(format_json(result))
            return 0 if result["ok"] else 1
        if args.command == "pre-acceptance":
            result = command_pre_acceptance(args.specs_dir)
            print(format_json(result))
            return 0 if result["ok"] else 1
        if args.command == "acceptance-init":
            print(format_json(command_acceptance_init(args.specs_dir, args.mode)))
            return 0
        if args.command == "acceptance-status":
            print(format_json(command_acceptance_status(args.specs_dir)))
            return 0
        if args.command == "acceptance-start-agent":
            print(format_json(command_acceptance_start_agent(args.specs_dir, args.agent_id)))
            return 0
        if args.command == "acceptance-complete-agent":
            print(format_json(command_acceptance_complete_agent(args.specs_dir, args.agent_id, args.result, args.report)))
            return 0
        if args.command == "acceptance-record-issue":
            print(format_json(command_acceptance_record_issue(
                args.specs_dir,
                args.unit,
                args.severity,
                args.title,
                args.evidence,
                args.tasks,
                args.agent,
            )))
            return 0
        if args.command == "acceptance-plan-fixes":
            print(format_json(command_acceptance_plan_fixes(args.specs_dir)))
            return 0
        if args.command == "acceptance-fix-start":
            print(format_json(command_acceptance_fix_start(args.specs_dir, args.fix_id)))
            return 0
        if args.command == "acceptance-fix-complete":
            print(format_json(command_acceptance_fix_complete(args.specs_dir, args.fix_id, args.evidence)))
            return 0
        if args.command == "acceptance-next-round":
            print(format_json(command_acceptance_next_round(args.specs_dir)))
            return 0
        if args.command == "acceptance-finish":
            print(format_json(command_acceptance_finish(args.specs_dir)))
            return 0
    except SpecProgressError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
