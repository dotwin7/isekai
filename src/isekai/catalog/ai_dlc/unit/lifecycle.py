from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from isekai.support.files import UnsafeControlFile, inspect_tree_beneath

from .authorization import _authorization_ledger_issues
from .artifacts import (
    approved_artifact_snapshot_issues,
    latest_decision_artifact_issues,
    status_artifact_readiness_issues,
    target_artifact_readiness_issues,
)
from .checkpointing import checkpoint_progress_issues
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
    _write_unit_json,
    unit_lock,
)
from .decisions import (
    ALLOWED_TRANSITIONS,
    LIFECYCLE_STATUSES,
    REQUIRED_DECISIONS_FOR_TRANSITIONS,
    STATUS_PHASE,
    _approved_envelope_decision_issues,
    _decision_description_language_issues,
    _decision_ledger_issues,
    _has_approved_decision,
    _latest_decision,
    _release_decision_evidence_issues,
)
from .amendments import amendment_status, transition_amendment_issues
from isekai.support.errors import IntegrityError, LifecycleError, PreflightError, WorkflowError
from .evidence import (
    _current_authorization_context,
    _evidence_issues,
    _historical_evidence_issues,
    _passing_evidence,
)
from .execution import _approve_execution_envelope, _execution_envelope_issues
from .execution_history import (
    EXECUTION_AUTHORIZATION_RECORDS_DIR,
    _execution_authorization_record_issues,
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


def _tree_inventory(unit_dir: Path) -> tuple[list[Path], list[str]]:
    try:
        files, _directories = inspect_tree_beneath(unit_dir, label="Unit tree")
        return files, []
    except UnsafeControlFile as exc:
        return [], [str(exc)]


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
        issues.extend(
            f"decision {index} {issue}"
            for issue in _decision_description_language_issues(
                decision,
                document_language,
            )
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
    tree_files, tree_issues = _tree_inventory(unit_dir)
    present = {
        file.as_posix()
        for file in tree_files
        if "__pycache__" not in file.parts
        and not file.name.startswith(UNIT_LOCK_NAME)
    }
    missing = sorted(UNIT_REQUIRED_FILES - present)
    issues = tree_issues + (
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
    # Abandonment is always available from a non-terminal status; the advisory
    # gate describes the forward delivery edge, not the escape hatch.
    forward_statuses = [
        candidate for candidate in next_statuses if candidate != "abandoned"
    ]
    next_status = forward_statuses[0] if len(forward_statuses) == 1 else None
    gate = (
        REQUIRED_DECISIONS_FOR_TRANSITIONS.get(next_status)
        if next_status is not None
        else None
    )
    approved = False
    gate_decisions: list[dict[str, Any]] = []
    if gate is not None and decisions is not None:
        approved = _has_approved_decision(
            decisions,
            gate,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        )
        entries = decisions.get("decisions")
        if isinstance(entries, list):
            gate_decisions = [
                decision
                for decision in entries
                if isinstance(decision, dict) and decision.get("gate") == gate
            ]
    latest_decision = gate_decisions[-1] if gate_decisions else None
    latest_outcome = (
        latest_decision.get("outcome")
        if latest_decision is not None
        else None
    )
    revision_requested = latest_outcome == "rejected"
    review_round = (
        1
        + sum(
            decision.get("outcome") == "rejected"
            for decision in gate_decisions
        )
        if gate is not None
        else 0
    )
    return {
        "next_transition": next_status,
        "gate": gate,
        "decision": (
            "approved"
            if approved
            else "rejected"
            if revision_requested
            else "required"
            if gate is not None
            else "not-applicable"
        ),
        "latest_decision_id": (
            latest_decision.get("id")
            if latest_decision is not None
            else None
        ),
        "review_round": review_round,
        "revision_requested": revision_requested,
        "confirmation_required": gate is not None and not approved,
    }


def transition_unit(path: str | Path, target_status: str) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise LifecycleError(f"Unit directory does not exist: {unit_dir}")
    if not isinstance(target_status, str):
        raise LifecycleError(
            f"target_status must be one of: {', '.join(LIFECYCLE_STATUSES)}"
        )
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

    progress_issues = checkpoint_progress_issues(unit_dir)
    if progress_issues:
        raise LifecycleError(
            f"transition to {target_status} requires a current checkpoint: "
            + "; ".join(progress_issues)
        )

    artifact_issues = target_artifact_readiness_issues(unit_dir, target_status)
    if artifact_issues:
        raise LifecycleError(
            f"transition to {target_status} requires materialized Unit artifacts: "
            + "; ".join(artifact_issues)
        )

    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(target_status)
    if required_gate:
        decisions = _unit_json(unit_dir, "decisions.json")
        amendment_issues = transition_amendment_issues(
            unit_dir, decisions, required_gate
        )
        if amendment_issues:
            raise LifecycleError(
                f"transition to {target_status} has unresolved Unit amendments: "
                + "; ".join(amendment_issues)
            )
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
        binding_issues = latest_decision_artifact_issues(
            unit_dir, decisions, required_gate
        )
        if binding_issues:
            raise IntegrityError(
                f"transition to {target_status} blocked by changed approved artifacts: "
                + "; ".join(binding_issues)
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

        if target_status in {"releasing", "learned"}:
            verification = _verify_unit_locked(unit_dir)
            if not verification["valid"]:
                verification_issues = [
                    *(
                        "missing required Unit artifact: " + relative
                        for relative in verification["missing"]
                    ),
                    *verification["issues"],
                ]
                raise LifecycleError(
                    f"transition to {target_status} requires a valid Unit: "
                    + "; ".join(verification_issues)
                )

        unit["status"] = target_status
        unit["phase"] = STATUS_PHASE[target_status]
        unit["updated_at"] = datetime.now(timezone.utc).isoformat()
        mutation_started = True
        _write_unit_json(unit_dir, "unit.json", unit)
        persisted = _unit_json(unit_dir, "unit.json")
        if persisted.get("status") != target_status:
            raise IntegrityError("Unit postflight blocked: lifecycle status was not persisted")
    except Exception as exc:
        if not mutation_started:
            raise
        snapshots = [(unit_path, unit_before)]
        if envelope_before is not None:
            snapshots.append((envelope_path, envelope_before))
        _restore_snapshots(snapshots, "Unit transition", exc, root=unit_dir)
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
    tree_files, tree_issues = _tree_inventory(unit_dir)
    tree_safe = not tree_issues
    present = {
        file.as_posix()
        for file in tree_files
        if "__pycache__" not in file.parts
        and not file.name.startswith(UNIT_LOCK_NAME)
    }
    missing = sorted(UNIT_REQUIRED_FILES - present)
    issues: list[str] = list(tree_issues)

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
    catalog_entry = unit.get("catalog_entry")
    if isinstance(catalog_entry, str) and catalog_entry.strip():
        try:
            from isekai.workflow.catalog import load_catalog

            known_ids = [
                e["id"]
                for e in load_catalog().get("entries", [])
                if isinstance(e, dict)
            ]
            if catalog_entry not in known_ids:
                issues.append(
                    f"Unit catalog_entry references unknown entry: {catalog_entry}"
                )
        except Exception as exc:
            issues.append(f"cannot validate Unit catalog_entry: {exc}")
    envelope_path = unit_dir / "execution-envelope.json"
    envelope: dict[str, Any] | None = None
    ledger: dict[str, Any] | None = None
    authorization_contexts: list[tuple[dict[str, Any], dict[str, Any]]] = []
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
            authorization_contexts.append((envelope, ledger))

    authorization_records = unit_dir / EXECUTION_AUTHORIZATION_RECORDS_DIR
    if authorization_records.is_symlink():
        issues.append("Execution authorization records path contains a symlink")
    elif authorization_records.exists() and not authorization_records.is_dir():
        issues.append("Execution authorization records path must be a directory")
    elif tree_safe and authorization_records.is_dir():
        try:
            record_paths = sorted(authorization_records.iterdir())
        except OSError as exc:
            issues.append(f"cannot inspect Execution authorization records: {exc}")
            record_paths = []
        for record_path in record_paths:
            relative = record_path.relative_to(unit_dir).as_posix()
            if (
                record_path.is_symlink()
                or not record_path.is_file()
                or record_path.suffix != ".json"
            ):
                issues.append(
                    f"Execution authorization record must be a regular JSON file: {relative}"
                )
                continue
            record = read_artifact(relative)
            if record is not None:
                issues.extend(
                    _execution_authorization_record_issues(
                        record,
                        unit,
                        expected_envelope_id=record_path.stem,
                    )
                )
                archived_envelope = record.get("envelope")
                archived_ledger = record.get("authorization_ledger")
                if isinstance(archived_envelope, dict):
                    issues.extend(
                        "archived " + issue
                        for issue in _execution_envelope_issues(
                            archived_envelope,
                            str(unit.get("id")),
                            check_expiry=False,
                            maximum_agent_level=maximum_agent_level,
                        )
                    )
                    if isinstance(archived_ledger, dict):
                        authorization_contexts.append(
                            (archived_envelope, archived_ledger)
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
        from isekai.workflow.project_knowledge import knowledge_decision_candidate_issues

        issues.extend(knowledge_decision_candidate_issues(unit_dir, decisions))
        issues.extend(approved_artifact_snapshot_issues(unit_dir, decisions))
    issues.extend(_human_document_language_issues(unit_dir, unit, decisions))
    if isinstance(decision_entries, list) and not decision_entries:
        issues.append("at least one recorded decision is required")
    elif isinstance(decision_entries, list):
        if decisions is not None:
            if isinstance(unit.get("status"), str) and unit.get("status") in {
                "awaiting-release-decision",
                "releasing",
            }:
                issues.extend(
                    _release_decision_evidence_issues(
                        unit_dir, decisions, unit, require_current=True
                    )
                )
            elif isinstance(unit.get("status"), str) and unit.get("status") in {
                "operating",
                "learned",
            }:
                issues.extend(
                    _release_decision_evidence_issues(
                        unit_dir, decisions, unit, require_current=False
                    )
                )

    status = unit.get("status")
    if not isinstance(status, str) or status not in LIFECYCLE_STATUSES:
        issues.append(f"invalid lifecycle status: {status}")
    elif isinstance(status, str):
        issues.extend(status_artifact_readiness_issues(unit_dir, status))
    required_gate = REQUIRED_DECISIONS_FOR_TRANSITIONS.get(str(status) if status is not None else "")
    if required_gate and isinstance(decision_entries, list):
        if decisions is not None and not _has_approved_decision(decisions, required_gate):
            issues.append(
                f"status {status} requires an approved {required_gate} Decision"
            )
    if (
        isinstance(status, str)
        and status in STATUS_PHASE
        and unit.get("phase") != STATUS_PHASE[status]
    ):
        issues.append("Unit phase does not match lifecycle status")
    if checkpoint is not None:
        if checkpoint.get("unit_id") != unit.get("id"):
            issues.append("checkpoint unit_id does not match Unit")
        # An abandoned Unit may legitimately close with blockers and pending
        # work; that unfinished state is why it was abandoned.
        if checkpoint.get("blocked_by") and unit.get("status") != "abandoned":
            issues.append("checkpoint has blockers")
        if unit.get("status") == "learned" and checkpoint.get("pending"):
            issues.append("learned Unit cannot have pending work")

    issues.extend(_acceptance_criteria_issues(unit_dir))
    amendments = amendment_status(
        unit_dir,
        unit=unit,
        decisions=decisions if decisions is not None else {"decisions": []},
    )
    issues.extend(amendments["issues"])
    issues.extend(
        f"pending Unit amendment {item.get('id')} requires a fresh "
        f"{item.get('required_gate')} Decision"
        for item in amendments["pending"]
    )

    criteria_path = unit_dir / "evaluations/criteria.json"
    if criteria_path.is_file():
        criteria = read_artifact("evaluations/criteria.json")
        if criteria is not None and criteria.get("visibility") != "evaluation-only":
            issues.append("evaluation criteria must be evaluation-only")

    evidence_path = unit_dir / "evidence/verification.json"
    evidence: dict[str, Any] | None = None

    evidence_records = unit_dir / "evidence/records"
    if evidence_records.is_symlink():
        issues.append("verification Evidence records path contains a symlink")
    elif evidence_records.exists() and not evidence_records.is_dir():
        issues.append("verification Evidence records path must be a directory")
    elif tree_safe and evidence_records.is_dir():
        try:
            evidence_record_paths = sorted(evidence_records.iterdir())
        except OSError as exc:
            issues.append(f"cannot inspect verification Evidence records: {exc}")
            evidence_record_paths = []
        for record_path in evidence_record_paths:
            relative = record_path.relative_to(unit_dir).as_posix()
            if (
                record_path.is_symlink()
                or not record_path.is_file()
                or record_path.suffix != ".json"
            ):
                issues.append(
                    f"verification Evidence record must be a regular JSON file: {relative}"
                )
                continue
            record = read_artifact(relative)
            if record is None:
                continue
            if record.get("id") != record_path.stem:
                issues.append(
                    "verification Evidence record id does not match its path: "
                    + relative
                )
            issues.extend(
                _historical_evidence_issues(
                    record,
                    str(unit.get("id")),
                    authorization_contexts,
                )
            )

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

    progress_issues = (
        checkpoint_progress_issues(
            unit_dir,
            checkpoint=checkpoint,
            active_ledger=ledger,
        )
        if checkpoint is not None and ledger is not None
        else ["checkpoint progress cannot be evaluated"]
    )
    issues.extend(progress_issues)
    issues = list(dict.fromkeys(issues))
    valid = not missing and not issues
    return {
        "valid": valid,
        "unit_id": unit.get("id"),
        "catalog_entry": unit.get("catalog_entry"),
        "title": unit.get("title"),
        "document_language": unit.get("document_language"),
        "phase": unit.get("phase"),
        "status": unit.get("status"),
        "artifact_count": len(present),
        "missing": missing,
        "issues": issues,
        "decision_count": len(decision_entries) if isinstance(decision_entries, list) else 0,
        "project_id": unit.get("project_id"),
        "pending": checkpoint.get("pending", []) if checkpoint is not None else [],
        "blocked_by": checkpoint.get("blocked_by", []) if checkpoint is not None else [],
        "checkpoint_progress": {
            "fresh": not progress_issues,
            "issues": progress_issues,
        },
        "amendments": amendments,
        "human_gate": _human_gate_status(unit, decisions),
        "evidence": {
            "id": evidence.get("id"),
            "passed": evidence.get("passed"),
            "stage": evidence.get("stage"),
            "command_count": len(evidence.get("commands", [])),
        } if evidence is not None else None,
    }


def unit_status(path: str | Path) -> dict[str, Any]:
    result = verify_unit(path)
    result["unit_dir"] = str(Path(path).resolve())
    return result
