from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

import isekai.catalog.ai_dlc.unit.managed_execution as managed_execution_module
from isekai.runtime_contract import dispatch
from isekai.catalog.ai_dlc.unit.managed_execution import (
    MAX_PROOF_CAPTURE_BYTES,
    MAX_PROOF_OUTPUT_BYTES,
    execute_managed_edit,
    execute_proof,
    write_unit_artifacts,
)
from isekai.catalog.ai_dlc.unit.proof_sandbox import SandboxInvocation
from isekai.workflow import authorize_action, record_unit_amendment
from isekai.workflow.errors import IntegrityError, LifecycleError, WorkflowError

from test_execution_envelope import approve_inception, make_enveloped_unit


@pytest.fixture(autouse=True)
def _proof_sandbox_double(monkeypatch: pytest.MonkeyPatch) -> None:
    def build(
        argv: list[str],
        **kwargs: object,
    ) -> SandboxInvocation:
        environment = kwargs.get("environment")
        assert isinstance(environment, dict)
        return SandboxInvocation(
            argv=list(argv),
            provider="test-double",
            environment=environment,
            resource_limits={
                "cpu_seconds": 305,
                "file_size_bytes": 256 * 1024 * 1024,
                "open_files": 256,
                "processes": 512,
                "core_dump_bytes": 0,
            },
        )

    monkeypatch.setattr(
        managed_execution_module,
        "build_sandbox_invocation",
        build,
    )


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
    real_write = managed_execution_module._write_managed_bytes
    failed = False

    def fail_second_once(
        root: Path,
        relative: str,
        content: bytes,
        **kwargs: object,
    ) -> None:
        nonlocal failed
        if root / relative == second and not failed:
            failed = True
            raise OSError("simulated second write failure")
        real_write(root, relative, content, **kwargs)

    monkeypatch.setattr(
        managed_execution_module, "_write_managed_bytes", fail_second_once
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


@pytest.mark.parametrize("action", ["edit", "test"])
def test_stable_workflow_facade_refuses_free_standing_managed_grants(
    tmp_path: Path,
    action: str,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    result = authorize_action(unit, action=action, target="tests/smoke.py")

    assert result["allowed"] is False
    assert result["reason_code"] == "core-managed-execution-required"


def test_proof_executes_and_binds_result_to_grant(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    result = execute_proof(
        unit,
        target="tests/smoke.py",
        command=[sys.executable, "-c", "print('prove-ok')"],
    )

    assert result["allowed"] is True
    assert result["passed"] is True
    assert result["stdout"] == "prove-ok\n"
    assert result["host_execution_required"] is False
    assert result["execution"]["type"] == "core-proof"
    assert result["execution"]["workspace"] == "disposable-copy"
    assert result["execution"]["sandbox_provider"] == "test-double"
    assert result["execution"]["sandbox_policy"] == (
        "provider-deny-default-explicit-allowlist"
    )
    assert result["execution"]["filesystem_isolation"] == (
        "source-and-user-data-read-denied-write-confined"
    )
    assert result["execution"]["network_isolation"] == "denied"
    ledger = json.loads(
        (unit / "execution-authorizations.json").read_text(encoding="utf-8")
    )
    assert ledger["grants"][-1]["execution"] == result["execution"]


def test_proof_writes_only_to_disposable_workspace(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"

    result = execute_proof(
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


def test_proof_ignores_symlinks_inside_excluded_virtualenv(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    virtualenv = tmp_path / "project/.venv/bin"
    virtualenv.mkdir(parents=True)
    (virtualenv / "python").symlink_to(sys.executable)

    result = execute_proof(
        unit,
        target="tests/smoke.py",
        command=[sys.executable, "-c", "print('ok')"],
    )

    assert result["passed"] is True


def test_proof_rejects_a_source_symlink(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"
    (project / "external-link").symlink_to(tmp_path)
    ledger_before = (unit / "execution-authorizations.json").read_bytes()

    with pytest.raises(WorkflowError, match="cannot contain symlinks"):
        execute_proof(
            unit,
            target="tests/smoke.py",
            command=[sys.executable, "-c", "print('must-not-run')"],
        )

    assert (unit / "execution-authorizations.json").read_bytes() == ledger_before


def test_proof_rejects_a_source_hardlink(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"
    original = project / "original.txt"
    original.write_text("shared inode", encoding="utf-8")
    (project / "hardlink.txt").hardlink_to(original)
    ledger_before = (unit / "execution-authorizations.json").read_bytes()

    with pytest.raises(WorkflowError, match="cannot contain hardlinked files"):
        execute_proof(
            unit,
            target="tests/smoke.py",
            command=[sys.executable, "-c", "print('must-not-run')"],
        )

    assert (unit / "execution-authorizations.json").read_bytes() == ledger_before


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO requires POSIX")
def test_proof_rejects_a_source_special_file(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    project = tmp_path / "project"
    os.mkfifo(project / "input.pipe")
    ledger_before = (unit / "execution-authorizations.json").read_bytes()

    with pytest.raises(WorkflowError, match="cannot contain special files"):
        execute_proof(
            unit,
            target="tests/smoke.py",
            command=[sys.executable, "-c", "print('must-not-run')"],
        )

    assert (unit / "execution-authorizations.json").read_bytes() == ledger_before


def test_proof_rejects_a_source_entry_swapped_during_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    source = tmp_path / "project/race.txt"
    source.write_text("safe", encoding="utf-8")
    external = tmp_path / "external-secret.txt"
    external.write_text("must-not-be-copied", encoding="utf-8")
    ledger_before = (unit / "execution-authorizations.json").read_bytes()
    real_open = os.open
    swapped = False

    def swap_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if path == "race.txt" and dir_fd is not None and not swapped:
            swapped = True
            source.unlink()
            source.symlink_to(external)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(managed_execution_module.os, "open", swap_before_open)

    with pytest.raises(WorkflowError, match="changed while copying"):
        execute_proof(
            unit,
            target="tests/race.py",
            command=[sys.executable, "-c", "print('must-not-run')"],
        )

    assert swapped is True
    assert (unit / "execution-authorizations.json").read_bytes() == ledger_before


def test_proof_fails_closed_when_os_sandbox_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    outside = tmp_path / "outside.txt"
    ledger_before = (unit / "execution-authorizations.json").read_bytes()

    def unavailable(*_args: object, **_kwargs: object) -> SandboxInvocation:
        raise WorkflowError("prove OS sandbox is unavailable")

    monkeypatch.setattr(
        managed_execution_module,
        "build_sandbox_invocation",
        unavailable,
    )

    with pytest.raises(WorkflowError, match="OS sandbox is unavailable"):
        execute_proof(
            unit,
            target="tests/smoke.py",
            command=[
                sys.executable,
                "-c",
                "from pathlib import Path; Path(r'%s').write_text('escaped')"
                % outside,
            ],
        )

    assert not outside.exists()
    assert (unit / "execution-authorizations.json").read_bytes() == ledger_before


def test_proof_digests_full_output_without_returning_it_all(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    output_size = MAX_PROOF_OUTPUT_BYTES + 4096
    payload = b"x" * output_size

    result = execute_proof(
        unit,
        target="tests/output.py",
        command=[
            sys.executable,
            "-c",
            f"import os; os.write(1, b'x' * {output_size})",
        ],
    )

    assert result["passed"] is True
    assert len(result["stdout"].encode("utf-8")) == MAX_PROOF_OUTPUT_BYTES
    assert result["stdout_truncated"] is True
    assert result["execution"]["stdout_bytes"] == output_size
    assert result["execution"]["stdout_digest"] == digest(payload)


def test_proof_stops_at_the_aggregate_output_capture_limit(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    result = execute_proof(
        unit,
        target="tests/output-flood.py",
        command=[
            sys.executable,
            "-c",
            "import os\nwhile True: os.write(1, b'x' * 65536)",
        ],
    )

    assert result["passed"] is False
    assert result["execution"]["status"] == "output-limit-exceeded"
    assert result["execution"]["output_limit_exceeded"] is True
    assert result["execution"]["output_capture_limit_bytes"] == (
        MAX_PROOF_CAPTURE_BYTES
    )
    assert result["execution"]["stdout_bytes"] == MAX_PROOF_CAPTURE_BYTES
    assert result["execution"]["stdout_digest"] == digest(
        b"x" * MAX_PROOF_CAPTURE_BYTES
    )
    assert result["stdout_truncated"] is True


@pytest.mark.skipif(os.name != "posix", reason="process groups require POSIX")
def test_proof_cleans_up_same_group_background_children(
    tmp_path: Path,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    program = (
        "import subprocess,sys\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "print(child.pid, flush=True)\n"
    )

    result = execute_proof(
        unit,
        target="tests/background.py",
        command=[sys.executable, "-c", program],
    )

    assert result["passed"] is True
    child_pid = int(result["stdout"].strip())
    deadline = time.monotonic() + 1
    while True:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        if time.monotonic() >= deadline:
            pytest.fail(f"prove background child survived: {child_pid}")
        time.sleep(0.01)


def test_proof_uses_an_allowlisted_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)
    monkeypatch.setenv("ISEKAI_TEST_SECRET", "must-not-cross-boundary")

    result = execute_proof(
        unit,
        target="tests/environment.py",
        command=[
            sys.executable,
            "-c",
            (
                "import os; "
                "print(os.environ.get('ISEKAI_TEST_SECRET', 'not-inherited'))"
            ),
        ],
    )

    assert result["passed"] is True
    assert result["stdout"] == "not-inherited\n"
    assert result["execution"]["environment"] == "core-allowlisted"


def test_proof_timeout_is_receipted(tmp_path: Path) -> None:
    unit = make_enveloped_unit(tmp_path)
    approve_inception(unit)

    result = execute_proof(
        unit,
        target="tests/timeout.py",
        command=[sys.executable, "-c", "import time; time.sleep(10)"],
        timeout_seconds=1,
    )

    assert result["passed"] is False
    assert result["execution"]["status"] == "timed-out"
    assert result["execution"]["exit_code"] is None
    assert result["execution"]["stdout_digest"] == digest(b"")


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
