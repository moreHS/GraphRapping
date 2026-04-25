"""
Explainer: score-faithful explanation paths.

Explanations come from the same matched concept paths used in scoring,
NOT from a separate LLM-only narrative pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from src.common.config_loader import get_texture_surface_to_keyword
from src.rec.scorer import ScoredProduct


class ProvenanceProvider(Protocol):
    """Protocol for provenance data access (DB or in-memory)."""
    async def get_signal_evidence(self, signal_id: str) -> list[dict]: ...
    async def get_fact_provenance(self, fact_id: str) -> list[dict]: ...
    async def get_review_snippet(self, review_id: str, start: int | None, end: int | None) -> str | None: ...


@dataclass
class ExplanationPath:
    concept_type: str    # keyword|bee_attr|concern|context|brand|category|goal|ingredient
    concept_id: str
    user_edge: str       # PREFERS_KEYWORD|HAS_CONCERN|...
    product_edge: str    # HAS_BEE_KEYWORD_SIGNAL|ADDRESSES_CONCERN_SIGNAL|...
    contribution: float


@dataclass
class Explanation:
    product_id: str
    paths: list[ExplanationPath]
    summary_ko: str = ""


# Mapping: concept_type → (user_edge_type, product_edge_type)
_EDGE_MAP = {
    "keyword": ("PREFERS_KEYWORD", "HAS_BEE_KEYWORD_SIGNAL"),
    "bee_attr": ("PREFERS_BEE_ATTR", "HAS_BEE_ATTR_SIGNAL"),
    "concern": ("HAS_CONCERN", "ADDRESSES_CONCERN_SIGNAL"),
    "concern_bridge": ("HAS_CONCERN", "HAS_BEE_ATTR_SIGNAL"),
    "context": ("PREFERS_CONTEXT", "USED_IN_CONTEXT_SIGNAL"),
    "brand": ("PREFERS_BRAND", "HAS_BRAND"),
    "category": ("PREFERS_CATEGORY", "IN_CATEGORY"),
    "goal_master": ("WANTS_GOAL", "HAS_MAIN_BENEFIT"),
    "ingredient": ("PREFERS_INGREDIENT", "HAS_INGREDIENT"),
    "tool": ("PREFERS_TOOL", "USED_WITH_TOOL_SIGNAL"),
    "coused": ("OWNS_PRODUCT", "USED_WITH_PRODUCT_SIGNAL"),
    "owned_family": ("OWNS_FAMILY", "HAS_VARIANT_FAMILY"),
    "repurchased_family": ("REPURCHASES_FAMILY", "HAS_VARIANT_FAMILY"),
}


def explain(
    scored: ScoredProduct,
    overlap_concepts: list[str],
    top_n: int = 5,
) -> Explanation:
    """Generate score-faithful explanation from feature contributions.

    Only includes concepts that actually contributed to the score.
    """
    paths: list[ExplanationPath] = []

    # Map back to specific concepts
    for concept_str in overlap_concepts:
        if ":" not in concept_str:
            continue
        ctype, cid = concept_str.split(":", 1)
        if ctype == "catalog_validation":
            continue
        edges = _EDGE_MAP.get(ctype)
        if not edges:
            continue

        # Find contribution for this concept type
        feature_key = _concept_to_feature(ctype)
        contribution = scored.feature_contributions.get(feature_key, 0.0)
        if contribution != 0:
            paths.append(ExplanationPath(
                concept_type=ctype,
                concept_id=cid,
                user_edge=edges[0],
                product_edge=edges[1],
                contribution=contribution,
            ))

    # Sort by contribution and take top-N
    paths.sort(key=lambda p: abs(p.contribution), reverse=True)
    paths = paths[:top_n]

    # Generate summary
    summary = _generate_summary_ko(paths)

    return Explanation(
        product_id=scored.product_id,
        paths=paths,
        summary_ko=summary,
    )


def _concept_to_feature(concept_type: str) -> str:
    mapping = {
        "keyword": "keyword_match",
        "bee_attr": "residual_bee_attr_match",
        "concern": "concern_fit",
        "concern_bridge": "concern_bridge_fit",
        "context": "context_match",
        "brand": "brand_match_conf_weighted",
        "category": "category_affinity",
        "goal_master": "goal_fit_master",
        "ingredient": "ingredient_match",
        "tool": "tool_alignment",
        "coused": "coused_product_bonus",
        "owned_family": "owned_family_penalty",
        "repurchased_family": "repurchase_family_affinity",
    }
    return mapping.get(concept_type, "")


def _get_texture_keywords() -> set[str]:
    return set(get_texture_surface_to_keyword().values())


def _get_texture_keyword_ko() -> dict[str, str]:
    s2k = get_texture_surface_to_keyword()
    reverse: dict[str, str] = {}
    for surface, keyword in s2k.items():
        if keyword not in reverse or len(surface) > len(reverse[keyword]):
            reverse[keyword] = surface
    return reverse


def _generate_summary_ko(paths: list[ExplanationPath]) -> str:
    if not paths:
        return ""

    parts = []
    for p in paths[:3]:
        if p.concept_type == "keyword":
            if p.concept_id in _get_texture_keywords():
                ko = _get_texture_keyword_ko().get(p.concept_id, p.concept_id)
                parts.append(f"제형 선호 '{ko}' 계열과 일치")
            else:
                parts.append(f"'{p.concept_id}' 키워드 선호와 일치")
        elif p.concept_type == "concern":
            from src.common.concept_resolver import concern_label
            label = concern_label(p.concept_id)
            parts.append(f"'{label}' 고민 대응 신호 보유")
        elif p.concept_type == "concern_bridge":
            from src.common.concept_resolver import concern_label
            label = concern_label(p.concept_id)
            parts.append(f"BEE 속성 기반 '{label}' 대응 추정")
        elif p.concept_type == "context":
            parts.append(f"'{p.concept_id}' 사용 맥락과 일치")
        elif p.concept_type == "brand":
            parts.append(f"선호 브랜드 '{p.concept_id}' 일치")
        elif p.concept_type == "bee_attr":
            if "formulation" in p.concept_id.lower() or "texture" in p.concept_id.lower():
                parts.append("제형 축 선호와 일치")
            else:
                parts.append(f"'{p.concept_id}' 속성 선호와 일치")
        elif p.concept_type == "goal_master":
            parts.append(f"'{p.concept_id}' 케어 목표와 부합")
        elif p.concept_type == "tool":
            parts.append(f"'{p.concept_id}' 도구와 함께 사용 패턴")
        elif p.concept_type == "coused":
            parts.append(f"'{p.concept_id}' 제품과 함께 사용 패턴")
        elif p.concept_type == "owned_family":
            parts.append("현재 사용 중인 제품과 같은 라인")
        elif p.concept_type == "repurchased_family":
            parts.append("같은 패밀리 재구매 성향")
        else:
            parts.append(f"'{p.concept_id}' 일치")

    return " / ".join(parts)


# ---------------------------------------------------------------------------
# ExplanationService: DB-backed provenance explanation
# ---------------------------------------------------------------------------

@dataclass
class ProvenanceExplanationPath(ExplanationPath):
    """Extended path with actual evidence from DB."""
    snippets: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    review_ids: list[str] = field(default_factory=list)


@dataclass
class ProvenanceExplanation(Explanation):
    """Explanation with full provenance chain from DB."""
    provenance_paths: list[ProvenanceExplanationPath] = field(default_factory=list)


class ExplanationService:
    """DB-backed explanation service with full provenance chain.

    Primary path: signal → signal_evidence → fact_provenance → raw snippet.
    Fallback: overlap-based explanation (when no DB available).
    """

    def __init__(self, provenance_provider: ProvenanceProvider | None = None) -> None:
        self._provider = provenance_provider

    async def explain_with_provenance(
        self,
        scored: ScoredProduct,
        overlap_concepts: list[str],
        signal_ids: list[str] | None = None,
        top_n: int = 5,
    ) -> ProvenanceExplanation:
        """Generate explanation with DB-backed provenance.

        Falls back to overlap-based explanation if no provider or no signal_ids.
        """
        # Always generate base explanation first
        base = explain(scored, overlap_concepts, top_n)

        if not self._provider or not signal_ids:
            return ProvenanceExplanation(
                product_id=base.product_id,
                paths=base.paths,
                summary_ko=base.summary_ko,
            )

        # Enrich with provenance from DB
        provenance_paths: list[ProvenanceExplanationPath] = []
        for path in base.paths:
            enriched = ProvenanceExplanationPath(
                concept_type=path.concept_type,
                concept_id=path.concept_id,
                user_edge=path.user_edge,
                product_edge=path.product_edge,
                contribution=path.contribution,
            )

            # Find matching signals for this concept
            for sid in signal_ids:
                evidence_rows = await self._provider.get_signal_evidence(sid)
                for ev in evidence_rows[:3]:  # top-3 evidence per signal
                    enriched.fact_ids.append(ev["fact_id"])
                    prov_rows = await self._provider.get_fact_provenance(ev["fact_id"])
                    for prov in prov_rows[:2]:  # top-2 provenance per fact
                        snippet = prov.get("snippet")
                        if not snippet and prov.get("review_id"):
                            snippet = await self._provider.get_review_snippet(
                                prov["review_id"],
                                prov.get("start_offset"),
                                prov.get("end_offset"),
                            )
                        if snippet:
                            enriched.snippets.append(snippet)
                        if prov.get("review_id"):
                            enriched.review_ids.append(prov["review_id"])

            provenance_paths.append(enriched)

        # Rebuild summary with snippets if available
        summary = base.summary_ko
        if provenance_paths and any(p.snippets for p in provenance_paths):
            top_snippets = []
            for p in provenance_paths[:3]:
                if p.snippets:
                    top_snippets.append(f'"{p.snippets[0][:50]}"')
            if top_snippets:
                summary += " | 근거: " + ", ".join(top_snippets)

        return ProvenanceExplanation(
            product_id=base.product_id,
            paths=base.paths,
            summary_ko=summary,
            provenance_paths=provenance_paths,
        )
