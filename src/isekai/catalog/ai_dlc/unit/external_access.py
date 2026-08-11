from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from isekai.support.scope import scope_pattern_matches


EXTERNAL_API_ACTION = "external-api"
EXTERNAL_ACCESS_ENVIRONMENTS = {"development", "test"}
EXTERNAL_ACCESS_METHODS = {
    "DELETE",
    "GET",
    "HEAD",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
}
EXTERNAL_ACCESS_REQUIRED_FIELDS = {
    "id",
    "credential_ref",
    "environment",
    "scheme",
    "host",
    "path",
    "methods",
    "max_requests",
}
_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
_HOST_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
)
_CREDENTIAL_REF_PATTERN = re.compile(
    r"secret://[a-z][a-z0-9._-]{0,62}/[A-Za-z0-9][A-Za-z0-9._/-]{0,126}"
)
_AMBIGUOUS_PATH_ENCODING = re.compile(r"%(?:0[0-9a-f]|1[0-9a-f]|2e|2f|5c|7f)", re.I)


def _external_path_issue(path: Any) -> bool:
    return (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or "?" in path
        or "#" in path
        or any(character.isspace() for character in path)
        or ".." in path.split("/")
        or _AMBIGUOUS_PATH_ENCODING.search(path) is not None
    )


def _host_is_ip_literal(host: str) -> bool:
    """Reject canonical and legacy IPv4 spellings, plus any IPv6 literal."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return True
    legacy_ipv4_label = re.compile(r"(?:0[xX][0-9A-Fa-f]+|0[0-7]*|[1-9][0-9]*)")
    labels = host.split(".")
    return 1 <= len(labels) <= 4 and all(
        legacy_ipv4_label.fullmatch(label) for label in labels
    )


def _external_host_issue(host: Any, *, require_lowercase: bool) -> bool:
    return (
        not isinstance(host, str)
        or (require_lowercase and host != host.lower())
        or not _HOST_PATTERN.fullmatch(host)
        or host == "localhost"
        or "." not in host
        or _host_is_ip_literal(host)
    )


def external_access_policy_issues(value: Any) -> list[str]:
    if not isinstance(value, list):
        return ["Execution Envelope external_access must be a list"]
    issues: list[str] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(value):
        label = f"Execution Envelope external_access {index}"
        if not isinstance(entry, dict):
            issues.append(f"{label} must be an object")
            continue
        missing = sorted(EXTERNAL_ACCESS_REQUIRED_FIELDS - entry.keys())
        unknown = sorted(entry.keys() - EXTERNAL_ACCESS_REQUIRED_FIELDS)
        if missing:
            issues.append(f"{label} missing fields: {', '.join(missing)}")
        if unknown:
            issues.append(
                f"{label} contains unsupported fields: {', '.join(unknown)}"
            )
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not _IDENTIFIER_PATTERN.fullmatch(entry_id):
            issues.append(f"{label} has an invalid id")
        elif entry_id in seen_ids:
            issues.append(f"{label} has a duplicate id")
        else:
            seen_ids.add(entry_id)
        credential_ref = entry.get("credential_ref")
        if not isinstance(credential_ref, str) or not _CREDENTIAL_REF_PATTERN.fullmatch(
            credential_ref
        ):
            issues.append(
                f"{label} credential_ref must be an opaque secret://provider/name reference"
            )
        elif ".." in credential_ref.split("/"):
            issues.append(f"{label} credential_ref cannot contain traversal segments")
        if not isinstance(entry.get("environment"), str) or entry.get(
            "environment"
        ) not in EXTERNAL_ACCESS_ENVIRONMENTS:
            issues.append(f"{label} environment must be development or test")
        if entry.get("scheme") != "https":
            issues.append(f"{label} scheme must be https")
        host = entry.get("host")
        if _external_host_issue(host, require_lowercase=True):
            issues.append(f"{label} host must be a lowercase external DNS name")
        path = entry.get("path")
        if _external_path_issue(path):
            issues.append(f"{label} path must be an absolute URL path pattern")
        methods = entry.get("methods")
        if not isinstance(methods, list) or not methods:
            issues.append(f"{label} methods must be a non-empty list")
        elif any(
            not isinstance(method, str)
            or method not in EXTERNAL_ACCESS_METHODS
            for method in methods
        ):
            issues.append(f"{label} contains an unsupported HTTP method")
        elif len(set(methods)) != len(methods):
            issues.append(f"{label} contains duplicate HTTP methods")
        max_requests = entry.get("max_requests")
        if (
            not isinstance(max_requests, int)
            or isinstance(max_requests, bool)
            or max_requests <= 0
        ):
            issues.append(f"{label} max_requests must be a positive integer")
    return issues


def normalize_external_api_request(
    target: str,
    method: str | None,
    credential_ref: str | None,
) -> tuple[dict[str, str] | None, str | None]:
    if not isinstance(target, str) or not target.strip():
        return None, "External API authorization requires a non-empty URL target"
    try:
        parsed = urlsplit(target.strip())
        port = parsed.port
    except ValueError as exc:
        return None, f"External API target is invalid: {exc}"
    if parsed.scheme.lower() != "https":
        return None, "External API target must use HTTPS"
    if not parsed.hostname or parsed.username is not None or parsed.password is not None:
        return None, "External API target cannot contain user information"
    host = parsed.hostname.lower()
    if _external_host_issue(host, require_lowercase=False):
        return None, "External API target must use an external DNS hostname"
    if port not in {None, 443}:
        return None, "External API target may only use the default HTTPS port"
    if parsed.query or parsed.fragment:
        return None, "External API target cannot contain a query or fragment"
    path = parsed.path or "/"
    if _external_path_issue(path):
        return None, "External API target contains an unsafe or ambiguous path"
    normalized_method = method.upper().strip() if isinstance(method, str) else ""
    if normalized_method not in EXTERNAL_ACCESS_METHODS:
        return None, "External API authorization requires a supported HTTP method"
    if not isinstance(credential_ref, str) or not _CREDENTIAL_REF_PATTERN.fullmatch(
        credential_ref
    ):
        return None, (
            "External API authorization requires an opaque "
            "secret://provider/name credential_ref"
        )
    if ".." in credential_ref.split("/"):
        return None, "External API credential_ref cannot contain traversal segments"
    normalized_url = urlunsplit(("https", host, path, "", ""))
    return {
        "target": normalized_url,
        "scheme": "https",
        "host": host,
        "path": path,
        "method": normalized_method,
        "credential_ref": credential_ref,
    }, None


def matching_external_access(
    policies: Any,
    request: dict[str, str],
) -> dict[str, Any] | None:
    if not isinstance(policies, list):
        return None
    for policy in policies:
        if not isinstance(policy, dict):
            continue
        if (
            policy.get("scheme") == request["scheme"]
            and policy.get("host") == request["host"]
            and policy.get("credential_ref") == request["credential_ref"]
            and request["method"] in policy.get("methods", [])
            and isinstance(policy.get("path"), str)
            and scope_pattern_matches(policy["path"], request["path"])
        ):
            return policy
    return None
