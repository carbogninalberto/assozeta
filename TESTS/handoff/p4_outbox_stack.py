"""Phase 4 part B stack: the P4 stack plus the WhatsApp provider stub, the Go API (embedded
WhatsApp outbox poller and webhook), both realtime relays and a realtime subscriber.

- WhatsAppStub: a local Cloud API (POST /<version>/<phone id>/messages) that records every send by
  its biz_opaque_callback_data (the delivery id), answers {"messages": [{"id": "wamid.p4.<n>"}]}, and
  can hold a request in flight (hold/release) -- the provider-side evidence of business effects.
- Go API: cmd/server (-tags handoff) on a private port with the stack's env file, the WhatsApp
  settings of the stub and ASSOZETA_HANDOFF_WHATSAPP_POLL (test-speed embedded poll interval).
- Relays: ``manage.py relay_go_realtime`` (Django, p4 settings: the production
  GoMirroredRedisChannelLayer on the private Redis) and cmd/relay-go-realtime (Go).
- RealtimeSubscriber: a Redis pub/sub client on 'assozeta:realtime:v1', the cross-runtime mirror
  both relays publish every delivered group message to; it can disconnect and reconnect.
"""
from contextlib import suppress
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid

from TESTS.handoff.p3_stack import REDIS_PASSWORD, ROOT, ContractFailure, check, free_port, wait_for
from TESTS.handoff.p4_stack import P4Stack

PHONE_ID, ACCOUNT_ID = '1000000000001', '2000000000002'
ACCESS_TOKEN = 'fixture-' + secrets.token_hex(8)
APP_SECRET = 'fixture-' + secrets.token_hex(8)
VERIFY_TOKEN = 'fixture-verify'
REALTIME_CHANNEL = 'assozeta:realtime:v1'
EVENTS = 'assozeta_go_realtime_events'
OUTBOX_EFFECTS = 'assozeta_outbox_effects'
P4B_MIGRATION = '000008_outbox_ownership'


class WhatsAppStub:
    def __init__(self):
        stub = self
        self.lock = threading.Lock()
        self.sends = []          # [{'reference', 'to', 'type', 'message_id'}] accepted by the provider
        self.in_flight = set()   # references whose request is held
        self.holds = {}          # reference -> threading.Event (released when set)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                payload = json.loads(self.rfile.read(length) or b'{}')
                reference = str(payload.get('biz_opaque_callback_data', ''))
                with stub.lock:
                    message_id = f'wamid.p4.{len(stub.sends) + 1}'
                    stub.sends.append({'reference': reference, 'to': payload.get('to'), 'type': payload.get('type'),
                                       'message_id': message_id})
                    hold = stub.holds.get(reference)
                    if hold:
                        stub.in_flight.add(reference)
                if hold:
                    hold.wait(120)
                body = json.dumps({'messaging_product': 'whatsapp', 'contacts': [{'wa_id': payload.get('to')}],
                                   'messages': [{'id': message_id}]}).encode()
                with suppress(OSError):
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def hold(self, reference):
        with self.lock:
            self.holds[str(reference)] = threading.Event()

    def release(self, reference):
        with self.lock:
            event = self.holds.pop(str(reference), None)
        if event:
            event.set()

    def count(self, reference=None):
        with self.lock:
            return len([s for s in self.sends if reference is None or s['reference'] == str(reference)])

    def message_id(self, reference):
        with self.lock:
            return next(s['message_id'] for s in self.sends if s['reference'] == str(reference))

    def reset(self):
        with self.lock:
            for event in self.holds.values():
                event.set()
            self.sends, self.in_flight, self.holds = [], set(), {}

    def close(self):
        self.reset()
        self.server.shutdown()


