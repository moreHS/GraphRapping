# Product Master / Review Graph Linkage Design Notes

작성일: 2026-06-16

## 1. 목적

GraphRapping의 최종 산출물은 AmoreSimulation에서 제품 상태와 구매 판단
입력으로 사용된다. 따라서 다음 세 축의 정보가 `prd_id` 계열 키로 끊기지
않고 이어져야 한다.

1. 상품마스터: 상품명, 브랜드, 카테고리, 가격, 성분, 효능, 대표상품명 등
   정규화된 product truth.
2. 리뷰 분석 결과: NER/BEE/REL raw, canonical fact, wrapped signal, aggregate,
   serving top fields.
3. 리뷰 요약/원천 통계: ES8 review summary의 비정형 요약, 주요 연령/성별/피부
   힌트, source raw review count/rating.

이번 문서는 "무엇을 graph에 넣을 것인가"와 "무엇을 product sidecar/serving
field로 유지할 것인가"를 실제 GraphRapping/AmoreSimulation 코드와 현재 DB
실측값 기준으로 정리한다.

## 2. 확인한 코드와 데이터

GraphRapping:

- `sql/ddl_raw.sql`
- `sql/ddl_mart.sql`
- `configs/projection_registry.csv`
- `src/ingest/product_ingest.py`
- `src/ingest/review_ingest.py`
- `src/jobs/run_full_load.py`
- `src/jobs/run_daily_pipeline.py`
- `src/loaders/product_loader.py`
- `src/loaders/product_truth_merge.py`
- `src/loaders/source_review_stats_loader.py`
- `src/link/placeholder_resolver.py`
- `src/link/bee_attribution.py`
- `src/canonical/canonical_fact_builder.py`
- `src/wrap/signal_emitter.py`
- `src/mart/build_serving_views.py`
- `src/db/repos/product_repo.py`
- `src/db/repos/canonical_repo.py`
- `src/db/repos/signal_repo.py`
- 관련 테스트:
  `tests/test_source_product_id_contract.py`,
  `tests/test_product_review_stats_repo.py`,
  `tests/test_source_review_stats_loader.py`

AmoreSimulation:

- `packages/core/src/beauty_market_twin_core/domain/data_source_models.py`
- `packages/adapters/src/beauty_market_twin_adapters/twin_build/graphrapping_data_source.py`
- `packages/core/src/beauty_market_twin_core/application/commands/graphrapping_master_materializer.py`
- `packages/twins/src/beauty_market_twin_twins/product/graphrapping_features.py`
- `packages/core/src/beauty_market_twin_core/application/commands/start_run.py`
- `packages/adapters/src/beauty_market_twin_adapters/review_summary/models.py`
- `packages/adapters/src/beauty_market_twin_adapters/review_summary/normalizer.py`
- `packages/adapters/src/beauty_market_twin_adapters/review_summary/es_repository.py`
- `packages/simulation/src/beauty_market_twin_simulation/conversion_engine/persona_encoder.py`
- `packages/simulation/src/beauty_market_twin_simulation/conversion_engine/llm_scorer.py`
- 관련 문서:
  `DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md`,
  `DECISIONS/2026-06-17_product_source_identity_amoresim_integration.md`,
  `DECISIONS/2026-06-17_review_summary_sidecar_final_output.md`,
  `docs/architecture/db_consumer_contract.md`

현재 로컬 GraphRapping DB:

- DSN: `postgresql://postgres:postgres@localhost:5432/graphrapping`
- 조회 방식: read-only aggregate query

## 3. 현재 DB 실측

2026-06-17에 로컬 `graphrapping` Postgres를 다시 조회했다. 아래 값은
2026-06-16 실상품마스터 refresh와 full-load 재적재 이후의 현재 상태다.

### 3.1 테이블 카운트

| Table | Count |
|---|---:|
| `product_master` | 517 |
| `review_raw` | 906 |
| `review_catalog_link` | 906 |
| `product_review_stats` | 516 |
| `serving_product_profile` | 517 |
| `review_summary_sidecar` | 516 |
| `review_summary_manifest` | 1 |
| `entity_concept_link` | 3,899 |
| `concept_registry` | 407 |
| `canonical_fact` | 3,873 |
| `wrapped_signal` | 2,801 |
| `agg_product_signal` | 6,849 |

### 3.2 Product master 품질

| Field | Non-empty / 517 |
|---|---:|
| `product_name` | 517 |
| `source_product_id` | 517 |
| `representative_product_name` | 517 |
| `category_name` | 245 |
| `brand_name` | 516 |
| `price > 0` | 353 |
| `ingredients` | 203 |
| `main_benefits` | 90 |
| `source_review_count` | 516 |
| `source_review_score` | 516 |

`source_truth_quality`:

| `source_truth_quality` | Count |
|---|---:|
| `SOURCE_GROUNDED` | 516 |
| `SOURCE_KEY_COLLISION` | 1 |

