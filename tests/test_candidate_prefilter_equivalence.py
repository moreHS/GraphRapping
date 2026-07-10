"""Phase 2.2 (fable_doc/03_improvement_plan.md §2.2, issue E2) — SQL prefilter
equivalence with the in-memory full traversal.

The DB-mode serving path promotes the SQL prefilter to the default candidate
path (``ServingStore.prefilter_candidate_ids`` -> ``sql_prefilter_candidates``
-> ``generate_candidates_prefiltered``). This module proves the prefiltered path
returns the *same* candidate set, per-candidate overlap score, and evidence
families as the full traversal (``generate_candidates`` over every product).

Two layers:

1. Pure-Python (no DB): a documented mirror of the SQL prefilter WHERE clause,
   fed the *production* avoided-id extraction (``_globally_avoided_ingredient_ids``),
   is applied to the dense_golden serving profiles and its
   ``generate_candidates_prefiltered`` output is asserted identical to full
   traversal, for every golden profile x category tab and both modes.

   It also (a) documents *why* the SQL positive concept gate is disabled for the
   default path — enabling it drops candidates the full traversal keeps — and
   (b) verifies that a category-scoped avoided ingredient is NOT pushed to the
   global SQL filter (recall-safety of the scope handling).

2. PG-gated (GRAPHRAPPING_TEST_DATABASE_URL): the *actual* SQL runs against
   Postgres and the prefiltered path is asserted equal to full traversal for a
   fixture that includes a review-graph-only candidate (which the positive gate
   would have dropped) and an avoided-ingredient product (which both drop).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import uuid
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from src.common.enums import RecommendationMode
from src.jobs.run_full_load import FullLoadConfig, run_full_load
from src.rec.candidate_generator import (
    CandidateProduct,
    generate_candidates,
    generate_candidates_prefiltered,
)
from src.rec.category_groups import (
    RECOMMEND_CATEGORY_DEFS,
    classify_product_category_group,
)
from src.rec.product_profile_enrichment import enrich_product_profiles_by_master
from src.web.serving_store import _globally_avoided_ingredient_ids

ROOT = Path(__file__).resolve().parents[1]
DENSE_DIR = ROOT / "mockdata" / "dense_golden"
GOLDEN_PROFILE_IDS = {
    "user_dry_30f",
    "user_brand_null_cat",
    "user_sensitive_40f",
    "user_scalp_care_50m",
    "user_fragrance_60f",
    "user_makeup_matte_50m",
}
CATEGORY_TABS = tuple(str(item["group"]) for item in RECOMMEND_CATEGORY_DEFS)
SERVER_CANDIDATE_LIMIT = 50


# ---------------------------------------------------------------------------
# Pure-Python mirror of mart_repo.sql_prefilter_candidates
# ---------------------------------------------------------------------------


def _sql_prefilter_mirror(
    user_profile: dict[str, Any],
    product_map: dict[str, dict[str, Any]],
    universe_ids: list[str],
    *,
    preferred_concept_ids: tuple[str, ...] = (),
    max_candidates: int | None = None,
) -> list[str]:
    """Mirror ``sql_prefilter_candidates`` applied to the DBServingStore path.

    Uses the *production* ``_globally_avoided_ingredient_ids`` for the avoided
    set (so the scope handling under test is the real one), then reimplements
    the SQL WHERE predicate: avoided exclusion across raw ``ingredient_ids`` and
    ``ingredient_concept_ids``, plus the optional positive concept gate. Iterates
    ``universe_ids`` in order, matching the store's order-preserving intersection.
    """
    avoided = set(_globally_avoided_ingredient_ids(user_profile))
    preferred = set(preferred_concept_ids)
    out: list[str] = []
    for pid in universe_ids:
        product = product_map[pid]
        ing_raw = {str(x) for x in (product.get("ingredient_ids") or [])}
        ing_concept = {str(x) for x in (product.get("ingredient_concept_ids") or [])}
        if avoided & (ing_raw | ing_concept):
            continue
        if preferred:
            positive: set[str] = set()
            for column in (
                "brand_concept_ids",
                "category_concept_ids",
                "ingredient_concept_ids",
                "main_benefit_concept_ids",
            ):
                positive |= {str(x) for x in (product.get(column) or [])}
            if not (preferred & positive):
                continue
        out.append(pid)
        if max_candidates is not None and len(out) >= max_candidates:
            break
    return out


def _naive_preferred_concept_ids(user_profile: dict[str, Any]) -> tuple[str, ...]:
    """The concept-id set a naive positive gate would build from a user
    (brand/category/ingredient/goal preferences). Used only to demonstrate the
    recall loss that motivated disabling the positive gate."""
    ids: set[str] = set()
    for field in (
        "preferred_brand_ids",
        "preferred_category_ids",
        "preferred_ingredient_ids",
        "goal_ids",
    ):
        for item in user_profile.get(field) or []:
            value = item.get("id") if isinstance(item, dict) else item
            if value:
                ids.add(str(value))
    for item in user_profile.get("scoped_preference_ids") or []:
        if isinstance(item, dict) and item.get("edge_type") in {
            "PREFERS_BRAND",
            "PREFERS_CATEGORY",
            "PREFERS_INGREDIENT",
            "WANTS_GOAL",
        }:
            value = item.get("id")
            if value:
                ids.add(str(value))
    return tuple(sorted(ids))


def _signature(candidates: list[CandidateProduct]) -> list[tuple[str, float, tuple[str, ...]]]:
    """Order-sensitive fingerprint: (product_id, overlap_score, evidence families).

    Captures exactly what §2.2 requires to match — candidate set, per-candidate
    score, and evidence family — in the order the candidate list is returned.
    """
    return [
        (
            candidate.product_id,
            round(candidate.overlap_score, 6),
            tuple(sorted(candidate.eligibility.evidence_families)),
        )
        for candidate in candidates
    ]


# ---------------------------------------------------------------------------
# Dense golden serving data (built once)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dense_serving() -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    product_records = json.loads((DENSE_DIR / "product_catalog_es.json").read_text(encoding="utf-8"))
    user_profiles = json.loads((DENSE_DIR / "user_profiles_normalized.json").read_text(encoding="utf-8"))
    review_path = DENSE_DIR / "review_triples_raw.json"

    with contextlib.redirect_stdout(io.StringIO()):
        result = run_full_load(FullLoadConfig(
            review_json_path=str(review_path),
            product_es_records=product_records,
            user_profiles=user_profiles,
            kg_mode="on",
        ))
    serving_products = enrich_product_profiles_by_master(
        result.serving_products,
        result.batch_result.get("product_masters", {}),
    )
    product_map = {str(p["product_id"]): p for p in serving_products}
    serving_users = list(result.serving_users)
    return product_map, serving_users


def _universe_ids(product_map: dict[str, dict[str, Any]], category_group: str) -> list[str]:
    if category_group == "all":
        return list(product_map.keys())
    return [
        pid
        for pid, product in product_map.items()
        if classify_product_category_group(product) == category_group
    ]


# ---------------------------------------------------------------------------
# Layer 1: pure-Python equivalence over dense_golden
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [RecommendationMode.EXPLORE, RecommendationMode.STRICT])
def test_prefilter_equivalent_to_full_traversal_all_profiles(
    dense_serving: tuple[dict[str, dict[str, Any]], list[dict[str, Any]]],
    mode: RecommendationMode,
) -> None:
    """Recall-safe prefilter (avoided-only, no truncation) == full traversal for
    every golden profile x category tab: same candidates, scores, and families."""
    product_map, serving_users = dense_serving
    assert {str(u.get("user_id")) for u in serving_users} == GOLDEN_PROFILE_IDS

    mismatches: list[str] = []
    for user in serving_users:
        uid = str(user.get("user_id"))
        for tab in CATEGORY_TABS:
            universe = _universe_ids(product_map, tab)

            full = generate_candidates(
                user,
                [product_map[pid] for pid in universe],
                mode,
                max_candidates=SERVER_CANDIDATE_LIMIT,
            )
            prefiltered_ids = _sql_prefilter_mirror(user, product_map, universe)
            pre = generate_candidates_prefiltered(
                user,
                prefiltered_ids,
                product_map,
                mode,
                max_candidates=SERVER_CANDIDATE_LIMIT,
            )

            if _signature(full) != _signature(pre):
                full_ids = {c.product_id for c in full}
                pre_ids = {c.product_id for c in pre}
                mismatches.append(
                    f"{uid}/{tab}: only_full={sorted(full_ids - pre_ids)} "
                    f"only_pre={sorted(pre_ids - full_ids)}"
                )

    assert not mismatches, "prefilter/full-traversal divergence:\n" + "\n".join(mismatches)


def test_prefilter_is_subset_and_only_drops_avoided(
    dense_serving: tuple[dict[str, dict[str, Any]], list[dict[str, Any]]],
) -> None:
    """The recall-safe prefilter returns a subset of the universe and only ever
    drops products with an avoided-ingredient conflict (never an eligible one).

    On the dense_golden fixture no product carries a user's avoided ingredient,
    so this is expected to hold vacuously (nothing dropped); the invariant still
    guards against a future prefilter change that would drop a non-avoided
    product. Actual narrowing + equivalence is exercised synthetically below."""
    product_map, serving_users = dense_serving
    for user in serving_users:
        avoided = set(_globally_avoided_ingredient_ids(user))
        universe = _universe_ids(product_map, "all")
        kept = set(_sql_prefilter_mirror(user, product_map, universe))
        assert kept <= set(universe)
        for pid in set(universe) - kept:
            product = product_map[pid]
            product_ings = {str(x) for x in (product.get("ingredient_ids") or [])}
            product_ings |= {str(x) for x in (product.get("ingredient_concept_ids") or [])}
            assert avoided & product_ings, (
                f"{pid} dropped by prefilter without an avoided-ingredient conflict"
            )


def test_prefilter_narrows_and_stays_equivalent_when_avoided_matches(
    dense_serving: tuple[dict[str, dict[str, Any]], list[dict[str, Any]]],
) -> None:
    """Synthetic narrowing: inject a globally-avoided ingredient that a real
    candidate carries. The prefilter must drop exactly that product, the full
    traversal must drop it too (avoided hard filter), and the two paths must
    still produce an identical candidate set."""
    import copy

    product_map, serving_users = dense_serving
    base = next(u for u in serving_users if str(u.get("user_id")) == "user_sensitive_40f")
    universe = _universe_ids(product_map, "all")

    base_candidates = generate_candidates(
        base, [product_map[pid] for pid in universe],
        RecommendationMode.EXPLORE, max_candidates=SERVER_CANDIDATE_LIMIT,
    )
    target = next(
        c for c in base_candidates
        if product_map[c.product_id].get("ingredient_concept_ids")
    )
    target_pid = target.product_id
    target_ingredient = str(product_map[target_pid]["ingredient_concept_ids"][0])

    user = copy.deepcopy(base)
    scoped = list(user.get("scoped_preference_ids") or [])
    scoped.append(
        {"id": target_ingredient, "edge_type": "AVOIDS_INGREDIENT", "scope_group": None}
    )
    user["scoped_preference_ids"] = scoped
    assert target_ingredient in _globally_avoided_ingredient_ids(user)

    kept = _sql_prefilter_mirror(user, product_map, universe)
    assert target_pid in universe
    assert target_pid not in kept, "prefilter must drop the avoided-ingredient product"

    full = generate_candidates(
        user, [product_map[pid] for pid in universe],
        RecommendationMode.EXPLORE, max_candidates=SERVER_CANDIDATE_LIMIT,
    )
    pre = generate_candidates_prefiltered(
        user, kept, product_map, RecommendationMode.EXPLORE,
        max_candidates=SERVER_CANDIDATE_LIMIT,
    )
    assert target_pid not in {c.product_id for c in full}
    assert _signature(full) == _signature(pre)
    assert pre, "expected remaining candidates after narrowing (non-vacuous)"


def test_naive_positive_gate_would_lose_recall(
    dense_serving: tuple[dict[str, dict[str, Any]], list[dict[str, Any]]],
) -> None:
    """Regression guard for the §2.2 decision: a positive concept gate over
    brand/category/ingredient/main_benefit drops candidates the full traversal
    keeps (review-graph / semantic / purchase / catalog-text channels). This is
    why the default path passes preferred_concept_ids=[]; if someone re-enables
    the gate this test documents the recall it costs."""
    product_map, serving_users = dense_serving
    total_lost = 0
    for user in serving_users:
        universe = _universe_ids(product_map, "all")
        full_ids = {
            c.product_id
            for c in generate_candidates(
                user,
                [product_map[pid] for pid in universe],
                RecommendationMode.EXPLORE,
                max_candidates=SERVER_CANDIDATE_LIMIT,
            )
        }
        preferred = _naive_preferred_concept_ids(user)
        gated_ids = {
            c.product_id
            for c in generate_candidates_prefiltered(
                user,
                _sql_prefilter_mirror(
                    user, product_map, universe, preferred_concept_ids=preferred
                ),
                product_map,
                RecommendationMode.EXPLORE,
                max_candidates=SERVER_CANDIDATE_LIMIT,
            )
        }
        # The positive gate can only ever be a subset of full traversal.
        assert gated_ids <= full_ids
        total_lost += len(full_ids - gated_ids)
    assert total_lost > 0, (
        "expected the naive positive gate to lose recall on the golden profiles"
    )


def test_category_scoped_avoided_ingredient_not_pushed_to_global_filter(
    dense_serving: tuple[dict[str, dict[str, Any]], list[dict[str, Any]]],
) -> None:
    """A category-scoped avoided ingredient must NOT be applied globally by the
    SQL prefilter, otherwise it would drop products outside its scope that the
    scope-aware full traversal keeps (recall loss)."""
    product_map, _ = dense_serving
    # Pick a real product and use one of its ingredient concept ids as the
    # scoped-avoided target, then confirm the product survives when the scope
    # does not match it globally.
    target_pid = next(
        pid for pid, p in product_map.items() if (p.get("ingredient_concept_ids") or [])
    )
    target_ingredient = str(product_map[target_pid]["ingredient_concept_ids"][0])

    scoped_user = {
        "user_id": "synthetic_scoped_avoid",
        "scoped_preference_ids": [
            {
                "id": target_ingredient,
                "edge_type": "AVOIDS_INGREDIENT",
                # scope restricted to a single category group.
                "scope_group": "makeup",
            }
        ],
    }
    # Production extraction must treat a category-scoped avoided id as NOT global.
    assert _globally_avoided_ingredient_ids(scoped_user) == []

    universe = _universe_ids(product_map, "all")
    kept = _sql_prefilter_mirror(scoped_user, product_map, universe)
    assert target_pid in kept, (
        "category-scoped avoided ingredient wrongly excluded the product globally"
    )
    # Full traversal (scope-aware) also keeps it, so the prefiltered path matches.
    full = generate_candidates(
        scoped_user,
        [product_map[pid] for pid in universe],
        RecommendationMode.EXPLORE,
        max_candidates=SERVER_CANDIDATE_LIMIT,
    )
    pre = generate_candidates_prefiltered(
        scoped_user, kept, product_map, RecommendationMode.EXPLORE,
        max_candidates=SERVER_CANDIDATE_LIMIT,
    )
    assert _signature(full) == _signature(pre)


# ---------------------------------------------------------------------------
# Layer 2: PG-gated equivalence with the real SQL
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = os.environ.get("GRAPHRAPPING_TEST_DATABASE_URL")

pgmark = pytest.mark.skipif(
    TEST_DATABASE_URL is None,
    reason="Set GRAPHRAPPING_TEST_DATABASE_URL to run Postgres integration tests.",
)
pytestmark = pytest.mark.timeout(120)


@pytest_asyncio.fixture()
async def pg_pool():
    assert TEST_DATABASE_URL is not None
    schema = f"graphrapping_p22_{uuid.uuid4().hex}"
    admin = await asyncpg.connect(TEST_DATABASE_URL)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
    finally:
        await admin.close()

    pool = None
    try:
        pool = await asyncpg.create_pool(
            TEST_DATABASE_URL,
            min_size=1,
            max_size=1,
            server_settings={"search_path": schema},
        )
        yield pool
    finally:
        if pool is not None:
            await pool.close()
        admin = await asyncpg.connect(TEST_DATABASE_URL)
        try:
            await admin.execute(f"DROP SCHEMA {schema} CASCADE")
        finally:
            await admin.close()


async def _insert_serving_product(pool, product_id: str, **arrays: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO serving_product_profile (
                product_id, ingredient_ids, ingredient_concept_ids,
                brand_concept_ids, category_concept_ids, main_benefit_concept_ids,
                top_keyword_ids, review_count_all
            ) VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb, $7::jsonb, $8)
            """,
            product_id,
            arrays.get("ingredient_ids") or [],
            json.dumps(arrays.get("ingredient_concept_ids") or []),
            json.dumps(arrays.get("brand_concept_ids") or []),
            json.dumps(arrays.get("category_concept_ids") or []),
            json.dumps(arrays.get("main_benefit_concept_ids") or []),
            json.dumps(arrays.get("top_keyword_ids") or []),
            arrays.get("review_count_all", 10),
        )


