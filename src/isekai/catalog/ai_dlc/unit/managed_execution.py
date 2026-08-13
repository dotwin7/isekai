from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from isekai.support.files import UnsafeControlFile, read_control_file_snapshot
from isekai.support.jsonio import (
    UnsafeWritePath,
    unlink_file_beneath,
    write_bytes_atomic_beneath,
)
from isekai.support.errors import IntegrityError, LifecycleError, WorkflowError
from isekai.workflow.project import receipt_source_manifest_path as _receipt_source_manifest_path
from .amendments import AMENDABLE_ARTIFACT_GATES, amendment_status
from .artifacts import ACCEPTANCE_CHECKBOX
from .decision_schema import TERMINAL_STATUSES
from .authorization import (
    authorization_ledger_issues as _authorization_ledger_issues,
    last_authorization_id as _last_authorization_id,
)
from .authorization_request import resolve_authorization_request
from .common import (
    unit_bytes as _unit_bytes,
    unit_json as _unit_json,
    unit_path_without_symlinks as _unit_path_without_symlinks,
    write_unit_json as _write_unit_json,
    unit_lock,
)
from .execution import authorize_action_locked as _authorize_action_locked
from .proof_sandbox import build_sandbox_invocation
from .proof_runtime import (
    MAX_PROOF_CAPTURE_BYTES,
    MAX_PROOF_OUTPUT_BYTES,
    ProofRun,
    capture_managed_process as _capture_managed_process,
    copy_test_workspace as _copy_test_workspace,
    isolated_test_command as _isolated_test_command,
    proof_environment as _proof_environment,
    proof_command_text,
    proof_output_digest,
)


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
ABSENT_DIGEST = "absent"
MAX_MANAGED_FILE_BYTES = 8 * 1024 * 1024
MAX_PROOF_SECONDS = 1800


def _content_digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _current_file_snapshot(
    root: Path,
    relative: str,
) -> tuple[str, bytes | None, int | None]:
    path = root / relative
    try:
        content, metadata = read_control_file_snapshot(
            path,
            root=root,
            label=f"managed write target {relative}",
        )
    except FileNotFoundError:
        return ABSENT_DIGEST, None, None
    except (OSError, UnsafeControlFile) as exc:
        raise IntegrityError(str(exc)) from exc
    return _content_digest(content), content, stat.S_IMODE(metadata.st_mode)


def _write_managed_bytes(
    root: Path,
    relative: str,
    content: bytes,
    *,
    mode: int | None = None,
    create_parents: bool = False,
) -> None:
    try:
        write_bytes_atomic_beneath(
            root,
            relative,
            content,
            mode=mode,
            create_parents=create_parents,
        )
    except UnsafeWritePath as exc:
        raise IntegrityError(str(exc)) from exc


def _validate_expected_digest(value: object, *, target: str) -> str:
    if value == ABSENT_DIGEST:
        return ABSENT_DIGEST
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise WorkflowError(
            f"managed write for {target} requires expected_digest as absent or SHA-256"
        )
    return value


def _validate_content(value: object, *, target: str) -> bytes:
    if not isinstance(value, str):
        raise WorkflowError(f"managed write content must be UTF-8 text: {target}")
    content = value.encode("utf-8")
    if len(content) > MAX_MANAGED_FILE_BYTES:
        raise WorkflowError(
            f"managed write content exceeds {MAX_MANAGED_FILE_BYTES} bytes: {target}"
        )
    return content


def _normalize_change_records(changes: object) -> list[dict[str, Any]]:
    if not isinstance(changes, list) or not changes:
        raise WorkflowError("managed edit requires a non-empty changes list")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(changes):
        if not isinstance(item, dict):
            raise WorkflowError(f"managed edit change {index} must be an object")
        target = item.get("target")
        if not isinstance(target, str) or not target.strip():
            raise WorkflowError(f"managed edit change {index} requires target")
        portable = target.replace("\\", "/")
        if portable in seen:
            raise WorkflowError(f"managed edit contains duplicate target: {portable}")
        seen.add(portable)
        normalized.append(
            {
                "target": portable,
                "expected_digest": _validate_expected_digest(
                    item.get("expected_digest"), target=portable
                ),
                "content": _validate_content(item.get("content"), target=portable),
            }
        )
    return normalized


