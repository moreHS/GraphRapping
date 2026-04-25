"""
Product matcher: brand+product name → product_id.

Match chain: exact → normalized → alias → fuzzy → quarantine
Thresholds: fuzzy auto-accept ≥0.93, manual review 0.80~0.93, quarantine <0.80
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from src.common.text_normalize import normalize_text, strip_brand_prefixes
from src.common.enums import MatchStatus


FUZZY_AUTO_ACCEPT = 0.93
FUZZY_MANUAL_REVIEW = 0.80


@dataclass
class MatchResult:
    matched_product_id: str | None
    match_status: MatchStatus
    match_score: float
    match_method: str


@dataclass
class ProductIndex:
    """In-memory product index for matching."""
    # product_id → product_name
    exact: dict[str, str]
    # normalized_key → product_id
    norm: dict[str, str]
    # brand-stripped normalized_key → product_ids. Multiple IDs mean ambiguous, no auto-match.
    norm_stripped: dict[str, list[str]]
    # alias_norm → product_id
    alias: dict[str, str]
    # product_id → brand_name_norm
    brands: dict[str, str]

    @classmethod
    def build(cls, products: list[dict]) -> ProductIndex:
        exact = {}
        norm = {}
        norm_stripped: dict[str, list[str]] = {}
        brands = {}
        for p in products:
            pid = p["product_id"]
            pname = p.get("product_name", "")
            bname = p.get("brand_name", "")
            exact[pid] = pname
            norm_key = _make_norm_key(bname, pname)
            norm[norm_key] = pid
            stripped_key = _make_stripped_norm_key(bname, pname)
            if stripped_key != norm_key:
                norm_stripped.setdefault(stripped_key, []).append(pid)
            if bname:
                brands[pid] = normalize_text(bname)
        return cls(exact=exact, norm=norm, norm_stripped=norm_stripped, alias={}, brands=brands)

    def add_alias(self, alias_norm: str, product_id: str) -> None:
        self.alias[alias_norm] = product_id


def match_product(
    brand_name_raw: str,
    product_name_raw: str,
    index: ProductIndex,
) -> MatchResult:
    """Match raw brand+product name to a product_id.

    Chain: exact_norm → alias → fuzzy → quarantine
    """
    norm_key = _make_norm_key(brand_name_raw, product_name_raw)

    # 1. Normalized exact match
    if norm_key in index.norm:
        return MatchResult(
            matched_product_id=index.norm[norm_key],
            match_status=MatchStatus.NORM,
            match_score=1.0,
            match_method="norm_exact",
        )

    # 2. Brand-stripped normalized match.
    # Handles catalog names like "라네즈 워터뱅크 세럼" vs review names like "워터뱅크 세럼".
    stripped_key = _make_stripped_norm_key(brand_name_raw, product_name_raw)
    if stripped_key != norm_key and stripped_key in index.norm:
        return MatchResult(
            matched_product_id=index.norm[stripped_key],
            match_status=MatchStatus.NORM,
            match_score=0.99,
            match_method="norm_input_brand_stripped",
        )

    stripped_candidates = index.norm_stripped.get(stripped_key, [])
    if len(stripped_candidates) == 1:
        return MatchResult(
            matched_product_id=stripped_candidates[0],
            match_status=MatchStatus.NORM,
            match_score=0.99,
            match_method="norm_brand_stripped",
        )

    # 3. Alias match
    if norm_key in index.alias:
        return MatchResult(
            matched_product_id=index.alias[norm_key],
            match_status=MatchStatus.ALIAS,
            match_score=0.97,
            match_method="alias",
        )

    # 4. Fuzzy match (brand-filtered)
    brand_norm = normalize_text(brand_name_raw)
    product_norm = normalize_text(product_name_raw)
    best_score = 0.0
    best_pid = None

    for pid, pname in index.exact.items():
        # Brand filter: only fuzzy against same brand
        pid_brand = index.brands.get(pid, "")
        if brand_norm and pid_brand and brand_norm != pid_brand:
            continue

        pname_norm = normalize_text(pname)
        score = SequenceMatcher(None, product_norm, pname_norm).ratio()
        if score > best_score:
            best_score = score
            best_pid = pid

    if best_pid and best_score >= FUZZY_AUTO_ACCEPT:
        return MatchResult(
            matched_product_id=best_pid,
            match_status=MatchStatus.FUZZY,
            match_score=best_score,
            match_method="fuzzy_auto",
        )

    if best_pid and best_score >= FUZZY_MANUAL_REVIEW:
        # Could be correct but needs human review — still quarantine for safety
        return MatchResult(
            matched_product_id=best_pid,
            match_status=MatchStatus.QUARANTINE,
            match_score=best_score,
            match_method="fuzzy_manual_review",
        )

    # 5. Quarantine
    return MatchResult(
        matched_product_id=None,
        match_status=MatchStatus.QUARANTINE,
        match_score=best_score,
        match_method="no_match",
    )


def _make_norm_key(brand: str, product: str) -> str:
    return f"{normalize_text(brand)}|{normalize_text(product)}"


def _make_stripped_norm_key(brand: str, product: str) -> str:
    return f"{normalize_text(brand)}|{strip_brand_prefixes(product, [brand])}"
