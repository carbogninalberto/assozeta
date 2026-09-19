import json
import subprocess
from unittest.mock import patch, MagicMock

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMessage
from django.test import TestCase, SimpleTestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.diagnostics import CHECKS, decorate, overall, integrations
from instance.diagnostic_probe import probe
from instance.email_configuration import EmailBackend, decrypt_password, effective_email, encrypt_password
from instance.models import InstanceConfiguration
from instance.probe_runner import run_probe

ENVIRONMENT = dict(EMAIL_HOST='smtp.example.test', EMAIL_PORT=465, EMAIL_HOST_USER='env-user',
                   EMAIL_HOST_PASSWORD='environment-secret', EMAIL_USE_SSL=True, EMAIL_USE_TLS=False,
                   DEFAULT_FROM_EMAIL='Original <mail@example.test>')


@override_settings(**ENVIRONMENT, EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class OperationsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='operations-owner', role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='Operations ASD')
        self.config = InstanceConfiguration.objects.create(domain='operations.example.test', name='Operations ASD', primary_association=association)
        self.client.force_authenticate(self.owner)
        self.data = {'host': 'mail.example.test', 'port': 587, 'security': 'tls', 'username': 'sender',
                     'from_email': 'sender@example.test', 'sender_name': 'Updated', 'revision': 0}

    def put(self, **changes):
        return self.client.put('/instance/admin/email', {**self.data, **changes}, format='json')

    def test_authorization_all_methods_and_no_superuser_bypass(self):
        other = User.objects.create_user(username='operations-other', is_superuser=True)
        for user in (None, other):
            self.client.force_authenticate(user)
            for method, path, data in [('get', 'email', {}), ('put', 'email', self.data), ('delete', 'email', {'revision': 0}),
                                       ('post', 'email/test', {'action': 'send', 'revision': 0, 'recipient': 'test@example.test'}),
                                       ('get', 'diagnostics', {}), ('post', 'diagnostics', {})]:
                with self.subTest(user=user, method=method, path=path):
                    response = getattr(self.client, method)('/instance/admin/' + path, data, format='json', HTTP_USER_ID=str(self.owner.pk))
                    self.assertIn(response.status_code, (401, 403))
        self.client.force_authenticate(self.owner)
        self.config.self_hosted = False
        self.config.save()
        self.assertEqual(self.client.get('/instance/admin/email').status_code, 403)

    def test_environment_fallback_and_public_secret_redaction(self):
        response = self.client.get('/instance/admin/email')
        self.assertEqual(response.data['source'], 'environment')
        self.assertTrue(response.data['password_configured'])
        self.assertNotIn('environment-secret', response.content.decode())
        self.assertNotIn('password', response.data)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(effective_email()['password'], 'environment-secret')
        self.assertFalse(response.data['restart_required'])

    def test_encryption_preservation_reload_and_reset(self):
        response = self.put()
        self.assertEqual(response.status_code, 200, response.data)
        self.config.refresh_from_db()
        self.assertEqual(decrypt_password(self.config.email_password_encrypted), 'environment-secret')
        self.assertNotIn('environment-secret', self.config.email_password_encrypted)
        self.assertNotIn('password', self.config.email_settings)
        self.assertEqual(self.client.get('/instance/admin/email').data['host'], 'mail.example.test')
        self.assertEqual(self.put(revision=1, password='replacement').status_code, 200)
        self.assertEqual(effective_email()['password'], 'replacement')
        self.assertEqual(self.put(revision=2).status_code, 200)
        self.assertEqual(effective_email()['password'], 'replacement')
        self.assertEqual(self.put(revision=3, clear_password=True).status_code, 200)
        self.assertEqual(effective_email()['password'], '')
        response = self.client.delete('/instance/admin/email', {'revision': 4}, format='json')
        self.assertEqual(response.data['source'], 'environment')
        self.assertEqual(effective_email()['password'], 'environment-secret')
        self.assertEqual(settings.EMAIL_HOST_PASSWORD, 'environment-secret')
        self.config.refresh_from_db()
        self.assertEqual(self.config.name, 'Operations ASD')
        self.assertEqual(self.config.email_password_encrypted, '')

    def test_stale_branding_object_cannot_overwrite_email_or_diagnostics(self):
        from instance.serializers import InstanceReconfigureSerializer
        stale = InstanceConfiguration.get_config()
        self.put(password='preserved-password')
        InstanceConfiguration.objects.filter(pk=stale.pk).update(diagnostic_results={'checks': [{'id': 'database', 'status': 'passed'}]})
        serializer = InstanceReconfigureSerializer(stale, data={'oem': {'name': 'New identity'}}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        current = InstanceConfiguration.get_config()
        self.assertEqual(current.name, 'New identity')
        self.assertEqual(decrypt_password(current.email_password_encrypted), 'preserved-password')
        self.assertEqual(current.email_revision, 1)
        self.assertEqual(current.diagnostic_results['checks'][0]['status'], 'passed')

    def test_stale_writes_and_reset_are_rejected(self):
        self.assertEqual(self.put().status_code, 200)
        self.assertEqual(self.put(password='stale').status_code, 409)
        self.assertEqual(self.client.delete('/instance/admin/email', {'revision': 0}, format='json').status_code, 409)
        self.assertEqual(effective_email()['password'], 'environment-secret')

    @override_settings(EMAIL_USE_TLS=True, EMAIL_USE_SSL=True)
    def test_conflicting_environment_security_is_not_silently_changed(self):
        from django.core.exceptions import ImproperlyConfigured
        from instance.email_configuration import smtp_connection
        self.assertEqual(self.client.get('/instance/admin/email').data['security'], 'invalid')
        self.assertEqual(probe('email', {})['status'], 'failed')
        with self.assertRaises(ImproperlyConfigured):
            smtp_connection(effective_email())
        self.assertEqual(self.put().status_code, 200)
        self.assertEqual(effective_email()['security'], 'tls')

    def test_invalid_fields_and_unsafe_authentication(self):
        for changes in ({'port': 0}, {'port': 65536}, {'host': 'smtp://host/path'}, {'host': 'host\nsecret'},
                        {'from_email': 'bad'}, {'password': ''}, {'security': 'none'},
                        {'password': 'new', 'clear_password': True}, {'sender_name': 'line\r\ninjection'}):
            with self.subTest(changes=changes):
                self.assertEqual(self.put(**changes).status_code, 400)
        self.assertEqual(self.put(security='none', username='', clear_password=True).status_code, 200)

    def test_credentials_and_diagnostics_not_in_public_instance_config(self):
        self.put(password='private-sentinel')
        self.config.refresh_from_db()
        self.config.diagnostic_results = {'email_test': {'message': 'private-result'}}
        self.config.save()
        content = self.client.get('/instance/config').content.decode()
        for value in ('private-sentinel', 'private-result', 'email_password_encrypted', 'email_settings'):
            self.assertNotIn(value, content)

    @patch('instance.email_configuration.smtp_connection')
    def test_existing_backend_objects_read_latest_transport_and_sender(self, connection):
        connection.return_value.send_messages.return_value = 1
        backend = EmailBackend()
        backend.send_messages([EmailMessage('before', 'body', 'original@example.test', ['test@example.test'])])
        self.assertEqual(connection.call_args.args[0]['host'], 'smtp.example.test')
        self.put(password='updated-secret')
        message = EmailMessage('after', 'body', 'original@example.test', ['test@example.test'])
        self.assertEqual(backend.send_messages([message]), 1)
        self.assertEqual(connection.call_args.args[0]['host'], 'mail.example.test')
        self.assertEqual(connection.call_args.args[0]['password'], 'updated-secret')
        self.assertEqual(connection.return_value.send_messages.call_args.args[0][0].from_email, 'Updated <sender@example.test>')
        self.assertEqual(message.from_email, 'original@example.test')

    def test_unreadable_password_requires_replacement_and_key_rotation_support(self):
        self.put(password='old-secret')
        with override_settings(SECRET_KEY='rotated', SECRET_KEY_FALLBACKS=[settings.SECRET_KEY]):
            self.assertEqual(effective_email()['password'], 'old-secret')
        with override_settings(SECRET_KEY='unknown', SECRET_KEY_FALLBACKS=[]):
            self.assertTrue(self.client.get('/instance/admin/email').data['credential_error'])
            self.assertEqual(self.put(revision=1).status_code, 400)
            self.assertEqual(self.put(revision=1, password='new-secret').status_code, 200)
            self.assertEqual(effective_email()['password'], 'new-secret')

    @patch('instance.operations.run_probe')
    def test_email_actions_explicit_recipient_revision_and_rate_limit(self, run):
        run.return_value = {'status': 'passed', 'level': 'functional', 'message': 'SMTP accepted', 'checked_at': '2026-09-18T12:00:00Z'}
        self.assertEqual(self.client.post('/instance/admin/email/test', {'action': 'send', 'revision': 0}, format='json').status_code, 400)
        self.assertEqual(self.client.post('/instance/admin/email/test', {'action': 'connect', 'revision': 99}, format='json').status_code, 409)
        run.assert_not_called()
        data = {'action': 'send', 'revision': 0, 'recipient': 'recipient@example.test'}
        self.assertEqual(self.client.post('/instance/admin/email/test', data, format='json').status_code, 200)
        run.assert_called_once_with('email_send', data)
        self.config.refresh_from_db()
        self.assertNotIn('recipient@example.test', json.dumps(self.config.diagnostic_results))
        self.assertIsNotNone(self.client.get('/instance/admin/email').data['last_test'])
        self.assertEqual(self.client.post('/instance/admin/email/test', data, format='json').status_code, 429)
        self.put()
        self.assertIsNone(self.client.get('/instance/admin/email').data['last_test'])

    @patch('instance.operations.run_probe')
    def test_concurrent_email_change_cannot_persist_a_stale_success(self, run):
        def change_during_test(*args, **kwargs):
            self.assertEqual(self.put(password='new-credential').status_code, 200)
            return {'status': 'passed', 'level': 'functional', 'message': 'accepted', 'checked_at': '2026-09-18T12:00:00Z'}
        run.side_effect = change_during_test
        response = self.client.post('/instance/admin/email/test', {'action': 'send', 'revision': 0, 'recipient': 'chosen@example.test'}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertIn('potrebbe essere stato inviato', response.data['error'])
        self.assertIsNone(self.client.get('/instance/admin/email').data['last_test'])

    @patch('instance.diagnostics.run_probe')
    def test_diagnostics_persist_results_and_never_send_or_mark_optional_failure_core(self, run):
        def fake(key):
            return {'status': 'failed' if key == 'email' else 'passed', 'level': 'connectivity', 'message': 'safe', 'checked_at': '2026-09-18T12:00:00Z'}
        run.side_effect = fake
        initial = self.client.get('/instance/admin/diagnostics').data
        self.assertEqual(initial['overall'], 'not_checked')
        self.assertTrue(all(item['status'] == 'not_checked' for item in initial['checks']))
        with patch('django.core.cache.cache.add', side_effect=RuntimeError('cache unavailable')):
            response = self.client.post('/instance/admin/diagnostics', {}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['overall'], 'passed')
        self.assertCountEqual([call.args[0] for call in run.call_args_list], [item[0] for item in CHECKS])
        self.assertEqual(self.client.get('/instance/admin/diagnostics').data['checks'], response.data['checks'])
        self.assertEqual(self.client.post('/instance/admin/diagnostics', {}, format='json').status_code, 429)
        self.put()
        email = next(item for item in self.client.get('/instance/admin/diagnostics').data['checks'] if item['id'] == 'email')
        self.assertEqual(email['status'], 'not_checked')

    def test_sensitive_endpoints_are_excluded_from_payload_audit(self):
        self.assertTrue({'instance-email-settings', 'instance-email-test', 'instance-diagnostics'}.issubset(settings.DRF_API_LOGGER_SKIP_URL_NAME))

    @patch('instance.email_configuration.smtp_connection')
    def test_routine_email_check_does_not_connect_and_test_actions_are_distinct(self, smtp):
        self.assertEqual(probe('email', {})['level'], 'configuration')
        smtp.assert_not_called()
        connection = smtp.return_value.__enter__.return_value
        self.assertEqual(probe('email_connect', {'revision': 0})['status'], 'passed')
        connection.send_messages.assert_not_called()
        connection.send_messages.return_value = 1
        self.assertEqual(probe('email_send', {'revision': 0, 'recipient': 'test@example.test'})['level'], 'functional')
        self.assertEqual(len(connection.send_messages.call_args.args[0]), 1)


class ProbeTests(SimpleTestCase):
    def test_overall_requires_evidence_for_every_core_check(self):
        self.assertEqual(overall(decorate({})), 'not_checked')
        checks = decorate({key: {'status': 'passed'} for key, *_ in CHECKS})
        self.assertEqual(overall(checks), 'passed')
        checks[2]['status'] = 'failed'
        self.assertEqual(overall(checks), 'failed')

    @patch('instance.probe_runner.subprocess.run')
    def test_hard_timeout_and_errors_are_sanitized(self, run):
        run.side_effect = subprocess.TimeoutExpired('private connection data', 0.01, output='secret')
        value = run_probe('database', timeout=0.01)
        self.assertEqual(value['status'], 'failed')
        self.assertNotIn('secret', json.dumps(value))
        self.assertEqual(run.call_args.kwargs['timeout'], 0.01)
        run.side_effect = None
        run.return_value = MagicMock(returncode=1, stdout='password=secret')
        self.assertNotIn('secret', json.dumps(run_probe('email_send')))
        with self.assertRaises(ValueError):
            run_probe('../../arbitrary')

    @override_settings(DRF_API_LOGGER_DATABASE=True, DRF_API_LOGGER_SIGNAL=True)
    @patch('drf_api_logger.middleware.api_logger_middleware.get_headers')
    def test_sensitive_requests_bypass_logger_before_body_or_header_collection(self, headers):
        from django.http import JsonResponse
        from django.test import RequestFactory
        from drf_api_logger.middleware.api_logger_middleware import APILoggerMiddleware
        middleware = APILoggerMiddleware(lambda request: JsonResponse({'ok': True}))
        for path in ('/instance/admin/email', '/instance/admin/email/test', '/instance/admin/diagnostics'):
            request = RequestFactory().post(path, {'password': 'do-not-log'}, content_type='application/json')
            self.assertEqual(middleware(request).status_code, 200)
        headers.assert_not_called()

    def test_operational_requests_are_not_captured_by_development_profiler(self):
        from django.test import RequestFactory
        if 'silk' not in settings.INSTALLED_APPS:
            self.assertNotIn('silk.middleware.SilkyMiddleware', settings.MIDDLEWARE)
            return
        from silk.middleware import _should_intercept
        for path in ('/instance/admin/email', '/instance/admin/email/test', '/instance/admin/diagnostics'):
            request = RequestFactory().post(path, {'password': 'do-not-profile'}, content_type='application/json')
            self.assertFalse(_should_intercept(request))

    @patch('instance.probe_runner.subprocess.run')
    def test_probe_returns_only_expected_fields(self, run):
        run.return_value = MagicMock(returncode=0, stdout=json.dumps({'status': 'passed', 'level': 'configuration', 'message': 'safe', 'password': 'secret'}))
        self.assertEqual(set(run_probe('email')), {'status', 'level', 'message', 'checked_at'})

    @override_settings(STRIPE_PUBLIC_KEY='', STRIPE_KEY='', STRIPE_WEBHOOK_SECRET='',
                       SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET='',
                       SOCIAL_AUTH_APPLE_ID_CLIENT='', SOCIAL_AUTH_APPLE_ID_TEAM='', SOCIAL_AUTH_APPLE_ID_KEY='', SOCIAL_AUTH_APPLE_ID_SECRET='')
    def test_optional_integrations_missing_credentials_and_mismatched_modes(self):
        self.assertTrue(all(item['status'] == 'not_configured' for item in integrations(None)))
        with override_settings(STRIPE_PUBLIC_KEY='pk_test_secret', STRIPE_KEY='sk_live_secret', STRIPE_WEBHOOK_SECRET='whsec_secret'):
            result = integrations(None)[0]
            self.assertEqual(result['status'], 'warning')
            self.assertNotIn('secret', json.dumps(result))

class ReadOnlyServiceTests(SimpleTestCase):
    @patch('instance.diagnostic_probe.health_response')
    @override_settings(APP_URL='http://example.test', RUNNING_VERSION='1.0.3', PUPPETEER_HOST='renderer', PUPPETEER_PORT='3000')
    def test_http_warning_and_renderer_reachability_do_not_claim_pdf_generation(self, health):
        health.return_value = True
        self.assertEqual(probe('public_url', {})['status'], 'warning')
        health.assert_called_with('http://example.test/api/healthz', '1.0.3')
        with override_settings(APP_URL='https://example.test'):
            self.assertEqual(probe('public_url', {})['status'], 'passed')
        result = probe('renderer', {})
        self.assertEqual(result['level'], 'connectivity')
        self.assertIn('non è stata provata', result['message'])
        health.return_value = False
        self.assertEqual(probe('renderer', {})['status'], 'failed')

    @override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    def test_scheduler_needs_fresh_heartbeat_not_just_a_running_process(self):
        import time
        cache.clear()
        self.assertEqual(probe('scheduler', {})['status'], 'failed')
        cache.set('instance:scheduler:heartbeat', time.time() - 100, 300)
        self.assertEqual(probe('scheduler', {})['status'], 'failed')
        cache.set('instance:scheduler:heartbeat', time.time(), 300)
        self.assertEqual(probe('scheduler', {})['status'], 'passed')

    @patch('boto3.client')
    def test_storage_check_uses_only_read_operations(self, factory):
        self.assertEqual(probe('storage', {})['status'], 'passed')
        self.assertEqual([call[0] for call in factory.return_value.mock_calls], ['list_objects_v2'])
        self.assertEqual(factory.return_value.list_objects_v2.call_args.kwargs['MaxKeys'], 1)

    def test_real_https_certificate_verification_and_redirect_refusal(self):
        import datetime
        import http.server
        import ipaddress
        import os
        import ssl
        import tempfile
        import threading
        from pathlib import Path
        import requests
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        from instance.diagnostic_probe import health_response
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'localhost')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=1))
                .not_valid_after(now + datetime.timedelta(days=1))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'), x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
                .sign(key, hashes.SHA256()))
        paths = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                paths.append(self.path)
                if self.path == '/redirect':
                    self.send_response(302)
                    self.send_header('Location', '/api/healthz')
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(b'{"status":"ok","version":"fixture"}')
        with tempfile.TemporaryDirectory() as temporary:
            certificate = Path(temporary) / 'certificate.pem'
            private_key = Path(temporary) / 'private.pem'
            certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            with http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certificate, private_key)
                server.socket = context.wrap_socket(server.socket, server_side=True)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                origin = f'https://127.0.0.1:{server.server_port}'
                try:
                    with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'REQUESTS_CA_BUNDLE': '', 'CURL_CA_BUNDLE': ''}):
                        with self.assertRaises(requests.exceptions.SSLError):
                            health_response(origin + '/api/healthz', 'fixture')
                    with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1', 'REQUESTS_CA_BUNDLE': str(certificate)}):
                        self.assertTrue(health_response(origin + '/api/healthz', 'fixture'))
                        self.assertFalse(health_response(origin + '/api/healthz', 'wrong-version'))
                        self.assertFalse(health_response(origin + '/redirect', 'fixture'))
                        self.assertEqual(paths[-1], '/redirect')
                finally:
                    server.shutdown()
                    thread.join(timeout=2)

    def test_service_processes_work_without_loading_unrelated_django_apps(self):
        import http.server
        import os
        import sys
        import tempfile
        import threading
        from pathlib import Path
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"status":"ok","version":"fixture"}')
            def log_message(self, *args):
                pass
        with tempfile.TemporaryDirectory() as directory, http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler) as server:
            # An unavailable unrelated app must not prevent checking basic services.
            Path(directory, 'isolated_probe_settings.py').write_text(
                "SECRET_KEY='fixture'\nINSTALLED_APPS=['unavailable_business_app']\n"
                "DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
                f"APP_URL='http://127.0.0.1:{server.server_port}'\nRUNNING_VERSION='fixture'\n")
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                environment = {**os.environ, 'DJANGO_SETTINGS_MODULE': 'isolated_probe_settings',
                               'PYTHONPATH': directory + os.pathsep + os.getcwd(), 'NO_PROXY': '127.0.0.1'}
                for name, expected in [('database', 'passed'), ('public_url', 'warning')]:
                    child = subprocess.run([sys.executable, '-m', 'instance.diagnostic_probe', name],
                                           input='{}', text=True, capture_output=True, env=environment,
                                           timeout=5, check=True)
                    self.assertEqual(json.loads(child.stdout)['status'], expected)
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_real_hung_child_is_terminated_by_deadline(self):
        import sys
        import time
        original = subprocess.run
        def sleeping_child(args, **kwargs):
            return original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        start = time.monotonic()
        with patch('instance.probe_runner.subprocess.run', side_effect=sleeping_child):
            result = run_probe('database', timeout=0.15)
        self.assertEqual(result['status'], 'failed')
        self.assertIn('Tempo massimo', result['message'])
        self.assertLess(time.monotonic() - start, 3)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   EMAIL_HOST='127.0.0.1', EMAIL_HOST_USER='', EMAIL_HOST_PASSWORD='',
                   EMAIL_USE_SSL=False, EMAIL_USE_TLS=False, DEFAULT_FROM_EMAIL='Fixture <sender@example.test>')
