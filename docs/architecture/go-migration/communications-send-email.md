# communications/send/email — implementing with HTTP, SMTP and Celery parity

`POST communications/send/email` (`configuration_send_email`, `BE/communications/views.py`) is implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/communications_send_email.go`, composed in
`BE_GO/cmd/server/communications_send_email.go`). Django remains the execution owner.

## Behaviour

- `@api_view(['POST'])` with `IsAuthenticated` only. The path is not in `permissions_registry.py`: FULL collaborators pass
  (acting as the owner), other collaborators get the unmapped-route 403. OPTIONS names `Configuration Send Email`.
- The view first logs `request.user.sport_association` (see [communications-smtp](communications-smtp.md)): athletes 403
  `User is not a sport association`, a missing or soft-deleted association 500.
- `'message_id' in data and data['message_id']`: a non-dict body is a 500. A truthy `message_id` goes through
  `UUIDField.to_python` (strings in the forms `uuid.UUID` accepts, ints and booleans as `UUID(int=)`; anything else is a
  500) and `Message.objects.filter(message_id=..., sport_association=...).first()`. A miss is 404
  `{'msg': 'message not found.'}`. A hit replaces `message` and `subject` in `request.data`; a form or multipart body is an
  immutable QueryDict (500).
- `EmailSerializer.is_valid`: falsy `email`, `subject`, `message` (Python truthiness, in that order) raise
  `ValidationError('<Field> is required')`, rendered as a list. `email.split(',')` (a non-string is a 500), then
  `check_email` on every piece raises `["Email is not valid."]` (Django's `validate_email`; pieces are not stripped). Then
  DRF: `subject` CharField(100), `message` CharField(10000) (trimmed, numbers become `str()`, booleans/lists/dicts invalid,
  blank, NUL). Form values are the last of a repeated key.
- Without `message_id` a `Message(type='EMAIL')` is created (auditlog create, indexed `direct`). One `MessageTransaction`
  per comma-separated recipient (not audited). Every create autocommits: a recipient over 255 characters is a 500 after the
  message (and earlier transactions) exist.
- `CommunicationConfiguration.objects.filter(sport_association=...).first()`. None is a 500 after the writes. Then
  `send_email(subject, body, recipient_list)`:
  - balance at or over the limit: `200 {'msg': 'Daily email limit reached', 'result': false}`;
  - without host, port, user and password, or when `verify_smtp(send_email=False)` fails (connect, STARTTLS for `TLS`,
    login; that connection is closed without QUIT): `send_mail_async.apply_async(kwargs=...)` (`core.tasks.send_mail_async`:
    subject, message, `DEFAULT_FROM_EMAIL` with `WHITELABEL_NAME` replaced by the denomination, recipient list,
    `fail_silently=False`, the association UUID, `reply_to=None`, `skip_association_check=True`);
  - otherwise the `MIMEMultipart` with a base64 `text/plain` part is sent on a second connection (`smtpsend`), then
    `daily_email_balance += 1` and `save()` of the loaded row (audited update of the balance, indexed `direct`, actor and
    impersonation data of the request).
  - An exception inside `send_email` (all recipients refused, a broker failure) returns `(False, exception)`, which DRF cannot
    render: the 500 page after the writes.
- Success is `200 {'msg': 'Ok', 'result': true}`.

## Not ported (refused with the 500 page before any write)

- An SMTP delivery whose message `smtpsend` still cannot render (restb2): text that is not valid Unicode, a header over
  200,000 bytes, a recipient or sender text outside what `smtpsend/python_addr.go` reads like `email.utils.parseaddr`
  (covered by `TestSend*` in `smtpsend/python_full_test.go`, which replays Python's own answers from
  `testdata/python_full.json`, recorded by `TESTS/adapters/go/smtpsend_full_golden.py`). The check runs whenever the
  configuration would take the SMTP path.
- Ported (restb2): subjects with line breaks (continuation lines are folded as Python writes them; a break not followed by
  a space or tab is the 500 page after the verify connection, as `as_string()` raises inside `send_email`), quoted local
  parts, address literals, and non-ASCII recipients (the commands before the failing one are sent, then the 500).
- Not compared: the EHLO name follows `socket.getfqdn()` (normalized in the evidence, as in [tasks-db-c](tasks-db-c.md));
  the MIME boundary is random in both.

## Evidence (TDD)

`TESTS/fixtures/communications_send_email_http.json` (61 cases, `generate_communications_send_email_http.py`). The adapter
owns four fake SMTP servers (STARTTLS, implicit TLS, plain with AUTH, plain without AUTH/STARTTLS) and a closed port, and
runs with the run's private Redis as Celery broker. A case may first replace the own configuration (audit disabled; ports
as `$port:<server>`). Each case checks the response, the complete model state (messages, transactions, configurations,
auditlog entries and index rows), the Celery messages (exact envelope and body) and the SMTP transcripts per connection
(commands, AUTH, SIZE equal to the message length, the full MIME text with the boundary normalized).

- RED (`native-r4`): Django 55/55; Go 404 on the first case.
- GREEN (`versioned-r1`, after merging the base): 55/55 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): the daily limit compared with `>` fails Go on `communications-send-email-limit-reached`.
- Re-run green: communications-smtp (`native-r1`, 73/73; embedded repository transaction).
