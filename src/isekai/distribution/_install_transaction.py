from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterable

from ._install_contract import (
    InstallOperations,
    InstallRequest,
    InstallResult,
    JsonObject,
    StagedInstall,
    WorkspacePlan,
)
from .lockfile import WORKSPACE_ADAPTER_PATHS
from .marketplace import CLAUDE_PROJECT_SETTINGS, CODEX_REPO_MARKETPLACE
from .release import (
    LOCK_NAME,
    LOCK_SCHEMA_VERSION,
    MANAGED_ROOT,
    RUNTIMES,
    DistributionError,
)


def _validated_request(
    checkout: str | Path,
    project: str | Path,
    *,
    source: str,
    ref: str,
    commit: str,
    runtimes: Iterable[str],
    update: bool,
    include_foundation: bool,
    adopt_foundation: bool,
    operations: InstallOperations,
) -> InstallRequest:
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
    project_root = Path(project).expanduser().resolve()
    if not project_root.is_dir():
        raise DistributionError(f"project root does not exist: {project_root}")
    project_manifest = project_root / "project.json"
    if project_manifest.exists() or project_manifest.is_symlink():
        operations.read_control_json(
            project_manifest,
            root=project_root,
            label="project manifest",
        )
    return InstallRequest(
        release_root=Path(checkout).resolve(),
        project_root=project_root,
        source=source,
        ref=ref,
        commit=commit,
        runtimes=operations.normalize_runtimes(runtimes),
        update=update,
        include_foundation=include_foundation,
        adopt_foundation=adopt_foundation,
    )


def _workspace_plan(
    request: InstallRequest,
    operations: InstallOperations,
) -> WorkspacePlan:
    manifest = operations.verify_release(request.release_root)
    current_lock = operations.load_lock(request.project_root)
    if request.update and current_lock is None:
        raise DistributionError("cannot update before ISEKAI is installed")
    if current_lock is None and (request.project_root / MANAGED_ROOT).exists():
        raise DistributionError(
            f"refusing to replace unmanaged {MANAGED_ROOT}; move it aside or adopt it explicitly"
        )
    current_adapters = (
        dict(current_lock.get("adapters", {})) if current_lock else {}
    )
    current_workspace = frozenset(
        runtime
        for runtime in RUNTIMES
        if operations.workspace_adapter_owned(current_adapters, runtime)
    )
    desired_workspace = frozenset(request.runtimes)
    workspace_targets = _workspace_targets(
        request.project_root,
        current_workspace | desired_workspace,
        operations,
    )
    for runtime in desired_workspace:
        target = workspace_targets[runtime]
        if (
            (target.exists() or target.is_symlink())
            and not operations.workspace_adapter_owned(current_adapters, runtime)
        ):
            raise DistributionError(
                "refusing to replace an unmanaged "
                f"{WORKSPACE_ADAPTER_PATHS[runtime].as_posix()} directory"
            )
    legacy_plugin_runtimes = frozenset(
        runtime
        for runtime in {"codex", "claude"} & desired_workspace
        if operations.adapter_uses_managed_plugin(current_adapters, runtime)
    )
    _validate_legacy_host_paths(
        request.project_root, legacy_plugin_runtimes, operations
    )
    if current_lock is not None:
        health = operations.doctor(request.project_root)
        if not health["ready"]:
            raise DistributionError(
                "installed files were modified or are incomplete: "
                + "; ".join(health["issues"])
            )
    marketplace = (
        str(current_lock.get("marketplace"))
        if current_lock and current_lock.get("marketplace")
        else "isekai-project"
    )
    host_state = operations.capture_host_slots(
        request.project_root,
        marketplace,
        legacy_plugin_runtimes,
    )
    return WorkspacePlan(
        manifest=manifest,
        selected=request.runtimes,
        current_lock=current_lock,
        current_adapters=current_adapters,
        current_workspace=current_workspace,
        desired_workspace=desired_workspace,
        workspace_changes=frozenset(
            desired_workspace | (current_workspace & desired_workspace)
        ),
        workspace_targets=workspace_targets,
        legacy_plugin_runtimes=legacy_plugin_runtimes,
        legacy_marketplace_name=marketplace,
        host_state=host_state,
        installed_runtimes=sorted(set(current_adapters) | desired_workspace),
    )


