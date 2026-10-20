"""Disposable HTTP server and PostgreSQL; no access to the development database."""
import contextlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
from uuid import uuid4
from TESTS.http_client import Client

ROOT = Path(__file__).resolve().parents[3]


@contextlib.contextmanager
def django_environment(scenario, output, migrations=False):
    command = [str(ROOT / 'selfhost/bin/assozeta'), 'dev-compose']
    name = 'assozeta-contract-' + uuid4().hex[:12]
    postgres = name + '-postgres'
    redis = name + '-redis'
    database = name.replace('-', '_')
    # Inspect configuration without printing environment values or credentials.
    config = json.loads(subprocess.check_output(command + ['config', '--format', 'json'], cwd=ROOT, timeout=30))
    network = config['networks']['dev']['name']
    subprocess.run(['docker', 'network', 'inspect', network], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=10)
    process = None
    with tempfile.TemporaryDirectory(prefix=name) as work:
        work = Path(work)
        with (output / f'{scenario}-server.log').open('w') as log:
            try:
                # These credentials belong only to this disposable container.
                # No host port or persistent volume is exposed. Durability is
                # unnecessary for fixtures; production PostgreSQL is untouched.
                subprocess.run(['docker', 'run', '-d', '--rm', '--name', postgres,
                    '--network', network, '--tmpfs', '/var/lib/postgresql/data',
                    '-e', 'POSTGRES_USER=contracts', '-e', 'POSTGRES_PASSWORD=contracts',
                    config['services']['postgres']['image'], '-c', 'fsync=off'],
                    check=True, stdout=log, stderr=subprocess.STDOUT, timeout=60)
                subprocess.run(['docker', 'run', '-d', '--rm', '--name', redis,
                    '--network', network, config['services']['redis']['image'],
                    'redis-server', '--save', '', '--appendonly', 'no', '--requirepass', 'contracts'],
                    check=True, stdout=log, stderr=subprocess.STDOUT, timeout=60)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    ready = subprocess.run(['docker', 'exec', postgres, 'pg_isready', '-U', 'contracts'],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    if ready.returncode == 0:
                        break
                    time.sleep(.2)
                else:
                    raise RuntimeError('Disposable PostgreSQL did not become ready')
                process = subprocess.Popen(command + ['run', '--rm', '--no-deps', '--name', name,
                    '-p', '127.0.0.1::8000', '-v', f'{work}:/contracts-output',
                    '-e', f'DBHOST={postgres}', '-e', 'DBPORT=5432',
                    '-e', 'DBUSER=contracts', '-e', 'DBPASSWORD=contracts',
                    '-e', f'REDIS_HOST={redis}', '-e', 'REDIS_PORT=6379',
                    '-e', 'REDIS_PASSWORD=contracts', '-e', 'REDIS_USERNAME=default', '-e', 'REDIS_SSLAUTH=',
                    '-e', f'TESTS_DATABASE={database}', '-e', f'TESTS_SCENARIO={scenario}',
                    '-e', f'TESTS_REAL_MIGRATIONS={int(migrations)}',
                    'api', 'python', '/manual-code/TESTS/adapters/django/server.py'],
                    cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                deadline = time.monotonic() + (900 if migrations else 300)
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError(f'Adapter exited ({process.returncode}); see {output / (scenario + "-server.log")}')
                    if (work / 'fixture.json').exists():
                        port = subprocess.run(['docker', 'port', name, '8000/tcp'], capture_output=True, text=True, timeout=5)
                        if port.returncode == 0:
                            base_url = 'http://' + port.stdout.strip()
                            try:
                                if Client(base_url, timeout=1).request('GET', '/healthz')[0] == 200:
                                    break
                            except (OSError, urllib.error.URLError):
                                pass
                    time.sleep(.2)
                else:
                    raise RuntimeError(f'Adapter readiness timed out; see {output / (scenario + "-server.log")}')
                yield base_url, work / 'fixture.json'
            finally:
                cleanup_errors = []
                if process is not None:
                    try:
                        subprocess.run(['docker', 'stop', '--time', '10', name], timeout=20,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except subprocess.TimeoutExpired:
                        cleanup_errors.append(f'Stop timed out: {name}')
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
                # Attempt both removals even if Docker became unavailable.
                for container in (name, postgres, redis):
                    try:
                        result = subprocess.run(['docker', 'rm', '-f', container], timeout=15,
                                                capture_output=True, text=True)
                        if result.returncode and 'No such container' not in result.stderr:
                            cleanup_errors.append(f'Removal failed: {container}: {result.stderr}')
                    except subprocess.TimeoutExpired:
                        cleanup_errors.append(f'Removal timed out: {container}')
                if cleanup_errors:
                    raise RuntimeError('; '.join(cleanup_errors))
