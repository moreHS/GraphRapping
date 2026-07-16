# P8-3 상세 계획 — G4 일반추천 유사도 boost + G5 쿼리 관련상품 확장

날짜: 2026-07-16 · 상태: 계획 v2(코덱스 크로스리뷰 반영, 사용자 승인 대기) ·
부모: `2026-07-15_phase8_shared_node_projection.md` §G4/§G5
전제 상태: P8-1(2b576b9)·P8-2(66fce24) 커밋 완료 — G1 계산 모듈 + 활성화 훅(gate ON) + G2/G3 서빙 중.
확정 결정(재논의 불가): 유사도=IDF 가중 공유노드 합 · **일반추천 boost=category_gate OFF**(다양성, 결정1 후속) ·
**쿼리 확장=상류 게이팅**(유사도 단계 추가 게이트 없음) · boost-only(단독 자격 불가) · keyword 복합키.

## 0. 사전 실측 (이 계획의 근거 수치, 2026-07-16 wide 517상품/50유저)

| 항목 | 실측 | 계획 귀결 |
|---|---|---|
| 서빙 유저 owned 엣지 | **1/50 유저, 1엣지** (user_dry_30f → 58763) — 코덱스 재측정 일치 | G4는 현 데이터에서 **준-dormant** — D1 전철. 부모 계획이 예고한 "배선+대기" 시나리오가 기본 |
| G4 발화 시뮬 (ungated, anchor=owned) | **1유저 / boost 후보 10개** (top 42.7~) | 스냅샷 diff = dense **무변경**, wide는 **user_dry_30f 1명 범위** → 재승인 부담 최소 |
| ungated top-N score 분포 | p90 ≈ **31.7~31.8**, max 207.3 (Fable/코덱스 양측 일치; n·median은 조립 경로 차이로 5001/5.19 vs 5096/6.47 — 방향 동일) | strength 포화 상수 **30.0**(p90 반올림). **구현 시 audit 실배선 기준으로 재측정·기록**(코덱스 지적) |
| §13.3 확장 계약 | 5조건 | §1이 조건별 1:1 이행 (§5 검수 기록의 충족표 참조) |
| 스냅샷 CLI | `generate_ranking_snapshot.py`: **--update 기본은 dense만, diff 미출력, wide는 별도 인자** | 재승인 절차를 실제 CLI 동작 기준으로 §1.6에 명시(코덱스 #8) |

## 1. P8-3a — G4: 일반 추천 re-rank boost (category_gate=False)

"A유저가 보유한 상품과 속성 연관이 많은 다른 상품"을 **이미 자격 있는 후보에 한해** 부스트.
D1(collab)/D2(comention) 계약을 미러하되, 아래 4가지 구조 사실(코덱스 검증)을 반영한다:
(i) 추천 경로는 **prefiltered 상품**만 받으므로 탭 밖 owned anchor 프로필이 안 보임,
(ii) 스냅샷은 audit 경로로 생성되며 웹 훅을 타지 않음, (iii) 프로파일 attach는 모든
상품 임베딩 API에 노출됨(고정 테스트 "신규 키는 similar_product_ids 하나" 위반),
(iv) `overlap_score=len(overlap)`이 retrieval 50컷 정렬에 쓰임.

### 1.1 데이터 플로우 — **스토어 사이드카** (프로파일 attach 아님; 코덱스 #12→#1·#2·#9 일괄 해소)
- ungated 유사도는 `build_similarity_signals(category_gate=False)` 결과 dict
  (`{anchor_pid: [SimilarProductSignal]}`)를 **그대로 사이드카 인덱스로 보관**:
  - **DBServingStore**: `_refresh`에서 기존 gated attach와 함께 계산(nodes/idf 재사용,
    쌍 열거만 2회 — 로드타임 실측 기록), `self._ungated_similar`에 보관 + 접근자
    `get_ungated_similar(product_id) -> list[SimilarProductSignal]`(ServingStore 프로토콜 확장).
  - **demo**: `load_demo_data`에서 동일 계산 → demo state 보관 → DemoServingStore 접근자.
  - **audit/스냅샷 경로**(`scripts/audit_recommendation_evidence.py` 조립부): 동일 3-함수
    조합으로 인덱스 구축 — 웹과 스냅샷이 **같은 활성화 상태**를 보게 함(코덱스 #2).
- **프로파일에는 아무 필드도 추가하지 않음** → `/api/products`·검색·추천 payload 무변경,
  P8-2 고정 테스트(신규 키 1개) 그대로 유지. **symmetrize 없음**(단방향, 부모 계획 80행).

### 1.2 계약 배선 (파일별 스펙)
- `src/rec/recommendation_evidence_index.py`: `BOOST_ONLY_TYPES` += `"similar"`.
  **`BOOST_ONLY_ADMISSIBLE_TYPES` 불변**({comparison}만) — 어떤 모드에서도 단독 자격 불가.
- **boost 인덱스 주입**(코덱스 #1): 호출자(server/audit)가 **전체 코퍼스 사이드카 × 유저
  owned ids**로 `similar_boost: dict[candidate_pid, list[(anchor_pid, strength)]]`를
  만들어 `generate_candidates(..., similar_boost=None)` **옵션 인자로 전달**(None=dormant).
  prefiltered 탭 밖 anchor도 사이드카가 전 코퍼스이므로 정상 작동. owned 엔트리는
  dict 형태(`{'id': 'product:58763', ...}`) — 기존 `_extract_ids` 정규화 사용, 유사도
  엔트리 키는 **`product_id`**(코덱스 #1 명칭 정정). `already_owned` 후보·anchor 자신 제외.
- 후보 루프: anchor별 overlap `similar:{anchor_pid}|strength={s}`,
  `s = min(score / _SIMILAR_STRENGTH_SATURATION, 1.0)`, `_SIMILAR_STRENGTH_SATURATION = 30.0`.
  **`overlap_score` 집계에서 `similar`는 제외**(코덱스 #5 — boost-only가 retrieval 50컷
  정렬을 사지 못하게). 기존 3종(comparison/collab/comention)의 집계 포함은 **불변**
  (스냅샷 보호) — 비대칭은 DECISIONS에 기록하고 후속 통일 후보로 남김.
- `src/rec/scorer.py`: top-level **`similar_product_weight`**(기본 **0.02** — §13.3(2)
  발화율-가중 반비례; 커버리지 99% 신호라 최저 계열) — `features` 맵 밖(`SCORING_FEATURE_KEYS`
  무변경). contribution 키 **`similar_product_affinity`**, 전 모드, `min(Σstrength, 1.0)`,
  `_score_layers` review_graph 그룹. **`load_from_dict`(수동 슬라이더) 경로에서는 기존
  D1/D2 의미론대로 backend boost 미적용 유지**(배선 변경 없음) — 이 의미론을 API
  테스트로 고정하고 DECISIONS에 명시(코덱스 #6은 테스트+문서로 수용, 배선은 선례 유지).
- `src/rec/explainer.py`: `_EDGE_MAP["similar"] = ("OWNS_PRODUCT", "SHARES_ATTRIBUTE")`
  — **G2 실엣지명과 통일**(코덱스 #4; _SIGNAL 접미사는 wrapped-signal 유래 엣지 관례라
  투영 신호엔 미적용). `_concept_to_feature` → `similar_product_affinity`, 요약
  "보유하신 '{anchor}' 제품과 속성을 공유하는 상품". **다중 anchor는 anchor별 path에
  해당 anchor의 strength만 표기**(합계 중복 표시 금지 — 코덱스 #4 배분 규칙).
- `configs/scoring_weights.yaml`: `similar_product_weight: 0.02` + 사유 주석.

### 1.3 명명·문서 (§13.3(4))
- evidence family 명 **`PRODUCT_SIMILARITY_AFFINITY`**(_AFFINITY 규칙, enum 충돌 없음).
  overlap prefix = `similar`.
- `db_consumer_contract.md`: §13.2 boost-only 행 "(예정)" → 확정 편입 · §13.4 갱신 ·
  **§13.3(3)에 "boost-only 타입은 `known_families`(OR 자격 버킷)에 추가하지 않는다"
  예외 명문화**(코덱스 #7 — 선례와 계약 문구의 충돌 해소).

### 1.4 provenance (§13.3(5) ← §5) — 운반 설계 (코덱스 #3 반영)
overlap 문자열에는 shared_axes가 없고 ExplanationPath도 운반하지 않으므로, **서버
후처리에서 복원**한다: 추천 결과의 `similar` path마다 `(anchor_pid, candidate_pid)`로
**사이드카 인덱스에서 shared_axes를 조회해 explanation payload에 동반**(신규 DB 조회
없음 — 로드타임 인덱스 재사용). 축별 추적 규칙(명문화 + 계약 테스트):

| 축 | 추적 경로 (신호→fact→원문) |
|---|---|
| keyword 복합키 | node_key(bee,kw,pol) → 양 상품 `wrapped_signal` 행(`idx_ws_product`, 기존 fetch 재사용) → `signal_evidence` → `canonical_fact` → 원문 |
| ingredient/category/brand/goal | serving concept id → 카탈로그 마스터(§3 진실) 또는 promoted 신호 경로 |

계약 테스트: 발화한 similar path의 shared_axes 중 keyword 노드가 실제 wrapped_signal
행으로 역추적되는지(demo 신호로) 1건 고정. — "추적 불가능한 근거는 family 후보가
아니다" 충족.

### 1.5 DECISIONS 기록 (§13.3(1) 의무)
`DECISIONS/2026-07-16_phase8_g4_similar_boost.md`: boost-only 명시 · 가중 0.02 근거 ·
SAT=30 근거(측정 소스·명령·분모 포함 — 코덱스 요구) · overlap_score 제외와 기존 3종
비대칭 · 수동 슬라이더 경로 의미론 · 발화 실측(1/50)과 "배선+대기" 판정.

### 1.6 스냅샷·기대셋 (§13.3(3), 실제 CLI 기준 — 코덱스 #8)
- 기대셋: `known_families` **불변**(§1.3 예외 명문화와 세트) + **단독 자격 fail 계약
  테스트**(similar-only → eligible=false, 전 모드) + "boost-only는 evidence family로
  집계되지 않는다" 불변식.
- 스냅샷 절차: **audit 경로에 사이드카 배선(§1.1) 후** ① dense/wide **각각** diff 모드
  실행 → ② diff 전문 보고(예상: dense 0 / wide user_dry_30f 1명 범위) → ③ **사용자
  재승인** → ④ 각 경로 명시적 update → ⑤ 회귀 재실행 green. diff가 예상 범위를
  벗어나면 **중지·보고**.

### 1.7 테스트 (synthetic 포함)
단독자격 fail(전 모드) · boost-only 불변식 · **off-category anchor → prefiltered 후보
발화**(코덱스 #1) · **eligible 후보 >50에서 similar가 50컷 정렬을 바꾸지 않음**(#5) ·
strength 정규화(30 포화)·다중 anchor 합산 클램프 · owned dict 파싱 · already_owned/
anchor 제외 · explainer path(anchor별 strength·SHARES_ATTRIBUTE) · **수동 슬라이더
경로에서 similar contribution 0 고정**(#6) · **audit 경로 발화**(#2) · provenance
역추적 1건(#3) · 사이드카 접근자(DB/demo) · 프로파일 무변경(기존 고정 테스트 유지) ·
스냅샷 재생성 후 회귀 green · G2/G3 표면 무영향.

### 1.8 완료 기준
계약 테스트 전부 + 게이트(0 failed) + wide 실측 발화 재현(user_dry_30f, 후보 10) +
스냅샷 diff 재승인. 판정 문구: 현 데이터 기준 **"배선 완료 + 구매 데이터 대기"**(D1
전철) — 액션/구매 스트림(Track E) 유입 시 자동 활성.

## 2. P8-3b — G5: 쿼리 기반 "관련 상품 더보기" (게이트=쿼리 상류)

**전제 확인(코덱스 #10 반증)**: ask-recommend는 `server.py:1141`에서 narrowed
`candidate_universe_ids`를 올바르게 전달 — "상류 게이팅" 전제 성립(실코드 확인).
선행 수정 불요.

### 2.1 서버
- `server.py` 헬퍼 `_related_products(anchor_ids, *, store, exclude_ids, limit=5)`:
  1차 결과 상위 5개의 product_id를 anchor로, **스토어 사이드카 접근자**
  (`get_ungated_similar`)에서 이웃 수집(임베딩된 `result["product"]` 의존 안 함 —
  코덱스 #9) → **`exclude_ids` 제외**: 1차 결과 전체 + (recommend 분기) 상류에서
  hard-filter로 제거된 상품·already_owned(**hard exclusion 보존** — 코덱스 #11;
  similarity 단계 게이트는 추가하지 않고 상류 계산 결과를 전달만) → 중복은 최대
  score 유지, 동점은 product_id 오름차순 tie-break → `{product_id, neighbor_name,
  score, shared_axes, anchor_product_id, anchor_name}` score 내림차순 `limit` 캡.
- `/api/search`(GET/POST)와 `/api/ask` 양 분기 응답에 `related_products` **additive**.
  search 분기 exclude = 1차 결과만(익명 설계). [C1] deep-copy 규율 준수. 1차
  `results` 구조·정렬 무변경. 빈 값 → `[]`(프론트 미노출).
- malformed 사이드카 엔트리·non-finite score 방어(skip) — 코덱스 #9.

### 2.2 프론트 (`src/static/app.js`)
검색·ask 결과 뒤 "관련 상품 더보기" 섹션: 이웃명 + anchor 귀속("'{anchor_name}'과
{axis 라벨} 공유") + shared_axes 칩(P8-2 칩 스타일) + 클릭 → `showProductDetail`.
1차 렌더 무수정. 빈 배열이면 미삽입.

### 2.3 테스트
헬퍼 단위(dedup/1차·hard-exclusion 제외/캡/귀속/최대 score/동점 tie-break/malformed
방어) · `/api/search` e2e(related_products 동반 + 기존 필드 무변경) · ask 양 분기
(recommend 분기: avoided-ingredient 상품이 related로 재등장하지 않음) · 빈 케이스 ·
1차 정렬 불변(기존 테스트 무수정 통과).

### 2.4 완료 기준
2차 섹션 shared_axes 동반 렌더 + 1차 정렬 불변 + hard exclusion 보존 + 게이트 green.
브라우저 시각 확인(스크래치 포트, 8123 무접촉)은 메인 세션 리뷰 단계에서 수행.

## 3. 시퀀싱·소유권

| 배치 | 파일 소유권 | 의존 |
|---|---|---|
| **P8-3a (G4)** | serving_store.py(사이드카+접근자), state.py(demo 사이드카), audit_recommendation_evidence.py(스냅샷 경로 배선), recommendation_evidence_index, candidate_generator, scorer, explainer, scoring_weights.yaml, §13 문서, DECISIONS, tests, 스냅샷 | — |
| **P8-3b (G5)** | server.py, app.js, tests | 3a의 사이드카 접근자 |

**순차 실행**(3b가 3a 접근자 의존, server/static 충돌 회피). 각 배치 CLAUDE.md 사이클
(Opus 구현 → Fable 리뷰 → 수정 → 게이트 → 완료 보고). 사용자 개입 지점 = **3a 스냅샷
diff 재승인 1회**.

## 4. 리스크·가드

| 리스크 | 가드 |
|---|---|
| G4 준-dormant(owned 1/50) | 계약은 synthetic으로 고정, "배선+대기" 명문화(D1 전철) |
| ungated 고점수(max 207, 변형상품쌍) 과대 부스트 | SAT=30 + 가중 0.02 + boost-only + **retrieval 집계 제외** 4중 캡 |
| retrieval 50컷 왜곡 | overlap_score에서 similar 제외 + >50 eligible 테스트(#5) |
| 스냅샷 diff 예상 초과 | 예상 범위(wide 1유저) 명시 — 초과 시 중지·보고 |
| API payload 오염 | 사이드카 설계(프로파일 무접촉) — 기존 고정 테스트가 자동 감시(#12) |
| G5 hard-exclusion 재유입 | 상류 excluded set 전달(#11) + 회귀 테스트 |
| ask 응답 스토어 참조 오염 | [C1] deep-copy 선례 준수 |
| 1차/관련 근거 혼동 | 섹션 분리 + anchor 귀속 문구 |

## 5. 검수 기록

### 코덱스 크로스리뷰 — 2026-07-16, APPROVE-WITH-CHANGES(12건) → v2 반영
- **수용 9건**: #1(anchor 가시성→인덱스 주입), #2(audit 경로 배선), #3(provenance
  운반 설계), #4(엣지명 SHARES_ATTRIBUTE 통일+anchor별 표기), #7(known_families 예외
  명문화), #8(스냅샷 CLI 실동작 절차), #9(사이드카 접근+방어), #11(hard-exclusion
  보존), #12(프로파일 attach → 스토어 사이드카).
- **스코프 조정 수용 2건**: #5(overlap_score에서 similar만 제외 — 기존 3종 불변으로
  스냅샷 보호, 비대칭은 DECISIONS 후속), #6(수동 슬라이더 배선은 D1/D2 선례 유지,
  테스트+문서로 고정).
- **반증 1건**: #10 — `server.py:1141`이 narrowed `candidate_universe_ids`를 정확히
  전달함을 실코드로 확인(코덱스 오독). G5 전제 성립, 선행 수정 불요.
- 수치 재현: owned 1/50·p90≈31.7·max 207.3 양측 일치, n/median은 조립 경로 차이 —
  구현 시 audit 실배선 기준 재측정을 §1.5에 의무화.
- §13.3 충족표(v2): (1) §1.2/1.5 ✓ (2) §1.2 가중+§1.7 테스트 ✓ (3) §1.3 예외
  명문화+§1.6/1.7 ✓ (4) §1.3 ✓ (5) §1.4 운반+추적 테스트 ✓.

### 전제 정정 + 스냅샷 재승인 — 2026-07-16 (구현 착수 전 검증에서 발견)

- **§0 "dense diff=0" 전제 반증**: 구현 에이전트가 코드 수정 전 시뮬레이션 검증
  (boost-OFF baseline이 커밋 골든과 byte 일치 → diff 100%가 boost 기여분)에서
  dense에도 diff 20줄 발견. 근본 원인 = **dense도 서빙 단계에서 user_dry_30f에게
  owned(58763) 엣지가 파생됨**(원천 픽스처엔 없음 — 메인 세션이 독립 재검증).
  계획 작성 시 원천 픽스처(owned 0)만 보고 dense=0으로 외삽한 실측 누락
  (원천≠서빙, trace-before-assert 위반). wide 9줄 예상은 정확했음.
- **diff 스코프는 의도대로**: dense/wide 모두 owned 보유 유일 유저 user_dry_30f
  1명 한정. wide makeup 탭에서 쿠션 보유자에게 설화수 퍼펙팅쿠션 top-3 진입 등 —
  기능이 설계 의도대로 작동하는 그림. 관찰: base score가 희박한 탭(0.01대)에선
  +0.02 상한 boost도 순위를 실질 재배열함(단 eligible 후보 간에서만).
- **사용자 재승인(A) 확정**: "골든 = 테스트 기대값 기록 2파일, 원천 데이터·연동
  데이터 무변경"을 소비자 전수 grep(테스트/생성기/CLI 3곳뿐)으로 확인 후
  dense+wide 함께 재승인. §1.6 절차 ③ 완료 → ④⑤(재생성+회귀 green)는 구현
  배치에 포함, 완료 조건은 전체 게이트 0 failed.

## 완료 보고

### P8-3a (G4) 완료 — 2026-07-16 (Opus 구현·2단계: 검증-중단 → 재승인 후 전체 구현, Fable 리뷰)

**변경**: candidate_generator(`build_similar_boost_index`/`extract_owned_product_ids`/
SAT=30.0/두 진입점 `similar_boost=None`/overlap_score에서 similar 제외) ·
evidence_index(BOOST_ONLY += similar, ADMISSIBLE 불변) · scorer(top-level
`similar_product_weight: 0.02`, load_from_dict 미로드=수동 슬라이더 D1/D2 의미론) ·
explainer(`OWNS_PRODUCT→SHARES_ATTRIBUTE`, anchor별 **비례배분** — 합계=기여 총량) ·
serving_store(`include_ungated=` 확장, Protocol+양 스토어 `get_ungated_similar`,
사이드카 — 프로파일 무접촉) · state(demo 사이드카) · server(`_run_scored_pipeline`
한정: duck-typed 조립+shared_axes (anchor,candidate) 인덱스로 provenance 동반,
추가 조회 0, [C1] 복사) · audit 스크립트(동일 3-함수 조합 — 웹/스냅샷 동일 활성화) ·
§13 문서(13.2 확정 편입·13.3(3) boost-only known_families 제외 예외 명문화·13.4) ·
DECISIONS 신규(측정 소스·명령·분모, dense 전제 정정 경위, overlap_score 비대칭
후속 후보) · 신규 테스트 27(계약 19+서빙/e2e 8) · 골든 2파일 재생성(재승인분).

**Fable 독립 검증**:
- 골든 변경 조합 = dense `user_dry_30f::{all,haircare,makeup,skincare}` / wide
  `user_dry_30f::makeup` **뿐** (추가/삭제 0, 메타 불변 — git 대조 스크립트로 확인)
- 재생성 diff ≡ 사전 시뮬레이션 diff (rank/score 이벤트 dense 20/20·wide 9/9,
  소수 4자리 일치; CLI 추가분은 score_layers 상세로 각 delta가 boost 기여분과
  산술 일치 — 예: 61289 +0.0184 = 0.02×27.6/30)
- 게이트 재실측: ruff ✅ / mypy 117 ✅ / pytest **1261 passed, 50 skipped,
  0 failed** (기존 1234+27, red 0)
- 로드타임: ungated 빌드 wide 122.5ms / dense 5.2ms (허용 범위)

**판정**: 현 데이터 기준 **"배선 완료 + 구매 데이터 대기"**(owned 1/50, D1 전철) —
Track E 구매/액션 스트림 유입 시 코드 변경 없이 자동 활성. wide 발화 재현
(user_dry_30f → 쿠션 이웃 10) 충족. §13.3 5조건 전부 이행.

**후속 후보(비차단)**: 기존 boost-only 3종 overlap_score 집계 비대칭 통일 여부 ·
'기타' 카테고리 그룹 min_score 재평가(P8-2 리뷰 #3).
