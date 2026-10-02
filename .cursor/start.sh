#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Per-boot runtime reconciliation (idempotent).
#
# Brings up the local PostgreSQL server, ensures the application role and
# database exist, and applies migrations. Tolerates being run repeatedly and
# against an already-running cluster, then returns so the terminals (API and
# web dev servers) can start.
# ---------------------------------------------------------------------------
set -euo pipefail

cd /workspace

# Start the PostgreSQL 16 cluster. Already-running is not an error here.
sudo pg_ctlcluster 16 main start 2>/dev/null || true

# Wait until PostgreSQL accepts connections before touching it.
for _ in $(seq 1 30); do
    if sudo -u postgres pg_isready -q; then
        break
    fi
    sleep 1
done

# Ensure the application role exists. "lateral" is a reserved SQL keyword, so
# the identifier must be quoted.
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='lateral'" | grep -q 1; then
    sudo -u postgres psql -c "CREATE ROLE \"lateral\" LOGIN PASSWORD 'lateral' CREATEDB;"
fi

# Ensure the application database exists, owned by that role.
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='lateral'" | grep -q 1; then
    sudo -u postgres createdb -O lateral lateral
fi

# Bring the schema to head. Idempotent: a no-op when already current.
# shellcheck disable=SC1091
. .venv/bin/activate
alembic upgrade head

echo "[start] PostgreSQL ready and migrations applied"
