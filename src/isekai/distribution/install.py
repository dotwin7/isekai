from __future__ import annotations
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterable
from .. import __version__
from ..support.locking import LockUnavailable, rooted_file_lock
from .lockfile import (
    INSTALL_LOCK_NAME,
    WORKSPACE_ADAPTER_PATHS,
    _installed_path,
    _load_install_lock_path,
    _project_path_without_symlinks,
    _workspace_adapter_owned,
    load_install_lock,
)
from .release import (
    LOCK_NAME,
    LOCK_SCHEMA_VERSION,
    MANAGED_ROOT,
    PROTOCOL_VERSION,
    RUNTIMES,
    DistributionError,
    _component_root,
    _normalize_runtimes,
    _read_control_bytes,
    _read_control_json,
    _verified_tree_digest,
    _verify_or_raise,
    _write_json_atomic,
    tree_digest,
)
from ..foundation import FoundationError, load_foundation
from .catalog import stage_catalog
from .project_io import (
    adapter_skill_source as _adapter_skill_source,
    remove_path as _remove_path,
    restore_project_file as _restore_project_file,
    write_project_json as _write_project_json,
)
from .marketplace import (
    CLAUDE_PROJECT_SETTINGS,
    CODEX_REPO_MARKETPLACE,
    _adapter_uses_managed_plugin,
    _capture_host_slots,
    _copy_managed_root,
    _managed_control_issues,
    _remove_legacy_project_plugin_declarations,
    _replace_tree,
    _restore_host_slots,
    _write_launchers,
)


def doctor_install(project: str | Path) -> dict[str, Any]:
    project_root = Path(project).expanduser().resolve()
    try:
        lock = load_install_lock(project_root)
    except DistributionError as exc:
        return {
            "ready": False,
            "project": str(project_root),
            "release": None,
            "protocol_version": None,
            "runtimes": [],
            "issues": [str(exc)],
        }
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
    catalog_lock_entry = lock.get("catalog")
    if isinstance(catalog_lock_entry, dict):
        components.append(("catalog", catalog_lock_entry))
    elif lock.get("release") == __version__:
        issues.append("lock is missing catalog")
    adapters = lock.get("adapters")
    if not isinstance(adapters, dict):
        issues.append("lock adapters must be an object")
        adapters = {}
    for runtime, entry in sorted(adapters.items()):
        if runtime not in RUNTIMES:
            issues.append(f"unknown adapter in lock: {runtime}")
            continue
        if isinstance(entry, dict):
            components.append((f"adapter:{runtime}", entry))
            if "workspace_path" in entry or "workspace_digest" in entry:
                components.append(
                    (f"adapter:{runtime}.workspace", {
                        "path": entry.get("workspace_path"),
                        "digest": entry.get("workspace_digest"),
                    })
                )
        else:
            issues.append(f"adapter lock is invalid: {runtime}")

    rollback = lock.get("rollback")
    if isinstance(rollback, dict):
        components.append(("rollback", rollback))

    for label, entry in components:
        try:
            target = _installed_path(
                project_root, entry.get("path"), label=f"{label}.path"
            )
            actual = tree_digest(target, include_transients=True)
        except DistributionError as exc:
            issues.append(str(exc))
            continue
        if actual != entry.get("digest"):
            issues.append(f"{label} digest mismatch")

    issues.extend(_managed_control_issues(project_root, lock))

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
    if (project_manifest.exists() or project_manifest.is_symlink()) and isinstance(
        foundation_entry, dict
    ):
        try:
            project_value = _read_control_json(
                project_manifest,
                root=project_root,
                label="project manifest",
            )
            foundation_path = project_value.get("foundation_path")
            if not isinstance(foundation_path, str):
                raise DistributionError("project foundation_path must be a string")
            selected = load_foundation(project_root / foundation_path)
            if selected.version != foundation_entry.get("version"):
                issues.append("Project Foundation version does not match lock")
            if tree_digest(
                selected.root, include_transients=True
            ) != foundation_entry.get("digest"):
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
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return True
    try:
        project = _read_control_json(
            manifest_path,
            root=project_root,
            label="project manifest",
        )
        foundation_path = project.get("foundation_path")
        if not isinstance(foundation_path, str):
            return False
        foundation = load_foundation(project_root / foundation_path)
        return (
            foundation.version == version
            and tree_digest(foundation.root, include_transients=True) == digest
        )
    except (DistributionError, FoundationError):
        return False


