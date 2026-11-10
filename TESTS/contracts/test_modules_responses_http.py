"""Independently authored module responses HTTP expectations; no Django imports.

The shared family checks plus:
- the Allow set of each view (add-attachment allows POST and DELETE);
- '$new' module_response_id accepts the created id (the runner compares it
  with the stored row through RUNTIME['response']);
- a response's attachment pks and attachments_docs are unordered;
- the export's ``{'$xlsx': spec}`` workbook is checked with the opt-in XLSX
  standard of TESTS/contracts/xlsx.py within the request window, and
  ``$today`` in its filename is the server's UTC date during the request.
"""
from datetime import datetime, timezone

from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.contracts import xlsx
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/modules_responses_http.json'
ALLOWED = {'info': {'GET', 'OPTIONS'}, 'add': {'POST', 'OPTIONS'}, 'approve': {'POST', 'OPTIONS'}, 'delete': {'DELETE', 'OPTIONS'},
           'add-attachment': {'POST', 'DELETE', 'OPTIONS'}, 'export': {'GET', 'OPTIONS'}}
RUNTIME = {}


def load_source(path=SOURCE):
    return family.load_source(path, minimum=60)


def allowed(case):
    return ALLOWED[case['path'].split('?')[0].rstrip('/').rsplit('/', 1)[1]]


def normalize(case, expected, actual):
    RUNTIME['response'] = actual
    finished = datetime.now(timezone.utc)
    expected, reason = xlsx.resolve(expected, actual, RUNTIME['started'], finished)
    require(reason is None, f'response.xlsx: {reason}'[:600])
    data = expected.get('data') if isinstance(expected, dict) else None
    if isinstance(data, dict) and isinstance(data.get('filename'), str) and '$today' in data['filename']:
        days = {RUNTIME['started'].date().isoformat(), finished.date().isoformat()}
        received = actual.get('data', {}).get('filename') if isinstance(actual, dict) and isinstance(actual.get('data'), dict) else None
        day = next((d for d in sorted(days) if data['filename'].replace('$today', d) == received), finished.date().isoformat())
        expected = {**expected, 'data': {**data, 'filename': data['filename'].replace('$today', day)}}

    def ordered(body):
        data = body.get('response_data') if isinstance(body, dict) else None
        if isinstance(data, dict):
            data = dict(data)
            if isinstance(data.get('attachments'), list):
                data['attachments'] = sorted(data['attachments'], key=str)
            if isinstance(data.get('attachments_docs'), list) and all(isinstance(d, dict) for d in data['attachments_docs']):
                data['attachments_docs'] = sorted(data['attachments_docs'], key=lambda d: str(d.get('document_id')))
            body = {**body, 'response_data': data}
        return body

    return ordered(expected), ordered(actual)


BAD_REQUEST_DOCUMENT = (b'\n<!doctype html>\n<html lang="en">\n<head>\n  <title>Bad Request (400)</title>\n</head>\n<body>\n'
                        b'  <h1>Bad Request (400)</h1><p></p>\n</body>\n</html>\n')


def assert_case(case, base_url, *, token, sessions):
    RUNTIME['response'] = None
    RUNTIME['started'] = datetime.now(timezone.utc)
    if case['response'] == '<bad-request-page>':
        # Django's SuspiciousOperation answer (Storage.save refusing a name).
        headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
                   **family.substitute(case['headers'], sessions)}
        status, raw, received = family.Client(base_url, token).request(case['method'], case['path'], case.get('body'), headers, raw=True,
                                                                       include_headers=True)
        if not (type(status) is int and status == case['status']):
            raise family.ResponseFailure('response.status', status)
        if raw != BAD_REQUEST_DOCUMENT:
            raise family.ResponseFailure('response.bad-request-document', status)
        if received.get('Content-Type', '').split(';')[0] != 'text/html':
            raise family.ResponseFailure('response.bad-request-type', status)
        return status
    return family.assert_case(case, base_url, token=token, sessions=sessions, allowed=allowed, new=('module_response_id',),
                              normalize=normalize)
