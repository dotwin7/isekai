from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .common import _unit_json, _unit_preflight_issues, _write_json, unit_lock


LIFECYCLE_STATUSES = (
    "proposed",
    "inception",
    "awaiting-inception-decision",
    "construction",
    "awaiting-release-decision",
    "releasing",
    "operating",
    "learned",
)

ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "proposed": ("inception",),
    "inception": ("awaiting-inception-decision",),
    "awaiting-inception-decision": ("construction",),
    "construction": ("awaiting-release-decision",),
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
        "awaiting-release-decision",
    },
    "architecture": {"construction"},
    "release": {"awaiting-release-decision"},
    "operation": {"operating"},
    "knowledge": {"operating", "learned"},
}
REQUIRED_DECISIONS_FOR_TRANSITIONS = {
    "construction": "inception",
    "awaiting-release-decision": "architecture",
    "releasing": "release",
    "learned": "operation",
}
STATUS_PHASE = {
    "proposed": "inception",
    "inception": "inception",
    "awaiting-inception-decision": "inception",
    "construction": "construction",
    "awaiting-release-decision": "construction",
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
}

DECISION_PACKET_FIELDS = {
    "decision_packet_version",
    "rationale",
    "alternatives",
    "tradeoffs",
    "risks",
    "references",
}


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


def _is_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


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
    approval_subject_types = {
        "inception": "execution-envelope",
        "release": "verification-evidence",
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
    issues.extend(_decision_packet_issues(decision))
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
    if decisions.get("unit_id") != unit.get("id"):
        return ["decisions.json unit_id does not match Unit"]
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
    return issues


def _release_decision_evidence_issues(
    unit_dir: Path,
    decisions: dict[str, Any],
    unit: dict[str, Any],
) -> list[str]:
    latest = _latest_decision(decisions, "release")
    if latest is None or latest.get("outcome") != "approved":
        return []
    issues = _decision_record_issues(
        latest,
        unit_id=str(unit.get("id")),
        scope=str(unit.get("scope")),
    )
    try:
        evidence = _unit_json(unit_dir, "evidence/verification.json")
    except ValueError as exc:
        return issues + [str(exc)]
    from .evidence import _verification_evidence_digest

    approval_subject = latest.get("approval_subject")
    if not isinstance(approval_subject, dict):
        return issues
    if approval_subject.get("id") != evidence.get("id"):
        issues.append("Release Decision does not reference the current verification Evidence")
    if approval_subject.get("digest") != _verification_evidence_digest(evidence):
        issues.append("Release Decision digest does not match current verification Evidence")
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
        raise ValueError(f"Unit directory does not exist: {unit_dir}")
    if gate not in DECISION_GATES:
        raise ValueError(f"gate must be one of: {', '.join(DECISION_GATES)}")
    if outcome not in DECISION_OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(DECISION_OUTCOMES)}")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError("summary must be a non-empty string")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise ValueError("decided_by must be a non-empty string")
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
        raise ValueError("Decision Packet rejected: " + "; ".join(packet_issues))

    with unit_lock(unit_dir):
        unit = _unit_json(unit_dir, "unit.json")
        preflight_issues = _unit_preflight_issues(unit_dir)
        if preflight_issues:
            raise ValueError("Decision preflight blocked: " + "; ".join(preflight_issues))
        current_status = unit.get("status")
        allowed_statuses = DECISION_ALLOWED_STATUSES[gate]
        if current_status not in allowed_statuses:
            raise ValueError(
                f"{gate} Decision cannot be recorded while Unit status is "
                f"{current_status}; allowed statuses: "
                + ", ".join(sorted(allowed_statuses))
            )
        decisions = _unit_json(unit_dir, "decisions.json")
        entries = decisions.get("decisions")
        if not isinstance(entries, list):
            raise ValueError("decisions.json decisions must be a list")
        if decisions.get("unit_id") != unit.get("id"):
            raise ValueError("decisions.json unit_id does not match Unit")
        for index, existing in enumerate(entries):
            existing_issues = _decision_record_issues(
                existing,
                unit_id=str(unit.get("id")),
                scope=str(unit.get("scope")),
            )
            if existing_issues:
                raise ValueError(
                    f"existing Decision {index} is invalid: " + "; ".join(existing_issues)
                )
        preceding_ids = [entry.get("id") for entry in entries]

        approval_subject: dict[str, str] | None = None
        if gate == "inception" and outcome == "approved":
            from .execution import _execution_envelope_issues

            if "execution-envelope.json" not in references:
                raise ValueError(
                    "approved Inception Decision must reference execution-envelope.json"
                )
            envelope = _unit_json(unit_dir, "execution-envelope.json")
            envelope_issues = _execution_envelope_issues(
                envelope, str(unit.get("id"))
            )
            if envelope_issues:
                raise ValueError(
                    "Inception Decision cannot bind an invalid Execution Envelope: "
                    + "; ".join(envelope_issues)
                )
            approval_subject = {
                "type": "execution-envelope",
                "id": str(envelope["id"]),
                "digest": str(envelope["approval_digest"]),
            }
        elif gate == "release" and outcome == "approved":
            from .evidence import _passing_evidence, _verification_evidence_digest

            if "evidence/verification.json" not in references:
                raise ValueError(
                    "approved Release Decision must reference evidence/verification.json"
                )
            if not _passing_evidence(unit_dir):
                raise ValueError(
                    "approved Release Decision requires current passing verification Evidence"
                )
            evidence = _unit_json(unit_dir, "evidence/verification.json")
            approval_subject = {
                "type": "verification-evidence",
                "id": str(evidence["id"]),
                "digest": _verification_evidence_digest(evidence),
            }

        now = datetime.now(timezone.utc)
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
        }
        if approval_subject is not None:
            decision["approval_subject"] = approval_subject
        entries.append(decision)
        decisions["unit_id"] = unit.get("id")
        _write_json(unit_dir / "decisions.json", decisions)
        persisted_entries = _unit_json(unit_dir, "decisions.json").get("decisions", [])
        persisted_ids = [entry.get("id") for entry in persisted_entries]
        # Check that no earlier record was dropped, not merely that this one
        # landed. A lost update leaves the winner's record last and looks fine.
        if persisted_ids != [*preceding_ids, decision["id"]]:
            raise ValueError(
                "Decision postflight blocked: the Decision ledger changed during the write"
            )
    return {"path": str(unit_dir / "decisions.json"), "decision": decision}
