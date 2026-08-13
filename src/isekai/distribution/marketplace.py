from __future__ import annotations

import os
import re
import shutil
import stat
from pathlib import Path
from typing import Any

from ._marketplace_validation import (
    MarketplaceValidationOperations,
    managed_control_issues as _validate_managed_controls,
)
from ..support.files import (
    UnsafeControlFile,
    inspect_tree_beneath,
    read_control_file_snapshot,
)
from ..support.jsonio import (
    write_bytes_atomic,
)
from .project_io import (
    unlink_project_file as _unlink_host_file,
    write_project_bytes as _write_host_bytes,
    write_project_json as _write_host_json,
)
from .release import (
    MANAGED_ROOT,
    LEGACY_PLUGIN_ID,
    DistributionError,
    is_transient as _is_transient,
    read_control_bytes as _read_control_bytes,
    read_control_json as _read_control_json,
    read_json as _read_json,
    verified_tree_digest as _verified_tree_digest,
    write_json_atomic as _write_json_atomic,
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
    if manifest.exists() or manifest.is_symlink():
        value = _read_control_json(
            manifest,
            root=project_root,
            label="project manifest",
        ).get("id")
        if isinstance(value, str) and value.strip():
            return value
    return project_root.name


def _replace_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, ignore=_ignore_transient_files, symlinks=True)


def _ignore_transient_files(path: str, names: list[str]) -> set[str]:
    directory = Path(path)
    return {name for name in names if _is_transient(directory / name)}


def _copy_managed_root(source: Path, destination: Path) -> None:
    inspect_tree_beneath(source, label="managed installation")

    def ignore(path: str, names: list[str]) -> set[str]:
        ignored = _ignore_transient_files(path, names)
        if Path(path).resolve() == source.resolve():
            ignored.update({"rollback"} & set(names))
        return ignored

    shutil.copytree(source, destination, ignore=ignore, symlinks=True)


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
    source_digest: object,
) -> tuple[Path, str]:
    root = managed / "marketplaces/codex"
    plugin_root = root / "plugins" / LEGACY_PLUGIN_ID
    _replace_tree(adapter_source, plugin_root)
    _verified_tree_digest(
        plugin_root,
        source_digest,
        label="codex Adapter",
        include_transients=True,
    )
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
    source_digest: object,
) -> Path:
    root = managed / "marketplaces/claude"
    plugin_root = root / "plugins" / LEGACY_PLUGIN_ID
    _replace_tree(adapter_source, plugin_root)
    _verified_tree_digest(
        plugin_root,
        source_digest,
        label="claude Adapter",
        include_transients=True,
    )
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
            actual = _read_control_bytes(
                path,
                root=managed,
                label="managed launcher",
            )
        except DistributionError as exc:
            issues.append(str(exc))
            continue
        if actual != expected:
            issues.append(f"managed launcher content mismatch: {path}")
    posix_launcher = binary / "isekai"
    if os.name != "nt" and posix_launcher.is_file():
        try:
            executable = bool(posix_launcher.lstat().st_mode & 0o111)
        except OSError:
            executable = False
        if not executable:
            issues.append(f"managed launcher is not executable: {posix_launcher}")
    return issues


def _codex_plugin_entry(*, managed: bool) -> dict[str, Any]:
    path = (
        f"./plugins/{LEGACY_PLUGIN_ID}"
        if not managed
        else f"./{MANAGED_ROOT}/marketplaces/codex/plugins/{LEGACY_PLUGIN_ID}"
    )
    return {
        "name": LEGACY_PLUGIN_ID,
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
                "name": LEGACY_PLUGIN_ID,
                "source": f"./plugins/{LEGACY_PLUGIN_ID}",
                "description": "ISEKAI AI-DLC workflow integration for Claude Code",
                "version": version,
            }
        ],
    }


def _adapter_uses_managed_plugin(adapters: object, runtime: str) -> bool:
    if not isinstance(adapters, dict):
        return False
    entry = adapters.get(runtime)
    expected = f"{MANAGED_ROOT}/marketplaces/{runtime}/plugins/{LEGACY_PLUGIN_ID}"
    return isinstance(entry, dict) and entry.get("path") == expected


