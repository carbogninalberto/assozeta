"""migrate_subscription_signatures_to_s3: actual Django management command versus the Go command.

Private PostgreSQL with the current Django schema plus the legacy
``application_subscription.signature`` text column that databases created before
migration 0415 still have (0415 removes the field in state only), and a private
MinIO behind the recording storage proxy of the subscription-sign family (it
answers PutObjectAcl itself: 200, or AccessDenied for one subscription's keys).
Both commands use the same S3 settings: AWS_LOCATION, AWS_S3_USE_OBJECT_ACL=True
and AWS_S3_PUBLIC_BASE_URL (Django through the process environment that
core.settings reads plus the S3Boto3Storage backend, Go through the same
variables).

Every authored case resets the same seed, runs ``manage.py
migrate_subscription_signatures_to_s3`` as a separate process, records exit
status, stdout, the storage requests in order, the bucket (keys, bytes,
ContentType, CacheControl), the complete managed-model state and the legacy
column, resets, runs the race-built Go binary with the same arguments and
environment, and requires the identical result. Django must first match the
authored exit status, stdout and per-row outcome. The only generated values are
the storage key timestamps (``signature_<repr(datetime.now().timestamp())>.png``)
and the audit entry ids and times: each is checked (Python float repr, inside the
run window) and then replaced by a placeholder. Django's stderr carries its
logging and tracebacks and is not compared.
"""
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_signature_migration'
LOCATION = 'contract-signatures'
PUBLIC_BASE = 'https://cdn.example.test/public'
SENTINEL = ('subscription-sign/sentinel/keep.txt', b'sentinel')
DJANGO = f'''
import sys
sys.path[:0] = [{str(ROOT / 'BE')!r}, {str(ROOT)!r}]
from unittest.mock import patch
with patch('environ.Env.read_env'):
    import django
    django.setup()
from django.test import override_settings
from django.core.management import execute_from_command_line
storages = {{'default': {{'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'}},
            'staticfiles': {{'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}}}
with override_settings(STORAGES=storages, AWS_S3_ADDRESSING_STYLE='path'):
    execute_from_command_line(['manage.py', 'migrate_subscription_signatures_to_s3', *sys.argv[1:]])
'''

OWNERS = {'A': UUID(int=93001), 'B': UUID(int=93002)}
ASSOCIATIONS = {'A': UUID(int=93011), 'B': UUID(int=93012)}
PNG = b'\x89PNG\r\n\x1a\n' + bytes(range(40))
B64 = base64.b64encode(PNG).decode()
LONG_B64 = base64.b64encode(bytes(range(80))).decode()  # 108 characters
INVALID = ("{'msg': ErrorDetail(string='Firma non valida.', code='invalid_signature'), 'code': ErrorDetail(string='400', "
           "code='invalid_signature'), 'ref': ErrorDetail(string='invalid_signature', code='invalid_signature')}")
STORAGE = ("{'msg': ErrorDetail(string='Storage temporaneamente non disponibile.', code='storage_unavailable'), 'code': "
           "ErrorDetail(string='503', code='storage_unavailable'), 'ref': ErrorDetail(string='signature_storage', "
           "code='storage_unavailable')}")
DATE = 'Invalid date combination: day is out of range for month'
TOO_LONG = 'value too long for type character varying(1024)\n'


def sid(n):
    return UUID(int=0x5100 + n)


