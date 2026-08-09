#!/usr/bin/env python3
"""Validate ISEKAI Runtime surfaces and optional installed host CLIs."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
RUNTIMES = ("codex", "claude", "kiro")
SKILLS = {
    runtime: ROOT / f"runtime/adapters/{runtime}/skills/isekai/SKILL.md"
    for runtime in RUNTIMES
}
EXECUTABLES = {"codex": "codex", "claude": "claude", "kiro": "kiro-cli"}


def _frontmatter(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"Skill has no YAML frontmatter: {path}")
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"Skill frontmatter is not closed: {path}") from exc
    values: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator or not key.strip() or not value.strip():
            raise ValueError(f"Skill frontmatter must use scalar key/value fields: {path}")
        values[key.strip()] = value.strip()
    return values


def _surface_issues(runtime: str) -> list[str]:
    path = SKILLS[runtime]
    if not path.is_file():
        return [f"missing Runtime Skill: {path.relative_to(ROOT)}"]
    try:
        frontmatter = _frontmatter(path)
    except (OSError, UnicodeError, ValueError) as exc:
        return [str(exc)]
    issues: list[str] = []
    if frontmatter.get("name") != "isekai":
        issues.append(f"{runtime} Skill name must be isekai")
    description = frontmatter.get("description", "")
    if not description:
        issues.append(f"{runtime} Skill requires description")
    if len(description) > 1024:
        issues.append(f"{runtime} Skill description exceeds 1024 characters")
    content = path.read_text(encoding="utf-8")
    expected = {
        "codex": ("$isekai", "--runtime codex"),
        "claude": ("/isekai", "--runtime claude", "disable-model-invocation: true"),
        "kiro": ("/isekai", "--runtime kiro", "ISEKAI_HEADLESS:"),
    }[runtime]
    for token in expected:
        if token not in content:
            issues.append(f"{runtime} Skill is missing host contract token: {token}")
    return issues


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _version(executable: str) -> tuple[str | None, str]:
    completed = _run((executable, "--version"))
    output = (completed.stdout or completed.stderr).strip().splitlines()
    first = output[0].strip() if output else ""
    match = re.search(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", first)
    return (match.group(0) if completed.returncode == 0 and match else None, first)


def _kiro_cli_issues(executable: str, version: str | None) -> list[str]:
    issues = []
    if version is None:
        issues.append("Kiro CLI version could not be identified")
    else:
        numeric = tuple(int(part) for part in version.split("+", 1)[0].split("-", 1)[0].split("."))
        if numeric < (2, 1, 0):
            issues.append("Kiro CLI 2.1.0+ is required for Skill slash commands")
    help_result = _run((executable, "chat", "--help"))
    help_text = help_result.stdout + help_result.stderr
    for token in ("--no-interactive", "--trust-tools"):
        if help_result.returncode != 0 or token not in help_text:
            issues.append(f"Kiro CLI is missing required capability: {token}")
    return issues


def _runtime_result(
    runtime: str,
    *,
    check_cli: bool,
    require_cli: bool,
) -> dict[str, Any]:
    issues = _surface_issues(runtime)
    executable = shutil.which(EXECUTABLES[runtime]) if check_cli else None
    version = None
    version_output = None
    cli_checked = executable is not None
    if require_cli and executable is None:
        issues.append(f"required host executable is unavailable: {EXECUTABLES[runtime]}")
    if executable is not None:
        version, version_output = _version(executable)
        if version is None:
            issues.append(f"cannot identify {runtime} host version")
        if runtime == "kiro":
            issues.extend(_kiro_cli_issues(executable, version))
    return {
        "runtime": runtime,
        "valid": not issues,
        "level": "cli-and-surface" if cli_checked else "surface-only",
        "executable": executable,
        "version": version,
        "version_output": version_output,
        "issues": issues,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--runtime",
        action="append",
        choices=("all", *RUNTIMES),
        default=[],
    )
    parser.add_argument("--check-cli", action="store_true")
    parser.add_argument(
        "--require-cli",
        action="store_true",
        help="Check the selected host CLI and fail when it is unavailable.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    selected = set(RUNTIMES if not args.runtime or "all" in args.runtime else args.runtime)
    results = [
        _runtime_result(
            runtime,
            check_cli=args.check_cli or args.require_cli,
            require_cli=args.require_cli,
        )
        for runtime in RUNTIMES
        if runtime in selected
    ]
    valid = all(result["valid"] for result in results)
    print(json.dumps({"valid": valid, "runtimes": results}, indent=2))
    return 0 if valid else 1


if __name__ == "__main__":
    raise SystemExit(main())
