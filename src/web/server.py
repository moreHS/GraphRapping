"""
FastAPI server for GraphRapping demo UI.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import logging
import math
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.web.state import demo_state, load_demo_data
from src.web.serving_store import (
    DBServingStore,
    DemoServingStore,
    ServingStore,
    DEFAULT_SERVING_REFRESH_SEC,
)
from src.rec.candidate_generator import (
    build_similar_boost_index,
    extract_owned_product_ids,
    generate_candidates_prefiltered,
)
from src.rec.scoped_preferences import (
    GLOBAL_SCOPES,
    collect_preference_ids,
    has_scoped_preferences,
    iter_scoped_preferences,
)
from src.rec.scorer import Scorer
from src.rec.reranker import rerank, RerankedProduct
from src.rec.explainer import explain, ExplanationService
from src.rec.search import search_products
# `_product_overlap` is imported read-only: it is the exact predicate search uses
# to decide "does this product carry these concepts", which /api/ask reuses to
# narrow the recommend candidate universe to query-relevant products (no logic is
# reimplemented, and search.py itself is unmodified beyond search_products' sig).
from src.rec.search import MatchedConcept, _concept_suffix, _product_overlap
from src.rec.ingredient_constraint import (
    count_evidence_unknown_products,
    matched_name_labels,
    product_passes_constraints,
)
from src.rec.semantic_compatibility import normalize_signal_id
from src.rec.query_understanding import (
    understand_query,
    QueryInterpretation,
    _category_concept_excluded,
)
from src.rec.provenance_provider import (
    DBProvenanceProvider,
    fetch_product_signals,
    signal_ids_by_concept_path,
)
from src.rec.hook_generator import generate_hooks
from src.rec.next_question import generate_next_question
from src.rec.category_groups import (
    RECOMMEND_CATEGORY_DEFS,
    RECOMMEND_CATEGORY_LABELS,
    classify_product_category_group,
    recommend_category_counts,
)
from src.common.config_loader import load_yaml
from src.common.enums import RecommendationMode
from src.common.text_normalize import normalize_text
from src.web.review_summary_sidecar import fetch_sidecar_summaries

logger = logging.getLogger(__name__)


# =============================================================================
# Serving store selection (Phase 2.1)
# =============================================================================
#
# GRAPHRAPPING_SERVING_MODE selects the recommendation data source:
#   - "demo" (default): in-memory DemoState from a pipeline run. Existing demo
#     and test behaviour is unchanged.
#   - "db": serving_product_profile / serving_user_profile read from Postgres
#     with a periodic-refresh cache. Requires GRAPHRAPPING_DATABASE_URL or
#     DATABASE_URL; a missing URL fails fast at app startup.

_SERVING_MODE_ENV = "GRAPHRAPPING_SERVING_MODE"
_SERVING_REFRESH_ENV = "GRAPHRAPPING_SERVING_REFRESH_SEC"

# Phase 2.2 (issue E2): GRAPHRAPPING_CANDIDATE_PREFILTER selects the candidate
# path:
#   - "auto" (default): SQL prefilter ON in db mode, OFF in demo mode.
#   - "on": consult the store's SQL prefilter (recall-safe; no-op if the store
#     does not implement one, e.g. the demo store).
#   - "off": full traversal over the category universe (no SQL pre-narrowing).
# The prefiltered path is proven equivalent to full traversal, so this only
# governs *where* the avoided hard filter runs, never the result set.
_CANDIDATE_PREFILTER_ENV = "GRAPHRAPPING_CANDIDATE_PREFILTER"

# DB-mode store, created at startup and reused so its refresh cache persists
# across requests. Stays None in demo mode.
_serving_store: ServingStore | None = None


def _serving_mode() -> str:
    return (os.environ.get(_SERVING_MODE_ENV) or "demo").strip().lower()


def _candidate_prefilter_enabled() -> bool:
    raw = (os.environ.get(_CANDIDATE_PREFILTER_ENV) or "auto").strip().lower()
    if raw == "on":
        return True
    if raw == "off":
        return False
    # auto: on in db mode, off in demo mode.
    return _serving_mode() == "db"


def _serving_refresh_sec() -> float:
    raw = os.environ.get(_SERVING_REFRESH_ENV)
    if not raw:
        return float(DEFAULT_SERVING_REFRESH_SEC)
    try:
        return max(0.0, float(raw))
    except ValueError:
        return float(DEFAULT_SERVING_REFRESH_SEC)


def get_serving_store() -> ServingStore:
    """Return the active serving store.

    DB mode reuses the startup-built store (holding the refresh cache). Demo
    mode (default) returns a lightweight store that reads the *live*
    module-level ``demo_state`` each call, so pipeline reloads and tests that
    monkeypatch ``server.demo_state`` are always reflected.
    """
    if _serving_store is not None:
        return _serving_store
    if _serving_mode() == "db":
        raise RuntimeError(
            "DB serving store is not initialized. It is created during app "
            "startup (lifespan); GRAPHRAPPING_SERVING_MODE=db requires the "
            "server to run through its lifespan."
        )
    return DemoServingStore(lambda: demo_state)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """App lifespan: in DB mode, build the pool + serving store at startup.

    A missing DB URL raises here (fail-fast at boot) rather than surfacing on
    the first recommendation request.
    """
    global _serving_store
    if _serving_mode() == "db":
        from src.db.connection import close_pool, create_pool, resolve_database_url

        # Fail fast with a clear, config-focused message before connecting.
        resolve_database_url()
        pool = await create_pool()
        _serving_store = DBServingStore(pool, refresh_sec=_serving_refresh_sec())
        try:
            yield
        finally:
            _serving_store = None
            await close_pool()
    else:
        yield


app = FastAPI(title="GraphRapping Demo", version="1.0", lifespan=_lifespan)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# =============================================================================
# Index
# =============================================================================

@app.get("/")
async def index():
    # no-cache on the DOCUMENT only: the browser must always revalidate
    # index.html so bumped `?v=` asset URLs take effect on a plain reload
    # (without this, heuristic caching can pin an old index -> old JS even
    # after a deploy — 2026-07-21 user-observed staleness). Static assets
    # themselves stay cacheable; the ?v= param versions them.
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-cache"},
    )


# =============================================================================
# Pipeline
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MOCKDATA_DIR = _PROJECT_ROOT / "mockdata"

# P1-2 (audit fix): demo review path is resolved via environment variable
# (GRAPHRAPPING_DEMO_REVIEW_PATH) with mockdata as the default. Previously this
# was a hardcoded absolute path tied to a single developer's machine.
_DEFAULT_FIXTURE = os.environ.get("GRAPHRAPPING_DEMO_FIXTURE", "wide")
# NOTE (IC-1): import-time capture of the legacy demo-review env is kept exactly
# as-is (do not refactor to call-time — plan §6/codex #5). Its value is consumed
# by _resolve_review_default_path below at a fixed priority rung; the request
# field no longer defaults to it (see PipelineRunRequest.review_json_path=None),
# so the new connector env can slot ABOVE it without changing the effective path
# when no connector env is set (clean import env → this constant is None).
_DEFAULT_REVIEW_PATH = os.environ.get("GRAPHRAPPING_DEMO_REVIEW_PATH")
_DEFAULT_SOURCE_REVIEW_STATS_PATH = (
    _PROJECT_ROOT / "data/source_snapshots/product_review_stats_snowflake_latest.json"
)

# IC-1 opt-in connector envs (resolved at CALL time inside pipeline_run — never
# captured at import). Priority per source is documented on the resolver helpers
# below. Both default to unset, so the standard demo/test path is byte-identical.
_REVIEW_TRIPLES_ENV = "GRAPHRAPPING_REVIEW_TRIPLES_JSON"
_PRODUCT_CATALOG_ENV = "GRAPHRAPPING_PRODUCT_CATALOG_JSON"


class PipelineRunRequest(BaseModel):
    fixture: str = _DEFAULT_FIXTURE
    # Default None (not the import-captured legacy env): the legacy demo-review
    # env is consulted by _resolve_review_default_path at a lower priority than
    # the new connector env, so it can no longer be the request-field default.
    # Byte-identical in a clean import env (legacy env unset → None either way).
    review_json_path: str | None = None
    product_json_path: str | None = None
    user_json_path: str | None = None
    max_reviews: int = 5000
    source: str = "demo"
    review_format: str = "relation"
    source_review_stats_json_path: str | None = str(_DEFAULT_SOURCE_REVIEW_STATS_PATH)


def _resolve_fixture_dir(fixture: str | None) -> Path:
    name = (fixture or "wide").strip()
    if name in {"", "wide", "mockdata", "default"}:
        return _MOCKDATA_DIR
    if name in {"dense", "dense_golden"}:
        return _MOCKDATA_DIR / "dense_golden"
    path = Path(name)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def _resolve_existing_json_path(path_value: str | None, default_path: Path, label: str) -> Path:
    path = Path(path_value) if path_value else default_path
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    if not path.exists():
        raise HTTPException(400, f"{label} not found: {path}")
    return path


def _resolve_user_default_path(fixture_dir: Path) -> Path:
    """Default user-profile file for the demo pipeline.

    Purchase-history backfill opt-in (fable_doc §C1): when
    ``GRAPHRAPPING_USER_PROFILES_JSON`` is set it replaces the fixture default
    (real pseudonymized profiles with embedded ``purchase_events``). Unset →
    the fixture file, so the standard demo path is byte-identical. An explicit
    request ``user_json_path`` still takes precedence over this default.
    """
    env_user_json = os.environ.get("GRAPHRAPPING_USER_PROFILES_JSON")
    if env_user_json:
        return Path(env_user_json)
    return fixture_dir / "user_profiles_normalized.json"


def _resolve_review_default_path(fixture_dir: Path) -> Path:
    """Default review-triples file for the demo pipeline (IC-1 §2).

    Priority (all below an explicit ``request.review_json_path`` — handled by the
    caller passing this as ``_resolve_existing_json_path``'s default):
    new connector env ``GRAPHRAPPING_REVIEW_TRIPLES_JSON`` (call-time) >
    legacy ``GRAPHRAPPING_DEMO_REVIEW_PATH`` (import-captured constant) >
    fixture default. Unset connector env in a clean import env → byte-identical
    to the prior behaviour (fixture default).
    """
    env_review = os.environ.get(_REVIEW_TRIPLES_ENV)
    if env_review:
        return Path(env_review)
    if _DEFAULT_REVIEW_PATH:
        return Path(_DEFAULT_REVIEW_PATH)
    return fixture_dir / "review_triples_raw.json"


def _resolve_product_default_path(fixture_dir: Path) -> Path:
    """Default product-catalog file for the demo pipeline (IC-1 §2).

    Priority (below an explicit ``request.product_json_path``): new connector env
    ``GRAPHRAPPING_PRODUCT_CATALOG_JSON`` (call-time) > fixture default. There is
    no legacy product env, so an unset connector env is byte-identical to the
    prior behaviour (fixture default).
    """
    env_product = os.environ.get(_PRODUCT_CATALOG_ENV)
    if env_product:
        return Path(env_product)
    return fixture_dir / "product_catalog_es.json"


def _check_pipeline_run_allowed(provided_token: str | None) -> None:
    """P4-2 (Wave 3.2): guard `/api/pipeline/run` from anonymous external calls.

    The endpoint can run an expensive local pipeline load, so an unprotected
    POST is unsafe outside explicit operator control. Two-layer policy:

    1. `GRAPHRAPPING_ENABLE_PIPELINE_RUN=1` must be set explicitly to opt in.
    2. If `GRAPHRAPPING_PIPELINE_RUN_TOKEN` is also set, the request must
       include `Authorization: Bearer <token>` (or `X-Pipeline-Token: <token>`).

    Operator can run with just the enable flag (loopback dev) or pair both for
    Internet-facing deployments.
    """
    if os.environ.get("GRAPHRAPPING_ENABLE_PIPELINE_RUN") != "1":
        raise HTTPException(
            403,
            "pipeline run is disabled. Set GRAPHRAPPING_ENABLE_PIPELINE_RUN=1 to enable.",
        )
    expected_token = os.environ.get("GRAPHRAPPING_PIPELINE_RUN_TOKEN", "")
    if expected_token:
        if not provided_token:
            raise HTTPException(403, "missing pipeline-run token")
        # Constant-time compare to avoid leaking length / prefix info.
        import hmac
        if not hmac.compare_digest(provided_token, expected_token):
            raise HTTPException(403, "invalid pipeline-run token")


def _extract_pipeline_token(authorization: str | None, x_pipeline_token: str | None) -> str | None:
    if x_pipeline_token:
        return x_pipeline_token
    if authorization and authorization.startswith("Bearer "):
        return authorization[len("Bearer "):]
    return None


@app.post("/api/pipeline/run")
async def pipeline_run(
    req: PipelineRunRequest,
    authorization: str | None = Header(default=None),
    x_pipeline_token: str | None = Header(default=None),
):
    _check_pipeline_run_allowed(_extract_pipeline_token(authorization, x_pipeline_token))
    import json as _json

    fixture_dir = _resolve_fixture_dir(req.fixture)

    # --- 1. Load products from selected fixture catalog ---
    # Priority: explicit request > GRAPHRAPPING_PRODUCT_CATALOG_JSON (call-time)
    # > fixture default. Missing file → the same 400 as before.
    product_path = _resolve_existing_json_path(
        req.product_json_path,
        _resolve_product_default_path(fixture_dir),
        "product_json_path",
    )
    mock_products = _json.loads(product_path.read_text(encoding="utf-8"))

    # --- 2. Load users from selected fixture profiles ---
    # Opt-in override (purchase-history backfill, fable_doc §C1): when
    # GRAPHRAPPING_USER_PROFILES_JSON is set, it becomes the default user file
    # (real pseudonymized profiles with embedded purchase_events). An explicit
    # request `user_json_path` still wins; when the env var is unset the default
    # stays the fixture file, so the standard demo path is byte-identical.
    user_path = _resolve_existing_json_path(
        req.user_json_path,
        _resolve_user_default_path(fixture_dir),
        "user_json_path",
    )
    # Cross-review P0-4 (auth explicitly rejected as over-engineering for a
    # loopback pseudonymized local demo — see DECISIONS/2026-07-18_purchase_
    # history_backfill.md): surface the operational constraint instead.
    if not req.user_json_path and os.environ.get("GRAPHRAPPING_USER_PROFILES_JSON"):
        logger.warning(
            "real pseudonymized profiles loaded — keep server loopback-bound; "
            "do not expose publicly"
        )
    mock_users = _json.loads(user_path.read_text(encoding="utf-8"))

    # Purchase-history backfill: surface any per-profile `purchase_events` into
    # the existing derive_purchase_features path (OWNS_PRODUCT/OWNS_FAMILY facts
    # → G4 similar_product_affinity boost). Returns None for the standard
    # fixtures (no `purchase_events` key), keeping this call byte-identical.
    from src.loaders.user_loader import extract_purchase_events_from_profiles
    purchase_events_by_user = extract_purchase_events_from_profiles(mock_users)

    # --- 3. Prepare review path ---
    # Priority: explicit request > GRAPHRAPPING_REVIEW_TRIPLES_JSON (call-time) >
    # legacy GRAPHRAPPING_DEMO_REVIEW_PATH > fixture default. Missing file → 400.
    selected_review_path = _resolve_existing_json_path(
        req.review_json_path,
        _resolve_review_default_path(fixture_dir),
        "review_json_path",
    )

    # The active fixture is the final 906-review source-grounded baseline,
    # synthesized from upstream NER-NER / NER-BeE annotation files + rs_own
    # metadata sample. External review files are loaded as-is; the endpoint must
    # not rewrite prod_nm/brnd_nm because those fields are part of the review's
    # product identity contract.
    load_demo_data(
        review_json_path=str(selected_review_path),
        product_es_records=mock_products,
        user_profiles=mock_users,
        max_reviews=req.max_reviews,
        source=req.source,
        review_format=req.review_format,
        source_review_stats_json_path=req.source_review_stats_json_path,
        purchase_events_by_user=purchase_events_by_user,
    )

    return {
        "status": "ok",
        "reviews": demo_state.review_count,
        "products": demo_state.product_count,
        "users": demo_state.user_count,
        "signals": demo_state.batch_result.get("total_signals", 0),
    }


# =============================================================================
# Dashboard
# =============================================================================

@app.get("/api/dashboard/summary")
async def dashboard_summary():
    # Serving counts + source-review stats come through the store, so they work
    # in both modes; the guard is mode-aware (DB mode is ready once started).
    # reviews_processed / total_signals / total_quarantined are demo-pipeline-run
    # artifacts with no DB-mode equivalent, so they fall back to 0 there.
    # `loaded` mirrors readiness per mode: demo_state.loaded in demo mode, True
    # in DB mode (reaching this point already passed _check_serving_ready()).
    _check_serving_ready()
    products = await get_serving_store().get_products()
    users = await get_serving_store().get_users()
    source_stats_positive = sum(
        1 for p in products
        if _positive_number(p.get("source_review_count_6m"))
    )
    source_rating_present = sum(
        1 for p in products
        if p.get("source_avg_rating_6m") is not None
    )
    db_mode = _serving_mode() == "db"
    return {
        "reviews_processed": 0 if db_mode else demo_state.review_count,
        "total_signals": 0 if db_mode else demo_state.batch_result.get("total_signals", 0),
        "total_quarantined": 0 if db_mode else sum(demo_state.quarantine_stats.values()),
        "serving_products": len(products),
        "serving_users": len(users),
        "source_review_stats_products": source_stats_positive,
        "source_avg_rating_products": source_rating_present,
        "loaded": True if db_mode else demo_state.loaded,
    }


@app.get("/api/dashboard/charts")
async def dashboard_charts():
    _check_loaded()
    return {
        "signal_families": _sorted_counts(demo_state.signal_family_counts),
        "relation_types": _sorted_counts(demo_state.relation_type_counts, limit=20),
        "bee_attrs": _sorted_counts(demo_state.bee_attr_counts, limit=20),
    }


# =============================================================================
# Data Explorer
# =============================================================================

@app.get("/api/reviews")
async def list_reviews(page: int = 1, size: int = 20, search: str = ""):
    _check_loaded()
    items = list(demo_state.bundles.values())
    if search:
        items = [r for r in items if search.lower() in str(r).lower()]
    total = len(items)
    start = (page - 1) * size
    page_items = items[start:start + size]
    return {"items": [_review_summary(r) for r in page_items], "total": total, "page": page}


@app.get("/api/reviews/{review_id}")
async def get_review(review_id: str):
    _check_loaded()
    bundle = demo_state.bundles.get(review_id)
    if not bundle:
        raise HTTPException(404, "Review not found")
    return bundle if isinstance(bundle, dict) else _review_detail(bundle)


@app.get("/api/products")
async def list_products():
    _check_serving_ready()
    products = await get_serving_store().get_products()
    return {"items": products, "total": len(products)}


@app.get("/api/products/{product_id}")
async def get_product(product_id: str):
    _check_serving_ready()
    product = await get_serving_store().get_product(product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    # master / concept_links are demo-pipeline artifacts (empty in DB mode).
    master = demo_state.product_masters.get(product_id, {})
    links = demo_state.concept_links.get(f"product:{product_id}", [])
    summaries = await fetch_sidecar_summaries([product_id])
    return {
        "serving_profile": product,
        "master": master,
        "concept_links": links,
        "review_summary": summaries.get(product_id),
    }


@app.get("/api/products/{product_id}/similar")
async def product_similar(product_id: str):
    """Phase 8 G3: attribute-similar products for a product (item-to-item).

    Returns the ephemeral ``similar_product_ids`` attached at serving load (top-N
    with ``shared_axes`` evidence, category-gated). This is a pure item-to-item
    lookup: it does NOT run the Scorer or eligibility pipeline (mirrors the
    anonymous design of ``search.py``). Unknown product -> 404; a known product
    with no attribute-similar neighbour -> empty list (200), so the frontend can
    hide the section rather than show an empty one.
    """
    _check_serving_ready()
    product = await get_serving_store().get_product(product_id)
    if not product:
        raise HTTPException(404, "Product not found")
    items = product.get("similar_product_ids") or []
    return {"product_id": product_id, "items": items, "total": len(items)}


@app.get("/api/users")
async def list_users():
    _check_serving_ready()
    users = await get_serving_store().get_users()
    return {"items": users, "total": len(users)}


@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    _check_serving_ready()
    user = await get_serving_store().get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return {"serving_profile": user}


# =============================================================================
# Recommendation
# =============================================================================

@app.get("/api/recommend/categories")
async def recommend_categories():
    _check_serving_ready()
    products = await get_serving_store().get_products()
    counts = recommend_category_counts(products)
    return {
        "items": [
            {
                "group": str(item["group"]),
                "label": str(item["label"]),
                "count": counts.get(str(item["group"]), 0),
            }
            for item in RECOMMEND_CATEGORY_DEFS
        ]
    }


class RecommendRequest(BaseModel):
    user_id: str
    mode: str = "explore"
    category_group: str = "all"
    top_k: int = 10
    weights: dict[str, float] | None = None
    shrinkage_k: float = 10.0
    diversity_weight: float = 0.1
    preset: str | None = None


# RecommendRequest.shrinkage_k's own field default -- used to detect "caller
# customized shrinkage_k without sending weights" (the C2 fix below) without
# hardcoding the literal a second time.
_DEFAULT_SHRINKAGE_K: float = float(RecommendRequest.model_fields["shrinkage_k"].default)


# =============================================================================
# Recommend intent presets (Phase 6 Track A1)
# =============================================================================
#
# A preset is a server-side named combination of the *existing* mode/weights/
# shrinkage_k/diversity_weight knobs -- no new scoring path. `configs/
# recommend_presets.yaml` is the single source of truth; the frontend reads it
# via GET /api/recommend/presets instead of hardcoding preset copy.
#
# Not cached: the YAML is tiny and this mirrors Scorer.load_config(), which
# also re-reads scoring_weights.yaml on every call.

_RECOMMEND_PRESETS_FILENAME = "recommend_presets.yaml"


def _load_recommend_presets() -> dict[str, dict[str, Any]]:
    data = load_yaml(_RECOMMEND_PRESETS_FILENAME)
    presets = data.get("presets") or {}
    if not isinstance(presets, dict):
        raise HTTPException(500, f"{_RECOMMEND_PRESETS_FILENAME} is malformed: 'presets' must be a mapping")
    return presets


def _resolve_preset(preset_key: str) -> dict[str, Any]:
    presets = _load_recommend_presets()
    preset = presets.get(preset_key)
    if preset is None:
        raise HTTPException(
            400,
            f"Unknown preset '{preset_key}'. Available: {sorted(presets)}",
        )
    return preset


@app.get("/api/recommend/presets")
async def recommend_presets():
    presets = _load_recommend_presets()
    return {
        "items": [
            {
                "key": key,
                "label_ko": preset.get("label_ko", key),
                "description_ko": preset.get("description_ko", ""),
            }
            for key, preset in presets.items()
        ]
    }


def _category_universe_ids(
    product_map: dict[str, dict[str, Any]],
    requested_category_group: str,
) -> list[str]:
    """Product ids in the requested recommend category group (all → every id)."""
    if requested_category_group == "all":
        return list(product_map.keys())
    return [
        pid
        for pid, product in product_map.items()
        if classify_product_category_group(product) == requested_category_group
    ]


def _label_has_surface(product: dict[str, Any], surfaces: set[str]) -> bool:
    """[A2/F3] Whether the product's OWN category label contains any excluded category
    surface (surface-keyed exclusion — no reliance on concept links)."""
    if not surfaces:
        return False
    label_norm = normalize_text(
        str(product.get("category_name") or product.get("category_id") or "")
    )
    return bool(label_norm) and any(surface in label_norm for surface in surfaces)


def _axis_excluded_product_ids(
    products: list[dict[str, Any]],
    *,
    brand_ids: set[str],
    category_surfaces: set[str],
    category_groups: set[str],
) -> set[str]:
    """[A2] Product ids hard-excluded by a query-negated brand / literal category
    SURFACE / category group. Mirrors the candidate-generator + search hard filters
    (category by the product's OWN label containing a surface — F3), so the server can
    pre-subtract them from the candidate universe, the ingredient relax count, and the
    related-products exclude set (parity with the A1 ``excluded_product_ids``
    treatment). Empty exclusion axes → empty set (dormant; no per-product work)."""
    if not (brand_ids or category_surfaces or category_groups):
        return set()
    excluded: set[str] = set()
    for product in products:
        pid = str(product.get("product_id") or "")
        if not pid:
            continue
        if brand_ids and ({str(b) for b in (product.get("brand_concept_ids") or [])} & brand_ids):
            excluded.add(pid)
            continue
        if category_surfaces and _label_has_surface(product, category_surfaces):
            excluded.add(pid)
            continue
        if category_groups and classify_product_category_group(product) in category_groups:
            excluded.add(pid)
    return excluded


def _dedupe_labels(labels: Iterator[str]) -> list[str]:
    """Order-preserving dedupe for a display-label array (A2 excluded meta): distinct
    ids can map to the same human label (two SKUs sharing a representative name, or
    two concept ids sharing a brand/category name), and the surfaced list must not
    repeat it."""
    seen: set[str] = set()
    out: list[str] = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def _excluded_meta(
    products: list[dict[str, Any]],
    interp: QueryInterpretation,
) -> dict[str, list[str]]:
    """[A2 surfacing] Labeled ``excluded`` response block derived from the
    interpretation (categories/groups are already display-ready surfaces/labels;
    brands/products are ids labeled from the catalog). Groups use the tab label;
    categories surface the negated expression verbatim (F3 — surface-keyed); brand
    labels come from any catalog product carrying the id (fallback: id suffix);
    products use the representative name. Label arrays are order-preserving-deduped
    (distinct SKUs can share one representative name; ids remain in the
    interpretation)."""
    # Groups + category surfaces need NO catalog scan (F3/F1).
    group_labels = _dedupe_labels(
        RECOMMEND_CATEGORY_LABELS.get(str(g), str(g)) for g in interp.excluded_category_groups
    )
    category_labels = _dedupe_labels(str(s) for s in interp.excluded_category_surfaces if s)
    brand_ids = {str(b) for b in interp.excluded_brand_ids if b}
    prod_ids = {str(p) for p in interp.excluded_product_ids if p}
    # [A2/F1 perf] Skip the per-request catalog loop entirely when there is nothing to
    # label FROM it — the common no-exclusion /api/ask path (and category/group-only
    # queries) never touches the 45k-scale product list.
    if not (brand_ids or prod_ids):
        return {
            "brands": [],
            "categories": category_labels,
            "category_groups": group_labels,
            "products": [],
        }

    brand_labels: dict[str, str] = {}
    prod_labels: dict[str, str] = {}
    for product in products:
        brand_name = product.get("brand_name")
        if brand_name and brand_ids:
            for bid in product.get("brand_concept_ids") or []:
                if str(bid) in brand_ids:
                    brand_labels.setdefault(str(bid), str(brand_name))
        pid = str(product.get("product_id") or "")
        if pid in prod_ids:
            prod_labels.setdefault(pid, str(product.get("representative_product_name") or pid))
    return {
        "brands": _dedupe_labels(brand_labels.get(b, _concept_suffix(b)) for b in sorted(brand_ids)),
        "categories": category_labels,
        "category_groups": group_labels,
        "products": _dedupe_labels(prod_labels.get(p, p) for p in sorted(prod_ids)),
    }


def _is_exclusion_only(interp: QueryInterpretation) -> bool:
    """[A2 F4/F7] The query resolved ONLY exclusions — no GENUINE positive concept for
    concept search to rank. A genuine positive is any resolved concept NOT accounted
    for by an exclusion: a non-category concept is always genuine, and a category
    concept is genuine UNLESS it is shadowed by a category/group exclusion
    (``_category_concept_excluded`` — so "이니스프리 말고 세럼" keeps 세럼 as a genuine
    positive and is NOT exclusion-only, while "스킨케어 빼고" whose only positive is the
    excluded group's own label IS). Used to surface honest guidance instead of a silent
    empty result set."""
    has_exclusion = bool(
        interp.excluded_brand_ids
        or interp.excluded_category_surfaces
        or interp.excluded_category_groups
        or interp.excluded_product_ids
    )
    if not has_exclusion:
        return False
    surfaces = list(interp.excluded_category_surfaces)
    groups = list(interp.excluded_category_groups)
    for concept in interp.resolved_concepts:
        if concept.concept_type != "category":
            return False  # a genuine non-category positive
        if not _category_concept_excluded(concept, surfaces, groups):
            return False  # a positive category NOT shadowed by an exclusion
    return True


def _similar_signal_field(sig: Any, key: str) -> Any:
    """Field access for an ungated-similarity sidecar entry.

    Entries are ``SimilarProductSignal`` objects from both stores; the dict
    branch keeps test doubles / serialized entries working (Phase 8 G4)."""
    if isinstance(sig, dict):
        return sig.get(key)
    return getattr(sig, key, None)


def _rerank_with_pins(
    scored: list[tuple[Any, Any]],
    *,
    pins: set[str],
    product_map: dict[str, dict[str, Any]],
    diversity_weight: float,
    top_k: int,
    mode: RecommendationMode,
) -> tuple[list[RerankedProduct], list[str]]:
    """A1 pin-aware rerank. Returns ``(reranked, topk_cut_pin_ids)``.

    The diversity reranker has a ``top_k*2`` window + a ``top_k`` cut that can drop
    a low-scoring pin, so the pin block is held OUT of the reranker and assembled as
    a leading block (score-desc within the block — ``scored`` is pre-sorted); the
    non-pinned candidates rerank into the remaining ``top_k - |pins|`` slots. When
    the pins alone fill/exceed ``top_k`` the response-size contract (top_k) wins:
    the reranker is skipped and the lowest-scored pins beyond ``top_k`` are CUT and
    reported in ``topk_cut_pin_ids`` (F6, reason="top_k" in the caller's trace).
    ``final_rank`` is reassigned across the assembled list (single owner — no
    post-serialize sort), so the downstream ``rank = final_rank + 1`` is contiguous
    with no dupes. Duplicate product_ids in ``scored`` are collapsed to their first
    occurrence (F9). ``top_k <= 0`` yields no results (F11 — no negative slicing).
    Empty pins → the exact prior ``rerank`` call (byte-identical)."""
    if not pins:
        return rerank(
            [s for _, s in scored], product_profiles=product_map,
            diversity_weight=diversity_weight, top_k=top_k, mode=mode,
        ), []
    # F9: collapse duplicate product_ids (keep first occurrence, preserving order).
    seen_ids: set[str] = set()
    deduped: list[tuple[Any, Any]] = []
    for c, s in scored:
        if c.product_id not in seen_ids:
            seen_ids.add(c.product_id)
            deduped.append((c, s))
    scored = deduped

    keep = max(0, top_k)  # F11: negative/0 top_k → no negative slicing
    pinned_scored = [(c, s) for c, s in scored if c.product_id in pins]
    rest_scored = [(c, s) for c, s in scored if c.product_id not in pins]
    n_pins = len(pinned_scored)
    topk_cut: list[str] = []
    if n_pins >= keep:
        rest_reranked: list[RerankedProduct] = []
        pinned_used = pinned_scored[:keep]
        topk_cut = [c.product_id for c, _s in pinned_scored[keep:]]  # F6
    else:
        rest_reranked = rerank(
            [s for _, s in rest_scored], product_profiles=product_map,
            diversity_weight=diversity_weight, top_k=keep - n_pins, mode=mode,
        )
        pinned_used = pinned_scored
    assembled: list[RerankedProduct] = [
        RerankedProduct(
            product_id=sp.product_id,
            original_rank=idx,
            final_rank=idx,
            final_score=round(sp.final_score, 4),
            rank_score=round(sp.final_score, 4),  # pin block has no diversity adjustment
            diversity_bonus=0.0,
            contribution_log=sp.feature_contributions,
        )
        for idx, (_c, sp) in enumerate(pinned_used)
    ]
    assembled.extend(rest_reranked)
    for i, r in enumerate(assembled):
        r.final_rank = i
    return assembled, topk_cut


async def _run_scored_pipeline(
    *,
    store: ServingStore,
    user_profile: dict[str, Any],
    product_map: dict[str, dict[str, Any]],
    candidate_universe_ids: list[str],
    mode: RecommendationMode,
    scorer: Scorer,
    diversity_weight: float,
    top_k: int,
    ingredient_name_labels: dict[str, list[str]] | None = None,
    query_product_ids: set[str] | None = None,
    excluded_product_ids: set[str] | None = None,
    excluded_brand_ids: set[str] | None = None,
    excluded_category_surfaces: set[str] | None = None,
    excluded_category_groups: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """Shared recommend pipeline: prefilter -> candidates -> score -> rerank ->
    explain -> per-path snippets -> result dicts.

    Returns ``(results, candidate_count, topk_cut_pin_ids)`` — the last is the pins
    the response-size (top_k) cut dropped (A1 F6; ``[]`` on the no-pin recommend
    path).

    Extracted verbatim from the /api/recommend handler so /api/ask's query-scoped
    recommend mode reuses the identical candidate/score/explain/snippet path. The
    only differences live in the caller: which user profile is scored, which
    candidate universe is scored, and any post-hoc explanation relabeling. Returns
    the result dicts and the raw candidate count (for the response candidate_count).

    ``ingredient_name_labels`` (Phase 6 B2) is forwarded to the candidate generator
    so a name-only wanted-ingredient carrier earns a ``product_name:<관용어>``
    overlap axis (evidence-qualified, PRODUCT_MASTER_TRUTH). None (default; the
    /api/recommend caller) keeps the pipeline byte-identical.

    ``query_product_ids`` / ``excluded_product_ids`` (search-absorption A1) are
    forwarded to the candidate generator (pins earn a ``product:<pid>`` master-truth
    overlap + survive the retrieval cut; excluded products are hard-filtered). Pins
    are additionally re-unioned into the prefiltered set (so a SQL prefilter can
    never drop a named product) and drive the pin-aware rerank assembly below. None
    (default; the /api/recommend caller) keeps the pipeline byte-identical."""
    pins = query_product_ids or set()
    prefiltered_product_ids = candidate_universe_ids
    if _candidate_prefilter_enabled():
        # Optional store capability (duck-typed like provenance.prefetch): the
        # DB store narrows via SQL; a store without it leaves the universe as-is.
        prefilter = getattr(store, "prefilter_candidate_ids", None)
        if prefilter is not None:
            prefiltered_product_ids = await prefilter(
                user_profile=user_profile,
                candidate_universe=candidate_universe_ids,
            )

    # A1: re-union pins into the prefiltered set so a SQL prefilter (or any
    # narrowing above) can never drop a named product. The caller already verified
    # pins pass the hard gates (category/ingredient/avoided/excluded), so this only
    # re-adds a survivor the prefilter may have dropped — order-preserving append.
    if pins:
        present = set(prefiltered_product_ids)
        prefiltered_product_ids = list(prefiltered_product_ids) + [
            pid for pid in candidate_universe_ids if pid in pins and pid not in present
        ]

    # Phase 8 G4: assemble the similar-boost index from the store's ungated
    # similarity sidecar × the user's owned products. Optional store capability
    # (duck-typed like prefilter_candidate_ids above): a store without the
    # accessor — or a user without an in-corpus owned anchor — leaves
    # similar_boost None (dormant; the default path is byte-identical). The
    # sidecar is corpus-wide, so an owned anchor OUTSIDE the current category
    # tab still boosts in-tab candidates. The per-anchor signals fetched here
    # are also indexed by (anchor, neighbour) for the shared_axes provenance
    # attached to `similar` explanation paths below — no second store/DB access.
    similar_boost: dict[str, list[tuple[str, float]]] | None = None
    similar_axes_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    anchor_names_by_id: dict[str, str] = {}
    ungated_getter = getattr(store, "get_ungated_similar", None)
    if ungated_getter is not None:
        owned_ids = extract_owned_product_ids(user_profile)
        signals_by_anchor: dict[str, list[Any]] = {}
        for anchor in sorted(owned_ids):
            anchor_signals = await ungated_getter(anchor)
            if anchor_signals:
                signals_by_anchor[anchor] = anchor_signals
        if signals_by_anchor:
            similar_boost = build_similar_boost_index(owned_ids, signals_by_anchor) or None
        if similar_boost:
            # Anchor display names for similar explanation paths (owned set is
            # tiny — a handful of store lookups once per request).
            for anchor in signals_by_anchor:
                anchor_profile = await store.get_product(anchor)
                if anchor_profile:
                    anchor_names_by_id[anchor] = (
                        anchor_profile.get("representative_product_name") or anchor
                    )
            for anchor, anchor_signals in signals_by_anchor.items():
                for sig in anchor_signals:
                    neighbor = _similar_signal_field(sig, "product_id")
                    axes = _similar_signal_field(sig, "shared_axes")
                    if neighbor and axes:
                        similar_axes_by_pair[(anchor, str(neighbor))] = axes

    candidates = generate_candidates_prefiltered(
        user_profile=user_profile,
        prefiltered_product_ids=prefiltered_product_ids,
        product_profiles_by_id=product_map,
        mode=mode,
        max_candidates=50,
        similar_boost=similar_boost,
        ingredient_name_labels=ingredient_name_labels,
        query_product_ids=query_product_ids,
        excluded_product_ids=excluded_product_ids,
        excluded_brand_ids=excluded_brand_ids,
        excluded_category_surfaces=excluded_category_surfaces,
        excluded_category_groups=excluded_category_groups,
    )

    scored = []
    for c in candidates:
        p = product_map.get(c.product_id)
        if p:
            s = scorer.score(user_profile, p, c.overlap_concepts, mode=mode)
            scored.append((c, s))

    scored.sort(key=lambda x: x[1].final_score, reverse=True)

    reranked, topk_cut_pins = _rerank_with_pins(
        scored, pins=pins, product_map=product_map,
        diversity_weight=diversity_weight, top_k=top_k, mode=mode,
    )
    summary_by_product = await fetch_sidecar_summaries([r.product_id for r in reranked])

    # Pre-pass: pair each reranked product with its candidate/score/explanation.
    # explain() is pure, in-memory and cheap, so computing all explanations up
    # front lets provenance snippets be resolved in a single request-level batch
    # (avoids per-product N+1 DB round-trips in DB mode).
    prepared: list[tuple[Any, Any, Any, dict, Any]] = []
    for r in reranked:
        candidate = next((candidate for candidate, scored_product in scored if scored_product.product_id == r.product_id), None)
        scored_product = next((scored_product for _, scored_product in scored if scored_product.product_id == r.product_id), None)
        if candidate is not None and scored_product is not None:
            product_profile = product_map.get(r.product_id, {})
            exp = explain(scored_product, candidate.overlap_concepts, top_n=5)
            prepared.append((r, candidate, scored_product, product_profile, exp))

    # Phase 0.4 / 2.1: attach per-path review provenance snippets. Each path is
    # enriched only with signals of its own concept for the recommended product,
    # so no unrelated review leaks onto a path. Batched once for the whole request.
    snippets_by_product = await _resolve_snippets_batch(prepared)

    results = []
    for r, candidate, scored_product, product_profile, exp in prepared:
        hooks = generate_hooks(exp, product_profile=product_profile)
        snippets_by_path_idx = snippets_by_product.get(r.product_id, {})
        explanation_paths: list[dict[str, Any]] = []
        for idx, p in enumerate(exp.paths):
            path_row: dict[str, Any] = {
                "type": p.concept_type, "id": p.concept_id,
                "user_edge": p.user_edge, "product_edge": p.product_edge,
                "contribution": p.contribution,
                "snippets": snippets_by_path_idx.get(idx, []),
            }
            if p.concept_type == "similar":
                # Phase 8 G4 provenance (plan §1.4): attach the shared-node
                # evidence for this (anchor, candidate) pair from the load-time
                # sidecar — additive key, entries copied ([C1] discipline: the
                # response must never alias store state).
                axes = similar_axes_by_pair.get((p.concept_id, r.product_id))
                if axes:
                    path_row["shared_axes"] = [dict(ax) for ax in axes]
                # Card-rendering aid (2026-07-21): the path id is the owned
                # ANCHOR product id — surface its human name so the UI can say
                # "보유 상품 '헤라 …'와 속성 공유" instead of a bare id.
                anchor_name = anchor_names_by_id.get(p.concept_id)
                if anchor_name:
                    path_row["anchor_name"] = anchor_name
            explanation_paths.append(path_row)
        # Readability patch (2026-07-21): the Korean summary references the
        # similar anchor by raw product id ("보유하신 '50165' 제품과 …") —
        # substitute the anchor's display name resolved above.
        summary_ko = exp.summary_ko
        if anchor_names_by_id and summary_ko:
            for _aid, _aname in anchor_names_by_id.items():
                summary_ko = summary_ko.replace(f"'{_aid}'", f"'{_aname}'")
        results.append({
            "rank": r.final_rank + 1,
            "product_id": r.product_id,
            "product": product_profile,
            "overlap_concepts": candidate.overlap_concepts,
            "raw_score": scored_product.raw_score,
            "shrinked_score": scored_product.shrinked_score,
            "final_score": r.final_score,
            "rank_score": r.rank_score,
            "diversity_bonus": r.diversity_bonus,
            "support_count": scored_product.support_count,
            "feature_contributions": scored_product.feature_contributions,
            "score_layers": scored_product.score_layers,
            "eligibility": candidate.eligibility.to_dict(),
            "review_summary": summary_by_product.get(r.product_id),
            "source_trust": _source_trust(product_profile),
            "explanation": summary_ko,
            "explanation_paths": explanation_paths,
            "hooks": {"discovery": hooks.discovery, "consideration": hooks.consideration, "conversion": hooks.conversion},
        })

    return results, len(candidates), topk_cut_pins


@app.post("/api/recommend")
async def recommend(req: RecommendRequest):
    if req.preset and req.weights:
        raise HTTPException(400, "Specify either 'preset' or 'weights', not both.")

    # Preset resolution (C2 fix folded in below): a preset always materializes
    # a *complete* weights dict (YAML base features + weight_overrides) so it
    # rides the same load_from_dict(weights, shrinkage_k=...) path as a
    # manually-customized request -- no separate scoring path.
    preset_used: dict[str, Any] | None = None
    effective_mode = req.mode
    effective_shrinkage_k = req.shrinkage_k
    effective_diversity_weight = req.diversity_weight
    materialized_weights: dict[str, float] | None = None

    if req.preset:
        preset = _resolve_preset(req.preset)
        base_scorer = Scorer()
        base_scorer.load_config()
        overrides = {str(k): float(v) for k, v in (preset.get("weight_overrides") or {}).items()}
        materialized_weights = {**base_scorer.weights, **overrides}
        # preset wins over req.mode/shrinkage_k/diversity_weight when both are given.
        effective_mode = str(preset.get("mode", req.mode))
        effective_shrinkage_k = float(preset.get("shrinkage_k", req.shrinkage_k))
        effective_diversity_weight = float(preset.get("diversity_weight", req.diversity_weight))
        preset_used = {
            "key": req.preset,
            "label_ko": preset.get("label_ko", req.preset),
            "mode": effective_mode,
            "shrinkage_k": effective_shrinkage_k,
            "diversity_weight": effective_diversity_weight,
            "weight_overrides": overrides,
        }

    _check_serving_ready()
    store = get_serving_store()
    user = await store.get_user(req.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    mode_map = {"strict": RecommendationMode.STRICT, "explore": RecommendationMode.EXPLORE, "compare": RecommendationMode.COMPARE}
    mode = mode_map.get(effective_mode, RecommendationMode.EXPLORE)

    # Candidate path (Phase 2.2): category universe -> optional SQL prefilter ->
    # in-memory scoring on the reduced set. The SQL prefilter (db mode default)
    # only pushes the avoided-ingredient hard filter to SQL, which the in-memory
    # full traversal applies identically, so the candidate set is unchanged.
    product_map = {p["product_id"]: p for p in await store.get_products()}
    requested_category_group = req.category_group if req.category_group in RECOMMEND_CATEGORY_LABELS else "all"
    category_universe_ids = _category_universe_ids(product_map, requested_category_group)

    scorer = Scorer()
    if materialized_weights is not None:
        # Preset path: load_config() first so brand_confidence (not part of
        # load_from_dict's contract) still comes from YAML, then override
        # weights/shrinkage_k with the materialized preset values.
        scorer.load_config()
        scorer.load_from_dict(materialized_weights, shrinkage_k=effective_shrinkage_k)
    elif req.weights:
        scorer.load_from_dict(req.weights, shrinkage_k=req.shrinkage_k)
    elif req.shrinkage_k != _DEFAULT_SHRINKAGE_K:
        # C2 fix: previously, no explicit `weights` meant this always fell
        # through to load_config(), silently discarding a caller-provided
        # shrinkage_k (e.g. moving only the shrinkage_k slider, without
        # touching any weight slider, had no effect). Materialize the YAML
        # base weights so shrinkage_k-only customization still applies.
        scorer.load_config()
        scorer.load_from_dict(dict(scorer.weights), shrinkage_k=req.shrinkage_k)
    else:
        # Pure default request (no preset/weights/shrinkage_k customization):
        # unchanged path, keeps existing tests/snapshots byte-identical.
        scorer.load_config()
    weights = scorer.weights

    results, candidate_count, _topk_cut = await _run_scored_pipeline(
        store=store,
        user_profile=user,
        product_map=product_map,
        candidate_universe_ids=category_universe_ids,
        mode=mode,
        scorer=scorer,
        diversity_weight=effective_diversity_weight,
        top_k=req.top_k,
    )

    # P4-3 (Wave 3.3): pass scored products so axis selection can use score
    # histograms instead of falling back to data-absence ordering only.
    nq = generate_next_question(user, scored_products=results)
    return {
        "user_id": req.user_id,
        "mode": effective_mode,
        "category_group": requested_category_group,
        "category_label": RECOMMEND_CATEGORY_LABELS[requested_category_group],
        "category_filtered_count": len(category_universe_ids),
        "total_product_count": len(product_map),
        "candidate_count": candidate_count,
        "results": results,
        "next_question": {"question": nq.question_ko, "axis": nq.uncertainty_axis, "options": nq.options} if nq else None,
        "weights_used": weights,
        "preset_used": preset_used,
    }


async def _build_provenance_context(
    product_ids: list[str],
) -> tuple[Any, dict[str, list[dict]]]:
    """Resolve the (provider, product_signals-by-id) pair for the active mode.

    Demo mode uses the in-memory provider + ``demo_state.product_signals``. DB
    mode builds a request-batched ``DBProvenanceProvider`` and fetches the
    products' raw signals in one query.
    """
    if _serving_mode() == "db":
        from src.db.connection import get_pool

        pool = await get_pool()
        provider: Any = DBProvenanceProvider(pool)
        product_signals_by_id = await fetch_product_signals(pool, product_ids)
        return provider, product_signals_by_id
    return demo_state.provenance_provider, demo_state.product_signals


async def _resolve_snippets_batch(
    prepared: list[tuple[Any, Any, Any, dict, Any]],
) -> dict[str, dict[int, list[dict]]]:
    """Resolve per-product, per-path review snippets in one request-level batch.

    Returns ``{product_id: {path_index: [{"review_id": str, "text": str}, ...]}}``
    keyed to the order of each product's ``exp.paths``. Products/paths with no
    backing provenance are omitted; an empty result means no snippets (backward
    compatible — the endpoint simply omits them).

    Provenance integrity: each path is matched only against signals of its own
    concept for that product, so an unrelated review can never attach. In DB
    mode the whole signal_evidence → fact_provenance → review chain is prefetched
    once (no N+1); in demo mode the in-memory provider has no ``prefetch`` and is
    read directly.
    """
    if not prepared:
        return {}

    product_ids = [r.product_id for r, *_rest in prepared]
    provider, product_signals_by_id = await _build_provenance_context(product_ids)
    if provider is None:
        return {}

    # Step 1: resolve each product's path→signal_ids, collecting the full batch.
    per_product: dict[str, tuple[Any, list[str], Any, dict[int, list[str]]]] = {}
    all_signal_ids: list[str] = []
    for r, candidate, scored_product, _product_profile, exp in prepared:
        if not exp.paths:
            continue
        product_signals = product_signals_by_id.get(r.product_id, [])
        signal_ids_by_concept = signal_ids_by_concept_path(exp.paths, product_signals)
        if not signal_ids_by_concept:
            continue
        per_product[r.product_id] = (
            scored_product, candidate.overlap_concepts, exp, signal_ids_by_concept,
        )
        for sids in signal_ids_by_concept.values():
            all_signal_ids.extend(sids)

    if not per_product:
        return {}

    # Step 2: request-level batch prefetch (DB provider only; in-memory has none).
    prefetch = getattr(provider, "prefetch", None)
    if prefetch is not None:
        await prefetch(all_signal_ids)

    # Step 3: per-product enrichment — O(1) cache reads after prefetch in DB mode.
    service = ExplanationService(provenance_provider=provider)
    out: dict[str, dict[int, list[dict]]] = {}
    for product_id, (scored_product, overlap_concepts, exp, signal_ids_by_concept) in per_product.items():
        prov_exp = await service.explain_with_provenance(
            scored=scored_product,
            overlap_concepts=overlap_concepts,
            top_n=len(exp.paths),
            signal_ids_by_concept=signal_ids_by_concept,
        )
        snippets_by_path_idx: dict[int, list[dict]] = {}
        for idx, ppath in enumerate(prov_exp.provenance_paths):
            if not ppath.snippet_evidence:
                continue
            # Each snippet carries its own review_id (see ExplanationService's
            # SnippetEvidence), so there are no parallel lists to index-align — a
            # snippet with no review_id surfaces as an empty string, not a wrong id.
            snippets_by_path_idx[idx] = [
                {"review_id": ev.review_id or "", "text": ev.snippet}
                for ev in ppath.snippet_evidence
            ]
        if snippets_by_path_idx:
            out[product_id] = snippets_by_path_idx
    return out


# =============================================================================
# Phase 8 G5: query-based "related products more" (plan §2)
# =============================================================================
#
# A purely additive 2차 surface: for the top few 1차 results (search or query-
# scoped recommend), gather each anchor's ungated attribute-similar neighbours
# from the store sidecar (``get_ungated_similar`` — NOT the profile-attached
# ``similar_product_ids`` field, so the 1차 pipeline's own results are never
# touched) and present the best few as "related products". No similarity-stage
# gate is added here: category constraints are already applied upstream by the
# query pipeline (confirmed decision). The caller assembles ``exclude_ids`` (all
# 1차 results, plus — recommend branch only — the user's owned products and any
# avoided-ingredient carriers) so upstream hard exclusions are preserved even
# for neighbours that fell outside the narrowed universe (plan §2.1, codex #11).


def _related_anchor_names(results: list[dict[str, Any]]) -> dict[str, str]:
    """Map each 1차-result product_id to its display name, for anchor attribution.

    Sourced from the result's embedded serving profile
    (``representative_product_name``), falling back to the product_id when a name
    is absent. Works for both the search result shape and the recommend result
    shape (both embed ``product``)."""
    names: dict[str, str] = {}
    for result in results:
        pid = result.get("product_id")
        if not pid:
            continue
        profile = result.get("product") or {}
        name = profile.get("representative_product_name")
        names[str(pid)] = str(name) if name else str(pid)
    return names


def _avoided_ingredient_product_ids(
    user_profile: dict[str, Any],
    product_map: dict[str, dict[str, Any]],
) -> set[str]:
    """Product ids whose profile carries an ingredient this user avoids.

    Mirrors ``candidate_generator``'s avoided-ingredient hard filter EXACTLY —
    the same scope-aware ``collect_preference_ids(..., "avoided_ingredient_ids",
    "AVOIDS_INGREDIENT", <product group>)`` and the same
    ``ingredient_concept_ids`` ∪ ``ingredient_ids`` intersection — so a related-
    products neighbour that would have been hard-filtered upstream is excluded
    here too. Upstream cannot be relied on for ungated neighbours: they can fall
    outside the narrowed candidate universe, where the filter never ran (plan
    §2.1 / codex #11). Query-injected AVOIDS_INGREDIENT entries live on the
    scoped profile passed in, so negated-ingredient queries propagate here as
    well."""
    excluded: set[str] = set()
    for pid, product in product_map.items():
        product_group = classify_product_category_group(product)
        avoided = collect_preference_ids(
            user_profile, "avoided_ingredient_ids", "AVOIDS_INGREDIENT", product_group,
        )
        if not avoided:
            continue
        product_ingredients = set(product.get("ingredient_concept_ids") or [])
        product_ingredients.update(product.get("ingredient_ids") or [])
        if avoided & product_ingredients:
            excluded.add(str(pid))
    return excluded


async def _related_products(
    anchor_ids: list[str],
    *,
    store: ServingStore,
    exclude_ids: set[str],
    limit: int = 5,
    anchor_names: dict[str, str] | None = None,
    require_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Assemble the G5 "related products" list from the anchors' ungated sidecar.

    ``anchor_ids`` are the top 1차-result product ids (the caller slices to 5).
    Each anchor's ungated similarity neighbours come from
    ``store.get_ungated_similar`` (never the profile-attached similar field). A
    neighbour is dropped when it is in ``exclude_ids`` or is the anchor itself,
    or when its entry is malformed / its score is non-finite or non-positive. A
    neighbour reachable from several anchors is deduped to its MAX-score entry
    (keeping that anchor's attribution); an exact score tie resolves to the
    smaller anchor id (anchors are visited in sorted order and an entry is
    replaced only on a strictly greater score). The final list is score-desc,
    then neighbour-id-asc, capped to ``limit``. Each entry deep-copies its
    ``shared_axes`` evidence ([C1]: the response must never alias store state)
    and names the anchor it was attributed to. Empty result → ``[]``.

    ``require_ids`` (Phase 6 B2): when given, a neighbour is kept ONLY if its id is
    in this set — the caller passes the wanted-ingredient constraint passers so a
    non-containing product is never re-surfaced under a 1차 result of an active
    ingredient filter. ``None`` (default) keeps every neighbour (no filter); the
    caller passes ``None`` when no ingredient filter is active OR it was relaxed.

    ``get_ungated_similar`` is duck-typed (an optional store capability, exactly
    as ``_run_scored_pipeline`` treats it): a store without the accessor yields
    no related products rather than erroring."""
    ungated_getter = getattr(store, "get_ungated_similar", None)
    if ungated_getter is None:
        return []
    names = anchor_names or {}
    best: dict[str, dict[str, Any]] = {}
    for anchor in sorted(set(anchor_ids)):
        anchor_name = names.get(anchor) or anchor
        for sig in await ungated_getter(anchor):
            neighbor_raw = _similar_signal_field(sig, "product_id")
            neighbor = str(neighbor_raw) if neighbor_raw else ""
            if not neighbor or neighbor == anchor or neighbor in exclude_ids:
                continue
            if require_ids is not None and neighbor not in require_ids:
                continue
            try:
                score = float(_similar_signal_field(sig, "score"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(score) or score <= 0.0:
                continue
            existing = best.get(neighbor)
            if existing is not None and existing["score"] >= score:
                continue
            axes = _similar_signal_field(sig, "shared_axes") or []
            best[neighbor] = {
                "product_id": neighbor,
                "neighbor_name": str(_similar_signal_field(sig, "neighbor_name") or neighbor),
                "score": score,
                "shared_axes": [dict(ax) for ax in axes],
                "anchor_product_id": anchor,
                "anchor_name": anchor_name,
            }
    ranked = sorted(best.values(), key=lambda entry: (-entry["score"], entry["product_id"]))[:limit]
    for entry in ranked:
        entry["score"] = round(entry["score"], 4)
    return ranked


# =============================================================================
# Search (Phase 4.2: concept-based search, not full-text)
# =============================================================================
#
# `/api/search` resolves the query text into known concepts (brand/category/
# ingredient/concern/goal/keyword — see src/rec/search.py) and ranks products
# by concept overlap. It reuses the same serving store + evidence-family
# classification as `/api/recommend`, but needs no user profile (anonymous
# search) and no scorer (simple overlap relevance, not the weighted score).
# A query that resolves to no concept returns an explicit, non-empty message
# rather than silently falling back to full-text search.

_SEARCH_NO_CONCEPT_MESSAGE = (
    "질의에서 해석된 concept이 없습니다. 브랜드/카테고리/성분/피부고민/케어목표/키워드 등 "
    "구체적인 표현을 포함해 다시 검색해주세요. (전문 검색이 아닌 concept 기반 검색입니다.)"
)

# [A2 F4] Exclusion-only anonymous query ("스킨케어 빼고 추천해줘"): the query resolved
# ONLY exclusions (no positive concept to rank), so concept search has nothing to
# return. Surface honest guidance instead of a silent empty list.
_SEARCH_EXCLUSION_ONLY_MESSAGE = (
    "제외 조건만 해석되었습니다. 원하는 조건(카테고리·성분·효능 등)을 함께 입력해주세요."
)


class SearchRequest(BaseModel):
    # `query` defaults to "" so a missing/blank query behaves the same on POST as
    # on GET (`search_get(query="")`): both return HTTP 200 with an explicit
    # no-concept guidance message, rather than POST alone raising a 422 for a
    # missing required field.
    query: str = ""
    top_k: int = 20


def _clamp_search_top_k(top_k: int) -> int:
    return max(1, min(int(top_k), 200))


async def _run_search(query: str, top_k: int) -> dict[str, Any]:
    """`/api/search` internally unified onto the anonymous /api/ask pipeline (plan
    §B2 v3, user-agreed 2번안): understand_query → wanted-ingredient constraints →
    search_products(+constraints) → related (same matcher filter), returning the
    identical anonymous-ask payload.

    Input contract stays on the route (shared payload receives a validated
    (query, top_k)): a blank/whitespace query returns HTTP 200 + the no-concept
    guidance message (never 400 — existing search contract), top_k defaults to 20
    and is clamped to [1, 200] inside the flow, and only an over-length query is
    rejected with 400 (aligned with /api/ask; understand_query truncates at the
    same limit)."""
    query = (query or "").strip()
    if len(query) > _ASK_MAX_QUERY_LEN:
        raise HTTPException(400, f"query exceeds the {_ASK_MAX_QUERY_LEN}-character limit.")
    _check_serving_ready()
    store = get_serving_store()
    products = await store.get_products()
    interp = await _understand_query_async(query, products)
    category_group = _ask_category_group(interp, None)
    return await _anonymous_ask_payload(query, interp, products, store, top_k, category_group)


@app.get("/api/search")
async def search_get(query: str = "", top_k: int = 20):
    return await _run_search(query, top_k)


@app.post("/api/search")
async def search(req: SearchRequest):
    return await _run_search(req.query, req.top_k)


# =============================================================================
# Ask — query-scoped recommend / search (Phase 6 Track B, B2)
# =============================================================================
#
# `/api/ask` is the service-facing entry the frontend query box calls. It runs
# the query through `understand_query` (LLM translator with an automatic
# dictionary fallback), then routes on user presence:
#   - no user_id  → anonymous concept search (+ avoided-ingredient hard filter).
#   - user_id     → query-scoped recommendation: the query's concern/goal/
#     keyword/ingredient/brand concepts are injected as REQUEST-SCOPED scoped
#     preferences onto a DEEP COPY of the user profile (never the shared cache),
#     the candidate universe is narrowed to query-relevant products (auto-relaxed
#     when the intersection is empty), and the query-injected concepts' paths are
#     relabeled "질의에서 언급" so the UI distinguishes them from stored prefs.
# The LLM never scores or invents evidence; it only translates free text into the
# existing ontology, so recommend/search reuse the identical evidence pipeline.

_ASK_MAX_QUERY_LEN = 500
_ASK_QUERY_USER_EDGE = "질의에서 언급"

# search.MatchedConcept.concept_type → the serving edge_type the recommendation
# consumer already understands. Verified against candidate_generator
# .collect_preference_ids / build_serving_views._collect (goal is WANTS_GOAL, not
# PURSUES_GOAL). Category is intentionally absent: it is consumed as the candidate
# *universe* axis (below), never injected as a preference.
_QUERY_INJECT_EDGE_TYPE = {
    "concern": "HAS_CONCERN",
    "goal": "WANTS_GOAL",
    "keyword": "PREFERS_KEYWORD",
    "ingredient": "PREFERS_INGREDIENT",
    "brand": "PREFERS_BRAND",
}

# Category-*group* concept ids search emits (concept:Category:<group>). Literal
# catalog category concepts (e.g. concept:Category:쿠션) are NOT group ids and are
# deliberately excluded, so only a real tab group maps the query to a universe.
_GROUP_CATEGORY_CONCEPT_IDS = {
    f"concept:Category:{item['group']}"
    for item in RECOMMEND_CATEGORY_DEFS
    if str(item["group"]) not in ("all", "other")
}


class AskRequest(BaseModel):
    user_id: str | None = None
    query: str = ""
    preset: str | None = None
    category_group: str | None = None
    top_k: int = 10


def _ask_category_group(interp: QueryInterpretation, requested: str | None) -> str:
    """Resolve the query's category group: a group concept in the interpretation
    wins over the request hint (first one, if several); otherwise the validated
    request hint; otherwise "all".

    [A2] An EXCLUDED category group ("스킨케어 빼고") can never be selected — the
    exclusion wins over both a positive group concept (the query_understanding layer
    already subtracts it, so this is belt-and-suspenders) and the request hint, so a
    request-hinted skincare tab is invalidated when the query negates skincare."""
    excluded_groups = {str(g) for g in interp.excluded_category_groups}
    for concept in interp.resolved_concepts:
        if concept.concept_type == "category" and concept.concept_id in _GROUP_CATEGORY_CONCEPT_IDS:
            group = concept.concept_id[len("concept:Category:"):]
            if group not in excluded_groups:
                return group
    if requested and requested in RECOMMEND_CATEGORY_LABELS and requested not in excluded_groups:
        return requested
    return "all"


def _inject_query_preferences(
    user_profile: dict[str, Any],
    interp: QueryInterpretation,
    scope_group: str,
) -> set[str]:
    """Append the query's concepts to ``user_profile`` as request-scoped
    scoped_preference entries, and return the injected POSITIVE concept ids (for
    the user_edge relabel).

    MUST be called only on a deep copy (see the /api/ask C1 note). The scoped
    shape mirrors build_serving_views._collect_scoped exactly; real users are
    scoped-only (scoped_preferences.py), so preferences must be injected as
    scoped entries — a legacy top-level field would be ignored.
    """
    scoped = user_profile.get("scoped_preference_ids")
    if not isinstance(scoped, list):
        scoped = []
    else:
        scoped = list(scoped)  # copy defensively even though the caller deep-copied
    user_profile["scoped_preference_ids"] = scoped

    injected_ids: set[str] = set()
    for concept in interp.resolved_concepts:
        edge_type = _QUERY_INJECT_EDGE_TYPE.get(concept.concept_type)
        if not edge_type or not concept.concept_id:
            continue
        scoped.append({
            "edge_type": edge_type,
            "id": concept.concept_id,
            "weight": 1.0,
            "scope_group": scope_group,
            "source_sections": ["query"],
        })
        injected_ids.add(concept.concept_id)

    # Avoided ingredients → request-scoped AVOIDS_INGREDIENT (global) so the
    # recommendation candidate generator's avoided hard filter drops carriers,
    # matching the search path. Scope must be global to apply across categories.
    for cid in interp.avoided_ingredient_concept_ids:
        if not cid:
            continue
        scoped.append({
            "edge_type": "AVOIDS_INGREDIENT",
            "id": cid,
            "weight": 1.0,
            "scope_group": "global",
            "source_sections": ["query"],
        })

    return injected_ids


# [F4-c''] Profile-reference injection — the server side of query_understanding's
# schema-based class selection. Distinct from _inject_query_preferences: profile
# refs are the LOGGED-IN USER's OWN stored concepts, joined deterministically from
# the enum classes the LLM picked. They must NOT join resolved_concepts (no query
# narrowing) and must NOT be relabeled "질의에서 언급" (they are stored prefs, not
# query mentions), so the adapter returns a display payload the caller keeps
# separate from the query-injected id set.
#
# Each class → list of (source_edge, inject_edge, legacy_field, label_kind):
#   - source_edge : scoped_preference edge_type to READ the user's concepts (+ their
#                   scope) from — mirrors build_serving_views._collect field origins.
#   - inject_edge : scoring edge_type to WRITE. For the "same-edge" classes it equals
#                   source_edge, so re-injection is a scoring NO-OP (candidate_generator
#                   collects prefs as a SET) — pure display. ``repurchase`` maps
#                   REPURCHASES_* → PREFERS_*, which the scorer DOES consume, so it can
#                   add a genuine but idempotent boost.
#   - legacy_field: fallback source when the profile carries no scoped prefs (older
#                   shape); scope defaults to global there.
_PROFILE_REF_SPECS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "concerns": (("HAS_CONCERN", "HAS_CONCERN", "concern_ids", "concern"),),
    "skin": (("HAS_CONCERN", "HAS_CONCERN", "concern_ids", "concern"),),
    "goals": (("WANTS_GOAL", "WANTS_GOAL", "goal_ids", "goal"),),
    "preferred_brands": (("PREFERS_BRAND", "PREFERS_BRAND", "preferred_brand_ids", "brand"),),
    "preferred_keywords": (("PREFERS_KEYWORD", "PREFERS_KEYWORD", "preferred_keyword_ids", "keyword"),),
    "repurchase": (
        ("REPURCHASES_BRAND", "PREFERS_BRAND", "repurchase_brand_ids", "brand"),
        ("REPURCHASES_CATEGORY", "PREFERS_CATEGORY", "repurchase_category_ids", "category"),
    ),
}
# ``owned`` is display-only: Phase 8 G4's similar-boost already reflects owned
# products on the shared scored path (_run_scored_pipeline), so re-injecting them
# would double an always-on signal. Accepted as a class and shown, never injected.
_PROFILE_REF_OWNED_CLASS = "owned"
_PROFILE_REF_LABEL_CAP = 6  # concepts shown per class in the response (display only)
_PROFILE_REF_SOURCE_SECTION = "profile_ref"


def _norm_profile_scope(scope: Any) -> str:
    """Canonical scope token for dedup: all global-equivalent scopes collapse to
    "global" so a None-scoped and a "global"-scoped entry are treated as one."""
    return "global" if scope in GLOBAL_SCOPES else str(scope)


def _profile_ref_label(kind: str, concept_id: str) -> str:
    """Human label for a profile-ref concept, reusing existing conventions: the
    concern axis has a Korean label map (concept_resolver.concern_label); every
    other axis embeds its surface in the id suffix (brand/goal/keyword/category),
    matching the ask-chip + user-graph rendering (last ':' segment)."""
    if kind == "concern":
        from src.common.concept_resolver import concern_label
        label = concern_label(concept_id)
        # concern_label passes unmapped ids through unchanged (full IRI) —
        # fall back to the id-suffix convention like every other axis.
        if label != concept_id:
            return label
    return concept_id.split(":")[-1] if ":" in concept_id else concept_id


def _profile_ref_concepts(
    user_profile: dict[str, Any],
    source_edge: str,
    legacy_field: str,
) -> list[tuple[str, Any]]:
    """(concept_id, scope) pairs for a source edge, scope-preserving. Mirrors
    collect_preference_ids' branch exactly: scoped-first (carries scope), else the
    legacy top-level field (scope defaults to None → global). Deterministic order,
    deduped on concept_id."""
    pairs: list[tuple[str, Any]] = []
    seen: set[str] = set()
    if has_scoped_preferences(user_profile):
        for item in iter_scoped_preferences(user_profile, edge_type=source_edge):
            cid = str(item.get("id") or "")
            if cid and cid not in seen:
                seen.add(cid)
                pairs.append((cid, item.get("scope_group")))
        return pairs
    for raw in user_profile.get(legacy_field) or []:
        cid = str((raw.get("id") if isinstance(raw, dict) else raw) or "")
        if cid and cid not in seen:
            seen.add(cid)
            pairs.append((cid, None))
    return pairs


def _apply_profile_refs(
    user_profile: dict[str, Any],
    classes: list[str],
) -> list[dict[str, Any]]:
    """Join the logged-in user's concepts for the LLM-selected profile-ref
    ``classes`` onto ``user_profile`` (a DEEP COPY — same C1 contract as
    _inject_query_preferences) and return the display payload
    ``[{class, concepts: [labels], injected}]``.

    Semantics (plan §F4-c'', codex #1/#2):
      - resolved_concepts are NOT touched (no query narrowing).
      - Each injected entry preserves the concept's ORIGINAL scope.
      - Dedup on (inject_edge, normalized_id, normalized_scope): an already-active
        preference is recorded (its class stays displayed) but NOT re-appended — the
        candidate generator collects prefs as a set, so a duplicate is a scoring no-op.
      - ``owned`` is display-only (never injected — G4 already boosts owned).
      - Returns only classes that resolved to ≥1 concept; empty classes are dropped
        (nothing to show) while interpretation.profile_refs keeps the raw selection.
    """
    scoped = user_profile.get("scoped_preference_ids")
    if not isinstance(scoped, list):
        scoped = []
        user_profile["scoped_preference_ids"] = scoped

    active: set[tuple[str, str, str]] = {
        (
            str(item.get("edge_type") or ""),
            normalize_signal_id(item.get("id")),
            _norm_profile_scope(item.get("scope_group")),
        )
        for item in scoped
        if isinstance(item, dict) and item.get("id")
    }

    applied: list[dict[str, Any]] = []
    seen_class: set[str] = set()
    for cls in classes:
        if cls in seen_class:
            continue
        seen_class.add(cls)

        if cls == _PROFILE_REF_OWNED_CLASS:
            owned = sorted(extract_owned_product_ids(user_profile))
            if owned:
                applied.append({
                    "class": cls,
                    "concepts": [pid.split(":")[-1] for pid in owned[:_PROFILE_REF_LABEL_CAP]],
                    "injected": False,
                })
            continue

        specs = _PROFILE_REF_SPECS.get(cls)
        if not specs:
            continue

        labels: list[str] = []
        seen_label: set[str] = set()
        injected_any = False
        for source_edge, inject_edge, legacy_field, kind in specs:
            for concept_id, scope in _profile_ref_concepts(user_profile, source_edge, legacy_field):
                label = _profile_ref_label(kind, concept_id)
                if label not in seen_label:
                    seen_label.add(label)
                    labels.append(label)
                key = (inject_edge, normalize_signal_id(concept_id), _norm_profile_scope(scope))
                if key in active:
                    continue  # already active → scoring no-op, display only
                scoped.append({
                    "edge_type": inject_edge,
                    "id": concept_id,
                    "weight": 1.0,
                    "scope_group": scope,
                    "source_sections": [_PROFILE_REF_SOURCE_SECTION],
                })
                active.add(key)
                injected_any = True
        if labels:
            applied.append({
                "class": cls,
                "concepts": labels[:_PROFILE_REF_LABEL_CAP],
                "injected": injected_any,
            })
    return applied


def _narrow_candidate_universe(
    interp: QueryInterpretation,
    product_map: dict[str, dict[str, Any]],
    category_universe_ids: list[str],
) -> tuple[list[str], bool]:
    """Narrow the category universe to products carrying a NON-category, NON-
    ingredient query concept (the soft axes injected as preferences). Empty
    intersection → return the full category universe with ``relaxed=True`` (recall
    protection, plan decision 2). No such concept → no narrowing, ``relaxed=False``.

    Ingredient concepts are EXCLUDED from this OR-reduction (Phase 6 B2): the
    wanted-ingredient hard gate already narrowed the universe upstream, so counting
    ingredient here would double-apply it (raw families) or let an LLM-only family
    hard-narrow the universe (llm families are soft-boost only).

    Product concepts (A1) are EXCLUDED too: a pin overlaps only its own product, so
    including it would collapse the soft-narrowed universe to just the pin(s) when a
    product is the query's only non-category/ingredient concept — losing the
    surrounding personalized recommendations. Pin inclusion is handled by the
    caller's universe-union + the pin-block assembly, not by soft narrowing."""
    narrowing: list[MatchedConcept] = [
        c
        for c in interp.resolved_concepts
        if c.concept_type not in ("category", "ingredient", "product")
    ]
    if not narrowing:
        return list(category_universe_ids), False
    carrying = {
        pid
        for pid in category_universe_ids
        if _product_overlap(product_map[pid], narrowing)
    }
    scoped = [pid for pid in category_universe_ids if pid in carrying]
    if scoped:
        return scoped, False
    return list(category_universe_ids), True


def _ask_preset_config(
    preset_key: str | None,
) -> tuple[dict[str, Any] | None, dict[str, float] | None, str, float, float]:
    """Resolve an optional preset into (preset_used, materialized_weights, mode,
    shrinkage_k, diversity_weight). No preset → recommend defaults (the exact
    RecommendRequest field defaults, so an unpreset ask == a default recommend)."""
    effective_mode = str(RecommendRequest.model_fields["mode"].default)
    effective_shrinkage_k = _DEFAULT_SHRINKAGE_K
    effective_diversity_weight = float(RecommendRequest.model_fields["diversity_weight"].default)
    if not preset_key:
        return None, None, effective_mode, effective_shrinkage_k, effective_diversity_weight

    preset = _resolve_preset(preset_key)
    base_scorer = Scorer()
    base_scorer.load_config()
    overrides = {str(k): float(v) for k, v in (preset.get("weight_overrides") or {}).items()}
    materialized_weights = {**base_scorer.weights, **overrides}
    effective_mode = str(preset.get("mode", effective_mode))
    effective_shrinkage_k = float(preset.get("shrinkage_k", effective_shrinkage_k))
    effective_diversity_weight = float(preset.get("diversity_weight", effective_diversity_weight))
    preset_used = {
        "key": preset_key,
        "label_ko": preset.get("label_ko", preset_key),
        "mode": effective_mode,
        "shrinkage_k": effective_shrinkage_k,
        "diversity_weight": effective_diversity_weight,
        "weight_overrides": overrides,
    }
    return preset_used, materialized_weights, effective_mode, effective_shrinkage_k, effective_diversity_weight


# ---------------------------------------------------------------------------
# Phase 6 B2: wanted-ingredient hard filter + relaxation + shared anonymous flow
# ---------------------------------------------------------------------------
#
# The wanted-ingredient hard gate keeps only products that CARRY a query
# ingredient family (structured ∪ product-name axis, AND across families), using
# the single pure matcher (src/rec/ingredient_constraint.py). Only raw-provenance
# constraints are hard (an alias/INCI surface literally in the query, outside a
# negation span); LLM-only families stay soft (PREFERS_INGREDIENT boost). If the
# gate empties the universe it is relaxed (ingredient condition only) so the user
# still gets a broadened, honestly-labelled result instead of nothing.

# User-facing reason attached to ``ingredient_filter`` when the gate matched no
# product and was relaxed (plan §B2 relax c안).
_INGREDIENT_RELAX_REASON = "요청한 성분을 함유한 상품이 없어 성분 조건을 완화했습니다"


def _ingredient_filter_meta(
    labels: list[str],
    matched_products: int,
    relaxed: bool,
    reason: str | None,
    evidence_unknown: int = 0,
) -> dict[str, Any]:
    """The ``ingredient_filter`` response block (plan §B2). ``applied`` is True only
    when a raw+required ingredient family constrained the returned results (labels
    present AND not relaxed): an LLM-only/preferred family (no labels here) or a
    relaxed gate both report ``applied=False`` while ``labels`` still names what was
    requested.

    [A4] ``evidence_unknown_products`` is the count of gate-eliminated products with
    no ingredient evidence for at least one required family ("확인 불가"). It is
    meaningful ONLY when the filter is applied; when not applied / relaxed it is
    forced to 0 (fixed rule so the frontend never renders a stale count)."""
    applied = bool(labels) and not relaxed
    return {
        "applied": applied,
        "labels": list(labels),
        "matched_products": matched_products,
        "relaxed": relaxed,
        "reason": reason,
        "evidence_unknown_products": evidence_unknown if applied else 0,
    }


async def _understand_query_async(
    query: str, products: list[dict[str, Any]]
) -> QueryInterpretation:
    """Run the synchronous ``understand_query`` off the event loop (blocking httpx
    on the active-provider path; the off/fallback path is cheap but harmless to
    offload). Shared by /api/ask and the /api/search unification."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, functools.partial(understand_query, query, products)
    )


async def _anonymous_ask_payload(
    query: str,
    interp: QueryInterpretation,
    products: list[dict[str, Any]],
    store: ServingStore,
    top_k: int,
    category_group: str,
) -> dict[str, Any]:
    """Anonymous concept search shared by /api/ask (no user_id) and /api/search
    (plan §B2 v3 unification). Applies the raw wanted-ingredient hard gate (+ relax
    when it empties the results), the avoided-ingredient hard filter, and the same
    matcher filter on related products; returns the unified anonymous payload
    (ask shape + the ``message`` no-concept rule /api/search keeps)."""
    # [A3] Hard gate = provenance=="raw" AND strength=="required". A preferred family
    # ("있으면 더 좋고") never hard-gates — its ingredient concept still rides the
    # search overlap/boost and is surfaced under ``ingredient_preferences`` (below).
    raw_constraints = [
        c for c in interp.ingredient_constraints
        if c.provenance == "raw" and c.strength == "required"
    ]
    # [A3] Preferred families (any provenance) — soft boost only; surfaced as a
    # "선호 반영" list, never a hard filter. NOTE the documented anonymous
    # degeneracy: search qualification is "overlap ≥ 1", so a preferred-only query
    # structurally returns only carriers (the preferred ingredient is the only
    # overlap axis) — surfaced honestly here as a preference, with
    # ``ingredient_filter.applied`` staying False. No full-catalog ranking of
    # non-carriers is introduced (that would be more dishonest, not less).
    ingredient_preferences = [
        c.label for c in interp.ingredient_constraints if c.strength == "preferred"
    ]
    avoided_ids = interp.avoided_ingredient_concept_ids
    query_avoided = {str(cid) for cid in (avoided_ids or []) if cid}
    max_results = _clamp_search_top_k(top_k)
    # A1: product pins + exclusions (exclusion wins over pin).
    excluded_product_ids = {str(pid) for pid in interp.excluded_product_ids if pid}
    # A2: brand / literal-category / category-group exclusions + the product ids they
    # hard-exclude (parity with excluded_product_ids in the relax count / related set;
    # search_products applies the actual hard filter).
    excluded_brand_ids = {str(b) for b in interp.excluded_brand_ids if b}
    excluded_category_surfaces = {str(s) for s in interp.excluded_category_surfaces if s}
    excluded_category_groups = {str(g) for g in interp.excluded_category_groups if g}
    axis_excluded_pids = _axis_excluded_product_ids(
        products,
        brand_ids=excluded_brand_ids,
        category_surfaces=excluded_category_surfaces,
        category_groups=excluded_category_groups,
    )
    products_by_id = {str(p.get("product_id") or ""): p for p in products}
    raw_pins = {
        c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"
    } - excluded_product_ids
    # A2: a pin hit by an explicit brand/category/group exclusion is dropped with a
    # specific reason ("명시 배제 > 핀") before the category-group gate below.
    pin_axis_dropped: list[dict[str, str]] = []
    for pid in sorted(raw_pins & axis_excluded_pids):
        product = products_by_id.get(pid)
        reason = "excluded_category"
        if product is not None:
            if excluded_brand_ids and (
                {str(b) for b in (product.get("brand_concept_ids") or [])} & excluded_brand_ids
            ):
                reason = "excluded_brand"
            elif excluded_category_surfaces and _label_has_surface(
                product, excluded_category_surfaces
            ):
                reason = "excluded_category"
            elif excluded_category_groups and (
                classify_product_category_group(product) in excluded_category_groups
            ):
                reason = "excluded_category_group"
        pin_axis_dropped.append({"id": pid, "reason": reason})
    raw_pins -= axis_excluded_pids
    # F4: the category gate is a hard filter over PINS (parity with login). With an
    # explicit group, a pin classifying to a different group is dropped (reason
    # "category"); general search results are NOT category-gated here (legacy — a
    # separate decision). None-group leaves every pin eligible.
    pin_category_dropped: list[dict[str, str]] = []
    if category_group != "all":
        query_product_ids: set[str] = set()
        for pid in raw_pins:
            product = products_by_id.get(pid)
            if product is not None and classify_product_category_group(product) == category_group:
                query_product_ids.add(pid)
            else:
                pin_category_dropped.append({"id": pid, "reason": "category"})
    else:
        query_product_ids = set(raw_pins)

    def _carries_avoided(product: dict[str, Any]) -> bool:
        return bool(query_avoided) and bool(
            {str(v) for v in (product.get("ingredient_concept_ids") or [])} & query_avoided
        )

    ingredient_relaxed = False
    ingredient_evidence_unknown = 0
    if raw_constraints:
        # F4: honour the category gate for the ingredient universe (login parity)
        # — a "히알루론 수분크림" must not surface a lipstick hyaluron carrier.
        if category_group != "all":
            universe = [
                p for p in products
                if classify_product_category_group(p) == category_group
            ]
        else:
            universe = products
        # [A4] Gate DENOMINATOR: the (category) universe AFTER removing avoided AND
        # explicitly-excluded (A1 product / A2 brand·category) products — the exact
        # stage the hard gate runs at. matched_products is the passing subset;
        # evidence-unknown is counted over the eliminated remainder.
        gate_universe = [
            p for p in universe
            if not _carries_avoided(p)
            and str(p.get("product_id") or "") not in excluded_product_ids
            and str(p.get("product_id") or "") not in axis_excluded_pids
        ]
        # F5: matched = carriers in the denominator, so applied/relaxed reflect what
        # search_products actually returns — never applied=true with 0 results.
        matched_products = [
            p for p in gate_universe if product_passes_constraints(p, raw_constraints)
        ]
        ingredient_relaxed = not matched_products
        # [A4] Evidence-unknown only meaningful when the gate stays applied.
        if not ingredient_relaxed:
            ingredient_evidence_unknown = count_evidence_unknown_products(
                gate_universe, raw_constraints
            )
        outcome = search_products(
            query,
            universe,
            max_results=max_results,
            avoided_ingredient_concept_ids=avoided_ids,
            # Relax the ingredient condition ONLY (category + avoided still apply)
            # when nothing carries the family — broaden honestly rather than [].
            ingredient_constraints=None if ingredient_relaxed else raw_constraints,
            # F1: pass the sets (not None) so search_products treats the guarded
            # interpretation as authoritative over internal product re-resolution.
            query_product_ids=query_product_ids,
            excluded_product_ids=excluded_product_ids,
            excluded_brand_ids=excluded_brand_ids,
            excluded_category_surfaces=excluded_category_surfaces,
            excluded_category_groups=excluded_category_groups,
        )
        ingredient_filter = _ingredient_filter_meta(
            [c.label for c in raw_constraints],
            len(matched_products),
            ingredient_relaxed,
            _INGREDIENT_RELAX_REASON if ingredient_relaxed else None,
            ingredient_evidence_unknown,
        )
    else:
        outcome = search_products(
            query,
            products,
            max_results=max_results,
            avoided_ingredient_concept_ids=avoided_ids,
            query_product_ids=query_product_ids,
            excluded_product_ids=excluded_product_ids,
            excluded_brand_ids=excluded_brand_ids,
            excluded_category_surfaces=excluded_category_surfaces,
            excluded_category_groups=excluded_category_groups,
        )
        ingredient_filter = _ingredient_filter_meta([], 0, False, None)

    payload = outcome.to_dict()
    search_results = payload["results"]

    # Related products (additive): exclude the 1차 results + any query-negated
    # ingredient carrier; when the ingredient filter is ACTIVE (raw families,
    # not relaxed), also require neighbours to pass the same matcher so a
    # non-containing product is never re-surfaced under the filter. Related is
    # intentionally cross-category, so require_ids is the CORPUS-WIDE carrier set
    # (not the category-scoped matched set above).
    search_result_pids = [str(r["product_id"]) for r in search_results if r.get("product_id")]
    exclude_ids = set(search_result_pids)
    if query_avoided:
        exclude_ids.update(
            str(p.get("product_id")) for p in products if _carries_avoided(p)
        )
    exclude_ids |= excluded_product_ids  # A1: a negated product is excluded from related too
    exclude_ids |= axis_excluded_pids  # A2: negated brand/category/group excluded from related too
    require_ids: set[str] | None = None
    if raw_constraints and not ingredient_relaxed:
        require_ids = {
            str(p.get("product_id"))
            for p in products
            if p.get("product_id") and product_passes_constraints(p, raw_constraints)
        }
    # A1: anchor related on the pinned products first, then the remaining results.
    # ``pinned_applied`` follows the result order (search_products already assembled
    # pins as a leading, relevance-ordered block) → deterministic (F10-consistent).
    result_id_set = set(search_result_pids)
    pinned_applied = [pid for pid in search_result_pids if pid in query_product_ids]
    # Pin trace (F12): the excluded reason is recorded here (excluded products were
    # never in query_product_ids, so they are added explicitly), plus A2 axis-excluded
    # pins ("명시 배제 > 핀"), category-gated pins (F4), and any remaining pin that did
    # not reach the results.
    pinned_dropped = [{"id": pid, "reason": "excluded_product"} for pid in sorted(excluded_product_ids)]
    pinned_dropped += pin_axis_dropped
    pinned_dropped += pin_category_dropped
    pinned_dropped += [
        {"id": pid, "reason": "filtered"}
        for pid in sorted(query_product_ids)
        if pid not in result_id_set
    ]
    pinned_applied_set = set(pinned_applied)
    anchor_order = pinned_applied + [p for p in search_result_pids if p not in pinned_applied_set]
    related = await _related_products(
        anchor_order[:5],
        store=store,
        exclude_ids=exclude_ids,
        anchor_names=_related_anchor_names(search_results),
        require_ids=require_ids,
    )

    # [A2 F4] message: no-concept guidance when nothing resolved; exclusion-only
    # guidance when the only resolved signals are exclusions (no positive concept to
    # rank) AND search returned nothing; else None (honest, consistent placement).
    if not outcome.resolved:
        message = _SEARCH_NO_CONCEPT_MESSAGE
    elif not search_results and _is_exclusion_only(interp):
        message = _SEARCH_EXCLUSION_ONLY_MESSAGE
    else:
        message = None

    return {
        "query": query,
        "interpretation": interp.to_dict(),
        "resolved_mode": "search",
        "relaxed": ingredient_relaxed,
        "category_group": category_group,
        "preset_used": None,
        "message": message,
        "ingredient_filter": ingredient_filter,
        # [A3] Preferred ingredient labels ("있으면 더 좋고") — soft, never a hard
        # filter; the frontend renders these as a "선호 반영" note (see the
        # documented anonymous preferred-only degeneracy above).
        "ingredient_preferences": ingredient_preferences,
        # [A1] Pin trace (same shape as the recommend branch): named products
        # pinned into the results, and any pin removed (excluded / filtered).
        "pinned_product_ids": pinned_applied,
        "pinned_dropped": pinned_dropped,
        # [A2] Labeled exclusion audit — same shape as the recommend branch.
        "excluded": _excluded_meta(products, interp),
        "results": search_results,
        "related_products": related,
    }


@app.post("/api/ask")
async def ask(req: AskRequest):
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(400, "query is required.")
    if len(query) > _ASK_MAX_QUERY_LEN:
        raise HTTPException(400, f"query exceeds the {_ASK_MAX_QUERY_LEN}-character limit.")

    # Validate any preset up front (recommend-mode only; search mode ignores it),
    # so an unknown preset 400s before the LLM/search work.
    (preset_used, materialized_weights, effective_mode,
     effective_shrinkage_k, effective_diversity_weight) = _ask_preset_config(req.preset)

    _check_serving_ready()
    store = get_serving_store()
    products = await store.get_products()

    # LLM query understanding. understand_query builds its own provider from
    # GRAPHRAPPING_QUERY_LLM (auto-off/dictionary-fallback when unset) and never
    # raises — a provider outage degrades to the dictionary path transparently.
    interp = await _understand_query_async(query, products)
    category_group = _ask_category_group(interp, req.category_group)

    # --- (a) Anonymous search mode (shared with /api/search, plan §B2 v3) ---
    if not req.user_id:
        return await _anonymous_ask_payload(
            query, interp, products, store, req.top_k, category_group,
        )

    # --- (b) Query-scoped recommend mode ---
    user = await store.get_user(req.user_id)
    if not user:
        raise HTTPException(404, "User not found")

    # [C1] Deep-copy BEFORE any injection: the serving store returns a reference
    # to its cached user dict, so mutating it in place would persist the
    # request-scoped query preferences into the shared profile (personalization
    # pollution). All injection happens on this copy only.
    user_scoped = copy.deepcopy(user)
    scope_group = "global" if category_group == "all" else category_group
    injected_ids = _inject_query_preferences(user_scoped, interp, scope_group)
    # [F4-c''] Join the user's own concepts for the LLM-selected profile-ref classes
    # onto the SAME deep copy (scoring no-op for already-active prefs; genuine
    # idempotent boost for repurchase → PREFERS_*). Kept SEPARATE from injected_ids
    # so the "질의에서 언급" relabel below never mislabels a stored preference.
    applied_profile_refs = _apply_profile_refs(user_scoped, interp.profile_refs)

    product_map = {p["product_id"]: p for p in products}
    category_universe_ids = _category_universe_ids(product_map, category_group)
    # Avoided-ingredient carriers (query-injected AVOIDS_INGREDIENT + stored prefs)
    # computed once — reused by both the ingredient gate (F5) and related below.
    avoided_pids = _avoided_ingredient_product_ids(user_scoped, product_map)
    # A1: products the query negated by name — reused by the pin trace below.
    excluded_product_ids = {str(pid) for pid in interp.excluded_product_ids if pid}
    # A2: brand / literal-category-surface / category-group exclusions + the set of
    # product ids they hard-exclude.
    excluded_brand_ids = {str(b) for b in interp.excluded_brand_ids if b}
    excluded_category_surfaces = {str(s) for s in interp.excluded_category_surfaces if s}
    excluded_category_groups = {str(g) for g in interp.excluded_category_groups if g}
    axis_excluded_pids = _axis_excluded_product_ids(
        products,
        brand_ids=excluded_brand_ids,
        category_surfaces=excluded_category_surfaces,
        category_groups=excluded_category_groups,
    )
    # [F4] Remove ALL negated products (A1) + axis-excluded products (A2) from the
    # candidate universe at a SINGLE point — right after the category gate, BEFORE the
    # ingredient gate and soft narrowing. Otherwise an excluded product that carries a
    # positive keyword could satisfy the soft-narrow intersection and keep the
    # narrowed universe non-empty (false-zero: the relax that should fire does not).
    # Downstream stages (relax count, soft narrow, candidate generation) are then
    # naturally consistent; candidate_generator still hard-filters as a backstop.
    universe_excluded = excluded_product_ids | axis_excluded_pids
    if universe_excluded:
        category_universe_ids = [
            pid for pid in category_universe_ids if pid not in universe_excluded
        ]

    # Ingredient HARD gate — [A3] provenance=="raw" AND strength=="required" families
    # only — applied INSIDE the category universe, BEFORE soft narrowing (plan §B2
    # order). A product passes via the structured OR product-name axis (AND across
    # families). 0 matches → relax the ingredient condition ONLY (keep the category
    # universe; category / avoided / other conditions untouched); ≥1 → keep the gate.
    raw_constraints = [
        c for c in interp.ingredient_constraints
        if c.provenance == "raw" and c.strength == "required"
    ]
    # [A3] Preferred families ("있으면 더 좋고") — never hard-gate; their ingredient
    # concept already earns the injected PREFERS_INGREDIENT boost, and the labels are
    # surfaced as a "선호 반영" list in the payload below.
    ingredient_preferences = [
        c.label for c in interp.ingredient_constraints if c.strength == "preferred"
    ]
    ingredient_relaxed = False
    ingredient_matched_count = 0
    ingredient_evidence_unknown = 0
    ingredient_name_labels: dict[str, list[str]] = {}
    if raw_constraints:
        # F5: exclude avoided carriers from the gate universe BEFORE counting, so
        # matched/relaxed reflect the true candidate set — never applied=true while
        # every carrier is dropped by the avoided filter (→ 0 results). Negated
        # products + axis-excluded (A1/A2) are ALREADY gone from category_universe_ids
        # (F4 single-point removal above), so only the avoided filter remains here.
        # [A4] This same avoided-removed universe is the evidence-unknown DENOMINATOR.
        gate_universe_ids = [pid for pid in category_universe_ids if pid not in avoided_pids]
        gated_ids = [
            pid
            for pid in gate_universe_ids
            if product_passes_constraints(product_map[pid], raw_constraints)
        ]
        ingredient_matched_count = len(gated_ids)
        if ingredient_matched_count == 0:
            ingredient_universe_ids = category_universe_ids
            ingredient_relaxed = True
        else:
            ingredient_universe_ids = gated_ids
            # Name-only carriers (structured carriers already earn an ingredient
            # overlap via the injected PREFERS_INGREDIENT) get a product_name axis
            # so they clear the candidate evidence gate.
            for pid in gated_ids:
                labels = matched_name_labels(product_map[pid], raw_constraints)
                if labels:
                    ingredient_name_labels[pid] = labels
            # [A4] Evidence-unknown count over the same denominator (only when the
            # gate stays applied — a relaxed gate forces 0 in the meta).
            ingredient_evidence_unknown = count_evidence_unknown_products(
                [product_map[pid] for pid in gate_universe_ids], raw_constraints
            )
    else:
        ingredient_universe_ids = category_universe_ids

    # --- A1: product pins + exclusions ---
    # Pins are the query's resolved product concepts (the brand-contradiction guard
    # already dropped brand-mismatched products upstream). Exclusions are products
    # the query negated by name. A pin hit by a HARD gate — excluded / outside the
    # category universe / avoided-ingredient carrier / failing an ACTIVE ingredient
    # gate — is recorded in the trace instead of pinned (hard filters beat pins);
    # the survivors are unioned into the candidate universe below so soft narrowing
    # cannot drop them.
    query_product_ids = {
        c.concept_id for c in interp.resolved_concepts if c.concept_type == "product"
    }
    # ``excluded_product_ids`` computed above (before the ingredient gate, F5).
    pinned_dropped: list[dict[str, str]] = []
    # F12: record the excluded reason in the trace BEFORE removing exclusions from
    # the pin set — otherwise the "excluded_product" reason is unreachable.
    for pid in sorted(excluded_product_ids):
        pinned_dropped.append({"id": pid, "reason": "excluded_product"})
    query_product_ids -= excluded_product_ids  # exclusion wins over pin
    # A2: an explicit brand/category exclusion also beats a pin ("명시 배제 > 핀") —
    # recorded with a specific reason before the generic hard gates below.
    category_universe_set = set(category_universe_ids)
    pinned_survivors: list[str] = []
    for pid in sorted(query_product_ids):
        product = product_map.get(pid)
        if product is None:
            pinned_dropped.append({"id": pid, "reason": "unknown_product"})
        elif excluded_brand_ids and (
            {str(b) for b in (product.get("brand_concept_ids") or [])} & excluded_brand_ids
        ):
            pinned_dropped.append({"id": pid, "reason": "excluded_brand"})
        elif excluded_category_surfaces and _label_has_surface(
            product, excluded_category_surfaces
        ):
            pinned_dropped.append({"id": pid, "reason": "excluded_category"})
        elif excluded_category_groups and (
            classify_product_category_group(product) in excluded_category_groups
        ):
            pinned_dropped.append({"id": pid, "reason": "excluded_category_group"})
        elif pid not in category_universe_set:
            pinned_dropped.append({"id": pid, "reason": "category_mismatch"})
        elif pid in avoided_pids:
            pinned_dropped.append({"id": pid, "reason": "avoided_ingredient"})
        elif (
            raw_constraints
            and not ingredient_relaxed
            and not product_passes_constraints(product_map[pid], raw_constraints)
        ):
            pinned_dropped.append({"id": pid, "reason": "ingredient_gate"})
        else:
            pinned_survivors.append(pid)

    candidate_universe_ids, soft_relaxed = _narrow_candidate_universe(
        interp, product_map, ingredient_universe_ids,
    )
    # A1: union pin survivors so soft narrowing never drops a named product (order-
    # preserving append; pins already passed every hard gate above).
    if pinned_survivors:
        seen_universe = set(candidate_universe_ids)
        candidate_universe_ids = list(candidate_universe_ids) + [
            pid for pid in pinned_survivors if pid not in seen_universe
        ]
    # Top-level relaxed = soft-narrow relax ∨ ingredient relax (plan §B2); the
    # ingredient-specific reason rides in ``ingredient_filter`` below.
    relaxed = soft_relaxed or ingredient_relaxed

    scorer = Scorer()
    scorer.load_config()
    if materialized_weights is not None:
        scorer.load_from_dict(materialized_weights, shrinkage_k=effective_shrinkage_k)

    mode_map = {"strict": RecommendationMode.STRICT, "explore": RecommendationMode.EXPLORE, "compare": RecommendationMode.COMPARE}
    mode = mode_map.get(effective_mode, RecommendationMode.EXPLORE)

    results, candidate_count, topk_cut_pins = await _run_scored_pipeline(
        store=store,
        user_profile=user_scoped,
        product_map=product_map,
        candidate_universe_ids=candidate_universe_ids,
        mode=mode,
        scorer=scorer,
        diversity_weight=effective_diversity_weight,
        top_k=req.top_k,
        ingredient_name_labels=ingredient_name_labels or None,
        query_product_ids=set(pinned_survivors) or None,
        excluded_product_ids=excluded_product_ids or None,
        excluded_brand_ids=excluded_brand_ids or None,
        excluded_category_surfaces=excluded_category_surfaces or None,
        excluded_category_groups=excluded_category_groups or None,
    )

    # A1 pin trace. ``pinned_applied`` follows the RESULT order (F10 — the pin block
    # already leads in score order), so both the meta and the related-anchor window
    # below are pin-score-ordered. A survivor cut by the response-size (top_k) limit
    # is recorded reason="top_k" (F6); any other missing survivor (e.g. ownership
    # suppression in STRICT, or the evidence gate) is "filtered".
    result_id_set = {str(r["product_id"]) for r in results if r.get("product_id")}
    result_order = [str(r["product_id"]) for r in results if r.get("product_id")]
    survivor_set = set(pinned_survivors)
    pinned_applied = [pid for pid in result_order if pid in survivor_set]
    topk_cut_set = set(topk_cut_pins)
    for pid in topk_cut_pins:
        pinned_dropped.append({"id": pid, "reason": "top_k"})
    for pid in pinned_survivors:
        if pid not in result_id_set and pid not in topk_cut_set:
            pinned_dropped.append({"id": pid, "reason": "filtered"})

    # user_edge rewrite: candidate_generator cannot distinguish an injected query
    # concept from a genuine stored preference, so relabel the query-injected
    # concepts' paths here (post-explain, pre-serialize). Compare on the shared
    # normalized signal key, not the raw id: the explanation path id can be a
    # resolver-normalized/prefix-stripped form of the injected concept_id (goal
    # and concern axes are re-normalized in candidate_generator), so a raw-string
    # membership test would miss those and leave their user_edge unrelabeled.
    if injected_ids:
        injected_keys = {normalize_signal_id(cid) for cid in injected_ids}
        for result in results:
            for path in result["explanation_paths"]:
                if normalize_signal_id(path.get("id")) in injected_keys:
                    path["user_edge"] = _ASK_QUERY_USER_EDGE

    # Phase 8 G5 (additive): "related products" from the top results' ungated
    # similarity neighbours. Personalized surface — preserve every upstream hard
    # exclusion (plan §2.1 / codex #11): the 1차 results, the user's owned
    # products, and any avoided-ingredient carrier (checked with the same
    # field/logic candidate_generator uses, since ungated neighbours can sit
    # outside the narrowed universe where that filter never ran).
    result_pids = [str(r["product_id"]) for r in results if r.get("product_id")]
    exclude_ids = set(result_pids)
    exclude_ids |= extract_owned_product_ids(user_scoped)
    exclude_ids |= avoided_pids  # computed once above (reused from the gate)
    exclude_ids |= excluded_product_ids  # A1: a negated product is excluded from related too
    exclude_ids |= axis_excluded_pids  # A2: negated brand/category/group excluded from related too
    # Phase 6 B2: when the wanted-ingredient gate is ACTIVE (raw families, not
    # relaxed), related neighbours must pass the same matcher — a non-containing
    # product must not re-surface beneath a filtered 1차 list.
    require_ids: set[str] | None = None
    if raw_constraints and not ingredient_relaxed:
        require_ids = {
            pid
            for pid in product_map
            if product_passes_constraints(product_map[pid], raw_constraints)
        }
    # A1: anchor the related-products expansion on the pinned products FIRST (a
    # named product is the strongest relatedness seed), then the remaining results.
    pinned_applied_set = set(pinned_applied)
    anchor_order = pinned_applied + [p for p in result_pids if p not in pinned_applied_set]
    related = await _related_products(
        anchor_order[:5],
        store=store,
        exclude_ids=exclude_ids,
        anchor_names=_related_anchor_names(results),
        require_ids=require_ids,
    )

    ingredient_filter = _ingredient_filter_meta(
        [c.label for c in raw_constraints],
        ingredient_matched_count,
        ingredient_relaxed,
        _INGREDIENT_RELAX_REASON if ingredient_relaxed else None,
        ingredient_evidence_unknown,
    )

    return {
        "query": query,
        "interpretation": interp.to_dict(),
        "resolved_mode": "recommend",
        "relaxed": relaxed,
        "ingredient_filter": ingredient_filter,
        # [A3] Preferred ingredient labels ("있으면 더 좋고") — soft, surfaced as a
        # "선호 반영" note; the hard filter above only fires for required families.
        "ingredient_preferences": ingredient_preferences,
        "category_group": category_group,
        # KPI meta (parity with /api/recommend) so the frontend dashboard can show
        # real counts instead of placeholders. category_filtered_count is the tab
        # universe BEFORE query narrowing; candidate_count is what was scored.
        "category_filtered_count": len(category_universe_ids),
        "total_product_count": len(product_map),
        "candidate_count": candidate_count,
        "weights_used": scorer.weights,
        "preset_used": preset_used,
        # [F4-c''] Recommend-branch ONLY (never in the anonymous search response, so
        # the anonymous shape + result id/score identity are unchanged). Class names
        # ride in interpretation.profile_refs; the joined concepts/labels are here.
        "applied_profile_refs": applied_profile_refs,
        # [A1] Pin trace: the named products that were pinned into the results, and
        # any pin dropped by a hard filter (with reason) — the front-end renders the
        # resolved product chip from interpretation; this meta is the pin audit.
        "pinned_product_ids": pinned_applied,
        "pinned_dropped": pinned_dropped,
        # [A2] Labeled exclusion audit (brand/category/group/product) for surfacing —
        # ids live in interpretation; labels are derived here (front-end batch reads
        # this to join the exclusion chips into the existing 🚫 avoided flow).
        "excluded": _excluded_meta(products, interp),
        "results": results,
        "related_products": related,
    }


# =============================================================================
# Graph
# =============================================================================

# Phase 8 G2: cap similar-product neighbours drawn per anchor in the corpus graph
# for readability (the G3 widget keeps the full top-N; the cap is graph-only).
# similar_product_ids is score-sorted (desc) by symmetrize, so [:cap] is the top-N.
_SIMILAR_GRAPH_CAP = 3


# =============================================================================
# F5: full graph (users + products + concepts) — node-identity constants
# =============================================================================
#
# Node-identity principle (plan §F5, codex #5 — "two islands" fix): every concept
# gets ONE canonical node id, and that id is the concept IRI itself
# (``concept:Type:Value``). This is exactly the canonical join key the serving
# schema calls ``*_concept_ids`` — verified identical on both sides: a product's
# ``brand_concept_ids`` (``concept:Brand:이니스프리``) and a user's scoped
# ``PREFERS_BRAND`` id (``concept:Brand:이니스프리``) are byte-equal, so the
# product edge and the user edge land on the SAME node instead of the two
# disjoint islands the legacy per-view ``id|scope:*`` node scheme produced.
# Concept scope / edge meaning is carried on the edge, never the node.

# Concept-IRI middle segment -> graph node type (aligns with graph_view.js
# TYPE_COLORS keys). Unknown segments fall through to a lowercased segment.
_CONCEPT_TYPE_BY_SEGMENT = {
    "Brand": "brand",
    "Category": "category",
    "BEEAttr": "bee_attr",
    "Keyword": "keyword",
    "Concern": "concern",
    "Ingredient": "ingredient",
    "Goal": "goal",
    "Context": "context",
    "Tool": "tool",
    "SkinType": "skin_type",
    "SkinTone": "skin_tone",
}

# Product -> concept edges. Truth concepts + promoted top_* signals. The bee_attr/
# keyword/context/concern/tool split reuses the corpus builder's field->label
# knowledge (see _build_corpus_graph); the truth block adds the concept-IRI forms
# of brand/category/ingredient/benefit so product and user concepts unify.
_FULL_PRODUCT_TRUTH_CONCEPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("brand_concept_ids", "BRAND"),
    ("category_concept_ids", "IN_CATEGORY"),
    ("ingredient_concept_ids", "HAS_INGREDIENT"),
    ("main_benefit_concept_ids", "HAS_BENEFIT"),
)
_FULL_PRODUCT_SIGNAL_CONCEPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("top_bee_attr_ids", "HAS_ATTRIBUTE"),
    ("top_keyword_ids", "HAS_KEYWORD"),
    ("top_context_ids", "USED_IN_CONTEXT"),
    ("top_concern_pos_ids", "ADDRESSES_CONCERN"),
    ("top_tool_ids", "USED_WITH_TOOL"),
)

# The four toggleable edge families (plan §F5: `edge_types` param).
_FULL_EDGE_FAMILIES: tuple[str, ...] = (
    "product_concept",
    "user_concept",
    "owns",
    "shares_attribute",
)
_FULL_GRAPH_MAX_NODES = 2000


@app.get("/api/graphs/product/{product_id}")
async def product_graph(product_id: str, view: str = "corpus"):
    """Build hierarchical product graph.

    Query params:
        view: "corpus" (promoted serving signals only, default) | "evidence" (all per-review signals)
    """
    _check_serving_ready()
    product = await get_serving_store().get_product(product_id)
    if not product:
        raise HTTPException(404)

    # Graph nodes carry the product name WITHOUT a brand prefix (the brand is
    # its own node; ~95% of representative names already start with the brand —
    # user-facing dedupe 2026-07-21). The brand rides in node data for tooltips.
    product_label = product.get("representative_product_name") or product_id
    brand = product.get("brand_name")

    main_node: dict[str, Any] = {
        "id": product_id, "label": product_label, "type": "product", "main": True,
    }
    if brand:
        main_node["brand"] = brand
    nodes_map: dict[str, dict] = {product_id: main_node}
    edges: list[dict] = []

    # Brand node
    if brand:
        brand_id = f"brand:{brand}"
        nodes_map[brand_id] = {"id": brand_id, "label": brand, "type": "brand"}
        edges.append({"source": product_id, "target": brand_id, "label": "BRAND", "weight": 1})

    if view == "corpus":
        # CORPUS VIEW: use serving_product_profile (promoted-only signals)
        _build_corpus_graph(product, product_id, nodes_map, edges)
    else:
        # EVIDENCE VIEW: raw per-review signals live only in the demo pipeline
        # state (demo_state.product_signals); DB mode carries no in-process
        # per-review signal index, so the view is explicitly unsupported there
        # rather than silently returning an empty graph.
        if _serving_mode() == "db":
            raise HTTPException(
                400,
                "evidence view is not available in DB serving mode; use view=corpus.",
            )
        _build_evidence_graph(product_id, nodes_map, edges)

    # Deduplicate edges
    seen_edges = set()
    unique_edges = []
    for e in edges:
        key = (e["source"], e["target"], e["label"])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(e)

    return {"nodes": list(nodes_map.values()), "edges": unique_edges, "view_mode": view}


def _build_corpus_graph(profile: dict, product_id: str, nodes_map: dict, edges: list) -> None:
    """Build graph from serving product profile (promoted signals only)."""
    # BEE_ATTR nodes
    for item in profile.get("top_bee_attr_ids", []):
        if not isinstance(item, dict):
            continue
        nid = item["id"]
        label = nid.split(":")[-1] if ":" in nid else nid
        if nid not in nodes_map:
            nodes_map[nid] = {"id": nid, "label": label, "type": "bee_attr",
                              "score": item.get("score", 1)}
        edges.append({"source": product_id, "target": nid, "label": "HAS_ATTRIBUTE", "weight": item.get("score", 1)})

    # KEYWORD nodes (attached to BEE_ATTR if possible)
    for item in profile.get("top_keyword_ids", []):
        if not isinstance(item, dict):
            continue
        nid = item["id"]
        label = nid.split(":")[-1] if ":" in nid else nid
        if len(label) < 2:
            continue
        if nid not in nodes_map:
            nodes_map[nid] = {"id": nid, "label": label, "type": "keyword", "score": item.get("score", 1)}
        edges.append({"source": product_id, "target": nid, "label": "HAS_KEYWORD", "weight": item.get("score", 1)})

    # Context, Concern, Tool, etc.
    for field_key, edge_label, node_type in [
        ("top_context_ids", "USED_IN_CONTEXT", "context"),
        ("top_concern_pos_ids", "ADDRESSES_CONCERN", "concern"),
        ("top_tool_ids", "USED_WITH_TOOL", "tool"),
        ("top_coused_product_ids", "USED_WITH_PRODUCT", "coused"),
    ]:
        for item in profile.get(field_key, []):
            if not isinstance(item, dict):
                continue
            nid = item["id"]
            label = nid.split(":")[-1] if ":" in nid else nid
            if nid not in nodes_map:
                nodes_map[nid] = {"id": nid, "label": label, "type": node_type, "score": item.get("score", 1)}
            edges.append({"source": product_id, "target": nid, "label": edge_label, "weight": item.get("score", 1)})

    # Ingredient / benefit from product truth
    for ing in profile.get("ingredient_concept_ids", []):
        label = ing.split(":")[-1] if ":" in ing else ing
        if ing not in nodes_map:
            nodes_map[ing] = {"id": ing, "label": label, "type": "ingredient"}
        edges.append({"source": product_id, "target": ing, "label": "HAS_INGREDIENT", "weight": 1})

    for ben in profile.get("main_benefit_concept_ids", []):
        label = ben.split(":")[-1] if ":" in ben else ben
        if ben not in nodes_map:
            nodes_map[ben] = {"id": ben, "label": label, "type": "goal"}
        edges.append({"source": product_id, "target": ben, "label": "HAS_BENEFIT", "weight": 1})

    # Phase 8 G2: product-product similarity edges (SHARES_ATTRIBUTE). The
    # activation hook embeds each neighbour's name + shared_axes evidence on the
    # profile, so the subgraph renders "why connected" without any corpus access.
    # Undirected edge (JS drops the arrow); capped to the top-N by score for graph
    # readability. Only the anchor is expanded here, so each anchor-neighbour pair
    # is emitted once (edge dedup at the endpoint guards accidental repeats).
    for sim in (profile.get("similar_product_ids") or [])[:_SIMILAR_GRAPH_CAP]:
        if not isinstance(sim, dict):
            continue
        nb_id = sim.get("product_id")
        if not nb_id or nb_id == product_id:
            continue
        if nb_id not in nodes_map:
            nodes_map[nb_id] = {
                "id": nb_id,
                "label": sim.get("neighbor_name") or nb_id,
                "type": "product",
            }
        edges.append({
            "source": product_id,
            "target": nb_id,
            "label": "SHARES_ATTRIBUTE",
            "weight": 1,
            "score": sim.get("score"),
            "shared_axes": sim.get("shared_axes") or [],
        })


def _build_evidence_graph(product_id: str, nodes_map: dict, edges: list) -> None:
    """Build graph from raw per-review signals (all signals, not just promoted)."""
    signals = demo_state.product_signals.get(product_id, [])
    for sig in signals:
        family = sig.get("signal_family", "")
        dst_id = sig.get("dst_id", "")
        dst_label = dst_id.split(":")[-1] if ":" in dst_id else dst_id
        bee_attr_id = sig.get("bee_attr_id")

        if family == "BEE_ATTR" and dst_id:
            if dst_id not in nodes_map:
                nodes_map[dst_id] = {"id": dst_id, "label": dst_label, "type": "bee_attr",
                                     "score": sig.get("weight", 1), "polarity": sig.get("polarity")}
            edges.append({"source": product_id, "target": dst_id,
                         "label": "HAS_ATTRIBUTE", "weight": sig.get("weight", 1)})

        elif family == "BEE_KEYWORD" and dst_id:
            if len(dst_label) < 2:
                continue
            parent = bee_attr_id or product_id
            if dst_id not in nodes_map:
                nodes_map[dst_id] = {"id": dst_id, "label": dst_label, "type": "keyword",
                                     "score": sig.get("weight", 1)}
            if parent and parent not in nodes_map:
                parent_label = parent.split(":")[-1] if ":" in parent else parent
                nodes_map[parent] = {"id": parent, "label": parent_label, "type": "bee_attr", "score": 1}
                edges.append({"source": product_id, "target": parent, "label": "HAS_ATTRIBUTE", "weight": 1})
            edges.append({"source": parent, "target": dst_id,
                         "label": "HAS_KEYWORD", "weight": sig.get("weight", 1)})

        elif family == "CATALOG_VALIDATION":
            continue

        elif dst_id:
            node_type = family.lower().replace("_signal", "")
            if dst_id not in nodes_map:
                nodes_map[dst_id] = {"id": dst_id, "label": dst_label, "type": node_type, "score": 1}
            edges.append({"source": product_id, "target": dst_id, "label": family, "weight": 1})


@app.get("/api/graphs/user/{user_id}")
async def user_graph(user_id: str):
    _check_serving_ready()
    user = await get_serving_store().get_user(user_id)
    if not user:
        raise HTTPException(404)

    nodes = [{"id": user_id, "label": user_id, "type": "user", "main": True}]
    edges = []

    scoped_preferences = user.get("scoped_preference_ids") or []
    if scoped_preferences:
        for item in scoped_preferences:
            if not isinstance(item, dict):
                continue
            nid = item.get("id", "")
            if not nid:
                continue
            scope = item.get("scope_group") or "global"
            node_id = f"{nid}|scope:{scope}"
            label = nid.split(":")[-1] if ":" in nid else nid
            edge_label = item.get("edge_type", "PREFERS")
            nodes.append({
                "id": node_id,
                "label": f"{label} ({scope})",
                "type": edge_label.lower(),
                "weight": item.get("weight", 0),
                "scope_group": scope,
            })
            edges.append({
                "source": user_id,
                "target": node_id,
                "label": f"{edge_label}[{scope}]",
                "weight": item.get("weight", 0),
            })
        return {"nodes": nodes, "edges": edges}

    for field_key, edge_label, node_type in [
        ("preferred_brand_ids", "PREFERS_BRAND", "brand"),
        ("active_category_ids", "ACTIVE_IN_CATEGORY", "category"),
        ("preferred_category_ids", "PREFERS_CATEGORY", "category"),
        ("preferred_ingredient_ids", "PREFERS_INGREDIENT", "ingredient"),
        ("avoided_ingredient_ids", "AVOIDS_INGREDIENT", "avoid_ingredient"),
        ("concern_ids", "HAS_CONCERN", "concern"),
        ("goal_ids", "WANTS_GOAL", "goal"),
        ("preferred_bee_attr_ids", "PREFERS_BEE_ATTR", "bee_attr"),
        ("preferred_keyword_ids", "PREFERS_KEYWORD", "keyword"),
        ("preferred_context_ids", "PREFERS_CONTEXT", "context"),
    ]:
        for item in user.get(field_key, []):
            if isinstance(item, dict):
                nid = item.get("id", "")
                weight = item.get("weight", 0)
                nodes.append({"id": nid, "label": nid.split(":")[-1] if ":" in nid else nid, "type": node_type, "weight": weight})
                edges.append({"source": user_id, "target": nid, "label": edge_label, "weight": weight})

    return {"nodes": nodes, "edges": edges}


# =============================================================================
# F5: full graph (users + products + concepts) + focus interaction
# =============================================================================

# Legacy user preference fields (concept-bearing only) used as a fallback when a
# profile carries no `scoped_preference_ids` (synthetic fixtures). Owned/product
# fields are intentionally excluded — the OWNS family handles those.
_FULL_USER_LEGACY_CONCEPT_FIELDS: tuple[tuple[str, str], ...] = (
    ("preferred_brand_ids", "PREFERS_BRAND"),
    ("active_category_ids", "ACTIVE_IN_CATEGORY"),
    ("preferred_category_ids", "PREFERS_CATEGORY"),
    ("preferred_ingredient_ids", "PREFERS_INGREDIENT"),
    ("concern_ids", "HAS_CONCERN"),
    ("goal_ids", "WANTS_GOAL"),
    ("preferred_bee_attr_ids", "PREFERS_BEE_ATTR"),
    ("preferred_keyword_ids", "PREFERS_KEYWORD"),
    ("preferred_context_ids", "PREFERS_CONTEXT"),
)


def _canonical_concept_node(concept_id: Any) -> dict[str, str] | None:
    """Canonical graph node ({id,label,type}) for a ``concept:Type:Value`` IRI.

    The node id IS the IRI — the same canonical join key both product profiles
    (``*_concept_ids`` / ``top_*_ids[].id``) and user scoped preferences
    (``scoped_preference_ids[].id``) already carry — so both sides unify on one
    node (plan §F5 / codex #5 "two islands" fix). Type is derived from the IRI's
    middle segment; the value's last segment is the label. Returns None for empty
    input.
    """
    cid = str(concept_id or "").strip()
    if not cid:
        return None
    parts = cid.split(":")
    if cid.startswith("concept:") and len(parts) >= 3:
        node_type = _CONCEPT_TYPE_BY_SEGMENT.get(parts[1], parts[1].lower())
    else:
        node_type = "concept"
    return {"id": cid, "label": parts[-1], "type": node_type}


def _full_product_label(product: dict) -> str:
    # Brand is intentionally NOT prefixed: the full graph carries a separate
    # brand node (via ``brand_concept_ids``) and each product node ships its
    # ``brand`` on the payload for the hover tooltip, so a "{brand} {name}"
    # label duplicated the brand (many representative names already begin with
    # it, e.g. "이니스프리 그린티…").
    pid = str(product.get("product_id") or "")
    name = product.get("representative_product_name") or pid
    return name or pid


def _iter_user_concept_prefs(user: dict) -> Iterator[dict[str, Any]]:
    """Yield {id, edge_type, scope} for a user's concept preferences.

    Primary source is ``scoped_preference_ids`` (real serving profiles); only
    ``concept:`` ids are user->concept edges — ``product:`` ids (OWNS_*/
    REPURCHASES_FAMILY) are left to the OWNS family. Falls back to legacy
    preference fields only when no scoped preferences exist (mirrors user_graph).
    """
    scoped = user.get("scoped_preference_ids") or []
    if scoped:
        for item in scoped:
            if not isinstance(item, dict):
                continue
            cid = item.get("id")
            if not cid or not str(cid).startswith("concept:"):
                continue
            yield {
                "id": str(cid),
                "edge_type": item.get("edge_type") or "PREFERS",
                "scope": item.get("scope_group"),
            }
        return
    for field, edge_type in _FULL_USER_LEGACY_CONCEPT_FIELDS:
        for item in (user.get(field) or []):
            cid = item.get("id") if isinstance(item, dict) else item
            if not cid:
                continue
            yield {"id": str(cid), "edge_type": edge_type, "scope": None}


def _parse_full_edge_types(raw: str | None) -> set[str]:
    """Parse the ``edge_types`` toggle list. Empty/absent -> all four families.
    Any unknown family is a 400 (the toggle UI only ever sends known families)."""
    if raw is None or not raw.strip():
        return set(_FULL_EDGE_FAMILIES)
    requested = [t.strip().lower() for t in raw.split(",") if t.strip()]
    allowed = set(_FULL_EDGE_FAMILIES)
    unknown = [t for t in requested if t not in allowed]
    if unknown:
        raise HTTPException(
            400, f"invalid edge_types {unknown}; allowed: {sorted(allowed)}"
        )
    selected = {t for t in requested if t in allowed}
    return selected or allowed


def _build_full_graph(
    products: list[dict],
    users: list[dict],
    *,
    edge_types: set[str],
    min_strength: float,
    max_nodes: int,
) -> dict[str, Any]:
    """Build the mixed user+product+concept graph (plan §F5).

    Node identity: concepts unify on their canonical IRI (see
    ``_canonical_concept_node``); products keyed by raw pid; users by pseudonym.
    Edge families (all toggleable via ``edge_types``): product_concept (promoted
    top_* + truth concepts), user_concept (scoped/legacy prefs), owns
    (``extract_owned_product_ids`` -> catalog products only), shares_attribute
    (attached ``similar_product_ids``, unordered-pair dedup, capped per anchor).
    ``min_strength`` filters ONLY the score-bearing shares_attribute family. All
    users and products are always nodes; concepts appear only when a surviving
    edge references them, so toggling a family off drops its orphaned concepts.
    ``max_nodes`` truncation is deterministic: keep users then products, then
    concepts by degree desc (id asc tie-break); dangling edges are dropped.
    """
    nodes: dict[str, dict] = {}
    user_ids: list[str] = []
    product_ids: set[str] = set()

    for u in users:
        uid = u.get("user_id")
        if not uid:
            continue
        # Privacy (plan §F5): pseudonymous id ONLY — no profile fields ever land
        # on a user node payload.
        nodes[str(uid)] = {"id": str(uid), "label": str(uid), "type": "user"}
        user_ids.append(str(uid))

    for p in products:
        pid = p.get("product_id")
        if not pid:
            continue
        product_ids.add(str(pid))
        node = {"id": str(pid), "label": _full_product_label(p), "type": "product"}
        # Brand rides along (not in the label) so the node hover tooltip can show
        # "브랜드 … · id …"; omitted when absent so the payload stays minimal.
        brand = p.get("brand_name")
        if brand:
            node["brand"] = brand
        nodes[str(pid)] = node

    edges: list[dict] = []

    def _ensure_concept(concept_id: Any) -> str | None:
        node = _canonical_concept_node(concept_id)
        if node is None:
            return None
        nid = node["id"]
        nodes.setdefault(nid, node)
        return nid

    # --- product -> concept -------------------------------------------------
    if "product_concept" in edge_types:
        pc_seen: set[tuple[str, str, str]] = set()
        for p in products:
            pid = p.get("product_id")
            if not pid:
                continue
            pid = str(pid)
            for field, label in _FULL_PRODUCT_TRUTH_CONCEPT_FIELDS:
                for raw in (p.get(field) or []):
                    cid = raw.get("id") if isinstance(raw, dict) else raw
                    nid = _ensure_concept(cid) if cid else None
                    if not nid or (pid, nid, label) in pc_seen:
                        continue
                    pc_seen.add((pid, nid, label))
                    edges.append({"source": pid, "target": nid, "label": label,
                                  "family": "product_concept"})
            for field, label in _FULL_PRODUCT_SIGNAL_CONCEPT_FIELDS:
                for item in (p.get(field) or []):
                    if not isinstance(item, dict):
                        continue
                    nid = _ensure_concept(item.get("id")) if item.get("id") else None
                    if not nid or (pid, nid, label) in pc_seen:
                        continue
                    pc_seen.add((pid, nid, label))
                    edges.append({"source": pid, "target": nid, "label": label,
                                  "family": "product_concept"})

    # --- user -> concept ----------------------------------------------------
    if "user_concept" in edge_types:
        uc_seen: set[tuple[str, str, str, str]] = set()
        for u in users:
            uid = u.get("user_id")
            if not uid:
                continue
            uid = str(uid)
            for pref in _iter_user_concept_prefs(u):
                nid = _ensure_concept(pref["id"])
                if not nid:
                    continue
                edge_type = str(pref["edge_type"])
                scope = pref["scope"]
                key = (uid, nid, edge_type, str(scope or ""))
                if key in uc_seen:
                    continue
                uc_seen.add(key)
                edge: dict[str, Any] = {"source": uid, "target": nid, "label": edge_type,
                                        "family": "user_concept"}
                if scope:
                    edge["scope"] = str(scope)
                edges.append(edge)

    # --- user -> product (owned) -------------------------------------------
    if "owns" in edge_types:
        for u in users:
            uid = u.get("user_id")
            if not uid:
                continue
            uid = str(uid)
            for opid in sorted(extract_owned_product_ids(u)):
                if opid in product_ids:
                    edges.append({"source": uid, "target": opid, "label": "OWNS",
                                  "family": "owns"})

    # --- product <-> product (SHARES_ATTRIBUTE) -----------------------------
    # Unordered-pair dedup (keep max score); min_strength applies ONLY here
    # (the sole score-bearing family — plan §F5 / codex #7).
    if "shares_attribute" in edge_types:
        pair_score: dict[tuple[str, str], float] = {}
        for p in products:
            pid = p.get("product_id")
            if not pid:
                continue
            pid = str(pid)
            for sim in (p.get("similar_product_ids") or [])[:_SIMILAR_GRAPH_CAP]:
                if not isinstance(sim, dict):
                    continue
                nb = sim.get("product_id")
                nb = str(nb) if nb else ""
                if not nb or nb == pid or nb not in product_ids:
                    continue
                try:
                    score = float(sim.get("score") or 0.0)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(score) or score < min_strength:
                    continue
                pair_key = (pid, nb) if pid < nb else (nb, pid)
                if pair_key not in pair_score or score > pair_score[pair_key]:
                    pair_score[pair_key] = score
        for (a, b), score in pair_score.items():
            edges.append({"source": a, "target": b, "label": "SHARES_ATTRIBUTE",
                          "family": "shares_attribute", "score": round(score, 4)})

    total_nodes = len(nodes)
    total_edges = len(edges)
    truncated = total_nodes > max_nodes

    if truncated:
        degree: dict[str, int] = {}
        for e in edges:
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1
        concepts_by_degree = sorted(
            (nid for nid, n in nodes.items() if n["type"] not in ("user", "product")),
            key=lambda nid: (-degree.get(nid, 0), nid),
        )
        ordered = sorted(user_ids) + sorted(product_ids) + concepts_by_degree
        survivors = set(ordered[:max_nodes])
        nodes = {nid: n for nid, n in nodes.items() if nid in survivors}
        edges = [e for e in edges if e["source"] in survivors and e["target"] in survivors]

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "meta": {
            "truncated": truncated,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "shown_nodes": len(nodes),
            "shown_edges": len(edges),
        },
    }


@app.get("/api/graphs/full")
async def full_graph(
    edge_types: str | None = None,
    min_strength: float = 0.0,
    max_nodes: int = _FULL_GRAPH_MAX_NODES,
):
    """Mixed user+product+concept graph with deterministic guards (plan §F5).

    Query params:
        edge_types: comma list of {product_concept,user_concept,owns,shares_attribute};
                    absent/empty -> all four.
        min_strength: minimum score for shares_attribute edges (other families ignore it).
        max_nodes: deterministic node cap (default 2000; users+products kept first,
                   then concepts by degree). Response `meta` carries truncation state.
    """
    _check_serving_ready()
    if max_nodes < 1:
        raise HTTPException(400, "max_nodes must be >= 1")
    families = _parse_full_edge_types(edge_types)
    store = get_serving_store()
    products = await store.get_products()
    users = await store.get_users()
    return _build_full_graph(
        products,
        users,
        edge_types=families,
        min_strength=min_strength,
        max_nodes=max_nodes,
    )


# =============================================================================
# Quarantine
# =============================================================================

@app.get("/api/quarantine/summary")
async def quarantine_summary():
    _check_loaded()
    return {"by_table": demo_state.quarantine_stats, "total": sum(demo_state.quarantine_stats.values())}


# P4-1 (Wave 3.1): `table` query param whitelist mirrors the names emitted by
# `src/qa/quarantine_handler.py`. Unknown values are rejected at the boundary.
_ALLOWED_QUARANTINE_TABLES = frozenset({
    "quarantine_product_match",
    "quarantine_placeholder",
    "quarantine_unknown_keyword",
    "quarantine_projection_miss",
    "quarantine_untyped_entity",
})


@app.get("/api/quarantine/entries")
async def quarantine_entries(table: str = "", page: int = 1, size: int = 20):
    _check_loaded()
    if table and table not in _ALLOWED_QUARANTINE_TABLES:
        raise HTTPException(
            400,
            f"Invalid table '{table}'. Allowed: {sorted(_ALLOWED_QUARANTINE_TABLES)}.",
        )
    if page < 1:
        raise HTTPException(400, "page must be >= 1")
    if size < 1 or size > 200:
        raise HTTPException(400, "size must be in [1, 200]")
    items = demo_state.quarantine_entries
    if table:
        items = [e for e in items if e.get("table") == table]
    total = len(items)
    start = (page - 1) * size
    return {"items": items[start:start + size], "total": total, "page": page}


# =============================================================================
# Helpers
# =============================================================================

def _check_loaded():
    if not demo_state.loaded:
        raise HTTPException(400, "데이터가 로드되지 않았습니다. POST /api/pipeline/run을 먼저 실행하세요.")


def _check_serving_ready():
    """Readiness guard for serving endpoints (products/users/recommend/graphs).

    Mode-aware: demo mode requires a pipeline run (``demo_state.loaded``); DB
    mode has no per-request load step (the store loads lazily and refreshes on
    a timer), so readiness is implicit once the app has started.
    """
    if _serving_mode() == "db":
        return
    _check_loaded()


def _sorted_counts(counts: dict, limit: int = 50) -> list[dict]:
    return [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])[:limit]]


def _positive_number(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def _source_trust(product: dict) -> dict:
    return {
        "review_count_6m": product.get("source_review_count_6m"),
        "avg_rating_6m": product.get("source_avg_rating_6m"),
        "review_count_all": product.get("source_review_count_all"),
        "avg_rating_all": product.get("source_avg_rating_all"),
    }


def _review_summary(r: dict) -> dict:
    if isinstance(r, dict):
        return {
            "review_id": r.get("review_id", ""),
            "match_status": r.get("match_status", ""),
            "matched_product_id": r.get("matched_product_id"),
            "entity_count": r.get("entity_count", 0),
            "fact_count": r.get("fact_count", 0),
            "signal_count": r.get("signal_count", 0),
            "quarantine_count": r.get("quarantine_count", 0),
        }
    return {}


def _review_detail(r) -> dict:
    return r if isinstance(r, dict) else {}
