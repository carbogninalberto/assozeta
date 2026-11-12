"""Unit tests for the shared PostgreSQL helper library.

No PostgreSQL server is started: subprocess calls and process liveness are replaced
with small fakes.
"""
import fcntl
import os
from pathlib import Path
import re
import subprocess

import pytest

from TESTS.adapters.django import shared_postgres as sp


KEY = '0123456789abcdef'
OTHER_KEY = 'fedcba9876543210'


# ---------------------------------------------------------------------------
# Settings and environment
# ---------------------------------------------------------------------------

def test_default_directories_use_xdg_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg'))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    assert sp.default_directory(os.environ) == tmp_path / 'xdg/assozeta-test-postgres'
    assert sp.default_binary_cache(os.environ) == tmp_path / 'xdg/assozeta-test-binaries'


def test_default_directories_fall_back_to_home_cache(monkeypatch, tmp_path):
    monkeypatch.delenv('XDG_CACHE_HOME', raising=False)
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    assert sp.default_directory(os.environ) == tmp_path / 'home/.cache/assozeta-test-postgres'
    assert sp.default_binary_cache(os.environ) == tmp_path / 'home/.cache/assozeta-test-binaries'


def test_resolve_private_wins_over_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'SHARED_BY_DEFAULT', True)
    environment = {
        'ASSOZETA_TEST_PRIVATE_POSTGRES': '1',
        'ASSOZETA_TEST_SHARED_POSTGRES': str(tmp_path / 'server'),
        'ASSOZETA_TEST_BINARY_CACHE': str(tmp_path / 'cache'),
        'HOME': str(tmp_path / 'home'),
    }
    assert sp.resolve(environment) == sp.Settings(None, None)


def test_resolve_uses_explicit_shared_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'SHARED_BY_DEFAULT', True)
    environment = {
        'ASSOZETA_TEST_SHARED_POSTGRES': str(tmp_path / 'server'),
        'HOME': str(tmp_path / 'home'),
    }
    settings = sp.resolve(environment)
    assert settings.postgres == (tmp_path / 'server').resolve()
    assert settings.binaries == sp.default_binary_cache(environment)


def test_resolve_is_private_when_shared_is_off_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'SHARED_BY_DEFAULT', False)
    environment = {
        'ASSOZETA_TEST_SHARED_POSTGRES': '',
        'HOME': str(tmp_path / 'home'),
    }
    assert sp.resolve(environment) == sp.Settings(None, None)


def test_shared_is_the_default_mode(tmp_path):
    assert sp.SHARED_BY_DEFAULT is True
    environment = {'HOME': str(tmp_path / 'home')}
    assert sp.resolve(environment) == sp.Settings(
        sp.default_directory(environment), sp.default_binary_cache(environment))


def test_resolve_uses_default_directory_when_shared_is_on(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'SHARED_BY_DEFAULT', True)
    environment = {'HOME': str(tmp_path / 'home')}
    assert sp.resolve(environment) == sp.Settings(
        sp.default_directory(environment), sp.default_binary_cache(environment))


def test_resolve_honours_binary_cache_override(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'SHARED_BY_DEFAULT', False)
    environment = {
        'ASSOZETA_TEST_SHARED_POSTGRES': str(tmp_path / 'server'),
        'ASSOZETA_TEST_BINARY_CACHE': str(tmp_path / 'cache'),
        'HOME': str(tmp_path / 'home'),
    }
    settings = sp.resolve(environment)
    assert settings.binaries == (tmp_path / 'cache').resolve()


def test_activate_and_active_round_trip():
    original = sp.active()
    try:
        chosen = sp.Settings(Path('/tmp/server'), None)
        sp.activate(chosen)
        assert sp.active() == chosen
    finally:
        sp.activate(original)


def test_server_environment_is_minimal(monkeypatch):
    monkeypatch.setenv('PATH', '/opt/bin')
    monkeypatch.setenv('HOME', '/home/tester')
    for name in ('TZ', 'PGHOST', 'PGPORT', 'PGDATABASE', 'PGOPTIONS', 'PGPASSWORD', 'PGUSER'):
        monkeypatch.setenv(name, 'value')
    assert sp.server_environment() == {
        'LANG': 'C',
        'LC_ALL': 'C',
        'PATH': '/opt/bin',
        'HOME': '/home/tester',
    }


def test_server_environment_omits_absent_path_and_home(monkeypatch):
    monkeypatch.delenv('PATH', raising=False)
    monkeypatch.delenv('HOME', raising=False)
    assert sp.server_environment() == {'LANG': 'C', 'LC_ALL': 'C'}


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------

