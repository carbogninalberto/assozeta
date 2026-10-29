"""Portable payment-category HTTP contracts.

The fixture is produced by the real Django oracle after its status, persistence,
tenant, and audit invariants have passed. This module imports only the neutral
HTTP client so the identical cases can run against Django or Go.

Generate with the production-pinned Python and
``LC_ALL=C LANG=C TESTS_PAYMENT_CATEGORIES_GO=1 python
TESTS/adapters/django/accounts.py --only payment-categories``. The generated
fixture contains ephemeral authentication tokens and is written only under the
ignored ``quality-reports/go-migration/payment-categories/fixture.json`` path.
Set TESTS_PAYMENT_CATEGORIES_FIXTURE to that path and TESTS_BASE_URL to a freshly
seeded matching backend. Do not replay the mutation sequence against its final
state; the integrated adapter resets the full seed between native and versioned
runs, including the matching private signing keys and impersonation sessions.
"""
import json
import os
from pathlib import Path
import re
import unittest

from TESTS.http_client import Client


UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$')
DATETIME = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$')
MICRO_DATETIME = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$')


def normalize(value, volatile):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in volatile and isinstance(item, str) and UUID.match(item):
                result[key] = '<uuid>'
            elif key in volatile and isinstance(item, str) and MICRO_DATETIME.match(item):
                result[key] = '<datetime-micros>'
            elif key in volatile and isinstance(item, str) and DATETIME.match(item):
                result[key] = '<datetime>'
            else:
                result[key] = normalize(item, volatile)
        return result
    if isinstance(value, list):
        return [normalize(item, volatile) for item in value]
    return value


def assert_case(testcase, base_url, case):
    """Execute the same neutral wire assertions in adapters and standalone tests."""
    testcase.assertIn(case['status'], range(200, 600))

    headers = {
        'Accept-Language': 'en',
        'X-Correlation-ID': case['name'],
        'X-Forwarded-For': case['remote_addr'],
        **case.get('headers', {}),
    }
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case.get('content_type', 'application/json')
    status, response, received = Client(
        base_url, case.get('token'), timeout=30
    ).request(
        case['method'],
        case['path'] + case.get('query', ''),
        body,
        headers,
        include_headers=True,
    )
    testcase.assertEqual(status, case['status'], case['name'])
    if case.get('response_contains'):
        testcase.assertIn(case['response_contains'], str(response), case['name'])
    else:
        volatile = set(case.get('volatile', ()))
        testcase.assertEqual(
            normalize(response, volatile),
            normalize(case['response'], volatile),
            case['name'],
        )
    if case.get('content_type_contains'):
        testcase.assertIn(
            case['content_type_contains'],
            received.get('Content-Type', '').lower(),
            case['name'],
        )
    if case.get('allow') and case['status'] < 500:
        testcase.assertEqual(
            set(filter(None, received.get('Allow', '').split(', '))),
            set(case['allow']),
            case['name'],
        )
    testcase.assertIsNone(received.get('Set-Cookie'), case['name'])

    return status, response, received


class PaymentCategoryContracts(unittest.TestCase):
    maxDiff = None

    def test_declared_payment_category_family(self):
        fixture_path = os.environ.get('TESTS_PAYMENT_CATEGORIES_FIXTURE')
        base_url = os.environ.get('TESTS_BASE_URL')
        self.assertTrue(fixture_path, 'TESTS_PAYMENT_CATEGORIES_FIXTURE is required; no skips')
        self.assertTrue(base_url, 'TESTS_BASE_URL is required; no skips')
        fixture = json.loads(Path(fixture_path).read_text())
        cases = fixture.get('cases')
        self.assertIsInstance(cases, list)
        self.assertGreater(len(cases), 0, 'fixture must declare cases')
        seen = set()
        for case in cases:
            with self.subTest(case=case['name']):
                self.assertNotIn(case['name'], seen, 'duplicate case name')
                seen.add(case['name'])
                assert_case(self, base_url, case)
