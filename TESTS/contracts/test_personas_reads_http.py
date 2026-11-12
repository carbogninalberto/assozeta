"""Independently authored personas reads HTTP expectations; no Django imports.

AssociateViewSet maps GET on every route and defines delete(), and the GET
mapping adds HEAD: Allow is GET, HEAD, DELETE, OPTIONS everywhere.

Placeholders: {'$age': 'YYYY-MM-DD'} is Associate.calculate_age() for that
born date on today's UTC date (int(days / 365.25)), {'$minor': ...} is that
age < 18. Unordered lists: the relations and M2M managers have no ordering, so
nested `tutors` lists (relation dicts by tutor id, serializer dicts by
associate id, pk lists) and `family_members` are compared sorted; the top-level
`data`/`rowIds` (and the all-tutors list) are sorted unless the case is ordered.
"""
from datetime import date, datetime, timezone
import json
from pathlib import Path

from TESTS.contracts import http_family as family
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/personas_reads_http.json'
ALLOWED = {'GET', 'HEAD', 'DELETE', 'OPTIONS'}


def load_source(path=SOURCE):
    return family.load_source(path)


def _age(born):
    today = datetime.now(timezone.utc).date()
    return int((today - date.fromisoformat(born)).days / 365.25)


def resolve(value):
    if isinstance(value, dict):
        if set(value) == {'$age'}:
            return _age(value['$age'])
        if set(value) == {'$minor'}:
            return _age(value['$minor']) < 18
        return {key: resolve(item) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item) for item in value]
    return value


def _key(item):
    if isinstance(item, dict):
        tutor = item.get('tutor')
        return str(tutor.get('associate_id') if isinstance(tutor, dict) else item.get('associate_id'))
    return str(item)


def normalize(value, *, top=True, ordered=False):
    """Sort the unordered lists; keep the top-level order of an ordered case."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item = normalize(item, top=False, ordered=ordered)
            if isinstance(item, list) and (key in ('tutors', 'family_members') or (top and key == 'data' and not ordered)):
                item = sorted(item, key=_key)
            elif top and key == 'meta' and isinstance(item, dict) and isinstance(item.get('rowIds'), list) and not ordered:
                item = {**item, 'rowIds': sorted(item['rowIds'], key=str)}
            result[key] = item
        return result
    if isinstance(value, list):
        items = [normalize(item, top=False, ordered=ordered) for item in value]
        return items if (ordered or not top) else sorted(items, key=_key)
    return value


def assert_case(case, base_url, *, token, sessions):
    """http_family.assert_case's checks, comparing the normalized bodies."""
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
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == ALLOWED, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = normalize(resolve(case['response']), ordered=case['ordered'])
    actual = normalize(actual, ordered=case['ordered'])
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