def _workspace_targets(
    project_root: Path,
    runtimes: Iterable[str],
    operations: InstallOperations,
) -> dict[str, Path]:
    return {
        runtime: operations.project_path(
            project_root,
            WORKSPACE_ADAPTER_PATHS[runtime],
            label=f"adapter:{runtime}.path",
        )
        for runtime in sorted(runtimes)
    }


def _validate_legacy_host_paths(
    project_root: Path,
    runtimes: Iterable[str],
    operations: InstallOperations,
) -> None:
    selected = set(runtimes)
    for runtime, relative in {
        "codex": CODEX_REPO_MARKETPLACE,
        "claude": CLAUDE_PROJECT_SETTINGS,
    }.items():
        if runtime in selected:
            operations.project_path(
                project_root,
                relative,
                label=f"host:{runtime}.path",
            )


def _unchanged_result(
    request: InstallRequest,
    plan: WorkspacePlan,
) -> InstallResult | None:
    current = plan.current_lock
    layout_current = all(
        isinstance(plan.current_adapters.get(runtime), dict)
        and plan.current_adapters[runtime].get("path")
        == WORKSPACE_ADAPTER_PATHS[runtime].as_posix()
        for runtime in plan.selected
    )
    if not (
        current
        and current.get("release") == plan.manifest["version"]
        and current.get("source", {}).get("commit") == request.commit
        and set(plan.selected) <= set(plan.current_adapters)
        and layout_current
        and not request.include_foundation
        and not request.adopt_foundation
    ):
        return None
    return {
        "installed": False,
        "updated": False,
        "unchanged": True,
        "project": str(request.project_root),
        "release": plan.manifest["version"],
        "commit": request.commit,
        "runtimes": plan.installed_runtimes,
        "foundation": current["foundation"],
        "catalog": current["catalog"],
        "lock": str(request.project_root / LOCK_NAME),
        "host_registration_required": False,
        "new_conversation_required": False,
    }


def _stage_paths(request: InstallRequest) -> tuple[Path, Path, Path, Path, Path]:
    stage_root = Path(
        tempfile.mkdtemp(prefix=".isekai-stage-", dir=request.project_root)
    )
    return (
        stage_root,
        stage_root / MANAGED_ROOT,
        request.project_root / MANAGED_ROOT,
        request.project_root / f".{MANAGED_ROOT}-backup-{uuid.uuid4().hex}",
        request.project_root / f".isekai-adapter-backup-{uuid.uuid4().hex}",
    )


def _stage_core_and_foundation(
    request: InstallRequest,
    plan: WorkspacePlan,
    staged: Path,
    operations: InstallOperations,
) -> tuple[str, JsonObject, JsonObject]:
    managed = request.project_root / MANAGED_ROOT
    if managed.is_dir():
        operations.copy_managed_root(managed, staged)
    else:
        staged.mkdir(parents=True)
    for runtime in plan.legacy_plugin_runtimes:
        operations.remove_path(staged / "marketplaces" / runtime)
    marketplaces = staged / "marketplaces"
    if marketplaces.is_dir() and not any(marketplaces.iterdir()):
        marketplaces.rmdir()
    core_source = operations.component_root(
        request.release_root,
        plan.manifest["core"]["path"],
        label="core.path",
    )
    operations.replace_tree(core_source, staged / "runtime/isekai")
    core_digest = operations.verified_tree_digest(
        staged / "runtime/isekai",
        plan.manifest["core"]["digest"],
        label="Core",
        include_transients=True,
    )
    operations.write_launchers(staged)
    catalog = operations.stage_catalog(
        request.release_root, staged, plan.manifest
    )
    foundation = _stage_foundation(request, plan, staged, operations)
    return core_digest, catalog, foundation


