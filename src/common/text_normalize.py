"""
Text normalization utilities.
"""

from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    """Normalize text for canonical matching: lowercase, strip, collapse whitespace."""
    text = unicodedata.normalize("NFC", text)
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def strip_brand_prefixes(name: str, brand_names: list[str] | tuple[str, ...] | set[str] | None = None) -> str:
    """Normalize a product name and remove a known brand prefix when it is separated by whitespace."""
    name_norm = normalize_text(re.sub(r"\s*\(.*?\)\s*", " ", name))
    if not brand_names:
        return name_norm

    for brand in brand_names:
        brand_norm = normalize_text(brand)
        if brand_norm and name_norm.startswith(f"{brand_norm} "):
            return name_norm[len(brand_norm):].strip()
    return name_norm
