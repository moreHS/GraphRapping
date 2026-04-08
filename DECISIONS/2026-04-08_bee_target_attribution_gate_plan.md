# BEE Target Attribution Gate — 상세 구현 계획

## 원본 지시서
`PLAN/260408/review_target_attribution_reframe_instruction_ko.md`

## Context — 왜 이 재정립이 필요한가

지시서 핵심 전제:
> BEE는 먼저 `리뷰 타깃 제품에 대한 평가/감상인가`를 판정하는 대상이다.
> relation이 연결되지 않은 BEE는 기본적으로 타깃 제품에 대한 언급이 아니라고 본다.
> 연결되지 않은 BEE에서 파생한 attr/keyword/concern/context signal을 상품 추천용 KG에 승격시키면 안 된다.

### 변경 원칙 (지시서 §구조적 원칙)
1. **Relation의 1차 역할은 semantic이 아니라 attribution** — "이 BEE가 누구에 대한 말인가"를 확정하는 근거
2. **BEE signal 승격은 relation-gated** — target-linked 판정을 통과해야만 Layer 2/2.5/3으로 올림
3. **Unlinked BEE는 폐기 금지, evidence-only로 격하** — dictionary growth, model 개선, false negative audit용
4. **BEE_ATTR + KEYWORD 둘 다 target-linked일 때만 승격** — unlinked BEE에서 keyword만 뽑아 올리는 것도 금지
5. **Concern/Context는 explicit relation 중심** — BEE phrase 단독으로 concern signal 생성 금지

### 레이어별 역할 (지시서 §레이어별 역할 재정의)
- **Layer 1 (Raw/Evidence)**: BEE가 target-linked인지 아직 확정 안 되어도 저장
- **Layer 2 (Canonical Fact)**: 타깃 귀속 확인된 사실만 canonical fact로 올림
- **Layer 2.5 (Wrapped Signal)**: target-linked canonical fact만 projection
- **Layer 3 (Serving)**: target-linked + corpus-promoted 둘 다 만족해야 함

### 현재 문제
- BEE_SYNTHETIC(relation 없는 BEE)은 EVIDENCE_ONLY로 격하됨 → 이건 OK
- 하지만 NER-BEE relation이 있어도 **다른 제품에 대한 BEE**가 타깃 제품 시그널로 승격될 수 있음
- Legacy path(kg_mode="off")는 BEE를 무조건 `add_bee_facts()`로 올림 → attribution gate 없음
- Concern/Context는 현재 REL 경로에서만 생성 (BEE-only 파생은 없음) → 방어 guard 필요

### 구현 범위 (지시서 §구체 작업 묶음 5개)
1. **작업 1**: BEE attribution 상태 모델 추가 — target_linked, attribution_source, attribution_confidence
2. **작업 2**: Signal emission gate 수정 — unlinked BEE → signal 차단
3. **작업 3**: Unlinked BEE evidence 보관/QA 경로
4. **작업 4**: Concern/Context 생성 규칙 축소 — BEE-only derived 금지 guard
5. **작업 5**: Texture 포함 BEE hierarchy 유지 + attribution gate 적용

---

## 핵심 설계 결정

### Q1: target_linked를 어디에 설정하나?
- **bee_rows dict에 최초 설정** (process_review에서 placeholder resolution 직후)
- EntityMention, RelationMention, KGEdge, CanonicalFact에 전파
- WrappedSignal에는 추가하지 않음 (emitted = linked by definition)

### Q2: Legacy path?
- 동일한 attribution helper로 bee_rows를 먼저 enrichment
- `bee_row["target_linked"]`가 True인 경우만 `add_bee_facts()` 호출

### Q3: BEE_SYNTHETIC → EVIDENCE_ONLY만으로 충분한가?
- 불충분. 다른 제품에 anchored된 NER-BEE는 잡지 못함
- adapter에서 추가 gate + emitter에서 defense-in-depth 필요

