"""HTTP routing contract; fixture backends expose only transport observations."""
import base64
import hashlib
import http.client
import json
import os
import socket
import struct
import unittest
from urllib.parse import urlsplit

from TESTS.http_client import Client, poll


def connection():
    url = urlsplit(os.environ['TESTS_ROUTING_URL'])
    return http.client.HTTPConnection(url.hostname, url.port, timeout=3)


class VersionedRoutingContracts(unittest.TestCase):
    def test_methods_bodies_queries_headers_and_status(self):
        client = Client(os.environ['TESTS_ROUTING_URL'])
        body = bytes(range(256)) + b'\r\n--upload-boundary\r\n'
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('/api', 'django')]:
            for method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'):
                with self.subTest(prefix=prefix, method=method):
                    path = '/probe/a%2Fb/%C3%A8?repeat=1&repeat=2&sig=a%2Bb%3D&blank='
                    status, data, headers = client.request(method, prefix + path, body, {
                        'Content-Type': 'multipart/form-data; boundary=upload-boundary',
                        'Authorization': 'Bearer synthetic-routing-only',
                        'Cookie': 'session=synthetic-routing-only',
                        'X-Correlation-ID': 'routing-probe',
                    }, include_headers=True)
                    self.assertEqual(status, 200)
                    self.assertEqual(data, {'backend': owner, 'method': method, 'path': path,
                        'body': base64.b64encode(body).decode(),
                        'content_type': 'multipart/form-data; boundary=upload-boundary',
                        'authorization': 'Bearer synthetic-routing-only',
                        'cookie': 'session=synthetic-routing-only', 'cid': 'routing-probe'})
                    self.assertEqual(headers['X-Fixture-Backend'], owner)
            for code in (201, 400, 401, 403, 404, 409, 429, 500, 503):
                with self.subTest(prefix=prefix, code=code):
                    status, data, headers = client.request('POST', prefix + f'/probe/status/{code}', {}, include_headers=True)
                    self.assertEqual(status, code)
                    self.assertEqual(data['backend'], owner)
                    self.assertEqual(headers['Retry-After'], '7')

    def test_exact_version_roots_preserve_method_body_and_query(self):
        for version, owner in [('v1', 'django'), ('v2', 'go')]:
            status, data = Client(os.environ['TESTS_ROUTING_URL']).request('POST', f'/api/{version}?x=1&x=2', b'root')
            self.assertEqual(status, 200)
            self.assertEqual((data['backend'], data['path'], data['method'], data['body']),
                             (owner, '/?x=1&x=2', 'POST', 'cm9vdA=='))

    def test_callbacks_signed_downloads_root_protocols_and_boundaries(self):
        client = Client(os.environ['TESTS_ROUTING_URL'])
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('/api', 'django')]:
            for path in ('/google/oauth2callback?code=synthetic&state=a%2Fb',
                         '/document/retrieve?token=synthetic%2Bsignature%3D',
                         '/bakney/v1/manifest', '/instance/admin/bakney-pairing'):
                with self.subTest(prefix=prefix, path=path):
                    status, data, headers = client.request('GET', prefix + path, include_headers=True)
                    self.assertEqual(status, 200)
                    self.assertEqual((data['backend'], data['path']), (owner, path))
                    if 'bakney' in path:
                        self.assertEqual(headers['Cache-Control'], 'no-store')
                        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        for path, upstream in [('/bakney/v1/manifest', '/bakney/v1/manifest'),
                               ('/api/v10/probe', '/v10/probe'),
                               ('/api/v20/probe', '/v20/probe')]:
            status, data = client.request('GET', path)
            self.assertEqual(status, 200)
            self.assertEqual((data['backend'], data['path']), ('django', upstream))

    def test_download_headers_binary_ranges_head_and_redirect(self):
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('/api', 'django')]:
            client = Client(os.environ['TESTS_ROUTING_URL'])
            status, body, headers = client.request('GET', prefix + '/probe/download',
                headers={'Range': 'bytes=16-47'}, raw=True, include_headers=True)
            self.assertEqual((status, body), (206, bytes(range(16, 48))))
            self.assertEqual(headers['Content-Type'], 'application/octet-stream')
            self.assertEqual(headers['Content-Disposition'], "attachment; filename*=UTF-8''caf%C3%A8.pdf")
            self.assertEqual(headers['Content-Range'], 'bytes 16-47/256')
            self.assertEqual(headers['ETag'], '"synthetic-download"')
            self.assertEqual(headers['X-Fixture-Backend'], owner)
            status, body, headers = client.request('HEAD', prefix + '/probe/download', raw=True, include_headers=True)
            self.assertEqual((status, body, headers['Content-Length']), (200, b'', '256'))
            conn = connection()
            try:
                conn.request('POST', prefix + '/probe/redirect', body=b'unchanged')
                response = conn.getresponse()
                self.assertEqual(response.status, 307)
                self.assertEqual(response.getheader('Location'), '/api/document/retrieve?token=synthetic%2Bvalue')
                self.assertEqual(response.getheaders().count(('Set-Cookie', 'first=1; HttpOnly; Secure; SameSite=Lax')), 1)
                self.assertEqual(response.getheaders().count(('Set-Cookie', 'second=2; Path=/; Secure')), 1)
                self.assertEqual(response.read(), b'fixture redirect')
            finally:
                conn.close()

    def test_stream_reaches_client_before_upstream_finishes(self):
        controls = json.loads(os.environ['TESTS_ROUTING_CONTROLS'])
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('/api', 'django')]:
            conn = connection()
            try:
                conn.request('GET', prefix + '/probe/stream')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                first = ('data: ' + owner + '\n\n').encode()
                self.assertEqual(response.read(len(first)), first)
                # Upstream cannot finish until this separate control request.
                self.assertEqual(Client(controls[owner]).request('POST', '/__fixture/release')[0], 200)
                self.assertEqual(response.read(), b'data: done\n\n')
            finally:
                Client(controls[owner]).request('POST', '/__fixture/release')
                conn.close()

    def test_websocket_upgrade_frames_and_close(self):
        url = urlsplit(os.environ['TESTS_ROUTING_URL'])
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('', 'django')]:
            with socket.create_connection((url.hostname, url.port), timeout=3) as sock:
                key = base64.b64encode(b'0123456789abcdef').decode()
                path = '/ws/probe?token=synthetic%2Btoken&impersonation=synthetic'
                sock.sendall((f'GET {prefix}{path} HTTP/1.1\r\nHost: {url.netloc}\r\n'
                    'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                    f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Protocol: fixture\r\n\r\n').encode())
                with sock.makefile('rb') as reader:
                    self.assertIn(b' 101 ', reader.readline())
                    headers = {}
                    while (line := reader.readline()) != b'\r\n':
                        self.assertTrue(line)
                        name, value = line.decode().split(':', 1)
                        headers[name.lower()] = value.strip()
                    expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                    self.assertEqual(headers['sec-websocket-accept'], expected)
                    self.assertEqual(headers['sec-websocket-protocol'], 'fixture')
                    message, mask = b'hello', b'abcd'
                    sock.sendall(bytes([0x81, 0x80 | len(message)]) + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(message)))
                    opcode, size = reader.read(2)
                    self.assertEqual(opcode, 0x81)
                    if size == 126:
                        size = struct.unpack('!H', reader.read(2))[0]
                    self.assertEqual(json.loads(reader.read(size)), {'backend': owner, 'path': path, 'message': 'hello'})
                    sock.sendall(b'\x88\x80abcd')
                    self.assertEqual(reader.read(2), b'\x88\x00')

    def test_admitted_stream_finishes_after_maintenance_starts(self):
        controls = json.loads(os.environ['TESTS_ROUTING_CONTROLS'])
        control = Client(controls['django'])
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('/api', 'django')]:
            conn = connection()
            try:
                conn.request('GET', prefix + '/probe/stream')
                response = conn.getresponse()
                first = ('data: ' + owner + '\n\n').encode()
                self.assertEqual(response.read(len(first)), first)
                self.assertEqual(control.request('POST', '/__fixture/maintenance/on')[0], 200)
                # Observe Caddy's admission state instead of assuming the host's
                # flag write is immediately visible inside the container.
                poll(lambda: Client(os.environ['TESTS_ROUTING_URL']).request('GET', prefix + '/probe')[0], lambda code: code == 503, timeout=5, interval=.01)
                self.assertEqual(Client(os.environ['TESTS_ROUTING_URL']).request('POST', prefix + '/probe', {})[0], 503)
                self.assertEqual(Client(controls[owner]).request('POST', '/__fixture/release')[0], 200)
                self.assertEqual(response.read(), b'data: done\n\n')
            finally:
                control.request('POST', '/__fixture/maintenance/off')
                Client(controls[owner]).request('POST', '/__fixture/release')
                conn.close()
                poll(lambda: Client(os.environ['TESTS_ROUTING_URL']).request('GET', prefix + '/probe')[0], lambda code: code == 200, timeout=5, interval=.01)

    def test_chunked_upload_preserves_every_byte(self):
        chunks = [bytes(range(256)) * 257, b'\x00\xff\r\n', b'last chunk']
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('/api', 'django')]:
            conn = connection()
            try:
                conn.request('PUT', prefix + '/probe/chunked', body=iter(chunks),
                             headers={'Content-Type': 'application/octet-stream'}, encode_chunked=True)
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                data = json.loads(response.read())
                self.assertEqual((data['backend'], data['method'], data['path']), (owner, 'PUT', '/probe/chunked'))
                self.assertEqual(base64.b64decode(data['body']), b''.join(chunks))
            finally:
                conn.close()