따라서 이제 local DB는 브랜드/가격/성분/효능/review stats 검증용으로 사용할 수
있다. 단 `35119` 1건은 cross-channel key collision을 안전하게 표시한 row이므로
clean product truth로 취급하면 안 된다.

### 3.3 Review to product link

| Check | Count |
|---|---:|
| `review_catalog_link` total | 906 |
| `source_product_id IS NOT NULL` | 906 |
| `matched_product_id IS NOT NULL` | 906 |
| `match_status='EXACT'` | 906 |
| `match_method='source_product_id'` | 906 |
| `source_product_id = matched_product_id` | 906 |

즉 현재 906개 리뷰의 product 연결은 상품명 본문 매칭이 아니라
`source_product_id` exact match로 성립한다.

### 3.4 Source channel/key 상태

`review_raw`:

| `source_channel` | `source_key_type` | Count |
|---|---|---:|
| `031` | `NULL` | 516 |
| `036` | `NULL` | 386 |
| `039` | `NULL` | 2 |
| `048` | `NULL` | 2 |

`product_master`:

| `source_channel` | `source_key_type` | `source_truth_source` | `source_truth_quality` | Count |
|---|---|---|---|---:|
| `031` | `ecp_onln_prd_srno` | `amore-prod-mstr+snowflake:2026-06-16` | `SOURCE_GROUNDED` | 353 |
| `036` | `chn_prd_cd` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | `SOURCE_GROUNDED` | 155 |
| `031` | `ecp_onln_prd_srno` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | `SOURCE_GROUNDED` | 4 |
| `039` | `chn_prd_cd` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | `SOURCE_GROUNDED` | 2 |
| `048` | `chn_prd_cd` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | `SOURCE_GROUNDED` | 2 |
| `031,036` | `source_key_collision` | `source_identity_merge:2026-06-16` | `SOURCE_KEY_COLLISION` | 1 |

`serving_product_profile`:

| `source_channel` | `source_key_type` | `source_review_stats_source` | Count |
|---|---|---|---:|
| `031` | `ecp_onln_prd_srno` | `amore-prod-mstr+snowflake:2026-06-16` | 353 |
| `036` | `chn_prd_cd` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | 155 |
| `031` | `ecp_onln_prd_srno` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | 4 |
| `039` | `chn_prd_cd` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | 2 |
| `048` | `chn_prd_cd` | `snowflake:f_prd_rv_hist+d_chn_prd_mstr:2026-06-16` | 2 |
| `031,036` | `source_key_collision` | `NULL` | 1 |

주의점:

- `review_triples_raw.json`에는 `channel` 필드가 있고, loader가 이를
  `source_channel`로 올린다.
- 현행 DB의 `review_raw.source_key_type`은 여전히 NULL이다. consumer용 source
  identity는 `product_master` / `serving_product_profile`의 key type을 우선
  사용해야 한다.
- `tests/test_rs_jsonl_maps_product_id_channel_key_type_and_rating`는
  rs-jsonl loader가 `031 -> ecp_onln_prd_srno`,
  `036/039/048 -> chn_prd_cd`로 매핑해야 한다고 고정한다.
- source stats 적재 시에는 product master의 key type과 review/channel
  key type을 다시 정렬해야 한다. `product_review_stats.load`는 product_id
  우선 조회 후 source key preference를 적용하므로 fallback은 가능하다.

### 3.5 Concept link 상태

| `entity_concept_link.link_type` | Count |
|---|---:|
| `HAS_INGREDIENT` | 2,975 |
| `HAS_BRAND` | 516 |
| `IN_CATEGORY` | 245 |
| `HAS_MAIN_BENEFIT` | 163 |

`src/ingest/product_ingest.py`는 브랜드/카테고리/성분/효능/국가를 concept으로
seed하고 product entity에 link한다. 상품명과 브랜드명은 숫자 metric이 아니라
product/brand canonical node label이다. 이 label/link는 상품마스터 기원 product
truth이며, review-derived graph support count로 집계하지 않는다.

### 3.6 Source review stats와 serving field

| Field | Non-null / 517 |
|---|---:|
| `serving_product_profile.source_review_count_6m` | 516 |
| `serving_product_profile.source_review_count_all` | 516 |
| `serving_product_profile.source_avg_rating_6m` | 0 |
| `serving_product_profile.source_avg_rating_all` | 516 |

`product_review_stats`는 516 rows다.

현재 6개월 rating 평균은 모두 NULL이고, all-time rating 평균은 516개에 존재한다.
따라서 AmoreSimulation의 `avg_rating`은 `source_avg_rating_6m`이 없으면
`source_avg_rating_all`로 fallback해야 한다. 이 값들은 graph evidence count가
아니라 source raw stats다.

GraphRapping에는 이미 다음 경로가 있다.