class RealtimeSubscriber:
    """Collects every mirrored group message ({'version', 'group', 'message'}) while connected."""

    def __init__(self, port):
        self.port, self.received, self.lock, self.thread, self.pubsub = port, [], threading.Lock(), None, None
        self.client, self.errors = None, []
        self.connections = 0

    def connect(self):
        import redis
        # Keep the client for the subscription's lifetime: redis-py closes its pool (and the pub/sub connection)
        # when the client object is collected.
        self.client = client = redis.Redis(host='127.0.0.1', port=self.port, password=REDIS_PASSWORD, socket_timeout=None)
        self.pubsub = client.pubsub(ignore_subscribe_messages=True)
        self.pubsub.subscribe(REALTIME_CHANNEL)
        self.connections += 1
        pubsub = self.pubsub

        def listen():
            try:
                for item in pubsub.listen():
                    if item.get('type') == 'message':
                        with self.lock:
                            self.received.append(json.loads(item['data']))
            except Exception as exc:  # recorded: a lost subscription must not look like zero deliveries
                if self.pubsub is pubsub:
                    self.errors.append(repr(exc))
        self.thread = threading.Thread(target=listen, daemon=True)
        self.thread.start()
        wait_for(lambda: client.execute_command('PUBSUB', 'NUMSUB', REALTIME_CHANNEL)[1] >= 1, 'subscriber attached')

    def disconnect(self):
        if self.pubsub:
            pubsub, self.pubsub = self.pubsub, None
            with suppress(Exception):
                pubsub.close()
        if self.client:
            with suppress(Exception):
                self.client.close()
            self.client = None

    def event_ids(self):
        check(not self.errors, f'the realtime subscriber lost its subscription: {self.errors}')
        with self.lock:
            return [item.get('message', {}).get('event_id') for item in self.received]

    def messages(self):
        with self.lock:
            return list(self.received)


