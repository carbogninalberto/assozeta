"""Unit tests for the shared-server path of the HTTP family driver.

No PostgreSQL, Redis, Django or Go server runs: the shared database lifetime,
processes and commands are replaced with small fakes.
"""
import contextlib
from pathlib import Path
import subprocess

import pytest

from TESTS.adapters.django import historical_read_routes as adapter
from TESTS.adapters.django import message_reads_runtime as reuse
from TESTS.adapters.django import shared_postgres as sp


KEY = '0123456789abcdef'
HOST = '/shared/socket'
NAME = 'assozeta_contract_run_1700000000_42_abcdef'


# ---------------------------------------------------------------------------
# Targets and settings
# ---------------------------------------------------------------------------

def test_database_target_defaults_to_the_private_directory(tmp_path):
    assert adapter.database_target(tmp_path) == (str(tmp_path), adapter.DB_NAME)
    assert adapter.database_target(tmp_path / 'missing') == (str(tmp_path / 'missing'), adapter.DB_NAME)


def test_database_target_reads_the_registered_value(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    adapter.SHARED_TARGETS[str(work.resolve())] = (HOST, NAME)
    try:
        assert adapter.database_target(work) == (HOST, NAME)
        assert adapter.database_target(str(work)) == (HOST, NAME)
    finally:
        adapter.SHARED_TARGETS.pop(str(work.resolve()), None)


def test_private_environment_uses_the_registered_target(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    adapter.SHARED_TARGETS[str(work.resolve())] = (HOST, NAME)
    try:
        environment, _ = reuse.private_environment(work, 6399, {'LC_ALL': 'C'})
    finally:
        adapter.SHARED_TARGETS.pop(str(work.resolve()), None)
    assert environment['TESTS_DATABASE'] == NAME
    assert environment['DBNAME'] == NAME
    assert environment['DBHOST'] == HOST
    assert environment['TESTS_WORKDIR'] == str(work)
    assert environment['REDIS_PORT'] == '6399'


def test_private_environment_keeps_the_private_target(tmp_path):
    environment, _ = reuse.private_environment(tmp_path, 6399, {'LC_ALL': 'C'})
    assert environment['TESTS_DATABASE'] == adapter.DB_NAME
    assert environment['DBNAME'] == adapter.DB_NAME
    assert environment['DBHOST'] == str(tmp_path)


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


class SharedFixture:
    """Record every command, process and database transition of one run."""

    def __init__(self, monkeypatch, tmp_path):
        self.events = []
        self.commands = []
        self.processes = []
        self.work = tmp_path / 'work'
        self.work.mkdir()
        self.output = tmp_path / 'output'
        self.output.mkdir()
        monkeypatch.setattr(adapter.tempfile, 'mkdtemp', lambda prefix, dir: str(self.work))
        monkeypatch.setattr(adapter.shutil, 'which', lambda name: '/fake/bin/' + name)
        monkeypatch.setattr(adapter.subprocess, 'run', self.run)
        monkeypatch.setattr(adapter.subprocess, 'Popen', self.popen)
        monkeypatch.setattr(sp, 'run_database', self.run_database)

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


def test_owned_database_uses_the_run_database_in_shared_mode(monkeypatch, tmp_path):
    fixture = SharedFixture(monkeypatch, tmp_path)
    report = {}
    with adapter.owned_database(fixture.output, report, shared=(tmp_path / 'server', KEY)) as (work, redis_port, _log):
        assert fixture.events == [('database', 'enter')]
        assert fixture.directory == tmp_path / 'server'
        assert fixture.key == KEY
        assert adapter.database_target(work) == (HOST, NAME)
        assert redis_port > 0
        assert report['service_cleanup']['redis'] == 'running'
    assert fixture.events == [('database', 'enter'), ('redis', 'stop'), ('database', 'drop')]
    assert adapter.database_target(fixture.work) == (str(fixture.work), adapter.DB_NAME)
    assert fixture.commands == [], 'Shared mode must not start a private server'
    assert len(fixture.processes) == 1 and fixture.processes[0].terminated
    assert not fixture.work.exists()
    assert report['owned_directory_removed'] is True
    assert report['service_cleanup']['postgres'] == 'not_started'
    assert report['service_cleanup']['redis'] == 'stopped'
    assert report['shared_postgres']['database_dropped'] is True
    assert 'recovery_directory' not in report


def test_owned_database_cleans_up_shared_mode_when_the_body_raises(monkeypatch, tmp_path):
    fixture = SharedFixture(monkeypatch, tmp_path)
    report = {}
    with pytest.raises(RuntimeError, match='boom'):
        with adapter.owned_database(fixture.output, report, shared=(tmp_path / 'server', KEY)):
            raise RuntimeError('boom')
    assert fixture.events == [('database', 'enter'), ('redis', 'stop'), ('database', 'drop')]
    assert adapter.database_target(fixture.work) == (str(fixture.work), adapter.DB_NAME)
    assert fixture.commands == []
    assert not fixture.work.exists()
    assert report['owned_directory_removed'] is True
    assert report['shared_postgres']['database_dropped'] is True
    assert report['service_cleanup']['redis'] == 'stopped'
