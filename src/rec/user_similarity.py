"""User-user similarity → collaborative-affinity signal (Phase 7 D1 prototype).

Goal (fable_doc/plans/2026-07-13_phase7_graph_intelligence.md §D1): turn the
already-serving user preference vectors into the first *connectivity* signal —
"users whose taste is similar to yours preferred this product". This is the
shortest path to "a different result *because* it's a graph": the recommendation
is driven by an edge between users, not by the candidate product's own catalog
truth or review graph.

Design notes
------------
* **Similarity metric = Jaccard over set-valued preference ids.** The serving
  profile stores preferences as *sets* of concept ids (concern/goal/brand/
  keyword/ingredient), not weighted vectors, so Jaccard (|A∩B| / |A∪B|) is the
  natural fit — no magnitude to normalize, symmetric, bounded [0,1], and it
  degrades gracefully when a user has only a handful of preferences. (Cosine
  over 0/1 indicator vectors would rank identically to Jaccard's numerator but
  Jaccard's union denominator additionally penalizes profiles that are large
  and only incidentally overlapping, which is the behaviour we want for a
  "similar taste" signal.)
* **Axes are namespaced** (``concern::x`` vs ``goal::x``) so the same raw id in
  two different preference axes never collides into a false overlap.
* **Cold / sparse defense** via ``min_common_prefs``: two users must share at
  least this many *specific* preferences before they count as neighbours. A
  single shared common concept is not "similar taste". Below the threshold the
  pair contributes nothing (an empty collaborative signal is the correct,
  expected output for sparse data — not an error).
* **Boost-only, evidence-first**: this module only *produces* candidate signals.
  Whether they qualify a candidate is decided downstream by the boost-only
  bucket in ``recommendation_evidence_index`` — ``collab`` never buys
  eligibility on its own (see BOOST_ONLY_ADMISSIBLE_TYPES).

The collaborative product signal is intentionally derived from *product-grained*
edges of similar users (``owned_product_ids``). Concept-level preferences drive
*similarity*; only real user→product edges become *candidate products*. In a
review-only fixture these edges are near-empty, so the signal is expected to be
sparse — the prototype's job is to verify the wiring, not to manufacture data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Preference axes used to measure user-user taste similarity. Chosen because
# they are populated and discriminative in the serving profile; degenerate axes
# (e.g. preferred_bee_attr_ids collapses to a single shared id) and empty axes
# (context/category) are intentionally excluded. The list is a module constant
# so tests and the sparsity probe agree on the axis set.
DEFAULT_SIMILARITY_AXES: tuple[str, ...] = (
    "concern_ids",
    "goal_ids",
    "preferred_brand_ids",
    "preferred_keyword_ids",
    "preferred_ingredient_ids",
)

# Minimum number of shared, specific preferences for two users to be neighbours.
DEFAULT_MIN_COMMON_PREFS = 3
# How many nearest neighbours contribute to a user's collaborative signal.
DEFAULT_TOP_K_NEIGHBORS = 10
# Similarity mass at which a product's collaborative strength saturates to 1.0.
# strength = min(sum(neighbour_similarity for supporters), _STRENGTH_SATURATION).
_STRENGTH_SATURATION = 1.0


@dataclass(frozen=True)
class CollaborativeProductSignal:
    """A product recommended to a user by their similar-taste neighbours."""

    product_id: str
    supporter_count: int          # distinct neighbours who own this product
    max_similarity: float         # strongest neighbour similarity backing it
    strength: float               # normalized score in (0, 1] for the scorer

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.product_id,
            "supporter_count": self.supporter_count,
            "max_similarity": round(self.max_similarity, 4),
            "strength": round(self.strength, 4),
        }


def _extract_ids(items: Any) -> set[str]:
    """Ids from a preference list (dicts with 'id' or plain strings)."""
    out: set[str] = set()
    for item in items or []:
        value = str(item.get("id", "")) if isinstance(item, dict) else str(item)
        if value:
            out.add(value)
    return out


def _strip_product_prefix(pid: str) -> str:
    return pid[len("product:"):] if pid.startswith("product:") else pid


def preference_signature(
    user_profile: dict[str, Any],
    axes: tuple[str, ...] = DEFAULT_SIMILARITY_AXES,
) -> set[str]:
    """Axis-namespaced set of a user's preference ids for similarity."""
    signature: set[str] = set()
    for axis in axes:
        for pid in _extract_ids(user_profile.get(axis)):
            signature.add(f"{axis}::{pid}")
    return signature


