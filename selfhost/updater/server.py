"""Private Unix-socket update service; independent from application containers."""
import fcntl
from http.server import BaseHTTPRequestHandler
import hmac
import json
import os
from pathlib import Path
import signal
from socketserver import ThreadingMixIn, UnixStreamServer
import sys
import threading
import time
import traceback
from uuid import UUID

from backups import BackupNotFound, inventory, open_archive
from diagnostics import backup_readiness
from backup_jobs import (BackupRejected, create_backup, delete_archive, run_backup, schedule_payload,
                         schedule_tick, set_keep, update_schedule)
from restore_jobs import ArchiveInvalid, UploadIncomplete, receive_upload, run_restore, start_restore
from engine import Engine
from journal import Journal, Conflict
from release_catalog import version_tuple
import remote_backups
from remote_backups import RemoteError, Uploader
from common import read_env, update_eligibility_reason
from status_access import AccessDenied, OwnershipUnavailable, authenticate_access_token, require_current_owner
from restart import launch_restart, reconcile_restarts

PROTOCOL = 1


def update_blocked_reason(root, env_file, journal):
    if (Path(root) / '.updater/pending-distribution').exists():
        return 'La transazione precedente richiede un ripristino prima di un nuovo aggiornamento.'
    if journal.requiring_recovery():
        return 'Un aggiornamento richiede una verifica di ripristino prima di un nuovo tentativo.'
    try:
        return update_eligibility_reason(read_env(env_file))
    except OSError:
        return 'La configurazione dell’installazione non è leggibile. Verifica il server prima di aggiornare.'


class Server(ThreadingMixIn, UnixStreamServer):
    daemon_threads = True


def validate_request(value):
    required = {'release_id', 'tag', 'request_id', 'actor_id', 'source_version'}
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError('Invalid update request fields.')
    if isinstance(value['release_id'], bool) or not isinstance(value['release_id'], int) or value['release_id'] < 1:
        raise ValueError('Invalid release identifier.')
    if not version_tuple(value['tag']) or not version_tuple(value['source_version']):
        raise ValueError('A stable source and target release is required.')
    UUID(value['request_id'])
    UUID(value['actor_id'])
    return value


def validate_restart_request(value, env_file):
    if not isinstance(value, dict) or set(value) != {'request_id', 'actor_id'}:
        raise ValueError('Invalid restart request fields.')
    UUID(value['request_id'])
    UUID(value['actor_id'])
    version = read_env(env_file).get('ASSOZETA_VERSION', 'unstable')
    return {**value, 'kind': 'restart', 'release_id': None, 'tag': version, 'source_version': version}


