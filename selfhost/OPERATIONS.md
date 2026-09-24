# Owner settings and diagnostics

The instance owner can open **Profile settings → Self Instance**. Ownership is the
user attached to the installation's primary association, not a collaborator or an
unrelated administrator. The API enforces this independently of navigation.

The page separates Overview, Branding, Email, Integrations, Updates and backups,
and Diagnostics. Switching sections preserves drafts. Unsaved changes prevent an
instance update until they are saved or discarded. Branding includes identity and
logo previews; saving branding does not overwrite operational settings.

## Adoption by existing installations

These changes ship with the normal self-host distribution. The normal update
runs database migration `instance.0003_instance_operations`, replaces API/worker/
beat images, and provisions the matching updater using the existing update flow.
No new environment variable, mount, credential file, or manual setup is needed.
Existing branding and provider configuration remain intact. Empty email overrides
mean the original environment configuration continues to apply.

For an installation maintained from a Git checkout, fetch the release and use its
normal update command, rather than starting only a new web container:

```sh
git pull --ff-only
cd selfhost
./bin/update --directory "$PWD" --version X.Y.Z
```

Replace `X.Y.Z` with the exact published stable release containing this feature.
`bin/update` requires both arguments and follows the bootstrap safeguards in
[UPDATES.md](UPDATES.md). Pulling a development commit does not publish that commit
as a stable release. Already bootstrapped installations can use their normal
`./bin/assozeta upgrade X.Y.Z` command instead. This feature is available only after a release containing it
has been built and installed; these local changes do not alter deployed instances.

## System email

Until the owner saves an override, the existing `EMAIL_HOST`, `EMAIL_PORT`,
`EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS`, `EMAIL_USE_SSL`, and
`DEFAULT_FROM_EMAIL` settings remain effective. The sender currently defaults to
the application name and `EMAIL_HOST_USER` as configured by the backend.

Saving the form stores SMTP host, port, encryption mode, username, and sender
identity in the database. The password is encrypted using a key derived from the
installation's Django `SECRET_KEY`; it is never returned by the API. Leaving the
password field empty preserves the effective password. Replacement and removal
are explicit actions. Concurrent edits receive a conflict response instead of
silently overwriting another session's changes.

The default Django mail backend reads this configuration for each send batch in
both API and worker processes. New sends use saved changes immediately; an email
already being sent finishes with its previous configuration. No service restart
is required for changes made in this form. Association-specific SMTP transports
remain separate. Existing reply-to addresses and support recipients are not
rewritten by changing the SMTP sender.

The reset action removes the database override and encrypted password, then uses
the server environment again. It never rewrites `.env`. Environment changes made
outside the application still require restarting API and workers. Keep the
installation's original secret key with the protected database/configuration
backup; replacing it without preserving a valid fallback key makes stored email
passwords unreadable. The form then requires a replacement or explicit removal.

**Verify SMTP connection** opens a connection and authenticates if configured but
sends no email. **Send test email** sends one fixed verification message to the
recipient entered by the owner. Save or cancel drafts before testing. A successful
send means SMTP accepted the message, not that it reached the inbox; check the
recipient's inbox and spam folder. Tests are rate limited and have hard deadlines.
The latest test result survives reload and is invalidated when settings change.
Test recipients and credentials are not included in stored diagnostic results.
Operational payloads are excluded from both API audit logging and the development
request profiler.

Use SSL or STARTTLS for authenticated SMTP. An unencrypted, unauthenticated local
relay can be configured, but diagnostics flag the absence of encryption. Failed
or timed-out sends may have reached SMTP before a response was lost: check the
recipient before repeating a test.

## Integration configuration in the UI

The **Integrations** section loads the effective environment values until an owner
saves an override. Secret inputs remain empty and indicate whether a value is
already configured. Omitted secrets are retained; replacement and removal are
explicit actions. Save, disable, cancel, and reset-to-environment are separate
controls for each provider. Independent revision checks reject stale saves.

