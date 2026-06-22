#!/usr/bin/env python3
"""Build the dense golden recommendation fixture.

The checked-in 906-review fixture remains the wide source-identity baseline.
This script writes a separate dense fixture under mockdata/dense_golden so
recommendation tests can exercise promoted review evidence with enough per-
product support.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.loaders.source_review_stats_loader import load_source_review_stats_snapshot
from src.rec.category_groups import classify_product_category_group


DEFAULT_SEED = 20260622
FIXTURE_DATE = "2026-06-22"
GOLDEN_PROFILE_IDS = [
    "user_dry_30f",
    "user_brand_null_cat",
    "user_sensitive_40f",
    "user_scalp_care_50m",
    "user_fragrance_60f",
    "user_makeup_matte_50m",
]
RECOMMENDATION_GROUPS = ("skincare", "makeup", "bodycare", "haircare", "fragrance")

NOISE_CATEGORY_TOKENS = (
    "소품",
    "도구",
    "메이크업소품",
    "스킨케어소품",
    "바디/헤어소품",
    "뷰티툴/소품",
    "미용 소도구",
    "구강",
    "치약",
    "칫솔",
    "Beauty서비스",
    "서비스",
    "화장솜",
    "면봉",
    "퍼프",
    "브러쉬",
    "브러시",
    "공병",
    "케이스",
    "Beauty서비스기타",
)
NOISE_NAME_TOKENS = (
    "SOURCE_KEY_COLLISION",
    "source_key_collision",
    "면봉",
    "치약",
    "칫솔",
    "구강",
    "0원",
    "체험단",
    "쿠폰",
    "발송되는 제품 없음",
    "서비스",
    "service",
    "화장솜",
    "퍼프",
    "공병",
    "쇼핑백",
    "종이백",
    "브러쉬",
    "브러시",
    "수정칼",
    "스펀지",
    "쇼핑백",
    "토트백",
    "키링",
    "파우치",
    "지퍼백",
    "기름종이",
    "샤워 볼",
    "샘플",
    "증정",
    "리필용기",
)


@dataclass
class BuildInputs:
    review_path: Path
    product_path: Path
    stats_path: Path
    user_path: Path
    output_dir: Path
    seed: int


@dataclass
class ProductSelection:
    products: list[dict[str, Any]]
    selected: dict[str, set[str]] = field(default_factory=dict)
    groups: dict[str, str] = field(default_factory=dict)
    source_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    anchor_diagnostics: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"resolved": [], "ambiguous": [], "unresolved": []}
    )

    @property
    def ordered_product_ids(self) -> list[str]:
        return [pid for pid in (str(p["ONLINE_PROD_SERIAL_NUMBER"]) for p in self.products) if pid in self.selected]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-path", default="mockdata/review_triples_raw.json")
    parser.add_argument("--product-path", default="mockdata/product_catalog_es.json")
    parser.add_argument("--stats-path", default="data/source_snapshots/product_review_stats_snowflake_latest.json")
    parser.add_argument("--user-path", default="mockdata/user_profiles_normalized.json")
    parser.add_argument("--output-dir", default="mockdata/dense_golden")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--dry-run", action="store_true", help="Build and print summary without writing files.")
    args = parser.parse_args()

    inputs = BuildInputs(
        review_path=Path(args.review_path),
        product_path=Path(args.product_path),
        stats_path=Path(args.stats_path),
        user_path=Path(args.user_path),
        output_dir=Path(args.output_dir),
        seed=args.seed,
    )
    expected_output_dir = Path("mockdata/dense_golden")
    if not args.dry_run and inputs.output_dir.resolve() != expected_output_dir.resolve():
        parser.error("non-dry-run writes are restricted to mockdata/dense_golden")
    outputs, manifest = build_fixture(inputs)

    if args.dry_run:
        print(json.dumps(_dry_run_summary(manifest), ensure_ascii=False, indent=2, sort_keys=True))
        return

    write_fixture(outputs, manifest, inputs.output_dir)
    print(json.dumps(_dry_run_summary(manifest), ensure_ascii=False, indent=2, sort_keys=True))


def build_fixture(inputs: BuildInputs) -> tuple[dict[str, Any], dict[str, Any]]:
    reviews = _load_json(inputs.review_path)
    products = _load_json(inputs.product_path)
    users = _load_json(inputs.user_path)

    if not isinstance(reviews, list):
        raise TypeError(f"{inputs.review_path} must contain a JSON array")
    if not isinstance(products, list):
        raise TypeError(f"{inputs.product_path} must contain a JSON array")
    if not isinstance(users, dict):
        raise TypeError(f"{inputs.user_path} must contain a JSON object")

    stats_by_product = load_source_review_stats_snapshot(inputs.stats_path)
    selection = select_products(products, stats_by_product, users)
    dense_products = [copy.deepcopy(p) for p in products if str(p.get("ONLINE_PROD_SERIAL_NUMBER")) in selection.selected]
    selected_by_id = {str(p["ONLINE_PROD_SERIAL_NUMBER"]): p for p in dense_products}
    dense_reviews, remap_summary = remap_reviews(reviews, products, selection, selected_by_id, inputs.seed)
    dense_users, profile_summary = extract_golden_profiles(users)

    outputs = {
        "review_triples_raw.json": dense_reviews,
        "product_catalog_es.json": dense_products,
        "user_profiles_normalized.json": dense_users,
    }

    output_hashes = {
        name: _sha256_bytes(_json_bytes(payload))
        for name, payload in outputs.items()
    }
    manifest = build_manifest(
        inputs=inputs,
        reviews=reviews,
        products=products,
        users=users,
        selection=selection,
        dense_products=dense_products,
        remap_summary=remap_summary,
        profile_summary=profile_summary,
        output_hashes=output_hashes,
    )
    outputs["manifest.json"] = manifest
    return outputs, manifest


def select_products(
    products: list[dict[str, Any]],
    stats_by_product: dict[str, dict[str, Any]],
    users: dict[str, Any],
) -> ProductSelection:
    product_by_id = {str(p["ONLINE_PROD_SERIAL_NUMBER"]): p for p in products if p.get("ONLINE_PROD_SERIAL_NUMBER") is not None}
    eligible = [
        product_by_id[pid]
        for pid in sorted(product_by_id)
        if (
            pid in stats_by_product
            and not is_noise_product(product_by_id[pid])
            and classify_product_group(product_by_id[pid]) in RECOMMENDATION_GROUPS
        )
    ]

    selected: dict[str, set[str]] = {}
    groups: dict[str, str] = {}

    def add(product: dict[str, Any], reason: str) -> None:
        pid = str(product["ONLINE_PROD_SERIAL_NUMBER"])
        selected.setdefault(pid, set()).add(reason)
        groups[pid] = classify_product_group(product)

    for product in _sort_products_by_stats(eligible, stats_by_product)[:20]:
        add(product, "overall_top20")

    for group in RECOMMENDATION_GROUPS:
        group_products = [p for p in eligible if classify_product_group(p) == group]
        for product in _sort_products_by_stats(group_products, stats_by_product)[:5]:
            add(product, f"{group}_top5")

    anchor_diagnostics = resolve_profile_anchors(users, product_by_id, stats_by_product)
    for item in anchor_diagnostics["resolved"]:
        product = product_by_id[item["product_id"]]
        add(product, "profile_anchor")

    if len(selected) > 45:
        _trim_to_max(selected, groups, stats_by_product, max_count=45)

    ordered_selected = [
        product_by_id[pid]
        for pid in sorted(
            selected,
            key=lambda pid: (
                -int(_stat(stats_by_product.get(pid), "source_review_count_6m", "review_count_6m")),
                groups.get(pid, ""),
                pid,
            ),
        )
    ]
    return ProductSelection(
        products=ordered_selected,
        selected=selected,
        groups=groups,
        source_stats=stats_by_product,
        anchor_diagnostics=anchor_diagnostics,
    )


def remap_reviews(
    reviews: list[dict[str, Any]],
    all_products: list[dict[str, Any]],
    selection: ProductSelection,
    selected_by_id: dict[str, dict[str, Any]],
    seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_product_by_id = {
        str(p["ONLINE_PROD_SERIAL_NUMBER"]): p
        for p in all_products
        if p.get("ONLINE_PROD_SERIAL_NUMBER") is not None
    }
    selected_ids = selection.ordered_product_ids
    if not selected_ids:
        raise ValueError("No products selected for dense fixture")

    selected_by_group: dict[str, list[str]] = {group: [] for group in RECOMMENDATION_GROUPS}
    for pid in selected_ids:
        group = selection.groups.get(pid, "unknown")
        if group in selected_by_group:
            selected_by_group[group].append(pid)

    cursors: Counter[str] = Counter()
    dense_reviews: list[dict[str, Any]] = []
    assigned_counts: Counter[str] = Counter()
    assigned_group_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()

    for index, review in enumerate(reviews):
        original_pid = _optional_str(review.get("source_product_id") or review.get("product_id"))
        original_product = all_product_by_id.get(original_pid or "")
        inferred_group = infer_review_group(review, original_product)
        pool = selected_by_group.get(inferred_group) or selected_ids
        cursor_key = inferred_group if selected_by_group.get(inferred_group) else "all"
        offset = (cursors[cursor_key] + seed + index) % len(pool)
        target_pid = pool[offset]
        cursors[cursor_key] += 1

        target_product = selected_by_id[target_pid]
        target_group = selection.groups.get(target_pid, "unknown")
        reason = f"dense_round_robin:{inferred_group if selected_by_group.get(inferred_group) else 'fallback_all'}"

        remapped = copy.deepcopy(review)
        remapped["fixture_original_source_product_id"] = original_pid
        remapped["fixture_original_prod_nm"] = review.get("prod_nm")
        remapped["fixture_original_brnd_nm"] = review.get("brnd_nm")
        remapped["fixture_original_channel"] = review.get("channel")
        remapped["fixture_remap_reason"] = reason
        remapped["source_product_id"] = target_pid
        remapped["prod_nm"] = target_product.get("prd_nm") or target_product.get("ONLINE_PROD_NAME") or ""
        remapped["brnd_nm"] = target_product.get("BRAND_NAME")
        remapped["channel"] = target_product.get("SOURCE_CHANNEL") or target_product.get("channel")
        remapped["source_channel"] = target_product.get("SOURCE_CHANNEL") or target_product.get("channel")
        remapped["source_key_type"] = target_product.get("SOURCE_KEY_TYPE") or "ecp_onln_prd_srno"
        if "product_id" in remapped:
            remapped["product_id"] = target_pid
        dense_reviews.append(remapped)

        assigned_counts[target_pid] += 1
        assigned_group_counts[target_group] += 1
        reason_counts[reason] += 1

    return dense_reviews, {
        "total_reviews": len(dense_reviews),
        "distinct_original_products": len({_optional_str(r.get("source_product_id") or r.get("product_id")) for r in reviews}),
        "distinct_dense_products": len(assigned_counts),
        "assigned_counts_by_product": dict(sorted(assigned_counts.items())),
        "assigned_counts_by_group": dict(sorted(assigned_group_counts.items())),
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def extract_golden_profiles(users: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    missing = [uid for uid in GOLDEN_PROFILE_IDS if uid not in users]
    if missing:
        raise ValueError(f"Missing golden profile(s) in normalized fixture: {', '.join(missing)}")
    dense_users = {uid: copy.deepcopy(users[uid]) for uid in GOLDEN_PROFILE_IDS}
    coverage = {uid: profile_coverage(profile) for uid, profile in dense_users.items()}
    return dense_users, {
        "profile_ids": GOLDEN_PROFILE_IDS,
        "profile_count": len(dense_users),
        "coverage": coverage,
        "source_policy": "copied_from_existing_normalized_fixture_after_verifying_personal_agent_final_six_ids",
    }


def build_manifest(
    *,
    inputs: BuildInputs,
    reviews: list[dict[str, Any]],
    products: list[dict[str, Any]],
    users: dict[str, Any],
    selection: ProductSelection,
    dense_products: list[dict[str, Any]],
    remap_summary: dict[str, Any],
    profile_summary: dict[str, Any],
    output_hashes: dict[str, str],
) -> dict[str, Any]:
    selected_products = []
    category_groups: dict[str, list[str]] = {group: [] for group in RECOMMENDATION_GROUPS}
    for product in dense_products:
        pid = str(product["ONLINE_PROD_SERIAL_NUMBER"])
        group = selection.groups.get(pid, classify_product_group(product))
        if group in category_groups:
            category_groups[group].append(pid)
        selected_products.append({
            "product_id": pid,
            "product_name": product.get("prd_nm") or product.get("ONLINE_PROD_NAME"),
            "brand_name": product.get("BRAND_NAME"),
            "category_group": group,
            "category_names": {
                "large": product.get("CTGR_L_NAME"),
                "medium": product.get("CTGR_M_NAME"),
                "small": product.get("CTGR_S_NAME"),
                "subsmall": product.get("CTGR_SS_NAME"),
            },
            "source_stats": source_stats_summary(selection.source_stats.get(pid)),
            "selection_reasons": sorted(selection.selected.get(pid, [])),
        })

    input_hashes = {
        "review_triples_raw.json": _sha256_file(inputs.review_path),
        "product_catalog_es.json": _sha256_file(inputs.product_path),
        "product_review_stats_snowflake_latest.json": _sha256_file(inputs.stats_path),
        "user_profiles_normalized.json": _sha256_file(inputs.user_path),
    }
    return {
        "schema_version": 1,
        "fixture_name": "dense_golden",
        "fixture_date": FIXTURE_DATE,
        "seed": inputs.seed,
        "policy": {
            "wide_baseline_preserved": True,
            "selection": [
                "top 20 source_review_count_6m overall after service/noise exclusion",
                "top 5 source_review_count_6m per recommendation category group",
                "profile anchors only when exact product id or exact normalized name resolves to one product",
            ],
            "review_remap": "text, NER, BEE, and relation annotations are preserved; product metadata is remapped.",
        },
        "inputs": {
            "review_count": len(reviews),
            "wide_distinct_product_count": len({_optional_str(r.get("source_product_id") or r.get("product_id")) for r in reviews}),
            "product_count": len(products),
            "user_count": len(users),
        },
        "selection": {
            "selected_product_count": len(dense_products),
            "target_min_products": 30,
            "target_max_products": 45,
            "noise_exclusion_tokens": {
                "category": list(NOISE_CATEGORY_TOKENS),
                "name": list(NOISE_NAME_TOKENS),
            },
            "selected_products": selected_products,
            "category_groups": {group: ids for group, ids in category_groups.items()},
            "anchor_resolution": selection.anchor_diagnostics,
        },
        "review_remap": remap_summary,
        "profiles": profile_summary,
        "hashes": {
            "algorithm": "sha256",
            "inputs": input_hashes,
            "outputs": output_hashes,
            "manifest_self_hash": None,
            "manifest_self_hash_note": "Self-hash is intentionally omitted because it would change the manifest content.",
        },
    }


def write_fixture(outputs: dict[str, Any], manifest: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("review_triples_raw.json", "product_catalog_es.json", "user_profiles_normalized.json"):
        _write_bytes_if_changed(output_dir / name, _json_bytes(outputs[name]))
    _write_bytes_if_changed(output_dir / "manifest.json", _json_bytes(manifest))


def _write_bytes_if_changed(path: Path, payload: bytes) -> None:
    if path.exists() and path.read_bytes() == payload:
        return
    path.write_bytes(payload)


def resolve_profile_anchors(
    users: dict[str, Any],
    product_by_id: dict[str, dict[str, Any]],
    stats_by_product: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    name_index: dict[str, set[str]] = defaultdict(set)
    for pid, product in product_by_id.items():
        for field in ("prd_nm", "ONLINE_PROD_NAME", "REPRESENTATIVE_PROD_NAME"):
            value = product.get(field)
            if value:
                name_index[_normalize_key(value)].add(pid)

    diagnostics: dict[str, list[dict[str, Any]]] = {"resolved": [], "ambiguous": [], "unresolved": []}
    seen_queries: set[tuple[str, str]] = set()
    for uid in GOLDEN_PROFILE_IDS:
        profile = users.get(uid) or {}
        for item in _iter_purchase_summary_items(profile):
            name = item.get("rprs_prd_nm") or item.get("prd_nm") or item.get("product_name")
            codes = [
                str(item[key])
                for key in ("rprs_prd_cd", "prd_cd", "product_id", "source_product_id")
                if item.get(key)
            ]
            query = (uid, "|".join(codes) + "|" + str(name))
            if query in seen_queries:
                continue
            seen_queries.add(query)

            code_hits = [code for code in codes if code in product_by_id]
            name_hits = sorted(name_index.get(_normalize_key(name), set())) if name else []
            hits = sorted(set(code_hits + name_hits))
            payload = {"user_id": uid, "query_name": name, "query_codes": codes, "matches": hits}
            if len(hits) == 1 and hits[0] in stats_by_product:
                payload["product_id"] = hits[0]
                payload["source_review_count_6m"] = _stat(stats_by_product.get(hits[0]), "source_review_count_6m", "review_count_6m")
                diagnostics["resolved"].append(payload)
            elif len(hits) > 1:
                diagnostics["ambiguous"].append(payload)
            else:
                diagnostics["unresolved"].append(payload)
    return diagnostics


def _iter_purchase_summary_items(profile: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if any(key in value for key in ("rprs_prd_nm", "prd_nm", "product_name", "rprs_prd_cd", "prd_cd")):
                items.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(profile.get("purchase_analysis") or {})
    return items


def profile_coverage(profile: dict[str, Any]) -> dict[str, Any]:
    purchase = profile.get("purchase_analysis") or {}
    chat = profile.get("chat") or {}
    purchase_brands = _flatten_values(
        purchase.get("preferred_brand"),
        purchase.get("preferred_skincare_brand"),
        purchase.get("preferred_makeup_brand"),
        purchase.get("preferred_bodycare_brand"),
        purchase.get("preferred_hair_brand"),
        purchase.get("preferred_perfume_brand"),
    )
    all_brands = _flatten_values(
        purchase_brands,
        chat.get("preferred_brand"),
        chat.get("preferred_skincare_brand"),
        chat.get("preferred_makeup_brand"),
        chat.get("preferred_bodycare_brand"),
        chat.get("preferred_hair_brand"),
        chat.get("preferred_perfume_brand"),
    )
    return {
        "preferred_brands": all_brands,
        "purchase_preferred_brands": purchase_brands,
        "active_categories": _flatten_values(purchase.get("active_product_category")),
        "owned_or_summary_products": [
            item.get("rprs_prd_nm") or item.get("prd_nm") or item.get("product_name")
            for item in _iter_purchase_summary_items(profile)
            if item.get("rprs_prd_nm") or item.get("prd_nm") or item.get("product_name")
        ],
        "concerns": _flatten_values(
            (profile.get("basic") or {}).get("skin_concerns"),
            (chat.get("face") or {}).get("skin_concerns"),
            (chat.get("scalp") or {}).get("concerns"),
            (chat.get("hair") or {}).get("concerns"),
            (chat.get("body") or {}).get("concerns"),
        ),
        "goals": _flatten_values(
            (chat.get("face") or {}).get("skincare_goals"),
            (chat.get("hair") or {}).get("haircare_goals"),
            (chat.get("body") or {}).get("bodycare_goals"),
            (chat.get("makeup") or {}).get("makeup_goals"),
        ),
        "texture_or_value_preferences": _flatten_values(
            (chat.get("face") or {}).get("texture_preferences"),
            (chat.get("makeup") or {}).get("finish_preferences"),
            (chat.get("scent") or {}).get("scent_preferences"),
        ),
        "empty_fields": {
            "has_no_purchase_preferred_brand": not purchase_brands,
            "has_no_any_preferred_brand": not all_brands,
            "has_no_active_category": not _flatten_values(purchase.get("active_product_category")),
            "has_no_chat": not bool(chat),
        },
    }


def infer_review_group(review: dict[str, Any], original_product: dict[str, Any] | None) -> str:
    if original_product:
        group = classify_product_group(original_product)
        if group in RECOMMENDATION_GROUPS:
            return group
    text = " ".join(str(review.get(key) or "") for key in ("prod_nm", "text"))
    return classify_text_group(text)


def classify_product_group(product: dict[str, Any]) -> str:
    shared_group = classify_product_category_group(_adapt_product_for_shared_classifier(product))
    if shared_group in RECOMMENDATION_GROUPS:
        return shared_group
    text = " ".join(str(product.get(key) or "") for key in (
        "CTGR_L_NAME",
        "CTGR_M_NAME",
        "CTGR_S_NAME",
        "CTGR_SS_NAME",
        "prd_nm",
        "ONLINE_PROD_NAME",
        "REPRESENTATIVE_PROD_NAME",
    ))
    return classify_text_group(text)


def classify_text_group(text: str) -> str:
    normalized = str(text or "").lower()
    # Keep this fallback aligned with src.rec.category_groups'
    # specificity-first order: fragrance, haircare, makeup, skincare, bodycare.
    if any(token in normalized for token in ("향수", "오드", "퍼퓸", "프래그런스", "fragrance", "perfume", "디퓨저", "코롱", "바디미스트", "body mist")):
        return "fragrance"
    if any(token in normalized for token in ("샴푸", "트리트먼트", "린스", "컨디셔너", "두피", "스칼프", "scalp", "헤어", "염색", "hair", "shampoo")):
        return "haircare"
    if any(token in normalized for token in ("메이크업", "색조", "쿠션", "립", "틴트", "마스카라", "브로우", "섀도", "파운데이션", "컨실러", "프라이머", "파우더", "블러셔", "팩트", "베이스")):
        return "makeup"
    if any(token in normalized for token in ("스킨케어", "기초", "크림", "세럼", "앰플", "토너", "스킨", "에센스", "마스크", "팩", "패드", "선크림", "선케어", "클렌징", "클렌저", "필링", "아이크림", "립케어", "립밤")):
        return "skincare"
    if any(token in normalized for token in ("body", "bodycare", "바디", "핸드", "풋", "샤워", "워시", "바디로션", "바디크림", "데오드란트")):
        return "bodycare"
    return "other"


def _adapt_product_for_shared_classifier(product: dict[str, Any]) -> dict[str, Any]:
    category_text = " ".join(
        str(product.get(key) or "")
        for key in ("CTGR_L_NAME", "CTGR_M_NAME", "CTGR_S_NAME", "CTGR_SS_NAME")
    )
    return {
        "category_name": category_text,
        "category_id": category_text,
        "representative_product_name": product.get("REPRESENTATIVE_PROD_NAME"),
        "product_name": product.get("prd_nm") or product.get("ONLINE_PROD_NAME"),
        "prd_nm": product.get("prd_nm") or product.get("ONLINE_PROD_NAME"),
    }


def is_noise_product(product: dict[str, Any]) -> bool:
    identity_text = " ".join(str(product.get(key) or "") for key in (
        "SOURCE_TRUTH_QUALITY",
        "SOURCE_TRUTH_SOURCE",
        "SOURCE_COMPAT_COLLAPSED",
        "SOURCE_IDENTITY_KEY",
        "SOURCE_KEY_TYPE",
    ))
    if (
        str(product.get("SOURCE_TRUTH_QUALITY") or "").upper() == "SOURCE_KEY_COLLISION"
        or str(product.get("SOURCE_KEY_TYPE") or "").lower() == "source_key_collision"
        or product.get("SOURCE_COMPAT_COLLAPSED") is True
        or "COLLISION" in identity_text.upper()
    ):
        return True

    category_text = " ".join(str(product.get(key) or "") for key in ("CTGR_L_NAME", "CTGR_M_NAME", "CTGR_S_NAME", "CTGR_SS_NAME"))
    name = str(product.get("prd_nm") or product.get("ONLINE_PROD_NAME") or "")
    category_norm = category_text.casefold()
    name_norm = name.casefold()
    return (
        any(token.casefold() in category_norm for token in NOISE_CATEGORY_TOKENS)
        or any(token.casefold() in name_norm for token in NOISE_NAME_TOKENS)
    )


def source_stats_summary(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "source_review_count_6m": None,
            "source_avg_rating_6m": None,
            "source_review_count_all": None,
            "source_avg_rating_all": None,
            "source": None,
        }
    keys = [
        "source_review_count_6m",
        "source_avg_rating_6m",
        "source_review_min_date_6m",
        "source_review_max_date_6m",
        "source_review_count_all",
        "source_avg_rating_all",
        "source_review_min_date_all",
        "source_review_max_date_all",
        "source",
        "source_channel",
        "source_key_type",
    ]
    return {key: row.get(key) for key in keys}


def _sort_products_by_stats(
    products: list[dict[str, Any]],
    stats_by_product: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        products,
        key=lambda product: (
            -int(_stat(stats_by_product.get(str(product["ONLINE_PROD_SERIAL_NUMBER"])), "source_review_count_6m", "review_count_6m")),
            str(product.get("prd_nm") or ""),
            str(product["ONLINE_PROD_SERIAL_NUMBER"]),
        ),
    )


def _trim_to_max(
    selected: dict[str, set[str]],
    groups: dict[str, str],
    stats_by_product: dict[str, dict[str, Any]],
    *,
    max_count: int,
) -> None:
    while len(selected) > max_count:
        candidates = [
            pid for pid, reasons in selected.items()
            if "profile_anchor" not in reasons and not any(reason.endswith("_top5") for reason in reasons)
        ]
        if not candidates:
            break
        victim = sorted(
            candidates,
            key=lambda pid: (
                int(_stat(stats_by_product.get(pid), "source_review_count_6m", "review_count_6m")),
                groups.get(pid, ""),
                pid,
            ),
        )[0]
        selected.pop(victim, None)
        groups.pop(victim, None)


def _flatten_values(*values: Any) -> list[str]:
    result: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in value:
                visit(key)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)
        elif value is not None and str(value).strip():
            text = str(value).strip()
            if text not in result:
                result.append(text)

    for value in values:
        visit(value)
    return result


def _stat(row: dict[str, Any] | None, *keys: str) -> int | float:
    if not row:
        return 0
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        try:
            return int(float(value))
        except (TypeError, ValueError):
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0
    return 0


def _normalize_key(value: Any) -> str:
    text = str(value or "").casefold()
    return re.sub(r"\s+", "", text)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text.strip() and text != "None" else None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _dry_run_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    selected = manifest["selection"]["selected_products"]
    return {
        "dry_run_safe": True,
        "fixture_name": manifest["fixture_name"],
        "seed": manifest["seed"],
        "selected_product_count": len(selected),
        "review_count": manifest["review_remap"]["total_reviews"],
        "dense_distinct_products": manifest["review_remap"]["distinct_dense_products"],
        "profile_ids": manifest["profiles"]["profile_ids"],
        "category_group_counts": {
            group: len(ids)
            for group, ids in manifest["selection"]["category_groups"].items()
        },
        "anchor_resolution_counts": {
            key: len(value)
            for key, value in manifest["selection"]["anchor_resolution"].items()
        },
        "output_hashes": manifest["hashes"]["outputs"],
    }


if __name__ == "__main__":
    main()
