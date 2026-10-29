"""Failure controls for the browser fixture, separate from portable contracts."""
import subprocess
import traceback
import unittest
from unittest.mock import patch

from TESTS.adapters.django import instance_pwa_browser as fixture


class PWAObservationTests(unittest.TestCase):
    def test_successful_django_response_is_rejected_for_go(self):
        rows = [{'path': '/instance/logo.png', 'method': 'GET', 'status': 200}]
        with self.assertRaisesRegex(AssertionError, '^upstream_mismatch$'):
            fixture.validate_selected_upstream('v2', rows)
        fixture.validate_selected_upstream('v2', [])
        expected = [{'path': '/' + name, 'method': 'GET', 'status': 200}
                    for name in fixture.SELECTED]
        for mode in ('stock', 'v1'):
            fixture.validate_selected_upstream(mode, expected)
            with self.assertRaises(AssertionError):
                fixture.validate_selected_upstream(mode, expected[:-1])
        with self.assertRaises(AssertionError):
            fixture.validate_selected_upstream('unknown', [])

    def test_docker_timeout_does_not_export_credentials_or_exception_context(self):
        canary = 'private-pwa-storage-timeout-canary'
        error = subprocess.TimeoutExpired(['docker', 'run', '--env', canary], 1)
        for client in (fixture.ImageFixture, fixture.BrowserImageFixture):
            with self.subTest(client=client.__name__), patch('subprocess.run', side_effect=error):
                try:
                    client.docker('run', '--env', canary)
                except RuntimeError as failure:
                    rendered = ''.join(traceback.format_exception(failure))
                    self.assertNotIn(canary, rendered)
                    self.assertIn('timed out', str(failure))
                    self.assertTrue(failure.__suppress_context__)
                else:
                    self.fail('Docker timeout was accepted')


if __name__ == '__main__':
    unittest.main()
