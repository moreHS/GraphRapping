# Dense Golden Personalization Evidence Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make GraphRapping's promoted review signals, product master truth, review summaries, and personal-agent user profiles usable for meaningful recommendation testing without corrupting the wide 906-review source-identity baseline.

**Architecture:** Preserve the current 906-review/517-product fixture as the wide source-identity baseline. Add a deterministic dense golden fixture for recommendation quality tests, replace default golden user profiles with the final six personal-agent profiles, and update recommendation matching so review graph evidence is used through value-and-polarity compatible semantics rather than broad axis-only matches. Add audit scripts and tests that show evidence density and recommendation evidence-family usage.

**Tech Stack:** Python 3.11, existing GraphRapping loaders/pipeline, JSON fixtures, YAML configs, pytest, FastAPI demo server.

---

## 0. Confirmed Baseline And Constraints

The current fixture is useful but not sufficient for recommendation quality checks:

- `mockdata/review_triples_raw.json`: 906 reviews
- distinct product ids in current fixture: 517
- median fixture reviews per product: 1
- products with at least 3 fixture reviews: 67
- current `kg_on` promoted serving relation density is low: BEE on 26 products, keyword on 0 products
- current `kg_off` keyword signal proves keyword information exists in the legacy path, but it is not available in `kg_on`

Dry simulation result for a dense remap into 33 products:

- average fixture reviews per selected product: about 27.5
- `kg_on` promoted aggregate rows: 65 to 954
- `kg_on` promoted BEE product coverage: 26 to 32
- `kg_off` promoted keyword product coverage: 5 to 17

Hard constraints:

- Do not delete or overwrite the 517-product wide baseline.
- Do not lower production promotion thresholds as the first fix.
- Do not use broad axis-only matching such as formulation to texture evidence.
- Do not treat source review count/rating as graph evidence.
- Do not change AmoreSimulation DB contract in this work.
- Review summary sidecar remains a product-scoped attachment, not graph facts.

## 1. File Structure

Create:

- `mockdata/dense_golden/review_triples_raw.json`
- `mockdata/dense_golden/product_catalog_es.json`
- `mockdata/dense_golden/user_profiles_normalized.json`
- `mockdata/dense_golden/manifest.json`
- `scripts/build_dense_golden_fixture.py`
- `scripts/audit_recommendation_evidence.py`
- `configs/recommendation_semantic_compatibility.yaml`
- `tests/test_dense_golden_fixture.py`
- `tests/test_recommendation_semantic_compatibility.py`
- `tests/test_kg_on_keyword_projection.py`
- `tests/test_golden_profile_recommendation_audit.py`

Modify:

- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- `src/kg/adapter.py`
- `src/web/server.py`
- `src/web/state.py`
- `src/loaders/user_loader.py`
- `scripts/sync_user_profiles.py`
- `docs/architecture/v260605_906_fixture_lineage.md`
- `docs/architecture/personal_agent_profile_graph_usage_2026_06_19.md`

Keep unchanged except for references/docs:

- `mockdata/review_triples_raw.json`
- `mockdata/product_catalog_es.json`
- `data/source_snapshots/product_review_stats_snowflake_latest.json`
- DB DDL and AmoreSimulation-facing contract

## 2. Task 1: Dense Golden Fixture Builder

**Purpose:** Build a deterministic recommendation-quality fixture without replacing the current wide baseline.

**Selection rule:**

- Read product truth from `mockdata/product_catalog_es.json`.
- Read source review volume from `data/source_snapshots/product_review_stats_snowflake_latest.json`.
- Select products by:
  - top 20 `source_review_count_6m` overall after excluding service/noise products by default,
  - top 5 per recommendation category group: `skincare`, `makeup`, `bodycare`, `haircare`, `fragrance`,
  - anchor products needed by the final six personal-agent profiles when available.
- Deduplicate selected products.
- Keep a small `control` bucket only if needed for tests; do not let service/utility products dominate recommendation examples.

**Remap rule:**

- Keep review text, NER, BEE, and relation annotations unchanged.
- Replace only product metadata fields:
  - `source_product_id`
  - `prod_nm`
  - `brnd_nm`
  - `channel`
  - source identity fields if present
- Preserve original mapping in diagnostic fields:
  - `fixture_original_source_product_id`
  - `fixture_original_prod_nm`
  - `fixture_remap_reason`
- Use `Review Target` placeholder semantics for product linkage; do not require product names to appear in text.
- Assign reviews to selected products through deterministic grouped round-robin:
  - infer review group from text, NER, BEE, and original `prod_nm`,
  - prefer selected products in the same recommendation category group,
  - fallback to all selected products.

**Manifest requirements:**

- selected product ids
- category group per product
- source review count and rating per selected product
- selected reason: `overall_top20`, `<group>_top5`, `profile_anchor`, or `control`
- review count assigned per product
- seed
- input file hashes
- output file hashes

**Verification:**

- Current wide fixture remains 906 reviews and 517 products.
- Dense fixture has 906 reviews and roughly 30 to 45 selected products.
- Every dense review `source_product_id` exists in dense `product_catalog_es.json`.
- Every dense product has source review stats unless explicitly marked as control.
- Running full load on dense fixture produces materially higher promoted relation density than the wide fixture.