def _stage_foundation(
    request: InstallRequest,
    plan: WorkspacePlan,
    staged: Path,
    operations: InstallOperations,
) -> JsonObject:
    if plan.current_lock and not request.include_foundation:
        foundation = dict(plan.current_lock["foundation"])
    else:
        source = operations.component_root(
            request.release_root,
            plan.manifest["foundation"]["path"],
            label="foundation.path",
        )
        version = str(plan.manifest["foundation"]["version"])
        operations.replace_tree(source, staged / "foundations" / version)
        operations.verified_tree_digest(
            staged / "foundations" / version,
            plan.manifest["foundation"]["digest"],
            label="Foundation",
            include_transients=True,
        )
        foundation = {
            "id": plan.manifest["foundation"]["id"],
            "version": plan.manifest["foundation"]["version"],
            "path": f"{MANAGED_ROOT}/foundations/{version}",
            "digest": plan.manifest["foundation"]["digest"],
            "source_release": plan.manifest["version"],
        }
    if not operations.current_foundation_matches(
        request.project_root,
        str(foundation["version"]),
        str(foundation["digest"]),
    ) and not request.adopt_foundation:
        raise DistributionError(
            "Project Foundation differs from the selected release; rerun with "
            "--adopt-foundation after reviewing the contract change"
        )
    return foundation


def _stage_adapter_entries(
    request: InstallRequest,
    plan: WorkspacePlan,
    operations: InstallOperations,
) -> tuple[JsonObject, JsonObject]:
    adapter_manifest = {item["id"]: item for item in plan.manifest["adapters"]}
    entries = dict(plan.current_adapters)
    for runtime in plan.selected:
        source_entry = adapter_manifest[runtime]
        adapter_source = operations.component_root(
            request.release_root,
            source_entry["path"],
            label=f"adapter:{runtime}.path",
        )
        skill_source = operations.adapter_skill_source(adapter_source, runtime)
        installed_digest = operations.verified_tree_digest(
            skill_source,
            source_entry["digest"],
            label=f"{runtime} Runtime Skill",
        )
        entries[runtime] = {
            "version": str(source_entry["version"]),
            "path": WORKSPACE_ADAPTER_PATHS[runtime].as_posix(),
            "source_digest": source_entry["digest"],
            "digest": installed_digest,
        }
    return entries, adapter_manifest


def _stage_rollback_snapshot(
    request: InstallRequest,
    plan: WorkspacePlan,
    staged: Path,
    managed: Path,
    lock_before: bytes | None,
    operations: InstallOperations,
) -> JsonObject | None:
    if plan.current_lock is None:
        return None
    rollback = staged / "rollback"
    operations.copy_managed_root(managed, rollback / "install")
    (rollback / LOCK_NAME).write_bytes(lock_before or b"")
    for runtime in sorted(plan.current_adapters):
        if operations.workspace_adapter_owned(plan.current_adapters, runtime):
            operations.replace_tree(
                plan.workspace_targets[runtime],
                rollback / "adapters" / runtime,
            )
    if plan.host_state:
        operations.write_json_atomic(rollback / "host-config.json", plan.host_state)
    project_manifest = request.project_root / "project.json"
    if project_manifest.exists() or project_manifest.is_symlink():
        (rollback / "project.json").write_bytes(
            operations.read_control_bytes(
                project_manifest,
                root=request.project_root,
                label="project manifest",
            )
        )
    return {
        "path": f"{MANAGED_ROOT}/rollback",
        "digest": operations.tree_digest(rollback, include_transients=True),
    }