class SMTPFixtureTests(TestCase):
    def test_real_smtp_connect_and_send_use_only_disposable_local_receiver(self):
        import socketserver
        import threading
        messages = []
        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.wfile.write(b'220 fixture ESMTP\r\n')
                collecting = False
                message = []
                while True:
                    line = self.rfile.readline()
                    if not line:
                        return
                    if collecting:
                        if line == b'.\r\n':
                            messages.append(b''.join(message))
                            self.wfile.write(b'250 accepted\r\n')
                            collecting = False
                        else:
                            message.append(line)
                    elif line.upper().startswith(b'DATA'):
                        self.wfile.write(b'354 send data\r\n')
                        collecting = True
                    elif line.upper().startswith(b'QUIT'):
                        self.wfile.write(b'221 bye\r\n')
                        return
                    else:
                        self.wfile.write(b'250 fixture\r\n')
        with socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with override_settings(EMAIL_PORT=server.server_address[1]):
                    self.assertEqual(probe('email_connect', {'revision': 0})['status'], 'passed')
                    self.assertEqual(messages, [])
                    result = probe('email_send', {'revision': 0, 'recipient': 'chosen@example.test'})
                    self.assertEqual(result['status'], 'passed')
                    self.assertIn('consegna non è garantita', result['message'])
                    self.assertEqual(len(messages), 1)
                    self.assertIn(b'To: chosen@example.test', messages[0])
            finally:
                server.shutdown()
                thread.join(timeout=2)

