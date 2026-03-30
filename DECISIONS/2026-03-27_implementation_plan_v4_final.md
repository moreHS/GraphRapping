# GraphRapping 프로젝트 구현 계획 (v4 Final — implementation-ready)

## Context

뷰티 리뷰에서 추출된 NER(10종) + BEE(39종) + REL(65개 canonical) 데이터를 상품 DB 정본 기반의 의미 신호 그래프로 재구성하고, 유저 그래프와 공통 개념층에서 연결하여 설명 가능한 추천/개인화 시스템을 만드는 프로젝트.

**검증 이력**: GPT Architect 1차 → 06_feedback 12개 → 07_feedback P0 7개+P1 7개 → v3 최종 피드백 10개 반영

---

## 구현 시작 전 7대 불변 원칙

1. **Layer 2는 relation 65개를 절대 잃어버리지 않는다.**
2. **Layer 3는 Projection Registry를 통해서만 생성한다. 임의 projection 금지.**
3. **Product/User 연결은 shared concept_id를 통해서만 한다.**
4. **reviewer proxy와 real user는 절대 merge하지 않는다.**
5. **Product master truth는 review-derived signal로 override하지 않는다.**
6. **모든 signal은 provenance 역추적이 가능해야 한다.**
7. **매핑 실패/미분류는 침묵 drop이 아니라 explicit quarantine가 기본이다.**

---

## Architecture (5-Layer)

```
Layer 0: Product/User Master (truth)
  - product_master, user_master, purchase_event_raw
  - 운영 정본 레코드

Layer 1: Raw / Evidence Layer
  - review_raw, ner_raw, bee_raw, rel_raw, review_catalog_link
  - append-only, 감사/재처리/근거 회수

Layer 2: Canonical Fact Layer
  - canonical_entity: raw mention이 정규화된 실체 (Product, ReviewerProxy, Brand entity 등)
  - canonical_fact + fact_provenance + fact_qualifier
  - 65 canonical relations 보존

Layer 3: Serving / Aggregate Layer
  - wrapped_signal → agg_product_signal (windowed) → serving_product_profile
  - canonical_user_fact → agg_user_preference → serving_user_profile
  - projection registry 기반

Layer 4: Recommendation / Explanation
  - candidate generation, scoring, reranking, explanation, hook, next-question

Common Concept Layer (전 레이어 공유):
  - concept_registry: user/product가 공유하는 추상 개념 사전
  - concept_alias: 다국어/romanization alias
  - entity_concept_link: entity↔concept 연결
```

**인프라**: Postgres-first hybrid (SoR=PostgreSQL, AGE/Neo4j=optional projection)

---

## P0 보강 사항 (07_feedback 반영)

### P0-1. canonical_entity / concept_registry / product_master 경계

| 테이블 | 역할 | 예시 |
|--------|------|------|
| `product_master` | 운영 정본 레코드 | product_id, brand, category, price, ingredients |
| `user_master` | 운영 정본 레코드 | user_id, age, gender, skin_type |
| `concept_registry` | user/product 공유 추상 개념 사전 | Brand:라네즈, Category:쿠션, Ingredient:세라마이드, BEEAttr:밀착력, Concern:건조함 |
| `canonical_entity` | raw mention→정규화된 실체 | Product entity, ReviewerProxy, OtherProduct entity |

```sql
-- Product master의 concept 연결
entity_concept_link (
  entity_iri text not null,
  concept_id text not null references concept_registry(concept_id),
  link_type text not null,   -- HAS_BRAND|IN_CATEGORY|HAS_INGREDIENT|HAS_BEE_ATTR|HAS_CONCERN|...
  confidence real,
  source text,               -- product_db|review_extraction|user_chat
  primary key (entity_iri, concept_id, link_type)
);
```

규칙:
- Product master row → `product_id`, 관련 concept는 `entity_concept_link`로 연결
- User canonical facts → concept를 직접 참조 (concept_iri)
- canonical_fact의 object가 concept인 경우 → 명시적으로 concept_iri 사용

### P0-2. wrapped_signal 스키마 명시 (★ v4: dst_ref_kind 추가)

