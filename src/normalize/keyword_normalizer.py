"""
Keyword normalizer: surface form → keyword_id.

Loads keyword_surface_map.yaml and provides lookup.
Unknown surfaces are routed to quarantine.
"""

from __future__ import annotations

from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text


class KeywordNormalizer:
    """Normalizes keyword surface forms to canonical keyword_ids."""

    def __init__(self) -> None:
        # surface_norm → [{keyword_id, label_ko}]
        self._map: dict[str, list[dict]] = {}

    def load(self, filename: str = "keyword_surface_map.yaml") -> None:
        raw = load_yaml(filename)
        self._map.clear()
        for surface, entries in raw.items():
            self._map[normalize_text(surface)] = entries

    def load_from_dict(self, mapping: dict) -> None:
        self._map.clear()
        for surface, entries in mapping.items():
            self._map[normalize_text(surface)] = entries

    def resolve(self, surface_text: str) -> list[dict] | None:
        """Resolve surface text to keyword entries, or None if unknown."""
        norm = normalize_text(surface_text)
        return self._map.get(norm)

    def is_known(self, surface_text: str) -> bool:
        return normalize_text(surface_text) in self._map

    @property
    def size(self) -> int:
        return len(self._map)
