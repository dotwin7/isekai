from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ...support.scope import scope_pattern_matches
from ..project import _receipt_source_manifest_path
from ..routing import AGENT_ALLOWED_ACTIONS
from .common import (
    PROTECTED_UNIT_ARTIFACT_PREFIXES,
    PROTECTED_UNIT_ARTIFACTS,
    UNIT_LOCK_NAME,
    _unit_json,
)
from .common import _is_iso_timestamp


AUTHORIZATION_LEDGER_REQUIRED_FIELDS = {
    "type",
    "schema_version",
    "unit_id",
    "envelope_id",
    "approval_digest",
    "grants",
}
AUTHORIZATION_GRANT_REQUIRED_FIELDS = {
    "id",
    "action",
    "target",
    "stage",
    "iteration",
    "decision_id",
    "envelope_digest",
    "authorized_at",
}


def _authorization_ledger_digest(ledger: dict[str, Any]) -> str:
    encoded = json.dumps(
        ledger,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _normalize_authorization_target(
    unit_dir: Path,
    target: str,
) -> tuple[str | None, str | None]:
    if not isinstance(target, str) or not target.strip():
        return None, "Authorization requires a non-empty target"
    portable_target = target.replace("\\", "/")
    if portable_target.startswith("/") or re.match(r"^[A-Za-z]:", portable_target):
        return None, f"Authorization target must be project-relative: {target}"
    if ".." in portable_target.split("/"):
        return None, f"Target escapes the selected Project: {target}"
    try:
        receipt = _unit_json(unit_dir, "context-receipt.json")
    except ValueError as exc:
        return None, str(exc)
    try:
        manifest_path = _receipt_source_manifest_path(receipt, unit_dir=unit_dir)
    except ValueError as exc:
        return None, str(exc) + " for target authorization"
    if manifest_path.name != "project.json":
        return None, "Context Receipt source_manifest is not project.json"
    project_root = manifest_path.parent
    # Interpret both separator styles consistently. On POSIX, ``Path`` otherwise
    # treats a Windows drive path as an ordinary relative filename and only the
    # postflight ledger validator notices the mismatch.
    requested = Path(portable_target).expanduser()
    candidate = (
        requested.resolve()
        if requested.is_absolute()
        else (project_root / requested).resolve()
    )
    try:
        relative = candidate.relative_to(project_root)
    except ValueError:
        return None, f"Target escapes the selected Project: {target}"
    if relative == Path("."):
        return None, "Authorization target must identify a path inside the Project"
    return relative.as_posix(), None


def _is_case_insensitive_directory(directory: Path) -> bool:
    """Detect whether names in ``directory`` alias across letter case.

    POSIX path normalization does not expose per-volume case sensitivity.  Use
    an existing entry instead, so APFS case-insensitive aliases are protected
    without treating distinct files on case-sensitive filesystems as equal.
    """
    try:
        entries = directory.iterdir()
        for entry in entries:
            alternate_name = entry.name.swapcase()
            if alternate_name == entry.name:
                continue
            try:
                return (directory / alternate_name).samefile(entry)
            except FileNotFoundError:
                return False
            except OSError:
                continue
    except OSError:
        return False
    return False


def _filesystem_path_key(value: str, *, case_insensitive: bool) -> str:
    return value.casefold() if case_insensitive else value


def _authorization_target_protection_issue(
    unit_dir: Path,
    action: str,
    normalized_target: str,
) -> str | None:
    target_parts = Path(normalized_target).parts
    if not target_parts:
        return "Authorization target must identify a path inside the Project"
    receipt = _unit_json(unit_dir, "context-receipt.json")
    project_root = _receipt_source_manifest_path(receipt, unit_dir=unit_dir).parent
    project_case_insensitive = _is_case_insensitive_directory(project_root)
    target_key = _filesystem_path_key(
        normalized_target, case_insensitive=project_case_insensitive
    )
    target_part_key = _filesystem_path_key(
        target_parts[0], case_insensitive=project_case_insensitive
    )
    if action == "edit" and target_key in {"project.json", "isekai.lock.json"}:
        return f"Core control artifact cannot be edited through authorize: {normalized_target}"
    if action == "edit" and target_part_key in {".git", ".isekai"}:
        return f"Managed control path cannot be edited through authorize: {normalized_target}"
    candidate = (project_root / normalized_target).resolve()
    try:
        resolved_relative = candidate.relative_to(project_root)
    except ValueError:
        return (
            "Authorization target escapes the selected Project after path "
            f"resolution: {normalized_target}"
        )
    resolved_target = resolved_relative.as_posix()
    resolved_parts = resolved_relative.parts
    resolved_target_key = _filesystem_path_key(
        resolved_target, case_insensitive=project_case_insensitive
    )
    resolved_part_key = (
        _filesystem_path_key(
            resolved_parts[0], case_insensitive=project_case_insensitive
        )
        if resolved_parts
        else ""
    )
    if action == "edit" and resolved_target_key in {
        "project.json",
        "isekai.lock.json",
    }:
        return (
            "Core control artifact cannot be edited through authorize after path "
            f"resolution: {normalized_target} -> {resolved_target}"
        )
    if action == "edit" and resolved_part_key in {".git", ".isekai"}:
        return (
            "Managed control path cannot be edited through authorize after path "
            f"resolution: {normalized_target} -> {resolved_target}"
        )
    try:
        if candidate.exists() and not candidate.is_dir():
            metadata = candidate.stat()
            if not candidate.is_file():
                return (
                    "Authorization target must be a regular file: "
                    f"{normalized_target}"
                )
            if metadata.st_nlink > 1:
                return (
                    "Hard-linked files cannot be authorized: "
                    f"{normalized_target}"
                )
    except OSError as exc:
        return (
            "Authorization target metadata cannot be verified: "
            f"{normalized_target}: {exc}"
        )
    if action != "edit":
        return None
    if candidate.is_dir():
        return (
            "Directory targets cannot be edited through authorize; "
            f"authorize each concrete file instead: {normalized_target}"
        )
    current = candidate if candidate.is_dir() else candidate.parent
    while current == project_root or project_root in current.parents:
        if (current / "unit.json").is_file():
            relative = candidate.relative_to(current).as_posix()
            unit_case_insensitive = _is_case_insensitive_directory(current)
            relative_key = _filesystem_path_key(
                relative, case_insensitive=unit_case_insensitive
            )
            protected_artifacts = {
                _filesystem_path_key(
                    artifact, case_insensitive=unit_case_insensitive
                )
                for artifact in PROTECTED_UNIT_ARTIFACTS
            }
            protected_prefixes = tuple(
                _filesystem_path_key(
                    prefix, case_insensitive=unit_case_insensitive
                )
                for prefix in PROTECTED_UNIT_ARTIFACT_PREFIXES
            )
            if relative_key in protected_artifacts or any(
                relative_key.startswith(prefix) for prefix in protected_prefixes
            ):
                return (
                    "Unit control artifact cannot be edited through authorize: "
                    f"{normalized_target}"
                )
            break
        if current == project_root:
            break
        current = current.parent
    candidate_case_insensitive = _is_case_insensitive_directory(candidate.parent)
    candidate_name_key = _filesystem_path_key(
        candidate.name, case_insensitive=candidate_case_insensitive
    )
    lock_name_key = _filesystem_path_key(
        UNIT_LOCK_NAME, case_insensitive=candidate_case_insensitive
    )
    if candidate_name_key.startswith(lock_name_key):
        return f"Unit lock cannot be edited through authorize: {normalized_target}"
    return None


def _authorization_ledger_issues(
    ledger: Any,
    unit: dict[str, Any],
    envelope: dict[str, Any],
    *,
    unit_dir: Path | None = None,
) -> list[str]:
    if not isinstance(ledger, dict):
        return ["Execution authorization ledger must be an object"]
    issues: list[str] = []
    missing = sorted(AUTHORIZATION_LEDGER_REQUIRED_FIELDS - ledger.keys())
    if missing:
        issues.append(
            "Execution authorization ledger missing fields: " + ", ".join(missing)
        )
    if ledger.get("type") != "execution-authorization-ledger":
        issues.append("Execution authorization ledger has an invalid type")
    if ledger.get("schema_version") != "1.0.0":
        issues.append("Execution authorization ledger has an unsupported schema_version")
    if ledger.get("unit_id") != unit.get("id"):
        issues.append("Execution authorization ledger unit_id does not match Unit")
    if ledger.get("envelope_id") != envelope.get("id"):
        issues.append("Execution authorization ledger does not match the active Envelope")
    if ledger.get("approval_digest") != envelope.get("approval_digest"):
        issues.append("Execution authorization ledger digest does not match the active Envelope")
    grants = ledger.get("grants")
    if not isinstance(grants, list):
        issues.append("Execution authorization ledger grants must be a list")
        return issues
    max_iterations = envelope.get("max_iterations")
    if isinstance(max_iterations, int) and not isinstance(max_iterations, bool):
        if len(grants) > max_iterations:
            issues.append("Execution authorization ledger exceeds max_iterations")
    if grants and envelope.get("status") != "approved":
        issues.append("Execution authorization ledger has grants for an unapproved Envelope")
    seen_grant_ids: set[str] = set()
    for index, grant in enumerate(grants):
        if not isinstance(grant, dict):
            issues.append(f"Execution authorization grant {index} must be an object")
            continue
        missing_grant = sorted(AUTHORIZATION_GRANT_REQUIRED_FIELDS - grant.keys())
        if missing_grant:
            issues.append(
                f"Execution authorization grant {index} missing fields: "
                + ", ".join(missing_grant)
            )
        grant_id = grant.get("id")
        if not isinstance(grant_id, str) or not grant_id.strip():
            issues.append(f"Execution authorization grant {index} requires a non-empty id")
        elif grant_id in seen_grant_ids:
            issues.append(f"Execution authorization grant {index} has a duplicate id")
        else:
            seen_grant_ids.add(grant_id)
        if grant.get("iteration") != index + 1:
            issues.append(f"Execution authorization grant {index} has invalid iteration")
        if grant.get("envelope_digest") != envelope.get("approval_digest"):
            issues.append(
                f"Execution authorization grant {index} has invalid Envelope digest"
            )
        if grant.get("decision_id") != envelope.get("approval_decision_id"):
            issues.append(
                f"Execution authorization grant {index} does not match the "
                "Envelope approval Decision"
            )
        if not _is_iso_timestamp(grant.get("authorized_at")):
            issues.append(f"Execution authorization grant {index} has invalid timestamp")
        _validate_grant_action(index, grant, envelope, issues)
        _validate_grant_target(index, grant, envelope, unit_dir, issues)
    return issues


def _validate_grant_action(
    index: int,
    grant: dict[str, Any],
    envelope: dict[str, Any],
    issues: list[str],
) -> None:
    action = grant.get("action")
    if not isinstance(action, str) or action not in AGENT_ALLOWED_ACTIONS:
        issues.append(f"Execution authorization grant {index} has an unsupported action")
    else:
        allowed_actions = envelope.get("allowed_actions")
        forbidden_actions = envelope.get("forbidden_actions")
        if not isinstance(allowed_actions, list) or action not in allowed_actions:
            issues.append(
                f"Execution authorization grant {index} action is not allowed by the Envelope"
            )
        if isinstance(forbidden_actions, list) and action in forbidden_actions:
            issues.append(
                f"Execution authorization grant {index} action is forbidden by the Envelope"
            )
    stage = grant.get("stage")
    stages = envelope.get("stages")
    matching_stages = (
        [
            item
            for item in stages
            if isinstance(item, dict) and item.get("name") == stage
        ]
        if isinstance(stages, list)
        else []
    )
    if not isinstance(stage, str) or not stage.strip() or not matching_stages:
        issues.append(f"Execution authorization grant {index} has an invalid stage")
    elif isinstance(action, str) and action not in matching_stages[0].get(
        "allowed_actions", []
    ):
        issues.append(
            f"Execution authorization grant {index} action is not allowed in its stage"
        )


def _validate_grant_target(
    index: int,
    grant: dict[str, Any],
    envelope: dict[str, Any],
    unit_dir: Path | None,
    issues: list[str],
) -> None:
    target = grant.get("target")
    if not isinstance(target, str) or not target.strip():
        issues.append(f"Execution authorization grant {index} requires a target")
        return
    normalized_target = target.replace("\\", "/")
    if (
        normalized_target.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized_target)
        or ".." in normalized_target.split("/")
        or normalized_target in {"", "."}
    ):
        issues.append(
            f"Execution authorization grant {index} target must be project-relative"
        )
        return
    scopes = envelope.get("scope")
    if not isinstance(scopes, list) or not any(
        isinstance(pattern, str)
        and scope_pattern_matches(pattern, normalized_target)
        for pattern in scopes
    ):
        issues.append(
            f"Execution authorization grant {index} target is outside the "
            "Execution Envelope scope"
        )
    action = grant.get("action")
    if unit_dir is None or not isinstance(action, str):
        return
    try:
        protection_issue = _authorization_target_protection_issue(
            unit_dir, action, normalized_target
        )
    except ValueError as exc:
        issues.append(str(exc))
    else:
        if protection_issue is not None:
            issues.append(
                f"Execution authorization grant {index} is invalid: {protection_issue}"
            )
