"""Unit tests for the shared PostgreSQL helper library.

No PostgreSQL server is started: subprocess calls and process liveness are replaced
with small fakes.
"""
import fcntl
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest import mock

from TESTS.adapters.django import shared_postgres as sp


KEY = '0123456789abcdef'
OTHER_KEY = 'fedcba9876543210'


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

    def setenv(self, **values):
        patcher = mock.patch.dict(os.environ, values)
        patcher.start()
        self.addCleanup(patcher.stop)

    def unsetenv(self, *names):
        saved = {name: os.environ[name] for name in names if name in os.environ}
        for name in names:
            os.environ.pop(name, None)

        def restore():
            for name in names:
                os.environ.pop(name, None)
            os.environ.update(saved)

        self.addCleanup(restore)


# ---------------------------------------------------------------------------
# Settings and environment
# ---------------------------------------------------------------------------

class SettingsAndEnvironmentTests(InventoryTestCase):
    def test_default_directories_use_xdg_cache(self):
        self.setenv(XDG_CACHE_HOME=str(self.tmp_path / 'xdg'), HOME=str(self.tmp_path / 'home'))
        self.assertEqual(sp.default_directory(os.environ), self.tmp_path / 'xdg/assozeta-test-postgres')
        self.assertEqual(sp.default_binary_cache(os.environ), self.tmp_path / 'xdg/assozeta-test-binaries')

    def test_default_directories_fall_back_to_home_cache(self):
        self.unsetenv('XDG_CACHE_HOME')
        self.setenv(HOME=str(self.tmp_path / 'home'))
        self.assertEqual(sp.default_directory(os.environ), self.tmp_path / 'home/.cache/assozeta-test-postgres')
        self.assertEqual(sp.default_binary_cache(os.environ), self.tmp_path / 'home/.cache/assozeta-test-binaries')

    def test_resolve_private_wins_over_everything(self):
        self.patch(sp, 'SHARED_BY_DEFAULT', True)
        environment = {
            'ASSOZETA_TEST_PRIVATE_POSTGRES': '1',
            'ASSOZETA_TEST_SHARED_POSTGRES': str(self.tmp_path / 'server'),
            'ASSOZETA_TEST_BINARY_CACHE': str(self.tmp_path / 'cache'),
            'HOME': str(self.tmp_path / 'home'),
        }
        self.assertEqual(sp.resolve(environment), sp.Settings(None, None))

    def test_resolve_uses_explicit_shared_directory(self):
        self.patch(sp, 'SHARED_BY_DEFAULT', True)
        environment = {
            'ASSOZETA_TEST_SHARED_POSTGRES': str(self.tmp_path / 'server'),
            'HOME': str(self.tmp_path / 'home'),
        }
        settings = sp.resolve(environment)
        self.assertEqual(settings.postgres, (self.tmp_path / 'server').resolve())
        self.assertEqual(settings.binaries, sp.default_binary_cache(environment))

    def test_resolve_is_private_when_shared_is_off_by_default(self):
        self.patch(sp, 'SHARED_BY_DEFAULT', False)
        environment = {
            'ASSOZETA_TEST_SHARED_POSTGRES': '',
            'HOME': str(self.tmp_path / 'home'),
        }
        self.assertEqual(sp.resolve(environment), sp.Settings(None, None))

    def test_shared_is_the_default_mode(self):
        self.assertIs(sp.SHARED_BY_DEFAULT, True)
        environment = {'HOME': str(self.tmp_path / 'home')}
        self.assertEqual(sp.resolve(environment), sp.Settings(
            sp.default_directory(environment), sp.default_binary_cache(environment)))

    def test_resolve_uses_default_directory_when_shared_is_on(self):
        self.patch(sp, 'SHARED_BY_DEFAULT', True)
        environment = {'HOME': str(self.tmp_path / 'home')}
        self.assertEqual(sp.resolve(environment), sp.Settings(
            sp.default_directory(environment), sp.default_binary_cache(environment)))

    def test_resolve_honours_binary_cache_override(self):
        self.patch(sp, 'SHARED_BY_DEFAULT', False)
        environment = {
            'ASSOZETA_TEST_SHARED_POSTGRES': str(self.tmp_path / 'server'),
            'ASSOZETA_TEST_BINARY_CACHE': str(self.tmp_path / 'cache'),
            'HOME': str(self.tmp_path / 'home'),
        }
        settings = sp.resolve(environment)
        self.assertEqual(settings.binaries, (self.tmp_path / 'cache').resolve())

    def test_activate_and_active_round_trip(self):
        original = sp.active()
        try:
            chosen = sp.Settings(Path('/tmp/server'), None)
            sp.activate(chosen)
            self.assertEqual(sp.active(), chosen)
        finally:
            sp.activate(original)

    def test_server_environment_is_minimal(self):
        self.setenv(PATH='/opt/bin', HOME='/home/tester')
        for name in ('TZ', 'PGHOST', 'PGPORT', 'PGDATABASE', 'PGOPTIONS', 'PGPASSWORD', 'PGUSER'):
            self.setenv(**{name: 'value'})
        self.assertEqual(sp.server_environment(), {
            'LANG': 'C',
            'LC_ALL': 'C',
            'PATH': '/opt/bin',
            'HOME': '/home/tester',
        })

    def test_server_environment_omits_absent_path_and_home(self):
        self.unsetenv('PATH', 'HOME')
        self.assertEqual(sp.server_environment(), {'LANG': 'C', 'LC_ALL': 'C'})


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

