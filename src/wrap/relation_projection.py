"""
Relation projection: canonical fact → wrapped signal(s) via registry.

Thin wrapper that coordinates BEE-specific logic (product linkage for HAS_KEYWORD)
with the generic signal emitter.
"""

from __future__ import annotations

from src.canonical.canonical_fact_builder import CanonicalFact
from src.wrap.signal_emitter import SignalEmitter


def project_bee_keyword_signals(
    emitter: SignalEmitter,
    keyword_fact: CanonicalFact,
    product_iri: str,
    target_product_id: str | None = None,
    window_ts: str | None = None,
) -> str | None:
    """Special handling for HAS_KEYWORD facts.

    HAS_KEYWORD is BEEAttr→Keyword, but the serving signal needs
    Product→Keyword linkage. This function creates the product-linked signal.
    """
    return emitter.emit_from_fact(
        fact=CanonicalFact(
            fact_id=keyword_fact.fact_id,
            review_id=keyword_fact.review_id,
            subject_iri=product_iri,
            predicate="HAS_KEYWORD",
            object_iri=keyword_fact.object_iri,
            object_ref_kind=keyword_fact.object_ref_kind,
            subject_type="Product",
            object_type="Keyword",
            polarity=keyword_fact.polarity,
            source_modalities=keyword_fact.source_modalities,
        ),
        target_product_id=target_product_id,
        keyword_id=keyword_fact.object_iri,
        bee_attr_id=keyword_fact.subject_iri,
        window_ts=window_ts,
    )