class P4OutboxStack(P4Stack):
    def __init__(self, output):
        self.whatsapp = WhatsAppStub()
        super().__init__(output)
        self.api_port = None
        self.subscriber = None

    def extra_django_environment(self):
        return {'WHATSAPP_API_BASE_URL': self.whatsapp.url(), 'WHATSAPP_PHONE_NUMBER_ID': PHONE_ID,
                'WHATSAPP_BUSINESS_ACCOUNT_ID': ACCOUNT_ID, 'WHATSAPP_ACCESS_TOKEN': ACCESS_TOKEN,
                'WHATSAPP_APP_SECRET': APP_SECRET, 'WHATSAPP_WEBHOOK_VERIFY_TOKEN': VERIFY_TOKEN,
                'WHATSAPP_NOTIFICATION_ROUTES': '{}'}

    def build_go(self):
        super().build_go()
        from TESTS.adapters.go.server import build_server
        build_server(self.work / 'server', self.log, dict(self.base_env), tags=('handoff',))  # + adjacent image-codec
        self.run(['go', 'build', '-tags', 'handoff', '-o', str(self.work / 'relay-go-realtime'), './cmd/relay-go-realtime'],
                 cwd=ROOT / 'BE_GO', env=dict(self.base_env), timeout=900)

    def start(self):
        super().start()
        self.subscriber = RealtimeSubscriber(self.redis_port)
        return self

    def reset(self):
        if self.subscriber:
            self.subscriber.disconnect()
            self.subscriber = RealtimeSubscriber(self.redis_port)
        super().reset()
        self.whatsapp.reset()
        present = [t for t in (OUTBOX_EFFECTS,) if self.exists('public.' + t)]
        self.sql('TRUNCATE ' + ', '.join(['communications_whatsappreceipt', 'communications_whatsappdelivery',
                                          'public.' + EVENTS, *('public.' + t for t in present)]) + ' RESTART IDENTITY CASCADE')

    def close(self):
        with suppress(Exception):
            self.subscriber.disconnect()
        with suppress(Exception):
            self.whatsapp.close()
        super().close()

    # -- schema ---------------------------------------------------------------------------------
    def require_outbox_schema(self):
        self.require_handoff_ledger()
        from TESTS.handoff.p4_stack import PROBE
        if PROBE:
            return
        import re
        version = int(re.search(r'^const CurrentVersion = (\d+)$', (ROOT / 'BE_GO/migrations/migrations.go').read_text(), re.M)[1])
        for suffix in ('up', 'down'):
            check((ROOT / f'BE_GO/migrations/{P4B_MIGRATION}.{suffix}.sql').exists(),
                  f'contract: BE_GO/migrations/{P4B_MIGRATION}.{{up,down}}.sql missing (migration {P4B_MIGRATION})')
        check(version >= 8, f'contract: migrations.CurrentVersion must be >= 8 (migration {P4B_MIGRATION})')
        check(self.exists('public.' + OUTBOX_EFFECTS), f'contract: public.{OUTBOX_EFFECTS} does not exist (migration {P4B_MIGRATION})')
        missing = {'outbox', 'item_id', 'attempt', 'runtime', 'ownership_generation', 'consumer_id', 'state', 'provider_ref',
                   'recorded_at', 'resolved_at', 'resolved_by'} - self.columns(OUTBOX_EFFECTS)
        check(not missing, f'contract: public.{OUTBOX_EFFECTS} lacks {sorted(missing)} (migration {P4B_MIGRATION})')
        missing = {'delivered_runtime', 'delivered_generation'} - self.columns(EVENTS)
        check(not missing, f'contract: public.{EVENTS} lacks {sorted(missing)} (migration {P4B_MIGRATION})')
        for signature in ('assozeta_outbox_claim(text,text,text)',
                          'assozeta_outbox_effect_intent(text,text,text,text,integer)',
                          'assozeta_outbox_effect_confirm(text,text,integer,text)',
                          'assozeta_outbox_reconcile(text,text,text,text)'):
            check(self.sql('SELECT to_regprocedure(%s) IS NOT NULL', ('public.' + signature,))[0][0],
                  f'contract: public.{signature} is missing (migration {P4B_MIGRATION})')

    # -- WhatsApp -------------------------------------------------------------------------------
    def queue_whatsapp(self, count=1, **fields):
        """Committed outbox rows as both runtimes' Enqueue writes them; returns their ids."""
        ids = []
        for _ in range(count):
            identifier = str(uuid.uuid4())
            values = dict(event_type='p4_probe', recipient='+390000000001', body='fixture body', template_name='',
                          template_language='', status='queued', attempts=0, error_code='', error_message='',
                          provider_message_id=None, next_attempt_offset='-1 second')
            values.update(fields)
            offset = values.pop('next_attempt_offset')
            self.sql(f"""INSERT INTO communications_whatsappdelivery (id, event_type, recipient, body, template_name,
                template_language, status, attempts, error_code, error_message, provider_message_id, created_at, updated_at,
                next_attempt_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), now(), now() + interval '{offset}')""",
                     (identifier, values['event_type'], values['recipient'], values['body'], values['template_name'],
                      values['template_language'], values['status'], values['attempts'], values['error_code'],
                      values['error_message'], values['provider_message_id']))
            ids.append(identifier)
        return ids

    def delivery(self, identifier):
        with self.connection.cursor() as cursor:
            cursor.execute('SELECT * FROM communications_whatsappdelivery WHERE id=%s', (identifier,))
            row = cursor.fetchone()
            return dict(zip([c.name for c in cursor.description], row)) if row else None

    def outbox_effects(self, item_id=None):
        with self.connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM public.{OUTBOX_EFFECTS}' + (' WHERE item_id=%s' if item_id else '') +
                           ' ORDER BY recorded_at', (item_id,) if item_id else None)
            return [dict(zip([c.name for c in cursor.description], row)) for row in cursor.fetchall()]

    def go_api(self, *, outbox=True):
        """cmd/server with the embedded WhatsApp poller (production default ASSOZETA_GO_WHATSAPP_OUTBOX=1)."""
        self.api_port = free_port()
        env_file = self.work / 'go-api.env'
        import base64
        keys = {f'{name}_B64': base64.b64encode(self.env[name].encode()).decode() for name in ('JWT_SECRET_KEY', 'JWT_PUBLIC_KEY')}
        extra = {'GO_LISTEN_ADDR': f'127.0.0.1:{self.api_port}', **self.extra_django_environment(), **keys,
                 'CURRENT_HOST': 'http://127.0.0.1:9', 'CURRENT_HOST_PUPPETEER': 'http://127.0.0.1:9'}
        base = ''.join(line + '\n' for line in self.go_env_file().read_text().splitlines() if not line.startswith('JWT_'))
        env_file.write_text(base + ''.join(f'{k}={v}\n' for k, v in extra.items()))
        env_file.chmod(0o600)
        log = self.output / f'go-api-{len(self.children) + 1}.log'
        stream = log.open('w')
        env = dict(self.base_env, ASSOZETA_HANDOFF_BARRIER_DIR=str(self.barrier.directory),
                   ASSOZETA_GO_WHATSAPP_OUTBOX='1' if outbox else '0', ASSOZETA_HANDOFF_WHATSAPP_POLL='100ms',
                   ASSOZETA_HANDOFF_HEARTBEAT='100ms')
        process = subprocess.Popen([str(self.work / 'server'), '--env-file', str(env_file)], env=env, stdout=stream,
                                   stderr=subprocess.STDOUT, cwd=str(self.work))
        self.children.append((process, stream, log))

        def ready():
            if process.poll() is not None:
                return 'exited'
            with suppress(OSError), socket.create_connection(('127.0.0.1', self.api_port), timeout=0.2):
                return 'ready'
            return None
        state = wait_for(ready, 'Go API startup', timeout=60)
        if state == 'exited':
            raise RuntimeError('Go API exited during startup: ' + log.read_text()[-1500:])
        return process

    def webhook_body(self, reference, message_id, status='delivered'):
        return json.dumps({'object': 'whatsapp_business_account', 'entry': [{'id': ACCOUNT_ID, 'changes': [{
            'field': 'messages', 'value': {'metadata': {'phone_number_id': PHONE_ID}, 'statuses': [{
                'id': message_id, 'status': status, 'timestamp': str(int(time.time())),
                'biz_opaque_callback_data': str(reference)}]}}]}]}).encode()

    @staticmethod
    def signature(body):
        return 'sha256=' + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()

    def receipt_to_django(self, reference, message_id, status='delivered'):
        """POST the provider's status callback to the Django webhook view (in-process test client)."""
        from django.test import Client
        body = self.webhook_body(reference, message_id, status)
        response = Client().post('/communications/whatsapp/webhook', data=body, content_type='application/json',
                                 HTTP_X_HUB_SIGNATURE_256=self.signature(body))
        return response.status_code

    def receipt_to_go(self, reference, message_id, status='delivered'):
        import urllib.request
        body = self.webhook_body(reference, message_id, status)
        request = urllib.request.Request(f'http://127.0.0.1:{self.api_port}/communications/whatsapp/webhook', data=body,
                                         method='POST', headers={'Content-Type': 'application/json',
                                                                 'X-Hub-Signature-256': self.signature(body)})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status
        except urllib.error.HTTPError as error:
            return error.code

    def receipts_of(self, identifier):
        return self.sql('SELECT provider_message_id, status FROM communications_whatsappreceipt WHERE delivery_id=%s '
                        'ORDER BY id', (identifier,))

    def push_poll(self, task_id, expires):
        """A legacy Beat poll envelope (communications.tasks.deliver_whatsapp, options.expires) on 'celery'."""
        self.push('celery', self.envelope('communications.tasks.deliver_whatsapp', task_id, expires=expires,
                                          stamp=time.time()))

    # -- realtime -------------------------------------------------------------------------------
    def add_events(self, count, target=None):
        target = target or str(uuid.uuid4())
        rows = self.sql(f"""INSERT INTO public.{EVENTS} (envelope_version, kind, target_id)
            SELECT 1, 'staff_board_changed', %s::uuid FROM generate_series(1, %s) RETURNING id""", (target, count))
        return [row[0] for row in rows], target

    def event_rows(self):
        with self.connection.cursor() as cursor:
            cursor.execute(f'SELECT * FROM public.{EVENTS} ORDER BY id')
            return [dict(zip([c.name for c in cursor.description], row)) for row in cursor.fetchall()]

    def django_relay(self):
        log = self.output / f'django-relay-{len(self.children) + 1}.log'
        stream = log.open('w')
        env = dict(self.env, ASSOZETA_HANDOFF_APPLICATION='handoff-django-relay')
        process = subprocess.Popen([sys.executable, 'manage.py', 'relay_go_realtime', '--poll-seconds', '0.1'],
                                   cwd=ROOT / 'BE', env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.children.append((process, stream, log))
        return process

    def go_relay(self):
        probe = subprocess.run([str(self.work / 'relay-go-realtime'), '--handoff-heartbeat', '100ms', '--poll-seconds', 'x'],
                               env=dict(self.base_env), capture_output=True, text=True, timeout=60)
        check('unrecognized arguments' not in probe.stderr,
              'contract: cmd/relay-go-realtime refused --handoff-heartbeat (Phase 4 relay registration flag missing)')
        log = self.output / f'go-relay-{len(self.children) + 1}.log'
        stream = log.open('w')
        env = dict(self.base_env, ASSOZETA_HANDOFF_BARRIER_DIR=str(self.barrier.directory))
        process = subprocess.Popen([str(self.work / 'relay-go-realtime'), '--env-file', str(self.go_env_file()),
                                    '--poll-seconds', '0.1', '--handoff-heartbeat', '100ms'],
                                   env=env, stdout=stream, stderr=subprocess.STDOUT)
        self.children.append((process, stream, log))
        return process
