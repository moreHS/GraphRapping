# GraphRapping vNext 수정/업데이트 작업 지시서 (최신 main 기준)

작성 목적: 최신 `main` 레포를 다시 점검한 결과를 기준으로,
**이미 수정 완료된 항목은 제외**하고,
남아 있는 구조 이슈/운영 리스크/방향성 보강 포인트만 Claude Code에게 파일 단위로 지시하기 위한 문서.

---

## 0. 이번 문서에서 제외하는 항목 (이미 반영된 것으로 확인)

아래 항목은 최신 `main`에서 이미 고쳐졌거나, 최소한 이전의 치명적 불일치는 해소된 것으로 판단하여 **재지시하지 않음**.

1. incremental pipeline에서 child raw rows 재로딩
2. `recommended_to`의 `qualifier_required=Y` 불일치
3. reverse transform의 `dst_ref_kind = ENTITY` 완전 하드코딩
4. BEE negation / intensity가 canonical fact로 아예 전달되지 않던 문제
5. `used_with`에서 Product co-use 축 부재
6. `main_benefits` ↔ `goal_fit` 완전 부재
7. user layer가 단순 preference edge만 갖던 상태

단, 위 항목이 **완벽히 끝난 것**과 **기본 수정이 들어간 것**은 다르다.
이번 문서에서는 **최신 main에서도 아직 남아 있는 부분만** 후속 작업으로 남긴다.

---

## 1. 최신 main 기준 핵심 진단

### 1-1. 방향성 판정

현재 프로젝트는 더 이상 “상품 1개 그래프를 보기 좋게 렌더링하는 프로젝트”가 아니라,
**상품 정본 + 리뷰 증거 + 유저 프로필을 shared concept plane으로 묶어 추천/개인화/탐색에 활용하려는 구조**로 상당히 옮겨와 있다.

하지만, 아직 네가 진짜 목표로 한

> 리뷰 코퍼스 전체를 KG화 → 노이즈 컷 → 고가중치/고신뢰 edge만 승격 → 추천/탐색에 활용

의 마지막 단계가 **fully enforced**되지는 않았다.

핵심적으로,
`aggregate_product_signals.py`는 `distinct_review_count`, `avg_confidence`, `synthetic_ratio`, `corpus_weight`, `is_promoted`를 계산하지만,
`build_serving_views.py`는 아직 `is_promoted=True`만 사용하도록 필터링하지 않는다.
즉 **corpus promotion은 계산하지만 serving에서는 아직 강제되지 않는다.**

### 1-2. review graph를 evidence-only로 두는 방향에 대한 결론

이건 잘못된 방향이 아니다.
오히려 `src/kg`는 **per-review evidence graph**로 두고,
전역적인 corpus KG와 serving graph는 Layer 2 / 2.5 / 3에서 재구성하는 쪽이 맞다.

문제는 evidence-only 자체가 아니라,
**Layer 3가 promoted-only corpus graph를 강하게 사용하지 않는 점**이다.

---

## 2. P0 — 반드시 먼저 고쳐야 하는 것

## P0-1. Corpus promotion을 실제 serving에서 강제

### 문제 설명

현재 aggregate는 corpus promotion 지표를 계산한다.
하지만 serving product profile은 window만 필터링하고 edge type별 score 상위 N개를 그대로 사용한다.
이 상태면 synthetic/저신뢰/저지지 신호가 여전히 추천/탐색 경로로 흘러갈 수 있다.

즉 지금은
- `evidence graph -> canonical facts -> wrapped signals -> aggregate`
까지는 잘 가는데,
- `aggregate -> serving profile`
에서 **promotion gating이 빠져 있다.**

네가 원한 “노이즈를 걸러서 weight 높은 것만 올리는 전역 KG”와 가장 크게 어긋나는 지점이 바로 여기다.

### 수정 대상 파일
- `src/mart/aggregate_product_signals.py`
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- 필요 시: `sql/ddl_mart.sql`
- 테스트:
  - `tests/test_serving_uses_promoted_only.py` (신규)
  - `tests/test_corpus_promotion_thresholds.py` (신규)

### 수정 지시

