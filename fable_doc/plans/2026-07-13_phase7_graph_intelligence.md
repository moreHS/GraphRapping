# Phase 7 — 그래프 지능화: 연결성 가치의 실체화 (계획)

작성: 2026-07-13 · 상태: **제안 (크로스리뷰 진행 + 사용자 승인 대기)** ·
근거 진단: `fable_doc/06_graph_ontology_assessment.md` (실측 전수) ·
상위: `fable_doc/03_improvement_plan.md` §Phase 7

## 0. 목표와 논리

진단의 한 줄: 연결성 고유 신호의 추천 기여 **0/140 전수**, 그래프 강점 '상'
유스케이스 **0개**. 그러나 원인이 전부 분류됨(구현 부재/데이터 부재/형태론
천장/게이트 붕괴/축 단절). Phase 7의 목표는 **"그래프여서 다른 결과"의 첫
실측 사례를 만들고, 유저 액션/인텐트 스트림이 들어오는 순간(변곡점)에
시스템이 진짜 그래프로 승격될 기반을 완성**하는 것.

우선순위 논리: 죽은 배선 소생(A) → 링킹 바닥(B) → 축 결합·도달 회복(C) →
진짜 그래프 신호(D) → 액션/인텐트 레이어(E) → 인사이트 서피스(F).
A·B는 병렬 가능, C는 B에 일부 의존, D는 독립, E는 계약(E0)과 이벤트 데이터
확보에 의존, F는 후순위.

원칙 (기존 계약 불변):
- evidence-first 유지 — 모든 신규 신호는 evidence family로 분류되고 단독
  자격 조건이 명시된다. 비개인화/행동 신호가 자격을 사지 못하게 하는 기존
  규율을 신규 family에도 그대로
- 승격 게이트 완화는 **스냅샷·기대셋(0.1/0.3) 회귀로 검증**하며 진행 —
  recall 회복이 노이즈 유입이 되지 않게
- 그래프 DB 도입은 여전히 보류(4.0 audit 유효) — Phase 7은 전부 현행 RDB
  위에서

## Track A — 죽은 배선 소생 (전부 S, 1배치)

### A1. comparison_with 스코어링 배선
- 근거: 데이터(8건)·서빙 필드(top_comparison_product_ids)·family 분류 모두
  존재하는데 candidate_generator가 `comparison:*` overlap을 만들지 않음
  (진단 §2). **[크로스리뷰 정정] "마지막 한 스텝"은 과장** — scorer feature/
  weight도 전무(SCORING_FEATURE_KEYS에 comparison 없음)라, overlap만 만들면
  eligibility는 통과하는데 점수 기여 0인 후보가 생기는 부작용 위험
- 작업: ① candidate overlap 생성 ② scorer feature 신설(scoring_weights,
  보수적 가중) ③ **유저측 앵커 결정** — comparison은 product↔product이므로
  owned_product_ids와 매칭(소유 상품과 비교되는 상품) + /api/ask 질의 상품
  매칭 중 선택/병행 ④ **자격 등급 결정: boost-only 권장**("비교됨"은 약신호
  — 단독 자격 부적절, D1의 boost-only 버킷 신설과 연계) ⑤ 스냅샷 diff 승인
- 완료 기준: comparison 신호 발화 테스트 + "overlap 있으나 점수 0" 상태
  부재 확인 + 앵커·자격 결정 기록

### A2. `modes` 죽은 설정 정리
- 근거: `modes.explore.category_penalty`/`modes.compare.comparison_neighbor`
  를 읽는 코드 0곳 — COMPARE는 실제로 EXPLORE와 동일 동작 (진단 §2)
- 작업: 구현 or 제거 결정 후 반영 — **결정: 구현** (COMPARE의 존재 이유를
  A1과 묶어 실체화. category_penalty는 현행 후보 게이팅과 중복이면 제거)
- **[크로스리뷰 반영]** config-only 정리가 아님 — scorer.score/reranker.rerank
  시그니처에 mode 인자가 없어(현행 mode는 후보 하드필터만), server→scorer/
  reranker로 mode를 흘리는 **신규 배선**이 필요(S이되 코드 변경)
- 완료 기준: 설정-동작 정합 (죽은 키 0), 모드별 차이 테스트

### A3. tool_alignment 공급 경로 결정
- 근거: scorer feature는 있는데 user adapter에 tool 공급 0 (진단 §2)
- 작업: TOOL 신호 자체가 生 0(진단 §3)이므로 **C1의 어휘 소생과 운명 공동** —
  C1에서 tool 신호가 살아나면 어댑터 배선, 안 살아나면 feature 제거.
  Track A 시점에는 "결정 기록"만
