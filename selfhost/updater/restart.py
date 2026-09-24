"""Restart existing installation containers from a helper that outlives the runner.

No image pull, container recreation, migration, or volume mutation is performed.
The existing journal and lifecycle leases serialize this with updates/restores.
"""
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
from uuid import UUID

from common import compose_command, compose_environment, read_env
from journal import Journal, now

RECOVERY = ('Verifica i container e i log del server, poi riprova il riavvio. '
            'Le immagini installate e i dati salvati non sono stati sostituiti.')


def helper_name(operation):
    return f'assozeta-restart-{UUID(operation["id"])}'


def helper_running(operation):
    """None means Docker could not be queried; never infer death from a timeout."""
    try:
        rows = json.loads(subprocess.check_output(
            ['docker', 'container', 'ls', '--all', '--filter', f'name=^/{helper_name(operation)}$',
             '--format', '{{json .}}'], text=True, timeout=10) or 'null')
        return bool(rows and rows['State'] in ('running', 'restarting', 'created'))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return None


def reconcile_restarts(journal):
    for operation in journal.records():
        if operation.get('kind') == 'restart' and operation['status'] == 'running':
            if helper_running(operation) is False:
                journal.update(operation['id'], status='failed', stage='failed',
                               error='Il processo di riavvio è stato interrotto.', recovery=RECOVERY)


def inspect_container(container):
    return json.loads(subprocess.check_output(['docker', 'inspect', container], text=True, timeout=15))[0]


def launch_restart(root, env_file, journal, operation):
    """Launch with the exact currently running updater image, even for edge builds."""
    root, env_file = Path(root), Path(env_file)
    journal.update(operation['id'], status='running', stage='checking')
    try:
        controller = subprocess.check_output(
            compose_command(root, env_file, updater=True) + ['ps', '-q', 'updater'],
            env=compose_environment(root, env_file), text=True, timeout=15).strip()
        if not controller:
            raise RuntimeError('Missing updater container')
        image = inspect_container(controller)['Image']
        command = ['docker', 'run', '--detach', '--rm', '--pull=never', '--name', helper_name(operation),
                   '--label', f'org.assozeta.lifecycle.installation={root}',
                   '--volume', f'{root}:{root}', '--volume', '/var/run/docker.sock:/var/run/docker.sock']
        if not env_file.is_relative_to(root):
            command += ['--volume', f'{env_file.parent}:{env_file.parent}']
        subprocess.run(command + [image, '/runner/restart.py', str(root), str(env_file), operation['id']],
                       check=True, stdout=subprocess.DEVNULL, timeout=30)
    except Exception:
        # A timed-out Docker client can still have created a live helper.
        if helper_running(operation) is False:
            journal.update(operation['id'], status='failed', stage='failed',
                           error='Impossibile avviare il processo di riavvio.', recovery=RECOVERY)


def service_order(services):
    """Only long-running configured services; never rerun tools/migrations."""
    selected = {name: value for name, value in services.items() if not value.get('profiles')}
    ordered = []
    visiting = set()

    def visit(name):
        if name in ordered or name not in selected:
            return
        if name in visiting:
            raise RuntimeError('Cyclic service dependencies')
        visiting.add(name)
        for dependency in selected[name].get('depends_on', {}):
            visit(dependency)
        visiting.remove(name)
        ordered.append(name)

    for name in selected:
        visit(name)
    return ordered


