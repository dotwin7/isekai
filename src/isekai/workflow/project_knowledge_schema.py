from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Any


PROJECT_KNOWLEDGE_SCHEMA_VERSION = "1.0.0"
PROJECT_KNOWLEDGE_READABLE_SCHEMA_VERSIONS = ("1.0.0",)
PROJECT_KNOWLEDGE_WRITE_SCHEMA_VERSION = "1.0.0"
PROJECT_KNOWLEDGE_KINDS = {"term", "convention", "guidance", "decision"}
CANDIDATE_REFERENCE = re.compile(
    r"project-knowledge/candidates/(PKC-[A-Z0-9-]+)\.json"
)
ENTRY_ID = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,63}")
SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
PROJECT_KNOWLEDGE_CANDIDATE_ID = re.compile(r"PKC-[A-Z0-9-]+")


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_iso_timestamp(value: Any) -> bool:
    if not _non_empty_string(value):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def canonical_digest(value: dict[str, Any], digest_field: str) -> str:
    subject = {key: item for key, item in value.items() if key != digest_field}
    encoded = json.dumps(
        subject, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def candidate_digest(candidate: dict[str, Any]) -> str:
    return canonical_digest(candidate, "candidate_digest")


def release_digest(release: dict[str, Any]) -> str:
    return canonical_digest(release, "release_digest")


def catalog_digest(catalog: dict[str, Any]) -> str:
    return canonical_digest(catalog, "catalog_digest")


def context_digest(context: dict[str, Any]) -> str:
    return canonical_digest(context, "context_digest")


def entry_issues(entry: Any, *, released: bool) -> list[str]:
    if not isinstance(entry, dict):
        return ["Project Knowledge entry must be an object"]
    issues: list[str] = []
    required = {"id", "kind", "title", "statement", "scope", "owner", "references"}
    allowed = required | {"replaces"}
    if released:
        allowed |= {
            "status",
            "source_unit_id",
            "candidate_id",
            "decision_id",
            "promoted_at",
            "superseded_by",
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
    if not isinstance(entry_id, str) or ENTRY_ID.fullmatch(entry_id) is None:
        issues.append("Project Knowledge entry id is invalid")
    if not isinstance(entry.get("kind"), str) or entry.get(
        "kind"
    ) not in PROJECT_KNOWLEDGE_KINDS:
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
        if field == "scope" and isinstance(values, list):
            for value in values:
                if not isinstance(value, str):
                    continue
                normalized = value.replace("\\", "/")
                if (
                    normalized.startswith("/")
                    or re.match(r"^[A-Za-z]:", normalized)
                    or ".." in normalized.split("/")
                ):
                    issues.append(
                        "Project Knowledge entry scope must be a project-relative pattern"
                    )
    replaces = entry.get("replaces")
    if replaces is not None and (
        not isinstance(replaces, str) or ENTRY_ID.fullmatch(replaces) is None
    ):
        issues.append("Project Knowledge entry replaces is invalid")
    if replaces == entry_id:
        issues.append("Project Knowledge entry cannot replace itself")
    if released:
        if not isinstance(entry.get("status"), str) or entry.get(
            "status"
        ) not in {"approved", "deprecated"}:
            issues.append("released Project Knowledge entry has an invalid status")
        for field in ("source_unit_id", "candidate_id", "decision_id"):
            if not _non_empty_string(entry.get(field)):
                issues.append(f"released Project Knowledge entry requires {field}")
        if not _is_iso_timestamp(entry.get("promoted_at")):
            issues.append(
                "released Project Knowledge entry promoted_at must be an ISO-8601 timestamp"
            )
        superseded_by = entry.get("superseded_by")
        if entry.get("status") == "deprecated" and (
            not isinstance(superseded_by, str)
            or ENTRY_ID.fullmatch(superseded_by) is None
        ):
            issues.append("deprecated Project Knowledge entry requires superseded_by")
    return issues


def release_issues(release: Any, *, project_id: str) -> list[str]:
    if not isinstance(release, dict):
        return ["Project Knowledge release must be an object"]
    issues: list[str] = []
    required = {
        "id",
        "type",
        "schema_version",
        "project_id",
        "version",
        "status",
        "entries",
        "previous_release_digest",
        "promoted_at",
        "promoted_by",
        "source_candidate_id",
        "source_decision_id",
        "release_digest",
    }
    allowed = set(required)
    missing = sorted(required - release.keys())
    if missing:
        issues.append("Project Knowledge release missing fields: " + ", ".join(missing))
    unexpected = sorted(set(release) - allowed)
    if unexpected:
        issues.append(
            "Project Knowledge release has unsupported fields: "
            + ", ".join(unexpected)
        )
    if release.get("type") != "project-knowledge-release":
        issues.append("Project Knowledge release has an invalid type")
    if release.get("schema_version") != PROJECT_KNOWLEDGE_SCHEMA_VERSION:
        issues.append("Project Knowledge release has an unsupported schema_version")
    if release.get("project_id") != project_id:
        issues.append("Project Knowledge release project_id does not match Project")
    version = release.get("version")
    if not isinstance(version, str) or SEMVER.fullmatch(version) is None:
        issues.append("Project Knowledge release version must be semantic versioning")
    release_id = release.get("id")
    if not _non_empty_string(release_id) or (
        isinstance(version, str)
        and SEMVER.fullmatch(version) is not None
        and release_id != f"PKR-{version}"
    ):
        issues.append("Project Knowledge release id does not match its version")
    if release.get("status") != "approved":
        issues.append("Project Knowledge release status must be approved")
    if not _is_iso_timestamp(release.get("promoted_at")):
        issues.append(
            "Project Knowledge release promoted_at must be an ISO-8601 timestamp"
        )
    for field in ("promoted_by", "source_decision_id"):
        if not _non_empty_string(release.get(field)):
            issues.append(f"Project Knowledge release requires {field}")
    source_candidate_id = release.get("source_candidate_id")
    if (
        not isinstance(source_candidate_id, str)
        or PROJECT_KNOWLEDGE_CANDIDATE_ID.fullmatch(source_candidate_id) is None
    ):
        issues.append("Project Knowledge release source_candidate_id is invalid")
    entries = release.get("entries")
    if not isinstance(entries, list):
        issues.append("Project Knowledge release entries must be a list")
    else:
        ids: list[str] = []
        for index, entry in enumerate(entries):
            issues.extend(
                f"entry {index}: {issue}"
                for issue in entry_issues(entry, released=True)
            )
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.append(entry["id"])
        if len(ids) != len(set(ids)):
            issues.append("Project Knowledge release contains duplicate entry ids")
    previous = release.get("previous_release_digest")
    if previous is not None and (
        not isinstance(previous, str) or SHA256.fullmatch(previous) is None
    ):
        issues.append("Project Knowledge previous_release_digest is invalid")
    digest = release.get("release_digest")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        issues.append("Project Knowledge release_digest is invalid")
    elif digest != release_digest(release):
        issues.append("Project Knowledge release digest does not match its record")
    return issues


def catalog_issues(catalog: Any, *, project_id: str) -> list[str]:
    if not isinstance(catalog, dict):
        return ["Project Knowledge catalog must be an object"]
    issues: list[str] = []
    required = {
        "type",
        "schema_version",
        "project_id",
        "current_version",
        "releases",
        "catalog_digest",
    }
    missing = sorted(required - catalog.keys())
    if missing:
        issues.append("Project Knowledge catalog missing fields: " + ", ".join(missing))
    unexpected = sorted(set(catalog) - required)
    if unexpected:
        issues.append(
            "Project Knowledge catalog has unsupported fields: "
            + ", ".join(unexpected)
        )
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
    versions: list[str] = []
    for index, release in enumerate(releases):
        issues.extend(
            f"release {index}: {issue}"
            for issue in release_issues(release, project_id=project_id)
        )
        if isinstance(release, dict):
            if release.get("previous_release_digest") != previous:
                issues.append(f"release {index}: release digest chain is broken")
            previous = release.get("release_digest")
            version = release.get("version")
            if isinstance(version, str):
                versions.append(version)
    if len(versions) != len(set(versions)):
        issues.append("Project Knowledge catalog contains duplicate versions")
    latest_release = releases[-1] if releases else None
    expected_version = (
        latest_release.get("version") if isinstance(latest_release, dict) else None
    )
    current_version = catalog.get("current_version")
    if current_version is not None and (
        not isinstance(current_version, str)
        or SEMVER.fullmatch(current_version) is None
    ):
        issues.append("Project Knowledge catalog current_version is invalid")
    if catalog.get("current_version") != expected_version:
        issues.append("Project Knowledge catalog current_version is invalid")
    digest = catalog.get("catalog_digest")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        issues.append("Project Knowledge catalog_digest is invalid")
    elif digest != catalog_digest(catalog):
        issues.append("Project Knowledge catalog digest does not match its record")
    return issues


def candidate_issues(candidate: Any, *, project_id: str, unit_id: str) -> list[str]:
    if not isinstance(candidate, dict):
        return ["Project Knowledge candidate must be an object"]
    issues: list[str] = []
    required = {
        "id",
        "type",
        "schema_version",
        "project_id",
        "source_unit_id",
        "base_release_digest",
        "proposed_by",
        "proposed_at",
        "entries",
        "source_artifacts",
        "candidate_digest",
    }
    allowed = required | {"source_unit"}
    missing = sorted(required - candidate.keys())
    if missing:
        issues.append("Project Knowledge candidate missing fields: " + ", ".join(missing))
    unexpected = sorted(set(candidate) - allowed)
    if unexpected:
        issues.append(
            "Project Knowledge candidate has unsupported fields: "
            + ", ".join(unexpected)
        )
    if candidate.get("type") != "project-knowledge-candidate":
        issues.append("Project Knowledge candidate has an invalid type")
    if candidate.get("schema_version") != PROJECT_KNOWLEDGE_SCHEMA_VERSION:
        issues.append("Project Knowledge candidate has an unsupported schema_version")
    if candidate.get("project_id") != project_id:
        issues.append("Project Knowledge candidate project_id does not match Project")
    if candidate.get("source_unit_id") != unit_id:
        issues.append("Project Knowledge candidate source_unit_id does not match Unit")
    candidate_id = candidate.get("id")
    if (
        not isinstance(candidate_id, str)
        or PROJECT_KNOWLEDGE_CANDIDATE_ID.fullmatch(candidate_id) is None
    ):
        issues.append("Project Knowledge candidate id is invalid")
    base_release_digest = candidate.get("base_release_digest")
    if base_release_digest is not None and (
        not isinstance(base_release_digest, str)
        or SHA256.fullmatch(base_release_digest) is None
    ):
        issues.append("Project Knowledge candidate base_release_digest is invalid")
    if not _non_empty_string(candidate.get("proposed_by")):
        issues.append("Project Knowledge candidate requires proposed_by")
    if not _is_iso_timestamp(candidate.get("proposed_at")):
        issues.append(
            "Project Knowledge candidate proposed_at must be an ISO-8601 timestamp"
        )
    source_unit = candidate.get("source_unit")
    if source_unit is not None:
        if not isinstance(source_unit, dict):
            issues.append("Project Knowledge candidate source_unit must be an object")
        else:
            base = source_unit.get("base")
            locator = source_unit.get("path")
            if set(source_unit) != {"base", "path"}:
                issues.append("Project Knowledge candidate source_unit fields are invalid")
            if base == "project":
                if not isinstance(locator, str) or not locator.strip():
                    issues.append(
                        "project-relative Project Knowledge source_unit requires path"
                    )
                else:
                    normalized = locator.replace("\\", "/")
                    if (
                        normalized.startswith("/")
                        or re.match(r"^[A-Za-z]:", normalized)
                        or ".." in normalized.split("/")
                    ):
                        issues.append(
                            "Project Knowledge source_unit path must be project-relative"
                        )
            elif base == "external":
                if locator is not None:
                    issues.append(
                        "external Project Knowledge source_unit cannot persist a machine path"
                    )
            else:
                issues.append("Project Knowledge candidate source_unit base is invalid")
    entries = candidate.get("entries")
    if not isinstance(entries, list) or not entries:
        issues.append("Project Knowledge candidate entries must be a non-empty list")
    else:
        ids: list[str] = []
        for index, entry in enumerate(entries):
            issues.extend(
                f"entry {index}: {issue}"
                for issue in entry_issues(entry, released=False)
            )
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.append(entry["id"])
        if len(ids) != len(set(ids)):
            issues.append("Project Knowledge candidate contains duplicate entry ids")
    artifacts = candidate.get("source_artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        issues.append("Project Knowledge candidate source_artifacts must be non-empty")
    else:
        references: list[str] = []
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                issues.append("Project Knowledge source artifact must be an object")
                continue
            unexpected_artifact_fields = sorted(
                set(artifact) - {"reference", "digest"}
            )
            if unexpected_artifact_fields:
                issues.append(
                    "Project Knowledge source artifact has unsupported fields: "
                    + ", ".join(unexpected_artifact_fields)
                )
            reference = artifact.get("reference")
            if not _non_empty_string(reference):
                issues.append("Project Knowledge source artifact reference is invalid")
            elif isinstance(reference, str):
                references.append(reference)
            if not isinstance(artifact.get("digest"), str) or SHA256.fullmatch(
                artifact.get("digest", "")
            ) is None:
                issues.append("Project Knowledge source artifact digest is invalid")
        if len(references) != len(set(references)):
            issues.append("Project Knowledge source artifacts contain duplicates")
    digest = candidate.get("candidate_digest")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        issues.append("Project Knowledge candidate_digest is invalid")
    elif digest != candidate_digest(candidate):
        issues.append("Project Knowledge candidate digest does not match its record")
    return issues


def _literal_scope_prefix(pattern: str) -> tuple[str, ...]:
    parts: list[str] = []
    for part in pattern.replace("\\", "/").strip("/").split("/"):
        if not part or part == "**" or "*" in part or "?" in part:
            break
        parts.append(part)
    return tuple(parts)


def scope_patterns_may_overlap(left: str, right: str) -> bool:
    """Conservatively detect whether two path-like scopes may intersect.

    False positives retain a little extra context; differing literal prefixes
    are the only case rejected, so wildcard uncertainty never hides knowledge.
    """
    normalized_left = left.replace("\\", "/").strip("/")
    normalized_right = right.replace("\\", "/").strip("/")
    if normalized_left == normalized_right:
        return True
    left_prefix = _literal_scope_prefix(normalized_left)
    right_prefix = _literal_scope_prefix(normalized_right)
    if not left_prefix or not right_prefix:
        return True
    common = min(len(left_prefix), len(right_prefix))
    return left_prefix[:common] == right_prefix[:common]


def select_release_context(
    release: dict[str, Any] | None,
    work_scope: list[str],
) -> dict[str, Any] | None:
    if release is None:
        return None
    active = [
        entry
        for entry in release.get("entries", [])
        if isinstance(entry, dict) and entry.get("status") == "approved"
    ]
    selected = (
        active
        if not work_scope
        else [
            entry
            for entry in active
            if any(
                scope_patterns_may_overlap(entry_scope, unit_scope)
                for entry_scope in entry.get("scope", [])
                if isinstance(entry_scope, str)
                for unit_scope in work_scope
            )
        ]
    )
    context = {
        "type": "project-knowledge-context",
        "schema_version": PROJECT_KNOWLEDGE_SCHEMA_VERSION,
        "project_id": release["project_id"],
        "release_id": release["id"],
        "release_version": release["version"],
        "release_digest": release["release_digest"],
        "entries": copy.deepcopy(selected),
        "selection": {
            "mode": "all-active" if not work_scope else "literal-prefix-overlap-v1",
            "work_scope": list(work_scope),
            "active_entry_count": len(active),
            "selected_entry_count": len(selected),
        },
    }
    context["context_digest"] = context_digest(context)
    return context


def summarize_release(release: dict[str, Any] | None) -> dict[str, Any] | None:
    if release is None:
        return None
    entries = [entry for entry in release.get("entries", []) if isinstance(entry, dict)]
    return {
        "type": "project-knowledge-summary",
        "schema_version": PROJECT_KNOWLEDGE_SCHEMA_VERSION,
        "project_id": release["project_id"],
        "release_id": release["id"],
        "release_version": release["version"],
        "release_digest": release["release_digest"],
        "active_entry_count": sum(entry.get("status") == "approved" for entry in entries),
        "deprecated_entry_count": sum(
            entry.get("status") == "deprecated" for entry in entries
        ),
    }


def context_issues(context: Any, *, project_id: str) -> list[str]:
    if not isinstance(context, dict):
        return ["Project Knowledge context must be an object"]
    issues: list[str] = []
    required = {
        "type",
        "schema_version",
        "project_id",
        "release_id",
        "release_version",
        "release_digest",
        "entries",
        "selection",
        "context_digest",
    }
    missing = sorted(required - context.keys())
    if missing:
        issues.append("Project Knowledge context missing fields: " + ", ".join(missing))
    unexpected = sorted(set(context) - required)
    if unexpected:
        issues.append(
            "Project Knowledge context has unsupported fields: "
            + ", ".join(unexpected)
        )
    if context.get("type") != "project-knowledge-context":
        issues.append("Project Knowledge context has an invalid type")
    if context.get("schema_version") != PROJECT_KNOWLEDGE_SCHEMA_VERSION:
        issues.append("Project Knowledge context has an unsupported schema_version")
    if context.get("project_id") != project_id:
        issues.append("Project Knowledge context project_id does not match Project")
    for field in ("release_id", "release_version"):
        if not isinstance(context.get(field), str) or not context.get(field, "").strip():
            issues.append(f"Project Knowledge context requires a non-empty {field}")
    if not isinstance(context.get("release_digest"), str) or SHA256.fullmatch(
        context.get("release_digest", "")
    ) is None:
        issues.append("Project Knowledge context release_digest is invalid")
    entries = context.get("entries")
    if not isinstance(entries, list):
        issues.append("Project Knowledge context entries must be a list")
    else:
        ids: list[str] = []
        for index, entry in enumerate(entries):
            issues.extend(
                f"entry {index}: {issue}"
                for issue in entry_issues(entry, released=True)
            )
            if isinstance(entry, dict) and entry.get("status") != "approved":
                issues.append(f"entry {index}: selected entry must be approved")
            if isinstance(entry, dict) and isinstance(entry.get("id"), str):
                ids.append(entry["id"])
        if len(ids) != len(set(ids)):
            issues.append("Project Knowledge context contains duplicate entry ids")
    selection = context.get("selection")
    if not isinstance(selection, dict):
        issues.append("Project Knowledge context selection must be an object")
    else:
        selection_fields = {
            "mode",
            "work_scope",
            "active_entry_count",
            "selected_entry_count",
        }
        missing_selection_fields = sorted(selection_fields - selection.keys())
        if missing_selection_fields:
            issues.append(
                "Project Knowledge context selection missing fields: "
                + ", ".join(missing_selection_fields)
            )
        unexpected_selection_fields = sorted(set(selection) - selection_fields)
        if unexpected_selection_fields:
            issues.append(
                "Project Knowledge context selection has unsupported fields: "
                + ", ".join(unexpected_selection_fields)
            )
        scopes = selection.get("work_scope")
        if not isinstance(scopes, list) or any(
            not isinstance(scope, str) or not scope.strip() for scope in scopes
        ):
            issues.append("Project Knowledge context work_scope must be a list of strings")
        elif len(scopes) != len(set(scopes)):
            issues.append("Project Knowledge context work_scope must not contain duplicates")
        if not isinstance(selection.get("mode"), str) or selection.get(
            "mode"
        ) not in {
            "all-active",
            "literal-prefix-overlap-v1",
        }:
            issues.append("Project Knowledge context selection mode is invalid")
        active_count = selection.get("active_entry_count")
        if (
            not isinstance(active_count, int)
            or isinstance(active_count, bool)
            or active_count < (len(entries) if isinstance(entries, list) else 0)
        ):
            issues.append("Project Knowledge active_entry_count is invalid")
        if selection.get("selected_entry_count") != (
            len(entries) if isinstance(entries, list) else None
        ):
            issues.append("Project Knowledge selected_entry_count is invalid")
    digest = context.get("context_digest")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        issues.append("Project Knowledge context_digest is invalid")
    elif digest != context_digest(context):
        issues.append("Project Knowledge context digest does not match its record")
    return issues


def receipt_issues(value: Any, *, project_id: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict) and value.get("type") == "project-knowledge-release":
        return release_issues(value, project_id=project_id)
    return context_issues(value, project_id=project_id)


def schema_compatibility() -> dict[str, Any]:
    """Expose the fail-closed schema evolution contract to adapters and operators."""
    return {
        "catalog_schema_version": PROJECT_KNOWLEDGE_SCHEMA_VERSION,
        "readable_schema_versions": list(PROJECT_KNOWLEDGE_READABLE_SCHEMA_VERSIONS),
        "write_schema_version": PROJECT_KNOWLEDGE_WRITE_SCHEMA_VERSION,
        "migration_required": False,
        "automatic_migration": False,
        "unknown_schema_policy": "fail-closed",
        "unit_receipt_policy": "pinned-receipts-are-never-auto-rewritten",
    }