- 완료 기준: DECISIONS 1건 (배선 or 제거 예약)

### A4. wide/실데이터 재검증 audit + wide 베이스라인 생성
- 근거: 진단 실측은 dense(32상품) 기준 — 517상품 wide에서 동일한지 확정
  필요 (투자 판단 근거). **[크로스리뷰 반영] wide 스냅샷/기대셋 베이스라인이
  현재 존재하지 않음**(dense 단일) — C2가 참조할 회귀 자산을 여기서 생성
- 작업: ① `audit_recommendation_evidence.py --fixture wide` 실행·기록,
  U1 표를 wide로 재산출해 06 문서에 추기 ② **wide 랭킹 스냅샷 베이스라인
  생성·커밋** ③ wide 서빙 도달률의 **구조적 천장 실측**(리뷰≥1 보유 상품
  비율 — 리뷰 0 상품은 게이트와 무관하게 영구 0이므로, C2 목표치는 이 천장
  기준으로 확정)
- 완료 기준: dense/wide 비교표 + wide 스냅샷 베이스라인 + 천장 수치

### A5. 어휘 정합 CI 확장 (ontology_validator v2)
- 근거: canonical_map meta 오기(65vs68), 고아 타입(Color/Volume), 生 0
  family 4종 — 층간 정합 검증 부재 (진단 §3)
- 작업: ontology_validator에 (a) 파일 내부 meta 정합 (b) 고아 entity 타입
  검출(생성되나 projectable predicate 없는 타입) (c) **生-死 감지**: 데모
  fixture 실행 산출물 기준 "contract/projection에 있는데 생성 0인 어휘"
  리포트(경고 — CI fail은 아님, 데이터 의존이므로) (d) 3층 브리지 상수
  (_NER_TO_CANONICAL_TYPE 등)의 어휘 커버 검증
- 완료 기준: 위 4종 검출이 현행 상태를 정확히 보고(고아 4·死 family 4 등)

## Track B — 엔티티 링킹 바닥 (M)

### B1. keyword 해소 경로 통합 + 한국어 형태론 정규화 ★핵심
- 근거: 미해결 2,482 표면형의 상위가 굴절형(`촉촉하고/촉촉해서/촉촉해요`)
  + 사전에 있는데도 격리(`무향` 8건) (진단 §4)
- **[크로스리뷰 치명갭 반영 — 구조 재정의]**:
  ① **진짜 근본 원인은 해소 경로 이중화**: bee_normalizer의 키워드 매처는
  부분문자열 매칭이라 `촉촉`⊂`촉촉하고`를 원래 잡는데, **quarantine을
  생성하는 mention_extractor candidate 큐는 keyword_surface_map을 아예
  조회하지 않음**. 접기 규칙 이전에 이 경로 통합이 최우선
  ② **"어간이 사전에 있다" 전제는 부분 반증됨**: `촉촉`(58건 계열)은 사전에
  있지만 미해결 2위 `순하고`(13)의 어간(순함/순한)은 **어느 사전에도 없음**
  — 이건 형태론이 아니라 사전 갭. 누락 어간 등재 substep 병기
- 작업 (순서 재정의):
  1. **두 keyword 해소 경로 통합** — mention_extractor candidate 큐를
     기존 사전 매처(부분문자열)에 통과시켜 사전 등재 표면형이 quarantine
     으로 새지 않게 (+ normalize 정렬로 `무향`류 누수 해소)
  2. 보수적 어미 접기 (어간이 사전에 존재할 때만): **부정 문맥 방어 필수**
     — `촉촉하지 않`이 `촉촉`으로 접혀 극성이 뒤집히면 안 됨(기존
     _detect_negation을 candidate 경로에도 적용). ㅂ불규칙(`가볍고`→dict
     `가벼움` 불일치)은 접기 실패=재현율 저하로 수용(오접힘보다 안전).
     kiwipiepy(형태소 분석기) 도입은 접기+등재 후 잔여율 실측 뒤 결정
  3. 누락 어간 사전 등재: quarantine 상위 중 사전 부재 어간(순함/마일드 등)
     — 도메인 감수 1회
  4. 효과 실측: 경로통합+접기+등재의 **합산 목표 50%↓** (개별 아님).
     before/after를 기대셋·스냅샷·3.3 코퍼스 baseline 회귀로 검증