def serve(root, env_file, engine_class=Engine):
    root = Path(root).resolve()
    directory = root / '.updater'
    directory.mkdir(mode=0o700, exist_ok=True)
    # A second runner must never mark a live operation as interrupted.
    lock = (directory / 'runner.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    api_directory = Path('/run/assozeta-updater')
    token = (api_directory / 'token').read_text().strip()
    if len(token) < 32:
        raise RuntimeError('Runner credentials are not provisioned')
    journal = Journal(directory / 'operations.sqlite3')
    journal.interrupt_running()
    reconcile_restarts(journal)
    engine = engine_class(root, env_file, journal)
    uploader = Uploader(root, env_file)
    stop = threading.Event()
    wake = threading.Event()

    def blocked_reason():
        return update_blocked_reason(root, env_file, journal)

    def status_payload():
        records = journal.records()
        active = next((item for item in records if item['status'] in ('queued', 'running')), None)
        recovery_reason = blocked_reason()
        return {
            'available': not stop.is_set(), 'protocol': PROTOCOL,
            'can_restart': not stop.is_set() and not journal.requiring_recovery() and not (directory / 'pending-distribution').exists(),
            'can_update': not recovery_reason and not stop.is_set(),
            'reason': recovery_reason if not active else None,
            'runner_version': os.environ.get('RUNNER_VERSION', 'development'),
            'active': active, 'history': [item for item in records if item != active],
        }

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *args):
            pass

        def respond(self, code, value):
            payload = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(payload)

        def send_archive(self, name):
            try:
                stream, size = open_archive(root, name)
            except BackupNotFound:
                self.respond(404, {'error': 'Backup non trovato.'})
                return
            with stream:
                self.send_response(200)
                self.send_header('Content-Type', 'application/gzip')
                self.send_header('Content-Length', str(size))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                # The API forwards at the browser's pace; allow slow clients.
                self.request.settimeout(300)
                remaining = size
                while remaining > 0:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def authorized(self):
            if not hmac.compare_digest(self.headers.get('Authorization', ''), f'Bearer {token}'):
                self.respond(403, {'error': 'Access denied.'})
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == '/diagnostics':
                self.respond(200, backup_readiness(root))
                return
            if self.path == '/backups':
                try:
                    value = remote_backups.merge_inventory(root, inventory(root, env_file))
                    self.respond(200, {**value, 'schedule': schedule_payload(root, env_file),
                                       'actions': {'create': True, 'keep': True, 'delete': True, 'restore': True,
                                                   'upload': True, 'remote': True}})
                except OSError:
                    self.respond(503, {'error': 'La cartella dei backup non è leggibile. Verifica i permessi sul server.'})
                return
            if self.path == '/backups/remote':
                self.respond(200, remote_backups.public_config(root))
                return
            if self.path.startswith('/backups/'):
                self.send_archive(self.path[len('/backups/'):])
                return
            if self.path != '/status':
                self.respond(404, {'error': 'Unknown endpoint.'})
                return
            self.respond(200, status_payload())

        def read_body(self, limit=4096):
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length < limit or stop.is_set():
                raise ValueError('Request cannot be accepted.')
            return json.loads(self.rfile.read(length))

        def backup_action(self, action):
            """Owner backup actions; the API has already checked the owner."""
            if not self.authorized():
                return
            try:
                status, value = action(self.read_body())
                self.respond(status, value)
                wake.set()
            except (BackupRejected, RemoteError) as exc:
                self.respond(exc.status, {'error': str(exc)})
            except Conflict as exc:
                self.respond(409, {'error': str(exc)})
            except (ValueError, TypeError, KeyError):
                self.respond(400, {'error': 'Richiesta non valida.'})
            except OSError:
                self.respond(503, {'error': 'La cartella dei backup non è accessibile. Verifica i permessi sul server.'})

        def upload(self):
            """Stream an uploaded archive into the backup directory."""
            if not self.authorized():
                return
            # An early refusal must not leave the unread body on a reused connection.
            self.close_connection = True
            try:
                length = int(self.headers.get('Content-Length', ''))
                if length <= 0 or stop.is_set():
                    raise ValueError('Request cannot be accepted.')
                # The API forwards at the browser's pace; allow slow clients.
                self.request.settimeout(300)
                self.respond(201, {'archive': receive_upload(root, env_file, journal, self.rfile, length)})
            except BackupRejected as exc:
                self.respond(exc.status, {'error': str(exc)})
            except ArchiveInvalid as exc:
                self.respond(400, {'error': str(exc)})
            except UploadIncomplete:
                self.respond(400, {'error': 'Caricamento interrotto. Riprova.'})
            except (ValueError, TypeError):
                self.respond(400, {'error': 'Richiesta non valida.'})
            except OSError:
                try:
                    self.respond(503, {'error': 'Caricamento non riuscito. Verifica lo spazio e i permessi sul server.'})
                except OSError:
                    pass

        def archive_name(self):
            reserved = self.path == '/backups/schedule' or self.path.startswith('/backups/remote')
            return self.path[len('/backups/'):] if self.path.startswith('/backups/') and not reserved else None

        def do_PATCH(self):
            name = self.archive_name()
            if name is None:
                self.respond(404, {'error': 'Unknown endpoint.'})
                return
            self.backup_action(lambda body: (200, set_keep(root, env_file, journal, name, body)))

        def do_DELETE(self):
            name = self.archive_name()
            if name is None:
                self.respond(404, {'error': 'Unknown endpoint.'})
                return
            self.backup_action(lambda body: (200, delete_archive(root, env_file, journal, name, body)))

        def do_PUT(self):
            if self.path == '/backups/remote':
                self.backup_action(lambda body: (200, remote_backups.update_config(root, body)))
                return
            if self.path != '/backups/schedule':
                self.respond(404, {'error': 'Unknown endpoint.'})
                return
            self.backup_action(lambda body: (200, {'schedule': update_schedule(root, env_file, body),
                                                    'retention': inventory(root, env_file)['retention']}))

        def do_POST(self):
            if self.path == '/backups':
                self.backup_action(lambda body: (202, {'operation': create_backup(root, env_file, journal, body)}))
                return
            if self.path == '/backups/upload':
                self.upload()
                return
            if self.path.startswith('/backups/') and self.path.endswith('/restore'):
                name = self.path[len('/backups/'):-len('/restore')]
                self.backup_action(lambda body: (202, {'operation': start_restore(root, env_file, journal, name, body)}))
                return
            remote_actions = {
                '/backups/remote/test': lambda body: (200, remote_backups.test_connection(root, body)),
                '/backups/remote/key': lambda body: (201, remote_backups.create_key(root, body)),
                '/backups/remote/key/confirm': lambda body: (200, remote_backups.confirm_key(root, body)),
                '/backups/remote/fetch': lambda body: (202, {'operation': remote_backups.start_fetch(root, env_file, journal, body)}),
            }
            if self.path in remote_actions:
                self.backup_action(remote_actions[self.path])
                return
            if not self.authorized():
                return
            if self.path not in ('/updates', '/restarts'):
                self.respond(404, {'error': 'Unknown endpoint.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 4096 or stop.is_set():
                    raise ValueError('Request cannot be accepted.')
                body = json.loads(self.rfile.read(length))
                if self.path == '/restarts':
                    request = validate_restart_request(body, env_file)
                    reason = 'La transazione precedente richiede un ripristino.' if (directory / 'pending-distribution').exists() else None
                else:
                    request, reason = validate_request(body), blocked_reason()
                operation = journal.create(request, blocked_reason=reason)
                self.respond(202, {'operation': operation})
                wake.set()
            except (ValueError, TypeError, KeyError):
                self.respond(400, {'error': 'Invalid update request.'})
            except Conflict as exc:
                self.respond(409, {'error': str(exc)})

    class StatusHandler(Handler):
        def do_GET(self):
            self.respond(405, {'error': 'Method not allowed.'})

        # The status socket is world-writable: it serves only the owner status.
        do_PUT = do_PATCH = do_DELETE = do_GET

        def do_POST(self):
            if self.path != '/instance-update-status':
                self.respond(404, {'error': 'Unknown endpoint.'})
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 8192:
                    raise AccessDenied()
                body = json.loads(self.rfile.read(length))
                if body != {}:
                    raise AccessDenied()
                authorization = self.headers.get('Authorization', '')
                if not authorization.startswith('Bearer '):
                    raise AccessDenied()
                actor = authenticate_access_token(authorization[7:], read_env(env_file))
                require_current_owner(root, env_file, actor)
                self.respond(200, {'is_owner': True, **status_payload()})
            except (AccessDenied, ValueError, TypeError, KeyError):
                self.respond(403, {'error': 'Access denied.'})
            except OwnershipUnavailable:
                self.respond(503, {'error': 'La verifica del proprietario non è momentaneamente disponibile.'})

    def work():
        last_schedule_check = 0.0
        while not stop.is_set():
            reconcile_restarts(journal)
            if time.monotonic() - last_schedule_check >= 30:
                last_schedule_check = time.monotonic()
                try:
                    schedule_tick(root, env_file, journal)
                except Exception:
                    # A broken schedule file must never stop updates or restarts.
                    traceback.print_exc()
                try:
                    # Uploads run in their own thread: nothing here waits for them.
                    uploader.kick()
                except Exception:
                    traceback.print_exc()
            pending = next((item for item in journal.records() if item['status'] == 'queued'), None)
            if pending:
                if pending.get('kind') == 'restart':
                    launch_restart(root, env_file, journal, pending)
                elif pending.get('kind') == 'backup':
                    run_backup(root, env_file, journal, pending)
                    uploader.kick()
                elif pending.get('kind') == 'restore':
                    run_restore(root, env_file, journal, pending)
                    # The restore takes a pre-restore backup: copy it off-site too.
                    uploader.kick()
                elif pending.get('kind') == 'fetch':
                    remote_backups.run_fetch(root, env_file, journal, pending)
                else:
                    engine.execute(pending)
            else:
                wake.wait(2)
                wake.clear()

    socket_path = api_directory / 'runner.sock'
    socket_path.unlink(missing_ok=True)
    server = Server(str(socket_path), Handler)
    os.chown(socket_path, 10001, 10001)
    os.chmod(socket_path, 0o660)
    status_directory = Path('/run/assozeta-update-status')
    status_directory.mkdir(mode=0o755, exist_ok=True)
    status_path = status_directory / 'status.sock'
    status_path.unlink(missing_ok=True)
    status_server = Server(str(status_path), StatusHandler)
    os.chmod(status_path, 0o666)
    status_thread = threading.Thread(target=status_server.serve_forever, kwargs={'poll_interval': 0.5})
    status_thread.start()
    worker = threading.Thread(target=work)
    worker.start()

    def shutdown(signum, frame):
        stop.set()
        wake.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        wake.set()
        worker.join()
        status_server.shutdown()
        status_thread.join()
        status_server.server_close()
        status_path.unlink(missing_ok=True)
        server.server_close()
        socket_path.unlink(missing_ok=True)
        lock.close()


if __name__ == '__main__':
    serve(sys.argv[1], sys.argv[2])
