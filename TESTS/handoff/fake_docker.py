"""Docker CLI stand-in for lifecycle topology tests (no containers are started).

`docker compose ... config` runs the real Docker Compose so service definitions,
profiles, replicas and dependencies come from the distribution under test. Every
other call is simulated: `up` starts the named services plus their transitive
`depends_on` closure (a service rendered with zero replicas starts nothing),
`run` records a one-shot container, `stop`/`down` stop services, `ps` reports
the simulated state. Each call is appended to the state file's event log.

Environment: HANDOFF_DOCKER_STATE (JSON state path), HANDOFF_REAL_DOCKER.
"""
import fcntl
import json
import os
import subprocess
import sys

STATE = os.environ['HANDOFF_DOCKER_STATE']
REAL = os.environ['HANDOFF_REAL_DOCKER']
# Compose options that take a value and precede the subcommand.
VALUE_OPTIONS = {'--env-file', '-f', '--file', '--profile', '--project-name', '-p',
                 '--project-directory', '--ansi', '--progress', '--parallel'}
# Subcommand options that take a value.
SUB_VALUE_OPTIONS = {'--status', '--filter', '--format', '--tail', '--timeout', '-t', '--time',
                     '--user', '-u', '--workdir', '-w', '--entrypoint', '--env', '-e', '--name',
                     '--label', '-l', '--volume', '-v', '--publish', '--wait-timeout', '--pull', '--scale'}


def load():
    try:
        with open(STATE) as handle:
            return json.load(handle)
    except FileNotFoundError:
        return {'running': [], 'definitions': {}, 'events': []}


def save(state):
    temporary = STATE + '.tmp'
    with open(temporary, 'w') as handle:
        json.dump(state, handle, indent=1, sort_keys=True)
    os.replace(temporary, STATE)


def split_sub(arguments):
    options, values = [], []
    index = 0
    while index < len(arguments):
        item = arguments[index]
        if item.startswith('-') and values == []:
            if item in SUB_VALUE_OPTIONS:
                options += [item, arguments[index + 1]]
                index += 2
                continue
            options.append(item)
        else:
            values.append(item)
        index += 1
    return options, values


def rendered(global_options):
    output = subprocess.run([REAL, 'compose', *global_options, 'config', '--format', 'json'],
                            check=True, capture_output=True, text=True).stdout
    return json.loads(output)['services']


def replicas(definition):
    value = (definition.get('deploy') or {}).get('replicas')
    return 1 if value is None else int(value)


def closure(services, names, include_self=True):
    seen, pending = set(), list(names)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend((services.get(name, {}).get('depends_on') or {}).keys())
    if not include_self:
        seen -= set(names)
    return seen


def describe(definition):
    return {key: definition.get(key) for key in ('image', 'entrypoint', 'command', 'profiles')}


def compose(arguments, state):
    global_options, index = [], 0
    while index < len(arguments) and arguments[index].startswith('-'):
        if arguments[index] in VALUE_OPTIONS:
            global_options += arguments[index:index + 2]
            index += 2
        else:
            global_options.append(arguments[index])
            index += 1
    command = arguments[index] if index < len(arguments) else ''
    options, values = split_sub(arguments[index + 1:])
    if command in ('config', 'version', 'ls'):
        return None  # delegated to the real CLI by main()
    event = {'call': ['compose', command, *options, *values]}
    if command == 'up':
        services = rendered(global_options)
        requested = values or list(services)
        unknown = sorted(set(requested) - set(services))
        if unknown:
            print('no such service: ' + ' '.join(unknown), file=sys.stderr)
            return 1
        started = sorted(name for name in closure(services, requested) if replicas(services[name]) > 0)
        for name in started:
            state['definitions'][name] = describe(services[name])
        state['running'] = sorted(set(state['running']) | set(started))
        event['started'] = started
    elif command == 'run':
        services = rendered(global_options)
        service, extra = values[0], values[1:]
        if service not in services:
            print('no such service: ' + service, file=sys.stderr)
            return 1
        if '--no-deps' not in options:
            dependencies = sorted(name for name in closure(services, [service], include_self=False)
                                  if replicas(services[name]) > 0)
            for name in dependencies:
                state['definitions'][name] = describe(services[name])
            state['running'] = sorted(set(state['running']) | set(dependencies))
            event['started'] = dependencies
        event['oneoff'] = {'service': service, 'command': extra, **describe(services[service])}
    elif command in ('stop', 'kill', 'rm'):
        stopped = values or list(state['running'])
        state['running'] = sorted(set(state['running']) - set(stopped))
        event['stopped'] = sorted(stopped)
    elif command == 'down':
        event['stopped'] = list(state['running'])
        state['running'] = []
    elif command == 'ps':
        selected = [name for name in state['running'] if not values or name in values]
        state['events'].append(event)
        save(state)
        if '--services' in options or '-q' in options:
            print('\n'.join(selected))
        else:
            print('NAME SERVICE STATUS')
            for name in selected:
                print(f'simulated-{name} {name} running')
        return 0
    state['events'].append(event)
    save(state)
    return 0


def main():
    arguments = sys.argv[1:]
    with open(STATE + '.lock', 'a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX)
        state = load()
        if arguments[:1] == ['compose']:
            status = compose(arguments[1:], state)
            if status is None:
                state['events'].append({'call': arguments[:1] + [a for a in arguments[1:] if a in ('config', 'version')]})
                save(state)
        else:
            state['events'].append({'call': arguments[:3]})
            save(state)
            status = 0
            if arguments[:1] == ['inspect']:
                print('[]')
    if arguments[:1] == ['compose'] and status is None:
        os.execv(REAL, [REAL, *arguments])
    return status


if __name__ == '__main__':
    raise SystemExit(main())
