from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from isekai.support.errors import IntegrityError, LifecycleError, WorkflowError
from .artifacts import artifact_content_digest
from .authorization import _authorization_ledger_digest
from .checkpointing import authorization_progress_cursor
from .common import (
    _restore_snapshots,
    _unit_bytes,
    _unit_json,
    _unit_path_without_symlinks,
    _write_json,
    unit_lock,
)
from .decisions import (
    DECISION_PACKET_VERSION,
    LIFECYCLE_STATUSES,
    STATUS_PHASE,
    TERMINAL_STATUSES,
    _decision_description_language_issues,
    _decision_ledger_issues,
    _decision_record_digest,
    _parse_iso_timestamp,
)


AMENDMENT_SCHEMA_VERSION = "1.0.0"
AMENDMENTS_FILE = "amendments.json"
AMENDABLE_ARTIFACT_GATES = {
    "intent.md": "inception",
    "requirements.md": "inception",
    "plan.md": "inception",
    "acceptance.md": "inception",
    "architecture.md": "architecture",
    "implementation-guide.md": "architecture",
    "release.md": "release",
    "operations.md": "operation",
}
_GATE_PRECEDENCE = ("inception", "architecture", "release", "operation")
_GATE_REWORK_STATUS = {
    "inception": "inception",
    "architecture": "construction",
    "release": "validation",
    "operation": "operating",
}
_STATUS_ORDER = {status: index for index, status in enumerate(LIFECYCLE_STATUSES)}
_AMENDMENT_REQUIRED_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "request",
    "reason",
    "affected_artifacts",
    "baseline_artifacts",
    "required_gate",
    "from_status",
    "rework_status",
    "requested_by",
    "requested_at",
    "decision_id",
    "previous_amendment_digest",
    "amendment_digest",
}


def _canonical_digest(value: dict[str, Any], digest_field: str) -> str:
    subject = {key: item for key, item in value.items() if key != digest_field}
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _empty_amendment_ledger(unit_id: str) -> dict[str, Any]:
    return {
        "type": "unit-amendment-ledger",
        "schema_version": AMENDMENT_SCHEMA_VERSION,
        "unit_id": unit_id,
        "amendments": [],
    }


def _load_amendment_ledger(unit_dir: Path, unit_id: str) -> dict[str, Any]:
    path = _unit_path_without_symlinks(unit_dir, AMENDMENTS_FILE)
    if not path.exists():
        return _empty_amendment_ledger(unit_id)
    return _unit_json(unit_dir, AMENDMENTS_FILE)