# n: (owner, legacy, storage key, url, deleted, outcome, dry-run outcome). Outcomes, read from the command:
# migrate (upload + save), url (keep the URL), populated / notb64 (skipped), ('error', text).
ROWS = {
    1: ('A', 'data:image/png;base64,' + B64, None, None, False, 'migrate', 'migrate'),
    2: ('A', B64, None, None, False, 'migrate', 'migrate'),
    3: ('A', 'http://legacy.example.test/sig/3.png', None, None, False, 'url', 'url'),
    4: ('A', 'https://legacy.example.test/sig/4.png', None, None, False, 'url', 'url'),
    5: ('A', '%%%abc', None, None, False, 'notb64', 'notb64'),            # lenient decode: incomplete quantum
    6: ('A', 'ab cd' + 'efgh', None, None, False, ('error', INVALID), 'migrate'),  # lenient ok, validate=True fails
    7: ('A', ' ', None, None, False, ('error', INVALID), 'migrate'),
    8: ('A', 'data:image/png;base64,', None, None, False, ('error', INVALID), 'migrate'),  # decodes to nothing
    9: ('A', '', None, None, False, None, None),                         # not selected
    10: ('A', None, None, None, False, None, None),                      # not selected
    11: ('A', B64, None, 'https://cdn.example.test/existing.png', False, 'populated', 'populated'),
    12: ('A', 'https://legacy.example.test/sig/12.png', '', '', False, 'url', 'url'),  # empty destinations are free
    13: ('A', B64, None, None, True, 'migrate', 'migrate'),               # soft-deleted rows are processed
    14: ('A', LONG_B64 + 'é', None, None, False, ('error', INVALID), 'migrate'),  # non-ASCII after 100 chars
    15: ('A', 'é' + B64, None, None, False, 'notb64', 'notb64'),      # non-ASCII in the checked prefix
    16: ('A', 'https://legacy.example.test/' + 'x' * 1100, None, None, False, ('error', TOO_LONG), 'url'),
    17: ('B', 'https://legacy.example.test/sig/17.png', None, None, False, ('error', DATE), 'url'),
    18: ('B', B64, None, None, False, ('error', DATE), 'migrate'),        # uploaded, then deleted
    19: ('A', B64, None, None, False, ('error', STORAGE), 'migrate'),     # object ACL denied
    20: ('A', 'https://legacy.example.test/sig/20.png', 'subscriptions/x/old.png', None, False, 'populated', 'populated'),
}
DENIED = str(sid(19))
SELECTED = [n for n, row in ROWS.items() if row[1]]


def authored_stdout(dry_run, batch_size):
    """The command's stdout for the seed, from the per-row outcomes above."""
    lines = []
    if dry_run:
        lines.append('Running in DRY-RUN mode - no changes will be made')
    total = len(SELECTED)
    lines.append(f'Found {total} legacy signatures')
    migrated = skipped = errors = 0
    for offset in range(0, total, batch_size):
        lines += ['', f'Processing batch {offset // batch_size + 1} ({offset + 1}-{min(offset + batch_size, total)} of {total})']
        for n in SELECTED[offset:offset + batch_size]:
            outcome = ROWS[n][6 if dry_run else 5]
            i = sid(n)
            if outcome == 'migrate':
                migrated += 1
                lines.append(f'[DRY-RUN] Would migrate {i} to configured storage' if dry_run
                             else f'Migrated {i} to storage key subscriptions/{i}/signature_<ts>.png')
            elif outcome == 'url':
                migrated += 1
                lines.append(f'[DRY-RUN] Would keep legacy URL for {i}' if dry_run else f'Kept legacy URL for {i}')
            elif outcome == 'populated':
                skipped += 1
                lines.append(f'Skipping {i} - destination already populated')
            elif outcome == 'notb64':
                skipped += 1
                lines.append(f'Skipping {i} - not valid base64 or URL')
            else:
                errors += 1
                lines.append(f'Error processing subscription {i}: {outcome[1]}'.rstrip('\n'))
    lines += ['', '=' * 60, 'Migration complete!', f'Total legacy signatures: {total}', f'Migrated: {migrated}',
              f'Skipped: {skipped}']
    if errors:
        lines.append(f'Errors: {errors}')
    lines.append('=' * 60)
    return '\n'.join(lines) + '\n'


def partial_stdout(dry_run):
    return ('Running in DRY-RUN mode - no changes will be made\n' if dry_run else '') + f'Found {len(SELECTED)} legacy signatures\n'




def negative_stdout():
    total = len(SELECTED)
    return '\n'.join([f'Found {total} legacy signatures', '', '=' * 60, 'Migration complete!',
                      f'Total legacy signatures: {total}', 'Migrated: 0', 'Skipped: 0', '=' * 60]) + '\n'


# (name, arguments, legacy column present, authored (exit status, stdout)).
CASES = [
    ('default batch', [], True, (0, authored_stdout(False, 100))),
    ('batches of four', ['--batch-size', '4'], True, (0, authored_stdout(False, 4))),
    ('abbreviated inline batch size', ['--batch=3'], True, (0, authored_stdout(False, 3))),
    ('dry run', ['--dry-run'], True, (0, authored_stdout(True, 100))),
    ('abbreviated dry run, batch of one', ['--dry', '--batch-size', '1'], True, (0, authored_stdout(True, 1))),
    ('negative batch size processes nothing', ['--batch-size', '-2'], True, (0, negative_stdout())),
    ('batch size zero', ['--batch-size', '0'], True, (1, partial_stdout(False))),
    ('batch size not an integer', ['--batch-size', 'x'], True, (2, '')),
    ('no legacy column', [], False, (0, 'No legacy subscription.signature column found\n')),
    ('no legacy column, dry run, batch size zero', ['--dry-run', '--batch-size', '0'], False,
     (0, 'No legacy subscription.signature column found\n')),
]

