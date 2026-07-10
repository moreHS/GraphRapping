"""Phase 6 Track A1 (fable_doc/plans/2026-07-10_phase6_service_frontend_query_understanding.md)
-- recommendation intent presets.

Covers:
  - configs/recommend_presets.yaml contract (3 presets, valid fields, feature
    keys are real Scorer features).
  - GET /api/recommend/presets (single source of truth for the frontend).
  - POST /api/recommend `preset` param: preset+weights conflict -> 400,
    unknown preset -> 400, preset_used response shape, mode override.
  - Preset weight materialization: weights_used fed to the scorer is the full
    (YAML base + weight_overrides) dict, not a partial pass-through.
  - C2 regression guard: a request that customizes shrinkage_k *without* also
    sending weights must still change scores (previously silently discarded
    by falling through to scorer.load_config()).
  - Differentiation: balanced/trusted/discovery must not all collapse to the
    same top-5 for at least one golden profile.

Uses the same dense_golden fixture + in-memory pipeline as
tests/test_expected_evidence_family_baseline.py and
tests/test_ranking_snapshot_regression.py, but loaded via
``src.web.state.load_demo_data`` (real ``/api/pipeline/run`` code path) and
driven through ``TestClient(server.app)`` so preset resolution is exercised
end-to-end (Pydantic validation, HTTP status codes, JSON response shape) --
not just the in-process function.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.common.config_loader import load_yaml
from src.rec.scorer import SCORING_FEATURE_KEYS
from src.web import server
from src.web.state import load_demo_data


ROOT = Path(__file__).resolve().parents[1]
DENSE_DIR = ROOT / "mockdata" / "dense_golden"

PRESET_KEYS = {"balanced", "trusted", "discovery"}
ALLOWED_MODES = {"explore", "strict", "compare"}
# user_dry_30f/all is a golden profile x tab combination with
# allowed_no_candidate=false (tests/fixtures/golden_expected_evidence.yaml),
# so it reliably produces candidates across all three presets.
GOLDEN_DIFFERENTIATION_USER = "user_dry_30f"


@pytest.fixture(scope="module")
def dense_golden_client() -> TestClient:
    """Load the dense_golden fixture into the real demo_state once per module.

    kg_mode is passed explicitly ("on", matching every other dense_golden test
    in this suite) rather than left to env resolution, so this fixture's
    result does not depend on fixture-ordering relative to the autouse
    GRAPHRAPPING_KG_MODE-clearing fixture in conftest.py.
    """
    product_records = json.loads((DENSE_DIR / "product_catalog_es.json").read_text(encoding="utf-8"))
    user_profiles = json.loads((DENSE_DIR / "user_profiles_normalized.json").read_text(encoding="utf-8"))
    load_demo_data(
        review_json_path=str(DENSE_DIR / "review_triples_raw.json"),
        product_es_records=product_records,
        user_profiles=user_profiles,
        max_reviews=5000,
        source="demo",
        review_format="relation",
        kg_mode="on",
    )
    return TestClient(server.app)


# ---------------------------------------------------------------------------
# configs/recommend_presets.yaml contract
# ---------------------------------------------------------------------------


def test_presets_yaml_defines_exactly_three_presets_with_valid_fields() -> None:
    data = load_yaml("recommend_presets.yaml")
    presets = data["presets"]
    assert set(presets) == PRESET_KEYS

    for key, preset in presets.items():
        assert set(preset) == {
            "label_ko", "description_ko", "mode", "shrinkage_k",
            "diversity_weight", "weight_overrides",
        }, f"{key}: unexpected/missing top-level fields"
        assert isinstance(preset["label_ko"], str) and preset["label_ko"]
        assert isinstance(preset["description_ko"], str) and preset["description_ko"]
        assert preset["mode"] in ALLOWED_MODES, f"{key}: invalid mode {preset['mode']!r}"
        assert float(preset["shrinkage_k"]) > 0
        assert 0.0 <= float(preset["diversity_weight"]) <= 1.0

        overrides = preset["weight_overrides"]
        assert isinstance(overrides, dict)
        unknown_features = set(overrides) - set(SCORING_FEATURE_KEYS)
        assert not unknown_features, f"{key}: weight_overrides has unknown feature keys {unknown_features}"
        for value in overrides.values():
            float(value)  # must be numeric


def test_balanced_preset_has_no_overrides_and_matches_request_defaults() -> None:
    """'balanced' is documented as "현행 기본값 그대로" (plan doc table row 1) --
    its mode/shrinkage_k/diversity_weight must match RecommendRequest's own
    field defaults exactly, and it must carry zero weight_overrides."""
    balanced = load_yaml("recommend_presets.yaml")["presets"]["balanced"]
    assert balanced["weight_overrides"] == {}
    assert balanced["mode"] == server.RecommendRequest.model_fields["mode"].default
    assert float(balanced["shrinkage_k"]) == server._DEFAULT_SHRINKAGE_K
    assert float(balanced["diversity_weight"]) == server.RecommendRequest.model_fields["diversity_weight"].default


# ---------------------------------------------------------------------------
# GET /api/recommend/presets
# ---------------------------------------------------------------------------


def test_get_presets_endpoint_returns_yaml_backed_list(dense_golden_client: TestClient) -> None:
    resp = dense_golden_client.get("/api/recommend/presets")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {item["key"] for item in items} == PRESET_KEYS
    for item in items:
        assert set(item) == {"key", "label_ko", "description_ko"}
        assert item["label_ko"]
        assert item["description_ko"]


# ---------------------------------------------------------------------------
# Validation: preset+weights conflict, unknown preset
# ---------------------------------------------------------------------------


def test_preset_and_weights_together_returns_400(dense_golden_client: TestClient) -> None:
    resp = dense_golden_client.post("/api/recommend", json={
        "user_id": "user_dry_30f", "preset": "trusted", "weights": {"keyword_match": 0.5},
    })
    assert resp.status_code == 400
    assert "preset" in resp.json()["detail"].lower()


def test_unknown_preset_returns_400(dense_golden_client: TestClient) -> None:
    resp = dense_golden_client.post("/api/recommend", json={"user_id": "user_dry_30f", "preset": "nonexistent"})
    assert resp.status_code == 400
    assert "nonexistent" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# preset_used response shape + mode override
# ---------------------------------------------------------------------------


def test_trusted_preset_smoke_returns_200_with_preset_used(dense_golden_client: TestClient) -> None:
    """Required smoke test: preset=trusted end-to-end through the real HTTP app
    (demo data loaded via the dense_golden_client fixture)."""
    resp = dense_golden_client.post("/api/recommend", json={
        "user_id": "user_dry_30f", "preset": "trusted", "top_k": 5,
    })
    assert resp.status_code == 200
    payload = resp.json()
    preset_used = payload["preset_used"]
    assert preset_used is not None
    assert preset_used["key"] == "trusted"
    assert preset_used["mode"] == "strict"
    assert preset_used["shrinkage_k"] == 40.0
    assert preset_used["diversity_weight"] == 0.05
    assert isinstance(preset_used["weight_overrides"], dict) and preset_used["weight_overrides"]
    # preset mode wins over the (unset, default) req.mode and is reflected
    # in the top-level response, not just preset_used.
    assert payload["mode"] == "strict"


def test_preset_used_is_null_when_no_preset_requested(dense_golden_client: TestClient) -> None:
    resp = dense_golden_client.post("/api/recommend", json={"user_id": "user_dry_30f", "top_k": 5})
    assert resp.status_code == 200
    assert resp.json()["preset_used"] is None


def test_preset_mode_overrides_request_mode_when_both_given(dense_golden_client: TestClient) -> None:
    """Plan §3 A1: "preset의 mode는 req.mode보다 우선(둘 다 오면 preset 승리)"."""
    resp = dense_golden_client.post("/api/recommend", json={
        "user_id": "user_dry_30f", "preset": "trusted", "mode": "compare", "top_k": 5,
    })
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["mode"] == "strict"  # preset wins, not the requested "compare"
    assert payload["preset_used"]["mode"] == "strict"


# ---------------------------------------------------------------------------
# Weight materialization: complete dict, not a partial pass-through
# ---------------------------------------------------------------------------


def test_preset_materializes_complete_weights_dict(dense_golden_client: TestClient) -> None:
    """weights_used (fed to the scorer) must be the FULL feature set (YAML
    base + overrides) -- the C2-adjacent materialization contract from the
    plan's cross-review C2 note, not a bare partial-dict pass-through."""
    base_features = load_yaml("scoring_weights.yaml")["features"]
    resp = dense_golden_client.post("/api/recommend", json={
        "user_id": "user_dry_30f", "preset": "trusted", "top_k": 5,
    })
    payload = resp.json()
    weights_used = payload["weights_used"]
    overrides = payload["preset_used"]["weight_overrides"]

    assert set(weights_used) == set(base_features)  # complete feature set
    for feature, value in overrides.items():
        assert weights_used[feature] == pytest.approx(value)
    for feature, value in base_features.items():
        if feature not in overrides:
            assert weights_used[feature] == pytest.approx(value)


