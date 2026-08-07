from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from isekai import foundation as foundation_module
from isekai.foundation.validation import _parse_timestamp
from isekai.foundation import (
    CONDITION_TYPES,
    FoundationError,
    evaluate_all_evaluations,
    evaluate_condition,
    load_foundation,
)
from isekai.workflow import WorkRoute, resolve_context

from test_core_workflow import make_project


ROOT = Path(__file__).resolve().parents[1]


def test_naive_foundation_timestamp_is_normalized_to_utc() -> None:
    parsed = _parse_timestamp("2026-08-05T00:00:00", "test timestamp")

    assert parsed.tzinfo == timezone.utc


def test_architecture_a_contract_assets_are_independent_versioned_approved_assets() -> None:
    foundation = load_foundation(ROOT / "foundation")
    expected = {
        "agent-execution-contract", "human-gate-contract", "exception-contract",
        "semantic-contract", "knowledge-contract", "unit-dod-evaluation-contract",
    }
    assert expected <= set(foundation.assets)
    matrix = foundation.assets["gate-matrix"]
    assert matrix["status"] == "approved"
    assert matrix["content"]["matrix_version"] == matrix["version"]
    assert {gate["id"] for gate in matrix["content"]["gates"]} == {gate["id"] for gate in foundation.assets["human-gate-contract"]["content"]["gates"]}
    assert all("schema_version" in foundation.assets[item] for item in expected)
    assert all("provenance" in foundation.assets[item] for item in expected)
    assert all(isinstance(rule.get("owner"), str) and rule.get("provenance") for asset in foundation.assets.values() if asset["kind"] == "rule-set" for rule in asset["content"]["rules"])


def test_closed_condition_allowlist_and_cross_reference_fail_closed(tmp_path: Path) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    rules_path = foundation / "governance/rules/core.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["content"]["rules"][0]["condition"]["type"] = "unknown-condition"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    with pytest.raises(FoundationError, match="unsupported condition"):
        load_foundation(foundation)


def test_all_evaluation_cases_execute_and_pass() -> None:
    result = evaluate_all_evaluations(ROOT / "foundation")
    assert result["passed"] is True
    assert {name for name in result["evaluations"]} >= {
        "routing", "gate-evaluation", "release-evaluation", "semantic-evaluation",
        "knowledge-evaluation", "exception-evaluation", "dod-evaluation",
    }
    assert all(case["passed"] for evaluation in result["evaluations"].values() for case in evaluation["cases"])


def test_condition_evaluators_fail_closed_for_decision_exception_and_dod() -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    decision = {"type": "required-decision", "gate": "release", "decision_ref": "release-1", "outcome": "approved", "decided_by": "human", "scope": "foundation", "expires_at": "2026-08-06T00:00:00Z", "id": "release-1"}
    assert evaluate_condition(decision, {"decisions": [{**decision}]}, now=now)
    assert not evaluate_condition(decision, {"decisions": []}, now=now)
    exception = {"type": "required-exception-controls", "rule_ref": "r", "reason": "reason", "owner": "owner", "scope": "unit", "compensating_controls": ["review"], "expires_at": "2026-08-04T00:00:00Z", "review_ref": "review", "decision_ref": "decision"}
    assert not evaluate_condition(exception, {key: exception[key] for key in exception if key != "type"}, now=now)
    dod = {"type": "required-dod", "unit_ref": "unit.json", "required_artifacts": ["architecture.md"], "evaluation_refs": ["dod-evaluation"], "evidence_ref": "evidence/verification.json"}
    assert evaluate_condition(dod, {"unit_ref": "unit.json", "artifacts": ["architecture.md"], "evaluations": ["dod-evaluation"], "evidence_ref": "evidence/verification.json", "evidence_passed": True})
    assert CONDITION_TYPES >= {"required-decision", "required-exception-controls", "required-dod"}


def test_root_and_reference_product_use_same_contract_graph() -> None:
    root_context = resolve_context(ROOT / "project.json", WorkRoute.UNIT)
    reference_context = resolve_context(ROOT / "examples/reference-product/project.json", WorkRoute.UNIT)
    assert root_context["foundation_id"] == reference_context["foundation_id"] == "isekai-foundation"
    assert root_context["foundation_version"] == reference_context["foundation_version"] == "0.1.0"
    assert reference_context["extension_assets"][0]["extends"][0]["version"] == "0.1.0"


def test_release_and_assets_are_approved_with_decision_and_evidence() -> None:
    foundation = load_foundation(ROOT / "foundation")
    assert foundation.manifest["status"] == "approved"
    assert all(asset["status"] == "approved" for asset in foundation.assets.values())
    assert (ROOT / "foundation/decisions.json").is_file()
    assert (ROOT / "foundation/evidence/release.json").is_file()
    readiness = foundation.readiness()
    assert readiness["ready"] is True
    assert readiness["evaluations"]["passed"] is True
    assert readiness["blockers"] == []


