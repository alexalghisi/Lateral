#!/bin/sh
# ---------------------------------------------------------------------------
# API container entrypoint.
#
# ARCHITECTURAL DECISION -- migrations run here, before the server starts.
#
# For a single-instance deployment this is the simplest correct arrangement:
# the schema is guaranteed to match the code before the process accepts its
# first request, and there is no window in which new code queries an old table.
#
# The trade-off is explicit and worth stating: with multiple replicas, every
# replica would race to run `alembic upgrade head` on the same database.
# Alembic takes a lock on its version table so the outcome is safe rather than
# corrupt, but the losers stall on startup. A horizontally scaled deployment
# should instead run migrations as a dedicated one-shot job that must complete
# before the API rollout begins. That is a deployment-topology change, not an
# application change, which is precisely why this logic lives in the entrypoint
# and not inside the application.
#
# `set -e` matters: without it a failed migration would be logged and the
# server would start anyway, against a schema it does not match.
# ---------------------------------------------------------------------------
set -eu

echo "[entrypoint] Applying database migrations..."
alembic upgrade head

echo "[entrypoint] Starting Uvicorn (${UVICORN_WORKERS:-2} worker(s))..."

# `exec` replaces the shell with Uvicorn so the server becomes PID 1 and
# receives SIGTERM directly. Without it, the shell holds PID 1, ignores the
# signal, and every deploy ends in a 10-second timeout and a SIGKILL that drops
# in-flight requests instead of draining them.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers "${UVICORN_WORKERS:-2}" \
    --log-level "${LOG_LEVEL:-info}" \
    --proxy-headers \
    --forwarded-allow-ips '*'
