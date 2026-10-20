"""Public setup and replacement-restore contract; requires an EMPTY disposable instance."""
import json
import os
from pathlib import Path
import unittest

from TESTS.fixtures.archive import archive_bytes, historical
from TESTS.http_client import Client, poll


class SetupAndRestore(unittest.TestCase):
    def test_fresh_setup_and_historical_restore_without_entitlements(self):
        fixture = json.loads(Path(os.environ['TESTS_FIXTURE']).read_text())
        public = Client(os.environ['TESTS_BASE_URL'], timeout=60)
        self.assertFalse(public.request('GET', '/instance/status')[1]['configured'])
        payload = {'domain': 'localhost', 'oem': {'name': 'Contract club'}, 'initialization': {
            'type': 'fresh', 'associationName': 'Fresh contract club',
            'ownerEmail': 'fresh@example.test', 'ownerPassword': 'ContractPass!123'}}
        self.assertEqual(public.request('POST', '/instance/configure', payload)[0], 401)
        status, result = public.request('POST', '/instance/configure', payload,
            {'X-Setup-Token': fixture['setup_token']})
        self.assertEqual(status, 200, result)
        owner, login = public.login({'username': 'fresh@example.test', 'password': 'ContractPass!123'})
        owner.timeout = 60
        self.assertEqual(owner.request('GET', '/collaborators/list')[0], 200)
        self.assertEqual(owner.request('GET', '/billing/active-plan')[0], 410)
        endpoint = '/instance/admin/data-restore'
        # Retired sections are accepted explicitly; unrelated malformed data is rejected.
        import copy
        for defect in ('unknown-model', 'retired-count', 'missing-owner'):
            invalid = copy.deepcopy(historical())
            if defect == 'unknown-model':
                invalid['entries']['manifest.json']['models_exported'].append(
                    {'name': 'UnknownFeature', 'file': 'data/unknown.json', 'count': 0})
                invalid['entries']['data/unknown.json'] = []
            elif defect == 'retired-count':
                invalid['entries']['manifest.json']['models_exported'][0]['count'] = 9
            else:
                invalid['entries']['data/02_users.json'] = []
                for spec in invalid['entries']['manifest.json']['models_exported']:
                    if spec['name'] == 'User':
                        spec['count'] = 0
            with self.subTest(defect=defect):
                status, rejected = owner.upload(endpoint, 'invalid.zip', archive_bytes(invalid))
                self.assertEqual(status, 400, rejected)
        status, operation = owner.upload(endpoint, 'historical.zip', archive_bytes())
        self.assertEqual(status, 201, operation)
        self.assertEqual(operation['preview']['skipped_records'], {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1})
        status, result = owner.request('POST', f"{endpoint}/{operation['id']}/start",
            {'confirmation': 'RIPRISTINA', 'allow_missing_media': True})
        self.assertEqual(status, 202, result)
        _, completed = poll(lambda: owner.request('GET', f"{endpoint}/{operation['id']}/status"),
                            lambda response: response[1].get('state') in ('completed', 'failed'), timeout=60)
        self.assertEqual(completed['state'], 'completed', completed)
        self.assertEqual(owner.request('GET', '/profile/info')[1]['user_data']['user_id'], login['user_data']['user_id'])
        self.assertEqual(owner.request('GET', '/collaborators/list')[0], 200)
        self.assertEqual(owner.request('GET', '/billing/active-plan')[0], 410)
        ids = historical()['ids']
        status, person = owner.request('GET', f"/personas/{ids['person']}/info")
        self.assertEqual(status, 200, person)
        self.assertIn('Historical', json.dumps(person))
        # A second restore produces a recovery export of the imported records.
        status, second = owner.upload(endpoint, 'historical-again.zip', archive_bytes())
        self.assertEqual(status, 201, second)
        self.assertEqual(owner.request('POST', f"{endpoint}/{second['id']}/start",
            {'confirmation': 'RIPRISTINA', 'allow_missing_media': True})[0], 202)
        _, completed = poll(lambda: owner.request('GET', f"{endpoint}/{second['id']}/status"),
                            lambda response: response[1].get('state') in ('completed', 'failed'), timeout=60)
        self.assertEqual(completed['state'], 'completed', completed)
        status, recovery = owner.request('GET', f"{endpoint}/{second['id']}/backup", raw=True)
        self.assertEqual(status, 200)
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(recovery)) as archive:
            attachments = [name for name in archive.namelist() if name.endswith('/historical.txt')]
            self.assertEqual(len(attachments), 1)
            self.assertEqual(archive.read(attachments[0]), b'Synthetic historical attachment.\n')
            subscriptions = json.loads(archive.read('data/25_subscriptions.json'))
            self.assertEqual(subscriptions[0]['signature_url'], 'https://legacy.example.test/signature.png')
            self.assertEqual(str(subscriptions[0]['payment_id']), ids['payment'])
        status, third = owner.upload(endpoint, 'recovery.zip', recovery)
        self.assertEqual(status, 201, third)
        self.assertEqual(owner.request('POST', f"{endpoint}/{third['id']}/start",
            {'confirmation': 'RIPRISTINA', 'allow_missing_media': True})[0], 202)
        _, completed = poll(lambda: owner.request('GET', f"{endpoint}/{third['id']}/status"),
                            lambda response: response[1].get('state') in ('completed', 'failed'), timeout=60)
        self.assertEqual(completed['state'], 'completed', completed)
        self.assertEqual(owner.request('GET', f"/personas/{ids['person']}/info")[0], 200)
