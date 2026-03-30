"""
Tool / Concern / Segment deriver.

Classifies object mentions in context-dependent relations:
  used_with(target, X) → X is Tool or Product?
  recommended_to(target, X) → X is UserSegment or raw Person?
  affects(target, X) → X is Concern?

Chain: exact dict → normalized dict → pattern rule → fallback quarantine.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text
from src.common.enums import EntityType


@dataclass
class DeriveResult:
    entity_type: EntityType | None
    concept_id: str | None
    label: str | None
    method: str  # EXACT_DICT|NORM_DICT|PATTERN|QUARANTINE


class ToolConcernSegmentDeriver:
    """Derives entity type for ambiguous mentions using dictionaries and patterns."""

    def __init__(self) -> None:
        self._tools: dict[str, dict] = {}         # norm → {concept_id, label_ko}
        self._concerns: dict[str, dict] = {}
        self._segments: dict[str, dict] = {}

    def load_dictionaries(
        self,
        tool_file: str = "tool_dict.yaml",
        concern_file: str = "concern_dict.yaml",
        segment_file: str = "segment_dict.yaml",
    ) -> None:
        for surface, entry in load_yaml(tool_file).items():
            self._tools[normalize_text(surface)] = entry
        for surface, entry in load_yaml(concern_file).items():
            self._concerns[normalize_text(surface)] = entry
        for surface, entry in load_yaml(segment_file).items():
            self._segments[normalize_text(surface)] = entry

    def derive_used_with(self, object_text: str) -> DeriveResult:
        """Classify used_with object as Tool or Product."""
        norm = normalize_text(object_text)

        # Tool dict lookup
        if norm in self._tools:
            entry = self._tools[norm]
            return DeriveResult(
                entity_type=EntityType.TOOL,
                concept_id=entry.get("concept_id"),
                label=entry.get("label_ko", object_text),
                method="EXACT_DICT",
            )

        # Pattern: common tool suffixes
        tool_suffixes = ("브러시", "퍼프", "스펀지", "면봉", "화장솜", "뷰러", "빗", "brush", "sponge", "puff")
        if any(norm.endswith(s) for s in tool_suffixes):
            return DeriveResult(
                entity_type=EntityType.TOOL,
                concept_id=None,
                label=object_text,
                method="PATTERN",
            )

        # Assume Product if not a tool (most common case for used_with)
        return DeriveResult(
            entity_type=EntityType.OTHER_PRODUCT,
            concept_id=None,
            label=object_text,
            method="PATTERN",
        )

    def derive_concern(self, object_text: str) -> DeriveResult:
        """Classify as Concern concept."""
        norm = normalize_text(object_text)

        if norm in self._concerns:
            entry = self._concerns[norm]
            return DeriveResult(
                entity_type=EntityType.CONCERN,
                concept_id=entry.get("concept_id"),
                label=entry.get("label_ko", object_text),
                method="EXACT_DICT",
            )

        # Partial match
        for key, entry in self._concerns.items():
            if key in norm or norm in key:
                return DeriveResult(
                    entity_type=EntityType.CONCERN,
                    concept_id=entry.get("concept_id"),
                    label=entry.get("label_ko", object_text),
                    method="NORM_DICT",
                )

        return DeriveResult(
            entity_type=None,
            concept_id=None,
            label=object_text,
            method="QUARANTINE",
        )

    def derive_segment(self, object_text: str) -> DeriveResult:
        """Classify recommended_to/targeted_at object as UserSegment."""
        norm = normalize_text(object_text)

        if norm in self._segments:
            entry = self._segments[norm]
            return DeriveResult(
                entity_type=EntityType.USER_SEGMENT,
                concept_id=entry.get("concept_id"),
                label=entry.get("label_ko", object_text),
                method="EXACT_DICT",
            )

        # Pattern: skin type keywords
        skin_keywords = ("건성", "지성", "복합성", "민감성", "중성")
        for kw in skin_keywords:
            if kw in norm:
                return DeriveResult(
                    entity_type=EntityType.USER_SEGMENT,
                    concept_id=f"segment_{normalize_text(kw)}_skin",
                    label=f"{kw}피부",
                    method="PATTERN",
                )

        # Age band pattern
        import re
        age_match = re.search(r"(\d+)\s*대", norm)
        if age_match:
            age = age_match.group(1)
            return DeriveResult(
                entity_type=EntityType.USER_SEGMENT,
                concept_id=f"segment_{age}s",
                label=f"{age}대",
                method="PATTERN",
            )

        # Raw person mention — not a segment
        return DeriveResult(
            entity_type=None,
            concept_id=None,
            label=object_text,
            method="QUARANTINE",
        )