@pgmark
@pytest.mark.asyncio
async def test_pg_prefilter_path_equivalent_to_full_traversal(pg_pool) -> None:
    """Real SQL prefilter (avoided-only) + generate_candidates_prefiltered ==
    full traversal generate_candidates, including a review-graph-only candidate
    that a positive concept gate would have dropped."""
    from src.db.migrate import migrate
    from src.db.repos.mart_repo import sql_prefilter_candidates
    from src.db.unit_of_work import UnitOfWork

    pool = pg_pool
    await migrate(pool)

    # P_kw: eligible ONLY via a review-graph keyword (no brand/cat/ing/benefit
    # concept overlap) — the case the positive gate misses.
    await _insert_serving_product(
        pool, "P_kw",
        top_keyword_ids=[{"id": "concept:Keyword:kw_moist", "score": 0.9}],
    )
    # P_brand: eligible via brand concept overlap.
    await _insert_serving_product(
        pool, "P_brand",
        brand_concept_ids=["concept:Brand:b1"],
    )
    # P_avoid: contains the avoided ingredient → dropped by both paths.
    await _insert_serving_product(
        pool, "P_avoid",
        ingredient_concept_ids=["concept:Ingredient:badx"],
        brand_concept_ids=["concept:Brand:b1"],
    )

    user_profile: dict[str, Any] = {
        "user_id": "U_pg",
        "avoided_ingredient_ids": [{"id": "concept:Ingredient:badx"}],
        "preferred_brand_ids": [{"id": "concept:Brand:b1"}],
        "preferred_keyword_ids": [{"id": "concept:Keyword:kw_moist"}],
    }
    product_map = {
        "P_kw": {"product_id": "P_kw", "top_keyword_ids": [{"id": "concept:Keyword:kw_moist", "score": 0.9}], "review_count_all": 10},
        "P_brand": {"product_id": "P_brand", "brand_concept_ids": ["concept:Brand:b1"], "review_count_all": 10},
        "P_avoid": {"product_id": "P_avoid", "ingredient_concept_ids": ["concept:Ingredient:badx"], "brand_concept_ids": ["concept:Brand:b1"], "review_count_all": 10},
    }
    universe = ["P_kw", "P_brand", "P_avoid"]

    # Prefiltered path via the REAL SQL (avoided-only, no truncation).
    async with UnitOfWork(pool) as uow:
        safe_ids = await sql_prefilter_candidates(
            uow,
            avoided_ingredient_ids=_globally_avoided_ingredient_ids(user_profile),
            preferred_concept_ids=[],
            max_candidates=None,
        )
    prefiltered_ids = [pid for pid in universe if pid in set(safe_ids)]
    pre = generate_candidates_prefiltered(
        user_profile, prefiltered_ids, product_map, RecommendationMode.EXPLORE,
        max_candidates=SERVER_CANDIDATE_LIMIT,
    )
    full = generate_candidates(
        user_profile, [product_map[pid] for pid in universe],
        RecommendationMode.EXPLORE, max_candidates=SERVER_CANDIDATE_LIMIT,
    )

    assert "P_avoid" not in safe_ids
    assert _signature(full) == _signature(pre)
    # The review-graph-only product survived (recall the positive gate would lose).
    assert "P_kw" in {c.product_id for c in pre}


