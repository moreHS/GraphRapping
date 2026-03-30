"""
KG Canonicalizer: mentions → canonical entities + edges.

Ported from Relation project. Adapted for GraphRapping per-review processing.
Key features: BEE_ATTR sentiment split, KEYWORD entity creation, HAS_KEYWORD edges.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

from src.kg.config import KGConfig
from src.kg.models import (
    EntityMention, RelationMention, KeywordMention,
    KGEntity, KGEdge, KGResult,
)
from src.common.text_normalize import normalize_text


def _hash_id(*parts: str) -> str:
    """Generate 32-char hex ID from parts (SHA256)."""
    joined = "::".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]


class Canonicalizer:
    """Converts mentions → canonical KG entities and edges (per-review)."""

    def __init__(self, config: KGConfig) -> None:
        self._config = config
        self._entities: dict[str, KGEntity] = {}
        self._edges: dict[str, KGEdge] = {}
        self._mention_to_entity: dict[str, str] = {}  # mention_id → entity_id

    def process(
        self,
        entity_mentions: list[EntityMention],
        relation_mentions: list[RelationMention],
        representative_map: dict[str, str],
        keyword_mentions: list[KeywordMention],
        product_id: str | None = None,
        brand_name: str = "",
    ) -> KGResult:
        """Canonicalize all mentions for a single review."""
        mention_map = {m.mention_id: m for m in entity_mentions}

        # 1. Canonicalize entities
        for mention in entity_mentions:
            rep_id = representative_map.get(mention.mention_id, mention.mention_id)
            rep_mention = mention_map.get(rep_id, mention)

            # Skip if this mention is already processed (prevents representative double-processing)
            if mention.mention_id in self._mention_to_entity:
                continue

            # If representative already processed by another member
            if rep_id in self._mention_to_entity:
                self._mention_to_entity[mention.mention_id] = self._mention_to_entity[rep_id]
                continue

            entity = self._create_entity(rep_mention, product_id)
            self._mention_to_entity[rep_id] = entity.entity_id
            self._mention_to_entity[mention.mention_id] = entity.entity_id

        # 2. Canonicalize relations
        for rel in relation_mentions:
            subj_eid = self._mention_to_entity.get(rel.subj_mention_id)
            obj_eid = self._mention_to_entity.get(rel.obj_mention_id)
            if not subj_eid or not obj_eid:
                logger.debug("Drop relation: unmapped mention (subj=%s obj=%s rel=%s)",
                             rel.subj_mention_id[:8], rel.obj_mention_id[:8], rel.relation_type)
                continue

            neo4j_type = self._config.get_neo4j_relation_type(rel.relation_type)
            if neo4j_type is None:
                # Try direct (already canonical type like "has_attribute")
                neo4j_type = rel.relation_type.upper().replace(" ", "_")
                if rel.relation_type == "has_attribute":
                    neo4j_type = "HAS_ATTRIBUTE"

            self._create_edge(subj_eid, obj_eid, neo4j_type, rel.sentiment)

        # 3. Canonicalize keywords → KEYWORD entities + HAS_KEYWORD edges
        for kw in keyword_mentions:
            bee_entity_id = self._mention_to_entity.get(kw.bee_mention_id)
            if not bee_entity_id:
                logger.debug("Drop keyword: unmapped BEE mention %s", kw.bee_mention_id[:8])
                continue

            kw_normalized = normalize_text(kw.word)
            kw_entity_id = _hash_id("KEYWORD", kw_normalized)

            if kw_entity_id not in self._entities:
                self._entities[kw_entity_id] = KGEntity(
                    entity_id=kw_entity_id,
                    entity_type="KEYWORD",
                    normalized_value=kw_normalized,
                    word=kw.word,
                )

            self._create_edge(bee_entity_id, kw_entity_id, "HAS_KEYWORD", "NEU")

        # 4. OFFICIAL_BRAND edge
        if product_id and brand_name:
            self._create_official_brand(product_id, brand_name)

        # Build result
        entity_map = dict(self._entities)
        return KGResult(
            entities=list(self._entities.values()),
            edges=list(self._edges.values()),
            entity_map=entity_map,
        )

    def _create_entity(self, mention: EntityMention, product_id: str | None) -> KGEntity:
        """Create or retrieve a canonical entity from a mention."""
        is_bee = self._config.is_bee_type(mention.original_type or "")

        if mention.is_placeholder and mention.placeholder_type:
            # Placeholder: scoped by product
            norm_value = mention.placeholder_type  # "reviewer" or "review_target"
            entity_type = self._config.get_placeholder_type(mention.placeholder_type)
            scope_key = product_id
            entity_id = _hash_id(entity_type, norm_value, scope_key or "")
            word = norm_value
        elif is_bee:
            # BEE_ATTR: sentiment-split
            bee_type = mention.original_type or mention.word
            polarity = mention.sentiment or "NEU"
            norm_value = f"{bee_type}_{polarity}"
            entity_type = "BEE_ATTR"
            scope_key = None
            entity_id = _hash_id("BEE_ATTR", norm_value)
            word = bee_type
        else:
            # Regular entity
            norm_value = normalize_text(mention.word)
            entity_type = mention.type
            scope_key = None
            entity_id = _hash_id(entity_type, norm_value)
            word = mention.word

        if entity_id in self._entities:
            existing = self._entities[entity_id]
            existing.weight += 1
            if is_bee and mention.word not in existing.original_phrases:
                existing.original_phrases.append(mention.word)
            return existing

        entity = KGEntity(
            entity_id=entity_id,
            entity_type=entity_type,
            normalized_value=norm_value,
            word=word,
            scope_key=scope_key,
            is_placeholder=mention.is_placeholder,
            bee_type=(mention.original_type if is_bee else None),
            polarity=(mention.sentiment if is_bee else None),
            original_phrases=[mention.word] if is_bee else [],
        )
        self._entities[entity_id] = entity
        return entity

    def _create_edge(self, subj_id: str, obj_id: str, rel_type: str, sentiment: str) -> KGEdge:
        edge_id = _hash_id(subj_id, rel_type, obj_id, sentiment)
        if edge_id in self._edges:
            self._edges[edge_id].weight += 1
            return self._edges[edge_id]

        edge = KGEdge(
            edge_id=edge_id,
            subj_entity_id=subj_id,
            obj_entity_id=obj_id,
            relation_type=rel_type,
            sentiment=sentiment,
        )
        self._edges[edge_id] = edge
        return edge

    def _create_official_brand(self, product_id: str, brand_name: str) -> None:
        """Auto-generate OFFICIAL_BRAND edge: review_target → brand."""
        # Find review_target entity
        rt_id = _hash_id("PRD", "review_target", product_id)
        if rt_id not in self._entities:
            return

        # Find/create brand entity
        brand_norm = normalize_text(brand_name)
        brand_id = _hash_id("BRD", brand_norm)
        if brand_id not in self._entities:
            self._entities[brand_id] = KGEntity(
                entity_id=brand_id,
                entity_type="BRD",
                normalized_value=brand_norm,
                word=brand_name,
            )

        self._create_edge(rt_id, brand_id, "OFFICIAL_BRAND", "NEU")
