# GraphRapping 결함 수정 계획 v4 (Post-Audit Fix Plan)

## Context

Sprint 1~3 후 GPT 리뷰 2회 REJECT → 수정 계획 v1~v3 REJECT.
v4에서는 v3 REJECT 5개 사유 반영:
1. predicate validation scope: review predicates만 (user prefs는 별도 계약)
2. invalid-fact quarantine: 구체 경로 + 핸들러 메서드
3. event_time_utc 파싱이 emission 전에 반드시 완료되는 순서 보장
4. product_linkage transform 실행 위치: SignalEmitter 내부에서 처리
5. benefits 필드명 통일: main_benefit_concept_ids 전 레이어 일관

---

## Phase 0: Contract 선행

### 0-1. ProjectionResult 메타 노출
`src/wrap/projection_registry.py` — `ProjectionResult` dataclass에 추가:
```python
qualifier_required: bool = False
qualifier_type: str = ""
transform: str = "identity"
weight_rule: str = "default_weight"
```
`project()` 메서드에서 이 4개를 `ProjectionRule`에서 복사해서 반환.

### 0-2. predicate contract 검증 (scope 분리)

**Review predicate contracts** (65개): `configs/predicate_contracts.csv`
- enforcement: `CanonicalFactBuilder.add_fact()` — review-derived facts (REL/BEE) 검증
- `__init__(contracts: dict | None)` — contracts 로드 (optional, 없으면 검증 skip)
- `add_fact()`에서 predicate가 contracts에 있으면 allowed_subject_types/allowed_object_types 체크
- 위반 시 → fact 생성 거부 + `_invalid_facts: list[dict]`에 추가 (payload: fact_id, predicate, subj_type, obj_type, reason)

**User preference contracts** (17개): `src/common/enums.py:USER_PREFERENCE_EDGE_TYPES`
- enforcement: `canonicalize_user_facts.py:canonicalize_user_facts()` — 이미 `if predicate not in USER_PREFERENCE_EDGE_TYPES: continue` 존재
- **predicate_contracts.csv에 user predicates를 추가하지 않음** (별도 namespace)

**Invalid-fact quarantine 경로**:
- `QuarantineHandler`에 새 메서드 추가: `quarantine_invalid_fact(fact_payload: dict)`
- 기존 5개 quarantine 테이블 중 `quarantine_projection_miss`를 재활용 (reason="PREDICATE_CONTRACT_VIOLATION")
- pipeline에서: `for inv in builder.invalid_facts: quarantine.quarantine_invalid_fact(inv)`

### 0-3. pyproject.toml pythonpath
`[tool.pytest.ini_options]`에 `pythonpath = ["."]`

### 0-4. 누락 파일
- `configs/relation_canonical_map.json`: 65 identity mapping
- `src/ingest/user_ingest.py`: user_master 적재
- `src/normalize/ner_normalizer.py`: NER → canonical entity

### 0-5. product_ingest에 main_benefits concept link 추가
**현상**: `src/ingest/product_ingest.py:95`는 Brand/Category/Ingredient/Country만 concept seed.
main_benefits가 누락되어 Phase 4에서 `main_benefit_concept_ids` 불가.
**수정**: main_benefits 각 항목을 Goal concept로 등록 + entity_concept_link 생성
```python
for benefit in record.main_benefits or []:
    benefit_cid = _make_concept_id(ConceptType.GOAL, benefit)
    concepts.append(ConceptSeed(concept_id=benefit_cid, concept_type=ConceptType.GOAL, ...))
    links.append(EntityConceptLink(entity_iri=product_iri, concept_id=benefit_cid, link_type="HAS_MAIN_BENEFIT"))
```
**파일**: `src/ingest/product_ingest.py`

---

## Phase 1: event_time 파싱 + window_ts hop

