# Recommendation Contract And Strength Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix measured recommendation-layer contract gaps without fabricating evidence or overfitting sparse categories.

**Architecture:** Keep product-master truth, review-graph evidence, purchase behavior, and source review stats in their current roles. Repair the typed contracts and scoring/explanation handoff so existing high-quality semantic evidence keeps its configured strength and remains visible to the user. Do not force fragrance/scalp graph evidence when the fixture does not contain aligned graph signals.

**Tech Stack:** Python recommendation pipeline, YAML scoring/rule configs, pytest, static JS recommendation tester.

---

## Implementation Result

Implemented on 2026-06-22.

- `OWNS_PRODUCT`, `OWNS_FAMILY`, and `REPURCHASES_FAMILY` now preserve `ENTITY` object refs through user fact canonicalization.
- Semantic compatibility overlap concepts now carry bounded `strength` metadata, and scoring uses that strength instead of flat semantic counts.
- Semantic and weak semantic review graph matches now participate in score-faithful explanations.
- `review_graph_weak_relation_match` is an explicit backend/YAML/frontend scoring contract; the previous hidden fallback weight was removed.
- Dense golden fixture generation now uses `load_source_review_stats_snapshot()` so ambiguous source stats product ids are skipped consistently.
- Recommendation audit now reports purchase path count and purchase-history contribution count without treating `novelty_bonus` as purchase history.
- Reranker now separates displayed `final_score` from diversity-adjusted `rank_score`; ranking can still use diversity while UI/API match scores do not become negative.
- 2026-06-23 follow-up: `purchase_analysis.active_product_category` is now
  `ACTIVE_IN_CATEGORY`, not `PREFERS_CATEGORY`. It can add weak
  `active_category_affinity` under `profile_fit_score`, but is excluded from
  evidence eligibility and is shown separately in explanations/user graphs.
- 2026-06-23 follow-up: recommendation tester labels `final_score` as
  recommendation score and `rank_score` as diversity-adjusted sorting score, so
  visible score/rank differences are intentional and traceable.
- 2026-06-23 follow-up: user keyword preferences can now match product-master
  name/category text via `catalog_keyword_match`; repurchase category behavior
  can match the same catalog text via `repurchase_category_affinity`. This
  repairs a measured gap where makeup preferences such as `틴트` existed in the
  user profile and product taxonomy/name, but not in promoted review keywords.

Fresh verification:

```bash
python -m pytest -q
# 765 passed, 36 skipped

python scripts/build_dense_golden_fixture.py --dry-run
# 906 reviews, 32 selected products, 6 users

python scripts/audit_recommendation_evidence.py --fixture dense_golden --kg-mode on --top-k 5
# 42 scenarios; graph evidence remains present where data supports it

git diff --check
# no output
```

## Measured Baseline

Commands run on 2026-06-22:

```bash
python scripts/build_dense_golden_fixture.py --dry-run
python scripts/audit_recommendation_evidence.py --fixture dense_golden --kg-mode on --top-k 5
python scripts/audit_recommendation_evidence.py --fixture dense_golden --kg-mode off --top-k 5 --category-group all
python -m pytest tests/test_recommendation_semantic_compatibility.py tests/test_recommendation_contract_consistency.py tests/test_user_adapter_semantics.py tests/test_profile_purchase_summary.py tests/test_reranker.py -q
```

Observed:

- Dense golden fixture: 906 reviews, 32 products, 6 users.
- Category product counts: skincare 11, makeup 6, bodycare 5, haircare 5, fragrance 5.
- Purchase anchor resolution: resolved 1, ambiguous 0, unresolved 18.
- Current focused tests: 27 passed.
- Source stats snapshot has one duplicate product id: `35119`, and it is not selected by the current dense fixture.
- `kg-mode on/off` audit currently reads the same dense serving profile surface, so all-category output can be identical; this is an audit naming/interpretation issue, not proof that graph evidence is unused.

Measured code gaps:

- `src/user/adapters/personal_agent_adapter.py` emits purchase product/family facts with `object_ref_kind = "ENTITY"`, but `src/user/canonicalize_user_facts.py` overwrites all adapted facts to `ObjectRefKind.CONCEPT`.
- `configs/recommendation_semantic_compatibility.yaml` defines per-match `strength`, and `src/rec/semantic_compatibility.py` stores it in `SemanticCompatibilityMatch`, but `to_overlap_concept()` drops it before `src/rec/scorer.py`.
- `src/rec/scorer.py` computes `review_graph_weak_relation_match`, but the feature is absent from `SCORING_FEATURE_KEYS`, `configs/scoring_weights.yaml`, and `src/static/app.js`.
- `src/rec/explainer.py` does not map `semantic_keyword`, `semantic_bee_attr`, `weak_semantic_keyword`, or `weak_semantic_bee_attr`, so scored semantic review evidence can be invisible in explanation paths.
- `scripts/build_dense_golden_fixture.py` loads source stats via a raw dict comprehension instead of `load_source_review_stats_snapshot()`, bypassing the duplicate/ambiguous product-id contract used elsewhere.

