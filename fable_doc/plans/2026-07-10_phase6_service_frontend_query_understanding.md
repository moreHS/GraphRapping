# Phase 6 — 서비스 지향 프론트 + LLM 쿼리 이해 (계획)

작성: 2026-07-10 · 상태: **제안 (크로스리뷰 진행 + 사용자 결정 대기)** ·
상위: fable_doc/03_improvement_plan.md §Phase 6

## 1. 배경 — 사용자 피드백 (2026-07-10)

1. 추천테스터가 개발자 하네스 그대로 노출됨: **가중치 슬라이더 25개**
   (scoring_weights.yaml features) + shrinkage_k + 다양성 + 모드(탐색/엄격/비교).
   각 항목을 조절했을 때 무엇이 바뀌는지, 어떤 추천을 더 보게 되는지 모호
2. 탐색/엄격/비교는 실측상 전부 **후보 게이팅 정책**(candidate_generator의
   STRICT 분기 등) — 사용자 언어가 아님
3. 실 서비스 관점 요구: 사용자는 "**새로운 추천 / 신뢰성 있는 추천 / 내 질문
   기반 추천**"을 원함. 질문 기반은 LLM 쿼리 분석 필요
4. 기술 패널은 **개발자 모드**로 숨김 (원하면 열람 가능)
5. 추천 결과에 **그래프 연관 뷰** 연결 — "그래프를 만들어온 이유(추천/검색/
   개인화)가 최종 결과에 어떻게 쓰이는지"를 가시화하는 것이 프로젝트 의미
6. 실 시스템 흐름 = 로그인(유저 선택) + 질의 입력 → 쿼리 분석(LLM) →
   카테고리/제품명/속성/성분 추출 → **중심 노드(anchor concept)** 로 활용 →
   그에 맞는 추천/검색

## 2. 설계 원칙 (다관점 분석 결과)

- **evidence-first 불변**: LLM은 "자연어 → 기존 온톨로지 concept **번역기**"로만
  쓴다. 근거 생성·스코어 개입 금지. LLM 출력의 모든 항목은 기존
  resolver로 canonical concept 검증을 통과해야 하며, 실패 항목은 **폐기하되
  unresolved로 명시 표기**(조용한 무시 금지)
- **단순성**: 프리셋 = 기존 mode/weights/후처리 파라미터의 **서버측 명명된
  조합(config)**. 새 스코어링 경로를 만들지 않는다
- **재사용**: 그래프 뷰 = 기존 cytoscape + `/api/graphs/*` 빌더 재사용.
  검색 = 4.2 `search_products` 확장. 질의 개인화 = 기존 scoped-preference
  주입 경로 재사용
- **폴백**: LLM 미가용/타임아웃/오류 시 기존 사전 기반
  `resolve_query_concepts`로 자동 폴백 — 서비스가 LLM 장애에 종속되지 않음
- **성능**: 질의당 LLM 1회, 구조화(JSON) 짧은 출력, 정규화 질의 키 캐시(TTL),
  타임아웃 기본 2.5s

## 3. Track A — 프론트 서비스화 (LLM 불필요, 선행 착수 가능)

### A1. 의도 프리셋 (사용자 언어 3종)

- 서버: `configs/recommend_presets.yaml` 신설 — 프리셋별
  `{label_ko, description_ko, mode, weight_overrides(부분), shrinkage_k,
  diversity_weight}`
  | 프리셋 | 의도 | 내부 매핑(초안 — 튜닝 대상) |
  |---|---|---|
  | **균형 추천** (기본) | 현행 기본값 그대로 | mode=explore, YAML 기본 가중치 |
  | **믿을 수 있는 추천** | 근거 많고 검증된 상품 위주 | mode=strict, shrinkage_k 10→25, source_rating/popularity·brand_conf↑, novelty/explore 계열↓ |
  | **새로운 발견** | 안 써본 브랜드/제품군 확장 | mode=explore, novelty_bonus·same_family_explore↑, exact_owned_penalty↑, diversity 0.10→0.20 |
- API: `POST /api/recommend`에 `preset: str | None` 추가. preset 지정 시
  서버가 해석(명시 weights와 동시 지정하면 400). 응답에 `preset_used` +
  해석된 유효 파라미터 포함(개발자 모드 표시용)
