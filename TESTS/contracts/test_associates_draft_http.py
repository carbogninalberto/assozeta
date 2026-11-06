"""Independently authored subscription/associates-draft list, add, edit, delete and bulk-delete HTTP expectations; no Django imports.

The checks are http_family's, plus:
- list renders ``{'0': draft, '1': draft, ...}`` in database order: the keys
  must be the consecutive indexes and the entries are compared by
  ``associate_import_draft_id``;
- a '$new' leaf accepts the generated certificate id (a UUID string) that
  edit writes into the echoed ``edit_data`` (the runner checks the stored one);
- JSON types are compared exactly (Python equality conflates True with 1);
- the athletes' empty 401 has no Bearer challenge.
"""
import json
import re
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/associates_draft_http.json'
ALLOWED = {'list': 'GET', 'add': 'POST', 'edit': 'POST', 'delete': 'POST', 'bulk-delete': 'DELETE'}
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    return {ALLOWED[case['path'].rstrip('/').split('/')[-1]], 'OPTIONS'}


def accept_new(expected, actual):
    """expected with each '$new' leaf replaced by the UUID string at the same place in actual."""
    if expected == '$new':
        return actual if isinstance(actual, str) and UUID.fullmatch(actual) else expected
    if isinstance(expected, dict) and isinstance(actual, dict):
        return {key: accept_new(value, actual.get(key)) for key, value in expected.items()}
    if isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        return [accept_new(e, a) for e, a in zip(expected, actual)]
    return expected


def entries(value):
    """The list's indexed drafts sorted by id, or None when the keys are not 0..n-1."""
    if not isinstance(value, dict) or set(value) != {str(i) for i in range(len(value))}:
        return None
    return sorted(value.values(), key=lambda item: str(item.get('associate_import_draft_id')) if isinstance(item, dict) else '')


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    # The views' own Response(status=401) carries no challenge; DRF adds it for NotAuthenticated only.
    challenged = status == 401 and case['response'] != '<empty>'
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if challenged else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = accept_new(case['response'], actual)
    if case.get('indexed'):
        listed = entries(actual.get('data')) if isinstance(actual, dict) else None
        check(listed is not None, 'response.indexes')
        expected, actual = {**expected, 'data': entries(expected['data'])}, {**actual, 'data': listed}
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), 'response.body-types')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
