# S3 rs.jsonl 리뷰 분석 데이터 스키마

리뷰 분석 파이프라인의 최종 산출물 형식. Snowflake 원본 → prepare → NER/BEE 분석 → rs.jsonl.
GraphRapping은 이 데이터를 입력으로 받아 KG를 구축한다.

---

## 1. 레코드 구조 개요

```
rs.jsonl (1 line = 1 review)
├── 공통 필드 (모든 source)
│   ├── id, text, date, product_id, prd_nm, channel
│   ├── p_chain_inputs[]  ← Review Summary 모델 입력
│   ├── tokens[]          ← MeCab 형태소 (참조용, 미사용 가능)
│   ├── ner_spans[]       ← NER 개체명 인식
│   ├── bee_spans[]       ← BEE 속성 감성분석
│   ├── relation[]        ← 관계 추출 (추가 예정)
│   └── brnd_nm           ← 브랜드명 (추가 예정)
├── own 전용 필드
│   ├── age_sctn_cd, sex_cd, sktp_nm, sktr_nm
└── extn/glb 전용 필드
    └── rspn_sal_lcns_nm
```

---

## 2. 공통 필드

| 필드 | 타입 | 설명 | 예시 |
|------|------|------|------|
| `id` | str | 리뷰 고유 ID | `"RV202603310001234"` (own), `"EXT_NV_20260331_98765"` (extn) |
| `text` | str | 리뷰 원문 (노이즈 제거 후) | `"이 립스틱 발색력이 정말 좋고..."` |
| `date` | str | 리뷰 기준일 (YYYY-MM-DD) | `"2026-03-31"` |
| `product_id` | str | 상품 고유 코드 (source/channel별 출처 상이 → 3절 참조) | `"1051234567"` |
| `prd_nm` | str | 상품명 | `"라네즈 워터뱅크 블루 히알루로닉 크림"` |
| `channel` | str | 채널 식별자 (own=숫자코드, extn/glb=사이트명) | `"031"`, `"navershopping"` |
| `brnd_nm` | str | 브랜드명 (**추가 예정**) | `"라네즈"` |
| `p_chain_inputs` | list[dict] | 문장별 BEE 태그 + 감성 집계 | (아래 상세) |
| `tokens` | list[str] | MeCab 형태소 토큰 (참조용) | `["이", "립스틱", ...]` |
| `ner_spans` | list[dict] | NER 개체명 인식 결과 (빈 리스트 가능) | (아래 상세) |
| `bee_spans` | list[dict] | BEE 속성 감성분석 결과 (빈 리스트 가능) | (아래 상세) |
| `relation` | list[dict] | 관계 추출 결과 (**추가 예정**) | (아래 상세) |

---

## 3. product_id 출처 (channel별)

| Source | Channel | product_id 출처 | Snowflake 컬럼 | 설명 |
|--------|---------|----------------|----------------|------|
| own | 031 (아모레퍼시픽) | ecp_onln_prd_srno | `t4.ecp_onln_prd_srno` | ECP 온라인 상품 시리얼넘버 (int→str) |
| own | 036 (이니스프리) | intg_onln_prd_cd_vl | `fprh.chn_prd_cd` | 통합 온라인 상품코드 |
| own | 039 (오설록) | intg_onln_prd_cd_vl | `fprh.chn_prd_cd` | 통합 온라인 상품코드 |
| own | 048 (아리따움) | intg_onln_prd_cd_vl | `fprh.chn_prd_cd` | 통합 온라인 상품코드 |
| own | 042, 099 | - | - | 현재 skip |
| extn | all | std_prd_cd | `rd_goods` | 표준 상품코드 |
| glb | all | std_prd_cd | `a.prod_nm` | 상품명이 코드로 사용 (주의) |

---

## 4. Channel 목록

### own (자사몰)

