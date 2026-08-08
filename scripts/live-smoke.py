#!/usr/bin/env python3
"""Exercise a checkout through a fresh project and optional live host sessions."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from isekai.distribution.install import (  # noqa: E402
    _install_from_verified_checkout as install_from_checkout,
)


STARTER = ROOT / "examples/reference-product/starter"
RUNTIMES = ("codex", "claude", "kiro")
SURFACES = {
    "codex": (
        ".agents/skills/isekai/SKILL.md",
        ".isekai/marketplaces/codex/plugins/isekai-agent-plugin/"
        ".codex-plugin/plugin.json",
    ),
    "claude": (
        ".claude/skills/isekai/SKILL.md",
        ".isekai/marketplaces/claude/plugins/isekai-agent-plugin/"
        ".claude-plugin/plugin.json",
    ),
    "kiro": (".kiro/skills/isekai/SKILL.md",),
}


class SmokeFailure(RuntimeError):
    """Report a failed smoke assertion without a Python traceback by default."""


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
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


def _expand_runtimes(values: Sequence[str]) -> tuple[str, ...]:
    selected = set(RUNTIMES if not values or "all" in values else values)
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
    commands: list[str] = []
    messages: list[str] = []
    action_results: dict[str, dict[str, Any]] = {}
    for line in trace.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            continue
        if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
            messages.append(item["text"])
        if item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if isinstance(command, str):
            commands.append(command)
        output = item.get("aggregated_output")
        if not isinstance(output, str):
            continue
        try:
            payload = json.loads(output)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or not isinstance(payload.get("action"), str):
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            action_results[payload["action"]] = result
    return {
        "commands": commands,
        "messages": messages,
        "action_results": action_results,
    }


def _codex_live(project: Path, timeout: int) -> dict[str, Any]:
    executable = shutil.which("codex")
    if executable is None:
        raise SmokeFailure("codex executable is unavailable")
    prompt = (
        f"$isekai on --project {project}\n\n"
        "Use the injected isekai Skill. Run only the project-local handshake and on. "
        "Do not modify files. If the Skill was not injected, fail instead of searching "
        "the project for a fallback. Report project id and adapter_mode.state."
    )
    completed = _run(
        (
            executable,
            "exec",
            "--ignore-user-config",
            "--ephemeral",
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
    commands = evidence["commands"]
    messages = evidence["messages"]
    results = evidence["action_results"]
    handshake = results.get("handshake", {})
    activated = results.get("on", {})
    adapter_mode = activated.get("adapter_mode", {})
    injected = any(
        "injected" in message.lower() or "주입" in message for message in messages
    )
    passed = all(
        (
            completed.returncode == 0,
            injected,
            any("plugin handshake --runtime codex" in command for command in commands),
            any("plugin on --project" in command for command in commands),
            handshake.get("compatible") is True,
            isinstance(adapter_mode, dict) and adapter_mode.get("state") == "on",
        )
    )
    if not passed:
        raise SmokeFailure(
            "Codex live smoke did not prove injected Skill activation\n"
            f"exit: {completed.returncode}\n"
            f"stdout: {trace[-5000:]}\n"
            f"stderr: {completed.stderr[-2000:]}"
        )
    return {
        "passed": True,
        "executable": executable,
        "checks": [
            "injected Skill acknowledged",
            "handshake compatible",
            "adapter mode on",
        ],
    }


def _claude_live(project: Path, timeout: int) -> dict[str, Any]:
    executable = shutil.which("claude")
    if executable is None:
        raise SmokeFailure("claude executable is unavailable")
    prompt = (
        f"/isekai on --project {project}\n\n"
        "Use the injected project Skill. Run only the project-local handshake and on, "
        "make no file changes, and report project id and adapter_mode.state."
    )
    completed = _run(
        (
            executable,
            "-p",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Bash,Read",
            prompt,
        ),
        cwd=project,
        timeout=timeout,
    )
    trace = completed.stdout
    required = ("plugin handshake --runtime claude", "plugin on --project")
    if completed.returncode != 0 or any(token not in trace for token in required):
        raise SmokeFailure(
            "Claude live smoke did not prove injected Skill activation\n"
            f"exit: {completed.returncode}\n"
            f"stdout: {trace[-5000:]}\n"
            f"stderr: {completed.stderr[-2000:]}"
        )
    return {"passed": True, "executable": executable, "checks": list(required)}


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
        choices=("codex", "claude"),
        default=[],
        help="Optional live model host to invoke; repeatable.",
    )
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--keep-project",
        action="store_true",
        help="Retain and print the generated temporary project path.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    runtimes = _expand_runtimes(args.runtime)
    missing_hosts = set(args.host) - set(runtimes)
    if missing_hosts:
        _parser().error(
            "live hosts must also be installed with --runtime: "
            + ", ".join(sorted(missing_hosts))
        )

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.keep_project:
        project = Path(tempfile.mkdtemp(prefix="isekai-live-smoke-"))
    else:
        temporary = tempfile.TemporaryDirectory(prefix="isekai-live-smoke-")
        project = Path(temporary.name)

    try:
        shutil.copytree(STARTER, project, dirs_exist_ok=True)
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
                "plugin",
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
        doctor = _json_command(
            (str(launcher), "doctor", "--path", str(project)),
            cwd=project,
            timeout=args.timeout,
        )
        if doctor.get("ready") is not True:
            raise SmokeFailure(f"doctor did not report ready: {doctor}")
        surfaces = _validate_surfaces(project, runtimes)
        hosts: dict[str, Any] = {}
        if "codex" in args.host:
            hosts["codex"] = _codex_live(project, args.timeout)
        if "claude" in args.host:
            hosts["claude"] = _claude_live(project, args.timeout)
        print(
            json.dumps(
                {
                    "passed": True,
                    "project": str(project),
                    "project_retained": args.keep_project,
                    "runtimes": list(runtimes),
                    "install": installed,
                    "init": initialized,
                    "doctor": doctor,
                    "surfaces": surfaces,
                    "live_hosts": hosts,
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