- 완료 기준: 격리 감소 수치 실측(합산) + 오탐 비증가 + 부정 문맥 테스트

### B2. 동일 개념 접힘(canonical alias 계층)
- 근거: 보습/촉촉/촉촉한이 3개 keyword_id로 분산, `보습`이 3개 taxonomy에
  병존, `무너짐`이 concern/BEE 양쪽에 (진단 §4)
- 작업: keyword_id 레벨의 canonical alias 맵(같은 개념 → 대표 id) 신설 +
  표면형-taxonomy 우선순위 규칙 명문화(같은 표면형이 여러 축에 있을 때
  경로 결정 규칙). agg/serving 재계산 영향은 스냅샷으로 확인
- 완료 기준: 대표 분산 사례(보습 계열)가 단일 concept으로 접히고 스냅샷
  diff가 의도 변경만 포함

### B3. 임베딩 보조 (기존 3.2 — 승인 후)
- B1 이후의 잔여 미해결분(진짜 신조어/개방 어휘)에 기존 3.2 계획 그대로.
  선행: 사내 승인(fable_doc/05 초안 제출됨) + B1 완료(임베딩 대상 축소)

## Track C — 개념 축 결합 + 서빙 도달 회복 (M)

### C1. concern/goal 어휘 소생 + 타입 해소층
- 근거: Concern/Goal/Segment/Tool 노드 生 0 — NLP가 안 뱉는 것이 아니라
  contract 타입 게이트 반려(`affects|Product→Category` 188건 등) + projection
  死 (진단 §3). **relation 모델이 학습 중이므로, 모델 개선분이 반려로 죽지
  않게 하는 타입 해소층이 선행돼야 함** (구조 리스크 ②)
- 작업:
  1. 반려 quarantine(projection_miss 3,547) 상위 패턴 분석 → NLP 출력
     타입과 contract 기대 타입 간 **타입 해소 어댑터**(예: Category로
     오타이핑된 concern 표면형을 사전 대조로 Concern 재타이핑 — 보수적,
     사전 멤버십 게이트 재사용)
  2. concern_bee_attr 브리지 5쌍(=4 distinct concern) → 사전(concern_dict×
     bee_attr_dict) 기반 수십 쌍으로 확장 + ingredient→concern 큐레이션 맵
     신설(도메인 감수 필요 — 1-hop 룩업 유지). **[크로스리뷰] 기존
     `ToolConcernSegmentDeriver.derive_concern` 재사용** — config-only 확장
     가능, 신규 deriver 불필요
  3. 유저측 미등재 concern(피지/등드름/냄새 등) 사전 등재
- 완료 기준: concern 계열 signal family 生 > 0 실측, concern_bridge_fit이
  골든 프로필에서 발화하는 케이스 ≥1, relation 모델 개선 시 반려율 모니터
  링 지표 추가
- 주의: 신규 신호의 자격 승격은 기대셋(0.1)에 반영해 계약으로 고정

### C2. 승격 게이트 카탈로그-인지 보완
- 근거: 동일 906리뷰가 dense 91% vs **wide 5%** 서빙 도달 — 절대 임계
  `distinct_review≥3`이 분산 카탈로그에서 리뷰 그래프를 꺼버림 (진단 §5)
- 작업: 절대 임계에 **카탈로그-인지 보조 게이트** 설계. **[크로스리뷰
  반영]** window 차등은 이미 부분 적용돼 있음(D30=2/D90·ALL=3) — 실제
  레버는 (a) 상품 리뷰 수 대비 상대 임계(리뷰 2개뿐인 상품은 2/2도 유의)
  (b) ALL/90d 완화 (c) confidence 상향과 교환. **다관점 비교 후 DECISIONS로
  결정** — 완화가 노이즈 유입이 되면 안 됨 (synthetic_ratio 게이트는 유지)
- 완료 기준: wide 서빙 도달률 5% → 실질 개선 — **목표치는 A4가 실측한
  구조적 천장(리뷰≥1 상품 비율) 기준으로 확정**(사전 못박기 금지). dense/
  wide 스냅샷 diff는 "의도 변경 재승인" 워크플로우로 처리(단순 green 아님)
  + 결정 기록

### C3. IRI 저장층 정규화 (신중 — 후순위 가능)
- 근거: `concept:Concern:concern_dryness` 이중 네임스페이스, goal 한국어
  토큰 등 저장층 혼재 — 현재는 concept_resolver가 조회 시점에 흡수 (진단 §3)
