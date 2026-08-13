from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, Required, TypedDict


JsonObject = dict[str, Any]


class InstallHealth(TypedDict, total=False):
    ready: Required[bool]
    project: str
    release: object
    protocol_version: object
    runtimes: list[str]
    issues: Required[list[str]]


class InstallResult(TypedDict, total=False):
    installed: bool
    updated: bool
    unchanged: bool
    project: str
    release: object
    commit: str
    runtimes: list[str]
    foundation: JsonObject
    catalog: JsonObject
    lock: str
    host_registration_required: bool
    new_conversation_required: bool
    next_action: str


@dataclass(frozen=True)
class InstallRequest:
    release_root: Path
    project_root: Path
    source: str
    ref: str
    commit: str
    runtimes: tuple[str, ...]
    update: bool
    include_foundation: bool
    adopt_foundation: bool


@dataclass(frozen=True)
class WorkspacePlan:
    manifest: JsonObject
    selected: tuple[str, ...]
    current_lock: JsonObject | None
    current_adapters: JsonObject
    current_workspace: frozenset[str]
    desired_workspace: frozenset[str]
    workspace_changes: frozenset[str]
    workspace_targets: dict[str, Path]
    legacy_plugin_runtimes: frozenset[str]
    legacy_marketplace_name: str
    host_state: JsonObject
    installed_runtimes: list[str]


@dataclass(frozen=True)
class StagedInstall:
    stage_root: Path
    staged: Path
    managed: Path
    backup: Path
    adapter_backup: Path
    lock_before: bytes | None
    lock: JsonObject
    foundation_entry: JsonObject
    catalog_entry: JsonObject


@dataclass(frozen=True)
class RollbackWorkspace:
    current: frozenset[str]
    previous: frozenset[str]
    targets: dict[str, Path]
    snapshots: dict[str, Path]


class RollbackOperations(Protocol):
    """Typed boundary used by rollback orchestration."""

    def load_lock(self, project_root: Path) -> JsonObject | None: ...

    def load_lock_path(self, path: Path) -> JsonObject: ...

    def doctor(self, project_root: Path) -> InstallHealth: ...

    def tree_digest(self, root: Path, *, include_transients: bool) -> str: ...

    def read_control_json(
        self, path: Path, *, root: Path, label: str
    ) -> JsonObject: ...

    def read_control_bytes(
        self, path: Path, *, root: Path, label: str
    ) -> bytes: ...

    def project_path(
        self, project_root: Path, relative: Path, *, label: str
    ) -> Path: ...

    def workspace_adapter_owned(self, adapters: object, runtime: str) -> bool: ...

    def capture_host_slots(
        self, project_root: Path, marketplace: str, runtimes: Iterable[str]
    ) -> JsonObject: ...

    def restore_host_slots(
        self, project_root: Path, state: JsonObject, marketplace: str
    ) -> None: ...

    def replace_tree(self, source: Path, target: Path) -> None: ...

    def copy_managed_root(self, source: Path, target: Path) -> None: ...

    def write_json_atomic(self, path: Path, value: JsonObject) -> None: ...

    def write_project_json(
        self, project_root: Path, relative: str, value: JsonObject
    ) -> None: ...

    def remove_path(self, path: Path) -> None: ...

    def restore_project_file(
        self, project_root: Path, relative: str, content: bytes | None
    ) -> None: ...


class InstallOperations(Protocol):
    """Typed boundary between installation orchestration and filesystem services."""

    def verify_release(self, release_root: Path) -> JsonObject: ...

    def normalize_runtimes(self, runtimes: Iterable[str]) -> tuple[str, ...]: ...

    def load_lock(self, project_root: Path) -> JsonObject | None: ...

    def read_control_json(
        self, path: Path, *, root: Path, label: str
    ) -> JsonObject: ...

    def read_control_bytes(
        self, path: Path, *, root: Path, label: str
    ) -> bytes: ...

    def project_path(
        self, project_root: Path, relative: Path, *, label: str
    ) -> Path: ...

    def workspace_adapter_owned(self, adapters: object, runtime: str) -> bool: ...

    def adapter_uses_managed_plugin(self, adapters: object, runtime: str) -> bool: ...

    def doctor(self, project_root: Path) -> InstallHealth: ...

    def capture_host_slots(
        self, project_root: Path, marketplace: str, runtimes: Iterable[str]
    ) -> JsonObject: ...

    def copy_managed_root(self, source: Path, target: Path) -> None: ...

    def remove_path(self, path: Path) -> None: ...

    def component_root(self, root: Path, value: object, *, label: str) -> Path: ...

    def replace_tree(self, source: Path, target: Path) -> None: ...

    def verified_tree_digest(
        self,
        root: Path,
        expected: object,
        *,
        label: str,
        include_transients: bool = False,
    ) -> str: ...

    def write_launchers(self, staged: Path) -> None: ...

    def stage_catalog(
        self, release_root: Path, staged: Path, manifest: JsonObject
    ) -> JsonObject: ...

    def current_foundation_matches(
        self, project_root: Path, version: str, digest: str
    ) -> bool: ...

    def adapter_skill_source(self, adapter_source: Path, runtime: str) -> Path: ...

    def write_json_atomic(self, path: Path, value: object) -> None: ...

    def tree_digest(self, root: Path, *, include_transients: bool) -> str: ...

    def remove_legacy_project_plugin_declarations(
        self,
        project_root: Path,
        marketplace: str,
        runtimes: Iterable[str],
        adapters: JsonObject,
    ) -> None: ...

    def restore_host_slots(
        self, project_root: Path, state: JsonObject, marketplace: str
    ) -> None: ...

    def adopt_foundation(self, project_root: Path, relative: str) -> bytes | None: ...

    def write_project_json(
        self, project_root: Path, relative: str, value: JsonObject
    ) -> None: ...

    def restore_project_file(
        self, project_root: Path, relative: str, content: bytes | None
    ) -> None: ...


__all__ = [
    "InstallHealth",
    "InstallOperations",
    "InstallRequest",
    "InstallResult",
    "JsonObject",
    "RollbackOperations",
    "RollbackWorkspace",
    "StagedInstall",
    "WorkspacePlan",
]
