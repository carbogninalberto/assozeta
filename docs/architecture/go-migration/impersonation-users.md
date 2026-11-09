# impersonation-users (re-verification after main 6b20715d)

Routes: `association/impersonation/users` and `administration/impersonation/users` (`impersonation_users`,
`BE/application/views/admin_views.py`), plus the shared eligibility of `eligible_users`
(`BE/application/impersonation.py`) used when a session is started or resolved.

The routes were ported by impersonation-management (see `impersonation-management.md`, which owns their transport,
permission, pagination and audit evidence). Main 6b20715d changed two things, re-verified here with an authored
`family.py` sequence.

## Behaviour

- `eligible_users(actor)` for an association owner:
  - when the actor owns the self-hosted instance (`is_instance_owner`: the first `InstanceConfiguration` by pk is
    `self_hosted` and its `primary_association` belongs to the actor), every active, non-deleted, non-superuser athlete
    plus the owner's own collaborators, the owner excluded;
  - otherwise the athletes with a live `Associate` row of the association (any group), the athletes with a non-deleted
    `Subscription` of the association (`Subscription._base_manager`, any group), and the owner's own collaborators.
  The administrator branch is unchanged.
- Search (`query[generalSearch]`, else `q`; stripped, first 150 characters): `query.split()[:10]` (Python whitespace,
  U+001C..U+001F included); every word must match (`icontains`, `%`/`_` literal) one of username, email, first name,
  last name, the user's association denomination or the connected owner's association denomination, on the same joined
  row (`pk__in` of one filtered `User` query).

Go: `managementEligible` and `managementSearchWords` (`BE_GO/internal/repository/impersonation_management.go`, list and
start/session target) and `IdentityRelations.EligibleTarget` (`BE_GO/internal/repository/identity.go`, the identity
middleware's session recheck) apply the same predicate (`managementInstanceOwner`).

## Evidence

`TESTS/fixtures/impersonation_users_http.json` (28 cases, `TESTS/fixtures/generate_impersonation_users_http.py`): a
seeded self-hosted `InstanceConfiguration` whose primary association is the owner's (owner = instance owner,
foreign_owner = not), athletes linked by member row, by subscription, by a deleted subscription, by another
association's subscription and not at all, an inactive and a deleted athlete, and a collaborator of the foreign owner.
Read family: every case leaves the complete state unchanged.

- RED (`payments-impersonation-reverify-impersonation-users-red`): Django 28/28; Go fails
  `impersonation-users-instance-owner-every-athlete` (users length).
- GREEN (`payments-impersonation-reverify-impersonation-users-green`, after merging codex/go-backend-parity 54165962):
  28/28 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`...-impersonation-users-mutation`): keeping eleven words instead of ten fails Go on
  `impersonation-users-search-eleventh-word-ignored` (CAUGHT).
- Identity (`TESTS/adapters/django/identity.py`, run by `accounts.py`): the instance owner resolves a session on an
  unlinked athlete; another owner resolves a subscriber and not an unlinked athlete or a deleted subscription; member
  row, subscription and `self_hosted` transitions revoke the session. Go replays the cases and transitions in
  `internal/service/identity_integration_test.go`. RED (`...-identity-red`, before the 50th case was added): Django 49 cases and 10 transitions pass, Go
  fails `instance-owner-unlinked-athlete` and `owner-impersonates-subscriber`. GREEN
  (`...-identity-impersonation-management-green`): the identity replay plus impersonation-management's 264-case
  workloads on native Django, native Go, Caddy `/api/v1` and `/api/v2` (`TESTS_IMPERSONATION_MANAGEMENT_GO=1
  TESTS_IMPERSONATION_MANAGEMENT_REPOSITORY=1 TESTS_IMPERSONATION_MANAGEMENT_VERSIONED=1 python
  TESTS/adapters/django/accounts.py --only impersonation-management`).

Receipts: `quality-reports/go-migration/payments-impersonation-reverify-*`.
