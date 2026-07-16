# GraphRapping DB Consumer Contract

**작성일**: 2026-06-08 (Wave 4 Task 7)
**대상**: GraphRapping Postgres 를 truth source 로 사용하는 downstream consumer
**전제**: Wave 4 완료 — `run_full_load_to_db` 또는 일일 incremental pipeline 이 5-layer 영속화를 마친 상태

이 문서는 consumer 가 GraphRapping DB 를 안전하게 읽고 자기 로컬 snapshot 을
구축하기 위한 최소 contract 를 정의한다. AmoreSimulation 의 pool contract
예시를 일반화한 표준이다.

---

## 1. 연결 contract

### 1.1 Asyncpg-compatible pool

Consumer 는 asyncpg-like 인터페이스로 풀을 생성한다. SQLAlchemy 의
`postgresql+asyncpg://` URL 도 허용되며 GraphRapping `src/db/connection.py`
의 `normalize_dsn` 가 `postgresql://` 로 정규화한다.

```python
import asyncpg

pool = await asyncpg.create_pool(
    "postgresql://reader:<pw>@db.example.com:5432/graphrapping",
    min_size=1,
    max_size=5,
    command_timeout=60,
)

async with pool.acquire() as conn:
    rows = await conn.fetch(query, *args)
```

### 1.2 Read-only role 권장 설정

GraphRapping truth tables 는 consumer 에게 read-only 이어야 한다. Simulation
또는 derived artifact 는 절대 GraphRapping 스키마에 write back 하지 않는다.

```sql
CREATE ROLE graphrapping_reader LOGIN PASSWORD '<set-password>';
GRANT CONNECT ON DATABASE graphrapping TO graphrapping_reader;
GRANT USAGE ON SCHEMA public TO graphrapping_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO graphrapping_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT ON TABLES TO graphrapping_reader;
```

### 1.3 Schema version 가드

```sql
SELECT version FROM schema_migrations ORDER BY version;
```

Wave 4 시점 최소 요구 마이그레이션:
- `ddl_ops.sql`
- `ddl_raw.sql`
- `ddl_concept.sql`
- `ddl_canonical.sql`
- `ddl_signal.sql`
- `ddl_mart.sql`
- `ddl_quarantine.sql`
- `indexes.sql`

Consumer 는 위 8개 버전이 누락되면 fail-fast 한다.

---

## 2. 필수 테이블과 필터

| 테이블 | 사용처 | 필수 필터 |
|---|---|---|
| `product_master` | 활성 상품 카탈로그 | `is_active = true` |
| `user_master` | 활성 사용자 프로필 | `is_active = true` |
| `purchase_event_raw` | 구매 이력 | (필요 시 기간 필터) |
| `agg_product_signal` | 상품 단위 집계 신호 | `is_active = true` AND `is_promoted = true` AND `window_type = <selected>` |
| `agg_user_preference` | 사용자 선호 집계 | `is_active = true` (선택 추가) `confidence >= <threshold>` |
| `serving_product_profile` | 추천용 product payload (promoted-only 신호 집계 적용 완료) | — |
| `serving_user_profile` | 추천용 user payload (active preference + purchase 기반; promotion gate 없음) | — |
| `signal_evidence` | 신호 → fact provenance | `signal_id` join |
| `concept_registry` | 정본 concept (brand/category/ingredient/...) | `concept_id` join |

### 2.1 Window 선택 가이드 (`agg_product_signal.window_type`)

| Window | 권장 사용처 |
|---|---|
| `all` | **기본값** — 안정적 offline simulation, 광범위한 product state |
| `90d` | 최근-but-stable trend surfaces |
| `30d` | freshness 가 민감한 실험 / 대시보드만 |

Consumer 는 단일 window 만 선택해 join 한다. 동시 다중 window read 는
표시용 보조 데이터로 한정한다.

---

## 3. Serving profile column 명세 (2026-06-17 source-grounded baseline)

`serving_profile_schema.py` 가 single source of truth. 아래는 그 미러.

