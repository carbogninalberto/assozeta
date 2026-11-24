"""Read-only Unix socket for the backup notices sent by job workers."""
import fcntl
import hmac
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import secrets
from socketserver import ThreadingUnixStreamServer
from tempfile import TemporaryDirectory
import threading

DIRECTORY = Path('/run/assozeta-updater-notices')
SOCKET = 'notices.sock'
PATH = '/backup-failures'


def credentials(directory=DIRECTORY, owner=(10001, 10001)):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    token = directory / 'token'
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        directory.chmod(0o700)
        if not token.exists():
            with TemporaryDirectory(dir=directory) as staging:
                fresh = Path(staging) / 'token'
                with fresh.open('x') as stream:
                    stream.write(secrets.token_hex(32) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
                fresh.chmod(0o600)
                fresh.replace(token)
                os.fsync(descriptor)
        if len(token.read_text().strip()) < 32:
            raise RuntimeError('Invalid existing notices credentials')
        token.chmod(0o600)
        if owner is not None:
            os.chown(token, *owner)
            os.fchown(descriptor, *owner)
    finally:
        os.close(descriptor)
    return token


def backup_failures(status, inventory):
    def fields(record, keys):
        return {key: record[key] for key in keys if key in record}

    last_run = (inventory.get('schedule') or {}).get('last_run') or {}
    last_failure = (inventory.get('remote') or {}).get('last_failure') or {}
    return {
        'history': [fields(operation, ('id', 'kind', 'status', 'created_at', 'updated_at', 'error'))
                    for operation in status.get('history', [])
                    if operation.get('kind') == 'backup' and operation.get('status') == 'failed'],
        'schedule': {'last_run': fields(last_run, ('at', 'status', 'operation', 'error'))},
        'remote': {'last_failure': fields(last_failure, ('at', 'error', 'name'))},
    }


def start(directory, token, payload):
    authorization = ('Bearer ' + token).encode()

    class Handler(BaseHTTPRequestHandler):
        timeout = 10

        def log_message(self, *args):
            pass

        def parse_request(self):
            if not super().parse_request():
                return False
            if self.command != 'GET':
                self.send_error(405)
                return False
            return True

        def do_GET(self):
            # BaseHTTPRequestHandler normalizes leading double slashes in self.path.
            if self.requestline.split()[1] != PATH:
                self.send_error(404)
                return
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode(), authorization):
                self.send_error(403)
                return
            try:
                body = json.dumps(payload()).encode()
            except OSError:
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    socket_path = directory / SOCKET
    socket_path.unlink(missing_ok=True)
    server = ThreadingUnixStreamServer(str(socket_path), Handler)
    server.daemon_threads = True
    try:
        socket_path.chmod(0o660)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except BaseException:
        server.server_close()
        socket_path.unlink(missing_ok=True)
        raise
    return server
