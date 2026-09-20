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


def call_runner(method, path, payload=None):
    if deployment_mode() != 'production':
        raise UpdaterUnavailable('Gli aggiornamenti dell’installazione sono disabilitati in sviluppo.')
    directory = Path(getattr(settings, 'ASSOZETA_UPDATER_DIRECTORY', '/run/assozeta-updater'))
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
