#!/usr/bin/env python3
"""Minimal stdio MCP server for Spec workflow progress tools.

The server intentionally wraps the stdlib-only spec_progress module. It avoids
duplicating task-state rules; CLI, hook, validator, and MCP share one state
machine.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from spec_progress import (  # noqa: E402
    SpecProgressError,
    command_acceptance_complete_agent,
    command_acceptance_finish,
    command_acceptance_fix_complete,
    command_acceptance_fix_start,
    command_acceptance_init,
    command_acceptance_next_round,
    command_acceptance_plan_fixes,
    command_acceptance_record_issue,
    command_acceptance_start_agent,
    command_acceptance_status,
    command_approve,
    command_block,
    command_complete,
    command_discover,
    command_resume,
    command_new_workflow,
    command_skip,
    command_start,
    command_status,
    specs_path,
)


DEFAULT_IDLE_TIMEOUT_SECONDS = 300.0
IDLE_TIMEOUT_ENV = "SPEC_WORKFLOW_MCP_IDLE_TIMEOUT_SECONDS"
LEGACY_IDLE_TIMEOUT_ENV = "SPEC_CODING_MCP_IDLE_TIMEOUT_SECONDS"


def _idle_timeout_seconds() -> float:
    raw = os.environ.get(IDLE_TIMEOUT_ENV) or os.environ.get(LEGACY_IDLE_TIMEOUT_ENV)
    if raw is None:
        return DEFAULT_IDLE_TIMEOUT_SECONDS
    try:
        return max(0.0, float(raw))
    except ValueError:
        print(
            f"Ignoring invalid {IDLE_TIMEOUT_ENV} value {raw!r}; "
            f"using {DEFAULT_IDLE_TIMEOUT_SECONDS:g} seconds.",
            file=sys.stderr,
            flush=True,
        )
        return DEFAULT_IDLE_TIMEOUT_SECONDS


class IdleShutdownTimer:
    """Terminate a stdio server whose client keeps an unused pipe open."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self._generation = 0
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None

    def arm(self) -> None:
        if self.timeout_seconds <= 0:
            return
        with self._lock:
            self._generation += 1
            generation = self._generation
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self.timeout_seconds, self._expire, args=(generation,))
            timer.daemon = True
            self._timer = timer
            timer.start()

    def pause(self) -> None:
        with self._lock:
            self._generation += 1
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _expire(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._timer = None
        # stdin may be blocked forever while the host retains the pipe. This
        # server is stateless, so an immediate exit is the portable way to
        # release it on Windows and POSIX without interrupting active tools.
        os._exit(0)


def _base_dir() -> Path:
    """Directory that all specs_dir arguments must stay within.

    Defaults to the server's current working directory (the repository it was
    launched in). Override with SPEC_WORKFLOW_BASE_DIR when the server runs from
    a different location than the project root.
    """
    return Path(os.environ.get("SPEC_WORKFLOW_BASE_DIR") or os.environ.get("SPEC_CODING_BASE_DIR", os.getcwd())).resolve()


def _checked_specs_dir(args: dict[str, Any]) -> str:
    raw = args.get("specs_dir")
    if not isinstance(raw, str) or not raw.strip():
        raise SpecProgressError("specs_dir is required")
    # Raises SpecProgressError on ../ traversal outside the base directory.
    return specs_path(raw, base_dir=_base_dir()).as_posix()


def _checked_specs_root(args: dict[str, Any]) -> str:
    raw = args.get("specs_root")
    if not isinstance(raw, str) or not raw.strip():
        raise SpecProgressError("specs_root is required")
    return specs_path(raw, base_dir=_base_dir()).as_posix()


def _required_string(args: dict[str, Any], key: str) -> str:
    raw = args.get(key)
    if not isinstance(raw, str) or not raw.strip():
        raise SpecProgressError(f"{key} is required")
    return raw


def _optional_string(args: dict[str, Any], key: str) -> str | None:
    raw = args.get(key)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise SpecProgressError(f"{key} must be a non-empty string when provided")
    return raw


TOOLS = [
    {
        "name": "spec_status",
        "description": "Return workflow, approval, current task, task counts, and next executable wave.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}},
            "required": ["specs_dir"],
        },
    },
    {
        "name": "spec_resume",
        "description": "Check progress.md/spec.yml/tasks.md and return safe resume state.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}},
            "required": ["specs_dir"],
        },
    },
    {
        "name": "spec_discover_workflows",
        "description": "Scan a specs root for open and completed workflow directories.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_root": {"type": "string"}},
            "required": ["specs_root"],
        },
    },
    {
        "name": "spec_new_workflow",
        "description": "Create a new isolated workflow directory under a specs root.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_root": {"type": "string"},
                "slug": {"type": "string"},
            },
            "required": ["specs_root"],
        },
    },
    {
        "name": "spec_approve",
        "description": "Record human approval and freeze the spec/task-plan baseline before implementation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["specs_dir", "evidence"],
        },
    },
    {
        "name": "spec_start_task",
        "description": "Mark a task active and write progress/spec.yml checkpoints.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}, "task_id": {"type": "string"}},
            "required": ["specs_dir", "task_id"],
        },
    },
    {
        "name": "spec_complete_task",
        "description": "Complete a task with verification evidence and update tasks.md/progress.md/spec.yml.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "task_id": {"type": "string"},
                "evidence": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["specs_dir", "task_id", "evidence"],
        },
    },
    {
        "name": "spec_block_task",
        "description": "Record a blocker for the current task without marking it complete.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "task_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["specs_dir", "task_id", "reason"],
        },
    },
    {
        "name": "spec_skip_task",
        "description": "Skip a task only when explicit human approval evidence is provided.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "task_id": {"type": "string"},
                "approval": {"type": "string"},
            },
            "required": ["specs_dir", "task_id", "approval"],
        },
    },
    {
        "name": "spec_acceptance_init",
        "description": "Initialize resumable quick, adaptive (default), or full acceptance review.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["quick", "adaptive", "full"],
                    "description": "Defaults to adaptive; quick is limited to low-risk workflows.",
                },
            },
            "required": ["specs_dir"],
        },
    },
    {
        "name": "spec_acceptance_status",
        "description": "Return acceptance mode, review/integration status, issues, fixes, and affected units.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}},
            "required": ["specs_dir"],
        },
    },
    {
        "name": "spec_acceptance_start_agent",
        "description": "Mark a planned acceptance sub-agent as running.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}, "agent_id": {"type": "string"}},
            "required": ["specs_dir", "agent_id"],
        },
    },
    {
        "name": "spec_acceptance_complete_agent",
        "description": "Record a PASS or ACTIONABLE_ISSUES result for an acceptance sub-agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "agent_id": {"type": "string"},
                "result": {"type": "string", "enum": ["PASS", "ACTIONABLE_ISSUES"]},
                "report": {"type": "string"},
            },
            "required": ["specs_dir", "agent_id", "result", "report"],
        },
    },
    {
        "name": "spec_acceptance_record_issue",
        "description": "Record an evidence-backed acceptance issue with severity P0-P4.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "unit_id": {"type": "string"},
                "severity": {"type": "string"},
                "title": {"type": "string"},
                "evidence": {"type": "string"},
                "task_ids": {"type": "string"},
                "agent_id": {"type": "string"},
            },
            "required": ["specs_dir", "unit_id", "severity", "title", "evidence"],
        },
    },
    {
        "name": "spec_acceptance_plan_fixes",
        "description": "Plan P0-P2 fixes, defer P3-P4, and enforce the two automatic-fix-round limit.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}},
            "required": ["specs_dir"],
        },
    },
    {
        "name": "spec_acceptance_fix_start",
        "description": "Mark an acceptance fix as active.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}, "fix_id": {"type": "string"}},
            "required": ["specs_dir", "fix_id"],
        },
    },
    {
        "name": "spec_acceptance_fix_complete",
        "description": "Mark an acceptance fix done with evidence.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "specs_dir": {"type": "string"},
                "fix_id": {"type": "string"},
                "evidence": {"type": "string"},
            },
            "required": ["specs_dir", "fix_id", "evidence"],
        },
    },
    {
        "name": "spec_acceptance_next_round",
        "description": "Plan delta re-review for affected units, then the global integration review.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}},
            "required": ["specs_dir"],
        },
    },
    {
        "name": "spec_acceptance_finish",
        "description": "Finish only after required reviewers, frozen artifacts, issues, fixes, and integration pass.",
        "inputSchema": {
            "type": "object",
            "properties": {"specs_dir": {"type": "string"}},
            "required": ["specs_dir"],
        },
    },
]


