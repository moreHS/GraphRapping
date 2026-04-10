# Concern/Context 연결 강화 — 상세 구현 계획

## Context

추천 매칭에서 concern/goal 축이 완전히 작동하지 않음. 3가지 근본 원인:

1. **Concern ID 불일치** — 유저 `concept:Concern:건조함` vs 상품 `concern_dryness`
2. **Goal 값 불일치** — 유저 `concept:Goal:보습강화` vs 상품 `concept:Goal:보습`
3. **BEE_ATTR↔Concern bridge 없음** — 상품 `보습력` BEE_ATTR와 유저 `건조함` concern 미연결

## 설계 원칙

- concern_dict.yaml 포맷 변경 없음
- projection_registry.csv 변경 없음
- BEE_ATTR→Concern은 **virtual signal이 아닌 candidate overlap 시 discounted bridge**
- Config-driven (새 매핑 파일)
- Backward compatible (comparison-time normalization으로 old/new 공존)

---

## Phase 1: Concept Resolver + Config 추가

### `src/common/concept_resolver.py` (신규)
Concern/Goal ID를 canonical stable key로 정규화하는 공유 레이어.

```python
def resolve_concern_id(value: str) -> str:
    """concept:Concern:건조함 → concern_dryness, 또는 concern_dryness → concern_dryness"""
    # 1. concept:Concern: prefix 제거
    # 2. concern_dict에서 lookup → concept_id 반환
    # 3. 이미 concern_* 형태면 그대로
    # 4. 없으면 normalize_text() fallback

def resolve_goal_id(value: str) -> str:
    """concept:Goal:보습강화 → 보습 (alias map 기반)"""
    # 1. concept:Goal: prefix 제거
    # 2. goal_alias_map에서 canonical 반환
    # 3. 없으면 normalize_text() fallback
```

### `configs/goal_alias_map.yaml` (신규)
```yaml
# Goal alias → canonical goal ID
보습: 보습
보습강화: 보습
수분보충: 보습
수분: 보습
톤업: 톤업
밝기개선: 톤업
브라이트닝: 톤업
주름개선: 주름개선
안티에이징: 주름개선
탄력개선: 탄력
탄력: 탄력
진정: 진정
수딩: 진정
피부장벽: 피부장벽보호
피부장벽보호: 피부장벽보호
미백: 미백
항산화: 항산화
```

### `configs/concern_bee_attr_map.yaml` (신규)
BEE_ATTR → Concern bridge 매핑. 보수적 시작.
```yaml
# BEE_ATTR canonical ID → concern canonical ID + weight
bee_attr_moisturizing_power:
  concern_id: concern_dryness
  weight: 0.8
  label: "보습력 → 건조함 대응"
bee_attr_side_effect:
  concern_id: concern_irritation
  weight: 0.6
  label: "부작용 → 자극 관련"
bee_attr_coverage:
  concern_id: concern_acne
  weight: 0.5
  label: "커버력 → 여드름 커버"
```

### `src/common/config_loader.py`
- `load_concern_dict()` 추가
- `load_goal_alias_map()` 추가
- `load_concern_bee_attr_map()` 추가

---

## Phase 2: Source 정규화

### `src/user/adapters/personal_agent_adapter.py`
- concern fact 생성 시: `resolve_concern_id(concern)` 사용
  - `건조함` → concern_dict lookup → `concern_dryness`
  - concept_id: `concept:Concern:concern_dryness` (stable)
- goal fact 생성 시: `resolve_goal_id(goal)` 사용
  - `보습강화` → alias map → `보습`
  - concept_id: `concept:Goal:보습` (canonical)

### `src/ingest/product_ingest.py`
- MAIN_EFFECT → Goal concept 생성 시: `resolve_goal_id(benefit)` 사용
  - `보습` → alias map → `보습` (이미 canonical)
  - 결과: 유저 `concept:Goal:보습`과 상품 `concept:Goal:보습`이 동일

### `src/jobs/run_daily_pipeline.py`
- REL concern 처리 (L335-342): derive_concern() 결과의 concept_id를 `resolve_concern_id()`로 wrap
  - `concern_dryness` → `concept:Concern:concern_dryness` (IRI 형태 통일)

---

## Phase 3: Candidate Generator 연결

### `src/rec/candidate_generator.py`
- concern overlap (L162-165): comparison-time normalization 추가
  ```python
  user_concerns = {resolve_concern_id(c) for c in _extract_ids(user_profile.get("concern_ids", []))}
  product_concerns = {resolve_concern_id(c) for c in _extract_signal_ids(product.get("top_concern_pos_ids", []))}
  for c in user_concerns & product_concerns:
      overlap.append(f"concern:{c}")
  ```
