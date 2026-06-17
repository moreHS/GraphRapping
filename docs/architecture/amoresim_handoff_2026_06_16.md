# AmoreSimulation Handoff — 2026-06-16

GraphRapping side 가 AmoreSimulation consumer-side 작업을 위해 준비한 현재
스냅샷 + 매핑 가이드 + 정리 결과.

같이 받는 자료:
- 본 문서
- [`graphrapping_snapshot_2026_06_16.json`](graphrapping_snapshot_2026_06_16.json) — 현재 영속 DB 의 실측 메타/카운트
- [`db_consumer_contract.md`](db_consumer_contract.md) — 표준 contract
- [`v260605_906_fixture_lineage.md`](v260605_906_fixture_lineage.md) — 906 review fixture lineage 와 `Review Target` contract
- [`product_master_review_graph_linkage_2026_06_16.md`](product_master_review_graph_linkage_2026_06_16.md) — product master / review graph / review summary linkage 쟁점과 권장 schema
- [`product_master_real_snapshot_2026_06_16.md`](product_master_real_snapshot_2026_06_16.md) — 오늘자 실상품마스터 조회/로컬 스냅샷/DB 재적재 결과
- [`/sql/consumer_contract_queries.sql`](../../sql/consumer_contract_queries.sql) — 표준 read 쿼리

---

## 1. 한 줄 요약

**2026-06-16 후속 작업으로 mock catalog 를 오늘자 실상품마스터 기반
compat catalog 로 교체했다. 906개 리뷰는 여전히
`review_raw.source_product_id = review_catalog_link.matched_product_id` 로
906/906 연결되지만, 실제 원천 기준 lossless 식별자는
`source_channel + source_key_type + source_product_id` 이다. `35119`는
031/036 cross-channel key collision 이므로 현행 product-id-only schema 에서는
`SOURCE_KEY_COLLISION`으로 표시한다.**

## 2. 2026-06-16 정리 사항

| 항목 | 현재 상태 |
|---|---|
| DB | `graphrapping` fresh full-load 완료 |
| 삭제한 로컬 구 DB | `graphrapping_wave4` |
| AmoreSimulation env | `GRAPHRAPPING_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/graphrapping` |
| 구 handoff/snapshot | 2026-06-10 파일 삭제, 2026-06-16 snapshot 으로 교체 |
| product catalog | 오늘자 실상품마스터 compat catalog 로 교체 |
| source identity snapshot | 518 source identities, Snowflake 매칭 518/518 |
| product id collision | `35119` 1건, `SOURCE_KEY_COLLISION`으로 표시 |
| product brand truth | 516/517 source-grounded brand 보유 |
| source review stats | 516/517 compat products 보유, source identity 기준 518/518 보유 |
| mock shared/KG fixture | 2026-06-17 최종 기준으로 `shared_entities`는 source-grounded brand 38개, product 517개, user 50개를 보유. KG 출력 참조물은 review-derived evidence로 유지하며 product master의 official brand claim을 대체하지 않음 |

## 3. 영속된 DB 스냅샷 (`graphrapping` / public schema)

| 테이블 | 행 수 | 비고 |
|---|---:|---|
| `schema_migrations` | 8 | DDL 적용 완료 |
| `product_master` | 517 | 516 source-grounded, 1 source key collision |
| `product_review_stats` | 516 | compat collision `35119` 제외 |
| `user_master` | 50 | active users |
| `review_raw` | 906 | source product id 누락 0 |
| `review_catalog_link` | 906 | source id exact match 906 |
| `wrapped_signal` | 2801 | per-review signals |
| `signal_evidence` | 2839 | provenance SoT |
| `canonical_fact` | 3873 | |
| `canonical_entity` | 1183 | |
| `agg_product_signal` | 6849 | 30d + 90d + all 합산 |
| `agg_user_preference` | 658 | |
| `serving_product_profile` | 517 | source stats 516개 반영 |
| `serving_user_profile` | 50 | |
| `concept_registry` | 407 | |
| `entity_concept_link` | 3899 | |
| `pipeline_run` | 1 | FULL / COMPLETED |

### Promoted signals

| window | promoted count |
|---|---:|
| `30d` | 237 |
| `90d` | 70 |
| `all` | 70 |

## 4. Source Truth Contract

2026-06-16 real snapshot 기준:

- `BRAND_NAME` source column is available for 516/517 compat products.
- Review source brand (`review_raw.brand_name_raw`) is absent, so review brand rows = 0.
- Source review volume/rating is Snowflake-grounded for 516/517 compat products.
- `source_review_count_* = NULL` still means unknown, but 현재 compat 에서는
  `SOURCE_KEY_COLLISION` 1건이 대표 사례다.

Graph support counts remain separate:

- `review_count_30d/90d/all`: promoted graph evidence distinct review count.
- `signal_support_count_all`: legacy signal-line support count.
- `source_review_*`: source raw review volume/rating only.

## 5. AmoreSimulation Adapter Notes

Required read filters remain:

```sql
WHERE pm.is_active = true
  AND spp.is_active = true
  AND ag.is_active = true
  AND ag.is_promoted = true
  AND aup.is_active = true
```

Default signal window should be `all` unless the caller explicitly asks for
`30d`/`90d`.

Product join no longer needs an id mapper:

```python
graphrapping_pid = es_pid
```

`project_id` is not part of the GraphRapping corpus. Keep it on the
AmoreSimulation routing side, not in GraphRapping SQL.

## 6. Quick Readiness Check

```sql
SELECT
  (SELECT COUNT(*) FROM product_master WHERE is_active=true) AS active_products,
  (SELECT COUNT(*) FROM review_raw WHERE is_active=true) AS active_reviews,
  (SELECT COUNT(*) FROM serving_product_profile WHERE is_active=true) AS serving_products;
-- Expected: 517 / 906 / 517

SELECT COUNT(*) FROM review_catalog_link
WHERE source_product_id IS NULL
   OR matched_product_id IS NULL
   OR source_product_id <> matched_product_id;
-- Expected: 0

SELECT COUNT(*) FROM product_master
WHERE brand_name IS NOT NULL
   OR source_truth_quality <> 'MISSING_SOURCE_BRAND';
-- Expected: 0 for current mock

SELECT COUNT(*) FROM serving_product_profile
WHERE source_review_count_all = 0;
-- Expected: 0 for current mock; unknown source stats are NULL
```

## 7. DB 접속 정보

```yaml
db_url:        postgresql://postgres:postgres@localhost:5432/graphrapping
schema:        public
asyncpg_pool:  min_size=1, max_size>=2 (advisory lock compatible)
contract_doc:  docs/architecture/db_consumer_contract.md
sql_lib:       sql/consumer_contract_queries.sql
snapshot:      docs/architecture/graphrapping_snapshot_2026_06_16.json
```

## 8. 변경 이력

| 날짜 | 변경 |
|---|---|
| 2026-06-10 | Mock product id universe 를 `rs_own.product_id` 517개로 확장 |
| 2026-06-15 | source-grounded product/review contract 보완 |
| 2026-06-16 | 구 DB/snapshot/mock brand 잔재 제거, `graphrapping` fresh full-load 완료 |
