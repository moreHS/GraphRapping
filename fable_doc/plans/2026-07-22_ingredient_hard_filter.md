# 성분 질의 하드필터 — 관용어 별칭 해석 + 함유 필터 + 상품명 폴백 (v3)

- 날짜: 2026-07-22 (v2 — codex 계획 리뷰 반영 / v3 — /api/search 통일 합의 반영)
- 상태: 계획 (최종 검토 후 구현 — 사용자 사전 승인: "이슈 없다면 구현 진행")
- 사용자 합의(2026-07-22): **/api/search는 익명 /api/ask 파이프라인으로 내부
  통일(2번안)** — 라우트 유지, 구현 공유. 질의 진입점의 의미론 분화를 구조적으로
  제거.
- 트리거: 사용자 실테스트 — 질의 "히알루론 든거 뭐 좋은거 없나"에 히알루론
  함유/공유 근거가 전혀 없는 추천이 반환됨.

## 1. 현상 트레이스 (실측)

동일 질의 API 재현 결과:

- LLM 해석: 카테고리 `스킨/스킨케어` + goal `보습` + keyword `보습좋음`으로
  의미 확장, **`히알루론`은 unresolved_terms로 낙하**(미해석 칩 표시).
- 랭킹: 리뷰 근거(keyword/goal) + 프로파일 부스트(brand/repurchase_*/
  active_category)만 — top5 근거 family에 성분 축 없음.
- 결론: "보습 잘하는 스킨케어 + 유저 취향" 순위. 히알루론 함유 여부 미반영.

## 2. 현재 인프라 실측

| 레이어 | 상태 |
|---|---|
| LLM 추출 슬롯 | `ingredients_wanted` **이미 존재** (query_understanding.py 프롬프트 스키마) |
| 검증 게이트 | resolve_query_concepts ingredient 축 = **INCI 접미가 질의에 통째로 포함**될 때만 매칭 (search.py:250) — **관용어 별칭 레이어 없음 ← 끊긴 지점 ①** |
| 추천 주입 | resolved ingredient → `PREFERS_INGREDIENT` request-scoped **소프트 부스트 이미 존재** (server.py `_QUERY_INJECT_EDGE_TYPE`) — **직접 언급 시 하드필터 없음 ← 끊긴 지점 ②** |
| 기피 성분 | `ingredients_avoided` → AVOIDS_INGREDIENT **하드필터 이미 존재** (비대칭: 회피만 하드) |
| 상품명 폴백 | **없음 ← 끊긴 지점 ③** |

데이터 실측 (v2에서 서빙 표면 기준으로 정정):

- 성분 사전 = MAIN_INGREDIENT 유니크 310 토큰(= 그래프 ingredient 노드 310 출처).
- 히알루론 계열 INCI 4종: 소듐하이알루로네이트 / 하이알루로닉애씨드 /
  하이드롤라이즈드하이알루로닉애씨드 / 소듐하이알루로네이트크로스폴리머.
- 성분 기준 함유 68행(유니크 ~65상품).
- **이름 표면 정정**: 서빙 프로파일에는 SKU명이 없고
  `representative_product_name`만 있음(serving_profile_schema). 대표상품명
  기준 '히알루론' 표기 = **4상품**(그린티히알루론산 로션/스킨/수분선세럼,
  트루히알루론수분자차선크림). SKU명에만 있는 4상품(더그린티씨드크림/세럼,
  그린티밸런싱세트, 해피바스 모이스트딥클렌징폼)은 v1 이름폴백으로 **미커버**
  — v1의 "16개"는 SKU 행 기준 과대집계였음. SKU명 서빙 계약 추가는 후속 옵션.
- MAIN_INGREDIENT 채움률 203/517 → 이름 폴백의 존재 이유는 유지(4상품 추가 커버).
- 유사상품 공유축에는 ingredient 이미 포함 — 상품↔상품 경로는 살아있고
  질의→상품 경로만 끊김.

외부 자원 (recommend-agent, 사용자 지정):

- `INGREDIENT_DICT`: 관용어→INCI 188 엔트리. 우리 카탈로그와 교집합:
  INCI 88종 / **관용어 85개가 닿음**. 히알루론산 엔트리는 INCI 2종만 —
  소듐하이알루로네이트(크로스폴리머) 누락 → 보강 필요.
- `PRODUCT_NAME_TO_INGREDIENT_MAP` 44 엔트리(오타 '히아루론산' 포함) —
  이름 표면 시드로 참고.

## 3. 사용자 확정 설계 방향 (2026-07-22)

