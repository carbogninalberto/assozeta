"""Native association_quote_assign worker on actual Django-created current schema.

Private PostgreSQL, a private Redis broker (DB 0, default queue ``celery``)
and a local fake SMTP server (STARTTLS, throwaway certificate) recording every
client byte. Each seed is first processed by the actual Django task (no
arguments, as Beat publishes it); the native Go job must then leave the
identical complete managed-model state (created payment keys, audit ids and
timestamps normalized after their shape and window checks), publish the same
Celery messages (protocol 2 envelope checked; bodies equal to Django's) and
produce the same SMTP transcripts after the delayed sends (MIME boundary and
EHLO name normalized). Explicit fixture producers and ownership are not
installed activation.
"""
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from uuid import UUID, uuid4

import psycopg2

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.worker_process import Rehearsal, await_condition, NIL
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.email_tasks_worker_process import FakeSMTP, tls_context, spec_from, Contract

DATABASE = 'assozeta_contract_quote_assign_worker'
KIND = 'association_quote_assign'
REDIS_PASSWORD = 'private-redis-password-canary'
BRAND = 'Assozeta Fixture'
APP_URL = 'https://app.example.test'
HOST_USER = 'noreply@example.test'
SMTP_PASSWORD = 'smtp-fixture-secret'
TIMER_WAIT = 33
UUID4 = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z')
VARIANTS = ('main', 'stop')
PROBE = bool(os.environ.get('QUOTE_ASSIGN_PROBE'))
ORDER_PROBE = bool(os.environ.get('QUOTE_ASSIGN_ORDER_PROBE'))
EXPECTED = {
    'default Django owner does not claim or send',
    'native worker reproduces the Django task, its messages and delayed sends',
    'a subscription without associate ends the run like Django',
    'a later run on the assigned state creates nothing',
    'unsupported version payload and tenant fail before business effects',
    'a fee outside the ported conversions refuses the run before any effect',
    'an SMTP subject outside the ported subset refuses the run before any effect',
}


def U(n):
    return UUID(int=0xA7000000 + n)


def plan(pid, previous, fee, name, *, day, month, auto=True, advanced=True):
    return {'id': pid, 'name': name, 'subscription_fee': fee, 'previous_subscription_fee_plan': previous, 'auto_assign': auto,
            'advanced_options': advanced, 'from_day': day, 'from_month': month, 'to_day': 28, 'to_month': 2}


