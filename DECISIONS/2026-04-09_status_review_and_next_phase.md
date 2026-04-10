# 현재 상태 점검 + 다음 페이즈 계획

## GPT 피드백 검증 결과

| 항목 | GPT 주장 | 실제 | 조치 필요 |
|------|---------|------|----------|
| **A. Graph API corpus/evidence** | view 파라미터 안 지킴 | **사실** — view param 받지만 필터링 없음 | **수정 필요** |
| **B. Texture 수동 동기화** | drift 위험 | **해결됨** — authoritative:true + shared loader + sync test | 추가 불필요 |
| **C. Family 1급 탐색 미약** | scorer feature 수준 | **해결됨** — candidate_bucket 필드 + 3분류 존재 | 활용 심화는 가능 |
| **D. source_fact_ids 잔존** | 스키마에 살아있음 | **부분** — deprecated 마킹, read path 없음, write-only cache | 중장기 정리 |
| **E. User raw contract** | raw transformer 없음 | **해결됨** — sync_user_profiles.py가 transformer 역할 | 추가 불필요 |
| **F. SQL-first 미완** | Python full-scan | **해결됨** — server.py가 prefiltered 사용 | 추가 불필요 |

## 실제 남은 개선 포인트 (검증 후)

### 즉시 수정 (이번 세션)

**1. Graph API corpus/evidence view 분기 구현**
- `src/web/server.py` `product_graph()`: view 파라미터가 존재하지만 무시됨
- corpus view → serving_product_profile 기반 (promoted only)
- evidence view → demo_state.product_signals 전체
- 응답에 `view_mode` 필드 추가
- 테스트 추가

### 다듬을 것 (품질 향상)

**2. BEE attribution gate가 KG pipeline 내부에서 edge까지 전파되는지 확인**
- 현재: run_daily_pipeline에서 bee_rows enrichment + adapter/emitter gate
- KG 내부(mention_extractor → canonicalizer)에서는 아직 attribution이 전파 안 됨
- 즉시 문제는 아님 (adapter gate가 잡음), 하지만 KG evidence graph에서도 linked/unlinked 구분이 있으면 더 좋음

**3. 프론트 그래프 뷰어에서 linked/unlinked BEE 시각 구분**
- graph API가 corpus/evidence를 구분하면 자연스럽게 해결

### 다음 페이즈 후보

**A. BEE Target Attribution 완성 (중기)**
- Phase 1-3은 구현됨 (attribution helper + adapter gate + emitter defense + legacy gate)
- 남은 것: KG 내부 전파 (mention_extractor → canonicalizer edge에 target_linked), SQL DDL 확장, 프론트 시각화

**B. Concern/Context 연결 강화 (중기)**
- 현재 concern/context signal이 거의 0 (explicit relation이 부족)
- product truth의 MAIN_EFFECT를 concern/goal concept에 매핑하는 경로 필요
- 또는 BEE_ATTR ↔ concern 매핑 사전 (보습력 ↔ 건조함 등)

**C. Keyword 군집화/대표값 정규화 (중기)**
- 현재 keyword가 자유 워딩 그대로 (순하, 사용중, 구입, 가볍 등)
- 유사 키워드 → 대표 keyword로 군집화 필요 (NLP 기반)
- 이건 별도 프로젝트 수준

**D. 실제 운영 데이터 연결 (장기)**
- mock → production DB 전환
- rs.jsonl 실시간 ingest
- product catalog ES 실시간 동기화

---

## 이번 세션 실행 계획

**Graph API corpus/evidence view 분기 구현**만 즉시 수정.

### 수정 파일
- `src/web/server.py`: `product_graph()` — view param 분기 로직
- `tests/test_graph_api_view.py`: 신규 — corpus/evidence 분기 테스트

### 구현 내용
1. `view="corpus"` (기본): `demo_state.serving_products`에서 해당 product의 serving profile로 그래프 구성 (promoted signal만)
2. `view="evidence"`: 기존 `demo_state.product_signals` 전체 사용
3. 응답에 `view_mode` 필드 추가
4. 프론트 그래프 뷰어에 view 선택 UI 추가

### 검증
- `python -m pytest tests/`
- 프론트에서 그래프 뷰어 corpus/evidence 전환 확인

### 실행 결과
- Graph API corpus/evidence view 분기 **구현 완료**
- Corpus: promoted serving signal + product truth 기반 (25 nodes)
- Evidence: 전체 per-review signal (187 nodes)
- 프론트 드롭다운으로 전환 가능

---

## 다음 페이즈 상세 분석 (우선순위순)

### 1순위: Concern/Context 연결 강화

**현재 상태**: 상품 serving profile의 `top_concern_pos_ids`와 `top_context_ids`가 전부 0.
유저는 `건조함`, `잔주름` concern과 `보습강화` goal을 갖지만, 상품에 매칭되는 concern/context signal이 없어 이 축으로 추천 매칭이 전혀 안 됨.

**원인**: concern signal은 `addresses(Product, Concern)` 같은 explicit relation이 필요한데 리뷰에 이런 relation이 거의 없음. 상품 마스터의 `MAIN_EFFECT: "보습"`과 유저 concern `건조함`은 다른 concept plane에 있어서 연결 안 됨.

**해결 시 효과**:
- `건조함` concern 유저 → `보습` 효능 상품 추천 가능 (현재 불가)
- BEE_ATTR `보습력`과 concern `건조함` 연결 → keyword/brand 외에 "당신의 건조함 고민에 맞습니다" 설명 가능
- 추천이 브랜드+카테고리+성분에만 의존하는 현재 한계 탈피

### 2순위: BEE Attribution KG 내부 전파 완성

**현재 상태**: adapter gate + emitter defense로 serving은 보호됨. 하지만 KG evidence graph 내부에서 linked/unlinked 구분 불가.

**해결 시 효과**:
- evidence graph에서 linked/unlinked BEE 시각 구분 → QA/디버깅 용이
- extractor recall 개선 시 unlinked BEE 패턴 분석 가능
- 외부 시스템이 evidence graph를 받아갈 때 정보 손실 없음

### 3순위: Keyword 군집화/대표값 정규화

**현재 상태**: keyword가 자유 워딩 그대로 (`순하`, `촉촉`, `촉촉함` 등 별개 존재). 의미적 중복으로 keyword 매칭률 저하.

**해결 시 효과**:
- 유사 keyword 수렴 → keyword_match contribution 실질화
- 범용 단어 필터링 → 시그널 품질 향상
- NLP 의존 작업이라 공수 가장 큼
