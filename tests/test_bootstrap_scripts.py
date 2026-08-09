from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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
            "--maximum-agent-level",
            "L1",
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
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert manifest["maximum_agent_level"] == "L1"
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
    assert "--maximum-agent-level L" in completed.stdout


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


def test_posix_bootstrap_rejects_transport_helpers_before_clone(
    tmp_path: Path,
) -> None:
    project = tmp_path / "product"
    project.mkdir()

    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/install.sh"),
            "--source",
            "ext::sh -c true",
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
    assert "transport helper" in completed.stderr
    assert not (project / "isekai.lock.json").exists()


def test_posix_bootstrap_rejects_noncanonical_file_authority_before_clone(
    tmp_path: Path,
) -> None:
    project = tmp_path / "product"
    project.mkdir()
    source = (tmp_path / "release").resolve().as_uri().replace(
        "file://", "file://example.invalid", 1
    )

    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/install.sh"),
            "--source",
            source,
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
    assert "file URL" in completed.stderr
    assert not (project / "isekai.lock.json").exists()


def test_posix_bootstrap_rejects_embedded_credentials_before_clone(
    tmp_path: Path,
) -> None:
    project = tmp_path / "product"
    project.mkdir()

    completed = subprocess.run(
        [
            "bash",
            str(ROOT / "scripts/install.sh"),
            "--source",
            "https://user:secret@example.invalid/isekai.git",
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
    assert "embedded credentials" in completed.stderr
    assert not (project / "isekai.lock.json").exists()


def test_posix_bootstrap_rejects_branch_before_release_code_execution(
    tmp_path: Path,
) -> None:
    release = tmp_path / "branch-release"
    shutil.copytree(
        ROOT,
        release,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache"
        ),
    )
    marker = tmp_path / "release-code-executed"
    (release / "src/isekai/__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "__version__ = '0.1.0'\n",
        encoding="utf-8",
    )
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
            "branch release",
        ],
        cwd=release,
        check=True,
    )
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=release,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    project = tmp_path / "product"
    project.mkdir()

    completed = subprocess.run(
        [
            "bash",
            str(release / "scripts/install.sh"),
            "--source",
            str(release),
            "--ref",
            branch,
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
    assert "immutable tag or full commit" in completed.stderr
    assert not marker.exists()
    assert not (project / "isekai.lock.json").exists()


def test_posix_bootstrap_rejects_git_revision_expressions(tmp_path: Path) -> None:
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
            "v0.1.0^0",
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
    assert "revision expressions" in completed.stderr
    assert not (project / "isekai.lock.json").exists()


def test_powershell_bootstrap_validates_immutable_ref_before_checkout() -> None:
    content = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    validation = content.index('refs/tags/${Ref}^{commit}')
    checkout = content.index("& git -C $checkout checkout --quiet --detach")

    assert validation < checkout
    assert "[0-9a-fA-F]{40}" in content
    assert "[0-9a-fA-F]{64}" in content
    assert "check-ref-format" in content
    assert "branches and abbreviated commits are not allowed" in content


def test_powershell_bootstrap_rejects_transport_helpers_before_clone() -> None:
    content = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    validation = content.index("Git transport helper")
    clone = content.index("& git clone")

    assert validation < clone
    assert "^[A-Za-z][A-Za-z0-9+.-]*::" in content


def test_powershell_bootstrap_rejects_credentials_before_clone() -> None:
    content = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    validation = content.index("Source must not contain embedded credentials")
    clone = content.index("& git clone")

    assert validation < clone
    assert "$sourceUri.UserInfo" in content


def test_powershell_bootstrap_rejects_noncanonical_file_authorities() -> None:
    content = (ROOT / "scripts/install.ps1").read_text(encoding="utf-8")

    validation = content.index("Source file URL must omit its authority")
    clone = content.index("& git clone")

    assert validation < clone
    assert '$sourceUri.Host.Equals("localhost"' in content


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")
def test_powershell_bootstrap_installs_and_initializes_from_local_git_tag(
    tmp_path: Path,
) -> None:
    release = _tagged_release(tmp_path)
    project = tmp_path / "windows-product"
    project.mkdir()

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(release / "scripts/install.ps1"),
            "-Source",
            str(release),
            "-Ref",
            "v0.1.0",
            "-ProjectPath",
            str(project),
            "-Runtime",
            "kiro",
            "-Init",
            "-MaximumAgentLevel",
            "L1",
            "-Profile",
            "software-delivery-profile",
            "-Python",
            sys.executable,
        ],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout + completed.stderr
    launcher = project / ".isekai/bin/isekai.py"
    doctor = subprocess.run(
        [sys.executable, str(launcher), "doctor", "--path", str(project)],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert json.loads(doctor.stdout)["ready"] is True
    assert (project / ".kiro/skills/isekai/SKILL.md").is_file()
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))
    assert manifest["maximum_agent_level"] == "L1"
