# 검수 결과 — 남은 이슈 수정 계획

## 수정 완료 확인 (5건 전부 PASS)
- C1: import 모듈 상단 이동 ✓
- C2: BEE attribution 전체 chain (bee_row → EntityMention → RelationMention → KGEdge → adapter gate) ✓
- M1: hair concern/goal resolver 적용 ✓
- M2: scoring weights 합계 1.02 ✓
- M3: goal_alias_map 100% coverage ✓

---

## 남은 MODERATE 이슈 수정 계획

### M4: skin_type_fit ID 불일치
**문제**: `_SKIN_TYPE_CONCERN_MAP`이 `"dryness"`, `"oily"` 같은 영어 fragment를 사용. 상품 concern ID가 `concept:Concern:concern_dryness` 형태이므로 매칭 안 됨.

**수정**:
- `_SKIN_TYPE_CONCERN_MAP`의 boost/penalty 값을 concern_dict stable key로 변경
- 비교 시 `resolve_concern_id()`로 정규화
- 파일: `src/rec/scorer.py` (_SKIN_TYPE_CONCERN_MAP + _skin_type_fit)

```python
_SKIN_TYPE_CONCERN_MAP = {
    "건성": {"boost": ["concern_dryness"], "penalty": ["concern_oiliness"]},
    "지성": {"boost": ["concern_oiliness"], "penalty": []},
    "복합성": {"boost": ["concern_oiliness", "concern_dryness"], "penalty": []},
    "민감성": {"boost": ["concern_sensitivity", "concern_irritation"], "penalty": []},
}
```
- `_skin_type_fit()`: pos_ids/neg_ids를 `resolve_concern_id()`로 정규화 후 비교

### M5: purchase_loyalty/novelty brand ID 불일치
**문제**: `product_profile["brand_id"]`는 raw (`"라네즈"`), 유저 `preferred_brand_ids`는 concept IRI (`concept:Brand:라네즈`).

**수정**:
- `_purchase_loyalty_score()`, `_novelty_bonus()`: user brand set에서 `concept:Brand:` prefix 제거
- 패턴: family prefix stripping과 동일 (`fid.startswith("concept:Brand:")`)
- 파일: `src/rec/scorer.py`

### M6: concern_bridge score 미활용
**문제**: `compute_bridged_concerns()`가 score를 반환하지만 candidate_generator가 count만 사용.

**수정**:
- 현재: `overlap.append(f"concern_bridge:{concern_id}")` — string tag만
- 변경: overlap에 score 정보를 태그에 포함하거나, scorer에서 bridge map을 직접 조회
- **권장**: scorer에서 `compute_bridged_concerns()`를 직접 호출해 weighted score 사용
- 파일: `src/rec/scorer.py`, `src/rec/candidate_generator.py`

### M7: goal_review dead path 정리
**문제**: `user_goals_norm & product_concerns_norm`은 goal vs concern 교차 비교 — 서로 다른 resolver를 쓰므로 절대 매칭 안 됨.

**수정**:
- 이 로직 제거 또는 concern_bridge와 통합
- `goal_fit_review_signal` weight를 0으로 두거나 feature 자체를 제거
- 파일: `src/rec/candidate_generator.py`, `configs/scoring_weights.yaml`

---

## LOW 이슈 정리

| # | 이슈 | 수정 |
|---|------|------|
| L1 | comparison_resolved 미구현 | bee_attribution.py에 주석 명시 (future work) |
| L2 | explainer sorted_contribs 미사용 | 변수 제거 |
| L3 | ATTRIBUTION_PRIORITY import 미사용 | import 제거 |
| L4 | None list field 방어 | `or []` fallback 추가 |

---

## 수정 파일 요약

| 파일 | 변경 |
|------|------|
| `src/rec/scorer.py` | M4: _SKIN_TYPE_CONCERN_MAP + _skin_type_fit 정규화 |
| `src/rec/scorer.py` | M5: brand prefix stripping |
| `src/rec/scorer.py` | M6: concern_bridge weighted score |
| `src/rec/candidate_generator.py` | M7: goal_review 제거 |
| `configs/scoring_weights.yaml` | M7: goal_fit_review_signal 제거/0 |
| `src/link/bee_attribution.py` | L3: unused import 제거 |
| `src/rec/explainer.py` | L2: unused variable 제거 |

## 검증
```bash
python -m pytest tests/ -v
```
