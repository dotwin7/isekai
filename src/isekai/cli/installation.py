from __future__ import annotations

from typing import Any

from ..distribution import (
    apply_execution_profile,
    doctor_install,
    execution_profile_status,
)


def configure_installed_profiles(
    path: str,
    result: dict[str, object],
) -> dict[str, object]:
    runtimes = result.get("runtimes")
    if not isinstance(runtimes, list) or any(
        not isinstance(runtime, str)
        or runtime not in {"codex", "claude", "kiro"}
        for runtime in runtimes
    ):
        raise ValueError("install result has invalid runtimes for execution guard setup")
    configured = {
        runtime: apply_execution_profile(path, runtime)
        for runtime in sorted(set(runtimes))
    }
    return {**result, "execution_guards": configured}


def doctor_project(
    path: str,
    *,
    fix: bool = False,
) -> dict[str, Any]:
    """Inspect the installation and every installed Runtime execution guard."""
    health = doctor_install(path)
    runtimes = health.get("runtimes", [])
    if not health.get("ready") or not isinstance(runtimes, list):
        return {
            **health,
            "fix_attempted": fix,
            "execution_guards": {},
        }

    guards: dict[str, dict[str, Any]] = {}
    guard_issues: list[str] = []
    for runtime in runtimes:
        if not isinstance(runtime, str) or runtime not in {"codex", "claude", "kiro"}:
            guard_issues.append(f"invalid installed Runtime: {runtime!r}")
            continue
        try:
            status = (
                apply_execution_profile(path, runtime)
                if fix
                else execution_profile_status(path, runtime)
            )
        except ValueError as exc:
            status = {
                "ready": False,
                "runtime": runtime,
                "issues": [str(exc)],
            }
        guards[runtime] = status
        if not status.get("ready"):
            issues = status.get("issues", [])
            if isinstance(issues, list):
                guard_issues.extend(
                    f"{runtime} execution guard: {issue}" for issue in issues
                )
            else:
                guard_issues.append(f"{runtime} execution guard is not ready")

    issues = [*health.get("issues", []), *guard_issues]
    return {
        **health,
        "ready": not issues,
        "issues": issues,
        "fix_attempted": fix,
        "execution_guards": guards,
    }
