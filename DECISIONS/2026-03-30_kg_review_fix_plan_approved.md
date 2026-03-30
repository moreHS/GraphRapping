# KG 이식 크로스 리뷰 수정 — 구현 계획 (16개 이슈)

## 구현 순서 (의존성 기반, 7단계)

### Step 1: run_daily_pipeline.py 구조 수정 (P0-1, P0-3, P1-7)
3개 이슈를 한번에 해결:
- P0-1: `on` 모드에서 legacy BEE+REL 완전 skip, `shadow`에서 별도 builder
- P0-3: KG_MODE를 import-time 바인딩 → 함수 파라미터로 전달
- P1-7: KGPipeline을 리뷰 루프 밖에서 1회만 생성

수정 위치: `src/jobs/run_daily_pipeline.py:125-185`
```python
# process_review()에 kg_mode 파라미터 추가 (default="off")
# kg_mode == "on": KG 경로만, legacy BEE+REL 완전 skip
# kg_mode == "shadow": 별도 builder로 KG (shadow_builder), legacy builder는 production
#   → shadow_builder 결과는 로그만, bundle에 포함 안 함
# kg_mode == "off": legacy만
# KGPipeline 인스턴스는 외부에서 주입 (optional param, default=None → 생성)
```
**caller 전파**: `run_batch()`, `run_incremental_pipeline.py`에서도 `kg_mode` 전달
**import-time 전역 변수** `KG_MODE` 제거 → 함수 파라미터로만 전달
**shadow bundle 격리**: shadow_builder의 facts/signals는 절대 ReviewPersistBundle에 포함 안 됨

### Step 2: 로깅 추가 (P0-2) — src/kg/ + run_daily_pipeline 포함
8개 silent drop 지점 + module logger:
- `src/kg/mention_extractor.py`: no_relationship skip, unknown relation
- `src/kg/canonicalizer.py`: unmapped relation mention, unmapped keyword mention, OFFICIAL_BRAND skip
- `src/kg/adapter.py`: unmapped IRI edge, OFFICIAL_BRAND skip
- **`src/jobs/run_daily_pipeline.py`**: no target_product KG skip (scope를 src/kg/만으로 제한하지 않음)

### Step 3: canonicalizer 버그 수정 (P1-2, P1-6, P1-8)
3개 이슈를 한번에:
- P1-6: representative 이중 처리 guard 수정 (`if mention.mention_id in self._mention_to_entity: continue`)
- P1-2: BEE_ATTR polarity None → "NEU" (not "POS")
- P1-8: NER-NER absent sentiment → "NEU" (mention_extractor.py:209)

수정 위치:
- `src/kg/canonicalizer.py:54` (guard 수정)
- `src/kg/canonicalizer.py:125` (`"POS"` → `"NEU"`)
- `src/kg/mention_extractor.py:209` (`"POS"` → `"NEU"`)

### Step 4: adapter 수정 (P1-3, P1-4)
- P1-3: HAS_KEYWORD fact에 parent BEE_ATTR polarity 전달
- P1-4: placeholder IRI → reviewer_proxy_iri / target_product_iri 사용
  - `kg_result_to_facts()` 시그니처에 `reviewer_proxy_iri` 추가
  - placeholder_iri_map 구축
  - `to_graphrapping_iri()`에서 placeholder 분기 제거
  - `run_daily_pipeline.py`에서 호출 시 `reviewer_proxy_iri=ingested.reviewer_proxy_id` 전달

수정 위치: `src/kg/adapter.py`, `src/jobs/run_daily_pipeline.py:140`

### Step 5: BEE↔NER-BeE join fallback (P2-2) — Step 6 전에 필수!
P2-2를 Step 5로 올림: synthetic BEE-only 생성(Step 6)이 join fallback에 의존.
- mention_extractor에서 position index 실패 시 `(review_id, word, entity_group)` fallback

수정 위치: `src/kg/mention_extractor.py:247`

### Step 6: BEE-only synthetic + drop counter (P1-1, P1-5)
- P1-1: BEE-only 리뷰용 synthetic HAS_ATTRIBUTE + review_target 생성 + auto keyword
  (Step 5의 join fallback 이후에 실행해야 NER-BeE 매칭을 먼저 시도)
- P1-5: KGResult에 drop counter 필드 추가 (5 counters + total_mentions + total_raw_relations)
  8개 drop 지점에서 counter 증가

수정 위치: `src/kg/mention_extractor.py`, `src/kg/models.py`, `src/kg/canonicalizer.py`

### Step 7: P2 나머지 마이너 수정
- P2-1: `config.py:15` `혼합` → `MIXED`
- P2-3: KGConfig load 후 validation (bee_types 비어있으면 warning)

수정 위치: `src/kg/config.py`, `src/kg/mention_extractor.py`

### Step 7: 테스트 + parity 재검증
- 기존 121 tests 유지 확인
- KG_MODE=on parity 재측정
- BEE_ATTR에 "시원해서" 같은 phrase 없음 확인
- BEE_KEYWORD 수 이전(257)과 유사하거나 증가
- quarantine 이전(701)보다 감소

## 파일별 수정 요약

| 파일 | Step | 수정 내용 |
|------|------|----------|
| `src/jobs/run_daily_pipeline.py` | 1,2,4 | shadow 격리, KG_MODE 파라미터화, KGPipeline 외부 생성, logging, reviewer_proxy 전달 |
| `src/kg/mention_extractor.py` | 2,3,5,6 | logging, NER-NER NEU, BEE-only synthetic, join fallback |
| `src/kg/canonicalizer.py` | 2,3,5 | logging, guard 수정, polarity NEU, drop counter |
| `src/kg/adapter.py` | 2,4 | logging, HAS_KEYWORD polarity, placeholder IRI, reviewer_proxy |
| `src/kg/models.py` | 5 | KGResult drop counter 필드 |
| `src/kg/config.py` | 6 | 혼합→MIXED, load validation |
| `src/kg/kg_pipeline.py` | 1 | config 캐싱 지원 |

## 검증 체크리스트 (Step 7)

- [ ] 121 tests passed (KG_MODE off)
- [ ] `on` 모드: legacy BEE+REL 실행 안 됨 (중복 fact 0)
- [ ] `shadow` 모드: 별도 builder (production 오염 없음)
- [ ] 8개 drop 지점 logging 확인
- [ ] BEE_ATTR polarity: None 없음 (전부 POS/NEG/NEU)
- [ ] NER-NER sentiment: 기본값 NEU
- [ ] representative 이중 처리: weight 정확
- [ ] placeholder IRI: reviewer_proxy:* / product:* 포맷
- [ ] HAS_KEYWORD fact에 polarity 전달
- [ ] BEE-only 리뷰: HAS_ATTRIBUTE + KEYWORD 생성
- [ ] KGPipeline config 1회만 로드
- [ ] `혼합` → MIXED 매핑
- [ ] KGResult drop counter 채워짐
- [ ] parity: BEE_KEYWORD ≥ 250, quarantine < 700
