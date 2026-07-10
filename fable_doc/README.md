# fable_doc — GraphRapping 구조 진단 및 개선 계획 (Claude Fable 5 분석)

작성일: 2026-07-07
작성: Claude Fable 5 (분석 세션), Codex(GPT) Architect 크로스 리뷰 반영
기준 커밋: `431dae3` (docs: localize recommendation signal flow)
기준 데이터: 906리뷰 / 517상품 / 50유저 wide fixture + dense golden fixture(33상품)

이 폴더는 2026-07-07 시점에 GraphRapping 전체 구조를 파악하고, 약점을 진단하고,
개선 계획을 수립한 결과물을 담는다. 이후 작업(Phase 실행, 재진단, 신규 참여자 온보딩)에서
참조 문서로 활용한다.

## 문서 맵

| 문서 | 내용 | 언제 읽나 |
|---|---|---|
| [01_project_understanding.md](01_project_understanding.md) | 시스템 구조 파악 — 데이터 소스 3종, 5-layer, 온톨로지 구성, KG/추천 파이프라인, DB 스키마, 운영 구조 | 프로젝트를 처음 파악할 때, 서브시스템 동작을 확인할 때 |
| [02_issues_assessment.md](02_issues_assessment.md) | 강점 7개 + 약점 A~G 상세 진단 (근거 파일:라인 포함) | 개선 항목의 "왜"를 확인할 때 |
| [03_improvement_plan.md](03_improvement_plan.md) | 개선 계획 상세 — Phase 0~5, 항목별 배경/작업/완료기준/의존성 | Phase 착수 전, 우선순위 재조정 시 |
| [04_cross_review_log.md](04_cross_review_log.md) | Codex Architect 크로스 리뷰 지적 10건 원문과 반영 내역 | 계획의 근거/이력을 추적할 때 |

## TL;DR

**시스템**: 상품마스터 + 리뷰 트리플(rs.jsonl) + 유저프로파일(personal-agent)을
`concept_id` 공통 평면으로 연결하는 5-layer evidence-first 추천 시스템.
Postgres 영속화, 사전/규칙 기반 시맨틱, FastAPI 데모 서빙.

**강점**: evidence-first 계약(3 evidence family + eligibility gate), 3중 promotion gate,
provenance 체인, idempotent 파이프라인, quarantine 체계, config 기반 온톨로지.
이 골격은 유지한다.

**핵심 약점** (상세: 02 문서):

| 축 | 요지 |
|---|---|
| A. 정체성 갭 | "그래프"인데 multi-hop 순회 0건, 검색 미구현, 온톨로지 비정형 |
| B. identity/계약 리스크 | source identity collision, glb 채널 product_id=상품명, scope 계약 회귀 방어가 수동 |
| C. 랭킹 품질 측정 부재 | evidence audit는 있으나 순위 품질 정량화 없음, 가중치 수동 튜닝 |
| D. 시맨틱 천장 | 임베딩 전무(사전+YAML만), broad semantic 누수 관측 |
| E. 서빙 미분리 | API가 in-memory DemoState만 순회, DB mart를 읽지 않음 |
| F. 운영 성숙도 | retention 미구현, 스케줄러/CLI/메트릭/알림 부재 |
| G. 확장성 | 한국어/뷰티 하드코딩, 단일 유저 어댑터, kg_mode 이중 파이프라인 |

**개선 순서** (상세: 03 문서): 품질/계약 기준선(Phase 0) → identity/계약 방어(1) →
서빙 경로 정리(2) → 시맨틱 규칙 강화(3) → 그래프/검색 실체화(4) → 백로그(5).
운영 확장(retention 구현, 파티셔닝, 부하검증)은 실데이터 연속 적재 시점으로 미룬다.

## 분석 방법론 (재현 가능하도록 기록)

1. **문서 정독**: README, ARCHITECTURE.md, db_consumer_contract.md,
   recommendation_signal_flow, SCHEMA_RS_JSONL, DECISIONS/ 주요 결정, pyproject.toml
2. **병렬 코드 탐색 3방향**: (a) KG 파이프라인/온톨로지 (b) 추천/유저 프로파일
   (c) DB/잡/서빙 인프라 — 각각 파일:라인 근거와 함께 결론 수집
3. **핵심 주장 직접 검증**: in-memory 서빙(server.py DB 접근 0건),
   후보 생성 선형 스캔(candidate_generator.py 순회 루프) 등 보고서의 load-bearing
   주장을 grep/정독으로 재확인
4. **크로스 리뷰**: 초안 계획을 Codex(GPT) Architect에 read-only 저장소 접근과 함께
   전달 → REVISE 판정 + 지적 10건 → 전건 검증 후 반영 (04 문서)

## 사용자 결정 대기 항목

- **Phase 0.5**: 랭킹 ground-truth 라벨 확보 방법 (도메인 평가자 라벨링 vs 구매 이력 proxy)
- **Phase 1.3**: 리뷰 max 보존 기간 (기존 Wave 6 조건과 동일 — retention TTL 설계의 입력)
- **Phase 계획 전체 승인** 후 Phase 0 착수
