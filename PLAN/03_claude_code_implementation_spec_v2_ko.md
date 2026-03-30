# Claude Code 구현 지시서 / 코드 구현 상세 (최종 통합본)

## 0. 구현 목표

이 구현의 목표는 raw review extraction 결과(`NER + BEE + REL`)와 상품/유저 정본 데이터를 받아 다음을 만드는 것이다.

1. Layer 1 raw evidence 저장
2. Layer 2 canonical fact 생성
3. Layer 3 aggregate serving graph 생성
4. user-product join 기반 추천 / 설명 / hook / 질문 생성

---

## 1. 레포 구조 권장안

```text
project/
  src/
    common/
      ids.py
      text_normalize.py
      enums.py
      config_loader.py
    ingest/
      review_ingest.py
      user_ingest.py
      product_ingest.py
    link/
      product_matcher.py
      alias_resolver.py
      placeholder_resolver.py
    normalize/
      ner_normalizer.py
      date_splitter.py
      bee_normalizer.py
      keyword_normalizer.py
      relation_canonicalizer.py
      tool_concern_segment_deriver.py
    wrap/
      signal_emitter.py
      relation_projection.py
    mart/
      aggregate_product_signals.py
      aggregate_user_preferences.py
      build_serving_views.py
    graph/
      age_materializer.py
      neo4j_materializer.py
      graph_query.py
    rec/
      candidate_generator.py
      scorer.py
      reranker.py
      explainer.py
      hook_generator.py
      next_question.py
    jobs/
      run_daily_pipeline.py
      run_incremental_pipeline.py
  sql/
    ddl_raw.sql
    ddl_canonical.sql
    ddl_mart.sql
    views_serving.sql
  configs/
    bee_attr_dict.yaml
    keyword_surface_map.yaml
    relation_projection_map.csv
    tool_dict.yaml
    concern_dict.yaml
    segment_dict.yaml
    date_context_dict.yaml
  tests/
    test_product_matcher.py
    test_date_splitter.py
    test_bee_normalizer.py
    test_relation_projection.py
    test_signal_emitter.py
```

---

## 2. 핵심 테이블 DDL

## 2-1. Layer 1 raw

```sql
create table if not exists review_raw (
  review_id text primary key,
  source_site text,
  brand_name_raw text,
  product_name_raw text,
  review_text text not null,
  raw_payload jsonb not null,
  created_at timestamptz default now()
);

create table if not exists review_catalog_link (
  review_id text primary key references review_raw(review_id),
  target_product_id text,
  brand_id text,
  category_id text,
  match_method text,
  match_score numeric,
  resolved_at timestamptz default now()
);

create table if not exists ner_raw (
  ner_row_id bigserial primary key,
  review_id text not null references review_raw(review_id),
  mention_text text not null,
  entity_group text not null,
  start_offset int,
  end_offset int,
  raw_sentiment text,
  is_placeholder boolean default false,
  placeholder_type text,
  created_at timestamptz default now()
);

create table if not exists bee_raw (
  bee_row_id bigserial primary key,
  review_id text not null references review_raw(review_id),
  phrase_text text not null,
  bee_attr_raw text not null,
  raw_sentiment text,
  start_offset int,
  end_offset int,
  created_at timestamptz default now()
);

create table if not exists rel_raw (
  rel_row_id bigserial primary key,
  review_id text not null references review_raw(review_id),
  subj_text text not null,
  subj_group text not null,
  obj_text text not null,
  obj_group text not null,
  relation_raw text not null,
  relation_canonical text,
  source_type text,
  created_at timestamptz default now()
);
```

## 2-2. Layer 2 canonical fact

```sql
create table if not exists canonical_entity (
  entity_iri text primary key,
  entity_type text not null,
  normalized_value text,
  display_name text,
  source_system text,
  attrs jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists canonical_fact (
  fact_id text primary key,
  review_id text,
  subj_iri text not null,
  predicate text not null,
  obj_iri text,
  obj_value text,
  polarity text,
  source_modality text,
  source_ref jsonb,
  created_at timestamptz default now()
);

create index if not exists idx_canonical_fact_subj on canonical_fact(subj_iri);
create index if not exists idx_canonical_fact_pred on canonical_fact(predicate);
create index if not exists idx_canonical_fact_obj on canonical_fact(obj_iri);
```

