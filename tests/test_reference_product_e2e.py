from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from isekai.distribution.install import (
    _install_from_verified_checkout as install_from_checkout,
)
from isekai.workflow.unit.lifecycle import verify_unit


ROOT = Path(__file__).resolve().parents[1]
STARTER = ROOT / "examples/reference-product/starter"
COMPLETED = ROOT / "examples/reference-product/completed"
EXTENSION = ROOT / "examples/reference-product/extension"
GOLDEN_UNITS = COMPLETED / "units"
HANGUL = re.compile(r"[가-힣]")
KOREAN_DOCUMENT_HEADINGS = {
    "intent.md": "# 기능 제안 우선순위 결정",
    "requirements.md": "# 요구사항",
    "architecture.md": "# 아키텍처",
    "implementation-guide.md": "# 구현 가이드",
    "plan.md": "# Level-1 계획",
    "acceptance.md": "# 인수 조건",
    "release.md": "# 릴리스",
    "operations.md": "# 운영",
}


def _golden_unit() -> Path:
    units = sorted(path for path in GOLDEN_UNITS.iterdir() if path.is_dir())
    assert len(units) == 1, "the Reference Product must expose one Golden Unit"
    return units[0]


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _assert_hangul(label: str, value: object) -> None:
    assert isinstance(value, str) and HANGUL.search(value), (
        f"{label} must be written in Korean: {value!r}"
    )


def _assert_korean_human_artifacts(unit: Path) -> None:
    unit_value = _json(unit / "unit.json")
    context = _json(unit / "context-receipt.json")
    unit_id = unit_value.get("id")
    assert isinstance(unit_id, str)
    assert re.fullmatch(r"UNIT-\d{8}-[A-F0-9]{32}", unit_id)
    assert unit.name == unit_id.lower()
    assert unit.name.isascii()
    assert unit_value["document_language"] == "ko"
    assert context["document_language"] == "ko"

    for relative, heading in KOREAN_DOCUMENT_HEADINGS.items():
        content = (unit / relative).read_text(encoding="utf-8")
        assert content.startswith(heading), f"{relative} must start with {heading}"
        assert HANGUL.search(content), f"{relative} must contain Korean content"

    for field in ("title", "goal", "expected_outcome"):
        _assert_hangul(f"unit.{field}", unit_value.get(field))
    for field in ("constraints", "acceptance_criteria"):
        values = unit_value.get(field)
        assert isinstance(values, list)
        for index, value in enumerate(values):
            _assert_hangul(f"unit.{field}[{index}]", value)

    envelope = _json(unit / "execution-envelope.json")
    stages = envelope.get("stages")
    assert isinstance(stages, list)
    for index, stage in enumerate(stages):
        assert isinstance(stage, dict)
        _assert_hangul(f"envelope.stages[{index}].reason", stage.get("reason"))

    checkpoint = _json(unit / "checkpoint.json")
    _assert_hangul("checkpoint.next_action", checkpoint.get("next_action"))
    for field in ("completed", "pending", "blocked_by"):
        values = checkpoint.get(field)
        assert isinstance(values, list)
        for index, value in enumerate(values):
            _assert_hangul(f"checkpoint.{field}[{index}]", value)

    verification = _json(unit / "evidence/verification.json")
    _assert_hangul("evidence.scope", verification.get("scope"))

    decisions = _json(unit / "decisions.json").get("decisions")
    assert isinstance(decisions, list)
    assert [
        decision.get("gate")
        for decision in decisions
        if isinstance(decision, dict)
    ] == ["inception", "architecture", "release", "operation"]
    for index, decision in enumerate(decisions):
        assert isinstance(decision, dict)
        assert decision.get("type") == "human-decision"
        assert decision.get("outcome") == "approved"
        assert decision.get("decided_by") == "reference-product-owner"
        _assert_hangul(f"decisions[{index}].summary", decision.get("summary"))
        for field in ("rationale", "tradeoffs", "risks"):
            values = decision.get(field)
            assert isinstance(values, list)
            for value_index, value in enumerate(values):
                _assert_hangul(f"decisions[{index}].{field}[{value_index}]", value)
        alternatives = decision.get("alternatives")
        assert isinstance(alternatives, list)
        for alternative_index, alternative in enumerate(alternatives):
            assert isinstance(alternative, dict)
            _assert_hangul(
                f"decisions[{index}].alternatives[{alternative_index}].option",
                alternative.get("option"),
            )
            _assert_hangul(
                f"decisions[{index}].alternatives[{alternative_index}].reason",
                alternative.get("reason"),
            )


