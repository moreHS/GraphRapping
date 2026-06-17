from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

from src.db.repos.review_summary_repo import (
    delete_review_summary_sidecar_outside_products,
    insert_review_summary_manifest,
    upsert_review_summary_sidecar,
)


class FakeUow:
    def __init__(self) -> None:
        self.as_of_ts = datetime(2026, 6, 17, tzinfo=timezone.utc)
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetchval_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "DELETE 3" if "DELETE FROM review_summary_sidecar" in query else "INSERT 0 1"

    async def fetchval(self, query: str, *args: Any) -> int:
        self.fetchval_calls.append((query, args))
        return 42


def test_upsert_review_summary_sidecar_serializes_jsonb_docs() -> None:
    uow = FakeUow()

    asyncio.run(
        upsert_review_summary_sidecar(
            uow,  # type: ignore[arg-type]
            {
                "product_id": "P1",
                "source_product_id": "4077",
                "source_channel": "036",
                "source_key_type": "chn_prd_cd",
                "review_source": "own",
                "review_channel": "036",
                "review_summary_category": "own-innisfree",
                "match_status": "exact_category",
                "long_doc_id": "long-1",
                "short_doc_id": "short-1",
                "long_doc": {"_source": {"summary": "좋음"}},
                "short_doc": {"_source": {"summary": "짧음"}},
                "candidate_metadata": {"long": {"candidate_count": 1}},
                "normalized_summary": {"long": {"summary": "좋음"}},
                "an_date": "2026-06-17",
            },
        )
    )

    query, args = uow.executed[0]
    assert "INSERT INTO review_summary_sidecar" in query
    assert "ON CONFLICT (product_id) DO UPDATE SET" in query
    assert args[:10] == (
        "P1",
        "4077",
        "036",
        "chn_prd_cd",
        "own",
        "036",
        "own-innisfree",
        "exact_category",
        "long-1",
        "short-1",
    )
    assert json.loads(args[10]) == {"_source": {"summary": "좋음"}}
    assert json.loads(args[11]) == {"_source": {"summary": "짧음"}}
    assert json.loads(args[12]) == {"long": {"candidate_count": 1}}
    assert json.loads(args[13]) == {"long": {"summary": "좋음"}}
    assert args[14] == "2026-06-17"
    assert args[15] == "es8_summary_review"
    assert args[16] == uow.as_of_ts


def test_delete_review_summary_sidecar_outside_products_returns_count() -> None:
    uow = FakeUow()

    deleted = asyncio.run(
        delete_review_summary_sidecar_outside_products(
            uow,  # type: ignore[arg-type]
            ["P1", "P2"],
        )
    )

    query, args = uow.executed[0]
    assert "DELETE FROM review_summary_sidecar" in query
    assert args == (["P1", "P2"],)
    assert deleted == 3


def test_insert_review_summary_manifest_serializes_payload_and_returns_id() -> None:
    uow = FakeUow()

    manifest_id = asyncio.run(
        insert_review_summary_manifest(
            uow,  # type: ignore[arg-type]
            {
                "source": "es8_summary_review",
                "long_alias": "summary-review-long",
                "short_alias": "summary-review-short",
                "an_date": "2026-06-17",
                "product_count": 2,
                "clean_lookup_product_count": 1,
                "fetched_long_docs": 10,
                "fetched_short_docs": 4,
                "matched": 1,
                "exact_category": 1,
                "source_unique": 0,
                "product_id_unique": 0,
                "ambiguous_skipped": 0,
                "not_found": 0,
                "collision_excluded": 1,
                "errors": 0,
                "payload": {"match_status_counts": {"exact_category": 1}},
            },
        )
    )

    query, args = uow.fetchval_calls[0]
    assert "INSERT INTO review_summary_manifest" in query
    assert manifest_id == 42
    assert args[:4] == (
        "es8_summary_review",
        "summary-review-long",
        "summary-review-short",
        "2026-06-17",
    )
    assert args[4:16] == (2, 1, 10, 4, 1, 1, 0, 0, 0, 0, 1, 0)
    assert json.loads(args[16]) == {"match_status_counts": {"exact_category": 1}}
