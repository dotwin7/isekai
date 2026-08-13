from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from isekai.support.errors import IntegrityError, WorkflowError

from ._verification_contract import UnitVerificationOperations, UnitVerificationResult
from .common import UNIT_LOCK_NAME, UNIT_REQUIRED_FILES
from .decision_schema import (
    LIFECYCLE_STATUSES,
    REQUIRED_DECISIONS_FOR_TRANSITIONS,
    STATUS_PHASE,
)
from .execution_history import EXECUTION_AUTHORIZATION_RECORDS_DIR


@dataclass
class _VerificationState:
    unit_dir: Path
    operations: UnitVerificationOperations
    tree_safe: bool
    present: set[str]
    missing: list[str]
    issues: list[str]
    unit: dict[str, Any]
    decisions: dict[str, Any] | None
    checkpoint: dict[str, Any] | None
    envelope: dict[str, Any] | None = None
    ledger: dict[str, Any] | None = None
    authorization_contexts: list[tuple[dict[str, Any], dict[str, Any]]] = field(
        default_factory=list
    )
    evidence: dict[str, Any] | None = None
    amendments: dict[str, Any] = field(default_factory=dict)
    progress_issues: list[str] = field(default_factory=list)

    def read_artifact(self, relative: str) -> dict[str, Any] | None:
        try:
            return self.operations.read_json(self.unit_dir, relative)
        except IntegrityError as exc:
            self.issues.append(str(exc))
            return None


def _initial_state(
    unit_dir: Path,
    operations: UnitVerificationOperations,
) -> _VerificationState:
    tree_files, tree_issues = operations.tree_inventory(unit_dir)
    present = {
        file.as_posix()
        for file in tree_files
        if "__pycache__" not in file.parts and not file.name.startswith(UNIT_LOCK_NAME)
    }
    state = _VerificationState(
        unit_dir=unit_dir,
        operations=operations,
        tree_safe=not tree_issues,
        present=present,
        missing=sorted(UNIT_REQUIRED_FILES - present),
        issues=list(tree_issues),
        unit={},
        decisions=None,
        checkpoint=None,
    )
    state.unit = state.read_artifact("unit.json") or {}
    state.decisions = state.read_artifact("decisions.json")
    state.checkpoint = state.read_artifact("checkpoint.json")
    state.issues.extend(operations.unit_preflight_issues(unit_dir))
    return state


def _catalog_issues(state: _VerificationState) -> None:
    catalog_entry = state.unit.get("catalog_entry")
    if not isinstance(catalog_entry, str) or not catalog_entry.strip():
        return
    try:
        known_ids = state.operations.known_catalog_ids()
        if catalog_entry not in known_ids:
            state.issues.append(
                f"Unit catalog_entry references unknown entry: {catalog_entry}"
            )
    except Exception as exc:
        state.issues.append(f"cannot validate Unit catalog_entry: {exc}")


def _active_execution_issues(state: _VerificationState) -> str | None:
    try:
        maximum_agent_level = state.operations.unit_maximum_agent_level(state.unit_dir)
    except IntegrityError as exc:
        state.issues.append(str(exc))
        maximum_agent_level = None
    if (state.unit_dir / "execution-envelope.json").is_file():
        state.envelope = state.read_artifact("execution-envelope.json")
        if state.envelope is not None:
            state.issues.extend(
                state.operations.execution_envelope_issues(
                    state.envelope,
                    str(state.unit.get("id")),
                    check_expiry=False,
                    maximum_agent_level=maximum_agent_level,
                )
            )
            state.issues.extend(
                state.operations.approved_envelope_decision_issues(
                    state.unit_dir, state.envelope, state.unit
                )
            )
    if (
        (state.unit_dir / "execution-authorizations.json").is_file()
        and state.envelope is not None
    ):
        state.ledger = state.read_artifact("execution-authorizations.json")
        if state.ledger is not None:
            state.issues.extend(
                state.operations.authorization_ledger_issues(
                    state.ledger,
                    state.unit,
                    state.envelope,
                    unit_dir=state.unit_dir,
                )
            )
            state.authorization_contexts.append((state.envelope, state.ledger))
    return maximum_agent_level


