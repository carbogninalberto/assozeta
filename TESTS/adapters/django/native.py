"""Optional native adapter: dedicated Unix-socket PostgreSQL and loopback HTTP."""
import contextlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
from uuid import uuid4
from TESTS.http_client import Client

ROOT = Path(__file__).resolve().parents[3]


@contextlib.contextmanager
def django_environment(scenario, output, migrations=False):
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres:
        raise RuntimeError('Native adapter requires PostgreSQL binaries on PATH')
    binaries = Path(postgres).parent
    python = os.environ.get('TESTS_DJANGO_PYTHON', str(ROOT / '.venv/bin/python'))
    name = 'assozeta_contract_' + uuid4().hex[:12]
    process = None
    redis_process = None
    started = False
    # A short Unix socket directory works on macOS too. No database TCP port.
    with tempfile.TemporaryDirectory(prefix='az-contract-', dir='/tmp') as work:
        work = Path(work)
        data = work / 'postgres'
        with (output / f'{scenario}-server.log').open('w') as log, socket.socket() as listener:
            try:
                subprocess.run([str(binaries / 'initdb'), '-D', str(data), '-A', 'trust',
                    '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'], check=True, stdout=log,
                    stderr=subprocess.STDOUT, timeout=60)
                subprocess.run([str(binaries / 'pg_ctl'), '-D', str(data), '-l', str(work / 'postgres.log'),
                    '-o', f"-k {work} -h '' -F", '-w', 'start'], check=True, stdout=log,
                    stderr=subprocess.STDOUT, timeout=60)
                started = True
                redis_binary = shutil.which('redis-server')
                if not redis_binary:
                    raise RuntimeError('Native adapter requires redis-server on PATH')
                with socket.socket() as reservation:
                    reservation.bind(('127.0.0.1', 0))
                    redis_port = reservation.getsockname()[1]
                redis_password = uuid4().hex
                redis_process = subprocess.Popen([redis_binary, '--bind', '127.0.0.1',
                    '--port', str(redis_port), '--save', '', '--appendonly', 'no',
                    '--requirepass', redis_password, '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
                listener.bind(('127.0.0.1', 0))
                listener.listen()
                base_url = f'http://127.0.0.1:{listener.getsockname()[1]}'
                process = subprocess.Popen([python, str(ROOT / 'TESTS/adapters/django/server.py')],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, pass_fds=(listener.fileno(),),
                    env={**os.environ, 'TESTS_NATIVE': '1', 'TESTS_DATABASE': name,
                        'TESTS_SCENARIO': scenario, 'TESTS_REAL_MIGRATIONS': str(int(migrations)),
                        'TESTS_OUTPUT': str(work), 'TESTS_WORKDIR': str(work),
                        'TESTS_HTTP_FD': str(listener.fileno()), 'DBTYPE': 'POSTGRES',
                        'DBHOST': str(work), 'DBPORT': '5432', 'DBUSER': 'contracts',
                        'DBPASSWORD': 'contracts', 'USE_PGBOUNCER': 'False',
                        'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(redis_port),
                        'REDIS_USERNAME': 'default', 'REDIS_PASSWORD': redis_password, 'REDIS_SSLAUTH': ''})
                deadline = time.monotonic() + (900 if migrations else 300)
                while time.monotonic() < deadline:
                    if redis_process.poll() is not None:
                        raise RuntimeError('Private Redis process exited during startup')
                    if process.poll() is not None:
                        raise RuntimeError(f'Native server exited ({process.returncode}); see {output / (scenario + "-server.log")}')
                    if (work / 'fixture.json').exists():
                        try:
                            if Client(base_url, timeout=1).request('GET', '/healthz')[0] == 200:
                                break
                        except (OSError, urllib.error.URLError):
                            pass
                    time.sleep(.2)
                else:
                    raise RuntimeError(f'Native server readiness timed out; see {output / (scenario + "-server.log")}')
                yield base_url, work / 'fixture.json'
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if redis_process and redis_process.poll() is None:
                    redis_process.terminate()
                    redis_process.wait(timeout=10)
                if started:
                    subprocess.run([str(binaries / 'pg_ctl'), '-D', str(data), '-m', 'immediate', '-w', 'stop'],
                        check=True, stdout=log, stderr=subprocess.STDOUT, timeout=30)