class QuoteFixture:
    def __init__(self, smtp_port):
        self.base = datetime.now(timezone.utc).replace(microsecond=0)
        self.smtp_port = smtp_port

    def snapshot(self):
        from django.apps import apps
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    def seed(self, variant='main'):
        from django.db import connection
        from application.models import User, SportAssociation, Payment, PaymentCategory
        from application.models.user_models import Associate
        from application.models.subscriptions_models import Subscription
        from application.models.balance_sheet_models import CustomAccounts
        from communications.models import CommunicationConfiguration
        from instance.models import InstanceConfiguration
        today = self.base.date()
        other = today + timedelta(days=1)
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, communications_communicationconfiguration,
              application_subscription, application_payment, application_customaccounts, application_paymentcategory,
              application_associate, application_sportassociation, instance_instanceconfiguration, bakney_user RESTART IDENTITY CASCADE''')
        InstanceConfiguration.objects.create(domain='fixture.example.test', self_hosted=True, primary_color='#351DC2', name='Polisportiva <Quote>')
        users = [(1, 'owner1@example.test', 1), (2, 'owner2@example.test', 1), (3, 'owner3@example.test', 1),
                 (4, 'owner4@example.test', 1), (5, 'owner5@example.test', 1), (6, 'owner6@example.test', 1),
                 (21, 'Athlete.One@Example.TEST', 2), (22, 'athlete2@example.test', 2), (23, 'athlete3@example.test', 2),
                 (24, 'athlete4@example.test', 2)]
        User.original_objects.bulk_create([User(user_id=U(n), username=f'quote-{n}', role=role, email=email, password='!private-fixture',
            first_name=f'Nome{n}', last_name='Fixture', date_joined=self.base) for n, email, role in users])
        day, month = today.day, today.month
        fee_two = 12.5
        if variant == 'unported':
            fee_two = '1e2'  # Decimal('1e2') in Django; exponent text is not a ported fee conversion
        plans = {
            1: [plan('plan-new', 'plan-old', 30, 'Quota "Seconda" & <rata>', day=day, month=month),
                plan('plan-float', 'plan-old', fee_two, 'Quota float', day=str(day), month=month),
                plan('plan-str', 'plan-old-2', '45.00', 'Quota stringa', day=day, month=month),
                plan('plan-tomorrow', 'plan-old', 99, 'Domani', day=other.day, month=other.month),
                plan('plan-manual', 'plan-old', 98, 'Manuale', day=day, month=month, auto=False),
                plan('plan-empty-previous', '', 97, 'Vuoto', day=day, month=month)],
            2: [plan(7, 'plan-old', 20, "Quota SMTP" if variant != 'smtp' else 'Quota\nSMTP', day=day, month=month)],
            3: [plan('plan-single', 'plan-old', 15, 'Singola', day=day, month=month)],
            4: [plan('plan-basic', 'plan-old', 15, 'Base', day=day, month=month, advanced=False)],
            5: [plan('plan-deleted', 'plan-old', 15, 'Eliminata', day=day, month=month)],
            6: [plan('plan-mail', 'plan-old', 25, 'Quota mail', day=day, month=month)],
        }
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=U(10 + n), user_id=U(n),
            denomination=name, tax_code='00000000000', multiple_subscription_fee=n != 3, subscription_fee_plans=plans[n], deleted=n == 5)
            for n, name in [(1, "Società Uno & C."), (2, 'Due SMTP'), (3, 'Tre singola'), (4, 'Quattro base'), (5, 'Cinque eliminata'),
                            (6, 'Sei mail')]])
        CustomAccounts.objects.bulk_create([CustomAccounts(custom_account_id=U(30 + n), sport_association_id=U(10 + n), name='Banca',
            account_type=CustomAccounts.BANK, editable=False, enabled=True, initial_balance=Decimal('0.00')) for n in (1, 2)])
        PaymentCategory.objects.bulk_create([PaymentCategory(payment_category_id=U(40), name='Entrate e proventi da attività tipiche')])
        CommunicationConfiguration.objects.bulk_create([CommunicationConfiguration(communication_configuration_id=U(50),
            sport_association_id=U(12), email_smtp_host='127.0.0.1', email_smtp_port=self.smtp_port, email_smtp_user='mailer@example.test',
            email_smtp_password=SMTP_PASSWORD, email_sender_name='Due SMTP', email_encryption='TLS', daily_email_limit=1000,
            daily_email_balance=4)])
        associates = [(61, 11, 'Anna', "D'Amico"), (62, 11, None, 'Senza nome'), (63, 11, 'Carla', 'Esistente'),
                      (64, 12, 'Dario', 'Smtp'), (65, 13, 'Elena', 'Singola'), (66, 16, 'Fabio', 'Mail'), (67, 11, 'Gaia', 'Owner')]
        Associate.objects.bulk_create([Associate(associate_id=U(n), sport_association_id=U(a), first_name=f, last_name=l)
                                       for n, a, f, l in associates])
        recent, old = self.base - timedelta(days=2), self.base - timedelta(days=500)
        previous = lambda pid: {'subscription_data': {'id': pid, 'name': 'Precedente', 'subscription_fee': 10}}
        # n, association, user, associate, payment meta, subscription meta, extra
        subscriptions = [
            (101, 11, 21, 61, previous('plan-old'), {'plan_id': 'plan-old'}, {}),
            (102, 11, 22, 62, previous('PLAN-OLD'), {'plan_id': 'x'}, {}),
            (103, 11, 23, 63, previous('plan-old'), {'plan_id': 'x'}, {}),
            (104, 11, 1, 67, previous('plan-old'), {'plan_id': 'x'}, {}),
            (105, 11, 21, 61, previous('plan-old-2'), {'plan_id': 'x'}, {}),
            (106, 11, 21, 61, previous('plan-old'), {'plan_id': 'x'}, {'archived': True}),
            (107, 11, 21, 61, previous('plan-old'), {'plan_id': 'x'}, {'deleted': True}),
            (108, 11, 21, 61, previous('plan-old'), {'plan_id': 'x'}, {'creation_date': old}),
            (109, 11, 21, 61, previous('plan-xld'), {'plan_id': 'x'}, {}),
            (110, 11, 21, 61, None, {'plan_id': 'x'}, {}),
            (111, 11, 21, 61, previous('plan-old'), None, {}),
            (112, 11, 21, 61, previous('plan-old'), '{"plan_id": null}', {}),
            (113, 12, 24, 64, previous('plan-old'), {'plan_id': 'x'}, {}),
            (114, 13, 21, 65, previous('plan-old'), {'plan_id': 'x'}, {}),
            (115, 14, 21, 65, previous('plan-old'), {'plan_id': 'x'}, {}),
            (116, 15, 21, 65, previous('plan-old'), {'plan_id': 'x'}, {}),
            (117, 16, 22, 66, previous('plan-old'), {'plan_id': 'x'}, {}),
            (118, 11, 21, 61, previous('plan-old'), {'plan_id': 'x'}, {}),
        ]
        if variant == 'stop':
            subscriptions.insert(3, (119, 11, 24, None, previous('plan-old'), {'plan_id': 'x'}, {}))
        payments = [Payment(payment_id=U(200 + n), amount=Decimal('10.00'), subject=1, user_id=U(u), sport_association_id=U(a),
                            meta=pm, paid=True, creation_date=recent) for n, a, u, _, pm, _, _ in subscriptions]
        # An existing quote of plan-new for user 23 / associate 63 (30.00): skipped.
        payments.append(Payment(payment_id=U(390), amount=Decimal('30.00'), subject=1, user_id=U(23), associate_id=U(63),
                                sport_association_id=U(11), meta={'subscription_data': {'id': 'plan-new'}}, creation_date=recent))
        Payment.objects.bulk_create(payments)
        Subscription._base_manager.bulk_create([Subscription(subscription_id=U(n), sport_association_id=U(a), user_id=U(u),
            associate_id=U(p) if p else None, payment_id=U(200 + n), meta=sm, **{'creation_date': recent, **extra})
            for n, a, u, p, _, sm, extra in subscriptions])
        # Fresh planner statistics on every seed: the unordered querysets then
        # get the same plan, hence the same row order, in the Django and Go runs.
        with connection.cursor() as cursor:
            cursor.execute('ANALYZE application_sportassociation, bakney_user, application_subscription, application_payment, '
                           'application_associate, application_customaccounts, application_paymentcategory, '
                           'communications_communicationconfiguration')
        return self.snapshot()


def delta(before, after):
    """Per model: added rows in full, changed rows as their changed fields, removed rows marked."""
    from django.apps import apps
    pks = {m._meta.label: m._meta.pk.attname for m in apps.get_models()}
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


def normalized(state, since, until, seeded):
    """Created payment keys and creation dates, audit ids and timestamps are checked, then replaced."""
    replace = {}
    for row in state['application.Payment']:
        key = str(row.get('payment_id'))
        if 'payment_id' in row and key not in seeded:
            assert UUID4.match(key), 'created payment key'
            assert since <= row['creation_date'] <= until, 'payment creation outside the run window'
            replace[key] = 'new-payment:' + str(row['user_id']) + ':' + str(row['meta']['subscription_data']['id'])
            row['creation_date'] = 'run'
    entries, keys = [], {}
    for entry in state['auditlog.LogEntry']:
        entry = dict(entry)
        assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
        if 'creation_date' in entry['changes']:
            stamp = datetime.fromisoformat(entry['changes']['creation_date'][1])
            assert since.replace(tzinfo=None) <= stamp <= until.replace(tzinfo=None), 'audited creation date outside the run window'
            entry['changes']['creation_date'][1] = 'run'
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
    text = json.dumps(state, default=str, sort_keys=True)
    for value, label in replace.items():
        text = text.replace(value, label)
    loaded = json.loads(text)
    return {label: sorted(rows, key=lambda row: json.dumps(row, sort_keys=True)) for label, rows in loaded.items()}


class QuoteRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries, redis_port, smtp):
        import redis
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.smtp = smtp
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID='fixture', AWS_SECRET_ACCESS_KEY='fixture',
            AWS_STORAGE_BUCKET_NAME='fixture', AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL='http://127.0.0.1:9000', AWS_LOCATION='private',
            REDIS_HOST='127.0.0.1', REDIS_PORT=str(redis_port), REDIS_USERNAME='default', REDIS_PASSWORD=REDIS_PASSWORD,
            WHITELABEL_NAME=BRAND, IS_WHITELABEL='False', APP_URL=APP_URL, EMAIL_HOST_USER=HOST_USER,
            DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.broker = redis.Redis(host='127.0.0.1', port=redis_port, db=0, password=REDIS_PASSWORD, socket_timeout=5)
        self.redis_port = redis_port
        self.fixture = QuoteFixture(smtp.port)
        self.django = {variant: self.django_expected(variant) for variant in VARIANTS}
        if ORDER_PROBE:
            self.order_probe()

    def order_probe(self):
        from django.db import connection
        self.fixture.seed('stop')
        sql = [line for line in (self.output / 'probe-stop.sql').read_text().splitlines()
               if line.startswith('SELECT "application_subscription"."subscription_id"')][0].rstrip(';')
        with connection.cursor() as cursor:
            cursor.execute(sql)
            django_order = [str(row[0])[-2:] for row in cursor.fetchall()]
            cursor.execute('EXPLAIN ' + sql)
            django_plan = [row[0] for row in cursor.fetchall()]
        with self.connection.cursor() as cursor:
            cursor.execute(sql)
            raw_order = [str(row[0])[-2:] for row in cursor.fetchall()]
            cursor.execute('EXPLAIN ' + sql)
            raw_plan = [row[0] for row in cursor.fetchall()]
        (self.output / 'order-probe.json').write_text(json.dumps({'django': django_order, 'raw': raw_order, 'django_plan': django_plan,
                                                                  'raw_plan': raw_plan}, indent=1))
        raise SystemExit('order probe written')

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE, application_name='fixture-quote-' + name)
        self.connections.append(connection)
        return connection

    def drain(self):
        raw = [item.decode() for item in reversed(self.broker.lrange('celery', 0, -1))]
        self.broker.delete('celery')
        return raw

    def django_expected(self, variant):
        """Run the actual Django task on a fresh seed with the production broker settings."""
        from contextlib import redirect_stdout, ExitStack
        import io
        from unittest.mock import patch
        from django.db import connection
        from django.test import override_settings
        from django.test.utils import CaptureQueriesContext
        from core.celery import app
        from core import settings as core_settings
        from TESTS.adapters.django.family_harness import _reset_celery_app, check_celery_message
        from application import tasks
        before = self.fixture.seed(variant)
        self.drain(); self.smtp.take()
        keys = ('CELERY_BROKER_URL', 'CELERY_TASK_DEFAULT_QUEUE', 'CELERY_TASK_ROUTES')
        saved = {key: app.conf[key] for key in keys}
        app.conf.update(dict(zip(keys, (f'redis://default:{REDIS_PASSWORD}@127.0.0.1:{self.redis_port}', 'celery', None))))
        _reset_celery_app(app)
        values = dict(WHITELABEL_NAME=BRAND, IS_WHITELABEL=False, APP_URL=APP_URL,
                      DEFAULT_FROM_EMAIL=f'{BRAND} <{HOST_USER}>', DEFAULT_TEAM_EMAIL=f'Il Team di {BRAND} <{HOST_USER}>')
        since, started = datetime.now(timezone.utc), time.time()
        try:
            with ExitStack() as stack:
                stack.enter_context(override_settings(**values))
                for name, value in values.items():
                    stack.enter_context(patch.object(core_settings, name, value))
                stack.enter_context(patch.object(tasks, 'WHITELABEL_NAME', BRAND))
                stack.enter_context(redirect_stdout(io.StringIO()))
                queries = stack.enter_context(CaptureQueriesContext(connection))
                assert tasks.association_quote_assign.run() is None
                time.sleep(TIMER_WAIT)  # the Timer threads send after 1-30 s
        finally:
            app.conf.update(saved)
            _reset_celery_app(app)
        until = datetime.now(timezone.utc)
        if PROBE:
            (self.output / f'probe-{variant}.sql').write_text(''.join(q['sql'] + ';\n' for q in queries.captured_queries))
        raw = self.drain()
        specs = [spec_from(item) for item in raw]
        for i, (item, spec) in enumerate(zip(raw, specs)):
            check_celery_message(Contract(), item, spec, since=started, until=time.time(), where=f':{i}')
        after = self.fixture.snapshot()
        transcripts = self.smtp.take()
        seeded = {str(row['payment_id']) for row in before['application.Payment']}
        self.authored_checks(variant, before, after, specs, transcripts, seeded)
        bodies = sorted(json.loads(item)['body'] for item in raw)
        return (normalized(delta(before, after), since, until, seeded), bodies, sorted(json.dumps(t) for t in transcripts), seeded)

    def authored_checks(self, variant, before, after, specs, transcripts, seeded):
        """The oracle exercised the intended paths."""
        created = [row for row in after['application.Payment'] if str(row['payment_id']) not in seeded]
        quotes = sorted((row['user_id'].int - U(0).int, row['meta']['subscription_data']['id'], str(row['amount'])) for row in created)
        kwargs = [dict(spec['kwargs']) for spec in specs]
        delivered = [t for t in transcripts if '<quit>' in t]
        balance = {row['communication_configuration_id']: row['daily_email_balance']
                   for row in after['communications.CommunicationConfiguration']}[U(50)]
        if variant == 'main':
            assert quotes == sorted([(21, 'plan-new', '30.00'), (21, 'plan-float', '12.50'), (21, 'plan-str', '45.00'),
                                     (22, 'plan-new', '30.00'), (22, 'plan-float', '12.50'), (23, 'plan-float', '12.50'),
                                     (1, 'plan-new', '30.00'), (1, 'plan-float', '12.50'), (24, 7, '20.00'), (22, 'plan-mail', '25.00')]), quotes
            assert all(row['custom_accounts_id'] == U(31) for row in created if row['sport_association_id'] == U(11))
            assert sorted(str(k['recipient_list']) for k in kwargs) == sorted(
                ["['Athlete.One@Example.TEST']"] * 3 + ["['athlete2@example.test']"] * 3 + ["['athlete3@example.test']"]), kwargs
            assert len(delivered) == 1 and balance == 5, (transcripts, balance)
            # Ten audited creates and the configuration's balance save.
            assert len(after['auditlog.LogEntry']) == 11, len(after['auditlog.LogEntry'])
        else:
            assert len(created) < 10 and (24, 'plan-new', '30.00') in quotes, quotes

    def seed_job(self, *, version=1, payload='{}', association=NIL, variant='main', reseed=True):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(variant) if reseed else self.fixture.snapshot()
        self.drain(); self.smtp.take()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,3,clock_timestamp())''', (identifier, association, KIND, version, identifier, payload))
        self.started, self.since = time.time(), datetime.now(timezone.utc)
        return identifier, before

    def matches_django(self, variant, identifier, before):
        await_condition(lambda: self.job(identifier)[0] in ('completed', 'failed'), 'registered quote assign worker durable completion',
                        timeout=60)
        assert self.job(identifier)[:3] == ('completed', 1, ''), self.job(identifier)
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]
        time.sleep(TIMER_WAIT)
        state, bodies, transcripts, seeded = self.django[variant]
        actual = normalized(delta(before, self.fixture.snapshot()), self.since, datetime.now(timezone.utc), seeded)
        if actual != state:
            differences = {label: {'django': state.get(label), 'go': actual.get(label)}
                           for label in set(state) | set(actual) if state.get(label) != actual.get(label)}
            (self.output / f'difference-{variant}.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == state, f'Go quote assign state differs from the Django task on the {variant} seed'
        raw = self.drain()
        go = sorted((json.loads(item)['body'], item) for item in raw)
        if [body for body, _ in go] != bodies:
            decode = lambda items: [json.loads(base64.b64decode(item)) for item in items]
            (self.output / f'messages-{variant}.json').write_text(json.dumps({'django': decode(bodies), 'go': decode([b for b, _ in go])},
                                                                            indent=1, ensure_ascii=False) + '\n')
        assert [body for body, _ in go] == bodies, f'Go quote assign messages differ from the Django task on the {variant} seed'
        from TESTS.adapters.django.family_harness import check_celery_message
        for i, (_, item) in enumerate(go):
            check_celery_message(Contract(), item, spec_from(item), since=self.started, until=time.time(), where=f':{i}')
        actual_transcripts = sorted(json.dumps(t) for t in self.smtp.take())
        if actual_transcripts != transcripts:
            (self.output / f'smtp-{variant}.json').write_text(json.dumps({'django': transcripts, 'go': actual_transcripts}, indent=1) + '\n')
        assert actual_transcripts == transcripts, f'Go quote assign SMTP transcripts differ from the Django task on the {variant} seed'

    def refused(self, identifier, before, label):
        await_condition(lambda: self.job(identifier)[0] == 'failed', label)
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('task_failed',)]
        assert self.fixture.snapshot() == before and not self.drain() and not self.smtp.take()

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_job()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        assert not self.drain() and not self.smtp.take()
        self.stop(process)
        self.case('default Django owner does not claim or send')

        self.handoff('go')
        process = self.worker()
        self.matches_django('main', identifier, before)
        self.case('native worker reproduces the Django task, its messages and delayed sends')
        later = self.fixture.snapshot()
        identifier, _ = self.seed_job(reseed=False)
        await_condition(lambda: self.job(identifier)[0] == 'completed', 'later quote assign run')
        assert self.fixture.snapshot() == later and not self.drain() and not self.smtp.take()
        self.case('a later run on the assigned state creates nothing')
        identifier, before = self.seed_job(variant='stop')
        self.matches_django('stop', identifier, before)
        self.case('a subscription without associate ends the run like Django')

        for kwargs, code in [({'version': 2}, 'unsupported_version'), ({'payload': '{"date":"2026-01-01"}'}, 'task_failed'),
                             ({'association': str(U(11))}, 'task_failed')]:
            identifier, before = self.seed_job(**kwargs)
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid quote assign envelope rejected')
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before and not self.drain() and not self.smtp.take()
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, before = self.seed_job(variant='unported')
        self.refused(identifier, before, 'unported fee refused')
        self.case('a fee outside the ported conversions refuses the run before any effect')
        identifier, before = self.seed_job(variant='smtp')
        self.refused(identifier, before, 'unsupported SMTP subject refused')
        self.stop(process)
        self.case('an SMTP subject outside the ported subset refuses the run before any effect')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)

    def worker(self, *arguments):
        return super().worker('--task-timeout', '50s', *arguments)

    def close(self):
        try:
            super().close()
        finally:
            self.broker.close()


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/quote-assign-worker-process'
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
    with tempfile.TemporaryDirectory(prefix='az-quote-worker-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None; redis_process = None; smtp = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            for name in ('worker', 'migrate'):
                subprocess.run(['go', 'build', '-race', '-o', str(work / name), './cmd/' + name], cwd=ROOT / 'BE_GO',
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
            rehearsal = QuoteRehearsal(work, output, environment, binaries, redis_port, smtp)
            rehearsal.run()
        finally:
            try:
                if rehearsal:
                    result.update(scenarios=rehearsal.completed, scenario_count=len(rehearsal.completed))
                    rehearsal.close()
            finally:
                try:
                    if smtp:
                        smtp.close()
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
    paths = ['TESTS/adapters/go/quote_assign_worker_process.py', 'TESTS/adapters/go/email_tasks_worker_process.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/internal/service/quote_assign.go', 'BE_GO/internal/repository/quote_assign.go',
             'BE_GO/internal/domain/quote_assign.go', 'BE_GO/internal/service/email_tasks.go', 'BE_GO/internal/service/payment_misc.go',
             'BE_GO/internal/emailrender/template.go', 'BE/application/tasks.py', 'BE/templates/email/payment/fee_assigned.html']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths if (ROOT / p).exists()})
    report.write_text(json.dumps(result, indent=2) + '\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
