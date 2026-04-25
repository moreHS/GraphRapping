import inspect

from src.db.repos import mart_repo


def test_serving_product_repo_writes_family_display_fields():
    src = inspect.getsource(mart_repo.upsert_serving_product_profile)

    assert "variant_family_id" in src
    assert "representative_product_name" in src


def test_serving_user_repo_writes_purchase_family_behavior_fields():
    src = inspect.getsource(mart_repo.upsert_serving_user_profile)

    for field in (
        "recent_purchase_brand_ids",
        "repurchase_brand_ids",
        "repurchase_category_ids",
        "owned_product_ids",
        "owned_family_ids",
        "repurchased_family_ids",
    ):
        assert field in src
