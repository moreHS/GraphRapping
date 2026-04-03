"""Tests: user loader input contract validation."""
import pytest
from src.loaders.user_loader import load_users_from_profiles


def test_raw_profile_rejected():
    """Raw 7-column profile should raise ValueError."""
    raw = {
        "user1": {
            "user_profile": {"sex_cd": "F", "age": 33},
            "skin_profile": {"skin_type": "건성"},
        }
    }
    with pytest.raises(ValueError, match="raw 7-column"):
        load_users_from_profiles(raw)


def test_missing_basic_rejected():
    """Profile without 'basic' key should raise ValueError."""
    bad = {"user1": {"purchase_analysis": {}, "chat": None}}
    with pytest.raises(ValueError, match="missing required 'basic'"):
        load_users_from_profiles(bad)


def test_normalized_accepted():
    """Valid normalized profile should work."""
    good = {
        "user1": {
            "basic": {"gender": "female", "age": "30대", "skin_type": "건성"},
            "purchase_analysis": {"preferred_skincare_brand": [], "preferred_makeup_brand": [], "active_product_category": [], "preferred_repurchase_category": []},
            "chat": None,
        }
    }
    result = load_users_from_profiles(good)
    assert result.user_count == 1
