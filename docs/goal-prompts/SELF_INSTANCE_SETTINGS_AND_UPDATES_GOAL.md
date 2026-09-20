# Goal prompt: Self Instance settings and owner-triggered updates

Implement a dedicated **Self Instance** tab within Assozeta’s **profile settings**, visible and accessible **only to the instance owner**. Consolidate instance branding and configuration there, and let that owner review release notes and update the self-hosted installation to the latest stable release from the application.

Deliver the complete UI, backend API, update runner, deployment integration, tests, and operator documentation. A button that only shows a shell command is not completion.

**Existing installations must self-configure this feature when their owner runs the normal supported update.** The upgrade must automatically provision the runner, service wiring, credentials, persistent state, and configuration defaults. Standard existing installations must not require a separate installation command, manual `.env` or Compose edits, or additional host setup after updating.

**Rollout amendment:** The user has accepted fetching a new update entry point
once for pre-feature installations. Document and verify running the release's
bootstrap from inside the existing `selfhost/` directory. Fetch the bootstrap
without replacing live deployment files before backup; the update itself must
configure the runner automatically. This supersedes the original requirement
that the unchanged legacy CLI must invoke the new tooling. Subsequent updates
must work through the owner interface without additional host setup.

## Context and existing work

Inspect the working tree before editing and preserve existing changes. Relevant implementation:

- `UI/src/routes/profile/Profile.svelte` and `ProfileMenu.svelte`: profile tabs and navigation.
- `UI/src/routes/profile/sections/Settings.svelte` and `InstanceBranding.svelte`: the recently added logo editor currently lives in general settings.
- `UI/src/store/instanceStore.js`: runtime branding, config cache, and logo upload.
- `UI/src/utils/ApiMiddleware.js`: multipart handling fixed to preserve boundaries after `fetch-intercept` converts uploads to `Request` objects. Preserve this fix and its regression tests.
- `BE/instance/`: configuration, reconfiguration, branding, and owner permissions.
- `BE/application/views/profile_views.py`: currently exposes the logo-edit capability through general settings.
- `selfhost/bin/assozeta`, `selfhost/compose.yml`, and `selfhost/README.md`: production lifecycle and installation tooling.
- `.github/workflows/publish-images.yml`: release tags, backend/web/renderer images, and self-host release archives.

The existing `upgrade VERSION` command creates a backup, changes the configured image version, pulls images, runs initialization/migrations, starts services, and checks public readiness. Production lifecycle operations share `selfhost/.lifecycle.lock/`. Image-pull failure restores the prior configured version; this is not a complete rollback strategy after migrations.

## 1. Self Instance tab

- Add a dedicated, clearly named tab in `ProfileMenu.svelte`, rendered through `Profile.svelte`, using the existing navigation and Italian UI conventions. Show it only to the instance owner. Support page refresh, restored navigation state, and mobile layouts; a non-owner must not gain access through a restored or manually selected tab.
- Move the logo editor out of general settings into this tab, without duplicating it. Preserve preview, validation, upload errors, cancellation, immediate branding refresh, and aspect-ratio-safe display.
- Group supported instance identity/branding fields here: instance name, abbreviation, primary color, support email, and logo. Reuse the existing reconfiguration API and verify which fields it accepts.
- Include installed version/build information, update availability, release notes, and update status.
- Keep association business settings and personal account settings in their existing sections. Stripe secret keys remain server environment configuration; do not expose them through this tab or API.
- Offer clear saved, unsaved, loading, unavailable, and error states. Instance administration must not depend on loading unrelated accounting settings.

## 2. Ownership and authorization

- The owner is exactly `InstanceConfiguration.primary_association.user`. Implement an explicit owner-only predicate for this tab and its new administration/update APIs. The existing `is_primary_association_owner_or_superuser` helper is broader than this requirement and must not be used unchanged to grant access.
- A Django superuser who is not the configured association owner must not see this tab or trigger its update action. Existing superuser maintenance permissions on other interfaces are separate from this feature.
- Derive UI capabilities from the server. Enforce owner authorization independently on the new administrative endpoints, including starting updates and reading operational details.
- Check the actual authenticated identity when collaborator or impersonation context changes `request.user`. An association collaborator must not inherit update privileges from the owner.
- Restrict this tab to configured self-hosted instances and their actual owner. Reject direct unauthorized API requests even when navigation is hidden. An unconfigured instance or one without a primary association has no owner access to this tab.
- Keep private operational information out of the public bootstrap/configuration API.

## 3. Releases and complete release notes

- Verify the canonical upstream repository from repository metadata and release workflows; it currently points to `carbogninalberto/assozeta`.
- Fetch release metadata server-side from the canonical upstream. Cache results, support an explicit refresh, and handle timeouts, rate limits, unavailable upstream, and missing notes.
- Use stable published releases by default, excluding drafts and prereleases. Compare versions semantically and normalize release tags against the image-tag conventions in the publishing workflow.
- Show the running version and build identity separately from the configured target version. Handle `dev`, commit hashes, `latest`, unknown versions, and installations ahead of the stable channel honestly.
- Show the latest release’s complete notes and every intervening release’s complete notes when an installation skips versions. Provide paginated access to older release history as well. Never silently truncate release bodies or omit additional result pages.
- Display versions, release dates, and upstream release links. Render Markdown safely, without executing embedded HTML/scripts or treating release text as commands.
- Resolve “latest” to an exact release before presenting the update action. Verify that the release’s required images and distribution artifacts are ready; a published release may appear before its build finishes.

