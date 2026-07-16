# Phase 8 — 공유 노드 상품 유사도 그래프 (상세 구현계획, 확정본)

작성: 2026-07-15 · 상태: **확정 (사용자 결정 4건 완료, 논의 로그 참조)** ·
근거 분석: `fable_doc/07_shared_node_projection_analysis.md` ·
설계 논의·근거: `DECISIONS/2026-07-15_phase8_shared_node_design_dialogue.md` ·
상위: `fable_doc/03_improvement_plan.md` §Phase 8

## 0. 확정 설계 (결정 4건 반영)

**공유 노드 2홉을 명시적 상품-상품 유사도로 실체화** — canonical_fact(상품→속성
이분 star)에서 상품 A·B가 공유하는 속성 노드를 IDF 가중해 유사도 점수로 투영.
그래프 DB 불요(self-join). Phase 7 D1/D2와 동일 계열(D4)이되 **데이터가 준비된
첫 연결 신호**.

**핵심 원칙(논의 확정)**:
1. **유사도 = Σ IDF(공유 노드) [× polarity 일치]**, 랭킹 + top-N. 하드-AND·노드
   병합·하드 카테고리 게이트 **없음**. 다축 공유는 점수로 저절로 반영
2. **카테고리 게이팅 = 소비 맥락 파라미터**(계산 속성 아님): 유사상품(G2/G3)=ON /
   일반추천 boost(G4)=OFF(다양성) / 쿼리(G5)=상류 게이팅. G1은 카테고리 비종속 +
   카테고리 노출
3. **축(전부 점수 노드)**: keyword(bee_attr 복합키·주력)·ingredient(주력)·
   category·brand·main_benefit. **bee_attr(클래스명) 점수 제외·keyword 스코핑 전용**.
   polarity(POS/NEG) 일치 반영
4. **노이즈 제어 = IDF**(흔한 노드 자동 감쇠 — 대형 브랜드/흔한 성분/보편 속성).
   하드 배제 안 함
5. **저장 = ephemeral**(serving 로드 시 attach), 영속 테이블 보류
6. **evidence-first**: 모든 유사도에 `shared_axes`(공유 노드·근거) 동반, 비면 emit 안 함

## Track G — 상세 구현

### 활성화 훅 (전 트랙 공통) ★
D1/D2 attach가 실서빙 호출 0건(dormant)인 함정 회피. **`src/web/serving_store.py`
로드 경로에서 1회 호출**: DBServingStore `_fetch_products` 직후(+`wrapped_signal`
keyword sidecar 조회) / DemoServingStore는 **`product_signals` 인덱스 구성 이후**
(demo는 serving_products→product_signals 순서라 attach를 signal index 뒤에 배치 —
리뷰 반영) `attach_similarity_signals(products, raw_keyword_signals=...)`.
corpus-level 1회(IDF·라벨 인덱스 집계 지점과 일치).
- **안전 계약**: candidate_generator는 `similar_product_ids`를 안 읽음 → **G1~G3
  활성화는 랭킹 스냅샷 재승인 불필요**(추천 점수 불변). 스냅샷 영향은 G4(boost)부터.
  부수효과: `/api/products` 응답에 `similar_product_ids` additive(하위호환)

### G1. 유사도 계산 모듈 (신규 `src/rec/product_similarity.py` — D1 옆)
위치 = **src/rec**(category_groups 게이팅이 rec 레이어, mart→rec 임포트 역전 회피).

**노드 키 규약** (축별 구분 유지, D1 `axis::id` 네임스페이스 재사용):
- `keyword::{bee_attr_id}:{keyword_id}:{polarity}` — **복합키**(bee_attr 스코핑 +
  극성, 결정4 확정 유지). 근거: "가볍다"가 제형/발림성/패키지에서 다른 의미 →
  bee_attr로 구분해야 함(사용자 확정, 2026-07-15 논의). B2 alias 정규화(canonical
  keyword) 적용 **후** bee_attr·polarity로 스코핑
  - **★ 데이터 소스 (3자 리뷰가 잡은 사실제약의 해소)**: 집계(`aggregate_product_
    signals` groupby=(product,edge_type,dst_type,dst_id))가 **bee_attr·polarity를
    버리므로 서빙 `top_keyword_ids`엔 없음**. 따라서 keyword 축만은 **raw
    `wrapped_signal` sidecar**에서 소싱 — DB: `wrapped_signal`(컬럼 bee_attr_id/
    keyword_id/polarity, `idx_ws_product` 인덱스) per-product 조회(`fetch_product_
    signals` 패턴), demo: `demo_state.product_signals`. **기존 집계/서빙 keyword
    파이프라인 무변경**(회귀 0) + 서빙에서 손실된 polarity 복구. (Option A[집계
    변경=회귀]·C[병합="가볍다" 박살] 기각, B[sidecar] 채택)
