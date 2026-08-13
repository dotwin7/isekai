from __future__ import annotations

import ast
import json
import socket
from pathlib import Path
from typing import Any

import pytest

from isekai.catalog.agent_control.connectors import (
    ConnectorHandle,
    ConnectorRequest,
    ConnectorSnapshot,
)
from isekai.catalog.agent_control.connectors.nahonza import ConnectorTransportError
from isekai.catalog.agent_control.connectors.nahonza import NahonzaConnector
from isekai.catalog.agent_control.service import (
    approve_engagement,
    create_engagement,
    engagement_status,
    poll_execution,
    start_execution,
)
from isekai.catalog.agent_control.runtime_handlers import ACTION_HANDLERS
from isekai.runtime.request_fields import RuntimeContractError
from isekai.workflow.errors import WorkflowError
from isekai.workflow.errors import IntegrityError
from isekai.workflow import promote_project_knowledge

from test_core_workflow import make_project
from test_project_knowledge import _approve, _operating_unit, _propose


def _configure_project(project: Path) -> None:
    value = json.loads(project.read_text(encoding="utf-8"))
    value["agent_control"] = {
        "connectors": [
            {
                "id": "nahonza-offsec",
                "kind": "nahonza",
                "transport": "agent-api",
                "endpoint_ref": "env://NAHONZA_TEST_URL",
                "auth_ref": "env://NAHONZA_TEST_TOKEN",
                "allowed_operations": ["va", "verify"],
                "maximum_action_level": "L1",
            }
        ]
    }
    project.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


class FakeConnector:
    def __init__(self) -> None:
        self.requests: list[ConnectorRequest] = []

    def start(self, request: ConnectorRequest) -> ConnectorHandle:
        self.requests.append(request)
        return ConnectorHandle(remote_task_id=f"task-{request.execution_id}", status="queued")

    def poll(self, remote_task_id: str) -> ConnectorSnapshot:
        return ConnectorSnapshot(
            remote_task_id=remote_task_id,
            status="completed",
            result={"findings": [{"id": "F-001", "severity": "high"}]},
            phase="completed",
        )


class UncertainConnector(FakeConnector):
    def start(self, request: ConnectorRequest) -> ConnectorHandle:
        raise ConnectorTransportError("connection ended before taskId")


def _engagement(project: Path) -> Path:
    created = create_engagement(
        project,
        title="payment-api 취약점 진단",
        objective="승인된 범위의 취약점을 식별한다.",
        connector_id="nahonza-offsec",
        operation="va",
        scope=["asset:payment-api", "path:services/payment/**"],
        maximum_executions=4,
        created_by="operator",
    )
    return Path(created["path"])


