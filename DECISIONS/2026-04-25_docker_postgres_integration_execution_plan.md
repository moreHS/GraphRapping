# Docker Postgres Integration Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬 Docker의 `postgres:16` 이미지로 GraphRapping 전용 임시 Postgres를 띄워 실제 integration test를 실행한다.

**Architecture:** 기존 다른 프로젝트의 `compose-postgres-1` 컨테이너는 시작/inspect/수정하지 않는다. GraphRapping 전용 ephemeral 컨테이너를 동적 localhost 포트로 실행하고, 테스트 종료 후 제거한다.

**Tech Stack:** Docker Desktop, PostgreSQL 16 container image, pytest, asyncpg.

---

## 발견 사항

- Docker context: `desktop-linux`
- `/Applications/Docker.app` 존재
- 실행 중인 컨테이너 없음
- 정지된 외부 프로젝트 추정 컨테이너:
  - `compose-postgres-1`
  - image: `postgres:16`
  - previous port: `0.0.0.0:5432->5432/tcp`
- 로컬 image:
  - `postgres:16`

## 안전 원칙

- `compose-postgres-1`는 다른 프로젝트 소유일 수 있으므로 건드리지 않는다.
- 기존 컨테이너의 env inspect는 credential exposure 위험이 있어 수행하지 않는다.
- 새 컨테이너는 `graphrapping-postgres-it-*` 이름만 사용한다.
- host port는 고정 5432가 아니라 Docker dynamic port를 사용한다.
- 테스트 DB URL은 script 내부에서만 `GRAPHRAPPING_TEST_DATABASE_URL`로 export한다.

## 변경 범위

- Create: `scripts/run_postgres_integration.sh`
- Modify: `README.md`
- Modify: `DECISIONS/2026-04-25_postgres_integration_verification_plan.md`
- Modify: `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

## Tasks

- [x] Create Docker-backed integration test runner script.
- [x] Document the script command in `README.md`.
- [x] Run `scripts/run_postgres_integration.sh`.
- [x] Run default DB-less suite after the Docker-backed run.
- [x] Record result and remaining risks in DECISIONS.

## Completion Record

Date: 2026-04-25

Changed files:
- `scripts/run_postgres_integration.sh`
- `README.md`
- `DECISIONS/2026-04-25_docker_postgres_integration_execution_plan.md`
- `DECISIONS/2026-04-25_postgres_integration_verification_plan.md`
- `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

Execution:
- Started Docker Desktop via `open -a Docker`.
- Did not start, inspect env, or modify `compose-postgres-1`.
- Used local `postgres:16` image.
- `scripts/run_postgres_integration.sh` created a temporary `graphrapping-postgres-it-*` container on a dynamic localhost port.
- Temporary container was removed after test execution.

Verification:
- `bash scripts/run_postgres_integration.sh` -> `3 passed`
- `docker ps -a --filter name=graphrapping-postgres-it` -> no remaining containers
- `python -m ruff check src tests/test_postgres_integration.py` -> `All checks passed!`
- `python -m pytest tests/ -q` -> `324 passed, 3 skipped`

Remaining risks:
- The script depends on Docker Desktop being running or startable.
- If `postgres:16` is missing locally, Docker may attempt to pull it.
