from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from isekai.cli import main as cli_main
from isekai.distribution import (
    DistributionError,
    build_distribution_manifest,
    doctor_install,
    install_from_git,
    load_distribution_manifest,
    load_install_lock,
    rollback_install,
    tree_digest,
    verify_adapter_handshake,
    verify_distribution,
    write_distribution_manifest,
)
from isekai.distribution.install import (
    _install_from_verified_checkout as install_from_checkout,
)


ROOT = Path(__file__).resolve().parents[1]


def _project_with_foundation(tmp_path: Path) -> Path:
    project = tmp_path / "product"
    project.mkdir(parents=True)
    shutil.copytree(ROOT / "foundation", project / "foundation")
    (project / "project.json").write_text(
        json.dumps(
            {
                "id": "product",
                "kind": "project",
                "schema_version": "1.0.0",
                "version": "0.1.0",
                "foundation_path": "foundation",
                "profiles": ["software-delivery-profile"],
                "extensions": [],
                "maximum_agent_level": "L0",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return project


def _install(project: Path, checkout: Path = ROOT, *, commit: str = "a" * 40):
    return install_from_checkout(
        checkout,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.0",
        commit=commit,
        runtimes=("all",),
    )


def _copy_release(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    shutil.copytree(
        ROOT,
        release,
        ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", ".pytest_cache"),
    )
    return release


def _bump_release(release: Path, version: str) -> None:
    replacements = [
        release / "pyproject.toml",
        release / "src/isekai/__init__.py",
        release / "plugin/isekai/manifest.json",
        release / "plugin/isekai/runtimes/codex/.codex-plugin/plugin.json",
        release / "plugin/isekai/runtimes/claude/.claude-plugin/plugin.json",
        release / "plugin/isekai/runtimes/kiro/skills/isekai/SKILL.md",
        release / "plugin/isekai/runtimes/codex/skills/isekai/SKILL.md",
        release / "plugin/isekai/runtimes/claude/skills/isekai/SKILL.md",
    ]
    replacements.extend(path for path in (release / "foundation").rglob("*.json"))
    for path in replacements:
        content = path.read_text(encoding="utf-8").replace("0.1.0", version)
        path.write_text(content, encoding="utf-8")
    write_distribution_manifest(release)


def test_checked_in_distribution_manifest_matches_release_components() -> None:
    result = verify_distribution(ROOT)

    assert result["valid"] is True
    assert result["release"] == "0.1.0"
    assert build_distribution_manifest(ROOT) == json.loads(
        (ROOT / "distribution/release.json").read_text(encoding="utf-8")
    )


def test_distribution_rejects_a_symlinked_component_root(tmp_path: Path) -> None:
    release = _copy_release(tmp_path)
    scripts = release / "scripts"
    scripts_real = release / "scripts-real"
    scripts.rename(scripts_real)
    scripts.symlink_to(scripts_real, target_is_directory=True)

    with pytest.raises(DistributionError, match="root cannot be a symlink"):
        tree_digest(scripts)
    result = verify_distribution(release)

    assert result["valid"] is False
    assert any("symlink" in issue for issue in result["issues"])


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_distribution_manifest_must_be_a_single_link_regular_file(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    release = _copy_release(tmp_path)
    manifest = release / "distribution/release.json"
    external = tmp_path / "external-release.json"
    manifest.rename(external)
    if alias_kind == "symlink":
        manifest.symlink_to(external)
    else:
        os.link(external, manifest)

    with pytest.raises(DistributionError, match="single-link|symlink"):
        verify_distribution(release)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("adapter-id", "string IDs"),
        ("duplicate-adapter", "must contain"),
        ("compatibility-string", "project schema"),
        ("component-list", "core must be an object"),
        ("digest-list", "SHA-256 digest"),
    ],
)
def test_distribution_manifest_schema_fails_closed(
    tmp_path: Path,
    mutation: str,
    message: str,
) -> None:
    release = _copy_release(tmp_path)
    manifest_path = release / "distribution/release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "adapter-id":
        manifest["adapters"][0]["id"] = ["claude"]
    elif mutation == "duplicate-adapter":
        manifest["adapters"].append(dict(manifest["adapters"][0]))
    elif mutation == "compatibility-string":
        manifest["compatibility"]["project_schema_versions"] = "1.0.0"
    elif mutation == "component-list":
        manifest["core"] = []
    else:
        manifest["core"]["digest"] = ["sha256", "not-a-string"]
    manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(DistributionError, match=message):
        load_distribution_manifest(release)


def test_distribution_digest_binds_the_executable_bit(tmp_path: Path) -> None:
    release = _copy_release(tmp_path)
    installer = release / "scripts/install.sh"
    assert installer.stat().st_mode & 0o111
    before = tree_digest(release / "scripts")

    installer.chmod(installer.stat().st_mode & ~0o111)
    after = tree_digest(release / "scripts")
    result = verify_distribution(release)

    assert after != before
    assert result["valid"] is False
    assert any("bootstrap digest mismatch" in issue for issue in result["issues"])


def test_distribution_verification_rejects_forged_component_metadata(
    tmp_path: Path,
) -> None:
    release = _copy_release(tmp_path)
    manifest_path = release / "distribution/release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["version"] = "forged-release"
    manifest["core"]["version"] = "forged-core"
    manifest["foundation"]["id"] = "forged-foundation"
    for adapter in manifest["adapters"]:
        adapter["version"] = "forged-adapter"
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    result = verify_distribution(release)

    assert result["valid"] is False
    assert any("source manifests" in issue for issue in result["issues"])


def test_project_install_is_pinned_idempotent_and_host_ready(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)

    first = _install(project)
    before = (project / "isekai.lock.json").read_bytes()
    second = _install(project)
    lock = load_install_lock(project)

    assert first["installed"] is True
    assert second["unchanged"] is True
    assert (project / "isekai.lock.json").read_bytes() == before
    assert lock is not None
    assert lock["source"]["ref"] == "v0.1.0"
    assert lock["source"]["commit"] == "a" * 40
    assert set(lock["adapters"]) == {"kiro", "claude", "codex"}
    assert lock["adapters"]["codex"]["path"] == (
        ".isekai/marketplaces/codex/plugins/isekai-agent-plugin"
    )
    assert lock["adapters"]["codex"]["workspace_path"] == (
        ".agents/skills/isekai"
    )
    assert lock["adapters"]["claude"]["path"] == (
        ".isekai/marketplaces/claude/plugins/isekai-agent-plugin"
    )
    assert lock["adapters"]["claude"]["workspace_path"] == (
        ".claude/skills/isekai"
    )
    assert lock["adapters"]["kiro"]["path"] == ".kiro/skills/isekai"
    assert (project / ".isekai/marketplaces/codex/plugins/isekai-agent-plugin/.codex-plugin/plugin.json").is_file()
    assert (project / ".isekai/marketplaces/claude/plugins/isekai-agent-plugin/.claude-plugin/plugin.json").is_file()
    assert (project / ".agents/skills/isekai/SKILL.md").is_file()
    assert (project / ".claude/skills/isekai/SKILL.md").is_file()
    assert (project / ".kiro/skills/isekai/SKILL.md").is_file()
    codex_marketplace = json.loads(
        (project / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )
    codex_entry = next(
        entry
        for entry in codex_marketplace["plugins"]
        if entry["name"] == "isekai-agent-plugin"
    )
    assert codex_entry["source"]["path"] == (
        "./.isekai/marketplaces/codex/plugins/isekai-agent-plugin"
    )
    assert codex_entry["policy"]["installation"] == "INSTALLED_BY_DEFAULT"
    claude_settings = json.loads(
        (project / ".claude/settings.json").read_text(encoding="utf-8")
    )
    plugin_key = f"isekai-agent-plugin@{lock['marketplace']}"
    assert claude_settings["enabledPlugins"][plugin_key] is True
    assert "registration_commands" not in first
    assert first["host_registration_required"] is False
    assert doctor_install(project)["ready"] is True
    assert verify_adapter_handshake("codex", "0.1.0", "1.0.0", project)["locked"] is True

    with pytest.raises(DistributionError, match="adapter version does not match project lock"):
        verify_adapter_handshake("codex", "0.2.0", "1.0.0", project)

    completed = subprocess.run(
        [str(project / ".isekai/bin/isekai"), "plugin", "compatibility"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["core_version"] == "0.1.0"


def test_handshake_rejects_a_project_without_an_install_lock(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)

    with pytest.raises(DistributionError, match="installation lock is missing"):
        verify_adapter_handshake("codex", "0.1.0", "1.0.0", project)


def test_installed_launcher_initializes_project_from_locked_foundation(
    tmp_path: Path,
) -> None:
    project = tmp_path / "empty-product"
    project.mkdir()
    result = _install(project)

    completed = subprocess.run(
        [str(project / ".isekai/bin/isekai"), "init", "--path", str(project)],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    manifest = json.loads((project / "project.json").read_text(encoding="utf-8"))

    assert "next_action" in result
    assert completed.returncode == 0, completed.stderr
    assert manifest["foundation_path"] == ".isekai/foundations/0.1.0"
    assert doctor_install(project)["ready"] is True


def test_doctor_and_update_fail_closed_after_managed_file_tampering(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    core_file = project / ".isekai/runtime/isekai/__init__.py"
    core_file.write_text(core_file.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

    health = doctor_install(project)

    assert health["ready"] is False
    assert "core digest mismatch" in health["issues"]
    with pytest.raises(DistributionError, match="modified or are incomplete"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.1",
            commit="b" * 40,
            runtimes=("codex",),
            update=True,
        )


def test_install_rejects_component_changes_after_release_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from isekai.distribution import install as install_module

    release = _copy_release(tmp_path)
    project = _project_with_foundation(tmp_path / "target")
    original_replace = install_module._replace_tree
    changed = False

    def change_before_copy(source: Path, target: Path) -> None:
        nonlocal changed
        if not changed and target.as_posix().endswith("/runtime/isekai"):
            changed = True
            init_module = source / "__init__.py"
            init_module.write_text(
                init_module.read_text(encoding="utf-8") + "\n# late change\n",
                encoding="utf-8",
            )
        original_replace(source, target)

    monkeypatch.setattr(install_module, "_replace_tree", change_before_copy)

    with pytest.raises(DistributionError, match="Core changed after release verification"):
        install_from_checkout(
            release,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("all",),
        )

    assert changed is True
    assert not (project / ".isekai").exists()
    assert not (project / "isekai.lock.json").exists()


def test_install_excludes_unhashed_bytecode_and_doctor_rejects_new_cache(
    tmp_path: Path,
) -> None:
    release = _copy_release(tmp_path)
    source_cache = release / "src/isekai/__pycache__/unchecked.pyc"
    source_cache.parent.mkdir()
    source_cache.write_bytes(b"unchecked release bytecode")
    assert verify_distribution(release)["valid"] is True

    project = _project_with_foundation(tmp_path / "project-root")
    _install(project, release)
    installed_core = project / ".isekai/runtime/isekai"

    assert not list(installed_core.rglob("__pycache__"))
    clean_launch = subprocess.run(
        [str(project / ".isekai/bin/isekai"), "plugin", "compatibility"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )
    assert clean_launch.returncode == 0, clean_launch.stderr
    assert not list(installed_core.rglob("__pycache__"))
    assert doctor_install(project)["ready"] is True

    installed_cache = installed_core / "__pycache__/poisoned.pyc"
    installed_cache.parent.mkdir()
    installed_cache.write_bytes(b"unchecked installed bytecode")

    health = doctor_install(project)
    launched = subprocess.run(
        [str(project / ".isekai/bin/isekai"), "plugin", "compatibility"],
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
    )

    assert health["ready"] is False
    assert "core digest mismatch" in health["issues"]
    assert launched.returncode != 0
    assert "unchecked bytecode" in launched.stderr


@pytest.mark.parametrize(
    ("relative", "expected_issue"),
    [
        (".isekai/bin/isekai", "managed launcher"),
        (
            ".isekai/marketplaces/codex/plugins/isekai-agent-plugin/skills/isekai/agents/openai.yaml",
            "adapter:codex digest mismatch",
        ),
        (
            ".agents/skills/isekai/agents/openai.yaml",
            "adapter:codex.workspace digest mismatch",
        ),
        (
            ".isekai/marketplaces/claude/plugins/isekai-agent-plugin/skills/isekai/SKILL.md",
            "adapter:claude digest mismatch",
        ),
        (
            ".claude/skills/isekai/SKILL.md",
            "adapter:claude.workspace digest mismatch",
        ),
        (".kiro/skills/isekai/SKILL.md", "adapter:kiro digest mismatch"),
        (".agents/plugins/marketplace.json", "Codex repo marketplace"),
        (".claude/settings.json", "Claude project marketplace declaration"),
    ],
)
def test_doctor_fails_closed_after_generated_control_file_tampering(
    tmp_path: Path,
    relative: str,
    expected_issue: str,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    target = project / relative
    target.write_text("{}\n", encoding="utf-8")

    health = doctor_install(project)

    assert health["ready"] is False
    assert any(expected_issue in issue for issue in health["issues"])


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize(
    "relative",
    [
        ".isekai/bin/isekai.py",
        ".isekai/marketplaces/codex/.agents/plugins/marketplace.json",
        ".agents/plugins/marketplace.json",
        ".claude/settings.json",
    ],
)
def test_doctor_rejects_aliased_launcher_and_host_control_files(
    tmp_path: Path,
    alias_kind: str,
    relative: str,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    control = project / relative
    external = tmp_path / (relative.replace("/", "-").replace(".", "_") + ".external")
    control.rename(external)
    if alias_kind == "symlink":
        control.symlink_to(external)
    else:
        os.link(external, control)

    health = doctor_install(project)

    assert health["ready"] is False
    assert any("single-link" in issue or "symlink" in issue for issue in health["issues"])


def test_install_fails_closed_while_another_install_holds_the_project_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from isekai.distribution import install as distribution_module
    from isekai.locking import file_lock as real_file_lock

    project = _project_with_foundation(tmp_path)
    lock = project / distribution_module.INSTALL_LOCK_NAME
    with real_file_lock(lock, subject="test installation holder"):
        monkeypatch.setattr(
            distribution_module,
            "file_lock",
            lambda path, *, subject: real_file_lock(
                path, subject=subject, timeout=0
            ),
        )
        with pytest.raises(DistributionError, match="being modified"):
            _install(project)

    assert not (project / ".isekai").exists()
    assert not (project / "isekai.lock.json").exists()


@pytest.mark.parametrize(
    "source",
    [
        "https://token@example.invalid/isekai.git",
        "https://user:secret@example.invalid/isekai.git",
        "https://example.invalid/isekai.git?token=secret",
        "https://example.invalid/isekai.git#credential",
    ],
)
def test_git_install_rejects_sources_that_could_persist_credentials(
    tmp_path: Path,
    source: str,
) -> None:
    project = _project_with_foundation(tmp_path)

    with pytest.raises(DistributionError, match="credentials|query or fragment"):
        install_from_git(source, "v0.1.0", project, runtimes=("kiro",))

    assert not (project / "isekai.lock.json").exists()


def test_git_source_rejects_noncanonical_file_url_authorities(
    tmp_path: Path,
) -> None:
    from isekai.distribution.git import _validate_git_source

    uri = (tmp_path / "release").resolve().as_uri()
    invalid = (
        uri.replace("file://", "file://localhost.", 1),
        uri.replace("file://", "file://127.0.0.1", 1),
        uri.replace("file://", "file://user@localhost", 1),
        uri.replace("file://", "file://%6cocalhost", 1),
        uri.replace("file://", "file://example.invalid", 1),
    )

    for source in invalid:
        with pytest.raises(DistributionError, match="file URL"):
            _validate_git_source(source)

    assert _validate_git_source(uri) == uri
    localhost = uri.replace("file://", "file://LOCALHOST", 1)
    assert _validate_git_source(localhost) == localhost


def test_update_preserves_foundation_and_rollback_restores_previous_release(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    old_lock = load_install_lock(project)
    assert old_lock is not None
    adapter_paths = {
        runtime: project / entry["path"]
        for runtime, entry in old_lock["adapters"].items()
    }
    old_adapter_digests = {
        runtime: tree_digest(path) for runtime, path in adapter_paths.items()
    }

    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    updated = install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    updated_lock = load_install_lock(project)

    assert updated["updated"] is True
    assert updated_lock is not None
    assert updated_lock["release"] == "0.1.1"
    assert updated_lock["core"]["version"] == "0.1.1"
    assert updated_lock["foundation"]["version"] == "0.1.0"
    assert updated_lock["rollback"]["digest"] == tree_digest(
        project / ".isekai/rollback",
        include_transients=True,
    )
    assert doctor_install(project)["ready"] is True

    rolled_back = rollback_install(project)
    restored = load_install_lock(project)

    assert rolled_back["rolled_back"] is True
    assert {key: value for key, value in restored.items() if key != "rollback"} == old_lock
    assert restored["rollback"]["digest"] == tree_digest(
        project / ".isekai/rollback",
        include_transients=True,
    )
    assert {
        runtime: tree_digest(path) for runtime, path in adapter_paths.items()
    } == old_adapter_digests
    assert doctor_install(project)["ready"] is True


def test_rollback_rejects_a_modified_integrity_bound_snapshot(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    current_lock = (project / "isekai.lock.json").read_bytes()
    current_core = tree_digest(project / ".isekai/runtime/isekai")
    rollback = project / ".isekai/rollback"
    snapshot_core = rollback / "install/runtime/isekai"
    init_module = snapshot_core / "__init__.py"
    init_module.write_text(
        init_module.read_text(encoding="utf-8") + "\n# modified snapshot\n",
        encoding="utf-8",
    )
    previous_lock_path = rollback / "isekai.lock.json"
    previous_lock = json.loads(previous_lock_path.read_text(encoding="utf-8"))
    previous_lock["core"]["digest"] = tree_digest(
        snapshot_core,
        include_transients=True,
    )
    previous_lock["source"]["commit"] = "c" * 40
    previous_lock_path.write_text(
        json.dumps(previous_lock, indent=2) + "\n",
        encoding="utf-8",
    )

    health = doctor_install(project)

    assert health["ready"] is False
    assert any("rollback digest mismatch" in issue for issue in health["issues"])
    with pytest.raises(DistributionError, match="modified installation"):
        rollback_install(project)
    assert (project / "isekai.lock.json").read_bytes() == current_lock
    assert tree_digest(project / ".isekai/runtime/isekai") == current_core


def test_malformed_install_lock_fails_closed_without_raw_cli_exception(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    lock_path = project / "isekai.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["source"] = []
    lock["core"]["version"] = {"unexpected": True}
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(DistributionError, match="lock source must be an object"):
        load_install_lock(project)
    health = doctor_install(project)
    exit_code = cli_main(
        ["update", "--check", "--ref", "v0.1.1", "--path", str(project)]
    )
    captured = capsys.readouterr()

    assert health["ready"] is False
    assert any("lock source must be an object" in issue for issue in health["issues"])
    assert exit_code == 2
    assert "lock source must be an object" in captured.err


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_install_lock_must_be_a_single_link_regular_file(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    lock_path = project / "isekai.lock.json"
    external = tmp_path / "external-lock.json"
    lock_path.rename(external)
    if alias_kind == "symlink":
        lock_path.symlink_to(external)
    else:
        os.link(external, lock_path)

    with pytest.raises(DistributionError, match="single-link|symlink"):
        load_install_lock(project)
    health = doctor_install(project)

    assert health["ready"] is False
    assert any("single-link" in issue or "symlink" in issue for issue in health["issues"])


def test_install_lock_directory_is_rejected_instead_of_treated_as_missing(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    lock_path = project / "isekai.lock.json"
    lock_path.unlink()
    lock_path.mkdir()

    with pytest.raises(DistributionError, match="single-link regular file"):
        load_install_lock(project)

    health = doctor_install(project)
    assert health["ready"] is False
    assert not any(issue == "missing isekai.lock.json" for issue in health["issues"])


def test_install_rejects_a_non_file_project_manifest(
    tmp_path: Path,
) -> None:
    project = tmp_path / "product"
    project.mkdir()
    shutil.copytree(ROOT / "foundation", project / "foundation")
    (project / "project.json").mkdir()

    with pytest.raises(DistributionError, match="single-link regular file"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("kiro",),
        )

    assert not (project / ".isekai").exists()
    assert not (project / "isekai.lock.json").exists()


def test_rollback_rejects_an_unbound_legacy_snapshot(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    lock_path = project / "isekai.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    del lock["rollback"]
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(DistributionError, match="not integrity-bound"):
        rollback_install(project)


def test_install_from_immutable_local_git_tag_records_resolved_commit(
    tmp_path: Path,
) -> None:
    release = _copy_release(tmp_path)
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
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    project = _project_with_foundation(tmp_path / "target")

    result = install_from_git(
        str(release),
        "v0.1.0",
        project,
        runtimes=("kiro",),
    )

    assert result["commit"] == expected_commit
    assert load_install_lock(project)["source"]["commit"] == expected_commit


def test_public_checkout_install_rejects_a_commit_unbound_to_head(
    tmp_path: Path,
) -> None:
    from isekai.distribution import install_from_checkout as verified_install

    release = _copy_release(tmp_path)
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
    project = _project_with_foundation(tmp_path / "target")

    with pytest.raises(DistributionError, match="claimed commit"):
        verified_install(
            release,
            project,
            source=str(release),
            ref="v0.1.0",
            commit="b" * 40,
            runtimes=("kiro",),
        )

    assert not (project / "isekai.lock.json").exists()


def test_later_kiro_install_refuses_unmanaged_skill(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=("codex",), **common)
    kiro = project / ".kiro/skills/isekai"
    kiro.mkdir(parents=True)
    marker = kiro / "UNMANAGED.txt"
    marker.write_text("preserve me", encoding="utf-8")

    with pytest.raises(DistributionError, match="unmanaged .kiro/skills/isekai"):
        install_from_checkout(ROOT, project, runtimes=("kiro",), **common)

    assert marker.read_text(encoding="utf-8") == "preserve me"
    assert set(load_install_lock(project)["adapters"]) == {"codex"}


@pytest.mark.parametrize(
    ("runtime", "relative"),
    [
        ("codex", ".agents/skills/isekai"),
        ("claude", ".claude/skills/isekai"),
        ("kiro", ".kiro/skills/isekai"),
    ],
)
def test_install_refuses_an_unmanaged_workspace_adapter(
    tmp_path: Path,
    runtime: str,
    relative: str,
) -> None:
    project = _project_with_foundation(tmp_path)
    target = project / relative
    target.mkdir(parents=True)
    marker = target / "UNMANAGED.txt"
    marker.write_text("preserve me", encoding="utf-8")

    with pytest.raises(DistributionError, match=f"unmanaged {relative}"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=(runtime,),
        )

    assert marker.read_text(encoding="utf-8") == "preserve me"
    assert not (project / "isekai.lock.json").exists()


def test_codex_install_refuses_an_unmanaged_isekai_marketplace_entry(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    marketplace = project / ".agents/plugins/marketplace.json"
    marketplace.parent.mkdir(parents=True)
    marketplace.write_text(
        json.dumps(
            {
                "name": "team-tools",
                "plugins": [
                    {
                        "name": "isekai-agent-plugin",
                        "source": {"source": "local", "path": "./someone-else"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DistributionError, match="unmanaged isekai-agent-plugin"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("codex",),
        )


def test_project_host_merge_and_rollback_preserve_unrelated_settings(
    tmp_path: Path,
) -> None:
    project = _project_with_foundation(tmp_path)
    codex_path = project / ".agents/plugins/marketplace.json"
    codex_path.parent.mkdir(parents=True)
    codex_path.write_text(
        json.dumps(
            {
                "name": "team-tools",
                "plugins": [{"name": "other-plugin", "source": "./plugins/other"}],
            }
        ),
        encoding="utf-8",
    )
    claude_path = project / ".claude/settings.json"
    claude_path.parent.mkdir(parents=True)
    claude_path.write_text(
        json.dumps(
            {
                "enabledPlugins": {"other-plugin@team": False},
                "extraKnownMarketplaces": {
                    "team": {"source": {"source": "github", "repo": "org/plugins"}}
                },
                "permissions": {"allow": ["Read"]},
            }
        ),
        encoding="utf-8",
    )
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=("kiro",), **common)
    install_from_checkout(ROOT, project, runtimes=("codex", "claude"), **common)

    codex = json.loads(codex_path.read_text(encoding="utf-8"))
    codex["plugins"].append({"name": "later-plugin", "source": "./plugins/later"})
    codex_path.write_text(json.dumps(codex), encoding="utf-8")
    claude = json.loads(claude_path.read_text(encoding="utf-8"))
    claude["customSetting"] = "preserve"
    claude_path.write_text(json.dumps(claude), encoding="utf-8")

    assert doctor_install(project)["ready"] is True
    rollback_install(project)

    restored_codex = json.loads(codex_path.read_text(encoding="utf-8"))
    assert [entry["name"] for entry in restored_codex["plugins"]] == [
        "other-plugin",
        "later-plugin",
    ]
    restored_claude = json.loads(claude_path.read_text(encoding="utf-8"))
    assert restored_claude["enabledPlugins"] == {"other-plugin@team": False}
    assert set(restored_claude["extraKnownMarketplaces"]) == {"team"}
    assert restored_claude["permissions"] == {"allow": ["Read"]}
    assert restored_claude["customSetting"] == "preserve"
    assert doctor_install(project)["ready"] is True


def test_rollback_removes_kiro_added_after_codex_only_install(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=("codex",), **common)
    install_from_checkout(ROOT, project, runtimes=("kiro",), **common)
    kiro = project / ".kiro/skills/isekai"
    assert kiro.is_dir()

    rollback_install(project)
    restored = load_install_lock(project)

    assert restored is not None
    assert set(restored["adapters"]) == {"codex"}
    assert not kiro.exists()
    assert doctor_install(project)["ready"] is True


@pytest.mark.parametrize(
    ("base_runtime", "added_runtime", "relative"),
    [
        (
            "kiro",
            "codex",
            ".isekai/marketplaces/codex/plugins/isekai-agent-plugin",
        ),
        (
            "kiro",
            "claude",
            ".isekai/marketplaces/claude/plugins/isekai-agent-plugin",
        ),
    ],
)
def test_rollback_removes_any_adapter_added_later(
    tmp_path: Path,
    base_runtime: str,
    added_runtime: str,
    relative: str,
) -> None:
    project = _project_with_foundation(tmp_path)
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=(base_runtime,), **common)
    install_from_checkout(ROOT, project, runtimes=(added_runtime,), **common)
    target = project / relative
    assert target.is_dir()

    rollback_install(project)
    restored = load_install_lock(project)

    assert restored is not None
    assert set(restored["adapters"]) == {base_runtime}
    assert not target.exists()
    assert doctor_install(project)["ready"] is True


def test_post_install_failure_restores_new_project_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai.distribution import install as distribution_module

    project = _project_with_foundation(tmp_path)
    project_before = (project / "project.json").read_bytes()
    monkeypatch.setattr(
        distribution_module,
        "doctor_install",
        lambda _project: {"ready": False, "issues": ["forced postflight failure"]},
    )

    with pytest.raises(DistributionError, match="forced postflight failure"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("kiro",),
        )

    assert (project / "project.json").read_bytes() == project_before
    assert not (project / ".isekai").exists()
    assert not (project / ".kiro/skills/isekai").exists()
    assert not (project / "isekai.lock.json").exists()


def test_partial_host_configuration_failure_restores_every_host_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai.distribution import marketplace as marketplace_module

    project = _project_with_foundation(tmp_path).resolve()
    original_write = marketplace_module._write_json_atomic

    def fail_claude_write(path: Path, value: dict[str, object]) -> None:
        if path == project / ".claude/settings.json":
            raise OSError("forced second host write failure")
        original_write(path, value)

    monkeypatch.setattr(marketplace_module, "_write_json_atomic", fail_claude_write)

    with pytest.raises(OSError, match="forced second host write failure"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("codex", "claude"),
        )

    assert not (project / ".agents/plugins/marketplace.json").exists()
    assert not (project / ".claude/settings.json").exists()
    assert not (project / ".isekai").exists()
    assert not (project / "isekai.lock.json").exists()


def test_rollback_failure_restores_current_project_lock_and_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai.distribution import install as distribution_module

    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
        include_foundation=True,
        adopt_foundation=True,
    )
    project_before = (project / "project.json").read_bytes()
    lock_before = (project / "isekai.lock.json").read_bytes()
    core_before = tree_digest(project / ".isekai/runtime/isekai")
    kiro_before = tree_digest(project / ".kiro/skills/isekai")
    original_write = distribution_module._write_json_atomic

    def fail_previous_lock(path: Path, value: dict[str, object]) -> None:
        if path == project / "isekai.lock.json" and value.get("release") == "0.1.0":
            raise OSError("forced rollback lock failure")
        original_write(path, value)

    monkeypatch.setattr(distribution_module, "_write_json_atomic", fail_previous_lock)

    with pytest.raises(OSError, match="forced rollback lock failure"):
        rollback_install(project)

    assert (project / "project.json").read_bytes() == project_before
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert tree_digest(project / ".isekai/runtime/isekai") == core_before
    assert tree_digest(project / ".kiro/skills/isekai") == kiro_before
    assert doctor_install(project)["ready"] is True


def test_git_install_rejects_branch_and_abbreviated_commit_refs(tmp_path: Path) -> None:
    release = _copy_release(tmp_path)
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
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=release,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=release,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    project = _project_with_foundation(tmp_path / "target")

    with pytest.raises(DistributionError, match="immutable tag or full commit"):
        install_from_git(str(release), branch, project, runtimes=("kiro",))
    with pytest.raises(DistributionError, match="immutable tag or full commit"):
        install_from_git(str(release), commit[:12], project, runtimes=("kiro",))

    result = install_from_git(str(release), commit, project, runtimes=("kiro",))
    assert result["commit"] == commit


@pytest.mark.parametrize("ref", ["v1^0", "v1^{}", "v1^{commit}", "v1~0"])
def test_git_revision_expressions_are_not_accepted_as_tag_names(
    tmp_path: Path,
    ref: str,
) -> None:
    from isekai.distribution.git import _resolve_immutable_git_ref

    release = tmp_path / "release"
    release.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=release, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ISEKAI Test",
            "-c",
            "user.email=isekai@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "release",
        ],
        cwd=release,
        check=True,
    )
    subprocess.run(["git", "tag", "v1"], cwd=release, check=True)

    with pytest.raises(DistributionError, match="revision expressions"):
        _resolve_immutable_git_ref(release, ref)


def test_moved_tag_cannot_bypass_the_lock_with_an_equivalent_source_spelling(
    tmp_path: Path,
) -> None:
    from isekai.distribution import _reject_moved_ref

    project = tmp_path / "project"
    project.mkdir()
    source = tmp_path / "release-repository"
    (project / "isekai.lock.json").write_text(
        json.dumps(
                {
                    "schema_version": "1.0.0",
                    "release": "0.1.0",
                    "protocol_version": "1.0.0",
                    "source": {
                        "git": str(source),
                        "ref": "v0.1.0",
                        "commit": "a" * 40,
                    },
                    "marketplace": "isekai-project",
                    "core": {
                        "version": "0.1.0",
                        "path": ".isekai/runtime/isekai",
                        "source_digest": "sha256:" + "0" * 64,
                        "digest": "sha256:" + "1" * 64,
                    },
                    "foundation": {
                        "id": "isekai-foundation",
                        "version": "0.1.0",
                        "path": ".isekai/foundations/0.1.0",
                        "source_release": "0.1.0",
                        "digest": "sha256:" + "2" * 64,
                    },
                    "adapters": {},
                }
        )
        + "\n",
        encoding="utf-8",
    )

    equivalent_sources = (
        source.as_uri(),
        source.as_uri().replace("file://", "file://LOCALHOST", 1),
        source.as_uri().replace("file://", "FILE://LOCALHOST", 1),
    )
    for equivalent_source in equivalent_sources:
        with pytest.raises(DistributionError, match="Git ref moved"):
            _reject_moved_ref(project, equivalent_source, "v0.1.0", "b" * 40)


def test_moved_tag_cannot_bypass_remote_url_canonicalization(
    tmp_path: Path,
) -> None:
    from isekai.distribution import _reject_moved_ref

    project = tmp_path / "project"
    project.mkdir()
    (project / "isekai.lock.json").write_text(
        json.dumps(
                {
                    "schema_version": "1.0.0",
                    "release": "0.1.0",
                    "protocol_version": "1.0.0",
                    "source": {
                        "git": "https://EXAMPLE.com:443/org/repo.git",
                        "ref": "v0.1.0",
                        "commit": "a" * 40,
                    },
                    "marketplace": "isekai-project",
                    "core": {
                        "version": "0.1.0",
                        "path": ".isekai/runtime/isekai",
                        "source_digest": "sha256:" + "0" * 64,
                        "digest": "sha256:" + "1" * 64,
                    },
                    "foundation": {
                        "id": "isekai-foundation",
                        "version": "0.1.0",
                        "path": ".isekai/foundations/0.1.0",
                        "source_release": "0.1.0",
                        "digest": "sha256:" + "2" * 64,
                    },
                    "adapters": {},
                }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(DistributionError, match="Git ref moved"):
        _reject_moved_ref(
            project,
            "https://example.com/org/repo.git",
            "v0.1.0",
            "b" * 40,
        )


def test_update_plan_reports_source_digest_changes(tmp_path: Path) -> None:
    from isekai.distribution import plan_git_update

    project = _project_with_foundation(tmp_path / "current")
    _install(project)
    current = load_install_lock(project)
    assert current is not None

    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
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
    subprocess.run(["git", "tag", "v0.1.1"], cwd=release, check=True)
    target_manifest = json.loads(
        (release / "distribution/release.json").read_text(encoding="utf-8")
    )

    plan = plan_git_update(
        str(release),
        "v0.1.1",
        project,
        runtimes=("codex",),
    )
    changes = {item["component"]: item for item in plan["changes"]}

    assert changes["core"] == {
        "component": "core",
        "from": "0.1.0",
        "to": "0.1.1",
        "from_digest": current["core"]["source_digest"],
        "to_digest": target_manifest["core"]["digest"],
        "changed": True,
    }
    assert changes["adapter:codex"]["from_digest"] == current["adapters"][
        "codex"
    ]["source_digest"]
    assert changes["adapter:codex"]["to_digest"] == next(
        item["digest"]
        for item in target_manifest["adapters"]
        if item["id"] == "codex"
    )
    assert changes["adapter:codex"]["changed"] is True
    assert changes["foundation"]["from_digest"] == current["foundation"]["digest"]
    assert changes["foundation"]["to_digest"] == current["foundation"]["digest"]
    assert changes["foundation"]["changed"] is False
    assert changes["foundation"]["policy"] == "preserved"

    explicit_plan = plan_git_update(
        str(release),
        "v0.1.1",
        project,
        runtimes=("codex",),
        include_foundation=True,
    )
    explicit_foundation = next(
        item for item in explicit_plan["changes"] if item["component"] == "foundation"
    )
    assert explicit_foundation["from"] == "0.1.0"
    assert explicit_foundation["to"] == "0.1.1"
    assert explicit_foundation["from_digest"] == current["foundation"]["digest"]
    assert explicit_foundation["to_digest"] == target_manifest["foundation"]["digest"]
    assert explicit_foundation["changed"] is True
    assert explicit_foundation["policy"] == "explicit"


def test_rollback_staging_failure_preserves_current_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai.distribution import install as distribution_module

    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    project_before = (project / "project.json").read_bytes()
    lock_before = (project / "isekai.lock.json").read_bytes()
    core_before = tree_digest(project / ".isekai/runtime/isekai")
    kiro_before = tree_digest(project / ".kiro/skills/isekai")

    monkeypatch.setattr(
        distribution_module,
        "_copy_managed_root",
        lambda _source, _destination: (_ for _ in ()).throw(
            OSError("forced rollback staging failure")
        ),
    )

    with pytest.raises(OSError, match="forced rollback staging failure"):
        rollback_install(project)

    assert (project / "project.json").read_bytes() == project_before
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert tree_digest(project / ".isekai/runtime/isekai") == core_before
    assert tree_digest(project / ".kiro/skills/isekai") == kiro_before
    assert doctor_install(project)["ready"] is True


def test_rollback_restores_absent_project_manifest(tmp_path: Path) -> None:
    project = tmp_path / "product"
    project.mkdir()
    common = {
        "source": "https://example.invalid/isekai.git",
        "ref": "v0.1.0",
        "commit": "a" * 40,
    }
    install_from_checkout(ROOT, project, runtimes=("codex",), **common)
    install_from_checkout(ROOT, project, runtimes=("kiro",), **common)
    assert not (project / "project.json").exists()
    (project / "project.json").write_text(
        json.dumps(
            {
                "id": "product",
                "kind": "project",
                "schema_version": "1.0.0",
                "version": "0.1.0",
                "foundation_path": ".isekai/foundations/0.1.0",
                "profiles": ["software-delivery-profile"],
                "extensions": [],
                "maximum_agent_level": "L0",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    assert doctor_install(project)["ready"] is True

    rollback_install(project)
    restored = load_install_lock(project)

    assert restored is not None
    assert set(restored["adapters"]) == {"codex"}
    assert not (project / "project.json").exists()
    assert not (project / ".kiro/skills/isekai").exists()
    assert doctor_install(project)["ready"] is True


def test_managed_kiro_symlink_is_rejected_before_update(tmp_path: Path) -> None:
    project = _project_with_foundation(tmp_path)
    _install(project)
    lock_before = (project / "isekai.lock.json").read_bytes()
    kiro = project / ".kiro/skills/isekai"
    kiro_copy = project / ".kiro/skills/isekai-copy"
    shutil.copytree(kiro, kiro_copy)
    shutil.rmtree(kiro)
    kiro.symlink_to(kiro_copy, target_is_directory=True)

    health = doctor_install(project)

    assert health["ready"] is False
    assert any(
        "adapter:kiro.path contains a symlink" in issue for issue in health["issues"]
    )
    with pytest.raises(
        DistributionError, match="adapter:kiro.path contains a symlink"
    ):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.1",
            commit="b" * 40,
            runtimes=("kiro",),
            update=True,
        )
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert kiro.is_symlink()
    assert not list(project.glob(".isekai-kiro-backup-*"))


def test_rollback_postflight_failure_restores_current_installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai.distribution import install as distribution_module

    project = _project_with_foundation(tmp_path)
    _install(project)
    release = _copy_release(tmp_path)
    _bump_release(release, "0.1.1")
    install_from_checkout(
        release,
        project,
        source="https://example.invalid/isekai.git",
        ref="v0.1.1",
        commit="b" * 40,
        runtimes=("all",),
        update=True,
    )
    project_before = (project / "project.json").read_bytes()
    lock_before = (project / "isekai.lock.json").read_bytes()
    core_before = tree_digest(project / ".isekai/runtime/isekai")
    kiro_before = tree_digest(project / ".kiro/skills/isekai")
    real_doctor = distribution_module.doctor_install
    calls = 0

    def fail_postflight(target: Path) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_doctor(target)
        return {"ready": False, "issues": ["forced rollback postflight failure"]}

    monkeypatch.setattr(distribution_module, "doctor_install", fail_postflight)

    with pytest.raises(DistributionError, match="forced rollback postflight failure"):
        rollback_install(project)

    assert (project / "project.json").read_bytes() == project_before
    assert (project / "isekai.lock.json").read_bytes() == lock_before
    assert tree_digest(project / ".isekai/runtime/isekai") == core_before
    assert tree_digest(project / ".kiro/skills/isekai") == kiro_before
    assert real_doctor(project)["ready"] is True


@pytest.mark.parametrize("symlink_parent", [".kiro", ".kiro/skills"])
def test_new_kiro_install_rejects_symlinked_parent_paths(
    tmp_path: Path, symlink_parent: str
) -> None:
    project = _project_with_foundation(tmp_path)
    external = tmp_path / "external-kiro-root"
    external.mkdir()
    parent = project / symlink_parent
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.symlink_to(external, target_is_directory=True)

    with pytest.raises(DistributionError, match="adapter:kiro.path contains a symlink"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=("kiro",),
        )


@pytest.mark.parametrize(
    ("runtime", "symlink_parent", "expected_label"),
    [
        ("codex", ".agents", "adapter:codex.path"),
        ("claude", ".claude", "adapter:claude.path"),
    ],
)
def test_plugin_install_rejects_symlinked_host_configuration_paths(
    tmp_path: Path,
    runtime: str,
    symlink_parent: str,
    expected_label: str,
) -> None:
    project = _project_with_foundation(tmp_path)
    external = tmp_path / f"external-{runtime}-root"
    external.mkdir()
    (project / symlink_parent).symlink_to(external, target_is_directory=True)

    with pytest.raises(DistributionError, match=f"{expected_label} contains a symlink"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=(runtime,),
        )

    assert not list(external.rglob("SKILL.md"))
    assert not (project / ".isekai").exists()
    assert not (project / "isekai.lock.json").exists()


@pytest.mark.parametrize(
    ("runtime", "relative"),
    [
        ("codex", ".agents/plugins/marketplace.json"),
        ("claude", ".claude/settings.json"),
    ],
)
def test_plugin_install_rejects_non_file_host_configuration(
    tmp_path: Path,
    runtime: str,
    relative: str,
) -> None:
    project = _project_with_foundation(tmp_path)
    target = project / relative
    target.mkdir(parents=True)

    with pytest.raises(DistributionError, match="single-link regular file"):
        install_from_checkout(
            ROOT,
            project,
            source="https://example.invalid/isekai.git",
            ref="v0.1.0",
            commit="a" * 40,
            runtimes=(runtime,),
        )

    assert target.is_dir()
    assert not (project / ".isekai").exists()
    assert not (project / "isekai.lock.json").exists()
