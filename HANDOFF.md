# HANDOFF — vNext 수정 완료 상태

## 완료 항목

### P0: 핵심 정합성
- [x] P0-1: Corpus promotion serving 강제 (promoted_only=True default)
- [x] P0-2: projection_registry.csv malformed rows 수정 + strict validation
- [x] P0-3: signal_evidence 정본화 (evidence_sample → signal_id)

### P1: 구조 보강
- [x] P1-1: Evidence/serving graph 경계 정리 (ARCHITECTURE.md + shadow 함수 추출)
- [x] P1-2: User aggregation weighting 강화 (recency × frequency × source_type)
- [x] P1-3: SQL-first candidate prefilter (sql_prefilter_candidates + generate_candidates_prefiltered)
- [x] P1-4: concept_id 용어 통일 (주석에서 "concept IRI" 제거)
- [x] P1-5: Generic provenance DDL/repo 일관화 (source_domain/source_kind + user facts provenance)
- [x] P1-6: catalog_validation 방어 심화 + explainer goal split 수정

### P2: 문서
- [x] README.md 신규 생성
- [x] ARCHITECTURE.md: evidence vs serving graph, kg_mode contract, promoted-only invariant
- [x] CHANGELOG.md: vNext 변경 기록

## 테스트 상태
- 180 tests 전부 통과

## 신규 테스트 파일
- tests/test_projection_registry_schema.py (4 tests)
- tests/test_serving_uses_promoted_only.py (4 tests)
- tests/test_catalog_validation_exclusion.py (4 tests)
- tests/test_candidate_prefilter.py (2 tests)
- tests/test_generic_provenance.py (5 tests)
- tests/test_user_preference_weighting.py (6 tests)
- tests/test_signal_evidence_source_of_truth.py (기존 확장 +1)

## 향후 작업 (deferred)
- [ ] NER-BeE flatten → anchor evidence transition
- [ ] BEE contract 추가 필드 (evidence_text, evidence_span, derived_qualifiers)
- [ ] Registry source/evidence gate enforcement
- [ ] Usage pattern mart (context×tool, context×bee_attr 등)
- [ ] kg_mode legacy/shadow 완전 분리
- [ ] SQL prefilter 실제 DB 통합 테스트
- [ ] corpus_weight 기반 scorer feature 계산
