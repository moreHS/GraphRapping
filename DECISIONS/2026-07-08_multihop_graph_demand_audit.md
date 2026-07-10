# Phase 4.0 — multi-hop 그래프 순회 수요 audit (판정: 4.1 착수 안 함)

작성일: 2026-07-08 · 상태: **판정 완료** · 결론: **Phase 4.1(recursive CTE/그래프 순회 모듈) 착수 보류** ·
근거: 분석 스크립트 [scripts/audit_multihop_demand.py](../scripts/audit_multihop_demand.py) 실측

## 배경

`fable_doc/02_issues_assessment.md` §A1과 `03_improvement_plan.md` §4.0의 지적:
`canonical_fact`는 (subject_iri, predicate, object_iri) RDF 트리플이고 순회용 인덱스
(`idx_cf_pred_subj/obj`)까지 있으나, 이를 그래프로 질의하는 코드가 0건이다. 유일한 간접
추론(concern_bridge: BEE→concern)은 YAML 하드코딩 1-hop이다.

§4.0의 관문 조건: **"multi-hop이 실제로 추가 후보/근거를 만드는지 수치로 판정. 수요
없으면 4.1 착수 안 함."** 수요 증명 없이 CTE 모듈부터 만드는 것은 과잉 투자(크로스 리뷰
지적 #10, `04_cross_review_log.md`).

이 문서는 그 판정이다. 재현 가능한 읽기 전용 스크립트로 dense golden(32상품/906리뷰)과
wide(517상품/906리뷰) fixture의 canonical_fact/serving 데이터를 실측했다.

## 측정 방법

`scripts/audit_multihop_demand.py`는 데모 서버와 동일한 in-memory 파이프라인
(`run_full_load`, kg_mode=on)을 돌려 canonical_fact 트리플을 DB 적재와 동일하게
materialize한 뒤, 후보 사용 사례별로 **"2-hop이 1-hop 대비 추가하는 순수 신규
추천가능 SKU 후보/근거"**와 **노이즈 비율**을 센다. DB/네트워크 접근 없음.

핵심 사전 관측 — dense fixture의 canonical_fact 3,876건은 **약 97%가 리뷰 대상 Product를
중심으로 한 star 구조**다:

| predicate | 건수 | 구조 |
|---|---:|---|
| `has_attribute` | 2,450 | Product → BEEAttr (1-hop, 이미 top_bee_attr_ids로 서빙) |
| `uses` | 653 | ReviewerProxy → Product |
| `purchases` | 243 | ReviewerProxy → Product |
| `HAS_KEYWORD` | 238 | BEEAttr → Keyword |
| `used_on`/`time_of_use` | 65 | Product → TemporalContext |
| `has_ingredient`/`ingredient_of` | 77 | Product ↔ Ingredient |
| `has_part`/`part_of`/`available_in` | 86 | Product ↔ Product (구성/규격) |
| `comparison_with` | 8 | Product ↔ Product (비교) |
| **`used_with`** | **2** | Product ↔ Product (함께 사용) |

**Concern / Goal은 canonical_entity에 노드로 존재하지 않는다(0건).** 이들은 projection/
serving 계층의 파생물이다. 이 한 가지 사실이 후보 사례 대부분의 운명을 결정한다.

## 후보 사용 사례와 사례별 판정

### UC1/UC3 — concern→ingredient→product, goal→…→bee_attr→product  ⟶ **수요 없음(NONE)**

- 브리프가 지목한 핵심 후보(concern_bridge의 일반화). 그러나 그래프에 **Concern 노드 0,
  Goal 노드 0, concern-side edge(`treats`/`addresses`/`addressed_by_treatment`) 0건.**
- 현재 concern 매칭의 1-hop 실체: direct concern signal은 서빙 32상품 중 **0**,
  concern_bridge(BEE→concern)도 **0**. 즉 이 fixture엔 개선 대상 concern 1-hop 자체가 없다.
- 판정 근거: concern→ingredient 같은 체인은 **기존 트리플 순회로 불가능**하다. 하려면
  concern→ingredient 큐레이션 맵(현 `concern_bee_attr_map.yaml`의 확장)을 **새로 저작**하고
  1-hop lookup을 붙여야 한다 — 그래프 순회(4.1)가 아니라 데이터 모델링/사전 저작 작업이다.

### UC1' — product→ingredient→product(공유 성분 확장)  ⟶ **미미(MARGINAL)**

