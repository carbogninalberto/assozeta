#!/usr/bin/env python3
"""Real Caddy transport rehearsal; synthetic upstreams are not backend parity."""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.caddy import proxy
from TESTS.http_client import Client, poll


def upstream(owner):
    release = threading.Event()
    connections, guard = set(), threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.settimeout(15)
            with guard:
                connections.add(self.connection)

        def finish(self):
            with guard:
                connections.discard(self.connection)
            super().finish()

        def log_message(self, *_args):
            pass

        def fixed(self, status, body, headers=()):
            self.send_response(status)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Fixture-Backend', owner)
            for key, value in headers:
                self.send_header(key, value)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def respond(self):
            if self.headers.get('Transfer-Encoding', '').lower() == 'chunked':
                chunks = []
                while True:
                    size = int(self.rfile.readline().split(b';', 1)[0], 16)
                    if not size:
                        assert self.rfile.readline() == b'\r\n'
                        break
                    chunks.append(self.rfile.read(size))
                    assert self.rfile.read(2) == b'\r\n'
                body = b''.join(chunks)
            else:
                body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            if self.path == '/__fixture/release':
                release.set()
                return self.fixed(200, b'{}')
            if self.path in ('/__fixture/maintenance/on', '/__fixture/maintenance/off'):
                flag = self.server.operations / 'maintenance.flag'
                if self.path.endswith('/on'):
                    flag.touch()
                else:
                    flag.unlink(missing_ok=True)
                return self.fixed(200, b'{}')
            if self.path == '/probe/download':
                ranged = self.headers.get('Range') == 'bytes=16-47'
                headers = [('Content-Type', 'application/octet-stream'),
                           ('Content-Disposition', "attachment; filename*=UTF-8''caf%C3%A8.pdf"),
                           ('ETag', '"synthetic-download"')]
                if ranged:
                    headers.append(('Content-Range', 'bytes 16-47/256'))
                return self.fixed(206 if ranged else 200, bytes(range(256))[16:48] if ranged else bytes(range(256)), headers)
            if self.path == '/probe/redirect':
                return self.fixed(307, b'fixture redirect', [
                    ('Location', '/api/document/retrieve?token=synthetic%2Bvalue'),
                    ('Set-Cookie', 'first=1; HttpOnly; Secure; SameSite=Lax'),
                    ('Set-Cookie', 'second=2; Path=/; Secure')])
            if self.path == '/probe/stream':
                release.clear()
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                def chunk(data):
                    self.wfile.write(f'{len(data):x}\r\n'.encode() + data + b'\r\n')
                    self.wfile.flush()
                chunk(('data: ' + owner + '\n\n').encode())
                if not release.wait(10):
                    raise AssertionError('Client did not receive/release the stream')
                chunk(b'data: done\n\n')
                self.wfile.write(b'0\r\n\r\n')
                return
            if self.path.startswith('/ws/') and self.headers.get('Upgrade', '').lower() == 'websocket':
                key = self.headers['Sec-WebSocket-Key']
                accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
                self.send_response(101)
                for name, value in [('Connection', 'Upgrade'), ('Upgrade', 'websocket'),
                                    ('Sec-WebSocket-Accept', accept), ('Sec-WebSocket-Protocol', 'fixture')]:
                    self.send_header(name, value)
                self.end_headers()
                opcode, size = self.rfile.read(2)
                assert opcode == 0x81 and size & 0x80 and size & 0x7f < 126
                mask = self.rfile.read(4)
                text = bytes(c ^ mask[i % 4] for i, c in enumerate(self.rfile.read(size & 0x7f))).decode()
                payload = json.dumps({'backend': owner, 'path': self.path, 'message': text}).encode()
                size = bytes([len(payload)]) if len(payload) < 126 else b'\x7e' + struct.pack('!H', len(payload))
                self.wfile.write(b'\x81' + size + payload)
                self.wfile.flush()
                assert self.rfile.read(6) == b'\x88\x80abcd'
                self.wfile.write(b'\x88\x00')
                self.close_connection = True
                return
            data = {'backend': owner, 'method': self.command, 'path': self.path,
                    'body': base64.b64encode(body).decode(),
                    'content_type': self.headers.get('Content-Type'),
                    'authorization': self.headers.get('Authorization'),
                    'cookie': self.headers.get('Cookie'), 'cid': self.headers.get('X-Correlation-ID')}
            payload = json.dumps(data).encode()
            code = int(self.path.rsplit('/', 1)[1]) if self.path.startswith('/probe/status/') else 200
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('X-Fixture-Backend', owner)
            self.send_header('Retry-After', '7')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = respond

    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def disconnect():
        with guard:
            current = list(connections)
        for sock in current:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
    server.disconnect = disconnect
    return server, thread


def main():
    output = ROOT / 'quality-reports/go-migration/versioned-routing'
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / 'results.json'
    result_path.write_text(json.dumps({'success': False, 'phase': 'started'}) + '\n')
    servers = [upstream(owner) for owner in ('django', 'go')]
    try:
        with (output / 'caddy.log').open('w') as log, tempfile.TemporaryDirectory(prefix='routing-operations-') as temporary:
            for server, _thread in servers:
                server.operations = Path(temporary)
            addresses = [f'host.docker.internal:{server.server_port}' for server, _ in servers]
            controls = {owner: f'http://127.0.0.1:{server.server_port}' for owner, (server, _) in zip(('django', 'go'), servers)}
            with proxy(*addresses, log, operations=temporary) as (base, image):
                with patch.dict(os.environ, TESTS_ROUTING_URL=base, TESTS_ROUTING_CONTROLS=json.dumps(controls)):
                    def contracts(name):
                        result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_versioned_routing.' + name))
                        assert result.wasSuccessful() and not result.skipped
                    contracts('VersionedRoutingContracts')
                    maintenance = Path(temporary) / 'maintenance.flag'
                    maintenance.touch()
                    try:
                        poll(lambda: Client(base).request('GET', '/api/probe')[0], lambda code: code == 503, timeout=5, interval=.01)
                        contracts('RoutingMaintenanceContracts')
                    finally:
                        maintenance.unlink()
                    poll(lambda: Client(base).request('GET', '/api/probe')[0], lambda code: code == 200, timeout=5, interval=.01)
                    servers[1][0].shutdown()
                    servers[1][0].server_close()
                    servers[1][0].disconnect()
                    contracts('RoutingUnavailableContracts')
                report = {'success': True, 'image': image, 'scope': 'actual self-host Caddy; synthetic transport upstreams; no application parity or coordinated backend switch claim'}
                result_path.write_text(json.dumps(report, indent=2) + '\n')
                print(json.dumps(report))
    finally:
        for server, thread in servers:
            server.shutdown()
            server.server_close()
            server.disconnect()
            thread.join(timeout=5)


if __name__ == '__main__':
    main()
