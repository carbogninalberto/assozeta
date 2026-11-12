"""Independently authored course-subscriptions list, update, delete and bulk-delete HTTP expectations; no Django imports.

The shared http_family checks apply to every case. In addition:
- nested lists the serializer renders without an ordering (rows, installments,
  membership payments, carnet holdings, attachments) are compared sorted by
  their identity field;
- medical labels count days from the request date: '{since:D}' and
  '{until:D}' are the days between D and today in Europe/Rome,
  '{missing:T}' the days since the Rome date of T (at least 0) and
  '{toage:D}' int(6 * 365.25 - days since D) in UTC (Django's TIME_ZONE);
- '$default_category' / '$default_category_name' are the first 'entrate e
  proventi da attività tipiche' category by key, bound by the runner.
"""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from TESTS.http_client import Client
from TESTS.contracts import http_family as family
from TESTS.contracts import pdf, xlsx
from TESTS.contracts.http_family import require  # noqa: F401  (runner interface)
from TESTS.contracts.test_email_logs_http import SERVER_ERROR_DOCUMENT, ResponseFailure, difference, substitute

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/course_subscriptions_http.json'
# The viewset's own delete() answers DELETE on every mapped route; the GET route also allows HEAD.
ALLOWED = {'list': {'GET', 'POST', 'HEAD', 'DELETE', 'OPTIONS'}, 'update': {'PATCH', 'DELETE', 'OPTIONS'},
           'delete': {'DELETE', 'OPTIONS'}, 'bulk-delete': {'DELETE', 'OPTIONS'}}
UNORDERED = {'data': ('course_subscription_id',), 'installments': ('id', 'course_subscription_installment_id'),
             'membership_payments': ('payment_id',), 'carnets': ('carnet_subscription_id',), 'attachments': ('document_id',)}
UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z')
# '@name' in an expected body binds the created row's key at that position (kept per case for the runner).
BINDINGS = {}
ROME = ZoneInfo('Europe/Rome')
TOKEN = re.compile(r'\{(since|until|missing|toage):([^}]+)\}')


def load_source(path=SOURCE):
    return family.load_source(path)


def normalized(value):
    """value with the unordered lists sorted by their identity field."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            item = normalized(item)
            if key in UNORDERED and isinstance(item, list) and all(isinstance(entry, dict) for entry in item):
                item = sorted(item, key=lambda entry: tuple(str(entry.get(name)) for name in UNORDERED[key]))
            result[key] = item
        return result
    if isinstance(value, list):
        return [normalized(item) for item in value]
    return value


def days_text(days):
    return f"{days} {'giorno' if days == 1 else 'giorni'}"


def materialized(value, bindings, now):
    """Fill the request-time day counts and the runner bindings."""
    if isinstance(value, dict):
        return {key: materialized(item, bindings, now) for key, item in value.items()}
    if isinstance(value, list):
        return [materialized(item, bindings, now) for item in value]
    if not isinstance(value, str):
        return value
    if value in bindings:
        return bindings[value]
    rome, utc = now.astimezone(ROME).date(), now.astimezone(timezone.utc).date()

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


def bind_symbols(expected, actual, binds):
    """Bind each '@name' of expected to the UUID at the same position of actual."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key, item in expected.items():
            if key in actual:
                bind_symbols(item, actual[key], binds)
    elif isinstance(expected, list) and isinstance(actual, list) and len(expected) == len(actual):
        for left, right in zip(expected, actual):
            bind_symbols(left, right, binds)
    elif isinstance(expected, str) and expected.startswith('@') and isinstance(actual, str) and UUID.match(actual):
        binds.setdefault(expected, actual)


def today(value, day):
    if isinstance(value, dict):
        return {key: today(item, day) for key, item in value.items()}
    if isinstance(value, list):
        return [today(item, day) for item in value]
    if isinstance(value, str):
        return value.replace('$today', day.strftime('%d/%m/%Y'))
    return value


def assert_case(case, base_url, *, token, sessions):
    headers = {'Accept-Language': 'en', 'Accept': 'application/json', 'X-Correlation-ID': case['name'],
               **substitute(case['headers'], sessions)}
    body = case.get('body')
    if 'raw_body' in case:
        body = case['raw_body'].encode()
        headers['Content-Type'] = case['content_type']
    before, started = datetime.now(timezone.utc).date(), datetime.now(timezone.utc)
    status, raw, received = Client(base_url, token).request(case['method'], case['path'], body, headers, raw=True, include_headers=True)
    finished, after = datetime.now(timezone.utc), datetime.now(timezone.utc).date()

    def check(condition, code):
        if not condition:
            raise ResponseFailure(code, status)

    check(type(status) is int and status == case['status'], 'response.status')
    if case['response'] == '<stable-server-error-page>':
        check(raw == SERVER_ERROR_DOCUMENT, 'response.error-document')
        check(received.get('Content-Type', '').split(';')[0] == 'text/html', 'response.error-type')
        return status
    allowed = ALLOWED[case['path'].split('?')[0].rstrip('/').split('/')[-1]]
    check(set(received.get('Allow', '').replace(' ', '').split(',')) == allowed, 'response.allow')
    check(received.get('Content-Language') == 'en' and received.get('Set-Cookie') is None, 'response.headers')
    check(received.get('WWW-Authenticate') == ('Bearer realm="api"' if status == 401 else None), 'response.authentication')
    if case['response'] == '<empty>':
        check(raw == b'', 'response.empty')
        return status
    try:
        actual = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise ResponseFailure('response.json', status) from None
    bindings = {key: value for key, value in sessions.items() if key.startswith('$')}
    expected = normalized(materialized(case['response'], bindings, datetime.now(timezone.utc)))
    actual = normalized(actual)
    binds = BINDINGS[case['name']] = {}
    bind_symbols(expected, actual, binds)
    expected = normalized(materialized(expected, binds, datetime.now(timezone.utc)))
    # '$today' is the %d/%m/%Y date of date.today() (UTC) before or after the request; '{"$xlsx": spec}' is a workbook
    # checked part by part with the opt-in XLSX standard of TESTS/contracts/xlsx.py within the request window.
    reasons = []
    for day in dict.fromkeys((before, after)):
        dated, reason = xlsx.resolve(today(expected, day), actual, started, finished)
        if reason is None:
            dated, reason = pdf.resolve(dated, actual, started, finished)
        if reason is None and actual == dated:
            break
        reasons.append(f'response.file: {reason}'[:600] if reason else 'response.body' + difference(dated, actual))
    else:
        check(False, reasons[-1])
    check(received.get('Content-Type', '').split(';')[0] == 'application/json', 'response.type')
    return status