- 실측(dense): master HAS_INGREDIENT 링크 보유 15상품, ≥2상품 공유 성분 59종, 유도 상품쌍
  69개 중 **동일 카테고리군 11 / 교차군 노이즈 58 (노이즈율 0.841)**.
- wide(517상품): 상품쌍 12,335개, 노이즈율 0.553 — 규모가 커져도 과반이 교차군 노이즈.
- 판정 근거: (a) 성분은 **product master 진실이라 이미 1-hop `ingredient` overlap으로 매칭**
  된다(사용자가 성분 X 선호 시 두 상품 모두 독립적으로 적격). 2-hop은 개인화가 아니라
  item-to-item 유사도라는 **다른 기능**이고, 그건 단순 `GROUP BY ingredient`로 충분해
  **recursive CTE가 불필요**하다. (b) 과반이 교차 카테고리 노이즈라 카테고리 게이팅 없이는
  품질 위험.

### UC2 — product→co-used→product→co-used→product(2-hop 함께쓰기)  ⟶ **수요 없음(NONE)**

- 실측: co-use edge(`used_with`+`comparison_with`) 총 10개인데 **실 카탈로그 SKU 간 edge는
  0개**, 전부 미해소 concept mention(`concept:Product:다른 퍼프`, `컬러립밤`, `코드` 등)이다.
- 2-hop이 만드는 신규 후보: **실 SKU 0개**(concept ghost 8개뿐). 서빙 `top_coused_product_ids`
  는 **32상품 전부 비어있음(0/32)** — 집계 계층이 co-use 신호를 아예 만들지 않는다.
- wide(517상품)에서도 실 SKU co-use edge 0 — 카탈로그 규모와 무관하게 구조적으로 부재.

### UC4 — product→bee_attr→keyword(속성 세분화)  ⟶ **미미(MARGINAL), 신규 후보 0**

- HAS_KEYWORD 238건으로 유일하게 조밀한 2-hop. dense 36상품 / wide 490상품이 도달.
- 그러나 이는 **같은 상품 내부**의 속성→하위키워드 확장이다. **신규 후보 상품 0개.** 해당
  상품은 이미 bee_attr로 1-hop 적격이라, 얻는 것은 설명 풍부화뿐(추천 후보/적격성 변화 없음).

### UC5 — product←uses←reviewer→uses→product(리뷰어 매개 co-use, 협업필터)  ⟶ **차단(BLOCKED)**

- 구조적으로 **가장 조밀한 2-hop**: 실 SKU ≥2개를 잇는 리뷰어 235~236명, degree 2~10,
  유도 상품쌍 363(dense)~1,214(wide).
- 그러나 두 가지 이유로 사용 불가:
  1. **invariant G4**(`ARCHITECTURE.md`): reviewer proxy ↔ 실유저 병합 금지로 협업필터류
     신호가 **의도적으로 원천 차단**됨(프라이버시). 되살리려면 별도 정책 결정이 필요.
  2. dense fixture의 리뷰어-상품 그래프는 `dense_round_robin` 리맵 산물
     ([build_dense_golden_fixture.py](../scripts/build_dense_golden_fixture.py)의 `remap_reviews`)이라
     **진짜 co-use가 아닌 합성 아티팩트**다. wide fixture의 리뷰어 co-occurrence도
     원 리뷰의 author_key 기준이라 별개 검증 필요.
- 즉 이건 CTE 모듈(4.1) 문제가 아니라 **정책+데이터 문제**다.

## 종합 판정 — Phase 4.1 착수 **보류(No)**

**기존 canonical_fact 트리플의 진짜 순회로 순수 신규 추천가능 SKU 후보를 만드는 사례가
없다.** 요약:

| 사례 | 신규 실-SKU 후보 | 저해 요인 | 판정 |
|---|---:|---|---|
| UC1/UC3 concern·goal 체인 | 0 | Concern/Goal 노드 부재 → 순회 불가 | 없음 |
| UC1' 공유 성분 | item-sim만 | 1-hop 매칭 이미 가능 + 과반 교차군 노이즈 + CTE 불필요 | 미미 |
| UC2 co-use 2-hop | 0 | 실 SKU co-use edge ≈ 0 | 없음 |
| UC4 bee→keyword | 0 | 같은 상품 내부 확장(신규 후보 0) | 미미 |
| UC5 리뷰어 co-use(CF) | (조밀) | invariant G4 차단 + fixture 아티팩트 | 차단 |

