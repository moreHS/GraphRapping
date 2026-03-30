"""
KG Same Entity Merger: Union-Find based entity merging.

Ported from Relation project. Groups same_entity pairs and selects representatives.
"""

from __future__ import annotations

from collections import Counter

from src.kg.models import EntityMention, SameEntityPair


class UnionFind:
    """Disjoint set with path compression and rank-based union."""

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

    def get_groups(self) -> dict[str, set[str]]:
        groups: dict[str, set[str]] = {}
        for x in self._parent:
            root = self.find(x)
            groups.setdefault(root, set()).add(x)
        return groups


# Placeholder merge priority (lower = higher priority)
_PLACEHOLDER_PRIORITY = {"review_target": 1, "reviewer": 2, "pronoun": 3}


class SameEntityMerger:
    """Merges same_entity pairs via Union-Find, selects representatives."""

    def process(
        self,
        entity_mentions: list[EntityMention],
        same_entity_pairs: list[SameEntityPair],
    ) -> dict[str, str]:
        """Returns representative_map: mention_id → representative_mention_id."""
        uf = UnionFind()
        mention_map = {m.mention_id: m for m in entity_mentions}

        # Union same_entity pairs
        for pair in same_entity_pairs:
            uf.union(pair.subj_mention.mention_id, pair.obj_mention.mention_id)

        # Build representative map
        representative_map: dict[str, str] = {}
        groups = uf.get_groups()

        for root_id, member_ids in groups.items():
            members = [mention_map[mid] for mid in member_ids if mid in mention_map]
            if not members:
                continue

            rep = self._determine_representative(members)
            for m in members:
                representative_map[m.mention_id] = rep.mention_id

        # Self-representative for mentions not in any pair
        for m in entity_mentions:
            if m.mention_id not in representative_map:
                representative_map[m.mention_id] = m.mention_id

        return representative_map

    def _determine_representative(self, members: list[EntityMention]) -> EntityMention:
        """Select representative: placeholder priority > most frequent word."""
        # Placeholder members first
        placeholders = [m for m in members if m.is_placeholder and m.placeholder_type]
        if placeholders:
            placeholders.sort(key=lambda m: _PLACEHOLDER_PRIORITY.get(m.placeholder_type, 99))
            return placeholders[0]

        # Most frequent word
        word_counts = Counter(m.word for m in members)
        most_common_word = word_counts.most_common(1)[0][0]
        for m in members:
            if m.word == most_common_word:
                return m

        return members[0]
