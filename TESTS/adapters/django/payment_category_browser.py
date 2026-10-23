"""Unchanged category/payment frontend against real versioned Django/Go roots.

This private proxy selects the API implementation only for the named workflow.
Canonical logo/manifest/export-denial resources and root WebSockets remain Django.
No installed alias switch, Celery handoff, full UI parity or historical migration
is claimed. Browser authentication always starts with a real password login.
"""
import argparse
import base64
import contextlib
from datetime import timedelta, timezone as datetime_timezone
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
DATABASE = 'assozeta_contract_payment_category_browser'
SELECTED_ROUTES = (
    'instance/status', 'instance/config', 'instance/access', 'oauth2/login',
    'oauth2/refresh-token', 'profile/info', 'profile/settings/tables',
    'payment/category/list', 'payment/category/add', 'payment/category/*/update', 'payment/category/*/delete',
)
COEXISTENCE_ROUTES = (
    'instance/manifest.json', 'instance/logo.png', 'association/export/active',
    'payment/list', 'payment/stats', 'payment/add', 'balance-sheet/accounts/list',
    'personas/all', 'supplier/list', 'instructor/list', 'course/list',
)
from TESTS.adapters.django.profile_browser import prepare_ui, free_port, fingerprints
from TESTS.adapters.django.profile_browser import WORKFLOW_SOURCES as PROFILE_SOURCES
WORKFLOW_SOURCES = PROFILE_SOURCES + (
    'src/routes/accounting/payment/category/Categories.svelte',
    'src/routes/accounting/payment/category/modals/EditModal.svelte',
    'src/routes/accounting/payment/category/catgoryUtils.js',
    'src/routes/accounting/payment/PaymentList.svelte',
    'src/routes/accounting/payment/modals/AddEditModal.svelte',
    'src/routes/accounting/payment/modals/partials/meta-payment-categories.svelte',
    'src/routes/accounting/payment/PaymentDrawer.svelte',
)

class DjangoTrafficObserver:
    """Observe real ASGI requests without changing requests or responses.

    The gateway header describes intended routing only. This separate observer
    detects a selected v2 request actually reaching Django, including a silent
    fallback with successful and apparently equivalent business responses.
    """
    def __init__(self, application):
        self.application = application
        self.lock = threading.Lock()
        self.label = 'startup'
        self.requests = {}

    def begin(self, label):
        with self.lock:
            self.label = label
            self.requests[label] = {}

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http':
            path = scope['path'].lstrip('/')
            coexistence = path in COEXISTENCE_ROUTES
            selected = not coexistence and path.startswith(('payment/category/', 'oauth2/', 'profile/', 'instance/'))
            classification = 'selected' if selected else ('coexistence' if coexistence else 'other')
            # No query strings, headers, credentials or bodies enter the receipt.
            key = (classification, scope['method'], '/' + path)
            with self.lock:
                counts = self.requests.setdefault(self.label, {})
                counts[key] = counts.get(key, 0) + 1
        await self.application(scope, receive, send)

    def snapshot(self, label):
        with self.lock:
            rows = [{'classification': category, 'method': method, 'path': path, 'count': count}
                    for (category, method, path), count in sorted(self.requests[label].items())]
        return {'selected_requests': sum(row['count'] for row in rows if row['classification'] == 'selected'),
                'coexistence_requests': sum(row['count'] for row in rows if row['classification'] == 'coexistence'),
                'requests': rows}

    def assert_no_selected(self, label):
        assert self.snapshot(label)['selected_requests'] == 0, 'Selected v2 traffic reached real Django'


