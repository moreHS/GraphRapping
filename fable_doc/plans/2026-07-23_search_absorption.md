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
   - **핀 > top_k 절단 정책**(구현 리뷰 F6 확정): 응답 크기 계약(top_k)이
     우선 — 핀 블록도 top_k에서 절단하되 점수순 결정적, 잘린 핀은
     `pinned_dropped`(reason="top_k")로 투명 기록. "전체 핀 블록 무조건
     유지"가 아님을 명시(v3 문구의 모호함 해소).
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

## A1 완료 보고 (2026-07-23, Opus 구현+수정 라운드·Fable 검수·codex 리뷰)

**구현**: 9파일 +1,180/−39 (신규 tests/test_search_absorption_a1.py 16건 포함,
전체 A1 테스트 ~52건). product 해석 축(정/역방향+cap 10+부정 가드+브랜드·
카테고리·그룹 표면 억제) · `excluded_product_ids`(`_negated_products` — 스팬
기반 다어절 해소, 최장 일치) · 병합-후 브랜드 모순 가드 · dormant 스레딩
(query/excluded product ids → `_run_scored_pipeline`→candidate_generator) ·
`product`=MASTER_TRUTH 등재 · 50-컷/프리필터/리랭커 절단 생존
(`_rerank_with_pins` — 핀 블록 리랭커 밖, top_k 절단은 점수순+
pinned_dropped(reason=top_k) 투명 기록) · 하드필터>핀(사유 trace) · 검색/
related 연동(핀 앵커 우선·배제 전파) · 응답 메타 pinned_product_ids/
pinned_dropped.

**codex 구현 리뷰 REQUEST CHANGES(P1 6·P2 6) → 전부 수정**: 익명 재해석의
가드 우회 차단(호출자 interp 권위) · **negation 공유 어휘에 '말고' 추가**
(성분 경로 동반 개선 — "레티놀 말고" 기피 동작) + 다어절 상품명 스팬 부정
("헤라 블랙 쿠션 빼고") · **LLM 슬롯별 개념 타입 제한**(_SLOT_CONCEPT_TYPES —
성분 슬롯 '콜라겐'이 "콜라겐 크림" 상품을 핀하던 교차 오염 제거) · 익명 핀의
카테고리 게이트 · relax 카운트 배제 선차감 · 핀 top_k 절단 정책 확정 ·
상품 부정의 성분 경고 중복 제거 · 그룹 키워드 역방향 억제 · dedupe ·
related 앵커 점수순 · top_k≤0 가드 · 배제 trace 도달성.

**게이트**: pytest **1605 passed / 50 skipped**(+50), ruff/mypy 클린, 무질의
byte-identity·랭킹 스냅샷 diff 0. **라이브(8123)**: "설화수 윤조에센스 어때"
로그인·익명 top1~3=윤조에센스 계열 핀(기존 top1 맨본윤에센스 교정) ·
"윤조에센스 말고" 배제 누출 0 · "콜라겐 든 크림" 상품 핀 오염 없음 ·
"레티놀 말고" 성분 기피 · 알콜업는/히알루론/콜라겐 회귀 무변.
**후속 기록**: 익명 일반 검색의 전면 카테고리 게이팅(레거시 동작 변경이라
이번 스코프 아웃 — 별도 결정 필요).

## A2 완료 보고 (2026-07-23~24, Opus 구현+수정 2라운드·Fable 검수·독립리뷰·codex)

**구현**: 7파일 +~1,250 (신규 test_search_absorption_a2.py 22건 포함 A2
테스트 ~60건). LLM `brands_excluded`/`categories_excluded` 슬롯 + 사전 폴백
스팬 해소 대칭(공용 `_iter_negation_spans`) · **배제 2계층**(Layer-0 정확
그룹라벨[기타 포함]→리터럴 표면⊂라벨→정확 일치 그룹 폴백) · negative-wins
concept 단위("선크림 빼고 세럼" 비상쇄) · 축 우선순위 product>brand>
category>ingredient(consumed-surface 스킵) · 하드 배제 전 소비 지점(후보
유니버스 선제거·검색·related 양분기·relax 유니버스·배제>핀 trace) ·
완화 없음 · `excluded` 응답 메타(라벨 dedupe) · 배제-only 익명 안내 문구.