def _build_lock(
    request: InstallRequest,
    plan: WorkspacePlan,
    *,
    core_digest: str,
    catalog: JsonObject,
    foundation: JsonObject,
    adapters: JsonObject,
    rollback: JsonObject | None,
    operations: InstallOperations,
) -> JsonObject:
    lock: JsonObject = {
        "schema_version": LOCK_SCHEMA_VERSION,
        "release": plan.manifest["version"],
        "protocol_version": plan.manifest["protocol_version"],
        "source": {
            "git": request.source,
            "ref": request.ref,
            "commit": request.commit,
        },
        "core": {
            "version": plan.manifest["core"]["version"],
            "path": f"{MANAGED_ROOT}/runtime/isekai",
            "source_digest": plan.manifest["core"]["digest"],
            "digest": core_digest,
        },
        "catalog": catalog,
        "foundation": foundation,
        "adapters": dict(sorted(adapters.items())),
    }
    if any(
        operations.adapter_uses_managed_plugin(adapters, runtime)
        for runtime in {"codex", "claude"}
    ):
        lock["marketplace"] = plan.legacy_marketplace_name
    if rollback is not None:
        lock["rollback"] = rollback
    return lock


def _stage_install(
    request: InstallRequest,
    plan: WorkspacePlan,
    paths: tuple[Path, Path, Path, Path, Path],
    operations: InstallOperations,
) -> tuple[StagedInstall, JsonObject]:
    stage_root, staged, managed, backup, adapter_backup = paths
    lock_before = (
        operations.read_control_bytes(
            request.project_root / LOCK_NAME,
            root=request.project_root,
            label=LOCK_NAME,
        )
        if plan.current_lock
        else None
    )
    core_digest, catalog, foundation = _stage_core_and_foundation(
        request, plan, staged, operations
    )
    adapters, adapter_manifest = _stage_adapter_entries(request, plan, operations)
    rollback = _stage_rollback_snapshot(
        request, plan, staged, managed, lock_before, operations
    )
    lock = _build_lock(
        request,
        plan,
        core_digest=core_digest,
        catalog=catalog,
        foundation=foundation,
        adapters=adapters,
        rollback=rollback,
        operations=operations,
    )
    return (
        StagedInstall(
            stage_root=stage_root,
            staged=staged,
            managed=managed,
            backup=backup,
            adapter_backup=adapter_backup,
            lock_before=lock_before,
            lock=lock,
            foundation_entry=foundation,
            catalog_entry=catalog,
        ),
        adapter_manifest,
    )


def _install_workspace_adapters(
    request: InstallRequest,
    plan: WorkspacePlan,
    staged: StagedInstall,
    adapter_manifest: JsonObject,
    operations: InstallOperations,
) -> None:
    for runtime in sorted(plan.workspace_changes):
        target = plan.workspace_targets[runtime]
        if target.exists():
            staged.adapter_backup.mkdir(parents=True, exist_ok=True)
            target.rename(staged.adapter_backup / runtime)
        if runtime in plan.desired_workspace:
            source_entry = adapter_manifest[runtime]
            adapter_source = operations.component_root(
                request.release_root,
                source_entry["path"],
                label=f"adapter:{runtime}.path",
            )
            source_skill = operations.adapter_skill_source(adapter_source, runtime)
            target.parent.mkdir(parents=True, exist_ok=True)
            operations.replace_tree(source_skill, target)


def _restore_failed_commit(
    request: InstallRequest,
    plan: WorkspacePlan,
    staged: StagedInstall,
    *,
    host_applied: bool,
    project_before: bytes | None,
    operations: InstallOperations,
) -> None:
    if host_applied:
        operations.restore_host_slots(
            request.project_root,
            plan.host_state,
            plan.legacy_marketplace_name,
        )
    if staged.backup.exists():
        if staged.managed.exists():
            shutil.rmtree(staged.managed)
        staged.backup.rename(staged.managed)
    elif staged.managed.exists() and not plan.current_lock:
        shutil.rmtree(staged.managed)
    for runtime in sorted(plan.workspace_changes):
        target = plan.workspace_targets[runtime]
        adapter_before = staged.adapter_backup / runtime
        if adapter_before.exists():
            operations.remove_path(target)
            adapter_before.rename(target)
        elif (
            runtime not in plan.current_workspace
            and (target.exists() or target.is_symlink())
        ):
            operations.remove_path(target)
    if project_before is not None:
        operations.restore_project_file(
            request.project_root, "project.json", project_before
        )
    operations.restore_project_file(
        request.project_root,
        LOCK_NAME,
        staged.lock_before,
    )


