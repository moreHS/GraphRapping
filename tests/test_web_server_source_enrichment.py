from __future__ import annotations

import pytest

from src.web import server
from src.web.state import DemoState


def _source_rich_product(
    product_id: str = "P1",
    *,
    category_id: str = "cat_cushion",
    category_name: str = "쿠션",
    representative_product_name: str = "블랙 쿠션",
    keyword_id: str = "kw_thin_spread",
) -> dict:
    return {
        "product_id": product_id,
        "brand_name": "헤라",
        "representative_product_name": representative_product_name,
        "brand_id": "brand_hera",
        "brand_concept_ids": ["brand_hera"],
        "category_id": category_id,
        "category_name": category_name,
        "category_concept_ids": [category_id],
        "ingredient_concept_ids": [],
        "main_benefit_concept_ids": [],
        "top_keyword_ids": [{"id": keyword_id, "score": 0.9, "review_cnt": 40}],
        "top_bee_attr_ids": [],
        "top_context_ids": [],
        "top_concern_pos_ids": [],
        "top_concern_neg_ids": [],
        "top_tool_ids": [],
        "top_coused_product_ids": [],
        "top_comparison_product_ids": [],
        "review_count_all": 120,
        "source_review_count_6m": 1200,
        "source_avg_rating_6m": 4.8,
        "source_review_count_all": 4900,
        "source_avg_rating_all": 4.85,
    }


def _user() -> dict:
    return {
        "user_id": "U1",
        "preferred_brand_ids": [{"id": "brand_hera", "weight": 1.0}],
        "preferred_category_ids": [{"id": "cat_cushion", "weight": 1.0}],
        "preferred_keyword_ids": [{"id": "kw_thin_spread", "weight": 1.0}],
    }


def _loaded_state() -> DemoState:
    state = DemoState(loaded=True)
    state.serving_products = [_source_rich_product()]
    state.serving_users = [_user()]
    state.product_masters = {"P1": {"product_id": "P1", "product_name": "블랙 쿠션"}}
    state.concept_links = {"product:P1": [{"src_id": "product:P1", "dst_id": "brand:brand_hera"}]}
    state.batch_result = {"total_signals": 1}
    return state


@pytest.mark.asyncio
async def test_get_product_includes_review_summary_sidecar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "demo_state", _loaded_state())

    async def fake_fetch(product_ids: list[str]) -> dict:
        assert product_ids == ["P1"]
        return {"P1": {"match_status": "exact_category", "short_summary": "요약"}}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", fake_fetch)

    payload = await server.get_product("P1")

    assert payload["serving_profile"]["source_review_count_6m"] == 1200
    assert payload["review_summary"] == {"match_status": "exact_category", "short_summary": "요약"}


@pytest.mark.asyncio
async def test_recommend_response_exposes_source_trust_and_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "demo_state", _loaded_state())

    async def fake_fetch(product_ids: list[str]) -> dict:
        assert product_ids == ["P1"]
        return {"P1": {"match_status": "exact_category", "short_summary": "요약"}}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", fake_fetch)

    payload = await server.recommend(server.RecommendRequest(user_id="U1", top_k=1))

    result = payload["results"][0]
    assert result["product_id"] == "P1"
    assert result["source_trust"]["review_count_6m"] == 1200
    assert result["source_trust"]["avg_rating_6m"] == 4.8
    assert result["eligibility"]["eligible"] is True
    assert "PRODUCT_MASTER_TRUTH" in result["eligibility"]["evidence_families"]
    assert "REVIEW_GRAPH_RELATION" in result["eligibility"]["evidence_families"]
    assert result["score_layers"]["master_truth_score"] > 0
    assert result["score_layers"]["review_graph_score"] > 0
    assert result["score_layers"]["source_trust_score"] > 0
    assert result["review_summary"]["short_summary"] == "요약"
    assert "최근 리뷰 1,200건" in result["hooks"]["conversion"]


@pytest.mark.asyncio
async def test_recommend_category_group_filters_prefiltered_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _loaded_state()
    state.serving_products = [
        _source_rich_product(
            "P1",
            category_id="cat_cushion",
            category_name="쿠션",
            representative_product_name="블랙 쿠션",
            keyword_id="kw_thin_spread",
        ),
        _source_rich_product(
            "P2",
            category_id="cat_essence",
            category_name="에센스",
            representative_product_name="보습 에센스",
            keyword_id="kw_thin_spread",
        ),
    ]
    monkeypatch.setattr(server, "demo_state", state)

    async def fake_fetch(product_ids: list[str]) -> dict:
        return {pid: {"match_status": "exact_category", "short_summary": "요약"} for pid in product_ids}

    monkeypatch.setattr(server, "fetch_sidecar_summaries", fake_fetch)

    payload = await server.recommend(
        server.RecommendRequest(user_id="U1", top_k=10, category_group="makeup")
    )

    assert payload["category_group"] == "makeup"
    assert payload["category_label"] == "메이크업"
    assert payload["category_filtered_count"] == 1
    assert [r["product_id"] for r in payload["results"]] == ["P1"]


@pytest.mark.asyncio
async def test_recommend_category_endpoint_reports_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _loaded_state()
    state.serving_products = [
        _source_rich_product(
            "P1",
            category_id="cat_cushion",
            category_name="쿠션",
            representative_product_name="블랙 쿠션",
        ),
        _source_rich_product(
            "P2",
            category_id="cat_essence",
            category_name="에센스",
            representative_product_name="보습 에센스",
        ),
        _source_rich_product(
            "P3",
            category_id="cat_shampoo",
            category_name="샴푸",
            representative_product_name="두피 샴푸",
        ),
    ]
    monkeypatch.setattr(server, "demo_state", state)

    payload = await server.recommend_categories()
    counts = {item["group"]: item["count"] for item in payload["items"]}

    assert counts["all"] == 3
    assert counts["makeup"] == 1
    assert counts["skincare"] == 1
    assert counts["haircare"] == 1
