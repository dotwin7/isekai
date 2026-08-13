from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .. import __version__
from ..foundation import FoundationError, load_foundation
from ..support.locking import LockUnavailable, rooted_file_lock
from ._install_contract import (
    InstallHealth,
    InstallOperations,
    JsonObject,
    RollbackOperations,
)
from ._install_transaction import execute_install_transaction
from .catalog import stage_catalog
from .lockfile import (
    INSTALL_LOCK_NAME,
    WORKSPACE_ADAPTER_PATHS,
    installed_path as _installed_path,
    load_install_lock_path as _load_install_lock_path,
    project_path_without_symlinks as _project_path_without_symlinks,
    workspace_adapter_owned as _workspace_adapter_owned,
    load_install_lock,
)
from .marketplace import (
    adapter_uses_managed_plugin as _adapter_uses_managed_plugin,
    capture_host_slots as _capture_host_slots,
    copy_managed_root as _copy_managed_root,
    managed_control_issues,
    remove_legacy_project_plugin_declarations as _remove_legacy_project_plugin_declarations,
    replace_tree as _replace_tree,
    restore_host_slots as _restore_host_slots,
    write_launchers as _write_launchers,
)
from .project_io import (
    adapter_skill_source as _adapter_skill_source,
    remove_path as _remove_path,
    restore_project_file as _restore_project_file,
    write_project_json as _write_project_json,
)
from .release import (
    LOCK_NAME,
    PROTOCOL_VERSION,
    RUNTIMES,
    DistributionError,
    component_root as _component_root,
    normalize_runtimes as _normalize_runtimes,
    read_control_bytes as _read_control_bytes,
    read_control_json as _read_control_json,
    verified_tree_digest as _verified_tree_digest,
    verify_or_raise as _verify_or_raise,
    write_json_atomic as _write_json_atomic,
    tree_digest,
)


def doctor_install(project: str | Path) -> InstallHealth:
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
        return {
            "ready": False,
            "project": str(project_root),
            "issues": [f"missing {LOCK_NAME}"],
        }
    issues: list[str] = []
    if lock.get("protocol_version") != PROTOCOL_VERSION:
        issues.append("installed protocol_version is not supported by this Core")
    components = _installed_components(lock, issues)
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
    issues.extend(managed_control_issues(project_root, lock))
    _check_installed_foundation(project_root, lock, issues)
    adapters = lock.get("adapters")
    adapter_names = sorted(adapters) if isinstance(adapters, dict) else []
    issues = list(dict.fromkeys(issues))
    return {
        "ready": not issues,
        "project": str(project_root),
        "release": lock.get("release"),
        "protocol_version": lock.get("protocol_version"),
        "runtimes": adapter_names,
        "issues": issues,
    }


def _installed_components(
    lock: JsonObject, issues: list[str]
) -> list[tuple[str, JsonObject]]:
    components: list[tuple[str, JsonObject]] = []
    for label in ("core", "foundation"):
        entry = lock.get(label)
        if isinstance(entry, dict):
            components.append((label, entry))
        else:
            issues.append(f"lock is missing {label}")
    catalog = lock.get("catalog")
    if isinstance(catalog, dict):
        components.append(("catalog", catalog))
    elif lock.get("release") == __version__:
        issues.append("lock is missing catalog")
    adapters = lock.get("adapters")
    if not isinstance(adapters, dict):
        issues.append("lock adapters must be an object")
        adapters = {}
    for runtime, entry in sorted(adapters.items()):
        if runtime not in RUNTIMES:
            issues.append(f"unknown adapter in lock: {runtime}")
        elif isinstance(entry, dict):
            components.append((f"adapter:{runtime}", entry))
            if "workspace_path" in entry or "workspace_digest" in entry:
                components.append(
                    (
                        f"adapter:{runtime}.workspace",
                        {
                            "path": entry.get("workspace_path"),
                            "digest": entry.get("workspace_digest"),
                        },
                    )
                )
        else:
            issues.append(f"adapter lock is invalid: {runtime}")
    rollback = lock.get("rollback")
    if isinstance(rollback, dict):
        components.append(("rollback", rollback))
    return components


