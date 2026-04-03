# HANDOFF — 후속 수정 지시서 완료 상태

## 이번 세션 완료 항목

### P0-1 확장: family-level identity 연결
- [x] serving_product_profile에 `variant_family_id` 추가
- [x] serving_user_profile에 `owned_family_ids`, `repurchased_family_ids` 추가
- [x] personal_agent_adapter에 `OWNS_FAMILY`, `REPURCHASES_FAMILY` predicate 수용
- [x] enums.py에 새 predicate 등록
- [x] candidate_generator에 `owned_family_match` 플래그 + same-family detection
- [x] scorer에 `owned_family_penalty`, `repurchase_family_affinity` feature + novelty에 family 반영

### P0-2 확장: texture config 분리
- [x] `configs/texture_keyword_map.yaml` 신규 생성 (texture_axis + surface_to_keyword)
- [x] personal_agent_adapter: 하드코드 `_TEXTURE_KEYWORD_MAP` → config loader 전환

### P0-4 확장: raw profile validation
- [x] user_loader.py: raw 7-column 감지 시 ValueError, basic 키 누락 시 ValueError

### P1-2 신규: co-used product / tool feature
- [x] candidate_generator: tool/co-used overlap 추가
- [x] scorer: `tool_alignment`, `coused_product_bonus` feature
- [x] explainer: tool/co-used 한국어 설명 + edge map

### P1-4 신규: rs.jsonl first-class loader
- [x] `src/loaders/rs_jsonl_loader.py` 신규 — S3 rs.jsonl → RawReviewRecord 변환
- [x] NER label 매핑 (BASE_COLOR→COL, CAPACITY→VOL, BRAND→BRD, CATEGORY→CAT)
- [x] channel→site 매핑, author_key 생성, 복합 sentiment 처리

### 프론트 mock 통합 (이전 세션)
- [x] server.py: mockdata/ 상품/유저 로딩 + 50K 리뷰 랜덤 상품ID 배분

## 테스트 상태
- **239 tests 전부 통과** (이전 218 → 239, 신규 21)

## 신규 테스트 파일 (이번 세션)
- tests/test_family_level_personalization.py (4 tests) — P0-1
- tests/test_texture_taxonomy_alignment.py (4 tests) — P0-2
- tests/test_user_loader_contract.py (3 tests) — P0-4
- tests/test_rs_jsonl_transform.py (6 tests) — P1-4
- tests/test_coused_product_and_tool_features.py (4 tests) — P1-2

## 수정된 소스 파일 (이번 세션)
- src/mart/build_serving_views.py — variant_family_id + owned/repurchased family_ids
- src/common/enums.py — OWNS_FAMILY, REPURCHASES_FAMILY
- src/user/adapters/personal_agent_adapter.py — family predicates + texture config 전환
- src/rec/candidate_generator.py — family match + tool/co-use overlap
- src/rec/scorer.py — family penalty/affinity + tool/co-use features
- src/rec/explainer.py — tool/co-use 설명 + edge map
- src/loaders/user_loader.py — raw profile validation
- src/loaders/rs_jsonl_loader.py (신규) — rs.jsonl loader
- src/web/server.py — mock 데이터 통합 로딩
- configs/texture_keyword_map.yaml (신규) — texture 정규화 config
- .gitignore — _remapped_reviews.json 제외

## 최종 완료 기준 체크리스트
1. [x] product truth 필드가 loader→ingest→serving까지 반영
2. [x] variant_family_id가 serving profile에 노출
3. [x] family-level owned/repurchased가 candidate/scorer에서 구분
4. [x] texture가 BEE_ATTR + KEYWORD 2단으로 config 기반 정규화
5. [x] promoted-only serving 전경로 강제
6. [x] raw profile 입력 시 명시적 validation error
7. [x] rs.jsonl → RawReviewRecord first-class transform 경로
8. [x] co-used/tool signal이 scoring feature로 활용
9. [x] mock regression tests 가동 (shared_entities, review_kg_output)
10. [x] 프론트에서 mock 데이터 기반 파이프라인 실행 가능

## 향후 작업 (deferred)
- [ ] P1-1: user weighting config 분리 (configs/user_weighting.yaml)
- [ ] P1-3: generic provenance 범용화 (source_domain/source_kind 확장)
- [ ] P2-2: SQL-first candidate path 공식화
- [ ] keyword_normalizer에서도 texture config 공유
- [ ] rs.jsonl loader를 server.py demo에 연동
