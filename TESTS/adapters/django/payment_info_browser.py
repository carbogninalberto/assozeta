"""Unchanged public checkout loading summaries against real Django/v1 and Go/v2.

Stripe POSTs are transparently gated, then released to real Django's disabled
payment rejection. No provider calls, enabled checkout, approved public-access
policy, installed switching or historical compatibility is claimed.
"""
import argparse
import asyncio
from decimal import Decimal
import time
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
DATABASE = 'assozeta_contract_payment_info_browser'
SELECTED_ROUTES = ('instance/status', 'instance/config', 'payment/*/info')
COEXISTENCE_ROUTES = ('instance/manifest.json', 'instance/logo.png', 'stripe/pay/*')
from TESTS.adapters.django.profile_browser import prepare_ui, free_port, fingerprints
WORKFLOW_SOURCES = (
    'src/App.svelte', 'src/routes.js', 'src/routes/stripe/Checkout.svelte',
    'src/utils/ApiMiddleware.js', 'src/store/instanceStore.js', 'src/utils/BrandTheme.js',
    'src/store/loadingStore.js', 'src/components/loading/LoadingOverlay.svelte',
    'src/utils/HealthWebSocket.js',
)

class CheckoutObserver:
    """Transparent upstream observer plus a bounded Stripe ingress gate.

    Control responses contain only fixture synchronization state. Every business
    request/response is processed unchanged by the real ASGI application.
    """
    def __init__(self, application, payment_ids, state_check):
        self.application = application
        self.state_check = state_check
        self.payment_ids = set(payment_ids)
        self.lock = threading.Lock()
        self.label = 'startup'
        self.requests = {}
        self.gates = {}
        self.history = []

    def begin(self, label):
        with self.lock:
            self.label = label
            self.requests[label] = []

    def snapshot(self, label):
        with self.lock:
            return [dict(row) for row in self.requests.get(label, [])]

    def assert_no_selected(self, label):
        assert not any(row['classification'] == 'selected' for row in self.snapshot(label)), 'Selected v2 traffic reached real Django'

    def release_all(self):
        with self.lock:
            for gate in self.gates.values():
                gate['release'].set()

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        control = re.fullmatch(r'/__fixture/payment-info/([0-9a-f-]+)/([a-z]+)', path)
        if scope['type'] == 'http' and control:
            payment, action = control.groups()
            assert payment in self.payment_ids
            if action == 'checkpoint':
                assert scope['method'] == 'POST'
                await asyncio.to_thread(self.state_check)
            with self.lock:
                if action == 'arm':
                    assert scope['method'] == 'POST'
                    old = self.gates.get(payment)
                    assert old is None or old['completed'], 'Previous real request has not completed'
                    gate = dict(payment_id=payment, label=self.label, entered=False, released=False,
                                completed=False, status=None, timed_out=False, release=threading.Event())
                    self.gates[payment] = gate
                    self.history.append(gate)
                else:
                    gate = self.gates[payment]
                    if action == 'release':
                        assert scope['method'] == 'POST' and gate['entered']
                        gate['released'] = True
                        gate['release'].set()
                    elif action == 'checkpoint':
                        assert gate['completed'] and gate['status'] == 409
                        gate['managed_state_unchanged'] = True
                    else:
                        assert action == 'status' and scope['method'] == 'GET'
                body = {k: v for k, v in gate.items() if k != 'release'}
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
            await send({'type': 'http.response.body', 'body': json.dumps(body).encode()})
            return
        row = None
        gate = None
        if scope['type'] == 'http':
            selected = path in ('/instance/status', '/instance/config') or bool(re.fullmatch(r'/payment/[^/]+/info', path))
            coexistence = path in ('/instance/manifest.json', '/instance/logo.png') or path.startswith('/stripe/pay/')
            row = dict(classification='selected' if selected else ('coexistence' if coexistence else 'other'),
                       method=scope['method'], path=path, status=None)
            with self.lock:
                self.requests.setdefault(self.label, []).append(row)
                if path.startswith('/stripe/pay/'):
                    payment = path.removeprefix('/stripe/pay/')
                    assert payment in self.payment_ids and scope['method'] == 'POST'
                    gate = self.gates[payment]
                    assert not gate['entered'], 'Duplicate business POST before gate rearm'
                    gate['entered'] = True
            if gate:
                deadline = time.monotonic() + 40
                while not gate['release'].is_set() and time.monotonic() < deadline:
                    await asyncio.sleep(.01)
                if not gate['release'].is_set():
                    gate['timed_out'] = True
                    raise AssertionError('Browser failed to release real Stripe request')
        async def observed_send(message):
            if message['type'] == 'http.response.start':
                if row is not None:
                    row['status'] = message['status']
                if gate:
                    gate['status'] = message['status']
            if gate and message['type'] == 'http.response.body' and not message.get('more_body', False):
                gate['completed'] = True
            await send(message)
        await self.application(scope, receive, observed_send)

    def gate_receipt(self):
        with self.lock:
            return [{k: v for k, v in gate.items() if k != 'release'} for gate in self.history]


