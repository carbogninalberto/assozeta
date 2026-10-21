"""Private PostgreSQL durable queue rehearsal; no application or customer data."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]


def main():
    output = ROOT / 'quality-reports/go-migration/jobs'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'scope': 'operational queue; not task parity'}
    (output / 'results.json').write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres or not shutil.which('go'):
        raise RuntimeError('PostgreSQL and Go required; integration is not skipped')
    binaries = Path(postgres).parent
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    env.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-jobs-', dir='/tmp') as directory:
        work = Path(directory)
        started = False
        with (output / 'services.log').open('w') as log:
            def run(args):
                return subprocess.run([str(a) for a in args], check=True, timeout=60,
                                      stdout=log, stderr=subprocess.STDOUT, env=env)
            try:
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
                started = True
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'job_contracts'])
                completed = subprocess.run(['go', 'test', '-race', '-tags=jobsintegration', './internal/repository', '-run', '^TestDurableJobsPostgres$', '-json'],
                               cwd=ROOT / 'BE_GO', env={**env, 'ASSOZETA_JOB_PLAN_OUTPUT': str(output / 'query-plan.json'), 'ASSOZETA_JOB_TEST_DSN': f'host={work} user=contracts dbname=job_contracts sslmode=disable'},
                               capture_output=True, text=True, timeout=180)
                (output / 'go-test.jsonl').write_text(completed.stdout)
                events = [json.loads(line) for line in completed.stdout.splitlines()]
                for event in events:
                    if event.get('Output'):
                        print(event['Output'], end='')
                if completed.stderr:
                    print(completed.stderr)
                completed.check_returncode()
                expected = {
                    'transactional intent and permanent scoped idempotency',
                    'business effect and completion share the caller transaction',
                    'mixed kinds and states preserve bounded due selection',
                    'concurrent claims skip locked rows and never duplicate live leases',
                    'lease token expiry renewal recovery and stale completion',
                    'bounded retry delayed eligibility and exhausted crash recovery',
                    'kind isolation future work cancellation and validation',
                    'killed claiming process leaves recoverable durable intent',
                    'bounded queries and additive migration ownership',
                    'duplicate publishers preserve one durable job',
                }
                expected = {'TestDurableJobsPostgres/' + name.replace(' ', '_') for name in expected}
                passed = {event.get('Test') for event in events if event['Action'] == 'pass' and '/' in event.get('Test', '')}
                assert passed == expected and not any(event['Action'] == 'skip' for event in events), 'missing or skipped queue scenario'
                result.update(success=True, private_postgres=True, scenario_groups=len(passed),
                              query_sizes=[1, 16, 128, 512], plan_dataset=4096,
                              business_schema='synthetic transaction/ownership sentinels only')
            finally:
                if started:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                if (work / 'postgres.log').exists():
                    shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
