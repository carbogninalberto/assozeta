"""Phase 5 Docker stand-in: TESTS/handoff/p2_docker.py plus real data services and updater tools.

Containers stay simulated exactly as in fake_docker.py/p2_docker.py (imported, not
copied: real `docker compose config`, simulated up/run/stop/ps/inspect). On top of
that, a simulated installation gets the three things a runtime switch needs to be
real rather than simulated:

  - `docker compose ... exec [-T] postgres psql ARGS`: runs the private PostgreSQL
    client (HANDOFF_P5_PSQL, a JSON argv prefix naming the private server and
    database). Connection options in ARGS (-U/-d/-h/-p and their long forms) are
    dropped; everything else (-X, -qAt, -v ..., -c ..., stdin) is passed through.
  - `docker compose ... exec [-T] redis ANYTHING`: a raw byte pipe between this
    process's stdin/stdout and the private redis-server (HANDOFF_P5_REDIS,
    "host:port"); the in-container command is not interpreted, so any RESP-over-
    stdio transport (e.g. `sh -c 'exec nc 127.0.0.1 6379'`) works unchanged.
  - `docker run [OPTIONS] IMAGE /runner/TOOL.py ARGS` (the CLI's run_updater_tool):
    runs `python3 -B <HANDOFF_P5_UPDATER>/TOOL.py ARGS` on the host with this same
    stand-in first on PATH and every `--env/-e K=V` of the call in its environment.
    `--detach` runs are recorded, not executed (the runner refresh).

One-shot containers:
  - `compose run -d ... SERVICE` prints `sim-run-SERVICE` and keeps it in the
    state's `oneoffs_running` until `docker rm|stop|kill sim-run-SERVICE`.
  - `docker exec sim-run-SERVICE ...` succeeds; it and `compose run ... SERVICE`
    exit 7 when SERVICE is listed in the state's `fail_oneoff`.

Every container-changing event (up/run/stop/kill/rm/down and `docker run|exec|rm`)
is stamped with the durable world at that instant: `authority` = [runtime,
generation, admission] read from the private database and `maintenance` = whether
HANDOFF_P5_FLAG (the installation's .operations/maintenance.flag) exists. Tests
use the stamps to decide which side of the authority commit, and of the
maintenance window, each container change happened on.

Environment: HANDOFF_DOCKER_STATE, HANDOFF_REAL_DOCKER (as fake_docker),
HANDOFF_P5_PSQL, HANDOFF_P5_REDIS, HANDOFF_P5_UPDATER, HANDOFF_P5_PYTHONPATH,
HANDOFF_P5_FLAG.
"""
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_docker  # noqa: E402
import p2_docker  # noqa: E402

CONNECTION_OPTIONS = {'-U', '-d', '-h', '-p', '--username', '--dbname', '--host', '--port'}
RUN_VALUE_OPTIONS = {'-v', '--volume', '--label', '-l', '--env', '-e', '--name', '--network', '--entrypoint',
                     '--user', '-u', '-w', '--workdir', '--env-file', '--mount', '--platform', '--pull'}
CHANGING = {'up', 'run', 'stop', 'kill', 'rm', 'down', 'start', 'restart', 'create'}


def lock():
    guard = open(fake_docker.STATE + '.lock', 'a')
    fcntl.flock(guard, fcntl.LOCK_EX)
    return guard


def compose_parts(arguments):
    options, rest = p2_docker.global_split(arguments[1:])
    command = rest[0] if rest else ''
    sub_options, values = fake_docker.split_sub(rest[1:]) if rest else ([], [])
    return options, command, sub_options, values


def world():
    """[runtime, generation, admission] and the maintenance flag, read directly."""
    authority = None
    command = os.environ.get('HANDOFF_P5_PSQL')
    if command:
        try:
            sql = ("SELECT runtime||'|'||generation||'|'||coalesce(to_jsonb(o)->>'admission','open') "
                   "FROM public.assozeta_go_job_ownership o WHERE singleton=1;")
            output = subprocess.run([*json.loads(command), '-X', '-qAt'], input=sql, capture_output=True, text=True,
                                    timeout=20).stdout.strip()
            if output:
                runtime, generation, admission = output.split('|')
                authority = [runtime, int(generation), admission]
        except (OSError, ValueError, subprocess.SubprocessError):
            authority = None
    flag = os.environ.get('HANDOFF_P5_FLAG')
    return {'authority': authority, 'maintenance': bool(flag) and Path(flag).exists()}


def stamp_new_events(before):
    with lock():
        state = p2_docker.load_extra(fake_docker.load())
        if len(state['events']) > before:
            stamp = world()
            for event in state['events'][before:]:
                call = event.get('call', [])
                if call[:1] == ['compose'] and len(call) > 1 and call[1] in CHANGING or call[:1] in (
                        ['run'], ['exec'], ['rm'], ['stop'], ['kill']):
                    event.setdefault('authority', stamp['authority'])
                    event.setdefault('maintenance', stamp['maintenance'])
            fake_docker.save(state)


def record(event):
    with lock():
        state = p2_docker.load_extra(fake_docker.load())
        state['events'].append(event)
        fake_docker.save(state)
        return len(state['events']) - 1


