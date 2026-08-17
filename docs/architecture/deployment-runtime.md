# Deployment and runtime behavior

## Stack surface

Self-hosted deployments are coordinated from `selfhost/compose.yml` and `selfhost/compose.dev.yml`:

- `api`: Django app container (ASGI-compatible)
- `worker`: Celery worker
- `beat`: Celery beat
- `postgres`: PostgreSQL
- `redis`: cache/session/channel layer broker
- `minio`: S3-compatible storage (self-host mode defaults)
- `renderer`: document/PDF service
- `web`: reverse proxy/static frontend (Caddy in production compose)
- `ui` (dev compose): Vite dev server

## Request handling

- API calls go through Django URLs composition in `BE/core/urls.py`.
- ASGI channels route websocket paths in:
  - `BE/application/routing.py` (`/ws/updates/`, `/ws/health/`)
  - `BE/application/chat/routing.py` (`/ws/agent/`)
  - `BE/notifications/routing.py` (`/ws/notifications/`)
- Redis is used by:
  - django cache/session
  - channels layer
  - celery broker

## Persistence and state

- PostgreSQL stores transactional domain data (memberships, courses, payments, audit logs, etc.).
- Redis is used for cache, queue broker, and some ephemeral state.
- Object storage is used for document templates, PDFs, signatures, and attachments.
- The settings path controls local/remote storage behavior and S3/DigitalOcean compatibility toggles.

## Background and scheduled work

From `BE/core/celery.py` and application task definitions:

- Worker processes asynchronous operations like:
  - reminders
  - payments renewal/validation
  - certificate expiry alerts
  - cleanup and archival jobs
- Beat schedules many recurring jobs by cron-like cadence.

## Operations and maintenance docs references

- `selfhost/README.md`
- `selfhost/bin/assozeta` CLI commands:
  - `install`, `status`, `logs`, `backup`, `restore`, `upgrade`, `start`, `stop`
- `Makefile` targets in repo root (`make dev`, `make dev-reset`, `make dev-test`, etc.).
- `docs/scripts/render_mermaid_diagrams.mjs` for rendered Mermaid snapshots.

## Development mode notes

- `make dev` and `make dev-config` generate `.env.dev` and start a local service mesh.
- In dev compose, UI routes through Vite and API via Django/gunicorn+uvicorn.
- Browser access defaults to:
  - UI: `http://localhost:5001`
  - API: `http://127.0.0.1:8000`

## Documentation evidence tasks (required)

- Keep this file updated whenever infrastructure-level services or compose definitions change.
- Capture screenshot evidence for both dev (`UI` from Vite) and prod (`web` reverse proxy) contexts when possible.
- Regenerate matrix artifacts before updating deployment claims.