## 2-3. Wrapped signal / mart

```sql
create table if not exists wrapped_signal (
  signal_id text primary key,
  review_id text not null,
  target_product_id text not null,
  source_modality text not null,
  signal_family text not null,
  canonical_edge_type text not null,
  dst_node_type text not null,
  dst_node_id text not null,
  polarity text,
  weight numeric not null,
  evidence jsonb,
  created_at timestamptz default now()
);

create table if not exists agg_product_signal (
  target_product_id text not null,
  canonical_edge_type text not null,
  dst_node_type text not null,
  dst_node_id text not null,
  review_cnt int not null,
  pos_cnt int not null,
  neg_cnt int not null,
  neu_cnt int not null,
  score numeric not null,
  recent_score numeric,
  window_start date,
  window_end date,
  evidence_sample jsonb,
  primary key (target_product_id, canonical_edge_type, dst_node_id)
);

create table if not exists agg_user_preference (
  user_id text not null,
  preference_edge_type text not null,
  dst_node_type text not null,
  dst_node_id text not null,
  weight numeric not null,
  source_mix jsonb,
  updated_at timestamptz default now(),
  primary key (user_id, preference_edge_type, dst_node_id)
);
```

---

## 3. 생성해야 하는 ID 규칙

### review_id
```python
md5(f"{source_site}|{brand_name_raw}|{product_name_raw}|{review_text}")
```

### reviewer_proxy_id
```python
md5(f"{review_id}|reviewer_proxy")
```

### fact_id
```python
md5(f"{review_id}|{subj_iri}|{predicate}|{obj_iri or obj_value}")
```

### signal_id
```python
md5(f"{review_id}|{target_product_id}|{canonical_edge_type}|{dst_node_id}|{polarity}")
```

---

## 4. Python 모듈별 책임

## 4-1. `src/common/ids.py`
필수 함수:
- `make_review_id(record)`
- `make_reviewer_proxy_id(review_id)`
- `make_fact_id(...)`
- `make_signal_id(...)`

## 4-2. `src/link/product_matcher.py`
입력:
- `brand_name_raw`
- `product_name_raw`
- optional hints: volume/color/category/site

출력:
```python
{
  "target_product_id": "prd_...",
  "brand_id": "brd_...",
  "category_id": "cat_...",
  "match_method": "exact|norm_exact|alias|fuzzy|manual",
  "match_score": 0.97
}
```

구현 우선순위:
1. exact
2. normalized exact
3. alias match
4. fuzzy
5. manual queue

## 4-3. `src/link/placeholder_resolver.py`
역할:
- `Review Target` -> `target_product_id`
- `Reviewer` / `I` / `my` -> `reviewer_proxy_id`
- PRD 대명사 `it`, `this` -> target product로 우선 resolve
- `same_entity` 기반 union-find merge

출력:
- resolved mention map
- alias group map

## 4-4. `src/normalize/date_splitter.py`
입력: DATE mention text

출력 예:
```python
{"kind": "TemporalContext", "value": "아침", "context_type": "day_part"}
{"kind": "TemporalContext", "value": "세안후", "context_type": "routine_step"}
{"kind": "Frequency", "value": "하루1회"}
{"kind": "Duration", "value": "2주"}
```

초기 룰 기반으로 구현하고, 이후 사전 확장.

## 4-5. `src/normalize/bee_normalizer.py`
입력: `bee_raw` row
출력: `[(bee_attr_id, keyword_id, polarity, evidence_text), ...]`

처리 규칙:
- BEE_ATTR는 raw taxonomy를 유지
- KEYWORD는 dict / surface map으로 정규화
- one phrase -> many keywords 허용
- sentiment raw -> POS/NEG/NEU/MIXED 처리
- mixed는 분리 가능하면 분리, 아니면 evidence queue

