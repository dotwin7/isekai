from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .jsonio import write_json_atomic
from .locking import file_lock


ALLOWED_STATUSES = {"draft", "approved", "deprecated"}
# Evaluation fixtures are graded against a fixed instant so that a released
# Foundation keeps the same verdict regardless of when it is re-checked.
EVALUATION_CLOCK = datetime(2026, 8, 5, tzinfo=timezone.utc)
FOUNDATION_LOCK_NAME = ".isekai-foundation.lock"
ALLOWED_ASSET_KINDS = {
    "schema", "profile", "extension", "rule-set", "policy", "semantic-mapping", "knowledge", "evaluation",
    "gate-matrix", "agent-execution-contract", "human-gate-contract", "exception-contract",
    "semantic-contract", "knowledge-contract", "unit-dod-evaluation-contract",
}
CONDITION_TYPES = {
    "extension-cannot-weaken-must", "required-artifact", "context-scope",
    "required-decision", "required-envelope", "required-lineage",
    "required-promotion-review", "required-exception-controls", "required-dod",
}
EVALUATOR_TYPES = {
    "required-decision", "required-envelope", "required-lineage",
    "required-promotion-review", "required-exception-controls", "required-dod",
}
REQUIRED_ASSET_FIELDS = {
    "id", "kind", "version", "schema_version", "status", "owner", "provenance",
    "classification", "scope", "content",
}
FOUNDATION_DECISION_FIELDS = {
    "id",
    "type",
    "schema_version",
    "foundation_id",
    "version",
    "approval_digest",
    "outcome",
    "summary",
    "decided_by",
    "decided_at",
}
FOUNDATION_EVIDENCE_FIELDS = {
    "id",
    "type",
    "schema_version",
    "foundation_id",
    "version",
    "approval_digest",
    "passed",
    "scope",
    "recorded_by",
    "recorded_at",
    "checks",
}
FOUNDATION_CHECK_FIELDS = {"id", "passed", "details", "provenance"}


class FoundationError(ValueError):
    """Raised when a Foundation release violates its structural contract."""


