"""Differential proof for the inert persisted daily-reset timing subset.

Runs the real private Django scheduler oracle, validates its portable report,
then executes the Go evaluator against both authored and observed outcomes.
No scheduler process, producer, ownership selector or migration is activated.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / 'quality-reports/go-migration/schedule-due'


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    result_path = OUTPUT / 'results.json'
    result_path.write_text(json.dumps({'success': False, 'phase': 'started'}) + '\n')
    with (OUTPUT / 'django-oracle.log').open('w') as log:
        subprocess.run([sys.executable, '-m', 'TESTS.adapters.django.scheduler_authority'],
                       cwd=ROOT, check=True, timeout=180, stdout=log, stderr=subprocess.STDOUT)
    oracle_path = ROOT / 'quality-reports/go-migration/scheduler-authority/results.json'
    environment = dict(os.environ, TESTS_SCHEDULER_AUTHORITY_REPORT=str(oracle_path))
    with (OUTPUT / 'portable-contract.log').open('w') as log:
        subprocess.run([sys.executable, '-m', 'unittest', 'TESTS.contracts.test_scheduler_authority', '-v'],
                       cwd=ROOT, env=environment, check=True, timeout=30, stdout=log, stderr=subprocess.STDOUT)
    with (OUTPUT / 'go-tests.jsonl').open('w') as log:
        subprocess.run(['go', 'test', '-json', '-race', './internal/service', '-run', '^TestScheduleDue', '-count=1'],
                       cwd=ROOT / 'BE_GO', env=environment, check=True, timeout=180,
                       stdout=log, stderr=subprocess.STDOUT)
    events = [json.loads(line) for line in (OUTPUT / 'go-tests.jsonl').read_text().splitlines()]
    if any(event.get('Action') == 'skip' for event in events):
        raise AssertionError('The differential proof must not skip any requested cases')
    cases = [event['Test'].split('/', 1)[1] for event in events
             if event.get('Action') == 'pass' and event.get('Test', '').startswith('TestScheduleDuePortableContract/')]
    if not cases:
        raise AssertionError('No actual Go differential observations ran')
    oracle = json.loads(oracle_path.read_text())
    sources = ['TESTS/fixtures/scheduler-authority.json', 'TESTS/contracts/test_scheduler_authority.py',
               'TESTS/adapters/django/scheduler_authority.py', 'TESTS/adapters/go/schedule_due.py',
               'BE_GO/internal/domain/schedule.go', 'BE_GO/internal/service/schedule_due.go',
               'BE_GO/internal/service/schedule_due_test.go']
    report = {
        'success': True, 'django_oracle_cases': len(oracle['cases']), 'differential_cases': sorted(cases),
        'rejection_cases': sum(event.get('Action') == 'pass' and event.get('Test', '').startswith(
            'TestScheduleDueRejectsUnsupportedInputs/') for event in events),
        'go_runtime': subprocess.check_output(['go', 'version'], cwd=ROOT / 'BE_GO', text=True).strip(),
        'django_runtime': oracle['runtime'],
        'sha256': {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sources},
        'scope': 'Pure persisted daily-reset timing only; no production scheduling, enqueue, ownership or handoff proof',
    }
    result_path.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
