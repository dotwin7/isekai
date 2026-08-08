from __future__ import annotations

import unittest

from reference_product.proposals import normalize_proposal, prioritize_proposals


class ProposalTests(unittest.TestCase):
    def test_normalizes_a_proposal_without_mutating_input(self) -> None:
        proposal = {"id": " FEAT-2 ", "title": " Search ", "impact": "HIGH"}

        result = normalize_proposal(proposal)

        self.assertEqual(
            result,
            {"id": "FEAT-2", "title": "Search", "impact": "high"},
        )
        self.assertEqual(
            proposal,
            {"id": " FEAT-2 ", "title": " Search ", "impact": "HIGH"},
        )

    def test_prioritizes_by_impact_then_id(self) -> None:
        proposals = [
            {"id": "FEAT-3", "title": "Export", "impact": "low"},
            {"id": "FEAT-2", "title": "Search", "impact": "high"},
            {"id": "FEAT-1", "title": "Login", "impact": "high"},
            {"id": "FEAT-4", "title": "Audit", "impact": "medium"},
        ]

        result = prioritize_proposals(proposals)

        self.assertEqual(
            [proposal["id"] for proposal in result],
            ["FEAT-1", "FEAT-2", "FEAT-4", "FEAT-3"],
        )

    def test_prioritization_does_not_mutate_input(self) -> None:
        proposals = [{"id": " FEAT-1 ", "title": " Login ", "impact": "HIGH"}]

        prioritize_proposals(proposals)

        self.assertEqual(
            proposals,
            [{"id": " FEAT-1 ", "title": " Login ", "impact": "HIGH"}],
        )

    def test_rejects_an_invalid_impact(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported proposal impact"):
            prioritize_proposals(
                [{"id": "FEAT-1", "title": "Login", "impact": "urgent"}]
            )


if __name__ == "__main__":
    unittest.main()