현 워크로드(집계 후 서빙)는 RDB로 충분하며, 수요 실증 전 recursive-CTE/그래프 순회
모듈 도입은 과잉 투자다. `03_improvement_plan.md` 리스크 절의 "그래프 DB 지연 결정"
방침과 일치한다.

## 만약 나중에 착수한다면 (권고 순서·형태)

수요가 생겼을 때의 우선순위와 구현 형태(비용 오름차순):

1. **공유 성분 item-similarity (UC1')** — 가장 저렴. **recursive CTE 아님**, 단순
   애플리케이션 조인/`GROUP BY canonical_fact WHERE predicate='has_ingredient'`.
   착수 조건: (a) "비슷한 상품" item-to-item 기능이 목적 3축에 실제 추가될 때, (b) **카테고리군
   게이팅 필수**(교차군 노이즈 55~84% 차단), (c) 일반 성분 토큰(`성분` 등) 제외.
2. **concern→ingredient 브리지 (UC1의 정공법)** — 중간 비용. 그래프 순회가 아니라
   **concern→ingredient 큐레이션 맵 저작** + 기존 concern_bridge 패턴 재사용(1-hop). 착수
   조건: 서빙에 direct concern signal이 유의미하게 존재하게 된 뒤(현재 0/32라 선행 불가).
3. **리뷰어 매개 co-use / CF (UC5)** — 가장 비쌈. **먼저 invariant G4 완화 여부를 별도
   DECISIONS로 결정**하고, round-robin 아닌 진짜 리뷰어-상품 데이터를 확보한 뒤에만.
   이때 비로소 canonical_fact 위 2-hop 질의(애플리케이션 조인으로 충분, degree≤10이라
   recursive CTE 불요)가 정당화된다.

**recursive CTE / Apache AGE·Neo4j**는 위 어느 것도 요구하지 않는다. degree가 낮고
(리뷰어 최대 10, co-use ≈0) 대부분 1~2 조인으로 끝나므로, 도입은 `03_improvement_plan.md`
Phase 5 백로그의 "그래프 DB 재평가(4.1 성능 데이터 확보 후)" 조건을 그대로 유지한다 —
즉 **지금 근거로는 재평가 트리거 자체가 발동하지 않는다.**

## 재현

```
python scripts/audit_multihop_demand.py --fixture dense_golden        # 사람용 요약
python scripts/audit_multihop_demand.py --fixture wide --json          # JSON 리포트
```

## 트레이드오프 / 한계

- **fixture 한정**: 판정은 dense/wide fixture 실측이다. 실데이터에서 리뷰가 명시적
  `used_with`/비교/성분을 더 많이 담으면 UC1'/UC2의 밀도가 오를 수 있다. 단 UC1/UC3/UC5의
  결론(노드 부재·invariant 차단)은 스키마/정책 차원이라 데이터가 늘어도 불변이다.
- **재평가 트리거**: 실데이터 적재 후 `audit_multihop_demand.py`를 재실행해 (a) 실 SKU
  co-use edge, (b) direct concern signal 보유 상품 수가 유의미해지면 이 판정을 갱신한다.
  게이트(`recommend_start_phase_4_1`)는 **5개 use case 중 하나라도 자체 verdict가
  DEMAND**이면 참이 되도록 계산한다 — use case별 독립 판정이며, UC1/UC3의 concern/goal
  노드 유무는 UC1/UC3 자신의 verdict에만 반영될 뿐 다른 use case의 verdict를 가리는
  별도 feasibility AND 조건으로 쓰이지 않는다. 즉 (a)만 유의미해져도(concern/goal
  노드는 여전히 0인 채로) 게이트가 정확히 반응한다.
  (정정: 2026-07-08 라운드 B — 종전 구현은 UC1/UC3를 이름으로 actionable demand에서
  배제하면서 그 판정을 `graph_traversal_feasible`라는 전역 플래그로 뽑아내 나머지 모든
  use case의 게이트에 AND로 걸었다. 그 결과 실데이터에서 UC2/UC1'에 실제 수요가
  생겨도 concern/goal 노드 부재만으로 게이트가 켜지지 않는 잠복 버그가 있었다. 이
  섹션의 결론 자체는 불변 — dense/wide 두 fixture 모두 재실행 결과
  `recommend_start_phase_4_1: False`, verdict_summary도 위 표와 동일.)
- 이번 스코프는 분석·판정에 한정했다. src/ 코드는 수정하지 않았고 신규 산출물은 위 스크립트와
  이 문서뿐이다.
