"""Native server lifetime used by the category HTTP oracle.

Image rehearsals supply the same context-manager interface with their packaged
server. The response and persistence contracts stay owned by the neutral oracle.
"""
from contextlib import contextmanager
import json
import os
import socket
import subprocess

from TESTS.adapters.go.server import build_server
from TESTS.http_client import Client, poll


INSTANCE_ADMIN_ENVIRONMENT = (
    'APP_URL', 'APP_HOST', 'ASSOZETA_VERSION', 'ASSOZETA_DEPLOYMENT_MODE', 'WHITELABEL_NAME', 'IS_WHITELABEL', 'EMAIL_HOST', 'EMAIL_PORT',
    'EMAIL_HOST_USER', 'EMAIL_HOST_PASSWORD', 'EMAIL_USE_TLS', 'EMAIL_USE_SSL', 'AI_MODEL', 'AI_CHEAP_MODEL', 'AI_BASE_URL',
    'MCP_AGENT_MAX_ITERATIONS', 'MCP_AGENT_MAX_RESULTS', 'MCP_AGENT_QUERY_TIMEOUT', 'MCP_AGENT_WS_RATE_LIMIT',
    'MCP_AGENT_WS_TIMEOUT', 'MCP_AGENT_HISTORY_CAP')
DOCUMENT_RENDER_ENVIRONMENT = ('PUPPETEER_HOST', 'PUPPETEER_PORT', 'CURRENT_HOST_PUPPETEER', 'DOCUMENT_BYPASS_TOKEN', 'EMAIL_HOST_SUPPORT')
GOOGLE_ENVIRONMENT = ('GOOGLE_CLIENT_SECRET_FILE', 'GOOGLE_CALENDAR_REDIRECT_URL', 'GOOGLE_OAUTH2_BASE', 'GOOGLE_API_BASE')
# A loopback port is chosen by binding port 0 and releasing it, so a concurrent
# run can claim it before the server binds; such a start is retried on a new port.
PORT_ATTEMPTS = 3
PORT_IN_USE = 'address already in use'


def free_http_port():
    """Return a loopback port that is free right now (it is not reserved)."""
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def log_since(log, offset):
    """Return the text one attempt appended to the service log after ``offset``."""
    name = getattr(log, 'name', None)
    if not isinstance(name, (str, os.PathLike)):
        return ''
    try:
        with open(name, 'r', errors='replace') as stream:
            stream.seek(offset)
            return stream.read()
    except OSError:
        return ''


@contextmanager
def native_server(work, log, tool_env, address, *, versioned=False, process_observer=None, query_observation=False):
    from django.conf import settings

    executable = work / 'payment-categories-server'
    build_server(executable, log, tool_env)
    host, port = address.rsplit(':', 1)
    database = settings.DATABASES['default']
    environment = {
        **tool_env,
        'DBTYPE': 'POSTGRES', 'DBNAME': database['NAME'], 'DBUSER': database['USER'],
        'DBPASSWORD': database['PASSWORD'], 'DBHOST': database['HOST'],
        'DBPORT': str(database['PORT']),
        'REDIS_HOST': host, 'REDIS_PORT': port, 'REDIS_USERNAME': 'default',
        'REDIS_PASSWORD': 'synthetic-identity-only',
        'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False',
        'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
        **{name: getattr(settings, name) for name in (
            'SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY',
            'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME',
            'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION',
            'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET', 'SENJA_WEBHOOK_SECRET',
            'INSTANCE_SETUP_TOKEN',
        )},
        # The process environment Django read for instance administration
        # (core.settings reads these with env.str/env.bool/os.environ.get).
        **{name: os.environ[name] for name in INSTANCE_ADMIN_ENVIRONMENT if name in os.environ},
        # A family-owned Stripe API stub (stripe-routes service_context); unset in production.
        **({'STRIPE_API_BASE': os.environ['STRIPE_API_BASE']} if 'STRIPE_API_BASE' in os.environ else {}),
        # Family-owned outbound stubs (instance-outbound service_context): the
        # HTTPS proxy and the private certificate authority; unset in production.
        **{name: os.environ[name] for name in ('HTTPS_PROXY', 'NO_PROXY', 'SSL_CERT_FILE') if name in os.environ},
        # The PDF renderer (a family-owned stub in document-render) and the document settings Django reads.
        **{name: os.environ[name] for name in DOCUMENT_RENDER_ENVIRONMENT if name in os.environ},
        # The google-calendar family's client secrets file and private Google stub; unset in production.
        **{name: os.environ[name] for name in GOOGLE_ENVIRONMENT if name in os.environ},
        # SessionMiddleware's cookie flag as this Django process resolved it (env.bool, default not DEBUG).
        'SESSION_COOKIE_SECURE': str(settings.SESSION_COOKIE_SECURE),
        # Family-owned process settings (for example the sso-pairing authority stub
        # and the development flags Django reads at request time); unset otherwise.
        **json.loads(os.environ.get('GO_FIXTURE_ENVIRONMENT', '{}')),
    }
    command = [str(executable)]
    observation_path = work / "query-observations.jsonl" if query_observation else None
    if observation_path is not None:
        command.extend(["--query-observation-file", str(observation_path)])

    def stop(process):
        """Terminate the attempt's server; an already exited process is left alone."""
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    process = base = None
    for attempt in range(PORT_ATTEMPTS):
        http_port = free_http_port()
        listen_address = f'{"0.0.0.0" if versioned else "127.0.0.1"}:{http_port}'
        log.flush()
        offset = log.tell()
        process = subprocess.Popen(command, cwd=work, env={**environment, 'GO_LISTEN_ADDR': listen_address},
                                   stdout=log, stderr=subprocess.STDOUT)
        base = f'http://127.0.0.1:{http_port}'

        def ready():
            if process.poll() is not None:
                raise RuntimeError('Go payment-category server exited')
            try:
                return Client(base).request('GET', '/readyz')[0] == 200
            except OSError:
                return False

        try:
            poll(ready, bool, timeout=30)
        except RuntimeError:
            # Only a lost port race is retried; any other early exit is fatal.
            stop(process)
            if attempt + 1 >= PORT_ATTEMPTS or PORT_IN_USE not in log_since(log, offset):
                raise
            continue
        except BaseException:
            # A readiness timeout or an interrupt must not leak the server.
            stop(process)
            raise
        break
    try:
        if process_observer is not None:
            process_observer(process)
        runtime = {'base_url': base, 'proxy_upstream': f'host.docker.internal:{http_port}',
                   'network': None, 'metadata': {'kind': 'native-built-server'}}
        if observation_path is not None:
            runtime['query_observation_path'] = observation_path
        yield runtime
    finally:
        stop(process)
        if observation_path is not None and process.returncode != 0:
            raise RuntimeError('Go query observation server did not finalize successfully')
