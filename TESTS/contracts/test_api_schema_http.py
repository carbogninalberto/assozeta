"""Independently authored schema/ HTTP expectations; no Django imports.

Literal responses are authored in the fixture generator. Document, OPTIONS and
page responses are references into the data Django generated for
BE_GO/internal/apischema (TESTS/adapters/django/api_schema.py, kept current by
its --check in CI): this contract rebuilds the expected bytes from that data
with Python's own json module and checks them byte for byte, plus the headers
Django's middleware stack sets. Vary is compared as a set of names.
"""
import email.utils
import gzip
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

from TESTS.contracts.http_family import load_source as family_load_source
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, require, substitute
from TESTS.http_client import NoRedirect

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'TESTS/fixtures/api_schema_http.json'
DATA = ROOT / 'BE_GO/internal/apischema'
CHARS = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
COOKIE = re.compile(r'csrftoken=([A-Za-z0-9]{32}); expires=([A-Za-z]{3}, \d{2} [A-Za-z]{3} \d{4} \d{2}:\d{2}:\d{2} GMT); '
                    r'Max-Age=31449600; Path=/; SameSite=Lax(; Secure)?\Z')


def load_source(path=SOURCE):
    return family_load_source(path)


def _data():
    catalog = json.loads((DATA / 'catalog.json').read_text())
    keys = json.loads((DATA / 'testdata/catalog_keys.json').read_text())
    base = {form: (DATA / f'schema.{form}').read_text().splitlines(keepends=True) for form in ('yaml', 'json')}
    return catalog, keys, base


def expected_document(form, lang, version, indent, version_text=None):
    catalog, keys, base = _data()
    lines = list(base[form])
    line = catalog['version_lines'][form]
    if version_text is not None:
        # Authored from Django's output: the renderer's own scalar style for the version.
        lines[line] = version_text
    elif version:
        lines[line] = lines[line].replace(catalog['version'], f"{catalog['version']} ({version})", 1)
    out, position = [], 0
    for start, end, replacement in catalog['variants'][catalog['variant_of'][keys[lang]]][form]:
        out += lines[position:start] + replacement
        position = end
    text = ''.join(out + lines[position:])
    if form == 'json' and indent != 4:
        # DRF dumps the data again with the accepted indent.
        text = json.dumps(json.loads(text), indent=indent, ensure_ascii=False, separators=(',', ': '))
        text = text.replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return text


def expected_body(response):
    """(prefix, suffix) of the expected body; a swagger page has the CSRF token between them."""
    if isinstance(response, str):
        return (b'' if response == '<empty>' else SERVER_ERROR_DOCUMENT if response == '<stable-server-error-page>'
                else response.encode()), None
    catalog, _keys, _base = _data()
    kind = response['kind']
    if kind == 'document':
        return expected_document(response['format'], response['lang'], response['version'], response['indent'],
                                 response.get('version_text')).encode(), None
    if kind == 'options':
        text = catalog['options'][response['language']][response['format']]
        if response['indent'] != 4:
            text = json.dumps(json.loads(text), indent=response['indent'], ensure_ascii=False, separators=(',', ': '))
        return text.encode(), None
    if kind == 'redoc':
        url = response['url'].replace('&', '&amp;')
        return catalog['pages']['redoc'].replace('{{schema_url}}', url).encode(), None
    if kind == 'redoc_options':
        return catalog['pages']['redoc_options'].encode(), None
    assert kind == 'swagger', kind
    escapes = {'\\': '\\u005C', "'": '\\u0027', '"': '\\u0022', '>': '\\u003E', '<': '\\u003C', '&': '\\u0026',
               '=': '\\u003D', '-': '\\u002D', ';': '\\u003B', '`': '\\u0060'}
    url = ''.join(escapes.get(character, character) for character in response['url'])
    prefix, suffix = catalog['pages']['swagger'].replace('{{schema_url}}', url).split('{{csrf_token}}')
    return prefix.encode(), suffix.encode()


def unmask(token):
    mask, cipher = token[:32], token[32:]
    return ''.join(CHARS[CHARS.index(c) - CHARS.index(m)] for c, m in zip(cipher, mask))


def assert_case(case, base_url, *, token, sessions):
    headers = {'X-Correlation-ID': case['name'], **substitute(case['headers'], sessions)}
    if token:
        headers.setdefault('Authorization', f'Bearer {token}')
    request = urllib.request.Request(base_url.rstrip('/') + case['path'], headers=headers, method=case['method'])
    try:
        response = urllib.request.build_opener(NoRedirect()).open(request, timeout=30)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        status, received, raw = response.status, response.headers, response.read()
    started = time.time()

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if received.get('Content-Encoding') == 'gzip':
        check(case['headers'].get('Accept-Encoding') == 'gzip', 'response.unrequested-encoding')
        raw = gzip.decompress(raw)
    prefix, suffix = expected_body(case['response'])
    length = len(prefix) + (64 + len(suffix) if suffix is not None else 0)
    if case['method'] == 'HEAD':
        check(raw == b'', 'response.head-body')
    elif suffix is None:
        check(raw == prefix, 'response.body')
    else:
        check(len(raw) == length and raw.startswith(prefix) and raw.endswith(suffix), 'response.page')
    content_type = 'text/html; charset=utf-8' if case['response'] == '<stable-server-error-page>' else case['content_type']
    check(received.get('Content-Type') == content_type, 'response.content-type')
    if received.get('Content-Encoding') != 'gzip' and case['status'] != 301:
        check(received.get('Content-Length') == str(length), 'response.content-length')
    check(received.get('Allow') == case['allow'], 'response.allow')
    check(received.get('Content-Language') == case['content_language'], 'response.content-language')
    check(received.get('Content-Disposition') == case['disposition'], 'response.disposition')
    check(received.get('Location') == case['location'], 'response.location')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    vary = {name.strip().lower() for value in received.get_all('Vary') or () for name in value.split(',')}
    # GZipMiddleware varies every response of 200 bytes or more on Accept-Encoding.
    expected_vary = {name.lower() for name in case['vary']} | ({'accept-encoding'} if length >= 200 else set())
    check(vary == expected_vary, 'response.vary')
    swagger = isinstance(case['response'], dict) and case['response']['kind'] == 'swagger'
    check(received.get('Cross-Origin-Opener-Policy') == ('unsafe-none' if swagger else 'same-origin'), 'response.opener-policy')
    cookies = received.get_all('Set-Cookie') or []
    if case['csrf'] is None:
        check(not cookies, 'response.cookies')
        return status
    check(len(cookies) == 1, 'response.csrf-cookie')
    match = COOKIE.match(cookies[0])
    check(match is not None, 'response.csrf-cookie-format')
    secret, expires = match[1], match[2]
    # Locale-independent: the server process may run under an Italian LC_TIME.
    expiry = email.utils.parsedate_to_datetime(expires).timestamp()
    check(abs(expiry - (started + 31449600)) < 120, 'response.csrf-cookie-expiry')
    if case['csrf'] != 'new':
        check(secret == case['csrf'], 'response.csrf-secret-kept')
    if case['method'] != 'HEAD':
        masked = raw[len(prefix):len(prefix) + 64].decode()
        check(re.fullmatch('[A-Za-z0-9]{64}', masked) is not None and unmask(masked) == secret, 'response.csrf-token')
    return status
