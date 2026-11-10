# Celery producer — publishing Django tasks from Go

`BE_GO/internal/celery` lets a Go route publish the same Celery message Django's `<task>.delay(...)` / `apply_async(...)`
publishes, to the same broker, so the existing Django workers execute it. Django stays the owner and executor of every task
(nothing here consumes, schedules or changes ownership; the Go job repository and worker are unrelated).

## What Django publishes (pinned: celery 5.6.2, kombu 5.6.2, redis-py 5.2.1)

- Broker: `CELERY_BROKER_URL` (`BE/core/settings.py`) is `redis[s]://REDIS_USERNAME:REDIS_PASSWORD@REDIS_HOST:REDIS_PORT` with no
  database number, so DB 0 (the Django cache is DB 1). There are no `CELERY_TASK_ROUTES` / queue settings and the worker runs
  `celery -A core worker` without `-Q` (`selfhost/compose.yml`): every task goes to the default queue `celery`.
- Publish: kombu declares the queue (`SADD _kombu.binding.celery "celery\x06\x16\x06\x16celery"`, once per connection) and
  `LPUSH celery <payload>`; anonymous exchange `''`, routing key `celery`, priority 0, `delivery_mode` 2.
- Payload: task protocol 2, JSON serializer (`json.dumps` defaults: `ensure_ascii`, `", "`/`": "` separators), body base64.
  Envelope keys `body, content-encoding, content-type, headers, properties`. Headers in Celery's order
  (`lang, task, id, shadow, eta, expires, group, group_index, retries, timelimit, root_id, parent_id, argsrepr, kwargsrepr, origin,
  ignore_result, replaced_task_nesting, stamped_headers, stamps`) plus `assozeta_published_at`; properties
  `correlation_id, reply_to, delivery_mode[, expiration], delivery_info, priority, body_encoding, delivery_tag`.
  Body `[args, kwargs, {"callbacks": null, "errbacks": null, "chain": null, "chord": null}]`; a `uuid.UUID` argument is kombu's
  registered type `{"__type__": "uuid", "__value__": {"hex": "..."}}` (the worker gets a UUID back). `argsrepr`/`kwargsrepr` are
  `celery.utils.saferepr` with maxlen 1024 (only `'` escaped, `...` truncation, 3 levels).
- Restore fencing: `BE/instance/restore/task_base.py` connects `stamp_published_task` to `before_task_publish`, which
  `setdefault`s `headers['assozeta_published_at'] = time.time()`. The worker's `CoordinatedTask` raises `StaleDataTask` when a data
  restore completed after that stamp (or the stamp is missing). The Go producer stamps the same header once per message, before
  the first attempt, so publish retries keep the original stamp (as Celery's do).
- Timing: `.delay()` runs where the Django code calls it. Without `transaction.on_commit` and with autocommit (no
  `ATOMIC_REQUESTS`), that is right after the preceding `save()` has committed; a broker failure then is a 500 with the save kept.
  A Go port commits its transaction first and publishes afterwards. Routes whose Django code publishes inside `transaction.atomic()`
  or through `on_commit` must reproduce that point instead; check each call site.
- Retries: `task_publish_retry` with the default policy (3 retries after 0 s, 0.2 s, 0.4 s) on connection errors.

## Go API

```go
tasks := celery.NewProducer(celery.RedisBroker{Client: broker}) // cmd/server/main.go: Redis DB 0, cache options otherwise
id, err := tasks.Publish(ctx, celery.Task{Name: "check_workflows_trigger", Kwargs: celery.Dict{
    {Key: "trigger_type", Value: "subscription_approved"},
    {Key: "sport_association_id", Value: uuid.MustParse(associationID)}, // a UUID in Django -> uuid.UUID here
    {Key: "subscription_id", Value: uuid.MustParse(subscriptionID)},
}})
```

`RedisBroker.Push` sends the binding `SADD` and the `LPUSH` in one `MULTI` on every publish (kombu adds the binding once per
connection; the set member is the same). Services take the `celery.Publisher` interface (`celery.Recorder` is an in-memory fake). Kwargs keep Django's call order (it is
visible in `kwargsrepr` and the body). Publish after the commit with `context.WithoutCancel` plus a timeout: Django's view is
not interrupted when the client goes away. Return the error (the 500 page) like Django.

`apply_async(args=[...])` with a list (not a tuple) makes Celery's `argsrepr` the list's repr (`['a', None]`): set
`Task.ArgsList` (golden case `list-args`; harness spec `'args_list': True`). `.delay(...)` and tuple args keep the default.

