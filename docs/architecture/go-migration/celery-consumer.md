# Celery consumer — Go ownership of message-consumed tasks

[tasks-db-e](tasks-db-e.md) kept every message-consumed task on Django's worker because all tasks share the one `celery`
Redis list. This slice adds the missing pieces: opt-in per-task routing to a dedicated queue on both producers, a Go
consumer for that queue with the worker-side semantics of Celery 5.6 / kombu 5.6 / django_celery_results 2.5, and the
first task, `core.tasks.send_mail_async`. Routing is **off by default**; nothing changes until a deployment sets
`GO_TASK_QUEUE_TASKS` and starts the consumer.

## Routing (off by default)

- `GO_TASK_QUEUE_TASKS` is a comma-separated list of registered Celery task names (for example
  `core.tasks.send_mail_async`). Empty (the default) defines nothing.
- Django: `BE/core/task_routing.py` builds `{name: {'queue': 'assozeta-go'}}`; `BE/core/settings.py` sets
  `CELERY_TASK_ROUTES` only when the list is non-empty. Celery then publishes those names with the anonymous exchange and
  routing key `assozeta-go` (binding `_kombu.binding.assozeta-go`), everything else unchanged.
- Go producer: `celery.ParseRoutes(cfg.GoTaskQueueTasks)` and `Producer.WithRoutes` in `cmd/server` and `cmd/worker`
  select the same queue for the same names; an explicit `Task.Queue` still wins.
- With the variable empty: no `CELERY_TASK_ROUTES` attribute, `app.conf.task_routes` is None and every name routes to
  `celery` (`BE/instance/tests/test_celery_task_routing.py`); the Go producer's golden payloads are unchanged
  (`go test ./internal/celery`, `TestRoutes` also checks that a routed payload differs only in its routing key). The
  golden file gains one case, `routed-to-go-queue`, captured from a Celery app configured with `go_task_routes`.
- A retry or deferral republishes to the queue the message came from (`signature_from_request` passes an explicit
  `queue`, which overrides the route), so a message that Django runs from `celery` keeps its retries on `celery`.

### Deployment change (documented, not enabled)

Self-host compose today runs `celery -A core worker` without `-Q`; it consumes only the default queue, so routed names
would wait on `assozeta-go` until a Go consumer runs. To enable Go ownership of a name:

1. Make the Django worker's queue explicit (same behaviour as today): `celery -A core worker -Q celery ...`.
2. Add a service running `celery-consumer --env-file <deployment .env> --concurrency 2` with the same environment as the
   Go worker (database, Redis, `EMAIL_*`, `DATA_RESTORE_LOCK_PATH` volume). It consumes `assozeta-go` only.
3. Set `GO_TASK_QUEUE_TASKS=core.tasks.send_mail_async` for **every** producer: the Django web and worker containers and
   the Go server and worker. Start the consumer before setting the variable.
4. Rollback: clear the variable (new messages go to `celery`), then stop the consumer after `assozeta-go` is empty;
   messages still on `assozeta-go` can be moved with the handoff rule below (they run unchanged on Django).

## The consumer (`cmd/celery-consumer`, `internal/celeryworker`, `internal/celery`)

What a Django worker does, and the Go counterpart:

