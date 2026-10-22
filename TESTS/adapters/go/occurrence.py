"""Required exact-row publication proof on private PostgreSQL and pinned Beat.

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
    'default Django ownership leaves source receipt and jobs unchanged',
    'publication commits exact intent cursor and retained receipt without marker mutation',
    'concurrent publishers commit one occurrence',
    'unsupported options are retained and never published',
    'UTC daily definition is mapped from the locked actual Django table',
    'source editor and uncommitted delete produce contention without partial writes',
    'definition-first editor can acquire parent after publisher backs off',
    'publication retains owner source and definition locks through transaction outcome',
    'owner lock is acquired before source and rechecks transition',
    'every SQL fault rolls back job receipt and source including commit failure',
    'receipts retain ordinal across count reset owner roundtrip and source ID reuse',
    'maintenance and missing mandatory file lease cannot mutate source',
    'bounded operational crashes become terminal without another receipt',
    'real shared lifecycle lease spans commit and cancelled rollback',
    'handled business UPDATE failure completes without business retry',
    'cancelled publication joins actual driver rollback and socket cleanup before releasing lifecycle lease',
    'committed source deletion is absent and migration history remains unchanged',
}


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/occurrence'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': 'exact-row transactions on actual Beat schema; no scheduler activation or producer handoff'}
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
    with tempfile.TemporaryDirectory(prefix='az-occurrence-', dir='/tmp') as directory:
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
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'assozeta_contract_occurrence'])
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
                                  DBNAME='assozeta_contract_occurrence', DBUSER='contracts',
                                  DBPASSWORD='fixture', USE_PGBOUNCER='False', STORE_ON_DISK='True')
                import django
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log), patch('environ.Env.read_env'):
                    django.setup()
                    from django.core.management import call_command
                    call_command('migrate', 'django_celery_beat', interactive=False, verbosity=0)
                    from django_celery_beat.models import IntervalSchedule, PeriodicTask
                    PeriodicTask.objects.create(name='preexisting-before-go-migration', task='historical.task',
                                                interval=IntervalSchedule.objects.create(every=120, period='seconds'),
                                                total_run_count=9)
                from django.db import connections
                connections.close_all()
                test = subprocess.run(
                    ['go', 'test', '-race', '-count=1', '-tags=occurrenceintegration', './internal/repository',
                     '-run', '^TestPeriodicOccurrencesPostgres$', '-json'], cwd=ROOT / 'BE_GO',
                    env={**environment, 'ASSOZETA_OCCURRENCE_TEST_DSN':
                         f'host={work} user=contracts dbname=assozeta_contract_occurrence sslmode=disable'},
                    capture_output=True, text=True, timeout=180)
                (output / 'postgres-test.jsonl').write_text(test.stdout)
                (output / 'postgres-stderr.log').write_text(test.stderr)
                events = [json.loads(line) for line in test.stdout.splitlines()]
                for event in events:
                    if event.get('Output'):
                        print(event['Output'], end='')
                test.check_returncode()
                cases = [event['Test'] for event in events if event.get('Action') == 'pass'
                         and event.get('Test', '').startswith('TestPeriodicOccurrencesPostgres/')]
                expected = {'TestPeriodicOccurrencesPostgres/' + name.replace(' ', '_') for name in EXPECTED}
                if set(cases) != expected or len(cases) != len(expected) or any(event.get('Action') == 'skip' for event in events):
                    raise AssertionError('Missing, duplicate, extra or skipped occurrence groups')
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