def _restore_file_snapshots(
    root: Path,
    snapshots: Iterable[tuple[str, bytes | None, int | None]],
    *,
    cause: Exception,
) -> None:
    errors: list[str] = []
    for relative, content, mode in reversed(list(snapshots)):
        try:
            if content is None:
                unlink_file_beneath(root, relative, missing_ok=True)
            else:
                _write_managed_bytes(root, relative, content, mode=mode)
        except Exception as exc:  # pragma: no cover - secondary filesystem failure
            errors.append(f"{root / relative}: {exc}")
    if errors:
        raise IntegrityError(
            "managed edit failed and could not restore Project files: "
            + "; ".join(errors)
        ) from cause


def execute_managed_edit(
    path: str | Path,
    *,
    changes: object,
) -> dict[str, Any]:
    """Authorize and apply one Project edit batch inside the Core boundary.

    The host supplies desired UTF-8 contents and optimistic precondition digests;
    it never receives a free-standing edit grant. Core validates every target,
    writes the files, and binds the resulting digests to the authorization ledger
    while holding the Unit lock. Any in-process failure restores both files and
    the prior ledger before returning.
    """

    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise WorkflowError(f"Unit directory does not exist: {unit_dir}")
    records = _normalize_change_records(changes)
    with unit_lock(unit_dir):
        unit = _unit_json(unit_dir, "unit.json")
        if isinstance(unit.get("status"), str) and unit.get(
            "status"
        ) in TERMINAL_STATUSES:
            raise LifecycleError(
                f"a {unit.get('status')} Unit cannot execute managed edits"
            )
        receipt = _unit_json(unit_dir, "context-receipt.json")
        project_root = _receipt_source_manifest_path(
            receipt, unit_dir=unit_dir
        ).parent.resolve()
        envelope = _unit_json(unit_dir, "execution-envelope.json")
        previous_ledger = _unit_json(unit_dir, "execution-authorizations.json")
        previous_ledger_bytes = _unit_bytes(
            unit_dir, "execution-authorizations.json"
        )

        normalized_targets: list[str] = []
        for record in records:
            normalized, _policy, _request, issue = resolve_authorization_request(
                unit_dir,
                action="edit",
                target=record["target"],
                method=None,
                credential_ref=None,
                envelope=envelope,
            )
            if issue is not None:
                raise LifecycleError(issue)
            assert normalized is not None
            normalized_targets.append(normalized)

        authorization = _authorize_action_locked(
            unit_dir,
            action="edit",
            target=normalized_targets[0],
            stage=None,
            method=None,
            credential_ref=None,
        )
        if authorization.get("allowed") is not True:
            raise LifecycleError(str(authorization.get("reason", "managed edit blocked")))

        snapshots: list[tuple[str, bytes | None, int | None]] = []
        execution_records: list[dict[str, str]] = []
        try:
            for requested, normalized in zip(records, normalized_targets, strict=True):
                current_digest, before_content, before_mode = _current_file_snapshot(
                    project_root,
                    normalized,
                )
                if current_digest != requested["expected_digest"]:
                    raise IntegrityError(
                        f"managed edit precondition changed for {normalized}: "
                        f"expected {requested['expected_digest']}, found {current_digest}"
                    )
                snapshots.append((normalized, before_content, before_mode))
                _write_managed_bytes(
                    project_root,
                    normalized,
                    requested["content"],
                    mode=before_mode,
                    create_parents=True,
                )
                after_digest, _after_content, _after_mode = _current_file_snapshot(
                    project_root,
                    normalized,
                )
                execution_records.append(
                    {
                        "target": normalized,
                        "before_digest": current_digest,
                        "after_digest": after_digest,
                    }
                )

            candidate_ledger = _unit_json(
                unit_dir, "execution-authorizations.json"
            )
            grants = candidate_ledger.get("grants")
            if not isinstance(grants, list) or not grants:
                raise IntegrityError("managed edit authorization grant was not persisted")
            grant = grants[-1]
            if not isinstance(grant, dict) or grant.get("id") != authorization.get(
                "authorization_id"
            ):
                raise IntegrityError("managed edit authorization grant changed concurrently")
            grant["targets"] = normalized_targets
            grant["execution"] = {
                "type": "core-managed-edit",
                "status": "completed",
                "files": execution_records,
            }
            issues = _authorization_ledger_issues(
                candidate_ledger, unit, envelope, unit_dir=unit_dir
            )
            if issues:
                raise IntegrityError(
                    "managed edit receipt rejected: " + "; ".join(issues)
                )
            _write_unit_json(
                unit_dir,
                "execution-authorizations.json",
                candidate_ledger,
            )
            persisted = _unit_json(unit_dir, "execution-authorizations.json")
            persisted_issues = _authorization_ledger_issues(
                persisted, unit, envelope, unit_dir=unit_dir
            )
            if persisted_issues or _last_authorization_id(
                persisted
            ) != authorization.get("authorization_id"):
                raise IntegrityError("managed edit receipt postflight failed")
        except Exception as exc:
            _restore_file_snapshots(project_root, snapshots, cause=exc)
            try:
                _write_managed_bytes(
                    unit_dir,
                    "execution-authorizations.json",
                    previous_ledger_bytes,
                )
            except Exception as restore_exc:  # pragma: no cover - secondary failure
                raise IntegrityError(
                    "managed edit failed and authorization ledger could not be restored: "
                    f"{restore_exc}"
                ) from exc
            raise

        return {
            **authorization,
            "execution": {
                "type": "core-managed-edit",
                "status": "completed",
                "files": execution_records,
            },
            "host_write_required": False,
            "previous_authorization_count": len(previous_ledger.get("grants", [])),
        }


