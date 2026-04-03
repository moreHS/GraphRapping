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


def test_keyword_normalizer_resolves_texture():
    """Review-side keyword normalizer must resolve texture surface forms to *Like IDs."""
    from src.normalize.keyword_normalizer import KeywordNormalizer
    kn = KeywordNormalizer()
    kn.load()
    # These surface forms must resolve to texture canonical keywords
    expected = {"젤": "GelLike", "크리미": "CreamyLike", "워터리": "WateryLike",
                "밀크": "MilkLike", "리치크림": "RichCreamLike"}
    for surface, expected_id in expected.items():
        result = kn.resolve(surface)
        assert result, f"Surface '{surface}' not found in keyword normalizer"
        ids = [r["keyword_id"] for r in result]
        assert expected_id in ids, \
            f"Surface '{surface}' resolved to {ids}, expected {expected_id}"


def test_texture_ids_converge_across_configs():
    """Texture canonical IDs in keyword_surface_map must match texture_keyword_map."""
    from src.common.config_loader import load_yaml
    texture_cfg = load_yaml("texture_keyword_map.yaml")
    keyword_cfg = load_yaml("keyword_surface_map.yaml")
    # Collect all *Like IDs from texture_keyword_map
    texture_ids = set(texture_cfg.get("surface_to_keyword", {}).values())
    # Collect all keyword_ids from keyword_surface_map
    surface_ids = set()
    for entries in keyword_cfg.values():
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict):
                    surface_ids.add(entry.get("keyword_id", ""))
    # Every texture canonical ID must appear in keyword_surface_map
    missing = texture_ids - surface_ids
    assert not missing, f"Texture IDs missing from keyword_surface_map: {missing}"


def test_bee_normalizer_finds_texture_keyword():
    """BEE normalizer keyword extraction should find texture surface forms."""
    from src.common.config_loader import load_yaml
    keyword_cfg = load_yaml("keyword_surface_map.yaml")
    # Verify texture surfaces exist in keyword_surface_map keys
    texture_surfaces = {"젤", "크리미", "워터리", "밀크", "리치크림", "가벼운로션"}
    ksm_keys = set(keyword_cfg.keys())
    missing = texture_surfaces - ksm_keys
    assert not missing, f"Texture surfaces missing from keyword_surface_map keys: {missing}"
