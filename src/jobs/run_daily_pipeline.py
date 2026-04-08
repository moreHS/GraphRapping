"""
Daily pipeline orchestration.

Full flow: ingest → link → normalize → canonical → signal → aggregate → serve.
Supports KG mode: GRAPHRAPPING_KG_MODE=off|shadow|on
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

from src.ingest.review_ingest import RawReviewRecord, ingest_review
from src.link.product_matcher import ProductIndex, match_product
from src.link.placeholder_resolver import resolve_placeholders
from src.normalize.bee_normalizer import BEENormalizer
from src.normalize.relation_canonicalizer import RelationCanonicalizer
from src.normalize.date_splitter import split_date
from src.normalize.ner_normalizer import normalize_ner_mention
from src.normalize.tool_concern_segment_deriver import ToolConcernSegmentDeriver
from src.canonical.canonical_fact_builder import (
    CanonicalFactBuilder, CanonicalEntity, FactProvenance,
)
from src.wrap.projection_registry import ProjectionRegistry
from src.wrap.signal_emitter import SignalEmitter
from src.qa.quarantine_handler import QuarantineHandler
from src.mart.aggregate_product_signals import aggregate_product_signals
from src.mart.aggregate_user_preferences import refresh_user_preferences
from src.mart.build_serving_views import build_serving_product_profile, build_serving_user_profile
from src.common.ids import make_product_iri, make_concept_iri, make_mention_iri
from src.common.text_normalize import normalize_text
from src.common.enums import MatchStatus, ObjectRefKind
from src.db.persist_bundle import ReviewPersistBundle

# NER code → canonical entity type mapping for projection registry
_NER_TO_CANONICAL_TYPE = {
    "PRD": "Product", "PER": "ReviewerProxy", "BRD": "Brand",
    "CAT": "Category", "ING": "Ingredient", "DATE": "TemporalContext",
    "COL": "Color", "AGE": "AgeBand", "VOL": "Volume", "EVN": "Event",
}

def _run_shadow_comparison(
    shadow_builder_facts: list,
    production_builder_facts: list,
    review_id: str,
) -> dict[str, Any]:
    """Compare shadow KG pipeline output with production for a single review.

    Returns comparison metrics dict.
    """
    shadow_ids = {f.fact_id for f in shadow_builder_facts if hasattr(f, 'fact_id')}
    prod_ids = {f.fact_id for f in production_builder_facts if hasattr(f, 'fact_id')}
    return {
        "review_id": review_id,
        "shadow_fact_count": len(shadow_ids),
        "production_fact_count": len(prod_ids),
        "new_in_shadow": len(shadow_ids - prod_ids),
        "missing_in_shadow": len(prod_ids - shadow_ids),
    }


def _canonical_type(ner_code: str) -> str:
    """Map NER entity group code to canonical type. Pure NER mapping only."""
    return _NER_TO_CANONICAL_TYPE.get(ner_code, ner_code)


def process_review(
    record: RawReviewRecord,
    source: str,
    product_index: ProductIndex,
    bee_normalizer: BEENormalizer,
    relation_canonicalizer: RelationCanonicalizer,
    projection_registry: ProjectionRegistry,
    quarantine: QuarantineHandler,
    deriver: ToolConcernSegmentDeriver | None = None,
    predicate_contracts: dict | None = None,
    kg_mode: str = "off",
    kg_pipeline_instance: Any = None,
) -> ReviewPersistBundle:
    """Process a single review through the full pipeline.

    kg_mode: "off" (legacy), "shadow" (dual-run), "on" (KG only)
    kg_pipeline_instance: reusable KGPipeline (avoids per-review config reload)
    """
    # 1. Ingest
    ingested = ingest_review(record, source=source)

    # 2. Product match
    match = match_product(record.brnd_nm, record.prod_nm, product_index)
    target_product_iri = None
    target_product_id = None
    if match.match_status != MatchStatus.QUARANTINE and match.matched_product_id:
        target_product_iri = make_product_iri(match.matched_product_id)
        target_product_id = match.matched_product_id
    else:
        quarantine.quarantine_product_match(
            review_id=ingested.review_id,
            source_brand=record.brnd_nm,
            source_product_name=record.prod_nm,
            attempted_score=match.match_score,
            attempted_method=match.match_method,
        )

    # 3. Placeholder resolution
    resolution = resolve_placeholders(
        ner_rows=ingested.ner_rows,
        rel_rows=ingested.rel_rows,
        review_id=ingested.review_id,
        target_product_iri=target_product_iri,
        reviewer_proxy_iri=ingested.reviewer_proxy_id,
    )

    # 3-b. BEE target attribution (§원칙2: relation-gated signal 승격)
    from src.link.bee_attribution import attribute_bee_rows
    bee_attributions = attribute_bee_rows(
        bee_rows=ingested.bee_rows,
        rel_rows=ingested.rel_rows,
        target_product_name=record.prod_nm,
        same_entity_pairs=[
            {"subj_text": r.get("subj_text", ""), "obj_text": r.get("obj_text", "")}
            for r in ingested.rel_rows
            if r.get("relation_raw", "").lower() == "same_entity"
        ] if ingested.rel_rows else None,
    )
    # Enrich bee_rows with attribution metadata
    for attr in bee_attributions:
        if attr.bee_idx < len(ingested.bee_rows):
            ingested.bee_rows[attr.bee_idx]["target_linked"] = attr.target_linked
            ingested.bee_rows[attr.bee_idx]["attribution_source"] = attr.attribution_source.value
            ingested.bee_rows[attr.bee_idx]["attribution_confidence"] = attr.attribution_confidence

    # 4. Build canonical facts
    builder = CanonicalFactBuilder(predicate_contracts=predicate_contracts)

    # 4-a. Register resolved entities (Phase 2-1)
    for idx, rm in resolution.resolved_mentions.items():
        if rm.resolution_type == "UNRESOLVED":
            # KG on mode: skip placeholder quarantine for independent NER mentions
            # (KG pipeline handles mention processing internally)
            if kg_mode != "on":
                quarantine.quarantine_placeholder(
                    review_id=ingested.review_id,
                    mention_text=rm.original_text,
                    entity_group=rm.entity_group,
                    reason="unresolved mention",
                )
            continue
        builder.register_entity(CanonicalEntity(
            entity_iri=rm.resolved_iri,
            entity_type=rm.entity_group,
            canonical_name=rm.original_text,
            canonical_name_norm=normalize_text(rm.original_text),
        ))

    # 4-b. Process NER DATE mentions → split + entity register (Phase 2-3)
    for idx, ner in enumerate(ingested.ner_rows):
        if ner.get("entity_group", "").upper() == "DATE" and not ner.get("is_placeholder"):
            ner_result = normalize_ner_mention(
                mention_text=ner["mention_text"],
                entity_group="DATE",
                review_id=ingested.review_id,
                mention_idx=idx,
            )
            if ner_result.entity:
                builder.register_entity(ner_result.entity)

    # 4-c. Process BEE + REL — KG or legacy path based on kg_mode
    if kg_mode in ("on", "shadow"):
        from src.kg.kg_pipeline import KGPipeline
        from src.kg.adapter import kg_result_to_facts

        kg = kg_pipeline_instance or KGPipeline()
        kg_result = kg.process_review(
            review_id=ingested.review_id,
            product_id=target_product_id,
            ner_rows=ingested.ner_rows,
            bee_rows=ingested.bee_rows,
            rel_rows=ingested.rel_rows,
            brand_name=ingested.review_raw.get("brand_name_raw", ""),
        )

        if kg_mode == "on":
            # KG only — write to production builder
            if target_product_iri:
                kg_result_to_facts(kg_result, ingested.review_id, target_product_iri, builder,
                                   reviewer_proxy_iri=ingested.reviewer_proxy_id)
            else:
                logger.debug("KG skip: no target_product_iri for review %s", ingested.review_id)
            # Route KG keyword candidates to quarantine
            for candidate in getattr(kg_result, "keyword_candidates", []):
                quarantine.quarantine_unknown_keyword(
                    surface_text=candidate.get("surface_text", ""),
                    bee_attr_raw=candidate.get("bee_attr_raw", ""),
                    review_id=candidate.get("review_id", ingested.review_id),
                    context_text=candidate.get("context_text", ""),
                    reason=candidate.get("reason", "KG auto keyword candidate"),
                )
        else:
            # Shadow — KG writes to separate builder (comparison only, not production)
            shadow_builder = CanonicalFactBuilder(predicate_contracts=predicate_contracts)
            if target_product_iri:
                kg_result_to_facts(kg_result, ingested.review_id, target_product_iri, shadow_builder,
                                   reviewer_proxy_iri=ingested.reviewer_proxy_id)
            comparison = _run_shadow_comparison(
                shadow_builder_facts=shadow_builder.facts,
                production_builder_facts=builder.facts,
                review_id=ingested.review_id,
            )
            logger.info("Shadow KG: entities=%d facts=%d signals_pending (review %s) | "
                        "shadow_facts=%d prod_facts=%d new_in_shadow=%d missing_in_shadow=%d",
                        len(shadow_builder.entities), len(shadow_builder.facts), ingested.review_id,
                        comparison["shadow_fact_count"], comparison["production_fact_count"],
                        comparison["new_in_shadow"], comparison["missing_in_shadow"])
            # Shadow mode also quarantines keyword candidates (for comparison completeness)
            for candidate in getattr(kg_result, "keyword_candidates", []):
                quarantine.quarantine_unknown_keyword(
                    surface_text=candidate.get("surface_text", ""),
                    bee_attr_raw=candidate.get("bee_attr_raw", ""),
                    review_id=candidate.get("review_id", ingested.review_id),
                    context_text=candidate.get("context_text", ""),
                    reason=candidate.get("reason", "KG shadow mode keyword candidate"),
                )

    # 4-c-legacy. Process BEE rows (legacy path — off or shadow mode)
    # GUARD (§원칙5): BEE phrase 단독으로 concern/context signal 생성 금지.
    # Concern/Context는 explicit relation (addresses, used_on, benefits, causes 등)에서만 생성.
    if kg_mode in ("off", "shadow"):
      for i, bee_row in enumerate(ingested.bee_rows):
        # BEE attribution gate: only process target-linked BEE (§원칙2)
        if not bee_row.get("target_linked", True):
            continue
        bee_result = bee_normalizer.normalize(
            phrase_text=bee_row["phrase_text"],
            bee_attr_raw=bee_row["bee_attr_raw"],
            raw_sentiment=bee_row.get("raw_sentiment"),
        )
        if target_product_iri:
            provenance = FactProvenance(
                raw_table="bee_raw", raw_row_id=str(i),
                review_id=ingested.review_id,
                snippet=bee_row["phrase_text"], source_modality="BEE",
            )
            builder.add_bee_facts(
                review_id=ingested.review_id,
                product_iri=target_product_iri,
                bee_attr_id=bee_result.bee_attr_id,
                bee_attr_label=bee_result.bee_attr_label,
                keyword_ids=bee_result.keyword_ids,
                keyword_labels=bee_result.keyword_labels,
                polarity=bee_result.polarity,
                provenance=provenance,
                negated=bee_result.negated if bee_result.negated else None,
                intensity=bee_result.intensity if bee_result.intensity != 1.0 else None,
                evidence_kind="BEE_DICT" if bee_result.keyword_source == "DICT" else "BEE_CANDIDATE",
                base_confidence=bee_result.confidence,
            )
        # Quarantine unknown keywords
        for surface in bee_normalizer.get_unknown_surfaces(bee_row["phrase_text"]):
            quarantine.quarantine_unknown_keyword(
                surface_text=surface,
                bee_attr_raw=bee_row["bee_attr_raw"],
                review_id=ingested.review_id,
            )

    # 4-d. Process REL rows (legacy path — off or shadow mode only)
    # KG "on" mode skips this entirely (KG pipeline handles REL in Step 4-c)
    mention_index_map: dict[tuple, int] = {}
    for idx, ner in enumerate(ingested.ner_rows):
        key = (ner.get("start_offset"), ner.get("end_offset"), ner["mention_text"])
        mention_index_map[key] = idx

    for i, rel_row in enumerate(ingested.rel_rows):
        # KG "on" mode: skip all legacy REL processing (handled by KG pipeline)
        if kg_mode == "on":
            break

        # NER-BeE relations are already handled by BEE row processing (Phase 4-c)
        if rel_row.get("source_type") == "NER-BeE":
            continue

        canon_result = relation_canonicalizer.canonicalize(rel_row["relation_raw"])

        # Phase 2-5: Unknown REL → quarantine
        if canon_result.action == "QUARANTINE":
            quarantine.quarantine_projection_miss(
                predicate=rel_row["relation_raw"],
                subject_type=_canonical_type(rel_row.get("subj_group", "")),
                object_type=_canonical_type(rel_row.get("obj_group", "")),
                review_id=ingested.review_id,
                reason="unknown_relation_canonical",
            )
            continue
        if canon_result.action in ("DROP", "PREPROCESS_ONLY"):
            continue
        if not target_product_iri:
            continue

        # Phase 2-2: Resolve subject/object IRIs via mention index
        subj_key = (rel_row.get("subj_start"), rel_row.get("subj_end"), rel_row["subj_text"])
        obj_key = (rel_row.get("obj_start"), rel_row.get("obj_end"), rel_row["obj_text"])

        subj_idx = mention_index_map.get(subj_key)
        obj_idx = mention_index_map.get(obj_key)

        subj_iri = (resolution.resolved_mentions[subj_idx].resolved_iri
                    if subj_idx is not None and subj_idx in resolution.resolved_mentions
                    else target_product_iri)
        obj_iri = None
        obj_type = rel_row.get("obj_group", "")
        obj_ref_kind = ObjectRefKind.ENTITY

        if obj_idx is not None and obj_idx in resolution.resolved_mentions:
            obj_iri = resolution.resolved_mentions[obj_idx].resolved_iri
        else:
            # BEE object (NER-BeE source) or unmatched → derive type
            obj_text = rel_row["obj_text"]
            predicate = canon_result.canonical_predicate

            # Phase 2-4: Ambiguous type derivation
            if deriver and predicate in ("used_with",):
                derive_result = deriver.derive_used_with(obj_text)
                if derive_result.entity_type:
                    obj_type = derive_result.entity_type.value
                    obj_iri = derive_result.concept_id or make_concept_iri(obj_type, normalize_text(obj_text))
                    obj_ref_kind = ObjectRefKind.CONCEPT
                else:
                    quarantine.quarantine_untyped_entity(
                        mention_text=obj_text, expected_types=["Tool", "Product"],
                        context_predicate=predicate, review_id=ingested.review_id,
                    )
                    continue
            elif deriver and predicate in ("causes", "affects", "addresses", "treats", "benefits", "used_for", "experiences"):
                derive_result = deriver.derive_concern(obj_text)
                if derive_result.entity_type:
                    obj_type = derive_result.entity_type.value
                    obj_iri = derive_result.concept_id or make_concept_iri("Concern", normalize_text(obj_text))
                    obj_ref_kind = ObjectRefKind.CONCEPT
                else:
                    obj_iri = None  # will use obj_value_text
            elif deriver and predicate in ("recommended_to", "targeted_at", "addressed_to"):
                derive_result = deriver.derive_segment(obj_text)
                if derive_result.entity_type:
                    obj_type = derive_result.entity_type.value
                    obj_iri = derive_result.concept_id or make_concept_iri("UserSegment", normalize_text(obj_text))
                    obj_ref_kind = ObjectRefKind.CONCEPT
                else:
                    quarantine.quarantine_untyped_entity(
                        mention_text=obj_text, expected_types=["UserSegment"],
                        context_predicate=predicate, review_id=ingested.review_id,
                    )
                    continue
            elif obj_type == "DATE" or rel_row.get("obj_group", "").upper() == "DATE":
                date_result = split_date(obj_text)
                obj_type = date_result.kind.value
                obj_iri = make_concept_iri(obj_type, normalize_text(date_result.value))
                obj_ref_kind = ObjectRefKind.CONCEPT
                builder.register_entity(CanonicalEntity(
                    entity_iri=obj_iri, entity_type=obj_type,
                    canonical_name=obj_text, canonical_name_norm=normalize_text(obj_text),
                ))
            else:
                # Fallback: use raw text
                obj_iri = None
                obj_ref_kind = ObjectRefKind.TEXT

        # Register object entity if concept IRI
        if obj_iri and obj_ref_kind == ObjectRefKind.CONCEPT:
            builder.register_entity(CanonicalEntity(
                entity_iri=obj_iri, entity_type=obj_type,
                canonical_name=rel_row["obj_text"],
                canonical_name_norm=normalize_text(rel_row["obj_text"]),
            ))

        provenance = FactProvenance(
            raw_table="rel_raw", raw_row_id=str(i),
            review_id=ingested.review_id,
            snippet=f"{rel_row['subj_text']} → {rel_row['obj_text']}",
            source_modality="REL",
        )
        builder.add_fact(
            review_id=ingested.review_id,
            subject_iri=subj_iri,
            predicate=canon_result.canonical_predicate,
            object_iri=obj_iri,
            object_value_text=rel_row["obj_text"] if not obj_iri else None,
            object_ref_kind=obj_ref_kind,
            subject_type=_canonical_type(rel_row.get("subj_group", "Product")),
            object_type=_canonical_type(obj_type),
            source_modality="REL",
            provenance=provenance,
        )

    # Quarantine invalid facts (predicate contract violations)
    for inv in builder.invalid_facts:
        quarantine.quarantine_invalid_fact(inv)

    # 5. Emit signals with window_ts
    window_ts = ingested.review_raw.get("event_time_utc")
    window_ts_str = str(window_ts) if window_ts else None

    emitter = SignalEmitter(projection_registry)
    emit_result = emitter.emit_from_facts(
        facts=builder.facts,
        target_product_id=target_product_id,
        window_ts=window_ts_str,
    )

    # Quarantine projection misses with actual predicate info
    for fact_id in emit_result.quarantined_facts:
        fact = next((f for f in builder.facts if f.fact_id == fact_id), None)
        quarantine.quarantine_projection_miss(
            predicate=fact.predicate if fact else "unknown",
            subject_type=fact.subject_type if fact else "",
            object_type=fact.object_type if fact else "",
            fact_id=fact_id,
            review_id=ingested.review_id,
            registry_version=projection_registry.version,
        )

    # Build the persist bundle
    bundle = ReviewPersistBundle(
        review_raw=ingested.review_raw,
        review_catalog_link={
            "review_id": ingested.review_id,
            "source_brand": ingested.review_raw.get("brand_name_raw"),
            "source_product_name": ingested.review_raw.get("product_name_raw"),
            "matched_product_id": target_product_id,
            "match_status": match.match_status.value,
            "match_score": match.match_score,
            "match_method": match.match_method,
        },
        ner_rows=ingested.ner_rows,
        bee_rows=ingested.bee_rows,
        rel_rows=ingested.rel_rows,
        canonical_entities=builder.entities,
        canonical_facts=builder.facts,
        wrapped_signals=list(emit_result.signals),
        signal_evidence_rows=list(emit_result.evidence_rows),
        quarantine_entries=quarantine.flush(),
        review_id=ingested.review_id,
        matched_product_id=target_product_id,
        dirty_product_ids={target_product_id} if target_product_id else set(),
        invalid_facts=builder.invalid_facts,
    )

    return bundle


def build_review_persist_bundle(
    record: RawReviewRecord,
    source: str,
    product_index: ProductIndex,
    bee_normalizer: BEENormalizer,
    relation_canonicalizer: RelationCanonicalizer,
    projection_registry: ProjectionRegistry,
    deriver: ToolConcernSegmentDeriver | None = None,
    predicate_contracts: dict | None = None,
    kg_mode: str = "off",
    kg_pipeline_instance: Any = None,
) -> ReviewPersistBundle:
    """Build a ReviewPersistBundle from a raw review record.

    Convenience wrapper: creates a fresh quarantine handler internally.
    """
    quarantine = QuarantineHandler()
    bundle = process_review(
        record=record, source=source,
        product_index=product_index,
        bee_normalizer=bee_normalizer,
        relation_canonicalizer=relation_canonicalizer,
        projection_registry=projection_registry,
        quarantine=quarantine,
        deriver=deriver,
        predicate_contracts=predicate_contracts,
        kg_mode=kg_mode,
        kg_pipeline_instance=kg_pipeline_instance,
    )
    return bundle


def bundle_to_result_dict(bundle: ReviewPersistBundle, window_ts_str: str | None = None) -> dict[str, Any]:
    """Convert ReviewPersistBundle to legacy summary dict for backward compatibility."""
    return {
        "review_id": bundle.review_id,
        "reviewer_proxy_id": bundle.review_raw.get("reviewer_proxy_id", ""),
        "identity_stability": bundle.review_raw.get("identity_stability", ""),
        "match_status": bundle.review_catalog_link.get("match_status", ""),
        "matched_product_id": bundle.matched_product_id,
        "entity_count": len(bundle.canonical_entities),
        "fact_count": len(bundle.canonical_facts),
        "signal_count": len(bundle.wrapped_signals),
        "quarantine_count": len(bundle.quarantine_entries),
        "invalid_fact_count": len(bundle.invalid_facts),
        "signals": [_signal_to_dict(s) for s in bundle.wrapped_signals],
        "evidence_rows": bundle.signal_evidence_rows,
        "event_time_utc": window_ts_str or str(bundle.review_raw.get("event_time_utc", "")),
    }


def run_batch(
    reviews: list[RawReviewRecord],
    source: str,
    product_index: ProductIndex,
    product_masters: dict[str, dict],
    concept_links: dict[str, list[dict]],
    user_masters: dict[str, dict],
    user_adapted_facts: dict[str, list[dict]],
    bee_normalizer: BEENormalizer,
    relation_canonicalizer: RelationCanonicalizer,
    projection_registry: ProjectionRegistry,
    quarantine: QuarantineHandler,
    deriver: ToolConcernSegmentDeriver | None = None,
    predicate_contracts: dict | None = None,
    purchase_events_by_user: dict[str, list] | None = None,
    kg_mode: str = "off",
) -> dict[str, Any]:
    """Full batch pipeline: reviews → signals → aggregate → serving.

    kg_mode: "off" (legacy), "shadow" (dual-run), "on" (KG only)
    """
    # Create KGPipeline once for entire batch (avoid per-review config reload)
    kg_pipeline_instance = None
    if kg_mode in ("on", "shadow"):
        from src.kg.kg_pipeline import KGPipeline
        kg_pipeline_instance = KGPipeline()

    # Step 1: Process each review → bundles
    review_results = []
    all_bundles: list[ReviewPersistBundle] = []
    all_signal_dicts = []
    for record in reviews:
        bundle = process_review(
            record=record, source=source,
            product_index=product_index,
            bee_normalizer=bee_normalizer,
            relation_canonicalizer=relation_canonicalizer,
            projection_registry=projection_registry,
            quarantine=quarantine,
            deriver=deriver,
            predicate_contracts=predicate_contracts,
            kg_mode=kg_mode,
            kg_pipeline_instance=kg_pipeline_instance,
        )
        all_bundles.append(bundle)
        result_dict = bundle_to_result_dict(bundle)
        review_results.append(result_dict)
        all_signal_dicts.extend(result_dict.get("signals", []))

    # Step 2: Aggregate product signals
    agg_signals = aggregate_product_signals(all_signal_dicts)

    # Step 2b: Build brand_lookup (product_id → brand_concept_id) for purchase confidence
    brand_lookup: dict[str, str] = {}
    for product_iri, links_list in concept_links.items():
        pid = product_iri.replace("product:", "") if product_iri.startswith("product:") else product_iri
        for link in links_list:
            if link.get("link_type") == "HAS_BRAND":
                brand_lookup[pid] = link["concept_id"]
                break

    # Step 3: Build user preferences (with purchase brand confidence)
    from src.ingest.purchase_ingest import derive_brand_confidence, PurchaseEvent
    from src.user.canonicalize_user_facts import canonicalize_user_facts

    serving_users = []
    for user_id, facts in user_adapted_facts.items():
        # Canonicalize user facts first
        canonical_facts = canonicalize_user_facts(user_id, facts)

        # Derive purchase-based brand confidence (concept IRI keyed)
        purchase_conf: dict[str, float] = {}
        if purchase_events_by_user and user_id in purchase_events_by_user:
            purchase_conf = derive_brand_confidence(
                purchase_events_by_user[user_id], brand_lookup,
            )

        user_prefs = refresh_user_preferences(user_id, canonical_facts, purchase_conf)
        if user_id in user_masters:
            profile = build_serving_user_profile(user_masters[user_id], user_prefs)
            serving_users.append(profile)

    # Step 4: Build serving product profiles
    serving_products = []
    for pid, master in product_masters.items():
        pid_signals = [s for s in agg_signals if s.target_product_id == pid]
        pid_signals_dicts = [_agg_to_dict(s) for s in pid_signals]
        links = concept_links.get(make_product_iri(pid), [])
        profile = build_serving_product_profile(master, pid_signals_dicts, concept_links=links)
        serving_products.append(profile)

    total_signals = sum(r.get("signal_count", 0) for r in review_results)
    total_quarantined = quarantine.pending_count

    return {
        "review_results": review_results,
        "agg_signal_count": len(agg_signals),
        "serving_products": serving_products,
        "serving_users": serving_users,
        "total_signals": total_signals,
        "total_quarantined": total_quarantined,
    }


def _signal_to_dict(signal) -> dict:
    return {
        "signal_id": signal.signal_id,
        "review_id": signal.review_id,
        "target_product_id": signal.target_product_id,
        "signal_family": signal.signal_family,
        "edge_type": signal.edge_type,
        "dst_type": signal.dst_type,
        "dst_id": signal.dst_id,
        "dst_ref_kind": signal.dst_ref_kind,
        "polarity": signal.polarity,
        "weight": signal.weight,
        "window_ts": signal.window_ts,
        "source_fact_ids": signal.source_fact_ids,
        "bee_attr_id": signal.bee_attr_id,
        "keyword_id": signal.keyword_id,
    }


def _agg_to_dict(agg) -> dict:
    return {
        "target_product_id": agg.target_product_id,
        "canonical_edge_type": agg.canonical_edge_type,
        "dst_node_type": agg.dst_node_type,
        "dst_node_id": agg.dst_node_id,
        "window_type": agg.window_type,
        "review_cnt": agg.review_cnt,
        "pos_cnt": agg.pos_cnt,
        "neg_cnt": agg.neg_cnt,
        "score": agg.score,
        "is_promoted": agg.is_promoted,
        "last_seen_at": agg.last_seen_at,
    }
