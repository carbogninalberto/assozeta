"""Engine of the Phase 4 task matrix (contract: TESTS/handoff/test_p4_task_matrix.py).

A family module (TESTS/handoff/p4_family_<id>.py) exports SCENARIOS = {name: fn(stack)}; most
build them with ``standard(...)`` from a Case: how to seed business state, how each runtime's
actual producer publishes the task, and how to verify the business outcome by identity. The
engine runs the case in steady Django mode, steady exclusive Go mode and across a switch with the
task queued (both directions), always through the identity oracle of Phase 3 and with the
exclusive-mode rule: in Go mode no Django process runs and no copy reaches the Django queue.

Producers (the actual publication paths, admitted per CONTRACT P3-A/B):
  django   Task.apply_async on the private broker (the Django producer API every caller uses)
  go_job   repository.Jobs.Enqueue through cmd/handoff-probe (native durable kinds)
  go_task  the Go Celery producer contract: an admitted protocol-2 envelope on 'assozeta-go'
           (celery.Producer.Publish wiring; the Go API/worker callers emit exactly this message)
"""
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
import json
import os
import shutil
import subprocess
import time
import uuid

from TESTS.handoff import p3_oracle
from TESTS.handoff.p3_stack import GO_QUEUE, NIL, ContractFailure, check, wait_for
from TESTS.handoff.p4_matrix import BY_ID, FAMILIES
from TESTS.handoff.p4_outbox_stack import P4OutboxStack

TERMINAL = ('completed', 'failed', 'cancelled', 'expired', 'revoked', 'blocked')
SELECTED = [f for f in os.environ.get('ASSOZETA_HANDOFF_P4_FAMILIES', '').split(',') if f] or [f.id for f in FAMILIES]


def selected_needs():
    return {need for family in SELECTED if family in BY_ID for need in BY_ID[family].needs}


