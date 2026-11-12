"""Unit tests for the shared-server path of the HTTP family driver.

No PostgreSQL, Redis, Django or Go server runs: the shared database lifetime,
processes and commands are replaced with small fakes.
"""
import contextlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from TESTS.adapters.django import historical_read_routes as adapter
from TESTS.adapters.django import message_reads_runtime as reuse
from TESTS.adapters.django import shared_postgres as sp


KEY = '0123456789abcdef'
HOST = '/shared/socket'
NAME = 'assozeta_contract_run_1700000000_42_abcdef'


class InventoryTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        # The code under test resolves the work directory, and macOS /var is a
        # symlink to /private/var, so compare against the resolved path.
        self.tmp_path = Path(directory.name).resolve()

    def patch(self, target, name, value):
        patcher = mock.patch.object(target, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)
        return patcher


# ---------------------------------------------------------------------------
# Targets and settings
# ---------------------------------------------------------------------------

def tracked_environment_module():
    """Stand-in for ``environ`` that reads the tracked example's KEY=VALUE lines."""
    module = types.ModuleType('environ')

    class Env:
        @staticmethod
        def read_env(path):
            for line in Path(path).read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                name, separator, value = line.partition('=')
                if separator:
                    os.environ.setdefault(name, value)

    module.Env = Env
    return module


class TargetsAndSettingsTests(InventoryTestCase):
    def setUp(self):
        super().setUp()
        # private_environment() reads the tracked example with django-environ and
        # generates a signing key pair with cryptography. The standard-library-only
        # interpreter has neither, so the tracked KEY=VALUE lines are read by a
        # stand-in and the generated key material (never asserted here) is fixed.
        try:
            import environ  # noqa: F401
        except ImportError:
            patcher = mock.patch.dict(sys.modules, {'environ': tracked_environment_module()})
            patcher.start()
            self.addCleanup(patcher.stop)
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.patch(reuse, 'signing_key_pair',
                       lambda: ('synthetic-private-key', 'synthetic-public-key'))

    def test_database_target_defaults_to_the_private_directory(self):
        self.assertEqual(adapter.database_target(self.tmp_path), (str(self.tmp_path), adapter.DB_NAME))
        self.assertEqual(adapter.database_target(self.tmp_path / 'missing'),
                         (str(self.tmp_path / 'missing'), adapter.DB_NAME))

    def test_database_target_reads_the_registered_value(self):
        work = self.tmp_path / 'work'
        work.mkdir()
        adapter.SHARED_TARGETS[str(work.resolve())] = (HOST, NAME)
        try:
            self.assertEqual(adapter.database_target(work), (HOST, NAME))
            self.assertEqual(adapter.database_target(str(work)), (HOST, NAME))
        finally:
            adapter.SHARED_TARGETS.pop(str(work.resolve()), None)

    def test_private_environment_uses_the_registered_target(self):
        work = self.tmp_path / 'work'
        work.mkdir()
        adapter.SHARED_TARGETS[str(work.resolve())] = (HOST, NAME)
        try:
            environment, _ = reuse.private_environment(work, 6399, {'LC_ALL': 'C'})
        finally:
            adapter.SHARED_TARGETS.pop(str(work.resolve()), None)
        self.assertEqual(environment['TESTS_DATABASE'], NAME)
        self.assertEqual(environment['DBNAME'], NAME)
        self.assertEqual(environment['DBHOST'], HOST)
        self.assertEqual(environment['TESTS_WORKDIR'], str(work))
        self.assertEqual(environment['REDIS_PORT'], '6399')

    def test_private_environment_keeps_the_private_target(self):
        environment, _ = reuse.private_environment(self.tmp_path, 6399, {'LC_ALL': 'C'})
        self.assertEqual(environment['TESTS_DATABASE'], adapter.DB_NAME)
        self.assertEqual(environment['DBNAME'], adapter.DB_NAME)
        self.assertEqual(environment['DBHOST'], str(self.tmp_path))


# ---------------------------------------------------------------------------
# Shared database lifetime
# ---------------------------------------------------------------------------

class FakeProcess:
    """Minimal Redis process compatible with ``stop_owned_process``."""

    def __init__(self, arguments, events):
        self.args = arguments
        self.pid = 4242
        self.events = events
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.events.append(('redis', 'stop'))
        self.terminated = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.terminated = True


