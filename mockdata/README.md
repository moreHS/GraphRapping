# Mock Data

GraphRapping 프로젝트의 3대 데이터 소스(유저/상품/리뷰트리플)에 대한 참조 데이터.

## 파일 목록

| 파일 | 형식 | 레코드 수 | 용도 |
|------|------|----------|------|
| `shared_entities.json` | JSON | 브랜드 6, 상품 10, 유저 3 | 교차 참조 앵커 (ID 일관성 보장) |
| `product_catalog_es.json` | JSON array | 12개 (판매중 10 + 중지 2) | `load_products_from_json()` 입력 |
| `user_profiles_raw.json` | JSON dict | 3명 | personal-agent 원본 7-column 형식 |
| `user_profiles_normalized.json` | JSON dict | 3명 | `load_users_from_profiles()` 입력 |
| `review_triples_raw.json` | JSON array | 15개 리뷰 | `load_reviews_from_json()` 입력 |
| `review_kg_output.json` | JSON object | entities ~44, edges ~51 | KG 파이프라인 출력 참조 |

## 데이터 소스별 스키마

### 1. 상품 (product_catalog_es.json)

ES 인덱스 `aibe-online-prod-mstr-rag` 형식. Loader 필수 필드:

```
ONLINE_PROD_SERIAL_NUMBER  → product_id
prd_nm                     → product_name
BRAND_NAME                 → brand_name
CTGR_SS_NAME               → category_name
SALE_STATUS                → 필터 ("판매중" only)
```

추가 참고 필드: `CTGR_L/M/S_NAME`, `SALE_PRICE`, `MAIN_EFFECT`, `MAIN_INGREDIENT`, `REVIEW_COUNT`, `REVIEW_SCORE`, 리뷰 키워드 필드들.

### 2. 유저 프로필

**Raw (7-column)**: `user_profile`, `skin_profile`, `purchase_profile`, `brand_affinity`, `repurchase_category_affinity`, `seasonal_affinity`, `profile_from_chathistory`

**Normalized (3-group)**: `basic`, `purchase_analysis`, `chat` — `adapt_user_profile()` 소비 형식

### 3. 리뷰 트리플 (review_triples_raw.json)

Relation 프로젝트 NER+BEE+REL 추출 출력:

```
brnd_nm, clct_site_nm, prod_nm, text, drup_dt
ner[]: {word, entity_group, start, end, sentiment}
bee[]: {word, entity_group, start, end, sentiment}
relation[]: {subject, object, relation, source_type}
```

### 4. KG 출력 (review_kg_output.json)

KGEntity + KGEdge 형식. evidence_kind별 confidence 범위:
- RAW_REL: 0.8~1.0
- NER_BEE_ANCHOR: 0.7~0.9
- BEE_SYNTHETIC: 0.3~0.5
- AUTO_KEYWORD: 0.2~0.4

## 교차 참조 규칙

1. user_profiles 상품 코드 ⊂ product_catalog `ONLINE_PROD_SERIAL_NUMBER`
2. 브랜드명 일치: user_profiles ↔ product_catalog ↔ review_triples
3. review_triples `prod_nm` ≈ product_catalog `prd_nm` (퍼지매칭)
4. review_kg_output edges의 entity_id ⊂ entities
5. user_profiles 카테고리 ⊂ product_catalog `CTGR_SS_NAME`

## 사용 예시

```python
import json
from src.loaders.product_loader import load_products_from_json
from src.loaders.user_loader import load_users_from_profiles
from src.loaders.relation_loader import load_reviews_from_json

# 상품 로딩
products = load_products_from_json("mockdata/product_catalog_es.json")

# 유저 로딩
with open("mockdata/user_profiles_normalized.json") as f:
    profiles = json.load(f)
users = load_users_from_profiles(profiles)

# 리뷰 로딩
reviews = load_reviews_from_json("mockdata/review_triples_raw.json")
```
