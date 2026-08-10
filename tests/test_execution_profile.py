from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from isekai.distribution.execution_profile import (
    apply_execution_profile,
    execution_profile_status,
)
from isekai.distribution.release import DistributionError

from test_core_workflow import make_project


def project_with_launcher(tmp_path: Path) -> Path:
    manifest = make_project(tmp_path)
    launcher = manifest.parent / ".isekai/bin/isekai"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    return manifest.parent


def test_codex_profile_preserves_config_and_forces_core_gateway(
    tmp_path: Path,
) -> None:
    project = project_with_launcher(tmp_path)
    config = project / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('model = "gpt-test"\n', encoding="utf-8")

    result = apply_execution_profile(project, "codex")
    parsed = tomllib.loads(config.read_text(encoding="utf-8"))

    assert result["ready"] is True
    assert result["configuration_ready"] is True
    assert result["hooks"] is False
    assert result["scope"] == "project-local-host-configuration"
    assert result["effective_enforcement"] == "host-controlled"
    assert parsed["model"] == "gpt-test"
    assert parsed["default_permissions"] == ":read-only"
    assert parsed["mcp_servers"]["isekai_core"]["args"] == [
        "mcp-serve",
        "--project",
        str(project),
        "--runtime",
        "codex",
    ]
    assert parsed["mcp_servers"]["isekai_core"]["required"] is True
    assert parsed["mcp_servers"]["isekai_core"]["enabled_tools"] == [
        "runtime_action",
        "catalog",
        "managed_edit",
        "artifact_write",
        "prove",
    ]


def test_claude_profile_denies_direct_writers_without_hooks(tmp_path: Path) -> None:
    project = project_with_launcher(tmp_path)
    settings = project / ".claude/settings.json"
    settings.parent.mkdir()
    settings.write_text('{"permissions":{"allow":["Read"]}}\n', encoding="utf-8")

    result = apply_execution_profile(project, "claude")
    written = json.loads(settings.read_text(encoding="utf-8"))
    mcp = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))

    assert result["ready"] is True
    assert set(written["permissions"]["deny"]) == {
        "Edit",
        "Write",
        "NotebookEdit",
        "Bash",
    }
    assert "hooks" not in written
    assert mcp["mcpServers"]["isekai_core"]["type"] == "stdio"


def test_kiro_profile_exposes_read_and_core_tools_only(tmp_path: Path) -> None:
    project = project_with_launcher(tmp_path)

    result = apply_execution_profile(project, "kiro")
    agent = json.loads(
        (project / ".kiro/agents/isekai-core.json").read_text(encoding="utf-8")
    )

    assert result["ready"] is True
    assert agent["tools"] == ["read", "@mcp"]
    assert "write" not in agent["tools"]
    assert "shell" not in agent["tools"]
    assert "hooks" not in agent


def test_profile_status_fails_closed_after_permission_tampering(
    tmp_path: Path,
) -> None:
    project = project_with_launcher(tmp_path)
    apply_execution_profile(project, "codex")
    config = project / ".codex/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(":read-only", ":workspace"),
        encoding="utf-8",
    )

    status = execution_profile_status(project, "codex")

    assert status["ready"] is False
    assert "not read-only" in "; ".join(status["issues"])


def test_codex_profile_upgrades_the_legacy_owned_mcp_entry(tmp_path: Path) -> None:
    project = project_with_launcher(tmp_path)
    config = project / ".codex/config.toml"
    config.parent.mkdir()
    launcher = project / ".isekai/bin/isekai"
    config.write_text(
        'default_permissions = ":read-only"\n\n'
        '[mcp_servers.isekai_core]\n'
        f'command = "{launcher}"\n'
        'args = ["mcp-serve", "--project", '
        f'"{project}", "--runtime", "codex"]\n',
        encoding="utf-8",
    )

    result = apply_execution_profile(project, "codex")
    entry = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"][
        "isekai_core"
    ]

    assert result["ready"] is True
    assert entry["required"] is True
    assert entry["enabled_tools"] == [
        "runtime_action",
        "catalog",
        "managed_edit",
        "artifact_write",
        "prove",
    ]


def test_codex_profile_rejects_a_conflicting_explicit_sandbox(tmp_path: Path) -> None:
    project = project_with_launcher(tmp_path)
    config = project / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text(
        'default_permissions = ":read-only"\n'
        'sandbox_mode = "workspace-write"\n',
        encoding="utf-8",
    )

    with pytest.raises(DistributionError, match="sandbox_mode conflicts"):
        apply_execution_profile(project, "codex")

    status = execution_profile_status(project, "codex")
    assert status["ready"] is False
    assert "sandbox_mode conflicts" in "; ".join(status["issues"])


def test_reapply_upgrades_an_owned_codex_tool_allowlist(tmp_path: Path) -> None:
    project = project_with_launcher(tmp_path)
    apply_execution_profile(project, "codex")
    config = project / ".codex/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'enabled_tools = ["runtime_action", "catalog", '
            '"managed_edit", "artifact_write", "prove"]',
            'enabled_tools = ["runtime_action"]',
        ),
        encoding="utf-8",
    )

    result = apply_execution_profile(project, "codex")
    entry = tomllib.loads(config.read_text(encoding="utf-8"))["mcp_servers"][
        "isekai_core"
    ]

    assert result["ready"] is True
    assert entry["enabled_tools"] == [
        "runtime_action",
        "catalog",
        "managed_edit",
        "artifact_write",
        "prove",
    ]
