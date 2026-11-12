"""Unit tests for per-run port and Redis ownership in parallel parity runs.

Everything runs with mocks: no server, subprocess or socket is started. The
diagnostics probe address, the private Redis start and the Go server port race
are the three places where concurrent runs used to share an address.
"""
import ast
import io
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'BE') not in sys.path:
    # BE/ is not on the path for the inventory suite; this module import is inert.
    sys.path.insert(0, str(ROOT / 'BE'))

from instance import diagnostic_probe
from TESTS.adapters.django import historical_read_routes as adapter


# ---------------------------------------------------------------------------
# Diagnostics probe address
# ---------------------------------------------------------------------------

@pytest.fixture
def probe_settings(monkeypatch):
    """The settings the probes read, cached on Django's lazy object.

    Writing into ``__dict__`` supplies the values the probe reads without
    configuring Django globally (which would leak into the rest of the suite).
    """
    from django.conf import settings
    monkeypatch.setitem(settings.__dict__, 'APP_URL', 'https://app.example.test')
    monkeypatch.setitem(settings.__dict__, 'RUNNING_VERSION', 'v-test')
    monkeypatch.setitem(settings.__dict__, 'DIAGNOSTICS_API_URL', 'http://127.0.0.1:41234')
    return settings


@pytest.mark.parametrize('configured', ['http://127.0.0.1:41234', 'http://127.0.0.1:8000'])
def test_api_probe_address_follows_the_setting(monkeypatch, probe_settings, configured):
    seen = []
    monkeypatch.setattr(diagnostic_probe, 'health_response',
                        lambda url, version=None: seen.append((url, version)) or True)
    monkeypatch.setitem(probe_settings.__dict__, 'DIAGNOSTICS_API_URL', configured)

    outcome = diagnostic_probe.probe('api', {})

    assert outcome['status'] == 'passed'
    assert seen == [(configured + '/healthz', 'v-test')]


def test_settings_read_the_probe_address_from_the_environment():
    """The setting is the environment variable, defaulted to the local API."""
    source = (ROOT / 'BE' / 'core' / 'settings.py').read_text(encoding='utf-8')
    assignment = next(
        node for node in ast.parse(source).body
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == 'DIAGNOSTICS_API_URL'
            for target in node.targets))

    text = ast.get_source_segment(source, assignment)

    assert "'ASSOZETA_DIAGNOSTICS_API_URL'" in text
    assert 'http://127.0.0.1:8000' in text


def test_public_url_probe_still_uses_app_url(monkeypatch, probe_settings):
    seen = []
    monkeypatch.setattr(diagnostic_probe, 'health_response',
                        lambda url, version=None: seen.append((url, version)) or True)
    # The local API address is never the public URL, however it is configured.
    monkeypatch.setitem(probe_settings.__dict__, 'DIAGNOSTICS_API_URL', 'http://127.0.0.1:59999')

    outcome = diagnostic_probe.probe('public_url', {})

    assert outcome['status'] == 'passed'
    assert seen == [('https://app.example.test/api/healthz', 'v-test')]


# ---------------------------------------------------------------------------
# Private Redis ownership
# ---------------------------------------------------------------------------

class FakeProcess:
    """The subprocess interface stop_process() and the readiness loop use."""

    def __init__(self, pid, exited=False):
        self.pid = pid
        self.returncode = None if not exited else 1
        self.terminated = 0
        self.waited = 0

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated += 1
        self.returncode = -15

    def wait(self, timeout=None):
        self.waited += 1
        self.returncode = -15
        return self.returncode


class FakeRedisError(Exception):
    pass


@pytest.fixture
def fake_redis(monkeypatch):
    """Replace the redis client: only the given ports answer, with a process id."""
    import redis
    owners, clients, closed = {}, [], []

    class FakeClient:
        def __init__(self, **kwargs):
            self.port = kwargs['port']
            self.password = kwargs['password']
            self.timeouts = (kwargs['socket_timeout'], kwargs['socket_connect_timeout'])
            clients.append(self)

        def info(self, section):
            assert section == 'server'
            pid = owners.get(self.port)
            if pid is None:
                raise FakeRedisError('no Redis on this port')
            return {'process_id': pid}

        def close(self):
            closed.append(self)

    monkeypatch.setattr(redis, 'Redis', FakeClient)
    monkeypatch.setattr(redis, 'RedisError', FakeRedisError, raising=False)
    return types.SimpleNamespace(owners=owners, clients=clients, closed=closed)