> **2026-06-18 local DB baseline** (Snowflake `f_prd_rv_hist` 6-month source
> stats refresh 적용 후 재조회):
> - kg_off: 2801 signals / 9255 quarantine
> - kg_on : 2529 signals / 6331 quarantine
> - **517 active products** (rs_own.product_id 문자열 그대로; 분포는 §3
>   product_id 형식 contract 참조), 50 active users, 906 reviews
> - `product_review_stats`: 516 rows
> - `serving_product_profile.source_review_count_6m`: 516 positive, 0 zero
> - `serving_product_profile.source_avg_rating_6m`: 516 non-null
> - `serving_product_profile.source_review_count_all`: 516 non-null
> - `serving_product_profile.source_avg_rating_all`: 516 non-null
> - promoted signals (kg_off, window=all): top_bee_attr_ids on 26 products,
>   top_keyword_ids on 5 products
>
> **product_id/source identity contract**: `product_id`는 AmoreSimulation
> 호환용 primary key로 유지한다. source 재조인과 외부 리뷰/요약 매칭은
> `source_channel + source_key_type + source_product_id` composite identity를
> 함께 사용해야 한다. 현재 대부분은 `product_id == source_product_id`지만,
> `35119`처럼 channel/key type이 다르면 다른 원천 상품을 가리키는 collision이
> 존재한다. Consumer는 `source_product_id` 단독 join을 clean source identity로
> 간주하지 않는다.

### 3.1 `serving_product_profile`

**Truth columns (product_master 기원)**:
`product_id, brand_id, brand_name, category_id, category_name,
country_of_origin, price, variant_family_id,
representative_product_name, main_benefit_ids, ingredient_ids,
source_truth_source, source_truth_quality, source_truth_updated_at`

**Source identity columns (serving contract)**:
`source_product_id, source_channel, source_key_type`

Consumer 는 source review stats 를 표시하거나 외부 source/review summary와
재조인할 때 이 세 필드를 같이 사용한다. `product_id`는 downstream 호환 key이고,
source identity 품질 판단은 `source_truth_quality`까지 함께 본다.

**Serving-only / forward-compatible columns** (`product_master` 스키마에는 없음):
`price_band` — nullable. 현재 빌더는 별도 구간화 로직을 수행하지 않고
`product_master.get("price_band")` 입력을 그대로 전달한다 (입력 소스 외부
제공 시 채워짐, 미제공 시 NULL).

**Concept ID columns (canonical join keys)**:
`brand_concept_ids, category_concept_ids, ingredient_concept_ids,
main_benefit_concept_ids`

**Signal columns (agg_product_signal 기원)**:
`top_bee_attr_ids, top_keyword_ids, top_context_ids, top_concern_pos_ids,
top_concern_neg_ids, top_tool_ids, top_comparison_product_ids,
top_coused_product_ids`

**Freshness & support**:
`last_signal_at, review_count_30d, review_count_90d, review_count_all,
signal_support_count_all`

**Source review stats (raw source 기원)**:
`source_review_count_6m, source_review_score_count_6m,
source_avg_rating_6m, source_review_min_date_6m, source_review_max_date_6m,
source_review_count_all, source_review_score_count_all,
source_avg_rating_all, source_review_min_date_all, source_review_max_date_all,
source_review_stats_source`

`source_review_count_*` 와 `source_review_score_count_*` 는 nullable 이다.
해당 source stats row/value 가 없으면 `NULL` 이며, `0` 은 실제 source stats
row 가 0 을 제공한 경우에만 유효하다.

**Source truth quality**:

| 값 | 의미 | Consumer 처리 |
|---|---|---|
| `SOURCE_GROUNDED` | 상품마스터/source stats에 의해 확인된 product/source identity | 정상 사용 |
| `MISSING_SOURCE_BRAND` 등 missing marker | 일부 master truth가 원천에서 비어 있음 | 누락 필드는 unknown으로 표시 |
| `SYNTHETIC_*` / mock source | 테스트 또는 합성 catalog truth | 운영 ranking/source stats 근거로 사용 금지 |
| `SOURCE_KEY_COLLISION` | 같은 compat product_id가 복수 source identity를 가리킴 | clean product로 자동 사용 금지. AmoreSimulation에서는 clean `source_product_id`/`review_channel`을 내보내지 않고, `source_key_collision:<id>` marker와 `source_product_id_collision` 진단값만 유지. source stats/review summary join 제외 또는 경고 처리 |

### 3.2 `serving_user_profile`

**Demographics**:
`user_id, age_band, gender, skin_type, skin_tone`

