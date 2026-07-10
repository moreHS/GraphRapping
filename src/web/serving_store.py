"""
ServingStore: abstraction over the recommendation serving data source.

Phase 2.1 (fable_doc/03_improvement_plan.md §2.1, issue E1): the web/API surface
reads serving products/users through this interface instead of touching the
in-memory `DemoState` directly, so the same endpoints can be backed by either
the demo pipeline or the DB serving mart.

Two implementations:
- ``DemoServingStore`` wraps the in-memory ``DemoState`` (current behaviour,
  default mode). It reads a *live* state via a provider callable so tests that
  monkeypatch ``server.demo_state`` keep working.
- ``DBServingStore`` reads ``serving_product_profile`` / ``serving_user_profile``
  from a Postgres pool into a periodic-refresh in-memory cache. First access
  lazily loads; subsequent accesses reuse the cache until it ages past
  ``refresh_sec`` (``GRAPHRAPPING_SERVING_REFRESH_SEC``, default 300s). The
  refresh is asyncio-safe: concurrent callers cannot trigger duplicate refreshes.
  Availability over freshness on refresh errors: once a first load has
  succeeded, a later refresh that raises (e.g. a transient DB outage) does not
  fail requests — the store logs a warning and keeps serving the last-good
  (stale) snapshot until the next refresh cycle. The very first load is the
  exception and re-raises, since serving an empty mart would be a silent,
  misleading success rather than an honest failure.

Array-element contract (``docs/architecture/db_consumer_contract.md`` §3.3):
serving-profile array columns may hold plain strings OR ``{"id": ..., ...}``
dicts, mixed per field. ``extract_id`` mirrors the consumer contract's helper
for callers that must key on ids; the stores themselves pass array payloads
through unchanged (JSONB is decoded but element shapes are preserved) so the
recommendation layer's own str|dict handling keeps working.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from src.mart.serving_profile_schema import (
    SERVING_PRODUCT_PROFILE_COLUMNS,
    SERVING_USER_PROFILE_COLUMNS,
)
from src.rec.scoped_preferences import GLOBAL_SCOPES, has_scoped_preferences
from src.web.state import DemoState

DEFAULT_SERVING_REFRESH_SEC = 300

logger = logging.getLogger(__name__)


def extract_id(item: Any) -> str | None:
    """Consumer-contract §3.3 id extractor.

    Serving-profile array elements may be a plain string or a
    ``{"id": ..., ...}`` dict. Returns the id as a string, or ``None`` when the
    element carries no usable id.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        got = item.get("id")
        return got if isinstance(got, str) else None
    return None


def _globally_avoided_ingredient_ids(user_profile: dict[str, Any]) -> list[str]:
    """Avoided-ingredient ids that apply to EVERY product, regardless of category.

    Phase 2.2 recall-safety: the in-memory ``generate_candidates`` hard-filters
    avoided ingredients *scope-aware* (recomputed per product category group),
    but the SQL prefilter has no per-product scope and applies avoided ids
    globally. Pushing a category-scoped avoided id to SQL would wrongly exclude
    products outside that scope — a recall loss vs the full traversal. So only
    globally-scoped avoided ids are eligible for the SQL prefilter; scoped ones
    are left to the in-memory pass, which honours their scope.
    """
    if not has_scoped_preferences(user_profile):
        ids = [extract_id(item) for item in (user_profile.get("avoided_ingredient_ids") or [])]
        return [i for i in ids if i]
    out: list[str] = []
    for item in user_profile.get("scoped_preference_ids") or []:
        if (
            isinstance(item, dict)
            and item.get("edge_type") == "AVOIDS_INGREDIENT"
            and item.get("scope_group") in GLOBAL_SCOPES
        ):
            value = item.get("id")
            if value:
                out.append(str(value))
    return out


@runtime_checkable
class ServingStore(Protocol):
    """Read interface for recommendation serving data (products + users).

    Covers the access patterns the web layer needs: list-all and
    lookup-by-id for both products and users.
    """

    async def get_products(self) -> list[dict[str, Any]]: ...

    async def get_product(self, product_id: str) -> dict[str, Any] | None: ...

    async def get_users(self) -> list[dict[str, Any]]: ...

    async def get_user(self, user_id: str) -> dict[str, Any] | None: ...


