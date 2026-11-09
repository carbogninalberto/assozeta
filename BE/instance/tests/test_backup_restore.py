import asyncio
import io
import json
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn, UnixStreamServer
import shutil
import tarfile
import tempfile
import threading
from unittest.mock import patch

from auditlog.models import LogEntry
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from rest_framework.test import APIClient

from application.models import SportAssociation, User
from instance.models import InstanceConfiguration
from instance.updater_client import UpdaterRejected
from instance.upload_stream import BODY_RECEIVE, stream_uploads

ARCHIVE = 'assozeta-20261106T200459Z.tar.gz'
TOKEN = 'runner-test-token-0123456789abcdef0123456789'


def small_archive(manifest=True):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in (('postgres.dump', b'dump'), ('objects/a.pdf', b'pdf'), ('manifest.txt', b'version=1.0.33\n')):
            if name == 'manifest.txt' and not manifest:
                continue
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class Runner(ThreadingMixIn, UnixStreamServer):
    """A stand-in runner on a Unix socket that checks uploads like the real one."""
    daemon_threads = True

    def __init__(self, path, reject=None):
        self.received, self.reject = [], reject

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(handler):
                assert handler.headers['Authorization'] == f'Bearer {TOKEN}'
                handler.close_connection = True
                if self.reject:
                    status, value = self.reject
                else:
                    data = handler.rfile.read(int(handler.headers['Content-Length']))
                    self.received.append(data)
                    try:
                        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
                            names = set(archive.getnames())
                        valid = {'postgres.dump', 'manifest.txt'} <= names
                    except tarfile.TarError:
                        valid = False
                    status, value = (201, {'archive': {'name': 'assozeta-20261109T100000Z.tar.gz', 'origin': 'uploaded'}}) if valid \
                        else (400, {'error': 'Il file non è un backup valido di Assozeta.'})
                payload = json.dumps(value).encode()
                handler.send_response(status)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(payload)))
                handler.end_headers()
                handler.wfile.write(payload)

        super().__init__(str(path), Handler)


@override_settings(ASSOZETA_DEPLOYMENT_MODE='production',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class BackupRestoreTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.owner = User.objects.create_user(username='restore-owner', password='OwnerSecret', role=User.ASSOCIATION)
        association = SportAssociation.objects.create(user=self.owner, denomination='Restore ASD')
        InstanceConfiguration.objects.create(domain='restore.example.test', name='Restore ASD', primary_association=association)
        self.client.force_authenticate(user=self.owner)

    def restore(self, password='OwnerSecret', confirm='RIPRISTINA', name=ARCHIVE):
        return self.client.post(f'/instance/admin/backups/{name}/restore', {'password': password, 'confirm': confirm}, format='json')

    def audited(self, action):
        return LogEntry.objects.filter(changes__icontains=action).count()

    def runner(self, reject=None):
        directory = Path(tempfile.mkdtemp(prefix='runner-', dir='/tmp'))
        self.addCleanup(shutil.rmtree, directory, True)
        (directory / 'token').write_text(TOKEN)
        server = Runner(directory / 'runner.sock', reject)
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        override = override_settings(ASSOZETA_UPDATER_DIRECTORY=str(directory))
        override.enable()
        self.addCleanup(override.disable)
        return server

    def upload(self, data):
        return self.client.generic('POST', '/instance/admin/backups/upload', data, content_type='application/gzip')

    def test_non_owners_cannot_restore_or_upload(self):
        other = User.objects.create_user(username='restore-other', password='x', role=User.ASSOCIATION)
        self.client.force_authenticate(user=other)
        with patch('instance.backups.call_runner') as runner, patch('instance.backups.send_runner_stream') as stream:
            self.assertEqual(self.restore(password='x').status_code, 403)
            self.assertEqual(self.upload(small_archive()).status_code, 403)
            runner.assert_not_called()
            stream.assert_not_called()

    @patch('instance.backups.call_runner', return_value={'operation': {'id': 'op-1', 'kind': 'restore', 'stage': 'queued'}})
    def test_restore_needs_the_password_and_the_word_and_is_audited(self, runner):
        self.assertEqual(self.restore(name='notes.txt').status_code, 404)
        self.assertEqual(self.restore(confirm='ripristina').status_code, 400)
        response = self.restore(password='wrong')
        self.assertEqual((response.status_code, response.data['error']), (400, 'Password non corretta.'))
        runner.assert_not_called()
        self.assertEqual(self.audited('backup_restore'), 0)
        response = self.restore()
        self.assertEqual(response.status_code, 202)
        method, path, payload = runner.call_args.args
        self.assertEqual((method, path, payload['confirm'], payload['actor_id']),
                         ('POST', f'/backups/{ARCHIVE}/restore', 'RIPRISTINA', str(self.owner.pk)))
        self.assertEqual(self.audited('backup_restore'), 1)

    def test_restore_password_attempts_are_limited(self):
        with patch('instance.backups.call_runner') as runner:
            for _ in range(5):
                self.restore(password='wrong')
            self.assertEqual(self.restore().status_code, 429)
            runner.assert_not_called()

    @patch('instance.backups.call_runner', side_effect=UpdaterRejected('Il backup è della versione 1.0.40, più recente di quella installata (1.0.33). Aggiorna prima l’app.', 409))
    def test_restore_refusals_keep_the_runner_message(self, runner):
        response = self.restore()
        self.assertEqual(response.status_code, 409)
        self.assertIn('più recente', response.data['error'])
        self.assertEqual(self.audited('backup_restore'), 0)

    def test_upload_streams_the_raw_body_to_the_runner_and_is_audited(self):
        runner = self.runner()
        data = small_archive()
        response = self.upload(data)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['archive']['origin'], 'uploaded')
        self.assertEqual(runner.received, [data])
        self.assertEqual(self.audited('backup_upload'), 1)

    def test_upload_rejects_bad_archives_and_empty_bodies(self):
        runner = self.runner()
        response = self.upload(small_archive(manifest=False))
        self.assertEqual((response.status_code, response.json()['error']), (400, 'Il file non è un backup valido di Assozeta.'))
        self.assertEqual(self.upload(b'').status_code, 411)
        self.assertEqual(len(runner.received), 1)
        self.assertEqual(self.audited('backup_upload'), 0)

    def test_upload_reports_an_early_runner_refusal(self):
        self.runner(reject=(409, {'error': 'Spazio su disco insufficiente: servono 9,0 GB, liberi 2,0 GB.'}))
        response = self.upload(b'\x1f\x8b' + b'x' * (8 * 1024 * 1024))
        self.assertEqual(response.status_code, 409)
        self.assertIn('Spazio su disco insufficiente', response.json()['error'])


