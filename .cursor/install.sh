#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Repository bootstrap (idempotent).
#
# Runs after the source is checked out. Installs project dependencies from the
# lockfiles and writes a local, git-ignored .env for host-based development.
# Contains no long-running processes and must terminate.
# ---------------------------------------------------------------------------
set -euo pipefail

cd /workspace

# --- Python (backend) ------------------------------------------------------
python3 -m venv .venv
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt

# --- Node (frontend) -------------------------------------------------------
( cd frontend && npm ci )

# --- Local environment file ------------------------------------------------
# .env is git-ignored (see .gitignore). It carries only local-development
# values: the app reads settings from the environment, and SECRET_KEY has no
# default, so a throwaway key is generated for the local stack. This is not a
# production secret and never leaves the VM.
if [ ! -f .env ]; then
    SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
    cat > .env <<EOF
APP_ENV=local
LOG_LEVEL=info
SECRET_KEY=${SECRET_KEY}
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
POSTGRES_USER=lateral
POSTGRES_PASSWORD=lateral
POSTGRES_DB=lateral
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
EOF
    echo "[install] wrote local .env"
fi

echo "[install] done"