**Preferences (agg_user_preference 기원)**:
`preferred_brand_ids, active_category_ids, preferred_category_ids, preferred_ingredient_ids,
avoided_ingredient_ids, concern_ids, goal_ids, preferred_bee_attr_ids,
preferred_keyword_ids, preferred_context_ids`

`active_category_ids`는 구매 활동 카테고리 컨텍스트(`ACTIVE_IN_CATEGORY`)이다.
명시 선호(`PREFERS_CATEGORY`)가 아니므로 추천 explanation에서 선호
카테고리로 표시하지 않는다.

**Behavior (purchase 기원)**:
`recent_purchase_brand_ids, repurchase_brand_ids, repurchase_category_ids,
owned_product_ids, owned_family_ids, repurchased_family_ids`

### 3.3 배열 element 타입

Consumer 는 array 요소가 **string 또는 `{"id": str, ...}` dict** 둘 다일 수
있음을 가정해야 한다. 현재 마트는 필드별로 형식이 다르다.

```python
def extract_id(item):
    return item if isinstance(item, str) else item.get("id")
```

### 3.4 `review_summary_sidecar`

Review summary는 GraphRapping 최종 산출물에 포함되지만 graph evidence는
아니다. Consumer는 `product_id`로 `product_master`/`serving_product_profile`에
left join해서 사용한다.

**Join key**:
`review_summary_sidecar.product_id = product_master.product_id`

**Source identity columns**:
`source_product_id, source_channel, source_key_type,
review_source, review_channel, review_summary_category`

**Match columns**:
`match_status, long_doc_id, short_doc_id, candidate_metadata, an_date, source`

현재 loader가 사용하는 `match_status`:

| 값 | 의미 | Consumer 처리 |
|---|---|---|
| `exact_category` | `source_product_id`와 `source_channel` 기반 expected category가 모두 일치 | 정상 사용 |
| `product_id_ambiguous_skipped` | product id 후보는 있으나 expected source category가 없거나 맞지 않음 | 자동 사용 금지 |
| `not_found` | ES summary 문서 없음 | summary 없음 |

`source_unique`/`product_id_unique` manifest columns are retained only for
historical compatibility. The final 906-review baseline loader does not attach
summary documents by `source_product_id` alone.

**Payload columns**:

- `normalized_summary`: consumer-friendly projection. 일반 사용자는 이 필드를
  우선 읽는다.
- `long_doc`, `short_doc`: ES hit 원문 JSONB. source field 손실 방지를 위해
  보존하며, consumer가 추가 원천 필드를 필요로 할 때만 읽는다.

2026-06-17 로컬 적재 기준:

| Check | Count |
|---|---:|
| clean sidecar rows | 516 |
| `exact_category` | 495 |
| `not_found` | 21 |
| collision sidecar rows | 0 |
| fetched `summary-review-long` | 14,477 |
| fetched `summary-review-short` | 3,695 |

`SOURCE_KEY_COLLISION` product는 clean review-summary lookup 대상에서 제외한다.
로컬 product id 목록은 ES에 전송하지 않고, ES alias 전체 export 후 로컬에서
source identity로 조인한다.

---

## 4. Source review stats vs graph support count 의미

| 컬럼 | 의미 | 사용 권장 |
|---|---|---|
| `source_review_count_6m` | source raw review volume, 최근 6개월 | social proof / source popularity ranking |
| `source_review_score_count_6m` | source rating 점수가 있는 최근 6개월 review 수 | 평균 평점 신뢰도 표시 |
| `source_avg_rating_6m` | source raw `prd_apal_scr` 평균, 최근 6개월 | product trust / rating display |
| `source_review_count_all` | source raw review volume, 전체 기간 | 6개월 stats 가 부족할 때 fallback |
| `source_review_score_count_all` | source rating 점수가 있는 전체 review 수 | 전체 평균 평점 신뢰도 표시 |
| `source_avg_rating_all` | source raw `prd_apal_scr` 평균, 전체 기간 | rating fallback |
| `review_count_30d` | graph evidence 에 포함된 최근 30일 distinct review_id 수 (product-level) | graph shrinkage / graph confidence |
| `review_count_90d` | graph evidence 에 포함된 최근 90일 distinct review_id 수 | graph trend confidence |
| `review_count_all` | graph evidence 에 포함된 전체 distinct review_id 수 | graph confidence / fallback only |
| `signal_support_count_all` | **legacy** signal 발생 합계 (review-distinct 아님) | UI badge 전용 ("N signals mention this") |

