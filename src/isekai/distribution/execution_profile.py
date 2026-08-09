from __future__ import annotations

import base64
import json
import os
import stat
import tomllib
from pathlib import Path
from typing import Any

from ..support.files import UnsafeControlFile, read_control_file
from ..support.jsonio import write_bytes_atomic, write_json_atomic
from .release import DistributionError, MANAGED_ROOT, RUNTIMES


PROFILE_STATE = Path(MANAGED_ROOT) / "host-profiles/state.json"
CODEX_CONFIG = Path(".codex/config.toml")
CLAUDE_SETTINGS = Path(".claude/settings.json")
CLAUDE_MCP = Path(".mcp.json")
KIRO_MCP = Path(".kiro/settings/mcp.json")
KIRO_AGENT = Path(".kiro/agents/isekai-core.json")
PROFILE_SCHEMA_VERSION = "1.0.0"
MCP_SERVER_ID = "isekai_core"
CORE_MCP_TOOLS = [
    "runtime_action",
    "feature_catalog",
    "managed_edit",
    "artifact_write",
    "managed_test",
]
_CLAUDE_DENY = ("Edit", "Write", "NotebookEdit", "Bash")


def _project_file(project_root: Path, relative: Path) -> Path:
    candidate = project_root
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise DistributionError(
                f"execution profile path contains a symlink: {relative.as_posix()}"
            )
    return candidate


def _read_bytes(project_root: Path, relative: Path) -> bytes | None:
    path = _project_file(project_root, relative)
    if not path.exists():
        return None
    try:
        return read_control_file(
            path,
            root=project_root,
            label=f"execution profile {relative.as_posix()}",
        )
    except (UnsafeControlFile, OSError) as exc:
        raise DistributionError(str(exc)) from exc


def _read_json(project_root: Path, relative: Path) -> dict[str, Any]:
    content = _read_bytes(project_root, relative)
    if content is None:
        return {}
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistributionError(
            f"execution profile JSON is invalid: {relative.as_posix()}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise DistributionError(
            f"execution profile JSON must be an object: {relative.as_posix()}"
        )
    return value


def _launcher(project_root: Path) -> Path:
    name = "isekai.cmd" if os.name == "nt" else "isekai"
    path = _project_file(project_root, Path(MANAGED_ROOT) / "bin" / name)
    if not path.is_file():
        raise DistributionError(
            "execution profile requires an installed project-local ISEKAI launcher"
        )
    return path.resolve()


def _mcp_entry(project_root: Path, runtime: str) -> dict[str, Any]:
    return {
        "type": "stdio",
        "command": str(_launcher(project_root)),
        "args": [
            "mcp-serve",
            "--project",
            str(project_root.resolve()),
            "--runtime",
            runtime,
        ],
    }


def _snapshot(project_root: Path, relative: Path) -> dict[str, Any]:
    content = _read_bytes(project_root, relative)
    path = project_root / relative
    return {
        "path": relative.as_posix(),
        "existed": content is not None,
        "content_base64": (
            base64.b64encode(content).decode("ascii") if content is not None else None
        ),
        "mode": stat.S_IMODE(path.lstat().st_mode) if content is not None else None,
    }


def _state(project_root: Path) -> dict[str, Any]:
    value = _read_json(project_root, PROFILE_STATE)
    if not value:
        return {
            "type": "isekai-host-execution-profiles",
            "schema_version": PROFILE_SCHEMA_VERSION,
            "profiles": {},
        }
    if (
        value.get("type") != "isekai-host-execution-profiles"
        or value.get("schema_version") != PROFILE_SCHEMA_VERSION
        or not isinstance(value.get("profiles"), dict)
    ):
        raise DistributionError("invalid ISEKAI host execution profile state")
    return value


