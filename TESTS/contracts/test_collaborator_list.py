"""Portable authored collaborator/invitation read contract; no ORM imports."""
import gzip
import json

from TESTS.contracts.test_impersonation_management import assert_json_equal
from TESTS.http_client import Client


MATRIX_VERSION = 1
MATRIX_CASE_COUNT = 115
MATRIX_REQUIRED_NAMES = {
    'collaborator-list-locale-fr-scoped-media', 'collaborator-list-locale-cookie-last-fr',
    'collaborator-list-locale-cookie-last-en', 'collaborator-list-owner-list', 'collaborator-list-detail-foreign',
    'collaborator-list-collaborator-chain', 'collaborator-list-custom-denied',
    'collaborator-list-uuid-unicode', 'collaborator-list-duplicate-query-last',
    'collaborator-list-format-before-accept-auth', 'collaborator-list-gzip',
    'collaborator-list-query-limit-1000-unique', 'collaborator-list-query-limit-1001-unique',
    'collaborator-list-query-limit-1001-before-format',
    'collaborator-list-locale-weighted-italian', 'collaborator-list-locale-french',
    'collaborator-list-locale-fr-method', 'collaborator-list-impersonation-614004',
}


def assert_case(test, base, case):
    headers = {'Accept-Language': 'en', 'X-Correlation-ID': case['name'], **case.get('headers', {})}
    status, content, received = Client(base, case.get('token')).request(case['method'], case['path'],
        case['raw_body'].encode() if 'raw_body' in case else case.get('body'), headers, include_headers=True, raw=True)
    if received.get('Content-Encoding') == 'gzip':
        content = gzip.decompress(content)
    try:
        response = json.loads(content)
    except (ValueError, UnicodeDecodeError):
        response = content.decode(errors='replace')
    test.assertEqual(status, case['status'], case['name'])
    if 'response' in case:
        assert_json_equal(test, response, case['response'], case['name'])
    test.assertIsNone(received.get('Set-Cookie'), case['name'])
    if status < 500:
        test.assertEqual(received.get('Content-Language'), case.get('language',headers['Accept-Language']), case['name'])
        test.assertEqual(received.get('WWW-Authenticate'), 'Bearer realm="api"' if status == 401 else None, case['name'])
        if case.get('allow','GET, OPTIONS') is None:
            test.assertIsNone(received.get('Allow'),case['name'])
        else:
            test.assertEqual(set(received.get('Allow', '').replace(' ', '').split(',')), {'GET', 'OPTIONS'}, case['name'])
        test.assertEqual(received.get('Content-Type', '').split(';', 1)[0], case.get('content_type','application/json'), case['name'])
    if case.get('gzip'):
        test.assertEqual(received.get('Content-Encoding'), 'gzip', case['name'])
    return status, response