```sql
wrapped_signal (
  signal_id text primary key,
  review_id text,
  user_id text,
  target_product_id text,
  source_fact_id text not null references canonical_fact(fact_id),
  signal_family text not null,     -- BEE_ATTR|BEE_KEYWORD|CONTEXT|TOOL|CONCERN_POS|CONCERN_NEG|COMPARISON|COUSED_PRODUCT|SEGMENT|CATALOG_VALIDATION
  edge_type text not null,         -- HAS_BEE_ATTR_SIGNAL|HAS_BEE_KEYWORD_SIGNAL|USED_IN_CONTEXT_SIGNAL|USED_WITH_PRODUCT_SIGNAL|...
  dst_type text not null,          -- BEEAttr|Keyword|TemporalContext|Tool|Concern|Product|UserSegment
  dst_id text not null,
  dst_ref_kind text not null,      -- ★ v4: ENTITY|CONCEPT|TEXT|NUMBER|JSON
  bee_attr_id text,                -- BEE 계열일 때 attr 연결
  keyword_id text,                 -- BEE 계열일 때 keyword 연결
  polarity text,
  negated boolean,
  intensity real,
  weight real not null,
  registry_version text not null,
  window_ts timestamptz,           -- event_time 기준
  created_at timestamptz not null default now()
);
```

계약: Layer 2 = `canonical_fact`, Layer 2.5→3 입력 = `wrapped_signal`, aggregate는 `wrapped_signal`만 읽음.

★ `signal_family`에 `COUSED_PRODUCT` 추가: `used_with`에서 dst가 Product일 때 루틴/번들/레이어링 추천용.

### P0-3. IRI / ID 규칙 전역 계약 (★ v4: 타입별 분리 + fact_id canonical merge 수정)

```python
# src/common/ids.py — deterministic ID patterns (entity_type별 전략 분리)

# Review / ReviewerProxy
review_id     = "review:{source}:{source_review_key}"
  # fallback (source_review_key 없을 때): "review:{source}:{md5(brand|product_name|review_text|collected_at|source_row_num)}"
  # ★ 짧은 리뷰 중복 collapse 방지: collected_at/source_row_num 등 추가 입력 포함
reviewer_proxy = "reviewer_proxy:{source}:{author_key}"       # stable author key 있을 때
  # fallback: "reviewer_proxy:{review_id}"                    # author key 없을 때
  # ★ identity_stability 컬럼 추가: STABLE | REVIEW_LOCAL

# Product / Concept
product_iri   = "product:{product_id}"
concept_iri   = "concept:{concept_type}:{concept_id}"

# Entity (★ 타입별 분리 — 전역 md5 금지)
entity_iri 전략:
  - Product     → "product:{product_id}"
  - ReviewerProxy → "reviewer_proxy:{...}" (위 규칙)
  - Brand/Category/Ingredient/BEEAttr/Keyword/... → "concept:{type}:{concept_id}"  (concept_registry 참조)
  - Review-local unresolved mention → "mention:{review_id}:{mention_idx}"  (merge 전까지 review-local)
  # ★ "entity:{type}:{md5(normalized_value)}" 같은 전역 규칙은 PER/unresolved 타입에서 잘못 합쳐질 수 있으므로 금지

# Canonical Fact (★ v4: canonical semantic key만으로 생성, raw_row_ref 제외)
fact_id       = "fact:{md5(review_id|subject_iri|predicate|object_ref|polarity|qualifier_fingerprint)}"
  # ★ source_modality, raw_row_ref는 fact_id에 포함하지 않음
  # ★ 같은 의미 fact가 BEE/REL 등 여러 modality에서 나와도 하나의 canonical_fact + 여러 provenance
  # ★ object_ref = object_iri or object_value_text (둘 중 있는 것)
  # ★ qualifier_fingerprint = md5(sorted(qualifier_key:qualifier_value pairs)) or '' if none

# Signal
signal_id     = "signal:{md5(review_id|target_product_id|edge_type|dst_id|polarity|registry_version)}"
```