class P4MatrixStack(P4OutboxStack):
    """The outbox stack plus the private services the selected families need ('storage': MinIO)."""

    def __init__(self, output):
        super().__init__(output)
        self.storage_endpoint = None
        self._minio = None
        self.processes = {'django': [], 'go': []}

    def start(self):
        if 'storage' in selected_needs():
            self.start_storage()
        super().start()
        # Both runtimes share the restore lock (the Go env file already names this path; Django reads it
        # from the environment before any restore work).
        self.env['DATA_RESTORE_LOCK_PATH'] = str(self.work / 'contract-lifecycle.lock')
        self._baseline_env = dict(self.env)
        return self

    def django_environment(self):
        # Modules that open their own Redis client from core.settings (notifications.utils.REDIS_POOL) must use the
        # stack's private Redis, never a host from BE/.env or a default localhost:6379 (driver and workers alike).
        from TESTS.handoff.p3_stack import REDIS_PASSWORD
        values = super().django_environment()
        values.update(REDIS_HOST='127.0.0.1', REDIS_PORT=str(self.redis_port), REDIS_USERNAME='default',
                      REDIS_PASSWORD=REDIS_PASSWORD)
        return values

    def start_storage(self):
        from TESTS.adapters.django.instance_logo import minio
        self._minio_context = minio(self.log, {k: v for k, v in os.environ.items()}, {})
        self.storage_endpoint, self.storage_access, self.storage_secret = self._minio_context.__enter__()
        self.storage_bucket = 'handoff-p4'
        import boto3
        boto3.client('s3', endpoint_url=self.storage_endpoint, aws_access_key_id=self.storage_access,
                     aws_secret_access_key=self.storage_secret, region_name='us-east-1').create_bucket(Bucket=self.storage_bucket)

    def storage_env(self):
        if not self.storage_endpoint:
            return {}
        return {'STORE_ON_DISK': 'False', 'AWS_ACCESS_KEY_ID': self.storage_access,
                'AWS_SECRET_ACCESS_KEY': self.storage_secret, 'AWS_STORAGE_BUCKET_NAME': self.storage_bucket,
                'AWS_S3_REGION_NAME': 'us-east-1', 'AWS_S3_ENDPOINT_URL': self.storage_endpoint, 'AWS_LOCATION': 'private'}

    def extra_django_environment(self):
        # core.settings derives the team/support senders from these at import; both runtimes share one identity.
        identity = {'EMAIL_HOST_USER': 'team@example.test', 'EMAIL_HOST_SUPPORT': 'support@example.test',
                    'WHITELABEL_NAME': 'Handoff Fixture'}
        return {**super().extra_django_environment(), **self.storage_env(), **identity}

    def go_env_file(self):
        path = super().go_env_file()
        extra = {**self.storage_env(), 'EMAIL_HOST_USER': 'team@example.test', 'EMAIL_HOST_SUPPORT': 'support@example.test',
                 'WHITELABEL_NAME': 'Handoff Fixture'}
        if 'P4_STORAGE=1' not in path.read_text():
            lines = [l for l in path.read_text().splitlines() if l.split('=', 1)[0] not in extra]
            path.write_text('\n'.join(lines + [f'{k}={v}' for k, v in extra.items()] + ['P4_STORAGE=1']) + '\n')
        if not hasattr(self, '_go_env_baseline'):
            self._go_env_baseline = path.read_text()
        return path

    def go_consumer_env_file(self):
        path = super().go_consumer_env_file()
        text = path.read_text()
        if 'EMAIL_HOST_USER=\n' in text:
            path.write_text(text.replace('EMAIL_HOST_USER=\n', 'EMAIL_HOST_USER=team@example.test\n'))
        return path

    def reset(self):
        super().reset()
        self.processes = {'django': [], 'go': []}
        if hasattr(self, '_baseline_env'):
            self.env = dict(self._baseline_env)  # a family may adjust worker environment for its own scenario only
        if hasattr(self, '_go_env_baseline'):  # ... and the Go env files (the consumer's is derived again)
            (self.work / 'go.env').write_text(self._go_env_baseline)
            consumer = self.work / 'go-consumer.env'
            if consumer.exists():
                consumer.unlink()
        # Family fixtures own their business rows: every scenario starts from an empty business schema.
        self.sql('TRUNCATE bakney_user, application_sportassociation, auditlog_logentry CASCADE')

    def close(self):
        try:
            super().close()
        finally:
            if self._minio is not None or self.storage_endpoint:
                with suppress(Exception):
                    self._minio_context.__exit__(None, None, None)

    # -- executors --------------------------------------------------------------------------------
    def executors(self, runtime):
        """Start the runtime's task executors once: Django worker, or Go worker + Go consumer."""
        if not self.processes[runtime]:
            if runtime == 'django':
                self.processes['django'].append(self.django_worker())
                self.django_ready()
            else:
                self.processes['go'] += [self.go_worker(), self.go_consumer()]
        return self.processes[runtime]

    def stop_executors(self, runtime):
        """Stop and forget the runtime's executors. Before a switch pass them to switch(stop=...) instead: a
        just-stopped consumer still looks live within the heartbeat grace and close_admission waits for it."""
        for process in self.processes[runtime]:
            self.stop(process)
        self.processes[runtime] = []


# -- producers ----------------------------------------------------------------------------------------
def produce_django(stack, name, args=(), kwargs=None, **options):
    """Task.apply_async of the registered task (Django producer API, admitted)."""
    task_id = options.pop('task_id', None) or str(uuid.uuid4())
    stack.app.loader.import_default_modules()  # task autodiscovery is lazy in the driver
    task = stack.app.tasks[name]
    try:
        task.apply_async(args=list(args), kwargs=kwargs or {}, task_id=task_id, **options)
    except Exception as exc:
        raise ContractFailure(f'Django producer of {name} raised {type(exc).__module__}.{type(exc).__name__}: {exc}') from None
    return task_id


