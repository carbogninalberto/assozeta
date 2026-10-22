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
                execution = subprocess.run(['go', 'test', '-race', '-tags=jobsintegration', './internal/service', '-run', '^TestJobExecutorPostgres$', '-json'],
                               cwd=ROOT / 'BE_GO', env={**env, 'ASSOZETA_JOB_TEST_DSN': f'host={work} user=contracts dbname=job_contracts sslmode=disable'},
                               capture_output=True, text=True, timeout=120)
                (output / 'executor-test.jsonl').write_text(execution.stdout)
                execution_events = [json.loads(line) for line in execution.stdout.splitlines()]
                for event in execution_events:
                    if event.get('Output'):
                        print(event['Output'], end='')
                if execution.stderr:
                    print(execution.stderr)
                execution.check_returncode()
                execution_passed = {event.get('Test') for event in execution_events if event['Action'] == 'pass' and '/' in event.get('Test', '')}
                expected_execution = {
                    'service owns atomic effects and completion',
                    'declared retries retain publication time',
                    'version panic timeout and missing completion fail explicitly',
                    'heartbeat retains a long running job',
                    'lease loss cancels the handler without stale writes',
                    'shutdown preserves an admitted lease failure',
                    'blocked heartbeat fails closed and database pool recovers',
                    'disabled restore coordination and database failure preserve admission',
                    'maintenance defers before claim without spending attempts',
                    'completed restore rejects stale jobs before effects',
                    'shutdown stops intake and drains admitted work',
                    'drain cancellation keeps restore lock until handler exits',
                    'worker concurrency is bounded and cancellation joins slots',
                }
                expected_execution = {'TestJobExecutorPostgres/' + name.replace(' ', '_') for name in expected_execution}
                assert execution_passed == expected_execution and not any(event['Action'] == 'skip' for event in execution_events), 'missing or skipped executor scenario'
                result['executor_scenarios'] = len(execution_passed)
                # Separate databases preserve each suite's real initial state:
                # ownership starts with Django, and migration starts at version1.
                ownership_suites = (
                    ('ownership', './internal/service', 'TestJobOwnershipPostgres', 'ASSOZETA_JOB_OWNERSHIP_DSN', {
                        'default Django ownership never claims or spends attempts',
                        'lost ownership rejects effects renewal failure and completion',
                        'returning Go ownership cannot revive prior generation',
                        'handoff before completion rolls back business transaction',
                        'successful completion holds ownership through commit and rollback',
                        'claim waits for concurrent handoff and rechecks owner',
                        'daily reset locks ownership before touching business rows',
                        'executor heartbeat cancels lost owner and stops intake',
                        'competing trusted fixture handoffs use generation compare and swap',
                        'ownership waits never lock job rows before the owner',
                        'missing owner row denies claims without losing queued work',
                    }),
                    ('ownership_migration', './migrations', 'TestOwnershipMigrationPostgres', 'ASSOZETA_JOB_MIGRATION_DSN', {
                        'additive upgrade preserves pending leased jobs and Django history',
                        'reapplying does not reset ownership or generation',
                        'down refusal retains ownership and permanent job keys',
                        'dirty history fails closed without force or data changes',
                    }),
                )
                for name, package, test, dsn_key, scenarios in ownership_suites:
                    database = name + '_contracts'
                    run([binaries / 'createdb', '-h', work, '-U', 'contracts', database])
                    proof = subprocess.run(['go', 'test', '-race', '-tags=jobsintegration', package, '-run', '^' + test + '$', '-json'],
                        cwd=ROOT / 'BE_GO', env={**env, dsn_key: f'host={work} user=contracts dbname={database} sslmode=disable'},
                        capture_output=True, text=True, timeout=120)
                    (output / (name + '-test.jsonl')).write_text(proof.stdout)
                    events = [json.loads(line) for line in proof.stdout.splitlines()]
                    for event in events:
                        if event.get('Output'):
                            print(event['Output'], end='')
                    if proof.stderr:
                        print(proof.stderr)
                    proof.check_returncode()
                    passed_scenarios = {event.get('Test') for event in events if event['Action'] == 'pass' and '/' in event.get('Test', '')}
                    required = {test + '/' + scenario.replace(' ', '_') for scenario in scenarios}
                    assert passed_scenarios == required and not any(event['Action'] == 'skip' for event in events), f'missing or skipped {name} scenario'
                    result[name + '_scenarios'] = len(passed_scenarios)
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
