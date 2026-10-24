"""Portable bootstrap observations; no framework, database or backend imports."""
import json
import os
from pathlib import Path
import unittest

from TESTS.contracts.test_payment_info import assert_json_equal

FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/instance-bootstrap.json'


def validate_report(report, fixture):
    if report.get('schema_version') != 1:
        raise AssertionError('Bootstrap report schema must be 1')
    if set(report.get('cases', {})) != set(fixture['cases']):
        raise AssertionError('Bootstrap case set differs; no skipped scenarios')
    for name, spec in fixture['cases'].items():
        actual = report['cases'][name]
        for field, expected in spec['expected'].items():
            if field == 'content_encoding_one_of':
                encoding = actual.get('headers', {}).get('Content-Encoding')
                if encoding not in expected:
                    raise AssertionError(f'{name}: unexpected randomized buffered encoding {encoding!r}')
                if encoding == 'gzip':
                    if actual.get('gzip_decoded') is not True or not 0 < actual['wire_body_bytes'] < actual['decoded_body_bytes']:
                        raise AssertionError(f'{name}: buffered gzip must decode and be smaller than its original body')
                elif actual['wire_body_bytes'] != actual['decoded_body_bytes']:
                    raise AssertionError(f'{name}: uncompressed byte counts differ')
                continue
            if field == 'headers':
                for key, value in expected.items():
                    if actual.get('headers', {}).get(key) != value:
                        raise AssertionError(f'{name}.header {key}: expected {value!r}, observed {actual.get("headers", {}).get(key)!r}')
                continue
            assert_json_equal(unittest.TestCase(), actual.get(field), expected, f'{name}.{field}')
        if actual.get('database_unchanged') is not True or type(actual.get('write_statements')) is not int or actual['write_statements'] != 0:
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