- `product_review_stats`
- `serving_product_profile.source_review_*`
- `src/loaders/source_review_stats_loader.py`
- `src/db/repos/product_repo.py::upsert_product_review_stats`
- `src/jobs/run_full_load.py::_merge_source_review_stats`

다만 테스트 fixture와 mock-configured catalog path에서는
`source_review_count=0`이나 synthetic/mock stats를 source stats로 승격하지
않도록 막고 있다. 이 계약은
`tests/test_mock_catalog_review_stats_are_not_promoted_as_source_fallback`와
`tests/test_configured_mock_review_stats_are_not_promoted`에서 확인된다.

### 3.7 Review summary sidecar 상태

2026-06-17에 ES8 `summary-review-long`/`summary-review-short` alias를
alias-wide scroll로 가져온 뒤, 로컬 DB의 clean source identity와만 조인해
`review_summary_sidecar`를 적재했다. 이 과정에서 로컬 product id 목록은 ES에
전송하지 않았다.

| Check | Count |
|---|---:|
| `product_master` active product | 517 |
| clean lookup product | 516 |
| `SOURCE_KEY_COLLISION` excluded | 1 |
| fetched `summary-review-long` docs | 14,477 |
| fetched `summary-review-short` docs | 3,695 |
| sidecar rows | 516 |
| `match_status='exact_category'` | 495 |
| `match_status='not_found'` | 21 |
| collision sidecar rows | 0 |
| rows with raw long ES hit | 495 |
| rows with raw short ES hit | 492 |

매칭 기준은 `source_channel -> review_summary_category`다:

| `source_channel` | review summary category |
|---|---|
| `031` | `own-apmall` |
| `036` | `own-innisfree` |
| `039` | `own-osulloc` |
| `048` | `own-aritaum` |

같은 `source_product_id` 문서가 ES에 있더라도 source channel 기반 expected
category가 없거나 다르면 자동 부착하지 않는다. 이 경우
`product_id_ambiguous_skipped`로 남긴다. 최종 loader는 `source_product_id`
단독 unique fallback으로 summary를 붙이지 않는다. 현재 로컬 적재에서는
ambiguous 0건, not_found 21건이다.

`review_summary_sidecar.long_doc`/`short_doc`는 ES hit 원문을 JSONB로 보존한다.
Consumer는 일반 read에서 `normalized_summary`를 우선 쓰고, 원천 필드가 더
필요하면 raw JSONB를 조회할 수 있다.

### 3.8 Review Target과 attribution 상태

현행 구조:

- `process_review()`가 먼저 `source_product_id` exact match를 시도한다.
- 매칭된 product id는 `target_product_iri = product:{id}`가 된다.
- `resolve_placeholders()`가 `Review Target` placeholder를
  `target_product_iri`로 해석한다.
- BEE attribution은 `attribute_bee_rows()`가 수행한다.

현재 DB:

| Table | Total | `target_linked=true` | `target_linked=false` | `target_linked IS NULL` |
|---|---:|---:|---:|---:|
| `canonical_fact` | 3,873 | 0 | 0 | 3,873 |
| `wrapped_signal` | 2,801 | 0 | 0 | 2,801 |

원인:

- `CanonicalFact`와 `WrappedSignal`에는 `target_linked` /
  `attribution_source` 필드가 있다.
- `canonical_repo`와 `signal_repo`도 해당 컬럼을 저장한다.
- 하지만 legacy `CanonicalFactBuilder.add_fact()`와 `add_bee_facts()`는
  `target_linked` / `attribution_source` 인자를 받지 않는다.
- `run_daily_pipeline.py`는 BEE raw row에 attribution metadata를 붙이고
  unlinked BEE row는 skip하지만, promoted fact/signal까지 이 metadata를
  전달하지 않는다.
- KG adapter도 edge promotion gate에는 attribution을 보지만,
  `builder.add_fact()`에 값을 넘길 수 없다.

결론: `Review Target` 연결 자체는 product id 기준으로 동작하지만,
attribution audit metadata는 L2/L2.5에 완전 보존되지 않는다.

## 4. AmoreSimulation 소비 현황

### 4.1 GraphRapping adapter

`GraphRappingTwinBuildDataSource.fetch_product_profiles()`는
`product_master + serving_product_profile`을 읽는다.

현재 읽는 필드:

- `product_id`
- product/brand/category/price
- ingredients/main_benefits
- `review_count_30d`, `review_count_90d`, `review_count_all`
- top signal fields
- `ingredient_concept_ids`

현재 읽지 않는 필드:

- `source_product_id`
- `source_channel`
- `source_key_type`
- `source_review_count_6m`
- `source_review_count_all`
- `source_avg_rating_6m`
- `source_avg_rating_all`
- `source_review_stats_source`

`ProductProfile` DTO에도 source stats 필드가 없다. 따라서 GraphRapping DB에
source stats가 채워져도 현재 AmoreSimulation boundary에서 정보가 잘린다.