1. 원하는-성분 해석 활성화 (슬롯은 있으니 별칭/게이트가 과제).
2. 별칭 사전은 recommend-agent `INGREDIENT_DICT` 기반 + 부족분 보강.
3. **질의에서 직접 언급된 성분 = 하드필터**. ("들어있으면 더 좋고"류 소프트
   요청은 엣지케이스로 스코프 제외.) 상품명 폴백 포함, 결과 부족 시 완화(c안).

## 4. 구현 설계 (v2)

### 핵심 모델: `IngredientConstraint` (codex 반영)

`understand_query`가 부정 차감 **후** 성분군 단위로 생성, interpretation에 동봉:

```python
@dataclass
class IngredientConstraint:
    label: str                    # 관용어 (사용자 언어, 칩/문구용)
    inci_concept_ids: list[str]   # 카탈로그 실존 INCI concept id들 (같은 성분군 변형)
    name_surfaces: list[str]      # 이름 폴백 표면 (관용어 + 오타 변형)
    provenance: str               # "raw" | "llm" (직접 언급 판별 근거)
```

- **의미론**: 같은 성분군 내 INCI 변형·이름 표면 = OR / 서로 다른 성분군
  constraint 간 = AND / structured ∪ name = OR.
- **provenance와 하드필터 자격(codex 2차 리뷰 반영)**: `raw` = 원문
  정규화 문자열에 별칭 surface 또는 INCI 표면이 **실재**(부정 스팬 밖) —
  **하드필터 대상**. `llm` = LLM이 채택했으나 원문 표면 부재(기존 recall
  확장 — 기존 테스트가 보장하는 동작) — **소프트만**(PREFERS_INGREDIENT
  부스트, 하드게이트 제외). 사용자 규칙 "직접 언급만 하드"의 집행.
  회귀 테스트: LLM-only 성분 → ingredient_filter 미적용 + 기존 확장 테스트
  그린 유지.
- 회피∧원함 동시 언급 시 기피 우선: 기존 avoided-set 차감이 constraint 생성
  **전에** 적용 (기존 로직 재사용, 테스트 고정).

### 단일 순수 matcher (codex 반영)

```python
def match_ingredient_constraint(product, constraint) -> str | None:
    # "ingredient" | "name" | None. 순수 함수 — 공유 dict 무변조(request-scoped).
```

- structured: `ingredient_concept_ids ∪ ingredient_ids` ∩ inci_concept_ids
  (AVOIDS 판정 미러).
- name: normalize_text(representative_product_name)에 surface 포함, 단
  **부정 접미 가드**(surface 직후 프리/free — "레티놀프리" 상품명 오탐 방지,
  기존 _NEGATION_FREE_RE 관례 공유).
- 재사용처 4곳: 로그인 /api/ask 하드게이트 · 익명 /api/ask(search_products에
  constraints **명시 전달** — 신규 파라미터) · relax 계산 · related_products 필터.

### B1. 별칭 사전 + 해석 게이트 확장

- 신규 **`configs/ingredient_alias_map.yaml`** (config_loader 기준 경로 —
  codex 경로 정정 반영): `관용어: [INCI 토큰...]`.
  - 시드: INGREDIENT_DICT 188 중 카탈로그에 닿는 85 엔트리(출처 주석).
  - 보강: 카탈로그 310 토큰 스템 스캔으로 누락 INCI 추가(히알루론산 4종 완성),
    오타/축약 변형(히아루론산, 히알루론) 별칭 추가.
  - 자동 접두/접미 스트리핑 없음 — 명시 사전만.
- `resolve_query_concepts` ingredient 축에 별칭 레이어 추가:
  - surface in query → 매핑 INCI 중 **현재 카탈로그 실존만** 채택(기존 원칙).
  - **부정 스팬 인지(codex 역전 방지)**: 기존 부정 정규식(없는/없이/빼고/
    제외/프리/free)을 공유 헬퍼로 추출, 부정 스팬에 걸린 surface는 양성
    채택하지 않음 → `/api/search`("레티놀 없는 크림")에서 별칭이 레티놀
    상품을 **끌어올리는 역전 차단** (해석 레벨 방어라 모든 호출자 안전).
  - matched_text/label=관용어, concept_id=INCI.
- LLM 경로: `ingredients_wanted`도 같은 게이트 통과(자동 수혜 확인 테스트).
- **unresolved 정리(codex)**: 별칭으로 해소된 surface는 unresolved_terms에서
  제거(칩 모순 방지).

### B2. 하드필터 배선 (4경로) + 완화 + 자격