def test_engagement_is_independent_from_ai_dlc_unit_and_supports_followups(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    units_root = project.parent / "units"
    units_before = sorted(units_root.iterdir()) if units_root.is_dir() else []
    engagement = _engagement(project)

    proposed = engagement_status(engagement)
    assert proposed["engagement"]["status"] == "proposed"
    assert proposed["executions"] == []
    assert (sorted(units_root.iterdir()) if units_root.is_dir() else []) == units_before

    approved = approve_engagement(
        engagement,
        decided_by="human-reviewer",
        summary="범위와 실행 4회를 승인한다.",
    )
    assert approved["engagement"]["status"] == "active"
    assert approved["approval"]["approval_digest"].startswith("sha256:")

    connector = FakeConnector()
    started = start_execution(
        engagement,
        prompt="최초 취약점 진단을 수행해줘.",
        scope=["asset:payment-api"],
        requested_by="operator",
        _connector_factory=lambda _config: connector,
    )
    first_id = started["execution"]["id"]
    assert started["execution"]["status"] == "queued"
    assert connector.requests[0].operation == "va"

    completed = poll_execution(
        engagement,
        execution_id=first_id,
        _connector_factory=lambda _config: connector,
    )
    assert completed["execution"]["status"] == "completed"
    assert completed["execution"]["result_digest"].startswith("sha256:")
    receipt = json.loads(
        (engagement / "results" / f"{first_id}.json").read_text(encoding="utf-8")
    )
    assert receipt["execution_id"] == first_id
    assert receipt["result"]["findings"][0]["id"] == "F-001"

    followup = start_execution(
        engagement,
        prompt="F-001의 오탐 가능성을 추가 확인해줘.",
        scope=["asset:payment-api"],
        requested_by="operator",
        prior_execution_ids=[first_id],
        _connector_factory=lambda _config: connector,
    )
    assert followup["execution"]["prior_execution_ids"] == [first_id]
    assert len(engagement_status(engagement)["executions"]) == 2
    assert (sorted(units_root.iterdir()) if units_root.is_dir() else []) == units_before


def test_execution_rejects_scope_expansion_and_parallel_start(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")
    connector = FakeConnector()

    with pytest.raises(WorkflowError, match="scope subset"):
        start_execution(
            engagement,
            prompt="scan",
            scope=["asset:other"],
            requested_by="operator",
            _connector_factory=lambda _config: connector,
        )

    start_execution(
        engagement,
        prompt="scan",
        scope=["asset:payment-api"],
        requested_by="operator",
        _connector_factory=lambda _config: connector,
    )
    with pytest.raises(WorkflowError, match="in-flight"):
        start_execution(
            engagement,
            prompt="second scan",
            scope=["asset:payment-api"],
            requested_by="operator",
            _connector_factory=lambda _config: connector,
        )


def test_post_connection_loss_is_recorded_as_uncertain_without_retry(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")

    result = start_execution(
        engagement,
        prompt="scan",
        scope=["asset:payment-api"],
        requested_by="operator",
        _connector_factory=lambda _config: UncertainConnector(),
    )

    assert result["execution"]["status"] == "uncertain"
    assert result["execution"]["remote_task_id"] is None
    assert len(engagement_status(engagement)["executions"]) == 1


def test_preview_catalog_actions_fail_closed(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _configure_project(project)

    with pytest.raises(RuntimeContractError, match="Catalog entry is not active"):
        ACTION_HANDLERS["agent-engagement-create"](
            {
                "project": str(project),
                "title": "preview",
                "objective": "must not execute",
                "connector_id": "nahonza-offsec",
                "operation": "va",
                "scope": ["asset:payment-api"],
                "maximum_executions": 1,
                "created_by": "operator",
                "knowledge_entry_ids": [],
            }
        )
    assert not (project.parent / "engagements").exists()


def test_agent_control_package_does_not_import_ai_dlc() -> None:
    root = Path(__file__).resolve().parents[1] / "src/isekai/catalog/agent_control"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "isekai.catalog.ai_dlc"
            ):
                violations.append(str(path.relative_to(root)))
            if isinstance(node, ast.Import) and any(
                alias.name.startswith("isekai.catalog.ai_dlc") for alias in node.names
            ):
                violations.append(str(path.relative_to(root)))
    assert violations == []


def test_unknown_prior_execution_is_rejected(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")

    with pytest.raises(WorkflowError, match="prior_execution_ids"):
        start_execution(
            engagement,
            prompt="follow-up",
            scope=["asset:payment-api"],
            requested_by="operator",
            prior_execution_ids=["EXEC-unknown"],
            _connector_factory=lambda _config: FakeConnector(),
        )


def test_approved_engagement_contract_is_digest_bound(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")
    path = engagement / "engagement.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["scope"].append("asset:unapproved")
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    with pytest.raises(IntegrityError, match="approved contract has changed"):
        start_execution(
            engagement,
            prompt="scan",
            scope=["asset:payment-api"],
            requested_by="operator",
            _connector_factory=lambda _config: FakeConnector(),
        )


def test_engagement_symlink_root_is_rejected(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    alias = engagement.parent / "ENG-20000101000000-00000000"
    try:
        alias.symlink_to(engagement, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable")

    with pytest.raises(WorkflowError, match="must be a real directory"):
        engagement_status(alias)


def test_engagement_pins_only_explicitly_selected_project_knowledge(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    source = _operating_unit(project)
    candidate = _propose(source, "service-id-format-v1")
    reference = str(candidate["reference"])
    _approve(source, reference)
    release = promote_project_knowledge(source, candidate=reference)["release"]

    created = create_engagement(
        project,
        title="knowledge-bound review",
        objective="승인된 프로젝트 관례를 반영한다.",
        connector_id="nahonza-offsec",
        operation="verify",
        scope=["path:services/**"],
        maximum_executions=1,
        created_by="operator",
        knowledge_entry_ids=["service-id-format-v1"],
    )
    context = created["engagement"]["knowledge_context"]

    assert context["release_digest"] == release["release_digest"]
    assert context["context_digest"].startswith("sha256:")
    assert context["entries"] == [
        {
            "id": "service-id-format-v1",
            "kind": "convention",
            "title": "서비스 식별자 표기 규칙",
            "statement": "서비스 식별자는 소문자 kebab-case로 기록한다.",
        }
    ]


class FakeResponse:
    def __init__(self, status: int, value: dict[str, Any]) -> None:
        self.status = status
        self.value = value

    def getcode(self) -> int:
        return self.status

    def read(self, _limit: int) -> bytes:
        return json.dumps(self.value).encode("utf-8")

    def close(self) -> None:
        pass


def test_nahonza_connector_uses_polling_and_binds_approved_knowledge() -> None:
    requests: list[Any] = []
    responses = iter(
        [
            FakeResponse(202, {"taskId": "task-1", "status": "accepted"}),
            FakeResponse(
                200,
                {
                    "taskId": "task-1",
                    "status": "completed",
                    "phase": "completed",
                    "result": {"summary": "done"},
                },
            ),
        ]
    )

    def opener(request: Any, *, timeout: float) -> FakeResponse:
        assert timeout == 10
        requests.append(request)
        return next(responses)

    connector = NahonzaConnector(
        endpoint="https://nahonza.example",
        token="opaque-test-token",
        timeout_seconds=10,
        opener=opener,
    )
    handle = connector.start(
        ConnectorRequest(
            execution_id="EXEC-1",
            operation="va",
            prompt="scan",
            scope=("asset:payment-api",),
            knowledge_context={
                "context_digest": "sha256:" + "a" * 64,
                "entries": [
                    {
                        "id": "auth-v1",
                        "kind": "convention",
                        "title": "인증 관례",
                        "statement": "mTLS를 사용한다.",
                    }
                ],
            },
        )
    )
    snapshot = connector.poll(handle.remote_task_id)

    assert handle.remote_task_id == "task-1"
    assert snapshot.status == "completed"
    assert snapshot.result == {"summary": "done"}
    start_body = json.loads(requests[0].data.decode("utf-8"))
    assert start_body["workflow"] == "va"
    assert "ISEKAI approved Project Knowledge" in start_body["prompt"]
    assert start_body["context"]["isekai"]["scope"] == ["asset:payment-api"]
    assert requests[0].get_header("Idempotency-key") == "EXEC-1"
    assert requests[1].full_url.endswith("/api/v1/agent/status/task-1")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://nahonza.example",
        "https://127.0.0.1",
        "https://localhost",
        "https://nahonza.example:8443",
        "https://user@nahonza.example",
    ],
)
def test_nahonza_connector_rejects_unsafe_endpoints(endpoint: str) -> None:
    with pytest.raises(WorkflowError, match="external HTTPS DNS URL|IP literal"):
        NahonzaConnector(endpoint=endpoint, token="opaque-test-token")


def test_nahonza_connector_rejects_non_global_dns() -> None:
    connector = NahonzaConnector(
        endpoint="https://nahonza.example",
        token="opaque-test-token",
        resolver=lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))
        ],
    )

    with pytest.raises(WorkflowError, match="only to global addresses"):
        connector.start(
            ConnectorRequest(
                execution_id="EXEC-1",
                operation="va",
                prompt="scan",
                scope=("asset:payment-api",),
                knowledge_context=None,
            )
        )


def test_approved_connector_contract_and_project_level_are_rechecked(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")
    original_ledger = (engagement / "executions.json").read_bytes()

    value = json.loads(project.read_text(encoding="utf-8"))
    value["agent_control"]["connectors"][0]["endpoint_ref"] = "env://OTHER_URL"
    project.write_text(json.dumps(value) + "\n", encoding="utf-8")

    with pytest.raises(IntegrityError, match="connector contract has changed"):
        start_execution(
            engagement,
            prompt="scan",
            scope=["asset:payment-api"],
            requested_by="operator",
            _connector_factory=lambda _config: FakeConnector(),
        )
    assert (engagement / "executions.json").read_bytes() == original_ledger

    value["agent_control"]["connectors"][0]["endpoint_ref"] = "env://NAHONZA_TEST_URL"
    value["maximum_agent_level"] = "L0"
    project.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="exceeds current Project L0"):
        start_execution(
            engagement,
            prompt="scan",
            scope=["asset:payment-api"],
            requested_by="operator",
            _connector_factory=lambda _config: FakeConnector(),
        )


def test_execution_and_result_receipt_tampering_fail_closed(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")
    connector = FakeConnector()
    started = start_execution(
        engagement,
        prompt="scan",
        scope=["asset:payment-api"],
        requested_by="operator",
        _connector_factory=lambda _config: connector,
    )
    execution_id = started["execution"]["id"]
    poll_execution(
        engagement,
        execution_id=execution_id,
        _connector_factory=lambda _config: connector,
    )

    receipt_path = engagement / "results" / f"{execution_id}.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["result"] = {"forged": True}
    receipt_path.write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    with pytest.raises(IntegrityError, match="result receipt is invalid"):
        engagement_status(engagement)

    receipt_path.unlink()
    with pytest.raises(IntegrityError, match="result receipt is missing"):
        engagement_status(engagement)


def test_execution_ledger_digest_chain_detects_tampering(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")
    started = start_execution(
        engagement,
        prompt="scan",
        scope=["asset:payment-api"],
        requested_by="operator",
        _connector_factory=lambda _config: FakeConnector(),
    )
    ledger_path = engagement / "executions.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["executions"][0]["scope"] = ["asset:unapproved"]
    ledger_path.write_text(json.dumps(ledger) + "\n", encoding="utf-8")

    with pytest.raises(IntegrityError, match="digest does not match"):
        engagement_status(engagement)
    assert started["execution"]["execution_digest"].startswith("sha256:")


def test_engagement_approval_rolls_back_when_approval_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import isekai.catalog.agent_control.service as service_module

    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    original_write = service_module.write_json

    def fail_approval(root: Path, relative: str | Path, value: dict[str, Any]) -> None:
        if Path(relative) == Path("approval.json"):
            raise OSError("simulated approval write failure")
        original_write(root, relative, value)

    monkeypatch.setattr(service_module, "write_json", fail_approval)

    with pytest.raises(OSError, match="simulated approval write failure"):
        approve_engagement(engagement, decided_by="human", summary="approved")

    value = json.loads((engagement / "engagement.json").read_text(encoding="utf-8"))
    assert value["status"] == "proposed"
    assert not (engagement / "approval.json").exists()


def test_engagement_creation_does_not_publish_partial_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import isekai.catalog.agent_control.service as service_module

    project = make_project(tmp_path)
    _configure_project(project)
    original_write = service_module.write_json

    def fail_ledger(root: Path, relative: str | Path, value: dict[str, Any]) -> None:
        if Path(relative) == Path("executions.json"):
            original_write(root, relative, value)
            raise OSError("simulated ledger write failure")
        original_write(root, relative, value)

    monkeypatch.setattr(service_module, "write_json", fail_ledger)

    with pytest.raises(OSError, match="simulated ledger write failure"):
        _engagement(project)

    engagements = project.parent / "engagements"
    assert not list(engagements.glob("ENG-*"))
    assert not list(engagements.glob(".*.stage-*"))


def test_result_persistence_rolls_back_receipt_when_ledger_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import isekai.catalog.agent_control.service as service_module

    project = make_project(tmp_path)
    _configure_project(project)
    engagement = _engagement(project)
    approve_engagement(engagement, decided_by="human", summary="approved")
    connector = FakeConnector()
    started = start_execution(
        engagement,
        prompt="scan",
        scope=["asset:payment-api"],
        requested_by="operator",
        _connector_factory=lambda _config: connector,
    )
    execution_id = started["execution"]["id"]
    original_ledger = (engagement / "executions.json").read_bytes()
    original_write = service_module.write_json

    def fail_ledger(root: Path, relative: str | Path, value: dict[str, Any]) -> None:
        if Path(relative) == Path("executions.json"):
            original_write(root, relative, value)
            raise OSError("simulated ledger write failure")
        original_write(root, relative, value)

    monkeypatch.setattr(service_module, "write_json", fail_ledger)

    with pytest.raises(OSError, match="simulated ledger write failure"):
        poll_execution(
            engagement,
            execution_id=execution_id,
            _connector_factory=lambda _config: connector,
        )

    assert (engagement / "executions.json").read_bytes() == original_ledger
    assert not (engagement / "results" / f"{execution_id}.json").exists()
