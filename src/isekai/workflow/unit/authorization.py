from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ...support.scope import scope_pattern_matches
from ..routing import AGENT_ALLOWED_ACTIONS
from .common import PROTECTED_UNIT_ARTIFACTS, UNIT_LOCK_NAME, _unit_json
from .decisions import _is_iso_timestamp


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
    try:
        receipt = _unit_json(unit_dir, "context-receipt.json")
    except ValueError as exc:
        return None, str(exc)
    source_manifest = receipt.get("source_manifest")
    if not isinstance(source_manifest, str) or not source_manifest.strip():
        return None, "Context Receipt has no source_manifest for target authorization"
    manifest_path = Path(source_manifest).expanduser().resolve()
    if manifest_path.name != "project.json":
        return None, "Context Receipt source_manifest is not project.json"
    project_root = manifest_path.parent
    requested = Path(target).expanduser()
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


def _authorization_target_protection_issue(
    unit_dir: Path,
    action: str,
    normalized_target: str,
) -> str | None:
    if action != "edit":
        return None
    target_parts = Path(normalized_target).parts
    if not target_parts:
        return "Authorization target must identify a path inside the Project"
    if normalized_target in {"project.json", "isekai.lock.json"}:
        return f"Core control artifact cannot be edited through authorize: {normalized_target}"
    if target_parts[0] in {".git", ".isekai"}:
        return f"Managed control path cannot be edited through authorize: {normalized_target}"
    receipt = _unit_json(unit_dir, "context-receipt.json")
    project_root = Path(str(receipt["source_manifest"])).expanduser().resolve().parent
    candidate = (project_root / normalized_target).resolve()
    if (
        candidate.name in PROTECTED_UNIT_ARTIFACTS
        and (candidate.parent / "unit.json").is_file()
    ):
        return f"Unit control artifact cannot be edited through authorize: {normalized_target}"
    if candidate.name == UNIT_LOCK_NAME:
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
        or re.match(r"^[A-Za-z]:/", normalized_target)
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
