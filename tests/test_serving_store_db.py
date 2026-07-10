"""
Phase 2.1: DBServingStore unit tests (fake asyncpg pool, no real DB).

Mirrors the fake pool/connection shape used in `test_retention_monitor.py`
(`pool.acquire() -> async with ... as conn`, `conn.fetch(query, *args)`), adapted
to route by the serving table name in the query.

Covers:
- lazy load on first access + periodic refresh keyed off an injectable clock,
- refresh reads the live table contents (pipeline update reflected in-cache),
- JSONB columns decode from JSON strings; TEXT[]/numeric/date normalize,
- mixed str|dict array elements survive intact (consumer contract §3.3),
- individual lookup by id (+ miss),
- concurrent first-access does not trigger duplicate refreshes,
- serve-stale-on-refresh-error: after a first successful load, a failing
  refresh serves the stale snapshot (+ warning) instead of erroring, defers the
  next attempt, and recovers on the next cycle; a first-load failure re-raises.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from decimal import Decimal
from typing import Any

import pytest

from src.web.serving_store import DBServingStore, extract_id


# ---------------------------------------------------------------------------
# Fake asyncpg pool / connection
# ---------------------------------------------------------------------------


class _FakeAcquireCtx:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    async def __aenter__(self) -> "_FakeConn":
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: "_FakeConn") -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)


class _FakeConn:
    """Routes `fetch` by serving-table substring. Returns whatever list the
    test currently has registered so refresh-picks-up-new-data can be exercised
    by mutating the lists between calls. Counts calls per table and yields
    control once per fetch so concurrent callers can interleave."""

    def __init__(self) -> None:
        self.products: list[dict[str, Any]] = []
        self.users: list[dict[str, Any]] = []
        self.product_fetches = 0
        self.user_fetches = 0
        # When set, fetch raises after counting the attempt — simulates a DB
        # outage for the serve-stale-on-refresh-error tests. Counting before
        # raising lets a test prove the fast path skips re-query on later reads.
        self.fail = False

    async def fetch(self, query: str, *_args: Any) -> list[dict[str, Any]]:
        await asyncio.sleep(0)  # force a scheduling point for concurrency tests
        if "serving_product_profile" in query:
            self.product_fetches += 1
            rows = self.products
        elif "serving_user_profile" in query:
            self.user_fetches += 1
            rows = self.users
        else:
            raise AssertionError(f"unexpected fetch query: {query!r}")
        if self.fail:
            raise RuntimeError("simulated DB outage")
        return [dict(row) for row in rows]


class _Clock:
    """Manually advanceable monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------


def _product_row(product_id: str = "p1") -> dict[str, Any]:
    """A serving_product_profile row as asyncpg would return it: JSONB columns
    as JSON *strings*, TEXT[] as lists, numeric as Decimal, dates as date."""
    return {
        "product_id": product_id,
        "source_product_id": product_id,
        "source_channel": "031",
        "source_key_type": "ecp_onln_prd_srno",
        "brand_id": "brand_hera",
        "brand_name": "헤라",
        "category_id": "cat_cushion",
        "category_name": "쿠션",
        "country_of_origin": "KR",
        "price": Decimal("19900"),
        "price_band": None,
        "variant_family_id": None,
        "representative_product_name": "헤라 블랙 쿠션",
        "main_benefit_ids": ["mb_cover"],
        "ingredient_ids": ["ing_niacinamide"],
        "brand_concept_ids": json.dumps(["concept:Brand:brand_hera"]),
        "category_concept_ids": json.dumps(["concept:Category:cat_cushion"]),
        "ingredient_concept_ids": json.dumps([]),
        "main_benefit_concept_ids": json.dumps([]),
        "top_bee_attr_ids": json.dumps([{"id": "bee_attr:coverage", "score": 3}]),
        # Mixed str|dict elements — consumer contract §3.3.
        "top_keyword_ids": json.dumps(
            ["keyword:plain_string", {"id": "keyword:kw_thin_spread", "score": 2}]
        ),
        "top_context_ids": json.dumps([]),
        "top_concern_pos_ids": json.dumps([]),
        "top_concern_neg_ids": json.dumps([]),
        "top_tool_ids": json.dumps([]),
        "top_comparison_product_ids": json.dumps([]),
        "top_coused_product_ids": json.dumps([]),
        "last_signal_at": datetime.datetime(2026, 6, 1, tzinfo=datetime.timezone.utc),
        "review_count_30d": 1,
        "review_count_90d": 2,
        "review_count_all": 3,
        "signal_support_count_all": 5,
        "source_review_count_6m": 100,
        "source_review_score_count_6m": 100,
        "source_avg_rating_6m": Decimal("4.900"),
        "source_review_min_date_6m": datetime.date(2025, 12, 1),
        "source_review_max_date_6m": datetime.date(2026, 6, 1),
        "source_review_count_all": 200,
        "source_review_score_count_all": 200,
        "source_avg_rating_all": Decimal("4.800"),
        "source_review_min_date_all": datetime.date(2024, 1, 1),
        "source_review_max_date_all": datetime.date(2026, 6, 1),
        "source_review_stats_source": "snowflake:f_prd_rv_hist:test",
    }