TIMESTAMP = re.compile(r'signature_(\d+\.\d+)\.png')


class Fixture:
    def __init__(self, client, bucket, proxy):
        from application.models import User, SportAssociation
        from auditlog.context import disable_auditlog
        self.client, self.bucket, self.proxy = client, bucket, proxy
        with disable_auditlog():
            User.original_objects.bulk_create([
                User(user_id=OWNERS['A'], username='SIGNATURE-OWNER-A', email='a@example.test', password='!private-fixture', role=1),
                # Balance year starting on 30 February: Subscription.save() raises before writing.
                User(user_id=OWNERS['B'], username='SIGNATURE-OWNER-B', email='b@example.test', password='!private-fixture', role=1,
                     subscription_start_month=2, subscription_start_day=30)])
            SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=ASSOCIATIONS[k], user_id=OWNERS[k],
                denomination='Private signature fixture', tax_code='00000000000') for k in ('A', 'B')])
        self.column(True)
        self.seed_subscriptions()
        self.seed = self.snapshot()

    @staticmethod
    def column(present):
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute('ALTER TABLE application_subscription ' +
                           ('ADD COLUMN IF NOT EXISTS signature text NULL' if present else 'DROP COLUMN IF EXISTS signature'))

    @staticmethod
    def seed_subscriptions():
        from application.models import Subscription
        from django.db import connection
        Subscription._base_manager.bulk_create([Subscription(
            subscription_id=sid(n), sport_association_id=ASSOCIATIONS[owner], user_id=OWNERS[owner], status_flag=1,
            creation_date=datetime(2026, 1, 10, 9, tzinfo=timezone.utc), deleted=deleted,
            signature_storage_key=key, signature_url=url) for n, (owner, _, key, url, deleted, *_) in ROWS.items()])
        with connection.cursor() as cursor:
            for n, row in ROWS.items():
                cursor.execute('UPDATE application_subscription SET signature=%s WHERE subscription_id=%s', [row[1], sid(n)])

    def reset(self, column=True):
        from application.models import Subscription
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        with disable_auditlog():
            AuditLogIndex.objects.all().delete()
            LogEntry.objects.all().delete()
            Subscription._base_manager.all().delete()
        self.column(True)
        self.seed_subscriptions()
        for key in self.objects():
            if key != SENTINEL[0]:
                self.client.delete_object(Bucket=self.bucket, Key=key)
        self.proxy.take()
        assert self.snapshot() == self.seed, 'fixture reset'
        if not column:
            self.column(False)

    def objects(self):
        pages = self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket)
        return sorted(item['Key'] for page in pages for item in page.get('Contents', []))

    def snapshot(self):
        from django.apps import apps
        from django.db import connection
        state = {m._meta.label: list(m._base_manager.order_by('pk').values())
                 for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
        with connection.cursor() as cursor:
            cursor.execute("SELECT EXISTS(SELECT 1 FROM information_schema.columns WHERE table_name='application_subscription' "
                           "AND column_name='signature')")
            if cursor.fetchone()[0]:
                cursor.execute('SELECT subscription_id::text, signature FROM application_subscription ORDER BY subscription_id')
                state['legacy'] = cursor.fetchall()
            else:
                state['legacy'] = 'absent'
        stored = {}
        for key in self.objects():
            item = self.client.get_object(Bucket=self.bucket, Key=key)
            stored[key] = {'bytes': base64.b64encode(item['Body'].read()).decode(), 'type': item.get('ContentType'),
                           'cache': item.get('CacheControl'), 'encoding': item.get('ContentEncoding')}
        state['bucket'] = stored
        return json.loads(json.dumps(state, default=str))


def normalized(value, since, until):
    """Replace checked generated values (storage key timestamps) by placeholders."""
    text = json.dumps(value, sort_keys=True)

    def stamp(match):
        assert repr(float(match.group(1))) == match.group(1), 'timestamp is a Python float repr'
        assert since - 1 <= float(match.group(1)) <= until + 1, 'timestamp inside the run'
        return 'signature_<ts>.png'
    return json.loads(TIMESTAMP.sub(stamp, text))


def normalized_state(state, since, until):
    state = normalized(state, since, until)
    entries = {}
    for entry in state['auditlog.LogEntry']:
        moment = datetime.fromisoformat(entry['timestamp']).timestamp()
        assert since - 1 <= moment <= until + 1, 'audit timestamp inside the run'
        entries[entry['id']] = f"entry-{entry['object_pk']}"
        entry.update(id=entries[entry['id']], timestamp='run')
        if isinstance(entry['changes'], str):
            entry['changes'] = json.loads(entry['changes'])
    for row in state['application.AuditLogIndex']:
        row.update(audit_log_index_id='index', log_entry_id=entries[row['log_entry_id']], created_at='run')
    state['auditlog.LogEntry'].sort(key=lambda e: e['id'])
    state['application.AuditLogIndex'].sort(key=lambda r: r['log_entry_id'])
    return state


def authored_effect(state, arguments):
    """Rows and objects match the authored outcomes of a full (non dry-run) run."""
    rows = {r['subscription_id']: r for r in state['application.Subscription']}
    entries = {e['object_pk']: e for e in state['auditlog.LogEntry']}
    indexes = {r['log_entry_id']: r for r in state['application.AuditLogIndex']}
    # A dry run, or a negative batch size (no batches), changes nothing.
    dry = any(a.startswith('--dry') for a in arguments) or '-2' in arguments
    for n, (owner, legacy, key, url, *_rest) in ROWS.items():
        i = str(sid(n))
        outcome = _rest[1]
        row, entry = rows[i], entries.get(i)
        if dry or outcome in (None, 'populated', 'notb64') or (isinstance(outcome, tuple) and outcome[1] != TOO_LONG):
            assert (row['signature_storage_key'], row['signature_url']) == (key, url), f'{n}: unchanged'
            assert entry is None, f'{n}: no audit entry'
        elif outcome == 'url':
            assert (row['signature_storage_key'], row['signature_url']) == (None, legacy), f'{n}: URL kept'
            changes = {'signature_url': [str(url), legacy]}
            if key is not None:
                changes['signature_storage_key'] = [key, 'None']
            assert entry['changes'] == changes and entry['action'] == 1 and entry['actor_id'] is None, f'{n}: entry'
            assert indexes[entry['id']]['resolution_path'] == 'direct', f'{n}: index'
        elif outcome == 'migrate':
            name = f'subscriptions/{i}/signature_<ts>.png'
            public = f'{PUBLIC_BASE}/{LOCATION}/{name}'
            assert (row['signature_storage_key'], row['signature_url']) == (name, public), f'{n}: migrated'
            assert entry['changes'] == {'signature_url': ['None', public], 'signature_storage_key': ['None', name]}, f'{n}: entry'
            stored = state['bucket'][f'{LOCATION}/{name}']
            assert base64.b64decode(stored['bytes']) == PNG and stored['type'] == 'image/png' \
                and stored['cache'] == 'max-age=86400' and stored['encoding'] is None, f'{n}: object'
        else:  # the URL longer than the column: the pre_save entry stays, the UPDATE fails
            assert (row['signature_storage_key'], row['signature_url']) == (key, url), f'{n}: unchanged'
            assert entry['changes'] == {'signature_url': ['None', legacy]}, f'{n}: autocommitted entry'
    expected_objects = {SENTINEL[0]} | ({f'{LOCATION}/subscriptions/{sid(n)}/signature_<ts>.png'
                                         for n in ROWS if ROWS[n][5] == 'migrate'} if not dry else set())
    assert set(state['bucket']) == expected_objects, 'bucket objects'


def run_case(fixture, executable, environment, case, output):
    name, arguments, column, (status, stdout) = case
    results = {}
    for runtime in ('django', 'go'):
        fixture.reset(column)
        if runtime == 'go' and executable is None:
            return {'case': name, 'django': 'pass', 'go': 'missing command'}
        command = [sys.executable, '-I', '-c', DJANGO, *arguments] if runtime == 'django' else [str(executable), *arguments]
        since = time.time()
        completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=300,
                                   start_new_session=True, cwd=ROOT / 'BE')
        until = time.time()
        requests = fixture.proxy.take()
        result = {'status': completed.returncode, 'stdout': normalized(completed.stdout, since, until),
                  'requests': normalized(requests, since, until),
                  'state': normalized_state(fixture.snapshot(), since, until)}
        (output / f'{runtime}-stderr.log').open('a').write(f'== {name}\n{completed.stderr}\n')
        if runtime == 'django':
            assert (result['status'], result['stdout']) == (status, stdout), \
                f'{name}: Django differs from the authored case: {result["status"]} {result["stdout"]!r}'
            if status == 0 and column:
                authored_effect(result['state'], arguments)
        results[runtime] = result
    fixture.reset()
    if results['django'] != results['go']:
        (output / 'difference.json').write_text(json.dumps({'case': name, **results}, indent=1, default=str) + '\n')
    assert results['django'] == results['go'], f'{name}: Go differs from the Django command'
    print(f'PASS: {name} (exit {status})', flush=True)
    return {'case': name, 'status': status, 'django': 'pass', 'go': 'pass', 'storage_requests': len(results['go']['requests'])}


