from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path

import pytest

from isekai.cli import main
from isekai.support.locking import LockUnavailable


def test_direct_intake_alias_matches_plugin_namespace(capsys) -> None:
    direct_exit = main(
        [
            "intake",
            "--source",
            "direct-request",
            "--goal",
            "Entity가 뭐야?",
        ]
    )
    direct_output = json.loads(capsys.readouterr().out)

    plugin_exit = main(
        [
            "plugin",
            "intake",
            "--source",
            "direct-request",
            "--goal",
            "Entity가 뭐야?",
        ]
    )
    plugin_output = json.loads(capsys.readouterr().out)

    assert direct_exit == 0
    assert plugin_exit == 0
    assert direct_output["action"] == "intake"
    assert plugin_output["action"] == "intake"
    assert direct_output["result"]["route"] == plugin_output["result"]["route"]


def test_direct_status_alias_uses_project_context(tmp_path: Path, capsys) -> None:
    from test_core_workflow import make_project

    project = make_project(tmp_path)
    exit_code = main(["status", "--project", str(project)])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["action"] == "status"
    assert output["result"]["project"]["id"] == "test-project"


def test_direct_unit_migrate_alias_uses_path_only_contract(
    tmp_path: Path,
    capsys,
) -> None:
    from isekai.workflow import initialize_unit
    from test_core_workflow import make_project

    project = make_project(tmp_path)
    unit = initialize_unit(project, "Portable CLI Unit", project.parent / "units")

    exit_code = main(
        [
            "unit-migrate",
            "--project",
            str(project),
            "--unit",
            str(unit),
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["action"] == "unit-migrate"
    assert output["result"]["migrated"] is False


def test_direct_on_and_off_aliases_expose_adapter_mode(
    tmp_path: Path, capsys
) -> None:
    from test_core_workflow import make_project

    project = make_project(tmp_path)
    on_exit = main(["on", "--project", str(project)])
    on_output = json.loads(capsys.readouterr().out)

    off_exit = main(["off"])
    off_output = json.loads(capsys.readouterr().out)

    assert on_exit == 0
    assert on_output["action"] == "on"
    assert on_output["result"]["activation"] == "project"
    assert on_output["result"]["unit"] is None
    assert on_output["result"]["active_unit"] is None
    assert on_output["result"]["adapter_mode"]["state"] == "on"
    assert on_output["result"]["adapter_mode"]["next_session_state"] == "off"
    assert off_exit == 0
    assert off_output["action"] == "off"
    assert off_output["result"]["adapter_mode"]["state"] == "off"
    assert off_output["result"]["artifacts_changed"] is False
    assert off_output["result"]["checkpoint_changed"] is False


def test_direct_init_alias_creates_project_manifest(tmp_path: Path, capsys) -> None:
    import shutil

    from test_core_workflow import ROOT

    project_root = tmp_path / "cli-project"
    project_root.mkdir()
    shutil.copytree(ROOT / "foundation", project_root / "foundation")

    exit_code = main(
        [
            "init",
            "--path",
            str(project_root),
            "--id",
            "cli-project",
            "--profile",
            "software-delivery-profile",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["action"] == "init"
    assert Path(output["result"]["created"]) == project_root / "project.json"
    assert (project_root / "units").is_dir()


def test_direct_on_rejects_unit_option(tmp_path: Path) -> None:
    from test_core_workflow import make_project

    project = make_project(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        main(["on", "--project", str(project), "--unit", "some-unit"])
    assert exc_info.value.code == 2


def test_json_array_options_reject_null_without_traceback(capsys) -> None:
    exit_code = main(
        [
            "envelope-propose",
            "--unit",
            "/tmp/missing-unit",
            "--scope",
            "src/**",
            "--stages-json",
            "null",
            "--allowed-action",
            "read",
            "--max-iterations",
            "1",
            "--proposed-by",
            "test-agent",
        ]
    )
    captured = capsys.readouterr()
    error = json.loads(captured.err)

    assert exit_code == 2
    assert captured.out == ""
    assert error == {"error": "plugin request field stages must be a list"}


def test_lock_contention_is_reported_as_json_without_traceback(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import isekai.workflow.session as session_module

    @contextmanager
    def unavailable_lock(_path: Path):
        raise LockUnavailable("Unit is being modified; retry after it completes")
        yield  # pragma: no cover - required by contextmanager syntax

    monkeypatch.setattr(session_module, "unit_lock", unavailable_lock)

    exit_code = main(
        [
            "checkpoint",
            "--unit",
            str(tmp_path),
            "--next-action",
            "retry",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "error": "Unit is being modified; retry after it completes"
    }
    assert "Traceback" not in captured.err
