from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..errors import (
    EvidenceError,
    IntegrityError,
    LifecycleError,
    PreflightError,
    WorkflowError,
)
from .common import (
    _is_iso_timestamp,
    _parse_iso_timestamp,
    _unit_json,
    _unit_maximum_agent_level,
    _unit_preflight_issues,
    _write_json,
    unit_lock,
)


LIFECYCLE_STATUSES = (
    "proposed",
    "inception",
    "awaiting-inception-decision",
    "construction",
    "validation",
    "awaiting-release-decision",
    "releasing",
    "operating",
    "learned",
)

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "proposed": ("inception",),
    "inception": ("awaiting-inception-decision",),
    "awaiting-inception-decision": ("construction",),
    "construction": ("validation",),
    "validation": ("awaiting-release-decision",),
    "awaiting-release-decision": ("releasing",),
    "releasing": ("operating",),
    "operating": ("learned",),
    "learned": (),
}

DECISION_GATES = ("inception", "architecture", "release", "operation", "knowledge")
DECISION_OUTCOMES = ("approved", "rejected")
DECISION_ALLOWED_STATUSES = {
    # Inception Decisions are also used to revoke or renew an Execution Envelope
    # while work is underway. Other gates are only meaningful immediately before
    # the lifecycle edge they govern, so they cannot be pre-approved.
    "inception": {
        "awaiting-inception-decision",
        "construction",
        "validation",
        "awaiting-release-decision",
        "releasing",
        "operating",
    },
    "architecture": {"construction"},
    # Release-stage work can stale the Evidence that the original Release
    # Decision approved. A fresh Decision in ``releasing`` is the recovery path
    # before entering Operations; it still has to bind the current Evidence.
    "release": {"awaiting-release-decision", "releasing"},
    "operation": {"operating"},
    "knowledge": {"operating", "learned"},
}
REQUIRED_DECISIONS_FOR_TRANSITIONS = {
    "construction": "inception",
    "validation": "architecture",
    "awaiting-release-decision": "architecture",
    "releasing": "release",
    "operating": "release",
    "learned": "operation",
}
STATUS_PHASE = {
    "proposed": "inception",
    "inception": "inception",
    "awaiting-inception-decision": "inception",
    "construction": "construction",
    "validation": "validation",
    "awaiting-release-decision": "validation",
    "releasing": "release",
    "operating": "operations",
    "learned": "operations",
}
DECISION_PACKET_VERSION = "1.0.0"
DECISION_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "gate",
    "outcome",
    "summary",
    "scope",
    "decision_packet_version",
    "rationale",
    "alternatives",
    "tradeoffs",
    "risks",
    "references",
    "decided_by",
    "decided_at",
    "previous_decision_digest",
    "decision_digest",
}

DECISION_PACKET_FIELDS = {
    "decision_packet_version",
    "rationale",
    "alternatives",
    "tradeoffs",
    "risks",
    "references",
}
_HANGUL = re.compile(r"[가-힣]")


