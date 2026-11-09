"""Native cleanup_old_exports worker on actual Django-created current schema.

Private PostgreSQL and a private MinIO (the S3 default storage). Each seed is
first processed by the actual Django ``cleanup_old_exports_task`` with the
production S3Boto3Storage backend pointed at the private MinIO; the native Go
worker must then leave the identical complete managed-model state on the same
reseeded rows (audit ids and timestamps normalized), the identical bucket
listing and the same task result. Explicit fixture producers and ownership are
not installed activation.
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

DATABASE = 'assozeta_contract_export_cleanup_worker'
KIND = 'cleanup_old_exports'
BUCKET = 'export-cleanup'
LOCATION = 'private'
SENTINEL = (LOCATION + '/unrelated/keep.txt', b'unrelated object')
EXPECTED = {
    'default Django owner does not claim or delete exports',
    'native worker reproduces the Django task state, storage and result',
    'unsupported version payload and tenant fail before business effects',
    'a later run on the cleaned state deletes nothing',
    'a cascading document reference refuses the run before any effect',
}


class ExportFixture:
    """Three associations: live with age and count deletions, deleted, and young only."""
    def __init__(self, storage):
        from application.models import User, SportAssociation
        self.storage = storage
        # One clock for every seed, so reseeded rows are identical; the run
        # must finish within the hour margin of the youngest kept export.
        self.now = datetime.now(timezone.utc)
        self.owners = [UUID(int=86001 + i) for i in range(3)]
        self.associations = [UUID(int=86101 + i) for i in range(3)]
        User.original_objects.bulk_create([User(user_id=pk, username=f'export-cleanup-{i}', role=1,
            password='!private-fixture') for i, pk in enumerate(self.owners)])
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=pk,
            user_id=self.owners[i], denomination='Private export fixture', tax_code='00000000000',
            deleted=(i == 1)) for i, pk in enumerate(self.associations)])

    def controls(self):
        """(association, archive date offset days, filename, created offset, filepath, stored)."""
        day, hour = timedelta(days=1), timedelta(hours=1)
        return [
            # live association: two old exports, then five young ones; three are kept.
            (0, 1, 'export_young_1.zip', 1 * day, 'exports/a/young_1.zip', True),
            (0, 2, 'export_old_1.zip', 40 * day, 'exports/a/old_1.zip', True),
            (0, 3, 'export_young_2.zip', 5 * day, None, False),
            (0, 4, 'export_young_3.zip', 6 * day, 'exports/a/young_3.zip', True),
            (0, 5, 'export_young_4.zip', 7 * day, 'exports/a/missing.zip', False),
            (0, 6, 'export_young_5.zip', 30 * day - hour, '', False),
            (0, 7, 'export_old_2.zip', 31 * day, 'exports/a/old_2.zip', True),
            (0, 8, 'report_old.zip', 90 * day, 'exports/a/report.zip', True),
            (0, 9, 'Export_old.zip', 90 * day, 'exports/a/upper.zip', True),
            # deleted association: one path outside the location is kept.
            (1, 1, 'export_deleted_old.zip', 45 * day, 'exports/b/old.zip', True),
            (1, 2, 'export_deleted_outside.zip', 45 * day, '../outside/b.zip', False),
            # young only.
            (2, 1, 'export_c_1.zip', 2 * day, 'exports/c/one.zip', True),
            (2, 2, 'export_c_2.zip', 3 * day, 'exports/c/two.zip', True),
        ]

    def seed(self, *, cascade=False):
        from application.models import Instructor, InstructorHours
        from application.models.user_models import SportAssociationDocumentsArchive
        from application.models.subscriptions_models import MedicalCertificate
        from docmanager.models import Document
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        now = self.now
        today = now.date()
        assert datetime.now(timezone.utc) - now < timedelta(minutes=50), 'fixture clock margin exceeded'
        client = self.storage['client']
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET):
            for item in page.get('Contents', []):
                client.delete_object(Bucket=BUCKET, Key=item['Key'])
        client.put_object(Bucket=BUCKET, Key=SENTINEL[0], Body=SENTINEL[1])
        with disable_auditlog():
            AuditLogIndex.objects.all().delete()
            LogEntry.objects.all().delete()
            InstructorHours.objects.all().delete()
            Instructor.objects.all().delete()
            MedicalCertificate.objects.all().delete()
            SportAssociationDocumentsArchive.objects.all().delete()
            Document.objects.all().delete()
            documents = []
            for i, (association, date, filename, age, filepath, stored) in enumerate(self.controls()):
                document = Document.objects.create(document_id=UUID(int=86200 + i), creation_date=now - age,
                    filepath=filepath, filename=filename, file_size_bytes=10 + i, token=UUID(int=86300 + i))
                SportAssociationDocumentsArchive.objects.create(sport_association_documents_archive_id=UUID(int=86400 + i),
                    sport_association_id=self.associations[association], document=document, date=today - timedelta(days=date))
                if stored:
                    client.put_object(Bucket=BUCKET, Key=LOCATION + '/' + filepath, Body=f'export {i}'.encode())
                documents.append(document)
            # An old export document also referenced by SET_NULL instructor hours.
            instructor = Instructor.objects.create(instructor_id=UUID(int=86500), user_id=self.owners[0],
                first_name='Export', last_name='Fixture')
            InstructorHours.objects.create(instructor_hours_id=UUID(int=86501), instructor=instructor, hours='1.00',
                creation_date=now, date=today, document=documents[1])
            if cascade:
                # A medical certificate cascades from a young export document.
                MedicalCertificate.objects.create(medical_id=UUID(int=86600), document=documents[0])
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
        state['auditlog.LogEntry'] = sorted(rows, key=lambda row: (row['content_type_id'], row['object_pk']))
        indexes = []
        for index in state['application.AuditLogIndex']:
            index = dict(index)
            assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
            index.pop('audit_log_index_id'); index['created_at'] = 'run'
            index['log_entry_id'] = keys[index['log_entry_id']]
            indexes.append(index)
        state['application.AuditLogIndex'] = sorted(indexes, key=lambda row: row['log_entry_id'])
        return state


class ExportRehearsal(Rehearsal):
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
        self.fixture = ExportFixture(storage)
        self.django = self.django_expected()

    def django_expected(self):
        """Run the actual Django task on a fresh seed against the private MinIO."""
        from contextlib import redirect_stdout
        import io
        from django.test import override_settings
        from application import tasks
        before = self.fixture.seed()
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        since = datetime.now(timezone.utc)
        with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=self.storage['access'],
                               AWS_SECRET_ACCESS_KEY=self.storage['secret'], AWS_STORAGE_BUCKET_NAME=BUCKET,
                               AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=self.storage['endpoint'],
                               AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path'), redirect_stdout(io.StringIO()):
            result = tasks.cleanup_old_exports_task.run()
        until = datetime.now(timezone.utc)
        after = self.fixture.snapshot()
        # Authored checks that the oracle took the intended paths.
        assert result == {'deleted_count': 5}, result
        archives = {row['sport_association_documents_archive_id'].int - 86400
                    for row in after['application.SportAssociationDocumentsArchive']}
        assert archives == {0, 2, 3, 7, 8, 10, 11, 12}, sorted(archives)
        assert set(after['storage']) == {SENTINEL[0], LOCATION + '/exports/a/young_1.zip',
            LOCATION + '/exports/a/young_3.zip', LOCATION + '/exports/a/report.zip',
            LOCATION + '/exports/a/upper.zip', LOCATION + '/exports/c/one.zip', LOCATION + '/exports/c/two.zip'}, after['storage']
        assert [row['document_id'] for row in after['application.InstructorHours']] == [None]
        assert len(after['auditlog.LogEntry']) == 10 and len(after['application.AuditLogIndex']) == 4
        assert before != after
        return self.fixture.normalized(after, since, until)

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-export-' + name)
        self.connections.append(connection)
        return connection

    def seed_job(self, *, version=1, payload='{}', association=NIL, attempts=3, cascade=False, reseed=True):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(cascade=cascade) if reseed else self.fixture.snapshot()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
          (identifier, association, KIND, version, identifier, payload, attempts))
        self.since = datetime.now(timezone.utc)
        return identifier, before

    def cleanup_complete(self, identifier, result):
        await_condition(lambda: self.job(identifier)[0] in ('completed', 'failed'), 'registered export cleanup worker durable completion')
        assert self.job(identifier)[:3] == ('completed', 1, ''), self.job(identifier)
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(result,)]

    def compare(self):
        until = datetime.now(timezone.utc)
        actual = self.fixture.normalized(self.fixture.snapshot(), self.since, until)
        state = self.django
        if actual != state:
            differences = {label: {'django': state.get(label), 'go': actual.get(label)}
                           for label in set(state) | set(actual) if state.get(label) != actual.get(label)}
            (self.output / 'difference.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == state, 'Go export cleanup differs from the Django task on the same seed'

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_job()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0) and self.fixture.snapshot() == before
        self.stop(process)
        self.case('default Django owner does not claim or delete exports')

        self.handoff('go')
        process = self.worker()
        self.cleanup_complete(identifier, {'deleted_count': 5})
        self.stop(process)
        self.compare()
        self.case('native worker reproduces the Django task state, storage and result')

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(self.fixture.associations[0])}, 'task_failed')]:
            identifier, before = self.seed_job(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid export cleanup envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert self.fixture.snapshot() == before
        self.case('unsupported version payload and tenant fail before business effects')

        identifier, _ = self.seed_job()
        process = self.worker(); self.cleanup_complete(identifier, {'deleted_count': 5}); self.stop(process)
        self.compare()
        identifier, later = self.seed_job(reseed=False)
        process = self.worker(); self.cleanup_complete(identifier, {'deleted_count': 0}); self.stop(process)
        assert self.fixture.snapshot() == later
        self.case('a later run on the cleaned state deletes nothing')

        identifier, before = self.seed_job(cascade=True)
        process = self.worker()
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'cascading export reference refused')
        self.stop(process)
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('task_failed',)]
        assert self.fixture.snapshot() == before
        self.case('a cascading document reference refuses the run before any effect')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)


def main():
    require_production_python()
    import boto3
    from botocore.config import Config
    from TESTS.adapters.django.instance_logo import minio
    output = ROOT / 'quality-reports/go-migration/export-cleanup-worker-process'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip(), 'schema': 'actual Django current models; no historical migration proof'}
    report.write_text(json.dumps(result)+'\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go') or not shutil.which('docker'):
        raise RuntimeError('Private PostgreSQL, Docker (MinIO) and Go are required')
    binaries = Path(postgres).parent
    environment = {k: v for k, v in os.environ.items()
                   if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'DOCKER_HOST', 'DOCKER_CONFIG')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-export-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
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
                    subprocess.run(['go', 'build', '-race', '-o', str(work/name), './cmd/'+name], cwd=ROOT/'BE_GO',
                        env=environment, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=300)
                run([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
                attempted = True
                run([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
                run([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
                initialize_current_schema(work, work, '5432', DATABASE, log)
                rehearsal = ExportRehearsal(work, output, environment, binaries, storage)
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
                            try: run([binaries/'pg_ctl', '-D', work/'postgres', '-m', 'immediate', '-w', 'stop'])
                            except subprocess.CalledProcessError:
                                if (work/'postgres/postmaster.pid').exists(): raise
                    finally:
                        if (work/'postgres.log').exists(): shutil.copyfile(work/'postgres.log', output/'postgres.log')
                        report.write_text(json.dumps(result, indent=2, default=str)+'\n')
    paths = ['TESTS/adapters/go/export_cleanup_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/cmd/worker/export_cleanup.go', 'BE_GO/internal/service/export_cleanup.go',
             'BE_GO/internal/repository/export_cleanup.go', 'BE_GO/internal/domain/export_cleanup.go',
             'BE_GO/pkg/storage/documentobjects/s3.go', 'BE/application/tasks.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str)+'\n'); print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
