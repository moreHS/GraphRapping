# 07. 공유 노드 멀티홉 연결 — 심층 분석

작성: 2026-07-15 · 작성 주체: 메인(Opus) 종합 + 리서치 3트랙 실측
(R-A: Relation 프로젝트 패턴 / R-B: GraphRapping 기질 정밀측정 / R-C: 설계공간·활용) ·
후속 계획: `plans/2026-07-15_phase8_shared_node_projection.md`

**질문(사용자, 2026-07-14)**: 리뷰에서 타제품을 정확히 찾는 상품↔상품 직접 엣지는
어차피 어렵다 치더라도, **상품에 붙은 속성 노드(보습·촉촉·성분·브랜드…)를 공유
노드로 삼으면 그걸 통해 상품이 2홉·3홉으로 연결**되지 않나? A제품의 '촉촉'과
B제품의 '촉촉'은 사실 같은 노드니까. Relation 프로젝트 new_graph_system/project_3에서
그렇게 멀티홉을 만들었는데 — 지금 GraphRapping은 그게 안 되는 상태인가?

## 0. 한 줄 답

> **직관은 정확합니다. 공유 노드 2홉 연결은 데이터에 이미 실재하고 조밀합니다
> (wide 카탈로그 기준 ingredient 12,335 상품쌍, category 2,770쌍). 추천에는 이미
> 암묵적으로 쓰이고 있고, 명시적 "유사 상품/탐색" 기능만 아직 안 만들었을 뿐입니다.**
> 단, 실측이 밝힌 비직관적 반전 하나 — **어느 축을 공유 노드로 쓰느냐가 승패를
> 가릅니다.** 가장 조밀한 bee_attr 축은 함정이고(노드가 값이 아니라 차원), 진짜
> 주력은 rarity 가중을 건 ingredient 축입니다. Relation도 이걸 했지만 IDF가 없어
> 허브 노드 과다연결을 못 풀었고 — 그게 GraphRapping이 개선할 지점입니다.

## 설계 확정 (2026-07-15 사용자 논의 반영 — 아래 측정 해석을 갱신)

이 문서의 **측정 수치(노이즈율·쌍수 등)는 유효**하나, 그 **해석·처방 일부는 논의로
정정**됐다. 최종 설계는 `DECISIONS/2026-07-15_phase8_shared_node_design_dialogue.md`
와 계획서가 정본이며, 요지는:
- **메커니즘 = IDF 가중 공유노드 합 + 랭킹 + top-N** (하드-AND·노드 병합·전역 하드
  게이트 **아님**). 아래 "ingredient∩main_benefit 결합"은 하드-AND 지시가 아니라
  "다축 공유 쌍일수록 깨끗하다"는 진단(가중 합 방식의 근거)
- **카테고리 게이팅 = 소비 맥락 파라미터**: 유사상품 추천=ON, 일반 추천=OFF(다양성),
  쿼리 기반=상류 게이팅. 아래 "카테고리 게이팅 필수"는 유사상품 맥락 한정
- **브랜드 = 노이즈 아님**(데이터 쏠림 아티팩트). 하드 제외 안 하고 **IDF가 자동
  보정**. 아래 "메가브랜드 제외"는 IDF가 대신함
- **keyword = 주력 축**(관찰용 아님), **(bee_attr_id, keyword_id) 복합키로 스코핑**.
  bee_attr(클래스명)은 점수 제외·스코핑 전용. 낮은 커버리지는 데이터 성숙으로 회복
- 노이즈 판정 지표: 전역 쌍 비율 → **기준 상품별 top-N 품질 + 커버리지**로 변경

## 1. 메커니즘 — "공유 노드 2홉"이란 정확히 무엇인가

`canonical_fact`는 **상품 → 속성** 이분(bipartite) star 구조입니다. 상품 A와 B가
같은 속성 노드(예: `kw_moisturizing`)에 각각 엣지를 가지면, `A → 노드 ← B`가
2홉 경로입니다. 이 2홉을 "A와 B는 유사"라는 **상품-상품 엣지로 투영(projection)**
하는 것이 사용자가 말한 그래프 활용입니다.

- **Relation 프로젝트가 정확히 이 방식**(R-A 실측): `entity_id =
  sha256(type::normalized_value)`로 "보습력_POS"가 전역 단일 노드가 되고,
  `SimilarProductRecommender`가 **공유 속성 개수 = 유사도 점수**로 계산합니다
  (인메모리 이분 역인덱스, `graph_system/src/query/recommender.py`).