@contextlib.contextmanager
def proxy_network(receipt):
    """Keep the container-to-container hop off host loopback on Linux and macOS."""
    name = 'az-payment-info-browser-net-' + uuid.uuid4().hex
    label = 'org.assozeta.payment-info-browser-network=' + name
    def docker(*args):
        return subprocess.run(['docker', *args], check=True, capture_output=True,
                              text=True, timeout=60).stdout
    docker('network', 'create', '--label', label, name)
    try:
        yield name
    finally:
        info = json.loads(docker('network', 'inspect', name))[0]
        assert info.get('Labels', {}).get('org.assozeta.payment-info-browser-network') == name
        docker('network', 'rm', name)
        assert not docker('network', 'ls', '--quiet', '--filter', 'label=' + label).strip(), 'Private proxy network remains'
        receipt['private_proxy_network_removed'] = True


@contextlib.contextmanager
def gateway(work, log, ui, django_port, versioned_upstream, version, *, network, selected_routes=SELECTED_ROUTES):
    """Exact allowlist routing; unknown API paths fail instead of falling back."""
    image = re.search(r'^FROM (caddy:\S+) AS production$', (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
    name = 'az-payment-info-browser-' + uuid.uuid4().hex
    label = 'django-v1' if version == 'v1' else 'go-v2'
    config = '''{
 admin off
}
:80 {
 root * /srv
 route {
  handle /fixture-health {
   respond 200
  }
  handle /__fixture/payment-info/* {
   header X-Fixture-Backend fixture-control
   reverse_proxy host.docker.internal:DJANGO
  }
  @selected path SELECTED
  handle @selected {
   header X-Fixture-Backend LABEL
   uri replace /api/ /api/VERSION/ 1
   reverse_proxy TARGET
  }
  @coexistence path COEXISTENCE
  handle @coexistence {
   header X-Fixture-Backend django-coexistence
   uri strip_prefix /api
   reverse_proxy host.docker.internal:DJANGO
  }
  handle /ws/* {
   header X-Fixture-Backend django-coexistence
   reverse_proxy host.docker.internal:DJANGO
  }
  @unknown path /api/* /instance-*
  handle @unknown {
   header X-Fixture-Backend unsupported-fixture-route
   respond 502
  }
  handle {
   header X-Fixture-Backend ui-static
   header Cache-Control no-cache
   try_files {path} /index.html
   file_server
  }
 }
}
'''.replace('SELECTED', ' '.join('/api/' + p for p in selected_routes)).replace('COEXISTENCE',
    ' '.join('/api/' + p for p in COEXISTENCE_ROUTES)).replace('LABEL', label).replace('VERSION', version).replace('TARGET', versioned_upstream).replace('DJANGO', str(django_port))
    path = work / (version + '.Caddyfile'); path.write_text(config)
    def docker(*args, check=True):
        return subprocess.run(['docker', *map(str, args)], check=check, capture_output=True, text=True, timeout=60)
    # Missing preexisting image fails; this runner never builds or pulls images.
    image_id = json.loads(docker('image', 'inspect', image).stdout)[0]['Id']
    try:
        docker('run', '--detach', '--name', name, '--label', 'org.assozeta.payment-info-browser=true',
               '--network', network, '--add-host', 'host.docker.internal:host-gateway', '--publish', '127.0.0.1::80',
               '--mount', f'type=bind,source={path},target=/etc/caddy/Caddyfile,readonly',
               '--mount', f'type=bind,source={ui},target=/srv,readonly', image_id)
        port = int(docker('port', name, '80/tcp').stdout.strip().rsplit(':', 1)[1])
        from TESTS.http_client import Client, poll
        origin = f'http://127.0.0.1:{port}'
        def ready():
            try: return Client(origin).request('GET', '/fixture-health')[0] == 200
            except OSError: return False
        poll(ready, bool, timeout=30)
        docker('exec', name, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile')
        yield origin, {'image_id': image_id, 'configuration_sha256': hashlib.sha256(config.encode()).hexdigest(),
                       'selected_backend': label, 'versioned_upstream': versioned_upstream,
                       'container_hop': 'owned Docker network alias', 'canonical_api_alias_is_private_test_routing': True, 'selected_versioned_prefix': '/api/'+version, 'selected_routes': list(selected_routes), 'django_coexistence_routes': list(COEXISTENCE_ROUTES)}
    finally:
        # No access log is enabled: URLs containing JWT query parameters and
        # Authorization/cookie headers must not enter persistent proof output.
        docker('rm', '-f', name, check=False)
        remaining = docker('container', 'ls', '--all', '--format', '{{.Names}}', '--filter', 'name=' + name).stdout.splitlines()
        assert name not in remaining, 'Private proxy cleanup failed'


def exercise(work, output, log, options, tool_env, network, result):
    import redis
    import uvicorn
    from django.apps import apps
    from django.conf import settings
    from django.core.cache import cache
    from django.db import connections
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from application.models import (User, SportAssociation, Associate, Subscription,
                                    Payment, PaymentCategory, Course, CourseSubscription)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from instance.models import InstanceConfiguration
    from TESTS.http_client import Client, poll
    from TESTS.adapters.caddy import proxy as versioned_proxy
    from TESTS.adapters.django.login_jpeg import build_codec

    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    result['managed_snapshot_scope'] = {'includes_auto_created_many_to_many': True, 'models': sorted(original)}
    created = []
    def seed(model, **values):
        with disable_auditlog():
            row = model._base_manager.create(**values)
        created.append((model, row.pk))
        return row
    server = thread = go_process = redis_process = observer = None
    result.update(versions={}, managed_cleanup_verified=False, private_services_stopped=False)
    runtime = dict(RUNNING_VERSION='payment-info-browser-fixture', AI_API_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
        SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', SOCIAL_AUTH_APPLE_ID_CLIENT='', STRIPE_KEY='', STRIPE_PUBLIC_KEY='',
        STRIPE_WEBHOOK_SECRET='', MANUAL_CORPUS_BASE_URL='', MANUAL_RUN_ID='', MANUAL_SOURCE_ROOT='',
        MANUAL_BUNDLE_PATH=str(work/'absent-manual'), MANUAL_BUNDLE_REQUIRED=False,
        MANUAL_INDEX_PATH=str(work/'absent-index.json'), MANUAL_DEVELOPMENT_ROOT=str(work/'absent-development'),
        FAMILY_REGISTRATION_ENABLED=True, FEDERATION_REGISTRATION_ENABLED=False, MULTIPLE_REGISTRATION_ENABLED=False)
    cache_port = options.cache_port
    cache_password = 'private-payment-info-browser-cache'
    caches = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{cache_port}/1',
                         'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': cache_password}}}
    layers = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer', 'CONFIG': {
        'hosts': [f'redis://:{cache_password}@127.0.0.1:{cache_port}/2']}}}
    try:
        redis_process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(cache_port),
            '--save', '', '--appendonly', 'no', '--requirepass', cache_password, '--dir', str(work)],
            stdout=log, stderr=subprocess.STDOUT)
        cache_client = redis.Redis(host='127.0.0.1', port=cache_port, db=1, password=cache_password)
        def cache_ready():
            try: return cache_client.ping()
            except redis.ConnectionError: return False
        poll(cache_ready, bool, timeout=15)
        with override_settings(**runtime, CACHES=caches, CHANNEL_LAYERS=layers):
            owner = seed(User, username='CHECKOUT-OWNER', email='checkout-owner@example.test',
                         role=User.ASSOCIATION, password='!unused', online_payments=False)
            association = seed(SportAssociation, user=owner, denomination='Checkout Browser ASD',
                               tax_code='12345678901', checkout_info='<p>Conserva la ricevuta &amp; il riepilogo.</p>',
                               additional_sections=[])
            athlete = seed(User, username='CHECKOUT-MEMBER', email='checkout-member@example.test',
                           first_name='Ada', last_name='Athlete', role=User.ATHLETE, password='!unused')
            member = seed(Associate, user=athlete, sport_association=association,
                          first_name='Ada', last_name='Browser', tax_code='BROWSERFAMILYCODE')
            subscription = seed(Subscription, user=athlete, associate=member, sport_association=association)
            category = seed(PaymentCategory, name='Quota speciale', sport_association=association)
            extra = seed(PaymentCategory, name='Quota aggiuntiva', sport_association=association)
            shared = seed(PaymentCategory, name='Causale condivisa', archived=True, deleted=True)
            seed(InstanceConfiguration, domain='payment-info-browser.example.test', name='Checkout Browser Fixture',
                 primary_association=association, support_email='support@example.test', logo_path='/api/instance/logo.png')
            def payment(subject, amount, **values):
                return seed(Payment, sport_association=association, user=athlete, associate=member,
                            subject=subject, amount=Decimal(amount), payment_category=category,
                            description='Browser payment info read fixture', **values)
            other = payment(0, '123.40', meta_payment_categories=[
                {'payment_category_id': str(extra.pk), 'amount': '10.25'},
                {'payment_category_id': str(shared.pk), 'amount': '20.50'},
                {'payment_category_id': str(extra.pk), 'amount': '3.00'}])
            membership = payment(1, '50.00')
            direct = payment(2, '56.70')
            course = seed(Course, sport_association=association, title='Yoga del mattino', fee=Decimal('777.12'))
            seed(CourseSubscription, course=course, payment=direct, subscription=subscription)
            carnet_payment = payment(2, '89.10')
            carnet = seed(Carnet, sport_association=association, title='Dieci ingressi', fee=Decimal('888.34'))
            seed(CarnetSubscription, carnet_id=carnet, payment=carnet_payment, subscription=subscription)
            cases = [
                dict(name='other-meta', id=str(other.pk), subject=0, amount='123.40',
                     heading='Altro pagamento Quota speciale', additional=True,
                     metadata=[['Quota aggiuntiva', '€10,25 x 1'], ['Causale condivisa', '€20,50 x 1'], ['Quota aggiuntiva', '€3,00 x 1']]),
                dict(name='subscription', id=str(membership.pk), subject=1, amount='50.00',
                     heading='Iscrizione a Checkout Browser ASD', additional=True, metadata=[]),
                dict(name='course', id=str(direct.pk), subject=2, amount='56.70',
                     heading='Yoga del mattino di Checkout Browser ASD', additional=False, metadata=[]),
                dict(name='carnet', id=str(carnet_payment.pk), subject=2, amount='89.10',
                     heading='Carnet (Dieci ingressi) di Checkout Browser ASD', additional=False, metadata=[]),
            ]
            logo = Path(settings.MEDIA_ROOT)/'instance/logo.png'
            logo.parent.mkdir(parents=True, exist_ok=True)
            logo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM0lT3EwMDAxAAGAAyNARjvuuOSAAAAAElFTkSuQmCC'))
            from application.utils.stripe_utils import online_payments_available, stripe_public_key
            assert not owner.online_payments and not online_payments_available(association) and stripe_public_key() == ''
            baseline = snapshot()
            from core.asgi import application
            def state_check():
                try:
                    assert snapshot() == baseline, 'Browser case changed managed state'
                finally:
                    connections.close_all()
            observer = CheckoutObserver(application, [case['id'] for case in cases], state_check)
            server = uvicorn.Server(uvicorn.Config(observer, host='0.0.0.0', port=0, lifespan='off', log_level='critical', access_log=False))
            thread = threading.Thread(target=server.run, daemon=True)
            thread.start()
            poll(lambda: server.started, bool, timeout=20)
            django_port = server.servers[0].sockets[0].getsockname()[1]
            codec_path = Path('pkg/storage/loginimage/native')
            assert fingerprints(options.go_source_root/codec_path) == fingerprints(ROOT/'BE_GO'/codec_path)
            build_codec(work/'image-codec', log, tool_env)
            subprocess.run(['go', 'build', *(['-overlay', str(options.go_overlay)] if options.go_overlay else []),
                            '-o', str(work/'payment-info-browser-go'), './cmd/server'],
                           cwd=options.go_source_root, env=tool_env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            go_port = free_port()
            environment = {**tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': DATABASE, 'DBUSER': 'contracts', 'DBPASSWORD': 'test-only',
                'DBHOST': str(work), 'DBPORT': '5432', 'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(cache_port),
                'REDIS_PASSWORD': cache_password, 'GO_LISTEN_ADDR': f'0.0.0.0:{go_port}',
                'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False', 'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
                **{key: str(value) for key, value in runtime.items()},
                **{key: getattr(settings, key) for key in ('SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY',
                    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME',
                    'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')}}
            go_process = subprocess.Popen([str(work/'payment-info-browser-go')], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)
            def go_ready():
                if go_process.poll() is not None: raise AssertionError('Private Go server exited')
                try:
                    with socket.create_connection(('127.0.0.1', go_port), timeout=.2): return True
                except OSError: return False
            poll(go_ready, bool, timeout=20)
            observer.begin('misroute-negative-control')
            with versioned_proxy(f'host.docker.internal:{django_port}', f'host.docker.internal:{django_port}', log,
                                 network=network, network_alias='payment-info-versioned'):
                with gateway(work, log, options.ui_dir, django_port, 'payment-info-versioned:80', 'v2', network=network) as (origin, _):
                    status, body, headers = Client(origin).request('GET', '/api/payment/'+cases[0]['id']+'/info', include_headers=True)
                    assert status == 200 and body['data']['amount'] == '123.40' and headers.get('X-Fixture-Backend') == 'go-v2'
            rejected = False
            try: observer.assert_no_selected('misroute-negative-control')
            except AssertionError: rejected = True
            assert rejected and len(observer.snapshot('misroute-negative-control')) == 1
            assert snapshot() == baseline, 'Misroute negative control changed managed state'
            result['routing_negative_control'] = dict(success=True, deliberately_misrouted_v2_to='Django',
                misleading_gateway_header='go-v2', actual_status=status, independent_observer_rejected=True,
                managed_state_unchanged=True, requests=observer.snapshot('misroute-negative-control'))
            with versioned_proxy(f'host.docker.internal:{django_port}', f'host.docker.internal:{go_port}', log,
                                 network=network, network_alias='payment-info-versioned') as (_, image):
                for version in ('v1', 'v2'):
                    cache.clear()
                    observer.begin(version)
                    target = output/version
                    target.mkdir()
                    receipt = dict(success=False, managed_state_unchanged=False)
                    result['versions'][version] = receipt
                    with gateway(work, log, options.ui_dir, django_port, 'payment-info-versioned:80', version, network=network) as (origin, routing):
                        observed_before = observer.snapshot(version)
                        unknown_status, _, unknown_headers = Client(origin).request('GET', '/api/__unsupported-payment-info-probe', include_headers=True)
                        assert unknown_status == 502 and unknown_headers.get('X-Fixture-Backend') == 'unsupported-fixture-route'
                        assert observer.snapshot(version) == observed_before, 'Unknown route fell through to Django'
                        receipt['unknown_route_rejected_without_fallback'] = True
                        fixture = dict(origin=origin, version=version, cases=cases,
                            playwright_module=str(options.playwright_module), selected_routes=list(SELECTED_ROUTES),
                            coexistence_routes=list(COEXISTENCE_ROUTES))
                        fixture_path = work/(version+'-browser.json')
                        fixture_path.write_text(json.dumps(fixture)); fixture_path.chmod(0o600)
                        try:
                            subprocess.run(['node', str(ROOT/'TESTS/browser/payment-info.mjs')], cwd=ROOT,
                                env={**tool_env, 'TESTS_FIXTURE': str(fixture_path), 'TESTS_OUTPUT': str(target)},
                                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=200)
                            browser = json.loads((target/'browser.json').read_text())
                            assert browser['success'] and len(browser['cases']) == 8
                            observed = observer.snapshot(version)
                            info_reads = [row for row in observed if re.fullmatch(r'/payment/[^/]+/info', row['path'])]
                            if version == 'v2': observer.assert_no_selected(version)
                            else:
                                assert len(info_reads) == 8 and all(row['status'] == 200 for row in info_reads)
                                for path in ('/instance/status', '/instance/config'):
                                    assert any(row['path'] == path and row['status'] == 200 for row in observed)
                            intents = [row for row in observed if row['path'].startswith('/stripe/pay/')]
                            assert len(intents) == 8 and all(row['status'] == 409 for row in intents)
                            gates = [gate for gate in observer.gate_receipt() if gate['label'] == version]
                            assert len(gates) == 8 and all(g['entered'] and g['released'] and g['completed'] and not g['timed_out'] and g['status'] == 409 and g.get('managed_state_unchanged') for g in gates)
                            assert snapshot() == baseline, 'Browser workflow changed managed state'
                            receipt.update(success=True, managed_state_unchanged=True, exact_audits_unchanged=True,
                                authored_summary_checks=8, real_disabled_stripe_rejections=8,
                                enabled_checkout_verified=False, routing=routing, shipped_caddy_image=image,
                                django_observer=observed, gates=gates)
                            print('PASS: '+version+' four real checkout summaries and revisits; eight actual Stripe409s; no managed mutation', flush=True)
                        finally:
                            fixture_path.unlink(missing_ok=True)
                            observer.release_all()
                            receipt['django_observer'] = observer.snapshot(version)
                            receipt['gates'] = [g for g in observer.gate_receipt() if g['label'] == version]
                            receipt['managed_state_unchanged'] = snapshot() == baseline
                            receipt['success'] = bool(receipt['success'] and receipt['managed_state_unchanged'])
                            (target/'validation.json').write_text(json.dumps(receipt, indent=2)+'\n')
    finally:
        if observer:
            observer.release_all()
            result['gate_history'] = observer.gate_receipt()
        try:
            if go_process:
                go_process.terminate()
                try: go_process.wait(timeout=30)
                except subprocess.TimeoutExpired: go_process.kill(); go_process.wait(timeout=10)
            if server: server.should_exit = True
            if thread:
                thread.join(timeout=30)
                assert not thread.is_alive(), 'Django ASGI thread did not stop'
        finally:
            try:
                with disable_auditlog():
                    for model, pk in reversed(created):
                        model._base_manager.filter(pk=pk).delete()
                result['managed_cleanup_verified'] = snapshot() == original
                assert result['managed_cleanup_verified'], 'Fixture cleanup changed unrelated managed rows'
            finally:
                try:
                    connections.close_all()
                finally:
                    try:
                        if redis_process:
                            redis_process.terminate()
                            try: redis_process.wait(timeout=10)
                            except subprocess.TimeoutExpired: redis_process.kill(); redis_process.wait(timeout=10)
                    finally:
                        result['private_services_stopped'] = not (go_process and go_process.poll() is None) and not (thread and thread.is_alive()) and not (redis_process and redis_process.poll() is None)

def source_hashes():
    return {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
        'TESTS/adapters/django/payment_info_browser.py', 'TESTS/browser/payment-info.mjs',
        'TESTS/adapters/django/profile_browser.py', 'TESTS/adapters/caddy.py',
        'TESTS/adapters/django/settings.py', 'selfhost/caddy/Caddyfile')}