규칙:
- hash salt: 없음 (deterministic 보장)
- hash collision 대응: INSERT 시 ON CONFLICT 체크
- `registry_version`은 signal_id에 포함 (registry 변경 시 새 signal 생성)
- **IRI strategy는 entity_type별로 다르다** — 단일 전역 규칙 금지

### P0-4. target_product_id 미해결 처리

```sql
review_catalog_link (
  review_id text primary key,
  source_brand text,
  source_product_name text,
  matched_product_id text,         -- NULL if unresolved
  match_status text not null,      -- EXACT|NORM|ALIAS|FUZZY|QUARANTINE
  match_score real,
  match_method text,
  created_at timestamptz default now()
);
```

- `review_id`, `reviewer_proxy_id`는 **항상** 생성
- `target_product_id`는 resolve 성공 시에만 생성
- 실패 시 `match_status='QUARANTINE'` + `quarantine_product_match`에 적재
- acceptance criteria 수정: "target_product_id는 resolve 성공 시 생성, 실패 시 quarantine 확인"

### P0-5. fact_qualifier 테이블 (★ v4: PK 수정)

```sql
fact_qualifier (
  qualifier_id bigserial primary key,
  fact_id text not null references canonical_fact(fact_id),
  qualifier_key text not null,
  qualifier_type text not null,     -- context|time|duration|frequency|segment|tool|reason
  qualifier_iri text,
  qualifier_value_text text,
  qualifier_value_num double precision,
  qualifier_value_json jsonb
);
-- ★ PK에 expression 불가하므로 surrogate key + unique index
create unique index uq_fact_qualifier
  on fact_qualifier (fact_id, qualifier_key, coalesce(qualifier_iri, ''), coalesce(qualifier_value_text, ''));
```

용도: projection registry의 `qualifier_required=Y`인 relation 처리.
예: `recommended_to(target, mother)` + qualifier `segment=dry_skin` → `RECOMMENDED_TO_SEGMENT(dry_skin)` projection

### P0-6. truth override 세부 정책

| 필드 유형 | 출처 | 규칙 |
|-----------|------|------|
| **master truth** (brand, category, ingredients, country, price, main_benefits) | product_master | review signal은 절대 overwrite 금지 |
| **validation/enrichment** (review에서 나온 ingredient/brand/category mention) | review extraction | `catalog_validation_signal`로만 저장, conflict → quarantine/QA queue |
| **review-native** (context, tool, concern, descriptor, comparison, segment targeting) | review only | 리뷰에서만 생성 가능, product master에 해당 필드 없음 |

`has_ingredient` raw relation → product master 수정 X, `catalog_validation_signal`로만 보관.

### P0-7. event_time 기준

```sql
-- review_raw에 추가
event_time timestamptz,
event_time_source text not null    -- SOURCE_CREATED|COLLECTED_AT|PROCESSING_TIME
```

우선순위:
1. review source의 original created_at → `SOURCE_CREATED`
2. collection timestamp → `COLLECTED_AT`
3. 둘 다 없으면 processing time + flag → `PROCESSING_TIME`

`wrapped_signal.window_ts`와 window aggregate의 기준 시간으로 사용.

---

## P1 보강 사항 (핵심만 반영)

### P1-1. bee_attr/keyword 이중계산 방지 (scoring)
```
# keyword가 이미 attr를 대표하면 attr weight 축소
feature_score = 0.28 * keyword_match
              + 0.12 * residual_bee_attr_match  (keyword로 커버 안 된 attr만)
              + 0.15 * context_match
              + 0.15 * concern_fit
              + 0.10 * ingredient_match
              + 0.08 * brand_match_conf_weighted
              + 0.07 * category_affinity
              + 0.05 * freshness_boost
```

### P1-2. recommendation mode
```python
class RecommendationMode(Enum):
    STRICT = "strict"      # category mismatch → zero-out
    EXPLORE = "explore"    # category mismatch → penalty only
    COMPARE = "compare"    # comparison-neighbor 허용
```
candidate_generator, scorer에 `mode` 파라미터 추가.

### P1-3. serving profile = materialized view or precomputed mart
- `serving_product_profile`: truth columns + signal columns + freshness columns
- `serving_user_profile`: demographics + preference edges + concern/goal
- view가 아닌 **materialized view 또는 mart table**로 실체화

