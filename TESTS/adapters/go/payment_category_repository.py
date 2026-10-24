"""Payment-category repositories/services on private current Django PostgreSQL schema.

This is database integration evidence, separate from HTTP, browser, migration
history and backend-switch acceptance. No caller database configuration is used.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_payment_category_repository'
EXPECTED = {
    'TestPaymentCategoryRepositoryListLive',
    'TestPaymentCategoryMutationsAtomicLive',
    'TestPaymentCategoryConcurrentPartialUpdatesLive',
    'TestPaymentCategoryDeletePreservesReferencesLive',
    'TestPaymentCategoryListQueriesBoundedLive',
    'TestPaymentCategoryBlockedCancellationRecoveryLive',
}


def main():
    require_production_python()
    output = ROOT / 'quality-reports/go-migration/payment-category-repository'
    output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'schema': 'current Django models, not migration history',
              'complete_payment_category_parity': False, 'private_postgres_stopped': False}
    (output / 'results.json').write_text(json.dumps(report) + '\n')
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres or not shutil.which('go'):
        raise RuntimeError('PostgreSQL and Go required; payment-category integration is not skipped')
    binaries = Path(postgres).parent
    environment = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-category-db-', dir='/tmp') as directory, \
            (output / 'services.log').open('w') as services:
        work = Path(directory)
        started = False

        def run(arguments):
            return subprocess.run([str(arg) for arg in arguments], env=environment,
                                  check=True, timeout=120, stdout=services,
                                  stderr=subprocess.STDOUT)

        try:
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts',
                 '--no-sync', '--no-locale', '-E', 'UTF8'])
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                 '-o', f"-k {work} -h '' -F", '-w', 'start'])
            started = True
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, services)
            with (output / 'go-tests.jsonl').open('w') as log:
                completed = subprocess.run(
                    ['go', 'test', '-race', '-json', '-count=1', '-tags=paymentcategoryintegration',
                     './internal/service', '-run', '^TestPaymentCategory.*Live$'],
                    cwd=ROOT / 'BE_GO', timeout=240,
                    env={**environment, 'ASSOZETA_PAYMENT_CATEGORY_DSN':
                         f'host={work} port=5432 user=contracts dbname={DATABASE} sslmode=disable'},
                    stdout=log, stderr=services,
                )
            events = [json.loads(line) for line in (output / 'go-tests.jsonl').read_text().splitlines()]
            passed = {event['Test'] for event in events
                      if event.get('Action') == 'pass' and 'Test' in event and '/' not in event['Test']}
            skipped = [event.get('Test', event.get('Package')) for event in events
                       if event.get('Action') == 'skip']
            report.update(exit_code=completed.returncode, passed=sorted(passed), skipped=skipped)
            if completed.returncode or passed != EXPECTED or skipped:
                raise AssertionError(f'Incomplete payment-category database proof: {report}')
            report['tests_passed'] = True
        finally:
            if 'django.db' in sys.modules:
                from django.db import connections
                connections.close_all()
            try:
                if started:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                    report['private_postgres_stopped'] = True
            finally:
                if (work / 'postgres.log').exists():
                    shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                report['success'] = bool(report.get('tests_passed') and report['private_postgres_stopped'])
                (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