class Launcher:
    """Scripted ``subprocess.Popen`` for the private Redis start."""

    def __init__(self):
        self.commands = []
        self.processes = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        assert kwargs['stdout'] is not None
        return self.processes.pop(0)

    def port(self, index):
        command = self.commands[index]
        return command[command.index('--port') + 1]


@pytest.fixture
def redis_ports(monkeypatch):
    ports = []
    monkeypatch.setattr(adapter, 'available_loopback_port', lambda: ports.pop(0))
    return ports


@pytest.fixture
def redis_launcher(monkeypatch):
    launcher = Launcher()
    monkeypatch.setattr(adapter.subprocess, 'Popen', launcher)
    return launcher


def test_redis_start_retries_when_the_process_exits(fake_redis, redis_ports, redis_launcher):
    redis_ports[:] = [5001, 5002]
    exited, healthy = FakeProcess(pid=101, exited=True), FakeProcess(pid=202)
    redis_launcher.processes[:] = [exited, healthy]
    fake_redis.owners[5002] = 202

    process, port = adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

    assert (process, port) == (healthy, 5002)
    assert (redis_launcher.port(0), redis_launcher.port(1)) == ('5001', '5002')
    # The failed attempt had already exited, so it is only reaped, never signalled.
    assert exited.poll() is not None and exited.terminated == 0
    assert [client.port for client in fake_redis.clients] == [5002]
    assert fake_redis.closed == fake_redis.clients


def test_redis_start_retries_when_a_foreign_process_answers(fake_redis, redis_ports, redis_launcher):
    redis_ports[:] = [6001, 6002]
    foreign_port_holder, healthy = FakeProcess(pid=111), FakeProcess(pid=222)
    redis_launcher.processes[:] = [foreign_port_holder, healthy]
    fake_redis.owners.update({6001: 999, 6002: 222})

    process, port = adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

    assert (process, port) == (healthy, 6002)
    assert foreign_port_holder.terminated == 1 and foreign_port_holder.waited == 1
    assert foreign_port_holder.poll() is not None
    assert [client.port for client in fake_redis.clients] == [6001, 6002]
    assert all(client.password == 'synthetic-identity-only' for client in fake_redis.clients)
    assert all(client.timeouts == (2, 2) for client in fake_redis.clients)
    # Every client, including the one that found the foreign server, is closed.
    assert fake_redis.closed == fake_redis.clients


def test_redis_start_keeps_a_live_process_when_nothing_answers(monkeypatch, fake_redis, redis_ports, redis_launcher):
    # An unreachable port is not another run's Redis: the run's own readiness
    # check reports a server that never became available.
    monkeypatch.setattr(adapter, 'REDIS_READY_TIMEOUT', 0)
    redis_ports[:] = [7301, 7302]
    silent = FakeProcess(pid=333)
    redis_launcher.processes[:] = [silent]

    process, port = adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

    assert (process, port) == (silent, 7301)
    assert len(redis_launcher.commands) == 1
    assert [client.port for client in fake_redis.clients] == [7301]
    assert fake_redis.closed == fake_redis.clients


def test_redis_start_gives_up_after_the_attempt_limit(fake_redis, redis_ports, redis_launcher):
    redis_ports[:] = [7001, 7002, 7003, 7004, 7005]
    redis_launcher.processes[:] = [FakeProcess(pid=300 + index, exited=True) for index in range(5)]

    with pytest.raises(AssertionError, match='Private Redis could not start'):
        adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

    assert len(redis_launcher.commands) == adapter.REDIS_ATTEMPTS == 5
    assert [redis_launcher.port(index) for index in range(5)] == ['7001', '7002', '7003', '7004', '7005']
    # Every attempt exited before anything answered, so no client was opened.
    assert fake_redis.clients == []


# ---------------------------------------------------------------------------
# Go server port race
# ---------------------------------------------------------------------------

class FakeGoProcess(FakeProcess):
    """A Go process that already exited, optionally after a bind failure."""

    def __init__(self, pid, log, message=None):
        super().__init__(pid)
        if message is not None:
            log.write(message + '\n')
            log.flush()
            self.returncode = 1