### P1-5. product matcher threshold
- exact precision: 0.99 목표
- alias precision: 0.97 목표
- fuzzy auto-accept: ≥0.93
- fuzzy manual review: 0.80~0.93
- fuzzy quarantine: <0.80

### P1-6. incremental pipeline tombstone/late-arrival
- raw: append-only
- source duplicate: `source + source_review_key` dedup
- 수정본: `review_version` 증가
- 삭제(tombstone): `active=false` + 재집계
- aggregate: recompute window or delta update

### P1-7. signal_evidence 테이블
```sql
signal_evidence (
  signal_id text not null,
  fact_id text not null,
  evidence_rank int not null,
  contribution real,
  primary key (signal_id, fact_id, evidence_rank)
);
```
설명 시 "실제 점수에 기여한 signal/fact/evidence" 기준 정렬 가능.

---

## v4 최종 피드백 반영 (10개)

### F1. ★ canonical_fact_builder 모듈 추가
현재 구조에서 Layer 2 canonical_fact를 **실제로 생성하는 전용 모듈이 없었음**. 이것이 v3의 가장 큰 실무 누락.

추가: `src/canonical/canonical_fact_builder.py`

책임:
- resolved mention → `canonical_entity` upsert
- normalized triple/value → `canonical_fact` upsert
- raw row link → `fact_provenance` insert
- qualifier → `fact_qualifier` insert
- 여러 modality에서 같은 fact 발견 시 `source_modalities[]` union

### F2. canonical_fact에 object_ref_kind 추가
```sql
-- canonical_fact 추가 컬럼
object_ref_kind text not null,      -- ENTITY|CONCEPT|TEXT|NUMBER|JSON
source_modalities text[],           -- ★ array (source_modality 단일 필드 대체)
```
object가 entity/concept/literal인지 명시. projection registry validation, generic query builder, aggregate logic에 필수.

### F3. fact_id에서 raw_row_ref 제외 (canonical merge)
- fact_id = `md5(review_id|subj_iri|predicate|object_ref|polarity|qualifier_fingerprint)`
- source_modality, raw_row_ref는 fact_provenance로만 관리
- 같은 의미 fact가 BEE+REL 동시에 나와도 1 canonical_fact + N provenance

### F4. entity_iri 타입별 전략 분리
- Product → `product:{product_id}`
- ReviewerProxy → `reviewer_proxy:{source}:{author_key}` (stable) 또는 `reviewer_proxy:{review_id}` (local)
- Brand/Category/Ingredient/... → `concept:{type}:{concept_id}`
- Unresolved mention → `mention:{review_id}:{mention_idx}` (merge 전까지 review-local)
- **전역 `entity:{type}:{md5(value)}` 금지** — "엄마" 같은 PER이 다른 리뷰와 잘못 합쳐질 위험

### F5. USED_WITH_PRODUCT signal family 추가
```
signal_family에 COUSED_PRODUCT 추가
edge_type: USED_WITH_PRODUCT_SIGNAL
```
`used_with(target, X)` → X=Tool이면 `USED_WITH_TOOL_SIGNAL`, X=Product이면 `USED_WITH_PRODUCT_SIGNAL`
루틴/번들/레이어링 추천에 핵심.

### F6. purchase_ingest.py 추가
```
src/ingest/purchase_ingest.py  # Sprint 2
```
brand confidence weighting, loyalty/repurchase, hard exclusion/availability에 필요.
MVP defer 시 문서에 명시적으로 기록.

### F7. Goal concept → scoring 연결
현재 scoring 식에 goal_fit 없음. Common Concept Layer에 Goal이 있으므로:
```
+ 0.08 * goal_fit   # user WANTS_GOAL ↔ product main_benefits / review-derived effect signal
```
scoring_weights.yaml에 추가. category_affinity를 0.07→0.05로 축소하여 합계 유지.

### F8. predicate_contracts.csv 추가
```
configs/predicate_contracts.csv
컬럼: predicate, allowed_subject_types, allowed_object_types, object_ref_kind,
      polarity_allowed, inverse_predicate, qualifier_allowed, projectable_to_layer3
```
65 canonical predicate 자체의 계약. canonical_fact validation, relation_canonicalizer sanity check, projection registry completeness test에 사용.

