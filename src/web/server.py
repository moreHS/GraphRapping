"""
FastAPI server for GraphRapping demo UI.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.web.state import demo_state, load_demo_data
from src.rec.candidate_generator import generate_candidates_prefiltered
from src.rec.scorer import Scorer
from src.rec.reranker import rerank
from src.rec.explainer import explain
from src.rec.hook_generator import generate_hooks
from src.rec.next_question import generate_next_question
from src.rec.category_groups import (
    RECOMMEND_CATEGORY_DEFS,
    RECOMMEND_CATEGORY_LABELS,
    classify_product_category_group,
    recommend_category_counts,
)
from src.common.enums import RecommendationMode
from src.web.review_summary_sidecar import fetch_sidecar_summaries

app = FastAPI(title="GraphRapping Demo", version="1.0")

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
    _check_loaded()
    source_stats_positive = sum(
        1 for p in demo_state.serving_products
        if _positive_number(p.get("source_review_count_6m"))
    )
    source_rating_present = sum(
        1 for p in demo_state.serving_products
        if p.get("source_avg_rating_6m") is not None
    )
    return {
        "reviews_processed": demo_state.review_count,
        "total_signals": demo_state.batch_result.get("total_signals", 0),
        "total_quarantined": sum(demo_state.quarantine_stats.values()),
        "serving_products": len(demo_state.serving_products),
        "serving_users": len(demo_state.serving_users),
        "source_review_stats_products": source_stats_positive,
        "source_avg_rating_products": source_rating_present,
        "loaded": demo_state.loaded,
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
    _check_loaded()
    return {"items": demo_state.serving_products, "total": len(demo_state.serving_products)}


@app.get("/api/products/{product_id}")
async def get_product(product_id: str):
    _check_loaded()
    product = next((p for p in demo_state.serving_products if p["product_id"] == product_id), None)
    if not product:
        raise HTTPException(404, "Product not found")
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
    _check_loaded()
    return {"items": demo_state.serving_users, "total": len(demo_state.serving_users)}


@app.get("/api/users/{user_id}")
async def get_user(user_id: str):
    _check_loaded()
    user = next((u for u in demo_state.serving_users if u["user_id"] == user_id), None)
    if not user:
        raise HTTPException(404, "User not found")
    return {"serving_profile": user}


# =============================================================================
# Recommendation
# =============================================================================

@app.get("/api/recommend/categories")
async def recommend_categories():
    _check_loaded()
    counts = recommend_category_counts(demo_state.serving_products)
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


@app.post("/api/recommend")
async def recommend(req: RecommendRequest):
    _check_loaded()
    user = next((u for u in demo_state.serving_users if u["user_id"] == req.user_id), None)
    if not user:
        raise HTTPException(404, "User not found")

    mode_map = {"strict": RecommendationMode.STRICT, "explore": RecommendationMode.EXPLORE, "compare": RecommendationMode.COMPARE}
    mode = mode_map.get(req.mode, RecommendationMode.EXPLORE)

    # SQL-first: use prefiltered path as default (avoids Python full-scan of all products)
    product_map = {p["product_id"]: p for p in demo_state.serving_products}
    requested_category_group = req.category_group if req.category_group in RECOMMEND_CATEGORY_LABELS else "all"
    if requested_category_group == "all":
        prefiltered_product_ids = list(product_map.keys())
    else:
        prefiltered_product_ids = [
            pid
            for pid, product in product_map.items()
            if classify_product_category_group(product) == requested_category_group
        ]
    candidates = generate_candidates_prefiltered(
        user_profile=user,
        prefiltered_product_ids=prefiltered_product_ids,
        product_profiles_by_id=product_map,
        mode=mode,
        max_candidates=50,
    )

    scorer = Scorer()
    if req.weights:
        scorer.load_from_dict(req.weights, shrinkage_k=req.shrinkage_k)
    else:
        scorer.load_config()
    weights = scorer.weights

    scored = []
    for c in candidates:
        p = product_map.get(c.product_id)
        if p:
            s = scorer.score(user, p, c.overlap_concepts)
            scored.append((c, s))

    scored.sort(key=lambda x: x[1].final_score, reverse=True)

    reranked = rerank([s for _, s in scored], product_profiles=product_map,
                      diversity_weight=req.diversity_weight, top_k=req.top_k)
    summary_by_product = await fetch_sidecar_summaries([r.product_id for r in reranked])

    results = []
    for r in reranked:
        candidate = next((candidate for candidate, scored_product in scored if scored_product.product_id == r.product_id), None)
        scored_product = next((scored_product for _, scored_product in scored if scored_product.product_id == r.product_id), None)
        if candidate is not None and scored_product is not None:
            product_profile = product_map.get(r.product_id, {})
            exp = explain(scored_product, candidate.overlap_concepts, top_n=5)
            hooks = generate_hooks(exp, product_profile=product_profile)
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
                                       "contribution": p.contribution} for p in exp.paths],
                "hooks": {"discovery": hooks.discovery, "consideration": hooks.consideration, "conversion": hooks.conversion},
            })

    # P4-3 (Wave 3.3): pass scored products so axis selection can use score
    # histograms instead of falling back to data-absence ordering only.
    nq = generate_next_question(user, scored_products=results)
    return {
        "user_id": req.user_id,
        "mode": req.mode,
        "category_group": requested_category_group,
        "category_label": RECOMMEND_CATEGORY_LABELS[requested_category_group],
        "category_filtered_count": len(prefiltered_product_ids),
        "total_product_count": len(product_map),
        "candidate_count": len(candidates),
        "results": results,
        "next_question": {"question": nq.question_ko, "axis": nq.uncertainty_axis, "options": nq.options} if nq else None,
        "weights_used": weights,
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
    _check_loaded()
    product = next((p for p in demo_state.serving_products if p["product_id"] == product_id), None)
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
        # EVIDENCE VIEW: use raw per-review signals (all, including non-promoted)
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
    _check_loaded()
    user = next((u for u in demo_state.serving_users if u["user_id"] == user_id), None)
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


def _sorted_counts(counts: dict, limit: int = 50) -> list[dict]:
    return [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])[:limit]]


def _positive_number(value: object) -> bool:
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
