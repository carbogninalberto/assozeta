"""Unit tests for per-run port and Redis ownership in parallel parity runs.

Everything runs with mocks: no server, subprocess or socket is started. The
diagnostics probe address, the private Redis start and the Go server port race
are the three places where concurrent runs used to share an address.
"""
import ast
import io
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / 'BE') not in sys.path:
    # BE/ is not on the path for the inventory suite; this module import is inert.
    sys.path.insert(0, str(ROOT / 'BE'))

from instance import diagnostic_probe
from TESTS.adapters.django import historical_read_routes as adapter


def probe_modules(settings):
    """Minimal stand-ins for the modules ``diagnostic_probe.probe`` imports."""

    def module(name, **attributes):
        value = types.ModuleType(name)
        value.__dict__.update(attributes)
        return value

    return {
        'django': module('django', __path__=[]),
        'django.conf': module('django.conf', settings=settings),
        'django.core': module('django.core', __path__=[]),
        'django.core.cache': module('django.core.cache', cache=object()),
        'django.db': module('django.db', connection=object()),
    }


class InventoryTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # The code under test resolves the work directory, and macOS /var is a
        # symlink to /private/var, so compare against the resolved path.
        self.tmp_path = Path(directory.name).resolve()

    def patch(self, target, name, value, **kwargs):
        patcher = mock.patch.object(target, name, value, **kwargs)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher


# ---------------------------------------------------------------------------
# Diagnostics probe address
# ---------------------------------------------------------------------------

