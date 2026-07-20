# 입력 커넥터 준비 계획 v2 — 원본 3소스 실로드 전환 대비 (IC 트랙)

날짜: 2026-07-19 · 상태: **v2**(코덱스 크로스리뷰 8건 전건 반영, 사용자 승인 대기) ·
근거: [10_service_transition_assessment.md](../10_service_transition_assessment.md) 갭 #3
발단(사용자 지시): "지금은 기존 데이터로 테스트하며 작업, 나중에 DB에서 원본
3개(유저·리뷰트리플·상품마스터)를 로드하는 형식으로 전환 — **지금 것 안
망가뜨리면서** 그 준비를 다 해둘 것."

## 0. 대원칙 (무파괴 계약)

1. **기본 경로 불변**: 기존 픽스처 경로·테스트·랭킹 스냅샷·데모 동작
   byte-identical. 커넥터는 전부 **독립 스크립트 + opt-in env**.
2. **staging은 git 밖**: `mockdata/real/{users,reviews,products}/` 하위
   (디렉토리 전체 .gitignore 기확립). 실데이터·자격증명 커밋 금지 규칙 그대로.
3. **소스 접근 미확정분과 준비 가능분 분리**: 리더 백엔드(S3/Snowflake/재추출)
   는 접근 확정 후 — 그 전에 인터페이스·검증기·landing·배선을 완성.
