"""Read-only inventory of the server backups and their disk, for the owner page.

Mirrors the archive names, sidecar metadata and rotation rules of
backup_prune in selfhost/bin/assozeta. Nothing here creates, changes or
deletes a backup.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import stat

from common import read_env

ARCHIVE = re.compile(r'assozeta-(\d{8}T\d{6}Z)(-\d+)?\.tar\.gz')
ORIGINS = {'manual', 'scheduled', 'pre-upgrade', 'pre-restore'}
DEFAULT_KEEP = 3
DEFAULT_MARGIN_MB = 1024
MAX_ARCHIVES = 1000


class BackupNotFound(Exception):
    pass


def _meta(path):
    values = {}
    try:
        with open(str(path) + '.meta', encoding='utf-8') as stream:
            for line in stream.read(4096).splitlines():
                key, separator, value = line.partition('=')
                if separator and key not in values:
                    values[key] = value
    except (OSError, UnicodeDecodeError):
        pass
    return values


def _timestamp(value):
    try:
        return datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _settings(env_file):
    try:
        values = read_env(env_file)
    except OSError:
        values = {}
    keep = values.get('ASSOZETA_BACKUP_KEEP', '') or str(DEFAULT_KEEP)
    margin = values.get('ASSOZETA_BACKUP_SPACE_MARGIN_MB', '') or str(DEFAULT_MARGIN_MB)
    return (int(keep) if keep.isdigit() else None,
            int(margin) if margin.isdigit() and int(margin) > 0 else DEFAULT_MARGIN_MB)


def _pending_archive(root):
    try:
        return (Path(root) / '.updater/pending-distribution/transaction.json').read_text()
    except OSError:
        return ''


def inventory(root, env_file):
    root = Path(root)
    directory = root / 'backups'
    if directory.is_symlink():
        raise OSError('The backup directory must not be a symbolic link')
    pending = _pending_archive(root)
    archives = []
    if directory.is_dir():
        for index, entry in enumerate(sorted(os.scandir(directory), key=lambda item: item.name, reverse=True)):
            if index >= MAX_ARCHIVES * 2:
                break
            match = ARCHIVE.fullmatch(entry.name)
            if not match or not entry.is_file(follow_symlinks=False):
                continue
            meta = _meta(entry.path)
            origin = meta.get('origin')
            archives.append({
                'name': entry.name,
                'size': entry.stat(follow_symlinks=False).st_size,
                'created_at': _timestamp(meta.get('created_at') or match.group(1)),
                'version': meta.get('version') or None,
                'origin': origin if origin in ORIGINS else 'unknown',
                'keep': meta.get('keep') == 'true',
                'upgrade_completed': meta.get('upgrade') == 'completed',
                'pending_upgrade': bool(pending) and entry.name in pending,
                'location': 'local',
            })
            if len(archives) >= MAX_ARCHIVES:
                break
    # Rotation protects only the newest archive of a successful upgrade.
    last_upgrade = next((index for index, item in enumerate(archives) if item['upgrade_completed']), None)
    for index, item in enumerate(archives):
        del item['upgrade_completed']
        item['last_upgrade'] = index == last_upgrade
    keep, margin_mb = _settings(env_file)
    usage = shutil.disk_usage(directory if directory.is_dir() else root)
    return {
        'archives': archives,
        'disk': {'total': usage.total, 'used': usage.used, 'free': usage.free,
                 'backups': sum(item['size'] for item in archives)},
        'retention': {'automatic': keep},
        'space_margin_mb': margin_mb,
    }


def open_archive(root, name):
    """Open one archive of the standard directory for reading, never a link."""
    if not isinstance(name, str) or not ARCHIVE.fullmatch(name):
        raise BackupNotFound()
    directory = Path(root) / 'backups'
    if directory.is_symlink():
        raise BackupNotFound()
    try:
        descriptor = os.open(directory / name, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except OSError as exc:
        raise BackupNotFound() from exc
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        os.close(descriptor)
        raise BackupNotFound()
    return os.fdopen(descriptor, 'rb'), info.st_size
