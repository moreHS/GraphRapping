"""
KG Pipeline: per-review knowledge graph construction.

Orchestrates: MentionExtractor → SameEntityMerger → Canonicalizer → KGResult.
No Aggregator (GraphRapping Layer 3 handles aggregation).
"""

from __future__ import annotations

from src.kg.config import KGConfig
from src.kg.models import KGResult
from src.kg.mention_extractor import MentionExtractor
from src.kg.same_entity_merger import SameEntityMerger
from src.kg.canonicalizer import Canonicalizer


class KGPipeline:
    """Per-review KG construction pipeline."""

    def __init__(self, config: KGConfig | None = None) -> None:
        self._config = config or KGConfig()
        if not config:
            self._config.load()

    def process_review(
        self,
        review_id: str,
        product_id: str | None,
        ner_rows: list[dict],
        bee_rows: list[dict],
        rel_rows: list[dict],
        brand_name: str = "",
    ) -> KGResult:
        """Process a single review through the full KG pipeline.

        Returns KGResult with canonical entities and edges.
        """
        pid = product_id or "UNKNOWN"

        # Step 1: Extract mentions (deduplicated)
        extractor = MentionExtractor(self._config)
        extractor.extract(
            review_id=review_id,
            product_id=pid,
            ner_rows=ner_rows,
            bee_rows=bee_rows,
            rel_rows=rel_rows,
            brand_name=brand_name,
        )

        # Step 2: Merge same_entity pairs
        merger = SameEntityMerger()
        representative_map = merger.process(
            extractor.entity_mentions,
            extractor.same_entity_pairs,
        )

        # Step 3: Canonicalize → entities + edges
        canonicalizer = Canonicalizer(self._config)
        result = canonicalizer.process(
            entity_mentions=extractor.entity_mentions,
            relation_mentions=extractor.relation_mentions,
            representative_map=representative_map,
            keyword_mentions=extractor.keyword_mentions,
            product_id=pid,
            brand_name=brand_name,
        )

        return result