class StreamUploadsTests(SimpleTestCase):
    def test_django_gets_an_empty_body_and_the_view_the_real_stream(self):
        seen = {}

        async def app(scope, receive, send):
            seen['first'] = await receive()
            seen['real'] = await scope[BODY_RECEIVE]()

        async def receive():
            return {'type': 'http.request', 'body': b'chunk', 'more_body': False}

        scope = {'type': 'http', 'method': 'POST', 'path': '/instance/admin/backups/upload'}
        asyncio.run(stream_uploads(app)(scope, receive, None))
        self.assertEqual(seen['first'], {'type': 'http.request', 'body': b'', 'more_body': False})
        self.assertEqual(seen['real']['body'], b'chunk')

    def test_other_requests_are_untouched(self):
        seen = {}

        async def app(scope, receive, send):
            seen['body'] = (await receive())['body']
            seen['streamed'] = BODY_RECEIVE in scope

        async def receive():
            return {'type': 'http.request', 'body': b'json', 'more_body': False}

        asyncio.run(stream_uploads(app)({'type': 'http', 'method': 'POST', 'path': '/instance/admin/backups'}, receive, None))
        self.assertEqual(seen, {'body': b'json', 'streamed': False})


# A tiny body limit proves that no middleware reads the upload into memory.
@override_settings(ASSOZETA_DEPLOYMENT_MODE='production', DATA_UPLOAD_MAX_MEMORY_SIZE=16)
class StreamedUploadThroughAsgiTests(TransactionTestCase):
    """The real ASGI handler: the view forwards chunks as they arrive."""

    def test_chunks_reach_the_runner_without_django_reading_the_body(self):
        from django.core.handlers.asgi import ASGIHandler
        from rest_framework.permissions import AllowAny
        from instance.backups import BackupUploadView

        data = small_archive()
        third = len(data) // 3
        parts = [data[:third], data[third:2 * third], data[2 * third:]]
        chunks = list(parts)
        forwarded = []

        def fake_runner(path, body, length):
            forwarded.extend(body)
            return {'archive': {'name': 'assozeta-20261109T100000Z.tar.gz'}}

        async def receive():
            if chunks:
                body = chunks.pop(0)
                return {'type': 'http.request', 'body': body, 'more_body': bool(chunks)}
            await asyncio.Event().wait()

        sent = []

        async def send(message):
            sent.append(message)

        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
                 'path': '/instance/admin/backups/upload', 'raw_path': b'/instance/admin/backups/upload', 'query_string': b'',
                 'root_path': '', 'headers': [(b'host', b'testserver'), (b'content-type', b'application/gzip'),
                                              (b'content-length', str(len(data)).encode())],
                 'client': ('127.0.0.1', 1), 'server': ('testserver', 80)}
        with patch.object(BackupUploadView, 'permission_classes', [AllowAny]), \
                patch.object(BackupUploadView, 'authentication_classes', []), \
                patch('instance.backups.audit'), patch('instance.backups.send_runner_stream', side_effect=fake_runner):
            asyncio.run(stream_uploads(ASGIHandler())(scope, receive, send))
        self.assertEqual(sent[0]['status'], 201, sent)
        self.assertEqual(forwarded, parts)
