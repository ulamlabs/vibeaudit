# VibeAudit project commands

set dotenv-load := true

image_repo := env_var_or_default("IMAGE_REPO", "ghcr.io/ulamlabs/vibeaudit")
image_tag := env_var_or_default("IMAGE_TAG", "latest")
image_ref := image_repo + ":" + image_tag

# Default recipe
default:
    @just --list

# Build local Docker image
build:
    docker build -t {{ image_ref }} .

# Run backend dev server
dev-be:
    cd backend && uv run python manage.py runserver 0.0.0.0:8000

# Alias for backend dev server
dbe: dev-be

# Install backend dependencies
ibe:
    cd backend && uv sync

# Install frontend dependencies
ife:
    cd frontend && npm install

# Install all dependencies
install: ibe ife

# Alias for install
i: install

# Run frontend dev server
dev-fe:
    cd frontend && npm run dev

# Alias for frontend dev server
dfe: dev-fe

# Run local Docker image
run:
    docker run --rm -p 8080:8080 {{ image_ref }}

# Start dev environment via Docker Compose (build if needed)
dc-up:
    docker compose -f docker-compose.dev.yml up --build

# Alias for dc-up
dcu: dc-up

# Stop dev environment
dc-down:
    docker compose -f docker-compose.dev.yml down

# Alias for dc-down
dcd: dc-down

# Build dev Docker images without starting
dc-build:
    docker compose -f docker-compose.dev.yml build

# Run Django migrations inside the running backend container
dc-migrate:
    docker compose -f docker-compose.dev.yml exec backend python manage.py migrate --noinput

# Open a bash shell inside the running backend container
dc-shell:
    docker compose -f docker-compose.dev.yml exec backend sh

# Run Django migrations
migrate:
    cd backend && uv run python manage.py migrate

# Run backend tests (pytest-style)
test-be:
    cd backend && uv run --group dev python -m pytest

# Run frontend checks
test-fe:
    cd frontend && npm run typecheck

# Run all tests
test: test-be test-fe

# Lint backend with Ruff
lint:
    cd backend && uvx ruff check .

# Lint backend with Ruff and apply fixes
lint-fix:
    cd backend && uvx ruff check . --fix && uvx ruff format .

# Format backend with Ruff
format:
    cd backend && uvx ruff format .
