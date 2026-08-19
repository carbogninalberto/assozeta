# Frontend/backend contract

[Docs hub](../README.md) · [Architecture overview](./architecture-overview.md) · [Functionality map](./functionality-map.md) · [Permissions](./permissions-and-access.md) · [Deployment](./deployment-runtime.md)

This page explains how a browser action becomes an API or realtime operation. It is the compact source map for maintainers and automated readers.

## Frontend entry and navigation

The SPA entry is `UI/src/routes.js`. It uses `svelte-spa-router/wrap` and hash URLs (`/#/<route>`). Route components are lazy-loaded with dynamic imports. A route definition can combine authentication (`isLogged()`), role checks (`association` versus `athlete`), subscription-plan state (`isPlanActive()`), a frontend permission key through `canPerformAction()`, and local UI state updates (`currentPage` and `subPage`).

`UI/src/store/stores.js` persists selected stores in `localStorage`. Important session keys are `sessionToken`, `refreshToken`, `expires`, `role`, `permissions`, `userData`, and `isExpired`. This is client navigation state, not the authoritative authorization boundary.

## HTTP request path

1. A route component reads an endpoint from `UI/endpoints.js` or shared endpoint configuration and calls `fetch`.
2. `UI/src/utils/ApiMiddleware.js` adds `Authorization: Bearer <access-token>`, refreshes tokens when needed, and adds supported impersonation/group headers.
3. In development Vite proxies `/api` to Django and `/ws` to ASGI. In production Caddy performs the equivalent reverse proxying.
4. `BE/core/asgi.py` sends HTTP to Django. `BE/core/urls.py` composes application, chat, document, communication, instance, health, and schema URL trees.
5. DRF authentication and `IsAuthenticated` establish the user. Collaborators are evaluated in the context of their connected association user; superuser impersonation can select a user through the supported user-id mechanism.
6. `BE/core/middleware.py` and the permission registry evaluate collaborator access. Views and serializers then apply domain rules and read/write PostgreSQL, Redis, object storage, or an integration.
7. The response returns to the route component, which updates Svelte stores and renders the relevant view.

## Realtime path

| Channel | Backend route | Frontend consumer | Purpose |
| --- | --- | --- | --- |
| Notifications | `BE/notifications/routing.py` → `/ws/notifications/` | `NotificationWebSocket.js` | User notifications and updates |
| Updates | `BE/application/routing.py` → `/ws/updates/` | `UpdatesWebSocket.js` and scanner flows | Live application updates |
| Health | `BE/application/routing.py` → `/ws/health/` | `HealthWebSocket.js` | Connection/health status |
| Agent | `BE/application/chat/routing.py` → `/ws/agent/` | `AgentWebSocket.js` and agent widget | AI/agent conversation events |

`BE/notifications/middleware.py` authenticates the WebSocket handshake with JWT context. Redis is the Channels layer and event broker; it is not the durable source of business records.

## Async work

API views dispatch Celery tasks for work that should not block an HTTP request, including exports, reminders, payment reconciliation, certificate notifications, cleanup, and scheduled membership operations. `BE/core/celery.py` autodiscovers task modules, configures rate limits, and defines the Beat schedule. The `worker` consumes queued tasks and `beat` publishes periodic tasks through Redis.

## API and data boundaries

- Django models and migrations define durable relational state.
- Object storage holds uploaded files, generated documents, signatures, and attachments; the application controls access to private self-hosted objects.
- Redis holds cache, sessions where configured, Channels messages, Celery broker data, and ephemeral coordination state.
- The renderer is a private HTTP service used for PDF/document generation; it is not exposed by Caddy.
- SMTP, Stripe, Google, and optional AI providers are external boundaries and require instance configuration.

## Evidence map

| Concern | Authoritative files |
| --- | --- |
| UI route registry | `UI/src/routes.js` |
| UI endpoint catalog | `UI/endpoints.js` |
| UI request/auth middleware | `UI/src/utils/ApiMiddleware.js` |
| UI permission catalog | `UI/src/utils/Permissions.js` |
| HTTP URL composition | `BE/core/urls.py`, `BE/application/urls.py` |
| ASGI and WebSockets | `BE/core/asgi.py`, `BE/application/routing.py`, `BE/notifications/routing.py`, `BE/application/chat/routing.py` |
| Auth and collaborator context | `BE/core/middleware.py`, `BE/core/jwt_backend.py` |
| Permission mapping | `BE/application/permissions_registry.py` |
| Async schedule | `BE/core/celery.py`, `BE/**/tasks.py` |
| Runtime services | `selfhost/compose.yml`, `selfhost/compose.dev.yml`, `selfhost/caddy/Caddyfile` |
| Machine inventory | `docs/matrix/architecture-inventory.json` |
