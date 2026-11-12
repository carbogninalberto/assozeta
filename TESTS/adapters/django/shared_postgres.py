"""Shared PostgreSQL server and per-run database helpers for parity tests.

A single PostgreSQL server per machine is started once in a shared directory. Each
parity run creates a uniquely named database and drops it at the end, so concurrent
runs never share a database. A migrated template database, keyed by a hash of the
migration and model sources, lets a run clone its database instead of migrating.

The module uses only the Python standard library and talks to the server through
the ``psql`` and ``pg_ctl`` binaries. It does not import Django.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
from typing import Iterator

SHARED_BY_DEFAULT = False
DATABASE_USER = 'contracts'
SOCKET_LIMIT = 100
COMMAND_TIMEOUT = 120
DEAD_DATABASE_AGE = 600
TEMPLATE_KEY_LENGTH = 16

# The test settings module accepts only names that start with this prefix and
# are Python identifiers, so every generated database name keeps that shape.
_RUN_NAME = re.compile(r'^assozeta_contract_run_(\d+)_(\d+)_[0-9a-f]{6}$')
_BUILD_NAME = re.compile(r'^assozeta_contract_tplb_([0-9a-f]{16})_(\d+)_[0-9a-f]{6}$')
_TEMPLATE_NAME = re.compile(r'^assozeta_contract_tpl_([0-9a-f]{16})$')
_TEMPLATE_KEY = re.compile(r'^[0-9a-f]{16}$')


@dataclass(frozen=True)
class Settings:
    postgres: Path | None
    binaries: Path | None


@dataclass
class RunDatabase:
    host: str
    name: str
    from_template: bool
    key: str


_ACTIVE = Settings(None, None)


def activate(settings: Settings) -> None:
    global _ACTIVE
    _ACTIVE = settings


def active() -> Settings:
    return _ACTIVE


def _cache_root(environ) -> Path:
    value = environ.get('XDG_CACHE_HOME')
    if value:
        return Path(value).expanduser()
    home = environ.get('HOME')
    return (Path(home).expanduser() if home else Path.home()) / '.cache'


def default_directory(environ) -> Path:
    return _cache_root(environ) / 'assozeta-test-postgres'


def default_binary_cache(environ) -> Path:
    return _cache_root(environ) / 'assozeta-test-binaries'


def resolve(environ) -> Settings:
    if environ.get('ASSOZETA_TEST_PRIVATE_POSTGRES') == '1':
        return Settings(None, None)
    shared = environ.get('ASSOZETA_TEST_SHARED_POSTGRES')
    if shared:
        directory = Path(shared).expanduser().resolve()
    elif SHARED_BY_DEFAULT:
        directory = default_directory(environ)
    else:
        return Settings(None, None)
    cache = environ.get('ASSOZETA_TEST_BINARY_CACHE')
    binaries = Path(cache).expanduser().resolve() if cache else default_binary_cache(environ)
    return Settings(directory, binaries)


def postgres_binaries() -> Path:
    postgres = shutil.which('postgres')
    if not postgres:
        candidates = sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'))
        postgres = str(candidates[-1]) if candidates else None
    if not postgres:
        raise AssertionError('Native PostgreSQL required')
    return Path(postgres).parent


def server_environment() -> dict:
    environment = {'LANG': 'C', 'LC_ALL': 'C'}
    for name in ('PATH', 'HOME'):
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    return environment


def _command(arguments, directory: Path, *, check: bool = True):
    with (directory / 'server.log').open('ab') as log:
        return subprocess.run(
            [str(argument) for argument in arguments],
            check=check,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=server_environment(),
            timeout=COMMAND_TIMEOUT,
        )


@contextlib.contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _socket_path(directory: Path) -> Path:
    return directory / '.s.PGSQL.5432'


def ensure_server(directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    socket_path = _socket_path(directory)
    if len(str(socket_path)) > SOCKET_LIMIT:
        raise AssertionError(
            f'Shared PostgreSQL socket path is too long '
            f'({len(str(socket_path))} > {SOCKET_LIMIT} characters): {socket_path}'
        )
    binaries = postgres_binaries()
    data = directory / 'data'
    with _exclusive_lock(directory / 'server.lock'):
        if not (data / 'PG_VERSION').is_file():
            if data.is_dir():
                shutil.rmtree(data)
            elif data.exists():
                data.unlink()
            _command([
                binaries / 'initdb', '-D', data, '-A', 'trust', '-U', DATABASE_USER,
                '--no-sync', '--no-locale', '-E', 'UTF8',
            ], directory)
        status = _command([binaries / 'pg_ctl', '-D', data, 'status'], directory, check=False)
        if status.returncode != 0:
            _command([
                binaries / 'pg_ctl', '-D', data, '-l', directory / 'postgres.log',
                '-o', f"-k {directory} -h '' -F -c max_connections=400",
                '-w', '-t', '60', 'start',
            ], directory)
    return binaries


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def psql(directory, sql, *, database='postgres', binaries=None) -> str:
    # Without an explicit installation, use the one that runs the server, not
    # whichever ``psql`` happens to be first on PATH.
    executable = Path(binaries) / 'psql' if binaries is not None else postgres_binaries() / 'psql'
    result = subprocess.run(
        [
            str(executable), '-h', str(directory), '-U', DATABASE_USER, '-d', database,
            '-X', '-w', '-At', '-v', 'ON_ERROR_STOP=1', '-c', sql,
        ],
        check=True,
        capture_output=True,
        text=True,
        env=server_environment(),
        timeout=COMMAND_TIMEOUT,
    )
    return result.stdout


def _database_exists(directory, name: str, *, binaries=None) -> bool:
    output = psql(
        directory,
        f'SELECT 1 FROM pg_database WHERE datname = {_quote_literal(name)}',
        binaries=binaries,
    )
    return bool(output.strip())


def _checked_key(key: str) -> str:
    if not isinstance(key, str) or not _TEMPLATE_KEY.fullmatch(key):
        raise ValueError(f'Invalid template key: {key!r}')
    return key


def run_name(now=None) -> str:
    created = int(time.time() if now is None else now)
    return f'assozeta_contract_run_{created}_{os.getpid()}_{secrets.token_hex(3)}'


def template_name(key: str) -> str:
    return 'assozeta_contract_tpl_' + _checked_key(key)


def build_name(key: str) -> str:
    return f'assozeta_contract_tplb_{_checked_key(key)}_{os.getpid()}_{secrets.token_hex(3)}'


def parse_run_name(name):
    if not isinstance(name, str):
        return None
    match = _RUN_NAME.fullmatch(name)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def parse_build_name(name):
    if not isinstance(name, str):
        return None
    match = _BUILD_NAME.fullmatch(name)
    if not match:
        return None
    return int(match.group(2))


def parse_template_name(name):
    if not isinstance(name, str):
        return None
    match = _TEMPLATE_NAME.fullmatch(name)
    return match.group(1) if match else None


def _template_sources(root: Path):
    selected = set()
    be = root / 'BE'
    if be.is_dir():
        for path in be.rglob('*'):
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if not path.is_file() or '__pycache__' in relative.parts:
                continue
            parts = relative.parts
            if path.name.endswith('.py') and 'migrations' in parts[:-1]:
                selected.add(path)
            elif path.name == 'models.py':
                selected.add(path)
            elif path.name.endswith('.py') and 'models' in parts[:-1]:
                selected.add(path)
    for path in (be / 'core/settings.py', root / 'TESTS/adapters/django/settings.py'):
        if path.is_file():
            selected.add(path)
    go = root / 'BE_GO/migrations'
    if go.is_dir():
        for path in go.rglob('*'):
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            if path.is_file() and '__pycache__' not in relative.parts:
                selected.add(path)
    return selected


def template_key(root: Path, extra: str = '') -> str:
    root = Path(root)
    digest = hashlib.sha256()
    for path in sorted(_template_sources(root), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode('utf-8', 'surrogateescape'))
        digest.update(b'\0')
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode('ascii'))
        digest.update(b'\n')
    digest.update(b'extra\0')
    digest.update(extra.encode('utf-8'))
    return digest.hexdigest()[:TEMPLATE_KEY_LENGTH]


def server_running(directory) -> bool:
    directory = Path(directory)
    if not (directory / 'data' / 'PG_VERSION').is_file():
        return False
    try:
        binaries = postgres_binaries()
        result = _command([binaries / 'pg_ctl', '-D', directory / 'data', 'status'], directory, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def run_database(directory, key, *, report=None) -> Iterator[RunDatabase]:
    directory = Path(directory)
    binaries = ensure_server(directory)
    try:
        cleanup(directory, hours=24, dead_only=True)
    except Exception:
        pass
    name = run_name()
    template = template_name(key)
    from_template = False
    if _database_exists(directory, template, binaries=binaries):
        delays = (0.5, 1.0)
        for attempt in range(3):
            try:
                psql(
                    directory,
                    f'CREATE DATABASE {_quote_identifier(name)} '
                    f'TEMPLATE {_quote_identifier(template)}',
                    binaries=binaries,
                )
                from_template = True
                break
            except subprocess.CalledProcessError:
                if attempt == len(delays):
                    break
                time.sleep(delays[attempt])
    if not from_template:
        psql(
            directory,
            f'CREATE DATABASE {_quote_identifier(name)}',
            binaries=binaries,
        )
    if isinstance(report, dict):
        report['shared_postgres'] = {
            'database_created': True,
            'from_template': from_template,
            'template_key': key,
            'database_dropped': False,
        }
    try:
        yield RunDatabase(host=str(directory), name=name, from_template=from_template, key=key)
    finally:
        propagating = sys.exc_info()[0] is not None
        dropped = False
        failure = None
        try:
            psql(
                directory,
                f'DROP DATABASE IF EXISTS {_quote_identifier(name)} WITH (FORCE)',
                binaries=binaries,
            )
            dropped = not _database_exists(directory, name, binaries=binaries)
        except Exception as error:
            failure = error
        entry = report.get('shared_postgres') if isinstance(report, dict) else None
        if isinstance(entry, dict):
            entry['database_dropped'] = dropped
        if failure is not None and not propagating:
            raise failure


def publish_template(directory, database: str, key: str) -> bool:
    directory = Path(directory)
    build = None
    try:
        template = template_name(key)
        build = build_name(key)
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f'template-{key}.lock').open('a') as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
            try:
                if _database_exists(directory, template):
                    return False
                psql(
                    directory,
                    f'CREATE DATABASE {_quote_identifier(build)} '
                    f'TEMPLATE {_quote_identifier(database)}',
                )
                psql(
                    directory,
                    f'ALTER DATABASE {_quote_identifier(build)} '
                    f'RENAME TO {_quote_identifier(template)}',
                )
                psql(
                    directory,
                    f'ALTER DATABASE {_quote_identifier(template)} '
                    f'WITH IS_TEMPLATE true ALLOW_CONNECTIONS false',
                )
                return True
            except Exception:
                try:
                    psql(
                        directory,
                        f'DROP DATABASE IF EXISTS {_quote_identifier(build)} WITH (FORCE)',
                    )
                except Exception:
                    pass
                return False
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception:
        return False


def cleanup(directory, *, hours: float = 6, dead_only: bool = False,
            templates: bool = False, now=None) -> list:
    directory = Path(directory)
    if not server_running(directory):
        return []
    listing = psql(
        directory,
        r"SELECT datname FROM pg_database WHERE datname LIKE 'assozeta\_contract\_%'",
    )
    names = [line.strip() for line in listing.splitlines() if line.strip()]
    moment = time.time() if now is None else now
    dropped = []
    for name in names:
        try:
            if parse_template_name(name) is not None:
                if not templates:
                    continue
                psql(
                    directory,
                    f'ALTER DATABASE {_quote_identifier(name)} WITH IS_TEMPLATE false',
                )
            elif parse_run_name(name) is not None:
                created, pid = parse_run_name(name)
                age = moment - created
                dead = not _process_alive(pid)
                abandoned = dead and age > DEAD_DATABASE_AGE
                expired = not dead_only and age > hours * 3600
                if not (abandoned or expired):
                    continue
            elif parse_build_name(name) is not None:
                if _process_alive(parse_build_name(name)):
                    continue
            else:
                continue
            psql(
                directory,
                f'DROP DATABASE IF EXISTS {_quote_identifier(name)} WITH (FORCE)',
            )
            dropped.append(name)
        except subprocess.CalledProcessError:
            continue
    return dropped


def status(directory) -> dict:
    directory = Path(directory)
    running = server_running(directory)
    names = []
    if running:
        listing = psql(
            directory,
            r"SELECT datname FROM pg_database WHERE datname LIKE 'assozeta\_contract\_%'",
        )
        names = sorted(line.strip() for line in listing.splitlines() if line.strip())
    templates = [name for name in names if parse_template_name(name) is not None]
    databases = [name for name in names if parse_template_name(name) is None]
    return {
        'directory': str(directory),
        'running': running,
        'databases': databases,
        'templates': templates,
    }


def stop(directory) -> bool:
    directory = Path(directory)
    if server_running(directory):
        binaries = postgres_binaries()
        _command([
            binaries / 'pg_ctl', '-D', directory / 'data', '-m', 'fast', '-w', 'stop',
        ], directory, check=False)
    return not server_running(directory)