## 4-6. `src/normalize/relation_canonicalizer.py`
입력: raw relation row
출력:
- Layer 2 predicate (65 canonical)
- direction fix 여부
- helper relation 여부

중요:
- Layer 2 predicate는 **raw canonical 65개 그대로** 유지
- Layer 3 projection은 별도 module에서 처리

## 4-7. `src/wrap/relation_projection.py`
입력: canonical fact row
출력: wrapped signal row(s)

예:
- `USED_ON(Product, 아침)` -> `USED_IN_CONTEXT_SIGNAL`
- `HAS_ATTRIBUTE(Product, BEEAttr:밀착력)` -> `HAS_BEE_ATTR_SIGNAL`
- `HAS_KEYWORD(BEEAttr:밀착력, Keyword:밀착좋음)` + product linkage -> `HAS_BEE_KEYWORD_SIGNAL`
- `CAUSES(Product, 건조함)` -> `MAY_CAUSE_CONCERN_SIGNAL`
- `ADDRESSES(Product, 건조함)` -> `ADDRESSES_CONCERN_SIGNAL`

## 4-8. `src/wrap/signal_emitter.py`
역할:
- review 단위로 모든 wrapped signal 생성
- dedup
- evidence payload attach
- idempotent upsert

---

## 5. BEE 처리 상세

## 5-1. dictionary 구조

### bee_attr_dict.yaml
```yaml
Adhesion:
  attr_id: bee_attr_adhesion
  label_ko: 밀착력
Spreadability:
  attr_id: bee_attr_spreadability
  label_ko: 발림성
Moisturizing Power:
  attr_id: bee_attr_moisturizing_power
  label_ko: 보습력
```

### keyword_surface_map.yaml
```yaml
착붙:
  - keyword_id: kw_adhesion_good
    label_ko: 밀착좋음
안 떠요:
  - keyword_id: kw_low_lifting
    label_ko: 들뜸없음
얇게 발려요:
  - keyword_id: kw_thin_spread
    label_ko: 얇게발림
```

## 5-2. BEE -> Layer 2 facts
예시 phrase: `착붙하고 오후에도 안 떠요`

생성 facts:
- `Product HAS_ATTRIBUTE BEEAttr:밀착력`
- `BEEAttr:밀착력 HAS_KEYWORD Keyword:밀착좋음`
- `BEEAttr:밀착력 HAS_KEYWORD Keyword:들뜸없음`
- `Product USED_ON TemporalContext:오후` (relation/ner에서 생성)

## 5-3. BEE -> Layer 3 signals
- `Product HAS_BEE_ATTR_SIGNAL BEEAttr:밀착력`
- `Product HAS_BEE_KEYWORD_SIGNAL Keyword:밀착좋음`
- `Product HAS_BEE_KEYWORD_SIGNAL Keyword:들뜸없음`

---

## 6. relation 65 -> projection rules

**절대 규칙**: Layer 2에서 65 canonical을 잃지 않는다.

### projection families
- `BEE_ATTR_SIGNAL`
- `BEE_KEYWORD_SIGNAL`
- `CONTEXT_SIGNAL`
- `TOOL_SIGNAL`
- `COUSE_PRODUCT_SIGNAL`
- `CONCERN_POS_SIGNAL`
- `CONCERN_NEG_SIGNAL`
- `COMPARISON_SIGNAL`
- `SEGMENT_SIGNAL`
- `CATALOG_VALIDATION_SIGNAL`

