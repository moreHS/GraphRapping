# HANDOFF — 현 상태 기준점 (2026-07-20)

새 세션은 이 문서를 먼저 읽고 이어서 진행. (규약: 작업 재개 후 이 문서가
가리키는 상태에서 벗어나면 갱신, 해당 트랙 완결 시 삭제)

## 기준점

- **main = `2204cb6`** (origin 동기화됨 — push는 위임받아 커밋마다 수행 중)
- **게이트**: ruff / mypy(120 files) / pytest **1431 passed, 50 skipped, 0 failed**
- 랭킹 스냅샷·픽스처: 기준 상태(마지막 재승인 = P8-3a 골든, 이후 무변경)
- 데모: 127.0.0.1:8123 라이브(구버전 코드로 기동된 상태일 수 있음 — **재시작은
  사용자 몫**, 검증은 8124+ 스크래치 포트에서)

## 완료된 것 (정본 문서)

| 트랙 | 내용 | 정본 |
|---|---|---|
| Phase 0~8 | 5레이어 파이프라인 → evidence-first 추천 → 그래프 지능화 → 공유노드 유사도(G1~G5) | [fable_doc/09_development_history.md](fable_doc/09_development_history.md) |
| 마감 스윕 | boost-only 4종 retrieval 제외 통일, 브랜드 단독 이웃 미노출, 스케일 실측 | [fable_doc/08](fable_doc/08_remaining_items_review_2026-07-18.md) |
| 구매이력 백필 | 실유저 가명 프로파일 + purchase_events → G4 실발화 39/39 | [DECISIONS/2026-07-18_purchase_history_backfill.md](DECISIONS/2026-07-18_purchase_history_backfill.md) |
| 서비스화 평가 | 사전계산 분리·실 DB 플로우 갭 6종 | [fable_doc/10](fable_doc/10_service_transition_assessment.md) |
| **입력 커넥터 IC-1~3** | 계약 검증기 4종·staging·env 배선·ES 상품 백엔드·유저 K=100 | [fable_doc/plans/2026-07-19_input_connectors_readiness.md](fable_doc/plans/2026-07-19_input_connectors_readiness.md) §완료 보고 |

## 지금 바로 쓸 수 있는 운영 레버

```bash
# 유저 실프로파일 (staging 스냅샷 이미 존재: users/user_profiles_real_20260720.json, K=100)
python scripts/fetch_user_profiles_pg.py --staging          # 재실행=갱신, .env 자격증명
export GRAPHRAPPING_USER_PROFILES_JSON=mockdata/real/users/user_profiles_real_20260720.json

# 상품 실카탈로그 (ES 전량 export → 검증 → 기존 대비 diff → staging)
python scripts/fetch_product_catalog_es.py                  # 스모크 완료: AMORE 33,963 + INNI 11,307
export GRAPHRAPPING_PRODUCT_CATALOG_JSON=<staging 산출 경로>

# 리뷰 landing (파일 백엔드 — rs.jsonl/relation 양 포맷, 누적 스냅샷)
python scripts/fetch_review_triples.py --format rs_jsonl --input <파일/디렉토리>
export GRAPHRAPPING_REVIEW_TRIPLES_JSON=<staging 산출 경로>   # rs는 review_format=rs_jsonl로 소비
```
env 우선순위: 명시적 request > 커넥터 env > (리뷰만) GRAPHRAPPING_DEMO_REVIEW_PATH > fixture.
**env 미설정 = 기존 픽스처 경로 byte-identical** (테스트·스냅샷은 실데이터 무의존).

## 남은 작업 (재개 트리거 순)

1. **리뷰 백엔드** — 트리거: inference-gerter에 **relation 스텝 합류 완료**(사용자
   통보). rs.jsonl 최종 형태/위치(S3 or Snowflake) 확정 → IC-2의
   `ReviewTripleReader`에 백엔드 1개 구현으로 3소스 실로드 전환 완성.
   정찰 기록: SageMaker NER→BEE 배치, `SNF_*_NER_TBL_NAME` Snowflake 테이블 +
   S3 TransformOutput ([DECISIONS/2026-07-20_ic3...](DECISIONS/2026-07-20_ic3_env_and_es_backend.md)).
2. **유사도 산출물 영속화 + refresh 백그라운드화** (갭1·2) — 트리거: 실카탈로그
   전환 전 선행 권장(**실상품 ~45k = 데모의 87배**, 유사도 빌드 O(Σdf²) 실측
   10k≈39s). 지금도 무해하게 선행 가능(fable_doc/10 §4).
3. **D1 collab attach 활성화** — 데이터 준비 완료(실유저 owned). attach 콜사이트
   1개 추가 + 스냅샷 재승인 1회. 결정만 남음.
4. 스케줄러/오케스트레이션(갭4) · retention 잡(갭5 — 트리거: 실데이터 연속 적재
   시작, R=24 확정) · glb 온보딩(갭6 — name_hint D안).
5. **장기 보류**: 0.5 랭킹 라벨(재상정: 체계적 튜닝 개시) · B3 임베딩(초안
   fable_doc/05, 제출=사용자) · Track E 액션/인텐트 본체(외부 모델 스펙 대기).

## 주의사항 (불변 규칙)

- **`scripts/run_906_full_load_db.py` 불가침** — 사용자 소유 미커밋 수정 존재
  (세션들 이전부터). 절대 수정·스테이징·커밋 금지.
- **`.env`** — git 밖(0600), 실 자격증명 보유(ES 4종 + AIBE_DB 6종, 이관
  2026-07-20). 값 출력·커밋 금지. 템플릿은 `.env.example`.
- **`mockdata/real/`** — 전체 git-ignore, 실고객 가명 데이터. 보고서·문서에는
  집계만(행수준 실데이터 금지 — 백필 코덱스 리뷰 P0 규칙).
- **랭킹 변경이 있는 작업**은 스냅샷 diff 전문 보고 → 사용자 재승인 → 골든
  재생성 절차(P8-3a 선례). boost-only는 자격·retrieval 순위 불가 계약(§13).
- **작업 사이클**: 계획(Fable, 실측 기반) → 크로스리뷰(코덱스 CLI:
  `~/.local/bin/codex exec -s read-only -C <dir> - < prompt.txt`) → 구현(Opus
  서브에이전트) → Fable 배치별 완료검토 → 게이트 → 계획서에 완료 보고 → 커밋·push.
- 데모 검증 시 스크래치 서버는 `GRAPHRAPPING_ENABLE_PIPELINE_RUN=1` +
  `POST /api/pipeline/run` 필요, 종료 시 kill.
