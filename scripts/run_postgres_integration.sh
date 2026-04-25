#!/usr/bin/env bash
set -euo pipefail

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

python -m pytest tests/test_postgres_integration.py -q
