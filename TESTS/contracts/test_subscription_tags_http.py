"""Independently authored subscription/tags list, add, update, delete, assign and unassign HTTP expectations; no Django imports.

The checks are http_family's; add nests the generated key under 'tag'
(``{'tag': {'tag_id': '$new', ...}}``), which the runner checks is new.
"""
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import ResponseFailure, require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_tags_http.json'
ALLOWED = {'list': 'GET', 'add': 'POST', 'update': 'PATCH', 'delete': 'DELETE'}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    segments = case['path'].split('/')
    verb = segments[-1] if segments[-1] in ALLOWED else segments[-2]
    return {ALLOWED.get(verb, 'PATCH'), 'OPTIONS'}


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
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    nested = expected.get('tag') if isinstance(expected, dict) else None
    if isinstance(nested, dict) and nested.get('tag_id') == '$new':
        created = actual.get('tag') if isinstance(actual, dict) else None
        check(isinstance(created, dict) and isinstance(created.get('tag_id'), str) and created['tag_id'], 'response.new-tag_id')
        expected = {**expected, 'tag': {**nested, 'tag_id': created['tag_id']}}
    check(actual == expected, 'response.body' + difference(expected, actual))
    # Python equality conflates False with 0; the echoed tag_name keeps its JSON type.
    check(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), 'response.body-types')
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
