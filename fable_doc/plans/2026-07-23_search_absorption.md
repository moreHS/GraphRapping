# 검색 흡수 트랙 (A1~A5) — 상품명 축·부정 일반화·선호 강도·증거투명·평가 인프라 (v2)

- 날짜: 2026-07-23 (v2 — codex 계획 리뷰 REJECT 5건 전부 반영)
- 상태: 계획 (codex 2차 확인 후 착수 — 사용자 사전 승인: "리뷰받고 이상없으면 시작")
- 배경: 3자 비교 분석 `fable_doc/11_search_comparison_absorption.md`.
- 공통 원칙: 신규 파라미터는 전부 **dormant 기본값**(None/미지정) — 무질의
  추천 경로 byte-identity·스냅샷 diff 0 유지(codex 확인: dormant면 top-pin과
  비충돌). 질의 경로만 활성화. 배치마다 Opus 구현→Fable 검수→codex 리뷰.

## A1. 상품명 축 + 식별자 직행 (단독 배치 — 최우선)

### 배경/실측
- 검증 게이트 6축에 product 축 부재 → LLM `product_names` 슬롯 해소 불가.
- 라이브: "설화수 윤조에센스 어때" → 실존(2종)에도 brand+category로만 해석,
  top1=맨본윤에센스.

### 구현 설계
1. **해석 축 추가** (resolve_query_concepts): 신규 concept_type `"product"`,
   concept_id=product_id, label=대표상품명.
   - 정방향(rep_name ⊂ query) + 역방향(고립 표현 ⊂ rep_name, Tier 3식 자기제한)
     + cap `_PRODUCT_NAME_MATCH_CAP = 10` + 부정 스팬 가드.
   - **부정 상품명 = 상품 배제**(codex 1 보강): "윤조에센스 말고"는 양성
     미채택에 그치지 않고 해당 상품 id를 `excluded_product_ids`로 분류 —
     브랜드/카테고리 결과에서도 그 상품 제외. interpretation에 동봉.
2. **브랜드 모순 가드 — 병합 후 적용**(codex 1): per-slot 해석 단계가 아니라
   raw+LLM 개념 **병합 완료 후** 일괄 — 질의에 brand 개념이 있으면 그 브랜드와
   불일치하는 product 매칭 취소(모든 소비자 일관).
