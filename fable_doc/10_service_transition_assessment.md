# 서비스화 전환 평가 — 사전 계산 분리와 실 DB 플로우 (2026-07-19)

상태: 평가·갭 정리(구현 아님) · 발단: 사용자 질문 "유사도 같은 사전 계산이
배치로 독립 가능하게 모듈화되어 있나 / 실 DB(유저·리뷰·상품마스터) 기준으로
지금 로직을 그대로 쓸 수 있나".

## 1. 판정 요약

- **사전 계산 모듈화**: 계산(순수 함수, `src/rec/product_similarity.py`)은
  저장소-무관으로 이미 모듈화. 현재 호출 시점은 로드타임(데모 적재 1회 / DB
  `_refresh` 주기)이며 요청 경로 계산이 아님. 배치 독립에 빠진 한 겹은
  **산출물 영속화**뿐(Phase 8 결정 3이 "ephemeral 우선, 영속은 실측/SLA 후"로
  의도적으로 미룬 자리). 소비자 4곳(그래프뷰/위젯/boost/관련상품)은 attach·
  사이드카 인터페이스만 보므로 영속화 도입 시 무변경.
- **실 DB 플로우**: 5레이어 파이프라인·승격·evidence-first 추천·provenance
  로직은 **그대로 사용**. DB 전 체인이 이미 존재(migrate / full-load /
  incremental[워터마크, DB=단일 진실] / validator / monitor + 서빙 모드
  `GRAPHRAPPING_SERVING_MODE=db`). 교체 대상은 로직이 아니라 **입력 커넥터
  3개**(리뷰 트리플·상품마스터·유저 프로파일 — 파일을 읽던 입구).

## 2. 현재 플로우 (구현 기준)

```
파일 3개(rs.jsonl 트리플 / product_catalog_es.json / user_profiles_*.json)
  → cli migrate → cli full-load  … 5레이어 전체 PG 영속
                                   (canonical_fact, wrapped_signal, 집계/승격,
                                    serving_product/user_profile, provenance)
  → cli incremental              … 워터마크 기반 증분(신규 리뷰만)
  → contract validator / monitor / 실패 웹훅(env)
서빙: DBServingStore(서빙 테이블 read-only, 300s refresh+serve-stale,
     로드타임에 유사도 gated+ungated 계산) — 요청 경로는 후보·스코어·설명만
프론트: 정적 파일(이미 독립)
```

## 3. 목표 플로우 (실 DB 기준)

```
[일/시간 배치]
 ① 입력 동기화(신설): 리뷰 트리플 적재(동일 포맷 어댑터) ·
    상품마스터/ES sync · 유저 프로파일 sync(scripts/fetch_user_profiles_pg.py가 원형)
 ② cli incremental (그대로)
 ③ 유사도 산출물 영속화(신설, 얇음 — 계산 함수 재사용)
 ④ validator + monitor + 웹훅 (그대로)
[서빙 API — 독립 배포] 테이블+산출물 로드만, refresh 백그라운드화(A3 단서)
[프론트 — 독립 배포] 정적 파일
```

부수 효과: 유사도가 ③으로 빠지면 A3 스케일 실측(1만 상품 ungated ~39s)은
배치 비용이 되어 우려 소멸(사용자 판정과 정합).

## 4. 갭 목록 (서비스화 전 필요 작업)

| # | 갭 | 규모 | 비고 |
|---|---|---|---|
| 1 | 유사도 산출물 영속화 + 서빙 로더 | S~M | Phase 8 결정 3 예정 항목. 테이블 vs 아티팩트는 설계 시 결정 |
| 2 | DBServingStore refresh 백그라운드화 | S | 현재 스테일 시 첫 요청이 refresh 비용 부담 — A3 단서로 등재됨 |
| 3 | 입력 커넥터 3종 | M | 리뷰=rs.jsonl 동일 포맷 어댑터 / 상품=마스터 sync / 유저=fetch 스크립트 일반화(가명화·프라이버시 규칙 재사용) |
| 4 | 배치 스케줄링·오케스트레이션 | S~M | 잡은 CLI로 실존, 스케줄러(cron/Airflow)로 묶는 작업 |
| 5 | retention 잡 구현 | M | R=24 확정(2026-07-10). 트리거 = 실데이터 연속 적재 시작 |
| 6 | glb 채널 온보딩(name_hint D안) | M | 조건부 — glb 데이터 유입 시 |

관련 확정 결정: ephemeral 우선(2026-07-15 논의록 결정 3) · R=24
([retention](../DECISIONS/2026-07-08_retention_policy_and_cleanup_default.md)) ·
glb D안([결정](../DECISIONS/2026-07-08_glb_channel_identity_strategy.md)) ·
A3 사전계산 수용([08 §판정 정정](08_remaining_items_review_2026-07-18.md)).

## 5. 착수 순서 권고 (계획 수립 시)

1→2는 서비스화와 무관하게 미리 해도 무해(현 데모 무변경·게이트 보호 하에).
3→4는 실 소스 접근권/스펙 확정 후. 5는 트리거 조건 그대로. 상세 계획은
착수 결정 시 별도 plan 문서로(크로스리뷰 포함).
