"""Author the subscription/import/upload HTTP sequence (subscription-import-upload.md).

subscription_import_upload (BE/application/views/subscriptions_views.py),
@api_view(['POST']), IsAuthenticated, registry association.members.create:

1. scoped_queryset(SportAssociation.objects).filter(user=request.user).first():
   None (athletes, owners without a live association) is Response(status=401)
   (no body, no challenge).
2. data.keys() (a non-dict body raises: 500).
3. 'associates_file': upload_and_store_file (Document.objects.create(filename=
   file.name) audited and indexed to the actor's association, then
   default_storage.save of the bytes under <timestamp>/<id>/<name>), then
   get_pandas_df_from_file: '.csv' (lower-cased path) is pandas.read_csv(file,
   delimiter=';'); Excel extensions use read_excel; anything else raises
   TypeError (500 after the writes); pandas errors are 500 after the writes.
   columns.tolist(), then auto_map_columns(columns, SubscriptionMaps.MAP):
   for each column the first key with the smallest edit distance of the
   lower-cased texts below len(column) * 0.25 (each key once per request) is
   set to the column in the class-level MAP. The answer is
   {'columns': [...], 'map': MAP, 'document_id': id}; MAP keeps the values of
   earlier requests in the same process.
4. 'document_id' and 'map': is_valid_uuid(document_id) (400 [str(exception)]),
   Document.objects.filter(document_id=...).first() (None: ValueError, 500),
   then the import itself (codicefiscale, dateutil): not ported, and only its
   failure paths are authored.
5. Otherwise TypeError: 500.

The first upload maps every key it will ever map in this sequence, so a later
workload in the same server process (Caddy after native) sees the same MAP.
Run with ``python3 -I`` after the message-mutations fixture.
"""
import base64
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_import_upload_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
SERVER = '<stable-server-error-page>'
PATH = '/subscription/import/upload'
BOUNDARY = 'ImportBoundary7d1f'
DOCUMENT = {'id': 'a1000000-0000-4000-8000-000000984001', 'token': 'a1000000-0000-4000-8000-000000984002', 'filename': 'soci.csv'}
KEYS = ['nome', 'cognome', 'sesso', 'codice fiscale', 'data di nascita', 'città di nascita', 'indirizzo', 'città di residenza', 'cap',
        'email', 'telefono', 'nome tutore', 'cognome tutore', 'sesso tutore', 'codice fiscale tutore', 'data di nascita tutore',
        'città di nascita tutore', 'città di residenza tutore', 'indirizzo tutore', 'cap tutore', 'email tutore', 'telefono tutore',
        'scadenza certificato medico', 'data tesseramento', 'data scadenza tesseramento', 'numero tessera']
MAP = {key: None for key in KEYS}
cases = []


def distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def auto_map(columns):
    keys = list(MAP)
    for column in columns:
        lowest, chosen = len(column) * 0.25, None
        for key in keys:
            d = distance(column.lower(), key.lower())
            if d < lowest:
                lowest, chosen = d, key
                if d == 0:
                    break
        if chosen is not None:
            keys.remove(chosen)
            MAP[chosen] = column


