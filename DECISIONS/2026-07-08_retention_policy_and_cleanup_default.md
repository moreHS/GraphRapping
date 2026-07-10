# Phase 1.3 — Retention 정책과 cleanup 기본값 (설계, 구현 아님)

작성일: 2026-07-08 · 상태: 모니터링 구현 완료 · **R=24개월 확정 (2026-07-10, 문서 말미 확정 기록 참조)** ·
cleanup 기본값은 Option A 유지→적재 시작 시 Option B 전환으로 확정 · TTL/파티셔닝 구현은 Phase 5 백로그

## 배경

`docs/architecture/db_consumer_contract.md` §12.3에 무한 누적 위험 3종이 문서화만 되어
있었다.

1. `quarantine_*` 5종 테이블(`sql/ddl_quarantine.sql`)에 TTL이 없음 — 일일 배치마다
   행이 추가되고 정리되는 경로가 없다.
2. `agg_product_signal`(특히 `all` window) 영구 누적 — 90일 stale cleanup
   (`mark_stale_agg_signals_inactive`, [mart_repo.py:333](../src/db/repos/mart_repo.py))이
   `GRAPHRAPPING_AGG_CLEANUP_ENABLED=1` opt-in이고, 인기 상품은 `last_seen_at`이 매일
   갱신되어 cleanup이 활성화되어도 해당 행은 영원히 `is_active=true`로 남는다.
3. `ner_raw` / `bee_raw` / `rel_raw`가 `review_version`마다 append-only로 누적
   (`sql/ddl_raw.sql`).

`fable_doc/03_improvement_plan.md` Phase 1.3(크로스 리뷰 지적 #5 반영, 참고
`fable_doc/04_cross_review_log.md`)은 fixture 단계에서 retention을 "구현"하는
것을 과잉으로 판단하고, 이번 범위를 다음으로 제한했다.

- **모니터링** (조회 전용) — `src/db/retention_monitor.py`로 구현 완료. 이 문서와
  별도 산출물이며, 아래 "모니터링 임계 초기값" 절에서 그 기본값의 근거만 설명한다.
- **cleanup 기본 정책 결정** — 이 문서.
- **TTL 설계** (구현 아님) — 이 문서.

리뷰 max 보존 기간(이하 R)은 아직 사용자가 확정하지 않았다(Wave 6/Phase 5 착수
조건). 따라서 TTL 설계는 R을 파라미터로 둔 채로 제시하고, 실제 구현 착수는
Phase 5 백로그(실데이터 연속 적재 시작 + R 확정) 조건이 충족된 뒤로 미룬다.

## 검토한 선택지 — cleanup 기본 정책

현재 `GRAPHRAPPING_AGG_CLEANUP_ENABLED`는 opt-in(기본 비활성)이다.
`_maybe_run_stale_cleanup`([run_incremental_pipeline.py:386](../src/jobs/run_incremental_pipeline.py))이
이 플래그가 `"1"`일 때만 `mark_stale_agg_signals_inactive`를 호출하며, 임계값은
`GRAPHRAPPING_AGG_CLEANUP_DAYS`(기본 90일)이다.

### Option A — 현행 유지: opt-in 기본 비활성

- 장점: cleanup은 파괴적이지 않지만(soft-delete, `DELETE` 아님) `is_active=false`
  전환은 serving profile 재구성을 유발하므로 부작용 표면적이 있다. fixture/개발
  환경에서 예기치 않은 soft-delete로 인한 회귀 디버깅 혼란이 없다. opt-in 상태로
  이미 수개월간 안정 운영되었고 전용 테스트(`tests/test_incremental_cleanup_wiring.py`)로
  방어된다.
- 단점: 운영자가 명시적으로 켜지 않으면 실데이터 연속 적재 시작과 동시에 위험 #2가
  현실화한다. "opt-in을 깜빡함"이 실패 모드가 된다.

### Option B — 기본 활성 전환 + dry-run 모드 추가

- 장점: 새 배포가 기본적으로 안전하다. 연속 적재 시작 시 운영자의 추가 조치가
  필요 없다.
