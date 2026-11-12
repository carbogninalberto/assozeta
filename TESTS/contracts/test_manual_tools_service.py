"""Portable comparison of complete tool results and live service authority."""
import hashlib
import json
import os
from pathlib import Path
import unittest

from TESTS.contracts.test_manual_tools import FIXTURE, validate_report


class ManualToolServiceContracts(unittest.TestCase):
    def test_actual_service_differential(self):
        path=os.environ.get('TESTS_MANUAL_TOOL_SERVICE_REPORT')
        self.assertTrue(path,'Actual PostgreSQL service report required')
        report=json.loads(Path(path).read_text());fixture=json.loads(FIXTURE.read_text())
        self.assertIs(report.get('success'),True)
        self.assertIs(report.get('private_services_stopped'),True)
        self.assertEqual(report.get('fixture_sha256'),hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        validate_report(report,fixture)
        for name,case in report['cases'].items():
            self.assertIs(case.get('go_service_files_unchanged'),True,name)
            self.assertIs(case.get('go_service_database_unchanged'),True,name)
            self.assertEqual(case.get('go_service_result'),case['result'],name)
            self.assertEqual(case.get('go_service_selects'),0 if case['result']['status']=='invalid_query' and fixture['cases'][name]['tool']=='search_manual' else 1,name)
        self.assertEqual(report.get('service_lifecycle'),dict(live_account_status=True,live_instance_and_association=True,
            concurrent_calls=32,sql_per_call=1,blocked_query_cancellation_and_recovery=True,database_unchanged=True,files_unchanged=True))


if __name__=='__main__':unittest.main()
