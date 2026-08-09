from __future__ import annotations

from pathlib import Path
from typing import Any

from .authorization import (
    _authorization_target_protection_issue,
    _normalize_authorization_target,
)
from .external_access import (
    EXTERNAL_API_ACTION,
    matching_external_access,
    normalize_external_api_request,
)
from ...support.scope import scope_pattern_matches


def resolve_authorization_request(
    unit_dir: Path,
    *,
    action: str,
    target: str | None,
    method: str | None,
    credential_ref: str | None,
    envelope: dict[str, Any],
) -> tuple[str | None, dict[str, Any] | None, dict[str, str] | None, str | None]:
    if action == EXTERNAL_API_ACTION:
        request, issue = normalize_external_api_request(
            str(target) if target is not None else "", method, credential_ref
        )
        if issue is not None:
            return None, None, None, issue
        assert request is not None
        policy = matching_external_access(envelope.get("external_access"), request)
        if policy is None:
            return (
                None,
                None,
                None,
                "External API request is outside the approved external_access policy",
            )
        return request["target"], policy, request, None
    if method is not None or credential_ref is not None:
        return (
            None,
            None,
            None,
            "method and credential_ref are only valid for external-api",
        )
    normalized_target, issue = _normalize_authorization_target(
        unit_dir, str(target) if target is not None else ""
    )
    if issue is not None:
        return None, None, None, issue
    assert normalized_target is not None
    protection_issue = _authorization_target_protection_issue(
        unit_dir, action, normalized_target
    )
    if protection_issue is not None:
        return None, None, None, protection_issue
    if not any(
        scope_pattern_matches(pattern, normalized_target)
        for pattern in envelope["scope"]
    ):
        return (
            None,
            None,
            None,
            f"Target is outside the approved Envelope scope: {normalized_target}",
        )
    return normalized_target, None, None, None


def external_request_count(
    grants: list[dict[str, Any]], policy: dict[str, Any]
) -> int:
    return sum(
        grant.get("action") == EXTERNAL_API_ACTION
        and grant.get("external_access_id") == policy.get("id")
        for grant in grants
    )


def external_grant_metadata(
    policy: dict[str, Any], request: dict[str, str]
) -> dict[str, Any]:
    return {
        "external_access_id": policy["id"],
        "environment": policy["environment"],
        "method": request["method"],
        "credential_ref": request["credential_ref"],
    }