- **[크로스리뷰 C2 반영]** 프리셋 해석은 반드시 **(YAML 기본 + overrides)로
  완전한 weights dict를 materialize해 `load_from_dict(weights,
  shrinkage_k=…)` 경로로** 태운다. 현행 server.py:490-494는 `req.weights`가
  없으면 `req.shrinkage_k`를 **버리는 잠재 버그**(슬라이더만 조작 시 무시)가
  있으며 이번에 함께 해소한다
- **[크로스리뷰 반영]** novelty_bonus는 구매 이력 없는 유저에게 균일값이라
  무력(scorer.py:437-447) — 프리셋의 주 차별 레버는 **shrinkage_k /
  source_rating·popularity / diversity / mode**로 두고 novelty는 보조
- 프론트: 프리셋 카드 3개(단일 선택), 카드마다 "무엇이 달라지나" 1줄
- **완료 기준**: 프리셋 3종이 골든 프로파일에서 서로 다른 상위권을 내는 것을
  검증(스냅샷 생성기가 프리셋 파라미터화를 지원하는지 먼저 확인, 미지원 시
  테스트에서 직접 3프리셋 호출·상위권 diff 검증으로 대체) + preset·weights
  동시 지정 400 + shrinkage 실적용 테스트

### A2. 개발자 모드

- 헤더 토글(🛠), `localStorage` 저장 + `?dev=1` 지원. 기본 OFF
- 숨김 대상: 가중치 슬라이더 전체, 모드 셀렉트, shrinkage/diversity,
  score-layer 수치 분해, weights_used, 파이프라인 실행 버튼·리뷰수 입력
- 사용자 모드 유지 대상(서비스 가치): 근거 evidence 칩, 설명 문장, 리뷰
  스니펫, 다음 질문
- **완료 기준**: 기본 화면에 기술 컨트롤 0개, 토글 시 전량 복원, 새로고침
  후 상태 유지

### A3. 추천 결과 ↔ 그래프 연관 뷰

- **[크로스리뷰 C4 반영 — 설계 변경]** 서버 재계산 API를 만들지 않는다.
  `GET ?user_id&product_id` 재계산은 preset/weights/질의 주입을 알 수 없어
  유저가 본 결과와 **드리프트**하고 "질의에서 언급" 경로가 소실된다
  (explanation_paths는 recommend 핸들러 내부 계산·무캐시, server.py:519,548).
  → 서브그래프는 **응답에 이미 담긴 `explanation_paths`로부터 프론트에서
  구성** (유저 노드 + 상품 노드 + path별 concept 노드, 엣지 라벨 =
  user_edge/product_edge, 굵기 = contribution, 색 = TYPE_COLORS). 서버 무변경
- **[크로스리뷰 반영]** 전역 단일 `cyInstance` + 단일 컨테이너
  `renderGraph`(app.js:594-648)는 카드별 인라인 뷰를 못 얹음 —
  `renderGraph(container, data)` 파라미터화 + 인스턴스 수명 관리(카드 접힘 시
  destroy) 리팩터 포함. 그래프 코드는 `static/graph_view.js`로 분리 권장
- 프론트: 추천 카드에 "🕸 그래프" 버튼 → 카드 하단 인라인 cytoscape
  미니뷰(높이 ~280px, 접기 가능). 그래프 뷰어 탭도 같은 렌더러 사용
- **완료 기준**: 추천 상위 상품에서 유저→concept→상품 경로가 스니펫과 함께
  시각 확인. 경로 없음/no-candidates 시 빈 상태 안내. 그래프 뷰어 탭 회귀 없음

### A4. 모드/부가 정리

- 탐색/엄격/비교는 삭제하지 않고 **개발자 모드 내부로 이동** + 설명 재작성
  (게이팅 정책임을 명시)
- CDN 의존(chart.js, cytoscape jsdelivr) → `src/static/vendor/`로 로컬
  vendoring (사내망/오프라인 데모 대비) — 결정 4

## 4. Track B — LLM 쿼리 이해 → 중심 노드 → 추천/검색

### B1. 쿼리 분석기 `src/rec/query_understanding.py`

- 입력: 자연어 질의(한국어) (+선택: 유저 요약 컨텍스트)
- LLM 구조화 출력(JSON Schema 고정):
  ```
  {intent: recommend|search|question,
   categories[], brands[], product_names[],
   desired_attributes[],        # BEE/keyword 표현 ("촉촉", "지속력")
   ingredients_wanted[], ingredients_avoided[],
   concerns[], goals[], freeform_terms[]}
  ```
