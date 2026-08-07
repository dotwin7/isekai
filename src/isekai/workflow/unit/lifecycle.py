from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...support.jsonio import write_bytes_atomic
from .authorization import _authorization_ledger_issues
from .common import (
    UNIT_LOCK_NAME,
    UNIT_REQUIRED_FILES,
    _unit_bytes,
    _unit_json,
    _unit_path_without_symlinks,
    _unit_preflight_issues,
    _unit_text,
    _write_json,
    unit_lock,
)
from .decisions import (
    ALLOWED_TRANSITIONS,
    LIFECYCLE_STATUSES,
    REQUIRED_DECISIONS_FOR_TRANSITIONS,
    STATUS_PHASE,
    _approved_envelope_decision_issues,
    _decision_ledger_issues,
    _has_approved_decision,
    _latest_decision,
    _release_decision_evidence_issues,
)
from .evidence import (
    _current_authorization_context,
    _evidence_issues,
    _passing_evidence,
)
from .execution import (
    _approve_execution_envelope,
    _execution_envelope_issues,
)


_ACCEPTANCE_ITEM = re.compile(
    r"^[ \t]*[-*+][ \t]+\[(?P<state>[ xX]*)\][ \t]*(?P<body>.*)$",
    re.MULTILINE,
)


def _acceptance_criteria_issues(unit_dir: Path) -> list[str]:
    try:
        path = _unit_path_without_symlinks(unit_dir, "acceptance.md")
    except ValueError as exc:
        return [str(exc)]
    if not path.is_file():
        return []
    try:
        content = _unit_text(unit_dir, "acceptance.md")
    except ValueError as exc:
        return [str(exc)]
    criteria = list(_ACCEPTANCE_ITEM.finditer(content))
    if not criteria:
        return ["acceptance criteria are missing"]
    issues: list[str] = []
    if any(not item.group("body").strip() for item in criteria):
        issues.append("acceptance criteria contain an empty item")
    if any(item.group("state").strip().lower() != "x" for item in criteria):
        issues.append("acceptance criteria remain unchecked")
    return issues


def _transition_completion_issues(unit_dir: Path, target_status: str) -> list[str]:
    if target_status not in {"releasing", "learned"}:
        return []
    present = {
        str(file.relative_to(unit_dir))
        for file in unit_dir.rglob("*")
        if file.is_file()
        and not file.is_symlink()
        and "__pycache__" not in file.parts
        and not file.name.startswith(UNIT_LOCK_NAME)
    }
    missing = sorted(UNIT_REQUIRED_FILES - present)
    issues = (
        ["required Unit artifacts are missing: " + ", ".join(missing)]
        if missing
        else []
    )
    issues.extend(_acceptance_criteria_issues(unit_dir))
    try:
        checkpoint = _unit_json(unit_dir, "checkpoint.json")
    except ValueError as exc:
        issues.append(str(exc))
        return issues
    if checkpoint.get("blocked_by"):
        issues.append("checkpoint has blockers")
    if target_status == "learned" and checkpoint.get("pending"):
        issues.append("learned Unit cannot have pending work")
    return issues


def transition_unit(path: str | Path, target_status: str) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        return _transition_unit_locked(unit_dir, target_status)


