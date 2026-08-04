# Assozeta self-hosting

This directory is the complete single-server Docker Compose distribution for
Assozeta. Application images are built by GitHub Actions; the server only pulls
images, creates its local configuration, and starts them.

## Requirements

- Linux server with Docker Engine and Docker Compose v2.20.0 or newer
- A public DNS record pointing to the server for automatic HTTPS
- Ports 80 and 443 reachable from the internet
- `openssl`, `curl`, and `tar`
- At least 4 GB RAM; 8 GB is recommended when rendering PDFs

The default stack contains PostgreSQL, Redis, MinIO, the Django API, Celery
worker and scheduler, the Svelte/Caddy web image, and a private Chromium PDF
renderer. Only Caddy publishes production ports.

## Install

From a tagged source checkout or a self-host release bundle:

```bash
./selfhost/bin/assozeta install \
  --domain assozeta.example.org \
  --email admin@example.org \
  --version 1.2.3
```

The command generates `selfhost/.env` with mode `0600`, generates an Ed25519
JWT key pair and first-run `INSTANCE_SETUP_TOKEN`, pulls the requested images,
initializes PostgreSQL and MinIO, runs committed Django migrations, seeds
required reference data, and waits for both public web and API readiness checks.

Open the printed URL and complete the first-run wizard with the printed setup
token. The token can be retrieved later from `selfhost/.env` as
`INSTANCE_SETUP_TOKEN`. The wizard creates the instance owner and association
and can only complete once.

For a local production-shaped installation without public TLS:

```bash
./selfhost/bin/assozeta install --domain localhost --version latest
```

To bind Caddy to a non-default HTTP or HTTPS port, include the port in the
domain, for example `--domain localhost:58080` or
`--domain https://assozeta.example.org:8443`. The generated `HTTP_PORT` or
`HTTPS_PORT` follows that URL port so Docker maps the same host/container port.

Public installations should use an exact release version. `latest` is intended
only for evaluation.

## Configuration

`selfhost/.env.example` documents every supported setting. The generated
`selfhost/.env` is the active configuration and is ignored by git.

Common optional integrations are disabled by leaving their credentials empty:

- SMTP email
- Google and Apple sign-in
- Stripe
- SMSAPI
- Groq AI agent

Restart the affected services after editing configuration. This recreates the
containers so edited environment values are loaded:

```bash
./selfhost/bin/assozeta restart
```

The bundled database, Redis, and MinIO services are intentionally not exposed
on host ports. Change Compose only if an external managed service is required.
The lifecycle wrapper runs Docker Compose with the selected env file isolated
from conflicting exported shell variables such as `COMPOSE_PROJECT_NAME`, port
values, passwords, or image names.

MinIO is used as private S3-compatible storage. The generated self-host env sets
`AWS_S3_USE_OBJECT_ACL=False` and leaves `AWS_S3_PUBLIC_BASE_URL` empty because
MinIO does not support the public object ACL flow used by some hosted providers.
The application stores subscription signatures by private storage key and reads
them through the backend. Do not add a Caddy route or host port for MinIO to make
objects public.

The self-host lifecycle commands are designed for the bundled private MinIO
service. In particular, `backup` and `restore` mirror that MinIO bucket and are
not a DigitalOcean Spaces backup/restore workflow. The application runtime still
supports public-ACL providers outside this bundled lifecycle: for example,
`AWS_S3_USE_OBJECT_ACL` defaults to enabled when `AWS_S3_ENDPOINT_URL` has a
`*.digitaloceanspaces.com` hostname, or you may set it explicitly.
`AWS_S3_PUBLIC_BASE_URL` can override the derived public bucket origin with a
DigitalOcean CDN or custom domain.

## Operations

```bash
./selfhost/bin/assozeta status
./selfhost/bin/assozeta logs api
./selfhost/bin/assozeta stop
./selfhost/bin/assozeta start
./selfhost/bin/assozeta migrate
```

Upgrade to an exact release:

```bash
./selfhost/bin/assozeta upgrade 1.3.0
```

The upgrade command creates a backup before changing the configured image
version. Database migrations may not be reversible, so retain that backup when
rolling back application images.

Mutating production lifecycle commands are serialized with
`selfhost/.lifecycle.lock/` so concurrent install, start, stop, backup, restore,
upgrade, or uninstall operations do not overlap. If an unclean shutdown leaves a
stale lock, verify that no lifecycle command is running before removing
`selfhost/.lifecycle.lock/` manually.

## Backups

Create a logical PostgreSQL dump, a bundled-MinIO object-storage mirror,
configuration snapshot, and manifest:

```bash
./selfhost/bin/assozeta backup
```

Backups are written to `selfhost/backups/` by default. They contain secrets and
must be encrypted or stored with restrictive permissions. A backup on the same
server does not protect against disk or host loss; copy it to independent
storage.

Backup stops the web, API, worker, and scheduler services while PostgreSQL and
MinIO are snapshotted, then restarts them only if any were running beforehand.
Expect brief write downtime for the duration of the dump and object mirror.

