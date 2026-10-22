"""Portable bootstrap observations; no framework, database or backend imports."""
import json
import os
from pathlib import Path
import unittest

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/instance-bootstrap.json'


def validate_report(report, fixture):
    if report.get('schema_version') != 1:
        raise AssertionError('Bootstrap report schema must be 1')
    if set(report.get('cases', {})) != set(fixture['cases']):
        raise AssertionError('Bootstrap case set differs; no skipped scenarios')
    for name, spec in fixture['cases'].items():
        actual = report['cases'][name]
        for field, expected in spec['expected'].items():
            if field == 'headers':
                for key, value in expected.items():
                    if actual.get('headers', {}).get(key) != value:
                        raise AssertionError(f'{name}.header {key}: expected {value!r}, observed {actual.get("headers", {}).get(key)!r}')
                continue
            if actual.get(field) != expected:
                raise AssertionError(f'{name}.{field}: expected {expected!r}, observed {actual.get(field)!r}')
        if actual.get('database_unchanged') is not True or actual.get('write_statements') != 0:
            raise AssertionError(f'{name}: bootstrap must not write database state')
        if actual.get('secret_free') is not True:
            raise AssertionError(f'{name}: private configuration leaked')


class InstanceBootstrapContracts(unittest.TestCase):
    def test_measured_bootstrap_contract(self):
        path = os.environ.get('TESTS_INSTANCE_BOOTSTRAP_REPORT')
        self.assertTrue(path, 'A real adapter report is required')
        report = json.loads(Path(path).read_text())
        self.assertIs(report.get('success'), True, 'Adapter must validate and clean private services')
        validate_report(report, json.loads(FIXTURE.read_text()))


if __name__ == '__main__':
    unittest.main()
