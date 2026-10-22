"""Portable profile-update responses. Fixtures declare every expected result."""
import json
import os
from pathlib import Path
import unittest
from TESTS.http_client import Client


class ProfileUpdateContracts(unittest.TestCase):
    maxDiff = None

    def test_declared_profile_updates(self):
        fixture = json.loads(Path(os.environ['TESTS_PROFILE_UPDATE_FIXTURE']).read_text())
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                body = case.get('body')
                headers = {'Accept-Language': 'en', 'X-Correlation-ID': case['name'], 'X-Forwarded-For': '198.51.100.79', **case.get('headers', {})}
                if 'raw_body' in case:
                    body = case['raw_body'].encode()
                    headers['Content-Type'] = case.get('content_type', 'application/json')
                status, response, received = Client(os.environ['TESTS_BASE_URL'], case.get('token')).request(
                    case.get('method', 'PATCH'), '/profile/update' + case.get('query', ''), body, headers, include_headers=True)
                self.assertEqual(status, case['status'], case['name'])
                self.assertIsNone(received.get('Set-Cookie'))
                if status == 500:
                    self.assertIn('Server Error (500)', response)
                else:
                    self.assertEqual(set(received['Allow'].split(', ')), {'PATCH', 'OPTIONS'})
                    self.assertEqual(response, case['response'], case['name'])
