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
  The paths that fail inside smtplib before any socket is opened are an empty
  or None host (no connect in `SMTP()`/`SMTP_SSL()`; the first command raises
  `please run connect() first`) and a falsy port with a host holding exactly
  one `:` whose suffix is not an int (`nonnumeric port`), both
  200 `{'verified': false, 'msg': ...}`. Every other configuration connects
  (`SMTP_SSL`, or `SMTP` and `starttls()` for `TLS`, 10 s timeout), logs in,
  sends the test message as bytes from `"<sender name or WHITELABEL_NAME> <user>"`
  to the association user's email and quits: 200 `{'verified': true, 'msg': 'Ok'}`.
  Any exception is 200 `{'verified': false, 'msg': str(exception)}`. Go
  (`smtpsend.VerifySend`, `smtpsend.PythonMessage`) reproduces the dialogue and
  the texts of the smtplib reply exceptions (`(535, b'denied')`, the refused
  sender with the from text, the refused-recipients dict), of the fixed-text
  errors (no AUTH, no STARTTLS, no suitable method, a closed connection, a
  plain-socket read timeout) and of a refused connection. The row is read and
  the transaction ended before the connection is opened.

## Not ported (refused before any effect)

Go answers HTTP 500 (`domain.ErrSMTPVerifyNotPorted`, logged) for these verify inputs:

- Before connecting: a falsy or out-of-range port with a plain host (smtplib's
  default port), a missing user or password, a user or owner email outside
  `smtpsend.CheckEnvelope`, a sender name with characters other than letters,
  digits, spaces and `. - _ &` (what `email.utils.parseaddr` reads differently),
  and hosts whose port suffix contains non-ASCII characters.
- After a failed connection whose Python text depends on the platform
  libraries: TLS handshake errors (for example `SSL` chosen on a STARTTLS
  port), name resolution failures, connect timeouts, resets and read timeouts
  inside TLS. Nothing is written in either case.

Go stores the validated password value exactly as the model column holds it
(the instance administration encryption helpers do not apply to this model).

## Evidence (TDD)

`TESTS/fixtures/communications_smtp_http.json` (generator alongside) authors 83
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

API v2 (v2-misc-2): verify against a connectable host. The adapter's
`service_context` owns seven fake SMTP servers (STARTTLS, implicit TLS, plain,
without AUTH, refusing the recipient, refusing the sender, never greeting) and
a closed port; a case's setup writes the connection columns of the own row.
Ten cases: three successful deliveries (STARTTLS with an accented sender name,
implicit TLS with the default name, plain with a blank name under an
administrator session), a wrong password, no AUTH, no STARTTLS, a refused
connection, a refused recipient, a refused sender and a server that never
greets. Each case compares the response, the own row, the absence of audit
entries and the client bytes each server recorded (none on the others).
RED `v2misc2-red-r1`: Django 83/83, Go 500. GREEN `v2misc2-green-r1`: 83/83 on
the four transports. Mutation `v2misc2-mutation-r1` (test email
addressed to the SMTP user) fails `communications-smtp-verify-starttls-sends-test-email`
on Go (`smtp.transcripts`). Re-run: communications-send-email 55/55 and
instance-outbound 106/106 (`v2misc2-rerun-r1`).
