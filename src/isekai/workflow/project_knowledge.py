from __future__ import annotations

import copy
import hashlib
import json
import re
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..support.files import UnsafeControlFile, metadata_is_path_alias, read_control_file
from ..support.jsonio import write_json_atomic
from ..support.locking import file_lock
from .errors import IntegrityError, LifecycleError, PreflightError, WorkflowError


PROJECT_KNOWLEDGE_ROOT = "project-knowledge"
PROJECT_KNOWLEDGE_CATALOG = f"{PROJECT_KNOWLEDGE_ROOT}/catalog.json"
PROJECT_KNOWLEDGE_LOCK = ".isekai-project-knowledge.lock"
PROJECT_KNOWLEDGE_SCHEMA_VERSION = "1.0.0"
PROJECT_KNOWLEDGE_KINDS = {"term", "convention", "guidance", "decision"}
_CANDIDATE_REFERENCE = re.compile(
    r"project-knowledge/candidates/(PKC-[A-Z0-9-]+)\.json"
)
_ENTRY_ID = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,63}")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def _canonical_digest(value: dict[str, Any], digest_field: str) -> str:
    subject = {key: item for key, item in value.items() if key != digest_field}
    encoded = json.dumps(
        subject, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _candidate_digest(candidate: dict[str, Any]) -> str:
    return _canonical_digest(candidate, "candidate_digest")


def _release_digest(release: dict[str, Any]) -> str:
    return _canonical_digest(release, "release_digest")


def _catalog_digest(catalog: dict[str, Any]) -> str:
    return _canonical_digest(catalog, "catalog_digest")


def _safe_json(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            read_control_file(path, root=root, label=label).decode("utf-8")
        )
    except FileNotFoundError as exc:
        raise IntegrityError(f"missing {label}: {path}") from exc
    except UnsafeControlFile as exc:
        raise IntegrityError(str(exc)) from exc
    except OSError as exc:
        raise IntegrityError(f"cannot safely read {label}: {path}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IntegrityError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrityError(f"{label} must be a JSON object")
    return value


def _managed_directory(project_root: Path, relative: str, *, create: bool) -> Path:
    directory = project_root / relative
    current = project_root
    for part in Path(relative).parts:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            if not create:
                raise IntegrityError(f"missing managed Project Knowledge path: {current}")
            try:
                current.mkdir()
                metadata = current.lstat()
            except OSError as exc:
                raise IntegrityError(
                    f"cannot create managed Project Knowledge path: {current}: {exc}"
                ) from exc
        if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise IntegrityError(
                f"managed Project Knowledge path must be a real directory: {current}"
            )
    return directory


def _entry_issues(entry: Any, *, released: bool) -> list[str]:
    if not isinstance(entry, dict):
        return ["Project Knowledge entry must be an object"]
    issues: list[str] = []
    required = {"id", "kind", "title", "statement", "scope", "owner", "references"}
    allowed = required | {"replaces"}
    if released:
        allowed |= {
            "status", "source_unit_id", "candidate_id", "decision_id",
            "promoted_at", "superseded_by",
        }
    missing = sorted(required - entry.keys())
    if missing:
        issues.append("Project Knowledge entry missing fields: " + ", ".join(missing))
    unexpected = sorted(set(entry) - allowed)
    if unexpected:
        issues.append(
            "Project Knowledge entry has unsupported fields: " + ", ".join(unexpected)
        )
    entry_id = entry.get("id")
    if not isinstance(entry_id, str) or _ENTRY_ID.fullmatch(entry_id) is None:
        issues.append("Project Knowledge entry id is invalid")
    if entry.get("kind") not in PROJECT_KNOWLEDGE_KINDS:
        issues.append("Project Knowledge entry kind is invalid")
    for field in ("title", "statement", "owner"):
        if not isinstance(entry.get(field), str) or not entry.get(field, "").strip():
            issues.append(f"Project Knowledge entry requires a non-empty {field}")
    for field in ("scope", "references"):
        values = entry.get(field)
        if not isinstance(values, list) or not values or any(
            not isinstance(value, str) or not value.strip() for value in values
        ):
            issues.append(
                f"Project Knowledge entry {field} must be a non-empty list of strings"
            )
        elif len(values) != len(set(values)):
            issues.append(f"Project Knowledge entry {field} must not contain duplicates")
    replaces = entry.get("replaces")
    if replaces is not None and (
        not isinstance(replaces, str) or _ENTRY_ID.fullmatch(replaces) is None
    ):
        issues.append("Project Knowledge entry replaces is invalid")
    if replaces == entry_id:
        issues.append("Project Knowledge entry cannot replace itself")
    if released:
        if entry.get("status") not in {"approved", "deprecated"}:
            issues.append("released Project Knowledge entry has an invalid status")
        for field in ("source_unit_id", "candidate_id", "decision_id", "promoted_at"):
            if not isinstance(entry.get(field), str) or not entry.get(field, "").strip():
                issues.append(f"released Project Knowledge entry requires {field}")
        if entry.get("status") == "deprecated" and not isinstance(
            entry.get("superseded_by"), str
        ):
            issues.append("deprecated Project Knowledge entry requires superseded_by")
    return issues


def _release_issues(release: Any, *, project_id: str) -> list[str]:
    if not isinstance(release, dict):
        return ["Project Knowledge release must be an object"]
    issues: list[str] = []
    required = {
        "id", "type", "schema_version", "project_id", "version", "status",
        "entries", "previous_release_digest", "promoted_at", "promoted_by",
        "source_candidate_id", "source_decision_id", "release_digest",
    }
    missing = sorted(required - release.keys())
    if missing:
        issues.append("Project Knowledge release missing fields: " + ", ".join(missing))
    if release.get("type") != "project-knowledge-release":
        issues.append("Project Knowledge release has an invalid type")
    if release.get("schema_version") != PROJECT_KNOWLEDGE_SCHEMA_VERSION:
        issues.append("Project Knowledge release has an unsupported schema_version")
    if release.get("project_id") != project_id:
        issues.append("Project Knowledge release project_id does not match Project")
    if release.get("status") != "approved":
        issues.append("Project Knowledge release status must be approved")
    entries = release.get("entries")
    if not isinstance(entries, list):
        issues.append("Project Knowledge release entries must be a list")
    else:
        ids: list[Any] = []
        for index, entry in enumerate(entries):
            issues.extend(
                f"entry {index}: {issue}" for issue in _entry_issues(entry, released=True)
            )
            if isinstance(entry, dict):
                ids.append(entry.get("id"))
        if len(ids) != len(set(ids)):
            issues.append("Project Knowledge release contains duplicate entry ids")
    previous = release.get("previous_release_digest")
    if previous is not None and not isinstance(previous, str):
        issues.append("Project Knowledge previous_release_digest is invalid")
    digest = release.get("release_digest")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        issues.append("Project Knowledge release_digest is invalid")
    elif digest != _release_digest(release):
        issues.append("Project Knowledge release digest does not match its record")
    return issues


def _catalog_issues(catalog: Any, *, project_id: str) -> list[str]:
    if not isinstance(catalog, dict):
        return ["Project Knowledge catalog must be an object"]
    issues: list[str] = []
    if catalog.get("type") != "project-knowledge-catalog":
        issues.append("Project Knowledge catalog has an invalid type")
    if catalog.get("schema_version") != PROJECT_KNOWLEDGE_SCHEMA_VERSION:
        issues.append("Project Knowledge catalog has an unsupported schema_version")
    if catalog.get("project_id") != project_id:
        issues.append("Project Knowledge catalog project_id does not match Project")
    releases = catalog.get("releases")
    if not isinstance(releases, list):
        return issues + ["Project Knowledge catalog releases must be a list"]
    previous: str | None = None
    versions: list[Any] = []
    for index, release in enumerate(releases):
        issues.extend(
            f"release {index}: {issue}"
            for issue in _release_issues(release, project_id=project_id)
        )
        if isinstance(release, dict):
            if release.get("previous_release_digest") != previous:
                issues.append(f"release {index}: release digest chain is broken")
            previous = release.get("release_digest")
            versions.append(release.get("version"))
    if len(versions) != len(set(versions)):
        issues.append("Project Knowledge catalog contains duplicate versions")
    expected_version = releases[-1].get("version") if releases else None
    if catalog.get("current_version") != expected_version:
        issues.append("Project Knowledge catalog current_version is invalid")
    digest = catalog.get("catalog_digest")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        issues.append("Project Knowledge catalog_digest is invalid")
    elif digest != _catalog_digest(catalog):
        issues.append("Project Knowledge catalog digest does not match its record")
    return issues


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


def project_knowledge_receipt_issues(
    value: Any, *, project_id: str
) -> list[str]:
    """Validate an optional release embedded in a Unit Context Receipt."""
    if value is None:
        return []
    return _release_issues(value, project_id=project_id)


def _unit_project(unit_dir: Path) -> tuple[dict[str, Any], Path, str]:
    from .project import (
        _context_contract_changed_fields,
        _receipt_source_manifest_path,
        resolve_context,
    )
    from .unit.common import _unit_json, _unit_preflight_issues

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


def _candidate_issues(candidate: Any, *, project_id: str, unit_id: str) -> list[str]:
    if not isinstance(candidate, dict):
        return ["Project Knowledge candidate must be an object"]
    issues: list[str] = []
    required = {
        "id", "type", "schema_version", "project_id", "source_unit_id",
        "base_release_digest", "proposed_by", "proposed_at", "entries",
        "source_artifacts", "candidate_digest",
    }
    missing = sorted(required - candidate.keys())
    if missing:
        issues.append("Project Knowledge candidate missing fields: " + ", ".join(missing))
    if candidate.get("type") != "project-knowledge-candidate":
        issues.append("Project Knowledge candidate has an invalid type")
    if candidate.get("schema_version") != PROJECT_KNOWLEDGE_SCHEMA_VERSION:
        issues.append("Project Knowledge candidate has an unsupported schema_version")
    if candidate.get("project_id") != project_id:
        issues.append("Project Knowledge candidate project_id does not match Project")
    if candidate.get("source_unit_id") != unit_id:
        issues.append("Project Knowledge candidate source_unit_id does not match Unit")
    entries = candidate.get("entries")
    if not isinstance(entries, list) or not entries:
        issues.append("Project Knowledge candidate entries must be a non-empty list")
    else:
        ids: list[Any] = []
        for index, entry in enumerate(entries):
            issues.extend(
                f"entry {index}: {issue}" for issue in _entry_issues(entry, released=False)
            )
            if isinstance(entry, dict):
                ids.append(entry.get("id"))
        if len(ids) != len(set(ids)):
            issues.append("Project Knowledge candidate contains duplicate entry ids")
    artifacts = candidate.get("source_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        issues.append("Project Knowledge candidate source_artifacts must be non-empty")
    else:
        references: list[Any] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                issues.append("Project Knowledge source artifact must be an object")
                continue
            references.append(artifact.get("reference"))
            if not isinstance(artifact.get("reference"), str):
                issues.append("Project Knowledge source artifact reference is invalid")
            if not isinstance(artifact.get("digest"), str) or _SHA256.fullmatch(
                artifact.get("digest", "")
            ) is None:
                issues.append("Project Knowledge source artifact digest is invalid")
        if len(references) != len(set(references)):
            issues.append("Project Knowledge source artifacts contain duplicates")
    digest = candidate.get("candidate_digest")
    if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
        issues.append("Project Knowledge candidate_digest is invalid")
    elif digest != _candidate_digest(candidate):
        issues.append("Project Knowledge candidate digest does not match its record")
    return issues


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
    from .unit.common import unit_lock

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
            write_json_atomic(candidate_path, candidate)
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
    from .unit.common import _unit_json, unit_lock
    from .unit.decisions import _decision_ledger_issues, _latest_decision

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
            write_json_atomic(project_root / PROJECT_KNOWLEDGE_CATALOG, updated)
            persisted = _load_catalog(project_root, project_id)
            if persisted is None or persisted.get("catalog_digest") != updated["catalog_digest"]:
                raise IntegrityError("Project Knowledge promotion postflight failed")
    return {"promoted": True, "release": release, "catalog": PROJECT_KNOWLEDGE_CATALOG}


def project_knowledge_status(path: str | Path = ".") -> dict[str, Any]:
    from .project import load_project

    manifest, project, _foundation, _extensions = load_project(path)
    catalog = _load_catalog(manifest.parent, str(project["id"]))
    current = catalog["releases"][-1] if catalog and catalog["releases"] else None
    candidates_path = manifest.parent / f"{PROJECT_KNOWLEDGE_ROOT}/candidates"
    candidate_count = 0
    if candidates_path.exists() or candidates_path.is_symlink():
        _managed_directory(
            manifest.parent, f"{PROJECT_KNOWLEDGE_ROOT}/candidates", create=False
        )
        candidate_count = sum(
            1
            for item in candidates_path.iterdir()
            if item.is_file() and not item.is_symlink() and _CANDIDATE_REFERENCE.fullmatch(
                f"{PROJECT_KNOWLEDGE_ROOT}/candidates/{item.name}"
            )
        )
    return {
        "project_id": project["id"],
        "catalog": PROJECT_KNOWLEDGE_CATALOG,
        "current_release": copy.deepcopy(current),
        "candidate_count": candidate_count,
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
