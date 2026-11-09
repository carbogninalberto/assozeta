"""Native Stripe and membership periodic task workers on actual Django-created current schema.

Private PostgreSQL, a private Redis (Celery broker DB 0, Django cache DB 1)
and a private Stripe API stub (a local HTTP server that records every request
and answers by path). For each task (change_type_of_stripe_payments,
generate_coupon_if_not_exists, renew_memberships_payments) the seed is first
processed by the actual Django task with the production broker and cache
backends; the native Go job must then leave the identical complete
managed-model state, send the same Stripe requests (method, path, form body,
headers, idempotency-key reuse and telemetry chaining) and publish the same
Celery messages. Random values (created payment keys, promotion codes,
renewal timestamps, audit ids and timestamps) are normalized after their
shape and run window are checked. Explicit fixture producers and ownership are
not installed activation.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qsl
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_tasks_db_d_worker'
REDIS_PASSWORD = 'private-redis-password-canary'
BRAND = 'Assozeta Fixture'
PUBLIC_KEY, SECRET_KEY = 'pk_test_placeholder', 'sk_test_placeholder'
LOCK_KEY = ':1:renew_memberships_payments_lock'
CHANGE, COUPON, RENEW = 'change_type_of_stripe_payments', 'generate_coupon_if_not_exists', 'renew_memberships_payments'
CODE = re.compile(r'\A[A-HJKMNP-Z2-9]{8}\Z')
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
CLIENT_AGENT_KEYS = ['bindings_version', 'lang', 'publisher', 'httplib', 'lang_version', 'platform', 'uname']
# initialize_current_schema replaces os.environ; read the development switches first.
PROBE, ORACLE_ONLY = bool(os.environ.get('TASKS_DB_D_PROBE')), bool(os.environ.get('TASKS_DB_D_ORACLE_ONLY'))
EXPECTED = {
    'default Django owner does not claim or call Stripe',
    'change type: native worker reproduces the Django task',
    'change type: a resource without status ends the task like Django',
    'change type: unconfigured credentials return before any request',
    'coupons: native worker reproduces the Django task',
    'coupons: a Stripe error ends the task after the earlier saves',
    'coupons: a missing secret key ends the task before any request',
    'renewals: native worker reproduces the Django task',
    'renewals: a locked subscription is skipped and the retry is published',
    'renewals: a held cache lock skips the run and the Go lock is the Django cache entry',
    'renewals: a run that would send the support e-mail is refused before any effect',
    'unsupported version payload and tenant fail before business effects',
}


def U(n):
    return UUID(int=0xD4000000 + n)


# ---------------------------------------------------------------------------
# Stripe API stub
# ---------------------------------------------------------------------------

INTENTS = {
    'pi_ok_1': (200, {'id': 'pi_ok_1', 'object': 'payment_intent', 'status': 'succeeded'}),
    'pi_pending': (200, {'id': 'pi_pending', 'object': 'payment_intent', 'status': 'processing'}),
    'pi_missing': (404, {'error': {'type': 'invalid_request_error', 'code': 'resource_missing', 'message': 'No such payment_intent'}}),
    'pi_empty': (200, {}),
    'pi old/ä': (200, {'id': 'pi old/ä', 'object': 'payment_intent', 'status': 'succeeded'}),
    'pi_card': (402, {'error': {'type': 'card_error', 'message': 'declined'}}),
    'pi_nostatus': (200, {'id': 'pi_nostatus', 'object': 'payment_intent'}),
    'pi_ok_2': (200, {'id': 'pi_ok_2', 'object': 'payment_intent', 'status': 'succeeded'}),
}
STUB = {}
PKS = {}


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _answer(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'method': self.command, 'path': self.path, 'headers': {k.lower(): v for k, v in self.headers.items()},
                                'header_names': [k.lower() for k in self.headers.keys()], 'body': body.decode()})
            number = len(STUB['log'])
            status, payload, retry = self.reply(body)
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Request-Id', f'req_stub_{number}')
        if retry is not None:
            self.send_header('Stripe-Should-Retry', retry)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def reply(self, body):
        fields = dict(parse_qsl(body.decode()))
        if self.command == 'GET' and self.path.startswith('/v1/payment_intents/'):
            from urllib.parse import unquote_plus
            intent = unquote_plus(self.path[len('/v1/payment_intents/'):])
            if intent == 'pi_retry':
                # The first attempt fails with a retryable 500; the SDK retries.
                seen = sum(1 for item in STUB['log'] if item['path'] == self.path)
                if seen == 1:
                    return 500, {'error': {'type': 'api_error', 'message': 'retry'}}, 'true'
                return 200, {'id': 'pi_retry', 'object': 'payment_intent', 'status': 'succeeded'}, None
            if intent in INTENTS:
                status, payload = INTENTS[intent]
                return status, payload, None
        if self.command == 'POST' and self.path == '/v1/coupons':
            if fields.get('name') == 'Errore coupon':
                return 400, {'error': {'type': 'invalid_request_error', 'message': 'rejected'}}, None
            STUB['coupons'] += 1
            return 200, {'id': f"co_stub_{STUB['coupons']}", 'object': 'coupon', 'percent_off': 10.0}, None
        if self.command == 'POST' and self.path == '/v1/promotion_codes':
            STUB['promotions'] += 1
            return 200, {'id': f"promo_stub_{STUB['promotions']}", 'object': 'promotion_code', 'code': fields.get('code')}, None
        return 500, {'error': {'type': 'api_error', 'message': 'unexpected request'}}, 'false'

    do_GET = do_POST = do_DELETE = _answer


class Stub:
    def __init__(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), _StubHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        STUB.update(lock=threading.Lock(), log=[], coupons=0, promotions=0)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'
        self.thread.start()

    def reset(self):
        with STUB['lock']:
            STUB.update(log=[], coupons=0, promotions=0)

    def take(self):
        with STUB['lock']:
            log = list(STUB['log'])
            STUB.update(log=[], coupons=0, promotions=0)
        return log

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


def check_requests(log):
    """Header checks shared by both runtimes; returns the comparable request list."""
    comparable, keys = [], []
    for index, sent in enumerate(log):
        where = f'stripe request {index}'
        headers = sent['headers']
        assert headers.get('authorization') == 'Bearer ' + SECRET_KEY, where + ': authorization'
        assert headers.get('stripe-version') == '2025-02-24.acacia', where + ': version'
        assert headers.get('user-agent') == 'Stripe/v1 PythonBindings/11.6.0', where + ': user agent'
        assert 'stripe-account' not in headers and 'stripe-context' not in headers, where + ': account'
        agent = json.loads(headers.get('x-stripe-client-user-agent', 'null'))
        assert isinstance(agent, dict) and list(agent) == CLIENT_AGENT_KEYS and json.dumps(agent) == headers['x-stripe-client-user-agent']
        assert [agent[k] for k in CLIENT_AGENT_KEYS[:4]] == ['11.6.0', 'python', 'stripe', 'requests'], where + ': agent'
        key = headers.get('idempotency-key')
        retry = False
        if sent['method'] == 'POST':
            assert headers.get('content-type') == 'application/x-www-form-urlencoded', where + ': content type'
            assert key and UUID4.match(key) and sent['header_names'].count('idempotency-key') == 1, where + ': key'
            assert key not in keys, where + ': fresh key'
        else:
            assert key is None and 'content-type' not in headers, where + ': GET headers'
            retry = bool(comparable) and comparable[-1]['path'] == sent['path']
        keys.append(key)
        telemetry = headers.get('x-stripe-client-telemetry')
        previous = None
        if telemetry is not None:
            metrics = json.loads(telemetry)
            inner = metrics['last_request_metrics']
            assert list(metrics) == ['last_request_metrics'] and list(inner) == ['request_id', 'request_duration_ms']
            assert type(inner['request_duration_ms']) is int and inner['request_duration_ms'] >= 0 and json.dumps(metrics) == telemetry
            previous = inner['request_id']
        comparable.append({'method': sent['method'], 'path': sent['path'], 'body': sent['body'], 'telemetry': previous, 'retry': retry})
    return comparable


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

class Fixture:
    def __init__(self):
        self.owner, self.athlete = U(1), U(2)
        self.owners = [U(1)] + [U(3 + i) for i in range(4)]
        self.associations = [U(11 + i) for i in range(5)]

    def users(self):
        from application.models import User
        User.original_objects.bulk_create([User(user_id=pk, username=f'tasks-d-{i}', role=2 if pk == self.athlete else 1,
            email=f'user{i}@example.test', first_name='Athlete' if pk == self.athlete else 'Owner', last_name='Fixture',
            password='!private-fixture', date_joined=datetime(2026, 1, 1, tzinfo=timezone.utc))
            for i, pk in enumerate([self.athlete] + self.owners)])

    def clear(self):
        from django.apps import apps
        from django.db import connection
        tables = [m._meta.db_table for m in apps.get_models(include_auto_created=True)
                  if m._meta.managed and not m._meta.proxy and m._meta.db_table not in ('django_content_type',
                                                                                         'auth_permission', 'django_migrations')]
        tables = [t for t in tables if not t.startswith('django_celery') or t == 'django_celery_results_taskresult']
        with connection.cursor() as cursor:
            cursor.execute('TRUNCATE ' + ','.join(f'"{t}"' for t in sorted(set(tables))) + ' CASCADE')

    def base(self, *, unconfigured=False):
        """Associations, a bank account and the default payment category."""
        from application.models import SportAssociation, PaymentCategory
        from application.models.balance_sheet_models import CustomAccounts
        from instance.models import InstanceConfiguration
        self.clear()
        self.users()
        rows = [('Società Sportiva Dilettantistica Àlfa Nuoto e Pallanuoto Riva', None, False),
                ('Errore coupon', None, False), ('Seconda', None, False), ('Già codificata', 'EXIST234', False),
                ('Eliminata', None, True)]
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=self.associations[i], user_id=self.owners[i],
            denomination=name, tax_code='00000000000', affiliate_code=code, affiliate_code_stripe='co_old' if i == 2 else None,
            deleted=deleted) for i, (name, code, deleted) in enumerate(rows)])
        CustomAccounts.objects.bulk_create([CustomAccounts(custom_account_id=U(21), sport_association_id=self.associations[0],
            name='Banca', account_type=CustomAccounts.BANK, editable=False, enabled=True, initial_balance=Decimal('0.00'))])
        PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=U(31), name='Entrate e proventi da attività tipiche')])
        if unconfigured:
            InstanceConfiguration.objects.create(self_hosted=True, integration_settings={'stripe': {'settings': {
                'enabled': True, 'public_key': PUBLIC_KEY, 'secret_key': '', 'webhook_secret': ''}}})

    # -- change_type_of_stripe_payments ------------------------------------------------
    def change_seed(self, *, stop=False, unconfigured=False):
        from application.models import Payment
        self.base(unconfigured=unconfigured)
        now = datetime.now(timezone.utc)
        recent, old = now - timedelta(days=2), now - timedelta(days=20)
        # intent, type, paid, creation, payment date, deleted
        if stop:
            controls = [('pi_ok_2', 'default', True, recent, None, False), ('pi_nostatus', 'default', True, recent, None, False),
                        ('pi_ok_1', 'default', True, recent, None, False)]
        else:
            controls = [('pi_ok_1', 'default', True, recent, None, False),
                        ('pi_pending', 'default', True, recent, recent, False),
                        ('pi_missing', 'default', True, recent, None, False),
                        ('', 'default', True, recent, None, False),
                        ('pi_empty', 'default', True, recent, None, False),
                        ('pi old/ä', 'default', True, old, now - timedelta(days=3), False),
                        ('pi_card', 'default', True, recent, None, False),
                        ('pi_retry', 'default', True, now - timedelta(days=9, hours=23), None, False),
                        ('pi_outside', 'default', True, old, old, False),
                        ('pi_cash', 'cash', True, recent, None, False),
                        ('pi_unpaid', 'default', False, recent, None, False),
                        ('pi_deleted', 'default', True, recent, None, True),
                        (None, 'default', True, recent, None, False)]
        Payment.objects.bulk_create([Payment(payment_id=U(100 + i), payment_intent_id=intent, type=kind, paid=paid, creation_date=created,
            payment_date=paid_on, deleted=deleted, amount=Decimal('12.50'), sport_association_id=self.associations[0], user_id=self.athlete,
            description=f'Pagamento {i}') for i, (intent, kind, paid, created, paid_on, deleted) in enumerate(controls)])
        return self.snapshot()

    # -- generate_coupon_if_not_exists -----------------------------------------------
    def coupon_seed(self, *, stop=False, unconfigured=False):
        from application.models import SportAssociation
        self.base(unconfigured=unconfigured)
        if not stop:
            SportAssociation.original_objects.filter(denomination='Errore coupon').update(affiliate_code='HELD2345')
        return self.snapshot()

    # -- renew_memberships_payments ---------------------------------------------------
    def renew_seed(self, *, paid_existing=False):
        from application.models import Payment, Associate, Subscription
        from application.models.courses_models import Course, CourseSubscription
        self.base()
        association = self.associations[0]
        Associate.objects.bulk_create([Associate(associate_id=U(41), sport_association_id=association, first_name='Ada', last_name='Rossi')])
        Subscription.objects.bulk_create([Subscription(subscription_id=U(51), sport_association_id=association, associate_id=U(41),
            user_id=self.athlete)])
        Course.objects.bulk_create([Course(course_id=U(61), sport_association_id=association, title='Nuoto "Master"', status_flag=2),
                                    Course(course_id=U(62), sport_association_id=association, title='Archiviato', status_flag=1),
                                    Course(course_id=U(63), sport_association_id=None, title='Senza associazione', status_flag=2)])
        now = datetime.now(timezone.utc)
        due = datetime(2026, 1, 31, 10, 30, tzinfo=timezone.utc)
        # id offset, course, billed_until, frequency, fee, auto renewal, active, deleted, subscription
        controls = [(0, 61, due, 1, '30.00', True, True, False, True),
                    (1, 61, datetime(2025, 11, 30, 23, 15, tzinfo=timezone.utc), 3, '45.50', True, True, False, True),
                    (2, 61, due, 1, '30.00', False, True, False, True),
                    (3, 61, due, 1, '0.00', True, True, False, True),
                    (4, 62, due, 1, '30.00', True, True, False, True),
                    (5, 61, due, 1, '30.00', True, True, True, True),
                    (6, 61, now + timedelta(days=3), 1, '30.00', True, True, False, True),
                    (7, 61, due, 1, '30.00', True, True, False, False),
                    (8, 63, due, 2, '12.00', True, True, False, True),
                    (9, 61, due, 1, '30.00', True, False, False, True),
                    (10, 61, datetime(2024, 2, 29, 0, 0, tzinfo=timezone.utc), 12, '99.99', True, True, False, True)]
        CourseSubscription.objects.bulk_create([CourseSubscription(course_subscription_id=U(70 + n), course_id=U(course), billed_until=until,
            billed_frequency=frequency, membership_fee=Decimal(fee), auto_renewal=renewal, membership_active=active, deleted=deleted,
            subscription_id=U(51) if subscription else None, type=3, creation_date=due)
            for n, course, until, frequency, fee, renewal, active, deleted, subscription in controls])
        # An existing unpaid renewal of the second subscription's next period is reused.
        Payment.objects.bulk_create([Payment(payment_id=U(90), amount=Decimal('45.50'), paid=paid_existing, subject=2,
            sport_association_id=association, user_id=self.owner, description='Esistente',
            meta={'course_subscription_id': str(U(71)), 'billed_from': '2025-11-30', 'billed_until': '2026-02-28'})])
        return self.snapshot()

    def snapshot(self):
        from django.apps import apps
        PKS.update({m._meta.label: m._meta.pk.attname for m in apps.get_models(include_auto_created=True)})
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy
                and not m._meta.label.startswith('django_celery_results')}


def delta(before, after):
    """Per model: added rows in full, changed rows as their changed fields, removed rows marked.

    Seeds use the current time, so the Django and Go runs are compared by
    their effects on the seeded rows rather than by absolute seeded values."""
    out = {}
    for label, rows in after.items():
        pk = PKS[label]
        old = {str(row[pk]): row for row in before.get(label, [])}
        new = {str(row[pk]): row for row in rows}
        items = [row for key, row in new.items() if key not in old]
        items += [dict({'~pk': key}, **{field: [old[key][field], row[field]] for field in row if row[field] != old[key][field]})
                  for key, row in new.items() if key in old and row != old[key]]
        items += [{'~removed': key} for key in old if key not in new]
        out[label] = items
    return out


def normalized(state, since, until, seeded):
    """Random keys, codes and audit ids are replaced after their shape and window are checked."""
    replace = {}
    for row in state['application.Payment']:
        key = str(row.get('payment_id'))
        if 'payment_id' in row and key not in seeded:
            assert UUID4.match(key), 'created payment key'
            replace[key] = 'new-payment:' + str((row['meta'] or {}).get('course_subscription_id'))
            stamp = datetime.fromisoformat(row['meta']['renewal_timestamp'])
            assert since <= stamp <= until, 'renewal timestamp outside the run window'
    for row in state['application.SportAssociation']:
        code = row.get('affiliate_code', [None, None])[1]
        if code:
            assert CODE.match(code), 'promotion code shape'
            replace[code] = 'code:' + row['~pk']
    entries = []
    keys = {}
    for entry in state['auditlog.LogEntry']:
        entry = dict(entry)
        assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
        keys[entry['id']] = entry
        entry.pop('id'); entry['timestamp'] = 'run'
        entries.append(entry)
    indexes = []
    for index in state['application.AuditLogIndex']:
        index = dict(index)
        assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
        entry = keys[index['log_entry_id']]
        index.pop('audit_log_index_id'); index['created_at'] = 'run'
        index['log_entry_id'] = [entry['object_pk'], entry['action'], json.dumps(entry['changes'], sort_keys=True, default=str)]
        indexes.append(index)
    state = dict(state, **{'auditlog.LogEntry': entries, 'application.AuditLogIndex': indexes})
    through = 'application.CourseSubscription_membership_payments'
    state[through] = [{k: v for k, v in row.items() if k != 'id'} for row in state[through]]
    assert all('~pk' not in row for label in ('auditlog.LogEntry', 'application.AuditLogIndex', through) for row in state[label])
    text = json.dumps(state, default=str, sort_keys=True)
    text = re.sub(r'(renewal_timestamp(\\*)": \2")[0-9T:.+-]+(\\*")', r'\1run\3', text)
    for value, label in replace.items():
        text = text.replace(value, label)
    loaded = json.loads(text)
    return {label: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)) for label, rows in loaded.items()}, replace


class Broker:
    def __init__(self, port):
        import redis
        self.client = redis.Redis(host='127.0.0.1', port=port, db=0, password=REDIS_PASSWORD, socket_timeout=5)
        self.cache = redis.Redis(host='127.0.0.1', port=port, db=1, password=REDIS_PASSWORD, socket_timeout=5)

    def messages(self, expected, since, until):
        from TESTS.adapters.django.family_harness import check_celery_message
        raw = [item.decode() for item in reversed(self.client.lrange('celery', 0, -1))]
        self.client.delete('celery')
        assert len(raw) == len(expected), f'published {len(raw)} messages, expected {len(expected)}'
        checked = []
        for i, (item, spec) in enumerate(zip(raw, expected)):
            spec = dict(spec)
            if spec.pop('countdown', None) is not None:
                eta = json.loads(item)['headers']['eta']
                moment = datetime.fromisoformat(eta)
                assert since + 59 <= moment.timestamp() <= until + 61, 'retry eta outside countdown window'
                spec['eta'] = eta
            checked.append(check_celery_message(Contract(), item, spec, since=since, until=until, where=f':{i}'))
        return checked


class Contract:
    def require(self, condition, label):
        assert condition, label


class TasksRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries, redis_port, stub):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.redis_port, self.stub = redis_port, stub
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
            REDIS_HOST='127.0.0.1', REDIS_PORT=str(redis_port), REDIS_USERNAME='default', REDIS_PASSWORD=REDIS_PASSWORD,
            WHITELABEL_NAME=BRAND, STRIPE_PUBLIC_KEY=PUBLIC_KEY, STRIPE_KEY=SECRET_KEY,
            DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.env = dict(environment, STRIPE_API_BASE=stub.base)
        self.broker = Broker(redis_port)
        self.fixture = Fixture()
        self.oracles = {}

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE, application_name='fixture-tasks-d-' + name)
        self.connections.append(connection)
        return connection

    # -- Django oracle ------------------------------------------------------------------
    def django(self, task, seed, label, *, before_run=None, after_run=None):
        """Run the actual Django task on a fresh seed with the production broker, cache and Stripe base."""
        from contextlib import redirect_stdout
        import io
        import stripe
        from django.core import mail
        from django.test import override_settings
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from core.celery import app
        from TESTS.adapters.django.family_harness import _reset_celery_app
        from application import tasks
        before = seed()
        self.broker.client.delete('celery')
        self.stub.reset()
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES')
        saved = {key: app.conf[key] for key in keys}
        url = f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.redis_port}'
        app.conf.update(dict(zip(keys, (url, 'celery', None))))
        _reset_celery_app(app)
        caches = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{self.redis_port}/1',
                              'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': REDIS_PASSWORD,
                                          'USERNAME': 'default'}}}
        saved_base, saved_client, brand = stripe.api_base, stripe.default_http_client, tasks.WHITELABEL_NAME
        since, started = datetime.now(timezone.utc), time.time()
        mail.outbox = []
        try:
            stripe.api_base, stripe.default_http_client = self.stub.base, None
            tasks.WHITELABEL_NAME = BRAND
            with override_settings(WHITELABEL_NAME=BRAND, STRIPE_PUBLIC_KEY=PUBLIC_KEY, STRIPE_KEY=SECRET_KEY, CACHES=caches,
                                   DEFAULT_FROM_EMAIL=f'{BRAND} <noreply@example.test>',
                                   DEFAULT_SUPPORT_EMAIL=f'{BRAND} <support@example.test>'), \
                    redirect_stdout(io.StringIO()), CaptureQueriesContext(connection) as queries:
                if before_run:
                    before_run()
                assert getattr(tasks, task).run() is None
                if after_run:
                    after_run()
        finally:
            stripe.api_base, stripe.default_http_client = saved_base, saved_client
            tasks.WHITELABEL_NAME = brand
            app.conf.update(saved)
            _reset_celery_app(app)
        until = datetime.now(timezone.utc)
        if PROBE:
            with (self.output / f'probe-{label}.sql').open('w') as probe:
                for query in queries.captured_queries:
                    probe.write(query['sql'] + ';\n')
        requests = check_requests(self.stub.take())
        after = self.fixture.snapshot()
        seeded = {str(row['payment_id']) for row in before['application.Payment']}
        state, replace = normalized(delta(before, after), since, until, seeded)
        bodies = self.raw_messages(started)
        oracle = {'state': state, 'requests': self.requests(requests, replace), 'messages': bodies, 'before': before,
                  'after': after, 'outbox': list(mail.outbox), 'seeded': seeded}
        self.oracles[label] = oracle
        return oracle

    def raw_messages(self, started):
        raw = [item.decode() for item in reversed(self.broker.client.lrange('celery', 0, -1))]
        self.broker.client.delete('celery')
        return raw

    @staticmethod
    def requests(requests, replace):
        text = json.dumps(requests)
        for value, label in replace.items():
            text = text.replace(value, label)
        return json.loads(text)

    # -- Go -----------------------------------------------------------------------------
    def seed_job(self, kind, seed, *, version=1, payload='{}', association=NIL):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.seeded_state = seed()
        self.broker.client.delete('celery')
        self.stub.reset()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,1,clock_timestamp())''', (identifier, association, kind, version, identifier, payload))
        self.started, self.since = time.time(), datetime.now(timezone.utc)
        return identifier, before

    def go_matches(self, identifier, label, *, timeout=40, state='completed'):
        await_condition(lambda: self.job(identifier)[0] in ('completed', 'failed'), f'{label}: registered worker durable outcome', timeout)
        assert self.job(identifier)[0] == state, f'{label}: job {self.job(identifier)[0]}'
        oracle = self.oracles[label]
        until = datetime.now(timezone.utc)
        after = self.fixture.snapshot()
        state, replace = normalized(delta(self.seeded_state, after), self.since, until, oracle['seeded'])
        if state != oracle['state']:
            differences = {name: {'django': oracle['state'].get(name), 'go': state.get(name)}
                           for name in set(state) | set(oracle['state']) if state.get(name) != oracle['state'].get(name)}
            (self.output / f'difference-{label}.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert state == oracle['state'], f'{label}: Go state differs from the Django task on the same seed'
        requests = self.requests(check_requests(self.stub.take()), replace)
        if requests != oracle['requests']:
            (self.output / f'requests-{label}.json').write_text(json.dumps({'django': oracle['requests'], 'go': requests}, indent=1) + '\n')
        assert requests == oracle['requests'], f'{label}: Go Stripe requests differ from Django'
        return after

    def quiet(self, process, identifier, before):
        await_condition(self.observed_claim, 'actual registered worker queue query')
        assert process.poll() is None
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0)
        assert self.fixture.snapshot() == before and self.broker.client.llen('celery') == 0 and not self.stub.take()

    def run(self):
        f = self.fixture
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]

        # Django oracles first, with authored checks that they took the intended paths.
        change = self.django(CHANGE, f.change_seed, 'change')
        payments = {row['payment_id']: row for row in change['after']['application.Payment']}
        assert {pk.int - U(100).int for pk, row in payments.items() if row['type'] == 'stripe'} == {0, 5, 7}, 'change: updated rows'
        assert [r['path'] for r in change['requests']] == [
            '/v1/payment_intents/' + p for p in ('pi_ok_1', 'pi_pending', 'pi_missing', 'pi_empty', 'pi+old%2F%C3%A4', 'pi_card',
                                                 'pi_retry', 'pi_retry')], change['requests']
        assert not change['after']['auditlog.LogEntry']
        stop = self.django(CHANGE, lambda: f.change_seed(stop=True), 'change-stop')
        assert [r['path'] for r in stop['requests']] == ['/v1/payment_intents/pi_ok_2', '/v1/payment_intents/pi_nostatus']
        assert stop['after']['application.Payment'] == stop['before']['application.Payment']
        bare = self.django(CHANGE, lambda: f.change_seed(unconfigured=True), 'change-unconfigured')
        assert not bare['requests'] and bare['after'] == bare['before']

        coupon = self.django(COUPON, f.coupon_seed, 'coupon')
        assert [r['path'] for r in coupon['requests']] == ['/v1/coupons', '/v1/promotion_codes'] * 2
        assert len(coupon['after']['auditlog.LogEntry']) == 2 and len(coupon['after']['application.AuditLogIndex']) == 2
        coupon_stop = self.django(COUPON, lambda: f.coupon_seed(stop=True), 'coupon-stop')
        assert len(coupon_stop['after']['auditlog.LogEntry']) in (0, 1, 2)
        assert coupon_stop['requests'][-1]['path'] == '/v1/coupons'
        coupon_bare = self.django(COUPON, lambda: f.coupon_seed(unconfigured=True), 'coupon-unconfigured')
        assert not coupon_bare['requests'] and coupon_bare['after'] == coupon_bare['before']

        renew = self.django(RENEW, f.renew_seed, 'renew')
        created = [row for row in renew['after']['application.Payment'] if str(row['payment_id']) not in renew['seeded']]
        assert sorted(row['meta']['course_subscription_id'] for row in created) == sorted(str(U(70 + n)) for n in (0, 10)), created
        assert not renew['messages'] and not renew['outbox'] and not self.broker.cache.exists(LOCK_KEY)
        holder = {}

        def lock_row():
            holder['connection'] = self.connect('renew-row-lock')
            with holder['connection'].cursor() as cursor:
                cursor.execute('SELECT 1 FROM application_coursesubscription WHERE course_subscription_id=%s FOR UPDATE', (str(U(70)),))

        def unlock_row():
            holder.pop('connection').rollback()
        locked = self.django(RENEW, f.renew_seed, 'renew-locked', before_run=lock_row, after_run=unlock_row)
        assert len(locked['messages']) == 1 and not locked['outbox']
        self.check_retry(locked['messages'])
        paid = self.django(RENEW, lambda: f.renew_seed(paid_existing=True), 'renew-paid')
        assert len(paid['outbox']) == 1 and paid['after'] != paid['before']
        from django.core.cache import cache
        held = self.django(RENEW, f.renew_seed, 'renew-held', before_run=lambda: cache.add('renew_memberships_payments_lock', 'LOCKED', 300),
                           after_run=lambda: self.held_lock_value())
        assert held['after'] == held['before'] and not held['messages']
        self.broker.cache.delete(LOCK_KEY)
        if ORACLE_ONLY:
            return

        identifier, before = self.seed_job(CHANGE, f.change_seed)
        process = self.worker()
        self.quiet(process, identifier, before)
        self.stop(process)
        self.case('default Django owner does not claim or call Stripe')

        self.sql('DELETE FROM assozeta_go_jobs')
        self.handoff('go')
        process = self.worker('--task-timeout', '60s')
        identifier, _ = self.seed_job(CHANGE, f.change_seed)
        self.go_matches(identifier, 'change')
        self.case('change type: native worker reproduces the Django task')
        identifier, _ = self.seed_job(CHANGE, lambda: f.change_seed(stop=True))
        self.go_matches(identifier, 'change-stop')
        self.case('change type: a resource without status ends the task like Django')
        identifier, _ = self.seed_job(CHANGE, lambda: f.change_seed(unconfigured=True))
        self.go_matches(identifier, 'change-unconfigured')
        self.case('change type: unconfigured credentials return before any request')

        identifier, _ = self.seed_job(COUPON, f.coupon_seed)
        self.go_matches(identifier, 'coupon')
        self.case('coupons: native worker reproduces the Django task')
        identifier, _ = self.seed_job(COUPON, lambda: f.coupon_seed(stop=True))
        self.go_matches(identifier, 'coupon-stop')
        self.case('coupons: a Stripe error ends the task after the earlier saves')
        identifier, _ = self.seed_job(COUPON, lambda: f.coupon_seed(unconfigured=True))
        self.go_matches(identifier, 'coupon-unconfigured')
        self.case('coupons: a missing secret key ends the task before any request')

        identifier, _ = self.seed_job(RENEW, f.renew_seed)
        self.go_matches(identifier, 'renew')
        assert self.broker.client.llen('celery') == 0 and not self.broker.cache.exists(LOCK_KEY)
        self.case('renewals: native worker reproduces the Django task')

        self.stop(process)
        identifier, _ = self.seed_job(RENEW, f.renew_seed)
        lock_row()
        process = self.worker('--task-timeout', '60s')
        try:
            self.go_matches(identifier, 'renew-locked')
        finally:
            unlock_row()
        raw = self.raw_messages(self.started)
        self.check_retry(raw)
        assert [self.body(m) for m in raw] == [self.body(m) for m in self.oracles['renew-locked']['messages']]
        self.case('renewals: a locked subscription is skipped and the retry is published')

        # A lock held through Django's cache stops the Go run; the Go run's own
        # lock is the same django_redis entry, observed while the run is blocked.
        self.stop(process)
        from django.core.cache import cache as django_cache
        from django.test import override_settings
        caches = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{self.redis_port}/1',
                              'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': REDIS_PASSWORD,
                                          'USERNAME': 'default'}}}
        identifier, before = self.seed_job(RENEW, f.renew_seed)
        with override_settings(CACHES=caches):
            assert django_cache.add('renew_memberships_payments_lock', 'LOCKED', 300)
            process = self.worker('--task-timeout', '60s')
            self.go_matches(identifier, 'renew-held')
            assert django_cache.get('renew_memberships_payments_lock') == 'LOCKED'
            django_cache.delete('renew_memberships_payments_lock')
            identifier, before = self.seed_job(RENEW, f.renew_seed)
            blocker = self.connect('renew-table-lock')
            with blocker.cursor() as cursor:
                cursor.execute('LOCK TABLE application_payment IN ACCESS EXCLUSIVE MODE')
            try:
                await_condition(lambda: self.broker.cache.exists(LOCK_KEY), 'Go renewal lock in the Django cache', 20)
                assert django_cache.get('renew_memberships_payments_lock') == 'LOCKED'
                assert not django_cache.add('renew_memberships_payments_lock', 'LOCKED', 300)
                assert self.broker.cache.get(LOCK_KEY) == self.held_value, 'Go lock value differs from django_redis'
                assert 295_000 <= self.broker.cache.pttl(LOCK_KEY) <= 300_000
            finally:
                blocker.rollback()
            self.go_matches(identifier, 'renew')
            assert not self.broker.cache.exists(LOCK_KEY)
        self.case('renewals: a held cache lock skips the run and the Go lock is the Django cache entry')

        identifier, before = self.seed_job(RENEW, lambda: f.renew_seed(paid_existing=True))
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'renewal support e-mail refused', 20)
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('task_failed',)]
        assert self.fixture.snapshot() == before and self.broker.client.llen('celery') == 0 and not self.broker.cache.exists(LOCK_KEY)
        self.case('renewals: a run that would send the support e-mail is refused before any effect')

        for kind in (CHANGE, COUPON, RENEW):
            for kwargs, code in [({'version': 2}, 'unsupported_version'), ({'payload': '{"unexpected":true}'}, 'task_failed'),
                                 ({'association': str(self.fixture.associations[0])}, 'task_failed')]:
                identifier, before = self.seed_job(kind, f.change_seed, **kwargs)
                await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid envelope rejected')
                assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
                assert self.fixture.snapshot() == before and self.broker.client.llen('celery') == 0 and not self.stub.take()
        self.stop(process)
        self.case('unsupported version payload and tenant fail before business effects')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)

    def held_lock_value(self):
        value = self.broker.cache.get(LOCK_KEY)
        assert value is not None and 295_000 <= self.broker.cache.pttl(LOCK_KEY) <= 300_000
        self.held_value = value

    @staticmethod
    def body(raw):
        return json.loads(raw)['body']

    def check_retry(self, raw):
        assert len(raw) == 1
        message = json.loads(raw[0])
        assert json.loads(base64.b64decode(message['body'])) == [[], {'attempt': 2, 'max_attempts': 3},
                                                                 {'callbacks': None, 'errbacks': None, 'chain': None, 'chord': None}]
        from TESTS.adapters.django.family_harness import check_celery_message
        eta = message['headers']['eta']
        stamp = message['headers']['assozeta_published_at']
        assert abs(datetime.fromisoformat(eta).timestamp() - stamp - 60) < 1, 'retry countdown'
        check_celery_message(Contract(), raw[0], {'task': RENEW, 'args': [], 'kwargs': [['attempt', 2], ['max_attempts', 3]], 'eta': eta},
                             since=stamp - 1, until=stamp + 1, where=':retry')

    def close(self):
        try:
            super().close()
        finally:
            self.broker.client.close()
            self.broker.cache.close()


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/tasks-db-d-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    redis_server = shutil.which('redis-server')
    if not Path(postgres).is_file() or not shutil.which('go') or not redis_server:
        raise RuntimeError('Private PostgreSQL, Redis and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-tasks-d-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None; redis_process = None; stub = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            if not ORACLE_ONLY:
                for name in ('worker', 'migrate'):
                    subprocess.run(['go', 'build', '-race', '-o', str(work / name), './cmd/' + name], cwd=ROOT / 'BE_GO',
                                   env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            else:
                subprocess.run(['go', 'build', '-o', str(work / 'migrate'), './cmd/migrate'], cwd=ROOT / 'BE_GO',
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass', REDIS_PASSWORD,
                                              '--save', '', '--appendonly', 'no', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
            stub = Stub()
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = TasksRehearsal(work, output, environment, binaries, redis_port, stub)
            rehearsal.run()
        finally:
            try:
                if rehearsal:
                    result.update(scenarios=rehearsal.completed, scenario_count=len(rehearsal.completed))
                    rehearsal.close()
            finally:
                try:
                    if stub:
                        stub.close()
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    try:
                        if redis_process:
                            redis_process.terminate(); redis_process.wait(timeout=10)
                        if attempted:
                            try:
                                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work / 'postgres/postmaster.pid').exists():
                                    raise
                    finally:
                        if (work / 'postgres.log').exists():
                            shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                        report.write_text(json.dumps(result, indent=2) + '\n')
    paths = ['TESTS/adapters/go/tasks_db_d_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py', 'BE_GO/cmd/worker/main.go',
             'BE_GO/cmd/worker/tasks_db_d.go', 'BE_GO/internal/service/tasks_db_d.go', 'BE_GO/internal/repository/tasks_db_d.go',
             'BE_GO/internal/domain/tasks_db_d.go', 'BE_GO/internal/stripeapi/client.go', 'BE/application/tasks.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths if (ROOT / p).exists()})
    report.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