### Q4: Concern 파생은 현재 BEE에서 발생하는가? (지시서 §작업4)
- 아니오. ToolConcernSegmentDeriver는 REL 경로에서만 호출 (run_daily_pipeline.py:311)
- 지시서 명시: "BEE-only derived concern/context 규칙 제거 또는 비활성화"
- 현재 경로가 없으므로 **향후 추가를 막는 explicit guard** 필요:
  - run_daily_pipeline.py의 BEE 처리 블록에 assertion/주석 guard 추가
  - "BEE phrase 단독으로 concern/context signal 생성 금지" 명시
  - concern 생성 허용 조건: explicit relation (`addresses`, `used_on`, `benefits`, `causes` 등)만

### Q5: Unlinked BEE 저장?
- bee_raw 테이블에 nullable 컬럼 3개 추가 (target_linked, attribution_source, attribution_context)
- 별도 quarantine 테이블 불필요

### Q6: 비교 리뷰 처리?
- comparison_with edge에서 target side만 증명 가능할 때 linked
- 비교 문구 자체에서 target side 추론 금지

### Q7: 간접 타깃 언급 ("이 크림 촉촉해요")?
- 한국어 지시 대명사 패턴 추가 (이거, 이 제품, 이 크림 등)
- 원칭(그거, 저거)은 unlinked 유지

---

## 파일별 구현 계획

### Phase 1: 기반 모델 + Attribution Helper

#### `src/common/enums.py`
- `AttributionSource` enum 추가: `direct_rel`, `placeholder_resolved`, `same_entity_resolved`, `comparison_resolved`, `unlinked`
- Priority 상수 추가 (merge 시 사용)

#### `src/link/bee_attribution.py` (신규) — 지시서 §코드/구현 방향 A
- `BeeAttribution` dataclass:
  - `target_linked: bool`
  - `attribution_source: AttributionSource` (direct_rel | placeholder_resolved | same_entity_resolved | comparison_resolved | unlinked)
  - `attribution_confidence: float` (지시서에서 명시한 필드)
  - `matched_rel_idx: int | None`
  - `match_strategy: str` (offset_match | text_match | synthetic)
  - `subject_text: str`
  - `subject_resolution_type: str`
  - `reason: str`
- `attribute_bee_rows(bee_rows, rel_rows, ner_rows, target_product_iri, review_product_name)` → list[BeeAttribution]
- 지시서 §원칙 2 기준 target-linked 판정:
  1. Review Target / target Product mention과 직접 relation 연결 → `direct_rel`
  2. same_entity / placeholder resolution 후 target product cluster에 귀속 → `placeholder_resolved` / `same_entity_resolved`
  3. 명시적 비교 구조에서 current target side로 판정 → `comparison_resolved`
  4. 그 외 → `unlinked`
- Matching rules:
  1. exact (obj_start, obj_end, obj_text, bee_attr_raw) match to NER-BEE relations
  2. fallback: normalized text + attribute (offset 없을 때만)
  3. 절대 fuzzy match 금지
- 보수적 ambiguity rule: 같은 BEE row가 여러 PRD subject와 match + 일부가 target이 아니면 → unlinked

#### `src/kg/models.py`
- EntityMention: +target_linked, +attribution_source
- RelationMention: +target_linked, +attribution_source
- KGEntity: +target_linked (summary mirror)
- KGEdge: +target_linked, +attribution_source

### Phase 2: KG Pipeline 전파

#### `src/kg/mention_extractor.py`
- extract(): enriched bee_rows에서 attribution 전달
- _create_or_get_mention(): +target_linked, +attribution_source params
- synthetic BEE block (L82-115): 명시적 target_linked=False
- _process_keywords(): 부모 BEE attribution 복사

#### `src/kg/canonicalizer.py`
- process(): RelationMention → KGEdge 변환 시 attribution 복사
- _create_edge(): target_linked를 BEE edge hash key에 포함 (linked/unlinked collapse 방지)
- keyword edge: 부모 BEE attribution 상속

#### `src/kg/adapter.py`
- _classify_promotion(): BEE edge + target_linked=False → KEEP_EVIDENCE_ONLY
- kg_result_to_facts(): unlinked BEE edge → fact 생성 안 함 (stats["blocked_unlinked"] 카운트)
- linked BEE edge → CanonicalFact에 target_linked/attribution_source 전달

### Phase 3: Legacy Path + Emitter Gate

