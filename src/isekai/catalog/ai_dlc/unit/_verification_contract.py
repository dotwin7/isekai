from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, TypedDict


class CheckpointProgress(TypedDict):
    fresh: bool
    issues: list[str]


class UnitVerificationResult(TypedDict):
    valid: bool
    unit_id: Any
    catalog_entry: Any
    title: Any
    document_language: Any
    phase: Any
    status: Any
    artifact_count: int
    missing: list[str]
    issues: list[str]
    decision_count: int
    project_id: Any
    pending: Any
    blocked_by: Any
    checkpoint_progress: CheckpointProgress
    amendments: dict[str, Any]
    human_gate: dict[str, Any]
    evidence: dict[str, Any] | None


class UnitVerificationOperations(Protocol):
    def tree_inventory(self, unit_dir: Path) -> tuple[list[Path], list[str]]: ...

    def read_json(self, unit_dir: Path, relative: str) -> dict[str, Any]: ...

    def unit_preflight_issues(self, unit_dir: Path) -> list[str]: ...

    def unit_maximum_agent_level(self, unit_dir: Path) -> str: ...

    def known_catalog_ids(self) -> list[str]: ...

    def execution_envelope_issues(
        self,
        envelope: Any,
        unit_id: str,
        *,
        check_expiry: bool,
        maximum_agent_level: str | None,
    ) -> list[str]: ...

    def approved_envelope_decision_issues(
        self,
        unit_dir: Path,
        envelope: dict[str, Any],
        unit: dict[str, Any],
    ) -> list[str]: ...

    def authorization_ledger_issues(
        self,
        ledger: Any,
        unit: dict[str, Any],
        envelope: dict[str, Any],
        *,
        unit_dir: Path,
    ) -> list[str]: ...

    def authorization_record_issues(
        self,
        record: Any,
        unit: dict[str, Any],
        *,
        expected_envelope_id: str | None = None,
    ) -> list[str]: ...

    def decision_ledger_issues(
        self,
        decisions: Any,
        *,
        unit_id: str | None = None,
        scope: str | None = None,
    ) -> list[str]: ...

    def knowledge_decision_candidate_issues(
        self,
        unit_dir: Path,
        decisions: dict[str, Any],
    ) -> list[str]: ...

    def approved_artifact_snapshot_issues(
        self,
        unit_dir: Path,
        decisions: dict[str, Any],
    ) -> list[str]: ...

    def human_document_language_issues(
        self,
        unit_dir: Path,
        unit: dict[str, Any],
        decisions: dict[str, Any] | None,
    ) -> list[str]: ...

    def release_decision_evidence_issues(
        self,
        unit_dir: Path,
        decisions: dict[str, Any],
        unit: dict[str, Any],
        *,
        require_current: bool,
    ) -> list[str]: ...

    def status_artifact_readiness_issues(
        self,
        unit_dir: Path,
        status: str,
    ) -> list[str]: ...

    def has_approved_decision(
        self,
        decisions: dict[str, Any],
        gate: str,
    ) -> bool: ...

    def acceptance_criteria_issues(self, unit_dir: Path) -> list[str]: ...

    def amendment_status(
        self,
        unit_dir: Path,
        *,
        unit: dict[str, Any],
        decisions: dict[str, Any],
    ) -> dict[str, Any]: ...

    def historical_evidence_issues(
        self,
        evidence: Any,
        unit_id: str,
        authorization_contexts: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> list[str]: ...

    def current_authorization_context(
        self,
        unit_dir: Path,
        unit: dict[str, Any],
        *,
        check_expiry: bool,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]: ...

    def evidence_issues(
        self,
        evidence: Any,
        unit_id: str | None = None,
        *,
        require_passing: bool = True,
        authorization_binding: dict[str, Any] | None = None,
        authorization_grants: dict[str, dict[str, Any]] | None = None,
    ) -> list[str]: ...

    def checkpoint_progress_issues(
        self,
        unit_dir: Path,
        *,
        checkpoint: dict[str, Any],
        active_ledger: dict[str, Any],
    ) -> list[str]: ...

    def human_gate_status(
        self,
        unit: dict[str, Any],
        decisions: dict[str, Any] | None,
    ) -> dict[str, Any]: ...


__all__ = ["UnitVerificationOperations", "UnitVerificationResult"]
