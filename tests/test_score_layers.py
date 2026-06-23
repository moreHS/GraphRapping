from src.rec.scorer import Scorer


def test_score_layers_group_feature_contributions_by_evidence_family():
    scorer = Scorer()
    scorer.load_from_dict(
        {
            "brand_match_conf_weighted": 0.1,
            "keyword_match": 0.2,
            "purchase_loyalty_score": 0.3,
            "source_popularity_score": 0.4,
        },
        shrinkage_k=10,
    )
    user = {
        "repurchase_brand_ids": [{"id": "concept:Brand:brand_a"}],
    }
    product = {
        "product_id": "P1",
        "brand_id": "brand_a",
        "review_count_all": 100,
        "source_review_count_6m": 1000,
    }

    scored = scorer.score(
        user,
        product,
        ["brand:concept:Brand:brand_a", "keyword:kw", "repurchase_brand:concept:Brand:brand_a"],
    )

    assert scored.score_layers["master_truth_score"] > 0
    assert scored.score_layers["review_graph_score"] > 0
    assert scored.score_layers["purchase_behavior_score"] > 0
    assert scored.score_layers["source_trust_score"] > 0


def test_freshness_is_product_activity_not_review_relation_layer():
    scorer = Scorer()
    scorer.load_from_dict({"freshness_boost": 0.4}, shrinkage_k=10)

    scored = scorer.score(
        {},
        {"product_id": "P1", "review_count_all": 100, "review_count_30d": 20},
        [],
    )

    assert scored.score_layers["review_graph_score"] == 0
    assert scored.score_layers["product_activity_score"] > 0


def test_active_category_affinity_is_profile_fit_not_master_truth():
    scorer = Scorer()
    scorer.load_from_dict(
        {
            "category_affinity": 0.05,
            "active_category_affinity": 0.02,
        },
        shrinkage_k=10,
    )

    scored = scorer.score(
        {},
        {"product_id": "P1", "review_count_all": 100},
        ["active_category:concept:Category:skincare"],
    )

    assert scored.feature_contributions["active_category_affinity"] > 0
    assert "category_affinity" not in scored.feature_contributions
    assert scored.score_layers["master_truth_score"] == 0
    assert scored.score_layers["profile_fit_score"] > 0


def test_catalog_keyword_and_repurchase_category_layers_are_separate():
    scorer = Scorer()
    scorer.load_from_dict(
        {
            "catalog_keyword_match": 0.04,
            "repurchase_category_affinity": 0.03,
        },
        shrinkage_k=10,
    )

    scored = scorer.score(
        {},
        {"product_id": "P1", "review_count_all": 100},
        ["catalog_keyword:concept:Keyword:틴트", "repurchase_category:concept:Category:틴트"],
    )

    assert scored.score_layers["master_truth_score"] > 0
    assert scored.score_layers["purchase_behavior_score"] > 0