### 1-1. review_ingest.py: event_time_utc 파싱
**현재**: `event_time_utc = None` 항상 (line 72)
**수정**: `created_at` 또는 `collected_at` 문자열을 실제 파싱
```python
from datetime import datetime, timezone

event_time_utc = None
event_time_source = EventTimeSource.PROCESSING_TIME
event_time_raw_text = None

if record.created_at:
    event_time_raw_text = record.created_at
    event_time_utc = _parse_to_utc(record.created_at)
    event_time_source = EventTimeSource.SOURCE_CREATED if event_time_utc else EventTimeSource.PROCESSING_TIME
elif record.collected_at:
    event_time_raw_text = record.collected_at
    event_time_utc = _parse_to_utc(record.collected_at)
    event_time_source = EventTimeSource.COLLECTED_AT if event_time_utc else EventTimeSource.PROCESSING_TIME

if event_time_utc is None:
    event_time_utc = datetime.now(timezone.utc)
    event_time_source = EventTimeSource.PROCESSING_TIME

def _parse_to_utc(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None
```
**결과**: `event_time_utc`가 **절대 None이 아님** (최악의 경우 현재 시각)
**파일**: `src/ingest/review_ingest.py:71-82`

### 1-2. source_row_num 추가
`RawReviewRecord`에 `source_row_num: str | None = None`
`ingest_review()`에서 `make_review_id()` 호출 시 `source_row_num=record.source_row_num` 전달
**파일**: `src/ingest/review_ingest.py`

### 1-3. ★ event_time_utc → window_ts 명시적 hop
**현상**: `run_daily_pipeline.py:129`에서 `emit_from_facts()` 호출 시 `window_ts` 미전달
**수정**:
```python
# run_daily_pipeline.py
window_ts = ingested.review_raw.get("event_time_utc")  # or str(event_time_utc)
emit_result = emitter.emit_from_facts(
    facts=builder.facts,
    target_product_id=...,
    window_ts=str(window_ts) if window_ts else None,
)
```
**이 hop이 없으면 Phase 4의 freshness와 windowed aggregate 전부 무효**
**파일**: `src/jobs/run_daily_pipeline.py`

---

## Phase 2: Pipeline REL wiring (5 substeps)

### ★ REL→NER join 계약 (v3 핵심 추가)
**현상**: `rel_rows`는 `subj_text/subj_group/obj_text/obj_group`만 가짐.
`resolved_mentions`는 mention index로 keyed.
텍스트가 겹칠 때 deterministic join 불가.

**해결**: `review_ingest.py`에서 REL row에 mention index 추가
```python
# rel_row 구성 시
rel_rows.append({
    "review_id": review_id,
    "subj_text": subj.get("word", ""),
    "subj_group": subj.get("entity_group", ""),
    "subj_start": subj.get("start"),  # ★ 추가
    "subj_end": subj.get("end"),      # ★ 추가
    "obj_text": obj.get("word", ""),
    "obj_group": obj.get("entity_group", ""),
    "obj_start": obj.get("start"),    # ★ 추가
    "obj_end": obj.get("end"),        # ★ 추가
    "relation_raw": rel.get("relation", ""),
    "source_type": rel.get("source_type"),
})
```

**Pipeline join logic**:
```python
# run_daily_pipeline.py Phase 2-2
# NER mention index lookup: (start, end, text) → mention_idx
mention_index_map = {}
for idx, ner in enumerate(ingested.ner_rows):
    key = (ner.get("start_offset"), ner.get("end_offset"), ner["mention_text"])
    mention_index_map[key] = idx

# REL → resolved IRI
for rel_row in ingested.rel_rows:
    subj_key = (rel_row.get("subj_start"), rel_row.get("subj_end"), rel_row["subj_text"])
    obj_key = (rel_row.get("obj_start"), rel_row.get("obj_end"), rel_row["obj_text"])

    subj_idx = mention_index_map.get(subj_key)
    obj_idx = mention_index_map.get(obj_key)

    subj_iri = resolution.resolved_mentions[subj_idx].resolved_iri if subj_idx is not None else make_mention_iri(...)
    obj_iri = resolution.resolved_mentions[obj_idx].resolved_iri if obj_idx is not None else make_mention_iri(...)
```

**BEE subject는 NER에 없음** (source_type="NER-BeE") → obj_key가 BEE phrase이므로 NER index에 없을 수 있음
→ BEE object는 `bee_attr_id` 또는 `mention_iri`로 fallback