**구현 중 판단 3건(수용)**: Layer-0 그룹라벨 오버라이드(부수 리터럴
'스킨케어기타' 오포섭 방지) · 그룹 폴백 정확 일치("윤조에센스 빼고"의 에센스
부분매칭이 그룹 전체를 지우는 것 방지) · 축 우선순위 신설.

**독립 리뷰+codex 리뷰(REQUEST CHANGES P1 5·P2 3) 전부 수정**: 스팬 축 점유
엄격화(브랜드=후보 전체 일치 — "이니스프리 선크림 빼고"가 브랜드를 삼키던
버그) · LLM 배제 슬롯의 consumed-span 대칭 · 리터럴 배제를 id 파생에서
**상품별 라벨 직접 판정**(`excluded_category_surfaces`)으로(링크 누락 누수·
공유 id 과배제 제거) · 배제 상품 유니버스 단일 지점 선제거(false-zero 해소) ·
**부정 해소 경량 인덱스 + cap**(45k 벤치 3.28s→0.15s, ~20배) · 발생 단위
스팬 dedupe · 배제-only 판정 정확화 · 기타 그룹 · _excluded_meta 무가드
스캔 제거 · 라벨 dedupe · product>brand 스킵 e2e 단언.

**게이트**: pytest **1648 passed / 50 skipped**(+43), ruff/mypy 클린, 무질의
byte-identity·스냅샷 diff 0. **라이브**: "이니스프리 선크림 빼고 세럼" →
선크림 리터럴만 배제(25개)·이니스프리 생존·누출 0 · "이니스프리 말고
보습크림" 누출 0 · "스킨케어 빼고"(로그인) 전원 비스킨케어 · 배제-only 익명
안내 표시 · 핀/성분 트랙 회귀 무변. 세션 리밋 중단 1회 복구 포함.

## A3+A4 완료 보고 (2026-07-24, Opus 구현·Fable 검수)

**구현**: 4파일 (신규 test_search_absorption_a3_a4.py 15건 + test_query_understanding.py
A3 8건 = +23 테스트).

**A3 (강도 required/preferred)**
- LLM 스키마 `ingredients_preferred` 슬롯 추가(시스템 프롬프트 예시 + 스키마
  키). `_SLOT_CONCEPT_TYPES`에 {ingredient} 등록, 신설 `_SLOT_STRENGTH`
  (wanted→required, preferred→preferred).
- **강도 스레딩**: `_interpret_with_llm` 양성 채택 루프에서 concept_id→강도
  맵을 concept_map 평탄화 **전에** 수집. setdefault no-op(raw 해석 선삽입)여도
  기록하고, `ingredients_wanted`가 `_POSITIVE_FIELDS`에서 preferred보다
  앞이라 required 절대 하향 안 됨(required-wins).
- **raw-floor 기본값**: `_build_ingredient_constraints`가 그룹 concept들의 강도
  집계 — required 있으면 required, 없고 preferred 있으면 preferred, 둘 다 없는
  (슬롯 미분류) raw-floor 그룹만 required 기본. → 명시 preferred가 raw 기본에
  덮이지 않음(양 케이스 테스트). 폴백(LLM off)은 strength_by_cid 미전달 → 전부
  required(문서화).
- `IngredientConstraint.strength: str = "required"` 필드 + to_dict(additive).
- **하드게이트 = provenance=="raw" ∧ strength=="required"** — 서버 두 사이트
  (로그인·익명) 모두. preferred는 게이트 제외 + 기존 PREFERS_INGREDIENT 부스트
  유지 + 응답 메타 `ingredient_preferences: [라벨]` 신설(양 사이트).
- **익명 preferred-only 퇴화**: 오버랩≥1 구조상 함유 상품만 반환됨을 문서화된
  퇴화로 확정 — applied=false 유지 + ingredient_preferences로 "선호 반영" 표면화,
  전카탈로그 랭킹 미도입(명시 테스트).

**A4 (증거 3상태 투명화)**
- matcher에 `ingredient_evidence_state`(matched / unmatched_in_available_evidence
  / no_evidence) + `count_evidence_unknown_products` 신설 — **기존 판정/배제
  무변경, 집계 전용**. 상품명은 no/unmatched 분기의 성분 증거로 치지 않음
  (이름은 성분목록이 아님 → matched만 생성).
- **분모** = 카테고리 게이트 + A2 명시 배제 + avoided 제거 후 유니버스(하드
  게이트와 동일 단계). **집계** = 게이트 탈락 상품 중 최소 1개 required 성분군이
  no_evidence인 상품 수.