def response(request_id: Any, result: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result
    return payload


def text_result(data: Any) -> dict[str, Any]:
    if isinstance(data, str):
        text = data
    else:
        text = json.dumps(data, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def call_tool(name: str, args: dict[str, Any]) -> Any:
    if name == "spec_status":
        return command_status(_checked_specs_dir(args))
    if name == "spec_resume":
        return command_resume(_checked_specs_dir(args))
    if name == "spec_discover_workflows":
        return command_discover(_checked_specs_root(args))
    if name == "spec_new_workflow":
        return command_new_workflow(_checked_specs_root(args), args.get("slug", "spec-workflow"))
    if name == "spec_approve":
        return command_approve(_checked_specs_dir(args), _required_string(args, "evidence"))
    if name == "spec_start_task":
        return command_start(_checked_specs_dir(args), _required_string(args, "task_id"))
    if name == "spec_complete_task":
        return command_complete(
            _checked_specs_dir(args),
            _required_string(args, "task_id"),
            _required_string(args, "evidence"),
            args.get("notes", ""),
        )
    if name == "spec_block_task":
        return command_block(_checked_specs_dir(args), _required_string(args, "task_id"), _required_string(args, "reason"))
    if name == "spec_skip_task":
        return command_skip(_checked_specs_dir(args), _required_string(args, "task_id"), _required_string(args, "approval"))
    if name == "spec_acceptance_init":
        return command_acceptance_init(_checked_specs_dir(args), _optional_string(args, "mode"))
    if name == "spec_acceptance_status":
        return command_acceptance_status(_checked_specs_dir(args))
    if name == "spec_acceptance_start_agent":
        return command_acceptance_start_agent(_checked_specs_dir(args), _required_string(args, "agent_id"))
    if name == "spec_acceptance_complete_agent":
        return command_acceptance_complete_agent(
            _checked_specs_dir(args),
            _required_string(args, "agent_id"),
            _required_string(args, "result"),
            _required_string(args, "report"),
        )
    if name == "spec_acceptance_record_issue":
        return command_acceptance_record_issue(
            _checked_specs_dir(args),
            _required_string(args, "unit_id"),
            _required_string(args, "severity"),
            _required_string(args, "title"),
            _required_string(args, "evidence"),
            args.get("task_ids", ""),
            args.get("agent_id", ""),
        )
    if name == "spec_acceptance_plan_fixes":
        return command_acceptance_plan_fixes(_checked_specs_dir(args))
    if name == "spec_acceptance_fix_start":
        return command_acceptance_fix_start(_checked_specs_dir(args), _required_string(args, "fix_id"))
    if name == "spec_acceptance_fix_complete":
        return command_acceptance_fix_complete(
            _checked_specs_dir(args),
            _required_string(args, "fix_id"),
            _required_string(args, "evidence"),
        )
    if name == "spec_acceptance_next_round":
        return command_acceptance_next_round(_checked_specs_dir(args))
    if name == "spec_acceptance_finish":
        return command_acceptance_finish(_checked_specs_dir(args))
    raise SpecProgressError(f"Unknown tool: {name}")


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        return response(
            request_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "spec-workflow-progress", "version": "0.3.0"},
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return response(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = message.get("params", {})
        try:
            result = call_tool(params.get("name", ""), params.get("arguments", {}) or {})
            return response(request_id, text_result(result))
        except SpecProgressError as exc:
            return response(request_id, error={"code": -32000, "message": str(exc)})
    return response(request_id, error={"code": -32601, "message": f"Unknown method: {method}"})


def main() -> int:
    idle_shutdown = IdleShutdownTimer(_idle_timeout_seconds())
    idle_shutdown.arm()
    try:
        for line in sys.stdin:
            idle_shutdown.pause()
            try:
                if not line.strip():
                    continue
                request_id: Any = None
                try:
                    message = json.loads(line)
                    request_id = message.get("id")
                    reply = handle(message)
                    if reply is not None:
                        print(json.dumps(reply, ensure_ascii=False), flush=True)
                except Exception as exc:  # Keep MCP server alive for debuggable tool errors.
                    print(
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": request_id,
                                "error": {"code": -32099, "message": str(exc)},
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
            finally:
                idle_shutdown.arm()
    finally:
        idle_shutdown.pause()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
