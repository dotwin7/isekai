from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from ..support.jsonio import write_bytes_atomic
from .release import (
    MANAGED_ROOT,
    PLUGIN_ID,
    DistributionError,
    _is_transient,
    _read_json,
    _write_json_atomic,
)


_PYTHON_LAUNCHER = (
    "from pathlib import Path\n"
    "import sys\n\n"
    "runtime = Path(__file__).resolve().parents[1] / 'runtime'\n"
    "if any(path.name == '__pycache__' for path in runtime.rglob('__pycache__')):\n"
    "    raise SystemExit('ISEKAI runtime contains unchecked bytecode; run doctor and repair the installation')\n"
    "sys.dont_write_bytecode = True\n"
    "sys.path.insert(0, str(runtime))\n"
    "from isekai.cli import main\n\n"
    "raise SystemExit(main())\n"
).encode("utf-8")
_POSIX_LAUNCHER = b'#!/bin/sh\nexec python3 "$(dirname "$0")/isekai.py" "$@"\n'
_WINDOWS_LAUNCHER = b'@py -3 "%~dp0isekai.py" %*\r\n'
_LAUNCHER_CONTENT = {
    "isekai.py": _PYTHON_LAUNCHER,
    "isekai": _POSIX_LAUNCHER,
    "isekai.cmd": _WINDOWS_LAUNCHER,
}
CODEX_REPO_MARKETPLACE = Path(".agents/plugins/marketplace.json")
CLAUDE_PROJECT_SETTINGS = Path(".claude/settings.json")


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
    shutil.copytree(source, target, ignore=_ignore_transient_files)


def _ignore_transient_files(path: str, names: list[str]) -> set[str]:
    directory = Path(path)
    return {name for name in names if _is_transient(directory / name)}


def _copy_managed_root(source: Path, destination: Path) -> None:
    def ignore(path: str, names: list[str]) -> set[str]:
        ignored = _ignore_transient_files(path, names)
        if Path(path).resolve() == source.resolve():
            ignored.update({"rollback"} & set(names))
        return ignored

    shutil.copytree(source, destination, ignore=ignore)


def _write_launchers(managed: Path) -> None:
    binary = managed / "bin"
    binary.mkdir(parents=True, exist_ok=True)
    for name, content in _LAUNCHER_CONTENT.items():
        (binary / name).write_bytes(content)
    (binary / "isekai").chmod(0o755)


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
        root / CODEX_REPO_MARKETPLACE,
        _codex_marketplace_manifest(marketplace_name, managed=False),
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
        _claude_marketplace_manifest(marketplace_name, version),
    )
    return plugin_root


def _launcher_issues(managed: Path) -> list[str]:
    """Validate generated launchers that sit outside component digest roots."""
    issues: list[str] = []
    binary = managed / "bin"
    for name, expected in _LAUNCHER_CONTENT.items():
        path = binary / name
        try:
            actual = path.read_bytes()
        except OSError:
            issues.append(f"managed launcher is missing or unreadable: {path}")
            continue
        if actual != expected:
            issues.append(f"managed launcher content mismatch: {path}")
    posix_launcher = binary / "isekai"
    if os.name != "nt" and posix_launcher.is_file():
        try:
            executable = bool(posix_launcher.stat().st_mode & 0o111)
        except OSError:
            executable = False
        if not executable:
            issues.append(f"managed launcher is not executable: {posix_launcher}")
    return issues


def _codex_plugin_entry(*, managed: bool) -> dict[str, Any]:
    path = (
        f"./plugins/{PLUGIN_ID}"
        if not managed
        else f"./{MANAGED_ROOT}/marketplaces/codex/plugins/{PLUGIN_ID}"
    )
    return {
        "name": PLUGIN_ID,
        "source": {"source": "local", "path": path},
        "policy": {
            "installation": "INSTALLED_BY_DEFAULT" if managed else "AVAILABLE",
            "authentication": "ON_INSTALL",
        },
        "category": "Productivity",
    }


def _codex_marketplace_manifest(
    marketplace_name: str,
    *,
    managed: bool = False,
) -> dict[str, Any]:
    return {
        "name": marketplace_name,
        "interface": {"displayName": f"ISEKAI ({marketplace_name})"},
        "plugins": [_codex_plugin_entry(managed=managed)],
    }