### 4.2 Product materializer / feature extractor

`graphrapping_master_materializer._product_metadata()`는 다음을 metadata에 넣는다.

- `source_product_id = profile.product_id`
- `review_source = "own"`
- brand/category/ingredients/main_benefits
- graph `review_count_30d/90d/all`
- top signal fields

하지만 source review count/rating은 metadata에 넣을 수 없다. DTO에 없기
때문이다.

`graphrapping_features.py` 현행 동작:

- `compute_review_volume_gr()`는 `profile.review_count_all`을 반환한다.
- `compute_avg_rating_gr()`는 signal polarity로 평균평점을 추정한다.
- 코드 주석도 "GraphRapping review_raw has no rating column"이라고 되어 있다.

이 값들은 user-facing raw review volume/rating이 아니라 graph signal 기반
proxy다. AmoreSimulation의 2026-06-15 plan도 `review_volume`은 source raw
review count여야 하고, promoted graph signal count와 분리해야 한다고 적고
있다.

### 4.3 Simulation prompt/scoring 사용처

`persona_encoder.encode_product_context()`는 다음을 product prompt에 쓴다.

- `avg_rating`
- `review_volume`
- `metadata_json.review_summary`

`llm_scorer._per_product_fallback()`는 `avg_rating`을 `review_trust` 계산에
쓴다.

따라서 source count/rating이 빠지는 것은 표시용 정보 손실이 아니라 실제
구매판단 입력 품질 저하다.

### 4.4 Review summary ES

AmoreSimulation의 ES8 review summary 구조:

- `start_run._enrich_products_with_review_summaries()`가 run-time product dict에
  summary를 attach한다.
- GraphRapping 최종 sidecar는 source channel 기반 category exact match만
  clean attach로 인정한다.
- downstream fallback이 필요하더라도 `source_product_id` 단독 attach는
  candidate provenance/debug 용도로만 취급한다.
- ambiguous product id는 기본적으로 text를 붙이지 않고 candidate provenance만
  보존한다.
- normalizer는 `review_count.effective`, 속성별 긍정/부정 summary,
  `age_sctn_nm`, `sex_nm`, `sktp_nm`, `sktr_nm` 등 product meta를 metadata로
  정리한다.
- 2026-06-09 plan은 ES summary에 rating/avg_rating이 없으므로 rating을
  만들지 말라고 명시한다.

결론: review summary는 현재 코드 구조상 GraphRapping graph node로 만들기보다
AmoreSimulation product metadata sidecar로 두는 것이 맞다. 단,
`source_product_id` / `review_source` / `review_channel` / category hint는
GraphRapping product serving contract에서 보존되어야 matching 품질이 오른다.

## 5. Graph layer별 배치 선택지

### 선택지 A. Product master를 serving join 전용으로만 둔다

내용:

- `product_master`를 authoritative source truth로 유지한다.
- GraphRapping review graph는 review-derived evidence만 canonical/signal로
  승격한다.
- AmoreSimulation은 serving query에서 product master truth와 graph signal을
  같이 읽는다.

장점:

- 단순하다.
- 가격/대표명/브랜드 같은 정본 값의 중복과 drift가 적다.
- source review count/rating처럼 graph evidence가 아닌 수치를 섞지 않는다.

단점:

- graph traversal에서 `Product -> Brand/Category/Ingredient`를 바로 쓰기
  어렵다.
- brand/category/ingredient 기반 설명 가능성을 별도 join에 의존한다.
- 상품명/브랜드명 canonical label을 graph 탐색 표면에서 충분히 활용하지 못한다.

현재 코드와의 부합:

- 이미 `serving_product_profile`은 product truth + graph signal을 합치는
  table-based mart다.
- AmoreSimulation adapter도 이 mart를 읽도록 되어 있다.

### 선택지 B. Product master 전체를 review graph fact/signal 경로로 승격한다

내용:

- 상품마스터의 브랜드/카테고리/성분/효능/가격/원산지/대표명 등을
  canonical_fact로 생성한다.
- projection registry를 통해 CATALOG_VALIDATION signal 등으로도 노출한다.

장점:

- 모든 product facet을 graph edge로 탐색할 수 있다.
- signal evidence와 product truth를 하나의 fact/provenance 체계로 설명할 수
  있다.

단점:

- 가격, 대표상품명, display name처럼 변동/표시 성격의 데이터까지 graph fact가
  되면 snapshot churn이 커진다.
- review-derived signal과 catalog truth가 같은 aggregate/promotion 경로에
  섞일 위험이 있다.
- source truth와 graph projection 중 어느 쪽이 정본인지 헷갈린다.
- 상품명/브랜드명 label 보존이라는 목적에 비해 과하게 무거운 구조다.

현재 코드와의 부합:

- `configs/projection_registry.csv`에는 `has_ingredient`, `brand_of`,
  `belongs_to`, `price_of` 등 catalog validation projection이 있다.
- 다만 registry notes도 "정본은 상품 DB", "catalog 정본"이라고 적어
  product DB가 정본임을 전제한다.

### 선택지 C. Hybrid: master label/link는 canonical node/concept로, 수치/요약은 sidecar로 둔다

내용:

- `product_master`는 계속 authoritative source truth다.
- 상품명은 product canonical node label로 보존한다.
- 브랜드명은 Brand concept/canonical label로 보존하고 Product와 `HAS_BRAND`
  link로 연결한다.
- stable categorical facets는 `entity_concept_link`로 연결한다.
  - brand
  - category
  - ingredient
  - main benefit / goal
  - variant family
  - optional price band
- volatile/display/numeric fields는 master/serving field로 둔다.
  - representative product name
  - raw price
  - source review count/rating
  - ES review summary text
- review-derived graph signal과 product-master truth는 evidence kind/source
  domain으로 구분한다.

장점:

- graph에서 필요한 product facet은 연결된다.
- 상품명/브랜드명은 graph node label/search/display에 안정적으로 노출된다.
- raw count/rating/summary 같은 비그래프 정보는 손실 없이 보존된다.
- review promotion gate와 catalog truth가 섞이는 위험을 낮춘다.

단점:

- 별도 product-master fact builder 없이도 우선 `canonical_entity`,
  `concept_registry`, `entity_concept_link` 계약을 정확히 검증해야 한다.
- consumer가 "graph count"와 "source raw count"를 명확히 구분해야 한다.

현재 코드와의 부합:

- 이미 `entity_concept_link`가 이 역할 일부를 하고 있다.
- `serving_product_profile`에 concept ids와 source review fields가 함께 있다.
- AmoreSimulation의 review summary adapter는 sidecar 방식이다.

권장: 선택지 C.

## 6. 권장 스키마 배치

### 6.1 Layer 0 / 1: source truth와 raw evidence

유지/보강:

- `product_master`
  - product id, source product id, channel/key type
  - product/brand/category/price/ingredient/benefit/country truth
  - source-grounded catalog review count/score, 단 source가 mock/synthetic이면
    stats 승격 금지
- `product_review_stats`
  - Snowflake/source raw review count/rating
  - `product_id + source_channel + source_key_type` composite key
  - product_id 우선 fallback 조회 유지
- `review_raw`
  - source review key
  - source product id
  - source channel
  - source key type
  - source rating if present
- `review_catalog_link`
  - review_id -> matched_product_id
  - source_product_id exact match provenance

하지 말아야 할 것:

- 모든 리뷰 raw row에 상품마스터 전체를 denormalize하지 않는다.
- product name token으로 brand를 만들지 않는다.
- source review count/rating이 없는데 0이나 signal count로 대체하지 않는다.

### 6.2 Layer 2: canonical nodes, concept links, canonical facts

review-derived facts:

- `Review Target`은 `review_catalog_link.matched_product_id` 기반
  `product:{prd_id}`로 resolve한다.
- BEE/REL fact는 product subject/object를 이 IRI에 연결한다.
- `target_linked` / `attribution_source`는 canonical fact까지 보존해야 한다.

product-master facts:

- 우선순위는 별도 fact builder가 아니라 현재 존재하는 node/link 계약이다.
  - Product node: `canonical_entity(entity_type='Product',
    canonical_name=product_name)`
  - Brand/Category/Ingredient/Goal: `concept_registry.canonical_name`
  - Product facet edge: `entity_concept_link`의 `HAS_BRAND`,
    `IN_CATEGORY`, `HAS_INGREDIENT`, `HAS_MAIN_BENEFIT`
- 필요해질 때만 product-master fact builder를 별도 설계한다.
- raw price, source review count/rating, review summary text는 fact화하지 않는다.

metadata/provenance를 product-master fact builder까지 확장할 경우:

- `FactProvenance.source_domain = "product"`
- `FactProvenance.source_kind = "master"`
- `evidence_kind = "PRODUCT_MASTER"` 또는 별도 catalog truth marker
- review-derived BEE/REL과 같은 promotion gate에서 corpus support를 평가하지
  않도록 분리한다.

### 6.3 Layer 2.5 / 3: signal and aggregate

review-derived signals:

- BEE, keyword, concern, context, comparison, co-use 등은 기존 signal/aggregate
  흐름을 유지한다.
- `review_count_30d/90d/all`은 promoted graph evidence distinct review count
  의미를 유지한다.

catalog-derived signals:

- `CATALOG_VALIDATION_SIGNAL`은 consumer top signal과 review-derived ranking을
  오염시키지 않도록 별도 취급한다.
- 현재 `build_serving_product_profile()`도 defense-in-depth로
  `CATALOG_VALIDATION_SIGNAL`을 top signal에서 제외한다.

