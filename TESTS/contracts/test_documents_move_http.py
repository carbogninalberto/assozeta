"""Independently authored documents/<pk>/move HTTP expectations; no Django imports."""
import json
from pathlib import Path
import re

from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/documents_move_http.json'


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source['matrix_version'] == 1 and len(source['cases']) >= 10, 'source.version-count')
    require(len({case['name'] for case in source['cases']}) == len(source['cases']), 'source.names')
    return source


def bind_folders(value, folders):
    """Replace $folder:<label> with the seeded integer ID."""
    if isinstance(value, str) and value.startswith('$folder-text:'):
        return str(folders[value[13:]])
    if isinstance(value, str) and value.startswith('$folder:'):
        return folders[value[8:]]
    if isinstance(value, list):
        return [bind_folders(item, folders) for item in value]
    if isinstance(value, dict):
        return {key: bind_folders(item, folders) for key, item in value.items()}
    return value


def bind_path(path, folders):
    # Whole labels only: '$folder:a' must not match inside '$folder:a1a'.
    return re.sub(r'\$folder:([a-z0-9_]+)', lambda match: str(folders[match.group(1)]), path)


def assert_case(case, base_url, *, token, sessions, folders):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    path = bind_path(case['path'], folders)
    body = bind_folders(case.get('body'), folders)
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    status, raw, received = Client(base_url, token).request(case['method'], path, body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        return status
    # The viewset's delete() makes DELETE allowed (and dispatched, with this pk) here.
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == {'POST', 'DELETE', 'OPTIONS'}, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'' and received.get('Content-Type') is None, 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = bind_folders(case['response'], folders)
    check(json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True), 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