- 작업: 저장 시점 정규화로 이동 + 기존 데이터 마이그레이션. **파급이 크므로
  Phase 7 내 착수는 보류 가능** — C1/B2가 만들 신규 어휘부터 규칙 적용,
  기존분 마이그레이션은 실데이터 적재 전 1회로 계획
- 완료 기준: 신규 생성 경로의 IRI 규칙 단일화 + 마이그레이션 계획 문서

## Track D — 진짜 그래프 신호 (M~L)

### D1. user-user 유사도 (협업 신호 프로토타입)
- 근거: agg_user_preference 벡터가 이미 존재, G4 invariant는 reviewer proxy
  병합만 금지(실유저 유사도는 정책 장벽 없음 — 확인됨), 계산 코드만 부재
  (진단 §2 U2). **"그래프여서 다른 결과"의 최단 경로**
- 작업: 유저 선호 벡터 코사인/자카드 최근접 → "유사 유저가 선호/구매한
  상품" 신호 → **신규 evidence family(COLLABORATIVE_AFFINITY, 단독 자격
  불가·결합 부스트만)** + 설명 문구("취향이 비슷한 고객들이…"). 50명
  fixture에서 유의성 사전 검증 후 채택 여부 판단(희소하면 실유저 데이터
  확보 시로 연기 — 프로토타입의 목적은 배선 검증)
- **[크로스리뷰 반영 — 코드 신설 필수]**: "단독 자격 불가"는 E0 문서만으론
  강제 불가 — 현행 `build_candidate_eligibility`는 4버킷 OR 구조라
  **boost-only 버킷(eligible 판정에서 제외되는 5번째 분류)**을 코드로
  신설해야 함. 이 확장을 D1(첫 소비자)에 귀속, A1의 comparison boost-only와
  공유. 용어 주의: 여기서 확장하는 것은 recommendation **evidence family**
  (frozenset)이지 SignalFamily(enum, 상품신호)가 아님 — 구현자 혼선 방지
- 완료 기준: 유사도 계산 모듈 + boost-only 버킷 + family 계약 + 골든
  스냅샷에서 발화 사례 또는 "데이터 희소로 연기" 판정 기록 + 단독 자격
  fail 계약 테스트
- 선행: E0 (family 확장 계약)

### D2. co-mention 상품-상품 유사도
- 근거: 같은 리뷰에 동시 언급된 상품쌍은 canonical_fact.review_id self-join
  으로 이미 계산 가능한데 구현 0 (진단 §2). co-use edge(실SKU 0)의 현실적
  대체재
- 작업: 동시언급 집계(최소 지지도 게이트) → top_comention_product_ids 서빙
  필드 or 기존 coused 필드 재사용 결정 → coused_product_bonus 배선 부활
- 완료 기준: 실 fixture에서 동시언급 쌍 실측 보고 + 유의미하면 배선,
  희소하면 실데이터 대기 판정
- 주의: 부정 문맥(비교 비하) 오염 — polarity 필터 동반

### D3. purchase_event 스코어링 그레인 전환
- 근거 **[크로스리뷰 정정]**: "죽은 경로"는 과장 — ingest/loader/brand-
  confidence 소비는 실재함. 정확한 문제는 **스코어링 그레인이 요약
  (user_summary)이라 이벤트 파생 신호를 쓰지 않는 것**. 액션 스트림(E)의
  구조적 선례(이벤트→파생 feature 패턴)가 됨 (진단 §6)
- 작업: personal_agent_adapter의 요약 의존을 이벤트 그레인 파생으로 점진
  전환(병행 기간 두고 diff 검증)
- 완료 기준: 이벤트→재구매/충성도 feature 파생이 기존 요약 결과와 정합 +
  이벤트 경로가 기본이 됨
- 선행: 원천 이벤트 타임스탬프 데이터 확보(사용자 확인 필요)

## Track E — 액션/인텐트 레이어 (L, 변곡점)

### E0. evidence-family 확장 계약 명문화 (S — Track A와 동시 착수 권장)
- 근거: family 3분류와 신규 추가 조건이 코드+테스트에만 있고 문서 계약 부재
  (진단 §6-4) — E·D의 신규 family가 임기응변이 되지 않게 선행
- 작업: db_consumer_contract(또는 신규 §)에 성문화 — 단독 자격 가능 여부
  기준, shrinkage/가중 원칙, 회귀 테스트 요구(기대셋 패턴), 명명 규칙