def test_non_routing_evaluators_ignore_valid_flag_and_use_subject_contracts(tmp_path: Path) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    path = foundation / "evaluations/semantic.json"
    fixture = json.loads(path.read_text(encoding="utf-8"))
    fixture["content"]["cases"][0]["input"]["valid"] = False
    fixture["content"]["cases"][1]["input"]["valid"] = True
    path.write_text(json.dumps(fixture), encoding="utf-8")
    result = evaluate_all_evaluations(foundation)["evaluations"]["semantic-evaluation"]
    assert result["passed"] is True
    assert [case["actual"] for case in result["cases"]] == ["pass", "fail"]


def test_reviewer_probes_fail_closed_for_envelope_lineage_exception_and_knowledge() -> None:
    now = datetime(2026, 8, 5, tzinfo=timezone.utc)
    envelope = {
        "type": "required-envelope", "envelope_ref": "execution-envelope.json", "action": "edit",
        "target_scope": "approved-unit", "stage": "construction", "expires_at": "2027-08-05T00:00:00Z",
    }
    subject = {
        "action": "edit", "target": "src/main.py", "stage": "construction",
        "envelope_ref": "execution-envelope.json", "target_scope": "approved-unit",
        "envelope": {"status": "approved", "scope": ["src/**"], "stages": [{"name": "construction", "allowed_actions": ["edit"]}], "allowed_actions": ["edit"], "forbidden_actions": ["deploy"], "max_iterations": 2, "expires_at": "2027-08-05T00:00:00Z"},
    }
    assert evaluate_condition(envelope, subject, now=now)
    assert not evaluate_condition(
        envelope,
        {**subject, "envelope_ref": "other-envelope.json"},
        now=now,
    )
    assert not evaluate_condition(
        envelope,
        {**subject, "target_scope": "other-scope"},
        now=now,
    )
    assert not evaluate_condition(envelope, {**subject, "envelope": {**subject["envelope"], "status": "draft"}}, now=now)
    assert not evaluate_condition(envelope, {**subject, "envelope": {**subject["envelope"], "max_iterations": 0}}, now=now)
    assert not evaluate_condition(
        envelope, {**subject, "target": "src_evil/secrets.txt"}, now=now
    )

    lineage = {"type": "required-lineage", "mapping_ref": "mapping@0.1.0", "source_ref": "source", "target_ref": "target", "transformation": "field-map", "raw_reference": "raw"}
    assert evaluate_condition(lineage, {key: lineage[key] for key in lineage if key != "type"})
    assert not evaluate_condition(lineage, {**{key: lineage[key] for key in lineage if key != "type"}, "target_ref": "other"})

    exception = {"type": "required-exception-controls", "rule_ref": "r", "reason": "reason", "owner": "owner", "scope": "bounded", "compensating_controls": ["review"], "expires_at": "2026-08-06T00:00:00Z", "review_ref": "review-1", "decision_ref": "decision-1"}
    exception_subject = {key: exception[key] for key in exception if key != "type"}
    exception_subject.update({"review": {"id": "review-1", "status": "approved"}, "decisions": [{"id": "decision-1", "outcome": "approved", "expires_at": "2026-08-06T00:00:00Z"}]})
    assert evaluate_condition(exception, exception_subject, now=now)
    assert not evaluate_condition(exception, {**exception_subject, "decisions": [{"id": "decision-1", "outcome": "rejected"}]}, now=now)

    promotion = {"type": "required-promotion-review", "entry_ref": "entry-1", "evidence_refs": ["evidence-1"], "reviewed_by": "reviewer", "effective_from": "2026-08-05T00:00:00Z", "expires_at": "2027-08-05T00:00:00Z", "promotion_decision_ref": "promotion-1"}
    promotion_subject = {"entry": {"id": "entry-1", "provenance": {"source": "body.md", "recorded_by": "owner", "recorded_at": "2026-08-05T00:00:00Z"}, "effective_from": promotion["effective_from"], "expires_at": promotion["expires_at"]}, "evidence": [{"id": "evidence-1", "passed": True}], "review": {"entry_ref": "entry-1", "reviewed_by": "reviewer", "duplicate_checked": True, "evidence_ref": "evidence-1"}, "decisions": [{"id": "promotion-1", "gate": "knowledge", "outcome": "approved", "expires_at": "2027-08-05T00:00:00Z"}]}
    assert evaluate_condition(promotion, promotion_subject, now=now)
    assert not evaluate_condition(promotion, {**promotion_subject, "evidence": []}, now=now)
    multiple_evidence = {**promotion, "evidence_refs": ["evidence-1", "evidence-2"]}
    assert not evaluate_condition(multiple_evidence, promotion_subject, now=now)
    assert evaluate_condition(
        multiple_evidence,
        {
            **promotion_subject,
            "evidence": [
                *promotion_subject["evidence"],
                {"id": "evidence-2", "passed": True},
            ],
        },
        now=now,
    )


def test_rule_metadata_requires_complete_provenance_and_applies_to(tmp_path: Path) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    path = foundation / "governance/contracts/exception.json"
    contract = json.loads(path.read_text(encoding="utf-8"))
    rule = contract["content"]["rules"][0]
    rule["provenance"].pop("recorded_at")
    rule.pop("applies_to", None)
    path.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(FoundationError, match="applies_to|recorded_at"):
        load_foundation(foundation)
