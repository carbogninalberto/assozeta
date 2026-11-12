"""Portable scheduler observations: no framework, DB, broker or backend imports.

An adapter must emit measured observations; expected outcomes live in the shared
fixture. This characterizes existing authority, including gaps, not desired Go
behavior or approval to activate a scheduler.
"""
import json
import os
from pathlib import Path
import unittest

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/scheduler-authority.json'


def validate_report(report, fixture):
    if report.get('schema_version') != 1:
        raise AssertionError('Scheduler report schema_version must be 1')
    if report.get('runtime') != fixture['runtime']:
        raise AssertionError('Scheduler oracle must use the pinned runtime')
    cases = report.get('cases', {})
    if set(cases) != set(fixture['cases']):
        raise AssertionError(f'Scheduler case set mismatch: {set(cases) ^ set(fixture["cases"])}')
    for name, spec in fixture['cases'].items():
        if cases[name] != spec['expected']:
            raise AssertionError(f'{name}: expected {spec["expected"]!r}, observed {cases[name]!r}')


class SchedulerAuthorityContracts(unittest.TestCase):
    def test_actual_scheduler_observations(self):
        report_path = os.environ.get('TESTS_SCHEDULER_AUTHORITY_REPORT')
        self.assertTrue(report_path, 'A real scheduler adapter report is required; no fixture-only pass')
        report = json.loads(Path(report_path).read_text())
        self.assertIs(report.get('success'), True, 'Adapter must finish validation and private-service cleanup')
        validate_report(report, json.loads(FIXTURE.read_text()))


if __name__ == '__main__':
    unittest.main()
