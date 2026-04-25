"""
Placeholder resolver: Review Target → product, Reviewer/I/my → proxy.

Uses Union-Find for same_entity merging within a single review.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.common.ids import make_mention_iri


# ---------------------------------------------------------------------------
# Union-Find for same_entity merge
# ---------------------------------------------------------------------------

class UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}
        self._rank: dict[str, int] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
            self._rank[x] = 0
        if self._parent[x] != x:
            self._parent[x] = self.find(self._parent[x])
        return self._parent[x]

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx == ry:
            return
        if self._rank[rx] < self._rank[ry]:
            rx, ry = ry, rx
        self._parent[ry] = rx
        if self._rank[rx] == self._rank[ry]:
            self._rank[rx] += 1

    def groups(self) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {}
        for x in self._parent:
            root = self.find(x)
            result.setdefault(root, []).append(x)
        return result


# ---------------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------------

# Mentions that should resolve to the review's target product
PRODUCT_PLACEHOLDERS = {"Review Target", "it", "this", "itself"}
# Mentions that should resolve to the reviewer proxy
REVIEWER_PLACEHOLDERS = {"Reviewer", "I", "my", "me", "myself"}


@dataclass
class ResolvedMention:
    original_text: str
    entity_group: str
    resolved_iri: str
    resolution_type: str  # PRODUCT_TARGET|REVIEWER_PROXY|SAME_ENTITY_MERGE|UNRESOLVED


@dataclass
class PlaceholderResolutionResult:
    resolved_mentions: dict[int, ResolvedMention]  # mention_idx → resolved
    alias_groups: dict[str, list[int]]  # root_mention_idx → [member indices]
    unresolved_count: int = 0


def resolve_placeholders(
    ner_rows: list[dict[str, Any]],
    rel_rows: list[dict[str, Any]],
    review_id: str,
    target_product_iri: str | None,
    reviewer_proxy_iri: str,
) -> PlaceholderResolutionResult:
    """Resolve placeholders and same_entity merges for a single review.

    Args:
        ner_rows: NER mention rows from review ingest
        rel_rows: REL rows (to find same_entity relations)
        review_id: Current review_id
        target_product_iri: Resolved product IRI (None if quarantined)
        reviewer_proxy_iri: Reviewer proxy IRI
    """
    uf = UnionFind()
    resolved: dict[int, ResolvedMention] = {}

    # Build mention text → index mapping
    text_to_indices: dict[str, list[int]] = {}
    for i, ner in enumerate(ner_rows):
        text = ner.get("mention_text", "")
        text_to_indices.setdefault(text, []).append(i)

    # Process same_entity relations first
    for rel in rel_rows:
        if rel.get("relation_raw", "").lower() == "same_entity":
            subj = rel.get("subj_text", "")
            obj = rel.get("obj_text", "")
            if subj and obj:
                uf.union(subj, obj)

    # Resolve each mention
    unresolved_count = 0
    for i, ner in enumerate(ner_rows):
        text = ner.get("mention_text", "")
        group = ner.get("entity_group", "")
        root_text = uf.find(text)

        # Check if root or any group member is a known placeholder
        group_members = _get_group_texts(uf, text)

        if _any_in(group_members, PRODUCT_PLACEHOLDERS) and group in ("PRD", ""):
            iri = target_product_iri or make_mention_iri(review_id, i)
            res_type = "PRODUCT_TARGET" if target_product_iri else "UNRESOLVED"
            if not target_product_iri:
                unresolved_count += 1
        elif _any_in(group_members, REVIEWER_PLACEHOLDERS) and group in ("PER", ""):
            iri = reviewer_proxy_iri
            res_type = "REVIEWER_PROXY"
        elif root_text != text:
            # Merged via same_entity but not a placeholder
            root_indices = text_to_indices.get(root_text, [])
            if root_indices and root_indices[0] in resolved:
                iri = resolved[root_indices[0]].resolved_iri
                res_type = "SAME_ENTITY_MERGE"
            else:
                iri = make_mention_iri(review_id, i)
                res_type = "UNRESOLVED"
                unresolved_count += 1
        else:
            iri = make_mention_iri(review_id, i)
            res_type = "UNRESOLVED"
            unresolved_count += 1

        resolved[i] = ResolvedMention(
            original_text=text,
            entity_group=group,
            resolved_iri=iri,
            resolution_type=res_type,
        )

    # Build alias groups
    alias_groups: dict[str, list[int]] = {}
    for root, members in uf.groups().items():
        root_indices = text_to_indices.get(root, [])
        if root_indices:
            member_indices = []
            for m in members:
                member_indices.extend(text_to_indices.get(m, []))
            if len(member_indices) > 1:
                alias_groups[str(root_indices[0])] = member_indices

    return PlaceholderResolutionResult(
        resolved_mentions=resolved,
        alias_groups=alias_groups,
        unresolved_count=unresolved_count,
    )


def _get_group_texts(uf: UnionFind, text: str) -> set[str]:
    root = uf.find(text)
    result = set()
    for x in uf._parent:
        if uf.find(x) == root:
            result.add(x)
    return result


def _any_in(texts: set[str], targets: set[str]) -> bool:
    lowered = {t.lower() for t in texts}
    return bool(lowered & {t.lower() for t in targets})
