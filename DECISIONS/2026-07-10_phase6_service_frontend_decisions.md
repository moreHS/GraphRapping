# Phase 6 (서비스 프론트 + LLM 쿼리 이해) 결정 모음

날짜: 2026-07-10 · 상태: 확정 · 상세 설계·실행 기록:
`fable_doc/plans/2026-07-10_phase6_service_frontend_query_understanding.md`

감사 지적(마감 리뷰)에 따라 계획 문서 내부에만 있던 결정들을 DECISIONS로 승격.

## 1. 사용자 확정 4건 (2026-07-10)

1. **LLM provider = 사내 Azure OpenAI** (표준 env 4종
   `AZURE_OPENAI_ENDPOINT/API_KEY/DEPLOYMENT/API_VERSION`,
   `GRAPHRAPPING_QUERY_LLM=azure|anthropic|off` 추상화)
2. **질의 스코프 = 제한→자동완화 하이브리드** (교집합 공집합 시 부스트-only
   완화 + 응답 `relaxed` 명시)
3. **CDN vendoring 채택** (chart.js/cytoscape → static/vendor/, MIT)
4. **착수 순서 = Track A(프론트 서비스화) → Track B(쿼리 이해)**

## 2. provider 기본값 재해석 (구현 중 결정, Fable 승인)

결정 1의 "azure 기본"을 **"활성화 시 권장 provider"**로 해석 —
`GRAPHRAPPING_QUERY_LLM` **미설정 시 off(사전 폴백)**. 근거: 데모/테스트
환경이 크리덴셜 없이 안전해야 하고, LLM 장애가 서비스를 못 멈추게 하는
폴백-우선 원칙. 트레이드오프: 기본 설정에서 부정어("~없는")를 사전 폴백이
못 읽는 침묵 실패가 발생(마감 감사 NOT-READY 근거) → **후속 결정 3으로 보완**.

## 3. 사전 폴백 부정어 보강 (마감 리뷰 F1, 차단 해소)

기본 경로(off)에서도 보수적 한국어 부정 패턴("X 없는/없이/빼고/제외/프리")을
감지해 성분 축 검증(사전 멤버십 게이트) 통과분을 회피 필터로 배선. 검증 실패
시 `interpretation.warnings`로 침묵 없이 노출. 모킹 없는 e2e 테스트로 고정.
전면 NLU는 비목표 — LLM 활성 시의 보조 안전망.

## 4. 프론트 스코프 축소 1건

해석 칩의 "칩 제거 → 재질의" 인터랙션은 **미구현 확정** — 질의문 수정 후
재제출이 대체 UX. 근거: 칩 제거는 LLM 재해석과 의미가 어긋나고(질의 원문이
진실), 데모 단계 가치 대비 복잡도 높음. 필요 시 후속 Phase에서 재검토.