- `ingredient::{ingredient_concept_id}` — 서빙 프로파일 소스
- `category::{category_concept_id}` — 서빙 프로파일
- `brand::{brand_concept_id}` — 서빙 프로파일
- `goal::{main_benefit_concept_id}` — 서빙 프로파일
- **bee_attr는 점수 노드 아님** — keyword 스코핑에만 사용

**함수**:
```
build_product_nodes(product_profiles, raw_keyword_signals) -> dict[pid, set[node_key]]
    # ingredient/category/brand/goal ← product_profiles, keyword(복합키) ← raw sidecar
build_idf(product_nodes) -> dict[node_key, float]                # 코퍼스 df → IDF=log(N/df)
build_similarity_signals(product_nodes, product_profiles, *, idf,
    category_gate: bool = False, min_score, top_n) -> dict[pid, list[SimilarProductSignal]]
attach_similarity_signals(product_profiles, signals) -> None     # ephemeral in-place
```
- `raw_keyword_signals`: `{pid: [(bee_attr_id, keyword_id, polarity), ...]}` — 활성화
  훅이 DB `wrapped_signal`/demo `product_signals`에서 조달해 주입
- 축별 역인덱스(`node_key → [pid]`) → 공유 상품쌍만 순회(D1/D2 패턴, O(노드 카디널리티²))
- score(A,B) = Σ_{공유 node} IDF(node). polarity는 복합키에 포함되므로 극성 불일치는
  애초에 다른 노드 → 자동 미공유(별도 factor 불필요)
- top_n 이웃 절단, min_score 하한. **이웃 비대칭 정책(리뷰 반영)**: A의 top_n에 B가
  있으나 B엔 A 없을 수 있음 → 유사상품 서피스(G2/G3)는 **대칭 union** 노출(양방향
  중 하나라도 top_n이면 표시), G4 boost는 앵커 기준 단방향으로 충분
- **SimilarProductSignal.to_dict()**: `{product_id, neighbor_name(representative_
  product_name), score, shared_axes:[{axis, node_key, label, idf}]}`. **label 소스
  (리뷰 반영)**: 서빙엔 concept id뿐이라 라벨은 load-time 라벨 인덱스(DB
  concept label / demo label sidecar)에서 조달, 없으면 concept id suffix fallback.
  이웃 라벨·근거 자체 포함(G2가 코퍼스 접근 없이 렌더). **shared_axes 비면 미방출**(계약)

**완료 기준**:
- dense/wide 실측: 각 축 IDF 가중 후 **기준 상품별 top-N 품질** — (a) category_gate
  =True 시 top-N 동일 카테고리 지배 (b) category_gate=False 시 앵커와 **공유 IDF 합**
  (개수 아님) 상위 상품이 top (c) **커버리지 하한**: 유사 이웃 ≥1 보유 상품 비율이
  wide **≥60%**(gate ON, 동일군 이웃 존재 기준)·dense ≥90% 미달 시 축/임계 재조정
  (빈 위젯 방지 — 리뷰 반영) (d) keyword 복합키가 raw sidecar에서 구성됨·bee_attr는
  점수 노드 아님 확인
- 무이웃 상품 UI 정책: G3는 빈 배열 200(섹션 미노출), 존재하지 않는 id는 404
- 단위테스트: IDF 계산, 축 네임스페이스 분리, **keyword 복합키(같은 keyword_id 다른
  bee_attr → 다른 노드; "가볍다"류 케이스 고정)**, 극성 다르면 다른 노드, category_gate
  on/off, shared_axes 강제(비면 미방출), 이웃 union 대칭, attach 1회성

