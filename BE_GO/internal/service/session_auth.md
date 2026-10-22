# Django cache-session identity (not public HTTP composition)

`SessionAuthentication.Resolve` reads the identity used by Django's
`AuthenticationMiddleware` and `AuditlogMiddleware` before a view runs. Its result
is a `SessionActor`, separate from Bearer authentication, effective identity and
resource scope. It grants no API permission. The composition root uses this service for the public refresh, identifier, 2FA
and retired-billing routes, and now public login.

## Observed contract

The real middleware chain and current database establish these behaviors:

- The cookie key must contain at least eight characters. Missing cache data,
  cached `None`, corrupt serialization and cache-load failures produce an empty,
  anonymous session. Load failure does not delete the cache row.
- A valid cached dictionary without authentication data is anonymous but retains
  its session key, even when the dictionary is empty. Non-dictionary decoded
  values cause an authentication error.
- Only the four configured backend names are recognized. Unknown/missing
  backends and missing accounts retain the anonymous session without deletion.
- `User._default_manager` is **`original_objects`**, despite the filtered
  `User.objects` manager used by other authentication operations. Thus an active,
  soft-deleted user can remain the session audit actor. ModelBackend and the Google, Apple and ORCID backends reject inactive users.
  The latter behavior follows social-auth-app-django 6.1.0: `get_user` now uses
  `filter_active_users` with the default `is_active=True` filter. Assozeta does
  not override `SOCIAL_AUTH_ACTIVE_USERS_FILTER`. The earlier 5.4.3 dependency
  retained inactive social actors; that historical behavior is superseded.
  The separate `SessionAccounts` query deliberately has no deletion predicate.
- A resolved user's current stored password and Django SECRET_KEY determine the
  SHA-256 session HMAC. Missing/mismatched hashes delete the session. Disabled
  accounts in all four backends are rejected before hash validation without
  deletion, including wrong hashes and oversized integer hashes.
- Each resolution rereads the current account with at most one parameterized
  SELECT. No account data, result or password hash is cached in the service.
  Session reads preserve serialized bytes and do not renew the TTL.
- A stored hash containing an integer beyond Python's 4,300-digit string limit
  raises an authentication error without deleting the session, including nested
  list/dictionary/tuple values. Ordinary mismatched numeric hashes still revoke it.

This matches the configured empty `SECRET_KEY_FALLBACKS`; fallback-key rotation
and general session writes have not been implemented.

## Cache and resource boundary

The repository reads cache database 1 at
`:1:django.contrib.sessions.cache<session-key>`. It requires disabled Redis
retries and context timeouts. GETRANGE bounds the response to 1 MiB plus one
overflow byte; accepted encoded data is at most 1 MiB. A normal lookup uses one
cache read, and invalid-hash revocation adds one DEL. Database and deletion errors
propagate; cache-load errors become anonymous state, except observed context
cancellation, which propagates.

The closed protocol-4/5 decoder accepts primitive strings/bytes, numbers, booleans,
None and supported dictionaries/lists/tuples. It never executes Python object
constructors. Integer cache optimization, memo references and frame boundaries
are handled. Stack, memo and individual containers are bounded at 8,192 entries;
materialization admits at most 8,192 values and nesting depth 128. Shared references
cannot expand into an unbounded tree. Raw integer text is bounded at 4,300 bytes;
pickle integer operands at 2,048 bytes.

These are explicit Go limits, **not equivalent Django limits**. Arbitrary pickled
objects, recursive containers, non-string dictionary keys, other protocols,
lone-surrogate strings and oversized legacy session data are not proven compatible.
Their prevalence and required support must be resolved before activation. The
[HTTP audit boundary](../middleware/session_audit.md) now characterizes cookies,
Vary and request metadata on the shipped ASGI transport. General session saving
and rotation, complete exception/middleware policy and remaining route wiring
remain separate work. The public refresh route now uses this resolver through
`DjangoSessionAudit`, including the registered public-login route. `config.SessionSecret` now reads existing `SECRET_KEY`; its 67-case
settings oracle is documented in [configuration](../config/README.md).

## Executed evidence

`TESTS/adapters/django/session_auth.py` declares 54 expected actor/error/cache
outcomes before invoking the actual conditional-session/authentication/audit
middleware on private PostgreSQL and Redis. The same persisted cache bytes are
then consumed by tagged Go integration tests; Django checks Go's deletion effects.
Managed-model snapshots must remain equal after explicitly restoring fixture changes.

Twenty additional same-key observations across all four backends assert current
email, inactive rejection, reactivation with soft deletion and password-change
revocation in both runtimes. Thirty-two
concurrent resolutions perform exactly 32 SELECTs and cache reads, using pools of
at most four connections. Tests also cover a 2 MiB cache value with a bounded
response, paused Redis deadlines and pool reuse, precancellation, closed database
and unavailable cache. Unicode keys are assigned directly to Django's cookie
mapping; those cases do not characterize HTTP cookie decoding.

Fixed CPython vectors caught shared-container expansion, an unassigned memo lookup
and sparse-memo indexing before fixes. Constructor tests rejected unsafe retry and
timeout configurations before the production guard. Four more red comparisons
caught erroneous deletion on oversized integer hash conversion. A 15-second codec fuzz run
completed 525,643 executions. Logs are local under
`quality-reports/go-migration/session-auth/`.

The Python 3.13.16 / Django 5.2.18 / social-auth-app-django 6.1.0 focused run
passes all 54 cases, twenty state observations and 27 HTTP contracts per backend.
HTTP subcases cover active, deleted and inactive Google/Apple/ORCID sessions,
including wrong and oversized hashes; cache bytes remain unchanged on rejection.
Nine red Go comparisons preceded the inactive-user guard correction. Evidence:
`quality-reports/go-migration/security-baseline/session-auth-red.log` and
`session-auth-current.log`. This does not revalidate the complete accounts suite.

Run the focused component (including its supporting identity fixtures):

```sh
quality-reports/go-migration/python31316/bin/python TESTS/adapters/django/accounts.py --only session-auth
```

Omit `--only` for the complete accounts adapter. Both create a disposable
current-model schema using `migrate --run-syncdb`, not historical migrations.
Neither run proves successful public login or full session/backend-switch parity.