def main():
    require_production_python()
    label = sys.argv[1] if len(sys.argv) > 1 else 'run'
    output = ROOT / 'quality-reports/go-migration/subscription-signature-migration' / label
    output.mkdir(parents=True, exist_ok=True)
    for stale in output.glob('*-stderr.log'):
        stale.unlink()
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    base.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-signatures-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False

        def run(args):
            return subprocess.run([str(a) for a in args], env=base, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            executable = work / 'migrate-subscription-signatures-to-s3'
            built = subprocess.run(['go', 'build', '-race', '-o', str(executable), './cmd/migrate-subscription-signatures-to-s3'],
                                   cwd=ROOT / 'BE_GO', env=base, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            if built.returncode or os.environ.get('SIGNATURE_MIGRATION_RED') == '1':
                executable = None  # RED: the Django cases still run against their authored expectations.
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            import boto3
            from botocore.config import Config
            from TESTS.adapters.django.instance_logo import minio
            from TESTS.adapters.django import subscription_sign_http as sign
            with minio(log, dict(base), result) as (endpoint, access, secret):
                client = boto3.client('s3', endpoint_url=endpoint, aws_access_key_id=access, aws_secret_access_key=secret,
                                      region_name='us-east-1', config=Config(signature_version='s3v4', retries={'max_attempts': 0},
                                                                             s3={'addressing_style': 'path'}))
                client.create_bucket(Bucket=sign.BUCKET)
                client.put_object(Bucket=sign.BUCKET, Key=SENTINEL[0], Body=SENTINEL[1], ContentType='text/plain')
                proxy = sign.RecordingProxy(endpoint, DENIED)
                try:
                    environment = dict(os.environ)
                    environment.update(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
                                       DBPASSWORD='private-signature-password-canary', SECRET_KEY='private-signature-secret-canary',
                                       DEBUG='False', REDIS_HOST='127.0.0.1', REDIS_PORT='9', REDIS_PASSWORD='private-redis-canary',
                                       DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'), DJANGO_COLORS='nocolor',
                                       AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret, AWS_STORAGE_BUCKET_NAME=sign.BUCKET,
                                       AWS_S3_REGION_NAME='us-east-1', AWS_S3_ENDPOINT_URL=proxy.endpoint, AWS_LOCATION=LOCATION,
                                       AWS_S3_USE_OBJECT_ACL='True', AWS_S3_PUBLIC_BASE_URL=PUBLIC_BASE, STORE_ON_DISK='False')
                    environment.pop('AWS_DEFAULT_ACL', None)
                    fixture = Fixture(client, sign.BUCKET, proxy)
                    result['cases'] = [run_case(fixture, executable, environment, case, output) for case in CASES]
                finally:
                    proxy.close()
            result['django_passed'] = sum(case['django'] == 'pass' for case in result['cases'])
            result['go_passed'] = sum(case['go'] == 'pass' for case in result['cases'])
            print(json.dumps({'django': f"{result['django_passed']}/{len(CASES)}", 'go': f"{result['go_passed']}/{len(CASES)}"}))
            assert result['go_passed'] == len(CASES), 'Go command missing or failing'
        finally:
            try:
                from django.db import connections
                connections.close_all()
            finally:
                if attempted:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    paths = ['TESTS/adapters/go/subscription_signature_migration_command.py',
             'BE/application/management/commands/migrate_subscription_signatures_to_s3.py',
             'BE/application/models/subscriptions_models.py', 'BE/application/utils/api_utils.py', 'BE/application/exceptions.py',
             'BE_GO/cmd/migrate-subscription-signatures-to-s3/main.go', 'BE_GO/internal/service/subscription_signature_migration.go',
             'BE_GO/internal/repository/subscription_signature_migration.go', 'BE_GO/internal/service/subscription_sign.go']
    result.update(success=True, case_count=len(result['cases']), race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'success': True, 'case_count': result['case_count']}))


if __name__ == '__main__':
    main()
