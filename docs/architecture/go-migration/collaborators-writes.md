# Collaborator writes (role and permission update) — implementing with HTTP parity

`PATCH collaborators/<uid>/update` (`collaborators_update` in `BE/application/views/collaborator_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/collaborators_writes.go`). Registry `other.users.collaborators.update`.
Django remains the execution owner. `collaborators/add` and `collaborators/<uid>/delete` are not ported (see below).

## Behaviour

- `@api_view(['PATCH'])` with `IsAuthenticated` and `IsAssociationUser`: `Allow: PATCH, OPTIONS`; OPTIONS names
  `Collaborators Update` with an empty description. Athletes get DRF's generic 403 before method dispatch; a restricted
  collaborator gets `Missing permission: other.users.collaborators.update`, also for a wrong method.
- In Django's order:
  1. `data = request.data` parses the body first: a malformed JSON body is DRF's 400 parse error even for a malformed uid;
  2. `is_valid_uuid(uid)`: 400 `["badly formed hexadecimal UUID string"]`;
  3. `User.objects.filter(connected_user=request.user, user_id=uid).first()`: live users only; `request.user` is the owner for
     collaborators and impersonated requests. No match is 400 `["Collaborator not found"]` (also for the owner's own id and for
     owners without an association, which need none here);
  4. `int(data.get('collaborator_role', User.COLLABORATOR))`: `User.COLLABORATOR` is 3, so a missing role is the custom role.
     A non-dict body (after the lookup) and a value `int()` rejects (`None`, lists, dicts, `'2.0'`, `''`, letters) are a 500
     before any write. Python's `int()` rules apply (whitespace, sign, underscores, float truncation, booleans). A role other
     than 1, 2 or 3 becomes 1 (FULL);
  5. `collaborator_permissions` is `data.get(...)` for the custom role and `None` otherwise; form bodies use the last value of a
     repeated key and store it as a JSON string;
  6. both fields are assigned and `save()`d: `{'msg': 'collaborator updated.'}`.
- auditlog logs the update on `pre_save` against the stored row: `str()` of the role, `json.dumps(sort_keys=True)` of the
  permissions (`null` for None); no entry when neither changed. The entry is committed before a save PostgreSQL rejects (for
  example `\u0000` in jsonb), which is a 500 with the entry kept and the row unchanged. The representation is
  `name: <first> <last>`. The index follows the User resolver (the target's own live association, `user.sport_association`),
  then the actor's live association (`actor.sport_association`); a collaborator actor without one gets no index row.
  Impersonated requests carry the session in `additional_data`.
- Requests are not atomic in Django; the Go port locks the target row, writes the audit entry and runs the update in a
  savepoint so a rejected value still commits the entry.

## Not ported

- `collaborators/add` (not routed in Go): every success path creates the invitation and dispatches the invitation e-mail
  through the `send_mail_async` Celery task; there is no success path without e-mail.
- `collaborators/<uid>/delete` stays `needs_review` and is not routed: deleting a connected user reaches the User cascade
  graph (89 potential model types per [collaborator-delete.md](collaborator-delete.md)), the accepted-invitation email link
  and the owner-first row-lock protocol, none of which this family's fixture covers.

## Evidence (TDD)

`TESTS/fixtures/collaborators_writes_http.json` (generator alongside) authors 45 cases on the shared identity seed plus six
seeded users (two collaborators of the owner, an association-role user of the owner with its own association, a foreign, a
deleted and an unconnected user). Each case checks the complete model state: only the target's `collaborator_role` and
`collaborator_permissions` change, with at most one exact User audit entry and its index row.

- RED (`native-r1`): Django 45/45; Go 404 on the first case before any Go code.
- GREEN (`versioned-r1`): 45/45 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): keeping the permissions for every role fails Go on
  `collaborators-writes-owner-accounting-drops-permissions` (`state.permissions`); CAUGHT.
