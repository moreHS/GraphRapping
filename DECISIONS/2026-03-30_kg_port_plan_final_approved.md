# Neo4j KG 파이프라인 이식 상세 계획

## Context
"시원해서"가 BEE_ATTR로 나오는 버그의 근본 원인은 GraphRapping이 raw 소스에서 직접 정규화하면서 BEE attr 이름과 entity type을 혼동하는 것.
Relation 프로젝트의 Neo4j 파이프라인(5단계)이 이미 이 작업을 올바르게 수행하므로, 핵심 로직을 이식.

## 핵심 제약사항 (탐색 결과)

### ~~제약 1: source JSON에 keywords 필드 없음~~ → 수정: keywords 필드 존재함!
`hab_rel_sample_ko_withPRD_listkeyword.json`의 **NER-BeE relation[].object에 keywords[] 필드가 존재**.

실측 (200건 리뷰):
- NER-BeE relation 680건 중 378건(56%)에 keywords 필드 있음
- 825개 keyword instances, 384개 unique keywords
- BEE row 자체에는 keywords 없음 (NER-BeE relation에서만 제공)
- keywords 없는 NER-BeE: phrase가 짧거나 키워드 추출 미완료 케이스

예시:
```
NER-BeE: {subj: "제품"/PRD, obj: "항상 애용하는 제품"/충성도, keywords: ["애용", "제품"]}
NER-BeE: {subj: "발림성"/CAT, obj: "가루 날림 거의 없고요"/가루날림, keywords: ["가루", "날림", "없"]}
```

→ **Neo4j 파이프라인의 `_process_keywords()` 로직이 그대로 작동 가능!**
→ keywords 없는 NER-BeE에 대해서만 auto fallback 필요
→ review_ingest.py에서 keywords 필드를 보존하도록 수정 필요 (현재 누락)

### 제약 2: ID 체계 불일치
- GraphRapping: `concept:{type}:{id}`, `product:{id}`, `fact:{md5}` (MD5 16자)
- Neo4j KG: `sha256(type::value)[:32]` (SHA256 32자)
→ **GraphRapping ID 체계 유지, KG 내부에서만 KG hash 사용**

### 제약 3: review_id 포맷 불일치
- GraphRapping: `review:{source}:{key}` (deterministic)
- Neo4j KG: `{product_id}_{review_idx}` (순차)
→ **GraphRapping review_id 유지, KG에 전달**

### 제약 4: product_id 생성 불일치
- GraphRapping: product_matcher가 외부 ID 매칭
- Neo4j KG: `P{xxxx}` 순차 생성
→ **GraphRapping product_matcher 유지, KG에 matched product_id 전달**

### 제약 5: provenance 손실
- Neo4j KG 산출물(entities.json, edges.json)은 aggregate 결과 → 개별 리뷰 추적 불가
- GraphRapping은 per-review provenance 필요 (fact_provenance, signal_evidence)
→ **KG 산출물 직접 사용 불가. KG 파이프라인을 per-review로 실행해야 함**

### 제약 6: 46/121 테스트 영향
bee_normalizer, relation_canonicalizer, tool_concern_segment_deriver를 직접 import하는 테스트:
test_bee_normalizer(9), test_end_to_end(2), test_integration_real_data(13), test_predicate_contracts(4) 등
→ **dual-run 방식: 기존 + KG 병렬 실행, parity 확인 후 전환**

## 이식 전략: Adapter-Led Refactoring Port

**원칙**:
1. public API 변경 없음 (process_review → ReviewPersistBundle 유지)
2. GraphRapping ID 체계 유지 (concept:*, product:*, fact:md5)
3. BEE_ATTR sentiment split은 내부에서만 (public dst_id는 기존 BEEAttr IRI)
4. per-review 처리 유지 (aggregate는 GraphRapping Layer 3에서)
5. dual-run → parity → switch → remove

## Phase 0: Contract Freeze + Source Field 보존

### 0-1. relation[].object에서 누락 field 보존
현재 누락된 핵심 필드: **`object.keywords[]`**
- `review_ingest.py:162-175`에서 REL row 생성 시 keywords 누락
- `relation_loader.py:95`에서도 keywords 누락
- 이 필드가 NER-BeE의 56%에 존재하며, KG keyword 추출의 핵심 소스

