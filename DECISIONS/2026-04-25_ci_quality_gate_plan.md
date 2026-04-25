# CI Quality Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use executing-plans or equivalent task-by-task execution. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로컬에서 안정화한 `ruff`, `mypy`, pytest, Docker-backed Postgres integration 검증을 CI에서 재현 가능하게 만든다.

**Architecture:** 기본 CI job은 push/PR에서 항상 실행한다. Docker-backed Postgres integration은 비용과 Docker 의존성을 고려해 `workflow_dispatch` 수동 실행 job으로 분리한다.

**Tech Stack:** GitHub Actions, Python 3.11, pip editable install, ruff, mypy, pytest, Docker.

---

## 배경

현재 로컬 검증 기준:

```bash
python -m ruff check src
python -m mypy src
python -m pytest tests/ -q
bash scripts/run_postgres_integration.sh
```

이 기준은 로컬에서는 통과하지만, 아직 CI에 고정되어 있지 않아 다른 작업자나 Claude Code가 같은 기준으로 회귀를 잡기 어렵다.

## 비목표

- GitHub Actions 실제 원격 실행은 이 로컬 작업 범위에서 수행하지 않는다.
- Docker-backed Postgres integration을 모든 push마다 강제하지 않는다.
- coverage gate를 추가하지 않는다.
- package import 경로를 `src.*`에서 `graphrapping.*`으로 바꾸지 않는다.

## 변경 범위

- Create: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`
- Update: this plan with completion record

## CI 계약

### Quality job

Triggers:
- `push` to `main` or `master`
- `pull_request`
- `workflow_dispatch`

Steps:
- checkout
- setup Python 3.11
- `python -m pip install --upgrade pip`
- `python -m pip install -e '.[dev]'`
- `python -m ruff check src`
- `python -m mypy src`
- `python -m pytest tests/ -q`

### Postgres integration job

Trigger:
- `workflow_dispatch` only, when `run_postgres_integration` input is true

Steps:
- checkout
- setup Python 3.11
- install dev dependencies
- `docker version`
- `bash scripts/run_postgres_integration.sh`

## Tasks

- [x] Create `.github/workflows/ci.yml`.
- [x] Document CI behavior in `README.md`.
- [x] Validate workflow YAML parses locally.
- [x] Run local quality commands.
- [x] Run Docker-backed Postgres integration locally.
- [x] Record completion and remaining risks.

## Completion Record

Date: 2026-04-25

Changed files:
- `.github/workflows/ci.yml`
- `README.md`
- `DECISIONS/2026-04-25_ci_quality_gate_plan.md`
- `DECISIONS/2026-04-25_audit_priority_execution_roadmap.md`

Implementation notes:
- `quality` job runs on push to `main`/`master`, pull request, and manual dispatch.
- `postgres-integration` job runs only on manual `workflow_dispatch` when `run_postgres_integration` is true.
- Postgres integration reuses `scripts/run_postgres_integration.sh`, so local and CI behavior share one command.

Verification:
- Workflow YAML parse check -> `workflow yaml ok`
- `python -m ruff check src` -> `All checks passed!`
- `python -m mypy src` -> `Success: no issues found in 86 source files`
- `python -m pytest tests/ -q` -> `324 passed, 3 skipped`
- `bash scripts/run_postgres_integration.sh` -> `3 passed`
- `docker ps -a --filter name=graphrapping-postgres-it` -> no remaining containers

Remaining risks:
- GitHub Actions remote execution was not run from this local environment.
- The manual Postgres job may pull `postgres:16` on a fresh runner.