def produce_go_task(stack, name, args=(), kwargs=None, *, queue=GO_QUEUE, priority=0, eta=None, expires=None,
                    parent_id=None, root_id=None, retries=0):
    """The Go Celery producer contract: admit (runtime go, transport broker) then push the envelope."""
    task_id = str(uuid.uuid4())
    payload = stack.envelope(name, task_id, args=args, kwargs=kwargs or {}, queue=queue, eta=eta, expires=expires,
                             parent_id=parent_id, root_id=root_id, retries=retries, priority=priority, stamp=time.time())
    outcome = stack.admit('go', task_id, name, queue, payload, eta=eta, expires=expires)
    check(outcome == 'accepted', f'Go producer admission of {name}: {outcome}')
    stack.push(queue, payload, priority)
    return task_id


def produce_go_job(stack, kind, payload='{}', association=NIL, available_at=None):
    job_id = str(uuid.uuid4())
    command_payload = payload if isinstance(payload, str) else json.dumps(payload)
    result = stack.go_enqueue(job_id, kind=kind, payload=command_payload, available_at=available_at)
    check(result.get('outcome') == 'enqueued', f'Go durable producer of {kind}: {result}')
    return job_id


# -- waits and checks -------------------------------------------------------------------------------
def await_done(stack, task_id, who, timeout=90):
    row = wait_for(lambda: (lambda r: r if r and r['state'] in TERMINAL else None)(stack.work_row(task_id)),
                   f'{who} never finished {task_id} (ledger state '
                   f'{(stack.work_row(task_id) or {}).get("state")}, broker copies {sorted(stack.redis_copies(task_id))})',
                   timeout=timeout)
    return row['state']


def exclusive_go(stack, task_ids, generation=None):
    """Exclusive Go mode: nothing handed back to the Django queue, no Django attempt, no Django process.
    After a switch to Go at `generation`, the source's history (finished attempts of earlier generations) is
    expected; only a Django attempt at or after that generation, or one still running, breaks exclusivity."""
    for task_id in task_ids:
        stale = sorted(k for k in stack.redis_copies(task_id) if k.startswith('celery'))
        check(not stale, f'{task_id} was handed back to the Django queue {stale} in exclusive Go mode')
        django = [a for a in stack.attempts(task_id=task_id) if a['runtime'] == 'django' and (
            generation is None or a['generation'] >= generation or a['outcome'] == 'running')]
        check(not django, f'{task_id} has Django attempts in exclusive Go mode: {django}')
    check(not stack.processes['django'], 'a Django executor ran in exclusive Go mode')


def identity(stack, accepted, source, target, completions):
    """The Phase 3 oracle; database-effect counts (the reset task's trigger) must come only from the
    runtimes that completed attempts."""
    from TESTS.handoff.p3_oracle import RUNTIME_APPLICATIONS
    observed = stack.effects()
    effects = {runtime: sum(observed.get(app, 0) for app in apps) for runtime, apps in RUNTIME_APPLICATIONS.items()}
    for runtime, count in completions.items():
        check(count or not effects.get(runtime), f'{runtime} committed {effects.get(runtime)} business effects without '
                                                 'a completed attempt')
    return p3_oracle.check(stack, accepted=accepted, source=source, target=target, effects=effects,
                           completions=completions)


class Case:
    """seed(stack) -> context; produce[runtime](stack, context) -> [task ids]; verify(stack, context, runtime)
    raises ContractFailure unless the business outcome (row identities, files, sends, provider calls) is right;
    children(stack, context) -> {child task id: expectation} published by the job itself (optional)."""

    def __init__(self, task, seed, produce, verify, children=None, timeout=90):
        self.task, self.seed, self.produce, self.verify, self.children, self.timeout = (task, seed, produce, verify,
                                                                                        children, timeout)


