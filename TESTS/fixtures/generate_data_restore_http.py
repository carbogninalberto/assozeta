"""Author the data-restore HTTP sequence (data-restore.md).

Pins BE/instance/restore/views.py (DataRestoreView, DataRestoreBackupsView,
DataRestoreDownloadView, DataRestoreActionView) with locking.py
(operation_lock, restore_pending), replacement.require_single,
execution.save_operation / cleanup / clean_prefix, progress.snapshot,
downloads.py (signed download tokens, stream_backup through the S3 storage)
and the restore_instance_data publish, in Django's order: identity, the
IsInstanceOwner permission, method dispatch, the operation lock, the row
lookup, require_single, the action rules, the stored receipt, the storage
cleanup and the Celery message.

The sequence runs on one seeded InstanceConfiguration (self-hosted, primary
association the shared owner's) and the DataRestore receipts below. Per case:
- 'single' hides every other SportAssociation row for the request (the
  shared fixture holds several; require_single needs exactly one);
- 'lock' holds the run's DATA_RESTORE_LOCK_PATH flock ('shared' or
  'exclusive') during the request;
- 'setup' overwrites receipt fields before the request (queryset.update);
- 'cache' stores progress snapshots (Django's cache.set, or raw bytes);
- 'objects' stores storage objects (keys under AWS_LOCATION) and
  'objects_after' lists the keys that must remain afterwards; the bucket is
  emptied after every case;
- 'effect' is the complete expected receipt state ('$now' marks an
  updated_at the request sets) and the Celery messages.
'$download-token:<op>' in a response is a token signing.loads accepts for
that receipt; '$token:<op>:<variant>' in a path is signed by the runner.
'$version' is the running version. Every value is a placeholder.
Run with ``python3 -I``.
"""
import base64
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('data_restore_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
ANON = {'detail': 'Authentication credentials were not provided.'}
DENIED = {'detail': 'Only the instance owner or an administrator can administer this installation.'}
LEAVE = {'detail': 'Esci dall’impersonificazione per accedere all’amministrazione.'}
NOT_FOUND = {'detail': 'No DataRestore matches the given query.'}
SINGLE = 'Il ripristino richiede una istanza self-hosted con una sola associazione.'
BUSY = 'Un’altra operazione sui dati è in corso. Riprova tra poco.'
PENDING = 'Un ripristino è già in corso.'
UPLOAD = 'Carica un file ZIP entro il limite indicato.'
NO_BACKUP = 'Backup di sicurezza non disponibile.'
MAX_UPLOAD = 65536
ROOT = '/instance/admin/data-restore'
BACKUPS = ROOT + '/backups'
OTHER_ASSOCIATION = 'd7000000-0000-4000-8000-00000000a5a5'
CREATED_UPDATED = '2026-04-01T08:00:00Z'
TASK = 'instance.restore.execute'

INSTANCE = {
    'id': 7304, 'domain': 'data-restore.example.test', 'name': 'Istanza di prova', 'abbreviation': 'IP',
    'primary_color': '#123456', 'support_email': 'supporto@example.test', 'logo_path': '', 'setup_provenance': 'fresh',
    'display_settings': {}, 'self_hosted': True, 'support_multiple_associations': False,
    'primary_association': 'own_association', 'google_client_id': '', 'apple_client_id': '', 'email_settings': {},
    'email_password_encrypted': '', 'email_revision': 0, 'diagnostic_results': {}, 'integration_settings': {},
}

PREVIEW = {
    'association': 'Associazione di prova', 'export_date': '2026-03-30T10:00:00', 'skipped_records': {'BillingPlan': 1},
    'records': 42, 'files': 1, 'missing_media': 0, 'legacy_invoices': 0, 'reassigned_tutors': 1, 'tutor_tax_code_conflicts': 1,
    'tutor_reassignment_details': [{'associate_id': 'd7000000-0000-4000-8000-00000000c001', 'source_association_id': None,
                                    'name': 'Tutore Prova', 'original_tax_code': 'PRVTTR80A01H501U',
                                    'conflicting_associate_id': 'd7000000-0000-4000-8000-00000000c002'}],
    'missing_relations': 1,
    'missing_relation_details': [{'model': 'Payment', 'record_id': 'd7000000-0000-4000-8000-00000000c003',
                                  'field': 'course_id', 'target_model': 'Course',
                                  'target_id': 'd7000000-0000-4000-8000-00000000c004'}],
}


def op_id(number):
    return f'd7000000-0000-4000-8000-{number:012x}'


ops = {}


def receipt(key, number, *, owner='owner', association='own_association', state='review', stage=None, created,
            upload=True, backup=False, preview=None, error='', attempts=0, version='$version', sha='a', completed=None,
            media=None):
    identity = op_id(number)
    ops[key] = {
        'id': identity, 'owner': owner, 'association_id': IDS[association] if association in IDS else association,
        'state': state, 'stage': stage or {'review': 'validated'}.get(state, state), 'created_at': created,
        'updated_at': CREATED_UPDATED, 'completed_at': completed,
        'upload_path': f'restores/{identity}/upload/archive.zip' if upload else '', 'sha256': sha * 64, 'version': version,
        'preview': copy.deepcopy(PREVIEW if preview is None else preview),
        'backup_path': f'restores/{identity}/recovery/{op_id(number + 0x900)}.zip' if backup else '',
        'backup_sha256': ('b' * 63 + format(number % 16, 'x')) if backup else '', 'media': media or {}, 'error': error,
        'allow_missing_media': False, 'attempts': attempts,
    }


receipt('review', 0x101, created='2099-03-01T00:00:00Z',
        media={'Document:d7000000-0000-4000-8000-00000000c010': 'restores/' + op_id(0x101) + '/media/m1/scheda.pdf'})
receipt('media', 0x102, created='2099-02-01T00:00:00Z', preview={**PREVIEW, 'missing_media': 2})
receipt('oldversion', 0x103, created='2099-01-20T00:00:00Z', version='v0.0.1-fixture')
receipt('moved', 0x104, created='2099-01-10T00:00:00Z', association=OTHER_ASSOCIATION)
receipt('stale', 0x105, created='2020-06-01T00:00:00Z')
receipt('failed', 0x106, state='failed', created='2026-03-01T00:00:00Z', backup=True, upload=False, attempts=2,
        error='Ripristino non riuscito. I dati precedenti sono conservati. Verifica il backup e riprova.')
receipt('completed', 0x107, state='completed', created='2026-02-01T00:00:00Z', backup=True, upload=False, attempts=1,
        completed='2026-02-01T00:30:00Z', preview={'association': 'Solo riepilogo', 'records': 3})
receipt('cancelled', 0x108, state='cancelled', created='2026-01-15T00:00:00Z', upload=False, preview={})
receipt('receipt', 0x109, state='completed', created='2025-12-01T00:00:00Z', upload=False, attempts=1,
        completed='2025-12-01T00:30:00Z')
receipt('foreign_association', 0x10a, state='completed', created='2025-11-01T00:00:00Z', upload=False, backup=True,
        association=OTHER_ASSOCIATION, attempts=1, completed='2025-11-01T00:30:00Z')
for index in range(1, 13):
    # archive05 and archive06 share created_at: the page orders by -id next.
    second = 5 if index == 6 else index
    receipt(f'archive{index:02d}', 0x200 + index, state='completed', created=f'2025-06-01T00:00:{second:02d}Z', upload=False,
            backup=True, attempts=1, completed='2025-06-01T01:00:00Z', preview={'records': index})
receipt('admin', 0x301, owner='admin', state='completed', created='2026-02-15T00:00:00Z', upload=False, backup=True,
        attempts=1, completed='2026-02-15T00:30:00Z', preview={'records': 7})
receipt('foreign_owner', 0x302, owner='foreign_owner', state='failed', created='2026-02-20T00:00:00Z', upload=False,
        backup=True, attempts=1, error='Errore di prova.')
SEED = copy.deepcopy(ops)

cases = []
dirty = {}  # receipt -> fields to restore before the next case


def iso_key(op):
    return op['created_at']


def owned(owner):
    return [op for op in ops.values() if op['owner'] == owner]


def public(key, *, progress=None):
    op = ops[key]
    return {'id': op['id'], 'state': op['state'], 'stage': op['stage'],
            'preview': {k: v for k, v in op['preview'].items() if k not in ('missing_relation_details', 'tutor_reassignment_details')},
            'error': op['error'], 'created_at': op['created_at'], 'updated_at': op['updated_at'],
            'has_recovery_backup': bool(op['backup_path']), 'attempts': op['attempts'],
            'progress': progress.get(key) if progress and op['state'] in ('queued', 'running') else None,
            'download_token': f'$download-token:{key}' if op['backup_path'] else None}


def key_of(op):
    return next(k for k, v in ops.items() if v is op)


def recovery_page(owner, before=None, progress=None):
    rows = [op for op in owned(owner) if op['backup_path']]
    if before is not None:
        boundary = ops[before]
        rows = [op for op in rows if op['created_at'] < boundary['created_at']
                or (op['created_at'] == boundary['created_at'] and op['id'] < boundary['id'])]
    rows.sort(key=lambda op: (op['created_at'], op['id']), reverse=True)
    rows = rows[:11]
    return {'backups': [public(key_of(op), progress=progress) for op in rows[:10]],
            'backups_next': rows[9]['id'] if len(rows) > 10 else None}


def overview(owner, *, available=True, progress=None):
    history = sorted(owned(owner), key=lambda op: op['created_at'], reverse=True)[:10]
    # Distinct created_at among the first ten (the generator's order is Django's).
    assert len({op['created_at'] for op in history}) == len(history)
    active = sorted((op for op in owned(owner) if op['state'] in ('queued', 'running')), key=lambda op: op['id'])
    return {'available': available, 'reason': '' if available else SINGLE, 'max_upload_bytes': MAX_UPLOAD,
            'active': public(key_of(active[0]), progress=progress) if active else None,
            'history': [public(key_of(op), progress=progress) for op in history], **recovery_page(owner, progress=progress)}


def add(name, actor, path, status, response, *, method='GET', body=None, raw=None, content_type=None, headers=None,
        single=True, lock=None, setup=None, cache=None, objects=None, objects_after=None, changes=None, celery=(),
        no_store=True, stream=None, attachment=None, referrer=None, archive=None, created=None):
    """changes: {receipt: {field: value}} the request makes ('$now' for updated_at)."""
    merged = copy.deepcopy(dirty)
    for key, fields in (setup or {}).items():
        merged.setdefault(key, {}).update(fields)
    dirty.clear()
    for key, fields in merged.items():
        ops[key].update(copy.deepcopy(fields))
    touched = {}
    for key, fields in (changes or {}).items():
        ops[key].update(copy.deepcopy(fields))
        if fields.get('updated_at') == '$now':
            touched[key] = {'updated_at': CREATED_UPDATED}
    if callable(response):
        response = response()
    case = {'name': f'data-restore-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'single': single, 'lock': lock, 'setup': merged or None,
            'cache': cache, 'objects': objects or {}, 'objects_after': sorted(objects or {}) if objects_after is None else sorted(objects_after),
            'effect': {'receipts': {**copy.deepcopy(ops), **({'$new': created} if created else {})}, 'celery': list(celery)},
            'cache_control': 'no-store' if no_store and response != SERVER else None,
            'stream': stream, 'attachment': attachment, 'referrer_policy': referrer}
    if body is not None:
        case['body'] = body
    if raw is not None:
        case['raw_b64'], case['content_type'] = base64.b64encode(raw).decode(), content_type
    if archive is not None:
        case['archive'] = archive
    cases.append(case)
    # A '$now' updated_at is only known to the run: restore it before the next case.
    for key, fields in touched.items():
        dirty.setdefault(key, {}).update(fields)
        ops[key].update(fields)


def action(key, name):
    return f'{ROOT}/{ops[key]["id"]}/{name}'


def download(key, token=None):
    path = f'{ROOT}/{ops[key]["id"]}/download'
    return path if token is None else f'{path}?download_token={token}'


def options(name):
    return {'name': name, 'description': '', 'renders': ['application/json'],
            'parses': ['multipart/form-data', 'application/json']}


def error(text):
    return {'error': text}


def task(key):
    # restore_instance_data(soft_time_limit=3300, time_limit=3600).apply_async(args=[id], task_id=id)
    return {'task': TASK, 'args': [ops[key]['id']], 'kwargs': [], 'args_list': True, 'id': ops[key]['id'], 'timelimit': [3600, 3300]}


def stream(key, content, *, content_type='application/octet-stream'):
    return {'body_b64': base64.b64encode(content).decode(), 'content_type': content_type, 'length': len(content),
            'disposition': f'attachment; filename="backup-prima-del-ripristino-{ops[key]["id"]}.zip"'}


def multipart(*parts, boundary='restore-fixture-boundary'):
    """parts: (name, filename or None, content bytes)."""
    body = b''
    for name, filename, content in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else '')
        body += f'--{boundary}\r\nContent-Disposition: {disposition}\r\n'.encode()
        if filename is not None:
            body += b'Content-Type: application/zip\r\n'
        body += b'\r\n' + content + b'\r\n'
    return body + f'--{boundary}--\r\n'.encode(), f'multipart/form-data; boundary={boundary}'