def _portable_command(command: object) -> object:
    if isinstance(command, str) and " -m unittest " in command:
        return "python -m unittest " + command.split(" -m unittest ", 1)[1]
    return command


def _evidence_contract(value: dict[str, object]) -> dict[str, object]:
    commands = value.get("commands", [])
    assert isinstance(commands, list)
    return {
        "type": value.get("type"),
        "schema_version": value.get("schema_version"),
        "stage": value.get("stage"),
        "passed": value.get("passed"),
        "scope": value.get("scope"),
        "recorded_by": value.get("recorded_by"),
        "commands": [
            {
                "command": _portable_command(command.get("command")),
                "exit_code": command.get("exit_code"),
                "has_output_digest": bool(command.get("output_digest")),
                "has_authorization_id": bool(command.get("authorization_id")),
            }
            for command in commands
            if isinstance(command, dict)
        ],
        "authorization_count": value.get("authorization_count"),
    }


def _unit_contract(unit: Path) -> dict[str, object]:
    artifacts: set[str] = set()
    markdown: dict[str, str] = {}
    for path in sorted(unit.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(unit).as_posix()
        if relative.startswith("evidence/records/"):
            passed = _json(path).get("passed")
            relative = f"evidence/records/{str(passed).lower()}.json"
        artifacts.add(relative)
        if path.suffix == ".md":
            markdown[relative] = path.read_text(encoding="utf-8")

    unit_value = _json(unit / "unit.json")
    context = _json(unit / "context-receipt.json")
    envelope = _json(unit / "execution-envelope.json")
    authorizations = _json(unit / "execution-authorizations.json")
    decisions = _json(unit / "decisions.json")
    checkpoint = _json(unit / "checkpoint.json")
    criteria = _json(unit / "evaluations/criteria.json")

    extension_assets = context.get("extension_assets", [])
    assert isinstance(extension_assets, list)
    stable_extensions = []
    for asset in extension_assets:
        assert isinstance(asset, dict)
        stable_extensions.append(
            {key: value for key, value in asset.items() if key != "source_path"}
        )

    grants = authorizations.get("grants", [])
    assert isinstance(grants, list)
    decision_entries = decisions.get("decisions", [])
    assert isinstance(decision_entries, list)
    evidence_records = [
        _evidence_contract(_json(path))
        for path in sorted((unit / "evidence/records").glob("*.json"))
    ]
    evidence_records.sort(key=lambda item: bool(item["passed"]))

    return {
        "artifacts": sorted(artifacts),
        "markdown": markdown,
        "unit": {
            key: unit_value.get(key)
            for key in (
                "title",
                "project_id",
                "phase",
                "status",
                "owner",
                "scope",
                "work_scope",
                "intent_source",
                "document_language",
                "goal",
                "expected_outcome",
                "constraints",
                "acceptance_criteria",
                "foundation_version",
                "foundation_digest",
            )
        },
        "context": {
            key: context.get(key)
            for key in (
                "project_id",
                "project_version",
                "project_schema_version",
                "document_language",
                "foundation_id",
                "foundation_version",
                "foundation_digest",
                "profiles",
                "extensions",
                "route",
                "maximum_agent_level",
                "rule_ids",
                "policy_ids",
            )
        }
        | {"extension_assets": stable_extensions},
        "envelope": {
            key: envelope.get(key)
            for key in (
                "type",
                "schema_version",
                "status",
                "scope",
                "stages",
                "allowed_actions",
                "forbidden_actions",
                "max_iterations",
                "proposed_by",
            )
        },
        "authorizations": [
            {
                key: grant.get(key)
                for key in ("action", "target", "stage", "iteration")
            }
            for grant in grants
            if isinstance(grant, dict)
        ],
        "decisions": [
            {
                key: decision.get(key)
                for key in (
                    "gate",
                    "outcome",
                    "summary",
                    "scope",
                    "decision_packet_version",
                    "rationale",
                    "alternatives",
                    "tradeoffs",
                    "risks",
                    "references",
                    "decided_by",
                )
            }
            | {
                "approval_subject_type": (
                    decision.get("approval_subject", {}).get("type")
                    if isinstance(decision.get("approval_subject"), dict)
                    else None
                )
            }
            for decision in decision_entries
            if isinstance(decision, dict)
        ],
        "checkpoint": {
            key: checkpoint.get(key)
            for key in ("completed", "pending", "blocked_by", "next_action")
        },
        "criteria": {
            key: criteria.get(key) for key in ("visibility", "criteria")
        },
        "evidence_records": evidence_records,
        "verification": _evidence_contract(
            _json(unit / "evidence/verification.json")
        ),
    }


def _run_isekai(project: Path, *arguments: str) -> dict[str, object]:
    launcher = project / ".isekai/bin/isekai.py"
    completed = subprocess.run(
        [sys.executable, str(launcher), *arguments],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"ISEKAI command failed: {' '.join(arguments)}\n"
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )
    value = json.loads(completed.stdout)
    assert isinstance(value, dict)
    return value


def _record_decision(
    project: Path,
    unit: Path,
    gate: str,
    summary: str,
    references: list[str],
) -> dict[str, object]:
    arguments = [
        "plugin",
        "decision",
        "--unit",
        str(unit),
        "--gate",
        gate,
        "--outcome",
        "approved",
        "--summary",
        summary,
        "--rationale",
        "승인된 Level-1 계획과 현재 Evidence가 이 Gate의 조건을 충족한다.",
        "--alternatives-json",
        json.dumps(
            [
                {
                    "option": "Gate 결정을 연기한다.",
                    "reason": "범위가 제한된 Reference Unit이 준비되어 기각했다.",
                }
            ]
        ),
        "--tradeoff",
        "넓은 범위보다 작고 결정적인 기능을 우선한다.",
        "--risk",
        "이 E2E는 실제 원격 배포를 수행하지 않는다.",
        "--decided-by",
        "reference-product-owner",
    ]
    for reference in references:
        arguments.extend(["--reference", reference])
    return _run_isekai(project, *arguments)


def _run_product_tests(project: Path) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(project / "src")
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=project,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _record_product_evidence(
    project: Path,
    unit: Path,
    authorization: dict[str, object],
    completed: subprocess.CompletedProcess[str],
    *,
    passed: bool,
) -> dict[str, object]:
    output = completed.stdout + completed.stderr
    arguments = [
        "plugin",
        "evidence",
        "--unit",
        str(unit),
    ]
    if passed:
        arguments.append("--passed")
    arguments.extend(
        [
            "--commands-json",
            json.dumps(
                [
                    {
                        "command": (
                            f"{sys.executable} -m unittest discover -s tests -v"
                        ),
                        "exit_code": completed.returncode,
                        "output_digest": hashlib.sha256(output.encode()).hexdigest(),
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                        "authorization_id": authorization["result"][  # type: ignore[index]
                            "authorization_id"
                        ],
                    }
                ]
            ),
            "--scope",
            "Reference Product 기능 제안 우선순위 결정",
            "--recorded-by",
            "reference-product-validator",
        ]
    )
    return _run_isekai(project, *arguments)


def _write_inception_artifacts(unit: Path) -> None:
    artifacts = {
        "requirements.md": """# 요구사항

- 검증된 FeatureProposal을 high, medium, low 영향도 순으로 정렬한다.
- 영향도가 같으면 제안 ID 순으로 정렬한다.
- 입력 레코드를 변경하지 않고 지원하지 않는 영향도를 거부한다.

## 비목표

- 영속성, 사용자 인터페이스, 원격 배포는 범위에 포함하지 않는다.
""",
        "architecture.md": """# 아키텍처

`reference_product.proposals`에 순수 도메인 함수 하나를 추가한다. 기존
정규화 경계를 재사용하고 복사된 dictionary를 반환하며, 저장소와 전송 계층에
의존하지 않도록 기능을 유지한다.
""",
        "plan.md": """# Level-1 계획

| Stage | Disposition | Depth | 사유 |
|---|---|---|---|
| Inception | apply | standard | 결정적인 동작을 정의한다. |
| Construction | apply | standard | 도메인 함수 하나를 구현한다. |
| Validation | apply | standard | 표준 라이브러리 단위 테스트를 실행한다. |
| Release | skip | light | 게시 또는 배포가 범위에 없다. |
| Operations | skip | light | 운영 환경이 범위에 없다. |
| Learn | apply | light | 결과와 E2E Evidence를 보존한다. |
""",
        "acceptance.md": """# 인수 조건

- [ ] high, medium, low 제안이 올바른 순서로 정렬된다.
- [ ] 영향도가 같은 제안은 ID 순으로 정렬된다.
- [ ] 잘못된 영향도를 거부한다.
- [ ] 입력 레코드를 변경하지 않는다.
""",
        "release.md": """# 릴리스

Disposition: `skip`. 이 Unit은 로컬 Reference Product만 변경하며 게시 또는
배포를 수행하지 않는다.
""",
        "operations.md": """# 운영

Disposition: `skip`. 승인된 Level-1 계획에는 원격 Runtime이나 운영 환경
작업이 없다.
""",
        "implementation-guide.md": """# 구현 가이드

mapping 레코드 iterable을 `prioritize_proposals`에 전달한다. 함수는 모든
제안을 검증하고 복사한 다음 새로 정렬된 목록을 반환한다.
""",
    }
    for relative, content in artifacts.items():
        (unit / relative).write_text(content, encoding="utf-8")


def test_reference_product_feature_runs_through_installed_codex_plugin(
    tmp_path: Path,
) -> None:
    project = tmp_path / "reference-product"
    shutil.copytree(STARTER, project)
    shutil.copytree(EXTENSION, project / "extension")

    commit = "e" * 40
    installed = install_from_checkout(
        ROOT,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.0",
        commit=commit,
        runtimes=("codex",),
    )
    assert installed["installed"] is True
    assert installed["new_conversation_required"] is True

    codex_plugin = (
        project
        / ".isekai/marketplaces/codex/plugins/isekai-agent-plugin"
    )
    manifest = json.loads(
        (codex_plugin / ".codex-plugin/plugin.json").read_text(encoding="utf-8")
    )
    marketplace = json.loads(
        (project / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    skill = (codex_plugin / "skills/isekai/SKILL.md").read_text(encoding="utf-8")
    workspace_skill = (
        project / ".agents/skills/isekai/SKILL.md"
    ).read_text(encoding="utf-8")
    assert manifest["version"] == "0.1.0+codex.eeeeeeeeeeee"
    assert marketplace["plugins"][0]["policy"]["installation"] == (
        "INSTALLED_BY_DEFAULT"
    )
    assert workspace_skill == skill
    assert "repo-local `$isekai ACTION`" in workspace_skill
    assert "The host agent drives the lifecycle" in skill

    handshake = _run_isekai(
        project,
        "plugin",
        "handshake",
        "--runtime",
        "codex",
        "--adapter-version",
        "0.1.0",
        "--protocol-version",
        "1.0.0",
        "--project",
        str(project),
    )
    assert handshake["result"]["locked"] is True  # type: ignore[index]

    _run_isekai(
        project,
        "plugin",
        "init",
        "--path",
        str(project),
        "--id",
        "reference-product",
        "--profile",
        "software-delivery-profile",
        "--document-language",
        "ko",
        "--maximum-agent-level",
        "L1",
    )
    project_manifest = project / "project.json"
    project_value = json.loads(project_manifest.read_text(encoding="utf-8"))
    project_value["extensions"] = [
        {
            "id": "reference-product-extension",
            "path": "extension/reference-product.json",
        }
    ]
    project_manifest.write_text(
        json.dumps(project_value, indent=2) + "\n",
        encoding="utf-8",
    )

    activated = _run_isekai(project, "plugin", "on", "--project", str(project))
    assert activated["result"]["adapter_mode"]["state"] == "on"  # type: ignore[index]

    intake = _run_isekai(
        project,
        "plugin",
        "intake",
        "--source",
        "direct-request",
        "--goal",
        "입력 레코드를 변경하지 않고 영향도에 따라 기능 제안의 우선순위를 결정한다.",
        "--expected-outcome",
        "검증된 기능 제안이 결정적인 우선순위 순서로 반환된다.",
        "--scope",
        "src/reference_product/proposals.py",
        "--scope",
        "tests/test_proposals.py",
        "--constraint",
        "원격 배포와 서드파티 의존성을 추가하지 않는다.",
        "--acceptance-criterion",
        "높은 영향도가 중간 및 낮은 영향도보다 먼저 온다.",
        "--acceptance-criterion",
        "영향도가 같으면 제안 ID 순으로 정렬한다.",
        "--acceptance-criterion",
        "잘못된 영향도를 거부하고 입력을 변경하지 않는다.",
    )
    result = intake["result"]
    assert result["route"]["route"] == "unit"  # type: ignore[index]
    assert result["workflow"]["driver"] == "adaptive-unit"  # type: ignore[index]
    assert result["workflow"]["plan"]["suggested_depth"] == "standard"  # type: ignore[index]

    created = _run_isekai(
        project,
        "plugin",
        "unit-init",
        "--project",
        str(project),
        "--title",
        "기능 제안 우선순위 결정",
        "--owner",
        "reference-product-owner",
        "--intent-json",
        json.dumps(result["intent"]),  # type: ignore[index]
    )
    unit = Path(created["result"]["created"])  # type: ignore[index]
    _write_inception_artifacts(unit)

    stages = [
        {
            "name": "inception",
            "disposition": "apply",
            "depth": "standard",
            "reason": "동작과 경계에 대한 명시적인 합의가 필요하다.",
            "allowed_actions": ["read"],
        },
        {
            "name": "construction",
            "disposition": "apply",
            "depth": "standard",
            "reason": "소스 파일 하나와 해당 테스트를 로컬에서 변경해야 한다.",
            "allowed_actions": ["read", "edit", "test"],
        },
        {
            "name": "validation",
            "disposition": "apply",
            "depth": "standard",
            "reason": "인수 동작을 자동화된 테스트로 검증해야 한다.",
            "allowed_actions": ["test"],
        },
        {
            "name": "release",
            "disposition": "skip",
            "depth": "light",
            "reason": "게시 또는 배포가 작업 범위에 없다.",
            "allowed_actions": [],
        },
        {
            "name": "operations",
            "disposition": "skip",
            "depth": "light",
            "reason": "운영 환경이 작업 범위에 없다.",
            "allowed_actions": [],
        },
        {
            "name": "learn",
            "disposition": "apply",
            "depth": "light",
            "reason": "E2E 결과를 감사 가능한 상태로 보존해야 한다.",
            "allowed_actions": [],
        },
    ]
    _run_isekai(
        project,
        "plugin",
        "envelope-propose",
        "--unit",
        str(unit),
        "--scope",
        "src/**",
        "--scope",
        "tests/**",
        "--stages-json",
        json.dumps(stages),
        "--allowed-action",
        "read",
        "--allowed-action",
        "edit",
        "--allowed-action",
        "test",
        "--forbidden-action",
        "remote",
        "--forbidden-action",
        "deploy",
        "--forbidden-action",
        "credential-access",
        "--max-iterations",
        "4",
        "--proposed-by",
        "codex-reference-agent",
    )
    _run_isekai(
        project,
        "plugin",
        "checkpoint",
        "--unit",
        str(unit),
        "--pending",
        "기능 제안 우선순위 결정을 구현하고 검증한다.",
        "--next-action",
        "Inception을 승인하고 Construction으로 전환한다.",
    )
    _run_isekai(project, "plugin", "transition", "--unit", str(unit), "--to", "inception")
    _run_isekai(
        project,
        "plugin",
        "transition",
        "--unit",
        str(unit),
        "--to",
        "awaiting-inception-decision",
    )
    _record_decision(
        project,
        unit,
        "inception",
        "범위가 제한된 Level-1 계획과 Execution Envelope를 승인한다.",
        ["plan.md", "requirements.md", "execution-envelope.json"],
    )
    _run_isekai(
        project,
        "plugin",
        "transition",
        "--unit",
        str(unit),
        "--to",
        "construction",
    )

    test_authorization = _run_isekai(
        project,
        "plugin",
        "authorize",
        "--unit",
        str(unit),
        "--action",
        "edit",
        "--target",
        "tests/test_proposals.py",
    )
    assert test_authorization["result"]["allowed"] is True  # type: ignore[index]
    (project / "tests/test_proposals.py").write_text(
        (COMPLETED / "tests/test_proposals.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    red_authorization = _run_isekai(
        project,
        "plugin",
        "authorize",
        "--unit",
        str(unit),
        "--action",
        "test",
        "--target",
        "tests/test_proposals.py",
    )
    assert red_authorization["result"]["allowed"] is True  # type: ignore[index]
    red_test = _run_product_tests(project)
    assert red_test.returncode != 0
    assert "prioritize_proposals" in red_test.stdout + red_test.stderr
    failed_evidence = _record_product_evidence(
        project,
        unit,
        red_authorization,
        red_test,
        passed=False,
    )
    assert failed_evidence["result"]["evidence"]["passed"] is False  # type: ignore[index]

    source_authorization = _run_isekai(
        project,
        "plugin",
        "authorize",
        "--unit",
        str(unit),
        "--action",
        "edit",
        "--target",
        "src/reference_product/proposals.py",
    )
    assert source_authorization["result"]["allowed"] is True  # type: ignore[index]
    (project / "src/reference_product/proposals.py").write_text(
        (COMPLETED / "src/reference_product/proposals.py").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    green_authorization = _run_isekai(
        project,
        "plugin",
        "authorize",
        "--unit",
        str(unit),
        "--action",
        "test",
        "--target",
        "tests/test_proposals.py",
    )
    assert green_authorization["result"]["allowed"] is True  # type: ignore[index]
    product_test = _run_product_tests(project)
    assert product_test.returncode == 0, product_test.stdout + product_test.stderr
    output = product_test.stdout + product_test.stderr
    evidence = _record_product_evidence(
        project,
        unit,
        green_authorization,
        product_test,
        passed=True,
    )
    assert evidence["result"]["evidence"]["passed"] is True  # type: ignore[index]

    (unit / "acceptance.md").write_text(
        """# 인수 조건

- [x] high, medium, low 제안이 올바른 순서로 정렬된다.
- [x] 영향도가 같은 제안은 ID 순으로 정렬된다.
- [x] 잘못된 영향도를 거부한다.
- [x] 입력 레코드를 변경하지 않는다.
""",
        encoding="utf-8",
    )
    _record_decision(
        project,
        unit,
        "architecture",
        "순수 도메인 함수 구현을 승인한다.",
        ["architecture.md", "implementation-guide.md"],
    )
    _run_isekai(
        project,
        "plugin",
        "transition",
        "--unit",
        str(unit),
        "--to",
        "validation",
    )
    _run_isekai(
        project,
        "plugin",
        "transition",
        "--unit",
        str(unit),
        "--to",
        "awaiting-release-decision",
    )
    _record_decision(
        project,
        unit,
        "release",
        "현재 Evidence를 승인하고 Release disposition을 skip으로 유지한다.",
        ["release.md", "evidence/verification.json", "plan.md"],
    )
    _run_isekai(
        project,
        "plugin",
        "transition",
        "--unit",
        str(unit),
        "--to",
        "releasing",
    )
    _run_isekai(
        project,
        "plugin",
        "transition",
        "--unit",
        str(unit),
        "--to",
        "operating",
    )
    _record_decision(
        project,
        unit,
        "operation",
        "이 로컬 Unit에서 Operations를 의도적으로 건너뛰었음을 확인한다.",
        ["operations.md", "plan.md"],
    )
    _run_isekai(
        project,
        "plugin",
        "checkpoint",
        "--unit",
        str(unit),
        "--completed",
        "기능 제안 우선순위 결정을 구현하고 검증했다.",
        "--next-action",
        "Unit을 완료한다.",
    )
    _run_isekai(
        project,
        "plugin",
        "transition",
        "--unit",
        str(unit),
        "--to",
        "learned",
    )

    verified = _run_isekai(project, "plugin", "verify", "--unit", str(unit))
    status = _run_isekai(
        project,
        "plugin",
        "status",
        "--project",
        str(project),
        "--unit",
        str(unit),
    )
    doctor = _run_isekai(project, "doctor", "--path", str(project))
    envelope = json.loads(
        (unit / "execution-envelope.json").read_text(encoding="utf-8")
    )
    dispositions = {
        stage["name"]: stage.get("disposition") for stage in envelope["stages"]
    }
    evidence_records = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((unit / "evidence/records").glob("*.json"))
    ]

    assert verified["result"]["valid"] is True  # type: ignore[index]
    assert status["result"]["unit"]["status"] == "learned"  # type: ignore[index]
    assert status["result"]["unit"]["pending"] == []  # type: ignore[index]
    assert doctor["ready"] is True
    assert dispositions["release"] == "skip"
    assert dispositions["operations"] == "skip"
    assert sorted(record["passed"] for record in evidence_records) == [False, True]
    assert "Ran 4 tests" in output
    _assert_korean_human_artifacts(unit)
    assert _unit_contract(unit) == _unit_contract(_golden_unit())


def test_completed_reference_product_contains_valid_golden_unit(
    monkeypatch,
) -> None:
    monkeypatch.chdir(COMPLETED)

    result = verify_unit(_golden_unit())

    _assert_korean_human_artifacts(_golden_unit())
    assert result["valid"] is True
    assert result["status"] == "learned"
    assert result["decision_count"] == 4
    assert result["evidence"]["passed"] is True
