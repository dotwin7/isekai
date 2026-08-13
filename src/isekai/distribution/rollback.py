from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..support.locking import LockUnavailable, rooted_file_lock
from ._install_contract import (
    JsonObject,
    RollbackOperations,
    RollbackWorkspace,
)
from .install import build_rollback_operations
from .lockfile import INSTALL_LOCK_NAME, WORKSPACE_ADAPTER_PATHS
from .marketplace import CLAUDE_PROJECT_SETTINGS, CODEX_REPO_MARKETPLACE
from .release import LOCK_NAME, MANAGED_ROOT, RUNTIMES, DistributionError


@dataclass(frozen=True)
class _RestoreContext:
    project_root: Path
    current: JsonObject
    managed: Path
    rollback: Path
    stage_root: Path
    previous_install: Path
    previous_lock: JsonObject
    marketplace: str
    host_restore_state: JsonObject
    redo_host_state: JsonObject
    workspace: RollbackWorkspace
    staged: Path
    previous_adapter_copies: Path
    backup: Path
    adapter_backup: Path
    current_project_bytes: bytes | None
    rebound_project: JsonObject | None
    current_lock_bytes: bytes


@dataclass
class _RollbackProgress:
    host_restored: bool = False


def _foundation_identity(lock: JsonObject) -> tuple[object, object]:
    foundation = lock.get("foundation")
    if not isinstance(foundation, dict):
        return None, None
    return foundation.get("version"), foundation.get("digest")


