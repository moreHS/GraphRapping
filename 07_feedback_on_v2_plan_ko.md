# GraphRapping 구현 계획 v2 피드백 (추가 보강 권고)

## 총평

현재 v2 계획은 아키텍처 방향이 맞고, 바로 구현 착수 가능한 수준에 거의 도달했다. 특히 아래는 적절하다.

- 5-Layer 분리
- Postgres-first hybrid
- Layer 2에서 65 predicate 보존
- Layer 3에서만 projection
- Common Concept Layer 도입
- reviewer proxy와 real user 분리
- deterministic projection registry
- quarantine를 explicit path로 설계
- idempotency / provenance / truth override 방어를 acceptance criteria에 포함

다만 실제 구현 단계에서 충돌하거나 재작업이 날 수 있는 지점이 아직 있다. 아래 항목들은 구현 전에 문서에 추가로 못 박는 것을 권장한다.

---

## P0. 반드시 먼저 보강할 것

### P0-1. `canonical_entity` / `concept_registry` / `product_master` 경계 명확화

현재 문서에는 `canonical_entity`와 `concept_registry`가 모두 있고, Layer 0 master truth도 있다. 이 3개가 서로 어떤 역할을 맡는지 문서에 더 명확히 써야 한다.

권장 정의:

- `product_master`, `user_master`: 운영 정본 레코드
- `concept_registry`: user/product가 공유하는 **추상 개념 사전**
  - Brand, Category, Ingredient, BEEAttr, Keyword, TemporalContext, Concern, Goal, Tool, SkinType 등
- `canonical_entity`: review/user raw에서 추출된 mention이 정규화되어 연결되는 **실체 레이어**
  - Product, ReviewerProxy, Brand entity, Ingredient entity, OtherProduct entity 등

권장 규칙:

- Product master row는 `product_id`를 갖고, 관련 concept는 link table로 연결
- user canonical facts는 concept를 직접 참조
- canonical fact는 `subject_iri`, `object_iri` 또는 value를 가지되, object가 concept인 경우 명시적으로 concept_iri를 사용

추가 권장 테이블:

```sql
entity_concept_link (
  entity_iri text not null,
  concept_id text not null references concept_registry(concept_id),
  link_type text not null,   -- HAS_BRAND|IN_CATEGORY|HAS_INGREDIENT|HAS_BEE_ATTR|HAS_CONCERN|...
  confidence real,
  source text,
  primary key (entity_iri, concept_id, link_type)
);
```

---

### P0-2. `wrapped_signal` 스키마를 문서에 명시

현재 계획서에는 signal emitter가 있지만, 실제 Layer 3 전 단계의 표준 row shape가 빠져 있다. 이게 없으면 `aggregate_product_signals.py`와 `build_serving_views.py`의 입력 계약이 모호해진다.

권장 테이블:

```sql
wrapped_signal (
  signal_id text primary key,
  review_id text,
  user_id text,
  target_product_id text,
  source_fact_id text not null references canonical_fact(fact_id),
  signal_family text not null,         -- DESCRIPTOR|CONTEXT|TOOL|CONCERN_POS|CONCERN_NEG|COMPARISON|SEGMENT|...
  edge_type text not null,             -- HAS_KEYWORD_SIGNAL|USED_IN_CONTEXT|USED_WITH_TOOL|SUITED_FOR|AVOID_FOR|...
  dst_type text not null,              -- Keyword|TemporalContext|Tool|Concern|Product|UserSegment|...
  dst_id text not null,
  bee_attr_id text,
  keyword_id text,
  polarity text,
  negated boolean,
  intensity real,
  weight real not null,
  registry_version text not null,
  window_ts timestamptz,
  created_at timestamptz not null default now()
);
```

핵심 포인트:

- Layer 2는 `canonical_fact`
- Layer 2.5/3 입력은 `wrapped_signal`
- aggregate는 `wrapped_signal`만 읽도록 고정

---

### P0-3. IRI / ID 규칙을 프로젝트 전역 계약으로 확정

현재 `deterministic ID` 아이디어는 좋지만, 구체 패턴이 필요하다.

권장 예:

- `review:{source}:{source_review_key}`
- `reviewer_proxy:{source}:{source_author_key_or_review_key}`
- `product:{product_id}`
- `concept:{concept_type}:{concept_id}`
- `fact:{hash(review_id|subj|pred|obj|source_modality|row_ref)}`
- `signal:{hash(review_id|target_product_id|edge_type|dst_id|registry_version)}`

반드시 문서에 포함할 것:

- 어떤 입력 조합으로 어떤 ID가 생성되는지
- hash salt/version 사용 여부
- source key가 없는 경우 fallback 규칙

---

### P0-4. `target_product_id`는 항상 생성되지 않을 수 있음

현재 acceptance criteria에 `raw review 1건 -> review_id, reviewer_proxy_id, target_product_id 생성`이 들어 있는데, product matcher quarantine가 존재하는 이상 `target_product_id`는 실패할 수 있다.

따라서 문장을 수정하는 게 맞다.

권장 수정:

- `review_id`, `reviewer_proxy_id`는 항상 생성
- `target_product_id`는 resolve 성공 시 생성
- 실패 시 `quarantine_product_match`에 적재되고 `review_catalog_link.match_status='QUARANTINE'`

권장 테이블:

```sql
review_catalog_link (
  review_id text primary key,
  source_brand text,
  source_product_name text,
  matched_product_id text,
  match_status text not null,   -- EXACT|NORM|ALIAS|FUZZY|QUARANTINE
  match_score real,
  match_method text,
  created_at timestamptz default now()
);
```

---

### P0-5. `qualifier_required`를 쓸 거면 qualifier 저장소도 필요

projection registry에 `qualifier_required`, `qualifier_type`가 있는데, 현재 canonical fact에는 qualifier를 구조적으로 저장하는 전용 테이블이 없다.

권장:

```sql
fact_qualifier (
  fact_id text not null references canonical_fact(fact_id),
  qualifier_key text not null,
  qualifier_type text not null,     -- context|time|duration|frequency|segment|tool|reason|...
  qualifier_iri text,
  qualifier_value_text text,
  qualifier_value_num double precision,
  qualifier_value_json jsonb,
  primary key (fact_id, qualifier_key, coalesce(qualifier_iri, qualifier_value_text))
);
```

예:

- `recommended_to(target, mother)` + nearby `dry skin`
- canonical fact는 `RECOMMENDED_TO(target, mother_proxy)`
- qualifier에는 `segment=dry_skin`
- projection 시 `RECOMMENDED_TO_SEGMENT(dry_skin)` 생성

---

### P0-6. truth override 정책을 더 세분화

현재 원칙은 좋지만, 실제 구현 규칙이 필요하다.

권장 정책:

1. **master truth fields**
   - brand, category, ingredients, country, price, main_benefits
   - review-derived signal은 절대 overwrite 금지

2. **review validation/enrichment fields**
   - review에서 나온 ingredient/brand/category mention은 `catalog_validation_signal`로만 저장
   - conflict가 나면 quarantine 또는 QA queue로 보냄

3. **review-native fields**
   - context, tool, concern, descriptor, comparison, segment targeting
   - 리뷰에서만 생성 가능

즉 `has_ingredient`가 raw relation에서 나와도 product master를 수정하지 않고, `catalog_validation` 테이블/시그널로만 보관해야 한다.

---

### P0-7. event time 기준을 명시

window_type 30d/90d/all은 적절하지만, 기준 시간이 필요하다.

권장 규칙:

- 우선순위 1: review source의 original created_at
- 우선순위 2: collection timestamp
- 둘 다 없으면 processing time 사용 + flag 기록

권장 컬럼:

```sql
review_raw.event_time timestamptz,
review_raw.event_time_source text  -- SOURCE_CREATED|COLLECTED_AT|PROCESSING_TIME
```

이 기준이 없으면 freshness score와 window aggregate 결과가 흔들린다.

---

## P1. 강하게 권장하는 추가 보강

### P1-1. scorer에서 `bee_attr_match`와 `keyword_match`의 이중계산 방지

현재 baseline 식은 이해하기 쉽지만, `bee_attr_match`와 `keyword_match`는 상관이 높아서 double counting 위험이 있다.

권장 대안 1:
- 먼저 BEEAttr 점수 계산
- 그다음 keyword는 BEEAttr 내부 fine-grain boost로만 사용

