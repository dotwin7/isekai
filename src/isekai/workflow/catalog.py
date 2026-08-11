from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..foundation import FoundationError
from ..support.files import UnsafeControlFile, read_control_file


CATALOG_SCHEMA_VERSION = "1.0.0"
CATALOG_URI = "isekai://runtime/catalog"
CATALOG_ROOT = Path(__file__).resolve().parents[3] / "catalog"
SOURCE_CATALOG_FILE = "catalog.json"
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
_STATUSES = {"active", "preview", "deprecated"}
_DELIVERY_MODES = {"core-bundled", "catalog-package"}
_BINDING_MODES = {"single", "multiple"}
_AUTHORITY = "cannot-expand-foundation-project-or-unit-authority"


def _digest(value: dict[str, Any], field: str) -> str:
    subject = {key: item for key, item in value.items() if key != field}
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _string_list(value: dict[str, Any], field: str, *, entry_id: str) -> list[str]:
    items = value.get(field)
    if (
        not isinstance(items, list)
        or any(not isinstance(item, str) or not item.strip() for item in items)
        or len(set(items)) != len(items)
    ):
        raise FoundationError(
            f"ISEKAI catalog entry {entry_id} {field} must be a unique string list"
        )
    return sorted(item.strip() for item in items)


def _load_json(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        content = read_control_file(
            path,
            root=root,
            label=label,
        ).decode("utf-8")
        value = json.loads(content)
    except FileNotFoundError as exc:
        raise FoundationError(f"missing {label}: {path}") from exc
    except (UnsafeControlFile, OSError) as exc:
        raise FoundationError(f"cannot safely read {label}: {exc}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FoundationError(f"invalid {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise FoundationError(f"{label} must be an object")
    return value


def _load_catalog_entry(
    path: Path,
    *,
    root: Path,
    expected_id: str,
    expected_version: str,
) -> dict[str, Any]:
    value = _load_json(
        path,
        root=root,
        label=f"ISEKAI catalog entry manifest {expected_id}@{expected_version}",
    )
    required = {
        "id",
        "kind",
        "schema_version",
        "version",
        "status",
        "title",
        "description",
        "control_protocol",
        "delivery",
        "binding_mode",
        "actions",
        "resources",
        "authority",
    }
    missing = sorted(required - value.keys())
    entry_id = str(value.get("id", expected_id))
    if missing:
        raise FoundationError(
            f"ISEKAI catalog entry {entry_id} missing fields: {', '.join(missing)}"
        )
    if _ID.fullmatch(entry_id) is None or entry_id != expected_id:
        raise FoundationError(f"ISEKAI catalog entry has an invalid id: {entry_id}")
    if value["kind"] != "isekai-catalog-entry":
        raise FoundationError(f"ISEKAI catalog entry {entry_id} has an invalid kind")
    if value["schema_version"] != CATALOG_SCHEMA_VERSION:
        raise FoundationError(
            f"ISEKAI catalog entry {entry_id} has an unsupported schema_version"
        )
    version = value.get("version")
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise FoundationError(f"ISEKAI catalog entry {entry_id} has an invalid version")
    if version != expected_version:
        raise FoundationError(
            f"ISEKAI catalog entry {entry_id} version does not match source catalog"
        )
    if not isinstance(value["status"], str) or value["status"] not in _STATUSES:
        raise FoundationError(f"ISEKAI catalog entry {entry_id} has an invalid status")
    for field in ("title", "description"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise FoundationError(f"ISEKAI catalog entry {entry_id} requires {field}")
    if value["control_protocol"] != "1.2.0":
        raise FoundationError(
            f"ISEKAI catalog entry {entry_id} requires an incompatible control protocol"
        )
    if not isinstance(value["delivery"], str) or value[
        "delivery"
    ] not in _DELIVERY_MODES:
        raise FoundationError(f"ISEKAI catalog entry {entry_id} has invalid delivery")
    if not isinstance(value.get("binding_mode"), str) or value.get(
        "binding_mode"
    ) not in _BINDING_MODES:
        raise FoundationError(f"ISEKAI catalog entry {entry_id} has invalid binding_mode")
    if value["authority"] != _AUTHORITY:
        raise FoundationError(f"ISEKAI catalog entry {entry_id} has invalid authority")
    normalized = dict(value)
    normalized["actions"] = _string_list(value, "actions", entry_id=entry_id)
    normalized["resources"] = _string_list(
        value,
        "resources",
        entry_id=entry_id,
    )
    normalized["package_path"] = path.parent.relative_to(root).as_posix()
    normalized["active"] = value["status"] == "active"
    normalized["entry_digest"] = _digest(normalized, "entry_digest")
    return normalized


def load_catalog() -> dict[str, Any]:
    root = CATALOG_ROOT
    if not root.is_dir():
        raise FoundationError("ISEKAI Catalog directory is missing")
    source = _load_json(
        root / SOURCE_CATALOG_FILE,
        root=root,
        label="ISEKAI Catalog source catalog",
    )
    if source.get("kind") != "isekai-source-catalog":
        raise FoundationError("ISEKAI Catalog source catalog has an invalid kind")
    if source.get("schema_version") != CATALOG_SCHEMA_VERSION:
        raise FoundationError(
            "ISEKAI Catalog source catalog has an unsupported schema_version"
        )
    if source.get("control_protocol") != "1.2.0":
        raise FoundationError(
            "ISEKAI Catalog source catalog requires an incompatible control protocol"
        )
    entries = source.get("entries")
    if not isinstance(entries, list) or not entries:
        raise FoundationError("ISEKAI Catalog source catalog cannot be empty")
    loaded_entries: list[dict[str, Any]] = []
    manifest_paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise FoundationError("ISEKAI Catalog source catalog entries must be objects")
        entry_id = entry.get("id")
        version = entry.get("version")
        manifest = entry.get("manifest")
        if not isinstance(entry_id, str) or _ID.fullmatch(entry_id) is None:
            raise FoundationError("ISEKAI Catalog source catalog has an invalid entry ID")
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            raise FoundationError(
                f"ISEKAI Catalog source catalog has an invalid version for {entry_id}"
            )
        expected = Path(entry_id) / version / "manifest.json"
        if not isinstance(manifest, str) or Path(manifest) != expected:
            raise FoundationError(
                f"ISEKAI Catalog source catalog has an invalid manifest path for {entry_id}"
            )
        manifest_paths.append(manifest)
        loaded_entries.append(
            _load_catalog_entry(
                root / expected,
                root=root,
                expected_id=entry_id,
                expected_version=version,
            )
        )
    identifiers = [str(entry["id"]) for entry in loaded_entries]
    if len(set(identifiers)) != len(identifiers):
        raise FoundationError("ISEKAI catalog has duplicate IDs")
    if len(set(manifest_paths)) != len(manifest_paths):
        raise FoundationError("ISEKAI Catalog source catalog has duplicate manifests")
    catalog = {
        "type": "isekai-catalog",
        "schema_version": CATALOG_SCHEMA_VERSION,
        "control_protocol": "1.2.0",
        "entries": loaded_entries,
    }
    catalog["catalog_digest"] = _digest(catalog, "catalog_digest")
    return catalog


def catalog_resources(catalog: dict[str, Any]) -> list[dict[str, str]]:
    resources = [
        {
            "uri": CATALOG_URI,
            "name": "isekai-catalog",
            "title": "ISEKAI Catalog",
            "description": "Versioned entries registered in this ISEKAI Runtime.",
            "mimeType": "application/json",
        }
    ]
    entries = catalog.get("entries", [])
    if not isinstance(entries, list):
        return resources
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        resources.append(
            {
                "uri": f"{CATALOG_URI}/{entry['id']}",
                "name": str(entry["id"]),
                "title": str(entry["title"]),
                "description": str(entry["description"]),
                "mimeType": "application/json",
            }
        )
    return resources


def read_catalog_resource(catalog: dict[str, Any], uri: str) -> dict[str, str]:
    if uri == CATALOG_URI:
        value: object = catalog
    else:
        prefix = CATALOG_URI + "/"
        entry_id = uri[len(prefix) :] if uri.startswith(prefix) else ""
        entries = catalog.get("entries", [])
        value = next(
            (
                entry
                for entry in entries
                if isinstance(entry, dict) and entry.get("id") == entry_id
            ),
            None,
        ) if isinstance(entries, list) else None
        if value is None:
            raise FoundationError(f"unknown ISEKAI catalog entry resource: {uri}")
    return {
        "uri": uri,
        "mimeType": "application/json",
        "text": json.dumps(value, ensure_ascii=False, indent=2),
    }


def select_active_entries(catalog: dict[str, Any]) -> dict[str, Any]:
    """Summarize catalog entries and auto-select when exactly one is active."""
    entries = catalog.get("entries", [])
    if not isinstance(entries, list):
        return {"catalog_entries": [], "active_entries": [], "selected_entry": None}
    summaries = [
        {
            "id": entry.get("id"),
            "version": entry.get("version"),
            "status": entry.get("status"),
            "active": entry.get("active", False),
            "binding_mode": entry.get("binding_mode"),
            "actions": entry.get("actions", []),
        }
        for entry in entries
        if isinstance(entry, dict)
    ]
    active_ids = sorted(s["id"] for s in summaries if s["active"])
    return {
        "catalog_entries": summaries,
        "active_entries": active_ids,
        "selected_entry": active_ids[0] if len(active_ids) == 1 else None,
    }
