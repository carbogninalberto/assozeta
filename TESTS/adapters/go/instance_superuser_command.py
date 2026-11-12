"""create_instance_superuser: actual Django management command versus the Go command.

Private PostgreSQL with the current Django schema. Every authored case resets
the same seed, runs ``manage.py create_instance_superuser`` as a separate
process (production PBKDF2 hashers), records exit status, stdout, stderr and
the complete managed-model state, resets, runs the Go binary with the same
arguments and environment, and requires the identical result. Generated ids,
timestamps and password salts are normalized after checking them (the stored
hash must verify the chosen password with Django's check_password and use
Django's preferred PBKDF2 parameters). Interactive cases pipe stdin without a
controlling terminal, so both runtimes use getpass's stdin fallback; their
warning text on stderr is not compared. Django's process startup warnings
are not part of the command; only its CommandError line is compared.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_instance_superuser'
PASSWORD = 'A-long-Test-password-491!'
SEEDED = [UUID(int=91001 + i) for i in range(3)]
DJANGO = f'''
import sys
sys.path[:0] = [{str(ROOT / 'BE')!r}, {str(ROOT)!r}]
from unittest.mock import patch
with patch('environ.Env.read_env'):
    import django
    django.setup()
from django.conf.global_settings import PASSWORD_HASHERS
from django.test import override_settings
from django.core.management import execute_from_command_line
with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS):
    execute_from_command_line(['manage.py', 'create_instance_superuser', *sys.argv[1:]])
'''

LONG = 'a' * 151
TOO_LONG_EMAIL = 'a' * 64 + '@' + ('b' * 60 + '.') * 4 + 'test'
TAKEN = 'An account with this {} already exists; no account was changed.'
USERNAME_CHARACTERS = ("Immetti un nome utente valido. Questo campo può contenere solo lettere dell'alfabeto inglese, "
                       "numeri e caratteri @/./+/-/_ .")


def too_long(limit, length):
    return f'Assicurati che questo valore non contenga più di {limit} caratteri (ne ha {length}).'


# (name, arguments, environment overrides, stdin or None, compare stderr, authored (exit, created, message)).
# Expectations are read from create_instance_superuser.py, Django's validators and the Italian catalog.
CASES = [
    ('full profile', ['--username', 'admin-new', '--email', 'Admin.New@EXAMPLE.Test', '--first-name', ' Ada ',
                      '--last-name', 'Lovelace', '--phone', '+390000000000', '--noinput'], {}, None, True, (0, 1, None)),
    ('minimal profile, full case mapping', ['--username=straße', '--email=s@example.test', '--noinput'], {}, None, True,
     (0, 1, None)),
    ('username required', ['--email', 'x@example.test', '--noinput'], {}, None, True,
     (1, 0, 'Username and email are required.')),
    ('whitespace username', ['--username', ' \u3000 ', '--email', 'x@example.test', '--noinput'], {}, None, True,
     (1, 0, 'Username and email are required.')),
    ('invalid email', ['--username', 'someone', '--email', 'not-an-email', '--noinput'], {}, None, True,
     (1, 0, 'Inserisci un indirizzo email valido.')),
    ('existing username, any case', ['--username', 'admin-taken', '--email', 'x@example.test', '--noinput'], {}, None, True,
     (1, 0, TAKEN.format('username'))),
    ('existing email, any case', ['--username', 'someone', '--email', 'TAKEN@example.test', '--noinput'], {}, None, True,
     (1, 0, TAKEN.format('email'))),
    ('soft-deleted account username', ['--username', 'deleted-admin', '--email', 'x@example.test', '--noinput'], {}, None,
     True, (1, 0, TAKEN.format('username'))),
    ('username characters', ['--username', 'bad name!', '--email', 'x@example.test', '--noinput'], {}, None, True,
     (1, 0, USERNAME_CHARACTERS)),
    ('username length', ['--username', LONG, '--email', 'x@example.test', '--noinput'], {}, None, True,
     (1, 0, too_long(150, 151))),
    ('username characters and length', ['--username', 'b d' + LONG, '--email', 'x@example.test', '--noinput'], {}, None,
     True, (1, 0, USERNAME_CHARACTERS + '; ' + too_long(150, 154))),
    ('first name length', ['--username', 'someone', '--email', 'x@example.test', '--first-name', LONG, '--noinput'], {},
     None, True, (1, 0, too_long(150, 151))),
    ('phone length', ['--username', 'someone', '--email', 'x@example.test', '--phone', '1' * 21, '--noinput'], {}, None,
     True, (1, 0, too_long(20, 21))),
    ('email length', ['--username', 'someone', '--email', TOO_LONG_EMAIL, '--noinput'], {}, None, True,
     (1, 0, too_long(254, len(TOO_LONG_EMAIL)))),
    ('empty password', ['--username', 'someone', '--email', 'x@example.test', '--noinput'],
     {'DJANGO_SUPERUSER_PASSWORD': ''}, None, True, (1, 0, 'A nonempty password is required.')),
    ('weak password', ['--username', 'someone', '--email', 'x@example.test', '--noinput'],
     {'DJANGO_SUPERUSER_PASSWORD': '1234'}, None, True,
     (1, 0, 'This password is too short. It must contain at least 8 characters.; Questa password è troppo comune.; '
            'Questa password è interamente numerica.')),
    ('password similar to the username', ['--username', 'administrator', '--email', 'x@example.test', '--noinput'],
     {'DJANGO_SUPERUSER_PASSWORD': 'Administrator1'}, None, True, (1, 0, 'La password è troppo simile a nome utente.')),
    ('weak password bypass outside development', ['--username', 'someone', '--email', 'x@example.test', '--noinput',
     '--allow-weak-password'], {'ASSOZETA_DEPLOYMENT_MODE': 'production'}, None, True,
     (1, 0, 'Weak test passwords are allowed only in development.')),
    ('weak password bypass in development', ['--username', 'dev-admin', '--email', 'dev@example.test', '--noinput',
     '--allow-weak-password'], {'ASSOZETA_DEPLOYMENT_MODE': 'development', 'DJANGO_SUPERUSER_PASSWORD': '1234'}, None, True,
     (0, 1, None)),
    ('username equal to an existing one after NFKC', ['--username', '\uff46\uff55\uff4c\uff4c\uff57\uff49\uff44\uff54\uff48',
     '--email', 'f@example.test', '--noinput'], {}, None, True,
     (1, 0, 'An account with these details already exists; no account was changed.')),
    ('interactive profile and confirmed password', [], {}, f'interactive\ni@example.test\nIda\n\n+39 1\n{PASSWORD}\n{PASSWORD}\n',
     False, (0, 1, None)),
    ('interactive options skip their prompts', ['--username', 'flagged', '--phone', '5'], {},
     f'flag@example.test\n\n\n{PASSWORD}\n{PASSWORD}\n', False, (0, 1, None)),
    ('interactive passwords differ', [], {}, f'other\no@example.test\n\n\n\n{PASSWORD}\ndifferent\n', False,
     (1, 0, 'Passwords do not match.')),
    ('interactive input ends', [], {}, 'cut\n', False, (1, 0, 'Account creation cancelled.')),
]


class Fixture:
    def __init__(self):
        from application.models import User
        User.original_objects.bulk_create([
            User(user_id=SEEDED[0], username='ADMIN-TAKEN', email='taken@example.test', password='!private-fixture', role=1),
            User(user_id=SEEDED[1], username='Deleted-Admin', email='ghost@example.test', password='!private-fixture',
                 role=2, deleted=True),
            User(user_id=SEEDED[2], username='FULLWIDTH', email='full@example.test', password='!private-fixture', role=2),
        ])
        self.seed = self.snapshot()

    def reset(self):
        from application.models import User
        from auditlog.context import disable_auditlog
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        with disable_auditlog():
            AuditLogIndex.objects.all().delete()
            LogEntry.objects.all().delete()
            User.original_objects.exclude(pk__in=SEEDED).delete()
        assert self.snapshot() == self.seed, 'fixture reset'

    @staticmethod
    def snapshot():
        from django.apps import apps
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}

    @staticmethod
    def normalized(state, since, until, password):
        """Check then replace generated values: new user id, timestamps and the salted hash."""
        from django.contrib.auth.hashers import check_password
        state = json.loads(json.dumps(state, default=str))
        created = {}
        for row in state['application.User']:
            if UUID(row['user_id']) in SEEDED:
                continue
            assert row['password'].startswith('pbkdf2_sha256$1000000$'), 'preferred Django hasher'
            assert check_password(password, row['password']), 'stored hash verifies the chosen password'
            assert since <= datetime.fromisoformat(row['date_joined']) <= until, 'date_joined inside the run'
            created[row['user_id']] = 'new-user'
            row.update(user_id='new-user', password='verified', date_joined='run')
        entries = state['auditlog.LogEntry']
        for entry in entries:
            assert since <= datetime.fromisoformat(entry['timestamp']) <= until, 'audit timestamp inside the run'
            entry.update(id='entry', timestamp='run', object_pk=created.get(entry['object_pk'], entry['object_pk']))
            changes = entry['changes'] if isinstance(entry['changes'], dict) else json.loads(entry['changes'])
            for key in ('user_id', 'date_joined'):
                if key in changes:
                    changes[key] = [changes[key][0], 'generated']
            entry['changes'] = changes
        assert not state['application.AuditLogIndex'], 'no index row for an administrator without association or actor'
        return state


def run_case(fixture, executable, environment, case, output):
    """Django must meet the authored expectation; Go must then equal Django."""
    name, arguments, overrides, stdin, compare_stderr, (status, created, message) = case
    results = {}
    for runtime in ('django', 'go'):
        fixture.reset()
        if runtime == 'go' and executable is None:
            return {'case': name, 'django': 'pass', 'go': 'missing command'}
        env = dict(environment, DJANGO_SUPERUSER_PASSWORD=PASSWORD, ASSOZETA_DEPLOYMENT_MODE='production')
        env.update(overrides)
        command = [sys.executable, '-I', '-c', DJANGO, *arguments] if runtime == 'django' else [str(executable), *arguments]
        since = datetime.now(timezone.utc)
        completed = subprocess.run(command, env=env, input=stdin if stdin is not None else '', capture_output=True,
                                   text=True, timeout=120, start_new_session=True, cwd=ROOT / 'BE')
        until = datetime.now(timezone.utc)
        password = env['DJANGO_SUPERUSER_PASSWORD'] if '--noinput' in arguments else PASSWORD
        # Django process startup (Python warnings, system checks) also writes
        # to stderr; the command's own output is its CommandError line.
        errors = [line for line in completed.stderr.splitlines() if line.startswith('CommandError')]
        if runtime == 'go' and compare_stderr:
            assert completed.stderr.splitlines() == errors, f'{name}: unexpected Go stderr'
        results[runtime] = {'status': completed.returncode, 'stdout': completed.stdout,
                            'stderr': errors if compare_stderr else None,
                            'state': fixture.normalized(fixture.snapshot(), since, until, password)}
        made = sum(row['user_id'] == 'new-user' for row in results[runtime]['state']['application.User'])
        authored = (completed.returncode, made, errors[-1][len('CommandError: '):] if errors else None)
        if runtime == 'django':
            assert authored == (status, created, message), f'{name}: Django differs from the authored case: {authored}'
    if results['django'] != results['go']:
        (output / 'difference.json').write_text(json.dumps({'case': name, **results}, indent=1, default=str) + '\n')
    assert results['django'] == results['go'], f'{name}: Go differs from the Django command'
    print(f'PASS: {name} (exit {status}, created {created})', flush=True)
    return {'case': name, 'status': status, 'created': created, 'message': message, 'django': 'pass', 'go': 'pass'}


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/instance-superuser-command'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    base.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-superuser-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False

        def run(args):
            return subprocess.run([str(a) for a in args], env=base, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            executable = work / 'create-instance-superuser'
            built = subprocess.run(['go', 'build', '-race', '-o', str(executable), './cmd/create-instance-superuser'],
                                   cwd=ROOT / 'BE_GO', env=base, stdout=log, stderr=subprocess.STDOUT, timeout=300)
            if built.returncode:
                executable = None  # RED: the Django cases still run against their authored expectations.
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            # The Django process inherits the contract settings environment;
            # the Go command reads the same database through the deployment variables.
            environment = dict(os.environ)
            environment.update(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
                               DBPASSWORD='private-superuser-password-canary', SECRET_KEY='private-superuser-secret-canary',
                               DEBUG='False', REDIS_HOST='127.0.0.1', REDIS_PORT='9', REDIS_PASSWORD='private-redis-canary',
                               DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'), DJANGO_COLORS='nocolor')
            fixture = Fixture()
            result['cases'] = [run_case(fixture, executable, environment, case, output) for case in CASES]
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
                report.write_text(json.dumps(result, indent=2) + '\n')
    paths = ['TESTS/adapters/go/instance_superuser_command.py', 'BE/application/management/commands/create_instance_superuser.py',
             'BE_GO/cmd/create-instance-superuser/main.go', 'BE_GO/internal/service/instance_superuser.go',
             'BE_GO/internal/repository/instance_superuser.go', 'BE_GO/internal/djangoauth/password.go',
             'BE_GO/internal/djangoauth/user.go', 'BE_GO/internal/djangoauth/text.go']
    result.update(success=True, case_count=len(result['cases']), race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'success': True, 'case_count': result['case_count']}))


if __name__ == '__main__':
    main()