### F9. signal merge policy 명시
```
dedup key: (review_id, target_product_id, edge_type, dst_id, polarity, registry_version)
merge 규칙:
  - weight = max(weight)
  - confidence = max(confidence)
  - source_modalities = union
  - signal_evidence에 top-k fact 연결
```
같은 review에서 BEE/REL 동시에 같은 signal 발생 시 merge.

### F10. catalog_validation_signal scoring 제외 명시
```
catalog_validation_signal:
  - candidate generation: 미사용
  - scoring: 미사용
  - explanation: QA/debug에만 사용
  - product master update: 절대 금지
```
projection_registry에서 `output_signal_family=CATALOG_VALIDATION` → scoring 대상에서 명시적 제외.

### F11. serving profile 기본 구현 = mart table
```
serving_product_profile = table-based mart (기본)
serving_user_profile = table-based mart (기본)
materialized view = 로컬 검증용 보조 옵션만 허용
```
incremental update, tombstone 처리, 운영 추적성 → mart table이 더 적합.

### F12. reviewer_proxy identity_stability 컬럼
```sql
-- review_raw 또는 별도 테이블
reviewer_proxy_id text,
identity_stability text not null,  -- STABLE|REVIEW_LOCAL
```
반복 리뷰어 behavior 분석 가능 여부 구분.

### F13. event_time UTC 정규화
```
규칙:
- 내부 저장: UTC
- source timezone 있으면 같이 저장
- timezone 불명: source locale default 적용 후 UTC 변환
- raw original timestamp string 보존

추천 컬럼:
  event_time_utc timestamptz,
  event_time_raw_text text,
  event_tz text
```

---

## 최종 DDL 전체 목록

```
sql/
├── ddl_raw.sql           # Layer 0/1: product_master, user_master, purchase_event_raw,
│                         #   review_raw(+event_time), ner_raw, bee_raw, rel_raw, review_catalog_link
├── ddl_concept.sql       # Common: concept_registry, concept_alias, entity_concept_link
├── ddl_canonical.sql     # Layer 2: canonical_entity, canonical_fact, fact_provenance, fact_qualifier
├── ddl_signal.sql        # Layer 2.5: wrapped_signal, signal_evidence
├── ddl_mart.sql          # Layer 3: agg_product_signal(windowed), agg_user_preference,
│                         #   serving_product_profile, serving_user_profile
├── ddl_quarantine.sql    # QA: quarantine_product_match, quarantine_placeholder,
│                         #   quarantine_unknown_keyword, quarantine_projection_miss, quarantine_untyped_entity
├── indexes.sql           # 복합 인덱스
└── views_serving.sql     # 서빙 뷰 정의
```

---

## 프로젝트 구조 (최종)

