"""Required real transaction-pooler and CLI drain concurrency regressions.

Both Go runtime participants connect through PgBouncer. The CLI executes its
production drain SQL via the fixture's real psql transport, as in self-hosting.
Direct database access is limited to fixture control and durable readback.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from private_pooler import private_pooler

ROOT = Path(__file__).resolve().parents[3]
EXPECTED = (
    'PASS transaction pool reassigns server:',
    'PASS bounded pooled exclusive drain rejected busy writer before any export read',
    'PASS cancelled COMMIT/cancel transport unavailable:',
    'PASS cancelled first-shared-lock waiter emitted zero business UPDATE commands',
    'PASS actual CLI drain retains exclusive file lease and delays snapshot until remote commit',
)


def main():
    output = ROOT / 'quality-reports/go-migration/restore-drain-pooler'
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    environment = {key: value for key, value in os.environ.items()
                   if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    environment.update(LC_ALL='C', LANG='C')
    with private_pooler(output, 'assozeta_contract_pooler') as fixture:
        test = subprocess.run(
            ['go', 'test', '-race', '-count=1', '-tags=restoredrainpoolerintegration',
             './internal/repository', '-run', '^TestRestoreDrainPooler$', '-json'],
            cwd=ROOT / 'BE_GO', env={**environment,
                'ASSOZETA_POOLER_POOL_DSN': fixture.pooled_dsn,
                'ASSOZETA_POOLER_ADMIN_DSN': fixture.direct_dsn,
                'ASSOZETA_POOLER_CONTAINER': fixture.container_name,
                'ASSOZETA_POOLER_PYTHON': sys.executable,
                'ASSOZETA_POOLER_CLI_SOURCE': str(ROOT / 'selfhost/bin/assozeta')},
            capture_output=True, text=True, timeout=180)
        (output / 'test.jsonl').write_text(test.stdout)
        (output / 'stderr.log').write_text(test.stderr)
        events = [json.loads(line) for line in test.stdout.splitlines()]
        lines = []
        for event in events:
            if event.get('Output'):
                print(event['Output'], end='')
                lines.extend(event['Output'].splitlines())
        test.check_returncode()
        cases = [line for line in lines if line.startswith('PASS ')]
        if len(cases) != len(EXPECTED) or any(
                sum(case.startswith(expected) for case in cases) != 1 for expected in EXPECTED):
            raise AssertionError('Every pooler and CLI scenario must pass exactly once')
        if any(event.get('Action') == 'skip' for event in events) or not any(
                event.get('Action') == 'pass' and event.get('Test') == 'TestRestoreDrainPooler'
                for event in events):
            raise AssertionError('Required pooler integration test did not pass')
        result.update(cases=cases, case_count=len(cases), fixture=fixture.metadata)
    sources = ['BE_GO/internal/repository/transaction.go',
               'BE_GO/internal/repository/restore_drain_pooler_integration_test.go',
               'TESTS/adapters/go/private_pooler.py', 'TESTS/adapters/go/restore_drain_pooler.py',
               'selfhost/bin/assozeta']
    result.update(success=True, sha256={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                       for name in sources})
    report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