@contextlib.contextmanager
def gateway(work, log, ui, django_port, versioned_origin, version, selected_routes=SELECTED_ROUTES):
    """Exact allowlist routing; unknown API paths fail instead of falling back."""
    image = re.search(r'^FROM (caddy:\S+) AS production$', (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
    name = 'az-category-browser-' + uuid.uuid4().hex
    target = int(versioned_origin.rsplit(':', 1)[1])
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
  @selected path SELECTED
  handle @selected {
   header X-Fixture-Backend LABEL
   uri replace /api/ /api/VERSION/ 1
   reverse_proxy host.docker.internal:TARGET
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
    ' '.join('/api/' + p for p in COEXISTENCE_ROUTES)).replace('LABEL', label).replace('VERSION', version).replace('TARGET', str(target)).replace('DJANGO', str(django_port))
    path = work / (version + '.Caddyfile'); path.write_text(config)
    def docker(*args, check=True):
        return subprocess.run(['docker', *map(str, args)], check=check, capture_output=True, text=True, timeout=60)
    # Missing preexisting image fails; this runner never builds or pulls images.
    image_id = json.loads(docker('image', 'inspect', image).stdout)[0]['Id']
    try:
        docker('run', '--detach', '--name', name, '--label', 'org.assozeta.category-browser=true',
               '--add-host', 'host.docker.internal:host-gateway', '--publish', '127.0.0.1::80',
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
                       'selected_backend': label, 'canonical_api_alias_is_private_test_routing': True, 'selected_versioned_prefix': '/api/'+version, 'selected_routes': list(selected_routes), 'django_coexistence_routes': list(COEXISTENCE_ROUTES)}
    finally:
        # No access log is enabled: URLs containing JWT query parameters and
        # Authorization/cookie headers must not enter persistent proof output.
        docker('rm', '-f', name, check=False)
        remaining = docker('container', 'ls', '--all', '--format', '{{.Names}}', '--filter', 'name=' + name).stdout.splitlines()
        assert name not in remaining, 'Private proxy cleanup failed'

def exercise(work, output, log, options, tool_env):
    import redis
    import uvicorn
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.core.cache import cache
    from django.db import connections
    from django.test import override_settings
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration, PaymentCategory, VatManagement, Payment, CustomAccounts
    from application.models.audit_models import AuditLogIndex
    from instance.models import InstanceConfiguration
    from TESTS.http_client import poll
    from TESTS.adapters.django.login_jpeg import build_codec

    cache_port = options.cache_port
    redis_process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(cache_port),
        '--save', '', '--appendonly', 'no', '--requirepass', 'private-category-browser-cache', '--dir', str(work)],
        stdout=log, stderr=subprocess.STDOUT)
    cache_client = redis.Redis(host='127.0.0.1', port=cache_port, db=1, password='private-category-browser-cache')
    server = thread = go_process = None
    runtime = dict(RUNNING_VERSION='category-browser-fixture', AI_API_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',
        SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', SOCIAL_AUTH_APPLE_ID_CLIENT='', STRIPE_KEY='', STRIPE_PUBLIC_KEY='',
        STRIPE_WEBHOOK_SECRET='', MANUAL_CORPUS_BASE_URL='', MANUAL_RUN_ID='', MANUAL_SOURCE_ROOT='',
        MANUAL_BUNDLE_PATH=str(work/'absent-manual'), MANUAL_BUNDLE_REQUIRED=False,
        MANUAL_INDEX_PATH=str(work/'absent-index.json'), MANUAL_DEVELOPMENT_ROOT=str(work/'absent-development'),
        FAMILY_REGISTRATION_ENABLED=True, FEDERATION_REGISTRATION_ENABLED=False, MULTIPLE_REGISTRATION_ENABLED=False)
    caches = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': f'redis://127.0.0.1:{cache_port}/1',
                         'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': 'private-category-browser-cache'}}}
    layers = {'default': {'BACKEND': 'channels_redis.core.RedisChannelLayer', 'CONFIG': {
        'hosts': [f'redis://:private-category-browser-cache@127.0.0.1:{cache_port}/2']}}}
    password = 'private-category-browser-password'
    reports = {}
    try:
        def cache_ready():
            try: return cache_client.ping()
            except redis.ConnectionError: return False
        poll(cache_ready, bool, timeout=15)
        with override_settings(**runtime, CACHES=caches, CHANNEL_LAYERS=layers, PASSWORD_HASHERS=PASSWORD_HASHERS):
            # Import the actual protocol router, including health and authenticated
            # notification consumers, after the private settings are in force.
            from core.asgi import application
            observer = DjangoTrafficObserver(application)
            server = uvicorn.Server(uvicorn.Config(observer, host='0.0.0.0', port=0, lifespan='off', log_level='critical', access_log=False))
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            poll(lambda: server.started, bool, timeout=20)
            django_port = server.servers[0].sockets[0].getsockname()[1]
            # Reuse the native codec fixture only when its exact source is the
            # same as the selected Go tree; never silently mix prior/current code.
            codec_path = Path('pkg/storage/loginimage/native')
            assert fingerprints(options.go_source_root/codec_path) == fingerprints(ROOT/'BE_GO'/codec_path)
            build_codec(work/'image-codec', log, tool_env)
            subprocess.run(['go', 'build', '-o', str(work/'category-browser-go'), './cmd/server'],
                cwd=options.go_source_root, env=tool_env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
            go_port = free_port()
            environment = {**tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': DATABASE, 'DBUSER': 'contracts', 'DBPASSWORD': 'test-only',
                'DBHOST': str(work), 'DBPORT': '5432', 'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(cache_port),
                'REDIS_PASSWORD': 'private-category-browser-cache', 'GO_LISTEN_ADDR': f'0.0.0.0:{go_port}',
                'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False', 'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
                **{key: str(value) for key, value in runtime.items()},
                **{key: getattr(settings, key) for key in ('SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY',
                    'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME',
                    'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')}}
            go_process = subprocess.Popen([str(work/'category-browser-go')], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)
            def go_ready():
                if go_process.poll() is not None: raise AssertionError('Private Go server exited')
                try:
                    with socket.create_connection(('127.0.0.1', go_port), timeout=.2): return True
                except OSError: return False
            poll(go_ready, bool, timeout=20)
            with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS): encoded = make_password(password)
            assert encoded.startswith('pbkdf2_sha256$'), 'Browser fixture requires actual supported production password hashing'
            from TESTS.adapters.caddy import proxy as versioned_proxy
            with disable_auditlog():
                owner = User.original_objects.create(username='CATEGORY-OWNER', email='category-owner@example.test',
                    role=User.ASSOCIATION, password='!unused', first_name='Owner', last_name='Fixture',
                    date_joined=timezone.now()-timedelta(days=60), auto_paid_payment=False)
                association = SportAssociation.original_objects.create(user=owner, denomination='Category Browser ASD',
                    tax_code='12345678901', additional_sections=[])
                SportAssociationMembershipCardConfiguration.objects.create(sport_association=association)
                permissions = ['bookeeping.payments.read', 'bookeeping.payments.update', 'bookeeping.payments.delete',
                    'bookeeping.payments.create', 'bookeeping.management.accounts.read', 'association.personas.read',
                    'bookeeping.management.suppliers.read', 'association.instructor.read', 'association.courses.read']
                def collaborator(username, grants):
                    return User.original_objects.create(username=username, email=username.lower()+'@example.test',
                        role=User.COLLABORATOR, password=encoded, first_name=username, last_name='Fixture', connected_user=owner,
                        collaborator_role=User.CUSTOM_COLLABORATOR_ROLE, collaborator_permissions=grants,
                        date_joined=timezone.now()-timedelta(days=60))
                writer = collaborator('CATEGORY-WRITER', permissions)
                reader = collaborator('CATEGORY-READER', ['bookeeping.payments.read'])
                InstanceConfiguration.objects.create(domain='category-browser.example.test', name='Category Browser Fixture',
                    primary_association=association, support_email='support@example.test', logo_path='/api/instance/logo.png')
                account = CustomAccounts.objects.create(name='Browser Cash', sport_association=association,
                    initial_balance=0, account_type=CustomAccounts.CASH, account_code='BROWSER-CASH')
                global_category = PaymentCategory.objects.create(name='entrate e proventi da attività tipiche',
                    vat_management=VatManagement.objects.create(vat=1, deduction=0))
                foreign_owner = User.original_objects.create(username='CATEGORY-FOREIGN', email='category-foreign@example.test',
                    role=User.ASSOCIATION, password='!unused', first_name='Other', last_name='Association')
                foreign_association = SportAssociation.original_objects.create(user=foreign_owner,
                    denomination='Other Category ASD', tax_code='98765432109', additional_sections=[])
                foreign_category = PaymentCategory.objects.create(name='Foreign category must stay hidden',
                    sport_association=foreign_association, vat_management=VatManagement.objects.create(vat=1, deduction=0))
                PaymentCategory.objects.create(name='Browser baseline category', sport_association=association,
                    vat_management=VatManagement.objects.create(vat=1, deduction=0))
            logo = Path(settings.MEDIA_ROOT)/'instance/logo.png'; logo.parent.mkdir(parents=True, exist_ok=True)
            logo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM0lT3EwMDAxAAGAAyNARjvuuOSAAAAAElFTkSuQmCC'))
            # Deliberately miswire the v2 upstream to actual Django. The gateway
            # still stamps go-v2 and the valid response is untouched; the observer
            # must independently reject this false-positive routing condition.
            observer.begin('v2-fallback-negative-control')
            from TESTS.http_client import Client
            with versioned_proxy(f'host.docker.internal:{django_port}', f'host.docker.internal:{django_port}', log) as (wrong_origin, _):
                with gateway(work, log, options.ui_dir, django_port, wrong_origin, 'v2') as (origin, _):
                    status, _, headers = Client(origin).request('GET', '/api/instance/status', include_headers=True)
                    assert status == 200 and headers.get('X-Fixture-Backend') == 'go-v2'
            detected = False
            try:
                observer.assert_no_selected('v2-fallback-negative-control')
            except AssertionError:
                detected = True
            assert detected, 'Independent Django observer accepted deliberately misrouted v2 traffic'
            negative_control = {'success': True, 'deliberate_v2_upstream': 'real Django',
                'misleading_gateway_header': 'go-v2', 'real_response_status': status,
                'independent_observer_rejected': detected,
                'django_observer': observer.snapshot('v2-fallback-negative-control')}
            assert negative_control['django_observer']['selected_requests'] == 1
            (output/'routing-negative-control.json').write_text(json.dumps(negative_control, indent=2)+'\n')
            with versioned_proxy(f'host.docker.internal:{django_port}', f'host.docker.internal:{go_port}', log) as (versioned_origin, image):
                for version in ('v1', 'v2'):
                    baseline_categories = list(PaymentCategory.objects.values().order_by('pk'))
                    baseline_payments = list(Payment._base_manager.values().order_by('pk'))
                    baseline_vat = list(VatManagement.objects.values().order_by('pk'))
                    cache.clear()
                    audit_baseline = list(LogEntry.objects.values().order_by('pk'))
                    audit_before = {row['id'] for row in audit_baseline}
                    index_baseline = list(AuditLogIndex.objects.values().order_by('pk'))
                    observer.begin(version)
                    with gateway(work, log, options.ui_dir, django_port, versioned_origin, version) as (origin, routing):
                        target = output/version; target.mkdir(exist_ok=True)
                        for stale in ('browser.json', 'validation.json'): (target/stale).unlink(missing_ok=True)
                        fixture = dict(origin=origin, version=version, username=writer.username, password=password,
                            user_id=str(writer.pk), reader_username=reader.username, reader_user_id=str(reader.pk),
                            association_id=str(association.pk), shared_category_id=str(global_category.pk),
                            foreign_category_id=str(foreign_category.pk),
                            account_id=str(account.pk), created_name='Browser category '+version,
                            changed_name='Browser edited '+version, payment_description='Browser selection '+version,
                            playwright_module=str(options.playwright_module), selected_routes=list(SELECTED_ROUTES),
                            coexistence_routes=list(COEXISTENCE_ROUTES))
                        fixture_path = work/(version+'-browser.json'); fixture_path.write_text(json.dumps(fixture)); fixture_path.chmod(0o600)
                        try:
                            subprocess.run(['node', str(ROOT/'TESTS/browser/payment-categories.mjs')], cwd=ROOT,
                                env={**tool_env, 'TESTS_FIXTURE': str(fixture_path), 'TESTS_OUTPUT': str(target)},
                                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=240)
                        finally: fixture_path.unlink(missing_ok=True)
                        browser = json.loads((target/'browser.json').read_text()); assert browser['success']
                        observed = observer.snapshot(version)
                        assert observed['coexistence_requests'] > 0, 'Expected actual Django coexistence traffic'
                        if version == 'v2':
                            observer.assert_no_selected(version)
                        else:
                            for prefix in ('/payment/category/', '/oauth2/', '/profile/', '/instance/'):
                                assert any(row['classification'] == 'selected' and row['path'].startswith(prefix) for row in observed['requests']), prefix
                        category = PaymentCategory.objects.get(pk=browser['category_id'])
                        payment = Payment._base_manager.get(pk=browser['payment_id'])
                        assert category.name == fixture['changed_name'] and category.type == 2
                        assert category.deleted and not category.archived and not category.expense and not category.tax_deductible
                        initial_vat = VatManagement.objects.get(pk=browser['initial_vat_id'])
                        assert (initial_vat.vat, initial_vat.deduction) == (1, 0)
                        assert initial_vat.pk != category.vat_management_id
                        assert category.sport_association_id == association.pk
                        assert (category.vat_management.vat, category.vat_management.deduction) == (16, 2)
                        assert payment.payment_category_id == category.pk and payment.sport_association_id == association.pk
                        assert payment.description == fixture['payment_description'] and str(payment.amount) == '12.50'
                        assert payment.custom_accounts_id == account.pk and not payment.deleted
                        assert list(PaymentCategory.objects.filter(pk__in=[x['payment_category_id'] for x in baseline_categories]).values().order_by('pk')) == baseline_categories
                        assert list(Payment._base_manager.filter(pk__in=[x['payment_id'] for x in baseline_payments]).values().order_by('pk')) == baseline_payments
                        assert PaymentCategory.objects.count() == len(baseline_categories) + 1
                        assert Payment._base_manager.count() == len(baseline_payments) + 1
                        assert VatManagement.objects.count() == len(baseline_vat) + 2
                        assert list(VatManagement.objects.filter(pk__in=[x['vat_management_id'] for x in baseline_vat]).values().order_by('pk')) == baseline_vat
                        assert list(LogEntry.objects.filter(pk__in=audit_before).values().order_by('pk')) == audit_baseline
                        assert list(AuditLogIndex.objects.filter(pk__in=[row['audit_log_index_id'] for row in index_baseline]).values().order_by('pk')) == index_baseline
                        events = list(LogEntry.objects.exclude(pk__in=audit_before).filter(
                            content_type__app_label='application', content_type__model='paymentcategory').order_by('id'))
                        assert len(events) == 5, 'Expected create, edit, archive, unarchive and deletion category events'
                        assert [event.action for event in events] == [0,1,1,1,1]
                        assert all(event.object_pk == str(category.pk) for event in events), 'Unexpected foreign/global category audit after denied writes'
                        creation_values = {
                            'payment_category_id': str(category.pk), 'name': fixture['created_name'],
                            'creation_date': str(category.creation_date.astimezone(datetime_timezone.utc).replace(tzinfo=None)),
                            'sport_association': str(association.pk), 'expense': 'False', 'tax_deductible': 'False',
                            'archived': 'False', 'type': '1', 'vat_management': str(initial_vat.pk), 'deleted': 'False',
                            'users_default_category': 'application.User.None',
                            'users_default_category_courses': 'application.User.None',
                        }
                        assert events[0].changes_dict == {key: ['None', value] for key, value in creation_values.items()}
                        assert events[1].changes_dict == {'name':[fixture['created_name'],fixture['changed_name']],
                            'type':['1','2'], 'vat_management':[browser['initial_vat_id'],str(category.vat_management_id)]}
                        assert events[2].changes_dict == {'archived':['False','True']}
                        assert events[3].changes_dict == {'archived':['True','False']}
                        assert events[4].changes_dict == {'deleted':['False','True']}
                        for event in events:
                            assert event.actor_id == writer.pk and event.content_type.model == 'paymentcategory'
                            assert list(AuditLogIndex.objects.filter(log_entry_id=event.pk).values_list('sport_association_id','resolution_path')) == [(association.pk,'direct')]
                        reports[version] = {'success': True, 'routing': routing, 'shipped_versioned_caddy_image': image,
                            'real_password_login': True, 'database_category_payment_and_audit_readback': True,
                            'category_audit_events': len(events), 'all_new_category_audits_exact': True,
                            'baseline_audits_and_indexes_preserved': True, 'initial_vat_values_and_retention_verified': True,
                            'independent_django_observer': observed, 'payment_reference_survives_soft_delete': True,
                            'baseline_categories_payments_vat_preserved_and_exact_counts': True,
                            'foreign_category_invisible_and_write_denied': True, 'browser_report': str((target/'browser.json').relative_to(output)),
                            'archive_control_limit': 'No unchanged category archive UI exists; real authenticated PATCH plus visible reload verified.'}
                        (target/'validation.json').write_text(json.dumps(reports[version], indent=2)+'\n')
                        print('PASS: '+version+' unchanged category UI and payment selection with real durable/audit readback', flush=True)
    finally:
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
            try: connections.close_all()
            finally:
                redis_process.terminate()
                try: redis_process.wait(timeout=10)
                except subprocess.TimeoutExpired: redis_process.kill(); redis_process.wait(timeout=10)
    return reports, negative_control