권장 대안 2:
```text
feature_score = 0.28 * keyword_match
              + 0.12 * residual_bee_attr_match
```

즉 keyword가 이미 attr를 대표하면 attr weight를 줄이는 방식이 더 안전하다.

---

### P1-2. recommendation mode 분리

현재 hard filter에 `category mismatch 강함 -> eliminate`가 들어 있는데, 이는 `strict recommendation` 모드에는 맞지만 `discovery/exploration` 모드에는 너무 강할 수 있다.

권장:

- `mode=strict`: category mismatch zero-out
- `mode=explore`: category mismatch penalty만 부여
- `mode=compare`: comparison-neighbor 제품 허용

즉 candidate generator/scorer에 `recommendation_mode`를 명시적으로 넣는 게 좋다.

---

### P1-3. `build_serving_views.py` 결과를 materialized view 또는 mart table로 보는 것이 현실적

통합 서빙 뷰는 좋지만, product/user serving은 실제로는 자주 읽히는 profile이라 `view`만으로 끝내기보다 materialized view나 precomputed mart table이 더 낫다.

권장 이름:

- `serving_product_profile`
- `serving_user_profile`

권장 컬럼 예:

- truth columns
  - brand_id, category_id, ingredient_ids, country, price_band, main_benefit_ids
- signal columns
  - top_bee_attr_ids, top_keyword_ids, top_context_ids, top_concern_pos_ids, top_concern_neg_ids, top_tool_ids, top_comparison_product_ids
- freshness columns
  - last_signal_at, review_count_30d, review_count_all

---

### P1-4. dictionary growth는 semi-automatic approval flow가 필요

`dictionary_growth.py`가 들어간 건 좋다. 다만 auto-add는 위험하다.

권장 루프:

1. unknown keyword quarantine 적재
2. surface clustering
3. candidate concept/keyword 추천
4. human approval or ruleset approval
5. dictionary version bump
6. 재처리(backfill)

즉 `dictionary_version`을 signal/fact에 기록하는 것도 고려할 만하다.

---

### P1-5. Product matcher의 threshold/metrics 정의

이 모듈은 전체 파이프라인의 성패를 좌우한다. 테스트뿐 아니라 평가 기준이 필요하다.

권장:

- exact precision = 0.99 목표
- alias precision = 0.97 목표
- fuzzy precision = 0.90 이상 아니면 quarantine
- fuzzy auto-accept threshold 예: 0.93
- 0.80~0.93 manual review
- <0.80 quarantine

그리고 최소 200~500건 정도의 gold set을 별도 마련하는 것을 권장한다.

---

### P1-6. incremental pipeline의 tombstone / late-arrival 정책

지금 계획에 incremental pipeline은 있지만, 리뷰 삭제/수정/중복 수집 대응 정책이 없다.

권장:

- raw는 append-only
- source duplicate는 `source + source_review_key` 기준 dedup
- 수정본이 오면 `review_version` 증가
- aggregate는 recompute window or delta update
- 삭제(tombstone)가 오면 active=false 처리하고 재집계

---

### P1-7. explanation evidence 구조를 별도 테이블로 두는 것도 고려

`fact_provenance`와 `evidence_sampler`가 있으면 충분할 수도 있지만, explanation 품질을 안정적으로 유지하려면 signal-level evidence 연결이 유용하다.

예:

```sql
signal_evidence (
  signal_id text not null,
  fact_id text not null,
  evidence_rank int not null,
  contribution real,
  primary key (signal_id, fact_id, evidence_rank)
);
```

이렇게 하면 설명이 "실제로 점수에 기여한 signal/fact/evidence" 기준으로 정렬 가능하다.

---

## P2. 있으면 더 좋은 보강

### P2-1. `ProductFamily`는 optional이지만 beauty 도메인에서는 빨리 유용해질 가능성이 큼

현재 optional로 둔 건 괜찮다. 다만 쿠션/립/향 제품에서 shade/volume variant가 많다면 `product_id` 하나로만 보면 signal이 흩어질 수 있다.

MVP는 product_id anchor로 가되, Sprint 4 이후 `product_family_id` 지원을 열어두는 것이 좋다.

---

### P2-2. `AbsoluteDate`는 serving에 거의 안 쓰더라도 버리면 아까움