class NameTests(InventoryTestCase):
    def test_run_name_round_trip_and_shape(self):
        name = sp.run_name(1_700_000_000)
        self.assertTrue(re.fullmatch(r'assozeta_contract_run_\d+_\d+_[0-9a-f]{6}', name))
        self.assertTrue(re.fullmatch(r'[a-z0-9_]+', name))
        self.assertEqual(sp.parse_run_name(name), (1_700_000_000, os.getpid()))
        self.assertLessEqual(len(name.encode('ascii')), 63)

    def test_run_name_parsing_rejects_other_names(self):
        self.assertIsNone(sp.parse_run_name('assozeta_contract_run_1_2_ABCDEF'))
        self.assertIsNone(sp.parse_run_name(sp.template_name(KEY)))
        self.assertIsNone(sp.parse_run_name('assozeta_contract_run_broken'))
        self.assertIsNone(sp.parse_run_name(None))

    def test_build_name_round_trip_and_shape(self):
        name = sp.build_name(KEY)
        self.assertTrue(re.fullmatch(r'assozeta_contract_tplb_[0-9a-f]{16}_\d+_[0-9a-f]{6}', name))
        self.assertTrue(re.fullmatch(r'[a-z0-9_]+', name))
        self.assertEqual(sp.parse_build_name(name), os.getpid())
        self.assertLessEqual(len(name.encode('ascii')), 63)
        self.assertIsNone(sp.parse_build_name(sp.template_name(KEY)))
        self.assertIsNone(sp.parse_build_name('assozeta_contract_tplb_broken'))

    def test_template_name_and_key_validation(self):
        self.assertEqual(sp.template_name(KEY), 'assozeta_contract_tpl_' + KEY)
        self.assertEqual(sp.parse_template_name('assozeta_contract_tpl_' + KEY), KEY)
        self.assertIsNone(sp.parse_template_name('assozeta_contract_tpl_' + KEY.upper()))
        self.assertIsNone(sp.parse_template_name('assozeta_contract_tplb_' + KEY + '_1_abcdef'))
        with self.assertRaises(ValueError):
            sp.template_name('not-a-key')
        with self.assertRaises(ValueError):
            sp.build_name('short')

    def test_names_stay_below_the_identifier_limit(self):
        # Worst-case numeric fields: 10-digit timestamp, 7-digit pid, 6 hex characters.
        self.assertLessEqual(
            len(f'assozeta_contract_run_{2 ** 31 - 1}_{2 ** 22 - 1}_abcdef'.encode()), 63)
        self.assertLessEqual(
            len(f'assozeta_contract_tplb_{KEY}_{2 ** 22 - 1}_abcdef'.encode()), 63)

    def test_names_satisfy_the_test_settings_contract(self):
        # The test settings module rejects other names before it reads a setting.
        for name in (sp.run_name(1_700_000_000), sp.template_name(KEY), sp.build_name(KEY)):
            self.assertTrue(name.startswith('assozeta_contract_'))
            self.assertTrue(name.isidentifier())
            self.assertLessEqual(len(name.encode('ascii')), 63)


