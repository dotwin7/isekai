from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from isekai.foundation import FoundationError, load_foundation
from isekai.session import SessionError, migrate_unit_context, resume_session
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
        "maximum_agent_level": "L1",
    }
    (project_root / "project.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return project_root / "project.json"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", ["not", "a", "string"]),
        ("version", {"major": 1}),
        ("foundation_path", ["foundation"]),
        ("profiles", [{"id": "security-profile"}]),
    ],
)
def test_project_manifest_rejects_non_string_contract_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    project = make_project(tmp_path)
    manifest = json.loads(project.read_text(encoding="utf-8"))
    manifest[field] = value
    project.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match=f"project .*{field}"):
        resolve_context(project)


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


def test_context_receipt_includes_applicable_project_extension_rules(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    extension_path = project.parent / "extension/reference-product.json"
    extension = json.loads(extension_path.read_text(encoding="utf-8"))
    extension["content"]["rules"] = [
        {
            "id": "PROJECT-EVIDENCE-001",
            "level": "MUST",
            "owner": "reference-product-owner",
            "provenance": {
                "source": "reference-product",
                "recorded_by": "reference-product-owner",
                "recorded_at": "2026-08-05T00:00:00Z",
            },
            "applies_to": ["unit"],
            "condition": {
                "type": "extension-cannot-weaken-must",
                "parent_asset": "core-rules",
                "parent_rule_id": "EVIDENCE-001",
                "parent_level": "MUST",
                "comparison": "preserve-or-strengthen",
            },
        }
    ]
    extension_path.write_text(
        json.dumps(extension, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    receipt = resolve_context(project, WorkRoute.UNIT)

    assert "PROJECT-EVIDENCE-001" in receipt["rule_ids"]
    applied = next(
        rule for rule in receipt["rules"] if rule["id"] == "PROJECT-EVIDENCE-001"
    )
    assert applied["condition"]["parent_rule_id"] == "EVIDENCE-001"


@pytest.mark.parametrize("field", ["profiles", "extensions"])
def test_project_rejects_duplicate_profile_and_extension_references(
    tmp_path: Path,
    field: str,
) -> None:
    project = make_project(tmp_path)
    manifest = json.loads(project.read_text(encoding="utf-8"))
    manifest[field].append(manifest[field][0])
    project.write_text(json.dumps(manifest) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match="duplicates|duplicate IDs"):
        resolve_context(project)


def test_project_extension_cannot_shadow_a_foundation_rule_id(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    extension_path = project.parent / "extension/reference-product.json"
    extension = json.loads(extension_path.read_text(encoding="utf-8"))
    extension["content"]["rules"] = [
        {
            "id": "EVIDENCE-001",
            "level": "MUST",
            "owner": "reference-product-owner",
            "provenance": {
                "source": "reference-product",
                "recorded_by": "reference-product-owner",
                "recorded_at": "2026-08-05T00:00:00Z",
            },
            "applies_to": ["unit"],
            "condition": {
                "type": "extension-cannot-weaken-must",
                "parent_asset": "core-rules",
                "parent_rule_id": "EVIDENCE-001",
                "parent_level": "MUST",
                "comparison": "preserve-or-strengthen",
            },
        }
    ]
    extension_path.write_text(json.dumps(extension) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match="duplicate applied rule id"):
        resolve_context(project)


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


def test_resume_does_not_advertise_optional_symlink_artifacts(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Resume Alias", project.parent / "units")
    external = tmp_path / "external-note.md"
    external.write_text("external\n", encoding="utf-8")
    (unit / "optional-note.md").symlink_to(external)

    resumed = resume_session(project)

    assert "optional-note.md" not in resumed["resume"]["artifact_references"]


@pytest.mark.parametrize("alias_level", ["root", "child"])
def test_default_unit_discovery_ignores_symlinked_external_units(
    tmp_path: Path,
    alias_level: str,
) -> None:
    project = make_project(tmp_path)
    external = initialize_unit(project, "External Resume", tmp_path / "external-units")
    units_root = project.parent / "units"
    if alias_level == "root":
        units_root.symlink_to(external.parent, target_is_directory=True)
        alias = units_root / external.name
    else:
        units_root.mkdir()
        alias = units_root / "external-alias"
        alias.symlink_to(external, target_is_directory=True)

    with pytest.raises(SessionError, match="no Unit is available"):
        resume_session(project)

    explicit = resume_session(project, alias)
    assert explicit["unit"]["path"] == str(external)


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


def test_resume_rejects_project_contract_drift(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Project Contract Drift", project.parent / "units")
    manifest = json.loads(project.read_text(encoding="utf-8"))
    manifest["version"] = "0.2.0"
    manifest["profiles"] = ["software-delivery-profile"]
    project.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SessionError, match="Project fields: profiles, project_version"):
        resume_session(project, unit)


def test_verify_rejects_a_tampered_context_receipt_fingerprint(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Receipt Tamper", project.parent / "units")
    receipt_path = unit / "context-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["rules"] = receipt["rules"][:1]
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    result = verify_unit(unit)

    assert "Context Receipt receipt_id does not match its bound context" in result["issues"]


def test_resume_rejects_unit_bound_to_a_different_project(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = make_project(first_root)
    second = make_project(second_root)
    unit = initialize_unit(first, "First Project Unit", first.parent / "units")

    second_manifest = json.loads(second.read_text(encoding="utf-8"))
    second_manifest["id"] = "different-project"
    second.write_text(json.dumps(second_manifest, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SessionError, match="project_id does not match"):
        resume_session(second, unit)


def test_portable_context_receipt_survives_project_move(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Portable Unit", project.parent / "units")
    receipt = json.loads((unit / "context-receipt.json").read_text(encoding="utf-8"))

    assert receipt["source_manifest_base"] == "unit"
    assert receipt["source_manifest"] == "../../project.json"
    assert receipt["extension_assets"][0]["source_path"] == (
        "extension/reference-product.json"
    )

    unit_name = unit.name
    moved_root = tmp_path / "moved-project"
    project.parent.rename(moved_root)
    moved_project = moved_root / "project.json"
    moved_unit = moved_root / "units" / unit_name

    resumed = resume_session(moved_project, moved_unit)

    assert resumed["unit"]["path"] == str(moved_unit)
    assert resumed["project"]["manifest"] == str(moved_project)


def test_unit_context_migration_rebinds_legacy_paths_only(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Legacy Location", project.parent / "units")
    receipt_path = unit / "context-receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["source_manifest"] = str(project)
    receipt.pop("source_manifest_base")
    for extension in receipt["extension_assets"]:
        extension["source_path"] = str(project.parent / extension["source_path"])
    from isekai.workflow.project import _context_receipt_id

    receipt["receipt_id"] = _context_receipt_id(receipt)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    unit_name = unit.name
    moved_root = tmp_path / "migrated-project"
    project.parent.rename(moved_root)
    moved_project = moved_root / "project.json"
    moved_unit = moved_root / "units" / unit_name
    with pytest.raises(SessionError, match="source_manifest does not match"):
        resume_session(moved_project, moved_unit)

    migration = migrate_unit_context(moved_project, moved_unit)

    assert migration["migrated"] is True
    assert migration["source_manifest"] == "../../project.json"
    assert migration["source_manifest_base"] == "unit"
    assert resume_session(moved_project, moved_unit)["unit"]["path"] == str(moved_unit)


def test_context_comparison_ignores_only_generated_extension_locator() -> None:
    from isekai.workflow.project import _context_contract_value

    first = [
        {
            "id": "extension",
            "source_path": "/first/location.json",
            "content": {"source_path": "semantic-source-a"},
        }
    ]
    moved = [
        {
            "id": "extension",
            "source_path": "/second/location.json",
            "content": {"source_path": "semantic-source-a"},
        }
    ]
    changed = [
        {
            "id": "extension",
            "source_path": "/second/location.json",
            "content": {"source_path": "semantic-source-b"},
        }
    ]

    assert _context_contract_value("extension_assets", first) == (
        _context_contract_value("extension_assets", moved)
    )
    assert _context_contract_value("extension_assets", first) != (
        _context_contract_value("extension_assets", changed)
    )


def test_unit_context_migration_rejects_project_contract_changes(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "Contract Change", project.parent / "units")
    before = (unit / "context-receipt.json").read_bytes()
    manifest = json.loads(project.read_text(encoding="utf-8"))
    manifest["version"] = "0.2.0"
    project.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(SessionError, match="project_version"):
        migrate_unit_context(project, unit)

    assert (unit / "context-receipt.json").read_bytes() == before


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


def test_unit_machine_identity_is_ascii_and_separate_from_korean_title(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "기능 제안 우선순위 결정")
    unit_json = json.loads((unit / "unit.json").read_text(encoding="utf-8"))

    assert unit_json["title"] == "기능 제안 우선순위 결정"
    assert re.fullmatch(r"UNIT-\d{8}-[A-F0-9]{32}", unit_json["id"])
    assert unit.name == unit_json["id"].lower()
    assert unit.name.isascii()


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


def test_verify_rejects_english_human_document_in_korean_unit(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    unit = initialize_unit(project, "문서 언어 검증", tmp_path / "project" / "units")
    (unit / "requirements.md").write_text(
        "# Requirements\n\nEnglish-only requirements.\n",
        encoding="utf-8",
    )

    result = verify_unit(unit)

    assert "requirements.md must use the ko document heading" in result["issues"]
    assert "requirements.md must contain Korean human-facing content" in result[
        "issues"
    ]


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
    assert activated["unit_candidate_details"] == [
        {
            "path": str(default_unit),
            "unit_id": json.loads(
                (default_unit / "unit.json").read_text(encoding="utf-8")
            )["id"],
            "title": "Project Relative Default",
            "document_language": "ko",
            "status": "proposed",
            "issue": None,
        }
    ]

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
