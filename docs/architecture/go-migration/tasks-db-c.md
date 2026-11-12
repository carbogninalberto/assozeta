# Periodic tasks, third slice: attendance auto-marking and e-mail tasks

This slice follows [tasks-db-a](tasks-db-a.md) and [tasks-db-b](tasks-db-b.md):
each Django task runs first on a seed, the native Go job must then leave the
identical database state and send the same messages, and the inert
persisted-Beat scheduler publishes the task only through
`domain.PeriodicJobKind`. Django remains the default owner; nothing here seeds
schedules, activates Go ownership or installs a service.

## What changed

- Worker registrations (version 1, `BE_GO/cmd/worker/main.go`):
  `auto_mark_attendance` (`internal/{domain,repository,service}/auto_mark_attendance.go`),
  `send_user_partial_registration_email`, `send_expiring_certificate_email`,
  `send_expired_certificate_email` and `send_reminders`
  (`internal/{domain,repository,service}/email_tasks.go`). The worker waits for
  the delayed reminder sends (below) before it exits.
- `domain.PeriodicJobKind` maps the five tasks. The evaluator accepts a `*/N`
  minute step (1-59) with `*` hour and wildcard day/weekday/month in UTC,
  expanded like Celery's crontab parser (`*/15` is 0, 15, 30, 45) and timed
  with the general `remaining_delta` rule over the hour and minute sets.
- `internal/emailrender` embeds four more templates
  (`email_user_partial_registration.html`, the expiring and expired
  certificate notices, `reminders/event.html`) with Django golden renders; see
  [email-render-routes](email-render-routes.md).
- New `internal/smtpsend`: the SMTP client and MIME builder of
  `CommunicationConfiguration.verify_smtp(send_email=False)` and
  `CommunicationConfiguration.send_email` (Python 3.13 `smtplib` and
  `email.mime` compat32, not Django's `EmailMessage`): command spelling,
  EHLO/HELO fallback, STARTTLS and implicit TLS with the stdlib context's
  settings, AUTH mechanism order (CRAM-MD5, PLAIN, LOGIN) and initial
  responses, ASCII command encoding, SIZE, refusal handling, dot quoting,
  QUIT; headers never folded (`as_string()` passes `maxheaderlen=0`), non-ASCII
  values as one UTF-8 encoded word (base64 only when shorter), base64 body at
  76 columns, Python's random boundary. Refused (`ErrUnsupported`) before any
  connection: header values with characters `str.splitlines()` splits on or
  over 4096 characters, and envelope addresses outside a dot-atom subset
  (with or without `@`).
- `modelAuditEntry.ObjectID` sets auditlog's `object_id` for integer primary
  keys (UserPartial, AssociateTutorRelation); every existing caller keeps NULL.

## Schedule declarations

| Declaration | Definition | Go scheduler |
|---|---|---|
| `auto-mark-attendance` | every 15 minutes (`*/15`) | publishes `auto_mark_attendance` v1 |
| `send-reminders` | every minute | publishes `send_reminders` v1 |
| `send-expiring-certificate-email` | 06:00 UTC daily | publishes `send_expiring_certificate_email` v1 |
| `send-expired-certificate-email` | 07:00 UTC daily | publishes `send_expired_certificate_email` v1 |
| `send-user-partial-registration-email` | 08:00 UTC daily | publishes `send_user_partial_registration_email` v1 |

The Celery `rate_limit` annotations (`send_reminders` 60/m,
`auto_mark_attendance` 4/m) are worker-side throttles of Django's consumer and
have no Go counterpart; Go runs one job per published occurrence.

## Behaviour (Django, reproduced in Go)

### auto_mark_attendance

- `User.objects` (not deleted) with role 1 and `auto_mark_attendance`, unordered;
  each owner's first live association by primary key; its active, not deleted
  courses; the course's first registry by primary key; the registry's days not
  yet marked with `date` between now minus one day and now (both inclusive,
  UTC), then only days strictly within the last 24 hours.
- The course's live course subscriptions are read once, at the first day in
  the window. For each: the expected-absence test (`str(id) ==
  item['course_subscription_id']`, short-circuiting), then the carnet query
  (linked to the course subscription or to none, same subscription, enabled,
  newest first). The carnet with the fewest positive `lessons_left` wins (the
  first on ties); an unpaid payment skips the attendee; otherwise
  `lessons_left -= 1` (bool becomes int, int exact, float IEEE),
  `lessons_registry.append({date: str(day.date), course: {id, title}, title})`
  and `carnet.save()`. Carnets that exist but have no positive lessons skip the
  attendee; no carnet at all marks the attendee.
- `day.attendees = [...]`, `expected_absences` None becomes `[]`,
  `auto_marked=True`, `day.save()`. Audit: update entries with only the changed
  fields (JSON as `json.dumps(sort_keys=True)`), indexed through
  `subscription.sport_association` for carnets (none without subscription) and
  `attendance_registry.course.sport_association` for days.
- Stored data that makes Python raise (meta not a dict, missing
  `lessons_left`, a non-numeric value, missing or non-list `lessons_registry`,
  a non-list truthy `expected_absences` or an item without the key) ends the
  task with `None`; Go completes the job with NULL and keeps every save
  committed before it.
- Go: reads go through one fenced READ COMMITTED transaction (each statement
  sees the committed saves); each carnet save and each day save is its own
  fenced transaction, as each `save()` autocommits. Queries keep Django's
  filters and no ORDER BY where Django has none, so PostgreSQL returns the same
  order. Go updates only the changed columns where `save()` writes all of them.

### send_user_partial_registration_email

- `UserPartial` rows created since yesterday's UTC midnight, unordered. For
  each: the rendered template (`email`, `app_host`, `settings`) is published as
  `send_mail_async(subject, message, from_email=DEFAULT_TEAM_EMAIL,
  recipient_list=[email.lower()], html_message, fail_silently=False)`, then
  `delete()` with its audit entry (no index resolves). A publish failure ends
  the task. Go renders every message before the first effect, then per row
  publishes and deletes in one fenced transaction.

### send_expiring_certificate_email / send_expired_certificate_email

- Live, not archived subscriptions created in the last 366 days whose
  certificate expires in 7 or 30 days (expiring) or expired yesterday
  (expired), with the select_related joins; the list is read and filtered by
  `is_current` before any send.
- Per subscription: owners with `medical_certificate_notifications` off are
  skipped; a subscription without associate raises and ends the task. Recipient:
  the associate e-mail when Django's `validate_email` accepts it, else the main
  tutor's (the first primary relation; else the first relation, which
  `get_main_tutor` saves as primary with an audit entry indexed through
  `associate.sport_association`, even when the associate e-mail is used), else
  the subscription user's; lower-cased with Python's `str.lower`.