### 2-1. Resolved mention → entity 등록
모든 PRODUCT_TARGET/REVIEWER_PROXY/SAME_ENTITY_MERGE → register_entity()
UNRESOLVED → quarantine_placeholder()

### 2-2. REL triple → canonical fact (위 join 계약 사용)
subject_iri/object_iri를 resolved mention에서 결정.
object가 BEE phrase(NER-BeE type)이면 bee_attr concept IRI 사용.

### 2-3. DATE mention → split + entity 등록
NER entity_group=="DATE" → split_date() → register TemporalContext/Frequency/Duration/AbsoluteDate

### 2-4. Ambiguous type derivation
used_with → derive_used_with() → Tool/Product
causes/affects/addresses → derive_concern() → Concern
recommended_to/targeted_at → derive_segment() → UserSegment
실패 → quarantine_untyped_entity()

### 2-5. Unknown REL quarantine
canonicalizer QUARANTINE → quarantine_projection_miss(predicate=actual, subject_type=actual, object_type=actual)

---

## Phase 3: Signal emitter transform/qualifier

### 3-1. Transform dispatcher (★ product_linkage는 SignalEmitter 내부에서 처리)
**실행 위치**: `src/wrap/signal_emitter.py:emit_from_fact()` 내부
`relation_projection.py:project_bee_keyword_signals()`는 삭제하거나 deprecated — 로직을 SignalEmitter로 이관

```python
# signal_emitter.py:emit_from_fact() 내부
transform = result.transform  # from ProjectionResult

if transform == "reverse":
    dst_id = fact.subject_iri
    # dst_type은 registry의 output_dst_type 사용 (e.g., Concern)
elif transform == "product_linkage":
    # BEE keyword: fact는 BEEAttr→Keyword, 하지만 signal은 Product→Keyword
    # dst_id = fact.object_iri (Keyword IRI)
    # signal은 target_product_id 기준으로 anchor
    dst_id = fact.object_iri
    bee_attr_id = fact.subject_iri  # BEEAttr IRI
    keyword_id = fact.object_iri    # Keyword IRI
else:  # identity
    dst_id = fact.object_iri or fact.object_value_text
```
**핵심**: product_linkage의 모든 로직이 SignalEmitter 내부에서 완결. 외부 helper 불필요.

### 3-2. Qualifier check
`result.qualifier_required` True + fact.qualifiers 비어있으면 → quarantine

### 3-3. Weight rule
`result.weight_rule == "bee_weight"` → `weight = fact.confidence or 1.0`
그 외 → `weight = 1.0`

### 3-4. BEE keyword routing
`emit_from_facts()`: predicate=="HAS_KEYWORD" + target_product_id → `project_bee_keyword_signals()` 호출
**registry 계약 수정**: `HAS_KEYWORD`의 transform을 `product_linkage`로 변경
(현재 CSV에서는 identity로 돼 있고, Python helper는 Product→Keyword로 재구성)

---

## Phase 4: Concept IRI 통일

### 4-1. DDL + 필드명 통일: serving_product_profile
**Benefits 필드명 계약**: 전 레이어에서 `main_benefit_concept_ids` 사용
- `product_master.main_benefits` (raw text[]) → product_ingest에서 Goal concept 변환 → entity_concept_link
- `build_serving_views.py`: entity_concept_link에서 `main_benefit_concept_ids` (concept IRI[]) 생성
- `candidate_generator.py`: `goal_ids`(user) vs `main_benefit_concept_ids`(product) 비교
- **기존 `main_benefit_ids` 필드는 `main_benefit_concept_ids`로 rename**

```sql
-- ddl_mart.sql 수정: serving_product_profile에 추가
brand_concept_ids jsonb,
category_concept_ids jsonb,
ingredient_concept_ids jsonb,
main_benefit_concept_ids jsonb,  -- Goal concept IRIs (Phase 0-5)
-- review_count_30d, review_count_90d: 기존 컬럼 있음, 값 채우기만 필요
-- last_signal_at: 기존 컬럼 있음, 값 채우기만 필요
```

### 4-2. build_serving_views.py: concept IRI + freshness 채우기
**입력**: product_master + entity_concept_link rows + agg_product_signal (전 window)
**출력**: concept IRI 필드 + review_count_30d/90d/last_signal_at

