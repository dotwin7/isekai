"""Feature proposal normalization and prioritization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


VALID_IMPACTS = {"low", "medium", "high"}
IMPACT_PRIORITY = {"low": 1, "medium": 2, "high": 3}


def normalize_proposal(proposal: Mapping[str, Any]) -> dict[str, str]:
    """Return a validated copy of a FeatureProposal record."""
    proposal_id = str(proposal.get("id", "")).strip()
    title = str(proposal.get("title", "")).strip()
    impact = str(proposal.get("impact", "")).strip().lower()
    if not proposal_id:
        raise ValueError("proposal id is required")
    if not title:
        raise ValueError("proposal title is required")
    if impact not in VALID_IMPACTS:
        raise ValueError(f"unsupported proposal impact: {impact}")
    return {"id": proposal_id, "title": title, "impact": impact}


def prioritize_proposals(
    proposals: Iterable[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Return validated proposals ordered by impact and stable proposal ID."""
    normalized = [normalize_proposal(proposal) for proposal in proposals]
    return sorted(
        normalized,
        key=lambda proposal: (
            -IMPACT_PRIORITY[proposal["impact"]],
            proposal["id"],
        ),
    )
