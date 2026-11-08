# SMTP configuration routes — info, update and verify implementing

`GET communications/settings/smtp/info`, `PATCH communications/settings/smtp/update`
and `GET communications/settings/smtp/verify` (`configuration_smtp_info`,
`configuration_smtp_update`, `configuration_smtp_verify` in
`BE/communications/views.py`) are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/communications_smtp.go`).
Django remains the production execution owner.

## Shared behavior

- `@api_view` with a single method plus OPTIONS and DRF `IsAuthenticated` only.
  The paths are not in `permissions_registry.py`: FULL collaborators pass and act
  on the owner's scope, other collaborators get the unmapped-route 403 (also for
  OPTIONS). An owner session scoped to an athlete is denied by
  `enforce_scoped_request` (the views are not athlete views).
- Scope is `request.user.sport_association` (see [email logs](email-logs.md)):
  athletes 403 `User is not a sport association`, collaborators the connected
  owner's association, otherwise `SportAssociation.objects.get(user=...)` through
  `ActiveManager` (missing or soft-deleted: HTTP 500). OPTIONS never evaluates it.
- The configuration is `CommunicationConfiguration.objects.filter(sport_association=...).first()`,
  the association's row with the lowest key.
- `CommunicationConfiguration` is registered with auditlog excluding
  `email_smtp_password`, indexed through the direct resolver.

## Routes

- **info (GET):** without a row (and `is_sport_association()`, true for every
  role that reaches it) creates one with port 587 and encryption `TLS`
  (limit 1000, balance 0), audited as a create without the password and without
  None-valued text fields. Answers `CommunicationConfigurationSerializer`: host,
  port, user, sender name, encryption, daily limit and balance (never the
  password). The `{'msg': 'info not found.'}` 404 branch is reachable only for a
  role outside association/collaborator, where `is_sport_association()` raises
  first (500). Go locks the association row before creating so concurrent
  requests create one row; Django does not lock.
- **update (PATCH):** no row: 404 `{'msg': 'info not found.'}` (before
  validation). `CommunicationConfigurationPatchSerializer(partial=True)`: host,
  user, password and sender name are `CharField(255)` nullable and blank
  (trimmed, numbers become `str()`, booleans/lists/dicts and NUL characters
  invalid; a form `''` stays `''`), port is `PositiveIntegerField` (DRF
  `IntegerField`, 0..2147483647, `'465'` and `25.0` accepted, a form `''` is
  None), encryption is the `SSL`/`TLS`/`NONE` choice. Any error, a non-object or
  `null` body: 400 `{'msg': 'info not valid.'}`. Read-only and unknown keys are
  ignored. `save()` writes every column; auditlog records `[str(old), str(new)]`
  for changed fields except the password (an unchanged or password-only update
  has no entry). 200 `{'msg': 'info updated.'}`.
- **verify (GET):** no row: 404. Otherwise `verify_smtp()` with its test email.
  Go answers the paths that fail inside smtplib before any socket is opened:
  an empty or None host (no connect in `SMTP()`/`SMTP_SSL()`; the first command
  raises `please run connect() first`) and a falsy port with a host holding
  exactly one `:` whose suffix is not an int (`nonnumeric port`), both as
  200 `{'verified': false, 'msg': ...}`.

## Not ported (refused before any effect)

- **verify with a connectable configuration:** every other configuration opens
  an SMTP connection, logs in and sends a test email to the owner. Go refuses it
  with HTTP 500 (`domain.ErrSMTPVerifyNotPorted`, logged) without any network
  I/O. Hosts whose port suffix contains non-ASCII characters are refused too
  (Python `int()` accepts Unicode digits and whitespace). Covered by
  `TestSMTPVerifyWithoutConnection`; not part of the HTTP sequence because Django
  would open a connection.

Go stores the validated password value exactly as the model column holds it
(the instance administration encryption helpers do not apply to this model).

## Evidence (TDD)

`TESTS/fixtures/communications_smtp_http.json` (generator alongside) authors 73
cases: identity, dispatch and scope for the three routes (anonymous, admin
without session, restricted collaborator, owner scoped to an athlete, athlete,
owner without association, soft-deleted association, wrong method, OPTIONS),
update/verify before any row (404), the audited create on info and the
lowest-key selection (two foreign rows seeded in reverse key order), 18 invalid
update bodies, updates by the owner, a FULL collaborator and an impersonating
administrator (audit actor and impersonation data), coercions, form input,
password-only and unchanged saves without audit entries, and every
no-connection verify path. The runner checks the complete model state after
each case (configuration rows, auditlog entries and their index rows).

RED: Django 73/73 on the first run; Go 404 on the first case before any Go code.
GREEN: 73/73 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
Mutation control: ordering the first configuration by descending key fails Go.
