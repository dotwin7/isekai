from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from isekai.support.scope import scope_pattern_matches


EXECUTION_ENVELOPE_APPROVAL_FIELDS = {
    "id",
    "type",
    "schema_version",
    "unit_id",
    "scope",
    "stages",
    "allowed_actions",
    "forbidden_actions",
    "external_access",
    "max_iterations",
    "proposed_by",
    "proposed_at",
    "expires_at",
}


def _execution_envelope_approval_digest(envelope: dict[str, Any]) -> str:
    subject = {
        field: envelope.get(field)
        for field in sorted(EXECUTION_ENVELOPE_APPROVAL_FIELDS)
        if field != "external_access" or field in envelope
    }
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _scope_pattern_issue(pattern: str) -> str | None:
    normalized = pattern.replace("\\", "/")
    if (
        not normalized.strip()
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in normalized.split("/")
    ):
        return f"Execution Envelope scope must be a project-relative pattern: {pattern}"
    return None


def _scope_pattern_matches(pattern: str, target: str) -> bool:
    """Match a scope pattern without allowing wildcards to cross segments."""
    return scope_pattern_matches(pattern, target)
