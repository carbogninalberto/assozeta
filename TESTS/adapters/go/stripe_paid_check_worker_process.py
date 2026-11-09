"""Native mark_as_paid_payments_checking_stripe worker on actual Django-created current schema.

Private PostgreSQL, a private Redis (Celery broker and notification store on
DB 0, Django cache on DB 1, the Channels mirror channel) and a private Stripe
API stub (a local HTTP server that records every request and answers by
path). Django's task submits every payment to a ten-thread pool, so invoice
numbers follow the threads' completion order and its production outcome is
not deterministic. The oracle runs the actual Django task with the pool forced
to one thread (ThreadPoolExecutor patched in this adapter only) on a seed whose
query order is the payment_id order (asserted from the Stripe request log); the
native Go job, which processes the payments one at a time in payment_id order,
must then leave the identical complete managed-model state (invoice and
broadcast keys, audit ids and timestamps normalized after their shape and
window checks), send the same Stripe requests, publish the same Celery
messages, write the same notification store and cache entries and mirror the
same Channels pushes. Explicit fixture producers and ownership are not
installed activation.
"""
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go import tasks_db_d_worker_process as stripe_stub
from TESTS.adapters.go.email_tasks_worker_process import spec_from, Contract

DATABASE = 'assozeta_contract_stripe_paid_check_worker'
KIND = 'mark_as_paid_payments_checking_stripe'
REDIS_PASSWORD = 'private-redis-password-canary'
BRAND = 'Assozeta Fixture'
PUBLIC_KEY, SECRET_KEY = stripe_stub.PUBLIC_KEY, stripe_stub.SECRET_KEY
REALTIME = 'assozeta:realtime:v1'
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')
UUID1 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-1[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}')
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(\.\d+)?(\+00:00|Z)?')
PROBE = bool(os.environ.get('STRIPE_PAID_CHECK_PROBE'))
EXPECTED = {
    'default Django owner does not claim or call Stripe',
    'native worker reproduces the one-thread Django task',
    'a later run marks nothing',
    'unconfigured credentials return before any request',
    'unsupported version payload and tenant fail before business effects',
    'a payment outside the ported paid flow refuses the run before any effect',
}
stripe_stub.INTENTS.update({
    'pi_ok_3': (200, {'id': 'pi_ok_3', 'object': 'payment_intent', 'status': 'succeeded'}),
    'pi_ok_4': (200, {'id': 'pi_ok_4', 'object': 'payment_intent', 'status': 'succeeded'}),
    'pi_ok_5': (200, {'id': 'pi_ok_5', 'object': 'payment_intent', 'status': 'succeeded'}),
})


def U(n):
    return UUID(int=0xB8000000 + n)


