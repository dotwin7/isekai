from __future__ import annotations

import copy
import hashlib
import json
import os
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
from ..support.files import UnsafeControlFile, read_control_file
from .errors import WorkflowError
from .routing import ALLOWED_AGENT_LEVELS, WorkRoute


CONTEXT_RECEIPT_NON_BINDING_FIELDS = {"receipt_id", "generated_at"}
CONTEXT_RECEIPT_LOCATION_FIELDS = {"source_manifest"}
# Project Knowledge is deliberately versioned context, not a mutable Project
# contract. A Unit keeps the release pinned in its own Receipt while newer Units
# can adopt later approved releases without invalidating the earlier Unit.
CONTEXT_RECEIPT_EVOLVING_FIELDS = {"project_knowledge"}


def _context_receipt_id(receipt: dict[str, Any]) -> str:
    """Return the stable fingerprint for the Project context bound to a Unit."""
    body = {
        key: value
        for key, value in receipt.items()
        if key not in CONTEXT_RECEIPT_NON_BINDING_FIELDS
    }
    digest_input = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "CTX-" + hashlib.sha256(digest_input.encode()).hexdigest()[:16]


def _context_contract_value(field: str, value: Any) -> Any:
    """Return the location-independent value used to compare Project contracts.

    Context Receipts bind the full rule and extension payload, but filesystem
    locations are locators rather than contract meaning.  Removing only the
    generated ``source_path`` fields lets a Unit move with its Project without
    weakening the ID, version, provenance, rule, or content comparisons.
    """

    if field != "extension_assets":
        return value

    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    for extension in value:
        if not isinstance(extension, dict):
            normalized.append(extension)
            continue
        asset = dict(extension)
        asset.pop("source_path", None)
        normalized.append(asset)
    return normalized


def _context_contract_changed_fields(
    receipt: dict[str, Any],
    context: dict[str, Any],
) -> list[str]:
    """List semantic Project fields that changed, excluding path locators."""

    binding_fields = sorted(
        set(context)
        - CONTEXT_RECEIPT_NON_BINDING_FIELDS
        - CONTEXT_RECEIPT_LOCATION_FIELDS
        - CONTEXT_RECEIPT_EVOLVING_FIELDS
    )
    return [
        field
        for field in binding_fields
        if _context_contract_value(field, receipt.get(field))
        != _context_contract_value(field, context.get(field))
    ]


def _portable_context_receipt(
    receipt: dict[str, Any],
    *,
    project_root: Path,
    unit_dir: Path,
) -> dict[str, Any]:
    """Bind a Context Receipt to portable, explicitly based path locators."""

    portable = copy.deepcopy(receipt)
    manifest = Path(str(receipt["source_manifest"])).expanduser().resolve()
    try:
        source_manifest = Path(os.path.relpath(manifest, start=unit_dir)).as_posix()
        source_manifest_base = "unit"
    except ValueError:  # pragma: no cover - different Windows drive letters
        source_manifest = str(manifest)
        source_manifest_base = "absolute"
    portable["source_manifest"] = source_manifest
    portable["source_manifest_base"] = source_manifest_base

    extensions = portable.get("extension_assets")
    if isinstance(extensions, list):
        for extension in extensions:
            if not isinstance(extension, dict):
                continue
            source_path = extension.get("source_path")
            if not isinstance(source_path, str) or not source_path.strip():
                continue
            absolute_source = Path(source_path).expanduser().resolve()
            try:
                extension["source_path"] = absolute_source.relative_to(
                    project_root.resolve()
                ).as_posix()
            except ValueError:
                extension["source_path"] = str(absolute_source)

    portable["receipt_id"] = _context_receipt_id(portable)
    return portable


def _receipt_source_manifest_path(
    receipt: dict[str, Any],
    *,
    unit_dir: Path,
    selected_project: Path | None = None,
) -> Path:
    """Resolve new portable and legacy Context Receipt manifest locators."""

    source_manifest = receipt.get("source_manifest")
    if not isinstance(source_manifest, str) or not source_manifest.strip():
        raise WorkflowError("Context Receipt has no source_manifest")
    portable_source = source_manifest.replace("\\", "/")
    source_path = Path(portable_source).expanduser()
    base = receipt.get("source_manifest_base")
    if source_path.is_absolute() or base == "absolute":
        return source_path.resolve()
    if base == "unit":
        return (unit_dir / source_path).resolve()
    if base is not None:
        raise WorkflowError("Context Receipt has an unsupported source_manifest_base")
    # Legacy relative locators were normalized against the selected Project (or
    # the caller's working directory when no Project was available). Prefer an
    # explicitly selected Project. Authorization and verification only have the
    # Unit path, so recover the nearest ancestor Project whose complete semantic
    # context matches the bound Receipt instead of trusting process cwd.
    if selected_project is not None:
        return (selected_project.parent / source_path).resolve()
    for parent in unit_dir.parents:
        candidate = (parent / source_path).resolve()
        if candidate.name != "project.json" or not candidate.is_file():
            continue
        try:
            candidate_context = resolve_context(candidate, WorkRoute.UNIT)
        except FoundationError:
            continue
        if not _context_contract_changed_fields(receipt, candidate_context):
            return candidate
    raise WorkflowError(
        "legacy relative source_manifest cannot resolve the bound Project; "
        "run unit-migrate with an explicit Project"
    )


