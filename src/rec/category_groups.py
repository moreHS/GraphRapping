"""
Shared recommendation category-group classification.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.common.text_normalize import normalize_text


RECOMMEND_CATEGORY_DEFS: tuple[dict[str, Any], ...] = (
    {"group": "all", "label": "전체", "keywords": ()},
    {
        "group": "skincare",
        "label": "스킨케어",
        "keywords": (
            "skincare", "skin care", "스킨케어", "기초", "토너", "스킨", "에센스", "세럼", "앰플",
            "크림", "로션", "클렌징", "클렌저", "필링", "팩", "마스크", "패드", "선크림",
            "선케어", "썬크림", "자외선", "아이크림", "립케어", "립밤",
        ),
    },
    {
        "group": "makeup",
        "label": "메이크업",
        "keywords": (
            "makeup", "make up", "메이크업", "색조", "쿠션", "cushion", "파운데이션",
            "베이스", "컨실러", "파우더", "팩트", "블러셔", "하이라이터", "쉐딩", "섀도",
            "아이섀도", "마스카라", "아이라이너", "아이브로우", "립스틱", "틴트",
            "립틴트", "립글로스", "립라이너", "lipstick", "lip tint", "lip gloss",
        ),
    },
    {
        "group": "bodycare",
        "label": "바디",
        "keywords": (
            "body", "bodycare", "body care", "바디", "핸드", "풋", "샤워", "바디워시",
            "바디로션", "바디크림", "데오드란트",
        ),
    },
    {
        "group": "haircare",
        "label": "헤어",
        "keywords": (
            "hair", "haircare", "hair care", "헤어", "샴푸", "린스", "트리트먼트",
            "컨디셔너", "두피", "스칼프", "scalp", "shampoo",
        ),
    },
    {
        "group": "fragrance",
        "label": "향수",
        "keywords": (
            "fragrance", "perfume", "향수", "퍼퓸", "프래그런스", "디퓨저", "코롱",
            "바디미스트", "body mist",
        ),
    },
    {"group": "other", "label": "기타", "keywords": ()},
)

RECOMMEND_CATEGORY_LABELS = {
    str(item["group"]): str(item["label"]) for item in RECOMMEND_CATEGORY_DEFS
}

_RECOMMEND_CATEGORY_KEYWORDS = {
    str(item["group"]): tuple(normalize_text(str(k)) for k in item.get("keywords", ()))
    for item in RECOMMEND_CATEGORY_DEFS
}
_CATEGORY_GROUP_VALUE_ALIASES = {
    "skincare": ("skincare", "skin care", "skin", "스킨케어", "기초"),
    "makeup": ("makeup", "make up", "메이크업", "색조"),
    "bodycare": ("bodycare", "body care", "body", "바디케어", "바디"),
    "haircare": ("haircare", "hair care", "hair", "헤어케어", "헤어", "scalp", "스칼프"),
    "fragrance": ("fragrance", "perfume", "scent", "향수", "퍼퓸", "프래그런스"),
}
_CATEGORY_GROUP_VALUE_ALIAS_KEYS = {
    group: tuple(normalize_text(alias) for alias in aliases)
    for group, aliases in _CATEGORY_GROUP_VALUE_ALIASES.items()
}

# Display order stays stable via RECOMMEND_CATEGORY_DEFS. Classification order is
# intentionally more specific first so broad terms such as "바디" and "크림" do not
# steal fragrance, haircare, makeup, or body-specific products from their tabs.
_CLASSIFICATION_GROUP_ORDER = ("fragrance", "haircare", "makeup", "bodycare", "skincare")


def recommend_category_defs() -> tuple[dict[str, object], ...]:
    return RECOMMEND_CATEGORY_DEFS


def recommend_category_labels() -> dict[str, str]:
    return RECOMMEND_CATEGORY_LABELS


def product_category_text(product: dict) -> str:
    fields = (
        "category_name",
        "category_id",
        "CTGR_L_NAME",
        "CTGR_M_NAME",
        "CTGR_S_NAME",
        "CTGR_SS_NAME",
        "representative_product_name",
        "REPRESENTATIVE_PROD_NAME",
        "product_name",
        "ONLINE_PROD_NAME",
        "ONLINE_PROD_SERIAL_NUMBER",
        "prd_nm",
        "rprs_prd_nm",
    )
    parts: list[str] = []
    for field in fields:
        value = product.get(field)
        if value:
            parts.append(str(value))
    es_meta = product.get("_es_meta") or {}
    if isinstance(es_meta, dict):
        for field in fields:
            value = es_meta.get(field)
            if value:
                parts.append(str(value))
    for field in ("category_concept_ids",):
        for value in product.get(field) or []:
            parts.append(str(value))
    return normalize_text(" ".join(parts))


def classify_product_category_group(product: dict) -> str:
    text = product_category_text(product)
    for group in _CLASSIFICATION_GROUP_ORDER:
        keywords = _RECOMMEND_CATEGORY_KEYWORDS[group]
        if any(keyword and keyword in text for keyword in keywords):
            return group
    return "other"


def category_groups_for_values(values: Iterable[str]) -> set[str]:
    """Map raw/category concept ids to recommendation category groups."""
    groups: set[str] = set()
    for value in values:
        text = _category_value_text(value)
        for group in _CLASSIFICATION_GROUP_ORDER:
            if text in _CATEGORY_GROUP_VALUE_ALIAS_KEYS[group]:
                groups.add(group)
    return groups


def recommend_category_counts(products: list[dict]) -> dict[str, int]:
    counts = {str(item["group"]): 0 for item in RECOMMEND_CATEGORY_DEFS}
    counts["all"] = len(products)
    for product in products:
        counts[classify_product_category_group(product)] += 1
    return counts


def _category_value_text(value: str) -> str:
    if value.startswith("concept:Category:"):
        value = value[len("concept:Category:"):]
    return normalize_text(value)
