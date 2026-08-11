from __future__ import annotations

import copy
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .evaluation import evaluate_all_evaluations
from .records import (
    _foundation_decision_digest,
    _foundation_decision_history_issues,
    _foundation_evidence_digest,
)
from .types import (
    FOUNDATION_CHECK_FIELDS,
    FOUNDATION_EVIDENCE_FIELDS,
    FOUNDATION_LOCK_NAME,
    FoundationError,
    FoundationRelease,
)
from .validation import (
    _load_foundation_documents,
    _load_json,
    _optional_json,
    _parse_timestamp,
    _safe_path,
    _validate_provenance_record,
    _write_json,
    load_foundation,
)
from ..support.files import (
    UnsafeControlFile,
    read_control_file,
    read_control_file_snapshot,
)
from ..support.jsonio import UnsafeWritePath, write_bytes_atomic_beneath
from ..support.locking import LockUnavailable, rooted_file_lock


def _latest_foundation_decision(
    foundation: FoundationRelease,
) -> tuple[dict[str, Any] | None, list[str]]:
    path = foundation.root / "decisions.json"
    if not path.is_file():
        return None, ["missing Foundation release Decision"]
    try:
        document = _load_json(path, root=foundation.root)
    except FoundationError as exc:
        return None, [str(exc)]
    if document.get("foundation_id") != foundation.manifest["id"]:
        return None, ["Foundation Decision foundation_id does not match release"]
    if document.get("version") != foundation.version:
        return None, ["Foundation Decision document version does not match release"]
    entries = document.get("decisions")
    if not isinstance(entries, list) or not entries:
        return None, ["Foundation release Decision list is empty"]
    issues = _foundation_decision_history_issues(
        foundation,
        entries,
        require_latest_approval=True,
    )
    latest = entries[-1]
    return (latest if isinstance(latest, dict) else None), issues