def _check_installed_foundation(
    project_root: Path, lock: JsonObject, issues: list[str]
) -> None:
    foundation_entry = lock.get("foundation")
    if not isinstance(foundation_entry, dict):
        return
    try:
        installed = load_foundation(
            _installed_path(
                project_root,
                foundation_entry.get("path"),
                label="foundation.path",
            )
        )
        if installed.version != foundation_entry.get("version"):
            issues.append("installed Foundation version does not match lock")
    except (DistributionError, FoundationError) as exc:
        issues.append(str(exc))
    project_manifest = project_root / "project.json"
    if not (project_manifest.exists() or project_manifest.is_symlink()):
        return
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


class _InstallOperationsAdapter(InstallOperations):
    """Bind the typed transaction contract to the current module seams."""

    def verify_release(self, release_root: Path) -> JsonObject:
        return _verify_or_raise(release_root)

    def normalize_runtimes(self, runtimes: Iterable[str]) -> tuple[str, ...]:
        return tuple(_normalize_runtimes(runtimes))

    def load_lock(self, project_root: Path) -> JsonObject | None:
        return load_install_lock(project_root)

    def read_control_json(
        self, path: Path, *, root: Path, label: str
    ) -> JsonObject:
        return _read_control_json(path, root=root, label=label)

    def read_control_bytes(
        self, path: Path, *, root: Path, label: str
    ) -> bytes:
        return _read_control_bytes(path, root=root, label=label)

    def project_path(
        self, project_root: Path, relative: Path, *, label: str
    ) -> Path:
        return _project_path_without_symlinks(
            project_root, relative, label=label
        )

    def workspace_adapter_owned(self, adapters: object, runtime: str) -> bool:
        return _workspace_adapter_owned(adapters, runtime)

    def adapter_uses_managed_plugin(self, adapters: object, runtime: str) -> bool:
        return _adapter_uses_managed_plugin(adapters, runtime)

    def doctor(self, project_root: Path) -> InstallHealth:
        return doctor_install(project_root)

    def capture_host_slots(
        self, project_root: Path, marketplace: str, runtimes: Iterable[str]
    ) -> JsonObject:
        return _capture_host_slots(project_root, marketplace, set(runtimes))

    def copy_managed_root(self, source: Path, target: Path) -> None:
        _copy_managed_root(source, target)

    def remove_path(self, path: Path) -> None:
        _remove_path(path)

    def component_root(self, root: Path, value: object, *, label: str) -> Path:
        return _component_root(root, value, label=label)

    def replace_tree(self, source: Path, target: Path) -> None:
        _replace_tree(source, target)

    def verified_tree_digest(
        self,
        root: Path,
        expected: object,
        *,
        label: str,
        include_transients: bool = False,
    ) -> str:
        return _verified_tree_digest(
            root,
            expected,
            label=label,
            include_transients=include_transients,
        )

    def write_launchers(self, staged: Path) -> None:
        _write_launchers(staged)

    def stage_catalog(
        self, release_root: Path, staged: Path, manifest: JsonObject
    ) -> JsonObject:
        return stage_catalog(release_root, staged, manifest)

    def current_foundation_matches(
        self, project_root: Path, version: str, digest: str
    ) -> bool:
        return _current_foundation_matches(project_root, version, digest)

    def adapter_skill_source(self, adapter_source: Path, runtime: str) -> Path:
        return _adapter_skill_source(adapter_source, runtime)

    def write_json_atomic(self, path: Path, value: object) -> None:
        if not isinstance(value, dict):
            raise DistributionError("installation JSON value must be an object")
        _write_json_atomic(path, value)

    def tree_digest(self, root: Path, *, include_transients: bool) -> str:
        return tree_digest(root, include_transients=include_transients)

    def remove_legacy_project_plugin_declarations(
        self,
        project_root: Path,
        marketplace: str,
        runtimes: Iterable[str],
        adapters: JsonObject,
    ) -> None:
        _remove_legacy_project_plugin_declarations(
            project_root, marketplace, set(runtimes), adapters
        )

    def restore_host_slots(
        self, project_root: Path, state: JsonObject, marketplace: str
    ) -> None:
        _restore_host_slots(project_root, state, marketplace)

    def adopt_foundation(self, project_root: Path, relative: str) -> bytes | None:
        return _adopt_foundation(project_root, relative)

    def write_project_json(
        self, project_root: Path, relative: str, value: JsonObject
    ) -> None:
        _write_project_json(project_root, relative, value)

    def restore_project_file(
        self, project_root: Path, relative: str, content: bytes | None
    ) -> None:
        _restore_project_file(project_root, relative, content)


