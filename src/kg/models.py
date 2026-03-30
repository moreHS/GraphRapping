"""
KG data models: mention-level and canonical-level representations.

Ported from Relation project's project_3_neo4j/src/models/.
Adapted for GraphRapping per-review processing.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional


# =============================================================================
# Mention Layer (per-review, deduplicated)
# =============================================================================

@dataclass
class EntityMention:
    """A single entity mention extracted from a review."""
    review_id: str
    product_id: str
    type: str                    # PRD, BRD, BEE_ATTR, KEYWORD, PER, CAT, DATE, etc.
    word: str                    # Original surface form
    start: int | None = None     # Character offset (None for meta/placeholder)
    end: int | None = None
    source: str = "ner"          # ner|bee|meta|relation
    is_placeholder: bool = False
    placeholder_type: str | None = None  # reviewer|review_target|pronoun
    original_type: str | None = None     # Raw entity_group before normalization (밀착력 etc.)
    sentiment: str | None = None         # POS|NEG|NEU (BEE_ATTR only)
    mention_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def get_dedup_key(self) -> tuple:
        return (self.review_id, self.type, self.word, self.start, self.end)


@dataclass
class RelationMention:
    """A relation between two entity mentions."""
    review_id: str
    product_id: str
    subj_mention_id: str
    obj_mention_id: str
    relation_type: str           # has_attribute, addresses, used_on, etc.
    sentiment: str = "NEU"       # POS|NEG|NEU
    source_type: str | None = None  # NER-NER|NER-BeE
    rel_mention_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])


@dataclass
class KeywordMention:
    """A keyword extracted from a BEE phrase."""
    review_id: str
    product_id: str
    word: str                    # Keyword text
    bee_attr_type: str           # Original BEE attribute (밀착력, 사용감 etc.)
    bee_mention_id: str          # Link to parent BEE_ATTR EntityMention
    mention_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])

    def get_dedup_key(self) -> tuple:
        return (self.review_id, self.word, self.bee_attr_type)


@dataclass
class SameEntityPair:
    """A pair of mentions identified as the same entity."""
    subj_mention: EntityMention
    obj_mention: EntityMention
    review_id: str


# =============================================================================
# Canonical Layer (normalized, per-review)
# =============================================================================

@dataclass
class KGEntity:
    """A canonical entity in the knowledge graph."""
    entity_id: str               # Internal KG hash
    entity_type: str             # PRD, BRD, BEE_ATTR, KEYWORD, PER, CAT, etc.
    normalized_value: str        # Canonical form
    word: str                    # Representative display word
    weight: int = 1
    scope_key: str | None = None # product_id for placeholders
    is_placeholder: bool = False
    bee_type: str | None = None  # BEE_ATTR only: 밀착력, 보습력 etc.
    polarity: str | None = None  # BEE_ATTR only: POS, NEG, NEU
    original_phrases: list[str] = field(default_factory=list)


@dataclass
class KGEdge:
    """A canonical edge (relation) in the knowledge graph."""
    edge_id: str
    subj_entity_id: str
    obj_entity_id: str
    relation_type: str           # HAS_ATTRIBUTE, HAS_KEYWORD, USED_BY, etc.
    sentiment: str = "NEU"
    weight: int = 1


@dataclass
class KGResult:
    """Output of KG pipeline for a single review."""
    entities: list[KGEntity] = field(default_factory=list)
    edges: list[KGEdge] = field(default_factory=list)
    # Lookup maps for adapter
    entity_map: dict[str, KGEntity] = field(default_factory=dict)  # entity_id → entity
