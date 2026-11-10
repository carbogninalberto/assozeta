"""Author the document upload HTTP sequence (documents-writes.md).

Routes: ``documents/add`` and ``documents/<pk>/update`` (DocumentArchiveViewSet,
JSONParser only), ``subscription/<uid>/upload-document``,
``document/medical/certificate/`` and ``subscription/<uid>/medical-certificate/upload``.

Every upload creates a Document (``filename`` as given), stores the bytes with
``default_storage.save(os.path.join(str(creation_date.timestamp()), str(id),
filename), ...)`` (S3: ``AWS_LOCATION``-prefixed key, ``mimetypes.guess_type``
content type and encoding, ``AWS_S3_OBJECT_PARAMETERS``) and links it:
- documents/add: base64 ``files[i].document`` per item, one archive row each
  (``folder`` is resolved with ``Folder.objects.get(id=...)``); an exception
  inside the loop becomes ``400 {'exception': str(e)}`` after
  ``document.delete()`` of the last Document the loop created; the response
  ``date`` is ``isoformat()`` of the ``timezone.now()`` default;
- documents/<pk>/update: DocumentArchiveSerializer partial update, then
  rendering the nested ``many=True`` document raises (500 after the save);
- upload-document: a SubscriptionFile, resolved with ``scoped_queryset``;
- medical/certificate: AllowAny, a MedicalCertificate for the requester
  (``None`` when anonymous), remembered for an association-scoped session.
  MedicalCertificateUploadThrottle (AnonRateThrottle, per address,
  MEDICAL_CERTIFICATE_UPLOADS_PER_HOUR) runs before the method dispatch for
  requests without an account: 429 with Retry-After. A file name whose type is
  no PDF and no image is 400 {'error'}, a file above
  MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES is 413 {'error'}, both before any write;
- medical-certificate/upload: ``_medical_certificate_targets`` (404), then the
  certificate attached with a full ``Subscription.save()`` or written into the
  draft's ``medical_certificate`` entry.
Requests are not atomic. Run with ``python3 -I``.
"""
import base64
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('documents_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{352001 + n:012d}' for n in range(200))
UNKNOWN = 'a1000000-0000-4000-8000-000000352999'
NEW_PK = 'a1000000-0000-4000-8000-000000352998'
SERVER = '<stable-server-error-page>'
BAD_REQUEST = '<bad-request-page>'
LOCATION = 'documents-writes'

FOLDERS = [('own', OWN), ('abroad', FOREIGN)]


def document(label, created='2026-08-01T08:00:00Z'):
    return {'id': next(ids), 'token': next(ids), 'filename': f'{label}.pdf', 'created': created}


DOCUMENTS = {label: document(label) for label in ('u1', 'u2', 'uf', 'ud', 'upk', 'old')}


def archive(association, doc, date, folder=None):
    return {'id': next(ids), 'association': association, 'document': doc, 'date': date, 'folder': folder}


ARCHIVES = {
    'u1': archive(OWN, 'u1', '2026-09-01'),
    'u2': archive(OWN, 'u2', '2026-09-02', 'own'),
    'uf': archive(FOREIGN, 'uf', '2026-09-03', 'abroad'),
    'ud': archive(OWN, 'ud', '2026-09-04'),
    'upk': archive(OWN, 'upk', '2026-09-05'),
}
CERTIFICATES = {'old': {'id': next(ids), 'user': 'owner', 'document': 'old', 'created': '2026-01-10T09:00:00Z'}}


def subscription(association, *, user='owner', medical=None, group=None, start='2026-01-01', end='2026-12-31',
                 created='2026-02-01T09:00:00Z'):
    return {'id': next(ids), 'association': association, 'user': user, 'medical': medical, 'group': group, 'start': start, 'end': end,
            'created': created}


# Another member of the association (an athlete account without records).
EXTRA_USERS = {'member': {'id': 'a1000000-0000-4000-8000-000000352997', 'username': 'documents-writes-member', 'email': 'documents-writes-member@example.test'}}
SUBSCRIPTIONS = {
    'main': subscription(OWN),
    'withcert': subscription(OWN, medical='old'),
    'nodates': subscription(OWN, start=None, end=None, created='2026-03-15T10:00:00Z'),
    'abroad': subscription(FOREIGN, user='foreign_owner'),
    'athlete': subscription(OWN, user='athlete'),
    'grouped': subscription(OWN, group='own_group'),
    'files': subscription(OWN),
}
DRAFTS = {
    'none': {'id': next(ids), 'association': OWN, 'data': {'associate': {'first_name': 'Ada'}}},
    'withmc': {'id': next(ids), 'association': OWN,
               'data': {'medical_certificate': {'medical_id': None, 'filename': 'x.pdf', 'certificate_expring_date': '01/01/2027', 'k': 1}}},
    'abroad_draft': {'id': next(ids), 'association': FOREIGN, 'data': {}},
    'list': {'id': next(ids), 'association': OWN, 'data': [1, 2]},
}
# A file name with '&' (the browser sends File.name as is): sanitize_file_name html.unescape()s it first.
SUBSCRIPTIONS['ampersand'] = subscription(OWN)

cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        throttle=None, retry_after=None):
    """throttle: {'preload': n, 'age': seconds}: the address's upload history holds n entries of that age before the request."""
    case = {'name': f'documents-writes-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if throttle is not None:
        case['throttle'] = throttle
    if retry_after is not None:
        case['retry_after'] = retry_after
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def stored(filename, content, content_type, encoding=None, *, kept=True, object_stored=True):
    """A Document the request creates: its object (bytes, ContentType, ContentEncoding) and whether the row remains."""
    return {'filename': filename, 'body_b64': base64.b64encode(content.encode()).decode() if object_stored else None,
            'content_type': content_type, 'encoding': encoding, 'kept': kept}


def effect(actor, *, documents=(), archives=(), files=(), certificates=(), subscriptions=None, drafts=None, audit=(), cache=None,
           archive_updates=None, archive_inserted=None, archive_deleted=()):
    """documents: stored(...) in creation order ($doc<i>); archives: [{'document': i, 'folder': label|None}] kept rows ($archive<i>);
    files: [{'document': i, 'subscription': label}] ($file<i>); certificates: [{'document': i, 'user': label|None}] ($cert<i>);
    subscriptions: {label: {column: value}} with '$cert<i>'; drafts: {label: final data}; audit: entries in write order;
    cache: the medical upload grant ({'session': label, 'certificate': i}); archive_updates: {label: {column: value}};
    archive_inserted: a copy of a seeded row under a new key; archive_deleted: seeded rows deleted."""
    return {'actor': actor, 'documents': list(documents), 'archives': list(archives), 'files': list(files),
            'certificates': list(certificates), 'subscriptions': subscriptions or {}, 'drafts': drafts or {}, 'audit': list(audit),
            'cache': cache, 'archive_updates': archive_updates or {}, 'archive_inserted': archive_inserted,
            'archive_deleted': list(archive_deleted)}


def entry(model, key, action, changes, index):
    """index: 'actor' (the actor's own association), 'direct', 'subscription:<label>', 'changes' or None."""
    return {'model': model, 'key': key, 'action': action, 'changes': changes, 'index': index}


doc_create = lambda i: entry('Document', f'$doc{i}', 0, '$created', 'actor')
doc_delete = lambda i: entry('Document', f'$doc{i}', 2, '$deleted', 'actor')
archive_create = lambda i: entry('SportAssociationDocumentsArchive', f'$archive{i}', 0, '$created', 'direct')
archive_cascade = lambda i: entry('SportAssociationDocumentsArchive', f'$archive{i}', 2, '$deleted', 'changes')
file_create = lambda i, sub: entry('SubscriptionFile', f'$file{i}', 0, '$created', f'subscription:{sub}')
cert_create = lambda i: entry('MedicalCertificate', f'$cert{i}', 0, '$created', 'actor')

ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ARCHIVE_UPDATE = {'detail': 'Missing permission: association.archive.update'}
MEMBERS_UPDATE = {'detail': 'Missing permission: association.members.update'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_RESOURCE = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
BAD_UUID = ['badly formed hexadecimal UUID string']
FORM = 'application/x-www-form-urlencoded'
B64 = lambda text: base64.b64encode(text.encode()).decode()
VALIDATION = lambda text: {'exception': "{'exception': ErrorDetail(string='" + text + "', code='invalid')}"}
options = lambda name, parses=('application/json', 'application/x-www-form-urlencoded', 'multipart/form-data'): {
    'name': name, 'description': '', 'renders': ['application/json'], 'parses': list(parses)}


def multipart(*parts, boundary='documentswritesboundary'):
    """parts: (name, filename or None, content type or None, text) as a multipart/form-data body."""
    chunks = []
    for name, filename, content_type, text in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else '')
        chunks.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\n'
                      + (f'Content-Type: {content_type}\r\n' if content_type else '') + f'\r\n{text}\r\n')
    return ''.join(chunks) + f'--{boundary}--\r\n', f'multipart/form-data; boundary={boundary}'


# ---------------------------------------------------------------- documents/add
ADD = '/documents/add'
ADD_OPTIONS = {**options('Document Archive', ('application/json',)), 'actions': {'POST': {
    'sport_association_documents_archive_id': {'type': 'string', 'required': False, 'read_only': False,
                                               'label': 'Sport association documents archive id'},
    'sport_association': {'type': 'field', 'required': True, 'read_only': False, 'label': 'Sport association'},
    'document': {'type': 'field', 'required': False, 'read_only': True, 'label': 'Document', 'child': {
        'type': 'nested object', 'required': False, 'read_only': True, 'children': {
            'document_id': {'type': 'string', 'required': False, 'read_only': False, 'label': 'Document id'},
            'creation_date': {'type': 'datetime', 'required': False, 'read_only': False, 'label': 'Creation date'},
            'filepath': {'type': 'string', 'required': False, 'read_only': False, 'label': 'Filepath', 'max_length': 1000},
            'filename': {'type': 'string', 'required': True, 'read_only': False, 'label': 'Filename', 'max_length': 255},
            'file_size_bytes': {'type': 'integer', 'required': False, 'read_only': False, 'label': 'File size bytes',
                                'min_value': 0, 'max_value': 9223372036854775807},
            'token': {'type': 'string', 'required': False, 'read_only': False, 'label': 'Token'}}}},
    'date': {'type': 'date', 'required': False, 'read_only': False, 'label': 'Date'},
    'folder': {'type': 'field', 'required': False, 'read_only': False, 'label': 'Folder'}}}}


def added(*rows):
    """The add response: one archive per stored item, '$archive<i>'/'$doc<i>' resolved by the runner."""
    return {'data': [{'sport_association_documents_archive_id': f'$archive{i}', 'sport_association': OWN,
                      'document': {'document_id': f'$doc{d}', 'creation_date': f'$doc{d}.created', 'filepath': None,
                                   'filename': filename, 'file_size_bytes': None, 'token': f'$doc{d}.token'},
                      'date': f'$archive{i}.date', 'folder': f'$folder:{folder}' if folder else None}
                     for i, (d, filename, folder) in enumerate(rows)]}


