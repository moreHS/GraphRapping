# 06. 그래프·온톨로지 구조 진단 — 연결성 가치 실측

작성: 2026-07-13 · 작성 주체: Fable(종합 판정) + 리서치 2트랙(Opus: 온톨로지·구조 / Sonnet: 활용성·확장성, 전부 실측 기반) · 후속 계획: `plans/2026-07-13_phase7_graph_intelligence.md`

**질문**: 이 시스템은 그래프인가? 그래프를 쓰는 이유(연결성 기반 인사이트·탐색·추천)가 발현되고 있는가? 유저 액션/인텐트 스트림(구매/탐색/고민 스테이지, 장바구니·클릭·리뷰탐색 액션)을 수용할 준비가 되어 있는가?

## 1. 한 줄 판정

> **지금의 GraphRapping은 "그래프"가 아니라 "provenance 인덱스가 달린 구조화 근거 저장소"다.** 이는 리뷰-only 데이터의 정직한 반영이며 결함이 아니다 — 그러나 "그래프여서 가능한 것"(연결성 신호가 추천을 바꾸고, 다중 관계를 탐색하고, 전파를 관찰하는 것)은 **실측상 아직 0건 발화**다. 유저 액션 스트림 유입이 이 시스템이 진짜 그래프가 되는 변곡점이고, 그 전에 죽은 어휘·배선·링킹 바닥을 정비해야 그 순간 가치가 터진다.

## 2. 연결성 가치 실측 (가장 중요한 수치)

골든 유저 6종 × 탭 7종 = 42 시나리오, 후보 풀 140행 전수 (dense_golden, 기존 audit 스크립트 재실행):

| 실측 | 결과 |
|---|---|
| 연결성 고유 신호(concern_bridge/coused/tool/comparison)가 순위에 기여한 건수 | **0 / 140 (전수, 예외 없음)** |
| top-10에서 그래프 유래(REVIEW_GRAPH_RELATION) 등장률 | 72.9% (후보 자격은 그래프가 다수 공급) |
| **top-3**에서 상품마스터 vs 그래프 | **PRODUCT_MASTER_TRUTH 62.3% > REVIEW_GRAPH_RELATION 60.4%** — 최상위 쟁탈전은 카탈로그 진실이 이김 |
| score layer 순기여 | source_trust 33.9% > 그래프 28.5% > 구매행동 16.7% > 마스터 15.9% |
| "항상 켜지는" 비개인화 신호의 잠식 | source_popularity(가중치 0.03, hit 100%) 총기여 ≈ keyword_match(가중치 0.16, hit 48.6%) 총기여 |
| 유스케이스 5종(위젯/검색/CS/기획인사이트/AmoreSim) 중 그래프 강점 '상' | **0개** (하 2, 중 3) |

연결성 신호 0건의 원인 분류 (증상이 아니라 원인별로 고쳐야 함):

| 신호 | 원인 |
|---|---|
| coused(함께 사용) | **데이터 부재** — 실 SKU co-use edge 0건 (4.0 audit 재확인) |
| comparison(비교) | **구현 부재** — 데이터 8건·서빙 필드·family 분류 다 있는데 candidate_generator가 안 읽음 (마지막 배선 누락). `modes.compare` 설정도 죽은 설정 — COMPARE 모드는 실제로 EXPLORE와 동일 동작 |
| tool(도구) | **구현 부재** — user adapter에 "tool" 문자열 0건 (preferred_tool_ids 공급 경로 없음) |
| concern_bridge(고민 브리지) | **데이터 희소** — 브리지 맵이 하드코딩 5쌍, 6개 골든 프로필 전원 교집합 0 |

## 3. 온톨로지 설계 소견 — 어휘의 대량 사멸

어휘가 3층(NER/BEE 원시 코드 → 개념 타입 → 사전)으로 분리돼 있고 **층간 정합 검증이 없다** (브리지는 코드 상수 2개뿐).

| 실측 | 수치 |
|---|---|
| predicate contract의 object type 20종 중 실제 생성되는 것 | **6종** (BEEAttr/Ingredient/Keyword/Product/ReviewerProxy/TemporalContext) |
| **Concern / Goal / UserSegment / Tool 노드 생성** | **0건** (kg_on·kg_off 공통) — signal family 4종(CONCERN_POS/NEG, SEGMENT, TOOL) 전량 死 |
| projection_registry 170행 중 신호 방출 행 | 34행 (20%) |
| relation_canonical_map 파일 내부 오기 | meta "65" vs 실제 68 엔트리 — 정합 검증 부재 실증 |
| 고아 entity 타입 | Color/Volume/AgeBand/Event — 노드는 생기는데 받아줄 projectable predicate 없음 (`has_attribute\|Product→Color` 149건 격리) |
| contract 반려로 버려지는 정보성 관계 | `used_by\|Product→ReviewerProxy` 900건, `brand_of` 54건 — **NLP 방향/타입이 contract와 안 맞아** 반려 (모델이 좋아져도 타입 해소층이 없으면 계속 0 — 구조 리스크) |

