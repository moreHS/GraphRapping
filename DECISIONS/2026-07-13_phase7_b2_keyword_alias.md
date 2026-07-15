# Phase 7 B2 — 동일 개념 접힘(keyword canonical alias) + taxonomy 우선순위

날짜: 2026-07-13 · 상위 계획: `fable_doc/plans/2026-07-13_phase7_graph_intelligence.md` §B2 ·
진단: `fable_doc/06_graph_ontology_assessment.md` §4 ·
아키텍처 문서: `docs/architecture/keyword_alias_and_taxonomy_priority_2026_07_13.md`

## 배경

진단 §4: 보습 계열이 3개 keyword_id로 분산(`보습→kw_moisturizing / 촉촉→kw_moist
/ 촉촉한→MoistLike`), 한 표면형이 여러 taxonomy 축에 병존. 분산은 agg 지지도를
흩어 승격을 스스로 어렵게 만들고, sibling id 중복 매칭은 한 mention을 이중계상한다.

## 실측 (접힘 대상)

dense_golden 기준 keyword_id별 raw 신호 지지도 실측 후 클러스터 판정:

- **채택(ADOPT)**: `kw_moist`(118) + `MoistLike`(87) + `kw_moisturizing`(44)
  → canonical `kw_moisturizing`. 진단이 명시한 확신 클러스터. `촉촉한`은 `촉촉`을
  부분포함해 실제로 `kw_moist`+`MoistLike`를 동시 방출(이중계상 실증).
- **후보(CANDIDATE, 미접힘)**: `kw_hydration`(수분감, 19) — 보습/촉촉과 근접하나
  hydration은 별개 granularity. 도메인 감수 필요.
- **기각(REJECT, 별개 유지)**: `kw_dry`↔`kw_low_dryness`(극성 반대),
  `kw_thin_spread`↔`kw_thick_spread`(반대), `kw_no_scent`↔`kw_good_scent`,
  `kw_soft`↔`kw_mild`, `kw_light_feel`↔`LightLotionLike`(feel↔texture type),
  `kw_fresh_feel`/`kw_cooling`/`kw_clean_feel`(refreshing 계열이나 별개 개념).

## 결정

1. **접힘 지점 = `resolve_surface_keywords` 출력 canonical 재매핑 + dedup**
   (`src/normalize/bee_normalizer.py`). 신호 생성/quarantine 공용 단일 경로이므로
   하류 agg/serving이 자동 통합. `configs/keyword_alias_map.yaml`(신규, alias→
   canonical) 도입 — 기존 config 관례(YAML) 준수. `apply_alias=False` 인자로
   해소 mechanic 단위테스트를 alias 정책과 격리.
2. **오류 클래스 방어**: 로더 `_flatten_alias_chains`가 순환/자기참조를 load 시점
   ValueError로 거부하고 단일-홉 체인을 canonical-terminal로 평탄화. canonical이
   또 alias인 경우(체이닝)도 종단까지 해소. 테스트 `tests/test_keyword_alias.py`.
3. **taxonomy 병존 = 정당한 병렬로 판정**(cross-axis 재배정 불필요). goal(유저
   전용)/keyword/bee_attr/concern은 서로 다른 입력·서로 다른 scorer feature가
   소비. 실측: 상품 concern 신호는 acne/flaking/wrinkles 등 독립 개념뿐이고
   moisture keyword를 재파생하지 않음(이중계상 아님). 유일한 실재 이중계상은
   keyword 축 내부 sibling id 분산 → alias 접힘으로 해소. 상세 규칙은 아키텍처
   문서에 명문화.

## 효과 실측 (before → after, kg_on)

| 지표 | before | after | 비고 |
|---|---|---|---|
| 보습 클러스터 지지도(dense, raw 신호) | 118/87/44 (3 id, 249행) | **157 (1 id kw_moisturizing)** | 92행은 동일 mention 이중계상, dedup |
| BEE_KEYWORD 신호(dense) | 802 | **710** | −92 = 이중계상 제거 |
| moisture 서빙 상품(dense) | 18 | **24** | +6: 지지도 통합이 승격 게이트 통과 |
| moisture 서빙 행(dense) | 36 | **24** | 상품 내 중복 나열 제거 |
| moisture 서빙 상품(wide) | 5 | **6** | +1 승격 |
| corpus signal_count(wide kg_on) | 3,340 | **3,248** | −92, quarantine·top_* 불변 |
| corpus signal_count(wide kg_off) | 3,365 | **3,273** | −92 |

## 스냅샷/베이스라인 영향 — **의도 변경, 재승인 대상**

접힘은 서빙 도달(승격)과 이중계상을 동시에 바꾸므로 랭킹 스냅샷이 이동한다.
단순 green이 아니라 "의도 변경 재승인" 워크플로우로 처리(계획 §A4-2).

- **랭킹 스냅샷 재생성**(intended): `dense_golden.json`(42조합) /
  `wide_golden.json`(350조합).
  - dense: NEW 8 / DROPPED 8 / rank changed 20 / score 변경 19.
  - wide: NEW 8 / DROPPED 8 / rank changed 26 / score 변경 16.
  - 방향: (a) 이중계상으로 부풀었던 상품은 review_graph_score 하락(예 100669
    0.1416→0.0989 — 버그였던 중복 제거), (b) 통합으로 moisture가 새로 승격된
    상품은 상승·top-N 진입(예 103537 review_graph 0.028→0.0507). **둘 다 접힘의
    올바른 귀결**(중복 제거 + 지지도 집중).
  - 재생성 커맨드: `python scripts/generate_ranking_snapshot.py --fixture
    dense_golden --update` / `... --fixture wide --snapshot-path
    tests/fixtures/ranking_snapshots/wide_golden.json --update`.
- **corpus 승격 베이스라인**(`test_corpus_promotion_baseline.py`): signal_count
  갱신(kg_on 3,340→3,248, kg_off 3,365→3,273). quarantine·top_*·floor 불변.
- **기대셋**(`test_expected_evidence_family_baseline`): green(계약 불변 — 신규
  family 없음, id 접힘만).
- **소급 마이그레이션 불필요**: 산출물은 fixture 데모 재실행으로 재생성. 실DB
  적재분이 있으면 재적재 시 자동 반영(별도 마이그레이션 스크립트 불요, alias는
  해소 시점 적용이므로 재적재만으로 소급됨).

## 검증

- `ruff`/`mypy(114)` ✅, `validate-ontology` exit 0(경고 4 불변),
  `pytest` **1,148 passed / 50 skipped / 0 failed**(+9: `test_keyword_alias.py`).
- 오류 클래스: 순환·자기참조·긴순환 거부 + 체인 종단 해소 테스트 통과.

## 잔여 follow-up

1. 유저측 texture alias 대칭 적용(`personal_agent_adapter` — B2 범위 밖). 현재
   상품측 접힘만으로 회귀 없음(실측), 향후 유저가 촉촉 texture 표현 시 contract
   정합 필요. 아키텍처 문서 §3 참조.
2. 접힘 후보(수분감·산뜻·시원·깔끔) 도메인 감수.
