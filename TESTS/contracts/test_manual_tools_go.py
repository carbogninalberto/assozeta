"""Portable result comparison; the Go adapter supplies trusted library context."""
import hashlib
import json
import os
from pathlib import Path
import unittest

from TESTS.contracts.test_manual_tools import FIXTURE, validate_report


class ManualToolLibraryContracts(unittest.TestCase):
    def test_actual_library_differential(self):
        path = os.environ.get('TESTS_MANUAL_TOOL_LIBRARY_REPORT')
        self.assertTrue(path, 'Actual Go library differential report required')
        report = json.loads(Path(path).read_text())
        fixture = json.loads(FIXTURE.read_text())
        self.assertIs(report.get('success'), True)
        self.assertIs(report.get('private_services_stopped'), True)
        self.assertEqual(report.get('fixture_sha256'), hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertEqual(report.get('source_differential_cases'), list(fixture['cases']))
        validate_report(report, fixture)
        for name, case in report['cases'].items():
            self.assertIs(case.get('go_library_files_unchanged'), True, name)
            self.assertEqual(case.get('go_library_result'), case['result'], name)


if __name__ == '__main__':
    unittest.main()
