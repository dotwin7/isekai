from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

import isekai.mcp_server as mcp_server_module
from isekai.distribution.execution_profile import apply_execution_profile
from isekai.mcp_server import ProjectMcpServer, serve_mcp
from isekai.workflow import initialize_unit

from test_core_workflow import make_project
from test_foundation_release import make_foundation


def project_with_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Path:
    project = make_project(tmp_path).parent
    launcher = project / ".isekai/bin/isekai"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    apply_execution_profile(project, "codex")
    if monkeypatch is not None:
        monkeypatch.setattr(
            mcp_server_module,
            "doctor_install",
            lambda _project: {
                "ready": True,
                "runtimes": ["codex"],
                "issues": [],
            },
        )
    return project / "project.json"


def test_mcp_server_advertises_only_core_mediated_write_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_with_profile(tmp_path, monkeypatch)
    incoming = io.BytesIO(
        (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2099-01-01"},
                }
            )
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            + "\n"
        ).encode()
    )
    outgoing = io.BytesIO()

    assert (
        serve_mcp(
            project,
            runtime="codex",
            input_stream=incoming,
            output_stream=outgoing,
        )
        == 0
    )

    responses = [json.loads(line) for line in outgoing.getvalue().splitlines()]
    assert responses[0]["result"]["serverInfo"]["name"] == "isekai-core"
    assert responses[0]["result"]["protocolVersion"] == "2025-06-18"
    names = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert names == {
        "runtime_action",
        "catalog",
        "managed_edit",
        "artifact_write",
        "prove",
    }


def test_mcp_artifact_write_persists_plan_through_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_with_profile(tmp_path, monkeypatch)
    unit = initialize_unit(project, "MCP plan", tmp_path / "units")
    plan = unit / "plan.md"
    before = plan.read_bytes()
    content = "# 계획\n\ninception construction validation release operations learn\n"
    server = ProjectMcpServer(project, runtime="codex")

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "artifact_write",
                "arguments": {
                    "unit": str(unit),
                    "artifacts": [
                        {
                            "target": "plan.md",
                            "expected_digest": "sha256:"
                            + hashlib.sha256(before).hexdigest(),
                            "content": content,
                        }
                    ],
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is False
    assert response["result"]["structuredContent"]["result"]["written"] is True
    assert plan.read_text(encoding="utf-8") == content


def test_mcp_server_rejects_unit_from_another_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = project_with_profile(first_root, monkeypatch)
    second = make_project(second_root)
    unit = initialize_unit(second, "Other Project", second_root / "units")
    server = ProjectMcpServer(first, runtime="codex")

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "artifact_write",
                "arguments": {"unit": str(unit), "artifacts": []},
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "different Project" in response["result"]["content"][0]["text"]


def test_mcp_server_refuses_tools_after_host_custody_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_with_profile(tmp_path, monkeypatch)
    server = ProjectMcpServer(project, runtime="codex")
    config = project.parent / ".codex/config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(":read-only", ":workspace"),
        encoding="utf-8",
    )

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "runtime_action",
                "arguments": {
                    "action": "status",
                    "payload": {"project": str(project)},
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "execution guard changed" in response["result"]["content"][0]["text"]


def test_mcp_exposes_isekai_catalog_and_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_with_profile(tmp_path, monkeypatch)
    server = ProjectMcpServer(project, runtime="codex")

    catalog_response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "catalog", "arguments": {}},
        }
    )
    resources_response = server.handle(
        {"jsonrpc": "2.0", "id": 2, "method": "resources/list"}
    )
    resource_response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "resources/read",
            "params": {
                "uri": "isekai://runtime/catalog/ai-dlc"
            },
        }
    )

    assert catalog_response is not None
    assert catalog_response["result"]["isError"] is False
    catalog = catalog_response["result"]["structuredContent"]["result"]
    assert {entry["id"] for entry in catalog["entries"]} == {"ai-dlc"}
    assert resources_response is not None
    uris = {
        item["uri"] for item in resources_response["result"]["resources"]
    }
    assert "isekai://runtime/catalog/ai-dlc" in uris
    assert resource_response is not None
    catalog_entry = json.loads(resource_response["result"]["contents"][0]["text"])
    assert catalog_entry["id"] == "ai-dlc"


def test_mcp_server_refuses_tools_without_a_healthy_install_lock(
    tmp_path: Path,
) -> None:
    project = project_with_profile(tmp_path)
    server = ProjectMcpServer(project, runtime="codex")

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "catalog", "arguments": {}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "installation is not healthy" in response["result"]["content"][0]["text"]


def test_mcp_server_binds_foundation_actions_to_its_fixed_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project-root"
    external_root = tmp_path / "external-root"
    project_root.mkdir()
    external_root.mkdir()
    project = project_with_profile(project_root, monkeypatch)
    external_foundation = make_foundation(external_root)
    server = ProjectMcpServer(project, runtime="codex")

    response = server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "runtime_action",
                "arguments": {
                    "action": "foundation-decision",
                    "payload": {
                        "foundation": str(external_foundation),
                        "outcome": "approved",
                        "summary": "must stay inside the fixed Project context",
                        "decided_by": "reviewer",
                    },
                },
            },
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert "Foundation not selected" in response["result"]["content"][0]["text"]
    assert not (external_foundation / "decisions.json").exists()
