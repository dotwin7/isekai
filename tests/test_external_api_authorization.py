from __future__ import annotations

import json
from pathlib import Path

import pytest

from isekai.workflow import (
    authorize_action,
    initialize_unit,
    propose_execution_envelope,
    record_decision,
    record_evidence,
    transition_unit,
)
from isekai.workflow.errors import AuthorizationError, EvidenceError
from isekai.workflow.session import update_checkpoint
from isekai.catalog.ai_dlc.unit.external_access import normalize_external_api_request

from test_core_workflow import make_project, materialize_unit_artifacts
from test_decision_lifecycle import authorize_test


EXTERNAL_POLICY = {
    "id": "openai-development",
    "credential_ref": "secret://openai/development",
    "environment": "development",
    "scheme": "https",
    "host": "api.openai.com",
    "path": "/v1/**",
    "methods": ["POST"],
    "max_requests": 2,
}


def make_l2_unit(tmp_path: Path, *, max_requests: int = 2) -> Path:
    project = make_project(tmp_path)
    manifest = json.loads(project.read_text(encoding="utf-8"))
    manifest["maximum_agent_level"] = "L2"
    project.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    unit = initialize_unit(project, "External API", project.parent / "units")
    materialize_unit_artifacts(unit)
    policy = {**EXTERNAL_POLICY, "max_requests": max_requests}
    propose_execution_envelope(
        unit,
        scope=["src/**", "tests/**"],
        stages=[
            {"name": "inception", "depth": "standard", "allowed_actions": ["read"]},
            {
                "name": "construction",
                "depth": "deep",
                "allowed_actions": ["read", "edit", "test", "external-api"],
            },
        ],
        allowed_actions=["read", "edit", "test", "external-api"],
        forbidden_actions=["remote", "deploy", "credential-access"],
        external_access=[policy],
        max_iterations=8,
        proposed_by="planner-agent",
    )
    transition_unit(unit, "inception")
    transition_unit(unit, "awaiting-inception-decision")
    record_decision(
        unit,
        gate="inception",
        outcome="approved",
        summary="개발 환경 외부 API 검증 범위를 승인한다.",
        rationale=["HTTPS 목적지와 호출 예산, 비밀 참조가 제한되어 있다."],
        alternatives=[],
        tradeoffs=["실제 자격증명 주입은 호스트 경계에 남긴다."],
        risks=["외부 서비스 호출은 비용과 데이터 전송을 유발할 수 있다."],
        references=["execution-envelope.json"],
        decided_by="human-reviewer",
    )
    transition_unit(unit, "construction")
    return unit


def test_l2_external_api_authorization_is_exact_and_budgeted(tmp_path: Path) -> None:
    unit = make_l2_unit(tmp_path, max_requests=1)

    allowed = authorize_action(
        unit,
        action="external-api",
        target="https://API.OPENAI.COM:443/v1/responses",
        method="post",
        credential_ref="secret://openai/development",
    )

    assert allowed["allowed"] is True
    assert allowed["target"] == "https://api.openai.com/v1/responses"
    assert allowed["method"] == "POST"
    assert allowed["environment"] == "development"
    assert allowed["remaining_requests"] == 0
    exhausted = authorize_action(
        unit,
        action="external-api",
        target="https://api.openai.com/v1/responses",
        method="POST",
        credential_ref="secret://openai/development",
    )
    assert exhausted["allowed"] is False
    assert "budget is exhausted" in exhausted["reason"]


@pytest.mark.parametrize(
    ("target", "method", "credential_ref"),
    [
        ("http://api.openai.com/v1/responses", "POST", "secret://openai/development"),
        ("https://api.openai.com/v1/responses?key=value", "POST", "secret://openai/development"),
        ("https://api.example.com/v1/responses", "POST", "secret://openai/development"),
        ("https://api.openai.com/v1/%2e%2e/admin", "POST", "secret://openai/development"),
        ("https://api.openai.com/v1/responses", "GET", "secret://openai/development"),
        ("https://api.openai.com/v1/responses", "POST", "sk-live-secret"),
    ],
)
def test_l2_external_api_authorization_fails_closed(
    tmp_path: Path,
    target: str,
    method: str,
    credential_ref: str,
) -> None:
    unit = make_l2_unit(tmp_path)

    result = authorize_action(
        unit,
        action="external-api",
        target=target,
        method=method,
        credential_ref=credential_ref,
    )

    assert result["allowed"] is False