def multipart(fields=(), files=()):
    parts = []
    for name, value in fields:
        parts.append(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, filename, content, kind in files:
        parts.append(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f'Content-Type: {kind}\r\n\r\n'.encode() + content + b'\r\n')
    return b''.join(parts) + f'--{BOUNDARY}--\r\n'.encode()


def add(name, actor, status, response, *, method='POST', body=None, raw=None, headers=None, effect=None):
    case = {'name': f'subscription-import-upload-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw is not None:
        # The contract sends raw_body.encode(): every authored body is UTF-8.
        case['raw_body'], case['content_type'] = raw.decode('utf-8'), f'multipart/form-data; boundary={BOUNDARY}'
    cases.append(case)


def upload(name, actor, filename, content, *, status=200, columns=None, kind='text/csv', content_type='text/csv', headers=None,
           session=None):
    """A stored upload: the Document (audited) and its object, then the columns or the 500 page."""
    effect = {'actor': actor, 'session': session, 'filename': filename, 'body_b64': base64.b64encode(content).decode(),
              'content_type': content_type}
    if status == 200:
        auto_map(columns)
        response = {'columns': columns, 'map': dict(MAP), 'document_id': '$new'}
    else:
        response = SERVER
    add(name, actor, status, response, raw=multipart(files=[('associates_file', filename, content, kind)]), headers=headers,
        effect=effect)


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SIMPLE = b'Nome;Cognome\nMario;Rossi\n'

# Identity and dispatch.
add('anonymous', 'anonymous', 401, ANONYMOUS, body={})
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={})
add('restricted-collaborator', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.members.create'}, body={})
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, body={},
    headers=SCOPED)
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Subscription Import Upload', 'description': '', 'renders': ['application/json'],
                              'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

# No association: the view's own 401 without a body.
add('athlete-unauthorized', 'athlete', 401, '<empty>', body={})
add('owner-without-association-unauthorized', 'owner_without_association', 401, '<empty>', body={})
add('deleted-association-unauthorized', 'deleted_association_owner', 401, '<empty>', body={})

# Bodies without a stored upload.
add('list-body-server-error', 'owner', 500, SERVER, body=[{'document_id': DOCUMENT['id'], 'map': {}}])
add('empty-object-server-error', 'owner', 500, SERVER, body={})
add('document-id-without-map-server-error', 'owner', 500, SERVER, body={'document_id': DOCUMENT['id']})
add('text-associates-file-server-error', 'owner', 500, SERVER, body={'associates_file': 'soci.csv'})
add('form-field-associates-file-server-error', 'owner', 500, SERVER, raw=multipart(fields=[('associates_file', 'soci.csv')]))
add('malformed-document-id', 'owner', 400, ['badly formed hexadecimal UUID string'], body={'document_id': 'non-un-uuid', 'map': {}})
add('integer-document-id', 'owner', 400, ["'int' object has no attribute 'replace'"], body={'document_id': 5, 'map': {}})
add('null-document-id', 'owner', 400, ['one of the hex, bytes, bytes_le, fields, or int arguments must be given'], body={'document_id': None, 'map': None})
add('unknown-document-server-error', 'owner', 500, SERVER, body={'document_id': IDS['missing'], 'map': {'nome': 'Nome'}})
add('unknown-document-form-server-error', 'full_collaborator', 500, SERVER,
    raw=multipart(fields=[('document_id', IDS['missing']), ('map', '{}')]))

# Uploads. The first maps every key this sequence maps (fuzzy, duplicated and unmatched columns included).
FIRST = ('Nome;COGNOME;Sesso;Codice fiscal;Data di Nascita;Città di nascita;Indirizzo;Città di residenza;CAP;E-mail;Telefono;'
         'Nome tutore;Cognome tutore;Sesso tutore;Codice fiscale tutore;Data nascita tutore;Città nascita tutore;'
         'Città residenza tutore;Indirizzo tutore;Cap tutore;Email tutore;Telefono tutore;Scadenza certificato med;Data tesseramento;'
         'Scadenza tesseramento;Numero tessera;Note;Nome\n'
         'Mario;Rossi;M;RSSMRA80A01H501U;01/01/1980;Roma;Via Roma 1;Roma;00100;mario@example.test;3330000000;;;;;;;;;;;;'
         '31/12/2026;01/09/2026;31/08/2027;42;;Mario\n').encode()
upload('csv-maps-every-key', 'owner', 'soci.csv', FIRST, columns=[
    'Nome', 'COGNOME', 'Sesso', 'Codice fiscal', 'Data di Nascita', 'Città di nascita', 'Indirizzo', 'Città di residenza', 'CAP',
    'E-mail', 'Telefono', 'Nome tutore', 'Cognome tutore', 'Sesso tutore', 'Codice fiscale tutore', 'Data nascita tutore',
    'Città nascita tutore', 'Città residenza tutore', 'Indirizzo tutore', 'Cap tutore', 'Email tutore', 'Telefono tutore',
    'Scadenza certificato med', 'Data tesseramento', 'Scadenza tesseramento', 'Numero tessera', 'Note', 'Nome.1'])
assert all(value is not None for value in MAP.values()), MAP
upload('csv-keeps-earlier-map', 'owner', 'altri.csv', SIMPLE, columns=['Nome', 'Cognome'])
upload('csv-bom-crlf-quoted-unnamed-duplicates', 'full_collaborator', 'Esportazione.CSV',
       b'\xef\xbb\xbf"Nome";;"Note;varie";Note;\r\nMario;;x;y;\r\n"Anna\r\nMaria";;;;\r\n', content_type='text/csv',
       columns=['Nome', 'Unnamed: 1', 'Note;varie', 'Note', 'Unnamed: 4'])
upload('csv-header-only', 'admin', 'vuoto.csv', b'Telefono;Altro\n', headers=ADMIN_OWNER, session='admin_owner',
       columns=['Telefono', 'Altro'])
upload('csv-implicit-index', 'owner', 'indice.csv', b'a;b\n1;2;3\n4;5;6\n', columns=['a', 'b'])
upload('csv-blank-lines-whitespace', 'owner', 'spazi.csv', b'\n  \n a ;b\n\t\n1;2\n', columns=[' a ', 'b'])
upload('csv-too-many-fields-server-error', 'owner', 'errore.csv', b'a;b\n1;2\n1;2;3\n', status=500)
upload('csv-unterminated-quote-server-error', 'owner', 'virgolette.csv', b'a;b\n"1;2\n', status=500)
upload('csv-empty-server-error', 'owner', 'nulla.csv', b'\n\n', status=500)
upload('text-extension-server-error', 'owner', 'soci.txt', SIMPLE, status=500, kind='text/plain', content_type='text/plain')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'subscription/import/upload', 'ids': IDS, 'location': 'import-uploads',
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