source review stats:

- graph signal로 만들지 않는다.
- `product_review_stats`와 `serving_product_profile.source_review_*`에 보존한다.
- AmoreSimulation의 `review_volume`과 `avg_rating`은 가능하면 이 필드에서
  가져온다.

### 6.4 Serving / consumer contract

`serving_product_profile`는 제품 소비 경계의 주 contract다.

필수 의미 구분:

- `review_count_30d/90d/all`
  - graph evidence distinct review count
  - promoted signal 기반
  - 제품 사회적 증거의 raw count가 아님
- `signal_support_count_all`
  - signal line support count
  - 더더욱 raw review volume이 아님
- `source_review_count_6m/all`
  - source raw review volume
  - user-facing social proof 후보
- `source_avg_rating_6m/all`
  - source raw rating average
  - `avg_rating` 후보
- `metadata_json.review_summary`
  - AmoreSimulation sidecar
  - qualitative prompt evidence

## 7. 현재 손실/불일치 지점

### 7.1 GraphRapping DB 적재 손실

2026-06-16 refresh 이후 source stats 적재는 완료됐다. 현재 DB 기준:

- `product_review_stats`: 516 rows
- `serving_product_profile.source_review_count_all`: 516 non-null
- `serving_product_profile.source_avg_rating_all`: 516 non-null
- `serving_product_profile.source_avg_rating_6m`: 0 non-null

남은 손실은 GraphRapping 내부보다 AmoreSimulation consumer boundary에 있다.
다만 GraphRapping에서는 다음 검증을 유지해야 한다.

- `review_count_*`와 `source_review_*` 의미가 섞이지 않는지 테스트한다.
- mock source는 계속 stats 승격 금지한다.
- `SOURCE_KEY_COLLISION` row는 source stats 없는 warning row로 유지한다.

### 7.2 Product master 품질 손실

2026-06-16 refresh 이후 `product_catalog_es.json`와 local DB는 오늘자
실상품마스터/Snowflake 기반 compat catalog로 교체됐다. 현재 DB 기준:

- brand present: 516/517
- price present: 353/517
- ingredients present: 203/517
- main benefits present: 90/517

남은 보완은 누락 상품의 source truth quality를 명확히 표시하고, consumer가
`SOURCE_KEY_COLLISION`을 clean product로 사용하지 않게 하는 것이다.

### 7.3 Attribution audit metadata 손실

`target_linked` / `attribution_source` 필드는 raw BEE row에서 산출되지만
canonical/signal DB에는 모두 NULL이다.

수정 방향:

- `CanonicalFactBuilder.add_fact()`에 optional
  `target_linked`, `attribution_source` 인자를 추가한다.
- `add_bee_facts()`도 같은 인자를 받아 Product->BEEAttr와 BEEAttr->Keyword
  fact에 전달한다.
- legacy path에서 `bee_row`의 attribution metadata를 넘긴다.
- KG adapter도 edge metadata를 넘긴다.
- DB 재적재 후 `canonical_fact`와 `wrapped_signal`에서 null-only 상태가
  해소되는지 검증한다.

### 7.4 AmoreSimulation consumer boundary 손실

GraphRapping `serving_product_profile`에는 source stats 필드가 있지만
AmoreSimulation이 읽지 않는다.

수정 방향:

- `ProductProfile`에 source identity/stats 필드 추가.
- `GraphRappingTwinBuildDataSource.fetch_product_profiles()` SELECT에
  `s.source_*` 추가.
- `graphrapping_master_materializer._product_metadata()`에 source stats 보존.
- `compute_review_volume_gr()`는 source raw count 우선, 없으면 graph count를
  "graph proxy"로 명시해 fallback.
- `compute_avg_rating_gr()`는 source avg rating 우선, 없으면 현재 polarity
  proxy를 fallback으로만 사용하거나 None으로 둔다.
- metadata에는 `graphrapping_review_count_all` 등 graph count 이름을 분리해
  남긴다.

### 7.5 Review summary 위치

ES review summary는 GraphRapping graph화하지 않는다. 대신 GraphRapping
최종 산출물에는 `review_summary_sidecar`로 포함한다.

이유:

- summary는 속성별 비정형 텍스트와 demographic hint다.
- graph evidence support를 늘리는 review-derived fact가 아니다.
- alias-wide export 후 로컬 clean source identity로 조인하면 local product id를
  외부 ES에 보내지 않고도 final output에 포함할 수 있다.
- ambiguous matching guard와 manifest는 GraphRapping sidecar loader에 있다.
- rating은 ES summary에 없다고 문서화되어 있어, summary를 graph화해도
  source avg rating 문제를 해결하지 못한다.

따라서 GraphRapping serving profile은 ES summary matching을 돕는 source
identity 필드를 잃지 않아야 하며, 최종 consumer query는
`review_summary_sidecar.normalized_summary`를 `product_id`로 left join한다.