def _claude_marketplace_manifest(
    marketplace_name: str,
    version: str,
) -> dict[str, Any]:
    return {
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
    }


def _adapter_uses_managed_plugin(adapters: object, runtime: str) -> bool:
    if not isinstance(adapters, dict):
        return False
    entry = adapters.get(runtime)
    expected = f"{MANAGED_ROOT}/marketplaces/{runtime}/plugins/{PLUGIN_ID}"
    return isinstance(entry, dict) and entry.get("path") == expected


def _find_plugin(plugins: list[Any]) -> tuple[int | None, Any]:
    matches = [
        (index, entry)
        for index, entry in enumerate(plugins)
        if isinstance(entry, dict) and entry.get("name") == PLUGIN_ID
    ]
    if len(matches) > 1:
        raise DistributionError(f"duplicate {PLUGIN_ID} entries in repo marketplace")
    return matches[0] if matches else (None, None)


def _capture_host_slots(
    project_root: Path,
    marketplace_name: str,
    runtimes: set[str],
) -> dict[str, Any]:
    """Capture only ISEKAI-owned settings so rollback preserves unrelated edits."""
    state: dict[str, Any] = {}
    if "codex" in runtimes:
        path = project_root / CODEX_REPO_MARKETPLACE
        document = _read_json(path) if path.is_file() else None
        plugins = document.get("plugins", []) if isinstance(document, dict) else []
        if not isinstance(plugins, list):
            raise DistributionError("Codex repo marketplace plugins must be a list")
        index, entry = _find_plugin(plugins)
        state["codex"] = {
            "file_existed": document is not None,
            "entry_index": index,
            "entry": entry,
        }
    if "claude" in runtimes:
        path = project_root / CLAUDE_PROJECT_SETTINGS
        document = _read_json(path) if path.is_file() else None
        settings = document if isinstance(document, dict) else {}
        marketplaces = settings.get("extraKnownMarketplaces", {})
        enabled = settings.get("enabledPlugins", {})
        if not isinstance(marketplaces, dict) or not isinstance(enabled, dict):
            raise DistributionError("Claude project plugin settings must be objects")
        state["claude"] = {
            "file_existed": document is not None,
            "marketplaces_existed": "extraKnownMarketplaces" in settings,
            "enabled_existed": "enabledPlugins" in settings,
            "marketplace": marketplaces.get(marketplace_name),
            "enabled": enabled.get(f"{PLUGIN_ID}@{marketplace_name}"),
            "marketplace_name": marketplace_name,
        }
    return state


