"""Portable table-preference HTTP expectations supplied by fixture adapters."""
import json
import os
import unittest
from TESTS.http_client import Client


class ProfileTablesContracts(unittest.TestCase):
    maxDiff = None

    def test_declared_table_preferences(self):
        with open(os.environ['TESTS_PROFILE_TABLES_FIXTURE']) as stream:
            cases = json.load(stream)['cases']
        for case in cases:
            with self.subTest(case=case['name']):
                headers = {'Accept-Language': 'en', 'X-Correlation-ID': case['name'], 'X-Forwarded-For': '198.51.100.83', **case.get('headers', {})}
                body = case.get('body', {})
                if 'raw_body' in case:
                    body = case['raw_body'].encode()
                    headers['Content-Type'] = case.get('content_type', 'application/json')
                status, response, received = Client(os.environ['TESTS_BASE_URL'], case.get('token')).request(
                    case.get('method', 'POST'), '/profile/settings/tables' + case.get('query', ''), body, headers, include_headers=True)
                self.assertEqual(status, case['status'])
                self.assertIsNone(received.get('Set-Cookie'))
                if status == 500:
                    self.assertIn('Server Error (500)', response)
                else:
                    self.assertEqual(response, case['response'])
                    self.assertEqual(set(received['Allow'].split(', ')), {'POST', 'OPTIONS'})
