"""Private PostgreSQL cluster for the Phase 1 handoff updater tests.

A throwaway cluster (Unix socket only, trust auth, no durability) with the
operational migrations in BE_GO/migrations applied as plain SQL to a template
database; each test gets a fresh copy. It never touches a development or
production database. Missing server binaries fail the suite: this integration
is never skipped.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[2]
# Verification only: a disposable copy (mutation controls); defaults to this checkout.
MIGRATIONS = Path(os.environ.get('ASSOZETA_HANDOFF_MIGRATIONS_DIR') or ROOT / 'BE_GO/migrations')
USER = 'contracts'
TEMPLATE = 'handoff_template'


def binaries():
    found = shutil.which('postgres')
    if found:
        return Path(found).parent
    for pattern in ('opt/homebrew/opt/postgresql*/bin/postgres', 'usr/lib/postgresql/*/bin/postgres',
                    'usr/local/opt/postgresql*/bin/postgres'):
        candidates = sorted(Path('/').glob(pattern))
        if candidates:
            return candidates[-1].parent
    raise AssertionError('PostgreSQL server binaries (initdb, pg_ctl, postgres, psql) are required; '
                         'this integration is never skipped')


class PrivatePostgres:
    def __init__(self):
        self.bin = binaries()
        self.psql_path = str(self.bin / 'psql') if (self.bin / 'psql').exists() else shutil.which('psql')
        if not self.psql_path:
            raise AssertionError('psql is required')
        # Short path: the Unix socket path limit is ~100 bytes on macOS.
        self.directory = Path(tempfile.mkdtemp(prefix='az-p1-', dir='/tmp'))
        self.started = False
        self.counter = 0
        self.lock = threading.Lock()

    def _run(self, *args, **kwargs):
        return subprocess.run([str(arg) for arg in args], check=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True, timeout=60, **kwargs)

    def start(self):
        data = self.directory / 'data'
        self._run(self.bin / 'initdb', '-D', data, '-A', 'trust', '-U', USER, '--no-sync', '--no-locale', '-E', 'UTF8')
        self._run(self.bin / 'pg_ctl', '-D', data, '-l', self.directory / 'postgres.log',
                  '-o', f"-k {self.directory} -h '' -F -c fsync=off", '-w', 'start')
        self.started = True
        self._run(self.bin / 'createdb', '-h', self.directory, '-U', USER, TEMPLATE)
        # Every operational migration present in this checkout, in order.
        self.applied = []
        for path in sorted(MIGRATIONS.glob('0*_*.up.sql')):
            self._run(self.psql_path, '-h', self.directory, '-U', USER, '-d', TEMPLATE, '-X', '-q',
                      '-v', 'ON_ERROR_STOP=1', '-f', path)
            self.applied.append(path.name)
        return self

    def has_runtime_authority(self):
        return any(name.startswith('000005_') for name in self.applied)

    def fresh_database(self):
        with self.lock:
            self.counter += 1
            name = f'handoff_{self.counter}'
        self._run(self.bin / 'createdb', '-h', self.directory, '-U', USER, '-T', TEMPLATE, name)
        return name

    def psql_command(self, database):
        """argv prefix: SQL on stdin, unaligned tuples-only output, stop on error."""
        return [self.psql_path, '-h', str(self.directory), '-U', USER, '-d', database, '-X', '-q', '-At',
                '-v', 'ON_ERROR_STOP=1']

    def query(self, database, sql):
        return subprocess.run(self.psql_command(database), input=sql, text=True, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30).stdout.strip()

    def authority_row(self, database):
        """(runtime, generation, operation_id or None) read directly, bypassing the code under test."""
        line = self.query(database, 'SELECT runtime, generation, operation_id FROM public.assozeta_go_job_ownership '
                                    'WHERE singleton=1;')
        runtime, generation, operation = line.split('|')
        return runtime, int(generation), operation or None

    def stop(self):
        try:
            if self.started:
                subprocess.run([str(self.bin / 'pg_ctl'), '-D', str(self.directory / 'data'), '-m', 'immediate', '-w', 'stop'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        finally:
            shutil.rmtree(self.directory, ignore_errors=True)
