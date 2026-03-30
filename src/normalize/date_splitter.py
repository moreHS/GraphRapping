"""
DATE splitter: DATE mention → TemporalContext | Frequency | Duration | AbsoluteDate

Rule-based initial implementation. Expandable via date_context_dict.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.common.enums import DateSubType
from src.common.text_normalize import normalize_text

# Load YAML config for extensible context terms
_YAML_LOADED = False
_YAML_DAY_PARTS: set[str] = set()
_YAML_ROUTINE_STEPS: set[str] = set()
_YAML_SEASONS: set[str] = set()


def _load_yaml_config() -> None:
    """Load date_context_dict.yaml once. YAML terms extend hardcoded rules."""
    global _YAML_LOADED, _YAML_DAY_PARTS, _YAML_ROUTINE_STEPS, _YAML_SEASONS
    if _YAML_LOADED:
        return
    try:
        from src.common.config_loader import load_yaml
        data = load_yaml("date_context_dict.yaml")
        tc = data.get("temporal_context", {})
        _YAML_DAY_PARTS = set(tc.get("day_part", []))
        _YAML_ROUTINE_STEPS = set(tc.get("routine_step", []))
        _YAML_SEASONS = set(tc.get("season", []))
    except Exception:
        pass  # fallback to hardcoded only
    _YAML_LOADED = True


@dataclass
class DateSplitResult:
    kind: DateSubType
    value: str
    context_type: str | None = None  # day_part|routine_step|season|time_of_day|...


# ---------------------------------------------------------------------------
# Pattern-based rules (Korean-focused, extensible)
# ---------------------------------------------------------------------------

# Frequency patterns
_FREQUENCY_PATTERNS = [
    re.compile(r"(?:매일|매번|하루에?\s*\d+\s*번|하루\s*\d+\s*회|주\s*\d+\s*회|주\s*\d+\s*번|일\s*\d+\s*회)", re.IGNORECASE),
    re.compile(r"(?:daily|every\s*day|once\s*a\s*day|\d+\s*times?\s*(?:a|per)\s*(?:day|week))", re.IGNORECASE),
]

# Duration patterns
_DURATION_PATTERNS = [
    re.compile(r"(?:\d+\s*(?:주|일|달|개월|년|주째|일째|달째|개월째|년째)\s*(?:동안|째|간)?)", re.IGNORECASE),
    re.compile(r"(?:\d+\s*(?:weeks?|days?|months?|years?)\s*(?:now|ago)?)", re.IGNORECASE),
    re.compile(r"(?:한\s*달|두\s*달|세\s*달|일주일|이주일)", re.IGNORECASE),
]

# AbsoluteDate patterns
_ABSOLUTE_DATE_PATTERNS = [
    re.compile(r"\d{4}\s*년", re.IGNORECASE),
    re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일", re.IGNORECASE),
    re.compile(r"(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d+", re.IGNORECASE),
    re.compile(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", re.IGNORECASE),
]

# TemporalContext known values
_CONTEXT_DAY_PARTS = {"아침", "저녁", "밤", "오전", "오후", "낮", "새벽", "morning", "evening", "night", "afternoon"}
_CONTEXT_ROUTINE_STEPS = {"세안후", "세안 후", "클렌징후", "클렌징 후", "화장전", "화장 전", "샤워후", "샤워 후",
                          "after cleansing", "after washing", "before makeup"}
_CONTEXT_SEASONS = {"봄", "여름", "가을", "겨울", "spring", "summer", "fall", "autumn", "winter",
                    "환절기", "장마"}


def split_date(mention_text: str) -> DateSplitResult:
    """Classify a DATE mention into one of 4 sub-types."""
    _load_yaml_config()  # lazy load YAML once
    text = mention_text.strip()
    text_norm = normalize_text(text)

    # 1. Check AbsoluteDate FIRST (before Duration, since "2024년" contains "년")
    for pattern in _ABSOLUTE_DATE_PATTERNS:
        if pattern.search(text):
            return DateSplitResult(kind=DateSubType.ABSOLUTE_DATE, value=text_norm)

    # 2. Check Frequency
    for pattern in _FREQUENCY_PATTERNS:
        if pattern.search(text):
            return DateSplitResult(kind=DateSubType.FREQUENCY, value=text_norm)

    # 3. Check Duration
    for pattern in _DURATION_PATTERNS:
        if pattern.search(text):
            return DateSplitResult(kind=DateSubType.DURATION, value=text_norm)

    # 4. TemporalContext (day part / routine step / season / default)
    context_type = _classify_context(text_norm)
    return DateSplitResult(
        kind=DateSubType.TEMPORAL_CONTEXT,
        value=text_norm,
        context_type=context_type,
    )


def _classify_context(text_norm: str) -> str:
    # Merge hardcoded + YAML-loaded terms
    day_parts = _CONTEXT_DAY_PARTS | _YAML_DAY_PARTS
    routine_steps = _CONTEXT_ROUTINE_STEPS | _YAML_ROUTINE_STEPS
    seasons = _CONTEXT_SEASONS | _YAML_SEASONS

    if text_norm in day_parts or any(p in text_norm for p in day_parts):
        return "day_part"
    if text_norm in routine_steps or any(p in text_norm for p in routine_steps):
        return "routine_step"
    if text_norm in seasons or any(p in text_norm for p in seasons):
        return "season"
    return "general"
