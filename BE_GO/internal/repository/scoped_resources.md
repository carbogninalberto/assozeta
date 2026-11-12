# Association-scoped resource reads

`ScopedResources.Available` implements the record predicates from
`BE/application/impersonation_scope.py:scoped_queryset` over existing PostgreSQL
tables. It returns the visible subset of up to 512 canonical UUID references in
one parameterized query. It accepts a sqlx database or the transaction owning a
business operation. Missing references are not authorized; errors return no IDs.

The trusted service supplies association, effective person/role and optional
selected group separately. A request header must never become the association or
role. The group only adds predicates; it cannot remove the association boundary.
Unbound administrator sessions use a different authorization path and must not
invent an association to call this repository.

The supported kinds are association, member, subscription, payment, invoice,
course, course subscription, installment, carnet, carnet subscription, attendance
registry/day, group, tag, subscription file and medical certificate reference.
Resource names and SQL identifiers come from a closed internal registry.

Athlete family visibility uses existing subscription tax codes, visible member
links and subscription/payment relationships, constrained within the association.
The SQL keeps the current manager differences:

| Model/query | Existing behavior preserved |
| --- | --- |
| Subscription | Selected-group filter; deleted subscriptions remain visible and can seed family tax codes |
| Associate, course, payment, invoice, installment | Selected-group and nondeleted predicates |
| Course subscription | Nondeleted predicate; both subscription and course must belong to the association |
| Carnet subscription | Both subscription and carnet must belong to the association |
| Related FK joins | Do not automatically inherit related model deletion/group filters |
| Athlete invoice | Requires a visible payment, including its own deletion/group predicates |
| Athlete family seeds | Related associate tax code is used without the associate manager; null/empty codes excluded |

These are compatibility semantics, not evidence that every business endpoint
should expose all returned records. Endpoint operation restrictions, instructor
rules, nested-reference traversal and returned-query filtering remain service
responsibilities. The composed `ScopedRequestGuard` now performs the association
operation/nested-reference stage; it does not replace those endpoint rules.
A successful lookup does not lock a record against concurrent
changes. Writes must coordinate authorization and invariants in a transaction.
The medical kind covers linked subscriptions only; the guard separately reads
upload cache grants. Document authorization and shared-certificate cloning remain
separate work.

Run `python TESTS/adapters/django/accounts.py` with the documented Python 3.13.2
environment. Its scoped adapter writes synthetic business rows using bulk inserts
to avoid unrelated creation hooks and compares 67 explicit expected visibility
sets in actual Django and Go. It covers foreign tenants, other families, legacy
deleted rows, cross-association parent links, selected/foreign groups and invoice
payment visibility. Transactional changes remove a family seed and a payment;
both runtimes immediately observe lost access, then roll back the fixture changes.

The integration test counts one query per resource batch, including 1, 16, 128
and 512 distinct existing tag IDs; 32 concurrent reads use four connections with
none left in use. Cancellation, malformed scope/references, unknown resource
kinds, oversized batches and database failure return no authorized rows. Django
readback compares every seeded resource table as well as identity/audit state.
This is bounded-query component evidence, not a throughput benchmark, historical
migration rehearsal, HTTP contract or completed resource-authorization workflow.