def _transition_unit_locked(unit_dir: Path, target_status: str) -> dict[str, Any]:
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise ValueError("Unit preflight blocked: " + "; ".join(preflight_issues))
    if target_status not in LIFECYCLE_STATUSES:
        raise ValueError(
            f"target_status must be one of: {', '.join(LIFECYCLE_STATUSES)}"
        )

    unit = _unit_json(unit_dir, "unit.json")
    current_status = unit.get("status")
    if current_status not in LIFECYCLE_STATUSES:
        raise ValueError(f"Unit has an invalid lifecycle status: {current_status}")
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise ValueError(
            f"invalid lifecycle transition: {current_status} -> {target_status}"
        )

    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(target_status)
    if required_gate:
        decisions = _unit_json(unit_dir, "decisions.json")
        if not _has_approved_decision(
            decisions,
            required_gate,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        ):
            raise ValueError(
                f"transition to {target_status} requires an approved "
                f"{required_gate} Decision"
            )

    unit_path = unit_dir / "unit.json"
    unit_before = _unit_bytes(unit_dir, "unit.json")
    envelope_path = unit_dir / "execution-envelope.json"
    envelope_before: bytes | None = None
    mutation_started = False
    try:
        if target_status == "construction":
            decisions = _unit_json(unit_dir, "decisions.json")
            inception_decision = _latest_decision(decisions, "inception")
            if inception_decision is None or inception_decision.get("outcome") != "approved":
                raise ValueError("Execution Envelope needs an approved inception Decision")
            envelope_before = _unit_bytes(unit_dir, "execution-envelope.json")
            mutation_started = True
            _approve_execution_envelope(unit_dir, inception_decision)

        if target_status in {"releasing", "operating"} and not _passing_evidence(unit_dir):
            raise ValueError(
                f"transition to {target_status} requires passing verification Evidence"
            )
        if target_status in {"releasing", "operating"}:
            decisions = _unit_json(unit_dir, "decisions.json")
            release_binding_issues = _release_decision_evidence_issues(
                unit_dir, decisions, unit
            )
            if release_binding_issues:
                raise ValueError(
                    f"transition to {target_status} blocked: "
                    + "; ".join(release_binding_issues)
                )
        if target_status == "learned" and not _passing_evidence(unit_dir):
            raise ValueError(
                "transition to learned requires current passing verification Evidence"
            )

        completion_issues = _transition_completion_issues(unit_dir, target_status)
        if completion_issues:
            raise ValueError(
                f"transition to {target_status} blocked: "
                + "; ".join(completion_issues)
            )

        unit["status"] = target_status
        unit["phase"] = STATUS_PHASE[target_status]
        unit["updated_at"] = datetime.now(timezone.utc).isoformat()
        mutation_started = True
        _write_json(unit_path, unit)
        persisted = _unit_json(unit_dir, "unit.json")
        if persisted.get("status") != target_status:
            raise ValueError("Unit postflight blocked: lifecycle status was not persisted")
    except Exception as exc:
        if not mutation_started:
            raise
        restore_errors: list[str] = []
        restore_files = [(unit_path, unit_before)]
        if envelope_before is not None:
            restore_files.append((envelope_path, envelope_before))
        for restore_path, content in restore_files:
            try:
                write_bytes_atomic(restore_path, content)
            except Exception as restore_exc:  # pragma: no cover - secondary filesystem failure
                restore_errors.append(f"{restore_path}: {restore_exc}")
        if restore_errors:
            raise ValueError(
                "Unit transition failed and could not be restored: "
                + "; ".join(restore_errors)
            ) from exc
        raise
    return {
        "unit_id": unit.get("id"),
        "from": current_status,
        "to": target_status,
        "phase": unit["phase"],
        "required_gate": required_gate,
    }