1. `build_serving_product_profile()`에 기본 정책 추가:
   - 기본값: `promoted_only=True`
   - `window_signals = [s for s in agg_signals if s.window_type == requested_window and s.is_promoted]`
   - 단, 디버그/분석용으로만 `include_non_promoted=True` 옵션 허용

2. `top_*` signal 필드 전체에 동일 정책 적용:
   - `top_bee_attr_ids`
   - `top_keyword_ids`
   - `top_context_ids`
   - `top_concern_pos_ids`
   - `top_concern_neg_ids`
   - `top_tool_ids`
   - `top_comparison_product_ids`
   - `top_coused_product_ids`

3. promoted signal이 없는 product는
   - truth profile만 유지하되
   - signal columns는 빈 리스트로 반환

4. `candidate_generator.py`는 기본적으로 serving profile만 읽으므로,
   별도 raw/agg bypass 경로가 없도록 확인

5. scorer가 직접 agg rows를 읽는 우회 경로가 있다면 제거

### Acceptance Criteria
- serving product profile의 top signal들은 기본적으로 모두 `is_promoted=True` 그룹에서만 생성된다.
- promoted signal이 하나도 없는 product도 truth profile은 정상 생성된다.
- 동일 raw 데이터에서 promoted_only on/off의 결과 차이가 재현된다.
- non-promoted signal은 debug mode에서만 노출된다.

### 테스트 항목
- support 1, synthetic_ratio 높은 signal은 aggregate에는 있으나 serving profile에는 안 올라온다.
- distinct_review_count ≥ 3, avg_confidence ≥ 0.6, synthetic_ratio ≤ 0.5인 signal은 serving에 올라온다.
- promoted-only policy를 켠 뒤 recommendation candidate set이 달라지는지 확인.

---

## P0-2. `projection_registry.csv` 구조 오류를 fail-fast로 잡도록 수정

### 문제 설명

최신 `main`의 `configs/projection_registry.csv`는 로컬 parse 기준으로 row length 불일치가 있다.
특히 `same_entity`, `no_relationship` 행에서 header 14 columns 대비 15 fields가 들어가 CSV parser가 실패할 수 있다.
이 파일은 Layer 2 → 2.5의 핵심 계약 파일이라,
파싱 실패나 느슨한 허용은 전체 signal projection을 망가뜨릴 수 있다.

### 수정 대상 파일
- `configs/projection_registry.csv`
- `src/wrap/projection_registry.py`
- 테스트:
  - `tests/test_projection_registry_schema.py` (신규 또는 기존 확장)

### 수정 지시

1. `projection_registry.csv`의 malformed rows 수정
   - 모든 row가 header와 동일한 field count를 갖게 정리
   - `same_entity`, `no_relationship`는 의도에 맞게 14-column으로 축소

2. `projection_registry.py` 로더에서 strict schema validation 추가:
   - expected columns 고정
   - field count mismatch 즉시 `ValueError`
   - unknown transform / unknown action / unknown weight_rule 즉시 실패

3. startup/load 단계에서 registry validation을 통과하지 못하면 pipeline 시작 금지

4. 가능하면 `projection_registry.csv`를 lint하는 작은 helper 추가

### Acceptance Criteria
- malformed CSV면 서비스가 조용히 시작되지 않고 fail-fast한다.
- 모든 registry row는 14-column contract를 만족한다.
- `same_entity` / `no_relationship` 같은 non-projecting predicate도 명시적 action으로 파싱된다.

### 테스트 항목
- 정상 CSV는 load 성공
- field count mismatch CSV는 예외 발생
- 미등록 transform 문자열은 예외 발생

---

## P0-3. `signal_evidence`를 provenance 정본으로 실제 코드까지 통일

### 문제 설명

최신 `signal_emitter.py`는 docstring에서
“provenance source of truth = `signal_evidence`”라고 말한다.
하지만 실제 구조에는 아직 `wrapped_signal.source_fact_ids`가 남아 있고,
aggregate의 `evidence_sample`도 `source_fact_ids[0]`을 참조한다.
즉 개념상 정본은 정해졌지만,
런타임 일부는 여전히 cache field를 실제 provenance처럼 쓰고 있다.

이 상태는 나중에 explanation/debug에서 정합성 문제를 만든다.