| Celery / kombu behaviour | Go |
|---|---|
| Queue declaration at start (`SADD _kombu.binding.<q>`) | `Transport.Declare` |
| `BRPOP q, q\x06\x163, q\x06\x166, q\x06\x169` (1 s), then `QoS.append`: `ZADD unacked_index {tag: time()}`, `HSET unacked tag dumps([message, exchange, routing_key])` in one MULTI | `Transport.Get` (re-encodes the decoded envelope with Python's `json.dumps` rules: order, `ensure_ascii`, separators, repr floats) |
| Early ack when the task is accepted (`acks_late` off): `ZREM` + `HDEL` | `Transport.Ack` before admission and the task body |
| `restore_visible` at start and every 10th 10 s tick (100 s), under `unacked_mutex` (`SET NX PX 300000`, compare-and-delete release): entries older than the 3600 s visibility timeout go back with `RPUSH` and `redelivered: true` in headers and `delivery_info` | `Transport.RestoreVisible` |
| Warm shutdown: running tasks finish, held (eta) messages are restored by tag (`RPUSH`, redelivered) | `Worker.Run` drain (`--drain-timeout`, default 30 s), then `RestoreByTag` |
| `expires` passed at receipt: `mark_as_revoked` REVOKED `TaskRevokedError('expired')`, then ack | `revoke` |
| `eta` in the future: held unacknowledged until due | timer, then the next free slot |
| `CoordinatedTask.__call__`: shared `flock` on `DATA_RESTORE_LOCK_PATH` held for the whole body; pending restore or busy lock: `defer_for_maintenance` republishes with countdown 15 and the same retries (retrying the publish until it succeeds), adds itself to `request.children`, RETRY with `Busy`; a completed restore after `assozeta_published_at` (or no stamp): FAILURE `StaleDataTask` | `service.JobRestoreAdmission` (same lock file and query), `defer15`, `staleData` |
| `Task.retry(exc, countdown)`: same id, `retries + 1`, `eta = now + countdown`, `root_id` (or the id), `parent_id` (or the id), the request's `origin` in headers **and** properties, `reply_to` kept, custom headers (`assozeta_published_at`, a `redelivered` flag) kept after the standard ones, `argsrepr` `()` for empty args; published before RETRY is stored; past `max_retries` the original exception fails the task (FAILURE) | `celery.EncodeRepublish`, `Retry` handling |
| django-db result: `get_or_create(task_id)` then save; `content_type` `application/json`, `content_encoding` `utf-8`, `result` kombu JSON (`null`, or `{"exc_type", "exc_message", "exc_module"}` with `ensure_serializable` args, bytes as kombu's `{"__type__": "bytes"}`), `meta` `{"children": [...]}`; `task_name`, `task_args`, `task_kwargs`, `worker`, `traceback`, `periodic_task_name` NULL because `result_extended` is off | `repository.CeleryResults.StoreResult` (`INSERT ... ON CONFLICT (task_id) DO UPDATE`) |

### Handoff: the explicit refusal

A message the consumer does not run is moved, before any effect, to Django's `celery` queue in one MULTI (`SADD` binding,
`LPUSH`, `ZREM`/`HDEL` of the delivery): the decoded envelope re-encoded unchanged except `delivery_info.routing_key =
"celery"` (what Django would have published without the route); an undecodable payload is pushed byte for byte. Django's
worker then executes it as before. Handoff applies to:

- task names without a Go registration;
- payloads outside the decoded subset: other serializers or encodings, protocol 1, kombu types other than `uuid`
  (`datetime`, `decimal`, `bytes`, ...), unpaired surrogate escapes, naive timestamps, canvas options (callbacks, errbacks,
  chain, chord);
- options the republish encoder does not reproduce: `expires` that has not passed yet, `group`, `shadow`, stamping, time
  limits, a non-zero priority or a named exchange;
- inputs a task's `Plan` refuses (below).

Celery itself would reject unknown names and undecodable messages; handing them to Django is a deliberate difference so
that a routing mistake never loses a message.

### Not reproduced (documented differences)

- Prefetch: Django holds up to concurrency × prefetch multiplier messages unacked; Go reads one message per free slot.
  Eta messages are held without a slot, as in Celery.
- Remote control and events: `celery control revoke`, the revoked-id list, pidbox, heartbeats and `task-*` events are not
  implemented (events are off in this deployment); a revoke request does not reach the Go consumer.
- Infrastructure errors (database unavailable inside the task or during admission) are stored as
  `django.db.utils.OperationalError` with the fixed text `database error` instead of the driver's message.
- SMTP exception arguments: reply errors (`SMTPAuthenticationError`, `SMTPSenderRefused`, `SMTPDataError`,
  `SMTPConnectError`, `SMTPHeloError`, `SMTPResponseException`) carry smtplib's code and reply bytes; connection errors
  carry errno and strerror (`ConnectionRefusedError [61, "Connection refused"]` on macOS, `111` on Linux, as Python);
  `SMTPServerDisconnected`, `SMTPRecipientsRefused`, `SMTPNotSupportedError` and TLS errors keep the class but not
  Python's exact message.
- TLS to the system SMTP server verifies against Go's system roots, where Python's `ssl.create_default_context()` uses
  OpenSSL's default paths; the differential exercises plaintext SMTP only (smtpsend's TLS dialogue has its own goldens).
- At shutdown the drain is bounded (`--drain-timeout`); Celery's warm shutdown waits without limit.

## First task: `core.tasks.send_mail_async`

`BE/core/tasks.py` (`bind=True, max_retries=3`, default retry delay 180):

1. `sport_association_id` truthy: `SportAssociation.objects.filter(pk=...).first()` (deleted rows excluded); with an
   association and `skip_association_check` absent or falsy, `CommunicationConfiguration.objects.filter(...).first()`;
   with a configuration, `config.send_email(...)` and `result = 'Sent'`.
