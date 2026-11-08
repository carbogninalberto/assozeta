"""Independently authored subscription list, list/all, list/archived, info and calendar HTTP expectations; no Django imports.

The routes are function views: Allow is GET, POST, OPTIONS on subscription/list
and GET, OPTIONS elsewhere. The checks are http_family.assert_case's, plus:
- subscription_list_archived answers athletes with a bare Response(status=401):
  cases with 'challenge': false expect no WWW-Authenticate header;
- request-time values are tokens: '{age:D}' is int(days since D / 365.25) and
  '{minor:D}' that age below 18 (both on the UTC date, Django's TIME_ZONE);
  '{since:D}', '{until:D}' and '{missing:T}' are day counts against today in
  Europe/Rome and '{toage:D}' int(6 * 365.25 - days since D) in UTC, each
  rendered as 'N giorno/giorni';
- lists the serializers render without an ordering (tags, the info tags,
  subscription files and installments, calendar events) are compared sorted; 'unordered' cases
  (an ordering Django leaves to the database) compare the data rows, keyed
  by index or listed, as multisets.
"""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from TESTS.contracts import http_family as family
from TESTS.http_client import Client
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, require, substitute

__all__ = ('require', 'load_source', 'assert_case', 'ResponseFailure')

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/subscription_reads_http.json'
ROME = ZoneInfo('Europe/Rome')
TOKEN = re.compile(r'\{(since|until|missing|toage):([^}]+)\}')
WHOLE = re.compile(r'\{(age|minor):(\d{4}-\d{2}-\d{2})\}\Z')
SORTED = {'tags': 'tag_id', 'subscription_files': 'subscription_file_id', 'installments': 'course_subscription_installment_id'}


def load_source(path=SOURCE):
    return family.load_source(path)


def allowed(case):
    path = case['path'].split('?')[0]
    return {'GET', 'POST', 'OPTIONS'} if path == '/subscription/list' else {'GET', 'OPTIONS'}


def days_text(days):
    return f"{days} {'giorno' if days == 1 else 'giorni'}"


def materialized(value, now):
    if isinstance(value, dict):
        return {key: materialized(item, now) for key, item in value.items()}
    if isinstance(value, list):
        return [materialized(item, now) for item in value]
    if not isinstance(value, str):
        return value
    rome, utc = now.astimezone(ROME).date(), now.astimezone(timezone.utc).date()
    whole = WHOLE.match(value)
    if whole:
        kind, text = whole.groups()
        age = int((utc - date.fromisoformat(text)).days / 365.25)
        return age if kind == 'age' else age < 18

    def fill(match):
        kind, text = match.groups()
        if kind == 'since':
            return days_text((rome - date.fromisoformat(text)).days)
        if kind == 'until':
            return days_text((date.fromisoformat(text) - rome).days)
        if kind == 'missing':
            created = datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(ROME).date()
            return days_text(max((rome - created).days, 0))
        return days_text(int(6 * 365.25 - (utc - date.fromisoformat(text)).days))
    return TOKEN.sub(fill, value)


def _key(item):
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def normalized(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item = normalized(item)
            if isinstance(item, list):
                if key in SORTED and all(isinstance(entry, dict) for entry in item):
                    item = sorted(item, key=lambda entry: str(entry.get(SORTED[key])))
                elif key in ('tags', 'events', 'family_members'):
                    item = sorted(item, key=_key)
            result[key] = item
        return result
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def unordered(value):
    if not isinstance(value, dict):
        return value
    data = value.get('data')
    if isinstance(data, dict) and all(re.fullmatch(r'\d+', key) for key in data):
        return {**value, 'data': sorted((_key(item) for item in data.values()))}
    if isinstance(data, list):
        return {**value, 'data': sorted(_key(item) for item in data)}
    return value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], case.get('body'), headers, raw=True,
                                                            include_headers=True)

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
    challenge = case.get('challenge', status == 401)
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if challenge else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    expected = normalized(materialized(case['response'], datetime.now(timezone.utc)))
    actual = normalized(actual)
    if case['unordered']:
        expected, actual = unordered(expected), unordered(actual)
    check(actual == expected, 'response.body' + difference(expected, actual))
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status

