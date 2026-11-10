# impersonation-users (re-verification after main 6b20715d)

Routes: `association/impersonation/users` and `administration/impersonation/users` (`impersonation_users`,
`BE/application/views/admin_views.py`), plus the shared eligibility of `eligible_users`
(`BE/application/impersonation.py`) used when a session is started or resolved.

The routes were ported by impersonation-management (see `impersonation-management.md`, which owns their transport,
permission, pagination and audit evidence). Main 6b20715d changed two things, re-verified here with an authored
`family.py` sequence.

## Behaviour

- `eligible_users(actor)` for an association owner (the self-hosted instance owner included): the athletes with a live
  `Associate` row of the association (any group) or a non-deleted `Subscription` of the association
  (`Subscription._base_manager`, any group), and the owner's own collaborators, the owner excluded. Impersonation is
  limited to athletes whose memberships are all within the actor's associations: an athlete with a live `Associate`
  row (with an association) or a non-deleted `Subscription` of an association the actor does not own is not eligible.
  The administrator branch is unchanged.
- Search (`query[generalSearch]`, else `q`; stripped, first 150 characters): `query.split()[:10]` (Python whitespace,
  U+001C..U+001F included); every word must match (`icontains`, `%`/`_` literal) one of username, email, first name,
  last name, the user's association denomination or the connected owner's association denomination, on the same joined
  row (`pk__in` of one filtered `User` query).

Go: `managementEligible` and `managementSearchWords` (`BE_GO/internal/repository/impersonation_management.go`, list and
start/session target) and `IdentityRelations.EligibleTarget` (`BE_GO/internal/repository/identity.go`, the identity
middleware's session recheck) apply the same predicate (`impersonationLinksOwned`).

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

## Re-verification: memberships within the actor's associations

Django `BE/application/impersonation.py` (tests: `BE/application/tests/test_association_impersonation.py`) now applies
the linked-members rule to the self-hosted instance owner too and requires every live link of the athlete to belong to
an association the actor owns. Fixture `TESTS/fixtures/impersonation_users_http.json` (35 cases) adds athletes linked to
both associations (member of one, subscriber of the other; subscriber of both) and one whose foreign links are deleted.
Identity (`TESTS/adapters/django/identity.py`, 53 cases, 10 transitions) adds the same shapes and a transition that
restores a deleted foreign membership. Fixtures where another association's rows named the shared athlete now register
them under that association's owner (`scoped_resources.py`).

- RED (`sec-impersonation-red`): Django 35/35; Go fails `impersonation-users-instance-owner-association-athletes`.
- GREEN (`sec-impersonation-green`): 35/35 on native Django, native Go, Caddy `/api/v1` and `/api/v2`; identity plus
  impersonation-management 264 cases on the same four transports (`sec-impersonation-identity-green`).
- Mutations: dropping the membership clause from `managementEligible` (CAUGHT on the list) and from
  `IdentityRelations.EligibleTarget` (CAUGHT by `owner-member-subscribed-elsewhere-not-eligible`,
  `owner-subscriber-member-elsewhere-not-eligible` and `membership-elsewhere-restored`).

Receipts: `quality-reports/go-migration/sec-impersonation-*`.

### Route-family fixtures

Fixtures that linked the shared athlete to the foreign association now either register those rows under the foreign
owner (carnet-list and its derived carnet families, course-list, subscription-tags, statistic-reads) or move the cases
about an athlete of both associations to a family athlete (`extra_users`, seeded by `family_harness.seed_extra_users`
or `carnet_list_http.add_extra_tokens`): carnet-subscription-topup, carnet-assign, carnet-subscription-list,
inconsistencies, subscription-writes, subscription-reads and profile-misc. The shared athlete's owner-scoped cases are
unchanged. All 20 families pass on four transports (`sec-impersonation-final-*`).
