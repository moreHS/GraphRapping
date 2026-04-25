# Postgres Integration Verification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 Postgres에서 migration, per-review persistence, snapshot replay, serving mart persistence가 깨지지 않는지 검증할 수 있는 opt-in 통합 테스트를 추가한다.

**Architecture:** 기본 unit test/CI는 DB 없이 계속 통과해야 한다. 실제 DB 검증은 `GRAPHRAPPING_TEST_DATABASE_URL`이 설정된 경우에만 실행하고, 테스트마다 임시 schema를 만들어 public schema와 운영 데이터를 건드리지 않는다.

**Tech Stack:** Python 3.11, pytest-asyncio, asyncpg, PostgreSQL.

---

## 배경

이전 P0/P1/P2 작업은 대부분 fake repo, source inspection, in-memory pipeline 기준으로 계약을 보강했다. 하지만 실제 Postgres에서는 다음 종류의 문제가 뒤늦게 드러날 수 있다.

- DDL 실행 순서/컬럼 누락
- asyncpg JSONB/text[] 인코딩 불일치
- FK 순서 문제
- `ReviewPersistBundle` 저장 후 snapshot reverse transform 불일치
- serving mart 신규 필드 DB round-trip 누락

## 비목표

- Docker/Postgres 자동 기동은 이번 범위에서 제외한다.
- production `DATABASE_URL`을 암묵적으로 사용하지 않는다.
- 전체 end-to-end recommender 품질 검증은 하지 않는다.
- 대용량 fixture나 성능 벤치마크는 포함하지 않는다.

## 변경 범위

- 신규 opt-in test: `tests/test_postgres_integration.py`
- JSONB 실 DB 인코딩 보정: `src/db/repos/review_repo.py`
- 실행 문서: `README.md`
- 로드맵 갱신: `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

## 검증할 계약

1. `migrate(pool)`가 fresh schema에서 전체 DDL을 순서대로 실행하고 `schema_migrations`를 기록한다.
2. 핵심 vNext 컬럼이 실제 DB에 존재한다.
   - `rel_raw.raw_sentiment`, `rel_raw.obj_keywords`
   - `canonical_fact.evidence_kind`, `canonical_fact.fact_status`, `canonical_fact.target_linked`
   - `wrapped_signal.evidence_kind`, `wrapped_signal.fact_status`, `wrapped_signal.source_confidence`
   - `serving_product_profile.variant_family_id`, `serving_product_profile.representative_product_name`
   - `serving_user_profile.owned_family_ids`, `serving_user_profile.repurchased_family_ids`
3. `persist_review_bundle()`가 L1/L2/L2.5/evidence를 실제 DB에 저장한다.
4. `load_full_review_snapshot()`가 DB child rows를 loader-compatible 형태로 복원한다.
5. `persist_aggregates()`가 product/user serving profile 신규 필드를 실제 DB에 보존한다.

## Tasks

- [x] Add isolated temporary-schema Postgres test fixture gated by `GRAPHRAPPING_TEST_DATABASE_URL`.
- [x] Add migration/schema contract integration test.
- [x] Add review bundle persist + snapshot + signal metadata round-trip integration test.
- [x] Add serving product/user profile round-trip integration test.
- [x] Fix JSONB write encoding discovered by the real-DB contract where needed.
- [x] Document the opt-in command in `README.md`.
- [x] Run DB-less default suite and lint.

## Rollback

- 통합 테스트가 기본 CI를 느리게 하거나 DB 없이 실패하면 env-gating을 우선 수정한다.
- 실제 DB에서 발견된 repo 인코딩 보정이 기존 unit behavior를 깨면 보정 helper만 되돌리고 테스트는 xfail/문서화하지 않는다. 대신 원인과 재현 명령을 completion record에 남긴다.

## Completion Record

Date: 2026-04-25

Changed files:
- `tests/test_postgres_integration.py`
- `scripts/run_postgres_integration.sh`
- `src/db/repos/review_repo.py`
- `README.md`
- `DECISIONS/2026-04-25_postgres_integration_verification_plan.md`
- `DECISIONS/2026-04-25_docker_postgres_integration_execution_plan.md`
- `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

Implementation notes:
- Postgres tests require explicit `GRAPHRAPPING_TEST_DATABASE_URL`; production `DATABASE_URL` is not used implicitly.
- Each test creates a unique `graphrapping_it_<uuid>` schema and drops it with `CASCADE` after the test.
- `review_raw.raw_payload` and `review_raw_history.raw_payload` are encoded as JSON strings before asyncpg writes to JSONB columns.

Verification:
- `python -m ruff check src tests/test_postgres_integration.py` -> `All checks passed!`
- `python -m pytest tests/test_postgres_integration.py -q` -> `3 skipped` without DB URL
- `bash scripts/run_postgres_integration.sh` -> `3 passed` with Docker-backed Postgres
- `python -m ruff check src` -> `All checks passed!`
- `python -m pytest tests/ -q` -> `324 passed, 3 skipped`

Remaining issues:
- External/shared Postgres execution still requires an explicit developer-provided `GRAPHRAPPING_TEST_DATABASE_URL`.
- Docker-backed execution is now available, but still depends on Docker Desktop and local/available `postgres:16`.