#### `src/jobs/run_daily_pipeline.py`
- placeholder resolution 직후: `attribute_bee_rows()` 호출
- KG branch: enriched bee_rows 전달
- Legacy branch (L208): `bee_row["target_linked"]` 확인 후 add_bee_facts() 조건 호출
- bundle_to_result_dict(): +bee_total, +bee_target_linked_count, +bee_unlinked_count

#### `src/canonical/canonical_fact_builder.py`
- CanonicalFact: +target_linked, +attribution_source
- add_fact(): +target_linked, +attribution_source params
- add_bee_facts(): attribution 전파

#### `src/wrap/signal_emitter.py`
- emit_from_fact(): BEE fact + target_linked=False → emission 차단 (defense-in-depth)
- EmitResult: +attribution_blocked_facts

### Phase 4: Concern/Context Guard + Storage + Viewer (지시서 §작업3,4,E)

#### `src/jobs/run_daily_pipeline.py` — 지시서 §작업4, §D
- BEE 처리 블록 (legacy + KG)에 explicit guard 추가:
  ```python
  # GUARD: BEE phrase 단독으로 concern/context signal 생성 금지
  # Concern/Context는 explicit relation (addresses, used_on, benefits, causes 등)
  # 에서만 생성한다. (지시서 §원칙5)
  ```
- ToolConcernSegmentDeriver 호출부 (L311)에도 주석 강화
- bundle_to_result_dict(): BEE attribution 통계 추가

#### `sql/ddl_raw.sql` — 지시서 §작업3
- bee_raw 테이블: +target_linked boolean, +attribution_source text, +attribution_context jsonb
- ALTER TABLE ... ADD COLUMN IF NOT EXISTS
- Index: `idx_bee_unlinked ON bee_raw(target_linked, attribution_source)`

#### `src/db/repos/review_repo.py`
- batch_insert_bee_raw(): 새 컬럼 write

#### `src/web/server.py` — 지시서 §E (review KG viewer 역할 재정의)
- graph API 응답에 BEE node의 `target_linked` 상태 포함
- 프론트에서 linked/unlinked BEE를 시각적으로 구분 가능하게
  - linked BEE: 기존 색상 유지
  - unlinked BEE: 회색/점선 등으로 구분 (evidence-only 표시)

#### `src/static/app.js`
- graph viewer에서 unlinked BEE node에 다른 스타일 적용

#### `src/qa/dictionary_growth.py` (신규 또는 확장) — 지시서 §작업3
- `get_recent_unlinked_bee()`: bee_raw WHERE target_linked=false 조회
- dictionary growth, model 개선, false negative audit용 인터페이스

---

## 테스트 계획

### 지시서 §테스트/검증 시나리오 1:1 매핑

| 지시서 시나리오 | 테스트 파일 | 기대 결과 |
|---|---|---|
| **시나리오 1**: target-linked BEE ("촉촉하고 흡수 빨라요" + Product A relation) | test_bee_target_attribution_gate.py | BEE_ATTR + KEYWORD signal 둘 다 생성 |
| **시나리오 2**: 타제품 BEE (타깃 A 리뷰에서 제품 B 발림성) | test_bee_target_attribution_gate.py | A 기준 target_linked=false, signal 미승격, evidence 보관 |
| **시나리오 3**: relation 없는 BEE | test_bee_attribution.py | unlinked, signal 미생성, quarantine/evidence 저장 |
| **시나리오 4**: explicit concern relation (addresses dryness) | test_bee_target_attribution_gate.py | concern signal 생성 (explicit evidence이므로 OK) |
| **시나리오 5**: texture hierarchy (젤 타입 + target relation) | test_bee_target_attribution_gate.py | BEE_ATTR(Texture) + KEYWORD(GelLike) 둘 다 생성 |

### `tests/test_bee_attribution.py` (신규) — 단위 테스트
- direct_rel via Review Target
- direct_rel via 제품명 exact match
- placeholder_resolved via "이 크림"
- same_entity_resolved
- comparison_resolved (target side)
- unlinked (no anchor)
- unlinked (anchor → 다른 제품) ← **시나리오 2**
- ambiguous multi-anchor → unlinked
- attribution_confidence 값 검증