### 예시 규칙
```python
if fact.predicate == "USED_ON":
    emit("USED_IN_CONTEXT_SIGNAL", "TemporalContext", obj)
elif fact.predicate == "TIME_OF_USE":
    emit("USED_IN_CONTEXT_SIGNAL", "TemporalContext", obj)
elif fact.predicate == "USED_WITH" and dst_type == "Tool":
    emit("USED_WITH_TOOL_SIGNAL", "Tool", obj)
elif fact.predicate == "USED_WITH" and dst_type == "Product":
    emit("USED_WITH_PRODUCT_SIGNAL", "Product", obj)
elif fact.predicate in {"ADDRESSES", "TREATS", "BENEFITS"}:
    emit("ADDRESSES_CONCERN_SIGNAL", "Concern", obj)
elif fact.predicate in {"CAUSES", "CAUSED_BY"}:
    emit("MAY_CAUSE_CONCERN_SIGNAL", "Concern", obj)
elif fact.predicate == "COMPARISON_WITH":
    emit("COMPARED_WITH_SIGNAL", "Product", obj)
```

---

## 7. user graph 구축 상세

## 7-1. raw tables
```sql
create table if not exists user_profile_raw (
  user_id text primary key,
  age int,
  gender text,
  skin_type text,
  skin_tone text,
  raw_payload jsonb
);

create table if not exists purchase_event_raw (
  purchase_event_id text primary key,
  user_id text not null,
  product_id text not null,
  purchased_at timestamptz,
  price numeric,
  quantity int,
  raw_payload jsonb
);

create table if not exists user_summary_raw (
  user_id text primary key,
  purchase_summary jsonb,
  chat_summary jsonb,
  updated_at timestamptz
);
```

## 7-2. canonical user facts
예시:
- `HAS_SKIN_TYPE(User, 건성)`
- `HAS_SKIN_TONE(User, 21호)`
- `PREFERS_BRAND(User, 헤라)`
- `PREFERS_CATEGORY(User, 쿠션)`
- `PREFERS_INGREDIENT(User, 세라마이드)`
- `AVOIDS_INGREDIENT(User, 에탄올)`
- `HAS_CONCERN(User, 건조함)`
- `WANTS_GOAL(User, 진정)`
- `PREFERS_CONTEXT(User, 아침)`
- `PREFERS_BEE_ATTR(User, 발림성)`
- `PREFERS_KEYWORD(User, 얇게발림)`

## 7-3. user preference derivation rules
- 구매 반복 제품 -> `REPURCHASES_PRODUCT_OR_FAMILY`
- 같은 카테고리의 반복 구매 -> `PREFERS_CATEGORY`
- 같은 브랜드 반복 구매 -> `PREFERS_BRAND`
- 채팅에서 `선호 성분` -> `PREFERS_INGREDIENT`
- 채팅에서 `기피/알러지 성분` -> `AVOIDS_INGREDIENT`
- 채팅에서 `카테고리별 고민` -> `HAS_CONCERN`
- 채팅에서 `케어 목표` -> `WANTS_GOAL`
- 채팅에서 `주 사용 시간/루틴` -> `PREFERS_CONTEXT`
- 제품 구매/클릭 이력에서 공통 BEE keyword가 드러나면 `PREFERS_KEYWORD`로 승격

---

## 8. aggregate mart 생성

## 8-1. product-side aggregate
예시 SQL 개념:
```sql
insert into agg_product_signal (
  target_product_id, canonical_edge_type, dst_node_type, dst_node_id,
  review_cnt, pos_cnt, neg_cnt, neu_cnt, score, recent_score, window_start, window_end, evidence_sample
)
select
  target_product_id,
  canonical_edge_type,
  dst_node_type,
  dst_node_id,
  count(distinct review_id) as review_cnt,
  count(*) filter (where polarity = 'POS') as pos_cnt,
  count(*) filter (where polarity = 'NEG') as neg_cnt,
  count(*) filter (where polarity = 'NEU') as neu_cnt,
  (count(*) filter (where polarity = 'POS') - count(*) filter (where polarity = 'NEG'))::numeric / nullif(count(*),0) as score,
  null as recent_score,
  min(created_at)::date,
  max(created_at)::date,
  jsonb_agg(evidence)->0
from wrapped_signal
group by 1,2,3,4;
```

## 8-2. user-side aggregate
- purchase summary refresh
- seasonal summary refresh
- keyword preference derivation refresh

---

## 9. graph materialization

