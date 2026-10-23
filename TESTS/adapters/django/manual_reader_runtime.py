"""Private Go runtime for the shared manual HTTP contract scenarios."""
import os
from pathlib import Path
import re
import socket
import subprocess

from TESTS.http_client import Client, poll


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class GoManualRuntime:
    def __init__(self, work, log, tool_env, versioned=False):
        self.work, self.log, self.tool_env = work, log, tool_env
        self.listen, self.listen_host = free_port(), '0.0.0.0' if versioned else '127.0.0.1'
        self.server = self.redis_process = self.override = None

    def __enter__(self):
        import redis
        from django.test import override_settings
        from TESTS.adapters.go.server import build_server
        try:
            build_server(self.work / 'manual-server', self.log, self.tool_env)
            self.port = free_port()
            self.redis_process = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(self.port),
                '--save', '', '--appendonly', 'no', '--requirepass', 'manual-fixture-cache', '--dir', str(self.work)],
                stdout=self.log, stderr=subprocess.STDOUT)
            self.cache = redis.Redis(host='127.0.0.1', port=self.port, db=1, password='manual-fixture-cache', socket_timeout=2)
            def ready():
                try:
                    return self.cache.ping()
                except redis.ConnectionError:
                    return False
            poll(ready, bool, timeout=10, interval=.025)
            self.override = override_settings(CACHES={'default': {'BACKEND': 'django_redis.cache.RedisCache',
                'LOCATION': f'redis://127.0.0.1:{self.port}/1', 'OPTIONS': {
                    'CLIENT_CLASS': 'django_redis.client.DefaultClient', 'PASSWORD': 'manual-fixture-cache'}}})
            self.override.enable()
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *args):
        try:
            for process in (self.server, self.redis_process):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        finally:
            if self.override:
                self.override.disable()

    def cache_snapshot(self):
        return {key: self.cache.get(key) for key in self.cache.scan_iter()}

    def start(self, manual, case_root):
        from django.conf import settings
        if self.server:
            self.server.terminate()
            self.server.wait(timeout=30)
        image = case_root / 'go-image'
        image.mkdir()
        for name, source in [('server', 'manual-server'), ('image-codec', 'image-codec')]:
            os.link(self.work / source, image / name)
        (image / 'release-manual').symlink_to(manual['MANUAL_BUNDLE_PATH'], target_is_directory=True)
        (image / 'CODE_REVISION').write_text(manual['MANUAL_APPLICATION_REVISION'])
        (image / 'VERSION').write_text(manual['RUNNING_VERSION'])
        (image / 'MANUAL_REQUIRED').write_text('true' if manual['MANUAL_BUNDLE_REQUIRED'] else 'false')
        listen = self.listen
        env = {**self.tool_env, 'DBTYPE': 'POSTGRES', 'DBNAME': 'assozeta_contract_manual_http',
            'DBUSER': 'contracts', 'DBPASSWORD': 'test-only', 'DBHOST': str(self.work), 'DBPORT': '5432',
            'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': str(self.port), 'REDIS_PASSWORD': 'manual-fixture-cache',
            'SECRET_KEY': settings.SECRET_KEY, 'JWT_SECRET_KEY': settings.JWT_SECRET_KEY,
            'JWT_PUBLIC_KEY': settings.JWT_PUBLIC_KEY, 'DEBUG': 'False', 'RATELIMIT_ENABLE': 'False',
            'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY': '', 'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET': '',
            'DATA_RESTORE_LOCK_PATH': settings.DATA_RESTORE_LOCK_PATH,
            'GO_LISTEN_ADDR': f'{self.listen_host}:{listen}', 'ASSOZETA_MANUAL_RUN_ID': manual['MANUAL_RUN_ID'],
            **{key: str(value) for key, value in manual.items()},
            **{key: getattr(settings, key) for key in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY',
                'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')}}
        self.server = subprocess.Popen([str(image / 'server')], cwd=case_root, env=env,
                                       stdout=self.log, stderr=subprocess.STDOUT)
        def ready():
            if self.server.poll() is not None:
                raise AssertionError('Go manual server exited before listening')
            try:
                with socket.create_connection(('127.0.0.1', listen), timeout=.2):
                    return True
            except OSError:
                return False
        poll(ready, bool, timeout=20, interval=.025)
        return Client(f'http://127.0.0.1:{listen}')

    def queries(self, offset):
        with (self.work / 'postgres.log').open() as stream:
            stream.seek(offset)
            logged = stream.read()
        records = re.split(r'(?m)^(?=\d{4}-\d{2}-\d{2} .* manual-http-sql )', logged)
        statements = []
        for record in records:
            match = re.search(r'LOG:  (?:execute [^:]+|statement): (.*)', record, re.S)
            if match:
                statement = re.sub(r'(?m)^\s*--[^\n]*(?:\n|$)', '', match.group(1).split('\n20', 1)[0]).strip()
                if statement:
                    statements.append(statement)
        return statements