def _amendment_ledger_issues(
    ledger: Any,
    *,
    unit_id: str,
    decisions: dict[str, Any] | None = None,
) -> list[str]:
    if not isinstance(ledger, dict):
        return ["amendments.json must be an object"]
    issues: list[str] = []
    if ledger.get("type") != "unit-amendment-ledger":
        issues.append("amendments.json has an invalid type")
    if ledger.get("schema_version") != AMENDMENT_SCHEMA_VERSION:
        issues.append("amendments.json has an unsupported schema_version")
    if ledger.get("unit_id") != unit_id:
        issues.append("amendments.json unit_id does not match Unit")
    entries = ledger.get("amendments")
    if not isinstance(entries, list):
        return issues + ["amendments.json amendments must be a list"]
    decision_by_id = {
        item.get("id"): item
        for item in (decisions or {}).get("decisions", [])
        if isinstance(item, dict)
    }
    previous_digest: str | None = None
    seen_ids: set[str] = set()
    for index, amendment in enumerate(entries):
        if not isinstance(amendment, dict):
            issues.append(f"amendment {index} must be an object")
            continue
        missing = sorted(_AMENDMENT_REQUIRED_FIELDS - amendment.keys())
        if missing:
            issues.append(f"amendment {index} missing fields: {', '.join(missing)}")
        amendment_id = amendment.get("id")
        if not isinstance(amendment_id, str) or not amendment_id.strip():
            issues.append(f"amendment {index} requires id")
        elif amendment_id in seen_ids:
            issues.append(f"amendment {index} has a duplicate id")
        else:
            seen_ids.add(amendment_id)
        if amendment.get("type") != "unit-amendment":
            issues.append(f"amendment {index} has an invalid type")
        if amendment.get("schema_version") != AMENDMENT_SCHEMA_VERSION:
            issues.append(f"amendment {index} has an unsupported schema_version")
        if amendment.get("unit_id") != unit_id:
            issues.append(f"amendment {index} unit_id does not match Unit")
        for field in ("request", "reason", "requested_by"):
            if not isinstance(amendment.get(field), str) or not amendment.get(
                field, ""
            ).strip():
                issues.append(f"amendment {index} requires {field}")
        if amendment.get("required_gate") not in _GATE_PRECEDENCE:
            issues.append(f"amendment {index} has an invalid required_gate")
        if amendment.get("from_status") not in LIFECYCLE_STATUSES:
            issues.append(f"amendment {index} has an invalid from_status")
        if amendment.get("rework_status") not in LIFECYCLE_STATUSES:
            issues.append(f"amendment {index} has an invalid rework_status")
        if _parse_iso_timestamp(amendment.get("requested_at")) is None:
            issues.append(f"amendment {index} has an invalid requested_at")
        affected = amendment.get("affected_artifacts")
        if not isinstance(affected, list) or not affected:
            issues.append(f"amendment {index} requires affected_artifacts")
            affected = []
        elif any(item not in AMENDABLE_ARTIFACT_GATES for item in affected):
            issues.append(f"amendment {index} has an unsupported affected artifact")
        baseline = amendment.get("baseline_artifacts")
        baseline_references: list[str] = []
        if not isinstance(baseline, list):
            issues.append(f"amendment {index} baseline_artifacts must be a list")
            baseline = []
        for item in baseline:
            if not isinstance(item, dict):
                issues.append(f"amendment {index} has an invalid baseline artifact")
                continue
            reference = item.get("reference")
            digest = item.get("digest")
            if isinstance(reference, str):
                baseline_references.append(reference)
            if not isinstance(digest, str) or re.fullmatch(
                r"sha256:[0-9a-f]{64}", digest
            ) is None:
                issues.append(f"amendment {index} baseline requires SHA-256 digest")
        if baseline_references != affected:
            issues.append(f"amendment {index} baseline does not match affected artifacts")
        if amendment.get("previous_amendment_digest") != previous_digest:
            issues.append(f"amendment {index} does not continue the digest chain")
        digest = amendment.get("amendment_digest")
        if not isinstance(digest, str) or digest != _canonical_digest(
            amendment, "amendment_digest"
        ):
            issues.append(f"amendment {index} digest does not match")
        else:
            previous_digest = digest
        if decisions is not None:
            decision = decision_by_id.get(amendment.get("decision_id"))
            subject = decision.get("approval_subject") if isinstance(decision, dict) else None
            if (
                not isinstance(decision, dict)
                or decision.get("gate") != "amendment"
                or decision.get("outcome") != "approved"
                or not isinstance(subject, dict)
                or subject.get("id") != amendment_id
                or subject.get("digest") != digest
            ):
                issues.append(f"amendment {index} is not bound to its Decision")
    return issues


def _pending_amendments(
    ledger: dict[str, Any],
    decisions: dict[str, Any],
) -> list[dict[str, Any]]:
    decision_entries = decisions.get("decisions")
    if not isinstance(decision_entries, list):
        return []
    decision_indexes = {
        decision.get("id"): index
        for index, decision in enumerate(decision_entries)
        if isinstance(decision, dict)
    }
    pending: list[dict[str, Any]] = []
    for amendment in ledger.get("amendments", []):
        if not isinstance(amendment, dict):
            continue
        amendment_index = decision_indexes.get(amendment.get("decision_id"), -1)
        resolved = any(
            index > amendment_index
            and isinstance(decision, dict)
            and decision.get("gate") == amendment.get("required_gate")
            and decision.get("outcome") == "approved"
            and amendment.get("id") in decision.get("references", [])
            for index, decision in enumerate(decision_entries)
        )
        if not resolved:
            pending.append(amendment)
    return pending