def _authorization_record_issues(
    state: _VerificationState,
    maximum_agent_level: str | None,
) -> None:
    records = state.unit_dir / EXECUTION_AUTHORIZATION_RECORDS_DIR
    if records.is_symlink():
        state.issues.append("Execution authorization records path contains a symlink")
        return
    if records.exists() and not records.is_dir():
        state.issues.append("Execution authorization records path must be a directory")
        return
    if not state.tree_safe or not records.is_dir():
        return
    try:
        record_paths = sorted(records.iterdir())
    except OSError as exc:
        state.issues.append(f"cannot inspect Execution authorization records: {exc}")
        return
    for record_path in record_paths:
        relative = record_path.relative_to(state.unit_dir).as_posix()
        if record_path.is_symlink() or not record_path.is_file() or record_path.suffix != ".json":
            state.issues.append(
                f"Execution authorization record must be a regular JSON file: {relative}"
            )
            continue
        record = state.read_artifact(relative)
        if record is None:
            continue
        state.issues.extend(
            state.operations.authorization_record_issues(
                record,
                state.unit,
                expected_envelope_id=record_path.stem,
            )
        )
        archived_envelope = record.get("envelope")
        archived_ledger = record.get("authorization_ledger")
        if isinstance(archived_envelope, dict):
            state.issues.extend(
                "archived " + issue
                for issue in state.operations.execution_envelope_issues(
                    archived_envelope,
                    str(state.unit.get("id")),
                    check_expiry=False,
                    maximum_agent_level=maximum_agent_level,
                )
            )
            if isinstance(archived_ledger, dict):
                state.authorization_contexts.append(
                    (archived_envelope, archived_ledger)
                )


def _decision_issues(state: _VerificationState) -> list[Any] | None:
    decisions = state.decisions
    entries = decisions.get("decisions") if decisions is not None else None
    if decisions is not None:
        state.issues.extend(
            state.operations.decision_ledger_issues(
                decisions,
                unit_id=str(state.unit.get("id")),
                scope=str(state.unit.get("scope")),
            )
        )
        state.issues.extend(
            state.operations.knowledge_decision_candidate_issues(
                state.unit_dir, decisions
            )
        )
        state.issues.extend(
            state.operations.approved_artifact_snapshot_issues(
                state.unit_dir, decisions
            )
        )
    state.issues.extend(
        state.operations.human_document_language_issues(
            state.unit_dir, state.unit, decisions
        )
    )
    if isinstance(entries, list) and not entries:
        state.issues.append("at least one recorded decision is required")
    elif isinstance(entries, list) and decisions is not None:
        status = state.unit.get("status")
        if isinstance(status, str) and status in {
            "awaiting-release-decision", "releasing",
        }:
            state.issues.extend(
                state.operations.release_decision_evidence_issues(
                    state.unit_dir, decisions, state.unit, require_current=True
                )
            )
        elif isinstance(status, str) and status in {"operating", "learned"}:
            state.issues.extend(
                state.operations.release_decision_evidence_issues(
                    state.unit_dir, decisions, state.unit, require_current=False
                )
            )
    return entries if isinstance(entries, list) else None


def _lifecycle_and_amendment_issues(
    state: _VerificationState,
    decision_entries: list[Any] | None,
) -> None:
    status = state.unit.get("status")
    if not isinstance(status, str) or status not in LIFECYCLE_STATUSES:
        state.issues.append(f"invalid lifecycle status: {status}")
    else:
        state.issues.extend(
            state.operations.status_artifact_readiness_issues(state.unit_dir, status)
        )
    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(
        str(status) if status is not None else ""
    )
    if (
        required_gate
        and decision_entries is not None
        and state.decisions is not None
        and not state.operations.has_approved_decision(state.decisions, required_gate)
    ):
        state.issues.append(
            f"status {status} requires an approved {required_gate} Decision"
        )
    if (
        isinstance(status, str)
        and status in STATUS_PHASE
        and state.unit.get("phase") != STATUS_PHASE[status]
    ):
        state.issues.append("Unit phase does not match lifecycle status")
    checkpoint = state.checkpoint
    if checkpoint is not None:
        if checkpoint.get("unit_id") != state.unit.get("id"):
            state.issues.append("checkpoint unit_id does not match Unit")
        if checkpoint.get("blocked_by") and status != "abandoned":
            state.issues.append("checkpoint has blockers")
        if status == "learned" and checkpoint.get("pending"):
            state.issues.append("learned Unit cannot have pending work")
    state.issues.extend(state.operations.acceptance_criteria_issues(state.unit_dir))
    state.amendments = state.operations.amendment_status(
        state.unit_dir,
        unit=state.unit,
        decisions=state.decisions or {"decisions": []},
    )
    amendment_issues = state.amendments.get("issues", [])
    if isinstance(amendment_issues, list):
        state.issues.extend(str(issue) for issue in amendment_issues)
    pending = state.amendments.get("pending", [])
    if isinstance(pending, list):
        state.issues.extend(
            f"pending Unit amendment {item.get('id')} requires a fresh "
            f"{item.get('required_gate')} Decision"
            for item in pending
            if isinstance(item, dict)
        )