- With a `CommunicationConfiguration` of the association (first by primary
  key): `send_email` as below; without one,
  `send_mail_async(..., from_email=DEFAULT_TEAM_EMAIL, ..., sport_association_id,
  reply_to=[owner email])`. A publish failure ends the task.

### CommunicationConfiguration.send_email (HTML body)

- Daily balance at or over the limit: nothing. Without host, port, user and
  password, or when `verify_smtp` fails (connect, STARTTLS for `TLS`, login;
  the connection is closed without QUIT):
  `send_mail_async(subject, message=None, from_email=DEFAULT_FROM_EMAIL with
  WHITELABEL_NAME replaced by the denomination, ..., skip_association_check=True)`.
  Otherwise the MIME message is sent on a second connection, then
  `daily_email_balance += 1` and `save()` of the loaded instance (every column;
  the audit entry compares every field but the password with the stored row,
  indexed `direct`). Any failure inside returns False and ends nothing.
- SMTP timeouts are 10 s per socket operation.

### send_reminders

- Not completed reminders due now with an association (inner joins on the
  association, its owner and the user; the instructor optional), unordered.
  Each is rendered once (`event_description` or `Nessun dettaglio.`), then
  `send_email_html` is called in the task for the user and, when present, the
  instructor, then the reminder is saved completed (audited, index `direct`).
- `send_email_html`: with a configuration, the send runs after a random 1-30 s
  delay in a Timer thread with the configuration loaded at call time; without
  one, `send_mail_async(..., from_email=DEFAULT_FROM_EMAIL, ...)`. Failures are
  swallowed. Go runs the delayed send in a goroutine (`time.AfterFunc`, same
  delay range) fenced by the Go ownership only (the job has completed), and
  the worker waits for pending ones on shutdown as Python waits for non-daemon
  threads. Two delayed sends of one association load the same balance and each
  saves balance + 1, as in Django.