# ---------------------------------------------------------------------------
# C2 fix: shrinkage_k-only customization (no weights) must actually apply
# ---------------------------------------------------------------------------


def test_shrinkage_k_only_customization_changes_scores_without_weights(
    dense_golden_client: TestClient,
) -> None:
    """Regression guard for the C2 bug: previously, sending shrinkage_k
    without also sending weights silently fell through to
    scorer.load_config(), which reloads shrinkage_k from YAML and discards
    the request value entirely -- moving only the shrinkage_k slider had no
    effect. This must now change scores while leaving weights_used untouched
    (isolating the regression check to shrinkage_k specifically)."""
    default_resp = dense_golden_client.post("/api/recommend", json={
        "user_id": "user_dry_30f", "category_group": "all", "top_k": 5,
    })
    customized_resp = dense_golden_client.post("/api/recommend", json={
        "user_id": "user_dry_30f", "category_group": "all", "top_k": 5, "shrinkage_k": 30.0,
    })
    assert default_resp.status_code == 200
    assert customized_resp.status_code == 200
    default_payload = default_resp.json()
    customized_payload = customized_resp.json()

    assert customized_payload["weights_used"] == default_payload["weights_used"], (
        "only shrinkage_k was customized -- feature weights must be unchanged"
    )

    default_scores = [(r["product_id"], r["final_score"]) for r in default_payload["results"]]
    customized_scores = [(r["product_id"], r["final_score"]) for r in customized_payload["results"]]
    assert default_scores, "golden user must produce candidates for this regression check to be meaningful"
    assert customized_scores != default_scores, (
        "shrinkage_k=30 (no weights) had no effect -- C2 regression: the "
        "scorer silently discarded the requested shrinkage_k"
    )