## 4. Owner-triggered update flow

- Provide **Check for updates**, release review, and an explicit **Update to [version]** action. Before starting, show the exact target, relevant notes, backup behavior, and expected temporary interruption.
- Pin the reviewed target. If a newer release appears while the owner is reviewing notes, do not silently substitute it.
- Run the operation asynchronously and return a durable operation ID. Repeated clicks and request retries must not start duplicate upgrades.
- Report meaningful stages: queued, checking prerequisites, backup, downloading, migrations, restarting, checking health, completed, and failed/recovery required.
- Preserve progress across browser refreshes and application restarts. During expected downtime, show reconnecting/updating rather than logging the owner out or claiming success.
- Confirm success only after health checks pass and the running services report the intended release. Include operation history, actor, timestamps, source/target versions, and useful failure details with secrets removed.

## 5. Update execution and deployment

- Implement a narrowly scoped host-side runner or equivalent isolated update service that survives replacement of the API, workers, and web containers. An ordinary Celery task inside a container being upgraded is insufficient.
- Reuse or extend the existing lifecycle tooling, backup behavior, and locking. Coordinate UI-triggered updates with CLI backup, restore, upgrade, and other lifecycle commands.
- Give the runner only the required interface: validated release requests and durable status. Do not expose arbitrary commands, filesystem paths, image repositories, download URLs, or shell arguments supplied by the client. Keep the Docker socket and unrestricted host execution out of the web/API containers.
- Authenticate the application-to-runner channel and keep it private. Validate allowed targets again at execution time. Persist state where application container replacement cannot erase it.
- Determine how the target release’s Compose definitions, lifecycle scripts, and configuration requirements are applied alongside images. Preserve local environment values, secrets, data volumes, and installation-specific configuration. Support existing installations as well as new installs.
- Build automatic, idempotent provisioning into the upgrade lifecycle. Detect older installations, add only missing configuration, generate private runner credentials once, create persistent state with appropriate ownership, install/register the supported runner, and start it. Re-running an upgrade must neither regenerate working credentials nor duplicate services, mounts, or configuration entries.
- Solve the first upgrade from an installation that predates this feature. Its currently installed CLI may only replace images and may not know how to update its own scripts or Compose definitions. Trace and test the actual supported legacy update entry point and provide a working bootstrap/migration mechanism; adding setup code only to a new script that the old installation never executes is insufficient. Do not assume that database migrations inside the API container can provision host services or Docker mounts.
- Preserve operator customizations while upgrading managed distribution files. Track provisioning/schema versions and verify the runner handshake and readiness before enabling the owner’s update button. New installations must receive the same working setup automatically through the normal installer.
- Abort safely if prerequisites or backup fail. Handle pull, migration, restart, health-check, and runner-interruption failures explicitly. Preserve recovery artifacts and document restoration. Do not describe switching back to old images as safe database rollback after incompatible migrations.
- If automatic provisioning encounters an unsupported deployment or an actual failure, show a precise unavailable/error state and recovery guidance; never expose a functioning-looking update action. Missing manual setup must not be the normal outcome for supported existing installations.
- Disable actual production self-upgrade execution in `make dev` environments while keeping the tab, branding, release browsing, and a testable simulated update flow available.

## 6. Verification and documentation

- Test owner-only visibility and access, anonymous users, unrelated association owners, collaborators, and a non-owner superuser, including impersonation behavior. Verify that a superuser passes only when that user is also the configured association owner.
- Retain the logo upload/cache tests and the multipart regression through the actual request interceptor and middleware.
- Test release pagination, complete notes, stable filtering, version normalization, unavailable artifacts, upstream failures, and safe rendering.
- Test pinned targets, duplicate requests, competing CLI operations, durable progress, failed backups, failed downloads/migrations/health checks, and recovery after interruption.
- Demonstrate a successful update and a controlled failure in an isolated disposable installation with fixture releases or images. Verify data, environment configuration, branding, and the resulting running version. Do not upgrade the user’s actual installation merely to test the feature.
- Start an additional integration scenario from a pre-feature release with no runner, no runner credentials, and no new configuration. Run its normal supported update entry point. Verify that provisioning completes automatically and that the owner can subsequently trigger another update from the tab without manual host/configuration steps. Repeat provisioning to verify idempotency and preservation of custom environment values, secrets, and volumes.
- Run appropriate backend/frontend tests, the production UI build, and relevant self-host validation/smoke checks. Visually inspect desktop/mobile UI when browser tools are available; report any unverified interaction explicitly.
- Update operator documentation with automatic provisioning for existing/new deployments, the supported legacy upgrade path, runner privileges, release source, development behavior, backup/recovery procedures, and manual CLI fallback. Explicitly document exceptional deployment limitations rather than silently imposing extra setup on standard installs.

## Completion criteria

An owner can open **Self Instance** in profile settings, edit branding, see the installed and latest stable versions, read full release notes, explicitly start an update, and return after restart to a verified success or actionable failure state. Unauthorized users cannot access the tab or its update APIs. A pre-feature installation automatically gains a working runner and this owner workflow through its normal supported update, with no additional setup. Both that migration and a subsequent UI-triggered upgrade have been demonstrated in an isolated installation.

Finish with a concise report of implemented behavior, tests and upgrade simulation results, evidence of automatic setup for existing installations, and any remaining limitations. Resolve routine implementation choices autonomously within this scope.