class ExistingInstallationMigrationTests(TransactionTestCase):
    def test_normal_migration_keeps_branding_credentials_and_environment_fallback(self):
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        previous = [('instance', '0002_setup_provenance_onboarding_completion')]
        try:
            executor.migrate(previous)
            old = executor.loader.project_state(previous).apps.get_model('instance', 'InstanceConfiguration')
            old.objects.create(domain='legacy.example.test', name='Preserved Legacy Club', primary_color='#abcdef',
                               logo_path='/api/instance/logo.png', stripe_secret_key='legacy-placeholder',
                               display_settings={'preserved': True}, self_hosted=True)
            executor = MigrationExecutor(connection)
            executor.migrate(latest)
            config = InstanceConfiguration.objects.get(domain='legacy.example.test')
            self.assertEqual(config.name, 'Preserved Legacy Club')
            self.assertEqual(config.primary_color, '#abcdef')
            self.assertEqual(config.display_settings, {'preserved': True})
            self.assertEqual(config.stripe_secret_key, 'legacy-placeholder')
            self.assertEqual(config.email_settings, {})
            self.assertEqual(config.email_password_encrypted, '')
            self.assertEqual(config.email_revision, 0)
            self.assertEqual(config.diagnostic_results, {})
            self.assertEqual(config.integration_settings, {})
            with override_settings(**ENVIRONMENT):
                self.assertEqual(effective_email(config)['password'], 'environment-secret')
                self.assertEqual(effective_email(config)['source'], 'environment')
        finally:
            MigrationExecutor(connection).migrate(latest)