def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-dir', type=Path, required=True, help='Existing unmodified built UI dist/public directory')
    parser.add_argument('--ui-provenance', type=Path, required=True, help='Immutable-image artifact hash manifest')
    parser.add_argument('--prepare-ui-image', help='Preparation only: extract /srv from an existing image, verify workflow source maps and write provenance')
    parser.add_argument('--go-source-root', type=Path, default=ROOT/'BE_GO', help='Explicit Go module source tree; supports an explicitly pinned comparison tree')
    parser.add_argument('--output', type=Path, default=ROOT/'quality-reports/go-migration/payment-info-browser')
    parser.add_argument('--playwright-module', type=Path, default=ROOT/'selfhost/tests/browser/node_modules/@playwright/test/index.mjs')
    parser.add_argument('--go-overlay', type=Path, help='Explicit ignored Go build overlay for route-regression negative control')
    options = parser.parse_args(); options.ui_dir = options.ui_dir.resolve(); options.playwright_module = options.playwright_module.resolve()
    if options.prepare_ui_image:
        sources = WORKFLOW_SOURCES
        prepare_ui(options.prepare_ui_image, options.ui_dir, options.ui_provenance.resolve(), sources)
        return
    require_production_python()
    options.go_source_root = options.go_source_root.resolve()
    options.cache_port = free_port()
    assert (options.ui_dir/'index.html').is_file() and options.playwright_module.is_file(), 'Existing UI and pinned Playwright installation required'
    output = options.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    assert not (output/'results.json').exists(), 'Use a fresh output directory; preserve previous evidence'
    if options.go_overlay: options.go_overlay = options.go_overlay.resolve()
    provenance_bytes = options.ui_provenance.read_bytes()
    provenance = json.loads(provenance_bytes)
    ui_hashes = fingerprints(options.ui_dir)
    assert provenance['success'] and provenance['extraction_container_never_started'] and provenance['extraction_container_removed']
    assert provenance['files'] == ui_hashes, 'Frontend does not match its reviewed immutable artifact'
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', provenance['image_id']), 'Immutable frontend image required'
    for source in WORKFLOW_SOURCES:
        matches = provenance['workflow_source_maps'].get(source)
        assert matches, 'Checkout workflow source provenance missing: '+source
        assert all(item['embedded_sha256'] == hashlib.sha256((ROOT/'UI'/source).read_bytes()).hexdigest() for item in matches)
    result = {'success': False, 'scope': __doc__.strip(), 'ui_artifact_hashes': ui_hashes,
        'ui_image_id': provenance['image_id'], 'ui_provenance_sha256': hashlib.sha256(provenance_bytes).hexdigest(),
        'go_source_root': str(options.go_source_root), 'go_source_hashes': fingerprints(options.go_source_root),
        'complete_payment_info_parity': False, 'enabled_checkout_verified': False, 'security_semantics_approved': False,
        'source_hashes': source_hashes(), 'go_overlay': str(options.go_overlay) if options.go_overlay else None}
    report = output/'results.json'; report.write_text(json.dumps(result, indent=2)+'\n')
    postgres = shutil.which('postgres') or '/opt/homebrew/opt/postgresql@16/bin/postgres'
    assert Path(postgres).is_file() and shutil.which('redis-server'), 'Private PostgreSQL and Redis required'
    binaries = Path(postgres).parent
    tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'PLAYWRIGHT_BROWSERS_PATH')}
    tool_env.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-payment-info-browser-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
        work = Path(directory); started = False
        def command(args): return subprocess.run([str(x) for x in args], env=tool_env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=180)
        try:
            command([binaries/'initdb', '-D', work/'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            started = True
            command([binaries/'pg_ctl', '-D', work/'postgres', '-l', work/'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            command([binaries/'createdb', '-h', work, '-U', 'contracts', DATABASE])
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            key = Ed25519PrivateKey.generate()
            import environ
            os.environ.clear(); os.environ.update(tool_env); environ.Env.read_env(str(ROOT/'selfhost/.env.example'))
            os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode(),
                JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
                DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings', TESTS_DATABASE=DATABASE, TESTS_WORKDIR=str(work),
                DBTYPE='POSTGRES', DBHOST=str(work), DBPORT='5432', DBNAME=DATABASE, DBUSER='contracts', DBPASSWORD='test-only',
                USE_PGBOUNCER='False', STORE_ON_DISK='True',
                REDIS_HOST='127.0.0.1', REDIS_PORT=str(options.cache_port), REDIS_PASSWORD='private-payment-info-browser-cache',
                REDIS_USERNAME='', REDIS_SSLAUTH='')
            import django
            with patch('environ.Env.read_env'): django.setup()
            from django.apps import apps
            from django.conf import settings
            from django.core.management import call_command
            settings.MIGRATION_MODULES = {app.label: None for app in apps.get_app_configs()}
            call_command('migrate', interactive=False, verbosity=0, run_syncdb=True, stdout=log, stderr=log)
            from application.audit_resolvers import setup_audit_resolvers
            setup_audit_resolvers()
            with proxy_network(result) as network:
                exercise(work, output, log, options, tool_env, network, result)
            result['private_proxy_network_removed'] = True
        except Exception as error:
            result['failure'] = {'class': type(error).__name__, 'message': str(error)}
            raise
        finally:
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                try:
                    if started:
                        stopped = subprocess.run([str(binaries/'pg_ctl'), '-D', str(work/'postgres'), '-m', 'immediate', '-w', 'stop'], stdout=log, stderr=subprocess.STDOUT, timeout=30)
                        result['private_postgres_stopped'] = stopped.returncode == 0 or not (work/'postgres/postmaster.pid').exists()
                        assert result['private_postgres_stopped'], 'Private PostgreSQL cleanup failed'
                finally: report.write_text(json.dumps(result, indent=2)+'\n')
    try:
        assert fingerprints(options.ui_dir) == result['ui_artifact_hashes'], 'Built frontend changed during proof'
        assert fingerprints(options.go_source_root) == result['go_source_hashes'], 'Selected Go source changed during proof'
        assert source_hashes() == result['source_hashes'], 'Browser fixture sources changed during proof'
        assert result['managed_cleanup_verified'] and result['private_services_stopped'] and result['private_postgres_stopped']
        result.update(success=True, current_model_schema_only=True)
    except Exception as error:
        result['success'] = False
        result['failure'] = {'class': type(error).__name__, 'message': str(error)}
        raise
    finally:
        report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'success': True, 'versions': list(result['versions']), 'private_services_stopped': True}))


if __name__ == '__main__':
    main()
