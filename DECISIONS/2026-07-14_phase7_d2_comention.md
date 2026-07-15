# P7-4 D2 — co-mention 상품-상품 유사도

작성: 2026-07-14 · 상위 계획: `fable_doc/plans/2026-07-13_phase7_graph_intelligence.md` §D2 ·
진단 근거: `fable_doc/06_graph_ontology_assessment.md` §2 (U2 co-mention = review_id self-join으로 계산 가능)

## 배경 / 목적

co-use edge(실 SKU 0)의 리뷰-데이터-네이티브 대체재. 같은 리뷰에 함께 언급된
상품쌍은 연관/대체 관계로 볼 수 있고, 유저가 소유한 상품과 co-mention된 상품을
부스트하는 신호가 된다. canonical_fact가 review_id를 보존하므로 self-join으로
계산 가능. 프로토타입의 목적은 **배선 검증** — 실데이터 유입 시 즉시 발화하도록.

## co-mention 밀도 실측 (2026-07-14, in-process 프로브, kg_mode=on)

측정 방법: `run_full_load` → `batch_result["all_bundles"]`의 canonical_fact를
review_id로 그룹핑. 상품을 **REAL(카탈로그 연결, 추천 가능)** vs **GHOST
(`concept:Product:*` 미해소 표면형)**로 분류.

| 실측 | dense_golden (32상품) | wide (517상품) |
|---|---|---|
| 리뷰당 ≥1 REAL 상품 | 906/906 | 906/906 |
| **리뷰당 ≥2 REAL 상품 (co-mention 성립)** | **0** | **0** |
| **REAL-REAL distinct 쌍** | **0** | **0** |
| fact-level ≥2 상품 리뷰(ghost 포함) | 58 | 58 |
| fact-level distinct 쌍(ghost 포함) | 66 | 73 |
| 신호-level Product-dst 쌍(전부 ghost, support=1) | 9 | 9 |
| Product-dst 신호 극성 | POS 12 (NEG 0) | POS 12 (NEG 0) |
| product-object fact 예: uses 653 / purchases 243 / ingredient_of 38 / comparison_with 8 | — | — |

**핵심 발견**: 리뷰-only 데이터에서 **한 리뷰가 서로 다른 두 REAL 상품을 동시
언급하는 경우는 0건**이다. 모든 리뷰는 정확히 1개 REAL 상품(리뷰 대상)에 대한
것이고, 두 번째 상품 언급은 전부 미해소 ghost(`다른라인`/`미니어처`/`에센스`/
`다른거`…)다. product-object fact가 1067건이나 되지만, 그 object는 (a) 리뷰
대상 상품 자기 자신(uses/purchases가 리뷰 대상을 가리킴)이거나 (b) ghost다.
comparison_with 8건도 전부 ghost(진단의 "comparison 실SKU 0, ghost 8" 재확인).

→ co-mention은 co-use(실SKU 0)와 **동일한 데이터 부재 상태**를 다른 각도에서
재확인. D1(user→product edge 1유저)과 같은 "배선 완성 + 실데이터 대기".

## 결정 요약

1. **필드 = 신설(재사용 아님)**. 기존 `top_coused_product_ids`(영속 서빙 컬럼,
   `USED_WITH_PRODUCT_SIGNAL` 집계로 채워짐)를 co-mention으로 재사용하지 않음:
   (a) co-use ≠ co-mention — 재사용은 provenance 거짓(co-USE 근거를 주장) (b)
   영속 컬럼 신설은 sql/DDL·`src/db/repos`·serving 스키마 정합 테스트를 건드림
   (금지 파일). **D1 패턴 답습** → 상품 프로필에 **ephemeral in-process 필드
   `comention_product_ids`** 부착(`src/mart/product_comention.attach_comention_signals`).
   영속 안 됨·서빙 컬럼 아님·스키마 표면 0. attach 호출 전엔 필드 부재 →
   overlap 미생성 → 기본 경로 byte-identical.
2. **evidence family**: boost-only 버킷 재사용 — `BOOST_ONLY_TYPES`에 `comention`
   추가. collab과 동일하게 **어느 모드에서도 단독 자격 불가**(`BOOST_ONLY_ADMISSIBLE_TYPES`
   미포함). "소유 상품과 함께 언급됨"은 연관성일 뿐 단독 추천 사유 아님.
