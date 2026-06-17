"""
Next-best-question generator: uncertainty axis based.

Asks about the single most uncertain preference axis to narrow recommendations.

P4-3 (Wave 3.3): when `scored_products` is provided, axes with mappable
scorer features are ranked by score variance — low variance = low information
gain across the candidate set = high "ask first" priority. Axes without
mappable features (coverage_vs_natural, scent_sensitivity) only surface via
the legacy data-absence fallback.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any


@dataclass
class NextQuestion:
    question_ko: str
    uncertainty_axis: str
    options: list[str] | None = None


# Uncertainty axes with their questions
_QUESTION_TEMPLATES = {
    "texture_vs_moisture": NextQuestion(
        question_ko="촉촉함이 더 중요하세요, 아니면 가벼운 텍스처가 더 중요하세요?",
        uncertainty_axis="texture_vs_moisture",
        options=["촉촉함", "가벼운 텍스처"],
    ),
    "coverage_vs_natural": NextQuestion(
        question_ko="커버력을 원하세요, 아니면 자연스러운 피부 표현을 원하세요?",
        uncertainty_axis="coverage_vs_natural",
        options=["커버력", "자연스러운 표현"],
    ),
    "scent_sensitivity": NextQuestion(
        question_ko="향이 있는 제품도 괜찮으세요?",
        uncertainty_axis="scent_sensitivity",
        options=["향 있어도 괜찮아요", "무향 선호"],
    ),
    "tool_preference": NextQuestion(
        question_ko="퍼프/브러시 어떤 걸로 바르세요?",
        uncertainty_axis="tool_preference",
        options=["퍼프", "브러시", "손"],
    ),
    "time_priority": NextQuestion(
        question_ko="아침 루틴용이세요, 지속력이 더 중요하세요?",
        uncertainty_axis="time_priority",
        options=["아침 빠른 루틴", "지속력 우선"],
    ),
    "concern_priority": NextQuestion(
        question_ko="가장 신경 쓰이는 피부 고민이 뭐예요?",
        uncertainty_axis="concern_priority",
    ),
}


# P4-3 priority order — used both as histogram tie-break and legacy fallback.
_PRIORITY_ORDER = (
    "concern_priority",
    "texture_vs_moisture",
    "coverage_vs_natural",
    "scent_sensitivity",
    "tool_preference",
    "time_priority",
)

# Axis → scorer.feature_contributions keys that summarize the axis. Axes
# omitted here have no scorer signal and fall back to legacy data-absence.
_AXIS_FEATURES: dict[str, tuple[str, ...]] = {
    "concern_priority": ("concern_fit", "concern_bridge_fit"),
    "texture_vs_moisture": ("keyword_match", "residual_bee_attr_match"),
    "time_priority": ("context_match",),
    "tool_preference": ("tool_alignment",),
    # coverage_vs_natural: no per-keyword-family feature; legacy fallback only.
    # scent_sensitivity: no scorer feature; legacy fallback only.
}

# Below this mean axis-score, the candidate set has effectively no signal on
# that axis — variance is meaningless. Drop the axis from histogram candidates.
_AXIS_SCORE_EPSILON = 0.01


def generate_next_question(
    user_profile: dict[str, Any],
    scored_products: list[dict[str, Any]] | None = None,
) -> NextQuestion | None:
    """Generate the single best next question based on uncertainty.

    Histogram mode (`scored_products` provided):
      For each histogram-eligible axis, sum its mapped features per product →
      compute variance across products. Drop axes whose mean score is below
      ε (no real signal). Pick the lowest-variance axis (tightest spread =
      least discriminating info → highest "ask" value). Tie → priority order.
      No candidates → fall back to legacy.

    Legacy mode (`scored_products` is None or yields no histogram candidate):
      Pick the first axis in priority order that has no user preference data.
    """
    if scored_products:
        winner = _select_axis_by_variance(scored_products)
        if winner is not None:
            return _QUESTION_TEMPLATES.get(winner)

    # Legacy: data-absence fallback (also handles axes without scorer features).
    axes_with_data: set[str] = set()
    if _has_data(user_profile, "preferred_keyword_ids"):
        axes_with_data.add("texture_vs_moisture")
    if _has_data(user_profile, "preferred_bee_attr_ids"):
        axes_with_data.add("coverage_vs_natural")
    if _has_data(user_profile, "preferred_context_ids"):
        axes_with_data.add("time_priority")
    if _has_data(user_profile, "concern_ids"):
        axes_with_data.add("concern_priority")

    for axis in _PRIORITY_ORDER:
        if axis not in axes_with_data:
            return _QUESTION_TEMPLATES.get(axis)
    return None


def _select_axis_by_variance(scored_products: list[dict[str, Any]]) -> str | None:
    """Return the lowest-variance histogram-eligible axis above the epsilon floor.

    Tie-break by `_PRIORITY_ORDER`. Returns None if no axis qualifies — caller
    falls back to legacy data-absence logic.
    """
    if not scored_products:
        return None

    axis_scores: dict[str, list[float]] = {}
    for axis, features in _AXIS_FEATURES.items():
        scores = []
        for product in scored_products:
            contributions = product.get("feature_contributions") or {}
            score = sum(float(contributions.get(f, 0.0)) for f in features)
            scores.append(score)
        axis_scores[axis] = scores

    # Variance candidates: mean > ε and ≥2 products (variance needs N≥2).
    candidates: list[tuple[str, float]] = []
    for axis, scores in axis_scores.items():
        if len(scores) < 2:
            continue
        mean = statistics.fmean(scores)
        if mean <= _AXIS_SCORE_EPSILON:
            continue
        var = statistics.pvariance(scores)
        candidates.append((axis, var))

    if not candidates:
        return None

    # Lowest variance wins; tie → earliest in priority order.
    priority_index = {axis: i for i, axis in enumerate(_PRIORITY_ORDER)}
    candidates.sort(key=lambda av: (av[1], priority_index.get(av[0], 999)))
    return candidates[0][0]


def _has_data(profile: dict, key: str) -> bool:
    val = profile.get(key, [])
    return bool(val) and len(val) > 0