| Code | 플랫폼 |
|------|--------|
| 031 | 아모레퍼시픽 (AP mall) |
| 036 | 이니스프리 (Innisfree) |
| 039 | 오설록 (O'Sulloc) |
| 042 | 에스쁘아 (Espoir) — 현재 미처리 |
| 048 | 아리따움 (Aritaum) |
| 099 | 에스트라 (Aestura) — 현재 미처리 |

### extn (외부몰)

| Code | 플랫폼 |
|------|--------|
| navershopping | 네이버 쇼핑 |
| ssg | SSG (신세계) |
| oliveyoung | 올리브영 |
| kakao | 카카오 선물하기 |

### glb (글로벌)

| Code | 플랫폼 |
|------|--------|
| amazon | Amazon |
| sephora | Sephora |

---

## 5. own 전용 필드

| 필드 | 타입 | 설명 | 예시값 |
|------|------|------|--------|
| `age_sctn_cd` | str | 리뷰어 연령대 코드 | `"20"`, `"30"`, `"40"`, `"None"` |
| `sex_cd` | str | 리뷰어 성별 코드 | `"F"`, `"M"`, `"None"` |
| `sktp_nm` | str | 피부 타입 | `"건성"`, `"지성"`, `"복합성"`, `"중성"`, `"None"` |
| `sktr_nm` | str | 피부 고민 | `"건조"`, `"모공"`, `"주름"`, `"트러블"`, `"None"` |

---

## 6. extn/glb 전용 필드

| 필드 | 타입 | 설명 | 예시값 |
|------|------|------|--------|
| `rspn_sal_lcns_nm` | str | 책임판매업자명 | `"아모레퍼시픽 공식스토어"`, `"(주)이니스프리"`, `""` |

---

## 7. 중첩 필드 스키마

### 7-1. p_chain_inputs

Review Summary 모델의 입력. 최소 토큰 수(15개) 미만 문장은 필터링됨.

| 필드 | 타입 | 설명 |
|------|------|------|
| `text` | str | 문장 텍스트 |
| `start` | int | 문서 내 시작 위치 (char offset) |
| `end` | int | 문서 내 끝 위치 (char offset) |
| `entity_group_tags` | list[str] | 해당 문장에 포함된 BEE 속성 라벨 목록 |
| `sentence_sentiment` | str | 문장 내 BEE span 감성 집계 (`"긍정"`, `"부정"`, `"중립"`, `"복합"`) |

### 7-2. ner_spans

NER 개체명 인식 결과. 빈 리스트 `[]` 가능.

| 필드 | 타입 | 설명 |
|------|------|------|
| `text` | str | 개체명 텍스트 |
| `label` | str | NER 라벨 (아래 표 참조) |
| `start` | int | 문서 내 시작 위치 (char offset) |
| `end` | int | 문서 내 끝 위치 (char offset) |

**NER Label 종류 (5개)**

| Label | 설명 | 예시 |
|-------|------|------|
| AGE | 나이 표현 | `"30대"`, `"25살"` |
| CAPACITY | 용량/중량 | `"100ml"`, `"50g"` |
| BASE_COLOR | 색상 호수 | `"21호"`, `"21N1"` |
| BRAND | 브랜드명 | `"이니스프리"`, `"라네즈"` |
| CATEGORY | 제품 카테고리 | `"립스틱"`, `"선크림"` |

### 7-3. bee_spans

BEE(Beauty Experience Expression) 속성 기반 감성분석 결과. 빈 리스트 `[]` 가능.

| 필드 | 타입 | 설명 |
|------|------|------|
| `text` | str | BEE 표현 텍스트 (원문에서 추출) |
| `label` | str | BEE 속성 라벨 (아래 표 참조) |
| `start` | int | 문서 내 시작 위치 (char offset) |
| `end` | int | 문서 내 끝 위치 (char offset) |
| `sentiment` | str | 감성 (`"긍정"`, `"부정"`, `"중립"`, `"복합"`) |

**BEE Label 종류 (39개)**

제품속성 (34개):
`가루날림`, `광감`, `구성`, `단품_디자인`, `맛`, `무너짐`, `뭉침`, `밀착력`, `발림성`, `발색력`, `백탁현상`, `번짐`, `보습력`, `부작용/손상`, `사용감`, `색상`, `성분`, `세정력`, `용량`, `유통기한`, `제형`, `지속력`, `커버력`, `컬링/볼륨`, `패키지/용기_디자인`, `펄감`, `편리성`, `표현력`, `품질`, `향`, `활용성`, `효과`, `휴대성`, `흡수력`

서비스속성 (3개): `배송`, `서비스`, `판촉`

고객속성 (2개): `인지가격`, `충성도`

### 7-4. relation (추가 예정)

관계 추출 결과. 현재 파이프라인에 추가 중.

| 필드 | 타입 | 설명 |
|------|------|------|
| `subject` | dict | 주어 엔티티 `{word, entity_group, sentiment, start, end}` |
| `object` | dict | 목적어 엔티티 `{word, entity_group, sentiment, start, end}` |
| `relation` | str | 관계 타입 (65 canonical predicates) |
| `source_type` | str | 추출 소스 (`"NER-NER"`, `"NER-BeE"`) |

---

## 8. Sentiment 값

| 값 | 의미 | 코드 |
|----|------|------|
| `"긍정"` | 긍정적 | 1 |
| `"부정"` | 부정적 | 0 |
| `"중립"` | 중립적 | 99 |
| `"복합"` | 긍정+부정 혼재 | - (집계 시 긍정/부정 수 동일) |

---

## 9. Snowflake 원본 매핑

### 9-1. own source

| rs.jsonl 필드 | prepare 필드 | Snowflake 테이블.컬럼 | 설명 |
|--------------|-------------|---------------------|------|
| id | id | fprh.rv_srno | 리뷰 일련번호 (PK) |
| text | text (← comment) | fprh.rv_txt | 리뷰 원문 |
| date | date | fprh.stnd_ymd | 기준일자 |
| product_id (ch=031) | ecp_onln_prd_srno | t4.ecp_onln_prd_srno | ECP 온라인 상품 시리얼넘버 |
| product_id (ch=036,039,048) | intg_onln_prd_cd_vl | fprh.chn_prd_cd | 통합 온라인 상품코드 |
| prd_nm (ch=031) | ecp_onln_prd_nm | t4.ecp_onln_prd_nm | ECP 온라인 상품명 |
| prd_nm (ch=036,039,048) | chn_prd_nm | dcpm.chn_prd_nm | 채널 상품명 |
| channel | ch_cd | literal '031' 등 | 채널코드 |
| age_sctn_cd | age_sctn_cd | fprh.age_sctn_cd | 연령대 코드 |
| sex_cd | sex_cd | fprh.sex_cd | 성별 코드 |
| sktp_nm | sktp_nm | fprh.sktp_nm | 피부타입명 |
| sktr_nm | sktr_nm | fprh.sktr_nm | 피부고민명 |

**own Snowflake 테이블 약어**

| 약어 | 정식 테이블명 | 설명 |
|------|-------------|------|
| fprh | f_prd_rv_hist | 상품 리뷰 이력 (Fact) |
| dpam | d_prd_anl_mstr | 상품 분석 마스터 (Dimension) |
| dcpm | d_chn_prd_mstr | 채널 상품 마스터 (Dimension) |
| t4 | d_ecp_onln_prd_mstr | ECP 온라인 상품 마스터 (Dimension) |

**own 조인 관계**

```
fprh.prd_cd = dpam.prd_cd
fprh.chn_cd + fprh.chn_prd_cd = dcpm.chn_cd + dcpm.chn_prd_cd
dcpm.chn_cd + dcpm.ecp_onln_prd_srno = t4.chn_cd + t4.ecp_onln_prd_srno
```

**prepare에 있지만 rs.jsonl에 빠진 own 필드**

| prepare 필드 | Snowflake 컬럼 | 설명 | 상품 마스터 연동 |
|-------------|---------------|------|---------------|
| prd_cd | dpam.prd_cd | 표준 상품코드 | dpam PK |
| prd_nm (원본) | dpam.prd_nm | 표준 상품명 | dpam |
| intg_onln_prd_cd_vl (031) | fprh.chn_prd_cd | 채널상품코드 | dcpm PK |
| ecp_onln_prd_srno (036~) | t4.ecp_onln_prd_srno | ECP 상품 시리얼 | t4 PK |

### 9-2. extn source

| rs.jsonl 필드 | prepare 필드 | Snowflake 테이블.컬럼 | 설명 |
|--------------|-------------|---------------------|------|
| id | id | rd_seq | 리뷰 시퀀스 ID |
| text | text (← comment) | rd_content | 리뷰 원문 |
| date | date | stnd_ymd | 기준일자 |
| product_id | std_prd_cd | rd_goods | 상품코드 |
| prd_nm | std_prd_nm | rd_product_title | 상품 타이틀 |
| channel | site_nm | s_name (CASE 변환) | 사이트명 |
| rspn_sal_lcns_nm | rspn_sal_lcns_nm | rd_brand_name | 브랜드명 (책임판매업자) |

소스 테이블: `cdp.ext_cdpods.review_rsn_extn_raw`

### 9-3. glb source

| rs.jsonl 필드 | prepare 필드 | Snowflake 테이블.컬럼 | 설명 |
|--------------|-------------|---------------------|------|
| id | id | a.rv_id | 리뷰 ID |
| text | text (← comment) | a.rv_orgn_txt | 리뷰 원문 (원어) |
| date | date | a.partitionkey | 파티션키 (=날짜) |
| product_id | std_prd_cd | a.prod_nm | **상품명이 코드로 사용** (주의!) |
| prd_nm | std_prd_nm | a.prod_nm | 상품명 |
| channel | site_nm | a.clct_site_nm | 수집사이트명 |
| rspn_sal_lcns_nm | rspn_sal_lcns_nm | literal '' | 빈값 (glb에는 없음) |

소스 테이블: `cdp.ext_cdpods.rpa_glbl_site_rv a LEFT JOIN cdp.sf_cdpdw.d_prd_anl_mstr b`

---

## 10. 상품 마스터 연동 키

| Source | rs.jsonl 필드 | 연동 대상 테이블 | 조인 키 |
|--------|-------------|---------------|---------|
| own (031) | product_id | d_ecp_onln_prd_mstr | ecp_onln_prd_srno |
| own (036,039,048) | product_id | d_chn_prd_mstr | chn_prd_cd (+ chn_cd) |
| own (all) | (미포함, 추가 필요) | d_prd_anl_mstr | prd_cd |
| extn | product_id | review_rsn_extn_raw | rd_goods |
| glb | product_id | rpa_glbl_site_rv | prod_nm |

---

## 11. GraphRapping 매핑 관계

rs.jsonl → GraphRapping 변환 시 필드 매핑:

| rs.jsonl | GraphRapping (relation_loader) | 비고 |
|----------|-------------------------------|------|
| id | source_review_key | deterministic review_id 생성 키 |
| text | text | 리뷰 원문 |
| date | drup_dt → created_at | 이벤트 시간 |
| product_id | (product matching 입력) | ProductIndex로 매칭 |
| prd_nm | prod_nm | 상품명 기반 매칭 |
| channel | clct_site_nm | 수집 채널 |
| brnd_nm | brnd_nm | 브랜드명 |
| ner_spans | ner[] | entity_group 재매핑 필요 |
| bee_spans | bee[] | entity_group 재매핑 필요 |
| relation | relation[] | 추가 예정 |
| age_sctn_cd + sex_cd | author_key 또는 reviewer_proxy 속성 | 리뷰어 프록시에 부착 |
| sktp_nm + sktr_nm | reviewer_proxy 속성 | 리뷰어 피부 프로필 |

### NER label 매핑

| rs.jsonl label | GraphRapping entity_group | KG type |
|---------------|--------------------------|---------|
| AGE | AGE | AgeBand |
| CAPACITY | VOL | Volume |
| BASE_COLOR | COL | Color |
| BRAND | BRD | Brand |
| CATEGORY | CAT | Category |

### BEE label 매핑

rs.jsonl의 BEE `label` → GraphRapping의 `entity_group` (BEE_ATTR type):
- 39개 BEE label이 그대로 `bee_type`으로 사용
- 감성(sentiment): `"긍정"→"POS"`, `"부정"→"NEG"`, `"중립"→"NEU"`, `"복합"→"MIXED"`