- 응답 메타 `ingredient_filter.evidence_unknown_products: N` — **적용 시에만**
  의미(미적용/relaxed 시 0 강제). X-free 이름 가드 유지, avoided 선차감(분모
  제외), relax 시 0.
- 프론트 무수정(별도 폴리시 배치) — 서버 메타까지.

**검수 반영**: A4 `no_evidence` 정의를 "성분 필드 전무"로 엄격화(이름 존재는
증거 아님) — 부재 증명 불가한 "확인 불가"만 카운트, 성분목록 보유하나 미함유
(unmatched)는 제외.

**게이트**: pytest **1671 passed / 50 skipped**(+23), ruff/mypy 클린, 무질의
byte-identity·랭킹 스냅샷 diff 0(기존 recommend 스위트 그린), A1 핀·A2 배제
회귀 무변. **시뮬**: "히알루론 들어있으면 더 좋고 보습 크림"(preferred, LLM
mock) → constraint provenance=raw·strength=preferred, 로그인 payload
applied=false·ingredient_preferences=['히알루론']·비함유 포함 전원 반환(하드
필터 안 걸림). "히알루론 든거"(required, 폴백) → provenance=raw·strength=required,
applied=true·matched=1·evidence_unknown_products=1(성분필드 전무 NO_EV만;
성분목록 보유 미함유 OTHER_ING은 제외)·CARRIER만 반환.

**계획과 달라진 판단**: 없음(계획 확정본 그대로 구현). shape 테스트 3건
(test_api_ask·test_ingredient_constraint·test_related_products)은 additive 계약대로
갱신. A5 스코프 아웃·프론트 무수정 준수.

## A5 완료 보고 (2026-07-24, Opus 구현·Fable 검수)

> 아래는 **codex A5 리뷰(REQUEST CHANGES HIGH 5·MED 3) 전부 반영 후 최종본**. 초기
> 수치(nDCG 0.883 등)는 판정 규칙이 느슨해 낙관 편향이었고, 규칙을 질의 의도에
> 맞게 조인 뒤 재생성한 정직한 값으로 교체함(낮아졌지만 그게 맞음).

**구현**: 신규 3파일, 프로덕션 코드 무접촉(평가는 관찰만 — monkeypatch로 충분).
- `tests/eval/retrieval_queries.yaml` — **44문항**, 실카탈로그(517) 사실 기반. 계층
  분포: exact_product 5·brand_category 6·ingredient_required 7·ingredient_avoided
  5·ingredient_preferred 3·brand_category_excluded 5·multi_constraint 4·profile_ref
  3(login)·legit_zero 2·typo 4. **holdout 14(31.8%, 계층별 ≥1)**. legit_zero는
  의도적 out-of-domain(강아지 사료/노트북 — 아무 개념도 미해소 → 정직한 0건).
- `scripts/eval_retrieval.py` — 하네스. 순수 헬퍼(데이터셋 로드·gold 조립·판정
  엔진·메트릭)와 인프로세스 파이프라인 드라이버(load_demo_data 1회 + TestClient)
  분리. gold 조립 = gold 슬롯→LLM raw JSON→**실 `_interpret_with_llm` 게이트**
  (동일 검증/제약빌더/negation/브랜드가드 통과). 판정(수작업 라벨 0)은 **의도
  충실**: gain 2=정확 지목 상품(explicit product_ids 또는 name 토큰 전부 AND) ·
  gain 1=질의의 전 제약 AND 충족(brand∧category_surface∧must_contain_ingredient∧
  profile_brand) · violations(must_not_brand/ingredient/category_surface/group).
  성분 판정은 시스템 자체 매처(required=product_passes_constraints, avoided=
  concept-id ∩) 재사용 — 객관·grounded. **카테고리 서브타입은 카탈로그 사실
  기반 family**(에센스=세럼=앰플, 스킨=토너 — K-beauty 동일군)로 매칭해 리터럴
  라벨 불일치 인공 오답 제거. 메트릭 **ExactHit@1/3·RelevantHit@1/3(각 분모 n
  명시)·nDCG@10·required 충족률(required 문항만)·preference_reflected(preferred
  문항, 캐리어 순위)·부정위반률·correct/false-zero·해석 슬롯 P/R(micro)**. 리포트
  매니페스트는 **데이터셋·하네스·카탈로그·프로파일 sha256 + git dirty + 커맨드
  라인 + profile_source + 실제 LLM 사용여부**. jsonl(문항별 원자료) + md(요약).
