# Celery consumer — automation workflow tasks

`check_workflows_trigger`, `check_workflows_trigger_cron` and `workflow_block_execute` (`BE/application/tasks.py`, with
`AutomationWorkflow.trigger` in `BE/communications/models.py`) run on the Go consumer (`cmd/celery-consumer`) when
`GO_TASK_QUEUE_TASKS` lists them; see [celery-consumer](celery-consumer.md) for routing, the worker semantics and the
handoff rule. Go: `internal/service/workflow_tasks.go`, `internal/repository/workflow_tasks.go`,
`internal/domain/workflow_tasks.go`; registration in `cmd/celery-consumer/main.go`.

## What Django does

Every body is wrapped in `try/except Exception` that only logs. Any Python exception (an invalid UUID string in a
lookup, a malformed `automation_tree`, an unbound countdown) ends the task with SUCCESS and result `null`; messages
already published stay published. CoordinatedTask admission applies as for every task.

- **check_workflows_trigger(trigger_type, sport_association_id, subscription_id=None, data=None)**: the association's
  enabled workflows (`count()`, then `iterator()` without ORDER BY). For each workflow whose `automation_tree[0]['value']`
  equals `trigger_type`: `Subscription.objects.filter(subscription_id=...).first()`; no subscription stops the whole
  task; otherwise `workflow.trigger(...)`. `data` is not used.
- **AutomationWorkflow.trigger** (type equal to the first node): a `cron` node runs the cron fan-out below; any other node
  sends `workflow_block_execute(args=[workflow_id, subscription_id, 1])` when the tree has more than one node.
- **check_workflows_trigger_cron(workflow_automation_id)**: the enabled workflow (`first()`), nothing when missing or when
  `len(automation_tree) <= 1`, then `trigger(trigger_type='cron')`, which does nothing unless the first node is `cron`.
- **Cron fan-out**: subscriptions of the workflow's association with `start_date <= today <= end_date` (UTC date). Target
  `all`; `approved`/`rejected`/`not_signed`/`pending` (status 4/3/1/2 and `archived=False`); `archived`; otherwise
  `Tags.objects.filter(tag_id=target).first()` (not scoped by association) and, when found,
  `Q(tags__tag_id=...) | Q(tags__tag_name__icontains=...)` with inner joins: a subscription is repeated once per matching
  tag. A tag id that is not found leaves the queryset unfiltered. One `workflow_block_execute(..., 1)` per row, in the
  order PostgreSQL returns them.