def _load_json(
    path: Path,
    *,
    root: Path | None = None,
    label: str = "project manifest",
) -> dict[str, Any]:
    try:
        content = read_control_file(
            path,
            root=root or path.parent,
            label=label,
        ).decode("utf-8")
        value = json.loads(content)
    except FileNotFoundError as exc:
        raise FoundationError(f"missing {label}: {path}") from exc
    except UnsafeControlFile as exc:
        raise FoundationError(str(exc)) from exc
    except OSError as exc:
        raise FoundationError(f"cannot safely read {label}: {path}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoundationError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise FoundationError(f"{label} must be a JSON object")
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
    lexical_extension_path = project_root / relative_path
    extension_path = lexical_extension_path.resolve()
    try:
        extension_path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise FoundationError(f"project extension path escapes project root: {relative_path}") from exc
    asset = _load_json(
        lexical_extension_path,
        root=project_root,
        label=f"project extension {asset_id}",
    )
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
        parent_asset = foundation.assets.get(str(condition.get("parent_asset", "")))
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
        manifest_path = Path(os.path.abspath(requested_path))
        if manifest_path.name != "project.json":
            raise FoundationError(
                f"project manifest must be named project.json: {manifest_path}"
            )
    project = _load_json(
        manifest_path,
        root=manifest_path.parent,
        label="project manifest",
    )
    required = {"id", "kind", "version", "foundation_path", "profiles", "extensions"}
    missing = sorted(required - project.keys())
    if missing:
        raise FoundationError(f"project manifest missing fields: {', '.join(missing)}")
    for field in ("id", "version", "foundation_path"):
        value = project.get(field)
        if not isinstance(value, str) or not value.strip():
            raise FoundationError(
                f"project manifest {field} must be a non-empty string"
            )
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
        if tree_digest(
            foundation.root, include_transients=True
        ) != foundation_pin.get("digest"):
            raise FoundationError("Project Foundation digest does not match isekai.lock.json")

    profiles = project["profiles"]
    if not isinstance(profiles, list) or any(
        not isinstance(profile, str) or not profile.strip()
        for profile in profiles
    ):
        raise FoundationError("project profiles must be a list of non-empty strings")
    if len(set(profiles)) != len(profiles):
        raise FoundationError("project profiles must not contain duplicates")
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
    extension_ids = [asset["id"] for asset in project_extensions]
    if len(set(extension_ids)) != len(extension_ids):
        raise FoundationError("project extensions must not contain duplicate IDs")
    rule_owners: dict[str, str] = {}
    for rule in foundation.rules():
        rule_id = str(rule["id"])
        if rule_id in rule_owners:
            raise FoundationError(f"duplicate applied Foundation rule id: {rule_id}")
        rule_owners[rule_id] = "Foundation"
    for extension in project_extensions:
        for rule in extension.get("content", {}).get("rules", []):
            rule_id = str(rule["id"])
            previous = rule_owners.get(rule_id)
            if previous is not None:
                raise FoundationError(
                    f"duplicate applied rule id {rule_id}: {previous} and "
                    f"extension {extension['id']}"
                )
            rule_owners[rule_id] = f"extension {extension['id']}"
    normalized_project = dict(project)
    normalized_project["schema_version"] = schema_version
    normalized_project["profiles"] = list(profiles)
    normalized_project["document_language"] = document_language
    normalized_project["maximum_agent_level"] = maximum_agent_level
    normalized_project["extensions"] = [asset["id"] for asset in project_extensions]
    return manifest_path, normalized_project, foundation, project_extensions


def resolve_context(path: str | Path, route: WorkRoute = WorkRoute.UNIT) -> dict[str, Any]:
    manifest_path, project, foundation, project_extensions = load_project(path)
    from .project_knowledge import (
        current_project_knowledge,
        summarize_project_knowledge,
    )

    applicable_rules: list[dict[str, Any]] = []
    rule_candidates = list(foundation.rules())
    rule_candidates.extend(
        rule
        for extension in project_extensions
        for rule in extension.get("content", {}).get("rules", [])
    )
    for rule in rule_candidates:
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
        "project_knowledge": summarize_project_knowledge(
            current_project_knowledge(manifest_path.parent, str(project["id"]))
        ),
        "source_manifest": str(manifest_path),
    }
    body["policy_ids"] = [item["id"] for item in body["policy_ids"]]
    receipt = {
        "receipt_id": _context_receipt_id(body),
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
        raise WorkflowError(f"project root does not exist or is not a directory: {project_root}")

    manifest_path = project_root / "project.json"
    if manifest_path.exists():
        raise FileExistsError(f"project manifest already exists: {manifest_path}")

    resolved_id = str(project_id or project_root.name).strip()
    if not resolved_id:
        raise WorkflowError("project id must be a non-empty string")
    if foundation_path is None:
        from ..distribution import load_install_lock

        lock = load_install_lock(project_root)
        pinned_path = lock.get("foundation", {}).get("path") if lock else None
        foundation_path = str(pinned_path or "foundation")
    if not isinstance(foundation_path, str) or not foundation_path.strip():
        raise WorkflowError("foundation_path must be a non-empty string")
    if document_language not in {"ko", "en"}:
        raise WorkflowError("document_language must be ko or en")
    if maximum_agent_level not in ALLOWED_AGENT_LEVELS:
        raise WorkflowError(
            "maximum_agent_level must be one of: "
            + ", ".join(sorted(ALLOWED_AGENT_LEVELS))
        )

    selected_profiles = list(profiles or [])
    if any(not isinstance(item, str) or not item.strip() for item in selected_profiles):
        raise WorkflowError("profiles must contain non-empty strings")
    foundation = load_foundation(project_root / foundation_path)
    for profile_id in selected_profiles:
        asset = foundation.assets.get(profile_id)
        if asset is None or asset.get("kind") != "profile":
            raise FoundationError(f"project references invalid profile: {profile_id}")

    manifest = {
        "id": resolved_id,
        "kind": "project",
        "schema_version": "1.0.0",
        "version": "0.2.0",
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
