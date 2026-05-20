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

# Run frontend dev server
dev-fe:
    cd frontend && npm run dev

# Alias for frontend dev server
dfe: dev-fe

# Run local Docker image
run:
    docker run --rm -p 8080:8080 {{ image_ref }}

# Run Django migrations
migrate:
    cd backend && uv run python manage.py migrate

# Run backend tests (pytest-style)
test-be:
    cd backend && uv run pytest

# Run frontend checks
test-fe:
    cd frontend && npm run typecheck

# Lint backend with Ruff
lint:
    cd backend && uvx ruff check .

# Lint backend with Ruff and apply fixes
lint-fix:
    cd backend && uvx ruff check . --fix && uvx ruff format .

# Format backend with Ruff
format:
    cd backend && uvx ruff format .

# Run backend + frontend tests
test: test-be test-fe
    @echo "All tests complete"
