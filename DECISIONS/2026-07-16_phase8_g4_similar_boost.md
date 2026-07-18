# Phase 8 P8-3a — G4 일반추천 유사도 boost 결정 기록

날짜: 2026-07-16 · 계획: `fable_doc/plans/2026-07-16_phase8-3_g4_similar_boost_g5_query_related.md` §1 ·
부모: `fable_doc/plans/2026-07-15_phase8_shared_node_projection.md` §G4 ·
계약: `docs/architecture/db_consumer_contract.md` §13

## 1. boost-only 확정 (단독 자격 불가, 전 모드)

`similar`(evidence family 명 **`PRODUCT_SIMILARITY_AFFINITY`**)는
`BOOST_ONLY_TYPES` 에 편입하고 `BOOST_ONLY_ADMISSIBLE_TYPES` 에는 **넣지 않는다**
({`comparison`}만 유지). 어떤 모드에서도(COMPARE 의 opt-in 포함) similar 단독으로
`eligible=true` 가 될 수 없다 — "보유 상품과 속성을 공유한다"는 관련성(relatedness)이지
그 자체로 추천 사유가 아니다(D1 collab / D2 comention 과 동일 규율). §13.3(1) 이행.
같은 이유로 boost-only 타입은 기대셋 `known_families` 에 추가하지 않는다
(§13.3(3) 예외 — 이번에 계약 문서에 명문화).

## 2. 가중 0.02 근거 (§13.3(2) 발화율-가중 반비례)

ungated 공유노드 유사도는 상품 커버리지 ~99%(P8-1 실측)로 "거의 항상 켜지는" 신호다.
§13.3(2)의 규칙(발화율이 높을수록 가중은 낮게)에 따라 최저 계열인
coused/collab/comention "나와 연결된 상품" 계열(0.02)에 앵커링하고 그 위로 올리지
않는다. `similar_product_weight: 0.02`, top-level 키(`features:` 맵 밖 —
프론트 슬라이더 계약 `SCORING_FEATURE_KEYS` 무변경). 4중 캡:
SAT=30 포화 + 가중 0.02 + boost-only + retrieval 집계 제외 — ungated 최고점
(max≈207, 변형상품쌍)도 contribution 상한 0.02 를 넘지 못한다.

## 3. SAT(strength 포화 상수) = 30.0 근거 — 측정 소스·명령·분모

- **측정 소스**: audit 실배선 함수
  `scripts/audit_recommendation_evidence._build_ungated_similarity`
  (서빙 스토어와 동일한 3-함수 조합: `build_product_nodes` → `build_idf` →
  `build_similarity_signals(category_gate=False)`, symmetrize 없음, top_n=10 기본).
- **명령**: `run_full_load(kg_mode=on, source_review_stats 포함)` 로 픽스처 적재 후
  위 함수 호출, 전 anchor 의 이웃 score 를 평탄화해 분포 산출(구현 검수 시 원라이너로
  재현 — 계획 §0 의 코덱스 교차측정과 동일 방향).
- **분모(n)**: top-N(10) 절단 후 **방향별 이웃 엔트리 수**.
  - wide(517상품/50유저): anchors=513, **n=5096, median 6.47, p90=31.63, max 207.34**
  - dense_golden(32상품/6유저): anchors=32, n=320, median 6.45, p90=11.86, max 45.0
- **채택**: wide p90(31.63)을 깨끗한 상수로 반내림한 **30.0**
  (`candidate_generator._SIMILAR_STRENGTH_SATURATION`). p90 부근 이웃이
  strength≈1.0 에 도달하고, 그 위 고점(변형상품쌍)은 1.0 클램프로 평탄화된다.
  `strength = min(score / 30, 1.0)`, scorer 는 anchor 합산 후 `min(Σ, 1.0)` 재클램프.
- 계획 §0 v2 의 "n/median 조립 경로 차이(5001/5.19 vs 5096/6.47)"는 audit 실배선
  기준 **5096/6.47** 로 확정한다(코덱스 측 조립 경로가 아닌 실제 배선 경로 수치).

## 4. overlap_score 집계 제외 — 기존 3종과의 비대칭

`similar` overlap 은 retrieval 정렬·50컷에 쓰이는 `overlap_score` 집계에서
**제외**한다(`len(overlap) - similar_overlap_count`). boost-only 신호가 첫 관문
(retrieval cut)의 순서를 사는 것을 원천 차단 — eligible 후보 >50 상황에서 similar
유무로 컷 구성이 바뀌지 않음을 계약 테스트로 고정.
**비대칭**: 기존 boost-only 3종(comparison/collab/comention)은 종전대로 집계에
포함된다. 이는 3종의 도입 시점 스냅샷을 보호하기 위한 의도적 유지이며(제외로
바꾸면 기존 랭킹이 움직인다), "3종도 제외로 통일"은 별도 재승인이 필요한
**후속 결정 후보**로 남긴다.

## 5. 수동 슬라이더(`Scorer.load_from_dict`) 의미론

