"""Independently authored instance setup HTTP expectations; no Django imports.

The shared family checks plus:
- '$setup-token' in a header is the run's INSTANCE_SETUP_TOKEN, installed in
  RUNTIME by TESTS/adapters/django/instance_setup_http.py;
- an 'upload' case is sent as multipart/form-data built from its parts (a
  file part's bytes are its fill character repeated to its size; a part
  without content_type sends no Content-Type header);
- '$new' association_id accepts the generated id and '$logo-version' a
  canonical logo URL with a 20-digit version; the runner compares both with
  the stored rows (RUNTIME['response'] holds the decoded body).
"""
import re
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/instance_setup_http.json'
BOUNDARY = 'instance-setup-boundary-7f3a'
ALLOWED = {'/instance/setup-token/validate': {'POST', 'OPTIONS'}, '/instance/configure': {'POST', 'OPTIONS'},
           '/instance/reconfigure': {'PUT', 'OPTIONS'}, '/instance/logo': {'POST', 'OPTIONS'},
           '/instance/admin/logo': {'POST', 'OPTIONS'}}
LOGO_VERSION = re.compile(r'/api/instance/logo\.png\?v=\d{20}\Z')
RUNTIME = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def content(part):
    return (part['fill'] * part['size'])[:part['size']]


def multipart(parts):
    chunks = []
    for part in parts:
        if 'filename' in part:
            header = f'Content-Disposition: form-data; name="{part["name"]}"; filename="{part["filename"]}"\r\n'
            if part['content_type'] is not None:
                header += f'Content-Type: {part["content_type"]}\r\n'
            chunks.append(f'--{BOUNDARY}\r\n{header}\r\n{content(part)}\r\n')
        else:
            chunks.append(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{part["name"]}"\r\n\r\n{part["value"]}\r\n')
    return ''.join(chunks) + f'--{BOUNDARY}--\r\n'


def prepared(case):
    headers = {key: RUNTIME['setup_token'] if value == '$setup-token' else value for key, value in case['headers'].items()}
    case = {**case, 'headers': headers}
    if 'upload' in case:
        case['raw_body'], case['content_type'] = multipart(case['upload']['parts']), f'multipart/form-data; boundary={BOUNDARY}'
    return case


def normalize(case, expected, actual):
    RUNTIME['response'] = actual
    if isinstance(expected, dict) and expected.get('logo_url') == '$logo-version' and isinstance(actual, dict):
        value = actual.get('logo_url')
        if isinstance(value, str) and LOGO_VERSION.match(value):
            expected = {**expected, 'logo_url': value}
    return expected, actual


def assert_case(case, base_url, *, token, sessions):
    RUNTIME['response'] = None
    return family.assert_case(prepared(case), base_url, token=token, sessions=sessions, allowed=lambda case: ALLOWED[case['path']],
                              new=('association_id',), normalize=normalize)