def _project_host_documents(
    project_root: Path,
    marketplace_name: str,
    runtimes: set[str],
    current_adapters: object,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Merge project-scoped host declarations without touching user config."""
    state = _capture_host_slots(project_root, marketplace_name, runtimes)
    documents: dict[str, dict[str, Any]] = {}
    if "codex" in runtimes:
        path = project_root / CODEX_REPO_MARKETPLACE
        document = (
            _read_json(path)
            if path.is_file()
            else _codex_marketplace_manifest(marketplace_name, managed=True)
        )
        plugins = document.get("plugins")
        if not isinstance(plugins, list):
            raise DistributionError("Codex repo marketplace plugins must be a list")
        index, existing = _find_plugin(plugins)
        expected = _codex_plugin_entry(managed=True)
        owned = _adapter_uses_managed_plugin(current_adapters, "codex")
        if existing is not None and existing != expected and not owned:
            raise DistributionError(
                f"refusing to replace unmanaged {PLUGIN_ID} repo marketplace entry"
            )
        if index is None:
            plugins.append(expected)
        else:
            plugins[index] = expected
        documents[CODEX_REPO_MARKETPLACE.as_posix()] = document
    if "claude" in runtimes:
        path = project_root / CLAUDE_PROJECT_SETTINGS
        document = _read_json(path) if path.is_file() else {}
        marketplaces = document.setdefault("extraKnownMarketplaces", {})
        enabled = document.setdefault("enabledPlugins", {})
        if not isinstance(marketplaces, dict) or not isinstance(enabled, dict):
            raise DistributionError("Claude project plugin settings must be objects")
        expected_marketplace = {
            "source": {
                "source": "directory",
                "path": f"./{MANAGED_ROOT}/marketplaces/claude",
            }
        }
        plugin_key = f"{PLUGIN_ID}@{marketplace_name}"
        existing = marketplaces.get(marketplace_name)
        owned = _adapter_uses_managed_plugin(current_adapters, "claude")
        if existing is not None and existing != expected_marketplace and not owned:
            raise DistributionError(
                f"refusing to replace unmanaged Claude marketplace {marketplace_name}"
            )
        if plugin_key in enabled and enabled[plugin_key] is not True and not owned:
            raise DistributionError(
                f"refusing to enable unmanaged Claude plugin setting {plugin_key}"
            )
        marketplaces[marketplace_name] = expected_marketplace
        enabled[plugin_key] = True
        documents[CLAUDE_PROJECT_SETTINGS.as_posix()] = document
    return documents, state


def _apply_project_host_documents(
    project_root: Path,
    documents: dict[str, dict[str, Any]],
) -> None:
    paths = [project_root / relative for relative in documents]
    snapshots = _host_file_snapshots(paths)
    try:
        for relative, document in documents.items():
            _write_json_atomic(project_root / relative, document)
    except Exception as exc:
        _restore_host_file_snapshots(snapshots, cause=exc)
        raise


def _restore_host_slots(
    project_root: Path,
    state: dict[str, Any],
    marketplace_name: str,
) -> None:
    paths = []
    if isinstance(state.get("codex"), dict):
        paths.append(project_root / CODEX_REPO_MARKETPLACE)
    if isinstance(state.get("claude"), dict):
        paths.append(project_root / CLAUDE_PROJECT_SETTINGS)
    snapshots = _host_file_snapshots(paths)
    try:
        _restore_host_slots_unchecked(project_root, state, marketplace_name)
    except Exception as exc:
        _restore_host_file_snapshots(snapshots, cause=exc)
        raise


def _host_file_snapshots(
    paths: list[Path],
) -> dict[Path, tuple[bytes, int] | None]:
    snapshots: dict[Path, tuple[bytes, int] | None] = {}
    for path in paths:
        if path.is_file():
            snapshots[path] = (
                path.read_bytes(),
                stat.S_IMODE(path.stat().st_mode),
            )
        else:
            snapshots[path] = None
    return snapshots


def _restore_host_file_snapshots(
    snapshots: dict[Path, tuple[bytes, int] | None],
    *,
    cause: Exception,
) -> None:
    errors: list[str] = []
    for path, snapshot in reversed(list(snapshots.items())):
        try:
            if snapshot is None:
                path.unlink(missing_ok=True)
            else:
                content, mode = snapshot
                write_bytes_atomic(path, content, mode=mode)
        except Exception as exc:  # pragma: no cover - secondary filesystem failure
            errors.append(f"{path}: {exc}")
    if errors:
        raise DistributionError(
            "host configuration transaction failed and could not be restored: "
            + "; ".join(errors)
        ) from cause


def _restore_host_slots_unchecked(
    project_root: Path,
    state: dict[str, Any],
    marketplace_name: str,
) -> None:
    codex = state.get("codex")
    if isinstance(codex, dict):
        path = project_root / CODEX_REPO_MARKETPLACE
        document = _read_json(path) if path.is_file() else {}
        plugins = document.setdefault("plugins", [])
        if not isinstance(plugins, list):
            raise DistributionError("Codex repo marketplace plugins must be a list")
        plugins[:] = [
            entry
            for entry in plugins
            if not (isinstance(entry, dict) and entry.get("name") == PLUGIN_ID)
        ]
        previous = codex.get("entry")
        if previous is not None:
            index = codex.get("entry_index")
            plugins.insert(index if isinstance(index, int) else len(plugins), previous)
        if not codex.get("file_existed") and document == {
            "name": marketplace_name,
            "interface": {"displayName": f"ISEKAI ({marketplace_name})"},
            "plugins": [],
        }:
            path.unlink(missing_ok=True)
        else:
            _write_json_atomic(path, document)
    claude = state.get("claude")
    if isinstance(claude, dict):
        path = project_root / CLAUDE_PROJECT_SETTINGS
        document = _read_json(path) if path.is_file() else {}
        name = str(claude.get("marketplace_name") or marketplace_name)
        plugin_key = f"{PLUGIN_ID}@{name}"
        marketplaces = document.setdefault("extraKnownMarketplaces", {})
        enabled = document.setdefault("enabledPlugins", {})
        if not isinstance(marketplaces, dict) or not isinstance(enabled, dict):
            raise DistributionError("Claude project plugin settings must be objects")
        if claude.get("marketplace") is None:
            marketplaces.pop(name, None)
        else:
            marketplaces[name] = claude["marketplace"]
        if claude.get("enabled") is None:
            enabled.pop(plugin_key, None)
        else:
            enabled[plugin_key] = claude["enabled"]
        if not claude.get("marketplaces_existed") and not marketplaces:
            document.pop("extraKnownMarketplaces", None)
        if not claude.get("enabled_existed") and not enabled:
            document.pop("enabledPlugins", None)
        if not claude.get("file_existed") and not document:
            path.unlink(missing_ok=True)
        else:
            _write_json_atomic(path, document)


def _managed_control_issues(
    project_root: Path,
    lock: dict[str, Any],
) -> list[str]:
    """Validate generated launchers and host metadata not covered by adapter digests."""
    issues = _launcher_issues(project_root / MANAGED_ROOT)
    adapters = lock.get("adapters")
    if not isinstance(adapters, dict):
        return issues
    selected = {
        runtime
        for runtime, legacy_path in {
            "codex": f"{MANAGED_ROOT}/marketplaces/codex/plugins/{PLUGIN_ID}",
            "claude": f"{MANAGED_ROOT}/marketplaces/claude/plugins/{PLUGIN_ID}",
        }.items()
        if isinstance(adapters.get(runtime), dict)
        and adapters[runtime].get("path") == legacy_path
    }
    if not selected:
        return issues
    marketplace_name = lock.get("marketplace")
    if not isinstance(marketplace_name, str) or not marketplace_name.strip():
        return [*issues, "lock marketplace must be a non-empty string"]

    expected_documents: list[tuple[str, Path, dict[str, Any]]] = []
    if "codex" in selected:
        expected_documents.append(
            (
                "codex",
                project_root
                / MANAGED_ROOT
                / "marketplaces/codex/.agents/plugins/marketplace.json",
                _codex_marketplace_manifest(marketplace_name),
            )
        )
    if "claude" in selected:
        entry = adapters.get("claude")
        version = entry.get("version") if isinstance(entry, dict) else None
        if not isinstance(version, str) or not version:
            issues.append("claude adapter lock has no version for marketplace validation")
        else:
            expected_documents.append(
                (
                    "claude",
                    project_root
                    / MANAGED_ROOT
                    / "marketplaces/claude/.claude-plugin/marketplace.json",
                    _claude_marketplace_manifest(marketplace_name, version),
                )
            )
    for runtime, path, expected in expected_documents:
        try:
            actual = _read_json(path)
        except DistributionError as exc:
            issues.append(str(exc))
            continue
        if actual != expected:
            issues.append(f"{runtime} marketplace metadata mismatch")
    if "codex" in selected:
        try:
            document = _read_json(project_root / CODEX_REPO_MARKETPLACE)
            plugins = document.get("plugins")
            if not isinstance(plugins, list):
                raise DistributionError("Codex repo marketplace plugins must be a list")
            _, entry = _find_plugin(plugins)
            if entry != _codex_plugin_entry(managed=True):
                issues.append("Codex repo marketplace ISEKAI entry mismatch")
        except DistributionError as exc:
            issues.append(str(exc))
    if "claude" in selected:
        try:
            settings = _read_json(project_root / CLAUDE_PROJECT_SETTINGS)
            marketplaces = settings.get("extraKnownMarketplaces")
            enabled = settings.get("enabledPlugins")
            expected_marketplace = {
                "source": {
                    "source": "directory",
                    "path": f"./{MANAGED_ROOT}/marketplaces/claude",
                }
            }
            plugin_key = f"{PLUGIN_ID}@{marketplace_name}"
            if not isinstance(marketplaces, dict) or (
                marketplaces.get(marketplace_name) != expected_marketplace
            ):
                issues.append("Claude project marketplace declaration mismatch")
            if not isinstance(enabled, dict) or enabled.get(plugin_key) is not True:
                issues.append("Claude project plugin enablement mismatch")
        except DistributionError as exc:
            issues.append(str(exc))
    return issues
