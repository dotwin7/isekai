from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..support.files import UnsafeControlFile, read_control_file
from ..support.jsonio import write_bytes_atomic, write_json_atomic
from ..support.locking import file_lock
from isekai.support.errors import IntegrityError, LifecycleError, PreflightError, WorkflowError
from .project_knowledge_observability import (
    CANDIDATE_STATUSES,
    candidate_status_details,
)
from .project_knowledge_schema import (
    CANDIDATE_REFERENCE as _CANDIDATE_REFERENCE,
    PROJECT_KNOWLEDGE_SCHEMA_VERSION,
    candidate_digest as _candidate_digest,
    candidate_issues as _candidate_issues,
    catalog_digest as _catalog_digest,
    catalog_issues as _catalog_issues,
    entry_issues as _entry_issues,
    receipt_issues as project_knowledge_receipt_issues,
    release_digest as _release_digest,
    schema_compatibility,
    select_release_context,
    summarize_release,
)
from .project_knowledge_storage import (
    managed_project_directory as _managed_directory,
    safe_project_json as _safe_json,
)


PROJECT_KNOWLEDGE_ROOT = "project-knowledge"
PROJECT_KNOWLEDGE_CATALOG = f"{PROJECT_KNOWLEDGE_ROOT}/catalog.json"
PROJECT_KNOWLEDGE_LOCK = ".isekai-project-knowledge.lock"


def _load_catalog(project_root: Path, project_id: str) -> dict[str, Any] | None:
    path = project_root / PROJECT_KNOWLEDGE_CATALOG
    if not path.exists() and not path.is_symlink():
        return None
    catalog = _safe_json(path, root=project_root, label="Project Knowledge catalog")
    issues = _catalog_issues(catalog, project_id=project_id)
    if issues:
        raise IntegrityError("invalid Project Knowledge catalog: " + "; ".join(issues))
    return catalog


def current_project_knowledge(project_root: Path, project_id: str) -> dict[str, Any] | None:
    """Return a detached copy of the latest approved release for Context pinning."""
    catalog = _load_catalog(project_root.resolve(), project_id)
    if catalog is None or not catalog["releases"]:
        return None
    release = catalog["releases"][-1]
    if not isinstance(release, dict):  # pragma: no cover - catalog validation
        raise IntegrityError("Project Knowledge current release must be an object")
    return copy.deepcopy(release)


def select_project_knowledge_context(
    release: dict[str, Any] | None,
    work_scope: list[str],
) -> dict[str, Any] | None:
    """Build the compact, release-pinned knowledge view for one new Unit."""
    return select_release_context(release, work_scope)