### 수정 대상 파일
- `sql/ddl_signal.sql`
- `src/wrap/signal_emitter.py`
- `src/db/repos/signal_repo.py`
- `src/mart/aggregate_product_signals.py`
- `src/rec/explainer.py`
- `src/db/repos/provenance_repo.py`
- 테스트:
  - `tests/test_signal_evidence_source_of_truth.py`
  - `tests/test_evidence_sample_from_signal_evidence.py`

### 수정 지시

1. `wrapped_signal.source_fact_ids`를 아래 둘 중 하나로 정리
   - A안(권장): DDL에서 제거
   - B안: 유지하되 cache/debug only로 두고, write source는 `signal_evidence`에서 재계산된 값만 허용

2. `signal_emitter.py`
   - `existing.source_fact_ids.append(...)` 같은 직접 mutation 제거 또는 cache-only rebuild로 전환
   - evidence row 생성을 항상 primary path로 사용

3. `aggregate_product_signals.py`
   - `evidence_sample` 생성 시 `source_fact_ids`를 직접 보지 말 것
   - aggregate 단계에서 signal_evidence를 직접 주입받지 않는다면,
     `evidence_sample`은 nullable로 두고 aggregate 단계에서 생성하지 않거나,
     별도 enrichment step에서 채우도록 분리

4. `explainer.py` / `provenance_repo.py`
   - explanation chain은 오직 `signal_evidence -> canonical_fact -> fact_provenance -> review_raw`만 사용

### Acceptance Criteria
- signal provenance 정본은 코드/DDL/설명 경로 모두 `signal_evidence`로 일치한다.
- `source_fact_ids`를 제거해도 explanation chain이 유지된다.
- aggregate evidence sample이 cache field에 의존하지 않는다.

### 테스트 항목
- signal_evidence만으로 explanation path 복원 가능
- cache field 제거/비사용 모드에서도 설명 동작
- aggregate evidence sample이 signal_evidence 기반으로 재구성 가능

---

## 3. P1 — 구조/운영성 보강

## P1-1. review evidence graph와 serving graph의 경계를 코드/문서로 더 명확히 분리

### 문제 설명

현재 `src/kg`는 per-review KG pipeline이고,
synthetic relation과 keyword candidates를 evidence-only로 낮추는 방향으로 많이 개선됐다.
이건 맞는 방향이다.
하지만 repo 안에 `src/kg`, `src/graph`, `src/web`, `src/static`가 모두 함께 있고,
`run_daily_pipeline.py`도 `kg_mode = off|shadow|on` 경로를 유지한다.
즉 evidence graph 실험축과 serving recommendation 코어가 아직 완전히 분리되진 않았다.

### 수정 대상 파일
- `src/jobs/run_daily_pipeline.py`
- `src/kg/*`
- `ARCHITECTURE.md`
- `README.md`
- 필요 시: 신규 `src/jobs/run_kg_shadow.py`

### 수정 지시

1. 문서에 아래를 명시
   - `src/kg`는 evidence graph / debug graph / analyst graph
   - Layer 2/2.5/3가 corpus KG / serving graph 본체

2. `kg_mode` handling을 core path에서 최소화
   - 가능하면 shadow execution entrypoint 분리
   - core recommendation path는 canonical/signal/mart 경로만 남김

3. front graph viewer가 있다면 포지션을 명확히
   - evidence graph viewer
   - serving graph viewer
   둘을 혼동하지 않게 한다.

### Acceptance Criteria
- 문서상 evidence graph와 serving graph 역할이 명확히 분리된다.
- core pipeline이 experimental branch에 덜 의존한다.
- reviewer/debug용 graph와 serving recommendation graph가 혼용되지 않는다.

---

## P1-2. user aggregation weighting을 구조적으로 강화

### 문제 설명

최신 user layer는 이전보다 훨씬 좋아졌다.
state / concern / goal / context / behavior가 canonical fact와 serving profile에 들어간다.
하지만 `aggregate_user_preferences.py`는 여전히 `(predicate, dst_id)`별 max confidence 위주로 단순 집계한다.
즉 “최근 explicit chat 선호”, “반복 구매 선호”, “낮은 확신의 간접 추론”을 충분히 다르게 다루지 못한다.