class RoutingMaintenanceContracts(unittest.TestCase):
    def test_all_api_versions_block_mutations_but_keep_readiness(self):
        client = Client(os.environ['TESTS_ROUTING_URL'])
        for prefix, owner in [('/api/v1', 'django'), ('/api/v2', 'go'), ('/api', 'django')]:
            for method in ('GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'):
                status, _data, headers = client.request(method, prefix + '/probe', {}, include_headers=True)
                self.assertEqual(status, 503)
                self.assertEqual(headers['Retry-After'], '60')
                self.assertEqual(headers['Cache-Control'], 'no-store')
            status, data = client.request('GET', prefix + '/readyz')
            self.assertEqual(status, 200)
            self.assertEqual((data['backend'], data['path']), (owner, '/readyz'))
        for path in ('/ws/probe', '/bakney/v1/manifest', '/api/v1/ws/probe', '/api/v2/ws/probe'):
            self.assertEqual(client.request('GET', path)[0], 503)


class RoutingUnavailableContracts(unittest.TestCase):
    def test_go_failure_does_not_fall_back_or_break_django(self):
        client = Client(os.environ['TESTS_ROUTING_URL'])
        for prefix in ('/api/v1', '/api'):
            status, data = client.request('POST', prefix + '/probe', {})
            self.assertEqual((status, data['backend']), (200, 'django'))
        self.assertEqual(client.request('POST', '/api/v2/probe', {})[0], 502)