## Scope Decisions

Do:

- Treat entity/concept ref-kind mismatch as a hard contract bug.
- Preserve and score configured semantic `strength`.
- Keep weak review evidence low-weight and explicit.
- Make semantic review evidence explainable.
- Keep purchase behavior exact-match only; add diagnostics/tests, not fake matches.
- Use the existing source stats loader in dense fixture generation.

Do not:

- Add graph evidence to fragrance or scalp profiles just because current fixture lacks it.
- Fuzzy-match purchase summaries into product ids.
- Promote source stats into eligibility evidence.
- Change AmoreSimulation DB contract.
- Lower graph promotion thresholds to inflate coverage.

## File Map

- Modify `src/user/canonicalize_user_facts.py`: preserve adapted `object_ref_kind` with enum normalization.
- Modify `tests/test_user_adapter_semantics.py`: add adapter plus canonicalizer contract regression.
- Modify `src/rec/semantic_compatibility.py`: encode semantic strength into overlap strings in a backward-compatible format.
- Modify `src/rec/scorer.py`: parse overlap strength and use weighted semantic scores.
- Modify `src/rec/explainer.py`: map semantic overlap types to explanation edges/features and strip metadata for display.
- Modify `tests/test_recommendation_semantic_compatibility.py`: assert strength affects scoring and explanations.
- Modify `configs/scoring_weights.yaml`: add explicit weak review graph feature weight.
- Modify `src/static/app.js`: add matching frontend default weight and metadata.
- Modify `tests/test_recommendation_contract_consistency.py`: keep YAML/backend/frontend contract check green.
- Modify `scripts/build_dense_golden_fixture.py`: load stats through `load_source_review_stats_snapshot()`.
- Modify `tests/test_dense_golden_fixture.py`: assert dense builder respects ambiguous stats skip behavior.
- Optional small cleanup in `src/rec/reranker.py` and `tests/test_reranker.py`: separate display score from diversity ranking score if negative displayed scores remain reproducible after main fixes.

## Task 1: Repair User Fact Ref-Kind Contract

**Files:**
- Modify: `src/user/canonicalize_user_facts.py`
- Modify: `tests/test_user_adapter_semantics.py`

- [ ] **Step 1: Write failing regression**

Add a test that adapts purchase features, canonicalizes them, and asserts product/family ownership facts remain entity references:

```python
from src.common.enums import ObjectRefKind
from src.user.canonicalize_user_facts import canonicalize_user_facts


def test_canonicalize_preserves_purchase_entity_ref_kind():
    profile = _make_profile()
    purchase_features = {
        "owned_product_ids": ["P001"],
        "owned_family_ids": ["FAM001"],
        "repurchased_family_ids": ["FAM002"],
        "repurchased_brand_ids": [],
        "recently_purchased_brand_ids": [],
    }
    adapted = adapt_user_profile("u1", profile, purchase_features=purchase_features)

    facts = canonicalize_user_facts("u1", adapted)
    by_predicate = {fact["predicate"]: fact for fact in facts if fact["predicate"].startswith(("OWNS_", "REPURCHASES_FAMILY"))}

    assert by_predicate["OWNS_PRODUCT"]["object_ref_kind"] == ObjectRefKind.ENTITY
    assert by_predicate["OWNS_FAMILY"]["object_ref_kind"] == ObjectRefKind.ENTITY
    assert by_predicate["REPURCHASES_FAMILY"]["object_ref_kind"] == ObjectRefKind.ENTITY
```

- [ ] **Step 2: Verify failure**

Run:

```bash
python -m pytest tests/test_user_adapter_semantics.py::test_canonicalize_preserves_purchase_entity_ref_kind -q
```

Expected before implementation: failure showing `ObjectRefKind.CONCEPT`.

- [ ] **Step 3: Implement minimal fix**

In `src/user/canonicalize_user_facts.py`, add a local normalizer:

```python
def _object_ref_kind(value: Any) -> ObjectRefKind:
    if isinstance(value, ObjectRefKind):
        return value
    if isinstance(value, str):
        try:
            return ObjectRefKind(value)
        except ValueError:
            return ObjectRefKind.CONCEPT
    return ObjectRefKind.CONCEPT
```

Then replace the hardcoded field:

```python
"object_ref_kind": _object_ref_kind(af.get("object_ref_kind")),
```

- [ ] **Step 4: Run focused tests**

```bash
python -m pytest tests/test_user_adapter_semantics.py tests/test_profile_purchase_summary.py tests/test_family_id_normalization.py -q
```

Expected: all pass.

## Task 2: Preserve Semantic Strength Through Candidate Overlap

**Files:**
- Modify: `src/rec/semantic_compatibility.py`
- Modify: `src/rec/scorer.py`
- Modify: `tests/test_recommendation_semantic_compatibility.py`

- [ ] **Step 1: Write failing scoring test**

Add a test proving different configured strengths produce different scores:

```python
def test_semantic_strength_changes_review_graph_score():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:산뜻", "weight": 1.0}])
    strong_product = _product("strong", top_keyword_ids=[{"id": "concept:Keyword:흡수", "score": 0.9, "review_cnt": 8}])
    weak_product = _product("weak", top_keyword_ids=[{"id": "concept:Keyword:끈적임 없음", "score": 0.9, "review_cnt": 8}])

    strong_candidate = generate_candidates(user, [strong_product], mode=RecommendationMode.EXPLORE)[0]
    weak_candidate = generate_candidates(user, [weak_product], mode=RecommendationMode.EXPLORE)[0]

    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 1.0}, shrinkage_k=0)

    strong = scorer.score(user, strong_product, strong_candidate.overlap_concepts)
    weak = scorer.score(user, weak_product, weak_candidate.overlap_concepts)

    assert any("|strength=" in concept for concept in strong_candidate.overlap_concepts + weak_candidate.overlap_concepts)
    assert strong.final_score > weak.final_score
```

- [ ] **Step 2: Verify failure**

```bash
python -m pytest tests/test_recommendation_semantic_compatibility.py::test_semantic_strength_changes_review_graph_score -q
```

Expected before implementation: strength metadata absent and/or scores equal by count.

- [ ] **Step 3: Encode strength in semantic overlap strings**

Change `SemanticCompatibilityMatch.to_overlap_concept()`:

```python
def to_overlap_concept(self) -> str:
    strength = _bounded_strength(self.strength)
    return f"{self.overlap_type}:{self.axis}:{self.value}:{self.product_id}|strength={strength:.4f}"
```

This keeps the existing prefix contract (`semantic_keyword:...`) intact.

- [ ] **Step 4: Parse weighted overlaps in scorer**

Add helpers in `src/rec/scorer.py`:

```python
def _parse_overlap_concept(concept: str) -> tuple[str, float]:
    ctype = concept.split(":", 1)[0] if ":" in concept else "other"
    strength = 1.0
    if "|strength=" in concept:
        raw_strength = concept.rsplit("|strength=", 1)[1]
        try:
            strength = float(raw_strength)
        except ValueError:
            strength = 1.0
    return ctype, max(0.0, min(strength, 1.0))
```

Use it in `Scorer.score()`:

```python
overlaps_by_type: dict[str, int] = {}
overlap_strength_by_type: dict[str, float] = {}
for concept in (overlap_concepts or []):
    ctype, strength = _parse_overlap_concept(concept)
    overlaps_by_type[ctype] = overlaps_by_type.get(ctype, 0) + 1
    overlap_strength_by_type[ctype] = overlap_strength_by_type.get(ctype, 0.0) + strength
```

Then compute semantic features with strength:

```python
semantic_keyword_strength = overlap_strength_by_type.get("semantic_keyword", 0.0)
semantic_bee_attr_strength = overlap_strength_by_type.get("semantic_bee_attr", 0.0)
weak_relation_strength = (
    overlap_strength_by_type.get("weak_semantic_keyword", 0.0)
    + overlap_strength_by_type.get("weak_semantic_bee_attr", 0.0)
)
keyword_score_units = overlaps_by_type.get("keyword", 0) + semantic_keyword_strength
bee_attr_score_units = overlaps_by_type.get("bee_attr", 0) + semantic_bee_attr_strength
residual_attr = max(0.0, bee_attr_score_units - keyword_score_units)
```

Use `keyword_score_units`, `residual_attr`, and `weak_relation_strength` in the feature dict.

- [ ] **Step 5: Run focused tests**