def _user_row(user_id: str = "u1") -> dict[str, Any]:
    return {
        "user_id": user_id,
        "age_band": "30s",
        "gender": "F",
        "skin_type": "oily",
        "skin_tone": None,
        "preferred_brand_ids": json.dumps([{"id": "concept:Brand:brand_hera", "weight": 1.0}]),
        "active_category_ids": json.dumps([]),
        "preferred_category_ids": json.dumps([{"id": "cat_cushion", "weight": 1.0}]),
        "preferred_ingredient_ids": json.dumps([]),
        "avoided_ingredient_ids": json.dumps([]),
        "concern_ids": json.dumps([]),
        "goal_ids": json.dumps([]),
        "preferred_bee_attr_ids": json.dumps([]),
        # Mixed str|dict elements again.
        "preferred_keyword_ids": json.dumps(["keyword:kw_thin_spread", {"id": "keyword:kw2"}]),
        "preferred_context_ids": json.dumps([]),
        "scoped_preference_ids": json.dumps([]),
        "recent_purchase_brand_ids": json.dumps([]),
        "repurchase_brand_ids": json.dumps([]),
        "repurchase_category_ids": json.dumps([]),
        "owned_product_ids": json.dumps([]),
        "owned_family_ids": json.dumps([]),
        "repurchased_family_ids": json.dumps([]),
    }


def _store(conn: _FakeConn, clock: _Clock, refresh_sec: float = 300) -> DBServingStore:
    return DBServingStore(_FakePool(conn), refresh_sec=refresh_sec, clock=clock)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lazy_load_on_first_access_then_cached_within_window() -> None:
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    conn.users = [_user_row("u1")]
    clock = _Clock()
    store = _store(conn, clock, refresh_sec=300)

    # No query issued until the first read.
    assert conn.product_fetches == 0

    products = await store.get_products()
    assert [p["product_id"] for p in products] == ["p1"]
    assert conn.product_fetches == 1
    assert conn.user_fetches == 1

    # Subsequent reads within the refresh window reuse the cache (no new query).
    await store.get_products()
    await store.get_users()
    await store.get_product("p1")
    assert conn.product_fetches == 1
    assert conn.user_fetches == 1


@pytest.mark.asyncio
async def test_refresh_after_window_reflects_new_table_contents() -> None:
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    clock = _Clock()
    store = _store(conn, clock, refresh_sec=300)

    assert [p["product_id"] for p in await store.get_products()] == ["p1"]
    assert conn.product_fetches == 1

    # Pipeline rewrites the serving table; still cached until the window passes.
    conn.products = [_product_row("p1"), _product_row("p2")]
    clock.advance(299)
    assert [p["product_id"] for p in await store.get_products()] == ["p1"]
    assert conn.product_fetches == 1

    # Past the refresh window → re-query picks up the new row.
    clock.advance(2)
    assert {p["product_id"] for p in await store.get_products()} == {"p1", "p2"}
    assert conn.product_fetches == 2


@pytest.mark.asyncio
async def test_jsonb_decoded_scalars_normalized() -> None:
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    store = _store(conn, _Clock())

    product = await store.get_product("p1")
    assert product is not None

    # JSONB string → Python list.
    assert product["brand_concept_ids"] == ["concept:Brand:brand_hera"]
    assert product["top_bee_attr_ids"] == [{"id": "bee_attr:coverage", "score": 3}]
    # TEXT[] left as a list.
    assert product["ingredient_ids"] == ["ing_niacinamide"]
    # numeric → float, date/timestamptz → ISO string.
    assert product["price"] == 19900.0 and isinstance(product["price"], float)
    assert product["source_avg_rating_6m"] == 4.9
    assert product["source_review_min_date_6m"] == "2025-12-01"
    assert product["last_signal_at"].startswith("2026-06-01T")


