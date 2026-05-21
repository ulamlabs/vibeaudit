# Frontend build stage
FROM node:20-alpine AS frontend-build

WORKDIR /frontend

# Copy dependency manifests first to leverage Docker layer caching.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build frontend assets.
COPY frontend/ ./
ARG FRONTEND_SENTRY_DSN=""
ENV VITE_SENTRY_DSN=$FRONTEND_SENTRY_DSN
RUN npm run build

# Backend build stage
FROM ghcr.io/astral-sh/uv:python3.13-alpine AS backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local/

WORKDIR /app

# Install only third-party backend dependencies with lockfile in the cacheable layer.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# Copy backend source code, including packaging metadata files such as README.md.
COPY backend/ ./
RUN uv sync --locked --no-dev

# Copy built frontend output into separate Django template/static locations.
RUN mkdir -p ./vibeaudit/templates
COPY --from=frontend-build /frontend/dist/index.html ./vibeaudit/templates/index.html
COPY --from=frontend-build /frontend/dist/static/. ./vibeaudit/static/

# Runtime entrypoint.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Collect static files for WhiteNoise.
RUN uv run python manage.py collectstatic --noinput --clear

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