### G2. 그래프 뷰어 상품-상품 서브그래프 (`_build_corpus_graph` 확장) — 1차 활용
유사상품 맥락 → **category_gate=True**.
- server.py `_build_corpus_graph`에 유사 상품 섹션: attach된 `similar_product_ids`
  순회 → 이웃 노드(neighbor_name) + `SHARES_ATTRIBUTE` 엣지 + tooltip에 `shared_axes`
  ("보습·저자극 공유")로 "왜 연결되나"를 그래프가 답함
- **graph_view.js 실제 작업(리뷰 정정 — "색 1개 외 무수정"은 틀림)**: 현재
  shared_axes를 Cytoscape data로 안 넘기고 **tooltip 이벤트 자체가 없으며** 모든
  엣지에 화살표를 그림. 따라서 (1) 엣지 data에 shared_axes 전달 (2) tooltip(또는
  근거 표시) 이벤트 신설 (3) `SHARES_ATTRIBUTE` 선택자 무방향 스타일 — 실제 JS 편집
  필요(색 1개 아님)
- **view 계약 하나로 고정**(P8-2 전): 신규 view 모드 or corpus 확장 중 택1 확정
- 완료 기준: 브라우저에서 앵커 상품 → 동일 카테고리 유사 상품이 근거와 함께 시각화,
  기존 그래프 뷰어 회귀 없음

### G3. 유사 상품 위젯 API — 1차 활용 (G2와 같은 배치)
유사상품 맥락 → **category_gate=True**.
- `GET /api/products/{id}/similar` — product_similarity 직접 호출, Scorer/eligibility
  미경유(item-to-item, search.py 익명 설계 논리). top-N + shared_axes 반환
- 프론트: 상품 상세/탐색기 "비슷한 상품" 섹션(shared_axes 칩)
- 완료 기준: 근거 있는 유사 상품 반환 + e2e

### G4. 일반 추천 re-rank boost — 후순위, **category_gate=False**
"A유저 owned 상품과 연관 많은 상품" — **다양성 위해 카테고리 비종속**(결정1 통찰).
- owned_product_ids 앵커 → 속성-유사 후보 부스트. `BOOST_ONLY_TYPES`에 `similar`
  추가(**`BOOST_ONLY_ADMISSIBLE_TYPES`엔 절대 미포함** — 단독 자격 불가), top-level
  `similar_product_weight`(features 맵 밖), family명 `PRODUCT_SIMILARITY_AFFINITY`
- **explainer `_EDGE_MAP["similar"]` 추가 필수(리뷰 반영)** — collab/comention 선례처럼.
  없으면 "점수 기여하나 설명 안 뜨는" score/explain 불일치. §13 provenance는
  shared_axes만으론 signal→fact 조건 미충족 → candidate overlap·scorer contribution·
  explainer path·provenance 추적을 세트로 구현
- diversity 페널티(reranker)와 상충 → boost만 먼저
- 완료 기준: 단독 자격 fail 계약(전 모드) + 기대셋/스냅샷 재승인. owned edge 1/50이라
  **dormant 가능성**(D1 전철) → 그 경우 배선+대기 판정

### G5. 쿼리 기반 확장 — 후순위, **게이트=쿼리 상류**
- /api/search·/api/ask 결과 뒤 "관련 상품 더보기" 2차 섹션. 쿼리에 카테고리 있으면
  이미 그 풀 → 별도 게이트 불필요. 1차 개념일치와 근거 구분
- 완료 기준: 2차 섹션 shared_axes 동반, 1차 정렬 불변

## 시퀀싱

| 배치 | 내용 | 노력 |
|---|---|---|
| **P8-1** | G1 계산 모듈(IDF·복합키·polarity·category_gate 파라미터·ephemeral) + 실측 검증 + 단위테스트 | M |
| **P8-2** | G2 그래프 뷰(gate ON) + G3 위젯 API(gate ON) — 1차 활용 | M |
| **P8-3** | G4 일반추천 boost(gate OFF, 조건부) + G5 쿼리 확장 | M, 후순위 |

각 배치 CLAUDE.md 사이클(구현→크로스리뷰→수정→게이트→보고). P8-1이 관문 —
실측이 예상과 다르면 스코프 재조정. P8-2의 server.py/static 파일은 G2·G3가
겹치므로 순차 or 신중 분리.

## 리스크·가드

