"""Product-product similarity via shared canonical-fact nodes (Phase 8 G1).

Design (fable_doc/plans/2026-07-15_phase8_shared_node_projection.md §Track G,
DECISIONS/2026-07-15_phase8_shared_node_design_dialogue.md): the canonical_fact
layer is a product -> attribute bipartite star. Two products that hang off the
*same* attribute node are two hops apart; projecting those 2-hops into an
explicit product-product edge turns the graph's shared attributes into a
similarity score without a graph DB (a self-join over an inverted index).

Core principle (user-confirmed, decision 1): similarity = sum of IDF over the
*shared* nodes, then rank + top-N. There is no hard-AND across axes, no node
merging, and no hard category gate baked into the score — sharing several axes
scores high on its own, and category gating is a *consumption-context*
parameter (``category_gate``), not a property of the computation.

Node key convention (axis-namespaced, decision 3/4):
* ``keyword::{bee_attr_id}:{canonical_keyword_id}:{polarity}`` — a **composite
  key**. The same canonical keyword under a different BEE attribute is a
  *different* node on purpose ("가볍다" means different things for formulation /
  spreadability / packaging), and opposite polarity is a different node too
  (so a polarity mismatch simply never shares — no separate factor needed).
  BEE-attr is used **only** to scope the keyword; the BEE-attr class name is
  never a scored node on its own.
* ``ingredient::{id}`` / ``category::{id}`` / ``brand::{id}`` /
  ``goal::{main_benefit id}`` — sourced from the serving product profile.

Data-source constraint (resolved per the 3-way review): the keyword composite
key needs ``(bee_attr_id, keyword_id, polarity)``, but the serving profile's
``top_keyword_ids`` has already lost bee_attr and polarity (the aggregation
groups by ``(product, edge_type, dst_type, dst_id)``). So the **keyword axis is
sourced from the raw wrapped-signal sidecar** — the caller injects
``raw_keyword_signals`` extracted from DB ``wrapped_signal``
(bee_attr_id/keyword_id/polarity columns, ``idx_ws_product``) or demo
``demo_state.product_signals``. The keyword aggregation/serving pipeline is left
untouched (zero regression) and the polarity dropped by serving is recovered.

Wiring note: the DB-side query that returns polarity-bearing keyword signals is
**P8-2 work**; this module only *receives* ``raw_keyword_signals`` and never
reads a DB itself. P8-1 verification runs on demo ``product_signals`` (which
carry polarity). The module is storage-agnostic and side-effect free until
``attach_similarity_signals`` writes the ephemeral field.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from src.normalize.bee_normalizer import canonical_keyword_id
from src.rec.category_groups import classify_product_category_group

# Axis namespaces (mirrors user_similarity's ``axis::id`` convention so the same
# raw id in two axes never collides into a false overlap).
KEYWORD_AXIS = "keyword"
INGREDIENT_AXIS = "ingredient"
CATEGORY_AXIS = "category"
BRAND_AXIS = "brand"
GOAL_AXIS = "goal"

# Serving-profile field -> axis for the non-keyword (concept-id) axes.
_PROFILE_AXES: tuple[tuple[str, str], ...] = (
    ("ingredient_concept_ids", INGREDIENT_AXIS),
    ("category_concept_ids", CATEGORY_AXIS),
    ("brand_concept_ids", BRAND_AXIS),
    ("main_benefit_concept_ids", GOAL_AXIS),
)

_CONCEPT_IRI_PREFIX = "concept:"

# How many nearest neighbours are kept per product, and the minimum shared-IDF
# mass for a pair to count as neighbours at all. Both are tunable per consumer;
# defaults are conservative (keep any pair with discriminative shared evidence).
DEFAULT_TOP_N = 10
DEFAULT_MIN_SCORE = 0.0


@dataclass
class SimilarProductSignal:
    """One product B that is attribute-similar to an anchor product A.

    ``shared_axes`` carries the evidence (the shared nodes and their IDF) so a
    consumer can render *why* the two are connected without touching the corpus.
    Evidence-first contract: a signal with no ``shared_axes`` is never emitted.
    """

    product_id: str
    neighbor_name: str
    score: float
    shared_axes: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "neighbor_name": self.neighbor_name,
            "score": round(self.score, 4),
            "shared_axes": self.shared_axes,
        }


def _bare_concept_id(value: str) -> str:
    """Bare id from a concept IRI (``concept:Type:id`` -> ``id``).

    Identity for a value that is already bare (no ``concept:`` prefix). Splits
    at most twice so an id that itself contains a colon is preserved.
    """
    if value.startswith(_CONCEPT_IRI_PREFIX):
        parts = value.split(":", 2)
        if len(parts) == 3:
            return parts[2]
    return value


def _ids(items: Any) -> list[str]:
    """Ids from a serving-profile array (plain strings or ``{"id": ...}`` dicts).

    Mirrors the consumer-contract §3.3 array-element shape; the concept-id axes
    are ``list[str]`` today but this stays tolerant of the dict form too.
    """
    out: list[str] = []
    for item in items or []:
        if isinstance(item, dict):
            value = item.get("id")
            value = str(value) if value is not None else ""
        else:
            value = str(item)
        if value:
            out.append(value)
    return out


def _field(sig: Any, key: str) -> Any:
    """Read ``key`` from a signal that is either a dict or a WrappedSignal."""
    if isinstance(sig, dict):
        return sig.get(key)
    return getattr(sig, key, None)


def _product_name(profile: dict[str, Any]) -> str:
    for key in (
        "representative_product_name",
        "product_name",
        "ONLINE_PROD_NAME",
        "prd_nm",
        "rprs_prd_nm",
    ):
        value = profile.get(key)
        if value:
            return str(value)
    return ""


def keyword_node_key(
    bee_attr_id: Any,
    keyword_id: Any,
    polarity: Any,
    *,
    alias_map: dict[str, str] | None = None,
) -> str:
    """Composite keyword node key ``keyword::{bee}:{canonical_kw}:{polarity}``.

    ``keyword_id``/``bee_attr_id`` arrive as concept IRIs in the signals
    (``concept:Keyword:kw_moist``); the suffix is extracted, the keyword id is
    folded to its canonical concept via the B2 alias map (``kw_moist`` ->
    ``kw_moisturizing``) and only then scoped by bee_attr and polarity. Applying
    the alias to the *bare* id is required because the alias map is keyed on
    bare ids, not IRIs.
    """
    bee = _bare_concept_id(str(bee_attr_id or ""))
    kw_bare = _bare_concept_id(str(keyword_id or ""))
    kw_canon = canonical_keyword_id(kw_bare, alias_map)
    pol = str(polarity or "")
    return f"{KEYWORD_AXIS}::{bee}:{kw_canon}:{pol}"


def build_product_nodes(
    product_profiles: list[dict[str, Any]],
    raw_keyword_signals: dict[str, list[tuple[Any, Any, Any]]],
    *,
    alias_map: dict[str, str] | None = None,
) -> dict[str, set[str]]:
    """Map each product to its set of axis-namespaced attribute nodes.

    ingredient/category/brand/goal nodes come from ``product_profiles`` concept
    ids; the keyword composite keys come from ``raw_keyword_signals``
    (``{pid: [(bee_attr_id, keyword_id, polarity), ...]}``) because serving has
    dropped bee_attr/polarity. Products with an empty node set are still present
    in the map (their similarity is simply empty).
    """
    nodes: dict[str, set[str]] = {}
    for profile in product_profiles:
        pid = str(profile.get("product_id", ""))
        if not pid:
            continue
        node_set = nodes.setdefault(pid, set())
        for field_name, axis in _PROFILE_AXES:
            for cid in _ids(profile.get(field_name)):
                node_set.add(f"{axis}::{_bare_concept_id(cid)}")
        for bee_attr_id, keyword_id, polarity in raw_keyword_signals.get(pid, ()):
            if not keyword_id:
                continue
            node_set.add(
                keyword_node_key(bee_attr_id, keyword_id, polarity, alias_map=alias_map)
            )
    return nodes


def build_idf(product_nodes: dict[str, set[str]]) -> dict[str, float]:
    """Corpus IDF per node: ``log(N / df)`` where df = products holding the node.

    A node present in every product (df == N) gets IDF 0 and thus damps to
    nothing — the automatic hub/commodity control (large brands, base
    ingredients, universal attributes) that replaces a hard exclusion list.
    """
    n = len(product_nodes)
    if n == 0:
        return {}
    df: dict[str, int] = defaultdict(int)
    for node_set in product_nodes.values():
        for node in node_set:
            df[node] += 1
    return {node: math.log(n / count) for node, count in df.items()}


def _shared_axis(
    node_key: str,
    idf: dict[str, float],
    label_index: dict[str, str] | None,
) -> dict[str, Any]:
    axis, _, rest = node_key.partition("::")
    if label_index and node_key in label_index:
        label = label_index[node_key]
    else:
        label = _fallback_label(axis, rest)
    return {
        "axis": axis,
        "node_key": node_key,
        "label": label,
        "idf": round(idf.get(node_key, 0.0), 4),
    }


def _fallback_label(axis: str, rest: str) -> str:
    """Human-ish label from a node key when no label index is available (P8-1).

    P8-2 supplies a proper label sidecar; until then the concept-id suffix is
    the fallback. For the keyword composite key the readable piece is the
    canonical keyword id (the middle segment of ``{bee}:{kw}:{pol}``).
    """
    if axis == KEYWORD_AXIS:
        parts = rest.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return rest


def build_similarity_signals(
    product_nodes: dict[str, set[str]],
    product_profiles: list[dict[str, Any]],
    *,
    idf: dict[str, float],
    category_gate: bool = False,
    min_score: float = DEFAULT_MIN_SCORE,
    top_n: int = DEFAULT_TOP_N,
    label_index: dict[str, str] | None = None,
) -> dict[str, list[SimilarProductSignal]]:
    """Compute per-product top-N attribute-similar neighbours.

    Uses an inverted index (node -> [products]) to visit only pairs that share
    at least one node, accumulating ``score(A,B) = sum IDF(shared node)``. Nodes
    with IDF <= 0 (commodity/hub nodes) are skipped: they add nothing to any
    score and would otherwise blow up pair enumeration. When ``category_gate``
    is True only same category-group pairs survive (item-to-item context); when
    False, cross-category pairs are kept and simply rank by shared IDF mass.

    Returns ``{pid: [SimilarProductSignal, ...]}`` truncated to ``top_n`` per
    product. Neighbour lists are **not** symmetric after truncation — call
    :func:`symmetrize` for the union view a similar-products surface wants.
    Products with no qualifying neighbour are absent from the map.
    """
    if top_n < 1:
        raise ValueError("top_n must be >= 1")

    profiles_by_id = {
        str(p.get("product_id", "")): p
        for p in product_profiles
        if p.get("product_id")
    }
    groups: dict[str, str] = {}
    if category_gate:
        groups = {
            pid: classify_product_category_group(profile)
            for pid, profile in profiles_by_id.items()
        }

    # Inverted index over discriminative nodes only (IDF > 0).
    inverted: dict[str, list[str]] = defaultdict(list)
    for pid, node_set in product_nodes.items():
        for node in node_set:
            if idf.get(node, 0.0) > 0.0:
                inverted[node].append(pid)

    # Accumulate shared nodes per unordered product pair.
    pair_shared: dict[tuple[str, str], list[str]] = defaultdict(list)
    for node, pids in inverted.items():
        if len(pids) < 2:
            continue
        for i in range(len(pids)):
            a = pids[i]
            for j in range(i + 1, len(pids)):
                b = pids[j]
                key = (a, b) if a < b else (b, a)
                pair_shared[key].append(node)

    neighbor_score: dict[str, dict[str, float]] = defaultdict(dict)
    neighbor_shared: dict[str, dict[str, list[str]]] = defaultdict(dict)
    for (a, b), shared_nodes in pair_shared.items():
        if not shared_nodes:
            continue
        if category_gate and groups.get(a) != groups.get(b):
            continue
        score = sum(idf.get(node, 0.0) for node in shared_nodes)
        if score < min_score:
            continue
        neighbor_score[a][b] = score
        neighbor_score[b][a] = score
        neighbor_shared[a][b] = shared_nodes
        neighbor_shared[b][a] = shared_nodes

    result: dict[str, list[SimilarProductSignal]] = {}
    for pid, neigh in neighbor_score.items():
        ranked = sorted(neigh.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
        signals: list[SimilarProductSignal] = []
        for nb_id, score in ranked:
            shared_axes = [
                _shared_axis(node, idf, label_index)
                for node in neighbor_shared[pid][nb_id]
            ]
            shared_axes.sort(key=lambda ax: (-ax["idf"], ax["node_key"]))
            if not shared_axes:  # evidence-first: never emit an unexplained edge
                continue
            nb_profile = profiles_by_id.get(nb_id, {})
            signals.append(
                SimilarProductSignal(
                    product_id=nb_id,
                    neighbor_name=_product_name(nb_profile) or nb_id,
                    score=score,
                    shared_axes=shared_axes,
                )
            )
        if signals:
            result[pid] = signals
    return result


def symmetrize(
    signals: dict[str, list[SimilarProductSignal]],
) -> dict[str, list[SimilarProductSignal]]:
    """Union-symmetrize neighbour lists (A~B if A->B *or* B->A survived top_n).

    ``build_similarity_signals`` scores each pair symmetrically but truncates
    each product's neighbours independently, so B may drop A even though A kept
    B. A similar-products surface (G2/G3) wants the union: if either direction
    is a top-N neighbour, show the edge on both sides. Score and shared_axes are
    symmetric, so the reverse edge reuses them. The reverse neighbour name is
    resolved from any existing signal that names that product, falling back to
    the product id when unknown.
    """
    names: dict[str, str] = {}
    for pid, sigs in signals.items():
        for sig in sigs:
            names.setdefault(sig.product_id, sig.neighbor_name)

    edges: dict[str, dict[str, SimilarProductSignal]] = defaultdict(dict)
    for src, sigs in signals.items():
        for sig in sigs:
            dst = sig.product_id
            # forward edge as-is
            edges[src].setdefault(dst, sig)
            # reverse edge: same score/shared_axes, neighbour is the source.
            # shared_axes is copied so the two directions never alias one list
            # (a consumer sorting/annotating one side must not mutate the other).
            if src not in edges[dst]:
                edges[dst][src] = SimilarProductSignal(
                    product_id=src,
                    neighbor_name=names.get(src, src),
                    score=sig.score,
                    shared_axes=list(sig.shared_axes),
                )

    out: dict[str, list[SimilarProductSignal]] = {}
    for pid, neigh in edges.items():
        if not neigh:
            continue
        out[pid] = sorted(
            neigh.values(), key=lambda s: (-s.score, s.product_id)
        )
    return out


def attach_similarity_signals(
    product_profiles: list[dict[str, Any]],
    signals: dict[str, list[SimilarProductSignal]],
) -> None:
    """Write the ephemeral ``similar_product_ids`` field on each profile in place.

    This is the single activation point an upstream loader calls; until it runs,
    the field is absent and nothing downstream reads it (candidate generation
    never touches ``similar_product_ids``), so the signal is dormant and the
    default recommendation path is unchanged. Products with no neighbours get an
    empty list (an honest "no similar products" rather than a missing field).
    """
    for profile in product_profiles:
        pid = str(profile.get("product_id", ""))
        profile["similar_product_ids"] = [
            sig.to_dict() for sig in signals.get(pid, [])
        ]


def keyword_signals_from_product_signals(
    product_signals: dict[str, list[Any]],
) -> dict[str, list[tuple[str, str, str]]]:
    """Extract keyword ``(bee_attr_id, keyword_id, polarity)`` triples per product.

    Source adapter for the demo path: ``demo_state.product_signals`` is
    ``{pid: [sig, ...]}`` where each ``sig`` is a signal dict (or WrappedSignal)
    carrying ``bee_attr_id``/``keyword_id``/``polarity``. Only signals with a
    keyword id are kept (the keyword axis); products with no keyword signal are
    absent from the result.
    """
    out: dict[str, list[tuple[str, str, str]]] = {}
    for pid, sigs in product_signals.items():
        triples: list[tuple[str, str, str]] = []
        for sig in sigs:
            keyword_id = _field(sig, "keyword_id")
            if not keyword_id:
                continue
            bee_attr_id = _field(sig, "bee_attr_id") or ""
            polarity = _field(sig, "polarity") or ""
            triples.append((str(bee_attr_id), str(keyword_id), str(polarity)))
        if triples:
            out[str(pid)] = triples
    return out
