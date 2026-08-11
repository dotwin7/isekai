from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, BinaryIO, Mapping

from . import __version__
from .distribution.install import doctor_install
from .distribution.execution_profile import execution_profile_status
from .runtime.actions import ACTION_HANDLERS
from .runtime_contract import dispatch
from .workflow.catalog import (
    catalog_resources,
    load_catalog,
    read_catalog_resource,
)
from .workflow.active_binding import project_manifest_for_unit
from .workflow.project import load_project
from .workflow.session import discover_project


MCP_SERVER_NAME = "isekai-core"
MCP_PROTOCOL_VERSION = "2025-06-18"
_FOUNDATION_ACTIONS = {
    "release-check",
    "foundation-decision",
    "foundation-evidence",
    "foundation-promote",
}


def _tool_schemas() -> list[dict[str, Any]]:
    change_schema = {
        "type": "object",
        "required": ["target", "expected_digest", "content"],
        "properties": {
            "target": {"type": "string"},
            "expected_digest": {
                "type": "string",
                "description": "Current sha256:... digest, or absent for creation",
            },
            "content": {"type": "string"},
        },
        "additionalProperties": False,
    }
    return [
        {
            "name": "runtime_action",
            "description": (
                "Run an ISEKAI lifecycle action. Edit and test authorizations are "
                "intentionally refused; use managed_edit or prove."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["action", "payload"],
                "properties": {
                    "action": {"type": "string", "enum": sorted(ACTION_HANDLERS)},
                    "payload": {"type": "object"},
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "catalog",
            "description": (
                "Read the versioned Catalog entries registered to this "
                "ISEKAI Runtime."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "managed_edit",
            "description": (
                "Atomically validate, authorize, apply, and receipt a Project file "
                "edit batch inside ISEKAI Core."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["unit", "changes"],
                "properties": {
                    "unit": {"type": "string"},
                    "changes": {
                        "type": "array",
                        "minItems": 1,
                        "items": change_schema,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "artifact_write",
            "description": (
                "Persist Unit documents through Core. Approved semantic content "
                "requires a pending amendment; acceptance checkboxes may only move "
                "forward as progress."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["unit", "artifacts"],
                "properties": {
                    "unit": {"type": "string"},
                    "artifacts": {
                        "type": "array",
                        "minItems": 1,
                        "items": change_schema,
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "prove",
            "description": (
                "Authorize, execute, and receipt one verification command in a "
                "disposable copy of the Project."
            ),
            "inputSchema": {
                "type": "object",
                "required": ["unit", "target", "command"],
                "properties": {
                    "unit": {"type": "string"},
                    "target": {"type": "string"},
                    "command": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 1800,
                        "default": 300,
                    },
                },
                "additionalProperties": False,
            },
        },
    ]


class ProjectMcpServer:
    def __init__(self, project: str | Path, *, runtime: str) -> None:
        self.project_manifest = discover_project(project).resolve()
        self.project_root = self.project_manifest.parent
        self.runtime = runtime

    def _same_project(self, candidate: Path) -> bool:
        return candidate.resolve() == self.project_manifest

    def _selected_foundation(self) -> Path:
        _manifest, _project, foundation, _extensions = load_project(
            self.project_manifest
        )
        return foundation.root.resolve()

    def _bound_payload(
        self,
        action: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        values = dict(payload)
        project = values.get("project")
        if project is not None and not isinstance(project, str):
            raise ValueError("Core broker project must be a string")
        if project is not None and not self._same_project(discover_project(project)):
            raise ValueError("Core broker request targets a different Project")
        # Project-scoped defaults must not depend on the MCP process working
        # directory. Bind every action, including calls that omitted --project.
        values["project"] = str(self.project_manifest)
        unit = values.get("unit")
        if unit is not None:
            if not isinstance(unit, str):
                raise ValueError("Core broker Unit must be a string")
            unit_path = Path(unit).expanduser().resolve()
            if not self._same_project(project_manifest_for_unit(unit_path)):
                raise ValueError("Core broker Unit belongs to a different Project")
            values["unit"] = str(unit_path)
        if "path" in values:
            raw_path = values["path"]
            if not isinstance(raw_path, str):
                raise ValueError("Core broker init path must be a string")
            requested_path = Path(raw_path).expanduser().resolve()
            if requested_path != self.project_root:
                raise ValueError("Core broker init path must be its fixed Project root")
        if action == "init":
            values["path"] = str(self.project_root)
        if action in _FOUNDATION_ACTIONS:
            selected_foundation = self._selected_foundation()
            requested_foundation = values.get("foundation")
            if requested_foundation is not None:
                if not isinstance(requested_foundation, str):
                    raise ValueError("Core broker Foundation must be a string")
                requested_path = Path(requested_foundation).expanduser()
                if not requested_path.is_absolute():
                    requested_path = self.project_root / requested_path
                if requested_path.resolve() != selected_foundation:
                    raise ValueError(
                        "Core broker request targets a Foundation not selected by "
                        "its fixed Project"
                    )
            values["foundation"] = str(selected_foundation)
        return values

    def _ensure_execution_ready(self) -> None:
        self._ensure_project_ready()
        profile = execution_profile_status(self.project_root, self.runtime)
        if not profile["ready"]:
            raise ValueError(
                "Project execution guard changed: " + "; ".join(profile["issues"])
            )

    def _ensure_project_ready(self) -> None:
        health = doctor_install(self.project_root)
        runtimes = health.get("runtimes")
        if (
            health.get("ready") is not True
            or not isinstance(runtimes, list)
            or self.runtime not in runtimes
        ):
            issues = health.get("issues")
            details = (
                "; ".join(str(issue) for issue in issues)
                if isinstance(issues, list) and issues
                else f"{self.runtime} Runtime is not installed for this Project"
            )
            raise ValueError("Project installation is not healthy: " + details)

    def _call_tool(self, name: str, arguments: object) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ValueError("MCP tool arguments must be an object")
        self._ensure_execution_ready()
        if name == "catalog":
            result = dispatch("catalog-status", {})
        elif name == "runtime_action":
            action = arguments.get("action")
            payload = arguments.get("payload")
            if not isinstance(action, str) or action not in ACTION_HANDLERS:
                raise ValueError("unsupported ISEKAI runtime action")
            if not isinstance(payload, dict):
                raise ValueError("runtime_action payload must be an object")
            result = dispatch(action, self._bound_payload(action, payload))
        elif name == "managed_edit":
            result = dispatch(
                "managed-edit",
                self._bound_payload(
                    "managed-edit",
                    {"unit": arguments.get("unit"), "changes": arguments.get("changes")}
                ),
            )
        elif name == "artifact_write":
            result = dispatch(
                "artifact-write",
                self._bound_payload(
                    "artifact-write",
                    {
                        "unit": arguments.get("unit"),
                        "artifacts": arguments.get("artifacts"),
                    }
                ),
            )
        elif name == "prove":
            result = dispatch(
                "prove",
                self._bound_payload(
                    "prove",
                    {
                        "unit": arguments.get("unit"),
                        "target": arguments.get("target"),
                        "command": arguments.get("command"),
                        "timeout_seconds": arguments.get("timeout_seconds", 300),
                    }
                ),
            )
        else:
            raise ValueError(f"unknown MCP tool: {name}")
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        return {
            "content": [{"type": "text", "text": encoded}],
            "structuredContent": result,
            "isError": False,
        }

    def handle(self, request: object) -> dict[str, Any] | None:
        if not isinstance(request, dict):
            return self._error(None, -32600, "request must be an object")
        request_id = request.get("id")
        method = request.get("method")
        if not isinstance(method, str):
            return self._error(request_id, -32600, "request method is required")
        if request_id is None:
            return None
        try:
            if method == "initialize":
                return self._result(
                    request_id,
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {
                            "tools": {"listChanged": False},
                            "resources": {"listChanged": False},
                        },
                        "serverInfo": {
                            "name": MCP_SERVER_NAME,
                            "version": __version__,
                        },
                        "instructions": (
                            "ISEKAI Core is the exclusive writer. Use artifact_write "
                            "for Unit documents, managed_edit for Project files, and "
                            "prove for verification. Never use host write tools."
                        ),
                    },
                )
            if method == "ping":
                return self._result(request_id, {})
            if method == "tools/list":
                return self._result(request_id, {"tools": _tool_schemas()})
            if method == "resources/list":
                self._ensure_execution_ready()
                catalog = load_catalog()
                return self._result(
                    request_id,
                    {"resources": catalog_resources(catalog)},
                )
            if method == "resources/read":
                params = request.get("params")
                if not isinstance(params, dict) or not isinstance(
                    params.get("uri"), str
                ):
                    raise ValueError("resources/read requires uri")
                self._ensure_execution_ready()
                catalog = load_catalog()
                content = read_catalog_resource(catalog, str(params["uri"]))
                return self._result(request_id, {"contents": [content]})
            if method == "tools/call":
                params = request.get("params")
                if not isinstance(params, dict) or not isinstance(
                    params.get("name"), str
                ):
                    raise ValueError("tools/call requires name and arguments")
                return self._result(
                    request_id,
                    self._call_tool(
                        str(params["name"]), params.get("arguments", {})
                    ),
                )
            return self._error(request_id, -32601, f"method not found: {method}")
        except Exception as exc:
            if method == "tools/call":
                message = json.dumps(
                    {"error": str(exc)}, ensure_ascii=False, indent=2
                )
                return self._result(
                    request_id,
                    {
                        "content": [{"type": "text", "text": message}],
                        "isError": True,
                    },
                )
            return self._error(request_id, -32602, str(exc))

    @staticmethod
    def _result(request_id: object, result: object) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(
        request_id: object, code: int, message: str
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": code, "message": message},
        }


def serve_mcp(
    project: str | Path,
    *,
    runtime: str,
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
) -> int:
    server = ProjectMcpServer(project, runtime=runtime)
    incoming = input_stream or sys.stdin.buffer
    outgoing = output_stream or sys.stdout.buffer
    for raw_line in incoming:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
            response = server.handle(request)
        except (UnicodeError, json.JSONDecodeError) as exc:
            response = ProjectMcpServer._error(None, -32700, str(exc))
        if response is not None:
            outgoing.write(
                json.dumps(
                    response,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
            outgoing.flush()
    return 0