def _commit_install(
    request: InstallRequest,
    plan: WorkspacePlan,
    staged: StagedInstall,
    adapter_manifest: JsonObject,
    operations: InstallOperations,
) -> None:
    project_before: bytes | None = None
    host_applied = False
    try:
        if staged.managed.exists():
            staged.managed.rename(staged.backup)
        staged.staged.rename(staged.managed)
        _install_workspace_adapters(
            request, plan, staged, adapter_manifest, operations
        )
        if plan.legacy_plugin_runtimes:
            operations.remove_legacy_project_plugin_declarations(
                request.project_root,
                plan.legacy_marketplace_name,
                plan.legacy_plugin_runtimes,
                plan.current_adapters,
            )
            host_applied = True
        if request.adopt_foundation:
            project_before = operations.adopt_foundation(
                request.project_root,
                str(staged.foundation_entry["path"]),
            )
        operations.write_project_json(
            request.project_root, LOCK_NAME, staged.lock
        )
        health = operations.doctor(request.project_root)
        if not health["ready"]:
            raise DistributionError(
                "post-install verification failed: "
                + "; ".join(health["issues"])
            )
    except Exception:
        _restore_failed_commit(
            request,
            plan,
            staged,
            host_applied=host_applied,
            project_before=project_before,
            operations=operations,
        )
        raise


def _cleanup(staged: StagedInstall) -> None:
    for path in (staged.stage_root, staged.backup, staged.adapter_backup):
        if path.exists():
            shutil.rmtree(path)


def _installed_result(
    request: InstallRequest,
    plan: WorkspacePlan,
    staged: StagedInstall,
) -> InstallResult:
    result: InstallResult = {
        "installed": True,
        "updated": plan.current_lock is not None,
        "project": str(request.project_root),
        "release": plan.manifest["version"],
        "commit": request.commit,
        "runtimes": plan.installed_runtimes,
        "foundation": staged.foundation_entry,
        "catalog": staged.catalog_entry,
        "lock": str(request.project_root / LOCK_NAME),
        "host_registration_required": False,
        "new_conversation_required": bool(plan.selected),
    }
    if not (request.project_root / "project.json").is_file():
        result["next_action"] = (
            f"{MANAGED_ROOT}/bin/isekai init --path . --foundation-path "
            f"{staged.foundation_entry['path']}"
        )
    return result


def execute_install_transaction(
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
    operations: InstallOperations,
) -> InstallResult:
    request = _validated_request(
        checkout,
        project,
        source=source,
        ref=ref,
        commit=commit,
        runtimes=runtimes,
        update=update,
        include_foundation=include_foundation,
        adopt_foundation=adopt_foundation,
        operations=operations,
    )
    plan = _workspace_plan(request, operations)
    unchanged = _unchanged_result(request, plan)
    if unchanged is not None:
        return unchanged
    paths = _stage_paths(request)
    staged: StagedInstall | None = None
    try:
        staged, adapter_manifest = _stage_install(
            request, plan, paths, operations
        )
        _commit_install(request, plan, staged, adapter_manifest, operations)
        return _installed_result(request, plan, staged)
    finally:
        if staged is not None:
            _cleanup(staged)
        else:
            stage_root, _staged, _managed, backup, adapter_backup = paths
            for path in (stage_root, backup, adapter_backup):
                if path.exists():
                    shutil.rmtree(path)


__all__ = ["execute_install_transaction"]
