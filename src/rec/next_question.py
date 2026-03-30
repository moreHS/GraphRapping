"""
Next-best-question generator: uncertainty axis based.

Asks about the single most uncertain preference axis to narrow recommendations.
"""

from __future__ import annotations

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


def generate_next_question(
    user_profile: dict[str, Any],
    scored_products: list[dict[str, Any]] | None = None,
) -> NextQuestion | None:
    """Generate the single best next question based on uncertainty.

    Picks the axis with the least user preference data.
    """
    # Check which axes have data
    axes_with_data = set()

    if _has_data(user_profile, "preferred_keyword_ids"):
        axes_with_data.add("texture_vs_moisture")
    if _has_data(user_profile, "preferred_bee_attr_ids"):
        axes_with_data.add("coverage_vs_natural")
    if _has_data(user_profile, "preferred_context_ids"):
        axes_with_data.add("time_priority")
    if _has_data(user_profile, "concern_ids"):
        axes_with_data.add("concern_priority")

    # Pick first axis WITHOUT data
    priority_order = [
        "concern_priority",
        "texture_vs_moisture",
        "coverage_vs_natural",
        "scent_sensitivity",
        "tool_preference",
        "time_priority",
    ]

    for axis in priority_order:
        if axis not in axes_with_data:
            return _QUESTION_TEMPLATES.get(axis)

    return None


def _has_data(profile: dict, key: str) -> bool:
    val = profile.get(key, [])
    return bool(val) and len(val) > 0
