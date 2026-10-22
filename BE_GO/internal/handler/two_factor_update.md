# Two-factor updates

`POST /two-fa/update` now runs through the explicit Go composition root. The
handler parses transport data, the service validates OTP and owns the transaction,
and the repository locks and updates the existing `bakney_user` row. There is no
new schema or operational setting. Django remains the migration/production owner.

All three fields (`enable`, `secret`, `otp`) are required. Existing Python
truthiness is preserved, including truthy strings such as `"false"`. A six-character
OTP with a nonempty secret is verified even when disabling; malformed secrets can
therefore fail before disabling. Disabling retains the old secret. Enabling requires
a valid current OTP and stores the supplied secret. Invalid OTP returns the existing
400 envelope; missing fields and incompatible types retain the current 500 boundary.
Successful messages, JSON/form/multipart parsing, OPTIONS and unsupported-method
behavior match the declared Django contracts.

Bearer authentication and `other.settings.update` authorize the resolved scope
account. Permitted collaborators update their owner's settings. Administrators
must impersonate, association-scoped athlete impersonation is denied, and foreign
referenced resources remain forbidden. The parsed body is retained at the transport
layer so scoped admission and the mutation do not consume it twice. Actor,
effective person and mutation target remain distinct. Audit attribution uses the
original JWT actor even when a different valid Django session cookie is supplied;
impersonation session, effective target and association are stored in existing
`additional_data`. Audit/index association resolution retains target-then-actor
precedence. Existing audit changes include the synthetic test secret just as Django
does; no real credentials or customer data are used in evidence.

## Confirmed Django defect and intended contract

The failure fixture first exposed an orphan audit when Django's user UPDATE failed:
its pre-save audit had already committed. Both backends now put the user change,
audit and association index in one transaction. Django reloads the target under
`SELECT FOR UPDATE` and saves only the applicable 2FA columns. Go uses the same row lock
and limited UPDATE. This also prevents a stale request snapshot from overwriting
concurrent password/profile changes. A failed user, audit or index write rolls back
all effects. Repeated identical updates produce no additional audit. These are
explicit bug fixes under requirement 10, not copied accidental failure semantics.

## Evidence and limits

The portable contract and backend-specific fixture are
`TESTS/contracts/test_two_factor_update.py` and
`TESTS/adapters/django/two_factor_update.py`. They exercise actual Django ASGI and
the compiled Go root on the same private PostgreSQL/Redis fixture. Expected
responses and durable effects are declared independently; all managed-model
snapshots and session bytes are checked and restored. The focused command is:

```sh
python TESTS/adapters/django/accounts.py --only two-factor-update
```

Use the exact Python version pinned in `BE/Dockerfile`. The full accounts adapter
also includes these checks. Coverage includes enable/disable/no-op, OTP and input
failures, session/JWT audit precedence, collaborator/admin/impersonation scope,
foreign-reference denial and three database-trigger failures. Two locked-row HTTP
scenarios per backend prove concurrent profile/password preservation and one audit
for identical concurrent writes.

The tagged PostgreSQL service test measures four SQL statements for a single
athlete transition and 66 for 32 concurrent identical calls: two per request plus
the association lookup and audit insertion for the single actual transition.
These counts exclude BEGIN/COMMIT and HTTP authentication/middleware. The test
uses at most four database connections, proves deadline cancellation while blocked
on a row lock, and checks subsequent pool recovery. Service tests bound conflict
retry to three attempts and prevent commit after cancellation following audit.
This is a fixed-size mutation, not application-wide performance evidence.

Initial red evidence includes `http-route-red.log` (Go route absent),
`atomic-reference-red.log` (Django orphan audit), and `composed-first.log`
(impersonated body consumed twice), under
`quality-reports/go-migration/two-factor-update/`. Final verification is recorded
in the migration evidence index and ledger. Current-model run-syncdb does not
prove historical migrations. Setup/QR, full authentication/middleware coverage,
versioned proxy routing, unchanged-frontend use and release activation remain open.