```
GraphRapping/
├── CLAUDE.md
├── PLAN/                              # 설계 문서
├── src/
│   ├── common/
│   │   ├── ids.py                     # ★ P0-3: deterministic IRI/ID, 전역 패턴
│   │   ├── text_normalize.py
│   │   ├── enums.py
│   │   └── config_loader.py
│   ├── ingest/
│   │   ├── review_ingest.py           # ★ P0-7: event_time + event_time_source
│   │   ├── user_ingest.py
│   │   ├── product_ingest.py          # Sprint 1
│   │   └── purchase_ingest.py         # ★ v4: Sprint 2 (brand conf weight, repurchase)
│   ├── link/
│   │   ├── product_matcher.py         # ★ P0-4: match_status, P1-5: threshold
│   │   ├── alias_resolver.py
│   │   └── placeholder_resolver.py
│   ├── normalize/
│   │   ├── ner_normalizer.py
│   │   ├── date_splitter.py           # 4분류
│   │   ├── bee_normalizer.py          # polarity/negation/intensity
│   │   ├── keyword_normalizer.py
│   │   ├── relation_canonicalizer.py  # idempotent
│   │   └── tool_concern_segment_deriver.py
│   ├── canonical/
│   │   └── canonical_fact_builder.py  # ★ v4: Layer 2 fact 생성 전용 모듈
│   ├── wrap/
│   │   ├── projection_registry.py     # ★ P0-5: qualifier 지원
│   │   ├── signal_emitter.py          # ★ P0-2: wrapped_signal 스키마 준수
│   │   └── relation_projection.py
│   ├── user/
│   │   ├── adapters/
│   │   │   └── personal_agent_adapter.py
│   │   └── canonicalize_user_facts.py
│   ├── mart/
│   │   ├── aggregate_product_signals.py   # windowed 30d/90d/all
│   │   ├── aggregate_user_preferences.py
│   │   └── build_serving_views.py         # ★ P1-3: materialized view / mart table
│   ├── qa/
│   │   ├── quarantine_handler.py
│   │   ├── dictionary_growth.py       # ★ P1-4: semi-auto approval flow
│   │   └── evidence_sampler.py
│   ├── graph/
│   │   ├── age_materializer.py
│   │   ├── neo4j_materializer.py
│   │   └── graph_query.py
│   ├── rec/
│   │   ├── candidate_generator.py     # ★ P1-2: recommendation_mode
│   │   ├── scorer.py                  # ★ P1-1: residual bee_attr, shrinkage
│   │   ├── reranker.py
│   │   ├── explainer.py               # ★ P1-7: signal_evidence 기반
│   │   ├── hook_generator.py
│   │   └── next_question.py
│   └── jobs/
│       ├── run_daily_pipeline.py
│       └── run_incremental_pipeline.py  # ★ P1-6: tombstone/late-arrival
├── sql/                               # 위 DDL 목록 참조
├── configs/
│   ├── projection_registry.csv        # 14-column 엄격 registry
│   ├── predicate_contracts.csv        # ★ v4: 65 predicates 계약
│   ├── scoring_weights.yaml           # ★ v4: goal_fit 추가
│   ├── bee_attr_dict.yaml
│   ├── keyword_surface_map.yaml
│   ├── relation_canonical_map.json
│   ├── date_context_dict.yaml
│   ├── tool_dict.yaml
│   ├── concern_dict.yaml
│   └── segment_dict.yaml
├── tests/
│   ├── test_product_matcher.py        # P1-5: threshold별 테스트
│   ├── test_placeholder_resolver.py
│   ├── test_date_splitter.py          # 4분류
│   ├── test_bee_normalizer.py         # negation/intensity
│   ├── test_projection_registry.py    # completeness + determinism
│   ├── test_signal_emitter.py
│   ├── test_idempotency.py
│   ├── test_provenance_fidelity.py    # signal→fact→raw 역추적
│   ├── test_truth_override_protection.py
│   ├── test_reviewer_isolation.py
│   ├── test_predicate_contracts.py     # ★ v4: 65 predicates 계약 검증
│   ├── test_signal_merge_policy.py    # ★ v4: multi-modality merge
│   ├── test_window_backfill.py        # ★ v4: late-arrival + tombstone
│   ├── test_concept_link_integrity.py # ★ v4: shared concept join + proxy isolation
│   ├── test_end_to_end.py             # wrapped_signal→agg→serving→rec→explain 전체 역추적
│   └── test_recommendation.py
├── ERR_HIST/
├── DECISIONS/
└── pyproject.toml
```

---

## 구현 순서 (4 Sprint)

### Sprint 1: Foundation
**목표**: DDL 전체, concept registry, product master, raw ingest, 매칭, 기초 정규화

