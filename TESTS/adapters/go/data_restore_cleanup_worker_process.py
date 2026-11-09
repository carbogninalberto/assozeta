"""Native cleanup_data_restores worker on actual Django-created current schema.

Private PostgreSQL, a private MinIO (the S3 default storage) and the restore
lock file. Each seed is first processed by the actual Django
``cleanup_data_restores`` task (``instance.restore.cleanup``) with the
production S3Boto3Storage backend pointed at the private MinIO and
DATA_RESTORE_LOCK_PATH on the fixture's lock file; the native Go worker must
then leave the identical complete managed-model state on the same reseeded
rows (receipt ``updated_at`` normalized when it lies in the run window), the
identical bucket listing, the same lock file (present, empty, not held) and a
NULL result. Explicit fixture producers and ownership are not installed
activation.
"""
import fcntl
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

DATABASE = 'assozeta_contract_restore_cleanup_worker'
KIND = 'instance.restore.cleanup'
BUCKET = 'restore-cleanup'
LOCATION = 'private'
SENTINELS = {LOCATION + '/unrelated/keep.txt': 'unrelated object',
             LOCATION + '/restores-other/keep.txt': 'neighbouring prefix'}
EXPECTED = {
    'default Django owner does not claim or clean restores',
    'native worker reproduces the Django task state, storage and lock file',
    'unsupported version payload and tenant fail before business effects',
    'a later run only refreshes the cleaned receipts',
    'a held restore lock completes without effects as Django returns None',
    'DATA_RESTORE_RECOVERY_BACKUPS from the environment selects the retained archives',
    'completed media outside the ported subset refuses the run before any effect',
}


def U(n):
    return UUID(int=87000 + n)


