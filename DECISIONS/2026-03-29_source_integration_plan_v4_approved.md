# 소스→GraphRapping 통합 계획 v4

## Context
3개 소스의 실제 스키마를 확인 완료. 이제 매핑 문서를 정본으로 만들고, 로더를 구현한다.
GPT 3회 REJECT → v4에서 "매핑 문서 first" 접근.

## 실행 순서

### Step 1: 매핑 문서 작성 (정본)
`DECISIONS/source_to_graphrapping_mapping.md`

이 문서가 모든 로더의 설계 명세 역할. 필드별 매핑 + 변환 규칙 + default값 + 갭/defer 전부 포함.

### Step 2: 로더 구현
매핑 문서 기준으로 4개 로더 구현.

### Step 3: 통합 스크립트 + 테스트

## Step 1 상세: 매핑 문서 내용

### 소스 1: Relation JSON → RawReviewRecord

```
소스 파일: /Jupyter_workplace/Relation/source_data/hab_rel_sample_ko_withPRD_listkeyword.json
(또는 hab_rel_sample.json 등 relation[]이 포함된 최종 산출물)
전제: upstream에서 이미 65 canonical predicate로 추출 완료
```

| 소스 필드 | 타입 | → GraphRapping 필드 | 변환 규칙 |
|----------|------|-------------------|----------|
| `brnd_nm` | str | `RawReviewRecord.brnd_nm` | 그대로 |
| `prod_nm` | str | `RawReviewRecord.prod_nm` | 그대로 |
| `text` | str | `RawReviewRecord.text` | 그대로 |
| `clct_site_nm` | str | `RawReviewRecord.clct_site_nm` | 그대로 |
| `drup_dt` | str(ISO) | `RawReviewRecord.created_at` | 필드명 매핑 |
| `ner[]` | array | `RawReviewRecord.ner[]` | 그대로 (entity_group, word, start, end, sentiment) |
| `bee[]` | array | `RawReviewRecord.bee[]` | 그대로 (entity_group, word, start, end, sentiment) |
| `relation[]` | array | `RawReviewRecord.relation[]` | 그대로 (subject{}, object{}, relation, source_type) |
| (row index) | int | `RawReviewRecord.source_row_num` | JSON 파일 내 0-based 순번 |
| (없음) | — | `RawReviewRecord.source_review_key` | None (fallback ID 사용) |
| (없음) | — | `RawReviewRecord.author_key` | None (REVIEW_LOCAL) |
| `ner_cot` | str | 미사용 | 무시 |
| `bee_cot` | str | 미사용 | 무시 |
| `pred[]` | array | 미사용 | relation[] 없는 파일에서는 BEE fact만 처리 |

**review_id 생성**: `make_review_id(source=clct_site_nm, source_review_key=None, brand_name_raw=brnd_nm, product_name_raw=prod_nm, review_text=text, collected_at=drup_dt, source_row_num=str(row_index))`

**relation[] 전제**: 입력 파일은 이미 65 canonical predicate. `relation_canonical_map.json` identity mapping으로 충분. 633 raw label이 섞인 경우 Relation 프로젝트의 full mapping 파일로 교체.

### 소스 2: Elasticsearch → ProductRecord

```
ES index: amore-prod-mstr
전제: ES 접근은 별도 클라이언트로 직접 또는 API로 — 로더에서 ES scroll/search 수행
```

| 소스 필드 (ES) | 타입 | → GraphRapping 필드 | 변환 |
|---------------|------|-------------------|------|
| `ONLINE_PROD_SERIAL_NUMBER` | str | `ProductRecord.product_id` | 그대로 |
| `prd_nm` | str | `ProductRecord.product_name` | 그대로 |
| `BRAND_NAME` | str | `ProductRecord.brand_name` | 그대로 |
| `BRAND_NAME` | str | `ProductRecord.brand_id` | `normalize_text(BRAND_NAME)` |
| `CTGR_SS_NAME` | str | `ProductRecord.category_name` | 그대로 |
| `CTGR_SS_NAME` | str | `ProductRecord.category_id` | `normalize_text(CTGR_SS_NAME)` |
| `SALE_STATUS` | str | (필터) | `판매중`만 로드 |
| (없음) | — | `ProductRecord.price` | None (defer) |
| (없음) | — | `ProductRecord.ingredients` | [] (defer) |
| (없음) | — | `ProductRecord.main_benefits` | [] (defer) |
| (없음) | — | `ProductRecord.country_of_origin` | None (defer) |

