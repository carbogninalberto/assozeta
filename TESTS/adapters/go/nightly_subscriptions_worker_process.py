"""Native clear_deleted_subscription and auto_archive_subscription workers on actual Django-created current schema.

Private PostgreSQL and a private MinIO (the S3 default storage of the
signature objects). Each seed is first processed by the actual Django task
with the production S3Boto3Storage backend pointed at the private MinIO; the
native Go worker must then leave the identical complete managed-model state on
the same reseeded rows (audit ids and timestamps normalized) and the identical
bucket contents. Explicit fixture producers and ownership are not installed
activation.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import shutil
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

DATABASE = 'assozeta_contract_nightly_subscriptions_worker'
CLEAR, ARCHIVE = 'clear_deleted_subscription', 'auto_archive_subscription'
BUCKET = 'nightly-subscriptions'
LOCATION = 'private'
SENTINEL = (LOCATION + '/unrelated/keep.txt', b'unrelated object')
VARIANTS = (('clear', False), ('clear', True), ('archive', False), ('archive', True))
EXPECTED = {
    'default Django owner does not claim either task',
    'clear: native worker reproduces the Django task state and storage',
    'clear: a failing subscription block is rolled back and the run goes on like Django',
    'clear: a later run on the cleaned state removes nothing',
    'archive: native worker reproduces the Django task state',
    'archive: a failing subscription block is rolled back and the run goes on like Django',
    'archive: a later run on the archived state changes nothing',
    'unsupported version payload and tenant fail before business effects',
}
TABLES = '''auditlog_logentry, application_auditlogindex, application_subscriptiontoken,
  application_coursesubscriptioninstallment, application_coursesubscription, application_course,
  application_subscription, application_payment, application_invoice, application_associate,
  application_sportassociation, bakney_user'''


def U(n):
    return UUID(int=0xF7000000 + n)


class NightlyFixture:
    """Owners, associations, subscriptions with payments, invoices, course rows and signature objects."""

    def __init__(self, storage):
        self.storage = storage
        self.now = datetime.now(timezone.utc).replace(microsecond=123456)
        # The archive owner's balance year starts on the first day of the month
        # six months ahead, so neither run crosses a year boundary.
        self.fiscal_month = (self.now.month + 5) % 12 + 1

    def reset(self):
        from django.db import connection
        client = self.storage['client']
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET):
            for item in page.get('Contents', []):
                client.delete_object(Bucket=BUCKET, Key=item['Key'])
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1])
        with connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER IF EXISTS fixture_fail ON application_subscription')
            # TRUNCATE resets the heaps, so every seed is laid out in insertion order.
            cursor.execute(f'TRUNCATE {TABLES} CASCADE')

    def owners(self, specs):
        from application.models import User, SportAssociation, Associate
        users, associations, associates = [], [], []
        for n, values in specs.items():
            users.append(User(user_id=U(n), username=f'nightly-owner-{n}', role=1, password='!private-fixture',
                              date_joined=self.now, **values.get('user', {})))
            associations.append(SportAssociation(sport_association_id=U(10 + n), user_id=U(n),
                denomination=f'Nightly fixture {n}', tax_code=f'{n:011d}', deleted=values.get('deleted', False)))
            associates.append(Associate(associate_id=U(20 + n), sport_association_id=U(10 + n), first_name='Nightly',
                last_name=f'Associate {n}', creation_date=self.now))
        User.original_objects.bulk_create(users)
        SportAssociation.original_objects.bulk_create(associations)
        Associate._base_manager.bulk_create(associates)

    def payment(self, n, owner, *, paid=False, invoice=None, **values):
        from application.models import Payment, Invoice
        if invoice == 'own':
            Invoice._base_manager.bulk_create([Invoice(invoice_id=U(300 + n), sport_association_id=U(10 + owner),
                membership_fee=Decimal('10.00'), activity_fee=Decimal('0.50'), creation_date=self.now,
                deleted=values.get('deleted', False), archived=values.get('archived', False))])
            invoice = U(300 + n)
        values = dict(dict(subject=1, description=f'Pagamento {n}'), **values)
        Payment._base_manager.bulk_create([Payment(payment_id=U(200 + n), sport_association_id=U(10 + owner), user_id=U(owner),
            amount=Decimal('25.00'), paid=paid, invoice_id=invoice, creation_date=self.now, **values)])
        return U(200 + n)

    def subscription(self, n, owner, *, created=None, **values):
        from application.models import Subscription
        Subscription._base_manager.bulk_create([Subscription(subscription_id=U(100 + n), sport_association_id=U(10 + owner),
            user_id=U(owner), associate_id=U(20 + owner), notes=f'nota {n}', **values)])
        self.created[U(100 + n)] = created or self.now
        return U(100 + n)

    def pin_times(self):
        """bulk_create still applies auto_now(_add); pin them so both seeds are identical."""
        from django.apps import apps
        from application.models import Subscription
        for model in apps.get_models():
            fields = [f.name for f in model._meta.concrete_fields if getattr(f, 'auto_now', False) or getattr(f, 'auto_now_add', False)]
            if fields and model._meta.managed and not model._meta.proxy and model._base_manager.exists():
                model._base_manager.update(**{name: self.now for name in fields})
        for key, created in self.created.items():
            Subscription._base_manager.filter(pk=key).update(creation_date=created)

    def seed(self, task, failing=False):
        from auditlog.context import disable_auditlog
        from django.db import connection
        self.reset()
        self.created = {}
        with disable_auditlog():
            (self.seed_clear if task == 'clear' else self.seed_archive)()
            self.pin_times()
        with connection.cursor() as cursor:
            if failing:
                # A database error inside one subscription's atomic block, for both runtimes.
                failing_key = U(104) if task == 'clear' else U(102)
                cursor.execute(f'''CREATE OR REPLACE FUNCTION fixture_fail() RETURNS trigger LANGUAGE plpgsql AS $$
                  BEGIN IF OLD.subscription_id = '{failing_key}' THEN RAISE EXCEPTION 'fixture failure'; END IF;
                  RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END; END $$''')
                cursor.execute(f'''CREATE TRIGGER fixture_fail BEFORE {'DELETE' if task == 'clear' else 'UPDATE'}
                  ON application_subscription FOR EACH ROW EXECUTE FUNCTION fixture_fail()''')
            cursor.execute('ANALYZE application_subscription, application_payment, application_invoice')
        return self.snapshot()

    def seed_clear(self):
        from application.models import Course, CourseSubscription, CourseSubscriptionInstallment, SubscriptionToken
        client = self.storage['client']
        self.owners({1: {}})
        # 1: paid payment and its invoice stay; 2: unpaid payment and its invoice go.
        self.subscription(1, 1, deleted=True, payment_id=self.payment(1, 1, paid=True, invoice='own'))
        self.subscription(2, 1, deleted=True, payment_id=self.payment(2, 1, invoice='own'))
        # 3: an unpaid payment whose invoice a paid payment also uses.
        shared = self.payment(3, 1, invoice='own')
        self.payment(4, 1, paid=True, invoice=U(303))
        self.subscription(3, 1, deleted=True, payment_id=shared)
        # 4: course subscriptions (one soft-deleted) and installments with paid and unpaid payments.
        self.subscription(4, 1, deleted=True)
        Course._base_manager.bulk_create([Course(course_id=U(400 + n), sport_association_id=U(11), title=f'Corso {n}',
            fee=Decimal('50.00'), creation_date=self.now, google_background_color='#123456') for n in range(3)])
        CourseSubscription._base_manager.bulk_create([
            CourseSubscription(course_subscription_id=U(500), course_id=U(400), subscription_id=U(104),
                payment_id=self.payment(5, 1, paid=True, invoice='own', subject=2), creation_date=self.now),
            CourseSubscription(course_subscription_id=U(501), course_id=U(401), subscription_id=U(104),
                payment_id=self.payment(6, 1, invoice='own', subject=2), deleted=True, creation_date=self.now),
            CourseSubscription(course_subscription_id=U(502), course_id=U(402), subscription_id=U(104),
                multi_payments=True, creation_date=self.now)])
        CourseSubscriptionInstallment._base_manager.bulk_create([
            CourseSubscriptionInstallment(course_subscription_installment_id=U(600), course_subscription_id=U(502), id=1,
                amount=Decimal('25.00'), paid=True, payment_date=self.now, payment_id=self.payment(7, 1, paid=True, invoice='own', subject=2)),
            CourseSubscriptionInstallment(course_subscription_installment_id=U(601), course_subscription_id=U(502), id=2,
                amount=Decimal('25.00'), payment_date=self.now, payment_id=self.payment(8, 1, invoice='own', subject=2))])
        SubscriptionToken._base_manager.bulk_create([SubscriptionToken(subscription_token_id=U(700), subscription_id=U(104),
            token=U(701), expiration_date=self.now + timedelta(days=1), creation_date=self.now)])
        # 5: its own signature object is removed after the commit; 6: a live subscription keeps the shared one.
        for name in ('signatures/own.png', 'signatures/shared.png'):
            client.put_object(Bucket=BUCKET, Key=f'{LOCATION}/{name}', Body=name.encode())
        self.subscription(5, 1, deleted=True, signature_storage_key='signatures/own.png')
        self.subscription(6, 1, deleted=True, signature_storage_key='signatures/shared.png')
        self.subscription(7, 1, signature_storage_key='signatures/shared.png', payment_id=self.payment(9, 1, invoice='own'))
        # 8: an unpaid payment a live subscription also references (SET_NULL on the live row).
        common = self.payment(10, 1)
        self.subscription(8, 1, deleted=True, payment_id=common)
        self.subscription(9, 1, payment_id=common)

    def seed_archive(self):
        old = self.now - timedelta(days=800)
        current = self.now - timedelta(hours=1)
        fiscal = dict(balance_sheet_start_day=1, balance_sheet_start_month=self.fiscal_month)
        self.owners({
            1: {'user': dict(auto_archive=True, **fiscal)},
            2: {'user': dict(auto_archive=False, **fiscal)},
            3: {'user': dict(auto_archive=True, subscription_start_month=0, **fiscal)},
            4: {'user': dict(auto_archive=True, balance_sheet_start_day=1, balance_sheet_start_month=13)},
            5: {'user': dict(auto_archive=True, **fiscal), 'deleted': True},
        })
        # 1: payment and invoice; 2: without payment; 3: payment without invoice.
        self.subscription(1, 1, created=old, payment_id=self.payment(1, 1, invoice='own'))
        self.subscription(2, 1, created=old)
        self.subscription(3, 1, created=old, payment_id=self.payment(3, 1))
        # 4: the current balance year; 5: deleted; 6: already archived.
        self.subscription(4, 1, created=current, payment_id=self.payment(4, 1, invoice='own'))
        self.subscription(5, 1, created=old, deleted=True, payment_id=self.payment(5, 1, invoice='own'))
        self.subscription(6, 1, created=old, archived=True, payment_id=self.payment(6, 1, invoice='own'))
        # 7: soft-deleted payment and invoice; 8: an already archived payment and invoice.
        self.subscription(7, 1, created=old, payment_id=self.payment(7, 1, invoice='own', deleted=True))
        self.subscription(8, 1, created=old, payment_id=self.payment(8, 1, invoice='own', archived=True))
        # 9 and 10 share one payment.
        shared = self.payment(9, 1, invoice='own')
        self.subscription(9, 1, created=old, payment_id=shared)
        self.subscription(10, 1, created=old, payment_id=shared)
        # 11: owner without auto_archive; 12: invalid subscription calendar;
        # 13: invalid balance year; 14: deleted association.
        self.subscription(11, 2, created=old, payment_id=self.payment(11, 2, invoice='own'))
        self.subscription(12, 3, created=old, payment_id=self.payment(12, 3, invoice='own'))
        self.subscription(13, 4, created=old, payment_id=self.payment(13, 4, invoice='own'))
        self.subscription(14, 5, created=old, payment_id=self.payment(14, 5, invoice='own'))

    def snapshot(self):
        from django.apps import apps
        state = {m._meta.label: list(m._base_manager.order_by('pk').values())
                 for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        state['storage'] = self.listing()
        return state

    def listing(self):
        client = self.storage['client']
        objects = {}
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET):
            for item in page.get('Contents', []):
                objects[item['Key']] = client.get_object(Bucket=BUCKET, Key=item['Key'])['Body'].read().decode()
        return objects

    @staticmethod
    def normalized(state, since, until):
        """Audit ids, index ids and timestamps vary by run; everything else must match."""
        state = dict(state)
        keys, rows = {}, []
        for entry in state['auditlog.LogEntry']:
            entry = dict(entry)
            assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
            keys[entry['id']] = (entry['content_type_id'], entry['object_pk'], entry['action'], json.dumps(entry['changes'], sort_keys=True))
            entry.pop('id'); entry['timestamp'] = 'run'
            # CourseSubscription's str() is its __dict__: the ModelState address varies per process.
            entry['object_repr'] = re.sub(r'(<django\.db\.models\.base\.ModelState object at )0x[0-9a-f]+>', r'\1ADDRESS>', entry['object_repr'])
            rows.append(entry)
        state['auditlog.LogEntry'] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))
        indexes = []
        for index in state['application.AuditLogIndex']:
            index = dict(index)
            assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
            index.pop('audit_log_index_id'); index['created_at'] = 'run'
            index['log_entry_id'] = keys[index['log_entry_id']]
            indexes.append(index)
        state['application.AuditLogIndex'] = sorted(indexes, key=lambda row: json.dumps(row, sort_keys=True, default=str))
        return state


class NightlyRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries, storage):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID=storage['access'], AWS_SECRET_ACCESS_KEY=storage['secret'],
            AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL=storage['endpoint'], AWS_LOCATION=LOCATION,
            REDIS_HOST='127.0.0.1', REDIS_PORT='9', REDIS_USERNAME='default', REDIS_PASSWORD='private-redis-password-canary',
            WHITELABEL_NAME='Assozeta Fixture', DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.storage = storage
        self.fixture = NightlyFixture(storage)
        self.seeds, self.django = {}, {}
        for task, failing in VARIANTS:
            self.django[task, failing] = self.django_expected(task, failing)

    def django_expected(self, task, failing):
        """Run the actual Django task on a fresh seed against the private MinIO."""
        from django.test import override_settings
        from application import tasks
        before = self.fixture.seed(task, failing)
        self.seeds[task, failing] = before
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        since = datetime.now(timezone.utc)
        with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=self.storage['access'],
                               AWS_SECRET_ACCESS_KEY=self.storage['secret'], AWS_STORAGE_BUCKET_NAME=BUCKET,
                               AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=self.storage['endpoint'],
                               AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path'):
            result = (tasks.clear_deleted_subscription if task == 'clear' else tasks.auto_archive_subscription).run()
        until = datetime.now(timezone.utc)
        after = self.fixture.snapshot()
        self.authored_checks(task, failing, before, after, result)
        return self.fixture.normalized(after, since, until)

    def authored_checks(self, task, failing, before, after, result):
        """The oracle took the intended paths."""
        def keys(label, field, base):
            return {row[field].int - U(base).int for row in after[label]}
        subscriptions = keys('application.Subscription', 'subscription_id', 100)
        payments = keys('application.Payment', 'payment_id', 200)
        invoices = keys('application.Invoice', 'invoice_id', 300)
        if task == 'clear':
            if failing:
                assert result == 6 and subscriptions == {4, 7, 9}, (result, subscriptions)
                return
            assert result == 7 and subscriptions == {7, 9}, (result, subscriptions)
            assert payments == {1, 4, 5, 7, 9}, sorted(payments)
            assert invoices == {1, 3, 5, 7, 9}, sorted(invoices)
            assert not after['application.CourseSubscription'] and not after['application.CourseSubscriptionInstallment']
            assert not after['application.SubscriptionToken']
            live = {row['subscription_id']: row['payment_id'] for row in after['application.Subscription']}
            assert live[U(109)] is None and live[U(107)] == U(209)
            assert set(after['storage']) == {SENTINEL[0], f'{LOCATION}/signatures/shared.png'}, sorted(after['storage'])
            return
        archived = {row['subscription_id'].int - U(100).int for row in after['application.Subscription'] if row['archived']}
        if failing:
            assert result == 7 and archived == {1, 3, 6, 7, 8, 9, 10, 14}, (result, sorted(archived))
            return
        assert result == 8 and archived == {1, 2, 3, 6, 7, 8, 9, 10, 14}, (result, sorted(archived))
        paid = {row['payment_id'].int - U(200).int for row in after['application.Payment'] if row['archived']}
        assert paid == {1, 3, 7, 8, 9, 14}, sorted(paid)
        entries = {(row['object_pk'], json.dumps(row['changes'], sort_keys=True)) for row in after['auditlog.LogEntry']}
        assert (str(U(207)), '{"archived": ["None", "True"]}') in entries and (str(U(307)), '{"archived": ["None", "True"]}') in entries
        assert not any(pk in (str(U(208)), str(U(308))) for pk, _ in entries), 'archived rows gained audits'
        indexed = {row['log_entry_id'] for row in after['application.AuditLogIndex']}
        assert len(after['auditlog.LogEntry']) == len(indexed) + 2, 'only the two hidden rows lack an index'

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-nightly-' + name)
        self.connections.append(connection)
        return connection

    def seed_job(self, kind, *, failing=False, version=1, payload='{}', association=NIL, reseed=True):
        task = 'clear' if kind == CLEAR else 'archive'
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(task, failing) if reseed else self.fixture.snapshot()
        if reseed:
            if before != self.seeds[task, failing]:
                first = self.seeds[task, failing]
                differences = {label: [(a, b) for a, b in zip(first[label], before[label]) if a != b][:2]
                               for label in first if first[label] != before.get(label)}
                (self.output / 'seed-difference.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
            assert before == self.seeds[task, failing], 'the seed is not deterministic'
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,3,clock_timestamp())''', (identifier, association, kind, version, identifier, payload))
        self.since = datetime.now(timezone.utc)
        return identifier, before

    def task_complete(self, identifier):
        await_condition(lambda: self.job(identifier)[0] in ('completed', 'failed'), 'registered nightly worker durable completion', timeout=30)
        assert self.job(identifier)[:3] == ('completed', 1, ''), self.job(identifier)
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]

    def compare(self, task, failing):
        until = datetime.now(timezone.utc)
        actual = self.fixture.normalized(self.fixture.snapshot(), self.since, until)
        state = self.django[task, failing]
        if actual != state:
            differences = {label: {'django': state.get(label), 'go': actual.get(label)}
                           for label in set(state) | set(actual) if state.get(label) != actual.get(label)}
            (self.output / f'difference-{task}-{failing}.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == state, f'Go {task} differs from the Django task on the {"failing" if failing else "main"} seed'

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        process = self.worker()
        for kind in (CLEAR, ARCHIVE):
            identifier, before = self.seed_job(kind)
            await_condition(self.observed_claim, 'actual registered worker queue query')
            time.sleep(.2)
            assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        self.stop(process)
        self.sql('DELETE FROM assozeta_go_jobs')
        self.case('default Django owner does not claim either task')

        self.handoff('go')
        process = self.worker()
        for kind, task in ((CLEAR, 'clear'), (ARCHIVE, 'archive')):
            labels = {'clear': 'native worker reproduces the Django task state and storage',
                      'archive': 'native worker reproduces the Django task state'}
            identifier, _ = self.seed_job(kind)
            self.task_complete(identifier)
            self.compare(task, False)
            self.case(f'{task}: {labels[task]}')
            identifier, _ = self.seed_job(kind, failing=True)
            self.task_complete(identifier)
            self.compare(task, True)
            self.case(f'{task}: a failing subscription block is rolled back and the run goes on like Django')
            identifier, _ = self.seed_job(kind)
            self.task_complete(identifier)
            self.compare(task, False)
            identifier, later = self.seed_job(kind, reseed=False)
            self.task_complete(identifier)
            assert self.fixture.snapshot() == later
            self.case(f'{task}: ' + ('a later run on the cleaned state removes nothing' if task == 'clear'
                                     else 'a later run on the archived state changes nothing'))

        for kind in (CLEAR, ARCHIVE):
            for kwargs, code in [({'version': 2}, 'unsupported_version'),
                                 ({'payload': '{"unexpected":true}'}, 'task_failed'),
                                 ({'association': str(U(11))}, 'task_failed')]:
                identifier, before = self.seed_job(kind, **kwargs)
                await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid nightly envelope rejected')
                assert self.job(identifier)[:2] == ('failed', 1)
                assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
                assert self.fixture.snapshot() == before
        self.stop(process)
        self.case('unsupported version payload and tenant fail before business effects')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    import boto3
    from botocore.config import Config
    from TESTS.adapters.django.instance_logo import minio
    output = ROOT / 'quality-reports/go-migration/nightly-subscriptions-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go') or not shutil.which('docker'):
        raise RuntimeError('Private PostgreSQL, Docker (MinIO) and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'DOCKER_HOST', 'DOCKER_CONFIG')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-nightly-worker-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory); attempted = False; rehearsal = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=environment, stdout=log,
                                  stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            with minio(log, dict(os.environ), result) as (endpoint, access, secret):
                client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                    region_name='us-east-1', config=Config(signature_version='s3v4', retries={'max_attempts': 0},
                    s3={'addressing_style': 'path'}))
                client.create_bucket(Bucket=BUCKET)
                storage = dict(client=client, endpoint=endpoint, access=access, secret=secret)
                for name in ('worker', 'migrate'):
                    subprocess.run(['go', 'build', '-race', '-o', str(work / name), './cmd/' + name], cwd=ROOT / 'BE_GO',
                        env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
                attempted = True
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
                initialize_current_schema(work, work, '5432', DATABASE, log)
                rehearsal = NightlyRehearsal(work, output, environment, binaries, storage)
                rehearsal.run()
        finally:
            try:
                if rehearsal:
                    result.update(scenarios=rehearsal.completed, scenario_count=len(rehearsal.completed))
                    rehearsal.close()
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    try:
                        if attempted:
                            try:
                                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work / 'postgres/postmaster.pid').exists():
                                    raise
                    finally:
                        if (work / 'postgres.log').exists():
                            shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                        report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    paths = ['TESTS/adapters/go/nightly_subscriptions_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/internal/service/clear_deleted_subscription.go',
             'BE_GO/internal/repository/clear_deleted_subscription.go', 'BE_GO/internal/domain/clear_deleted_subscription.go',
             'BE_GO/internal/service/subscription_auto_archive.go', 'BE_GO/internal/repository/subscription_auto_archive.go',
             'BE_GO/internal/domain/subscription_auto_archive.go', 'BE_GO/internal/repository/collaborator_delete_collector.go',
             'BE_GO/internal/repository/collaborator_delete_graph.json', 'BE_GO/pkg/storage/documentkey/stored.go',
             'BE_GO/pkg/storage/documentobjects/s3.go', 'BE/application/tasks.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths if (ROOT / p).exists()})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n'); print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
