"""
FastAPI server for GraphRapping demo UI.
"""

from __future__ import annotations

import asyncio
import copy
import functools
import os
from collections.abc import AsyncIterator
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
from src.rec.candidate_generator import generate_candidates_prefiltered
from src.rec.scorer import Scorer
from src.rec.reranker import rerank
from src.rec.explainer import explain, ExplanationService
from src.rec.search import search_products
# `_product_overlap` is imported read-only: it is the exact predicate search uses
# to decide "does this product carry these concepts", which /api/ask reuses to
# narrow the recommend candidate universe to query-relevant products (no logic is
# reimplemented, and search.py itself is unmodified beyond search_products' sig).
from src.rec.search import MatchedConcept, _product_overlap
from src.rec.semantic_compatibility import normalize_signal_id
from src.rec.query_understanding import understand_query, QueryInterpretation
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
from src.web.review_summary_sidecar import fetch_sidecar_summaries


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
    return FileResponse(str(STATIC_DIR / "index.html"))


# =============================================================================
# Pipeline
# =============================================================================

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_MOCKDATA_DIR = _PROJECT_ROOT / "mockdata"

# P1-2 (audit fix): demo review path is resolved via environment variable
# (GRAPHRAPPING_DEMO_REVIEW_PATH) with mockdata as the default. Previously this
# was a hardcoded absolute path tied to a single developer's machine.
_DEFAULT_FIXTURE = os.environ.get("GRAPHRAPPING_DEMO_FIXTURE", "wide")
_DEFAULT_REVIEW_PATH = os.environ.get("GRAPHRAPPING_DEMO_REVIEW_PATH")
_DEFAULT_SOURCE_REVIEW_STATS_PATH = (
    _PROJECT_ROOT / "data/source_snapshots/product_review_stats_snowflake_latest.json"
)


