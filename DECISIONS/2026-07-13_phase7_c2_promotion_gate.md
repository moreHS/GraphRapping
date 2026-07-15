# Phase 7 C2 — 승격 게이트 카탈로그-인지 보완

날짜: 2026-07-13 · 상태: **✅ 확정 (사용자 승인 — (a) ALL/90d 3→2 채택)** · 근거:
fable_doc/06_graph_ontology_assessment.md §5 + A4 추기(실측)

## 배경 (전부 실측)

- 동일 906리뷰가 dense(32상품)에선 서빙 도달 91%, **wide(517상품)에선 5%**
  — 실데이터형 분산에서 리뷰 그래프가 서빙에서 사실상 소멸 (top-10 그래프
  등장률 2.3%, score 기여 0.9%)
- **A4 핵심 발견**: wide 517상품 전부 리뷰≥1 (이론 천장 100%, projectable
  신호 보유 99.6%) — **리뷰 없는 상품은 병목이 아니며 5% 붕괴는 전적으로
  승격 게이트(`distinct_review≥3`)가 만든다**
- 완화 곡선 실측: ≥3(현행) = 26상품(5.0%) / **≥2 = 90상품(17.4%)** /
  ≥1 = 515상품(99.6%). 품질 게이트(avg_conf≥0.6, synthetic_ratio≤0.5)는
  비구속(제거해도 동일 — 걸러내는 것은 오직 지지도)
- P7-2 C1이 소생시킨 CONCERN 신호(9건)도 같은 게이트에 걸려 서빙 미도달
  (상품당 평균 distinct_review 1)
- 상품별 최대 distinct_review: 1리뷰 상품이 82%(425/517)

## 선택지

| 안 | 내용 | wide 도달률 | 리스크 |
|---|---|---|---|
| (a) **ALL/90d 임계 3→2** (D30은 이미 2) | 독립 리뷰 2건 교차 검증 유지 | **17.4% (3.5배)** | 낮음 — 최소한의 교차 검증 보존 |
| (b) 상대 임계 (상품 리뷰 수 대비, 2/2 인정) | 리뷰 2개뿐인 상품은 2/2도 유의 | (a)에 포함 — 증분 없음 (실측) | — |
| (c) ≥1 인정 (단독 리뷰 자격) | 99.6% | **높음** — 교차 검증 부재, 실데이터 리뷰 스팸/단발 오정보가 그대로 서빙. 채택하려면 boost-only 저신뢰 티어 한정(E0 §13 정합) 필요 |
| (d) 현상 유지 | 5% | 리뷰 그래프의 서빙 소멸 지속 — Phase 7 목적 무산 |

## 권고: (a) — agg ALL/90d의 distinct_review 임계 3→2

근거:
1. 3.5배 개선이면서 "서로 다른 리뷰 2건"이라는 **최소 교차 검증 의미 보존**
2. 품질 게이트(confidence/synthetic)는 그대로 — 완화되는 것은 지지도 축뿐
3. 지지도는 서빙 노출 이후에도 **shrinkage가 점수에서 계속 반영**(support
   낮으면 점수 자동 감쇠) — 서빙 도달 ≠ 상위 랭킹 보장, evidence-first 유지
4. (c)는 비채택 — 단독 리뷰 자격은 실데이터에서 위험. 필요해지면 boost-only
   티어로 별도 결정
5. 실데이터에서 리뷰 볼륨이 늘면 임계 재상향 여지(파라미터라 코드 변경 없음)

## 검증 계획 (구현 시 완료 기준)

- dense/wide 랭킹 스냅샷 diff **재승인 워크플로우**(의도 변경 — 단순 green
  아님) + 기대셋(golden_expected_evidence) 영향 검토
- wide 서빙 도달률 실측 17.4% 달성 확인 + C1 CONCERN 신호 서빙 도달 확인
- corpus 승격 베이스라인(test_corpus_promotion_baseline) 의도 갱신

## 트레이드오프

- 지지도 2건 신호의 서빙 노출 증가 — 노이즈가 아니라 "표본 적은 진짜 신호"
  라는 것이 evidence-first 전제이며, 점수 shrinkage가 완충. 실데이터 첫
  구간에서 모니터링(retention_monitor 지표와 병행) 권장

## 구현 결과 (2026-07-14, 실측)

임계 변경: `src/mart/aggregate_product_signals._PROMOTION_MIN_REVIEWS_BY_WINDOW`
D90·ALL 3→2 (D30=2 불변), `src/db/contract_validator._PROMOTION_MIN_REVIEWS`
미러 상수 락스텝. synthetic_ratio/confidence 게이트 불변. 게이트 green
(1150 passed / 50 skipped / 0 failed, ruff/mypy clean). 스냅샷·corpus
베이스라인 의도 갱신.

**서빙 도달률 (before=임계3 / after=임계2, 동일 코드 토글 실측):**

| fixture | bee_attr | keyword |
|---|---|---|
| dense (32) | 90.6%(29) → **100%(32)** | 25 → 30 |
| **wide (517)** | **5.0%(26) → 17.4%(90)** | 7 → **30** |

wide 3.5배 회복 — A4 예측치 정확 일치. C2 자체 목표(리뷰 신호 서빙 도달
회복) 달성.

**⚠️ 정직한 한계 — C1 CONCERN 신호는 서빙 미도달 (before/after 모두 0):**
P7-2 C1이 소생시킨 CONCERN 신호 9건(canonical_fact 레벨)은 상품당 support가
1이라, 완화된 임계 2(distinct_review≥2)에도 **여전히 미달**. concern 계열
연결성 신호의 "첫 발화"는 이번에도 일어나지 않음. 원인은 게이트가 아니라
**concern 데이터 희소성**(support-1). 해소 경로: (a) relation 모델 개선(학습
중 — concern 관계 생성량 증가 시) (b) ingredient_concern_map 소비 배선(P7-2
follow-up — 성분 기반으로 concern 신호를 상품에 밀도 있게 부여) (c) 임계 1
완화는 DECISIONS에서 이미 기각(교차검증 부재 위험). **임계 1 재검토는 금지 —
concern 밀도 확보가 정답.** 이 한계를 06 진단·plan follow-up에 반영.