| # | 작업 | 핵심 파일 |
|---|------|----------|
| 1.1 | 프로젝트 초기화 | `pyproject.toml`, 디렉토리 |
| 1.2 | ★ 전체 DDL (raw, concept, canonical, signal, mart, quarantine, indexes) | `sql/` 전체 |
| 1.3 | Deterministic ID 모듈 (P0-3 패턴) | `src/common/ids.py` |
| 1.4 | Enum/Config | `src/common/enums.py`, `config_loader.py` |
| 1.5 | Product ingest + concept registry 초기 적재 | `src/ingest/product_ingest.py` |
| 1.6 | Review ingest (event_time 포함, P0-7) | `src/ingest/review_ingest.py` |
| 1.7 | Product matcher (P0-4: match_status, P1-5: threshold) | `src/link/product_matcher.py` |
| 1.8 | Alias resolver | `src/link/alias_resolver.py` |
| 1.9 | Placeholder resolver | `src/link/placeholder_resolver.py` |
| 1.10 | DATE splitter (4분류) | `src/normalize/date_splitter.py` |
| 1.11 | Projection Registry (14-column, P0-5: qualifier 지원) | `configs/projection_registry.csv`, `src/wrap/projection_registry.py` |
| 1.12 | Quarantine handler | `src/qa/quarantine_handler.py` |
| 1.13 | ★ Predicate contracts 정의 | `configs/predicate_contracts.csv` |
| 1.14 | Config 파일 초기 작성 | `configs/` 전체 |

**검증**:
- review_id/reviewer_proxy_id 항상 생성, target_product_id는 성공시만/실패시 quarantine
- product matching: exact/norm/alias/fuzzy/quarantine 각 1건+
- placeholder union-find 검증
- DATE 4분류 테스트
- concept_registry에 Brand/Category/Ingredient 적재 확인

### Sprint 2: Canonicalization + Signal + User Graph
**목표**: BEE/REL 정규화, canonical fact+provenance, wrapped signal, user canonical facts, 서빙 프로필

| # | 작업 | 핵심 파일 |
|---|------|----------|
| 2.1 | BEE normalizer (polarity/negation/intensity) | `src/normalize/bee_normalizer.py` |
| 2.2 | Keyword normalizer | `src/normalize/keyword_normalizer.py` |
| 2.3 | Relation canonicalizer (idempotent) | `src/normalize/relation_canonicalizer.py` |
| 2.4 | Tool/Concern/Segment deriver | `src/normalize/tool_concern_segment_deriver.py` |
| 2.5 | ★ Canonical fact builder (Layer 2 생성 전용) | `src/canonical/canonical_fact_builder.py` |
| 2.6 | Signal emitter (P0-2 스키마, registry 기반, merge policy) | `src/wrap/signal_emitter.py`, `relation_projection.py` |
| 2.7 | Personal-agent adapter | `src/user/adapters/personal_agent_adapter.py` |
| 2.8 | User canonicalize (canonical_user_fact) | `src/user/canonicalize_user_facts.py` |
| 2.9 | User ingest | `src/ingest/user_ingest.py` |
| 2.10 | ★ Purchase ingest | `src/ingest/purchase_ingest.py` |
| 2.11 | Product aggregate (windowed 30d/90d/all) | `src/mart/aggregate_product_signals.py` |
| 2.12 | User aggregate | `src/mart/aggregate_user_preferences.py` |
| 2.13 | Evidence sampler (top-k) + signal_evidence | `src/qa/evidence_sampler.py` |
| 2.14 | Serving profiles (★ table-based mart) | `src/mart/build_serving_views.py` |

**검증**:
- BEE → BEE_ATTR + KEYWORD + polarity/negation/intensity
- 65 relations Layer 2 손실 없음
- fact_provenance 연결됨
- projection miss → quarantine
- user → canonical_user_fact → agg
- idempotency: 2번 처리 중복 없음
- catalog_validation_signal이 master overwrite 안 함 (P0-6)
- serving_product_profile에 truth + signals 통합됨

### Sprint 3: Recommendation + Explanation
**목표**: SQL-first 추천, 스코어링, 설명, hook/질문

| # | 작업 | 핵심 파일 |
|---|------|----------|
| 3.1 | Candidate generator (mode 지원) | `src/rec/candidate_generator.py` |
| 3.2 | Scorer (residual bee_attr, shrinkage, config화) | `src/rec/scorer.py`, `configs/scoring_weights.yaml` |
| 3.3 | Explainer (signal_evidence 기반, score-faithful) | `src/rec/explainer.py` |
| 3.4 | Hook generator | `src/rec/hook_generator.py` |
| 3.5 | Next-best-question | `src/rec/next_question.py` |
| 3.6 | Daily pipeline | `src/jobs/run_daily_pipeline.py` |
| 3.7 | 전체 테스트 | `tests/` |

