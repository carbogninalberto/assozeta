"""Differential harness for the Go Celery consumer: the automation workflow tasks.

check_workflows_trigger, check_workflows_trigger_cron and workflow_block_execute
(BE/application/tasks.py, AutomationWorkflow.trigger in BE/communications/models.py),
routed to ``assozeta-go`` by ``GO_TASK_QUEUE_TASKS``. Private PostgreSQL (actual
Django current schema) and a private Redis broker, as celery_consumer_process.py.

Each scenario builds one message with Django's own producer, then runs it once on
a real worker of the Django Celery app (solo pool, ``-Q assozeta-go``, django-db,
CoordinatedTask) and once, on the same seed, on the race-built Go consumer. The
messages the tasks publish (``send_task`` inside the worker) are part of the result:
``workflow_block_execute`` children are routed back to ``assozeta-go`` and executed
by the same worker (eta children are held and restored at shutdown);
``send_email_html`` goes to the unconsumed ``celery`` queue.

Compared after each worker stopped: the complete managed-model state (TaskResult
rows, the auditlog entry and index of get_main_tutor's promotion), and every broker
key with decoded messages, plus every message published during the run (recorded
through Redis MONITOR, executed children included). Normalized after a shape check:
task ids (labelled by their place among the messages their parent published: T, T.0,
T.0.1, ...; root_id must be the scenario's message), delivery tags,
origin, reply_to, the publish stamp of new messages (inside the run window), the
random 1-180 s countdown of the email block (both messages of one block share it;
shown as ``random``), the order of ``recipient_list`` (Python builds it from a set)
after ``kwargsrepr`` was checked against the message's own kwargs, and the order of
list entries (held messages are restored in delivery order).
"""
import base64
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import free_port
from TESTS.adapters.go import celery_consumer_process as base

DATABASE = 'assozeta_contract_celery_workflow'
ROUTED = 'check_workflows_trigger,check_workflows_trigger_cron,workflow_block_execute'
GO_QUEUE, DJANGO_QUEUE = base.GO_QUEUE, base.DJANGO_QUEUE
OUTPUT = ROOT / os.environ.get('CELERY_WORKFLOW_OUTPUT', 'quality-reports/go-migration/consumer-workflow-process')
DJANGO_ONLY = bool(os.environ.get('CELERY_CONSUMER_DJANGO_ONLY'))
base.TASK, base.OUTPUT, base.DATABASE = ROUTED, OUTPUT, DATABASE


def U(n):
    return UUID(int=0xC6000000 + n)


OWNER, ATHLETE, OTHER = U(1), U(2), U(3)
A, B, C = U(11), U(12), U(13)
P1, P2, P3, T1, T2 = U(21), U(22), U(25), U(23), U(24)
S1, S2, S3, S4, S5, S6 = U(31), U(32), U(33), U(34), U(35), U(36)
TAG_X, TAG_Y, TAG_Z = U(41), U(42), U(43)
W1, W2, W3, W4, W5, W6, W7, W8, W9, W10, W11, W12, W13, W_OK, W_BAD = (U(n) for n in range(51, 66))
MISSING = U(99)
OWNER_MAIL, ATHLETE_MAIL = 'owner1@example.test', 'atleta@example.test'


def trigger(value, **data):
    return {'id': 'trigger', 'value': value, 'data': data}


def email(recipients, subject='Benvenuto', content='<p>Ciao, benvenuto!</p>'):
    return {'id': 'message', 'value': 'email', 'data': {'subject': subject, 'content': content, 'recipients': recipients}}


def wait(kind, amount):
    return {'id': 'wait', 'value': 'wait', 'data': {'type': kind, 'amount': amount}}


