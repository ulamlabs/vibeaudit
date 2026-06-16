# VibeAudit

VibeAudit is a GitHub App that automates code audits using LLM agents. Install it on your GitHub account, select repositories to grant access, and spawn audit tasks that clone your code, analyze it, and generate detailed reports. The app runs as a standalone web service (`app.example.com`) where users can connect their GitHub account, pick repositories, and receive audit reports via email.

## Development

**Prerequisites:** [`uv`](https://docs.astral.sh/uv/), [`just`](https://github.com/casey/just), `docker`

### Option A — Docker Compose (recommended)

Starts Traefik, Postgres, Redis, Django, Celery worker, and Vite — all with hot reload.

```bash
docker compose -f docker-compose.dev.yml up --build
# or 
just dc-up      # alias dcu
```

First run — apply migrations:

```bash
docker compose -f docker-compose.dev.yml exec backend python manage.py migrate --noinput
# or with just:
just dc-migrate

# attach to backend container to run eg createsuperuser
just dc-shell 
```

App is available at `http://localhost:3000` (the same port as frontend dev server so that `ngrok` session can be reused)  
Backend admin panel `http://localhost:8000/admin`

If you need the GitHub integration, place your `.pem` key at `backend/secrets/github-app.pem` (see [GitHub integration](#github-integration-optional)) and set `GITHUB_APP_ID`, `GITHUB_APP_SLUG` in the compose environment or a `backend/.env` file.

### Option B — manual


```bash
cp backend/.env.example backend/.env  # set DEBUG=True, SECRET_KEY at minimum
just i        # install all dependencies
just migrate  # run DB migrations
just dbe      # Terminal 1 — backend on :8000
just dfe      # Terminal 2 — frontend on :3000 (proxies /api to backend)
```

### GitHub integration (optional)

Required only if you want to test the GitHub App OAuth flow locally. Needs a tunnel to expose localhost publicly — [ngrok](https://ngrok.com/) or equivalent.

#### 1. Expose a public URL

```bash
ngrok http 3000
```

Note the HTTPS URL (e.g. `https://9647-46-205-207-234.ngrok-free.app`). Used as `<NGROK>` below.

#### 2. Create a GitHub App

Go to **Settings → Developer settings → GitHub Apps → New GitHub App**:

| Field | Value |
|---|---|
| Homepage URL | `<NGROK>` |
| Setup URL | `<NGROK>/api/github/setup` |
| Redirect after installation | checked |
| Webhook | disabled |
| Permissions | **Repository: Contents** → Read-only |

Generate a **private key** (`.pem`) and save it as `backend/secrets/github-app.pem` (gitignored). Note the **App ID** and **App slug** (`github.com/apps/<slug>`).

If you need to pass the private key as an environment variable (e.g., in SOPS or `.env` files), convert it to a single-line format with escaped newlines:

```bash
python3 backend/scripts/pem_to_env.py backend/secrets/github-app.pem | pbcopy
```

This copies the key to your clipboard, ready to paste into environment config.

#### 3. Configure

Add to `backend/.env`:

```env
GITHUB_APP_ID=<your app id>
GITHUB_APP_PRIVATE_KEY_PATH=secrets/github-app.pem
GITHUB_APP_SLUG=<your app slug>
ALLOWED_HOSTS=localhost,127.0.0.1,<NGROK host>
CSRF_TRUSTED_ORIGINS=<NGROK>
ALLOW_ANONYMOUS_AUDIT=True
```

Add to `frontend/.env`:

```env
VITE_EXTRA_HOST=<NGROK host>
```

Open `<NGROK>/connect` to test the install flow.

## just commands

Run from the project root:

- `just dcu` / `just dc-up` - Start full dev environment via Docker Compose
- `just dcd` / `just dc-down` - Stop Docker Compose dev environment
- `just dc-build` - Build dev Docker images without starting
- `just dc-migrate` - Run Django migrations inside the running backend container
- `just dc-shell` - Open a shell inside the running backend container (e.g. to run `python manage.py createsuperuser`)
- `just i` / `just install` - Install all dependencies (`ibe` + `ife`)
- `just ibe` - Install backend dependencies
- `just ife` - Install frontend dependencies
- `just dbe` / `just dev-be` - Run backend dev server (manual mode)
- `just dfe` / `just dev-fe` - Run frontend dev server (manual mode)
- `just migrate` - Run backend migrations
- `just test` - Run backend + frontend checks
- `just lint` / `just lint-fix` - Ruff lint / lint with autofix + format
- `just format` - Run Ruff formatter
- `just build` - Build production Docker image
- `just run` - Run production Docker image on port 8080

```bash
just --list
```

## Deployment

Build and push with custom image ref:

```bash
IMAGE_REPO=ghcr.io/ulamlabs/vibeaudit IMAGE_TAG=main just build
```

Run locally:

```bash
just run
```

## Optional: Sentry

Backend — set `BACKEND_SENTRY_DSN` in runtime env.

Frontend — pass `FRONTEND_SENTRY_DSN` at image build time (embedded as `VITE_SENTRY_DSN`):

```bash
docker build --build-arg FRONTEND_SENTRY_DSN=https://<key>@o0.ingest.sentry.io/<project> -t ghcr.io/ulamlabs/vibeaudit:latest .
```

## Email templates

Three transactional templates live in `backend/audit/templates/email/`:

| Template | Sent when |
|---|---|
| `report_email.html` | Audit completed — report delivered to submitter |
| `failure_email.html` | Audit run failed — submitter notified with a reference ID |
| `new_submission_email.html` | Job reaches `AWAITING_APPROVAL` — staff notified |

Sources are MJML (`src/*.mjml`). After editing, recompile:

```bash
just compile-email   # alias: just ce
```

**Django template tags in MJML** — MJML strips bare `{% %}` tags during compilation. Wrap them in `<mj-raw>` so they survive into the compiled HTML:
```xml
<mj-raw>{% if site_url %}</mj-raw>
<mj-button href="{{ site_url }}/...">...</mj-button>
<mj-raw>{% endif %}</mj-raw>
```

**Overriding templates** — set `EXTRA_EMAIL_TEMPLATES_DIR` to an absolute path. Files found there take precedence over bundled ones; unmatched filenames fall back to bundled.

**Staff notifications** — recipients must be `is_staff=True` **and** members of the `audit_notifications` group (created automatically by migrations). Add users via Django admin.

**Report subject** — configurable via the `REPORT_EMAIL_SUBJECT` environment variable (default: `"VibeAudit Report"`).