## Not ported or refused

- Refused before any effect (job fails with `task_failed`): a run whose SMTP
  path would need a message or envelope outside the smtpsend subset (for
  example a reminder title with a line break, or an address with quotes or
  spaces), and template inputs the renderer refuses. The check covers every
  item the run would process; Django would send these.
- `send_updated_certificate_email`: its only call site
  (`BE/application/views/subscriptions_views.py`) is commented out, so no
  producer exists and the Go worker consumes only its own job table. Not
  registered.
- Differences only under infrastructure failures: database errors inside the
  per-item steps go back to the executor (the job fails) where Django logs and
  returns `None`; a COMMIT failure after a publish can publish again on retry.
- The EHLO name follows `socket.getfqdn()` with Go's resolver; where the C
  resolver reports extra aliases (macOS reports an `ip6.arpa` alias for `::1`)
  the name differs. The evidence normalizes the EHLO argument.

## Evidence

Receipts are under ignored `quality-reports/go-migration/tasks-db-c/`,
`auto-mark-worker-process/`, `email-tasks-worker-process/` and `schedule-due/`.

- **Auto mark worker** (`TESTS/adapters/go/auto_mark_worker_process.py`):
  race-built worker and migrate on the current Django schema in private
  PostgreSQL; four seeds (main, malformed meta, malformed expected absences,
  missing registry), each first processed by the actual Django task with
  authored assertions on the paths it took; the Go job must leave the same
  complete managed-model state. Seven groups: default Django owner, native
  parity, invalid version/payload/tenant, replay and a later run, three stop
  paths. RED: without the registration the job stays pending (`Timed out:
  registered auto mark worker durable completion`) after the Django oracles and
  the default-owner group pass. GREEN 7/7.
- **E-mail worker** (`TESTS/adapters/go/email_tasks_worker_process.py`): plus a
  private Redis and two fake SMTP servers (STARTTLS, implicit TLS) recording
  every client byte. One seed covers each filter, the tutor write, every
  configuration branch (STARTTLS and TLS delivery with balance saves,
  `apikey` login, no SMTP, limit reached, failed login fallback), the expired
  task's stop, reminders with and without configuration and the delayed sends.
  Each task runs first in Django with the production broker settings; Go must
  match the state, the Celery messages (envelope checked, bodies equal to
  Django's) and the SMTP transcripts (boundary and EHLO name normalized).
  Seven groups, including invalid envelopes for all four tasks and a refused
  SMTP subject that leaves no effect. RED: without the registrations the
  first native job stays pending. GREEN 7/7.
- **Timing** (`TESTS/adapters/go/schedule_due.py`): twelve new `*/15` and
  `*/45` cases match actual Celery 5.6.2 / Beat 2.8.1 (71 oracle cases); the Go
  differential runs 46 supported cases for each of ten tasks; 57 rejection
  cases include other step forms (`*/0`, `*/60`, `5/15`, `0-30/15`, scalar
  minute with `*` hour, hour steps, Rome, one weekday).
- **Golden**: `go test ./internal/emailrender` (36 Django renders) and
  `go test ./internal/smtpsend` (36 transcripts of the real
  `verify_smtp`/`send_email` against a scripted server, 258 MIME messages,
  2022 envelope addresses), regenerated by `TESTS/adapters/go/email_render_golden.py`
  and `TESTS/adapters/go/smtpsend_golden.py`.
- **Mutations** (all caught): carnet unpaid-payment check removed (Go state
  differs on the main seed); daily limit `>=` to `>` (expiring state and
  transcripts differ); `*/N` expansion starting at minute 1 (13 differential
  subtests fail).
- Re-run green after the shared changes: `occurrence.py`, `worker_process.py`,
  `age_worker_process.py`, `token_worker_process.py`, `audit_worker_process.py`,
  `user_deletion_worker_process.py`, `schedule_page.py`, `scheduler_process.py`;
  HTTP families `email-render-routes`, `attendance-mark-absent` and
  `carnet-subscription-delete` (shared `appendModelAudit`); `go vet ./...` and
  `go test -race` for service, celery, emailrender, smtpsend and cmd.

Current-model fixtures only; packaged images, installed ownership activation,
crash/drain groups for these tasks and mixed-runtime handoff remain open.