@dataclass(frozen=True)
class FoundationRelease:
    root: Path
    manifest: dict[str, Any]
    assets: dict[str, dict[str, Any]]

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    def assets_by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [asset for asset in self.assets.values() if asset["kind"] == kind]

    @property
    def contract_digest(self) -> str:
        """Identify the immutable release manifest and every registered contract asset."""
        digest = hashlib.sha256()
        paths = [Path("release.json")]
        paths.extend(
            Path(str(descriptor["path"]))
            for descriptor in self.manifest.get("artifacts", [])
        )
        for relative in sorted(paths, key=lambda item: item.as_posix()):
            content = (self.root / relative).read_bytes()
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(len(content)).encode("ascii"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
        return "sha256:" + digest.hexdigest()

    @property
    def approval_digest(self) -> str:
        """Bind approval to semantic release content while ignoring promotion status."""
        digest = hashlib.sha256()
        paths = [Path("release.json")]
        paths.extend(
            Path(str(descriptor["path"]))
            for descriptor in self.manifest.get("artifacts", [])
        )
        for relative in sorted(paths, key=lambda item: item.as_posix()):
            try:
                value = json.loads((self.root / relative).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - load already validates
                raise FoundationError(
                    f"cannot calculate Foundation approval digest for {relative}: {exc}"
                ) from exc
            if not isinstance(value, dict):  # pragma: no cover - load already validates
                raise FoundationError(
                    f"Foundation approval digest requires an object: {relative}"
                )
            subject = copy.deepcopy(value)
            subject.pop("status", None)
            content = json.dumps(
                subject,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
        return "sha256:" + digest.hexdigest()

    def rules(self) -> Iterator[dict[str, Any]]:
        for asset in self.assets_by_kind("rule-set"):
            yield from asset["content"].get("rules", [])

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for asset in self.assets.values():
            kind = str(asset["kind"])
            kinds[kind] = kinds.get(kind, 0) + 1
        return {
            "id": self.manifest["id"],
            "version": self.version,
            "contract_digest": self.contract_digest,
            "approval_digest": self.approval_digest,
            "status": self.manifest["status"],
            "asset_count": len(self.assets),
            "kinds": dict(sorted(kinds.items())),
        }

    def readiness(self) -> dict[str, Any]:
        blockers: list[str] = []
        if self.manifest["status"] != "approved":
            blockers.append(
                f"Foundation release status is {self.manifest['status']}; human approval is required"
            )
        for asset in sorted(self.assets.values(), key=lambda item: item["id"]):
            if asset["status"] != "approved":
                blockers.append(
                    f"asset {asset['id']} status is {asset['status']}; human approval is required"
                )
        # Grade the evaluation matrix once and reuse it; approval blockers report
        # the same failures, so the results must not be recomputed per consumer.
        evaluations = evaluate_all_evaluations(self)
        blockers.extend(_approval_blockers(self, evaluations=evaluations))
        return {
            "ready": not blockers,
            "summary": self.summary(),
            "evaluations": evaluations,
            "blockers": list(dict.fromkeys(blockers)),
        }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FoundationError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FoundationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FoundationError(f"expected JSON object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    write_json_atomic(path, value)


def _optional_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return _load_json(path)


def _latest_foundation_decision(
    foundation: FoundationRelease,
) -> tuple[dict[str, Any] | None, list[str]]:
    path = foundation.root / "decisions.json"
    if not path.is_file():
        return None, ["missing Foundation release Decision"]
    try:
        document = _load_json(path)
    except FoundationError as exc:
        return None, [str(exc)]
    if document.get("foundation_id") != foundation.manifest["id"]:
        return None, ["Foundation Decision foundation_id does not match release"]
    if document.get("version") != foundation.version:
        return None, ["Foundation Decision document version does not match release"]
    entries = document.get("decisions")
    if not isinstance(entries, list) or not entries:
        return None, ["Foundation release Decision list is empty"]
    latest = entries[-1]
    if not isinstance(latest, dict):
        return None, ["latest Foundation release Decision must be an object"]
    missing = sorted(FOUNDATION_DECISION_FIELDS - latest.keys())
    if missing:
        return None, [f"Foundation Decision missing fields: {', '.join(missing)}"]
    if latest.get("foundation_id") != foundation.manifest["id"]:
        return None, ["Foundation Decision foundation_id does not match release"]
    if latest.get("version") != foundation.version:
        return None, ["Foundation Decision version does not match release"]
    if latest.get("approval_digest") != foundation.approval_digest:
        return None, ["Foundation Decision approval_digest does not match release content"]
    if latest.get("outcome") not in {"approved", "rejected"}:
        return None, ["Foundation Decision has an invalid outcome"]
    issues: list[str] = []
    if latest.get("type") != "foundation-release-decision":
        issues.append("Foundation Decision has an invalid type")
    if latest.get("schema_version") != "1.0.0":
        issues.append("Foundation Decision has an unsupported schema_version")
    for field in ("id", "summary", "decided_by"):
        if not isinstance(latest.get(field), str) or not latest.get(field, "").strip():
            issues.append(f"Foundation Decision requires a non-empty {field}")
    try:
        _parse_timestamp(latest.get("decided_at"), "Foundation Decision decided_at")
    except FoundationError as exc:
        issues.append(str(exc))
    return latest, issues


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
    if evidence.get("passed") is not True:
        issues.append("Foundation release Evidence is not passing")
    if not isinstance(evidence.get("scope"), str) or not evidence.get("scope", "").strip():
        issues.append("Foundation Evidence requires a scope")
    if not isinstance(evidence.get("recorded_by"), str) or not evidence.get("recorded_by", "").strip():
        issues.append("Foundation Evidence requires recorded_by provenance")
    if not isinstance(evidence.get("id"), str) or not evidence.get("id", "").strip():
        issues.append("Foundation Evidence requires a non-empty id")
    try:
        _parse_timestamp(evidence.get("recorded_at"), "Foundation Evidence recorded_at")
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
        evidence = _optional_json(evidence_path)
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
    foundation = load_foundation(root)
    if foundation.manifest["status"] != "draft":
        raise FoundationError("Foundation Decision can only be recorded for a draft release")
    if outcome not in {"approved", "rejected"}:
        raise FoundationError("Foundation Decision outcome must be approved or rejected")
    if not isinstance(summary, str) or not summary.strip():
        raise FoundationError("Foundation Decision summary must be non-empty")
    if not isinstance(decided_by, str) or not decided_by.strip():
        raise FoundationError("Foundation Decision decided_by must be non-empty")

    path = foundation.root / "decisions.json"
    # decisions.json is an append-only ledger, so concurrent recorders would
    # otherwise silently drop each other's Decisions.
    with file_lock(foundation.root / FOUNDATION_LOCK_NAME, subject="Foundation release"):
        document = _optional_json(path) or {
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
        preceding_ids = [entry.get("id") for entry in entries if isinstance(entry, dict)]
        now = datetime.now(timezone.utc)
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
        }
        entries.append(decision)
        document["version"] = foundation.version
        _write_json(path, document)
        persisted = _load_json(path).get("decisions", [])
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
    foundation = load_foundation(root)
    if not isinstance(passed, bool):
        raise FoundationError("Foundation Evidence passed must be boolean")
    if not isinstance(checks, list) or not checks:
        raise FoundationError("Foundation Evidence checks must be a non-empty list")
    if not isinstance(scope, str) or not scope.strip():
        raise FoundationError("Foundation Evidence scope must be non-empty")
    if not isinstance(recorded_by, str) or not recorded_by.strip():
        raise FoundationError("Foundation Evidence recorded_by must be non-empty")
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
        _validate_provenance_record(check.get("provenance"), f"Foundation Evidence check {index} provenance")
    if passed and any(check["passed"] is not True for check in checks):
        raise FoundationError("passing Foundation Evidence cannot contain failed checks")

    now = datetime.now(timezone.utc)
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
        "checks": checks,
    }
    path = foundation.root / "evidence/release.json"
    _write_json(path, evidence)
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
    """Write and fsync a same-directory temporary file without touching its target."""
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".promotion-tmp", dir=path.parent
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


def _replace_staged(temporary: Path, target: Path) -> None:
    os.replace(temporary, target)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _restore_original(file: _PromotionFile) -> None:
    with file.path.open("wb") as handle:
        handle.write(file.original_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(file.path, file.original_mode)


def _postflight_promotion(root: Path, expected_count: int) -> dict[str, Any]:
    promoted = load_foundation(root)
    if promoted.manifest["status"] != "approved" or len(promoted.assets) != expected_count - 1:
        raise FoundationError("promotion postflight status/count check failed")
    if any(asset["status"] != "approved" for asset in promoted.assets.values()):
        raise FoundationError("promotion postflight found an unapproved asset")
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
        files[path] = _PromotionFile(
            path=path,
            original_bytes=path.read_bytes(),
            original_mode=stat.S_IMODE(path.stat().st_mode),
            new_bytes=b"",
        )
        if target["id"] == manifest["id"]:
            value = manifest
        else:
            value = copy.deepcopy(foundation.assets[target["id"]])
            value["status"] = "approved"
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
            return _load_json(path)
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
    _, files = _preflight_promotion(foundation, plan)
    ordered_files = [files[foundation_root / target["path"]] for target in plan["targets"]]
    staged: list[tuple[Path, _PromotionFile]] = []
    commit_started = False
    try:
        for file in ordered_files:
            staged.append((_write_staged_json(file.path, file.new_bytes, file.original_mode), file))
        commit_started = True
        for temporary, file in staged:
            _replace_staged(temporary, file.path)
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
                    _restore_original(file)
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


def _safe_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise FoundationError("path must be a non-empty relative path")
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise FoundationError(f"unsafe path: {relative}")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise FoundationError(f"path escapes Foundation root: {relative}") from exc
    return candidate


def _require_fields(value: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(fields - value.keys())
    if missing:
        raise FoundationError(f"{label} missing fields: {', '.join(missing)}")


def _reference_id(reference: Any, label: str) -> str:
    if isinstance(reference, str) and reference.strip():
        raise FoundationError(f"{label} must pin a parent version: {reference}")
    if not isinstance(reference, dict):
        raise FoundationError(f"{label} must be a pinned reference object")
    _require_fields(reference, {"id", "version"}, label)
    if not all(isinstance(reference[key], str) and reference[key].strip() for key in ("id", "version")):
        raise FoundationError(f"{label} id and version must be non-empty strings")
    return reference["id"]


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise FoundationError(f"{label} must be a concrete ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FoundationError(f"{label} must be a concrete ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _validate_provenance_record(provenance: Any, label: str) -> None:
    if not isinstance(provenance, dict):
        raise FoundationError(f"{label} must be an object")
    _require_fields(provenance, {"source", "recorded_by", "recorded_at"}, label)
    for key in ("source", "recorded_by"):
        if not isinstance(provenance[key], str) or not provenance[key].strip() or "<" in provenance[key]:
            raise FoundationError(f"{label} {key} must be concrete")
    _parse_timestamp(provenance["recorded_at"], f"{label} recorded_at")


def _validate_provenance(asset: dict[str, Any]) -> None:
    _validate_provenance_record(asset.get("provenance"), f"{asset['id']} provenance")
    for key in ("owner", "classification", "scope", "schema_version", "version"):
        if not isinstance(asset.get(key), str) or not asset[key].strip():
            raise FoundationError(f"{asset['id']} requires a non-empty {key}")
    if asset["schema_version"] != "1.0.0":
        raise FoundationError(f"{asset['id']} has an unsupported schema_version")


def _validate_rule_metadata(rule: dict[str, Any], label: str) -> None:
    _require_fields(rule, {"owner", "provenance", "applies_to"}, label)
    if not isinstance(rule["owner"], str) or not rule["owner"].strip():
        raise FoundationError(f"{label} requires an owner")
    _validate_provenance_record(rule["provenance"], f"{label} provenance")
    applies_to = rule["applies_to"]
    if not isinstance(applies_to, list) or not applies_to or any(
        not isinstance(item, str) or not item.strip() for item in applies_to
    ):
        raise FoundationError(f"{label} requires non-empty applies_to values")


def _require_condition_fields(condition: dict[str, Any], fields: set[str], rule_id: str) -> None:
    _require_fields(condition, fields, f"{rule_id} condition")


def _require_condition_strings(condition: dict[str, Any], fields: set[str], rule_id: str) -> None:
    for field in fields:
        if not isinstance(condition.get(field), str) or not condition[field].strip():
            raise FoundationError(f"{rule_id} condition {field} must be a non-empty string")


def _validate_condition(condition: Any, rule_id: str) -> None:
    if not isinstance(condition, dict) or condition.get("type") not in CONDITION_TYPES:
        raise FoundationError(f"{rule_id} has an unsupported condition")
    condition_type = condition["type"]
    if condition_type == "extension-cannot-weaken-must":
        _require_condition_fields(condition, {"parent_asset", "parent_rule_id", "parent_level", "comparison"}, rule_id)
        _require_condition_strings(condition, {"parent_asset", "parent_rule_id", "parent_level", "comparison"}, rule_id)
        if condition["parent_level"] != "MUST" or condition["comparison"] not in {"preserve-or-strengthen", "equal-or-stronger"}:
            raise FoundationError(f"{rule_id} extension condition requires a MUST parent and valid comparison")
    elif condition_type == "required-artifact":
        _require_condition_fields(condition, {"artifact", "field", "equals"}, rule_id)
        if not all(isinstance(condition[key], str) and condition[key].strip() for key in ("artifact", "field")):
            raise FoundationError(f"{rule_id} required-artifact paths must be non-empty")
        if "commands_required" in condition and not isinstance(condition["commands_required"], bool):
            raise FoundationError(f"{rule_id} commands_required must be boolean")
    elif condition_type == "context-scope":
        fields = condition.get("required_fields")
        if not isinstance(fields, list) or not fields or any(not isinstance(item, str) or not item for item in fields):
            raise FoundationError(f"{rule_id} context-scope needs required_fields")
        if "allowed_routes" in condition and (not isinstance(condition["allowed_routes"], list) or not condition["allowed_routes"]):
            raise FoundationError(f"{rule_id} context-scope allowed_routes must be a non-empty list")
    elif condition_type == "required-decision":
        _require_condition_fields(condition, {"gate", "decision_ref", "outcome", "decided_by", "scope"}, rule_id)
        _require_condition_strings(condition, {"gate", "decision_ref", "outcome", "decided_by", "scope"}, rule_id)
    elif condition_type == "required-envelope":
        _require_condition_fields(condition, {"envelope_ref", "action", "target_scope", "stage", "expires_at"}, rule_id)
        _require_condition_strings(condition, {"envelope_ref", "action", "target_scope", "stage", "expires_at"}, rule_id)
        _parse_timestamp(condition["expires_at"], f"{rule_id} condition expires_at")
    elif condition_type == "required-lineage":
        _require_condition_fields(condition, {"mapping_ref", "source_ref", "target_ref", "transformation", "raw_reference"}, rule_id)
        _require_condition_strings(condition, {"mapping_ref", "source_ref", "target_ref", "transformation", "raw_reference"}, rule_id)
    elif condition_type == "required-promotion-review":
        _require_condition_fields(condition, {"entry_ref", "evidence_refs", "reviewed_by", "effective_from", "expires_at", "promotion_decision_ref"}, rule_id)
        _require_condition_strings(condition, {"entry_ref", "reviewed_by", "effective_from", "expires_at"}, rule_id)
        _parse_timestamp(condition["effective_from"], f"{rule_id} condition effective_from")
        _parse_timestamp(condition["expires_at"], f"{rule_id} condition expires_at")
        if not isinstance(condition["evidence_refs"], list) or not condition["evidence_refs"]:
            raise FoundationError(f"{rule_id} evidence_refs must be a non-empty list")
    elif condition_type == "required-exception-controls":
        _require_condition_fields(condition, {"rule_ref", "reason", "owner", "scope", "compensating_controls", "expires_at", "review_ref", "decision_ref"}, rule_id)
        _require_condition_strings(condition, {"rule_ref", "reason", "owner", "scope", "expires_at", "review_ref", "decision_ref"}, rule_id)
        _parse_timestamp(condition["expires_at"], f"{rule_id} condition expires_at")
        if not isinstance(condition["compensating_controls"], list) or not condition["compensating_controls"] or any(not isinstance(item, str) or not item.strip() for item in condition["compensating_controls"]):
            raise FoundationError(f"{rule_id} compensating_controls must be a non-empty list of strings")
    elif condition_type == "required-dod":
        _require_condition_fields(condition, {"unit_ref", "required_artifacts", "evaluation_refs", "evidence_ref"}, rule_id)
        _require_condition_strings(condition, {"unit_ref", "evidence_ref"}, rule_id)
        for field in ("required_artifacts", "evaluation_refs"):
            if not isinstance(condition[field], list) or not condition[field]:
                raise FoundationError(f"{rule_id} {field} must be a non-empty list")


def _validate_rules(asset: dict[str, Any]) -> None:
    rules = asset["content"].get("rules")
    if not isinstance(rules, list) or not rules:
        raise FoundationError(f"{asset['id']} requires rules")
    seen: set[str] = set()
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or not rule["id"].strip():
            raise FoundationError(f"{asset['id']} has an invalid rule")
        if rule["id"] in seen:
            raise FoundationError(f"duplicate rule id: {rule['id']}")
        seen.add(rule["id"])
        if rule.get("level") not in {"MUST", "SHOULD", "MAY"}:
            raise FoundationError(f"{rule['id']} has an invalid level")
        _validate_rule_metadata(rule, rule["id"])
        condition = rule.get("condition")
        if rule["level"] == "MUST" and not isinstance(condition, dict):
            raise FoundationError(f"{rule['id']} MUST rule requires a condition")
        if condition is not None:
            _validate_condition(condition, rule["id"])


def _validate_asset_specific(root: Path, asset: dict[str, Any]) -> None:
    kind = asset["kind"]
    content = asset["content"]
    if not isinstance(content, dict):
        raise FoundationError(f"{asset['id']} content must be an object")
    if kind == "gate-matrix":
        _require_fields(content, {"matrix_version", "gates"}, asset["id"])
        if content["matrix_version"] != asset["version"]:
            raise FoundationError(f"{asset['id']} matrix_version must match asset version")
        gates = content["gates"]
        if not isinstance(gates, list) or not gates:
            raise FoundationError(f"{asset['id']} requires gates")
        seen: set[str] = set()
        for gate in gates:
            if not isinstance(gate, dict):
                raise FoundationError(f"{asset['id']} gate must be an object")
            _require_fields(gate, {"id", "trigger", "required_decision", "accountable_role"}, asset["id"])
            if any(not isinstance(gate[key], str) or not gate[key].strip() for key in ("id", "trigger", "accountable_role")):
                raise FoundationError(f"{asset['id']} gate metadata must be non-empty")
            if not isinstance(gate["required_decision"], bool):
                raise FoundationError(f"{asset['id']} required_decision must be boolean")
            if gate["id"] in seen:
                raise FoundationError(f"{asset['id']} has duplicate gate: {gate['id']}")
            seen.add(gate["id"])
    elif kind in {"profile", "extension"}:
        namespace = content.get("namespace")
        if not isinstance(namespace, str) or not namespace.strip():
            raise FoundationError(f"{asset['id']} requires a namespace")
    elif kind == "rule-set":
        _validate_rules(asset)
    elif kind == "policy":
        if content.get("effect") not in {"allow", "deny"}:
            raise FoundationError(f"{asset['id']} has an invalid policy effect")
        if not isinstance(content.get("when"), dict):
            raise FoundationError(f"{asset['id']} requires a policy condition")
    elif kind == "semantic-mapping":
        required = {"mapping_version", "source", "target_type", "fields", "lineage_required", "preserve_raw_reference", "change_approval"}
        _require_fields(content, required, asset["id"])
        if content.get("mapping_version") != asset["version"] or any(not isinstance(content.get(field), str) or not content[field].strip() for field in ("source", "target_type")):
            raise FoundationError(f"{asset['id']} requires concrete source, target_type, and matching mapping_version")
        if not isinstance(content["fields"], dict) or not content["fields"] or any(not isinstance(key, str) or not isinstance(value, str) or not key.strip() or not value.strip() for key, value in content["fields"].items()) or content["lineage_required"] is not True or content["preserve_raw_reference"] is not True:
            raise FoundationError(f"{asset['id']} requires versioned fields, lineage, and raw reference")
        if not isinstance(content["change_approval"], dict) or not content["change_approval"].get("gate") or content["change_approval"].get("decision_required") is not True:
            raise FoundationError(f"{asset['id']} requires change approval")
    elif kind == "knowledge":
        entries = content.get("entries")
        if not isinstance(entries, list) or not entries:
            raise FoundationError(f"{asset['id']} requires knowledge entries")
        for entry in entries:
            if not isinstance(entry, dict):
                raise FoundationError(f"{asset['id']} knowledge entry must be an object")
            _require_fields(entry, {"id", "type", "status", "owner", "body_path", "classification", "scope", "provenance", "review", "effective_from", "expires_at"}, f"{asset['id']} entry")
            if entry.get("status") not in ALLOWED_STATUSES:
                raise FoundationError(f"{asset['id']} entry has an invalid status")
            _validate_provenance_record(entry["provenance"], f"{asset['id']} entry provenance")
            effective_from = _parse_timestamp(entry["effective_from"], f"{asset['id']} entry effective_from")
            expires_at = _parse_timestamp(entry["expires_at"], f"{asset['id']} entry expires_at")
            if effective_from >= expires_at:
                raise FoundationError(f"{asset['id']} entry effective_from must precede expires_at")
            if not isinstance(entry["review"], dict) or not entry["review"].get("reviewed_by") or entry["review"].get("duplicate_checked") is not True or not entry["review"].get("evidence_ref"):
                raise FoundationError(f"{asset['id']} entry requires provenance-backed duplicate review")
            if not isinstance(entry["body_path"], str) or not _safe_path(root, entry["body_path"]).is_file():
                raise FoundationError(f"{asset['id']} has a missing knowledge body")
    elif kind == "evaluation":
        if content.get("visibility") != "evaluation-only":
            raise FoundationError(f"{asset['id']} must be evaluation-only")
        if asset["id"] != "routing-evaluation" and content.get("evaluator") not in EVALUATOR_TYPES:
            raise FoundationError(f"{asset['id']} requires a supported evaluator")
        cases = content.get("cases")
        if not isinstance(cases, list) or not cases:
            raise FoundationError(f"{asset['id']} requires evaluation cases")
        ids: set[str] = set()
        for case in cases:
            if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not isinstance(case.get("input"), dict) or "expected" not in case:
                raise FoundationError(f"{asset['id']} has an invalid evaluation case")
            if asset["id"] != "routing-evaluation" and case["expected"] not in {"pass", "fail"}:
                raise FoundationError(f"{asset['id']} evaluation expected must be pass or fail")
            if case["id"] in ids:
                raise FoundationError(f"{asset['id']} has duplicate evaluation case: {case['id']}")
            ids.add(case["id"])
    elif kind.endswith("-contract"):
        _require_fields(content, {"contract_version", "references", "rules"}, asset["id"])
        if not isinstance(content["references"], list) or not isinstance(content["rules"], list) or not content["rules"]:
            raise FoundationError(f"{asset['id']} requires references and rules")
        for rule in content["rules"]:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str) or rule.get("level") not in {"MUST", "SHOULD", "MAY"} or not isinstance(rule.get("condition"), dict):
                raise FoundationError(f"{asset['id']} contract rules require id, level, and condition")
            _validate_rule_metadata(rule, f"{asset['id']} rule {rule.get('id', '<unknown>')}")
            _validate_condition(rule["condition"], rule["id"])


def _validate_cross_references(assets: dict[str, dict[str, Any]]) -> None:
    for asset in assets.values():
        references = asset.get("extends", [])
        if not isinstance(references, list):
            raise FoundationError(f"{asset['id']} extends must be a list")
        for reference in references:
            parent_id = _reference_id(reference, f"{asset['id']} extends")
            parent = assets.get(parent_id)
            if parent is None:
                raise FoundationError(f"{asset['id']} references unknown asset: {parent_id}")
            if reference["version"] != parent["version"]:
                raise FoundationError(f"{asset['id']} extends {parent_id} with an unmatching version")
        content_refs = asset.get("content", {}).get("references", [])
        if not isinstance(content_refs, list):
            raise FoundationError(f"{asset['id']} content references must be a list")
        for reference in content_refs:
            if reference not in assets:
                raise FoundationError(f"{asset['id']} references unknown contract asset: {reference}")
        rules = asset.get("content", {}).get("rules", [])
        for rule in rules if isinstance(rules, list) else []:
            condition = rule.get("condition") if isinstance(rule, dict) else None
            if isinstance(condition, dict) and condition.get("type") == "extension-cannot-weaken-must":
                parent_asset = condition.get("parent_asset")
                if parent_asset not in assets:
                    raise FoundationError(f"{asset['id']} condition references unknown parent asset: {parent_asset}")
                parent_rules = assets[parent_asset].get("content", {}).get("rules", [])
                if parent_asset != "core-model" and not any(isinstance(parent, dict) and parent.get("id") == condition.get("parent_rule_id") for parent in parent_rules):
                    raise FoundationError(f"{asset['id']} condition references unknown parent rule: {condition.get('parent_rule_id')}")
    gate_matrix = assets.get("gate-matrix")
    human_gate = assets.get("human-gate-contract")
    if gate_matrix is None or human_gate is None:
        raise FoundationError("Foundation requires a versioned gate-matrix and human-gate-contract")
    matrix_ids = {gate["id"] for gate in gate_matrix["content"]["gates"]}
    declared_gates = {gate.get("id") for gate in human_gate["content"].get("gates", []) if isinstance(gate, dict)}
    if matrix_ids != declared_gates:
        raise FoundationError("human-gate-contract gates must match gate-matrix")


def _load_foundation_documents(
    foundation_root: Path,
    manifest: dict[str, Any],
    *,
    document_loader: Callable[[Path], dict[str, Any]] = _load_json,
) -> FoundationRelease:
    _require_fields(manifest, {"id", "kind", "version", "status", "owner", "artifacts"}, "release")
    if manifest["kind"] != "foundation-release":
        raise FoundationError("release kind must be foundation-release")
    if manifest["status"] not in ALLOWED_STATUSES:
        raise FoundationError("release has an invalid status")
    if not isinstance(manifest["id"], str) or not manifest["id"].strip():
        raise FoundationError("release id must be a non-empty string")
    _validate_provenance(manifest)
    descriptors = manifest["artifacts"]
    if not isinstance(descriptors, list) or not descriptors:
        raise FoundationError("release requires at least one artifact")
    assets: dict[str, dict[str, Any]] = {}
    paths: dict[Path, str] = {}
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise FoundationError("artifact descriptor must be an object")
        _require_fields(descriptor, {"id", "kind", "version", "path"}, "artifact")
        asset_id = descriptor["id"]
        if not isinstance(asset_id, str) or not asset_id:
            raise FoundationError("artifact id must be a non-empty string")
        if descriptor["kind"] not in ALLOWED_ASSET_KINDS:
            raise FoundationError(f"{asset_id} has an unknown asset kind: {descriptor['kind']}")
        if asset_id in assets:
            raise FoundationError(f"duplicate artifact id: {asset_id}")
        if not isinstance(descriptor["path"], str) or not descriptor["path"].strip():
            raise FoundationError(f"{asset_id} artifact path must be a non-empty string")
        asset_path = _safe_path(foundation_root, descriptor["path"])
        if asset_path == foundation_root / "release.json":
            raise FoundationError(f"{asset_id} artifact path collides with release.json")
        previous = paths.get(asset_path)
        if previous is not None:
            raise FoundationError(
                f"duplicate artifact path: {descriptor['path']} (also used by {previous})"
            )
        paths[asset_path] = asset_id
        asset = document_loader(asset_path)
        _require_fields(asset, REQUIRED_ASSET_FIELDS, str(asset_id))
        for key in ("id", "kind", "version"):
            if asset[key] != descriptor[key]:
                raise FoundationError(f"{asset_id} descriptor mismatch: {key}")
        if asset["status"] not in ALLOWED_STATUSES:
            raise FoundationError(f"{asset_id} has an invalid status")
        _validate_provenance(asset)
        _validate_asset_specific(foundation_root, asset)
        assets[asset_id] = asset
    _validate_cross_references(assets)
    return FoundationRelease(foundation_root, manifest, assets)


def load_foundation(root: str | Path) -> FoundationRelease:
    foundation_root = Path(root).resolve()
    return _load_foundation_documents(foundation_root, _load_json(foundation_root / "release.json"))


def validate_context(context: dict[str, Any]) -> bool:
    required = {"project_id", "route", "rules", "profiles", "extensions"}
    return isinstance(context, dict) and required <= context.keys() and context["route"] in {"query", "quick-change", "unit"}


def _dict_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _not_expired(value: Any, now: datetime) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        expiry = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return expiry > now


def _effective_and_not_expired(effective_from: Any, expires_at: Any, now: datetime) -> bool:
    try:
        effective = _parse_timestamp(effective_from, "effective_from")
        expiry = _parse_timestamp(expires_at, "expires_at")
    except FoundationError:
        return False
    return effective <= now < expiry


def evaluate_condition(condition: dict[str, Any], subject: dict[str, Any] | None = None, *, now: datetime | None = None) -> bool:
    """Evaluate a closed Foundation condition against a supplied evidence subject."""
    _validate_condition(condition, "condition")
    value = subject or {}
    current = now or datetime.now(timezone.utc)
    kind = condition["type"]
    if kind == "required-artifact":
        artifact = value.get("artifacts", {}).get(condition["artifact"], value.get(condition["artifact"]))
        passed = _dict_path(artifact, condition["field"]) == condition["equals"]
        if condition.get("commands_required"):
            passed = passed and bool(value.get("commands"))
        return passed
    if kind == "context-scope":
        return all(field in value for field in condition["required_fields"]) and (not condition.get("allowed_routes") or value.get("route") in condition["allowed_routes"])
    if kind == "extension-cannot-weaken-must":
        levels = {"MAY": 0, "SHOULD": 1, "MUST": 2}
        return levels.get(value.get("extension_level"), -1) >= levels["MUST"] and value.get("parent_level") == condition["parent_level"]
    if kind == "required-decision":
        return any(isinstance(d, dict) and d.get("id") == condition["decision_ref"] and d.get("gate") == condition["gate"] and d.get("outcome") == condition["outcome"] and d.get("decided_by") == condition["decided_by"] and d.get("scope") == condition["scope"] and (not d.get("expires_at") or _not_expired(d["expires_at"], current)) for d in value.get("decisions", []))
    if kind == "required-envelope":
        envelope = value.get("envelope")
        if not isinstance(envelope, dict) or envelope.get("status") != "approved":
            return False
        if not _not_expired(envelope.get("expires_at"), current) or not _not_expired(condition["expires_at"], current):
            return False
        action = value.get("action")
        target = value.get("target")
        stage = value.get("stage")
        scopes = envelope.get("scope")
        stages = envelope.get("stages")
        allowed = envelope.get("allowed_actions")
        forbidden = envelope.get("forbidden_actions")
        if not isinstance(action, str) or action != condition["action"] or not isinstance(allowed, list) or action not in allowed:
            return False
        if not isinstance(forbidden, list) or action in forbidden:
            return False
        if not isinstance(scopes, list) or not isinstance(target, str):
            return False
        in_scope = any(isinstance(item, str) and (item == target or item == condition["target_scope"] or (item.endswith("/**") and target.startswith(item[:-3]))) for item in scopes)
        if not in_scope or stage != condition["stage"]:
            return False
        if not isinstance(stages, list) or not any(isinstance(item, dict) and item.get("name") == stage and action in item.get("allowed_actions", []) for item in stages):
            return False
        return isinstance(envelope.get("max_iterations"), int) and not isinstance(envelope.get("max_iterations"), bool) and envelope["max_iterations"] > 0
    if kind == "required-lineage":
        return all(value.get(key) == condition[key] for key in ("mapping_ref", "source_ref", "target_ref", "transformation", "raw_reference"))
    if kind == "required-promotion-review":
        entry = value.get("entry")
        provenance = entry.get("provenance") if isinstance(entry, dict) else None
        evidence = value.get("evidence")
        review = value.get("review")
        decisions = value.get("decisions")
        if not isinstance(entry, dict) or entry.get("id") != condition["entry_ref"]:
            return False
        if not isinstance(provenance, dict):
            return False
        try:
            _validate_provenance_record(provenance, "knowledge entry provenance")
        except FoundationError:
            return False
        if entry.get("effective_from") != condition["effective_from"] or entry.get("expires_at") != condition["expires_at"]:
            return False
        if not _effective_and_not_expired(entry.get("effective_from"), entry.get("expires_at"), current):
            return False
        if not isinstance(evidence, list) or not evidence or any(not isinstance(item, dict) or item.get("id") not in condition["evidence_refs"] or item.get("passed") is not True for item in evidence):
            return False
        if not isinstance(review, dict) or review.get("entry_ref") != condition["entry_ref"] or review.get("reviewed_by") != condition["reviewed_by"] or review.get("duplicate_checked") is not True or review.get("evidence_ref") not in condition["evidence_refs"]:
            return False
        return any(isinstance(decision, dict) and decision.get("id") == condition["promotion_decision_ref"] and decision.get("outcome") == "approved" and decision.get("gate") == "knowledge" and _not_expired(decision.get("expires_at"), current) for decision in (decisions if isinstance(decisions, list) else []))
    if kind == "required-exception-controls":
        if not all(value.get(key) == condition[key] for key in ("rule_ref", "reason", "owner", "scope", "compensating_controls")) or not _not_expired(value.get("expires_at"), current):
            return False
        review = value.get("review")
        if not isinstance(review, dict) or review.get("id") != condition["review_ref"] or review.get("status") not in {"approved", "passed"}:
            return False
        return any(isinstance(decision, dict) and decision.get("id") == condition["decision_ref"] and decision.get("outcome") == "approved" and _not_expired(decision.get("expires_at"), current) for decision in value.get("decisions", []))
    if kind == "required-dod":
        return value.get("unit_ref") == condition["unit_ref"] and all(item in value.get("artifacts", []) for item in condition["required_artifacts"]) and all(item in value.get("evaluations", []) for item in condition["evaluation_refs"]) and value.get("evidence_ref") == condition["evidence_ref"] and value.get("evidence_passed") is True
    return False


def _as_release(root: str | Path | FoundationRelease) -> FoundationRelease:
    """Accept an already validated release so callers can avoid re-reading it."""
    if isinstance(root, FoundationRelease):
        return root
    return load_foundation(root)


def evaluate_routing_cases(root: str | Path | FoundationRelease) -> dict[str, Any]:
    foundation = _as_release(root)
    asset = foundation.assets.get("routing-evaluation")
    if asset is None:
        raise FoundationError("missing routing-evaluation asset")
    from .workflow import RouteRequest, classify_work
    results = []
    for case in asset["content"]["cases"]:
        decision = classify_work(RouteRequest(**case["input"]))
        results.append({"id": case["id"], "expected": case["expected"], "actual": decision.route.value, "passed": decision.route.value == case["expected"]})
    return {"passed": all(item["passed"] for item in results), "cases": results}


def validate_foundation(root: str | Path) -> dict[str, Any]:
    foundation = load_foundation(root)
    routing = evaluate_routing_cases(foundation)
    return {"valid": True, "summary": foundation.summary(), "evaluations": {"routing": routing}}


# Public names for the structural validators that Project-owned assets reuse.
# Project extensions are held to the same rule, provenance, and condition
# contract as Foundation assets, so these are part of the supported surface.
validate_condition_definition = _validate_condition
validate_asset_provenance = _validate_provenance
validate_rule_definition = _validate_rule_metadata



def _evaluation_condition(foundation: FoundationRelease, asset: dict[str, Any]) -> dict[str, Any]:
    evaluator = asset["content"].get("evaluator")
    if evaluator not in EVALUATOR_TYPES:
        raise FoundationError(f"{asset['id']} has no supported evaluator")
    for reference in asset.get("extends", []):
        parent = foundation.assets.get(reference.get("id") if isinstance(reference, dict) else None)
        if parent is None:
            continue
        for rule in parent.get("content", {}).get("rules", []):
            condition = rule.get("condition") if isinstance(rule, dict) else None
            if isinstance(condition, dict) and condition.get("type") == evaluator:
                return condition
    raise FoundationError(f"{asset['id']} cannot resolve condition for evaluator {evaluator}")


def _evaluate_case(foundation: FoundationRelease, asset: dict[str, Any], case: dict[str, Any]) -> bool:
    evaluator = asset["content"].get("evaluator")
    subject = case["input"]
    if not isinstance(subject, dict):
        return False
    if evaluator == "required-decision":
        matrix = foundation.assets["gate-matrix"]["content"]["gates"]
        gate = subject.get("gate")
        if not any(item.get("id") == gate for item in matrix):
            return False
    condition = case.get("condition", _evaluation_condition(foundation, asset))
    return evaluate_condition(condition, subject, now=EVALUATION_CLOCK)


def evaluate_all_evaluations(root: str | Path | FoundationRelease) -> dict[str, Any]:
    """Run every versioned evaluation fixture through its declared evaluator."""
    foundation = _as_release(root)
    evaluations: dict[str, Any] = {"routing": evaluate_routing_cases(foundation)}
    for asset in sorted(foundation.assets_by_kind("evaluation"), key=lambda item: item["id"]):
        if asset["id"] == "routing-evaluation":
            continue
        cases = []
        for case in asset["content"]["cases"]:
            expected = case["expected"]
            actual = "pass" if _evaluate_case(foundation, asset, case) else "fail"
            cases.append({"id": case["id"], "expected": expected, "actual": actual, "passed": actual == expected})
        evaluations[asset["id"]] = {"passed": all(item["passed"] for item in cases), "cases": cases}
    return {"passed": all(result["passed"] for result in evaluations.values()), "evaluations": evaluations}


def evaluate_foundation(root: str | Path) -> dict[str, Any]:
    """Validate the Foundation and execute its complete evaluation matrix."""
    foundation = load_foundation(root)
    evaluations = evaluate_all_evaluations(foundation)
    return {"valid": True, "evaluations_passed": evaluations["passed"], "summary": foundation.summary(), "evaluations": evaluations["evaluations"]}