def amendment_status(
    unit_dir: Path,
    *,
    unit: dict[str, Any] | None = None,
    decisions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_unit = unit if unit is not None else _unit_json(unit_dir, "unit.json")
    current_decisions = (
        decisions if decisions is not None else _unit_json(unit_dir, "decisions.json")
    )
    ledger = _load_amendment_ledger(unit_dir, str(current_unit.get("id")))
    issues = _amendment_ledger_issues(
        ledger,
        unit_id=str(current_unit.get("id")),
        decisions=current_decisions,
    )
    pending = _pending_amendments(ledger, current_decisions) if not issues else []
    return {
        "active_unit": current_unit.get("status") not in TERMINAL_STATUSES,
        "count": len(ledger.get("amendments", [])),
        "pending_count": len(pending),
        "pending": [
            {
                "id": amendment.get("id"),
                "request": amendment.get("request"),
                "required_gate": amendment.get("required_gate"),
                "affected_artifacts": amendment.get("affected_artifacts"),
            }
            for amendment in pending
        ],
        "issues": issues,
    }


def approval_amendment_issues(
    unit_dir: Path,
    decisions: dict[str, Any],
    gate: str,
    references: list[str],
) -> list[str]:
    unit = _unit_json(unit_dir, "unit.json")
    ledger = _load_amendment_ledger(unit_dir, str(unit.get("id")))
    pending = [
        amendment
        for amendment in _pending_amendments(ledger, decisions)
        if amendment.get("required_gate") == gate
    ]
    issues: list[str] = []
    for amendment in pending:
        amendment_id = str(amendment.get("id"))
        if amendment_id not in references:
            issues.append(f"{gate} Decision must reference pending amendment {amendment_id}")
        for baseline in amendment.get("baseline_artifacts", []):
            if not isinstance(baseline, dict):
                continue
            reference = str(baseline.get("reference"))
            try:
                current_digest = artifact_content_digest(unit_dir, reference)
            except IntegrityError as exc:
                issues.append(str(exc))
            else:
                if current_digest == baseline.get("digest"):
                    issues.append(
                        f"{reference} has not changed for pending amendment {amendment_id}"
                    )
    return issues


def transition_amendment_issues(
    unit_dir: Path,
    decisions: dict[str, Any],
    gate: str,
) -> list[str]:
    unit = _unit_json(unit_dir, "unit.json")
    ledger = _load_amendment_ledger(unit_dir, str(unit.get("id")))
    return [
        f"pending amendment {amendment.get('id')} requires a fresh {gate} Decision"
        for amendment in _pending_amendments(ledger, decisions)
        if amendment.get("required_gate") == gate
    ]


def _required_gate(affected_artifacts: list[str]) -> str:
    gates = {AMENDABLE_ARTIFACT_GATES[relative] for relative in affected_artifacts}
    return next(gate for gate in _GATE_PRECEDENCE if gate in gates)


def _rework_status(current_status: str, required_gate: str) -> str:
    target = _GATE_REWORK_STATUS[required_gate]
    return (
        target
        if _STATUS_ORDER[current_status] > _STATUS_ORDER[target]
        else current_status
    )


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
    if not isinstance(request, str) or not request.strip():
        raise WorkflowError("amendment request must be a non-empty string")
    if not isinstance(requested_by, str) or not requested_by.strip():
        raise WorkflowError("requested_by must be a non-empty string")
    normalized_artifacts = list(dict.fromkeys(affected_artifacts))
    if not normalized_artifacts:
        raise WorkflowError("amendment requires at least one affected Unit artifact")
    unsupported = sorted(set(normalized_artifacts) - AMENDABLE_ARTIFACT_GATES.keys())
    if unsupported:
        raise WorkflowError("unsupported amendment artifacts: " + ", ".join(unsupported))

    with unit_lock(unit_dir):
        unit = _unit_json(unit_dir, "unit.json")
        if unit.get("status") in TERMINAL_STATUSES:
            raise LifecycleError(
                f"a {unit.get('status')} Unit is closed and cannot be amended; "
                "start a new Unit"
            )
        if unit.get("status") not in LIFECYCLE_STATUSES:
            raise LifecycleError("Unit has an invalid lifecycle status")
        decisions = _unit_json(unit_dir, "decisions.json")
        decision_issues = _decision_ledger_issues(
            decisions,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        )
        if decision_issues:
            raise IntegrityError(
                "existing Decision history is invalid: " + "; ".join(decision_issues)
            )
        ledger = _load_amendment_ledger(unit_dir, str(unit.get("id")))
        ledger_issues = _amendment_ledger_issues(
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
        authorization_ledger = _unit_json(
            unit_dir, "execution-authorizations.json"
        )
        evidence = _unit_json(unit_dir, "evidence/verification.json")
        current_status = str(unit.get("status"))
        required_gate = _required_gate(normalized_artifacts)
        rework_status = _rework_status(current_status, required_gate)
        now = datetime.now(timezone.utc)
        decision_entries = decisions.get("decisions")
        amendment_entries = ledger.get("amendments")
        if not isinstance(decision_entries, list) or not isinstance(
            amendment_entries, list
        ):
            raise IntegrityError("Unit amendment ledgers must contain lists")
        if decision_entries:
            previous_time = _parse_iso_timestamp(decision_entries[-1].get("decided_at"))
            if previous_time is not None and now <= previous_time:
                now = previous_time + timedelta(microseconds=1)
        stamp = now.strftime("%Y%m%d%H%M%S%f")
        amendment_id = "AMD-" + stamp
        decision_id = "DEC-" + stamp
        localized_reason = reason.strip() if isinstance(reason, str) else ""
        if not localized_reason:
            localized_reason = (
                "사용자가 활성 Unit의 변경을 요청했다."
                if unit.get("document_language") == "ko"
                else "The user requested a change to the active Unit."
            )
        amendment = {
            "id": amendment_id,
            "type": "unit-amendment",
            "schema_version": AMENDMENT_SCHEMA_VERSION,
            "unit_id": unit.get("id"),
            "request": request.strip(),
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
            "requested_by": requested_by.strip(),
            "requested_at": now.isoformat(),
            "decision_id": decision_id,
            "previous_amendment_digest": (
                amendment_entries[-1].get("amendment_digest")
                if amendment_entries
                else None
            ),
        }
        amendment["amendment_digest"] = _canonical_digest(
            amendment, "amendment_digest"
        )
        korean = unit.get("document_language") == "ko"
        decision = {
            "id": decision_id,
            "type": "human-decision",
            "schema_version": "1.0.0",
            "unit_id": unit.get("id"),
            "gate": "amendment",
            "outcome": "approved",
            "summary": (
                f"활성 Unit 변경 요청을 기록한다: {request.strip()}"
                if korean
                else f"Record the active Unit amendment: {request.strip()}"
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
            "decided_by": requested_by.strip(),
            "decided_at": now.isoformat(),
            "attestation": {
                "type": "human-decision-attestation",
                "reported_actor": requested_by.strip(),
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
                if decision_entries
                else None
            ),
        }
        language_issues = _decision_description_language_issues(
            decision, str(unit.get("document_language"))
        )
        if language_issues:
            raise IntegrityError(
                "Amendment Decision rejected: " + "; ".join(language_issues)
            )
        decision["decision_digest"] = _decision_record_digest(decision)
        candidate_decisions = {**decisions, "decisions": [*decision_entries, decision]}
        candidate_ledger = {**ledger, "amendments": [*amendment_entries, amendment]}
        candidate_issues = _decision_ledger_issues(
            candidate_decisions,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        )
        candidate_issues.extend(
            _amendment_ledger_issues(
                candidate_ledger,
                unit_id=str(unit.get("id")),
                decisions=candidate_decisions,
            )
        )
        if candidate_issues:
            raise IntegrityError(
                "Unit amendment rejected: " + "; ".join(candidate_issues)
            )

        candidate_unit = {**unit, "status": rework_status, "phase": STATUS_PHASE[rework_status]}
        pending_label = f"{amendment_id}: {request.strip()}"
        candidate_checkpoint = {
            **checkpoint,
            "unit_id": unit.get("id"),
            "pending": [
                *[
                    item
                    for item in checkpoint.get("pending", [])
                    if isinstance(item, str) and item.strip()
                ],
                pending_label,
            ],
            "blocked_by": [],
            "next_action": (
                "변경 요청을 관련 Unit 문서와 구현에 반영한다."
                if korean
                else "Apply the amendment to the affected Unit artifacts and implementation."
            ),
            "authorization_cursor": authorization_progress_cursor(
                unit_dir, authorization_ledger
            ),
            "updated_at": now.isoformat(),
        }
        candidate_evidence: dict[str, Any] = {
            "id": "",
            "type": "verification-evidence",
            "schema_version": "1.0.0",
            "unit_id": unit.get("id"),
            "stage": STATUS_PHASE[rework_status],
            "passed": False,
            "scope": "",
            "recorded_by": "",
            "recorded_at": "",
            "commands": [],
            "envelope_id": envelope.get("id"),
            "envelope_digest": envelope.get("approval_digest"),
            "authorization_ledger_digest": _authorization_ledger_digest(
                authorization_ledger
            ),
            "authorization_count": len(authorization_ledger.get("grants", [])),
        }
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
            _write_json(amendment_path, candidate_ledger)
            _write_json(unit_dir / "decisions.json", candidate_decisions)
            _write_json(unit_dir / "unit.json", candidate_unit)
            _write_json(unit_dir / "checkpoint.json", candidate_checkpoint)
            _write_json(unit_dir / "evidence/verification.json", candidate_evidence)
            persisted = _load_amendment_ledger(unit_dir, str(unit.get("id")))
            persisted_decisions = _unit_json(unit_dir, "decisions.json")
            postflight = _amendment_ledger_issues(
                persisted,
                unit_id=str(unit.get("id")),
                decisions=persisted_decisions,
            )
            if postflight:
                raise IntegrityError(
                    "Unit amendment postflight failed: " + "; ".join(postflight)
                )
        except Exception as exc:
            _restore_snapshots(snapshots, "Unit amendment", exc)
            if not amendment_existed:
                try:
                    amendment_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
    return {
        "path": str(unit_dir / AMENDMENTS_FILE),
        "amendment_id": amendment_id,
        "decision_id": decision_id,
        "required_gate": required_gate,
        "from_status": current_status,
        "status": rework_status,
        "affected_artifacts": normalized_artifacts,
    }
