"""Required bounded candidate rotation proof on private PostgreSQL and pinned Beat.

Only Beat's real schema is migrated. Restore and business tables are explicit
synthetic sentinels; no installed scheduler, customer data or producer handoff is
involved. The required case set must pass without skips and the private server
must stop before success is reported.
"""
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from TESTS.adapters.django.production_runtime import require_production_python
EXPECTED = {
    'invalid seek bounds fail before database access',
    'mixed sparse datasets use one bounded primary key seek with measured buffers',
    'full rotation visits every sparse source despite disabled and unrelated prefixes',
    'higher ID arrivals cannot prevent a finite rotation and low ID revisit',
    'unsupported busy and deleted sources cannot starve a later valid page',
    'markerless edits are reloaded after rotation and after candidate selection',
    'publication has a fixed per attempt query budget without history scan',
    'Django ownership and restore admission stop the page without durable effects',
    'SQL failure and cancelled source roll back and stop later publication',
}


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/schedule-page'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': 'bounded inert page rotation on actual Beat schema; no scheduler activation or producer handoff'}
    report = output / 'results.json'
    report.write_text(json.dumps(result) + '\n')
    environment = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    postgres = shutil.which('postgres')
    if not postgres:
        candidate = Path('/opt/homebrew/opt/postgresql@16/bin/postgres')
        postgres = str(candidate) if candidate.exists() else None
    if not postgres or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required; this proof cannot be skipped')
    binaries = Path(postgres).parent
    with tempfile.TemporaryDirectory(prefix='az-schedule-page-', dir='/tmp') as directory:
        work = Path(directory)
        attempted = False
        with (output / 'services.log').open('w') as log:
            def run(arguments):
                return subprocess.run([str(argument) for argument in arguments], env=environment,
                                      stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
            try:
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts',
                     '--no-sync', '--no-locale', '-E', 'UTF8'])
                attempted = True
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                     '-o', f"-k {work} -h '' -F", '-w', 'start'])
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'assozeta_contract_schedule_page'])
                # The fresh process deliberately discards inherited application
                # configuration. Only the tracked example and private DB enter
                # Django settings; the repository .env is never loaded.
                sys.path.insert(0, str(ROOT / 'BE'))
                import environ
                os.environ.clear()
                os.environ.update(environment)
                environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
                os.environ.update(DJANGO_SETTINGS_MODULE='core.settings', DEBUG='False',
                                  JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                                  JWT_SECRET_KEY='fixture', JWT_PUBLIC_KEY='fixture',
                                  DBTYPE='POSTGRES', DBHOST=str(work), DBPORT='5432',
                                  DBNAME='assozeta_contract_schedule_page', DBUSER='contracts',
                                  DBPASSWORD='fixture', USE_PGBOUNCER='False', STORE_ON_DISK='True')
                import django
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), patch('environ.Env.read_env'):
                    django.setup()
                    from django.core.management import call_command
                    call_command('migrate', 'django_celery_beat', interactive=False, verbosity=0)
                from django.db import connections
                connections.close_all()
                test = subprocess.run(
                    ['go', 'test', '-race', '-count=1', '-tags=schedulepageintegration', './internal/repository',
                     '-run', '^TestSchedulePagesPostgres$', '-json'], cwd=ROOT / 'BE_GO',
                    env={**environment, 'ASSOZETA_SCHEDULE_PAGE_PLAN_OUTPUT': str(output / 'query-plans.json'), 'ASSOZETA_SCHEDULE_PAGE_DSN':
                         f'host={work} user=contracts dbname=assozeta_contract_schedule_page sslmode=disable'},
                    capture_output=True, text=True, timeout=180)
                (output / 'postgres-test.jsonl').write_text(test.stdout)
                (output / 'postgres-stderr.log').write_text(test.stderr)
                events = [json.loads(line) for line in test.stdout.splitlines()]
                for event in events:
                    if event.get('Output'):
                        print(event['Output'], end='')
                test.check_returncode()
                cases = [event['Test'] for event in events if event.get('Action') == 'pass'
                         and event.get('Test', '').startswith('TestSchedulePagesPostgres/')]
                expected = {'TestSchedulePagesPostgres/' + name.replace(' ', '_') for name in EXPECTED}
                if set(cases) != expected or len(cases) != len(expected) or any(event.get('Action') == 'skip' for event in events):
                    raise AssertionError('Missing, duplicate, extra or skipped schedule page groups')
                result.update(cases=cases, case_count=len(cases))
            finally:
                try:
                    if (work / 'postgres.log').exists():
                        shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                finally:
                    if attempted:
                        try:
                            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                        except subprocess.CalledProcessError:
                            if (work / 'postgres/postmaster.pid').exists():
                                raise
    result.update(success=True, private_postgres_stopped=True)
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