3. **개인화 파이프라인 관통**(codex 1 핵심 — 검색 경로만으론 불충분):
   - `query_product_ids: set[str] | None = None`(dormant)을
     `_run_scored_pipeline` → `generate_candidates(_prefiltered)`로 스레딩.
   - **후보 유니버스 보장**: 지목 상품을 소프트 축소(_narrow) 결과와 **union**
     (축소가 지목 상품을 떨어뜨리지 못함). 카테고리 게이트·성분 하드게이트·
     avoided·(A2)명시 배제는 **pin보다 우선** — 하드 필터에 걸린 지목 상품은
     핀 없이 탈락(트레이스 기록).
   - **자격/오버랩**: 지목 상품에 `product:{id}` 오버랩 부여,
     `MASTER_TRUTH_TYPES`(recommendation_evidence_index.py:13)에 `product` 추가
     — evidence gate 통과 보장.
   - **절단 생존**: `max_candidates=50` 컷(candidate_generator.py:504) 전에
     지목 상품 보존(컷 대상에서 제외 후 재합류) — 컷이 지목 상품을 못 버림.
   - **핀 위치 = 랭킹 조립 단계**(직렬화 후 post-sort 금지 — rank/rank_score/
     다양성 리랭크/related 앵커 정합 유지): 스코어러 산출 후 최종 순위 조립
     시 지목 상품을 선두 블록으로(블록 내부는 스코어 순), 나머지는 기존 순위.
     rank 필드는 조립 후 일괄 재부여(단일 소유자).
   - **리랭커 절단 생존**(codex 2차 #1): 다양성 리랭커의 `top_k*2` 윈도우·
     `top_k` 절단(reranker.py:52)도 지목 상품을 버릴 수 있음 — **핀 블록은
     리랭커 입력에서 제외**하고 리랭커는 비핀 후보만 `top_k − |핀|` 슬롯으로
     실행, 조립 = [핀 블록] + [리랭크 결과]. 핀이 top_k 이상이면 리랭커 스킵.
4. **검색(익명) 경로**: `_product_overlap`에 product 축 + 동일 선두 블록 조립.
5. **related 연동**: 지목 상품이 있으면 G5 앵커를 지목 상품 우선으로.
6. 미실존 상품명 → 기존 unresolved 칩(정직).

### 테스트 (codex 5 반영 확장)
해석(정/역방향·cap·부정 스팬), **evidence gate 생존**(개념 오버랩 0인 지목
상품이 gate 통과), **50-컷 생존**(후보 51+ 상황 합성), **하드필터 우선**
(지목 상품이 avoided/성분게이트/명시배제에 걸리면 핀 없이 탈락+트레이스),
브랜드 모순 가드(병합 후), 부정 상품명 배제(브랜드 결과에서도 제거),
로그인·익명 top1 고정, related 앵커, 다양성 리랭크와 핀 공존(순위 필드 정합),
**질의 골든 케이스 신설**(윤조에센스 시나리오 — dense/wide 스냅샷은 무질의만
보호), 무질의 byte-identity·스냅샷 diff 0.

## A2. 부정(polarity) 일반화 — 브랜드/카테고리/상품 exclude

### 배경 + codex 2 반영
- "선크림"과 "세럼"이 **같은 skincare 그룹 키워드**(category_groups.py:19-20)
  → 그룹 단위 배제로 설계하면 "선크림 빼고 세럼"이 스킨케어 전체를 지우거나
  세럼까지 배제하는 모순. **리터럴/서브타입 배제와 그룹 배제를 분리**해야 함.

### 구현 설계
1. **LLM 스키마**: `brands_excluded`, `categories_excluded` 슬롯 추가.
   사전 폴백 경로는 negation 마커 group1을 brand/category 축으로 해소해 대칭.
2. **배제 해소의 2계층**(codex 2):
   - 부정 표면이 **리터럴 카테고리 개념**으로 해소 → `excluded_category_ids`
     (리터럴). 상품의 category_concept_ids ∩ 또는 서브타입 분류 일치로 배제.
     **서브타입 포함 매칭 선행**(codex 2차 #2): 카탈로그 라벨이 복합형
     ("선크림 & 선블럭")이라 완전일치로는 '선크림'이 리터럴 해소 불가 —
     배제 해소는 **표현⊂카탈로그 카테고리 라벨** 포함 매칭을 그룹 폴백보다
     먼저 시도(부정 스팬 처리 동일, 다수 라벨 매칭 시 전부 배제 — 서브타입
     계열 일괄이 의도). 그룹 폴백은 리터럴 0건이고 표현이 그룹/탭 키워드일
     때만.
   - 부정 표면이 **그룹으로만** 해소("스킨케어 빼고") →
     `excluded_category_groups`. 유니버스 구성 = "전체 − 배제 그룹"
     (category_group 선택이 배제 그룹과 충돌하면 배제 우선 + trace).
   - **negative-wins는 concept-id 단위**(축 단위 아님): "선크림 빼고 세럼"
     = 그룹 skincare 양성 유지 + 리터럴 선크림 배제 → 세럼 검색 결과에서
     선크림류만 제거. 양성 그룹과 부정 리터럴은 상쇄되지 않음.
3. **소비**: 검색·추천 후보에서 excluded brand(brand_concept_ids ∩)/
   excluded literal category/excluded group/excluded product(A1 유래) 하드
   배제. **related products 양쪽 분기**(로그인·익명) 모두 전파(codex 5).
   **명시 배제 > A1 핀**(codex 2) — "윤조에센스 말고 설화수 에센스"에서
   윤조에센스는 핀 대상이어도 배제.
4. **완화 없음**: 명시 배제는 0건이어도 유지(POC required 미완화 원칙).
5. **표면화**: 기존 🚫 칩 재사용 + 응답 메타
   `excluded: {brands, categories, category_groups, products}`.

### 테스트
"이니스프리 말고 보습크림"(브랜드 배제), "선크림 빼고 세럼"(**리터럴 배제 —
세럼 결과 유지, 선크림류만 제거**), "스킨케어 빼고"(그룹 배제 — 유니버스
재구성), 양성 그룹∧부정 리터럴 비상쇄, 배제>핀 우선, 폴백 경로(오타 업는),
related 양분기 전파, 0건 시 비완화, byte-identity·스냅샷 diff 0.

## A3. strength(required/preferred) — "들어있으면 더 좋고"

### 배경 + codex 3 반영
- 현행 양성 슬롯은 하나의 concept_map으로 **평탄화**돼 빌더가 슬롯 출처를
  모름(raw 해석이 먼저 삽입) — `ingredients_preferred` 슬롯만 추가해선
  required와 구분 불가. **슬롯→강도 정보를 평탄화 전에 보존**해야 함.

### 구현 설계
1. **LLM 스키마**: `ingredients_preferred` 추가. 폴백 경로는 전부 required
   (LLM-off 환경 현행 유지, 문서화).
2. **강도 스레딩**(codex 3): 채택 루프에서 concept_id→강도 출처 맵을 별도
   수집(평탄화 전). 규칙 — 같은 성분군에 여러 출처면
   **avoided > required > preferred** 병합(기피 우선은 기존 차감 로직 그대로,
   required 승격은 빌더에서).
   **raw-floor 기본값 규칙**(codex 2차 #3): raw 해석이 슬롯보다 먼저
   삽입되므로 — **슬롯 분류를 먼저 확정**하고(wanted→required,
   preferred→preferred), 어떤 슬롯에도 미분류인 raw-floor 성분군만
   required 기본값. 즉 raw 표면이 있어도 LLM이 preferred 슬롯에 넣었으면
   preferred 유지(raw 기본값이 명시 분류를 덮지 않음). 양 케이스 테스트 고정.
3. **IngredientConstraint.strength** ("required"|"preferred"). to_dict 추가
   필드 = additive 계약 변경(무질의 byte-identity 무관 — 질의 응답에만 등장,
   관련 테스트 기대값 갱신 명시).
4. **하드게이트 조건 = `provenance=="raw" ∧ strength=="required"`** —
   서버 두 사이트(로그인 ~1645·익명 ~1800) 모두. preferred는 게이트 제외
   + PREFERS_INGREDIENT 부스트 유지.
5. **익명 preferred-only 의미론 정의**(codex 3): 검색 자격이 "오버랩≥1"이라
   preferred 성분만 있는 익명 질의는 구조상 함유 상품만 반환됨(사실상 필터와
   동일 결과). 이를 **문서화된 퇴화 케이스**로 확정 — 표면화는 "선호 반영"
   톤(필터 문구 아님), `ingredient_filter.applied=false` +
   `ingredient_preferences` 메타로 구분. 비함유까지 섞는 전카탈로그 랭킹은
   도입하지 않음(무근거 노출이 더 부정직).
6. **표면화**: "선호 반영: {라벨}" 칩 + `ingredient_preferences: [...]` 메타.

### 테스트
preferred 분류(필터 미적용+부스트+표면화), required 회귀, required∧preferred
승격, 기피 우선, **강도-출처 병합**(같은 성분군 wanted+preferred 동시),
익명 preferred-only 퇴화 케이스 명시 테스트, 폴백 required 유지,
byte-identity.

## A4. 증거 상태 투명화 (A3와 동일 배치 또는 직후 — codex 순서 반영)

### 배경 + codex 4 반영
- MAIN_INGREDIENT는 **전성분이 아님** — 비어있지 않아도 성분 X 부재를 증명
  못함. "명시 no"와 "unknown"의 정직한 구분은 3상태:
  - `matched`: 가용 증거(구조화/raw/이름)에서 매칭
  - `unmatched_in_available_evidence`: 증거는 있으나 미매칭(부재 증명 아님)
  - `no_evidence`: 성분 증거 필드 자체가 전무(구조화·raw 공백 + 이름 미매칭)

### 구현 설계
1. matcher에 상태 반환 변형(기존 판정 무변경 — 배제는 동일, 집계만 분리).
2. **분모 확정**(codex 4): 카테고리 게이트 + (A2)명시 배제 + avoided 적용
   **후**, 소프트 축소 **전** 유니버스(성분 하드게이트와 동일 단계).
3. **다중 성분군 집계**: `no_evidence` 카운트 = 게이트 탈락 상품 중
   **최소 1개 required 성분군이 no_evidence**인 상품 수(증거가 있었다면
   매칭됐을 수 있는 상품 — 정직한 "확인 불가" 정의).
4. 메타 `ingredient_filter.evidence_unknown_products: N` + 프론트 문구
   ("성분 정보가 없어 확인 불가한 상품 N개 제외") — 필터 적용 시에만.
5. 커버 케이스: 이름-only 매칭 상품(=matched), X-free 이름(가드 유지),
   avoided 선차감 상품(분모 제외), relax 시(필터 해제 — unknown 문구 미표시).

### 테스트
3상태 판정(합성: 구조화 보유/raw만/전무/이름-only/X-free), 분모(배제·avoided
차감 후), 다중 성분군 unknown 집계, relax 시 미표시, 기존 필터 결과 불변.

## A5. 평가 인프라 — gold-vs-LLM 손실 분해 (트리거형, v1과 동일)

- 착수 조건: 평가 트랙 재개 결정 시(0.5 라벨·NDCG 재상정과 함께).
- 설계: `tests/eval/retrieval_queries.yaml`(30~50문항, 계층: 정확 상품명/
  브랜드+카테고리/required 성분/기피/선호/복수 조건/프로파일 참조/정당한 0건/
  오타) + gold interpretation 주입 vs LLM 페어 실행(`scripts/eval_retrieval.py`)
  → 추출 손실·검색 손실 분해. 메트릭 Hit@1/3·nDCG@10·required 충족률·부정
  위반률·false-zero. holdout 30% 동결(특정 문항 겨냥 수정 금지 — POC §13 준용).
- 도입 시 베이스라인 리포트 → 이후 질의 배치의 선택 게이트.

## 구현 순서 (codex 5 반영)

| 순서 | 배치 | 비고 |
|---|---|---|
| 1 | **A1 단독** | 랭킹 관통 변경이라 단독 격리 |
| 2 | **A2** | 배제 유니버스 확정(A4 분모 의존) |
| 3 | **A3 (+A4 동승 가능)** | A4는 A2 유니버스·A3 의미론 의존 |
| 대기 | **A5** | 트리거 대기 |

## codex 계획 리뷰(1차 REJECT) 처리 내역

| # | 지적 | 처리 |
|---|---|---|
| 1 | A1이 개인화 파이프라인(후보생성·evidence gate·50컷·리랭커) 미관통, post-sort 부작용, 병합 전 브랜드 가드, 부정 상품명 미배제 | dormant 스레딩 + product=MASTER_TRUTH + 컷 생존 + 랭킹 조립 단계 핀 + 병합 후 가드 + 상품 배제 신설 |
| 2 | 선크림·세럼 동일 그룹 → 그룹 배제 모순 | 리터럴/그룹 2계층 배제 + concept-id 단위 negative-wins + 배제>핀 + related 양분기 전파 |
| 3 | 슬롯 평탄화로 강도 유실, 하드게이트 조건 미정, 익명 preferred-only 미정의 | 평탄화 전 강도 맵 + avoided>required>preferred + raw∧required 게이트(2사이트) + 퇴화 케이스 문서화 |
| 4 | no/unknown 구분 불가(MAIN_INGREDIENT 비전수), 분모·다중군 미정의 | 3상태 모델 + 분모(게이트 단계) + 최소1군 no_evidence 집계 + A2/A3 뒤로 재배치 |
| 5 | 테스트 누락(게이트 생존·절단·우선순위·강도병합 등)·순서 | 전 항목 테스트 반영 + A1 단독→A2→A3(+A4)→A5 |

**codex 2차(REJECT 3건) 처리**: ① 리랭커 `top_k*2` 윈도우/절단 — 핀 블록을
리랭커 밖으로(비핀만 `top_k−|핀|` 슬롯) ② 복합 라벨("선크림 & 선블럭") 리터럴
해소 불가 — 배제 해소에 표현⊂라벨 포함 매칭을 그룹 폴백보다 선행 ③ raw-floor
성분군 강도 미정 — 슬롯 분류 선행, 미분류 raw만 required 기본(명시 preferred
불침범). 3건 반영으로 계획 확정(v3) — 각 배치 구현 시 codex 코드 리뷰가 재검증.