**유저-상품 개념 축 단절**: 유저는 concern/goal 축으로 표현되고 상품은 (현 데이터상) BEE 축으로만 표현됨 → Concern 노드가 상품 쪽에 0개라 **그래프상 유저와 상품이 개념으로 만나지 못함**. 유일한 결합은 스코어링 시점의 하드코딩 브리지 5쌍.

**긍정 소견**: 3중 승격 게이트 + signal_evidence provenance 정본(L2까지 원문 추적 실측 확인), config 기반 온톨로지(contract 70행·projection 170행 — 확장이 CSV 수정), quarantine-first(실패를 버리지 않고 격리 — 관측 가능), **유저 평면의 분리 설계**(typed edge + recency/frequency/source 가중 — 이벤트 수용에 그대로 재사용 가능).

## 4. 엔티티 링킹(동의어) 실측 — 병목은 사전이 아니라 형태론

| 실측 | 수치 |
|---|---|
| 미해결 표면형 (unknown_keyword, 906리뷰) | 2,784건 / distinct **2,482개** |
| 미해결 상위의 성격 | **압도적으로 한국어 굴절형** — `촉촉하고`(36) `순하고`(13) `촉촉해서`(12) `촉촉해요`(10)… 어간(`촉촉`)은 사전에 **있음**, 활용형이 미등재 |
| 사전에 있는데도 격리 | `무향`(8) `진정`(3) — KG 추출 표면형과 사전 조회 표면형의 정규화 불일치 누수 |
| 동일 개념의 ID 분산 | `보습`→kw_moisturizing / `촉촉`→kw_moist / `촉촉한`→MoistLike — 사실상 한 개념이 3개 keyword_id |
| 한 표면형의 taxonomy 병존 | `보습`이 goal/keyword/concern 3곳에 각각 다른 id로 — 접힘 규칙 비일관 |
| 유저측 개방 어휘 잔류 | `피지`/`등드름`/`냄새` 등 미등재 concern이 resolved id와 혼재 |

**판정**: 사전 추가로 해결 불가한 오류 클래스가 지배적 — ① 교착어 굴절(어간당 활용형 수십 개, 열거 불가) ② 개방 어휘(사전은 항상 사후적) ③ 축 불일치(사전이 아니라 브리지의 문제). **임베딩(3.2, 승인 대기) 이전에 형태론 정규화 계층이 먼저다** — 이것만으로 미해결의 다수가 해소되고, 임베딩은 그 잔여분에 쓰는 게 맞다.

## 5. 3레이어 승격 — 실데이터형 분산에서의 붕괴

승격 게이트 자체는 리뷰 코퍼스에 타당하나, **카탈로그 크기가 승격을 지배**한다:

| 동일 906리뷰 | dense (32상품) | wide (517상품) |
|---|---|---|
| promoted(all) | 305건 | **70건** |
| 서빙 top_bee_attr 보유 상품 | 29/32 = **91%** | 26/517 = **5%** |

→ 리뷰가 분산되면 `distinct_review≥3` 절대 임계에 대부분 걸려 **리뷰 유래 신호가 서빙에 거의 도달하지 못하고, 추천 근거가 사실상 카탈로그 진실로 수렴**한다. 실데이터(수만 상품)에서는 이 붕괴가 더 심해진다 — 게이트의 카탈로그-인지 보완이 필요한 이유.

기타: polarity는 agg에서 (pos−neg)/total로 압축(부호 보존, 개별 극성 소실 — 수용 가능), 강도·negated는 agg 미반영, 시간성은 rolling 3윈도우로만(캘린더 버킷 없음 → 트렌드 분석 불가), `date_splitter`가 routine_step/day_part를 구분해내고도 **영속화 직전에 버림**(루틴 신호 잠재 자산).

## 6. 액션/인텐트 스트림 수용성

**순풍**: 유저 평면이 이미 이벤트가 필요로 하는 구조를 갖춤 — User→Product typed edge(OWNS/RECENTLY_PURCHASED/REPURCHASES_*) + recency-decay·frequency-cap·source-weight 집계기 + `FactProvenance.source_domain`에 user/system 예비. `purchase_event_raw` 테이블도 이벤트 그레인으로 이미 존재(단, 실배선 없는 죽은 경로).

