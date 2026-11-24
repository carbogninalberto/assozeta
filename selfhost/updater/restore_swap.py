"""Durable record for the host CLI's PostgreSQL and object-store swap."""
import json
import os
import re
from pathlib import Path

from common import read_env, write_json


def journal(root, env_file, command, *args):
    path = Path(root) / '.operations/restore-swap.json'
    values = read_env(env_file)
    installation = [values.get(key, '') for key in ('COMPOSE_PROJECT_NAME', 'DBNAME', 'AWS_STORAGE_BUCKET_NAME')]
    if command == 'begin':
        suffix, directory, running = args
        if path.exists():
            raise SystemExit('unfinished restore swap')
        entry = dict(installation=installation, phase='swapping', suffix=suffix, directory=directory, running=running)
    else:
        entry = json.loads(path.read_bytes())
    if (entry.get('installation') != installation or
            not re.fullmatch(r'[0-9]{14}_[0-9]+', entry['suffix']) or
            not re.fullmatch(r'\.restore-[a-zA-Z0-9]{6}', entry['directory']) or
            (Path(root) / entry['directory']).is_symlink() or
            entry['running'] not in ('true', 'false') or
            entry['phase'] not in ('swapping', 'committed', 'rolled-back')):
        raise SystemExit('restore swap journal does not match this installation')

    if entry['phase'] == 'swapping' and not (Path(root) / entry['directory'] / 'live-broker').is_dir():
        raise SystemExit('missing broker rollback snapshot')

    if command == 'read':
        print(entry['phase'], entry['suffix'], entry['directory'], entry['running'])
    elif command == 'clear':
        if entry['phase'] == 'swapping':
            raise SystemExit('cannot clear an unfinished swap')
        path.unlink()
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    elif command in ('begin', 'committed', 'rolled-back'):
        if command != 'begin':
            if entry['phase'] not in ('swapping', command):
                raise SystemExit('conflicting restore swap completion')
            entry['phase'] = command
        write_json(path, entry)
    else:
        raise SystemExit('unknown restore journal operation')
