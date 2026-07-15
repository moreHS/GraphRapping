# Keyword Canonical Alias + Surface-Form / Taxonomy Priority

작성: 2026-07-13 · Phase 7 B2 · 근거 진단: `fable_doc/06_graph_ontology_assessment.md` §4 ·
계획: `fable_doc/plans/2026-07-13_phase7_graph_intelligence.md` §B2

이 문서는 (1) 동일 개념이 여러 keyword_id로 흩어질 때의 접힘 계층과 (2) 하나의
표면형이 여러 taxonomy 축(goal/keyword/concern/BEE)에 병존할 때의 해석 규칙을
명문화한다.

## 1. Keyword canonical alias 계층

### 어디서 접히나

접힘은 **키워드 해소 시점** 단 한 곳에서 일어난다:
`src/normalize/bee_normalizer.resolve_surface_keywords`. 이 함수는 리뷰 BEE
문구를 keyword_id로 해소하는 유일한 경로이며(신호 생성 + quarantine 억제 공용,
Phase 7 B1), 여기서 해소된 raw keyword_id를 `configs/keyword_alias_map.yaml`의
`alias → canonical` 맵으로 접은 뒤 **canonical id 기준으로 dedup**한다.

접힘이 여기 있으므로 하류(canonical_fact → signal_emitter → agg → serving)는
자동으로 통합된다. agg 지지도가 한 개념에 집중되어 승격 게이트(distinct_review
≥3)를 통과할 확률이 오른다.

```
리뷰 BEE 문구 ─► resolve_surface_keywords ─► [surface→keyword_id]
                                          └► alias 접힘(canonical) + dedup
                                          ─► canonical_fact(HAS_KEYWORD)
                                          ─► BEE_KEYWORD signal ─► agg ─► serving
```

### 접힘 규율 (config: `configs/keyword_alias_map.yaml`)

- **명백한 동일 개념만** 접는다. 현재 채택: 보습 계열
  `kw_moist(촉촉함) + MoistLike(촉촉한 텍스처 키워드) → kw_moisturizing(보습좋음)`.
- canonical target은 **그 자체가 alias key가 아니어야** 한다(체이닝 금지). 로더
  `_flatten_alias_chains`는 단일-홉 체인을 평탄화하고 **순환/자기참조는 load
  시점에 ValueError**로 거부한다(오류 클래스 테스트: `tests/test_keyword_alias.py`).
- 애매한 이웃(수분감 `kw_hydration`, 산뜻 `kw_fresh_feel`, 시원 `kw_cooling`,
  깔끔 `kw_clean_feel`)은 접지 않는다 — 후보로만 기록(DECISIONS 참조).

### 이중계상(double-count) 제거

한 표면형이 keyword 축 내 여러 sibling id에 매칭되면(예: `촉촉한` ⊃ `촉촉` → 
`kw_moist` + `MoistLike`) 접힘 전에는 같은 mention이 2~3개 신호로 부풀었다. 접힘은
canonical dedup으로 이를 1개로 눌러 **키워드 축 내부의 이중계상을 제거**한다.

## 2. 표면형-taxonomy 우선순위 규칙

같은 표면형이 여러 축에 존재한다(실측):

| 표면형 | 병존 축 |
|---|---|
| 보습 / 진정 / 탄력 | keyword ∩ goal |
| 건조 | keyword ∩ concern |
| 발림성 / 세정력 / 지속력 | keyword ∩ bee_attr |

### 판정: 병존은 정당하다 (cross-axis 재배정 불필요)

각 축은 **서로 다른 입력에서 채워지고 서로 다른 scorer feature가 소비**한다.
따라서 한 표면형이 여러 축에 있는 것은 이중계상이 아니라 설계된 병렬이다:

- **goal** (`goal_alias_map.yaml`): **유저 평면 전용**. goal은 유저 프로필에서
  나오지 리뷰 문구에서 나오지 않는다 — 보습/진정/탄력이 goal로도 있다는 사실은
  상품 신호에 영향이 없다(상품쪽 goal 신호 0).
- **keyword** (`keyword_surface_map.yaml`): 상품 리뷰 BEE_KEYWORD 신호 →
  keyword-overlap feature.
- **bee_attr** (`bee_attr_dict.yaml`): 상품 BEE_ATTR 축 → bee_attr-overlap feature.
  발림성/세정력/지속력이 attr名이면서 keyword인 것은 normalizer의 핵심 설계(BEE
  문구 → **BEE_ATTR + KEYWORD 동시 방출, "never merge"**)의 정상 결과다. 한
  mention이 attr축과 keyword축 양쪽에 기여하는 것은 2축 표상이지 단일 feature의
  부풀림이 아니다.
- **concern** (`concern_dict.yaml`): 유저 concern + 상품쪽 파생 concern(CONCERN
  family) → concern-bridge feature.

### 실측 근거 (이중계상이 아님)

dense_golden 900 리뷰 실측:
- 상품 concern 신호의 대상은 concern_acne(4)/flaking(2)/wrinkles(2)/hair_loss(1)
  뿐 — **보습/건조 keyword를 재파생하지 않는다**. concern 축은 독립 개념을
  concern-bridge로 만든다.
- keyword 신호와 concern 신호를 **둘 다** 가진 리뷰는 4/900이며, 그 concern
  대상은 keyword와 다른 개념(서로 다른 feature 소비) — 같은 feature로 합산되는
  이중계상이 아니다.

결론: **cross-axis 우선순위(재타이핑/억제) 규칙은 불필요**하다. 유일하게 실재한
이중계상은 keyword 축 **내부**의 sibling-id 분산이었고, 이는 §1의 canonical alias
접힘으로 해소한다.

### 규칙 요약

1. 축 간(goal↔keyword↔concern↔bee_attr) 병존은 유지한다(정당한 병렬 소비).
   각 축은 자기 입력·자기 feature를 가진다.
2. 축 **내부**(특히 keyword)의 동일 개념 sibling id는 canonical alias로 접어
   해소 시점에 dedup한다(§1).
3. 새 alias는 명백한 동일 개념만, canonical-terminal·비순환 불변식을 지켜 추가한다.

## 3. 알려진 후속(follow-up)

- **유저측 texture alias 대칭 적용**: `MoistLike`는 texture 축 id로,
  `personal_agent_adapter`의 `preferred_texture` 해소(`get_texture_surface_to_keyword`)
  가 유저측에서 minting한다(B2 범위 밖 파일). 현재 keyword-overlap은 대소문자
  민감(`_join_key`)이고 골든 유저가 moisture keyword를 안 가져서 상품측만 접어도
  회귀가 없음이 실측됨(dense/wide 서빙·랭킹 재승인 diff로 확인). texture 축
  선호는 `PREFERS_BEE_ATTR(bee_attr_formulation)`로 독립 보존되므로 keyword id
  접힘이 축 의미를 잃지 않는다. 다만 향후 유저가 촉촉 texture를 표현하기
  시작하면 유저측에도 동일 alias를 적용해 contract 정합을 맞춰야 한다.
- 브리지/접힘 후보(수분감·산뜻·시원·깔끔)의 도메인 감수.
