from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from isekai.support.errors import IntegrityError
from isekai.support.files import UnsafeControlFile, inspect_tree_beneath
from .project_knowledge_schema import CANDIDATE_REFERENCE, candidate_issues
from .project_knowledge_storage import managed_project_directory, safe_project_json
from isekai.catalog.ai_dlc.unit.common import _unit_json
from isekai.catalog.ai_dlc.unit.decisions import _decision_ledger_issues


CANDIDATE_STATUSES = (
    "pending-decision",
    "approved",
    "rejected",
    "stale",
    "promoted",
    "invalid",
)


def _source_unit_path(
    project_root: Path, candidate: dict[str, Any]
) -> Path | None:
    source_unit = candidate.get("source_unit")
    if isinstance(source_unit, dict):
        if source_unit.get("base") == "external":
            return None
        locator = source_unit.get("path")
        if source_unit.get("base") != "project" or not isinstance(locator, str):
            return None
        lexical = project_root / locator
        if lexical.is_symlink():
            return None
        resolved = lexical.resolve()
        try:
            resolved.relative_to(project_root.resolve())
        except ValueError:
            return None
        return resolved if resolved.is_dir() else None

    # Compatibility for candidates created before source_unit locators existed.
    source_unit_id = candidate.get("source_unit_id")
    if isinstance(source_unit_id, str):
        legacy = project_root / "units" / source_unit_id.lower()
        if legacy.is_dir() and not legacy.is_symlink():
            return legacy.resolve()
    return None


def _decision_matches_candidate(
    decision: dict[str, Any],
    candidate: dict[str, Any],
    reference: str,
) -> bool:
    if decision.get("gate") != "knowledge":
        return False
    if decision.get("outcome") == "approved":
        return decision.get("approval_subject") == {
            "type": "project-knowledge-candidate",
            "id": candidate.get("id"),
            "digest": candidate.get("candidate_digest"),
            "reference": reference,
        }
    references = decision.get("references")
    return isinstance(references, list) and reference in references


def _candidate_decision(
    unit_dir: Path,
    candidate: dict[str, Any],
    reference: str,
    validate_sources: Callable[[Path, dict[str, Any]], None],
) -> tuple[dict[str, Any], list[str]]:
    try:
        unit = _unit_json(unit_dir, "unit.json")
        decisions = _unit_json(unit_dir, "decisions.json")
        ledger_issues = _decision_ledger_issues(
            decisions,
            unit_id=str(unit.get("id")),
            scope=str(unit.get("scope")),
        )
        validate_sources(unit_dir, candidate)
    except IntegrityError as exc:
        return {
            "available": True,
            "outcome": None,
            "decision_id": None,
            "current_for_promotion": False,
        }, [str(exc)]
    if ledger_issues:
        return {
            "available": True,
            "outcome": None,
            "decision_id": None,
            "current_for_promotion": False,
        }, ["invalid source Unit Decision ledger: " + "; ".join(ledger_issues)]
    knowledge = [
        decision
        for decision in decisions.get("decisions", [])
        if isinstance(decision, dict) and decision.get("gate") == "knowledge"
    ]
    matching = [
        decision
        for decision in knowledge
        if _decision_matches_candidate(decision, candidate, reference)
    ]
    latest_match = matching[-1] if matching else None
    current = latest_match is not None and knowledge and latest_match is knowledge[-1]
    return {
        "available": True,
        "outcome": latest_match.get("outcome") if latest_match else None,
        "decision_id": latest_match.get("id") if latest_match else None,
        "current_for_promotion": bool(current),
    }, []


def _promotion_index(catalog: dict[str, Any] | None) -> dict[Any, dict[str, Any]]:
    return {
        release.get("source_candidate_id"): {
            "id": release.get("id"),
            "version": release.get("version"),
            "digest": release.get("release_digest"),
        }
        for release in (catalog.get("releases", []) if catalog else [])
        if isinstance(release, dict)
    }


def candidate_status_details(
    project_root: Path,
    project_id: str,
    catalog: dict[str, Any] | None,
    *,
    knowledge_root: str,
    validate_sources: Callable[[Path, dict[str, Any]], None],
) -> list[dict[str, Any]]:
    candidates_path = project_root / knowledge_root / "candidates"
    try:
        managed_project_directory(
            project_root, f"{knowledge_root}/candidates", create=False
        )
    except IntegrityError as exc:
        if isinstance(exc.__cause__, FileNotFoundError):
            return []
        raise
    try:
        candidate_files, _candidate_directories = inspect_tree_beneath(
            candidates_path,
            label="Project Knowledge candidates",
        )
    except UnsafeControlFile as exc:
        raise IntegrityError(str(exc)) from exc
    promoted = _promotion_index(catalog)
    current = (
        catalog["releases"][-1]
        if catalog and isinstance(catalog.get("releases"), list) and catalog["releases"]
        else None
    )
    current_digest = current.get("release_digest") if isinstance(current, dict) else None
    details: list[dict[str, Any]] = []
    for relative_path in candidate_files:
        if len(relative_path.parts) != 1:
            continue
        path = candidates_path / relative_path
        reference = f"{knowledge_root}/candidates/{path.name}"
        match = CANDIDATE_REFERENCE.fullmatch(reference)
        if match is None:
            continue
        try:
            candidate = safe_project_json(
                path, root=project_root, label="Project Knowledge candidate"
            )
        except IntegrityError as exc:
            details.append(
                {
                    "id": match.group(1),
                    "reference": reference,
                    "status": "invalid",
                    "decision": None,
                    "issues": [str(exc)],
                    "promoted_release": None,
                }
            )
            continue
        source_unit_id = candidate.get("source_unit_id")
        issues = candidate_issues(
            candidate,
            project_id=project_id,
            unit_id=str(source_unit_id),
        )
        if candidate.get("id") != match.group(1):
            issues.append("Project Knowledge candidate id does not match its path")
        source_unit = _source_unit_path(project_root, candidate) if not issues else None
        if source_unit is None:
            decision = {
                "available": False,
                "outcome": None,
                "decision_id": None,
                "current_for_promotion": False,
            }
        else:
            decision, decision_issues = _candidate_decision(
                source_unit, candidate, reference, validate_sources
            )
            issues.extend(decision_issues)
        promoted_release = promoted.get(candidate.get("id"))
        if issues:
            status = "invalid"
        elif promoted_release is not None:
            status = "promoted"
        elif candidate.get("base_release_digest") != current_digest:
            status = "stale"
        elif decision.get("current_for_promotion") and decision.get("outcome") == "approved":
            status = "approved"
        elif decision.get("current_for_promotion") and decision.get("outcome") == "rejected":
            status = "rejected"
        else:
            status = "pending-decision"
        entries = candidate.get("entries")
        details.append(
            {
                "id": candidate.get("id"),
                "reference": reference,
                "source_unit_id": source_unit_id,
                "source_unit": candidate.get("source_unit"),
                "proposed_by": candidate.get("proposed_by"),
                "proposed_at": candidate.get("proposed_at"),
                "base_release_digest": candidate.get("base_release_digest"),
                "candidate_digest": candidate.get("candidate_digest"),
                "entry_ids": [
                    entry.get("id") for entry in entries if isinstance(entry, dict)
                ]
                if isinstance(entries, list)
                else [],
                "status": status,
                "decision": decision,
                "issues": issues,
                "promoted_release": promoted_release,
            }
        )
    return details