- 프롬프트에 폐쇄 어휘 힌트 주입: 카테고리 그룹 6종 라벨, 대표 concern/goal
  라벨 목록 (사전에서 생성) — 환각 억제
- **검증 계층(핵심 방어) [크로스리뷰 C3 반영]**: bare
  `resolve_concern_id`/`resolve_goal_id` 직접 호출 **금지** — 이 함수들은
  unknown 입력도 normalize해 반환하므로(concept_resolver.py:62-63,111-112)
  검증기로 쓰면 환각이 전부 통과한다. 검증은 4.2 방식 그대로 **사전 키
  멤버십 + 카탈로그 존재 여부 게이팅**(`resolve_query_concepts` 재사용,
  products는 ServingStore.get_products()로 조달)으로 판정. 통과 실패 항목은
  결과에서 제외 + `unresolved_terms`로 응답 명시
- Provider 추상화: `LLMClient` 프로토콜 —
  `GRAPHRAPPING_QUERY_LLM=anthropic|azure|off` (결정 1).
  off/오류/타임아웃 → 사전 기반 폴백(현행 4.2 경로)
- 캐시: 정규화 질의 키 TTL 캐시 (동일 질의 LLM 재호출 없음)
- 테스트: LLM 모킹 fixture + 검증 계층 단위 + 폴백 경로. 실 LLM 호출은
  env-gated 통합 테스트 1개

### B2. 중심 노드 → 결과 생성 (2경로)

- **(a) 검색 경로** (유저 무관): resolved concepts → 기존 `search_products`
  랭킹 + **회피 성분 hard filter 신설** ("레티놀 없는 크림" 대응)
- **(b) 질의 스코프 추천** (유저 + 질의):
  - 후보: 질의 카테고리 → 해당 탭 universe. 질의 concept 보유 상품으로
    프리필터하되 **교집합이 비면 부스트-only로 자동 완화**하고 응답에 완화
    사실 명시 (recall 보호 — 결정 2)
  - 스코어: **새 레이어를 만들지 않고** 질의 concern/goal/keyword를 요청
    스코프의 임시 scoped-preference로 유저 프로필에 주입(저장 금지) — 기존
    계약·경로 재사용. 회피 성분은 기존 hard filter로
  - **[크로스리뷰 C1 반영 — 최대 리스크]**: ServingStore.get_user는 캐시
    dict의 **참조**를 반환(serving_store.py:138-142, 352-354)하므로 주입 전
    **deep-copy 필수**. 완료 기준에 "동일 유저 2연속 요청에서 주입 비잔류"
    테스트 포함
  - **[크로스리뷰 반영]** 주입 항목은 `_collect_scoped` 출력 스키마
    (`{edge_type,id,weight,scope_group,source_sections}`,
    build_serving_views.py:211-223)와 동일 shape + scope_group은 global 또는
    질의 카테고리여야 실제 매칭됨. 실 유저는 scoped-only 단락이므로
    (scoped_preferences.py:35-43) 반드시 scoped로 주입 (legacy 필드 주입은
    무시됨)
  - 설명: 질의 유래 경로는 user_edge 라벨을 "질의에서 언급"으로 표기 —
    candidate_generator는 주입/실선호를 구분 못 하므로 **/api/ask가 주입
    concept_id 집합을 기억해 응답 paths의 user_edge를 후처리 재작성**
    (explainer.py:97 meta override 활용)
- 신규 API: `POST /api/ask {user_id?, query, preset?}` →
  `{interpretation(칩 데이터), unresolved_terms, resolved_mode:
  recommend|search, results[](기존 형식)}`
  — user_id 있으면 (b), 없으면 (a)
- **[크로스리뷰 반영] 가드**: query max length(예: 500자), LLM 호출은
  `GRAPHRAPPING_QUERY_LLM` 미설정 시 자동 off(폴백), API 키는 env로만·로그
  금지. (인터넷 노출 배포 시 pipeline/run과 같은 opt-in 가드 패턴 재사용
  여지 — 데모 단계에선 max-length만)
- **[크로스리뷰 반영] 의존성**: LLM HTTP 클라이언트(httpx 등)는 pyproject
  **optional extra**(`query-llm`)로 선언 + import 가드 — off/폴백 기본값은
  코어 의존성 무추가
- **완료 기준**: "지성 피부에 맞는 순한 토너 추천해줘" → 해석 칩(지성/토너/
  순함) + 근거 있는 결과. "레티놀 없는 수분크림" → 회피 필터 실동작.
  LLM off 폴백에서도 동일 API가 검색 수준으로 동작

