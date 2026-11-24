"""Phase 6 Docker stand-in: TESTS/handoff/p5_docker.py plus real data tools and Compose exec semantics.

Containers stay simulated exactly as in fake_docker.py/p2_docker.py/p5_docker.py (imported,
not copied). Phase 6 lifecycle commands (backup, restore, health, superuser, manual sync)
additionally need what a real installation gives them:

  - `docker compose ... exec [-T] [-e K=V] postgres TOOL ARGS` with TOOL one of psql,
    pg_dump, pg_restore, createdb, dropdb runs the real client from HANDOFF_P6_PGBIN
    against the private cluster (Unix socket HANDOFF_P6_PGHOST, role HANDOFF_P6_PGUSER).
    Database names are mapped through HANDOFF_P6_DBMAP ({installation DBNAME: private
    database}) wherever they appear (-d, positional names, `-v db=/old=/new=` values), so
    the installation's own restore swap renames real databases. stdin/stdout pass through.
  - `docker compose ... exec [-T] [-e REDISCLI_AUTH=...] redis redis-cli ARGS` sends ARGS as
    one command to the private Redis (HANDOFF_P5_REDIS) and prints the reply as redis-cli
    does; any other redis exec stays p5_docker's raw RESP pipe.
  - `docker compose ... exec SERVICE ...` of a service that is not running fails like
    Compose does (exit 1, `service "SERVICE" is not running`) and is recorded with
    `exit: 1`; a running service is recorded (fake_docker) and exits 0.
  - `docker image inspect REF`: exit 1 when REF is listed in the state's `images_absent`,
    otherwise a minimal document (exit 0).
  - Image crash injection: a service started from an image listed in the state's
    `crashing_images` exits right after it starts (p2_docker `crashing` works by service name;
    this works by image, so a failing *target* image can be told apart from the source one).
  - Kill injection: when the state's `kill_after` fragment occurs in a call, that call
    completes, the fragment is cleared, a `killed` event is recorded and the whole process
    group of the caller is killed with SIGKILL (tests start the CLI in its own session),
    which is how an interrupted lifecycle command looks to the next one.

Environment: everything p5_docker.py reads, plus HANDOFF_P6_PGBIN, HANDOFF_P6_PGHOST,
HANDOFF_P6_PGUSER, HANDOFF_P6_DBMAP, HANDOFF_P6_REDIS_PASSWORD.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_docker  # noqa: E402
import p2_docker  # noqa: E402
import p5_docker  # noqa: E402

PG_TOOLS = {'psql', 'pg_dump', 'pg_restore', 'createdb', 'dropdb'}
EXEC_VALUE_OPTIONS = {'-e', '--env', '-u', '--user', '-w', '--workdir', '--index'}
# Connection options of the PostgreSQL clients that take a value.
PG_VALUE = {'-U', '--username', '-d', '--dbname', '-h', '--host', '-p', '--port', '--maintenance-db', '-v',
            '--variable', '-c', '--command', '-f', '--file', '-F', '--format', '-n', '-t', '-T', '--template',
            '-O', '--owner', '-E', '--encoding', '-j', '--jobs', '-o', '--output'}
SHARED = {'postgres', 'redis', 'minio', 'renderer', 'web'}


def record(event):
    with p5_docker.lock():
        state = p2_docker.load_extra(fake_docker.load())
        state['events'].append(event)
        fake_docker.save(state)


def split_exec(values_and_options):
    """`exec` sub-arguments -> (environment {K: V}, service, command)."""
    environment, index, items = {}, 0, values_and_options
    while index < len(items) and items[index].startswith('-'):
        item = items[index]
        if item in ('-e', '--env'):
            key, _, value = items[index + 1].partition('=')
            environment[key] = value
            index += 2
            continue
        if item.startswith('--env='):
            key, _, value = item[len('--env='):].partition('=')
            environment[key] = value
        elif item in EXEC_VALUE_OPTIONS:
            index += 2
            continue
        index += 1
    if index >= len(items):
        return environment, None, []
    return environment, items[index], items[index + 1:]


def database(name):
    mapping = json.loads(os.environ.get('HANDOFF_P6_DBMAP') or '{}')
    return mapping.get(name, name)


def postgres_tool(tool, arguments):
    """The real client against the private cluster, names mapped, connection forced local."""
    rest, index = [], 0
    while index < len(arguments):
        item = arguments[index]
        value = arguments[index + 1] if index + 1 < len(arguments) else None
        if item in ('-h', '--host', '-p', '--port'):
            index += 2
            continue
        if item.startswith(('--host=', '--port=')):
            index += 1
            continue
        if item in ('-U', '--username'):
            index += 2
            continue
        if item.startswith('--username='):
            index += 1
            continue
        if item in ('-d', '--dbname', '--maintenance-db') and value is not None:
            rest += [item, database(value)]
            index += 2
            continue
        if item.startswith(('--dbname=', '--maintenance-db=')):
            key, _, value = item.partition('=')
            rest.append(f'{key}={database(value)}')
            index += 1
            continue
        if item in ('-v', '--variable') and value is not None:
            key, _, assigned = value.partition('=')
            rest += [item, f'{key}={database(assigned)}' if key in ('db', 'old', 'new') else value]
            index += 2
            continue
        if item in PG_VALUE and value is not None:
            rest += [item, value]
            index += 2
            continue
        if not item.startswith('-') and tool in ('createdb', 'dropdb'):
            rest.append(database(item))
            index += 1
            continue
        rest.append(item)
        index += 1
    binary = str(Path(os.environ['HANDOFF_P6_PGBIN']) / tool)
    command = [binary, '-h', os.environ['HANDOFF_P6_PGHOST'], '-U', os.environ['HANDOFF_P6_PGUSER'], *rest]
    return subprocess.run(command).returncode


def redis_reply(stream):
    line = stream.readline()
    kind, rest = line[:1], line[1:-2]
    if kind in (b'+', b'-'):
        return ('(error) ' if kind == b'-' else '') + rest.decode()
    if kind == b':':
        return str(int(rest))
    if kind == b'$':
        size = int(rest)
        return '' if size < 0 else stream.read(size + 2)[:-2].decode(errors='replace')
    if kind == b'*':
        size = int(rest)
        return '\n'.join(redis_reply(stream) for _ in range(max(size, 0)))
    return ''


def redis_cli(environment, arguments):
    command, index = [], 0
    password = environment.get('REDISCLI_AUTH') or os.environ.get('HANDOFF_P6_REDIS_PASSWORD', '')
    while index < len(arguments):
        item = arguments[index]
        if item in ('-a', '--pass', '--user', '-h', '-p', '-n'):
            if item in ('-a', '--pass'):
                password = arguments[index + 1]
            index += 2
            continue
        if item in ('--no-auth-warning', '--raw', '-x'):
            index += 1
            continue
        command.append(item)
        index += 1
    host, port = os.environ['HANDOFF_P5_REDIS'].rsplit(':', 1)
    with socket.create_connection((host, int(port)), timeout=30) as sock, sock.makefile('rb') as stream:
        def send(*parts):
            frame = [b'*%d\r\n' % len(parts)]
            for part in parts:
                data = part.encode()
                frame += [b'$%d\r\n' % len(data), data, b'\r\n']
            sock.sendall(b''.join(frame))
            return redis_reply(stream)
        if password:
            send('AUTH', 'default', password)
        reply = send(*command) if command else ''
    print(reply)
    return 1 if reply.startswith('(error)') else 0


def handle_exec(arguments):
    """Return an exit status for a compose exec handled here, None to delegate."""
    options, rest = p2_docker.global_split(arguments[1:])
    if not rest or rest[0] != 'exec':
        return None
    environment, service, command = split_exec(rest[1:])
    if service is None:
        return None
    if service == 'postgres' and command[:1] and command[0] in PG_TOOLS:
        status = postgres_tool(command[0], command[1:])
        record({'call': ['compose', 'exec', 'postgres', command[0]], 'exit': status})
        return status
    if service == 'redis' and command[:1] == ['redis-cli']:
        status = redis_cli(environment, command[1:])
        record({'call': ['compose', 'exec', 'redis', 'redis-cli', *command[1:2]], 'exit': status})
        return status
    if service in SHARED:
        return None
    with p5_docker.lock():
        state = p2_docker.load_extra(fake_docker.load())
        running = service in state['running']
    if not running:
        record({'call': ['compose', 'exec', service, *command], 'exit': 1, 'not_running': True})
        print(f'service "{service}" is not running', file=sys.stderr)
        return 1
    return None


def image_inspect(arguments):
    if arguments[:2] != ['image', 'inspect']:
        return None
    references = [item for item in arguments[2:] if not item.startswith('-')]
    with p5_docker.lock():
        state = p2_docker.load_extra(fake_docker.load())
        absent = [reference for reference in references if reference in state.get('images_absent', [])]
        state['events'].append({'call': ['image', 'inspect', *references], 'exit': 1 if absent else 0})
        fake_docker.save(state)
    if absent:
        print(f'Error response from daemon: No such image: {absent[0]}', file=sys.stderr)
        return 1
    print(json.dumps([{'Id': 'sha256:' + hashlib.sha256(reference.encode()).hexdigest(),
                       'RepoTags': [reference], 'RepoDigests': []} for reference in references]))
    return 0


def crash_images():
    with p5_docker.lock():
        state = p2_docker.load_extra(fake_docker.load())
        images = set(state.get('crashing_images') or ())
        crashed = sorted(name for name in state['running']
                         if (state['definitions'].get(name) or {}).get('image') in images)
        if crashed:
            state['created'] = sorted(set(state['created']) | set(crashed))
            state['running'] = sorted(set(state['running']) - set(crashed))
            state['events'].append({'call': ['crashed', *crashed]})
            fake_docker.save(state)


def kill_if_requested(arguments):
    with p5_docker.lock():
        state = p2_docker.load_extra(fake_docker.load())
        fragment = state.get('kill_after')
        if not fragment or fragment not in ' '.join(arguments):
            return
        state['kill_after'] = None
        state['events'].append({'call': ['killed', fragment]})
        fake_docker.save(state)
    sys.stdout.flush()
    os.killpg(os.getpgrp(), signal.SIGKILL)


def runner_credentials(arguments):
    """`docker run ... IMAGE /runner/provision.py --credentials` (updater_api) or `--notices-credentials`
    (updater_notices, Phase 4 family 08) writes into a Docker volume as the image's root user: simulated."""
    options = ('--credentials', '--notices-credentials')
    if arguments[:1] == ['run'] and '/runner/provision.py' in arguments and arguments[-1] in options:
        record({'call': ['run', 'provision.py', arguments[-1]], 'tool': 'provision.py'})
        return 0
    return None


def main():
    arguments = sys.argv[1:]
    status = runner_credentials(arguments)
    if status is not None:
        return status
    if arguments[:1] == ['compose']:
        status = handle_exec(arguments)
    elif arguments[:2] == ['image', 'inspect']:
        status = image_inspect(arguments)
    if status is None:
        sys.argv[1:] = arguments
        status = p5_docker.main()
    crash_images()
    kill_if_requested(arguments)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
