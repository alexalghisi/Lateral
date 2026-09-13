# ---------------------------------------------------------------------------
# Frontend + edge image.
#
# ARCHITECTURAL DECISION -- build the React SPA, then serve it from the same
# Nginx that fronts the API.
#
# Serving the static bundle and proxying the API from one origin means the
# browser only ever talks to a single host: every API call is a same-origin
# relative URL, so there is no CORS to configure and no preflight round-trips.
# The build stage produces immutable hashed assets; the runtime stage is a
# stock Nginx that ships only those assets and the proxy config -- no Node, no
# source, no toolchain in the image that faces the internet.
# ---------------------------------------------------------------------------

ARG NODE_VERSION=22-alpine
ARG NGINX_VERSION=1.27-alpine

# =============================== BUILDER ===================================
FROM node:${NODE_VERSION} AS builder

WORKDIR /app

# Copy manifests first so the dependency layer is cached independently of source
# changes -- editing a component then rebuilds in seconds, not minutes.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Now the source, and the production build (tsc typecheck + vite bundle).
COPY frontend/ ./
RUN npm run build

# =============================== RUNTIME ===================================
FROM nginx:${NGINX_VERSION} AS runtime

# The reverse-proxy + static-serving configuration.
COPY docker/nginx/default.conf /etc/nginx/conf.d/default.conf

# The compiled SPA. Nginx serves these directly with a hard cache on hashed
# assets and a no-cache index.html (see default.conf).
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
