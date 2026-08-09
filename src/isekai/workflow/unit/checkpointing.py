from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..errors import IntegrityError
from .common import _unit_json, _unit_path_without_symlinks
from .execution_history import EXECUTION_AUTHORIZATION_RECORDS_DIR


CHECKPOINT_CURSOR_VERSION = "1.0.0"
PROGRESS_ACTIONS = frozenset({"edit", "test", "external-api"})


def _progress_entries(
    ledger: Any,
    stages: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(ledger, dict):
        return []
    envelope_id = ledger.get("envelope_id")
    grants = ledger.get("grants")
    if not isinstance(grants, list):
        return []
    return [
        {"envelope_id": envelope_id, "grant": grant}
        for grant in grants
        if isinstance(grant, dict)
        and grant.get("action") in PROGRESS_ACTIONS
        and (stages is None or grant.get("stage") in stages)
    ]


def _authorization_progress_entries(
    unit_dir: Path,
    active_ledger: dict[str, Any],
    stages: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    records_dir = _unit_path_without_symlinks(
        unit_dir, EXECUTION_AUTHORIZATION_RECORDS_DIR
    )
    if records_dir.exists():
        if not records_dir.is_dir():
            raise IntegrityError(
                "Execution authorization records path must be a directory"
            )
        try:
            record_paths = sorted(records_dir.iterdir())
        except OSError as exc:
            raise IntegrityError(
                f"cannot inspect Execution authorization records: {exc}"
            ) from exc
        for record_path in record_paths:
            relative = record_path.relative_to(unit_dir).as_posix()
            record = _unit_json(unit_dir, relative)
            archived_ledger = record.get("authorization_ledger")
            if not isinstance(archived_ledger, dict):
                raise IntegrityError(
                    f"Execution authorization record has no ledger: {relative}"
                )
            entries.extend(_progress_entries(archived_ledger, stages))
    entries.extend(_progress_entries(active_ledger, stages))
    return entries


def authorization_progress_cursor(
    unit_dir: Path,
    active_ledger: dict[str, Any] | None = None,
    *,
    stages: frozenset[str] | None = None,
) -> dict[str, Any]:
    ledger = active_ledger or _unit_json(unit_dir, "execution-authorizations.json")
    entries = _authorization_progress_entries(unit_dir, ledger, stages)
    encoded = json.dumps(
        entries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    last_grant = entries[-1]["grant"] if entries else None
    return {
        "type": "execution-authorization-cursor",
        "schema_version": CHECKPOINT_CURSOR_VERSION,
        "work_grant_count": len(entries),
        "last_work_grant_id": (
            last_grant.get("id") if isinstance(last_grant, dict) else None
        ),
        "progress_digest": "sha256:" + hashlib.sha256(encoded).hexdigest(),
    }


def authorization_cursor_issues(cursor: Any) -> list[str]:
    if not isinstance(cursor, dict):
        return ["authorization progress cursor must be an object"]
    issues: list[str] = []
    if cursor.get("type") != "execution-authorization-cursor":
        issues.append("authorization progress cursor has an invalid type")
    if cursor.get("schema_version") != CHECKPOINT_CURSOR_VERSION:
        issues.append("authorization progress cursor has an unsupported schema_version")
    count = cursor.get("work_grant_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        issues.append("authorization progress cursor has an invalid work_grant_count")
    last_id = cursor.get("last_work_grant_id")
    if count == 0 and last_id is not None:
        issues.append("authorization progress cursor has an unexpected last grant")
    if isinstance(count, int) and count > 0 and (
        not isinstance(last_id, str) or not last_id.strip()
    ):
        issues.append("authorization progress cursor requires the last work grant id")
    digest = cursor.get("progress_digest")
    if not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None:
        issues.append("authorization progress cursor requires a SHA-256 digest")
    return issues


def checkpoint_progress_issues(
    unit_dir: Path,
    *,
    checkpoint: dict[str, Any] | None = None,
    active_ledger: dict[str, Any] | None = None,
) -> list[str]:
    try:
        current_checkpoint = checkpoint or _unit_json(unit_dir, "checkpoint.json")
        expected = authorization_progress_cursor(unit_dir, active_ledger)
    except IntegrityError as exc:
        return [str(exc)]
    recorded = current_checkpoint.get("authorization_cursor")
    if recorded is None and expected["work_grant_count"] == 0:
        return []  # Legacy checkpoints without any governed work remain readable.
    if not isinstance(recorded, dict):
        return ["checkpoint does not acknowledge authorized implementation progress"]
    cursor_issues = authorization_cursor_issues(recorded)
    if cursor_issues:
        return ["checkpoint " + issue for issue in cursor_issues]
    if recorded != expected:
        recorded_count = recorded.get("work_grant_count")
        current_count = expected["work_grant_count"]
        if isinstance(recorded_count, int) and recorded_count < current_count:
            delta = current_count - recorded_count
            return [
                "checkpoint is stale; "
                f"{delta} authorized implementation action(s) are not recorded"
            ]
        return ["checkpoint authorization cursor does not match current progress"]
    return []


def progress_authorization_block(
    unit_dir: Path,
    action: str,
    active_ledger: dict[str, Any],
) -> str | None:
    if action not in PROGRESS_ACTIONS:
        return None
    issues = checkpoint_progress_issues(unit_dir, active_ledger=active_ledger)
    if not issues:
        return None
    return "Checkpoint required before another implementation action: " + "; ".join(
        issues
    )


def progress_authorization_obligation(action: str) -> dict[str, Any]:
    if action not in PROGRESS_ACTIONS:
        return {}
    return {
        "checkpoint_required": True,
        "checkpoint_reason": "record this completed work batch before continuing",
    }
