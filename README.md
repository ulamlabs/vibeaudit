# VibeAudit

Minimal commands for local development.

## Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (required)
- [`just`](https://github.com/casey/just) (optional)

## Quick Start

Backend:

```bash
cd backend
cp .env.example .env
uv sync
uv run python manage.py migrate
uv run python manage.py runserver 0.0.0.0:8000
```

Use `DEBUG=True` in `backend/.env` for local development.

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## Optional: just commands

If you have just installed, run commands from the project root:

- `just build` - Build local Docker image (`vibeaudit`)
- `just run` - Run local Docker image on port 8080
- `just dev-be` / `just dbe` - Run backend dev server
- `just dev-fe` / `just dfe` - Run frontend dev server
- `just migrate` - Run backend migrations
- `just test` - Run backend + frontend checks (`test-be`, `test-fe`)
- `just lint` / `just lint-fix` - Ruff lint only / lint with autofix + format
- `just format` - Run Ruff formatter

Show all recipes:

```bash
just --list
```
