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
    concept_type: str    # keyword|bee_attr|concern|context|brand|category|active_category|goal|ingredient
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
    "semantic_keyword": ("PREFERS_KEYWORD", "HAS_BEE_KEYWORD_SIGNAL"),
    "semantic_bee_attr": ("PREFERS_BEE_ATTR", "HAS_BEE_ATTR_SIGNAL"),
    "weak_semantic_keyword": ("PREFERS_KEYWORD", "HAS_WEAK_BEE_KEYWORD_SIGNAL"),
    "weak_semantic_bee_attr": ("PREFERS_BEE_ATTR", "HAS_WEAK_BEE_ATTR_SIGNAL"),
    "concern": ("HAS_CONCERN", "ADDRESSES_CONCERN_SIGNAL"),
    "concern_bridge": ("HAS_CONCERN", "HAS_BEE_ATTR_SIGNAL"),
    "context": ("PREFERS_CONTEXT", "USED_IN_CONTEXT_SIGNAL"),
    "catalog_keyword": ("PREFERS_KEYWORD", "HAS_CATALOG_KEYWORD"),
    "brand": ("PREFERS_BRAND", "HAS_BRAND"),
    "category": ("PREFERS_CATEGORY", "IN_CATEGORY"),
    "active_category": ("ACTIVE_IN_CATEGORY", "IN_CATEGORY"),
    "goal_master": ("WANTS_GOAL", "HAS_MAIN_BENEFIT"),
    "ingredient": ("PREFERS_INGREDIENT", "HAS_INGREDIENT"),
    "tool": ("PREFERS_TOOL", "USED_WITH_TOOL_SIGNAL"),
    "coused": ("OWNS_PRODUCT", "USED_WITH_PRODUCT_SIGNAL"),
    "comparison": ("OWNS_PRODUCT", "COMPARED_WITH_SIGNAL"),
    "collab": ("SIMILAR_USER_AFFINITY", "PREFERRED_BY_SIMILAR_USERS"),
    "comention": ("OWNS_PRODUCT", "CO_MENTIONED_WITH_SIGNAL"),
    "owned_family": ("OWNS_FAMILY", "HAS_VARIANT_FAMILY"),
    "repurchased_family": ("REPURCHASES_FAMILY", "HAS_VARIANT_FAMILY"),
    "repurchase_brand": ("REPURCHASES_BRAND", "HAS_BRAND"),
    "repurchase_category": ("REPURCHASES_CATEGORY", "IN_CATEGORY"),
    "recent_purchase_brand": ("RECENTLY_PURCHASED", "HAS_BRAND"),
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
        cid, meta = _split_concept_metadata(cid)
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
                user_edge=meta.get("user_edge") or edges[0],
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
        "semantic_keyword": "keyword_match",
        "semantic_bee_attr": "residual_bee_attr_match",
        "weak_semantic_keyword": "review_graph_weak_relation_match",
        "weak_semantic_bee_attr": "review_graph_weak_relation_match",
        "concern": "concern_fit",
        "concern_bridge": "concern_bridge_fit",
        "context": "context_match",
        "catalog_keyword": "catalog_keyword_match",
        "brand": "brand_match_conf_weighted",
        "category": "category_affinity",
        "active_category": "active_category_affinity",
        "goal_master": "goal_fit_master",
        "ingredient": "ingredient_match",
        "tool": "tool_alignment",
        "coused": "coused_product_bonus",
        "comparison": "comparison_alternative",
        "collab": "collaborative_affinity",
        "comention": "comention_product_bonus",
        "owned_family": "owned_family_penalty",
        "repurchased_family": "repurchase_family_affinity",
        "repurchase_brand": "purchase_loyalty_score",
        "repurchase_category": "repurchase_category_affinity",
        "recent_purchase_brand": "purchase_loyalty_score",
    }
    return mapping.get(concept_type, "")


def _split_concept_metadata(value: str) -> tuple[str, dict[str, str]]:
    parts = value.split("|")
    concept_id = parts[0]
    meta: dict[str, str] = {}
    for part in parts[1:]:
        if "=" not in part:
            continue
        key, raw_value = part.split("=", 1)
        if key:
            meta[key] = raw_value
    return concept_id, meta


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
        elif p.concept_type == "semantic_keyword":
            parts.append(f"리뷰 키워드 신호 '{p.concept_id}'와 의미적으로 일치")
        elif p.concept_type == "semantic_bee_attr":
            parts.append(f"리뷰 BEE 속성 '{p.concept_id}'와 의미적으로 일치")
        elif p.concept_type == "weak_semantic_keyword":
            parts.append(f"약한 리뷰 키워드 신호 '{p.concept_id}'와 의미적으로 일치")
        elif p.concept_type == "weak_semantic_bee_attr":
            parts.append(f"약한 리뷰 BEE 속성 '{p.concept_id}'와 의미적으로 일치")
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
        elif p.concept_type == "catalog_keyword":
            parts.append(f"상품명/카테고리의 '{p.concept_id}'와 선호 키워드 일치")
        elif p.concept_type == "brand":
            parts.append(f"선호 브랜드 '{p.concept_id}' 일치")
        elif p.concept_type == "active_category":
            parts.append(f"활동 카테고리 '{p.concept_id}'와 일치")
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
        elif p.concept_type == "comparison":
            parts.append(f"보유하신 '{p.concept_id}' 제품과 비교되는 상품")
        elif p.concept_type == "collab":
            parts.append("취향이 비슷한 고객들이 선호한 상품")
        elif p.concept_type == "comention":
            parts.append(f"보유하신 '{p.concept_id}' 제품과 리뷰에서 함께 언급되는 상품")
        elif p.concept_type == "owned_family":
            parts.append("현재 사용 중인 제품과 같은 라인")
        elif p.concept_type == "repurchased_family":
            parts.append("같은 패밀리 재구매 성향")
        elif p.concept_type == "repurchase_brand":
            parts.append("반복 구매 브랜드 성향과 일치")
        elif p.concept_type == "repurchase_category":
            parts.append(f"반복 구매 카테고리 '{p.concept_id}'와 일치")
        elif p.concept_type == "recent_purchase_brand":
            parts.append("최근 구매 브랜드 성향과 일치")
        else:
            parts.append(f"'{p.concept_id}' 일치")

    return " / ".join(parts)