### `tests/test_bee_target_attribution_gate.py` (신규) — 통합 테스트
- process_review kg_mode="on":
  - **시나리오 1**: target-linked BEE → attr + keyword signal ✓
  - **시나리오 2**: 타제품 BEE → signal 미생성, evidence 보관 ✓
  - **시나리오 3**: relation 없는 BEE → unlinked, signal 없음 ✓
  - **시나리오 4**: explicit concern relation → concern signal ✓
  - **시나리오 5**: texture linked → attr + keyword ✓, unlinked texture → 둘 다 없음
- process_review kg_mode="off" (legacy):
  - 동일 시나리오 반복 (legacy path도 gate 적용 확인)

### 기존 테스트 수정
- test_signal_emitter.py: target_linked=False BEE fact → no signal
- test_phase1_semantic_preservation.py: adapter level unlinked block
- test_end_to_end.py: BEE-only review → concern/context signal 미생성 확인

### 지시서 §가드레일 검증 (핵심 불변식)
1. BEE 있다고 자동 상품 signal 승격 안 됨 → unlinked BEE signal 카운트 = 0
2. Relation 없는 BEE → concern/context 파생 안 됨
3. Unlinked BEE 삭제 안 됨 → bee_raw에 항상 존재
4. BEE_ATTR + KEYWORD 계층 유지 → linked texture = attr + keyword 동시 생성
5. Serving = target-linked + promoted만 → 최종 serving profile에 unlinked signal 없음

---

## 구현 순서

1. **enums + bee_attribution.py** (독립)
2. **kg/models.py** 필드 추가 (독립)
3. **mention_extractor + canonicalizer** 전파 (1,2 의존)
4. **adapter + canonical_fact_builder** gate (3 의존)
5. **signal_emitter** defense (4 의존)
6. **run_daily_pipeline** 통합 (1-5 의존)
7. **SQL DDL + repo** 저장 (독립)
8. **테스트** (6,7 이후)

---

## 수정 파일 요약

| 파일 | 변경 | Phase |
|------|------|-------|
| `src/common/enums.py` | +AttributionSource enum | 1 |
| `src/link/bee_attribution.py` | 신규: attribution helper | 1 |
| `src/kg/models.py` | +target_linked 필드 4개 dataclass | 1 |
| `src/kg/mention_extractor.py` | attribution 전파 | 2 |
| `src/kg/canonicalizer.py` | edge attribution + hash key | 2 |
| `src/kg/adapter.py` | BEE gate in _classify_promotion | 2 |
| `src/canonical/canonical_fact_builder.py` | +attribution params | 2 |
| `src/wrap/signal_emitter.py` | defense-in-depth gate | 3 |
| `src/jobs/run_daily_pipeline.py` | attribution enrichment + legacy gate | 3 |
| `sql/ddl_raw.sql` | bee_raw +3 columns | 4 |
| `src/db/repos/review_repo.py` | write attribution | 4 |
| `tests/test_bee_attribution.py` | 신규 unit tests | 5 |
| `tests/test_bee_target_attribution_gate.py` | 신규 integration tests | 5 |

---

## 검증

```bash
python -m pytest tests/ -v
```
- 기존 267 테스트 전부 통과
- 새 attribution 테스트 전부 통과
- 파이프라인 실행 후 BEE_ATTR signal 수 변화 확인 (unlinked 제거)

### 프론트 검증
```bash
curl -s -X POST http://localhost:8000/api/pipeline/run -d '{}'
```
- Dashboard: BEE_ATTR / BEE_KEYWORD signal 수가 이전보다 줄어듦 (unlinked 제거)
- Graph viewer: BEE node에 linked/unlinked 구분 표시
- Recommendation: 남은 signal은 target-linked + promoted만

### 지시서 §최종 메시지 확인
> 이번 단계는 recall을 무작정 늘리는 단계가 아니라,
> **타깃 귀속 정확도를 높여 전역 KG와 추천 신호의 정밀도를 올리는 단계**다.

→ signal 수가 줄더라도, 남은 signal의 precision이 높아야 성공