- 완료 기준: 문서 + 검증 테스트 참조 링크

### E1. 이벤트 스키마 + 유저 평면 확장 (안 A 채택)
- 설계 (진단 §6 안 A — 최소 침습):
  - 액션 = 유저 평면 신규 behavior edge: `CLICKED_PRODUCT` / `CARTED_PRODUCT`
    / `BROWSED_REVIEW` (User→Product, provenance source_domain=user,
    source_kind=event, dedup 키 = event_id)
  - 인텐트 스테이지 = **user state** (`INTENT_STAGE: explore|deliberate|
    purchase` — 가변 상태이므로 canonical_fact 불변 invariant 회피, 상태
    +이력 테이블)
  - 이벤트 원시 테이블은 **집계-전용 설계 + 첫날부터 TTL** (리뷰 raw의
    append-only 무기한 패턴 금지 — F1 리스크 ①). 세션 개념(session_id)
    도입 여부는 이벤트 스펙 확정 시 결정
  - 기존 recency-decay/frequency-cap/source-weight 집계기 재사용
- 완료 기준: 스키마 DDL + adapter 계약 + 합성 이벤트로 e2e (실 이벤트 모델
  은 외부 개발 중 — 계약 먼저 고정해 인터페이스 리스크 제거)
- 선행: E0, 이벤트 스펙(필드/볼륨/전달 방식) 사용자 확인

### E2. BEHAVIORAL_INTEREST family + 스코어링 편입
- 액션 유래 신호의 계약: **단독 자격 불가**(스쳐본 것이 추천 자격이 되면
  안 됨 — source_review_*와 동급 규율), 결합 시 보정/부스트만. 구매 확정
  (PURCHASE_BEHAVIOR)과 명확 분리
- 완료 기준: 계약 테스트(단독 자격 fail 케이스) + 기대셋 갱신

### E3. funnel_stage → 프리셋 자동 라우팅
- 근거: 프리셋 메커니즘은 준비됨, 병목은 인텐트 신호 (진단 §6). 매핑:
  탐색→discovery / 고민→**신규 중간 변형**(trusted에서 novelty 완전 차단을
  완화한 "비교 검토" 프리셋 — 현 trusted는 검토 단계에 과함) / 구매 직전→
  balanced+소유·재구매 강화
- 작업: funnel_stage 추정 규칙(최근 액션 패턴 기반, 단순 규칙 시작) →
  /api/recommend·/api/ask에 stage 힌트 파라미터 + 자동 프리셋 선택(사용자
  수동 선택이 항상 우선) + `QueryInterpretation.intent`와 이름 분리
  (`funnel_stage` 필드)