def test_run_name_round_trip_and_shape():
    name = sp.run_name(1_700_000_000)
    assert re.fullmatch(r'assozeta_contract_run_\d+_\d+_[0-9a-f]{6}', name)
    assert re.fullmatch(r'[a-z0-9_]+', name)
    assert sp.parse_run_name(name) == (1_700_000_000, os.getpid())
    assert len(name.encode('ascii')) <= 63


def test_run_name_parsing_rejects_other_names():
    assert sp.parse_run_name('assozeta_contract_run_1_2_ABCDEF') is None
    assert sp.parse_run_name(sp.template_name(KEY)) is None
    assert sp.parse_run_name('assozeta_contract_run_broken') is None
    assert sp.parse_run_name(None) is None


def test_build_name_round_trip_and_shape():
    name = sp.build_name(KEY)
    assert re.fullmatch(r'assozeta_contract_tplb_[0-9a-f]{16}_\d+_[0-9a-f]{6}', name)
    assert re.fullmatch(r'[a-z0-9_]+', name)
    assert sp.parse_build_name(name) == os.getpid()
    assert len(name.encode('ascii')) <= 63
    assert sp.parse_build_name(sp.template_name(KEY)) is None
    assert sp.parse_build_name('assozeta_contract_tplb_broken') is None


def test_template_name_and_key_validation():
    assert sp.template_name(KEY) == 'assozeta_contract_tpl_' + KEY
    assert sp.parse_template_name('assozeta_contract_tpl_' + KEY) == KEY
    assert sp.parse_template_name('assozeta_contract_tpl_' + KEY.upper()) is None
    assert sp.parse_template_name('assozeta_contract_tplb_' + KEY + '_1_abcdef') is None
    with pytest.raises(ValueError):
        sp.template_name('not-a-key')
    with pytest.raises(ValueError):
        sp.build_name('short')


def test_names_stay_below_the_identifier_limit():
    # Worst-case numeric fields: 10-digit timestamp, 7-digit pid, 6 hex characters.
    assert len(f'assozeta_contract_run_{2 ** 31 - 1}_{2 ** 22 - 1}_abcdef'.encode()) <= 63
    assert len(f'assozeta_contract_tplb_{KEY}_{2 ** 22 - 1}_abcdef'.encode()) <= 63


def test_names_satisfy_the_test_settings_contract():
    # The test settings module rejects other names before it reads a setting.
    for name in (sp.run_name(1_700_000_000), sp.template_name(KEY), sp.build_name(KEY)):
        assert name.startswith('assozeta_contract_')
        assert name.isidentifier()
        assert len(name.encode('ascii')) <= 63


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


def test_template_key_is_deterministic(tmp_path):
    _fake_tree(tmp_path)
    first = sp.template_key(tmp_path, 'django test postgres test')
    assert re.fullmatch(r'[0-9a-f]{16}', first)
    assert sp.template_key(tmp_path, 'django test postgres test') == first


def test_template_key_changes_when_a_migration_changes_or_is_renamed(tmp_path):
    migration = _fake_tree(tmp_path)
    original = sp.template_key(tmp_path)
    migration.write_text('initial = 2\n')
    changed = sp.template_key(tmp_path)
    assert changed != original
    migration.write_text('initial = 1\n')
    assert sp.template_key(tmp_path) == original
    migration.rename(migration.with_name('0002_initial.py'))
    assert sp.template_key(tmp_path) != original


def test_template_key_changes_when_extra_changes(tmp_path):
    _fake_tree(tmp_path)
    assert sp.template_key(tmp_path, 'one') != sp.template_key(tmp_path, 'two')


def test_template_key_ignores_pycache_and_unrelated_python(tmp_path):
    _fake_tree(tmp_path)
    original = sp.template_key(tmp_path)
    (tmp_path / 'BE/app/migrations/__pycache__/ignored.py').write_text('changed\n')
    (tmp_path / 'BE/app/views.py').write_text('def changed(): pass\n')
    assert sp.template_key(tmp_path) == original


def test_template_key_tracks_models_and_go_migrations(tmp_path):
    _fake_tree(tmp_path)
    original = sp.template_key(tmp_path)
    (tmp_path / 'BE/app/models/extra.py').write_text('class B: changed = True\n')
    models_changed = sp.template_key(tmp_path)
    assert models_changed != original
    (tmp_path / 'BE_GO/migrations/000001_jobs.up.sql').write_text('CREATE TABLE other ();\n')
    assert sp.template_key(tmp_path) != models_changed


