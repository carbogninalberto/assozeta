"""Owner switch for the opt-in Go background task consumer."""
import os
from pathlib import Path
import subprocess
import traceback
from uuid import UUID

from common import GO_TASKS_PROFILE, compose_command, compose_environment, read_env, update_env
from journal import now

# The task names the owner switch routes to the Go queue. Only names verified
# on the Go consumer; never taken from a request.
GO_TASK_NAMES = (
    'core.tasks.send_mail_async',
    'send_email_text',
    'send_email_template',
    'send_email_html',
)
MISSING_WORKER = 'Questa versione non include il servizio worker-go: aggiorna l’installazione prima di attivare le attività in background su Go.'
CHECK_FAILED = 'Impossibile verificare i servizi dell’installazione.'
RESTART_FAILED = 'Riavvio non completato dopo la modifica delle attività in background su Go.'
RESTART_RECOVERY = 'La configurazione è stata salvata. Controlla i log del server ed esegui ./bin/assozeta restart.'


def go_tasks_values(enabled):
    """Never unset ASSOZETA_GO_TASKS: an interrupted queue drains there."""
    if enabled:
        return {'ASSOZETA_GO_TASKS': '1', 'GO_TASK_QUEUE_TASKS': ','.join(GO_TASK_NAMES)}
    return {'GO_TASK_QUEUE_TASKS': ''}


def release_has_go_worker(root, env_file):
    services = subprocess.check_output(
        compose_command(root, env_file) + ['--profile', GO_TASKS_PROFILE, 'config', '--services'],
        env=compose_environment(root, env_file), text=True, timeout=30)
    return 'worker-go' in services.splitlines()


def validate_go_tasks_request(value, env_file):
    if not isinstance(value, dict) or set(value) != {'request_id', 'actor_id', 'enabled'}:
        raise ValueError('Invalid go tasks request fields.')
    UUID(value['request_id'])
    UUID(value['actor_id'])
    if type(value['enabled']) is not bool:
        raise ValueError('Invalid go tasks switch.')
    version = read_env(env_file).get('ASSOZETA_VERSION', 'unstable')
    return {**value, 'kind': 'go_tasks', 'release_id': None, 'tag': version, 'source_version': version}


def apply_go_tasks(root, env_file, journal, operation):
    """Runs in the runner's worker thread; the CLI restart leaves it alive."""
    root, env_file = Path(root), Path(env_file)
    operation_id = operation['id']
    log = root / '.updater/logs' / f'{UUID(operation_id)}.log'
    journal.update(operation_id, status='running', stage='checking')
    if operation['enabled']:
        try:
            available = release_has_go_worker(root, env_file)
        except Exception:
            journal.update(operation_id, status='failed', stage='failed', error=CHECK_FAILED, recovery=None)
            return
        if not available:
            journal.update(operation_id, status='failed', stage='failed', error=MISSING_WORKER, recovery=None)
            return
    try:
        update_env(env_file, go_tasks_values(operation['enabled']))
        journal.update(operation_id, stage='restarting')
        log.parent.mkdir(parents=True, exist_ok=True)
        environment = {**os.environ, 'ASSOZETA_INSTALL_ROOT': str(root), 'ASSOZETA_ENV_FILE': str(env_file)}
        with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'w') as output:
            subprocess.run([str(root / 'bin/assozeta'), 'restart'], env=environment, stdout=output,
                           stderr=subprocess.STDOUT, check=True, timeout=900)
        journal.update(operation_id, status='succeeded', stage='completed', verified_at=now())
    except Exception:
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'w') as output:
                traceback.print_exc(file=output)
        except OSError:
            # A log permission/disk error must not strand the journal in running.
            pass
        journal.update(operation_id, status='failed', stage='failed',
                       error=RESTART_FAILED, recovery=RESTART_RECOVERY)
