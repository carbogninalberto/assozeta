"""Encrypted off-site copies of the server backups on S3-compatible storage.

Every archive is streamed through `age -r <public key>` into a multipart
upload, next to a small unencrypted metadata object (date, version, size,
origin and key id; never data). The runner keeps only the public key: the
owner holds the recovery (private) key, which is shown once when generated
and is needed to bring a copy back. A recovery key sent for a fetch lives in
memory only, never on disk or in the journal.

Settings live in .updater/remote-backup.json (mode 0600, the trust level of
.env); upload results in .updater/remote-backup-state.json.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

from backups import ARCHIVE, ORIGINS, inventory
from common import atomic_write, read_env
from endpoint_guard import BLOCKED_MESSAGE, blocked_in, endpoint_problem, pin_client

CONFIG_FILE = '.updater/remote-backup.json'
STATE_FILE = '.updater/remote-backup-state.json'
DEFAULT_RETENTION = 3
MAX_RETENTION = 30
LISTING_TTL = 60
FAILURE_PAUSE = 6 * 3600
LEGACY_MIN_AGE = 3600
RETRY_DELAYS = (30, 120)
MAX_REMOTE = 200
CHUNK = 1024 * 1024
AGE = 'age'
AGE_KEYGEN = 'age-keygen'
SETTINGS = ('endpoint', 'region', 'bucket', 'prefix', 'access_key', 'secret_key', 'path_style', 'retention', 'enabled')
LOCATION = ('endpoint', 'region', 'bucket', 'prefix', 'path_style')
SECRET_KEY = re.compile(r'AGE-SECRET-KEY-1[0-9A-Z]{20,120}')
PUBLIC_KEY = re.compile(r'age1[0-9a-z]{20,120}')
BUCKET = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{1,61}[A-Za-z0-9]')
PREFIX = re.compile(r'[A-Za-z0-9._/-]{0,200}')
REGION = re.compile(r'[A-Za-z0-9-]{0,64}')
ENDPOINT = re.compile(r'https?://[^\s/?#]+(/[^\s?#]*)?')
ACCESS_KEY = re.compile(r'[\x21-\x7e]{1,128}')

_lock = threading.Lock()
_cache = {}
_identities = {}


class RemoteError(Exception):
    """A plain Italian message for the owner."""

    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


# Settings ------------------------------------------------------------------

def _read_json(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def load_config(root):
    value = _read_json(Path(root) / CONFIG_FILE)
    return {'endpoint': '', 'region': '', 'bucket': '', 'prefix': '', 'access_key': '', 'secret_key': '',
            'path_style': False, 'enabled': False, 'retention': DEFAULT_RETENTION, 'key': None,
            'previous_keys': [], 'pending_key': None, **value}


def save_config(root, value):
    atomic_write(Path(root) / CONFIG_FILE, json.dumps(value, indent=2) + '\n', mode=0o600)


def load_state(root):
    return {'last_upload': None, 'last_failure': None, 'failures': {}, **_read_json(Path(root) / STATE_FILE)}


def save_state(root, value):
    atomic_write(Path(root) / STATE_FILE, json.dumps(value, indent=2) + '\n', mode=0o600)


def configured(config):
    return bool(config.get('bucket') and config.get('access_key') and config.get('secret_key'))


def public_config(root):
    """Settings for the owner page. Secrets are never returned."""
    config = load_config(root)
    state = load_state(root)
    key = config.get('key')
    pending = config.get('pending_key')
    return {
        'configured': configured(config), 'enabled': bool(config['enabled']),
        **{name: config[name] for name in ('endpoint', 'region', 'bucket', 'prefix', 'access_key', 'path_style', 'retention')},
        'secret_configured': bool(config.get('secret_key')),
        'key': {'id': key['id'], 'created_at': key['created_at']} if key else None,
        'pending_key': {'id': pending['id']} if pending else None,
        'previous_keys': [item['id'] for item in config.get('previous_keys', [])],
        'last_upload': state['last_upload'], 'last_failure': state['last_failure'],
        'uploading': Uploader.current,
    }


def validate_settings(body):
    if not isinstance(body, dict) or set(body) - set(SETTINGS):
        raise ValueError('Invalid remote settings fields.')
    value = {}
    for name, item in body.items():
        if name in ('path_style', 'enabled'):
            if not isinstance(item, bool):
                raise ValueError(f'Invalid {name}.')
        elif name == 'retention':
            if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= MAX_RETENTION:
                raise ValueError('Invalid retention.')
        else:
            if not isinstance(item, str):
                raise ValueError(f'Invalid {name}.')
            item = item.strip()
            if name == 'prefix':
                item = item.strip('/')
                if not PREFIX.fullmatch(item) or '..' in item.split('/') or '//' in item:
                    raise ValueError('Invalid prefix.')
            pattern = {'endpoint': ENDPOINT, 'region': REGION, 'bucket': BUCKET, 'access_key': ACCESS_KEY}.get(name)
            if item and pattern and not pattern.fullmatch(item):
                raise ValueError(f'Invalid {name}.')
            if name == 'secret_key' and (len(item) > 256 or any(ord(char) < 33 or ord(char) > 126 for char in item)):
                raise ValueError('Invalid secret key.')
        value[name] = item
    return value


def update_config(root, body):
    value = validate_settings(body)
    with _lock:
        config = load_config(root)
        if not value.get('secret_key'):
            value.pop('secret_key', None)  # An empty field keeps the saved secret.
        moved = any(name in value and value[name] != config[name] for name in LOCATION)
        updated = {**config, **value}
        if updated['enabled'] and not configured(updated):
            raise RemoteError('Completa bucket e chiavi di accesso prima di attivare la copia remota.')
        if updated['enabled'] and not updated.get('key'):
            raise RemoteError('Genera e conferma la chiave di ripristino prima di attivare la copia remota.')
        save_config(root, updated)
    if moved:
        # A new bucket starts empty: upload again, and never free local space
        # because of a copy stored somewhere else.
        _forget_uploads(root)
        state = load_state(root)
        state.update(last_failure=None, failures={})
        save_state(root, state)
    invalidate(root)
    return public_config(root)


# Keys ------------------------------------------------------------------------

def key_id(public_key):
    return hashlib.sha256(public_key.encode()).hexdigest()[:8]


def _check(public_key, tail):
    return hashlib.sha256(f'{public_key}:{tail.strip().upper()}'.encode()).hexdigest()


def generate_keypair():
    """(public, private) from age-keygen; the private key is never stored."""
    result = subprocess.run([AGE_KEYGEN], capture_output=True, text=True, timeout=30)
    private = next((line.strip() for line in result.stdout.splitlines() if line.startswith('AGE-SECRET-KEY-')), '')
    public = next((match.group(0) for match in PUBLIC_KEY.finditer(result.stdout + result.stderr)), '')
    if result.returncode != 0 or not SECRET_KEY.fullmatch(private) or not public:
        raise RemoteError('Generazione della chiave non riuscita.', 503)
    return public, private


def public_of(identity):
    """The public key of a recovery key, computed in memory."""
    result = subprocess.run([AGE_KEYGEN, '-y'], input=identity + '\n', capture_output=True, text=True, timeout=30)
    match = PUBLIC_KEY.search(result.stdout)
    if result.returncode != 0 or not match:
        raise RemoteError('Chiave di ripristino non valida.', 400)
    return match.group(0)


def parse_identity(value):
    """Accept the bare key or the whole key file the owner downloaded."""
    if not isinstance(value, str) or len(value) > 4096:
        raise RemoteError('Chiave di ripristino non valida.', 400)
    for line in value.splitlines() or [value]:
        line = line.strip().upper()
        if SECRET_KEY.fullmatch(line):
            return line
    raise RemoteError('Chiave di ripristino non valida.', 400)


def create_key(root, body):
    if body != {}:
        raise ValueError('Invalid key request.')
    public, private = generate_keypair()
    pending = {'id': key_id(public), 'public_key': public, 'created_at': now(), 'check': _check(public, private[-6:])}
    with _lock:
        config = load_config(root)
        config['pending_key'] = pending
        save_config(root, config)
    return {'key_id': pending['id'], 'recovery_key': private}


def confirm_key(root, body):
    if not isinstance(body, dict) or set(body) != {'key_id', 'tail'} or not all(isinstance(body[name], str) for name in body):
        raise ValueError('Invalid key confirmation.')
    with _lock:
        config = load_config(root)
        pending = config.get('pending_key')
        if not pending or pending['id'] != body['key_id']:
            raise RemoteError('Chiave non trovata: generane una nuova.', 404)
        if len(body['tail'].strip()) != 6 or _check(pending['public_key'], body['tail']) != pending['check']:
            raise RemoteError('I caratteri non corrispondono alla chiave.', 400)
        first = config.get('key') is None
        if config.get('key'):
            config['previous_keys'] = [*config.get('previous_keys', []), {**config['key'], 'retired_at': now()}][-20:]
        config['key'] = {name: pending[name] for name in ('id', 'public_key', 'created_at')}
        config['pending_key'] = None
        # The first confirmed key completes the setup: start copying.
        if first and configured(config):
            config['enabled'] = True
        save_config(root, config)
    return public_config(root)


# S3 --------------------------------------------------------------------------

def settings_from(config, override=None):
    merged = {**config, **(override or {})}
    if override is not None and not override.get('secret_key'):
        merged['secret_key'] = config.get('secret_key', '')
    return merged


def make_client(config, quick=False):
    """An S3 client for the saved (or proposed) settings.

    The endpoint is checked again here, so settings saved earlier follow the
    current address rules, and every connection goes to a checked address.
    """
    problem = endpoint_problem(config.get('endpoint') or '')
    if problem:
        raise RemoteError(problem, 400)
    import boto3
    from botocore.config import Config
    options = Config(connect_timeout=3 if quick else 10, read_timeout=5 if quick else 60,
                     retries={'max_attempts': 1 if quick else 3},
                     s3={'addressing_style': 'path' if config.get('path_style') else 'virtual'})
    return pin_client(boto3.client('s3', endpoint_url=config.get('endpoint') or None, region_name=config.get('region') or None,
                                   aws_access_key_id=config['access_key'], aws_secret_access_key=config['secret_key'],
                                   config=options))


def object_key(config, name):
    prefix = (config.get('prefix') or '').strip('/')
    return f'{prefix}/{name}' if prefix else name


def plain_error(exc):
    """One Italian line for an S3 failure, without credentials or URLs."""
    if isinstance(exc, RemoteError):
        return str(exc)
    if blocked_in(exc):
        return BLOCKED_MESSAGE
    code = (getattr(exc, 'response', None) or {}).get('Error', {}).get('Code', '')
    if code in ('InvalidAccessKeyId', 'SignatureDoesNotMatch', 'AuthorizationHeaderMalformed', 'InvalidToken'):
        return 'Chiavi di accesso non valide.'
    if code in ('AccessDenied', 'AllAccessDisabled', '403'):
        return 'Accesso negato: controlla i permessi delle chiavi sul bucket.'
    if code in ('NoSuchBucket', '404'):
        return 'Bucket inesistente.'
    if code == 'PermanentRedirect':
        return 'Regione o endpoint non corretti.'
    name = type(exc).__name__
    if 'Endpoint' in name or 'Connect' in name or 'Timeout' in name or isinstance(exc, OSError):
        return 'Storage non raggiungibile: controlla endpoint e rete.'
    return 'Operazione sullo storage remoto non riuscita.'


def test_connection(root, body, client_factory=make_client):
    override = validate_settings(body) if body else None
    config = settings_from(load_config(root), override)
    if not configured(config):
        raise RemoteError('Completa bucket e chiavi di accesso.', 400)
    payload = uuid4().hex.encode()
    key = object_key(config, f'.assozeta-test-{uuid4().hex}')
    try:
        client = client_factory(config, quick=True)
        client.put_object(Bucket=config['bucket'], Key=key, Body=payload)
        try:
            read = client.get_object(Bucket=config['bucket'], Key=key)['Body'].read()
        finally:
            client.delete_object(Bucket=config['bucket'], Key=key)
        if read != payload:
            return {'ok': False, 'message': 'Il file di prova letto non corrisponde a quello scritto.'}
    except Exception as exc:
        return {'ok': False, 'message': plain_error(exc)}
    return {'ok': True, 'message': 'Connessione riuscita: scrittura, lettura ed eliminazione funzionano.'}


def _list(client, config):
    """Remote copies described by their metadata objects, newest first."""
    prefix = (config.get('prefix') or '').strip('/')
    names = []
    token = None
    while True:
        options = {'Bucket': config['bucket'], 'Prefix': f'{prefix}/' if prefix else ''}
        if token:
            options['ContinuationToken'] = token
        page = client.list_objects_v2(**options)
        for item in page.get('Contents', ()):
            name = item['Key'][len(options['Prefix']):]
            if name.endswith('.json') and ARCHIVE.fullmatch(name[:-5]):
                names.append(name[:-5])
        token = page.get('NextContinuationToken')
        if not page.get('IsTruncated') or not token:
            break
    entries = []
    for name in sorted(set(names), reverse=True)[:MAX_REMOTE]:
        try:
            meta = json.loads(client.get_object(Bucket=config['bucket'], Key=object_key(config, name + '.json'))['Body'].read(65536))
        except ValueError:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        size = meta.get('size')
        entries.append({'name': name, 'created_at': meta.get('created_at') if isinstance(meta.get('created_at'), str) else None,
                        'version': meta.get('version') if isinstance(meta.get('version'), str) else None,
                        'size': size if isinstance(size, int) and not isinstance(size, bool) else None,
                        'origin': meta.get('origin') if meta.get('origin') in ORIGINS else 'unknown',
                        'key_id': meta.get('key_id') if isinstance(meta.get('key_id'), str) else None})
    return entries


def _location(config):
    return tuple(config.get(name) for name in LOCATION) + (config.get('access_key'),)


def invalidate(root):
    with _lock:
        _cache.pop(str(Path(root).resolve()), None)


def listing(root, client_factory=make_client, wait=2.0, force=False):
    """Cached remote listing: (entries | None, error | None). Never raises.

    A stale listing is refreshed in the background; only the first one waits,
    briefly, so the backup page stays fast while the storage is slow or down.
    """
    config = load_config(root)
    if not configured(config):
        return None, None
    path = str(Path(root).resolve())
    with _lock:
        entry = _cache.get(path)
        if entry and entry['location'] != _location(config):
            entry = _cache[path] = None
        fresh = entry and entry.get('at') and time.monotonic() - entry['at'] < LISTING_TTL and not force
        if not fresh and not (entry and entry.get('thread') and entry['thread'].is_alive()):
            entry = _cache[path] = {**(entry or {}), 'location': _location(config)}
            thread = threading.Thread(target=_refresh, args=(path, config, client_factory, entry), daemon=True)
            entry['thread'] = thread
            thread.start()
    thread = entry.get('thread')
    if (entry.get('at') is None or force) and thread is not None:
        thread.join(wait)
    return entry.get('entries'), entry.get('error')


def _refresh(path, config, client_factory, entry):
    try:
        entries, error = _list(client_factory(config), config), None
    except Exception as exc:
        entries, error = entry.get('entries'), plain_error(exc)
    with _lock:
        entry.update(entries=entries, error=error, at=time.monotonic())


def merge_inventory(root, value, client_factory=make_client):
    """Add the remote side to the local inventory: location, remote-only rows, status."""
    config = load_config(root)
    state = load_state(root)
    summary = {'configured': configured(config), 'enabled': bool(config['enabled']),
               'key_id': (config.get('key') or {}).get('id'), 'last_upload': state['last_upload'],
               'last_failure': state['last_failure'], 'uploading': Uploader.current, 'status': 'off', 'error': None}
    value['remote'] = summary
    if not summary['configured']:
        return value
    entries, error = listing(root, client_factory)
    summary.update(status='error' if error else 'ok' if entries is not None else 'unknown', error=error)
    remote = {item['name']: item for item in entries or ()}
    for archive in value['archives']:
        copy = remote.pop(archive['name'], None)
        meta = _meta(Path(root) / 'backups' / archive['name'])
        if copy:
            archive.update(location='both', key_id=copy['key_id'])
        elif entries is None and meta.get('remote') == 'uploaded':
            archive.update(location='both', key_id=meta.get('remote_key'))
    for copy in remote.values():
        value['archives'].append({
            'name': copy['name'], 'size': copy['size'], 'created_at': copy['created_at'], 'version': copy['version'],
            'origin': copy['origin'], 'keep': False, 'pending_upgrade': False, 'last_upgrade': False,
            'location': 'remote', 'key_id': copy['key_id']})
    value['archives'].sort(key=lambda item: item['created_at'] or '', reverse=True)
    return value


# Local metadata ------------------------------------------------------------------

def _meta(path):
    values = {}
    try:
        meta = Path(str(path) + '.meta')
        if not meta.is_symlink():
            for line in meta.read_text().splitlines():
                key, separator, item = line.partition('=')
                if separator and key not in values:
                    values[key] = item
    except (OSError, UnicodeDecodeError):
        pass
    return values


def _set_meta(path, updates):
    """Rewrite selected keys of an archive sidecar, if the archive still exists."""
    path = Path(path)
    meta = Path(str(path) + '.meta')
    if path.is_symlink() or not path.is_file() or meta.is_symlink():
        return
    try:
        lines = meta.read_text().splitlines() if meta.is_file() else []
    except OSError:
        return
    lines = [line for line in lines if line.partition('=')[0] not in updates]
    lines += [f'{key}={item}' for key, item in updates.items() if item is not None]
    atomic_write(meta, ''.join(f'{line}\n' for line in lines))


def _forget_uploads(root):
    directory = Path(root) / 'backups'
    if not directory.is_dir() or directory.is_symlink():
        return
    for path in directory.iterdir():
        if ARCHIVE.fullmatch(path.name) and _meta(path).get('remote'):
            _set_meta(path, {'remote': None, 'remote_key': None})


# Upload ------------------------------------------------------------------------

def _transfer_config():
    try:
        from boto3.s3.transfer import TransferConfig
    except ImportError:
        return None
    # 64 MiB parts allow archives up to 640 GB within the 10,000-part limit.
    return TransferConfig(multipart_threshold=64 * CHUNK, multipart_chunksize=64 * CHUNK, max_concurrency=2)


def upload_archive(client, config, path, metadata):
    """Stream archive -> age -> multipart upload, then write the metadata object."""
    key = object_key(config, path.name)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as source:
        process = subprocess.Popen([AGE, '-r', config['key']['public_key']], stdin=source,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            extra = _transfer_config()
            options = {'Config': extra} if extra is not None else {}
            client.upload_fileobj(process.stdout, config['bucket'], key + '.age', **options)
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            process.stdout.close()
        if process.wait() != 0:
            client.delete_object(Bucket=config['bucket'], Key=key + '.age')
            raise RemoteError('Cifratura del backup non riuscita.')
    client.put_object(Bucket=config['bucket'], Key=key + '.json', ContentType='application/json',
                      Body=json.dumps(metadata, indent=2).encode())


class Uploader:
    """Background uploads after backups. Never blocks the runner loop."""

    current = None
    _thread = None

    def __init__(self, root, env_file, client_factory=make_client, sleep=time.sleep, delays=RETRY_DELAYS):
        self.root = Path(root)
        self.env_file = env_file
        self.client_factory = client_factory
        self.sleep = sleep
        self.delays = delays

    def kick(self):
        if not load_config(self.root)['enabled']:
            return False
        with _lock:
            if Uploader._thread and Uploader._thread.is_alive():
                return False
            Uploader._thread = threading.Thread(target=self._safe_run, daemon=True)
            Uploader._thread.start()
        return True

    def _safe_run(self):
        try:
            self.run_once()
        except Exception:
            import traceback
            traceback.print_exc()

    def _local(self):
        archives = inventory(self.root, self.env_file)['archives']
        state = load_state(self.root)
        result = []
        for archive in archives:
            path = self.root / 'backups' / archive['name']
            meta = _meta(path)
            if not meta:
                # Without a sidecar the archive may still be written: legacy ones only.
                try:
                    if time.time() - path.lstat().st_mtime < LEGACY_MIN_AGE:
                        continue
                except OSError:
                    continue
            failure = state['failures'].get(archive['name'])
            paused = failure and time.time() - failure.get('time', 0) < FAILURE_PAUSE
            result.append({**archive, 'meta': meta, 'paused': bool(paused)})
        return result

    def run_once(self):
        config = load_config(self.root)
        if not config['enabled'] or not configured(config) or not config.get('key'):
            return {'uploaded': [], 'deleted': []}
        local = self._local()
        waiting = [item for item in local if item['meta'].get('remote') != 'uploaded' and not item['paused']]
        if not waiting:
            return {'uploaded': [], 'deleted': []}
        try:
            client = self.client_factory(config)
            remote = _list(client, config)
        except Exception as exc:
            self._failed(waiting[0]['name'], plain_error(exc))
            return {'uploaded': [], 'deleted': []}
        present = {item['name'] for item in remote}
        for item in local:
            if item['name'] in present and item['meta'].get('remote') != 'uploaded':
                _set_meta(self.root / 'backups' / item['name'], {'remote': 'uploaded'})
        # Upload only what remote retention would keep: kept ones and the newest N.
        ranked = sorted([*({'name': item['name'], 'created_at': item['created_at']} for item in remote),
                         *({'name': item['name'], 'created_at': item['created_at']} for item in waiting if item['name'] not in present)],
                        key=lambda item: item['created_at'] or '', reverse=True)
        newest = {item['name'] for item in ranked[:config['retention']]}
        uploaded = []
        for item in waiting:
            if item['name'] in present or not (item['keep'] or item['name'] in newest):
                continue
            if self._upload(client, config, item):
                uploaded.append(item['name'])
        deleted = self.apply_retention(client, config) if uploaded else []
        invalidate(self.root)
        return {'uploaded': uploaded, 'deleted': deleted}

    def _upload(self, client, config, item):
        path = self.root / 'backups' / item['name']
        metadata = {'format': 1, 'name': item['name'], 'created_at': item['created_at'], 'version': item['version'],
                    'size': item['size'], 'origin': item['origin'], 'key_id': config['key']['id'], 'uploaded_at': now()}
        error = None
        for attempt, delay in enumerate((*self.delays, None)):
            Uploader.current = item['name']
            try:
                upload_archive(client, config, path, metadata)
                break
            except FileNotFoundError:
                Uploader.current = None
                return False  # Deleted meanwhile (rotation or the owner): nothing to copy.
            except Exception as exc:
                error = plain_error(exc)
                if delay is not None:
                    self.sleep(delay)
        else:
            Uploader.current = None
            self._failed(item['name'], error)
            return False
        Uploader.current = None
        _set_meta(path, {'remote': 'uploaded', 'remote_key': config['key']['id']})
        with _lock:
            state = load_state(self.root)
            state['last_upload'] = {'at': now(), 'name': item['name']}
            state['failures'].pop(item['name'], None)
            if not state['failures']:
                state['last_failure'] = None
            save_state(self.root, state)
        return True

    def _failed(self, name, error):
        with _lock:
            state = load_state(self.root)
            state['failures'][name] = {'time': time.time(), 'error': error}
            state['last_failure'] = {'at': now(), 'name': name, 'error': error}
            save_state(self.root, state)

    def apply_retention(self, client, config):
        """Keep the newest N remote copies and every copy of a kept archive."""
        remote = sorted(_list(client, config), key=lambda item: item['created_at'] or '', reverse=True)
        deleted = []
        for item in remote[config['retention']:]:
            path = self.root / 'backups' / item['name']
            if _meta(path).get('keep') == 'true':
                continue
            for suffix in ('.age', '.json'):
                client.delete_object(Bucket=config['bucket'], Key=object_key(config, item['name'] + suffix))
            # The local copy is no longer safe off-site: never free space because of it.
            _set_meta(path, {'remote': None, 'remote_key': None})
            deleted.append(item['name'])
        return deleted


# Fetch ---------------------------------------------------------------------------

def download_archive(client, config, name, identity, directory, expected_size=None):
    """Download and decrypt one copy into `directory`; the key stays in memory."""
    directory = Path(directory)
    target = directory / name
    partial = directory / f'.fetch-{name}.{os.getpid()}.partial'
    body = client.get_object(Bucket=config['bucket'], Key=object_key(config, name + '.age'))['Body']
    read_end, write_end = os.pipe()
    try:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            # The identity reaches age through a pipe, never through a file.
            process = subprocess.Popen([AGE, '-d', '-i', f'/dev/fd/{read_end}'], stdin=subprocess.PIPE,
                                       stdout=output, stderr=subprocess.DEVNULL, pass_fds=(read_end,))
            os.close(read_end)
            read_end = None
            os.write(write_end, (identity + '\n').encode())
            os.close(write_end)
            write_end = None
            try:
                while True:
                    chunk = body.read(CHUNK)
                    if not chunk:
                        break
                    process.stdin.write(chunk)
            except BrokenPipeError:
                pass  # age stopped early (wrong key): its exit code says why.
            except BaseException:
                process.kill()
                process.wait()
                raise
            finally:
                try:
                    process.stdin.close()
                except BrokenPipeError:
                    pass
            if process.wait() != 0:
                raise RemoteError('Decifratura non riuscita: la chiave non corrisponde o la copia è danneggiata.', 400)
            output.flush()
            os.fsync(output.fileno())
        size = partial.stat().st_size
        if expected_size is not None and size != expected_size:
            raise RemoteError('La copia scaricata è incompleta.')
        if target.exists() or target.is_symlink():
            raise RemoteError('Il backup è già presente sul server.')
        os.replace(partial, target)
        return target
    finally:
        for end in (read_end, write_end):
            if end is not None:
                os.close(end)
        if partial.exists():
            partial.unlink()


def _compact(created_at):
    try:
        return datetime.fromisoformat(created_at).astimezone(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    except (TypeError, ValueError):
        return None


def write_fetched_meta(path, entry, key):
    values = {'origin': entry.get('origin') if entry.get('origin') in ORIGINS else None,
              'created_at': _compact(entry.get('created_at')), 'version': entry.get('version'),
              'remote': 'uploaded', 'remote_key': key}
    atomic_write(Path(str(path) + '.meta'), ''.join(f'{k}={v}\n' for k, v in values.items() if v))


def _free_bytes(directory):
    import shutil
    return shutil.disk_usage(directory).free


def start_fetch(root, env_file, journal, body, client_factory=make_client):
    """Queue a fetch of one remote-only copy; check the key before queuing."""
    if not isinstance(body, dict) or set(body) != {'name', 'recovery_key', 'request_id', 'actor_id'}:
        raise ValueError('Invalid fetch request fields.')
    UUID(body['request_id'])
    UUID(body['actor_id'])
    name = body['name']
    if not isinstance(name, str) or not ARCHIVE.fullmatch(name):
        raise RemoteError('Copia remota non trovata.', 404)
    identity = parse_identity(body['recovery_key'])
    config = load_config(root)
    if not configured(config):
        raise RemoteError('Configura prima la copia remota.')
    directory = Path(root) / 'backups'
    if directory.is_symlink():
        raise OSError('The backup directory must not be a symbolic link')
    if (directory / name).exists() or (directory / name).is_symlink():
        raise RemoteError('Il backup è già presente sul server.')
    if any(item['status'] in ('queued', 'running') for item in journal.records()):
        raise RemoteError('C’è già un’operazione in corso. Riprova quando è finita.')
    entries, error = listing(root, client_factory, wait=8.0)
    entry = next((item for item in entries or () if item['name'] == name), None)
    if entry is None:
        raise RemoteError(error or 'Copia remota non trovata.', 503 if error else 404)
    if entry['key_id'] and key_id(public_of(identity)) != entry['key_id']:
        raise RemoteError(f'La chiave non corrisponde: questa copia richiede la chiave {entry["key_id"]}.', 400)
    directory.mkdir(mode=0o700, exist_ok=True)
    margin = 1024 * CHUNK
    if entry['size'] and _free_bytes(directory) < entry['size'] + margin:
        raise RemoteError('Spazio su disco insufficiente per recuperare la copia.')
    version = read_env(env_file).get('ASSOZETA_VERSION', '')
    operation = journal.create({'kind': 'fetch', 'name': name, 'request_id': body['request_id'], 'actor_id': body['actor_id'],
                                'release_id': None, 'tag': version, 'source_version': version})
    with _lock:
        _identities[operation['id']] = identity
    return operation


def run_fetch(root, env_file, journal, operation, client_factory=make_client):
    """Journal operation `fetch`: download and decrypt into the backups directory."""
    with _lock:
        identity = _identities.pop(operation['id'], None)
    journal.update(operation['id'], status='running', stage='checking')
    try:
        if not identity:
            raise RemoteError('La chiave di ripristino non è più in memoria. Riprova.')
        config = load_config(root)
        entries, error = listing(root, client_factory, wait=30.0)
        entry = next((item for item in entries or () if item['name'] == operation['name']), None)
        if entry is None:
            raise RemoteError(error or 'Copia remota non trovata.')
        journal.update(operation['id'], stage='downloading')
        client = client_factory(config)
        path = download_archive(client, config, operation['name'], identity, Path(root) / 'backups', entry['size'])
        write_fetched_meta(path, entry, entry['key_id'])
    except Exception as exc:
        message = plain_error(exc)
        journal.update(operation['id'], status='failed', stage='failed', error=f'Recupero non riuscito: {message}')
        return
    finally:
        identity = None
        invalidate(root)
    journal.update(operation['id'], status='succeeded', stage='completed', verified_at=now())


# Command line (fresh-server restore) ---------------------------------------------------------

ENVIRONMENT = {'endpoint': 'ASSOZETA_S3_ENDPOINT', 'region': 'ASSOZETA_S3_REGION', 'bucket': 'ASSOZETA_S3_BUCKET',
               'prefix': 'ASSOZETA_S3_PREFIX', 'access_key': 'ASSOZETA_S3_ACCESS_KEY', 'secret_key': 'ASSOZETA_S3_SECRET_KEY'}


def settings_from_environment(root, environ=None):
    environ = os.environ if environ is None else environ
    config = load_config(root)
    for name, variable in ENVIRONMENT.items():
        if environ.get(variable):
            config[name] = environ[variable].strip()
    if environ.get('ASSOZETA_S3_PATH_STYLE'):
        config['path_style'] = environ['ASSOZETA_S3_PATH_STYLE'].strip().lower() in ('1', 'true', 'yes')
    config['prefix'] = (config.get('prefix') or '').strip('/')
    validate_settings({name: config[name] for name in ('endpoint', 'region', 'bucket', 'prefix', 'access_key', 'path_style')})
    if not configured(config):
        raise RemoteError('S3 settings are incomplete: set ASSOZETA_S3_BUCKET, ASSOZETA_S3_ACCESS_KEY and ASSOZETA_S3_SECRET_KEY.')
    return config


def main(argv, stdin=sys.stdin, client_factory=make_client):
    if len(argv) < 2 or argv[0] not in ('list', 'fetch') or (argv[0] == 'fetch' and len(argv) != 3):
        print('usage: remote_backups.py list ROOT | fetch ROOT NAME (recovery key on stdin)', file=sys.stderr)
        return 2
    root = Path(argv[1])
    try:
        config = settings_from_environment(root)
        client = client_factory(config)
        entries = _list(client, config)
        if argv[0] == 'list':
            for item in entries:
                print(f"{item['name']}  {item['created_at'] or '-'}  {item['size'] or '-'} bytes  key {item['key_id'] or '-'}")
            if not entries:
                print('no remote backups found', file=sys.stderr)
            return 0
        name = argv[2]
        entry = next((item for item in entries if item['name'] == name), None)
        if not ARCHIVE.fullmatch(name) or entry is None:
            raise RemoteError(f'remote backup not found: {name}')
        directory = root / 'backups'
        directory.mkdir(mode=0o700, exist_ok=True)
        if (directory / name).exists():
            print(directory / name)
            return 0
        identity = parse_identity(stdin.read(4096))
        if entry['key_id'] and key_id(public_of(identity)) != entry['key_id']:
            raise RemoteError(f'the recovery key does not match: this copy needs key {entry["key_id"]}')
        print(f'downloading and decrypting {name}', file=sys.stderr)
        path = download_archive(client, config, name, identity, directory, entry['size'])
        write_fetched_meta(path, entry, entry['key_id'])
        print(path)
        return 0
    except Exception as exc:
        print(f'error: {plain_error(exc)}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
