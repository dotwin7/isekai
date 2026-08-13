from __future__ import annotations

from pathlib import Path
from typing import Any

from isekai.catalog.ai_dlc.unit.execution import issue_action_grant as _issue_action_grant


def authorize_action(
    path: str | Path,
    *,
    action: str,
    target: str | None = None,
    stage: str | None = None,
    method: str | None = None,
    credential_ref: str | None = None,
) -> dict[str, Any]:
    """Authorize only actions that can safely remain separate from execution."""
    if not isinstance(action, str):
        return {"allowed": False, "reason": "Action must be a string"}
    if action in {"edit", "test"}:
        return {
            "allowed": False,
            "reason_code": "core-managed-execution-required",
            "reason": (
                f"{action} cannot be separated from execution; use the Core "
                "managed execution action"
            ),
        }
    return _issue_action_grant(
        path,
        action=action,
        target=target,
        stage=stage,
        method=method,
        credential_ref=credential_ref,
    )