def _evidence_record_issues(state: _VerificationState) -> None:
    records = state.unit_dir / "evidence/records"
    if records.is_symlink():
        state.issues.append("verification Evidence records path contains a symlink")
        return
    if records.exists() and not records.is_dir():
        state.issues.append("verification Evidence records path must be a directory")
        return
    if not state.tree_safe or not records.is_dir():
        return
    try:
        record_paths = sorted(records.iterdir())
    except OSError as exc:
        state.issues.append(f"cannot inspect verification Evidence records: {exc}")
        return
    for record_path in record_paths:
        relative = record_path.relative_to(state.unit_dir).as_posix()
        if record_path.is_symlink() or not record_path.is_file() or record_path.suffix != ".json":
            state.issues.append(
                f"verification Evidence record must be a regular JSON file: {relative}"
            )
            continue
        record = state.read_artifact(relative)
        if record is None:
            continue
        if record.get("id") != record_path.stem:
            state.issues.append(
                "verification Evidence record id does not match its path: " + relative
            )
        state.issues.extend(
            state.operations.historical_evidence_issues(
                record,
                str(state.unit.get("id")),
                state.authorization_contexts,
            )
        )


def _current_evidence_issues(state: _VerificationState) -> None:
    if not (state.unit_dir / "evidence/verification.json").is_file():
        return
    state.evidence = state.read_artifact("evidence/verification.json")
    if state.evidence is None:
        return
    try:
        binding, grants = state.operations.current_authorization_context(
            state.unit_dir, state.unit, check_expiry=False
        )
    except WorkflowError as exc:
        state.issues.append(str(exc))
        binding = None
        grants = None
    state.issues.extend(
        state.operations.evidence_issues(
            state.evidence,
            str(state.unit.get("id")),
            authorization_binding=binding,
            authorization_grants=grants,
        )
    )


def _finalize_progress(state: _VerificationState) -> None:
    if state.checkpoint is not None and state.ledger is not None:
        state.progress_issues = state.operations.checkpoint_progress_issues(
            state.unit_dir,
            checkpoint=state.checkpoint,
            active_ledger=state.ledger,
        )
    else:
        state.progress_issues = ["checkpoint progress cannot be evaluated"]
    state.issues.extend(state.progress_issues)
    state.issues = list(dict.fromkeys(state.issues))


def _verification_result(
    state: _VerificationState,
    decision_entries: list[Any] | None,
) -> UnitVerificationResult:
    checkpoint = state.checkpoint
    evidence_summary = (
        {
            "id": state.evidence.get("id"),
            "passed": state.evidence.get("passed"),
            "stage": state.evidence.get("stage"),
            "command_count": len(state.evidence.get("commands", [])),
        }
        if state.evidence is not None
        else None
    )
    return {
        "valid": not state.missing and not state.issues,
        "unit_id": state.unit.get("id"),
        "catalog_entry": state.unit.get("catalog_entry"),
        "title": state.unit.get("title"),
        "document_language": state.unit.get("document_language"),
        "phase": state.unit.get("phase"),
        "status": state.unit.get("status"),
        "artifact_count": len(state.present),
        "missing": state.missing,
        "issues": state.issues,
        "decision_count": len(decision_entries or []),
        "project_id": state.unit.get("project_id"),
        "pending": checkpoint.get("pending", []) if checkpoint is not None else [],
        "blocked_by": checkpoint.get("blocked_by", []) if checkpoint is not None else [],
        "checkpoint_progress": {
            "fresh": not state.progress_issues,
            "issues": state.progress_issues,
        },
        "amendments": state.amendments,
        "human_gate": state.operations.human_gate_status(
            state.unit, state.decisions
        ),
        "evidence": evidence_summary,
    }


def execute_unit_verification(
    unit_dir: Path,
    operations: UnitVerificationOperations,
) -> UnitVerificationResult:
    state = _initial_state(unit_dir, operations)
    _catalog_issues(state)
    maximum_agent_level = _active_execution_issues(state)
    _authorization_record_issues(state, maximum_agent_level)
    decision_entries = _decision_issues(state)
    _lifecycle_and_amendment_issues(state, decision_entries)
    criteria_path = unit_dir / "evaluations/criteria.json"
    if criteria_path.is_file():
        criteria = state.read_artifact("evaluations/criteria.json")
        if criteria is not None and criteria.get("visibility") != "evaluation-only":
            state.issues.append("evaluation criteria must be evaluation-only")
    _evidence_record_issues(state)
    _current_evidence_issues(state)
    _finalize_progress(state)
    return _verification_result(state, decision_entries)


__all__ = ["execute_unit_verification"]
