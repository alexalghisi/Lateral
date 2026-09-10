# ---------------------------------------------------------------------------
# API image.
#
# ARCHITECTURAL DECISION -- multi-stage build with a virtualenv handoff.
#
# Dependencies are installed in a builder stage and only the resulting
# virtualenv is copied into the runtime stage. pip's cache, its metadata and
# any transient build tooling stay behind in a layer that is never shipped.
#
# The stages are also ordered so that the layer keyed on requirements files sits
# above the layer keyed on source code. Editing a route therefore reuses the
# cached dependency layer, turning a rebuild from minutes into seconds. The
# reverse order -- the common mistake -- reinstalls every dependency on every
# source change.
# ---------------------------------------------------------------------------

# Pinned to a minor version, not `latest` or bare `3`. Reproducibility beats
# convenience: a floating base image means today's green build and tomorrow's
# red build have no diff to explain them.
ARG PYTHON_VERSION=3.12-slim


# =============================== BUILDER ===================================
FROM python:${PYTHON_VERSION} AS builder

# Isolate dependencies in a virtualenv that can be copied wholesale. Copying a
# self-contained directory is far more predictable than trying to replicate
# site-packages across stages.
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /build

# Copy ONLY the dependency manifests first -- this is the cache boundary.
COPY requirements.txt requirements-dev.txt ./

# Runtime dependencies always. Development dependencies only when explicitly
# requested, which lets the same Dockerfile produce both the lean production
# image and the image the test suite runs in, with no second file to keep in
# sync (DRY applied to infrastructure, not just to code).
ARG INSTALL_DEV=false
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_DEV" = "true" ]; then pip install -r requirements-dev.txt; fi


# =============================== RUNTIME ===================================
FROM python:${PYTHON_VERSION} AS runtime

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    # Never write .pyc files: they bloat the image and the container filesystem
    # is discarded anyway.
    PYTHONDONTWRITEBYTECODE=1 \
    # Unbuffered stdout/stderr. Buffered logs are lost when a container is
    # killed, which is exactly when the logs matter most.
    PYTHONUNBUFFERED=1

# Run as an unprivileged user. Container root is not a security boundary; a
# container escape from root is dramatically more useful to an attacker than
# one from uid 1000. Created before the copies so ownership can be set in the
# same layer rather than with a costly recursive chown afterwards.
RUN groupadd --system --gid 1000 lateral \
    && useradd --system --uid 1000 --gid lateral --create-home lateral

COPY --from=builder --chown=lateral:lateral /opt/venv /opt/venv

# Create the working directory with the correct ownership BEFORE `WORKDIR`.
# `WORKDIR` creates missing directories as root, which would leave the
# unprivileged runtime user unable to write anything into its own working
# directory -- a failure that only shows up later, and confusingly (a test
# runner unable to write its cache, a library unable to create a temp file).
RUN mkdir -p /srv/app && chown lateral:lateral /srv/app

WORKDIR /srv/app

COPY --chown=lateral:lateral alembic.ini ./
COPY --chown=lateral:lateral migrations ./migrations
COPY --chown=lateral:lateral app ./app
COPY --chown=lateral:lateral docker/api/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod +x /usr/local/bin/entrypoint.sh

USER lateral

EXPOSE 8000

# Documented default; docker-compose.yml overrides it for local development.
ENV UVICORN_WORKERS=2

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