2. Otherwise `custom_send_mail(subject, message, from_email, recipient_list, **kwargs)`: `EmailMultiAlternatives`
   (`reply_to`, optional `text/html` alternative) sent through `instance.email_configuration.EmailBackend`
   (`effective_email`: the instance source when the instance is self-hosted with email settings, else the environment:
   `EMAIL_HOST`, `EMAIL_PORT` or 465, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, TLS/SSL/none, `ImproperlyConfigured`
   when both TLS and SSL are set), Django's SMTP backend (timeout 5, EHLO name `DNS_NAME`, login only with user and
   password; a failure during `open()` does not send QUIT, a failure after it does).
3. `EmailLog.objects.create(recipient=recipient_list[0], subject, result='Sent', sport_association)`.
4. Any exception: `self.retry(exc=exc, countdown=int(180 * 2 ** retries))`; on the fourth failure `retry` re-raises the
   original exception, so the task ends FAILURE with it and the `MaxRetriesExceededError` branch (its EmailLog) is not
   reached.

Go (`internal/service/send_mail_async.go`) runs step 2 with the environment source, steps 1 (lookups) and 3 and 4.
`Plan` hands off, before any effect: the configuration path (association with a CommunicationConfiguration and no
skip), the instance email source, `fail_silently` truthy, keyword arguments other than `fail_silently`, `html_message`,
`reply_to` and `skip_association_check`, more than five positional arguments or duplicates, non-string subject, a
`reply_to` string, an empty or non-list recipient list, a `sport_association_id` that is neither a UUID nor a UUID
string, `EMAIL_PORT` that `int()` rejects, an empty `EMAIL_HOST`, a subject over 255 or first recipient over 254
characters, and messages outside the rendered subset.

`smtpsend.RenderDjangoMessage` reproduces `message().as_bytes(linesep='\r\n')` (compat32): a single `text/plain` part,
or `multipart/alternative` with the text part only when the body is non-empty; `7bit` for ASCII parts, `8bit`
otherwise; line breaks to CRLF; headers Content-Type, MIME-Version, (Content-Transfer-Encoding), Subject, From, To,
Reply-To, Date (`-0000`), Message-ID, folded like compat32; Python's random boundary with its collision suffix.
Subset (`ErrUnsupported`, hence handoff): subjects that are ASCII up to 76 characters without folding whitespace runs or
non-ASCII as one encoded word up to 76 characters; From as `address` or `Name <address>` (ASCII kept as written, a
non-ASCII name as one encoded word); To and Reply-To as ASCII plain addresses; body lines up to 998 bytes (Django
switches to quoted-printable beyond). Golden: `TESTS/adapters/go/django_mail_golden.py` →
`BE_GO/internal/smtpsend/testdata/django_mail.json` (24 cases, 6 refused).

## Evidence

`TESTS/adapters/go/celery_consumer_process.py` (receipts in `quality-reports/go-migration/celery-consumer-process*`):
private PostgreSQL with the current Django schema, private Redis (DB 0, password), a fake SMTP server recording client
bytes. Each scenario publishes with Django's producer (routed by `go_task_routes('core.tasks.send_mail_async')`), pushes
the identical bytes to a real Django worker (`celery.contrib.testing.worker`, solo pool, `-Q assozeta-go`, django-db,
CoordinatedTask, production EmailBackend) and, after re-seeding, to the race-built Go consumer, then compares after each
worker stopped: SMTP transcripts (Message-ID, Date, boundary, EHLO name and SIZE normalized), the complete managed-model
state with TaskResult and EmailLog rows (timestamps within the run window), and every broker key (queues with decoded,
order-preserving messages; unacked hash and index; bindings), with random publish values normalized after a shape check
and a retry eta shown as its offset from `date_done`. Authored assertions check that the Django run took the intended
path. 14 scenarios:

plain text; html alternative with reply-to, two recipients and an association without configuration;
`skip_association_check` with a configuration; a deleted association; SMTP authentication failure (RETRY, retry message
with retries 1 and eta +180 s held, restored at shutdown); connection refused on retries 1 (+360 s); failure on the last
retry (FAILURE, no EmailLog); a restore completed after the stamp (StaleDataTask, no SMTP); a pending restore (RETRY Busy,
deferral +15 s with the same retries and itself as child); a future eta (held, restored redelivered); an expired message
(REVOKED); an unacked message past the visibility timeout (restored at start and executed); the configuration path and
`fail_silently=True` handed off (the message on `celery` must equal the original except its routing key, with no effect,
and a Django worker on `celery` must then reach the Django-only final state; in the configuration case the child
`send_mail_async` it publishes, routed back to `assozeta-go` with Django's `root_id`/`parent_id`, is executed by Go).

- RED: the consumer built without the `send_mail_async` registration hands every message off; the Django oracle passed
  and Go failed (`Timed out: Go consumer: plain text without association`). Receipt `celery-consumer-process-red`.
  The Django oracle alone passed all 14 scenarios before the Go task existed.
- GREEN: 14/14 (`celery-consumer-process/results.json`).
- Mutation (CAUGHT): `EncodeRepublish` dropping `assozeta_published_at` from the retry message
  (`celery-consumer-process-mutation`).
- Unit and golden tests: `go test ./internal/celery` (producer goldens incl. the routed case, envelope round trip of every
  captured payload, republish encoding, refusals, routes), `./internal/smtpsend` (Django message goldens),
  `./internal/service -run SendMailAsync` (plan handoffs, countdown); Django `instance/tests/test_celery_task_routing.py`.
- Re-run after the shared changes (producer routes, config field, `cmd/server`/`cmd/worker` wiring, smtpsend addition):
  HTTP families `subscription-approve` (27), `celery-routes-a` (76), `communications-send-email` (55) natively green;
  `email_tasks_worker_process.py` 7/7.

## Recipe: porting the next consumed task

1. **Characterize on Django first.** Add scenarios to a copy of `celery_consumer_process.py` (or parametrize it by
   task): build the message with Django's producer and the route, run the Django worker, write authored assertions
   (rows, transcripts or requests, result status and exception class, messages it publishes). Run with
   `CELERY_CONSUMER_DJANGO_ONLY=1` until the oracle passes.
2. **Implement** `celeryworker.Task{MaxRetries, Plan, Run}` in `internal/service`: `Plan` binds the Python signature from
   `Request.Args`/`Kwargs` (kombu-decoded: `uuid.UUID`, `celery.Dict`, `celery.Number`), performs only reads, and returns
   `ErrHandoff` for anything outside the subset; `Run` performs Django's effects in Django's order and returns the task's
   value, `*celeryworker.Retry` (the task's own `self.retry` policy), or `*celeryworker.Exception` (Python class, module and
   args). Register it in `cmd/celery-consumer/main.go`.
3. **Messages the task publishes**: use a producer with the routes, `celery.Task.RootID = request.RootID or request.ID`
   and `ParentID = request.ID` (Celery's `send_task`/`apply_async` inside a running task). Do not call
   `Request.AddChild`: `CoordinatedTask.__call__` runs the body through `Task.__call__`, which pushes its own request,
   so body publishes never reach the stored `meta.children` (only the maintenance deferral, raised before the body, does).
   First used by [consumer-workflow](consumer-workflow.md).
4. **Run** RED (without the registration), GREEN, a semantic mutation, and re-run the families that share touched code.
5. **Enable** per name with `GO_TASK_QUEUE_TASKS` only after GREEN.

Per remaining task:

| Task | What the port needs |
|---|---|
| `send_email_text` | One `send_mail_async.apply_async(kwargs=...)` from inside the task: child publish with root/parent ids and children meta (step 3); routed to `assozeta-go` itself when listed. |
| `send_email_template` | `render_to_string(template, data)` with the template name from the message: hand off names outside `internal/emailrender`'s embedded set; golden renders per template. |
| `send_email_html` | With a configuration, the send runs from a `threading.Timer` 1-30 s after the task returns (the result is already SUCCESS); reuse the delayed-send pattern of tasks-db-c, and port `CommunicationConfiguration.send_email` without the Go job fencing (it is also the handoff reason of this slice). |
| `print_document_subscription`, `print_document_invoice` | `requests.get(CURRENT_HOST/document/<kind>/<id>, Authorization=<token>)` with errors swallowed; test against a stub server recording requests. |
| `save_to_storage` | `default_storage.save` with `AWS_S3_FILE_OVERWRITE=False` (exists checks, random suffix): reproduce the name selection against MinIO. |
| `export_invoices_to_zip` | ZIP bytes (entry order and timestamps) and the Rome-clock file name; golden archives. |
| workflow triggers (`check_workflows_trigger`, `check_workflows_trigger_cron`, `workflow_block_execute`) | Ported: [consumer-workflow](consumer-workflow.md). |
| restore / import / export (`instance.restore.*`, import/export tasks) | Prefork capabilities (acks_late, reject_on_worker_lost, time limits, the `instance.restore.` admission bypass) are in place: [consumer-restore](consumer-restore.md). |
