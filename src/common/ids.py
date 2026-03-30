"""
Deterministic ID generation for GraphRapping.

IRI strategy is entity_type-specific — no single global rule.
All IDs are deterministic (no random UUIDs) to enable idempotent upsert.
"""

from __future__ import annotations

import hashlib


def _md5(*parts: str) -> str:
    joined = "|".join(parts)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Review / ReviewerProxy
# ---------------------------------------------------------------------------

def make_review_id(
    source: str,
    source_review_key: str | None = None,
    *,
    brand_name_raw: str = "",
    product_name_raw: str = "",
    review_text: str = "",
    collected_at: str = "",
    source_row_num: str = "",
) -> str:
    """Generate deterministic review_id.

    Priority:
      1. source + source_review_key (stable)
      2. source + md5(brand|product|text|collected_at|source_row_num) (fallback)
    """
    if source_review_key:
        return f"review:{source}:{source_review_key}"
    fallback_hash = _md5(brand_name_raw, product_name_raw, review_text, collected_at, source_row_num)
    return f"review:{source}:{fallback_hash}"


def make_reviewer_proxy_id(
    source: str,
    author_key: str | None = None,
    *,
    review_id: str = "",
) -> tuple[str, str]:
    """Generate deterministic reviewer_proxy_id.

    Returns:
        (reviewer_proxy_id, identity_stability)
        identity_stability: 'STABLE' if author_key exists, 'REVIEW_LOCAL' otherwise
    """
    if author_key:
        return f"reviewer_proxy:{source}:{author_key}", "STABLE"
    return f"reviewer_proxy:{review_id}", "REVIEW_LOCAL"


# ---------------------------------------------------------------------------
# Product / Concept / Entity IRIs
# ---------------------------------------------------------------------------

def make_product_iri(product_id: str) -> str:
    return f"product:{product_id}"


def make_concept_iri(concept_type: str, concept_id: str) -> str:
    return f"concept:{concept_type}:{concept_id}"


def make_mention_iri(review_id: str, mention_idx: int) -> str:
    """Review-local unresolved mention IRI. Used before merge/canonicalization."""
    return f"mention:{review_id}:{mention_idx}"


# ---------------------------------------------------------------------------
# Canonical Fact
# ---------------------------------------------------------------------------

def make_qualifier_fingerprint(qualifiers: list[tuple[str, str]] | None) -> str:
    """Generate deterministic fingerprint from sorted qualifier key-value pairs.

    Args:
        qualifiers: list of (qualifier_key, qualifier_value) pairs, or None
    """
    if not qualifiers:
        return ""
    sorted_pairs = sorted(qualifiers, key=lambda x: x[0])
    joined = "|".join(f"{k}:{v}" for k, v in sorted_pairs)
    return _md5(joined)


def make_fact_id(
    review_id: str,
    subject_iri: str,
    predicate: str,
    object_ref: str,
    polarity: str = "",
    qualifier_fingerprint: str = "",
) -> str:
    """Generate deterministic fact_id from canonical semantic key only.

    source_modality and raw_row_ref are NOT included —
    same semantic fact from BEE+REL produces one canonical_fact + multiple provenance.
    """
    return f"fact:{_md5(review_id, subject_iri, predicate, object_ref, polarity, qualifier_fingerprint)}"


# ---------------------------------------------------------------------------
# Wrapped Signal
# ---------------------------------------------------------------------------

def make_signal_id(
    review_id: str,
    target_product_id: str,
    edge_type: str,
    dst_id: str,
    polarity: str = "",
    registry_version: str = "",
) -> str:
    """Generate deterministic signal_id."""
    return f"signal:{_md5(review_id, target_product_id, edge_type, dst_id, polarity, registry_version)}"
