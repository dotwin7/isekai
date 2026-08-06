from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import _unit_json, _unit_preflight_issues, _write_json, unit_lock
from .decisions import _is_iso_timestamp


EVIDENCE_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "passed",
    "scope",
    "recorded_by",
    "recorded_at",
    "commands",
}
EVIDENCE_COMMAND_REQUIRED_FIELDS = {
    "command",
    "exit_code",
    "output_digest",
    "observed_at",
}


def _evidence_issues(
    evidence: Any,
    unit_id: str | None = None,
    *,
    require_passing: bool = True,
) -> list[str]:
    if not isinstance(evidence, dict):
        return ["verification evidence must be an object"]
    issues: list[str] = []
    missing_fields = sorted(EVIDENCE_REQUIRED_FIELDS - evidence.keys())
    if missing_fields:
        issues.append(
            f"verification evidence missing fields: {', '.join(missing_fields)}"
        )
    if unit_id is not None and evidence.get("unit_id") != unit_id:
        issues.append("verification evidence unit_id does not match Unit")
    if evidence.get("type") != "verification-evidence":
        issues.append("verification evidence has an invalid type")
    if evidence.get("schema_version") != "1.0.0":
        issues.append("verification evidence has an unsupported schema_version")
    if not isinstance(evidence.get("passed"), bool):
        issues.append("verification evidence passed must be boolean")
    if not isinstance(evidence.get("scope"), str) or not evidence.get("scope", "").strip():
        issues.append("verification evidence requires a scope")
    if not isinstance(evidence.get("recorded_by"), str) or not evidence.get("recorded_by", "").strip():
        issues.append("verification evidence requires recorded_by provenance")
    if not _is_iso_timestamp(evidence.get("recorded_at")):
        issues.append("verification evidence recorded_at must be an ISO-8601 timestamp")

    commands = evidence.get("commands")
    if not isinstance(commands, list) or not commands:
        issues.append("verification evidence has no commands")
    else:
        for index, command in enumerate(commands):
            if not isinstance(command, dict):
                issues.append(f"evidence command {index} must be an object")
                continue
            missing_command_fields = sorted(
                EVIDENCE_COMMAND_REQUIRED_FIELDS - command.keys()
            )
            if missing_command_fields:
                issues.append(
                    f"evidence command {index} missing fields: "
                    f"{', '.join(missing_command_fields)}"
                )
                continue
            if not isinstance(command.get("command"), str) or not command["command"].strip():
                issues.append(f"evidence command {index} requires command text")
            exit_code = command.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                issues.append(f"evidence command {index} exit_code must be an integer")
            digest = command.get("output_digest")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                issues.append(
                    f"evidence command {index} output_digest must be a SHA-256 hex digest"
                )
            if not _is_iso_timestamp(command.get("observed_at")):
                issues.append(
                    f"evidence command {index} observed_at must be an ISO-8601 timestamp"
                )
            if evidence.get("passed") is True and exit_code != 0:
                issues.append(
                    f"passing verification evidence has non-zero command {index} exit_code"
                )
    if require_passing and evidence.get("passed") is not True:
        issues.append("verification evidence is not passing")
    return issues


def _passing_evidence(unit_dir: Path) -> bool:
    evidence_path = unit_dir / "evidence/verification.json"
    if not evidence_path.is_file():
        return False
    try:
        evidence = _unit_json(unit_dir, "evidence/verification.json")
        unit = _unit_json(unit_dir, "unit.json")
    except ValueError:
        return False
    return not _evidence_issues(evidence, str(unit.get("id")))


def build_command_evidence(
    command: str,
    exit_code: int,
    output: str,
    observed_at: str,
) -> dict[str, Any]:
    if not isinstance(command, str) or not command.strip():
        raise ValueError("command must be a non-empty string")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError("exit_code must be an integer")
    if not isinstance(output, str):
        raise ValueError("output must be a string")
    if not isinstance(observed_at, str) or not observed_at.strip():
        raise ValueError("observed_at must be a non-empty string")
    return {
        "command": command.strip(),
        "exit_code": exit_code,
        "output_digest": hashlib.sha256(output.encode("utf-8")).hexdigest(),
        "observed_at": observed_at.strip(),
    }


def record_evidence(
    path: str | Path,
    *,
    passed: bool,
    commands: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
    notes: str = "",
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    if not isinstance(passed, bool):
        raise ValueError("passed must be boolean")
    if not isinstance(commands, list) or not commands:
        raise ValueError("commands must be a non-empty list")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("scope must be a non-empty string")
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise ValueError("recorded_by must be a non-empty string")

    with unit_lock(unit_dir):
        return _record_evidence_locked(
            unit_dir,
            passed=passed,
            commands=commands,
            scope=scope,
            recorded_by=recorded_by,
            notes=notes,
        )


def _record_evidence_locked(
    unit_dir: Path,
    *,
    passed: bool,
    commands: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
    notes: str,
) -> dict[str, Any]:
    unit = _unit_json(unit_dir, "unit.json")
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise ValueError("Evidence preflight blocked: " + "; ".join(preflight_issues))
    normalized_commands: list[dict[str, Any]] = []
    for index, command in enumerate(commands):
        if not isinstance(command, dict):
            raise ValueError(f"command {index} must be an object")
        item = dict(command)
        if "output" in item:
            output = item.pop("output")
            computed = build_command_evidence(
                str(item.get("command", "")),
                item.get("exit_code"),
                output,
                str(item.get("observed_at", "")),
            )
            supplied_digest = item.get("output_digest")
            if supplied_digest is not None and supplied_digest != computed["output_digest"]:
                raise ValueError(f"command {index} output_digest does not match output")
            item.update(computed)
        normalized_commands.append(item)

    now = datetime.now(timezone.utc)
    evidence = {
        "id": "EVD-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "verification-evidence",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "passed": passed,
        "scope": scope.strip(),
        "recorded_by": recorded_by.strip(),
        "recorded_at": now.isoformat(),
        "commands": normalized_commands,
    }
    if notes.strip():
        evidence["notes"] = notes.strip()
    issues = _evidence_issues(
        evidence,
        str(unit.get("id")),
        require_passing=False,
    )
    if issues:
        raise ValueError("; ".join(issues))
    _write_json(unit_dir / "evidence/verification.json", evidence)
    persisted_evidence = _unit_json(unit_dir, "evidence/verification.json")
    if persisted_evidence.get("id") != evidence["id"]:
        raise ValueError("Evidence postflight blocked: record was not persisted")
    return {"path": str(unit_dir / "evidence/verification.json"), "evidence": evidence}
