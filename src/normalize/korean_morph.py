"""
Conservative Korean morphology helper for keyword resolution (Phase 7 B1).

Goal: let inflected adjective/verb surfaces (`촉촉해서`, `순하고`, `순해요`)
resolve to a dictionary stem WITHOUT a full morphological analyzer. This is
deliberately a whitelist ending-stripper, not kiwipiepy — the precision guard
is that a folded stem is only ever *used* when it is present in the keyword
dictionary (see `bee_normalizer.resolve_surface_keywords`), and that folding is
skipped in negation context so polarity never flips (`촉촉하지 않` must not
assert `촉촉`).

Known limitation: ㅂ-irregular conjugation (`가볍고`→dict `가벼움`, `부드러워요`
→`부드럽`) is not reconciled — such forms simply fail to fold, which is a recall
miss, never a wrong fold. That is the intended safe trade-off.
"""

from __future__ import annotations

from src.common.text_normalize import normalize_text

# Connective / terminal verb-adjective endings, longest first so the longest
# ending wins (strip `해서` before `서`). The dictionary-membership gate in the
# resolver is what keeps this safe; this list only has to be *plausible* endings.
_ENDINGS: tuple[str, ...] = (
    "했었어요", "하였어요", "했습니다", "합니다",
    "했어요", "해봤어요", "해서요", "하더라",
    "해요", "해서", "해도", "해야", "하고", "하니", "하며", "하는",
    "하지", "한데", "네요", "어요", "아요", "워요", "와요", "워서",
    "라서", "구요", "고요", "던데", "은데", "는데",
    "한", "함", "해", "하", "고", "서", "게", "음", "임", "니", "며", "지", "도",
)

# Negation markers (kept in sync with bee_normalizer._NEGATION_MARKERS). A
# phrase carrying any of these disables the *folding* pass — substring matching
# is untouched (its polarity is already flipped downstream by the normalizer).
_NEGATION_MARKERS: frozenset[str] = frozenset({
    "안", "않", "못", "없", "아닌", "아니", "덜", "말고",
    "not", "no", "don't", "doesn't", "never", "without",
})

_MIN_STEM_LEN = 2


def has_negation(text: str) -> bool:
    """True when `text` contains a negation marker (word-initial aware).

    Mirrors bee_normalizer._detect_negation's marker set but is a plain
    presence check (not parity) — the folding guard only needs "is there any
    negation nearby", not double-negation resolution.
    """
    norm = normalize_text(text)
    prefix_markers = ("않", "없", "아닌", "아니", "못")
    exact_markers = {"안", "덜", "말고", "not", "no", "don't", "doesn't", "never", "without"}
    for tok in norm.split():
        if tok in exact_markers:
            return True
        if any(tok.startswith(p) for p in prefix_markers):
            return True
    # Also catch fused forms like "촉촉하지않아요" (no space before 않).
    if "않" in norm or "없" in norm:
        return True
    return False


def stem_candidates(word: str) -> list[str]:
    """Return candidate dictionary-lookup stems for one inflected token.

    Conservative: strips a single whitelisted ending, then also offers a
    `해`→`하` adjective-contraction variant (`순해`→`순하`). Never returns a
    stem shorter than `_MIN_STEM_LEN`. Order is deterministic; caller dedups.
    """
    norm = normalize_text(word)
    candidates: list[str] = []

    def _add(value: str) -> None:
        if len(value) >= _MIN_STEM_LEN and value not in candidates:
            candidates.append(value)

    for ending in _ENDINGS:
        if norm.endswith(ending) and len(norm) - len(ending) >= _MIN_STEM_LEN:
            stem = norm[: len(norm) - len(ending)]
            _add(stem)
            # `순해` (stem left after stripping e.g. `요`) → `순하`
            if stem.endswith("해"):
                _add(stem[:-1] + "하")
            break

    # Whole-word `해`→`하` contraction (e.g. token already ends in 해 with no
    # further ending, such as `촉촉해`).
    if norm.endswith("해") and len(norm) >= _MIN_STEM_LEN + 1:
        _add(norm[:-1] + "하")

    return candidates
