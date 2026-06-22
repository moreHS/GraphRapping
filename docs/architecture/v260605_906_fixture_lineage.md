# v260605 906 Review Fixture Lineage And Contract

작성일: 2026-06-16

이 문서는 GraphRapping의 주 테스트 데이터셋인
`mockdata/review_triples_raw.json` 906건과 그와 함께 생성된
`mockdata/product_catalog_es.json` 517개 상품 universe의 출처, 의미,
현재 DB 조합 상태, 한계를 기록한다.

## 1. 결론

현재 `graphrapping` DB에 적재된 906개 리뷰는
`mockdata/review_triples_raw.json`에서 온 것이며,
`scripts/synthesize_mock_from_v260605.py`로 재생성한 결과와 일치한다.

이 fixture의 핵심 contract는 다음이다.

1. `source_product_id`는 source product id 문자열 그대로이며,
   `product_catalog_es.json.ONLINE_PROD_SERIAL_NUMBER`,
   `product_master.product_id`, `product_master.source_product_id`,
   `review_raw.source_product_id`,
   `review_catalog_link.matched_product_id`와 직접 연결된다.
2. 리뷰 본문 안의 `Review Target`은 해당 review row의 target product를
   가리키는 placeholder다. 상품명이 본문에 문자 그대로 등장할 필요는 없다.
3. relation/BEE/NER 분석 결과는 `Review Target`을 통해 해당 product id의
   graph evidence로 연결되어야 한다.
4. 현재 fixture는 source brand, source review volume, source rating을
   제공하지 않는다. 이 값은 가짜로 채우지 않고 NULL 또는
   `MISSING_SOURCE_BRAND`로 보존한다.

## 2. 생성 경로

생성 스크립트:

- `scripts/synthesize_mock_from_v260605.py`

입력 파일:

- `/Users/amore/Jupyter_workplace/Relation/source_data/ver260605/final_relation_ko_ner2ner.jsonl`
  - 1,400 rows
  - NER-NER relation annotation
- `/Users/amore/Jupyter_workplace/Relation/source_data/ver260605/fin_ko_ner2bee_true_0528.jsonl`
  - 1,495 rows
  - NER-BeE relation annotation
- `/Users/amore/Jupyter_workplace/Relation/source_data/ver260605/rs_own.jsonl`
  - 3,410 rows
  - `product_id`, `prd_nm`, channel, date, reviewer profile metadata source

합성 절차:

1. NER-NER와 NER-BeE의 `id` overlap 998건을 잡는다.
2. overlap 중 broken markup이 있는 N2B 92건을 제외한다.
3. 최종 usable id 906건을 만든다.
4. text, NER, BEE, relation은 relation annotation 파일에서 변환한다.
5. `rs_own.jsonl`을 정렬한 뒤 `random.Random(42).sample(k=906)`으로
   metadata row를 붙인다.
6. `rs_own.product_id`를 `source_product_id`로 string 보존한다.
7. 906개 review가 참조하는 distinct product id 517개로
   `product_catalog_es.json`을 재생성한다.

재현 확인:

- review count: 906
- catalog count: 517
- synthesized failure: 0
- review file hash:
  `625386841199c94b54bb50be953090f73222b76b521addc56881664e0b5569d0`
- catalog file hash:
  `feffb18b6cb19e7c95c9acf6d9c70322a7cc708c652f7d575c2150a22be4ead7`

## 3. Fixture 의미

`review_triples_raw.json`의 한 review row는 다음 의미를 가진다.

- `source_review_key`: fixture 안에서 stable한 review source key
- `source_product_id`: 해당 review가 대상으로 삼는 source product id
- `prod_nm`: 해당 review의 target product name
- `text`: relation annotation용 리뷰 본문
- `ner`: 본문 내 entity mention
- `bee`: BEE phrase mention
- `relation`: NER-NER 또는 NER-BeE relation result

본문의 `Review Target`은 `prod_nm` 문자열 자체가 아니라 review target
placeholder다. GraphRapping은 이 placeholder를
`review_catalog_link.matched_product_id`의 product IRI로 풀어야 한다.

따라서 이 fixture를 검증할 때는 다음을 본다.

- `source_product_id`가 catalog/product master와 직접 연결되는가
- `Review Target` NER/REL이 target product로 resolve되는가
- BEE/REL 결과가 target product id를 가진 canonical fact/signal로
  승격되는가
- 승격되지 않은 relation도 raw/provenance 계층에 남는가

다음은 검증 기준이 아니다.

- `prod_nm` 전체 문자열이 `text`에 문자 그대로 등장하는지
- product name token 일부가 본문에 등장하는지

실제 운영 리뷰에서도 상품 식별은 본문 문자열 매칭이 아니라 source row의
`prd_id`/`prd_nm` metadata로 하는 것이 정상 경로다.

## 4. 현재 DB 적재 상태

2026-06-16 현재 로컬 `graphrapping` DB의 fresh full load 기준:

| Table | Count | 의미 |
|---|---:|---|
| `product_master` | 517 | fixture product universe |
| `review_raw` | 906 | fixture review rows |
| `review_catalog_link` | 906 | source id exact link |
| `ner_raw` | 4,507 | NER mentions |
| `bee_raw` | 2,783 | BEE mentions |
| `rel_raw` | 20,741 | relation rows |
| `canonical_fact` | 3,873 | canonicalized facts |
| `wrapped_signal` | 2,801 | projected product signals |
| `signal_evidence` | 2,839 | signal to fact provenance |
| `agg_product_signal` | 6,849 | windowed product aggregates |
| `serving_product_profile` | 517 | consumer-facing product payload |
| `product_review_stats` | 0 | source review stats absent in fixture |

Link checks:

- `review_catalog_link` total: 906
- `source_product_id = matched_product_id`: 906
- missing matched product: 0
- `wrapped_signal.target_product_id` missing: 0
- `wrapped_signal.target_product_id` not in `product_master`: 0

`Review Target` checks:

- reviews with `Review Target` PRD NER mention: 906
- `Review Target` PRD NER mentions: 906
- relations where subject is `Review Target`: 6,296
- relations where object is `Review Target`: 3,601
- NER-BeE relations where subject is `Review Target`: 2,695

## 5. 세 축 조합 현황

GraphRapping이 현재 조합하는 데이터 축은 다음 상태다.

### 5.1 상품마스터

현재 fixture catalog는 `product_catalog_es.json`에서 온다.

조합되는 것:

- `product_id`
- `source_product_id`
- `product_name`
- `representative_product_name`
- keyword-rule 기반 `category_name`
- source truth quality

빠져 있는 것:

- source-grounded brand
- source-grounded price
- source-grounded main effects
- source-grounded ingredients
- source review count/rating

이 누락은 의도적으로 NULL 또는 placeholder quality로 남긴다. product name
token에서 brand를 만들지 않는다.

### 5.2 리뷰 분석 결과

relation fixture는 가장 많이 조합되는 축이다.

보존되는 것:

- raw NER: `ner_raw`
- raw BEE: `bee_raw`
- raw REL: `rel_raw`
- canonical fact: `canonical_fact`
- signal projection: `wrapped_signal`
- signal provenance: `signal_evidence`
- product aggregate: `agg_product_signal`
- serving top fields: `serving_product_profile.top_*`

현재 signal family 분포:

| Signal family | Count |
|---|---:|
| `BEE_ATTR` | 2,447 |
| `BEE_KEYWORD` | 238 |
| `CONTEXT` | 65 |
| `CATALOG_VALIDATION` | 40 |
| `COMPARISON` | 9 |
| `COUSED_PRODUCT` | 2 |

Serving profile에서 promoted-only로 노출되는 product 수:

| Field | Products |
|---|---:|
| `top_bee_attr_ids` | 26 |
| `top_keyword_ids` | 5 |
| `top_context_ids` | 0 |
| `top_concern_pos_ids` | 0 |
| `top_concern_neg_ids` | 0 |
| `top_tool_ids` | 0 |
| `top_comparison_product_ids` | 0 |

승격되지 않은 relation도 raw table에는 남아 있다. 다만 serving profile은
promotion gate와 projection registry를 통과한 일부 signal만 담는다.

### 5.3 리뷰 요약 ES / source review stats

현재 코드에는 source review stats를 받는 contract와 저장소가 있다.

- `FullLoadConfig.source_review_stats_by_product`
- `product_review_stats`
- `serving_product_profile.source_review_*`
- `src/loaders/source_review_stats_loader.py`
- product catalog fallback `REVIEW_COUNT` / `REVIEW_SCORE`

하지만 현재 906 fixture full load에는 이 세 번째 축이 들어오지 않았다.

현재 DB 상태:

- `product_review_stats`: 0 rows
- `review_raw.source_rating`: 0 rows
- `product_master.source_review_count`: 0 rows
- `serving_product_profile.source_review_count_all`: all NULL
- `serving_product_profile.source_avg_rating_all`: all NULL

따라서 현재 산출물은 `상품마스터 + relation graph` 중심이며,
리뷰요약 ES 또는 source review stats를 풍부하게 결합한 최종 산출물은 아니다.
별도 리뷰요약 ES 데이터가 있다면 그 데이터를
`source_review_stats_by_product` 또는 별도 loader/contract로 연결해야 한다.

## 6. 현재 보완 필요 지점

1. **Legacy path attribution 관측성 부족**

   `Review Target` 기반 gating은 동작하지만, 현재 DB의
   `canonical_fact.target_linked`와 `wrapped_signal.target_linked`는 모두 NULL이다.
   legacy `kg_mode=off` 경로에서 target-linked BEE만 통과시키고, 그
   attribution source/confidence를 fact/signal row에 남기지 않기 때문이다.

   보완:

   - `bee_raw` 또는 별도 attribution table에 `target_linked`,
     `attribution_source`, `attribution_confidence`, `matched_rel_idx`를 저장한다.
   - `CanonicalFactBuilder.add_bee_facts()`가 attribution metadata를 받아
     `canonical_fact`와 `wrapped_signal`까지 전달하게 한다.