def _remove_legacy_project_plugin_declarations(
    project_root: Path,
    marketplace_name: str,
    runtimes: set[str],
    current_adapters: object,
) -> dict[str, Any]:
    """Remove only declarations owned by the former Plugin-first installer.

    Runtime Skill releases no longer register project marketplaces.  This
    migration keeps unrelated host configuration byte-for-byte and returns the
    previous owned slots so install rollback can restore them.
    """
    selected = {
        runtime
        for runtime in runtimes
        if runtime in {"codex", "claude"}
        and _adapter_uses_managed_plugin(current_adapters, runtime)
    }
    state = _capture_host_slots(project_root, marketplace_name, selected)
    paths = [
        project_root / relative
        for runtime, relative in {
            "codex": CODEX_REPO_MARKETPLACE,
            "claude": CLAUDE_PROJECT_SETTINGS,
        }.items()
        if runtime in selected
    ]
    snapshots = _host_file_snapshots(project_root, paths)
    try:
        if "codex" in selected:
            path = project_root / CODEX_REPO_MARKETPLACE
            document = _read_control_json(
                path,
                root=project_root,
                label="Codex repo marketplace",
            )
            plugins = document.get("plugins")
            if not isinstance(plugins, list):
                raise DistributionError(
                    "Codex repo marketplace plugins must be a list"
                )
            index, entry = _find_plugin(plugins)
            if entry != _codex_plugin_entry(managed=True):
                raise DistributionError(
                    "legacy Codex ISEKAI marketplace entry is not installer-owned"
                )
            assert index is not None
            plugins.pop(index)
            generated_empty = {
                "name": marketplace_name,
                "interface": {"displayName": f"ISEKAI ({marketplace_name})"},
                "plugins": [],
            }
            if document == generated_empty:
                _unlink_host_file(project_root, CODEX_REPO_MARKETPLACE)
            else:
                _write_host_json(project_root, CODEX_REPO_MARKETPLACE, document)

        if "claude" in selected:
            path = project_root / CLAUDE_PROJECT_SETTINGS
            document = _read_control_json(
                path,
                root=project_root,
                label="Claude project settings",
            )
            marketplaces = document.get("extraKnownMarketplaces")
            enabled = document.get("enabledPlugins")
            expected_marketplace = {
                "source": {
                    "source": "directory",
                    "path": f"./{MANAGED_ROOT}/marketplaces/claude",
                }
            }
            plugin_key = f"{LEGACY_PLUGIN_ID}@{marketplace_name}"
            if not isinstance(marketplaces, dict) or (
                marketplaces.get(marketplace_name) != expected_marketplace
            ):
                raise DistributionError(
                    "legacy Claude ISEKAI marketplace is not installer-owned"
                )
            if not isinstance(enabled, dict) or enabled.get(plugin_key) is not True:
                raise DistributionError(
                    "legacy Claude ISEKAI Plugin enablement is not installer-owned"
                )
            marketplaces.pop(marketplace_name)
            enabled.pop(plugin_key)
            if not marketplaces:
                document.pop("extraKnownMarketplaces")
            if not enabled:
                document.pop("enabledPlugins")
            if document:
                _write_host_json(project_root, CLAUDE_PROJECT_SETTINGS, document)
            else:
                _unlink_host_file(project_root, CLAUDE_PROJECT_SETTINGS)
    except Exception as exc:
        _restore_host_file_snapshots(project_root, snapshots, cause=exc)
        raise
    return state


def _find_plugin(plugins: list[Any]) -> tuple[int | None, Any]:
    matches = [
        (index, entry)
        for index, entry in enumerate(plugins)
        if isinstance(entry, dict) and entry.get("name") == LEGACY_PLUGIN_ID
    ]
    if len(matches) > 1:
        raise DistributionError(f"duplicate {LEGACY_PLUGIN_ID} entries in repo marketplace")
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
        document = (
            _read_control_json(
                path,
                root=project_root,
                label="Codex repo marketplace",
            )
            if path.exists() or path.is_symlink()
            else None
        )
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
        document = (
            _read_control_json(
                path,
                root=project_root,
                label="Claude project settings",
            )
            if path.exists() or path.is_symlink()
            else None
        )
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
            "enabled": enabled.get(f"{LEGACY_PLUGIN_ID}@{marketplace_name}"),
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
            _read_control_json(
                path,
                root=project_root,
                label="Codex repo marketplace",
            )
            if path.exists() or path.is_symlink()
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
                f"refusing to replace unmanaged {LEGACY_PLUGIN_ID} repo marketplace entry"
            )
        if index is None:
            plugins.append(expected)
        else:
            plugins[index] = expected
        documents[CODEX_REPO_MARKETPLACE.as_posix()] = document
    if "claude" in runtimes:
        path = project_root / CLAUDE_PROJECT_SETTINGS
        document = (
            _read_control_json(
                path,
                root=project_root,
                label="Claude project settings",
            )
            if path.exists() or path.is_symlink()
            else {}
        )
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
        plugin_key = f"{LEGACY_PLUGIN_ID}@{marketplace_name}"
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
    snapshots = _host_file_snapshots(project_root, paths)
    try:
        for relative, document in documents.items():
            _write_host_json(project_root, relative, document)
    except Exception as exc:
        _restore_host_file_snapshots(project_root, snapshots, cause=exc)
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
    snapshots = _host_file_snapshots(project_root, paths)
    try:
        _restore_host_slots_unchecked(project_root, state, marketplace_name)
    except Exception as exc:
        _restore_host_file_snapshots(project_root, snapshots, cause=exc)
        raise


