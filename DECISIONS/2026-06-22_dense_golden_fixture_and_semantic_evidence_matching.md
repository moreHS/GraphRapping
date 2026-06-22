# Dense Golden Fixture And Semantic Evidence Matching

Date: 2026-06-22

## Background

The current source-grounded review fixture preserves the final 906 review rows
and 517 product ids. This is useful for source identity, product-master join,
and broad pipeline regression tests. It is weak for recommendation quality
tests because review evidence is spread across too many products.

Measured baseline:

- `mockdata/review_triples_raw.json`: 906 reviews
- distinct `source_product_id`: 517 products
- median fixture reviews per product: 1
- products with at least 3 fixture reviews: 67
- `kg_on` serving coverage: `top_bee_attr_ids` on 26 products, no keyword
  serving coverage

A dry simulation that remapped the 906 reviews into 33 high-review, category
balanced products produced much denser promoted review evidence:

- average fixture reviews per selected product: about 27.5
- `kg_on` promoted aggregate rows: 65 to 954
- `kg_on` products with promoted BEE evidence: 26 to 32
- `kg_off` products with promoted keyword evidence: 5 to 17

## Decision

Keep the current 517-product fixture as the wide source-identity baseline and
add a separate dense golden fixture for recommendation and promoted-evidence
quality tests.

The dense golden fixture must be deterministic, source-grounded, and documented
with a manifest. Product selection will use local source review stats
(`source_review_count_6m`) and recommendation category coverage, not ad-hoc
random product ids.

Recommendation matching must not connect broad axes such as formulation or
texture to product review attributes by axis alone. User preference matching
must require meaningful value and polarity compatibility, for example:

- moist preference can match moisturizing or moisture keywords with positive
  polarity.
- matte or oil-control preference must not receive a bonus from moist/rich
  review evidence.
- a generic texture axis without a compatible value is weak debug evidence or
  ignored for scoring.

## Options Considered

### Option A: Lower promotion thresholds

This would immediately increase serving density in the 517-product fixture, but
it makes the production promotion contract noisier and hides the real fixture
distribution problem.

Rejected for the first implementation phase.

### Option B: Replace the 517-product fixture

This would make recommendation tests simpler, but it would remove the current
wide baseline that is valuable for source identity, product master, and
join-contract regression.

Rejected.

### Option C: Add a dense golden fixture alongside the wide baseline

This preserves the existing join-contract baseline and adds a purpose-built
quality fixture for recommendation, evidence promotion, and frontend manual
inspection.

Selected.

## Tradeoffs

- The dense golden fixture is a test/evaluation fixture, not an operational
  distribution sample.
- Product ids and product truth remain source-grounded, but relation review
  text is still the v260605 relation fixture text attached through the `Review
  Target` placeholder contract.
- Dense remapping makes promoted evidence easier to observe; it must not be
  used to claim real product-market distribution.
- Long-tail unpromoted evidence can be exposed for audit/debug, but promoted
  serving evidence remains the high-confidence path.

## Follow-Up Requirements

- Add a manifest documenting selected product ids, source review stats,
  category group, and remapped review counts.
- Keep the current 517-product baseline tests intact.
- Add golden profile fixtures based on the final six personal-agent profiles.
- Repair `kg_on` keyword utilization if source-backed keyword evidence exists.
- Add recommendation evaluation output that reports evidence-family usage,
  not only ranking.
