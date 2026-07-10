# 04. 크로스 리뷰 로그 — Codex(GPT) Architect

작성일: 2026-07-07

CLAUDE.md 워크플로우의 크로스 리뷰 단계 기록. 개선 계획 초안을 GPT 전문가에게
리뷰받고 반영한 이력이다.

## 실행 경위

- 시도 1: `plan-review-loop` MCP (Codex 기반 Architect 리뷰 루프) → **실패**.
  MCP 서버가 스폰하는 homebrew 전역 npm 설치
  (`/opt/homebrew/lib/node_modules/@openai/codex/...`)의 바이너리가 없음(ENOENT)
- 시도 2: 로컬 `~/.local/bin/codex`(v0.142.0, 정상)를 직접 실행 —
  `codex exec --sandbox read-only` + 저장소 read-only 접근 + Architect/Plan Reviewer
  페르소나 프롬프트. **성공**
- ⚠️ 환경 이슈: homebrew 쪽 `@openai/codex` 재설치 필요 (plan-review-loop MCP 복구용)

리뷰어에게 제공한 것: 초안 계획 전문 + 프로젝트 컨텍스트(스택, upstream/downstream,
fixture 단계임) + 저장소 read-only 접근. 요구사항: 사실 주장을 코드로 검증할 것,
Phase 순서를 도전할 것, DECISIONS와의 모순을 찾을 것, 누락 약점을 지적할 것.

## 결과: verdict REVISE + 지적 10건

> 총평: "약점 식별은 상당수 맞지만, 우선순위가 틀렸다. fixture-scale pre-production에서
> Phase 0/1을 retention, partitioning, 10만 상품 부하검증으로 시작하는 것은 과하다.
> 현재 더 큰 리스크는 '추천 품질을 어떻게 판단할지'와 'source identity / profile
> scope / evidence eligibility 계약이 깨지지 않는지'다."

| # | 지적 요지 | 리뷰어가 든 근거 | 판단 | 반영 |
|---|---|---|---|---|
| 1 | Phase 0을 NDCG 하네스가 아니라 "품질/계약 기준선"으로 재정의하라. 골든 프로파일별 expected evidence family, no-candidate 허용 조건, source-stats-only 금지를 먼저 고정 | scripts/audit_recommendation_evidence.py 존재, DECISIONS/2026-06-22가 dense golden을 품질 fixture로 결정 | 수용 — NDCG는 정답 라벨 없이는 무의미, 기존 자산 확장이 맞음 | Phase 0.1로 재정의, NDCG는 0.5(라벨 전략 결정)로 분리 |
| 2 | "추천 품질 평가 인프라 전무"는 과장 — "랭킹 메트릭 부재"로 좁혀라 | audit script + tests/test_golden_profile_recommendation_audit.py가 coverage/evidence-family/score-layer 이미 검증 | 수용 — 기존 자산 무시는 부정확 | 이슈 C 재서술 (02 문서) |
| 3 | "후보 생성 선형 스캔"은 과장 — SQL prefilter(mart_repo.py:411)와 prefiltered path(candidate_generator.py:326)가 존재. 문제는 기본 경로가 아니고 동치성 미검증인 것 | 코드 확인 | 수용 — 스캔 자체는 사실이나 prefilter 자산 존재를 반영해 해법 수정 | 이슈 E2 재서술 + Phase 2.2를 "기본 경로 승격 + 동치성 테스트"로 변경 |
| 4 | 10만 상품/수백만 리뷰 합성 부하검증은 뒤로 미뤄라 — 서빙 경로가 consumer contract로 고정된 뒤에 | fixture 단계에서 synthetic scale보다 dense golden 품질/identity/scope drift가 먼저 | 수용 | Phase 1에서 제거, 백로그로 이동(착수 조건: Phase 2 완료) |
| 5 | Retention은 "구현"이 아니라 "모니터링 + 정책 결정 + TTL 설계"로 축소, 파티셔닝은 운영 데이터 증가가 보일 때 | retention gap은 문서화된 사실이나(db_consumer_contract §12) fixture 단계 구현은 과잉 | 수용 | Phase 1.3으로 축소, 구현은 백로그(착수 조건 명시) |
| 6 | 누락 약점: source identity / product_id collision 리스크를 최상위 항목으로 승격하라 | db_consumer_contract §3의 35119 collision, clean identity는 3-필드 복합키 | 수용 — downstream 오염 위험이 추천 품질보다 큼 | 이슈 B1 신설(최상위), Phase 1.1 신설 |
| 7 | 누락 약점: profile scope / active-category 의미 drift를 넣어라 — 계약이 깨지면 그럴듯한 잘못된 개인화가 됨 | DECISIONS/2026-06-23(active category ≠ preference), scoped preference 결정들 | 수용 | 이슈 B3 신설, Phase 0.2(계약 회귀 테스트) 신설 |
| 8 | 임베딩을 Phase 3 주요 해법으로 두는 것은 이르다 — 먼저 YAML rule의 scope/category gating + regression fixture, 임베딩은 unknown keyword 제안 보조로만 | DECISIONS/2026-06-22 repair가 value-and-polarity rule로 누수를 막기로 결정 | 수용 — 기존 결정과의 일관성 | Phase 3.1(scope) 선행, 3.2를 보조 한정으로 명시, recall 채널은 백로그 강등 |
| 9 | "서빙이 데모 수준" 표현을 "FastAPI demo serving이 in-memory"로 제한하라 — DB serving contract는 이미 존재 | state.py:21/:66의 DemoState는 사실이나 db_consumer_contract §3의 serving 테이블 계약 존재 | 수용 — 문제의 본질은 "웹/API surface가 mart reader로 미분리" | 이슈 E 재서술 |
| 10 | Phase 4는 "multi-hop 구현"보다 "multi-hop 사용 사례 검증"을 먼저 — 어떤 질문이 2-hop 이상을 필요로 하는지 audit query로 증명하라 | multi-hop 미구현은 사실(consumer contract §12.2)이나 수요 미증명 | 수용 | Phase 4.0(사용 사례 audit) 신설, 4.1을 조건부로 변경 |

## 리뷰가 확인해준 사실 (초안 주장 중 검증 통과)

- in-memory DemoState 서빙 (state.py:21, :66)
- multi-hop/centrality 미구현 (consumer contract §12.2와 일치)
- retention gap 문서화 위치 (§12.1의 :417/:421/:424)
- source identity 3-필드 복합키 계약과 35119 collision

## 메인 세션 측 독립 검증 (리뷰와 별개로 직접 확인)

- `src/web/server.py`에 asyncpg/pool/fetch 참조 0건 — grep 확인
- `generate_candidates()`의 `for product in product_profiles` 전체 순회 — 코드 정독
- pyproject.toml 의존성에 ML/임베딩/그래프DB/스케줄러 부재
- scripts/, tests/ 목록에서 audit 자산 존재 확인

## 결론

지적 10건 전건 수용. 핵심 교정은 (1) 측정·계약 기준선을 최우선으로 (2) 운영
확장은 착수 조건부 백로그로 (3) 기존 자산(audit, prefilter, DECISIONS 결정)을
무시하지 않는 방향으로 계획 재편. 반영 결과는 [03_improvement_plan.md](03_improvement_plan.md).