**수정**:
```python
# relation_loader.py _convert_record()에서:
relation.append({
    "subject": item.get("subject", {}),
    "object": item.get("object", {}),  # keywords 포함된 전체 object 보존
    ...
})

# review_ingest.py rel_row 생성에서:
rel_rows.append({
    ...
    "obj_keywords": obj.get("keywords", []),  # ★ 신규
})
```

기존 보존 확인:
- `obj_start`, `obj_end`: ✅ 이미 추가됨
- `obj_group` (BEE attr 이름): ✅ 이미 보존
- `source_type`: ✅ 이미 보존

### 0-2. NER-BeE 식별 계약
KG 파이프라인에서 NER-BeE relation을 올바르게 처리하려면:
- `source_type == "NER-BeE"` → `relation_type`을 `has_attribute`로 강제
- BEE entity의 sentiment는 entity에 보존, relation은 NEU
이 로직이 mention_extractor의 `_process_relation()`에 있음. 이식 시 유지.

### 0-3. ID 정책 확정
| 항목 | KG 내부 | GraphRapping 외부 |
|------|---------|-----------------|
| entity_id | sha256(type::value)[:32] | concept:{type}:{normalize(value)} |
| edge_id | sha256(subj::rel::obj::sentiment)[:32] | fact:{md5(review_id\|subj\|pred\|obj\|polarity)} |
| review_id | {product_id}_{idx} (KG 내부용) | review:{source}:{key} (GraphRapping) |
| product_id | 외부에서 전달받음 | product_matcher 결과 |

→ `src/kg/` 내부에서 KG ID 사용, 외부로 나갈 때 GraphRapping IRI로 변환하는 **adapter**가 필요

## Phase 1: 버그 즉시 수정 (KG 이식 전)

이식은 시간이 걸리므로, 현재 버그부터 먼저 수정:

1. **REL NER-BeE skip**: `source_type == "NER-BeE"` → continue (BEE row에서 처리)
2. **`_BEE_ATTR_NAMES` 제거**: `_canonical_type()`에서 BEE attr 이름 체크 삭제
3. **relation_canonical_map.json**: 39개 BEE attr→has_attribute 매핑 제거
4. **projection_registry.csv**: 스팟 수정 13개 엔트리 제거 (line 75-87)

## Phase 2: KG 핵심 모듈 이식

### 2-1. 데이터 모델 (`src/kg/models.py`)
Relation 프로젝트에서 이식:
- `EntityMention` (review_id, type, word, start, end, source, sentiment, original_type, is_placeholder)
- `RelationMention` (subj_mention_id, obj_mention_id, relation_type, sentiment, source_type)
- `KeywordMention` (word, bee_attr_type, bee_mention_id)
- `SameEntityPair` (subj_mention, obj_mention)

**적응점**:
- `review_id`는 GraphRapping 포맷 사용
- `product_id`는 product_matcher 결과 사용

### 2-2. Mention Extractor (`src/kg/mention_extractor.py`)
Relation 프로젝트에서 이식하되 적응:

**유지하는 로직**:
- NER mention 추출 + dedup (mention_index)
- BEE mention 추출 (sentiment 보존)
- Position index (O(1) lookup)
- NER-BeE relation → has_attribute 강제 + sentiment 분리
- same_entity pair 수집
- **keyword 추출 부분은 별도 처리 (source에 keywords 필드 없음)**

**적응하는 로직**:
- `review_id`: GraphRapping의 `make_review_id()` 결과를 받음 (자체 생성 안 함)
- `product_id`: 외부에서 전달 (product_matcher 결과)
- `brand mention`: 이미 review_ingest에서 처리 → KG에서 중복 안 함

**keyword 추출 전략** (source에 keywords 없으므로):
- BEE phrase에서 직접 keyword surface matching (기존 keyword_surface_map.yaml 재활용)
- 또는 BEE phrase 자체를 normalize하여 auto keyword 생성
- 이 부분은 Phase 2에서 설계, Phase 3에서 구현

### 2-3. Same Entity Merger (`src/kg/same_entity_merger.py`)
Relation 프로젝트에서 거의 그대로 이식:
- Union-Find (이미 GraphRapping placeholder_resolver에도 있음)
- Representative 선택 (placeholder 우선 → 최빈 word)

**적응점**:
- GraphRapping의 placeholder_resolver가 이미 하는 일과 중복
- **결정 필요**: KG merger를 사용하되 product_matcher 결과를 주입하여 placeholder→product 매핑

### 2-4. Canonicalizer (`src/kg/canonicalizer.py`)
가장 큰 이식 대상. 핵심 로직:

**유지**:
- BEE_ATTR sentiment split (밀착력_POS, 밀착력_NEG)
- KEYWORD entity 생성 + HAS_KEYWORD edge
- NER entity 정규화
- OFFICIAL_BRAND edge 자동 생성

**적응 (GraphRapping IRI로 변환)**:
```python
# KG 내부: entity_id = sha256("BEE_ATTR::밀착력_POS")[:32]
# GraphRapping 외부: concept_iri = concept:BEEAttr:bee_attr_adhesion

# Adapter: KG entity → GraphRapping CanonicalEntity
def to_graphrapping_entity(kg_entity: KGCanonicalEntity) -> CanonicalEntity:
    if kg_entity.type == "BEE_ATTR":
        # BEE_ATTR: 속성 이름만 (polarity는 fact에 보존)
        iri = make_concept_iri("BEEAttr", kg_entity.bee_type)
    elif kg_entity.type == "KEYWORD":
        iri = make_concept_iri("Keyword", kg_entity.normalized_value)
    elif kg_entity.type == "BRD":
        iri = make_concept_iri("Brand", kg_entity.normalized_value)
    # ... 타입별 매핑
    return CanonicalEntity(entity_iri=iri, ...)
```

**BEE_ATTR sentiment split 처리**:
KG 내부에서는 밀착력_POS, 밀착력_NEG가 별도 노드.
GraphRapping 외부에서는 concept:BEEAttr:밀착력 하나 + fact.polarity로 구분.
→ adapter에서 **폴라리티를 entity에서 fact로 이동**

### 2-5. KG Pipeline Orchestrator (`src/kg/kg_pipeline.py`)
```python
class KGPipeline:
    """Per-review KG construction (not corpus-level)."""

    def process_review(
        self,
        review_id: str,           # GraphRapping review_id
        product_id: str | None,   # product_matcher 결과
        ner_rows: list[dict],     # ingest 결과
        bee_rows: list[dict],
        rel_rows: list[dict],
        brand_name: str,
    ) -> KGResult:
        # 1. MentionExtractor (per-review)
        # 2. SameEntityMerger (per-review)
        # 3. Canonicalizer (per-review)
        # → KGResult (entities, edges, keyword_mentions)
```

**핵심 차이**: Relation 프로젝트는 전체 corpus를 한번에 처리하지만, GraphRapping은 **per-review** 처리.
→ MentionExtractor/Canonicalizer를 per-review 단위로 호출하도록 적응.
→ Aggregator는 사용하지 않음 (GraphRapping Layer 3에서 처리)

## Phase 3: GraphRapping 파이프라인 연결

### 3-1. KG → CanonicalFact 변환 adapter (`src/kg/adapter.py`)
```python
def kg_result_to_facts(
    kg_result: KGResult,
    review_id: str,
    target_product_iri: str,
    builder: CanonicalFactBuilder,
) -> None:
    """KG 산출물을 GraphRapping CanonicalFact로 변환."""

    # 1. Entity 등록
    for kg_entity in kg_result.entities:
        gr_entity = to_graphrapping_entity(kg_entity)
        builder.register_entity(gr_entity)

    # 2. BEE_ATTR facts (has_attribute edges)
    for edge in kg_result.edges:
        if edge.relation_type == "HAS_ATTRIBUTE":
            builder.add_fact(
                review_id=review_id,
                subject_iri=...,  # product_iri
                predicate="has_attribute",
                object_iri=...,   # BEEAttr concept IRI
                object_type="BEEAttr",
                polarity=edge.sentiment,  # KG에서 가져온 polarity
                source_modality="BEE",
            )

    # 3. HAS_KEYWORD facts
    for edge in kg_result.edges:
        if edge.relation_type == "HAS_KEYWORD":
            builder.add_fact(
                review_id=review_id,
                subject_iri=...,  # BEEAttr IRI
                predicate="HAS_KEYWORD",
                object_iri=...,   # Keyword concept IRI
                object_type="Keyword",
                source_modality="BEE",
            )

    # 4. 기타 REL facts (NER-NER only, NER-BeE는 위에서 처리)
    for edge in kg_result.edges:
        if edge.relation_type not in ("HAS_ATTRIBUTE", "HAS_KEYWORD", "OFFICIAL_BRAND"):
            builder.add_fact(
                review_id=review_id,
                subject_iri=...,
                predicate=edge.relation_type.lower(),
                object_iri=...,
                subject_type=...,   # KG entity type → GraphRapping canonical type
                object_type=...,
                polarity=edge.sentiment,
                source_modality="REL",
            )
```

