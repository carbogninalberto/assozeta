"""Private WhatsApp Cloud API stub shared by the WhatsApp process harnesses (no __main__).

A loopback HTTP server that records every request (method, path, parsed query,
Authorization, Content-Type, raw body) and answers from an authored route
table. Both backends reach it through WHATSAPP_API_BASE_URL; nothing is ever
sent to graph.facebook.com. A reply is (status, headers, body bytes) or
'close' (the connection is dropped after the request is read).
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import parse_qs, urlsplit


class MetaStub:
    def __init__(self):
        self.lock = threading.Lock()
        self.requests, self.replies, self.default = [], {}, (500, {}, b'{}')
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def handle_one(self):
                length = int(self.headers.get('Content-Length') or 0)
                body = self.rfile.read(length) if length else b''
                parts = urlsplit(self.path)
                record = {'method': self.command, 'path': parts.path, 'query': parse_qs(parts.query, keep_blank_values=True),
                          'authorization': self.headers.get('Authorization'), 'content_type': self.headers.get('Content-Type'),
                          'body': body.decode('utf-8', 'replace')}
                with stub.lock:
                    stub.requests.append(record)
                    reply = stub.reply_for(record, body)
                if reply == 'close':
                    self.close_connection = True
                    self.connection.close()
                    return
                status, headers, content = reply
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(content)
                self.close_connection = True

            do_GET = do_POST = handle_one

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        return f'http://127.0.0.1:{self.server.server_address[1]}'

    def reply_for(self, record, body):
        """Messages are answered by recipient ('to'), template listings by their 'after' cursor ('' for the first page)."""
        if record['path'].endswith('/messages'):
            try:
                key = json.loads(body).get('to')
            except (ValueError, AttributeError):
                key = None
        elif record['method'] == 'GET':
            key = 'list:' + (record['query'].get('after') or [''])[0]
        else:
            key = 'register'
        return self.replies.get(key, self.default)

    def reset(self, replies, default=None):
        with self.lock:
            self.requests, self.replies = [], dict(replies)
            self.default = default or (500, {}, b'{}')

    def taken(self):
        with self.lock:
            return list(self.requests)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def json_reply(status, value, headers=None):
    return status, {'Content-Type': 'application/json', **(headers or {})}, json.dumps(value).encode()


def raw_reply(status, text, content_type='application/json', headers=None):
    return status, {'Content-Type': content_type, **(headers or {})}, text.encode()
