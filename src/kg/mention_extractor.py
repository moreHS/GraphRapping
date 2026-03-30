"""
KG Mention Extractor: raw NER/BEE/REL rows → deduplicated mentions.

Ported from Relation project. Adapted for GraphRapping per-review processing.
Key features: position-indexed dedup, NER-BeE → has_attribute, keyword extraction.
"""

from __future__ import annotations

import logging
from typing import Any

from src.kg.config import KGConfig

logger = logging.getLogger(__name__)
from src.kg.models import EntityMention, RelationMention, KeywordMention, SameEntityPair
from src.common.text_normalize import normalize_text


class MentionExtractor:
    """Extracts deduplicated mentions from a single review's NER/BEE/REL rows."""

    def __init__(self, config: KGConfig) -> None:
        self._config = config
        self._mention_index: dict[tuple, EntityMention] = {}
        self._position_index: dict[tuple, EntityMention] = {}
        self._keyword_index: dict[tuple, KeywordMention] = {}
        self.entity_mentions: list[EntityMention] = []
        self.relation_mentions: list[RelationMention] = []
        self.same_entity_pairs: list[SameEntityPair] = []
        self.keyword_mentions: list[KeywordMention] = []

    def extract(
        self,
        review_id: str,
        product_id: str,
        ner_rows: list[dict],
        bee_rows: list[dict],
        rel_rows: list[dict],
        brand_name: str = "",
    ) -> None:
        """Extract all mentions from a single review."""
        # 1. NER mentions
        for ner in ner_rows:
            self._create_or_get_mention(
                review_id, product_id,
                word=ner.get("mention_text", ""),
                entity_group=ner.get("entity_group", ""),
                start=ner.get("start_offset"),
                end=ner.get("end_offset"),
                sentiment=ner.get("raw_sentiment"),
                source="ner",
            )

        # 2. BEE mentions
        for bee in bee_rows:
            self._create_or_get_mention(
                review_id, product_id,
                word=bee.get("phrase_text", ""),
                entity_group=bee.get("bee_attr_raw", ""),
                start=bee.get("start_offset"),
                end=bee.get("end_offset"),
                sentiment=bee.get("raw_sentiment"),
                source="bee",
            )

        # 3. Brand mention (meta)
        if brand_name:
            self._create_or_get_mention(
                review_id, product_id,
                word=brand_name,
                entity_group="BRD",
                start=None, end=None,
                source="meta",
            )

        # 4. Relations
        for rel in rel_rows:
            self._process_relation(review_id, product_id, rel)

        # 5. BEE-only synthetic: for BEE mentions not matched by any NER-BeE relation
        bee_mention_ids_in_rel = {
            rm.obj_mention_id for rm in self.relation_mentions
            if rm.relation_type == "has_attribute"
        }
        # Ensure review_target placeholder exists
        rt = next((m for m in self.entity_mentions if m.placeholder_type == "review_target"), None)
        if not rt:
            rt = self._create_or_get_mention(
                review_id, product_id, word="Review Target", entity_group="PRD",
                start=None, end=None, source="synthetic",
            )
        for mention in list(self.entity_mentions):
            if mention.type == "BEE_ATTR" and mention.mention_id not in bee_mention_ids_in_rel:
                # Synthetic HAS_ATTRIBUTE
                self.relation_mentions.append(RelationMention(
                    review_id=review_id, product_id=product_id,
                    subj_mention_id=rt.mention_id, obj_mention_id=mention.mention_id,
                    relation_type="has_attribute", sentiment="NEU", source_type="BEE-synthetic",
                ))
                # Auto keyword from phrase
                auto_kw = normalize_text(mention.word)[:30]
                if auto_kw:
                    self._process_keywords(
                        review_id, product_id, mention,
                        mention.original_type or "", [auto_kw],
                    )
                logger.debug("Synthetic HAS_ATTRIBUTE for BEE-only mention: %s", mention.word[:30])

    def _create_or_get_mention(
        self,
        review_id: str,
        product_id: str,
        word: str,
        entity_group: str,
        start: int | None,
        end: int | None,
        sentiment: str | None = None,
        source: str = "ner",
    ) -> EntityMention:
        """Create or retrieve a deduplicated entity mention."""
        # Normalize entity type
        normalized_type = self._config.normalize_entity_type(entity_group)
        original_type = entity_group if normalized_type != entity_group else None

        # Check placeholder
        is_placeholder, placeholder_type = self._config.is_placeholder_word(word)
        if is_placeholder:
            normalized_type = self._config.get_placeholder_type(placeholder_type)

        # Normalize sentiment for BEE
        norm_sentiment = None
        if normalized_type == "BEE_ATTR" and sentiment:
            norm_sentiment = self._config.normalize_sentiment(sentiment)

        # Dedup check
        dedup_key = (review_id, normalized_type, word, start, end)
        if dedup_key in self._mention_index:
            return self._mention_index[dedup_key]

        mention = EntityMention(
            review_id=review_id,
            product_id=product_id,
            type=normalized_type,
            word=word,
            start=start,
            end=end,
            source=source,
            is_placeholder=is_placeholder,
            placeholder_type=placeholder_type,
            original_type=original_type or entity_group,
            sentiment=norm_sentiment,
        )

        self._mention_index[dedup_key] = mention
        self.entity_mentions.append(mention)

        # Position index for O(1) lookups
        if start is not None and end is not None:
            pos_key = (review_id, start, end)
            if pos_key not in self._position_index:
                self._position_index[pos_key] = mention

        return mention

    def _process_relation(self, review_id: str, product_id: str, rel_row: dict) -> None:
        """Process a single relation row."""
        # Find/create subject and object mentions
        subj_mention = self._find_or_create_mention(
            review_id, product_id,
            word=rel_row.get("subj_text", ""),
            entity_group=rel_row.get("subj_group", ""),
            start=rel_row.get("subj_start"),
            end=rel_row.get("subj_end"),
            source="relation",
        )
        obj_mention = self._find_or_create_mention(
            review_id, product_id,
            word=rel_row.get("obj_text", ""),
            entity_group=rel_row.get("obj_group", ""),
            start=rel_row.get("obj_start"),
            end=rel_row.get("obj_end"),
            sentiment=rel_row.get("raw_sentiment"),
            source="relation",
        )

        relation_raw = rel_row.get("relation_raw", "")
        source_type = rel_row.get("source_type")

        # same_entity → pair collection, no edge
        if relation_raw.lower() == "same_entity":
            self.same_entity_pairs.append(SameEntityPair(
                subj_mention=subj_mention,
                obj_mention=obj_mention,
                review_id=review_id,
            ))
            return

        # no_relationship → skip
        if relation_raw.lower() == "no_relationship":
            logger.debug("Drop no_relationship: review=%s", review_id)
            return

        # Determine relation type and sentiment
        is_nerbee = source_type == "NER-BeE" or self._config.is_bee_type(rel_row.get("obj_group", ""))

        if is_nerbee:
            # NER-BeE: force has_attribute, sentiment NEU on relation
            final_relation = "has_attribute"
            rel_sentiment = "NEU"
            # BEE sentiment stored on entity mention
            if rel_row.get("raw_sentiment"):
                obj_mention.sentiment = self._config.normalize_sentiment(rel_row["raw_sentiment"])

            # Extract keywords from obj_keywords
            obj_keywords = rel_row.get("obj_keywords", [])
            if obj_keywords:
                self._process_keywords(
                    review_id, product_id,
                    bee_mention=obj_mention,
                    bee_attr_type=rel_row.get("obj_group", ""),
                    keywords=obj_keywords,
                )
            else:
                # Auto fallback: generate keyword from phrase
                phrase = rel_row.get("obj_text", "")
                if phrase and len(phrase) > 1:
                    auto_kw = normalize_text(phrase)[:30]
                    if auto_kw:
                        self._process_keywords(
                            review_id, product_id,
                            bee_mention=obj_mention,
                            bee_attr_type=rel_row.get("obj_group", ""),
                            keywords=[auto_kw],
                        )
        else:
            # NER-NER: use canonical relation type
            neo4j_type = self._config.get_neo4j_relation_type(relation_raw)
            if neo4j_type is None:
                logger.debug("Drop unknown relation: %s review=%s", relation_raw, review_id)
                return
            final_relation = neo4j_type
            rel_sentiment = self._config.normalize_sentiment(rel_row.get("raw_sentiment")) if rel_row.get("raw_sentiment") else "NEU"

        self.relation_mentions.append(RelationMention(
            review_id=review_id,
            product_id=product_id,
            subj_mention_id=subj_mention.mention_id,
            obj_mention_id=obj_mention.mention_id,
            relation_type=final_relation,
            sentiment=rel_sentiment,
            source_type=source_type,
        ))

    def _process_keywords(
        self,
        review_id: str,
        product_id: str,
        bee_mention: EntityMention,
        bee_attr_type: str,
        keywords: list[str],
    ) -> None:
        """Extract keyword mentions from BEE phrase keywords."""
        for kw in keywords:
            if not kw or len(kw.strip()) < 1:
                continue
            dedup_key = (review_id, kw.strip(), bee_attr_type)
            if dedup_key in self._keyword_index:
                continue

            kw_mention = KeywordMention(
                review_id=review_id,
                product_id=product_id,
                word=kw.strip(),
                bee_attr_type=bee_attr_type,
                bee_mention_id=bee_mention.mention_id,
            )
            self._keyword_index[dedup_key] = kw_mention
            self.keyword_mentions.append(kw_mention)

    def _find_or_create_mention(
        self,
        review_id: str,
        product_id: str,
        word: str,
        entity_group: str,
        start: int | None,
        end: int | None,
        sentiment: str | None = None,
        source: str = "relation",
    ) -> EntityMention:
        """Find existing mention by dedup key or position, or create new."""
        normalized_type = self._config.normalize_entity_type(entity_group)

        # Try exact dedup key
        dedup_key = (review_id, normalized_type, word, start, end)
        if dedup_key in self._mention_index:
            return self._mention_index[dedup_key]

        # Try position index
        if start is not None and end is not None:
            pos_key = (review_id, start, end)
            if pos_key in self._position_index:
                return self._position_index[pos_key]

        # Fallback: try (review_id, type, word, None, None) — for BEE↔NER-BeE join
        fallback_key = (review_id, normalized_type, word, None, None)
        if fallback_key in self._mention_index:
            return self._mention_index[fallback_key]

        # Create new
        return self._create_or_get_mention(
            review_id, product_id, word, entity_group,
            start, end, sentiment, source,
        )