3. **polarity 필터**: NEG 극성의 Product-dst edge(비하성 comparison — "A가 B보다
   낫다")로 유입된 상품은 co-mention 집합에서 제외(`exclude_negative=True`).
   비하 비교가 "유사/연관"으로 오염되지 않게. (실측상 현 fixture Product-dst는
   전부 POS라 현재 영향 0이나, 계약으로 고정.)
4. **최소 지지도 게이트**: 한 쌍이 `min_support=2` 개의 *서로 다른* 리뷰에서
   동시 언급돼야 인정(1회는 노이즈). C2 승격 게이트의 교차검증 철학과 동일.
   실데이터 볼륨 증가 시 상향 가능.
5. **scorer 가중 위치**: `comention_product_bonus`를 `features:` 맵에 넣지 않음
   (프론트 슬라이더 계약 테스트가 src/static 수정 강요 → 금지). collaborative_affinity
   선례와 동일하게 top-level 키 `comention_product_weight: 0.02`(coused_product_bonus
   앵커, 보수적)로 두고 scorer가 직접 읽어 **전 모드 적용**. contribution 키는
   SCORING_FEATURE_KEYS 밖.
6. **score layer**: 신규 layer 키 금지 → 기존 `review_graph_score` 그룹에 편입
   (comparison/collab 이웃).
7. **유저 앵커 = owned_product_ids** (coused/comparison과 동일). 후보 상품이
   유저 소유 상품과 co-mention돼 있으면 `comention:<owned>|strength=` 부스트.

## 판정 — **배선 완성 + 실데이터 대기** (D-트랙 위임 규칙대로 자동 전환)

co-mention 밀도 실측이 REAL-REAL 0을 확정 → 골든 프로필에서 발화 0(예상된 결과).
Phase 7 "연결성 신호 첫 유의미 발화(0건 탈출)"는 D2에서도 **미발화**. co-use·
comparison(데이터 0)·concern(support-1 희소)에 이어 co-mention도 데이터 부재.

발화 조건: 한 리뷰가 2+ REAL 상품을 언급하는 실데이터. 경로 두 가지 —
(a) relation 모델 개선으로 comparison/uses 대상 상품이 실 SKU로 해소(ghost→real)
(b) 멀티상품 리뷰(세트/비교 리뷰) 유입. 둘 다 relation 파이프라인·링킹 개선
(B트랙) 또는 액션/인텐트 데이터(E트랙)에 의존.

회귀 보호: `tests/test_comention.py::test_real_fixture_has_no_real_real_comention`가
현 "대기" 상태를 락. 이 테스트가 깨지면 = 실 co-mention 데이터 출현 →
D2를 "대기"에서 "활성"으로 승격하고 랭킹 스냅샷 의도 재승인.

## 기본 경로 불변 증거

- 3중: (1) 데이터 프로브 — REAL-REAL 0, attach 시 전 상품 `comention_product_ids=[]`
  (2) 구조 — comention overlap은 attach 호출 시에만 생성, 기본 파이프라인/서버/
  audit은 attach 미호출(D1 collab과 동일 dormancy) (3) 스냅샷 — dense/wide 랭킹
  스냅샷 회귀 green(D2 델타 0), 기대셋 green
- scoring_weights의 `comention_product_weight: 0.02`는 non-zero지만 comention
  overlap이 없으면 value=0 → contribution 0 → raw_score 불변(collab과 동일 논리)

## 변경 파일

- 신규 `src/mart/product_comention.py` — 계산 모듈(membership 추출·페어링·attach)
- 신규 `tests/test_comention.py` — 27 테스트(밀도 가드 포함)
- `src/rec/recommendation_evidence_index.py` — `comention` boost-only(비-admissible)
- `src/rec/candidate_generator.py` — comention overlap 생성(owned 앵커, dormant)
- `src/rec/scorer.py` — `comention_product_weight` + comention 기여 + score layer
- `src/rec/explainer.py` — comention 경로·설명 문구("리뷰에서 함께 언급되는 상품")
- `configs/scoring_weights.yaml` — `comention_product_weight: 0.02`

## 게이트

ruff/mypy(116) ✅, pytest **1201 passed, 50 skipped, 0 failed** (+27, 전부 신규
test_comention.py). 골든 스냅샷·기대셋 무변경.