### 3-2. run_daily_pipeline.py 수정
```python
# 기존 (Lines 131-279): bee_normalizer + relation_canonicalizer + _canonical_type
# 변경: KG pipeline 호출

from src.kg.kg_pipeline import KGPipeline
from src.kg.adapter import kg_result_to_facts

# process_review() 내부:
kg = KGPipeline(config)
kg_result = kg.process_review(
    review_id=ingested.review_id,
    product_id=target_product_id,
    ner_rows=ingested.ner_rows,
    bee_rows=ingested.bee_rows,
    rel_rows=ingested.rel_rows,
    brand_name=record.brnd_nm,
)
kg_result_to_facts(kg_result, ingested.review_id, target_product_iri, builder)
```

Lines 131-279 전체를 위 3줄로 대체.

### 3-3. Keyword 처리 전략 (수정: keywords 필드 존재!)

**실제 데이터 구조**:
- NER-BeE relation의 56%에 `keywords[]` 필드 존재 (이미 추출됨)
- Neo4j 파이프라인의 `_process_keywords()`가 이 필드를 직접 사용
- BEE row에는 keywords 없음 → NER-BeE relation에서만 keyword 소스

**전략: KG mention_extractor가 NER-BeE keywords를 그대로 사용**:
1. NER-BeE relation에서 `object.keywords[]` 읽기 → KeywordMention 생성
2. Canonicalizer가 KEYWORD entity + HAS_KEYWORD edge 생성
3. keywords 없는 NER-BeE (44%) → auto fallback (phrase normalize)

**필수 수정: review_ingest.py에서 keywords 보존**:
현재 `relation_loader.py:95`와 `review_ingest.py:162`에서 `object.keywords`가 **누락됨**.
REL row에 `obj_keywords` 필드 추가:
```python
# review_ingest.py rel_row 생성 시:
rel_rows.append({
    ...
    "obj_keywords": obj.get("keywords", []),  # ★ 추가
})
```

이렇게 하면:
- KG mention_extractor가 `rel_row["obj_keywords"]`에서 keyword 추출
- 56%는 source keyword 사용 (품질 높음)
- 44%는 auto fallback (phrase normalize)
- keyword_surface_map.yaml 의존도 제거 (legacy/shadow 모드에서는 유지)

### 3-4. BEE ↔ NER-BeE join/fallback 규칙

BEE row에는 keywords 없음, NER-BeE relation에만 존재 → join 필요:
**Join key**: `(review_id, obj_word, obj_group)` 또는 `(review_id, obj_start, obj_end)`
**규칙**:
- NER-BeE에 keywords 있으면 → `keyword_origin=source` (56%)
- NER-BeE에 keywords 없으면 → phrase normalize → auto keyword, `keyword_origin=fallback` (44%)
- 매칭 NER-BeE 없으면 → auto fallback

### 3-5. Adapter 계약 (frozen)

```python
@dataclass
class KGResult:
    entities: list[KGEntity]     # BEE_ATTR, KEYWORD, PRD, BRD, etc.
    edges: list[KGEdge]          # HAS_ATTRIBUTE, HAS_KEYWORD, etc.

# Adapter 변환 규칙:
# KGEntity → CanonicalEntity (BEE_ATTR→concept:BEEAttr:{bee_type}, polarity→fact로 이동)
# HAS_ATTRIBUTE edge → CanonicalFact(predicate=has_attribute, obj_type=BEEAttr, modality=BEE)
# HAS_KEYWORD edge → CanonicalFact(predicate=HAS_KEYWORD, obj_type=Keyword, modality=BEE)
# NER-NER edge → CanonicalFact(predicate=lower(rel_type), modality=REL)
# OFFICIAL_BRAND → skip (product_ingest에서 처리)
# provenance: FactProvenance(raw_table=bee_raw|rel_raw, raw_row_id=mention_idx)
```

### 3-6. Shadow mode (3단계 flag)

`GRAPHRAPPING_KG_MODE=off|shadow|on`
- **off**: 기존 경로 (기본)
- **shadow**: 양쪽 실행 + delta 로깅 (기존=production, KG=비교)
- **on**: KG 경로만
- 전환 기준: signal ±10%, quarantine 감소, KEYWORD 증가

### 3-7. 영향 테스트 목록