@pytest.mark.asyncio
async def test_mixed_str_dict_array_elements_survive_and_extract_id() -> None:
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    conn.users = [_user_row("u1")]
    store = _store(conn, _Clock())

    product = await store.get_product("p1")
    user = await store.get_user("u1")
    assert product is not None and user is not None

    # The mixed array is preserved exactly (a plain string AND a dict).
    assert product["top_keyword_ids"] == [
        "keyword:plain_string",
        {"id": "keyword:kw_thin_spread", "score": 2},
    ]
    # extract_id (consumer-contract helper) handles both element shapes.
    ids = [extract_id(item) for item in product["top_keyword_ids"]]
    assert ids == ["keyword:plain_string", "keyword:kw_thin_spread"]

    user_ids = [extract_id(item) for item in user["preferred_keyword_ids"]]
    assert user_ids == ["keyword:kw_thin_spread", "keyword:kw2"]


@pytest.mark.asyncio
async def test_lookup_by_id_and_miss() -> None:
    conn = _FakeConn()
    conn.products = [_product_row("p1"), _product_row("p2")]
    conn.users = [_user_row("u1")]
    store = _store(conn, _Clock())

    assert (await store.get_product("p2"))["product_id"] == "p2"  # type: ignore[index]
    assert await store.get_product("missing") is None
    assert (await store.get_user("u1"))["user_id"] == "u1"  # type: ignore[index]
    assert await store.get_user("missing") is None


@pytest.mark.asyncio
async def test_concurrent_first_access_refreshes_once() -> None:
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    conn.users = [_user_row("u1")]
    store = _store(conn, _Clock(), refresh_sec=300)

    # 20 concurrent first reads must collapse to a single refresh (one query
    # per serving table), proving the lock + double-check prevents duplicates.
    results = await asyncio.gather(*(store.get_products() for _ in range(20)))

    assert all([p["product_id"] for p in r] == ["p1"] for r in results)
    assert conn.product_fetches == 1
    assert conn.user_fetches == 1


@pytest.mark.asyncio
async def test_zero_refresh_sec_reloads_every_access() -> None:
    """refresh_sec=0 (never fresh) is a useful test/opt-out mode: every read
    re-queries so freshly written rows appear immediately."""
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    store = _store(conn, _Clock(), refresh_sec=0)

    await store.get_products()
    await store.get_products()
    assert conn.product_fetches == 2


@pytest.mark.asyncio
async def test_get_products_returns_a_copy_not_the_cache_list() -> None:
    """get_products/get_users hand out a shallow copy so a caller mutating the
    returned list cannot corrupt the shared refresh cache."""
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    conn.users = [_user_row("u1")]
    store = _store(conn, _Clock(), refresh_sec=300)

    products = await store.get_products()
    products.append({"product_id": "injected"})
    users = await store.get_users()
    users.clear()

    # Cache is intact on the next (still-fresh) read — no new query, no leak.
    assert [p["product_id"] for p in await store.get_products()] == ["p1"]
    assert [u["user_id"] for u in await store.get_users()] == ["u1"]
    assert conn.product_fetches == 1
    assert conn.user_fetches == 1