- **workflow_block_execute(workflow_id, subscription_id=None, idx=None)**: the subscription (select_related associate and
  user, deleted rows included), then the enabled workflow with its association owner. No workflow: `None`. No
  subscription: `(False, "Subscription is None")`. `node = automation_tree[idx]` (Python indexing, negative from the end),
  `next_id = idx + 1` or None past the end.
  - `message`/`email`: recipients `athletes`/`all` build `[user.email]` (empty for `athletes` when the owner is the
    subscriber) plus the owner's email when different, the associate's email when truthy, then
    `associate.get_main_tutor()` (promotes and saves the first tutor relation when none is primary: an audited update
    indexed through the associate's association) and the tutor's email when the associate is a minor. Any other value
    mails the owner only. `list(set(...))` removes duplicates; `countdown = random.randint(1, 180)`;
    `send_task('send_email_html', kwargs={html_message, subject, recipient_list, sport_association_id, reply_to: [owner]})`
    and, with a next block, `send_task('workflow_block_execute', args=[workflow_id as received, subscription_id, next_id])`,
    both with that countdown.
  - `wait`: `int(amount) * 60/3600/86400` for `minutes`/`hours`/`days` and the next block with that countdown (`0` means
    no eta). Another type with a next block raises (countdown unbound).
- Messages are published with `app.send_task` inside the worker: `root_id` is the request's root, `parent_id` its id, a
  fresh publish stamp, and the routes of `GO_TASK_QUEUE_TASKS` (`send_email_html` goes to `celery` unless listed). In the
  production (prefork) worker each one is appended to `task.request.children` and stored, in publish order, in the
  result's `meta.children` (Go: `Request.AddChild` after each publish). The first oracle, a worker thread inside the
  harness, stored `{"children": []}` because there `Task.__call__` pushes a fresh request.

## Go

`Plan` binds the signature and performs every read in Django's order, recording the effects (the tutor promotion and
each message with its countdown); a Python exception becomes the `None` result with the effects recorded so far. `Run`
performs them in order: the promotion (audit entry and index, then the row update) and each publish through
`celery.Producer` with routes, `RootID`/`ParentID` from the request (`celery.Task` gained both fields; empty keeps the
producer's previous output) and `eta = now + countdown`.

Deterministic where Django is: the same lookups and the same SQL shape without ORDER BY for the trigger loop and the cron
queryset, Python's index and equality rules on the JSON tree, the tutor relation chosen by primary key. Where Django is
random, Go draws its own value: the email countdown is uniform in 1..180 (shared by the block's two messages), and the
recipient list keeps the first occurrence of each address (Python's order follows the per-process string hash seed).

### Handed off to Django (before any effect)

Arguments Python would reject at binding (missing required, unknown keyword, too many positional); UUID arguments that
are neither a UUID, a string nor None; a non-string, non-None `trigger_type` or cron `target`; a boolean `idx` or an
index beyond int64; a `wait` amount other than a JSON integer or a plain decimal string (floats, booleans, strings that
are not `[ws][+-]digits[ws]`); a countdown outside ±2^31 s; email `content`/`subject` containing JSON floats; a tree the
JSON decoder refuses.

### Differences (documented)

- Infrastructure errors (database, broker publish) are stored as the consumer's `OperationalError('database error')`
  FAILURE; Django's body catches them and stores SUCCESS `None`.
- Logging is not reproduced.

## Evidence

`TESTS/adapters/go/celery_consumer_workflow_process.py` (built on `celery_consumer_process.py`): private PostgreSQL with
the current Django schema and a private Redis. Each scenario publishes with Django's producer (routes for the three
names), runs a real Django worker (`-Q assozeta-go`, django-db, CoordinatedTask) and, on the same seed, the race-built Go
consumer. Children routed back to `assozeta-go` are executed in the same run (eta children are held and restored at
shutdown). Compared: the complete managed-model state (TaskResult rows, LogEntry/AuditLogIndex of the promotion), every
broker key, and every message published during the run (recorded through Redis MONITOR, executed children included),
with ids labelled by their position in the publish tree, `root_id`/`parent_id` checked against it, the random countdown
checked to lie in 1..180 s and to be shared by the two messages of a block, `argsrepr`/`kwargsrepr` checked against
`saferepr` of the published arguments before `recipient_list` is sorted. 20 scenarios:

trigger publishing the first block whose chain mails everyone and waits; email block for a minor with tutor promotion;
two-day wait; last email block to the owner; missing subscription (tuple result); disabled workflow; `athletes` block with
the owner as subscriber; zero-minute wait (no eta, executed) followed by an unknown wait type; negative index; malformed
subscription id; trigger with an unknown subscription; trigger keeping the first workflow's publish when the next tree
raises; cron targets `approved` (chain mails), tag (duplicate rows), `all`, `archived`, a single-node workflow, a target
that is neither keyword nor UUID; handoffs of a float wait amount (Django then schedules the next block, held by Go) and
of a non-string trigger type.

- Oracle: Django alone 20/20 (`consumer-workflow-process-oracle`).
- RED: consumer built without the registration; Django passed, Go timed out on the first scenario (all messages handed
  off) (`consumer-workflow-process-red`).
- GREEN: 20/20 (`consumer-workflow-process/results.json`).
- Mutation (CAUGHT): the cron status filter without `archived=False` (`repository/workflow_tasks.go`); Go fanned out to the
  archived accepted subscription as well and never reached Django's result count (`Timed out: Go consumer: cron approved
  target ...`, `consumer-workflow-process-mutation`).
- Re-run after the shared changes (`celery.Task` root/parent fields, `cmd/celery-consumer` registration): `celery_consumer_process.py`
  (send_mail_async) 14/14; `go test ./internal/celery` (producer goldens unchanged).
- Unit tests: `go test ./internal/service -run WorkflowTasks`, `./internal/celery -run EncodeParentIDs`.

### Prefork re-check (`consumer-prefork-recheck`)

The harness runs the Django oracle as a `celery worker -P prefork -c 1` subprocess by default (production has no `-P`
in `selfhost/compose.yml`, so Linux uses prefork); `CELERY_CONSUMER_ORACLE=thread` keeps the earlier in-thread worker.
The authored check now requires each stored row's `meta.children` to list the messages that task published, in order.

- Django prefork oracle alone: 20/20 (`consumer-prefork-recheck-workflow-oracle`).
- RED: only `meta.children` differed on the first scenario (Django `T: [T.0]`, `T.0: [T.0.0, T.0.1]`; Go `[]`)
  (`consumer-prefork-recheck-workflow-red`).
- GREEN: 20/20 (`consumer-prefork-recheck-workflow-green`).
- Mutation (CAUGHT): `AddChild` skipped for eta publishes (`service/workflow_tasks.go`); the first block's row lost
  `[T.0.0, T.0.1]` (`consumer-prefork-recheck-workflow-mutation`).
- Re-run of the other consumer harnesses: see consumer-email.md.