def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-dir', type=Path, required=True, help='Existing unmodified built UI dist/public directory')
    parser.add_argument('--ui-provenance', type=Path, required=True, help='Immutable-image artifact hash manifest')
    parser.add_argument('--prepare-ui-image', help='Preparation only: extract /srv from an existing image, verify workflow source maps and write provenance')
    parser.add_argument('--go-source-root', type=Path, default=ROOT/'BE_GO', help='Explicit Go module source tree; supports an explicitly pinned comparison tree')
    parser.add_argument('--output', type=Path, default=ROOT/'quality-reports/go-migration/payment-category-browser')
    parser.add_argument('--playwright-module', type=Path, default=ROOT/'selfhost/tests/browser/node_modules/@playwright/test/index.mjs')
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
    provenance_bytes = options.ui_provenance.read_bytes()
    provenance = json.loads(provenance_bytes)
    ui_hashes = fingerprints(options.ui_dir)
    assert provenance['success'] and provenance['extraction_container_never_started'] and provenance['extraction_container_removed']
    assert provenance['files'] == ui_hashes, 'Frontend does not match its reviewed immutable artifact'
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', provenance['image_id']), 'Immutable frontend image required'
    for source in WORKFLOW_SOURCES:
        matches = provenance['workflow_source_maps'].get(source)
        assert matches, 'Category/payment workflow source provenance missing: '+source
        assert all(item['embedded_sha256'] == hashlib.sha256((ROOT/'UI'/source).read_bytes()).hexdigest() for item in matches)
    result = {'success': False, 'scope': __doc__.strip(), 'ui_artifact_hashes': ui_hashes,
        'ui_image_id': provenance['image_id'], 'ui_provenance_sha256': hashlib.sha256(provenance_bytes).hexdigest(),
        'go_source_root': str(options.go_source_root), 'go_source_hashes': fingerprints(options.go_source_root)}
    report = output/'results.json'; report.write_text(json.dumps(result, indent=2)+'\n')
    postgres = shutil.which('postgres') or '/opt/homebrew/opt/postgresql@16/bin/postgres'
    assert Path(postgres).is_file() and shutil.which('redis-server'), 'Private PostgreSQL and Redis required'
    binaries = Path(postgres).parent
    tool_env = {key: value for key, value in os.environ.items() if key in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN', 'PLAYWRIGHT_BROWSERS_PATH')}
    tool_env.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-category-browser-', dir='/tmp') as directory, (output/'services.log').open('w') as log:
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
                REDIS_HOST='127.0.0.1', REDIS_PORT=str(options.cache_port), REDIS_PASSWORD='private-category-browser-cache',
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
            result['versions'], result['routing_negative_control'] = exercise(work, output, log, options, tool_env)
        finally:
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                try:
                    if started:
                        stopped = subprocess.run([str(binaries/'pg_ctl'), '-D', str(work/'postgres'), '-m', 'immediate', '-w', 'stop'], stdout=log, stderr=subprocess.STDOUT, timeout=30)
                        assert stopped.returncode == 0 or not (work/'postgres/postmaster.pid').exists(), 'Private PostgreSQL cleanup failed'
                finally: report.write_text(json.dumps(result, indent=2)+'\n')
    assert fingerprints(options.ui_dir) == result['ui_artifact_hashes'], 'Built frontend changed during proof'
    assert fingerprints(options.go_source_root) == result['go_source_hashes'], 'Selected Go source changed during proof'
    result.update(success=True, private_services_stopped=True, current_model_schema_only=True,
        source_hashes={p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in (
            'TESTS/adapters/django/payment_category_browser.py', 'TESTS/browser/payment-categories.mjs',
            'TESTS/adapters/django/profile_browser.py', 'TESTS/adapters/caddy.py', 'selfhost/caddy/Caddyfile')})
    report.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'success': True, 'versions': list(result['versions']), 'private_services_stopped': True}))


if __name__ == '__main__':
    main()