4. 5레이어 파이프라인·추천 로직의 의미 변경 없음. 단 **비파괴 파라미터 추가는
   허용**(full-load `review_format` 기본 "relation" — 코덱스 #2).

## 1. 사실 확인 (2026-07-19 실측/코드 확인 — 코덱스 정정 반영)

| 소스 | 확인 결과 | 귀결 |
|---|---|---|
| **리뷰 트리플** | **포맷이 2종 실존**: ① raw rs.jsonl(S3 산출 — `id/date/product_id/ner_spans/bee_spans` + top-level 인구통계, SCHEMA_RS_JSONL.md) ② relation JSON(현 픽스처 — `source_review_key/drup_dt/source_product_id/ner/bee/relation` + 중첩 `reviewer_profile`). **둘 다 로더가 이미 있음**: `rs_jsonl_loader`(raw→RawReviewRecord, NER 라벨·감성 정규화 포함)와 `relation_loader`(canonical) — 동일 출력 타입. 데모는 `review_format` 파라미터로 선택 가능(기본 relation), full-load는 relation 고정(파라미터 추가 필요) | 커넥터 = **rs.jsonl 원형 landing + 소비측 포맷 명시** — 변환 코드는 재사용, 신규 변환 작성 불요 |
| **상품 마스터** | 이미 실데이터 스냅샷 — 출처·재생성 절차 문서화([lineage](../../docs/architecture/v260605_906_fixture_lineage.md), [real snapshot](../../docs/architecture/product_master_real_snapshot_2026_06_16.md)). **주의: 골든 카탈로그 자체에 REP_CODE 비정상 6건 실재**(Z_Z×4·5자리 1·null 1 — 백필 때도 스킵 처리) | 커넥터 = 재추출 갱신 + **식별 3키·collision marker 검증** + diff 리포트. 9자리 규칙은 카탈로그 거부 조건이 아니라 **구매 조인 가능성 집계 리포트**로 한정(코덱스 #4) |
| **유저 프로파일** | PG 뷰 원본 로더 실존(`fetch_user_profiles_pg.py`) — 같은 뷰·같은 정규화기로 mock과 동일 형태 확인. 현 경로 가드는 `mockdata/real/` 직하만 허용(하위 디렉토리 거부됨 — 코덱스 #6) | 커넥터 = staging 모드 일반화(하위 디렉토리+매니페스트, 기존 기본 출력 계약은 호환 유지) |
| **배선 실사실** (코덱스 #5) | `FullLoadConfig`엔 review path(+optional product path)만 있고 **user path 없음**(CLI가 product/user JSON을 미리 읽어 객체 전달). 데모 리뷰엔 기존 env `GRAPHRAPPING_DEMO_REVIEW_PATH`가 있고 **import 시점 캡처** | 신규 env는 **호출 시점 해석**, 데모 우선순위 명문화(아래 §2) |

## 2. IC-0 — 공통 기반 (지금 전부 가능)

- **staging 규약**: `mockdata/real/{users,reviews,products}/` + 일자 스냅샷
  파일명(`*_YYYYMMDD.json(l)`) + **매니페스트**(`manifest.json`: 경로·포맷·
  건수·added/updated/unchanged/conflict·생성시각·검증 결과 — 원문 미포함,
  집계만). 하위 디렉토리에도 기존 보호장치 그대로(0700 디렉토리·0600 원자
  쓰기·symlink 거부 — 코덱스 #6).
- **입력 검증기** (`src/ingest/input_contracts.py` 신규) — **계약 분리**(코덱스 #1):
  - `RsJsonlSourceContract`: raw rs.jsonl(문서 스키마 기준 — id/date/product_id/
    ner_spans/bee_spans, channel별 product_id 규칙, top-level 인구통계, 추가
    예정 필드 nullable)
  - `RelationLandingContract`: 현 픽스처 형태(source_review_key/drup_dt/
    source_product_id/ner/bee/relation, 중첩 reviewer_profile)
  - 두 계약 간 필드 매핑 표를 코드 상수로 명시(문서↔픽스처 이름 차이의 단일 진실)
  - `ProductCatalogContract`: 식별 3키(source_channel/key_type/product_id)·
    collision marker·필수 컬럼. REP_CODE는 **비거부**(9자리 여부는 조인 가능성
    집계로 리포트만)
  - `UserProfileContract`: `{basic, purchase_analysis, chat}` + `purchase_events`
    (백필 테스트 재사용)
  - **골든 테스트**: 현 픽스처가 해당 계약을 통과(계약↔현실 정합 증명) —
    RsJsonlSource는 `review_rs_samples.json`(raw 20건, 저장소 실존 — 2026-07-20
    Fable 재검토에서 확인), RelationLanding은 `review_triples_raw.json`,
    Product는 `product_catalog_es.json`, User는 기존 유저 픽스처
- **opt-in env 2종 추가**: `GRAPHRAPPING_REVIEW_TRIPLES_JSON`,
  `GRAPHRAPPING_PRODUCT_CATALOG_JSON` — **호출 시점 해석**(import 캡처 금지),
  데모 우선순위 명문화: **명시적 request > 신규 커넥터 env > 기존
  `GRAPHRAPPING_DEMO_REVIEW_PATH`(리뷰만) > fixture 기본**. wide/dense 양쪽
  fixture로 테스트(코덱스 #5).
- **full-load `review_format` 파라미터**(기본 `"relation"` — 비파괴): 데모에만
  있던 rs_jsonl 선택을 full-load에도(코덱스 #2). 매니페스트의 포맷 필드와 짝.
- **CLI 구매 의미론 정합**(코덱스 #8): CLI/full-load 래퍼에서
  `extract_purchase_events_from_profiles(users)` 호출 →
  `FullLoadConfig.purchase_events_by_user` 전달(픽스처는 None → 기존 경로
  무변경). 실프로파일을 CLI full-load로 돌려도 brand-confidence 경로까지 정합.
- **테스트 env 격리**(코덱스 #7): conftest autouse에
  `GRAPHRAPPING_USER_PROFILES_JSON`·신규 2종·`GRAPHRAPPING_DEMO_REVIEW_PATH`
  클리어 추가. env unset/set/명시적-request-우선/missing-file 케이스 테스트.

## 3. IC-U — 유저 프로파일 커넥터 (기반 완성 → staging 일반화, S)

- `fetch_user_profiles_pg.py`에 **staging 모드**: `mockdata/real/users/` 일자
  스냅샷+매니페스트(경로 가드를 하위 디렉토리 허용으로 확장하되 보호장치
  동일 적용). **기존 기본 출력 경로는 호환 유지**(현 데모 env 문서와의 계약 —
  코덱스 #6).
- 재실행 = 스냅샷 갱신 semantics 문서화(뷰가 최신 상태의 단일 진실).
- 생성 후 `UserProfileContract` 자체 검증 → 매니페스트 기록.
- 최초 로드 규모 K: 상한 500 유지, 실제 K는 사용자 결정(§7).

## 4. IC-R — 리뷰 트리플 커넥터 (준비분 지금 / 리더는 접근 확정 후, M)

**지금 구현 (소스 접근 불요):**
- `scripts/fetch_review_triples.py` — `ReviewTripleReader` 인터페이스 + **파일
  백엔드**(로컬 rs.jsonl/relation JSON — 현 픽스처로 e2e). 처리 체인:
  포맷 감지/명시 → 해당 계약 검증 → **누적 스냅샷 landing**(코덱스 #3):
  기존 landing + 신규 입력을 `source_review_key`(relation) / `id`(rs.jsonl)
  기준으로 병합해 **전체 코퍼스 스냅샷을 원자적으로 재작성** — full-load/
  데모는 항상 전체를 소비하므로 "신규분만 파일" 금지. 매니페스트에
  added/updated/unchanged/**conflict** 기록. **동일 키·상이 payload는
  hard-fail**(침묵 제거 금지).
- **파일→DB 증분은 비스코프**(코덱스 #3): DB incremental은 `review_raw`
  테이블 cursor(updated_at/review_id)로 동작하며 중복 방어는 결정적
  review ID+idempotent upsert — landing은 "전체 스냅샷 공급"까지만 책임.
  직접 DB 적재 커넥터는 후속 트랙.
- 자격증명 env 규약 선정의(문서만): AWS 표준 env/프로파일, `SNOWFLAKE_*` —
  하드코딩 금지(백필 규칙 준용).
**접근 확정 후(IC-3):** S3(boto3) 또는 Snowflake(connector) 백엔드 택1 선구현.

## 5. IC-P — 상품 마스터 커넥터 (준비분 지금 / 재추출은 소스 확정 후, S~M)

**지금 구현:**
- `scripts/refresh_product_catalog.py` — 입력(신규 카탈로그 파일) →
  `ProductCatalogContract` 검증(3키·collision — REP_CODE는 집계 리포트) →
  **기존 대비 diff 리포트**(추가/삭제/변경 상품, 3키 충돌 변동, REP_CODE 변동
  → 유사도·구매 조인 영향 예고) → staging 기록+매니페스트. lineage 절차를
  docstring에서 참조.
**소스 확정 후(IC-3):** lineage 재추출 자동화 백엔드.

## 6. 테스트·게이트 (완료 기준)

- 계약 골든(픽스처 3종 통과) + 위반 거부(필드 결측·타입·동일키 상이 payload
  hard-fail·3키 위반) + 매핑 표 정합.
- 커넥터 e2e(파일 백엔드): 픽스처 → staging(누적 병합·원자성·매니페스트) →
  env 배선 → 기존 진입점 소비 성공(양 포맷).
- env 4종 격리 + unset/set/명시적 우선/missing-file. **미설정 시 기존 테스트
  무수정 통과 + 스냅샷 diff 0**(무파괴 증명).
- CLI 구매 정합: 실프로파일 CLI full-load에서 purchase_events가 양 경로
  (user facts + brand-confidence)에 도달, 픽스처는 무변경.
- 게이트: ruff/mypy/pytest **1318 기준 + 신규, 0 failed**.

## 7. 사용자 결정 필요

| # | 결정 | 기본 제안 |
|---|---|---|
| 1 | 리뷰 소스: **S3 vs Snowflake 중 먼저 붙일 것** + 접근 정보 시점 | 인터페이스 양쪽 커버, 백엔드는 확정 후 택1 |
| 2 | 상품 마스터 재추출 소스 확정 | 준비분(검증+diff) 먼저, 재추출은 확정 후 |
| 3 | 유저 최초 로드 규모 K | 상한 500 내 시작, 필요 시 상향 재검토 |

## 8. 비스코프

스케줄러(갭4) · 유사도 영속화+refresh 백그라운드화(갭1·2 — 독립 선행 가능) ·
retention(갭5) · **파일→DB 직접 증분 적재**(코덱스 #3 — 후속 트랙) ·
glb product_id 해소(갭6 — IC-R 검증기는 channel 규칙 검증만).

## 9. 시퀀싱

| 배치 | 내용 | 규모 |
|---|---|---|
| **IC-1** | IC-0 공통(계약 2+2종·staging 규약·env 배선·review_format·CLI 구매 정합·conftest 격리) + IC-U staging 일반화 | M |
| **IC-2** | IC-R 준비분(리더 IF+파일 백엔드+누적 landing) + IC-P 준비분(검증+diff) | M |
| **IC-3** | 접근 확정 후: 리뷰 백엔드 1개 + 상품 재추출 자동화 | M, 대기 |

각 배치 = Opus 구현 → Fable 리뷰 → 게이트 → 완료 보고.

## 10. 리스크·가드

| 리스크 | 가드 |
|---|---|
| 기존 데모/테스트 파손 | env 미설정 byte-identical + 스냅샷 diff 0 완료 기준 + conftest env 격리(#7) |
| 실데이터 유출 | staging 전체 git-ignore + 매니페스트 집계만 + 백필 프라이버시 규칙 준용 |
| 포맷 혼선(rs.jsonl↔relation) | 계약 2종 분리 + 매핑 표 단일 진실 + 매니페스트 포맷 필드 + 소비측 review_format 명시(#1·#2) |
| 부분 코퍼스 공급으로 집계 왜곡 | 누적 스냅샷 landing 원칙 + conflict hard-fail(#3) |
| 카탈로그 비정상 REP_CODE로 골든 파손 | 9자리 = 집계 리포트만, 거부는 3키·collision 위반만(#4) |
| env import-시점 캡처 | 신규 env 호출 시점 해석 강제 + 테스트(#5) |

## 검수 기록

### 코덱스 크로스리뷰 — 2026-07-19, APPROVE-WITH-CHANGES(8건) → v2 전건 반영
#1 리뷰 계약 2종 분리+매핑 표 · #2 "shape 어댑터 불요" 정정(rs_jsonl_loader
실존 — 원형 landing+포맷 명시+full-load review_format 비파괴 추가) ·
#3 누적 스냅샷 landing(신규분만 공급 금지·conflict hard-fail·파일→DB 증분
비스코프) · #4 9자리=집계 리포트(골든에 위반 6건 실재) · #5 배선 사실 정정
(FullLoadConfig user path 없음·기존 데모 리뷰 env·호출 시점 해석·우선순위
명문화) · #6 유저 staging 하위 디렉토리 호환(기존 경로 계약 유지) ·
#7 conftest env 4종 격리 · #8 CLI purchase_events 정합. 메인 세션이 #2·#5
근거(rs_jsonl_loader docstring·GRAPHRAPPING_DEMO_REVIEW_PATH import 캡처)를
코드로 재검증 후 수용.

## 완료 보고

_(실행 후 누적)_

## 완료 보고

### IC-1 완료 — 2026-07-20 (Opus 구현, Fable 검토 승인, 커밋 391034d)
계약 검증기 4종(+RS↔Relation 매핑 표, 로더 드리프트 방지 테스트) — 골든 4종
전부 무수정 통과(rs 20/20·relation 906/906·카탈로그 517/517[REP_CODE 비정상
6건은 joinability 집계로]·유저 50/50). staging 공용 모듈(백필 보호 로직 단일화),
env 2종 호출시점 배선(우선순위: request > 신규 env > legacy DEMO_REVIEW_PATH >
fixture — request 기본값 None화는 우선순위 구현의 필수 변경으로 Fable 승인),
full-load `review_format`(기본 relation 비파괴), CLI purchase_events 정합,
conftest 4종 격리, 유저 커넥터 --staging 모드(기존 경로 호환).
게이트 1370/0 failed, 스냅샷·픽스처 diff 0.

### IC-2 완료 — 2026-07-20 (Opus 구현, Fable 검토 승인)
- 리뷰 커넥터 `scripts/fetch_review_triples.py`: ReviewTripleReader IF + File
  백엔드(.json/.jsonl/디렉토리) + **누적 스냅샷 landing**(매니페스트 포인터 체인,
  전체 코퍼스 원자 재작성, added/updated/unchanged/conflict 실기록, 동일 키
  상이 payload는 canonical JSON 대조로 **기본 hard-fail**·--allow-updates 옵트인,
  포맷 혼합 중단, reject_rate>10% 전체 중단).
- 상품 커넥터 `scripts/refresh_product_catalog.py`: 계약 검증 + baseline diff
  (added/removed/changed_by_field/신규 3키 충돌/joinability delta — 집계+상위 N
  SKU **id만**, 원문 미노출) + 전량 스냅샷 교체 의미론(재추출=전체 진실; 누적
  병합은 리뷰만 — 부분 코퍼스 왜곡 방지 구분).
- **e2e 증명 3건**: rs 20건 landing→env→데모 pipeline_run(review_format=
  rs_jsonl) 소비 / rs→full-load / relation 906건 전량 landing→full-load. 전부 PASS.
- 게이트 **1401 passed, 50 skipped, 0 failed**(+31), 무파괴 diff 0.

### IC 트랙 상태: 준비분(IC-1·IC-2) 완료 → IC-3 사용자 확정 반영 (2026-07-20)

§7 결정 확정(사용자):
1. **상품 마스터 = ES9** — 접속 방식은 recommend-agent에서 확인(Fable 정찰):
   REST `{ES_CLOUD_URL}/{index}/_search` + `Authorization: ApiKey {ES_CLOUD_KEY}`,
   인덱스 `ES_AMORE_INDEX`/`ES_INNI_INDEX` 2종. ES 문서 필드가 카탈로그 컬럼과
   동일 네이밍(BRAND_NAME/CTGR_SS_NAME…) — 픽스처의 원 출처로 확정. → IC-3에서
   ES 재추출 백엔드 구현(전량 export → 기존 refresh_product_catalog 체인).
2. **env 일원화** — GraphRapping 자체 `.env`(git-ignore)로 DB 관련 환경변수
   전부 관리(`.env.example` 커밋): ES 3종+KEY, 유저 Azure PG(AIBE_DB_* — 기존
   personal-agent .env 경로 참조는 fallback으로 유지=무파괴 이관), 기존
   GRAPHRAPPING_DATABASE_URL 등, 미래 SNOWFLAKE_*/AWS 규약 주석. 로딩은
   **명시적 opt-in**(스크립트/서버 기동 시 호출, 이미 설정된 os.environ 우선 —
   테스트는 .env 미로드로 격리 유지).
3. **리뷰 소스 = 대기 확정** — inference-gerter 정찰: SageMaker 배치
   (gerter_ner_step → gerter_bee_step, relation 스텝은 합류 예정 — 사용자 확인),
   산출은 Snowflake NER/BEE 테이블(`SNF_{OWN,EXTN,GLOBAL}_NER_TBL_NAME`) +
   S3 TransformOutput 양쪽. relation 합류 후 최종 rs.jsonl 형태/위치 확정 시
   백엔드 구현(인터페이스는 IC-2에서 준비 완료).
4. **유저 최초 로드 K=100** (상한 500 내).

### IC-3(가용분) 완료 — 2026-07-20 (Opus 구현, Fable 검토·실값 이관·라이브 스모크)
- **.env 일원화**: `src/common/env_file.py`(의존성 0, os.environ 우선, opt-in 호출만)
  + `.env.example` 커밋(플레이스홀더) + `.env` git-ignore. 실값 이관은 메인 세션이
  수행(recommend-agent ES 4종 + personal-agent AIBE_DB 6종 → GraphRapping `.env`,
  0600, 값 미출력). 서버 .env 로드는 보류(데모/테스트 오염 방지 — DECISIONS).
- **상품 ES 백엔드**: `scripts/fetch_product_catalog_es.py` — search_after
  (ONLINE_PROD_SERIAL_NUMBER,_doc 정렬, PIT/scroll 없이 stateless) 전량 export →
  기존 refresh_product_catalog 체인 재사용. **라이브 스모크(Fable)**: AMORE
  33,963 + INNI 11,307 = **실상품 ~45k 확인**, 정렬키 실 매핑 정상(override 불요).
- **유저 이관+K=100**: 자격증명 해석 GraphRapping env 우선→personal-agent
  fallback(무파괴), DEFAULT_LIMIT=100. **라이브 스모크(Fable)**: K=100 staging
  최초 로드 성공(매칭률 49.2%, owned families/user 평균 2.04,
  `mockdata/real/users/user_profiles_real_20260720.json` 0600+매니페스트).
- 게이트 **1431 passed, 50 skipped, 0 failed**(+30), 무파괴 diff 0.
- **잔여 = 리뷰 백엔드만**(대기 확정): inference-gerter relation 스텝 합류 후
  rs.jsonl 최종 형태/위치 확정 시 `ReviewTripleReader` 백엔드 1개 구현.
- **규모 참고**: 실 카탈로그 ~45k는 데모 517의 87배 — 전환 시 유사도 사전 계산은
  배치 분리(갭 1·2, A3 수용 판정의 전제) 선행 권장.
