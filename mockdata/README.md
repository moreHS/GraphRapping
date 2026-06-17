# Mock Data

GraphRapping 프로젝트의 3대 데이터 소스(유저/상품/리뷰트리플)에 대한 참조 데이터.

## 파일 목록

| 파일 | 형식 | 레코드 수 | 용도 |
|------|------|----------|------|
| `shared_entities.json` | JSON | 브랜드 38, 상품 517, 유저 50 | source brand/product/user ID 앵커 |
| `product_catalog_es.json` | JSON array | 517개 | `load_products_from_json()` 입력 |
| `user_profiles_raw.json` | JSON dict | 50명 | personal-agent 원본 7-column 형식 |
| `user_profiles_normalized.json` | JSON dict | 50명 | `load_users_from_profiles()` 입력 |
| `review_triples_raw.json` | JSON array | 906개 리뷰 (v260605 refresh, 2026-06-05) | `load_reviews_from_json()` 입력 |
| `review_kg_output.json` | JSON object | entities ~38, edges ~43 | KG 파이프라인 출력 참조 (브랜드 official claim 없음) |
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
SALE_STATUS                → 선택 필터. 기본 loader는 전체 source product 로드
```

추가 참고 필드: `CTGR_L/M/S_NAME`, `SALE_PRICE`, `MAIN_EFFECT`, `MAIN_INGREDIENT`, `REVIEW_COUNT`, `REVIEW_SCORE`, 리뷰 키워드 필드들.

### 상품 source truth 의미

- `ONLINE_PROD_SERIAL_NUMBER`는 GraphRapping `product_id`의 원본 source product id이다.
- `BRAND_NAME`은 명시적 source/catalog 필드가 있을 때만 product truth로 취급한다.
- 합성 mock은 `prd_nm` 첫 token이나 프로모션 prefix에서 브랜드를 만들지 않는다. 브랜드 출처가 없으면 `BRAND_NAME`은 `null`/누락이어야 하며, loader는 이를 `brand_name=None` + `source_truth_quality="MISSING_SOURCE_BRAND"`로 유지한다.
- 현재 체크인된 `product_catalog_es.json`은 2026-06-16 실상품 source truth로 refresh된 fixture다. 따라서 516개 상품은 `SOURCE_GROUNDED` brand/review stats를 갖고, 1개 상품은 `SOURCE_KEY_COLLISION`으로 표시된다.
- `REVIEW_COUNT` / `REVIEW_SCORE`는 source catalog 또는 source stats가 제공한 값만 의미가 있다. source 값이 없으면 `null`/누락이어야 하며, mock 합성은 `0` / `0.0`을 가짜 source truth로 쓰지 않는다.

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

#### 출처 (v260605 refresh, 2026-06-05)

기존 15개 손수 큐레이션 fixture → **906 reviews** 로 확대.

- **본문 + ner + bee + relation**: `/Users/amore/Jupyter_workplace/Relation/source_data/ver260605/` 의 신규 데이터 합성
  - `final_relation_ko_ner2ner.jsonl` (1,400 reviews, NER-NER 관계)
  - `fin_ko_ner2bee_true_0528.jsonl` (1,495 reviews, NER-BeE 관계)
  - id-overlap 998 중 n2b broken markup 92건 drop → **906 합성**
- **메타 (product_id/prd_nm/channel/date/age/sex/sktp/sktr)**: `rs_own.jsonl` 에서 `random.Random(42).sample(k=906)` 으로 부여한 fixture target metadata
- **product 일관성**: `rs_own.product_id` 를 `source_product_id`로 string 보존하고, review sample의 distinct product universe로 `product_catalog_es.json`를 구성
- **author_key**: `hashlib.sha256(rs_own.id) % 150` → 150 distinct buckets
- **변환 스크립트**: `scripts/synthesize_mock_from_v260605.py` (재실행 가능, 결정적)

세부 lineage, `Review Target` 의미, 현재 DB 적재/조합 상태:
`docs/architecture/v260605_906_fixture_lineage.md`

최종 906-review 기준과 active 문서 정리 기준:
`DECISIONS/2026-06-17_final_906_review_baseline_cleanup.md`

#### Review Target contract

`review_triples_raw.json` 본문의 `Review Target`은 해당 review row의
target product placeholder다. 검증 기준은 상품명이 본문에 문자 그대로
등장하는지가 아니라, `source_product_id`/`prod_nm` metadata가
`product_catalog_es.json`와 `product_master`의 같은 product id로 연결되고,
`Review Target` relation/BEE evidence가 그 matched product로 resolve되는지다.

현재 체크인된 mock catalog는 실상품 source truth가 있는 상태다.
따라서 `shared_entities.json`은 source-grounded brand 38개,
`product_id == source_product_id`인 상품 ID 517개, user ID 50개를
교차 참조 앵커로 제공한다. 단 `review_kg_output.json`은 review-derived KG
출력 참조물이므로 product master의 official brand claim을 별도로 만들지 않는다.

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

1. `shared_entities.products[].product_id == source_product_id`이며 catalog `ONLINE_PROD_SERIAL_NUMBER`와 일치
2. review_triples `source_product_id` ⊂ product_catalog `ONLINE_PROD_SERIAL_NUMBER`
3. review_kg_output PRD `scope_key` ⊂ shared_entities product IDs
4. review_kg_output edges의 entity_id ⊂ entities
5. source brand가 없는 합성 mock에서는 shared/KG artifact가 브랜드 truth를 만들지 않음
6. 현재 source-grounded catalog에서는 `shared_entities.brands`가 catalog source brand를 포함

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
| `relation_loader.load_reviews_from_json()` | Relation project JSON | relation[]이 68개 canonical predicates로 이미 추출됨 | 기본/레거시 중간 산출물 |
| `rs_jsonl_loader.load_reviews_from_rs_jsonl()` | S3 rs.jsonl | NER/BEE spans 존재, relation model 서빙 후 relation[] 포함 예정 | 운영 원본 소스 |

두 loader 모두 `RawReviewRecord`를 출력합니다.

일반적인 pipeline run은 두 loader를 동시에 쓰지 않고 입력 포맷에 맞는 loader 하나를 선택합니다. 여러 소스 형식을 의도적으로 합치는 batch에서만 각 loader 결과를 `RawReviewRecord`로 변환한 뒤 합칩니다.

### Product matching 계약

`source_product_id`/catalog `ONLINE_PROD_SERIAL_NUMBER` exact match가 우선입니다.
카탈로그 `prd_nm`에는 브랜드 prefix가 포함될 수 있고, 리뷰 `prod_nm`에는 같은 prefix가 빠져 있을 수 있습니다.

예:
- catalog: `라네즈 워터뱅크 블루 히알루로닉 세럼`
- review: `워터뱅크 블루 히알루로닉 세럼`

Product matcher는 source-grounded brand가 있을 때만 같은 브랜드 안에서 brand-stripped normalized key가 단일 후보인지 확인합니다.
현재 mock처럼 source brand가 없으면 `prd_nm` 첫 token을 브랜드로 추론하지 않습니다.

**rs_jsonl 사용 예시:**
```python
from src.loaders.rs_jsonl_loader import load_reviews_from_rs_jsonl
reviews = load_reviews_from_rs_jsonl("mockdata/review_rs_samples.json")
```
