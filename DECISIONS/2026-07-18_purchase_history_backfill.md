# 구매이력 백필 — 실유저 프로파일로 owned 엣지 공급 (fable_doc §C1)

## 결정

Azure PG 개인화 뷰(`agent.aibe_user_context_mstr_v`)에서 대표상품코드를 보유한
실유저 행을 조회 → 가명화 정규화 프로파일에 **구매 이벤트를 동봉** → 데모 유저
평면에 OWNS_PRODUCT/OWNS_FAMILY 엣지를 공급한다. 소비는 **기존
`derive_purchase_features` 경로**(신규 어댑터 표면 없음), 활성화는 **opt-in
env**로 하여 기본 경로/랭킹 스냅샷을 무변경으로 유지한다.

- **조인 키 = 대표상품코드**: DB `rprs_prd_cd`(9자리) == 카탈로그
  `REPRESENTATIVE_PROD_CODE` == 서빙 `variant_family_id`. `^[0-9]{9}$` 형식을
  카탈로그 인덱싱·프로파일 추출 양쪽에 강제(불합격 코드는 집계 드롭 카운트).
- **매칭률(실측, 크로스리뷰 후 재생성)**: 50행 라이브 샘플에서 distinct 코드
  102종 중 54종 매칭(52.9%). (스펙 기준 300행 샘플 42%와 동일 계열 — 표본 차이.)
- **소비 경로**: 프로파일 top-level `purchase_events` → `load_users_from_profiles(
  purchase_events_by_user=...)` → `derive_purchase_features` →
  `purchase_features_to_adapter_dict` → `adapt_user_profile` → OWNS_* fact →
  `build_serving_user_profile`의 `owned_product_ids`/`owned_family_ids` →
  서버 `build_similar_boost_index` → 스코어러 `similar_product_affinity`(G4).

## 근거

- **대표상품코드가 유일한 실사용 조인 키**: 개인화 뷰의 구매 요약에는
  `rprs_prd_cd`가 실재하나 5자리 시리얼(`prd_cd`)과 카탈로그 교집합은 0.
  대표코드는 카탈로그 `REPRESENTATIVE_PROD_CODE`(= `variant_family_id`)와 직접
  일치하므로, 대표코드 기반 해소가 유일하게 성립하는 방식.
- **정규화기가 코드를 버림**: 개인화 `_normalize_profile`의
  `_normalize_product_summary`는 `rprs_prd_nm`/날짜만 유지하고 `rprs_prd_cd`를
  드롭한다. 따라서 코드는 **원본 JSON 컬럼**(purchase_profile /
  repurchase_category_affinity / seasonal_affinity)에서 직접 추출한다. 정규화
  자체(basic/purchase_analysis/chat 생성)는 개인화기를 그대로 재사용.
- **event 경로 채택(summary 경로 아님)**: fable_doc §C1이 명시한 정본은
  `derive_purchase_features`(user_loader.py). summary 경로
  (`derive_purchase_summary_features`)는 대표코드를 인덱싱하지 않아 코드 기반
  해소가 불가하고, 스펙이 "새 어댑터 표면 발명 금지"를 요구하므로 event 경로가
  정합.

## 이벤트 의미론 — 발생(occurrence) 기반 (크로스리뷰 P0-3 반영)

**구매 발생 1회 = PurchaseEvent 1건.** 발생 := 원본 요약에서 추출한 distinct
(대표코드, 구매일) 쌍 — 날짜가 전혀 없는 코드는 무일자 발생 1회로 취급.
이벤트의 `product_id`는 **결정적 대표 멤버**(패밀리 멤버 SKU 정렬 첫 항목).

