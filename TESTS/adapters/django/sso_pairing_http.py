"""Parent-only Bakney pairing/SSO HTTP runner; imports are inert.

service_context owns a private Bakney authority stub for the whole run: a
local HTTP server that records every request and answers with the replies
authored on the current case. Before either server starts, Django's settings
are overridden (and the Go server's environment set) to the same values:
DEBUG with ASSOZETA_DEPLOYMENT_MODE=development (so HTTP origins and the stub's
HTTP API base are accepted), APP_URL http://localhost, the stub as
BAKNEY_SSO_API_BASE and a placeholder UI origin. Requests carry Host:
localhost (allowed), on every transport.

seed_rows inserts the self-hosted InstanceConfiguration (primary association:
the fixture's own association) and the extra users with their links. Before
each case the authored SSO state is applied (configuration flags, pairing,
logins, challenges, nonces, revocations; the pairing secret is a per-run
random value encrypted like Django stores it) and, for throttle cases, the
DRF throttle history is preloaded through Django's cache. Each case is checked
against the complete model state: only the five SSO models (and last_login of
the authored user) may change, exactly as authored. The outbound authority
requests must be exactly the authored ones (path, JSON bytes, the requests
library's headers), and the DRF throttle entry must hold one recent timestamp
(Django's pickled list) after every request that reached the throttle.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import hashlib
import json
import os
import re
import secrets
import threading
import time
import types

from TESTS.contracts import test_sso_pairing_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import carnet_list_http as carnets
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django.workflow_writes_http import scalar

SOURCES = ('TESTS/fixtures/sso_pairing_http.json', 'TESTS/contracts/test_sso_pairing_http.py', 'TESTS/adapters/django/sso_pairing_http.py',
           'TESTS/adapters/go/payment_category_server.py', 'BE/instance/sso/views.py', 'BE/instance/sso/service.py',
           'BE/instance/sso/protocol.py', 'BE/instance/sso/models.py', 'BE/instance/permissions.py', 'BE/instance/urls.py',
           'BE/core/urls.py', 'BE/core/settings.py', 'BE/application/services/jwt_token_service.py',
           'BE/application/models/user_models.py') + harness.SOURCES
MODELS = ('instance.BakneyPairing', 'instance.BakneyLogin', 'instance.BakneyChallenge', 'instance.BakneyNonce',
          'instance.BakneyRevocation')
USER = 'application.User'
UPDATED = datetime(2026, 1, 1, tzinfo=timezone.utc)
CREATED = datetime(2026, 1, 2, tzinfo=timezone.utc)
GARBAGE = 'gAAAAA-not-a-fernet-token'
REQUESTS_HEADERS = {'user-agent': 'python-requests/2.34.2', 'accept-encoding': 'gzip, deflate', 'accept': 'application/json',
                    'connection': 'keep-alive', 'content-type': 'application/json'}
STUB = {}
RUNTIME = base_contract.RUNTIME


# ---------------------------------------------------------------------------
# Authority stub
# ---------------------------------------------------------------------------

def _values(value, now):
    if isinstance(value, dict):
        return {key: _values(item, now) for key, item in value.items()}
    if isinstance(value, list):
        return [_values(item, now) for item in value]
    if isinstance(value, str) and value.startswith('$now'):
        return now + int(value[4:] or 0)
    if value == '$secret':
        return RUNTIME['secret']
    return value


class _StubServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # A client closing its idle keep-alive connection is not a failure.


class _StubHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def _answer(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'method': self.command, 'path': self.path, 'headers': [(k.lower(), v) for k, v in self.headers.items()],
                                'body': body})
            number = len(STUB['log'])
            script = STUB['script']
            reply = script[number - 1]['reply'] if number <= len(script) else None
        if reply is None:
            status, data = 500, b'{"error": "unexpected request"}'
        elif 'raw' in reply:
            status, data = reply['status'], reply['raw'].encode()
        else:
            status, data = reply['status'], json.dumps(_values(reply['json'], int(time.time()))).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _answer


@contextmanager
def service_context(report, log):
    from django.conf import settings
    from django.test.utils import override_settings
    source = base_contract.load_source()
    server = _StubServer(('127.0.0.1', 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    STUB.update(lock=threading.Lock(), log=[], script=[])
    authority = f'http://127.0.0.1:{server.server_address[1]}' + source['settings']['api_path']
    values = {'DEBUG': True, 'ASSOZETA_DEPLOYMENT_MODE': 'development', 'APP_URL': source['settings']['app_url'],
              'BAKNEY_SSO_API_BASE': authority, 'BAKNEY_SSO_UI_ORIGIN': source['settings']['ui_origin']}
    # The Go server reads the same values from its environment; the global flags
    # DEBUG would change there are pinned to Django's effective settings.
    environment = {'DEBUG': 'True', 'ASSOZETA_DEPLOYMENT_MODE': 'development', 'APP_URL': values['APP_URL'],
                   'BAKNEY_SSO_API_BASE': authority, 'BAKNEY_SSO_UI_ORIGIN': values['BAKNEY_SSO_UI_ORIGIN'],
                   'CORS_ORIGIN_ALLOW_ALL': str(settings.CORS_ORIGIN_ALLOW_ALL),
                   'CORS_ALLOWED_ORIGINS': ','.join(settings.CORS_ALLOWED_ORIGINS),
                   'CSRF_COOKIE_SECURE': str(settings.CSRF_COOKIE_SECURE)}
    saved = os.environ.get('GO_FIXTURE_ENVIRONMENT')
    override = override_settings(**values)
    report['service_cleanup']['bakney_stub'] = 'running'
    thread.start()
    try:
        override.enable()
        os.environ['GO_FIXTURE_ENVIRONMENT'] = json.dumps(environment)
        RUNTIME.update(source=source, authority=authority, secret=secrets.token_urlsafe(32))
        yield
    finally:
        override.disable()
        if saved is None:
            os.environ.pop('GO_FIXTURE_ENVIRONMENT', None)
        else:
            os.environ['GO_FIXTURE_ENVIRONMENT'] = saved
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        report['service_cleanup']['bakney_stub'] = 'stopped' if not thread.is_alive() else 'cleanup_failed'
        STUB.clear()
        RUNTIME.clear()


def check_authority(case):
    require = base_contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    expected = case['authority']
    require(len(log) == len(expected), 'authority.request-count')
    port = RUNTIME['authority'].split('://', 1)[1].split('/', 1)[0]
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        body = json.dumps(_values(spec['body'], 0)).encode()
        require(sent['method'] == 'POST' and sent['path'] == RUNTIME['source']['settings']['api_path'] + spec['path'],
                'authority.request-line' + where)
        require(sent['body'] == body, 'authority.body' + where)
        names = [name for name, _ in sent['headers']]
        require(len(names) == len(set(names)), 'authority.duplicate-header' + where)
        require(dict(sent['headers']) == {**REQUESTS_HEADERS, 'host': port, 'content-length': str(len(body))},
                'authority.headers' + where)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def seed_rows(source, created):
    from application.models import Associate, Instructor, User
    from instance.models import InstanceConfiguration
    ids = source['ids']

    def insert(model, row):
        base_contract.require(row.pk is None or not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    base_contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    config = insert(InstanceConfiguration, InstanceConfiguration(domain='sso-pairing.example.test', name='SSO pairing fixture',
                                                                 self_hosted=True, primary_association_id=ids['own_association']))
    if config.pk is None:
        config = InstanceConfiguration._base_manager.get(domain='sso-pairing.example.test')
        created[-1] = (InstanceConfiguration, config.pk)
    for label, spec in source['users'].items():
        insert(User, User(user_id=spec['id'], username=f'sso-pairing-{label}', email=f'sso-pairing-{label}@example.test',
                          password='!synthetic', role=spec['role'], first_name=spec['first_name'], last_name='Fixture',
                          is_active=spec.get('is_active', True), is_staff=spec.get('is_staff', False)))
        link = source['links'][label]
        if spec['link'] in ('associate', 'deleted_associate'):
            insert(Associate, Associate(associate_id=link, user_id=spec['id'], sport_association_id=ids['own_association'],
                                        first_name=spec['first_name'] or 'Istruttore', last_name='Fixture',
                                        deleted=spec['link'] == 'deleted_associate'))
        elif spec['link'] == 'instructor':
            insert(Instructor, Instructor(instructor_id=link, user_id=ids['owner'], first_name='Istruttore', last_name='Fixture',
                                          associated_user_id=spec['id']))


def _models():
    from instance.models import BakneyChallenge, BakneyLogin, BakneyNonce, BakneyPairing, BakneyRevocation
    return BakneyPairing, BakneyLogin, BakneyChallenge, BakneyNonce, BakneyRevocation


base_fixture_context = harness.fixture_context_for(base_contract, seed_rows, purge=tuple((lambda index=index: _models()[index])
                                                                                        for index in range(5)))


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        from instance.models import InstanceConfiguration
        fixture['config_pk'] = InstanceConfiguration._base_manager.get(domain='sso-pairing.example.test').pk
        try:
            yield fixture
        finally:
            for model in reversed(_models()):
                model._base_manager.all().delete()


# ---------------------------------------------------------------------------
# Per-case state
# ---------------------------------------------------------------------------

def digest(value):
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def challenge_digest(source, spec):
    settings = source['settings']
    origin = settings['app_url']
    fields = [spec['pairing_id'], spec['remote_pairing_id'], source['ids']['own_association'], source['pairing_ids']['instance'],
              origin, origin + '/bakney/v1/callback', spec['generation']]
    return digest('\n'.join(fields))


def nonce_digest(spec):
    return digest(f"{spec['pairing_id']}:{spec['phase']}:{spec['nonce']}")


def stored_secret(value):
    from instance.sso.protocol import encrypt
    if value == '$secret':
        return encrypt(RUNTIME['secret'])
    if value == '$garbage':
        return GARBAGE
    return value


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def user_id(source, label):
    if label is None:
        return None
    return source['users'][label]['id'] if label in source['users'] else source['ids'][label]


def apply_setup(case, source, fixture):
    from instance.models import InstanceConfiguration
    from instance.sso.protocol import encrypt
    BakneyPairing, BakneyLogin, BakneyChallenge, BakneyNonce, BakneyRevocation = _models()
    spec = case['setup']
    now = datetime.now(timezone.utc)
    for model in reversed(_models()):
        model._base_manager.all().delete()
    config = spec['config']
    InstanceConfiguration._base_manager.filter(pk=fixture['config_pk']).update(
        self_hosted=config['self_hosted'], primary_association_id=source['ids']['own_association'] if config['primary'] else None)
    row = spec['pairing']
    if row is not None:
        pairing = BakneyPairing(config_id=fixture['config_pk'], instance_id=row['instance_id'], pairing_id=row['pairing_id'],
                                secret_encrypted=stored_secret(row['secret']),
                                authority=RUNTIME['authority'] if row['authority'] == '$authority' else row['authority'],
                                ui_origin=row['ui_origin'], remote_pairing_id=row['remote_pairing_id'],
                                remote_generation=row['remote_generation'], origin=row['origin'], association_id=row['association_id'],
                                association_name=row['association_name'], state=row['state'],
                                forwarding_enabled=row['forwarding_enabled'], checked_at=moment(row['checked_at']))
        BakneyPairing._base_manager.bulk_create([pairing])
        BakneyPairing._base_manager.update(updated_at=UPDATED)
    for item in spec['logins']:
        BakneyLogin._base_manager.create(
            digest=digest(item['cookie']), pairing_id=item['pairing_id'], remote_generation=item['remote_generation'],
            attempt_id=item['attempt_id'], state=item['state'],
            verifier_encrypted='' if not item['verifier'] else GARBAGE if item['verifier'] == '$garbage' else encrypt(item['verifier']),
            stage=item['stage'], user_id=user_id(source, item['user']), expires_at=now + timedelta(seconds=item['expires_in']))
    for item in spec['challenges']:
        BakneyChallenge._base_manager.create(digest=challenge_digest(source, item), pairing_id=item['pairing_id'],
                                             expires_at=now + timedelta(seconds=item['expires']))
    for item in spec['nonces']:
        BakneyNonce._base_manager.create(digest=nonce_digest(item), expires_at=now + timedelta(seconds=item['expires']))
    for item in spec['revocations']:
        BakneyRevocation._base_manager.create(pairing_id=item['pairing_id'], remote_pairing_id=item['remote_pairing_id'],
                                              authority=RUNTIME['authority'], secret_encrypted=stored_secret(item['secret']))
    BakneyRevocation._base_manager.update(created_at=CREATED)
    return now


def throttle_key(case):
    return f"throttle_bakney-sso:{case['view']}_{RUNTIME['ident']}"


def preload_throttle(case):
    from django.core.cache import cache
    spec = case.get('throttle')
    if spec:
        stamp = time.time() - spec['age']
        cache.set(throttle_key(case), [stamp] * spec['preload'], 60)


def check_throttle(case, status):
    from django.core.cache import cache
    require = base_contract.require
    key = throttle_key(case)
    history = cache.get(key)
    spec = case.get('throttle')
    denied = case['view'] == 'PairingAdminView' and status in (401, 403) and isinstance(case['response'], dict) \
        and 'detail' in case['response']
    if spec:
        require(isinstance(history, list) and len(history) == spec['preload'] and len(set(history)) == 1, 'throttle.unchanged')
    elif denied:
        require(history is None, 'throttle.not-reached')
    else:
        require(isinstance(history, list) and len(history) == 1 and type(history[0]) is float
                and 0 <= time.time() - history[0] < 30, 'throttle.recorded')
    cache.delete(key)


# ---------------------------------------------------------------------------
# Effects
# ---------------------------------------------------------------------------

def recent(when, seconds=60):
    return isinstance(when, datetime) and abs((datetime.now(timezone.utc) - when).total_seconds()) < seconds


def close(when, target, seconds=15):
    return isinstance(when, datetime) and abs((when - target).total_seconds()) < seconds


def decrypt(value):
    from instance.sso.protocol import SSOError, decrypt as django_decrypt
    try:
        return django_decrypt(value)
    except SSOError:
        return None


def check_pairing(case, source, fixture, setup_rows, captured, last):
    require = base_contract.require
    BakneyPairing = _models()[0]
    rows = list(BakneyPairing._base_manager.values())
    expected = case['effect']['pairing']
    if expected == 'unchanged':
        require(rows == setup_rows, 'pairing.unchanged')
        return
    if expected == 'created':
        require(not setup_rows and len(rows) == 1, 'pairing.created-count')
        row = rows[0]
        require(row['config_id'] == fixture['config_pk'] and row['secret_encrypted'] == '' and row['authority'] == ''
                and row['ui_origin'] == '' and row['remote_pairing_id'] is None and row['remote_generation'] is None
                and row['origin'] == '' and row['association_id'] is None and row['association_name'] == ''
                and row['state'] == 'disconnected' and row['forwarding_enabled'] is False and row['checked_at'] is None
                and recent(row['updated_at']) and row['instance_id'] != row['pairing_id'], 'pairing.created')
        if 'new:pairing_id' in captured:
            require(str(row['pairing_id']) == captured['new:pairing_id'] and str(row['instance_id']) == captured['new:instance_id'],
                    'pairing.created-response')
        return
    require(len(rows) == 1 and len(setup_rows) == 1, 'pairing.count')
    row, before = rows[0], setup_rows[0]
    require(row['id'] == before['id'] and row['config_id'] == before['config_id'], 'pairing.identity')
    for field in ('instance_id', 'remote_pairing_id', 'remote_generation', 'association_id'):
        require((None if row[field] is None else str(row[field])) == expected[field], 'pairing.' + field)
    if expected['pairing_id'] == '$new':
        require(str(row['pairing_id']) != str(before['pairing_id']) and str(row['pairing_id']) == captured.get('new:pairing_id'),
                'pairing.new-pairing-id')
    else:
        require(str(row['pairing_id']) == expected['pairing_id'], 'pairing.pairing_id')
    secret = expected['secret']
    if secret == '$secret':
        require(row['secret_encrypted'] == before['secret_encrypted'], 'pairing.secret')
    elif secret == '$response':
        require(row['secret_encrypted'] != before['secret_encrypted'] and decrypt(row['secret_encrypted']) == captured.get('secret'),
                'pairing.secret-rotated')
    else:
        require(row['secret_encrypted'] == stored_secret(secret), 'pairing.secret-value')
    authority = RUNTIME['authority'] if expected['authority'] == '$authority' else expected['authority']
    require(row['authority'] == authority and row['ui_origin'] == expected['ui_origin'] and row['origin'] == expected['origin']
            and row['association_name'] == expected['association_name'] and row['state'] == expected['state']
            and row['forwarding_enabled'] is expected['forwarding_enabled'], 'pairing.fields')
    if expected['checked_at'] == '$now':
        require(recent(row['checked_at']), 'pairing.checked-now')
        if 'now:checked_at' in captured:
            require(row['checked_at'] == moment(captured['now:checked_at']), 'pairing.checked-response')
    else:
        require(row['checked_at'] == moment(expected['checked_at']), 'pairing.checked_at')
    if expected.get('saved'):
        require(recent(row['updated_at']), 'pairing.saved')
    else:
        require(row['updated_at'] == before['updated_at'], 'pairing.not-saved')


def check_logins(case, source, setup_rows, setup_time, captured):
    require = base_contract.require
    BakneyLogin = _models()[1]
    rows = sorted(BakneyLogin._base_manager.values(), key=lambda row: row['digest'])
    expected = case['effect']['logins']
    if expected == 'unchanged':
        require(rows == setup_rows, 'logins.unchanged')
        return
    require(len(rows) == len(expected), 'logins.count')
    by_digest = {row['digest']: row for row in rows}
    for spec in expected:
        cookie = captured.get('fresh') if spec['cookie'] == '$fresh' else spec['cookie']
        row = by_digest.get(digest(cookie) if cookie else '')
        require(row is not None, 'logins.digest')
        state = captured.get('state') if spec['state'] == '$state' else spec['state']
        require(str(row['pairing_id']) == spec['pairing_id'] and str(row['remote_generation']) == spec['remote_generation']
                and str(row['attempt_id']) == spec['attempt_id'] and row['state'] == state and row['stage'] == spec['stage']
                and (None if row['user_id'] is None else str(row['user_id'])) == user_id(source, spec['user']), 'logins.fields')
        if spec['verifier'] == '$verifier':
            verifier = decrypt(row['verifier_encrypted'])
            require(isinstance(verifier, str) and re.fullmatch(r'[A-Za-z0-9_-]{43}', verifier) is not None
                    and base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=') == captured.get('challenge')
                    and len({verifier, state, cookie}) == 3, 'logins.verifier')
        elif spec['verifier'] == '$garbage':
            require(row['verifier_encrypted'] == GARBAGE, 'logins.verifier-garbage')
        elif spec['verifier']:
            require(decrypt(row['verifier_encrypted']) == spec['verifier'], 'logins.verifier-value')
        else:
            require(row['verifier_encrypted'] == '', 'logins.verifier-empty')
        target = setup_time + timedelta(seconds=spec['expires_in'])
        require(close(row['expires_at'], target), 'logins.expires')


def check_challenges(case, source, setup_rows, setup_time, last):
    require = base_contract.require
    BakneyChallenge = _models()[2]
    rows = sorted(BakneyChallenge._base_manager.values(), key=lambda row: row['digest'])
    expected = case['effect']['challenges']
    if expected == 'unchanged':
        require(rows == setup_rows, 'challenges.unchanged')
        return
    require(len(rows) == len(expected), 'challenges.count')
    by_digest = {row['digest']: row for row in rows}
    for spec in expected:
        row = by_digest.get(challenge_digest(source, spec))
        require(row is not None and str(row['pairing_id']) == spec['pairing_id'], 'challenges.row')
        check_expiry(row['expires_at'], spec['expires'], setup_time, last, 'challenges.expires')


def check_expiry(value, spec, setup_time, last, code):
    require = base_contract.require
    if spec == '$sent':
        require(value == datetime.fromtimestamp(last['sent']['expires_at'], timezone.utc), code)
    else:
        require(close(value, setup_time + timedelta(seconds=spec)), code)


def check_nonces(case, setup_rows, setup_time, last):
    require = base_contract.require
    BakneyNonce = _models()[3]
    rows = sorted(BakneyNonce._base_manager.values(), key=lambda row: row['digest'])
    expected = case['effect']['nonces']
    if expected == 'unchanged':
        require(rows == setup_rows, 'nonces.unchanged')
        return
    require(len(rows) == len(expected), 'nonces.count')
    by_digest = {row['digest']: row for row in rows}
    for spec in expected:
        row = by_digest.get(nonce_digest(spec))
        require(row is not None, 'nonces.row')
        check_expiry(row['expires_at'], spec['expires'], setup_time, last, 'nonces.expires')


def check_revocations(case, setup_rows):
    require = base_contract.require
    BakneyRevocation = _models()[4]
    rows = sorted(BakneyRevocation._base_manager.values(), key=lambda row: str(row['pairing_id']))
    expected = case['effect']['revocations']
    if expected == 'unchanged':
        require(rows == setup_rows, 'revocations.unchanged')
        return
    require(len(rows) == len(expected), 'revocations.count')
    by_id = {str(row['pairing_id']): row for row in rows}
    kept = {str(row['pairing_id']): row for row in setup_rows}
    for spec in expected:
        row = by_id.get(spec['pairing_id'])
        require(row is not None and str(row['remote_pairing_id']) == spec['remote_pairing_id'] and row['authority'] == RUNTIME['authority']
                and decrypt(row['secret_encrypted']) == RUNTIME['secret'], 'revocations.row')
        if spec['pairing_id'] in kept:
            require(row == kept[spec['pairing_id']], 'revocations.kept')
        else:
            require(recent(row['created_at']), 'revocations.created')


def check_users(case, source, before, after):
    require = base_contract.require
    label = case['effect']['last_login']
    if label is None:
        require(before[USER] == after[USER], 'users.unchanged')
        return
    target = user_id(source, label)
    old, new = {str(scalar(row['user_id'])): row for row in before[USER]}, {str(scalar(row['user_id'])): row for row in after[USER]}
    require(set(old) == set(new), 'users.rows')
    for key, row in new.items():
        if key == target:
            changed = {name for name in row if row[name] != old[key][name]}
            require(changed == {'last_login'}, 'users.last-login-only')
            value = row['last_login']
            value = datetime.fromisoformat(value['value'].replace('Z', '+00:00')) if isinstance(value, dict) else value
            require(recent(value), 'users.last-login')
        else:
            require(row == old[key], 'users.other')


def check_login_response(case, source):
    import json as json_module
    from django.conf import settings
    from rest_framework.renderers import JSONRenderer
    from rest_framework_simplejwt.tokens import AccessToken, RefreshToken
    from application.models import User
    from application.services.jwt_token_service import JWTTokenService
    require = base_contract.require
    actual = RUNTIME['last']['response']
    user = User.objects.get(pk=user_id(source, case['response'][len('$login:'):]))
    access, refresh = AccessToken(actual['access_token']), RefreshToken(actual['refresh_token'])
    for token, kind, span in ((access, 'access', settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME']),
                              (refresh, 'refresh', settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'])):
        claims = token.payload
        require(claims['token_type'] == kind and claims['user_id'] == str(user.pk) and claims['role'] == user.role
                and claims['email'] == user.email and claims['iss'] == 'https://bakney.com' and claims['aud'] == 'bakney'
                and claims['exp'] - claims['iat'] == int(span.total_seconds()) and abs(claims['iat'] - time.time()) < 120
                and re.fullmatch(r'[0-9a-f]{32}', claims['jti']), 'login.token:' + kind)
    require(access['jti'] != refresh['jti'], 'login.token-ids')
    tokens = {'access_token': actual['access_token'], 'refresh_token': actual['refresh_token'],
              'expires_in': int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()), 'token_type': 'Bearer'}
    expected = json_module.loads(JSONRenderer().render(JWTTokenService.build_login_response(user, tokens)))
    require(actual == expected, 'login.response')


def assert_effect(case, source, fixture, before, after, setup_state, setup_time):
    require = base_contract.require
    check_authority(case)
    managed = MODELS + (USER,)
    harness.require_unmanaged_unchanged(base_contract, before, after, managed)
    last = RUNTIME['last']
    captured = last['captured']
    check_pairing(case, source, fixture, setup_state['pairing'], captured, last)
    check_logins(case, source, setup_state['logins'], setup_time, captured)
    check_challenges(case, source, setup_state['challenges'], setup_time, last)
    check_nonces(case, setup_state['nonces'], setup_time, last)
    check_revocations(case, setup_state['revocations'])
    check_users(case, source, before, after)
    if isinstance(case['response'], str) and case['response'].startswith('$login:'):
        check_login_response(case, source)
    require(True, 'effect.done')


def sso_state():
    BakneyPairing, BakneyLogin, BakneyChallenge, BakneyNonce, BakneyRevocation = _models()
    return {'pairing': list(BakneyPairing._base_manager.values()),
            'logins': sorted(BakneyLogin._base_manager.values(), key=lambda row: row['digest']),
            'challenges': sorted(BakneyChallenge._base_manager.values(), key=lambda row: row['digest']),
            'nonces': sorted(BakneyNonce._base_manager.values(), key=lambda row: row['digest']),
            'revocations': sorted(BakneyRevocation._base_manager.values(), key=lambda row: str(row['pairing_id']))}


def client_ident(base_url):
    """DRF get_ident: REMOTE_ADDR natively; behind Caddy the X-Forwarded-For it sets."""
    from TESTS.http_client import Client
    if '/api/v' not in base_url:
        return '127.0.0.1'
    gateway = base_url.rsplit('/api/v', 1)[0]
    status, _body, headers = Client(gateway).request('GET', '/healthz', include_headers=True)
    base_contract.require(status == 200 and headers.get('X-Fixture-Client-IP'), 'throttle.client-ip')
    return headers['X-Fixture-Client-IP']


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = base_contract.load_source()
    carnets.state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    RUNTIME.update(source=source, tokens=fixture['tokens'], ident=client_ident(base_url))
    results = []
    for case in source['cases']:
        try:
            setup_time = apply_setup(case, source, fixture)
            preload_throttle(case)
            setup_state = sso_state()
            before = scopes.snapshot_state(apps)
            with STUB['lock']:
                STUB['log'], STUB['script'] = [], case['authority']
            cursor = observer.cursor()
            token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
            status = base_contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            check_throttle(case, status)
            assert_effect(case, source, fixture, before, after, setup_state, setup_time)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    for model in reversed(_models()):
        model._base_manager.all().delete()
    from instance.models import InstanceConfiguration
    InstanceConfiguration._base_manager.filter(pk=fixture['config_pk']).update(
        self_hosted=True, primary_association_id=source['ids']['own_association'])
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=base_contract.assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)
