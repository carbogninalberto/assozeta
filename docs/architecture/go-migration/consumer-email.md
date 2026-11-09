# Celery consumer — e-mail tasks

Follows [celery-consumer](celery-consumer.md), which made `cmd/celery-consumer` run `core.tasks.send_mail_async` for the
system-backend path. This slice adds the three `application.tasks` e-mail tasks and the `send_mail_async` paths that were
handed back to Django. Routing stays **off by default**: a name runs in Go only when every producer lists it in
`GO_TASK_QUEUE_TASKS` and the consumer runs (see the deployment steps in celery-consumer.md).

## Behaviour (Django, reproduced in Go)

### send_email_text(message, subject, recipient_list, sport_association_id=None, reply_to=None)

Publishes `send_mail_async.apply_async(kwargs={subject, message, from_email=DEFAULT_TEAM_EMAIL, recipient_list,
fail_silently=False, sport_association_id, reply_to})` and returns None; every exception is logged and swallowed.

### send_email_template(template, subject, recipient_list, data, sport_association_id=None, reply_to=None)

`render_to_string(template, data)` (no language activated in the worker: `LANGUAGE_CODE`; `instance_email_brand` reads
`InstanceConfiguration.get_config()`), then, when `sport_association_id is not None`,
`CommunicationConfiguration.objects.filter(sport_association_id=...).first()` (by primary key, no join). With a
configuration: `configuration.send_email(subject, body=None, html_body=message, recipient_list, reply_to)` inside the task;
otherwise `send_mail_async` with `DEFAULT_TEAM_EMAIL` and `html_message=message`. Exceptions are swallowed.

### send_email_html(html_message, subject, recipient_list, sport_association_id=None, reply_to=None)

With a configuration of the association, `configuration.send_email(subject, body=None, html_body=html_message, ...)` runs
on the configuration loaded by the task from a `threading.Timer` 1-30 s (`random.randint`) after the task returned
(SUCCESS is already stored). Otherwise `send_mail_async` with `DEFAULT_FROM_EMAIL`, `message=html_message` and
`html_message`. Exceptions are swallowed.

### send_mail_async, the paths added here

- **Association with a configuration** (`skip_association_check` absent or falsy): `config.send_email(subject,
  body=message, recipient_list, html_body=kwargs.get('html_message'), reply_to=kwargs.get('reply_to'))`, whose result is
  ignored, then `EmailLog(..., 'Sent', association)`.
- **Instance email source** (`effective_email`: a self-hosted `InstanceConfiguration` with truthy `email_settings`): the
  SMTP backend takes `host`, `port`, `username`, the password decrypted with `SECRET_KEY` (Fernet keyed by
  `sha256('assozeta:instance-email:v1:' + secret)`), and `security`; the message's From becomes
  `formataddr((sender_name, from_email))`. A password no key opens raises `ImproperlyConfigured('La credenziale email
  salvata deve essere reinserita.')` and the task retries (+180 s); a `security` other than tls/ssl/none raises the
  TLS/SSL `ImproperlyConfigured`.
- **fail_silently truthy**: Django's SMTP backend swallows any `OSError` while opening (connection, STARTTLS, login: the
  connection is left without QUIT) and any `SMTPException` from `sendmail` (then QUIT); the task logs `Sent`.

### CommunicationConfiguration.send_email (shared by the three callers)

1. `daily_email_balance >= daily_email_limit`: nothing.
2. Without host, port, user and password, or when `verify_smtp(send_email=False)` fails (connect, STARTTLS for `TLS`,
   login; the connection is dropped without QUIT): `send_mail_async(subject, message=body, from_email=DEFAULT_FROM_EMAIL
   with WHITELABEL_NAME replaced by the association's denomination, recipient_list, fail_silently=False,
   [html_message when html_body is truthy], sport_association_id, reply_to, skip_association_check=True)`.
3. Otherwise `MIMEMultipart('alternative')` with the HTML part, or `MIMEMultipart()` with `MIMEText(body, 'plain')` (a
   None body raises before any delivery), sent on a second connection (login, `sendmail(user, recipient_list, ...)`,
   QUIT); then `daily_email_balance += 1` and `save()` of the loaded instance (every column; auditlog update entry for
   the changed fields, indexed `direct`).
4. Any exception inside returns `(False, e)`: nothing is raised to the task.

SMTP timeouts are 10 s per socket operation (`SMTP_TIMEOUT_SECONDS`); the system backend uses 5 s.

### Messages published from a task

`apply_async` inside a running task publishes with `root_id = request.root_id or request.id` and `parent_id = request.id`
(Go: `celery.Task.RootID/ParentID`, encoded in place of the defaults). From the `send_email_html` Timer thread there is
no current task: `root_id` is the message's own id and `parent_id` None. `add_to_parent` appends the result to
`task.request`, but Celery 5.6's tracer stores SUCCESS from the request copy it pushed, so the stored `meta` keeps
`{"children": []}`; Go records no children for these publishes. (The recipe in celery-consumer.md step 3 assumed the
opposite; the Django oracle shows `[]`.) The publisher follows `GO_TASK_QUEUE_TASKS` like Django's producer, so a listed
`send_mail_async` goes back to `assozeta-go`.

## Go