class Restart:
    def __init__(self, root, env_file, journal, operation_id):
        self.root, self.env_file = Path(root), Path(env_file)
        self.journal, self.operation_id = journal, operation_id
        self.deadline = time.monotonic() + 480
        self.environment = compose_environment(root, env_file)

    def run(self, args, timeout=60):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Restart deadline exceeded')
        return subprocess.check_output(args, env=self.environment, text=True, timeout=min(timeout, remaining))

    def inventory(self, updater=False):
        command = compose_command(self.root, self.env_file, updater=updater)
        config = json.loads(self.run(command + ['config', '--format', 'json']))
        containers = []
        for service in service_order(config['services']):
            ids = self.run(command + ['ps', '--all', '-q', service]).split()
            if not ids:
                raise RuntimeError(f'Missing existing container: {service}')
            for container in ids:
                item = json.loads(self.run(['docker', 'inspect', container]))[0]
                labels = item['Config'].get('Labels', {})
                if (labels.get('com.docker.compose.project') != config['name'] or
                        labels.get('com.docker.compose.service') != service or
                        labels.get('com.docker.compose.oneoff', '').lower() == 'true'):
                    raise RuntimeError('Container does not belong to this installation service')
                containers.append(item)
        if not containers:
            raise RuntimeError('No installation containers found')
        return containers

    def wait_healthy(self, original):
        while True:
            current = json.loads(self.run(['docker', 'inspect', original['Id']]))[0]
            state = current['State']
            if current['Image'] != original['Image']:
                raise RuntimeError('Container image changed during restart')
            if (state['Running'] and state['StartedAt'] != original['State']['StartedAt'] and
                    state.get('Health', {}).get('Status', 'healthy') == 'healthy'):
                return
            if state.get('Status') == 'dead':
                raise RuntimeError('Container failed to start')
            if time.monotonic() >= self.deadline:
                raise TimeoutError('Service did not recover')
            time.sleep(2)

    def execute(self):
        with ExitStack() as stack:
            # Same order as bin/assozeta. Refuse concurrent host lifecycle work
            # and application exports/restores before stopping any container.
            for path in (self.root / '.lifecycle.flock', self.root / '.operations/lifecycle.lock'):
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = stack.enter_context(path.open('a'))
                if path.name == 'lifecycle.lock':
                    path.chmod(0o666)
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            legacy_lock = self.root / '.lifecycle.lock'
            if legacy_lock.exists() and not (legacy_lock / 'flock-managed').exists():
                raise RuntimeError('Legacy lifecycle command is active')
            if (self.root / '.updater/pending-distribution').exists() or self.journal.requiring_recovery():
                raise RuntimeError('An update requires recovery')
            application, updater = self.inventory(), self.inventory(updater=True)
            self.journal.update(self.operation_id, stage='restarting')
            # Stop consumers first, preserving queued tasks and persistent stores.
            for item in reversed(application):
                self.run(['docker', 'stop', '--time', '60', item['Id']], timeout=75)
            for item in application:
                self.run(['docker', 'start', item['Id']])
                self.wait_healthy(item)
            # This process is outside both Compose projects and survives this.
            for item in updater:
                self.run(['docker', 'restart', '--time', '30', item['Id']])
                self.wait_healthy(item)
            self.journal.update(self.operation_id, stage='health_check')
            for item in application + updater:
                self.wait_healthy(item)
            # Container health alone does not prove routing/public API readiness.
            app_url = read_env(self.env_file).get('APP_URL', '').rstrip('/')
            if not app_url.startswith(('http://', 'https://')):
                raise RuntimeError('Missing installation URL')
            for endpoint in ('/healthz', '/api/readyz'):
                while True:
                    try:
                        self.run(['curl', '--fail', '--silent', '--show-error', '--connect-timeout', '5',
                                  '--max-time', '10', app_url + endpoint], timeout=15)
                        break
                    except subprocess.CalledProcessError:
                        time.sleep(2)
            self.journal.update(self.operation_id, status='succeeded', stage='completed', verified_at=now())


def execute_restart(root, env_file, operation_id):
    journal = Journal(Path(root) / '.updater/operations.sqlite3')
    log = Path(root) / '.updater/logs' / f'{UUID(operation_id)}.log'
    try:
        Restart(root, env_file, journal, operation_id).execute()
    except Exception:
        log.parent.mkdir(parents=True, exist_ok=True)
        try:
            with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'w') as output:
                traceback.print_exc(file=output)
        finally:
            journal.update(operation_id, status='failed', stage='failed',
                           error='Riavvio non completato. Un servizio non è disponibile oppure un’altra operazione è in corso.',
                           recovery=RECOVERY)


if __name__ == '__main__':
    execute_restart(*sys.argv[1:])
