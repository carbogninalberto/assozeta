"""Matrix family collectors: auto_archive_subscription, clear_deleted_subscription,
auto_delete_orphan_medical_certificate, delete_users_with_request.

task-coverage.md: check collector behaviour, protected rows, files and follow-up email; switch at a
page/batch boundary and prove no missed rows or duplicated external work.

Fixtures reuse the Go worker adapters' scope controls (NightlyFixture, OrphanFixture,
DeletionFixture in TESTS/adapters/go/*_worker_process.py). Every case first runs the actual
Django task body in-process on a fresh seed -- the uninterrupted control -- including the email
children it publishes (executed in-process, so their SMTP sends are counted), then reseeds the
identical state for the runtime under test. Verification by identity:
  - every managed row of the task's tables equals the control (timestamps written during the run
    normalized to 'run', ids of rows created during the run to 'new');
  - the private MinIO bucket listing (keys and contents) equals the control (files deleted once,
    protected ones kept, the unrelated sentinel untouched);
  - the SMTP messages accepted by the counting server (recipients + subject) equal the control:
    a follow-up email is sent exactly once, never twice across a switch.

Scenarios: standard four boundaries per task, plus
  <task>__batch_boundary_forward / __batch_boundary_reverse
    (clear_deleted_subscription, delete_users_with_request): the source executor is killed at the
    first committed unit of the run; the switch releases its attempt (P3-B settle rules) and the
    target runs the job: final rows, files and SMTP sends equal the control -- no row missed, no
    file or email processed twice.

SEAMS the implementation must provide (both runtimes, test builds/settings only):
  barrier 'batch:committed', key '<task name>-<n>': after the n-th committed unit of one run
    (one subscription / user / certificate block, counted from 1), before the next unit starts.
    Go: -tags handoff + ASSOZETA_HANDOFF_BARRIER_DIR; Django: core.handoff.barrier.
  Storage: the matrix stack's MinIO (P4MatrixStack 'storage'), S3 in both runtimes (p4_django_settings
    switches Django workers to S3Boto3Storage when the stack runs MinIO).
  The stack's Go env file shares SECRET_KEY with Django (aligned here); sender and brand come from the
    kit (EMAIL_HOST_USER / WHITELABEL_NAME).
"""
import base64
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
import email
import email.header
import json
import re
import uuid

from TESTS.handoff.p3_stack import ContractFailure, check, wait_for
from TESTS.handoff.p4_matrix_kit import (Case, TERMINAL, await_done, exclusive_go, identity, now, produce_django,
                                          produce_go_job, require_matrix_prerequisites, require_record, standard)

FAMILY = 'collectors'
EMAIL_TASKS = ('core.tasks.send_mail_async', 'send_email_text', 'send_email_template', 'send_email_html')
SYSTEM_PREFIXES = ('contenttypes.', 'auth.', 'sessions.', 'admin.', 'django_celery_beat.', 'django_celery_results.')
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\+00:00|Z)?')
# Audit request/correlation metadata is not a business effect (a worker sets a cid, an in-process control none).
VOLATILE = ('cid', 'remote_addr', 'remote_port')
UUIDS = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


# == shared control kit (also used by p4_family_retention / p4_family_payments) ========================
def clean_business(stack):
    """TRUNCATE every managed business table so adapter fixtures can recreate their fixed keys."""
    from django.apps import apps
    from django.db import connection
    tables = sorted({m._meta.db_table for m in apps.get_models(include_auto_created=True)
                     if m._meta.managed and not m._meta.proxy and not m._meta.label.startswith(SYSTEM_PREFIXES)})
    with connection.cursor() as cursor:
        cursor.execute('TRUNCATE ' + ', '.join(f'"{t}"' for t in tables) + ' CASCADE')


def scrub(stack):
    """Forget everything the in-process control published or recorded (it ran outside any worker)."""
    stack.redis.flushall()
    tables = [t for t in ('assozeta_handoff_effects', 'assozeta_handoff_attempts', 'assozeta_handoff_transfers',
                          'assozeta_handoff_work') if stack.exists('public.' + t)]
    stack.sql('TRUNCATE ' + ', '.join(['public.assozeta_go_jobs', 'django_celery_results_taskresult', 'handoff_test_effects',
                                       *('public.' + t for t in tables)]) + ' CASCADE')
    stack.smtp.reset()


