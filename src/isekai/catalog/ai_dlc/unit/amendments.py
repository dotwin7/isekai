from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from isekai.support.errors import IntegrityError, LifecycleError, WorkflowError
from .artifacts import artifact_content_digest
from .authorization import authorization_ledger_digest as _authorization_ledger_digest
from .checkpointing import authorization_progress_cursor
from .common import (
    decision_description_language_issues as _decision_description_language_issues,
    parse_iso_timestamp as _parse_iso_timestamp,
    restore_snapshots as _restore_snapshots,
    unlink_unit_file as _unlink_unit_file,
    unit_bytes as _unit_bytes,
    unit_json as _unit_json,
    unit_path_without_symlinks as _unit_path_without_symlinks,
    write_unit_json as _write_unit_json,
    unit_lock,
)
from .decision_schema import (
    DECISION_PACKET_VERSION,
    LIFECYCLE_STATUSES,
    STATUS_PHASE,
    TERMINAL_STATUSES,
    decision_ledger_issues,
    decision_record_digest,
)


from .amendment_schema import (
    AMENDABLE_ARTIFACT_GATES as AMENDABLE_ARTIFACT_GATES,
    AMENDMENTS_FILE as AMENDMENTS_FILE,
    AMENDMENT_SCHEMA_VERSION as AMENDMENT_SCHEMA_VERSION,
    amendment_digest,
    amendment_ledger_issues,
    amendment_rework_status,
    amendment_status as amendment_status,
    approval_amendment_issues as approval_amendment_issues,
    load_amendment_ledger,
    required_amendment_gate,
    transition_amendment_issues as transition_amendment_issues,
)

@dataclass(frozen=True)
class _AmendmentPreparation:
    unit: dict[str, Any]
    decisions: dict[str, Any]
    ledger: dict[str, Any]
    checkpoint: dict[str, Any]
    envelope: dict[str, Any]
    authorization_ledger: dict[str, Any]
    normalized_artifacts: list[str]
    current_status: str
    required_gate: str
    rework_status: str
    amendment_id: str
    decision_id: str
    amendment: dict[str, Any]
    decision: dict[str, Any]
    now: datetime
    korean: bool


@dataclass(frozen=True)
class _AmendmentMutation:
    unit: dict[str, Any]
    decisions: dict[str, Any]
    ledger: dict[str, Any]
    checkpoint: dict[str, Any]
    evidence: dict[str, Any]


def _validated_amendment_input(
    *,
    request: str,
    reason: str,
    affected_artifacts: list[str],
    requested_by: str,
) -> tuple[str, str, list[str], str]:
    if not isinstance(request, str) or not request.strip():
        raise WorkflowError("amendment request must be a non-empty string")
    if not isinstance(reason, str):
        raise WorkflowError("amendment reason must be a string")
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise WorkflowError("requested_by must be a non-empty string")
    if not isinstance(affected_artifacts, list) or any(
        not isinstance(relative, str) or not relative.strip()
        for relative in affected_artifacts
    ):
        raise WorkflowError(
            "affected_artifacts must be a list of non-empty strings"
        )
    normalized = list(dict.fromkeys(affected_artifacts))
    if not normalized:
        raise WorkflowError("amendment requires at least one affected Unit artifact")
    unsupported = sorted(set(normalized) - AMENDABLE_ARTIFACT_GATES.keys())
    if unsupported:
        raise WorkflowError("unsupported amendment artifacts: " + ", ".join(unsupported))
    return request.strip(), reason.strip(), normalized, requested_by.strip()


def _amendment_timestamp(decision_entries: list[Any]) -> datetime:
    now = datetime.now(timezone.utc)
    if decision_entries:
        previous = decision_entries[-1]
        previous_time = (
            _parse_iso_timestamp(previous.get("decided_at"))
            if isinstance(previous, dict)
            else None
        )
        if previous_time is not None and now <= previous_time:
            return previous_time + timedelta(microseconds=1)
    return now


