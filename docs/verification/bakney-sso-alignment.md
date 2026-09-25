# Bakney receiver alignment verification — 2026-09-25

## Delivery state

Prepared on `codex/selfhost-bakney-sso` in the isolated `assozeta-sso` worktree,
against PR #15's existing commit `b06b754`. The user subsequently authorized
committing and pushing the alignment changes on this branch.
This session did not modify either Bakney repository's source files.

## Implemented and reviewed

| Requirement | Implementation and evidence |
| --- | --- |
| Pairing directly in Panoramica | `SelfInstance.svelte` mounts `BakneyPairing.svelte` in Overview; actual browser screenshots reviewed at desktop and mobile sizes. |
| Readable/copyable administrator code | Protected generation response, encrypted backend storage, transient readable input and clipboard button. Browser checked copy equality; backend checks permissions and absence from ordinary/public responses. |
| Connection and forwarding status | Bakney's authenticated status verifies the binding; UI shows the verified association UUID with imported local name, last verification and a separate forwarding value. |
| Removal and regeneration | Local transactions are invalidated immediately; encrypted revocation outbox retries through the real Bakney status endpoint. Real browser run re-paired after regeneration and verified revocation/removal. |
| One shared protocol | Receiver uses Bakney's challenge/commit HMAC encoding, remote pairing/generation IDs, dedicated `handoff.html`, status and token APIs. Shared signature fixture matches Bakney's file byte for byte. |
| Public routing and privacy | Actual production Caddy configuration exercised for every `/bakney/v1/` route. Privacy headers checked; global referrer default now preserves stricter endpoint policies. Service worker bypasses auth navigation. |
| Normal users and stable identities | Local UUID, membership, association-owner activity and role checks. Tests reject excluded roles, instructors, connected users, disabled/draft memberships, missing and inactive accounts; no email fallback or auto-creation. |
| Completed authentication/MFA | Real Bakney password login rejected missing required TOTP, then accepted valid TOTP and completed automatic local login without a second login. |
| Browser state and PKCE | Separate HTTPS sites, secure temporary cookie, same-tab attempt, PKCE/code exchange, expired/replayed/wrong-state cases and generation invalidation exercised. |
| Session preservation and switching | Real browser failure tests preserve a valid existing session. Different identities require confirmation; caches are cleared and another open tab reloads after acceptance. |
| Migration | `0008` removes obsolete revision state and invalidates credentials/transactions from the incompatible draft protocol while retaining the stable instance ID. Migration behavior and drift checked. |

The authoritative wire contract remains Bakney's
`application/pairing/CONTRACT.md`. Receiver configuration, deployment and session
semantics are documented in [the integration guide](../contracts/bakney-sso-v1.md).

## Results

These results cover the protocol alignment before the subsequent Panoramica
layout/copy refinement and development-only HTTP support. Those follow-up
changes have not been tested; the retained screenshots show the earlier layout.

- Backend pairing, administration, auth and import regression suite: **133 passed**.
- Frontend Node suite: **130 passed**.
- Production frontend build: passed (existing accessibility/chunk-size warnings).
- Migration drift check: passed (existing missing static-directory warning).
- `selfhost/tests/validate.sh`: passed, including updater/restart/maintenance,
  configuration, quality, diagnostics and storage-image checks.
- `actionlint` and `git diff --check`: passed.
- Real cross-repository HTTPS run: **7 scenario groups passed**.
- Simulated Bakney CI fixture: **6 scenario groups passed**; this result is
  separate from the real cross-repository verification.

Backend command, inside the existing isolated test container:

```sh
python -m pytest instance/tests/test_bakney_sso.py \
  instance/tests/test_administration.py application/tests/test_auth_views.py \
  application/tests/test_auth_login.py application/tests/test_import_identity_media.py \
  -q --reuse-db
```

Real integration command (with the standard fixture environment and Docker
executable configured as described in the integration guide):

```sh
ASSOZETA_BAKNEY_BACKEND=/path/to/django-bakney-sport \
ASSOZETA_BAKNEY_UI=/path/to/svelte-bakney-dashboard \
node selfhost/tests/browser/bakney-sso.mjs
```

## Tested sources and scope

- Bakney backend: `21ec9155f363368e5b50540af32bf1c7513c81c6`.
  The snapshot also included contemporaneous tracked CI/documentation changes;
  their complete working-diff SHA-256 was
  `7e1331bd4a814988f38918d34d06de4b98fab2666c87200352f521b2426f1142`.
- Bakney frontend: `5b91bbe6645151d3d7220b94c94cbe57c8175518`, clean snapshot.
- Receiver: staged alignment changes on PR #15's branch.

The real run exercised both actual Django backends, the built receiver app,
Bakney's actual pairing components and transport, its actual password/TOTP login
endpoint, and its actual standalone handoff page. Bakney's complete dashboard
startup is outside the isolated component harness. No deployed account or
production service was contacted.

Both sites used distinct HTTPS names on port 443. Backends verified the fixture
certificate; Chromium accepted its self-signed certificate. Bakney's otherwise
unchanged pinned HTTPS transport had an explicit test-only allowance for the
private fixture DNS address. Independent negative scenarios reset fixture
throttle counters; this run is not a throughput/rate-limit assessment.

Safe JSON reports and reviewed screenshots are retained locally under
`quality-reports/bakney-sso/` (real) and
`quality-reports/bakney-sso-simulated/` (simulated). Temporary source copies,
containers and browser databases were removed. No browser trace or persisted
browser credentials are retained.

## Remaining deployment steps

Deploy the matching receiver migration, UI, Caddy, API, worker and beat together with Bakney's pairing backend
and frontend including `handoff.html`. Configure the canonical public HTTPS
origin and deployment-controlled Bakney API/UI addresses. Regenerate any pairing
created using PR #15's earlier draft protocol. This verification does not claim a
production rollout or full Bakney dashboard regression coverage.