**권장 수용 형태 (안 A — 최소 침습)**: 액션 = 유저 평면 신규 behavior edge(`CLICKED_PRODUCT`/`CARTED_PRODUCT`/`BROWSED_REVIEW`), 인텐트 스테이지 = 신규 **user state**(가변 상태 — canonical_fact의 "생성 후 불변" invariant와 충돌하므로 fact가 아니라 상태+이력으로). enum+adapter+config 확장으로 수용 가능.

**충돌 지점 (선행 정비 필수)**:
1. **볼륨**: 이벤트는 리뷰의 10²~10³배 — all-window 무한 누적(F1)과 quarantine TTL 부재가 그대로 폭발. **이벤트는 첫날부터 TTL/집계-전용 설계 필수**
2. 승격 게이트 `distinct_review≥3`는 리뷰 논리 — 이벤트에는 빈도/최근성 게이트(유저 평면이 이미 보유) 적용
3. dedup 키가 review_id 기반 — 이벤트는 event_id 키 필요(유저 평면은 이미 해결된 패턴 보유)
4. 계약: 액션 신호는 기존 PURCHASE_BEHAVIOR에 합치지 말고 **신규 family(BEHAVIORAL_INTEREST)** + "단독 자격 불가" 조항 — 그런데 **evidence-family 확장 조건 자체가 아직 문서화돼 있지 않음**(코드+테스트에만 존재) → 명문화 선행
5. 실시간성: 액션 대부분은 배치+5분 캐시로 충분. 즉시성이 필요한 것("방금 본 상품 제외")은 재집계 실시간화가 아니라 **요청 시점 오버레이**로
6. `QueryInterpretation.intent`(recommend|search)와 퍼널 스테이지의 **이름 충돌 주의** — `funnel_stage` 별도 필드로

**퍼널 × 프리셋 자연 결합**: 탐색→discovery / 고민→trusted 계열 변형(현 trusted는 novelty 완전 차단이라 검토 단계엔 과함 — 중간 변형 필요) / 구매 직전→balanced+소유·재구매 강화. 프리셋 메커니즘은 준비됨 — 병목은 프리셋이 아니라 **그걸 촉발할 인텐트 신호의 부재**.

## 7. 강점 / 약점 / 구조 리스크 (실측 인용)

**강점**: ① evidence-first + L2 원문 추적 실증 ② config 온톨로지(확장=CSV) ③ 유저 평면 분리 + 가중 기계(이벤트 재사용 가능) ④ quarantine-first(미해결 2,482건이 관측 가능한 자산) ⑤ 한계의 정직한 문서화(4.0 audit 등)

**약점**: ① concern/goal/tool/segment 어휘 전면 死(4 family 生 0) ② wide 서빙 도달률 5% 붕괴 ③ 형태론 천장(미해결 상위 전량 굴절형) ④ 어휘 3층 정합 검증 부재(65vs68 오기 실증) ⑤ 연결성 신호 스코어링 기여 0/140

**구조 리스크**: ① 이벤트 유입 시 무한 누적 폭발(F1 미구현 × 10²배 볼륨) ② NLP 모델이 개선돼도 타입 게이트 반려로 어휘 死 고착(타입 해소층 부재) ③ 유저-상품 개념 축 영구 단절(Concern 노드 0)

## 8. 종합 — 발전 방향의 논리

1. **죽은 것부터 살린다** (S: comparison 배선, modes 정리, tool 결정, 정합 CI) — 비용 대비 즉효
2. **링킹 바닥을 다진다** (형태론 정규화 → 개념 접힘 → [승인 후] 임베딩 잔여분) — 모든 상류 신호의 품질 바닥
3. **개념 축을 결합하고 서빙 도달을 회복한다** (concern/goal 소생 + 타입 해소층 + 게이트 보완) — "리뷰 그래프"가 실데이터에서 살아있게
4. **진짜 그래프 신호를 만든다** (user-user 유사도, co-mention 상품 유사도, purchase_event 실배선) — "그래프여서 다른 결과"의 첫 사례
5. **액션/인텐트 레이어** (계약 명문화 → 이벤트 스키마+TTL → BEHAVIORAL_INTEREST → funnel×preset) — 시스템이 진짜 그래프가 되는 변곡점
6. 인사이트 서피스(캘린더 버킷/세그먼트 교차/루틴)는 위가 자리 잡은 뒤

상세 실행 계획: **`plans/2026-07-13_phase7_graph_intelligence.md`** (Phase 7)