## 9-1. AGE 선택 시
- canonical entity -> AGE vertex
- agg_product_signal / agg_user_preference -> AGE edges
- product/user common concept join을 openCypher로 질의

## 9-2. Neo4j 선택 시
MERGE key 정책:
- Product: `product_id`
- Brand: `brand_id`
- Category: `category_id`
- Ingredient: `ingredient_id`
- BEEAttr: `attr_id`
- Keyword: `keyword_id`
- TemporalContext: `context_id`
- Concern: `concern_id`
- User: `user_id`

예시 Cypher:
```cypher
UNWIND $product_rows AS row
MERGE (p:Product {product_id: row.product_id})
MERGE (a:BEEAttr {attr_id: row.dst_node_id})
MERGE (p)-[r:HAS_BEE_ATTR_SIGNAL]->(a)
SET r.review_cnt = row.review_cnt,
    r.pos_cnt = row.pos_cnt,
    r.neg_cnt = row.neg_cnt,
    r.score = row.score,
    r.window_start = row.window_start,
    r.window_end = row.window_end
```

---

## 10. 추천 엔진 스켈레톤

## 10-1. candidate generation
입력:
- user_id
- optional category / price band / inventory constraints

후보 조건:
- preferred category match
- no avoid ingredient conflict
- concern fit
- preferred context fit
- preferred BEE_ATTR / KEYWORD fit

## 10-2. scoring
```python
score = (
    0.20 * brand_match
  + 0.15 * category_match
  + 0.15 * ingredient_fit
  + 0.15 * concern_fit
  + 0.15 * context_fit
  + 0.10 * bee_attr_fit
  + 0.10 * keyword_fit
) - conflict_penalty
```

## 10-3. explanation
path examples:
- User `PREFERS_KEYWORD(얇게발림)` <- Product `HAS_BEE_KEYWORD_SIGNAL`
- User `HAS_CONCERN(건조함)` <- Product `ADDRESSES_CONCERN_SIGNAL`
- User `PREFERS_CONTEXT(세안후)` <- Product `USED_IN_CONTEXT_SIGNAL`

## 10-4. hook generation
- discovery angle: "요즘 찾는 사용감/발림성에 가까운 제품"
- consideration angle: "건조한 날에도 비교적 부담이 적은 편"
- conversion angle: "평소 쓰는 루틴과 잘 맞는 편"

## 10-5. next-best-question
불확실성이 큰 축 1개만 질문:
- 커버력 vs 촉촉함
- 향 민감 여부
- 퍼프/브러시 사용 여부
- 아침용/지속력 우선 여부

---

## 11. 테스트 전략

### 단위 테스트
- placeholder resolution
- date splitter
- bee keyword mapping
- relation projection
- signal dedup

### 통합 테스트
- raw review 1건 -> Layer 2 facts -> Layer 3 signals
- user summary 1건 -> user serving edges
- user/product join query

### 회귀 테스트
- dictionary 변경 전후 signal drift
- relation mapping 변경 전후 aggregate diff

---

## 12. 우선 구현 순서

### Sprint 1
- raw DDL
- ingest loader
- product matcher
- placeholder resolver
- date splitter

### Sprint 2
- BEE normalizer
- relation canonicalizer
- wrapped signal emitter
- aggregate mart SQL

### Sprint 3
- AGE or Neo4j materializer
- user graph derivation
- candidate generator
- explanation skeleton

### Sprint 4
- reranker 개선
- hook / next question
- analyst queries
- optional evidence retrieval with pgvector

---

## 13. acceptance criteria

1. raw review 1건을 넣으면 `review_id`, `reviewer_proxy_id`, `target_product_id`가 생성된다.
2. `Review Target`, `Reviewer`, 대명사가 resolve된다.
3. BEE phrase가 BEE_ATTR + KEYWORD로 정규화된다.
4. relation 65개가 Layer 2 predicate로 손실 없이 저장된다.
5. Layer 3 aggregate row가 product 기준으로 생성된다.
6. user summary가 canonical user facts로 변환된다.
7. 최소 1개의 추천 query와 1개의 explanation path query가 동작한다.
