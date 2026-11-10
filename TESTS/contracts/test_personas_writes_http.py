"""Independently authored personas writes HTTP expectations; no Django imports.

Each case names its exact Allow set (AssociateViewSet and
AssociateSubscriptionViewSet define delete(), so DELETE is allowed on every
route; GET mappings add HEAD).

Placeholders in the authored bodies: '$aN' is an associate created by the case
(any UUID, used consistently within the response), '$nowN' its creation time
(a UTC 'Z' timestamp), {'$age': 'YYYY-MM-DD'} is Associate.calculate_age() on
today's UTC date and {'$minor': ...} that age < 18. The relation and M2M
managers have no ordering: nested `tutors` lists are compared sorted by the
tutor's names, `family_members` and pk lists sorted. '$picN' is the URL of a
picture the case uploads (the fixture's picture_base, AWS_LOCATION,
associates/<folder>/picture_<timestamp>.jpg); the bound values of the last
case stay in BOUND for the adapter, which ties them to the stored object.
"""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
import uuid

from TESTS.contracts import http_family as family
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/personas_writes_http.json'
PICTURE = re.compile(r'https://bakney-object-spaces\.fra1\.cdn\.digitaloceanspaces\.com/[^/]+/associates/[^/]+/picture_\d+\.\d+\.jpg')
BOUND = {}


def load_source(path=SOURCE):
    return family.load_source(path)


def _age(born):
    today = datetime.now(timezone.utc).date()
    return int((today - date.fromisoformat(born)).days / 365.25)


def _key(item):
    if isinstance(item, dict):
        person = item.get('tutor') if isinstance(item.get('tutor'), dict) else item
        return (str(person.get('first_name')), str(person.get('last_name')), str(person.get('tax_code')))
    return ('', '', str(item))


def normalize(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item = normalize(item)
            if isinstance(item, list) and key in ('tutors', 'family_members', 'tags'):
                item = sorted(item, key=_key)
            result[key] = item
        return result
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def bind(expected, actual, bindings, check):
    """Resolve placeholders of expected against actual (same shape)."""
    if isinstance(expected, dict):
        if set(expected) == {'$age'}:
            return _age(expected['$age'])
        if set(expected) == {'$minor'}:
            return _age(expected['$minor']) < 18
        return {key: bind(item, actual.get(key) if isinstance(actual, dict) else None, bindings, check) for key, item in expected.items()}
    if isinstance(expected, list):
        items = actual if isinstance(actual, list) and len(actual) == len(expected) else [None] * len(expected)
        return [bind(item, other, bindings, check) for item, other in zip(expected, items)]
    if isinstance(expected, str) and expected.startswith('$a'):
        if expected not in bindings:
            check(isinstance(actual, str), 'response.new-associate')
            uuid.UUID(actual)
            check(actual not in bindings.values(), 'response.new-associate-distinct')
            bindings[expected] = actual
        return bindings[expected]
    if isinstance(expected, str) and expected.startswith('$pic'):
        if expected not in bindings:
            check(isinstance(actual, str) and PICTURE.fullmatch(actual) is not None, 'response.picture-url')
            bindings[expected] = actual
        return bindings[expected]
    if isinstance(expected, str) and expected.startswith('$now'):
        if expected not in bindings:
            check(isinstance(actual, str) and actual.endswith('Z'), 'response.new-creation-date')
            bindings[expected] = actual
        return bindings[expected]
    return expected


def _resolve_only(value):
    """Ages without placeholders bound (for sorting before binding)."""
    if isinstance(value, dict):
        if set(value) <= {'$age', '$minor'} and value:
            return bind(value, None, {}, lambda *a: None)
        return {key: _resolve_only(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve_only(item) for item in value]
    return value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    BOUND.clear()
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == set(case['allow']), 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    actual = normalize(actual)
    bindings = {}
    expected = bind(normalize(_resolve_only(case['response'])), actual, bindings, check)
    BOUND.update({key: value for key, value in bindings.items() if key.startswith('$pic')})
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
