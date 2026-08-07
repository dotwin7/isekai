from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from isekai.foundation import FoundationError, load_foundation
from isekai.session import SessionError, resume_session
from isekai.workflow import (
    UNIT_REQUIRED_FILES,
    RouteRequest,
    WorkRoute,
    classify_work,
    initialize_unit,
    resolve_context,
    transition_unit,
    verify_unit,
)


ROOT = Path(__file__).resolve().parents[1]


def make_project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    project_root.mkdir()
    shutil.copytree(ROOT / "foundation", project_root / "foundation")
    (project_root / "extension").mkdir()
    shutil.copy(
        ROOT / "examples/reference-product/extension/reference-product.json",
        project_root / "extension/reference-product.json",
    )
    manifest = {
        "id": "test-project",
        "kind": "project",
        "version": "0.1.0",
        "foundation_path": "foundation",
        "profiles": ["security-profile", "software-delivery-profile"],
        "extensions": [
            {"id": "reference-product-extension", "path": "extension/reference-product.json"}
        ],
        "maximum_agent_level": "L0",
    }
    (project_root / "project.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return project_root / "project.json"


def test_canonical_unit_tree_matches_core_required_artifacts() -> None:
    canonical = (ROOT / "docs/unit.md").read_text(encoding="utf-8")

    for relative in UNIT_REQUIRED_FILES:
        assert relative in canonical
    assert "unit.yaml" not in canonical
    assert "decisions.md" not in canonical


def test_foundation_loads_and_reports_expected_assets() -> None:
    foundation = load_foundation(ROOT / "foundation")

    assert foundation.manifest["status"] == "approved"
    assert foundation.summary()["asset_count"] == 21
    assert foundation.summary()["kinds"]["agent-execution-contract"] == 1
    assert foundation.summary()["kinds"]["unit-dod-evaluation-contract"] == 1


@pytest.mark.parametrize(
    ("route_request", "expected"),
    [
        (RouteRequest(change="none", risk="low"), WorkRoute.QUERY),
        (RouteRequest(change="local", risk="low"), WorkRoute.QUICK_CHANGE),
        (RouteRequest(change="persistent", risk="low"), WorkRoute.UNIT),
        (RouteRequest(change="local", risk="high"), WorkRoute.UNIT),
        (RouteRequest(change="local", risk="low", ambiguous=True), WorkRoute.UNIT),
        (RouteRequest(change="none", risk="high"), WorkRoute.UNIT),
        (RouteRequest(change="none", risk="low", remote=True), WorkRoute.UNIT),
        (RouteRequest(change="none", risk="low", sensitive=True), WorkRoute.UNIT),
    ],
)
def test_routing_contract(route_request: RouteRequest, expected: WorkRoute) -> None:
    assert classify_work(route_request).route is expected


def test_context_receipt_resolves_project_profiles_rules_and_policies(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)

    receipt = resolve_context(project, WorkRoute.UNIT)

    assert receipt["project_id"] == "test-project"
    assert receipt["foundation_version"] == "0.1.0"
    assert receipt["profiles"] == ["security-profile", "software-delivery-profile"]
    assert "FOUNDATION-001" in receipt["rule_ids"]
    evidence_rule = next(rule for rule in receipt["rules"] if rule["id"] == "EVIDENCE-001")
    assert evidence_rule["level"] == "MUST"
    assert evidence_rule["condition"]["type"] == "required-artifact"
    assert receipt["rules"] == sorted(receipt["rules"], key=lambda rule: rule["id"])
    assert "high-risk-policy" in receipt["policy_ids"]
    assert receipt["receipt_id"].startswith("CTX-")


def test_unit_init_scaffolds_every_verifier_artifact_but_remains_pending(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Golden Path", tmp_path / "project" / "units", "tester")

    present = {
        str(path.relative_to(unit))
        for path in unit.rglob("*")
        if path.is_file()
    }
    assert UNIT_REQUIRED_FILES <= present

    result = verify_unit(unit)
    assert result["missing"] == []
    assert result["valid"] is False
    assert "at least one recorded decision is required" in result["issues"]
    assert "verification evidence is not passing" in result["issues"]


def test_checkpoint_and_resume_restore_authoritative_next_action(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Resume Contract", tmp_path / "project" / "units")

    checkpoint = json.loads((unit / "checkpoint.json").read_text(encoding="utf-8"))
    checkpoint["completed"] = ["scaffold"]
    checkpoint["pending"] = ["record inception decision"]
    checkpoint["next_action"] = "record inception decision"
    (unit / "checkpoint.json").write_text(
        json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8"
    )

    resumed = resume_session(project)

    assert resumed["resume"]["completed"] == ["scaffold"]
    assert resumed["resume"]["pending"] == ["record inception decision"]
    assert resumed["resume"]["next_action"] == "record inception decision"


def test_resume_rejects_foundation_contract_drift_with_same_version(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    initialize_unit(project, "Pinned Foundation", tmp_path / "project" / "units")
    rules_path = project.parent / "foundation/governance/rules/core.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["content"]["rules"][0]["title"] += " changed"
    rules_path.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SessionError, match="Foundation contract digest"):
        resume_session(project)


def test_resume_rejects_unit_bound_to_a_different_project(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = make_project(first_root)
    second = make_project(second_root)
    unit = initialize_unit(first, "First Project Unit", first.parent / "units")

    with pytest.raises(SessionError, match="source_manifest does not match"):
        resume_session(second, unit)


def test_foundation_readiness_reports_approved_baseline() -> None:
    foundation = load_foundation(ROOT / "foundation")

    readiness = foundation.readiness()

    assert readiness["ready"] is True
    assert readiness["summary"]["status"] == "approved"
    assert readiness["evaluations"]["passed"] is True
    assert readiness["blockers"] == []


def test_lifecycle_preflight_blocks_missing_full_rule_context(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Fail Closed", tmp_path / "project" / "units")
    receipt_path = unit / "context-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.pop("rules")
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unit preflight blocked"):
        transition_unit(unit, "inception")

    result = verify_unit(unit)
    assert "Context Receipt missing fields: rules" in result["issues"]


def test_lifecycle_preflight_blocks_ambiguous_scope(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Scoped Work", tmp_path / "project" / "units")
    unit_path = unit / "unit.json"
    unit_value = json.loads(unit_path.read_text(encoding="utf-8"))
    unit_value["scope"] = ""
    unit_path.write_text(json.dumps(unit_value, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Unit preflight blocked"):
        transition_unit(unit, "inception")


def test_example_project_resolves_local_extension_outside_foundation() -> None:
    project = ROOT / "examples/reference-product/project.json"

    receipt = resolve_context(project, WorkRoute.UNIT)

    assert receipt["foundation_id"] == "isekai-foundation"
    assert receipt["extensions"] == ["reference-product-extension"]
    assert receipt["extension_assets"][0]["content"]["namespace"] == "reference-product"
    assert receipt["extension_assets"][0]["source_path"].endswith(
        "examples/reference-product/extension/reference-product.json"
    )


def test_project_local_extension_cannot_escape_project_root(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    manifest_path = project
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["extensions"] = [
        {"id": "reference-product-extension", "path": "../outside.json"}
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match="escapes project root"):
        resolve_context(project, WorkRoute.UNIT)


def test_unit_templates_default_to_korean(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "문서 언어 테스트", tmp_path / "project" / "units")

    intent = (unit / "intent.md").read_text(encoding="utf-8")
    requirements = (unit / "requirements.md").read_text(encoding="utf-8")
    checkpoint = json.loads((unit / "checkpoint.json").read_text(encoding="utf-8"))
    unit_json = json.loads((unit / "unit.json").read_text(encoding="utf-8"))

    assert "## 목표" in intent
    assert "## 기대 결과" in intent
    assert requirements.startswith("# 요구사항")
    assert checkpoint["next_action"] == "의도와 인수 조건을 구체화합니다."
    assert unit_json["document_language"] == "ko"


def test_unit_templates_support_english_override(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    manifest_path = project
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["document_language"] = "en"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    unit = initialize_unit(project, "English template", tmp_path / "project" / "units")

    intent = (unit / "intent.md").read_text(encoding="utf-8")
    requirements = (unit / "requirements.md").read_text(encoding="utf-8")
    checkpoint = json.loads((unit / "checkpoint.json").read_text(encoding="utf-8"))

    assert "## Goal" in intent
    assert requirements.startswith("# Requirements")
    assert checkpoint["next_action"] == "clarify intent and acceptance criteria"


def test_repository_root_project_resolves_local_foundation() -> None:
    context = resolve_context(ROOT / "project.json")

    assert context["project_id"] == "isekai-agent-plugin"
    assert context["source_manifest"] == str((ROOT / "project.json").resolve())
    assert context["profiles"] == ["security-profile", "software-delivery-profile"]


def test_unit_default_and_relative_outputs_are_project_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isekai.session import activate_session

    project = make_project(tmp_path)
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    monkeypatch.chdir(unrelated_cwd)

    default_unit = initialize_unit(project, "Project Relative Default")
    assert default_unit.parent == project.parent / "units"

    activated = activate_session(project)
    assert activated["activation"] == "project"
    assert activated["unit"] is None
    assert activated["active_unit"] is None
    assert activated["unit_candidates"] == [str(default_unit)]

    resumed = resume_session(project)
    assert resumed["unit"]["path"] == str(default_unit)

    relative_unit = initialize_unit(
        project,
        "Project Relative Custom",
        Path("custom-units"),
    )
    assert relative_unit.parent == project.parent / "custom-units"

    absolute_root = tmp_path / "absolute-units"
    absolute_unit = initialize_unit(
        project,
        "Explicit Absolute Output",
        absolute_root,
    )
    assert absolute_unit.parent == absolute_root

    with pytest.raises(ValueError, match="escapes project root"):
        initialize_unit(project, "Traversal Escape", Path("../outside-units"))

    symlink_target = tmp_path / "symlink-target"
    symlink_target.mkdir()
    symlink_output = project.parent / "linked-units"
    symlink_output.symlink_to(symlink_target, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes project root"):
        initialize_unit(project, "Symlink Escape", Path("linked-units"))


def test_unit_default_output_rejects_a_symlink_escape(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    outside = tmp_path / "outside-units"
    outside.mkdir()
    (project.parent / "units").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes project root"):
        initialize_unit(project, "Default Symlink Escape")

    assert list(outside.iterdir()) == []


def test_unit_metadata_is_versionable_but_raw_evidence_is_ignored() -> None:
    ignore_lines = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }

    assert "units/" not in ignore_lines
    assert "units/**/evidence/raw/" in ignore_lines
    assert ".isekai-runtime/" in ignore_lines


def test_verify_reports_missing_json_artifact_without_crashing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Partial Unit", project.parent / "units")
    (unit / "decisions.json").unlink()

    result = verify_unit(unit)

    assert result["valid"] is False
    assert "decisions.json" in result["missing"]
    assert any("decisions.json" in issue for issue in result["issues"])