**검증**:
- deterministic top-k 추천
- hard exclusion zero-out
- explanation = score contributor top-n (score-faithful)
- provenance 전체 역추적: signal→fact→raw
- reviewer isolation

### Sprint 4: Polish + Optional
**목표**: 리랭킹, dictionary growth, graph projection(필요시)

| # | 작업 |
|---|------|
| 4.1 | Reranker (calibration, contribution logging) |
| 4.2 | Dictionary growth (semi-auto approval) |
| 4.3 | Analyst queries |
| 4.4 | Graph projection interface → AGE (필요시) → Neo4j (필요시) |
| 4.5 | pgvector evidence retrieval (optional) |
| 4.6 | Incremental pipeline (tombstone/late-arrival) |

Graph 전환 조건: recursive join 3단계+ 반복 → AGE, explanation path P95 초과 → Neo4j

---

## Acceptance Criteria (최종)

1. review_id, reviewer_proxy_id 항상 생성
2. target_product_id: resolve 성공 시 생성, 실패 시 quarantine에 match_status='QUARANTINE'
3. placeholder (Review Target, Reviewer, 대명사) resolve됨
4. BEE phrase → BEE_ATTR + KEYWORD + polarity/negation/intensity
5. relation 65개 Layer 2 predicate 손실 없음
6. canonical_fact에 fact_provenance + fact_qualifier 연결
7. projection registry completeness: 실제 observed combo 기준 통과
8. projection miss → quarantine_projection_miss
9. wrapped_signal 스키마 준수 + registry_version 기록
10. agg_product_signal: windowed (30d/90d/all) + event_time 기준
11. serving_product_profile: master truth + signal 통합
12. user → canonical_user_fact → agg_user_preference
13. concept_registry를 통해 user/product shared concept_id 연결
14. catalog_validation_signal이 product master overwrite 안 함
15. event_time_source 누락 없이 기록
16. idempotency: 같은 리뷰 2번 처리 중복 없음
17. reviewer proxy ≠ real user isolation
18. 최소 1개 추천 + explanation path 동작
19. **전체 역추적**: wrapped_signal → agg → serving → recommendation → explanation → fact → raw 1건 성공
20. ★ predicate_contracts.csv: 65 predicates 전부 계약 보유
21. ★ signal merge: 같은 review에서 multi-modality 동일 signal → 정상 merge
22. ★ catalog_validation_signal: scoring에서 제외 확인
23. ★ USED_WITH_PRODUCT_SIGNAL: co-use product 정상 생성

---

## 참조 파일 경로

| 문서 | 경로 |
|------|------|
| Handoff 최종본 | `PLAN/01_final_project_handoff_v2_ko.md` |
| 프로젝트 개요 | `PLAN/02_project_overview_goals_v2_ko.md` |
| 구현 지시서 | `PLAN/03_claude_code_implementation_spec_v2_ko.md` |
| Layer 매핑 부록 | `PLAN/04_layer_mapping_appendix_v2_ko.md` |
| Relation CSV | `PLAN/05_relation_wrapping_mapping_v2.csv` |
| GPT 피드백 1차 | `06_feedback_on_claude_plan_ko.md` |
| GPT 피드백 2차 | `07_feedback_on_v2_plan_ko.md` |
| Canonical mapping | `/Users/amore/Jupyter_workplace/Relation/source_data/relation_canonical_mapping_bidir_v1.json` |
| Entity types config | `/Users/amore/Jupyter_workplace/Relation/project_3_neo4j/config/entity_types.json` |
| Relation types config | `/Users/amore/Jupyter_workplace/Relation/project_3_neo4j/config/relation_types.json` |
| Neo4j pipeline | `/Users/amore/Jupyter_workplace/Relation/project_3_neo4j/` |
| Personal-agent SignalBuilder | `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/signal_builder.py` |
| Personal-agent FieldRouter | `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/field_router.py` |
| Personal-agent constants | `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/constants.py` |
| Personal-agent data_store | `/Users/amore/workplace/agent-aibc/persnal-agent/src/personalization/data_store.py` |
