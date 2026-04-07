"""
FastAPI server for GraphRapping demo UI.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.web.state import demo_state, load_demo_data
from src.rec.candidate_generator import generate_candidates, generate_candidates_prefiltered
from src.rec.scorer import Scorer
from src.rec.reranker import rerank
from src.rec.explainer import explain
from src.rec.hook_generator import generate_hooks
from src.rec.next_question import generate_next_question
from src.common.enums import RecommendationMode

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
_DEFAULT_REVIEW_PATH = "/Users/amore/Jupyter_workplace/Relation/source_data/hab_rel_sample_ko_withPRD_listkeyword.json"


class PipelineRunRequest(BaseModel):
    review_json_path: str = _DEFAULT_REVIEW_PATH
    max_reviews: int = 5000
    source: str = "demo"
    review_format: str = "relation"


@app.post("/api/pipeline/run")
async def pipeline_run(req: PipelineRunRequest):
    import json as _json
    import random as _random

    # --- 1. Load products from mock catalog ---
    mock_products = _json.loads((_MOCKDATA_DIR / "product_catalog_es.json").read_text(encoding="utf-8"))

    # --- 2. Load users from mock profiles ---
    mock_users = _json.loads((_MOCKDATA_DIR / "user_profiles_normalized.json").read_text(encoding="utf-8"))

    # --- 3. Prepare product assignment pool (active products only) ---
    active_products = [p for p in mock_products if p.get("SALE_STATUS") == "판매중"]
    product_pairs = [
        (p["prd_nm"], p["BRAND_NAME"]) for p in active_products
    ]

    # --- 4. Remap external review data to mock product IDs ---
    review_path = Path(req.review_json_path)
    remapped_path = _PROJECT_ROOT / "mockdata" / "_remapped_reviews.json"

    if review_path.exists():
        raw_data = _json.loads(review_path.read_text(encoding="utf-8"))
        _random.seed(42)  # deterministic
        for record in raw_data:
            prd_nm, brnd_nm = _random.choice(product_pairs)
            record["prod_nm"] = prd_nm
            record["brnd_nm"] = brnd_nm

        # --- 5. Append mock 15 reviews (cross-referenced) ---
        mock_review_path = _MOCKDATA_DIR / "review_triples_raw.json"
        if mock_review_path.exists():
            mock_reviews = _json.loads(mock_review_path.read_text(encoding="utf-8"))
            raw_data.extend(mock_reviews)

        remapped_path.write_text(_json.dumps(raw_data, ensure_ascii=False), encoding="utf-8")
    else:
        # Fallback: use mock reviews only
        remapped_path = _MOCKDATA_DIR / "review_triples_raw.json"

    load_demo_data(
        review_json_path=str(remapped_path),
        product_es_records=mock_products,
        user_profiles=mock_users,
        max_reviews=req.max_reviews,
        source=req.source,
        review_format=req.review_format,
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
    return {
        "reviews_processed": demo_state.review_count,
        "total_signals": demo_state.batch_result.get("total_signals", 0),
        "total_quarantined": sum(demo_state.quarantine_stats.values()),
        "serving_products": len(demo_state.serving_products),
        "serving_users": len(demo_state.serving_users),
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
    return {"serving_profile": product, "master": master, "concept_links": links}


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

class RecommendRequest(BaseModel):
    user_id: str
    mode: str = "explore"
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
    candidates = generate_candidates_prefiltered(
        user_profile=user,
        prefiltered_product_ids=list(product_map.keys()),
        product_profiles_by_id=product_map,
        mode=mode,
        max_candidates=50,
    )

    scorer = Scorer()
    if req.weights:
        scorer.load_from_dict(req.weights, shrinkage_k=req.shrinkage_k)
    else:
        scorer.load_config()
    weights = scorer._weights

    scored = []
    for c in candidates:
        p = product_map.get(c.product_id)
        if p:
            s = scorer.score(user, p, c.overlap_concepts)
            scored.append((c, s))

    scored.sort(key=lambda x: x[1].final_score, reverse=True)

    reranked = rerank([s for _, s in scored], product_profiles=product_map,
                      diversity_weight=req.diversity_weight, top_k=req.top_k)

    results = []
    for r in reranked:
        c = next((c for c, s in scored if s.product_id == r.product_id), None)
        s = next((s for _, s in scored if s.product_id == r.product_id), None)
        if c and s:
            exp = explain(s, c.overlap_concepts, top_n=5)
            hooks = generate_hooks(exp)
            results.append({
                "rank": r.final_rank + 1,
                "product_id": r.product_id,
                "product": product_map.get(r.product_id, {}),
                "overlap_concepts": c.overlap_concepts,
                "raw_score": s.raw_score,
                "shrinked_score": s.shrinked_score,
                "final_score": r.final_score,
                "diversity_bonus": r.diversity_bonus,
                "support_count": s.support_count,
                "feature_contributions": s.feature_contributions,
                "explanation": exp.summary_ko,
                "explanation_paths": [{"type": p.concept_type, "id": p.concept_id,
                                       "user_edge": p.user_edge, "product_edge": p.product_edge,
                                       "contribution": p.contribution} for p in exp.paths],
                "hooks": {"discovery": hooks.discovery, "consideration": hooks.consideration, "conversion": hooks.conversion},
            })

    nq = generate_next_question(user)
    return {
        "user_id": req.user_id,
        "mode": req.mode,
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
    """Build hierarchical product graph: Product → BEE_ATTR → KEYWORD (from signals).

    Query params:
        view: "corpus" (promoted signals only, default) | "evidence" (all signals)
    """
    _check_loaded()
    product = next((p for p in demo_state.serving_products if p["product_id"] == product_id), None)
    if not product:
        raise HTTPException(404)

    # Product center node (Product only, Brand is separate)
    nodes_map: dict[str, dict] = {
        product_id: {"id": product_id, "label": product_id, "type": "product", "main": True}
    }
    edges: list[dict] = []

    # Brand as separate node connected to Product
    brand = product.get("brand_name")
    if brand:
        brand_id = f"brand:{brand}"
        nodes_map[brand_id] = {"id": brand_id, "label": brand, "type": "brand"}
        edges.append({"source": product_id, "target": brand_id, "label": "BRAND", "weight": 1})

    # Build from per-product signals (preserves BEE_ATTR → KEYWORD hierarchy)
    signals = demo_state.product_signals.get(product_id, [])

    for sig in signals:
        family = sig.get("signal_family", "")
        dst_id = sig.get("dst_id", "")
        dst_label = dst_id.split(":")[-1] if ":" in dst_id else dst_id
        bee_attr_id = sig.get("bee_attr_id")
        keyword_id = sig.get("keyword_id")

        if family == "BEE_ATTR" and dst_id:
            # Product → BEE_ATTR
            if dst_id not in nodes_map:
                nodes_map[dst_id] = {"id": dst_id, "label": dst_label, "type": "bee_attr",
                                     "score": sig.get("weight", 1), "polarity": sig.get("polarity")}
            edges.append({"source": product_id, "target": dst_id,
                         "label": "HAS_ATTRIBUTE", "weight": sig.get("weight", 1)})

        elif family == "BEE_KEYWORD" and dst_id:
            # BEE_ATTR → KEYWORD (hierarchical!)
            parent = bee_attr_id or product_id
            if dst_id not in nodes_map:
                nodes_map[dst_id] = {"id": dst_id, "label": dst_label, "type": "keyword",
                                     "score": sig.get("weight", 1)}
            # Ensure parent BEE_ATTR node exists
            if parent and parent not in nodes_map:
                parent_label = parent.split(":")[-1] if ":" in parent else parent
                nodes_map[parent] = {"id": parent, "label": parent_label, "type": "bee_attr", "score": 1}
                edges.append({"source": product_id, "target": parent,
                             "label": "HAS_ATTRIBUTE", "weight": 1})
            edges.append({"source": parent, "target": dst_id,
                         "label": "HAS_KEYWORD", "weight": sig.get("weight", 1)})

        elif family == "CONTEXT" and dst_id:
            if dst_id not in nodes_map:
                nodes_map[dst_id] = {"id": dst_id, "label": dst_label, "type": "context", "score": 1}
            edges.append({"source": product_id, "target": dst_id, "label": "USED_IN_CONTEXT", "weight": 1})

        elif family == "CATALOG_VALIDATION":
            continue  # skip catalog validation from graph

        elif dst_id:
            # Other signals → Product direct
            node_type = family.lower().replace("_signal", "")
            if dst_id not in nodes_map:
                nodes_map[dst_id] = {"id": dst_id, "label": dst_label, "type": node_type, "score": 1}
            edges.append({"source": product_id, "target": dst_id, "label": family, "weight": 1})

    # Deduplicate edges
    seen_edges = set()
    unique_edges = []
    for e in edges:
        key = (e["source"], e["target"], e["label"])
        if key not in seen_edges:
            seen_edges.add(key)
            unique_edges.append(e)

    return {"nodes": list(nodes_map.values()), "edges": unique_edges}


@app.get("/api/graphs/user/{user_id}")
async def user_graph(user_id: str):
    _check_loaded()
    user = next((u for u in demo_state.serving_users if u["user_id"] == user_id), None)
    if not user:
        raise HTTPException(404)

    nodes = [{"id": user_id, "label": user_id, "type": "user", "main": True}]
    edges = []

    for field_key, edge_label, node_type in [
        ("preferred_brand_ids", "PREFERS_BRAND", "brand"),
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


@app.get("/api/quarantine/entries")
async def quarantine_entries(table: str = "", page: int = 1, size: int = 20):
    _check_loaded()
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