@pgmark
@pytest.mark.asyncio
async def test_pg_db_serving_store_prefilter_candidate_ids(pg_pool) -> None:
    """DBServingStore.prefilter_candidate_ids runs end-to-end against real
    Postgres via `async with pool.acquire()` (the refactor away from UnitOfWork).
    It must drop the avoided-ingredient product and preserve universe order —
    the same result the raw ``sql_prefilter_candidates`` call produces above."""
    from src.db.migrate import migrate
    from src.web.serving_store import DBServingStore

    pool = pg_pool
    await migrate(pool)
    await _insert_serving_product(
        pool, "P_kw", top_keyword_ids=[{"id": "concept:Keyword:kw_moist", "score": 0.9}]
    )
    await _insert_serving_product(pool, "P_brand", brand_concept_ids=["concept:Brand:b1"])
    await _insert_serving_product(
        pool, "P_avoid",
        ingredient_concept_ids=["concept:Ingredient:badx"],
        brand_concept_ids=["concept:Brand:b1"],
    )

    user_profile: dict[str, Any] = {
        "user_id": "U_pg",
        "avoided_ingredient_ids": [{"id": "concept:Ingredient:badx"}],
    }
    # refresh_sec=0: prefilter queries the live table directly (no cache load).
    store = DBServingStore(pool, refresh_sec=0)
    kept = await store.prefilter_candidate_ids(
        user_profile=user_profile,
        candidate_universe=["P_kw", "P_brand", "P_avoid"],
    )
    assert kept == ["P_kw", "P_brand"]

    # No globally-avoided ingredient → universe returned unchanged, no query needed.
    kept_all = await store.prefilter_candidate_ids(
        user_profile={"user_id": "U_pg2"},
        candidate_universe=["P_kw", "P_brand", "P_avoid"],
    )
    assert kept_all == ["P_kw", "P_brand", "P_avoid"]
