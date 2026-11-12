# Profile update contract — implementing

`PATCH /profile/update` is explicitly composed in `cmd/server/main.go`. The
transport uses the existing JWT, impersonation, scoped-reference and permission
admission, parses the request, invokes the service and formats the response.
`OPTIONS` retains the reference metadata. Production API selection remains Django.

## Fields, identity and response

The legacy serializer reads `user_data` without validated-data coercion. The five
required personal keys are `first_name`, `last_name`, `username`, `avatar_image`
and `sport_association`. Null personal values are skipped. Username uses Unicode
uppercasing; other text fields preserve Python-style conversion. String avatars
use the shared 512-pixel upload helper, including its decoded-file size threshold.

Collaborators update their effective personal record. Only a locked role-1 user
can update an association, with matching trusted owner and optional impersonation
scope. The DTO lists 17 mandatory association keys, four optional nullable fields
and five optional non-null fields. `checkout_info` always accepts null. JSON audit
text preserves Python sorted-key/ASCII/spacing behavior; accepted BooleanField
values are normalized for storage while audit values retain the reference text.
Unknown fields are ignored, but referenced foreign records still face admission.
Repository column identifiers come from a fixed whitelist; all values and scope
predicates are parameterized.

Success returns `role` and the personal `user_data`; owners additionally receive
the base association and registration-feature list. Presentation images use the
200-pixel helper through the same process-wide pool. The response has no login
tokens, message, membership-card creation or profile-info augmentation.

## Transaction and confirmed reference repair

Both runtimes lock and re-read the person, then the association when requested.
Selected fields, changed-field audit entries and their association indexes commit
in one transaction. Audit attribution uses the authenticated actor, effective
person, impersonation context, correlation ID and remote IP. Passwords, unrelated
association fields, sessions and jobs are not modified.

The first HTTP characterization demonstrated two Django defects: a missing
association key left the personal change committed, and an audit-index insertion
failure returned success. The repaired reference wraps the complete mutation in
`transaction.atomic()`, locks both records and saves only selected association
fields. The Go service implements this repaired invariant explicitly. Both roots
roll back personal, association and audit effects on field/database/index errors.

Serialization still occurs after commit. A role-4 label error or missing owner
association during response construction returns the reference 500 while retaining
the mutation; the adapter verifies that boundary. The response preserves this
request's committed first/last name, username and avatar when another writer
commits before response reads; a failing unit test preceded this correction and
the competing HTTP writers now assert their individual response names. The service retries credential
transaction conflicts at most three times. Image, repository, scope and context
failures abort without committing.

## Executable evidence and limits

Run `python TESTS/adapters/django/accounts.py --only profile-update` with the
production Python dependencies and local disposable PostgreSQL/Redis/native codec
toolchain. This runs the same portable HTTP expectations against actual Django
ASGI and Go roots, then the tagged PostgreSQL mutation integration test.

The focused suite passes 52 HTTP/status/body/durable-effect/audit cases and four
locked-row scenarios per backend: a concurrent credential write, duplicate profile
writes, successive profile writers and an unrelated association-logo write. It
covers all supported roles, impersonation, denied/forged scopes, malformed inputs,
null/conversion behavior, native avatars and injected audit/index/database errors.
Snapshots check managed models and cache state; fixture cleanup restores them.

Measured mutation statements are 4 SELECT / 2 UPDATE / 4 INSERT for an owner
change and 3 / 2 / 0 for an unchanged repetition. Twenty-four competitors execute
73 / 48 / 4 with exactly two audit records. Pre-write and mid-write cancellation
roll back, release connections and permit recovery with a maximum pool of four.
Direct foreign-owner/scope and unlisted-column probes reject writes. These counts include the shared transaction fence and
exclude authentication and response reads and are not application-wide latency or
memory claims.

Red and green logs are under ignored `quality-reports/go-migration/profile-update/`:
`dto-red.log`, `service-red.log`, `response-red.log`,
`http-django-atomic-confirmed-red.log`, `http-route-red.log` and
`association-audit-final.log` and `response-snapshot-final.log`. The independent profile/password adapter also exercises
both profile writers against both password writers.

This is current-model fixture proof, not historical-schema or restore proof.
TIFF/AVIF codecs remain explicit gaps. Frontend/versioned-API workflows, complete
image coverage, cross-endpoint races involving other serialized preferences,
release delivery and activation remain open; the ledger entry is
**implementing**, not verified.
