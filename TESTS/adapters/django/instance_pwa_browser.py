"""Unchanged UI manifest/logo consumers, without installed PWA or backend selection claims.

Stock mode exercises the shipped /api alias (Django). Private v1/v2 modes route
only the four named public resources through shipped versioned Caddy routes.
The same extracted UI bytes are reused. External provider requests are blocked;
no business response, DOM configuration or runtime API host is fabricated.
"""
import argparse
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
from TESTS.adapters.django.profile_browser import prepare_ui, fingerprints
from TESTS.adapters.go.payment_category_image import ImageFixture, DATABASE
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.caddy import proxy
from TESTS.http_client import Client, poll

SELECTED = ('instance/status', 'instance/config', 'instance/manifest.json', 'instance/logo.png')
WORKFLOW_SOURCES = ('src/App.svelte', 'src/store/instanceStore.js', 'src/utils/BrandTheme.js',
    'src/routes/login/Login.svelte', 'src/routes.js', 'src/utils/HealthWebSocket.js',
    'src/utils/ApiMiddleware.js', 'src/store/loadingStore.js', 'src/components/loading/LoadingOverlay.svelte')
SOURCES = ('TESTS/adapters/django/instance_pwa_browser.py', 'TESTS/browser/instance-pwa.mjs',
    'TESTS/adapters/django/test_instance_pwa_browser.py',
    'TESTS/adapters/django/profile_browser.py', 'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/age_worker_fixture.py', 'TESTS/adapters/caddy.py', 'selfhost/caddy/Caddyfile')
MANIFEST = {'name': 'PWA Fixture Club', 'short_name': 'PWA Club', 'description': 'Private PWA browser fixture',
    'icons': [{'src': '/api/instance/logo.png', 'sizes': size, 'type': 'image/png'} for size in ('192x192', '512x512')],
    'start_url': '/', 'display': 'standalone', 'background_color': '#ffffff', 'theme_color': '#2468AC'}
TITLE = 'PWA fixture · Club'
LOGO_URL = '/api/instance/logo.png?v=20250102030405123456'


class BrowserImageFixture(ImageFixture):
    def run_container(self, suffix, image, *args):
        # The shared factory still checks the image's health definition and
        # invokes actual readiness/health endpoints. Avoid independent periodic
        # readiness SETs during the browser's exact Redis-state comparison.
        if suffix == 'server': args = (*args, '--no-healthcheck')
        return super().run_container(suffix, image, *args)


def source_hashes():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}


def validate_selected_upstream(mode, rows):
    if mode in ('stock', 'v1'):
        assert {row['path'].lstrip('/') for row in rows} == set(SELECTED)
        assert all(row['status'] == 200 and row['method'] == 'GET' for row in rows)
    else:
        assert mode == 'v2', 'Unknown upstream mode'
        assert not rows, 'upstream_mismatch'


class Observer:
    def __init__(self, application):
        self.application, self.rows, self.lock = application, [], threading.Lock()

    def clear(self):
        with self.lock: self.rows.clear()

    def snapshot(self):
        with self.lock: return [dict(row) for row in self.rows]

    async def __call__(self, scope, receive, send):
        row = None
        if scope['type'] == 'http':
            row = {'path': scope['path'], 'method': scope['method'], 'status': None}
            with self.lock: self.rows.append(row)
        async def observed(message):
            if row is not None and message['type'] == 'http.response.start': row['status'] = message['status']
            await send(message)
        await self.application(scope, receive, observed)