@contextmanager
def sender_settings():
    """The in-process control runs on the same settings module as the Django workers
    (p4_django_settings: team sender, brand), so no override is needed; kept as the seam."""
    yield


def align_environment(stack, **extra):
    """Per-scenario deployment values: the Django workers' environment (stack.env, restored by the kit
    between scenarios) and the Go env file (rewritten by every scenario that needs them). SECRET_KEY is
    shared with Go because pairing secrets are encrypted with it."""
    from django.conf import settings
    # core.settings builds DEFAULT_TEAM_EMAIL/DEFAULT_SUPPORT_EMAIL from these *environment* values at import
    # and modules copy them; the settings-level override alone leaves 'Il Team di X <None>' (TypeError in
    # sanitize_address, the notice retries every 180 s). Same sender as the kit's Go env.
    stack.env.update(EMAIL_HOST_USER='team@example.test', EMAIL_HOST_SUPPORT='support@example.test')
    stack.env.update({k: v for k, v in extra.items()})
    values = {'SECRET_KEY': settings.SECRET_KEY, **{k: v for k, v in extra.items() if k not in GO_UNSHARED}}
    path = stack.go_env_file()
    lines = [line for line in path.read_text().splitlines() if line.split('=', 1)[0] not in values]
    path.write_text('\n'.join(lines + [f'{k}={v}' for k, v in values.items()]) + '\n')


# Django-worker-only switches (test settings); the Go equivalent is named in the family docstring.
GO_UNSHARED = ('ASSOZETA_HANDOFF_DEVELOPMENT_HTTP',)


def storage(stack, module=None):
    """The matrix stack's MinIO as the adapters' storage dict; points the adapter module's BUCKET at it."""
    check(stack.storage_endpoint, f'the {FAMILY}/retention matrix stack needs private MinIO (family needs "storage")')
    import boto3
    client = boto3.client('s3', endpoint_url=stack.storage_endpoint, aws_access_key_id=stack.storage_access,
                          aws_secret_access_key=stack.storage_secret, region_name='us-east-1')
    if module is not None:
        module.BUCKET = stack.storage_bucket
    return {'client': client, 'access': stack.storage_access, 'secret': stack.storage_secret, 'endpoint': stack.storage_endpoint}


def s3_settings(stack, **extra):
    from django.test import override_settings
    storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
    return override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=stack.storage_access,
                             AWS_SECRET_ACCESS_KEY=stack.storage_secret, AWS_STORAGE_BUCKET_NAME=stack.storage_bucket,
                             AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=stack.storage_endpoint,
                             AWS_LOCATION='private', AWS_S3_ADDRESSING_STYLE='path', **extra)


def listing(stack):
    client = storage(stack)['client']
    objects = {}
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=stack.storage_bucket):
        for item in page.get('Contents', []):
            objects[item['Key']] = client.get_object(Bucket=stack.storage_bucket, Key=item['Key'])['Body'].read()
    return objects


def model_state(labels):
    from django.apps import apps
    return {label: list(apps.get_model(label)._base_manager.order_by('pk').values()) for label in labels}


