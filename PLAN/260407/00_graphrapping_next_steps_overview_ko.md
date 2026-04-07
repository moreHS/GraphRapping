# GraphRapping 다음 단계 러프 로드맵

## 기준
이 문서는 최신 `main`과 최근 수정 흐름을 기준으로, 현재 남은 작업을 5개 스텝으로 재정리한 러프 로드맵이다.
방향성은 유지한다.

- Evidence graph(per-review)와 serving graph(corpus-promoted)는 계속 분리한다.
- Product truth는 override하지 않는다.
- User/Product join은 shared concept plane을 통해서만 한다.
- 추천은 promoted signal과 truth를 함께 사용한다.
- 이번 사이클은 “재설계”가 아니라 “활용률/운영성/계약 정교화” 단계다.

---

## Step 1. Texture 정본화
### 목표
Texture를 상위 BEE_ATTR 축과 하위 KEYWORD 표현의 2단 구조로 user/review 양쪽에서 완전히 동일하게 쓰도록 고정한다.

### 목적
현재는 `texture_keyword_map.yaml`과 `keyword_surface_map.yaml`에 texture 관련 규칙이 중복된다.
방향은 맞지만 수동 동기화 구조라 drift 위험이 있다.
Texture는 recommendation/explanation 모두에서 중요한 공통 축이므로 authoritative source를 하나로 고정해야 한다.

### 러프 체크리스트
- `texture_keyword_map.yaml`을 texture 정본으로 지정
- `keyword_surface_map.yaml`의 texture 섹션을 자동 생성 또는 검증 전용으로 전환
- review normalizer와 user adapter가 같은 texture taxonomy version을 쓰게 만들기
- texture sync regression test 추가

---

## Step 2. Family 활용 심화
### 목표
exact SKU / same family other variant / repurchased family를 1급 personalization 신호로 승격한다.

### 목적
현재 family penalty/bonus는 들어갔지만, family는 아직 주로 scorer feature 수준이다.
variant-heavy 뷰티 상품에서는 family 자체가 강한 개인화 단위이므로, candidate generation과 serving profile에서도 더 명시적으로 다뤄야 한다.

### 러프 체크리스트
- family key canonicalization 완전 정리
- same family 다른 variant를 별도 candidate bucket으로 운영
- family summary 신호를 serving profile에 추가
- strict / explore / compare 모드에서 family 규칙 차등화
- family personalization regression test 추가

---

## Step 3. SQL-first 전환
### 목표
기본 candidate/runtime 경로를 Python full scan에서 SQL-first prefilter 경로로 이동시킨다.

### 목적
현재 `generate_candidates_prefiltered()`가 존재하지만 기본 경로는 여전히 Python 리스트 순회에 가깝다.
catalog 규모가 커질수록 병목이 된다.
Postgres-first hybrid라는 원래 방향에 맞춰, 1차 후보 생성과 aggregate recompute를 SQL로 더 밀어야 한다.

### 러프 체크리스트
- `server.py` / recommendation entrypoint에서 prefiltered 경로를 기본값으로 승격
- candidate prefilter SQL 추가
- dirty product aggregate SQL group-by 확장
- Python path와 SQL path 동등성 테스트 추가

---

## Step 4. pycache / repo hygiene 정리
### 목표
저장소 상태를 깔끔하게 만들고 협업·CI 안정성을 높인다.

### 목적
`__pycache__` 같은 산출물이 repo에 남아 있으면 PR diff 품질과 CI 신뢰도가 떨어진다.
지금은 기능 이슈보다 품질/운영성 보강 단계다.

### 러프 체크리스트
- tracked `__pycache__` 제거
- `.gitignore` 보강
- lint/type/test tooling 정리
- README/ARCHITECTURE/CHANGELOG 링크 점검

---

## Step 5. `source_fact_ids` 점진 제거
### 목표
signal provenance의 정본을 `signal_evidence`로 완전히 통일한다.

### 목적
현재 방향성은 이미 `signal_evidence`를 SoT로 잡고 있고 `source_fact_ids`는 deprecated/cache field다.
하지만 스키마와 런타임에 아직 field가 남아 있어 책임이 약간 중복된다.
이걸 단계적으로 걷어내야 explanation/provenance 정합성이 더 좋아진다.

### 러프 체크리스트
- `source_fact_ids` read path 제거
- aggregate/explainer/debug 경로를 `signal_evidence`만 사용하도록 정리
- DDL에서 deprecated 주석 → migration plan → 최종 제거
- provenance regression test 강화

---

## 우선순위 제안
1. Texture 정본화
2. Family 활용 심화
3. SQL-first 전환
4. `source_fact_ids` 점진 제거
5. pycache / repo hygiene

---

## 이번 5스텝 이후 추천 후속 과제
- rs.jsonl raw ingest를 first-class path로 더 강화
- mock assets를 더 강한 regression fixture로 승격
- promoted-only contract를 API/export/debug 전 경로에 확대 검증
- family-aware recommendation을 explanation에서도 더 직접 노출