### 수정 대상 파일
- `src/mart/aggregate_user_preferences.py`
- `src/user/adapters/personal_agent_adapter.py`
- `src/user/canonicalize_user_facts.py`
- `src/rec/scorer.py`
- 테스트:
  - `tests/test_user_preference_weighting.py` (신규)

### 수정 지시

1. user preference aggregation에 최소 아래 축 추가
   - `source_type_weight` (purchase > explicit_chat > inferred_profile)
   - `recency_weight`
   - `frequency_weight`

2. `source_mix`는 단순 set이 아니라 weight derivation 근거를 남기는 구조로 보강

3. scorer에서 user-side feature를 조금 더 명시적으로 사용
   - `skin_type_fit`
   - `purchase_loyalty`
   - `goal_fit_master`
   - `goal_fit_review_signal`
   는 유지
   - 가능하면 context preference confidence도 반영

### Acceptance Criteria
- purchase-derived 선호와 weak chat-derived 선호가 동일하게 취급되지 않는다.
- 최근 반복 행동이 오래된 단발 행동보다 더 큰 가중치를 가질 수 있다.
- user profile의 state/behavior가 scorer에 실제 반영된다.

---

## P1-3. SQL-first 방향으로 candidate / aggregate를 더 밀기

### 문제 설명

현재 최신 구현은 correctness 면에서는 좋아졌지만,
candidate generation과 dirty product aggregate가 여전히 Python loop에 꽤 의존한다.
이는 catalog/review volume이 커질수록 운영 비용을 높인다.

### 수정 대상 파일
- `src/rec/candidate_generator.py`
- `src/jobs/run_incremental_pipeline.py`
- `src/db/repos/mart_repo.py`
- 필요 시: 신규 SQL query 파일
- 테스트:
  - `tests/test_candidate_prefilter_sql_logic.py`
  - `tests/test_batch_aggregate_consistency.py`

### 수정 지시

1. candidate generation
   - 1차는 SQL prefilter (category / active / avoided ingredient / price ceiling)
   - 2차만 Python overlap + scorer

2. dirty product aggregate
   - per-product `SELECT * FROM wrapped_signal WHERE target_product_id = $1` 반복 대신
   - batch dirty product set을 group-by aggregate하는 path 추가

3. Python recompute path는 fallback/debug 모드로 남겨도 됨

### Acceptance Criteria
- 전체 product list full-scan 없이 1차 후보가 줄어든다.
- batch aggregate와 기존 per-product aggregate 결과가 동일하다.
- incremental recompute 비용이 dirty product 수에 비례하되, 불필요한 full read가 줄어든다.

---

## P1-4. `concept_id`를 serving/runtime 표준 키로 고정

### 문제 설명

최신 코드에선 concept join 방향이 많이 좋아졌지만,
주석/용어에서 아직 `concept IRI`와 `concept_id`가 혼용된다.
serving/runtime에서는 `concept_id`로 통일하는 편이 안전하다.

### 수정 대상 파일
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `ARCHITECTURE.md`
- `README.md`
- 테스트:
  - `tests/test_concept_key_consistency.py`

### 수정 지시

1. serving product/user profile, candidate generator, scorer에서는 `concept_id`를 canonical join key로 통일
2. IRI는 canonical/entity layer에서만 사용
3. 주석과 필드명에서 “concept IRI” 표현 제거

### Acceptance Criteria
- serving/runtime 레이어는 `concept_id`만 비교한다.
- entity/canonical layer에서만 IRI가 필요하다.
- 문서/주석/코드 용어가 일치한다.

---

## P1-5. provenance를 review 전용에서 범용 모델로 더 일반화

### 문제 설명

`canonical_fact_builder.py`에는 이미 `FactProvenance.source_domain/source_kind`가 들어와 있어서 방향은 좋다.
하지만 DDL/Repo/설명 경로 전부가 이 범용성을 일관되게 쓰는지까지는 더 강화할 여지가 있다.

### 수정 대상 파일
- `sql/ddl_canonical.sql`
- `src/canonical/canonical_fact_builder.py`
- `src/db/repos/provenance_repo.py`
- `src/user/canonicalize_user_facts.py`
- `src/ingest/product_ingest.py`

### 수정 지시

1. review-derived fact 뿐 아니라
   - user fact
   - product truth fact
   - manual fact
   도 같은 provenance 모델로 표현되도록 repo write path 점검

