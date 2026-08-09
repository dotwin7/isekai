from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..foundation import FoundationError
from ..support.files import UnsafeControlFile, read_control_file


FEATURE_SCHEMA_VERSION = "1.0.0"
FEATURE_CATALOG_URI = "isekai://runtime/features"
FEATURE_ROOT = Path(__file__).resolve().parents[3] / "features"
FEATURE_SOURCE_CATALOG = "catalog.json"
_ID = re.compile(r"[a-z][a-z0-9-]{0,63}")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?")
_STATUSES = {"active", "preview", "deprecated"}
_DELIVERY_MODES = {"core-bundled", "feature-package"}
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


def _string_list(value: dict[str, Any], field: str, *, feature_id: str) -> list[str]:
    items = value.get(field)
    if (
        not isinstance(items, list)
        or any(not isinstance(item, str) or not item.strip() for item in items)
        or len(set(items)) != len(items)
    ):
        raise FoundationError(
            f"ISEKAI feature {feature_id} {field} must be a unique string list"
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


def _load_feature(
    path: Path,
    *,
    root: Path,
    expected_id: str,
    expected_version: str,
) -> dict[str, Any]:
    value = _load_json(
        path,
        root=root,
        label=f"ISEKAI feature manifest {expected_id}@{expected_version}",
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
        "actions",
        "resources",
        "authority",
    }
    missing = sorted(required - value.keys())
    feature_id = str(value.get("id", expected_id))
    if missing:
        raise FoundationError(
            f"ISEKAI feature {feature_id} missing fields: {', '.join(missing)}"
        )
    if _ID.fullmatch(feature_id) is None or feature_id != expected_id:
        raise FoundationError(f"ISEKAI feature has an invalid id: {feature_id}")
    if value["kind"] != "isekai-feature":
        raise FoundationError(f"ISEKAI feature {feature_id} has an invalid kind")
    if value["schema_version"] != FEATURE_SCHEMA_VERSION:
        raise FoundationError(
            f"ISEKAI feature {feature_id} has an unsupported schema_version"
        )
    version = value.get("version")
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        raise FoundationError(f"ISEKAI feature {feature_id} has an invalid version")
    if version != expected_version:
        raise FoundationError(
            f"ISEKAI feature {feature_id} version does not match source catalog"
        )
    if value["status"] not in _STATUSES:
        raise FoundationError(f"ISEKAI feature {feature_id} has an invalid status")
    for field in ("title", "description"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise FoundationError(f"ISEKAI feature {feature_id} requires {field}")
    if value["control_protocol"] != "1.1.0":
        raise FoundationError(
            f"ISEKAI feature {feature_id} requires an incompatible control protocol"
        )
    if value["delivery"] not in _DELIVERY_MODES:
        raise FoundationError(f"ISEKAI feature {feature_id} has invalid delivery")
    if value["authority"] != _AUTHORITY:
        raise FoundationError(f"ISEKAI feature {feature_id} has invalid authority")
    normalized = dict(value)
    normalized["actions"] = _string_list(value, "actions", feature_id=feature_id)
    normalized["resources"] = _string_list(
        value,
        "resources",
        feature_id=feature_id,
    )
    normalized["package_path"] = path.parent.relative_to(root).as_posix()
    normalized["active"] = value["status"] == "active"
    normalized["feature_digest"] = _digest(normalized, "feature_digest")
    return normalized


def load_feature_catalog() -> dict[str, Any]:
    root = FEATURE_ROOT
    if not root.is_dir():
        raise FoundationError("ISEKAI Feature Catalog directory is missing")
    source = _load_json(
        root / FEATURE_SOURCE_CATALOG,
        root=root,
        label="ISEKAI Feature source catalog",
    )
    if source.get("kind") != "isekai-feature-source-catalog":
        raise FoundationError("ISEKAI Feature source catalog has an invalid kind")
    if source.get("schema_version") != FEATURE_SCHEMA_VERSION:
        raise FoundationError(
            "ISEKAI Feature source catalog has an unsupported schema_version"
        )
    if source.get("control_protocol") != "1.1.0":
        raise FoundationError(
            "ISEKAI Feature source catalog requires an incompatible control protocol"
        )
    entries = source.get("features")
    if not isinstance(entries, list) or not entries:
        raise FoundationError("ISEKAI Feature source catalog cannot be empty")
    features: list[dict[str, Any]] = []
    manifest_paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise FoundationError("ISEKAI Feature source catalog entries must be objects")
        feature_id = entry.get("id")
        version = entry.get("version")
        manifest = entry.get("manifest")
        if not isinstance(feature_id, str) or _ID.fullmatch(feature_id) is None:
            raise FoundationError("ISEKAI Feature source catalog has an invalid feature ID")
        if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
            raise FoundationError(
                f"ISEKAI Feature source catalog has an invalid version for {feature_id}"
            )
        expected = Path(feature_id) / version / "feature.json"
        if not isinstance(manifest, str) or Path(manifest) != expected:
            raise FoundationError(
                f"ISEKAI Feature source catalog has an invalid manifest path for {feature_id}"
            )
        manifest_paths.append(manifest)
        features.append(
            _load_feature(
                root / expected,
                root=root,
                expected_id=feature_id,
                expected_version=version,
            )
        )
    identifiers = [str(feature["id"]) for feature in features]
    if len(set(identifiers)) != len(identifiers):
        raise FoundationError("ISEKAI feature catalog has duplicate IDs")
    if len(set(manifest_paths)) != len(manifest_paths):
        raise FoundationError("ISEKAI Feature source catalog has duplicate manifests")
    catalog = {
        "type": "isekai-feature-catalog",
        "schema_version": FEATURE_SCHEMA_VERSION,
        "control_protocol": "1.1.0",
        "features": features,
    }
    catalog["catalog_digest"] = _digest(catalog, "catalog_digest")
    return catalog


def feature_resources(catalog: dict[str, Any]) -> list[dict[str, str]]:
    resources = [
        {
            "uri": FEATURE_CATALOG_URI,
            "name": "isekai-feature-catalog",
            "title": "ISEKAI Feature catalog",
            "description": "Versioned features attached to this ISEKAI Runtime.",
            "mimeType": "application/json",
        }
    ]
    features = catalog.get("features", [])
    if not isinstance(features, list):
        return resources
    for feature in features:
        if not isinstance(feature, dict):
            continue
        resources.append(
            {
                "uri": f"{FEATURE_CATALOG_URI}/{feature['id']}",
                "name": str(feature["id"]),
                "title": str(feature["title"]),
                "description": str(feature["description"]),
                "mimeType": "application/json",
            }
        )
    return resources


def read_feature_resource(catalog: dict[str, Any], uri: str) -> dict[str, str]:
    if uri == FEATURE_CATALOG_URI:
        value: object = catalog
    else:
        prefix = FEATURE_CATALOG_URI + "/"
        feature_id = uri[len(prefix) :] if uri.startswith(prefix) else ""
        features = catalog.get("features", [])
        value = next(
            (
                feature
                for feature in features
                if isinstance(feature, dict) and feature.get("id") == feature_id
            ),
            None,
        ) if isinstance(features, list) else None
        if value is None:
            raise FoundationError(f"unknown ISEKAI feature resource: {uri}")
    return {
        "uri": uri,
        "mimeType": "application/json",
        "text": json.dumps(value, ensure_ascii=False, indent=2),
    }
