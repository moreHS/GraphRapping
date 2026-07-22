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
    # Phase 6 B2: a query ingredient family matched a product's
    # representative_product_name (the product-name axis; overlap concept
    # ``product_name:<관용어>``). The product name is catalog master truth, so a
    # name-only ingredient carrier is evidence-qualified (not dropped by the
    # "overlap ≥ 1" / evidence gate). See db_consumer_contract.md §13.2.
    "product_name",
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
})

REVIEW_GRAPH_WEAK_TYPES = frozenset({
    "weak_semantic_keyword",
    "weak_semantic_bee_attr",
})

# Boost-only evidence: user-aligned but too weak to qualify a candidate on its
# own (evidence-first discipline). Boost-only paths NEVER set `eligible` in
# STRICT/EXPLORE. This bucket is the shared extension point for weak signals.
#
#   `comparison` ("this candidate is compared-with a product you own") — a weak
#     "alternative" signal; being compared against is not, by itself, a reason
#     to recommend. A mode may opt it in (COMPARE admits comparison neighbors)
#     via build_candidate_eligibility(..., boost_only_qualifies=True).
#   `collab` (Phase 7 D1 — "users with taste similar to yours preferred this
#     product") — a collaborative-affinity signal derived from user-user
#     similarity (src/rec/user_similarity.py). It NEVER qualifies a candidate on
#     its own in ANY mode (see BOOST_ONLY_ADMISSIBLE_TYPES): "similar users like
#     it" must always ride on first-class evidence, never substitute for it.
#   `comention` (Phase 7 D2 — "this product is mentioned together with a
#     product you own, across reviews") — a product-product co-mention signal
#     derived from review co-occurrence (src/mart/product_comention.py). Like
#     `collab`, it NEVER qualifies a candidate on its own in ANY mode: being
#     talked about alongside something you own is relatedness, not a
#     stand-alone reason to recommend.
#   `similar` (Phase 8 G4 — "this candidate shares attribute nodes with a
#     product you own") — an ungated product-product shared-node similarity
#     projection (src/rec/product_similarity.py; evidence-family name
#     PRODUCT_SIMILARITY_AFFINITY, db_consumer_contract.md §13). Like
#     `collab`/`comention`, it NEVER qualifies a candidate on its own in ANY
#     mode: sharing attributes with something you own is relatedness, not a
#     stand-alone reason to recommend.
#
# 4-type-common rule (unified 2026-07-18): EVERY boost-only type above is
# excluded from the retrieval ``overlap_score`` aggregate (candidate_generator),
# so no boost-only signal can buy a place in the max_candidates retrieval cut —
# it can only re-score candidates already retrieved on first-class evidence.
# (Formerly only ``similar`` was excluded; the asymmetry is gone. See
# DECISIONS/2026-07-16_phase8_g4_similar_boost.md §4 + 2026-07-18 addendum.)
#
# NOTE (terminology): this is a recommendation *evidence family* concept
# (frozenset of overlap-concept prefixes), distinct from SignalFamily — the
# product-signal enum in src/common/enums.py.
BOOST_ONLY_TYPES = frozenset({
    "comparison",
    "collab",
    "comention",
    "similar",
})

# Of the boost-only types, only these may be *admitted* as eligibility-buying
# when a mode opts in (build_candidate_eligibility(boost_only_qualifies=True)).
# `comparison` is admitted by COMPARE mode. `collab`, `comention` and `similar`
# are intentionally absent: all three are pure boosts that must always be
# accompanied by first-class evidence in every mode (D1/D2/G4 contract: "cannot
# qualify alone"), so they never appear here and thus never buy eligibility.
BOOST_ONLY_ADMISSIBLE_TYPES = frozenset({
    "comparison",
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
    # Boost-only paths (e.g. comparison). Reported for explainability/scoring but
    # deliberately NOT part of `eligibility_reasons`/`evidence_families`: they do
    # not, on their own, make a candidate eligible (see BOOST_ONLY_TYPES). A mode
    # may still admit them via `boost_only_qualifies` (COMPARE).
    boost_only_paths: list[str] = field(default_factory=list)
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
            "boost_only_paths": self.boost_only_paths,
            "rejection_reasons": self.rejection_reasons,
        }


def _overlap_type(concept: str) -> str:
    """Return the type prefix of an overlap concept string (part before ':')."""
    return concept.split(":", 1)[0] if ":" in concept else concept


def classify_overlap(concept: str) -> str | None:
    """Return the evidence family for an overlap concept string."""
    ctype = _overlap_type(concept)
    if ctype in MASTER_TRUTH_TYPES:
        return "master_truth"
    if ctype in REVIEW_GRAPH_TYPES:
        return "review_graph"
    if ctype in REVIEW_GRAPH_WEAK_TYPES:
        return "weak_review_graph"
    if ctype in PURCHASE_BEHAVIOR_TYPES:
        return "purchase"
    if ctype in BOOST_ONLY_TYPES:
        return "boost_only"
    return None


def build_candidate_eligibility(
    overlap_concepts: list[str],
    *,
    boost_only_qualifies: bool = False,
) -> CandidateEligibility:
    """Classify matched paths and decide whether a candidate is evidence-qualified.

    Boost-only paths do not qualify a candidate on their own. Only *admissible*
    boost-only paths (``BOOST_ONLY_ADMISSIBLE_TYPES``, currently ``comparison``)
    can contribute to eligibility, and only when ``boost_only_qualifies`` is True
    (COMPARE mode admits comparison neighbors). Non-admissible boost-only paths
    (``collab``/``comention``/``similar``) are recorded for scoring/explainability
    but NEVER buy eligibility in any mode, keeping the evidence-first contract
    intact.
    """
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
        elif family == "boost_only":
            eligibility.boost_only_paths.append(concept)

    admissible_boost_paths = [
        concept
        for concept in eligibility.boost_only_paths
        if _overlap_type(concept) in BOOST_ONLY_ADMISSIBLE_TYPES
    ]
    eligibility.eligible = bool(
        eligibility.master_truth_paths
        or eligibility.review_graph_paths
        or eligibility.weak_review_graph_paths
        or eligibility.purchase_paths
        or (boost_only_qualifies and admissible_boost_paths)
    )
    if not eligibility.eligible:
        eligibility.rejection_reasons.append("NO_USER_ALIGNED_EVIDENCE")
    return eligibility