Supported: protocol 2 messages to the default queue (or a named direct queue), positional and keyword arguments of type None,
bool, int/int64, str, `uuid.UUID`, list and dict (`celery.Dict`, ordered), `ETA` (countdown) and `Expires` as absolute UTC times
(with kombu's `expiration` property), the restore stamp, Celery's publish retry policy, TLS through the shared Redis options.

Not supported (refuse the route or path before any write, or extend the package with golden evidence first): float, Decimal,
datetime/date, bytes, sets, tuples inside arguments and model instances as arguments; `link`/`link_error` callbacks, chains,
groups, chords and `apply_async(queue=..., priority=..., headers=...)` options; `task_routes`; reading results
(`AsyncResult.get()`, the django-db result backend), revoking, and anything that waits on the worker; `task_send_sent_event`;
eta/expires given in a non-UTC timezone (Go renders `+00:00`); other brokers (AMQP) and the `rediss` options kombu derives from
the URL beyond the shared TLS configuration. Delivery is at least once, as with kombu: a broker timeout after the push can lead
to a duplicate on retry.

## Evidence

- Byte parity: `TESTS/adapters/go/celery_producer_golden.py` publishes eight authored tasks through a Celery app built like
  `BE/core/celery.py` (`CoordinatedTask`, the restore stamp, broker on a private redis-server DB 0) and records the LPUSH payloads
  and broker commands in `BE_GO/internal/celery/testdata/kombu_messages.json` (the hostname in `origin` is fixed to `golden-host`).
  `go test ./internal/celery` re-encodes each from its inputs with the captured id, delivery tag, reply_to, origin, stamp and
  expiration and requires identical bytes: the subscription-approved call, text escaping (quotes, non-ASCII, astral, control
  characters), ints beyond 2^53, booleans/None, a single-argument tuple, nesting past maxlevels, truncation of long text and of
  quoted text, countdown and expires. Unit tests cover the stamp, retry delays, resending the same message, no retry on error
  replies or cancellation, and refusal of unsupported values.
- HTTP families: `family_harness.with_celery_broker(...)` points Django's Celery app at the run's private Redis DB 0 with the
  production queue settings (the test settings use `memory://` and queue `contracts`); the Go server publishes to the same Redis.
  `family_harness.celery_published(contract, fixture, expected, since=, until=)` drains the queue after every case and checks each
  message against the authored task: exact envelope, header and property order and values, `argsrepr`/`kwargsrepr`, the body
  bytes (`kombu.utils.json.dumps`) and their decoded values, the binding, and the shape of the random fields (uuid4 id equal to
  `root_id`/`correlation_id`, uuid delivery tag and reply_to, `gen<pid>@<host>` origin, stamp within the request window).
- Worker acceptance: `family_harness.celery_worker_accepts(...)` pushes a captured payload back and runs a real worker of the Django
  app (`celery.contrib.testing.worker.start_worker`, solo pool, django-db backend): the task must start with the decoded kwargs and
  succeed; with a data restore completed after the stamp, the same message must fail with `StaleDataTask`. Result and restore
  rows and the worker's broker keys are removed afterwards.
- First consumer: [subscription-approve](subscription-approve.md) (both checks on all four transports).

## Recipe for a family that publishes

1. Find the `.delay`/`apply_async` call and its position relative to the commit (autocommit, `atomic`, `on_commit`), the task
   name (`@shared_task(name=...)`) and the exact argument types (UUID objects vs strings, ints, None).
2. Generator: put `'celery': [{'task': name, 'args': [...], 'kwargs': [[key, value], ...]}]` in each success effect (UUIDs as
   `{'uuid': '<id>'}`, kwargs as ordered pairs; optional `eta`/`expires` ISO strings); cases that publish nothing have none.
3. Adapter: `fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))`; return
   `{'started': time.time()}` from `prepare`; in the effect check call
   `harness.celery_published(contract, context['fixture'], effect['celery'] if effect else [], since=..., until=time.time())` on
   every case; optionally wrap `run_workload` to run `harness.celery_worker_accepts` on `fixture['celery_accepted'][-1]`.
4. Go: inject `celery.Publisher`, publish at Django's point (after `Commit()` for autocommit code), unit-test the order with a fake
   transaction and `celery.Recorder`-style publisher; mutation candidates: a wrong argument type/name, publishing before commit.

## Remote control (pidbox)

`internal/celery/pidbox.go` sends `app.control.inspect(timeout=...).<command>()` over the Redis broker the way
kombu does from a fresh process: the reply queue binding, the fanout PUBLISH on `/0.celery.pidbox` with kombu's
envelope, BRPOP until the collection timeout passes without a reply, and the reply queue removed afterwards.
The instance diagnostics `worker` probe uses it for `ping` ([instance-admin](instance-admin.md)); a real
`core.celery` worker answered it.