class SchedulerInstrumentationTests(SimpleTestCase):
    @patch('instance.scheduler.cache')
    @patch('django_celery_beat.schedulers.DatabaseScheduler.tick', return_value=60)
    def test_successful_scheduler_tick_publishes_heartbeat_without_delaying_work(self, tick, mocked_cache):
        cache_set = mocked_cache.set
        from instance.scheduler import InstanceScheduler
        scheduler = object.__new__(InstanceScheduler)
        self.assertEqual(scheduler.tick(), 30)
        scheduler._heartbeat_publisher.join(timeout=2)
        self.assertEqual(cache_set.call_args.args[0], 'instance:scheduler:heartbeat')
        self.assertEqual(cache_set.call_args.kwargs['timeout'], 90)
        cache_set.side_effect = RuntimeError('unavailable cache')
        self.assertEqual(scheduler.tick(), 30)
        scheduler._heartbeat_publisher.join(timeout=2)
        tick.side_effect = RuntimeError('scheduler failed')
        cache_set.reset_mock()
        with self.assertRaises(RuntimeError):
            scheduler.tick()
        cache_set.assert_not_called()

    @patch('instance.scheduler.cache')
    @patch('django_celery_beat.schedulers.DatabaseScheduler.tick', return_value=5)
    def test_stalled_monitoring_does_not_block_beat_or_accumulate_publishers(self, tick, mocked_cache):
        cache_set = mocked_cache.set
        import threading
        import time
        from instance.scheduler import InstanceScheduler
        release = threading.Event()
        cache_set.side_effect = lambda *args, **kwargs: release.wait(5)
        scheduler = object.__new__(InstanceScheduler)
        try:
            start = time.monotonic()
            scheduler.tick()
            publisher = scheduler._heartbeat_publisher
            for _ in range(10):
                self.assertEqual(scheduler.tick(), 5)
                self.assertIs(scheduler._heartbeat_publisher, publisher)
            self.assertLess(time.monotonic() - start, 1)
        finally:
            release.set()
            scheduler._heartbeat_publisher.join(timeout=2)