class DemoServingStore:
    """``ServingStore`` backed by the in-memory demo pipeline state.

    Reads the state through a provider callable (rather than capturing it once)
    so that rebinding the module-level ``demo_state`` — as the pipeline reload
    and the existing tests do — is always reflected.
    """

    def __init__(self, state_provider: Callable[[], DemoState]) -> None:
        self._state_provider = state_provider

    async def get_products(self) -> list[dict[str, Any]]:
        return list(self._state_provider().serving_products)

    async def get_product(self, product_id: str) -> dict[str, Any] | None:
        for product in self._state_provider().serving_products:
            if product.get("product_id") == product_id:
                return product
        return None

    async def get_users(self) -> list[dict[str, Any]]:
        return list(self._state_provider().serving_users)

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        for user in self._state_provider().serving_users:
            if user.get("user_id") == user_id:
                return user
        return None

    async def prefilter_candidate_ids(
        self,
        *,
        user_profile: dict[str, Any],
        candidate_universe: list[str],
    ) -> list[str]:
        """Demo mode keeps the full-traversal path (issue E2): the in-memory
        ``generate_candidates`` applies every hard filter itself, so no SQL
        pre-narrowing is done here. Returns the universe unchanged, which makes
        the prefiltered path exactly equivalent to full traversal."""
        return list(candidate_universe)


# JSONB columns per serving table. asyncpg returns JSONB as a JSON *string*
# (no custom codec is registered on this project's pools), so these must be
# json-decoded on read. TEXT[] columns (main_benefit_ids, ingredient_ids) come
# back as Python lists already and are left untouched.
_PRODUCT_JSONB_COLUMNS: frozenset[str] = frozenset({
    "brand_concept_ids",
    "category_concept_ids",
    "ingredient_concept_ids",
    "main_benefit_concept_ids",
    "top_bee_attr_ids",
    "top_keyword_ids",
    "top_context_ids",
    "top_concern_pos_ids",
    "top_concern_neg_ids",
    "top_tool_ids",
    "top_comparison_product_ids",
    "top_coused_product_ids",
})

_USER_JSONB_COLUMNS: frozenset[str] = frozenset({
    "preferred_brand_ids",
    "active_category_ids",
    "preferred_category_ids",
    "preferred_ingredient_ids",
    "avoided_ingredient_ids",
    "concern_ids",
    "goal_ids",
    "preferred_bee_attr_ids",
    "preferred_keyword_ids",
    "preferred_context_ids",
    "scoped_preference_ids",
    "recent_purchase_brand_ids",
    "repurchase_brand_ids",
    "repurchase_category_ids",
    "owned_product_ids",
    "owned_family_ids",
    "repurchased_family_ids",
})


def _decode_jsonb(value: Any) -> Any:
    """Decode a JSONB column value into a Python object.

    SQL NULL / empty / JSON ``null`` all coerce to an empty list, matching the
    demo builder which always emits array payloads for these columns. Values
    already decoded to a list/dict (e.g. a fake pool in tests, or a pool with a
    JSON codec) are passed through unchanged.
    """
    if value is None:
        return []
    if isinstance(value, str):
        if not value:
            return []
        decoded = json.loads(value)
        return decoded if decoded is not None else []
    return value


