"""Portable monitoring contract: public probes and GET-only admission."""
import os
import unittest
from TESTS.http_client import Client


class HealthContract(unittest.TestCase):
    def setUp(self):
        self.client = Client(os.environ['TESTS_BASE_URL'])

    def test_public_liveness_preserves_status_and_version(self):
        status, body = self.client.request('GET', '/healthz')
        self.assertEqual(status, 200)
        self.assertEqual(body['status'], 'ok')
        self.assertIsInstance(body['version'], str)
        self.assertTrue(body['version'])
        self.assertEqual(set(body), {'status', 'version'})

    def test_ready_requires_database_and_cache(self):
        status, body = self.client.request('GET', '/readyz')
        self.assertEqual(status, 200)
        self.assertEqual(body, {'status': 'ready', 'checks': {'database': True, 'redis': True}})

    def test_probes_accept_only_get(self):
        for path in ('/healthz', '/readyz'):
            for method in ('POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'):
                with self.subTest(path=path, method=method):
                    expected = 403 if method in ('POST', 'PUT', 'PATCH', 'DELETE') else 405
                    self.assertEqual(self.client.request(method, path)[0], expected)
                    if expected == 403:
                        token = 'a' * 32
                        headers = {'Cookie': 'csrftoken=' + token, 'X-CSRFToken': token}
                        self.assertEqual(self.client.request(method, path, headers=headers)[0], 405)
