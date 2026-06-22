from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from src.jobs.run_full_load import FullLoadConfig, run_full_load
from src.loaders.user_loader import load_users_from_profiles
from scripts.build_dense_golden_fixture import classify_product_group, is_noise_product


ROOT = Path(__file__).resolve().parents[1]
WIDE = ROOT / "mockdata"
DENSE = WIDE / "dense_golden"
SCRIPT = ROOT / "scripts" / "build_dense_golden_fixture.py"
GOLDEN_PROFILE_IDS = {
    "user_dry_30f",
    "user_brand_null_cat",
    "user_sensitive_40f",
    "user_scalp_care_50m",
    "user_fragrance_60f",
    "user_makeup_matte_50m",
}
TOP_SIGNAL_FIELDS = (
    "top_bee_attr_ids",
    "top_keyword_ids",
    "top_context_ids",
    "top_concern_pos_ids",
    "top_concern_neg_ids",
    "top_tool_ids",
    "top_comparison_product_ids",
    "top_coused_product_ids",
)
FORBIDDEN_SELECTED_TOKENS = (
    "SOURCE_KEY_COLLISION",
    "source_key_collision",
    "collision",
    "0원",
    "체험단",
    "쿠폰",
    "발송되는 제품 없음",
    "서비스",
    "service",
    "면봉",
    "치약",
    "칫솔",
    "구강",
    "화장솜",
    "퍼프",
    "브러시",
    "브러쉬",
    "쇼핑백",
    "토트백",
    "키링",
    "파우치",
    "지퍼백",
    "기름종이",
    "수정칼",
    "스펀지",
    "샤워 볼",
)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_dense_builder_dry_run_does_not_write(tmp_path: Path) -> None:
    output_dir = tmp_path / "dense_out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--dry-run",
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    summary = json.loads(result.stdout)
    assert summary["dry_run_safe"] is True
    assert 30 <= summary["selected_product_count"] <= 45
    assert summary["review_count"] == 906
    assert set(summary["profile_ids"]) == GOLDEN_PROFILE_IDS
    assert not output_dir.exists()


def test_dense_builder_rejects_non_default_non_dry_run_output_dir(tmp_path: Path) -> None:
    output_dir = tmp_path / "dense_out"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "non-dry-run writes are restricted to mockdata/dense_golden" in result.stderr
    assert not output_dir.exists()


def test_dense_manifest_and_selection_contract() -> None:
    manifest = _load_json(DENSE / "manifest.json")
    dense_products = _load_json(DENSE / "product_catalog_es.json")

    assert manifest["fixture_name"] == "dense_golden"
    assert manifest["seed"] == 20260622
    assert manifest["policy"]["wide_baseline_preserved"] is True
    assert 30 <= manifest["selection"]["selected_product_count"] <= 45
    assert manifest["selection"]["selected_product_count"] == len(dense_products)

    selected = manifest["selection"]["selected_products"]
    selected_ids = {row["product_id"] for row in selected}
    dense_product_by_id = {str(row["ONLINE_PROD_SERIAL_NUMBER"]): row for row in dense_products}
    assert selected_ids == set(dense_product_by_id)

    reasons = {
        reason
        for row in selected
        for reason in row["selection_reasons"]
    }
    assert "overall_top20" in reasons
    assert {"skincare_top5", "makeup_top5", "bodycare_top5", "haircare_top5", "fragrance_top5"} <= reasons
    assert "profile_anchor" in reasons

    group_counts = {
        group: len(product_ids)
        for group, product_ids in manifest["selection"]["category_groups"].items()
    }
    assert group_counts["skincare"] >= 5
    assert group_counts["makeup"] >= 5
    assert group_counts["bodycare"] >= 5
    assert group_counts["haircare"] >= 5
    assert group_counts["fragrance"] >= 1
    assert sum(group_counts.values()) == len(dense_products)

    for row in selected:
        product = dense_product_by_id[row["product_id"]]
        stats = row["source_stats"]
        assert stats["source_review_count_6m"] > 0
        assert stats["source_avg_rating_6m"] is not None
        assert row["category_group"] in {"skincare", "makeup", "bodycare", "haircare", "fragrance"}
        assert classify_product_group(product) == row["category_group"]
        assert not is_noise_product(product)
        assert product.get("SOURCE_TRUTH_QUALITY") != "SOURCE_KEY_COLLISION"
        assert product.get("SOURCE_KEY_TYPE") != "source_key_collision"
        assert product.get("SOURCE_COMPAT_COLLAPSED") is not True
        product_text = " ".join(
            str(value or "")
            for value in [
                row["product_id"],
                row["product_name"],
                row["brand_name"],
                product.get("SOURCE_TRUTH_QUALITY"),
                product.get("SOURCE_KEY_TYPE"),
                product.get("SOURCE_IDENTITY_KEY"),
                row["category_names"]["medium"],
                row["category_names"]["small"],
                row["category_names"]["subsmall"],
            ]
        )
        assert not any(token.casefold() in product_text.casefold() for token in FORBIDDEN_SELECTED_TOKENS)

    anchors = manifest["selection"]["anchor_resolution"]
    assert len(anchors["resolved"]) == 1
    assert anchors["ambiguous"] == []
    assert anchors["unresolved"]