- **로그인 /api/ask 순서 고정(codex)**: category gate → **ingredient hard
  gate**(constraint AND) → 나머지 soft narrowing. `_narrow_candidate_universe`
  의 OR-축소에서 ingredient 개념 **제외**(하드게이트와 이중 계산 방지).
- **익명 /api/ask**: interp의 constraints를 `search_products`에 새 파라미터로
  전달(현재는 원문 재해석만 — 미전달 확정 실측). search_products가 게이트 적용.
- **/api/search 내부 통일(사용자 합의 2번안)**: 익명 ask 흐름을 공유 헬퍼로
  추출(`understand_query → constraints → search_products(+constraints) →
  related(동일 matcher 필터)`)하고 `/api/ask` 익명 분기와 `/api/search`
  GET/POST가 **같은 헬퍼를 호출**. 응답도 ask 익명 payload로 통일하되 기존
  no-concept 안내 `message` 규칙은 유지. 이 엔드포인트도 질의 이해를 타게 됨
  (LLM 미설정 시 사전 폴백 → 테스트 결정성 유지). 기존 /api/search 테스트는
  새 payload 기준으로 갱신 + **ask 익명과의 응답 동등성 테스트** 추가.
  Phase 4.2 원형 경로의 별도 유지보수 종료.
  - **입력 계약(codex 2차 리뷰 반영)**: 라우트별 검증/기본값은 **라우트에
    잔류**, 공유 헬퍼는 검증 완료된 (query, top_k)만 받음. /api/search는
    빈/공백 질의 200+no-concept 안내(기존 계약·테스트 유지), top_k 기본
    20/clamp [1,200] 유지, **500자 초과만 400으로 ask와 정렬**(신규 —
    understand_query 내부 가드와 일치). 동등성 테스트는 동일 명시 top_k +
    비어있지 않은 질의로 실행, search 전용 빈 질의 계약은 별도 테스트 유지.
- **related_products(codex 우회 지적)**: constraint 활성 시 동일 matcher로
  필터 — 1차 결과 아래 비함유 재노출 방지.
- **이름-only 자격/랭킹(codex)**: 새 overlap 축 `product_name:<surface>` —
  `build_candidate_eligibility`에서 **PRODUCT_MASTER_TRUTH**로 분류(상품명은
  마스터 원천). search의 "오버랩 ≥1" 자격과 recommend evidence gate를
  이름-only 상품도 통과. 계약 문서(§evidence family 매핑)에 축 추가 명기.
- **relax 확정(codex)**:
  - 계산 유니버스 = category gate 내부, 시점 = ingredient gate 직후.
  - 0건이면 **ingredient 조건만 해제**(category/avoided 유지), 1건 이상이면 유지.
  - 응답 `ingredient_filter: {applied, labels, matched_products(게이트 직후
    유니버스 내 매칭 수), relaxed, reason}` 신설, 기존 top-level `relaxed`는
    (soft-narrow relax ∨ ingredient relax)로 유지 — reason은 분리 기록.
- PREFERS_INGREDIENT 소프트 부스트 유지(게이트 통과분 내부 순위 기여).

### B3. 표면화 (프론트)

- resolved 칩: ingredient 개념 자동 표시(라벨=관용어).
- 요약줄: "성분 필터: 히알루론산 — 후보 N개" + relaxed 시 완화 안내 문구.
- 캐시버스터 범프.

### 알려진 한계 (명시적 스코프 아웃)

- **avoided 이름폴백 비대칭**: 기존 AVOIDS는 structured만 검사 — 이름에만
  성분이 있는 상품은 기피 질의를 통과(기존 동작). 이번에 확장하지 않음
  (기존 랭킹 변경 회피). 후속 B4 후보로 기록.
- SKU명 서빙 계약 추가(위 4상품 커버) — 후속 옵션.
- "들어있으면 더 좋고" 소프트 의도 분류 — 후속.
- INGREDIENT_DICT의 카탈로그 밖 103 엔트리 이식 — 45k 전환 때 재평가.
- 기존 6축 해석/스코어링 가중 변경 없음, avoided 로직 변경 없음.
- (v3에서 해소) ~~/api/search 하드필터 미적용~~ → 익명 ask 파이프라인으로
  내부 통일(사용자 합의).

### 검증 게이트

- 단위: 별칭 해석(히알루론→4 INCI)·카탈로그-실존 필터·부정 스팬 비채택
  ("레티놀 없는"→양성 0)·이름 폴백(대표명 4상품)·이름 부정 접미 가드·
  constraint AND/OR 의미론·0건 relax(ingredient만 해제)·기피 우선·
  LLM 경로 채택·unresolved 정리.
