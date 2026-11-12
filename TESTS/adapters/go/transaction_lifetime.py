"""Private PostgreSQL proof of transaction cleanup before admitted work returns.

The business tables are explicit synthetic sentinels, not a Django business
parity fixture. The real repository, service, middleware, executor and pgx
driver participate; only cancellation/cleanup timing is controlled by tests.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = {
    'worker daily reset retains admission through rollback and cleanup',
    'HTTP table mutation retains admission through rollback and cleanup',
    'read only snapshot joins rollback and cleanup before returning',
}
OWNED_EXPECTED = {
    'healthy commit and rollback reuse owned connection',
    'concurrent repeated completion joins one driver operation',
    'cancelled begin leaves pool and server usable',
    'automatic rollback joins driver and socket cleanup before lease release',
    'failed SQL discards connection before return',
}


def main():
    output = ROOT / 'quality-reports/go-migration/transaction-lifetime'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': __doc__.strip()}
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
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    with tempfile.TemporaryDirectory(prefix='az-tx-lifetime-', dir='/tmp') as directory:
        work = Path(directory)
        attempted = False
        with (output / 'services.log').open('w') as log:
            def run(arguments):
                subprocess.run([str(argument) for argument in arguments], env=environment,
                               stdout=log, stderr=subprocess.STDOUT, check=True, timeout=90)
            try:
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts',
                     '--no-sync', '--no-locale', '-E', 'UTF8'])
                attempted = True
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                     '-o', f"-k {work} -h '' -F", '-w', 'start'])
                cases = []
                for name, database, variable, tag, function, names in [
                    ('representative', 'assozeta_contract_tx_lifetime', 'ASSOZETA_TRANSACTION_LIFETIME_DSN',
                     'transactionlifetimeintegration', 'TestTransactionLifetimePostgres', EXPECTED),
                    ('owned', 'assozeta_contract_owned_tx', 'ASSOZETA_OWNED_TRANSACTION_DSN',
                     'ownedtransactionintegration', 'TestOwnedTransactionPostgres', OWNED_EXPECTED),
                ]:
                    run([binaries / 'createdb', '-h', work, '-U', 'contracts', database])
                    test = subprocess.run(
                        ['go', 'test', '-race', '-count=1', '-tags=' + tag,
                         './internal/repository', '-run', '^' + function + '$', '-json'],
                        cwd=ROOT / 'BE_GO', env={**environment, variable:
                            f'host={work} user=contracts dbname={database} sslmode=disable'},
                        capture_output=True, text=True, timeout=180)
                    (output / (name + '-test.jsonl')).write_text(test.stdout)
                    (output / (name + '-stderr.log')).write_text(test.stderr)
                    events = [json.loads(line) for line in test.stdout.splitlines()]
                    for event in events:
                        if event.get('Output'):
                            print(event['Output'], end='')
                    test.check_returncode()
                    actual = [event['Test'] for event in events if event.get('Action') == 'pass'
                              and event.get('Test', '').startswith(function + '/')]
                    expected = {function + '/' + name.replace(' ', '_') for name in names}
                    if set(actual) != expected or len(actual) != len(expected) or any(event.get('Action') == 'skip' for event in events):
                        raise AssertionError('Missing, duplicate, extra or skipped lifetime groups')
                    cases.extend(actual)
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