예: "2024 여름 세일", "3월 1일에 샀다" 같은 표현은 직접 추천엔 약하지만, seasonality / campaign analysis엔 쓸모가 있다.

Layer 2 보존 원칙은 맞고, analyst mart에서 활용 가능성을 남겨두면 좋다.

---

### P2-3. Graph projection 전환 조건을 문서에 숫자로 명시

현재 `SQL serving → AGE → Neo4j` 순서는 아주 좋다. 여기에 객관적 전환 조건이 있으면 더 좋다.

예:

- recursive join이 3단계 이상 반복되고 analyst query 유지보수 비용이 높아질 때 AGE 검토
- explanation path / neighbor exploration / graph analyst 요구가 증가하고, SQL path query P95가 목표 초과할 때 Neo4j 검토

---

## 모듈별 짧은 세부 피드백

### `src/common/ids.py`
- deterministic hash inputs를 문서에 명시
- hash collisions 대응 정책 필요
- `registry_version`, `dictionary_version`, `extraction_version`이 ID 생성에 들어갈지 여부 명시

### `src/link/product_matcher.py`
- `match_status`, `match_score`, `match_method` 출력 고정
- brand stripping / shade parsing / volume parsing 규칙 명시
- exact/norm/alias/fuzzy 순서와 cutoff 문서화

### `src/link/placeholder_resolver.py`
- `Review Target`, `Reviewer`, `I`, `my`, `it`, `this` 처리 규칙 예시 추가
- review-local entity resolution 결과를 canonical_fact 이전에 저장 가능하면 디버깅이 쉬워짐

### `src/normalize/bee_normalizer.py`
- `surface_forms[]`와 `raw_phrase`를 함께 남기기
- negation과 polarity를 동시에 유지하는 contract 명시

### `src/normalize/tool_concern_segment_deriver.py`
- dict -> normalized string -> pattern -> fallback classifier 순서 문서화
- `used_with`에서 Tool/Product 분기 규칙 추가
- `recommended_to(target, mother)`처럼 person mention을 segment로 승격하는 rule 예시 추가

### `src/wrap/projection_registry.py`
- 실제 corpus combo 전수 검사 또는 샘플 기반 completeness test 정의
- 미매핑 combo는 반드시 DROP/QUARANTINE/KEEP_CANONICAL_ONLY 중 하나

### `src/wrap/signal_emitter.py`
- dedup key에 polarity/negated/intensity 포함 여부를 명시
- multi-source fusion(BEE+REL 동시) 시 weight merge 전략 필요

### `src/mart/aggregate_product_signals.py`
- polarity aggregation 규칙 필요
- 긍/부정 conflicting signal에서 `score` 산식 문서화

### `src/rec/scorer.py`
- weights config화는 좋음
- hard filter vs soft score를 코드 레벨에서 완전히 분리
- support_count shrinkage는 overall support인지 signal-local support인지 정의 필요

### `src/rec/explainer.py`
- score contributor top-n 기반으로만 explanation 생성
- evidence는 `signal_evidence` 또는 `fact_provenance`에서 top-k refs 사용

---

## acceptance criteria에 추가 추천

현재도 충분히 좋지만, 아래 5개를 더 넣으면 훨씬 강해진다.

1. `projection registry completeness test`가 실제 observed combo 기준 통과
2. `review_catalog_link`에서 unresolved match가 quarantine 상태로 정확히 집계
3. `catalog_validation_signal`이 product master를 overwrite하지 않음
4. `event_time_source`가 누락 없이 기록됨
5. `wrapped_signal -> aggregate -> serving profile -> recommendation -> explanation` 전체 역추적이 1건 이상 성공

---

## 최종 평가

현재 v2 계획은 **방향이 틀리지 않았고, 실질적으로 구현에 들어갈 수 있다.**
다만 아래 P0만 먼저 문서에 더 박고 시작하는 것이 좋다.

1. canonical_entity / concept_registry / product_master 경계
2. wrapped_signal 스키마
3. ID / IRI 규칙
4. unresolved target_product 처리 규칙
5. fact_qualifier 구조
6. truth override 세부 정책
7. event_time 기준

이 7개를 보강하면, 이후 구현은 비교적 안정적으로 진행될 가능성이 높다.
