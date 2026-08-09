from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from isekai.foundation import promotion as foundation_module
from isekai.foundation import (
    FoundationError,
    load_foundation,
    plan_foundation_promotion,
    promote_foundation,
    record_foundation_decision,
    record_foundation_evidence,
)


ROOT = Path(__file__).resolve().parents[1]


def make_foundation(tmp_path: Path) -> Path:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    release_path = foundation / "release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release["status"] = "draft"
    release_path.write_text(json.dumps(release, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for descriptor in release["artifacts"]:
        asset_path = foundation / descriptor["path"]
        asset = json.loads(asset_path.read_text(encoding="utf-8"))
        asset["status"] = "draft"
        if asset.get("kind") == "knowledge":
            for entry in asset.get("content", {}).get("entries", []):
                entry["status"] = "draft"
        asset_path.write_text(json.dumps(asset, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (foundation / "decisions.json").unlink(missing_ok=True)
    (foundation / "evidence/release.json").unlink(missing_ok=True)
    return foundation


def passing_checks() -> list[dict[str, object]]:
    provenance = {
        "source": "pytest-foundation-release",
        "recorded_by": "release-validator",
        "recorded_at": "2026-08-05T00:00:00Z",
    }
    return [
        {
            "id": evaluation_id,
            "passed": True,
            "details": f"Evaluation group {evaluation_id} passed with positive and negative fixtures.",
            "provenance": provenance,
        }
        for evaluation_id in (
            "routing", "gate-evaluation", "release-evaluation", "semantic-evaluation",
            "knowledge-evaluation", "exception-evaluation", "dod-evaluation",
        )
    ]


def approve_and_evidence(foundation: Path) -> None:
    record_foundation_decision(
        foundation,
        outcome="approved",
        summary="Foundation v0.1 release approved after review.",
        decided_by="foundation-owner",
    )
    record_foundation_evidence(
        foundation,
        passed=True,
        checks=passing_checks(),
        scope="Foundation v0.1 release",
        recorded_by="release-validator",
    )


def test_new_foundation_records_disclose_core_trust_boundaries(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    decision = record_foundation_decision(
        foundation,
        outcome="approved",
        summary="Foundation release approved after review.",
        decided_by="foundation-owner",
    )["decision"]
    evidence = record_foundation_evidence(
        foundation,
        passed=True,
        checks=passing_checks(),
        scope="Foundation release",
        recorded_by="release-validator",
    )["evidence"]

    assert decision["attestation"] == {
        "type": "human-decision-attestation",
        "reported_actor": "foundation-owner",
        "identity_verification": "not-performed-by-core",
        "confirmation_source": "caller-attested",
    }
    assert evidence["attestation"] == {
        "type": "local-evaluation-attestation",
        "reported_actor": "release-validator",
        "execution_verification": "not-performed-by-core",
        "identity_verification": "not-performed-by-core",
    }


def replace_with_alias(path: Path, external: Path, alias: str) -> None:
    external.write_bytes(path.read_bytes())
    path.unlink()
    if alias == "symlink":
        path.symlink_to(external)
    else:
        path.hardlink_to(external)


@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
@pytest.mark.parametrize(
    "relative",
    [
        "release.json",
        "governance/policies/high-risk.json",
        "knowledge/software-delivery-review.md",
    ],
)
def test_foundation_registered_files_reject_aliases(
    tmp_path: Path,
    alias: str,
    relative: str,
) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    target = foundation / relative
    external = tmp_path / (target.name + ".external")
    replace_with_alias(target, external, alias)

    with pytest.raises(FoundationError, match="single-link|symlink|unsafe"):
        load_foundation(foundation)


@pytest.mark.parametrize("relative", ["decisions.json", "evidence/release.json"])
@pytest.mark.parametrize("alias", ["symlink", "hardlink"])
def test_foundation_readiness_rejects_aliased_approval_records(
    tmp_path: Path,
    alias: str,
    relative: str,
) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    target = foundation / relative
    external = tmp_path / (target.name + ".approval-external")
    replace_with_alias(target, external, alias)

    readiness = load_foundation(foundation).readiness()

    assert readiness["ready"] is False
    assert any(
        "single-link" in blocker or "symlink" in blocker or "unsafe" in blocker
        for blocker in readiness["blockers"]
    )


def test_foundation_evidence_write_rejects_symlinked_parent(
    tmp_path: Path,
) -> None:
    foundation = make_foundation(tmp_path)
    evidence_dir = foundation / "evidence"
    shutil.rmtree(evidence_dir)
    external = tmp_path / "external-evidence"
    external.mkdir()
    evidence_dir.symlink_to(external, target_is_directory=True)

    with pytest.raises(FoundationError, match="write target|symlink|unsafe"):
        record_foundation_evidence(
            foundation,
            passed=True,
            checks=passing_checks(),
            scope="Foundation v0.1 release",
            recorded_by="release-validator",
        )

    assert not (external / "release.json").exists()


def test_promotion_is_blocked_without_decision_and_evidence(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)

    with pytest.raises(FoundationError, match="cannot be promoted"):
        promote_foundation(foundation)

    assert load_foundation(foundation).manifest["status"] == "draft"
    assert not (foundation / "decisions.json").exists()
    assert not (foundation / "evidence/release.json").exists()


def test_rejected_decision_or_failed_evidence_cannot_promote(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    record_foundation_decision(
        foundation,
        outcome="rejected",
        summary="Release requires more evidence.",
        decided_by="foundation-owner",
    )
    record_foundation_evidence(
        foundation,
        passed=False,
        checks=[
            {
                "id": "gate-evaluation",
                "passed": False,
                "details": "A required evaluation group failed.",
                "provenance": {
                    "source": "pytest-foundation-release",
                    "recorded_by": "release-validator",
                    "recorded_at": "2026-08-05T00:00:00Z",
                },
            }
        ],
        scope="Foundation v0.1 release",
        recorded_by="release-validator",
    )

    with pytest.raises(FoundationError, match="cannot be promoted"):
        promote_foundation(foundation)

    assert load_foundation(foundation).manifest["status"] == "draft"


def test_approved_decision_and_passing_evidence_promote_all_assets(
    tmp_path: Path,
) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)

    result = promote_foundation(foundation)
    promoted = load_foundation(foundation)

    assert result["promoted"] is True
    assert promoted.manifest["status"] == "approved"
    assert all(asset["status"] == "approved" for asset in promoted.assets.values())
    assert all(entry["status"] == "approved" for entry in promoted.knowledge_entries())
    assert promoted.readiness()["ready"] is True

    release = json.loads((foundation / "release.json").read_text(encoding="utf-8"))
    assert release["status"] == "approved"


def test_foundation_mutations_fail_closed_while_the_release_lock_is_held(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from isekai.locking import file_lock as real_file_lock

    foundation = make_foundation(tmp_path)
    lock = foundation / ".isekai-foundation.lock"
    with real_file_lock(lock, subject="test Foundation holder"):
        monkeypatch.setattr(
            foundation_module,
            "file_lock",
            lambda path, *, subject: real_file_lock(
                path, subject=subject, timeout=0
            ),
        )
        with pytest.raises(FoundationError, match="being modified"):
            record_foundation_decision(
                foundation,
                outcome="approved",
                summary="Blocked concurrent decision.",
                decided_by="foundation-owner",
            )
        with pytest.raises(FoundationError, match="being modified"):
            record_foundation_evidence(
                foundation,
                passed=True,
                checks=passing_checks(),
                scope="Blocked concurrent Evidence",
                recorded_by="release-validator",
            )
        with pytest.raises(FoundationError, match="being modified"):
            promote_foundation(foundation)

    assert not (foundation / "decisions.json").exists()
    assert not (foundation / "evidence/release.json").exists()
    assert load_foundation(foundation).manifest["status"] == "draft"


def test_content_change_after_approval_invalidates_promotion(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    policy_path = foundation / "governance/policies/high-risk.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["content"]["reason"] = "Changed after approval"
    policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match="approval_digest"):
        promote_foundation(foundation)

    assert load_foundation(foundation).manifest["status"] == "draft"


def test_content_change_after_promotion_plan_is_revalidated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    original_load = foundation_module.load_foundation
    calls = 0

    def mutate_between_plan_and_commit(root: str | Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            policy_path = foundation / "governance/policies/high-risk.json"
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["content"]["reason"] = "changed after the approved plan"
            policy_path.write_text(json.dumps(policy, indent=2) + "\n", encoding="utf-8")
        return original_load(root)

    monkeypatch.setattr(foundation_module, "load_foundation", mutate_between_plan_and_commit)

    with pytest.raises(FoundationError, match="changed after promotion planning"):
        promote_foundation(foundation)

    assert original_load(foundation).manifest["status"] == "draft"


def test_knowledge_body_change_invalidates_foundation_digests_and_readiness(
    tmp_path: Path,
) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    before = load_foundation(foundation)
    contract_digest = before.contract_digest
    approval_digest = before.approval_digest

    body = foundation / "knowledge/software-delivery-review.md"
    body.write_text("# Replaced without approval\n", encoding="utf-8")

    changed = load_foundation(foundation)
    assert changed.contract_digest != contract_digest
    assert changed.approval_digest != approval_digest
    readiness = changed.readiness()
    assert readiness["ready"] is False
    assert any("approval_digest" in blocker for blocker in readiness["blockers"])


def test_must_rule_without_condition_is_rejected(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    rules_path = foundation / "governance/rules/core.json"
    rules = json.loads(rules_path.read_text(encoding="utf-8"))
    rules["content"]["rules"][0].pop("condition")
    rules_path.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match="MUST rule requires a condition"):
        load_foundation(foundation)


def test_generic_one_check_evidence_cannot_promote(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    record_foundation_decision(
        foundation,
        outcome="approved",
        summary="Approval is present but evaluation evidence is incomplete.",
        decided_by="foundation-owner",
    )
    record_foundation_evidence(
        foundation,
        passed=True,
        checks=[
            {
                "id": "generic-check",
                "passed": True,
                "details": "Malformed generic evidence probe.",
                "provenance": {
                    "source": "pytest-foundation-release",
                    "recorded_by": "release-validator",
                    "recorded_at": "2026-08-05T00:00:00Z",
                },
            }
        ],
        scope="Foundation v0.1 release",
        recorded_by="release-validator",
    )
    with pytest.raises(FoundationError, match="evaluation checks"):
        promote_foundation(foundation)
    assert load_foundation(foundation).manifest["status"] == "draft"


def test_release_metadata_and_approval_provenance_fail_closed(tmp_path: Path) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    release_path = foundation / "release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release.pop("provenance")
    release_path.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match="provenance"):
        load_foundation(foundation)

    shutil.rmtree(foundation)
    shutil.copytree(ROOT / "foundation", foundation)
    decisions_path = foundation / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions["decisions"][-1]["decided_at"] = "not-a-timestamp"
    decisions_path.write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")
    evidence_path = foundation / "evidence/release.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["recorded_at"] = "not-a-timestamp"
    evidence["checks"].append(dict(evidence["checks"][0]))
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    readiness = load_foundation(foundation).readiness()

    assert readiness["ready"] is False
    assert any("decided_at" in blocker for blocker in readiness["blockers"])
    assert any("recorded_at" in blocker for blocker in readiness["blockers"])
    assert any("duplicate check id" in blocker for blocker in readiness["blockers"])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("reviewed_by", "unapproved-reviewer", "reviewer"),
        ("evidence_ref", "missing-evidence", "evidence_ref"),
    ],
)
def test_knowledge_entry_review_must_match_its_promotion_contract(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    foundation = make_foundation(tmp_path)
    catalog_path = foundation / "knowledge/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["content"]["entries"][0]["review"][field] = value
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(FoundationError, match=message):
        load_foundation(foundation)


def test_foundation_evidence_rejects_checks_recorded_after_the_evidence(
    tmp_path: Path,
) -> None:
    foundation = make_foundation(tmp_path)
    checks = passing_checks()
    checks[0]["provenance"] = {
        "source": "future-check",
        "recorded_by": "release-validator",
        "recorded_at": "2099-01-01T00:00:00+00:00",
    }

    with pytest.raises(FoundationError, match="after Evidence recorded_at"):
        record_foundation_evidence(
            foundation,
            passed=True,
            checks=checks,
            scope="Foundation v0.1 release",
            recorded_by="release-validator",
        )

    approved = tmp_path / "approved-foundation"
    shutil.copytree(ROOT / "foundation", approved)
    evidence_path = approved / "evidence/release.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["checks"][0]["provenance"]["recorded_at"] = (
        "2099-01-01T00:00:00+00:00"
    )
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    readiness = load_foundation(approved).readiness()
    assert readiness["ready"] is False
    assert any(
        "after Evidence recorded_at" in blocker for blocker in readiness["blockers"]
    )


def test_foundation_decision_and_evidence_record_digests_detect_edits(
    tmp_path: Path,
) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    decisions_path = foundation / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))

    # Model a legitimate rejected record, then a one-field approval edit that
    # keeps the original digest.
    latest = decisions["decisions"][-1]
    latest["outcome"] = "rejected"
    latest["decision_digest"] = foundation_module._foundation_decision_digest(latest)
    latest["outcome"] = "approved"
    decisions_path.write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")

    readiness = load_foundation(foundation).readiness()
    assert readiness["ready"] is False
    assert any("Decision digest" in blocker for blocker in readiness["blockers"])

    shutil.rmtree(foundation)
    shutil.copytree(ROOT / "foundation", foundation)
    evidence_path = foundation / "evidence/release.json"
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence["passed"] = False
    evidence["evidence_digest"] = foundation_module._foundation_evidence_digest(evidence)
    evidence["passed"] = True
    evidence_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")

    readiness = load_foundation(foundation).readiness()
    assert readiness["ready"] is False
    assert any("Evidence digest" in blocker for blocker in readiness["blockers"])


def test_foundation_readiness_validates_historical_decision_digests(
    tmp_path: Path,
) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    decisions_path = foundation / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions["decisions"][0]["summary"] = "silently replaced history"
    decisions_path.write_text(json.dumps(decisions, indent=2) + "\n", encoding="utf-8")

    readiness = load_foundation(foundation).readiness()
    assert readiness["ready"] is False
    assert any(
        "Foundation Decision 0" in blocker and "digest" in blocker
        for blocker in readiness["blockers"]
    )


def test_only_the_first_v01_decision_may_omit_approval_digest(
    tmp_path: Path,
) -> None:
    foundation = tmp_path / "foundation"
    shutil.copytree(ROOT / "foundation", foundation)
    decisions_path = foundation / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    later = decisions["decisions"][1]
    later.pop("approval_digest")
    later["decision_digest"] = foundation_module._foundation_decision_digest(later)
    decisions_path.write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    readiness = load_foundation(foundation).readiness()

    assert readiness["ready"] is False
    assert any(
        "Foundation Decision 1" in blocker and "approval_digest" in blocker
        for blocker in readiness["blockers"]
    )


def test_foundation_decision_reordering_cannot_restore_an_older_approval(
    tmp_path: Path,
) -> None:
    foundation = make_foundation(tmp_path)
    record_foundation_decision(
        foundation,
        outcome="approved",
        summary="Approve the current release candidate.",
        decided_by="foundation-owner",
    )
    record_foundation_decision(
        foundation,
        outcome="rejected",
        summary="Revoke the earlier approval.",
        decided_by="foundation-owner",
    )
    record_foundation_evidence(
        foundation,
        passed=True,
        checks=passing_checks(),
        scope="Foundation v0.1 release",
        recorded_by="release-validator",
    )
    decisions_path = foundation / "decisions.json"
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    decisions["decisions"][-2:] = reversed(decisions["decisions"][-2:])
    decisions_path.write_text(
        json.dumps(decisions, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FoundationError, match="digest chain|later than"):
        promote_foundation(foundation)

    assert load_foundation(foundation).manifest["status"] == "draft"


def target_snapshot(foundation: Path) -> dict[str, tuple[bytes, int]]:
    release = load_foundation(foundation)
    paths = [foundation / "release.json"] + [
        foundation / descriptor["path"] for descriptor in release.manifest["artifacts"]
    ]
    return {
        str(path): (path.read_bytes(), path.stat().st_mode & 0o7777)
        for path in paths
    }


def test_promotion_plan_is_deterministic_and_contains_release_plus_21_assets(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)

    first = plan_foundation_promotion(foundation)
    second = plan_foundation_promotion(foundation)

    assert first == second
    assert first["target_count"] == 22
    assert len(first["targets"]) == 22
    assert first["targets"][0] == {
        "id": "isekai-foundation",
        "kind": "foundation-release",
        "path": "release.json",
        "version": "0.1.0",
        "from_status": "draft",
        "to_status": "approved",
    }
    assert {target["from_status"] for target in first["targets"]} == {"draft"}
    assert {target["to_status"] for target in first["targets"]} == {"approved"}
    assert all(target["version"] == "0.1.0" for target in first["targets"])


def test_dry_run_reports_plan_and_does_not_mutate_foundation(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    before = target_snapshot(foundation)

    result = promote_foundation(foundation, dry_run=True)

    assert result["promoted"] is False
    assert result["dry_run"] is True
    assert result["target_count"] == 22
    assert result["blockers"] == []
    assert result["plan"] == plan_foundation_promotion(foundation)["targets"]
    assert target_snapshot(foundation) == before


def test_dry_run_reports_blockers_without_mutation(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    before = target_snapshot(foundation)

    result = promote_foundation(foundation, dry_run=True)

    assert result["promoted"] is False
    assert result["dry_run"] is True
    assert "missing Foundation release Decision" in result["blockers"]
    assert result["target_count"] == 22
    assert target_snapshot(foundation) == before


def test_successful_promotion_commits_all_22_files_and_preserves_modes(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    before = target_snapshot(foundation)

    result = promote_foundation(foundation)

    assert result["promoted"] is True
    assert result["target_count"] == 22
    after = target_snapshot(foundation)
    assert all(after[path][0] != original[0] for path, original in before.items())
    assert all(after[path][1] == original[1] for path, original in before.items())
    promoted = load_foundation(foundation)
    assert promoted.manifest["status"] == "approved"
    assert len(promoted.assets) == 21
    assert all(asset["status"] == "approved" for asset in promoted.assets.values())
    assert promoted.readiness()["ready"] is True


def test_already_approved_dry_run_is_a_noop_with_approved_statuses(tmp_path: Path) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    promote_foundation(foundation)
    before = target_snapshot(foundation)

    result = promote_foundation(foundation, dry_run=True)

    assert result["already_approved"] is True
    assert result["blockers"] == []
    assert result["target_count"] == 22
    assert {target["from_status"] for target in result["plan"]} == {"approved"}
    assert target_snapshot(foundation) == before


def test_write_failure_leaves_all_original_bytes_and_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    before = target_snapshot(foundation)
    original = foundation_module._write_staged_json
    calls = 0

    def fail_during_staging(path: Path, content: bytes, mode: int) -> Path:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write failure")
        return original(path, content, mode)

    monkeypatch.setattr(foundation_module, "_write_staged_json", fail_during_staging)
    with pytest.raises(FoundationError, match="injected write failure"):
        promote_foundation(foundation)
    assert target_snapshot(foundation) == before
    assert not list(foundation.rglob("*.promotion-tmp"))


def test_replace_failure_rolls_back_all_original_bytes_and_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    before = target_snapshot(foundation)
    original = foundation_module._replace_staged
    calls = 0

    def replace_then_fail_once(temporary: Path, target: Path) -> None:
        nonlocal calls
        calls += 1
        original(temporary, target)
        if calls == 2:
            raise OSError("injected replace failure")

    monkeypatch.setattr(foundation_module, "_replace_staged", replace_then_fail_once)
    with pytest.raises(FoundationError, match="injected replace failure"):
        promote_foundation(foundation)
    assert target_snapshot(foundation) == before
    assert not list(foundation.rglob("*.promotion-tmp"))


def test_postflight_failure_rolls_back_all_original_bytes_and_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    foundation = make_foundation(tmp_path)
    approve_and_evidence(foundation)
    before = target_snapshot(foundation)

    def fail_postflight(root: Path, expected_count: int) -> dict[str, object]:
        raise FoundationError("injected postflight failure")

    monkeypatch.setattr(foundation_module, "_postflight_promotion", fail_postflight)
    with pytest.raises(FoundationError, match="injected postflight failure"):
        promote_foundation(foundation)
    assert target_snapshot(foundation) == before
    assert not list(foundation.rglob("*.promotion-tmp"))


@pytest.mark.parametrize("mutation", ["unsafe", "duplicate"])
def test_unsafe_or_duplicate_paths_are_preflight_blockers(tmp_path: Path, mutation: str) -> None:
    foundation = make_foundation(tmp_path)
    release_path = foundation / "release.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    if mutation == "unsafe":
        release["artifacts"][0]["path"] = "../outside.json"
    else:
        release["artifacts"][1]["path"] = release["artifacts"][0]["path"]
    release_path.write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    before = release_path.read_bytes()

    result = promote_foundation(foundation, dry_run=True)

    assert result["promoted"] is False
    assert result["target_count"] == 0
    assert result["blockers"]
    assert release_path.read_bytes() == before
