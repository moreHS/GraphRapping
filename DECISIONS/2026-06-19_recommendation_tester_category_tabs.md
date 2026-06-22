# Recommendation Tester Category Tabs

## Background

추천 테스트 화면이 전체 상품 후보군만 대상으로 추천을 실행하면, 사용자 관점에서
카테고리별 추천 적합성을 검수하기 어렵다. 특히 메이크업 선호 유저의 결과에
스킨케어/립케어/기타 제품이 섞일 때, 추천 엔진 문제가 카테고리 후보군 문제인지
스코어링 문제인지 분리해 보기 어렵다.

또한 기존 `score_layers.review_graph_score`에는 relation overlap이 아닌
`freshness_boost`가 포함되어 있어, 리뷰 relation 근거가 없어도 리뷰그래프 점수가
있는 것처럼 보일 수 있었다.

## Options

1. 프론트에서 결과만 카테고리별로 후처리한다.
   - 구현은 쉽지만 서버 후보 생성/스코어링은 전체 후보 기준이라 검수 의미가 약하다.
2. 추천 API 요청에 `category_group`을 추가하고 서버 후보군부터 필터링한다.
   - 탭별 후보군, evidence gate, scoring, rerank가 같은 조건에서 수행되어 검수 의미가 명확하다.
3. 카테고리 탭 대신 별도 테스트 페이지를 만든다.
   - 확장성은 있지만 현재 정적 demo UI 구조 대비 변경 범위가 크다.

## Decision

Option 2를 선택한다.

- `/api/recommend/categories`는 현재 로드된 serving product 기준 탭별 후보 수를 반환한다.
- `/api/recommend`는 `category_group`을 받아 candidate generation 전에 product id 후보군을 제한한다.
- 추천 테스트 UI는 `전체`, `스킨케어`, `메이크업`, `바디`, `헤어`, `향수`, `기타` 탭을 표시한다.
- `freshness_boost`는 relation 기반 리뷰그래프 점수가 아니므로
  `score_layers.product_activity_score`로 분리한다.
- `review_graph_score`는 keyword, BEE attr, context, concern, concern bridge, tool,
  co-used product 같은 리뷰 relation 기반 feature만 나타낸다.

## Tradeoffs

- 카테고리 그룹 판정은 테스트 UI용 coarse filter다. canonical product category taxonomy를
  대체하지 않으며, `category_name`, `category_id`, 대표상품명, category concept id의
  키워드 기반으로 분류한다.
- `기타` 탭은 분류 실패/미분류 상품을 드러내는 디버그 구좌로 남긴다.
- `product_activity_score`가 추가되어 UI 레이어가 하나 늘지만, relation evidence와
  리뷰 활동성을 섞어 보여주는 것보다 검수 정확도가 높다.

## Verification

2026-06-19 로컬 906 리뷰 데모 로드 기준:

- `POST /api/pipeline/run` -> `reviews=906`, `products=517`, `users=50`, `signals=2529`
- `/api/recommend/categories`
  - 전체 517
  - 스킨케어 308
  - 메이크업 66
  - 바디 15
  - 헤어 23
  - 향수 0
  - 기타 105
- `user_makeup_glow_20f`, `category_group=makeup` 상위 결과는 메이크업 후보군 66개 안에서 산출된다.
- relation overlap이 없는 상위 결과는 `review_graph_score=0`,
  `product_activity_score`에 `freshness_boost`가 표시된다.

Test command:

```bash
python -m pytest -q
```

Result: `713 passed, 36 skipped`.
