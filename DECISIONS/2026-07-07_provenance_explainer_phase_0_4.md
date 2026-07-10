# Phase 0.4 — Provenance explainer 완성

작성일: 2026-07-07 · 상태: 구현 완료

## 배경

`ProvenanceExplanationPath`(snippets/fact_ids/review_ids)와 `ExplanationService.explain_with_provenance`,
그리고 low-level `ProvenanceProvider` Protocol(3 async 메서드) + `DBProvenanceProvider`(async DB 구현)까지
이미 존재. 빠진 것은 (a) in-memory provider, (b) 데모 파이프라인 산출물 → provider 구성/보관,
(c) `/api/recommend` 응답에 per-path 스니펫 노출. 그리고 기존 `explain_with_provenance`가
모든 signal_id를 모든 path에 무차별 부착 → provenance 정합성(엉뚱한 리뷰) 위험.

## 데이터 가용성 trace 결과 (실측, 추측 아님)

데모 파이프라인(`load_demo_data` → `run_batch`) 실행 후:

- `batch_result["all_bundles"]`: `ReviewPersistBundle` 목록. 각 bundle에:
  - `canonical_facts`: `CanonicalFact` (각 `.provenance`는 `FactProvenance` — snippet/start_offset/
    end_offset/review_id 보유)
  - `signal_evidence_rows`: `{signal_id, fact_id, evidence_rank, contribution}` (signal→fact 정본)
  - `review_raw["review_text"]`: 원문 텍스트 (200/200 bundle 모두 존재)
- `demo_state.product_signals[pid]`: signal dict 목록 (signal_id / review_id / dst_id /
  bee_attr_id / keyword_id / signal_family 보유) — concept↔signal 역인덱스에 사용
- **중요 실측**: KG-on(데모 기본)에서 `FactProvenance.snippet`은 대부분 빈 문자열(""), offset은 None.
  859개 provenance 중 45개(BEE_KEYWORD phrase_text)만 snippet 보유. 따라서 snippet이 비면
  **review_text로 fallback**(앞부분 truncate)해야 함 → 기존 chain의 review_snippet fallback과 일치.
- explanation path의 `concept_id`는 실데이터에서 full IRI(`concept:Brand:이니스프리`,
  `concept:Keyword:로션`), 단위테스트에선 raw id(`kw_thin_spread`). → join은 양측 IRI prefix를
  벗겨 정규화(`_join_key` 방식)로 매칭.
  - **⚠️ 정정(2026-07-08)**: 위 "prefix 벗겨 정규화" 서술은 **bare-id/leading-IRI path에만** 성립.
    `semantic_*`/`weak_semantic_*` path의 `concept_id`는 `axis:value:<IRI>` 형식
    (예 `moisture:moist:concept:Keyword:kw_moist`)이라 IRI가 **선두가 아니라 내포**되어 있어,
    `normalize_signal_id`의 선두 prefix-strip만으로는 `axis:value` 머리가 남아 signal anchor
    (`kw_moist`)와 **절대 일치하지 못했다**. 결과적으로 semantic path는 provenance 스니펫이
    한 건도 부착되지 않던 결함. dense_golden 실측: semantic path 64건 중 매칭 0건.
  - **수정(Round A)**: `provenance_provider._concept_path_match_key`가 `concept:`/`product:`가
    내포(위치>0)되면 마지막 등장 지점부터 잘라 뒤쪽 IRI를 회수한 뒤 normalize하도록 하고,
    `signal_ids_by_concept_path`가 이 헬퍼로 매칭 키를 만든다. bare-id/leading-IRI(위치0) path는
    기존 동작 그대로(무회귀). dense_golden 재측정: semantic path 64/64 매칭. explanation path의
    `concept_id` 포맷 자체는 불변(랭킹 스냅샷/golden fixture 의존이라 유지) — 수정은 매칭 키 회수에 국한.

## 검토한 선택지

1. **새 독립 인터페이스**(path 리스트 받아 반환) — 기존 Protocol/`ExplanationService`/`DBProvenanceProvider`와
   중복. async DB 구현이 이미 그 Protocol을 따름. 재발명 = 계약 파편화.
2. **기존 low-level Protocol 재사용 + in-memory 구현 추가** (채택) — `InMemoryProvenanceProvider`가
   `DBProvenanceProvider`와 동일 3-메서드 async Protocol을 만족 → `ExplanationService`에 drop-in.
   async 시그니처 유지로 "나중에 async DB 구현" 요건도 이미 충족.

## 결정

- `InMemoryProvenanceProvider` (신규, `src/rec/provenance_provider.py`): 기존 `ProvenanceProvider`
  Protocol의 3개 async 메서드 구현. `all_bundles`에서 signal_evidence / fact_provenance /
  review_text 인덱스를 미리 구성. `get_review_snippet`은 review_text 반환(offset 있으면 substring).
- concept→signal 매핑 정합성: path.concept_id ↔ 그 product의 signal(dst_id/keyword_id/bee_attr_id)를
  정규화 매칭해 **path별 signal_id 목록**을 산출하는 `signal_ids_by_concept_path(...)` 헬퍼를
  같은 모듈에 둔다. 이렇게 얻은 매핑을 `ExplanationService`에 넘겨 path별로 자기 signal의 근거만 부착.
- `ExplanationService.explain_with_provenance`에 optional `signal_ids_by_concept` 인자 추가(additive).
  주어지면 path별 매핑 사용, 없으면 기존 동작(전체 signal_ids) 그대로 → 하위호환 유지.
  path당 스니펫 최대 2개, 120자 truncate, review_id/fact_id 포함.
- 데모: `load_demo_data`가 `all_bundles`로 `InMemoryProvenanceProvider`를 구성해 `demo_state`에 보관.
  `/api/recommend`는 provider가 있으면 `explanation_paths` 각 항목에 `snippets`(review_id+text)를
  additive로 추가. 스키마 제거/변경 없음.

## 트레이드오프

- `demo_state`가 `all_bundles`를 유지하므로 메모리 사용 증가. 데모/fixture 규모(수백~수천 리뷰)에서 허용 가능.
- snippet이 review 전체 텍스트 truncate라 정밀 span 하이라이트는 불가(offset 부재). 향후 NER offset이
  provenance에 채워지면 자동으로 substring 경로가 활성화됨(코드 변경 불필요).