# ---------------------------------------------------------------------------
# Template key
# ---------------------------------------------------------------------------

def _fake_tree(root: Path) -> Path:
    (root / 'BE/app/migrations/__pycache__').mkdir(parents=True)
    (root / 'BE/app/models').mkdir(parents=True)
    (root / 'BE/core').mkdir(parents=True)
    (root / 'BE/other/migrations/__pycache__').mkdir(parents=True)
    (root / 'BE_GO/migrations').mkdir(parents=True)
    (root / 'TESTS/adapters/django').mkdir(parents=True)
    (root / 'BE/app/migrations/0001_initial.py').write_text('initial = 1\n')
    (root / 'BE/app/migrations/__pycache__/ignored.py').write_text('ignored\n')
    (root / 'BE/app/models.py').write_text('class A: pass\n')
    (root / 'BE/app/models/extra.py').write_text('class B: pass\n')
    (root / 'BE/app/views.py').write_text('def view(): pass\n')
    (root / 'BE/other/migrations/__pycache__/cached.py').write_text('cached\n')
    (root / 'BE/core/settings.py').write_text('DEBUG = False\n')
    (root / 'BE_GO/migrations/000001_jobs.up.sql').write_text('CREATE TABLE jobs ();\n')
    (root / 'TESTS/adapters/django/settings.py').write_text('DATABASES = {}\n')
    return root / 'BE/app/migrations/0001_initial.py'


class TemplateKeyTests(InventoryTestCase):
    def test_template_key_is_deterministic(self):
        _fake_tree(self.tmp_path)
        first = sp.template_key(self.tmp_path, 'django test postgres test')
        self.assertTrue(re.fullmatch(r'[0-9a-f]{16}', first))
        self.assertEqual(sp.template_key(self.tmp_path, 'django test postgres test'), first)

    def test_template_key_changes_when_a_migration_changes_or_is_renamed(self):
        migration = _fake_tree(self.tmp_path)
        original = sp.template_key(self.tmp_path)
        migration.write_text('initial = 2\n')
        changed = sp.template_key(self.tmp_path)
        self.assertNotEqual(changed, original)
        migration.write_text('initial = 1\n')
        self.assertEqual(sp.template_key(self.tmp_path), original)
        migration.rename(migration.with_name('0002_initial.py'))
        self.assertNotEqual(sp.template_key(self.tmp_path), original)

    def test_template_key_changes_when_extra_changes(self):
        _fake_tree(self.tmp_path)
        self.assertNotEqual(sp.template_key(self.tmp_path, 'one'), sp.template_key(self.tmp_path, 'two'))

    def test_template_key_ignores_pycache_and_unrelated_python(self):
        _fake_tree(self.tmp_path)
        original = sp.template_key(self.tmp_path)
        (self.tmp_path / 'BE/app/migrations/__pycache__/ignored.py').write_text('changed\n')
        (self.tmp_path / 'BE/app/views.py').write_text('def changed(): pass\n')
        self.assertEqual(sp.template_key(self.tmp_path), original)

    def test_template_key_tracks_models_and_go_migrations(self):
        _fake_tree(self.tmp_path)
        original = sp.template_key(self.tmp_path)
        (self.tmp_path / 'BE/app/models/extra.py').write_text('class B: changed = True\n')
        models_changed = sp.template_key(self.tmp_path)
        self.assertNotEqual(models_changed, original)
        (self.tmp_path / 'BE_GO/migrations/000001_jobs.up.sql').write_text('CREATE TABLE other ();\n')
        self.assertNotEqual(sp.template_key(self.tmp_path), models_changed)


# ---------------------------------------------------------------------------
# Server guards
# ---------------------------------------------------------------------------

