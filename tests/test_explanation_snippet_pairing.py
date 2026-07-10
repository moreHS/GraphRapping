"""
Phase 0/1 fix: snippet ↔ review_id atomic pairing in ExplanationService.

`fact_provenance.review_id` is nullable in the DDL, so a provenance row can
yield a snippet with no review_id. Previously snippets were appended
unconditionally while review_ids were appended only when present, letting two
parallel lists drift out of alignment — a snippet could then be index-matched
to the wrong review_id downstream. These tests pin the atomic pairing:
`snippet_evidence` binds each snippet to its own review_id (or None).
"""

from __future__ import annotations

import asyncio

from src.rec.explainer import ExplanationService, SnippetEvidence
from src.rec.scorer import ScoredProduct


class _MixedReviewIdProvider:
    """Provider whose single fact has two provenance rows: the first snippet
    has NO review_id (nullable), the second HAS one. The old parallel-list
    code would mis-pair review_id 'r_present' onto the first (review_id-less)
    snippet."""

    async def get_signal_evidence(self, signal_id: str) -> list[dict]:
        return [{"signal_id": signal_id, "fact_id": "fact_1", "evidence_rank": 0}]

    async def get_fact_provenance(self, fact_id: str) -> list[dict]:
        return [
            {
                "fact_id": fact_id,
                "snippet": "snippet without review",
                "review_id": None,
                "start_offset": None,
                "end_offset": None,
            },
            {
                "fact_id": fact_id,
                "snippet": "snippet with review",
                "review_id": "r_present",
                "start_offset": None,
                "end_offset": None,
            },
        ]

    async def get_review_snippet(
        self, review_id: str, start=None, end=None
    ) -> str | None:  # pragma: no cover - snippet already present
        return None


def _single_keyword_scored() -> tuple[ScoredProduct, list[str]]:
    scored = ScoredProduct(
        product_id="p1",
        raw_score=0.8,
        shrinked_score=0.75,
        final_score=0.75,
        feature_contributions={"keyword_match": 0.5},
    )
    return scored, ["keyword:moisture"]


def test_snippet_evidence_pairs_each_snippet_with_its_own_review_id() -> None:
    scored, overlap = _single_keyword_scored()
    service = ExplanationService(provenance_provider=_MixedReviewIdProvider())

    result = asyncio.run(
        service.explain_with_provenance(
            scored=scored, overlap_concepts=overlap, signal_ids=["sig_1"],
        )
    )

    path = next(p for p in result.provenance_paths if p.snippet_evidence)
    # Exactly the two provenance snippets, in order, each bound to its own id.
    assert path.snippet_evidence == [
        SnippetEvidence(snippet="snippet without review", review_id=None),
        SnippetEvidence(snippet="snippet with review", review_id="r_present"),
    ]
    # The review_id-less snippet is NOT mis-paired with 'r_present'.
    assert path.snippet_evidence[0].review_id is None
    assert path.snippet_evidence[1].review_id == "r_present"


def test_snippets_property_is_a_view_over_snippet_evidence() -> None:
    scored, overlap = _single_keyword_scored()
    service = ExplanationService(provenance_provider=_MixedReviewIdProvider())

    result = asyncio.run(
        service.explain_with_provenance(
            scored=scored, overlap_concepts=overlap, signal_ids=["sig_1"],
        )
    )

    path = next(p for p in result.provenance_paths if p.snippet_evidence)
    assert path.snippets == ["snippet without review", "snippet with review"]
