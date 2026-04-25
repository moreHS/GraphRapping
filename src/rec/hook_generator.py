"""
Hook generator: creates discovery/consideration/conversion angle copy.

Based on explanation paths and product signals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.rec.explainer import Explanation


@dataclass
class HookCopy:
    discovery: str      # "요즘 찾는 사용감에 가까운 제품"
    consideration: str  # "건조한 날에도 비교적 부담이 적은 편"
    conversion: str     # "평소 쓰는 루틴과 잘 맞는 편"


def generate_hooks(
    explanation: Explanation,
    product_profile: dict[str, Any] | None = None,
) -> HookCopy:
    """Generate hook copy from explanation paths.

    Three angles:
      discovery: attracts curiosity (keyword/attr based)
      consideration: reduces concern (concern/context based)
      conversion: confirms fit (brand/category/routine based)
    """
    paths = explanation.paths

    discovery_parts = []
    consideration_parts = []
    conversion_parts = []

    for p in paths:
        if p.concept_type in ("keyword", "bee_attr"):
            discovery_parts.append(p.concept_id)
        elif p.concept_type in ("concern", "context"):
            consideration_parts.append(p.concept_id)
        elif p.concept_type in ("brand", "category", "goal"):
            conversion_parts.append(p.concept_id)

    discovery = _build_discovery(discovery_parts)
    consideration = _build_consideration(consideration_parts)
    conversion = _build_conversion(conversion_parts)

    return HookCopy(
        discovery=discovery,
        consideration=consideration,
        conversion=conversion,
    )


def _build_discovery(concepts: list[str]) -> str:
    if not concepts:
        return "새로운 제품을 만나보세요"
    joined = ", ".join(concepts[:2])
    return f"{joined} 사용감에 가까운 제품이에요"


def _build_consideration(concepts: list[str]) -> str:
    if not concepts:
        return "부담 없이 시도해볼 수 있어요"
    joined = ", ".join(concepts[:2])
    return f"{joined}에도 비교적 부담이 적은 편이에요"


def _build_conversion(concepts: list[str]) -> str:
    if not concepts:
        return "평소 루틴과 잘 어울려요"
    joined = ", ".join(concepts[:2])
    return f"평소 선호하는 {joined}과(와) 잘 맞는 편이에요"