def _adopt_foundation(project_root: Path, relative: str) -> bytes | None:
    path = project_root / "project.json"
    if not path.exists() and not path.is_symlink():
        return None
    before = _read_control_bytes(
        path,
        root=project_root,
        label="project manifest",
    )
    project = _read_control_json(
        path,
        root=project_root,
        label="project manifest",
    )
    project["foundation_path"] = relative
    _write_project_json(project_root, "project.json", project)
    return before


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
) -> dict[str, Any]:
    """Verify and install a Git checkout under an immutable commit claim."""
    from .git import _verify_checkout_claim

    project_root = Path(project).expanduser().resolve()
    if not project_root.is_dir():
        raise DistributionError(f"project root does not exist: {project_root}")
    try:
        with rooted_file_lock(
            project_root,
            INSTALL_LOCK_NAME,
            subject=f"ISEKAI installation for {project_root}",
        ):
            release_root = Path(checkout).expanduser().resolve()
            _verify_checkout_claim(
                release_root,
                project_root,
                source=source,
                ref=ref,
                commit=commit,
            )
            return _install_from_checkout_locked(
                release_root,
                project_root,
                source=source,
                ref=ref,
                commit=commit,
                runtimes=runtimes,
                update=update,
                include_foundation=include_foundation,
                adopt_foundation=adopt_foundation,
            )
    except LockUnavailable as exc:
        raise DistributionError(str(exc)) from exc


def _install_from_verified_checkout(
    checkout: str | Path,
    project: str | Path,
    **options: Any,
) -> dict[str, Any]:
    """Internal test seam for a checkout already verified by its caller."""
    project_root = Path(project).expanduser().resolve()
    if not project_root.is_dir():
        raise DistributionError(f"project root does not exist: {project_root}")
    try:
        with rooted_file_lock(
            project_root,
            INSTALL_LOCK_NAME,
            subject=f"ISEKAI installation for {project_root}",
        ):
            return _install_from_checkout_locked(checkout, project_root, **options)
    except LockUnavailable as exc:
        raise DistributionError(str(exc)) from exc