| 리스크 | 가드 |
|---|---|
| 허브 노드(흔한 속성/대형 브랜드/베이스 성분) 과다연결 | IDF 자동 감쇠 (하드 배제 대신 — 결정1) |
| 교차 카테고리 오연결 | 유사상품 맥락 category_gate=True + 그 외 맥락은 점수로 자연 하위화 |
| keyword 오뭉개짐(같은 값 다른 bee_attr) | (bee_attr_id, keyword_id) 복합키 — 계약 테스트 |
| evidence-first 우회 | shared_axes 강제(비면 미방출) + G4는 boost-only §13 |
| 스키마 조기 확장 | ephemeral 우선(D1/D2 원칙) |
| SimRank화/순환 | 2홉 고정, 재귀 금지 |
| 활성화 누락(dormant 함정) | serving_store 로드 훅 명시 + 안전 계약(G4 전까지 스냅샷 무영향) |
| keyword 저커버리지 | 데이터 성숙으로 회복(설계 문제 아님) — 커버리지 지표 관찰 |

## 향후 (Phase 8 이후)

- Track E(액션) 도래 → G4 앵커 owned→viewed/carted 확장 → user→product→유사product
  3홉 자연 확장(재설계 불요)
- 영속 `product_similarity` 테이블(위젯 SLA 필요 시) — DDL+repo+계약 3자 동기
- 상품 클러스터 인사이트(Track F, 수요 확인 시)
- Relation 대비 차별점 실현: IDF로 허브 노드 제어(Relation 미해결) + evidence 근거
  (shared_axes) + polarity/bee_attr 스코핑

## 검수 기록

### 1차 크로스리뷰 — 2026-07-15 (Opus, APPROVE-WITH-CHANGES)
wide 517 핵심 수치 독립 재현. 치명갭(활성화 훅) + G1 위치(src/rec) + 이웃 라벨/
shared_axes 자체포함 + df/카테고리 게이팅 분리 + 커버리지 하한 반영.

### 사용자 논의 확정 — 2026-07-15
결정1 메커니즘 재정립(가중 합, 하드-AND/게이트 제거) + 카테고리 게이팅 맥락
파라미터화 + 브랜드/카테고리 유지(IDF) + keyword 주력·bee_attr 복합키 스코핑.
상세 근거: DECISIONS/2026-07-15_phase8_shared_node_design_dialogue.md.

### 3자 재리뷰 (Opus·Sonnet·Codex) + 사용자 재확정 — 2026-07-15
논의록을 확정제약으로 공유해 방향 재설정 금지 프레이밍으로 실행. 3자 전부
APPROVE-WITH-CHANGES, 프레이밍 준수(기각 대안 재론 없음).
- **3/3 수렴 사실오류**: keyword 복합키가 **서빙 프로파일엔 없음**(집계 groupby가
  bee_attr·polarity 버림). → **사용자 재확정: 복합키 유지가 맞다**("가볍다"가 제형/
  발림성/패키지에서 다른 의미, BEE 분류 신뢰, 보습-분산은 사이드이펙트). 병합(C)·
  집계변경(A) 기각. **해소 = raw `wrapped_signal` sidecar**(DB `idx_ws_product`
  인덱스 조회 / demo `product_signals`, 컬럼 bee_attr_id·keyword_id·polarity 확인)
  → 기존 keyword 파이프라인 무변경 + polarity 복구. 위 G1에 반영
- **3/3 확인 안전장치**: candidate_generator가 similar 필드 미독 → G1~G3 스냅샷 무영향
- **반영된 갭**: G2 graph_view.js 실제 tooltip 작업(색 1개 아님)·이웃 union 대칭·
  G4 explainer _EDGE_MAP+provenance·shared_axes 라벨 sidecar·커버리지 하한 수치·
  gate-OFF는 IDF합 평가·G3 빈배열/404·demo attach 타이밍·view 계약 고정
- **문서 갱신**: 03 §Phase 8 stale 갱신, db_consumer_contract §13.2 comparison
  오기(review-graph→boost-only) 정정
- **P8-1 착수 가부**: 조건부 가 → 위 반영 완료로 **착수 가능 상태**

## 완료 보고

### P8-1 (G1 계산 모듈) 완료 — 2026-07-16 (Opus 구현, Fable 리뷰)

**변경**: 신규 `src/rec/product_similarity.py`(442줄) + `tests/test_product_similarity.py`
(20 테스트). 그 외 파일 무접촉(활성화 훅·G2~G5 미착수 — 의도된 dormant 모듈).