class RestoreFixture:
    """Receipts in every state with their upload, media and recovery objects."""
    def __init__(self, storage, lock):
        from application.models import User, SportAssociation
        self.storage, self.lock = storage, lock
        # One clock for every seed so reseeded rows are identical; review
        # receipts sit far from the 24-hour boundary.
        self.now = datetime.now(timezone.utc).replace(microsecond=123456)
        User.original_objects.bulk_create([User(user_id=U(1), username='restore-cleanup-owner', role=1,
            password='!private-fixture')])
        SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=U(2), user_id=U(1),
            denomination='Private restore fixture', tax_code='00000000000')])

    def receipts(self):
        """(n, state, stage, age, upload_path, backup_path, media, objects)."""
        hour, day = timedelta(hours=1), timedelta(days=1)
        r = {n: str(U(100 + n)) for n in range(1, 13)}
        return [
            (1, 'review', 'validated', 48 * hour, f'restores/{r[1]}/upload/r1.zip', '', {},
             [f'restores/{r[1]}/upload/r1.zip', f'restores/{r[1]}/media/m1/a.pdf']),
            (2, 'review', 'validated', hour, f'restores/{r[2]}/upload/r2.zip', '', {},
             [f'restores/{r[2]}/upload/r2.zip', f'restores/{r[2]}/media/m2/b.pdf']),
            (3, 'failed', 'failed', 10 * day, f'restores/{r[3]}/upload/missing.zip', f'restores/{r[3]}/recovery/b3.zip', {},
             [f'restores/{r[3]}/upload/extra/part.bin', f'restores/{r[3]}/media/x/y/z.pdf',
              f'restores/{r[3]}/recovery/b3.zip', f'restores/{r[3]}/recovery/old.zip']),
            (4, 'cancelled', 'cancelled', 5 * day, '', '', {}, []),
            (5, 'completed', 'completed', day, '', f'restores/{r[5]}/recovery/b5.zip',
             {'doc': f'restores/{r[5]}/media/u1/doc.pdf', 'sig': f'restores/{r[5]}/media/u2/sig.png',
              'gone': f'restores/{r[5]}/media/u3/gone.pdf', 'dup': f'restores/{r[5]}/media/u3/gone.pdf'},
             [f'restores/{r[5]}/recovery/b5.zip', f'restores/{r[5]}/recovery/attempt1.zip',
              f'restores/{r[5]}/recovery/nested/keep.zip', f'restores/{r[5]}/upload/left.zip',
              f'restores/{r[5]}/media/u1/doc.pdf', f'restores/{r[5]}/media/u2/sig.png', f'restores/{r[5]}/media/u3/gone.pdf']),
            (6, 'completed', 'completed', 3 * day, '', f'restores/{r[6]}/recovery/b6.zip',
             {'k': f'restores/{r[6]}/media/k/key.png'},
             [f'restores/{r[6]}/recovery/b6.zip', f'restores/{r[6]}/media/k/key.png']),
            (7, 'queued', 'queued', 2 * day, f'restores/{r[7]}/upload/q.zip', f'restores/{r[7]}/recovery/b7.zip', {},
             [f'restores/{r[7]}/upload/q.zip', f'restores/{r[7]}/recovery/b7.zip', f'restores/{r[7]}/recovery/old.zip']),
            (8, 'running', 'restoring', 2 * day, f'restores/{r[8]}/upload/run.zip', '', {'m': f'restores/{r[8]}/media/m/f.pdf'},
             [f'restores/{r[8]}/upload/run.zip', f'restores/{r[8]}/media/m/f.pdf']),
            (9, 'completed', 'completed', 6 * day, '', '', {}, []),
            (10, 'failed', 'failed', 2 * day, '../outside/r10.zip', '', {}, [f'restores/{r[10]}/media/a/b.pdf']),
            (11, 'review', 'validated', 30 * hour, '', '', {}, [f'restores/{r[11]}/recovery/stray.zip']),
        ]

    def seed(self, *, unsupported=False, keep_lock=False):
        from auditlog.context import disable_auditlog
        with disable_auditlog():
            return self._seed(unsupported, keep_lock)

    def _seed(self, unsupported, keep_lock):
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        from instance.models import DataRestore
        from docmanager.models import Document
        from application.models import Subscription
        now = self.now
        assert datetime.now(timezone.utc) - now < timedelta(minutes=50), 'fixture clock margin exceeded'
        client = self.storage['client']
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET):
            for item in page.get('Contents', []):
                client.delete_object(Bucket=BUCKET, Key=item['Key'])
        for key, body in SENTINELS.items():
            client.put_object(Bucket=BUCKET, Key=key, Body=body.encode())
        if self.lock.exists() and not keep_lock:
            self.lock.unlink()
        AuditLogIndex.objects.all().delete()
        LogEntry.objects.all().delete()
        Subscription.objects.all().delete()
        Document.objects.all().delete()
        DataRestore.objects.all().delete()
        r = {n: str(U(100 + n)) for n in range(1, 13)}
        for n, state, stage, age, upload, backup, media, objects in self.receipts():
            DataRestore.objects.create(id=U(100 + n), owner_id=U(1), association_id=U(200 + n), state=state, stage=stage,
                upload_path=upload, sha256='%064x' % n, version='v-fixture', preview={'n': n}, backup_path=backup,
                backup_sha256=('%064x' % (n + 50)) if backup else '', media=media, attempts=n % 3,
                completed_at=(now - age + timedelta(minutes=5)) if state == 'completed' else None)
            for key in objects:
                client.put_object(Bucket=BUCKET, Key=LOCATION + '/' + key, Body=f'object {key}'.encode())
        if unsupported:
            DataRestore.objects.create(id=U(112), owner_id=U(1), association_id=U(212), state='completed', stage='completed',
                sha256='0' * 64, media=['restores/unsupported'])
        for n, state, stage, age, *_ in self.receipts() + ([(12, '', '', timedelta(days=4))] if unsupported else []):
            DataRestore.objects.filter(pk=U(100 + n)).update(created_at=now - age, updated_at=now - age + timedelta(seconds=n))
        # References that keep restored attachments: a document filepath, a
        # signature URL and a signature storage key.
        Document.objects.create(document_id=U(301), filepath=f'restores/{r[5]}/media/u1/doc.pdf', filename='doc.pdf',
            file_size_bytes=1, token=U(302), creation_date=now)
        Subscription.objects.bulk_create([
            Subscription(subscription_id=U(401), sport_association_id=U(2), user_id=U(1), creation_date=now,
                         signature_url=f'https://cdn.example.invalid/{LOCATION}/restores/{r[5]}/media/u2/sig.png'),
            Subscription(subscription_id=U(402), sport_association_id=U(2), user_id=U(1), creation_date=now,
                         signature_storage_key=f'restores/{r[6]}/media/k/key.png')])
        return self.snapshot()

    def snapshot(self):
        from django.apps import apps
        state = {m._meta.label: list(m._base_manager.order_by('pk').values())
                 for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        state['storage'] = self.listing()
        state['lock'] = self.lock.read_bytes() if self.lock.exists() else None
        return state

    def listing(self):
        client = self.storage['client']
        objects = {}
        for page in client.get_paginator('list_objects_v2').paginate(Bucket=BUCKET):
            for item in page.get('Contents', []):
                objects[item['Key']] = client.get_object(Bucket=BUCKET, Key=item['Key'])['Body'].read().decode()
        return objects

    @staticmethod
    def normalized(state, before, since, until):
        """Receipts saved during the run carry the run clock, those saved by an
        earlier run an earlier clock; the rest must keep the seeded value."""
        state = dict(state)
        seeded = {row['id']: row['updated_at'] for row in before['instance.DataRestore']}
        rows = []
        for row in state['instance.DataRestore']:
            row = dict(row)
            if row['updated_at'] != seeded.get(row['id']):
                if row['updated_at'] < since:
                    row['updated_at'] = 'earlier'
                else:
                    assert row['updated_at'] <= until, 'receipt saved outside the run window'
                    row['updated_at'] = 'run'
            rows.append(row)
        state['instance.DataRestore'] = rows
        return state


class RestoreCleanupRehearsal(Rehearsal):
    def __init__(self, work, output, environment, binaries, storage):
        self.work, self.output, self.env, self.binaries = work, output, environment, binaries
        self.children, self.connections, self.completed = [], [], []
        self.connection = self.connect('fixture')
        self.connection.autocommit = True
        self.env_file = work / 'existing.env'
        self.lock = work / 'operations.lock'
        values = dict(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
            DBPASSWORD='private-worker-password-canary', SECRET_KEY='private-worker-secret-canary', DEBUG='False',
            JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
            SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', AWS_ACCESS_KEY_ID=storage['access'], AWS_SECRET_ACCESS_KEY=storage['secret'],
            AWS_STORAGE_BUCKET_NAME=BUCKET, AWS_S3_REGION_NAME='us-east-1',
            AWS_S3_ENDPOINT_URL=storage['endpoint'], AWS_LOCATION=LOCATION,
            REDIS_HOST='127.0.0.1', REDIS_PORT='9', REDIS_USERNAME='default', REDIS_PASSWORD='private-redis-password-canary',
            WHITELABEL_NAME='Assozeta Fixture', DATA_RESTORE_LOCK_PATH=str(self.lock))
        self.env_file.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        self.env_file.chmod(0o600)
        self.storage = storage
        self.fixture = RestoreFixture(storage, self.lock)
        self.django = self.django_expected()

    def django_run(self, *, retained=None, unsupported=False):
        """Run the actual Django task on a fresh seed against the private MinIO."""
        from contextlib import redirect_stdout
        import io
        import logging
        from django.test import override_settings
        from instance import tasks
        before = self.fixture.seed(unsupported=unsupported)
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        previous = os.environ.pop('DATA_RESTORE_RECOVERY_BACKUPS', None)
        assert previous is None
        if retained is not None:
            os.environ['DATA_RESTORE_RECOVERY_BACKUPS'] = retained
        since = datetime.now(timezone.utc)
        logging.disable(logging.CRITICAL)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=self.storage['access'],
                                   AWS_SECRET_ACCESS_KEY=self.storage['secret'], AWS_STORAGE_BUCKET_NAME=BUCKET,
                                   AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=self.storage['endpoint'],
                                   AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path',
                                   DATA_RESTORE_LOCK_PATH=str(self.lock)), redirect_stdout(io.StringIO()):
                result = tasks.cleanup_data_restores.run()
        finally:
            logging.disable(logging.NOTSET)
            os.environ.pop('DATA_RESTORE_RECOVERY_BACKUPS', None)
        until = datetime.now(timezone.utc)
        assert result is None, result
        return before, self.fixture.snapshot(), since, until

    def django_expected(self):
        before, after, since, until = self.django_run()
        self.seeded = before
        # Authored checks that the oracle took the intended paths.
        receipts = {row['id'].int - 87100: row for row in after['instance.DataRestore']}
        seeded = {row['id'].int - 87100: row for row in before['instance.DataRestore']}
        r = {n: str(U(100 + n)) for n in range(1, 13)}
        assert (receipts[1]['state'], receipts[1]['stage'], receipts[1]['upload_path']) == ('cancelled', 'expired', '')
        assert receipts[2] == seeded[2] and receipts[7] == seeded[7] and receipts[8] == seeded[8]
        assert (receipts[3]['upload_path'], receipts[3]['backup_path'], receipts[3]['backup_sha256']) == ('', '', '')
        assert receipts[4]['updated_at'] != seeded[4]['updated_at'] and receipts[4]['media'] == {}
        assert receipts[5]['media'] == {'doc': f'restores/{r[5]}/media/u1/doc.pdf', 'sig': f'restores/{r[5]}/media/u2/sig.png'}
        assert receipts[5]['backup_path'] == seeded[5]['backup_path']
        assert (receipts[6]['backup_path'], receipts[6]['media']) == ('', seeded[6]['media'])
        assert receipts[9] == seeded[9], 'a completed receipt without objects is not saved'
        assert receipts[10] == seeded[10], 'a name outside the location stops that receipt cleanup'
        assert receipts[11]['state'] == 'cancelled' and receipts[11]['stage'] == 'expired'
        keys = {key.removeprefix(LOCATION + '/') for key in after['storage']} - {k.removeprefix(LOCATION + '/') for k in SENTINELS}
        assert keys == {f'restores/{r[2]}/upload/r2.zip', f'restores/{r[2]}/media/m2/b.pdf',
                        f'restores/{r[5]}/recovery/b5.zip', f'restores/{r[5]}/recovery/nested/keep.zip',
                        f'restores/{r[5]}/media/u1/doc.pdf', f'restores/{r[5]}/media/u2/sig.png',
                        f'restores/{r[6]}/media/k/key.png', f'restores/{r[7]}/upload/q.zip',
                        f'restores/{r[7]}/recovery/b7.zip', f'restores/{r[7]}/recovery/old.zip',
                        f'restores/{r[8]}/upload/run.zip', f'restores/{r[8]}/media/m/f.pdf',
                        f'restores/{r[10]}/media/a/b.pdf'}, sorted(keys)
        assert after['lock'] == b'' and before['lock'] is None
        return self.fixture.normalized(after, before, since, until)

    def connect(self, name):
        connection = psycopg2.connect(host=str(self.work), user='contracts', dbname=DATABASE,
                                     application_name='fixture-restore-' + name)
        self.connections.append(connection)
        return connection

    def seed_job(self, *, version=1, payload='{}', association=NIL, attempts=3, unsupported=False, reseed=True, keep_lock=False):
        self.sql('DELETE FROM assozeta_go_jobs')
        before = self.fixture.seed(unsupported=unsupported, keep_lock=keep_lock) if reseed else self.fixture.snapshot()
        identifier = str(uuid4())
        self.sql('''INSERT INTO assozeta_go_jobs(id,association_id,kind,version,idempotency_key,payload,max_attempts,available_at)
          VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,clock_timestamp())''',
          (identifier, association, KIND, version, identifier, payload, attempts))
        self.since = datetime.now(timezone.utc)
        return identifier, before

    def cleanup_complete(self, identifier):
        await_condition(lambda: self.job(identifier)[0] in ('completed', 'failed'), 'registered restore cleanup worker durable completion')
        assert self.job(identifier)[:3] == ('completed', 1, ''), self.job(identifier)
        assert self.sql('SELECT result FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(None,)]

    def compare(self, expected, before):
        until = datetime.now(timezone.utc)
        actual = self.fixture.normalized(self.fixture.snapshot(), before, self.since, until)
        if actual != expected:
            differences = {label: {'django': expected.get(label), 'go': actual.get(label)}
                           for label in set(expected) | set(actual) if expected.get(label) != actual.get(label)}
            (self.output / 'difference.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert actual == expected, 'Go restore cleanup differs from the Django task on the same seed'
        self.assert_file_lease(held=False)

    def hold_lock(self):
        handle = self.lock.open('a')
        fcntl.flock(handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return handle

    def run(self):
        self.command('migrate')
        assert self.sql('SELECT runtime,generation FROM assozeta_go_job_ownership') == [('django', 1)]
        identifier, before = self.seed_job()
        process = self.worker()
        await_condition(self.observed_claim, 'actual registered worker queue query')
        time.sleep(.2)
        assert self.job(identifier)[:2] == ('pending', 0)
        assert {k: v for k, v in self.fixture.snapshot().items() if k != 'lock'} == {k: v for k, v in before.items() if k != 'lock'}
        self.stop(process)
        self.case('default Django owner does not claim or clean restores')

        self.handoff('go')
        identifier, before = self.seed_job()
        process = self.worker()
        self.cleanup_complete(identifier)
        self.stop(process)
        self.compare(self.django, before)
        self.case('native worker reproduces the Django task state, storage and lock file')

        for kwargs, code in [({'version': 2}, 'unsupported_version'),
                             ({'payload': '{"unexpected":true}'}, 'task_failed'),
                             ({'association': str(U(2))}, 'task_failed')]:
            identifier, before = self.seed_job(**kwargs)
            process = self.worker()
            await_condition(lambda: self.job(identifier)[0] == 'failed', 'invalid restore cleanup envelope rejected')
            self.stop(process)
            assert self.job(identifier)[:2] == ('failed', 1)
            assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [(code,)]
            assert {k: v for k, v in self.fixture.snapshot().items() if k != 'lock'} == {k: v for k, v in before.items() if k != 'lock'}
        self.case('unsupported version payload and tenant fail before business effects')

        # A second Django run on the cleaned state, then the same on Go: only
        # the failed and cancelled receipts are saved again (media={}).
        self.fixture.seed()
        self.django_task()
        since = datetime.now(timezone.utc)
        self.django_task()
        django_later = self.fixture.normalized(self.fixture.snapshot(), self.seeded, since, datetime.now(timezone.utc))
        assert sum(row['updated_at'] == 'run' for row in django_later['instance.DataRestore']) == 4
        identifier, _ = self.seed_job()
        process = self.worker(); self.cleanup_complete(identifier); self.stop(process)
        identifier, _ = self.seed_job(reseed=False)
        process = self.worker(); self.cleanup_complete(identifier); self.stop(process)
        self.compare(django_later, self.seeded)
        self.case('a later run only refreshes the cleaned receipts')

        # Busy: Django returns None without effects while another holder has the lock.
        self.fixture.seed()
        handle = self.hold_lock()
        try:
            before = self.fixture.snapshot()
            self.django_task()
            assert self.fixture.snapshot() == before, 'Django cleaned while the lock was held'
            identifier, before = self.seed_job(keep_lock=True)
            process = self.worker()
            self.cleanup_complete(identifier)
            self.stop(process)
            assert self.fixture.snapshot() == before
        finally:
            handle.close()
        self.assert_file_lease(held=False)
        self.case('a held restore lock completes without effects as Django returns None')

        before, after, since, until = self.django_run(retained=' 0 ')
        expected = self.fixture.normalized(after, before, since, until)
        assert [row['backup_path'] for row in after['instance.DataRestore'] if row['id'] == U(105)] == ['']
        identifier, before = self.seed_job()
        environment = self.env
        self.env = dict(environment, DATA_RESTORE_RECOVERY_BACKUPS=' 0 ')
        try:
            process = self.worker()
        finally:
            self.env = environment
        self.cleanup_complete(identifier)
        self.stop(process)
        self.compare(expected, before)
        self.case('DATA_RESTORE_RECOVERY_BACKUPS from the environment selects the retained archives')

        identifier, before = self.seed_job(unsupported=True)
        process = self.worker()
        await_condition(lambda: self.job(identifier)[0] == 'failed', 'unsupported media refused')
        self.stop(process)
        assert self.sql('SELECT error_code FROM assozeta_go_jobs WHERE id=%s', (identifier,)) == [('task_failed',)]
        assert {k: v for k, v in self.fixture.snapshot().items() if k != 'lock'} == {k: v for k, v in before.items() if k != 'lock'}
        self.assert_file_lease(held=False)
        self.case('completed media outside the ported subset refuses the run before any effect')
        assert set(self.completed) == EXPECTED and len(self.completed) == len(EXPECTED)

    def django_task(self):
        from contextlib import redirect_stdout
        import io
        import logging
        from django.test import override_settings
        from instance import tasks
        storages = {'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
        logging.disable(logging.CRITICAL)
        try:
            with override_settings(STORAGES=storages, AWS_ACCESS_KEY_ID=self.storage['access'],
                                   AWS_SECRET_ACCESS_KEY=self.storage['secret'], AWS_STORAGE_BUCKET_NAME=BUCKET,
                                   AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=self.storage['endpoint'],
                                   AWS_LOCATION=LOCATION, AWS_S3_ADDRESSING_STYLE='path',
                                   DATA_RESTORE_LOCK_PATH=str(self.lock)), redirect_stdout(io.StringIO()):
                assert tasks.cleanup_data_restores.run() is None
        finally:
            logging.disable(logging.NOTSET)


def main():
    require_production_python()
    import boto3
    from botocore.config import Config
    from TESTS.adapters.django.instance_logo import minio
    output = ROOT / 'quality-reports/go-migration/restore-cleanup-worker-process'
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
    with tempfile.TemporaryDirectory(prefix='az-restore-worker-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
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
                rehearsal = RestoreCleanupRehearsal(work, output, environment, binaries, storage)
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
    paths = ['TESTS/adapters/go/data_restore_cleanup_worker_process.py', 'TESTS/adapters/go/age_worker_fixture.py',
             'BE_GO/cmd/worker/main.go', 'BE_GO/cmd/worker/data_restore_cleanup.go', 'BE_GO/cmd/worker/export_cleanup.go',
             'BE_GO/internal/service/data_restore_cleanup.go', 'BE_GO/internal/service/data_restore.go',
             'BE_GO/internal/repository/data_restore_cleanup.go', 'BE_GO/internal/repository/data_restore.go',
             'BE_GO/internal/repository/restore_lock.go', 'BE_GO/internal/domain/data_restore_cleanup.go',
             'BE_GO/pkg/storage/documentobjects/s3.go', 'BE/instance/tasks.py', 'BE/instance/restore/execution.py',
             'BE/instance/restore/locking.py', 'BE/instance/models.py']
    result.update(success=True, private_postgres_stopped=True, race_instrumented=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str)+'\n'); print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
