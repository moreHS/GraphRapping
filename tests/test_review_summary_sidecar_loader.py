from __future__ import annotations

from src.loaders.review_summary_sidecar_loader import (
    build_lookup_products,
    build_sidecar_rows,
    choose_review_summary_match,
    derive_review_summary_category,
    group_docs_by_product_id,
)


def _hit(doc_id: str, product_id: str, category: str, *, review_cnt: int = 1) -> dict:
    return {
        "_id": doc_id,
        "_source": {
            "product_id": product_id,
            "category": category,
            "review_cnt": review_cnt,
            "@timestamp": "2026-06-17T00:00:00Z",
            "An_date": "2026-06-17",
            "summary": f"{category} summary",
            "prd_nm": "소스상품명",
        },
    }


def test_derive_review_summary_category_from_own_channel() -> None:
    assert derive_review_summary_category("own", "031") == "own-apmall"
    assert derive_review_summary_category("own", "036") == "own-innisfree"
    assert derive_review_summary_category("external", "036") is None
    assert derive_review_summary_category("own", "999") is None


def test_build_lookup_products_excludes_collision_rows() -> None:
    result = build_lookup_products([
        {
            "product_id": "P1",
            "source_product_id": "4077",
            "source_channel": "036",
            "source_key_type": "chn_prd_cd",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
        {
            "product_id": "35119",
            "source_product_id": "source_key_collision:35119",
            "source_channel": None,
            "source_key_type": None,
            "source_truth_quality": "SOURCE_KEY_COLLISION",
        },
    ])

    assert result.product_count == 2
    assert result.collision_excluded == 1
    assert len(result.products) == 1
    assert result.products[0]["review_summary_category"] == "own-innisfree"


def test_exact_category_match_preserves_raw_docs_and_normalized_summary() -> None:
    product = build_lookup_products([
        {
            "product_id": "P1",
            "source_product_id": "4077",
            "source_channel": "036",
            "source_key_type": "chn_prd_cd",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
    ]).products[0]
    rows, manifest = build_sidecar_rows(
        [product],
        [
            _hit("wrong", "4077", "own-apmall", review_cnt=5),
            _hit("right", "4077", "own-innisfree", review_cnt=10),
        ],
        [_hit("short-right", "4077", "own-innisfree")],
        product_count=1,
    )

    row = rows[0]
    assert row["match_status"] == "exact_category"
    assert row["long_doc_id"] == "right"
    assert row["short_doc_id"] == "short-right"
    assert row["long_doc"]["_source"]["summary"] == "own-innisfree summary"
    assert row["normalized_summary"]["long"]["prd_nm"] == "소스상품명"
    assert row["candidate_metadata"]["long"]["candidate_count"] == 2
    assert manifest["matched"] == 1
    assert manifest["exact_category"] == 1


def test_category_mismatch_is_ambiguous_not_auto_attached() -> None:
    product = build_lookup_products([
        {
            "product_id": "P1",
            "source_product_id": "4077",
            "source_channel": "036",
            "source_key_type": "chn_prd_cd",
            "source_truth_quality": "SOURCE_GROUNDED",
        },
    ]).products[0]
    rows, manifest = build_sidecar_rows(
        [product],
        [_hit("wrong", "4077", "own-apmall")],
        [],
        product_count=1,
    )

    row = rows[0]
    assert row["match_status"] == "product_id_ambiguous_skipped"
    assert row["long_doc"] is None
    assert row["normalized_summary"] is None
    assert row["candidate_metadata"]["long"]["reason"] == "source_product_id_found_but_expected_category_missing"
    assert manifest["ambiguous_skipped"] == 1
    assert manifest["matched"] == 0


def test_category_hint_absent_is_not_auto_attached() -> None:
    product = {
        "product_id": "P1",
        "source_product_id": "4077",
        "source_channel": None,
        "source_key_type": "unknown",
        "review_source": "own",
        "review_summary_category": None,
    }
    grouped = group_docs_by_product_id([
        _hit("a", "4077", "own-apmall", review_cnt=1),
        _hit("b", "4077", "own-apmall", review_cnt=2),
    ])

    result = choose_review_summary_match(product, grouped)

    assert result.status == "product_id_ambiguous_skipped"
    assert result.doc is None
    assert result.reason == "missing_or_unmatched_review_summary_category"


def test_product_id_only_match_is_not_attached_and_not_found_path() -> None:
    product = {
        "product_id": "P1",
        "source_product_id": "4077",
        "source_channel": None,
        "source_key_type": "unknown",
        "review_source": None,
        "review_summary_category": None,
    }

    ambiguous = choose_review_summary_match(product, group_docs_by_product_id([_hit("only", "4077", "extn-olive")]))
    missing = choose_review_summary_match(product, group_docs_by_product_id([]))

    assert ambiguous.status == "product_id_ambiguous_skipped"
    assert ambiguous.doc is None
    assert missing.status == "not_found"