PDF = '%PDF-1.4 statuto'
add('add-anonymous', 'anonymous', ADD, 401, ANONYMOUS, body={'files': []})
add('add-admin-without-session', 'admin', ADD, 403, ADMIN, body={'files': []})
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, ARCHIVE_UPDATE, body={'files': []})
add('add-restricted-before-method', 'restricted_collaborator', ADD, 403, ARCHIVE_UPDATE, method='GET')
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, SCOPED_DENIED, body={'files': []}, headers=SCOPED)
add('add-wrong-method', 'owner', ADD, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('add-options', 'owner', ADD, 200, ADD_OPTIONS, method='OPTIONS')
add('add-delete-without-pk-server-error', 'owner', ADD, 500, SERVER, method='DELETE')
add('add-form-unsupported-media', 'owner', ADD, 415, {'detail': f'Unsupported media type "{FORM}" in request.'}, raw_body='files=1',
    content_type=FORM)
add('add-malformed-json', 'owner', ADD, 400, {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{bad', content_type='application/json')
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, body=[])
add('add-unknown-folder-server-error', 'owner', ADD, 500, SERVER, body={'folder': 999999, 'files': []})
add('add-text-folder-server-error', 'owner', ADD, 500, SERVER, body={'folder': 'abc', 'files': []})
add('add-athlete-server-error', 'athlete', ADD, 500, SERVER, body={'files': []})
add('add-owner-without-association-server-error', 'owner_without_association', ADD, 500, SERVER, body={'files': []})
add('add-number-files-server-error', 'owner', ADD, 500, SERVER, body={'files': 5})
add('add-null-files-server-error', 'owner', ADD, 500, SERVER, body={'files': None})
add('add-no-files', 'owner', ADD, 201, {'data': []}, body={})
add('add-empty-files', 'owner', ADD, 201, {'data': []}, body={'files': [], 'folder': None})
add('add-missing-filename', 'owner', ADD, 400, VALIDATION('document or filename key not present'), body={'files': [{'document': B64('x')}]})
add('add-text-files-iterates-characters', 'owner', ADD, 400, VALIDATION('document or filename key not present'), body={'files': 'ab'})
add('add-dict-files-iterates-keys', 'owner', ADD, 400, VALIDATION('document or filename key not present'),
    body={'files': {'document': 1, 'filename': 2}})
add('add-number-item', 'owner', ADD, 400, {'exception': "argument of type 'int' is not iterable"}, body={'files': [5]})
add('add-null-item', 'owner', ADD, 400, {'exception': "argument of type 'NoneType' is not iterable"}, body={'files': [None]})
add('add-text-item-with-both-keys', 'owner', ADD, 400, {'exception': "string indices must be integers, not 'str'"},
    body={'files': ['documentfilename']})
add('add-list-item-with-both-keys', 'owner', ADD, 400, {'exception': 'list indices must be integers or slices, not str'},
    body={'files': [['document', 'filename']]})
add('add-base64-one-extra-character', 'owner', ADD, 400,
    {'exception': 'Invalid base64-encoded string: number of data characters (5) cannot be 1 more than a multiple of 4'},
    body={'files': [{'document': 'abcde', 'filename': 'a.pdf'}]})
add('add-base64-incorrect-padding', 'owner', ADD, 400, {'exception': 'Incorrect padding'}, body={'files': [{'document': 'YQ==x', 'filename': 'a.pdf'}]})
add('add-base64-number', 'owner', ADD, 400, {'exception': "argument should be a bytes-like object or ASCII string, not 'int'"},
    body={'files': [{'document': 5, 'filename': 'a.pdf'}]})
add('add-base64-non-ascii', 'owner', ADD, 400, {'exception': 'string argument should contain only ASCII characters'},
    body={'files': [{'document': 'è', 'filename': 'a.pdf'}]})
add('add-filename-too-long', 'owner', ADD, 400, {'exception': 'value too long for type character varying(255)\n'},
    body={'files': [{'document': B64('x'), 'filename': 'a' * 252 + '.pdf'}]})
add('add-owner-one-root-file', 'owner', ADD, 201, added((0, 'statuto è.pdf', None)),
    body={'files': [{'document': B64(PDF), 'filename': 'statuto è.pdf'}]},
    effect=effect('owner', documents=[stored('statuto è.pdf', PDF, 'application/pdf')], archives=[{'document': 0, 'folder': None}],
                  audit=[doc_create(0), archive_create(0)]))
add('add-collaborator-two-files-into-folder', 'full_collaborator', ADD, 201,
    added((0, 'note.txt', 'own'), (1, 'backup.tar.gz', 'own')),
    body={'folder': '$folder:own', 'files': [{'document': B64('riga uno\n'), 'filename': 'note.txt'},
                                             {'document': B64('tarball'), 'filename': 'backup.tar.gz', 'extra': True}]},
    effect=effect('full_collaborator', documents=[stored('note.txt', 'riga uno\n', 'text/plain'),
                                                  stored('backup.tar.gz', 'tarball', 'application/x-tar', 'gzip')],
                  archives=[{'document': 0, 'folder': 'own'}, {'document': 1, 'folder': 'own'}],
                  audit=[doc_create(0), archive_create(0), doc_create(1), archive_create(1)]))
add('add-folder-resolved-by-id', 'owner', ADD, 201, added((0, 'senza estensione', 'abroad')),
    body={'folder': '$folder:abroad', 'files': [{'document': 'c2Vu\nemE=', 'filename': 'senza estensione'}]},
    effect=effect('owner', documents=[stored('senza estensione', 'senza', 'application/octet-stream')],
                  archives=[{'document': 0, 'folder': 'abroad'}], audit=[doc_create(0), archive_create(0)]))
add('add-admin-impersonating-owner', 'admin', ADD, 201, added((0, 'scan.png', None)),
    body={'files': [{'document': B64('png-bytes'), 'filename': 'scan.png'}]}, headers=ADMIN_OWNER,
    effect=effect('admin', documents=[stored('scan.png', 'png-bytes', 'image/png')], archives=[{'document': 0, 'folder': None}],
                  audit=[doc_create(0), archive_create(0)]))
add('add-second-item-error-removes-last-document', 'owner', ADD, 400, {'exception': 'Incorrect padding'},
    body={'files': [{'document': B64('primo'), 'filename': 'primo.pdf'}, {'document': 'abc', 'filename': 'secondo.pdf'}]},
    effect=effect('owner', documents=[stored('primo.pdf', 'primo', 'application/pdf', kept=False)],
                  audit=[doc_create(0), archive_create(0), archive_cascade(0), doc_delete(0)]))
add('add-absolute-filename-removes-document', 'owner', ADD, 400, {'exception': "Detected path traversal attempt in '/etc/x.pdf'"},
    body={'files': [{'document': B64('x'), 'filename': '/etc/x.pdf'}]},
    effect=effect('owner', documents=[stored('/etc/x.pdf', 'x', None, kept=False, object_stored=False)],
                  audit=[doc_create(0), doc_delete(0)]))
add('add-number-filename-removes-document', 'owner', ADD, 400,
    {'exception': "join() argument must be str, bytes, or os.PathLike object, not 'int'"},
    body={'files': [{'document': B64('x'), 'filename': 7}]},
    effect=effect('owner', documents=[stored('7', 'x', None, kept=False, object_stored=False)], audit=[doc_create(0), doc_delete(0)]))

# ---------------------------------------------------------------- documents/<pk>/update
UPDATE = lambda label: f'/documents/{ARCHIVES[label]["id"] if label in ARCHIVES else label}/update'
NO_ARCHIVE = {'detail': 'No SportAssociationDocumentsArchive matches the given query.'}
archive_update = lambda label, changes: entry('SportAssociationDocumentsArchive', label, 1, changes, 'direct')
add('update-anonymous', 'anonymous', UPDATE('u1'), 401, ANONYMOUS, method='PATCH', body={})
add('update-admin-without-session', 'admin', UPDATE('u1'), 403, ADMIN, method='PATCH', body={})
add('update-restricted-missing-permission', 'restricted_collaborator', UPDATE('u1'), 403, ARCHIVE_UPDATE, method='PATCH', body={})
add('update-wrong-method', 'owner', UPDATE('u1'), 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('update-options', 'owner', UPDATE('u1'), 200, options('Document Archive', ('application/json',)), method='OPTIONS')
add('update-form-unsupported-media', 'owner', UPDATE('u1'), 415, {'detail': f'Unsupported media type "{FORM}" in request.'},
    method='PATCH', raw_body='date=01%2F02%2F2026', content_type=FORM)
add('update-malformed-pk', 'owner', UPDATE('abc'), 404, {'detail': 'Not found.'}, method='PATCH', body={})
add('update-unknown', 'owner', UPDATE(UNKNOWN), 404, NO_ARCHIVE, method='PATCH', body={})
add('update-foreign', 'owner', UPDATE('uf'), 404, NO_ARCHIVE, method='PATCH', body={})
add('update-athlete-server-error', 'athlete', UPDATE('u1'), 500, SERVER, method='PATCH', body={})
add('update-list-body', 'owner', UPDATE('u1'), 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']},
    method='PATCH', body=[])
add('update-iso-date-rejected', 'owner', UPDATE('u1'), 400,
    {'date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']}, method='PATCH', body={'date': '2026-02-01'})
add('update-unknown-folder', 'owner', UPDATE('u1'), 400, {'folder': ['Invalid pk "999999" - object does not exist.']},
    method='PATCH', body={'folder': 999999})
add('update-text-folder', 'owner', UPDATE('u1'), 400, {'folder': ['Incorrect type. Expected pk value, received str.']},
    method='PATCH', body={'folder': 'abc'})
add('update-foreign-folder-denied', 'owner', UPDATE('u1'), 403, {'detail': 'You do not have permission to perform this action.'},
    method='PATCH', body={'folder': '$folder:abroad'})
add('update-foreign-association-denied', 'owner', UPDATE('u1'), 403, {'detail': 'You do not have permission to perform this action.'},
    method='PATCH', body={'sport_association': FOREIGN})
add('update-unknown-association', 'owner', UPDATE('u1'), 400,
    {'sport_association': [f'Invalid pk "{UNKNOWN}" - object does not exist.']}, method='PATCH', body={'sport_association': UNKNOWN})
add('update-null-association', 'owner', UPDATE('u1'), 400, {'sport_association': ['This field may not be null.']},
    method='PATCH', body={'sport_association': None})
add('update-taken-primary-key', 'owner', UPDATE('u1'), 400,
    {'sport_association_documents_archive_id': ['sport association documents archive with this sport association documents archive id already exists.']},
    method='PATCH', body={'sport_association_documents_archive_id': ARCHIVES['u2']['id']})
add('update-no-change-server-error', 'owner', UPDATE('u1'), 500, SERVER, method='PATCH', body={'document': 'ignored'})
add('update-date-saved-before-server-error', 'owner', UPDATE('u1'), 500, SERVER, method='PATCH', body={'date': '01/02/2026'},
    effect=effect('owner', archive_updates={'u1': {'date': '2026-02-01'}},
                  audit=[archive_update('u1', {'date': ['2026-09-01', '2026-02-01']})]))
add('update-folder-saved-before-server-error', 'full_collaborator', UPDATE('u1'), 500, SERVER, method='PATCH',
    body={'folder': '$folder:own', 'sport_association': OWN},
    effect=effect('full_collaborator', archive_updates={'u1': {'folder': '$folder:own'}},
                  audit=[archive_update('u1', {'folder': ['None', '$folder-text:own']})]))
add('update-folder-cleared-before-server-error', 'owner', UPDATE('u2'), 500, SERVER, method='PATCH', body={'folder': None},
    effect=effect('owner', archive_updates={'u2': {'folder': None}}, audit=[archive_update('u2', {'folder': ['$folder-text:own', 'None']})]))
add('update-new-primary-key-inserts-row', 'owner', UPDATE('upk'), 500, SERVER, method='PATCH',
    body={'sport_association_documents_archive_id': NEW_PK},
    effect=effect('owner', archive_inserted={'from': 'upk', 'id': NEW_PK},
                  # pre_save finds no row under the new key: an update diff against None, indexed before the row exists
                  # (through changes_json), then post_save logs the create.
                  audit=[entry('SportAssociationDocumentsArchive', NEW_PK, 1, '$inserted', 'changes'),
                         entry('SportAssociationDocumentsArchive', NEW_PK, 0, '$inserted', 'direct')]))
add('update-route-delete', 'owner', UPDATE('ud'), 204, '<empty>', method='DELETE',
    effect=effect('owner', archive_deleted=['ud'], audit=[entry('SportAssociationDocumentsArchive', 'ud', 2, '$deleted', 'changes')]))

# ---------------------------------------------------------------- subscription/<uid>/upload-document
UPLOAD = lambda label: f'/subscription/{SUBSCRIPTIONS[label]["id"] if label in SUBSCRIPTIONS else label}/upload-document'
FILE_UPLOADED = {'mgs': 'file uploaded'}
add('upload-anonymous', 'anonymous', UPLOAD('files'), 401, ANONYMOUS, body={})
add('upload-admin-without-session', 'admin', UPLOAD('files'), 403, ADMIN, body={})
add('upload-restricted-missing-permission', 'restricted_collaborator', UPLOAD('files'), 403, MEMBERS_UPDATE, body={})
add('upload-wrong-method', 'owner', UPLOAD('files'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('upload-options', 'owner', UPLOAD('files'), 200, options('Subscription Upload Document'), method='OPTIONS')
add('upload-malformed-uid', 'owner', UPLOAD('abc'), 400, BAD_UUID, body={})
add('upload-unknown-server-error', 'owner', UPLOAD(UNKNOWN), 500, SERVER, body={'document': B64('x'), 'filename': 'a.pdf'})
add('upload-grouped-foreign-group-server-error', 'owner', UPLOAD('grouped'), 500, SERVER, body={'document': B64('x'), 'filename': 'a.pdf'},
    headers={'X-Group-Id': IDS['foreign_group']})
add('upload-missing-filename', 'owner', UPLOAD('files'), 400, {'exception': 'document key not present'}, body={'document': B64('x')})
add('upload-list-body-server-error', 'owner', UPLOAD('files'), 500, SERVER, body=[])
add('upload-malformed-json', 'owner', UPLOAD('files'), 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{bad', content_type='application/json')
add('upload-owner-json', 'owner', UPLOAD('files'), 200, FILE_UPLOADED, body={'document': B64('%PDF-1.4 tessera'), 'filename': 'tessera.pdf'},
    effect=effect('owner', documents=[stored('tessera.pdf', '%PDF-1.4 tessera', 'application/pdf')],
                  files=[{'document': 0, 'subscription': 'files'}], audit=[doc_create(0), file_create(0, 'files')]))
add('upload-collaborator-form', 'full_collaborator', UPLOAD('files'), 200, FILE_UPLOADED,
    raw_body=f'filename=ignored.txt&document={B64("riga")}&filename=modulo.txt', content_type=FORM,
    effect=effect('full_collaborator', documents=[stored('modulo.txt', 'riga', 'text/plain')],
                  files=[{'document': 0, 'subscription': 'files'}], audit=[doc_create(0), file_create(0, 'files')]))
add('upload-resolves-subscription-by-id', 'owner', UPLOAD('abroad'), 200, FILE_UPLOADED,
    body={'document': B64('estero'), 'filename': 'estero.csv'},
    effect=effect('owner', documents=[stored('estero.csv', 'estero', 'text/csv')], files=[{'document': 0, 'subscription': 'abroad'}],
                  audit=[doc_create(0), file_create(0, 'abroad')]))
add('upload-athlete-own-subscription', 'athlete', UPLOAD('athlete'), 200, FILE_UPLOADED,
    body={'document': B64('certificato'), 'filename': 'certificato.jpg'},
    effect=effect('athlete', documents=[stored('certificato.jpg', 'certificato', 'image/jpeg')],
                  files=[{'document': 0, 'subscription': 'athlete'}], audit=[doc_create(0), file_create(0, 'athlete')]))
# Files are attached by the association's staff and by the member the subscription belongs to.
add('upload-athlete-other-subscription', 'athlete', UPLOAD('files'), 403, SCOPED_RESOURCE,
    body={'document': B64('certificato'), 'filename': 'certificato.jpg'})
add('upload-athlete-other-subscription-form', 'athlete', UPLOAD('main'), 403, SCOPED_RESOURCE,
    raw_body=f'document={B64("riga")}&filename=modulo.txt', content_type=FORM)
add('upload-athlete-other-subscription-missing-filename', 'athlete', UPLOAD('files'), 403, SCOPED_RESOURCE, body={'document': B64('x')})
add('upload-athlete-other-association-subscription', 'athlete', UPLOAD('abroad'), 403, SCOPED_RESOURCE,
    body={'document': B64('estero'), 'filename': 'estero.csv'})
add('upload-athlete-unknown-server-error', 'athlete', UPLOAD(UNKNOWN), 500, SERVER, body={'document': B64('x'), 'filename': 'a.pdf'})
add('upload-member-athlete-subscription', 'member', UPLOAD('athlete'), 403, SCOPED_RESOURCE,
    body={'document': B64('certificato'), 'filename': 'certificato.jpg'})
add('upload-owner-full-session', 'owner', UPLOAD('main'), 200, FILE_UPLOADED, headers=OWNER_FULL,
    body={'document': B64('sessione'), 'filename': 'sessione.pdf'},
    effect=effect('owner', documents=[stored('sessione.pdf', 'sessione', 'application/pdf')],
                  files=[{'document': 0, 'subscription': 'main'}], audit=[doc_create(0), file_create(0, 'main')]))
add('upload-owner-full-session-foreign-denied', 'owner', UPLOAD('abroad'), 403, SCOPED_RESOURCE, headers=OWNER_FULL,
    body={'document': B64('x'), 'filename': 'x.pdf'})
add('upload-bad-base64-keeps-document', 'owner', UPLOAD('files'), 500, SERVER, body={'document': 'abc', 'filename': 'rotto.pdf'},
    effect=effect('owner', documents=[stored('rotto.pdf', '', None, object_stored=False)], audit=[doc_create(0)]))
add('upload-number-filename-keeps-document', 'owner', UPLOAD('files'), 500, SERVER, body={'document': B64('x'), 'filename': 12},
    effect=effect('owner', documents=[stored('12', '', None, object_stored=False)], audit=[doc_create(0)]))
add('upload-traversal-filename-keeps-document', 'owner', UPLOAD('files'), 400, BAD_REQUEST, body={'document': B64('x'), 'filename': '../x.pdf'},
    effect=effect('owner', documents=[stored('../x.pdf', '', None, object_stored=False)], audit=[doc_create(0)]))

# ---------------------------------------------------------------- document/medical/certificate/
CERT = '/document/medical/certificate/'
CERT_LIMIT = 4096
CERT_BODY = multipart(('medical_certificate', 'certificato medico.pdf', 'application/pdf', '%PDF-1.4 certificato'))
created_cert = lambda: {'msg': 'success!', 'uid': '$cert0', 'expiring_date': None}
add('certificate-wrong-method', 'owner', CERT, 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('certificate-options', 'owner', CERT, 200, options('Medical Certificate Document'), method='OPTIONS')
add('certificate-admin-without-session', 'admin', CERT, 403, ADMIN, raw_body=CERT_BODY[0], content_type=CERT_BODY[1])
add('certificate-missing-file-server-error', 'owner', CERT, 500, SERVER, body={})
add('certificate-text-value-server-error', 'owner', CERT, 500, SERVER, body={'medical_certificate': 'x'})
add('certificate-list-body-server-error', 'owner', CERT, 500, SERVER, body=[])
add('certificate-dot-dot-filename-skipped-server-error', 'owner', CERT, 500, SERVER,
    raw_body=multipart(('medical_certificate', '..', None, 'x'))[0], content_type=CERT_BODY[1])
add('certificate-owner', 'owner', CERT, 200, created_cert(), raw_body=CERT_BODY[0], content_type=CERT_BODY[1],
    effect=effect('owner', documents=[stored('certificato medico.pdf', '%PDF-1.4 certificato', 'application/pdf')],
                  certificates=[{'document': 0, 'user': 'owner'}], audit=[doc_create(0), cert_create(0)]))
add('certificate-anonymous', 'anonymous', CERT, 200, created_cert(),
    raw_body=multipart(('note', None, None, 'ignorata'), ('medical_certificate', 'C:\\scan\\visita.png', 'image/png', 'png'))[0],
    content_type=CERT_BODY[1],
    effect=effect(None, documents=[stored('visita.png', 'png', 'image/png')], certificates=[{'document': 0, 'user': None}],
                  audit=[entry('Document', '$doc0', 0, '$created', None), entry('MedicalCertificate', '$cert0', 0, '$created', None)]))
add('certificate-collaborator-path-filename', 'full_collaborator', CERT, 200, created_cert(),
    raw_body=multipart(('medical_certificate', 'cartella/primo.pdf', None, 'uno'), ('medical_certificate', 'cartella/ultimo.JPG', None, 'due'))[0],
    content_type=CERT_BODY[1],
    effect=effect('full_collaborator', documents=[stored('ultimo.JPG', 'due', 'image/jpeg')], certificates=[{'document': 0, 'user': 'owner'}],
                  audit=[doc_create(0), cert_create(0)]))
# A PDF or an image, by the type of the file name: anything else is refused before any write.
NOT_ACCEPTED = {'error': 'Seleziona un file PDF o un’immagine.'}
for label, actor, filename, kind in (('text', 'owner', 'note.txt', 'text/plain'), ('declared-pdf', 'owner', 'referto.html', 'application/pdf'),
                                     ('no-extension', 'anonymous', 'certificato', 'image/png'), ('archive', 'full_collaborator', 'scan.pdf.zip', None)):
    add(f'certificate-{label}-not-accepted', actor, CERT, 400, NOT_ACCEPTED,
        raw_body=multipart(('medical_certificate', filename, kind, 'contenuto'))[0], content_type=CERT_BODY[1])
# The size limit (MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES of the run): a file of exactly the limit is stored, one byte more is refused.
AT_LIMIT = '%PDF-1.4 ' + 'x' * (CERT_LIMIT - 9)
add('certificate-at-the-limit', 'owner', CERT, 200, created_cert(),
    raw_body=multipart(('medical_certificate', 'limite.pdf', 'application/pdf', AT_LIMIT))[0], content_type=CERT_BODY[1],
    effect=effect('owner', documents=[stored('limite.pdf', AT_LIMIT, 'application/pdf')], certificates=[{'document': 0, 'user': 'owner'}],
                  audit=[doc_create(0), cert_create(0)]))
add('certificate-above-the-limit', 'anonymous', CERT, 413, {'error': f'Il file supera la dimensione massima consentita ({CERT_LIMIT} byte).'},
    raw_body=multipart(('medical_certificate', 'grande.png', 'image/png', AT_LIMIT + 'x'))[0], content_type=CERT_BODY[1])
# Requests without an account are counted per address and hour (60): the next one is asked to wait, before the body is read.
FULL = {'preload': 60, 'age': 10.2}
THROTTLED = {'detail': 'Request was throttled. Expected available in 3590 seconds.'}
add('certificate-anonymous-over-the-rate', 'anonymous', CERT, 429, THROTTLED, raw_body=CERT_BODY[0], content_type=CERT_BODY[1],
    throttle=FULL, retry_after='3590')
add('certificate-anonymous-wrong-method-over-the-rate', 'anonymous', CERT, 429, THROTTLED, method='GET', throttle=FULL, retry_after='3590')
add('certificate-anonymous-below-the-rate', 'anonymous', CERT, 200, created_cert(),
    raw_body=multipart(('medical_certificate', 'visita.png', 'image/png', 'png'))[0], content_type=CERT_BODY[1],
    throttle={'preload': 59, 'age': 10.2},
    effect=effect(None, documents=[stored('visita.png', 'png', 'image/png')], certificates=[{'document': 0, 'user': None}],
                  audit=[entry('Document', '$doc0', 0, '$created', None), entry('MedicalCertificate', '$cert0', 0, '$created', None)]))
add('certificate-anonymous-old-history', 'anonymous', CERT, 405, {'detail': 'Method "GET" not allowed.'}, method='GET',
    throttle={'preload': 60, 'age': 3601})
# A signed-in account is not counted.
add('certificate-owner-full-history', 'owner', CERT, 200, created_cert(), raw_body=CERT_BODY[0], content_type=CERT_BODY[1], throttle=FULL,
    effect=effect('owner', documents=[stored('certificato medico.pdf', '%PDF-1.4 certificato', 'application/pdf')],
                  certificates=[{'document': 0, 'user': 'owner'}], audit=[doc_create(0), cert_create(0)]))
add('certificate-owner-full-session-remembered', 'owner', CERT, 200, created_cert(), raw_body=CERT_BODY[0], content_type=CERT_BODY[1],
    headers=OWNER_FULL,
    effect=effect('owner', documents=[stored('certificato medico.pdf', '%PDF-1.4 certificato', 'application/pdf')],
                  certificates=[{'document': 0, 'user': 'owner'}], audit=[doc_create(0), cert_create(0)],
                  cache={'session': 'owner_full', 'certificate': 0}))
# html.unescape: a bare '&' stays; named references with and without ';' (longest prefix: '&notit;' is '¬it;'),
# numeric ones ('&#13;' is a carriage return, then dropped as non-printable); '&sol;' becomes a separator.
for label, sent, kept in (('bare', 'Rossi & Bianchi.pdf', 'Rossi & Bianchi.pdf'),
                          ('references', 'a&amp;b&lt;c&#38;d&#x26;e&notit;&ampf&AElig&#13;.pdf', 'a&b<c&d&e¬it;&fÆ.pdf'),
                          ('unknown-reference', 'referto &xyz; &#; &#x;.pdf', 'referto &xyz; &#; &#x;.pdf'),
                          ('solidus', 'cartella&sol;visita&period;pdf', 'visita.pdf')):
    add(f'certificate-ampersand-{label}', 'owner', CERT, 200, created_cert(),
        raw_body=multipart(('medical_certificate', sent, 'application/pdf', '%PDF-1.4 e'))[0], content_type=CERT_BODY[1],
        effect=effect('owner', documents=[stored(kept, '%PDF-1.4 e', 'application/pdf')], certificates=[{'document': 0, 'user': 'owner'}],
                      audit=[doc_create(0), cert_create(0)]))

# ---------------------------------------------------------------- subscription/<uid>/medical-certificate/upload
MED = lambda label: (f'/subscription/{SUBSCRIPTIONS[label]["id"] if label in SUBSCRIPTIONS else DRAFTS[label]["id"] if label in DRAFTS else label}'
                     '/medical-certificate/upload')
MED_BODY = multipart(('medical_certificate', 'visita.pdf', 'application/pdf', '%PDF-1.4 visita'))
NOT_FOUND = {'error': 'Subscription not found'}
attached = {'medical': '$doc0', 'expiring_date': None}
sub_update = lambda label, changes: entry('Subscription', label, 1, changes, 'direct')
draft_update = lambda label, before, after: entry('AssociateImportDraft', label, 1, {'data': [before, after]}, 'direct')
dumps = lambda value: json.dumps(value, sort_keys=True)
add('medical-anonymous', 'anonymous', MED('main'), 401, ANONYMOUS, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-admin-without-session', 'admin', MED('main'), 403, ADMIN, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-restricted-missing-permission', 'restricted_collaborator', MED('main'), 403, MEMBERS_UPDATE, raw_body=MED_BODY[0],
    content_type=MED_BODY[1])
add('medical-wrong-method', 'owner', MED('main'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('medical-options', 'owner', MED('main'), 200, options('Subscription Medical Certificate Upload'), method='OPTIONS')
add('medical-malformed-uid', 'owner', MED('abc'), 400, BAD_UUID, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-unknown', 'owner', MED(UNKNOWN), 404, NOT_FOUND, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-foreign-subscription', 'owner', MED('abroad'), 404, NOT_FOUND, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-foreign-draft', 'owner', MED('abroad_draft'), 404, NOT_FOUND, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-grouped-foreign-group', 'owner', MED('grouped'), 404, NOT_FOUND, raw_body=MED_BODY[0], content_type=MED_BODY[1],
    headers={'X-Group-Id': IDS['foreign_group']})
add('medical-athlete-other-subscription', 'athlete', MED('main'), 404, NOT_FOUND, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-athlete-no-drafts', 'athlete', MED('none'), 404, NOT_FOUND, raw_body=MED_BODY[0], content_type=MED_BODY[1])
add('medical-missing-file', 'owner', MED('main'), 400, {'error': 'medical_certificate key not present!'}, body={})
add('medical-text-value', 'owner', MED('main'), 500, {'error': "'str' object has no attribute 'name'"}, body={'medical_certificate': 'x'})
add('medical-number-value', 'owner', MED('main'), 500, {'error': "'int' object has no attribute 'name'"}, body={'medical_certificate': 3})
add('medical-list-body', 'owner', MED('main'), 500, {'error': "'list' object has no attribute 'get'"}, body=[])
add('medical-malformed-json', 'owner', MED('main'), 500,
    {'error': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{bad', content_type='application/json')
add('medical-owner-subscription', 'owner', MED('main'), 200, attached, raw_body=MED_BODY[0], content_type=MED_BODY[1],
    effect=effect('owner', documents=[stored('visita.pdf', '%PDF-1.4 visita', 'application/pdf')], certificates=[{'document': 0, 'user': 'owner'}],
                  subscriptions={'main': {'medical_id': '$cert0'}},
                  audit=[doc_create(0), cert_create(0), sub_update('main', {'medical': ['None', '$cert0']})]))
add('medical-collaborator-replaces-certificate', 'full_collaborator', MED('withcert'), 200, attached, raw_body=MED_BODY[0],
    content_type=MED_BODY[1],
    effect=effect('full_collaborator', documents=[stored('visita.pdf', '%PDF-1.4 visita', 'application/pdf')],
                  certificates=[{'document': 0, 'user': 'owner'}], subscriptions={'withcert': {'medical_id': '$cert0'}},
                  audit=[doc_create(0), cert_create(0), sub_update('withcert', {'medical': [CERTIFICATES['old']['id'], '$cert0']})]))
add('medical-save-fills-dates', 'owner', MED('nodates'), 200, attached, raw_body=MED_BODY[0], content_type=MED_BODY[1],
    effect=effect('owner', documents=[stored('visita.pdf', '%PDF-1.4 visita', 'application/pdf')], certificates=[{'document': 0, 'user': 'owner'}],
                  subscriptions={'nodates': {'medical_id': '$cert0', 'start_date': '2026-01-01', 'end_date': '2026-12-31'}},
                  audit=[doc_create(0), cert_create(0),
                         sub_update('nodates', {'medical': ['None', '$cert0'], 'start_date': ['None', '2026-01-01'],
                                                'end_date': ['None', '2026-12-31']})]))
add('medical-athlete-own-subscription', 'athlete', MED('athlete'), 200, attached, raw_body=MED_BODY[0], content_type=MED_BODY[1],
    effect=effect('athlete', documents=[stored('visita.pdf', '%PDF-1.4 visita', 'application/pdf')],
                  certificates=[{'document': 0, 'user': 'athlete'}], subscriptions={'athlete': {'medical_id': '$cert0'}},
                  audit=[doc_create(0), cert_create(0), sub_update('athlete', {'medical': ['None', '$cert0']})]))
add('medical-owner-full-session', 'owner', MED('grouped'), 200, attached, raw_body=MED_BODY[0], content_type=MED_BODY[1], headers=OWNER_FULL,
    effect=effect('owner', documents=[stored('visita.pdf', '%PDF-1.4 visita', 'application/pdf')], certificates=[{'document': 0, 'user': 'owner'}],
                  subscriptions={'grouped': {'medical_id': '$cert0'}},
                  audit=[doc_create(0), cert_create(0), sub_update('grouped', {'medical': ['None', '$cert0']})]))
add('medical-ampersand-filename', 'owner', MED('ampersand'), 200, attached,
    raw_body=multipart(('medical_certificate', 'Rossi & Bianchi.pdf', 'application/pdf', '%PDF-1.4 visita'))[0], content_type=MED_BODY[1],
    effect=effect('owner', documents=[stored('Rossi & Bianchi.pdf', '%PDF-1.4 visita', 'application/pdf')],
                  certificates=[{'document': 0, 'user': 'owner'}], subscriptions={'ampersand': {'medical_id': '$cert0'}},
                  audit=[doc_create(0), cert_create(0), sub_update('ampersand', {'medical': ['None', '$cert0']})]))
DEFAULT_MC = lambda filename: {'medical_id': '$cert0', 'filename': filename, 'certificate_expring_date': None}
add('medical-draft-adds-entry', 'owner', MED('none'), 200, {'msg': 'updated draft', 'expiring_date': None}, raw_body=MED_BODY[0],
    content_type=MED_BODY[1],
    effect=effect('owner', documents=[stored('visita.pdf', '%PDF-1.4 visita', 'application/pdf')], certificates=[{'document': 0, 'user': 'owner'}],
                  drafts={'none': {**DRAFTS['none']['data'], 'medical_certificate': DEFAULT_MC('visita.pdf')}},
                  audit=[doc_create(0), cert_create(0),
                         draft_update('none', dumps(DRAFTS['none']['data']),
                                      dumps({**DRAFTS['none']['data'], 'medical_certificate': DEFAULT_MC('visita.pdf')}))]))
WITHMC = {'medical_certificate': {**DRAFTS['withmc']['data']['medical_certificate'], 'medical_id': '$cert0', 'filename': 'referto.txt'}}
add('medical-draft-keeps-entry-keys', 'full_collaborator', MED('withmc'), 200, {'msg': 'updated draft', 'expiring_date': None},
    raw_body=multipart(('medical_certificate', 'referto.txt', 'text/plain', 'referto'))[0], content_type=MED_BODY[1],
    effect=effect('full_collaborator', documents=[stored('referto.txt', 'referto', 'text/plain')], certificates=[{'document': 0, 'user': 'owner'}],
                  drafts={'withmc': WITHMC},
                  audit=[doc_create(0), cert_create(0), draft_update('withmc', dumps(DRAFTS['withmc']['data']), dumps(WITHMC))]))
add('medical-draft-list-data-keeps-certificate', 'owner', MED('list'), 500, {'error': "'list' object has no attribute 'keys'"},
    raw_body=MED_BODY[0], content_type=MED_BODY[1],
    effect=effect('owner', documents=[stored('visita.pdf', '%PDF-1.4 visita', 'application/pdf')], certificates=[{'document': 0, 'user': 'owner'}],
                  audit=[doc_create(0), cert_create(0)]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Document uploads: archive add/update, subscription files and medical certificates',
                              'ids': IDS, 'location': LOCATION, 'folders': FOLDERS, 'documents': DOCUMENTS, 'archives': ARCHIVES,
                              'certificates': CERTIFICATES, 'subscriptions': SUBSCRIPTIONS, 'drafts': DRAFTS, 'unknown': UNKNOWN,
                              'extra_users': EXTRA_USERS, 'certificate_limit': CERT_LIMIT, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
