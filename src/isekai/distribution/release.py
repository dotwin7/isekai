from __future__ import annotations

import hashlib
import json
import re
import stat
import tomllib
from pathlib import Path
from typing import Any, Iterable

from ..support.files import (
    UnsafeControlFile,
    read_control_file,
    read_control_file_snapshot,
)
from ..support.jsonio import write_json_atomic


DISTRIBUTION_SCHEMA_VERSION = "1.0.0"
PROTOCOL_VERSION = "1.0.0"
LOCK_SCHEMA_VERSION = "1.0.0"
MANIFEST_PATH = Path("distribution/release.json")
LOCK_NAME = "isekai.lock.json"
MANAGED_ROOT = ".isekai"
PLUGIN_ID = "isekai-agent-plugin"
RUNTIMES = {"kiro", "claude", "codex"}


class DistributionError(ValueError):
    """Raised when an ISEKAI release cannot be safely installed or updated."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DistributionError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DistributionError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DistributionError(f"expected a JSON object: {path}")
    return value


def _read_control_bytes(path: Path, *, root: Path, label: str) -> bytes:
    try:
        return read_control_file(path, root=root, label=label)
    except FileNotFoundError as exc:
        raise DistributionError(f"missing {label}: {path}") from exc
    except UnsafeControlFile as exc:
        raise DistributionError(str(exc)) from exc
    except OSError as exc:
        raise DistributionError(f"cannot safely read {label}: {path}: {exc}") from exc


def _read_control_text(path: Path, *, root: Path, label: str) -> str:
    try:
        return _read_control_bytes(path, root=root, label=label).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DistributionError(f"{label} is not valid UTF-8: {path}") from exc


def _read_control_json(path: Path, *, root: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_control_text(path, root=root, label=label))
    except json.JSONDecodeError as exc:
        raise DistributionError(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DistributionError(f"{label} must be a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    write_json_atomic(path, value)


def _safe_relative_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DistributionError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise DistributionError(f"{label} must stay inside the release: {value}")
    return path


def _component_root(root: Path, value: object, *, label: str) -> Path:
    relative = _safe_relative_path(value, label=label)
    release_root = root.resolve()
    lexical_candidate = release_root
    for part in relative.parts:
        lexical_candidate /= part
        if lexical_candidate.is_symlink():
            raise DistributionError(
                f"{label} contains a symlink: {lexical_candidate}"
            )
    candidate = lexical_candidate.resolve()
    try:
        candidate.relative_to(release_root)
    except ValueError as exc:  # pragma: no cover - defensive after path validation
        raise DistributionError(f"{label} escapes the release root: {relative}") from exc
    if not candidate.is_dir():
        raise DistributionError(f"{label} directory does not exist: {relative}")
    return candidate


def _is_transient(candidate: Path) -> bool:
    """Report build output and short-lived lock files that are not release content."""
    if "__pycache__" in candidate.parts:
        return True
    return any(
        part.startswith(".isekai-") and ".lock" in part for part in candidate.parts
    )


def tree_digest(
    path: str | Path,
    *,
    include_transients: bool = False,
) -> str:
    """Return a deterministic SHA-256 for a directory without following symlinks.

    Release checkouts may contain interpreter caches created while the bootstrap
    imports Core, so source-release digests omit those transient files. Installed
    trees use ``include_transients=True``: no unhashed file may live beside code
    that the project launcher can execute.
    """
    requested_root = Path(path).expanduser()
    if requested_root.is_symlink():
        raise DistributionError(
            f"release component root cannot be a symlink: {requested_root}"
        )
    root = requested_root.resolve()
    if not root.is_dir():
        raise DistributionError(f"digest target is not a directory: {root}")
    digest = hashlib.sha256()
    files: list[Path] = []
    directories: list[Path] = []
    for candidate in root.rglob("*"):
        try:
            metadata = candidate.lstat()
        except OSError as exc:
            raise DistributionError(
                f"release component changed during digest: {candidate}: {exc}"
            ) from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise DistributionError(f"release components cannot contain symlinks: {candidate}")
        if not include_transients and _is_transient(candidate):
            continue
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise DistributionError(
                    f"release components cannot contain hard-linked files: {candidate}"
                )
            files.append(candidate)
        elif stat.S_ISDIR(metadata.st_mode):
            directories.append(candidate)
        else:
            raise DistributionError(
                f"release components cannot contain special files: {candidate}"
            )
    for candidate in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = candidate.relative_to(root).as_posix().encode("utf-8")
        try:
            content, metadata = read_control_file_snapshot(
                candidate,
                root=root,
                label="release component file",
            )
        except (OSError, UnsafeControlFile) as exc:
            raise DistributionError(
                f"release component changed during digest: {candidate}: {exc}"
            ) from exc
        digest.update(relative)
        digest.update(b"\0")
        # Git's portable file-mode contract records only executability.  Bind
        # that bit without changing historical digests for non-executable files.
        if metadata.st_mode & 0o111:
            digest.update(b"executable\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    # A directory holding no files anywhere beneath it leaves no trace in the
    # loop above, so removing it would be invisible to doctor. Record those.
    populated = {parent for file in files for parent in file.parents}
    empty = [candidate for candidate in directories if candidate not in populated]
    for candidate in sorted(empty, key=lambda item: item.relative_to(root).as_posix()):
        digest.update(b"dir\0")
        digest.update(candidate.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _verified_tree_digest(
    path: str | Path,
    expected: object,
    *,
    label: str,
    include_transients: bool = False,
) -> str:
    actual = tree_digest(path, include_transients=include_transients)
    if actual != expected:
        raise DistributionError(f"{label} changed after release verification")
    return actual


def _project_version(root: Path) -> str:
    try:
        project = tomllib.loads(
            _read_control_text(
                root / "pyproject.toml",
                root=root,
                label="pyproject.toml",
            )
        )
        version = project["project"]["version"]
    except (KeyError, tomllib.TOMLDecodeError) as exc:
        raise DistributionError("pyproject.toml does not declare project.version") from exc
    if not isinstance(version, str) or not version.strip():
        raise DistributionError("pyproject.toml project.version must be a non-empty string")
    return version


def build_distribution_manifest(root: str | Path) -> dict[str, Any]:
    release_root = Path(root).resolve()
    version = _project_version(release_root)
    foundation = _read_control_json(
        release_root / "foundation/release.json",
        root=release_root,
        label="Foundation release manifest",
    )
    plugin = _read_control_json(
        release_root / "plugin/isekai/manifest.json",
        root=release_root,
        label="plugin manifest",
    )
    release_version = plugin.get("version")
    if not isinstance(release_version, str) or not release_version:
        raise DistributionError("plugin manifest must declare the distribution version")
    plugin_core = plugin.get("core")
    if (
        not isinstance(plugin_core, dict)
        or plugin_core.get("protocol_version") != PROTOCOL_VERSION
    ):
        raise DistributionError("plugin manifest protocol_version does not match Core")
    init_content = _read_control_text(
        release_root / "src/isekai/__init__.py",
        root=release_root,
        label="Core version module",
    )
    if f'__version__ = "{version}"' not in init_content:
        raise DistributionError("Core package version does not match pyproject.toml")
    for runtime in ("codex", "claude"):
        runtime_manifest = _read_control_json(
            release_root
            / f"plugin/isekai/runtimes/{runtime}/.{runtime}-plugin/plugin.json",
            root=release_root,
            label=f"{runtime} runtime manifest",
        )
        if runtime_manifest.get("version") != release_version:
            raise DistributionError(f"{runtime} Adapter version does not match plugin release")

    adapter_paths = {
        "kiro": "plugin/isekai/runtimes/kiro/skills/isekai",
        "claude": "plugin/isekai/runtimes/claude",
        "codex": "plugin/isekai/runtimes/codex",
    }
    adapters = []
    for runtime in sorted(adapter_paths):
        path = adapter_paths[runtime]
        component = _component_root(
            release_root, path, label=f"adapter:{runtime}.path"
        )
        adapters.append(
            {
                "id": runtime,
                "version": release_version,
                "path": path,
                "digest": tree_digest(component),
            }
        )
    return {
        "schema_version": DISTRIBUTION_SCHEMA_VERSION,
        "id": "isekai",
        "version": release_version,
        "protocol_version": PROTOCOL_VERSION,
        "compatibility": {
            "project_schema_versions": ["1.0.0"],
            "foundation_schema_versions": ["1.0.0"],
        },
        "core": {
            "package": "isekai-agent-plugin",
            "version": version,
            "path": "src/isekai",
            "digest": tree_digest(
                _component_root(release_root, "src/isekai", label="core.path")
            ),
        },
        "bootstrap": {
            "id": "bootstrap",
            "version": release_version,
            "path": "scripts",
            "digest": tree_digest(
                _component_root(release_root, "scripts", label="bootstrap.path")
            ),
        },
        "foundation": {
            "id": foundation.get("id"),
            "version": foundation.get("version"),
            "path": "foundation",
            "digest": tree_digest(
                _component_root(release_root, "foundation", label="foundation.path")
            ),
        },
        "adapters": adapters,
    }


def write_distribution_manifest(
    root: str | Path,
    output: str | Path = MANIFEST_PATH,
) -> Path:
    release_root = Path(root).resolve()
    destination = Path(output)
    if not destination.is_absolute():
        destination = release_root / destination
    _write_json_atomic(destination, build_distribution_manifest(release_root))
    return destination


def load_distribution_manifest(root: str | Path) -> dict[str, Any]:
    release_root = Path(root).resolve()
    manifest = _read_control_json(
        release_root / MANIFEST_PATH,
        root=release_root,
        label="distribution manifest",
    )
    required = {
        "schema_version",
        "id",
        "version",
        "protocol_version",
        "core",
        "bootstrap",
        "foundation",
        "adapters",
        "compatibility",
    }
    missing = sorted(required - manifest.keys())
    if missing:
        raise DistributionError(
            "distribution manifest missing fields: " + ", ".join(missing)
        )
    if manifest["schema_version"] != DISTRIBUTION_SCHEMA_VERSION:
        raise DistributionError("unsupported distribution manifest schema_version")
    if manifest["id"] != "isekai":
        raise DistributionError("distribution manifest id must be isekai")
    if manifest["protocol_version"] != PROTOCOL_VERSION:
        raise DistributionError("unsupported ISEKAI protocol_version")
    if not isinstance(manifest["version"], str) or not manifest["version"].strip():
        raise DistributionError("distribution version must be a non-empty string")
    compatibility = manifest["compatibility"]
    if not isinstance(compatibility, dict):
        raise DistributionError("distribution compatibility must be an object")
    project_schemas = compatibility.get("project_schema_versions")
    foundation_schemas = compatibility.get("foundation_schema_versions")
    if (
        not isinstance(project_schemas, list)
        or any(not isinstance(item, str) for item in project_schemas)
        or "1.0.0" not in project_schemas
    ):
        raise DistributionError("distribution does not support project schema 1.0.0")
    if (
        not isinstance(foundation_schemas, list)
        or any(not isinstance(item, str) for item in foundation_schemas)
        or "1.0.0" not in foundation_schemas
    ):
        raise DistributionError("distribution does not support Foundation schema 1.0.0")
    if not isinstance(manifest["adapters"], list):
        raise DistributionError("distribution adapters must be a list")
    adapter_ids: list[str] = []
    for item in manifest["adapters"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise DistributionError("distribution adapter entries require string IDs")
        adapter_ids.append(item["id"])
    if len(adapter_ids) != len(set(adapter_ids)) or set(adapter_ids) != RUNTIMES:
        raise DistributionError("distribution must contain kiro, claude, and codex adapters")
    components = [
        ("core", manifest["core"]),
        ("bootstrap", manifest["bootstrap"]),
        ("foundation", manifest["foundation"]),
        *((f"adapter:{entry['id']}", entry) for entry in manifest["adapters"]),
    ]
    digest_pattern = re.compile(r"sha256:[0-9a-f]{64}")
    for label, entry in components:
        if not isinstance(entry, dict):
            raise DistributionError(f"distribution {label} must be an object")
        for field in ("version", "path"):
            if not isinstance(entry.get(field), str) or not entry[field].strip():
                raise DistributionError(
                    f"distribution {label}.{field} must be a non-empty string"
                )
        _safe_relative_path(entry["path"], label=f"{label}.path")
        if (
            not isinstance(entry.get("digest"), str)
            or digest_pattern.fullmatch(entry["digest"]) is None
        ):
            raise DistributionError(
                f"distribution {label}.digest must be a SHA-256 digest"
            )
    return manifest


def verify_distribution(root: str | Path) -> dict[str, Any]:
    release_root = Path(root).resolve()
    manifest = load_distribution_manifest(release_root)
    issues: list[str] = []
    components: list[dict[str, str]] = []
    try:
        canonical = build_distribution_manifest(release_root)
    except (DistributionError, OSError, KeyError, TypeError) as exc:
        canonical = None
        issues.append(f"cannot derive canonical distribution metadata: {exc}")

    if canonical is not None:
        for field in ("version", "protocol_version", "compatibility"):
            if manifest.get(field) != canonical[field]:
                issues.append(
                    f"distribution {field} does not match source manifests"
                )

        component_pairs = [
            ("core", manifest.get("core"), canonical["core"]),
            ("bootstrap", manifest.get("bootstrap"), canonical["bootstrap"]),
            ("foundation", manifest.get("foundation"), canonical["foundation"]),
        ]
        actual_adapters = {
            item.get("id"): item
            for item in manifest.get("adapters", [])
            if isinstance(item, dict)
        }
        canonical_adapters = {
            item["id"]: item for item in canonical["adapters"]
        }
        component_pairs.extend(
            (
                f"adapter:{runtime}",
                actual_adapters.get(runtime),
                canonical_adapters[runtime],
            )
            for runtime in sorted(RUNTIMES)
        )
        for label, actual, expected in component_pairs:
            if not isinstance(actual, dict):
                continue
            actual_metadata = {
                key: value for key, value in actual.items() if key != "digest"
            }
            expected_metadata = {
                key: value for key, value in expected.items() if key != "digest"
            }
            if actual_metadata != expected_metadata:
                issues.append(f"{label} metadata does not match source manifests")

    entries = [
        manifest["core"],
        manifest["bootstrap"],
        manifest["foundation"],
        *manifest["adapters"],
    ]
    for entry in entries:
        if not isinstance(entry, dict):
            issues.append("distribution component must be an object")
            continue
        label = str(entry.get("id") or entry.get("package") or "component")
        try:
            component = _component_root(
                release_root, entry.get("path"), label=f"{label}.path"
            )
            actual = tree_digest(component)
        except DistributionError as exc:
            issues.append(str(exc))
            continue
        expected = entry.get("digest")
        if actual != expected:
            issues.append(f"{label} digest mismatch: expected {expected}, got {actual}")
        components.append({"id": label, "digest": actual})
    for label, entry in (
        ("core", manifest["core"]),
        ("bootstrap", manifest["bootstrap"]),
        ("foundation", manifest["foundation"]),
    ):
        if not isinstance(entry, dict):
            continue
        if not isinstance(entry.get("version"), str) or not entry["version"]:
            issues.append(f"{label} version must be a non-empty string")
    for adapter in manifest["adapters"]:
        if not isinstance(adapter, dict):
            continue
        if not isinstance(adapter.get("version"), str) or not adapter["version"]:
            issues.append(f"{adapter.get('id')} adapter version must be a non-empty string")
    return {
        "valid": not issues,
        "release": manifest["version"],
        "protocol_version": manifest["protocol_version"],
        "components": components,
        "issues": issues,
    }


def _verify_or_raise(root: Path) -> dict[str, Any]:
    result = verify_distribution(root)
    if not result["valid"]:
        raise DistributionError("release verification failed: " + "; ".join(result["issues"]))
    return load_distribution_manifest(root)


def _normalize_runtimes(runtimes: Iterable[str]) -> list[str]:
    values = list(runtimes)
    if not values or "all" in values:
        return sorted(RUNTIMES)
    unknown = sorted(set(values) - RUNTIMES)
    if unknown:
        raise DistributionError("unknown runtime: " + ", ".join(unknown))
    return sorted(set(values))
