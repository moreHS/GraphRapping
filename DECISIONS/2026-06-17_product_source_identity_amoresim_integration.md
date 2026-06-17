# Product Source Identity And AmoreSimulation Integration

## Background

2026-06-16 real product master refresh made GraphRapping's local `graphrapping`
DB source-grounded enough for AmoreSimulation use:

- `product_master`: 517 active products.
- `product_review_stats`: 516 rows.
- `review_raw`: 906 active reviews.
- `review_catalog_link`: 906/906 joined to `product_master`.
- `serving_product_profile`: 517 rows with source stats reflected for 516
  compatibility products.

The remaining issue is not BEE/RELATION graph promotion. GraphRapping already
promotes relation-gated review semantics:

- review processing resolves a target product from `source_product_id` before
  brand/name fallback;
- BEE target attribution links only BEE rows proven to belong to the review
  target via direct relation, placeholder resolution, or same-entity resolution;
- unlinked BEE is kept as evidence/candidate data but not emitted as product
  signals.

The actual integration gap is the downstream boundary: AmoreSimulation still
reads only a subset of `serving_product_profile`, drops source identity and
source review stats, then computes review volume/rating from graph support
proxies.

## Decision

Use a hybrid contract:

1. **Graph evidence remains graph evidence.**
   Review-derived BEE/RELATION signals stay relation-gated and review-proven.
   Do not add a broad "all BEE" promotion path.

2. **Product master becomes the authoritative product/brand/category node
   source.**
   Product names and brand names are not numeric scalar metrics. They are labels
   for product/brand canonical nodes and concepts:
   - `canonical_entity.canonical_name` for products;
   - `concept_registry.canonical_name` for Brand/Category/Ingredient/Goal;
   - `entity_concept_link` for Product `HAS_BRAND`, `IN_CATEGORY`,
     `HAS_INGREDIENT`, `HAS_MAIN_BENEFIT`.

   These master-derived labels/links are product truth, not review evidence
   signals. They should not be counted as `review_count_*` support.

3. **Source identity is explicit.**
   Lossless source identity is:

   ```text
   source_channel + source_key_type + source_product_id
   ```

   The current compatibility schema may still use `product_master.product_id`
   as a single product key, but consumers must also read
   `source_product_id`, `source_channel`, `source_key_type`, and
   `source_truth_quality`. `SOURCE_KEY_COLLISION` products are valid warning
   rows, not trustworthy single-product truth. Downstream materializers must
   quarantine them by withholding clean `source_product_id`/`review_channel`
   metadata and, where a non-null product source key is required, using an
   explicit marker such as `source_key_collision:<id>`.

4. **Raw review volume/rating and review summary are sidecars.**
   Source review counts/ratings stay in `product_review_stats` and
   `serving_product_profile.source_review_*`.
   Review-summary ES text stays attached in AmoreSimulation metadata through
   source identity and channel/category hints.

5. **AmoreSimulation feature extraction must use source stats first.**
   `source_review_count_*` and `source_avg_rating_*` are social proof/rating
   fields. Graph `review_count_*` is graph confidence/fallback only.

## Consequences

- Existing GraphRapping BEE attribution and promotion gates are preserved.
- Product master refresh work is used to fill canonical product/brand labels and
  concept links from source truth instead of NER/BEE extraction coverage.
- AmoreSimulation must extend its DTO/query/materializer/features to carry
  source identity and source review stats.
- Existing local AmoreSimulation metadata merge behavior must be adjusted
  carefully: GraphRapping-owned source fields should refresh for GraphRapping
  rows, while unrelated local/custom metadata is preserved.
