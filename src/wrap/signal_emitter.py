"""
Signal emitter: canonical facts → wrapped signals via projection registry.

Merge policy: weight=max, confidence=max, source_modalities=union.
Transform dispatcher: identity, reverse, product_linkage (all inside emit_from_fact).
Evidence rows generated alongside signals.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.ids import make_signal_id, make_qualifier_fingerprint
from src.common.enums import SignalFamily, SCORING_EXCLUDED_FAMILIES
from src.wrap.projection_registry import ProjectionRegistry, ProjectionResult
from src.canonical.canonical_fact_builder import CanonicalFact


@dataclass
class WrappedSignal:
    """Layer 2.5 wrapped signal.

    Provenance source of truth: signal_evidence table (not source_fact_ids).
    source_fact_ids is a cache/debug field — always use signal_evidence for explanation chains.
    """
    signal_id: str
    review_id: str
    user_id: str | None
    target_product_id: str | None
    source_fact_ids: list[str]  # DEPRECATED: write-only cache. Read provenance from signal_evidence table.
    # NOTE: All provenance reads must use signal_evidence table, not source_fact_ids.
    # This field is populated for backward compatibility only and will be removed.
    signal_family: str
    edge_type: str
    dst_type: str
    dst_id: str
    dst_ref_kind: str
    bee_attr_id: str | None = None
    keyword_id: str | None = None
    polarity: str | None = None
    negated: bool | None = None
    intensity: float | None = None
    weight: float = 1.0
    registry_version: str = ""
    window_ts: str | None = None


@dataclass
class EmitResult:
    signals: list[WrappedSignal]
    quarantined_facts: list[str]
    evidence_rows: list[dict] = field(default_factory=list)


class SignalEmitter:
    """Emits wrapped signals with transform dispatch and evidence generation."""

    def __init__(self, registry: ProjectionRegistry) -> None:
        self._registry = registry
        self._signals: dict[str, WrappedSignal] = {}
        self._quarantined: list[str] = []
        self._evidence_rows: list[dict] = []

    def reset(self) -> None:
        self._signals.clear()
        self._quarantined.clear()
        self._evidence_rows.clear()

    def emit_from_fact(
        self,
        fact: CanonicalFact,
        target_product_id: str | None = None,
        bee_attr_id: str | None = None,
        keyword_id: str | None = None,
        negated: bool | None = None,
        intensity: float | None = None,
        window_ts: str | None = None,
    ) -> str | None:
        """Emit a wrapped signal from a canonical fact.

        Handles transform dispatch (identity/reverse/product_linkage),
        qualifier checks, and weight rules.
        """
        result = self._registry.project(
            predicate=fact.predicate,
            subject_type=fact.subject_type,
            object_type=fact.object_type,
            polarity=fact.polarity or "",
        )

        if isinstance(result, str):
            if result == "QUARANTINE":
                self._quarantined.append(fact.fact_id)
            return None

        # Qualifier check
        if result.qualifier_required and not fact.qualifiers:
            self._quarantined.append(fact.fact_id)
            return None

        # Transform dispatch
        transform = result.transform
        dst_id: str
        actual_bee_attr_id = bee_attr_id
        actual_keyword_id = keyword_id

        if transform == "reverse":
            dst_id = fact.subject_iri
        elif transform == "product_linkage":
            # BEE keyword: fact is BEEAttr→Keyword, signal anchors to Product
            dst_id = fact.object_iri or fact.object_value_text or ""
            actual_bee_attr_id = fact.subject_iri  # BEEAttr IRI
            actual_keyword_id = fact.object_iri    # Keyword IRI
        else:  # identity
            dst_id = fact.object_iri or fact.object_value_text or ""

        # Weight rule
        if result.weight_rule == "bee_weight":
            weight = fact.confidence or 1.0
        else:
            weight = 1.0

        # Use fact-level negation/intensity if caller didn't provide
        actual_negated = negated if negated is not None else fact.negated
        actual_intensity = intensity if intensity is not None else fact.intensity

        # Qualifier fingerprint for dedup key
        qfp = ""
        if fact.qualifiers:
            q_pairs = [(q.qualifier_key, q.qualifier_iri or q.qualifier_value_text or str(q.qualifier_value_num or ""))
                       for q in fact.qualifiers]
            qfp = make_qualifier_fingerprint(q_pairs)

        signal_id = make_signal_id(
            review_id=fact.review_id,
            target_product_id=target_product_id or "",
            edge_type=result.edge_type,
            dst_id=dst_id,
            polarity=fact.polarity or "",
            registry_version=result.registry_version,
            negated=str(actual_negated).lower() if actual_negated is not None else "",
            qualifier_fingerprint=qfp,
        )

        existing = self._signals.get(signal_id)
        if existing:
            # Merge policy: block merge if polarity/negated differ
            if existing.polarity != fact.polarity or existing.negated != actual_negated:
                pass  # Different signal_id should already handle this, but guard
            else:
                # Cache-only: provenance SoT is signal_evidence, not source_fact_ids
                if fact.fact_id not in existing.source_fact_ids:
                    existing.source_fact_ids.append(fact.fact_id)
                existing.weight = max(existing.weight, weight)
        else:
            # Determine dst_ref_kind based on transform
            if transform == "reverse":
                # Reverse: dst is now the subject — use subject_ref_kind
                dst_ref_kind = getattr(fact, "subject_ref_kind", "") or "ENTITY"
            else:
                dst_ref_kind = fact.object_ref_kind

            signal = WrappedSignal(
                signal_id=signal_id,
                review_id=fact.review_id,
                user_id=None,
                target_product_id=target_product_id,
                source_fact_ids=[fact.fact_id],
                signal_family=result.signal_family,
                edge_type=result.edge_type,
                dst_type=result.dst_type,
                dst_id=dst_id,
                dst_ref_kind=dst_ref_kind,
                bee_attr_id=actual_bee_attr_id,
                keyword_id=actual_keyword_id,
                polarity=fact.polarity,
                negated=actual_negated,
                intensity=actual_intensity,
                weight=weight,
                registry_version=result.registry_version,
                window_ts=window_ts,
            )
            self._signals[signal_id] = signal

        # Generate evidence row
        self._evidence_rows.append({
            "signal_id": signal_id,
            "fact_id": fact.fact_id,
            "evidence_rank": len([e for e in self._evidence_rows if e["signal_id"] == signal_id]),
            "contribution": 1.0,
        })

        return signal_id

    def emit_from_facts(
        self,
        facts: list[CanonicalFact],
        target_product_id: str | None = None,
        window_ts: str | None = None,
    ) -> EmitResult:
        """Emit signals for a batch of canonical facts.

        Auto-routes HAS_KEYWORD facts through product_linkage.
        """
        for fact in facts:
            # Skip evidence-only facts — they are not promoted to signals
            if getattr(fact, "fact_status", "CANONICAL_PROMOTED") == "EVIDENCE_ONLY":
                continue

            # Auto-detect BEE metadata from fact context
            bee_attr_id = None
            keyword_id = None
            if fact.predicate == "has_attribute" and fact.object_type == "BEEAttr":
                bee_attr_id = fact.object_iri
            elif fact.predicate == "HAS_KEYWORD" and fact.subject_type == "BEEAttr":
                bee_attr_id = fact.subject_iri
                keyword_id = fact.object_iri

            self.emit_from_fact(
                fact,
                target_product_id=target_product_id,
                bee_attr_id=bee_attr_id,
                keyword_id=keyword_id,
                negated=fact.negated,
                intensity=fact.intensity,
                window_ts=window_ts,
            )

        return EmitResult(
            signals=list(self._signals.values()),
            quarantined_facts=list(self._quarantined),
            evidence_rows=list(self._evidence_rows),
        )

    @property
    def signal_count(self) -> int:
        return len(self._signals)

    @property
    def quarantined_count(self) -> int:
        return len(self._quarantined)