- `internal/service/consumer_email.go`: the three tasks (`SendEmailTextTask`, `SendEmailTemplateTask`,
  `SendEmailHTMLTask`), the `send_email` port (`planConfigSend`, `configSend`) and the child publish.
  `internal/service/send_mail_async.go`: configuration path, `planSource` (environment or instance source, decryption),
  `fail_silently`. `WithEmailTasks` wires the publisher and settings; `Wait` holds shutdown for pending Timer sends.
- `internal/repository/celery_consumer.go`: configuration read and save (reuses the audited save of tasks-db-c in its own
  transaction, without the Go-job fence), denomination, instance brand and email rows.
- `cmd/celery-consumer`: registers the three names next to `core.tasks.send_mail_async` and waits for Timer sends after
  the drain.
- `internal/celery`: `Task.RootID/ParentID` (empty keeps the previous encoding; `TestEncodeChildIDs`).
- `internal/emailrender`: embeds `email/account/email_new_subscription.html` (the template the subscription flows pass
  to `send_email_template`) with three Django golden renders (46 renders total).

### Handed off before any effect (to the `celery` queue, unchanged)

- A call the Python signature rejects (missing or unknown arguments, duplicates).
- Forwarded values outside what the producer re-encodes exactly: floats, non-canonical integers, kombu types other than
  `uuid`, dicts with repeated keys.
- `send_email_template`: templates outside `internal/emailrender`'s embedded set, `data` that is not a dict or holds lists,
  floats or UUIDs, brand values the renderer refuses.
- A `sport_association_id` the UUID field rejects (Django swallows the `ValidationError`; no effect either way).
- Configuration sends whose SMTP message or envelope is outside the smtpsend subset, a non-list `recipient_list`, or a
  `reply_to` that is neither None, a string nor a list of strings.
- `send_mail_async`: instance `email_settings` without the six keys as strings and an integer port 1-65535, a sender
  name that `formataddr` would quote or encode, a non-UTF-8 decrypted password, and every handoff of the first slice
  except `fail_silently` and the configuration path.

### Not reproduced (documented differences)

- Production runs the Django worker with the prefork pool; a pool child that exits (shutdown, recycling) drops pending
  Timer threads. Go waits for every pending `send_email_html` send before the consumer exits.
- Infrastructure failures inside a swallowing task (database read in the plan) end SUCCESS with no effect; Django would
  have done the effects up to the failing statement. A failure of the EmailLog insert after a configuration-path child
  publish retries in both, but Celery's RETRY row then lists the child (it stores `task.request`), Go's does not.
- `send_email` failures are logged, not printed.

## Evidence

`TESTS/adapters/go/celery_consumer_email_process.py` (extends `celery_consumer_process.py`; receipts in
`quality-reports/go-migration/celery-consumer-email-process*`). Each scenario publishes with Django's producer routed by its
own `GO_TASK_QUEUE_TASKS` names, runs a real Django worker, re-seeds, runs the race-built consumer, and compares SMTP
transcripts, the complete managed-model state (TaskResult, EmailLog, CommunicationConfiguration, auditlog and index
rows), and every broker key, with child messages compared field by field (id, origin, reply_to, stamp normalized; root
and parent ids, kwargs and kwargsrepr exact). 25 scenarios:

- `send_email_text`: child left on `celery`; child routed to Go and sent with association and reply-to; child SMTP
  authentication failure (child RETRY, +180 s held and restored); missing argument handed off (TypeError FAILURE on Django).
- `send_email_template`: no configuration (child with `DEFAULT_TEAM_EMAIL`); SMTP configuration (delivery, balance 5 to
  6, audit entry); daily limit; failing SMTP login (fallback child under the denomination, executed by Go); configuration
  without SMTP (fallback child on `celery`, string reply-to); StaleDataTask after a completed restore; a template outside
  the embedded set handed off (Django renders, its child runs in Go).
- `send_email_html`: no association; SMTP configuration through the Timer with Go stopped right after the task result
  (the consumer must finish the send before exiting); configuration without SMTP (Timer-published child, no parent);
  pending-restore deferral; malformed association handed off.
- `send_mail_async`: configuration SMTP html and plain text, daily limit (EmailLog `Sent`, nothing sent), login failure
  fallback child, instance email source, undecryptable instance password (RETRY ImproperlyConfigured +180 s),
  `fail_silently` with a login failure and with a refused recipient, a non-string subject handed off.

- Django oracle alone: 25/25 (`celery-consumer-email-process-django`).
- RED: consumer without the three registrations: `Timed out: Go consumer: send_email_text publishes send_mail_async with
  the team address` (`celery-consumer-email-process-red`).
- GREEN: 25/25 (`celery-consumer-email-process`).
- Mutation (CAUGHT): `consumerEmailPublish` without the parent id (`celery-consumer-email-process-mutation`).
- Re-run after the shared changes: `celery_consumer_process.py` 15/15 (the configuration and `fail_silently` scenarios now
  run in Go; an unknown keyword is the handoff case; receipt `celery-consumer-process-consumer-email`),
  `email_tasks_worker_process.py` 7/7, HTTP families natively green: `email-render-routes` (82), `celery-routes-a` (76),
  `communications-send-email` (55); `go test` for celery (incl. `TestEncodeChildIDs`), emailrender (46 renders), smtpsend
  and `-race` for the service plans (`SendMailAsync|ConsumerEmail|EmailTask`).