def _foundation_evidence_issues(
    foundation: FoundationRelease,
    evidence: dict[str, Any] | None,
    *,
    evaluations: dict[str, Any] | None = None,
) -> list[str]:
    if evidence is None:
        return ["missing Foundation release Evidence"]
    issues: list[str] = []
    missing = sorted(FOUNDATION_EVIDENCE_FIELDS - evidence.keys())
    if missing:
        issues.append(f"Foundation Evidence missing fields: {', '.join(missing)}")
    if evidence.get("foundation_id") != foundation.manifest["id"]:
        issues.append("Foundation Evidence foundation_id does not match release")
    if evidence.get("version") != foundation.version:
        issues.append("Foundation Evidence version does not match release")
    if evidence.get("approval_digest") != foundation.approval_digest:
        issues.append("Foundation Evidence approval_digest does not match release content")
    if evidence.get("type") != "foundation-release-evidence":
        issues.append("Foundation Evidence has an invalid type")
    if evidence.get("schema_version") != "1.0.0":
        issues.append("Foundation Evidence has an unsupported schema_version")
    evidence_digest = evidence.get("evidence_digest")
    if not isinstance(evidence_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", evidence_digest
    ):
        issues.append("Foundation Evidence evidence_digest must be a SHA-256 digest")
    elif evidence_digest != _foundation_evidence_digest(evidence):
        issues.append("Foundation Evidence digest does not match its record")
    if evidence.get("passed") is not True:
        issues.append("Foundation release Evidence is not passing")
    if not isinstance(evidence.get("scope"), str) or not evidence.get("scope", "").strip():
        issues.append("Foundation Evidence requires a scope")
    if not isinstance(evidence.get("recorded_by"), str) or not evidence.get("recorded_by", "").strip():
        issues.append("Foundation Evidence requires recorded_by provenance")
    attestation = evidence.get("attestation")
    if attestation is not None:
        if not isinstance(attestation, dict):
            issues.append("Foundation Evidence attestation must be an object")
        else:
            if attestation.get("type") != "local-evaluation-attestation":
                issues.append("Foundation Evidence attestation has an invalid type")
            if attestation.get("reported_actor") != evidence.get("recorded_by"):
                issues.append(
                    "Foundation Evidence attestation reported_actor must match recorded_by"
                )
            if attestation.get("execution_verification") != "not-performed-by-core":
                issues.append(
                    "Foundation Evidence attestation must disclose the Core execution boundary"
                )
            if attestation.get("identity_verification") != "not-performed-by-core":
                issues.append(
                    "Foundation Evidence attestation must disclose the Core identity boundary"
                )
    if not isinstance(evidence.get("id"), str) or not evidence.get("id", "").strip():
        issues.append("Foundation Evidence requires a non-empty id")
    recorded_at: datetime | None = None
    try:
        recorded_at = _parse_timestamp(
            evidence.get("recorded_at"), "Foundation Evidence recorded_at"
        )
    except FoundationError as exc:
        issues.append(str(exc))
    checks = evidence.get("checks")
    if not isinstance(checks, list) or not checks:
        issues.append("Foundation Evidence has no checks")
    else:
        seen_check_ids: set[str] = set()
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                issues.append(f"Foundation Evidence check {index} must be an object")
                continue
            missing_check = sorted(FOUNDATION_CHECK_FIELDS - check.keys())
            if missing_check:
                issues.append(
                    f"Foundation Evidence check {index} missing fields: "
                    f"{', '.join(missing_check)}"
                )
            if check.get("passed") is not True:
                issues.append(f"Foundation Evidence check {index} is not passing")
            check_id = check.get("id")
            if not isinstance(check_id, str) or not check_id.strip():
                issues.append(f"Foundation Evidence check {index} requires a non-empty id")
            elif check_id in seen_check_ids:
                issues.append(f"Foundation Evidence has duplicate check id: {check_id}")
            else:
                seen_check_ids.add(check_id)
            if not isinstance(check.get("details"), str) or not check.get("details", "").strip():
                issues.append(f"Foundation Evidence check {index} requires details")
            try:
                _validate_provenance_record(check.get("provenance"), f"Foundation Evidence check {index} provenance")
                check_recorded_at = _parse_timestamp(
                    check["provenance"].get("recorded_at"),
                    f"Foundation Evidence check {index} provenance recorded_at",
                )
                if recorded_at is not None and check_recorded_at > recorded_at:
                    issues.append(
                        f"Foundation Evidence check {index} provenance recorded_at "
                        "is after Evidence recorded_at"
                    )
            except FoundationError as exc:
                issues.append(str(exc))
        try:
            graded = evaluations if evaluations is not None else evaluate_all_evaluations(foundation)
            expected = set(graded["evaluations"])
            actual = {check.get("id") for check in checks if isinstance(check, dict)}
            missing_groups = sorted(expected - actual)
            if missing_groups:
                issues.append("Foundation Evidence missing evaluation checks: " + ", ".join(missing_groups))
        except FoundationError as exc:
            issues.append(str(exc))
    return issues


def _approval_blockers(
    foundation: FoundationRelease,
    *,
    evaluations: dict[str, Any] | None = None,
) -> list[str]:
    decision, decision_issues = _latest_foundation_decision(foundation)
    blockers = list(decision_issues)
    if decision is not None and decision.get("outcome") != "approved":
        blockers.append("latest Foundation release Decision is not approved")
    evidence_path = foundation.root / "evidence/release.json"
    try:
        evidence = _optional_json(evidence_path, root=foundation.root)
    except FoundationError as exc:
        evidence = None
        blockers.append(str(exc))
    graded = evaluations
    if graded is None:
        try:
            graded = evaluate_all_evaluations(foundation)
        except FoundationError as exc:
            blockers.append(str(exc))
    blockers.extend(_foundation_evidence_issues(foundation, evidence, evaluations=graded))
    if graded is not None:
        for evaluation_id, result in graded["evaluations"].items():
            if result["passed"] is not True:
                blockers.append(f"evaluation group {evaluation_id} did not pass")
    return blockers


def record_foundation_decision(
    root: str | Path,
    *,
    outcome: str,
    summary: str,
    decided_by: str,
) -> dict[str, Any]:
    foundation_root = Path(root).resolve()
    try:
        with rooted_file_lock(
            foundation_root,
            FOUNDATION_LOCK_NAME,
            subject="Foundation release",
        ):
            return _record_foundation_decision_locked(
                foundation_root,
                outcome=outcome,
                summary=summary,
                decided_by=decided_by,
            )
    except LockUnavailable as exc:
        raise FoundationError(str(exc)) from exc