def test_dense_review_linkage_preserves_review_annotations_and_original_fixture() -> None:
    manifest = _load_json(DENSE / "manifest.json")
    wide_reviews = _load_json(WIDE / "review_triples_raw.json")
    dense_reviews = _load_json(DENSE / "review_triples_raw.json")
    dense_products = _load_json(DENSE / "product_catalog_es.json")

    assert len(wide_reviews) == 906
    assert len({str(row["source_product_id"]) for row in wide_reviews}) == 517
    assert _sha256_file(WIDE / "review_triples_raw.json") == manifest["hashes"]["inputs"]["review_triples_raw.json"]

    product_by_id = {
        str(row["ONLINE_PROD_SERIAL_NUMBER"]): row
        for row in dense_products
    }
    assert len(dense_reviews) == 906
    assert len({str(row["source_product_id"]) for row in dense_reviews}) == len(product_by_id)

    for original, dense in zip(wide_reviews, dense_reviews, strict=True):
        assert dense["text"] == original["text"]
        assert dense["ner"] == original["ner"]
        assert dense["bee"] == original["bee"]
        assert dense["relation"] == original["relation"]
        assert dense["fixture_original_source_product_id"] == original.get("source_product_id")
        assert dense["fixture_original_prod_nm"] == original.get("prod_nm")
        assert dense["fixture_remap_reason"].startswith("dense_round_robin:")

        target = product_by_id[str(dense["source_product_id"])]
        assert dense["prod_nm"] == target["prd_nm"]
        assert dense["brnd_nm"] == target["BRAND_NAME"]
        assert dense["channel"] == target["SOURCE_CHANNEL"]
        assert dense["source_channel"] == target["SOURCE_CHANNEL"]
        assert dense["source_key_type"] == target["SOURCE_KEY_TYPE"]

    assert _sha256_file(DENSE / "review_triples_raw.json") == manifest["hashes"]["outputs"]["review_triples_raw.json"]
    assert _sha256_file(DENSE / "product_catalog_es.json") == manifest["hashes"]["outputs"]["product_catalog_es.json"]
    assert _sha256_file(DENSE / "user_profiles_normalized.json") == manifest["hashes"]["outputs"]["user_profiles_normalized.json"]


def test_dense_golden_profiles_are_final_six_and_loadable() -> None:
    manifest = _load_json(DENSE / "manifest.json")
    profiles = _load_json(DENSE / "user_profiles_normalized.json")

    assert set(profiles) == GOLDEN_PROFILE_IDS
    assert len(profiles) == 6
    result = load_users_from_profiles(profiles)
    assert result.user_count == 6

    sparse = profiles["user_brand_null_cat"]
    purchase = sparse["purchase_analysis"]
    assert purchase["preferred_brand"] == []
    assert purchase["active_product_category"] == []
    empty_fields = manifest["profiles"]["coverage"]["user_brand_null_cat"]["empty_fields"]
    assert empty_fields["has_no_purchase_preferred_brand"] is True
    assert empty_fields["has_no_any_preferred_brand"] is False
    assert empty_fields["has_no_active_category"] is True


@pytest.fixture(scope="module")
def _density_metrics() -> dict[str, dict[str, Any]]:
    return {
        "wide": _run_pipeline_metrics(
            WIDE / "review_triples_raw.json",
            WIDE / "product_catalog_es.json",
            WIDE / "user_profiles_normalized.json",
        ),
        "dense": _run_pipeline_metrics(
            DENSE / "review_triples_raw.json",
            DENSE / "product_catalog_es.json",
            DENSE / "user_profiles_normalized.json",
        ),
    }


def _run_pipeline_metrics(review_path: Path, product_path: Path, user_path: Path) -> dict[str, Any]:
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_full_load(FullLoadConfig(
            review_json_path=str(review_path),
            product_es_records=_load_json(product_path),
            user_profiles=_load_json(user_path),
            kg_mode="on",
        ))

    agg = result.batch_result.get("agg_signals", [])
    promoted = [row for row in agg if row.is_promoted]
    promoted_by_edge: dict[str, int] = {}
    for row in promoted:
        promoted_by_edge[row.canonical_edge_type] = promoted_by_edge.get(row.canonical_edge_type, 0) + 1

    return {
        "review_count": result.review_count,
        "product_count": result.product_count,
        "user_count": result.user_count,
        "promoted_count": len(promoted),
        "promoted_by_edge": promoted_by_edge,
        "top_field_product_counts": {
            field: sum(1 for product in result.serving_products if product.get(field))
            for field in TOP_SIGNAL_FIELDS
        },
        "top_field_item_counts": {
            field: sum(len(product.get(field) or []) for product in result.serving_products)
            for field in TOP_SIGNAL_FIELDS
        },
    }


def test_dense_full_load_has_materially_more_promoted_relation_density(
    _density_metrics: dict[str, dict[str, Any]],
) -> None:
    wide = _density_metrics["wide"]
    dense = _density_metrics["dense"]

    assert wide["review_count"] == dense["review_count"] == 906
    assert wide["product_count"] == 517
    assert dense["product_count"] == 32
    assert dense["user_count"] == 6

    assert dense["promoted_count"] > wide["promoted_count"] * 2
    assert (
        dense["promoted_count"] / dense["product_count"]
        > (wide["promoted_count"] / wide["product_count"]) * 5
    )
    assert dense["promoted_by_edge"]["HAS_BEE_ATTR_SIGNAL"] > wide["promoted_by_edge"]["HAS_BEE_ATTR_SIGNAL"] * 2
    assert dense["top_field_product_counts"]["top_bee_attr_ids"] > wide["top_field_product_counts"]["top_bee_attr_ids"]
    assert dense["top_field_item_counts"]["top_bee_attr_ids"] >= wide["top_field_item_counts"]["top_bee_attr_ids"] * 3
