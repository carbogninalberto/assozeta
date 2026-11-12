"""Portable impersonation management HTTP contracts; no application imports.

The adapter supplies synthetic identities, authored expected responses and a
fresh private database/cache. Session UUIDs are validated before later requests
refer to them; no other response values are normalized.
"""
import json
import gzip
import os
from pathlib import Path
import unittest
from uuid import UUID

from TESTS.http_client import Client


def substitute(value, sessions):
    if isinstance(value, str) and value.startswith('$session:'):
        return sessions[value.split(':', 1)[1]]
    if isinstance(value, dict):
        return {key: substitute(item, sessions) for key, item in value.items()}
    if isinstance(value, list):
        return [substitute(item, sessions) for item in value]
    return value


def assert_json_equal(test, actual, expected, path='$'):
    """Preserve the declared JSON types, including integer response fields."""
    test.assertIs(type(actual), type(expected), path + ': JSON type')
    if isinstance(expected, dict):
        test.assertEqual(set(actual), set(expected), path + ': object keys')
        for key in expected:
            assert_json_equal(test, actual[key], expected[key], path + '.' + key)
    elif isinstance(expected, list):
        test.assertEqual(len(actual), len(expected), path + ': array length')
        for index, (left, right) in enumerate(zip(actual, expected)):
            assert_json_equal(test, left, right, f'{path}[{index}]')
    else:
        test.assertEqual(actual, expected, path)


def assert_case(test, base, case, sessions):
    headers = substitute({'Accept-Language': 'en', 'X-Correlation-ID': case['name'],
                          **case.get('headers', {})}, sessions)
    body = substitute(case.get('body'), sessions)
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case.get('content_type', 'application/json')
    status, content, received = Client(base, case.get('token')).request(
        case['method'], case['path'], body, headers, include_headers=True, raw=True)
    if received.get('Content-Encoding') == 'gzip':
        content = gzip.decompress(content)
    try:
        response = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        response = content.decode(errors='replace')[:1000]
    test.assertEqual(status, case['status'], (case['name'], response))
    if 'save_session' in case:
        identifier = response.get('session_id')
        parsed = UUID(identifier)
        test.assertEqual(parsed.version, 4, case['name'])
        test.assertEqual(str(parsed), identifier, case['name'])
        test.assertNotIn(identifier, sessions.values(), case['name'])
        sessions[case['save_session']] = identifier
    if 'response' in case:
        assert_json_equal(test, response, substitute(case['response'], sessions), case['name'])
    if 'allow' in case:
        test.assertEqual(set(received.get('Allow', '').replace(' ', '').split(',')),
                         set(case['allow']), case['name'])
    test.assertIsNone(received.get('Set-Cookie'), case['name'])
    if status < 500:
        test.assertEqual(received.get('Content-Language'), headers.get('Accept-Language', 'en'), case['name'])
        if status != 204:
            test.assertEqual(received.get('Content-Type', '').split(';', 1)[0], 'application/json', case['name'])
        test.assertEqual(received.get('WWW-Authenticate'), 'Bearer realm="api"' if status == 401 else None, case['name'])
    if case.get('gzip'):
        test.assertEqual(received.get('Content-Encoding'), 'gzip', case['name'])
        test.assertIn('Accept-Encoding', received.get('Vary', ''), case['name'])
    return response


class ImpersonationManagementContracts(unittest.TestCase):
    def test_management_sequence(self):
        self.assertTrue(os.environ.get('TESTS_IMPERSONATION_MANAGEMENT_FIXTURE'), 'Fixture required; no skips')
        self.assertTrue(os.environ.get('TESTS_BASE_URL'), 'Backend required; no skips')
        fixture = json.loads(Path(os.environ['TESTS_IMPERSONATION_MANAGEMENT_FIXTURE']).read_text())
        self.assertIsInstance(fixture['cases'], list)
        self.assertGreater(len(fixture['cases']), 0)
        self.assertEqual(len(fixture['cases']), len({case['name'] for case in fixture['cases']}))
        sessions = dict(fixture.get('sessions', {}))
        for case in fixture['cases']:
            with self.subTest(case=case['name']):
                assert_case(self, os.environ['TESTS_BASE_URL'], case, sessions)
