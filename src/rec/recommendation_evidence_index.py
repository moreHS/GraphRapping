"""Evidence classification for recommendation candidates.

Product master truth, review-derived graph relations, and purchase behavior are
all first-class evidence families. Source review stats are intentionally absent
from this module: they are trust/tie-break signals, not eligibility evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field


MASTER_TRUTH_TYPES = frozenset({
    "brand",
    "category",
    "catalog_keyword",
    "ingredient",
    "goal_master",
})
# ACTIVE_IN_CATEGORY is intentionally excluded. It is profile context/affinity,
# not explicit preference or product master truth strong enough to qualify a
# recommendation by itself.

REVIEW_GRAPH_TYPES = frozenset({
    "keyword",
    "bee_attr",
    "semantic_keyword",
    "semantic_bee_attr",
    "context",
    "concern",
    "concern_bridge",
    "tool",
    "coused",
    "comparison",
})

REVIEW_GRAPH_WEAK_TYPES = frozenset({
    "weak_semantic_keyword",
    "weak_semantic_bee_attr",
})

PURCHASE_BEHAVIOR_TYPES = frozenset({
    "owned_family",
    "repurchased_family",
    "repurchase_brand",
    "repurchase_category",
    "recent_purchase_brand",
})


@dataclass
class CandidateEligibility:
    """First-class evidence attached to a candidate product."""

    eligible: bool = False
    master_truth_paths: list[str] = field(default_factory=list)
    review_graph_paths: list[str] = field(default_factory=list)
    weak_review_graph_paths: list[str] = field(default_factory=list)
    purchase_paths: list[str] = field(default_factory=list)
    rejection_reasons: list[str] = field(default_factory=list)

    @property
    def eligibility_reasons(self) -> list[str]:
        reasons: list[str] = []
        if self.master_truth_paths:
            reasons.append("PRODUCT_MASTER_TRUTH")
        if self.review_graph_paths:
            reasons.append("REVIEW_GRAPH_RELATION")
        if self.weak_review_graph_paths:
            reasons.append("REVIEW_GRAPH_WEAK_RELATION")
        if self.purchase_paths:
            reasons.append("PURCHASE_BEHAVIOR")
        return reasons

    @property
    def evidence_families(self) -> list[str]:
        return self.eligibility_reasons

    def to_dict(self) -> dict[str, object]:
        return {
            "eligible": self.eligible,
            "eligibility_reasons": self.eligibility_reasons,
            "evidence_families": self.evidence_families,
            "master_truth_paths": self.master_truth_paths,
            "review_graph_paths": self.review_graph_paths,
            "weak_review_graph_paths": self.weak_review_graph_paths,
            "purchase_paths": self.purchase_paths,
            "rejection_reasons": self.rejection_reasons,
        }


def classify_overlap(concept: str) -> str | None:
    """Return the evidence family for an overlap concept string."""
    ctype = concept.split(":", 1)[0] if ":" in concept else concept
    if ctype in MASTER_TRUTH_TYPES:
        return "master_truth"
    if ctype in REVIEW_GRAPH_TYPES:
        return "review_graph"
    if ctype in REVIEW_GRAPH_WEAK_TYPES:
        return "weak_review_graph"
    if ctype in PURCHASE_BEHAVIOR_TYPES:
        return "purchase"
    return None


def build_candidate_eligibility(overlap_concepts: list[str]) -> CandidateEligibility:
    """Classify matched paths and decide whether a candidate is evidence-qualified."""
    eligibility = CandidateEligibility()
    for concept in overlap_concepts:
        family = classify_overlap(concept)
        if family == "master_truth":
            eligibility.master_truth_paths.append(concept)
        elif family == "review_graph":
            eligibility.review_graph_paths.append(concept)
        elif family == "weak_review_graph":
            eligibility.weak_review_graph_paths.append(concept)
        elif family == "purchase":
            eligibility.purchase_paths.append(concept)

    eligibility.eligible = bool(
        eligibility.master_truth_paths
        or eligibility.review_graph_paths
        or eligibility.weak_review_graph_paths
        or eligibility.purchase_paths
    )
    if not eligibility.eligible:
        eligibility.rejection_reasons.append("NO_USER_ALIGNED_EVIDENCE")
    return eligibility
