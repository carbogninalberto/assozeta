# Self Instance operations acceptance

Implementation audit, 18 September 2026. This report concerns the owner
configuration/diagnostics goal. The user subsequently authorized deployment to
assozeta.bakney.com; this is a test deployment, not a published release certification.
Existing staged work has been preserved.

Legend: **✓ Passed** means the stated evidence was observed; **✕ Failed** means an
unresolved failure; **○ Not tested** identifies a boundary or pending verification.
A narrow check does not imply broader functionality.

| Requirement | Status and evidence |
| --- | --- |
| Owner-only profile section and backend authorization | ✓ Existing owner navigation rendering tests plus operational API tests reject anonymous, unrelated owners, and non-owner superusers, including impersonation headers. Non-self-hosted access is rejected. |
| Clear Overview, Branding, Email, Integrations, Updates/backups, Diagnostics sections | ✓ Real components compiled without new Svelte warnings. Desktop 1440×1000 and mobile 390×844 fixture browser checks exercise all six sections and detect horizontal overflow. Screenshots reviewed. |
| Branding preview/save and preserved existing capabilities | ✓ Identity/color preview, existing logo preview, save/cancel, release/history/recovery code retained. API branding/logo tests pass. Field-specific writes preserve concurrently saved email and diagnostics. Real application desktop/mobile branding and operations rehearsal passed. |
| Honest, timestamped status with distinct evidence levels | ✓ Tests cover initial “not checked”, core failure, configuration/connection/functional labels, persistence/reload, optional failures excluded from core health, and invalidated email results. |
| Public URL/HTTPS, API, database, S3, worker, scheduler, renderer, email, updater, backups | ✓ Ten explicit, fixed-purpose checks implemented. Read-only local runtime checks verify database, S3 and renderer success, absent worker/scheduler failure, and updater “not applicable” in development. HTTP/TLS distinctions, scheduler freshness, read-only S3 access, and backup failures are tested. |
| Timeouts and safe failure handling | ✓ A genuinely sleeping child process is terminated by the hard deadline. Failed process output and extra result fields are filtered. Routine diagnostics cannot invoke email-send probes. Concurrent actions are limited by database leases so Redis failure does not block coordination. |
| Editable integrations with environment defaults | ✓ Owner UI reads effective environment defaults, masks credentials, and saves encrypted database overrides for Stripe, Google client ID and Apple token-verification client ID. Independent revisions, retain/replace/clear secrets, enable/disable, explicit environment reset, and audit/profiler exclusions are tested. The host `.env` is intentionally unchanged; new API/worker operations use saved settings without restart. |
| Supported, shared email configuration | ✓ Database-backed transport and sender override, encrypted password, per-send API/worker backend reads, environment fallback/reset, unchanged credential preservation, and stale-edit rejection. Unit/API tests verify saves and reload. Existing backend objects pick up subsequent saves. Caller-owned message objects retain their sender. |
| Explicit SMTP connection test and chosen-recipient test email | ✓ A real SMTP exchange with a disposable loopback receiver verifies connection-only sends no message and send submits exactly one to the chosen recipient. UI checks distinguish both actions and require saved settings. SMTP acceptance never claims inbox delivery. |
| Secret handling | ✓ Credential fields absent from public config and email responses; password never stored as plaintext. Operational payloads excluded from API audit logging and the development request profiler. Diagnostic results omit recipients, raw exceptions, connection details, and secrets. Email responses use no-store caching. |
| Existing installations self-configure on normal update | ✓ Migration from the prior instance schema retains branding, existing integration fields and display settings while supplying empty defaults that preserve email environment fallback. No new environment keys or mounts required. Existing image/migration/updater replacement flow carries the new code. Disposable full update/recovery distribution rehearsal passed. |
| Failure, persistence, and form-state tests | ✓ Failed saves retain drafts/password replacements; cancel restores baseline; switching sections preserves drafts; tests and updates require saved settings; reset requires explicit confirmation; HTTP error/retry and unavailable-service results covered. |
| Responsive/accessibility checks | ✓ Native labelled controls/fieldset, semantic headings/navigation, visible current section, text plus icons, live result announcements, keyboard focus styles, 390px layout without overflow. Browser screenshots inspected; no claim of a formal assistive-technology audit. |
| Operator documentation and repeatable automation | ✓ `selfhost/OPERATIONS.md` explains upgrade adoption, supported email behavior, evidence boundaries, recovery, and repeatable commands. Release-quality workflow includes the operational backend and isolated browser job. |
| Isolated failure testing and authorized test deployment | ✓ Failure injection uses disposable infrastructure. No external email, commit, or publication performed. Test-server deployment was subsequently authorized; its verification is recorded separately below. |

## Verification evidence

- Final backend instance and Stripe consumer suites: **86 passed**, including owner authorization, integration encryption/revisions/reset, dynamic Stripe API/worker/webhook credentials, cache isolation, Google/Apple token audiences, legacy login visibility, and operational regressions. An earlier broader authentication/signup regression run passed **139 tests**; these runs overlap and are not additive.
- Frontend selected form/owner/update/status/store/middleware tests: **31 passed**.
- Host updater/quality/backup unit tests: **34 passed**.
- Production Vite build: **passed**. The repository still emits pre-existing
  Svelte/accessibility and large-chunk warnings elsewhere; new operational
  components compile without warnings.