def summarize_project_knowledge(
    release: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Return Project-level metadata without injecting every knowledge entry."""
    return summarize_release(release)


def project_knowledge_binding_issues(
    unit_dir: Path,
    receipt: dict[str, Any],
) -> list[str]:
    """Verify a pinned selection against its immutable catalog release."""
    pinned = receipt.get("project_knowledge")
    if pinned is None:
        return []
    from .project import _receipt_source_manifest_path

    try:
        manifest = _receipt_source_manifest_path(receipt, unit_dir=unit_dir)
        project_id = str(receipt.get("project_id"))
        catalog = _load_catalog(manifest.parent, project_id)
    except (IntegrityError, WorkflowError) as exc:
        return [str(exc)]
    if catalog is None:
        return ["pinned Project Knowledge release is missing from the Project catalog"]
    pinned_digest = pinned.get("release_digest") if isinstance(pinned, dict) else None
    release = next(
        (
            item
            for item in catalog.get("releases", [])
            if isinstance(item, dict) and item.get("release_digest") == pinned_digest
        ),
        None,
    )
    if release is None:
        return ["pinned Project Knowledge release digest is not in the Project catalog"]
    if pinned.get("type") == "project-knowledge-release":
        return [] if pinned == release else [
            "legacy pinned Project Knowledge release does not match the catalog"
        ]
    selection = pinned.get("selection")
    work_scope = selection.get("work_scope") if isinstance(selection, dict) else None
    if not isinstance(work_scope, list) or any(
        not isinstance(scope, str) for scope in work_scope
    ):
        return ["pinned Project Knowledge selection has an invalid work_scope"]
    expected = select_release_context(release, work_scope)
    if pinned != expected:
        return [
            "pinned Project Knowledge entries do not match deterministic release selection"
        ]
    return []


def _unit_project(unit_dir: Path) -> tuple[dict[str, Any], Path, str]:
    from .project import (
        _context_contract_changed_fields,
        _receipt_source_manifest_path,
        resolve_context,
    )
    from isekai.catalog.ai_dlc.unit.common import _unit_json, _unit_preflight_issues

    issues = _unit_preflight_issues(unit_dir)
    if issues:
        raise PreflightError("Project Knowledge preflight blocked: " + "; ".join(issues))
    unit = _unit_json(unit_dir, "unit.json")
    receipt = _unit_json(unit_dir, "context-receipt.json")
    manifest = _receipt_source_manifest_path(receipt, unit_dir=unit_dir)
    if manifest.name != "project.json":
        raise IntegrityError("Unit Context Receipt source_manifest is not project.json")
    context = resolve_context(manifest)
    changed = _context_contract_changed_fields(receipt, context)
    if changed:
        raise IntegrityError(
            "Unit Context Receipt does not match its Project fields: "
            + ", ".join(changed)
        )
    return unit, manifest.parent, str(receipt.get("project_id"))


def _reference_bytes(unit_dir: Path, reference: str) -> bytes:
    portable = reference.replace("\\", "/")
    path = Path(portable)
    if path.is_absolute() or ".." in path.parts or portable in {"", "."}:
        raise IntegrityError(
            f"Project Knowledge reference must be Unit-relative: {reference}"
        )
    try:
        return read_control_file(
            unit_dir / path,
            root=unit_dir,
            label=f"Project Knowledge source artifact {reference}",
        )
    except (FileNotFoundError, UnsafeControlFile, OSError) as exc:
        raise IntegrityError(
            f"cannot safely read Project Knowledge source artifact {reference}: {exc}"
        ) from exc


def _validate_candidate_sources(unit_dir: Path, candidate: dict[str, Any]) -> None:
    expected = {
        item["reference"]: item["digest"]
        for item in candidate.get("source_artifacts", [])
        if isinstance(item, dict) and isinstance(item.get("reference"), str)
    }
    referenced = {
        reference
        for entry in candidate.get("entries", [])
        if isinstance(entry, dict)
        for reference in entry.get("references", [])
        if isinstance(reference, str)
    }
    if referenced != set(expected):
        raise IntegrityError(
            "Project Knowledge candidate source_artifacts do not match entry references"
        )
    for reference, digest in expected.items():
        actual = "sha256:" + hashlib.sha256(
            _reference_bytes(unit_dir, reference)
        ).hexdigest()
        if actual != digest:
            raise IntegrityError(
                f"Project Knowledge source artifact changed after proposal: {reference}"
            )


def _candidate_reference(candidate_id: str) -> str:
    return f"{PROJECT_KNOWLEDGE_ROOT}/candidates/{candidate_id}.json"


def _source_unit_locator(unit_dir: Path, project_root: Path) -> dict[str, Any]:
    try:
        relative = unit_dir.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return {"base": "external", "path": None}
    return {"base": "project", "path": relative}


def load_project_knowledge_candidate(
    unit_dir: Path,
    reference: str,
    *,
    require_current_base: bool = False,
) -> dict[str, Any]:
    portable_reference = reference.replace("\\", "/")
    match = _CANDIDATE_REFERENCE.fullmatch(portable_reference)
    if match is None:
        raise IntegrityError("Knowledge Decision reference is not a Project Knowledge candidate")
    unit, project_root, project_id = _unit_project(unit_dir)
    candidate = _safe_json(
        project_root / portable_reference,
        root=project_root,
        label="Project Knowledge candidate",
    )
    if candidate.get("id") != match.group(1):
        raise IntegrityError("Project Knowledge candidate id does not match its path")
    issues = _candidate_issues(
        candidate, project_id=project_id, unit_id=str(unit.get("id"))
    )
    if issues:
        raise IntegrityError("invalid Project Knowledge candidate: " + "; ".join(issues))
    _validate_candidate_sources(unit_dir, candidate)
    if require_current_base:
        current = current_project_knowledge(project_root, project_id)
        current_digest = current.get("release_digest") if current else None
        if candidate.get("base_release_digest") != current_digest:
            raise IntegrityError(
                "Project Knowledge candidate is stale; propose it again from the latest release"
            )
    return candidate


def _validate_changes(entries: list[dict[str, Any]], current: dict[str, Any] | None) -> None:
    current_entries = current.get("entries", []) if current else []
    by_id = {
        entry.get("id"): entry
        for entry in current_entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    replaced: set[str] = set()
    for entry in entries:
        entry_id = str(entry["id"])
        if entry_id in by_id:
            raise IntegrityError(
                f"Project Knowledge entry id already exists and is immutable: {entry_id}"
            )
        replaces = entry.get("replaces")
        if replaces is None:
            continue
        previous = by_id.get(replaces)
        if previous is None or previous.get("status") != "approved":
            raise IntegrityError(
                f"Project Knowledge replacement target is not active: {replaces}"
            )
        if replaces in replaced:
            raise IntegrityError(
                f"Project Knowledge entry is replaced more than once: {replaces}"
            )
        replaced.add(replaces)


def propose_project_knowledge(
    path: str | Path,
    *,
    entries: list[dict[str, Any]],
    proposed_by: str,
) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise WorkflowError(f"Unit directory does not exist: {unit_dir}")
    if not isinstance(proposed_by, str) or not proposed_by.strip():
        raise WorkflowError("proposed_by must be a non-empty string")
    if not isinstance(entries, list):
        raise WorkflowError("entries must be a list")
    from isekai.catalog.ai_dlc.unit.common import unit_lock

    with unit_lock(unit_dir):
        unit, project_root, project_id = _unit_project(unit_dir)
        if unit.get("status") not in {"operating", "learned"}:
            raise LifecycleError(
                "Project Knowledge can only be proposed from an operating or learned Unit"
            )
        entry_issues = [
            f"entry {index}: {issue}"
            for index, entry in enumerate(entries)
            for issue in _entry_issues(entry, released=False)
        ]
        if not entries:
            entry_issues.append("Project Knowledge proposal requires at least one entry")
        if entry_issues:
            raise IntegrityError("invalid Project Knowledge entries: " + "; ".join(entry_issues))
        normalized_entries = copy.deepcopy(entries)
        references = sorted(
            {
                reference
                for entry in normalized_entries
                for reference in entry["references"]
            }
        )
        source_artifacts = [
            {
                "reference": reference,
                "digest": "sha256:"
                + hashlib.sha256(_reference_bytes(unit_dir, reference)).hexdigest(),
            }
            for reference in references
        ]
        with file_lock(
            project_root / PROJECT_KNOWLEDGE_LOCK,
            subject=f"Project Knowledge {project_id}",
        ):
            current = current_project_knowledge(project_root, project_id)
            _validate_changes(normalized_entries, current)
            now = datetime.now(timezone.utc)
            candidate_id = (
                "PKC-" + now.strftime("%Y%m%d%H%M%S%f") + "-" + uuid.uuid4().hex.upper()
            )
            candidate = {
                "id": candidate_id,
                "type": "project-knowledge-candidate",
                "schema_version": PROJECT_KNOWLEDGE_SCHEMA_VERSION,
                "project_id": project_id,
                "source_unit_id": unit["id"],
                "source_unit": _source_unit_locator(unit_dir, project_root),
                "base_release_digest": current.get("release_digest") if current else None,
                "proposed_by": proposed_by.strip(),
                "proposed_at": now.isoformat(),
                "entries": normalized_entries,
                "source_artifacts": source_artifacts,
            }
            candidate["candidate_digest"] = _candidate_digest(candidate)
            candidates = _managed_directory(
                project_root, f"{PROJECT_KNOWLEDGE_ROOT}/candidates", create=True
            )
            candidate_path = candidates / f"{candidate_id}.json"
            if candidate_path.exists():  # pragma: no cover - UUID collision
                raise IntegrityError("Project Knowledge candidate id collision")
            try:
                write_json_atomic(candidate_path, candidate)
                persisted = _safe_json(
                    candidate_path,
                    root=project_root,
                    label="Project Knowledge candidate postflight",
                )
                if persisted.get("candidate_digest") != candidate["candidate_digest"]:
                    raise IntegrityError("Project Knowledge candidate postflight failed")
            except Exception as exc:
                try:
                    candidate_path.unlink(missing_ok=True)
                except OSError as rollback_exc:  # pragma: no cover - secondary I/O failure
                    raise IntegrityError(
                        "Project Knowledge proposal failed and candidate rollback failed: "
                        + str(rollback_exc)
                    ) from exc
                raise
    return {
        "candidate": candidate,
        "reference": _candidate_reference(candidate_id),
        "next_action": "record an explicit knowledge Decision for this candidate",
    }


def _next_version(current: dict[str, Any] | None) -> str:
    if current is None:
        return "0.1.0"
    parts = str(current.get("version", "")).split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise IntegrityError("Project Knowledge current release has an invalid version")
    return f"{int(parts[0])}.{int(parts[1])}.{int(parts[2]) + 1}"


def promote_project_knowledge(path: str | Path, *, candidate: str) -> dict[str, Any]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise WorkflowError(f"Unit directory does not exist: {unit_dir}")
    from isekai.catalog.ai_dlc.unit.common import _unit_json, unit_lock
    from isekai.catalog.ai_dlc.unit.decisions import _decision_ledger_issues, _latest_decision

    candidate = candidate.replace("\\", "/")

    with unit_lock(unit_dir):
        unit, project_root, project_id = _unit_project(unit_dir)
        with file_lock(
            project_root / PROJECT_KNOWLEDGE_LOCK,
            subject=f"Project Knowledge {project_id}",
        ):
            proposed = load_project_knowledge_candidate(
                unit_dir, candidate, require_current_base=True
            )
            decisions = _unit_json(unit_dir, "decisions.json")
            decision_issues = _decision_ledger_issues(
                decisions,
                unit_id=str(unit.get("id")),
                scope=str(unit.get("scope")),
            )
            if decision_issues:
                raise IntegrityError(
                    "Project Knowledge promotion found an invalid Decision ledger: "
                    + "; ".join(decision_issues)
                )
            decision = _latest_decision(decisions, "knowledge")
            if decision is None or decision.get("outcome") != "approved":
                raise LifecycleError(
                    "Project Knowledge promotion requires the latest knowledge Decision to be approved"
                )
            subject = decision.get("approval_subject")
            expected = {
                "type": "project-knowledge-candidate",
                "id": proposed["id"],
                "digest": proposed["candidate_digest"],
                "reference": candidate,
            }
            if subject != expected:
                raise IntegrityError(
                    "latest knowledge Decision does not bind the selected Project Knowledge candidate"
                )
            catalog = _load_catalog(project_root, project_id)
            current = catalog["releases"][-1] if catalog and catalog["releases"] else None
            _validate_changes(proposed["entries"], current)
            now = datetime.now(timezone.utc).isoformat()
            released_entries = copy.deepcopy(current.get("entries", [])) if current else []
            for proposed_entry in proposed["entries"]:
                replacement = proposed_entry.get("replaces")
                if replacement is not None:
                    for existing in released_entries:
                        if existing.get("id") == replacement:
                            existing["status"] = "deprecated"
                            existing["superseded_by"] = proposed_entry["id"]
                            break
                released_entry = copy.deepcopy(proposed_entry)
                released_entry.update(
                    {
                        "status": "approved",
                        "source_unit_id": unit["id"],
                        "candidate_id": proposed["id"],
                        "decision_id": decision["id"],
                        "promoted_at": now,
                    }
                )
                released_entries.append(released_entry)
            version = _next_version(current)
            release = {
                "id": f"PKR-{version}",
                "type": "project-knowledge-release",
                "schema_version": PROJECT_KNOWLEDGE_SCHEMA_VERSION,
                "project_id": project_id,
                "version": version,
                "status": "approved",
                "entries": released_entries,
                "previous_release_digest": current.get("release_digest") if current else None,
                "promoted_at": now,
                "promoted_by": decision["decided_by"],
                "source_candidate_id": proposed["id"],
                "source_decision_id": decision["id"],
            }
            release["release_digest"] = _release_digest(release)
            releases = [*catalog["releases"], release] if catalog else [release]
            updated = {
                "type": "project-knowledge-catalog",
                "schema_version": PROJECT_KNOWLEDGE_SCHEMA_VERSION,
                "project_id": project_id,
                "current_version": version,
                "releases": releases,
            }
            updated["catalog_digest"] = _catalog_digest(updated)
            _managed_directory(project_root, PROJECT_KNOWLEDGE_ROOT, create=True)
            catalog_path = project_root / PROJECT_KNOWLEDGE_CATALOG
            catalog_before = (
                read_control_file(
                    catalog_path,
                    root=project_root,
                    label="Project Knowledge catalog snapshot",
                )
                if catalog is not None
                else None
            )
            try:
                write_json_atomic(catalog_path, updated)
                persisted = _load_catalog(project_root, project_id)
                if (
                    persisted is None
                    or persisted.get("catalog_digest") != updated["catalog_digest"]
                ):
                    raise IntegrityError("Project Knowledge promotion postflight failed")
            except Exception as exc:
                try:
                    if catalog_before is None:
                        catalog_path.unlink(missing_ok=True)
                    else:
                        write_bytes_atomic(catalog_path, catalog_before)
                except Exception as restore_exc:  # pragma: no cover - secondary I/O failure
                    raise IntegrityError(
                        "Project Knowledge promotion failed and catalog rollback failed: "
                        + str(restore_exc)
                    ) from exc
                raise
    return {"promoted": True, "release": release, "catalog": PROJECT_KNOWLEDGE_CATALOG}


def project_knowledge_status(path: str | Path = ".") -> dict[str, Any]:
    from .project import load_project

    manifest, project, _foundation, _extensions = load_project(path)
    catalog = _load_catalog(manifest.parent, str(project["id"]))
    current = catalog["releases"][-1] if catalog and catalog["releases"] else None
    candidates = candidate_status_details(
        manifest.parent,
        str(project["id"]),
        catalog,
        knowledge_root=PROJECT_KNOWLEDGE_ROOT,
        validate_sources=_validate_candidate_sources,
    )
    status_counts = {
        status: sum(candidate["status"] == status for candidate in candidates)
        for status in CANDIDATE_STATUSES
    }
    return {
        "project_id": project["id"],
        "catalog": PROJECT_KNOWLEDGE_CATALOG,
        "current_release": copy.deepcopy(current),
        "candidate_count": len(candidates),
        "candidate_status_counts": status_counts,
        "candidates": candidates,
        "schema_compatibility": schema_compatibility(),
    }


def knowledge_decision_candidate_issues(
    unit_dir: Path, decisions: dict[str, Any]
) -> list[str]:
    issues: list[str] = []
    for index, decision in enumerate(decisions.get("decisions", [])):
        if not isinstance(decision, dict) or decision.get("gate") != "knowledge":
            continue
        if decision.get("outcome") != "approved":
            continue
        subject = decision.get("approval_subject")
        if not isinstance(subject, dict):
            continue
        reference = subject.get("reference")
        if not isinstance(reference, str):
            continue
        try:
            candidate = load_project_knowledge_candidate(unit_dir, reference)
        except (IntegrityError, PreflightError) as exc:
            issues.append(f"decision {index}: {exc}")
            continue
        if subject.get("id") != candidate.get("id"):
            issues.append(f"decision {index}: Project Knowledge candidate id changed")
        if subject.get("digest") != candidate.get("candidate_digest"):
            issues.append(f"decision {index}: Project Knowledge candidate digest changed")
    return issues