SMS = {'id': 'message', 'value': 'sms', 'data': {'content': 'ciao'}}
CRON = '2026-01-01T10:00'
WORKFLOWS = [  # (id, association, enabled, tree), inserted in this order
    (W1, A, True, [trigger('new_subscription'), email('all'), wait('days', '2'), email('me', subject='Promemoria')]),
    (W2, A, True, [trigger('new_subscription')]),
    (W3, A, True, [trigger('subscription_signed'), SMS]),
    (W4, A, False, [trigger('new_subscription'), email('all')]),
    (W5, A, True, [trigger('cron', time=CRON, target='approved'), email('all', subject='Quota')]),
    (W6, A, True, [trigger('cron', time=CRON, target=str(TAG_X)), SMS]),
    (W7, A, True, [trigger('cron', time=CRON, target='all'), SMS]),
    (W8, A, True, [trigger('cron', time=CRON, target='archived'), SMS]),
    (W9, A, True, [trigger('cron', time=CRON, target='all')]),
    (W10, B, True, [trigger('cron', time=CRON, target='pending'), SMS]),
    (W11, A, True, [trigger('subscription_signed'), email('athletes', content='<p>Solo atleti</p>'), wait('minutes', 0),
                    wait('weeks', 'x'), wait('hours', '1')]),
    (W12, A, True, [trigger('cron', time=CRON, target='vip'), SMS]),
    (W13, A, True, [trigger('subscription_signed'), wait('minutes', 1.5), SMS]),
    (W_OK, C, True, [trigger('payment_paid'), SMS]),
    (W_BAD, C, True, []),
]


class Scenario:
    def __init__(self, name, task, args=None, kwargs=None, *, rows=1, held=0, emails=(), result=None, audit=0,
                 handoff=False, children=None):
        self.name, self.task, self.args, self.kwargs = name, task, args, kwargs or {}
        self.rows, self.held, self.emails, self.result, self.audit = rows, held, emails, result, audit
        self.handoff, self.children = handoff, children
        # celery_consumer_process.Stack/Harness attributes
        self.expect = {'status': 'SUCCESS'}
        self.unacked_seed, self.smtp, self.done = False, 'ok', 'held' if held else 'result'


BLOCK, TRIGGER, CRON_TASK = 'workflow_block_execute', 'check_workflows_trigger', 'check_workflows_trigger_cron'
EVERYONE = sorted([ATHLETE_MAIL, OWNER_MAIL, 'p1@example.test'])
SCENARIOS = [
    Scenario('trigger new_subscription publishes the first block, which mails everyone and waits a random countdown', TRIGGER,
             ['new_subscription', str(A)], {'subscription_id': str(S1), 'data': {'source': 'web'}}, rows=2, held=1,
             emails=[EVERYONE], children={'T': [BLOCK], 'T.0': ['send_email_html', BLOCK]}),
    Scenario('email block for a minor promotes the first tutor relation and adds the tutor', BLOCK, [str(W1), str(S2), 1],
             held=1, emails=[sorted([ATHLETE_MAIL, OWNER_MAIL, 'tutore@example.test'])], audit=1,
             children={'T': ['send_email_html', BLOCK]}),
    Scenario('wait block of two days schedules the next block', BLOCK, [str(W1), str(S1), 2], held=1, children={'T': [BLOCK]}),
    Scenario('last email block to the owner only schedules nothing else', BLOCK, [str(W1), str(S1), 3], emails=[[OWNER_MAIL]],
             children={'T': ['send_email_html']}),
    Scenario('block with a missing subscription returns the tuple', BLOCK, [str(W1), str(MISSING), 1],
             result=[False, 'Subscription is None']),
    Scenario('block of a disabled workflow returns None', BLOCK, [str(W4), str(S1), 1]),
    Scenario('athletes block when the owner subscribed keeps the associate only', BLOCK, [W11, S3, 1], held=1,
             emails=[['p3@example.test']], children={'T': ['send_email_html', BLOCK]}),
    Scenario('zero-minute wait publishes without eta; the unknown wait type then stops', BLOCK,
             kwargs={'workflow_id': str(W11), 'subscription_id': str(S3), 'idx': 2}, rows=2, children={'T': [BLOCK]}),
    Scenario('negative index runs the last block and schedules index 0', BLOCK, [str(W11), str(S3), -1], held=1,
             children={'T': [BLOCK]}),
    Scenario('malformed subscription id ends the block with None', BLOCK, [str(W1), 'not-a-uuid', 1]),
    Scenario('trigger with an unknown subscription stops', TRIGGER, ['new_subscription', str(A), str(MISSING)]),
    Scenario('trigger keeps the first workflow publish when the next tree raises', TRIGGER, ['payment_paid', str(C), str(S1)],
             rows=2, children={'T': [BLOCK]}),
    Scenario('cron approved target fans out to the accepted subscription', CRON_TASK, [str(W5)], rows=2,
             emails=[EVERYONE], children={'T': [BLOCK], 'T.0': ['send_email_html']}),
    Scenario('cron tag target repeats a subscription per matching tag', CRON_TASK, [str(W6)], rows=4,
             children={'T': [BLOCK, BLOCK, BLOCK]}),
    Scenario('cron all target covers every active subscription', CRON_TASK, [W7], rows=5, children={'T': [BLOCK] * 4}),
    Scenario('cron archived target', CRON_TASK, [str(W8)], rows=2, children={'T': [BLOCK]}),
    Scenario('cron workflow with a single node does nothing', CRON_TASK, [str(W9)]),
    Scenario('cron target that is neither a keyword nor a uuid ends with None', CRON_TASK, [str(W12)]),
    Scenario('float wait amount is handed off to the Django queue', BLOCK, [str(W13), str(S1), 1], held=1, handoff=True,
             children={'T': [BLOCK]}),
    Scenario('non-string trigger type is handed off to the Django queue', TRIGGER, [5, str(A), str(S1)], handoff=True),
]