상품의 원천 review volume/rating 은 반드시 `source_review_*` 를 사용한다.
`review_count_*` 는 GraphRapping graph evidence support 이며 source review
volume 이 아니다. `signal_support_count_all` 을 ranking 에 쓰면 corpus
inflation 으로 편향된다.
source stats 가 없는 상품은 `source_review_count_*`/`source_review_score_count_*`
가 `NULL` 이다. 이를 `0` 으로 간주하지 말고 unknown 으로 처리한다.

---

## 5. Provenance contract

- **`signal_evidence`** 가 signal-to-fact provenance 의 **single source of truth**
  - join: `signal_evidence.signal_id = wrapped_signal.signal_id`
  - fact ID: `signal_evidence.fact_id`
- **`wrapped_signal.source_fact_ids`** 는 **deprecated cache/debug 필드**
  - consumer 는 읽지 않는다 (향후 제거 예정)

```sql
SELECT sf.signal_id, se.fact_id, cf.predicate, cf.object_iri
FROM wrapped_signal sf
JOIN signal_evidence se ON se.signal_id = sf.signal_id
JOIN canonical_fact cf  ON cf.fact_id = se.fact_id
WHERE sf.is_promoted = true;
```

---

## 6. Promotion gate

Wave 2.8 promotion 기준 (`agg_product_signal.is_promoted = true` 행이 만족):

| Window | min distinct reviews |
|---|---|
| `30d` | 2 |
| `90d` | 3 |
| `all` | 3 |

추가:
- `avg_confidence >= 0.6`
- `synthetic_ratio <= 0.5`

Consumer 는 `is_promoted = true` 만 사용한다. 미달 행을 직접 promote 해서는
안 된다.

---

## 7. Active / soft-delete contract

| 테이블 | `is_active` 의미 |
|---|---|
| `product_master` | 카탈로그상 active SKU |
| `user_master` | 활성 사용자 |
| `agg_product_signal` | 최신 파이프라인 사이클에서 재확인됨. `false` = stale soft-deleted |

Consumer 는 모든 read 에서 `is_active = true` 를 기본 필터로 둔다.

---

## 8. 구매 이벤트 contract (`purchase_event_raw`)

Consumer 가 의존하는 필수 컬럼:
- `user_id`
- `product_id`
- `purchased_at` (timestamptz)
- `price` (numeric, nullable)
- `quantity` (int)
- `channel` (text)

---

## 9. Write-back 금지

GraphRapping truth tables (`product_master`, `user_master`,
`canonical_*`, `wrapped_signal`, `agg_*`, `serving_*`) 에 consumer 의
synthetic / simulation 출력을 write back 하지 않는다. 시뮬레이션 결과는
consumer 자체 스키마(별도 DB 또는 별도 schema)에 저장한다.

GraphRapping → consumer 는 **단방향 read**. 양방향이 필요하면 별도 API
(미정) 를 통해 협상한다.

---

## 10. 빠른 검증 쿼리

아래 4개는 표준 readiness check. 같은 쿼리 + 추가 reference 쿼리 (products /
users / window variants / operational helpers) 가
[`sql/consumer_contract_queries.sql`](../../sql/consumer_contract_queries.sql)
에 정본으로 묶여 있다. Consumer 는 그 파일을 import 하거나 발췌하여 사용.

```sql
-- 1) Schema readiness
SELECT version FROM schema_migrations ORDER BY version;

-- 2) Active product / user count
SELECT
  (SELECT COUNT(*) FROM product_master WHERE is_active) AS active_products,
  (SELECT COUNT(*) FROM user_master    WHERE is_active) AS active_users;

-- 3) Promoted signals per window
SELECT window_type, COUNT(*)
FROM agg_product_signal
WHERE is_active = true AND is_promoted = true
GROUP BY window_type
ORDER BY window_type;

-- 4) Provenance sanity
SELECT COUNT(*) AS signals_with_evidence
FROM wrapped_signal s
WHERE EXISTS (SELECT 1 FROM signal_evidence e WHERE e.signal_id = s.signal_id);
```