## 8. 권장 구현 단위

### Task A. GraphRapping 문서/계약 최신화

목표:

- 2026-06-16 real snapshot 이후 상태를 문서와 consumer query에 반영한다.

작업:

- stale mock-era 수치를 제거한다.
- BEE/RELATION graph promotion은 이미 relation-gated라는 점을 명시한다.
- 상품명/브랜드명은 canonical node/concept label이라는 점을 명시한다.
- consumer query에 `source_truth_*`를 추가한다.

### Task B. Product master canonical node/link 검증

목표:

- 상품마스터 기반 product/brand/category/ingredient/benefit label/link가
  NER/BEE 추출 여부와 무관하게 생성됨을 테스트로 잠근다.

작업:

- `ingest_product()`가 product canonical entity와 Brand concept/link를 만드는
  것을 테스트한다.
- concept link가 serving profile concept id에는 반영되지만 graph evidence
  count를 증가시키지 않는 것을 테스트한다.
- product-name token brand 복원 금지는 유지한다.

### Task C. AmoreSimulation consumer 확장

목표:

- GraphRapping source identity/source stats가 simulation의
  `review_volume` / `avg_rating` / review-summary lookup까지 도달하게 한다.

작업:

- `ProductProfile`에 source identity/stats 필드 추가.
- GraphRapping adapter SELECT 확장.
- materializer metadata 확장.
- feature extractor에서 source stats 우선 사용.
- 기존 graph counts는 graph support 이름으로 분리 보존.
- review summary는 현행 ES sidecar 구조 유지하되 `review_channel`을 전달한다.

### Task D. BEE attribution metadata 보존

목표:

- `target_linked` / `attribution_source`를 L2/L2.5까지 남긴다.

작업:

- `CanonicalFactBuilder.add_fact()` / `add_bee_facts()` 인자 확장.
- legacy path와 KG adapter에서 metadata 전달.
- repo 저장 경로는 이미 있으므로 테스트 추가 후 재적재.

이 작업은 현재 graph 승격 원칙 변경이 아니라 audit metadata 보강이다. 이번
consumer source-stats 연동의 필수 선행은 아니며, 별도 소작업으로 분리 가능하다.


## 9. 최종 권장안

채택할 구조는 Hybrid다.

1. 상품마스터는 `product_master`와 `serving_product_profile`의 정본 필드로
   유지한다.
2. 브랜드/카테고리/성분/효능/국가처럼 안정적인 facet은 현재 계약대로
   `entity_concept_link`로 graph에 연결한다. 별도 product-master fact builder는
   필요가 생길 때 별도 decision으로 다룬다.
3. 가격 원값, review count, 평균평점, review summary text는 graph signal로
   만들지 않는다.
4. source review count/rating은 `product_review_stats`와
   `serving_product_profile.source_review_*`에 남긴다.
5. review summary text는 `review_summary_sidecar`에 raw ES hit와 normalized
   projection을 함께 보존한다.
6. AmoreSimulation은 `review_volume`/`avg_rating`을 source stats에서 먼저
   가져오고, graph counts/polarity는 fallback 또는 별도 metadata로 둔다.
7. `Review Target`은 현행 호환 경로에서는 `source_product_id ->
   matched_product_id -> product:{prd_id}`로 resolve한다. 단 2026-06-16
   실상품마스터 조회에서 `35119` cross-channel collision이 확인되었으므로,
   lossless 구조에서는 `source_channel + source_key_type + source_product_id`
   composite identity를 함께 사용해야 한다. attribution metadata도 canonical
   fact/signal까지 보존하도록 보완한다.

이 구조가 현재 코드와 가장 잘 맞고, source에 존재하는 고품질 정보가 graph
promotion/serving/consumer 경계에서 손실되는 문제를 가장 작게 고친다.

## 10. 2026-06-16 실상품마스터 후속 확인

후속 작업에서 기존 mock-era catalog를 오늘자 실상품마스터 기반 compat
catalog로 교체했다. 상세는
[`product_master_real_snapshot_2026_06_16.md`](product_master_real_snapshot_2026_06_16.md)에
기록했다.

- ES `amore-prod-mstr`는 517 product id 중 380개를 `ONLINE_PROD_SERIAL_NUMBER`
  로 매칭했다.
- Snowflake `f_prd_rv_hist` + `d_chn_prd_mstr`는 518 source identity 전부를
  매칭했다.
- 채널별 key type은 `031 = ecp_onln_prd_srno`,
  `036/039/048 = chn_prd_cd`다.
- `mockdata/product_catalog_es.json`는 517개 compat catalog로 갱신했다.
- 로컬 DB는 truncate 후 full load 재적재했고, `review_catalog_link` 906/906이
  `product_master`와 join된다.