class ServerGuardTests(InventoryTestCase):
    def test_ensure_server_refuses_a_long_socket_path(self):
        directory = self.tmp_path / ('x' * 60)
        self.patch(sp, 'postgres_binaries', lambda: Path('/should/not/be/used'))
        with self.assertRaisesRegex(AssertionError, 'too long'):
            sp.ensure_server(directory)

    def test_psql_uses_the_server_installation_by_default(self):
        commands = []
        self.patch(sp, 'postgres_binaries', lambda: Path('/postgres/bin'))

        class Result:
            stdout = '1\n'

        def fake_run(arguments, **kwargs):
            commands.append([str(argument) for argument in arguments])
            return Result()

        self.patch(sp.subprocess, 'run', fake_run)
        self.assertEqual(sp.psql(self.tmp_path, 'SELECT 1'), '1\n')
        self.assertEqual(commands[0][0], '/postgres/bin/psql')

    def test_socket_limit_matches_the_documented_value(self):
        self.assertEqual(sp.SOCKET_LIMIT, 100)
        self.assertLessEqual(len(str(Path('/tmp/az-postgres') / '.s.PGSQL.5432')), sp.SOCKET_LIMIT)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

NOW = 2_000_000
ALIVE_PID = 111
DEAD_PID = 222
DEAD_OLD = f'assozeta_contract_run_{NOW - 700}_{DEAD_PID}_aaaaaa'
DEAD_YOUNG = f'assozeta_contract_run_{NOW - 60}_{DEAD_PID}_bbbbbb'
ALIVE_OLD = f'assozeta_contract_run_{NOW - 7200}_{ALIVE_PID}_cccccc'
ALIVE_YOUNG = f'assozeta_contract_run_{NOW - 100}_{ALIVE_PID}_dddddd'
BUILD_DEAD = f'assozeta_contract_tplb_{KEY}_{DEAD_PID}_eeeeee'
BUILD_ALIVE = f'assozeta_contract_tplb_{KEY}_{ALIVE_PID}_ffffff'
TEMPLATE = f'assozeta_contract_tpl_{KEY}'
BROKEN = 'assozeta_contract_run_broken'
DATABASES = [DEAD_OLD, DEAD_YOUNG, ALIVE_OLD, ALIVE_YOUNG, BUILD_DEAD, BUILD_ALIVE, TEMPLATE, BROKEN]