### B3. 프론트 통합

- 홈(추천 탭 개편): 유저 선택 + 통합 입력창("검색하거나 질문하세요") +
  프리셋 카드 → 결과는 기존 카드(+A3 그래프 버튼) 재사용
- 해석 칩 UI: LLM 해석 결과를 칩으로 노출, unresolved는
  회색 칩("'저분자'는 아직 사전에 없어요")
- **[스코프 축소 확정 2026-07-10]** "칩 제거 → 재실행"은 미구현 — 질의문
  수정 후 재제출이 대체 UX (DECISIONS/2026-07-10_phase6_service_frontend_decisions.md §4)

## 5. 결정 — ✅ 사용자 확정 (2026-07-10)

1. **LLM provider 기본값 = 사내 Azure OpenAI.** 표준 env 4종으로 설정:
   `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` /
   `AZURE_OPENAI_DEPLOYMENT` / `AZURE_OPENAI_API_VERSION`
   (`GRAPHRAPPING_QUERY_LLM=azure` 기본, `anthropic|off` 교체 가능 추상화 유지.
   키/배포명은 사용자 환경에서 주입 — 코드/저장소에 비밀값 금지)
2. **질의 스코프 = 제한→자동완화 하이브리드** (교집합 공집합 시 부스트-only
   완화 + 응답에 완화 사실 명시)
3. **CDN vendoring 채택** — chart.js/cytoscape를 `src/static/vendor/`로 로컬화
4. **착수 범위 = Track A 먼저 → Track B** (P6-A 완료·리뷰 후 P6-B/C).
   프리셋 3종 명칭·매핑 초안은 기본 승인, 스냅샷 결과 보고 튜닝

## 6. 실행 계획 (배치당 에이전트 2, 각 배치 후 Fable 리뷰+게이트)

**[크로스리뷰 반영 — P6-A를 순차 2스텝으로 재편]** (C4로 A3가 프론트 전용이
되면서 server.py 충돌은 해소됐으나, app.js 685줄 단일 파일을 두 에이전트가
동시 수정하는 위험은 여전 → 순차)

| 배치 | 내용 | 담당 | 파일 |
|---|---|---|---|
| **P6-A Step1** | A1 프리셋(config+API+테스트, C2 버그 동시 해소) + A2 개발자 모드 + A4 CDN vendoring | Sonnet | server.py(recommend 부분), configs/recommend_presets.yaml, app.js, index.html, app.css, static/vendor/, tests |
| **P6-A Step2** (Step1 후) | A3 그래프 렌더러 파라미터화·`static/graph_view.js` 분리 + 결과 카드 인라인 뷰(응답 paths 기반, 서버 무변경) | Opus | app.js, index.html(script), app.css, static/graph_view.js |
| **P6-B** | B1 쿼리 분석기 + provider(azure 기본) + 멤버십 검증 + 폴백 + 테스트 | Opus | src/rec/query_understanding.py(신규), pyproject(optional extra), tests(신규) |
| | (여유 시) 문서/모드 설명 정리 | Sonnet | 문서 |
| **P6-C** | B2 /api/ask(deep-copy 주입·비잔류 테스트·user_edge 재작성·가드) + 검색 회피필터(search.py:380 시그니처 확장) | Opus | server.py, search.py, tests |
| | B3 프론트 통합(입력창/해석 칩) | Sonnet | app.js/index.html/app.css |
| 마감 | Opus+Sonnet 크로스리뷰 → 수정 → 게이트 → 보고서 | — | — |

- 노력: Track A = **M**, Track B = **L**

## 7. 리스크 / 트레이드오프

- **LLM 환각** → 검증 계층이 유일 방어선. resolver 미통과 항목은 폐기+표기.
  프롬프트 폐쇄 어휘로 1차 억제
- **질의 스코프의 recall 손실** → 자동 완화 + 응답 명시로 방어
- **프리셋 차별성 부족** → 완료 기준에 "프리셋 간 상위권 차이 스냅샷 고정" 포함
- **LLM 레이턴시/비용** → 캐시 + 타임아웃 + 폴백. 데모 규모에선 무시 가능
- **계약 순수성**: 질의 주입이 저장되면 개인화 오염 — "요청 스코프, 저장
  금지"를 테스트로 고정. **[위치 정정 2026-07-10]** 비잔류 테스트는 0.2
  파일이 아니라 `tests/test_api_ask.py::test_ask_query_injection_does_not_persist_into_store`
  에 배치됨(/api/ask 응집도 우선 — 마감 감사 지적에 따라 실위치 기록)

