"""Tests for source data loaders."""

import json
import tempfile
import pytest
from pathlib import Path

from src.loaders.relation_loader import load_reviews_from_json
from src.loaders.product_loader import load_products_from_es_records
from src.loaders.user_loader import load_users_from_profiles


# --- Relation Loader ---

SAMPLE_REVIEW_JSON = [
    {
        "brnd_nm": "LANEIGE",
        "clct_site_nm": "Sephora",
        "prod_nm": "Lip Sleeping Mask",
        "text": "Love this product so much",
        "drup_dt": "2025-06-15",
        "ner": [
            {"word": "Review Target", "entity_group": "PRD", "start": None, "end": None, "sentiment": "중립"},
        ],
        "bee": [
            {"word": "Love this product", "entity_group": "효과", "start": 0, "end": 17, "sentiment": "긍정"},
        ],
        "relation": [
            {
                "subject": {"word": "Review Target", "entity_group": "PRD"},
                "object": {"word": "Love this product", "entity_group": "효과"},
                "relation": "has_attribute",
                "source_type": "NER-BeE",
            }
        ],
    },
    {
        "brnd_nm": "CLIO",
        "clct_site_nm": "화해",
        "prod_nm": "Kill Cover Cushion",
        "text": "착붙하고 좋아요",
        "drup_dt": "2025-07-01",
        "ner": [],
        "bee": [
            {"word": "착붙하고 좋아요", "entity_group": "밀착력", "start": 0, "end": 8, "sentiment": "긍정"},
        ],
        # No relation[] → BEE-only mode
    },
]


class TestRelationLoader:
    def test_load_basic(self, tmp_path):
        json_file = tmp_path / "reviews.json"
        json_file.write_text(json.dumps(SAMPLE_REVIEW_JSON), encoding="utf-8")

        reviews = load_reviews_from_json(str(json_file))
        assert len(reviews) == 2

    def test_field_mapping(self, tmp_path):
        json_file = tmp_path / "reviews.json"
        json_file.write_text(json.dumps(SAMPLE_REVIEW_JSON), encoding="utf-8")

        reviews = load_reviews_from_json(str(json_file))
        r = reviews[0]
        assert r.brnd_nm == "LANEIGE"
        assert r.prod_nm == "Lip Sleeping Mask"
        assert r.created_at == "2025-06-15"  # drup_dt → created_at
        assert r.source_row_num == "0"       # row index

    def test_relation_missing_graceful(self, tmp_path):
        """Review without relation[] should load with empty relation list."""
        json_file = tmp_path / "reviews.json"
        json_file.write_text(json.dumps(SAMPLE_REVIEW_JSON), encoding="utf-8")

        reviews = load_reviews_from_json(str(json_file))
        assert len(reviews[1].relation) == 0  # CLIO review has no relation

    def test_max_count(self, tmp_path):
        json_file = tmp_path / "reviews.json"
        json_file.write_text(json.dumps(SAMPLE_REVIEW_JSON), encoding="utf-8")

        reviews = load_reviews_from_json(str(json_file), max_count=1)
        assert len(reviews) == 1

    def test_source_row_num_unique(self, tmp_path):
        json_file = tmp_path / "reviews.json"
        json_file.write_text(json.dumps(SAMPLE_REVIEW_JSON), encoding="utf-8")

        reviews = load_reviews_from_json(str(json_file))
        assert reviews[0].source_row_num == "0"
        assert reviews[1].source_row_num == "1"


# --- Product Loader ---

SAMPLE_ES_RECORDS = [
    {
        "ONLINE_PROD_SERIAL_NUMBER": "P001",
        "prd_nm": "립 슬리핑 마스크",
        "BRAND_NAME": "라네즈",
        "CTGR_SS_NAME": "립케어",
        "SALE_STATUS": "판매중",
    },
    {
        "ONLINE_PROD_SERIAL_NUMBER": "P002",
        "prd_nm": "킬커버 쿠션",
        "BRAND_NAME": "클리오",
        "CTGR_SS_NAME": "쿠션",
        "SALE_STATUS": "판매중지",  # should be filtered
    },
]


class TestProductLoader:
    def test_load_filters_sale_status(self):
        result = load_products_from_es_records(SAMPLE_ES_RECORDS)
        assert result.product_count == 1  # only 판매중
        assert "P001" in result.product_masters

    def test_field_mapping(self):
        result = load_products_from_es_records(SAMPLE_ES_RECORDS)
        master = result.product_masters["P001"]
        assert master["product_name"] == "립 슬리핑 마스크"
        assert master["brand_name"] == "라네즈"
        assert master["category_name"] == "립케어"

    def test_product_index_built(self):
        result = load_products_from_es_records(SAMPLE_ES_RECORDS)
        assert result.product_index is not None
        assert result.product_index.exact  # has entries

    def test_concept_links_created(self):
        result = load_products_from_es_records(SAMPLE_ES_RECORDS)
        links = result.concept_links.get("product:P001", [])
        assert any(l["link_type"] == "HAS_BRAND" for l in links)
        assert any(l["link_type"] == "IN_CATEGORY" for l in links)


# --- User Loader ---

SAMPLE_USER_PROFILES = {
    "u_1001": {
        "basic": {"skin_type": "건성", "age": "30s", "gender": "female"},
        "purchase_analysis": {"preferred_skincare_brand": ["라네즈"]},
        "chat": {
            "face": {"skin_concerns": ["건조함"], "skincare_goals": ["보습"]},
            "ingredients": {"preferred": ["세라마이드"], "avoid": ["에탄올"], "allergy": []},
        },
    },
}


class TestUserLoader:
    def test_load_basic(self):
        result = load_users_from_profiles(SAMPLE_USER_PROFILES)
        assert result.user_count == 1
        assert "u_1001" in result.user_masters

    def test_user_master_fields(self):
        result = load_users_from_profiles(SAMPLE_USER_PROFILES)
        master = result.user_masters["u_1001"]
        assert master["skin_type"] == "건성"
        assert master["gender"] == "female"

    def test_adapted_facts_generated(self):
        result = load_users_from_profiles(SAMPLE_USER_PROFILES)
        facts = result.user_adapted_facts["u_1001"]
        assert len(facts) > 0
        predicates = {f["predicate"] for f in facts}
        assert "HAS_SKIN_TYPE" in predicates
        assert "PREFERS_BRAND" in predicates
        assert "AVOIDS_INGREDIENT" in predicates