### 4-3. candidate_generator.py: concept IRI 비교
`_extract_ids()` → concept IRI set으로 통일
user `preferred_brand_ids[].id` vs product `brand_concept_ids[].id`

### 4-4. scorer.py
scorer는 overlap_concepts 기반이므로 candidate_generator에서 올바른 concept IRI로 overlap 생성하면 자동 해결.
`_freshness_score()`는 이미 `review_count_30d` 읽으므로 Phase 4-2만 완성하면 동작.

---

## Phase 5: Evidence chain

### 5-1. signal_emitter: evidence row 생성
`EmitResult`에 `evidence_rows: list[dict]` 추가.
signal 생성/merge 시 source_fact_id별 evidence row 동시 생성.

### 5-2. explainer: provenance 경로
`explain()` signature:
```python
def explain(scored, overlap_concepts, top_n=5,
            signal_evidence_lookup=None, fact_provenance_lookup=None):
```
lookup 있으면 → signal→fact→snippet 체인.
없으면 → 기존 overlap 기반 fallback.

---

## Phase 6: Pipeline L3/4 연결

### run_batch() I/O 계약
```python
def run_batch(
    reviews: list[RawReviewRecord],
    source: str,
    product_index: ProductIndex,
    product_masters: dict[str, dict],    # product_id → product_master row
    concept_links: dict[str, list[dict]], # product_iri → entity_concept_link rows
    user_masters: dict[str, dict],       # user_id → user_master row
    user_adapted_facts: dict[str, list[dict]], # user_id → adapted preference facts
    bee_normalizer: BEENormalizer,
    relation_canonicalizer: RelationCanonicalizer,
    projection_registry: ProjectionRegistry,
    quarantine: QuarantineHandler,
) -> dict:
    """
    Returns:
        review_results: list[dict]  — per-review process_review() results
        agg_signals: list[AggProductSignalRow]
        serving_products: list[dict]
        serving_users: list[dict]
        total_signals: int
        total_quarantined: int
    """
```
순서: process_review() × N → aggregate → user prefs → serving profiles

---

## Phase 7: 테스트 (13+1 신규)

| 테스트 | 검증 |
|--------|------|
| test_projection_registry.py | completeness vs observed combos + unmapped → QUARANTINE |
| test_signal_emitter.py | reverse(caused_by)→dst=Concern; product_linkage; merge 2×→1 signal |
| test_idempotency.py | 2× same review → 0 duplicate facts/signals |
| test_provenance_fidelity.py | signal→evidence→fact→provenance→raw snippet |
| test_truth_override_protection.py | has_ingredient→catalog_validation, product_master unchanged |
| test_reviewer_isolation.py | reviewer_proxy IRI ≠ user IRI, concept graph no merge |
| test_predicate_contracts.py | all 65 have contracts; invalid type combo → rejected by builder |
| test_signal_merge_policy.py | BEE+REL same semantic→1 fact + 2 provenance |
| test_window_backfill.py | event_time→window_ts→30d/90d/all correct |
| test_concept_link_integrity.py | user concept IRI ∩ product concept IRI → match |
| test_qualifier_quarantine.py | qualifier_required=Y + no qualifier → quarantine |
| test_freshness_propagation.py | review_count_30d → serving → scorer._freshness_score() |
| test_rel_ner_join.py | duplicate mention text → correct resolution via (start,end,text) key |
| ★ test_run_batch.py | full orchestration: raw→canonical→signal→agg→serving + counts verified |

| test_user_fact_contract.py | user fact predicate가 USER_PREFERENCE_EDGE_TYPES에 없으면 skip |
| test_invalid_fact_quarantine.py | review fact predicate contract 위반 → _invalid_facts + quarantine 호출 |
| test_event_time_propagation.py | created_at → event_time_utc(non-null) → window_ts → 30d/90d aggregate |
| test_product_linkage_transform.py | HAS_KEYWORD fact → product_linkage → signal dst=Keyword, anchor=Product |

**기존 테스트 수정**:
- test_end_to_end.py: concept IRI fixture + event_time + full L3/4 경로
- test_recommendation.py: concept IRI namespace fixture
