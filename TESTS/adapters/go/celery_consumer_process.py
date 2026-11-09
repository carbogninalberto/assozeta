"""Differential harness for the Go Celery consumer (first task: core.tasks.send_mail_async).

Private PostgreSQL (actual Django current schema), a private Redis broker
(DB 0, password) and a local fake SMTP server that records every client byte.
Each scenario builds one message with Django's own producer
(``send_mail_async.apply_async`` routed by ``GO_TASK_QUEUE_TASKS`` to the
``assozeta-go`` queue) and then:

1. pushes the identical bytes to a real worker of the Django Celery app
   (celery.contrib.testing worker, solo pool, ``-Q assozeta-go``, django-db
   result backend, CoordinatedTask, the production EmailBackend);
2. resets the same seed and pushes the identical bytes to the native Go
   consumer (``cmd/celery-consumer``, race-built).

After each worker has stopped (warm shutdown restores held messages), the
harness compares: the SMTP transcripts, the complete managed-model state
(TaskResult and EmailLog rows included; timestamps checked against the run
window), and the broker state (every key: queues with decoded messages,
unacked hash and index, bindings). Random per-publish values (delivery tag,
origin, Message-ID, MIME boundary, EHLO name) are normalized; the retry
message's id, retries, root/parent ids, stamp, eta and body are compared.
Authored assertions check that the Django oracle took the intended path.
Handoff scenarios (inputs outside the Go subset) require Go to move the
message unchanged to Django's ``celery`` queue, and a Django worker on that
queue must then reach the same final state as the Django-only run.
"""
import base64
import hashlib
import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import FakeSMTP, SMTP_PASSWORD, tls_context, free_port

DATABASE = 'assozeta_contract_celery_consumer'
REDIS_PASSWORD = 'private-redis-password-canary'
BRAND = 'Assozeta Fixture'
HOST_USER = 'noreply@example.test'
TASK = 'core.tasks.send_mail_async'
GO_QUEUE = 'assozeta-go'
DJANGO_QUEUE = 'celery'
OUTPUT = ROOT / 'quality-reports/go-migration/celery-consumer-process'
# Read before initialize_current_schema replaces the process environment.
DJANGO_ONLY = bool(os.environ.get('CELERY_CONSUMER_DJANGO_ONLY'))
MESSAGE_ID = re.compile(r'Message-ID: <[^>\r\n]+>')
DATE = re.compile(r'Date: [A-Z][a-z]{2}, \d\d [A-Z][a-z]{2} \d{4} \d\d:\d\d:\d\d -0000')
SIZE = re.compile(r' size=(\d+)\r\n\Z')


def U(n):
    return UUID(int=0xC5000000 + n)


ASSOCIATION, CONFIGURED, DELETED, OWNER = U(11), U(12), U(13), U(1)


class Scenario:
    def __init__(self, name, kwargs, *, retries=None, countdown=None, expires=None, smtp='ok', setup=None,
                 done='result', handoff=False, expect=None, unacked_seed=False):
        self.name, self.kwargs, self.retries, self.countdown, self.expires = name, kwargs, retries, countdown, expires
        self.smtp, self.setup, self.done, self.handoff, self.expect, self.unacked_seed = smtp, setup, done, handoff, expect, unacked_seed


def base_kwargs(**extra):
    kwargs = {'subject': 'Conferma iscrizione', 'message': 'Ciao,\nla tua iscrizione è confermata.\n',
              'from_email': f'{BRAND} <{HOST_USER}>', 'recipient_list': ['socio@example.test']}
    kwargs.update(extra)
    return kwargs


