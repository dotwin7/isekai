from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ..support.locking import LockUnavailable, rooted_file_lock
from . import install as install_module
from .marketplace import (
    CLAUDE_PROJECT_SETTINGS,
    CODEX_REPO_MARKETPLACE,
    _capture_host_slots,
    _restore_host_slots,
)
from .release import LOCK_NAME, MANAGED_ROOT, RUNTIMES, DistributionError


def _foundation_identity(lock: dict[str, Any]) -> tuple[object, object]:
    foundation = lock.get("foundation")
    if not isinstance(foundation, dict):
        return None, None
    return foundation.get("version"), foundation.get("digest")


def _rollback_project_manifest(
    current_bytes: bytes | None,
    previous_bytes: bytes | None,
    current_lock: dict[str, Any],
    previous_lock: dict[str, Any],
) -> dict[str, Any] | None:
    """Return a minimally rebound Project manifest, or ``None`` for no write.

    Project manifests are user-owned. Rollback must preserve a manifest created
    or edited after the update and only adjust the Foundation binding when the
    installed Foundation contract itself moves back.
    """

    if current_bytes is None or _foundation_identity(current_lock) == _foundation_identity(
        previous_lock
    ):
        return None
    try:
        current_manifest = json.loads(current_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError(f"project manifest is invalid during rollback: {exc}") from exc
    if not isinstance(current_manifest, dict):
        raise DistributionError("project manifest must be an object during rollback")

    previous_foundation_path: object = None
    if previous_bytes is not None:
        try:
            previous_manifest = json.loads(previous_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DistributionError(
                f"rollback project manifest is invalid: {exc}"
            ) from exc
        if not isinstance(previous_manifest, dict):
            raise DistributionError("rollback project manifest must be an object")
        previous_foundation_path = previous_manifest.get("foundation_path")
    if not isinstance(previous_foundation_path, str) or not previous_foundation_path.strip():
        previous_foundation = previous_lock.get("foundation")
        if isinstance(previous_foundation, dict):
            previous_foundation_path = previous_foundation.get("path")
    if not isinstance(previous_foundation_path, str) or not previous_foundation_path.strip():
        raise DistributionError(
            "previous installation has no Foundation path for Project rollback"
        )

    rebound = dict(current_manifest)
    rebound["foundation_path"] = previous_foundation_path
    return rebound


def rollback_install(project: str | Path) -> dict[str, Any]:
    """Serialize rollback mutations for one Project."""
    project_root = Path(project).expanduser().resolve()
    if not project_root.is_dir():
        raise DistributionError(f"project root does not exist: {project_root}")
    try:
        with rooted_file_lock(
            project_root,
            install_module.INSTALL_LOCK_NAME,
            subject=f"ISEKAI installation for {project_root}",
        ):
            return _rollback_install_locked(project_root)
    except LockUnavailable as exc:
        raise DistributionError(str(exc)) from exc


def _rollback_install_locked(project: str | Path) -> dict[str, Any]:
    project_root = Path(project).expanduser().resolve()
    current = install_module.load_install_lock(project_root)
    if current is None:
        raise DistributionError("cannot roll back before ISEKAI is installed")
    rollback_entry = current.get("rollback")
    if not isinstance(rollback_entry, dict):
        raise DistributionError(
            "cannot roll back an installation whose snapshot is not integrity-bound"
        )
    health = install_module.doctor_install(project_root)
    if not health["ready"]:
        raise DistributionError("cannot roll back a modified installation")
    managed = project_root / MANAGED_ROOT
    rollback = managed / "rollback"
    previous_install = rollback / "install"
    previous_lock_path = rollback / LOCK_NAME
    if not previous_install.is_dir() or not previous_lock_path.is_file():
        raise DistributionError("no previous ISEKAI installation is available")
    stage_root = Path(
        tempfile.mkdtemp(prefix=".isekai-rollback-stage-", dir=project_root)
    )
    try:
        verified_rollback = stage_root / "verified-rollback"
        shutil.copytree(rollback, verified_rollback, symlinks=True)
        actual_digest = install_module.tree_digest(
            verified_rollback,
            include_transients=True,
        )
        if actual_digest != rollback_entry.get("digest"):
            raise DistributionError("rollback snapshot digest mismatch")
        return _restore_verified_snapshot(
            project_root,
            current,
            managed,
            verified_rollback,
            stage_root,
        )
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def _restore_verified_snapshot(
    project_root: Path,
    current: dict[str, Any],
    managed: Path,
    rollback: Path,
    stage_root: Path,
) -> dict[str, Any]:
    previous_install = rollback / "install"
    previous_lock_path = rollback / LOCK_NAME
    previous_lock = install_module._load_install_lock_path(previous_lock_path)

    current_marketplace = str(current.get("marketplace") or "isekai-project")
    host_state_path = rollback / "host-config.json"
    host_restore_state = (
        install_module._read_control_json(
            host_state_path,
            root=rollback,
            label="rollback host configuration",
        )
        if host_state_path.exists() or host_state_path.is_symlink()
        else {}
    )
    host_runtimes = set(host_restore_state) & {"codex", "claude"}
    for runtime, relative in {
        "codex": CODEX_REPO_MARKETPLACE,
        "claude": CLAUDE_PROJECT_SETTINGS,
    }.items():
        if runtime in host_runtimes:
            install_module._project_path_without_symlinks(
                project_root,
                relative,
                label=f"host:{runtime}.path",
            )
    redo_host_state = _capture_host_slots(
        project_root,
        current_marketplace,
        host_runtimes,
    )

    current_adapters = current.get("adapters", {})
    previous_adapters = previous_lock.get("adapters", {})
    current_workspace = {
        runtime
        for runtime in RUNTIMES
        if install_module._workspace_adapter_owned(current_adapters, runtime)
    }
    previous_workspace = {
        runtime
        for runtime in RUNTIMES
        if install_module._workspace_adapter_owned(previous_adapters, runtime)
    }
    adapter_targets: dict[str, Path] = {}
    previous_snapshots: dict[str, Path] = {}
    for runtime in sorted(current_workspace | previous_workspace):
        relative = install_module.WORKSPACE_ADAPTER_PATHS[runtime]
        adapter_targets[runtime] = install_module._project_path_without_symlinks(
            project_root,
            relative,
            label=f"adapter:{runtime}.path",
        )
        snapshot = rollback / "adapters" / runtime
        if runtime == "kiro" and not snapshot.is_dir():
            snapshot = rollback / "kiro"
        previous_snapshots[runtime] = snapshot
        if runtime in previous_workspace and not snapshot.is_dir():
            raise DistributionError(
                f"previous {runtime} workspace Adapter snapshot is missing"
            )
        target = adapter_targets[runtime]
        if (
            runtime in previous_workspace
            and runtime not in current_workspace
            and (target.exists() or target.is_symlink())
        ):
            raise DistributionError(
                f"refusing to replace an unmanaged {relative.as_posix()} directory"
            )

    staged = stage_root / MANAGED_ROOT
    previous_adapter_copies = stage_root / "previous-adapters"
    previous_project_bytes = (
        install_module._read_control_bytes(
            rollback / "project.json",
            root=rollback,
            label="rollback project manifest",
        )
        if (rollback / "project.json").exists()
        or (rollback / "project.json").is_symlink()
        else None
    )
    backup = project_root / f".{MANAGED_ROOT}-backup-{uuid.uuid4().hex}"
    adapter_backup = project_root / f".isekai-adapter-backup-{uuid.uuid4().hex}"
    project_manifest = project_root / "project.json"
    current_project_bytes = (
        install_module._read_control_bytes(
            project_manifest,
            root=project_root,
            label="project manifest",
        )
        if project_manifest.exists() or project_manifest.is_symlink()
        else None
    )
    rebound_project = _rollback_project_manifest(
        current_project_bytes,
        previous_project_bytes,
        current,
        previous_lock,
    )
    lock_path = project_root / LOCK_NAME
    current_lock_bytes = install_module._read_control_bytes(
        lock_path,
        root=project_root,
        label=LOCK_NAME,
    )
    host_restored = False

    try:
        for runtime in sorted(previous_workspace):
            install_module._replace_tree(
                previous_snapshots[runtime],
                previous_adapter_copies / runtime,
            )
        shutil.copytree(previous_install, staged, symlinks=True)
        redo = staged / "rollback"
        install_module._copy_managed_root(managed, redo / "install")
        install_module._write_json_atomic(redo / LOCK_NAME, current)
        for runtime in sorted(current_workspace):
            install_module._replace_tree(
                adapter_targets[runtime],
                redo / "adapters" / runtime,
            )
        if redo_host_state:
            install_module._write_json_atomic(
                redo / "host-config.json", redo_host_state
            )
        if current_project_bytes is not None:
            (redo / "project.json").write_bytes(current_project_bytes)
        previous_lock = dict(previous_lock)
        previous_lock["rollback"] = {
            "path": f"{MANAGED_ROOT}/rollback",
            "digest": install_module.tree_digest(
                redo,
                include_transients=True,
            ),
        }

        managed.rename(backup)
        staged.rename(managed)
        for runtime in sorted(current_workspace | previous_workspace):
            target = adapter_targets[runtime]
            if runtime in current_workspace:
                adapter_backup.mkdir(parents=True, exist_ok=True)
                target.rename(adapter_backup / runtime)
            previous_copy = previous_adapter_copies / runtime
            if previous_copy.is_dir():
                target.parent.mkdir(parents=True, exist_ok=True)
                install_module._replace_tree(previous_copy, target)
        if host_restore_state:
            _restore_host_slots(
                project_root,
                host_restore_state,
                current_marketplace,
            )
            host_restored = True
        if rebound_project is not None:
            install_module._write_project_json(
                project_root,
                "project.json",
                rebound_project,
            )
        install_module._write_project_json(project_root, LOCK_NAME, previous_lock)
        postflight = install_module.doctor_install(project_root)
        if not postflight["ready"]:
            raise DistributionError(
                "rollback verification failed: " + "; ".join(postflight["issues"])
            )
    except Exception:
        if host_restored:
            _restore_host_slots(project_root, redo_host_state, current_marketplace)
        if backup.exists():
            if managed.exists():
                shutil.rmtree(managed)
            backup.rename(managed)
        for runtime in sorted(current_workspace | previous_workspace):
            target = adapter_targets[runtime]
            adapter_before = adapter_backup / runtime
            if adapter_before.exists():
                install_module._remove_path(target)
                adapter_before.rename(target)
            elif (
                runtime not in current_workspace
                and (previous_adapter_copies / runtime).is_dir()
                and (target.exists() or target.is_symlink())
            ):
                install_module._remove_path(target)
        if current_project_bytes is None:
            install_module._restore_project_file(
                project_root,
                "project.json",
                None,
            )
        else:
            install_module._restore_project_file(
                project_root,
                "project.json",
                current_project_bytes,
            )
        install_module._restore_project_file(
            project_root,
            LOCK_NAME,
            current_lock_bytes,
        )
        raise
    finally:
        if backup.exists():
            shutil.rmtree(backup)
        if adapter_backup.exists():
            shutil.rmtree(adapter_backup)

    runtimes = sorted(previous_lock.get("adapters", {}))
    return {
        "rolled_back": True,
        "project": str(project_root),
        "release": previous_lock.get("release"),
        "runtimes": runtimes,
        "host_registration_required": False,
        "new_conversation_required": bool(runtimes),
    }