## Follow-up (Phase 6 이후, 보류)

- 브라우저 검증(개발자 모드/인라인 그래프/칩)의 재실행 가능한 회귀 자산 부재
  — 각 배치의 Playwright 검증은 1회성. Playwright 스펙 도입 여부는 별도 결정
- 칩/경고 렌더 XSS 전용 회귀 테스트 (코드 검토상 displayText 일관 적용으로
  안전 — 테스트만 공백)
- 혼합 질의("레티놀 토너와 레티놀 없는 크림") avoided 우선 맹점 — 문서화된
  설계 절충, 실사용 불만 관측 시 재검토

## 검수 기록

### 계획 크로스리뷰 — 2026-07-10 (Opus Plan Reviewer, 실코드 대조)

**판정: APPROVE-WITH-CHANGES** → 치명 갭 4건 전부 본 계획에 반영 완료:
- C1 서빙 스토어 유저 dict 참조 반환 → 주입 전 deep-copy 필수 + 비잔류 테스트
- C2 weights 미지정 시 shrinkage_k 무시(현행 잠재 버그) → 프리셋은 완전한
  weights dict materialize 경로로, 버그 동시 해소
- C3 bare resolver가 unknown 통과 → 사전 멤버십 게이팅으로 검증 방식 교정
- C4 서브그래프 서버 재계산 드리프트 → 응답 paths 기반 프론트 구성으로 변경
중요 개선 6건(의존성 optional 선언, novelty 무력 프로필, /api/ask 가드,
스냅샷 파라미터화 확인, cyInstance 리팩터, user_edge 후처리)도 반영.
안전 확인: 프리셋 차별성 구조 성립, scoped 주입 경로 존재(scoped-only 단락
확인), 검색 회피필터 삽입점(search.py:380), 계약 테스트 비파손.

## 완료 보고 (실행 후 누적)

### P6-A (Track A) 완료 — 2026-07-10

**Step 1 (Sonnet)**: A1 프리셋 3종(configs/recommend_presets.yaml + `preset`
파라미터 + GET /api/recommend/presets) — 해석은 완전 weights materialize 경로,
**C2 잠재 버그(weights 미지정 시 shrinkage_k 무시) 해소**(4분기 정리, 순수
기본 요청은 byte-identical 유지). 차별성 실측: user_dry_30f 상위5가 3프리셋
pairwise 전부 상이. A2 개발자 모드(localStorage `gr_dev_mode` + `?dev=1`,
기술 컨트롤 dev-only 분리, 사용자 모드 = 유저선택+프리셋 카드). A4 vendoring
(chart.js 4.5.1 + cytoscape 3.34.0, MIT, vendor/README). 신규 테스트 12개.
Playwright 32건 검증. Fable 리뷰: 스코어러 4분기 배선 승인.

**Step 2 (Opus)**: A3 — `static/graph_view.js` 신설(WeakMap 인스턴스 수명
관리, TYPE_COLORS 이동), 그래프 뷰어 탭 리팩터(회귀 없음 브라우저 확인),
추천 카드 인라인 "왜 이 추천" 서브그래프(**응답 explanation_paths 기반, 서버
무변경 — C4 설계 준수**): 유저→concept→상품, 엣지 굵기=|contribution| 상대
스케일, 음수=빨강, 스니펫 수 💬N 배지, 다중 카드 독립·접기/재추천 정리·빈
경로 빈상태 전부 브라우저 실검증(콘솔 에러 0). Fable 리뷰: 서브그래프 빌더
승인(노드 병합·스니펫 누적 정확).

**게이트**: ruff/mypy ✅, pytest **1041 passed, 49 skipped, 0 failed**(+12),
node --check 전체 OK. 데모 서버 8123 새 UI로 재기동.

### P6-B (B1 쿼리 분석기) 완료 — 2026-07-10 (Opus)

- 신규: src/rec/llm_client.py(LLMClient Protocol + Azure/Anthropic REST 직접,
  httpx lazy import 가드, 키 무로깅) + src/rec/query_understanding.py
  (understand_query → QueryInterpretation, TTL 10분 캐시, 폐쇄 어휘 프롬프트,
  인젝션 방어)
