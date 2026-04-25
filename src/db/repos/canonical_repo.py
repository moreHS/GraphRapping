"""
Canonical repository: canonical_entity + canonical_fact + fact_provenance + fact_qualifier.

Handles diff-based fact reprocess: unchanged keep, removed close, reactivate.
"""

from __future__ import annotations

import json

from src.db.unit_of_work import UnitOfWork
from src.canonical.canonical_fact_builder import CanonicalFact, CanonicalEntity


async def upsert_canonical_entity(uow: UnitOfWork, entity: CanonicalEntity) -> None:
    await uow.execute("""
        INSERT INTO canonical_entity (entity_iri, entity_type, canonical_name,
            canonical_name_norm, source_system, source_key, match_confidence, attrs,
            created_at, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$9)
        ON CONFLICT (entity_iri) DO UPDATE SET
            canonical_name = CASE
                WHEN EXCLUDED.match_confidence > COALESCE(canonical_entity.match_confidence,0)
                THEN EXCLUDED.canonical_name ELSE canonical_entity.canonical_name END,
            match_confidence = GREATEST(COALESCE(EXCLUDED.match_confidence,0),
                COALESCE(canonical_entity.match_confidence,0)),
            updated_at = EXCLUDED.updated_at
    """,
        entity.entity_iri, entity.entity_type, entity.canonical_name,
        entity.canonical_name_norm, entity.source_system, entity.source_key,
        entity.match_confidence, json.dumps(entity.attrs) if entity.attrs else None,
        uow.as_of_ts,
    )


async def diff_upsert_facts(
    uow: UnitOfWork,
    review_id: str,
    new_facts: list[CanonicalFact],
) -> dict[str, int]:
    """Diff-based fact upsert for a review reprocess.

    Returns: {"inserted": n, "refreshed": n, "reactivated": n, "closed": n}
    """
    # Get currently open fact_ids for this review
    rows = await uow.fetch(
        "SELECT fact_id FROM canonical_fact WHERE review_id = $1 AND valid_to IS NULL",
        review_id,
    )
    open_ids = {r["fact_id"] for r in rows}
    new_ids = {f.fact_id for f in new_facts}

    # Close removed facts
    to_close = open_ids - new_ids
    for fid in to_close:
        await uow.execute(
            "UPDATE canonical_fact SET valid_to = $1 WHERE fact_id = $2",
            uow.as_of_ts, fid,
        )

    stats = {"inserted": 0, "refreshed": 0, "reactivated": 0, "closed": len(to_close)}

    for fact in new_facts:
        existing = await uow.fetchrow(
            "SELECT fact_id, valid_to FROM canonical_fact WHERE fact_id = $1",
            fact.fact_id,
        )

        if existing is None:
            # New fact: insert
            await _insert_fact(uow, fact)
            stats["inserted"] += 1
        elif existing["valid_to"] is None:
            # Active: refresh columns (keep fact_id, created_at, valid_from)
            await _refresh_fact(uow, fact)
            stats["refreshed"] += 1
        else:
            # Closed: reactivate
            await _reactivate_fact(uow, fact)
            stats["reactivated"] += 1

        # Full-replace provenance and qualifiers
        await _replace_provenance(uow, fact)
        await _replace_qualifiers(uow, fact)

    return stats


async def _insert_fact(uow: UnitOfWork, fact: CanonicalFact) -> None:
    await uow.execute("""
        INSERT INTO canonical_fact (fact_id, review_id, subject_iri, predicate,
            object_iri, object_value_text, object_value_num, object_value_json,
            object_ref_kind, subject_type, object_type, polarity, confidence,
            negated, intensity, evidence_kind, fact_status, target_linked,
            attribution_source,
            source_modalities, extraction_version, registry_version,
            valid_from, valid_to, created_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,NULL,$24)
    """,
        fact.fact_id, fact.review_id, fact.subject_iri, fact.predicate,
        fact.object_iri, fact.object_value_text, fact.object_value_num, None,
        fact.object_ref_kind, fact.subject_type, fact.object_type,
        fact.polarity, fact.confidence,
        fact.negated, fact.intensity, fact.evidence_kind, fact.fact_status,
        fact.target_linked, fact.attribution_source, fact.source_modalities,
        fact.extraction_version, fact.registry_version,
        uow.as_of_ts, uow.as_of_ts,
    )