def _write_json(project_root: Path, relative: Path, value: dict[str, Any]) -> None:
    target = _project_file(project_root, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(target, value)


def _merge_mcp_json(
    project_root: Path,
    relative: Path,
    runtime: str,
) -> dict[str, Any]:
    document = _read_json(project_root, relative)
    servers = document.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise DistributionError(f"{relative.as_posix()} mcpServers must be an object")
    expected = _mcp_entry(project_root, runtime)
    existing = servers.get(MCP_SERVER_ID)
    if existing is not None and existing != expected:
        raise DistributionError(
            f"refusing to replace unmanaged MCP server {MCP_SERVER_ID} in "
            f"{relative.as_posix()}"
        )
    servers[MCP_SERVER_ID] = expected
    return document


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _codex_document(project_root: Path, *, managed: bool = False) -> bytes:
    before = _read_bytes(project_root, CODEX_CONFIG) or b""
    try:
        parsed = tomllib.loads(before.decode("utf-8")) if before else {}
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise DistributionError(f"invalid Codex project config: {exc}") from exc
    if (
        "default_permissions" in parsed
        and parsed.get("default_permissions") != ":read-only"
    ):
        raise DistributionError(
            "Codex default_permissions conflicts with the required read-only profile"
        )
    if "sandbox_mode" in parsed and parsed.get("sandbox_mode") != "read-only":
        raise DistributionError(
            "Codex sandbox_mode conflicts with the required read-only profile"
        )
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise DistributionError("Codex mcp_servers must be a table")
    expected_command = str(_launcher(project_root))
    expected_args = [
        "mcp-serve",
        "--project",
        str(project_root.resolve()),
        "--runtime",
        "codex",
    ]
    expected: dict[str, Any] = {
        "command": expected_command,
        "args": expected_args,
        "required": True,
        "enabled_tools": CORE_MCP_TOOLS,
    }
    existing = servers.get(MCP_SERVER_ID)
    legacy_expected = {
        "command": expected_command,
        "args": expected_args,
    }
    managed_upgrade = (
        managed
        and isinstance(existing, dict)
        and existing.get("command") == expected_command
        and existing.get("args") == expected_args
    )
    if (
        existing is not None
        and existing != expected
        and existing != legacy_expected
        and not managed_upgrade
    ):
        raise DistributionError(
            f"refusing to replace unmanaged Codex MCP server {MCP_SERVER_ID}"
        )
    prefix = b"" if "default_permissions" in parsed or "sandbox_mode" in parsed else (
        b'default_permissions = ":read-only"\n\n'
    )
    if existing == expected:
        return prefix + before
    if existing == legacy_expected or managed_upgrade:
        marker = ("[mcp_servers." + MCP_SERVER_ID + "]").encode("utf-8")
        lines = before.splitlines(keepends=True)
        starts = [
            index for index, line in enumerate(lines) if line.strip() == marker
        ]
        if len(starts) != 1:
            raise DistributionError(
                "legacy Codex MCP table could not be upgraded safely"
            )
        start = starts[0]
        end = next(
            (
                index
                for index in range(start + 1, len(lines))
                if lines[index].lstrip().startswith(b"[")
            ),
            len(lines),
        )
        before = b"".join([*lines[:start], *lines[end:]])
    separator = b"" if not before or before.endswith(b"\n") else b"\n"
    block = (
        "\n[mcp_servers."
        + MCP_SERVER_ID
        + "]\ncommand = "
        + _toml_string(expected_command)
        + "\nargs = ["
        + ", ".join(_toml_string(item) for item in expected_args)
        + "]\n"
        + "required = true\n"
        + "enabled_tools = ["
        + ", ".join(_toml_string(item) for item in CORE_MCP_TOOLS)
        + "]\n"
    ).encode("utf-8")
    return prefix + before + separator + block


def _claude_documents(project_root: Path) -> dict[Path, bytes]:
    settings = _read_json(project_root, CLAUDE_SETTINGS)
    permissions = settings.setdefault("permissions", {})
    if not isinstance(permissions, dict):
        raise DistributionError("Claude permissions must be an object")
    deny = permissions.setdefault("deny", [])
    if not isinstance(deny, list) or any(not isinstance(item, str) for item in deny):
        raise DistributionError("Claude permissions.deny must be a string list")
    for tool in _CLAUDE_DENY:
        if tool not in deny:
            deny.append(tool)
    mcp = _merge_mcp_json(project_root, CLAUDE_MCP, "claude")
    return {
        CLAUDE_SETTINGS: (
            json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
        CLAUDE_MCP: (json.dumps(mcp, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8"
        ),
    }


def _kiro_documents(
    project_root: Path,
    *,
    managed: bool = False,
) -> dict[Path, bytes]:
    mcp = _merge_mcp_json(project_root, KIRO_MCP, "kiro")
    expected_agent = {
        "name": "isekai-core",
        "description": "ISEKAI Core-exclusive execution profile",
        "tools": ["read", "@mcp"],
        "allowedTools": ["read"],
        "resources": ["skill://.kiro/skills/isekai/SKILL.md"],
        "includeMcpJson": True,
        "prompt": (
            "Use ISEKAI Core MCP for every Unit document write, Project edit, "
            "and test. Direct write and shell tools are intentionally unavailable."
        ),
    }
    existing = _read_json(project_root, KIRO_AGENT)
    if existing and existing != expected_agent and not managed:
        raise DistributionError(
            "refusing to replace unmanaged .kiro/agents/isekai-core.json"
        )
    return {
        KIRO_MCP: (json.dumps(mcp, indent=2, ensure_ascii=False) + "\n").encode(
            "utf-8"
        ),
        KIRO_AGENT: (
            json.dumps(expected_agent, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8"),
    }


def _profile_documents(project_root: Path, runtime: str) -> dict[Path, bytes]:
    managed = runtime in _state(project_root)["profiles"]
    if runtime == "codex":
        return {CODEX_CONFIG: _codex_document(project_root, managed=managed)}
    if runtime == "claude":
        return _claude_documents(project_root)
    if runtime == "kiro":
        return _kiro_documents(project_root, managed=managed)
    raise DistributionError(f"unsupported runtime: {runtime}")


def apply_execution_profile(
    project: str | Path,
    runtime: str,
) -> dict[str, Any]:
    requested = Path(project).expanduser().resolve()
    project_root = requested.parent if requested.is_file() else requested
    if runtime not in RUNTIMES:
        raise DistributionError(f"unsupported runtime: {runtime}")
    documents = _profile_documents(project_root, runtime)
    attempt_snapshots = [
        _snapshot(project_root, relative) for relative in documents
    ]
    state_before = _read_bytes(project_root, PROFILE_STATE)
    state = _state(project_root)
    profiles = state["profiles"]
    if runtime not in profiles:
        profiles[runtime] = {
            "snapshots": [
                _snapshot(project_root, relative) for relative in documents
            ]
        }
    written: list[str] = []
    try:
        for relative, content in documents.items():
            target = _project_file(project_root, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(target, content)
            written.append(relative.as_posix())
        _write_json(project_root, PROFILE_STATE, state)
        status = execution_profile_status(project_root, runtime)
        if not status["ready"]:
            raise DistributionError(
                "execution profile postflight failed: "
                + "; ".join(status["issues"])
            )
    except Exception as exc:
        _restore_profile_snapshots(project_root, attempt_snapshots, exc)
        state_path = _project_file(project_root, PROFILE_STATE)
        if state_before is None:
            state_path.unlink(missing_ok=True)
        else:
            write_bytes_atomic(state_path, state_before)
        raise
    return {"applied": True, "runtime": runtime, "files": written, **status}


def _restore_profile_snapshots(
    project_root: Path,
    snapshots: object,
    cause: Exception,
) -> None:
    errors: list[str] = []
    if not isinstance(snapshots, list):
        raise DistributionError("execution profile snapshots are invalid") from cause
    for snapshot in reversed(snapshots):
        try:
            if not isinstance(snapshot, dict) or not isinstance(
                snapshot.get("path"), str
            ):
                raise ValueError("invalid snapshot")
            relative = Path(snapshot["path"])
            target = _project_file(project_root, relative)
            if snapshot.get("existed") is True:
                encoded = snapshot.get("content_base64")
                if not isinstance(encoded, str):
                    raise ValueError("snapshot content is missing")
                mode = snapshot.get("mode")
                write_bytes_atomic(
                    target,
                    base64.b64decode(encoded, validate=True),
                    mode=mode if isinstance(mode, int) else None,
                )
            else:
                target.unlink(missing_ok=True)
        except Exception as exc:  # pragma: no cover - secondary failure
            errors.append(str(exc))
    if errors:
        raise DistributionError(
            "execution profile failed and could not be restored: " + "; ".join(errors)
        ) from cause


def execution_profile_status(
    project: str | Path,
    runtime: str,
) -> dict[str, Any]:
    requested = Path(project).expanduser().resolve()
    project_root = requested.parent if requested.is_file() else requested
    issues: list[str] = []
    try:
        if runtime == "codex":
            content = _read_bytes(project_root, CODEX_CONFIG)
            parsed = tomllib.loads(content.decode("utf-8")) if content else {}
            default_permissions = parsed.get("default_permissions")
            sandbox_mode = parsed.get("sandbox_mode")
            if (
                default_permissions != ":read-only"
                and sandbox_mode != "read-only"
            ):
                issues.append("Codex Project filesystem profile is not read-only")
            if (
                default_permissions is not None
                and default_permissions != ":read-only"
            ):
                issues.append("Codex default_permissions conflicts with read-only")
            if sandbox_mode is not None and sandbox_mode != "read-only":
                issues.append("Codex sandbox_mode conflicts with read-only")
            servers = parsed.get("mcp_servers", {})
            expected = {
                "command": str(_launcher(project_root)),
                "args": [
                    "mcp-serve",
                    "--project",
                    str(project_root.resolve()),
                    "--runtime",
                    "codex",
                ],
                "required": True,
                "enabled_tools": CORE_MCP_TOOLS,
            }
            if not isinstance(servers, dict) or servers.get(MCP_SERVER_ID) != expected:
                issues.append("Codex is not connected to the Project Core gateway")
        elif runtime == "claude":
            settings = _read_json(project_root, CLAUDE_SETTINGS)
            permissions = settings.get("permissions")
            deny = permissions.get("deny") if isinstance(permissions, dict) else None
            if not isinstance(deny, list) or any(tool not in deny for tool in _CLAUDE_DENY):
                issues.append("Claude direct write and shell tools are not denied")
            mcp = _read_json(project_root, CLAUDE_MCP).get("mcpServers")
            if not isinstance(mcp, dict) or mcp.get(MCP_SERVER_ID) != _mcp_entry(
                project_root, "claude"
            ):
                issues.append("Claude is not connected to the Project Core gateway")
        elif runtime == "kiro":
            mcp = _read_json(project_root, KIRO_MCP).get("mcpServers")
            if not isinstance(mcp, dict) or mcp.get(MCP_SERVER_ID) != _mcp_entry(
                project_root, "kiro"
            ):
                issues.append("Kiro is not connected to the Project Core gateway")
            agent = _read_json(project_root, KIRO_AGENT)
            if agent.get("tools") != ["read", "@mcp"]:
                issues.append("Kiro isekai-core profile exposes direct write tools")
        else:
            issues.append(f"unsupported runtime: {runtime}")
    except (DistributionError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        issues.append(str(exc))
    return {
        "ready": not issues,
        "configuration_ready": not issues,
        "runtime": runtime,
        "project": str(project_root),
        "boundary": "core-exclusive",
        "hooks": False,
        "scope": "project-local-host-configuration",
        "effective_enforcement": "host-controlled",
        "override_warning": (
            "higher-precedence host flags or managed policy can change the effective "
            "sandbox; this status verifies Project configuration, not the host process"
        ),
        "issues": list(dict.fromkeys(issues)),
    }
