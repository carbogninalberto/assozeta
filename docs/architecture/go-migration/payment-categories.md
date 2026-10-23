# Payment-category baseline and compatibility work

The four payment-category routes remain owned by Django. Go implementation and
portable HTTP verification are in progress; this document does not establish Go
parity, frontend acceptance, or release readiness.

## Intentional baseline corrections

Actual Django requests with signed JWTs, the complete middleware stack and a
private PostgreSQL database confirmed these defects in the previous baseline:

- PATCH could transfer an owned category to another association or make it global.
- PATCH with a fresh category UUID cloned the row instead of preserving identity.
- An association account without an active association could update or delete a
  global category.
- A category INSERT failure after nested VAT creation left VAT and audit records
  committed independently.

These effects are not supported compatibility requirements. UPDATE now retains
both category identity and association. Sending the current values remains valid;
changing either produces a field-specific 400 response. Missing or deleted
association records cause all three mutations to return 403 with
`{"error":"user not authorized"}`. LIST still permits those accounts to read
global categories.

Each mutation enters `atomic_audit_mutation()` and locks the current owner user,
active scoped association, then existing category, in that order. The locked user
is checked again for active association-account status. VAT, category, audit entry
and audit-index writes commit together. A waiting PATCH or DELETE reads current
category values after acquiring the row lock, preserving intervening changes.

CREATE continues to force the authenticated association and accepts explicit
category and new VAT UUIDs subject to uniqueness. Supported writable fields,
including `creation_date`, `archived` and `deleted`, remain writable. DELETE is
soft deletion; a repeated DELETE produces no duplicate audit transition. Empty
nested VAT on PATCH remains a no-op; null and existing nested VAT IDs retain DRF
validation. Audit attribution retains the initiating actor. Collaborator VAT
audit entries retain the existing actor-association fallback, which can leave
them without an association index.

## Verification

`BE/application/tests/test_payment_category_integrity.py` adds 13 regression
tests with real database failure injection, complete managed-table snapshots,
JWT admission, collaborator controls and concurrent PATCH/DELETE requests.
Eleven initial tests produced 28 failing subtests on the original implementation.
After correction, the parent independently ran all 13 tests plus six existing
atomic-profile audit tests successfully, with no skips.

Another 188 existing payment, permission-registry, association-impersonation,
administrator-impersonation and audit-resolver tests passed with private
PostgreSQL and Redis. The first broader run lacked notification Redis and failed
one test; the complete rerun used a disposable real Redis pool and passed.
All owned services were stopped after each run. These checks initialize the
current Django model schema; they do not prove migration-history compatibility.

Run these test modules in the supported backend test environment:

```sh
python manage.py test \
  application.tests.test_payment_category_integrity \
  application.tests.test_atomic_profile_audit \
  application.tests.test_payment_views \
  application.tests.test_permissions_registry \
  application.tests.test_association_impersonation \
  application.tests.test_impersonation \
  application.tests.test_audit_resolvers
```

Go HTTP responses, complete durable effects, malformed-input behavior, query
scaling, concurrent mutations, versioned routing and unchanged frontend workflows
remain separate acceptance gates. A generated fixture or passing Go unit tests
alone cannot establish this endpoint family's compatibility.