def private_psql(values):
    rest, skip = [], False
    for item in values:
        if skip:
            skip = False
            continue
        if item in CONNECTION_OPTIONS:
            skip = True
            continue
        if any(item.startswith(option + '=') for option in CONNECTION_OPTIONS if option.startswith('--')):
            continue
        rest.append(item)
    command = json.loads(os.environ['HANDOFF_P5_PSQL']) + rest
    os.execv(command[0], command)


def redis_pipe():
    host, port = os.environ['HANDOFF_P5_REDIS'].rsplit(':', 1)
    connection = socket.create_connection((host, int(port)), timeout=60)
    connection.settimeout(None)

    def upstream():
        try:
            while True:
                chunk = os.read(0, 65536)
                if not chunk:
                    break
                connection.sendall(chunk)
        except OSError:
            pass
        finally:
            try:
                connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
    thread = threading.Thread(target=upstream, daemon=True)
    thread.start()
    try:
        while True:
            chunk = connection.recv(65536)
            if not chunk:
                break
            os.write(1, chunk)
    except OSError:
        pass
    connection.close()
    return 0


def updater_tool(arguments):
    """`docker run ... IMAGE /runner/TOOL.py ARGS` -> the checkout's updater tool on the host."""
    index, environment, detached, image = 1, {}, False, None
    while index < len(arguments):
        item = arguments[index]
        if item in ('-d', '--detach'):
            detached = True
        elif item in ('--env', '-e'):
            key, _, value = arguments[index + 1].partition('=')
            environment[key] = value
            index += 1
        elif item.startswith('--env='):
            key, _, value = item[len('--env='):].partition('=')
            environment[key] = value
        elif item in RUN_VALUE_OPTIONS:
            index += 1
        elif not item.startswith('-'):
            image = item
            break
        index += 1
    command = arguments[index + 1:] if image else []
    if not command or not command[0].startswith('/runner/') or not command[0].endswith('.py'):
        return None
    record({'call': ['run', image, *command[:2]], 'tool': Path(command[0]).name, 'detached': detached,
            'env': sorted(environment), **world()})
    if detached:
        return 0
    tool = Path(os.environ.get('HANDOFF_P5_UPDATER', '')) / Path(command[0]).name
    env = {**os.environ, **environment}
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(tool.parent), os.environ.get('HANDOFF_P5_PYTHONPATH', '')]))
    return subprocess.run([sys.executable, '-B', str(tool), *command[1:]], env=env).returncode


def oneoff(arguments, state):
    """Handle sim-run-* one-shot containers; None when not one of them."""
    names = [item for item in arguments[1:] if item.startswith('sim-run-')]
    if not names:
        return None
    name = names[0]
    service = name[len('sim-run-'):]
    event = {'call': [arguments[0], name, *[a for a in arguments[1:] if a != name][:6]]}
    status = 0
    if arguments[0] == 'exec':
        status = 7 if service in state.get('fail_oneoff', []) else 0
        event['exit'] = status
    elif arguments[0] in ('rm', 'stop', 'kill'):
        state['oneoffs_running'] = [item for item in state.get('oneoffs_running', []) if item != name]
    elif arguments[0] == 'inspect':
        running = name in state.get('oneoffs_running', [])
        print(json.dumps([{'Id': name, 'Name': '/' + name, 'State': {'Running': running, 'Status':
                                                                     'running' if running else 'exited',
                                                                     'Health': {'Status': 'healthy'}}}]))
        return 0
    else:
        return None
    state['events'].append(event)
    fake_docker.save(state)
    return status


def main():
    arguments = sys.argv[1:]
    if arguments[:1] == ['compose']:
        options, command, sub_options, values = compose_parts(arguments)
        if command == 'exec' and values:
            service, extra = values[0], values[1:]
            if service == 'postgres' and extra[:1] == ['psql'] and os.environ.get('HANDOFF_P5_PSQL'):
                return private_psql(extra[1:])
            if service == 'redis' and os.environ.get('HANDOFF_P5_REDIS'):
                return redis_pipe()
        if command == 'run' and values:
            with lock():
                state = p2_docker.load_extra(fake_docker.load())
                failing = values[0] in state.get('fail_oneoff', [])
            if failing:
                record({'call': ['compose', 'run', *sub_options, *values], 'oneoff': {'service': values[0]},
                        'exit': 7, **world()})
                print(f'simulated one-shot failure: {values[0]}', file=sys.stderr)
                return 7
    elif arguments[:1] == ['run']:
        status = updater_tool(arguments)
        if status is not None:
            return status
    elif arguments[:1] in (['exec'], ['rm'], ['stop'], ['kill'], ['inspect']):
        with lock():
            state = p2_docker.load_extra(fake_docker.load())
            status = oneoff(arguments, state)
        if status is not None:
            stamp_new_events(len(state['events']) - 1)
            return status

    with lock():
        before = len(fake_docker.load()['events'])
    sys.argv[1:] = arguments
    status = p2_docker.main()
    stamp_new_events(before)
    if arguments[:1] == ['compose'] and status == 0:
        options, command, sub_options, values = compose_parts(arguments)
        if command == 'run' and values and ('-d' in sub_options or '--detach' in sub_options):
            name = 'sim-run-' + values[0]
            with lock():
                state = p2_docker.load_extra(fake_docker.load())
                state['oneoffs_running'] = sorted(set(state.get('oneoffs_running', [])) | {name})
                fake_docker.save(state)
            print(name)
    return status


if __name__ == '__main__':
    raise SystemExit(main())