- `tests/test_eval_harness.py` — **21건**. 판정(gain 2/1/0·AND결합·profile_brand·
  위반·성분 주입 constraint)·nDCG 수계산·ExactvsRelevant 분모·required만 채점·
  preference_reflected 순위·**false_zero 신정의**(gain>0 0건∧카탈로그 relevant
  존재)·gold raw 매핑·**per-surface gold 레퍼런스(F6)**·holdout≥30%·슬롯 P/R·
  집계 분모. LLM 미호출(순수 로직만).

**베이스라인**(dev 30문항, `tests/eval/reports/baseline_20260724.{jsonl,md}` — 값(분모 n)):
| 메트릭 | gold | actual(폴백) | 추출손실 |
|---|---|---|---|
| ExactHit@1 | 1.000 (4) | 0.750 (4) | 0.250 |
| ExactHit@3 | 1.000 (4) | 1.000 (4) | 0 |
| RelevantHit@1 | 0.448 (29) | 0.448 (29) | 0 |
| RelevantHit@3 | 0.621 (29) | 0.621 (29) | 0 |
| nDCG@10 | 0.465 (29) | 0.461 (29) | 0.004 |
| required 충족률 | 0.762 (8) | 0.762 (8) | 0 |
| 부정 위반률 | 0.000 (9) | 0.000 (9) | 0 |
| correct-zero | 1.000 (1) | — | — |
| false-zero | 0.069 (29) | 0.069 (29) | 0 |
- **LLM 불가 사유 기록**: `GRAPHRAPPING_QUERY_LLM=azure`로 실행했으나 Azure
  엔드포인트가 사설망 주소(10.105.104.167 — VPN 필요)로 이 호스트에서 도달 불가
  (ConnectError). 실 LLM 경로는 문서화된 사전 폴백으로 degrade → 하네스가
  매니페스트(`llm_actually_used=False`·`actual_extraction_path=dictionary_fallback`)
  와 리포트 NOTE로 자동 명시. "actual" 패스 = **사전 폴백**(실 LLM 아님). VPN
  환경 재실행 시 동일 커맨드로 실 LLM 베이스라인 산출.
- **손실 분해 핵심**: (검색 손실) gold RelevantHit@1 0.448·nDCG 0.465 — 완벽
  추출에도 익명 검색이 요청 **카테고리 서브타입을 순위에 반영 안 함**(예 "헤라
  쿠션" top1=헤라 아이라이너, 실제 쿠션 rank5 / "히알루론 크림" top1=히알루론
  마스크, 실제 크림 rank5) — 진짜 랭킹 품질 이슈로 노출. (추출 손실) 폴백은
  ExactHit@1만 1.0→0.75(products recall 0.133 — 윤조에센스만 핀), preferred
  recall 0.0(폴백 preferred 슬롯 없음→required 붕괴), profile_refs P/R 0.5.
  typo 2건은 gold·폴백 모두 false-zero(교정어가 raw-provenance 아니라 하드게이트
  미발동 — A3 raw-floor 설계의 알려진 한계, 검색 손실). preference_reflected는
  익명 preferred가 캐리어만 반환(A3 문서화 퇴화)이라 비교 대상 없어 n=0(정직).
- **F6 검증**: gold 슬롯 P/R이 더는 동어반복 아님 — per-surface gold vs 조립
  interp 비교라 brands P=0.917·categories P=0.860(전질의 조립이 부가 개념 해소)로
  조립 충실도가 실제 측정됨.

**게이트**: pytest **1697 passed / 50 skipped**(신규 harness 21건), ruff(src tests
scripts)·mypy(src) 클린. 프로덕션 코드 무수정이라 무질의 byte-identity·스냅샷
diff 0은 기존 recommend/snapshot 스위트 그린으로 보장. holdout 14문항은 생성만·
기본 리포트 제외(동결 규율 — `--holdout`로만 포함).

**계획과 달라진 판단**: (1) gold 조립을 gold 슬롯→`_interpret_with_llm` 재사용
(프로덕션 게이트 무편차). (2) 판정에 `must_not_category_group`·`category_surface`
family·`profile_brand` 추가(의도 충실 + 카탈로그 라벨 현실 반영). (3) "llm" 패스
사내망 미도달→사전 폴백 자동 감지·명시. 나머지 계획 확정본 그대로.