async def _refresh_fact(uow: UnitOfWork, fact: CanonicalFact) -> None:
    await uow.execute("""
        UPDATE canonical_fact SET
            subject_iri=$2, predicate=$3, object_iri=$4, object_value_text=$5,
            object_ref_kind=$6, subject_type=$7, object_type=$8,
            polarity=$9, confidence=$10,
            source_modalities = (
                SELECT ARRAY(SELECT DISTINCT unnest(source_modalities || $11))
            ),
            negated=$12, intensity=$13, evidence_kind=$14, fact_status=$15,
            target_linked=$16, attribution_source=$17
        WHERE fact_id = $1
    """,
        fact.fact_id, fact.subject_iri, fact.predicate, fact.object_iri,
        fact.object_value_text, fact.object_ref_kind, fact.subject_type,
        fact.object_type, fact.polarity, fact.confidence, fact.source_modalities,
        fact.negated, fact.intensity, fact.evidence_kind, fact.fact_status,
        fact.target_linked, fact.attribution_source,
    )


async def _reactivate_fact(uow: UnitOfWork, fact: CanonicalFact) -> None:
    await uow.execute("""
        UPDATE canonical_fact SET
            valid_from = $2, valid_to = NULL,
            subject_iri=$3, predicate=$4, object_iri=$5, object_value_text=$6,
            object_ref_kind=$7, subject_type=$8, object_type=$9,
            polarity=$10, confidence=$11, source_modalities=$12,
            negated=$13, intensity=$14, evidence_kind=$15, fact_status=$16,
            target_linked=$17, attribution_source=$18
        WHERE fact_id = $1
    """,
        fact.fact_id, uow.as_of_ts, fact.subject_iri, fact.predicate,
        fact.object_iri, fact.object_value_text, fact.object_ref_kind,
        fact.subject_type, fact.object_type, fact.polarity,
        fact.confidence, fact.source_modalities,
        fact.negated, fact.intensity, fact.evidence_kind, fact.fact_status,
        fact.target_linked, fact.attribution_source,
    )


async def _replace_provenance(uow: UnitOfWork, fact: CanonicalFact) -> None:
    await uow.execute("DELETE FROM fact_provenance WHERE fact_id = $1", fact.fact_id)
    for prov in fact.provenance:
        await uow.execute("""
            INSERT INTO fact_provenance (fact_id, raw_table, raw_row_id, review_id,
                snippet, start_offset, end_offset, source_modality, evidence_rank,
                source_domain, source_kind)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
        """,
            fact.fact_id, prov.raw_table, prov.raw_row_id, prov.review_id,
            prov.snippet, prov.start_offset, prov.end_offset,
            prov.source_modality, prov.evidence_rank,
            prov.source_domain, prov.source_kind,
        )


async def _replace_qualifiers(uow: UnitOfWork, fact: CanonicalFact) -> None:
    await uow.execute("DELETE FROM fact_qualifier WHERE fact_id = $1", fact.fact_id)
    for q in fact.qualifiers:
        await uow.execute("""
            INSERT INTO fact_qualifier (fact_id, qualifier_key, qualifier_type,
                qualifier_iri, qualifier_value_text, qualifier_value_num)
            VALUES ($1,$2,$3,$4,$5,$6)
        """,
            fact.fact_id, q.qualifier_key, q.qualifier_type,
            q.qualifier_iri, q.qualifier_value_text, q.qualifier_value_num,
        )
