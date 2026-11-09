"""Parent-only google/* HTTP runner; imports are inert.

service_context owns a private Google stub for the whole run: a local HTTP
server that records every request and answers with the replies authored on
the current case (nothing else is contacted). Before either server starts it
writes a client secrets file with placeholder values (the real Google
endpoints) and points GOOGLE_CLIENT_SECRET_FILE / GOOGLE_CALENDAR_REDIRECT_URL
at it for both servers (core.settings attributes for Django, the environment
for Go). Requests addressed to https://oauth2.googleapis.com and
https://www.googleapis.com are redirected to the stub at the transport layer:
in this process by wrapping requests' HTTPAdapter.send and httplib2's
Http.request (the libraries otherwise run unchanged), in Go through
GOOGLE_OAUTH2_BASE / GOOGLE_API_BASE. Each case's outbound requests must be
exactly the authored ones: method, path and query, the ordered header list
(the Host value is the stub's) and the body.

seed_rows sets the base users' Google fields (audit disabled) and inserts the
courses with their attendance registries. Each case is checked against the
complete model state: only the authored users' integration_google_state /
_credentials / _auto_sync and the authored courses' google_calendar_id
change, and the new auditlog entries match the authored ones in order, with
their index rows. A '$state' is the state the contract saw in the answer;
credentials JSON must be the authored to_json() text key by key, with an
expiry of the authored kind inside the request window.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
import types

from TESTS.contracts import test_google_calendar_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/google_calendar_http.json', 'TESTS/contracts/test_google_calendar_http.py',
           'TESTS/adapters/django/google_calendar_http.py', 'TESTS/adapters/go/payment_category_server.py',
           'BE/application/views/google_views.py', 'BE/application/models/user_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/attendee_models.py', 'BE/application/mixin.py', 'BE/application/impersonation.py',
           'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py',
           'BE/core/settings.py', 'BE/core/middleware.py', 'BE/core/authentication.py', 'BE/requirements.txt') + harness.SOURCES
USER, COURSE = 'application.User', 'application.Course'
MANAGED = (USER, COURSE, harness.AUDIT, harness.INDEX)
GOOGLE_HOSTS = ('https://oauth2.googleapis.com', 'https://www.googleapis.com')
STUB = {}
FIELDS = {'state': 'integration_google_state', 'credentials': 'integration_google_credentials', 'auto_sync': 'integration_google_auto_sync'}


# ---------------------------------------------------------------------------
# Google stub
# ---------------------------------------------------------------------------

class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _answer(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'method': self.command, 'path': self.path, 'headers': list(self.headers.items()), 'body': body,
                                'at': time.time()})
            number = len(STUB['log'])
            script = STUB['script']
            reply = script[number - 1]['reply'] if number <= len(script) else None
        if reply is None:
            reply = {'status': 500, 'json': {'error': {'code': 500, 'message': 'unexpected request'}}}
        status = reply['status']
        if 'text' in reply:
            data = reply['text'].encode()
        elif status == 204:
            data = b''
        else:
            data = json.dumps(reply['json']).encode()
        self.send_response(status, reply.get('reason'))
        if status != 204:
            self.send_header('Content-Type', 'text/html' if 'text' in reply else 'application/json; charset=UTF-8')
            self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if status != 204:
            self.wfile.write(data)

    do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = _answer


def _rewrite(url):
    for host in GOOGLE_HOSTS:
        if isinstance(url, str) and url.startswith(host + '/'):
            return STUB['base'] + url[len(host):]
    return url


@contextmanager
def service_context(report, log):
    import httplib2
    import requests.adapters
    import core.settings as django_settings
    server = ThreadingHTTPServer(('127.0.0.1', 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    base = f'http://127.0.0.1:{server.server_address[1]}'
    STUB.update(lock=threading.Lock(), log=[], script=[], base=base)
    private = Path(tempfile.mkdtemp(prefix='google-calendar-'))
    secrets = private / 'client_secret.json'
    source = base_contract.load_source()
    secrets.write_text(json.dumps(source['client']))
    environment = {'GOOGLE_CLIENT_SECRET_FILE': str(secrets), 'GOOGLE_CALENDAR_REDIRECT_URL': source['redirect'],
                   'GOOGLE_OAUTH2_BASE': base, 'GOOGLE_API_BASE': base}
    saved_env = {name: os.environ.get(name) for name in environment}
    saved_settings = (django_settings.GOOGLE_CALENDAR_CLIENT_SECRET_FILE, django_settings.GOOGLE_CALENDAR_REDIRECT_URL)
    send, http_request = requests.adapters.HTTPAdapter.send, httplib2.Http.request

    def redirected_send(self, request, *args, **kwargs):
        request.url = _rewrite(request.url)
        return send(self, request, *args, **kwargs)

    def redirected_request(self, uri, *args, **kwargs):
        return http_request(self, _rewrite(uri), *args, **kwargs)

    report['service_cleanup']['google_stub'] = 'running'
    thread.start()
    try:
        os.environ.update(environment)
        django_settings.GOOGLE_CALENDAR_CLIENT_SECRET_FILE = str(secrets)
        django_settings.GOOGLE_CALENDAR_REDIRECT_URL = source['redirect']
        requests.adapters.HTTPAdapter.send = redirected_send
        httplib2.Http.request = redirected_request
        yield
    finally:
        requests.adapters.HTTPAdapter.send, httplib2.Http.request = send, http_request
        django_settings.GOOGLE_CALENDAR_CLIENT_SECRET_FILE, django_settings.GOOGLE_CALENDAR_REDIRECT_URL = saved_settings
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        shutil.rmtree(private, ignore_errors=True)
        report['service_cleanup']['google_stub'] = 'stopped' if not thread.is_alive() else 'cleanup_failed'
        STUB.clear()


def _assert_case(case, base_url, *, token, sessions):
    from django.conf import settings
    case = {**case, 'secure_cookie': settings.SESSION_COOKIE_SECURE}
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = case['google']
    STUB['window'] = [time.time(), None]
    try:
        return base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    finally:
        STUB['window'][1] = time.time()


# The harness drives this proxy: the stub is scripted with the case's replies before each request.
contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


def check_google(case):
    require = contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    expected = case['google']
    require(len(log) == len(expected), 'google.request-count')
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        spec = spec['request']
        require(sent['method'] == spec['method'] and sent['path'] == spec['path'], 'google.request-line' + where)
        headers = sent['headers']
        require(headers and headers[0][0] == 'Host', 'google.host' + where)
        require([list(item) for item in headers[1:]] == spec['headers'], 'google.headers' + where)
        require(sent['body'] == spec['body'].encode(), 'google.body' + where)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def seed_rows(source, created):
    from application.models import AttendanceRegistry, Course, User
    ids = source['ids']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for label, row in source['seed_users'].items():
        User._base_manager.filter(pk=ids[label]).update(integration_google_state=row['state'],
                                                        integration_google_credentials=json.dumps(row['credentials']),
                                                        integration_google_auto_sync=row['auto_sync'])
    for label, row in source['courses'].items():
        insert(Course, Course(course_id=row['id'], sport_association_id=row['association'], title=row['title'], description='',
                              fee=Decimal('10.00'), status_flag=Course.ACTIVE, deleted=row['deleted'],
                              google_background_color=row['color'], google_calendar_id=row['calendar'],
                              group_id=ids[row['group']] if row['group'] else None))
        if row['registry'] is not None:
            insert(AttendanceRegistry, AttendanceRegistry(attendance_registry_id=row['registry_id'], course_id=row['id'],
                                                          events=row['registry'], status=AttendanceRegistry.PUBLISHED))
    insert(AttendanceRegistry, AttendanceRegistry(attendance_registry_id=source['alpha_later_registry'],
                                                  course_id=source['courses']['alpha']['id'], events=[], status=AttendanceRegistry.DRAFT))


base_fixture_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        fixture['google_raw'] = raw
        yield fixture


SESSION_PREFIX = b':1:django.contrib.sessions.cache'


def check_session(case, context):
    """The cache session backend's cache.add of {'state': state} (django_redis pickle, the default protocol: 4) for
    SESSION_COOKIE_AGE; removed afterwards so the cache baseline holds."""
    import pickle
    raw = context['fixture']['google_raw']
    key = SESSION_PREFIX + base_contract.SESSIONS.pop(case['name']).encode()
    stored, ttl = raw.get(key), raw.ttl(key)
    raw.delete(key)
    contract.require(stored == pickle.dumps({'state': base_contract.STATES[case['state_for']]}, pickle.DEFAULT_PROTOCOL), 'session.value')
    contract.require(1209600 - 120 <= ttl <= 1209600, 'session.ttl')


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def value(item):
    if isinstance(item, dict) and set(item) == {'type', 'value'}:
        return item['value']
    return item


def flat(row):
    return {key: value(item) for key, item in row.items()}


def expiry_ok(text, kind, window):
    """'$callback:N': utcfromtimestamp(round(time.time()) + N) (seconds only);
    '$refresh:N': utcnow() + timedelta(seconds=N) (isoformat, microseconds when non-zero)."""
    name, _, amount = kind.partition(':')
    if not isinstance(text, str) or not text.endswith('Z'):
        return False
    body = text[:-1]
    start, end = window
    if name == '$callback':
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', body):
            return False
        moment = datetime.fromisoformat(body).replace(tzinfo=timezone.utc).timestamp()
        return round(start) + int(amount) <= moment <= round(end) + int(amount)
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?', body):
        return False
    moment = datetime.fromisoformat(body).replace(tzinfo=timezone.utc).timestamp()
    offset = float(amount)
    return start + offset - 0.001 <= moment <= end + offset + 0.001 and not body.endswith('.000000')


def credentials_ok(stored, spec, window):
    if not isinstance(stored, str):
        return False
    try:
        data = json.loads(stored)
    except ValueError:
        return False
    template = spec['$json']
    if not isinstance(data, dict) or list(data) != list(template) or json.dumps(data) != stored:
        return False
    for key, expected in template.items():
        if key == 'expiry' and isinstance(expected, str) and expected.startswith('$'):
            if not expiry_ok(data[key], expected, window):
                return False
        elif data[key] != expected:
            return False
    return True


def audit_value(field, item):
    if field == 'credentials':
        return 'null' if item is None else json.dumps(item)
    if field == 'auto_sync':
        return str(bool(item))
    return 'None' if item is None else str(item)


def assert_effect(case, before, after, source, context):
    require = contract.require
    check_google(case)
    if case.get('session'):
        check_session(case, context)
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    ids, window = source['ids'], STUB['window']
    labels = {v: k for k, v in ids.items()}

    # Users: only the authored Google fields change.
    old_users, new_users = keyed(before[USER], 'user_id'), keyed(after[USER], 'user_id')
    require(set(old_users) == set(new_users), 'state.user-keys')
    for key, old in old_users.items():
        new, old = flat(new_users[key]), flat(old)
        spec = case['users'].get(labels.get(str(key)), {})
        expected = dict(old)
        for name, item in spec.items():
            column = FIELDS[name]
            if item == '$state':
                expected[column] = base_contract.STATES.get(labels[str(key)])
            elif isinstance(item, dict) and '$json' in item:
                require(credentials_ok(new[column], item, window), 'state.credentials')
                expected[column] = new[column]
            else:
                expected[column] = item
        require(new == expected, 'state.users')

    # Courses: only the authored google_calendar_id values change.
    old_courses, new_courses = keyed(before[COURSE], 'course_id'), keyed(after[COURSE], 'course_id')
    require(set(old_courses) == set(new_courses), 'state.course-keys')
    by_id = {row['id']: label for label, row in source['courses'].items()}
    for key, old in old_courses.items():
        expected = flat(old)
        label = by_id.get(str(key))
        if label in case['courses']:
            expected['google_calendar_id'] = case['courses'][label]
        require(flat(new_courses[key]) == expected, 'state.courses')

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: row['id'])
    require(len(added) == len(case['audit']), 'audit.count')
    types_ = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before[USER], 'user_id')
    session = case['headers'].get('X-Impersonation-Id', '')
    # Several saves of one user in one request: each entry diffs against the previous save;
    # an 'intermediate' spec is the credentials JSON that save wrote (refreshed before the final save).
    running = {}
    for event, spec in zip(added, case['audit']):
        if spec['model'] == 'user':
            key = ids[spec['label']]
            old = running.get(key) or flat(old_users[key])
            new = flat(new_users[key])
            if spec.get('intermediate'):
                column = FIELDS['credentials']
                reported = (event['changes'].get(column) or [None, None])[1]
                try:
                    decoded = json.loads(reported)
                except (TypeError, ValueError):
                    decoded = None
                require(credentials_ok(decoded, spec['intermediate'], window), 'audit.intermediate')
                new = {**old, column: decoded}
            running[key] = new
            changes = {FIELDS[name]: [audit_value(name, old[FIELDS[name]]), audit_value(name, new[FIELDS[name]])]
                       for name in ('state', 'credentials', 'auto_sync') if FIELDS[name] in spec['fields']}
            require(event['object_repr'] == f"name: {users[key]['first_name']} {users[key]['last_name']}", 'audit.repr')
        else:
            key = source['courses'][spec['label']]['id']
            changes = spec['changes']
            require(event['object_repr'] == f'Course object ({key})', 'audit.repr')
        require(event['content_type_id'] == types_[spec['model']] and event['action'] == 1 and event['object_pk'] == key, 'audit.identity')
        require(event['changes'] == changes, 'audit.changes')
        actor = None if spec['actor'] == 'anonymous' else ids[spec['actor']]
        require(event['actor_id'] == actor and event['actor_email'] == (users[actor]['email'] if actor else None), 'audit.actor')
        additional = None
        if session.startswith('$session:'):
            label = session[9:]
            additional = {'impersonation_session': context['fixture']['sessions'][label], 'actor_id': actor,
                          'target_user_id': case['headers']['User-Id'],
                          'association_id': None if label.startswith('admin_') else ids['own_association']}
        require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        index = indexes.get(str(event['id']))
        if spec['index'] is None:
            require(index is None, 'index.absent')
        else:
            require(index is not None and index['sport_association_id'] == spec['index'][0]
                    and index['resolution_path'] == spec['index'][1], 'index.fields')


run_workload = harness.run_workload_for(contract, assert_effect)
