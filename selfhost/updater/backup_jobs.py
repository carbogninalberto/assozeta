"""Owner backup actions run by the update runner: create, keep, delete and the schedule.

The lifecycle CLI still takes every backup, so archives, rotation and the
space rules stay in one place. Only archive names of the standard directory
are accepted and links are never followed.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
from uuid import uuid4

from backups import ARCHIVE, inventory
from common import atomic_write, read_env, update_env
from journal import Conflict, now

SCHEDULE_FILE = '.updater/backup-schedule.json'
DEFAULT_SCHEDULE = {'enabled': False, 'frequency': 'daily', 'time': '03:00', 'weekday': 6}
DEFAULT_TIMEZONE = 'Europe/Rome'
# The scheduler has no user; this fixed actor marks its operations.
SCHEDULER_ACTOR = '00000000-0000-4000-8000-000000000000'
MAX_KEEP = 30
TIME = re.compile(r'([01]\d|2[0-3]):([0-5]\d)')


class BackupRejected(Exception):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def timezone_of(env_file):
    """The installation's local time, for "every day at 03:00"."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(read_env(env_file).get('ASSOZETA_TIMEZONE') or DEFAULT_TIMEZONE)
    except Exception:
        return timezone.utc


@contextmanager
def _lock(root):
    path = Path(root) / '.updater' / 'backup-schedule.lock'
    path.parent.mkdir(mode=0o700, exist_ok=True)
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def load_schedule(root):
    try:
        value = json.loads((Path(root) / SCHEDULE_FILE).read_text())
        if not isinstance(value, dict):
            raise ValueError
    except (OSError, ValueError):
        value = {}
    return {**DEFAULT_SCHEDULE, **value}


def save_schedule(root, value):
    atomic_write(Path(root) / SCHEDULE_FILE, json.dumps(value, indent=2) + '\n')


def validate_schedule(body):
    if not isinstance(body, dict) or not {'enabled', 'frequency', 'time', 'keep'} <= set(body) or set(body) - {'enabled', 'frequency', 'time', 'weekday', 'keep'}:
        raise ValueError('Invalid schedule fields.')
    if not isinstance(body['enabled'], bool) or body['frequency'] not in ('daily', 'weekly'):
        raise ValueError('Invalid schedule.')
    if not isinstance(body['time'], str) or not TIME.fullmatch(body['time']):
        raise ValueError('Invalid time.')
    weekday = body.get('weekday', DEFAULT_SCHEDULE['weekday'])
    keep = body['keep']
    for number, low, high in ((weekday, 0, 6), (keep, 1, MAX_KEEP)):
        if isinstance(number, bool) or not isinstance(number, int) or not low <= number <= high:
            raise ValueError('Invalid number.')
    return {'enabled': body['enabled'], 'frequency': body['frequency'], 'time': body['time'], 'weekday': weekday}, keep


def next_run(schedule, after, zone):
    """First scheduled time strictly after `after` (aware), or None when disabled."""
    if not schedule.get('enabled'):
        return None
    hour, minute = (int(part) for part in schedule['time'].split(':'))
    local = after.astimezone(zone)
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule['frequency'] == 'weekly':
        candidate += timedelta(days=(schedule['weekday'] - candidate.weekday()) % 7)
    step = timedelta(days=7 if schedule['frequency'] == 'weekly' else 1)
    while candidate <= local:
        candidate += step
    # Rebuild from the wall-clock time so daylight saving changes keep 03:00.
    candidate = datetime(candidate.year, candidate.month, candidate.day, hour, minute, tzinfo=zone)
    return candidate.astimezone(timezone.utc)


