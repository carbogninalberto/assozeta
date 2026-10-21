"""Disposable Go health rehearsal with real PostgreSQL/Redis, no customer data.

This is a foundation harness, NOT the complete business-fixture Go adapter.
Requires Go, PostgreSQL binaries and redis-server. Exits nonzero on every gap.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.http_client import Client, poll


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def main():
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres or not shutil.which('redis-server') or not shutil.which('go'):
        raise RuntimeError('Go, PostgreSQL and redis-server are required; no skipped integration run')
    binaries = Path(postgres).parent
    output = ROOT / 'quality-reports/go-migration/go-health'
    output.mkdir(parents=True, exist_ok=True)
    result = {'success': False, 'checks': []}
    api = cache = None
    pg_started = False
    with tempfile.TemporaryDirectory(prefix='az-go-', dir='/tmp') as directory:
        work = Path(directory)
        with (output / 'services.log').open('w') as log:
            def run(arguments, **kwargs):
                return subprocess.run([str(a) for a in arguments], check=True, timeout=60,
                                      stdout=log, stderr=subprocess.STDOUT, **kwargs)

            def start_pg():
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log',
                     '-o', f"-k {work} -h '' -F", '-w', 'start'])

            def stop_pg():
                run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])

            def start_cache():
                return subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(redis_port),
                    '--save', '', '--appendonly', 'no', '--requirepass', 'go-contract-only',
                    '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)

            try:
                run(['go', 'build', '-o', work / 'server', './cmd/server'], cwd=ROOT / 'BE_GO')
                run(['openssl','genpkey','-algorithm','ED25519','-out',work/'key.pem'])
                run(['openssl','pkey','-in',work/'key.pem','-pubout','-out',work/'public.pem'])
                run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts',
                     '--no-sync', '--no-locale', '-E', 'UTF8'])
                start_pg(); pg_started = True
                redis_port, http_port = port(), port()
                cache = start_cache()
                environment = {'PATH': os.environ['PATH'], 'DBTYPE': 'POSTGRES', 'DBNAME': 'postgres',
                    'DBUSER': 'contracts', 'DBPASSWORD': 'go-contract-only', 'DBHOST': str(work),
                    'DBPORT': '5432', 'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(redis_port),
                    'REDIS_PASSWORD': 'go-contract-only', 'GO_LISTEN_ADDR': f'127.0.0.1:{http_port}',
                    'RUNNING_VERSION': 'v0.0.0-contract',
                    'SECRET_KEY':'synthetic-session-secret','DEBUG':'False','CORS_ALLOWED_ORIGINS':'https://allowed.example.test','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'',
                    'JWT_SECRET_KEY':(work/'key.pem').read_text(), 'JWT_PUBLIC_KEY':(work/'public.pem').read_text()}
                api = subprocess.Popen([str(work / 'server')], cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT)
                base_url = f'http://127.0.0.1:{http_port}'
                client = Client(base_url, timeout=8)

                def observe():
                    if api.poll() is not None:
                        raise RuntimeError('Go server exited unexpectedly')
                    try:
                        return client.request('GET', '/readyz')
                    except OSError:
                        return None

                poll(observe, lambda value: value == (200, {'status': 'ready', 'checks': {'database': True, 'redis': True}}), timeout=30)
                os.environ['TESTS_BASE_URL'] = base_url
                suite = unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_health')
                contracts = unittest.TextTestRunner(verbosity=2).run(suite)
                if not contracts.wasSuccessful() or contracts.skipped:
                    raise AssertionError('Portable health contracts did not all pass')
                result['checks'].append({'name': 'portable-health', 'tests': contracts.testsRun, 'passed': True})
                stop_pg(); pg_started = False
                expected = {'status': 'unavailable', 'checks': {'database': False, 'redis': True}}
                assert client.request('GET', '/readyz') == (503, expected)
                assert client.request('GET', '/healthz') == (200, {'status': 'ok', 'version': 'v0.0.0-contract'})
                result['checks'].append({'name': 'database-loss', 'passed': True})
                cache.terminate(); cache.wait(timeout=10)
                expected['checks']['redis'] = False
                assert client.request('GET', '/readyz') == (503, expected)
                result['checks'].append({'name': 'both-dependencies-lost', 'passed': True})
                for path in ('/readyz','/oauth2/refresh-token','/two-fa/info','/billing/checkout','/missing-cors-route'):
                    status,body,headers=client.request('OPTIONS',path,headers={
                        'Origin':'https://allowed.example.test','Access-Control-Request-Method':'POST',
                        'Cookie':'sessionid=synthetic-preflight-only','Authorization':'Bearer invalid'},include_headers=True)
                    assert (status,body)==(200,'') and headers['Access-Control-Allow-Origin']=='https://allowed.example.test'
                    assert 'cookie' not in headers['Vary'].lower() and headers.get('Set-Cookie') is None
                result['checks'].append({'name':'cors-preflight-with-both-dependencies-lost','paths':5,'passed':True})
                start_pg(); pg_started = True
                cache = start_cache()
                poll(observe, lambda value: value == (200, {'status': 'ready', 'checks': {'database': True, 'redis': True}}), timeout=30)
                result['checks'].append({'name': 'dependency-recovery', 'passed': True})
                api.terminate(); api.wait(timeout=30)
                assert api.returncode == 0, 'SIGTERM did not shut down cleanly'
                result['checks'].append({'name': 'sigterm-exit', 'passed': True})
                result['success'] = True
            finally:
                for process in (api, cache):
                    if process is not None and process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=30)
                        except subprocess.TimeoutExpired:
                            process.kill(); process.wait(timeout=5)
                if pg_started:
                    stop_pg()
                (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
