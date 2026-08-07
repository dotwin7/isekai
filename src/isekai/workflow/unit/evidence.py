from __future__ import annotations

import hashlib
import json
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
    "stage",
    "passed",
    "scope",
    "recorded_by",
    "recorded_at",
    "commands",
    "envelope_id",
    "envelope_digest",
    "authorization_ledger_digest",
    "authorization_count",
}
EVIDENCE_COMMAND_REQUIRED_FIELDS = {
    "command",
    "exit_code",
    "output_digest",
    "observed_at",
    "authorization_id",
}
EVIDENCE_ALLOWED_STATUSES = {
    "construction",
    "awaiting-release-decision",
    "releasing",
    "operating",
}
EVIDENCE_ID_PATTERN = re.compile(r"EVD-[0-9]{20}")


def _evidence_issues(
    evidence: Any,
    unit_id: str | None = None,
    *,
    require_passing: bool = True,
    authorization_binding: dict[str, Any] | None = None,
    authorization_grants: dict[str, dict[str, Any]] | None = None,
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
    evidence_id = evidence.get("id")
    if not isinstance(evidence_id, str) or not EVIDENCE_ID_PATTERN.fullmatch(
        evidence_id
    ):
        issues.append("verification evidence has an invalid id")
    if not isinstance(evidence.get("stage"), str) or not evidence.get(
        "stage", ""
    ).strip():
        issues.append("verification evidence requires stage")
    if not isinstance(evidence.get("passed"), bool):
        issues.append("verification evidence passed must be boolean")
    if not isinstance(evidence.get("scope"), str) or not evidence.get("scope", "").strip():
        issues.append("verification evidence requires a scope")
    if not isinstance(evidence.get("recorded_by"), str) or not evidence.get("recorded_by", "").strip():
        issues.append("verification evidence requires recorded_by provenance")
    if not _is_iso_timestamp(evidence.get("recorded_at")):
        issues.append("verification evidence recorded_at must be an ISO-8601 timestamp")
    for field in ("envelope_id", "envelope_digest", "authorization_ledger_digest"):
        if not isinstance(evidence.get(field), str) or not evidence.get(field, "").strip():
            issues.append(f"verification evidence requires {field}")
    for field in ("envelope_digest", "authorization_ledger_digest"):
        value = evidence.get(field)
        if isinstance(value, str) and not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
            issues.append(f"verification evidence {field} must be a SHA-256 digest")
    authorization_count = evidence.get("authorization_count")
    if (
        not isinstance(authorization_count, int)
        or isinstance(authorization_count, bool)
        or authorization_count < 0
    ):
        issues.append("verification evidence authorization_count must be a non-negative integer")
    if authorization_binding is not None:
        for field, expected in authorization_binding.items():
            if evidence.get(field) != expected:
                issues.append(
                    "verification evidence is stale because the authorization ledger changed"
                )
                break

    commands = evidence.get("commands")
    used_authorizations: set[str] = set()
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
            authorization_id = command.get("authorization_id")
            if not isinstance(authorization_id, str) or not authorization_id.strip():
                issues.append(
                    f"evidence command {index} requires authorization_id"
                )
            elif authorization_id in used_authorizations:
                issues.append(
                    f"evidence command {index} reuses authorization_id: {authorization_id}"
                )
            else:
                used_authorizations.add(authorization_id)
                if authorization_grants is not None:
                    grant = authorization_grants.get(authorization_id)
                    if grant is None or grant.get("action") != "test":
                        issues.append(
                            f"evidence command {index} is not bound to a current test authorization"
                        )
                    else:
                        if isinstance(authorization_count, int) and not isinstance(
                            authorization_count, bool
                        ):
                            expected_iteration = (
                                authorization_count - len(commands) + index + 1
                            )
                            if grant.get("iteration") != expected_iteration:
                                issues.append(
                                    f"evidence command {index} must use the latest authorized test actions"
                                )
                        if grant.get("stage") != evidence.get("stage"):
                            issues.append(
                                f"evidence command {index} authorization stage does not match Evidence stage"
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
        binding, grants = _current_authorization_context(
            unit_dir, unit, check_expiry=False
        )
    except ValueError:
        return False
    return not _evidence_issues(
        evidence,
        str(unit.get("id")),
        authorization_binding=binding,
        authorization_grants=grants,
    )


def _current_authorization_context(
    unit_dir: Path,
    unit: dict[str, Any],
    *,
    check_expiry: bool,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    from .authorization import (
        _authorization_ledger_digest,
        _authorization_ledger_issues,
    )
    from .decisions import _approved_envelope_decision_issues
    from .execution import _execution_envelope_issues

    envelope = _unit_json(unit_dir, "execution-envelope.json")
    ledger = _unit_json(unit_dir, "execution-authorizations.json")
    envelope_issues = _execution_envelope_issues(
        envelope,
        str(unit.get("id")),
        require_approved=True,
        check_expiry=check_expiry,
    )
    envelope_issues.extend(
        _approved_envelope_decision_issues(unit_dir, envelope, unit)
    )
    if envelope_issues:
        raise ValueError(
            "Execution Envelope blocks Evidence: " + "; ".join(envelope_issues)
        )
    ledger_issues = _authorization_ledger_issues(
        ledger, unit, envelope, unit_dir=unit_dir
    )
    if ledger_issues:
        raise ValueError("Action ledger blocks Evidence: " + "; ".join(ledger_issues))
    grants = {
        str(grant.get("id")): grant
        for grant in ledger["grants"]
        if isinstance(grant, dict) and isinstance(grant.get("id"), str)
    }
    binding = {
        "envelope_id": envelope.get("id"),
        "envelope_digest": envelope.get("approval_digest"),
        "authorization_ledger_digest": _authorization_ledger_digest(ledger),
        "authorization_count": len(ledger["grants"]),
    }
    return binding, grants


def _current_authorization_binding(
    unit_dir: Path,
    unit: dict[str, Any],
) -> dict[str, Any]:
    binding, _grants = _current_authorization_context(
        unit_dir, unit, check_expiry=False
    )
    return binding


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


def _verification_evidence_digest(evidence: dict[str, Any]) -> str:
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _evidence_record_relative(evidence_id: str) -> str:
    """Return the immutable record path for one generated Evidence ID."""
    if not EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
        raise ValueError(f"verification evidence has an invalid id: {evidence_id}")
    return f"evidence/records/{evidence_id}.json"


def _persist_evidence_record(
    unit_dir: Path,
    evidence: dict[str, Any],
) -> str:
    """Persist Evidence by ID without allowing a prior record to be replaced."""
    relative = _evidence_record_relative(str(evidence.get("id", "")))
    target = unit_dir / relative
    expected_digest = _verification_evidence_digest(evidence)
    if target.is_file():
        existing = _unit_json(unit_dir, relative)
        if _verification_evidence_digest(existing) != expected_digest:
            raise ValueError(
                "verification Evidence record conflicts with an existing Evidence ID"
            )
        return relative
    _write_json(target, evidence)
    persisted = _unit_json(unit_dir, relative)
    if _verification_evidence_digest(persisted) != expected_digest:
        raise ValueError("verification Evidence record postflight failed")
    return relative


def _archive_current_evidence(unit_dir: Path) -> None:
    """Backfill a legacy singleton Evidence before it is replaced."""
    try:
        current = _unit_json(unit_dir, "evidence/verification.json")
    except ValueError:
        return
    evidence_id = current.get("id")
    if isinstance(evidence_id, str) and EVIDENCE_ID_PATTERN.fullmatch(evidence_id):
        _persist_evidence_record(unit_dir, current)


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
    if unit.get("status") not in EVIDENCE_ALLOWED_STATUSES:
        raise ValueError(
            "Evidence can only be recorded after Construction begins; current status: "
            + str(unit.get("status"))
        )
    authorization_binding, authorization_grants = _current_authorization_context(
        unit_dir, unit, check_expiry=True
    )
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
            if (
                supplied_digest is not None
                and supplied_digest != computed["output_digest"]
            ):
                raise ValueError(f"command {index} output_digest does not match output")
            item.update(computed)
        normalized_commands.append(item)

    now = datetime.now(timezone.utc)
    evidence = {
        "id": "EVD-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "verification-evidence",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "stage": unit.get("phase"),
        "passed": passed,
        "scope": scope.strip(),
        "recorded_by": recorded_by.strip(),
        "recorded_at": now.isoformat(),
        "commands": normalized_commands,
        **authorization_binding,
    }
    if notes.strip():
        evidence["notes"] = notes.strip()
    issues = _evidence_issues(
        evidence,
        str(unit.get("id")),
        require_passing=False,
        authorization_binding=authorization_binding,
        authorization_grants=authorization_grants,
    )
    if issues:
        raise ValueError("; ".join(issues))
    _archive_current_evidence(unit_dir)
    record_relative = _persist_evidence_record(unit_dir, evidence)
    _write_json(unit_dir / "evidence/verification.json", evidence)
    persisted_evidence = _unit_json(unit_dir, "evidence/verification.json")
    if persisted_evidence.get("id") != evidence["id"]:
        raise ValueError("Evidence postflight blocked: record was not persisted")
    return {
        "path": str(unit_dir / "evidence/verification.json"),
        "record_path": str(unit_dir / record_relative),
        "evidence": evidence,
    }
