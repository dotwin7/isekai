from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..foundation import (
    FoundationError,
    FoundationRelease,
    load_foundation,
    validate_asset_provenance,
    validate_condition_definition,
    validate_rule_definition,
)
from .routing import ALLOWED_AGENT_LEVELS, WorkRoute


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FoundationError(f"missing project manifest: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FoundationError(f"invalid project manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise FoundationError("project manifest must be a JSON object")
    return value


def _load_project_extension(
    project_root: Path,
    entry: Any,
    foundation: FoundationRelease,
) -> dict[str, Any]:
    if isinstance(entry, str):
        asset = foundation.assets.get(entry)
        if asset is None or asset["kind"] != "extension":
            raise FoundationError(f"project references invalid extension: {entry}")
        return asset
    if not isinstance(entry, dict):
        raise FoundationError("project extension reference must be an ID or object")
    asset_id = entry.get("id")
    relative_path = entry.get("path")
    if not isinstance(asset_id, str) or not asset_id.strip():
        raise FoundationError("project extension reference needs id")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise FoundationError(f"project extension {asset_id} needs path")
    extension_path = (project_root / relative_path).resolve()
    try:
        extension_path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise FoundationError(f"project extension path escapes project root: {relative_path}") from exc
    asset = _load_json(extension_path)
    required = {
        "id", "kind", "version", "schema_version", "status", "owner", "provenance",
        "classification", "scope", "content",
    }
    missing = sorted(required - asset.keys())
    if missing:
        raise FoundationError(
            f"project extension {asset_id} missing fields: {', '.join(missing)}"
        )
    validate_asset_provenance(asset)
    if asset["id"] != asset_id:
        raise FoundationError(f"project extension descriptor mismatch: {asset_id}")
    if asset["kind"] != "extension":
        raise FoundationError(f"project asset {asset_id} kind must be extension")
    if asset["status"] not in {"draft", "approved", "deprecated"}:
        raise FoundationError(f"project extension {asset_id} has an invalid status")
    content = asset["content"]
    if not isinstance(content, dict) or not isinstance(content.get("namespace"), str) or not content["namespace"].strip():
        raise FoundationError(f"project extension {asset_id} requires a namespace")
    extends = asset.get("extends", [])
    extension_rules = content.get("rules", [])
    if not isinstance(extension_rules, list):
        raise FoundationError(f"project extension {asset_id} rules must be a list")
    level_strength = {"MAY": 0, "SHOULD": 1, "MUST": 2}
    for rule in extension_rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or rule.get("level") not in level_strength:
            raise FoundationError(f"project extension {asset_id} has an invalid rule")
        validate_rule_definition(rule, f"{asset_id} rule {rule.get('id', '<unknown>')}")
        condition = rule.get("condition")
        if not isinstance(condition, dict) or condition.get("type") != "extension-cannot-weaken-must":
            raise FoundationError(f"project extension {asset_id} rules require extension integrity condition")
        validate_condition_definition(condition, rule.get("id", "extension-rule"))
        parent_asset = foundation.assets.get(condition.get("parent_asset"))
        if parent_asset is None or condition.get("parent_level") != "MUST":
            raise FoundationError(f"project extension {asset_id} has an invalid parent MUST reference")
        parent_rules = parent_asset.get("content", {}).get("rules", [])
        parent_rule = next((item for item in parent_rules if isinstance(item, dict) and item.get("id") == condition.get("parent_rule_id")), None)
        if parent_rule is None or level_strength[rule["level"]] < level_strength["MUST"]:
            raise FoundationError(f"project extension {asset_id} weakens an inherited MUST rule")
    if not isinstance(extends, list):
        raise FoundationError(f"project extension {asset_id} extends must be a list")
    for parent in extends:
        if not isinstance(parent, dict):
            raise FoundationError(
                f"project extension {asset_id} extends must pin parent versions"
            )
        parent_id = parent.get("id")
        parent_version = parent.get("version")
        if not isinstance(parent_id, str) or not isinstance(parent_version, str):
            raise FoundationError(
                f"project extension {asset_id} has an invalid pinned parent"
            )
        foundation_parent = foundation.assets.get(parent_id)
        if foundation_parent is None:
            raise FoundationError(
                f"project extension {asset_id} references unknown Foundation asset: {parent_id}"
            )
        if parent_version != foundation_parent["version"]:
            raise FoundationError(
                f"project extension {asset_id} parent version mismatch: {parent_id}"
            )
    asset["source_path"] = str(extension_path)
    return asset


def load_project(
    path: str | Path,
) -> tuple[Path, dict[str, Any], FoundationRelease, list[dict[str, Any]]]:
    requested_path = Path(path).expanduser()
    if requested_path.is_dir():
        # Import lazily because session owns discovery and imports workflow types.
        from .session import discover_project

        manifest_path = discover_project(requested_path)
    else:
        manifest_path = requested_path.resolve()
    project = _load_json(manifest_path)
    required = {"id", "kind", "version", "foundation_path", "profiles", "extensions"}
    missing = sorted(required - project.keys())
    if missing:
        raise FoundationError(f"project manifest missing fields: {', '.join(missing)}")
    if project["kind"] != "project":
        raise FoundationError("project manifest kind must be project")
    schema_version = str(project.get("schema_version", "1.0.0"))
    if schema_version != "1.0.0":
        raise FoundationError("project manifest has an unsupported schema_version")
    foundation = load_foundation(manifest_path.parent / str(project["foundation_path"]))

    lock_path = manifest_path.parent / "isekai.lock.json"
    if lock_path.is_file():
        from ..distribution import load_install_lock, tree_digest

        lock = load_install_lock(manifest_path.parent)
        foundation_pin = lock.get("foundation") if lock else None
        if not isinstance(foundation_pin, dict):
            raise FoundationError("isekai.lock.json has no Foundation pin")
        if foundation.version != foundation_pin.get("version"):
            raise FoundationError("Project Foundation version does not match isekai.lock.json")
        if tree_digest(foundation.root) != foundation_pin.get("digest"):
            raise FoundationError("Project Foundation digest does not match isekai.lock.json")

    profiles = project["profiles"]
    if not isinstance(profiles, list):
        raise FoundationError("project profiles must be a list")
    document_language = str(project.get("document_language", "ko"))
    if document_language not in {"ko", "en"}:
        raise FoundationError("project document_language must be ko or en")
    maximum_agent_level = str(project.get("maximum_agent_level", "L0"))
    if maximum_agent_level not in ALLOWED_AGENT_LEVELS:
        raise FoundationError(
            "project maximum_agent_level must be one of: "
            + ", ".join(sorted(ALLOWED_AGENT_LEVELS))
        )
    for asset_id in profiles:
        asset = foundation.assets.get(asset_id)
        if asset is None or asset["kind"] != "profile":
            raise FoundationError(f"project references invalid profile: {asset_id}")

    raw_extensions = project["extensions"]
    if not isinstance(raw_extensions, list):
        raise FoundationError("project extensions must be a list")
    project_extensions = [
        _load_project_extension(manifest_path.parent, entry, foundation)
        for entry in raw_extensions
    ]
    normalized_project = dict(project)
    normalized_project["schema_version"] = schema_version
    normalized_project["profiles"] = list(profiles)
    normalized_project["document_language"] = document_language
    normalized_project["maximum_agent_level"] = maximum_agent_level
    normalized_project["extensions"] = [asset["id"] for asset in project_extensions]
    return manifest_path, normalized_project, foundation, project_extensions


def resolve_context(path: str | Path, route: WorkRoute = WorkRoute.UNIT) -> dict[str, Any]:
    manifest_path, project, foundation, project_extensions = load_project(path)
    applicable_rules: list[dict[str, Any]] = []
    for rule in foundation.rules():
        targets = rule.get("applies_to", [])
        if "*" in targets or route.value in targets:
            applicable_rules.append(dict(rule))

    body = {
        "project_id": project["id"],
        "project_version": project["version"],
        "project_schema_version": project["schema_version"],
        "document_language": project["document_language"],
        "foundation_id": foundation.manifest["id"],
        "foundation_version": foundation.version,
        "foundation_digest": foundation.contract_digest,
        "profiles": project["profiles"],
        "extensions": project["extensions"],
        "extension_assets": sorted(project_extensions, key=lambda asset: asset["id"]),
        "route": route.value,
        "maximum_agent_level": project.get("maximum_agent_level", "L0"),
        "rule_ids": sorted(rule["id"] for rule in applicable_rules),
        "rules": sorted(applicable_rules, key=lambda rule: rule["id"]),
        "policy_ids": sorted(foundation.assets_by_kind("policy"), key=lambda item: item["id"]),
        "source_manifest": str(manifest_path),
    }
    body["policy_ids"] = [item["id"] for item in body["policy_ids"]]
    digest_input = json.dumps(body, sort_keys=True, separators=(",", ":"))
    receipt = {
        "receipt_id": "CTX-" + hashlib.sha256(digest_input.encode()).hexdigest()[:16],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **body,
    }
    return receipt

def initialize_project(
    path: str | Path = ".",
    *,
    project_id: str | None = None,
    foundation_path: str | None = None,
    profiles: list[str] | None = None,
    document_language: str = "ko",
    maximum_agent_level: str = "L0",
    _postflight: Callable[[str | Path], object] = load_project,
) -> Path:
    project_root = Path(path).expanduser().resolve()
    if not project_root.is_dir():
        raise ValueError(f"project root does not exist or is not a directory: {project_root}")

    manifest_path = project_root / "project.json"
    if manifest_path.exists():
        raise FileExistsError(f"project manifest already exists: {manifest_path}")

    resolved_id = str(project_id or project_root.name).strip()
    if not resolved_id:
        raise ValueError("project id must be a non-empty string")
    if foundation_path is None:
        from ..distribution import load_install_lock

        lock = load_install_lock(project_root)
        pinned_path = lock.get("foundation", {}).get("path") if lock else None
        foundation_path = str(pinned_path or "foundation")
    if not isinstance(foundation_path, str) or not foundation_path.strip():
        raise ValueError("foundation_path must be a non-empty string")
    if document_language not in {"ko", "en"}:
        raise ValueError("document_language must be ko or en")
    if maximum_agent_level not in ALLOWED_AGENT_LEVELS:
        raise ValueError(
            "maximum_agent_level must be one of: "
            + ", ".join(sorted(ALLOWED_AGENT_LEVELS))
        )

    selected_profiles = list(profiles or [])
    if any(not isinstance(item, str) or not item.strip() for item in selected_profiles):
        raise ValueError("profiles must contain non-empty strings")
    foundation = load_foundation(project_root / foundation_path)
    for profile_id in selected_profiles:
        asset = foundation.assets.get(profile_id)
        if asset is None or asset.get("kind") != "profile":
            raise FoundationError(f"project references invalid profile: {profile_id}")

    manifest = {
        "id": resolved_id,
        "kind": "project",
        "schema_version": "1.0.0",
        "version": "0.1.0",
        "foundation_path": foundation_path,
        "profiles": selected_profiles,
        "extensions": [],
        "document_language": document_language,
        "maximum_agent_level": maximum_agent_level,
    }
    units_root = project_root / "units"
    created_units_root = not units_root.exists()
    units_root.mkdir(parents=False, exist_ok=True)
    try:
        with manifest_path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
        _postflight(manifest_path)
    except Exception:
        manifest_path.unlink(missing_ok=True)
        if created_units_root:
            try:
                units_root.rmdir()
            except OSError:
                pass
        raise
    return manifest_path