## 3. Task 2: Golden User Profile Fixture

**Purpose:** Replace noisy 50-profile default quality testing with meaningful personal-agent final profiles.

**Source profiles:**

Use the final six profiles confirmed from:

- `/Users/amore/workplace/agent-aibc/persnal-agent/MOCK_PROFILES_6.md`
- `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/mock_data.py`

Golden profile ids:

- `user_dry_30f`
- `user_brand_null_cat`
- `user_sensitive_40f`
- `user_scalp_care_50m`
- `user_fragrance_60f`
- `user_makeup_matte_50m`

**Fixture policy:**

- Put the six normalized profiles in `mockdata/dense_golden/user_profiles_normalized.json`.
- Keep the current 50-profile fixture as an extended/stress profile set.
- Do not silently include real sample profiles whose product ids do not resolve cleanly.
- Real sample profiles can be documented as future candidates only after exact product resolution is verified.

**Verification:**

- Six golden profiles load through `load_users_from_profiles`.
- Each profile has explicit coverage metadata in the manifest:
  - preferred brands
  - active categories
  - owned/resolved products
  - concerns/goals
  - texture/value preferences
  - empty-field edge cases
- `user_brand_null_cat` remains a sparse fallback test profile.

## 4. Task 3: Semantic Compatibility For Recommendation Evidence

**Purpose:** Use review graph evidence meaningfully without unsafe broad matches.

**New config:** `configs/recommendation_semantic_compatibility.yaml`

Required model:

- `axis`: high-level preference plane, for example `texture`, `moisture`, `finish`, `sensitivity`, `lasting`.
- `value`: directional preference, for example `moist`, `fresh`, `matte`, `rich`, `light`, `non_sticky`.
- `polarity`: positive, negative, or avoid.
- `matches`: allowed product-side evidence ids with strength.
- `blocks`: incompatible values that must not receive positive score.

Rules:

- A generic formulation or texture axis alone must not score.
- A user value may score only against compatible product BEE/keyword evidence.
- Opposite values must block or penalize, not boost.
- Product master ingredients/brand/category and review graph relation evidence remain first-class evidence families.
- Source review stats remain trust/tie-break features, not eligibility features.

Examples:

- User value `moist` can match `bee_attr_moisturizing_power` positive evidence and keywords such as `촉촉`, `수분`, `보습`.
- User value `matte` must not match `moist`, `rich`, or `glow` evidence.
- User value `fresh/light` can match `흡수`, `산뜻`, `가벼움`, `끈적임 없음` when positive.
- Generic `bee_attr_texture_feel` without compatible keyword/value evidence is weak debug evidence or ignored.

**Verification:**

- Add tests proving moist preference does not match matte evidence.
- Add tests proving generic texture axis does not score by itself.
- Add tests proving compatible value-and-polarity evidence contributes to graph relation score.

## 5. Task 4: `kg_on` Keyword Utilization Repair

**Purpose:** Prevent final KG mode from losing usable keyword evidence that exists in legacy path.

Known issue:

- `kg_off` emits `BEE_KEYWORD` signals.
- `kg_on` currently emits no promoted keyword serving fields in the measured baseline.

Implementation direction:

- Inspect `src/kg/adapter.py` and KG output contracts.
- Emit dictionary-backed keyword signals in `kg_on` when the source is review-derived and not synthetic `AUTO_KEYWORD`.
- Keep `AUTO_KEYWORD` and weak inferred keywords in quarantine or debug-only evidence.
- Preserve provenance so keyword signals can be traced to source review/fact ids.

**Verification:**

- Add a small KG-mode unit test where a source-backed keyword becomes a wrapped `HAS_BEE_KEYWORD_SIGNAL`.
- Add a corpus audit assertion that dense fixture `kg_on` has non-zero keyword aggregate rows if source-backed keyword evidence exists.
- Do not force keyword coverage by fake projection.

## 6. Task 5: Promoted And Long-Tail Evidence Separation

**Purpose:** Avoid losing useful low-support evidence while keeping promoted serving evidence trustworthy.

Policy:

- Promoted `top_*` serving fields remain high-confidence evidence.
- Unpromoted aggregate evidence may be exposed to audit/debug and optional explanation fields.
- Standard recommendation scoring should prefer promoted evidence.
- Low-support evidence can contribute only as bounded weak evidence when:
  - it is user-aligned,
  - it is value/polarity compatible,
  - it is clearly labeled as weak or long-tail.

Implementation direction:

- Add audit output that reports raw, wrapped, aggregate, promoted, and serving coverage by edge family.
- If scoring uses weak evidence, put it in a separate score layer such as `review_graph_weak_evidence_score`.
- Explanation must distinguish promoted review graph evidence from weak review evidence.

**Verification:**

- Recommendation payload separates:
  - `PRODUCT_MASTER_TRUTH`
  - `REVIEW_GRAPH_RELATION`
  - `REVIEW_GRAPH_WEAK_RELATION`
  - `PURCHASE_BEHAVIOR`
  - `SOURCE_REVIEW_STATS`
