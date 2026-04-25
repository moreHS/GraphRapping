"""
Quarantine repository: routes QuarantineHandler.flush() entries to DB tables.
"""

from __future__ import annotations

import json

from src.db.unit_of_work import UnitOfWork
from src.qa.quarantine_handler import QuarantineEntry


# Table → INSERT SQL mapping
_TABLE_SQL = {
    "quarantine_product_match": """
        INSERT INTO quarantine_product_match (review_id, source_brand, source_product_name,
            attempted_match_score, attempted_match_method, reason, raw_data, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
    """,
    "quarantine_placeholder": """
        INSERT INTO quarantine_placeholder (review_id, mention_text, entity_group,
            placeholder_type, reason, status)
        VALUES ($1,$2,$3,$4,$5,$6)
    """,
    "quarantine_unknown_keyword": """
        INSERT INTO quarantine_unknown_keyword (review_id, surface_text, bee_attr_raw,
            context_text, reason, status)
        VALUES ($1,$2,$3,$4,$5,$6)
    """,
    "quarantine_projection_miss": """
        INSERT INTO quarantine_projection_miss (fact_id, review_id, predicate,
            subject_type, object_type, polarity, registry_version, reason, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
    """,
    "quarantine_untyped_entity": """
        INSERT INTO quarantine_untyped_entity (review_id, mention_text, expected_types,
            context_predicate, reason, status)
        VALUES ($1,$2,$3,$4,$5,$6)
    """,
}


async def flush_quarantine(uow: UnitOfWork, entries: list[QuarantineEntry]) -> int:
    """Write all quarantine entries to their respective tables. Returns count."""
    count = 0
    for entry in entries:
        if entry.table == "quarantine_product_match":
            d = entry.data
            await uow.execute(_TABLE_SQL[entry.table],
                d.get("review_id"), d.get("source_brand"), d.get("source_product_name"),
                d.get("attempted_match_score"), d.get("attempted_match_method"),
                d.get("reason"), json.dumps(d.get("raw_data")) if d.get("raw_data") else None,
                str(d.get("status", "PENDING")),
            )
        elif entry.table == "quarantine_placeholder":
            d = entry.data
            await uow.execute(_TABLE_SQL[entry.table],
                d.get("review_id"), d.get("mention_text"), d.get("entity_group"),
                d.get("placeholder_type"), d.get("reason"), str(d.get("status", "PENDING")),
            )
        elif entry.table == "quarantine_unknown_keyword":
            d = entry.data
            await uow.execute(_TABLE_SQL[entry.table],
                d.get("review_id"), d.get("surface_text"), d.get("bee_attr_raw"),
                d.get("context_text"), d.get("reason"), str(d.get("status", "PENDING")),
            )
        elif entry.table == "quarantine_projection_miss":
            d = entry.data
            await uow.execute(_TABLE_SQL[entry.table],
                d.get("fact_id"), d.get("review_id"), d.get("predicate"),
                d.get("subject_type"), d.get("object_type"), d.get("polarity"),
                d.get("registry_version"), d.get("reason"), str(d.get("status", "PENDING")),
            )
        elif entry.table == "quarantine_untyped_entity":
            d = entry.data
            await uow.execute(_TABLE_SQL[entry.table],
                d.get("review_id"), d.get("mention_text"), d.get("expected_types", []),
                d.get("context_predicate"), d.get("reason"), str(d.get("status", "PENDING")),
            )
        count += 1
    return count
