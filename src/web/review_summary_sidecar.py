"""
Read-only access helpers for review-summary sidecar previews.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

import asyncpg

from src.db.connection import resolve_database_url


_MAX_SUMMARY_PREVIEW_CHARS = 1200
_SIDECAR_TIMEOUT_SECONDS = 2.0


def summarize_sidecar_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """Project a sidecar DB row to a safe API preview.

    The sidecar keeps raw ES documents and source identity metadata, but the
    web API only needs bounded summary text, match state, date, and review count.
    """
    if not row:
        return None

    normalized = _as_dict(row.get("normalized_summary"))
    long_doc = _as_dict(normalized.get("long"))
    short_doc = _as_dict(normalized.get("short"))
    review_count = _safe_int(long_doc.get("review_cnt"))
    if review_count is None:
        review_count = _safe_int(short_doc.get("review_cnt"))

    return {
        "product_id": _safe_text(row.get("product_id")),
        "match_status": _safe_text(row.get("match_status")),
        "an_date": _safe_text(row.get("an_date")),
        "long_summary": _summary_text(long_doc.get("summary")),
        "short_summary": _summary_text(short_doc.get("summary")),
        "review_count": review_count,
    }


async def fetch_sidecar_summaries(
    product_ids: list[str],
    *,
    database_url: str | None = None,
    timeout_seconds: float = _SIDECAR_TIMEOUT_SECONDS,
) -> dict[str, dict[str, Any]]:
    """Fetch sidecar previews for product IDs.

    This helper is intentionally read-only and fail-closed: absent DB settings,
    connection errors, query errors, and malformed rows all result in an empty or
    partial mapping instead of breaking demo API responses.
    """
    ids = _unique_product_ids(product_ids)
    if not ids:
        return {}

    try:
        dsn = resolve_database_url(database_url)
    except RuntimeError:
        return {}

    pool: asyncpg.Pool | None = None
    try:
        async with asyncio.timeout(timeout_seconds):
            pool = await asyncpg.create_pool(
                dsn,
                min_size=1,
                max_size=1,
                command_timeout=timeout_seconds,
            )
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT product_id, match_status, normalized_summary, an_date
                    FROM review_summary_sidecar
                    WHERE product_id = ANY($1::text[])
                    """,
                    ids,
                )
    except Exception:
        return {}
    finally:
        if pool is not None:
            with suppress(Exception):
                await asyncio.wait_for(pool.close(), timeout=timeout_seconds)

    summaries: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_dict = dict(row)
        preview = summarize_sidecar_row(row_dict)
        product_id = _safe_text(row_dict.get("product_id"))
        if preview is not None and product_id:
            summaries[product_id] = preview
    return summaries


def _unique_product_ids(product_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for product_id in product_ids:
        text = _safe_text(product_id)
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        with suppress(json.JSONDecodeError):
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
    return {}


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _summary_text(value: Any) -> str | None:
    text = _safe_text(value)
    if text is None:
        return None
    if len(text) <= _MAX_SUMMARY_PREVIEW_CHARS:
        return text
    return text[:_MAX_SUMMARY_PREVIEW_CHARS].rstrip()
