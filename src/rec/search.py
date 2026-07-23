"""
Concept-based search (Phase 4.2, fable_doc/03_improvement_plan.md §4.2).

Not full-text search: the query is resolved into known concepts (brand /
category / ingredient / concern / goal / keyword) using the same surface-form
dictionaries and catalog vocabulary the recommendation candidate generator
already relies on, then products are ranked by how many of those concepts
they carry. This is evidence-first-consistent: no free-text scoring model, no
substring-over-product-name fallback when nothing resolves.

Reuses (does not reimplement):
- ``src.common.concept_resolver`` for concern/goal surface-form normalization.
- ``src.rec.category_groups`` for the recommendation tab keyword vocabulary
  (스킨케어/메이크업/바디/헤어/향수) and per-product category classification.
- ``src.rec.recommendation_evidence_index`` for evidence-family classification
  of matched concepts (PRODUCT_MASTER_TRUTH / REVIEW_GRAPH_RELATION), so a
  search result's evidence is labeled identically to a recommendation's.
- ``src.rec.semantic_compatibility.normalize_signal_id`` for signal-id
  comparisons that must tolerate both plain ids and ``concept:Keyword:x``-style
  IRIs.

Does NOT reuse the recommendation ``Scorer`` — relevance here is a simple
overlap count, not the weighted/shrunk recommendation score (search is
anonymous and user-profile-free by design).

Known limitation (surface matching): every axis resolves a surface form via a
raw ``surface in norm_query`` substring test. Korean writes compounds without
internal spaces, so a short surface can match inside a larger unrelated token
(e.g. the category "크림" matches inside "핸드크림"; a 2-character concern can
match inside a bigger word). A token-boundary rule was evaluated and rejected:
because Korean tokens are space-free, boundary matching also drops the
legitimate spaceless matches the resolver depends on ("건조" in "건조해서",
"촉촉" in "촉촉한"), i.e. it would break resolution far more broadly than it
tightens it. The ``_MIN_SURFACE_LEN`` floor removes single-character noise; the
residual compound-substring over-match is accepted as a known limitation rather
than replaced with a rule that regresses recall. ``resolve_query_concepts``'s
return therefore reflects substring (not token-boundary) resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from src.common.concept_resolver import resolve_concern_id, resolve_goal_id
from src.common.config_loader import load_yaml
from src.common.text_normalize import normalize_text
from src.rec.category_groups import (
    RECOMMEND_CATEGORY_DEFS,
    RECOMMEND_CATEGORY_LABELS,
    classify_product_category_group,
)
from src.rec.ingredient_constraint import (
    IngredientConstraint,
    match_ingredient_constraint,
)
from src.rec.negation import negated_surfaces
from src.rec.recommendation_evidence_index import (
    CandidateEligibility,
    build_candidate_eligibility,
)
from src.rec.semantic_compatibility import normalize_signal_id

# Surface-form tokens shorter than this are excluded from substring scanning
# to avoid single-character noise matches (mirrors the min_label_len=2 floor
# already applied to keyword promotion in src/mart/build_serving_views.py).
_MIN_SURFACE_LEN = 2

# Tier 3 (reverse-containment) cardinality cap: if a colloquial expression is a
# substring of MORE than this many distinct catalog INCI, it is too generic to be
# a specific ingredient intent (오일 → 39 tokens) and adopts NOTHING (surfaced as
# an unresolved chip instead). Specific families stay under it (콜라겐 2 /
# 세라마이드 1 / 펩타이드 8), so the cap admits real ingredient names and rejects
# category-like words. 10 = a conservative ceiling above the observed specific
# families and well below the 오일 (39) over-general case.
_REVERSE_MATCH_CAP = 10


@dataclass(frozen=True)
class MatchedConcept:
    """A single concept resolved from the query text."""

    concept_type: str  # brand | category | ingredient | concern | goal | keyword
    concept_id: str
    matched_text: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {
            "concept_type": self.concept_type,
            "concept_id": self.concept_id,
            "matched_text": self.matched_text,
            "label": self.label,
        }


@dataclass
class SearchResultItem:
    """A product carrying at least one resolved query concept."""

    product_id: str
    product: dict[str, Any]
    matched_concepts: list[str]
    relevance_score: float
    eligibility: CandidateEligibility

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product": self.product,
            # `overlap_concepts` mirrors the /api/recommend result field name so
            # the shared front-end evidence renderer (static/app.js reads
            # `overlap_concepts`) consumes search and recommend results
            # identically. `matched_concepts` is kept as the search-native alias
            # (same value) for callers already keyed on it.
            "overlap_concepts": self.matched_concepts,
            "matched_concepts": self.matched_concepts,
            "relevance_score": self.relevance_score,
            "eligibility": self.eligibility.to_dict(),
        }


@dataclass
class SearchOutcome:
    """Full search response: resolved query concepts + ranked results."""

    query: str
    resolved_concepts: list[MatchedConcept]
    results: list[SearchResultItem]

    @property
    def resolved(self) -> bool:
        return bool(self.resolved_concepts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "resolved": self.resolved,
            "resolved_concepts": [c.to_dict() for c in self.resolved_concepts],
            "result_count": len(self.results),
            "results": [r.to_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Query -> concept resolution
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _concern_surface_dict() -> dict[str, Any]:
    return load_yaml("concern_dict.yaml")


@lru_cache(maxsize=1)
def _goal_alias_dict() -> dict[str, Any]:
    return load_yaml("goal_alias_map.yaml")


@lru_cache(maxsize=1)
def _keyword_surface_dict() -> dict[str, Any]:
    return load_yaml("keyword_surface_map.yaml")


@lru_cache(maxsize=1)
def _ingredient_alias_dict() -> dict[str, Any]:
    """Colloquial ingredient name (관용어) → catalog INCI surface tokens
    (configs/ingredient_alias_map.yaml). Seeded from recommend-agent's
    INGREDIENT_DICT intersected with the catalog's MAIN_INGREDIENT tokens; see the
    file header for provenance + augmentation rules. Consumed by the ingredient
    alias layer in ``resolve_query_concepts``."""
    return load_yaml("ingredient_alias_map.yaml")


def resolve_query_concepts(
    query_text: str,
    products: list[dict[str, Any]],
) -> list[MatchedConcept]:
    """Resolve free-text query into known concepts across six axes.

    - concern / goal / keyword: surface-form dictionaries also consulted by
      ``src/rec/candidate_generator.py`` (concern_dict.yaml, goal_alias_map.yaml,
      keyword_surface_map.yaml). IDs are canonicalized via
      ``resolve_concern_id``/``resolve_goal_id`` so they line up with however a
      product profile stores the same concept.
    - brand / category (literal catalog name) / ingredient: scanned against
      the currently loaded product catalog's own text (brand_name,
      category_name) and, for ingredients, each product's ingredient CONCEPT id
      suffix (which encodes the normalized ingredient name) — there is no static
      surface dictionary for catalog truth, so the catalog itself is the
      vocabulary. The raw ``ingredient_ids`` master array is not used for the
      ingredient axis because it is not positionally aligned with
      ``ingredient_concept_ids`` (see the ingredient-loop comment below). On top
      of the bare axis, an ingredient ALIAS layer (Phase 6 B1) maps colloquial
      names (관용어, e.g. 히알루론) to catalog INCI concept ids via
      ``ingredient_alias_map.yaml``, still gated to catalog-existing ids and
      skipped inside a negation span (see the alias-layer comment below).
    - category (group): the same tab keyword vocabulary as
      ``src/rec/category_groups.py`` (RECOMMEND_CATEGORY_DEFS), so a query can
      resolve to a whole category group the way the demo UI tabs do, even
      though no product literally carries a "concept:Category:skincare" id.

    A token that matches none of the above resolves to nothing for that axis —
    there is no full-text fallback (see module docstring). Resolution is by raw
    substring (``surface in norm_query``), subject to the ``_MIN_SURFACE_LEN``
    floor; the compound-substring over-match this can cause is an accepted known
    limitation (see the module docstring's "Known limitation" note).
    """
    norm_query = normalize_text(query_text or "")
    if not norm_query:
        return []

    found: dict[tuple[str, str], MatchedConcept] = {}

    def _add(concept_type: str, concept_id: str, matched_text: str, label: str) -> None:
        if not concept_id:
            return
        key = (concept_type, concept_id)
        if key not in found:
            found[key] = MatchedConcept(concept_type, concept_id, matched_text, label)

    for surface, entry in _concern_surface_dict().items():
        if not isinstance(entry, dict):
            continue
        surface_norm = normalize_text(str(surface))
        if len(surface_norm) >= _MIN_SURFACE_LEN and surface_norm in norm_query:
            concept_id = resolve_concern_id(str(surface))
            _add("concern", concept_id, str(surface), str(entry.get("label_ko", concept_id)))

    for alias, canonical in _goal_alias_dict().items():
        alias_norm = normalize_text(str(alias))
        if len(alias_norm) >= _MIN_SURFACE_LEN and alias_norm in norm_query:
            concept_id = resolve_goal_id(str(alias))
            _add("goal", concept_id, str(alias), str(canonical))

    for surface, entries in _keyword_surface_dict().items():
        surface_norm = normalize_text(str(surface))
        if len(surface_norm) < _MIN_SURFACE_LEN or surface_norm not in norm_query:
            continue
        for entry in entries or []:
            if isinstance(entry, dict) and entry.get("keyword_id"):
                _add(
                    "keyword",
                    str(entry["keyword_id"]),
                    str(surface),
                    str(entry.get("label_ko", "")),
                )

    # Negated surfaces (RAW query) — shared by the bare ingredient axis (F7) and
    # the alias layer below. An ingredient surface sitting inside a negated word
    # (없는/없이/빼고/제외(한)/프리/free) is refused positive adoption at RESOLUTION
    # level, so "레티놀 없는 크림" never resolves 레티놀 as a wanted ingredient (no
    # reliance on a downstream subtraction step, and every caller — incl. the
    # anonymous /api/search path — is protected).
    negated = negated_surfaces(query_text or "")

    for product in products:
        brand_label = product.get("brand_name") or product.get("brand_id")
        if brand_label:
            label_norm = normalize_text(str(brand_label))
            if len(label_norm) >= _MIN_SURFACE_LEN and label_norm in norm_query:
                for cid in product.get("brand_concept_ids") or []:
                    _add("brand", str(cid), str(brand_label), str(brand_label))

        category_label = product.get("category_name") or product.get("category_id")
        if category_label:
            label_norm = normalize_text(str(category_label))
            if len(label_norm) >= _MIN_SURFACE_LEN and label_norm in norm_query:
                for cid in product.get("category_concept_ids") or []:
                    _add("category", str(cid), str(category_label), str(category_label))

        # Ingredient axis: label/match on each concept id's OWN suffix, which
        # already encodes the normalized ingredient name (product_ingest builds
        # concept:Ingredient:<normalize_text(name)>). The raw ``ingredient_ids``
        # master array is deliberately NOT paired by position here: it and
        # ``ingredient_concept_ids`` are built independently in
        # build_serving_views.py (the concept list is the filtered/reordered
        # HAS_INGREDIENT link subset; the raw list is the original master, which
        # differs in length AND order and includes generic ingredients that never
        # resolved to a concept). Pairing them by index (raw_names[idx]) mislabels
        # concepts, producing both false positives (an unrelated raw name resolves
        # a concept the product carries) and false negatives (the real ingredient
        # name never matches). Deriving the surface from the concept id is
        # self-consistent and confines resolution to exactly the ids
        # ``_product_overlap`` can match.
        for cid in product.get("ingredient_concept_ids") or []:
            cid_str = str(cid)
            if ("ingredient", cid_str) in found:
                continue
            label = _concept_suffix(cid_str)
            label_norm = normalize_text(label)
            if len(label_norm) < _MIN_SURFACE_LEN or label_norm not in norm_query:
                continue
            # F7: same negation-span guard as the alias layer — a bare INCI surface
            # inside a negated word ("레티놀 없는 크림") is not adopted as positive.
            if any(label_norm in neg for neg in negated):
                continue
            _add("ingredient", cid_str, label, label)

    # Ingredient alias layer (Phase 6 B1): colloquial ingredient names (관용어, e.g.
    # 히알루론) → catalog INCI concept ids. The bare ingredient axis above only fires
    # when an INCI's OWN normalized surface appears verbatim in the query; this
    # layer bridges 관용어→INCI via configs/ingredient_alias_map.yaml, building
    # ``concept:Ingredient:<normalize_text(INCI)>`` and adopting ONLY ids that exist
    # on the currently-loaded catalog (same catalog-existence gate the rest of
    # resolution uses — no forged ids). matched_text/label carry the 관용어 (user
    # language) so the resolved chip reads in the user's own words; MatchedConcept's
    # (type, concept_id) dedupe means a concept the bare axis already found is not
    # duplicated.
    #
    # Negation-span guard: adoption is tested on the NORMALIZED query, but negation
    # is tested on the RAW query (markers are surface-adjacent). An alias surface
    # sitting inside a negated word (없는/없이/빼고/제외(한)/프리/free) is not adopted,
    # so "레티놀 없는 크림" / "히알루론 빼고" never pull the negated ingredient in through
    # the alias map. This is a resolution-level defence, so every caller is safe —
    # including the anonymous /api/search path that does not run the LLM negation
    # step. (``negated`` is computed once above, shared with the bare axis.)
    alias_hits: list[tuple[str, Any]] = []
    # F1 (codex): dictionary COVERAGE of the expression — True when any alias key is
    # a substring of the query, independent of the catalog-existence gate and of
    # negation. This gates Tier 3 below: a curated expression (알코올) must suppress
    # the reverse-containment fallback even when its curated target (변성알코올) is
    # ABSENT from the current catalog — otherwise Tier 3 would wrongly sweep in the
    # fatty alcohols the curation deliberately excludes. Adoption still passes
    # through the catalog gate; only Tier 3 suppression keys off coverage.
    alias_covered = False
    for alias_surface, inci_tokens in _ingredient_alias_dict().items():
        surface_norm = normalize_text(str(alias_surface))
        if len(surface_norm) < _MIN_SURFACE_LEN or surface_norm not in norm_query:
            continue
        alias_covered = True
        if any(surface_norm in neg for neg in negated):
            continue
        alias_hits.append((str(alias_surface), inci_tokens or []))

    # F1 longest-match: when one matched alias key is a substring of another
    # (비타민 ⊂ 비타민A, 히알루론 ⊂ 히알루론산), drop the shorter — otherwise
    # "비타민A 든거" would fire BOTH 비타민(→비타민C 계열) and 비타민A(→레티놀), binding
    # two different families as an AND. For same-INCI nested pairs (히알루론산 group)
    # either survivor yields the identical concept ids. Compared on normalized keys.
    if len(alias_hits) > 1:
        matched_norms = [normalize_text(surface) for surface, _ in alias_hits]
        alias_hits = [
            (surface, tokens)
            for surface, tokens in alias_hits
            if not any(
                normalize_text(surface) != other and normalize_text(surface) in other
                for other in matched_norms
            )
        ]

    if alias_hits:
        catalog_ingredient_ids = {
            str(cid)
            for product in products
            for cid in (product.get("ingredient_concept_ids") or [])
        }
        for surface, inci_tokens in alias_hits:
            for inci in inci_tokens:
                concept_id = f"concept:Ingredient:{normalize_text(str(inci))}"
                if concept_id in catalog_ingredient_ids:
                    _add("ingredient", concept_id, surface, surface)

    # Ingredient Tier 3 (reverse containment): the dictionary handles names string
    # matching can't bridge (히알루론↔하이알루로네이트) and curation (알코올=변성알코올 only);
    # THIS tier normalizes a colloquial expression that string-CONTAINS INTO a catalog
    # INCI ('콜라겐' ⊂ '솔루블콜라겐') WITHOUT needing a dictionary entry — so an LLM-
    # extracted ingredient (or a single-word query) is not silently dropped just
    # because it lacks an alias key. Fires ONLY when the bare axis (①) resolved NO
    # ingredient AND the expression is NOT dictionary-covered (``alias_covered``,
    # F1) — so a curated expression always defers to the dictionary (알코올 →
    # 변성알코올 alone; Tier 3 never adds the fatty alcohols on top, even when the
    # curated target is missing from the catalog). Self-limiting: a term only
    # contains-INTO a single spaceless INCI token when it is a short isolated
    # expression (an LLM slot item, a negation group1, a single-word query); a full
    # multi-word query is a substring of no catalog token ("알콜업는 스킨케어" ⊄ any),
    # so no path flag is needed.
    if not alias_covered and not any(ctype == "ingredient" for ctype, _cid in found):
        if len(norm_query) >= _MIN_SURFACE_LEN and not any(norm_query in neg for neg in negated):
            # F4: scan distinct catalog INCI suffixes, stopping as soon as the count
            # exceeds the cap (an over-general expression like 오일 → 39 INCI adopts
            # nothing and stays honestly unresolved). No full materialize/sort in the
            # reject case. NOTE (45k scale): replace this per-call linear scan with a
            # suffix index rebuilt alongside the serving-store refresh; unnecessary at
            # demo scale.
            catalog_concept_ids = {
                str(cid)
                for product in products
                for cid in (product.get("ingredient_concept_ids") or [])
            }
            reverse_ids: list[str] = []
            over_cap = False
            for cid in catalog_concept_ids:
                suffix = normalize_text(_concept_suffix(cid))
                if suffix != norm_query and norm_query in suffix:
                    reverse_ids.append(cid)
                    if len(reverse_ids) > _REVERSE_MATCH_CAP:
                        over_cap = True
                        break
            if reverse_ids and not over_cap:
                # sorted → deterministic adoption (set iteration order is arbitrary).
                for cid in sorted(reverse_ids):
                    _add("ingredient", cid, norm_query, norm_query)

    for group, keyword in _matching_category_groups(norm_query):
        _add(
            "category",
            f"concept:Category:{group}",
            keyword,
            RECOMMEND_CATEGORY_LABELS.get(group, group),
        )

    return list(found.values())


def _matching_category_groups(norm_query: str) -> list[tuple[str, str]]:
    """Every recommendation category group whose tab keyword vocabulary
    appears in the normalized query.

    Unlike ``classify_product_category_group`` (single best label for a
    product, in priority order), search wants recall across every group a
    query plausibly mentions — a query can legitimately span multiple tabs.
    Reuses the same ``RECOMMEND_CATEGORY_DEFS`` keyword table, just applied
    plurally instead of picking one winner.
    """
    matches: list[tuple[str, str]] = []
    for item in RECOMMEND_CATEGORY_DEFS:
        group = str(item["group"])
        if group in {"all", "other"}:
            continue
        for kw in item.get("keywords", ()):
            kw_norm = normalize_text(str(kw))
            if kw_norm and kw_norm in norm_query:
                matches.append((group, str(kw)))
                break
    return matches


def _concept_suffix(concept_id: str) -> str:
    """Strip a ``concept:Type:value`` prefix for display, if present."""
    parts = concept_id.split(":", 2)
    return parts[2] if len(parts) == 3 else concept_id


def _signal_ids(items: Any) -> set[str]:
    """Extract ids from a serving-profile signal-summary list.

    Elements are normally ``{"id": ..., "score": ...}`` dicts, but the
    consumer contract (docs/architecture/db_consumer_contract.md §3.3) allows
    plain strings too, so both are accepted.
    """
    out: set[str] = set()
    for item in items or []:
        if isinstance(item, dict):
            got = item.get("id")
            if isinstance(got, str) and got:
                out.add(got)
        elif isinstance(item, str) and item:
            out.add(item)
    return out


# ---------------------------------------------------------------------------
# Concept -> product overlap + ranking
# ---------------------------------------------------------------------------

def _product_overlap(product: dict[str, Any], resolved: list[MatchedConcept]) -> list[str]:
    """Overlap-style ``"type:concept_id"`` strings for every resolved query
    concept this product actually carries.

    Same string shape as ``candidate_generator.generate_candidates``'s overlap
    concepts, so ``build_candidate_eligibility`` classifies evidence families
    (PRODUCT_MASTER_TRUTH / REVIEW_GRAPH_RELATION) identically to a
    recommendation result.
    """
    brand_ids = {str(v) for v in (product.get("brand_concept_ids") or [])}
    category_ids = {str(v) for v in (product.get("category_concept_ids") or [])}
    ingredient_ids = {str(v) for v in (product.get("ingredient_concept_ids") or [])}
    product_group_concept = f"concept:Category:{classify_product_category_group(product)}"

    concern_ids_norm = {
        resolve_concern_id(cid) for cid in _signal_ids(product.get("top_concern_pos_ids"))
    }
    benefit_values = product.get("main_benefit_concept_ids") or product.get("main_benefit_ids") or []
    goal_ids_norm = {resolve_goal_id(str(g)) for g in benefit_values}
    keyword_keys = {normalize_signal_id(kid) for kid in _signal_ids(product.get("top_keyword_ids"))}

    # Category axis dedupe: a resolved category-*group* concept (e.g.
    # concept:Category:makeup) is derived FROM the product's own literal category
    # via classify_product_category_group, so when the product already matches a
    # literal category concept the group match is the same categorical dimension
    # counted twice. Left un-deduped it inflates the overlap-count relevance
    # (len(overlap)) and biases ranking toward products that happen to match both
    # forms. Emit literal category matches always; emit the derived-group match
    # only when no literal category matched for this product.
    category_literal_hit = any(
        concept.concept_type == "category" and concept.concept_id in category_ids
        for concept in resolved
    )

    overlap: list[str] = []
    for concept in resolved:
        if concept.concept_type == "brand":
            if concept.concept_id in brand_ids:
                overlap.append(f"brand:{concept.concept_id}")
        elif concept.concept_type == "category":
            if concept.concept_id in category_ids:
                overlap.append(f"category:{concept.concept_id}")
            elif concept.concept_id == product_group_concept and not category_literal_hit:
                overlap.append(f"category:{concept.concept_id}")
        elif concept.concept_type == "ingredient":
            if concept.concept_id in ingredient_ids:
                overlap.append(f"ingredient:{concept.concept_id}")
        elif concept.concept_type == "concern":
            if concept.concept_id in concern_ids_norm:
                overlap.append(f"concern:{concept.concept_id}")
        elif concept.concept_type == "goal":
            if concept.concept_id in goal_ids_norm:
                overlap.append(f"goal_master:{concept.concept_id}")
        elif concept.concept_type == "keyword":
            if normalize_signal_id(concept.concept_id) in keyword_keys:
                overlap.append(f"keyword:{concept.concept_id}")
    return overlap


def search_products(
    query_text: str,
    products: list[dict[str, Any]],
    *,
    max_results: int = 20,
    avoided_ingredient_concept_ids: list[str] | None = None,
    ingredient_constraints: list[IngredientConstraint] | None = None,
) -> SearchOutcome:
    """Concept-based product search (evidence-first; no full-text fallback).

    1. Resolve the query into known concepts (``resolve_query_concepts``). If
       nothing resolves, return an empty, explicitly-unresolved outcome —
       callers (the API layer) surface this as guidance, not a silent empty
       list.
    2. Rank every product carrying at least one resolved concept by overlap
       count — simple relevance, not the full recommendation scorer (no user
       profile is involved; search is anonymous-safe).

    ``avoided_ingredient_concept_ids`` — optional hard filter (Phase 6 B2, for
    negation queries like "레티놀 없는 크림"): any product whose
    ``ingredient_concept_ids`` intersects this set is skipped entirely, never
    ranked. Mirrors the recommendation candidate generator's avoided-ingredient
    hard filter so search and recommend honour a negated ingredient identically.
    Defaults to ``None`` so existing callers are unaffected.

    ``ingredient_constraints`` — optional wanted-ingredient hard gate (Phase 6
    B2): a product must satisfy EVERY family (AND) via the shared matcher
    (structured ∪ name). A product satisfying a family only by its
    ``representative_product_name`` gets an extra ``product_name:<관용어>`` overlap
    axis so it clears the "overlap ≥ 1" / evidence gate (classified
    PRODUCT_MASTER_TRUTH). Defaults to ``None`` (no gate) so existing callers are
    byte-identical. Callers pass only ``provenance == "raw"`` constraints. When
    constraints are present the empty-resolution short-circuit is skipped and the
    families' INCI are synthesized as ingredient concepts (F3, codex), so an
    ingredient-only query the LLM isolated but the multi-word text can't re-resolve
    ("콜라겐 추천해줘") still ranks carriers and reports ``resolved``.
    """
    resolved = resolve_query_concepts(query_text, products)
    constraints = list(ingredient_constraints or [])

    # F3: synthesize the constraint families' INCI as ingredient concepts so a
    # STRUCTURED carrier earns an ``ingredient:<concept_id>`` overlap via
    # ``_product_overlap`` (name carriers earn ``product_name:<label>`` below), even
    # when the full-query re-resolution above found nothing. The AND hard gate still
    # runs per product, so a synthesized concept never admits a non-carrier.
    if constraints:
        seen = {(c.concept_type, c.concept_id) for c in resolved}
        for constraint in constraints:
            for cid in constraint.inci_concept_ids:
                if ("ingredient", cid) not in seen:
                    seen.add(("ingredient", cid))
                    resolved.append(MatchedConcept("ingredient", cid, constraint.label, constraint.label))

    if not resolved:
        return SearchOutcome(query=query_text, resolved_concepts=[], results=[])

    avoided = {str(cid) for cid in (avoided_ingredient_concept_ids or []) if cid}

    items: list[SearchResultItem] = []
    for product in products:
        # Avoided-ingredient hard filter (concept-id join, same id space the
        # ingredient axis resolves): a product carrying an avoided ingredient is
        # excluded before ranking, not merely down-ranked.
        if avoided and {str(v) for v in (product.get("ingredient_concept_ids") or [])} & avoided:
            continue

        # Wanted-ingredient hard gate (AND across families). A product failing any
        # family is excluded before ranking; name-only carriers earn a
        # product_name overlap axis so they survive the "overlap ≥ 1" gate below.
        name_labels: list[str] = []
        if constraints:
            axes = [match_ingredient_constraint(product, c) for c in constraints]
            if any(axis is None for axis in axes):
                continue
            seen_label: set[str] = set()
            for constraint, axis in zip(constraints, axes):
                if axis == "name" and constraint.label not in seen_label:
                    seen_label.add(constraint.label)
                    name_labels.append(constraint.label)

        overlap = _product_overlap(product, resolved)
        overlap.extend(f"product_name:{label}" for label in name_labels)
        if not overlap:
            continue
        items.append(
            SearchResultItem(
                product_id=str(product.get("product_id", "")),
                product=product,
                matched_concepts=overlap,
                relevance_score=float(len(overlap)),
                eligibility=build_candidate_eligibility(overlap),
            )
        )

    items.sort(key=lambda item: (-item.relevance_score, item.product_id))
    return SearchOutcome(
        query=query_text,
        resolved_concepts=resolved,
        results=items[: max(0, max_results)],
    )
