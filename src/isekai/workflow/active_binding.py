from __future__ import annotations

import hashlib
import json
import re
import stat
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from ..support.files import (
    UnsafeControlFile,
    metadata_is_path_alias,
    read_control_file,
)
from ..support.jsonio import write_json_atomic
from ..support.locking import file_lock
from isekai.support.errors import IntegrityError, LifecycleError, WorkflowError
from .project import _receipt_source_manifest_path
from isekai.catalog.ai_dlc.unit.checkpointing import checkpoint_progress_issues
from isekai.catalog.ai_dlc.unit.common import _unit_json
from isekai.catalog.ai_dlc.unit.decisions import (
    TERMINAL_STATUSES as _TERMINAL_STATUSES,
)


ACTIVE_BINDING_SCHEMA_VERSION = "1.0.0"
ACTIVE_BINDING_DIRECTORY = ".isekai-runtime"
ACTIVE_BINDING_FILE = "active-unit.json"
ACTIVE_BINDING_LOCK = ".active-unit.lock"
_EVENT_ACTIONS = {"bind", "detach", "learned", "abandoned"}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def _canonical_digest(value: dict[str, Any]) -> str:
    subject = {key: item for key, item in value.items() if key != "event_digest"}
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _project_value(project_manifest: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            read_control_file(
                project_manifest,
                root=project_manifest.parent,
                label="Project manifest",
            ).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, UnsafeControlFile) as exc:
        raise IntegrityError(f"cannot read Project manifest: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("id"), str):
        raise IntegrityError("Project manifest requires an id")
    return payload


def _runtime_directory(project_manifest: Path, *, create: bool) -> Path:
    root = project_manifest.parent.resolve()
    directory = root / ACTIVE_BINDING_DIRECTORY
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        if not create:
            return directory
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            metadata = directory.lstat()
        else:
            metadata = directory.lstat()
    if metadata_is_path_alias(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise IntegrityError(
            f"{ACTIVE_BINDING_DIRECTORY} must be a real directory below the Project"
        )
    return directory


def _empty_binding(project_id: str) -> dict[str, Any]:
    return {
        "type": "project-active-unit-binding",
        "schema_version": ACTIVE_BINDING_SCHEMA_VERSION,
        "project_id": project_id,
        "active_unit": None,
        "generation": 0,
        "events": [],
        "updated_at": None,
    }


def _binding_path(project_manifest: Path, *, create: bool) -> Path:
    return _runtime_directory(project_manifest, create=create) / ACTIVE_BINDING_FILE


def _load_binding(project_manifest: Path, project_id: str) -> dict[str, Any]:
    path = _binding_path(project_manifest, create=False)
    if not path.exists():
        return _empty_binding(project_id)
    try:
        value = json.loads(
            read_control_file(
                path,
                root=project_manifest.parent,
                label="active Unit binding",
            ).decode("utf-8")
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, UnsafeControlFile) as exc:
        raise IntegrityError(f"cannot read active Unit binding: {exc}") from exc
    if not isinstance(value, dict):
        raise IntegrityError("active Unit binding must be an object")
    issues = _binding_issues(value, project_id=project_id)
    if issues:
        raise IntegrityError("active Unit binding is invalid: " + "; ".join(issues))
    return value


def _binding_issues(value: Any, *, project_id: str) -> list[str]:
    if not isinstance(value, dict):
        return ["binding must be an object"]
    issues: list[str] = []
    if value.get("type") != "project-active-unit-binding":
        issues.append("binding has an invalid type")
    if value.get("schema_version") != ACTIVE_BINDING_SCHEMA_VERSION:
        issues.append("binding has an unsupported schema_version")
    if value.get("project_id") != project_id:
        issues.append("binding project_id does not match Project")
    generation = value.get("generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
        issues.append("binding generation must be a non-negative integer")
    active_unit = value.get("active_unit")
    if active_unit is not None:
        if not isinstance(active_unit, dict):
            issues.append("active_unit must be an object or null")
        else:
            if not isinstance(active_unit.get("unit_id"), str):
                issues.append("active_unit requires unit_id")
            path = active_unit.get("path")
            path_base = active_unit.get("path_base")
            if path_base not in {"project", "absolute"}:
                issues.append("active_unit requires a supported path_base")
            elif not isinstance(path, str) or not path.strip():
                issues.append("active_unit requires path")
            elif path_base == "project" and (
                Path(path).is_absolute() or ".." in Path(path).parts
            ):
                issues.append("active_unit Project path must be relative")
            elif path_base == "absolute" and not Path(path).is_absolute():
                issues.append("active_unit absolute path must be absolute")
    events = value.get("events")
    if not isinstance(events, list):
        return issues + ["binding events must be a list"]
    previous_digest: str | None = None
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            issues.append(f"binding event {index} must be an object")
            continue
        for field in (
            "id",
            "action",
            "unit_id",
            "path",
            "path_base",
            "actor",
            "reason",
            "recorded_at",
        ):
            if not isinstance(event.get(field), str) or not event.get(field, "").strip():
                issues.append(f"binding event {index} requires {field}")
        if event.get("action") not in _EVENT_ACTIONS:
            issues.append(f"binding event {index} has an invalid action")
        if event.get("action") == "detach":
            attestation = event.get("attestation")
            if not isinstance(attestation, dict):
                issues.append(f"binding event {index} detach requires attestation")
            elif (
                attestation.get("type") != "human-decision-attestation"
                or attestation.get("reported_actor") != event.get("actor")
                or attestation.get("identity_verification") != "not-performed-by-core"
                or attestation.get("confirmation_source") != "caller-attested"
            ):
                issues.append(f"binding event {index} has an invalid attestation")
        event_path = event.get("path")
        event_base = event.get("path_base")
        if event_base not in {"project", "absolute"}:
            issues.append(f"binding event {index} has an invalid path_base")
        elif isinstance(event_path, str):
            if event_base == "project" and (
                Path(event_path).is_absolute() or ".." in Path(event_path).parts
            ):
                issues.append(f"binding event {index} Project path must be relative")
            elif event_base == "absolute" and not Path(event_path).is_absolute():
                issues.append(f"binding event {index} absolute path must be absolute")
        if event.get("previous_event_digest") != previous_digest:
            issues.append(f"binding event {index} does not continue the digest chain")
        digest = event.get("event_digest")
        if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
            issues.append(f"binding event {index} requires a SHA-256 digest")
        elif digest != _canonical_digest(event):
            issues.append(f"binding event {index} digest does not match")
        else:
            previous_digest = digest
    if isinstance(generation, int) and generation != len(events):
        issues.append("binding generation does not match event count")
    return issues


def _unit_locator(
    project_manifest: Path,
    unit_dir: Path,
) -> tuple[str, str, dict[str, Any]]:
    root = project_manifest.parent.resolve()
    unit = unit_dir.expanduser().resolve()
    try:
        path = unit.relative_to(root).as_posix()
        path_base = "project"
    except ValueError:
        path = str(unit)
        path_base = "absolute"
    value = _unit_json(unit, "unit.json")
    project = _project_value(project_manifest)
    if value.get("project_id") != project.get("id"):
        raise WorkflowError("active Unit project_id does not match selected Project")
    receipt = _unit_json(unit, "context-receipt.json")
    try:
        bound_manifest = _receipt_source_manifest_path(
            receipt,
            unit_dir=unit,
            selected_project=project_manifest,
        )
    except WorkflowError as exc:
        raise WorkflowError(f"active Unit has an invalid Project binding: {exc}") from exc
    if bound_manifest != project_manifest.resolve():
        raise WorkflowError("active Unit is bound to a different Project manifest")
    return path, path_base, value


def project_manifest_for_unit(unit: str | Path) -> Path:
    unit_dir = Path(unit).expanduser().resolve()
    receipt = _unit_json(unit_dir, "context-receipt.json")
    try:
        manifest = _receipt_source_manifest_path(receipt, unit_dir=unit_dir)
    except WorkflowError as exc:
        raise WorkflowError(f"cannot resolve the Unit's Project binding: {exc}") from exc
    project = _project_value(manifest)
    unit_value = _unit_json(unit_dir, "unit.json")
    if unit_value.get("project_id") != project.get("id"):
        raise WorkflowError("Unit project_id does not match its bound Project")
    return manifest


def _active_unit_value(
    project_manifest: Path,
    binding: dict[str, Any],
) -> tuple[Path, dict[str, Any]] | None:
    active = binding.get("active_unit")
    if active is None:
        return None
    if not isinstance(active, dict):  # validated before this helper
        raise IntegrityError("active Unit binding has an invalid active_unit")
    candidate = (
        Path(str(active.get("path"))).resolve()
        if active.get("path_base") == "absolute"
        else (project_manifest.parent / str(active.get("path"))).resolve()
    )
    if not candidate.is_dir():
        raise IntegrityError(f"bound active Unit does not exist: {candidate}")
    unit = _unit_json(candidate, "unit.json")
    if unit.get("id") != active.get("unit_id"):
        raise IntegrityError("bound active Unit id does not match its Unit artifact")
    return candidate, unit


def active_unit_binding(project: str | Path) -> dict[str, Any]:
    manifest = Path(project).expanduser().resolve()
    project_value = _project_value(manifest)
    binding = _load_binding(manifest, str(project_value["id"]))
    current = _active_unit_value(manifest, binding)
    return _binding_status(manifest, binding, current)


def _binding_status(
    manifest: Path,
    binding: dict[str, Any],
    current: tuple[Path, dict[str, Any]] | None,
) -> dict[str, Any]:
    active = (
        current is not None
        and current[1].get("status") not in _TERMINAL_STATUSES
    )
    return {
        "active": active,
        "unit": (
            {
                "path": str(current[0]),
                "unit_id": current[1].get("id"),
                "catalog_entry": current[1].get("catalog_entry"),
                "title": current[1].get("title"),
                "status": current[1].get("status"),
                "phase": current[1].get("phase"),
            }
            if active and current is not None
            else None
        ),
        "generation": binding.get("generation", 0),
        "updated_at": binding.get("updated_at"),
        "state_path": str(
            manifest.parent / ACTIVE_BINDING_DIRECTORY / ACTIVE_BINDING_FILE
        ),
    }


def _event(
    binding: dict[str, Any],
    *,
    action: str,
    unit_id: str,
    path: str,
    path_base: str,
    actor: str,
    reason: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%d%H%M%S%f")
    events = binding.get("events", [])
    event = {
        "id": "ACT-" + stamp,
        "action": action,
        "unit_id": unit_id,
        "path": path,
        "path_base": path_base,
        "actor": actor,
        "reason": reason,
        "recorded_at": now.isoformat(),
        "previous_event_digest": events[-1].get("event_digest") if events else None,
    }
    if action == "detach":
        event["attestation"] = {
            "type": "human-decision-attestation",
            "reported_actor": actor,
            "identity_verification": "not-performed-by-core",
            "confirmation_source": "caller-attested",
        }
    event["event_digest"] = _canonical_digest(event)
    return event


def _write_event(
    project_manifest: Path,
    binding: dict[str, Any],
    event: dict[str, Any],
    active_unit: dict[str, str] | None,
) -> dict[str, Any]:
    events = [*binding.get("events", []), event]
    candidate = {
        **binding,
        "active_unit": active_unit,
        "generation": len(events),
        "events": events,
        "updated_at": event["recorded_at"],
    }
    issues = _binding_issues(candidate, project_id=str(binding.get("project_id")))
    if issues:
        raise IntegrityError("active Unit binding update is invalid: " + "; ".join(issues))
    write_json_atomic(_binding_path(project_manifest, create=True), candidate)
    return candidate


def _complete_terminal_binding(
    project_manifest: Path,
    binding: dict[str, Any],
    current: tuple[Path, dict[str, Any]],
) -> dict[str, Any]:
    status = str(current[1].get("status"))
    if status not in _TERMINAL_STATUSES:
        raise LifecycleError(
            "active Unit binding can complete only at learned or abandoned"
        )
    locator, path_base, _unit = _unit_locator(project_manifest, current[0])
    event = _event(
        binding,
        action=status,
        unit_id=str(current[1].get("id")),
        path=locator,
        path_base=path_base,
        actor="runtime-core",
        reason=(
            "The final Operation Decision transitioned the Unit to learned."
            if status == "learned"
            else "An approved abandonment Decision closed this Unit."
        ),
    )
    return _write_event(project_manifest, binding, event, None)


def _reconcile_terminal_binding(
    project_manifest: Path,
    binding: dict[str, Any],
    current: tuple[Path, dict[str, Any]] | None,
) -> tuple[dict[str, Any], tuple[Path, dict[str, Any]] | None]:
    if current is None or current[1].get("status") not in _TERMINAL_STATUSES:
        return binding, current
    return _complete_terminal_binding(project_manifest, binding, current), None


def bind_active_unit(
    project: str | Path,
    unit: str | Path,
    *,
    actor: str = "runtime-core",
    reason: str = "Continue persistent work in this Unit.",
) -> dict[str, Any]:
    manifest = Path(project).expanduser().resolve()
    project_value = _project_value(manifest)
    locator, path_base, unit_value = _unit_locator(manifest, Path(unit))
    if unit_value.get("status") in _TERMINAL_STATUSES:
        return active_unit_binding(manifest)
    runtime_dir = _runtime_directory(manifest, create=True)
    with file_lock(
        runtime_dir / ACTIVE_BINDING_LOCK,
        subject="Project active Unit binding",
    ):
        binding = _load_binding(manifest, str(project_value["id"]))
        current = _active_unit_value(manifest, binding)
        binding, current = _reconcile_terminal_binding(manifest, binding, current)
        if current is not None and current[1].get("status") not in _TERMINAL_STATUSES:
            if current[0] == Path(unit).expanduser().resolve():
                return active_unit_binding(manifest)
            raise LifecycleError(
                f"Project already has an unfinished active Unit: {current[0]}; "
                "record active-unit-detach before switching work"
            )
        event = _event(
            binding,
            action="bind",
            unit_id=str(unit_value.get("id")),
            path=locator,
            path_base=path_base,
            actor=actor,
            reason=reason,
        )
        _write_event(
            manifest,
            binding,
            event,
            {
                "unit_id": str(unit_value.get("id")),
                "path": locator,
                "path_base": path_base,
            },
        )
    return active_unit_binding(manifest)


@contextmanager
def active_unit_creation_guard(
    project: str | Path,
) -> Iterator[Callable[[str | Path, str, str], dict[str, Any]]]:
    """Serialize new Unit creation with the Project's active work boundary."""
    manifest = Path(project).expanduser().resolve()
    project_value = _project_value(manifest)
    runtime_dir = _runtime_directory(manifest, create=True)
    with file_lock(
        runtime_dir / ACTIVE_BINDING_LOCK,
        subject="Project active Unit binding",
    ):
        binding = _load_binding(manifest, str(project_value["id"]))
        current = _active_unit_value(manifest, binding)
        binding, current = _reconcile_terminal_binding(manifest, binding, current)
        if current is not None and current[1].get("status") not in _TERMINAL_STATUSES:
            raise LifecycleError(
                f"unit-init blocked by unfinished active Unit {current[1].get('id')}; "
                "amend it or record active-unit-detach first"
            )

        committed = False

        def commit(unit: str | Path, actor: str, reason: str) -> dict[str, Any]:
            nonlocal binding, committed
            if committed:
                raise IntegrityError("active Unit creation binding was already committed")
            locator, path_base, unit_value = _unit_locator(manifest, Path(unit))
            event = _event(
                binding,
                action="bind",
                unit_id=str(unit_value.get("id")),
                path=locator,
                path_base=path_base,
                actor=actor,
                reason=reason,
            )
            binding = _write_event(
                manifest,
                binding,
                event,
                {
                    "unit_id": str(unit_value.get("id")),
                    "path": locator,
                    "path_base": path_base,
                },
            )
            committed = True
            return _binding_status(
                manifest,
                binding,
                (Path(unit).expanduser().resolve(), unit_value),
            )

        yield commit


def require_active_unit_match(
    project: str | Path,
    unit: str | Path,
    *,
    action: str,
    bind_if_empty: bool,
) -> dict[str, Any]:
    manifest = Path(project).expanduser().resolve()
    requested = Path(unit).expanduser().resolve()
    status = active_unit_binding(manifest)
    active = status.get("unit")
    if isinstance(active, dict):
        if Path(str(active.get("path"))).resolve() != requested:
            raise LifecycleError(
                f"{action} is outside the unfinished active Unit "
                f"{active.get('unit_id')}; use active-unit-detach after an explicit "
                "user decision before switching"
            )
        return status
    if bind_if_empty:
        return bind_active_unit(
            manifest,
            requested,
            reason=f"Core bound the first persistent Unit action: {action}.",
        )
    return status


@contextmanager
def active_unit_action_guard(
    project: str | Path,
    unit: str | Path,
    *,
    action: str,
) -> Iterator[Callable[[], dict[str, Any]]]:
    """Keep the Project binding stable and optionally close it before unlock."""
    manifest = Path(project).expanduser().resolve()
    project_value = _project_value(manifest)
    requested = Path(unit).expanduser().resolve()
    runtime_dir = _runtime_directory(manifest, create=True)
    with file_lock(
        runtime_dir / ACTIVE_BINDING_LOCK,
        subject="Project active Unit binding",
    ):
        binding = _load_binding(manifest, str(project_value["id"]))
        current = _active_unit_value(manifest, binding)
        binding, current = _reconcile_terminal_binding(manifest, binding, current)
        if current is not None and current[1].get("status") not in _TERMINAL_STATUSES:
            if current[0] != requested:
                raise LifecycleError(
                    f"{action} is outside the unfinished active Unit "
                    f"{current[1].get('id')}; use active-unit-detach after an "
                    "explicit user decision before switching"
                )
        else:
            locator, path_base, unit_value = _unit_locator(manifest, requested)
            if unit_value.get("status") not in _TERMINAL_STATUSES:
                event = _event(
                    binding,
                    action="bind",
                    unit_id=str(unit_value.get("id")),
                    path=locator,
                    path_base=path_base,
                    actor="runtime-core",
                    reason=f"Core bound the first persistent Unit action: {action}.",
                )
                binding = _write_event(
                    manifest,
                    binding,
                    event,
                    {
                        "unit_id": str(unit_value.get("id")),
                        "path": locator,
                        "path_base": path_base,
                    },
                )
                current = (requested, unit_value)

        completed = False

        def complete() -> dict[str, Any]:
            nonlocal binding, completed, current
            if completed:
                return active_unit_binding(manifest)
            if current is None or current[0] != requested:
                return active_unit_binding(manifest)
            refreshed = _unit_json(requested, "unit.json")
            current = (requested, refreshed)
            binding = _complete_terminal_binding(manifest, binding, current)
            current = None
            completed = True
            return _binding_status(manifest, binding, None)

        yield complete


def detach_active_unit(
    project: str | Path,
    *,
    unit: str | Path,
    requested_by: str,
    reason: str,
) -> dict[str, Any]:
    if not requested_by.strip() or not reason.strip():
        raise WorkflowError("active-unit-detach requires requested_by and reason")
    manifest = Path(project).expanduser().resolve()
    project_value = _project_value(manifest)
    requested = Path(unit).expanduser().resolve()
    runtime_dir = _runtime_directory(manifest, create=True)
    with file_lock(
        runtime_dir / ACTIVE_BINDING_LOCK,
        subject="Project active Unit binding",
    ):
        binding = _load_binding(manifest, str(project_value["id"]))
        current = _active_unit_value(manifest, binding)
        if current is None or current[1].get("status") in _TERMINAL_STATUSES:
            raise LifecycleError("Project has no unfinished active Unit to detach")
        if current[0] != requested:
            raise LifecycleError("active-unit-detach unit does not match the active Unit")
        checkpoint_issues = checkpoint_progress_issues(current[0])
        if checkpoint_issues:
            raise LifecycleError(
                "active-unit-detach requires a current Checkpoint: "
                + "; ".join(checkpoint_issues)
            )
        locator, path_base, _unit = _unit_locator(manifest, current[0])
        event = _event(
            binding,
            action="detach",
            unit_id=str(current[1].get("id")),
            path=locator,
            path_base=path_base,
            actor=requested_by.strip(),
            reason=reason.strip(),
        )
        _write_event(manifest, binding, event, None)
    return active_unit_binding(manifest)


def complete_active_unit(project: str | Path, unit: str | Path) -> dict[str, Any]:
    manifest = Path(project).expanduser().resolve()
    project_value = _project_value(manifest)
    requested = Path(unit).expanduser().resolve()
    runtime_dir = _runtime_directory(manifest, create=True)
    with file_lock(
        runtime_dir / ACTIVE_BINDING_LOCK,
        subject="Project active Unit binding",
    ):
        binding = _load_binding(manifest, str(project_value["id"]))
        current = _active_unit_value(manifest, binding)
        if current is None or current[0] != requested:
            return active_unit_binding(manifest)
        _complete_terminal_binding(manifest, binding, current)
    return active_unit_binding(manifest)