class CleanupTests(InventoryTestCase):
    def fake_listing(self, names, calls):
        def fake_psql(directory, sql, *, database='postgres', binaries=None):
            calls.append(sql)
            if 'SELECT datname' in sql:
                return '\n'.join(names) + '\n'
            return ''
        self.patch(sp, 'psql', fake_psql)
        self.patch(sp, 'server_running', lambda directory: True)
        self.patch(sp, '_process_alive', lambda pid: pid == ALIVE_PID)

    def test_cleanup_drops_abandoned_and_expired_run_databases(self):
        calls = []
        self.fake_listing(DATABASES, calls)
        dropped = sp.cleanup(self.tmp_path, hours=1, now=NOW)
        self.assertEqual(dropped, [DEAD_OLD, ALIVE_OLD, BUILD_DEAD])
        drops = [sql for sql in calls if sql.startswith('DROP DATABASE')]
        self.assertEqual(drops, [
            f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
            for name in (DEAD_OLD, ALIVE_OLD, BUILD_DEAD)
        ])
        self.assertFalse(any(BROKEN in sql for sql in calls))
        self.assertFalse(any(DEAD_YOUNG in sql or BUILD_ALIVE in sql for sql in calls))

    def test_cleanup_dead_only_keeps_live_processes(self):
        calls = []
        self.fake_listing(DATABASES, calls)
        self.assertEqual(sp.cleanup(self.tmp_path, hours=1, dead_only=True, now=NOW), [DEAD_OLD, BUILD_DEAD])
        self.assertFalse(any(ALIVE_OLD in sql for sql in calls))

    def test_cleanup_drops_templates_only_when_asked(self):
        calls = []
        self.fake_listing(DATABASES, calls)
        dropped = sp.cleanup(self.tmp_path, hours=1, templates=True, now=NOW)
        self.assertIn(TEMPLATE, dropped)
        self.assertEqual(dropped, [DEAD_OLD, ALIVE_OLD, BUILD_DEAD, TEMPLATE])
        template_calls = [index for index, sql in enumerate(calls) if TEMPLATE in sql]
        self.assertIn('IS_TEMPLATE false', calls[template_calls[0]])
        self.assertTrue(calls[template_calls[-1]].startswith(f'DROP DATABASE IF EXISTS "{TEMPLATE}"'))

    def test_cleanup_does_nothing_when_server_is_not_running(self):
        self.patch(sp, 'server_running', lambda directory: False)

        def unexpected(*args, **kwargs):
            raise AssertionError('psql must not run')

        self.patch(sp, 'psql', unexpected)
        self.assertEqual(sp.cleanup(self.tmp_path), [])

    def test_cleanup_and_status_filter_on_the_contract_prefix(self):
        calls = []

        def fake_psql(directory, sql, **kwargs):
            calls.append(sql)
            return ''

        self.patch(sp, 'psql', fake_psql)
        self.patch(sp, 'server_running', lambda directory: True)
        self.assertEqual(sp.cleanup(self.tmp_path), [])
        sp.status(self.tmp_path)
        expected = r"SELECT datname FROM pg_database WHERE datname LIKE 'assozeta\_contract\_%'"
        self.assertEqual(calls, [expected, expected])

    def test_process_alive_uses_os_kill(self):
        seen = []
        with mock.patch.object(sp.os, 'kill', lambda pid, signal: seen.append((pid, signal))):
            self.assertIs(sp._process_alive(1234), True)
            self.assertEqual(seen, [(1234, 0)])

        def missing(pid, signal):
            raise ProcessLookupError

        with mock.patch.object(sp.os, 'kill', missing):
            self.assertIs(sp._process_alive(1234), False)

        def denied(pid, signal):
            raise PermissionError

        with mock.patch.object(sp.os, 'kill', denied):
            self.assertIs(sp._process_alive(1234), True)


# ---------------------------------------------------------------------------
# Run databases
# ---------------------------------------------------------------------------

class FakeServer:
    """Minimal psql replacement that tracks the databases it knows about."""

    def __init__(self, existing=()):
        self.existing = set(existing)
        self.calls = []
        self.template_failures = 0
        self.drop_error = None

    def __call__(self, directory, sql, *, database='postgres', binaries=None):
        self.calls.append(sql)
        if sql.startswith('SELECT 1'):
            name = re.search(r"datname = '([^']+)'", sql).group(1)
            return '1\n' if name in self.existing else ''
        if sql.startswith('CREATE DATABASE'):
            name = re.search(r'CREATE DATABASE "([^"]+)"', sql).group(1)
            if 'TEMPLATE' in sql and self.template_failures:
                self.template_failures -= 1
                raise subprocess.CalledProcessError(1, 'psql')
            self.existing.add(name)
            return ''
        if sql.startswith('DROP DATABASE'):
            if self.drop_error is not None:
                raise self.drop_error
            name = re.search(r'DROP DATABASE IF EXISTS "([^"]+)"', sql).group(1)
            self.existing.discard(name)
            return ''
        return ''


