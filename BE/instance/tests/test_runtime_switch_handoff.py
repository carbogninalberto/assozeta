"""Phase 1 handoff: the owner's instance runtime switch on the Django API.

Contract (the Go API exposes the same; TESTS/contracts/test_instance_runtime_http.py
runs one authored sequence against both):

GET  /instance/admin/runtime   IsInstanceOwner; relays runner GET /runtime and
     returns its status code and JSON body verbatim.
POST /instance/admin/runtime   IsInstanceOwner; body exactly
     {target: 'django'|'go', expected_generation: int >= 1 (not bool),
      idempotency_key: uuid, password}; anything else -> 400
     {'error': 'Richiesta non valida.'} before the password. Then the same
     password confirmation (and attempt limit) as the Go tasks switch and
     backup actions. Then runner POST /runtime-switches
     {request_id: canonical key, actor_id, target, expected_generation}
     (timeout 30 s); the runner's 200/202/400/409/503 status and body are
     returned verbatim (a 409 keeps code/authority/active for the UI).
     Transport failure -> 503 {'error': UNREACHABLE}.
Audit: one LogEntry on the instance configuration when the runner accepted a
new operation (202, replayed false): changes {'runtime_switch': [previous,
requested]}, additional_data {'actor_id', 'operation', 'expected_generation'}.
None for a replay, a no-op, a refusal or a validation error. The password never
reaches the runner, the audit or a response. Every response carries
Cache-Control: no-store.

Run: python manage.py test instance.tests.test_runtime_switch_handoff
"""
import http.server
import json
from pathlib import Path
import shutil
import socketserver
import tempfile
import threading
from uuid import uuid4

from auditlog.models import LogEntry
from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.models import InstanceConfiguration

PATH = '/instance/admin/runtime'
PASSWORD = 'Handoff-owner-placeholder-1'
TOKEN = 'runner-token-placeholder-p1'
UNREACHABLE = 'Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.'
INVALID = {'error': 'Richiesta non valida.'}
AUTHORITY = {'runtime': 'django', 'generation': 1, 'operation_id': None}
OPERATION_ID = 'c1000000-0000-4000-8000-000000000001'


def operation(**changes):
    return {'id': OPERATION_ID, 'kind': 'runtime_switch', 'actor_id': None, 'previous_runtime': 'django',
            'requested_runtime': 'go', 'expected_generation': 1, 'status': 'queued', 'stage': 'queued', 'outcome': None,
            'counts': {'pending': 0, 'transferred': 0, 'blocked': 0}, 'committed_generation': None, 'error': None,
            'error_code': None, 'recovery': None, 'created_at': '2026-11-20T10:00:00+00:00',
            'updated_at': '2026-11-20T10:00:00+00:00', **changes}


