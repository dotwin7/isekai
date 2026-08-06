from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ALLOWED_STATUSES = {"draft", "approved", "deprecated"}
# Evaluation fixtures are graded against a fixed instant so that a released
# Foundation keeps the same verdict regardless of when it is re-checked.
EVALUATION_CLOCK = datetime(2026, 8, 5, tzinfo=timezone.utc)
FOUNDATION_LOCK_NAME = ".isekai-foundation.lock"
ALLOWED_ASSET_KINDS = {
    "schema", "profile", "extension", "rule-set", "policy", "semantic-mapping", "knowledge", "evaluation",
    "gate-matrix", "agent-execution-contract", "human-gate-contract", "exception-contract",
    "semantic-contract", "knowledge-contract", "unit-dod-evaluation-contract",
}
CONDITION_TYPES = {
    "extension-cannot-weaken-must", "required-artifact", "context-scope",
    "required-decision", "required-envelope", "required-lineage",
    "required-promotion-review", "required-exception-controls", "required-dod",
}
EVALUATOR_TYPES = {
    "required-decision", "required-envelope", "required-lineage",
    "required-promotion-review", "required-exception-controls", "required-dod",
}
REQUIRED_ASSET_FIELDS = {
    "id", "kind", "version", "schema_version", "status", "owner", "provenance",
    "classification", "scope", "content",
}
FOUNDATION_DECISION_FIELDS = {
    "id",
    "type",
    "schema_version",
    "foundation_id",
    "version",
    "approval_digest",
    "outcome",
    "summary",
    "decided_by",
    "decided_at",
}
FOUNDATION_EVIDENCE_FIELDS = {
    "id",
    "type",
    "schema_version",
    "foundation_id",
    "version",
    "approval_digest",
    "passed",
    "scope",
    "recorded_by",
    "recorded_at",
    "checks",
}
FOUNDATION_CHECK_FIELDS = {"id", "passed", "details", "provenance"}


class FoundationError(ValueError):
    """Raised when a Foundation release violates its structural contract."""


@dataclass(frozen=True)
class FoundationRelease:
    root: Path
    manifest: dict[str, Any]
    assets: dict[str, dict[str, Any]]

    @property
    def version(self) -> str:
        return str(self.manifest["version"])

    def assets_by_kind(self, kind: str) -> list[dict[str, Any]]:
        return [asset for asset in self.assets.values() if asset["kind"] == kind]

    @property
    def contract_digest(self) -> str:
        """Identify the immutable release manifest and every registered contract asset."""
        digest = hashlib.sha256()
        paths = [Path("release.json")]
        paths.extend(
            Path(str(descriptor["path"]))
            for descriptor in self.manifest.get("artifacts", [])
        )
        for relative in sorted(paths, key=lambda item: item.as_posix()):
            content = (self.root / relative).read_bytes()
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(len(content)).encode("ascii"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
        return "sha256:" + digest.hexdigest()

    @property
    def approval_digest(self) -> str:
        """Bind approval to semantic release content while ignoring promotion status."""
        digest = hashlib.sha256()
        paths = [Path("release.json")]
        paths.extend(
            Path(str(descriptor["path"]))
            for descriptor in self.manifest.get("artifacts", [])
        )
        for relative in sorted(paths, key=lambda item: item.as_posix()):
            try:
                value = json.loads((self.root / relative).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:  # pragma: no cover - load already validates
                raise FoundationError(
                    f"cannot calculate Foundation approval digest for {relative}: {exc}"
                ) from exc
            if not isinstance(value, dict):  # pragma: no cover - load already validates
                raise FoundationError(
                    f"Foundation approval digest requires an object: {relative}"
                )
            subject = copy.deepcopy(value)
            subject.pop("status", None)
            content = json.dumps(
                subject,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
        return "sha256:" + digest.hexdigest()

    def rules(self) -> Iterator[dict[str, Any]]:
        for asset in self.assets_by_kind("rule-set"):
            yield from asset["content"].get("rules", [])

    def summary(self) -> dict[str, Any]:
        kinds: dict[str, int] = {}
        for asset in self.assets.values():
            kind = str(asset["kind"])
            kinds[kind] = kinds.get(kind, 0) + 1
        return {
            "id": self.manifest["id"],
            "version": self.version,
            "contract_digest": self.contract_digest,
            "approval_digest": self.approval_digest,
            "status": self.manifest["status"],
            "asset_count": len(self.assets),
            "kinds": dict(sorted(kinds.items())),
        }

    def readiness(self) -> dict[str, Any]:
        blockers: list[str] = []
        if self.manifest["status"] != "approved":
            blockers.append(
                f"Foundation release status is {self.manifest['status']}; human approval is required"
            )
        for asset in sorted(self.assets.values(), key=lambda item: item["id"]):
            if asset["status"] != "approved":
                blockers.append(
                    f"asset {asset['id']} status is {asset['status']}; human approval is required"
                )
        from .evaluation import evaluate_all_evaluations
        from .promotion import _approval_blockers

        # Grade the evaluation matrix once and reuse it; approval blockers report
        # the same failures, so the results must not be recomputed per consumer.
        evaluations = evaluate_all_evaluations(self)
        blockers.extend(_approval_blockers(self, evaluations=evaluations))
        return {
            "ready": not blockers,
            "summary": self.summary(),
            "evaluations": evaluations,
            "blockers": list(dict.fromkeys(blockers)),
        }
