"""Tests: concept resolver — concern/goal ID normalization."""
from src.common.concept_resolver import resolve_concern_id, resolve_goal_id, concern_label


# --- Concern resolver ---

def test_concern_surface_to_stable_id():
    """Korean surface form → concern_dict stable key."""
    assert resolve_concern_id("건조함") == "concern_dryness"
    assert resolve_concern_id("여드름") == "concern_acne"
    assert resolve_concern_id("잔주름") == "concern_wrinkles"


def test_concern_stable_id_passthrough():
    """Already a concern_* key → passthrough."""
    assert resolve_concern_id("concern_dryness") == "concern_dryness"
    assert resolve_concern_id("concern_acne") == "concern_acne"


def test_concern_iri_prefix_stripped():
    """concept:Concern:건조함 → concern_dryness."""
    assert resolve_concern_id("concept:Concern:건조함") == "concern_dryness"
    assert resolve_concern_id("concept:Concern:concern_dryness") == "concern_dryness"


def test_concern_unknown_fallback():
    """Unknown concern → normalized text fallback."""
    result = resolve_concern_id("뭔가새로운고민")
    assert result == "뭔가새로운고민"  # normalize_text passthrough


def test_concern_label():
    """Concern ID → Korean label."""
    assert concern_label("concern_dryness") == "건조함"
    assert concern_label("concern_acne") == "여드름"


# --- Goal resolver ---

def test_goal_alias_canonical():
    """Goal alias → canonical goal."""
    assert resolve_goal_id("보습강화") == "보습"
    assert resolve_goal_id("수분보충") == "보습"
    assert resolve_goal_id("보습") == "보습"  # already canonical


def test_goal_iri_prefix_stripped():
    """concept:Goal:보습강화 → 보습."""
    assert resolve_goal_id("concept:Goal:보습강화") == "보습"
    assert resolve_goal_id("concept:Goal:보습") == "보습"


def test_goal_alias_tonup():
    """Brightening aliases → 톤업."""
    assert resolve_goal_id("톤업") == "톤업"
    assert resolve_goal_id("밝기개선") == "톤업"
    assert resolve_goal_id("브라이트닝") == "톤업"


def test_goal_unknown_fallback():
    """Unknown goal → normalized text."""
    result = resolve_goal_id("뭔가새로운목표")
    assert result == "뭔가새로운목표"


def test_goal_antiaging_alias():
    """Anti-aging aliases → 주름개선."""
    assert resolve_goal_id("안티에이징") == "주름개선"
    assert resolve_goal_id("주름개선") == "주름개선"