class Runner:
    """The update runner on a Unix socket: scripted (status, body) answers, recorded requests."""

    def __init__(self):
        self.directory = Path(tempfile.mkdtemp(prefix='azp1-', dir='/tmp'))
        (self.directory / 'token').write_text(TOKEN + '\n')
        self.replies, self.requests, self.lock = [], [], threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):
                pass

            def address_string(self):
                return 'runner-client'

            def answer(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                with outer.lock:
                    outer.requests.append({'method': self.command, 'path': self.path,
                                           'authorization': self.headers.get('Authorization'),
                                           'json': json.loads(body) if body else None})
                    status, value = outer.replies.pop(0) if outer.replies else (599, {'error': 'unscripted'})
                content = json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(content)
                self.close_connection = True

            do_GET = do_POST = answer

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

            def get_request(self):
                request, _ = self.socket.accept()
                return request, ('runner-client', 0)

        self.server = Server(str(self.directory / 'runner.sock'), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.directory, ignore_errors=True)


class RuntimeSwitchHandoffTests(TestCase):
    def setUp(self):
        self.runner = Runner()
        self.addCleanup(self.runner.close)
        settings = override_settings(ASSOZETA_DEPLOYMENT_MODE='production', ASSOZETA_UPDATER_DIRECTORY=str(self.runner.directory),
                                     CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
        settings.enable()
        self.addCleanup(settings.disable)
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='p1-owner', password=PASSWORD, role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='Runtime ASD')
        self.config = InstanceConfiguration.objects.create(domain='runtime.example.test', name='Runtime ASD',
                                                           primary_association=association)
        self.client.force_authenticate(user=self.owner)

    def script(self, *replies):
        self.runner.replies = list(replies)

    def body(self, **changes):
        return {'target': 'go', 'expected_generation': 1, 'idempotency_key': str(uuid4()), 'password': PASSWORD, **changes}

    def entries(self):
        return list(LogEntry.objects.filter(changes__icontains='runtime_switch').order_by('id'))

    def post(self, body):
        return self.client.post(PATH, body, format='json')

    @staticmethod
    def decoded(response):
        """The JSON body, or a marker: a missing route answers Django's HTML 404 page."""
        try:
            return response.json()
        except ValueError:
            return f'<{response.status_code} not JSON: CONTRACT MISSING {PATH}>'

    def assertNoStore(self, response):
        self.assertEqual(response.get('Cache-Control'), 'no-store', f'{response.status_code} must not be cached')

    def test_route_exists_for_the_owner(self):
        self.script((200, {'protocol': 1}))
        response = self.client.get(PATH)
        self.assertNotEqual(response.status_code, 404, 'CONTRACT MISSING: /instance/admin/runtime route')

    def test_anonymous_and_non_owners_are_rejected_before_the_runner(self):
        self.client.force_authenticate(user=None)
        for method in ('get', 'post'):
            response = getattr(self.client, method)(PATH, self.body(), format='json')
            self.assertEqual(response.status_code, 401, method)
            self.assertNoStore(response)
        collaborator = User.objects.create_user(username='p1-collaborator', password=PASSWORD, role=User.COLLABORATOR,
                                                connected_user=self.owner)
        other = User.objects.create_user(username='p1-other', password=PASSWORD, role=User.ASSOCIATION)
        inactive_admin = User.objects.create_superuser(username='p1-inactive', password=PASSWORD)
        inactive_admin.is_active = False
        inactive_admin.save()
        for user in (collaborator, other, inactive_admin):
            self.client.force_authenticate(user=user)
            for method in ('get', 'post'):
                with self.subTest(user=user.username, method=method):
                    response = getattr(self.client, method)(PATH, self.body(), format='json')
                    self.assertEqual(response.status_code, 403)
                    self.assertNoStore(response)
        self.assertEqual(self.runner.requests, [])
        self.assertEqual(self.entries(), [])

    def test_status_relays_the_runner_verbatim(self):
        status = {'protocol': 1, 'effective_runtime': 'django', 'generation': 1, 'last_operation_id': None,
                  'requested_runtime': None, 'operation': None, 'active': None, 'authority_error': None,
                  'recovery_command': './bin/assozeta runtime-recover'}
        self.script((200, status))
        response = self.client.get(PATH)
        self.assertEqual((response.status_code, self.decoded(response)), (200, status))
        self.assertNoStore(response)
        self.assertEqual(self.runner.requests, [{'method': 'GET', 'path': '/runtime', 'authorization': f'Bearer {TOKEN}', 'json': None}])
        admin = User.objects.create_superuser(username='p1-admin', password=PASSWORD)
        self.client.force_authenticate(user=admin)
        self.script((200, status))
        self.assertEqual(self.client.get(PATH).status_code, 200)

    def test_unreachable_runner_answers_503(self):
        (self.runner.directory / 'runner.sock').rename(self.runner.directory / 'off.sock')
        for response in (self.client.get(PATH), self.post(self.body())):
            self.assertEqual((response.status_code, self.decoded(response)), (503, {'error': UNREACHABLE}))
            self.assertNoStore(response)
        self.assertEqual(self.entries(), [])

    def test_malformed_requests_are_refused_before_password_and_runner(self):
        valid = self.body()
        cases = [{**valid, 'extra': 1}, {k: v for k, v in valid.items() if k != 'idempotency_key'},
                 {k: v for k, v in valid.items() if k != 'password'}, {**valid, 'target': 'node'},
                 {**valid, 'target': 'Go'}, {**valid, 'expected_generation': True}, {**valid, 'expected_generation': '1'},
                 {**valid, 'expected_generation': 0}, {**valid, 'idempotency_key': 'retry-1'}, [valid]]
        for body in cases:
            with self.subTest(body=body):
                response = self.post(body)
                self.assertEqual((response.status_code, self.decoded(response)), (400, INVALID))
                self.assertNoStore(response)
        self.assertEqual(self.runner.requests, [])
        self.assertEqual(self.entries(), [])

    def test_wrong_password_is_refused_before_the_runner(self):
        response = self.post(self.body(password='not-the-password'))
        self.assertEqual((response.status_code, self.decoded(response)), (400, {'error': 'Password non corretta.'}))
        response = self.post(self.body(password=''))
        self.assertEqual(response.status_code, 400)
        self.assertIn('password', self.decoded(response))
        self.assertEqual(self.runner.requests, [])

    def test_accepted_switch_relays_exact_request_and_audits_once(self):
        key = str(uuid4()).upper()
        accepted = {'operation': operation(actor_id=str(self.owner.pk)), 'authority': AUTHORITY, 'replayed': False}
        self.script((202, accepted))
        response = self.post(self.body(idempotency_key=key))
        self.assertEqual((response.status_code, self.decoded(response)), (202, accepted))
        self.assertNoStore(response)
        [request] = self.runner.requests
        self.assertEqual((request['method'], request['path'], request['authorization']), ('POST', '/runtime-switches', f'Bearer {TOKEN}'))
        self.assertEqual(request['json'], {'request_id': key.lower(), 'actor_id': str(self.owner.pk), 'target': 'go',
                                           'expected_generation': 1})
        [entry] = self.entries()
        changes = entry.changes if isinstance(entry.changes, dict) else json.loads(entry.changes)
        self.assertEqual(changes, {'runtime_switch': ['django', 'go']})
        self.assertEqual(entry.additional_data, {'actor_id': str(self.owner.pk), 'operation': OPERATION_ID, 'expected_generation': 1})
        self.assertEqual(entry.actor_id, self.owner.pk)
        self.assertEqual((entry.content_type.model, entry.object_pk), ('instanceconfiguration', str(self.config.pk)))
        stored = json.dumps([LogEntry.objects.filter(pk=entry.pk).values()[0]], default=str)
        self.assertNotIn(PASSWORD, stored)
        self.assertNotIn(PASSWORD, json.dumps(self.runner.requests))

    def test_replay_noop_and_refusals_are_relayed_verbatim_without_audit(self):
        replay = {'operation': operation(), 'authority': AUTHORITY, 'replayed': True}
        noop = {'operation': operation(requested_runtime='django', status='succeeded', stage='completed', outcome='noop'),
                'authority': AUTHORITY, 'replayed': False}
        stale = {'error': 'Stato cambiato.', 'code': 'stale_generation',
                 'authority': {'runtime': 'go', 'generation': 2, 'operation_id': OPERATION_ID}, 'active': None}
        busy = {'error': 'Operazione in corso.', 'code': 'operation_in_progress', 'authority': AUTHORITY,
                'active': {'id': 'c1000000-0000-4000-8000-000000000002', 'kind': 'restore', 'status': 'running', 'stage': 'checking'}}
        reused = {'error': 'Identificativo già usato.', 'code': 'idempotency_conflict', 'authority': AUTHORITY,
                  'active': None, 'operation': operation()}
        unavailable = {'error': 'Lo stato del runtime non è leggibile.', 'code': 'authority_unavailable'}
        for status, value in ((202, replay), (200, noop), (409, stale), (409, busy), (409, reused), (503, unavailable)):
            with self.subTest(status=status, code=value.get('code')):
                self.script((status, value))
                response = self.post(self.body())
                self.assertEqual((response.status_code, self.decoded(response)), (status, value))
                self.assertNoStore(response)
        self.assertEqual(self.entries(), [])

    def test_methods(self):
        self.assertEqual(self.client.put(PATH, self.body(), format='json').status_code, 405)
        response = self.client.options(PATH)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({part.strip() for part in response['Allow'].split(',')}, {'GET', 'POST', 'HEAD', 'OPTIONS'})
