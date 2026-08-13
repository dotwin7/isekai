#!/usr/bin/env python3
"""Exercise a checkout through a fresh project and optional live host sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from isekai.distribution.install import (  # noqa: E402
    _install_from_verified_checkout as install_from_checkout,
)
from isekai.distribution.execution_profile import (  # noqa: E402
    apply_execution_profile,
)
from isekai.distribution.release import tree_digest  # noqa: E402
from isekai.support.jsonio import write_json_atomic  # noqa: E402


STARTER = ROOT / "examples/reference-product/starter"
COMPLETED = ROOT / "examples/reference-product/completed"
FOUNDATION = ROOT / "foundation"
RUNTIMES = ("codex", "claude", "kiro")
SURFACES = {
    "codex": (".agents/skills/isekai/SKILL.md",),
    "claude": (".claude/skills/isekai/SKILL.md",),
    "kiro": (".kiro/skills/isekai/SKILL.md",),
}


class SmokeFailure(RuntimeError):
    """Report a failed smoke assertion without a Python traceback by default."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=None if environment is None else dict(environment),
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeFailure(f"command timed out after {timeout}s: {command[0]}") from exc


def _json_command(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, Any]:
    completed = _run(command, cwd=cwd, timeout=timeout)
    if completed.returncode != 0:
        raise SmokeFailure(
            f"command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout: {completed.stdout[-2000:]}\n"
            f"stderr: {completed.stderr[-2000:]}"
        )
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(f"command did not return JSON: {' '.join(command)}") from exc
    if not isinstance(value, dict):
        raise SmokeFailure(f"command returned non-object JSON: {' '.join(command)}")
    return value