def _normalize_scalar(value: Any) -> Any:
    """Make non-JSONB scalars JSON-friendly and consistent with the demo path.

    ``numeric`` → float, ``date``/``timestamptz`` → ISO string. Lists (TEXT[])
    and plain scalars pass through unchanged.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value


def _decode_row(record: Any, jsonb_columns: frozenset[str]) -> dict[str, Any]:
    row: dict[str, Any] = dict(record)
    for key in list(row):
        if key in jsonb_columns:
            row[key] = _decode_jsonb(row[key])
        else:
            row[key] = _normalize_scalar(row[key])
    return row


class DBServingStore:
    """``ServingStore`` backed by ``serving_*_profile`` tables with a refresh cache.

    The cache is loaded lazily on first access and refreshed once it ages past
    ``refresh_sec``. A pipeline that rewrites the serving tables is therefore
    reflected without an API restart, within one refresh interval.

    Refresh errors after the first successful load do not surface to callers:
    the store logs a warning and keeps serving the stale snapshot, trading a
    little extra staleness for availability during a transient DB outage. The
    first load re-raises instead (there is no snapshot to fall back on). See
    ``_ensure_loaded`` for the failure-handling rationale.
    """

    def __init__(
        self,
        pool: Any,
        *,
        refresh_sec: float = DEFAULT_SERVING_REFRESH_SEC,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._pool = pool
        self._refresh_sec = max(0.0, float(refresh_sec))
        self._clock = clock or time.monotonic
        self._lock = asyncio.Lock()
        self._loaded_at: float | None = None
        self._products: list[dict[str, Any]] = []
        self._products_by_id: dict[str, dict[str, Any]] = {}
        self._users: list[dict[str, Any]] = []
        self._users_by_id: dict[str, dict[str, Any]] = {}

    def _is_fresh(self) -> bool:
        return (
            self._loaded_at is not None
            and (self._clock() - self._loaded_at) < self._refresh_sec
        )

    async def _ensure_loaded(self) -> None:
        # Fast path: cache is present and still fresh — no lock, no query.
        if self._is_fresh():
            return
        async with self._lock:
            # Re-check under the lock: a concurrent caller may have just
            # refreshed while we waited, so we must not refresh again.
            if self._is_fresh():
                return
            try:
                await self._refresh()
            except Exception:
                # First load has never succeeded: there is no snapshot to fall
                # back on. Serving an empty mart would look like a successful
                # (but wrong) response, so surface the failure explicitly.
                if self._loaded_at is None:
                    raise
                # A previously-loaded, now-stale snapshot exists. Prefer
                # availability: keep serving it instead of 500-ing a request
                # that only a transient refresh blip would otherwise take down.
                # (_refresh mutates no state until both fetches succeed, so the
                # last-good cache is fully intact here.)
                logger.warning(
                    "DBServingStore refresh failed; serving the stale cache "
                    "and deferring the next attempt ~%.0fs.",
                    self._refresh_sec,
                    exc_info=True,
                )
                # Reset the freshness clock even though the refresh failed.
                # Tradeoff: the snapshot may now live up to one extra
                # refresh_sec, but this avoids a re-query storm — without it,
                # every request during the outage would miss the fast path and
                # re-hit the failing pool. Recovery is automatic on the next
                # cycle, when a succeeding refresh replaces the stale data.
                self._loaded_at = self._clock()

    async def _refresh(self) -> None:
        products = await self._fetch_products()
        users = await self._fetch_users()
        self._products = products
        self._products_by_id = {
            p["product_id"]: p for p in products if p.get("product_id")
        }
        self._users = users
        self._users_by_id = {u["user_id"]: u for u in users if u.get("user_id")}
        self._loaded_at = self._clock()

    async def _fetch_products(self) -> list[dict[str, Any]]:
        columns = ", ".join(SERVING_PRODUCT_PROFILE_COLUMNS)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {columns} FROM serving_product_profile WHERE is_active = true"
            )
        return [_decode_row(row, _PRODUCT_JSONB_COLUMNS) for row in rows]

    async def _fetch_users(self) -> list[dict[str, Any]]:
        columns = ", ".join(SERVING_USER_PROFILE_COLUMNS)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {columns} FROM serving_user_profile WHERE is_active = true"
            )
        return [_decode_row(row, _USER_JSONB_COLUMNS) for row in rows]

    async def get_products(self) -> list[dict[str, Any]]:
        await self._ensure_loaded()
        # Shallow copy so callers can't mutate the shared refresh cache list
        # (consistent with DemoServingStore, which copies serving_products).
        return list(self._products)

    async def get_product(self, product_id: str) -> dict[str, Any] | None:
        await self._ensure_loaded()
        return self._products_by_id.get(product_id)

    async def get_users(self) -> list[dict[str, Any]]:
        await self._ensure_loaded()
        return list(self._users)

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        await self._ensure_loaded()
        return self._users_by_id.get(user_id)

    async def prefilter_candidate_ids(
        self,
        *,
        user_profile: dict[str, Any],
        candidate_universe: list[str],
    ) -> list[str]:
        """Phase 2.2 recall-safe SQL prefilter: drop products whose ingredients
        overlap the user's globally-avoided ingredients — the one hard filter
        the in-memory full traversal applies identically — while preserving
        ``candidate_universe`` order.

        Only the avoided hard filter is pushed to SQL (positive concept gate
        left off); see ``sql_prefilter_candidates``. The result is therefore a
        superset of every candidate the full traversal keeps, so the downstream
        ``generate_candidates_prefiltered`` yields an identical candidate set,
        scores, and evidence families. When the user has no globally-avoided
        ingredient the universe is returned unchanged (no query)."""
        avoided = _globally_avoided_ingredient_ids(user_profile)
        if not avoided:
            return list(candidate_universe)

        # Lazy import mirrors server.py's db.connection usage and keeps the
        # module import surface free of the repo/asyncpg layer in demo mode.
        from src.db.repos.mart_repo import sql_prefilter_candidates

        # Read-only prefilter (a single SELECT via sql_prefilter_candidates,
        # which only calls `.fetch`), so no transaction is needed. Acquiring a
        # connection with `async with pool.acquire()` — the same shape as
        # `_fetch_products` — keeps this compatible with the standard fake pool
        # used across the serving-store tests (a bare `await pool.acquire()`,
        # as UnitOfWork does, is not). A raw asyncpg connection exposes the same
        # `.fetch` sql_prefilter_candidates needs.
        async with self._pool.acquire() as conn:
            safe_ids = await sql_prefilter_candidates(
                conn,
                avoided_ingredient_ids=avoided,
                preferred_concept_ids=[],
                max_candidates=None,
            )
        # Intersect with the (cached, is_active) universe, preserving its order.
        # sql_prefilter_candidates queries the live table and may include rows
        # absent from the cache; the intersection confines the result to the
        # caller's universe.
        safe = set(safe_ids)
        return [pid for pid in candidate_universe if pid in safe]
