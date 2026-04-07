# Step 5 상세 실행 작업지시서 — `source_fact_ids` 점진 제거

## 1. 목적
signal provenance의 정본을 `signal_evidence`로 완전히 통일하고,
`source_fact_ids`는 단계적으로 제거한다.

현재 방향성은 이미 좋다.
`signal_emitter.py`와 DDL 주석은 `signal_evidence`를 provenance SoT로 두고,
`source_fact_ids`는 cache/debug field라고 설명한다.
다만 field가 여전히 스키마와 런타임에 남아 있으므로,
정합성 관점에서 점진 제거가 바람직하다.

---

## 2. 목표
1. read path에서 `source_fact_ids` 의존을 제거한다.
2. aggregate / explainer / debug path가 `signal_evidence`만으로 provenance를 따라가게 한다.
3. `source_fact_ids`는 deprecated 상태로 두고, 마이그레이션 계획 후 제거한다.

---

## 3. 현재 상태 요약
- `wrapped_signal`에는 `source_fact_ids`가 남아 있다.
- `signal_evidence`는 별도 테이블로 존재한다.
- SoT는 사실상 `signal_evidence`지만 cache field도 계속 채워진다.

---

## 4. 수정 대상 파일

### 핵심 파일
- `sql/ddl_signal.sql`
- `src/wrap/signal_emitter.py`
- `src/db/repos/signal_repo.py`
- `src/db/repos/provenance_repo.py`
- `src/rec/explainer.py`
- `src/mart/aggregate_product_signals.py`
- `tests/test_provenance_fidelity.py`

### 선택적 신규 파일
- `sql/migrations/XXXX_drop_source_fact_ids.sql`

---

## 5. 상세 구현 지시

### 5-1. runtime read path에서 `source_fact_ids` 의존 제거
#### 수정 파일
- `src/rec/explainer.py`
- `src/db/repos/provenance_repo.py`
- `src/mart/aggregate_product_signals.py`

#### 해야 할 일
provenance가 필요할 때는 반드시:
`signal_id -> signal_evidence -> fact_id -> fact_provenance -> raw snippet`
경로만 사용한다.

#### 금지
- `source_fact_ids[0]` 같은 shortcut 사용
- debug/evidence_sample을 `source_fact_ids`에서 직접 뽑기

---

### 5-2. `signal_emitter.py`에서 cache field downgrade
#### 수정 파일
- `src/wrap/signal_emitter.py`

#### 해야 할 일
- `source_fact_ids`를 유지하더라도 optional/debug-only field로 취급
- merge 시 append를 계속할지 재검토
- 가능하면 config/flag로 cache population off 가능하게 만들기

#### 목적
정본이 하나라는 철학을 코드에 반영한다.

---

### 5-3. `ddl_signal.sql`에 deprecation 주석 명시
#### 해야 할 일
`source_fact_ids` 컬럼 주석에 아래 수준의 문구 추가:

```sql
-- DEPRECATED: cache/debug only. Source of truth for signal provenance is signal_evidence.
```

필요하면 다음 단계 migration 계획도 TODO로 넣는다.

---

### 5-4. aggregate/explainer에서 provenance sample을 `signal_evidence` 기반으로 재구성
#### 수정 파일
- `src/mart/aggregate_product_signals.py`
- `src/rec/explainer.py`

#### 해야 할 일
evidence sample이 필요하면 `signal_evidence`에서 top-k fact를 조회한 뒤, `fact_provenance`로 snippet을 가져온다.

#### 목적
provenance 정합성을 cache field에 의존하지 않게 한다.

---

### 5-5. 최종 제거 준비
#### 단계적 계획
1. read path 제거
2. write path cache-only downgrade
3. migration script 준비
4. field drop

이번 사이클에 실제 drop까지 안 해도 된다.
최소한 **모든 주요 read path**에서 제거하는 게 목표다.

---

## 6. Acceptance Criteria
1. explainer/provenance/aggregate 주요 read path가 `signal_evidence`만 사용한다.
2. `source_fact_ids`는 cache/debug 전용으로만 남는다.
3. DDL과 코드 주석이 provenance SoT를 일관되게 설명한다.
4. `source_fact_ids`를 비워도 주요 explanation/provenance 테스트가 통과한다.

---

## 7. 테스트 항목

### 7-1. `tests/test_provenance_fidelity.py`
- `signal_evidence -> fact_provenance -> raw snippet` 경로만으로 explanation이 복원되는지 검증

### 7-2. 신규/확장 테스트
- `source_fact_ids=[]`인 signal에서도 provenance path가 동작하는지 확인
- aggregate evidence sample이 `signal_evidence` 기반으로 생성되는지 확인

---

## 8. 완료 후 검토 포인트
- migration 타이밍 (호환성 고려)
- debug tooling에서 cache field가 꼭 필요한지 여부