class PipelineRunRequest(BaseModel):
    fixture: str = _DEFAULT_FIXTURE
    review_json_path: str | None = _DEFAULT_REVIEW_PATH
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
    product_path = _resolve_existing_json_path(
        req.product_json_path,
        fixture_dir / "product_catalog_es.json",
        "product_json_path",
    )
    mock_products = _json.loads(product_path.read_text(encoding="utf-8"))

    # --- 2. Load users from selected fixture profiles ---
    user_path = _resolve_existing_json_path(
        req.user_json_path,
        fixture_dir / "user_profiles_normalized.json",
        "user_json_path",
    )
    mock_users = _json.loads(user_path.read_text(encoding="utf-8"))

    # --- 3. Prepare review path ---
    selected_review_path = _resolve_existing_json_path(
        req.review_json_path,
        fixture_dir / "review_triples_raw.json",
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
) -> tuple[list[dict[str, Any]], int]:
    """Shared recommend pipeline: prefilter -> candidates -> score -> rerank ->
    explain -> per-path snippets -> result dicts.

    Extracted verbatim from the /api/recommend handler so /api/ask's query-scoped
    recommend mode reuses the identical candidate/score/explain/snippet path. The
    only differences live in the caller: which user profile is scored, which
    candidate universe is scored, and any post-hoc explanation relabeling. Returns
    the result dicts and the raw candidate count (for the response candidate_count).
    """
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

    candidates = generate_candidates_prefiltered(
        user_profile=user_profile,
        prefiltered_product_ids=prefiltered_product_ids,
        product_profiles_by_id=product_map,
        mode=mode,
        max_candidates=50,
    )

    scored = []
    for c in candidates:
        p = product_map.get(c.product_id)
        if p:
            s = scorer.score(user_profile, p, c.overlap_concepts, mode=mode)
            scored.append((c, s))

    scored.sort(key=lambda x: x[1].final_score, reverse=True)

    reranked = rerank([s for _, s in scored], product_profiles=product_map,
                      diversity_weight=diversity_weight, top_k=top_k, mode=mode)
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
            "explanation": exp.summary_ko,
            "explanation_paths": [{"type": p.concept_type, "id": p.concept_id,
                                   "user_edge": p.user_edge, "product_edge": p.product_edge,
                                   "contribution": p.contribution,
                                   "snippets": snippets_by_path_idx.get(idx, [])}
                                  for idx, p in enumerate(exp.paths)],
            "hooks": {"discovery": hooks.discovery, "consideration": hooks.consideration, "conversion": hooks.conversion},
        })

    return results, len(candidates)


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

    results, candidate_count = await _run_scored_pipeline(
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
    _check_serving_ready()
    products = await get_serving_store().get_products()
    outcome = search_products(query, products, max_results=_clamp_search_top_k(top_k))
    payload = outcome.to_dict()
    payload["message"] = None if outcome.resolved else _SEARCH_NO_CONCEPT_MESSAGE
    return payload


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
    request hint; otherwise "all"."""
    for concept in interp.resolved_concepts:
        if concept.concept_type == "category" and concept.concept_id in _GROUP_CATEGORY_CONCEPT_IDS:
            return concept.concept_id[len("concept:Category:"):]
    if requested and requested in RECOMMEND_CATEGORY_LABELS:
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


def _narrow_candidate_universe(
    interp: QueryInterpretation,
    product_map: dict[str, dict[str, Any]],
    category_universe_ids: list[str],
) -> tuple[list[str], bool]:
    """Narrow the category universe to products carrying a NON-category query
    concept (the axes actually injected as preferences). Empty intersection →
    return the full category universe with ``relaxed=True`` (recall protection,
    plan decision 2). No such concept → no narrowing, ``relaxed=False``."""
    narrowing: list[MatchedConcept] = [
        c for c in interp.resolved_concepts if c.concept_type != "category"
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
    # It is synchronous (blocking httpx on the active-provider path), so run it in
    # the default executor to avoid stalling the event loop; the off/fallback path
    # is cheap but harmless to offload.
    loop = asyncio.get_running_loop()
    interp = await loop.run_in_executor(
        None, functools.partial(understand_query, query, products)
    )
    category_group = _ask_category_group(interp, req.category_group)

    # --- (a) Anonymous search mode ---
    if not req.user_id:
        outcome = search_products(
            query,
            products,
            max_results=_clamp_search_top_k(req.top_k),
            avoided_ingredient_concept_ids=interp.avoided_ingredient_concept_ids,
        )
        return {
            "query": query,
            "interpretation": interp.to_dict(),
            "resolved_mode": "search",
            "relaxed": False,
            "category_group": category_group,
            "preset_used": None,
            "results": outcome.to_dict()["results"],
        }

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

    product_map = {p["product_id"]: p for p in products}
    category_universe_ids = _category_universe_ids(product_map, category_group)
    candidate_universe_ids, relaxed = _narrow_candidate_universe(
        interp, product_map, category_universe_ids,
    )

    scorer = Scorer()
    scorer.load_config()
    if materialized_weights is not None:
        scorer.load_from_dict(materialized_weights, shrinkage_k=effective_shrinkage_k)

    mode_map = {"strict": RecommendationMode.STRICT, "explore": RecommendationMode.EXPLORE, "compare": RecommendationMode.COMPARE}
    mode = mode_map.get(effective_mode, RecommendationMode.EXPLORE)

    results, candidate_count = await _run_scored_pipeline(
        store=store,
        user_profile=user_scoped,
        product_map=product_map,
        candidate_universe_ids=candidate_universe_ids,
        mode=mode,
        scorer=scorer,
        diversity_weight=effective_diversity_weight,
        top_k=req.top_k,
    )

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

    return {
        "query": query,
        "interpretation": interp.to_dict(),
        "resolved_mode": "recommend",
        "relaxed": relaxed,
        "category_group": category_group,
        # KPI meta (parity with /api/recommend) so the frontend dashboard can show
        # real counts instead of placeholders. category_filtered_count is the tab
        # universe BEFORE query narrowing; candidate_count is what was scored.
        "category_filtered_count": len(category_universe_ids),
        "total_product_count": len(product_map),
        "candidate_count": candidate_count,
        "weights_used": scorer.weights,
        "preset_used": preset_used,
        "results": results,
    }


# =============================================================================
# Graph
# =============================================================================

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

    product_label = product.get("representative_product_name") or product_id
    brand = product.get("brand_name")
    if brand and product_label != product_id:
        product_label = f"{brand} {product_label}"

    nodes_map: dict[str, dict] = {
        product_id: {"id": product_id, "label": product_label, "type": "product", "main": True}
    }
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