SCENARIOS = [
    Scenario('plain text without association', base_kwargs(),
             expect={'status': 'SUCCESS', 'emaillog': [('socio@example.test', 'Conferma iscrizione', 'Sent', None)], 'mails': 1}),
    Scenario('html alternative, reply-to, two recipients, association without configuration',
             base_kwargs(subject='Promemoria: quota già scaduta', message='Testo semplice',
                         from_email=f'Il Team di {BRAND} <{HOST_USER}>', recipient_list=['uno@example.test', 'due@example.test'],
                         html_message='<p>Quota <b>già</b> scaduta</p>', reply_to=['owner@example.test'],
                         sport_association_id=ASSOCIATION, fail_silently=False),
             expect={'status': 'SUCCESS', 'emaillog': [('uno@example.test', 'Promemoria: quota già scaduta', 'Sent', ASSOCIATION)], 'mails': 1}),
    Scenario('skip_association_check with a configuration uses the system backend',
             base_kwargs(message=None, html_message='<p>ok</p>', sport_association_id=CONFIGURED, reply_to=None,
                         skip_association_check=True, from_email=f'Società Tre <{HOST_USER}>'),
             expect={'status': 'SUCCESS', 'emaillog': [('socio@example.test', 'Conferma iscrizione', 'Sent', CONFIGURED)], 'mails': 1}),
    Scenario('deleted association resolves to none', base_kwargs(sport_association_id=DELETED),
             expect={'status': 'SUCCESS', 'emaillog': [('socio@example.test', 'Conferma iscrizione', 'Sent', None)], 'mails': 1}),
    Scenario('SMTP authentication failure retries with the same id after 180 s', base_kwargs(), smtp='auth', done='held',
             expect={'status': 'RETRY', 'exc': 'SMTPAuthenticationError', 'emaillog': [], 'retry': (1, 180)}),
    Scenario('connection refused on the second attempt retries after 360 s', base_kwargs(), retries=1, smtp='refused', done='held',
             expect={'status': 'RETRY', 'exc': 'ConnectionRefusedError', 'emaillog': [], 'retry': (2, 360)}),
    Scenario('failure after the last retry stores the SMTP exception', base_kwargs(), retries=3, smtp='auth',
             expect={'status': 'FAILURE', 'exc': 'SMTPAuthenticationError', 'emaillog': []}),
    Scenario('a restore completed after the publish stamp refuses the task', base_kwargs(), setup='restore-completed',
             expect={'status': 'FAILURE', 'exc': 'StaleDataTask', 'emaillog': [], 'mails': 0}),
    Scenario('a pending restore defers the task by 15 s without spending a retry', base_kwargs(), setup='restore-pending', done='held',
             expect={'status': 'RETRY', 'exc': 'Busy', 'emaillog': [], 'retry': (0, 15), 'mails': 0}),
    Scenario('a future eta is held unacknowledged and restored at shutdown', base_kwargs(), countdown=3600, done='held',
             expect={'status': None, 'emaillog': [], 'mails': 0}),
    Scenario('an expired message is revoked', base_kwargs(), expires=-60,
             expect={'status': 'REVOKED', 'exc': 'TaskRevokedError', 'emaillog': [], 'mails': 0}),
    Scenario('an unacknowledged message past the visibility timeout is restored and executed', base_kwargs(), unacked_seed=True,
             expect={'status': 'SUCCESS', 'emaillog': [('socio@example.test', 'Conferma iscrizione', 'Sent', None)], 'mails': 1}),
    # CommunicationConfiguration.send_email and fail_silently run in Go since consumer-email
    # (celery_consumer_email_process.py covers their other branches).
    Scenario('association with a configuration without SMTP publishes a child the consumer executes',
             base_kwargs(sport_association_id=CONFIGURED, html_message='<p>via associazione</p>'),
             expect={'rows': 2, 'status': 'SUCCESS', 'emaillog': [('socio@example.test', 'Conferma iscrizione', 'Sent', CONFIGURED)] * 2,
                     'mails': 1}),
    Scenario('fail_silently=True with a refused connection logs Sent', base_kwargs(fail_silently=True), smtp='refused',
             expect={'status': 'SUCCESS', 'emaillog': [('socio@example.test', 'Conferma iscrizione', 'Sent', None)], 'mails': 0}),
    Scenario('an unknown keyword argument is handed off to the Django queue', base_kwargs(auth_user=None), handoff=True,
             expect={'status': 'SUCCESS', 'emaillog': [('socio@example.test', 'Conferma iscrizione', 'Sent', None)], 'mails': 1}),
]