def verify_unit(path: str | Path) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    present = {
        str(file.relative_to(unit_dir))
        for file in unit_dir.rglob("*")
        if file.is_file()
        and not file.is_symlink()
        and "__pycache__" not in file.parts
        and not file.name.startswith(UNIT_LOCK_NAME)
    }
    missing = sorted(UNIT_REQUIRED_FILES - present)
    issues: list[str] = []

    def read_artifact(relative: str) -> dict[str, Any] | None:
        try:
            return _unit_json(unit_dir, relative)
        except ValueError as exc:
            issues.append(str(exc))
            return None

    unit = read_artifact("unit.json") or {}
    decisions = read_artifact("decisions.json")
    checkpoint = read_artifact("checkpoint.json")
    issues.extend(_unit_preflight_issues(unit_dir))
    envelope_path = unit_dir / "execution-envelope.json"
    envelope: dict[str, Any] | None = None
    if envelope_path.is_file():
        envelope = read_artifact("execution-envelope.json")
        if envelope is not None:
            # Verification audits structure and binding, not whether the
            # approval window is still open, so a Unit stays verifiable after
            # its Envelope lapses.
            issues.extend(
                _execution_envelope_issues(
                    envelope, str(unit.get("id")), check_expiry=False
                )
            )
            issues.extend(_approved_envelope_decision_issues(unit_dir, envelope, unit))
    ledger_path = unit_dir / "execution-authorizations.json"
    if ledger_path.is_file() and envelope is not None:
        ledger = read_artifact("execution-authorizations.json")
        if ledger is not None:
            issues.extend(
                _authorization_ledger_issues(
                    ledger, unit, envelope, unit_dir=unit_dir
                )
            )

    decision_entries = decisions.get("decisions") if decisions is not None else None
    if decisions is not None:
        issues.extend(
            _decision_ledger_issues(
                decisions,
                unit_id=str(unit.get("id")),
                scope=str(unit.get("scope")),
            )
        )
    if isinstance(decision_entries, list) and not decision_entries:
        issues.append("at least one recorded decision is required")
    elif isinstance(decision_entries, list):
        if unit.get("status") in {"awaiting-release-decision", "releasing"}:
            issues.extend(
                _release_decision_evidence_issues(
                    unit_dir, decisions, unit, require_current=True
                )
            )
        elif unit.get("status") in {"operating", "learned"}:
            issues.extend(
                _release_decision_evidence_issues(
                    unit_dir, decisions, unit, require_current=False
                )
            )

    status = unit.get("status")
    if status not in LIFECYCLE_STATUSES:
        issues.append(f"invalid lifecycle status: {status}")
    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(status)
    if required_gate and isinstance(decision_entries, list):
        if not _has_approved_decision(decisions, required_gate):
            issues.append(
                f"status {status} requires an approved {required_gate} Decision"
            )
    if status in STATUS_PHASE and unit.get("phase") != STATUS_PHASE[status]:
        issues.append("Unit phase does not match lifecycle status")
    if checkpoint is not None:
        if checkpoint.get("unit_id") != unit.get("id"):
            issues.append("checkpoint unit_id does not match Unit")
        if checkpoint.get("blocked_by"):
            issues.append("checkpoint has blockers")
        if unit.get("status") == "learned" and checkpoint.get("pending"):
            issues.append("learned Unit cannot have pending work")

    issues.extend(_acceptance_criteria_issues(unit_dir))

    criteria_path = unit_dir / "evaluations/criteria.json"
    if criteria_path.is_file():
        criteria = read_artifact("evaluations/criteria.json")
        if criteria is not None and criteria.get("visibility") != "evaluation-only":
            issues.append("evaluation criteria must be evaluation-only")

    evidence_path = unit_dir / "evidence/verification.json"
    evidence: dict[str, Any] | None = None
    if evidence_path.is_file():
        evidence = read_artifact("evidence/verification.json")
        if evidence is not None:
            try:
                binding, grants = _current_authorization_context(
                    unit_dir, unit, check_expiry=False
                )
            except ValueError as exc:
                issues.append(str(exc))
                binding = None
                grants = None
            issues.extend(
                _evidence_issues(
                    evidence,
                    str(unit.get("id")),
                    authorization_binding=binding,
                    authorization_grants=grants,
                )
            )

    issues = list(dict.fromkeys(issues))
    valid = not missing and not issues
    return {
        "valid": valid,
        "unit_id": unit.get("id"),
        "phase": unit.get("phase"),
        "status": unit.get("status"),
        "artifact_count": len(present),
        "missing": missing,
        "issues": issues,
        "decision_count": len(decision_entries) if isinstance(decision_entries, list) else 0,
        "project_id": unit.get("project_id"),
        "foundation_version": unit.get("foundation_version"),
        "foundation_digest": unit.get("foundation_digest"),
        "pending": checkpoint.get("pending", []) if checkpoint is not None else [],
        "blocked_by": checkpoint.get("blocked_by", []) if checkpoint is not None else [],
        "evidence": evidence,
    }


def unit_status(path: str | Path) -> dict[str, Any]:
    result = verify_unit(path)
    result["unit_dir"] = str(Path(path).resolve())
    return result