def _install_from_checkout_locked(
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
) -> dict[str, Any]:
    """Install an already resolved checkout using ``commit`` as the immutable pin.

    Normal callers must use :func:`install_from_git`, which validates tags and full
    commits before loading release code. This lower-level helper trusts the caller
    to supply the checkout for the recorded full commit; ``ref`` is descriptive.
    """
    for field, value in (
        ("update", update),
        ("include_foundation", include_foundation),
        ("adopt_foundation", adopt_foundation),
    ):
        if not isinstance(value, bool):
            raise DistributionError(f"{field} must be boolean")
    if not isinstance(commit, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit
    ):
        raise DistributionError("commit must be a full 40- or 64-character hash")
    release_root = Path(checkout).resolve()
    project_root = Path(project).expanduser().resolve()
    if not project_root.is_dir():
        raise DistributionError(f"project root does not exist: {project_root}")
    project_manifest = project_root / "project.json"
    if project_manifest.exists() or project_manifest.is_symlink():
        _read_control_json(
            project_manifest,
            root=project_root,
            label="project manifest",
        )
    manifest = _verify_or_raise(release_root)
    selected = _normalize_runtimes(runtimes)
    current_lock = load_install_lock(project_root)
    if update and current_lock is None:
        raise DistributionError("cannot update before ISEKAI is installed")
    if current_lock is None and (project_root / MANAGED_ROOT).exists():
        raise DistributionError(
            f"refusing to replace unmanaged {MANAGED_ROOT}; move it aside or adopt it explicitly"
        )
    current_adapters = dict(current_lock.get("adapters", {})) if current_lock else {}
    current_workspace = {
        runtime
        for runtime in RUNTIMES
        if _workspace_adapter_owned(current_adapters, runtime)
    }
    desired_workspace = set(selected)
    workspace_changes = desired_workspace | (current_workspace & set(selected))
    workspace_targets: dict[str, Path] = {}
    for runtime in sorted(current_workspace | desired_workspace):
        relative = WORKSPACE_ADAPTER_PATHS[runtime]
        workspace_targets[runtime] = _project_path_without_symlinks(
            project_root,
            relative,
            label=f"adapter:{runtime}.path",
        )
    for runtime in desired_workspace:
        target = workspace_targets[runtime]
        if (
            (target.exists() or target.is_symlink())
            and not _workspace_adapter_owned(current_adapters, runtime)
        ):
            raise DistributionError(
                "refusing to replace an unmanaged "
                f"{WORKSPACE_ADAPTER_PATHS[runtime].as_posix()} directory"
            )
    legacy_plugin_runtimes = {
        runtime
        for runtime in {"codex", "claude"} & set(selected)
        if _adapter_uses_managed_plugin(current_adapters, runtime)
    }
    for runtime, relative in {
        "codex": CODEX_REPO_MARKETPLACE,
        "claude": CLAUDE_PROJECT_SETTINGS,
    }.items():
        if runtime in legacy_plugin_runtimes:
            _project_path_without_symlinks(
                project_root,
                relative,
                label=f"host:{runtime}.path",
            )
    if current_lock is not None:
        health = doctor_install(project_root)
        if not health["ready"]:
            raise DistributionError(
                "installed files were modified or are incomplete: " + "; ".join(health["issues"])
            )

    adapter_manifest = {item["id"]: item for item in manifest["adapters"]}
    legacy_marketplace_name = (
        str(current_lock.get("marketplace"))
        if current_lock and current_lock.get("marketplace")
        else "isekai-project"
    )
    host_state = _capture_host_slots(
        project_root,
        legacy_marketplace_name,
        legacy_plugin_runtimes,
    )
    installed_runtimes = sorted(set(current_adapters) | set(selected))

    def adapter_layout_current(runtime: str) -> bool:
        entry = current_adapters.get(runtime)
        return isinstance(entry, dict) and entry.get("path") == (
            WORKSPACE_ADAPTER_PATHS[runtime].as_posix()
        )

    selected_layout_current = all(adapter_layout_current(runtime) for runtime in selected)
    if (
        current_lock
        and current_lock.get("release") == manifest["version"]
        and current_lock.get("source", {}).get("commit") == commit
        and set(selected) <= set(current_adapters)
        and selected_layout_current
        and not include_foundation
        and not adopt_foundation
    ):
        return {
            "installed": False,
            "updated": False,
            "unchanged": True,
            "project": str(project_root),
            "release": manifest["version"],
            "commit": commit,
            "runtimes": installed_runtimes,
            "foundation": current_lock["foundation"],
            "catalog": current_lock["catalog"],
            "lock": str(project_root / LOCK_NAME),
            "host_registration_required": False,
            "new_conversation_required": False,
        }

    stage_root = Path(tempfile.mkdtemp(prefix=".isekai-stage-", dir=project_root))
    staged = stage_root / MANAGED_ROOT
    managed = project_root / MANAGED_ROOT
    backup = project_root / f".{MANAGED_ROOT}-backup-{uuid.uuid4().hex}"
    adapter_backup = project_root / f".isekai-adapter-backup-{uuid.uuid4().hex}"
    project_before: bytes | None = None
    lock_before = (
        _read_control_bytes(
            project_root / LOCK_NAME,
            root=project_root,
            label=LOCK_NAME,
        )
        if current_lock
        else None
    )
    host_applied = False
    try:
        if managed.is_dir():
            _copy_managed_root(managed, staged)
        else:
            staged.mkdir(parents=True)
        for runtime in legacy_plugin_runtimes:
            _remove_path(staged / "marketplaces" / runtime)
        marketplaces = staged / "marketplaces"
        if marketplaces.is_dir() and not any(marketplaces.iterdir()):
            marketplaces.rmdir()

        core_source = _component_root(
            release_root, manifest["core"]["path"], label="core.path"
        )
        _replace_tree(core_source, staged / "runtime/isekai")
        core_digest = _verified_tree_digest(
            staged / "runtime/isekai",
            manifest["core"]["digest"],
            label="Core",
            include_transients=True,
        )
        _write_launchers(staged)

        catalog_entry = stage_catalog(release_root, staged, manifest)

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
            _verified_tree_digest(
                staged / "foundations" / str(manifest["foundation"]["version"]),
                manifest["foundation"]["digest"],
                label="Foundation",
                include_transients=True,
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
            skill_source = _adapter_skill_source(adapter_source, runtime)
            installed_digest = _verified_tree_digest(
                skill_source,
                source_entry["digest"],
                label=f"{runtime} Runtime Skill",
            )
            adapter_entries[runtime] = {
                "version": str(source_entry["version"]),
                "path": WORKSPACE_ADAPTER_PATHS[runtime].as_posix(),
                "source_digest": source_entry["digest"],
                "digest": installed_digest,
            }

        rollback_entry: dict[str, str] | None = None
        if current_lock:
            rollback = staged / "rollback"
            _copy_managed_root(managed, rollback / "install")
            (rollback / LOCK_NAME).write_bytes(lock_before or b"")
            for runtime in sorted(current_adapters):
                if _workspace_adapter_owned(current_adapters, runtime):
                    _replace_tree(
                        workspace_targets[runtime],
                        rollback / "adapters" / runtime,
                    )
            if host_state:
                _write_json_atomic(rollback / "host-config.json", host_state)
            project_manifest = project_root / "project.json"
            if project_manifest.exists() or project_manifest.is_symlink():
                (rollback / "project.json").write_bytes(
                    _read_control_bytes(
                        project_manifest,
                        root=project_root,
                        label="project manifest",
                    )
                )
            rollback_entry = {
                "path": f"{MANAGED_ROOT}/rollback",
                "digest": tree_digest(rollback, include_transients=True),
            }

        lock = {
            "schema_version": LOCK_SCHEMA_VERSION,
            "release": manifest["version"],
            "protocol_version": manifest["protocol_version"],
            "source": {"git": source, "ref": ref, "commit": commit},
            "core": {
                "version": manifest["core"]["version"],
                "path": f"{MANAGED_ROOT}/runtime/isekai",
                "source_digest": manifest["core"]["digest"],
                "digest": core_digest,
            },
            "catalog": catalog_entry,
            "foundation": foundation_entry,
            "adapters": dict(sorted(adapter_entries.items())),
        }
        if any(
            _adapter_uses_managed_plugin(adapter_entries, runtime)
            for runtime in {"codex", "claude"}
        ):
            lock["marketplace"] = legacy_marketplace_name
        if rollback_entry is not None:
            lock["rollback"] = rollback_entry

        if managed.exists():
            managed.rename(backup)
        staged.rename(managed)

        for runtime in sorted(workspace_changes):
            target = workspace_targets[runtime]
            if target.exists():
                adapter_backup.mkdir(parents=True, exist_ok=True)
                target.rename(adapter_backup / runtime)
            if runtime in desired_workspace:
                source_entry = adapter_manifest[runtime]
                adapter_source = _component_root(
                    release_root,
                    source_entry["path"],
                    label=f"adapter:{runtime}.path",
                )
                source_skill = _adapter_skill_source(adapter_source, runtime)
                target.parent.mkdir(parents=True, exist_ok=True)
                _replace_tree(source_skill, target)

        if legacy_plugin_runtimes:
            _remove_legacy_project_plugin_declarations(
                project_root,
                legacy_marketplace_name,
                legacy_plugin_runtimes,
                current_adapters,
            )
            host_applied = True

        if adopt_foundation:
            project_before = _adopt_foundation(
                project_root, str(foundation_entry["path"])
            )
        _write_project_json(project_root, LOCK_NAME, lock)
        health = doctor_install(project_root)
        if not health["ready"]:
            raise DistributionError(
                "post-install verification failed: " + "; ".join(health["issues"])
            )
    except Exception:
        if host_applied:
            _restore_host_slots(
                project_root,
                host_state,
                legacy_marketplace_name,
            )
        if backup.exists():
            if managed.exists():
                shutil.rmtree(managed)
            backup.rename(managed)
        elif managed.exists() and not current_lock:
            shutil.rmtree(managed)
        for runtime in sorted(workspace_changes):
            target = workspace_targets[runtime]
            adapter_before = adapter_backup / runtime
            if adapter_before.exists():
                _remove_path(target)
                adapter_before.rename(target)
            elif (
                runtime not in current_workspace
                and (target.exists() or target.is_symlink())
            ):
                _remove_path(target)
        if project_before is not None:
            _restore_project_file(project_root, "project.json", project_before)
        if lock_before is not None:
            _restore_project_file(project_root, LOCK_NAME, lock_before)
        else:
            _restore_project_file(project_root, LOCK_NAME, None)
        raise
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)
        if backup.exists():
            shutil.rmtree(backup)
        if adapter_backup.exists():
            shutil.rmtree(adapter_backup)

    result = {
        "installed": True,
        "updated": current_lock is not None,
        "project": str(project_root),
        "release": manifest["version"],
        "commit": commit,
        "runtimes": installed_runtimes,
        "foundation": foundation_entry,
        "catalog": catalog_entry,
        "lock": str(project_root / LOCK_NAME),
        "host_registration_required": False,
        "new_conversation_required": bool(selected),
    }
    if not (project_root / "project.json").is_file():
        result["next_action"] = (
            f"{MANAGED_ROOT}/bin/isekai init --path . --foundation-path "
            f"{foundation_entry['path']}"
        )
    return result