`src/db/contract_validator.py::validate_all` 가 프로그램적으로 검증하는 항목:
- 필수 테이블 + 필수 컬럼 schema — `_REQUIRED_TABLES` 의 11개 테이블:
  `product_master`, `user_master`, `purchase_event_raw`, `wrapped_signal`,
  `signal_evidence`, `agg_product_signal`, `agg_user_preference`,
  `serving_product_profile`, `serving_user_profile` (마지막 둘은
  `SERVING_*_PROFILE_COLUMNS` 미러로 **column completeness** 포함),
  `concept_registry`, `schema_migrations`
- promotion gate (window 별 min reviews + avg_confidence + synthetic_ratio)
- stale-active invariant
- product ID consistency
- strict source-grounding (호출 시 `enforce_source_grounding=True`):
  `serving_product_profile.source_product_id` 와 `product_master.product_id`
  불일치, source stats 가 존재하는데 promo-prefix brand (`【...`, `[...]`) 가
  product truth 로 노출되는 경우를 INVALID 처리
- minimum counts: `active_products`, `active_users`, `concepts`,
  `promoted_signals.<window>` (호출 시 `expected_min_*` / `signal_window` 지정)

> Validator 가 **직접 검증하지 않는 것** — consumer 측에서 별도로 확인:
> - `schema_migrations.version` 의 명시적 minimum 셋 존재 (위 §10 쿼리 1)
> - `signal_evidence` provenance coverage (위 §10 쿼리 4)
> - builder ↔ upsert 정합성 — `tests/test_serving_profile_alignment.py` 가
>   파이프라인 빌드 결과로 별도 보장

CI/배포 전 호출 권장.

---

## 12. Retention 한계 (2026-06-09 trace)

GraphRapping 의 누적 방어 메커니즘은 계층별로 강도가 다르다. Consumer 가
장기 운영을 계획할 때 알아야 할 알려진 한계를 명시한다.

### 12.1 계층별 누적 방어 현황

