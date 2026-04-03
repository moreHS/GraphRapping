"""Tests: texture taxonomy alignment between user adapter and config."""
import yaml
from pathlib import Path
from src.user.adapters.personal_agent_adapter import adapt_user_profile


def test_config_file_exists():
    config_path = Path("configs/texture_keyword_map.yaml")
    assert config_path.exists()


def test_config_has_required_keys():
    config = yaml.safe_load(Path("configs/texture_keyword_map.yaml").read_text())
    assert "texture_axis" in config
    assert "surface_to_keyword" in config
    assert isinstance(config["surface_to_keyword"], dict)


def test_user_texture_uses_config_keywords():
    profile = {
        "basic": {"skin_type": "건성"},
        "purchase_analysis": {"preferred_skincare_brand": [], "preferred_makeup_brand": [], "active_product_category": [], "preferred_repurchase_category": []},
        "chat": {
            "face": {"skin_concerns": [], "skincare_goals": [], "preferred_texture": ["젤", "크리미"]},
            "hair": {}, "scent": {"preferences": []},
            "ingredients": {"preferred": [], "avoid": [], "allergy": []},
        },
    }
    facts = adapt_user_profile("u1", profile)
    keywords = [f["concept_value"] for f in facts if f["predicate"] == "PREFERS_KEYWORD"]
    assert "GelLike" in keywords
    assert "CreamyLike" in keywords


def test_texture_axis_from_config():
    profile = {
        "basic": {"skin_type": "건성"},
        "purchase_analysis": {"preferred_skincare_brand": [], "preferred_makeup_brand": [], "active_product_category": [], "preferred_repurchase_category": []},
        "chat": {
            "face": {"skin_concerns": [], "skincare_goals": [], "preferred_texture": ["젤"]},
            "hair": {}, "scent": {"preferences": []},
            "ingredients": {"preferred": [], "avoid": [], "allergy": []},
        },
    }
    facts = adapt_user_profile("u1", profile)
    attrs = [f for f in facts if f["predicate"] == "PREFERS_BEE_ATTR"]
    texture_attrs = [f for f in attrs if f["concept_value"] == "Texture"]
    assert len(texture_attrs) == 1
