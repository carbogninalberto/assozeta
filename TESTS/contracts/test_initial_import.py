"""Import a fixed legacy archive through public setup endpoints on an EMPTY instance."""
import json
import os
from pathlib import Path
import unittest

from TESTS.fixtures.archive import archive_bytes, historical
from TESTS.http_client import Client, poll


class InitialImport(unittest.TestCase):
    def test_initial_import_and_setup_ignore_legacy_software_entitlements(self):
        fixture = json.loads(Path(os.environ['TESTS_FIXTURE']).read_text())
        client = Client(os.environ['TESTS_BASE_URL'], timeout=60)
        headers = {'X-Setup-Token': fixture['setup_token']}
        status, task = client.upload('/association/import/start', 'historical.zip', archive_bytes(), headers)
        self.assertEqual(status, 202, task)
        _, result = poll(lambda: client.request('GET', '/association/import/status?task_id=' + task['task_id'], headers=headers),
                         lambda response: response[1].get('ready'), timeout=60)
        self.assertTrue(result['result']['success'], result)
        self.assertEqual(result['result']['skipped_records'], {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1})
        status, config = client.request('POST', '/instance/configure', {
            'domain': 'localhost', 'oem': {'name': 'Imported club'},
            'initialization': {'type': 'import', 'importTaskId': task['task_id']},
        }, headers)
        self.assertEqual(status, 200, config)
        owner, login = client.login(historical()['account'])
        self.assertEqual(login['user_data']['user_id'], historical()['ids']['owner'])
        self.assertEqual(owner.request('GET', '/collaborators/list')[0], 200)
        self.assertEqual(owner.request('GET', '/billing/active-plan')[0], 410)
        self.assertEqual(owner.request('GET', f"/personas/{historical()['ids']['person']}/info")[0], 200)
