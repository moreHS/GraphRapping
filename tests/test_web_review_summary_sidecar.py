from __future__ import annotations

import asyncio
import json

from src.web.review_summary_sidecar import (
    fetch_sidecar_summaries,
    summarize_sidecar_row,
)


def test_summarize_sidecar_row_returns_safe_preview() -> None:
    row = {
        "product_id": "61289",
        "match_status": "exact_category",
        "normalized_summary": {
            "long": {
                "summary": "롱 요약입니다.",
                "review_cnt": 4965,
                "An_date": "2026-06-17",
            },
            "short": {
                "summary": "짧은 요약입니다.",
                "review_cnt": 4965,
            },
        },
        "an_date": "2026-06-17",
        "long_doc": {"raw": "not exposed"},
        "candidate_metadata": {"source_identity": {"source_product_id": "61289"}},
    }

    preview = summarize_sidecar_row(row)

    assert preview == {
        "product_id": "61289",
        "match_status": "exact_category",
        "an_date": "2026-06-17",
        "long_summary": "롱 요약입니다.",
        "short_summary": "짧은 요약입니다.",
        "review_count": 4965,
    }


def test_summarize_sidecar_row_accepts_jsonb_string_payload() -> None:
    row = {
        "product_id": "P1",
        "match_status": "exact_category",
        "normalized_summary": json.dumps(
            {
                "long": {"summary": "Long summary", "review_cnt": "42"},
                "short": {"summary": "Short summary"},
            }
        ),
        "an_date": "2026-06-17",
    }

    preview = summarize_sidecar_row(row)

    assert preview is not None
    assert preview["long_summary"] == "Long summary"
    assert preview["short_summary"] == "Short summary"
    assert preview["review_count"] == 42


def test_fetch_sidecar_summaries_fails_closed_without_database_url(monkeypatch) -> None:
    monkeypatch.delenv("GRAPHRAPPING_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert asyncio.run(fetch_sidecar_summaries(["P1"])) == {}


def test_fetch_sidecar_summaries_fails_closed_when_database_unavailable(monkeypatch) -> None:
    async def fake_create_pool(*args, **kwargs):
        raise OSError("database unavailable")

    monkeypatch.setenv("GRAPHRAPPING_DATABASE_URL", "postgresql://localhost/graphrapping")
    monkeypatch.setattr(
        "src.web.review_summary_sidecar.asyncpg.create_pool",
        fake_create_pool,
    )

    assert asyncio.run(fetch_sidecar_summaries(["P1"])) == {}


def test_fetch_sidecar_summaries_fails_closed_on_slow_connection(monkeypatch) -> None:
    async def fake_create_pool(*args, **kwargs):
        await asyncio.sleep(0.05)

    monkeypatch.setenv("GRAPHRAPPING_DATABASE_URL", "postgresql://localhost/graphrapping")
    monkeypatch.setattr(
        "src.web.review_summary_sidecar.asyncpg.create_pool",
        fake_create_pool,
    )

    assert asyncio.run(fetch_sidecar_summaries(["P1"], timeout_seconds=0.001)) == {}
