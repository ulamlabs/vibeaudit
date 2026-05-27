# VibeAudit

Minimal commands for local development.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (required)
- [`just`](https://github.com/casey/just) (optional)

## Quick Start

Backend:

```bash
cd backend
uv sync
uv run python manage.py migrate
uv run python manage.py runserver 0.0.0.0:8000
```

(Optional) Create `backend/.env` with `DEBUG=True` and `SECRET_KEY` for local development; see `backend/.env.example`.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Optional: Sentry

Backend (runtime env in `backend/.env`):

- `BACKEND_SENTRY_DSN` enables backend Sentry when set.

Frontend (build-time env):

- Pass `FRONTEND_SENTRY_DSN` at image build time.
- It is embedded into the frontend bundle as `VITE_SENTRY_DSN`.
- If unset, frontend Sentry is not initialized.

Example Docker build with frontend Sentry enabled:

```bash
docker build --build-arg FRONTEND_SENTRY_DSN=https://<key>@o0.ingest.sentry.io/<project> -t ghcr.io/ulamlabs/vibeaudit:latest .
```

## Optional: just commands

If you have just installed, run commands from the project root:

- `just build` - Build Docker image (`$IMAGE_REPO:$IMAGE_TAG`, defaults to `ghcr.io/ulamlabs/vibeaudit:latest`)
- `just run` - Run Docker image on port 8080
- `just dev-be` / `just dbe` - Run backend dev server
- `just dev-fe` / `just dfe` - Run frontend dev server
- `just migrate` - Run backend migrations
- `just test` - Run backend + frontend checks (`test-be`, `test-fe`)
- `just lint` / `just lint-fix` - Ruff lint only / lint with autofix + format
- `just format` - Run Ruff formatter

Example with GHCR:

```bash
IMAGE_REPO=ghcr.io/ulamlabs/vibeaudit IMAGE_TAG=main just build
```

Show all recipes:

```bash
just --list
```