- **멤버 SKU 전개 기각(초기안 폐기)**: 최초 구현은 대표코드당 모든 멤버 SKU를
  이벤트로 전개했으나, `derive_purchase_features`가 이벤트 수로
  `family_purchase_count`를 집계하므로 **멤버 2개 패밀리의 단일 구매가
  REPURCHASES_FAMILY/REPURCHASES_BRAND를 조작 생성**하는 정합성 버그(코덱스
  크로스리뷰 P0-3). 수정 후 단위테스트로 양방향 보증: "다중 멤버 패밀리 1회
  발생 → 재구매 fact 미생성", "동일 패밀리 2회 발생(distinct 날짜 2개) → 재구매
  fact 정상 생성".
- 패밀리 수준 소유 커버리지는 기존 경로(`family_lookup` → OWNS_FAMILY)가 담당
  — 이벤트가 대표 멤버 1개만 참조해도 OWNS_FAMILY는 패밀리 전체 진실을 표현.
- 동일 코드가 두 요약 소스에 같은 날짜로 등장하면 1건으로 dedup(동일 구매의
  중복 반영일 가능성이 높음 — 과대계상 방지 쪽으로 보수적).
- 참고: 기존 summary 경로(`derive_purchase_summary_features`)는 개인화
  에이전트가 스스로 "재구매 요약"으로 분류한 항목을 이름 기반으로 해소해
  repurchased_family를 표시할 수 있다. 이는 이 백필 이전부터 있던 별도
  메커니즘(에이전트 자체 판정의 소비)으로, 본 결정의 이벤트 조작 버그와 무관.

## 프라이버시 규칙 (필수)

- **가명화**: user_id = `real_{incs_no 앞 12자}` (incs_no는 이미 해시된 값).
  incs_no 결측/공백/12자 미만 행은 스킵+카운트. 서로 다른 incs_no가 같은 12자
  프리픽스를 가지면(가명 충돌) **전체 중단(abort)** — 두 유저의 무음 병합 방지.
- **출력 디렉토리 격리**: 출력은 전용 git-ignored 디렉토리
  `mockdata/real/`로만 제한. `.gitignore`는 파일명이 아닌 **디렉토리 전체**
  (`mockdata/real/`)를 등재해 파일명 변형 누출을 방어. `--output`은 해당
  디렉토리 내부만 허용(외부 경로·`..` 탈출·symlink 거부), 쓰기는 원자적
  (temp→rename) + 파일모드 0600, 디렉토리 0700.
- **행수준 데이터 비노출**: 스크립트 stdout·보고서·결정문서는 **집계만**
  (가명 user_id + 상품 + owned SKU 조합의 행수준 샘플 금지). 예시가 필요하면
  명시적 합성 id(`synthetic_example_user` 등)만 사용.
- **자격증명 경로 참조**: 개인화 에이전트 `.env`(AIBE_DB_*)를 런타임에 경로로만
  읽음. 리포 복사·로그 출력 없음.
- **읽기전용 조회**: SELECT-only + `transaction(readonly=True)`,
  `ORDER BY incs_no LIMIT $1`(결정적), `--limit` 상한 500 강제(argparse 거부).
  라이브 DB이므로 CI 미실행 — 단위테스트는 목 행.

## DB 연결 하드닝 (크로스리뷰 P1-5)

- **TLS**: verify-full 의미론을 `ssl.create_default_context()`(CA 번들 신뢰 +
  호스트명 검증)로 시도 — asyncpg의 문자열 `"verify-full"`은
  `~/.postgresql/root.crt`를 요구(libpq 관례)해 이 호스트 구성과 불일치.
  체인 검증 실패 시 `ssl='require'`(암호화, 체인 미검증) 폴백 + stderr 명시.
  **실측: verify-full 성공** (2026-07-18 재생성 런, 폴백 미발동).
- connect timeout 30s, command_timeout 60s 명시.

## opt-in 설계 사유 (스냅샷 재승인 불요)

- env `GRAPHRAPPING_USER_PROFILES_JSON` 설정 시에만 데모 로드가 실프로파일
  파일을 기본 유저 파일 대신 사용. 미설정 시 기존 fixture와 byte-identical.