**(A3+A4) codex 리뷰 수정 라운드(REQUEST CHANGES 2건 전부 반영)**: ① F1(P1) — LLM이 같은
표현을 비강도 슬롯(desired_attributes)과 ingredients_preferred에 동시에 내면
seen_terms가 강도 분류 전에 소비돼 preferred가 required 기본값으로 오승격→하드게이트
오진입. 채택 dedupe는 유지하되 **강도 슬롯은 중복 표현이어도 강도 신호를 기록**하도록
수정(setdefault 멱등). ② F2(P2) — 증거 유무를 리스트 truthiness로 판정해
`ingredient_ids=[""]`/공백이 "증거 있음"으로 오분류→no_evidence 과소집계. **정규화 후
비어있지 않은 값만** presence 판정. 게이트 재실행: pytest **1676 passed / 50
skipped**(+5), ruff/mypy 클린, 스냅샷 diff 0·byte-identity 유지.

## A5 codex 리뷰 수정 라운드 (2026-07-24, REQUEST CHANGES HIGH 5·MED 3 전부 반영)

초기 A5 하네스는 판정 규칙이 느슨해 측정 유효성 결함으로 수치가 낙관 편향(nDCG
0.883 등). codex 리뷰 8건 전부 수정 후 정직한 값으로 재생성(위 A5 완료 보고는 이미
최종본). 처리 내역:

| # | 지적 | 처리 |
|---|---|---|
| F1(H) | 판정이 질의 의도 미반영 — 이름 부분문자열/광역 group만으로 gain 부여(nDCG 부풀림) | exact_top=explicit product_ids 또는 name 토큰 **전부 AND**; relevant=질의 전 제약 AND(brand∧category_surface family∧must_contain∧profile_brand). 40문항 전수 재점검. false 1.000 계층 소멸 확인(nDCG 0.883→0.465) |
| F2(H) | Hit@1/3 분모 은폐(gain2 4/29만 채점을 전체처럼 보고) | ExactHit@k(분모 명시)로 개명 + gain≥1 **RelevantHit@k** 보조지표 신설, 계층별 n 표기 |
| F3(H) | false_zero 미발화(returned==0만 검사) | false_zero=결과 내 gain>0 0건 ∧ 카탈로그 relevant 존재. 배제/기피 6문항에 양성 relevant(category_surface) 부여 → 0.069로 의미화 |
| F4(H) | profile_ref가 프로파일 무평가 | `profile_refs`를 슬롯 P/R에 편입(gold 기대 클래스 명시) + 판정을 **유저 자신의 preferred/repurchase 브랜드 상품=gain 1**로, `profile_applied` 플래그(personalization 미발화 감지) |
| F5(M) | required_satisfaction에 preferred 혼입 | required 강도 문항만 분모. preferred는 별도 `preference_reflected`(캐리어 평균 순위<비캐리어) 신설 |
| F6(M) | gold 슬롯 P/R 동어반복(pred를 gold_interp 재직렬화) | pred=서버 해석, gold=**YAML 원문 슬롯 per-surface 해소**로 분리 → 조립 충실도 실측(brands P=0.917·categories P=0.860) |
| F7(M) | holdout<30%·typo 무보호 | 44문항 확장(typo 4·부족 계층 보강), holdout 14(31.8%)·계층별 ≥1 |
| F8(H) | 매니페스트 재현 불가 | 데이터셋·하네스·카탈로그·프로파일 **sha256** + git dirty + 커맨드라인 추가. 실프로파일 옵션화(부재 시 픽스처 degrade + `profile_source` 기록 — 클린 체크아웃 실행 가능) |

추가 판단(구현 중): 카테고리 서브타입을 리터럴 라벨이 아닌 **카탈로그 사실 family**
(에센스=세럼=앰플, 스킨=토너)로 매칭 — "레티놀 세럼" 질의가 레티놀 에센스(라벨
'에센스')를 인공 오답 처리하던 결함 제거(false_zero 0.24→0.069). 구 리포트 파일은
삭제·같은 날짜로 교체(부풀린 수치 미잔존). 게이트: pytest **1697 passed / 50
skipped**, ruff(src tests scripts)/mypy(src) 클린.
