"""Independently authored modules list/overview/check-link HTTP expectations; no Django imports.

Every check of http_family.assert_case applies. The views read without
ORDER BY, so before the exact comparison both bodies are put in one canonical
order: modules by module_id, responses by module_response_id, and each
response's attachments (pks) and attachments_docs (by document_id). Only the
order is normalized; every value, key and length is still compared exactly.
"""
import json
from pathlib import Path

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/modules_reads_http.json'


def load_source(path=SOURCE):
    return family.load_source(path)


def _by(items, field):
    if isinstance(items, list) and all(isinstance(item, dict) for item in items):
        return sorted(items, key=lambda item: str(item.get(field)))
    return items


def _response(item):
    if not isinstance(item, dict):
        return item
    item = dict(item)
    if isinstance(item.get('attachments'), list):
        item['attachments'] = sorted(item['attachments'], key=str)
    item['attachments_docs'] = _by(item.get('attachments_docs'), 'document_id')
    return item


def _module(item):
    if isinstance(item, dict) and isinstance(item.get('responses'), list):
        item = {**item, 'responses': [_response(row) for row in _by(item['responses'], 'module_response_id')]}
    return item


def canonical(body):
    """Canonical order of the unordered lists; values are untouched."""
    if not isinstance(body, dict) or 'data' not in body:
        return body
    data = body['data']
    if isinstance(data, list):
        data = [_module(item) for item in _by(data, 'module_id')]
    else:
        data = _module(data)
    return {**body, 'data': data}


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], None, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'GET', 'OPTIONS'}, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected, actual = canonical(case['response']), canonical(actual)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