```bash
python -m pytest tests/test_recommendation_semantic_compatibility.py tests/test_concern_context_matching.py tests/test_score_layers.py -q
```

Expected: all pass.

## Task 3: Restore Semantic Evidence Explanations

**Files:**
- Modify: `src/rec/explainer.py`
- Modify: `tests/test_recommendation_semantic_compatibility.py`

- [ ] **Step 1: Write failing explanation test**

```python
def test_semantic_review_graph_match_is_explainable():
    user = _user(preferred_keyword_ids=[{"id": "concept:Keyword:촉촉", "weight": 1.0}])
    product = _product(top_keyword_ids=[{"id": "concept:Keyword:보습", "score": 0.9, "review_cnt": 8}])
    candidate = generate_candidates(user, [product], mode=RecommendationMode.EXPLORE)[0]

    scorer = Scorer()
    scorer.load_from_dict({"keyword_match": 1.0}, shrinkage_k=0)
    scored = scorer.score(user, product, candidate.overlap_concepts)

    from src.rec.explainer import explain
    explanation = explain(scored, candidate.overlap_concepts)

    assert explanation.paths
    assert explanation.paths[0].concept_type == "semantic_keyword"
```

- [ ] **Step 2: Verify failure**

```bash
python -m pytest tests/test_recommendation_semantic_compatibility.py::test_semantic_review_graph_match_is_explainable -q
```

Expected before implementation: `explanation.paths == []`.

- [ ] **Step 3: Add semantic mappings**

In `src/rec/explainer.py`, extend `_EDGE_MAP`:

```python
"semantic_keyword": ("PREFERS_KEYWORD", "HAS_BEE_KEYWORD_SIGNAL"),
"semantic_bee_attr": ("PREFERS_BEE_ATTR", "HAS_BEE_ATTR_SIGNAL"),
"weak_semantic_keyword": ("PREFERS_KEYWORD", "HAS_WEAK_BEE_KEYWORD_SIGNAL"),
"weak_semantic_bee_attr": ("PREFERS_BEE_ATTR", "HAS_WEAK_BEE_ATTR_SIGNAL"),
```

Extend `_concept_to_feature()`:

```python
"semantic_keyword": "keyword_match",
"semantic_bee_attr": "residual_bee_attr_match",
"weak_semantic_keyword": "review_graph_weak_relation_match",
"weak_semantic_bee_attr": "review_graph_weak_relation_match",
```

When building `ExplanationPath`, strip `|strength=...` metadata from `concept_id`:

```python
cid = cid.split("|strength=", 1)[0]
```

- [ ] **Step 4: Run explanation tests**

```bash
python -m pytest tests/test_recommendation_semantic_compatibility.py tests/test_recommendation_contract_consistency.py -q
```

Expected: all pass.

## Task 4: Make Weak Review Feature Explicit In The Contract

**Files:**
- Modify: `src/rec/scorer.py`
- Modify: `configs/scoring_weights.yaml`
- Modify: `src/static/app.js`
- Modify: `tests/test_recommendation_contract_consistency.py`

- [ ] **Step 1: Add explicit contract entries**

Add `review_graph_weak_relation_match` to `SCORING_FEATURE_KEYS`.

Add to YAML:

```yaml
  review_graph_weak_relation_match: 0.02
```

Add to frontend `DEFAULT_WEIGHTS` and `WEIGHT_META`:

```javascript
review_graph_weak_relation_match: 0.02,
```

```javascript
review_graph_weak_relation_match: { label: '약한 리뷰관계', group: 'core', desc: '승격되지 않은 long-tail 리뷰 신호가 유저 선호와 의미적으로 맞을 때만 낮은 가중치로 반영.' },
```

- [ ] **Step 2: Remove hidden fallback**

Replace `_feature_weight()` with direct configured weight lookup:

```python
def _feature_weight(feature: str, weights: dict[str, float]) -> float:
    return weights.get(feature, 0.0)
```

- [ ] **Step 3: Run contract tests**

```bash
python -m pytest tests/test_recommendation_contract_consistency.py tests/test_recommendation_semantic_compatibility.py -q
```

Expected: backend YAML and frontend default weights match exactly.

## Task 5: Keep Purchase Behavior Honest And Measurable

**Files:**
- Modify: `scripts/audit_recommendation_evidence.py`
- Modify: `tests/test_golden_profile_recommendation_audit.py`

- [ ] **Step 1: Add audit fields, not scoring hacks**

Expose these fields per scenario:

```python
"purchase_path_count": sum(len(item.get("eligibility", {}).get("purchase_paths", [])) for item in top_items),
"purchase_score_nonzero_count": sum(
    1
    for item in top_items
    if any(
        abs((item.get("feature_contributions") or {}).get(feature, 0.0)) > 0
        for feature in (
            "purchase_loyalty_score",
            "exact_owned_penalty",
            "owned_family_penalty",
            "same_family_explore_bonus",
            "repurchase_family_affinity",
        )
    )
),
```

- [ ] **Step 2: Test field presence only**

In `tests/test_golden_profile_recommendation_audit.py`, assert the audit report includes integer purchase diagnostics. Do not assert a minimum nonzero count while dense anchor resolution is 1/19. The score count intentionally excludes `novelty_bonus`, because novelty can be nonzero even when no product/family/brand purchase history resolved.

```python
assert isinstance(scenario["purchase_path_count"], int)
assert isinstance(scenario["purchase_score_nonzero_count"], int)
```

- [ ] **Step 3: Run audit tests**

```bash
python -m pytest tests/test_golden_profile_recommendation_audit.py -q
```

Expected: pass without requiring fake purchase signal coverage.

## Task 6: Use Source Stats Loader In Dense Fixture Builder

**Files:**
- Modify: `scripts/build_dense_golden_fixture.py`
- Modify: `tests/test_dense_golden_fixture.py`

- [ ] **Step 1: Replace raw stats dict build**

Import and use the canonical loader:

```python
from src.loaders.source_review_stats_loader import load_source_review_stats_snapshot
```

In `build_fixture()`:

```python
stats_by_product = load_source_review_stats_snapshot(inputs.stats_path)
```

Remove the raw `_extract_records(_load_json(inputs.stats_path))` path for source stats.

- [ ] **Step 2: Add duplicate-skip regression**

Add a test that feeds a tiny stats snapshot with two rows for the same `product_id` but different source identities and asserts the ambiguous product is not selected.

- [ ] **Step 3: Run dense fixture checks**

```bash
python scripts/build_dense_golden_fixture.py --dry-run
python -m pytest tests/test_dense_golden_fixture.py tests/test_source_review_stats_loader.py -q
```

Expected:

- Dry-run remains 906 reviews, 32 selected products, 6 users.
- Duplicate product id `35119` remains unselected.
- Loader contract tests pass.

## Task 7: Optional Reranker Display Cleanup

**Trigger:** Do this only if current manual/audit output still displays negative `final_score` after Tasks 1-6. This is presentation quality, not evidence correctness.

**Files:**
- Modify: `src/rec/reranker.py`
- Modify: `src/web/server.py`
- Modify: `src/static/app.js`
- Modify: `tests/test_reranker.py`

Plan:

- Keep greedy diversity selection based on adjusted rank score.
- Return original scorer `final_score` as displayed match score.
- Add `rank_score` for diversity-adjusted ordering.
- Keep `diversity_bonus` as the transparent delta.

Validation:

```bash
python -m pytest tests/test_reranker.py -q
```

## Verification Suite

Run after implementation:

```bash
python -m pytest tests/test_user_adapter_semantics.py tests/test_profile_purchase_summary.py tests/test_recommendation_semantic_compatibility.py tests/test_recommendation_contract_consistency.py tests/test_dense_golden_fixture.py tests/test_golden_profile_recommendation_audit.py tests/test_source_review_stats_loader.py tests/test_score_layers.py tests/test_family_id_normalization.py -q
python scripts/build_dense_golden_fixture.py --dry-run
python scripts/audit_recommendation_evidence.py --fixture dense_golden --kg-mode on --top-k 5
python -m pytest -q
git diff --check
```

Expected final acceptance:

- No adapted product/family purchase fact is canonicalized as `CONCEPT`.
- Semantic compatibility overlap strings carry bounded strength metadata.
- Strong semantic evidence scores higher than weaker semantic evidence under equal support.
- Semantic review graph matches appear in explanation paths.
- Weak review evidence has an explicit backend/YAML/frontend contract.
- Purchase behavior diagnostics are visible, but no fake minimum usage is enforced.
- Dense fixture builder uses the same source stats ambiguity handling as production/local loaders.
- Full test suite passes.

## Review Checklist

- Confirm no DB schema or AmoreSimulation contract change.
- Confirm source stats are still scoring/trust only, not eligibility.
- Confirm fragrance/scalp graph absence is documented as current data reality, not treated as failure.
- Confirm no fuzzy purchase summary matching was added.
- Confirm all new features are visible in `score_layers`, `feature_contributions`, explanations, or audit output.
