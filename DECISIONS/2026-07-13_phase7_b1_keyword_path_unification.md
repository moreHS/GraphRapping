# Phase 7 B1 — keyword 해소 경로 통합 + 한국어 형태론 정규화

날짜: 2026-07-13 · 상위 계획: `fable_doc/plans/2026-07-13_phase7_graph_intelligence.md` §B1

## 배경

진단(`fable_doc/06_graph_ontology_assessment.md` §4): `quarantine_unknown_keyword`
2,784건(distinct 2,482), 상위가 굴절형(`촉촉하고`36 `순하고`13 `촉촉해서`12).
근본 원인은 **해소 경로 이중화** — bee_normalizer의 키워드 매처는 부분문자열
매칭이라 `촉촉`⊂`촉촉하고`를 잡는데, quarantine을 생성하는 mention_extractor
candidate 큐는 keyword_surface_map을 아예 조회하지 않아, 사전에 있는 표면형
(`무향` 등)까지 격리로 새어나갔다.

## 결정

1. **단일 해소 함수** `bee_normalizer.resolve_surface_keywords(phrase, map)`로
   통합. bee_normalizer(신호 생성)와 mention_extractor(격리 억제)가 같은 함수를
   사용 → "known"의 정의 일치. `_extract_keywords`/`get_unknown_surfaces`도 위임.
2. **보수적 어미 접기** `src/normalize/korean_morph.py` — 화이트리스트 어미
   스트리핑 + `해→하` 축약. **접기는 스템이 사전에 있을 때만 사용**(정밀도 보존),
   **부정 문맥에서는 접기 스킵**(polarity flip 방지). kiwipiepy 미도입(계획 확정).
   ㅂ불규칙(`부드러워요`→`부드럽`)은 접기 실패=재현율 저하로 수용(오접힘보다 안전).
3. **누락 스킨케어 속성 스템 등재**(keyword_surface_map.yaml): 순함/발림성/자극없음/
   부드러움/시원함/세정력/지속력/가성비/진정/흡수/산뜻/깔끔/수분감/탄력/향좋음/거품
   등 ~20 concept. 커머스·행동 노이즈(재구매/배송/가격/포장)와 부정어휘화 개념
   (non-sticky)은 **의도적으로 미등재** — 신호 오염/극성 혼선 방지.

## 실측 효과 (mockdata 906, in-process)

B1 단독 기여(동시 C1 변경분을 git stash로 분리 측정한 clean isolation):
- kg_on `unknown_keyword`: **2,784 → 2,088 (−25.0%, −696)** — 전량 B1
  (C1-only 상태에서도 2,784로 불변 → 키워드 해소는 C1 무관)
- kg_off `unknown_keyword`: 2,477 → 2,020 (−18.4%)
- `BEE_KEYWORD` 신호: **238 → 802 (+564)** — 전량 B1. kg_on signal_count
  증가분(+564)과 정확히 일치
- `top_keyword_ids` 상품 5 → 7

주의(병렬 실행): `test_corpus_promotion_baseline`의 signal_count 베이스라인
(kg_on 3,340)은 동시 C1 변경분(+9 신호: COMPARISON/CONCERN 등)을 포함한
**결합 상태** 측정값이다. `unknown_keyword`(2,088/2,020)는 C1과 무관하게 안정.
P7-2 최종 병합 시 C1 최종 수치가 다르면 signal_count 베이스라인 재측정 필요.
- 잔여(76%)는 진성 open-vocab(신조어 `정착템`, 타도메인 `맛도 괜찮아요`,
  부정어휘 `끈적임없이`) — 계획상 B3(임베딩) 영역. 정밀도 보존 등재로 50%는
  비현실(재구매/배송/가격을 키워드로 등재해야 도달 → 신호 오염).

## 회귀

- 랭킹 스냅샷 dense_golden / wide_golden: **무변경**(신규 keyword 신호가 골든
  프로필 top-N 순위를 이동시키지 않음 — weak-evidence 계층).
- 기대셋(`test_expected_evidence_family_baseline`): green(계약 불변).
- `test_corpus_promotion_baseline`: 베이스라인 수치를 의도 변경으로 갱신
  (signal/quarantine/top_keyword_ids). 본 문서가 갱신 근거.
- 부정 문맥 전용 테스트: `tests/test_korean_morph.py`.