# ---------------------------------------------------------------------------
# ExplanationService: DB-backed provenance explanation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SnippetEvidence:
    """A review snippet paired atomically with its originating review_id.

    `review_id` is optional because `fact_provenance.review_id` is nullable in
    the DDL — a snippet can come from a provenance row that carries no review
    linkage. Carrying the pair together (rather than in two parallel lists)
    guarantees a snippet is never index-aligned to the wrong review_id when
    some snippets lack one.
    """
    snippet: str
    review_id: str | None


@dataclass
class ProvenanceExplanationPath(ExplanationPath):
    """Extended path with actual evidence from DB.

    Snippets are stored in `snippet_evidence` where each snippet is bound to
    its own review_id (or None). `snippets` is a read-only convenience view for
    callers that only need the texts.
    """
    snippet_evidence: list[SnippetEvidence] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)

    @property
    def snippets(self) -> list[str]:
        """Snippet texts only (order matches `snippet_evidence`)."""
        return [ev.snippet for ev in self.snippet_evidence]


@dataclass
class ProvenanceExplanation(Explanation):
    """Explanation with full provenance chain from DB."""
    provenance_paths: list[ProvenanceExplanationPath] = field(default_factory=list)


# Provenance snippet limits (Phase 0.4): keep explanations compact and faithful.
_MAX_SNIPPETS_PER_PATH = 2
_SNIPPET_MAX_CHARS = 120


def _truncate_snippet(text: str, limit: int = _SNIPPET_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class ExplanationService:
    """Explanation service with full provenance chain.

    Primary path: signal → signal_evidence → fact_provenance → raw snippet
    (review_text fallback when a provenance row has no stored snippet/offsets).
    Fallback: overlap-based explanation (when no provider available).

    Provider-agnostic: works with `DBProvenanceProvider` (async DB) or
    `InMemoryProvenanceProvider` (demo pipeline artifacts) — both satisfy the
    `ProvenanceProvider` Protocol.
    """

    def __init__(self, provenance_provider: ProvenanceProvider | None = None) -> None:
        self._provider = provenance_provider

    async def explain_with_provenance(
        self,
        scored: ScoredProduct,
        overlap_concepts: list[str],
        signal_ids: list[str] | None = None,
        top_n: int = 5,
        *,
        signal_ids_by_concept: dict[int, list[str]] | None = None,
    ) -> ProvenanceExplanation:
        """Generate explanation with provider-backed provenance.

        Falls back to overlap-based explanation if no provider, or if neither
        `signal_ids` nor `signal_ids_by_concept` is supplied.

        Args:
            signal_ids: legacy flat list applied to every path (backward compat).
            signal_ids_by_concept: per-path signal ids keyed by path index
                (produced by `signal_ids_by_concept_path`). When given, each path
                is enriched ONLY with its own concept's signals, preserving
                provenance integrity (no unrelated review leaks onto a path).
                Takes precedence over `signal_ids` for path enrichment.
        """
        # Always generate base explanation first
        base = explain(scored, overlap_concepts, top_n)

        if not self._provider or (not signal_ids and not signal_ids_by_concept):
            return ProvenanceExplanation(
                product_id=base.product_id,
                paths=base.paths,
                summary_ko=base.summary_ko,
            )

        # Enrich with provenance
        provenance_paths: list[ProvenanceExplanationPath] = []
        for idx, path in enumerate(base.paths):
            enriched = ProvenanceExplanationPath(
                concept_type=path.concept_type,
                concept_id=path.concept_id,
                user_edge=path.user_edge,
                product_edge=path.product_edge,
                contribution=path.contribution,
            )

            # Path-scoped signals (provenance integrity) or legacy flat list.
            if signal_ids_by_concept is not None:
                path_signal_ids = signal_ids_by_concept.get(idx, [])
            else:
                path_signal_ids = signal_ids or []

            for sid in path_signal_ids:
                if len(enriched.snippet_evidence) >= _MAX_SNIPPETS_PER_PATH:
                    break
                evidence_rows = await self._provider.get_signal_evidence(sid)
                for ev in evidence_rows[:3]:  # top-3 evidence per signal
                    if len(enriched.snippet_evidence) >= _MAX_SNIPPETS_PER_PATH:
                        break
                    if ev["fact_id"] not in enriched.fact_ids:
                        enriched.fact_ids.append(ev["fact_id"])
                    prov_rows = await self._provider.get_fact_provenance(ev["fact_id"])
                    for prov in prov_rows[:2]:  # top-2 provenance per fact
                        if len(enriched.snippet_evidence) >= _MAX_SNIPPETS_PER_PATH:
                            break
                        snippet = prov.get("snippet")
                        if not snippet and prov.get("review_id"):
                            snippet = await self._provider.get_review_snippet(
                                prov["review_id"],
                                prov.get("start_offset"),
                                prov.get("end_offset"),
                            )
                        if snippet:
                            # Bind snippet ↔ review_id atomically. review_id is
                            # explicitly None when the provenance row has none,
                            # so consumers never mis-pair via list indexing.
                            enriched.snippet_evidence.append(SnippetEvidence(
                                snippet=_truncate_snippet(snippet),
                                review_id=prov.get("review_id") or None,
                            ))

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