직접 영향: `test_bee_normalizer(9)`, `test_end_to_end(2)`, `test_integration_real_data(13)`, `test_predicate_contracts(4)`, `test_loaders(12)`
간접 영향: `test_signal_emitter(10)` — 하류 호환성 검증
Golden fixtures 필요: 8-12건 (BEE-only, NER-BeE+keywords, same_entity, DATE, unknown_rel, product_miss, negation, multi-keyword)

## Phase 4: Dual-Run + Parity 검증

### 4-1. feature flag
```python
USE_KG_PIPELINE = os.environ.get("GRAPHRAPPING_USE_KG", "false") == "true"

# process_review() 내부:
if USE_KG_PIPELINE:
    # Phase 2-3 경로 (KG pipeline)
else:
    # 기존 경로 (bee_normalizer + relation_canonicalizer)
```

### 4-2. parity 테스트
- 같은 100건 리뷰에 대해 양쪽 실행
- 비교: entity 수, fact 수, signal 수, signal family 분포
- BEE_ATTR signal이 같은 dst_id를 가지는지
- "시원해서" 같은 phrase가 KEYWORD로 나오는지

### 4-3. 전환 판단 기준
- parity 테스트 통과 (signal 분포 ±10% 이내)
- "시원해서" 버그 해결 확인
- KEYWORD signal 수 증가 확인
- quarantine_projection_miss 감소 확인

## Phase 5: Legacy 모듈 제거

parity 확인 후:
- `src/normalize/bee_normalizer.py` → deprecated (test_bee_normalizer도 deprecated)
- `src/normalize/relation_canonicalizer.py` → CANONICAL_PREDICATES constant는 유지
- `src/normalize/keyword_normalizer.py` → 제거
- `src/normalize/tool_concern_segment_deriver.py` → KG config로 대체
- `_canonical_type()`, `_BEE_ATTR_NAMES` → 제거

## 이슈 체크리스트

| 이슈 | 영향도 | 해결 방안 |
|------|--------|---------|
| ID 체계 충돌 (MD5 vs SHA256) | 높음 | KG 내부=SHA256, 외부=GraphRapping IRI. adapter에서 변환 |
| BEE_ATTR sentiment split 노출 | 높음 | KG 내부에서만 split, 외부로는 기존 BEEAttr IRI + polarity 분리 |
| keywords 필드 없음 | 높음 | Option B+C: 사전 확장 + auto fallback |
| per-review vs corpus-level | 중간 | KG pipeline을 per-review 단위로 호출 |
| 46 tests 영향 | 중간 | dual-run + feature flag로 점진적 전환 |
| provenance 유지 | 중간 | KG에서 mention-level 정보 보존 → FactProvenance 생성 |
| placeholder 처리 중복 | 낮음 | KG same_entity_merger 사용, product_matcher 결과 주입 |
| Aggregator 불필요 | 낮음 | 이식 안 함 (GraphRapping Layer 3에서 처리) |
| config 통합 | 낮음 | KG config(entity_types, relation_types)를 configs/에 추가 |

## 파일 구조

```
src/kg/                        # 신규 패키지
  __init__.py
  models.py                    # EntityMention, RelationMention, KeywordMention 등
  mention_extractor.py         # 5단계 중 1단계
  same_entity_merger.py        # 5단계 중 2단계
  canonicalizer.py             # 5단계 중 3단계
  kg_pipeline.py               # per-review 오케스트레이션
  adapter.py                   # KG → GraphRapping CanonicalFact 변환
  config.py                    # entity_types + relation_types 로더
configs/
  kg_entity_types.json         # Relation 프로젝트에서 복사
  kg_relation_types.json       # Relation 프로젝트에서 복사
```

## 검증

- [ ] "시원해서"가 KEYWORD 노드로 나옴 (BEE_ATTR 아님)
- [ ] BEE_ATTR 노드: 밀착력, 발림성 등 (속성 축만)
- [ ] HAS_KEYWORD edge: BEE_ATTR → KEYWORD 정상 생성
- [ ] same_entity merge: Union-Find 동작
- [ ] NER-NER relation: CONTEXT, CONCERN, COMPARISON signal 정상
- [ ] NER-BeE relation: has_attribute + NEU (KG가 처리)
- [ ] OFFICIAL_BRAND edge 자동 생성
- [ ] dual-run parity: entity/fact/signal 수 ±10%
- [ ] 기존 121 tests 유지 (feature flag OFF 시)
- [ ] KG feature flag ON 시 demo UI 그래프 개선
