# Frontend build stage
FROM node:20-alpine AS frontend-build

WORKDIR /frontend

# Copy dependency manifests first to leverage Docker layer caching.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build frontend assets.
COPY frontend/ ./
RUN npm run build

# Backend build stage
FROM ghcr.io/astral-sh/uv:python3.13-alpine AS backend

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/usr/local/

WORKDIR /app

# Install backend dependencies with lockfile.
COPY backned/pyproject.toml backned/uv.lock ./
RUN uv sync --locked --no-dev

# Copy backend source code.
COPY backned/ ./

# Copy built frontend output into Django static directory; collectstatic will handle discovery.
COPY --from=frontend-build /frontend/build/client/. ./vibeaudit/static/

# Runtime entrypoint.
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Collect static files for WhiteNoise.
RUN uv run python manage.py collectstatic --noinput --clear

EXPOSE 8080

ENTRYPOINT ["/entrypoint.sh"]
CMD ["web"]
