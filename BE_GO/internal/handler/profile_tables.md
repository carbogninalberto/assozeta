# Table preferences — implementing

`POST /profile/settings/tables` preserves the partial
`UserTablesSettingsSerializer` contract. `OPTIONS` returns the reference metadata;
other methods and admission/negotiation errors use the shared REST transport.
The composition root constructs the service and SQL repository explicitly.

The only writable field is `tables_settings`. Missing and unknown fields are
successful no-ops. JSON objects, arrays, strings, booleans and finite numbers are
accepted; numeric precision and sorted/ASCII JSON audit text are preserved. A
JSON string remains a string. Form/multipart fields instead contain a JSON
document; file fields retain the reference filename-as-JSON behavior while their
contents are ignored. Direct null is a field-level 400, but form-decoded null
reaches the existing SQL NOT NULL constraint and returns 500. A null request body
returns `non_field_errors: ["No data provided"]`, including in Italian. Direct
non-finite numbers fail JSONField validation; form numeric overflow reaches the
storage failure boundary. The independent contracts record these differences.

A route-specific parser preserves unpaired UTF-16 escapes as invalid code-unit
strings until the service rejects the write, matching the Django/PostgreSQL
failure instead of silently saving U+FFFD. Literal replacement characters, valid
surrogate pairs and ignored fields retain their separate behavior. The same
parser is used for JSON bodies, form JSON and impersonation's cached body. Other
routes keep their existing parser. `surrogate-red.log` demonstrates the original
Go mutation mismatch; `surrogate-unit-red.log` precedes the parser implementation.
The focused HTTP suite and 571,538 fuzz executions pass after the correction;
fuzz coverage is bounded, not proof of all possible JSON inputs.

Identity ownership follows `request.user`, including its mapped association
owner for collaborators. It intentionally differs from personal profile changes.
The service validates that the trusted scope user matches the effective person
or that collaborator's connected owner. Existing `other.settings.update`, direct
administrator, impersonation and recursive foreign-reference rules run before
mutation. Audit attribution remains the authenticated actor, with the effective
impersonation target, correlation ID and remote IP kept separately.

## Atomicity and regression repair

The original Django serializer saved the entire stale user instance. An actual
locked-row HTTP test showed that saving table preferences overwrote a concurrent
name and credential change. Injected audit-index failure also returned 200 and
persisted preferences without their index. The repaired view validates the same
serializer, locks/re-reads the mapped user, and saves only `tables_settings` with
its audit and index in one transaction. Omitted fields perform no mutation.

Go implements the same invariant through a dedicated transaction interface. The
repository uses one parameterized user predicate with the soft-delete filter;
it exposes no arbitrary column selection. The service uses bounded conflict
retries and explicit audit writes. Failed storage/audit/index writes and request
cancellation roll back the preference change and its audit. No session, cache,
association, credential or job changes belong to this operation.

## Evidence and remaining scope

`python TESTS/adapters/django/accounts.py --only profile-tables` starts private
PostgreSQL/Redis and actual Django/Go roots. It passes 62 portable response cases
with independent persisted-row, audit/index and cache checks, plus three
locked-row scenarios per backend: unrelated profile/credential preservation,
duplicate preferences and ordered competing preferences. The full accounts
adapter includes this workflow.

The required `profiletablesintegration` suite measures 2 SELECT / 1 UPDATE /
2 INSERT across 1, 16, 128 and 512 table entries. Identical repetition uses
1 / 1 / 0, while an omitted field uses 0 / 0 / 0. Twenty-four concurrent writes
produce 25 / 24 / 2 and one audit. Cancellation before the row lock and during
audit insertion rolls back, releases connections and recovers within a four-slot
pool. Counts exclude HTTP admission; they do not prove application-wide latency
or memory bounds.

TDD and final evidence are under ignored
`quality-reports/go-migration/profile-tables/`: `http-initial.log` establishes
33 Django cases and the absent Go route; `django-atomic-red.log` and
`django-concurrency-red.log` expose the reference defects. `dto-red.log` and
`service-red.log` precede implementation. `http-after-django-repair.log` passes
36 reference cases and three races before Go's 404s. `surrogate-http-green.log` passes the
final focused HTTP/SQL suite. `full-final.log` and `full-final-results.json` record
the complete accounts regression passing after the parser repair.
`http-expanded.log` also records correction of the
initial null-body expectation; that expectation mismatch is not an application bug.

Fixtures use current models, not historical migration/restore data. The real
frontend workflow, versioned routing, release delivery and backend activation
remain separate open requirements. This ledger entry is **implementing**, not
verified.