def rollback_install(project: str | Path) -> dict[str, Any]:
    from .rollback import rollback_install as execute_rollback

    return execute_rollback(project)


def verify_adapter_handshake(
    runtime: str,
    adapter_version: str,
    protocol_version: str,
    project: str | Path = ".",
) -> dict[str, Any]:
    if not isinstance(runtime, str) or runtime not in RUNTIMES:
        raise DistributionError(f"unknown runtime: {runtime}")
    if not isinstance(adapter_version, str) or not adapter_version.strip():
        raise DistributionError("adapter version must be a non-empty string")
    if not isinstance(protocol_version, str) or not protocol_version.strip():
        raise DistributionError("protocol version must be a non-empty string")
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
        if (candidate / "project.json").is_file():
            # Stop at the nearest Project. An uninstalled Project must not
            # borrow an unrelated ancestor's lock and report itself as healthy.
            root = candidate
            break
    lock = load_install_lock(root)
    if lock is None:
        raise DistributionError(
            "project installation lock is missing; install ISEKAI or run doctor"
        )
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
    from .execution_profile import execution_profile_status

    execution_guard = execution_profile_status(root, runtime)
    if not execution_guard["ready"]:
        raise DistributionError(
            "Project execution guard is not ready: "
            + "; ".join(execution_guard["issues"])
            + "; run isekai doctor --path PROJECT --fix"
        )
    return {
        "compatible": True,
        "runtime": runtime,
        "adapter_version": adapter_version,
        "core_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "locked": True,
        "execution_guard": execution_guard,
    }
