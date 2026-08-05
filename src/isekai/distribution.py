from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import uuid
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .foundation import FoundationError, load_foundation


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


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _safe_relative_path(value: object, *, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise DistributionError(f"{label} must be a non-empty relative path")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise DistributionError(f"{label} must stay inside the release: {value}")
    return path


def _component_root(root: Path, value: object, *, label: str) -> Path:
    relative = _safe_relative_path(value, label=label)
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:  # pragma: no cover - defensive after path validation
        raise DistributionError(f"{label} escapes the release root: {relative}") from exc
    if not candidate.is_dir():
        raise DistributionError(f"{label} directory does not exist: {relative}")
    return candidate


def tree_digest(path: str | Path) -> str:
    """Return a deterministic SHA-256 for a directory without following symlinks."""
    root = Path(path).resolve()
    if not root.is_dir():
        raise DistributionError(f"digest target is not a directory: {root}")
    digest = hashlib.sha256()
    files: list[Path] = []
    for candidate in root.rglob("*"):
        if candidate.is_symlink():
            raise DistributionError(f"release components cannot contain symlinks: {candidate}")
        if candidate.is_file() and "__pycache__" not in candidate.parts:
            files.append(candidate)
    for candidate in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = candidate.relative_to(root).as_posix().encode("utf-8")
        content = candidate.read_bytes()
        digest.update(relative)
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _project_version(root: Path) -> str:
    try:
        project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        version = project["project"]["version"]
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise DistributionError("pyproject.toml does not declare project.version") from exc
    if not isinstance(version, str) or not version.strip():
        raise DistributionError("pyproject.toml project.version must be a non-empty string")
    return version


def build_distribution_manifest(root: str | Path) -> dict[str, Any]:
    release_root = Path(root).resolve()
    version = _project_version(release_root)
    foundation = _read_json(release_root / "foundation/release.json")
    plugin = _read_json(release_root / "plugin/isekai/manifest.json")
    release_version = plugin.get("version")
    if not isinstance(release_version, str) or not release_version:
        raise DistributionError("plugin manifest must declare the distribution version")
    if plugin.get("core", {}).get("protocol_version") != PROTOCOL_VERSION:
        raise DistributionError("plugin manifest protocol_version does not match Core")
    init_content = (release_root / "src/isekai/__init__.py").read_text(encoding="utf-8")
    if f'__version__ = "{version}"' not in init_content:
        raise DistributionError("Core package version does not match pyproject.toml")
    for runtime in ("codex", "claude"):
        runtime_manifest = _read_json(
            release_root
            / f"plugin/isekai/runtimes/{runtime}/.{runtime}-plugin/plugin.json"
        )
        if runtime_manifest.get("version") != release_version:
            raise DistributionError(f"{runtime} Adapter version does not match plugin release")

    adapter_paths = {
        "kiro": ".kiro/skills/isekai",
        "claude": "plugin/isekai/runtimes/claude",
        "codex": "plugin/isekai/runtimes/codex",
    }
    adapters = []
    for runtime in sorted(adapter_paths):
        path = adapter_paths[runtime]
        adapters.append(
            {
                "id": runtime,
                "version": release_version,
                "path": path,
                "digest": tree_digest(release_root / path),
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
            "digest": tree_digest(release_root / "src/isekai"),
        },
        "bootstrap": {
            "id": "bootstrap",
            "version": release_version,
            "path": "scripts",
            "digest": tree_digest(release_root / "scripts"),
        },
        "foundation": {
            "id": foundation.get("id"),
            "version": foundation.get("version"),
            "path": "foundation",
            "digest": tree_digest(release_root / "foundation"),
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
    manifest = _read_json(release_root / MANIFEST_PATH)
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
    compatibility = manifest["compatibility"]
    if not isinstance(compatibility, dict):
        raise DistributionError("distribution compatibility must be an object")
    if "1.0.0" not in compatibility.get("project_schema_versions", []):
        raise DistributionError("distribution does not support project schema 1.0.0")
    if "1.0.0" not in compatibility.get("foundation_schema_versions", []):
        raise DistributionError("distribution does not support Foundation schema 1.0.0")
    if not isinstance(manifest["adapters"], list):
        raise DistributionError("distribution adapters must be a list")
    adapter_ids = [item.get("id") for item in manifest["adapters"] if isinstance(item, dict)]
    if len(adapter_ids) != len(manifest["adapters"]) or set(adapter_ids) != RUNTIMES:
        raise DistributionError("distribution must contain kiro, claude, and codex adapters")
    return manifest


def verify_distribution(root: str | Path) -> dict[str, Any]:
    release_root = Path(root).resolve()
    manifest = load_distribution_manifest(release_root)
    issues: list[str] = []
    components: list[dict[str, str]] = []
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
        if not isinstance(entry.get("version"), str) or not entry["version"]:
            issues.append(f"{label} version must be a non-empty string")
    for adapter in manifest["adapters"]:
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


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:48] or "project"


def _project_id(project_root: Path) -> str:
    manifest = project_root / "project.json"
    if manifest.is_file():
        value = _read_json(manifest).get("id")
        if isinstance(value, str) and value.strip():
            return value
    return project_root.name


def _replace_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)


def _copy_managed_root(source: Path, destination: Path) -> None:
    def ignore(path: str, names: list[str]) -> set[str]:
        if Path(path).resolve() == source.resolve():
            return {"rollback"} & set(names)
        return set()

    shutil.copytree(source, destination, ignore=ignore)


def _write_launchers(managed: Path) -> None:
    binary = managed / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    python_launcher = binary / "isekai.py"
    python_launcher.write_text(
        "from pathlib import Path\n"
        "import sys\n\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))\n"
        "from isekai.cli import main\n\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    posix_launcher = binary / "isekai"
    posix_launcher.write_text(
        "#!/bin/sh\nexec python3 \"$(dirname \"$0\")/isekai.py\" \"$@\"\n",
        encoding="utf-8",
    )
    posix_launcher.chmod(0o755)
    (binary / "isekai.cmd").write_text(
        "@py -3 \"%~dp0isekai.py\" %*\r\n",
        encoding="utf-8",
    )


def _codex_cachebuster(plugin_root: Path, commit: str) -> str:
    path = plugin_root / ".codex-plugin/plugin.json"
    manifest = _read_json(path)
    base = str(manifest.get("version", "")).split("+", 1)[0]
    if not base:
        raise DistributionError("Codex plugin manifest has no version")
    token = re.sub(r"[^0-9A-Za-z-]", "-", commit[:12]) or "local"
    version = f"{base}+codex.{token}"
    manifest["version"] = version
    _write_json_atomic(path, manifest)
    return version


def _prepare_codex_marketplace(
    managed: Path,
    adapter_source: Path,
    marketplace_name: str,
    commit: str,
) -> tuple[Path, str]:
    root = managed / "marketplaces/codex"
    plugin_root = root / "plugins" / PLUGIN_ID
    _replace_tree(adapter_source, plugin_root)
    installed_version = _codex_cachebuster(plugin_root, commit)
    _write_json_atomic(
        root / ".agents/plugins/marketplace.json",
        {
            "name": marketplace_name,
            "interface": {"displayName": f"ISEKAI ({marketplace_name})"},
            "plugins": [
                {
                    "name": PLUGIN_ID,
                    "source": {
                        "source": "local",
                        "path": f"./plugins/{PLUGIN_ID}",
                    },
                    "policy": {
                        "installation": "AVAILABLE",
                        "authentication": "ON_INSTALL",
                    },
                    "category": "Productivity",
                }
            ],
        },
    )
    return plugin_root, installed_version


def _prepare_claude_marketplace(
    managed: Path,
    adapter_source: Path,
    marketplace_name: str,
    version: str,
) -> Path:
    root = managed / "marketplaces/claude"
    plugin_root = root / "plugins" / PLUGIN_ID
    _replace_tree(adapter_source, plugin_root)
    _write_json_atomic(
        root / ".claude-plugin/marketplace.json",
        {
            "name": marketplace_name,
            "owner": {"name": "ISEKAI"},
            "description": "Project-local ISEKAI AI-DLC plugins",
            "plugins": [
                {
                    "name": PLUGIN_ID,
                    "source": f"./plugins/{PLUGIN_ID}",
                    "description": "ISEKAI AI-DLC workflow integration for Claude Code",
                    "version": version,
                }
            ],
        },
    )
    return plugin_root


def load_install_lock(project: str | Path) -> dict[str, Any] | None:
    root = Path(project).expanduser().resolve()
    path = root if root.name == LOCK_NAME else root / LOCK_NAME
    if not path.is_file():
        return None
    lock = _read_json(path)
    if lock.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise DistributionError("unsupported isekai.lock.json schema_version")
    return lock


def _installed_path(project_root: Path, value: object, *, label: str) -> Path:
    relative = _safe_relative_path(value, label=label)
    target = (project_root / relative).resolve()
    try:
        target.relative_to(project_root.resolve())
    except ValueError as exc:  # pragma: no cover - defensive
        raise DistributionError(f"{label} escapes the project root") from exc
    return target


def doctor_install(project: str | Path) -> dict[str, Any]:
    project_root = Path(project).expanduser().resolve()
    lock = load_install_lock(project_root)
    if lock is None:
        return {"ready": False, "project": str(project_root), "issues": [f"missing {LOCK_NAME}"]}
    issues: list[str] = []
    if lock.get("protocol_version") != PROTOCOL_VERSION:
        issues.append("installed protocol_version is not supported by this Core")

    components: list[tuple[str, dict[str, Any]]] = []
    for label in ("core", "foundation"):
        entry = lock.get(label)
        if isinstance(entry, dict):
            components.append((label, entry))
        else:
            issues.append(f"lock is missing {label}")
    adapters = lock.get("adapters")
    if not isinstance(adapters, dict):
        issues.append("lock adapters must be an object")
        adapters = {}
    for runtime, entry in sorted(adapters.items()):
        if isinstance(entry, dict):
            components.append((f"adapter:{runtime}", entry))
        else:
            issues.append(f"adapter lock is invalid: {runtime}")

    for label, entry in components:
        try:
            target = _installed_path(
                project_root, entry.get("path"), label=f"{label}.path"
            )
            actual = tree_digest(target)
        except DistributionError as exc:
            issues.append(str(exc))
            continue
        if actual != entry.get("digest"):
            issues.append(f"{label} digest mismatch")

    foundation_entry = lock.get("foundation")
    if isinstance(foundation_entry, dict):
        try:
            installed_foundation = load_foundation(
                _installed_path(
                    project_root,
                    foundation_entry.get("path"),
                    label="foundation.path",
                )
            )
            if installed_foundation.version != foundation_entry.get("version"):
                issues.append("installed Foundation version does not match lock")
        except (DistributionError, FoundationError) as exc:
            issues.append(str(exc))

    project_manifest = project_root / "project.json"
    if project_manifest.is_file() and isinstance(foundation_entry, dict):
        try:
            project_value = _read_json(project_manifest)
            foundation_path = project_value.get("foundation_path")
            if not isinstance(foundation_path, str):
                raise DistributionError("project foundation_path must be a string")
            selected = load_foundation(project_root / foundation_path)
            if selected.version != foundation_entry.get("version"):
                issues.append("Project Foundation version does not match lock")
            if tree_digest(selected.root) != foundation_entry.get("digest"):
                issues.append("Project Foundation digest does not match lock")
        except (DistributionError, FoundationError) as exc:
            issues.append(str(exc))

    issues = list(dict.fromkeys(issues))
    return {
        "ready": not issues,
        "project": str(project_root),
        "release": lock.get("release"),
        "protocol_version": lock.get("protocol_version"),
        "runtimes": sorted(adapters),
        "issues": issues,
    }


def _current_foundation_matches(
    project_root: Path,
    version: str,
    digest: str,
) -> bool:
    manifest_path = project_root / "project.json"
    if not manifest_path.is_file():
        return True
    project = _read_json(manifest_path)
    foundation_path = project.get("foundation_path")
    if not isinstance(foundation_path, str):
        return False
    try:
        foundation = load_foundation(project_root / foundation_path)
        return foundation.version == version and tree_digest(foundation.root) == digest
    except (DistributionError, FoundationError):
        return False


def _adopt_foundation(project_root: Path, relative: str) -> bytes | None:
    path = project_root / "project.json"
    if not path.is_file():
        return None
    before = path.read_bytes()
    project = _read_json(path)
    project["foundation_path"] = relative
    _write_json_atomic(path, project)
    return before


def _registration_commands(
    project_root: Path,
    marketplace_name: str,
    runtimes: Iterable[str],
    *,
    update: bool,
) -> list[list[str]]:
    commands: list[list[str]] = []
    selected = set(runtimes)
    if "codex" in selected:
        marketplace = project_root / MANAGED_ROOT / "marketplaces/codex"
        if not update:
            commands.append(["codex", "plugin", "marketplace", "add", str(marketplace)])
        commands.append(["codex", "plugin", "add", f"{PLUGIN_ID}@{marketplace_name}"])
    if "claude" in selected:
        marketplace = project_root / MANAGED_ROOT / "marketplaces/claude"
        if update:
            commands.append(
                [
                    "claude",
                    "plugin",
                    "update",
                    f"{PLUGIN_ID}@{marketplace_name}",
                    "--scope",
                    "project",
                ]
            )
        else:
            commands.append(
                [
                    "claude",
                    "plugin",
                    "marketplace",
                    "add",
                    str(marketplace),
                    "--scope",
                    "project",
                ]
            )
            commands.append(
                [
                    "claude",
                    "plugin",
                    "install",
                    f"{PLUGIN_ID}@{marketplace_name}",
                    "--scope",
                    "project",
                ]
            )
    return commands


def _run_registration(commands: list[list[str]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for command in commands:
        if shutil.which(command[0]) is None:
            raise DistributionError(f"host CLI is not installed: {command[0]}")
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        result = {
            "command": command,
            "exit_code": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }
        results.append(result)
        if completed.returncode != 0:
            raise DistributionError(
                f"host registration failed ({' '.join(command)}): "
                + (completed.stderr.strip() or completed.stdout.strip())
            )
    return results


def install_from_checkout(
    checkout: str | Path,
    project: str | Path,
    *,
    source: str,
    ref: str,
    commit: str,
    runtimes: Iterable[str] = ("all",),
    update: bool = False,
    include_foundation: bool = False,
    adopt_foundation: bool = False,
    register: bool = False,
) -> dict[str, Any]:
    release_root = Path(checkout).resolve()
    project_root = Path(project).expanduser().resolve()
    if not project_root.is_dir():
        raise DistributionError(f"project root does not exist: {project_root}")
    manifest = _verify_or_raise(release_root)
    selected = _normalize_runtimes(runtimes)
    current_lock = load_install_lock(project_root)
    if update and current_lock is None:
        raise DistributionError("cannot update before ISEKAI is installed")
    if current_lock is None and (project_root / MANAGED_ROOT).exists():
        raise DistributionError(
            f"refusing to replace unmanaged {MANAGED_ROOT}; move it aside or adopt it explicitly"
        )
    unmanaged_kiro = project_root / ".kiro/skills/isekai"
    if current_lock is None and "kiro" in selected and unmanaged_kiro.exists():
        raise DistributionError(
            "refusing to replace an unmanaged .kiro/skills/isekai directory"
        )
    if current_lock is not None:
        health = doctor_install(project_root)
        if not health["ready"]:
            raise DistributionError(
                "installed files were modified or are incomplete: " + "; ".join(health["issues"])
            )

    adapter_manifest = {item["id"]: item for item in manifest["adapters"]}
    marketplace_name = (
        str(current_lock.get("marketplace"))
        if current_lock and current_lock.get("marketplace")
        else "isekai-" + _slug(_project_id(project_root))
    )
    current_adapters = dict(current_lock.get("adapters", {})) if current_lock else {}
    installed_runtimes = sorted(set(current_adapters) | set(selected))
    if (
        current_lock
        and current_lock.get("release") == manifest["version"]
        and current_lock.get("source", {}).get("commit") == commit
        and set(selected) <= set(current_adapters)
        and not include_foundation
        and not adopt_foundation
    ):
        commands = _registration_commands(
            project_root, marketplace_name, selected, update=True
        )
        registration = _run_registration(commands) if register else []
        return {
            "installed": False,
            "updated": False,
            "unchanged": True,
            "project": str(project_root),
            "release": manifest["version"],
            "commit": commit,
            "runtimes": installed_runtimes,
            "foundation": current_lock["foundation"],
            "lock": str(project_root / LOCK_NAME),
            "registration_commands": commands,
            "registration": registration,
            "new_conversation_required": False,
        }

    stage_root = Path(tempfile.mkdtemp(prefix=".isekai-stage-", dir=project_root))
    staged = stage_root / MANAGED_ROOT
    managed = project_root / MANAGED_ROOT
    backup = project_root / f".{MANAGED_ROOT}-backup-{uuid.uuid4().hex}"
    kiro_target = project_root / ".kiro/skills/isekai"
    kiro_backup = project_root / f".isekai-kiro-backup-{uuid.uuid4().hex}"
    project_before: bytes | None = None
    lock_before = (project_root / LOCK_NAME).read_bytes() if current_lock else None
    try:
        if managed.is_dir():
            _copy_managed_root(managed, staged)
        else:
            staged.mkdir(parents=True)

        core_source = _component_root(
            release_root, manifest["core"]["path"], label="core.path"
        )
        _replace_tree(core_source, staged / "runtime/isekai")
        _write_launchers(staged)

        if current_lock and not include_foundation:
            foundation_entry = dict(current_lock["foundation"])
        else:
            foundation_source = _component_root(
                release_root,
                manifest["foundation"]["path"],
                label="foundation.path",
            )
            foundation_relative = (
                f"{MANAGED_ROOT}/foundations/{manifest['foundation']['version']}"
            )
            _replace_tree(
                foundation_source,
                staged / "foundations" / str(manifest["foundation"]["version"]),
            )
            foundation_entry = {
                "id": manifest["foundation"]["id"],
                "version": manifest["foundation"]["version"],
                "path": foundation_relative,
                "digest": manifest["foundation"]["digest"],
                "source_release": manifest["version"],
            }

        project_matches = _current_foundation_matches(
            project_root,
            str(foundation_entry["version"]),
            str(foundation_entry["digest"]),
        )
        if not project_matches and not adopt_foundation:
            raise DistributionError(
                "Project Foundation differs from the selected release; rerun with "
                "--adopt-foundation after reviewing the contract change"
            )

        adapter_entries = dict(current_adapters)
        for runtime in selected:
            source_entry = adapter_manifest[runtime]
            adapter_source = _component_root(
                release_root,
                source_entry["path"],
                label=f"adapter:{runtime}.path",
            )
            installed_version = str(source_entry["version"])
            if runtime == "kiro":
                adapter_entries[runtime] = {
                    "version": installed_version,
                    "path": ".kiro/skills/isekai",
                    "source_digest": source_entry["digest"],
                    "digest": source_entry["digest"],
                }
            elif runtime == "codex":
                plugin_root, installed_version = _prepare_codex_marketplace(
                    staged, adapter_source, marketplace_name, commit
                )
                adapter_entries[runtime] = {
                    "version": str(source_entry["version"]),
                    "installed_version": installed_version,
                    "path": f"{MANAGED_ROOT}/marketplaces/codex/plugins/{PLUGIN_ID}",
                    "source_digest": source_entry["digest"],
                    "digest": tree_digest(plugin_root),
                }
            else:
                plugin_root = _prepare_claude_marketplace(
                    staged,
                    adapter_source,
                    marketplace_name,
                    installed_version,
                )
                adapter_entries[runtime] = {
                    "version": installed_version,
                    "path": f"{MANAGED_ROOT}/marketplaces/claude/plugins/{PLUGIN_ID}",
                    "source_digest": source_entry["digest"],
                    "digest": tree_digest(plugin_root),
                }

        if current_lock:
            rollback = staged / "rollback"
            _copy_managed_root(managed, rollback / "install")
            (rollback / LOCK_NAME).write_bytes(lock_before or b"")
            if kiro_target.is_dir() and "kiro" in current_adapters:
                shutil.copytree(kiro_target, rollback / "kiro")
            project_manifest = project_root / "project.json"
            if project_manifest.is_file():
                (rollback / "project.json").write_bytes(project_manifest.read_bytes())

        lock = {
            "schema_version": LOCK_SCHEMA_VERSION,
            "release": manifest["version"],
            "protocol_version": manifest["protocol_version"],
            "source": {"git": source, "ref": ref, "commit": commit},
            "marketplace": marketplace_name,
            "core": {
                "version": manifest["core"]["version"],
                "path": f"{MANAGED_ROOT}/runtime/isekai",
                "source_digest": manifest["core"]["digest"],
                "digest": tree_digest(staged / "runtime/isekai"),
            },
            "foundation": foundation_entry,
            "adapters": dict(sorted(adapter_entries.items())),
        }

        if managed.exists():
            managed.rename(backup)
        staged.rename(managed)

        if "kiro" in selected:
            source_skill = _component_root(
                release_root,
                adapter_manifest["kiro"]["path"],
                label="adapter:kiro.path",
            )
            if kiro_target.exists():
                kiro_target.rename(kiro_backup)
            kiro_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_skill, kiro_target)

        if adopt_foundation:
            project_before = _adopt_foundation(
                project_root, str(foundation_entry["path"])
            )
        _write_json_atomic(project_root / LOCK_NAME, lock)
    except Exception:
        if managed.exists() and backup.exists():
            shutil.rmtree(managed)
            backup.rename(managed)
        elif managed.exists() and not current_lock:
            shutil.rmtree(managed)
        if kiro_backup.exists():
            if kiro_target.exists():
                shutil.rmtree(kiro_target)
            kiro_backup.rename(kiro_target)
        elif "kiro" in selected and kiro_target.exists() and "kiro" not in current_adapters:
            shutil.rmtree(kiro_target)
        if project_before is not None:
            (project_root / "project.json").write_bytes(project_before)
        if lock_before is not None:
            (project_root / LOCK_NAME).write_bytes(lock_before)
        else:
            (project_root / LOCK_NAME).unlink(missing_ok=True)
        raise
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)
        if backup.exists():
            shutil.rmtree(backup)
        if kiro_backup.exists():
            shutil.rmtree(kiro_backup)

    commands = _registration_commands(
        project_root, marketplace_name, selected, update=current_lock is not None
    )
    registration = _run_registration(commands) if register else []
    health = doctor_install(project_root)
    if not health["ready"]:
        raise DistributionError("post-install verification failed: " + "; ".join(health["issues"]))
    result = {
        "installed": True,
        "updated": current_lock is not None,
        "project": str(project_root),
        "release": manifest["version"],
        "commit": commit,
        "runtimes": installed_runtimes,
        "foundation": foundation_entry,
        "lock": str(project_root / LOCK_NAME),
        "registration_commands": commands,
        "registration": registration,
        "new_conversation_required": bool({"codex", "claude"} & set(selected)),
    }
    if not (project_root / "project.json").is_file():
        result["next_action"] = (
            f"{MANAGED_ROOT}/bin/isekai init --path . --foundation-path "
            f"{foundation_entry['path']}"
        )
    return result


def _git(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *command],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise DistributionError(
            f"git {' '.join(command)} failed: "
            + (completed.stderr.strip() or completed.stdout.strip())
        )
    return completed.stdout.strip()


def install_from_git(
    source: str,
    ref: str,
    project: str | Path,
    *,
    runtimes: Iterable[str] = ("all",),
    update: bool = False,
    include_foundation: bool = False,
    adopt_foundation: bool = False,
    register: bool = False,
) -> dict[str, Any]:
    if not isinstance(source, str) or not source.strip() or source.startswith("-"):
        raise DistributionError("Git source must be a non-empty path or URL")
    if not isinstance(ref, str) or not ref.strip() or ref.startswith("-"):
        raise DistributionError("Git ref must be a non-empty tag, branch, or commit")
    with tempfile.TemporaryDirectory(prefix="isekai-release-") as temporary:
        checkout = Path(temporary) / "checkout"
        _git(["clone", "--quiet", "--no-checkout", source, str(checkout)])
        _git(["checkout", "--quiet", "--detach", ref], cwd=checkout)
        commit = _git(["rev-parse", "HEAD^{commit}"], cwd=checkout)
        current = load_install_lock(project)
        if current:
            locked_source = current.get("source", {})
            if (
                locked_source.get("git") == source
                and locked_source.get("ref") == ref
                and locked_source.get("commit") not in {None, commit}
            ):
                raise DistributionError(
                    "Git ref moved to a different commit; use a new immutable tag"
                )
        return install_from_checkout(
            checkout,
            project,
            source=source,
            ref=ref,
            commit=commit,
            runtimes=runtimes,
            update=update,
            include_foundation=include_foundation,
            adopt_foundation=adopt_foundation,
            register=register,
        )


def plan_git_update(
    source: str,
    ref: str,
    project: str | Path,
    *,
    runtimes: Iterable[str] = ("all",),
    include_foundation: bool = False,
) -> dict[str, Any]:
    project_root = Path(project).expanduser().resolve()
    current = load_install_lock(project_root)
    if current is None:
        raise DistributionError("cannot plan an update before ISEKAI is installed")
    selected = _normalize_runtimes(runtimes)
    with tempfile.TemporaryDirectory(prefix="isekai-release-plan-") as temporary:
        checkout = Path(temporary) / "checkout"
        _git(["clone", "--quiet", "--no-checkout", source, str(checkout)])
        _git(["checkout", "--quiet", "--detach", ref], cwd=checkout)
        commit = _git(["rev-parse", "HEAD^{commit}"], cwd=checkout)
        locked_source = current.get("source", {})
        if (
            locked_source.get("git") == source
            and locked_source.get("ref") == ref
            and locked_source.get("commit") not in {None, commit}
        ):
            raise DistributionError(
                "Git ref moved to a different commit; use a new immutable tag"
            )
        target = _verify_or_raise(checkout)
    adapters = {item["id"]: item for item in target["adapters"]}
    changes = [
        {
            "component": "core",
            "from": current.get("core", {}).get("version"),
            "to": target["core"]["version"],
        }
    ]
    changes.extend(
        {
            "component": f"adapter:{runtime}",
            "from": current.get("adapters", {}).get(runtime, {}).get("version"),
            "to": adapters[runtime]["version"],
        }
        for runtime in selected
    )
    changes.append(
        {
            "component": "foundation",
            "from": current.get("foundation", {}).get("version"),
            "to": (
                target["foundation"]["version"]
                if include_foundation
                else current.get("foundation", {}).get("version")
            ),
            "policy": "explicit" if include_foundation else "preserved",
        }
    )
    return {
        "ready": True,
        "project": str(project_root),
        "source": source,
        "ref": ref,
        "commit": commit,
        "current_release": current.get("release"),
        "target_release": target["version"],
        "protocol_version": target["protocol_version"],
        "changes": changes,
        "requires_confirmation": True,
        "new_conversation_required": bool({"codex", "claude"} & set(selected)),
    }


def rollback_install(project: str | Path, *, register: bool = False) -> dict[str, Any]:
    project_root = Path(project).expanduser().resolve()
    current = load_install_lock(project_root)
    if current is None:
        raise DistributionError("cannot roll back before ISEKAI is installed")
    health = doctor_install(project_root)
    if not health["ready"]:
        raise DistributionError("cannot roll back a modified installation")
    managed = project_root / MANAGED_ROOT
    rollback = managed / "rollback"
    previous_install = rollback / "install"
    previous_lock_path = rollback / LOCK_NAME
    if not previous_install.is_dir() or not previous_lock_path.is_file():
        raise DistributionError("no previous ISEKAI installation is available")
    previous_lock = _read_json(previous_lock_path)

    stage_root = Path(tempfile.mkdtemp(prefix=".isekai-rollback-stage-", dir=project_root))
    staged = stage_root / MANAGED_ROOT
    previous_kiro_copy = stage_root / "previous-kiro"
    previous_project_bytes = (
        (rollback / "project.json").read_bytes()
        if (rollback / "project.json").is_file()
        else None
    )
    if (rollback / "kiro").is_dir():
        shutil.copytree(rollback / "kiro", previous_kiro_copy)
    backup = project_root / f".{MANAGED_ROOT}-backup-{uuid.uuid4().hex}"
    kiro_target = project_root / ".kiro/skills/isekai"
    kiro_backup = project_root / f".isekai-kiro-backup-{uuid.uuid4().hex}"
    try:
        shutil.copytree(previous_install, staged)
        redo = staged / "rollback"
        _copy_managed_root(managed, redo / "install")
        _write_json_atomic(redo / LOCK_NAME, current)
        if kiro_target.is_dir() and "kiro" in current.get("adapters", {}):
            shutil.copytree(kiro_target, redo / "kiro")
        project_manifest = project_root / "project.json"
        if project_manifest.is_file():
            (redo / "project.json").write_bytes(project_manifest.read_bytes())

        managed.rename(backup)
        staged.rename(managed)
        if previous_kiro_copy.is_dir():
            if kiro_target.exists():
                kiro_target.rename(kiro_backup)
            kiro_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(previous_kiro_copy, kiro_target)
        if previous_project_bytes is not None:
            project_manifest.write_bytes(previous_project_bytes)
        _write_json_atomic(project_root / LOCK_NAME, previous_lock)
    except Exception:
        if managed.exists() and backup.exists():
            shutil.rmtree(managed)
            backup.rename(managed)
        if kiro_backup.exists():
            if kiro_target.exists():
                shutil.rmtree(kiro_target)
            kiro_backup.rename(kiro_target)
        raise
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)
        if backup.exists():
            shutil.rmtree(backup)
        if kiro_backup.exists():
            shutil.rmtree(kiro_backup)

    runtimes = sorted(previous_lock.get("adapters", {}))
    commands = _registration_commands(
        project_root,
        str(previous_lock.get("marketplace")),
        runtimes,
        update=True,
    )
    registration = _run_registration(commands) if register else []
    postflight = doctor_install(project_root)
    if not postflight["ready"]:
        raise DistributionError("rollback verification failed: " + "; ".join(postflight["issues"]))
    return {
        "rolled_back": True,
        "project": str(project_root),
        "release": previous_lock.get("release"),
        "runtimes": runtimes,
        "registration_commands": commands,
        "registration": registration,
        "new_conversation_required": bool({"codex", "claude"} & set(runtimes)),
    }


def verify_adapter_handshake(
    runtime: str,
    adapter_version: str,
    protocol_version: str,
    project: str | Path = ".",
) -> dict[str, Any]:
    if runtime not in RUNTIMES:
        raise DistributionError(f"unknown runtime: {runtime}")
    if protocol_version != PROTOCOL_VERSION:
        raise DistributionError(
            f"adapter protocol {protocol_version} is incompatible with Core protocol {PROTOCOL_VERSION}"
        )
    requested = Path(project).expanduser().resolve()
    root = requested.parent if requested.is_file() else requested
    for candidate in (root, *root.parents):
        if (candidate / LOCK_NAME).is_file():
            root = candidate
            break
    lock = load_install_lock(root)
    if lock is not None:
        adapter = lock.get("adapters", {}).get(runtime)
        if not isinstance(adapter, dict):
            raise DistributionError(f"{runtime} adapter is not installed for this project")
        if str(adapter.get("version", "")).split("+", 1)[0] != adapter_version.split("+", 1)[0]:
            raise DistributionError(f"{runtime} adapter version does not match project lock")
        if lock.get("core", {}).get("version") != __version__:
            raise DistributionError("running Core version does not match project lock")
        health = doctor_install(root)
        if not health["ready"]:
            raise DistributionError("project installation is unhealthy: " + "; ".join(health["issues"]))
    return {
        "compatible": True,
        "runtime": runtime,
        "adapter_version": adapter_version,
        "core_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "locked": lock is not None,
    }