def normal_state(before, after, since, until):
    """What the run did, by identity: per table the removed seeded keys, the rows it added and the
    fields it changed. Run-window timestamps -> 'run'; ids of rows created by the run -> 'new'.
    (A delta, so seed values computed from the wall clock never make two seeds differ.)"""
    from django.apps import apps
    created = set()
    for label, rows in after.items():
        pk = apps.get_model(label)._meta.pk.attname
        seeded = {str(row[pk]) for row in before.get(label, [])}
        created |= {str(row[pk]) for row in rows if str(row[pk]) not in seeded}

    def text(value):
        value = UUIDS.sub(lambda m: 'new' if m[0] in created else m[0], value)

        def stamp(match):
            with suppress(ValueError):
                moment = datetime.fromisoformat(match[0].replace('Z', '+00:00').replace(' ', 'T'))
                moment = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)  # audit text is naive UTC
                if since <= moment <= until:
                    return 'run'
            return match[0]
        # An object's repr may embed a process address (CourseSubscription's str() is its __dict__).
        value = re.sub(r'object at 0x[0-9a-f]+', 'object at ADDRESS', value)
        return STAMP.sub(stamp, value)

    def value(key, item):
        if isinstance(item, datetime):
            return 'run' if item.tzinfo and since <= item <= until else item.isoformat()
        if isinstance(item, (dict, list)):
            return text(json.dumps(item, sort_keys=True, default=str))
        if isinstance(item, (str, uuid.UUID)):
            return text(str(item))
        # Integer foreign keys are compared as is (content_type_id 34 is not a created row); created
        # integer ids are rewritten only where the key names a created row (pk, audit references).
        if key in ('id', 'object_pk', 'log_entry_id') and str(item) in created:
            return 'new'
        return item if item is None or isinstance(item, (int, float, bool)) else str(item)

    def key(row):
        return json.dumps(row, sort_keys=True, default=str)
    delta = {}
    for label, rows in after.items():
        pk = apps.get_model(label)._meta.pk.attname
        old = {str(row[pk]): row for row in before.get(label, [])}
        new = {str(row[pk]): row for row in rows}
        delta[label] = {
            'removed': sorted(k for k in old if k not in new),
            'added': sorted(({f: value(f, v) for f, v in row.items() if f not in VOLATILE}
                             for k, row in new.items() if k not in old), key=key),
            'changed': sorted(((k, {f: value(f, v) for f, v in row.items() if f not in VOLATILE and old[k].get(f) != v})
                               for k, row in new.items() if k in old and old[k] != row), key=key)}
        delta[label]['changed'] = [item for item in delta[label]['changed'] if item[1]]
    return delta