- 프로파일에 `purchase_events` 키가 없으면 추출이 `None`을 반환 → 기존
  `purchase_events_by_user=None`과 동일. 합성 fixture 경로 무변경(기존 테스트
  무수정 통과 + 스냅샷 diff 0이 증거).
- 따라서 랭킹 스냅샷·골든은 재승인 불요 — 실프로파일은 순수 opt-in.

## 인증 미구현 결정 (코덱스 P0-3 제기 → 메인 세션 기각)

코덱스 크로스리뷰는 실프로파일 모드에 대한 접근 인증 추가를 제기했으나, 메인
세션이 **기각**: 루프백 바인딩 + 가명화된 로컬 데모에 인증 레이어는 과설계.
대신 완화책 2건을 채택:
- (a) 실프로파일 모드 적재 시 경고 로그 1줄 — "real pseudonymized profiles
  loaded — keep server loopback-bound; do not expose publicly" (server.py
  pipeline_run, env override가 실제 사용된 경우에만).
- (b) **운영 제약(본 문서)**: 실프로파일 모드의 서버는 반드시
  127.0.0.1 바인딩으로만 기동하고 공개 네트워크에 노출하지 않는다. 공개 노출이
  필요해지면 그 시점에 인증을 별도 결정으로 설계한다(현 결정의 범위 밖).

## 적재 경로 중앙화 (크로스리뷰 P1-9)

`load_users_from_profiles`에 fallback 추가: 호출자가 `purchase_events_by_user`
를 넘기지 않으면(None) 프로파일 동봉 `purchase_events`를 자동 추출. 위치 선정
근거: 이 함수가 `run_full_load`·`load_demo_data` 양쪽이 통과하는 단일 관문
(진짜 중앙화)이고 OWNS fact 생성이 백필의 핵심 산출물. 경계: run_batch의
brand-confidence 가중(별도 계약)은 여전히 명시 전달 필요 — 데모 경로는
server.py가 명시 추출해 양쪽에 공급, full_load는 `config.purchase_events_by_user`
1급 필드로 기지원. `run_906_full_load_db.py`는 무접촉. 표준 픽스처(키 없음)는
fallback이 None을 반환해 byte-identical(기존 테스트 무수정 통과 + full-load
회귀 테스트 1건 추가).

## 동봉 이벤트 경계 계약 (크로스리뷰 P1-10 — 침묵 보정 금지)

`extract_purchase_events_from_profiles`:
- 항목이 mapping이 아니거나 `product_id` 결측/공백 → **이벤트 스킵**+카운트.
- `quantity` 부재 → 1(문서화된 기본값). 존재하나 양의 정수가 아님(bool 배제,
  0/음수/비정수/float) → **이벤트 스킵**+카운트 — quantity는 재구매 집계에
  들어가므로 값 조작이 이벤트 드롭보다 위험.
- `purchased_at`/`channel`이 str 아님, `price`가 숫자 아님 → **해당 필드만
  None**(이벤트 유지) — 소유 진실은 상품 참조이고 이들은 보조 메타데이터.
- 스킵/무효화 건수는 `logger.warning`으로 집계 표면화(침묵 없음).

## 배선 지점

- `src/loaders/user_loader.py`: `extract_purchase_events_from_profiles()`(경계
  계약 포함) + `load_users_from_profiles` fallback.
- `src/web/server.py`: `_resolve_user_default_path()`(env override) +
  pipeline_run에서 추출→`load_demo_data(purchase_events_by_user=)` 전달(run_batch
  brand-confidence 포함 양 계약 공급) + 실프로파일 모드 경고 로그.
- `run_full_load`: `config.purchase_events_by_user` 1급 필드 + loader fallback.

---

## 완료 보고서 — 1차 (2026-07-18)

최초 구현: 조회 스크립트/opt-in 배선/17 테스트/실측(G4 발화 확인). 전체 게이트
통과(1304 passed). 이후 코덱스 크로스리뷰에서 P0 4건·P1 6건 지적 → 2차 반영.