class StubSMTP:
    port = 9

    def take(self):
        return []

    def close(self):
        pass


class Stack(base.Stack):
    def seed(self, scenario, stamp=None):
        from django.db import connection
        from application.models import User, SportAssociation, Associate, Subscription
        from application.models.subscriptions_models import Tags
        from application.models.user_models import AssociateTutorRelation
        from communications.models import AutomationWorkflow
        started = datetime.now(timezone.utc)
        today = started.date()
        created = datetime(2025, 1, 1, tzinfo=timezone.utc)
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, django_celery_results_taskresult,
              instance_datarestore, application_associatetutorrelation, application_associate, application_tags,
              communications_automationworkflow, application_subscription, application_sportassociation, bakney_user
              RESTART IDENTITY CASCADE''')
        User.original_objects.bulk_create([User(user_id=pk, username=f'workflow-user-{n}', role=1, password='!private-fixture',
                                                date_joined=created, email=mail) for n, pk, mail in
                                           ((1, OWNER, OWNER_MAIL), (2, ATHLETE, ATHLETE_MAIL), (3, OTHER, 'owner3@example.test'))])
        SportAssociation.original_objects.bulk_create([
            SportAssociation(sport_association_id=A, user_id=OWNER, denomination='ASD Uno', tax_code='00000000000'),
            SportAssociation(sport_association_id=B, user_id=OTHER, denomination='ASD Due', tax_code='00000000000'),
            SportAssociation(sport_association_id=C, user_id=ATHLETE, denomination='ASD Tre', tax_code='00000000000')])
        Associate._base_manager.bulk_create([
            Associate(associate_id=P1, sport_association_id=A, first_name='Paolo', email='p1@example.test', creation_date=created),
            Associate(associate_id=P2, sport_association_id=A, first_name='Marta', email=None, is_minor=True, creation_date=created),
            Associate(associate_id=P3, sport_association_id=A, first_name='Luca', email='p3@example.test', is_minor=True,
                      creation_date=created),
            Associate(associate_id=T1, sport_association_id=A, first_name='Tutore', email='tutore@example.test', creation_date=created),
            Associate(associate_id=T2, sport_association_id=A, first_name='Secondo', email=None, creation_date=created)])
        AssociateTutorRelation.objects.bulk_create([
            AssociateTutorRelation(id=1, associate_id=P2, tutor_id=T1, is_primary=False),
            AssociateTutorRelation(id=2, associate_id=P2, tutor_id=T2, is_primary=False),
            AssociateTutorRelation(id=3, associate_id=P3, tutor_id=T2, is_primary=True)])
        Tags.objects.bulk_create([Tags(tag_id=TAG_X, tag_name='Base', sport_association_id=A, creation_date=created),
                                  Tags(tag_id=TAG_Y, tag_name='Base serale', sport_association_id=A, creation_date=created),
                                  Tags(tag_id=TAG_Z, tag_name='Avanzato', sport_association_id=B, creation_date=created)])
        live = dict(start_date=today - timedelta(days=30), end_date=today + timedelta(days=300), creation_date=created)
        Subscription._base_manager.bulk_create([
            Subscription(subscription_id=S1, sport_association_id=A, user_id=ATHLETE, associate_id=P1, status_flag=4, **live),
            Subscription(subscription_id=S2, sport_association_id=A, user_id=ATHLETE, associate_id=P2, status_flag=2, **live),
            Subscription(subscription_id=S3, sport_association_id=A, user_id=OWNER, associate_id=P3, status_flag=1, **live),
            Subscription(subscription_id=S4, sport_association_id=A, user_id=ATHLETE, associate_id=P1, status_flag=4,
                         archived=True, **live),
            Subscription(subscription_id=S5, sport_association_id=A, user_id=ATHLETE, associate_id=P1, status_flag=4,
                         start_date=today - timedelta(days=400), end_date=today - timedelta(days=1), creation_date=created),
            Subscription(subscription_id=S6, sport_association_id=B, user_id=ATHLETE, status_flag=2, **live)])
        through = Subscription.tags.through
        through.objects.bulk_create([through(id=1, subscription_id=S1, tags_id=TAG_X), through(id=2, subscription_id=S2, tags_id=TAG_X),
                                     through(id=3, subscription_id=S2, tags_id=TAG_Y), through(id=4, subscription_id=S6, tags_id=TAG_Z)])
        AutomationWorkflow.objects.bulk_create([AutomationWorkflow(automation_workflow_id=pk, sport_association_id=association,
                                                                   name=f'Workflow {i}', automation_tree=tree, enabled=enabled,
                                                                   created_at=created, updated_at=created)
                                                for i, (pk, association, enabled, tree) in enumerate(WORKFLOWS)])
        self.seeding = (started, datetime.now(timezone.utc))

    def publish(self, scenario):
        """Django's producer (apply_async) with the production routing for the three task names."""
        from application import tasks
        task = {BLOCK: tasks.workflow_block_execute, TRIGGER: tasks.check_workflows_trigger,
                CRON_TASK: tasks.check_workflows_trigger_cron}[scenario.task]
        self.broker.flushdb()
        task.apply_async(args=scenario.args, kwargs=scenario.kwargs)
        raw = self.broker.rpop(GO_QUEUE)
        assert raw is not None and self.broker.llen(GO_QUEUE) == 0 and self.broker.llen(DJANGO_QUEUE) == 0, 'routed publish'
        return raw.decode()

    def quiescent(self, scenario, task_id, held=None):
        from django_celery_results.models import TaskResult
        if self.broker.llen(GO_QUEUE) or self.broker.hlen('unacked') != scenario.held:
            return False
        rows = list(TaskResult.objects.values_list('task_id', 'status'))
        return (len(rows) == scenario.rows and any(t == task_id for t, _ in rows)
                and all(status in ('SUCCESS', 'FAILURE', 'REVOKED') for _, status in rows))


class Monitor:
    """Records every message pushed to a queue (LPUSH/RPUSH seen through Redis MONITOR), in order."""

    def __init__(self, stack):
        self.stack, self.published, self.lines = stack, [], []

    def __enter__(self):
        import socket
        import threading
        self.socket = socket.create_connection(('127.0.0.1', self.stack.redis_port))
        password = base.REDIS_PASSWORD.encode()
        self.socket.sendall(b'*2\r\n$4\r\nAUTH\r\n$%d\r\n%s\r\n*1\r\n$7\r\nMONITOR\r\n' % (len(password), password))
        self.file = self.socket.makefile('rb')
        assert self.file.readline() == b'+OK\r\n' and self.file.readline() == b'+OK\r\n', 'MONITOR'
        self.thread = threading.Thread(target=self.read, daemon=True)
        self.thread.start()
        return self

    def read(self):
        try:
            for line in self.file:
                self.lines.append(line)
        except (OSError, ValueError):
            pass

    def __exit__(self, *exc):
        import time
        time.sleep(0.3)
        self.socket.close()
        self.thread.join(timeout=5)
        for line in self.lines:
            arguments = monitor_arguments(line.rstrip(b'\r\n').decode('ascii'))
            if arguments and arguments[0].lower() in ('lpush', 'rpush') and arguments[1] in (GO_QUEUE, DJANGO_QUEUE):
                message = json.loads(arguments[2])
                if all(m['headers']['id'] != message['headers']['id'] or m['headers'].get('eta') != message['headers'].get('eta')
                       or m['headers']['retries'] != message['headers']['retries'] for m in self.published):
                    self.published.append(message)  # restores (RPUSH, redelivered) repeat a message
        return False


def monitor_arguments(line):
    """The quoted arguments of one MONITOR line (sdscatrepr escapes)."""
    start = line.find('"')
    if start < 0:
        return None
    arguments, i = [], start
    while i < len(line):
        if line[i] != '"':
            i += 1
            continue
        i += 1
        out = []
        while line[i] != '"':
            if line[i] == '\\':
                nxt = line[i + 1]
                if nxt == 'x':
                    out.append(chr(int(line[i + 2:i + 4], 16)))
                    i += 4
                    continue
                out.append({'n': '\n', 'r': '\r', 't': '\t', 'a': '\a', 'b': '\b'}.get(nxt, nxt))
                i += 2
                continue
            out.append(line[i])
            i += 1
        arguments.append(''.join(out))
        i += 1
    return arguments


class Harness(base.Harness):
    def observe(self, scenario, payload, since, until, published):
        """Normalized state, results, broker keys and every message published during the run."""
        from kombu.utils.json import loads as kombu_loads
        from celery.utils.saferepr import saferepr
        stack = self.stack
        original = json.loads(payload)
        state = stack.snapshot()
        rows = state.pop('django_celery_results.TaskResult')
        # Label each task id by its place among the messages its parent published (T, T.0, T.0.1, ...).
        labels = {original['headers']['id']: 'T'}
        counts, children = {}, []
        for message in published:
            headers = message['headers']
            if headers['id'] == original['headers']['id']:
                continue  # the scenario's own message (pushed, handed off or restored)
            assert headers['parent_id'] in labels, 'a message published outside the task tree'
            parent = labels[headers['parent_id']]
            labels[headers['id']] = f'{parent}.{counts.get(parent, 0)}'
            counts[parent] = counts.get(parent, 0) + 1
            children.append(message)
        results = []
        for row in rows:
            row = dict(row)
            for field in ('date_created', 'date_done'):
                assert since <= row[field] <= until, f'{field} outside the run window'
            assert row['task_id'] in labels, 'a stored result outside the task tree'
            row.pop('id')
            row['task_id'] = labels[row['task_id']]
            meta = json.loads(row['meta'])
            meta['children'] = [[[labels[c[0][0]], c[0][1]], c[1]] for c in meta['children']]
            row['meta'] = meta
            row['date_created'] = row['date_done'] = 'run'
            results.append(row)
        results.sort(key=lambda row: row['task_id'])
        done = {labels[row['task_id']]: row['date_done'] for row in rows}
        siblings = {}
        for message in children:
            siblings.setdefault(message['headers']['parent_id'], []).append(message)

        def normalize(message):
            message = json.loads(json.dumps(message))
            headers, properties = message['headers'], message['properties']
            assert headers['id'] in labels, f"unknown task id {headers['id']}"
            own = labels[headers['id']]
            raw_parent = headers['parent_id']
            assert properties['correlation_id'] == headers['id']
            text = base64.b64decode(message['body']).decode()
            body, decoded = json.loads(text), kombu_loads(text)
            UUID(properties['delivery_tag'])
            properties['delivery_tag'] = ('<original-tag>' if properties['delivery_tag'] == original['properties']['delivery_tag']
                                          else '<tag>')
            if own == 'T':
                for field in ('origin', 'assozeta_published_at'):
                    assert headers[field] == original['headers'][field], field
                assert properties['reply_to'] == original['properties']['reply_to']
                headers['origin'], properties['reply_to'] = '<original-origin>', '<original-reply-to>'
            else:
                assert re.fullmatch(r'gen\d+@.+', headers['origin']), headers['origin']
                UUID(properties['reply_to'])
                stamp = headers['assozeta_published_at']
                assert since.timestamp() <= stamp <= until.timestamp(), 'child publish stamp outside the run window'
                headers['origin'], properties['reply_to'], headers['assozeta_published_at'] = '<origin>', '<reply-to>', '<stamp>'
                # argsrepr/kwargsrepr are saferepr of the published arguments (send_task: a list, or () without args).
                expected_args = saferepr(decoded[0], 1024) if decoded[0] else '()'
                assert headers['argsrepr'] == expected_args, (headers['argsrepr'], expected_args)
                assert headers['kwargsrepr'] == saferepr(decoded[1], 1024), headers['kwargsrepr']
            assert headers['root_id'] == original['headers']['id'], 'root is the published message'
            headers['id'] = properties['correlation_id'] = own
            headers['root_id'] = 'T'
            if raw_parent is not None:
                headers['parent_id'] = labels[raw_parent]
            if headers.get('eta') and (own != 'T' or headers['eta'] != original['headers'].get('eta')):
                reference = done[own.rsplit('.', 1)[0] if own != 'T' else 'T']
                offset = datetime.fromisoformat(headers['eta']).timestamp() - reference.timestamp()
                group = siblings.get(raw_parent, []) if own != 'T' else []
                if any(m['headers']['task'] == 'send_email_html' for m in group):
                    etas = [datetime.fromisoformat(m['headers']['eta']).timestamp() for m in group]
                    assert max(etas) - min(etas) < 1, 'one countdown per email block'
                    assert 0 < math.ceil(offset) <= 180, offset
                    headers['eta'] = 'date_done+random(1..180)s'
                else:
                    assert 0 < math.ceil(offset) - offset < 1 or offset == int(offset), offset
                    headers['eta'] = 'date_done+%ds' % math.ceil(offset)
            if headers['task'] == 'send_email_html':
                body[1]['recipient_list'] = sorted(body[1]['recipient_list'])
                headers['kwargsrepr'] = '<checked: saferepr of the kwargs>'
            message['body'] = body
            return json.dumps(message, ensure_ascii=False)

        broker = {}
        for key, (kind, value) in stack.broker_state().items():
            if kind == 'list':
                value = sorted(normalize(json.loads(item)) for item in value)
            elif kind == 'hash':
                entries = []
                for tag, item in value.items():
                    message, exchange, routing_key = json.loads(item)
                    assert message['properties']['delivery_tag'] == tag, 'unacked key is the delivery tag'
                    entries.append(json.dumps([normalize(message), exchange, routing_key]))
                value = sorted(entries)
            elif kind == 'zset':
                value = len(value)
            broker[key] = [kind, value]
        state = self.normalized_audit(state, since, until)
        return {'state': state, 'task_results': results, 'broker': broker,
                'published': sorted(normalize(message) for message in children)}

    @staticmethod
    def normalized_audit(state, since, until):
        state = dict(state)
        keys, entries = {}, []
        for entry in state['auditlog.LogEntry']:
            entry = dict(entry)
            assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
            keys[entry['id']] = (entry['object_pk'], json.dumps(entry['changes'], sort_keys=True))
            entry.pop('id')
            entry['timestamp'] = 'run'
            entries.append(entry)
        state['auditlog.LogEntry'] = sorted(entries, key=lambda row: (row['object_pk'], json.dumps(row['changes'], sort_keys=True)))
        indexes = []
        for index in state['application.AuditLogIndex']:
            index = dict(index)
            assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
            index.pop('audit_log_index_id')
            index['created_at'] = 'run'
            index['log_entry_id'] = keys[index['log_entry_id']]
            indexes.append(index)
        state['application.AuditLogIndex'] = sorted(indexes, key=lambda row: row['log_entry_id'])
        return state

    def execute(self, scenario):
        stack = self.stack
        context, _ = self.django_context(scenario, (GO_QUEUE,))
        with context:
            payload = stack.publish(scenario)
        stack.seed(scenario)
        before = stack.snapshot()
        since = datetime.now(timezone.utc)
        with Monitor(stack) as monitor:
            self.run_django(scenario, payload)
        until = datetime.now(timezone.utc)
        django = self.observe(scenario, payload, since, until, monitor.published)
        self.authored(scenario, payload, django)
        if DJANGO_ONLY:
            self.observations[scenario.name] = {'django': django}
            self.completed.append(scenario.name)
            print('DJANGO: ' + scenario.name, flush=True)
            return
        stack.seed(scenario)
        assert stack.snapshot() == before, 'seed is not reproducible'
        since = datetime.now(timezone.utc)
        monitor = Monitor(stack).__enter__()
        self.run_go(scenario, payload)
        if scenario.handoff:
            handed = stack.broker.lrange(DJANGO_QUEUE, 0, -1)
            assert len(handed) == 1, 'handoff publishes exactly one message'
            expected = json.loads(payload)
            expected['properties']['delivery_info']['routing_key'] = DJANGO_QUEUE
            assert handed[0].decode() == json.dumps(expected), 'handoff keeps the message bytes (routing key celery)'
            assert stack.snapshot() == before, 'handoff has no effects'
            task_id = expected['headers']['id']
            from django_celery_results.models import TaskResult
            self.run_django(scenario, payload, queues=(DJANGO_QUEUE,), push=False,
                            wait=lambda: stack.broker.llen(DJANGO_QUEUE) == 0 and stack.broker.hlen('unacked') == 0
                            and TaskResult.objects.filter(task_id=task_id, status__in=('SUCCESS', 'FAILURE')).exists())
            if stack.broker.llen(GO_QUEUE):
                self.run_go(scenario, payload, push=False, handoff=False)
        monitor.__exit__(None, None, None)
        until = datetime.now(timezone.utc)
        go = self.observe(scenario, payload, since, until, monitor.published)
        if scenario.handoff:
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
        results = {row['task_id']: row for row in observed['task_results']}
        assert len(results) == scenario.rows, results
        assert all(row['status'] == 'SUCCESS' for row in results.values()), results
        # Messages published from the task body never reach request.children (Task.__call__ pushes its own request).
        assert all(row['meta'] == {'children': []} for row in results.values()), results
        assert json.loads(results['T']['result']) == scenario.result, results['T']
        messages = [json.loads(item) for item in observed['published']]
        names = {}
        for message in messages:
            names.setdefault(message['headers']['parent_id'], []).append(message['headers']['task'])
        assert {k: sorted(v) for k, v in names.items()} == {k: sorted(v) for k, v in (scenario.children or {}).items()}, names
        for message in messages:
            routed = DJANGO_QUEUE if message['headers']['task'] == 'send_email_html' else GO_QUEUE
            assert message['properties']['delivery_info']['routing_key'] == routed, message
            if message['headers']['task'] == BLOCK and message['headers']['id'] not in results:
                assert message['headers']['eta'], 'only eta children stay unexecuted'
        mails = [m for m in messages if m['headers']['task'] == 'send_email_html']
        assert sorted(m['body'][1]['recipient_list'] for m in mails) == sorted(scenario.emails), mails
        held = observed['broker'].get(GO_QUEUE, ['list', []])[1]  # held eta messages, restored at shutdown
        assert len(held) == scenario.held, held
        assert len(observed['state']['auditlog.LogEntry']) == scenario.audit, observed['state']['auditlog.LogEntry']


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
    with tempfile.TemporaryDirectory(prefix='az-celery-workflow-', dir='/tmp') as directory, (OUTPUT / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; redis_process = None; harness = None
        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            if not DJANGO_ONLY:
                subprocess.run(['go', 'build', '-race', '-o', str(work / 'celery-consumer'), './cmd/celery-consumer'], cwd=ROOT / 'BE_GO',
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass',
                                              base.REDIS_PASSWORD, '--save', '', '--appendonly', 'no', '--dir', str(work)],
                                             stdout=log, stderr=subprocess.STDOUT)
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            stack = Stack(work, environment, redis_port, StubSMTP())
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
    paths = ['TESTS/adapters/go/celery_consumer_workflow_process.py', 'TESTS/adapters/go/celery_consumer_process.py',
             'BE/application/tasks.py', 'BE/communications/models.py', 'BE/application/models/user_models.py',
             'BE/core/task_routing.py', 'BE/instance/restore/task_base.py']
    paths += sorted(str(p.relative_to(ROOT)) for p in (ROOT / 'BE_GO').rglob('*.go')
                    if any(part in str(p) for part in ('internal/celery/', 'internal/celeryworker/', 'cmd/celery-consumer/',
                                                       'workflow_tasks')))
    result.update(success=True, partial=bool(only), race_instrumented=not DJANGO_ONLY, django_only=DJANGO_ONLY,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'source_hashes'}))


if __name__ == '__main__':
    main()