def backup_key(key):
    return ops[key]['backup_path']


ZIP = b'PK\x05\x06' + bytes(18)
BACKUP_BYTES = b'PK\x03\x04 recovery archive placeholder \x00\x01\x02\xff'
NOW = '$now'

# ---------------------------------------------------------------------------
# Identity and permission (IsInstanceOwner on the authenticated actor, before
# method dispatch); every response, errors included, has Cache-Control.
# ---------------------------------------------------------------------------
add('anonymous', 'anonymous', ROOT, 401, ANON)
add('anonymous-backups', 'anonymous', BACKUPS, 401, ANON)
add('anonymous-action', 'anonymous', action('review', 'status'), 401, ANON)
add('anonymous-start', 'anonymous', action('review', 'start'), 401, ANON, method='POST', body={})
add('athlete-denied', 'athlete', ROOT, 403, DENIED)
add('collaborator-denied', 'full_collaborator', ROOT, 403, DENIED)
add('restricted-collaborator-denied', 'restricted_collaborator', BACKUPS, 403, DENIED)
add('foreign-owner-upload-denied', 'foreign_owner', ROOT, 403, DENIED, method='POST', body={})
add('foreign-owner-action-denied', 'foreign_owner', action('foreign_owner', 'status'), 403, DENIED)
add('athlete-start-denied', 'athlete', action('review', 'start'), 403, DENIED, method='POST', body={'confirmation': 'RIPRISTINA'})
add('athlete-wrong-method-denied-first', 'athlete', ROOT, 403, DENIED, method='PUT', body={})
add('owner-impersonation-header', 'owner', ROOT, 403, LEAVE, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('owner-wrong-method', 'owner', ROOT, 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT', body={})
add('owner-backups-post', 'owner', BACKUPS, 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('owner-action-delete', 'owner', action('review', 'cancel'), 405, {'detail': 'Method "DELETE" not allowed.'}, method='DELETE')
add('download-post', 'anonymous', download('completed'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('options-root', 'owner', ROOT, 200, options('Data Restore'), method='OPTIONS')
add('options-backups', 'owner', BACKUPS, 200, options('Data Restore Backups'), method='OPTIONS')
add('options-action', 'owner', action('review', 'start'), 200, options('Data Restore Action'), method='OPTIONS')
add('options-download-anonymous', 'anonymous', download('completed'), 200, options('Data Restore Download'), method='OPTIONS')

# ---------------------------------------------------------------------------
# GET admin/data-restore
# ---------------------------------------------------------------------------
add('overview-several-associations', 'owner', ROOT, 200, lambda: overview('owner', available=False), single=False)
add('overview', 'owner', ROOT, 200, lambda: overview('owner'))
add('overview-head', 'owner', ROOT, 200, '<head>', method='HEAD')
add('overview-admin', 'admin', ROOT, 200, lambda: overview('admin'))
add('overview-admin-session-ignored', 'admin', ROOT, 200, lambda: overview('admin'),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('overview-lock-held', 'owner', ROOT, 200, lambda: overview('owner'), lock='exclusive')
PROGRESS = {'operation_id': op_id(0x106), 'attempt': 3, 'sequence': 12, 'phase': 'files',
            'updated_at': '2026-04-02T10:00:00.123456+00:00', 'completed': 512, 'total': 2048, 'unit': 'bytes',
            'detail': '1 / 4 allegati', 'percent': 25, 'elapsed_seconds': 9, 'eta_seconds': 27}
add('overview-running-progress', 'owner', ROOT, 200, lambda: overview('owner', progress={'failed': PROGRESS}),
    setup={'failed': {'state': 'running', 'stage': 'files', 'attempts': 3, 'error': ''}},
    cache={'failed': {'payload': PROGRESS}})
add('status-running-progress', 'owner', action('failed', 'status'), 200, lambda: public('failed', progress={'failed': PROGRESS}),
    cache={'failed': {'payload': PROGRESS}})
add('status-progress-other-attempt', 'owner', action('failed', 'status'), 200, lambda: public('failed'),
    cache={'failed': {'payload': {**PROGRESS, 'attempt': 2}}})
add('status-progress-boolean-attempt', 'owner', action('failed', 'status'), 200,
    lambda: public('failed', progress={'failed': {**PROGRESS, 'attempt': True}}),
    setup={'failed': {'attempts': 1}}, cache={'failed': {'payload': {**PROGRESS, 'attempt': True}}})
add('status-progress-integer-value', 'owner', action('failed', 'status'), 200, lambda: public('failed'),
    cache={'failed': {'raw': '7'}})
add('status-progress-empty', 'owner', action('failed', 'status'), 200, lambda: public('failed'),
    cache={'failed': {'payload': {}}})
add('status-progress-without-attempt', 'owner', action('failed', 'status'), 200, lambda: public('failed'),
    cache={'failed': {'payload': {'phase': 'backup'}}})
add('status-progress-list', 'owner', action('failed', 'status'), 200, lambda: public('failed'),
    cache={'failed': {'payload': ['attempt', 1]}})
add('overview-queued-no-progress', 'owner', ROOT, 200, lambda: overview('owner'),
    setup={'failed': {'state': 'queued', 'stage': 'queued', 'attempts': 0}})

# ---------------------------------------------------------------------------
# POST admin/data-restore (upload): every path up to the archive validation.
# A queued receipt exists now (restore_pending).
# ---------------------------------------------------------------------------
add('upload-lock-held', 'owner', ROOT, 409, error(BUSY), method='POST', body={}, lock='exclusive')
add('upload-pending', 'owner', ROOT, 409, error(PENDING), method='POST', raw=multipart(('file', 'backup.zip', ZIP))[0],
    content_type=multipart(('file', 'backup.zip', ZIP))[1])
add('upload-shared-lock-pending', 'owner', ROOT, 409, error(PENDING), method='POST', body={}, lock='shared')
add('upload-several-associations', 'owner', ROOT, 400, error(SINGLE), method='POST', body={}, single=False,
    setup={'failed': {'state': 'failed', 'stage': 'failed', 'attempts': 2,
                      'error': SEED['failed']['error']}})
add('upload-json-body', 'owner', ROOT, 400, error(UPLOAD), method='POST', body={'file': 'backup.zip'})
add('upload-empty-body', 'owner', ROOT, 400, error(UPLOAD), method='POST')
body, kind = multipart(('file', None, ZIP))
add('upload-file-as-field', 'owner', ROOT, 400, error(UPLOAD), method='POST', raw=body, content_type=kind)
body, kind = multipart(('archive', 'backup.zip', ZIP), ('note', None, b'x'))
add('upload-other-file-name', 'owner', ROOT, 400, error(UPLOAD), method='POST', raw=body, content_type=kind)
body, kind = multipart(('file', 'backup.zip', b'P' * (MAX_UPLOAD + 1)))
add('upload-too-large', 'owner', ROOT, 400, error(UPLOAD), method='POST', raw=body, content_type=kind)
body, kind = multipart(('extra', 'notes.zip', b'N' * 40000), ('file', 'backup.zip', b'P' * 30000))
add('upload-cumulative-limit', 'owner', ROOT, 400, error(UPLOAD), method='POST', raw=body, content_type=kind)
body, kind = multipart(('file', '', ZIP))
add('upload-empty-filename', 'owner', ROOT, 400, error(UPLOAD), method='POST', raw=body, content_type=kind)
add('upload-admin-lock-held', 'admin', ROOT, 409, error(BUSY), method='POST', body={}, lock='exclusive')

# ---------------------------------------------------------------------------
# GET admin/data-restore/backups
# ---------------------------------------------------------------------------
add('backups-first-page', 'owner', BACKUPS, 200, lambda: recovery_page('owner'))
add('backups-empty-before', 'owner', BACKUPS + '?before=', 200, lambda: recovery_page('owner'))
add('backups-next-page', 'owner', f'{BACKUPS}?before={ops["archive06"]["id"]}', 200, lambda: recovery_page('owner', 'archive06'))
add('backups-tied-boundary', 'owner', f'{BACKUPS}?before={ops["archive05"]["id"]}', 200, lambda: recovery_page('owner', 'archive05'))
add('backups-receipt-cursor', 'owner', f'{BACKUPS}?before={ops["receipt"]["id"]}', 200, lambda: recovery_page('owner', 'receipt'))
add('backups-upper-case-cursor', 'owner', f'{BACKUPS}?before={ops["completed"]["id"].upper()}', 200,
    lambda: recovery_page('owner', 'completed'))
add('backups-urn-cursor', 'owner', f'{BACKUPS}?before=urn:uuid:{ops["failed"]["id"]}', 200, lambda: recovery_page('owner', 'failed'))
add('backups-last-value', 'owner', f'{BACKUPS}?before=bad&before={ops["archive10"]["id"]}', 200,
    lambda: recovery_page('owner', 'archive10'))
add('backups-invalid-cursor', 'owner', BACKUPS + '?before=page-2', 400, error('Pagina dei backup non valida.'))
add('backups-unknown-cursor', 'owner', f'{BACKUPS}?before={op_id(0xfff)}', 404, NOT_FOUND)
add('backups-foreign-cursor', 'owner', f'{BACKUPS}?before={ops["admin"]["id"]}', 404, NOT_FOUND)
add('backups-admin', 'admin', BACKUPS, 200, lambda: recovery_page('admin'))

# ---------------------------------------------------------------------------
# GET admin/data-restore/<id>/<action>
# ---------------------------------------------------------------------------
add('status-review', 'owner', action('review', 'status'), 200, lambda: public('review'))
add('status-completed', 'owner', action('completed', 'status'), 200, lambda: public('completed'))
add('status-head', 'owner', action('review', 'status'), 200, '<head>', method='HEAD')
add('status-foreign-receipt', 'owner', action('admin', 'status'), 404, NOT_FOUND)
add('status-unknown', 'owner', f'{ROOT}/{op_id(0xfff)}/status', 404, NOT_FOUND)
add('status-several-associations', 'owner', action('review', 'status'), 200, lambda: public('review'), single=False)
add('admin-status', 'admin', action('admin', 'status'), 200, lambda: public('admin'))


def jsonb(value):
    """A JSONField value as PostgreSQL jsonb returns it: object keys ordered by length, then bytes."""
    if isinstance(value, dict):
        return {k: jsonb(value[k]) for k in sorted(value, key=lambda k: (len(k.encode()), k.encode()))}
    if isinstance(value, list):
        return [jsonb(item) for item in value]
    return value


def report(key):
    op = ops[key]
    preview = jsonb(op['preview'])
    return json.dumps({'operation_id': op['id'], 'source_sha256': op['sha256'],
                       'missing_relations': preview.get('missing_relation_details', []),
                       'tutor_reassignments': preview.get('tutor_reassignment_details', [])}, indent=2)


add('missing-relations', 'owner', action('review', 'missing-relations'), 200, lambda: {'$raw': report('review')},
    attachment=f'attachment; filename="rapporto-backup-{ops["review"]["id"]}.json"')
add('missing-relations-empty', 'owner', action('cancelled', 'missing-relations'), 200, lambda: {'$raw': report('cancelled')},
    attachment=f'attachment; filename="rapporto-backup-{ops["cancelled"]["id"]}.json"')
add('backup-stream', 'owner', action('completed', 'backup'), 200, '<stream>',
    objects={backup_key('completed'): BACKUP_BYTES.decode('latin-1')}, stream=stream('completed', BACKUP_BYTES))
add('backup-missing-object', 'owner', action('failed', 'backup'), 500, SERVER)
add('backup-without-recovery', 'owner', action('review', 'backup'), 404, error(NO_BACKUP))
add('unknown-get-action', 'owner', action('completed', 'explode'), 404, error(NO_BACKUP))

# ---------------------------------------------------------------------------
# GET admin/data-restore/<id>/download (AllowAny, no authentication)
# ---------------------------------------------------------------------------
OBJECTS = {backup_key('completed'): BACKUP_BYTES.decode('latin-1')}
add('download-anonymous', 'anonymous', download('completed', '$token:completed:fresh'), 200, '<stream>', objects=OBJECTS,
    stream=stream('completed', BACKUP_BYTES), referrer='no-referrer')
add('download-owner-bearer', 'owner', download('completed', '$token:completed:fresh'), 200, '<stream>', objects=OBJECTS,
    stream=stream('completed', BACKUP_BYTES), referrer='no-referrer')
add('download-several-associations', 'anonymous', download('completed', '$token:completed:fresh'), 200, '<stream>',
    objects=OBJECTS, stream=stream('completed', BACKUP_BYTES), referrer='no-referrer', single=False)
add('download-admin-receipt', 'anonymous', download('admin', '$token:admin:fresh'), 200, '<stream>',
    objects={backup_key('admin'): 'admin recovery'}, stream=stream('admin', b'admin recovery'), referrer='no-referrer')
add('download-without-token', 'anonymous', download('completed'), 403, error('Link di download non valido o scaduto.'))
add('download-expired', 'anonymous', download('completed', '$token:completed:expired'), 403, error('Link di download non valido o scaduto.'))
add('download-tampered', 'anonymous', download('completed', '$token:completed:tampered'), 403, error('Link di download non valido o scaduto.'))
add('download-other-salt', 'anonymous', download('completed', '$token:completed:other-salt'), 403, error('Link di download non valido o scaduto.'))
add('download-other-checksum', 'anonymous', download('completed', '$token:completed:other-sha'), 403, error('Link di download non valido o scaduto.'))
add('download-other-receipt-token', 'anonymous', download('completed', '$token:failed:fresh'), 403, error('Link di download non valido o scaduto.'))
add('download-extra-key', 'anonymous', download('completed', '$token:completed:extra-key'), 403, error('Link di download non valido o scaduto.'))
add('download-compressed', 'anonymous', download('completed', '$token:completed:compressed'), 200, '<stream>', objects=OBJECTS,
    stream=stream('completed', BACKUP_BYTES), referrer='no-referrer')
add('download-without-timestamp', 'anonymous', download('completed', '$token:completed:untimed'), 500, SERVER)
add('download-unknown', 'anonymous', f'{ROOT}/{op_id(0xfff)}/download?download_token=x', 404, NOT_FOUND)
add('download-foreign-owner', 'anonymous', download('foreign_owner', '$token:foreign_owner:fresh'), 403, error('Download non autorizzato.'))
add('download-other-association', 'anonymous', download('foreign_association', '$token:foreign_association:fresh'), 403,
    error('Download non autorizzato.'))
add('download-missing-object', 'anonymous', download('failed', '$token:failed:fresh'), 500, SERVER)
add('download-head', 'anonymous', download('completed', '$token:completed:fresh'), 200, '<head>', method='HEAD', objects=OBJECTS,
    stream=stream('completed', BACKUP_BYTES), referrer='no-referrer')

# ---------------------------------------------------------------------------
# POST admin/data-restore/<id>/<action>: the exclusive lock, the row, the
# instance, then the action.
# ---------------------------------------------------------------------------
add('action-lock-shared-held', 'owner', action('review', 'cancel'), 409, error(BUSY), method='POST', body={}, lock='shared')
add('action-lock-exclusive-held', 'owner', action('review', 'cancel'), 409, error(BUSY), method='POST', body={}, lock='exclusive')
add('action-unknown-receipt', 'owner', f'{ROOT}/{op_id(0xfff)}/cancel', 404, NOT_FOUND, method='POST', body={})
add('action-foreign-receipt', 'owner', action('admin', 'cancel'), 404, NOT_FOUND, method='POST', body={})
add('action-several-associations', 'owner', action('review', 'cancel'), 400, error(SINGLE), method='POST', body={}, single=False)
add('action-unknown', 'owner', action('review', 'explode'), 404, error('Azione non disponibile.'), method='POST', body={})

# delete-backup
add('delete-backup-active', 'owner', action('failed', 'delete-backup'), 400,
    error('Attendi il termine del ripristino prima di eliminare il backup di sicurezza.'), method='POST', body={'confirm': True},
    setup={'failed': {'state': 'running', 'stage': 'backup', 'attempts': 1}})
add('delete-backup-unconfirmed', 'owner', action('completed', 'delete-backup'), 400,
    error('Conferma l’eliminazione del backup di sicurezza.'), method='POST', body={},
    setup={'failed': {'state': 'failed', 'stage': 'failed', 'attempts': 2}})
add('delete-backup-truthy', 'owner', action('completed', 'delete-backup'), 400,
    error('Conferma l’eliminazione del backup di sicurezza.'), method='POST', body={'confirm': 1})
body, kind = multipart(('confirm', None, b'true'))
add('delete-backup-form', 'owner', action('completed', 'delete-backup'), 400,
    error('Conferma l’eliminazione del backup di sicurezza.'), method='POST', raw=body, content_type=kind)
add('delete-backup-list-body', 'owner', action('completed', 'delete-backup'), 500, SERVER, method='POST', body=['confirm'])
add('delete-backup', 'owner', action('completed', 'delete-backup'), 200, lambda: public('completed'), method='POST',
    body={'confirm': True}, objects={backup_key('completed'): 'recovery', backup_key('archive01'): 'other recovery'},
    objects_after=[backup_key('archive01')],
    changes={'completed': {'backup_path': '', 'backup_sha256': '', 'updated_at': NOW}})
add('delete-backup-again', 'owner', action('completed', 'delete-backup'), 200, lambda: public('completed'), method='POST',
    body={'confirm': True})
add('delete-backup-absent-object', 'owner', action('archive01', 'delete-backup'), 200, lambda: public('archive01'), method='POST',
    body={'confirm': True}, changes={'archive01': {'backup_path': '', 'backup_sha256': '', 'updated_at': NOW}})
add('backups-after-deletes', 'owner', BACKUPS, 200, lambda: recovery_page('owner'))

# dismiss
add('dismiss-review', 'owner', action('review', 'dismiss'), 400, error('Puoi chiudere solo un ripristino terminato.'), method='POST', body={})
add('dismiss-failed', 'owner', action('failed', 'dismiss'), 200, lambda: public('failed'), method='POST', body={},
    changes={'failed': {'preview': {**SEED['failed']['preview'], 'dismissed': True}, 'updated_at': NOW}})
add('dismiss-again', 'owner', action('failed', 'dismiss'), 200, lambda: public('failed'), method='POST', body=['ignored'],
    changes={'failed': {'updated_at': NOW}})
add('dismiss-cancelled', 'owner', action('cancelled', 'dismiss'), 200, lambda: public('cancelled'), method='POST',
    changes={'cancelled': {'preview': {'dismissed': True}, 'updated_at': NOW}})

# cancel (then cleanup of the upload and staged media prefixes)
add('cancel-completed', 'owner', action('completed', 'cancel'), 400, error('Puoi annullare solo un backup in attesa di conferma.'),
    method='POST', body={})
REVIEW = ops['review']['id']
CANCEL_OBJECTS = {
    f'restores/{REVIEW}/upload/archive.zip': 'upload', f'restores/{REVIEW}/upload/parts/one.bin': 'part',
    f'restores/{REVIEW}/media/m1/scheda.pdf': 'media', f'restores/{REVIEW}/media/m2/deep/foto.jpg': 'photo',
    f'restores/{REVIEW}/recovery/keep.zip': 'recovery', f'restores/{REVIEW}/uploads-sibling.txt': 'sibling',
    f'restores/{ops["media"]["id"]}/upload/archive.zip': 'other upload',
}
add('cancel-review', 'owner', action('review', 'cancel'), 202, lambda: public('review'), method='POST', body={},
    objects=CANCEL_OBJECTS,
    objects_after=[f'restores/{REVIEW}/recovery/keep.zip', f'restores/{REVIEW}/uploads-sibling.txt',
                   f'restores/{ops["media"]["id"]}/upload/archive.zip'],
    changes={'review': {'state': 'cancelled', 'stage': 'cancelled', 'upload_path': '', 'media': {}, 'updated_at': NOW}})
add('cancel-again', 'owner', action('review', 'cancel'), 400, error('Puoi annullare solo un backup in attesa di conferma.'),
    method='POST', body={})
add('cancel-review-without-upload', 'owner', action('review', 'cancel'), 202, lambda: public('review'), method='POST',
    setup={'review': {'state': 'review', 'stage': 'validated'}},
    changes={'review': {'state': 'cancelled', 'stage': 'cancelled', 'updated_at': NOW}})

# start / resume
RESTORE = {'state': 'review', 'stage': 'validated', 'upload_path': SEED['review']['upload_path']}
add('start-completed', 'owner', action('completed', 'start'), 200, lambda: public('completed'), method='POST', body={})
add('resume-completed', 'owner', action('completed', 'resume'), 200, lambda: public('completed'), method='POST', body=['x'])
add('start-cancelled', 'owner', action('cancelled', 'start'), 400, error('Carica e valida nuovamente il backup.'), method='POST',
    body={'confirmation': 'RIPRISTINA'})
add('resume-review', 'owner', action('review', 'resume'), 400, error('Carica e valida nuovamente il backup.'), method='POST',
    body={'confirmation': 'RIPRISTINA'}, setup={'review': RESTORE})
add('start-without-confirmation', 'owner', action('review', 'start'), 400, error('Scrivi RIPRISTINA per confermare la sostituzione.'),
    method='POST', body={})
add('start-lower-case-confirmation', 'owner', action('review', 'start'), 400,
    error('Scrivi RIPRISTINA per confermare la sostituzione.'), method='POST', body={'confirmation': 'ripristina'})
add('start-list-body', 'owner', action('review', 'start'), 500, SERVER, method='POST', body=['RIPRISTINA'])
add('start-stale', 'owner', action('stale', 'start'), 400, error('La validazione è scaduta. Carica nuovamente il backup.'),
    method='POST', body={'confirmation': 'RIPRISTINA'})
add('start-other-version', 'owner', action('oldversion', 'start'), 400, error('L’istanza è cambiata. Carica nuovamente il backup.'),
    method='POST', body={'confirmation': 'RIPRISTINA'})
add('start-other-association', 'owner', action('moved', 'start'), 400, error('L’istanza è cambiata. Carica nuovamente il backup.'),
    method='POST', body={'confirmation': 'RIPRISTINA'})
add('start-missing-media-unconfirmed', 'owner', action('media', 'start'), 400, error('Conferma l’assenza degli allegati indicati.'),
    method='POST', body={'confirmation': 'RIPRISTINA', 'allow_missing_media': 'true'})
add('start-pending', 'owner', action('review', 'start'), 409, error(PENDING), method='POST', body={'confirmation': 'RIPRISTINA'},
    setup={'foreign_association': {'state': 'running', 'stage': 'restoring'}})
add('start', 'owner', action('review', 'start'), 202, lambda: public('review'), method='POST',
    body={'confirmation': 'RIPRISTINA', 'allow_missing_media': True},
    setup={'foreign_association': {'state': 'completed', 'stage': 'completed'}},
    changes={'review': {'state': 'queued', 'stage': 'queued', 'allow_missing_media': True, 'updated_at': NOW}}, celery=[task('review')])
add('overview-queued', 'owner', ROOT, 200, lambda: overview('owner'))
add('upload-while-queued', 'owner', ROOT, 409, error(PENDING), method='POST', body={})
add('start-queued-redelivery', 'owner', action('review', 'start'), 202, lambda: public('review'), method='POST', body={},
    celery=[task('review')])
add('resume-running', 'owner', action('review', 'resume'), 202, lambda: public('review'), method='POST', body=['ignored'],
    setup={'review': {'state': 'running', 'stage': 'restoring', 'attempts': 1}}, celery=[task('review')])
add('resume-running-progress', 'owner', action('review', 'resume'), 202,
    lambda: public('review', progress={'review': {**PROGRESS, 'operation_id': REVIEW, 'attempt': 1}}),
    method='POST', cache={'review': {'payload': {**PROGRESS, 'operation_id': REVIEW, 'attempt': 1}}}, celery=[task('review')])
add('dismiss-running', 'owner', action('review', 'dismiss'), 400, error('Puoi chiudere solo un ripristino terminato.'),
    method='POST', body={})
add('cancel-running', 'owner', action('review', 'cancel'), 400, error('Puoi annullare solo un backup in attesa di conferma.'),
    method='POST', body={})
add('start-media-pending', 'owner', action('media', 'start'), 409, error(PENDING), method='POST',
    body={'confirmation': 'RIPRISTINA', 'allow_missing_media': True})
body, kind = multipart(('confirmation', None, b'RIPRISTINA'), ('allow_missing_media', None, b'true'))
add('start-missing-media-form', 'owner', action('media', 'start'), 400, error('Conferma l’assenza degli allegati indicati.'),
    method='POST', raw=body, content_type=kind, setup={'review': {'state': 'completed', 'stage': 'completed', 'attempts': 1}})
add('start-missing-media-confirmed', 'owner', action('media', 'start'), 202, lambda: public('media'), method='POST',
    body={'allow_missing_media': True, 'confirmation': 'RIPRISTINA'},
    changes={'media': {'state': 'queued', 'stage': 'queued', 'allow_missing_media': True, 'updated_at': NOW}}, celery=[task('media')])
body, kind = multipart(('confirmation', None, b'RIPRISTINA'))
add('start-form-confirmation', 'owner', action('oldversion', 'start'), 202, lambda: public('oldversion'), method='POST',
    raw=body, content_type=kind, setup={'media': {'state': 'failed', 'stage': 'failed'}, 'oldversion': {'version': '$version'}},
    changes={'oldversion': {'state': 'queued', 'stage': 'queued', 'updated_at': NOW}}, celery=[task('oldversion')])
add('overview-final', 'owner', ROOT, 200, lambda: overview('owner'))
add('admin-start-completed', 'admin', action('admin', 'start'), 200, lambda: public('admin'), method='POST', body={},
    setup={'oldversion': {'state': 'cancelled', 'stage': 'cancelled'}})

# ---------------------------------------------------------------------------
# POST admin/data-restore with archives. The runner builds each ZIP from
# Django's export layout (AssociationExportService.EXPORT_ORDER: every
# section listed with its file and count, empty unless given) and posts it as
# 'file'. A created receipt is checked field by field ('$new'), then the
# runner removes it so the next upload starts from the same receipts.
# ---------------------------------------------------------------------------
ARCHIVE_ASSOCIATION, ARCHIVE_OWNER = 'd7000000-0000-4000-8000-00000000e001', 'd7000000-0000-4000-8000-00000000e002'
ARCHIVE_DOCUMENT, ARCHIVE_MEDIA_DOCUMENT = 'd7000000-0000-4000-8000-00000000e003', 'd7000000-0000-4000-8000-00000000e004'
ARCHIVE_LINK = 'd7000000-0000-4000-8000-00000000e005'
DAMAGED = 'Il backup ZIP è danneggiato o non rispetta il formato Bakney.'


def archive(*, rows=None, members=None, manifest=None, drop=(), associations=None, raw=None):
    if raw is not None:
        return {'raw_b64': base64.b64encode(raw).decode()}
    return {'association': ARCHIVE_ASSOCIATION, 'denomination': 'Associazione archiviata', 'owner': ARCHIVE_OWNER,
            'export_date': '2026-03-30T10:00:00+00:00', 'rows': rows or {}, 'members': members or {},
            'manifest': manifest or {}, 'drop': list(drop), 'associations': associations}


def summary(*, records=2, files=0, missing_media=0, relations=()):
    return {'association': 'Associazione archiviata', 'export_date': '2026-03-30T10:00:00+00:00', 'skipped_records': {},
            'records': records, 'files': files, 'missing_media': missing_media, 'legacy_invoices': 0, 'reassigned_tutors': 0,
            'tutor_tax_code_conflicts': 0, 'tutor_reassignment_details': [], 'missing_relations': len(relations),
            'missing_relation_details': list(relations)}


def created_receipt(preview, owner='owner'):
    return {'id': '$new', 'owner': owner, 'association_id': IDS['own_association'], 'state': 'review', 'stage': 'validated',
            'created_at': '$now', 'updated_at': '$now', 'completed_at': None, 'upload_path': 'restores/$new/upload/archive.zip',
            'sha256': '$archive-sha256', 'version': '$version', 'preview': preview, 'backup_path': '', 'backup_sha256': '',
            'media': {}, 'error': '', 'allow_missing_media': False, 'attempts': 0}


def new_public(preview):
    return {'id': '$new', 'state': 'review', 'stage': 'validated',
            'preview': {k: v for k, v in preview.items() if k not in ('missing_relation_details', 'tutor_reassignment_details')},
            'error': '', 'created_at': '$now', 'updated_at': '$now', 'has_recovery_backup': False, 'attempts': 0,
            'progress': None, 'download_token': None}


def upload(name, actor, status, response, spec, **extra):
    add(name, actor, ROOT, status, response, method='POST', archive=spec, **extra)


IDLE = {'review': {'state': 'failed', 'stage': 'failed', 'attempts': 1}}
upload('upload-archive-not-a-zip', 'owner', 400, error(DAMAGED), archive(raw=b'not a zip archive'), setup=IDLE)
upload('upload-archive-without-manifest', 'owner', 400, error(DAMAGED), archive(raw=ZIP))
upload('upload-archive-other-version', 'owner', 400, error('Formato non supportato. Carica un export ZIP Bakney versione 1.0.0.'),
       archive(manifest={'version': '2.0.0'}))
upload('upload-archive-export-errors', 'owner', 400, error('Il backup segnala errori di esportazione. Genera un export completo.'),
       archive(manifest={'errors': ['Documento mancante']}))
upload('upload-archive-incomplete', 'owner', 400, error('Export incompleto: mancano Course.'), archive(drop=['Course']))
upload('upload-archive-two-associations', 'owner', 400, error('Il backup deve contenere una sola associazione.'),
       archive(associations=2))
upload('upload-archive-manifest-mismatch', 'owner', 400, error('Il manifest non corrisponde all’associazione esportata.'),
       archive(manifest={'association': {'sport_association_id': ARCHIVE_ASSOCIATION, 'denomination': 'Altro nome'}}))
upload('upload-archive-orphan-attachment', 'owner', 400, error('Un allegato fa riferimento a un record mancante.'),
       archive(members={f'files/documents/{ARCHIVE_DOCUMENT}/scheda.pdf': 'pdf'}))
STALE, MOVED = ops['stale']['id'], ops['moved']['id']
MINIMAL = summary()
upload('upload-archive', 'owner', 201, new_public(MINIMAL), archive(),
       objects={f'restores/{STALE}/upload/archive.zip': 'stale upload', f'restores/{STALE}/media/m1/a.pdf': 'stale media',
                f'restores/{MOVED}/upload/archive.zip': 'moved upload', f'restores/{MOVED}/recovery/keep.zip': 'kept'},
       objects_after=[f'restores/{MOVED}/recovery/keep.zip', 'restores/$new/upload/archive.zip'],
       changes={key: {'state': 'cancelled', 'stage': 'cancelled', 'upload_path': '', 'media': {}, 'updated_at': NOW}
                for key in ('stale', 'moved')},
       created=created_receipt(MINIMAL))
DOCUMENTS = [{'document_id': ARCHIVE_DOCUMENT, 'filename': 'scheda.pdf', 'filepath': 'documents/scheda.pdf'},
             {'document_id': ARCHIVE_MEDIA_DOCUMENT, 'filename': 'foto.jpg', 'filepath': 'documents/foto.jpg'}]
MEDIA = summary(records=4, files=1, missing_media=1)
upload('upload-archive-with-media', 'owner', 201, new_public(MEDIA),
       archive(rows={'Document': DOCUMENTS}, members={f'files/documents/{ARCHIVE_MEDIA_DOCUMENT}/foto.jpg': 'jpeg'}),
       objects_after=['restores/$new/upload/archive.zip'], created=created_receipt(MEDIA))
RELATION = {'model': 'SportAssociationDocumentsArchive', 'record_id': ARCHIVE_LINK, 'field': 'folder_id', 'target_model': 'Folder',
            'target_id': '4242'}
LINKED = summary(records=4, missing_media=1, relations=[RELATION])
upload('upload-archive-missing-relation', 'admin', 201, new_public(LINKED),
       archive(rows={'Document': DOCUMENTS[:1], 'SportAssociationDocumentsArchive': [
           {'sport_association_documents_archive_id': ARCHIVE_LINK, 'sport_association_id': ARCHIVE_ASSOCIATION,
            'document_id': ARCHIVE_DOCUMENT, 'date': '2026-03-01', 'folder_id': 4242}]}),
       objects_after=['restores/$new/upload/archive.zip'], created=created_receipt(LINKED, owner='admin'))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'data restore administration (overview, upload admission, '
                              'recovery backups, receipts, actions, downloads)', 'ids': IDS, 'instance': INSTANCE,
                              'other_association': OTHER_ASSOCIATION, 'max_upload_bytes': MAX_UPLOAD, 'receipts': SEED,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