# ---------------------------------------------------------------------------
# Server guards
# ---------------------------------------------------------------------------

def test_ensure_server_refuses_a_long_socket_path(monkeypatch, tmp_path):
    directory = tmp_path / ('x' * 60)
    monkeypatch.setattr(sp, 'postgres_binaries', lambda: Path('/should/not/be/used'))
    with pytest.raises(AssertionError, match='too long'):
        sp.ensure_server(directory)


def test_psql_uses_the_server_installation_by_default(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(sp, 'postgres_binaries', lambda: Path('/postgres/bin'))

    class Result:
        stdout = '1\n'

    def fake_run(arguments, **kwargs):
        commands.append([str(argument) for argument in arguments])
        return Result()

    monkeypatch.setattr(sp.subprocess, 'run', fake_run)
    assert sp.psql(tmp_path, 'SELECT 1') == '1\n'
    assert commands[0][0] == '/postgres/bin/psql'


def test_socket_limit_matches_the_documented_value():
    assert sp.SOCKET_LIMIT == 100
    assert len(str(Path('/tmp/az-postgres') / '.s.PGSQL.5432')) <= sp.SOCKET_LIMIT


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


def _fake_listing(monkeypatch, names, calls):
    def fake_psql(directory, sql, *, database='postgres', binaries=None):
        calls.append(sql)
        if 'SELECT datname' in sql:
            return '\n'.join(names) + '\n'
        return ''
    monkeypatch.setattr(sp, 'psql', fake_psql)
    monkeypatch.setattr(sp, 'server_running', lambda directory: True)
    monkeypatch.setattr(sp, '_process_alive', lambda pid: pid == ALIVE_PID)


def test_cleanup_drops_abandoned_and_expired_run_databases(monkeypatch, tmp_path):
    calls = []
    _fake_listing(monkeypatch, DATABASES, calls)
    dropped = sp.cleanup(tmp_path, hours=1, now=NOW)
    assert dropped == [DEAD_OLD, ALIVE_OLD, BUILD_DEAD]
    drops = [sql for sql in calls if sql.startswith('DROP DATABASE')]
    assert drops == [
        f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'
        for name in (DEAD_OLD, ALIVE_OLD, BUILD_DEAD)
    ]
    assert not any(BROKEN in sql for sql in calls)
    assert not any(DEAD_YOUNG in sql or BUILD_ALIVE in sql for sql in calls)


def test_cleanup_dead_only_keeps_live_processes(monkeypatch, tmp_path):
    calls = []
    _fake_listing(monkeypatch, DATABASES, calls)
    assert sp.cleanup(tmp_path, hours=1, dead_only=True, now=NOW) == [DEAD_OLD, BUILD_DEAD]
    assert not any(ALIVE_OLD in sql for sql in calls)


def test_cleanup_drops_templates_only_when_asked(monkeypatch, tmp_path):
    calls = []
    _fake_listing(monkeypatch, DATABASES, calls)
    dropped = sp.cleanup(tmp_path, hours=1, templates=True, now=NOW)
    assert TEMPLATE in dropped
    assert dropped == [DEAD_OLD, ALIVE_OLD, BUILD_DEAD, TEMPLATE]
    template_calls = [index for index, sql in enumerate(calls) if TEMPLATE in sql]
    assert 'IS_TEMPLATE false' in calls[template_calls[0]]
    assert calls[template_calls[-1]].startswith(f'DROP DATABASE IF EXISTS "{TEMPLATE}"')


def test_cleanup_does_nothing_when_server_is_not_running(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'server_running', lambda directory: False)

    def unexpected(*args, **kwargs):
        raise AssertionError('psql must not run')

    monkeypatch.setattr(sp, 'psql', unexpected)
    assert sp.cleanup(tmp_path) == []


def test_cleanup_and_status_filter_on_the_contract_prefix(monkeypatch, tmp_path):
    calls = []

    def fake_psql(directory, sql, **kwargs):
        calls.append(sql)
        return ''

    monkeypatch.setattr(sp, 'psql', fake_psql)
    monkeypatch.setattr(sp, 'server_running', lambda directory: True)
    assert sp.cleanup(tmp_path) == []
    sp.status(tmp_path)
    expected = r"SELECT datname FROM pg_database WHERE datname LIKE 'assozeta\_contract\_%'"
    assert calls == [expected, expected]


def test_process_alive_uses_os_kill(monkeypatch):
    seen = []
    monkeypatch.setattr(sp.os, 'kill', lambda pid, signal: seen.append((pid, signal)))
    assert sp._process_alive(1234) is True
    assert seen == [(1234, 0)]

    def missing(pid, signal):
        raise ProcessLookupError

    monkeypatch.setattr(sp.os, 'kill', missing)
    assert sp._process_alive(1234) is False

    def denied(pid, signal):
        raise PermissionError

    monkeypatch.setattr(sp.os, 'kill', denied)
    assert sp._process_alive(1234) is True


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


def _fake_run_environment(monkeypatch, fake_psql):
    monkeypatch.setattr(sp, 'ensure_server', lambda directory: Path('/postgres/bin'))
    monkeypatch.setattr(sp, 'cleanup', lambda *args, **kwargs: [])
    monkeypatch.setattr(sp, 'psql', fake_psql)


def test_run_database_falls_back_to_plain_create(monkeypatch, tmp_path):
    fake = FakeServer({sp.template_name(KEY)})
    fake.template_failures = 3
    sleeps = []
    _fake_run_environment(monkeypatch, fake)
    monkeypatch.setattr(sp.time, 'sleep', sleeps.append)
    report = {}
    with sp.run_database(tmp_path, KEY, report=report) as run:
        assert run.host == str(tmp_path)
        assert run.name in fake.existing
        assert run.from_template is False
        assert run.key == KEY
        assert report['shared_postgres'] == {
            'database_created': True,
            'from_template': False,
            'template_key': KEY,
            'database_dropped': False,
        }
    assert report['shared_postgres']['database_dropped'] is True
    assert sleeps == [0.5, 1.0]
    assert fake.existing == {sp.template_name(KEY)}
    template_attempts = [sql for sql in fake.calls
                         if sql.startswith('CREATE DATABASE') and 'TEMPLATE' in sql]
    plain_attempts = [sql for sql in fake.calls
                      if sql.startswith('CREATE DATABASE') and 'TEMPLATE' not in sql]
    assert len(template_attempts) == 3
    assert plain_attempts == [f'CREATE DATABASE "{run.name}"']


def test_run_database_clones_the_template(monkeypatch, tmp_path):
    fake = FakeServer({sp.template_name(KEY)})
    sleeps = []
    _fake_run_environment(monkeypatch, fake)
    monkeypatch.setattr(sp.time, 'sleep', sleeps.append)
    report = {}
    with sp.run_database(tmp_path, KEY, report=report) as run:
        assert run.from_template is True
        assert report['shared_postgres']['from_template'] is True
    assert sleeps == []
    assert not any(sql.startswith('CREATE DATABASE') and 'TEMPLATE' not in sql
                   for sql in fake.calls)
    assert report['shared_postgres']['database_dropped'] is True


def test_run_database_creates_plain_database_without_template(monkeypatch, tmp_path):
    fake = FakeServer()
    sleeps = []
    _fake_run_environment(monkeypatch, fake)
    monkeypatch.setattr(sp.time, 'sleep', sleeps.append)
    with sp.run_database(tmp_path, KEY) as run:
        assert run.from_template is False
    assert sleeps == []
    assert [sql for sql in fake.calls if sql.startswith('CREATE DATABASE')] == [
        f'CREATE DATABASE "{run.name}"']


def test_run_database_drops_on_exception(monkeypatch, tmp_path):
    fake = FakeServer()
    _fake_run_environment(monkeypatch, fake)
    report = {}
    with pytest.raises(RuntimeError, match='boom'):
        with sp.run_database(tmp_path, KEY, report=report) as run:
            raise RuntimeError('boom')
    assert report['shared_postgres']['database_dropped'] is True
    assert any(sql == f'DROP DATABASE IF EXISTS "{run.name}" WITH (FORCE)'
               for sql in fake.calls)


def test_run_database_reports_failed_drop(monkeypatch, tmp_path):
    fake = FakeServer()
    fake.drop_error = subprocess.CalledProcessError(1, 'psql')
    _fake_run_environment(monkeypatch, fake)
    report = {}
    with pytest.raises(subprocess.CalledProcessError):
        with sp.run_database(tmp_path, KEY, report=report):
            pass
    assert report['shared_postgres']['database_dropped'] is False


def test_run_database_does_not_mask_the_body_exception(monkeypatch, tmp_path):
    fake = FakeServer()
    fake.drop_error = subprocess.CalledProcessError(1, 'psql')
    _fake_run_environment(monkeypatch, fake)
    report = {}
    with pytest.raises(RuntimeError, match='body'):
        with sp.run_database(tmp_path, KEY, report=report):
            raise RuntimeError('body')
    assert report['shared_postgres']['database_dropped'] is False


# ---------------------------------------------------------------------------
# Templates, status and stop
# ---------------------------------------------------------------------------

def test_publish_template_clones_and_marks_the_template(monkeypatch, tmp_path):
    fake = FakeServer()
    monkeypatch.setattr(sp, 'psql', fake)
    assert sp.publish_template(tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY) is True
    create = [sql for sql in fake.calls if sql.startswith('CREATE DATABASE')]
    assert len(create) == 1
    assert create[0].startswith('CREATE DATABASE "assozeta_contract_tplb_')
    assert 'TEMPLATE "assozeta_contract_run_1_2_abcdef"' in create[0]
    renamed = [sql for sql in fake.calls if 'RENAME TO' in sql]
    assert len(renamed) == 1
    assert renamed[0].startswith('ALTER DATABASE "assozeta_contract_tplb_')
    assert renamed[0].endswith(f'RENAME TO "{TEMPLATE}"')
    marked = [sql for sql in fake.calls if 'IS_TEMPLATE true' in sql]
    assert marked == [f'ALTER DATABASE "{TEMPLATE}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false']


def test_publish_template_returns_false_when_template_exists(monkeypatch, tmp_path):
    fake = FakeServer({TEMPLATE})
    monkeypatch.setattr(sp, 'psql', fake)
    assert sp.publish_template(tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY) is False
    assert not any(sql.startswith('CREATE DATABASE') for sql in fake.calls)


def test_publish_template_drops_the_build_database_on_failure(monkeypatch, tmp_path):
    calls = []

    def fake_psql(directory, sql, *, database='postgres', binaries=None):
        calls.append(sql)
        if sql.startswith('SELECT 1'):
            return ''
        if 'RENAME TO' in sql:
            raise subprocess.CalledProcessError(1, 'psql')
        return ''

    monkeypatch.setattr(sp, 'psql', fake_psql)
    assert sp.publish_template(tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY) is False
    assert any(sql.startswith('DROP DATABASE IF EXISTS "assozeta_contract_tplb_') and 'WITH (FORCE)' in sql
               for sql in calls)


def test_publish_template_returns_false_for_an_invalid_key(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'psql', FakeServer())
    assert sp.publish_template(tmp_path, 'assozeta_contract_run_1_2_abcdef', 'not-a-key') is False


def test_publish_template_returns_false_when_the_lock_is_held(monkeypatch, tmp_path):
    fake = FakeServer()
    monkeypatch.setattr(sp, 'psql', fake)
    with (tmp_path / f'template-{KEY}.lock').open('a') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert sp.publish_template(tmp_path, 'assozeta_contract_run_1_2_abcdef', KEY) is False
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    assert not any(sql.startswith('CREATE DATABASE') for sql in fake.calls)


def test_status_reports_databases_and_templates(monkeypatch, tmp_path):
    fake = FakeServer()
    monkeypatch.setattr(sp, 'server_running', lambda directory: True)
    monkeypatch.setattr(sp, 'psql', lambda directory, sql, **kwargs: (
        f'{ALIVE_YOUNG}\n{TEMPLATE}\n{BUILD_ALIVE}\n'))
    result = sp.status(tmp_path)
    assert result == {
        'directory': str(tmp_path),
        'running': True,
        'databases': sorted([ALIVE_YOUNG, BUILD_ALIVE]),
        'templates': [TEMPLATE],
    }


def test_status_when_stopped_does_not_query(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'server_running', lambda directory: False)

    def unexpected(*args, **kwargs):
        raise AssertionError('psql must not run')

    monkeypatch.setattr(sp, 'psql', unexpected)
    assert sp.status(tmp_path) == {
        'directory': str(tmp_path), 'running': False, 'databases': [], 'templates': []}


def test_stop_is_a_noop_when_already_stopped(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, 'server_running', lambda directory: False)
    assert sp.stop(tmp_path) is True


def test_stop_uses_fast_mode_and_reports_the_result(monkeypatch, tmp_path):
    answers = iter([True, False])
    monkeypatch.setattr(sp, 'server_running', lambda directory: next(answers))
    monkeypatch.setattr(sp, 'postgres_binaries', lambda: Path('/postgres/bin'))
    commands = []

    def fake_command(arguments, directory, *, check=True):
        commands.append([str(argument) for argument in arguments])

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(sp, '_command', fake_command)
    assert sp.stop(tmp_path) is True
    assert commands == [[
        '/postgres/bin/pg_ctl', '-D', str(tmp_path / 'data'),
        '-m', 'fast', '-w', 'stop',
    ]]