- 단점:
  - 기존 fixture 기반 테스트/데모 워크플로우가 조용히 동작을 바꿀 회귀 위험이 있다.
    안전하게 전환하려면 dry-run 모드(카운트만 계산·로그, 실제 `UPDATE` 없음)를 먼저
    구현해야 하는데, 이는 Phase 1.3의 "설계까지만" 경계를 넘는 코드 변경이라 이번
    스코프에서 만들지 않았다.
  - `threshold_days=90` 고정값이 R(리뷰 max 보존 기간) 결정과 아직 분리되어 있다.
    R 확정 전에 기본 활성화하면 "왜 90일인가"에 대한 근거가 임시방편이 된다.

### Option C — 기본 활성화하되 threshold_days를 보수적으로(예: 365일) 설정

- 장점: Option B의 회귀 위험을 완화한다 — 대부분의 fixture/데모 시나리오에는 365일
  이상 묵은 데이터가 없어 실질적으로 cleanup이 트리거되지 않는다.
- 단점: 트리거되지 않는 한 opt-in과 결과가 같아 "기본 활성화"의 의미가 희석된다.
  그럼에도 운영자가 별도 조치 없이 장기적으로 안전망을 얻는다는 점은 유효하다.

## 결정 — 권고안 제시, 사용자 확정 대기

**당분간 Option A(opt-in 유지)를 그대로 둘 것을 권고한다.** 근거:

1. fixture 단계이며 실데이터 연속 적재가 아직 시작되지 않았다 — 위험 #1은 현재
   휴면 상태다(`fable_doc/03_improvement_plan.md` 우선순위 원칙 참고).
2. 안전한 기본 활성화에는 dry-run 모드가 선행되어야 하는데, 그 구현은 이번 Phase 1.3
   스코프("설계까지만") 밖이다 — 순서상 dry-run이 먼저다.
3. `threshold_days`의 근거가 될 R이 아직 없다. Phase 5 조건인 "리뷰 max 보존 기간
   사용자 지정"이 선행되어야 90일(혹은 다른 값)이라는 숫자에 정당성이 생긴다.

**단, 실데이터 연속 적재를 시작하는 시점에는 Option B(dry-run 포함 기본 활성화)로
전환할 것을 권고한다.** 착수 조건: (a) R 확정 (b) dry-run 모드 구현 (c)
`src/db/retention_monitor.py`의 `agg_product_signal.<window>.active` 경고가 실제
운영 데이터에서 관측되는지 확인.

**이 권고안은 최종 결정이 아니다.** cleanup 기본값 전환 여부와 그 시점은 사용자
확정 대기 상태로 남긴다. 사용자가 (1) 지금 기본 활성화로 전환할지 (2) 어떤
`threshold_days`를 쓸지 답하면 이 문서를 갱신하거나 새 DECISIONS로 대체한다.

## TTL 설계 (구현 아님) — R을 파라미터로 둔 설계

R = "리뷰 max 보존 기간"(사용자 미확정 — `fable_doc/03_improvement_plan.md` Phase 1.3/5,
예시로 "6개월"이 언급된 바 있음). 아래는 R 확정 후 Phase 5에서 구현할 설계이며, 이번
스코프에서는 구현하지 않는다 — DDL 변경, DELETE/DROP/파티션 실행 코드는 존재하지 않는다.

### 1. `quarantine_*` 5종

- **제안: 고정 30일 TTL** (R과 무관하게 독립 결정). quarantine은 "검토 대기열"이지
  원본 데이터가 아니므로 review 보존 정책(R)과 분리해도 무방하다.
- 근거: quarantine 항목의 정상 경로는 사람이 검토해 `status`를
  `RESOLVED`/`REJECTED`로 바꾸거나, `dictionary_growth` 루프(`src/qa/dictionary_growth.py`)가
  흡수하는 것이다. `PENDING`으로 30일 이상 남았다면 이미 그 review 사이클을 놓친
  것이고, 계속 쌓아 둔다고 가치가 늘지 않는다.
- status별 차등 제안:
  - `status IN ('RESOLVED','REJECTED') AND resolved_at < now() - 7일` → 삭제
    (처리 완료 후 짧은 감사 유예만 부여).
  - `status = 'PENDING' AND created_at < now() - 30일` → 삭제 전 아카이브 이관을
    권고(완전 삭제보다 분석 가치 보존 우선 — `dictionary_growth` 학습 입력으로
    재사용될 수 있다).
- 구현 방법(Phase 5): 배치 `DELETE`. quarantine 5종은 `review_raw`/`ner_raw` 대비
  절대량이 작고 성장 속도도 낮아 파티셔닝은 과잉이다.