SETTINGS_KEYS = (
    'SECRET_KEY', 'JWT_SECRET_KEY', 'JWT_PUBLIC_KEY', 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
    'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION',
    'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET', 'SENJA_WEBHOOK_SECRET',
    'INSTANCE_SETUP_TOKEN',
)


@pytest.fixture
def go_server(monkeypatch, tmp_path):
    """native_server with a stubbed build, port picker, process and readiness."""
    from TESTS.adapters.go import payment_category_server as native
    settings = types.SimpleNamespace(
        DATABASES={'default': {'NAME': 'db', 'USER': 'u', 'PASSWORD': 'p', 'HOST': 'h', 'PORT': '5432'}},
        DATA_RESTORE_LOCK_PATH=str(tmp_path / 'lock'), SESSION_COOKIE_SECURE=False,
        **{name: 'value' for name in SETTINGS_KEYS})
    monkeypatch.setitem(sys.modules, 'django.conf', types.SimpleNamespace(settings=settings))
    monkeypatch.setattr(native, 'build_server', lambda *args, **kwargs: None)
    monkeypatch.setattr(native, 'Client', lambda base: types.SimpleNamespace(request=lambda method, path: (200, {})))
    ports = []
    monkeypatch.setattr(native, 'free_http_port', lambda: ports.pop(0))
    log = (tmp_path / 'services.log').open('w')
    yield types.SimpleNamespace(native=native, work=tmp_path, log=log, ports=ports, attempted=[])
    log.close()


def go_launcher(go_server, monkeypatch, exits):
    """Script the attempts: each entry is a log line to write before exiting.

    ``None`` leaves that process running and ready.
    """
    def popen(command, **kwargs):
        index = len(go_server.attempted)
        process = FakeGoProcess(pid=500 + index, log=go_server.log, message=exits[index])
        go_server.attempted.append(process)
        return process
    monkeypatch.setattr(go_server.native.subprocess, 'Popen', popen)


BIND_ERROR = 'listen tcp 127.0.0.1:8101: bind: address already in use'


def test_go_server_retries_a_port_that_is_already_in_use(go_server, monkeypatch):
    go_server.ports[:] = [8101, 8102]
    go_launcher(go_server, monkeypatch, [BIND_ERROR, None])

    with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379') as runtime:
        assert runtime['base_url'] == 'http://127.0.0.1:8102'
        assert runtime['proxy_upstream'] == 'host.docker.internal:8102'

    assert len(go_server.attempted) == 2
    assert go_server.attempted[0].returncode == 1
    assert go_server.attempted[1].returncode == -15


def test_go_server_does_not_retry_an_unrelated_early_exit(go_server, monkeypatch):
    go_server.ports[:] = [8201, 8202]
    # Written before the attempt: stale output from an earlier phase is not this
    # attempt's bind failure, so the offset keeps it from triggering a retry.
    go_server.log.write('listen tcp 127.0.0.1:8201: bind: address already in use\n')
    go_server.log.flush()
    go_launcher(go_server, monkeypatch, [''])

    with pytest.raises(RuntimeError, match='exited'):
        with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379'):
            pytest.fail('an exited server must not be yielded')
    assert len(go_server.attempted) == 1


def test_go_server_stops_after_three_port_attempts(go_server, monkeypatch):
    go_server.ports[:] = [8301, 8302, 8303]
    go_launcher(go_server, monkeypatch, [BIND_ERROR, BIND_ERROR, BIND_ERROR])

    with pytest.raises(RuntimeError, match='exited'):
        with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379'):
            pytest.fail('an exited server must not be yielded')
    assert len(go_server.attempted) == go_server.native.PORT_ATTEMPTS == 3


def test_go_server_stops_the_process_when_readiness_times_out(go_server, monkeypatch):
    # A readiness timeout is not a port race: the running process must be
    # terminated before the timeout propagates, never left behind.
    go_server.ports[:] = [8401]
    go_launcher(go_server, monkeypatch, [None])

    def timed_out(*args, **kwargs):
        raise AssertionError('Timed out waiting for operation: readiness')

    monkeypatch.setattr(go_server.native, 'poll', timed_out)

    with pytest.raises(AssertionError, match='Timed out waiting for operation'):
        with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379'):
            pytest.fail('an unready server must not be yielded')

    assert len(go_server.attempted) == 1
    assert go_server.attempted[0].poll() is not None
    assert go_server.attempted[0].terminated == 1
    assert go_server.attempted[0].waited == 1
