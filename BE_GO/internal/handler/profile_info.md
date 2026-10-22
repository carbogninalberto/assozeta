# Authenticated profile information

`GET /profile/info` returns the existing `info.role` and `user_data` envelope.
The service receives resolved actor, effective person and scope separately. It
serializes the effective person, including a collaborator's own preferences,
avatar and invoice-deletion setting, then reads the admitted owner's association.
The repository constrains the details query to that person/owner relationship;
the service also checks returned person/association identities and an explicit
impersonation association. Association-bound foreign references and forged impersonation retain
the shared admission denials.

Profile access is an existing collaborator permission exemption, including
restricted and instructor roles. An administrator may read the profile without
impersonation. The administrator response retains `is_superuser` and its original
avatar, with no ordinary association processing. Historical role zero retains
the existing association label without association fields; out-of-range roles
retain the current generic server error. Disabled/deleted users and missing or
unavailable collaborator owners retain authentication/scope denials.

The DTO intentionally models this endpoint's distinct fields. It contains no
credentials, top-level login settings, instructor ID or membership-card settings.
Profile reads do not create default cards, audit rows or jobs. All nonadministrator
avatars are processed, including athlete avatars. Association logos are processed
regardless of whether regulation/demand are complete. Empty sections are marked
only for null/empty regulation or demand. Review URL preserves null versus empty
text and includes its enabled flag. Existing instance registration flags determine
the same three feature labels and order; they grant no access rights.

The composition root now supplies one shared image processor to login and profile,
retaining the process-wide concurrency limit of two and existing deadlines/logical
budgets. The complete image-format and supported-data sizing gaps documented by
`pkg/storage/loginimage` still apply to this endpoint. No new schema, environment
variable, provider or migration is introduced.

## Executed evidence

`TESTS/contracts/test_profile_info.py` runs against actual Django ASGI and the
compiled Go root with disposable PostgreSQL/Redis. Expected complete responses
come from authored fixture values, not captured application output. The adapter
reuses the existing explicit base-profile and association expectations and adds
this endpoint's fields/omissions.

The focused run passes 56 declared HTTP scenarios and 88 concurrent reads per
backend across all eight registration-flag combinations. It covers direct and
impersonated owners/collaborators/athletes, administrators, instructors, historical
roles, missing/deleted associations, authentication, foreign references, methods,
OPTIONS and content negotiation. Four additional reads per backend change stored
personal/review settings and section completeness while using the same identity
credentials. All managed model rows and session/cache bytes remain unchanged by
reads; fixture transitions and cleanup are checked separately. The server's
short-lived readiness keys are excluded explicitly from cache comparisons.

`profileinfointegration` uses actual repositories, identity resolution and the
native image processor. Direct athlete/administrator reads use two SELECTs;
owner/collaborator reads use six. Thirty-two mixed calls use 128 SELECTs with a
four-connection pool and no connections left in use. Adding 1/16/128/512 groups
to the owner leaves the six-SELECT count unchanged and excludes foreign groups.
The executed details statement is also inspected with 4,000 additional synthetic
tenants. EXPLAIN ANALYZE uses `bakney_user_pkey` and the existing association-owner
unique index, returning one row from each relation without filtered rows. Its JSON
plan is retained as `details-plan.json`. The existing full nested group payload
remains unpaginated; this is not a bounded total-payload or representative
performance claim. A foreign
person/owner details lookup fails, and a real blocked table read cancels, releases
its connection and permits subsequent recovery.

Unit tests exercise failed/mismatched readers, trusted-scope mismatches, image
failure, cancellation and role-specific dependencies. An administrator cancellation
test first exposed a partial result on error; all cancellation paths now return no
profile. The initial HTTP run passed Django and failed Go on the missing route.
Logs are under `quality-reports/go-migration/profile-info/`, including
`http-route-red.log`, `unit-red.log`, `cancel-red.log`, `modes-current.log` and
`plan-final.log` and `fresh-token-final.log`.
The migration evidence index records final aggregate/image checks.

Run `python TESTS/adapters/django/accounts.py --only profile-info` with the
production Python version and repository test dependencies, or run the full
accounts adapter. Fixtures use current-model run-syncdb. Full frontend workflows,
all image formats, historical schemas, versioned routing, lifecycle switching and
activation remain unverified; the ledger entry stays implementing.
