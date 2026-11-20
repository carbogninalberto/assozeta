"""Phase 2 Docker stand-in: TESTS/handoff/fake_docker.py plus the runtime authority.

Everything fake_docker.py simulates is unchanged (it is imported, not copied).
On top of it, this stand-in answers what Phase 2 lifecycle code needs to ask a
simulated installation:

  - `docker compose ... exec ... postgres psql ...` whose SQL (from -c or
    stdin) reads public.assozeta_go_job_ownership: answered from the JSON file
    HANDOFF_P2_AUTHORITY, `{"runtime": "go", "generation": 2, "operation_id":
    "<uuid>"}`, printed as psql -At does (selected columns joined by '|').
    `{"unavailable": true}` makes psql fail like an unreachable server; a
    missing file or `{"row": null}` returns no row (exit 0, no output), which is
    what a database without the seeded row answers. Every other psql call keeps
    fake_docker's behaviour (exit 0, no output).
  - `docker compose ... ps --all -q [SERVICE]`: one simulated container ID per
    service that has a container (running or stopped); fake_docker only knows
    running services, so existence is tracked here (`created`).
  - `docker compose ... ps -q SERVICE`: the container ID when it runs.
  - `docker inspect sim-<service>`: a container document with Compose labels,
    state, health (healthy when the rendered service has a health check) and
    the rendered image.
  - `docker compose ... exec -T SERVICE cat /app/VERSION`: ASSOZETA_VERSION.

Environment: HANDOFF_DOCKER_STATE, HANDOFF_REAL_DOCKER (as fake_docker),
HANDOFF_P2_AUTHORITY (optional).
"""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_docker  # noqa: E402

AUTHORITY = os.environ.get('HANDOFF_P2_AUTHORITY', '')
COLUMNS = ('singleton', 'runtime', 'generation', 'operation_id', 'committed_at')


def global_split(arguments):
    options, index = [], 0
    while index < len(arguments) and arguments[index].startswith('-'):
        if arguments[index] in fake_docker.VALUE_OPTIONS:
            options += arguments[index:index + 2]
            index += 2
        else:
            options.append(arguments[index])
            index += 1
    return options, arguments[index:]


def env_file_value(options, key):
    for index, item in enumerate(options):
        if item == '--env-file' and index + 1 < len(options):
            for line in Path(options[index + 1]).read_text().splitlines():
                if line.startswith(key + '='):
                    return line.split('=', 1)[1]
    return ''


def authority_answer(sql):
    try:
        state = json.loads(Path(AUTHORITY).read_text()) if AUTHORITY else {'row': None}
    except FileNotFoundError:
        state = {'row': None}
    if state.get('unavailable'):
        print('psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed',
              file=sys.stderr)
        return 2
    if 'row' in state and state['row'] is None:
        return 0
    row = {'singleton': '1', 'runtime': state['runtime'], 'generation': str(state.get('generation', 1)),
           'operation_id': state.get('operation_id') or '', 'committed_at': ''}
    match = re.search(r'select\s+(.*?)\s+from\s+(?:public\.)?assozeta_go_job_ownership', sql, re.I | re.S)
    selected = [column.strip().split('.')[-1] for column in match.group(1).split(',')] if match else ['*']
    if selected == ['*']:
        selected = list(COLUMNS)
    print('|'.join(row.get(column, '') for column in selected))
    return 0


def psql_sql(values):
    for index, item in enumerate(values):
        if item in ('-c', '--command') and index + 1 < len(values):
            return values[index + 1]
        if item.startswith('--command='):
            return item.split('=', 1)[1]
    if not sys.stdin.isatty():
        return sys.stdin.read()
    return ''


def load_extra(state):
    state.setdefault('created', [])
    return state


def rendered_services(options):
    output = subprocess.run([fake_docker.REAL, 'compose', *options, '--profile', '*', 'config', '--format', 'json'],
                            check=True, capture_output=True, text=True).stdout
    return json.loads(output)


def container(name, state, options):
    service = name[len('sim-'):]
    config = rendered_services(options)
    definition = config['services'].get(service, {})
    running = service in state['running']
    healthcheck = definition.get('healthcheck')
    document = {
        'Id': name, 'Name': '/' + name, 'Image': definition.get('image', ''),
        'Config': {'Image': definition.get('image', ''), 'Labels': {
            'com.docker.compose.project': config.get('name', 'assozeta'),
            'com.docker.compose.service': service, 'com.docker.compose.oneoff': 'False'}},
        'State': {'Running': running, 'Status': 'running' if running else 'exited', 'StartedAt': 'simulated'},
        'HostConfig': {'RestartPolicy': {'Name': definition.get('restart', 'no')}},
    }
    if healthcheck and not healthcheck.get('disable'):
        document['State']['Health'] = {'Status': 'healthy' if running else 'unhealthy'}
    return document