**로더 반환값**:
```python
@dataclass
class ProductLoadResult:
    product_masters: dict[str, dict]        # product_id → product_master row
    product_index: ProductIndex             # ProductIndex.build(products)
    concept_links: dict[str, list[dict]]    # "product:{pid}" → entity_concept_link rows
    concept_seeds: list[dict]               # concept_registry rows
    canonical_entities: list[dict]          # canonical_entity rows
```

이 구조가 `run_batch()`의 `product_masters`, `product_index`, `concept_links` 입력에 직접 매핑.

### 소스 3: personal-agent PostgreSQL → User Data

```
DB: personal-agent PostgreSQL (agent.aibe_user_context_mstr_v 뷰)
7개 JSONB 컬럼: user_profile, skin_profile, purchase_profile, brand_affinity,
                repurchase_category_affinity, seasonal_affinity, profile_from_chathistory
전제: encrypted user_id가 stable (preflight gate로 검증)
```

| 정규화 후 필드 | → GraphRapping 처리 |
|--------------|-------------------|
| `basic.*` | `adapt_user_profile()` → HAS_SKIN_TYPE, HAS_SKIN_TONE 등 |
| `purchase_analysis.preferred_*_brand` | → PREFERS_BRAND facts |
| `purchase_analysis.active_product_category` | → PREFERS_CATEGORY facts |
| `chat.face/hair/body/...` | → HAS_CONCERN, WANTS_GOAL, PREFERS_BEE_ATTR 등 |
| `chat.ingredients.preferred/avoid/allergy` | → PREFERS_INGREDIENT, AVOIDS_INGREDIENT |

**로더 반환값**:
```python
@dataclass
class UserLoadResult:
    user_masters: dict[str, dict]           # user_id → user_master row
    user_adapted_facts: dict[str, list[dict]]  # user_id → adapted preference facts
```

**preflight gate**: `verify_user_id_stability(pool, n=5)`
- 랜덤 5명 유저에 대해 2회 조회, encrypted user_id 비교
- 전부 일치 → 통과
- 1건이라도 불일치 → user loading 전체 defer, 로그에 WARNING 기록
- 이 경우 `run_batch()`에 `user_masters={}`, `user_adapted_facts={}` 전달 (상품 그래프만 구축)

**MVP defer**: `repurchase_summary`, `seasonal_summary`, 구매 이벤트 — v1에서 미적재

### 소스 4: Purchase Events (Optional, MVP defer)
- personal-agent DB 또는 구매 이력 DB에서 PurchaseEvent 로드
- MVP에서는 명시적으로 defer
- defer 시: `run_batch(purchase_events_by_user={})` → brand confidence 부스트 없음

## Step 2: 로더 구현

| 파일 | 입력 | 출력 | 핵심 로직 |
|------|------|------|----------|
| `src/loaders/relation_loader.py` | JSON file path | `list[RawReviewRecord]` | streaming read, field mapping, row_index |
| `src/loaders/product_loader.py` | ES client/config | `ProductLoadResult` | ES scroll, field mapping, product_ingest 호출, index 구축 |
| `src/loaders/user_loader.py` | PG pool + user_ids | `UserLoadResult` | PG query, normalize, adapt, preflight gate |

## Step 3: 통합 스크립트

`src/jobs/run_full_load.py`:
```python
async def run_full_load(config):
    pool = await create_pool(config.graphrapping_db_url)
    await migrate(pool)

    # 1. Product loading (ES → GraphRapping)
    product_result = await load_products(es_config)
    # persist: product_master, concept_registry, entity_concept_link, canonical_entity

    # 2. User loading (PG → GraphRapping) — with preflight gate
    user_result = await load_users(user_pg_pool, user_ids)
    # if gate fails: user_result = empty

    # 3. Review loading + pipeline (JSON → GraphRapping)
    reviews = load_reviews_from_json(json_path)
    batch_result = run_batch(
        reviews=reviews,
        product_index=product_result.product_index,
        product_masters=product_result.product_masters,
        concept_links=product_result.concept_links,
        user_masters=user_result.user_masters,
        user_adapted_facts=user_result.user_adapted_facts,
        ...
    )

    # 4. Persist (if DB mode)
    for bundle in batch_result.bundles:
        await persist_review_bundle(pool, bundle)
    await persist_aggregates(pool, ...)
```

순서: Product(concept seed 필요) → User(optional) → Review(product_index 필요) → Aggregate → Serve

## 검증
- [ ] relation_loader: 실제 JSON 10건 → RawReviewRecord 정상 변환
- [ ] product_loader: ES mock/실제 → ProductLoadResult 정상 (product_masters + index + links)
- [ ] user_loader: preflight gate pass/fail 양쪽 테스트
- [ ] run_full_load: 소규모 (100 reviews + 10 products + 3 users) E2E
- [ ] 기존 96 tests 유지
