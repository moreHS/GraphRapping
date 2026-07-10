# neo4j_label 죽은 코드 정리 범위 결정 (4.4)

날짜: 2026-07-10 · 상태: 결정 (Fable) · 배경: fable_doc/03 §4.4 — 4.3 완료 기준의
"neo4j_label 등 죽은 필드 정리 여부 결정"이 미이행이던 것(5차 갭 감사 MED)을 이행.

## 실측 사용처 맵 (2026-07-10 grep 전수)

| 대상 | 위치 | 상태 |
|---|---|---|
| `KGConfig.get_neo4j_label()` accessor | src/kg/config.py:77-78 | **죽음** — 호출자 0 (src/tests 전체) |
| `self._neo4j_labels` dict + 구축 라인 | src/kg/config.py:26,39 | **죽음** — 위 accessor로만 노출 |
| config 필드 `neo4j_label` (kg_entity_types.json) | ontology_validator.py:112,127,151 | **살아있음** — BEE 그룹 판별("BEE_ATTR" 공유 라벨)과 타입 매핑의 실사용 마커 |
| relation `neo4j_type` + `get_neo4j_relation_type()` | mention_extractor.py:257, canonicalizer.py:79-84 | **살아있음** — canonical relation 정규화의 핵심 경로 |

즉 "neo4j_*"는 이름만 legacy(Relation 프로젝트 Neo4j 시절 포팅 흔적)일 뿐,
**필드/relation 매핑은 현행 기능**이고 죽은 것은 entity-label accessor 한 쌍뿐.

## 검토한 선택지

- **(a) 전면 리네임** (neo4j_label→node_label 등): 이름의 오해 소지는 해소하나
  config json 스키마 + validator + 로더 동시 변경. 그래프 DB가 4.0 audit로
  보류된 상황에서 순수 네이밍 비용만 발생 — 과잉.
- **(b) 죽은 accessor만 제거, 살아있는 필드는 유지 + 유래 문서화** ← **채택**
  - src/kg/config.py에서 `get_neo4j_label`, `_neo4j_labels`(26행), 구축(39행) 제거
  - config 필드는 유지하되 로더/validator 주변 주석에 "이름은 Neo4j 포팅 유래,
    현재는 BEE 그룹핑·타입 매핑 마커로 사용"을 명시
  - relation `neo4j_type` 경로는 이름 포함 그대로 유지
- **(c) 전부 유지(현상 동결)**: 갭 감사가 반복 지적하는 미결정 상태 지속 — 탈락.

## 선택 이유 / 트레이드오프

- 죽은 코드는 제거가 원칙(이번 감사가 혼란 비용의 실증). 반면 살아있는 필드의
  리네임은 그래프 DB 재평가(Phase 5, 4.1 성능 데이터 확보 후) 결정과 묶는 것이
  일관적 — 그때 스키마를 어차피 재설계한다.
- 트레이드오프: "neo4j"라는 오해 소지 이름이 당분간 남음. 완화: 주석 문서화.
  git 이력이 제거분을 보존하므로 재도입 시 정보 손실 없음.

## 구현 범위 (Batch 2 위임)

1. src/kg/config.py — 26·39·77-78행 제거 (dict/구축/accessor)
2. 같은 파일 로딩부에 필드 유래·현행 역할 주석 1-2줄
3. 게이트: 참조 0이므로 테스트 영향 없음 예상 — 전체 게이트로 확인