def _record_foundation_decision_locked(
    root: str | Path,
    *,
    outcome: str,
    summary: str,
    decided_by: str,
) -> dict[str, Any]:
    foundation = load_foundation(root)
    if foundation.manifest["status"] != "draft":
        raise FoundationError("Foundation Decision can only be recorded for a draft release")
    if not isinstance(outcome, str) or outcome not in {"approved", "rejected"}:
        raise FoundationError("Foundation Decision outcome must be approved or rejected")
    if not isinstance(summary, str) or not summary.strip():
        raise FoundationError("Foundation Decision summary must be non-empty")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise FoundationError("Foundation Decision decided_by must be non-empty")

    path = foundation.root / "decisions.json"
    document = _optional_json(path, root=foundation.root) or {
        "foundation_id": foundation.manifest["id"],
        "version": foundation.version,
        "decisions": [],
    }
    if document.get("foundation_id") != foundation.manifest["id"]:
        raise FoundationError("Foundation Decision foundation_id does not match release")
    if document.get("version") != foundation.version:
        raise FoundationError("Foundation Decision document version does not match release")
    entries = document.get("decisions")
    if not isinstance(entries, list):
        raise FoundationError("Foundation decisions must be a list")
    existing_issues = _foundation_decision_history_issues(
        foundation,
        entries,
        require_latest_approval=False,
    )
    if existing_issues:
        raise FoundationError(
            "existing Foundation Decision history is invalid: "
            + "; ".join(existing_issues)
        )
    preceding_ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
    now = datetime.now(timezone.utc)
    if entries:
        previous_decided_at = _parse_timestamp(
            entries[-1].get("decided_at"),
            "latest Foundation Decision decided_at",
        )
        if now <= previous_decided_at:
            now = previous_decided_at + timedelta(microseconds=1)
    decision = {
        "id": "DEC-FND-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "foundation-release-decision",
        "schema_version": "1.0.0",
        "foundation_id": foundation.manifest["id"],
        "version": foundation.version,
        "approval_digest": foundation.approval_digest,
        "outcome": outcome,
        "summary": summary.strip(),
        "decided_by": decided_by.strip(),
        "decided_at": now.isoformat(),
        "attestation": {
            "type": "human-decision-attestation",
            "reported_actor": decided_by.strip(),
            "identity_verification": "not-performed-by-core",
            "confirmation_source": "caller-attested",
        },
        "previous_decision_digest": (
            entries[-1].get("decision_digest") if entries else None
        ),
    }
    decision["decision_digest"] = _foundation_decision_digest(decision)
    entries.append(decision)
    document["version"] = foundation.version
    _write_json(path, document, root=foundation.root)
    persisted = _load_json(path, root=foundation.root).get("decisions", [])
    persisted_ids = [entry.get("id") for entry in persisted if isinstance(entry, dict)]
    if persisted_ids != [*preceding_ids, decision["id"]]:
        raise FoundationError(
            "Foundation Decision postflight blocked: the ledger changed during the write"
        )
    return {"path": str(path), "decision": decision}


def record_foundation_evidence(
    root: str | Path,
    *,
    passed: bool,
    checks: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
) -> dict[str, Any]:
    foundation_root = Path(root).resolve()
    try:
        with rooted_file_lock(
            foundation_root,
            FOUNDATION_LOCK_NAME,
            subject="Foundation release",
        ):
            return _record_foundation_evidence_locked(
                foundation_root,
                passed=passed,
                checks=checks,
                scope=scope,
                recorded_by=recorded_by,
            )
    except LockUnavailable as exc:
        raise FoundationError(str(exc)) from exc