- 통합 e2e: "히알루론 든거 뭐 좋은거 없나" — 결과 전원 matcher 통과 +
  ingredient_filter 메타 + 칩. 익명 동일 질의(search 경로). related_products
  전원 통과.
- **byte-identity(codex)**: 기존 무질의 /api/recommend 동일성 테스트 유지 +
  **성분 질의 직후 무질의 재호출 byte-identity**(request-scoped 무변조 증명).
- **스냅샷 diff 0**: 무질의 추천(G4 랭킹 스냅샷·golden) diff 실측 — 0이 아니면
  중단·보고(스냅샷 재승인 절차).
- ruff/mypy/pytest 전체, 브라우저 실측(질의 실행 + 칩/문구 캡처).

## 5. codex 계획 리뷰 처리 내역 (v1→v2)

| # | 지적 | 처리 |
|---|---|---|
| 1 | 질의 3경로(로그인/익명 ask, /api/search) 불일치 — 익명 ask는 interp 미전달, /api/search는 부정 미처리 역전 위험 | constraints 명시 전달(익명 ask) + 부정-세이프 별칭(해석 레벨) + **v3: /api/search를 익명 ask 파이프라인으로 내부 통일(사용자 합의)** — 경로 분화 자체 제거 |
| 2 | 서빙 표면에 SKU명 없음 — "16개" 과대집계 | 대표명 기준 4상품으로 정정, SKU 4상품 미커버 명시 + 후속 옵션 |
| 3 | 이름-only 상품이 overlap 0/evidence gate 탈락 | `product_name` overlap 축 신설(PRODUCT_MASTER_TRUTH 분류) |
| 4 | 성분군 OR/AND 의미론 부재, 평면 MatchedConcept 한계 | IngredientConstraint 모델(성분군 단위) + 명시 의미론 |
| 5 | relax 의미 미정 | 유니버스/시점/해제 범위/reason 분리/메타 스키마 확정 |
| 6 | related_products 우회, avoided 비대칭 | related 동일 matcher 필터 / avoided 비대칭은 명시적 스코프 아웃(B4 후보) |
| - | configs/ 경로, unresolved 중복, byte-identity 확장 | 전부 반영 |

## 6. 완료 보고 (2026-07-23, Opus 구현 3배치·Fable 배치별 검수·codex 리뷰)

**구현 규모**: 13파일 +1,200/−122 (신규: configs/ingredient_alias_map.yaml
87엔트리 · src/rec/negation.py · src/rec/ingredient_constraint.py ·
tests/test_ingredient_alias.py · tests/test_ingredient_constraint.py).

**B1 (별칭·게이트)**: 시드 85(INGREDIENT_DICT∩카탈로그, 계획 추정 정확 일치)
+히알루론산 4 INCI 완성+오타 키 2. 별칭 레이어(부정 스팬 가드·카탈로그
게이트)·negation 공유 모듈(정규식 verbatim 이관)·unresolved 모순 제거.
부수 개선: "히알루론 없는" 기피 질의도 별칭 경유 AVOIDS 매핑(기존 미해석 경고).

**B2 (하드필터 배선)**: IngredientConstraint(성분군 단위, provenance raw=하드/
llm=소프트)+순수 matcher(구조화∪대표상품명, free-of 접미 가드). 로그인
ask(카테고리→성분 하드게이트→소프트 축소, _narrow에서 ingredient 제외)·익명
ask/·/api/search 공유 헬퍼 통일(입력 계약 라우트 잔류: 빈질의 200+안내,
top_k 20, 501자만 400)·related require_ids·relax(성분 조건만 해제,
ingredient_filter 메타{applied,labels,matched_products,relaxed,reason},
top-level relaxed OR)·product_name 근거 축(PRODUCT_MASTER_TRUTH, 계약 문서
갱신). 구현 중 발견·수정: 필터 라벨은 사용자 타이핑 표기 유지(성분군 병합 시
오표기 버그), 모델은 순환 import 회피로 신규 모듈.

**B3 (표면화)**: renderAskInterpretation에 성분 필터 줄(applied: 🧪 라벨—함유
N개 / relaxed: ⚠️ 서버 reason)+해석 칩 (type,label) dedupe(히알루론×4→1,
스킨케어×2→1). 캐시버스터 2회 범프(최종 v=20260723-ingredient-filter2).
에이전트가 자체 적발한 NUL 바이트 오삽입 →   이스케이프로 교정.

**게이트 결과**:
- pytest **1525 passed / 50 skipped**(+31, Fable 재실행), ruff/mypy 클린.
- 무질의 랭킹 스냅샷(dense/wide golden) **diff 0** — 재승인 절차 미발동.
- **byte-identity**: 성분 질의 직후 무질의 /api/recommend 응답 byte-identical
  (request-scoped 무변조 증명, 신규 테스트).