def test_l1_project_cannot_propose_external_api_access(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "L1 boundary", project.parent / "units")

    with pytest.raises(AuthorizationError, match="maximum_agent_level L1"):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=[
                {
                    "name": "construction",
                    "depth": "deep",
                    "allowed_actions": ["external-api"],
                }
            ],
            allowed_actions=["external-api"],
            forbidden_actions=["credential-access"],
            external_access=[EXTERNAL_POLICY],
            max_iterations=1,
            proposed_by="planner-agent",
        )


@pytest.mark.parametrize(
    "override",
    [
        {"credential_ref": "sk-live-secret"},
        {"environment": "production"},
        {"scheme": "http"},
        {"host": "localhost"},
        {"host": "127.0.0.1"},
        {"host": "169.254.169.254"},
        {"host": "127.1"},
        {"host": "0x7f.0.0.1"},
        {"token": "raw-secret"},
    ],
)
def test_external_access_policy_rejects_secret_and_unsafe_fields(
    tmp_path: Path,
    override: dict[str, str],
) -> None:
    project = make_project(tmp_path)
    manifest = json.loads(project.read_text(encoding="utf-8"))
    manifest["maximum_agent_level"] = "L2"
    project.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    unit = initialize_unit(project, "Unsafe external policy", project.parent / "units")

    with pytest.raises(AuthorizationError, match="Execution Envelope rejected"):
        propose_execution_envelope(
            unit,
            scope=["src/**"],
            stages=[
                {
                    "name": "construction",
                    "depth": "deep",
                    "allowed_actions": ["external-api"],
                }
            ],
            allowed_actions=["external-api"],
            forbidden_actions=["credential-access"],
            external_access=[{**EXTERNAL_POLICY, **override}],
            max_iterations=1,
            proposed_by="planner-agent",
        )


@pytest.mark.parametrize(
    "target",
    [
        "https://127.0.0.1/admin",
        "https://10.0.0.1/api",
        "https://169.254.169.254/latest/meta-data",
        "https://127.1/admin",
        "https://0177.0.0.1/admin",
        "https://0x7f.0.0.1/admin",
    ],
)
def test_external_api_request_rejects_ip_literals(target: str) -> None:
    request, issue = normalize_external_api_request(
        target,
        "GET",
        "secret://provider/development",
    )

    assert request is None
    assert issue == "External API target must use an external DNS hostname"


def test_external_authorization_can_be_bound_to_verification_evidence(
    tmp_path: Path,
) -> None:
    unit = make_l2_unit(tmp_path)
    external = authorize_action(
        unit,
        action="external-api",
        target="https://api.openai.com/v1/responses",
        method="POST",
        credential_ref="secret://openai/development",
    )
    update_checkpoint(
        unit,
        completed=["개발 External API 호출"],
        pending=["통합 검증"],
        blocked_by=[],
        next_action="External API 결과를 통합 테스트로 검증한다.",
    )
    test_id = authorize_test(unit, target="tests/test_external.py")
    evidence = record_evidence(
        unit,
        passed=True,
        commands=[
            {
                "authorization_id": test_id,
                "external_authorization_ids": [external["authorization_id"]],
            }
        ],
        scope="development API integration",
        recorded_by="test-validator",
        notes="secret values were injected and redacted by the host",
    )

    assert evidence["passed"] is True
    persisted = json.loads((unit / "evidence/verification.json").read_text(encoding="utf-8"))
    assert persisted["commands"][0]["external_authorization_ids"] == [
        external["authorization_id"]
    ]


def test_external_evidence_rejects_unapproved_external_grant(tmp_path: Path) -> None:
    unit = make_l2_unit(tmp_path)
    test_id = authorize_test(unit, target="tests/test_external.py")

    with pytest.raises(EvidenceError, match="external-api authorization"):
        record_evidence(
            unit,
            passed=True,
            commands=[
                {
                    "authorization_id": test_id,
                    "external_authorization_ids": ["AUTH-NOT-FOUND"],
                }
            ],
            scope="development API integration",
            recorded_by="test-validator",
            notes="",
        )