def owned_product_ids(user_profile: dict[str, Any]) -> set[str]:
    """Prefix-normalized set of product ids the user owns."""
    return {_strip_product_prefix(pid) for pid in _extract_ids(user_profile.get("owned_product_ids"))}


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    """|A∩B| / |A∪B|, 0.0 when either side is empty."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if intersection == 0:
        return 0.0
    return intersection / len(a | b)


def build_collaborative_signals(
    user_profiles: list[dict[str, Any]],
    *,
    axes: tuple[str, ...] = DEFAULT_SIMILARITY_AXES,
    min_common_prefs: int = DEFAULT_MIN_COMMON_PREFS,
    top_k_neighbors: int = DEFAULT_TOP_K_NEIGHBORS,
) -> dict[str, list[CollaborativeProductSignal]]:
    """Compute per-user collaborative product signals from user-user similarity.

    For each user: find neighbours sharing >= ``min_common_prefs`` specific
    preferences, keep the ``top_k_neighbors`` most similar, then collect the
    products those neighbours own (excluding products the user already owns).
    Each such product becomes a ``CollaborativeProductSignal`` whose strength
    accumulates the similarity of the neighbours backing it.

    Returns a mapping ``user_id -> [signals]``. Users with no qualifying
    neighbour or no neighbour-owned products are simply absent from the map
    (an empty collaborative signal is the correct output for sparse data).
    """
    if min_common_prefs < 1:
        raise ValueError("min_common_prefs must be >= 1")
    if top_k_neighbors < 1:
        raise ValueError("top_k_neighbors must be >= 1")

    signatures: dict[str, set[str]] = {}
    owned: dict[str, set[str]] = {}
    order: list[str] = []
    for profile in user_profiles:
        uid = str(profile.get("user_id", ""))
        if not uid or uid in signatures:
            continue
        signatures[uid] = preference_signature(profile, axes)
        owned[uid] = owned_product_ids(profile)
        order.append(uid)

    result: dict[str, list[CollaborativeProductSignal]] = {}
    for uid in order:
        sig = signatures[uid]
        if not sig:
            continue
        # Rank neighbours by (similarity desc, user_id asc) for determinism.
        neighbors: list[tuple[float, str]] = []
        for other in order:
            if other == uid:
                continue
            if len(sig & signatures[other]) < min_common_prefs:
                continue
            sim = jaccard_similarity(sig, signatures[other])
            if sim > 0.0:
                neighbors.append((sim, other))
        neighbors.sort(key=lambda pair: (-pair[0], pair[1]))
        neighbors = neighbors[:top_k_neighbors]
        if not neighbors:
            continue

        # Accumulate neighbour support per product not already owned by the user.
        own_products = owned[uid]
        support: dict[str, list[float]] = {}
        for sim, other in neighbors:
            for pid in owned[other] - own_products:
                support.setdefault(pid, []).append(sim)
        if not support:
            continue

        signals = [
            CollaborativeProductSignal(
                product_id=pid,
                supporter_count=len(sims),
                max_similarity=max(sims),
                strength=min(sum(sims), _STRENGTH_SATURATION),
            )
            for pid, sims in support.items()
        ]
        # Deterministic ordering: strongest signal first, then product_id.
        signals.sort(key=lambda s: (-s.strength, s.product_id))
        result[uid] = signals

    return result


def attach_collaborative_signals(
    user_profiles: list[dict[str, Any]],
    *,
    axes: tuple[str, ...] = DEFAULT_SIMILARITY_AXES,
    min_common_prefs: int = DEFAULT_MIN_COMMON_PREFS,
    top_k_neighbors: int = DEFAULT_TOP_K_NEIGHBORS,
) -> None:
    """Populate each profile's ``collaborative_product_ids`` field in place.

    This is the single wiring point an upstream caller (serving load / audit)
    invokes to activate the collaborative signal. Until it is called, the field
    is absent and candidate generation emits no ``collab`` overlaps — so the
    signal is dormant and the default recommendation path is unchanged.
    """
    signals = build_collaborative_signals(
        user_profiles,
        axes=axes,
        min_common_prefs=min_common_prefs,
        top_k_neighbors=top_k_neighbors,
    )
    for profile in user_profiles:
        uid = str(profile.get("user_id", ""))
        profile["collaborative_product_ids"] = [
            signal.to_dict() for signal in signals.get(uid, [])
        ]
