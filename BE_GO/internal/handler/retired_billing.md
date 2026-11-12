# Retired software billing compatibility

The composition root registers `GET /billing/active-plan` and
`POST /billing/checkout` as legacy compatibility endpoints. Current Django retains
them for older clients after removing software subscription gating and platform
checkout. Association/course memberships, payments and communications remain in
the supported migration scope; this change does not retire those workflows.

After the existing Bearer, identity and scope admission, an association-scoped
account receives HTTP 410 with the existing endpoint message. An athlete or other
non-association role receives 403 `{"msg":"Info not available."}`. A collaborator
maps to the active association owner; these two routes retain their permission
registry exclusion. Administrators must impersonate. Association-scoped athlete
sessions are denied by the operation guard before reaching the role check, and
association-scoped collaborators still cannot reference foreign resources.

The service receives a resolved identity and only chooses whether the association
tombstone applies. It has no entitlement, payment or provider dependencies. It is
not an authorization gate for other business endpoints. No old billing record is
read, created, renewed or deleted by the service. Existing stored plan states do
not change the response.

The transport uses the same protected REST admission helper as `/two-fa/info`.
The helper preserves negotiation, current Bearer authentication, anonymous forged
scope denial, missing-credential localization and resource/body-error precedence;
each handler keeps its own service call, DTO, OPTIONS metadata and method handling.
AllowAny routes retain their separate anonymous-scope behavior.

The checkout endpoint does not parse an otherwise unused request body. An ordinary
authenticated owner still receives 410 for malformed JSON or unsupported media.
An association-scoped impersonation request must parse and validate its resource
references first, so malformed JSON remains 400 and foreign references remain
403. Existing session/audit middleware is retained; cached sessions and BKN_AUTH
cookies do not replace REST Bearer authentication.

Ten portable contracts first pass on actual Django ASGI and fail on unregistered
Go routes, then pass against both real HTTP servers. Missing, expired, cancelled,
paid and no-expiration plan scenarios retain the same tombstone. Role/scope,
method/OPTIONS, malformed-session, body parsing and 32 concurrent calls are
covered. All managed model tables and valid/malformed session cache bytes remain
unchanged by the declared requests, including legacy entitlements. Fixture cleanup
restores preexisting state. General shared session hash-revocation behavior remains.

The PostgreSQL integration check traces real account/identity queries, forbids
legacy billing-table queries, and expects one SELECT for direct accounts or two
for collaborators. The tombstone service adds no query. Thirty-two mixed calls
expect 40 SELECTs and at most four test database connections. This is scoped
service evidence, not an entire middleware query count or performance improvement.

Run the full accounts adapter, or `accounts.py --only retired-billing` for this
component with its supporting identity/session/refresh/identifier/2FA fixtures.
Fixtures use current-model run-syncdb, not historical migrations or backup restore.
Global middleware, versioned proxy routing, supported association payment flows
and backend activation remain unfinished.