# ---------------------------------------------------------------------------
# serve-stale-on-refresh-error (fable_doc/03 §2.4(c))
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_error_after_first_load_serves_stale_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A refresh failure after a successful first load must NOT error the
    request: the stale snapshot is served, a warning (with exc info) is logged,
    and _loaded_at is bumped so reads within the window skip the failing pool."""
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    conn.users = [_user_row("u1")]
    clock = _Clock()
    store = _store(conn, clock, refresh_sec=300)

    # First load succeeds and populates the cache.
    assert [p["product_id"] for p in await store.get_products()] == ["p1"]
    assert conn.product_fetches == 1
    assert conn.user_fetches == 1

    # DB goes down; the cache ages past the refresh window.
    conn.fail = True
    clock.advance(301)

    with caplog.at_level(logging.WARNING):
        products = await store.get_products()

    # Stale data served, not an exception.
    assert [p["product_id"] for p in products] == ["p1"]
    # One (failed) refresh attempt was made; users fetch never ran (products
    # fetch raised first), so the users cache is untouched.
    assert conn.product_fetches == 2
    assert conn.user_fetches == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "a refresh failure must emit a warning"
    assert "refresh failed" in warnings[0].getMessage()
    assert warnings[0].exc_info is not None  # exception info attached

    # _loaded_at was reset on failure → a follow-up read within refresh_sec
    # hits the fast path and does NOT re-hit the (still-failing) pool.
    caplog.clear()
    again = await store.get_users()
    assert [u["user_id"] for u in again] == ["u1"]  # stale users still served
    assert conn.product_fetches == 2  # no new attempt
    assert conn.user_fetches == 1
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.asyncio
async def test_refresh_error_on_first_load_propagates() -> None:
    """With no prior successful load there is no snapshot to fall back on, so a
    first-load failure must propagate (serving an empty mart would be a silent
    wrong answer, not an honest failure)."""
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    conn.fail = True  # DB down from the very start
    store = _store(conn, _Clock(), refresh_sec=300)

    with pytest.raises(RuntimeError, match="simulated DB outage"):
        await store.get_products()


@pytest.mark.asyncio
async def test_refresh_recovers_on_next_cycle_after_failure() -> None:
    """After serving stale through an outage, the next post-window refresh that
    succeeds replaces the stale snapshot with fresh data."""
    conn = _FakeConn()
    conn.products = [_product_row("p1")]
    conn.users = [_user_row("u1")]
    clock = _Clock()
    store = _store(conn, clock, refresh_sec=300)

    assert [p["product_id"] for p in await store.get_products()] == ["p1"]

    # DB down; window passes; stale served.
    conn.fail = True
    clock.advance(301)
    assert [p["product_id"] for p in await store.get_products()] == ["p1"]

    # DB recovers and the pipeline has written a new row; after the next window
    # the refresh succeeds and the fresh data replaces the stale cache.
    conn.fail = False
    conn.products = [_product_row("p1"), _product_row("p2")]
    clock.advance(301)
    assert {p["product_id"] for p in await store.get_products()} == {"p1", "p2"}


# ---------------------------------------------------------------------------
# prefilter_candidate_ids — standard-fake-pool compatibility (fix: async with
# acquire, not UnitOfWork's bare `await pool.acquire()`) + avoided filtering.
# ---------------------------------------------------------------------------


class _PrefilterConn:
    """Models ``sql_prefilter_candidates``' avoided-exclusion SELECT: returns
    product_ids whose ingredient_ids / ingredient_concept_ids do not overlap the
    avoided list passed as $1. Exposes only ``fetch`` (like a raw connection)."""

    def __init__(self, products: list[dict[str, Any]]) -> None:
        self._products = products
        self.fetches = 0

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        assert "serving_product_profile" in query
        self.fetches += 1
        avoided = set(args[0]) if args else set()
        out: list[dict[str, Any]] = []
        for product in self._products:
            ingredients = {str(x) for x in (product.get("ingredient_ids") or [])}
            ingredients |= {str(x) for x in (product.get("ingredient_concept_ids") or [])}
            if avoided & ingredients:
                continue
            out.append({"product_id": product["product_id"]})
        return out


@pytest.mark.asyncio
async def test_prefilter_candidate_ids_filters_via_standard_fake_pool() -> None:
    """The refactor to `async with self._pool.acquire()` makes prefilter work
    with the standard fake pool (a bare `await pool.acquire()` would raise).
    Order is preserved and the avoided-ingredient product is dropped."""
    products = [
        {"product_id": "P_kw", "ingredient_concept_ids": []},
        {"product_id": "P_brand", "ingredient_concept_ids": []},
        {"product_id": "P_avoid", "ingredient_concept_ids": ["concept:Ingredient:badx"]},
    ]
    conn = _PrefilterConn(products)
    store = DBServingStore(_FakePool(conn), refresh_sec=0, clock=_Clock())  # type: ignore[arg-type]

    user_profile = {"user_id": "u", "avoided_ingredient_ids": [{"id": "concept:Ingredient:badx"}]}
    kept = await store.prefilter_candidate_ids(
        user_profile=user_profile,
        candidate_universe=["P_kw", "P_brand", "P_avoid"],
    )
    assert kept == ["P_kw", "P_brand"]
    assert conn.fetches == 1


@pytest.mark.asyncio
async def test_prefilter_candidate_ids_no_avoided_skips_query() -> None:
    conn = _PrefilterConn([])
    store = DBServingStore(_FakePool(conn), refresh_sec=0, clock=_Clock())  # type: ignore[arg-type]
    kept = await store.prefilter_candidate_ids(
        user_profile={"user_id": "u"},  # no avoided ingredient
        candidate_universe=["A", "B"],
    )
    assert kept == ["A", "B"]
    assert conn.fetches == 0