def redis_client_module():
    """Client stand-in reporting the fake process as the port owner."""
    module = types.ModuleType('redis')

    class RedisError(Exception):
        pass

    class Redis:
        def __init__(self, **kwargs):
            self.port = kwargs['port']

        def info(self, section):
            return {'process_id': 4242}

        def close(self):
            pass

    module.Redis = Redis
    module.RedisError = RedisError
    return module


class SharedFixture:
    """Record every command, process and database transition of one run."""

    def __init__(self, case, tmp_path):
        self.events = []
        self.commands = []
        self.processes = []
        self.work = tmp_path / 'work'
        self.work.mkdir()
        self.output = tmp_path / 'output'
        self.output.mkdir()
        case.patch(adapter.tempfile, 'mkdtemp', lambda prefix, dir: str(self.work))
        case.patch(adapter.shutil, 'which', lambda name: '/fake/bin/' + name)
        case.patch(adapter.subprocess, 'run', self.run)
        case.patch(adapter.subprocess, 'Popen', self.popen)
        case.patch(sp, 'run_database', self.run_database)

    def run(self, arguments, **kwargs):
        self.commands.append([str(argument) for argument in arguments])
        return subprocess.CompletedProcess(arguments, 0)

    def popen(self, arguments, **kwargs):
        process = FakeProcess(arguments, self.events)
        self.processes.append(process)
        return process

    @contextlib.contextmanager
    def run_database(self, directory, key, *, report=None):
        self.events.append(('database', 'enter'))
        self.directory = Path(directory)
        self.key = key
        if isinstance(report, dict):
            report['shared_postgres'] = {
                'database_created': True,
                'from_template': False,
                'template_key': key,
                'database_dropped': False,
            }
        try:
            yield sp.RunDatabase(host=HOST, name=NAME, from_template=False, key=key)
        finally:
            self.events.append(('database', 'drop'))
            if isinstance(report, dict):
                report['shared_postgres']['database_dropped'] = True


class SharedDatabaseLifetimeTests(InventoryTestCase):
    def setUp(self):
        super().setUp()
        # The run owns one fake Redis process (pid 4242 above). start_private_redis
        # proves the port owner through redis.Redis, so the client stand-in
        # reports that process and needs no Redis installation.
        patcher = mock.patch.dict(sys.modules, {'redis': redis_client_module()})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_owned_database_uses_the_run_database_in_shared_mode(self):
        fixture = SharedFixture(self, self.tmp_path)
        report = {}
        with adapter.owned_database(fixture.output, report,
                                    shared=(self.tmp_path / 'server', KEY)) as (work, redis_port, _log):
            self.assertEqual(fixture.events, [('database', 'enter')])
            self.assertEqual(fixture.directory, self.tmp_path / 'server')
            self.assertEqual(fixture.key, KEY)
            self.assertEqual(adapter.database_target(work), (HOST, NAME))
            self.assertGreater(redis_port, 0)
            self.assertEqual(report['service_cleanup']['redis'], 'running')
        self.assertEqual(fixture.events, [('database', 'enter'), ('redis', 'stop'), ('database', 'drop')])
        self.assertEqual(adapter.database_target(fixture.work), (str(fixture.work), adapter.DB_NAME))
        self.assertEqual(fixture.commands, [], 'Shared mode must not start a private server')
        self.assertEqual(len(fixture.processes), 1)
        self.assertTrue(fixture.processes[0].terminated)
        self.assertFalse(fixture.work.exists())
        self.assertIs(report['owned_directory_removed'], True)
        self.assertEqual(report['service_cleanup']['postgres'], 'not_started')
        self.assertEqual(report['service_cleanup']['redis'], 'stopped')
        self.assertIs(report['shared_postgres']['database_dropped'], True)
        self.assertNotIn('recovery_directory', report)

    def test_owned_database_cleans_up_shared_mode_when_the_body_raises(self):
        fixture = SharedFixture(self, self.tmp_path)
        report = {}
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            with adapter.owned_database(fixture.output, report, shared=(self.tmp_path / 'server', KEY)):
                raise RuntimeError('boom')
        self.assertEqual(fixture.events, [('database', 'enter'), ('redis', 'stop'), ('database', 'drop')])
        self.assertEqual(adapter.database_target(fixture.work), (str(fixture.work), adapter.DB_NAME))
        self.assertEqual(fixture.commands, [])
        self.assertFalse(fixture.work.exists())
        self.assertIs(report['owned_directory_removed'], True)
        self.assertIs(report['shared_postgres']['database_dropped'], True)
        self.assertEqual(report['service_cleanup']['redis'], 'stopped')
