# 03. 개선 계획 상세

작성일: 2026-07-07 · 상태: 제안 (사용자 승인 대기) · 크로스 리뷰 반영 완료(04 참조)

## 0. 우선순위 원칙

fixture 단계 pre-production에서 가장 큰 리스크는 두 가지다:

1. **추천 품질을 어떻게 판단할지가 없다** — 이후 모든 개선(가중치, semantic rule,
   임베딩, 그래프)의 효과를 측정할 수 없으면 개선 자체가 불가능
2. **identity / scope / evidence 계약이 조용히 깨질 수 있다** — 깨져도 겉보기에
   그럴듯한 결과가 나오므로 눈검사로 못 잡음

반면 운영 확장(retention 구현, 파티셔닝, 대규모 부하검증)은 실데이터 연속 적재가
시작될 때의 리스크이므로 **정책 결정/설계만 선행**하고 구현은 백로그로 둔다.

따라서: **측정 기준선(0) → 계약 방어(1) → 서빙 경로 정리(2) → 시맨틱 규칙 강화(3)
→ 그래프/검색 실체화(4) → 백로그(5)**. Phase 0과 1은 독립적이라 병렬 착수 가능.

각 Phase 실행 시 CLAUDE.md 필수 사이클(계획 → 구현 → 1차 검수[codex 크로스리뷰]
→ 수정 → 2차 검수 → 완료 보고서)을 Phase 단위로 적용한다.

## Phase 0 — 품질/계약 기준선 (1~2주) ★최우선

기존 자산([audit_recommendation_evidence.py](../scripts/audit_recommendation_evidence.py),
tests/test_golden_profile_recommendation_audit.py, dense golden fixture)의 **확장**이지
신규 구축이 아니다.

### 0.1 골든 프로파일별 expected evidence-family assertion

- **배경**: 현재 audit는 evidence-family 분포를 "보여주는" 수준. 어떤 분포가
  정상인지의 기대값이 코드에 고정되어 있지 않음 (이슈 C1)
- **작업**: 6개 골든 프로파일 × 카테고리 탭별로 (a) 기대 evidence family 조합
  (b) no-candidate가 허용되는 조건 (c) source-stats-only 추천 금지를 assertion으로
  고정. dense golden fixture 기준
- **완료 기준**: 기대셋 위반 시 CI fail. "이 유저의 skincare 탭 추천에
  PRODUCT_MASTER_TRUTH 계열 근거가 하나도 없으면 실패" 수준의 구체성
- **의존성**: 없음 (즉시 착수 가능)

### 0.2 개인화 계약 회귀 테스트 (→ 이슈 B3)

- **배경**: [recommendation_signal_flow_2026_06_23.md](../docs/architecture/recommendation_signal_flow_2026_06_23.md)
  말미의 "검토 체크리스트"가 수동 검토용으로만 존재. 계약이 깨지면 잘못된 개인화가
  조용히 발생
- **작업**: 체크리스트 전 항목을 자동 테스트로 전환:
  - `ACTIVE_IN_CATEGORY`가 eligibility/`PREFERS_CATEGORY`/master_truth_score로
    승격되면 fail
  - scoped preference(makeup 전용 키워드 등)가 타 카테고리 점수에 새면 fail
  - `AVOIDS_INGREDIENT`가 hard filter가 아니라 감점으로만 동작하면 fail
  - `source_review_*`가 단독 eligibility 근거가 되면 fail
  - `review_summary_sidecar`가 후보/점수에 개입하면 fail
- **완료 기준**: 체크리스트 항목 전체가 테스트로 존재하고 CI에 포함
- **의존성**: 없음

### 0.3 랭킹 스냅샷 회귀

- **배경**: 스코어링 변경(가중치/규칙)이 순위에 미치는 영향을 지금은 사람이
  프론트에서 눈으로 확인 (이슈 C1, C2)
- **작업**: 골든 프로파일 top-N 결과(순위 + score layer 분해 + evidence)를
  스냅샷 파일로 고정, 변경 시 diff 리포트 자동 생성. 의도된 변경은 스냅샷 갱신으로
  승인하는 워크플로우
- **완료 기준**: scoring_weights.yaml 값 하나를 바꾸면 어떤 유저·상품 순위가 어떻게
  변하는지 diff로 보임
- **의존성**: 0.1과 같은 fixture 사용

### 0.4 Provenance explainer 완성 (→ 이슈 C3)

- **배경**: ProvenanceExplanationPath(리뷰 스니펫/fact_ids/review_ids)가
  [explainer.py](../src/rec/explainer.py)에 자료구조만 정의됨. 추천 근거의 원문
  확인이 안 되면 품질 판정 속도가 느림
- **작업**: signal_evidence → canonical_fact → fact_provenance → raw 체인을 따라
  추천 사유에 리뷰 스니펫을 연결하는 async provider 구현. 데모 UI 노출
- **완료 기준**: 추천 결과의 각 근거 경로에서 원문 리뷰 스니펫 확인 가능
- **의존성**: DB 조회 필요 → Phase 2.1(mart reader)과 코드 공유 가능하나 선행 불필요
  (파이프라인 실행 시점 자료로도 구현 가능)

### 0.5 랭킹 메트릭 도입 준비 — ground-truth 라벨 전략 결정 ⚠️사용자 결정

- **배경**: NDCG/precision류는 "정답 상품셋" 없이는 무의미. 현재 정답 라벨이 없음
- **작업**: 라벨 확보 방법 결정 — (a) 사내 도메인 평가자가 골든 프로파일별 기대
  상품셋 라벨링 (b) 구매 이력 기반 proxy(구매/재구매를 정답으로) (c) 혼합.
  결정을 DECISIONS로 기록 후 하네스에 지표 추가
- **완료 기준**: 라벨 전략 DECISIONS 기록. **결정 전 NDCG류 구현은 보류**
- **의존성**: 사용자/도메인 조직 결정 필요
- **상태 (2026-07-10)**: **사용자 숙고 보류.** 영향 분석 결과 블로킹 없음 —
  막히는 것은 NDCG류 구현 자체와 Phase 5 "임베딩 recall 채널 실험"의 채택
  게이트뿐이고, 그 외 어떤 작업/백로그도 이 결정에 의존하지 않음.
  **재상정 트리거**: 체계적 가중치 튜닝 라운드 개시 또는 모델 변형 비교 필요 시.
  추천안(참고): (c) 혼합 — (a) 평가자 소규모(골든 6프로파일×주요 탭, top-30
  후보 풀 관련성 판정)로 시작, 실데이터 적재 후 (b) 구매 proxy 교차검증

