"""Required real PostgreSQL server-fence proof for all five queue mutations.

Uses private operational tables and ordinary deferred triggers. The late-commit
case makes only the separate cancellation transport unavailable: local socket
cleanup can finish while PostgreSQL still holds the shared transaction fence.
No installed application, restore, worker activation or customer data is used.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = {
    f'TestQueueServerFence/{operation}/{scenario}'
    for operation in ('claim', 'renew', 'complete', 'fail', 'cancel_pending')
    for scenario in ('healthy_first_fence', 'cancelled_first_fence_zero_effects',
                     'late_commit_retains_fence')
}


def main():
    output = ROOT / 'quality-reports/go-migration/server-fence'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': __doc__.strip(), 'required_cases': sorted(EXPECTED)}
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
    with tempfile.TemporaryDirectory(prefix='az-server-fence-', dir='/tmp') as directory:
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
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'assozeta_server_fence'])
                test = subprocess.run(
                    ['go', 'test', '-race', '-count=1', '-tags=serverfenceintegration',
                     './internal/repository', '-run', '^TestQueueServerFence$', '-json', '-timeout', '150s'],
                    cwd=ROOT / 'BE_GO', env={**environment, 'ASSOZETA_SERVER_FENCE_DSN':
                        f'host={work} user=contracts dbname=assozeta_server_fence sslmode=disable'},
                    capture_output=True, text=True, timeout=180)
                (output / 'go-test.jsonl').write_text(test.stdout)
                (output / 'stderr.log').write_text(test.stderr)
                events = [json.loads(line) for line in test.stdout.splitlines()]
                for event in events:
                    if event.get('Output'):
                        print(event['Output'], end='')
                test.check_returncode()
                actual = [event['Test'] for event in events if event.get('Action') == 'pass'
                          and event.get('Test', '').count('/') == 2]
                if set(actual) != EXPECTED or len(actual) != len(EXPECTED) or any(event.get('Action') == 'skip' for event in events):
                    raise AssertionError('Missing, duplicate, extra or skipped server-fence groups')
                result.update(cases=actual, case_count=len(actual))
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
    sources = ['BE_GO/internal/repository/transaction.go', 'BE_GO/internal/repository/jobs.go',
               'BE_GO/internal/repository/server_fence_integration_test.go', 'TESTS/adapters/go/server_fence.py']
    result.update(success=True, private_postgres_stopped=True,
                  sha256={source: hashlib.sha256((ROOT / source).read_bytes()).hexdigest() for source in sources})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
