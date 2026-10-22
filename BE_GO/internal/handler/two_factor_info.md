# Authenticated two-factor status

`GET /two-fa/info` returns `{"data":{"enabled":true}}` (or false), preserving
the existing Django endpoint. It requires Bearer authentication and the existing
identity/permission resolver. A valid Django session or BKN_AUTH cookie supplies
no REST credential. Anonymous impersonation headers retain their explicit denial
before the missing-credential 401. Negotiation and malformed cached sessions keep
their existing precedence; authenticated OPTIONS is supported and HEAD is 405.

The returned account is Django's scoped request.user. A permitted collaborator
reads the association owner's flag, even when the collaborator's own flag differs.
The permission is `other.settings.read`; the FULL collaborator role retains its
existing exception. An administrator must explicitly impersonate an eligible
account. Association-scoped athlete impersonation cannot access this account-wide
operation. Association-scoped collaborator requests still validate referenced
foreign resources before reading status. Actor, effective person and scope remain
distinct throughout admission.

The shared account SELECT includes `two_fa AS account_two_fa`. The alias keeps the
admission snapshot separate from LoginChallenge's locked `two_fa` field, and does
not change the database schema. The service accepts a resolved identity and reads
its scope snapshot without an additional query or reading the OTP secret. It
rejects missing identity and cancelled contexts. Every new HTTP request reloads
current account state through the existing authentication and scope repositories.

Ten portable contracts run on actual Django ASGI/Uvicorn and the Go executable.
They cover the rules above, exact response envelopes and OPTIONS metadata,
English/Italian missing-credential errors, inactive/deleted accounts, malformed
sessions and 32 concurrent requests. The adapter also toggles the owner's flag
twice, reusing owner and collaborator credentials against each backend. Managed
tables and valid/malformed session cache bytes remain unchanged in these fixtures;
fixture teardown restores the preexisting model snapshot. Shared session
middleware retains its invalid-password-hash revocation behavior.

The PostgreSQL integration check measures account lookup plus identity resolution:
one SELECT for direct owner/athlete access and two for collaborator access. The
status service itself adds zero queries. Thirty-two concurrent mixed calls use
48 SELECTs and at most four database connections in the test pool. This is scoped
admission evidence, not a count for the entire HTTP middleware chain or a latency
improvement claim. Public HTTP tests separately exercise real impersonation/cache
paths and foreign-reference denial.

Run `TESTS/adapters/django/accounts.py` for the full private fixture suite, or
`accounts.py --only two-factor-info` for this component and its supporting
account/identity/session/refresh/identifier fixtures. These use current-model
run-syncdb, not historical migrations. This endpoint only reads status; [2FA updates](two_factor_update.md) now have
separate write/effect contracts. Setup/QR, full global middleware, versioned proxy
routing and production activation remain unfinished.
