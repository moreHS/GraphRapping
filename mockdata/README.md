# Mock Data

GraphRapping 프로젝트의 3대 데이터 소스(유저/상품/리뷰트리플)에 대한 참조 데이터.

## 파일 목록

| 파일 | 형식 | 레코드 수 | 용도 |
|------|------|----------|------|
| `shared_entities.json` | JSON | 브랜드 6, 상품 10, 유저 3 | 교차 참조 앵커 (ID 일관성 보장) |
| `product_catalog_es.json` | JSON array | 47개 | `load_products_from_json()` 입력 |
| `user_profiles_raw.json` | JSON dict | 50명 | personal-agent 원본 7-column 형식 |
| `user_profiles_normalized.json` | JSON dict | 50명 | `load_users_from_profiles()` 입력 |
| `review_triples_raw.json` | JSON array | 15개 리뷰 | `load_reviews_from_json()` 입력 |
| `review_kg_output.json` | JSON object | entities ~44, edges ~51 | KG 파이프라인 출력 참조 |
| `review_rs_samples.json` | JSON array | 20개 (own 10 + extn 6 + glb 4) | **실제 S3 rs.jsonl 형식** 참조 데이터 |
| `SCHEMA_RS_JSONL.md` | Markdown | - | rs.jsonl 전체 스키마 + Snowflake 매핑 문서 |

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

### 유저 데이터 로딩 계약

**공식 입력 형식**: `user_profiles_normalized.json` (3-group: basic/purchase_analysis/chat)
- `load_users_from_profiles()` 직접 입력 가능
- `adapt_user_profile()` 소비 형식과 동일

**참조용**: `user_profiles_raw.json` (7-column: personal-agent 원본)
- GraphRapping loader에 직접 입력 불가
- personal-agent의 `_normalize_profile()` 변환이 필요
- 스키마 참조 및 미래 raw → normalized 변환기 개발 시 활용

### 3. 리뷰 트리플 (review_triples_raw.json)

GraphRapping `load_reviews_from_json()` 입력 형식 (중간 변환 포맷):

```
brnd_nm, clct_site_nm, prod_nm, text, drup_dt
source_review_key: stable external review ID — deterministic review_id 생성
author_key: stable reviewer identity — cross-review reviewer proxy
source_product_id: 소스 시스템 원본 상품 ID (own: ecp_onln_prd_srno, extn: std_prd_cd)
channel: 채널 코드 ("031", "036", "navershopping", "kakao" 등)
reviewer_profile: 리뷰어 인구통계 (own만 제공, extn/glb은 null)
ner[]: {word, entity_group, start, end, sentiment}
bee[]: {word, entity_group, start, end, sentiment}
relation[]: {subject, object, relation, source_type}
```

### 3-1. 실제 파이프라인 원본 (review_rs_samples.json)

S3 rs.jsonl 원본 형식. 상세 스키마: [SCHEMA_RS_JSONL.md](SCHEMA_RS_JSONL.md)

| Source | 레코드 수 | Channel | 전용 필드 |
|--------|----------|---------|----------|
| own | 10 | 031, 036, 048 | age_sctn_cd, sex_cd, sktp_nm, sktr_nm |
| extn | 6 | navershopping, ssg, oliveyoung, kakao | rspn_sal_lcns_nm |
| glb | 4 | amazon, sephora | rspn_sal_lcns_nm |

### 4. KG 출력 (review_kg_output.json)

KGEntity + KGEdge 형식. evidence_kind별 confidence 범위:
- RAW_REL: 0.8~1.0
- NER_BEE_ANCHOR: 0.7~0.9
- BEE_SYNTHETIC: 0.3~0.5
- AUTO_KEYWORD: 0.2~0.4

## 교차 참조 규칙

1. user_profiles 상품 코드 ⊂ product_catalog `ONLINE_PROD_SERIAL_NUMBER`
2. 브랜드명 일치: user_profiles ↔ product_catalog ↔ review_triples
3. review_triples `prod_nm` ≈ product_catalog `prd_nm` (brand-aware normalized matching)
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

### Loader 계약 구분

| Loader | 입력 형식 | 전제조건 | 사용처 |
|--------|----------|----------|--------|
| `relation_loader.load_reviews_from_json()` | Relation project JSON | relation[]이 65개 canonical predicates로 이미 추출됨 | 기본/레거시 중간 산출물 |
| `rs_jsonl_loader.load_reviews_from_rs_jsonl()` | S3 rs.jsonl | NER/BEE spans 존재, relation model 서빙 후 relation[] 포함 예정 | 운영 원본 소스 |

두 loader 모두 `RawReviewRecord`를 출력합니다.

일반적인 pipeline run은 두 loader를 동시에 쓰지 않고 입력 포맷에 맞는 loader 하나를 선택합니다. 여러 소스 형식을 의도적으로 합치는 batch에서만 각 loader 결과를 `RawReviewRecord`로 변환한 뒤 합칩니다.

### Product matching 계약

카탈로그 `prd_nm`에는 브랜드 prefix가 포함될 수 있고, 리뷰 `prod_nm`에는 같은 prefix가 빠져 있을 수 있습니다.

예:
- catalog: `라네즈 워터뱅크 블루 히알루로닉 세럼`
- review: `워터뱅크 블루 히알루로닉 세럼`

Product matcher는 같은 브랜드 안에서 brand-stripped normalized key가 단일 후보일 때 fuzzy 이전에 deterministic match로 처리합니다.

**rs_jsonl 사용 예시:**
```python
from src.loaders.rs_jsonl_loader import load_reviews_from_rs_jsonl
reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json")
```