| Integration | Editable settings | Environment fallback | Applied to |
| --- | --- | --- | --- |
| Stripe | Enabled, publishable key, secret/restricted API key, webhook signing secret | `STRIPE_PUBLIC_KEY`, `STRIPE_KEY`, `STRIPE_WEBHOOK_SECRET` | Public checkout key, each API/worker Stripe request, webhook signature verification |
| Google | Enabled, web client ID | `SOCIAL_AUTH_GOOGLE_OAUTH2_KEY` | Login button and backend token audience verification |
| Apple | Token verification enabled, client ID | `SOCIAL_AUTH_APPLE_ID_CLIENT` | Backend Apple identity-token audience verification; web login button is not implemented |

The current Google token flow does not consume a client secret. Apple team ID,
key ID, and private key are not consumed by the implemented identity-token
verification flow. Those legacy environment variables remain untouched; this UI
does not offer settings that would misleadingly imply an implemented code-exchange
or Apple web sign-in flow. Provider-console setup remains external: authorize the
shown Google origin, and configure the shown Stripe webhook endpoint for
`charge.succeeded`. [Google's client setup](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid)
and [Apple's identity verification documentation](https://developer.apple.com/documentation/signinwithapple/verifying-a-user)
describe those provider requirements.

Overrides persist in `InstanceConfiguration.integration_settings`; credentials
are encrypted using the instance `SECRET_KEY`, with `SECRET_KEY_FALLBACKS` support.
Migration `0004_instance_integrations` supplies an empty map for existing installs.
It does not import, overwrite, or erase their environment or legacy configuration.
A normal image update and migration enable the controls automatically.

Saving **does not rewrite the host `.env` file**. The API and workers read current
settings for new operations, using explicit per-request Stripe authentication;
no process-global key is changed. Financial cache entries are separated by key,
so changing accounts does not reuse the previous account's cached fees. Reload
open pages after changing integration settings. Manual `.env` edits still require
the affected services to restart; the UI shows the environment currently loaded
by those processes. Reset removes only that provider's override and reuses its
environment values. Preserve the database and `SECRET_KEY` together in backups.

Changing Stripe account/environment can make existing payment intents inaccessible;
handle outstanding payments before switching. Format and sandbox/live consistency
checks do not prove account ownership, API permissions, successful payments,
webhook delivery, or OAuth login. Saving and routine diagnostics do not initiate
payments or provider sign-ins. [Stripe supports per-request authentication](https://docs.stripe.com/api/authentication?lang=python).

## What the diagnostic results prove

Diagnostics are owner-triggered, bounded, and read-only with respect to application
and service data. The result snapshot and short-lived operation leases in the database are the
only diagnostic bookkeeping writes. Coordination does not depend on Redis, so a
broker/cache outage does not itself prevent the diagnostic probes from running. No payment, email, application task, backup,
or update is initiated by the routine checks. Service probes load only settings and client libraries; model-backed email checks
also initialize Django. This avoids booting the entire application for every
service check on small servers. Four isolated probe processes run
at most concurrently, each with a 10-second hard deadline; ten checks normally
complete within approximately 30 seconds plus request/database overhead.

| Check | Evidence | What remains untested |
| --- | --- | --- |
| Public URL / HTTPS | Configured public health endpoint, installed version, trusted TLS certificate | Reachability from every external network, DNS propagation everywhere |
| API | Local HTTP health endpoint and installed version | Every application route |
| Database | Successful `SELECT 1` | Writes, all tables, full integrity |
| File storage | Read/list access with configured S3 credentials | Uploads, writes, every stored object |
| Worker | A Celery worker answers a broker inspection request | Successful execution of business tasks |
| Scheduler | Fresh heartbeat emitted by the normal scheduler loop | Completion of scheduled tasks |
| PDF renderer | Its health endpoint can open and close a browser page | Rendering an application document |
| Email | Required configuration fields | Connectivity and sending until explicitly requested |
| Updater | Compatible private status API and recovery readiness | Downloading and installing a future release |
| Backup preparation | Standard backup directory permissions, disk threshold, latest archive timestamp and gzip header | Full archive integrity, exact required space, successful restoration |

Each result names its evidence level: **Configuration**, **Connection**, or
**Functional test**, along with the time checked. A green checkmark applies only
to that stated check. Unknown, not configured, and not applicable states are
separate from success. Stored results describe the last check, not continuous
monitoring. Changing email settings invalidates old email evidence.

Overview health is derived from public URL, API, database, storage, worker,
scheduler, and renderer checks. Optional email/integration/update/backup issues
remain visible without changing the core-service health indicator. Stripe and
OAuth cards inspect configuration presence only; they do not test payments,
webhooks, or sign-in. Stripe sandbox/live key mismatches and differing public/server
OAuth client IDs are flagged. Provider secrets remain server-managed and hidden.

## Handling failures

- **Timeout or unavailable service:** check that service on the server, resolve
  its configuration/network issue, then rerun diagnostics. Raw logs, passwords,
  service addresses, and exception details are deliberately omitted from results.
- **Missing scheduler heartbeat:** restart the normal `beat` service after the
  update and allow a minute before rerunning. The scheduler writes its heartbeat
  during normal operation, independently of diagnostics.
- **Email conflict:** cancel the local draft, reload, and reapply the intended
  changes. An unreadable password requires replacement or explicit removal.
- **No recent backup:** run `./bin/assozeta backup` from `selfhost`; this briefly
  stops application services for consistency. Backups outside the standard
  directory are not discovered. Keep protected off-server copies and perform a
  restore drill in an isolated installation.
- **Interrupted update:** use the operation's recovery instructions and
  [UPDATES.md](UPDATES.md). Changing image tags alone does not restore migrated data.
- **API or database unavailable:** owner diagnostics cannot establish a new
  authorized session without the application database. The existing independent
  update-status/recovery view remains available according to its documented
  authorization and availability limits.

## Repeatable verification

With the development image and its database dependencies prepared:

```sh
./selfhost/bin/assozeta dev-compose run --rm --no-deps api python -m pytest instance/tests/ -q
python3 -m unittest discover -s selfhost/tests -p test_operational_diagnostics.py
npm ci --prefix UI
npm ci --prefix selfhost/tests/browser
npm exec --prefix selfhost/tests/browser -- playwright install chromium
(cd UI && node --test src/routes/profile/sections/formChanges.test.js src/routes/profile/sections/selfInstanceRendering.test.js)
node selfhost/tests/browser/operations-ui.mjs
```

The last command mounts the real Svelte components against fixture APIs, without
an account or deployment. It checks desktop/mobile navigation, draft retention,
save failure and reload, explicit email actions, unavailable-service results,
and overflow. Screenshots and its report go to `quality-reports/operations-ui/`.
It does not replace the real application checks. After building the four test
images required by `application-update-smoke.py`, the focused end-to-end check is:

```sh
ASSOZETA_QUALITY_REPORT_DIR="$PWD/quality-reports/operations-current" python3 selfhost/tests/application-update-smoke.py --operations-only
```

This installs a disposable production-mode stack, checks owner/non-owner access,
branding, email save/reload/reset, an explicit SMTP connection failure against a
closed local port, all ten diagnostic results, and cleanup. It never sends an
external message. The full upgrade/recovery/browser harness remains documented
in [QUALITY.md](QUALITY.md); neither harness proves real SMTP inbox delivery.

The reusable release-quality workflow now runs these operational checks in a
separate disposable job, including a legacy-schema migration test and local SMTP
receiver. Publication already depends on that workflow. CI results must be
verified on the actual release commit before rollout.
# Restarting from Self Instance

The instance owner can choose **Ricarica applicazione** in Self Instance. The
Italian confirmation explains the interruption for all users and the risk of
losing unsaved edits. Cancel leaves the installation untouched.

After confirmation, the existing updater records an idempotent restart operation
and launches a detached helper using its currently installed image. The helper
shares the lifecycle and data-operation locks, stops the application's consumers
before their dependencies, and starts existing containers in dependency order.
It also restarts the updater itself. One-shot setup and migration jobs are
excluded. Container images, persistent volumes, configuration and session data
are retained; this action does not upgrade the installation.

The page keeps polling the existing independent status endpoint through expected
connection failures. It reloads only after the recorded operation succeeds and
web/API readiness checks pass. After ten minutes it displays recovery guidance
and a **Verifica di nuovo lo stato** action, which only checks status and never
submits another restart. Failed operations include guidance in the existing
operation history; private details are in `.updater/logs/<operation-id>.log`.
Inspect the installation's container state and logs before retrying a failure.

Restarts are disabled in development, during another operation, when an update
requires recovery, or when the installed updater does not support restarts.
