from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from isekai.distribution import doctor_install, write_distribution_manifest


ROOT = Path(__file__).resolve().parents[1]


def _tagged_release(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    shutil.copytree(
        ROOT,
        release,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache"
        ),
    )
    write_distribution_manifest(release)
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(["git", "add", "."], cwd=release, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ISEKAI Test",
            "-c",
            "user.email=isekai@example.invalid",
            "commit",
            "-qm",
            "release",
        ],
        cwd=release,
        check=True,
    )
    subprocess.run(["git", "tag", "v0.1.0"], cwd=release, check=True)
    return release


def test_posix_bootstrap_installs_and_initializes_from_local_git_tag(
    tmp_path: Path,
) -> None:
    release = _tagged_release(tmp_path)
    project = tmp_path / "product"
    project.mkdir()

    completed = subprocess.run(
        [
            "bash",
            str(release / "scripts/install.sh"),
            "--source",
            str(release),
            "--ref",
            "v0.1.0",
            "--path",
            str(project),
            "--runtime",
            "kiro",
            "--init",
            "--python",
            sys.executable,
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    lock = json.loads((project / "isekai.lock.json").read_text(encoding="utf-8"))
    assert lock["source"]["ref"] == "v0.1.0"
    assert set(lock["adapters"]) == {"kiro"}
    assert (project / "project.json").is_file()
    assert (project / ".kiro/skills/isekai/SKILL.md").is_file()
    assert doctor_install(project)["ready"] is True


def test_posix_bootstrap_help_does_not_require_dependencies() -> None:
    completed = subprocess.run(
        ["bash", str(ROOT / "scripts/install.sh"), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "--source GIT_URL" in completed.stdout


def test_posix_bootstrap_preserves_git_failure_exit_code(tmp_path: Path) -> None:
    project = tmp_path / "product"
    project.mkdir()

    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/install.sh"),
            "--source",
            str(tmp_path / "missing-release"),
            "--ref",
            "v0.1.0",
            "--path",
            str(project),
            "--python",
            sys.executable,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode != 0
    assert not (project / "isekai.lock.json").exists()