def _host_file_snapshots(
    project_root: Path,
    paths: list[Path],
) -> dict[Path, tuple[bytes, int] | None]:
    snapshots: dict[Path, tuple[bytes, int] | None] = {}
    for path in paths:
        if path.exists() or path.is_symlink():
            try:
                content, metadata = read_control_file_snapshot(
                    path,
                    root=project_root,
                    label="project host configuration",
                )
            except (OSError, UnsafeControlFile) as exc:
                raise DistributionError(str(exc)) from exc
            snapshots[path] = (content, stat.S_IMODE(metadata.st_mode))
        else:
            snapshots[path] = None
    return snapshots


def _restore_host_file_snapshots(
    project_root: Path,
    snapshots: dict[Path, tuple[bytes, int] | None],
    *,
    cause: Exception,
) -> None:
    errors: list[str] = []
    for path, snapshot in reversed(list(snapshots.items())):
        try:
            if snapshot is None:
                _unlink_host_file(project_root, path.relative_to(project_root))
            else:
                content, mode = snapshot
                _write_host_bytes(
                    project_root,
                    path.relative_to(project_root),
                    content,
                    mode=mode,
                )
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
        document = (
            _read_control_json(
                path,
                root=project_root,
                label="Codex repo marketplace",
            )
            if path.exists() or path.is_symlink()
            else {}
        )
        plugins = document.setdefault("plugins", [])
        if not isinstance(plugins, list):
            raise DistributionError("Codex repo marketplace plugins must be a list")
        plugins[:] = [
            entry
            for entry in plugins
            if not (isinstance(entry, dict) and entry.get("name") == LEGACY_PLUGIN_ID)
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
            _unlink_host_file(project_root, CODEX_REPO_MARKETPLACE)
        else:
            _write_host_json(project_root, CODEX_REPO_MARKETPLACE, document)
    claude = state.get("claude")
    if isinstance(claude, dict):
        path = project_root / CLAUDE_PROJECT_SETTINGS
        document = (
            _read_control_json(
                path,
                root=project_root,
                label="Claude project settings",
            )
            if path.exists() or path.is_symlink()
            else {}
        )
        name = str(claude.get("marketplace_name") or marketplace_name)
        plugin_key = f"{LEGACY_PLUGIN_ID}@{name}"
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
            _unlink_host_file(project_root, CLAUDE_PROJECT_SETTINGS)
        else:
            _write_host_json(project_root, CLAUDE_PROJECT_SETTINGS, document)


class _MarketplaceValidationAdapter(MarketplaceValidationOperations):
    def launcher_issues(self, managed: Path) -> list[str]:
        return _launcher_issues(managed)

    def codex_marketplace_manifest(
        self, marketplace_name: str, *, managed: bool = False
    ) -> dict[str, Any]:
        return _codex_marketplace_manifest(marketplace_name, managed=managed)

    def claude_marketplace_manifest(
        self, marketplace_name: str, version: str
    ) -> dict[str, Any]:
        return _claude_marketplace_manifest(marketplace_name, version)

    def read_control_json(
        self, path: Path, *, root: Path, label: str
    ) -> dict[str, Any]:
        return _read_control_json(path, root=root, label=label)

    def find_plugin(self, plugins: list[Any]) -> tuple[int | None, Any]:
        return _find_plugin(plugins)

    def codex_plugin_entry(self, *, managed: bool) -> dict[str, Any]:
        return _codex_plugin_entry(managed=managed)


_VALIDATION_OPERATIONS: MarketplaceValidationOperations = (
    _MarketplaceValidationAdapter()
)


def managed_control_issues(
    project_root: Path,
    lock: dict[str, Any],
) -> list[str]:
    return _validate_managed_controls(project_root, lock, _VALIDATION_OPERATIONS)


# Typed internal marketplace transaction contract.
adapter_uses_managed_plugin = _adapter_uses_managed_plugin
capture_host_slots = _capture_host_slots
copy_managed_root = _copy_managed_root
remove_legacy_project_plugin_declarations = _remove_legacy_project_plugin_declarations
replace_tree = _replace_tree
restore_host_slots = _restore_host_slots
write_launchers = _write_launchers
