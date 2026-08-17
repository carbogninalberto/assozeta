# Permissions and access control

 [Docs hub](../README.md) · [Architecture overview](./architecture-overview.md) · [Functionality map](./functionality-map.md) · [Deployment/runtime](./deployment-runtime.md) · [Coverage gaps](./coverage-gaps.md)

## Authentication stack

Assozeta uses JWT-based authentication for API and browser session handoff:

- Login endpoint: `POST /oauth2/login`
- Response includes JWT access and refresh tokens.
- Access token is stored in frontend local storage (`sessionToken`) and also written as `BKN_AUTH` cookie for websocket auth.
- JWT is EdDSA-based and consumed by DRF and websocket middleware.

## Runtime permissions model

Permissions are enforced in middleware and checked when the user context is a collaborator:

- Core check flow in `BE/application/permissions.py` and `BE/application/permissions_registry.py`
- Middleware path in `BE/core/middleware.py`
- Collaborator impersonation and connected-user mapping in `BE/core/middleware.py`
- Websocket JWT auth via `BE/notifications/middleware.py`

## Permission registry strategy

- `BE/application/permissions_registry.py` is the source of permission requirements.
- Path matcher supports:
  - wildcard segments (`*`)
  - optional method-specific mappings
- Excluded paths (public or external callback style) are defined separately:
  - OAuth handlers
  - Stripe webhook endpoints
  - health checks and some plan/billing endpoints
  - public token-link endpoints
- Access decision:
  - if collaborator role is not allowed and path has a registry match: permission is required
  - if no match is found and access is not excluded: request is denied

## Current coverage snapshot

From current generated inventory (`docs/matrix/architecture-inventory.json`):

- backend endpoint patterns: **353**
- permission registry patterns: **241**
- permission coverage shortfall: **89** endpoint patterns without registry mapping
- permissions without backend match: **1**

Known current exclusion-style categories (not counted as required permission in some cases):

- OAuth routes (e.g. `oauth2/login`, `oauth2/signup`)
- Stripe webhook/public callback routes
- health checks (`health`, `check-inconsistencies`)
- selected superuser/public federation endpoints
- token-link public endpoints (`subscription/generate-token-link`, `subscription/validate-token-link-and-get-subscriptions`)
- `billing/active-plan`, `billing/checkout`, `search/profile/*`, `export-all-data`

## Frontend route authorization behavior

Route-level guards in `UI/src/routes.js` use:

- `canPerformAction(...)` against frontend permission catalog
- helper `checkAssociationAccess(...)` which also validates role and plan status
- `isPlanActive`, `isLogged` conditions in route conditions

Not every route currently includes explicit permission gating:

- total routes: **85**
- explicit route permission checks: **49**
- routes without route-level checks: **36**

Examples include login/welcome/onboarding and some scanner/invite/shared-calendar flows, where authorization is performed by server-side behavior or intentionally handled through UX entry state.

## What to keep in mind while documenting docs

- Permission checks can exist at:
  - route guard level (`UI/src/routes.js`)
  - backend middleware level (`BE/core/middleware.py` + `BE/application/permissions.py`)
  - endpoint-level business logic (`BE/application/views/*`)
- The frontend list can therefore look permissive on route entries that are redirected for auth/plan reasons by UI state and back-end policy.
- Use `docs/matrix/architecture-inventory.json` and this file as a combined evidence pair when documenting access behavior.