def _command_version(
    executable: str,
    *,
    cwd: Path,
    timeout: int,
) -> dict[str, str]:
    completed = _run((executable, "--version"), cwd=cwd, timeout=timeout)
    output = (completed.stdout or completed.stderr).strip()
    if completed.returncode != 0 or not output:
        raise SmokeFailure(f"cannot identify live host version: {executable}")
    first_line = output.splitlines()[0].strip()
    match = re.search(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", first_line)
    if match is None:
        raise SmokeFailure(f"cannot parse live host version: {first_line}")
    return {"version": match.group(0), "version_output": first_line}


def _evidence_digest(evidence: dict[str, Any]) -> str:
    body = {key: value for key, value in evidence.items() if key != "evidence_digest"}
    encoded = json.dumps(
        body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _release_manifest_digest() -> str:
    content = (ROOT / "distribution/release.json").read_bytes()
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _smoke_evidence(
    *,
    recorded_by: str,
    runtimes: Sequence[str],
    surfaces: dict[str, list[str]],
    hosts: dict[str, Any],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    observations = []
    for runtime in runtimes:
        live = hosts.get(runtime)
        observation = {
            "runtime": runtime,
            "status": "live-verified" if live is not None else "surface-only",
            "version": live.get("version") if isinstance(live, dict) else None,
            "checks": (
                list(live.get("checks", []))
                if isinstance(live, dict)
                else [
                    "installed Runtime surface exists",
                    "project-local doctor reported ready",
                ]
            ),
            "surfaces": surfaces[runtime],
        }
        if isinstance(live, dict):
            observation["version_output"] = live.get("version_output")
            execution_evidence = live.get("execution_evidence")
            if isinstance(execution_evidence, dict):
                observation["execution_evidence"] = execution_evidence
        observations.append(observation)
    evidence = {
        "id": "RUNTIME-SMOKE-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "runtime-smoke-evidence",
        "schema_version": "1.0.0",
        "recorded_at": now.isoformat(),
        "recorded_by": recorded_by,
        "attestation": {
            "type": "runtime-smoke-attestation",
            "reported_actor": recorded_by,
            "identity_verification": "not-performed-by-script",
            "execution_verification": "script-observed-subprocess",
        },
        "release_manifest_digest": _release_manifest_digest(),
        "passed": True,
        "observations": observations,
    }
    evidence["evidence_digest"] = _evidence_digest(evidence)
    return evidence


def _expand_runtimes(values: Sequence[str]) -> tuple[str, ...]:
    selected = set(RUNTIMES if not values or "all" in values else values)
    return tuple(runtime for runtime in RUNTIMES if runtime in selected)


def _expand_hosts(
    values: Sequence[str],
    runtimes: Sequence[str],
) -> tuple[str, ...]:
    selected = set(runtimes if "all" in values else values)
    return tuple(runtime for runtime in RUNTIMES if runtime in selected)


def _validate_surfaces(project: Path, runtimes: Sequence[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for runtime in runtimes:
        relative_paths = SURFACES[runtime]
        missing = [relative for relative in relative_paths if not (project / relative).is_file()]
        if missing:
            raise SmokeFailure(f"{runtime} surface is incomplete: {', '.join(missing)}")
        result[runtime] = list(relative_paths)
    return result


def _codex_trace_evidence(trace: str) -> dict[str, Any]:
    messages: list[str] = []
    mcp_actions: list[str] = []
    action_results: dict[str, dict[str, Any]] = {}
    thread_id = None
    for line in trace.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            if (
                isinstance(event, dict)
                and event.get("type") == "thread.started"
                and isinstance(event.get("thread_id"), str)
            ):
                thread_id = event["thread_id"]
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        _collect_mcp_actions(item, mcp_actions)
        _collect_runtime_payloads(item, action_results)
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
    return {
        "mcp_actions": mcp_actions,
        "messages": messages,
        "action_results": action_results,
        "thread_id": thread_id,
    }


def _collect_mcp_actions(value: object, actions: list[str]) -> None:
    if isinstance(value, list):
        for item in value:
            _collect_mcp_actions(item, actions)
        return
    if not isinstance(value, dict):
        return
    tool_name = value.get("name") or value.get("tool")
    arguments = value.get("arguments") or value.get("input")
    if isinstance(arguments, str):
        try:
            parsed_arguments = json.loads(arguments)
        except json.JSONDecodeError:
            parsed_arguments = None
        if isinstance(parsed_arguments, dict):
            arguments = parsed_arguments
    if (
        isinstance(tool_name, str)
        and tool_name.replace("-", "_").endswith("runtime_action")
        and isinstance(arguments, dict)
        and isinstance(arguments.get("action"), str)
    ):
        actions.append(arguments["action"])
    for item in value.values():
        _collect_mcp_actions(item, actions)


def _collect_runtime_payloads(
    value: object,
    action_results: dict[str, dict[str, Any]],
) -> None:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return
        _collect_runtime_payloads(parsed, action_results)
        return
    if isinstance(value, list):
        for item in value:
            _collect_runtime_payloads(item, action_results)
        return
    if not isinstance(value, dict):
        return
    action = value.get("action")
    result = value.get("result")
    if isinstance(action, str) and isinstance(result, dict):
        action_results[action] = result
    for item in value.values():
        _collect_runtime_payloads(item, action_results)


def _dict_nodes(value: object) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    if isinstance(value, list):
        for item in value:
            nodes.extend(_dict_nodes(item))
    elif isinstance(value, dict):
        nodes.append(value)
        for item in value.values():
            nodes.extend(_dict_nodes(item))
    return nodes


def _runtime_tool_identity(value: object) -> bool:
    for node in _dict_nodes(value):
        for key in ("name", "title", "tool", "toolName", "tool_name"):
            candidate = node.get(key)
            if not isinstance(candidate, str):
                continue
            normalized = re.sub(r"[^a-z0-9]", "", candidate.lower())
            if normalized.endswith("runtimeaction"):
                return True
    return False


def _object_value(value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _kiro_acp_runtime_evidence(trace: str) -> dict[str, Any]:
    parsed_events = 0
    calls: dict[str, dict[str, Any]] = {}
    call_order: list[str] = []
    for line in trace.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        parsed_events += 1
        for update in _dict_nodes(event):
            update_type = update.get("sessionUpdate")
            call_id = update.get("toolCallId")
            if not isinstance(call_id, str) or not call_id:
                continue
            if update_type == "tool_call":
                raw_input = _object_value(update.get("rawInput"))
                action = raw_input.get("action") if raw_input else None
                if not isinstance(action, str) or not _runtime_tool_identity(update):
                    continue
                if call_id not in calls:
                    call_order.append(call_id)
                calls[call_id] = {"action": action, "updates": [update]}
            elif update_type == "tool_call_update" and call_id in calls:
                calls[call_id]["updates"].append(update)

    mcp_actions: list[str] = []
    action_results: dict[str, dict[str, Any]] = {}
    completed_calls = 0
    for call_id in call_order:
        call = calls[call_id]
        updates = call["updates"]
        status = next(
            (
                update.get("status")
                for update in reversed(updates)
                if isinstance(update.get("status"), str)
            ),
            None,
        )
        action = call["action"]
        mcp_actions.append(action)
        if status != "completed":
            continue
        completed_calls += 1
        observed_results: dict[str, dict[str, Any]] = {}
        for update in updates:
            _collect_runtime_payloads(update.get("rawOutput"), observed_results)
            _collect_runtime_payloads(update.get("content"), observed_results)
        result = observed_results.get(action)
        if isinstance(result, dict):
            action_results[action] = result
    return {
        "mcp_actions": mcp_actions,
        "action_results": action_results,
        "parsed_events": parsed_events,
        "completed_calls": completed_calls,
    }


def _trace_digest(trace: str) -> str:
    return "sha256:" + hashlib.sha256(trace.encode("utf-8")).hexdigest()


def _stage_execution_evidence(
    *,
    trace_format: str,
    trace: str,
    actions: Sequence[str],
) -> dict[str, Any]:
    return {
        "format": trace_format,
        "trace_digest": _trace_digest(trace),
        "mcp_actions": list(actions),
    }


def _kiro_acp_evidence(path: Path) -> tuple[str, dict[str, Any]]:
    try:
        trace = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SmokeFailure(f"Kiro ACP trace is unavailable: {path}: {exc}") from exc
    evidence = _kiro_acp_runtime_evidence(trace)
    if evidence["parsed_events"] == 0:
        raise SmokeFailure(f"Kiro ACP trace contains no JSONL events: {path}")
    return trace, evidence


def _kiro_environment(trace_path: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment["KIRO_ACP_RECORD_PATH"] = str(trace_path)
    environment["KIRO_LOG_NO_COLOR"] = "1"
    environment["NO_COLOR"] = "1"
    return environment


def _claude_trace_evidence(trace: str) -> dict[str, Any]:
    mcp_actions: list[str] = []
    action_results: dict[str, dict[str, Any]] = {}
    session_id = None
    for line in trace.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        candidate_session = event.get("session_id")
        if isinstance(candidate_session, str) and candidate_session:
            session_id = candidate_session
        _collect_mcp_actions(event, mcp_actions)
        _collect_runtime_payloads(event, action_results)
    return {
        "mcp_actions": mcp_actions,
        "action_results": action_results,
        "session_id": session_id,
    }


def _golden_action_results_valid(results: dict[str, dict[str, Any]]) -> bool:
    status = results.get("status", {})
    resumed = results.get("resume", {})
    verified = results.get("verify", {})
    status_unit = status.get("unit")
    resumed_unit = resumed.get("unit")
    return all(
        (
            isinstance(status_unit, dict),
            isinstance(status_unit, dict) and status_unit.get("status") == "learned",
            isinstance(resumed_unit, dict),
            isinstance(resumed_unit, dict) and resumed_unit.get("status") == "learned",
            verified.get("valid") is True,
        )
    )


def _followup_prompt() -> str:
    return (
        "Read the current project and briefly explain what it does. Make no file "
        "changes."
    )


def _off_prompt(runtime: str) -> str:
    marker = {
        "codex": "$isekai off",
        "claude": "/isekai off",
        "kiro": "ISEKAI_HEADLESS: off",
    }[runtime]
    return (
        f"{marker}\n\n"
        "Use the explicitly invoked ISEKAI Skill and only the project-local "
        "isekai-core MCP runtime_action tool. Resolve the host-provided argument as "
        "the off action without asking to repeat the command. Call handshake and off, "
        "make no file changes, and report adapter_mode.state."
    )


def _golden_prompt(runtime: str, project: Path, unit: Path) -> str:
    marker = {
        "codex": "$isekai status",
        "claude": "/isekai status",
        "kiro": "ISEKAI_HEADLESS: status",
    }[runtime]
    return (
        f"{marker} --project {project} --unit {unit}\n\n"
        "Use only the installed ISEKAI Skill and project-local isekai-core MCP tools. "
        "Call runtime_action for handshake before each action, then call status, resume, "
        "and verify for exactly this completed "
        "Unit. Make no file changes. Include every exact command and its unmodified JSON "
        "response; the final response must show the learned status and verify.valid."
    )


def _codex_live(
    project: Path,
    golden_project: Path,
    golden_unit: Path,
    timeout: int,
) -> dict[str, Any]:
    executable = shutil.which("codex")
    if executable is None:
        raise SmokeFailure("codex executable is unavailable")
    prompt = (
        f"$isekai on --project {project}\n\n"
        "Use the injected isekai Skill and only the project-local isekai-core MCP "
        "runtime_action tool. Call handshake and on. "
        "Do not modify files. If the Skill was not injected, fail instead of searching "
        "the project for a fallback. Report project id and adapter_mode.state."
    )
    completed = _run(
        (
            executable,
            "exec",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            prompt,
        ),
        cwd=project,
        timeout=timeout,
    )
    trace = completed.stdout
    evidence = _codex_trace_evidence(trace)
    mcp_actions = evidence["mcp_actions"]
    messages = evidence["messages"]
    results = evidence["action_results"]
    handshake = results.get("handshake", {})
    activated = results.get("on", {})
    adapter_mode = activated.get("adapter_mode", {})
    thread_id = evidence.get("thread_id")
    injected = any(
        "injected" in message.lower() or "주입" in message for message in messages
    )
    passed = all(
        (
            completed.returncode == 0,
            injected,
            "handshake" in mcp_actions,
            "on" in mcp_actions,
            handshake.get("compatible") is True,
            isinstance(adapter_mode, dict) and adapter_mode.get("state") == "on",
            isinstance(thread_id, str) and bool(thread_id),
        )
    )
    if not passed:
        raise SmokeFailure(
            "Codex live smoke did not prove injected Skill activation\n"
            f"exit: {completed.returncode}\n"
            f"stdout: {trace[-5000:]}\n"
            f"stderr: {completed.stderr[-2000:]}"
        )
    deactivated = _run(
        (
            executable,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            _off_prompt("codex"),
        ),
        cwd=project,
        timeout=timeout,
    )
    off_evidence = _codex_trace_evidence(deactivated.stdout)
    off_actions = off_evidence["mcp_actions"]
    off_result = off_evidence["action_results"].get("off", {})
    off_mode = off_result.get("adapter_mode", {})
    if (
        deactivated.returncode != 0
        or "handshake" not in off_actions
        or "off" not in off_actions
        or not isinstance(off_mode, dict)
        or off_mode.get("state") != "off"
    ):
        raise SmokeFailure(
            "Codex live smoke did not execute the explicitly invoked off action\n"
            f"exit: {deactivated.returncode}\n"
            f"stdout: {deactivated.stdout[-5000:]}\n"
            f"stderr: {deactivated.stderr[-2000:]}"
        )
    followup = _run(
        (
            executable,
            "exec",
            "resume",
            str(thread_id),
            "--ignore-user-config",
            "--skip-git-repo-check",
            "--json",
            _followup_prompt(),
        ),
        cwd=project,
        timeout=timeout,
    )
    followup_evidence = _codex_trace_evidence(followup.stdout)
    intake = followup_evidence["action_results"].get("intake", {})
    if (
        followup.returncode != 0
        or not isinstance(intake.get("route"), dict)
        or not isinstance(intake.get("workflow"), dict)
    ):
        raise SmokeFailure(
            "Codex live smoke did not automatically intake the next turn in the "
            "same session\n"
            f"exit: {followup.returncode}\n"
            f"stdout: {followup.stdout[-5000:]}\n"
            f"stderr: {followup.stderr[-2000:]}"
        )
    golden = _run(
        (
            executable,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
            _golden_prompt("codex", golden_project, golden_unit),
        ),
        cwd=golden_project,
        timeout=timeout,
    )
    golden_evidence = _codex_trace_evidence(golden.stdout)
    if golden.returncode != 0 or not _golden_action_results_valid(
        golden_evidence["action_results"]
    ):
        raise SmokeFailure(
            "Codex live smoke did not complete status/resume/verify for the Golden Unit\n"
            f"exit: {golden.returncode}\n"
            f"stdout: {golden.stdout[-5000:]}\n"
            f"stderr: {golden.stderr[-2000:]}"
        )
    version = _command_version(executable, cwd=project, timeout=timeout)
    return {
        "passed": True,
        "executable": executable,
        **version,
        "execution_evidence": {
            "activation": _stage_execution_evidence(
                trace_format="codex-jsonl",
                trace=trace,
                actions=mcp_actions,
            ),
            "followup": _stage_execution_evidence(
                trace_format="codex-jsonl",
                trace=followup.stdout,
                actions=followup_evidence["mcp_actions"],
            ),
            "off": _stage_execution_evidence(
                trace_format="codex-jsonl",
                trace=deactivated.stdout,
                actions=off_actions,
            ),
            "golden": _stage_execution_evidence(
                trace_format="codex-jsonl",
                trace=golden.stdout,
                actions=golden_evidence["mcp_actions"],
            ),
        },
        "checks": [
            "injected Skill acknowledged",
            "handshake compatible",
            "adapter mode on",
            "same-session automatic intake",
            "explicit off invocation resolved from host arguments",
            "Golden Unit status/resume/verify valid",
        ],
    }


def _claude_live(
    project: Path,
    golden_project: Path,
    golden_unit: Path,
    timeout: int,
) -> dict[str, Any]:
    executable = shutil.which("claude")
    if executable is None:
        raise SmokeFailure("claude executable is unavailable")
    prompt = (
        f"/isekai on --project {project}\n\n"
        "Use only the explicitly invoked project Skill and the project-local "
        "isekai-core MCP runtime_action tool. Call handshake and on, make no file "
        "changes, and report the exact MCP actions, "
        "project id, and adapter_mode.state."
    )
    completed = _run(
        (
            executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
            "--tools=mcp__isekai_core__runtime_action,Read",
            "--allowedTools=mcp__isekai_core__runtime_action",
            prompt,
        ),
        cwd=project,
        timeout=timeout,
    )
    trace = completed.stdout
    evidence = _claude_trace_evidence(trace)
    session_id = evidence["session_id"]
    mcp_actions = evidence["mcp_actions"]
    results = evidence["action_results"]
    handshake = results.get("handshake", {})
    activated = results.get("on", {})
    adapter_mode = activated.get("adapter_mode", {})
    if (
        completed.returncode != 0
        or "handshake" not in mcp_actions
        or "on" not in mcp_actions
        or handshake.get("compatible") is not True
        or not isinstance(adapter_mode, dict)
        or adapter_mode.get("state") != "on"
        or not isinstance(session_id, str)
        or not session_id
    ):
        raise SmokeFailure(
            "Claude live smoke did not prove injected Skill activation\n"
            f"exit: {completed.returncode}\n"
            f"stdout: {trace[-5000:]}\n"
            f"stderr: {completed.stderr[-2000:]}"
        )
    deactivated = _run(
        (
            executable,
            "-p",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
            "--tools=mcp__isekai_core__runtime_action,Read",
            "--allowedTools=mcp__isekai_core__runtime_action",
            _off_prompt("claude"),
        ),
        cwd=project,
        timeout=timeout,
    )
    off_evidence = _claude_trace_evidence(deactivated.stdout)
    off_actions = off_evidence["mcp_actions"]
    off_result = off_evidence["action_results"].get("off", {})
    off_mode = off_result.get("adapter_mode", {})
    if (
        deactivated.returncode != 0
        or "handshake" not in off_actions
        or "off" not in off_actions
        or not isinstance(off_mode, dict)
        or off_mode.get("state") != "off"
    ):
        raise SmokeFailure(
            "Claude live smoke did not execute the explicitly invoked off action\n"
            f"exit: {deactivated.returncode}\n"
            f"stdout: {deactivated.stdout[-5000:]}\n"
            f"stderr: {deactivated.stderr[-2000:]}"
        )
    followup = _run(
        (
            executable,
            "-p",
            "--resume",
            session_id,
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
            "--tools=mcp__isekai_core__runtime_action,Read",
            "--allowedTools=mcp__isekai_core__runtime_action",
            _followup_prompt(),
        ),
        cwd=project,
        timeout=timeout,
    )
    followup_evidence = _claude_trace_evidence(followup.stdout)
    followup_actions = followup_evidence["mcp_actions"]
    intake = followup_evidence["action_results"].get("intake", {})
    if (
        followup.returncode != 0
        or "intake" not in followup_actions
        or not isinstance(intake.get("route"), dict)
        or not isinstance(intake.get("workflow"), dict)
    ):
        raise SmokeFailure(
            "Claude live smoke did not automatically intake the next turn in the "
            "same session\n"
            f"exit: {followup.returncode}\n"
            f"stdout: {followup.stdout[-5000:]}\n"
            f"stderr: {followup.stderr[-2000:]}"
        )
    golden = _run(
        (
            executable,
            "-p",
            "--no-session-persistence",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "dontAsk",
            "--tools=mcp__isekai_core__runtime_action,Read",
            "--allowedTools=mcp__isekai_core__runtime_action",
            _golden_prompt("claude", golden_project, golden_unit),
        ),
        cwd=golden_project,
        timeout=timeout,
    )
    golden_evidence = _claude_trace_evidence(golden.stdout)
    golden_actions = golden_evidence["mcp_actions"]
    if (
        golden.returncode != 0
        or not all(action in golden_actions for action in ("status", "resume", "verify"))
        or not _golden_action_results_valid(golden_evidence["action_results"])
    ):
        raise SmokeFailure(
            "Claude live smoke did not complete status/resume/verify for the Golden "
            "Unit\n"
            f"exit: {golden.returncode}\n"
            f"stdout: {golden.stdout[-5000:]}\n"
            f"stderr: {golden.stderr[-2000:]}"
        )
    version = _command_version(executable, cwd=project, timeout=timeout)
    return {
        "passed": True,
        "executable": executable,
        **version,
        "execution_evidence": {
            "activation": _stage_execution_evidence(
                trace_format="claude-stream-json",
                trace=trace,
                actions=mcp_actions,
            ),
            "followup": _stage_execution_evidence(
                trace_format="claude-stream-json",
                trace=followup.stdout,
                actions=followup_actions,
            ),
            "off": _stage_execution_evidence(
                trace_format="claude-stream-json",
                trace=deactivated.stdout,
                actions=off_actions,
            ),
            "golden": _stage_execution_evidence(
                trace_format="claude-stream-json",
                trace=golden.stdout,
                actions=golden_actions,
            ),
        },
        "checks": [
            "project Skill activation",
            "handshake compatible",
            "adapter mode on",
            "same-session automatic intake",
            "explicit off invocation resolved from host arguments",
            "Golden Unit status/resume/verify valid",
        ],
    }


def _kiro_live(
    project: Path,
    golden_project: Path,
    golden_unit: Path,
    timeout: int,
) -> dict[str, Any]:
    executable = shutil.which("kiro-cli")
    if executable is None:
        raise SmokeFailure("kiro-cli executable is unavailable")
    trace_root = project.parent / ".kiro-acp"
    trace_root.mkdir(exist_ok=True)
    activation_trace_path = trace_root / "activation.jsonl"
    followup_trace_path = trace_root / "followup.jsonl"
    off_trace_path = trace_root / "off.jsonl"
    golden_trace_path = trace_root / "golden.jsonl"
    prompt = (
        f"ISEKAI_HEADLESS: on --project {project}\n\n"
        "Use only the workspace ISEKAI Skill activated by the exact headless marker. "
        "Use only the project-local isekai-core MCP runtime_action tool. Call handshake "
        "and on, make no file changes, and report the exact MCP actions, project id, "
        "and adapter_mode.state."
    )
    completed = _run(
        (
            executable,
            "chat",
            "--agent",
            "isekai-core",
            "--no-interactive",
            "--require-mcp-startup",
            "--trust-tools=read,@mcp",
            prompt,
        ),
        cwd=project,
        timeout=timeout,
        environment=_kiro_environment(activation_trace_path),
    )
    if completed.returncode != 0:
        raise SmokeFailure(
            "Kiro activation command failed\n"
            f"exit: {completed.returncode}\n"
            f"stdout: {completed.stdout[-5000:]}\n"
            f"stderr: {completed.stderr[-2000:]}"
        )
    activation_trace, activation_evidence = _kiro_acp_evidence(
        activation_trace_path
    )
    activation_actions = activation_evidence["mcp_actions"]
    activation_results = activation_evidence["action_results"]
    handshake = activation_results.get("handshake", {})
    activated = activation_results.get("on", {})
    adapter_mode = activated.get("adapter_mode", {})
    if (
        "handshake" not in activation_actions
        or "on" not in activation_actions
        or handshake.get("compatible") is not True
        or not isinstance(adapter_mode, dict)
        or adapter_mode.get("state") != "on"
    ):
        raise SmokeFailure(
            "Kiro live smoke did not prove workspace Skill activation\n"
            f"exit: {completed.returncode}\n"
            f"stdout: {completed.stdout[-5000:]}\n"
            f"stderr: {completed.stderr[-2000:]}"
        )
    deactivated = _run(
        (
            executable,
            "chat",
            "--agent",
            "isekai-core",
            "--no-interactive",
            "--require-mcp-startup",
            "--trust-tools=read,@mcp",
            _off_prompt("kiro"),
        ),
        cwd=project,
        timeout=timeout,
        environment=_kiro_environment(off_trace_path),
    )
    if deactivated.returncode != 0:
        raise SmokeFailure(
            "Kiro explicit off command failed\n"
            f"exit: {deactivated.returncode}\n"
            f"stdout: {deactivated.stdout[-5000:]}\n"
            f"stderr: {deactivated.stderr[-2000:]}"
        )
    off_trace, off_evidence = _kiro_acp_evidence(off_trace_path)
    off_actions = off_evidence["mcp_actions"]
    off_result = off_evidence["action_results"].get("off", {})
    off_mode = off_result.get("adapter_mode", {})
    if (
        "handshake" not in off_actions
        or "off" not in off_actions
        or not isinstance(off_mode, dict)
        or off_mode.get("state") != "off"
    ):
        raise SmokeFailure(
            "Kiro live smoke did not execute the explicit headless off action\n"
            f"stdout: {deactivated.stdout[-5000:]}\n"
            f"stderr: {deactivated.stderr[-2000:]}"
        )
    followup = _run(
        (
            executable,
            "chat",
            "--agent",
            "isekai-core",
            "--resume",
            "--no-interactive",
            "--require-mcp-startup",
            "--trust-tools=read,@mcp",
            _followup_prompt(),
        ),
        cwd=project,
        timeout=timeout,
        environment=_kiro_environment(followup_trace_path),
    )
    if followup.returncode != 0:
        raise SmokeFailure(
            "Kiro same-session follow-up command failed\n"
            f"exit: {followup.returncode}\n"
            f"stdout: {followup.stdout[-5000:]}\n"
            f"stderr: {followup.stderr[-2000:]}"
        )
    followup_trace, followup_evidence = _kiro_acp_evidence(followup_trace_path)
    followup_actions = followup_evidence["mcp_actions"]
    intake = followup_evidence["action_results"].get("intake", {})
    if (
        "intake" not in followup_actions
        or not isinstance(intake.get("route"), dict)
        or not isinstance(intake.get("workflow"), dict)
    ):
        raise SmokeFailure(
            "Kiro live smoke did not automatically intake the next turn in the same "
            "session\n"
            f"exit: {followup.returncode}\n"
            f"stdout: {followup.stdout[-5000:]}\n"
            f"stderr: {followup.stderr[-2000:]}"
        )
    golden = _run(
        (
            executable,
            "chat",
            "--agent",
            "isekai-core",
            "--no-interactive",
            "--require-mcp-startup",
            "--trust-tools=read,@mcp",
            _golden_prompt("kiro", golden_project, golden_unit),
        ),
        cwd=golden_project,
        timeout=timeout,
        environment=_kiro_environment(golden_trace_path),
    )
    if golden.returncode != 0:
        raise SmokeFailure(
            "Kiro Golden Unit command failed\n"
            f"exit: {golden.returncode}\n"
            f"stdout: {golden.stdout[-5000:]}\n"
            f"stderr: {golden.stderr[-2000:]}"
        )
    golden_trace, golden_evidence = _kiro_acp_evidence(golden_trace_path)
    golden_actions = golden_evidence["mcp_actions"]
    if (
        not all(
            action in golden_actions for action in ("status", "resume", "verify")
        )
        or not _golden_action_results_valid(golden_evidence["action_results"])
    ):
        raise SmokeFailure(
            "Kiro live smoke did not complete status/resume/verify for the Golden Unit\n"
            f"exit: {golden.returncode}\n"
            f"stdout: {golden.stdout[-5000:]}\n"
            f"stderr: {golden.stderr[-2000:]}"
        )
    version = _command_version(executable, cwd=project, timeout=timeout)
    return {
        "passed": True,
        "executable": executable,
        **version,
        "execution_evidence": {
            "activation": _stage_execution_evidence(
                trace_format="kiro-acp-jsonl",
                trace=activation_trace,
                actions=activation_actions,
            ),
            "followup": _stage_execution_evidence(
                trace_format="kiro-acp-jsonl",
                trace=followup_trace,
                actions=followup_actions,
            ),
            "off": _stage_execution_evidence(
                trace_format="kiro-acp-jsonl",
                trace=off_trace,
                actions=off_actions,
            ),
            "golden": _stage_execution_evidence(
                trace_format="kiro-acp-jsonl",
                trace=golden_trace,
                actions=golden_actions,
            ),
        },
        "checks": [
            "workspace Skill headless marker activated",
            "handshake compatible",
            "adapter mode on",
            "same-session automatic intake",
            "explicit off invocation resolved from host arguments",
            "Golden Unit status/resume/verify valid",
        ],
    }


def _prepare_golden_project(
    smoke_root: Path,
    runtimes: Sequence[str],
    timeout: int,
) -> tuple[Path, Path, dict[str, Any]]:
    golden_root = smoke_root / "golden"
    golden_project = golden_root / "examples/reference-product/completed"
    shutil.copytree(COMPLETED, golden_project)
    shutil.copytree(FOUNDATION, golden_root / "foundation")
    installed = install_from_checkout(
        ROOT,
        golden_project,
        source="https://example.invalid/isekai.git",
        ref="live-smoke",
        commit="c" * 40,
        runtimes=runtimes,
    )
    execution_guards = {
        runtime: apply_execution_profile(golden_project, runtime)
        for runtime in runtimes
    }
    launcher = golden_project / ".isekai/bin/isekai"
    doctor = _json_command(
        (str(launcher), "doctor", "--path", str(golden_project)),
        cwd=golden_project,
        timeout=timeout,
    )
    if doctor.get("ready") is not True:
        raise SmokeFailure(f"Golden Project doctor did not report ready: {doctor}")
    units = sorted(path for path in (golden_project / "units").iterdir() if path.is_dir())
    if len(units) != 1:
        raise SmokeFailure("Golden Project must contain exactly one completed Unit")
    verified = _json_command(
        (str(launcher), "runtime", "verify", "--unit", str(units[0])),
        cwd=golden_project,
        timeout=timeout,
    )
    result = verified.get("result")
    if not isinstance(result, dict) or result.get("valid") is not True:
        raise SmokeFailure(f"Golden Unit did not verify before host smoke: {verified}")
    return golden_project, units[0], {
        "install": installed,
        "doctor": doctor,
        "execution_guards": execution_guards,
        "verify": verified,
    }


def _validate_host_clis(
    runtimes: Sequence[str],
    timeout: int,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}
    for runtime in runtimes:
        result = _json_command(
            (
                sys.executable,
                str(ROOT / "scripts/runtime-host-check.py"),
                "--runtime",
                runtime,
                "--require-cli",
            ),
            cwd=ROOT,
            timeout=timeout,
        )
        if result.get("valid") is not True:
            raise SmokeFailure(f"{runtime} live host preflight failed: {result}")
        checks[runtime] = result
    return checks


def _run_host_without_project_changes(
    runtime: str,
    project: Path,
    golden_project: Path,
    golden_unit: Path,
    timeout: int,
) -> dict[str, Any]:
    before = {
        "activation": tree_digest(project, include_transients=True),
        "golden": tree_digest(golden_project, include_transients=True),
    }
    runner = {
        "codex": _codex_live,
        "claude": _claude_live,
        "kiro": _kiro_live,
    }[runtime]
    result = runner(project, golden_project, golden_unit, timeout)
    after = {
        "activation": tree_digest(project, include_transients=True),
        "golden": tree_digest(golden_project, include_transients=True),
    }
    changed = [name for name in before if before[name] != after[name]]
    if changed:
        raise SmokeFailure(
            f"{runtime} read-only live smoke changed Project files: "
            + ", ".join(changed)
        )
    result["project_trees_unchanged"] = True
    checks = result.get("checks")
    if isinstance(checks, list):
        checks.append("activation and Golden Project trees unchanged")
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        action="append",
        choices=("all", *RUNTIMES),
        default=[],
        help="Runtime to install; repeatable and defaults to all.",
    )
    parser.add_argument(
        "--host",
        action="append",
        choices=("all", *RUNTIMES),
        default=[],
        help="Optional live model host to invoke; repeatable and accepts all.",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="Retain and print the generated temporary project path.",
    )
    parser.add_argument(
        "--evidence-output",
        help="Write a digest-bound runtime smoke Evidence JSON record.",
    )
    parser.add_argument(
        "--recorded-by",
        help="Actor recording --evidence-output; required with that option.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtimes = _expand_runtimes(args.runtime)
    host_runtimes = _expand_hosts(args.host, runtimes)
    missing_hosts = set(host_runtimes) - set(runtimes)
    if missing_hosts:
        _parser().error(
            "live hosts must also be installed with --runtime: "
            + ", ".join(sorted(missing_hosts))
        )
    if args.evidence_output and (
        not isinstance(args.recorded_by, str) or not args.recorded_by.strip()
    ):
        _parser().error("--recorded-by is required with --evidence-output")
    if args.recorded_by and not args.evidence_output:
        _parser().error("--recorded-by requires --evidence-output")

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_project:
        smoke_root = Path(tempfile.mkdtemp(prefix="isekai-live-smoke-"))
    else:
        temporary = tempfile.TemporaryDirectory(prefix="isekai-live-smoke-")
        smoke_root = Path(temporary.name)
    project = smoke_root / "activation"

    try:
        shutil.copytree(STARTER, project)
        installed = install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="live-smoke",
            commit="c" * 40,
            runtimes=runtimes,
        )
        launcher = project / ".isekai/bin/isekai"
        initialized = _json_command(
            (
                str(launcher),
                "runtime",
                "init",
                "--path",
                str(project),
                "--id",
                "isekai-live-smoke",
                "--profile",
                "software-delivery-profile",
                "--document-language",
                "ko",
            ),
            cwd=project,
            timeout=args.timeout,
        )
        execution_guards = {
            runtime: apply_execution_profile(project, runtime)
            for runtime in runtimes
        }
        doctor = _json_command(
            (str(launcher), "doctor", "--path", str(project)),
            cwd=project,
            timeout=args.timeout,
        )
        if doctor.get("ready") is not True:
            raise SmokeFailure(f"doctor did not report ready: {doctor}")
        surfaces = _validate_surfaces(project, runtimes)
        hosts: dict[str, Any] = {}
        golden_project = None
        golden_unit = None
        golden_setup = None
        host_preflight = None
        if host_runtimes:
            host_preflight = _validate_host_clis(host_runtimes, args.timeout)
            golden_project, golden_unit, golden_setup = _prepare_golden_project(
                smoke_root,
                runtimes,
                args.timeout,
            )
        for runtime in host_runtimes:
            assert golden_project is not None and golden_unit is not None
            hosts[runtime] = _run_host_without_project_changes(
                runtime,
                project,
                golden_project,
                golden_unit,
                args.timeout,
            )
        evidence_path = None
        if args.evidence_output:
            evidence = _smoke_evidence(
                recorded_by=args.recorded_by.strip(),
                runtimes=runtimes,
                surfaces=surfaces,
                hosts=hosts,
            )
            evidence_path = write_json_atomic(args.evidence_output, evidence)
        print(
            json.dumps(
                {
                    "passed": True,
                    "project": str(project),
                    "smoke_root": str(smoke_root),
                    "project_retained": args.keep_project,
                    "runtimes": list(runtimes),
                    "live_hosts_requested": list(host_runtimes),
                    "install": installed,
                    "init": initialized,
                    "doctor": doctor,
                    "execution_guards": execution_guards,
                    "surfaces": surfaces,
                    "live_hosts": hosts,
                    "host_preflight": host_preflight,
                    "golden_project": str(golden_project) if golden_project else None,
                    "golden_unit": str(golden_unit) if golden_unit else None,
                    "golden_setup": golden_setup,
                    "evidence": str(evidence_path) if evidence_path else None,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, ValueError, SmokeFailure) as exc:
        print(
            json.dumps(
                {
                    "passed": False,
                    "project": str(project),
                    "smoke_root": str(smoke_root),
                    "project_retained": args.keep_project,
                    "error": str(exc),
                },
                indent=2,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    finally:
        if temporary is not None:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