def _record_foundation_evidence_locked(
    root: str | Path,
    *,
    passed: bool,
    checks: list[dict[str, Any]],
    scope: str,
    recorded_by: str,
) -> dict[str, Any]:
    foundation = load_foundation(root)
    if not isinstance(passed, bool):
        raise FoundationError("Foundation Evidence passed must be boolean")
    if not isinstance(checks, list) or not checks:
        raise FoundationError("Foundation Evidence checks must be a non-empty list")
    if not isinstance(scope, str) or not scope.strip():
        raise FoundationError("Foundation Evidence scope must be non-empty")
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise FoundationError("Foundation Evidence recorded_by must be non-empty")
    now = datetime.now(timezone.utc)
    seen_check_ids: set[str] = set()
    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            raise FoundationError(f"Foundation Evidence check {index} must be an object")
        if not FOUNDATION_CHECK_FIELDS <= check.keys():
            missing = sorted(FOUNDATION_CHECK_FIELDS - check.keys())
            raise FoundationError(
                f"Foundation Evidence check {index} missing fields: {', '.join(missing)}"
            )
        if not isinstance(check["passed"], bool):
            raise FoundationError(f"Foundation Evidence check {index} passed must be boolean")
        if not isinstance(check["details"], str) or not check["details"].strip():
            raise FoundationError(f"Foundation Evidence check {index} details must be non-empty")
        check_id = check.get("id")
        if not isinstance(check_id, str) or not check_id.strip():
            raise FoundationError(f"Foundation Evidence check {index} id must be non-empty")
        if check_id in seen_check_ids:
            raise FoundationError(f"Foundation Evidence has duplicate check id: {check_id}")
        seen_check_ids.add(check_id)
        provenance_label = f"Foundation Evidence check {index} provenance"
        _validate_provenance_record(check.get("provenance"), provenance_label)
        check_recorded_at = _parse_timestamp(
            check["provenance"].get("recorded_at"),
            f"{provenance_label} recorded_at",
        )
        if check_recorded_at > now:
            raise FoundationError(
                f"{provenance_label} recorded_at is after Evidence recorded_at"
            )
    if passed and any(check["passed"] is not True for check in checks):
        raise FoundationError("passing Foundation Evidence cannot contain failed checks")

    evidence = {
        "id": "EVD-FND-" + now.strftime("%Y%m%d%H%M%S%f"),
        "type": "foundation-release-evidence",
        "schema_version": "1.0.0",
        "foundation_id": foundation.manifest["id"],
        "version": foundation.version,
        "approval_digest": foundation.approval_digest,
        "passed": passed,
        "scope": scope.strip(),
        "recorded_by": recorded_by.strip(),
        "recorded_at": now.isoformat(),
        "attestation": {
            "type": "local-evaluation-attestation",
            "reported_actor": recorded_by.strip(),
            "execution_verification": "not-performed-by-core",
            "identity_verification": "not-performed-by-core",
        },
        "checks": checks,
    }
    evidence["evidence_digest"] = _foundation_evidence_digest(evidence)
    path = foundation.root / "evidence/release.json"
    _write_json(path, evidence, root=foundation.root)
    persisted = _load_json(path, root=foundation.root)
    if persisted.get("evidence_digest") != _foundation_evidence_digest(persisted):
        raise FoundationError("Foundation Evidence postflight digest check failed")
    return {"path": str(path), "evidence": evidence}


def plan_foundation_promotion(root: str | Path) -> dict[str, Any]:
    """Return a deterministic, side-effect-free Foundation promotion plan."""
    foundation = load_foundation(root)
    descriptors = sorted(
        foundation.manifest["artifacts"],
        key=lambda item: (str(item["path"]), str(item["id"])),
    )
    targets = [
        {
            "id": foundation.manifest["id"],
            "kind": foundation.manifest["kind"],
            "path": "release.json",
            "version": foundation.version,
            "from_status": foundation.manifest["status"],
            "to_status": "approved",
        }
    ]
    targets.extend(
        {
            "id": descriptor["id"],
            "kind": descriptor["kind"],
            "path": descriptor["path"],
            "version": descriptor["version"],
            "from_status": foundation.assets[descriptor["id"]]["status"],
            "to_status": "approved",
        }
        for descriptor in descriptors
    )
    blockers = _approval_blockers(foundation)
    return {
        "foundation_id": foundation.manifest["id"],
        "version": foundation.version,
        "target_count": len(targets),
        "targets": targets,
        # Keep the explicit name convenient for callers that treat this as a plan document.
        "plan": targets,
        "blockers": blockers,
    }


@dataclass(frozen=True)
class _PromotionFile:
    path: Path
    original_bytes: bytes
    original_mode: int
    new_bytes: bytes