def test_pure_default_request_unaffected_by_preset_feature(dense_golden_client: TestClient) -> None:
    """A request with no preset/weights/shrinkage_k customization must stay on
    the exact pre-existing scorer.load_config() path (existing tests/
    snapshots depend on its precise behaviour, including brand_confidence)."""
    base = load_yaml("scoring_weights.yaml")
    resp = dense_golden_client.post("/api/recommend", json={
        "user_id": "user_dry_30f", "category_group": "all", "top_k": 5,
    })
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["preset_used"] is None
    assert payload["weights_used"] == pytest.approx(base["features"])


# ---------------------------------------------------------------------------
# Differentiation: the three presets must not all collapse to the same top-5
# ---------------------------------------------------------------------------


def test_presets_produce_different_top5_for_a_golden_user(
    dense_golden_client: TestClient,
) -> None:
    """Differentiation (F3-strengthened): rather than the weak `len(orderings)>1`
    (any single reorder passes), assert each intent preset is explicitly distinct
    from balanced for user_dry_30f/all -- (a) discovery differs from balanced, and
    (b) trusted's top-5 differs by at least TWO members (not just a reshuffle or a
    single swap), which is the F3 tuning target."""
    top5_by_preset: dict[str, list[str]] = {}
    for preset_key in ("balanced", "trusted", "discovery"):
        resp = dense_golden_client.post("/api/recommend", json={
            "user_id": GOLDEN_DIFFERENTIATION_USER, "category_group": "all", "top_k": 5, "preset": preset_key,
        })
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert results, f"preset={preset_key} produced no candidates for {GOLDEN_DIFFERENTIATION_USER}"
        top5_by_preset[preset_key] = [r["product_id"] for r in results]

    balanced = top5_by_preset["balanced"]
    trusted = top5_by_preset["trusted"]
    discovery = top5_by_preset["discovery"]

    # (a) discovery must not be identical to balanced (ordered top-5 differs).
    assert discovery != balanced, (
        f"discovery produced balanced's exact top-5 for {GOLDEN_DIFFERENTIATION_USER}: {balanced}"
    )

    # (b) trusted must swap in >= 2 different products vs balanced -- the F3 target
    # (before re-tuning, trusted differed by only one member).
    trusted_only = set(trusted) - set(balanced)
    balanced_only = set(balanced) - set(trusted)
    assert len(trusted_only) >= 2 and len(balanced_only) >= 2, (
        f"trusted top-5 must differ from balanced by >= 2 members for "
        f"{GOLDEN_DIFFERENTIATION_USER}: balanced={balanced} trusted={trusted} "
        f"(trusted_only={sorted(trusted_only)}, balanced_only={sorted(balanced_only)})"
    )
