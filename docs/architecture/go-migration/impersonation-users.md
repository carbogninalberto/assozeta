# impersonation-users (re-verification after main 6b20715d)

Routes: `association/impersonation/users` and `administration/impersonation/users` (`impersonation_users`,
`BE/application/views/admin_views.py`), plus the shared eligibility of `eligible_users`
(`BE/application/impersonation.py`) used when a session is started or resolved.

The routes were ported by impersonation-management (see `impersonation-management.md`, which owns their transport,
permission, pagination and audit evidence). Main 6b20715d changed two things, re-verified here with an authored
`family.py` sequence.

## Behaviour

- `eligible_users(actor)` for an association owner:
  - the instance owner (`is_instance_owner`: the first `InstanceConfiguration` by pk is `self_hosted` and its
    `primary_association` belongs to the actor) sees every active, non-deleted, non-superuser athlete, as the
    administrator does, plus the owner's own collaborators, the owner excluded. An installation serves one association;
  - the owner of another association sees the athletes with a live `Associate` row of the association (any group) or
    a non-deleted `Subscription` of the association (`Subscription._base_manager`, any group), and the owner's own
    collaborators, the owner excluded. That owner selects athletes whose memberships are all within the actor's
    associations: an athlete with a live `Associate` row (with an association) or a non-deleted `Subscription` of an
    association the actor does not own is not eligible.
  The administrator branch is unchanged. The list, the session start and the session recheck follow the same rule.
- Search (`query[generalSearch]`, else `q`; stripped, first 150 characters): `query.split()[:10]` (Python whitespace,
  U+001C..U+001F included); every word must match (`icontains`, `%`/`_` literal) one of username, email, first name,
  last name, the user's association denomination or the connected owner's association denomination, on the same joined
  row (`pk__in` of one filtered `User` query).

Go: `managementEligible` and `managementSearchWords` (`BE_GO/internal/repository/impersonation_management.go`, list and
start/session target) and `IdentityRelations.EligibleTarget` (`BE_GO/internal/repository/identity.go`, the identity
middleware's session recheck) apply the same predicate (`impersonationInstanceOwner`, `impersonationLinksOwned`).

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

## Re-verification: instance owner sees every athlete, as the admin does

Django `BE/application/impersonation.py` (tests: `BE/application/tests/test_association_impersonation.py`) applies
`is_instance_owner(actor)` first: the instance owner selects every athlete plus its own collaborators. The owner of
another association keeps the linked-members rule of the previous section. Go: `impersonationInstanceOwner` in
`managementEligible` (list and session start) and in `IdentityRelations.EligibleTarget` (session recheck).

- Fixture `TESTS/fixtures/impersonation_users_http.json` (38 cases): the instance owner lists the unlinked athlete, the
  athletes of the other association, the athletes linked to both and no inactive or deleted athlete; the other owner's
  cases are unchanged.
- Identity (`TESTS/adapters/django/identity.py`, 55 cases, 12 transitions): the instance owner resolves sessions on an
  athlete of the other association, an unlinked athlete, its own subscriber and an athlete linked to both; the other
  owner resolves a subscriber whose link elsewhere is deleted and not one who is a member elsewhere. Transitions:
  restoring the deleted membership elsewhere, `self_hosted` false and a different primary association each end the
  session. Go replays them in `internal/service/identity_integration_test.go`.
- Impersonation-management (`TESTS/adapters/django/impersonation_management.py`, 280 cases, 68 audits): adds the
  instance owner's list, session start, read and end on an unlinked athlete and on a member of another association,
  and its refused targets (owners, other collaborators, administrator, inactive and deleted accounts, itself).
- RED (`impersonation-users-http-ownerimp-red`): Django 38/38; Go fails
  `impersonation-users-instance-owner-every-athlete`. Identity RED (`ownerimp-identity-red`): Django 55 cases and 12
  transitions pass; the Go replay fails the three instance-owner cases.
- GREEN (`impersonation-users-http-ownerimp-green`): 38/38 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
  Identity replay plus impersonation-management 280 cases on the same four transports (`ownerimp-identity-green`).
- Mutations: the linked-members rule applied to the instance owner in `managementEligible` (CAUGHT on
  `impersonation-users-instance-owner-every-athlete`, `impersonation-users-http-ownerimp-mutation`) and in
  `EligibleTarget` (CAUGHT on the three instance-owner identity cases, `ownerimp-identity-mutation`); the instance-owner
  rule applied to every owner in `EligibleTarget` (CAUGHT on the other owner's cases and five transitions,
  `ownerimp-identity-mutation-open`).
- Route families re-run green on native Django and Go (`*-http-ownerimp-rerun`): statistic-reads, profile-misc and
  association-transfer (their fixtures seed an instance configuration), subscription-reads, subscription-writes,
  inconsistencies and carnet-assign. The other route-family fixtures seed no instance configuration, so their owners
  follow the linked-members rule as before.
- Browser (`TESTS/browser/impersonation.mjs`, `TESTS/adapters/django/impersonation_browser.py`): the fixture owner is the
  instance owner, so its picker lists the other association's athlete again (check `foreign-owner-excluded`).

Receipts: `quality-reports/go-migration/*ownerimp-*`.
