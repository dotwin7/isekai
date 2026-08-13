from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from isekai.support.files import UnsafeControlFile, inspect_tree_beneath

from ._unit_verification import execute_unit_verification
from ._verification_contract import UnitVerificationOperations
from .authorization import authorization_ledger_issues as _authorization_ledger_issues
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
    decision_description_language_issues as _decision_description_language_issues,
    restore_snapshots as _restore_snapshots,
    unit_bytes as _unit_bytes,
    unit_json as _unit_json,
    unit_maximum_agent_level as _unit_maximum_agent_level,
    unit_path_without_symlinks as _unit_path_without_symlinks,
    unit_preflight_issues as _unit_preflight_issues,
    unit_text as _unit_text,
    write_unit_json as _write_unit_json,
    unit_lock,
)
from .decision_schema import (
    ALLOWED_TRANSITIONS,
    LIFECYCLE_STATUSES,
    REQUIRED_DECISIONS_FOR_TRANSITIONS,
    STATUS_PHASE,
    decision_ledger_issues,
    has_approved_decision,
    latest_decision,
)
from .decisions import (
    approved_envelope_decision_issues,
    release_decision_evidence_issues,
)
from .amendments import amendment_status, transition_amendment_issues
from isekai.support.errors import IntegrityError, LifecycleError, PreflightError, WorkflowError
from .evidence import (
    current_authorization_context as _current_authorization_context,
    historical_evidence_issues as _historical_evidence_issues,
    passing_evidence as _passing_evidence,
    validate_evidence,
)
from .execution import approve_execution_envelope_locked as _approve_execution_envelope
from .execution_schema import execution_envelope_issues
from .execution_history import (
    EXECUTION_AUTHORIZATION_RECORDS_DIR,
    execution_authorization_record_issues as _execution_authorization_record_issues,
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
        approved = has_approved_decision(
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
        if not has_approved_decision(
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
            inception_decision = latest_decision(decisions, "inception")
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
            release_binding_issues = release_decision_evidence_issues(
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


class _LifecycleVerificationAdapter(UnitVerificationOperations):
    def tree_inventory(self, unit_dir: Path) -> tuple[list[Path], list[str]]:
        return _tree_inventory(unit_dir)

    def read_json(self, unit_dir: Path, relative: str) -> dict[str, Any]:
        return _unit_json(unit_dir, relative)

    def unit_preflight_issues(self, unit_dir: Path) -> list[str]:
        return _unit_preflight_issues(unit_dir)

    def unit_maximum_agent_level(self, unit_dir: Path) -> str:
        return _unit_maximum_agent_level(unit_dir)

    def known_catalog_ids(self) -> list[str]:
        from isekai.workflow.catalog import load_catalog

        return [
            str(entry["id"])
            for entry in load_catalog().get("entries", [])
            if isinstance(entry, dict) and "id" in entry
        ]

    def execution_envelope_issues(
        self,
        envelope: Any,
        unit_id: str,
        *,
        check_expiry: bool,
        maximum_agent_level: str | None,
    ) -> list[str]:
        return execution_envelope_issues(
            envelope,
            unit_id,
            check_expiry=check_expiry,
            maximum_agent_level=maximum_agent_level,
        )

    def approved_envelope_decision_issues(
        self,
        unit_dir: Path,
        envelope: dict[str, Any],
        unit: dict[str, Any],
    ) -> list[str]:
        return approved_envelope_decision_issues(unit_dir, envelope, unit)

    def authorization_ledger_issues(
        self,
        ledger: Any,
        unit: dict[str, Any],
        envelope: dict[str, Any],
        *,
        unit_dir: Path,
    ) -> list[str]:
        return _authorization_ledger_issues(
            ledger, unit, envelope, unit_dir=unit_dir
        )

    def authorization_record_issues(
        self,
        record: Any,
        unit: dict[str, Any],
        *,
        expected_envelope_id: str | None = None,
    ) -> list[str]:
        return _execution_authorization_record_issues(
            record,
            unit,
            expected_envelope_id=expected_envelope_id,
        )

    def decision_ledger_issues(
        self,
        decisions: Any,
        *,
        unit_id: str | None = None,
        scope: str | None = None,
    ) -> list[str]:
        return decision_ledger_issues(
            decisions,
            unit_id=unit_id,
            scope=scope,
        )

    def knowledge_decision_candidate_issues(
        self,
        unit_dir: Path,
        decisions: dict[str, Any],
    ) -> list[str]:
        from isekai.workflow.project_knowledge import (
            knowledge_decision_candidate_issues,
        )

        return knowledge_decision_candidate_issues(unit_dir, decisions)

    def approved_artifact_snapshot_issues(
        self,
        unit_dir: Path,
        decisions: dict[str, Any],
    ) -> list[str]:
        return approved_artifact_snapshot_issues(unit_dir, decisions)

    def human_document_language_issues(
        self,
        unit_dir: Path,
        unit: dict[str, Any],
        decisions: dict[str, Any] | None,
    ) -> list[str]:
        return _human_document_language_issues(unit_dir, unit, decisions)

    def release_decision_evidence_issues(
        self,
        unit_dir: Path,
        decisions: dict[str, Any],
        unit: dict[str, Any],
        *,
        require_current: bool,
    ) -> list[str]:
        return release_decision_evidence_issues(
            unit_dir,
            decisions,
            unit,
            require_current=require_current,
        )

    def status_artifact_readiness_issues(
        self,
        unit_dir: Path,
        status: str,
    ) -> list[str]:
        return status_artifact_readiness_issues(unit_dir, status)

    def has_approved_decision(
        self,
        decisions: dict[str, Any],
        gate: str,
    ) -> bool:
        return has_approved_decision(decisions, gate)

    def acceptance_criteria_issues(self, unit_dir: Path) -> list[str]:
        return _acceptance_criteria_issues(unit_dir)

    def amendment_status(
        self,
        unit_dir: Path,
        *,
        unit: dict[str, Any],
        decisions: dict[str, Any],
    ) -> dict[str, Any]:
        return amendment_status(unit_dir, unit=unit, decisions=decisions)

    def historical_evidence_issues(
        self,
        evidence: Any,
        unit_id: str,
        authorization_contexts: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> list[str]:
        return _historical_evidence_issues(
            evidence,
            unit_id,
            authorization_contexts,
        )

    def current_authorization_context(
        self,
        unit_dir: Path,
        unit: dict[str, Any],
        *,
        check_expiry: bool,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
        return _current_authorization_context(
            unit_dir,
            unit,
            check_expiry=check_expiry,
        )

    def evidence_issues(
        self,
        evidence: Any,
        unit_id: str | None = None,
        *,
        require_passing: bool = True,
        authorization_binding: dict[str, Any] | None = None,
        authorization_grants: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]:
        return validate_evidence(
            evidence,
            unit_id,
            require_passing=require_passing,
            authorization_binding=authorization_binding,
            authorization_grants=authorization_grants,
        )

    def checkpoint_progress_issues(
        self,
        unit_dir: Path,
        *,
        checkpoint: dict[str, Any],
        active_ledger: dict[str, Any],
    ) -> list[str]:
        return checkpoint_progress_issues(
            unit_dir,
            checkpoint=checkpoint,
            active_ledger=active_ledger,
        )

    def human_gate_status(
        self,
        unit: dict[str, Any],
        decisions: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return _human_gate_status(unit, decisions)


_VERIFICATION_OPERATIONS: UnitVerificationOperations = _LifecycleVerificationAdapter()


def _verify_unit_locked(unit_dir: Path) -> dict[str, Any]:
    return dict(execute_unit_verification(unit_dir, _VERIFICATION_OPERATIONS))

def unit_status(path: str | Path) -> dict[str, Any]:
    result = verify_unit(path)
    result["unit_dir"] = str(Path(path).resolve())
    return result