- Source stats never make an otherwise unrelated candidate eligible.

## 7. Task 6: Category Taxonomy Cleanup For Recommendation Tabs

**Purpose:** Make manual recommendation testing meaningful by ensuring category tabs select the right products.

Observed issue:

- Some fragrance-like products are caught by bodycare or skincare keywords before fragrance, which makes fragrance tab coverage poor.

Implementation direction:

- Move category classification into a shared module, for example `src/rec/category_groups.py`.
- Check more specific groups before broad groups:
  - fragrance before bodycare for `퍼퓸`, `프래그런스`, `바디미스트`
  - haircare before skincare/bodycare for scalp and shampoo terms
  - makeup before skincare for lip/cushion/base terms
- Keep `other` as fallback.

**Verification:**

- Category count endpoint reports non-zero fragrance candidates when fragrance-like products exist.
- Dense fixture manifest includes at least one product per target recommendation tab where source data allows it.

## 8. Task 7: Recommendation Audit And Golden Evaluation

**Purpose:** Evaluate whether recommendation results actually use the graph and user profile signals.

Create `scripts/audit_recommendation_evidence.py`.

Required output:

- fixture name
- KG mode
- user id
- category group
- candidate count
- top-k products
- evidence family counts in top-k
- score layer totals
- promoted relation hit count
- weak relation hit count
- source stats contribution count
- owned/family suppression count

Golden evaluation profiles:

- Run each of the six golden users against:
  - `all`
  - their main category
  - one contrast category when useful

Success criteria:

- Recommendations must not be source-stats-only.
- At least some dense-fixture recommendations must show `REVIEW_GRAPH_RELATION` when graph evidence exists.
- Sparse profile fallback must still produce honest, lower-confidence recommendations.
- Makeup matte/oily user must not be boosted by moist/rich texture evidence.
- Fragrance profile must either produce fragrance/body-scent candidates or clearly report insufficient fragrance candidate coverage.

## 9. Task 8: Documentation Updates

Update:

- `docs/architecture/v260605_906_fixture_lineage.md`
- `docs/architecture/personal_agent_profile_graph_usage_2026_06_19.md`
- `docs/architecture/db_consumer_contract.md` only if new fixture references need clarification

Document:

- wide fixture purpose
- dense golden fixture purpose
- product selection rule
- remap contract
- profile fixture policy
- recommendation evidence-family semantics
- known limitations

## 10. Task 9: Verification Commands

Run after implementation:

```bash
python scripts/build_dense_golden_fixture.py --dry-run
python scripts/build_dense_golden_fixture.py
GRAPHRAPPING_KG_MODE=on python -m pytest tests/test_dense_golden_fixture.py tests/test_recommendation_semantic_compatibility.py tests/test_kg_on_keyword_projection.py -q
GRAPHRAPPING_KG_MODE=on python scripts/audit_recommendation_evidence.py --fixture dense_golden --top-k 10
GRAPHRAPPING_KG_MODE=off python scripts/audit_recommendation_evidence.py --fixture dense_golden --top-k 10
pytest -q
```

If full `pytest -q` is too slow, run the focused tests first and then run the current recommendation, loader, and web test groups:

```bash
pytest -q tests/test_recommendation.py tests/test_candidate_prefilter.py tests/test_web_server_source_enrichment.py tests/test_user_adapter_semantics.py tests/test_mock_integrity.py
```

Manual frontend check:

```bash
uvicorn src.web.server:app --host 127.0.0.1 --port 8010
```

Check:

- category tabs have candidates
- six golden users appear
- top recommendations expose evidence family/layer details
- graph relation evidence appears when present
- source review stats are visible but not treated as graph evidence

## 11. Review Checklist

- [x] The original 906/517 fixture is unchanged.
- [x] Dense golden fixture is deterministic and has a manifest.
- [x] Dense full load has higher promoted relation density than wide fixture.
- [x] Final six personal-agent profiles are the default golden profiles.
- [x] The 50-profile set is retained only as extended/stress data.
- [x] Texture/formulation matching requires value and polarity compatibility.
- [x] `kg_on` does not lose source-backed keyword evidence.
- [x] Recommendation explanations separate product master, review graph,
      purchase behavior, weak graph evidence, and source stats.
- [x] Category tabs use shared category logic.
- [x] Tests and audit output support the quality claims.

## 12. Implementation Order

1. Add dense fixture builder and manifest tests.
2. Add golden six-profile fixture sync path and tests.
3. Extract shared category group classifier and fix fragrance/body ordering.
4. Add semantic compatibility config and scoring tests.
5. Repair `kg_on` keyword projection with focused tests.
6. Add promoted/weak evidence audit separation.
7. Add recommendation audit script and golden profile evaluation.
8. Update architecture docs.
9. Run focused tests, full tests, and manual frontend sample checks.

This order keeps data foundations first, then matching semantics, then UI and
evaluation. It also avoids changing production promotion thresholds before the
fixture and evidence utilization problems are measured cleanly.