- 실데이터 e2e(8123 재기동, LLM azure·실프로파일 100): "히알루론 든거 뭐
  좋은거 없나" → constraint(raw, INCI 4종+표면 3종), **함유 53개 하드필터**,
  결과 10개 전원 성분 근거, 미해석은 '든거'만, 관련상품도 함유만. #1=이니스프리
  그린티히알루론산수분선세럼(설명 경로: 질의에서 언급→소듐하이알루로네이트→
  HAS_INGREDIENT). 캡처 `성분필터_히알루론_확인.png`.
- **codex 통합 구현 리뷰(B1+B2+B3 전체 diff): REQUEST CHANGES(P1 7·P2 3)
  → 9건 수정·1건 기각 → 재게이트 통과**. 수정 라운드(F1~F9):
  ① 중첩 별칭 최장 일치(비타민⊂비타민A 등 7쌍 — DIFF-INCI 4쌍이 AND 오생성)
  ② matcher raw 도메인 정규화(concept IRI↔raw 문자열 suffix 조인 — 테스트가
  IRI를 raw 필드에 넣어 가리던 것 교정, 실측 함유 65→74 확대)
  ③ 직접 INCI 입력 시 name_surfaces에 타이핑 표면+INCI suffix 포함
  ④ 익명 경로 성분 게이트를 카테고리 유니버스 내로(립스틱 혼입 차단)
  ⑤ relax 판정을 기피-차감 후로(전원 기피 시 applied=true·결과0 모순 제거)
  ⑥ PG 통합 테스트 통일 payload로 갱신 ⑦ bare INCI 축에도 부정 가드
  ⑧ byte-identity를 response.content bytes 비교로 강화
  ⑨ free-of 접미가 '프리미엄'을 오인하던 정규식 경계 수정.
  **기각 1건**: GET /api/search LLM rate-limit/인증 — 루프백 전용 데모 과설계
  (백필 인증 기각과 동일 기준), 서비스화 전환 시 재평가 기록.
- 수정 라운드 후 최종: pytest **1534 passed / 50 skipped**(+9/갱신 3),
  ruff/mypy 클린, 스냅샷 diff 0·byte-identity(강화판)·동등성 그린 유지.
  재기동 실검증: 비타민A→레티놀 성분군만, "히알루론 수분크림"(익명)
  →skincare 유니버스 내 53개, /api/search "히알루론 든거"→전체 74개.

**후속(스코프 아웃 유지)**: avoided 이름폴백 비대칭(B4 후보) · SKU명 서빙
계약(이름-only 4상품) · "들어있으면 더 좋고" 소프트 의도 · 카탈로그 밖 별칭
103 엔트리(45k 전환 시).

### 갭 수정 라운드 — "알콜업는 스킨케어" (2026-07-23, 사용자 실테스트 발견)

**진단(실측)**: LLM은 파악(알콜→'알코올' 정규화)했으나 별칭 사전에 알코올
엔트리 부재 — 원본 INGREDIENT_DICT의 `알코올→에탄올`이 카탈로그 표기
('변성알코올')와 어긋나 시드 교집합 필터에서 드롭된 보강 누락. 오타 '업는'은
부정 정규식 미매치라 폴백 경로에서 양성 반전 위험도 잠복.

**수정 3건**: ① 별칭 보강 `알콜/알코올/에탄올→변성알코올`(87→90키) —
지방 알코올 7종(세틸/스테아릴 등)은 유화·보습 성분이라 의도적 제외(과차단
방지, 원본 사전과 동일 취지 주석화) ② negation 마커에 '업는'(없는의 빈출
오타) 추가 — 마커는 기피 후보만 생성하고 사전 게이트를 통과해야 실기피라
오탐 비용 ~0 ③ 기피로 해소된 부정 표면('알콜'⊂'알콜업는')을 포함하는
unresolved 블롭 제거 — 기피 반영됐는데 "사전에 없는 표현" 칩이 뜨는 모순
해소(미해소 부정 '제라늄업는'은 정직 잔존 — 과삭제 방지 테스트 고정).

**게이트**: pytest **1541 passed / 50 skipped**(+7), ruff/mypy 클린, 스냅샷
diff 0. 라이브(8123, LLM azure): "알콜없는/알콜업는 스킨케어" 둘 다
avoided=[변성알코올]·미해석 칩 0·결과 10개 중 변성알코올 보유 0(원천 40행
대조).