def _rollback_project_manifest(
    current_bytes: bytes | None,
    previous_bytes: bytes | None,
    current_lock: JsonObject,
    previous_lock: JsonObject,
) -> JsonObject | None:
    """Return a minimally rebound Project manifest, or ``None`` for no write."""
    if current_bytes is None or _foundation_identity(
        current_lock
    ) == _foundation_identity(previous_lock):
        return None
    try:
        current_manifest = json.loads(current_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError(
            f"project manifest is invalid during rollback: {exc}"
        ) from exc
    if not isinstance(current_manifest, dict):
        raise DistributionError("project manifest must be an object during rollback")
    previous_foundation_path = _previous_foundation_path(
        previous_bytes, previous_lock
    )
    rebound = dict(current_manifest)
    rebound["foundation_path"] = previous_foundation_path
    return rebound


def _previous_foundation_path(
    previous_bytes: bytes | None, previous_lock: JsonObject
) -> str:
    value: object = None
    if previous_bytes is not None:
        try:
            previous_manifest = json.loads(previous_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DistributionError(
                f"rollback project manifest is invalid: {exc}"
            ) from exc
        if not isinstance(previous_manifest, dict):
            raise DistributionError("rollback project manifest must be an object")
        value = previous_manifest.get("foundation_path")
    if not isinstance(value, str) or not value.strip():
        foundation = previous_lock.get("foundation")
        if isinstance(foundation, dict):
            value = foundation.get("path")
    if not isinstance(value, str) or not value.strip():
        raise DistributionError(
            "previous installation has no Foundation path for Project rollback"
        )
    return value


def rollback_install(project: str | Path) -> dict[str, Any]:
    """Serialize rollback mutations for one Project."""
    project_root = Path(project).expanduser().resolve()
    if not project_root.is_dir():
        raise DistributionError(f"project root does not exist: {project_root}")
    operations = build_rollback_operations()
    try:
        with rooted_file_lock(
            project_root,
            INSTALL_LOCK_NAME,
            subject=f"ISEKAI installation for {project_root}",
        ):
            return _rollback_install_locked(project_root, operations)
    except LockUnavailable as exc:
        raise DistributionError(str(exc)) from exc


def _rollback_install_locked(
    project_root: Path, operations: RollbackOperations
) -> dict[str, Any]:
    current = operations.load_lock(project_root)
    if current is None:
        raise DistributionError("cannot roll back before ISEKAI is installed")
    rollback_entry = current.get("rollback")
    if not isinstance(rollback_entry, dict):
        raise DistributionError(
            "cannot roll back an installation whose snapshot is not integrity-bound"
        )
    health = operations.doctor(project_root)
    if not health["ready"]:
        raise DistributionError("cannot roll back a modified installation")
    managed = project_root / MANAGED_ROOT
    rollback = managed / "rollback"
    if not (rollback / "install").is_dir() or not (rollback / LOCK_NAME).is_file():
        raise DistributionError("no previous ISEKAI installation is available")
    stage_root = Path(
        tempfile.mkdtemp(prefix=".isekai-rollback-stage-", dir=project_root)
    )
    try:
        verified = stage_root / "verified-rollback"
        shutil.copytree(rollback, verified, symlinks=True)
        actual = operations.tree_digest(verified, include_transients=True)
        if actual != rollback_entry.get("digest"):
            raise DistributionError("rollback snapshot digest mismatch")
        return _restore_verified_snapshot(
            project_root, current, managed, verified, stage_root, operations
        )
    finally:
        if stage_root.exists():
            shutil.rmtree(stage_root)


def _host_restore_state(
    project_root: Path,
    rollback: Path,
    marketplace: str,
    operations: RollbackOperations,
) -> tuple[JsonObject, JsonObject]:
    state_path = rollback / "host-config.json"
    state = (
        operations.read_control_json(
            state_path,
            root=rollback,
            label="rollback host configuration",
        )
        if state_path.exists() or state_path.is_symlink()
        else {}
    )
    runtimes = set(state) & {"codex", "claude"}
    for runtime, relative in {
        "codex": CODEX_REPO_MARKETPLACE,
        "claude": CLAUDE_PROJECT_SETTINGS,
    }.items():
        if runtime in runtimes:
            operations.project_path(
                project_root,
                relative,
                label=f"host:{runtime}.path",
            )
    redo = operations.capture_host_slots(project_root, marketplace, runtimes)
    return state, redo


def _rollback_workspace(
    project_root: Path,
    rollback: Path,
    current: JsonObject,
    previous: JsonObject,
    operations: RollbackOperations,
) -> RollbackWorkspace:
    current_adapters = current.get("adapters", {})
    previous_adapters = previous.get("adapters", {})
    current_set = frozenset(
        runtime
        for runtime in RUNTIMES
        if operations.workspace_adapter_owned(current_adapters, runtime)
    )
    previous_set = frozenset(
        runtime
        for runtime in RUNTIMES
        if operations.workspace_adapter_owned(previous_adapters, runtime)
    )
    targets: dict[str, Path] = {}
    snapshots: dict[str, Path] = {}
    for runtime in sorted(current_set | previous_set):
        relative = WORKSPACE_ADAPTER_PATHS[runtime]
        targets[runtime] = operations.project_path(
            project_root,
            relative,
            label=f"adapter:{runtime}.path",
        )
        snapshot = rollback / "adapters" / runtime
        if runtime == "kiro" and not snapshot.is_dir():
            snapshot = rollback / "kiro"
        snapshots[runtime] = snapshot
        if runtime in previous_set and not snapshot.is_dir():
            raise DistributionError(
                f"previous {runtime} workspace Adapter snapshot is missing"
            )
        if (
            runtime in previous_set
            and runtime not in current_set
            and (targets[runtime].exists() or targets[runtime].is_symlink())
        ):
            raise DistributionError(
                f"refusing to replace an unmanaged {relative.as_posix()} directory"
            )
    return RollbackWorkspace(current_set, previous_set, targets, snapshots)


def _optional_control_bytes(
    path: Path,
    *,
    root: Path,
    label: str,
    operations: RollbackOperations,
) -> bytes | None:
    if not path.exists() and not path.is_symlink():
        return None
    return operations.read_control_bytes(path, root=root, label=label)


def _restore_context(
    project_root: Path,
    current: JsonObject,
    managed: Path,
    rollback: Path,
    stage_root: Path,
    operations: RollbackOperations,
) -> _RestoreContext:
    previous_lock = operations.load_lock_path(rollback / LOCK_NAME)
    marketplace = str(current.get("marketplace") or "isekai-project")
    host_state, redo_host_state = _host_restore_state(
        project_root, rollback, marketplace, operations
    )
    workspace = _rollback_workspace(
        project_root, rollback, current, previous_lock, operations
    )
    previous_project_bytes = _optional_control_bytes(
        rollback / "project.json",
        root=rollback,
        label="rollback project manifest",
        operations=operations,
    )
    current_project_bytes = _optional_control_bytes(
        project_root / "project.json",
        root=project_root,
        label="project manifest",
        operations=operations,
    )
    rebound = _rollback_project_manifest(
        current_project_bytes,
        previous_project_bytes,
        current,
        previous_lock,
    )
    return _RestoreContext(
        project_root=project_root,
        current=current,
        managed=managed,
        rollback=rollback,
        stage_root=stage_root,
        previous_install=rollback / "install",
        previous_lock=previous_lock,
        marketplace=marketplace,
        host_restore_state=host_state,
        redo_host_state=redo_host_state,
        workspace=workspace,
        staged=stage_root / MANAGED_ROOT,
        previous_adapter_copies=stage_root / "previous-adapters",
        backup=project_root / f".{MANAGED_ROOT}-backup-{uuid.uuid4().hex}",
        adapter_backup=project_root
        / f".isekai-adapter-backup-{uuid.uuid4().hex}",
        current_project_bytes=current_project_bytes,
        rebound_project=rebound,
        current_lock_bytes=operations.read_control_bytes(
            project_root / LOCK_NAME,
            root=project_root,
            label=LOCK_NAME,
        ),
    )


def _stage_restore(
    context: _RestoreContext, operations: RollbackOperations
) -> Path:
    workspace = context.workspace
    for runtime in sorted(workspace.previous):
        operations.replace_tree(
            workspace.snapshots[runtime],
            context.previous_adapter_copies / runtime,
        )
    shutil.copytree(context.previous_install, context.staged, symlinks=True)
    redo = context.staged / "rollback"
    operations.copy_managed_root(context.managed, redo / "install")
    operations.write_json_atomic(redo / LOCK_NAME, context.current)
    for runtime in sorted(workspace.current):
        operations.replace_tree(
            workspace.targets[runtime], redo / "adapters" / runtime
        )
    if context.redo_host_state:
        operations.write_json_atomic(
            redo / "host-config.json", context.redo_host_state
        )
    if context.current_project_bytes is not None:
        (redo / "project.json").write_bytes(context.current_project_bytes)
    context.previous_lock["rollback"] = {
        "path": f"{MANAGED_ROOT}/rollback",
        "digest": operations.tree_digest(redo, include_transients=True),
    }
    return redo


def _commit_restore(
    context: _RestoreContext,
    operations: RollbackOperations,
    progress: _RollbackProgress,
) -> None:
    workspace = context.workspace
    context.managed.rename(context.backup)
    context.staged.rename(context.managed)
    for runtime in sorted(workspace.current | workspace.previous):
        target = workspace.targets[runtime]
        if runtime in workspace.current:
            context.adapter_backup.mkdir(parents=True, exist_ok=True)
            target.rename(context.adapter_backup / runtime)
        previous_copy = context.previous_adapter_copies / runtime
        if previous_copy.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            operations.replace_tree(previous_copy, target)
    if context.host_restore_state:
        operations.restore_host_slots(
            context.project_root,
            context.host_restore_state,
            context.marketplace,
        )
        progress.host_restored = True
    if context.rebound_project is not None:
        operations.write_project_json(
            context.project_root,
            "project.json",
            context.rebound_project,
        )
    operations.write_project_json(
        context.project_root, LOCK_NAME, context.previous_lock
    )
    postflight = operations.doctor(context.project_root)
    if not postflight["ready"]:
        raise DistributionError(
            "rollback verification failed: " + "; ".join(postflight["issues"])
        )


def _restore_failed_rollback(
    context: _RestoreContext,
    operations: RollbackOperations,
    progress: _RollbackProgress,
) -> None:
    if progress.host_restored:
        operations.restore_host_slots(
            context.project_root,
            context.redo_host_state,
            context.marketplace,
        )
    if context.backup.exists():
        if context.managed.exists():
            shutil.rmtree(context.managed)
        context.backup.rename(context.managed)
    workspace = context.workspace
    for runtime in sorted(workspace.current | workspace.previous):
        target = workspace.targets[runtime]
        adapter_before = context.adapter_backup / runtime
        if adapter_before.exists():
            operations.remove_path(target)
            adapter_before.rename(target)
        elif (
            runtime not in workspace.current
            and (context.previous_adapter_copies / runtime).is_dir()
            and (target.exists() or target.is_symlink())
        ):
            operations.remove_path(target)
    operations.restore_project_file(
        context.project_root,
        "project.json",
        context.current_project_bytes,
    )
    operations.restore_project_file(
        context.project_root,
        LOCK_NAME,
        context.current_lock_bytes,
    )


def _restore_verified_snapshot(
    project_root: Path,
    current: JsonObject,
    managed: Path,
    rollback: Path,
    stage_root: Path,
    operations: RollbackOperations,
) -> dict[str, Any]:
    context = _restore_context(
        project_root, current, managed, rollback, stage_root, operations
    )
    progress = _RollbackProgress()
    try:
        _stage_restore(context, operations)
        _commit_restore(context, operations, progress)
    except Exception:
        _restore_failed_rollback(context, operations, progress)
        raise
    finally:
        for path in (context.backup, context.adapter_backup):
            if path.exists():
                shutil.rmtree(path)
    runtimes = sorted(context.previous_lock.get("adapters", {}))
    return {
        "rolled_back": True,
        "project": str(project_root),
        "release": context.previous_lock.get("release"),
        "runtimes": runtimes,
        "host_registration_required": False,
        "new_conversation_required": bool(runtimes),
    }