class ProbeAddressTests(InventoryTestCase):
    def probe_settings(self):
        """The settings the probes read, cached on Django's lazy object.

        Writing into ``__dict__`` supplies the values the probe reads without
        configuring Django globally (which would leak into the rest of the suite).
        A standard-library-only interpreter has no Django, so the probe's imports
        are served by stand-in modules carrying the same three settings.
        """
        try:
            from django.conf import settings
        except ImportError:
            settings = types.SimpleNamespace()
            patcher = mock.patch.dict(sys.modules, probe_modules(settings))
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.dict(settings.__dict__, {
            'APP_URL': 'https://app.example.test',
            'RUNNING_VERSION': 'v-test',
            'DIAGNOSTICS_API_URL': 'http://127.0.0.1:41234',
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        return settings

    def test_api_probe_address_follows_the_setting(self):
        settings = self.probe_settings()
        seen = []
        self.patch(diagnostic_probe, 'health_response',
                   lambda url, version=None: seen.append((url, version)) or True)
        for configured in ('http://127.0.0.1:41234', 'http://127.0.0.1:8000'):
            with self.subTest(configured=configured):
                seen.clear()
                with mock.patch.dict(settings.__dict__, {'DIAGNOSTICS_API_URL': configured}):
                    outcome = diagnostic_probe.probe('api', {})

                self.assertEqual(outcome['status'], 'passed')
                self.assertEqual(seen, [(configured + '/healthz', 'v-test')])

    def test_settings_read_the_probe_address_from_the_environment(self):
        """The setting is the environment variable, defaulted to the local API."""
        source = (ROOT / 'BE' / 'core' / 'settings.py').read_text(encoding='utf-8')
        assignment = next(
            node for node in ast.parse(source).body
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == 'DIAGNOSTICS_API_URL'
                for target in node.targets))

        text = ast.get_source_segment(source, assignment)

        self.assertIn("'ASSOZETA_DIAGNOSTICS_API_URL'", text)
        self.assertIn('http://127.0.0.1:8000', text)

    def test_public_url_probe_still_uses_app_url(self):
        settings = self.probe_settings()
        seen = []
        self.patch(diagnostic_probe, 'health_response',
                   lambda url, version=None: seen.append((url, version)) or True)
        # The local API address is never the public URL, however it is configured.
        with mock.patch.dict(settings.__dict__, {'DIAGNOSTICS_API_URL': 'http://127.0.0.1:59999'}):
            outcome = diagnostic_probe.probe('public_url', {})

        self.assertEqual(outcome['status'], 'passed')
        self.assertEqual(seen, [('https://app.example.test/api/healthz', 'v-test')])


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


class PrivateRedisTests(InventoryTestCase):
    def fake_redis(self):
        """Replace the redis client: only the given ports answer, with a process id.

        The ownership probe imports ``redis`` and uses only the client factory
        and its error type, so a stand-in module serves the tests on an
        interpreter without the Redis package.
        """
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

        module = types.ModuleType('redis')
        module.Redis = FakeClient
        module.RedisError = FakeRedisError
        patcher = mock.patch.dict(sys.modules, {'redis': module})
        patcher.start()
        self.addCleanup(patcher.stop)
        return types.SimpleNamespace(owners=owners, clients=clients, closed=closed)

    def redis_ports(self):
        ports = []
        self.patch(adapter, 'available_loopback_port', lambda: ports.pop(0))
        return ports

    def redis_launcher(self):
        launcher = Launcher()
        self.patch(adapter.subprocess, 'Popen', launcher)
        return launcher

    def test_redis_start_retries_when_the_process_exits(self):
        fake_redis = self.fake_redis()
        redis_ports = self.redis_ports()
        redis_launcher = self.redis_launcher()
        redis_ports[:] = [5001, 5002]
        exited, healthy = FakeProcess(pid=101, exited=True), FakeProcess(pid=202)
        redis_launcher.processes[:] = [exited, healthy]
        fake_redis.owners[5002] = 202

        process, port = adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

        self.assertEqual((process, port), (healthy, 5002))
        self.assertEqual((redis_launcher.port(0), redis_launcher.port(1)), ('5001', '5002'))
        # The failed attempt had already exited, so it is only reaped, never signalled.
        self.assertIsNotNone(exited.poll())
        self.assertEqual(exited.terminated, 0)
        self.assertEqual([client.port for client in fake_redis.clients], [5002])
        self.assertEqual(fake_redis.closed, fake_redis.clients)

    def test_redis_start_retries_when_a_foreign_process_answers(self):
        fake_redis = self.fake_redis()
        redis_ports = self.redis_ports()
        redis_launcher = self.redis_launcher()
        redis_ports[:] = [6001, 6002]
        foreign_port_holder, healthy = FakeProcess(pid=111), FakeProcess(pid=222)
        redis_launcher.processes[:] = [foreign_port_holder, healthy]
        fake_redis.owners.update({6001: 999, 6002: 222})

        process, port = adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

        self.assertEqual((process, port), (healthy, 6002))
        self.assertEqual(foreign_port_holder.terminated, 1)
        self.assertEqual(foreign_port_holder.waited, 1)
        self.assertIsNotNone(foreign_port_holder.poll())
        self.assertEqual([client.port for client in fake_redis.clients], [6001, 6002])
        self.assertTrue(all(client.password == 'synthetic-identity-only' for client in fake_redis.clients))
        self.assertTrue(all(client.timeouts == (2, 2) for client in fake_redis.clients))
        # Every client, including the one that found the foreign server, is closed.
        self.assertEqual(fake_redis.closed, fake_redis.clients)

    def test_redis_start_keeps_a_live_process_when_nothing_answers(self):
        # An unreachable port is not another run's Redis: the run's own readiness
        # check reports a server that never became available.
        self.patch(adapter, 'REDIS_READY_TIMEOUT', 0)
        fake_redis = self.fake_redis()
        redis_ports = self.redis_ports()
        redis_launcher = self.redis_launcher()
        redis_ports[:] = [7301, 7302]
        silent = FakeProcess(pid=333)
        redis_launcher.processes[:] = [silent]

        process, port = adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

        self.assertEqual((process, port), (silent, 7301))
        self.assertEqual(len(redis_launcher.commands), 1)
        self.assertEqual([client.port for client in fake_redis.clients], [7301])
        self.assertEqual(fake_redis.closed, fake_redis.clients)

    def test_redis_start_gives_up_after_the_attempt_limit(self):
        fake_redis = self.fake_redis()
        redis_ports = self.redis_ports()
        redis_launcher = self.redis_launcher()
        redis_ports[:] = [7001, 7002, 7003, 7004, 7005]
        redis_launcher.processes[:] = [FakeProcess(pid=300 + index, exited=True) for index in range(5)]

        with self.assertRaisesRegex(AssertionError, 'Private Redis could not start'):
            adapter.start_private_redis('redis-server', Path('/work'), io.StringIO())

        self.assertEqual(len(redis_launcher.commands), adapter.REDIS_ATTEMPTS)
        self.assertEqual(adapter.REDIS_ATTEMPTS, 5)
        self.assertEqual([redis_launcher.port(index) for index in range(5)],
                         ['7001', '7002', '7003', '7004', '7005'])
        # Every attempt exited before anything answered, so no client was opened.
        self.assertEqual(fake_redis.clients, [])


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

BIND_ERROR = 'listen tcp 127.0.0.1:8101: bind: address already in use'


class GoServerTests(InventoryTestCase):
    def go_server(self):
        """native_server with a stubbed build, port picker, process and readiness."""
        from TESTS.adapters.go import payment_category_server as native
        settings = types.SimpleNamespace(
            DATABASES={'default': {'NAME': 'db', 'USER': 'u', 'PASSWORD': 'p', 'HOST': 'h', 'PORT': '5432'}},
            DATA_RESTORE_LOCK_PATH=str(self.tmp_path / 'lock'), SESSION_COOKIE_SECURE=False,
            **{name: 'value' for name in SETTINGS_KEYS})
        patcher = mock.patch.dict(sys.modules, {'django.conf': types.SimpleNamespace(settings=settings)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.patch(native, 'build_server', lambda *args, **kwargs: None)
        self.patch(native, 'Client', lambda base: types.SimpleNamespace(request=lambda method, path: (200, {})))
        ports = []
        self.patch(native, 'free_http_port', lambda: ports.pop(0))
        log = (self.tmp_path / 'services.log').open('w')
        self.addCleanup(log.close)
        return types.SimpleNamespace(native=native, work=self.tmp_path, log=log, ports=ports, attempted=[])

    def go_launcher(self, go_server, exits):
        """Script the attempts: each entry is a log line to write before exiting.

        ``None`` leaves that process running and ready.
        """
        def popen(command, **kwargs):
            index = len(go_server.attempted)
            process = FakeGoProcess(pid=500 + index, log=go_server.log, message=exits[index])
            go_server.attempted.append(process)
            return process
        self.patch(go_server.native.subprocess, 'Popen', popen)

    def test_go_server_retries_a_port_that_is_already_in_use(self):
        go_server = self.go_server()
        go_server.ports[:] = [8101, 8102]
        self.go_launcher(go_server, [BIND_ERROR, None])

        with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379') as runtime:
            self.assertEqual(runtime['base_url'], 'http://127.0.0.1:8102')
            self.assertEqual(runtime['proxy_upstream'], 'host.docker.internal:8102')

        self.assertEqual(len(go_server.attempted), 2)
        self.assertEqual(go_server.attempted[0].returncode, 1)
        self.assertEqual(go_server.attempted[1].returncode, -15)

    def test_go_server_does_not_retry_an_unrelated_early_exit(self):
        go_server = self.go_server()
        go_server.ports[:] = [8201, 8202]
        # Written before the attempt: stale output from an earlier phase is not this
        # attempt's bind failure, so the offset keeps it from triggering a retry.
        go_server.log.write('listen tcp 127.0.0.1:8201: bind: address already in use\n')
        go_server.log.flush()
        self.go_launcher(go_server, [''])

        with self.assertRaisesRegex(RuntimeError, 'exited'):
            with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379'):
                self.fail('an exited server must not be yielded')
        self.assertEqual(len(go_server.attempted), 1)

    def test_go_server_stops_after_three_port_attempts(self):
        go_server = self.go_server()
        go_server.ports[:] = [8301, 8302, 8303]
        self.go_launcher(go_server, [BIND_ERROR, BIND_ERROR, BIND_ERROR])

        with self.assertRaisesRegex(RuntimeError, 'exited'):
            with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379'):
                self.fail('an exited server must not be yielded')
        self.assertEqual(len(go_server.attempted), go_server.native.PORT_ATTEMPTS)
        self.assertEqual(go_server.native.PORT_ATTEMPTS, 3)

    def test_go_server_stops_the_process_when_readiness_times_out(self):
        # A readiness timeout is not a port race: the running process must be
        # terminated before the timeout propagates, never left behind.
        go_server = self.go_server()
        go_server.ports[:] = [8401]
        self.go_launcher(go_server, [None])

        def timed_out(*args, **kwargs):
            raise AssertionError('Timed out waiting for operation: readiness')

        self.patch(go_server.native, 'poll', timed_out)

        with self.assertRaisesRegex(AssertionError, 'Timed out waiting for operation'):
            with go_server.native.native_server(go_server.work, go_server.log, {}, '127.0.0.1:16379'):
                self.fail('an unready server must not be yielded')

        self.assertEqual(len(go_server.attempted), 1)
        self.assertIsNotNone(go_server.attempted[0].poll())
        self.assertEqual(go_server.attempted[0].terminated, 1)
        self.assertEqual(go_server.attempted[0].waited, 1)
