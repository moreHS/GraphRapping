from __future__ import annotations

import pytest

from src.rec.category_groups import (
    RECOMMEND_CATEGORY_DEFS,
    classify_product_category_group,
    recommend_category_counts,
)
from src.web import server
from src.web.state import DemoState


def _product(
    product_id: str = "P1",
    *,
    category_id: str = "cat_misc",
    category_name: str = "기타",
    representative_product_name: str = "테스트 상품",
    product_name: str | None = None,
) -> dict:
    return {
        "product_id": product_id,
        "category_id": category_id,
        "category_name": category_name,
        "category_concept_ids": [category_id],
        "representative_product_name": representative_product_name,
        "product_name": product_name or representative_product_name,
    }


@pytest.mark.parametrize(
    ("product", "expected_group"),
    [
        (
            _product(
                category_id="cat_body",
                category_name="바디",
                representative_product_name="프래그런스 퍼퓸 바디미스트",
            ),
            "fragrance",
        ),
        (
            _product(
                category_id="cat_bodycare",
                category_name="bodycare",
                representative_product_name="body mist perfume spray",
            ),
            "fragrance",
        ),
        (
            _product(
                category_id="cat_body",
                category_name="바디",
                representative_product_name="두피 진정 스칼프 샴푸",
            ),
            "haircare",
        ),
        (
            _product(
                category_id="cat_skincare",
                category_name="스킨케어",
                representative_product_name="롱웨어 쿠션 베이스",
            ),
            "makeup",
        ),
        (
            _product(
                category_id="cat_skin",
                category_name="기초",
                representative_product_name="벨벳 립스틱",
            ),
            "makeup",
        ),
        (
            _product(
                category_id="cat_skincare",
                category_name="스킨케어",
                representative_product_name="한란 핸드 크림",
            ),
            "bodycare",
        ),
        (
            _product(
                category_id="cat_skincare",
                category_name="스킨케어",
                representative_product_name="보습 바디로션",
            ),
            "bodycare",
        ),
        (
            _product(
                category_id="cat_skincare",
                category_name="스킨케어",
                representative_product_name="고보습 바디크림",
            ),
            "bodycare",
        ),
        (
            _product(
                category_id="cat_body",
                category_name="바디",
                representative_product_name="퍼퓸드 핸드크림",
            ),
            "fragrance",
        ),
    ],
)
def test_specific_recommendation_category_groups_win_before_broad_groups(
    product: dict,
    expected_group: str,
) -> None:
    assert classify_product_category_group(product) == expected_group


@pytest.mark.parametrize(
    ("product", "expected_group"),
    [
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100001",
                "CTGR_L_NAME": "바디",
                "CTGR_M_NAME": "프래그런스",
                "CTGR_S_NAME": "바디미스트",
                "CTGR_SS_NAME": "퍼퓸 바디미스트",
                "ONLINE_PROD_NAME": "향기 좋은 바디 미스트",
            },
            "fragrance",
        ),
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100002",
                "CTGR_L_NAME": "바디",
                "CTGR_M_NAME": "헤어",
                "CTGR_S_NAME": "샴푸",
                "ONLINE_PROD_NAME": "두피 진정 샴푸",
            },
            "haircare",
        ),
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100003",
                "CTGR_L_NAME": "스킨케어",
                "CTGR_M_NAME": "메이크업",
                "CTGR_S_NAME": "베이스메이크업",
                "ONLINE_PROD_NAME": "헤라 블랙 쿠션",
            },
            "makeup",
        ),
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100004",
                "CTGR_L_NAME": "기초",
                "prd_nm": "라네즈 크림 스킨",
            },
            "skincare",
        ),
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100005",
                "REPRESENTATIVE_PROD_NAME": "우디 퍼퓸 디퓨저",
            },
            "fragrance",
        ),
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100006",
                "CTGR_L_NAME": "스킨케어",
                "ONLINE_PROD_NAME": "한란 핸드 크림",
            },
            "bodycare",
        ),
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100007",
                "CTGR_L_NAME": "스킨케어",
                "ONLINE_PROD_NAME": "고보습 바디크림",
            },
            "bodycare",
        ),
        (
            {
                "ONLINE_PROD_SERIAL_NUMBER": "100008",
                "CTGR_L_NAME": "스킨케어",
                "ONLINE_PROD_NAME": "퍼퓸드 핸드크림",
            },
            "fragrance",
        ),
    ],
)
def test_raw_es_product_fields_feed_recommendation_category_classifier(
    product: dict,
    expected_group: str,
) -> None:
    assert classify_product_category_group(product) == expected_group


def test_recommend_category_counts_preserves_all_and_fallback_groups() -> None:
    products = [
        _product("F1", category_name="바디", representative_product_name="퍼퓸 바디미스트"),
        _product("H1", category_name="바디", representative_product_name="두피 샴푸"),
        _product("M1", category_name="스킨케어", representative_product_name="립틴트 쿠션"),
        _product("S1", category_name="세럼", representative_product_name="보습 세럼"),
        _product("O1", category_name="잡화", representative_product_name="테스트 키트"),
    ]

    counts = recommend_category_counts(products)

    assert counts["all"] == 5
    assert counts["fragrance"] == 1
    assert counts["haircare"] == 1
    assert counts["makeup"] == 1
    assert counts["skincare"] == 1
    assert counts["other"] == 1


@pytest.mark.asyncio
async def test_recommend_category_endpoint_keeps_shape_and_shared_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = DemoState(loaded=True)
    state.serving_products = [
        _product("F1", category_name="바디", representative_product_name="퍼퓸 바디미스트"),
        _product("M1", category_name="스킨케어", representative_product_name="쿠션 베이스"),
        _product("H1", category_name="바디", representative_product_name="두피 샴푸"),
    ]
    monkeypatch.setattr(server, "demo_state", state)

    payload = await server.recommend_categories()

    assert list(payload) == ["items"]
    assert [
        {key: item[key] for key in ("group", "label")}
        for item in payload["items"]
    ] == [
        {"group": str(item["group"]), "label": str(item["label"])}
        for item in RECOMMEND_CATEGORY_DEFS
    ]

    counts = {item["group"]: item["count"] for item in payload["items"]}
    assert counts["all"] == 3
    assert counts["fragrance"] == 1
    assert counts["makeup"] == 1
    assert counts["haircare"] == 1
