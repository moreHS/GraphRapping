"""Tests: user mock contract — normalized format is the official loader input."""
import json
from pathlib import Path

def test_normalized_has_required_keys():
    """Normalized profile must have basic, purchase_analysis, chat keys."""
    data = json.loads(Path("mockdata/user_profiles_normalized.json").read_text(encoding="utf-8"))
    for uid, profile in data.items():
        assert "basic" in profile, f"{uid} missing basic"
        assert "purchase_analysis" in profile, f"{uid} missing purchase_analysis"
        assert "chat" in profile or profile.get("chat") is None, f"{uid} missing chat"

def test_normalized_basic_fields():
    """basic group must have gender, age (as age_band), skin_type, skin_tone."""
    data = json.loads(Path("mockdata/user_profiles_normalized.json").read_text(encoding="utf-8"))
    for uid, profile in data.items():
        basic = profile["basic"]
        assert "gender" in basic, f"{uid} basic missing gender"
        assert "age" in basic or "age_band" in basic, f"{uid} basic missing age/age_band"
        assert "skin_type" in basic, f"{uid} basic missing skin_type"

def test_raw_is_not_loader_compatible():
    """Raw profile should NOT pass as valid loader input (missing 'basic' key at top level)."""
    data = json.loads(Path("mockdata/user_profiles_raw.json").read_text(encoding="utf-8"))
    for uid, profile in data.items():
        # Raw has 7-column keys, not 3-group
        assert "user_profile" in profile, f"{uid} expected raw 7-column format"
        assert "basic" not in profile, f"{uid} raw should not have 'basic' (that's normalized)"

def test_loader_accepts_normalized():
    """load_users_from_profiles should succeed with normalized mock data."""
    from src.loaders.user_loader import load_users_from_profiles
    data = json.loads(Path("mockdata/user_profiles_normalized.json").read_text(encoding="utf-8"))
    result = load_users_from_profiles(data)
    assert result.user_count >= 3  # 50 from personal-agent sync, at least seed 3
    assert "user_dry_30f" in result.user_masters