def _json_bytes(value: dict[str, Any]) -> bytes:
    try:
        return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FoundationError(f"promotion produced non-serializable JSON: {exc}") from exc


def _write_staged_json(path: Path, content: bytes, mode: int) -> Path:
    """Write and fsync a private temporary file without touching its target tree."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".isekai-", suffix=".promotion-tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _replace_staged(temporary: Path, target: Path, *, root: Path) -> None:
    try:
        relative = target.relative_to(root)
        write_bytes_atomic_beneath(
            root,
            relative,
            temporary.read_bytes(),
            mode=stat.S_IMODE(temporary.stat().st_mode),
        )
    except (ValueError, UnsafeWritePath) as exc:
        raise FoundationError(f"unsafe Foundation promotion target {target}: {exc}") from exc
    temporary.unlink()


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_original(file: _PromotionFile, *, root: Path) -> None:
    try:
        relative = file.path.relative_to(root)
        write_bytes_atomic_beneath(
            root,
            relative,
            file.original_bytes,
            mode=file.original_mode,
        )
    except (ValueError, UnsafeWritePath) as exc:
        raise FoundationError(
            f"unsafe Foundation promotion rollback target {file.path}: {exc}"
        ) from exc


def _postflight_promotion(root: Path, expected_count: int) -> dict[str, Any]:
    promoted = load_foundation(root)
    if promoted.manifest["status"] != "approved" or len(promoted.assets) != expected_count - 1:
        raise FoundationError("promotion postflight status/count check failed")
    if any(asset["status"] != "approved" for asset in promoted.assets.values()):
        raise FoundationError("promotion postflight found an unapproved asset")
    if any(entry["status"] != "approved" for entry in promoted.knowledge_entries()):
        raise FoundationError("promotion postflight found an unapproved Knowledge entry")
    readiness = promoted.readiness()
    if readiness["ready"] is not True:
        raise FoundationError(
            "promotion postflight readiness failed: " + "; ".join(readiness["blockers"])
        )
    return {"summary": promoted.summary(), "readiness": readiness}


def _preflight_promotion(
    foundation: FoundationRelease,
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[Path, _PromotionFile]]:
    """Validate every promoted JSON document before any target is changed."""
    manifest = copy.deepcopy(foundation.manifest)
    manifest["status"] = "approved"
    files: dict[Path, _PromotionFile] = {}
    for target in plan["targets"]:
        path = foundation.root / target["path"]
        try:
            original_bytes, original_metadata = read_control_file_snapshot(
                path,
                root=foundation.root,
                label="Foundation promotion target",
            )
        except (OSError, UnsafeControlFile) as exc:
            raise FoundationError(
                f"cannot safely read Foundation promotion target {path}: {exc}"
            ) from exc
        try:
            current_value = json.loads(original_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FoundationError(
                f"Foundation promotion target changed to invalid JSON: {path}: {exc}"
            ) from exc
        expected_value = (
            foundation.manifest
            if target["id"] == manifest["id"]
            else foundation.assets[target["id"]]
        )
        if current_value != expected_value:
            raise FoundationError(
                f"Foundation promotion target changed after approval validation: {path}"
            )
        files[path] = _PromotionFile(
            path=path,
            original_bytes=original_bytes,
            original_mode=stat.S_IMODE(original_metadata.st_mode),
            new_bytes=b"",
        )
        if target["id"] == manifest["id"]:
            value = manifest
        else:
            value = copy.deepcopy(foundation.assets[target["id"]])
            value["status"] = "approved"
            if value.get("kind") == "knowledge":
                for entry in value.get("content", {}).get("entries", []):
                    entry["status"] = "approved"
        serialized = _json_bytes(value)
        files[path] = _PromotionFile(
            path=files[path].path,
            original_bytes=files[path].original_bytes,
            original_mode=files[path].original_mode,
            new_bytes=serialized,
        )
    document_by_path = {
        path: file.new_bytes
        for path, file in files.items()
        if path != foundation.root / "release.json"
    }

    def memory_loader(path: Path) -> dict[str, Any]:
        if path not in document_by_path:
            return _load_json(path, root=foundation.root)
        try:
            value = json.loads(document_by_path[path].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FoundationError(f"promotion produced invalid JSON in {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise FoundationError(f"promotion produced a non-object JSON document: {path}")
        return value

    candidate = _load_foundation_documents(foundation.root, manifest, document_loader=memory_loader)
    if candidate.manifest["status"] != "approved" or any(
        asset["status"] != "approved" for asset in candidate.assets.values()
    ):
        raise FoundationError("promotion preflight did not produce all approved documents")
    return plan, files


def promote_foundation(root: str | Path, *, dry_run: bool = False) -> dict[str, Any]:
    if not isinstance(dry_run, bool):
        raise FoundationError("dry_run must be boolean")
    foundation_root = Path(root).resolve()
    if dry_run:
        return _promote_foundation_locked(foundation_root, dry_run=True)
    try:
        with rooted_file_lock(
            foundation_root,
            FOUNDATION_LOCK_NAME,
            subject="Foundation release",
        ):
            return _promote_foundation_locked(foundation_root, dry_run=False)
    except LockUnavailable as exc:
        raise FoundationError(str(exc)) from exc


def _promote_foundation_locked(
    root: str | Path,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Promote a Foundation transactionally; ``dry_run`` never mutates the root.

    The default remains an executing call for compatibility with the original API.
    """
    foundation_root = Path(root).resolve()
    try:
        plan = plan_foundation_promotion(foundation_root)
    except FoundationError as exc:
        if dry_run:
            return {"promoted": False, "dry_run": True, "target_count": 0, "plan": [], "blockers": [str(exc)]}
        raise

    blockers = list(plan["blockers"])
    already_approved = foundation_root.joinpath("release.json").is_file() and plan["targets"][0]["from_status"] == "approved"
    if dry_run:
        return {
            "promoted": False,
            "dry_run": True,
            "already_approved": already_approved,
            "target_count": plan["target_count"],
            "plan": plan["targets"],
            "blockers": blockers,
        }
    if already_approved:
        raise FoundationError("Foundation is already approved")
    if plan["targets"][0]["from_status"] != "draft":
        raise FoundationError("only a draft Foundation can be promoted")
    if blockers:
        raise FoundationError("Foundation cannot be promoted: " + "; ".join(blockers))

    foundation = load_foundation(foundation_root)
    refreshed_blockers = _approval_blockers(foundation)
    if refreshed_blockers:
        raise FoundationError(
            "Foundation changed after promotion planning: "
            + "; ".join(refreshed_blockers)
        )
    approved_digest = foundation.approval_digest
    _, files = _preflight_promotion(foundation, plan)
    if foundation.approval_digest != approved_digest:
        raise FoundationError(
            "Foundation content changed during promotion preflight"
        )
    ordered_files = [files[foundation_root / target["path"]] for target in plan["targets"]]
    staged: list[tuple[Path, _PromotionFile]] = []
    commit_started = False
    try:
        for file in ordered_files:
            staged.append((_write_staged_json(file.path, file.new_bytes, file.original_mode), file))
        final_blockers = _approval_blockers(foundation)
        if foundation.approval_digest != approved_digest or final_blockers:
            details = final_blockers or ["approval digest changed"]
            raise FoundationError(
                "Foundation changed while promotion files were staged: "
                + "; ".join(details)
            )
        commit_started = True
        for temporary, file in staged:
            _replace_staged(temporary, file.path, root=foundation_root)
        _fsync_directory(foundation_root)
        postflight = _postflight_promotion(foundation_root, plan["target_count"])
        return {
            "promoted": True,
            "dry_run": False,
            "target_count": plan["target_count"],
            "plan": plan["targets"],
            **postflight,
        }
    except Exception as exc:
        rollback_errors: list[str] = []
        if commit_started:
            for file in ordered_files:
                try:
                    _restore_original(file, root=foundation_root)
                except Exception as rollback_exc:  # pragma: no cover - filesystem failure path
                    rollback_errors.append(str(rollback_exc))
            try:
                _fsync_directory(foundation_root)
            except Exception as rollback_exc:  # pragma: no cover - filesystem failure path
                rollback_errors.append(str(rollback_exc))
        detail = f"Foundation promotion rolled back: {exc}"
        if rollback_errors:
            detail += "; rollback errors: " + "; ".join(rollback_errors)
        raise FoundationError(detail) from exc
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