**구현 확정 사항**:
- 복합키 `keyword::{bee}:{canonical_kw}:{polarity}` — 신호의 IRI에서 suffix 추출 →
  **bare id에 B2 alias 적용**(alias 맵이 bare 키) → bee_attr·polarity 스코핑.
- keyword 축 = raw sidecar 주입(`raw_keyword_signals`); 모듈은 DB를 읽지 않음
  (DB polarity 질의 배선 = P8-2 훅 몫, docstring 명시). demo 어댑터
  `keyword_signals_from_product_signals` 제공.
- 역인덱스는 **IDF>0 노드만** 포함(df==N 허브는 기여 0 + 쌍폭발 방지) →
  "보편노드만 공유" 쌍 미방출(evidence-first, 테스트 고정).
- `build_similarity_signals`에 `label_index` optional 후행 인자(P8-2 라벨 sidecar
  대비, 기본 concept id suffix fallback). 계획 시그니처 파괴 없음.

**실측 (완료 기준 전 항목 충족)**:
| 기준 | dense_golden(32) | wide(517) |
|---|---|---|
| (a) gate ON top-N 동일 카테고리 지배 | 141/141=100% | 2543/2543=100% |
| (b) gate OFF top == 공유 IDF 합 최대(brute 대조) | 3/3 | 3/3 |
| (c) 커버리지(이웃≥1, gate ON) | 100% (목표≥90%) | **99.0%** (목표≥60%) |
| (d) 복합키: keyword가 >1 bee_attr로 분리 / bee_attr 단독 노드 | 12개 / 0 | 12개 / 0 |

IDF 허브 감쇠 실증: wide 최저 IDF = `brand::이니스프리 1.02`(186상품 쏠림 자동
최하 가중) vs 최고 3.47~6.25(니치). 워크드 예제(100389 한란핸드크림): top-1이
동일 브랜드 아닌 일리윤 로션(공유 keyword 7종, ΣIDF 4.98) > 동일 브랜드+카테고리
쌍(2.97) — "카탈로그가 아닌 공유 속성이 유사도를 결정"이 실데이터에서 성립.

**게이트**: ruff ✅ / mypy 117 ✅ / pytest **1221 passed, 50 skipped, 0 failed**
(기존 1201 + 신규 20). 기존 스냅샷·기대셋 무변경(`similar_product_ids` 소비처 0 —
grep 검증).

### Fable 리뷰 (P8-1) — 2026-07-16, 판정 **APPROVE**

전 소스 정독 + 안전계약 grep 실측. 계획 §G1 대비 이탈 0. 반영/인계:
1. **[반영] symmetrize aliasing** — 역방향 신호가 shared_axes 리스트를 원본과
   공유 → 방어 복사 1줄(리뷰 수정). 게이트 재통과.
2. **[P8-2 필수 체크] polarity 정규화** — demo 신호는 `"NEU"` 문자열, DB
   `wrapped_signal.polarity`는 null 허용. DB 조달 훅에서 null↔""↔"NEU"가
   demo와 동일하게 접히도록 정규화하지 않으면 소스 간 노드 분열. P8-2 훅 구현
   시 단위테스트로 고정할 것.
3. **[P8-2 체크] 라벨 인덱스** — G1은 suffix fallback만. 활성화 훅에서 DB concept
   label / demo label sidecar 조달(계획 §활성화 훅 그대로).
4. **[인지] gate 조립질 = category_group(6군)** — 핸드크림(bodycare)의 top-1이
   바디로션(bodycare) 가능. 사용자 의도("메이크업↔헤어 방지")·계획과 부합, 세부
   카테고리는 IDF 점수 노드(2.08)로 이미 우대. G3 UX에서 필요 시 세분화 여지만 기록.
5. **[데이터 관찰]** `keyword::bee_attr_loyalty:GelLike:NEU` 등 BEE 스팬 노이즈
   흔적 — 방침(모델 신뢰, 상류 데이터 이슈)대로 시스템은 그대로 처리. 기록만.

**현 데이터 한계(설계 아님)**: keyword polarity 전부 NEU(710/710 — 극성 변별은
POS/NEG 데이터 유입 시 자동 활성, 합성 테스트로 고정) · keyword 축 보유 상품
wide 63.8%(데이터 성숙으로 회복 예정, 전체 커버리지는 타 축이 견인).
