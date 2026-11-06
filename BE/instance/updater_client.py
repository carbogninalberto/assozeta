"""Private authenticated Unix-socket transport to the independent update runner."""
import http.client
import json
import socket
from pathlib import Path

from django.conf import settings


class UpdaterUnavailable(Exception):
    pass


class UpdaterRejected(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__('localhost', timeout=5)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


def deployment_mode():
    return getattr(settings, 'ASSOZETA_DEPLOYMENT_MODE', 'development')


def _runner_directory():
    if deployment_mode() != 'production':
        raise UpdaterUnavailable('Gli aggiornamenti dell’installazione sono disabilitati in sviluppo.')
    return Path(getattr(settings, 'ASSOZETA_UPDATER_DIRECTORY', '/run/assozeta-updater'))


class RunnerBody:
    """File-like runner response that owns, and closes, its connection."""

    def __init__(self, connection, response):
        self.connection = connection
        self.response = response

    def read(self, size=-1):
        return self.response.read(size)

    def close(self):
        self.response.close()
        self.connection.close()


def open_runner_stream(path):
    """GET a binary runner resource; return (size, RunnerBody)."""
    directory = _runner_directory()
    connection = UnixConnection(directory / 'runner.sock')
    connection.timeout = 300
    try:
        token = (directory / 'token').read_text().strip()
        if not token:
            raise OSError('Missing runner credentials')
        connection.request('GET', path, headers={'Authorization': f'Bearer {token}'})
        response = connection.getresponse()
        if response.status >= 400:
            try:
                message = json.loads(response.read()).get('error', 'Risorsa non disponibile.')
            except ValueError:
                message = 'Risorsa non disponibile.'
            raise UpdaterRejected(message, response.status)
        size = int(response.getheader('Content-Length', ''))
    except UpdaterRejected:
        connection.close()
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        connection.close()
        raise UpdaterUnavailable('Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.') from exc
    return size, RunnerBody(connection, response)


def call_runner(method, path, payload=None):
    directory = _runner_directory()
    connection = UnixConnection(directory / 'runner.sock')
    try:
        token = (directory / 'token').read_text().strip()
        if not token:
            raise OSError('Missing runner credentials')
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers={
            'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
        })
        response = connection.getresponse()
        body = json.loads(response.read())
        if response.status >= 400:
            raise UpdaterRejected(body.get('error', 'Aggiornamento non disponibile.'), response.status)
        return body
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise UpdaterUnavailable('Il servizio di aggiornamento non è raggiungibile. Verifica l’esito della configurazione automatica.') from exc
    finally:
        connection.close()