- **C3 준수**: bare resolver 미사용 — LLM term별로 `resolve_query_concepts`
  멤버십 게이트 통과분만 채택, 실패는 unresolved_terms. 원 질의 직접 매칭과
  합집합 → **폴백 ⊇ 보장**(recall 무손실). 의도적 예외: 회피 확정 성분은
  positive 매칭에서도 제거("레티놀 없는" negation 대응)
- provider 기본: env 미설정=off(폴백) — azure/anthropic 명시 시에만 활성
  (결정 1의 "azure 기본"은 활성화 시 권장값으로 해석, 데모 안전 우선)
- pyproject optional extra `query-llm`(httpx), README env 6행. 신규 테스트
  16(+skip 1). **게이트: 1057 passed, 50 skipped, 0 failed** (mypy 113 files).
  Fable 스팟 리뷰 승인

### P6-C (B2 /api/ask + B3 프론트 통합) 완료 — 2026-07-10

- **B2 (Opus)**: search.py 회피 hard filter(기본 None 하위호환) + `/api/ask`
  (search/recommend 2모드, 질의 카테고리 매핑, **C1 deep-copy 주입 + 공유
  캐시 비잔류 테스트**, 제한→자동완화 relaxed, preset 재사용, user_edge
  "질의에서 언급" 후처리). recommend 핸들러의 후보→스코어→설명 구간을
  `_run_scored_pipeline` 공통 헬퍼로 추출 — 전 스위트 green으로
  byte-identical 입증. 주입 어휘 실측 교정(goal=WANTS_GOAL). 테스트 +15
- **B3 (Sonnet)**: 통합 검색바(IME 조합 가드) + "로그인 없이 (검색만)" +
  해석 칩(개념 색상/🚫회피/미해석 점선) + 모드별 결과 렌더 분기 + 그래프
  인스턴스 정리 공유. **실 e2e로 서버 실응답과 계약 일치 검증**(콘솔 에러 0)
- 중간 세션 리밋 1회 — 두 에이전트 모두 디스크 상태 실측 후 동일 에이전트
  재개로 완주. 게이트: 1072 passed, 50 skipped, 0 failed

### 마감 크로스리뷰 + 수정 라운드 — 2026-07-10

**리뷰**: Opus 버그헌트(신규 HIGH 0·MED 1·LOW 4, 헬퍼 추출/C1/어휘/게이트
우회 불가 등 안전 확인) + Sonnet 완료기준 감사(**NOT-READY** — 라이브 재현
차단 1건: 기본 설정(LLM off)에서 "레티놀 없는 수분크림"이 레티놀 상품을
1위 추천하는 침묵 실패, 기존 테스트는 전부 가짜 해석 주입이라 미탐지 + 문서
정합 갭 다수).

**Fable 종합 → 수정 라운드 (2-way)**:
- A(Opus): **F1 차단 해소** — 경로 공통 부정 전처리(한국어 문법형 무공백
  허용 / '프리·free'는 구분자 필수 — "이니스프리" 오탐을 실측으로 잡아 분리)
  + 성분 축 멤버십 검증 + `interpretation.warnings` + **모킹 없는 e2e**
  (수정 전후 실측: top5 전부 레티놀 → 레티놀 0). F2 LLM 동기 호출
  executor 오프로드. F3 trusted 재튜닝(balanced와 top5 ≥2 상이, 테스트
  강화). F4 ask KPI 메타(candidate_count 등+weights_used). F5 user_edge
  정규화 비교(구코드 실패 테스트로 고정). F6 혼합 질의 맹점 docstring
- B(Sonnet): G1 칩 잔류 클리어, G2 warnings 경고 배너(XSS 프로브 확인),
  G3 preset_used/weights_used 개발자 모드 표시(override 수 실값 일치 확인),
  G4 README Demo UI 절
- 문서 정합(Fable): 03 상태 갱신, DECISIONS 승격
  (2026-07-10_phase6_service_frontend_decisions.md — provider 기본 재해석·
  부정어 보강·칩 스코프 축소 포함), 비잔류 테스트 실위치 정정, follow-up
  3건 등재(브라우저 회귀 자산/XSS 테스트/혼합 질의)

**최종 게이트 (Fable 직접 실측)**: ruff/mypy(113)/node --check ✅, pytest
**1082 passed, 50 skipped, 0 failed** (Phase 6 누적 +53). 라이브 8123에서
차단 시나리오("레티놀 없는 수분크림" — 검색·유저 추천 양 모드) 해소 직접
재현: avoided 배선·결과 내 레티놀 상품 0. **판정: READY**
