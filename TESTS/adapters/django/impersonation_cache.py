"""Explicit interoperability boundary for Django's impersonation:v1 cache records.

Private Redis only, real configured django-redis serializer, no database access.
This proves cache DTO transport, not impersonation eligibility or audit semantics.
"""
import contextlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import pickle
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]


def main():
    output = ROOT / 'quality-reports/go-migration/impersonation-cache'
    output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'phase': 'started'}
    (output / 'results.json').write_text(json.dumps(report) + '\n')
    if not shutil.which('redis-server') or not shutil.which('go'):
        raise RuntimeError('Redis and Go are required, no skipped cache checks')
    tool_env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-imp-cache-') as directory, (output / 'redis.log').open('w') as log:
        work = Path(directory)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(port), '--save', '',
                                    '--appendonly', 'no', '--requirepass', 'synthetic-cache-only', '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
        try:
            import redis
            raw = redis.Redis(host='127.0.0.1', port=port, db=1, password='synthetic-cache-only', socket_timeout=2)
            deadline = time.monotonic() + 10
            while True:
                try:
                    raw.ping()
                    break
                except redis.ConnectionError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Private cache did not start')
                    time.sleep(.025)
            import environ
            os.environ.clear()
            os.environ.update(tool_env)
            environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
            os.environ.update(DEBUG='False', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
                JWT_SECRET_KEY='cache-only', JWT_PUBLIC_KEY='cache-only', DJANGO_SETTINGS_MODULE='core.settings',
                DBHOST='127.0.0.1', DBPORT='1', STORE_ON_DISK='True', REDIS_HOST='127.0.0.1', REDIS_PORT=str(port),
                REDIS_USERNAME='default', REDIS_PASSWORD='synthetic-cache-only', REDIS_SSLAUTH='')
            import django
            from django.db.backends.base.base import BaseDatabaseWrapper
            with patch.object(BaseDatabaseWrapper, 'ensure_connection', side_effect=RuntimeError('Cache tests prohibit database access')), patch('environ.Env.read_env'):
                with open(os.devnull, 'w') as quiet, contextlib.redirect_stderr(quiet):
                    django.setup()
                from django.core.cache import cache
                from application.impersonation import session_key, SESSION_TTL
                def uid(n):
                    return f'00000000-0000-4000-8000-{n:012d}'
                admin = {'actor_id': uid(1), 'target_id': uid(2), 'association_id': None}
                owner = {**admin, 'association_id': uid(3)}
                cache.set(session_key(uid(10)), admin, SESSION_TTL)
                cache.set(session_key(uid(11)), owner, SESSION_TTL)
                assert cache.get(session_key(uid(10))) == admin
                assert 3590 <= cache.ttl(session_key(uid(10))) <= 3600
                assert cache.make_key(session_key(uid(10))) == ':1:impersonation:v1:' + uid(10)
                shared_id = uid(1)
                vectors = []
                for protocol in (4, 5):
                    for index, record in enumerate((admin, owner, {'actor_id': uid(1), 'target_id': uid(2)},
                                                     {'actor_id': shared_id, 'target_id': shared_id, 'association_id': shared_id})):
                        identifier = uid(100 + protocol * 10 + index)
                        raw.set(cache.make_key(session_key(identifier)), pickle.dumps(record, protocol=protocol), ex=300)
                        assert cache.get(session_key(identifier)) == record
                        vectors.append({'id': identifier, 'record': record})
                revoked = uid(20)
                cache.set(session_key(revoked), admin, SESSION_TTL)
                cache.delete(session_key(revoked))
                assert cache.get(session_key(revoked)) is None
                expired = uid(21)
                cache.set(session_key(expired), admin, 1)
                fixture = work / 'fixture.json'
                fixture.write_text(json.dumps({'vectors': vectors}))
                print('Django cache format, namespaces, TTL and revocation expectations established', flush=True)
                subprocess.run(['go', 'test', '-race', '-tags=cacheinterop', './internal/repository', '-run', 'Impersonation'],
                    cwd=ROOT / 'BE_GO', check=True, timeout=120, env={**tool_env,
                    'ASSOZETA_IMPERSONATION_CACHE': f'127.0.0.1:{port}', 'ASSOZETA_IMPERSONATION_FIXTURE': str(fixture)})
                assert cache.get(session_key(uid(30))) == admin
                assert cache.get(session_key(uid(31))) == owner
                assert 3590 <= cache.ttl(session_key(uid(30))) <= 3600
                assert cache.get(session_key(uid(11))) is None, 'Go revocation did not affect Django'
                report = {'success': True, 'django_to_go_records': 2 + len(vectors), 'go_to_django_records': 2,
                          'checks': ['real serializer and namespace', 'protocols 4/5', 'TTL expiry and no renewal on read',
                                     'revocation both directions', 'unsupported opcodes denied'],
                          'dependencies': {name: version(name) for name in ('Django', 'django-redis', 'redis')},
                          'scope': 'cache record transport only; eligibility, authorization and audit remain separate',
                          'database_access': 'prohibited'}
                cache.close()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill(); process.wait(timeout=5)
            (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
