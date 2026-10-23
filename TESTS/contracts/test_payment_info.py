"""Portable authored payment-info wire contracts; no Django/Go imports.

Generate the private fixture using accounts.py --only payment-info. Its tokens
are ephemeral; replay requires the matching live private seed and signing keys.
Observed baseline concerns are characterization, not approved security policy.
"""
import json
import os
from pathlib import Path
import unittest

from TESTS.http_client import Client


def decode_response(content):
    """Retain the complete body so exclusion assertions cannot miss a suffix."""
    try:
        return json.loads(content)
    except (ValueError, UnicodeDecodeError):
        return content.decode(errors='replace')


def assert_json_equal(testcase, actual, expected, path='$'):
    """Compare JSON values without Python's boolean/number equivalence."""
    def kind(value):
        if value is None:
            return 'null'
        if isinstance(value, bool):
            return 'boolean'
        if isinstance(value, (int, float)):
            return 'number'
        if isinstance(value, str):
            return 'string'
        if isinstance(value, list):
            return 'array'
        if isinstance(value, dict):
            return 'object'
        testcase.fail(f'{path}: unsupported JSON value {type(value).__name__}')

    expected_kind = kind(expected)
    testcase.assertEqual(kind(actual), expected_kind, f'{path}: JSON type')
    if expected_kind == 'object':
        testcase.assertEqual(set(actual), set(expected), f'{path}: object keys')
        for key in expected:
            assert_json_equal(testcase, actual[key], expected[key], f'{path}.{key}')
    elif expected_kind == 'array':
        testcase.assertEqual(len(actual), len(expected), f'{path}: array length')
        for index, (value, wanted) in enumerate(zip(actual, expected)):
            assert_json_equal(testcase, value, wanted, f'{path}[{index}]')
    else:
        # JSON has one number type: 1 and 1.0 are equivalent, unlike true and 1.
        testcase.assertEqual(actual, expected, path)


def assert_case(testcase, base, case, observe=None):
    headers = {'Accept-Language': 'en', 'X-Correlation-ID': case['name'], **case.get('headers', {})}
    status, content, received = Client(base, case.get('token')).request(
        case.get('method', 'GET'), case['path'], case.get('body'), headers, raw=True, include_headers=True,
    )
    result = status, decode_response(content), received
    if observe:
        observe(result)
    status, response, received = result
    testcase.assertEqual(status, case['status'], case['name'])
    if case.get('html_error'):
        testcase.assertIn('Server Error', response, case['name'])
        testcase.assertIn('text/html', received.get('Content-Type', ''), case['name'])
    else:
        assert_json_equal(testcase, response, case['response'], case['name'])
        testcase.assertIn('application/json', received.get('Content-Type', ''), case['name'])
    for value in case.get('response_excludes', []):
        testcase.assertNotIn(value, str(response), case['name'])
    if status < 500:
        testcase.assertEqual({item.strip() for item in received.get('Allow', '').split(',')}, {'OPTIONS', 'GET'}, case['name'])
    testcase.assertIsNone(received.get('Set-Cookie'), case['name'])
    return result


class PaymentInfoContracts(unittest.TestCase):
    maxDiff = None

    def test_authored_payment_info_cases(self):
        path = os.environ.get('TESTS_PAYMENT_INFO_FIXTURE')
        base = os.environ.get('TESTS_BASE_URL')
        self.assertTrue(path, 'TESTS_PAYMENT_INFO_FIXTURE is required; no skips')
        self.assertTrue(base, 'TESTS_BASE_URL is required; no skips')
        fixture = json.loads(Path(path).read_text())
        cases = fixture['cases']
        self.assertTrue(cases)
        self.assertEqual(len(cases), len({case['name'] for case in cases}))
        for case in cases:
            with self.subTest(case=case['name']):
                assert_case(self, base, case)
