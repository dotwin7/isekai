from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from isekai.support.errors import (
    EvidenceError,
    IntegrityError,
    LifecycleError,
    PreflightError,
    WorkflowError,
)
from .common import (
    decision_description_language_issues as _decision_description_language_issues,
    parse_iso_timestamp as _parse_iso_timestamp,
    unit_json as _unit_json,
    unit_maximum_agent_level as _unit_maximum_agent_level,
    unit_preflight_issues as _unit_preflight_issues,
    write_unit_json as _write_unit_json,
    unit_lock,
)
from .artifacts import (
    build_artifact_snapshot,
    gate_artifact_readiness_issues,
    latest_decision_artifact_issues,
)


from .decision_schema import (
    ALLOWED_TRANSITIONS as ALLOWED_TRANSITIONS,
    DECISION_ALLOWED_STATUSES as DECISION_ALLOWED_STATUSES,
    DECISION_GATES as DECISION_GATES,
    DECISION_OUTCOMES as DECISION_OUTCOMES,
    DECISION_PACKET_FIELDS as DECISION_PACKET_FIELDS,
    DECISION_PACKET_VERSION as DECISION_PACKET_VERSION,
    DECISION_REQUIRED_FIELDS as DECISION_REQUIRED_FIELDS,
    LIFECYCLE_STATUSES as LIFECYCLE_STATUSES,
    REQUIRED_DECISIONS_FOR_TRANSITIONS as REQUIRED_DECISIONS_FOR_TRANSITIONS,
    STATUS_PHASE as STATUS_PHASE,
    TERMINAL_STATUSES as TERMINAL_STATUSES,
    decision_ledger_issues,
    decision_packet_issues,
    decision_record_digest,
    decision_record_issues,
    has_approved_decision,
    latest_decision,
)