2. explanation에서는 review facts는 snippet까지,
   product/user facts는 provenance summary까지 내려주도록 분리

### Acceptance Criteria
- review/user/product/manual facts가 모두 같은 provenance contract를 따른다.
- explanation chain이 review와 non-review provenance를 구분해 다룬다.

---

## P1-6. `catalog_validation_signal`은 recommendation 경로에서 완전 배제

### 문제 설명

최신 scorer는 `SCORING_EXCLUDED_FAMILIES`를 통해 catalog validation을 제외한다.
좋다. 하지만 candidate overlap, serving profile top signals, standard explanation에도 섞이지 않도록 경계를 더 명시하는 게 좋다.

### 수정 대상 파일
- `src/mart/build_serving_views.py`
- `src/rec/candidate_generator.py`
- `src/rec/scorer.py`
- `src/rec/explainer.py`
- 테스트:
  - 기존 `test_truth_override_protection.py` 확장

### 수정 지시

1. serving product profile의 `top_*` signal에 catalog_validation family가 들어오지 않게 보장
2. candidate overlap 계산에서 catalog_validation 완전 제외
3. standard explanation path에서 catalog_validation 미노출
4. QA/debug mode에서만 별도 노출 허용

### Acceptance Criteria
- catalog_validation은 QA/debug용이고, 추천 품질 계산에는 개입하지 않는다.

---

## P2 — 코드베이스 건강도 / 문서

## P2-1. README / ARCHITECTURE / CHANGELOG / repo metadata 정리

### 문제 설명

지금 repo는 루트 문서가 생기긴 했지만, GitHub 메타데이터(description/website/topics)와 변경 이력 관리가 여전히 약하다.
도메인 규칙이 많은 프로젝트라서 유지보수 리스크가 크다.

### 수정 대상 파일
- `README.md`
- `ARCHITECTURE.md`
- `CHANGELOG.md`
- GitHub repo metadata
- `pyproject.toml`

### 수정 지시

1. README:
   - 프로젝트 목적
   - 5-layer + Common Concept Plane
   - evidence graph vs corpus KG vs serving graph 분리
   - local run / test / DB migrate 방법

2. ARCHITECTURE:
   - 레이어별 contract
   - invariants
   - promoted-only serving 원칙

3. CHANGELOG:
   - 큰 계약 변경 기록

4. pyproject:
   - `ruff`
   - `mypy`
   - `pytest-cov`
   추가 검토

---

## 4. 권장 작업 순서

### 이번 사이클에서 먼저
1. P0-1 Corpus promotion serving 강제
2. P0-2 projection_registry.csv malformed rows 수정 + strict validation
3. P0-3 signal_evidence 정본화

### 그 다음
4. P1-1 evidence graph / serving graph 경계 정리
5. P1-2 user aggregation weighting 강화
6. P1-3 SQL-first candidate / aggregate
7. P1-4 concept_id 통일
8. P1-5 generic provenance 일관화
9. P1-6 catalog_validation 완전 분리

### 마지막
10. P2 문서 / repo hygiene

---

## 5. 최종 Acceptance Criteria

이번 후속 수정이 끝나면 아래를 만족해야 한다.

1. `serving_product_profile`은 기본적으로 promoted signal만 사용한다.
2. `projection_registry.csv`는 strict parser에서 100% 통과한다.
3. signal provenance 정본은 `signal_evidence` 하나로 통일된다.
4. recommendation runtime은 evidence/debug용 synthetic signal을 직접 먹지 않는다.
5. user weighting은 source/recency/frequency 차이를 반영한다.
6. serving/runtime concept join key는 `concept_id`로 일관된다.
7. catalog_validation은 QA/debug 외 추천 경로에서 배제된다.
8. evidence graph와 corpus/serving graph의 역할이 문서/코드에서 분리된다.

---

## 6. Claude Code에 전달할 핵심 한 문장

> 이번 수정은 구조를 갈아엎는 작업이 아니다.
> 목표는 **review evidence graph를 corpus-promoted serving graph로 제대로 승격시키고**,
> **signal provenance 정본을 정리하고**,
> **user weighting과 SQL-first serving 방향을 강화하는 것**이다.