- **중요**: Relation의 실제 서빙은 **Neo4j를 쓰지 않습니다** — project_3의 Neo4j는
  export 타깃일 뿐, 상품 유사도는 인메모리 Python 역인덱스입니다. 즉 **이건
  그래프 DB 기능이 아니라 트리플 테이블 self-join**입니다.
- **Relation의 미해결 약점**: TF-IDF/희귀도 가중이 **전혀 없어**(코드 grep 0건),
  허브 속성(`충성도_POS` weight 36,258)이 거의 모든 상품을 이어버립니다. 이게
  GraphRapping이 넘어설 지점.

→ GraphRapping 이식은 `canonical_fact`(이미 있음)의 `GROUP BY 공유노드` self-join
한 방. 4.0 audit의 "그래프 DB/recursive CTE 수요 없음" 판정과 정합합니다.

## 2. "지금 되는가" — 층위별 정밀 답

| 층위 | 상태 |
|---|---|
| 공유 노드 연결 **데이터** | ✅ 실재·조밀 (§3 표) — B2(개념 접힘)가 촉촉/촉촉한/보습을 단일 `kw_moisturizing`로 통합해 정합성까지 개선 |
| 추천에서 **암묵적 활용** | ✅ 되고 있음 — `keyword_match`/`ingredient_match`가 상품별 feature라, 유저가 원하는 속성을 공유한 A·B가 함께 노출 (공유노드 연결과 동일 효과) |
| 명시적 **상품↔상품 유사도 / 탐색 기능** | ✗ 미구현 (grep 0건) — 데이터 부재가 아니라 미구현 |

### 비직관적 반전 — 축이 승패를 가른다

사용자의 "촉촉" 예시는 **keyword 축**에 해당합니다(값을 운반: `kw_moisturizing`).
그런데 실측(R-B)이 밝힌 함정: **가장 조밀한 bee_attr 축은 유사도 축으로 최악**입니다.

- bee_attr 노드는 **"값"이 아니라 "차원"**입니다. top 노드가 `bee_attr_texture_feel`,
  `bee_attr_effect`, `bee_attr_moisturizing_power` — "두 상품이 texture_feel 속성을
  가짐" = "둘 다 리뷰에서 질감이 언급됨"이라 **거의 모든 상품이 공유** → promiscuity
  최대(wide 최대 노드 64상품), 노이즈 최고(0.66~0.80). rarity 가중으로도 안 낫습니다
  (df≤5에서도 0.71).
- 실제 "값"(촉촉/순함/진정)은 **keyword 축**이 운반하고, 이건 B2가 정돈했습니다.
- 진짜 주력은 **ingredient 축**: rarity(IDF) 가중을 걸면 노이즈가 0.566 → **0.331**
  (df≤5) → 0.233(df≤2)로 극적으로 정화되고, IDF-가중 상위 쌍은 동일 카테고리 100%.

## 3. 축별 투영 가치 실측 판정 (R-B)

wide(517상품) 기준. 노이즈율 = 교차 카테고리군 쌍 비율(낮을수록 좋음).

| 축 | 투영 쌍 | 노이즈율(원) | 게이팅/가중 후 | 판정 |
|---|--:|--:|--:|---|
| **ingredient** | 12,335 | 0.566 | **0.331**(df≤5) / 게이팅 시 동일군 5,349쌍 | ★ **주력** — 게이팅+rarity 필수, "비슷한 상품"의 핵심 |
| **category_concept** | 2,770 | **0.048** | 이미 최저, 커버 456/509 | 게이팅 **기반축**(거의 동어반복이나 gate의 토대) |
| **main_benefit(Goal)** | 2,444 | **0.058** | 소어휘 6노드 | **결합용** — 단독은 거칠지만 ingredient와 AND 시 최강 |
| **brand** | 19,765 | 0.503 | **0.058**(메가브랜드 df≤10 제외) | 게이팅 필요 — 브랜드 라인 탐색용 |
| **keyword** | 167 | 0.569 | 게이팅 필요 | 값 운반(B2 정돈)이나 **wide 커버리지 27** — C2 서빙도달 회복 의존 |
| **bee_attr** | 2,413 | 0.663 | rarity로도 0.71 | ✗ **배제** — 노드가 차원(값 아님) |
| concern_pos / context | 0 | — | — | ✗ 데이터 부재(상품측 0) |

**최강 결합: ingredient ∩ main_benefit = 2,111쌍 / 노이즈 6.1%** (단일 ingredient
56.6% 대비 9배 개선). "같은 성분 + 같은 효능 목표"가 저노이즈 유사의 핵심 조합.

