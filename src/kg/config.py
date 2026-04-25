"""
KG Config loader: entity_types + relation_types from JSON configs.

Ported from Relation project's ConfigLoader, simplified for GraphRapping.
"""

from __future__ import annotations

import logging

from src.common.config_loader import load_json

logger = logging.getLogger(__name__)


# Sentiment mapping (Korean → canonical)
SENTIMENT_MAP = {"긍정": "POS", "부정": "NEG", "중립": "NEU", "혼합": "MIXED"}


class KGConfig:
    """Configuration for KG pipeline: entity types, relation types, BEE detection."""

    def __init__(self) -> None:
        self._bee_types: set[str] = set()
        self._type_aliases: dict[str, str] = {}
        self._neo4j_labels: dict[str, str] = {}  # code → neo4j_label
        self._relation_neo4j: dict[str, str] = {}  # code → neo4j_type
        self._placeholder_types: dict[str, str] = {"reviewer": "PER", "review_target": "PRD"}

    def load(
        self,
        entity_types_file: str = "kg_entity_types.json",
        relation_types_file: str = "kg_relation_types.json",
    ) -> None:
        # Entity types
        et_data = load_json(entity_types_file)
        for t in et_data.get("types", []):
            code = t.get("code", "")
            self._neo4j_labels[code] = t.get("neo4j_label", code)
            if t.get("is_bee"):
                self._bee_types.add(code)

        aliases = et_data.get("type_aliases", {})
        for k, v in aliases.items():
            if k != "_comment":
                self._type_aliases[k] = v

        # Relation types
        rt_data = load_json(relation_types_file)
        for t in rt_data.get("types", []):
            code = t.get("code", "")
            neo4j_type = t.get("neo4j_type")
            if neo4j_type:
                self._relation_neo4j[code] = neo4j_type

        # Post-load validation
        if not self._bee_types:
            logger.warning("KGConfig: no BEE types loaded — BEE detection will fail")
        if not self._relation_neo4j:
            logger.warning("KGConfig: no relation types loaded — all relations will be dropped")
        logger.info("KGConfig loaded: %d BEE types, %d relation types, %d aliases",
                     len(self._bee_types), len(self._relation_neo4j), len(self._type_aliases))

    def is_bee_type(self, entity_group: str) -> bool:
        """Check if an entity_group is a BEE attribute type."""
        return entity_group in self._bee_types

    def normalize_entity_type(self, raw_type: str) -> str:
        """Normalize entity type: apply aliases (B-PRD → PRD), BEE → BEE_ATTR."""
        # Check alias first
        normalized = self._type_aliases.get(raw_type, raw_type)
        # BEE types → BEE_ATTR
        if normalized in self._bee_types:
            return "BEE_ATTR"
        return normalized

    def get_neo4j_label(self, entity_type: str) -> str:
        return self._neo4j_labels.get(entity_type, entity_type)

    def get_neo4j_relation_type(self, relation_code: str) -> str | None:
        """Get Neo4j relation type. Returns None for dropped relations (same_entity, no_relationship)."""
        return self._relation_neo4j.get(relation_code)

    def normalize_sentiment(self, raw_sentiment: str | None) -> str:
        if not raw_sentiment:
            return "NEU"
        return SENTIMENT_MAP.get(raw_sentiment.strip(), raw_sentiment.upper() if raw_sentiment else "NEU")

    def is_placeholder_word(self, word: str) -> tuple[bool, str | None]:
        """Check if word is a placeholder (reviewer, review_target, etc.)."""
        w = word.strip().lower()
        if w in ("reviewer", "i", "my", "me", "myself"):
            return True, "reviewer"
        if w in ("review target", "it", "this", "itself"):
            return True, "review_target"
        return False, None

    def get_placeholder_type(self, placeholder_type: str) -> str:
        return self._placeholder_types.get(placeholder_type, "PER")

    @property
    def bee_types(self) -> set[str]:
        return self._bee_types
