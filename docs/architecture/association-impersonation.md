# Association impersonation

Association owners can use **Gestisci accessi → Impersona un utente** in the sidebar identity panel to enter an active athlete account linked through a non-deleted `Associate`, or a collaborator connected to that owner. Athletes and collaborators cannot initiate sessions. Administrators retain their existing global picker and endpoints. Owners have no separate impersonation menu entry; active impersonation sessions keep their switch and return controls.

The association endpoints are `GET /association/impersonation/users` and `GET|POST|DELETE /association/impersonation`. They use the original authenticated account even during impersonation. Switching replaces the original actor's session; it never creates an impersonation stack. No target credentials are issued.

`application/impersonation.py` centralizes eligibility. Cached, one-hour sessions bind actor, target, and association. Every HTTP request and WebSocket receive/send rechecks the actor, target, association and membership. Revocation, expiry, deletion, deactivation, and collaborator reassignment take effect without logging in again. Audit entries retain the initiating actor and include target, association, and session IDs.

`application/impersonation_scope.py` applies explicit tenant and person filters to athlete queries, including family subscriptions inside the initiating association. The dashboard uses that association even when the instance has another primary association. Resource references are checked before mutations. Shared medical certificates are copied before editing their metadata; detaching a shared document does not delete another association's document.

The athlete endpoint registry is fail-closed: when adding an athlete endpoint, include its name only after applying `scoped_queryset` and checking input references and side effects. Account-wide credentials, integrations and administrative functions are unavailable in association-scoped sessions. Collaborators retain their existing permission checks and owner scope.

Notifications carry `association_id` through storage and pushes. Scoped sessions see and mark read only matching notifications. Historical notifications without tenant provenance remain hidden in these sessions. Standalone medical uploads are bound to the session in cache until they are attached to a subscription.

The picker and identity panel use the initiating actor's endpoint and expose **Torna all’associazione**. Identity changes clear person-specific stores and reload the document, reconnecting sockets; other tabs follow the change. Invalid-session responses restore the original login, while stale responses cannot clear a newer session.

## Validation

- `application/tests/test_association_impersonation.py`: actor/target eligibility, lifecycle, tenant/person isolation for reads and writes, shared documents/certificates, audit attribution, and restoration.
- `application/tests/test_impersonation.py`: existing administrator behavior.
- `application/tests/test_impersonation_websocket.py`: revocation, membership/account changes, notification isolation and read mutations.
- `UI/src/utils/impersonation.test.js`: routing, switching, restoration, cache cleanup, cross-tab changes and stale-response handling.

Backend checks require valid test Ed25519 signing/verification keys. Local verification used SQLite with `--nomigrations -o addopts=''` for the wider regression suite, plus an isolated PostgreSQL test database for all impersonation tests and payment approval (which uses PostgreSQL advisory locks). The PostgreSQL run used an in-memory task broker and email backend. No schema changes are introduced by this feature. Run frontend checks with `npm --prefix UI test` and `npm --prefix UI run build:vite:production`.