- 완료 기준: stage별 프리셋 자동 적용 e2e + 데모 UI 표시("탐색 중이시네요
  — 새로운 발견 위주로")
- 선행: E1/E2

### E4. 실시간성 원칙 (결정 기록)
- 배치+5분 캐시 기본 유지. 즉시성 요구("방금 본 상품 제외/반영")는 재집계
  실시간화가 아니라 **요청 시점 오버레이**(클라이언트가 최근 액션 id 전달
  → 서버 후처리)로. 스트림 인프라 도입은 이벤트 볼륨 실측 후 재평가
- 완료 기준: DECISIONS 1건

## Track F — 인사이트 서피스 (후순위, 수요 확인 시)

- F1. 캘린더 버킷(주/월) 집계 추가 → 트렌드 비교("이번 달 보습 언급 급증")
- F2. 세그먼트×상품 교차 집계(skin_type 코호트 통계 — 현행 5행 규칙표 대체)
- F3. routine_step/day_part 보존(date_splitter가 구분해놓고 버리는 것 영속화)
  → 루틴 신호("세안 후 단계에서 함께 언급")
- 착수 조건: 상품기획/브랜드 인사이트 소비자(사내 조직)의 실제 수요 확인
  — 진단 U4에서 이 유스케이스가 '하'인 이유가 정확히 이 부재들

## 시퀀싱과 배치

| 배치 | 내용 | 노력 |
|---|---|---|
| **P7-1** | A1+A2 (comparison·modes — scorer/reranker mode 배선 포함) / A4+A5 (audit·wide 베이스라인·정합 CI) + E0 (계약 명문화) | S×5 |
| **P7-2** | B1 (경로 통합+형태론) / C1 (concern 소생+타입 해소) — **[크로스리뷰] 둘 다 KG normalize 계층(mention_extractor 등)을 건드려 파일 경합 위험 → 직렬 실행 또는 mention_extractor 소유권 B1 배정·C1은 deriver/relation부만** | M×2 |
| **P7-3** | B2 (개념 접힘) / C2 (게이트 보완 — DECISIONS 선행, A4 천장 실측 필요) | M×2 |
| **P7-4** | D1 (user-user + boost-only 버킷 신설) / D2 (co-mention) | M×2 |
| **P7-5** | E1~E4 (액션/인텐트) — **이벤트 스펙·데이터 확보 후** | L |
| 조건부 | B3(승인 후) / D3(데이터 후) / C3(실적재 전 1회) / F(수요 시) | — |

각 배치는 CLAUDE.md 사이클(구현→크로스리뷰→수정→게이트→보고) 적용.

## 결정 — ✅ 사용자 확정 (2026-07-13)

1. **B1 형태론 = (a) 보수적 어미 스트리핑 채택** (의존성 0). kiwipiepy는
   접기+등재 후 잔여율 실측 뒤 재검토
2. **C2 게이트 방향**: P7-3 착수 시 DECISIONS 초안 제출 → 승인 흐름 유지
3. **E1 이벤트 스펙 = 스킵 확정** — 외부 액션/인텐트 모델 개발·스펙 확정
   시 착수. **Track E 전체 보류** (E0 계약 명문화만 P7-1에서 선행 — 이는
   D 트랙의 신규 family에도 필요)
4. **D 트랙 판정 위임 = 동의** — fixture 희소 시 "배선 완성 + 실데이터
   대기"로 자동 전환

실행 방식: 구현 = Opus(P7-1 지정)/Sonnet, 각 배치 후 **Fable 리뷰/검토** +
게이트. P7-1 → P7-2 → P7-3 → P7-4 순차, E는 보류.

## 리스크

- **게이트 완화(C2)의 노이즈 유입** → 기대셋·스냅샷 회귀를 완료 기준에
  포함, synthetic_ratio 게이트 불변
- **형태론 접기의 오접힘**(다른 개념이 같은 어간으로) → 어간이 사전에
  존재할 때만 접는 보수 규칙 + 3.3 코퍼스 baseline 회귀
- **이벤트 볼륨 폭발** → E1의 TTL-first 설계 + Phase 5 retention 구현과
  연계(실데이터 적재 시 착수 조건이 이미 충족되는 시점)
- **신규 family 남용으로 evidence-first 희석** → E0 계약이 방어선, 모든
  신규 family는 "단독 자격 불가"에서 시작
- 도메인 큐레이션(C1 성분-고민 맵) 품질 → 도메인 감수자 필요(사용자/조직)

## 검수 기록

### 계획 크로스리뷰 — 2026-07-13 (Opus Plan Reviewer, 실코드 대조)

**판정: APPROVE-WITH-CHANGES** → 전부 본 계획에 반영 완료:
- **치명 갭(B1)**: "어간은 사전에 있다" 전제가 미해결 2위 `순하고`에서 반증
  (어간 사전 부재 = 사전 갭) + 진짜 근본 원인은 **keyword 해소 경로 이중화**
  (quarantine 생성 경로가 사전을 아예 조회 안 함) → B1을 "경로 통합 → 접기
  (부정 문맥 방어) → 어간 등재" 3단으로 재정의, 50%는 합산 목표로
- A1 "마지막 배선" 과장 → scorer feature 신설 + 유저 앵커 + boost-only 자격
  결정 추가 (점수 0-자격만 후보 부작용 방지)
- A2는 config-only 아님(scorer/reranker mode 배선 신설) 명시
- C2의 wide 회귀 베이스라인 부재 → A4가 생성, 30% 목표는 천장 실측 후 확정
- "단독 자격 불가"는 문서 강제 불가 → build_candidate_eligibility에
  boost-only 버킷 코드 신설(D1 귀속), SignalFamily/evidence family 용어
  이원화 명시
- P7-2 병렬의 mention_extractor 파일 경합 → 직렬화/소유권 배정
- D3 "죽은 경로" 과장 정정(스코어링 그레인 문제로), C1은
  ToolConcernSegmentDeriver 재사용 명시, 안전 확인: C1 타입해소는 dict
  멤버십 게이트+provenance 보존 시 evidence-first 무저촉(재타이핑 가능
  비율 ~5-15%로 기대 관리), E 트랙 스키마 적합·TTL은 Phase 5 기계 재사용

## 완료 보고 (실행 후 누적)

_(비어 있음)_