## 완료 보고서 — 2차 (크로스리뷰 반영, 2026-07-18)

### 변경 파일
- `scripts/fetch_user_profiles_pg.py`: 발생 기반 이벤트(대표 멤버 앵커),
  9자리 코드 강제(양쪽), 가명 검증/충돌 abort, 출력 경로 격리(원자적 0600),
  TLS verify-full(+require 폴백), readonly tx, timeout, limit≤500, stdout
  집계 전용(행수준 샘플 제거).
- `src/loaders/user_loader.py`: 경계 계약(P1-10) + 중앙화 fallback(P1-9).
- `src/web/server.py`: logger 신설 + 실프로파일 모드 경고 로그(P0-4).
- `.gitignore`: `mockdata/real/` 디렉토리 전체 등재(파일명 변형 방어).
  구 파일 `mockdata/user_profiles_real_normalized.json`은 새 위치로 이동.
- `tests/test_purchase_backfill.py`: 17 → **31 케이스**(오염 보증 2건 의무 포함,
  full-load 회귀, 경로 격리, 충돌 abort, 쿼리 3소스, 경계 계약).
- 본 문서 갱신(행수준 샘플 제거, 정책 갱신).

### 조회·해소 통계 (K=50 라이브 재생성, 집계만)
- ssl=**verify-full**(폴백 미발동), rows=50, invalid incs 스킵=0, users=50.
- **users_with_owned_edges=39**, total_owned_families=70, anchor SKUs=70,
  **total_purchase_events=73**(distinct 날짜 2개 이상 코드 3건).
- distinct 코드: seen=102, matched=54, unmatched 드롭=48, 형식 불합격=0,
  **매칭률 52.9%**. owned families/user: min1 max8 mean1.79.
- 카탈로그: 517레코드 → 유효 대표코드 381종/멤버 511(형식 불합격·결측 6레코드
  스킵). (1차 대비 seen 100→102 등 소폭 차이는 라이브 뷰 데이터 변동.)

### G4/D1 실발화 재실측 (스크래치 :8128, 라이브 :8123 무접촉, 집계만)
- 파이프라인: reviews=906, products=517, users=50, signals=3248.
- **G4: owned 39유저 전원(39/39) 발화** (1차 37/38). top-20 내 boosted 결과
  167건, `similar_product_affinity` min 0.0016 / max 0.02 / mean 0.0113.
  (발생 기반 수정 후 앵커는 대표 멤버로 줄었지만 발화율은 오히려 완전 커버.)
- 서빙 owned 엣지: OWNS_PRODUCT 70 / OWNS_FAMILY 70.
- **D1**: `collaborative_product_ids` 비어있음 50/50 —
  `attach_collaborative_signals` 콜사이트 0(스코프 밖, 데이터만 준비 완료).
- G2/G3/대시보드 무영향: G3 total=5, G2 nodes=6, 대시보드 정상.
- 실프로파일 모드 경고 로그 발화 확인(서버 로그 실측).

### 검증 게이트 (2차)
- ruff: All checks passed. mypy src: Success(117 files). mypy 스크립트: Success.
- pytest: **1318 passed, 50 skipped, 0 failed** (baseline 1287 + 신규 31).
- `git diff --stat tests/fixtures/ mockdata/`: 빈 출력(골든·스냅샷 무변경).
- git status에 실데이터 미등장, `mockdata/real/` 임의 파일명도 ignore 확인.
- 출력 파일 0600 / 디렉토리 0700 실측.

### 잔여 follow-up
- D1 발화: `attach_collaborative_signals` 콜사이트 1개(서버 로드 후) — 별도 스코프.
- 매칭률 개선: 미매칭 48종은 데모 카탈로그(517 SKU) 커버리지 한계. 풀 카탈로그
  적재 시 상승 기대.
