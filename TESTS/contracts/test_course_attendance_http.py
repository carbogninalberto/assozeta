"""Independently authored course attendance HTTP expectations; no Django imports.

The shared http_family checks apply to every case. In addition:
- '{today}' is the request's Europe/Rome date; the medical label tokens are
  those of test_course_subscriptions_http ('{missing:T}', '{until:D}', ...);
- lists the views build from unordered querysets are compared sorted by their
  identity field: potential_attendees (course_subscription_id), the
  subscription history's courses (course_id) and the check-in choices
  (attendance_day_id).
"""
from datetime import datetime, timezone
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from TESTS.contracts import http_family as family
from TESTS.contracts import test_course_subscriptions_http as labels
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute
from TESTS.http_client import Client

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_attendance_http.json'
ROME = ZoneInfo('Europe/Rome')
UNORDERED = {'potential_attendees': 'course_subscription_id', 'courses': 'course_id'}


def load_source(path=SOURCE):
    return family.load_source(path)


def today():
    return datetime.now(timezone.utc).astimezone(ROME).date().isoformat()


def with_today(value, day):
    """value with '{today}' replaced in every string."""
    if isinstance(value, dict):
        return {key: with_today(item, day) for key, item in value.items()}
    if isinstance(value, list):
        return [with_today(item, day) for item in value]
    return value.replace('{today}', day) if isinstance(value, str) else value


def normalized(value, top=True):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item = normalized(item, False)
            field = UNORDERED.get(key) or ('attendance_day_id' if top and key == 'data' and 'msg' in value else None)
            if field and isinstance(item, list) and all(isinstance(entry, dict) for entry in item):
                item = sorted(item, key=lambda entry: str(entry.get(field)))
            result[key] = item
        return result
    if isinstance(value, list):
        return [normalized(item, False) for item in value]
    return value


def allowed(case):
    path = case['path'].split('?')[0].rstrip('/')
    return {'POST', 'OPTIONS'} if path.endswith(('/update', '/mark')) else {'GET', 'OPTIONS'}


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
    now = datetime.now(timezone.utc)
    expected = normalized(labels.materialized(with_today(case['response'], today()), {}, now))
    actual = normalized(actual)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