def normalize_unit(unit):
    unit = MESSAGE_ID.sub('Message-ID: <message-id>', DATE.sub('Date: <date>', unit))
    return unit


def normalize_transcript(units):
    """Normalize the random parts. MAIL FROM's SIZE follows the message length, which the
    random Message-ID changes; the DATA bytes themselves are compared."""
    out = []
    for unit in units:
        if unit.lower().startswith('mail from:'):
            unit = SIZE.sub(' size=<n>\r\n', unit)
        out.append(normalize_unit(unit))
    return out


class Stack:
    def __init__(self, work, environment, redis_port, smtp):
        import redis
        self.work, self.env, self.redis_port, self.smtp = work, environment, redis_port, smtp
        self.broker = redis.Redis(host='127.0.0.1', port=redis_port, db=0, password=REDIS_PASSWORD, socket_timeout=10)
        self.closed_port = free_port()
        self.children = []

    # -- database -----------------------------------------------------------------------------
    def seed(self, scenario, stamp=None):
        from django.db import connection
        from django.utils import timezone as dj_timezone
        from application.models import User, SportAssociation
        from communications.models import CommunicationConfiguration
        from instance.models import DataRestore
        started = datetime.now(timezone.utc)
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, application_emaillog,
              django_celery_results_taskresult, communications_communicationconfiguration, instance_datarestore,
              application_sportassociation, instance_instanceconfiguration, bakney_user RESTART IDENTITY CASCADE''')
        User.original_objects.bulk_create([User(user_id=U(n), username=f'consumer-owner-{n}', role=1, password='!private-fixture',
                                                date_joined=started, email=f'owner{n}@example.test') for n in (1, 2, 3)])
        SportAssociation.original_objects.bulk_create([
            SportAssociation(sport_association_id=ASSOCIATION, user_id=OWNER, denomination='ASD Uno', tax_code='00000000000'),
            SportAssociation(sport_association_id=CONFIGURED, user_id=U(2), denomination='Società Tre', tax_code='00000000000'),
            SportAssociation(sport_association_id=DELETED, user_id=U(3), denomination='Cancellata', tax_code='00000000000',
                             deleted=True)])
        CommunicationConfiguration.objects.bulk_create([CommunicationConfiguration(communication_configuration_id=U(31),
            sport_association_id=CONFIGURED, email_smtp_host=None, email_smtp_port=None, email_smtp_user=None,
            email_smtp_password=None, email_sender_name='Tre', email_encryption='NONE', daily_email_limit=1000, daily_email_balance=0)])
        if scenario.setup == 'restore-pending':
            DataRestore._base_manager.bulk_create([DataRestore(id=U(50), owner_id=OWNER, association_id=ASSOCIATION, state='running',
                                                                sha256='0' * 64)])
        if scenario.setup == 'restore-completed':
            # Completed just after the message's publish stamp.
            DataRestore._base_manager.bulk_create([DataRestore(id=U(51), owner_id=OWNER, association_id=ASSOCIATION,
                state='completed', completed_at=datetime.fromtimestamp(stamp + 0.001, timezone.utc), sha256='0' * 64)])
        self.seeding = (started, datetime.now(timezone.utc))
        return dj_timezone

    def snapshot(self):
        from django.apps import apps
        since, until = self.seeding
        def value(item):
            return 'seed-default' if isinstance(item, datetime) and since <= item <= until else item
        return {m._meta.label: [{k: value(v) for k, v in row.items()} for row in m._base_manager.order_by('pk').values()]
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    # -- broker -------------------------------------------------------------------------------
    def broker_state(self):
        state = {}
        for key in sorted(self.broker.keys('*')):
            kind = self.broker.type(key).decode()
            name = key.decode('utf-8', 'backslashreplace')
            if kind == 'list':
                state[name] = ['list', [item.decode() for item in self.broker.lrange(key, 0, -1)]]
            elif kind == 'set':
                state[name] = ['set', sorted(item.decode('utf-8', 'backslashreplace') for item in self.broker.smembers(key))]
            elif kind == 'hash':
                state[name] = ['hash', {k.decode(): v.decode() for k, v in self.broker.hgetall(key).items()}]
            elif kind == 'zset':
                state[name] = ['zset', [(m.decode(), s) for m, s in self.broker.zrange(key, 0, -1, withscores=True)]]
            else:
                state[name] = [kind, None]
        return state

    def publish(self, scenario):
        """Django's producer, with the production routing for GO_TASK_QUEUE_TASKS=core.tasks.send_mail_async."""
        from core.tasks import send_mail_async
        options = {'kwargs': scenario.kwargs}
        if scenario.retries is not None:
            options['retries'] = scenario.retries
        if scenario.countdown is not None:
            options['countdown'] = scenario.countdown
        if scenario.expires is not None:
            options['expires'] = datetime.now(timezone.utc) + timedelta(seconds=scenario.expires)
        self.broker.flushdb()
        send_mail_async.apply_async(**options)
        raw = self.broker.rpop(GO_QUEUE)
        assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
        return raw.decode()

    def push(self, scenario, payload):
        self.broker.flushdb()
        if scenario.unacked_seed:
            message = json.loads(payload)
            tag = message['properties']['delivery_tag']
            # kombu's QoS.append, written by a consumer that disappeared 4000 s ago.
            from kombu.utils.json import dumps
            self.broker.hset('unacked', tag, dumps([message, '', GO_QUEUE]))
            self.broker.zadd('unacked_index', {tag: time.time() - 4000})
            self.broker.sadd('_kombu.binding.' + GO_QUEUE, f'{GO_QUEUE}\x06\x16\x06\x16{GO_QUEUE}')
        else:
            self.broker.lpush(GO_QUEUE, payload)

    def quiescent(self, scenario, task_id, held=None):
        """Queues empty, the expected number of held messages, and every stored result final
        (the scenario's own row present unless the scenario expects none)."""
        from django_celery_results.models import TaskResult
        if self.broker.llen(GO_QUEUE) or self.broker.llen(DJANGO_QUEUE):
            return False
        if held is None:
            held = 1 if scenario.done == 'held' else 0
        if self.broker.hlen('unacked') != held:
            return False
        rows = {row.task_id: row.status for row in TaskResult.objects.all()}
        own = task_id in rows
        if scenario.expect['status'] is None:
            return not own
        final = ('SUCCESS', 'FAILURE', 'REVOKED', 'RETRY') if held else ('SUCCESS', 'FAILURE', 'REVOKED')
        return own and all(status in final for status in rows.values())

    # -- environment --------------------------------------------------------------------------
    def smtp_settings(self, scenario):
        port = self.closed_port if scenario.smtp == 'refused' else self.smtp.port
        password = 'wrong-password' if scenario.smtp == 'auth' else SMTP_PASSWORD
        return {'EMAIL_HOST': '127.0.0.1', 'EMAIL_PORT': str(port), 'EMAIL_HOST_USER': HOST_USER, 'EMAIL_HOST_PASSWORD': password,
                'EMAIL_USE_TLS': False, 'EMAIL_USE_SSL': False}


class Harness:
    def __init__(self, stack, binary):
        self.stack, self.binary = stack, binary
        self.completed, self.observations = [], {}

    # -- Django worker ------------------------------------------------------------------------
    def django_context(self, scenario, queues):
        from contextlib import ExitStack
        from django.test import override_settings
        from core.celery import app
        from core.task_routing import go_task_routes
        from TESTS.adapters.django.family_harness import _reset_celery_app
        stack = ExitStack()
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES', 'CELERY_RESULT_BACKEND')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.stack.redis_port}', DJANGO_QUEUE,
                                        go_task_routes(TASK), 'django-db'))))
        _reset_celery_app(app)
        def restore():
            app.conf.update(saved)
            _reset_celery_app(app)
        stack.callback(restore)
        smtp = self.stack.smtp_settings(scenario)
        smtp['EMAIL_PORT'] = int(smtp['EMAIL_PORT'])
        from unittest.mock import patch
        from core import settings as core_settings
        for name, value in (('WHITELABEL_NAME', BRAND), ('DEFAULT_FROM_EMAIL', f'{BRAND} <{HOST_USER}>')):
            stack.enter_context(patch.object(core_settings, name, value))
        stack.enter_context(override_settings(EMAIL_BACKEND='instance.email_configuration.EmailBackend',
            DATA_RESTORE_LOCK_PATH=str(self.stack.work / 'operations.lock'), WHITELABEL_NAME=BRAND,
            DEFAULT_FROM_EMAIL=f'{BRAND} <{HOST_USER}>', **smtp))
        return stack, app

    def run_django(self, scenario, payload, queues=(GO_QUEUE,), push=True, wait=None):
        import io
        from contextlib import redirect_stdout
        from celery.contrib.testing.worker import start_worker
        message = json.loads(payload)
        task_id = message['headers']['id']
        stack, app = self.django_context(scenario, queues)
        with stack:
            if push:
                self.stack.push(scenario, payload)
            with redirect_stdout(io.StringIO()), start_worker(app, pool='solo', concurrency=1, perform_ping_check=False,
                                                              loglevel='WARNING', shutdown_timeout=30, queues=list(queues)):
                self.wait(wait or (lambda: self.stack.quiescent(scenario, task_id)), 'Django worker: ' + scenario.name)
                time.sleep(0.3)
        # A connection Django's SMTP backend leaves open after a failed login is
        # closed when the object is collected; collect it so the server records it now.
        import gc
        gc.collect()
        time.sleep(0.5)
        return task_id

    # -- Go consumer --------------------------------------------------------------------------
    def env_file(self, scenario):
        smtp = self.stack.smtp_settings(scenario)
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(self.stack.work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000',
            AWS_LOCATION='private', REDIS_HOST='127.0.0.1', REDIS_PORT=str(self.stack.redis_port), REDIS_USERNAME='default',
            REDIS_PASSWORD=REDIS_PASSWORD, WHITELABEL_NAME=BRAND, IS_WHITELABEL='False', GO_TASK_QUEUE_TASKS=TASK,
            DATA_RESTORE_LOCK_PATH=str(self.stack.work / 'operations.lock'),
            **{k: (str(v) if not isinstance(v, bool) else str(v)) for k, v in smtp.items()})
        path = self.stack.work / 'consumer.env'
        path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        path.chmod(0o600)
        return path

    def run_go(self, scenario, payload, push=True, handoff=None):
        message = json.loads(payload)
        task_id = message['headers']['id']
        handoff = scenario.handoff if handoff is None else handoff
        if push:
            self.stack.push(scenario, payload)
        log = OUTPUT / f'consumer-{len(self.stack.children) + 1}.log'
        stream = log.open('w')
        process = subprocess.Popen([str(self.binary), '--env-file', str(self.env_file(scenario)), '--concurrency', '1'],
                                   env=self.stack.env, stdout=stream, stderr=subprocess.STDOUT)
        self.stack.children.append((process, stream, log))
        try:
            if handoff:
                self.wait(lambda: self.stack.broker.llen(GO_QUEUE) == 0 and self.stack.broker.hlen('unacked') == 0
                          and self.stack.broker.llen(DJANGO_QUEUE) == 1 or process.poll() is not None, 'Go handoff: ' + scenario.name)
            else:
                self.wait(lambda: self.stack.quiescent(scenario, task_id) or process.poll() is not None, 'Go consumer: ' + scenario.name)
            assert process.poll() is None, 'Go consumer exited: ' + log.read_text()[-2000:]
            time.sleep(0.3)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGTERM)
            code = process.wait(timeout=60)
            stream.close()
        text = log.read_text()
        assert 'WARNING: DATA RACE' not in text, 'Go race detector: ' + scenario.name
        for private in (REDIS_PASSWORD, SMTP_PASSWORD, 'private-worker-password-canary'):
            assert private not in text, 'Go consumer leaked private configuration'
        assert code == 0, f'Go consumer exit status {code}: {text[-2000:]}'
        return task_id

    @staticmethod
    def wait(predicate, description, timeout=45):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError('Timed out: ' + description)

    # -- observation --------------------------------------------------------------------------
    def observe(self, scenario, payload, since, until):
        stack = self.stack
        state = stack.snapshot()
        original = json.loads(payload)
        rows = state.pop('django_celery_results.TaskResult')
        results, reference = [], None
        for row in rows:
            row = dict(row)
            reference = row['date_done']
            for field in ('date_created', 'date_done'):
                assert since <= row[field] <= until, f'{field} outside the run window'
                row[field] = 'run'
            row.pop('id')
            if row['task_id'] != original['headers']['id']:
                UUID(row['task_id'])
                row['task_id'] = '<child-task-id>'  # published by the task with a fresh uuid4
            meta = json.loads(row['meta'])
            if any(child != original['headers']['id'] for (child, _), _ in meta['children']):
                meta['children'] = [[[child if child == original['headers']['id'] else '<child-task-id>', rest], extra]
                                    for (child, rest), extra in meta['children']]
                row['meta'] = json.dumps(meta)
            results.append(row)
        logs = []
        for row in state.pop('application.EmailLog'):
            row = dict(row)
            assert since <= row['sent_at'] <= until, 'EmailLog.sent_at outside the run window'
            row['sent_at'] = 'run'
            row.pop('email_log_id')
            logs.append(row)
        broker = {}
        for key, (kind, value) in stack.broker_state().items():
            if kind == 'list':
                value = [self.normalize_message(item, original, reference) for item in value]
            elif kind == 'hash':
                value = {('<original-tag>' if k == original['properties']['delivery_tag'] else '<new-tag>'):
                         self.normalize_unacked(v, original, reference) for k, v in value.items()}
            elif kind == 'zset':
                value = len(value)
            broker[key] = [kind, value]
        transcripts = sorted(json.dumps(normalize_transcript(t)) for t in stack.smtp.take())
        return {'state': state, 'task_results': results, 'email_logs': logs, 'broker': broker, 'smtp': transcripts}

    def normalize_unacked(self, raw, original, reference):
        message, exchange, routing_key = json.loads(raw)
        return [self.normalize_message(json.dumps(message), original, reference), exchange, routing_key]

    @staticmethod
    def normalize_message(raw, original, reference):
        """The envelope as an order-preserving JSON string; per-publish random values replaced after their shape is checked.

        A new eta (a retry) is shown as its offset from the task result's date_done, which
        both workers store right after publishing the retry."""
        message = json.loads(raw)
        headers, properties = message['headers'], message['properties']
        UUID(properties['delivery_tag'])
        properties['delivery_tag'] = '<original-tag>' if properties['delivery_tag'] == original['properties']['delivery_tag'] else '<new-tag>'
        assert re.fullmatch(r'gen\d+@.+', headers['origin']), headers['origin']
        headers['origin'] = '<original-origin>' if headers['origin'] == original['headers']['origin'] else '<origin>'
        UUID(properties['reply_to'])
        properties['reply_to'] = '<original-reply-to>' if properties['reply_to'] == original['properties']['reply_to'] else '<reply-to>'
        if headers.get('eta') and headers['eta'] != original['headers'].get('eta'):
            assert reference is not None, 'a new eta without a task result'
            offset = datetime.fromisoformat(headers['eta']).timestamp() - reference.timestamp()
            # eta = publish time + countdown; date_done is stored a few milliseconds after the publish.
            assert 0 < math.ceil(offset) - offset < 1 or offset == int(offset), offset
            headers['eta'] = 'date_done+%ds' % math.ceil(offset)
        message['body'] = json.loads(base64.b64decode(message['body']))
        return json.dumps(message, ensure_ascii=False)

    # -- scenarios ----------------------------------------------------------------------------
    def execute(self, scenario):
        stack = self.stack
        context, _ = self.django_context(scenario, (GO_QUEUE,))
        with context:
            payload = stack.publish(scenario)
        stamp = json.loads(payload)['headers']['assozeta_published_at']
        stack.seed(scenario, stamp)
        stack.smtp.take()
        before = stack.snapshot()
        since = datetime.now(timezone.utc)
        self.run_django(scenario, payload)
        if scenario.handoff:
            # Django-only reference for a handed-off message: the same bytes on the Django queue.
            pass
        until = datetime.now(timezone.utc)
        django = self.observe(scenario, payload, since, until)
        self.authored(scenario, payload, django)
        if DJANGO_ONLY:
            self.observations[scenario.name] = {'django': django}
            self.completed.append(scenario.name)
            print('DJANGO: ' + scenario.name, flush=True)
            return

        stack.seed(scenario, stamp)
        stack.smtp.take()
        assert stack.snapshot() == before, 'seed is not reproducible'
        since = datetime.now(timezone.utc)
        self.run_go(scenario, payload)
        if scenario.handoff:
            handed = stack.broker.lrange(DJANGO_QUEUE, 0, -1)
            assert len(handed) == 1, 'handoff publishes exactly one message'
            expected = json.loads(payload)
            expected['properties']['delivery_info']['routing_key'] = DJANGO_QUEUE
            assert handed[0].decode() == json.dumps(expected), 'handoff keeps the message bytes (routing key celery)'
            assert stack.smtp.take() == [] and stack.snapshot()['django_celery_results.TaskResult'] == [], 'handoff has no effects'
            task_id = expected['headers']['id']
            from django_celery_results.models import TaskResult
            self.run_django(scenario, payload, queues=(DJANGO_QUEUE,), push=False,
                            wait=lambda: stack.broker.llen(DJANGO_QUEUE) == 0 and stack.broker.hlen('unacked') == 0
                            and TaskResult.objects.filter(task_id=task_id, status__in=('SUCCESS', 'FAILURE')).exists())
            if stack.broker.llen(GO_QUEUE):
                # Messages the Django task published (routed back to the Go queue) are consumed by Go.
                self.run_go(scenario, payload, push=False, handoff=False)
        until = datetime.now(timezone.utc)
        go = self.observe(scenario, payload, since, until)
        if scenario.handoff:
            # The Django run consumed from assozeta-go; the handoff run from celery: only the bindings differ.
            for observation in (django, go):
                observation['broker'] = {k: v for k, v in observation['broker'].items() if not k.startswith('_kombu.binding.')}
        self.observations[scenario.name] = {'django': django, 'go': go}
        if go != django:
            (OUTPUT / 'difference.json').write_text(json.dumps({'scenario': scenario.name, 'django': django, 'go': go},
                                                               indent=1, default=str, ensure_ascii=False) + '\n')
        assert go == django, 'Go differs from Django: ' + scenario.name
        self.completed.append(scenario.name)
        print('PASS: ' + scenario.name, flush=True)

    def authored(self, scenario, payload, observed):
        """The Django oracle took the path the scenario was written for."""
        expect = scenario.expect
        results = observed['task_results']
        if expect['status'] is None:
            assert results == [], results
        else:
            own = [row for row in results if row['task_id'] == json.loads(payload)['headers']['id']]
            assert len(results) == expect.get('rows', 1) and len(own) == 1 and own[0]['status'] == expect['status'], results
            # The maintenance deferral publishes with apply_async (added to the request's children);
            # Task.retry does not add its own message.
            children = [[[own[0]['task_id'], None], None]] if expect.get('exc') == 'Busy' else []
            assert own[0]['meta'] == json.dumps({'children': children}), results
            assert all(row['meta'] == '{"children": []}' for row in results if row is not own[0]), results
            if 'exc' in expect:
                assert json.loads(own[0]['result'])['exc_type'] == expect['exc'], results
            else:
                assert own[0]['result'] == 'null', results
        logs = [(r['recipient'], r['subject'], r['result'], r['sport_association_id']) for r in observed['email_logs']]
        assert logs == expect['emaillog'], logs
        if 'mails' in expect:
            delivered = [t for t in observed['smtp'] if '\\r\\n.\\r\\n' in t]
            assert len(delivered) == expect['mails'], observed['smtp']
        if 'retry' in expect:
            retries, countdown = expect['retry']
            held = [v for k, (kind, v) in observed['broker'].items() if k == GO_QUEUE]
            assert held and len(held[0]) == 1, observed['broker']
            message = json.loads(held[0][0])
            original = json.loads(payload)
            assert message['headers']['id'] == original['headers']['id'] and message['headers']['retries'] == retries
            assert message['headers']['eta'] == 'date_done+%ds' % countdown, message['headers']['eta']
            assert message['headers']['assozeta_published_at'] == original['headers']['assozeta_published_at']
            assert message['headers']['redelivered'] is True


def main():
    require_production_python()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report = OUTPUT / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server:
        raise RuntimeError('Private PostgreSQL, Redis and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    only = os.environ.get('CELERY_CONSUMER_ONLY')
    with tempfile.TemporaryDirectory(prefix='az-celery-consumer-', dir='/tmp') as directory, (OUTPUT / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; redis_process = None; smtp = None; harness = None
        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            if not DJANGO_ONLY:
              subprocess.run(['go', 'build', '-race', '-o', str(work / 'celery-consumer'), './cmd/celery-consumer'], cwd=ROOT / 'BE_GO',
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass', REDIS_PASSWORD,
                                              '--save', '', '--appendonly', 'no', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
            smtp = FakeSMTP(tls_context(work), False)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            stack = Stack(work, environment, redis_port, smtp)
            harness = Harness(stack, work / 'celery-consumer')
            for scenario in SCENARIOS:
                if only and only not in scenario.name:
                    continue
                harness.execute(scenario)
            if (work / 'celery-consumer').exists():
                result['binary_sha256'] = hashlib.sha256((work / 'celery-consumer').read_bytes()).hexdigest()
        finally:
            try:
                if harness:
                    result.update(scenarios=harness.completed, scenario_count=len(harness.completed))
                    (OUTPUT / 'observations.json').write_text(json.dumps(harness.observations, indent=1, default=str,
                                                                         ensure_ascii=False) + '\n')
                    for process, stream, _ in harness.stack.children:
                        if process.poll() is None:
                            process.kill()
                        stream.close()
            finally:
                try:
                    if smtp:
                        smtp.close()
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    if redis_process:
                        redis_process.terminate(); redis_process.wait(timeout=10)
                    if attempted:
                        try:
                            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                        except subprocess.CalledProcessError:
                            if (work / 'postgres/postmaster.pid').exists():
                                raise
                    report.write_text(json.dumps(result, indent=2) + '\n')
    expected = [s.name for s in SCENARIOS if not only or only in s.name]
    assert harness.completed == expected, 'not every scenario completed'
    paths = ['TESTS/adapters/go/celery_consumer_process.py', 'BE/core/tasks.py', 'BE/core/task_routing.py', 'BE/core/settings.py',
             'BE/instance/restore/task_base.py', 'BE/instance/email_configuration.py']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'cmd/celery-consumer/', 'celery_consumer', 'send_mail_async', 'internal/smtpsend/')))
    result.update(success=True, partial=bool(only), race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_hashes'}))


if __name__ == '__main__':
    main()
