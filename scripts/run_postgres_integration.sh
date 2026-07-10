#!/usr/bin/env bash
#
# Wave 4 Task 6: Run GraphRapping Postgres integration tests.
#
# Two modes:
#   1. LOCAL  — if GRAPHRAPPING_TEST_DATABASE_URL is already set in the
#               environment, the script uses that DB as-is (no Docker spawn).
#               Recommended for laptop dev against the local Postgres 16 on
#               localhost:5432.
#   2. DOCKER — otherwise, the script launches an ephemeral postgres:16
#               container, exports GRAPHRAPPING_TEST_DATABASE_URL, runs the
#               suite, and tears the container down on exit.
#
# Example (local):
#   createdb -h localhost -U postgres graphrapping
#   export GRAPHRAPPING_TEST_DATABASE_URL="postgresql://postgres:postgres@localhost:5432/graphrapping"
#   ./scripts/run_postgres_integration.sh
#
# Example (Docker fallback, CI-friendly):
#   ./scripts/run_postgres_integration.sh
#
set -euo pipefail

# Wave 4 PG-bound test set. Add new files here so both local and Docker
# runs cover the same surface. Keep the PG-gated block below in lockstep with
# the `postgres-service` job in .github/workflows/ci.yml.
PG_TESTS=(
  # PG-gated suites — must match ci.yml `postgres-service` exactly (the
  # canonical Postgres surface). Each self-skips without
  # GRAPHRAPPING_TEST_DATABASE_URL, so they only truly run here / in that job.
  tests/test_postgres_integration.py
  tests/test_wave1_integration_smoke.py
  tests/test_dirty_product_propagation.py
  tests/test_incremental_watermark_safety.py
  tests/test_sql_prefilter_avoided.py
  tests/test_candidate_prefilter_equivalence.py
  tests/test_full_load_db.py
  tests/test_incremental_pipeline_db.py
  tests/test_pipeline_lock.py
  tests/test_source_identity_collision.py
  tests/test_retention_monitor.py
  # NOT PG-gated: these also run in the CI quality job (no DB). Kept here for
  # extra local/Docker coverage; intentionally omitted from ci.yml
  # postgres-service so CI doesn't run them twice.
  tests/test_master_upsert_completeness.py
  tests/test_incremental_cleanup_wiring.py
  tests/test_stale_agg_soft_delete.py
)

run_tests() {
  python -m pytest "${PG_TESTS[@]}" -q --timeout=400
}

# LOCAL mode — caller already pointed us at a database.
if [[ -n "${GRAPHRAPPING_TEST_DATABASE_URL:-}" ]]; then
  # Mask credentials: print scheme + host/db only, not user:password.
  redacted_url="$(printf '%s' "${GRAPHRAPPING_TEST_DATABASE_URL}" | sed -E 's#(://)[^@]+@#\1<redacted>@#')"
  echo "[run_postgres_integration] LOCAL mode — using ${redacted_url}"
  run_tests
  exit $?
fi

# DOCKER mode — spawn ephemeral postgres:16.
IMAGE="${POSTGRES_IMAGE:-postgres:16}"
CONTAINER_NAME="${CONTAINER_NAME:-graphrapping-postgres-it-$$}"
POSTGRES_USER="${POSTGRES_USER:-postgres}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
POSTGRES_DB="${POSTGRES_DB:-graphrapping_test}"

CONTAINER_ID=""

cleanup() {
  if [[ -n "${CONTAINER_ID}" ]]; then
    docker rm -f "${CONTAINER_ID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT

echo "[run_postgres_integration] DOCKER mode — launching ${IMAGE}"
CONTAINER_ID="$(
  docker run --rm -d \
    --name "${CONTAINER_NAME}" \
    -e "POSTGRES_USER=${POSTGRES_USER}" \
    -e "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
    -e "POSTGRES_DB=${POSTGRES_DB}" \
    -p 127.0.0.1::5432 \
    "${IMAGE}"
)"

for _ in {1..60}; do
  if docker exec "${CONTAINER_ID}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

if ! docker exec "${CONTAINER_ID}" pg_isready -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" >/dev/null 2>&1; then
  echo "Postgres did not become ready in time." >&2
  docker logs "${CONTAINER_ID}" >&2 || true
  exit 1
fi

HOST_PORT="$(docker port "${CONTAINER_ID}" 5432/tcp | sed 's/.*://')"
export GRAPHRAPPING_TEST_DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${HOST_PORT}/${POSTGRES_DB}"

run_tests
