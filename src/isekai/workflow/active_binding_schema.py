from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


ACTIVE_BINDING_SCHEMA_VERSION = "1.0.0"
_EVENT_ACTIONS = {"bind", "detach", "learned", "abandoned"}
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


def binding_event_digest(value: dict[str, Any]) -> str:
    subject = {key: item for key, item in value.items() if key != "event_digest"}
    encoded = json.dumps(
        subject,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def binding_issues(value: Any, *, project_id: str) -> list[str]:
    if not isinstance(value, dict):
        return ["binding must be an object"]
    issues: list[str] = []
    required_fields = {
        "type",
        "schema_version",
        "project_id",
        "active_unit",
        "generation",
        "events",
        "updated_at",
    }
    missing_fields = sorted(required_fields - value.keys())
    if missing_fields:
        issues.append("binding missing fields: " + ", ".join(missing_fields))
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
            if not isinstance(path_base, str) or path_base not in {
                "project",
                "absolute",
            }:
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
    expected_active_unit: dict[str, str] | None = None
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
        if not isinstance(event.get("action"), str) or event.get(
            "action"
        ) not in _EVENT_ACTIONS:
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
        if not isinstance(event_base, str) or event_base not in {
            "project",
            "absolute",
        }:
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
        elif digest != binding_event_digest(event):
            issues.append(f"binding event {index} digest does not match")
        else:
            previous_digest = digest
        action = event.get("action")
        locator = {
            "unit_id": event.get("unit_id"),
            "path": event.get("path"),
            "path_base": event.get("path_base"),
        }
        if action == "bind":
            if expected_active_unit is not None:
                issues.append(
                    f"binding event {index} binds while another Unit is active"
                )
            if all(isinstance(item, str) for item in locator.values()):
                expected_active_unit = {
                    key: str(item) for key, item in locator.items()
                }
        elif action in {"detach", "learned", "abandoned"}:
            if expected_active_unit is None:
                issues.append(
                    f"binding event {index} closes without an active Unit"
                )
            elif locator != expected_active_unit:
                issues.append(
                    f"binding event {index} does not match the active Unit"
                )
            expected_active_unit = None
    if isinstance(generation, int) and generation != len(events):
        issues.append("binding generation does not match event count")
    if active_unit != expected_active_unit:
        issues.append("binding active_unit does not match event history")
    expected_updated_at = events[-1].get("recorded_at") if events else None
    if value.get("updated_at") != expected_updated_at:
        issues.append("binding updated_at does not match event history")
    return issues


__all__ = [
    "ACTIVE_BINDING_SCHEMA_VERSION",
    "binding_event_digest",
    "binding_issues",
]