class RunDatabaseTests(InventoryTestCase):
    def fake_run_environment(self, fake_psql):
        self.patch(sp, 'ensure_server', lambda directory: Path('/postgres/bin'))
        self.patch(sp, 'cleanup', lambda *args, **kwargs: [])
        self.patch(sp, 'psql', fake_psql)

    def test_run_database_falls_back_to_plain_create(self):
        fake = FakeServer({sp.template_name(KEY)})
        fake.template_failures = 3
        sleeps = []
        self.fake_run_environment(fake)
        self.patch(sp.time, 'sleep', sleeps.append)
        report = {}
        with sp.run_database(self.tmp_path, KEY, report=report) as run:
            self.assertEqual(run.host, str(self.tmp_path))
            self.assertIn(run.name, fake.existing)
            self.assertIs(run.from_template, False)
            self.assertEqual(run.key, KEY)
            self.assertEqual(report['shared_postgres'], {
                'database_created': True,
                'from_template': False,
                'template_key': KEY,
                'database_dropped': False,
            })
        self.assertIs(report['shared_postgres']['database_dropped'], True)
        self.assertEqual(sleeps, [0.5, 1.0])
        self.assertEqual(fake.existing, {sp.template_name(KEY)})
        template_attempts = [sql for sql in fake.calls
                             if sql.startswith('CREATE DATABASE') and 'TEMPLATE' in sql]
        plain_attempts = [sql for sql in fake.calls
                          if sql.startswith('CREATE DATABASE') and 'TEMPLATE' not in sql]
        self.assertEqual(len(template_attempts), 3)
        self.assertEqual(plain_attempts, [f'CREATE DATABASE "{run.name}"'])

    def test_run_database_clones_the_template(self):
        fake = FakeServer({sp.template_name(KEY)})
        sleeps = []
        self.fake_run_environment(fake)
        self.patch(sp.time, 'sleep', sleeps.append)
        report = {}
        with sp.run_database(self.tmp_path, KEY, report=report) as run:
            self.assertIs(run.from_template, True)
            self.assertIs(report['shared_postgres']['from_template'], True)
        self.assertEqual(sleeps, [])
        self.assertFalse(any(sql.startswith('CREATE DATABASE') and 'TEMPLATE' not in sql
                             for sql in fake.calls))
        self.assertIs(report['shared_postgres']['database_dropped'], True)

    def test_run_database_creates_plain_database_without_template(self):
        fake = FakeServer()
        sleeps = []
        self.fake_run_environment(fake)
        self.patch(sp.time, 'sleep', sleeps.append)
        with sp.run_database(self.tmp_path, KEY) as run:
            self.assertIs(run.from_template, False)
        self.assertEqual(sleeps, [])
        self.assertEqual([sql for sql in fake.calls if sql.startswith('CREATE DATABASE')], [
            f'CREATE DATABASE "{run.name}"'])

    def test_run_database_drops_on_exception(self):
        fake = FakeServer()
        self.fake_run_environment(fake)
        report = {}
        with self.assertRaisesRegex(RuntimeError, 'boom'):
            with sp.run_database(self.tmp_path, KEY, report=report) as run:
                raise RuntimeError('boom')
        self.assertIs(report['shared_postgres']['database_dropped'], True)
        self.assertTrue(any(sql == f'DROP DATABASE IF EXISTS "{run.name}" WITH (FORCE)'
                            for sql in fake.calls))

    def test_run_database_reports_failed_drop(self):
        fake = FakeServer()
        fake.drop_error = subprocess.CalledProcessError(1, 'psql')
        self.fake_run_environment(fake)
        report = {}
        with self.assertRaises(subprocess.CalledProcessError):
            with sp.run_database(self.tmp_path, KEY, report=report):
                pass
        self.assertIs(report['shared_postgres']['database_dropped'], False)

    def test_run_database_does_not_mask_the_body_exception(self):
        fake = FakeServer()
        fake.drop_error = subprocess.CalledProcessError(1, 'psql')
        self.fake_run_environment(fake)
        report = {}
        with self.assertRaisesRegex(RuntimeError, 'body'):
            with sp.run_database(self.tmp_path, KEY, report=report):
                raise RuntimeError('body')
        self.assertIs(report['shared_postgres']['database_dropped'], False)


# ---------------------------------------------------------------------------
# Templates, status and stop
# ---------------------------------------------------------------------------

