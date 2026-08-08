from __future__ import annotations

import unittest

from reference_product.proposals import normalize_proposal


class NormalizeProposalTests(unittest.TestCase):
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

    def test_rejects_an_incomplete_proposal(self) -> None:
        with self.assertRaisesRegex(ValueError, "proposal title is required"):
            normalize_proposal({"id": "FEAT-2", "impact": "high"})


if __name__ == "__main__":
    unittest.main()
