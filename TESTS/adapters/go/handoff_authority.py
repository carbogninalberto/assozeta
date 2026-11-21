"""Phase 1 handoff: runtime authority compare-and-set on a private PostgreSQL cluster.

Runs BE_GO/migrations TestHandoffRuntimeAuthorityPostgres (-race, build tag
jobsintegration) against a fresh database in a throwaway cluster with a Unix
socket only. Every named scenario must pass; a skipped or missing one fails.
No application or customer data. Reports go to the ignored
quality-reports/go-migration/handoff-authority/.

    python3 TESTS/adapters/go/handoff_authority.py [--be-go DIR]

--be-go runs the same test file against a disposable BE_GO copy (mutation
controls); the report then records that directory.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
TEST = 'TestHandoffRuntimeAuthorityPostgres'
SCENARIOS = {
    'upgrade from version 4 keeps ownership and adds operation identity',
    'one commit advances generation and records the operation',
    'retrying the committed operation never commits twice',
    'stale expected state is rejected with the current authority',
    'invalid or no-op transitions are refused without change',
    'a competing commit waits and loses to a committed winner',
    'a competing commit waits and wins after the holder rolls back',
    'concurrent opposite requests produce exactly one winner',
    'round trips keep generations strictly monotonic',
    'authority commit waits for admitted fenced work',
}


def postgres_binaries():
    found = shutil.which('postgres')
    if found:
        return Path(found).parent
    for pattern in ('/opt/homebrew/opt/postgresql*/bin/postgres', '/usr/lib/postgresql/*/bin/postgres',
                    '/usr/local/opt/postgresql*/bin/postgres'):
        candidates = sorted(Path('/').glob(pattern.lstrip('/')))
        if candidates:
            return candidates[-1].parent
    raise RuntimeError('PostgreSQL server binaries are required; this integration is never skipped')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--be-go', type=Path, default=ROOT / 'BE_GO')
    module = parser.parse_args(argv).be_go.resolve()
    output = ROOT / 'quality-reports/go-migration/handoff-authority'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'test': TEST, 'module': str(module), 'scope': 'phase 1 runtime authority; no application data'}
    binaries = postgres_binaries()
    if not shutil.which('go'):
        raise RuntimeError('Go is required; this integration is never skipped')
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'GOFLAGS')}
    env.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-auth-', dir='/tmp') as directory:
        work = Path(directory)
        started = False
        with (output / 'services.log').open('w') as log:
            def run(args):
                return subprocess.run([str(a) for a in args], check=True, timeout=60, stdout=log, stderr=subprocess.STDOUT, env=env)
            try:
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
                started = True
                run([binaries / 'createdb', '-h', work, '-U', 'contracts', 'authority_contracts'])
                proof = subprocess.run(['go', 'test', '-race', '-count=1', '-tags=jobsintegration', './migrations', '-run', f'^{TEST}$', '-json'],
                                       cwd=module, capture_output=True, text=True, timeout=300,
                                       env={**env, 'ASSOZETA_HANDOFF_AUTHORITY_DSN': f'host={work} user=contracts dbname=authority_contracts sslmode=disable'})
                (output / 'go-test.jsonl').write_text(proof.stdout)
                events = []
                for line in proof.stdout.splitlines():
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        print(line)
                for event in events:
                    if event.get('Output'):
                        print(event['Output'], end='')
                if proof.stderr:
                    print(proof.stderr, file=sys.stderr)
                passed = {e.get('Test') for e in events if e.get('Action') == 'pass' and '/' in e.get('Test', '')}
                failed = sorted(e.get('Test') for e in events if e.get('Action') == 'fail' and '/' in e.get('Test', ''))
                required = {f'{TEST}/' + name.replace(' ', '_') for name in SCENARIOS}
                skipped = [e.get('Test') for e in events if e.get('Action') == 'skip']
                result.update(passed=len(passed & required), required=len(required), failed=failed,
                              missing=sorted(required - passed - set(failed)), skipped=skipped)
                result['success'] = proof.returncode == 0 and passed >= required and not skipped
            finally:
                if started:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                if (work / 'postgres.log').exists():
                    shutil.copyfile(work / 'postgres.log', output / 'postgres.log')
                (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))
    return 0 if result['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