### 0.6 데모 UI 리뷰 스니펫 노출 (추가 2026-07-10 — 0.4 잔여 완결) — ✅ 반영

- **배경**: 5차 갭 감사 — API는 `explanation_paths[].snippets`를 반환하나
  (src/web/server.py) 데모 프론트(static/app.js 설명경로 카드)가 스니펫을
  렌더링하지 않아 0.4 완료 기준("데모 UI 노출")의 UI 측이 미달. 현재는
  curl/API로만 원문 확인 가능
- **작업**: 설명경로 카드에 스니펫 접기/펼치기 + review_id 표시. 프론트 전용,
  API 계약 변경 없음
- **완료 기준**: 데모 UI에서 추천 근거 경로별 원문 리뷰 스니펫 확인 가능
- **반영 (2026-07-10 Batch 1, Sonnet)**: app.js `renderPathSnippets()` +
  경로별 `<details>` 접기/펼치기(+18줄), app.css +9줄. 전 출력이 기존
  `displayText`(escapeHtml) 경유 — XSS 페이로드 어서션으로 실측. API 무변경,
  `{review_id, text}` 계약 TestClient 실측. Fable 리뷰 승인

## Phase 1 — identity/데이터 계약 방어 (1~2주, Phase 0과 병렬 가능)

### 1.1 source identity collision 처리 일관화 (→ 이슈 B1)

- **배경**: collision 감지가 로더별 수동 계약에 의존. 실데이터/채널 확대 시
  신규 collision이 조용히 유입될 수 있고, downstream(AmoreSimulation) 오염 위험
- **작업**: collision 감지를 [contract_validator.py](../src/db/contract_validator.py)
  공용 검증으로 이동. 파이프라인 적재 시 신규 collision 자동 검출 → 경고 +
  `SOURCE_KEY_COLLISION` 마킹 + clean join 자동 제외를 일관 적용. collision 수를
  pipeline_run 카운터에 노출
- **완료 기준**: fixture에 인위적 collision을 추가하면 파이프라인이 자동 검출·격리하고
  validator가 리포트
- **의존성**: 없음

### 1.2 glb 채널 identity 전략 사전 결정 (→ 이슈 B2) ⚠️결정 기록 필요

- **배경**: glb는 상품명이 product_id. 온보딩 후 고치려면 재적재 비용이 큼 —
  **온보딩 전에** 결정해야 하는 사안
- **작업**: 대안 검토 후 DECISIONS 기록 — (a) glb 전용 source_key_type 신설 +
  상품명 정규화 키 (b) 상품마스터 매칭 성공분만 수용 (c) glb 보류.
  구현은 glb 온보딩 시점(Phase 5)
- **완료 기준**: DECISIONS 문서 1건
- **의존성**: 없음 (결정만)
- **✅ 확정 (2026-07-10, 사용자 승인)**: **D안** — B(기존 상품마스터 매칭
  성공분만 수용, 미매칭 quarantine)를 기본 전략으로 + A안은 실측 수요 시
  조건부 재검토. key_type 마커 명칭 `name_hint` 채택. 상세:
  DECISIONS/2026-07-08_glb_channel_identity_strategy.md 확정 기록.
  → Phase 5 glb 온보딩 착수 조건 충족

### 1.3 운영 최소 안전장치 — retention은 설계까지만 (→ 이슈 F1) ⚠️사용자 결정