def _decision_record_digest(decision: dict[str, Any]) -> str:
    """Bind every auditable Decision field to one canonical digest."""
    subject = {
        key: value for key, value in decision.items() if key != "decision_digest"
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _decision_packet_issues(decision: Any) -> list[str]:
    if not isinstance(decision, dict):
        return ["Decision Packet must be an object"]
    issues: list[str] = []
    missing = sorted(DECISION_PACKET_FIELDS - decision.keys())
    if missing:
        issues.append(f"Decision Packet missing fields: {', '.join(missing)}")
    if decision.get("decision_packet_version") != DECISION_PACKET_VERSION:
        issues.append("Decision Packet has an unsupported version")
    rationale = decision.get("rationale")
    if not isinstance(rationale, list) or not rationale or any(
        not isinstance(item, str) or not item.strip() for item in rationale
    ):
        issues.append("Decision Packet rationale must be a non-empty list of strings")
    for field in ("tradeoffs", "risks", "references"):
        value = decision.get(field)
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            issues.append(f"Decision Packet {field} must be a list of strings")
    alternatives = decision.get("alternatives")
    if not isinstance(alternatives, list):
        issues.append("Decision Packet alternatives must be a list")
    else:
        for index, alternative in enumerate(alternatives):
            if not isinstance(alternative, dict):
                issues.append(f"Decision Packet alternative {index} must be an object")
                continue
            if not isinstance(alternative.get("option"), str) or not alternative["option"].strip():
                issues.append(f"Decision Packet alternative {index} needs option")
            if not isinstance(alternative.get("reason"), str) or not alternative["reason"].strip():
                issues.append(f"Decision Packet alternative {index} needs reason")
    return issues


def _decision_description_language_issues(
    decision: dict[str, Any],
    document_language: str,
) -> list[str]:
    if document_language != "ko":
        return []
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
    return [
        f"{field} must use Korean for document_language ko"
        for field, value in descriptions
        if isinstance(value, str) and value.strip() and not _HANGUL.search(value)
    ]


def _decision_attestation_issues(decision: dict[str, Any]) -> list[str]:
    """Validate optional trust metadata without invalidating legacy records."""
    attestation = decision.get("attestation")
    if attestation is None:
        return []
    if not isinstance(attestation, dict):
        return ["Decision attestation must be an object"]
    issues: list[str] = []
    if attestation.get("type") != "human-decision-attestation":
        issues.append("Decision attestation has an invalid type")
    if attestation.get("reported_actor") != decision.get("decided_by"):
        issues.append("Decision attestation reported_actor must match decided_by")
    if attestation.get("identity_verification") != "not-performed-by-core":
        issues.append(
            "Decision attestation identity_verification must disclose the Core boundary"
        )
    if attestation.get("confirmation_source") != "caller-attested":
        issues.append("Decision attestation has an invalid confirmation_source")
    return issues


def _decision_record_issues(
    decision: Any,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> list[str]:
    if not isinstance(decision, dict):
        return ["Decision must be an object"]
    issues: list[str] = []
    missing = sorted(DECISION_REQUIRED_FIELDS - decision.keys())
    if missing:
        issues.append(f"Decision missing fields: {', '.join(missing)}")
    if decision.get("type") != "human-decision":
        issues.append("Decision has an invalid type")
    if decision.get("schema_version") != "1.0.0":
        issues.append("Decision has an unsupported schema_version")
    if unit_id is not None and decision.get("unit_id") != unit_id:
        issues.append("Decision unit_id does not match Unit")
    if decision.get("gate") not in DECISION_GATES:
        issues.append("Decision has an invalid gate")
    if decision.get("outcome") not in DECISION_OUTCOMES:
        issues.append("Decision has an invalid outcome")
    for field in ("id", "summary", "scope", "decided_by"):
        if not isinstance(decision.get(field), str) or not decision.get(field, "").strip():
            issues.append(f"Decision requires a non-empty {field}")
    if scope is not None and decision.get("scope") != scope:
        issues.append("Decision scope does not match Unit")
    if not _is_iso_timestamp(decision.get("decided_at")):
        issues.append("Decision decided_at must be an ISO-8601 timestamp")
    decision_digest = decision.get("decision_digest")
    if not isinstance(decision_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", decision_digest
    ):
        issues.append("Decision decision_digest must be a SHA-256 digest")
    elif decision_digest != _decision_record_digest(decision):
        issues.append("Decision digest does not match its record")
    previous_digest = decision.get("previous_decision_digest")
    if previous_digest is not None and not (
        isinstance(previous_digest, str)
        and re.fullmatch(r"sha256:[0-9a-f]{64}", previous_digest)
    ):
        issues.append(
            "Decision previous_decision_digest must be null or a SHA-256 digest"
        )
    issues.extend(_decision_attestation_issues(decision))
    approval_subject_types = {
        "inception": "execution-envelope",
        "release": "verification-evidence",
        "knowledge": "project-knowledge-candidate",
    }
    expected_subject_type = approval_subject_types.get(str(decision.get("gate")))
    if expected_subject_type is not None and decision.get("outcome") == "approved":
        approval_subject = decision.get("approval_subject")
        if not isinstance(approval_subject, dict):
            issues.append(
                f"approved {decision.get('gate')} Decision requires approval_subject"
            )
        else:
            if approval_subject.get("type") != expected_subject_type:
                issues.append(
                    f"{decision.get('gate')} Decision approval_subject type is invalid"
                )
            if not isinstance(approval_subject.get("id"), str) or not approval_subject.get(
                "id", ""
            ).strip():
                issues.append(
                    f"{decision.get('gate')} Decision approval_subject requires id"
                )
            if not isinstance(approval_subject.get("digest"), str) or not re.fullmatch(
                r"sha256:[0-9a-f]{64}", approval_subject.get("digest", "")
            ):
                issues.append(
                    f"{decision.get('gate')} Decision approval_subject requires SHA-256 digest"
                )
            if expected_subject_type == "project-knowledge-candidate" and (
                not isinstance(approval_subject.get("reference"), str)
                or not approval_subject.get("reference", "").strip()
            ):
                issues.append(
                    "knowledge Decision approval_subject requires candidate reference"
                )
    issues.extend(_decision_packet_issues(decision))
    return issues


def _decision_ledger_issues(
    decisions: Any,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> list[str]:
    """Validate Decision records and their append-only digest chain."""
    if not isinstance(decisions, dict):
        return ["decisions.json must be an object"]
    issues: list[str] = []
    if unit_id is not None and decisions.get("unit_id") != unit_id:
        issues.append("decisions.json unit_id does not match Unit")
    entries = decisions.get("decisions")
    if not isinstance(entries, list):
        return issues + ["decisions.json decisions must be a list"]

    seen_ids: set[str] = set()
    previous_digest: str | None = None
    previous_decided_at: datetime | None = None
    for index, decision in enumerate(entries):
        issues.extend(
            f"decision {index}: {issue}"
            for issue in _decision_record_issues(
                decision,
                unit_id=unit_id,
                scope=scope,
            )
        )
        if not isinstance(decision, dict):
            continue
        decision_id = decision.get("id")
        if isinstance(decision_id, str):
            if decision_id in seen_ids:
                issues.append(f"decision {index}: duplicate Decision id")
            seen_ids.add(decision_id)
        if decision.get("previous_decision_digest") != previous_digest:
            issues.append(
                f"decision {index}: Decision does not continue the digest chain"
            )
        decided_at = _parse_iso_timestamp(decision.get("decided_at"))
        if (
            decided_at is not None
            and previous_decided_at is not None
            and decided_at <= previous_decided_at
        ):
            issues.append(
                f"decision {index}: decided_at must be later than the previous Decision"
            )
        if decided_at is not None:
            previous_decided_at = decided_at
        digest = decision.get("decision_digest")
        previous_digest = digest if isinstance(digest, str) else None
    return issues

def _latest_decision(decisions: dict[str, Any], gate: str) -> dict[str, Any] | None:
    entries = decisions.get("decisions", [])
    if not isinstance(entries, list):
        return None
    for entry in reversed(entries):
        if isinstance(entry, dict) and entry.get("gate") == gate:
            return entry
    return None


def _has_approved_decision(
    decisions: dict[str, Any],
    gate: str,
    *,
    unit_id: str | None = None,
    scope: str | None = None,
) -> bool:
    if _decision_ledger_issues(decisions, unit_id=unit_id, scope=scope):
        return False
    latest = _latest_decision(decisions, gate)
    return (
        latest is not None
        and latest.get("outcome") == "approved"
        and not _decision_record_issues(latest, unit_id=unit_id, scope=scope)
    )


def _approved_envelope_decision_issues(
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
    ledger_issues = _decision_ledger_issues(
        decisions,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    if ledger_issues:
        return ledger_issues
    latest = _latest_decision(decisions, "inception")
    if latest is None:
        return ["approved Execution Envelope has no Inception Decision"]
    issues = _decision_record_issues(
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
    return issues


def _release_decision_evidence_issues(
    unit_dir: Path,
    decisions: dict[str, Any],
    unit: dict[str, Any],
    *,
    require_current: bool = True,
) -> list[str]:
    latest = _latest_decision(decisions, "release")
    if latest is None or latest.get("outcome") != "approved":
        return []
    issues = _decision_record_issues(
        latest,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    approval_subject = latest.get("approval_subject")
    if not isinstance(approval_subject, dict):
        return issues
    from .evidence import (
        _evidence_record_relative,
        _verification_evidence_digest,
    )

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
    if approval_subject.get("digest") != _verification_evidence_digest(evidence):
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
        if approval_subject.get("digest") != _verification_evidence_digest(current):
            issues.append(
                "Release Decision digest does not match current verification Evidence"
            )
    return issues


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
    if gate not in DECISION_GATES:
        raise WorkflowError(f"gate must be one of: {', '.join(DECISION_GATES)}")
    if outcome not in DECISION_OUTCOMES:
        raise WorkflowError(f"outcome must be one of: {', '.join(DECISION_OUTCOMES)}")
    if not isinstance(summary, str) or not summary.strip():
        raise WorkflowError("summary must be a non-empty string")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise WorkflowError("decided_by must be a non-empty string")
    packet_issues = _decision_packet_issues(
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

    with unit_lock(unit_dir):
        unit = _unit_json(unit_dir, "unit.json")
        language_issues = _decision_description_language_issues(
            {
                "summary": summary,
                "rationale": rationale,
                "alternatives": alternatives,
                "tradeoffs": tradeoffs,
                "risks": risks,
            },
            str(unit.get("document_language")),
        )
        if language_issues:
            raise IntegrityError(
                "Decision Packet rejected: " + "; ".join(language_issues)
            )
        preflight_issues = _unit_preflight_issues(unit_dir)
        if preflight_issues:
            raise PreflightError("Decision preflight blocked: " + "; ".join(preflight_issues))
        current_status = unit.get("status")
        allowed_statuses = DECISION_ALLOWED_STATUSES[gate]
        if current_status not in allowed_statuses:
            raise LifecycleError(
                f"{gate} Decision cannot be recorded while Unit status is "
                f"{current_status}; allowed statuses: "
                + ", ".join(sorted(allowed_statuses))
            )
        decisions = _unit_json(unit_dir, "decisions.json")
        entries = decisions.get("decisions")
        if not isinstance(entries, list):
            raise IntegrityError("decisions.json decisions must be a list")
        if decisions.get("unit_id") != unit.get("id"):
            raise IntegrityError("decisions.json unit_id does not match Unit")
        existing_issues = _decision_ledger_issues(
            decisions,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        )
        if existing_issues:
            raise IntegrityError(
                "existing Decision history is invalid: "
                + "; ".join(existing_issues)
            )
        preceding_ids = [entry.get("id") for entry in entries]

        approval_subject: dict[str, str] | None = None
        if gate == "inception" and outcome == "approved":
            from .execution import _execution_envelope_issues

            if "execution-envelope.json" not in references:
                raise IntegrityError(
                    "approved Inception Decision must reference execution-envelope.json"
                )
            envelope = _unit_json(unit_dir, "execution-envelope.json")
            envelope_issues = _execution_envelope_issues(
                envelope,
                str(unit.get("id")),
                maximum_agent_level=_unit_maximum_agent_level(unit_dir),
            )
            if envelope_issues:
                raise IntegrityError(
                    "Inception Decision cannot bind an invalid Execution Envelope: "
                    + "; ".join(envelope_issues)
                )
            approval_subject = {
                "type": "execution-envelope",
                "id": str(envelope["id"]),
                "digest": str(envelope["approval_digest"]),
            }
        elif gate == "release" and outcome == "approved":
            from .evidence import (
                _passing_evidence,
                _persist_evidence_record,
                _verification_evidence_digest,
            )

            if "evidence/verification.json" not in references:
                raise EvidenceError(
                    "approved Release Decision must reference evidence/verification.json"
                )
            if not _passing_evidence(unit_dir):
                raise EvidenceError(
                    "approved Release Decision requires current passing verification Evidence"
                )
            evidence = _unit_json(unit_dir, "evidence/verification.json")
            evidence_reference = _persist_evidence_record(unit_dir, evidence)
            approval_subject = {
                "type": "verification-evidence",
                "id": str(evidence["id"]),
                "digest": _verification_evidence_digest(evidence),
                "reference": evidence_reference,
            }
        elif gate == "knowledge" and outcome == "approved":
            from ..project_knowledge import load_project_knowledge_candidate

            candidate_references = [
                reference
                for reference in references
                if isinstance(reference, str)
                and reference.replace("\\", "/").startswith(
                    "project-knowledge/candidates/"
                )
                and reference.replace("\\", "/").endswith(".json")
            ]
            if len(candidate_references) != 1:
                raise IntegrityError(
                    "approved Knowledge Decision must reference exactly one "
                    "Project Knowledge candidate"
                )
            candidate_reference = candidate_references[0].replace("\\", "/")
            candidate = load_project_knowledge_candidate(
                unit_dir, candidate_reference, require_current_base=True
            )
            approval_subject = {
                "type": "project-knowledge-candidate",
                "id": str(candidate["id"]),
                "digest": str(candidate["candidate_digest"]),
                "reference": candidate_reference,
            }

        now = datetime.now(timezone.utc)
        if entries:
            previous_decided_at = _parse_iso_timestamp(entries[-1].get("decided_at"))
            if previous_decided_at is not None and now <= previous_decided_at:
                now = previous_decided_at + timedelta(microseconds=1)
        decision = {
            "id": "DEC-" + now.strftime("%Y%m%d%H%M%S%f"),
            "type": "human-decision",
            "schema_version": "1.0.0",
            "unit_id": unit.get("id"),
            "gate": gate,
            "outcome": outcome,
            "summary": summary.strip(),
            "scope": unit["scope"],
            "decision_packet_version": DECISION_PACKET_VERSION,
            "rationale": rationale,
            "alternatives": alternatives,
            "tradeoffs": tradeoffs,
            "risks": risks,
            "references": references,
            "decided_by": decided_by.strip(),
            "decided_at": now.isoformat(),
            "attestation": {
                "type": "human-decision-attestation",
                "reported_actor": decided_by.strip(),
                "identity_verification": "not-performed-by-core",
                "confirmation_source": "caller-attested",
            },
            "previous_decision_digest": (
                entries[-1].get("decision_digest") if entries else None
            ),
        }
        if approval_subject is not None:
            decision["approval_subject"] = approval_subject
        decision["decision_digest"] = _decision_record_digest(decision)
        entries.append(decision)
        decisions["unit_id"] = unit.get("id")
        _write_json(unit_dir / "decisions.json", decisions)
        persisted_entries = _unit_json(unit_dir, "decisions.json").get("decisions", [])
        persisted_ids = [entry.get("id") for entry in persisted_entries]
        # Check that no earlier record was dropped, not merely that this one
        # landed. A lost update leaves the winner's record last and looks fine.
        if persisted_ids != [*preceding_ids, decision["id"]]:
            raise IntegrityError(
                "Decision postflight blocked: the Decision ledger changed during the write"
            )
    return {"path": str(unit_dir / "decisions.json"), "decision": decision}