def approved_envelope_decision_issues(
    unit_dir: Path,
    envelope: dict[str, Any],
    unit: dict[str, Any],
) -> list[str]:
    if envelope.get("status") != "approved":
        return []
    try:
        decisions = _unit_json(unit_dir, "decisions.json")
    except ValueError as exc:
        return [str(exc)]
    ledger_issues = decision_ledger_issues(
        decisions,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if ledger_issues:
        return ledger_issues
    latest = latest_decision(decisions, "inception")
    if latest is None:
        return ["approved Execution Envelope has no Inception Decision"]
    issues = decision_record_issues(
        latest,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if latest.get("outcome") != "approved":
        issues.append("approved Execution Envelope was revoked by the latest Inception Decision")
    else:
        approval_subject = latest.get("approval_subject")
        if not isinstance(approval_subject, dict):
            issues.append("latest Inception Decision has no bound Execution Envelope subject")
        else:
            if approval_subject.get("id") != envelope.get("id"):
                issues.append("Execution Envelope id does not match its Inception Decision")
            if approval_subject.get("digest") != envelope.get("approval_digest"):
                issues.append("Execution Envelope digest does not match its Inception Decision")
        if latest.get("id") != envelope.get("approval_decision_id"):
            issues.append("Execution Envelope approval does not match the latest Inception Decision")
        if latest.get("decision_digest") != envelope.get("approval_decision_digest"):
            issues.append(
                "Execution Envelope approval digest does not match the latest "
                "Inception Decision"
            )
        issues.extend(latest_decision_artifact_issues(unit_dir, decisions, "inception"))
    return issues


def release_decision_evidence_issues(
    unit_dir: Path,
    decisions: dict[str, Any],
    unit: dict[str, Any],
    *,
    require_current: bool = True,
) -> list[str]:
    latest = latest_decision(decisions, "release")
    if latest is None or latest.get("outcome") != "approved":
        return []
    issues = decision_record_issues(
        latest,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    approval_subject = latest.get("approval_subject")
    if not isinstance(approval_subject, dict):
        return issues
    from .evidence import (
        evidence_record_relative as _evidence_record_relative,
    )
    from .evidence_validation import verification_evidence_digest

    evidence_id = approval_subject.get("id")
    try:
        record_relative = _evidence_record_relative(str(evidence_id))
    except ValueError as exc:
        return issues + [str(exc)]
    reference = approval_subject.get("reference")
    if reference is not None and reference != record_relative:
        issues.append("Release Decision has an invalid verification Evidence reference")

    try:
        evidence = _unit_json(unit_dir, record_relative)
    except ValueError:
        # Backward compatibility for Units whose singleton Evidence has not yet
        # been archived. The next Evidence write backfills this record first.
        try:
            current = _unit_json(unit_dir, "evidence/verification.json")
        except ValueError as exc:
            return issues + [str(exc)]
        if current.get("id") != evidence_id:
            issues.append(
                "Release Decision verification Evidence record is missing: "
                + record_relative
            )
            return issues
        evidence = current

    if evidence.get("id") != evidence_id:
        issues.append("Release Decision does not reference its verification Evidence record")
    if approval_subject.get("digest") != verification_evidence_digest(evidence):
        issues.append("Release Decision digest does not match its verification Evidence record")

    if require_current:
        try:
            current = _unit_json(unit_dir, "evidence/verification.json")
        except ValueError as exc:
            return issues + [str(exc)]
        if current.get("id") != evidence_id:
            issues.append(
                "Release Decision does not reference the current verification Evidence"
            )
        if approval_subject.get("digest") != verification_evidence_digest(current):
            issues.append(
                "Release Decision digest does not match current verification Evidence"
            )
    return issues


@dataclass(frozen=True)
class _DecisionRequest:
    gate: str
    outcome: str
    summary: str
    rationale: list[str]
    alternatives: list[dict[str, Any]]
    tradeoffs: list[str]
    risks: list[str]
    references: list[str]
    decided_by: str


def _validated_decision_request(
    *,
    gate: str,
    outcome: str,
    summary: str,
    rationale: list[str],
    alternatives: list[dict[str, Any]],
    tradeoffs: list[str],
    risks: list[str],
    references: list[str],
    decided_by: str,
) -> _DecisionRequest:
    if not isinstance(gate, str) or gate not in DECISION_GATES:
        raise WorkflowError(f"gate must be one of: {', '.join(DECISION_GATES)}")
    if gate == "amendment":
        raise WorkflowError("use the amend action to record an Amendment Decision")
    if not isinstance(outcome, str) or outcome not in DECISION_OUTCOMES:
        raise WorkflowError(f"outcome must be one of: {', '.join(DECISION_OUTCOMES)}")
    if not isinstance(summary, str) or not summary.strip():
        raise WorkflowError("summary must be a non-empty string")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise WorkflowError("decided_by must be a non-empty string")
    packet_issues = decision_packet_issues(
        {
            "decision_packet_version": DECISION_PACKET_VERSION,
            "rationale": rationale,
            "alternatives": alternatives,
            "tradeoffs": tradeoffs,
            "risks": risks,
            "references": references,
        }
    )
    if packet_issues:
        raise IntegrityError("Decision Packet rejected: " + "; ".join(packet_issues))
    return _DecisionRequest(
        gate=gate,
        outcome=outcome,
        summary=summary.strip(),
        rationale=rationale,
        alternatives=alternatives,
        tradeoffs=tradeoffs,
        risks=risks,
        references=references,
        decided_by=decided_by.strip(),
    )


def _decision_preflight(
    unit_dir: Path,
    request: _DecisionRequest,
) -> tuple[dict[str, Any], dict[str, Any], list[Any], list[Any]]:
    unit = _unit_json(unit_dir, "unit.json")
    language_issues = _decision_description_language_issues(
        {
            "summary": request.summary,
            "rationale": request.rationale,
            "alternatives": request.alternatives,
            "tradeoffs": request.tradeoffs,
            "risks": request.risks,
        },
        str(unit.get("document_language")),
    )
    if language_issues:
        raise IntegrityError("Decision Packet rejected: " + "; ".join(language_issues))
    preflight_issues = _unit_preflight_issues(unit_dir)
    if preflight_issues:
        raise PreflightError("Decision preflight blocked: " + "; ".join(preflight_issues))
    current_status = unit.get("status")
    allowed_statuses = DECISION_ALLOWED_STATUSES[request.gate]
    if current_status not in allowed_statuses:
        raise LifecycleError(
            f"{request.gate} Decision cannot be recorded while Unit status is "
            f"{current_status}; allowed statuses: "
            + ", ".join(sorted(allowed_statuses))
        )
    if request.outcome == "approved":
        readiness_issues = gate_artifact_readiness_issues(unit_dir, request.gate)
        if readiness_issues:
            raise IntegrityError(
                f"approved {request.gate} Decision requires materialized Unit artifacts: "
                + "; ".join(readiness_issues)
            )
    decisions = _unit_json(unit_dir, "decisions.json")
    entries = decisions.get("decisions")
    if not isinstance(entries, list):
        raise IntegrityError("decisions.json decisions must be a list")
    if decisions.get("unit_id") != unit.get("id"):
        raise IntegrityError("decisions.json unit_id does not match Unit")
    existing_issues = decision_ledger_issues(
        decisions,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if existing_issues:
        raise IntegrityError(
            "existing Decision history is invalid: " + "; ".join(existing_issues)
        )
    if request.outcome == "approved":
        from .amendment_schema import approval_amendment_issues

        amendment_issues = approval_amendment_issues(
            unit_dir, decisions, request.gate, request.references
        )
        if amendment_issues:
            raise IntegrityError(
                f"approved {request.gate} Decision has unresolved amendments: "
                + "; ".join(amendment_issues)
            )
    return unit, decisions, entries, [
        entry.get("id") if isinstance(entry, dict) else None for entry in entries
    ]


def _decision_approval_subject(
    unit_dir: Path,
    request: _DecisionRequest,
    unit: dict[str, Any],
) -> dict[str, str] | None:
    if request.outcome != "approved":
        return None
    if request.gate == "inception":
        from .execution_schema import execution_envelope_issues

        if "execution-envelope.json" not in request.references:
            raise IntegrityError(
                "approved Inception Decision must reference execution-envelope.json"
            )
        envelope = _unit_json(unit_dir, "execution-envelope.json")
        envelope_issues = execution_envelope_issues(
            envelope,
            str(unit.get("id")),
            maximum_agent_level=_unit_maximum_agent_level(unit_dir),
        )
        if envelope_issues:
            raise IntegrityError(
                "Inception Decision cannot bind an invalid Execution Envelope: "
                + "; ".join(envelope_issues)
            )
        return {
            "type": "execution-envelope",
            "id": str(envelope["id"]),
            "digest": str(envelope["approval_digest"]),
        }
    if request.gate == "release":
        from .evidence import (
            passing_evidence as _passing_evidence,
            persist_evidence_record as _persist_evidence_record,
        )
        from .evidence_validation import verification_evidence_digest

        if "evidence/verification.json" not in request.references:
            raise EvidenceError(
                "approved Release Decision must reference evidence/verification.json"
            )
        if not _passing_evidence(unit_dir):
            raise EvidenceError(
                "approved Release Decision requires current passing verification Evidence"
            )
        evidence = _unit_json(unit_dir, "evidence/verification.json")
        return {
            "type": "verification-evidence",
            "id": str(evidence["id"]),
            "digest": verification_evidence_digest(evidence),
            "reference": _persist_evidence_record(unit_dir, evidence),
        }
    if request.gate == "knowledge":
        from isekai.workflow.project_knowledge import load_project_knowledge_candidate

        candidates = [
            reference
            for reference in request.references
            if reference.replace("\\", "/").startswith(
                "project-knowledge/candidates/"
            )
            and reference.replace("\\", "/").endswith(".json")
        ]
        if len(candidates) != 1:
            raise IntegrityError(
                "approved Knowledge Decision must reference exactly one "
                "Project Knowledge candidate"
            )
        candidate_reference = candidates[0].replace("\\", "/")
        candidate = load_project_knowledge_candidate(
            unit_dir, candidate_reference, require_current_base=True
        )
        return {
            "type": "project-knowledge-candidate",
            "id": str(candidate["id"]),
            "digest": str(candidate["candidate_digest"]),
            "reference": candidate_reference,
        }
    return None


def _decision_timestamp(entries: list[Any]) -> datetime:
    now = datetime.now(timezone.utc)
    if entries:
        previous = entries[-1]
        previous_decided_at = (
            _parse_iso_timestamp(previous.get("decided_at"))
            if isinstance(previous, dict)
            else None
        )
        if previous_decided_at is not None and now <= previous_decided_at:
            return previous_decided_at + timedelta(microseconds=1)
    return now


def _build_decision(
    unit_dir: Path,
    request: _DecisionRequest,
    unit: dict[str, Any],
    entries: list[Any],
    approval_subject: dict[str, str] | None,
) -> dict[str, Any]:
    now = _decision_timestamp(entries)
    decision: dict[str, Any] = {
        "id": "DEC-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "human-decision",
        "schema_version": "1.0.0",
        "unit_id": unit.get("id"),
        "gate": request.gate,
        "outcome": request.outcome,
        "summary": request.summary,
        "scope": unit["scope"],
        "decision_packet_version": DECISION_PACKET_VERSION,
        "rationale": request.rationale,
        "alternatives": request.alternatives,
        "tradeoffs": request.tradeoffs,
        "risks": request.risks,
        "references": request.references,
        "decided_by": request.decided_by,
        "decided_at": now.isoformat(),
        "attestation": {
            "type": "human-decision-attestation",
            "reported_actor": request.decided_by,
            "identity_verification": "not-performed-by-core",
            "confirmation_source": "caller-attested",
        },
        "previous_decision_digest": (
            entries[-1].get("decision_digest")
            if entries and isinstance(entries[-1], dict)
            else None
        ),
    }
    if approval_subject is not None:
        decision["approval_subject"] = approval_subject
    artifact_snapshot = (
        build_artifact_snapshot(unit_dir, request.gate)
        if request.outcome == "approved"
        else None
    )
    if artifact_snapshot is not None:
        decision["artifact_snapshot"] = artifact_snapshot
    decision["decision_digest"] = decision_record_digest(decision)
    return decision


def _persist_decision(
    unit_dir: Path,
    *,
    decisions: dict[str, Any],
    entries: list[Any],
    preceding_ids: list[Any],
    unit: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    entries.append(decision)
    decisions["unit_id"] = unit.get("id")
    _write_unit_json(unit_dir, "decisions.json", decisions)
    persisted_entries = _unit_json(unit_dir, "decisions.json").get("decisions", [])
    persisted_ids = [
        entry.get("id") if isinstance(entry, dict) else None
        for entry in persisted_entries
    ]
    if persisted_ids != [*preceding_ids, decision["id"]]:
        raise IntegrityError(
            "Decision postflight blocked: the Decision ledger changed during the write"
        )


def record_decision(
    path: str | Path,
    *,
    gate: str,
    outcome: str,
    summary: str,
    rationale: list[str],
    alternatives: list[dict[str, Any]],
    tradeoffs: list[str],
    risks: list[str],
    references: list[str],
    decided_by: str,
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise WorkflowError(f"Unit directory does not exist: {unit_dir}")
    request = _validated_decision_request(
        gate=gate,
        outcome=outcome,
        summary=summary,
        rationale=rationale,
        alternatives=alternatives,
        tradeoffs=tradeoffs,
        risks=risks,
        references=references,
        decided_by=decided_by,
    )
    with unit_lock(unit_dir):
        unit, decisions, entries, preceding_ids = _decision_preflight(
            unit_dir, request
        )
        approval_subject = _decision_approval_subject(unit_dir, request, unit)
        decision = _build_decision(
            unit_dir, request, unit, entries, approval_subject
        )
        _persist_decision(
            unit_dir,
            decisions=decisions,
            entries=entries,
            preceding_ids=preceding_ids,
            unit=unit,
            decision=decision,
        )
    return {
        "path": str(unit_dir / "decisions.json"),
        "decision_id": decision["id"],
        "gate": decision["gate"],
        "outcome": decision["outcome"],
        "decision_digest": decision["decision_digest"],
    }