def run_steady(stack, case, runtime):
    stack.set_authority(runtime, 1)
    context = case.seed(stack)
    stack.executors(runtime)
    task_ids = case.produce[runtime](stack, context)
    for task_id in task_ids:
        state = await_done(stack, task_id, f'the {runtime} executor', case.timeout)
        check(state == 'completed', f'{case.task} on {runtime}: ledger state {state} (expected completed)')
    case.verify(stack, context, runtime)
    if runtime == 'go':
        exclusive_go(stack, task_ids)
    other = 'go' if runtime == 'django' else 'django'
    children = case.children(stack, context) if case.children else {}
    identity(stack, {t: 'source_completed' for t in task_ids} | children, (runtime, 1), (other, 2),
             {runtime: len(task_ids) + sum(1 for v in children.values() if v == 'source_completed'), other: 0})


def run_queued_switch(stack, case, source, target):
    """The task is accepted on the source and still queued when the switch happens; it runs once on the target."""
    stack.set_authority(source, 1)
    context = case.seed(stack)
    task_ids = case.produce[source](stack, context)
    generation = stack.switch(source, 1, target)
    stack.executors(target)
    for task_id in task_ids:
        state = await_done(stack, task_id, f'the {target} executor after the switch', case.timeout)
        check(state == 'completed', f'{case.task} {source}->{target}: ledger state {state}')
    case.verify(stack, context, target)
    if target == 'go':
        exclusive_go(stack, task_ids)
    children = case.children(stack, context) if case.children else {}
    identity(stack, {t: 'target_completed' for t in task_ids} | children, (source, 1), (target, generation),
             {target: len(task_ids) + sum(1 for v in children.values() if v == 'target_completed'), source: 0})


def standard(case, family):
    """The four boundaries every task runs through (steady both ways, queued at the switch both ways); each
    ends with the family's machine-checked record (CONTRACT P4-D) so a scenario is green only when both the
    behaviour and its record are in place."""
    slug = case.task.replace('.', '_')

    def with_record(body):
        def scenario(stack):
            require_matrix_prerequisites(stack)
            body(stack)
            require_record(case.task, family)
        return scenario
    return {f'{slug}__django': with_record(lambda stack: run_steady(stack, case, 'django')),
            f'{slug}__go': with_record(lambda stack: run_steady(stack, case, 'go')),
            f'{slug}__queued_forward': with_record(lambda stack: run_queued_switch(stack, case, 'django', 'go')),
            f'{slug}__queued_reverse': with_record(lambda stack: run_queued_switch(stack, case, 'go', 'django'))}


def require_record(task, family):
    """The task's row in docs/architecture/go-migration/task-handoff-dispositions.json (CONTRACT P4-D)."""
    from TESTS.handoff.test_p4_inventory import DISPOSITIONS
    check(DISPOSITIONS.exists(), 'contract: docs/architecture/go-migration/task-handoff-dispositions.json is missing '
                                 f'(record for {task})')
    record = json.loads(DISPOSITIONS.read_text()).get('tasks', {}).get(task)
    check(record and record.get('family') == family, f'contract: task-handoff-dispositions.json has no record for {task} '
                                                     f'in family {family}')


def children_of(stack, task_id):
    """Ledger rows whose parent or root is task_id (children published by the job itself)."""
    return [row[0] for row in stack.sql('SELECT task_id FROM public.assozeta_handoff_work WHERE task_id<>%s AND '
                                        '(parent_id=%s OR root_id=%s)', (task_id, task_id, task_id))]


def snapshot_models(*labels):
    """{model label: [row dicts ordered by pk]} through the base manager (deleted rows included)."""
    from django.apps import apps
    return {label: list(apps.get_model(label)._base_manager.order_by('pk').values()) for label in labels}


def require_matrix_prerequisites(stack):
    stack.require_handoff_ledger()
    check(shutil.which('go') is not None, 'Go toolchain required')


def now():
    return datetime.now(timezone.utc)