class PaidFixture:
    def __init__(self):
        self.base = datetime.now(timezone.utc).replace(microsecond=0)

    def snapshot(self):
        from django.apps import apps
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy
                and not m._meta.label.startswith('django_celery')}

    def seed(self, variant='main'):
        from django.db import connection
        from application.models import User, SportAssociation, Payment, PaymentCategory, Invoice
        from application.models.invoices_models import InvoiceSuppliers
        from application.models.user_models import Associate
        from application.models.balance_sheet_models import CustomAccounts
        from instance.models import InstanceConfiguration
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, application_invoicesuppliers, application_invoice,
              application_payment, application_customaccounts, application_paymentcategory, application_associate,
              application_sportassociation, notifications_usernotificationsbroadcast, notifications_notificationsbroadcast,
              instance_instanceconfiguration, bakney_user RESTART IDENTITY CASCADE''')
        if variant == 'unconfigured':
            InstanceConfiguration.objects.create(self_hosted=True, integration_settings={'stripe': {'settings': {
                'enabled': True, 'public_key': PUBLIC_KEY, 'secret_key': '', 'webhook_secret': ''}}})
        users = [(1, 'owner1@example.test', 1, 'Olga', 'Owner'), (21, 'athlete21@example.test', 2, 'Anna', 'Atleta'),
                 (22, 'athlete22@example.test', 2, 'Bruno', 'Utente')]
        User.original_objects.bulk_create([User(user_id=U(n), username=f'paid-{n}', role=role, email=email, password='!private-fixture',
            first_name=first, last_name=last, date_joined=self.base, starting_number_invoices=3) for n, email, role, first, last in users])
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=U(11), user_id=U(1),
            denomination='Società Pagamenti', tax_code='00000000000')])
        CustomAccounts.objects.bulk_create([CustomAccounts(custom_account_id=U(12), sport_association_id=U(11), name='Banca',
            account_type=CustomAccounts.BANK, editable=False, enabled=True, initial_balance=Decimal('0.00'))])
        PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=U(13), name='Entrate e proventi da attività tipiche')])
        Associate.objects.bulk_create([Associate(associate_id=U(61), sport_association_id=U(11), first_name='Carla', last_name='Socia',
                                                 user_id=U(21))])
        recent = self.base - timedelta(days=3)
        Invoice.objects.bulk_create([Invoice(invoice_id=U(70), sport_association_id=U(11), creation_date=recent, membership_fee=Decimal('5.00'),
            activity_fee=Decimal('0.00'), number=7, description='Esistente')])
        # n, intent, subject, user, associate, type, extra
        rows = [
            (0, 'pi_ok_1', 1, 21, 61, 'default', {}),
            (1, 'pi_pending', 1, 21, 61, 'default', {}),
            (2, 'pi_missing', 0, 21, None, 'default', {}),
            (3, '', 0, 21, None, 'default', {}),
            (4, 'pi_card', 0, 21, None, 'default', {}),
            (5, 'pi_empty', 0, 21, None, 'default', {}),
            (6, 'pi_nostatus', 0, 21, None, 'default', {}),
            (7, 'pi_ok_2', 0, 22, None, 'stripe', {'description': 'Quota extra'}),
            (8, 'pi_ok_3', 1, None, 61, 'cash', {}),
            (9, 'pi_ok_4', 0, 21, None, 'default', {'invoice_id': U(70), 'description': 'Con fattura'}),
            (10, 'pi_ok_1', 0, 21, None, 'default', {'paid': True}),
            (11, 'pi_ok_1', 0, 21, None, 'default', {'deleted': True}),
            (12, 'pi_ok_5', 1, 22, None, 'default', {'meta_payment_categories': [{'payment_category_id': str(U(13)), 'amount': 5.5}]}),
            (13, None, 0, 21, None, 'default', {}),
        ]
        if variant == 'unported':
            # A membership fee (30 - 0.125) whose two-place rounding is an exact tie: not ported.
            rows[0] = (0, 'pi_ok_1', 1, 21, 61, 'default', {'meta_payment_categories': [{'amount': 0.125}]})
        Payment.objects.bulk_create([Payment(payment_id=U(100 + n), payment_intent_id=intent, subject=subject, user_id=U(u) if u else None,
            associate_id=U(a) if a else None, type=kind, amount=Decimal('30.00'), sport_association_id=U(11), creation_date=recent,
            payment_category_id=U(13), custom_accounts_id=U(12), **extra) for n, intent, subject, u, a, kind, extra in rows])
        InvoiceSuppliers.objects.bulk_create([InvoiceSuppliers(invoice_supplier_id=U(80), invoice_identifier='F-1', amount=Decimal('30.00'),
            sport_association_id=U(11), payment_id=U(109), paid=False)])
        # NotificationManager() creates the 'all_notifications' broadcast on first use: seeded for both runtimes.
        from notifications.models import NotificationsBroadcast
        NotificationsBroadcast.objects.create(broadcast_id=U(90), broadcast_name='all_notifications')
        with connection.cursor() as cursor:
            cursor.execute('ANALYZE application_payment, application_sportassociation, bakney_user, application_invoice')
        return self.snapshot()


def delta(before, after):
    from django.apps import apps
    pks = {m._meta.label: m._meta.pk.attname for m in apps.get_models(include_auto_created=True)}
    out = {}
    for label, rows in after.items():
        pk = pks[label]
        old = {str(row[pk]): row for row in before.get(label, [])}
        new = {str(row[pk]): row for row in rows}
        items = [row for key, row in new.items() if key not in old]
        items += [dict({'~pk': key}, **{f: [old[key][f], row[f]] for f in row if row[f] != old[key][f]})
                  for key, row in new.items() if key in old and row != old[key]]
        items += [{'~removed': key} for key in old if key not in new]
        out[label] = items
    return out


def labels(after, seeded):
    """Created invoice and broadcast keys get stable names."""
    names = {}
    for row in after['application.Invoice']:
        if str(row['invoice_id']) not in seeded:
            names[str(row['invoice_id'])] = f"invoice:{row['number']}"
    for row in after['notifications.NotificationsBroadcast']:
        if str(row['broadcast_id']) not in seeded:
            names[str(row['broadcast_id'])] = f"broadcast:{row['broadcast_name']}"
    for row in after['notifications.UserNotificationsBroadcast']:
        if str(row['user_broadcast_id']) not in seeded:
            names[str(row['user_broadcast_id'])] = f"user-broadcast:{row['user_id']}"
    return names


def normalize_text(text, names, since, until):
    for value, label in names.items():
        text = text.replace(value, label)
    assert not UUID4.search(text.replace(str(U(0))[:8], '')), 'unlabelled random key: ' + UUID4.search(text).group(0)

    def stamp(match):
        value = datetime.fromisoformat(match.group(0).replace('Z', '+00:00'))
        aware = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return 'run' if since - timedelta(seconds=1) <= aware <= until + timedelta(seconds=1) else match.group(0)
    return STAMP.sub(stamp, text)


def normalized(state, since, until, names):
    entries, keys = [], {}
    for entry in state['auditlog.LogEntry']:
        entry = dict(entry)
        keys[entry['id']] = entry
        entry.pop('id')
        entries.append(entry)
    indexes = []
    for index in state['application.AuditLogIndex']:
        index = dict(index)
        entry = keys[index['log_entry_id']]
        index.pop('audit_log_index_id')
        index['log_entry_id'] = [entry['object_pk'], entry['action'], json.dumps(entry['changes'], sort_keys=True, default=str)]
        indexes.append(index)
    state = dict(state, **{'auditlog.LogEntry': entries, 'application.AuditLogIndex': indexes})
    text = normalize_text(json.dumps(state, default=str, sort_keys=True), names, since, until)
    loaded = json.loads(text)
    return {label: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)) for label, rows in loaded.items()}


class Realtime:
    """Records the Channels mirror envelopes published on the realtime channel."""

    def __init__(self, port):
        import redis
        self.client = redis.Redis(host='127.0.0.1', port=port, password=REDIS_PASSWORD, socket_timeout=5)
        self.pubsub = self.client.pubsub()
        self.pubsub.subscribe(REALTIME)
        self.items, self.lock, self.stopped = [], threading.Lock(), False
        self.thread = threading.Thread(target=self.listen, daemon=True)
        self.thread.start()

    def listen(self):
        while not self.stopped:
            message = self.pubsub.get_message(timeout=0.2)
            if message and message['type'] == 'message':
                with self.lock:
                    self.items.append(message['data'].decode())

    def take(self):
        time.sleep(0.3)
        with self.lock:
            items, self.items = self.items, []
        return items

    def close(self):
        self.stopped = True
        self.thread.join(5)
        self.pubsub.close()
        self.client.close()


class PaidRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries, redis_port, stub):
        import redis
        self.work, self.output, self.env, self.binaries = work, output, dict(environment, STRIPE_API_BASE=stub.base), binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.redis_port, self.stub = redis_port, stub
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
        self.store = redis.Redis(host='127.0.0.1', port=redis_port, db=0, password=REDIS_PASSWORD, socket_timeout=5)
        self.cache = redis.Redis(host='127.0.0.1', port=redis_port, db=1, password=REDIS_PASSWORD, socket_timeout=5)
        self.realtime = Realtime(redis_port)
        # NotificationManager's module-level pool, pointed at the private Redis.
        from notifications import utils as notification_utils
        notification_utils.REDIS_POOL = redis.ConnectionPool(host='127.0.0.1', port=redis_port, password=REDIS_PASSWORD, username='default',
                                                             socket_connect_timeout=5, socket_timeout=5, decode_responses=False)
        self.fixture = PaidFixture()
        self.oracles = {variant: self.django(variant) for variant in ('main', 'unconfigured')}

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE, application_name='fixture-paid-' + name)
        self.connections.append(connection)
        return connection

    def reset_redis(self):
        self.store.flushdb()
        self.cache.flushdb()
        self.store.set('all_notifications', json.dumps({'messages': ''}))
        self.realtime.take()
        self.stub.reset()

    def messages(self):
        raw = [item.decode() for item in reversed(self.store.lrange('celery', 0, -1))]
        self.store.delete('celery')
        return raw

    def redis_state(self):
        state = {}
        for db, client in (('store', self.store), ('cache', self.cache)):
            for key in sorted(client.keys('*')):
                name = key.decode()
                if name == 'celery' or name.startswith('_kombu') or name.startswith('unacked'):
                    continue
                kind = client.type(key).decode()
                value = client.get(key) if kind == 'string' else kind
                text = value.decode() if isinstance(value, bytes) and db == 'store' else base64.b64encode(value).decode() \
                    if isinstance(value, bytes) else value
                state[f'{db}:{name}'] = text
        return state

    def outcome(self, before, since, until, started):
        """State, Stripe requests, Celery bodies, Redis store/cache and Channels mirror, normalized."""
        after = self.fixture.snapshot()
        seeded = {str(v) for rows in before.values() for row in rows for v in row.values() if isinstance(v, UUID)}
        names = labels(after, seeded)
        raw = self.messages()
        for i, item in enumerate(raw):
            from TESTS.adapters.django.family_harness import check_celery_message
            check_celery_message(Contract(), item, spec_from(item), since=started, until=time.time(), where=f':{i}')
        bodies = [json.loads(base64.b64decode(json.loads(item)['body'])) for item in raw]
        published = []
        for body in bodies:
            (invoice, token), kwargs, _ = body
            assert UUID4.fullmatch(invoice) and token is None and kwargs == {}, body
            published.append(names.get(invoice, 'invoice:rolled-back'))
        redis_state = {key: normalize_text(UUID1.sub('uuid1', re.sub(r'"expires": \d+', '"expires": "ttl"', value)), names, since, until)
                       for key, value in self.redis_state().items()}
        pushes = [normalize_text(UUID1.sub('uuid1', self.issued(item, since, until)), names, since, until) for item in self.realtime.take()]
        requests = stripe_stub.check_requests(self.stub.take())
        return {'state': normalized(delta(before, after), since, until, names), 'requests': requests, 'published': published,
                'redis': redis_state, 'pushes': pushes}, after

    @staticmethod
    def issued(item, since, until):
        """The pushed notification's issue time (epoch and ctime) must fall in the run window."""
        envelope = json.loads(item)
        notification = envelope['message']['notification']
        assert since.timestamp() - 1 <= notification['issued'] <= until.timestamp() + 1, 'push issued outside the run window'
        assert datetime.strptime(notification['date'], '%a %b %d %H:%M:%S %Y') == datetime.fromtimestamp(notification['issued']).replace(
            microsecond=0), 'push date is not the issued ctime'
        notification['issued'] = notification['date'] = 'run'
        return json.dumps(envelope, ensure_ascii=False)

    # -- Django oracle --------------------------------------------------------------
    def django(self, variant):
        """The actual Django task on a fresh seed, its pool forced to one thread."""
        from contextlib import redirect_stdout, ExitStack
        import concurrent.futures
        import io
        import stripe
        from unittest.mock import patch
        from django.test import override_settings
        from channels.layers import channel_layers
        from core.celery import app
        from notifications.services import NotificationService
        from TESTS.adapters.django.family_harness import _reset_celery_app
        from application import tasks
        real = concurrent.futures.ThreadPoolExecutor

        def one_thread(max_workers=None, *args, **kwargs):
            assert max_workers == 10, max_workers
            return real(1, *args, **kwargs)
        before = self.fixture.seed(variant)
        self.reset_redis()
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.redis_port}', 'celery', None))))
        _reset_celery_app(app)
        caches = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{self.redis_port}/1',
                              'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': REDIS_PASSWORD,
                                          'USERNAME': 'default'}}}
        layers = {'default': {'BACKEND': 'notifications.realtime_layer.GoMirroredRedisChannelLayer', 'CONFIG': {'hosts': [{
            'address': f'redis://127.0.0.1:{self.redis_port}/0', 'password': REDIS_PASSWORD}]}}}
        saved_base, saved_client = stripe.api_base, stripe.default_http_client
        since, started = datetime.now(timezone.utc), time.time()
        try:
            with ExitStack() as stack:
                stripe.api_base, stripe.default_http_client = self.stub.base, None
                stack.enter_context(override_settings(WHITELABEL_NAME=BRAND, STRIPE_PUBLIC_KEY=PUBLIC_KEY, STRIPE_KEY=SECRET_KEY,
                                                      CACHES=caches, CHANNEL_LAYERS=layers, DOCUMENT_BYPASS_TOKEN='bypass-placeholder'))
                stack.enter_context(patch.object(concurrent.futures, 'ThreadPoolExecutor', one_thread))
                stack.enter_context(redirect_stdout(io.StringIO()))
                channel_layers.backends = {}
                NotificationService._manager = None
                assert tasks.mark_as_paid_payments_checking_stripe.run() is None
        finally:
            stripe.api_base, stripe.default_http_client = saved_base, saved_client
            channel_layers.backends = {}
            NotificationService._manager = None
            app.conf.update(saved)
            _reset_celery_app(app)
        until = datetime.now(timezone.utc)
        outcome, after = self.outcome(before, since, until, started)
        self.authored_checks(variant, outcome, after)
        return outcome

    def authored_checks(self, variant, outcome, after):
        paths = [r['path'] for r in outcome['requests']]
        if variant == 'unconfigured':
            assert not paths and not outcome['published'] and not outcome['pushes'], outcome
            return
        # The one-thread run reached the payments in payment_id order.
        assert paths == ['/v1/payment_intents/' + p for p in ('pi_ok_1', 'pi_pending', 'pi_missing', 'pi_card', 'pi_empty', 'pi_nostatus',
                                                              'pi_ok_2', 'pi_ok_3', 'pi_ok_4', 'pi_ok_5')], paths
        # print_document_invoice runs on commit: the payment without user (8, rolled back) publishes nothing.
        assert outcome['published'] == ['invoice:8', 'invoice:9', 'invoice:10'], outcome['published']
        payments = {row['payment_id']: row for row in after['application.Payment']}
        # mark_payment_as_paid saves every unpaid payment paid (9 keeps its existing invoice); 8 rolls back.
        assert [n for n in range(14) if payments[U(100 + n)]['paid']] == [0, 7, 9, 10, 12]
        assert payments[U(108)]['type'] == 'cash' and payments[U(109)]['type'] == 'stripe' and payments[U(109)]['invoice_id'] == U(70)
        # The task's type save, then mark_payment_as_paid's update_fields save (paid, payment date; type already
        # 'stripe'), then the save that records the invoice.
        # A payment that already has an invoice gets no further save.
        for n, expected, invoiced in ((0, [['type'], ['paid', 'payment_date']], True), (7, [['paid', 'payment_date']], True),
                                      (9, [['type'], ['paid', 'payment_date']], False), (12, [['type'], ['paid', 'payment_date']], True)):
            entries = sorted((e for e in after['auditlog.LogEntry'] if e['object_pk'] == str(U(100 + n))), key=lambda e: e['id'])
            changes = [e['changes'] if isinstance(e['changes'], dict) else json.loads(e['changes']) for e in entries]
            if invoiced:
                assert [sorted(c) for c in changes[:-1]] == expected and 'invoice' in changes[-1], (n, changes)
            else:
                assert [sorted(c) for c in changes] == expected, (n, changes)
        assert [row['paid'] for row in after['application.InvoiceSuppliers']] == [True]
        assert len(outcome['pushes']) == 3, outcome['pushes']

    # -- Go ---------------------------------------------------------------------------
    def seed_job(self, *, variant='main', version=1, payload='{}', association=NIL, reseed=True):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(variant) if reseed else self.fixture.snapshot()
        self.reset_redis()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,1,clock_timestamp())''', (identifier, association, KIND, version, identifier, payload))
        self.started, self.since = time.time(), datetime.now(timezone.utc)
        return identifier, before

    def go_matches(self, identifier, before, variant):
        await_condition(lambda: self.job(identifier)[0] in ('completed', 'failed'), 'registered stripe paid check worker durable outcome', 40)
        assert self.job(identifier)[:3] == ('completed', 1, ''), self.job(identifier)
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        outcome, _ = self.outcome(before, self.since, datetime.now(timezone.utc), self.started)
        oracle = self.oracles[variant]
        for part in ('state', 'requests', 'published', 'redis', 'pushes'):
            if outcome[part] != oracle[part]:
                (self.output / f'difference-{variant}-{part}.json').write_text(
                    json.dumps({'django': oracle[part], 'go': outcome[part]}, indent=1, default=str, ensure_ascii=False) + '\n')
            assert outcome[part] == oracle[part], f'{variant}: Go {part} differs from the one-thread Django task'

    def quiet(self):
        identifier, before = self.seed_job()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        assert not self.messages() and not self.stub.take()
        self.stop(process)
        return identifier, before

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.quiet()
        self.case('default Django owner does not claim or call Stripe')

        self.handoff('go')
        process = self.worker('--task-timeout', '60s')
        self.go_matches(identifier, before, 'main')
        self.case('native worker reproduces the one-thread Django task')
        later = self.fixture.snapshot()
        identifier, _ = self.seed_job(reseed=False)
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'later stripe paid check run')
        requests = [r['path'] for r in stripe_stub.check_requests(self.stub.take())]
        assert requests == ['/v1/payment_intents/' + p for p in ('pi_pending', 'pi_missing', 'pi_card', 'pi_empty', 'pi_nostatus',
                                                                 'pi_ok_3')], requests
        # Nothing changes: the payment without user rolls back again and its on-commit invoice task is discarded.
        changed = {label: rows for label, rows in delta(later, self.fixture.snapshot()).items() if rows}
        assert changed == {}, changed
        assert self.messages() == []
        self.case('a later run marks nothing')
        identifier, before = self.seed_job(variant='unconfigured')
        self.go_matches(identifier, before, 'unconfigured')
        self.case('unconfigured credentials return before any request')

        for kwargs, code in [({'version': 2}, 'unsupported_version'), ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(U(11))}, 'task_failed')]:
            identifier, before = self.seed_job(**kwargs)
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid envelope rejected')
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before and not self.messages() and not self.stub.take()
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, before = self.seed_job(variant='unported')
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'unported paid flow refused')
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('task_failed',)]
        assert self.fixture.snapshot() == before and not self.messages() and not self.stub.take() and not self.realtime.take()
        self.stop(process)
        self.case('a payment outside the ported paid flow refuses the run before any effect')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)

    def close(self):
        try:
            super().close()
        finally:
            self.realtime.close()
            self.store.close()
            self.cache.close()


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/stripe-paid-check-worker-process'
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
    with tempfile.TemporaryDirectory(prefix='az-paid-check-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None; redis_process = None; stub = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work / name), './cmd/' + name], cwd=ROOT / 'BE_GO',
                               env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
            redis_port = free_port()
            redis_process = subprocess.Popen([redis_server, '--port', str(redis_port), '--bind', '127.0.0.1', '--requirepass', REDIS_PASSWORD,
                                              '--save', '', '--appendonly', 'no', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
            stub = stripe_stub.Stub()
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            rehearsal = PaidRehearsal(work, output, environment, binaries, redis_port, stub)
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
    paths = ['TESTS/adapters/go/stripe_paid_check_worker_process.py', 'TESTS/adapters/go/tasks_db_d_worker_process.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/cmd/worker/tasks_db_f.go', 'BE_GO/internal/service/stripe_paid_check.go',
             'BE_GO/internal/repository/stripe_paid_check.go', 'BE_GO/internal/domain/stripe_paid_check.go',
             'BE_GO/internal/service/payment_paid_flow.go', 'BE_GO/internal/service/stripe_routes.go', 'BE/application/tasks.py',
             'BE/application/views/stripe_views.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths if (ROOT / p).exists()})
    report.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
