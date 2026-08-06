from __future__ import annotations

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Iterable

from .. import __version__
from .release import (
    LOCK_NAME,
    LOCK_SCHEMA_VERSION,
    MANAGED_ROOT,
    PLUGIN_ID,
    PROTOCOL_VERSION,
    RUNTIMES,
    DistributionError,
    _component_root,
    _normalize_runtimes,
    _read_json,
    _safe_relative_path,
    _verify_or_raise,
    _write_json_atomic,
    tree_digest,
)
from ..foundation import FoundationError, load_foundation
from .marketplace import (
    _copy_managed_root,
    _prepare_claude_marketplace,
    _prepare_codex_marketplace,
    _project_id,
    _registration_commands,
    _replace_tree,
    _run_registration,
    _slug,
    _write_launchers,
)


def load_install_lock(project: str | Path) -> dict[str, Any] | None:
    root = Path(project).expanduser().resolve()
    path = root if root.name == LOCK_NAME else root / LOCK_NAME
    if not path.is_file():
        return None
    lock = _read_json(path)
    if lock.get("schema_version") != LOCK_SCHEMA_VERSION:
        raise DistributionError("unsupported isekai.lock.json schema_version")
    return lock


def _project_path_without_symlinks(
    project_root: Path, relative: Path, *, label: str
) -> Path:
    lexical = project_root
    for part in relative.parts:
        lexical = lexical / part
        if lexical.is_symlink():
            raise DistributionError(f"{label} contains a symlink: {relative}")
    return lexical


def _installed_path(project_root: Path, value: object, *, label: str) -> Path:
    relative = _safe_relative_path(value, label=label)
    lexical = _project_path_without_symlinks(project_root, relative, label=label)
    target = lexical.resolve()
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
    """Install an already resolved checkout using ``commit`` as the immutable pin.

    Normal callers must use :func:`install_from_git`, which validates tags and full
    commits before loading release code. This lower-level helper trusts the caller
    to supply the checkout for the recorded full commit; ``ref`` is descriptive.
    """
    if not isinstance(commit, str) or not re.fullmatch(
        r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", commit
    ):
        raise DistributionError("commit must be a full 40- or 64-character hash")
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
    kiro_relative = Path(".kiro/skills/isekai")
    kiro_target = project_root / kiro_relative
    current_kiro_owned = (
        current_lock is not None
        and isinstance(current_lock.get("adapters"), dict)
        and "kiro" in current_lock["adapters"]
    )
    if "kiro" in selected or current_kiro_owned:
        kiro_target = _project_path_without_symlinks(
            project_root, kiro_relative, label="adapter:kiro.path"
        )
    if (
        "kiro" in selected
        and (kiro_target.exists() or kiro_target.is_symlink())
        and not current_kiro_owned
    ):
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
        health = doctor_install(project_root)
        if not health["ready"]:
            raise DistributionError(
                "post-install verification failed: " + "; ".join(health["issues"])
            )
    except Exception:
        if backup.exists():
            if managed.exists():
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
    try:
        registration = _run_registration(commands) if register else []
    except DistributionError as exc:
        # The project-local install is committed and verified at this point;
        # only the host-side registration failed, so say so rather than let the
        # caller read this as a failed installation.
        rerun = " && ".join(" ".join(command) for command in commands)
        raise DistributionError(
            f"ISEKAI is installed in {project_root} but host registration failed: {exc}. "
            f"The installation is complete and verified; rerun manually: {rerun}"
        ) from exc
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

    current_adapters = current.get("adapters", {})
    previous_adapters = previous_lock.get("adapters", {})
    current_kiro_owned = (
        isinstance(current_adapters, dict) and "kiro" in current_adapters
    )
    previous_kiro_owned = (
        isinstance(previous_adapters, dict) and "kiro" in previous_adapters
    )
    previous_kiro_snapshot = rollback / "kiro"
    if previous_kiro_owned and not previous_kiro_snapshot.is_dir():
        raise DistributionError("previous Kiro installation snapshot is missing")
    kiro_relative = Path(".kiro/skills/isekai")
    kiro_target = project_root / kiro_relative
    if current_kiro_owned or previous_kiro_owned:
        kiro_target = _project_path_without_symlinks(
            project_root, kiro_relative, label="adapter:kiro.path"
        )
    if (
        previous_kiro_owned
        and not current_kiro_owned
        and (kiro_target.exists() or kiro_target.is_symlink())
    ):
        raise DistributionError(
            "refusing to replace an unmanaged .kiro/skills/isekai directory"
        )

    stage_root = Path(
        tempfile.mkdtemp(prefix=".isekai-rollback-stage-", dir=project_root)
    )
    staged = stage_root / MANAGED_ROOT
    previous_kiro_copy = stage_root / "previous-kiro"
    previous_project_bytes = (
        (rollback / "project.json").read_bytes()
        if (rollback / "project.json").is_file()
        else None
    )
    backup = project_root / f".{MANAGED_ROOT}-backup-{uuid.uuid4().hex}"
    kiro_backup = project_root / f".isekai-kiro-backup-{uuid.uuid4().hex}"
    project_manifest = project_root / "project.json"
    current_project_bytes = (
        project_manifest.read_bytes() if project_manifest.is_file() else None
    )
    lock_path = project_root / LOCK_NAME
    current_lock_bytes = lock_path.read_bytes()

    try:
        if previous_kiro_snapshot.is_dir():
            shutil.copytree(previous_kiro_snapshot, previous_kiro_copy)
        shutil.copytree(previous_install, staged)
        redo = staged / "rollback"
        _copy_managed_root(managed, redo / "install")
        _write_json_atomic(redo / LOCK_NAME, current)
        if current_kiro_owned:
            shutil.copytree(kiro_target, redo / "kiro")
        if current_project_bytes is not None:
            (redo / "project.json").write_bytes(current_project_bytes)

        managed.rename(backup)
        staged.rename(managed)
        if current_kiro_owned:
            kiro_target.rename(kiro_backup)
        if previous_kiro_copy.is_dir():
            kiro_target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(previous_kiro_copy, kiro_target)
        if previous_project_bytes is None:
            project_manifest.unlink(missing_ok=True)
        else:
            project_manifest.write_bytes(previous_project_bytes)
        _write_json_atomic(lock_path, previous_lock)
        postflight = doctor_install(project_root)
        if not postflight["ready"]:
            raise DistributionError(
                "rollback verification failed: " + "; ".join(postflight["issues"])
            )
    except Exception:
        if backup.exists():
            if managed.exists():
                shutil.rmtree(managed)
            backup.rename(managed)
        if current_kiro_owned and kiro_backup.exists():
            if kiro_target.exists() or kiro_target.is_symlink():
                if kiro_target.is_dir() and not kiro_target.is_symlink():
                    shutil.rmtree(kiro_target)
                else:
                    kiro_target.unlink()
            kiro_backup.rename(kiro_target)
        elif not current_kiro_owned and previous_kiro_copy.is_dir() and (
            kiro_target.exists() or kiro_target.is_symlink()
        ):
            if kiro_target.is_dir() and not kiro_target.is_symlink():
                shutil.rmtree(kiro_target)
            else:
                kiro_target.unlink()
        if current_project_bytes is None:
            project_manifest.unlink(missing_ok=True)
        else:
            project_manifest.write_bytes(current_project_bytes)
        lock_path.write_bytes(current_lock_bytes)
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
        if (candidate / "project.json").is_file():
            # Stop at the nearest Project. An uninstalled Project must not
            # borrow an unrelated ancestor's lock and report itself as healthy.
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
