# Public identifier availability

The composition root registers `GET /oauth2/check/email` and
`GET /oauth2/check/username`, with OPTIONS metadata and the existing Django
session/audit reader. The handler performs transport and admission; the service
owns validation and lookup ordering; the repository owns parameterized SQL.

These are public, global account lookups in the existing Django contract.
Supplied Bearer credentials still undergo current-account authentication and
identity/impersonation admission. Anonymous scope headers do not create identity.
An association-bound athlete cannot use these operations. Session audit identity
does not authenticate the REST actor. Do not reuse this repository as a scoped
business-resource reader.

Email and username comparisons use PostgreSQL `UPPER(column::text) = UPPER($1)`.
Deleted accounts are excluded; inactive accounts remain occupied. Input is not
trimmed or checked for email syntax. Limits count Unicode characters: 255 for
email and 150 for username. Duplicate query parameters retain their last value.
Missing required query parameters retain Django's production HTML 500 response;
too-long input returns 400 with `valid: false` and `invalid payload.`.

Occupied identifiers return 409, available identifiers 200. The presence of
`get_user`, even with an empty or false-looking value, enables email details and
username fallback. The email match takes priority; fallback runs only after no
email match. Each match selects the smallest UUID, as Django's unordered
QuerySet.first() does. Details contain only first_name, last_name, email and
user_id. Username checks never disclose these fields. This preserves the existing
public disclosure behavior; it does not introduce a new account-disclosure policy.

The service performs one SELECT for email and at most one additional SELECT for
fallback. Username uses SELECT EXISTS. The email query returns at most one row;
neither service loads an unbounded account collection. On the synthetic
current-model fixture, EXPLAIN ANALYZE shows a sequential scan and top-N sort for
email, and a sequential scan inside EXISTS for username. Query count and returned
rows are bounded, but scanned rows and latency are not independent of table size.
No performance improvement or new business-schema index is claimed.

`TESTS/contracts/test_identifier_availability.py` has 16 portable methods, run
against real Django ASGI/Uvicorn and the compiled Go server on the same private
PostgreSQL/Redis state. Explicit expectations cover duplicate selection, inactive
and deleted accounts, presence flags, Unicode limits, literal SQL-like input,
methods/OPTIONS, negotiation and authentication precedence, forged scope,
impersonation denial, malformed sessions and 32 concurrent requests. All managed
model tables and malformed session bytes remain unchanged after each backend.
Fixture cleanup also verifies that preexisting model state is unchanged.

The tagged PostgreSQL test checks query counts for 1, 16, 128 and 512 duplicates,
actual query plans, fallback ordering, cancellation/database failure and 32
concurrent reads with a four-connection test pool. Plans are saved under ignored
`quality-reports/go-migration/accounts/identifier-plans.json`. The production pool
is not claimed to have that test limit. Run the complete accounts adapter, or
`accounts.py --only identifiers` for this component with its required supporting
account/identity/session/refresh fixtures. Fixtures use current-model run-syncdb,
not historical migrations. Versioned proxy routing, complete global middleware,
remaining endpoints and production activation remain unfinished.