def _prepare_amendment(
    unit_dir: Path,
    *,
    request: str,
    reason: str,
    normalized_artifacts: list[str],
    requested_by: str,
) -> _AmendmentPreparation:
    unit = _unit_json(unit_dir, "unit.json")
    unit_status = unit.get("status")
    if isinstance(unit_status, str) and unit_status in TERMINAL_STATUSES:
        raise LifecycleError(
            f"a {unit.get('status')} Unit is closed and cannot be amended; start a new Unit"
        )
    if not isinstance(unit_status, str) or unit_status not in LIFECYCLE_STATUSES:
        raise LifecycleError("Unit has an invalid lifecycle status")
    decisions = _unit_json(unit_dir, "decisions.json")
    decision_issues = decision_ledger_issues(
        decisions,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if decision_issues:
        raise IntegrityError(
            "existing Decision history is invalid: " + "; ".join(decision_issues)
        )
    ledger = load_amendment_ledger(unit_dir, str(unit.get("id")))
    ledger_issues = amendment_ledger_issues(
        ledger,
        unit_id=str(unit.get("id")),
        decisions=decisions,
    )
    if ledger_issues:
        raise IntegrityError(
            "existing amendment history is invalid: " + "; ".join(ledger_issues)
        )
    checkpoint = _unit_json(unit_dir, "checkpoint.json")
    envelope = _unit_json(unit_dir, "execution-envelope.json")
    authorization_ledger = _unit_json(unit_dir, "execution-authorizations.json")
    _unit_json(unit_dir, "evidence/verification.json")
    decision_entries = decisions.get("decisions")
    amendment_entries = ledger.get("amendments")
    if not isinstance(decision_entries, list) or not isinstance(amendment_entries, list):
        raise IntegrityError("Unit amendment ledgers must contain lists")
    current_status = unit_status
    required_gate = required_amendment_gate(normalized_artifacts)
    rework_status = amendment_rework_status(current_status, required_gate)
    now = _amendment_timestamp(decision_entries)
    stamp = now.strftime("%Y%m%d%H%M%S%f")
    amendment_id = "AMD-" + stamp
    decision_id = "DEC-" + stamp
    localized_reason = reason or (
        "사용자가 활성 Unit의 변경을 요청했다."
        if unit.get("document_language") == "ko"
        else "The user requested a change to the active Unit."
    )
    amendment: dict[str, Any] = {
        "id": amendment_id,
        "type": "unit-amendment",
        "schema_version": AMENDMENT_SCHEMA_VERSION,
        "unit_id": unit.get("id"),
        "request": request,
        "reason": localized_reason,
        "affected_artifacts": normalized_artifacts,
        "baseline_artifacts": [
            {
                "reference": relative,
                "digest": artifact_content_digest(unit_dir, relative),
            }
            for relative in normalized_artifacts
        ],
        "required_gate": required_gate,
        "from_status": current_status,
        "rework_status": rework_status,
        "requested_by": requested_by,
        "requested_at": now.isoformat(),
        "decision_id": decision_id,
        "previous_amendment_digest": (
            amendment_entries[-1].get("amendment_digest")
            if amendment_entries and isinstance(amendment_entries[-1], dict)
            else None
        ),
    }
    amendment["amendment_digest"] = amendment_digest(
        amendment, "amendment_digest"
    )
    korean = unit.get("document_language") == "ko"
    decision: dict[str, Any] = {
        "id": decision_id,
        "type": "human-decision",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "gate": "amendment",
        "outcome": "approved",
        "summary": (
            f"활성 Unit 변경 요청을 기록한다: {request}"
            if korean
            else f"Record the active Unit amendment: {request}"
        ),
        "scope": unit.get("scope"),
        "decision_packet_version": DECISION_PACKET_VERSION,
        "rationale": [localized_reason],
        "alternatives": [],
        "tradeoffs": (
            ["변경된 결과는 현재 Unit에서 다시 검증하고 승인한다."]
            if korean
            else ["The changed result must be reverified and approved in this Unit."]
        ),
        "risks": (
            ["변경 문서나 재승인을 누락하면 이전 승인과 결과가 어긋난다."]
            if korean
            else ["Missing documentation or reapproval would leave stale approval."]
        ),
        "references": [AMENDMENTS_FILE, *normalized_artifacts],
        "decided_by": requested_by,
        "decided_at": now.isoformat(),
        "attestation": {
            "type": "human-decision-attestation",
            "reported_actor": requested_by,
            "identity_verification": "not-performed-by-core",
            "confirmation_source": "caller-attested",
        },
        "approval_subject": {
            "type": "unit-amendment",
            "id": amendment_id,
            "digest": amendment["amendment_digest"],
            "reference": f"{AMENDMENTS_FILE}#{amendment_id}",
        },
        "previous_decision_digest": (
            decision_entries[-1].get("decision_digest")
            if decision_entries and isinstance(decision_entries[-1], dict)
            else None
        ),
    }
    language_issues = _decision_description_language_issues(
        decision, str(unit.get("document_language"))
    )
    if language_issues:
        raise IntegrityError("Amendment Decision rejected: " + "; ".join(language_issues))
    decision["decision_digest"] = decision_record_digest(decision)
    candidate_decisions = {**decisions, "decisions": [*decision_entries, decision]}
    candidate_ledger = {**ledger, "amendments": [*amendment_entries, amendment]}
    candidate_issues = decision_ledger_issues(
        candidate_decisions,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    candidate_issues.extend(
        amendment_ledger_issues(
            candidate_ledger,
            unit_id=str(unit.get("id")),
            decisions=candidate_decisions,
        )
    )
    if candidate_issues:
        raise IntegrityError("Unit amendment rejected: " + "; ".join(candidate_issues))
    return _AmendmentPreparation(
        unit=unit,
        decisions=candidate_decisions,
        ledger=candidate_ledger,
        checkpoint=checkpoint,
        envelope=envelope,
        authorization_ledger=authorization_ledger,
        normalized_artifacts=normalized_artifacts,
        current_status=current_status,
        required_gate=required_gate,
        rework_status=rework_status,
        amendment_id=amendment_id,
        decision_id=decision_id,
        amendment=amendment,
        decision=decision,
        now=now,
        korean=korean,
    )


def _amendment_mutation(
    unit_dir: Path,
    prepared: _AmendmentPreparation,
    request: str,
) -> _AmendmentMutation:
    candidate_unit = {
        **prepared.unit,
        "status": prepared.rework_status,
        "phase": STATUS_PHASE[prepared.rework_status],
    }
    pending_label = f"{prepared.amendment_id}: {request}"
    candidate_checkpoint = {
        **prepared.checkpoint,
        "unit_id": prepared.unit.get("id"),
        "pending": [
            *[
                item
                for item in prepared.checkpoint.get("pending", [])
                if isinstance(item, str) and item.strip()
            ],
            pending_label,
        ],
        "blocked_by": [],
        "next_action": (
            "변경 요청을 관련 Unit 문서와 구현에 반영한다."
            if prepared.korean
            else "Apply the amendment to the affected Unit artifacts and implementation."
        ),
        "authorization_cursor": authorization_progress_cursor(
            unit_dir, prepared.authorization_ledger
        ),
        "updated_at": prepared.now.isoformat(),
    }
    candidate_evidence: dict[str, Any] = {
        "id": "",
        "type": "verification-evidence",
        "schema_version": "1.0.0",
        "unit_id": prepared.unit.get("id"),
        "stage": STATUS_PHASE[prepared.rework_status],
        "passed": False,
        "scope": "",
        "recorded_by": "",
        "recorded_at": "",
        "commands": [],
        "envelope_id": prepared.envelope.get("id"),
        "envelope_digest": prepared.envelope.get("approval_digest"),
        "authorization_ledger_digest": _authorization_ledger_digest(
            prepared.authorization_ledger
        ),
        "authorization_count": len(prepared.authorization_ledger.get("grants", [])),
    }
    return _AmendmentMutation(
        unit=candidate_unit,
        decisions=prepared.decisions,
        ledger=prepared.ledger,
        checkpoint=candidate_checkpoint,
        evidence=candidate_evidence,
    )


def _commit_amendment(
    unit_dir: Path,
    prepared: _AmendmentPreparation,
    mutation: _AmendmentMutation,
) -> None:
    amendment_path = _unit_path_without_symlinks(unit_dir, AMENDMENTS_FILE)
    amendment_existed = amendment_path.exists()
    snapshots = [
        (unit_dir / "unit.json", _unit_bytes(unit_dir, "unit.json")),
        (unit_dir / "decisions.json", _unit_bytes(unit_dir, "decisions.json")),
        (unit_dir / "checkpoint.json", _unit_bytes(unit_dir, "checkpoint.json")),
        (
            unit_dir / "evidence/verification.json",
            _unit_bytes(unit_dir, "evidence/verification.json"),
        ),
    ]
    if amendment_existed:
        snapshots.append((amendment_path, _unit_bytes(unit_dir, AMENDMENTS_FILE)))
    try:
        _write_unit_json(unit_dir, AMENDMENTS_FILE, mutation.ledger)
        _write_unit_json(unit_dir, "decisions.json", mutation.decisions)
        _write_unit_json(unit_dir, "unit.json", mutation.unit)
        _write_unit_json(unit_dir, "checkpoint.json", mutation.checkpoint)
        _write_unit_json(unit_dir, "evidence/verification.json", mutation.evidence)
        persisted = load_amendment_ledger(
            unit_dir, str(prepared.unit.get("id"))
        )
        persisted_decisions = _unit_json(unit_dir, "decisions.json")
        postflight = amendment_ledger_issues(
            persisted,
            unit_id=str(prepared.unit.get("id")),
            decisions=persisted_decisions,
        )
        if postflight:
            raise IntegrityError(
                "Unit amendment postflight failed: " + "; ".join(postflight)
            )
    except Exception as exc:
        _restore_snapshots(snapshots, "Unit amendment", exc, root=unit_dir)
        if not amendment_existed:
            try:
                _unlink_unit_file(unit_dir, AMENDMENTS_FILE, missing_ok=True)
            except Exception as cleanup_exc:
                raise IntegrityError(
                    "Unit amendment failed and its new ledger could not be removed: "
                    f"{cleanup_exc}"
                ) from exc
        raise


def record_unit_amendment(
    path: str | Path,
    *,
    request: str,
    reason: str,
    affected_artifacts: list[str],
    requested_by: str,
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise WorkflowError(f"Unit directory does not exist: {unit_dir}")
    request, reason, normalized_artifacts, requested_by = _validated_amendment_input(
        request=request,
        reason=reason,
        affected_artifacts=affected_artifacts,
        requested_by=requested_by,
    )
    with unit_lock(unit_dir):
        prepared = _prepare_amendment(
            unit_dir,
            request=request,
            reason=reason,
            normalized_artifacts=normalized_artifacts,
            requested_by=requested_by,
        )
        mutation = _amendment_mutation(unit_dir, prepared, request)
        _commit_amendment(unit_dir, prepared, mutation)
    return {
        "path": str(unit_dir / AMENDMENTS_FILE),
        "amendment_id": prepared.amendment_id,
        "decision_id": prepared.decision_id,
        "required_gate": prepared.required_gate,
        "from_status": prepared.current_status,
        "status": prepared.rework_status,
        "affected_artifacts": prepared.normalized_artifacts,
    }