2. **Unlinked BEE의 canonical evidence-only 보존 부족**

   raw `bee_raw`/`rel_raw`에는 남지만, legacy path에서는 target-linked가
   아닌 BEE를 canonical `EVIDENCE_ONLY` fact로 만들지 않고 skip한다.
   source 정보 손실 방지 관점에서는 raw 보존만으로 충분한지, canonical
   evidence layer까지 필요한지 결정해야 한다.

3. **Projection/keyword coverage 부족**

   현재 quarantine:

   - `quarantine_projection_miss`: 4,475
   - `quarantine_unknown_keyword`: 2,477
   - `quarantine_placeholder`: 2,303
   - `quarantine_product_match`: 0

   product id matching은 안정적이지만, relation 결과의 상당 부분은
   serving signal로 승격되지 않는다. fixture를 주 테스트데이터로 쓸 경우
   raw preservation test와 serving projection coverage test를 분리해야 한다.

4. **리뷰요약/source stats 축 미적재**

   code hook과 DB schema는 있지만 현재 fixture와 local DB에는 값이 없다.
   실제 최종 산출물이 상품마스터, relation 결과, 리뷰요약 ES 세 축을 모두
   써야 한다면 이 축은 별도 ingestion path와 regression test가 필요하다.

## 7. Dense Golden 평가 Fixture

2026-06-22 기준으로 추천 품질 검증용 별도 fixture를 추가했다.

- 위치: `mockdata/dense_golden/`
- 리뷰 수: 906
- 선택 상품 수: 32
- 사용자 프로필 수: 6
- 목적: source identity 회귀가 아니라 추천 품질, category tab, promoted
  review evidence 활용도를 보기 위한 dense 평가 데이터

Wide fixture와 역할이 다르다.

| Fixture | Products | 목적 |
|---|---:|---|
| `mockdata/` | 517 | source identity, product master join, DB/full-load baseline |
| `mockdata/dense_golden/` | 32 | recommendation QA, graph evidence utilization, golden profile checks |

Dense fixture는 906개 review text/NER/BEE/REL annotation을 유지하고,
`source_product_id`, `prod_nm`, `brnd_nm` 등 product metadata만 source-grounded
상위 리뷰수 상품으로 deterministic remap한다. 원래 mapping은
`fixture_original_source_product_id`, `fixture_original_prod_nm`,
`fixture_remap_reason`에 남긴다.

카테고리 분포:

| Category group | Products |
|---|---:|
| skincare | 11 |
| makeup | 6 |
| bodycare | 5 |
| haircare | 5 |
| fragrance | 5 |

추천 audit의 최종 기준:

- `kg_on` signal count: 2,767
- wide fixture `kg_on` serving `top_keyword_ids`: 5 products
- dense fixture `kg_on` serving `top_keyword_ids`: 18 products / 22 items
- source review stats snapshot is loaded explicitly for audit/UI so
  `source_review_count_6m`, `source_avg_rating_6m`, min/max dates are visible.
- source review stats remain trust/tie-break data, not graph evidence or
  candidate eligibility data.

Recommendation evidence rules:

- product master brand/category/ingredient/benefit truth is first-class
  evidence.
- promoted review graph evidence is first-class only when exact or
  semantic-value compatible.
- generic axes such as `bee_attr_formulation` do not qualify candidates by
  exact BEE attr match.
- group-level category aliases such as `perfume -> fragrance` are allowed for
  personal-agent profile intent; detailed category ids remain exact-match only.

## 8. Fixture 사용 가이드

이 fixture로 반드시 고정해야 하는 테스트:

- `source_product_id` exact link 906/906
- `Review Target` PRD mention 906/906
- `ner_raw`, `bee_raw`, `rel_raw` count가 fixture 총량과 일치
- `wrapped_signal.target_product_id`가 모두 `product_master.product_id`에 존재
- source brand/stats가 없는 경우 NULL을 유지하고 0이나 fake value로 채우지 않음
- serving profile은 promoted-only이므로 raw relation count보다 작을 수 있음

이 fixture로 고정하면 안 되는 테스트:

- brand/display truth 정확도
- source review volume/rating ranking
- 상품명이 review text에 등장한다는 lexical assumption
- 모든 relation이 serving signal로 승격된다는 assumption

## 9. 관련 문서

- `mockdata/README.md`
- `DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md`
- `DECISIONS/2026-06-15_source_grounded_product_contract_plan.md`
- `docs/architecture/db_consumer_contract.md`
- `docs/architecture/amoresim_handoff_2026_06_16.md`