@contextmanager
def gateway(proof, work, ui, upstream, django_port, mode, misroute=None):
    """Private static UI wrapper; stock leaves the shipped canonical alias intact."""
    image = re.search(r'^FROM (caddy:\S+) AS production$', (ROOT / 'UI/Dockerfile').read_text(), re.M).group(1)
    image_id = proof.image(image)['Id']
    redirect = '' if mode == 'stock' else 'uri replace /api/ /api/' + mode + '/ 1'
    wrong = '' if misroute is None else '\n'.join([
        'handle /api/' + misroute + ' {', 'uri strip_prefix /api',
        'reverse_proxy host.docker.internal:' + str(django_port), '}'])
    configuration = '''{
 admin off
}
:80 {
 root * /srv
 route {
  handle /fixture-health {
   respond 200
  }
  WRONG
  @selected path SELECTED
  handle @selected {
   REWRITE
   reverse_proxy UPSTREAM
  }
  handle /ws/* {
   reverse_proxy host.docker.internal:DJANGO
  }
  @unknown path /api/* /instance-*
  handle @unknown {
   respond 502
  }
  handle {
   header Cache-Control no-cache
   try_files {path} /index.html
   file_server
  }
 }
}
'''.replace('WRONG', wrong).replace('SELECTED', ' '.join('/api/' + p for p in SELECTED)).replace(
        'REWRITE', redirect).replace('UPSTREAM', upstream).replace('DJANGO', str(django_port))
    config = work / ('pwa-' + uuid4().hex + '.Caddyfile'); config.write_text(configuration)
    name = proof.run_container('ui-' + uuid4().hex[:8], image_id,
        '--publish', '127.0.0.1::80', '--add-host', 'host.docker.internal:host-gateway',
        '--mount', f'type=bind,source={config},target=/etc/caddy/Caddyfile,readonly',
        '--mount', f'type=bind,source={ui},target=/srv,readonly')
    try:
        base = 'http://127.0.0.1:' + str(proof.published_port(name, 80))
        def ready():
            try: return Client(base).request('GET', '/fixture-health')[0] == 200
            except OSError: return False
        poll(ready, bool, timeout=30)
        proof.docker('exec', name, 'caddy', 'validate', '--config', '/etc/caddy/Caddyfile')
        yield base, {'mode': mode, 'private_alias_rewrite': mode != 'stock', 'misroute': misroute,
            'image_id': image_id, 'configuration_sha256': hashlib.sha256(configuration.encode()).hexdigest()}
    finally:
        try:
            logs = proof.docker('logs', name, check=False)
            proof.log.write(logs.stdout + logs.stderr); proof.log.flush()
        finally:
            proof.docker('rm', '--force', name)
            assert proof.docker('inspect', name, check=False).returncode != 0