def _normalize_command(command: object) -> list[str]:
    if not isinstance(command, list) or not command:
        raise WorkflowError("proof requires a non-empty argv list")
    if len(command) > 64 or any(
        not isinstance(item, str) or not item or "\x00" in item for item in command
    ):
        raise WorkflowError("proof argv must contain 1-64 non-empty strings")
    return list(command)


def _validated_proof_request(
    path: str | Path,
    command: object,
    timeout_seconds: int,
) -> tuple[Path, list[str]]:
    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise WorkflowError(f"Unit directory does not exist: {unit_dir}")
    argv = _normalize_command(command)
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
        or timeout_seconds > MAX_PROOF_SECONDS
    ):
        raise WorkflowError(f"proof timeout must be 1-{MAX_PROOF_SECONDS} seconds")
    return unit_dir, argv


def _run_proof(
    project_root: Path,
    argv: list[str],
    timeout_seconds: int,
) -> ProofRun:
    with tempfile.TemporaryDirectory(prefix="isekai-proof-") as temp:
        temp_root = Path(temp).resolve()
        sandbox_project = temp_root / "project"
        dependency_roots = _copy_test_workspace(project_root, sandbox_project)
        environment = _proof_environment(
            temp_root,
            dependency_roots=dependency_roots,
        )
        sandbox = build_sandbox_invocation(
            argv,
            temp_root=temp_root,
            workspace=sandbox_project,
            source_project=project_root,
            dependency_roots=dependency_roots,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        process = subprocess.Popen(
            sandbox.argv,
            cwd=sandbox_project,
            env=sandbox.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        (
            exit_code,
            timed_out,
            output_limit_exceeded,
            stdout_capture,
            stderr_capture,
        ) = _capture_managed_process(process, timeout_seconds=timeout_seconds)
        return ProofRun(
            argv=argv,
            dependency_roots=dependency_roots,
            provider=sandbox.provider,
            sandbox_policy=sandbox.sandbox_policy,
            filesystem_isolation=sandbox.filesystem_isolation,
            network_isolation=sandbox.network_isolation,
            process_isolation=sandbox.process_isolation,
            resource_limits=sandbox.resource_limits,
            exit_code=exit_code,
            timed_out=timed_out,
            output_limit_exceeded=output_limit_exceeded,
            stdout_digest=stdout_capture.digest,
            stderr_digest=stderr_capture.digest,
            stdout=stdout_capture.text,
            stderr=stderr_capture.text,
            stdout_truncated=stdout_capture.truncated,
            stderr_truncated=stderr_capture.truncated,
            stdout_bytes=stdout_capture.byte_count,
            stderr_bytes=stderr_capture.byte_count,
        )


def _proof_execution_record(
    run: ProofRun,
    project_root: Path,
) -> dict[str, Any]:
    status = (
        "timed-out"
        if run.timed_out
        else ("output-limit-exceeded" if run.output_limit_exceeded else "completed")
    )
    execution: dict[str, Any] = {
        "type": "core-proof",
        "status": status,
        "workspace": "disposable-copy",
        "sandbox_provider": run.provider,
        "sandbox_policy": run.sandbox_policy,
        "filesystem_isolation": run.filesystem_isolation,
        "network_isolation": run.network_isolation,
        "process_isolation": run.process_isolation,
        "resource_limits": run.resource_limits,
        "environment": "core-allowlisted",
        "dependency_views": [
            root.relative_to(project_root).as_posix()
            for root in run.dependency_roots
        ],
        "command": run.argv,
        "exit_code": run.exit_code,
        "timed_out": run.timed_out,
        "stdout_digest": run.stdout_digest,
        "stderr_digest": run.stderr_digest,
        "stdout_bytes": run.stdout_bytes,
        "stderr_bytes": run.stderr_bytes,
        "stdout_truncated": run.stdout_truncated,
        "stderr_truncated": run.stderr_truncated,
        "output_capture_limit_bytes": MAX_PROOF_CAPTURE_BYTES,
        "output_limit_exceeded": run.output_limit_exceeded,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    execution["evidence_command"] = proof_command_text(run.argv)
    execution["evidence_output_digest"] = proof_output_digest(execution)
    return execution


def _persist_proof_receipt(
    unit_dir: Path,
    *,
    unit: dict[str, Any],
    envelope: dict[str, Any],
    authorization: dict[str, Any],
    execution: dict[str, Any],
) -> None:
    candidate_ledger = _unit_json(unit_dir, "execution-authorizations.json")
    grants = candidate_ledger.get("grants")
    if not isinstance(grants, list) or not grants:
        raise IntegrityError("proof authorization grant was not persisted")
    grant = grants[-1]
    if not isinstance(grant, dict) or grant.get("id") != authorization.get(
        "authorization_id"
    ):
        raise IntegrityError("proof authorization grant changed concurrently")
    grant["execution"] = execution
    issues = _authorization_ledger_issues(
        candidate_ledger, unit, envelope, unit_dir=unit_dir
    )
    if issues:
        raise IntegrityError("proof receipt rejected: " + "; ".join(issues))
    _write_unit_json(unit_dir, "execution-authorizations.json", candidate_ledger)


def _restore_proof_ledger(
    unit_dir: Path,
    previous_ledger_bytes: bytes,
    cause: Exception,
) -> None:
    try:
        _write_managed_bytes(
            unit_dir,
            "execution-authorizations.json",
            previous_ledger_bytes,
        )
    except Exception as restore_exc:  # pragma: no cover - secondary failure
        raise IntegrityError(
            "proof failed and authorization ledger could not be restored: "
            f"{restore_exc}"
        ) from cause


def execute_proof(
    path: str | Path,
    *,
    target: str,
    command: object,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    """Authorize, execute, and receipt a verification run inside Core."""
    unit_dir, argv = _validated_proof_request(path, command, timeout_seconds)
    with unit_lock(unit_dir):
        unit = _unit_json(unit_dir, "unit.json")
        if isinstance(unit.get("status"), str) and unit.get("status") in TERMINAL_STATUSES:
            raise LifecycleError(f"a {unit.get('status')} Unit cannot execute proofs")
        receipt = _unit_json(unit_dir, "context-receipt.json")
        project_root = _receipt_source_manifest_path(
            receipt, unit_dir=unit_dir
        ).parent.resolve()
        argv = _isolated_test_command(argv, project_root)
        envelope = _unit_json(unit_dir, "execution-envelope.json")
        previous_ledger_bytes = _unit_bytes(
            unit_dir, "execution-authorizations.json"
        )
        authorization = _authorize_action_locked(
            unit_dir,
            action="test",
            target=target,
            stage=None,
            method=None,
            credential_ref=None,
        )
        if authorization.get("allowed") is not True:
            raise LifecycleError(str(authorization.get("reason", "proof blocked")))
        try:
            run = _run_proof(project_root, argv, timeout_seconds)
            execution = _proof_execution_record(run, project_root)
            _persist_proof_receipt(
                unit_dir,
                unit=unit,
                envelope=envelope,
                authorization=authorization,
                execution=execution,
            )
        except Exception as exc:
            _restore_proof_ledger(unit_dir, previous_ledger_bytes, exc)
            raise
        return {
            **authorization,
            "passed": (
                run.exit_code == 0
                and not run.timed_out
                and not run.output_limit_exceeded
            ),
            "execution": execution,
            "evidence_command": {
                "authorization_id": authorization["authorization_id"],
            },
            "stdout": run.stdout,
            "stderr": run.stderr,
            "stdout_truncated": run.stdout_truncated,
            "stderr_truncated": run.stderr_truncated,
            "host_execution_required": False,
        }


def _approved_gate_exists(decisions: dict[str, Any], gate: str) -> bool:
    entries = decisions.get("decisions")
    return isinstance(entries, list) and any(
        isinstance(item, dict)
        and item.get("gate") == gate
        and item.get("outcome") == "approved"
        for item in entries
    )


def _acceptance_progress_only(before: bytes, after: bytes) -> bool:
    try:
        before_text = before.decode("utf-8")
        after_text = after.decode("utf-8")
    except UnicodeDecodeError:
        return False
    marker = ACCEPTANCE_CHECKBOX
    before_states = [item.group("state") for item in marker.finditer(before_text)]
    after_states = [item.group("state") for item in marker.finditer(after_text)]
    if not before_states or len(before_states) != len(after_states):
        return False
    normalizer = r"\g<prefix> \g<suffix>"
    if marker.sub(normalizer, before_text) != marker.sub(normalizer, after_text):
        return False
    return all(
        new.strip().lower() in {"", "x"}
        and (old.strip() == "" or new.strip().lower() == "x")
        for old, new in zip(before_states, after_states, strict=True)
    )


def write_unit_artifacts(
    path: str | Path,
    *,
    artifacts: object,
) -> dict[str, Any]:
    """Persist Unit documents through Core with gate and Amendment checks."""

    unit_dir = Path(path).expanduser().resolve()
    if not unit_dir.is_dir():
        raise WorkflowError(f"Unit directory does not exist: {unit_dir}")
    records = _normalize_change_records(artifacts)
    with unit_lock(unit_dir):
        unit = _unit_json(unit_dir, "unit.json")
        if isinstance(unit.get("status"), str) and unit.get(
            "status"
        ) in TERMINAL_STATUSES:
            raise LifecycleError(
                f"a {unit.get('status')} Unit cannot change Unit artifacts"
            )
        decisions = _unit_json(unit_dir, "decisions.json")
        amendment = amendment_status(unit_dir, unit=unit, decisions=decisions)
        if amendment["issues"]:
            raise IntegrityError(
                "Unit amendment history is invalid: " + "; ".join(amendment["issues"])
            )
        pending_by_artifact = {
            artifact
            for item in amendment["pending"]
            for artifact in item.get("affected_artifacts", [])
        }
        snapshots: list[tuple[str, bytes | None, int | None]] = []
        written: list[dict[str, str]] = []
        try:
            for record in records:
                relative = record["target"]
                if relative not in AMENDABLE_ARTIFACT_GATES:
                    raise WorkflowError(
                        f"Core artifact-write does not manage: {relative}"
                    )
                gate = AMENDABLE_ARTIFACT_GATES[relative]
                _unit_path_without_symlinks(unit_dir, relative)
                current_digest, before_content, before_mode = _current_file_snapshot(
                    unit_dir,
                    relative,
                )
                if current_digest != record["expected_digest"]:
                    raise IntegrityError(
                        f"Unit artifact precondition changed for {relative}: "
                        f"expected {record['expected_digest']}, found {current_digest}"
                    )
                if before_content is None:
                    raise IntegrityError(f"missing Unit artifact: {relative}")
                progress_only = relative == "acceptance.md" and _acceptance_progress_only(
                    before_content, record["content"]
                )
                if (
                    _approved_gate_exists(decisions, gate)
                    and relative not in pending_by_artifact
                    and not progress_only
                ):
                    raise LifecycleError(
                        f"{relative} is already approved; record an amendment before changing it"
                    )
                snapshots.append((relative, before_content, before_mode))
                _write_managed_bytes(
                    unit_dir,
                    relative,
                    record["content"],
                    mode=before_mode,
                )
                after_digest, _after_content, _after_mode = _current_file_snapshot(
                    unit_dir,
                    relative,
                )
                written.append(
                    {
                        "artifact": relative,
                        "before_digest": current_digest,
                        "after_digest": after_digest,
                    }
                )
        except Exception as exc:
            _restore_file_snapshots(unit_dir, snapshots, cause=exc)
            raise
        return {
            "written": True,
            "unit_id": unit.get("id"),
            "artifacts": written,
            "host_write_required": False,
        }
