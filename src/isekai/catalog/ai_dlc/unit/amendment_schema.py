from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from isekai.support.errors import IntegrityError

from .artifacts import artifact_content_digest
from .common import (
    parse_iso_timestamp as _parse_iso_timestamp,
    unit_json as _unit_json,
    unit_path_without_symlinks as _unit_path_without_symlinks,
)
from .decision_schema import LIFECYCLE_STATUSES, TERMINAL_STATUSES


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


def amendment_digest(value: dict[str, Any], digest_field: str) -> str:
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


def load_amendment_ledger(unit_dir: Path, unit_id: str) -> dict[str, Any]:
    path = _unit_path_without_symlinks(unit_dir, AMENDMENTS_FILE)
    if not path.exists():
        return _empty_amendment_ledger(unit_id)
    return _unit_json(unit_dir, AMENDMENTS_FILE)


def amendment_ledger_issues(
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
        if not isinstance(amendment.get("required_gate"), str) or amendment.get(
            "required_gate"
        ) not in _GATE_PRECEDENCE:
            issues.append(f"amendment {index} has an invalid required_gate")
        if not isinstance(amendment.get("from_status"), str) or amendment.get(
            "from_status"
        ) not in LIFECYCLE_STATUSES:
            issues.append(f"amendment {index} has an invalid from_status")
        if not isinstance(amendment.get("rework_status"), str) or amendment.get(
            "rework_status"
        ) not in LIFECYCLE_STATUSES:
            issues.append(f"amendment {index} has an invalid rework_status")
        if _parse_iso_timestamp(amendment.get("requested_at")) is None:
            issues.append(f"amendment {index} has an invalid requested_at")
        affected = amendment.get("affected_artifacts")
        if not isinstance(affected, list) or not affected:
            issues.append(f"amendment {index} requires affected_artifacts")
            affected = []
        elif any(
            not isinstance(item, str) or item not in AMENDABLE_ARTIFACT_GATES
            for item in affected
        ):
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
        if not isinstance(digest, str) or digest != amendment_digest(
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
    ledger = load_amendment_ledger(unit_dir, str(current_unit.get("id")))
    issues = amendment_ledger_issues(
        ledger,
        unit_id=str(current_unit.get("id")),
        decisions=current_decisions,
    )
    pending = _pending_amendments(ledger, current_decisions) if not issues else []
    return {
        "active_unit": (
            not isinstance(current_unit.get("status"), str)
            or current_unit.get("status") not in TERMINAL_STATUSES
        ),
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
    ledger = load_amendment_ledger(unit_dir, str(unit.get("id")))
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
    ledger = load_amendment_ledger(unit_dir, str(unit.get("id")))
    return [
        f"pending amendment {amendment.get('id')} requires a fresh {gate} Decision"
        for amendment in _pending_amendments(ledger, decisions)
        if amendment.get("required_gate") == gate
    ]


def required_amendment_gate(affected_artifacts: list[str]) -> str:
    gates = {AMENDABLE_ARTIFACT_GATES[relative] for relative in affected_artifacts}
    return next(gate for gate in _GATE_PRECEDENCE if gate in gates)


def amendment_rework_status(current_status: str, required_gate: str) -> str:
    target = _GATE_REWORK_STATUS[required_gate]
    return (
        target
        if _STATUS_ORDER[current_status] > _STATUS_ORDER[target]
        else current_status
    )



__all__ = [
    "AMENDABLE_ARTIFACT_GATES",
    "AMENDMENTS_FILE",
    "AMENDMENT_SCHEMA_VERSION",
    "amendment_digest",
    "amendment_ledger_issues",
    "amendment_rework_status",
    "amendment_status",
    "approval_amendment_issues",
    "load_amendment_ledger",
    "required_amendment_gate",
    "transition_amendment_issues",
]
