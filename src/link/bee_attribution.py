"""
BEE Target Attribution: determines whether a BEE phrase refers to the review target product.

Core principle (from instruction doc):
  - Relation의 1차 역할은 semantic이 아니라 attribution이다.
  - BEE signal 승격은 relation-gated 이어야 한다.
  - Unlinked BEE는 evidence-only로 격하, 폐기 금지.

Attribution sources (in priority order):
  1. direct_rel — explicit NER-BEE relation where subject is review target
  2. placeholder_resolved — subject is a placeholder (이거, 이 제품, Review Target) resolved to target
  3. same_entity_resolved — subject resolved to target through same_entity merge
  4. comparison_resolved — target side proven through comparison_with structure
  5. unlinked — no proof of target attribution
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.enums import AttributionSource
from src.common.text_normalize import normalize_text


# Korean proximal demonstratives that refer to the review target product
_TARGET_PLACEHOLDER_PATTERNS = {
    "review target", "it", "this", "itself",
    "이거", "이것", "이 제품", "요 제품", "해당 제품", "본 제품",
}

# Distal / ambiguous forms — do NOT auto-link
_AMBIGUOUS_PLACEHOLDER_PATTERNS = {
    "그거", "그것", "그 제품", "저거", "저것", "저 제품",
}


@dataclass
class BeeAttribution:
    """Attribution result for a single BEE phrase."""
    bee_idx: int                          # Index in bee_rows
    target_linked: bool                   # Whether this BEE is attributed to the target product
    attribution_source: AttributionSource
    attribution_confidence: float = 1.0   # 0.0-1.0, higher = stronger evidence
    matched_rel_idx: int | None = None    # Index of the matching relation (if any)
    match_strategy: str = ""              # offset_match | text_match | synthetic | none
    subject_text: str = ""                # Subject text from the matching relation
    reason: str = ""                      # Human-readable explanation


def attribute_bee_rows(
    bee_rows: list[dict[str, Any]],
    rel_rows: list[dict[str, Any]],
    target_product_name: str | None = None,
    same_entity_pairs: list[dict] | None = None,
) -> list[BeeAttribution]:
    """Determine target attribution for each BEE row.

    For each BEE phrase, checks if it can be attributed to the review target product
    through NER-BEE relations, placeholder resolution, or same_entity merges.

    Args:
        bee_rows: Raw BEE extraction rows
        rel_rows: Raw relation extraction rows
        target_product_name: The review's target product name (for title matching)
        same_entity_pairs: same_entity relation pairs (for resolution)

    Returns:
        List of BeeAttribution, one per BEE row, in same order as bee_rows.
    """
    if not bee_rows:
        return []

    # Build index: NER-BEE relations (where obj is BEE)
    nerbee_rels = _build_nerbee_index(rel_rows)

    # Normalized target product name for title matching
    norm_target = normalize_text(target_product_name) if target_product_name else ""

    # Build same_entity resolution set
    target_entity_texts = _build_target_entity_set(same_entity_pairs, norm_target)

    results: list[BeeAttribution] = []

    for i, bee in enumerate(bee_rows):
        bee_text = bee.get("phrase_text", "") or bee.get("word", "")
        bee_attr = bee.get("bee_attr_raw", "") or bee.get("entity_group", "")
        bee_start = bee.get("start_offset")
        bee_end = bee.get("end_offset")

        # Try to find matching NER-BEE relation
        match = _find_nerbee_match(nerbee_rels, bee_text, bee_attr, bee_start, bee_end)

        if match:
            rel_idx, rel = match
            subj_text = rel.get("subj_text", "")
            norm_subj = normalize_text(subj_text)

            # Check if subject is the target product
            source = _classify_subject(norm_subj, norm_target, target_entity_texts)
            results.append(BeeAttribution(
                bee_idx=i,
                target_linked=(source != AttributionSource.UNLINKED),
                attribution_source=source,
                attribution_confidence=_source_confidence(source),
                matched_rel_idx=rel_idx,
                match_strategy="offset_match" if bee_start is not None else "text_match",
                subject_text=subj_text,
                reason=f"NER-BEE relation subject '{subj_text}' → {source.value}",
            ))
        else:
            # No NER-BEE relation → unlinked (BEE_SYNTHETIC path)
            results.append(BeeAttribution(
                bee_idx=i,
                target_linked=False,
                attribution_source=AttributionSource.UNLINKED,
                attribution_confidence=0.0,
                match_strategy="none",
                reason="No NER-BEE relation found for this BEE phrase",
            ))

    return results


def _build_nerbee_index(rel_rows: list[dict]) -> list[tuple[int, dict]]:
    """Index NER-BEE relations for matching."""
    result = []
    for i, rel in enumerate(rel_rows):
        source_type = rel.get("source_type", "")
        # NER-BEE relations or BEE-type object groups
        if source_type == "NER-BeE" or rel.get("obj_group", "") in (
            "사용감", "효과", "보습력", "제형", "발림성", "충성도", "세정력",
            "밀착력", "향", "색상", "지속력", "편리성", "흡수력", "커버력",
        ):
            result.append((i, rel))
    return result


def _find_nerbee_match(
    nerbee_rels: list[tuple[int, dict]],
    bee_text: str,
    bee_attr: str,
    bee_start: int | None,
    bee_end: int | None,
) -> tuple[int, dict] | None:
    """Find the NER-BEE relation that matches this BEE row."""
    norm_bee = normalize_text(bee_text)

    # Priority 1: Exact offset + text match
    if bee_start is not None and bee_end is not None:
        for idx, rel in nerbee_rels:
            if (rel.get("obj_start") == bee_start and
                rel.get("obj_end") == bee_end and
                normalize_text(rel.get("obj_text", "")) == norm_bee):
                return (idx, rel)

    # Priority 2: Text + attribute match (fallback when offsets missing)
    norm_attr = normalize_text(bee_attr)
    for idx, rel in nerbee_rels:
        if (normalize_text(rel.get("obj_text", "")) == norm_bee and
            normalize_text(rel.get("obj_group", "")) == norm_attr):
            return (idx, rel)

    return None


def _classify_subject(
    norm_subj: str,
    norm_target: str,
    target_entity_texts: set[str],
) -> AttributionSource:
    """Classify the relation subject as target-linked or not."""
    # Direct match to target product name
    if norm_target and norm_subj == norm_target:
        return AttributionSource.DIRECT_REL

    # Proximal placeholder / demonstrative
    if norm_subj in {normalize_text(p) for p in _TARGET_PLACEHOLDER_PATTERNS}:
        return AttributionSource.PLACEHOLDER_RESOLVED

    # Ambiguous placeholder → unlinked
    if norm_subj in {normalize_text(p) for p in _AMBIGUOUS_PLACEHOLDER_PATTERNS}:
        return AttributionSource.UNLINKED

    # Same-entity resolved to target cluster
    if norm_subj in target_entity_texts:
        return AttributionSource.SAME_ENTITY_RESOLVED

    # Subject is a different product → unlinked
    return AttributionSource.UNLINKED


def _build_target_entity_set(
    same_entity_pairs: list[dict] | None,
    norm_target: str,
) -> set[str]:
    """Build set of normalized texts that resolve to the target product via same_entity."""
    if not same_entity_pairs or not norm_target:
        return set()

    # Simple transitive closure: if A same_entity B and A matches target, then B is also target
    adj: dict[str, set[str]] = {}
    for pair in same_entity_pairs:
        a = normalize_text(pair.get("subj_text", ""))
        b = normalize_text(pair.get("obj_text", ""))
        if a and b:
            adj.setdefault(a, set()).add(b)
            adj.setdefault(b, set()).add(a)

    # BFS from target
    target_set = {norm_target}
    # Also add placeholder patterns as seeds
    for p in _TARGET_PLACEHOLDER_PATTERNS:
        np = normalize_text(p)
        if np in adj:
            target_set.add(np)

    visited = set()
    queue = list(target_set)
    while queue:
        current = queue.pop()
        if current in visited:
            continue
        visited.add(current)
        for neighbor in adj.get(current, set()):
            if neighbor not in visited:
                queue.append(neighbor)

    return visited


def _source_confidence(source: AttributionSource) -> float:
    """Return confidence score for an attribution source."""
    return {
        AttributionSource.DIRECT_REL: 1.0,
        AttributionSource.PLACEHOLDER_RESOLVED: 0.9,
        AttributionSource.SAME_ENTITY_RESOLVED: 0.8,
        AttributionSource.COMPARISON_RESOLVED: 0.7,
        AttributionSource.UNLINKED: 0.0,
    }.get(source, 0.0)