- Standalone operational browser: **desktop and mobile passed**. Evidence:
  `quality-reports/operations-ui/report.json` and `email-*.png`, `integrations-*.png`, `diagnostics-*.png`.
  This uses real components with fixture APIs, not the deployed application.
- Local read-only service probes: `quality-reports/operations-probes.json`.
- Private updater provisioning/socket smoke: **passed** in a disposable project,
  including the new authenticated `/diagnostics` route, rejection without its
  credential, and rejection through the public status socket. Project resources
  were removed by the harness.
- Full real-application update/recovery rehearsal: **passed**, including failed backups, migrations, readiness, interrupted runner recovery, and data/configuration preservation. Evidence: `quality-reports/operations-full/update-recovery.json`.
- Final-build real-application operations rehearsal: **desktop/mobile passed**, including all ten diagnostics, saved/reset email settings, reload persistence, branding, and non-owner rejection. Evidence: `quality-reports/operations-current/operations.json` and `browser/operations/diagnostics.json`; the final reduced-startup probe build also passed in `quality-reports/operations-final/`. Seven internal service checks passed; public URL correctly warned about the HTTP fixture; optional email/backup warnings were expected.
- The first concurrent local rehearsal hit the hard diagnostic deadline. The issue also reproduced on the two-core test server. Service probes now initialize only settings/client libraries; only model-backed email probes initialize the full Django application. A real subprocess regression checks basic services with an unavailable unrelated Django app. The corrected probes passed on the two-core server with the same 10-second deadline. Final deployed browser verification is recorded below.
- Final integration application rehearsal: **passed**, including desktop/mobile save/reload/reset for all three providers, masked credentials, non-owner rejection, email and diagnostics, and Google login client-ID refresh using a mocked SDK. Evidence: `quality-reports/integrations-final/operations.json`. Disposable resources were removed. No external provider authentication/payment was performed.
- Real loopback HTTPS tests verify trusted certificates, reject untrusted certificates/redirects, and check the reported version. Probe startup SQL audit observed only SELECT statements.
- Static self-host validation: **passed**. Migration drift check: **No changes detected**. Existing missing-static-directory warning remains unrelated to this feature.

## Limits and release gates

- Apple configuration supports the implemented backend token-verification flow. Apple web sign-in is not implemented. Unused Google client-secret and Apple team/private-key variables are preserved and are not presented as active integration settings.
- ○ Real external SMTP delivery, SPF/DKIM/DMARC, inbox placement, Stripe payments,
  and provider sign-in are **not tested by this goal**. Test-server HTTPS is checked
  separately during deployment.
  Their UI evidence explicitly remains configuration-only or unverified until an
  appropriate explicit test is performed.
- ○ Backup restore confidence cannot be derived from a gzip header or free-space
  check. Use the disposable recovery rehearsal and an operator restore drill for
  the actual installation's backup.
- ○ The newly edited GitHub workflow has not run on a published release commit.
  Verify that required CI/rehearsal gate before a published production rollout.
- The working tree contains earlier staged and unstaged changes. Local passing
  checks do not identify a committed/published artifact for production rollout.
- The test build uses a development version. Release notes remain available, but
  one-click stable-release updates intentionally require a recognized stable
  installed version. The update/recovery paths were exercised with versioned
  disposable distributions, not by replacing the live test build with a release.

## Authorized test-server deployment

✓ Initial operations deployment and diagnostic startup correction verified at https://assozeta.bakney.com (85.235.148.212), version `v1.0.3-dev.20260918.2`. Live desktop/mobile checks passed all core diagnostics, updater and backup readiness; email remains honestly reported as not configured. Existing environment, application data, and unrelated containers were preserved. Evidence: `quality-reports/operations-deployment/report.json`.

✓ The integration extension is deployed and verified at the same test URL, version `v1.0.3-dev.20260918.3`. Live desktop/mobile checks cover all six sections, environment-loaded integration values, masked secret fields, disabled save buttons until edits, draft cancellation, owner access and anonymous rejection, no horizontal overflow/runtime errors, diagnostics persistence, and release catalog availability. All seven core checks plus updater and backup readiness passed; optional email/integrations remain unconfigured where environment credentials are absent. The existing onboarding widget was collapsed through its normal control during the browser checks.

Evidence: `quality-reports/integrations-deployment/report.json`, `diagnostics.json`, `deployment.json`, and desktop/mobile screenshots. Live checks preserve configuration; save/reset/provider-consumer behavior was exercised in disposable tests. No real email, provider sign-in, or payment was initiated.

Deployment records and the hashed source manifest are retained under `/opt/assozeta/builds/integrations-20260918`. Backend image: `sha256:97b8643d56cb63654ae2fa32d562ad114bee8b7f030b95cfe7a522d9f50b853e`; web image: `sha256:10f793e18f7095447c1dfa7b16fda484475b153066942401b3da3786fa4fb8ca`. Updater and renderer images are unchanged. A fresh pre-migration backup is retained at `/opt/assozeta/releases/operations-20260918/selfhost/backups/assozeta-20260918T143744Z.tar.gz`; the previous release directory remains available. Environment, application-data hashes and unrelated container states were compared and preserved. The final acceptance report was updated after the source package and is stored separately as deployment evidence.
