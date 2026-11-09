"""Native auto_delete_orphan_medical_certificate worker on actual Django-created current schema.

Private PostgreSQL and a private MinIO (the S3 default storage). Each seed is
first processed by the actual Django task with the production S3Boto3Storage
backend pointed at the private MinIO; the native Go worker must then leave the
identical complete managed-model state on the same reseeded rows (audit ids
and timestamps normalized) and the identical bucket contents. Explicit fixture
producers and ownership are not installed activation.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
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

DATABASE = 'assozeta_contract_orphan_medical_worker'
KIND = 'auto_delete_orphan_medical_certificate'
BUCKET = 'orphan-medical'
LOCATION = 'private'
SENTINEL = (LOCATION + '/unrelated/keep.txt', b'unrelated object')
VARIANTS = ('main', 'stop-document', 'stop-outside')
EXPECTED = {
    'default Django owner does not claim or delete certificates',
    'native worker reproduces the Django task state and storage',
    'a certificate without document ends the run like Django',
    'a file name outside the storage location ends the run like Django',
    'unsupported version payload and tenant fail before business effects',
    'a later run on the cleaned state deletes nothing',
    'a cascading document reference refuses the run before any effect',
}


def U(n):
    return UUID(int=0xF6000000 + n)


class OrphanFixture:
    """One association; certificates kept by live and deleted subscriptions, and orphans."""

    def __init__(self, storage):
        from application.models import User, SportAssociation
        self.storage = storage
        self.now = datetime.now(timezone.utc).replace(microsecond=123456)
        User.original_objects.bulk_create([User(user_id=U(1), username='orphan-medical-owner', role=1, password='!private-fixture'),
                                           User(user_id=U(2), username='orphan-medical-athlete', role=2, password='!private-fixture')])
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=U(11), user_id=U(1),
            denomination='Private orphan fixture', tax_code='00000000000')])

    def controls(self, variant):
        """(certificate n, document filename or None, created offset, stored, kept by subscription)."""
        rows = [
            (0, 'certificato.pdf', timedelta(days=3), True, None),
            (1, 'tenuto.pdf', timedelta(days=4), True, 'live'),
            (2, 'archiviato.pdf', timedelta(days=5), True, 'deleted'),
            (3, 'scansione à.pdf', timedelta(days=6), False, None),
            (4, 'sub/../normalizzato.pdf', timedelta(days=7), True, None),
            (5, 'con filepath.pdf', timedelta(days=8), True, None),
            (6, 'secondi interi.pdf', timedelta(days=9, microseconds=123456), True, None),
        ]
        if variant == 'stop-document':
            rows.insert(3, (7, None, None, False, None))
        elif variant == 'stop-outside':
            rows.insert(3, (8, '/fuori.pdf', timedelta(days=2), False, None))
        return rows

    def seed(self, variant='main', *, cascade=False):
        from application.models import Instructor, InstructorHours
        from application.models.user_models import SportAssociationDocumentsArchive
        from application.models.subscriptions_models import MedicalCertificate, Subscription
        from docmanager.models import Document
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        client = self.storage['client']
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET):
            for item in page.get('Contents', []):
                client.delete_object(Bucket=BUCKET, Key=item['Key'])
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1])
        from django.db import connection
        # TRUNCATE resets the heaps, so every seed is laid out in insertion order
        # and the unordered queryset returns the same order for Django and Go.
        with connection.cursor() as cursor:
            cursor.execute('''TRUNCATE auditlog_logentry, application_auditlogindex, application_instructorhours, application_instructor,
              application_subscription, application_sportassociationdocumentsarchive, application_medicalcertificate,
              docmanager_document CASCADE''')
        with disable_auditlog():
            documents = {}
            for n, filename, age, stored, kept in self.controls(variant):
                document = None
                if filename is not None:
                    created = self.now - age
                    document = Document.objects.create(document_id=U(200 + n), creation_date=created, filename=filename,
                        filepath='other/path.pdf' if n == 5 else None, file_size_bytes=10 + n, token=U(300 + n))
                    documents[n] = document
                    if stored:
                        name = os.path.normpath(f'{created.timestamp()}/{document.document_id}/{filename}')
                        client.put_object(Bucket=BUCKET, Key=f'{LOCATION}/{name}', Body=f'certificate {n}'.encode())
                MedicalCertificate.objects.create(medical_id=U(100 + n), document=document, user_id=U(2), creation_date=self.now,
                    expiration_date=self.now.date() + timedelta(days=30), notes=f'nota {n}')
                if kept:
                    Subscription._base_manager.bulk_create([Subscription(subscription_id=U(400 + n), sport_association_id=U(11), user_id=U(2),
                        medical_id=U(100 + n), creation_date=self.now, deleted=kept == 'deleted', archived=kept == 'deleted')])
            client.put_object(Bucket=BUCKET, Key=LOCATION + '/other/path.pdf', Body=b'filepath object')
            # SET_NULL references to orphan documents are cleared by the collector.
            instructor = Instructor.objects.create(instructor_id=U(500), user_id=U(1), first_name='Orphan', last_name='Fixture')
            InstructorHours.objects.create(instructor_hours_id=U(501), instructor=instructor, hours='1.00',
                creation_date=self.now, date=self.now.date(), document=documents[0])
            Subscription._base_manager.bulk_create([Subscription(subscription_id=U(450), sport_association_id=U(11), user_id=U(2),
                document_pdf=documents[3], creation_date=self.now)])
            if cascade:
                SportAssociationDocumentsArchive.objects.create(sport_association_documents_archive_id=U(600),
                    sport_association_id=U(11), document=documents[4], date=self.now.date())
        # Fresh planner statistics on every seed: the unordered queryset then gets
        # the same plan, hence the same row order, in the Django and Go runs.
        with connection.cursor() as cursor:
            cursor.execute('ANALYZE application_medicalcertificate, docmanager_document, application_subscription')
        return self.snapshot()

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
            keys[entry['id']] = (entry['content_type_id'], entry['object_pk'], entry['action'])
            entry.pop('id'); entry['timestamp'] = 'run'
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


class OrphanRehearsal(Rehearsal):
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
        self.fixture = OrphanFixture(storage)
        self.django = {variant: self.django_expected(variant) for variant in VARIANTS}

    def django_expected(self, variant):
        """Run the actual Django task on a fresh seed against the private MinIO."""
        from contextlib import redirect_stdout
        import io
        from django.db import connection
        from django.test import override_settings
        from django.test.utils import CaptureQueriesContext
        from application import tasks
        before = self.fixture.seed(variant)
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        since = datetime.now(timezone.utc)
        with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=self.storage['access'],
                               AWS_SECRET_ACCESS_KEY=self.storage['secret'], AWS_STORAGE_BUCKET_NAME=BUCKET,
                               AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=self.storage['endpoint'],
                               AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path'), \
                redirect_stdout(io.StringIO()) as printed, CaptureQueriesContext(connection) as queries:
            assert tasks.auto_delete_orphan_medical_certificate.run() is None
        until = datetime.now(timezone.utc)
        if os.environ.get('ORPHAN_MEDICAL_PROBE'):
            (self.output / f'probe-{variant}.sql').write_text(''.join(q['sql'] + ';\n' for q in queries.captured_queries))
        after = self.fixture.snapshot()
        self.authored_checks(variant, before, after, printed.getvalue())
        return self.fixture.normalized(after, since, until)

    def authored_checks(self, variant, before, after, printed):
        """The oracle took the intended paths."""
        certificates = {row['medical_id'].int - U(100).int for row in after['application.MedicalCertificate']}
        documents = {row['document_id'].int - U(200).int for row in after['docmanager.Document']}
        entries = after['auditlog.LogEntry']
        if variant == 'main':
            assert certificates == {1, 2}, sorted(certificates)
            assert documents == {1, 2}, sorted(documents)
            assert len(entries) == 15 and not after['application.AuditLogIndex'], (len(entries), after['application.AuditLogIndex'])
            assert [row['document_id'] for row in after['application.InstructorHours']] == [None]
            assert {row['subscription_id']: row['document_pdf_id'] for row in after['application.Subscription']}[U(450)] is None
            assert set(after['storage']) == {SENTINEL[0], LOCATION + '/other/path.pdf'} | {
                key for key in before['storage'] if f'/{U(201)}/' in key or f'/{U(202)}/' in key}, sorted(after['storage'])
            assert '[DEPLOY TASK] OK!' in printed
        elif variant == 'stop-document':
            # PostgreSQL's plan for the unordered LEFT OUTER JOIN reaches the
            # certificate without document after the others: the stop ends the loop.
            assert certificates == {1, 2, 7}, sorted(certificates)
            assert len(entries) == 15 and "error 'NoneType' object has no attribute 'creation_date'" in printed, printed
        else:
            # This plan also reaches the document outside the location last.
            assert certificates == {1, 2, 8}, (sorted(certificates), printed)
            assert len(entries) == 15 and "error Attempted access to '/fuori.pdf' denied." in printed, printed

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-orphan-' + name)
        self.connections.append(connection)
        return connection

    def seed_job(self, *, version=1, payload='{}', association=NIL, variant='main', cascade=False, reseed=True):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(variant, cascade=cascade) if reseed else self.fixture.snapshot()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,3,clock_timestamp())''', (identifier, association, KIND, version, identifier, payload))
        self.since = datetime.now(timezone.utc)
        return identifier, before

    def cleanup_complete(self, identifier):
        await_condition(lambda: self.job(identifier)[0] in ('completed', 'failed'), 'registered orphan medical worker durable completion')
        assert self.job(identifier)[:3] == ('completed', 1, ''), self.job(identifier)
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]

    def compare(self, variant):
        until = datetime.now(timezone.utc)
        actual = self.fixture.normalized(self.fixture.snapshot(), self.since, until)
        state = self.django[variant]
        if actual != state:
            differences = {label: {'django': state.get(label), 'go': actual.get(label)}
                           for label in set(state) | set(actual) if state.get(label) != actual.get(label)}
            (self.output / f'difference-{variant}.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == state, f'Go orphan medical cleanup differs from the Django task on the {variant} seed'

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_job()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        self.stop(process)
        self.case('default Django owner does not claim or delete certificates')

        self.handoff('go')
        labels = {'main': 'native worker reproduces the Django task state and storage',
                  'stop-document': 'a certificate without document ends the run like Django',
                  'stop-outside': 'a file name outside the storage location ends the run like Django'}
        process = self.worker()
        self.cleanup_complete(identifier)
        self.compare('main')
        self.case(labels['main'])
        for variant in VARIANTS[1:]:
            identifier, _ = self.seed_job(variant=variant)
            self.cleanup_complete(identifier)
            self.compare(variant)
            self.case(labels[variant])

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(U(11))}, 'task_failed')]:
            identifier, before = self.seed_job(**kwargs)
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid orphan medical envelope rejected')
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, _ = self.seed_job()
        self.cleanup_complete(identifier)
        self.compare('main')
        identifier, later = self.seed_job(reseed=False)
        self.cleanup_complete(identifier)
        assert self.fixture.snapshot() == later
        self.case('a later run on the cleaned state deletes nothing')

        identifier, before = self.seed_job(cascade=True)
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'cascading orphan medical reference refused')
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('task_failed',)]
        assert self.fixture.snapshot() == before
        self.stop(process)
        self.case('a cascading document reference refuses the run before any effect')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    import boto3
    from botocore.config import Config
    from TESTS.adapters.django.instance_logo import minio
    output = ROOT / 'quality-reports/go-migration/orphan-medical-worker-process'
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
    probe = os.environ.get('ORPHAN_MEDICAL_PROBE')
    with tempfile.TemporaryDirectory(prefix='az-orphan-worker-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
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
                if probe:
                    os.environ['ORPHAN_MEDICAL_PROBE'] = probe
                rehearsal = OrphanRehearsal(work, output, environment, binaries, storage)
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
    paths = ['TESTS/adapters/go/orphan_medical_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/cmd/worker/tasks_db_f.go', 'BE_GO/internal/service/orphan_medical_cleanup.go',
             'BE_GO/internal/repository/orphan_medical_cleanup.go', 'BE_GO/internal/domain/orphan_medical_cleanup.go',
             'BE_GO/pkg/storage/documentkey/stored.go', 'BE_GO/pkg/storage/documentobjects/s3.go', 'BE/application/tasks.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths if (ROOT / p).exists()})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n'); print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
