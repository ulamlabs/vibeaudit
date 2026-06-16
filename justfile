# VibeAudit project commands

set dotenv-load := true

image_repo := env_var_or_default("IMAGE_REPO", "ghcr.io/ulamlabs/vibeaudit")
image_tag := env_var_or_default("IMAGE_TAG", "latest")
image_ref := image_repo + ":" + image_tag

alias i   := install
alias dbe := dev-be     # Dev Back End 
alias dfe := dev-fe     # Dev Front End
alias dcu := dc-up      # Docker Compose Up
alias dcd := dc-down    # Docker Compose Down
alias ce  := compile-email

# Default recipe
default:
    @just --list

# Build local Docker image
build:
    docker build -f docker/Dockerfile -t {{ image_ref }} .

# Run backend dev server
dev-be:
    cd backend && uv run python manage.py runserver 0.0.0.0:8000

# Install backend dependencies
ibe:
    cd backend && uv sync

# Install frontend dependencies
ife:
    cd frontend && npm install

# Install all dependencies
install: ibe ife

# Run frontend dev server
dev-fe:
    cd frontend && npm run dev

# Run local Docker image
run:
    docker run --rm -p 8080:8080 {{ image_ref }}

# Start dev environment via Docker Compose (build if needed)
dc-up:
    docker compose -f docker/docker-compose.dev.yml up --build

# Stop dev environment
dc-down:
    docker compose -f docker/docker-compose.dev.yml down

# Build dev Docker images without starting
dc-build:
    docker compose -f docker/docker-compose.dev.yml build

# Run Django migrations inside the running backend container
dc-migrate:
    docker compose -f docker/docker-compose.dev.yml exec backend python manage.py migrate --noinput

# Open a bash shell inside the running backend container
dc-shell:
    docker compose -f docker/docker-compose.dev.yml exec backend bash

# Run Django migrations
migrate:
    cd backend && uv run python manage.py migrate

# Compile all MJML email sources to Django HTML templates (run after editing any src/*.mjml)
compile-email:
    npx mjml backend/audit/templates/email/src/report_email.mjml -o backend/audit/templates/email/report_email.html
    npx mjml backend/audit/templates/email/src/failure_email.mjml -o backend/audit/templates/email/failure_email.html
    npx mjml backend/audit/templates/email/src/new_submission_email.mjml -o backend/audit/templates/email/new_submission_email.html

# Run backend tests (pytest-style)
test-be:
    cd backend && uv run --group dev python -m pytest

# Type-check backend with mypy
typecheck-be:
    cd backend && uv run mypy audit github_app vibeaudit

# Run frontend checks
test-fe:
    cd frontend && npm run typecheck

# Run all tests
test: test-be test-fe

# Run all type checks
typecheck: typecheck-be test-fe

# Lint backend with Ruff
lint:
    cd backend && uvx ruff check .

# Lint backend with Ruff and apply fixes
lint-fix:
    cd backend && uvx ruff check . --fix && uvx ruff format .

# Format backend with Ruff
format:
    cd backend && uvx ruff format .