class _RollbackOperationsAdapter(RollbackOperations):
    """Bind rollback to explicit services while preserving failure-injection seams."""

    def load_lock(self, project_root: Path) -> JsonObject | None:
        return load_install_lock(project_root)

    def load_lock_path(self, path: Path) -> JsonObject:
        return _load_install_lock_path(path)

    def doctor(self, project_root: Path) -> InstallHealth:
        return doctor_install(project_root)

    def tree_digest(self, root: Path, *, include_transients: bool) -> str:
        return tree_digest(root, include_transients=include_transients)

    def read_control_json(
        self, path: Path, *, root: Path, label: str
    ) -> JsonObject:
        return _read_control_json(path, root=root, label=label)

    def read_control_bytes(
        self, path: Path, *, root: Path, label: str
    ) -> bytes:
        return _read_control_bytes(path, root=root, label=label)

    def project_path(
        self, project_root: Path, relative: Path, *, label: str
    ) -> Path:
        return _project_path_without_symlinks(
            project_root, relative, label=label
        )

    def workspace_adapter_owned(self, adapters: object, runtime: str) -> bool:
        return _workspace_adapter_owned(adapters, runtime)

    def capture_host_slots(
        self, project_root: Path, marketplace: str, runtimes: Iterable[str]
    ) -> JsonObject:
        return _capture_host_slots(project_root, marketplace, set(runtimes))

    def restore_host_slots(
        self, project_root: Path, state: JsonObject, marketplace: str
    ) -> None:
        _restore_host_slots(project_root, state, marketplace)

    def replace_tree(self, source: Path, target: Path) -> None:
        _replace_tree(source, target)

    def copy_managed_root(self, source: Path, target: Path) -> None:
        _copy_managed_root(source, target)

    def write_json_atomic(self, path: Path, value: JsonObject) -> None:
        _write_json_atomic(path, value)

    def write_project_json(
        self, project_root: Path, relative: str, value: JsonObject
    ) -> None:
        _write_project_json(project_root, relative, value)

    def remove_path(self, path: Path) -> None:
        _remove_path(path)

    def restore_project_file(
        self, project_root: Path, relative: str, content: bytes | None
    ) -> None:
        _restore_project_file(project_root, relative, content)


def build_rollback_operations() -> RollbackOperations:
    """Return the typed internal services used by rollback orchestration."""
    return _RollbackOperationsAdapter()


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
    from .git import verify_checkout_claim as _verify_checkout_claim

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
    """Install a checkout while the caller owns the Project install lock."""
    return dict(
        execute_install_transaction(
            checkout,
            project,
            source=source,
            ref=ref,
            commit=commit,
            runtimes=runtimes,
            update=update,
            include_foundation=include_foundation,
            adopt_foundation=adopt_foundation,
            operations=_InstallOperationsAdapter(),
        )
    )


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
            root = candidate
            break
    lock = load_install_lock(root)
    if lock is None:
        raise DistributionError(
            "project installation lock is missing; install ISEKAI or run doctor"
        )
    adapters = lock.get("adapters")
    adapter = adapters.get(runtime) if isinstance(adapters, dict) else None
    if not isinstance(adapter, dict):
        raise DistributionError(f"{runtime} adapter is not installed for this project")
    if str(adapter.get("version", "")).split("+", 1)[0] != adapter_version.split(
        "+", 1
    )[0]:
        raise DistributionError(
            f"{runtime} adapter version does not match project lock"
        )
    core = lock.get("core")
    if not isinstance(core, dict) or core.get("version") != __version__:
        raise DistributionError("running Core version does not match project lock")
    health = doctor_install(root)
    if not health["ready"]:
        raise DistributionError(
            "project installation is unhealthy: " + "; ".join(health["issues"])
        )
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


adopt_foundation = _adopt_foundation
current_foundation_matches = _current_foundation_matches
