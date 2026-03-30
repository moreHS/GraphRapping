"""
Alias resolver for multilingual / romanization matching.

Manages concept_alias lookups for brand, ingredient, category names
across Korean, English, and romanized forms.
"""

from __future__ import annotations

from src.common.text_normalize import normalize_text


class AliasResolver:
    """Resolves surface text to concept_id via alias lookup."""

    def __init__(self) -> None:
        # alias_norm → concept_id
        self._alias_map: dict[str, str] = {}

    def load_aliases(self, aliases: list[dict]) -> None:
        """Load alias records from concept_alias table.

        Each dict should have: alias_norm, concept_id
        """
        for a in aliases:
            self._alias_map[a["alias_norm"]] = a["concept_id"]

    def add_alias(self, alias_text: str, concept_id: str) -> None:
        self._alias_map[normalize_text(alias_text)] = concept_id

    def resolve(self, text: str) -> str | None:
        """Resolve surface text to concept_id, or None if not found."""
        return self._alias_map.get(normalize_text(text))

    def resolve_brand(self, brand_text: str) -> str | None:
        return self.resolve(brand_text)

    def resolve_ingredient(self, ingredient_text: str) -> str | None:
        return self.resolve(ingredient_text)

    @property
    def size(self) -> int:
        return len(self._alias_map)
