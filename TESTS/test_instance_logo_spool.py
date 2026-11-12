"""Real HTTP failure controls for the packaged logo resource harness.

Run with the production Python environment:
python -m unittest TESTS.test_instance_logo_spool
"""
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
import unittest

from TESTS.adapters.go.instance_logo_spool import BLOCK, Deployment, Source


@contextmanager
def source():
    stop = threading.Event()
    observations = {'trickle_bytes': 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(500 if self.path == '/reject' else 200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(BLOCK)))
            if self.path in ('/close', '/stall-close'):
                self.send_header('Connection', 'close')
            self.end_headers()
            try:
                if self.path in ('/success', '/close'):
                    self.wfile.write(BLOCK)
                elif self.path == '/trickle':
                    while not stop.wait(.02):
                        self.wfile.write(b'x')
                        self.wfile.flush()
                        observations['trickle_bytes'] += 1
                else:
                    stop.wait(5)
            except OSError:
                pass  # Expected when the deadline/peer failure closes the client.

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01))
    thread.start()

    def request(path):
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        connection.request('GET', path)
        return connection, connection.getresponse()

    try:
        yield request, observations
    finally:
        stop.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive(), 'Owned HTTP server did not stop'


class LogoSpoolDeadlines(unittest.TestCase):
    def test_exact_body_and_close_delimited_socket_ownership(self):
        with source() as (request, _):
            for path in ('/success', '/close'):
                with self.subTest(path=path):
                    connection, response = request(path)
                    result = Deployment.finish(connection, response, len(BLOCK), total_timeout=1)
                    self.assertEqual(result['bytes'], len(BLOCK))
                    self.assertEqual(result['sha256'], Source.digest(len(BLOCK)))
                    self.assertTrue(result['exact_logo_bytes'])
                    self.assertIsNone(connection.sock)
                    self.assertTrue(response.closed)

    def test_stalled_body_expires_and_closes_socket(self):
        self.assert_deadline('/stall')

    def test_continuous_trickle_cannot_extend_total_deadline(self):
        self.assert_deadline('/trickle')

    def assert_deadline(self, path):
        with source() as (request, observations):
            connection, response = request(path)
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                Deployment.finish(connection, response, len(BLOCK), total_timeout=.3)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, .25)
            self.assertLess(elapsed, 2, 'Total deadline was extended by ongoing/stalled I/O')
            self.assertIsNone(connection.sock)
            self.assertTrue(response.closed)
            if path == '/trickle':
                self.assertGreater(observations['trickle_bytes'], 2)

    def test_failed_peer_aborts_stalled_consumers_before_executor_join(self):
        self.assert_peer_abort(('/reject', '/stall', '/trickle', '/stall'))

    def test_later_failed_peer_aborts_earlier_close_header_consumers(self):
        self.assert_peer_abort(('/stall-close', '/trickle', '/reject', '/stall-close'))

    def assert_peer_abort(self, paths):
        with source() as (request, _):
            held = [request(path) for path in paths]
            threads_before = {thread.ident for thread in threading.enumerate()}
            started = time.monotonic()
            with self.assertRaisesRegex(AssertionError, 'Logo status 500'):
                Deployment.finish_concurrently(held, len(BLOCK), total_timeout=5)
            self.assertLess(time.monotonic() - started, 2,
                            'Failed peer left executor waiting for its five-second body deadline')
            self.assertFalse([thread for thread in threading.enumerate()
                              if thread.ident not in threads_before
                              and thread.name.startswith('ThreadPoolExecutor')])
            for connection, response in held:
                self.assertIsNone(connection.sock)
                self.assertTrue(response.closed)


if __name__ == '__main__':
    unittest.main()
