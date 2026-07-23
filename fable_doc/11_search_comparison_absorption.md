# 하이브리드 상품검색 3자 비교 및 흡수 결정 기록

- 날짜: 2026-07-23
- 비교 대상:
  - **[POC]** AI검색_poc 설계 문서 `AI검색_poc/docs/superpowers/specs/2026-07-23-hybrid-product-retrieval-dual-es-design.md` (Dual ES 실험 설계 — 타깃 ES)
  - **[PDA]** 기존 서치툴 `agent-aibc/product-discovery-agent` (planner LLM→ToolParams→InquiryExecutor→ES)
  - **[GR]** GraphRapping 질의 파이프라인 (understand_query→검증 게이트→IngredientConstraint→하드게이트+relax→evidence-first 랭킹, 타깃=서빙 스토어/DB)
- 목적: POC 계획·PDA 실코드와의 차이 확인, 흡수 가능한 기능 선별 (사용자 지시)

## 1. 구조 비교 요약

| 축 | POC 설계 | PDA (실코드 실측) | GR (현재) |
|---|---|---|---|
| 추출 | 사전 API + Gemma V1-G | planner LLM(gpt-5.1) 20 few-shot + 키워드 힌트 → ToolParams ~20필드 | LLM 슬롯 11종 + 사전 폴백 |
| 중간 계약 | SearchIntent/Constraint — **polarity×strength×provenance×span 전 축 공통**, compilation_trace | 평면 ToolParams(provenance 없음) | QueryInterpretation + IngredientConstraint(**성분만**; provenance raw/llm은 GR이 더 정밀 — 원문 표면 실재 기준) |
| 검증 | provenance 우선순위 원칙 선언 | 부분(BRAND_LIST 등) | **카탈로그-실존 게이트 + 3티어 성분 정규화**(별칭/INCI/역방향) — GR 강점 |
| 부정 | 전 축 exclude 1급 | 성분·브랜드·카테고리 exclude(must_not) | **성분만** exclude |
| 완화 | required 절대 미완화, 증거면만 확장(N0→N2) | CTGR 확장→시맨틱 RRF 폴백 | 성분 조건만 완화+투명 표시(철학 동일) |
| 랭킹 | 결정적 7단, 랜덤 금지 | ES 스코어+정렬 스크립트, **`secrets.choice` 랜덤 정렬 실존**(query_builder.py:1079) | evidence-first 결정적 — GR 강점 |
| 평가 | **gold-vs-모델 페어 손실 분해, holdout 동결, nDCG/Hit@k, corpus_mismatch 분리** | 없음 | 회귀·결정성 게이트만, 품질 메트릭 없음 |
| 유저 프로파일 | 비목표 | recommend만(외부 personal-agent, inquiry 미사용) | **핵심 결합**(profile_refs 7클래스, repurchase 부스트) — GR 강점 |

## 2. PDA 실코드 주요 실측 (Explore 에이전트, 파일:라인 근거)

- 분기: `product_name` 있으면 product_search 라우트, 없으면 RecommendQueryBuilder 공유 라우트 (inquiry.py:65).
- **결함(반면교사①)**: product_name 라우트는 ingredient/exclude_ingredient/skin_*/product_attribute/sale_price/extra_keyword/sort를 **전부 무시**(product_search.py:117-179에 해당 절 부재) — "윤조에센스인데 향료 없는 거"의 부정이 조용히 소실. GR이 codex 리뷰에서 잡아 통일했던 "경로별 의미론 분화"의 대형 사례.
- **결함(반면교사②)**: 인기순 정렬이 `secrets.choice(["order_count","click_score"])`로 매 호출 무작위 — 재현 불가(POC 문서의 "randomized sort" 실위치).
- 후보 cap 10(inquiry.py:64), dedup·가드 후 재현율 급감 위험.
- 강점: 5단계 상품명 부스팅(exact→phrase→동의어→ngram→cross_fields), **전성분 nested 검색**(MAIN_INGREDIENT + noti_info_all_ingredient…), 성분 동의어 확장(INGREDIENT_DICT — GR이 B1에서 이미 흡수), 판매상태 가드 단일 원천, 다단 폴백.

## 3. GR 갭 실증 (이번 비교로 발견)

**상품명 축 부재**: 검증 게이트 6축(concern/goal/keyword/brand/category/ingredient)에 product 축이 없어 LLM `product_names` 슬롯이 해소 불가. 라이브 실측 — "설화수 윤조에센스 어때" → 윤조에센스가 카탈로그 실존(2종: 윤조에센스/미스트)인데 brand+category로만 해석, top1=설화수 맨본윤에센스. 즉 "설화수 에센스 아무거나"와 동일 동작.

## 4. 흡수 결정 (상세는 plans/2026-07-23_search_absorption.md)

| # | 항목 | 출처 | 판정 |
|---|---|---|---|
| A1 | 상품명 축 + 식별자 직행(수락 가드 포함) | POC K-트랙 §7 | **채택** — 갭 실증, 소형 |
| A2 | 부정(polarity)의 전 축 일반화 — 브랜드/카테고리 exclude | POC §6.2 / PDA exclude_* | **채택** — 중형 |
| A3 | strength(required/preferred) — "들어있으면 더 좋고" | POC §6.2 | **채택** — 기존 스코프아웃 해소 |
| A4 | required_evidence_unknown 투명화 | POC §14 | **채택(경량)** — 정책은 배제 유지, 표시만 |
| A5 | 평가 인프라(gold-vs-LLM 손실 분해, nDCG/Hit@k, holdout) | POC §12 | **채택(트리거형)** — 0.5 라벨/평가 트랙 재개 시 |
| - | 전성분 nested 검색 / SearchResult 정규화·매니페스트 / 다중 타깃 그룹 | POC §6.3·§10·§11, PDA | **45k ES 전환 체크리스트에 기록**(즉시 구현 안 함) |

## 5. 흡수 불필요 (이미 보유 또는 GR 우위)

- 미해석 보존 원칙(unsupported trace) = GR 미해석 칩(F2)으로 기구현.
- provenance 기반 하드필터 자격 — GR이 이미 구현(원문 표면 기준, POC보다 판정 기준 구체적).
- 성분 동의어 사전 — B1에서 INGREDIENT_DICT 흡수 완료(+카탈로그 정합 보강).
- required 미완화 철학 — GR relax는 성분 조건만·투명 표시로 동일 취지 기구현.
- 결정적 랭킹 — GR 기본 원칙(PDA 랜덤 정렬은 반면교사).
- 유저 프로파일 결합 — GR 고유 강점(POC 비목표, PDA는 inquiry 미사용).

## 6. 45k ES 전환 체크리스트 추가분 (fable_doc/10 서비스화 갭 보충)

1. 성분 검색을 전성분(nested) 필드까지 확장 — PDA query_builder의 noti_info_* nested 절 참조.
2. 백엔드 교체 실험 시 SearchResult 정규화 계약 + 실행 매니페스트(인덱스 식별·Git SHA·visibility 정책 버전) — POC §10·§11 차용.
3. 다중 상품 타깃 그룹 분리(§6.3) — 비교 질의를 단일 AND로 뭉개지 않기.
4. Tier 3 역방향 스캔 suffix 인덱스(기존 기록 재확인).