Backup archives are specific to the bundled private MinIO layout. New manifests
record the source `AWS_LOCATION`; archives created before that manifest field are
read from their archived configuration when available and otherwise treated as
the legacy self-host default `storage` prefix. Restore stops before the live swap
if the source and target prefixes differ.

Restore replaces the current database and object-storage contents:

```bash
./selfhost/bin/assozeta restore \
  selfhost/backups/assozeta-20260101T120000Z.tar.gz
```

Always test restores on a separate host before relying on a backup policy.
Restore uses the current `selfhost/.env` only; it does not restore or trust the
archive's `config.env` snapshot. Before live data is changed, the PostgreSQL
dump is restored into a staging database and the object tree is mirrored into a
staging bucket. After the app services are stopped, the current database and
object bucket are kept as rollback copies until migrations and public web/API
readiness checks succeed. Redis is flushed before the restored stack starts to
avoid stale sessions, cache entries, or queued jobs.

For backups created before the private signature-storage change, restore still
brings back the database and full MinIO tree first; current Django migrations
then migrate legacy `Subscription.signature` URL/base64 values, preserve
`signature_url` fallbacks, and conservatively recover existing
`subscriptions/<subscription UUID>/signature_*.png` objects when no DB pointer
exists. URL-only signature records remain supported as fallbacks.

## Development

From the repository root:

```bash
make dev
```

This builds and starts the entire development stack in Docker. Vite runs at
`http://localhost:5001` with HMR. Django runs at `http://localhost:8000` under
Gunicorn/Uvicorn with source reload. PostgreSQL, Redis, MinIO, the renderer,
Celery worker, and Celery Beat are included. Stateful dependencies stay on the
private Docker network; the MinIO console is available at
`http://localhost:59001`.

Development uses the same private-object behavior as production: MinIO API
traffic stays inside Docker, the console is bound to localhost for operators,
and generated env files set `AWS_S3_USE_OBJECT_ACL=False` with no public base
URL.

### First development setup

1. Generate the local configuration before starting Docker:

   ```bash
   make dev-config
   ```

   This creates `selfhost/.env.dev` with generated secrets and explanatory
   comments. Do not copy `.env.dev.example` over it and do not replace generated
   secrets with arbitrary values. Optional integration credentials may remain
   blank; the usual developer customizations are the `DEV_*` ports and
   `VITE_USE_POLLING`.
2. Run `make dev` and wait until the UI and API are reported healthy. Keep this
   terminal open while developing. Use `make dev-up` instead if you want the
   stack to run in the background. Both commands also create `.env.dev`
   automatically if you skipped step 1.
3. Copy the **first-run setup token** printed by the command. This is a generated
   secret, not a value you choose. A random string will be rejected with HTTP
   `401` by `POST /api/instance/configure`.
4. Open `http://localhost:5001`. In the first wizard step, leave the detected
   domain as `localhost` and paste the exact generated token into **Token di
   Setup**.
5. Choose **Crea una nuova istanza**, enter the association and owner account
   details, configure the branding, and complete the wizard. You can then sign
   in with the owner email and password entered in the wizard.

The token is stored only in `selfhost/.env.dev`. To retrieve it after the
startup message has scrolled away, run this from the repository root:

```bash
awk -F= '$1 == "INSTANCE_SETUP_TOKEN" { print substr($0, index($0, "=") + 1) }' selfhost/.env.dev
```

Paste only the value printed by that command, without
`INSTANCE_SETUP_TOKEN=`. The token remains the same across `make dev-down` and
`make dev-reset`; a new token is generated only if `selfhost/.env.dev` is
removed and recreated. If you previously submitted a wrong token, no reset is
needed: return to the first wizard step and enter the correct value.

Useful commands:

```bash
make dev-up
make dev-logs SERVICE=api
make dev-shell
make dev-db-shell
make dev-migrate
make dev-test
make dev-down
make dev-reset CONFIRM=1
```

`make dev-down` keeps development data. `make dev-reset CONFIRM=1` permanently
deletes the development database, Redis, MinIO, static, and Node dependency
volumes. It does not delete `selfhost/.env.dev`.

On Docker Desktop, set `VITE_USE_POLLING=true` in `selfhost/.env.dev` if native
filesystem notifications do not trigger Vite HMR.

## Images

`.github/workflows/publish-images.yml` publishes:

- `ghcr.io/<repository-owner>/assozeta-backend`
- `ghcr.io/<repository-owner>/assozeta-web`
- `ghcr.io/<repository-owner>/assozeta-renderer`

Pull requests build without pushing. Main publishes `edge` and commit tags.
GitHub releases publish semantic-version and `latest` tags and attach this
self-host distribution as a release archive.

## Security

- Do not commit `.env`, `.env.dev`, or backup archives.
- Use exact image release versions in production.
- Keep PostgreSQL, Redis, MinIO, and the renderer private.
- Protect and copy backups off-host.
- Treat `INSTANCE_SETUP_TOKEN` as a secret until first-run setup is complete.
- Keep Docker and the host operating system patched.
- Configure SMTP before depending on password-reset email.