**3홉은 폭발·무이득**: ingredient 3홉은 신규 쌍의 노이즈가 오히려 **상승**
(0.566→0.759), dense bee_attr은 2홉에서 이미 포화. **2홉만 의미 있음** — 이는
"멀티홉을 몇 홉까지?"에 대한 실측 답입니다(3홉 이상은 전이적 폐포만 만듦).

## 4. 4.0 audit 재판정

4.0 audit(2026-07-08)은 공유-속성 2홉을 **MARGINAL**로 판정했는데, 그건 **추천
후보 생성 렌즈**에서 타당했습니다(성분은 1홉 개인화로 이미 매칭 / 교차군 노이즈 /
CTE 불필요). 이번 실측은 노이즈 수치를 재확인(dense ingredient 0.812/wide 0.566 —
4.0의 0.841/0.553과 일치)하되, **"유사 상품/탐색/그래프 뷰"라는 다른 기능 렌즈**에서
결론을 상향합니다:

| | 후보생성 렌즈 (4.0) | 탐색/유사상품 렌즈 (이번) |
|---|---|---|
| 판정 | MARGINAL (recursive CTE 보류) | **VIABLE-WITH-GATING** |
| "1홉 중복" 논거 | 성립 (개인화 중복) | **무효** — item-to-item은 애초에 개인화가 아님(4.0도 인정) |
| 노이즈 | "게이팅 없이 품질 위험" | 게이팅+rarity로 **통제 가능함이 실측됨**(레버 확인) |
| 구현 형태 | self-join, CTE/그래프DB 불요 | **동일 유지** |
| materialize 가치 | 후보엔 불필요 | ingredient(rarity+게이팅)+category/main_benefit **결합축은 가치 있음** |

즉 **recursive CTE/그래프DB 보류는 유지**하되, **공유속성 2홉은 "부적합"이 아니라
"게이팅·가중 전제 하 실현 가능"이며, 축은 ingredient·category·main_benefit(결합)에
한정하고 bee_attr는 배제**가 정밀 판정입니다.

## 5. 활용 방향 (R-C, evidence-first 정합)

1. **유사 상품 위젯** (`GET /api/products/{id}/similar`) — item-to-item이라
   개인화 eligibility 미경유(search.py 익명 설계 논리). 응답에 `shared_axes`
   ("보습·저자극 공유") 항상 동반 = "왜 유사한가" 구조적 강제
2. **탐색/그래프 뷰** — `graph_view.js`가 완전 data-driven이라 서버측 노드/엣지
   추가만으로 렌더(JS 무수정). 상품-상품 `SHARES_ATTRIBUTE` 엣지 + `shared_axes`
   tooltip으로 "이 상품이 왜 저 상품과 연결되나"를 그래프가 직접 답함
3. **추천 re-rank** — boost(유저 소유상품과 유사한 후보 부스트, boost-only 계약)
   vs diversity 페널티(너무 유사한 중복 상품 억제) — 상충하므로 하나만 먼저
4. **검색 확장** — /api/search·/api/ask의 concept 매칭 결과 뒤 "관련 상품 더보기"
   2차 섹션(1차 개념일치와 근거 성격 구분)
5. **인사이트** (상품 클러스터) — Track F, 수요 확인 시

## 6. 결론 및 발전 방향

- **사용자 직관 맞음 + 데이터 뒷받침 + Relation보다 개선 가능**(IDF 도입). 그래프
  DB 불요 — self-join으로 충분.
- **승패는 축 선택**: ingredient(rarity+category 게이팅) 주력, ingredient∩main_benefit
  결합이 최정밀, bee_attr 배제, keyword는 C2 서빙도달 회복 의존.
- **Track D 계열의 확장(D4)**: D1(user-user)·D2(co-mention)와 동일한 Jaccard/
  ephemeral/boost-only 패턴을 상품-속성 축에 적용. **D1/D2와 달리 데이터가 준비돼
  있어(bee_attr 아닌 ingredient/category) 실제로 발화 가능한 첫 연결 신호**.
- **유저/액션 레이어와 수렴**: Relation의 유저 통합도 같은 공유-속성 노드 위에
  올렸음(감성 일치 5/3/1). Track E(액션) 도래 시 앵커가 owned→viewed/carted로
  넓어지며 user→product→유사product 3홉이 재설계 없이 열림.

→ 상세 실행 계획: **`plans/2026-07-15_phase8_shared_node_projection.md`** (Phase 8)
