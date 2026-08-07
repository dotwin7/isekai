from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import (
    UNIT_LOCK_NAME,
    UNIT_REQUIRED_FILES,
    _unit_json,
    _unit_preflight_issues,
    _write_json,
    unit_lock,
)
from .decisions import (
    ALLOWED_TRANSITIONS,
    LIFECYCLE_STATUSES,
    REQUIRED_DECISIONS_FOR_TRANSITIONS,
    STATUS_PHASE,
    _approved_envelope_decision_issues,
    _decision_record_issues,
    _has_approved_decision,
    _latest_decision,
)
from .evidence import (
    _current_authorization_binding,
    _evidence_issues,
    _passing_evidence,
)
from .execution import (
    _approve_execution_envelope,
    _authorization_ledger_issues,
    _execution_envelope_issues,
)


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

    if target_status == "construction":
        decisions = _unit_json(unit_dir, "decisions.json")
        inception_decision = _latest_decision(decisions, "inception")
        if inception_decision is None or inception_decision.get("outcome") != "approved":
            raise ValueError("Execution Envelope needs an approved inception Decision")
        _approve_execution_envelope(unit_dir, inception_decision)

    if target_status == "releasing" and not _passing_evidence(unit_dir):
        raise ValueError(
            "transition to releasing requires passing verification Evidence"
        )

    unit["status"] = target_status
    unit["phase"] = STATUS_PHASE[target_status]
    unit["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(unit_dir / "unit.json", unit)
    persisted = _unit_json(unit_dir, "unit.json")
    if persisted.get("status") != target_status:
        raise ValueError("Unit postflight blocked: lifecycle status was not persisted")
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
            issues.extend(_authorization_ledger_issues(ledger, unit, envelope))

    decision_entries = decisions.get("decisions") if decisions is not None else None
    if decisions is not None:
        if decisions.get("unit_id") != unit.get("id"):
            issues.append("decisions.json unit_id does not match Unit")
    if decisions is not None and not isinstance(decision_entries, list):
        issues.append("decisions.json decisions must be a list")
    elif isinstance(decision_entries, list) and not decision_entries:
        issues.append("at least one recorded decision is required")
    elif isinstance(decision_entries, list):
        for index, decision in enumerate(decision_entries):
            issues.extend(
                f"decision {index}: {issue}"
                for issue in _decision_record_issues(
                    decision,
                    unit_id=str(unit.get("id")),
                    scope=str(unit.get("scope")),
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

    acceptance_path = unit_dir / "acceptance.md"
    if acceptance_path.is_file() and "- [ ]" in acceptance_path.read_text(encoding="utf-8"):
        issues.append("acceptance criteria remain unchecked")

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
                binding = _current_authorization_binding(unit_dir, unit)
            except ValueError as exc:
                issues.append(str(exc))
                binding = None
            issues.extend(
                _evidence_issues(
                    evidence,
                    str(unit.get("id")),
                    authorization_binding=binding,
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