def intercept(arguments):
    """Return an exit status when handled here, None to delegate to fake_docker."""
    if arguments[:1] == ['inspect']:
        names = [item for item in arguments[1:] if not item.startswith('-')]
        if names and all(item.startswith('sim-') for item in names):
            state = load_extra(fake_docker.load())
            # Rendered with the Compose options of the last container listing.
            print(json.dumps([container(item, state, state.get('options', [])) for item in names]))
            return 0
        return None
    if arguments[:1] != ['compose']:
        return None
    options, rest = global_split(arguments[1:])
    if not rest:
        return None
    command = rest[0]
    sub_options, values = fake_docker.split_sub(rest[1:])
    state = load_extra(fake_docker.load())
    if command == 'exec':
        if not values:
            return None
        service, extra = values[0], values[1:]
        if service == 'postgres' and extra[:1] == ['psql']:
            sql = psql_sql(extra[1:])
            if 'assozeta_go_job_ownership' in sql:
                state['events'].append({'call': ['compose', 'exec', 'postgres', 'psql', 'authority']})
                fake_docker.save(state)
                return authority_answer(sql)
            return None
        if extra == ['cat', '/app/VERSION']:
            state['events'].append({'call': ['compose', 'exec', service, 'cat', '/app/VERSION']})
            fake_docker.save(state)
            print(env_file_value(options, 'ASSOZETA_VERSION'))
            return 0
        return None
    if command == 'ps' and ('-q' in sub_options or '--quiet' in sub_options) and '--services' not in sub_options:
        everything = '--all' in sub_options or '-a' in sub_options
        pool = sorted(set(state['created']) | set(state['running'])) if everything else state['running']
        selected = [name for name in pool if not values or name in values]
        state['options'] = options
        state['events'].append({'call': ['compose', 'ps', *sub_options, *values]})
        fake_docker.save(state)
        if selected:
            print('\n'.join('sim-' + name for name in selected))
        return 0
    return None


def model(options):
    output = subprocess.run([fake_docker.REAL, 'compose', *options, 'config', '--services'],
                            check=True, capture_output=True, text=True).stdout
    return set(output.split())


def profile_scoped(arguments):
    """Real Compose semantics fake_docker does not model, or None.

    Without service names, stop/kill/rm/down act only on the services of the
    rendered model (profiles not enabled are left alone: a `docker compose
    stop` without `--profile` does not stop a profile service). With explicit
    names, up/run/stop also reach services whose profile is not enabled.
    Returns (status or None, rewritten arguments).
    """
    if arguments[:1] != ['compose']:
        return None, arguments
    options, rest = global_split(arguments[1:])
    if not rest:
        return None, arguments
    command = rest[0]
    sub_options, values = fake_docker.split_sub(rest[1:])
    if command in ('up', 'run') and values:
        return None, ['compose', *options, '--profile', '*', *rest]
    if command in ('stop', 'kill', 'rm', 'down') and not values:
        state = load_extra(fake_docker.load())
        scope = model(options)
        affected = sorted(set(state['running']) & scope)
        state['running'] = sorted(set(state['running']) - scope)
        if command in ('rm', 'down'):
            state['created'] = sorted(set(state['created']) - scope)
        state['events'].append({'call': ['compose', command, *sub_options], 'stopped': affected})
        fake_docker.save(state)
        return 0, arguments
    return None, arguments


def main():
    arguments = sys.argv[1:]
    with open(fake_docker.STATE + '.lock', 'a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        status = intercept(arguments)
        if status is None:
            status, arguments = profile_scoped(arguments)
    if status is not None:
        return status
    sys.argv[1:] = arguments
    result = fake_docker.main()
    # Services listed in the state's `crashing` exit right after they start.
    with open(fake_docker.STATE + '.lock', 'a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        state = load_extra(fake_docker.load())
        crashed = set(state['running']) & set(state.get('crashing', []))
        if crashed:
            state['created'] = sorted(set(state['created']) | crashed)
            state['running'] = sorted(set(state['running']) - crashed)
            fake_docker.save(state)
    # Containers keep existing after stop; only rm/down remove them.
    with open(fake_docker.STATE + '.lock', 'a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        state = load_extra(fake_docker.load())
        created = set(state['created']) | set(state['running'])
        if arguments[:1] == ['compose']:
            _, rest = global_split(arguments[1:])
            if rest and rest[0] == 'down':
                created = set()
            elif rest and rest[0] == 'rm':
                _, values = fake_docker.split_sub(rest[1:])
                created -= set(values or created)
        state['created'] = sorted(created)
        fake_docker.save(state)
    return result


if __name__ == '__main__':
    raise SystemExit(main())