def exercise(proof, work, log, args, report):
    import boto3
    from botocore.config import Config
    import redis
    import uvicorn
    from PIL import Image
    from django.apps import apps
    from django.conf import settings
    from django.db import connections
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from instance.models import InstanceConfiguration
    from TESTS.adapters.django.s3_objects import IMAGE as MINIO_IMAGE
    from TESTS.adapters.go.payment_category_image import LABEL

    def snapshot():
        return {model._meta.label: list(model._base_manager.order_by('pk').values())
            for model in apps.get_models(include_auto_created=True) if model._meta.managed and not model._meta.proxy}
    original = snapshot()
    report['managed_snapshot_scope'] = {'models': sorted(original), 'includes_auto_created_many_to_many': True}
    configuration = server = thread = None
    access, secret = 'pwa-' + uuid4().hex[:12], uuid4().hex + uuid4().hex
    proof.private_values.add(secret)
    minio = proof.identifier + '-minio'; proof.containers.append(minio)
    proof.docker('run', '--detach', '--pull', 'never', '--name', minio,
        '--label', LABEL + '=' + proof.identifier, '--network', proof.network, '--network-alias', 'pwa-minio',
        '--publish', '127.0.0.1::9000', '--tmpfs', '/data:rw,size=64m',
        '--env', 'MINIO_ROOT_USER=' + access, '--env', 'MINIO_ROOT_PASSWORD=' + secret,
        proof.image(MINIO_IMAGE)['Id'], 'server', '/data', '--address', ':9000')
    endpoint = 'http://127.0.0.1:' + str(proof.published_port(minio, 9000))
    def ready():
        try: return Client(endpoint).request('GET', '/minio/health/ready')[0] == 200
        except OSError: return False
    poll(ready, bool, timeout=30)
    admin = boto3.client('s3', endpoint_url=endpoint, region_name='us-east-1', aws_access_key_id=access,
        aws_secret_access_key=secret, config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    bucket = 'pwa-fixture'; admin.create_bucket(Bucket=bucket)
    png = BytesIO(); Image.new('RGB', (512, 512), '#2468ac').save(png, format='PNG'); logo = png.getvalue()
    admin.put_object(Bucket=bucket, Key='pwa/instance/logo.png', Body=logo, ContentType='image/png')
    admin.put_object(Bucket=bucket, Key='pwa/unrelated-sentinel', Body=b'Owned sentinel must survive unchanged')
    def objects():
        result = {}
        for row in admin.list_objects_v2(Bucket=bucket).get('Contents', []):
            value = admin.get_object(Bucket=bucket, Key=row['Key'])
            try: data = value['Body'].read()
            finally: value['Body'].close()
            result[row['Key']] = {'sha256': hashlib.sha256(data).hexdigest(), 'length': len(data),
                'properties': {key: item for key, item in value.items() if key not in ('Body', 'ResponseMetadata')},
                'tags': admin.get_object_tagging(Bucket=bucket, Key=row['Key'])['TagSet']}
        return result
    object_baseline = objects()
    cache = redis.Redis(host='127.0.0.1', port=proof.redis_port, db=1, password='synthetic-identity-only')
    def cache_snapshot(): return {key: cache.get(key) for key in cache.scan_iter()}
    runtime = dict(AWS_ACCESS_KEY_ID=access, AWS_SECRET_ACCESS_KEY=secret, AWS_STORAGE_BUCKET_NAME=bucket,
        AWS_LOCATION='pwa', AWS_S3_ENDPOINT_URL=endpoint, AWS_S3_REGION_NAME='us-east-1', STORE_ON_DISK=True,
        STORAGES={'default': {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'},
                  'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}},
        CACHES={'default': {'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': f'redis://127.0.0.1:{proof.redis_port}/1',
            'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': 'synthetic-identity-only'}}},
        AI_API_KEY='', STRIPE_KEY='', STRIPE_PUBLIC_KEY='', STRIPE_WEBHOOK_SECRET='',
        SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='', SOCIAL_AUTH_APPLE_ID_CLIENT='',
        MANUAL_CORPUS_BASE_URL='', MANUAL_RUN_ID='', MANUAL_SOURCE_ROOT='',
        MANUAL_BUNDLE_PATH=str(work / 'absent-manual'), MANUAL_BUNDLE_REQUIRED=False,
        MANUAL_INDEX_PATH=str(work / 'absent-index'), MANUAL_DEVELOPMENT_ROOT=str(work / 'absent-development'))
    try:
        with override_settings(**runtime), ExitStack() as stack:
            configuration = InstanceConfiguration.objects.bulk_create([InstanceConfiguration(
                domain='pwa.example.test', name=MANIFEST['name'], abbreviation=MANIFEST['short_name'],
                meta_title=TITLE, meta_description=MANIFEST['description'], primary_color=MANIFEST['theme_color'],
                logo_path='/api/instance/logo.png', display_settings={'login': {'allowOauthLogin': False},
                    'general': {'cookieConsent': False}})])[0]
            InstanceConfiguration.objects.filter(pk=configuration.pk).update(updated_at=datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc))
            baseline = snapshot(); cache_baseline = cache_snapshot()
            from core.asgi import application
            observer = Observer(application)
            server = uvicorn.Server(uvicorn.Config(observer, host='0.0.0.0', port=0, lifespan='off', log_level='critical', access_log=False))
            thread = threading.Thread(target=server.run, daemon=True); thread.start()
            poll(lambda: server.started, bool, timeout=20)
            django_port = server.servers[0].sockets[0].getsockname()[1]
            django_upstream = 'host.docker.internal:' + str(django_port)
            golang_upstream = django_upstream
            report['go_runtime'] = {'status': 'not_run', 'reason': 'Authored Django baseline only'}
            if args.with_go:
                with override_settings(AWS_S3_ENDPOINT_URL='http://pwa-minio:9000'):
                    go = stack.enter_context(proof.server(work, log, {}, f'127.0.0.1:{proof.redis_port}', versioned=True))
                golang_upstream = go['proxy_upstream']; report['go_runtime'] = go['metadata']
                poll(cache_snapshot, lambda value: value == cache_baseline, timeout=15, interval=.1)
                report['cache_isolation'] = {'periodic_container_healthcheck_disabled': True,
                    'actual_startup_health_and_readiness_verified': True,
                    'startup_readiness_keys_expired_to_exact_original_cache': True,
                    'workload_cache_keys_excluded': []}
            stack.enter_context(proxy(django_upstream, golang_upstream, log, network=proof.network, network_alias='pwa-versioned'))
            workloads = [('stock', None), ('v1', None)]
            if args.with_go: workloads += [('v2', None), ('v2', 'instance/manifest.json'), ('v2', 'instance/logo.png')]
            for mode, misroute in workloads:
                name = mode if misroute is None else 'negative-' + misroute.split('/')[-1]
                target = args.output / name; target.mkdir()
                receipt = {'success': False}; report.setdefault('workloads', {})[name] = receipt
                observer.clear()
                try:
                    with gateway(proof, work, args.ui_dir, 'pwa-versioned:80', django_port, mode, misroute) as (origin, routing):
                        receipt['routing'] = routing
                        assert Client(origin).request('GET', '/api/__unclassified')[0] == 502
                        assert not observer.snapshot(), 'Unknown route fell through'
                        fixture = {'origin': origin, 'title': TITLE, 'color': '#2468ac', 'manifest': MANIFEST,
                            'logo_url': LOGO_URL, 'logo_sha256': hashlib.sha256(logo).hexdigest(),
                            'selected': ['/api/' + value for value in SELECTED], 'playwright_module': str(args.playwright_module)}
                        private = work / 'browser-input.json'; private.write_text(json.dumps(fixture)); private.chmod(0o600)
                        try:
                            subprocess.run(['node', str(ROOT / 'TESTS/browser/instance-pwa.mjs')], cwd=ROOT,
                                env={**os.environ, 'TESTS_FIXTURE': str(private), 'TESTS_OUTPUT': str(target)},
                                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
                        finally: private.unlink(missing_ok=True)
                        browser = json.loads((target / 'browser.json').read_text())
                        assert browser['success'] and len(browser['checks']) == 2
                        selected = [row for row in observer.snapshot() if row['path'].lstrip('/') in SELECTED]
                        if misroute:
                            assert selected and {row['path'] for row in selected} == {'/' + misroute}
                            assert all(row['status'] == 200 for row in selected)
                            try: validate_selected_upstream(mode, selected)
                            except AssertionError as error: assert str(error) == 'upstream_mismatch'
                            else: raise AssertionError('Misroute control was accepted')
                            receipt['rejected_reason'] = 'upstream_mismatch'
                        else: validate_selected_upstream(mode, selected)
                        after, after_objects, after_cache = snapshot(), objects(), cache_snapshot()
                        receipt.update(managed_state_unchanged=after == baseline,
                            storage_unchanged=after_objects == object_baseline, cache_unchanged=after_cache == cache_baseline)
                        receipt['changed_models'] = [key for key in baseline if after[key] != baseline[key]]
                        receipt['storage_changed_fields'] = {key: [field for field in set(value) | set(after_objects.get(key, {}))
                            if value.get(field) != after_objects.get(key, {}).get(field)]
                            for key, value in object_baseline.items() if value != after_objects.get(key)}
                        receipt['cache_key_counts'] = {'before': len(cache_baseline), 'after': len(after_cache)}
                        assert receipt['managed_state_unchanged'], 'Managed rows changed'
                        assert receipt['storage_unchanged'], 'Owned object state changed'
                        assert receipt['cache_unchanged'], 'Redis state changed'
                        receipt['success'] = True
                finally:
                    receipt['django_observer'] = observer.snapshot()
                    receipt['managed_state_unchanged'] = snapshot() == baseline
                    receipt['storage_unchanged'] = objects() == object_baseline
                    receipt['cache_unchanged'] = cache_snapshot() == cache_baseline
                    receipt['success'] = bool(receipt['success'] and receipt['managed_state_unchanged']
                        and receipt['storage_unchanged'] and receipt['cache_unchanged'])
                    (target / 'validation.json').write_text(json.dumps(receipt, indent=2) + '\n')
            expected = {'stock', 'v1', 'v2', 'negative-manifest.json', 'negative-logo.png'} if args.with_go else {'stock', 'v1'}
            assert set(report['workloads']) == expected and all(row['success'] for row in report['workloads'].values())
    finally:
        try:
            if server: server.should_exit = True
            if thread: thread.join(timeout=30); assert not thread.is_alive(), 'ASGI did not stop'
            report['asgi_stopped'] = thread is None or not thread.is_alive()
        finally:
            try:
                if configuration:
                    with disable_auditlog(): InstanceConfiguration.objects.filter(pk=configuration.pk).delete()
                report['managed_cleanup_verified'] = snapshot() == original
                report['storage_unchanged'] = objects() == object_baseline
                assert report['managed_cleanup_verified'] and report['storage_unchanged']
            finally:
                try: connections.close_all()
                finally: admin.close()


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ui-dir', type=Path, required=True)
    parser.add_argument('--ui-provenance', type=Path, required=True)
    parser.add_argument('--prepare-ui-image')
    parser.add_argument('--image', help='Immutable Go image; inspected for fixture safety but run only with --with-go')
    parser.add_argument('--expected-revision')
    parser.add_argument('--with-go', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--playwright-module', type=Path, default=ROOT / 'selfhost/tests/browser/node_modules/@playwright/test/index.mjs')
    args = parser.parse_args(); args.ui_dir = args.ui_dir.resolve(); args.ui_provenance = args.ui_provenance.resolve()
    if args.prepare_ui_image:
        prepare_ui(args.prepare_ui_image, args.ui_dir, args.ui_provenance, WORKFLOW_SOURCES); return
    require_production_python()
    if not args.image or not args.expected_revision or not args.output: parser.error('--image, --expected-revision and --output required')
    assert re.fullmatch(r'[0-9a-f]{40}', args.expected_revision)
    args.output = args.output.resolve(); args.output.mkdir(parents=True, exist_ok=True)
    args.playwright_module = args.playwright_module.resolve(); assert args.playwright_module.is_file()
    path = args.output / 'results.json'; assert not path.exists(), 'Fresh output required'
    report = {'success': False, 'scope': __doc__, 'operator_selection_verified': False, 'installed_pwa_verified': False,
        'complete_pwa_parity': False, 'cleanup_verified': False, 'source_hashes': source_hashes()}
    path.write_text(json.dumps(report, indent=2) + '\n')
    try:
        provenance = json.loads(args.ui_provenance.read_text()); ui_hashes = fingerprints(args.ui_dir)
        assert provenance['success'] and provenance['extraction_container_never_started'] and provenance['extraction_container_removed']
        assert provenance['files'] == ui_hashes
        for name in WORKFLOW_SOURCES:
            matches = provenance['workflow_source_maps'].get(name); assert matches
            assert all(row['embedded_sha256'] == hashlib.sha256((ROOT / 'UI' / name).read_bytes()).hexdigest() for row in matches)
        report.update(ui_image_id=provenance['image_id'], ui_artifact_hashes=ui_hashes,
            workflow_source_maps=provenance['workflow_source_maps'], source_map_scope=list(WORKFLOW_SOURCES),
            provenance_limits=['Not every build input is source-map verified; no service-worker installation/offline claim.'])
        public_assets = ('sw.js', 'manifest.json', 'favicon.svg', 'brand-cache.js', 'oem/assozeta/brand/logo.svg')
        for name in public_assets:
            assert (args.ui_dir / name).read_bytes() == (ROOT / 'UI/public' / name).read_bytes(), 'Public asset source differs: ' + name
        report['public_asset_source_hashes'] = {name: ui_hashes[name] for name in public_assets}
        report['build_input_reference_hashes_not_embedded_provenance'] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('UI/vite.config.js', 'UI/endpoints.js', 'UI/oems.json', 'UI/Dockerfile', 'UI/src/main.js')}
        report['service_worker_installation_verified'] = False
        with tempfile.TemporaryDirectory(prefix='az-pwa-', dir='/tmp') as directory, (args.output / 'services.log').open('w') as log:
            work = Path(directory); proof = BrowserImageFixture(args.image, args.expected_revision, work, args.output, log)
            try:
                proof.start_services('postgres:16-alpine', 'redis:7-alpine')
                initialize_current_schema(work, '127.0.0.1', proof.postgres_port, DATABASE, log)
                exercise(proof, work, log, args, report)
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    proof.cleanup(); report['cleanup_verified'] = proof.cleanup_verified
        assert source_hashes() == report['source_hashes'] and fingerprints(args.ui_dir) == ui_hashes
        assert report['cleanup_verified'] and report['managed_cleanup_verified'] and report['asgi_stopped']
        report['source_stable'] = True; report['success'] = True
    except Exception as error:
        report['failure'] = {'class': type(error).__name__, 'message': str(error)}
        raise
    finally:
        report['source_stable'] = source_hashes() == report['source_hashes']
        report['success'] = bool(report['success'] and report['source_stable'])
        path.write_text(json.dumps(report, indent=2) + '\n')
    assert report['success'], 'PWA receipt did not satisfy all checks'


if __name__ == '__main__': main()