- **배경**: 무한 누적 3종은 실데이터 연속 적재 전까지 휴면. fixture 단계에서
  파티셔닝까지 구현하는 것은 과잉(크로스 리뷰 지적 #5)
- **작업**:
  - quarantine 5종 / agg_product_signal(window별) / ner·bee·rel_raw row count
    모니터링 쿼리 + 임계 경고 (consumer contract §12.4의 지표를 실행 가능하게)
  - cleanup 기본 정책 결정: `GRAPHRAPPING_AGG_CLEANUP_ENABLED` opt-in 유지 여부
    (기본 활성 전환 시 dry-run 모드 포함)
  - TTL **설계 문서**: quarantine 보존기간, all-window TTL, raw 파티셔닝 방식 —
    구현은 "리뷰 max 보존 기간" 사용자 지정 후(기존 Wave 6 조건 그대로)
- **완료 기준**: 모니터링 지표 3종 동작 + 정책 DECISIONS 기록
- **의존성**: 리뷰 max 보존 기간은 사용자 결정 대기
- **✅ R 확정 (2026-07-10, 사용자 승인)**: 리뷰 max 보존 기간 = **24개월**
  (사내 데이터 보존/개인정보 정책에 더 짧은 상한 확인 시 그 값 우선).
  quarantine 30일 / all-window 90+180일 / raw 파티션 R+1=25개월 설계가 실행
  수치 확보. cleanup은 Option A 유지 → 실데이터 적재 시작 시 Option B 전환
  확정. 상세: DECISIONS/2026-07-08_retention_policy_and_cleanup_default.md
  확정 기록. → Phase 5 retention 잔여 착수 조건은 "실데이터 연속 적재 시작"뿐

### 1.4 CLI 엔트리포인트 (→ 이슈 F2 일부)

- **배경**: migrate/load가 라이브러리 함수라 운영자가 Python 코드를 직접 작성해야 함
- **작업**: `python -m src.cli` 또는 콘솔 스크립트로 migrate / full-load /
  incremental / validate / audit(0.1의 리포트 실행) 명령화. 기존 함수 시그니처는
  유지(래핑만)
- **완료 기준**: README의 실행 안내가 CLI 명령으로 대체, 운영자가 코드 없이 실행
- **의존성**: 없음

### 1.5 CI PG 테스트 커버리지 정합 (추가 2026-07-10) — ✅ 5차 수정 라운드 반영

- **배경**: postgres-service CI job의 명시적 테스트 목록에 1.1/1.3 산출물
  (test_source_identity_collision.py, test_retention_monitor.py)이 빠져
  pg_only 8건이 CI에서 한 번도 실행되지 않았음 (5차 갭 감사 HIGH)
- **반영**: ci.yml 목록에 두 파일 추가 (5차 수정 라운드 B1)
- **watch**: 다음 CI 실행에서 (a) 신규 8건 실제 실행 (b) collision 검증 기본
  활성화(`enforce_source_identity_collision=True`)가 906 목데이터에서 트립하지
  않는지 확인 (5차 Opus LOW — 트립 시 마킹 누락 데이터 조사)
- **✅ watch 해소 (2026-07-10, 로컬 PG 선제 실측)**: (a) collision 5 +
  retention 3 pg_only 전부 로컬 PG에서 pass (b) 906/517 목데이터 전체 적재 +
  validate_after=True에서 기본 활성화 상태로 트립 없음(status OK) 실측.
  추가로 scripts/run_postgres_integration.sh PG_TESTS를 CI postgres-service와
  정합(PG-gated 11개 일치, 비-PG 3개는 이중 실행 방지로 분리 블록 유지)

## Phase 2 — 서빙 경로 정리 (2~3주)

### 2.1 mart reader 분리 — DB-backed 서빙 (→ 이슈 E1)

- **배경**: DB serving 테이블/contract는 존재하는데 자체 API는 in-memory
  DemoState만 순회. 문제의 본질은 "웹/API surface가 mart reader로 분리되지 않은 것"
- **작업**: ServingStore 인터페이스 도입 — (a) DBServingStore: `serving_*` 테이블
  조회 + 주기 리프레시 캐시 (b) DemoState: 데모 모드 구현체로 격리.
  `/api/recommend`는 인터페이스만 의존
- **완료 기준**: 파이프라인이 DB를 갱신하면 API 재시작/재적재 없이(캐시 주기 내)
  반영. 데모 모드는 기존과 동일 동작
- **의존성**: 없음. 0.4(provenance)와 DB 접근 코드 공유 가능

### 2.2 SQL prefilter를 기본 후보 경로로 + 동치성 검증 (→ 이슈 E2)

- **배경**: prefilter 경로가 이미 있으나([mart_repo.py:411](../src/db/repos/mart_repo.py),
  [candidate_generator.py:326](../src/rec/candidate_generator.py)) 기본 경로가 아니고,
  in-memory 전체 순회 경로와 결과가 같은지 보장이 없음
- **작업**: DB 모드에서 prefilter를 기본 경로로 승격. **동치성 테스트**: 동일
  입력에서 (전체 순회 vs prefiltered) 후보 집합·점수·설명이 일치함을 검증.
  불일치 발견 시 prefilter 조건 수정
- **완료 기준**: 동치성 테스트 CI 통과, DB 모드 기본 경로 전환
- **의존성**: 2.1

### 2.3 파이프라인 관측성 (→ 이슈 F2 일부)

- **배경**: 실패가 pipeline_run 폴링으로만 감지됨, 단계별 소요시간 불명
- **작업**: 단계별 timing/row count 구조화 로그(JSON), pipeline_run 실패 시 알림 훅
  (webhook/슬랙 — 사내 표준 채널에 맞춤), 1.3의 모니터링 지표를 정기 리포트화
- **완료 기준**: 실패가 폴링 없이 감지되고, run별 단계 소요시간이 로그로 남음
- **의존성**: 1.3

### 2.4 알림 경로 신뢰성 (추가 2026-07-10) — (a)(b) ✅ 5차 반영, (c) ✅ 잔여계획 Batch1

- **(a) ✅** 실패 알림을 pipeline_run 완료 DB 쓰기 **앞**으로 이동 + DB 쓰기를
  try/except로 보호 — DB 다운(알림이 가장 필요한 순간)에 2차 예외로 알림 스킵
  + 원 예외 은폐 + RUNNING 고착이 재발하던 결함(5차 Opus MED) 해소. 원 예외는
  bare raise로 보존 (run_incremental_pipeline / run_full_load_db)
- **(b) ✅** async 경로의 동기 urlopen 블로킹을 `asyncio.to_thread`로 오프로드
  (`send_pipeline_failure_alert_async` 신설, retention 경로 포함) + malformed
  URL 시 Request 생성 ValueError가 "알림 절대 예외 없음" 계약을 뚫던 구멍 봉합
  — **기존 follow-up① 해소**
- **(c) ✅ 반영 (2026-07-10 Batch 1, Opus)**: DBServingStore
  serve-stale-on-refresh-error — 최초 로드 실패는 기존대로 전파(빈 데이터
  서빙 방지), stale 캐시 보유 시 리프레시 실패는 warning(exc_info) + stale
  서빙 + `_loaded_at` 갱신(재조회 폭풍 방지, 스테일 최대 1주기 연장
  트레이드오프 주석 명시). 실패 주입/최초 실패 전파/회복 테스트 3종 추가.
  Fable 리뷰 승인 — 락+double-check 동시성 설계 무손상

### 2.5 DBProvenanceProvider 실PG e2e (추가 2026-07-10) — ✅ 반영

- **배경**: fake-pool 유닛테스트만 존재 — DBServingStore는 실PG 통합테스트가
  있는 것과 대조 (5차 갭 감사 MED)
- **작업**: test_postgres_integration.py에 signal_evidence→canonical_fact→
  fact_provenance→review_raw 체인 e2e 1케이스
- **반영 (2026-07-10 Batch 2, Opus)**: e2e 2건 —
  `test_db_provenance_provider_resolves_real_persisted_chain`(snippet을 비워
  review_raw 폴백 강제, 3쿼리 체인 실데이터 검증) +
  `test_fetch_product_signals_matches_semantic_path_over_real_load`
  (semantic `axis:value:IRI` 매칭이 실PG에서 성립 — 5차 64/64 fix 고정).
  로컬 PG 실측 pass, postgres-service CI 목록 포함 파일

### 2.6 stage_logging Tier-3 실행 기반 대체 (추가 2026-07-10 — 기존 follow-up② 계획화) — ✅ 반영

- **배경**: Tier-3 6개 테스트가 inspect.getsource 문자열/순서 매칭 — 주석만
  맞아도 통과하는 거짓양성 구조 (5차 실측 확인)
- **반영 (2026-07-10 Batch 2, Opus)**: (a) 실PG 실행 검증 —
  test_full_load_db에 caplog 스테이지 로그 5종, test_incremental_pipeline_db에
  6종(부모 로거 `src.jobs` 캡처 + clear로 픽스처 혼입 차단) 어서션. 기존
  적재 흐름 재사용(추가 적재 없음). (b) Tier-3 격하 — tokenize 기반
  `_strip_comments`(문자열 보존)를 6개 전부에 적용 + 가드 테스트, docstring에
  "실행 검증 정본은 PG caplog 테스트" 명시. **follow-up② 해소**

## Phase 3 — 시맨틱 규칙 강화 (2~3주)

임베딩 도입 전에 기존 결정(value-and-polarity rule,
[DECISIONS/2026-06-22 repair](../DECISIONS/2026-06-22_recommendation_master_graph_evidence_usage_repair.md))의
연장선에서 규칙 체계를 먼저 완성한다 (크로스 리뷰 지적 #8).

### 3.1 semantic rule 카테고리 scope/gating (→ 이슈 D2)

- **배경**: `지속력→lasting_power`가 카테고리 무관 발화 → 스킨케어 탭 도배 관측
- **작업**: recommendation_semantic_compatibility.yaml 규칙에 `category_scope`
  필드 추가(기본 global 유지, 누수 규칙부터 명시 scope 부여). 관측된 누수 케이스를
  regression fixture로 고정
- **완료 기준**: 관측된 누수 케이스가 0.3 스냅샷/0.1 리포트에서 소멸, regression
  테스트 존재
- **의존성**: Phase 0 (효과 측정 기준선)

### 3.2 임베딩 보조 도구 — 오프라인 한정 (→ 이슈 D1)

- **배경**: 사전 유지보수(quarantine_unknown_keyword 검토→사전 등록)가 수작업.
  임베딩을 스코어링에 직접 넣는 것은 evidence-first 철학과 충돌하므로 **보조 한정**
- **작업**: 한국어 문장 임베딩(사내 승인 모델)으로 quarantine_unknown_keyword
  각 항목에 대해 기존 concept top-k 유사 후보 제안 리포트 생성 →
  [dictionary_growth.py](../src/qa/dictionary_growth.py) 루프에 통합.
  **사람 승인 게이트 유지** — 자동 반영 금지
- **완료 기준**: 제안 리포트가 사전 성장 루프에 연결, 승인된 항목만 사전 반영
- **의존성**: 임베딩 모델 사용 승인(사내 정책). 런타임 의존성 추가 없음(오프라인 배치)
- **진행 (2026-07-10)**: 승인 신청 초안 작성 —
  `fable_doc/05_embedding_model_approval_request_draft.md` (로컬 추론·외부
  전송 없음·quarantine 키워드 표면형만으로 범위 축소). 사내 프로세스 제출은
  사용자 몫, 승인 확보 시 착수

### 3.3 한글 인지 퍼지 매칭 개선 (→ 이슈 D3)

- **배경**: product_matcher가 ASCII 지향 SequenceMatcher — 한글 자모/띄어쓰기 변형 취약
- **작업**: 정규화 단계에 한글 특성(자모 분해 비교, 공백/기호 변형) 반영.
  기존 임계값 체계(0.93/0.80)와 quarantine 흐름은 유지
- **완료 기준**: 기존 quarantine_product_match 표본에서 정탐 증가·오탐 비증가를
  수치로 확인
- **의존성**: 없음

## Phase 4 — 그래프/검색 실체화 (3~4주)

### 4.0 multi-hop 사용 사례 audit — 구현보다 선행 (→ 이슈 A1)

- **배경**: multi-hop 미구현은 사실이나, 어떤 질문이 2-hop 이상을 실제로 요구하는지
  증명된 바 없음. 수요 증명 없이 CTE 모듈부터 만드는 것은 과잉(크로스 리뷰 지적 #10)
- **작업**: 후보 사용 사례를 canonical_fact 대상 분석 쿼리로 검증 —
  예: "유저 concern → (addressed_by) → ingredient → (has_ingredient) → product"
  경로가 1-hop concern_bridge 대비 추가 후보/근거를 실제로 만드는가.
  기대 효용을 수치로 판정
- **완료 기준**: 사용 사례 목록 + 수요 판정 DECISIONS. 수요 없으면 4.1 착수 안 함
- **의존성**: Phase 0 (효용 판정에 평가 기준선 필요)

### 4.1 (수요 실증 시) multi-hop 질의 모듈

- **작업**: canonical_fact 위 recursive CTE 기반 k-hop 이웃/경로 질의 모듈.
  기존 `idx_cf_pred_subj/obj` 인덱스 활용. dense fixture에서 응답시간 측정
- **완료 기준**: 분석가용 질의 3종 이상 + 응답시간 기록. 성능 한계 데이터 확보 후
  Apache AGE/Neo4j 도입 여부를 별도 DECISIONS로 재결정
- **의존성**: 4.0 수요 판정

### 4.2 검색 경로 (→ 이슈 A3)

- **배경**: 목적의 3축(추천/검색/개인화) 중 검색이 미구현. 단, 기존 추천 인프라
  (concept resolver, evidence index, serving profile)를 재사용하면 신규 구축이 아님
- **작업**: `/api/search` — 질의 텍스트 → 기존 concept resolver로 concept 해석 →
  concept overlap 상품 검색 → evidence family 표시. 텍스트 전문검색이 아니라
  **concept 기반 검색**으로 시작(철학 일관성)
- **완료 기준**: 질의("보습 잘 되는 스킨케어")가 concept 해석 근거와 함께 상품 반환
- **의존성**: 2.1 (mart reader)

### 4.3 온톨로지 통합 검증기 (→ 이슈 A2)

- **작업**: 4개 core config의 cross-check CI 도구 — 관계가 계약에 존재하는가,
  계약의 타입이 entity_types에 존재하는가, projection 입력 predicate가 relation에
  존재하는가, 사전 yaml의 concept 참조가 유효한가 + 전체 온톨로지 문서 자동 생성.
  `neo4j_label` 등 죽은 필드 정리 여부도 이때 결정
- **완료 기준**: config 불일치를 CI가 검출, 온톨로지 문서가 config에서 자동 생성
- **의존성**: 없음

### 4.4 neo4j_label 등 죽은 필드 결정 (추가 2026-07-10 — 4.3 완료 기준 잔여) — ✅ 결정·반영

- **배경**: 계획의 "죽은 필드 정리 여부도 이때 결정"이 미이행 —
  `KGConfig.get_neo4j_label()` 호출자 0건(src/kg/config.py) 상태 지속
  (5차 갭 감사 MED)
- **결정 (2026-07-10, DECISIONS/2026-07-10_neo4j_label_dead_field_cleanup.md)**:
  실측 사용처 맵 결과 죽은 것은 entity-label accessor 한 쌍뿐 — config 필드
  `neo4j_label`은 validator의 BEE 그룹핑 마커로, relation `neo4j_type`은
  canonicalizer 경로로 **현행 사용**. → (b) 죽은 accessor만 제거, 살아있는
  필드는 유래 주석과 함께 유지. 전면 리네임은 그래프DB 재평가(Phase 5)와 묶음
- **반영 (2026-07-10 Batch 2, Sonnet)**: `get_neo4j_label`/`_neo4j_labels`
  dict/구축 라인 제거 + 로딩부 유래 NOTE 주석. 참조 0 grep 확인,
  관련 테스트 97+18 passed, validate-ontology OK. Fable 리뷰 승인

### 4.5 검색 스케일 대비 — concept 인버티드 인덱스 (추가 2026-07-10, 조건부)

- **배경**: `resolve_query_concepts`/`search_products`가 요청마다 전 상품 순회
  (성분 concept suffix까지 스캔). 데모 517개에선 무해하나 실데이터(수만 SKU)
  에서 요청당 병목 후보 (5차 Fable 스팟 리뷰)
- **작업**: concept→product_ids 인버티드 인덱스를 서빙 캐시 리프레시 시 함께
  구축, 검색을 인덱스 조회로 전환. 기존 결과와의 동치성 테스트 필수
- **착수 조건**: 실데이터 적재로 상품 수 급증 또는 Phase 5 부하검증에서 검색
  p95 기준 초과 시. **노력**: M

## Phase 5 — 백로그 (착수 조건 명시)

| 항목 | 대응 이슈 | 착수 조건 |
|---|---|---|
| Retention 구현 (quarantine TTL, all-window TTL job, raw 월별 파티셔닝) | F1 | 실데이터 연속 적재 시작 + 리뷰 max 보존 기간 사용자 결정 (설계는 1.3에서 완료) **(2026-07-10 R=24개월 확정 — 잔여 조건: 실데이터 연속 적재 시작만)** |
| 합성 스케일 부하검증 (수만 상품/수십만 리뷰 생성기) | E3 | Phase 2 완료 — 서빙 경로가 contract로 고정된 후 **(2026-07-10 조건 충족 확인 — 승격 후보, 사용자 우선순위 결정 대기. serving 캐시 전량 리로드·검색 풀스캔·source_product_id 인덱스 효과가 최우선 측정 대상)** |
| 임베딩 recall 채널 (별도 evidence family + 신뢰 게이트) 실험 | D1 | Phase 0 기준선 + 3.1 완료 후, 평가 지표 개선 시에만 채택 **(평가 지표는 0.5 라벨 전략 선행 — 0.5는 사용자 보류 중)** |
| 다국어 사전 구조(lang-keyed) 개편 + glb 온보딩 | G1, B2 | 1.2의 glb identity 전략 결정 선행 **(2026-07-10 D안 확정 — 조건 충족, 착수는 우선순위 결정 대기)** |
| 콜드스타트 fallback (인기도 기반, evidence-first와 구분 라벨) | C4 | 실사용자 유입 시 |
| kg_mode legacy(off) 경로 제거 | G3 | shadow parity 리포트 기준 정의 + 통과 후 |
| 그래프 DB(Apache AGE/Neo4j) 재평가 | A1 | 4.1 성능 데이터 확보 후 |
| 온톨로지 문서 자동생성 (4.3 잔여) | A2 | `ontology_validator.py`는 cross-check만 구현, 문서 자동생성은 모듈 docstring에 OUT OF SCOPE로 명시(4.3 완료기준 절반 — 스코프 축소를 여기 기록). 4개 core config 스키마 변경 빈도 안정화 + 운영자/온보딩 문서 소비 수요 확인 시 착수 |
| down migration / 마이그레이션 도구 정비 | F3 | 스키마 변경 빈도가 부담될 때 |
| 유저 어댑터 계약 일반화 | G2 | 두 번째 유저 소스 추가 시 |
| 파이프라인 스케줄러·자동 재시도·운영 runbook (F2 잔여 — 2026-07-10 추적 등재: 1.4/2.3이 "일부"만 해결, 잔여분이 미추적이었음) | F2 | 운영 cron 인프라 결정 시 |
| serving 캐시 증분 리프레시 (전량 리로드 → 변경분 기반) + 대량 적재 병목 측정 | E1/E3 | Phase 5 부하검증과 함께 — 측정 후 필요 시 |

## Phase 6 — 서비스 지향 프론트 + LLM 쿼리 이해 (추가 2026-07-10, 제안)

사용자 피드백(2026-07-10): 추천테스터의 25개 가중치 슬라이더·탐색/엄격/비교
모드는 개발자 언어 — 실 서비스 관점 재설계 요구. 상세 계획(정본):
`fable_doc/plans/2026-07-10_phase6_service_frontend_query_understanding.md`

- **6.1 (Track A)** 프론트 서비스화: 의도 프리셋 3종(균형/신뢰/새로운 발견,
  기존 mode·weights의 서버측 명명 조합) + 개발자 모드 토글(기술 패널 숨김) +
  추천 결과↔그래프 연관 뷰(`/api/graphs/recommendation` 서브그래프) + CDN
  vendoring. 노력 M
- **6.2 (Track B)** LLM 쿼리 이해: 자연어 질의 → LLM 구조화 추출(카테고리/
  브랜드/속성/성분/고민/목표) → **기존 resolver 검증 통과분만** 중심 노드로
  → 질의 스코프 추천(`/api/ask`, 임시 scoped-preference 주입·저장 금지) /
  검색(회피 성분 hard filter 신설). LLM 미가용 시 사전 기반 폴백. 노력 L
- **원칙**: evidence-first 불변 — LLM은 번역기, 근거 생성 금지. 새 스코어링
  경로 금지(기존 조합·주입 재사용)
- **상태**: ✅ **실행 완료 (2026-07-10)** — 결정 4건 확정(DECISIONS/2026-07-10_phase6_service_frontend_decisions.md),
  P6-A/B/C 구현 + 마감 크로스리뷰(Opus 버그헌트/Sonnet 감사) + 수정 라운드
  (차단 1건 포함 6+4건) 완료. 게이트 1082 passed. 상세는 계획 문서 완료 보고
  및 아래 실행 보고 참조

## Phase 7 — 그래프 지능화: 연결성 가치의 실체화 (추가 2026-07-13, 제안)

그래프·온톨로지 구조 진단(`fable_doc/06_graph_ontology_assessment.md`, 리서치
2트랙 실측) 결과: 연결성 고유 신호의 추천 기여 **0/140 전수**, concern/goal/
tool/segment 어휘 生 0, 링킹 미해결 2,482건의 상위가 전량 한국어 굴절형,
wide 카탈로그에서 리뷰 신호 서빙 도달률 5% 붕괴 — "그래프여서 가능한 것"이
아직 실체가 없음. 상세 계획(정본): `fable_doc/plans/2026-07-13_phase7_graph_intelligence.md`

- **A 죽은 배선 소생(S×5)**: comparison 스코어링 배선, modes 죽은 설정 구현/
  정리, tool 결정, wide 재검증 audit, 어휘 정합 CI(生-死 감지) + E0 evidence-
  family 확장 계약 명문화
- **B 링킹 바닥(M)**: 한국어 형태론 정규화(어미 접기 — 사전 어간 존재 시만),
  동일 개념 접힘(canonical alias), [승인 후] 임베딩 잔여분(3.2 연계)
- **C 축 결합·도달 회복(M)**: concern/goal 어휘 소생 + NLP 타입 해소층(모델
  개선분이 반려로 죽지 않게), 승격 게이트 카탈로그-인지 보완(5%→30% 목표),
  IRI 저장층 정규화(신중·후순위)
- **D 진짜 그래프 신호(M)**: user-user 유사도(협업 family, G4 비저촉 확인),
  co-mention 상품 유사도, purchase_event 실배선
- **E 액션/인텐트 레이어(L, 변곡점)**: 이벤트 스키마(유저 평면 behavior edge
  + intent stage=user state, TTL-first), BEHAVIORAL_INTEREST family(단독 자격
  불가), funnel_stage→프리셋 자동 라우팅(탐색→발견/고민→중간 변형/구매→신뢰)
- **F 인사이트 서피스(조건부)**: 캘린더 버킷, 세그먼트 교차, 루틴 신호
- **상태**: ✅ **승인·착수 (2026-07-13)** — 크로스리뷰(APPROVE-WITH-CHANGES)
  반영 완료. 결정: B1=어미 접기 채택, C2=착수 시 DECISIONS 승인, **E 트랙
  보류**(외부 액션/인텐트 모델 스펙 확정 시), D 판정 위임 동의.
  P7-1부터 순차 실행(구현 Opus/Sonnet, Fable 리뷰)

## 실행 보고

### Phase 0 + Phase 1 (+4.3 선행) 완료 보고 — 2026-07-08

**구현 범위** (전부 미커밋 워킹트리, 이전 세션 + 이번 세션 누적):
- 0.1 tests/test_expected_evidence_family_baseline.py + tests/fixtures/golden_expected_evidence.yaml (골든 프로파일×카테고리 기대 evidence family assertion)
- 0.2 tests/test_personalization_contract_checklist.py — signal_flow 체크리스트 전 항목 자동화 (31 tests — 5차 감사에서 실측 정정, 종전 33 오기재)
- 0.3 scripts/generate_ranking_snapshot.py + tests/test_ranking_snapshot_regression.py + fixtures/ranking_snapshots/ + cli `snapshot` 명령
- 0.4 src/rec/provenance_provider.py + explainer/server 연동 (DECISIONS/2026-07-07_provenance_explainer_phase_0_4.md)
- 1.1 contract_validator collision 검증 공용화(+~200줄) + run_full_load_db 감지 로그 + tests/test_source_identity_collision.py
- 1.2 DECISIONS/2026-07-08_glb_channel_identity_strategy.md
- 1.3 src/db/retention_monitor.py + tests + DECISIONS/2026-07-08_retention_policy_and_cleanup_default.md (TTL은 기간 파라미터화 설계)
- 1.4 src/cli.py — migrate/full-load/incremental/validate/snapshot/monitor + tests/test_cli_monitor.py, test_cli_commands.py
- 4.3(선행) src/kg/ontology_validator.py + tests(14) — 4 config cross-check, **현행 configs 위반 0건**

**검수**: Opus 4.8 + Sonnet 5 병렬 리뷰 → Fable 종합 판정 → Opus 수정 라운드 → 2차 게이트.
반영 7건: ① snippet↔review_id 원자 페어링(SnippetEvidence 도입, MED) ② collision 카운트 row/group 단위 분리 보고(MED) ③ CLI 전 서브커맨드 테스트 추가(MED) ④ pool=None empty-as-success 폴백 제거 ⑤ clean-join leak 쿼리 is_active 정합 ⑥ snapshot --top-k>0 가드 ⑦ full-load in-memory shared-source-id 그룹 감지 구현.
테스트 유효성 판정(Opus): 신규 계약 테스트는 동어반복 아님 — 기대셋이 구현과 독립적으로 수기 작성됨.

**의도적 스코프 트림**: collision 카운터의 pipeline_run 영속화는 제외(로그+validator 보고로 완료 기준 충족, 스키마 변경 회피). in-memory full-load 경로 caplog 테스트는 해당 경로가 PG-gated라 불가 확인.

**게이트(2차 검증 실측)**: ruff ✅ / mypy ✅(107 files) / pytest **894 passed, 44 skipped, 0 failed**

**보류 유지**: 0.5(라벨 전략)·1.3 보존 기간 값 — 사용자 결정 대기. 3.2 임베딩 보조 — 사내 모델 승인 대기.

### Phase 2 + Phase 3 완료 보고 — 2026-07-08

**구현 범위**:
- 2.1 src/web/serving_store.py(ServingStore 추상화: Demo/DB 구현, 주기 캐시, str|dict 계약) + server.py 서빙 모드 배선(GRAPHRAPPING_SERVING_MODE=demo|db) + provenance_provider.py DBProvenanceProvider(배치 조회 ≤3쿼리, N+1 회피)
- 2.2 mart_repo.sql_prefilter_candidates(max_candidates) + serving_store.prefilter_candidate_ids + server.py 배선(GRAPHRAPPING_CANDIDATE_PREFILTER=auto|on|off). **동치성 검증이 기존 SQL positive-concept prefilter의 recall 버그(골든 6프로파일서 적격 후보 56개 누락) 발견 → avoided-only SQL로 수정, dense/wide/PG 완전 동치**
- 2.3 src/common/alerting.py(webhook 실패 알림, 절대 파이프라인 미중단) + pipeline_observability.py(단계 timing JSON 로그) + jobs 4파일 계측
- 3.1 semantic_compatibility.py category_scope 도입 — long_lasting만 makeup/fragrance로 제한, 나머지 8규칙 근거와 함께 global 유지. user_makeup_matte_50m/skincare 탭이 의도적 no_candidates(evidence-first)
- 3.3 product_matcher.py 자모(NFD) 블렌드 — 한글 변형 강인성, 정탐 869/오탐 23 baseline 테스트로 고정. 괄호통째제거 접근은 오탐 폭증(24→122)으로 폐기

**검수**: Opus+Sonnet 통합 리뷰(Phase 2+3 묶음) → Fable→Opus 종합 → 2-way 수정 라운드 → 2차 게이트.
확정·반영: **HIGH 3건** — ① provenance semantic path IRI 무매칭(주력 리뷰 계열 snippet 0개 → **수정 후 0/64→64/64 매칭**) ② dashboard_summary 가드가 demo 전용이라 DB 모드 항상 400(신규 코드 데드) ③ prefilter_candidate_ids가 UnitOfWork라 fake-pool 비호환+커버리지 0 → acquire 리팩터+PG 테스트. **MED 4건** — UnboundLocalError(watermark 실패 시 원 예외 은폐+알림 스킵, 2.3 목표 무력화) / README env 5종 표 추가 / SOURCE_KEY_COLLISION 상수 enums 일원화 / 3.3 corpus fp·tp 회귀 테스트. **LOW 3건** — 캐시 리스트 복사 / 혼합 스크립트 자모 게이팅 / DB 모드 evidence 그래프 명시적 400.

**follow-up 보류(수용)**: 알림 urlopen이 async except에서 동기 블로킹(배치 실패경로라 영향 낮음, uncertain) / test_pipeline_stage_logging Tier-3가 inspect.getsource 문자열 매칭(test 품질 — PG-gated 실행검증으로 보강 권장).

**게이트(2차 검증 실측)**: ruff ✅ / mypy ✅(110 files) / pytest **984 passed, 46 skipped, 0 failed**

### Phase 4 완료 보고 — 2026-07-08~09

**구현**:
- 4.0 multi-hop 사용사례 audit (scripts/audit_multihop_demand.py + DECISIONS/2026-07-08_multihop_graph_demand_audit.md) — **결론: 현 데이터에서 multi-hop 실수요 없음 → 4.1(CTE 모듈) 착수 보류**. 근거: Concern/Goal 그래프 노드 0건, 실 SKU co-use edge 0건, 공유성분 2-hop은 1-hop 커버+교차군 노이즈 84%, canonical_fact 97%가 Product 중심 star. 재평가 트리거(실데이터 적재 후 재실행) 명시
- 4.2 검색 API (src/rec/search.py + /api/search) — 기존 concept resolver + evidence index 재사용, evidence-first 계약 일관(전문검색 fallback 없음)
- 4.3 배선 완결 — cli `validate-ontology` 명령 + CI 스텝

**검수**: Opus+Sonnet 병렬 리뷰 → 4.3 검증기 tautology 아님·4.0 audit 방법론 신뢰가능 양쪽 독립 확인. 수정 반영: HIGH 1(search 성분 축이 ingredient_ids/ingredient_concept_ids를 위치 인덱스로 오정렬 → `_concept_suffix` 자기정합 매칭) + MED 4(overlap_concepts 필드 통일, 성분 축 e2e 테스트, ontology CLI/CI 진입점, audit 게이트 분리) + LOW 4.

**게이트(최종 실측)**: ruff ✅ / mypy ✅(111 files) / pytest **1018 passed, 47 skipped, 0 failed**

### 전체 실행 최종 요약 — 2026-07-09

| Phase | 결과 |
|---|---|
| 0 품질/계약 기준선 | ✅ 0.1~0.4 완료 (0.5 라벨 전략은 사용자 결정 대기) |
| 1 identity/계약 방어 | ✅ 1.1~1.4 완료 (보존 기간 값은 사용자 결정 대기) |
| 2 서빙 경로 정리 | ✅ 2.1~2.3 완료 — prefilter recall 버그(56 후보 누락)·provenance 무매칭(0/64→64/64) 등 실버그 4건 발견·수정 |
| 3 시맨틱 강화 | ✅ 3.1·3.3 완료 (3.2 임베딩은 사내 모델 승인 대기) |
| 4 그래프/검색 | ✅ 4.0(→4.1 데이터 근거로 보류)·4.2·4.3 완료 |

**루프 방식**: 구현(Opus/Sonnet 배타 파일 할당) → Opus 4.8+Sonnet 5 병렬 리뷰 → Fable 종합 판정 → 수정 라운드 → 메인 게이트 실측 → 보고서. 리뷰 4라운드에 걸쳐 HIGH 5·MED 12·LOW 10여 건 반영.

**Follow-up(보류)**: 알림 urlopen 이벤트루프 블로킹(영향 낮음), test_pipeline_stage_logging의 getsource 문자열 매칭 취약, Phase 5 백로그(착수 조건부).
**사용자 결정 대기**: ① 0.5 랭킹 ground-truth 라벨 전략 ② 1.3 리뷰 max 보존 기간 ③ 3.2 임베딩 모델 사용 승인.
**전체 미커밋** — 커밋 지시 대기.

### 5차 통합 리뷰 + 수정 라운드 — 2026-07-10

**방식**: 3자 독립 리뷰 — Opus(적대적 버그헌트: 기존 수정 6건 값-수준 재검증 + 신규 헌트), Sonnet(계획 항목 전수 갭 감사: 완료 기준 문구 vs 실코드/테스트/CI), Fable(메인: HIGH fix 사이트 직접 스팟 + 종합 판정·이슈 결정).

**판정**: 기존 수정 6건 전부 **CONFIRMED-CORRECT**(3자 일치, 값 추적+테스트 실행). 신규 HIGH 버그 0. 발견: 결함 MED 1(실패 알림이 DB 완료쓰기 뒤라 DB 다운 시 스킵+원예외 은폐+RUNNING 고착 재발) + 갭 HIGH 2(CI PG 테스트 8건 영구 skip / 0.4 데모 UI 스니펫 미렌더) + MED 4(neo4j_label 결정 미이행, F2 잔여 미추적, provenance 실PG e2e 부재, stage_logging Tier-3 취약) + LOW/문서 다수.

**5차 수정 라운드 반영 (2-way, 파일 배타)**:
- A(Opus): 알림 선행 + complete 쓰기 try/except 보호 + bare raise 원예외 보존(incremental/full_load) / `send_pipeline_failure_alert_async`(to_thread) 신설·retention 경로 오프로드 / malformed URL ValueError 계약 구멍 봉합 / 실행 기반 테스트 4종 + Tier-2/3 어서션 갱신 → **follow-up① 해소**
- B(Sonnet): ci.yml PG 테스트 2파일 등재(1.5) / db모드 dashboard `loaded` 수정 / validate-ontology CLI 디스패치 테스트 / `idx_pm_source_product_id` 인덱스(sql/indexes.sql, idempotent) / README CI·REFRESH_SEC=0 문서 정합 / "보습 잘 되는 스킨케어" 동일문구 e2e(4.2 완료 기준 마감)

**문서 정정**: 0.2 테스트 수 33→31(실측), HANDOFF 사용자 결정 대기 3건→**4건**(1.2 glb 확정 누락 복구).

**계획 삽입**: 0.6(UI 스니펫) / 1.5(반영 기록) / 2.4(c)·2.5·2.6 / 4.4·4.5 / Phase 5 백로그 2행 + 부하검증 조건 충족 노트.

**게이트(통합 실측)**: ruff ✅ / mypy ✅(111 files) / validate-ontology ✅ / pytest **1025 passed, 47 skipped, 0 failed** (+7 tests)

**잔여 watch**: 다음 CI에서 PG 신규 8건 실행 및 collision 기본 활성화 트립 여부 확인. search 풀스캔/serving 전량 리로드는 실데이터 규모 진입 시 4.5·Phase 5 항목으로 대응.

### 잔여 계획 실행 — 2026-07-10 (Batch 1·2)

**방식**: Fable(계획·리뷰·이슈 결정) / 구현은 Opus·Sonnet 5 서브에이전트 배치당 2개.
각 배치 후 Fable 직접 diff 리뷰 + 게이트 실측.

- **Batch 1**: 0.6 데모 UI 스니펫 ✅ (Sonnet — `renderPathSnippets` + `<details>`,
  전 출력 escapeHtml 경유·XSS 어서션 실측, API 무변경) / 2.4(c) serve-stale ✅
  (Opus — 최초실패 전파·stale 서빙·재조회 폭풍 방지, 테스트 3종). 게이트 1028 green
- **Fable 직접**: 4.4 결정 — DECISIONS/2026-07-10_neo4j_label_dead_field_cleanup.md
  (실측 사용처 맵 기반: 죽은 accessor만 제거, 살아있는 필드·relation 매핑 유지,
  리네임은 그래프DB 재평가와 묶음)
- **Batch 2**: 4.4 구현 ✅ (Sonnet — config.py 정리, 참조 0 grep·97+18 tests) /
  PG 묶음 ✅ (Opus — 2.5 실PG e2e 2건 · 2.6 caplog 실행검증+Tier-3 comment-strip ·
  PG 표준면 정합(스크립트 11=CI 11) · **T4: collision 기본 활성화 트립 없음 실측
  → 1.5 watch 해소**). 중간 네트워크 순단 1회 — 동일 에이전트 재개로 완주
- **최종 게이트 (Fable 직접 실측)**: 비-PG **1029 passed / 49 skipped / 0 failed**
  + ruff(src·tests)/mypy(111)/validate-ontology ✅. **PG 실측: 55 passed**(provenance
  e2e·collision·retention) **+ 11 passed**(full_load_db·incremental_pipeline_db,
  9m23s) — PG 표준면 전체 green
- **남은 계획**: 4.5(조건부 — 착수 조건 미충족), Phase 5 백로그(부하검증 승격
  후보 — 사용자 우선순위 결정 대기), 사용자 결정 4건(0.5/1.2/1.3/3.2)
  → 같은 날 후속 결정 라운드에서 3건 처리 (아래)

### 사용자 결정 라운드 — 2026-07-10

- **1.2 glb: D안 확정** (key_type 마커 `name_hint`) — DECISIONS 확정 기록.
  Phase 5 glb 온보딩 착수 조건 충족
- **1.3: R = 24개월 확정** (사내 보존정책 상한 발견 시 그 값 우선 단서) —
  retention 설계 전체가 실행 수치 확보. 잔여 착수 조건은 실데이터 적재 시작뿐
- **3.2: 승인 신청 초안 작성** (fable_doc/05) — 사내 프로세스 제출은 사용자
- **0.5: 사용자 숙고 보류** — 영향 분석 결과 NDCG 구현·Phase 5 임베딩 recall
  게이트 외 블로킹 없음. 재상정 트리거는 0.5 항에 명시

### Phase 6 실행 — 2026-07-10 (서비스 프론트 + LLM 쿼리 이해)

사용자 피드백 기반 신규 페이즈. 정본: fable_doc/plans/2026-07-10_phase6_service_frontend_query_understanding.md (계획 크로스리뷰 C1~C4 반영 → P6-A/B/C 배치 실행 → 마감 크로스리뷰 → 수정 라운드).

- **6.1 Track A** ✅: 의도 프리셋 3종(균형/신뢰/발견 — 차별성 실측 고정) +
  현행 shrinkage 무시 버그(C2) 해소 + 개발자 모드(기술 패널 숨김, ?dev=1) +
  추천 카드 인라인 "왜 이 추천" 서브그래프(응답 paths 기반, graph_view.js
  분리) + CDN vendoring
- **6.2 Track B** ✅: query_understanding/llm_client(Azure·Anthropic REST,
  멤버십 검증 게이트, 폴백 ⊇) + `/api/ask`(deep-copy 주입·비잔류 테스트,
  제한→자동완화, user_edge "질의에서 언급") + 검색 회피 hard filter + 프론트
  통합 검색바·해석 칩·경고 배너
- **마감 리뷰 → 수정**: 차단 1건(사전 폴백 부정어 침묵 실패 — "레티놀 없는
  수분크림"이 레티놀 상품 추천하던 것 → 부정 전처리+성분 검증+warnings로
  해소, 모킹 없는 e2e 고정, 수정 전후 실측 비교) + MED 2(LLM 동기 블로킹
  executor 오프로드, trusted 차별성 재튜닝) + LOW 다수
- **게이트(Fable 최종 실측)**: ruff/mypy(113) ✅, pytest **1082 passed,
  50 skipped, 0 failed** (Phase 6 누적 +53 테스트). 라이브 8123에서 차단
  시나리오 해소 직접 재현 확인 → READY
- 사용자 원피드백 6항목(가중치 모호/모드 은어/프리셋 요구/개발자 숨김/그래프
  연관 뷰/질의 흐름) 전부 해소

## 리스크 / 트레이드오프

- **그래프 DB 지연 결정**: 현 워크로드(집계 후 서빙)는 RDB로 충분. 수요 실증
  전 도입은 과잉 투자 — 4.0 audit를 관문으로 둠
- **임베딩 vs evidence-first**: 임베딩을 직접 스코어링에 넣으면 설명가능성 계약이
  약화 → 보조 도구 + 승인 게이트로 한정. recall 채널은 기준선 확립 후 실험,
  지표 개선 시에만 채택
- **랭킹 메트릭의 전제**: ground-truth 라벨 없이 NDCG는 무의미 — 라벨 전략(0.5)
  결정 전까지는 evidence-family/계약 assertion이 품질 게이트
- **retention 시점**: fixture 단계 과잉 구현 방지를 위해 설계/정책까지만 선행.
  단, 실데이터 적재 시작 시점을 놓치면 F1 리스크가 현실화되므로 착수 조건을
  백로그에 명시
- **팀 리소스 미상**: Phase는 순차 가정. Phase 0/1 병렬 가능. 각 Phase는
  CLAUDE.md 사이클(계획→구현→크로스리뷰→검수→완료보고)로 실행