class TemplateStatusStopTests(InventoryTestCase):
    def test_publish_template_clones_and_marks_the_template(self):
        fake = FakeServer()
        self.patch(sp, 'psql', fake)
        self.assertIs(sp.publish_template(
            self.tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY), True)
        create = [sql for sql in fake.calls if sql.startswith('CREATE DATABASE')]
        self.assertEqual(len(create), 1)
        self.assertTrue(create[0].startswith('CREATE DATABASE "assozeta_contract_tplb_'))
        self.assertIn('TEMPLATE "assozeta_contract_run_1_2_abcdef"', create[0])
        renamed = [sql for sql in fake.calls if 'RENAME TO' in sql]
        self.assertEqual(len(renamed), 1)
        self.assertTrue(renamed[0].startswith('ALTER DATABASE "assozeta_contract_tplb_'))
        self.assertTrue(renamed[0].endswith(f'RENAME TO "{TEMPLATE}"'))
        marked = [sql for sql in fake.calls if 'IS_TEMPLATE true' in sql]
        self.assertEqual(marked, [f'ALTER DATABASE "{TEMPLATE}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false'])

    def test_publish_template_returns_false_when_template_exists(self):
        fake = FakeServer({TEMPLATE})
        self.patch(sp, 'psql', fake)
        self.assertIs(sp.publish_template(
            self.tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY), False)
        self.assertFalse(any(sql.startswith('CREATE DATABASE') for sql in fake.calls))

    def test_publish_template_drops_the_build_database_on_failure(self):
        calls = []

        def fake_psql(directory, sql, *, database='postgres', binaries=None):
            calls.append(sql)
            if sql.startswith('SELECT 1'):
                return ''
            if 'RENAME TO' in sql:
                raise subprocess.CalledProcessError(1, 'psql')
            return ''

        self.patch(sp, 'psql', fake_psql)
        self.assertIs(sp.publish_template(
            self.tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY), False)
        self.assertTrue(any(sql.startswith('DROP DATABASE IF EXISTS "assozeta_contract_tplb_')
                            and 'WITH (FORCE)' in sql for sql in calls))

    def test_publish_template_returns_false_for_an_invalid_key(self):
        self.patch(sp, 'psql', FakeServer())
        self.assertIs(sp.publish_template(self.tmp_path, 'assozeta_contract_run_1_2_abcdef', 'not-a-key'), False)

    def test_publish_template_returns_false_when_the_lock_is_held(self):
        fake = FakeServer()
        self.patch(sp, 'psql', fake)
        with (self.tmp_path / f'template-{KEY}.lock').open('a') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                self.assertIs(sp.publish_template(
                    self.tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY), False)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.assertFalse(any(sql.startswith('CREATE DATABASE') for sql in fake.calls))

    def test_status_reports_databases_and_templates(self):
        fake = FakeServer()
        self.patch(sp, 'server_running', lambda directory: True)
        self.patch(sp, 'psql', lambda directory, sql, **kwargs: (
            f'{ALIVE_YOUNG}\n{TEMPLATE}\n{BUILD_ALIVE}\n'))
        result = sp.status(self.tmp_path)
        self.assertEqual(result, {
            'directory': str(self.tmp_path),
            'running': True,
            'databases': sorted([ALIVE_YOUNG, BUILD_ALIVE]),
            'templates': [TEMPLATE],
        })

    def test_status_when_stopped_does_not_query(self):
        self.patch(sp, 'server_running', lambda directory: False)

        def unexpected(*args, **kwargs):
            raise AssertionError('psql must not run')

        self.patch(sp, 'psql', unexpected)
        self.assertEqual(sp.status(self.tmp_path), {
            'directory': str(self.tmp_path), 'running': False, 'databases': [], 'templates': []})

    def test_stop_is_a_noop_when_already_stopped(self):
        self.patch(sp, 'server_running', lambda directory: False)
        self.assertIs(sp.stop(self.tmp_path), True)

    def test_stop_uses_fast_mode_and_reports_the_result(self):
        answers = iter([True, False])
        self.patch(sp, 'server_running', lambda directory: next(answers))
        self.patch(sp, 'postgres_binaries', lambda: Path('/postgres/bin'))
        commands = []

        def fake_command(arguments, directory, *, check=True):
            commands.append([str(argument) for argument in arguments])

            class Result:
                returncode = 0

            return Result()

        self.patch(sp, '_command', fake_command)
        self.assertIs(sp.stop(self.tmp_path), True)
        self.assertEqual(commands, [[
            '/postgres/bin/pg_ctl', '-D', str(self.tmp_path / 'data'),
            '-m', 'fast', '-w', 'stop',
        ]])
