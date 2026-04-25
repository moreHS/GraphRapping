import re
from pathlib import Path

from src.common.config_loader import load_yaml
from src.rec.explainer import _EDGE_MAP, _concept_to_feature, explain
from src.rec.scorer import SCORING_FEATURE_KEYS, Scorer


def _frontend_default_weights() -> dict[str, float]:
    js = Path("src/static/app.js").read_text(encoding="utf-8")
    match = re.search(r"const DEFAULT_WEIGHTS = \{(?P<body>.*?)\};", js, re.S)
    assert match, "DEFAULT_WEIGHTS block not found"
    weights = {}
    for key, value in re.findall(r"([A-Za-z0-9_]+):\s*([0-9.]+)", match.group("body")):
        weights[key] = float(value)
    return weights


def test_scoring_yaml_matches_backend_feature_contract():
    yaml_features = load_yaml("scoring_weights.yaml")["features"]

    assert set(yaml_features) == set(SCORING_FEATURE_KEYS)
    assert "goal_fit_review_signal" not in yaml_features


def test_goal_review_dead_path_removed_from_explainer_and_frontend():
    assert "goal_review" not in _EDGE_MAP
    assert _concept_to_feature("goal_review") == ""
    assert "goal_fit_review_signal" not in _frontend_default_weights()


def test_frontend_default_weights_match_yaml_features():
    yaml_features = load_yaml("scoring_weights.yaml")["features"]
    frontend_weights = _frontend_default_weights()

    assert frontend_weights == yaml_features


def test_negative_contribution_is_retained_and_explainable():
    user = {"owned_family_ids": [{"id": "FAM001"}]}
    product = {"product_id": "P2", "variant_family_id": "FAM001", "review_count_all": 50}
    scorer = Scorer()
    scorer.load_from_dict({"owned_family_penalty": 1.0})

    scored = scorer.score(user, product, ["owned_family:FAM001"])
    assert scored.feature_contributions["owned_family_penalty"] < 0

    explanation = explain(scored, ["owned_family:FAM001"])
    assert explanation.paths
    assert explanation.paths[0].contribution < 0
