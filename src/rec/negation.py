"""Shared conservative ingredient-negation detection (Phase 6 Track B).

Extracted so the LLM query-understanding path (``src.rec.query_understanding``)
and the dictionary/alias resolution path (``src.rec.search``) apply the SAME
negation semantics without a circular import (this module imports only
``text_normalize``; neither ``search`` nor ``query_understanding`` is imported
here). The two compiled patterns and their contract are unchanged from their
former home in ``query_understanding`` — they are re-exported there under the
same private names so existing behaviour and tests are preserved byte-for-byte.

Deliberately narrow: a single preceding word (a run of hangul/alnum — no
particle stripping) followed by one fixed negation marker. It does not attempt
to parse arbitrary syntax; markers the regex misses are left to the LLM.

Two patterns because the loanword "free" marker is dangerous without a
separator: many brand/compound names simply end in 프리 (이니스프리 = Innisfree),
so requiring a space/hyphen before 프리/free avoids that whole false-positive
class. Korean grammatical markers (없는/없이/빼고/제외(한)) legitimately attach with
or without a space ("레티놀 없는" / "레티놀없는"), so they allow an optional space.
``제외한?`` matches "제외" or "제외한"; ``free`` is case-insensitive ("retinol-free").
"""

from __future__ import annotations

import re

from src.common.text_normalize import normalize_text

NEGATION_KO_RE = re.compile(r"([0-9A-Za-z가-힣]+?)\s*(없는|없이|빼고|제외한?)")
NEGATION_FREE_RE = re.compile(r"([0-9A-Za-z가-힣]+?)[\s-]+(프리|free)", re.IGNORECASE)


def negation_matches(query: str) -> list[re.Match[str]]:
    """Every negation match in the RAW query (KO markers then the free marker),
    in that fixed order. Each match exposes group(1)=negated word, group(2)=marker.
    """
    return [*NEGATION_KO_RE.finditer(query), *NEGATION_FREE_RE.finditer(query)]


def negated_surfaces(query: str) -> set[str]:
    """Normalized negated WORDS — the surface immediately preceding a negation
    marker (group 1) — for every negation match in the RAW query.

    Used by the ingredient-alias layer (``src.rec.search``) to refuse positive
    adoption of an alias surface that sits inside a negated span, so a query like
    "히알루론 없는 크림" or "레티놀-프리 토너" cannot pull the negated ingredient in
    through the alias map. Comparison is a substring test against these words
    (``alias_surface in negated_word``), so a shorter alias key inside a longer
    negated compound ("히알루론" inside a negated "히알루론산") is still caught.
    """
    out: set[str] = set()
    for match in negation_matches(query):
        norm = normalize_text(match.group(1))
        if norm:
            out.add(norm)
    return out
