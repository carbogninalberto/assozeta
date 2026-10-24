"""Native server lifetime used by the category HTTP oracle.

Image rehearsals supply the same context-manager interface with their packaged
server. The response and persistence contracts stay owned by the neutral oracle.
"""
from contextlib import contextmanager
import socket
import subprocess

from TESTS.adapters.go.server import build_server
from TESTS.http_client import Client, poll


@contextmanager
def native_server(work, log, tool_env, address, *, versioned=False):
    from django.conf import settings

    executable = work / 'payment-categories-server'
    build_server(executable, log, tool_env)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        http_port = listener.getsockname()[1]
    host, port = address.rsplit(':', 1)
    database = settings.DATABASES['default']
    environment = {
        **tool_env,
        'DBTYPE': 'POSTGRES', 'DBNAME': database['NAME'], 'DBUSER': database['USER'],
        'DBPASSWORD': database['PASSWORD'], 'DBHOST': database['HOST'],
        'DBPORT': str(database['PORT']),
        'REDIS_HOST': host, 'REDIS_PORT': port, 'REDIS_USERNAME': 'default',
        'REDIS_PASSWORD': 'synthetic-identity-only',
        'GO_LISTEN_ADDR': f'{"0.0.0.0" if versioned else "127.0.0.1"}:{http_port}',
        'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False',
        'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
        **{name: getattr(settings, name) for name in (
            'SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY',
            'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME',
            'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION',
            'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET',
        )},
    }
    process = subprocess.Popen([str(executable)], cwd=work, env=environment,
                               stdout=log, stderr=subprocess.STDOUT)
    base = f'http://127.0.0.1:{http_port}'
    try:
        def ready():
            if process.poll() is not None:
                raise RuntimeError('Go payment-category server exited')
            try:
                return Client(base).request('GET', '/readyz')[0] == 200
            except OSError:
                return False

        poll(ready, bool, timeout=30)
        yield {'base_url': base, 'proxy_upstream': f'host.docker.internal:{http_port}',
               'network': None, 'metadata': {'kind': 'native-built-server'}}
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