- goal overlap (L167-173): comparison-time normalization
  ```python
  user_goals = {resolve_goal_id(g) for g in _extract_ids(user_profile.get("goal_ids", []))}
  product_benefits = {resolve_goal_id(g) for g in set(product.get("main_benefit_concept_ids") or [])}
  for g in user_goals & product_benefits:
      overlap.append(f"goal_master:{g}")
  ```
- BEE_ATTR → Concern bridge (신규 블록):
  ```python
  # Bridge: BEE_ATTR → Concern (discounted)
  bridged = compute_bridged_concerns(product.get("top_bee_attr_ids", []))
  for concern_id in user_concerns & set(bridged.keys()):
      if f"concern:{concern_id}" not in overlap:  # explicit 우선
          overlap.append(f"concern_bridge:{concern_id}")
  ```

### `src/rec/concern_bridge.py` (신규)
```python
def compute_bridged_concerns(top_bee_attr_ids: list[dict]) -> dict[str, dict]:
    """BEE_ATTR → Concern bridge 매핑 결과 반환."""
    # concern_bee_attr_map.yaml 로드
    # attr score > 0인 것만 필터
    # 매핑된 concern_id별 max score 반환
```

---

## Phase 4: Scorer + Explainer

### `configs/scoring_weights.yaml`
```yaml
concern_bridge_fit: 0.04  # explicit concern_fit(0.11)보다 낮게
```

### `src/rec/scorer.py`
- features dict에 추가:
  ```python
  "concern_bridge_fit": min(overlaps_by_type.get("concern_bridge", 0) / 2.0, 1.0),
  ```

### `src/rec/explainer.py`
- `_EDGE_MAP`: `"concern_bridge": ("HAS_CONCERN", "HAS_BEE_ATTR_SIGNAL")`
- `_concept_to_feature`: `"concern_bridge": "concern_bridge_fit"`
- `_generate_summary_ko`: `"BEE 속성 기반 '{concern_label}' 대응 추정"`

---

## 테스트

### `tests/test_concept_resolver.py` (신규)
- concern surface → stable ID
- goal alias → canonical
- unknown fallback
- IRI prefix 처리

### `tests/test_concern_context_matching.py` (신규)
- 유저 concern `건조함` × 상품 concern `concern_dryness` → overlap ✓
- 유저 goal `보습강화` × 상품 benefit `보습` → overlap ✓
- BEE_ATTR `보습력` POS × 유저 concern `건조함` → concern_bridge ✓
- explicit concern이 bridge보다 우선
- bridge 없는 attr → concern_bridge 미생성

### 기존 테스트 업데이트
- test_user_adapter_semantics.py: concern/goal canonical ID 확인
- test_recommendation.py: concern/goal overlap 동작 확인

---

## 수정 파일 요약

| 파일 | 변경 | Phase |
|------|------|-------|
| `src/common/concept_resolver.py` | 신규: resolve_concern_id, resolve_goal_id | 1 |
| `configs/goal_alias_map.yaml` | 신규: goal alias → canonical | 1 |
| `configs/concern_bee_attr_map.yaml` | 신규: BEE_ATTR → Concern bridge | 1 |
| `src/common/config_loader.py` | +3 cached loaders | 1 |
| `src/user/adapters/personal_agent_adapter.py` | concern/goal 정규화 | 2 |
| `src/ingest/product_ingest.py` | goal 정규화 | 2 |
| `src/jobs/run_daily_pipeline.py` | concern IRI 통일 | 2 |
| `src/rec/candidate_generator.py` | concern/goal normalization + bridge | 3 |
| `src/rec/concern_bridge.py` | 신규: BEE_ATTR→Concern bridge | 3 |
| `configs/scoring_weights.yaml` | +concern_bridge_fit | 4 |
| `src/rec/scorer.py` | +concern_bridge_fit feature | 4 |
| `src/rec/explainer.py` | +concern_bridge edge/summary | 4 |
| `tests/test_concept_resolver.py` | 신규 | 5 |
| `tests/test_concern_context_matching.py` | 신규 | 5 |

---

## 검증

```bash
python -m pytest tests/ -v
```

### 프론트 검증
파이프라인 재실행 후:
- 추천 테스터에서 concern/goal overlap이 0이 아닌 값으로 잡힘
- 설명에 "건조함 고민 대응" 같은 concern 기반 문구 출력
- BEE_ATTR bridge 경우: "BEE 속성 기반 '건조함' 대응 추정" 표시