`load_from_dict` 는 top-level boost 가중(collab/comention/similar)을 로드하지
않는다(D1/D2 선례 유지 — 배선 변경 없음). 수동 가중 실험 경로에서 similar
contribution 은 항상 0 이며, 이 의미론을 테스트로 고정했다. 프리셋 경로는
`load_config()` 선행 호출로 boost 가중이 살아 있다(기존 preset 코드 경로 그대로).

## 6. 발화 실측과 "배선 완료 + 구매 데이터 대기" 판정

서빙 유저 owned 엣지 실측: wide **1/50 유저·1엣지**, dense_golden **1/6 유저·1엣지**
(둘 다 user_dry_30f → 58763 아이오페 에어쿠션). 따라서 G4 는 현 데이터에서
**준-dormant** — D1 전철의 "배선 완료 + 구매 데이터 대기" 판정. 계약은 synthetic
테스트로 고정했고, 액션/구매 스트림(Track E) 유입 시 코드 변경 없이 자동 활성.
로드타임 실측(ungated 사이드카 빌드, nodes+idf 포함): wide ≈ **123ms**,
dense ≈ 5ms — 서빙 refresh(300s 주기) 1회당 비용으로 수용.

## 7. dense 스냅샷 전제 정정 경위 (계획 §0 대비 유일한 반증)

계획 §0/§1.6 은 "스냅샷 diff = dense 무변경(0) / wide user_dry_30f 1명 범위"를
예상했다. 구현 착수 전 실측에서 **dense_golden 에도 user_dry_30f 가 58763 을
보유**하고, 그 ungated 이웃(60892·50165·60766·103537·36903·61257·19929·52110·
60188)이 **이미 dense 커밋 스냅샷의 eligible top-N 에 존재**함을 확인 — boost
배선 시 dense diff 는 0 이 아니라 user_dry_30f 스코프 20줄(rank/score 기준,
all/skincare/haircare/makeup 4개 탭)이었다. 시뮬레이션의 boost-OFF baseline 이
커밋 스냅샷과 완전 일치함을 검증해 diff 전량이 boost 기여분임을 입증한 뒤
중지·보고했고, 사용자가 **(A) dense+wide 골든 동시 재승인**(테스트 기대값 기록
2파일만 재기록, 원천 데이터 무변경)을 확정해 재생성을 진행했다. wide 는 계획
예상(1명 범위)과 일치. — "dense diff 0" 전제만 사실오류였고 나머지 계획 전제
(owned 1/50, p90≈31.6, top-10 이웃 구성)는 전부 실측 재현되었다.

## 8. 사이드카 설계 (프로파일 무접촉)

ungated 인덱스는 **스토어 사이드카**로만 보관한다: DBServingStore
`_refresh`(gated attach 와 같은 호출에서 nodes/idf 재사용, 쌍 열거만 2회) /
demo `load_demo_data` → `DemoState.similar_ungated` / audit 스크립트(동일 3-함수
조합). 접근자 `get_ungated_similar(product_id)` (ServingStore 프로토콜 확장).
프로파일에는 어떤 필드도 추가하지 않아 `/api/products`·검색·추천 payload 가
불변이고, P8-2 고정 테스트("훅이 추가하는 키는 `similar_product_ids` 하나")가
그대로 감시한다. provenance(§13.3(5))는 서버 후처리로 similar path 에
(anchor, candidate) 쌍의 shared_axes 를 사이드카에서 조회해 additive 동반
(신규 DB 조회 없음), keyword 축 복합키는 wrapped_signal/demo 신호 행으로
역추적됨을 계약 테스트로 고정.

---

## 추기 (2026-07-18): §4 비대칭 해소 — boost-only 4종 retrieval 집계 통일

P8 마감 스윕(A1)에서 §4가 남긴 후속 결정 후보("3종도 제외로 통일")를
**발화 0 실측으로 스냅샷 무영향 확인 후** 실행했다.

- **변경**: `candidate_generator.overlap_score` 를
  `len(overlap) - similar_overlap_count`(similar만 제외)에서
  **BOOST_ONLY_TYPES 4종 전체 제외**로 통일 —
  `sum(1 for c in overlap if _type(c) not in BOOST_ONLY_TYPES)`.
  `similar_overlap_count` 변수 제거, `BOOST_ONLY_TYPES` 는
  `recommendation_evidence_index` 에서 임포트.
- **무영향 근거(2026-07-18 실측, fable_doc/08 §A1)**: comparison overlap 발화
  **0**(dense/wide 모두 — 서빙 `top_comparison_product_ids` 보유 상품 자체가 0),
  collab/comention attach 콜사이트 **0**. 4종 전부 현재 발화 0이므로 제외 집합이
  종전(similar만 제외) 대비 실질적으로 동일 → retrieval 50컷 구성 불변 →
  **랭킹 스냅샷 byte-identical(재생성 없음)**.
- **불변식 테스트**: 4종 각각에 대해 "boost-only overlap이 있어도 overlap_score
  불변 · >50 eligible 상황에서 50컷 순서 불변"을 파라미터라이즈로 고정
  (tests/test_similar_boost.py — 기존 >50컷 테스트 패턴 확장).
- **효과**: 실데이터 유입으로 comparison/collab/comention 이 발화하기 시작해도
  이 비대칭 때문에 diff 재승인이 필요해지는 상황을 선제 차단(지금이 가장 싼 시점).