def _parse(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def schedule_payload(root, env_file):
    schedule = load_schedule(root)
    zone = timezone_of(env_file)
    anchor = _parse(schedule.get('anchor')) or datetime.now(timezone.utc)
    upcoming = next_run(schedule, anchor, zone)
    return {
        'enabled': schedule['enabled'], 'frequency': schedule['frequency'], 'time': schedule['time'],
        'weekday': schedule['weekday'], 'timezone': getattr(zone, 'key', 'UTC'),
        'next_run': upcoming.isoformat() if upcoming else None,
        'last_run': schedule.get('last_run'),
    }


def update_schedule(root, env_file, body):
    value, keep = validate_schedule(body)
    with _lock(root):
        current = load_schedule(root)
        changed = any(current.get(key) != value[key] for key in ('enabled', 'frequency', 'time', 'weekday'))
        updated = {**current, **value}
        # Count from now: enabling or moving the time never triggers a backlog.
        if changed:
            updated['anchor'] = now()
        save_schedule(root, updated)
        update_env(env_file, {'ASSOZETA_BACKUP_KEEP': str(keep)})
    return schedule_payload(root, env_file)


def due_backup(root, env_file, current=None):
    """The scheduled time that has passed and not yet been attempted, if any."""
    schedule = load_schedule(root)
    current = current or datetime.now(timezone.utc)
    anchor = _parse(schedule.get('anchor'))
    if not schedule['enabled'] or anchor is None:
        return None
    upcoming = next_run(schedule, anchor, timezone_of(env_file))
    return upcoming if upcoming and upcoming <= current else None


def schedule_tick(root, env_file, journal, current=None):
    """Queue at most one scheduled backup. After downtime it runs once, never a burst."""
    current = current or datetime.now(timezone.utc)
    with _lock(root):
        if not due_backup(root, env_file, current):
            return None
        records = journal.records()
        if any(item['status'] in ('queued', 'running') for item in records):
            return None  # Retried on a later tick, once the other operation ends.
        schedule = load_schedule(root)
        version = read_env(env_file).get('ASSOZETA_VERSION', '')
        request = {'kind': 'backup', 'origin': 'scheduled', 'request_id': str(uuid4()), 'actor_id': SCHEDULER_ACTOR,
                   'release_id': None, 'tag': version, 'source_version': version}
        schedule['anchor'] = current.isoformat()
        try:
            operation = journal.create(request)
        except Conflict as exc:
            schedule['last_run'] = {'at': current.isoformat(), 'status': 'failed', 'error': f'Backup automatico non eseguito: {exc}'}
            save_schedule(root, schedule)
            return None
        save_schedule(root, schedule)
        return operation


def create_backup(root, env_file, journal, body):
    if not isinstance(body, dict) or set(body) != {'request_id', 'actor_id'}:
        raise ValueError('Invalid backup request fields.')
    from uuid import UUID
    UUID(body['request_id'])
    UUID(body['actor_id'])
    if _busy(journal):
        raise BackupRejected('C’è già un’operazione in corso. Riprova quando è finita.')
    version = read_env(env_file).get('ASSOZETA_VERSION', '')
    return journal.create({**body, 'kind': 'backup', 'origin': 'manual', 'release_id': None, 'tag': version, 'source_version': version})


def _archive_path(root, name):
    if not isinstance(name, str) or not ARCHIVE.fullmatch(name):
        raise BackupRejected('Backup non trovato.', 404)
    directory = Path(root) / 'backups'
    path = directory / name
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise BackupRejected('Backup non trovato.', 404)
    return path


def _entry(root, env_file, name):
    return next((item for item in inventory(root, env_file)['archives'] if item['name'] == name), None)


def _busy(journal):
    return any(item['status'] in ('queued', 'running') for item in journal.records())


def set_keep(root, env_file, journal, name, body):
    if not isinstance(body, dict) or set(body) != {'keep'} or not isinstance(body['keep'], bool):
        raise ValueError('Invalid keep request.')
    path = _archive_path(root, name)
    if _busy(journal):
        raise BackupRejected('Attendi la fine dell’operazione in corso.')
    meta = Path(str(path) + '.meta')
    lines = [line for line in (meta.read_text().splitlines() if meta.is_file() and not meta.is_symlink() else []) if not line.startswith('keep=')]
    if body['keep']:
        lines.append('keep=true')
    atomic_write(meta, ''.join(f'{line}\n' for line in lines))
    return _entry(root, env_file, name)


def delete_archive(root, env_file, journal, name, body):
    if not isinstance(body, dict) or set(body) - {'confirm'} or not isinstance(body.get('confirm', False), bool):
        raise ValueError('Invalid delete request.')
    path = _archive_path(root, name)
    if _busy(journal):
        raise BackupRejected('Attendi la fine dell’operazione in corso.')
    entry = _entry(root, env_file, name)
    if entry is None:
        raise BackupRejected('Backup non trovato.', 404)
    if entry['pending_upgrade']:
        raise BackupRejected('Questo backup serve all’aggiornamento in sospeso e non può essere eliminato.')
    if (entry['last_upgrade'] or entry['keep']) and not body.get('confirm'):
        raise BackupRejected('Conferma l’eliminazione di un backup protetto.')
    path.unlink()
    meta = Path(str(path) + '.meta')
    if meta.is_file() and not meta.is_symlink():
        meta.unlink()
    return {'deleted': name}


def run_backup(root, env_file, journal, operation):
    """Run the installed lifecycle CLI and mirror its stages in the journal."""
    root, env_file = Path(root).resolve(), Path(env_file).resolve()
    operation_id = operation['id']
    scheduled = operation.get('origin') == 'scheduled'
    journal.update(operation_id, status='running', stage='checking')
    log = root / '.updater' / 'logs' / f'{operation_id}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [str(root / 'bin/assozeta'), 'backup'] + (['--scheduled'] if scheduled else [])
    environment = {**os.environ, 'ASSOZETA_ENV_FILE': str(env_file), 'ASSOZETA_BACKUP_STAGES': '1',
                   'ASSOZETA_OPERATION_ID': operation_id}
    error = None
    try:
        with os.fdopen(os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as output:
            with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                  env=environment, cwd=str(root)) as process:
                for line in process.stdout:
                    output.write(line)
                    output.flush()
                    if line.startswith('@@ASSOZETA_STAGE '):
                        stage = line.split(' ', 1)[1].strip()
                        if stage in ('backup', 'restarting'):
                            journal.update(operation_id, stage=stage)
                    elif line.startswith('@@ASSOZETA_ERROR '):
                        error = line.split(' ', 1)[1].strip()
                code = process.wait()
    except OSError:
        code = 1
    finished = now()
    if code == 0:
        journal.update(operation_id, status='succeeded', stage='completed', verified_at=finished)
        result = {'at': finished, 'status': 'succeeded', 'error': None}
    else:
        message = error or 'Backup non riuscito. Controlla il registro sul server.'
        journal.update(operation_id, status='failed', stage='failed', error=message,
                       recovery='Se l’app non torna disponibile, avviala dal server con ./bin/assozeta start.')
        result = {'at': finished, 'status': 'failed', 'error': message}
    if scheduled:
        with _lock(root):
            schedule = load_schedule(root)
            schedule['last_run'] = {**result, 'operation': operation_id}
            save_schedule(root, schedule)
