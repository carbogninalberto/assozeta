# Superuser association admin update — implementing with HTTP parity

`POST sport-associations/<uid>/admin-update` (`sport_association_admin_update`
in `BE/application/views/profile_views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/association_admin_update.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** the administrator impersonation exemption only matches a path ID
  of 36 hex or hyphen characters; other IDs are denied before the view (`403`).
  The collaborator registry excludes the path.
- **Authorization:** a non-superuser scope user gets a bare `401` (empty body).
- **Lookups:** `is_valid_uuid` raises DRF's `ValidationError` (`400 ["badly formed
  hexadecimal UUID string"]`); no active association or no billing subscription
  for its owner is a bare `404`; the subscription is the first by primary key.
- **Billing:** `request.data.get('billing_subscription')` (non-dict bodies raise,
  500; missing or `null` is a bare `400`; a non-dict value raises). Each of
  `auto_renewal`, `renewal_type`, `ends_on` and `billing_plan` is assigned
  raw and saved without validation. auditlog diffs the raw in-memory values on
  `pre_save` (`str()` of the value, `DateTimeField.to_python` for `ends_on`, the
  raw attname for the plan), so `1.0` is recorded although `1` is stored, an
  uppercase plan ID differs from the stored one, and the entry is committed
  before a save that rejects the value (500). An `ends_on` string that
  `to_python` rejects raises during the diff itself (500, no entry). The index
  falls back to the actor's association (`actor.sport_association`).
- **Notes:** `request.data.get('notes', current)` and a full-row association
  save inside `try`; any exception returns `400 {"msg": "Errore durante
  l'aggiornamento delle note."}` with the billing save already committed. The
  notes entry is indexed through the self resolver.
- **Response:** `200 {"msg": "Modifiche salvate."}`.

## Go implementation

One owned transaction with foreign keys checked immediately (Django's
autocommit saves check each statement). Each audit entry and each update runs
in its own savepoint: a rejected billing value keeps the billing entry and
returns 500; a notes failure keeps the billing changes and returns the 400. The
association update writes only `notes`, where Django rewrites the full row.
Python `int()`, `BooleanField.to_python` and `DateTimeField.to_python` ports are
unit-tested against CPython and the pinned Django.

## Evidence (TDD)

`TESTS/fixtures/association_admin_update_http.json` (generator alongside)
authors 19 cases on the association-list seed. The Django reference corrected
one expectation, confirmed in `application/impersonation.py`: a non-UUID-shaped
path is denied by identity resolution. RED: Django 19/19 and Go 404 on the first
case before any Go code. GREEN: native Django, native Go, Caddy `/api/v1` and
`/api/v2` pass 19/19; discarding the billing entry when its save fails fails the
run.
