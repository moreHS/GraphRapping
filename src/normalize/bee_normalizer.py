"""
BEE normalizer: BEE phrase → BEE_ATTR + KEYWORD(s) + polarity/negation/intensity.

BEE_ATTR and KEYWORD remain separate — never merge.
Structure: BEE phrase(raw) → BEE_ATTR(attribute axis) → KEYWORD(normalized expression)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text
from src.common.enums import Polarity, SENTIMENT_MAP
from src.normalize import korean_morph


# --- Keyword canonical alias layer (Phase 7 B2) -----------------------------
# Cache of the flattened alias map (alias keyword_id -> canonical keyword_id).
_ALIAS_MAP_CACHE: dict[str, str] | None = None


def _flatten_alias_chains(aliases: dict[str, str]) -> dict[str, str]:
    """Flatten single-hop chains and reject cycles.

    Guards two error classes the concept-folding layer must never ship with
    (Phase 7 B2): a cycle (``a->b, b->a``) and a canonical target that is itself
    an alias (``a->b, b->c`` must resolve ``a`` to ``c``). Raising here means a
    malformed config fails loudly at load rather than corrupting aggregation.
    """
    resolved: dict[str, str] = {}
    for alias in aliases:
        chain = [alias]
        current = alias
        while current in aliases:
            nxt = aliases[current]
            if nxt in chain:
                raise ValueError(
                    f"keyword_alias_map cycle detected: {' -> '.join(chain + [nxt])}"
                )
            chain.append(nxt)
            current = nxt
        if current != alias:
            resolved[alias] = current
    return resolved


def load_keyword_alias_map(
    filename: str = "keyword_alias_map.yaml", *, force: bool = False
) -> dict[str, str]:
    """Load + cache the flattened keyword alias map (alias -> canonical)."""
    global _ALIAS_MAP_CACHE
    if _ALIAS_MAP_CACHE is None or force:
        raw = load_yaml(filename)
        aliases = raw.get("aliases", {}) if isinstance(raw, dict) else {}
        _ALIAS_MAP_CACHE = _flatten_alias_chains(
            {str(k): str(v) for k, v in aliases.items()}
        )
    return _ALIAS_MAP_CACHE


def canonical_keyword_id(
    keyword_id: str, alias_map: dict[str, str] | None = None
) -> str:
    """Return the canonical keyword_id for ``keyword_id`` (identity if not aliased)."""
    amap = alias_map if alias_map is not None else load_keyword_alias_map()
    return amap.get(keyword_id, keyword_id)


def resolve_surface_keywords(
    phrase_text: str,
    keyword_map: dict[str, list[dict]],
    *,
    use_morphology: bool = True,
    apply_alias: bool = True,
) -> list[tuple[str, str, str]]:
    """Resolve a phrase to dictionary keyword entries.

    Single source of truth for keyword resolution, shared by BEENormalizer
    (signal generation) and the KG mention_extractor candidate queue
    (quarantine suppression) so both paths agree on what "known" means
    (Phase 7 B1 — the two keyword-resolution paths were previously divergent:
    the normalizer matched substrings while the quarantine path never consulted
    the dictionary at all).

    Returns a list of ``(keyword_id, label_ko, surface)`` tuples, deduplicated
    by keyword_id in first-seen order.

    Resolution has two passes:
      1. substring — the historical behavior (surface_norm in phrase_norm),
         kept byte-for-byte so existing signals/snapshots do not shift on this
         pass alone. Polarity of a negated match is handled downstream.
      2. morphology — for phrases the substring pass missed, fold each token to
         a dictionary stem (korean_morph). Skipped entirely when the phrase is
         in a negation context, so folding can never flip polarity.

    Phase 7 B2 (``apply_alias``, default on): after a surface resolves to a
    keyword_id, the id is folded onto its canonical concept via the keyword
    alias map, and dedup then runs on the *canonical* id. This both concentrates
    aggregation support (one concept, not sibling ids) and eliminates the
    double-count where one surface hits multiple sibling ids (e.g. "촉촉한"
    formerly emitted kw_moist AND MoistLike). The canonical label is taken from
    the map entry of the canonical id when present. Pass ``apply_alias=False``
    to test raw resolution mechanics in isolation from alias policy.
    """
    phrase_norm = normalize_text(phrase_text)
    alias_map = load_keyword_alias_map() if apply_alias else {}
    # Canonical label lookup: build once from the same map (single pass) so a
    # folded id reports the canonical concept's label, not the alias surface's.
    id_to_label: dict[str, str] = {}
    if alias_map:
        for entries in keyword_map.values():
            for entry in entries:
                kid = entry.get("keyword_id", "")
                if kid:
                    id_to_label.setdefault(kid, entry.get("label_ko", ""))

    results: list[tuple[str, str, str]] = []
    seen_ids: set[str] = set()

    def _emit(entries: list[dict], surface: str) -> None:
        for entry in entries:
            raw_kid = entry.get("keyword_id", "")
            if not raw_kid:
                continue
            kid = alias_map.get(raw_kid, raw_kid)
            if kid in seen_ids:
                continue
            seen_ids.add(kid)
            label = id_to_label.get(kid) or entry.get("label_ko", surface)
            results.append((kid, label, surface))

    # Pass 1: substring (unchanged legacy behavior).
    for surface, entries in keyword_map.items():
        if normalize_text(surface) in phrase_norm:
            _emit(entries, surface)

    # Pass 2: morphology — only on tokens the substring pass did not cover, and
    # never under negation (guards polarity, e.g. "촉촉하지 않").
    if use_morphology and not korean_morph.has_negation(phrase_norm):
        surface_norms = {surface: normalize_text(surface) for surface in keyword_map}
        for token in phrase_norm.split():
            for stem in korean_morph.stem_candidates(token):
                for surface, entries in keyword_map.items():
                    snorm = surface_norms[surface]
                    if snorm and (snorm in stem or stem in snorm):
                        _emit(entries, surface)

    return results


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

        Strategy: token exact match + prefix match for Korean agglutinative forms.
        Only checks word-initial position to avoid false positives ("안녕", "편안한").
        """
        tokens = text.lower().split()
        neg_count = 0

        # Multi-char Korean negation markers: check if any token STARTS with them
        # This catches "아닌데", "않은", "없는" etc. while avoiding "안녕", "편안한"
        _PREFIX_MARKERS = {"아닌", "아니", "않", "못", "없"}
        # Single-char "안" only as exact token (not prefix, to avoid "안녕")
        _EXACT_MARKERS = {"안", "덜", "not", "no", "don't", "doesn't", "never", "without"}

        for t in tokens:
            if t in _EXACT_MARKERS:
                neg_count += 1
            else:
                for prefix in _PREFIX_MARKERS:
                    if t.startswith(prefix):
                        neg_count += 1
                        break

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
        """Extract keywords by matching surface forms in the phrase.

        Delegates to the shared resolver (substring + conservative morphology)
        so signal generation and quarantine suppression stay in lock-step.
        """
        matches = resolve_surface_keywords(phrase_text, self._keyword_map)
        keyword_ids = [m[0] for m in matches]
        keyword_labels = [m[1] for m in matches]
        surface_forms = [m[2] for m in matches]
        return keyword_ids, keyword_labels, surface_forms

    def get_unknown_surfaces(self, phrase_text: str) -> list[str]:
        """Return surface forms in phrase that don't match any keyword."""
        if resolve_surface_keywords(phrase_text, self._keyword_map):
            return []
        return [phrase_text]