- 단일 `product_id=35119`는 서로 다른 031/036 상품을 가리키므로
  `SOURCE_KEY_COLLISION`으로 표시했다. 이는 현재 schema의 한계이며, 다음
  설계에서는 source identity 테이블 또는 composite key match가 필요하다.

## 11. 2026-06-17 consumer 반영 및 AmoreSimulation 최신화

2026-06-17 후속 구현에서 위 Hybrid 계약을 코드와 local DB에 반영했다.

- GraphRapping consumer query는 `source_truth_source`,
  `source_truth_quality`, `source_truth_updated_at`을 노출한다.
- GraphRapping regression test는 상품마스터 기반 product canonical entity와
  brand/category concept link가 NER/BEE 추출 없이 생성되고, 이 master-derived
  link가 graph review support count를 증가시키지 않는 것을 확인한다.
- AmoreSimulation `ProductProfile`, GraphRapping adapter, materializer,
  feature extractor, GraphRapping schema preflight가 source identity/source
  review stats를 전달하도록 확장됐다.
- `SOURCE_KEY_COLLISION`은 clean source identity가 아니므로
  AmoreSimulation에서 clean `source_product_id`, `review_channel`,
  source review stats/rating, review-summary lookup key로 승격하지 않는다.
  `ProductModel.source_product_id`에는 `source_key_collision:<id>` marker를
  저장하고, metadata에는 `source_identity_clean=false`와
  `source_product_id_collision` 진단값만 둔다.
- AmoreSimulation local DB는 project
  `00000000-0000-0000-0000-000000000001`, snapshot date `2026-06-17`로
  GraphRapping mode rebuild를 수행했다.
- rebuild 결과:
  - consumer twins: 50
  - product states: 517
  - `source_review_count_all` 보유 product states: 516
  - `source_avg_rating_all` 보유 product states: 516
  - clean `source_product_id` 보유 product states: 516
  - `review_channel` 보유 product states: 516
  - `SOURCE_KEY_COLLISION` product states: 1
  - local `product` row: 565 total, current snapshot 밖 48, 그중
    GraphRapping-owned stale row 0
- sample 검증:
  - `61289`: `review_volume=4919`, `avg_rating=4.95`,
    `source_product_id=61289`, `review_channel=031`,
    `source_truth_quality=SOURCE_GROUNDED`
  - `35119`: `review_volume=0`, `avg_rating=NULL`,
    clean `source_product_id` 없음, `review_channel` 없음,
    `ProductModel.source_product_id=source_key_collision:35119`,
    `source_product_id_collision=35119`, `source_channel=031,036`,
    `source_truth_quality=SOURCE_KEY_COLLISION`

실행/검증 상세는
[`../superpowers/plans/2026-06-17-product-source-identity-amoresim-integration-plan.md`](../superpowers/plans/2026-06-17-product-source-identity-amoresim-integration-plan.md)에
남겼다.

## 12. 2026-06-17 review summary sidecar 최종화

Review summary는 이번 최종화에서 GraphRapping final output 범위로 포함했다.
단 graph fact/signal로 승격하지 않고, `product_id`로 join되는 mart sidecar로
분리했다.

구현:

- `sql/ddl_mart.sql`
  - `review_summary_sidecar`
  - `review_summary_manifest`
- `src/loaders/review_summary_sidecar_loader.py`
  - ES alias-wide export
  - clean source identity matching
  - collision exclusion
  - raw ES hit 보존
- `src/jobs/load_review_summary_sidecar.py`
  - active product snapshot read
  - sidecar replace/upsert
  - manifest insert
- `scripts/load_review_summary_sidecar.py`
  - local DB finalization CLI
- `sql/consumer_contract_queries.sql`
  - manifest readiness query
  - product read query에 `review_summary_sidecar` left join

적재 결과:

| Metric | Count |
|---|---:|
| active products | 517 |
| clean lookup products | 516 |
| `SOURCE_KEY_COLLISION` excluded | 1 |
| fetched `summary-review-long` docs | 14,477 |
| fetched `summary-review-short` docs | 3,695 |
| sidecar rows | 516 |
| matched | 495 |
| `exact_category` | 495 |
| `not_found` | 21 |
| ambiguous skipped | 0 |
| collision sidecar rows | 0 |

검증:

- sidecar 516 rows 모두 `product_master`와 join된다.
- `SOURCE_KEY_COLLISION` product는 sidecar에 없다.
- matched 495 rows는 raw long ES hit를 JSONB로 보존한다.
- short ES hit는 492 rows에 존재한다. 이는 source ES short alias 커버리지의
  차이이며, long summary는 495 matched rows 모두에 있다.

실행/검증 상세는
[`../superpowers/plans/2026-06-17-review-summary-sidecar-finalization-plan.md`](../superpowers/plans/2026-06-17-review-summary-sidecar-finalization-plan.md)에
남겼다.