| 계층 | 메커니즘 | 한계 |
|---|---|---|
| `review_raw` | `review_version` 기반 idempotent upsert | review 텍스트 변경 시 child append (다음 행) |
| `ner_raw / bee_raw / rel_raw` | review_version 별 append-only | **review 자주 갱신되는 도메인에서 GB 단위 누적 가능** |
| `canonical_fact` | diff-based upsert (per-review) | 안정 |
| `wrapped_signal` | per-review full-replace ([signal_repo.py:45](../../src/db/repos/signal_repo.py#L45)) | 안정 |
| `agg_product_signal` (30d / 90d) | aggregation 시 시간 cutoff input filter; **persisted row 자동 삭제 없음** | stale cleanup 또는 backfill 에 의존 |
| **`agg_product_signal` (모든 window)** | `last_seen_at < now - 90일` 시 `is_active=false` ([mart_repo.py:293](../../src/db/repos/mart_repo.py#L293)) — incremental 에서 **opt-in** (`GRAPHRAPPING_AGG_CLEANUP_ENABLED=1`) | **인기 상품은 매일 갱신 → 영원히 active**; cleanup 미활성 시 stale row 영구 잔존 |
| `agg_user_preference` | `updated_at < now - 90일` 시 `is_active=false` — 동일 opt-in gate | 동일 — 활성 사용자는 영원히 active |
| **`quarantine_*` (5개 테이블)** | **TTL 없음** | **일일 누적, 정리 정책 부재** |
| `serving_product_profile` | promoted-only filter + product 별 edge type top-10 ([build_serving_views.py:59-64](../../src/mart/build_serving_views.py#L59-L64)) | 안정 |
| `serving_user_profile` | `agg_user_preference.is_active=true` filter + 선호 종류별 top-20 ([build_serving_views.py:153](../../src/mart/build_serving_views.py#L153)); **promotion gate 없음** | 안정 (단 user pref cleanup 미활성 시 stale row noise 가능) |

### 12.2 Quality filter 한계
- **1글자 필터는 `top_keyword_ids` 1종에만 적용** ([build_serving_views.py:124](../../src/mart/build_serving_views.py#L124)).
  다른 edge type (`top_bee_attr_ids`, `top_context_ids`, `top_concern_*`,
  `top_tool_ids` 등) 은 글자수 필터 없음 — promotion gate 만 통과하면 짧은
  노이즈 entity 도 노출 가능.
- Graph centrality (PageRank, eigenvector 등) 는 미구현. "graph" naming 이지만
  실제로는 RDB 기반 score aggregation + threshold gate.

### 12.3 알려진 무한 누적 위험 (3종)
1. **`agg_product_signal` `all` window + 30d/90d stale rows** — `last_seen_at`
   90일 cleanup 이 유일한 정리. 두 조건이 겹침:
   - cleanup 자체가 opt-in (`GRAPHRAPPING_AGG_CLEANUP_ENABLED=1`) — 미활성 시
     stale row 영구 잔존
   - 활성 시에도 인기 상품 (매일 신호 갱신) 은 last_seen_at 가 advance →
     stale 판정 회피
2. **`quarantine_*` 테이블 5종** — retention/TTL 정책 부재. 일일 batch 마다
   placeholder / unknown_keyword 등 일부 종류는 새 row 추가.
3. **`ner_raw / bee_raw / rel_raw`** — review_version 마다 append. 활발한
   review 갱신 도메인이면 row 수가 review × version 으로 증가.

### 12.3.b Quality (not retention) 위험 1종
- **`top_keyword_ids` 외 1글자 필터 부재** — 짧은 노이즈 entity 가 serving 에
  surface. 누적 hardcap 문제는 아니지만 (top-N cap 으로 양은 제한됨) 품질
  noise 위험. Wave 6 에서 quality filter 일반화 검토.

### 12.4 Consumer 운영 가이드

**단기 (수 개월 운영)**: 위 가드들로 충분. promoted-only filter + window=`all`
+ active=true 만 읽으면 자연 정제된 데이터.

**장기 (1년+ 운영)**: 모니터링 권장 지표:
- `pg_relation_size(table_name)` per 5계층 테이블
- `agg_product_signal` row count by `window_type` (특히 `all`)
- `quarantine_*` 5종 row count, 일일 delta
- `ner_raw / bee_raw / rel_raw` row count, 일일 delta
- 임계 초과 시 GraphRapping 팀에 retention job 요청

### 12.5 Wave 6 예정 작업 (사용자 지정 max 기간 후)
사용자가 리뷰 기준 max 기간 (예: 6개월) 을 지정하면 다음 작업 착수:
- `agg_product_signal.all` window TTL job
- `quarantine_*` 5 테이블 retention job (예: 30일 후 파기)
- `ner / bee / rel_raw` partitioning (월별 partition + drop)
- 미구현 1글자 필터를 다른 edge type 으로 확장 검토

현재 시점은 **미구현**. Consumer 는 위 한계 인지하고 시작.

---

## 13. Recommendation evidence-family 확장 계약 (2026-07-13, Phase 7 E0)

추천 후보의 **자격(eligibility)** 은 evidence family 로 판정된다. 이 절은
family 의 현행 분류·의미론과, **신규 family 를 추가할 때 반드시 지켜야 하는
계약**을 성문화한다 (기존에는 코드+테스트에만 존재 — fable_doc/06 진단 §6-4).

### 13.1 용어 구분 — SignalFamily ≠ evidence family (혼동 금지)

| 용어 | 정의 위치 | 성격 | 예 |
|---|---|---|---|
| **SignalFamily** | `src/common/enums.py` (enum) | **상품 신호**의 projection 분류 — Layer 2.5 wrapped_signal 이 어떤 종류의 리뷰 유래 신호인지 | `BEE_ATTR`, `CONCERN_POS`, `COMPARISON`, `TOOL` |
| **evidence family** | `src/rec/recommendation_evidence_index.py` (frozenset 타입 분류 + `CandidateEligibility`) | **추천 자격** 분류 — 유저-상품 overlap concept 이 어떤 근거 계열로 후보를 자격화하는지 | `PRODUCT_MASTER_TRUTH`, `REVIEW_GRAPH_RELATION`, `PURCHASE_BEHAVIOR` |

같은 단어(family)를 쓰지만 **서로 다른 레이어의 서로 다른 분류다**. 신규
신호를 붙일 때 SignalFamily(enum)에 값을 추가하는 것과 evidence family
(frozenset/eligibility 버킷)를 확장하는 것은 별개의 결정이며, 이 절의 계약은
**후자**에 적용된다.

### 13.2 현행 분류와 자격 의미론 (OR 자격)

`build_candidate_eligibility` 는 유저-상품 overlap concept 을 아래 계열로
분류하고, **하나라도 비어 있지 않으면 eligible** (OR 의미론):

| Evidence family | overlap concept 타입 (frozenset) | 의미 |
|---|---|---|
| `PRODUCT_MASTER_TRUTH` | `brand, category, catalog_keyword, ingredient, goal_master` | 카탈로그 진실과 유저 명시 선호의 일치 |
| `REVIEW_GRAPH_RELATION` | `keyword, bee_attr, semantic_keyword, semantic_bee_attr, context, concern, concern_bridge, tool, coused` | 리뷰 그래프 유래(promoted) 신호와의 일치 |
| `REVIEW_GRAPH_WEAK_RELATION` | `weak_semantic_keyword, weak_semantic_bee_attr` | 위의 약한(간접 semantic) 변형 |
| `PURCHASE_BEHAVIOR` | `owned_family, repurchased_family, repurchase_brand, repurchase_category, recent_purchase_brand` | 구매 확정 행동과의 일치 |
| (boost-only, **자격 불가**) | `comparison, collab, comention, similar` | `BOOST_ONLY_TYPES` — 결합 시 보정만, 단독으로 eligibility 못 삼. `comparison`만 COMPARE 모드에서 admit(`BOOST_ONLY_ADMISSIBLE_TYPES`). `similar`(Phase 8 G4, family 명 `PRODUCT_SIMILARITY_AFFINITY`, 2026-07-16 확정)는 추가로 retrieval `overlap_score` 집계에서도 제외(50컷 정렬 무영향). 2026-07 정정: 종전 표가 `comparison`을 review-graph로 오기 |

자격이 **될 수 없는** 것 (기존 규율, 신규 family 에도 그대로):
- `source_review_*` (source trust/popularity) — trust/tie-break 신호이지 자격
  근거가 아니다. source-stats 단독 eligible 은 전역 불변식 위반
  (`tests/test_expected_evidence_family_baseline.py` invariant (b)).
- `ACTIVE_IN_CATEGORY` — 활동 컨텍스트이지 명시 선호가 아님 (frozenset 에서
  의도적으로 제외됨).
- `review_summary` — 표시용 sidecar (§3.4), graph evidence 아님.

### 13.3 신규 evidence family 추가 조건 (계약)

신규 family(예: 액션 유래, 협업 신호)를 추가하는 변경은 아래 5개 조건을
**모두** 충족해야 한다:

1. **단독 자격 가능 여부를 반드시 명시** — 기본값은 **boost-only**:
   후보의 점수를 보정할 수는 있으나 그 family 단독으로는 `eligible=true`
   판정에 기여하지 못한다 (OR 자격 버킷에서 제외). 단독 자격을 부여하려면
   근거(왜 이 신호가 자격 수준의 확실성인지)를 DECISIONS 로 기록하고
   승인받아야 한다. boost-only 버킷의 코드 실체(`build_candidate_eligibility`
   의 eligible 판정에서 제외되는 5번째 분류)는 Phase 7 D1 에서 신설 예정이며
   A1 의 COMPARISON 과 공유한다.
2. **가중/shrinkage 원칙** — 신규 family 의 스코어 기여는 보수적 초기 가중
   으로 시작하고(기존 `scoring_weights.yaml` 패턴), support 가 낮은 신호는
   기존 shrinkage 메커니즘(support 기반 축소)을 그대로 통과해야 한다.
   "항상 켜지는" 비개인화 신호가 개인화 신호를 잠식하는 패턴(fable_doc/06
   진단 §2)을 재생산하지 않도록, 발화율(hit rate)이 높은 신호일수록 가중은
   낮게 잡는다.
3. **기대셋 + 계약 테스트 갱신 필수** —
   `tests/fixtures/golden_expected_evidence.yaml` 의 `known_families` 와
   해당 조합의 required/forbidden 을 갱신하고, **"단독 자격 fail" 계약
   테스트**(신규 family 만 있는 후보가 eligible=false 인 케이스)를 추가한다.
   boost-only family 는 top-N 에 등장해도 evidence family 로 세어지면 안
   된다는 불변식도 함께 고정한다. 랭킹 이동은 스냅샷 회귀
   (`tests/fixtures/ranking_snapshots/dense_golden.json` /
   `wide_golden.json`) diff 재승인으로 검증한다.
   **예외(boost-only, 2026-07-16 명문화)**: boost-only 타입은 자격(OR 버킷)에
   기여하지 않으므로 `known_families` 에 **추가하지 않는다** — `known_families`
   는 자격 가능 family 의 전수 집합이고, boost-only 는 "top-N 에 등장해도
   evidence family 로 세어지지 않는다" 불변식의 대상이다. `comparison`/
   `collab`/`comention`(D1·D2 선례)과 `similar`(P8-3a) 모두 이 예외를 따른다.
   "단독 자격 fail" 계약 테스트와 불변식 고정 의무는 예외 없이 그대로 적용된다.
4. **명명 규칙** — 대문자 SNAKE_CASE 명사구, 근거의 **성격**을 이름에
   담는다 (`PURCHASE_BEHAVIOR` 처럼 "무엇이 확인되었는가"). 접미사 규칙:
   확정 행동은 `_BEHAVIOR`, 관심/친화 수준은 `_INTEREST`/`_AFFINITY`.
   기존 SignalFamily enum 값과 이름이 겹치지 않게 한다 (COMPARISON 처럼
   양쪽에 존재하게 될 이름은 문서/코드 주석에서 레이어를 항상 명기).
5. **provenance 유지** — 신규 family 의 overlap 이 가리키는 근거도
   §5 provenance contract(신호→fact→원문 추적)를 만족해야 한다. 추적
   불가능한 근거는 family 후보가 아니다.

### 13.4 예정/도입 family 현황 (본 계약의 적용 이력)

| family | 트랙 | 자격 등급 | 상태 |
|---|---|---|---|
| `COMPARISON` | P7-1a (A1) | boost-only (`comparison` — COMPARE 모드만 admit) | **도입 완료** |
| `COLLABORATIVE_AFFINITY` | P7-4 (D1) | boost-only (`collab` — 단독 자격 불가·전 모드 admit 없음) | **도입 완료** (배선+대기) |
| `COMENTION` (co-mention) | P7 (D2) | boost-only (`comention` — 단독 자격 불가·전 모드 admit 없음) | **도입 완료** (배선+대기) |
| `PRODUCT_SIMILARITY_AFFINITY` | P8-3a (G4) | boost-only (`similar` — 단독 자격 불가·전 모드 admit 없음·retrieval 집계 제외) | **도입 완료** (배선+대기: 서빙 owned 엣지 1/50, DECISIONS/2026-07-16_phase8_g4_similar_boost.md) |
| `BEHAVIORAL_INTEREST` | Track E (E2) | 단독 자격 불가 (스쳐본 것은 자격이 아님) | 보류 (이벤트 스펙 확정 시) |

---

## 11. 변경 이력

| 날짜 | Wave | 변경 |
|---|---|---|
| 2026-06-08 | Wave 4 Task 7 | 초기 작성 |
| 2026-06-09 | Wave 5.5 | §10 에 `sql/consumer_contract_queries.sql` reference 추가 |
| 2026-06-09 | Wave 5.6 | §12 "Retention 한계" 섹션 추가 (documentation-only) |
| 2026-06-10 | Mockdata real product_id fix | product universe를 rs_own source product id 기반 517개로 고정하고 promoted product 수치 갱신 |
| 2026-06-15 | Source-grounded contract | `serving_product_profile` 에 source identity/review stats 를 추가하고 `review_count_*` 를 graph support count 로 명확화 |
| 2026-07-13 | Phase 7 E0 | §13 "Recommendation evidence-family 확장 계약" 추가 — 현행 자격 의미론(OR)과 신규 family 추가 조건(단독 자격/가중·shrinkage/기대셋·계약 테스트/명명/provenance) 성문화 |
| 2026-07-16 | Phase 8 P8-3a | §13.2 boost-only 행에 `similar` 확정 편입(`PRODUCT_SIMILARITY_AFFINITY`, retrieval 집계 제외 명기) · §13.3(3) boost-only 는 `known_families` 제외 예외 명문화 · §13.4 도입 현황 표로 갱신(COMPARISON/COLLABORATIVE_AFFINITY/COMENTION/PRODUCT_SIMILARITY_AFFINITY 도입 완료) |
