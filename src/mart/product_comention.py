"""Product-product co-mention → co-mention affinity signal (Phase 7 D2).

Goal (fable_doc/plans/2026-07-13_phase7_graph_intelligence.md §D2): derive a
product-product connectivity signal from reviews — "these two products are
mentioned together in the same reviews". This is the review-data-native
alternative to the co-use edge (``top_coused_product_ids``), which is 0 in the
current SKU data. Two products co-mentioned across independent reviews are
plausibly related/substitutable, so co-mention is a candidate boost for
products connected (via reviews) to something the user already owns.

Why a *separate* signal (not a reuse of the coused channel)
-----------------------------------------------------------
Co-use ("used together") and co-mention ("mentioned together") are different
relations. ``top_coused_product_ids`` is a persisted serving column populated
by ``build_serving_views`` from ``USED_WITH_PRODUCT_SIGNAL`` aggregation;
repurposing it for co-mention would be a provenance lie (the field would claim
co-USE evidence it does not have) and would drag in the DB/DDL/serving-schema
layers. Following the D1 pattern (``src/rec/user_similarity.py``), co-mention
is instead attached to the product profile as an *ephemeral* in-process field
``comention_product_ids`` — never persisted, never a serving column, so it
adds no schema/DDL surface. Until ``attach_comention_signals`` is called the
field is absent, no ``comention`` overlap is generated, and the default
recommendation path is byte-identical.

Density reality (measured 2026-07-14, dense_golden + wide, kg_mode=on)
----------------------------------------------------------------------
In the review-only fixtures, **no review co-mentions two distinct *real*
(catalog-linked) products** — every review is about exactly one real product,
and any second product reference is an unresolved ghost surface form
(``concept:Product:다른라인`` / ``미니어처`` / ``에센스`` …). So the signal is
expected to be empty here: the prototype's job is to verify the wiring so the
signal activates the moment real co-mention data (multi-product reviews, or
resolved comparison SKUs) arrives — not to manufacture data. See
``DECISIONS/2026-07-14_phase7_d2_comention.md``.

Design notes
------------
* **Polarity filter**: a product brought into a review through a *negative*
  Product-typed edge (e.g. a disparaging ``comparison_with``: "A is better than
  B") must NOT count as a similarity/relatedness co-mention. Negative
  memberships are dropped before pairing (``exclude_negative``), so disparaging
  comparisons never pollute the co-mention set.
* **Minimum support gate**: a pair must be co-mentioned in ``>= min_support``
  *distinct* reviews to count (default 2). A single shared review is noise — the
  same cross-review corroboration philosophy as the corpus promotion gate. As
  real review volume grows this can be raised.
* **Boost-only, evidence-first**: this module only *produces* candidate signals.
  Whether they qualify a candidate is decided downstream by the boost-only
  bucket in ``recommendation_evidence_index`` — ``comention`` NEVER buys
  eligibility on its own in any mode (see ``BOOST_ONLY_ADMISSIBLE_TYPES``).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any


# Minimum number of distinct reviews co-mentioning a pair for it to count.
DEFAULT_MIN_SUPPORT = 2
# How many co-mention neighbours a product keeps.
DEFAULT_TOP_N = 10
# Review support at which a pair's co-mention strength saturates to 1.0.
_STRENGTH_SATURATION = 5.0


@dataclass(frozen=True)
class ComentionProductSignal:
    """A product co-mentioned with another product across reviews."""

    product_id: str
    support: int          # distinct reviews co-mentioning the two products
    strength: float       # normalized score in (0, 1] for the scorer

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.product_id,
            "support": self.support,
            "strength": round(self.strength, 4),
        }


def _strip_product_prefix(pid: str) -> str:
    return pid[len("product:"):] if pid.startswith("product:") else pid


def _is_ghost(pid: str) -> bool:
    """Unresolved product mention (never linked to a real catalog SKU)."""
    return pid.startswith("concept:Product:")


def review_products_from_signals(
    signals: list[dict[str, Any]],
    *,
    real_product_ids: set[str] | None = None,
    exclude_negative: bool = True,
) -> dict[str, set[str]]:
    """Map each review_id to the set of REAL products it references.

    A review's product set is ``{target_product}`` plus any product brought in
    by a Product-typed destination signal (``comparison`` / ``coused`` edges).
    Ghost mentions (``concept:Product:*``) are dropped — only recommendable
    catalog SKUs can seed a product-product recommendation. When
    ``real_product_ids`` is given, products outside it are also dropped. When
    ``exclude_negative`` is True, a Product-dst edge with ``NEG`` polarity does
    not contribute its product (disparaging comparison is not relatedness).
    """
    def _real(pid: str) -> bool:
        if not pid or _is_ghost(pid):
            return False
        return real_product_ids is None or pid in real_product_ids

    review_products: dict[str, set[str]] = defaultdict(set)
    for sig in signals:
        rid = sig.get("review_id") or ""
        if not rid:
            continue
        target = _strip_product_prefix(str(sig.get("target_product_id") or ""))
        if _real(target):
            review_products[rid].add(target)
        if (sig.get("dst_type") or "") == "Product":
            polarity = (sig.get("polarity") or "").upper()
            if exclude_negative and polarity == "NEG":
                continue
            dst = _strip_product_prefix(str(sig.get("dst_id") or ""))
            if _real(dst):
                review_products[rid].add(dst)
    return review_products


def build_comention_signals(
    review_products: dict[str, set[str]],
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    top_n: int = DEFAULT_TOP_N,
    saturation: float = _STRENGTH_SATURATION,
) -> dict[str, list[ComentionProductSignal]]:
    """Compute per-product co-mention neighbours from review→products membership.

    For every unordered product pair co-mentioned in ``>= min_support`` distinct
    reviews, each product gains the other as a ``ComentionProductSignal`` whose
    strength grows with the number of corroborating reviews. Pairs below the
    support gate contribute nothing (an empty result is the correct output for
    sparse data — not an error).

    Returns ``product_id -> [signals]`` (products with no qualifying pair are
    absent from the map).
    """
    if min_support < 1:
        raise ValueError("min_support must be >= 1")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if saturation <= 0:
        raise ValueError("saturation must be > 0")

    pair_reviews: dict[tuple[str, str], set[str]] = defaultdict(set)
    for rid, products in review_products.items():
        ordered = sorted(products)
        for i in range(len(ordered)):
            for j in range(i + 1, len(ordered)):
                pair_reviews[(ordered[i], ordered[j])].add(rid)

    neighbours: dict[str, list[ComentionProductSignal]] = defaultdict(list)
    for (a, b), rids in pair_reviews.items():
        support = len(rids)
        if support < min_support:
            continue
        strength = min(support / saturation, 1.0)
        neighbours[a].append(ComentionProductSignal(b, support, strength))
        neighbours[b].append(ComentionProductSignal(a, support, strength))

    result: dict[str, list[ComentionProductSignal]] = {}
    for pid, signals in neighbours.items():
        signals.sort(key=lambda s: (-s.strength, s.product_id))
        result[pid] = signals[:top_n]
    return result


def attach_comention_signals(
    product_profiles: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    *,
    min_support: int = DEFAULT_MIN_SUPPORT,
    top_n: int = DEFAULT_TOP_N,
    exclude_negative: bool = True,
) -> None:
    """Populate each profile's ``comention_product_ids`` field in place.

    This is the single wiring point an upstream caller (serving load / audit)
    invokes to activate the co-mention signal. Until it is called, the field is
    absent and candidate generation emits no ``comention`` overlaps — so the
    signal is dormant and the default recommendation path is unchanged.
    """
    real_ids = {str(p.get("product_id")) for p in product_profiles if p.get("product_id") is not None}
    review_products = review_products_from_signals(
        signals, real_product_ids=real_ids, exclude_negative=exclude_negative,
    )
    signals_by_product = build_comention_signals(
        review_products, min_support=min_support, top_n=top_n,
    )
    for profile in product_profiles:
        pid = str(profile.get("product_id"))
        profile["comention_product_ids"] = [
            signal.to_dict() for signal in signals_by_product.get(pid, [])
        ]
