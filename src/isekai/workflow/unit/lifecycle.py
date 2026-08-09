from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .authorization import _authorization_ledger_issues
from .common import (
    UNIT_LOCK_NAME,
    UNIT_REQUIRED_FILES,
    _restore_snapshots,
    _unit_bytes,
    _unit_json,
    _unit_maximum_agent_level,
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
from ..errors import IntegrityError, LifecycleError, PreflightError, WorkflowError
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
_HANGUL = re.compile(r"[가-힣]")
_HUMAN_DOCUMENT_HEADINGS = {
    "ko": {
        "intent.md": "# ",
        "requirements.md": "# 요구사항",
        "architecture.md": "# 아키텍처",
        "implementation-guide.md": "# 구현 가이드",
        "plan.md": "# ",
        "acceptance.md": "# 인수 조건",
        "release.md": "# 릴리스",
        "operations.md": "# 운영",
    },
    "en": {
        "intent.md": "# ",
        "requirements.md": "# Requirements",
        "architecture.md": "# Architecture",
        "implementation-guide.md": "# Implementation Guide",
        "plan.md": "# Plan",
        "acceptance.md": "# Acceptance Criteria",
        "release.md": "# Release",
        "operations.md": "# Operations",
    },
}


def _decision_language_issues(
    decisions: dict[str, Any] | None,
    document_language: str,
) -> list[str]:
    if document_language != "ko" or decisions is None:
        return []
    entries = decisions.get("decisions")
    if not isinstance(entries, list):
        return []
    issues: list[str] = []
    for index, decision in enumerate(entries):
        if not isinstance(decision, dict):
            continue
        descriptions: list[tuple[str, Any]] = [("summary", decision.get("summary"))]
        for field in ("rationale", "tradeoffs", "risks"):
            values = decision.get(field)
            if isinstance(values, list):
                descriptions.extend((field, value) for value in values)
        alternatives = decision.get("alternatives")
        if isinstance(alternatives, list):
            for alternative in alternatives:
                if isinstance(alternative, dict):
                    descriptions.extend(
                        (
                            ("alternatives.option", alternative.get("option")),
                            ("alternatives.reason", alternative.get("reason")),
                        )
                    )
        for field, value in descriptions:
            if isinstance(value, str) and value.strip() and not _HANGUL.search(value):
                issues.append(
                    f"decision {index} {field} must use Korean for "
                    "document_language ko"
                )
    return issues


def _human_document_language_issues(
    unit_dir: Path,
    unit: dict[str, Any],
    decisions: dict[str, Any] | None,
) -> list[str]:
    document_language = unit.get("document_language")
    headings = _HUMAN_DOCUMENT_HEADINGS.get(str(document_language))
    if headings is None:
        return ["Unit document_language must be ko or en"]
    issues: list[str] = []
    for relative, heading in headings.items():
        try:
            content = _unit_text(unit_dir, relative)
        except IntegrityError as exc:
            issues.append(str(exc))
            continue
        if not content.startswith(heading):
            issues.append(
                f"{relative} must use the {document_language} document heading"
            )
        if document_language == "ko" and not _HANGUL.search(content):
            issues.append(f"{relative} must contain Korean human-facing content")
    issues.extend(_decision_language_issues(decisions, str(document_language)))
    return issues


def _acceptance_criteria_issues(unit_dir: Path) -> list[str]:
    try:
        path = _unit_path_without_symlinks(unit_dir, "acceptance.md")
    except IntegrityError as exc:
        return [str(exc)]
    if not path.is_file():
        return []
    try:
        content = _unit_text(unit_dir, "acceptance.md")
    except IntegrityError as exc:
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
    except IntegrityError as exc:
        issues.append(str(exc))
        return issues
    if checkpoint.get("blocked_by"):
        issues.append("checkpoint has blockers")
    if target_status == "learned" and checkpoint.get("pending"):
        issues.append("learned Unit cannot have pending work")
    return issues


def _human_gate_status(
    unit: dict[str, Any],
    decisions: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe the human Decision that governs the next lifecycle edge.

    Adapters should not need to reimplement the lifecycle table to know when to
    stop and ask a person.  This is advisory state derived from the same gate
    constants used by ``transition_unit``; the transition remains the enforcing
    boundary.
    """

    status = unit.get("status")
    next_statuses = ALLOWED_TRANSITIONS.get(str(status), ())
    next_status = next_statuses[0] if len(next_statuses) == 1 else None
    gate = (
        REQUIRED_DECISIONS_FOR_TRANSITIONS.get(next_status)
        if next_status is not None
        else None
    )
    approved = False
    if gate is not None and decisions is not None:
        approved = _has_approved_decision(
            decisions,
            gate,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        )
    return {
        "next_transition": next_status,
        "gate": gate,
        "decision": (
            "approved"
            if approved
            else "required"
            if gate is not None
            else "not-applicable"
        ),
        "blocks_next_transition": gate is not None and not approved,
        "confirmation_required": gate is not None and not approved,
        "confirmation_channel": "interactive-human-or-authenticated-external-approval",
        "core_identity_verification": "not-performed-by-core",
    }


def transition_unit(path: str | Path, target_status: str) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise LifecycleError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        return _transition_unit_locked(unit_dir, target_status)


def _transition_unit_locked(unit_dir: Path, target_status: str) -> dict[str, Any]:
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise PreflightError("Unit preflight blocked: " + "; ".join(preflight_issues))
    if target_status not in LIFECYCLE_STATUSES:
        raise LifecycleError(
            f"target_status must be one of: {', '.join(LIFECYCLE_STATUSES)}"
        )

    unit = _unit_json(unit_dir, "unit.json")
    current_status = unit.get("status")
    if current_status not in LIFECYCLE_STATUSES:
        raise LifecycleError(f"Unit has an invalid lifecycle status: {current_status}")
    if target_status not in ALLOWED_TRANSITIONS[current_status]:
        raise LifecycleError(
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
            raise LifecycleError(
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
                raise LifecycleError("Execution Envelope needs an approved inception Decision")
            envelope_before = _unit_bytes(unit_dir, "execution-envelope.json")
            mutation_started = True
            _approve_execution_envelope(unit_dir, inception_decision)

        if target_status in {"releasing", "operating"} and not _passing_evidence(unit_dir):
            raise LifecycleError(
                f"transition to {target_status} requires passing verification Evidence"
            )
        if target_status in {"releasing", "operating"}:
            decisions = _unit_json(unit_dir, "decisions.json")
            release_binding_issues = _release_decision_evidence_issues(
                unit_dir, decisions, unit
            )
            if release_binding_issues:
                raise IntegrityError(
                    f"transition to {target_status} blocked: "
                    + "; ".join(release_binding_issues)
                )
        if target_status == "learned" and not _passing_evidence(unit_dir):
            raise LifecycleError(
                "transition to learned requires current passing verification Evidence"
            )

        completion_issues = _transition_completion_issues(unit_dir, target_status)
        if completion_issues:
            raise LifecycleError(
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
            raise IntegrityError("Unit postflight blocked: lifecycle status was not persisted")
    except Exception as exc:
        if not mutation_started:
            raise
        snapshots = [(unit_path, unit_before)]
        if envelope_before is not None:
            snapshots.append((envelope_path, envelope_before))
        _restore_snapshots(snapshots, "Unit transition", exc)
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
        raise LifecycleError(f"Unit directory does not exist: {unit_dir}")
    with unit_lock(unit_dir):
        return _verify_unit_locked(unit_dir)


def _verify_unit_locked(unit_dir: Path) -> dict[str, Any]:
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
        except IntegrityError as exc:
            issues.append(str(exc))
            return None

    unit = read_artifact("unit.json") or {}
    decisions = read_artifact("decisions.json")
    checkpoint = read_artifact("checkpoint.json")
    issues.extend(_unit_preflight_issues(unit_dir))
    envelope_path = unit_dir / "execution-envelope.json"
    envelope: dict[str, Any] | None = None
    try:
        maximum_agent_level = _unit_maximum_agent_level(unit_dir)
    except IntegrityError as exc:
        issues.append(str(exc))
        maximum_agent_level = None
    if envelope_path.is_file():
        envelope = read_artifact("execution-envelope.json")
        if envelope is not None:
            # Verification audits structure and binding, not whether the
            # approval window is still open, so a Unit stays verifiable after
            # its Envelope lapses.
            issues.extend(
                _execution_envelope_issues(
                    envelope,
                    str(unit.get("id")),
                    check_expiry=False,
                    maximum_agent_level=maximum_agent_level,
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
        from ..project_knowledge import knowledge_decision_candidate_issues

        issues.extend(knowledge_decision_candidate_issues(unit_dir, decisions))
    issues.extend(_human_document_language_issues(unit_dir, unit, decisions))
    if isinstance(decision_entries, list) and not decision_entries:
        issues.append("at least one recorded decision is required")
    elif isinstance(decision_entries, list):
        if decisions is not None:
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
    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(str(status) if status is not None else "")
    if required_gate and isinstance(decision_entries, list):
        if decisions is not None and not _has_approved_decision(decisions, required_gate):
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
            except WorkflowError as exc:
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
        "title": unit.get("title"),
        "document_language": unit.get("document_language"),
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
        "human_gate": _human_gate_status(unit, decisions),
        "evidence": evidence,
    }


def unit_status(path: str | Path) -> dict[str, Any]:
    result = verify_unit(path)
    result["unit_dir"] = str(Path(path).resolve())
    return result
