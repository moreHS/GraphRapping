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


def strip_brand_prefixes(name: str) -> str:
    """Remove common brand prefixes/suffixes for fuzzy matching."""
    name = re.sub(r"\s*\(.*?\)\s*", " ", name)
    return normalize_text(name)