### 2. `agg_product_signal` all-window TTL

- 핵심 문제: 현행 `is_active=false` 전환은 "삭제"가 아니라 "숨김"이라 물리적 누적을
  막지 못한다. TTL은 이와 별도로 필요하다.
- **제안: 2단계 정책.**
  1. (현행 유지) `last_seen_at < now() - 90일` → soft-delete(`is_active=false`).
  2. (신규, Phase 5) `is_active=false AND updated_at < now() - 180일` → 물리 `DELETE`.
  "죽은 지 오래된 행"만 정리하고, "최근에 죽은" 행은 재활성화 여지를 남긴다 — 재
  upsert 시 `ON CONFLICT ... DO UPDATE`(`EXCLUDED`)로 자동 재활성화되는 기존 동작
  ([ddl_mart.sql:238-239](../sql/ddl_mart.sql) 주석: "Re-upsert (EXCLUDED)
  reactivates")과 일관성을 유지하기 위함이다.
- `all` window를 특별히 취급하는 이유: `30d`/`90d`는 재계산될 때마다 조합이 자연
  교체되지만, `all`은 한 번 발생한 `(product, edge_type, dst_node)` 조합이 그
  상품이 활성인 한 계속 존재할 근거를 가지므로 가장 크게 누적된다.
- R과의 관계: R이 "리뷰 원본 보존 기간"으로 확정되면, 그 리뷰에서 나온 신호도 같은
  시점 이후에는 evidence 근거가 사라지는 것이 일관적이다(원본 리뷰는 없는데 신호만
  남는 모순 방지). 180일이라는 2단계 buffer는 R 확정 후 재조정 대상이다.

### 3. `ner_raw` / `bee_raw` / `rel_raw` 파티셔닝

- **제안: `review_raw`의 리뷰 생성 시점(월별) 기준 range partition.** append-only
  구조이므로 오래된 파티션을 `DROP`하는 것이 `DELETE`보다 훨씬 저렴하다(인덱스
  재구성 없음).
- **파티션 보존 기간: R + 버퍼(예: R + 1개월)**. `review_raw` 자체가 R 이후
  제거된다면(그 자체의 retention 정책은 이 문서 범위 밖이다 — `review_raw_history`가
  이미 "immutable audit ledger"로 설계되어 있어([ddl_raw.sql:136](../sql/ddl_raw.sql)
  주석) 원본 삭제 이후에도 감사 이력은 별도로 남는다), 자식 테이블(`ner`/`bee`/`rel_raw`)도
  같은 기준을 따르되 파이프라인 처리 지연을 흡수할 버퍼를 더한다.
- 범위 확인: `canonical_fact`/`wrapped_signal`은 `review_id` FK를 갖지만 이 설계의
  파티셔닝 대상이 **아니다** — 각각 diff-based upsert / per-review full-replace로
  이미 안정적이다(`db_consumer_contract.md` §12.1). `ner_raw`/`bee_raw`/`rel_raw`만
  파티셔닝 대상이다.

## 모니터링 임계 초기값과 근거

`src/db/retention_monitor.py`의 기본 임계값이다. 모두 `run_retention_monitor(...)`
호출 시 키워드 인자로 오버라이드 가능하며, 코드 변경 없이 재조정할 수 있다.

| 임계값 | 기본값 | 근거 |
|---|---:|---|
| quarantine 총량 | 20,000 | 906리뷰/517상품 baseline에서 kg_off 9,255행 (`db_consumer_contract.md` §3, 2026-06-18 측정) — 약 2.2배 여유 |
| quarantine 테이블별 | 8,000 | 5개 테이블 중 하나가 총량 대부분을 차지하지 않는다는 가정 하의 단일 테이블 상한. 테이블별 분포 실측치가 없어 총량 임계보다 낮은 보수적 값을 사용 — 실측 후 재조정 필요 |
| `agg_product_signal` window별 active | 10,000 | v260605 lineage(§4, 2026-06-16 측정) 기준 전체 window 합산 6,849행 — 단일 window 몫보다 여유 있게 설정 |
| `agg_user_preference` active | 5,000 | 현 baseline 50명 — 실사용자 규모 데이터 부재로 넉넉한 자리표시자 |
| `review_raw` / `ner_raw` / `bee_raw` / `rel_raw` | 5,000 / 20,000 / 15,000 / 80,000 | v260605 lineage 실측(906 / 4,507 / 2,783 / 20,741)의 약 4배 여유 |
| 테이블 물리 크기(모든 모니터링 테이블 공통) | 500MB | fixture 규모의 실측 바이트 수치가 없다 — "fixture치고 지나치게 크다"는 일반 경보선. 운영 시작 후 `pg_relation_size` 실측치로 재조정 필요 |

**모든 임계값은 fixture 규모 추정치이며 운영 데이터 기준이 아니다.** 실데이터 적재가
시작되면 첫 1~2주 관측치로 재조정해야 한다. 이 재조정은 Phase 5(retention 구현
착수) 이전에도, 그와 무관하게도 수행 가능하다 — 임계값이 함수 인자이므로 호출부
설정만으로 코드 변경 없이 조정된다.

## 트레이드오프

- 이번 스코프는 "측정 가능하게 만듦"과 "정책 방향 제시"까지다. TTL job/파티셔닝을
  실제로 만들지 않기로 한 것은 fixture 단계 과잉 구현을 피하려는 의도적 선택
  (`fable_doc/04_cross_review_log.md` 지적 #5)이지만, 그 대가로 위험 3종은 이 문서
  이후에도 여전히 "휴면 상태로 존재"한다 — 모니터링이 경고를 띄워도 자동으로
  정리되지는 않는다.
- cleanup 기본값을 바꾸지 않기로 한 것(Option A 권고)은 안전한 선택이지만, "언제
  Option B로 전환하는가"에 대한 명시적 트리거가 없으면 영원히 미뤄질 위험이 있다.
  이 문서에 착수 조건(R 확정 + dry-run 구현 + 모니터링 경고 실측)을 명시해 둔
  이유가 이것이다.
- TTL 설계를 R 파라미터로 열어 둔 것은 사용자 결정을 기다리는 정직한 선택이지만,
  R이 확정되기 전까지는 quarantine(30일 고정 제안)을 제외한 나머지 설계는 실행
  가능한 숫자가 없다 — quarantine 정리만 R과 무관하게 먼저 착수 가능하고, agg
  all-window TTL과 raw 파티셔닝은 R 확정 대기 상태로 남는다.
- 모니터링 임계값 전부가 906리뷰/517상품 fixture 실측에서 역산한 여유값이라, 실제
  운영 스케일(상품/리뷰 수 자릿수가 달라지는 시점)에서는 무의미해질 수 있다.
  함수 인자 오버라이드로 재조정 비용은 낮췄지만, "언제 재조정하는가"는 이 문서가
  결정하지 않는다 — Phase 2.3(파이프라인 관측성)에서 정기 리포트화될 때 자연스럽게
  드러날 것으로 기대한다.

## 확정 기록 (2026-07-10, 사용자 승인)

1. **R(리뷰 max 보존 기간) = 24개월 확정.**
   - 근거: 화장품 리뷰의 계절성 제품 사이클 2회 + 리뉴얼 주기 커버. 과거 예시
     언급값 6개월은 계절성 한 사이클도 못 담아 신호 손실이 큼.
   - **단서**: 사내 데이터 보존/개인정보 정책에 24개월보다 짧은 상한이 확인되면
     그 값으로 대체하고 이 문서에 추기한다 (확인 책임: 운영 온보딩 시점).
2. 이 문서의 R-파라미터 설계가 실행 가능한 숫자를 얻음:
   - quarantine: 고정 30일 TTL (RESOLVED/REJECTED 7일, PENDING 30일 아카이브
     권고) — 제안대로 채택
   - agg all-window: 90일 soft-delete(현행) + is_active=false 후 180일 물리
     DELETE — 채택 (180일 버퍼는 운영 실측 후 재조정 가능)
   - raw 파티셔닝(ner/bee/rel): 월별 range partition, 보존 = R+1개월 = **25개월**
3. **cleanup 기본값: 권고대로 확정** — 현행 Option A(opt-in) 유지, 실데이터
   연속 적재 시작 시 Option B(dry-run 포함 기본 활성) 전환. 전환 착수 조건:
   dry-run 모드 구현 + retention_monitor 경고 실측 (R은 본 확정으로 충족).
4. 효과: Phase 5 백로그 "Retention 구현"의 착수 조건 중 사용자 결정분이 해제됨.
   잔여 조건은 "실데이터 연속 적재 시작" 하나.
