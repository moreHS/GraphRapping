"""
BEE normalizer: BEE phrase → BEE_ATTR + KEYWORD(s) + polarity/negation/intensity.

BEE_ATTR and KEYWORD remain separate — never merge.
Structure: BEE phrase(raw) → BEE_ATTR(attribute axis) → KEYWORD(normalized expression)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text
from src.common.enums import Polarity, SENTIMENT_MAP


@dataclass
class BEENormalizeResult:
    bee_attr_id: str
    bee_attr_label: str
    keyword_ids: list[str] = field(default_factory=list)
    keyword_labels: list[str] = field(default_factory=list)
    polarity: str | None = None
    negated: bool = False
    intensity: float = 1.0
    confidence: float = 1.0
    raw_phrase: str = ""
    surface_forms: list[str] = field(default_factory=list)
    keyword_source: str | None = None  # DICT|RULE|CANDIDATE — validation status


# Negation markers (Korean + English)
_NEGATION_MARKERS = {
    "안", "않", "못", "없", "아닌", "아니", "덜",
    "not", "no", "don't", "doesn't", "never", "without",
}

# Intensity modifiers
_LOW_INTENSITY = {"조금", "약간", "살짝", "미세하게", "slightly", "a bit", "a little"}
_HIGH_INTENSITY = {"매우", "정말", "진짜", "엄청", "완전", "very", "extremely", "super", "really"}


class BEENormalizer:
    """Normalizes BEE raw phrases into BEE_ATTR + KEYWORD(s)."""

    def __init__(self) -> None:
        self._attr_dict: dict[str, dict] = {}
        self._keyword_map: dict[str, list[dict]] = {}

    def load_dictionaries(
        self,
        attr_dict_file: str = "bee_attr_dict.yaml",
        keyword_map_file: str = "keyword_surface_map.yaml",
    ) -> None:
        self._attr_dict = load_yaml(attr_dict_file)
        self._keyword_map = load_yaml(keyword_map_file)

    def load_from_dicts(self, attr_dict: dict, keyword_map: dict) -> None:
        self._attr_dict = attr_dict
        self._keyword_map = keyword_map

    def normalize(
        self,
        phrase_text: str,
        bee_attr_raw: str,
        raw_sentiment: str | None = None,
    ) -> BEENormalizeResult:
        """Normalize a single BEE raw row.

        Args:
            phrase_text: Raw BEE phrase (e.g. "착붙하고 오후에도 안 떠요")
            bee_attr_raw: Raw attribute type (e.g. "밀착력", "Adhesion")
            raw_sentiment: Raw sentiment string (e.g. "긍정", "부정")
        """
        # Resolve BEE_ATTR
        attr_entry = self._attr_dict.get(bee_attr_raw, {})
        bee_attr_id = attr_entry.get("attr_id", f"bee_attr_{normalize_text(bee_attr_raw)}")
        bee_attr_label = attr_entry.get("label_ko", bee_attr_raw)

        # Detect polarity
        polarity = self._resolve_polarity(raw_sentiment)

        # Detect negation
        negated = self._detect_negation(phrase_text)
        if negated and polarity == Polarity.NEG.value:
            polarity = Polarity.POS.value  # double negation → positive
        elif negated and polarity == Polarity.POS.value:
            polarity = Polarity.NEG.value  # negated positive → negative

        # Detect intensity
        intensity = self._detect_intensity(phrase_text)

        # Extract keywords from phrase
        keyword_ids, keyword_labels, surface_forms = self._extract_keywords(phrase_text)

        # Determine keyword source
        if keyword_ids:
            keyword_source = "DICT"
        else:
            keyword_source = "CANDIDATE"

        return BEENormalizeResult(
            bee_attr_id=bee_attr_id,
            bee_attr_label=bee_attr_label,
            keyword_ids=keyword_ids,
            keyword_labels=keyword_labels,
            polarity=polarity,
            negated=negated,
            intensity=intensity,
            confidence=1.0 if attr_entry else 0.7,
            raw_phrase=phrase_text,
            surface_forms=surface_forms,
            keyword_source=keyword_source,
        )

    def _resolve_polarity(self, raw_sentiment: str | None) -> str | None:
        if not raw_sentiment:
            return None
        mapped = SENTIMENT_MAP.get(raw_sentiment.strip().lower())
        if mapped:
            return mapped.value
        mapped = SENTIMENT_MAP.get(raw_sentiment.strip())
        if mapped:
            return mapped.value
        return raw_sentiment

    def _detect_negation(self, text: str) -> bool:
        """Detect negation with double-negation awareness.

        Single negation → True (negated)
        Double negation (e.g. "안 건조한 건 아닌데") → False (double negation cancels)
        Uses both token matching and substring matching for Korean agglutinative forms.
        """
        text_lower = text.lower()
        tokens = text_lower.split()
        neg_count = sum(1 for t in tokens if t in _NEGATION_MARKERS)
        # Also check substring matches for agglutinative Korean (e.g. "아닌데" contains "아닌")
        for marker in _NEGATION_MARKERS:
            if len(marker) >= 2:  # avoid single-char false positives
                count_in_text = text_lower.count(marker)
                token_count = sum(1 for t in tokens if t == marker)
                # Add substring matches that weren't caught as tokens
                neg_count += max(0, count_in_text - token_count)
        return neg_count % 2 == 1

    def _detect_intensity(self, text: str) -> float:
        tokens = text.lower().split()
        for t in tokens:
            if t in _LOW_INTENSITY:
                return 0.4
            if t in _HIGH_INTENSITY:
                return 1.5
        return 1.0

    def _extract_keywords(self, phrase_text: str) -> tuple[list[str], list[str], list[str]]:
        """Extract keywords by matching surface forms in the phrase."""
        keyword_ids: list[str] = []
        keyword_labels: list[str] = []
        surface_forms: list[str] = []
        phrase_norm = normalize_text(phrase_text)

        for surface, entries in self._keyword_map.items():
            surface_norm = normalize_text(surface)
            if surface_norm in phrase_norm:
                for entry in entries:
                    kid = entry.get("keyword_id", "")
                    label = entry.get("label_ko", surface)
                    if kid and kid not in keyword_ids:
                        keyword_ids.append(kid)
                        keyword_labels.append(label)
                        surface_forms.append(surface)

        return keyword_ids, keyword_labels, surface_forms

    def get_unknown_surfaces(self, phrase_text: str) -> list[str]:
        """Return surface forms in phrase that don't match any keyword."""
        phrase_norm = normalize_text(phrase_text)
        known_surfaces = set()
        for surface in self._keyword_map:
            if normalize_text(surface) in phrase_norm:
                known_surfaces.add(normalize_text(surface))
        if not known_surfaces:
            return [phrase_text]
        return []
