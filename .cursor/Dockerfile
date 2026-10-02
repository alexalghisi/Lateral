# ---------------------------------------------------------------------------
# Cloud Agent base image for Lateral.
#
# Provides the stable system toolchain the project needs: Python 3.12 (the
# version the app targets and CI uses), Node.js 22 (the frontend toolchain),
# and a local PostgreSQL 16 server (the app and its 130-test suite run against
# real PostgreSQL, never SQLite). Project dependencies themselves are installed
# from lockfiles in the repository-bootstrap `install` step, not baked here, so
# the image stays cache-friendly and does not go stale against the source.
# ---------------------------------------------------------------------------
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

# System toolchain and a local PostgreSQL server. build-essential/python3-dev
# cover any package that needs to compile a C extension during `pip install`.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gnupg \
        sudo \
        build-essential \
        python3 \
        python3-venv \
        python3-dev \
        postgresql \
        postgresql-contrib \
    && rm -rf /var/lib/apt/lists/*

# Node.js 22 from NodeSource (Ubuntu's own package is too old for the frontend).
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Cursor runs commands as `ubuntu` (uid 1000, already present on this base).
# Passwordless sudo lets the start script manage the local PostgreSQL cluster.
RUN echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/ubuntu \
    && chmod 0440 /etc/sudoers.d/ubuntu

USER ubuntu