def describe(expected, actual, labels, runtime):
    """Compact by-identity difference: per table and part, items only in the control / only in the run."""
    out = {}
    for label in labels:
        for part in ('removed', 'added', 'changed'):
            control = [json.dumps(i, sort_keys=True, default=str) for i in expected[label][part]]
            observed = [json.dumps(i, sort_keys=True, default=str) for i in actual[label][part]]
            only_control = [i for i in control if i not in observed]
            only_run = [i for i in observed if i not in control]
            if only_control or only_run:
                out[f'{label}.{part}'] = {'control_only': [i[:300] for i in only_control[:2]], 'count_control': len(control),
                                          f'{runtime}_only': [i[:300] for i in only_run[:2]], f'count_{runtime}': len(observed)}
                if only_control and only_run:
                    a, b = only_control[0], only_run[0]
                    at = next((n for n, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                    out[f'{label}.{part}']['first_difference'] = [a[max(0, at - 120):at + 80], b[max(0, at - 120):at + 80]]
    return json.dumps(out, ensure_ascii=False)[:1800]


def broker_envelopes(stack):
    """Every envelope currently in a Redis list (any queue), decoded: (key, headers, args, kwargs)."""
    found = []
    for key in stack.redis.keys('*'):
        if stack.redis.type(key) != b'list':
            continue
        for raw in stack.redis.lrange(key, 0, -1):
            with suppress(ValueError, KeyError, TypeError):
                message = json.loads(raw)
                args, kwargs, _ = json.loads(base64.b64decode(message['body']))
                found.append((key.decode(), message['headers'], args, kwargs))
    return found


SEND_POSITIONS = {'send_email_text': (1, 2), 'send_email_html': (1, 2), 'send_email_template': (1, 2),
                  'core.tasks.send_mail_async': (0, 3)}  # (subject, recipient_list) positional indexes


def drain_control_children(stack):
    """Collect (and remove) the children the in-process control published: their task names, and the
    email each email child would send (recipients, subject) -- the control's downstream work. The
    children are not executed in-process; the runtime under test executes its own."""
    names, mails = [], []
    for _, headers, args, kwargs in broker_envelopes(stack):
        names.append(headers['task'])
        if headers['task'] in SEND_POSITIONS:
            subject_at, recipients_at = SEND_POSITIONS[headers['task']]
            subject = kwargs.get('subject', args[subject_at] if len(args) > subject_at else '')
            recipients = kwargs.get('recipient_list', args[recipients_at] if len(args) > recipients_at else [])
            mails.append((tuple(sorted(str(r).strip().lower() for r in recipients)), subject))
    stack.redis.flushdb()
    stack.control_mails = sorted(mails)
    return sorted(names)


def smtp_messages(stack):
    """(sorted recipients, subject) of every message the counting SMTP server accepted."""
    result = []
    for body in stack.smtp.messages():
        message = email.message_from_string(body)
        recipients = sorted(address.strip().lower() for field in ('To', 'Cc', 'Bcc') for address in
                            (message.get(field) or '').split(',') if address.strip())
        subject = str(email.header.make_header(email.header.decode_header(message.get('Subject') or '')))
        result.append((tuple(recipients), subject))
    return sorted(result)


def wait_quiet(stack, produced, timeout=60):
    """Every ledger row (parents and the children they published) reached a terminal state."""
    def quiet():
        rows = stack.sql('SELECT task_id, state FROM public.assozeta_handoff_work')
        return rows if rows and all(state in TERMINAL for _, state in rows) and set(produced) <= {t for t, _ in rows} else None
    return wait_for(quiet, f'jobs never settled: {stack.sql("SELECT task_name, state FROM public.assozeta_handoff_work")}',
                    timeout=timeout)


class Controlled:
    """A Case whose expectation is the in-process Django control on the same seed.

    make(stack) builds the adapter fixture once per scenario (one fixture clock, as the adapters do);
    reseed(stack, fixture) lays out the identical seed (called for the control and again for the
    runtime under test); control(stack, fixture) runs the Django task body in-process (with its
    settings overrides); externals: observed provider sides ('storage', 'smtp', callables via extra)."""

    def __init__(self, task, family, labels, make, reseed, control, go_kind=None, externals=(), extra=None, timeout=90,
                 random_fields=None):
        self.task, self.family, self.labels, self.control = task, family, labels, control
        # {label: {field: compiled regex}}: values the task draws at random (a coupon code) are compared by shape.
        self.random_fields = random_fields or {}
        self.make, self.reseed = make, reseed
        self.go_kind, self.externals, self.extra, self.timeout = go_kind or task, externals, extra or {}, timeout

    def masked(self, delta):
        for label, fields in self.random_fields.items():
            def mask(row):
                return {f: (f'<{f}:shape-ok>' if f in fields and isinstance(v, str) and fields[f].fullmatch(v) else v)
                        for f, v in row.items()}
            part = delta[label]
            part['added'] = [mask(row) for row in part['added']]
            part['changed'] = [[key, mask(row)] for key, row in part['changed']]
        return delta

    def observe(self, stack):
        observed = {}
        if 'storage' in self.externals:
            observed['storage'] = listing(stack)
        if 'smtp' in self.externals:
            observed['smtp'] = smtp_messages(stack)
        for name, probe in self.extra.items():
            observed[name] = probe(stack)
        return observed

    def seed(self, stack):
        clean_business(stack)
        scrub(stack)
        fixture = self.make(stack)
        self.reseed(stack, fixture)
        before = model_state(self.labels)
        since = now()
        with django_owned(stack), sender_settings():
            self.control(stack, fixture)
            children = drain_control_children(stack)
        expected = self.masked(normal_state(before, model_state(self.labels), since, now()))
        expected_external = self.observe(stack)
        if 'smtp' in expected_external:
            expected_external['smtp'] = sorted(expected_external['smtp'] + stack.control_mails)
        scrub(stack)
        self.reseed(stack, fixture)
        return {'fixture': fixture, 'before': model_state(self.labels), 'since': now(), 'expected': expected,
                'external': expected_external, 'children': children, 'produced': []}

    def verify(self, stack, context, runtime):
        wait_quiet(stack, context['produced'], self.timeout)
        actual = self.masked(normal_state(context['before'], model_state(self.labels), context['since'], now()))
        differences = sorted(label for label in self.labels if actual[label] != context['expected'][label])
        check(not differences, f'{runtime}: {self.task} rows differ from the uninterrupted Django control in {differences}: '
              + describe(context['expected'], actual, differences[:2], runtime))
        observed = self.observe(stack)
        for name, value in context['external'].items():
            check(observed.get(name) == value, f'{runtime}: {self.task} {name} differs from the control: '
                  f'control {str(value)[:500]} / {runtime} {str(observed.get(name))[:500]}')
        published = sorted(row[0] for row in stack.sql('SELECT task_name FROM public.assozeta_handoff_work WHERE '
                                                        'NOT (task_id = ANY(%s))', (context['produced'],))
                           if row[0] not in EMAIL_TASKS)
        expected = [name for name in context['children'] if name not in EMAIL_TASKS]
        check(published == expected, f'{runtime}: {self.task} published downstream tasks {published}; the control '
                                     f'published {expected} (emails are compared at the SMTP server instead)')

    def children(self, stack, context):
        rows = stack.sql('SELECT task_id, state, owner_generation FROM public.assozeta_handoff_work WHERE NOT '
                         '(task_id = ANY(%s))', (context['produced'],))
        return {task: ('source_completed' if generation == 1 else 'target_completed') if state == 'completed'
                else f'terminal:{state}' for task, state, generation in rows}

    def producers(self):
        def django(stack, context):
            context['produced'] = [produce_django(stack, self.task)]
            return context['produced']

        def go(stack, context):
            context['produced'] = [produce_go_job(stack, self.go_kind)]
            return context['produced']
        return {'django': django, 'go': go}

    def case(self):
        return Case(self.task, self.seed, self.producers(), self.verify, children=self.children, timeout=self.timeout)


@contextmanager
def django_owned(stack):
    """The in-process control is a Django producer: run it while Django owns the authority (fixture
    only, like the stack reset), then restore the scenario's authority."""
    runtime, generation = stack.authority()
    stack.set_authority('django', 1)
    try:
        yield
    finally:
        stack.set_authority(runtime, generation)


def quiet_django(body):
    import contextlib
    import io
    import logging

    @contextmanager
    def silenced():
        logging.disable(logging.CRITICAL)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                yield
        finally:
            logging.disable(logging.NOTSET)
    with silenced():
        return body()


def await_silent(stack, runtime, grace=2.0):
    """Durable evidence that the runtime's stopped/killed consumers stopped heartbeating (older than the
    controller's heartbeat_grace), so close_admission treats them as gone rather than waiting for them."""
    if not stack.exists('public.assozeta_handoff_consumers'):
        return
    wait_for(lambda: not stack.sql("""SELECT count(*) FROM public.assozeta_handoff_consumers WHERE runtime=%s
        AND heartbeat_at >= clock_timestamp() - make_interval(secs => %s)""", (runtime, grace))[0][0],
             f'stopped {runtime} consumers kept heartbeating', timeout=30)


def switch_stopping(stack, source, generation, target):
    """switch() stopping the source executors inside it (after settle), as the kit requires."""
    processes, stack.processes[source] = list(stack.processes[source]), []
    return stack.switch(source, generation, target, stop=processes)


def kill_executors(stack, runtime, arrival):
    """SIGKILL the runtime's executors (and the barrier-reporting child) at a barrier."""
    for process in stack.processes[runtime]:
        stack.kill_tree(process, arrival)
    stack.processes[runtime] = []
    await_silent(stack, runtime)


def killed_at_batch(controlled, source, target, key_index=1):
    """Kill the source executor at batch:committed '<task>-<n>', switch, finish on the target."""
    case = controlled.case()

    def scenario(stack):
        require_matrix_prerequisites(stack)
        stack.set_authority(source, 1)
        context = case.seed(stack)
        key = f'{controlled.task}-{key_index}'
        stack.barrier.hold('batch:committed', key)
        stack.executors(source)
        produced = case.produce[source](stack, context)
        arrival = stack.barrier.wait_arrived('batch:committed', key, f'the {source} executor after the first committed '
                                             f'unit of {controlled.task} (contract: barrier batch:committed)', timeout=45)
        kill_executors(stack, source, arrival)
        stack.barrier.reset()
        generation = stack.switch(source, 1, target)
        stack.executors(target)
        for task_id in produced:
            check(await_done(stack, task_id, f'the {target} executor after the batch-boundary switch', 120) == 'completed',
                  f'{controlled.task}: the interrupted job completes on {target}')
        case.verify(stack, context, target)
        if target == 'go':
            exclusive_go(stack, produced)
        children = case.children(stack, context)
        identity(stack, {t: 'target_completed' for t in produced} | children, (source, 1), (target, generation),
                 {target: len(produced) + sum(1 for v in children.values() if v == 'target_completed'),
                  source: sum(1 for v in children.values() if v == 'source_completed')})
        require_record(controlled.task, controlled.family)
    return scenario


# == the four collectors ==================================================================================
def nightly(variant):
    def make(stack):
        from TESTS.adapters.go import nightly_subscriptions_worker_process as adapter
        align_environment(stack)
        return adapter.NightlyFixture(storage(stack, adapter))
    return make, lambda stack, fixture: fixture.seed(variant)


def nightly_control(name):
    def control(stack, fixture):
        from application import tasks
        with s3_settings(stack):
            quiet_django(getattr(tasks, name).run)
    return control


NIGHTLY_LABELS = ('application.Subscription', 'application.Payment', 'application.Invoice', 'application.SubscriptionToken',
                  'application.CourseSubscription', 'application.CourseSubscriptionInstallment', 'application.Associate',
                  'auditlog.LogEntry', 'application.AuditLogIndex')
ARCHIVE = Controlled('auto_archive_subscription', FAMILY, NIGHTLY_LABELS, *nightly('archive'),
                     nightly_control('auto_archive_subscription'), externals=('storage', 'smtp'))
CLEAR = Controlled('clear_deleted_subscription', FAMILY, NIGHTLY_LABELS, *nightly('clear'),
                   nightly_control('clear_deleted_subscription'), externals=('storage', 'smtp'))


def orphan_make(stack):
    from TESTS.adapters.go import orphan_medical_worker_process as adapter
    align_environment(stack)
    return adapter.OrphanFixture(storage(stack, adapter))


def orphan_control(stack, fixture):
    from application import tasks
    with s3_settings(stack):
        quiet_django(tasks.auto_delete_orphan_medical_certificate.run)


ORPHAN = Controlled('auto_delete_orphan_medical_certificate', FAMILY,
                    ('application.MedicalCertificate', 'docmanager.Document', 'application.Subscription',
                     'auditlog.LogEntry', 'application.AuditLogIndex'),
                    orphan_make, lambda stack, fixture: fixture.seed('main'), orphan_control, externals=('storage',))


def deletion_make(stack):
    from TESTS.adapters.go import user_deletion_worker_process as adapter
    align_environment(stack)
    return adapter.DeletionFixture()


def deletion_seed(stack, fixture):
    from application.models import User
    fixture.seed()
    # The control without an address ('Nobody') retries its notice every 180 s on both runtimes; keep
    # it out of the due set so the follow-up emails settle inside the scenario.
    User.original_objects.filter(pk=fixture.users[7]).update(delete_on=None)


def deletion_control(stack, fixture):
    from application import tasks
    quiet_django(tasks.delete_users_with_request.run)


DELETION = Controlled('delete_users_with_request', FAMILY,
                      ('application.User', 'application.SportAssociation', 'auditlog.LogEntry', 'application.AuditLogIndex'),
                      deletion_make, deletion_seed, deletion_control, externals=('smtp',))


SCENARIOS = {}
for _controlled in (ARCHIVE, CLEAR, ORPHAN, DELETION):
    SCENARIOS.update(standard(_controlled.case(), FAMILY))
for _controlled in (CLEAR, DELETION):
    _slug = _controlled.task
    SCENARIOS[f'{_slug}__batch_boundary_forward'] = killed_at_batch(_controlled, 'django', 'go')
    SCENARIOS[f'{_slug}__batch_boundary_reverse'] = killed_at_batch(_controlled, 'go', 'django')
