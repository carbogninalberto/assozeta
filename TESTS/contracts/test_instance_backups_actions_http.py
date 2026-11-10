"""Independently authored backup actions HTTP expectations; no Django imports.

The shared family checks (status, error page, Allow, Content-Language, no
cookie, Bearer challenge) plus Cache-Control (no-store only on the remote
relays' successes) and no attachment. Requests are sent with http.client so
an upload can carry an exact body with its Content-Length, or a chunked one
without. '<empty>' is a body-less JSON answer (HEAD); '<none>' is DRF rendering
None (no body, no Content-Type).
"""
import base64
import http.client
import json
import random
import threading
from pathlib import Path
from urllib.parse import urlsplit

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/instance_backups_actions_http.json'
BASE = '/instance/admin/backups'


def load_source(path=SOURCE):
    return family.load_source(path)


def materialize(spec):
    """Upload bytes: {'b64': ...} or {'seed', 'size'} (gzip magic then seeded random bytes)."""
    if 'b64' in spec:
        return base64.b64decode(spec['b64'])
    return b'\x1f\x8b\x08\x00' + random.Random(spec['seed']).randbytes(spec['size'] - 4)


def allowed(path):
    path = path.split('?', 1)[0]
    fixed = {BASE + '/schedule': {'PUT', 'OPTIONS'}, BASE + '/upload': {'POST', 'OPTIONS'},
             BASE + '/remote': {'GET', 'HEAD', 'PUT', 'OPTIONS'}, BASE + '/remote/test': {'POST', 'OPTIONS'},
             BASE + '/remote/key': {'POST', 'OPTIONS'}, BASE + '/remote/key/confirm': {'POST', 'OPTIONS'},
             BASE + '/remote/fetch': {'POST', 'OPTIONS'}}
    if path in fixed:
        return fixed[path]
    if path.endswith('/restore'):
        return {'POST', 'OPTIONS'}
    return {'PATCH', 'DELETE', 'OPTIONS'}


def language(headers):
    value = headers.get('Accept-Language', 'en')
    return 'en' if value.lower().startswith('en') else 'it'


def send(base_url, method, path, body, headers, *, chunked=False):
    parts = urlsplit(base_url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=60)
    try:
        if chunked:
            headers = {**headers, 'Transfer-Encoding': 'chunked'}
            connection.request(method, parts.path + path, body=iter([body]), headers=headers, encode_chunked=True)
        else:
            connection.request(method, parts.path + path, body=body, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), response.headers
    finally:
        connection.close()


def send_early(base_url, method, path, body, headers):
    """The body is written by a thread while the answer is read: a server may answer before reading it."""
    parts = urlsplit(base_url)
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=60)
    try:
        connection.putrequest(method, parts.path + path)
        for name, value in {**headers, 'Content-Length': str(len(body))}.items():
            connection.putheader(name, value)
        connection.endheaders()

        def write():
            view = memoryview(body)
            try:
                for start in range(0, len(view), 65536):
                    connection.sock.sendall(view[start:start + 65536])
            except OSError:
                pass  # the server answered and closed without reading the rest
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        response = connection.getresponse()
        result = response.status, response.read(), response.headers
        writer.join(timeout=60)
        return result
    finally:
        connection.close()


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name']}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body, chunked = None, False
    if case['send'] is not None:
        body = materialize(case['send'])
        chunked = bool(case['send'].get('chunked'))
        headers['Content-Type'] = 'application/gzip'
    elif 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    elif 'body' in case:
        body = json.dumps(case['body']).encode()
        headers['Content-Type'] = 'application/json'
    headers.update(substitute(case['headers'], sessions))
    if case['send'] is not None and case['send'].get('early'):
        status, raw, received = send_early(base_url, case['method'], case['path'], body, headers)
    else:
        status, raw, received = send(base_url, case['method'], case['path'], body, headers, chunked=chunked)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed(case['path']), 'response.allow')
    check(received.get('Content-Language') == language(headers) and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    check(received.get('Cache-Control') == ('no-store' if case['no_store'] else None), 'response.cache-control')
    check(received.get('Content-Disposition') is None, 'response.no-attachment')
    if case['response'] == '<none>':
        # DRF renders None as no content and drops the Content-Type.
        check(raw == b'' and received.get('Content-Type') is None, 'response.none')
        return status
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty-body')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = case['response']
    check(type(actual) is type(expected) and actual == expected, 'response.body' + difference(expected, actual))
    return status
