from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

import isekai.workflow.unit.managed_execution as managed_execution_module
from isekai.runtime_contract import dispatch
from isekai.workflow.unit.managed_execution import (
    execute_managed_edit,
    execute_managed_test,
    write_unit_artifacts,
)
from isekai.workflow import record_unit_amendment
from isekai.workflow.errors import IntegrityError, LifecycleError

from test_execution_envelope import approve_inception, make_enveloped_unit


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def test_managed_edit_authorizes_executes_and_records_one_core_batch(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"
    (project / "src").mkdir()
    existing = project / "src/existing.py"
    existing.write_text("OLD = True\n", encoding="utf-8")

    result = execute_managed_edit(
        unit,
        changes=[
            {
                "target": "src/existing.py",
                "expected_digest": digest(b"OLD = True\n"),
                "content": "NEW = True\n",
            },
            {
                "target": "src/created.py",
                "expected_digest": "absent",
                "content": "CREATED = True\n",
            },
        ],
    )

    assert result["allowed"] is True
    assert result["host_write_required"] is False
    assert existing.read_text(encoding="utf-8") == "NEW = True\n"
    assert (project / "src/created.py").read_text(encoding="utf-8") == (
        "CREATED = True\n"
    )
    ledger = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )
    grant = ledger["grants"][-1]
    assert grant["targets"] == ["src/existing.py", "src/created.py"]
    assert grant["execution"]["type"] == "core-managed-edit"
    assert grant["execution"]["status"] == "completed"
    assert [item["target"] for item in grant["execution"]["files"]] == grant[
        "targets"
    ]


def test_managed_edit_precondition_failure_restores_files_and_grant(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"
    (project / "src").mkdir()
    target = project / "src/main.py"
    target.write_text("CURRENT = True\n", encoding="utf-8")
    ledger_before = (unit / "execution-authorizations.json").read_bytes()

    with pytest.raises(IntegrityError, match="precondition changed"):
        execute_managed_edit(
            unit,
            changes=[
                {
                    "target": "src/main.py",
                    "expected_digest": digest(b"STALE = True\n"),
                    "content": "REPLACED = True\n",
                }
            ],
        )

    assert target.read_text(encoding="utf-8") == "CURRENT = True\n"
    assert (unit / "execution-authorizations.json").read_bytes() == ledger_before


def test_managed_edit_write_failure_restores_earlier_batch_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"
    (project / "src").mkdir()
    first = project / "src/first.py"
    second = project / "src/second.py"
    first.write_text("FIRST = 'old'\n", encoding="utf-8")
    second.write_text("SECOND = 'old'\n", encoding="utf-8")
    ledger_before = (unit / "execution-authorizations.json").read_bytes()
    real_write = managed_execution_module.write_bytes_atomic
    failed = False

    def fail_second_once(path: Path, content: bytes, **kwargs: object) -> None:
        nonlocal failed
        if path == second and not failed:
            failed = True
            raise OSError("simulated second write failure")
        real_write(path, content, **kwargs)

    monkeypatch.setattr(
        managed_execution_module, "write_bytes_atomic", fail_second_once
    )

    with pytest.raises(OSError, match="simulated second write failure"):
        execute_managed_edit(
            unit,
            changes=[
                {
                    "target": "src/first.py",
                    "expected_digest": digest(b"FIRST = 'old'\n"),
                    "content": "FIRST = 'new'\n",
                },
                {
                    "target": "src/second.py",
                    "expected_digest": digest(b"SECOND = 'old'\n"),
                    "content": "SECOND = 'new'\n",
                },
            ],
        )

    assert first.read_text(encoding="utf-8") == "FIRST = 'old'\n"
    assert second.read_text(encoding="utf-8") == "SECOND = 'old'\n"
    assert (unit / "execution-authorizations.json").read_bytes() == ledger_before


def test_runtime_refuses_free_standing_host_edit_authorization(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    result = dispatch(
        "authorize",
        {
            "unit": str(unit),
            "requested_action": "edit",
            "target": "src/main.py",
        },
    )["result"]

    assert result == {
        "allowed": False,
        "reason_code": "core-managed-execution-required",
        "reason": (
            "edit cannot be separated from execution; use the Core managed "
            "execution action"
        ),
    }


def test_runtime_refuses_free_standing_host_test_authorization(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    result = dispatch(
        "authorize",
        {
            "unit": str(unit),
            "requested_action": "test",
            "target": "tests/smoke.py",
        },
    )["result"]

    assert result["allowed"] is False
    assert result["reason_code"] == "core-managed-execution-required"


def test_managed_test_executes_and_binds_result_to_grant(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    result = execute_managed_test(
        unit,
        target="tests/smoke.py",
        command=[sys.executable, "-c", "print('managed-test-ok')"],
    )

    assert result["allowed"] is True
    assert result["passed"] is True
    assert result["stdout"] == "managed-test-ok\n"
    assert result["host_execution_required"] is False
    assert result["execution"]["workspace"] == "isolated-copy"
    ledger = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )
    assert ledger["grants"][-1]["execution"] == result["execution"]


def test_managed_test_writes_only_to_disposable_workspace(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"

    result = execute_managed_test(
        unit,
        target="tests/smoke.py",
        command=[
            sys.executable,
            "-c",
            "from pathlib import Path; Path('generated.txt').write_text('temp')",
        ],
    )

    assert result["passed"] is True
    assert not (project / "generated.txt").exists()


def test_approved_unit_artifact_requires_pending_amendment_before_write(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    plan = unit / "plan.md"
    before = plan.read_bytes()
    changed = before.decode("utf-8") + "\n- 추가 구현 결정을 반영한다.\n"

    with pytest.raises(LifecycleError, match="record an amendment"):
        write_unit_artifacts(
            unit,
            artifacts=[
                {
                    "target": "plan.md",
                    "expected_digest": digest(before),
                    "content": changed,
                }
            ],
        )

    record_unit_amendment(
        unit,
        request="계획에 추가 구현 결정을 반영한다.",
        reason="사용자가 같은 Unit 안에서 범위를 보완했다.",
        affected_artifacts=["plan.md"],
        requested_by="human-reviewer",
    )
    result = write_unit_artifacts(
        unit,
        artifacts=[
            {
                "target": "plan.md",
                "expected_digest": digest(before),
                "content": changed,
            }
        ],
    )

    assert result["written"] is True
    assert result["host_write_required"] is False
    assert plan.read_text(encoding="utf-8") == changed


def test_acceptance_progress_can_only_check_existing_items_forward(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    acceptance = unit / "acceptance.md"
    before = acceptance.read_bytes()
    assert b"[ ]" in before
    checked = before.decode("utf-8").replace("[ ]", "[x]", 1)

    write_unit_artifacts(
        unit,
        artifacts=[
            {
                "target": "acceptance.md",
                "expected_digest": digest(before),
                "content": checked,
            }
        ],
    )

    checked_bytes = checked.encode("utf-8")
    with pytest.raises(LifecycleError, match="record an amendment"):
        write_unit_artifacts(
            unit,
            artifacts=[
                {
                    "target": "acceptance.md",
                    "expected_digest": digest(checked_bytes),
                    "content": checked + "\n- [x] 새 인수 조건\n",
                }
            ],
        )
