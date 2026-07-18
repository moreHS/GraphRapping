# Phase 8 A2 — 브랜드 단독 공유 이웃 미노출 정책 (유사상품 서피스)

날짜: 2026-07-18 · 배경 문서: `fable_doc/08_remaining_items_review_2026-07-18.md` §A2 ·
관련: `fable_doc/plans/2026-07-15_phase8_shared_node_projection.md` §Track G,
`DECISIONS/2026-07-16_phase8_g4_similar_boost.md`

## 결정

유사상품 서피스(G3 위젯 / G2 그래프)에서 **`shared_axes`가 brand 축 하나뿐인
이웃은 미노출**한다. "같은 브랜드"만으로는 유사상품 근거로 빈약하다는 도메인
규칙. 계산 모듈(`src/rec/product_similarity.py`)은 무변경 — 순수 **서피스 필터**로
`src/web/serving_store.build_and_attach_similarity`의 **gated 체인에서 symmetrize
직전**에 적용한다(`_drop_brand_only_signals`).

## 근거

- **'기타' 그룹 실측(2026-07-18)**: wide **78/517(15%)** 상품이 6개 카테고리 그룹
  어디에도 못 들어가는 이질 묶음(슬리밍 8·Beauty서비스기타 6·구강 6·뷰티툴 6·
  비누 5·칫솔 4·네일 4·카테고리명 없음 4 …). 칫솔을 스킨케어에 넣을 수 없으므로
  **그룹 매핑 보강으로 풀 문제가 아님**. 이들이 같은 그룹으로 묶이면 게이트 의미가
  약해져, **브랜드만 공유(IDF≈1.02)한 약한 이웃이 유사상품으로 노출**될 수 있다
  (P8-2 실측: 상품 100317의 이웃 2·3위가 이니스프리 단독 공유).
- 서피스 필터로 한정하는 이유: `similar_product_ids`(gated attach)는 추천 후보
  생성이 절대 읽지 않는다(grep 검증된 P8-2 안전계약). 따라서 이 필터는 **랭킹
  스냅샷에 무영향**이고 G2/G3 표시 품질만 개선한다.

## 검토한 선택지

- **(a) 브랜드 단독 배제 — 채택(사용자 승인)**: `shared_axes` 전부가 brand이면
  제거. 명확·결정적, 튜닝 상수 없음. 도메인 규칙과 직결.
- (b) `min_score` 임계(예: ≥2.0) — 기각: 수치 튜닝이 필요하고, 니치 브랜드
  단독(IDF 3+)을 통과시키는 부작용(그게 자연스러울 수도 있어 (a)와 판단 방향이
  반대). 임계값이 코퍼스 규모/IDF 분포에 종속되어 유지비가 큼.
- (c) 현상 유지 — 기각: 점수상 정직하게 하위이긴 하나 데모 인상이 약함(브랜드
  단독 이웃이 상위에 보임).

## 양방향 일관성 (구현 불변식)

pair `(A,B)`의 `shared_axes`는 symmetrize 이전 **양방향 동일**
(`neighbor_shared[a][b]`와 `[b][a]`가 같은 노드 리스트). 따라서 "brand 단독"
판정도 양방향 동일 → 필터를 symmetrize 전에 걸면 A→B·B→A가 함께 제거되고,
symmetrize가 역엣지를 되살릴 씨앗(잔존 방향)이 없다.

## 범위 경계

- **gated 체인만**: 어태치되는 `similar_product_ids`(G2/G3)에서만 배제.
- **ungated 사이드카 무변경**: G4 boost / G5 related는 필터하지 않음 — boost는 점수
  기여일 뿐이고 lone-brand 노드의 IDF가 이미 감쇠하며, 여기를 건드리면
  스냅샷-중립 boost 채널이 흔들린다.

## 커버리지 재실측 (필터 전/후, 게이트 ON)

`_drop_brand_only_signals` 적용 전후로 "이웃≥1 보유 상품 비율(gate ON)"이 ≥60%
하한을 유지하는지 확인(완료 보고서 참조). dense도 동일 보고.
