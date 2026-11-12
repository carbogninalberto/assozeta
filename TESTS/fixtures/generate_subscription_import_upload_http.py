"""Author the subscription/import/upload HTTP sequence (subscription-import-upload.md).

subscription_import_upload (BE/application/views/subscriptions_views.py),
@api_view(['POST']), IsAuthenticated, registry association.members.create:

1. scoped_queryset(SportAssociation.objects).filter(user=request.user).first():
   None (athletes, owners without a live association) is Response(status=401)
   (no body, no challenge).
2. data.keys() (a non-dict body raises: 500).
3. 'associates_file': upload_and_store_file (Document.objects.create(filename=
   file.name) audited and indexed to the actor's association, then
   default_storage.save of the bytes under
   subscription-import/<association id>/<timestamp>/<id>/<name>), then
   get_pandas_df_from_file: '.csv' (lower-cased path) is pandas.read_csv(file,
   delimiter=';'); Excel extensions use read_excel; anything else raises
   TypeError (500 after the writes); pandas errors are 500 after the writes.
   columns.tolist(), then auto_map_columns(columns, map) on the request's own
   copy of SubscriptionMaps.MAP: for each column the first key with the
   smallest edit distance of the lower-cased texts below len(column) * 0.25
   (each key once per request) is set to the column. The answer is
   {'columns': [...], 'map': map, 'document_id': id}.
   An Excel suffix reads the bytes with pandas.read_excel(engine="openpyxl")
   whatever the suffix says (a failure falls back to xlrd, which is not
   installed: 500 after the writes). Number-like text becomes numbers, a
   column with a missing value is float, date cells are datetimes.
   A file above SUBSCRIPTION_IMPORT_MAX_UPLOAD_BYTES is 413 {'msg': ...} before
   any write. Once the upload is stored, remove_expired_uploads removes
   the association's import uploads older than
   SUBSCRIPTION_IMPORT_RETENTION_HOURS: Document.delete() (audited) and the
   stored objects.
4. 'document_id' and 'map': is_valid_uuid(document_id) (400 [str(exception)]),
   Document.objects.filter(document_id=...).first() (None: ValueError, 500),
   default_storage.exists of its path in the association's import folder
   (False: the same ValueError, 500: a document that is no import upload of
   the association), the same read of the stored object (500 when it fails),
   then the import. The columns the map gives to tax code, CAP, phone and
   card number are read as text (text_columns: numbers as their str(), text
   as written). manage_import_status('start') deletes the association's
   IMPORTING statuses and creates one; inside transaction.atomic()
   process_import_rows walks the rows and the map's keys in order
   (KEY_TO_FIELD_ASSOCIATE[key without 'tutore']; the cell, stripped; 'nan' is
   None; birth and membership dates through import_date to %d/%m/%Y or None: a
   date cell, or text read with the day first unless it starts with the year;
   a missing 'sesso' is 'A'; a true 'scadenza certificato medico' creates a
   MedicalCertificate for request.user and saves its expiration, or deletes it
   when the text is not a date; a date cell of any other field is stored as
   %d/%m/%Y and an empty cell of a date column is None; a None value takes
   data['default'][key]), fills born_city from the tax code
   (codicefiscale.decode, once more with the expected control character),
   applies unmapped_static(_tutor), and validates the mandatory fields and the
   tax code pattern; AssociateImportDraft.objects.bulk_create (no audit
   entries). 'end' deletes the status. An exception rolls the rows back (no
   certificate, no draft) and answers 400 {'msg': ...}; the upload stays.
   Otherwise the upload is removed (Document.delete() audited, then its
   stored object) and the answer is 200 {'columns', 'map'}, the map with every
   key None.
5. Otherwise TypeError: 500.

Run with ``python3 -I`` after the message-mutations fixture.
"""
import base64
import io
import json
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

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
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
# AssociateImportDraftStatus rows: status_type 1 is IMPORTING, 2 APPROVING.
# SUBSCRIPTION_IMPORT_MAX_UPLOAD_BYTES of the run (both servers).
LIMIT = 16384
STATUSES = {
    'own_importing': {'id': 'a1000000-0000-4000-8000-000000984101', 'association': OWN, 'doing': True, 'type': 1, 'created': '2026-05-01T08:00:00Z'},
    'own_importing_done': {'id': 'a1000000-0000-4000-8000-000000984102', 'association': OWN, 'doing': False, 'type': 1, 'created': '2026-05-02T08:00:00Z'},
    'own_approving': {'id': 'a1000000-0000-4000-8000-000000984103', 'association': OWN, 'doing': True, 'type': 2, 'created': '2026-05-03T08:00:00Z'},
    'foreign_importing': {'id': 'a1000000-0000-4000-8000-000000984104', 'association': FOREIGN, 'doing': True, 'type': 1, 'created': '2026-05-04T08:00:00Z'},
}


def distance(left, right):
    previous = list(range(len(right) + 1))
    for i, a in enumerate(left, 1):
        current = [i]
        for j, b in enumerate(right, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (a != b)))
        previous = current
    return previous[-1]


def auto_map(columns):
    """The request's map: every key, set to the column auto_map_columns chooses."""
    mapped = dict(MAP)
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
            mapped[chosen] = column
    return mapped


def multipart(fields=(), files=()):
    parts = []
    for name, value in fields:
        parts.append(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, filename, content, kind in files:
        parts.append(f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f'Content-Type: {kind}\r\n\r\n'.encode() + content + b'\r\n')
    return b''.join(parts) + f'--{BOUNDARY}--\r\n'.encode()


def add(name, actor, status, response, *, method='POST', body=None, raw=None, binary=None, headers=None, effect=None):
    case = {'name': f'subscription-import-upload-{name}', 'actor': actor, 'method': method, 'path': PATH, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw is not None:
        # The contract sends raw_body.encode(): every authored body is UTF-8.
        case['raw_body'], case['content_type'] = raw.decode('utf-8'), f'multipart/form-data; boundary={BOUNDARY}'
    if binary is not None:
        case['raw_b64'], case['content_type'] = base64.b64encode(binary).decode(), f'multipart/form-data; boundary={BOUNDARY}'
    cases.append(case)


def upload(name, actor, filename, content, *, status=200, columns=None, kind='text/csv', content_type='text/csv', headers=None,
           session=None, keep=None, association='own_association', expired=(), texts=None):
    """A stored upload: the Document (audited) and its object in the association's import folder, then the columns or the 500 page.
    expired: the seeded uploads past their retention that this upload removes once stored.
    texts: str() of each column where a label is not text (auto_map_columns maps the text)."""
    effect = {'actor': actor, 'session': session, 'filename': filename, 'body_b64': base64.b64encode(content).decode(),
              'content_type': content_type, 'association': association, 'expired': list(expired)}
    if keep:
        # The object stays for the imports that name '$document:<keep>'; the case that lists it in 'release' removes it.
        effect['keep'] = keep
    if status == 200:
        response = {'columns': columns, 'map': auto_map(texts or columns), 'document_id': '$new'}
    else:
        response = SERVER
    try:
        raw = multipart(files=[('associates_file', filename, content, kind)])
        raw.decode('utf-8')
        add(name, actor, status, response, raw=raw, headers=headers, effect=effect)
    except UnicodeDecodeError:
        add(name, actor, status, response, binary=raw, headers=headers, effect=effect)


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
# t4l1: a file sent as document_id is uuid.UUID(file), the in-memory upload class's AttributeError.
add('file-document-id', 'owner', 400, ["'InMemoryUploadedFile' object has no attribute 'replace'"],
    raw=multipart(fields=[('map', '{}')], files=[('document_id', 'soci.csv', b'Nome\nAda\n', 'text/csv')]))
add('unknown-document-server-error', 'owner', 500, SERVER, body={'document_id': IDS['missing'], 'map': {'nome': 'Nome'}})
add('unknown-document-form-server-error', 'full_collaborator', 500, SERVER,
    raw=multipart(fields=[('document_id', IDS['missing']), ('map', '{}')]))

# Uploads. The first maps every key (fuzzy, duplicated and unmatched columns included); each answer maps its own columns.
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
    'Scadenza certificato med', 'Data tesseramento', 'Scadenza tesseramento', 'Numero tessera', 'Note', 'Nome.1'],
    expired=['own'])
assert all(value is not None for value in cases[-1]['response']['map'].values()), cases[-1]['response']['map']
upload('csv-maps-its-own-columns', 'owner', 'altri.csv', SIMPLE, columns=['Nome', 'Cognome'])
upload('csv-bom-crlf-quoted-unnamed-duplicates', 'full_collaborator', 'Esportazione.CSV',
       b'\xef\xbb\xbf"Nome";;"Note;varie";Note;\r\nMario;;x;y;\r\n"Anna\r\nMaria";;;;\r\n', content_type='text/csv',
       columns=['Nome', 'Unnamed: 1', 'Note;varie', 'Note', 'Unnamed: 4'])
# t4l1: columns with a dotted capital I or a capital sigma are lower-cased as str.lower() does ('i' with a combining dot, the
# final sigma) before the distance to each key is measured.
upload('csv-dotted-i-and-sigma-columns', 'owner', 'greco.csv', 'NOMİ;COGNOMİ;ΟΔΟΣ;ΣΕΣΣΟ;İndirizzo\nA;B;C;D;E\n'.encode(),
       columns=['NOMİ', 'COGNOMİ', 'ΟΔΟΣ', 'ΣΕΣΣΟ', 'İndirizzo'])
upload('csv-header-only', 'admin', 'vuoto.csv', b'Telefono;Altro\n', headers=ADMIN_OWNER, session='admin_owner',
       columns=['Telefono', 'Altro'])
upload('csv-implicit-index', 'owner', 'indice.csv', b'a;b\n1;2;3\n4;5;6\n', columns=['a', 'b'])
upload('csv-blank-lines-whitespace', 'owner', 'spazi.csv', b'\n  \n a ;b\n\t\n1;2\n', columns=[' a ', 'b'])
upload('csv-too-many-fields-server-error', 'owner', 'errore.csv', b'a;b\n1;2\n1;2;3\n', status=500)
upload('csv-unterminated-quote-server-error', 'owner', 'virgolette.csv', b'a;b\n"1;2\n', status=500)
upload('csv-empty-server-error', 'owner', 'nulla.csv', b'\n\n', status=500)
upload('text-extension-server-error', 'owner', 'soci.txt', SIMPLE, status=500, kind='text/plain', content_type='text/plain')
# The size limit: a file of exactly the limit is stored, one byte more is refused before any write.
AT_LIMIT = b'Nome;Cognome\n' + b'M' * (LIMIT - 20) + b';Rossi\n'
assert len(AT_LIMIT) == LIMIT
upload('csv-at-the-limit', 'owner', 'limite.csv', AT_LIMIT, columns=['Nome', 'Cognome'])
add('csv-above-the-limit', 'owner', 413, {'msg': f'Il file supera la dimensione massima consentita ({LIMIT} byte).'},
    raw=multipart(files=[('associates_file', 'grande.csv', AT_LIMIT + b'x', 'text/csv')]))


# --- Excel workbooks (stdlib only: fixed parts, deterministic archive) -----------------------------------------
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
RELS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PACKAGE = 'http://schemas.openxmlformats.org/package/2006'
DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
# cellXfs: 0 General, 1 a date (numFmtId 14).
STYLES = (f'{DECLARATION}<styleSheet xmlns="{MAIN}"><fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
          '<fills count="1"><fill><patternFill patternType="none"/></fill></fills>'
          '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
          '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
          '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
          '<xf numFmtId="21" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
          '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def letters(column):
    text = ''
    while column:
        column, rest = divmod(column - 1, 26)
        text = chr(65 + rest) + text
    return text


def archive(parts):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, text in parts:
            bundle.writestr(zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0)), text.encode('utf-8'), zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def workbook(rows):
    """A one-sheet .xlsx. A cell is None (absent), a str (shared string), an int or float (number) or
    ('date', serial): a number with the date style (days since 1899-12-30), ('time', fraction): a number with the
    h:mm:ss style, True/False (a boolean cell), ('error', text): an error cell."""
    strings, body = [], []
    for number, row in enumerate(rows, 1):
        cells = ''
        for column, value in enumerate(row, 1):
            reference = f'{letters(column)}{number}'
            if value is None:
                continue
            if isinstance(value, str):
                if value not in strings:
                    strings.append(value)
                cells += f'<c r="{reference}" t="s"><v>{strings.index(value)}</v></c>'
            elif isinstance(value, bool):
                cells += f'<c r="{reference}" t="b"><v>{int(value)}</v></c>'
            elif isinstance(value, tuple) and value[0] == 'error':
                cells += f'<c r="{reference}" t="e"><v>{value[1]}</v></c>'
            elif isinstance(value, tuple):
                cells += f'<c r="{reference}" s="{2 if value[0] == "time" else 1}"><v>{value[1]}</v></c>'
            else:
                cells += f'<c r="{reference}"><v>{value!r}</v></c>'
        body.append(f'<row r="{number}">{cells}</row>')
    shared = ''.join(f'<si><t xml:space="preserve">{escape(text)}</t></si>' for text in strings)
    return archive([
        ('[Content_Types].xml', f'{DECLARATION}<Types xmlns="{PACKAGE}/content-types">'
         '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
         '<Default Extension="xml" ContentType="application/xml"/>'
         f'<Override PartName="/xl/workbook.xml" ContentType="{XLSX}.main+xml"/>'
         '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
         '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
         '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
         '</Types>'),
        ('_rels/.rels', f'{DECLARATION}<Relationships xmlns="{PACKAGE}/relationships">'
         f'<Relationship Id="rId1" Type="{RELS}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        ('xl/workbook.xml', f'{DECLARATION}<workbook xmlns="{MAIN}" xmlns:r="{RELS}">'
         '<sheets><sheet name="Soci" sheetId="1" r:id="rId1"/></sheets></workbook>'),
        ('xl/_rels/workbook.xml.rels', f'{DECLARATION}<Relationships xmlns="{PACKAGE}/relationships">'
         f'<Relationship Id="rId1" Type="{RELS}/worksheet" Target="worksheets/sheet1.xml"/>'
         f'<Relationship Id="rId2" Type="{RELS}/styles" Target="styles.xml"/>'
         f'<Relationship Id="rId3" Type="{RELS}/sharedStrings" Target="sharedStrings.xml"/></Relationships>'),
        ('xl/worksheets/sheet1.xml', f'{DECLARATION}<worksheet xmlns="{MAIN}"><sheetData>{"".join(body)}</sheetData></worksheet>'),
        ('xl/styles.xml', STYLES),
        ('xl/sharedStrings.xml', f'{DECLARATION}<sst xmlns="{MAIN}">{shared}</sst>'),
    ])


ANNA, WRONG_CONTROL = 'BNCNNA90C42F205F', 'RSSMRA80A01H501X'  # Milano; Roma once the control character is corrected
COLUMNS = ['Nome', 'COGNOME', 'Sesso', 'Codice fiscal', 'Data di Nascita', 'Città di nascita', 'Indirizzo', 'Città di residenza', 'CAP',
           'E-mail', 'Telefono', 'Scadenza certificato med', 'Data tesseramento', 'Scadenza tesseramento', 'Numero tessera', 'Note']
# Serials: 29221 is 1980-01-01, 46266 is 2026-09-01, 46387 is 2026-12-31.
MEMBERS = workbook([
    COLUMNS,
    ['Mario', 'Rossi', 'M', 'RSSMRA80A01H501U', ('date', 29221), 'Roma', 'Via Roma 1', 'Roma', '00100', 'mario@example.test',
     '3330000000', '31/12/2026', ('date', 46266), '31/08/2027', 42, 'x'],
    ['Anna', 'Bianchi', 'F', ANNA, '03/02/1990', None, ' Via Milano 2 ', 'Milano', '20100', 'anna@example.test', None, 'abc', None, None,
     43, None],
    ['Luca', 'Verdi', None, 'XXX', None, None, None, None, None, None, '02 1234567', None, None, None, None, None],
    ['Ugo', 'Neri', ' m ', WRONG_CONTROL, '1975-12-25', None, 'Via Po 3', 'Torino', '10100', None, None, '2027-01-15', None, None, None,
     None],
])
upload('xlsx-members', 'owner', 'Soci 2026.xlsx', MEMBERS, columns=COLUMNS, kind=XLSX, content_type=XLSX, keep='members')
# Serial 46451 is 2027-03-05.
upload('xls-suffix-date-cells', 'full_collaborator', 'date.XLS', workbook([
    ['Nome', 'Scadenza certificato med'], ['Mario', ('date', 46451)], ['Anna', None]]),
    columns=['Nome', 'Scadenza certificato med'], kind='application/vnd.ms-excel', content_type='application/vnd.ms-excel', keep='dated')
upload('xlsx-header-only-unnamed-duplicates', 'admin', 'vuoto.xlsx', workbook([['Telefono', None, 'Altro', 'Altro', None, 'Fine']]),
       headers=ADMIN_OWNER, session='admin_owner', columns=['Telefono', 'Unnamed: 1', 'Altro', 'Altro.1', 'Unnamed: 4', 'Fine'],
       kind=XLSX, content_type=XLSX, keep='header')
upload('xlsx-empty-sheet', 'owner', 'niente.xlsx', workbook([]), columns=[], kind=XLSX, content_type=XLSX)
upload('xlsx-not-a-workbook-server-error', 'owner', 'testo.xlsx', SIMPLE, status=500, kind=XLSX, content_type=XLSX, keep='broken')
upload('ods-archive-server-error', 'owner', 'soci.ods', archive([('mimetype', 'application/vnd.oasis.opendocument.spreadsheet'),
                                                                 ('content.xml', '<office/>')]),
       status=500, kind='application/vnd.oasis.opendocument.spreadsheet', content_type='application/vnd.oasis.opendocument.spreadsheet')
# The same workbook uploaded for the import of another association.
upload('xlsx-members-other-association', 'foreign_owner', 'Soci 2026.xlsx', MEMBERS, columns=COLUMNS, kind=XLSX, content_type=XLSX,
       keep='other', association='foreign_association', expired=['foreign'])
# A second upload of the workbook: an import removes the upload it reads.
upload('xlsx-members-again', 'full_collaborator', 'Soci 2026.xlsx', MEMBERS, columns=COLUMNS, kind=XLSX, content_type=XLSX, keep='again')
# Cells of every type. Serials: 46024 is 2026-01-02, 46085 is 2026-03-04, 29252 is 1980-02-01.
TYPED_COLUMNS = ['Nome', 'Indirizzo', 'CAP', 'Telefono', 'Tessera', 'Nascita', 'Tesseramento', 'Scadenza', 'Certificato', 'Codice']
TYPED = workbook([
    TYPED_COLUMNS,
    ['Mario', ('date', 46024), ('date', 46085), 3331234567, 7, '03/02/1990', '1/9/2026', '2027-08-31', '05/03/2027', 'RSSMRA80A01H501U'],
    ['Anna', 'Via Po 3', 20100, '0612345678', '0042', '1975-12-25', '25/12/2026', 'domani', '2027-03-05 00:00:00', 12345],
    ['Luca', None, 12.5, None, None, ('date', 29252), '12/25/2026', None, None, None],
    ['Ugo', 5, '00100', 'True', 'NA', '31/02/1990', None, None, 'abc', None],
])
upload('xlsx-typed-cells', 'owner', 'tipi.xlsx', TYPED, columns=TYPED_COLUMNS, kind=XLSX, content_type=XLSX, keep='typed')
# Boolean cells and a time-of-day cell.
KINDS_COLUMNS = ['Nome', 'Attivo', 'Orario', 'Numero']
upload('xlsx-booleans-times', 'owner', 'altri-tipi.xlsx', workbook([
    KINDS_COLUMNS, ['Mario', True, None, 7], ['Anna', False, None, 5]]),
    columns=KINDS_COLUMNS, kind=XLSX, content_type=XLSX, keep='kinds')
upload('xlsx-time-of-day', 'owner', 'orari.xlsx', workbook([
    KINDS_COLUMNS, ['Mario', True, ('time', 0.4375), 1], ['Anna', False, None, 5]]),
    columns=KINDS_COLUMNS, kind=XLSX, content_type=XLSX, keep='times')
# A CSV for the import: text, whole numbers, decimals, booleans, missing values and codes with leading zeros.
CSV_COLUMNS = ['Nome', 'Cognome', 'Codice', 'CAP', 'Telefono', 'Tessera', 'Nascita', 'Sesso', 'Attivo', 'Quota', 'Note', 'Anno']
CSV_MEMBERS = ('Nome;Cognome;Codice;CAP;Telefono;Tessera;Nascita;Sesso;Attivo;Quota;Note;Anno\n'
               'Mario;Rossi;RSSMRA80A01H501U;00100;3331234567;7;03/02/1990;M;True;12.5;x;1990\n'
               f'Anna;Bianchi;{ANNA};20100;;0042;1975-12-25;F;False;3;;1975\n'
               ' Luca ;Verdi;XXX;;02 1234567;;31/02/1990;;True;;nan;1980\n').encode()
upload('csv-members', 'owner', 'soci-import.csv', CSV_MEMBERS, columns=CSV_COLUMNS, keep='csv')
upload('csv-infinite-number', 'owner', 'infinito.csv', b'Nome;Quota\nMario;inf\nAnna;1.5\n', columns=['Nome', 'Quota'], keep='infinite')
upload('csv-whole-numbers', 'owner', 'interi.csv', b'Nome;CAP;Attivo\n1;00100;True\n2;20100;False\n', columns=['Nome', 'CAP', 'Attivo'],
       keep='csvnumbers')
upload('xlsx-whole-numbers', 'owner', 'numeri.xlsx', workbook([['Nome', 'CAP'], [1, 100], [2, 20100]]), columns=['Nome', 'CAP'], kind=XLSX,
       content_type=XLSX, keep='numbers')

# --- The import of a stored workbook ---------------------------------------------------------------------------
pending_statuses = ['own_importing', 'own_importing_done']
# A Document seeded with its workbook under <timestamp>/<id>/<name>, the folder of the other document uploads; the
# runner registers it as '$document:stored'.
STORED = {'id': 'a1000000-0000-4000-8000-000000984201', 'token': 'a1000000-0000-4000-8000-000000984202', 'filename': 'elenco.xlsx',
          'created': '2026-04-01T08:00:00Z', 'body_b64': base64.b64encode(MEMBERS).decode(), 'content_type': XLSX}
# Import uploads seeded past their retention, each in its association's import folder: the association's next upload
# removes them.
EXPIRED = {
    'own': {'id': 'a1000000-0000-4000-8000-000000984301', 'token': 'a1000000-0000-4000-8000-000000984302', 'filename': 'vecchio.csv',
            'created': '2026-04-02T08:00:00.250000Z', 'association': OWN, 'body_b64': base64.b64encode(SIMPLE).decode(),
            'content_type': 'text/csv'},
    'foreign': {'id': 'a1000000-0000-4000-8000-000000984303', 'token': 'a1000000-0000-4000-8000-000000984304', 'filename': 'vecchio.xlsx',
                'created': '2026-04-03T08:00:00Z', 'association': FOREIGN, 'body_b64': base64.b64encode(MEMBERS).decode(),
                'content_type': XLSX},
}
FAILED = {'msg': 'Importazione non riuscita. Controlla i dati e riprova.'}


def imported(name, actor, document, body, *, columns, steps=(), drafts=(), headers=None, session=None, form=False, failed=False,
             unpadded=None, unrendered=False):
    """An import: the association's IMPORTING statuses go, one is created and deleted around the rows.
    steps: the certificates in order, ('kept', label, 'YYYY-MM-DD') or ('deleted',). drafts: [(data, valid)] with
    '$mc:<label>' for a kept certificate's id. A 200 removes the upload it read (the Document and its object).
    failed: the rows raise: 400, no certificate and no draft, and the upload stays.
    unpadded: (steps, drafts) where the C library writes a year below 1000 without padding (glibc).
    unrendered: the import completes and the answer cannot be rendered: the 500 page."""
    assert not (failed and (steps or drafts))
    effect = {'kind': 'import', 'actor': actor, 'session': session, 'statuses': list(pending_statuses), 'steps': [list(step) for step in steps],
              'drafts': [{'data': data, 'valid': valid} for data, valid in drafts], 'document': document, 'failed': failed}
    if unpadded:
        effect['unpadded'] = {'steps': [list(step) for step in unpadded[0]],
                              'drafts': [{'data': data, 'valid': valid} for data, valid in unpadded[1]]}
    pending_statuses.clear()
    status = 400 if failed else 200
    response = FAILED if failed else {'columns': columns, 'map': dict(MAP)}  # no upload in this request: every key None
    if unrendered:
        status, response = 500, SERVER
    if form:
        add(name, actor, status, response, raw=multipart(fields=[('document_id', f'$document:{document}'), *body]), headers=headers,
            effect=effect)
    else:
        add(name, actor, status, response, body={'document_id': f'$document:{document}', **body}, headers=headers, effect=effect)


HEADER_COLUMNS = ['Telefono', 'Unnamed: 1', 'Altro', 'Altro.1', 'Unnamed: 4', 'Fine']
# No rows: only the statuses move, whatever the map is, and the upload is removed.
imported('header-only-statuses', 'admin', 'header', {'map': ['non', 'un', 'oggetto'], 'default': 5}, columns=HEADER_COLUMNS,
         headers=ADMIN_OWNER, session='admin_owner')

UI_MAP = {**{key: None for key in KEYS}, 'nome': 'Nome', 'cognome': 'COGNOME', 'sesso': 'Sesso', 'codice fiscale': 'Codice fiscal',
          'data di nascita': 'Data di Nascita', 'città di nascita': 'Città di nascita', 'indirizzo': 'Indirizzo',
          'città di residenza': 'Città di residenza', 'cap': 'CAP', 'email': 'E-mail', 'telefono': 'Telefono',
          'scadenza certificato medico': 'Scadenza certificato med', 'data tesseramento': 'Data tesseramento',
          'data scadenza tesseramento': 'Scadenza tesseramento', 'numero tessera': 'Numero tessera'}
TUTOR = {'sex': 'A'}  # 'sesso tutore' without a column
# Imports that raise: nothing of the rows stays and the upload can be imported again.
# A key outside KEY_TO_FIELD_ASSOCIATE raises at the first row, after that row's certificate.
imported('unknown-key-fails', 'owner', 'members', {'map': {'scadenza certificato medico': 'Scadenza certificato med', 'tessera oro': 'Note'}},
         columns=COLUMNS, failed=True)
imported('list-map-fails', 'owner', 'members', {'map': []}, columns=COLUMNS, failed=True)
imported('form-map-fails', 'owner', 'members', [('map', '{"nome": "Nome"}')], columns=COLUMNS, form=True, failed=True)
# A tax code that is not text raises at the pattern check of the first complete row.
imported('number-tax-code-fails', 'owner', 'members', {
    'map': {key: UI_MAP[key] for key in ('nome', 'cognome', 'sesso', 'data di nascita', 'città di nascita', 'indirizzo',
                                         'città di residenza', 'cap')} | {'codice fiscale': None},
    'default': {'codice fiscale': 12345}}, columns=COLUMNS, failed=True)
# Defaults for missing values, an absent column, tutor keys and the static values.
STATIC = {'address': 'Sede', 'extra': [1, {'a': None}]}
imported('defaults-tutor-static', 'full_collaborator', 'members', {
    'map': {'nome': 'Nome', 'cognome': 'COGNOME', 'codice fiscale': 'Codice fiscal', 'sesso': None, 'città di nascita': None,
            'nome tutore': 'Note', 'codice fiscale tutore': 'Codice fiscal', 'telefono': 'Colonna assente'},
    'default': {'città di nascita': 'Trento', 'sesso': 'X', 'telefono': '000'},
    'unmapped_static': STATIC, 'unmapped_static_tutor': {'email': 'tutore@example.test'}}, columns=COLUMNS,
    drafts=[({'associate': {'first_name': first, 'last_name': last, 'tax_code': code, 'sex': 'A', 'born_city': 'Trento', 'phone': '000', **STATIC},
              'associate_tutor': {**tutor, 'tax_code': code, 'email': 'tutore@example.test'}}, False)
            for first, last, code, tutor in (('Mario', 'Rossi', 'RSSMRA80A01H501U', {'first_name': 'x', 'born_city': 'Roma'}),
                                             ('Anna', 'Bianchi', ANNA, {'born_city': 'Milano'}), ('Luca', 'Verdi', 'XXX', {}),
                                             ('Ugo', 'Neri', WRONG_CONTROL, {'born_city': 'Roma'}))])
# The wizard's request on the second upload: every key, most tutor keys unmapped. CAP and the card number are read as
# text: the CAP keeps its zeros.
imported('wizard-map', 'owner', 'again', {'map': UI_MAP, 'default': {}}, columns=COLUMNS,
         steps=[('kept', 'mario', '2026-12-31'), ('deleted',), ('kept', 'ugo', '2027-01-15')],
         drafts=[
             ({'associate': {'first_name': 'Mario', 'last_name': 'Rossi', 'sex': 'M', 'tax_code': 'RSSMRA80A01H501U', 'born_date': '01/01/1980',
                             'born_city': 'Roma', 'address': 'Via Roma 1', 'address_city': 'Roma', 'address_cap': '00100',
                             'email': 'mario@example.test', 'phone': '3330000000', 'certificate_expiring_date': '31/12/2026',
                             'membership_start_date': '01/09/2026', 'membership_end_date': '31/08/2027', 'subscription_number': '42'},
               'associate_tutor': TUTOR,
               'medical_certificate': {'medical_id': '$mc:mario', 'filename': None, 'certificate_expring_date': '31/12/2026'}}, True),
             # 03/02/1990 has the day first; the birth city comes from the tax code; 'abc' is no date.
             ({'associate': {'first_name': 'Anna', 'last_name': 'Bianchi', 'sex': 'F', 'tax_code': ANNA, 'born_date': '03/02/1990',
                             'born_city': 'Milano', 'address': 'Via Milano 2', 'address_city': 'Milano', 'address_cap': '20100',
                             'email': 'anna@example.test', 'certificate_expiring_date': 'abc', 'subscription_number': '43'},
               'associate_tutor': TUTOR}, True),
             ({'associate': {'first_name': 'Luca', 'last_name': 'Verdi', 'sex': 'A', 'tax_code': 'XXX', 'phone': '02 1234567'},
               'associate_tutor': TUTOR}, False),
             ({'associate': {'first_name': 'Ugo', 'last_name': 'Neri', 'sex': 'm', 'tax_code': WRONG_CONTROL, 'born_date': '25/12/1975',
                             'born_city': 'Roma', 'address': 'Via Po 3', 'address_city': 'Torino', 'address_cap': '10100',
                             'certificate_expiring_date': '2027-01-15'},
               'associate_tutor': TUTOR,
               'medical_certificate': {'medical_id': '$mc:ugo', 'filename': None, 'certificate_expring_date': '15/01/2027'}}, True),
         ])
# Date cells in the certificate column: the certificate is saved and the draft stores the date as day/month/year;
# an empty cell is no value (no certificate). The upload was the collaborator's, for the same association.
imported('date-cell-certificates', 'owner', 'dated', {'map': {'nome': 'Nome', 'scadenza certificato medico': 'Scadenza certificato med'}},
         columns=['Nome', 'Scadenza certificato med'], steps=[('kept', 'mario', '2027-03-05')],
         drafts=[({'associate': {'first_name': 'Mario', 'certificate_expiring_date': '05/03/2027'}, 'associate_tutor': {},
                   'medical_certificate': {'medical_id': '$mc:mario', 'filename': None, 'certificate_expring_date': '05/03/2027'}}, False),
                 ({'associate': {'first_name': 'Anna'}, 'associate_tutor': {}}, False)])
# Cells of every type. Columns of codes are text (numbers as written, the NA text missing); a date cell of a text
# field is day/month/year; date text has the day first unless it starts with the year or its second number is no month;
# an impossible date and a word are no date.
imported('typed-cells', 'owner', 'typed', {'map': {
    'nome': 'Nome', 'indirizzo': 'Indirizzo', 'cap': 'CAP', 'telefono': 'Telefono', 'numero tessera': 'Tessera', 'data di nascita': 'Nascita',
    'data tesseramento': 'Tesseramento', 'data scadenza tesseramento': 'Scadenza', 'scadenza certificato medico': 'Certificato',
    'codice fiscale': 'Codice', 'data di nascita tutore': 'Nascita', 'cap tutore': 'CAP'}}, columns=TYPED_COLUMNS,
    steps=[('kept', 'mario', '2027-03-05'), ('kept', 'anna', '2027-03-05'), ('deleted',)],
    drafts=[
        ({'associate': {'first_name': 'Mario', 'address': '02/01/2026', 'address_cap': '04/03/2026', 'phone': '3331234567',
                        'subscription_number': '7', 'born_date': '03/02/1990', 'membership_start_date': '01/09/2026',
                        'membership_end_date': '31/08/2027', 'certificate_expiring_date': '05/03/2027', 'tax_code': 'RSSMRA80A01H501U',
                        'born_city': 'Roma'},
          'associate_tutor': {'born_date': '03/02/1990', 'address_cap': '04/03/2026'},
          'medical_certificate': {'medical_id': '$mc:mario', 'filename': None, 'certificate_expring_date': '05/03/2027'}}, False),
        ({'associate': {'first_name': 'Anna', 'address': 'Via Po 3', 'address_cap': '20100', 'phone': '0612345678',
                        'subscription_number': '0042', 'born_date': '25/12/1975', 'membership_start_date': '25/12/2026',
                        'certificate_expiring_date': '2027-03-05 00:00:00', 'tax_code': '12345'},
          'associate_tutor': {'born_date': '25/12/1975', 'address_cap': '20100'},
          'medical_certificate': {'medical_id': '$mc:anna', 'filename': None, 'certificate_expring_date': '05/03/2027'}}, False),
        ({'associate': {'first_name': 'Luca', 'address_cap': '12.5', 'born_date': '01/02/1980', 'membership_start_date': '25/12/2026'},
          'associate_tutor': {'born_date': '01/02/1980', 'address_cap': '12.5'}}, False),
        ({'associate': {'first_name': 'Ugo', 'address': 5, 'address_cap': '00100', 'phone': 'True', 'certificate_expiring_date': 'abc'},
          'associate_tutor': {'address_cap': '00100'}}, False),
    ])
# A sheet of whole numbers: the CAP is text, the other column keeps its numbers.
imported('whole-numbers', 'owner', 'numbers', {'map': {'nome': 'Nome', 'cap': 'CAP'}}, columns=['Nome', 'CAP'],
         drafts=[({'associate': {'first_name': 1, 'address_cap': '100'}, 'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 2, 'address_cap': '20100'}, 'associate_tutor': {}}, False)])
# Date text in every form dateutil reads, with the day first unless the text starts with the year: dashes, dots, month
# names, a time after the date, and a whole number (YYYYMMDD read as year, day, month); a word that is no date is no value.
DATE_FORMS_COLUMNS = ['Nome', 'Nascita', 'Tesseramento']
upload('xlsx-date-forms', 'owner', 'date.xlsx', workbook([
    DATE_FORMS_COLUMNS,
    ['Dina', '03-02-1990', '1.9.2026'],
    ['Aldo', 'March 4, 1985', '4 Sep 2026'],
    ['Ines', '1985-04-03 10:30', 'giovedì prossimo'],
    ['Elio', 19900203, '2026/09/01'],
    ['Olga', '13-02-1990', '02-13-2026'],
]), columns=DATE_FORMS_COLUMNS, kind=XLSX, content_type=XLSX, keep='date_forms')
imported('date-forms', 'owner', 'date_forms', {'map': {'nome': 'Nome', 'data di nascita': 'Nascita', 'data tesseramento': 'Tesseramento'}},
         columns=DATE_FORMS_COLUMNS,
         drafts=[({'associate': {'first_name': 'Dina', 'born_date': '03/02/1990', 'membership_start_date': '01/09/2026'},
                   'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 'Aldo', 'born_date': '04/03/1985', 'membership_start_date': '04/09/2026'},
                   'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 'Ines', 'born_date': '03/04/1985'}, 'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 'Elio', 'born_date': '02/03/1990', 'membership_start_date': '01/09/2026'},
                   'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 'Olga', 'born_date': '13/02/1990', 'membership_start_date': '13/02/2026'},
                   'associate_tutor': {}}, False)])
# A stored CSV. The columns of codes are text as written; the others are read_csv's values: whole numbers, decimals and
# booleans; a missing value is no value; date text has the day first.
imported('csv-import-members', 'owner', 'csv', {'map': {
    'nome': 'Nome', 'cognome': 'Cognome', 'codice fiscale': 'Codice', 'cap': 'CAP', 'telefono': 'Telefono', 'numero tessera': 'Tessera',
    'data di nascita': 'Nascita', 'sesso': 'Sesso', 'indirizzo': 'Attivo', 'città di residenza': 'Quota', 'email': 'Note',
    'città di nascita': 'Anno', 'data tesseramento': 'Attivo'}}, columns=CSV_COLUMNS,
    drafts=[
        ({'associate': {'first_name': 'Mario', 'last_name': 'Rossi', 'tax_code': 'RSSMRA80A01H501U', 'address_cap': '00100',
                        'phone': '3331234567', 'subscription_number': '7', 'born_date': '03/02/1990', 'sex': 'M', 'address': True,
                        'address_city': 12.5, 'email': 'x', 'born_city': 1990}, 'associate_tutor': {}}, True),
        # False is a value: the membership date keeps it.
        ({'associate': {'first_name': 'Anna', 'last_name': 'Bianchi', 'tax_code': ANNA, 'address_cap': '20100', 'subscription_number': '0042',
                        'born_date': '25/12/1975', 'sex': 'F', 'address': False, 'address_city': 3.0, 'born_city': 1975,
                        'membership_start_date': False}, 'associate_tutor': {}}, True),
        ({'associate': {'first_name': 'Luca', 'last_name': 'Verdi', 'tax_code': 'XXX', 'phone': '02 1234567', 'sex': 'A', 'address': True,
                        'born_city': 1980}, 'associate_tutor': {}}, False),
    ])
# A frame of numbers and booleans only: the CAP stays text.
imported('csv-import-whole-numbers', 'owner', 'csvnumbers', {'map': {'nome': 'Nome', 'cap': 'CAP', 'indirizzo': 'Attivo'}},
         columns=['Nome', 'CAP', 'Attivo'],
         drafts=[({'associate': {'first_name': 1, 'address_cap': '00100', 'address': True}, 'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 2, 'address_cap': '20100', 'address': False}, 'associate_tutor': {}}, False)])
# An infinite number cannot be stored: the import fails.
imported('csv-infinite-number-fails', 'owner', 'infinite', {'map': {'nome': 'Nome', 'indirizzo': 'Quota'}}, columns=['Nome', 'Quota'],
         failed=True)
# The same upload without that column is imported.
imported('csv-import-after-a-failure', 'owner', 'infinite', {'map': {'nome': 'Nome'}}, columns=['Nome', 'Quota'],
         drafts=[({'associate': {'first_name': 'Mario'}, 'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 'Anna'}, 'associate_tutor': {}}, False)])
# Booleans of a workbook are stored as they are.
imported('xlsx-booleans', 'owner', 'kinds', {'map': {'nome': 'Nome', 'indirizzo': 'Attivo', 'email': 'Numero'}},
         columns=KINDS_COLUMNS,
         drafts=[({'associate': {'first_name': 'Mario', 'address': True, 'email': 7}, 'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 'Anna', 'address': False, 'email': 5}, 'associate_tutor': {}}, False)])
# A time-of-day cell cannot be stored: the import fails.
imported('xlsx-time-of-day-fails', 'owner', 'times', {'map': {'nome': 'Nome', 'indirizzo': 'Orario'}}, columns=KINDS_COLUMNS, failed=True)
imported('xlsx-time-of-day-column-left-out', 'owner', 'times', {'map': {'nome': 'Nome', 'indirizzo': 'Attivo', 'email': 'Orario tutore'}},
         columns=KINDS_COLUMNS,
         drafts=[({'associate': {'first_name': 'Mario', 'address': True}, 'associate_tutor': {}}, False),
                 ({'associate': {'first_name': 'Anna', 'address': False}, 'associate_tutor': {}}, False)])
# t6x2: column labels that are not text. The answer lists them as the renderer writes them (numbers, booleans, a date
# as its ISO text); auto_map_columns maps their str(). The import looks the map's values up in the row's Index: a
# number finds the label equal to it (True is 1), text only a text label.
def simple(rows):
    return [({'associate': row, 'associate_tutor': {}}, False) for row in rows]


MIXED_LABELS = ['Nome', 5, True, '2026-01-02T00:00:00', 0.5]
upload('xlsx-mixed-labels', 'owner', 'etichette.xlsx', workbook([
    ['Nome', 5, True, ('date', 46024), 0.5], ['Ada', 'Rossi', 'Via Po 1', 'x', 'Roma'], ['Bea', None, 7, None, None]]),
    columns=MIXED_LABELS, texts=['Nome', '5', 'True', '2026-01-02 00:00:00', '0.5'], kind=XLSX, content_type=XLSX, keep='mixed_labels')
imported('mixed-labels', 'owner', 'mixed_labels', {'map': {
    'nome': 'Nome', 'cognome': 5, 'indirizzo': True, 'città di residenza': 0.5, 'email': '5', 'telefono': 1, 'città di nascita': 5.0,
    'cap': '2026-01-02T00:00:00', 'numero tessera': 2}}, columns=MIXED_LABELS,
    drafts=simple([{'first_name': 'Ada', 'last_name': 'Rossi', 'address': 'Via Po 1', 'address_city': 'Roma', 'phone': 'Via Po 1',
                    'born_city': 'Rossi'},
                   {'first_name': 'Bea', 'address': 7, 'phone': 7}]))
# Whole numbers only: an int64 Index takes numbers without a fraction, never a boolean or text.
upload('xlsx-number-labels', 'owner', 'numeri-colonna.xlsx', workbook([[1, 2, 3], ['Ada', 'Rossi', 'F'], ['Bea', 'Verdi', 'M']]),
       columns=[1, 2, 3], texts=['1', '2', '3'], kind=XLSX, content_type=XLSX, keep='number_labels')
imported('number-labels', 'owner', 'number_labels', {'map': {'nome': 1, 'cognome': 2.0, 'sesso': 3, 'indirizzo': True, 'email': '3',
                                                             'telefono': 4}}, columns=[1, 2, 3],
         drafts=simple([{'first_name': 'Ada', 'last_name': 'Rossi', 'sex': 'F'}, {'first_name': 'Bea', 'last_name': 'Verdi', 'sex': 'M'}]))
# A number with a decimal makes a float64 Index.
upload('xlsx-decimal-labels', 'owner', 'decimali-colonna.xlsx', workbook([[0.5, 2], ['Ada', 'Rossi']]), columns=[0.5, 2.0],
       texts=['0.5', '2.0'], kind=XLSX, content_type=XLSX, keep='decimal_labels')
imported('decimal-labels', 'owner', 'decimal_labels', {'map': {'nome': 0.5, 'cognome': 2, 'email': True, 'indirizzo': '2.0'}},
         columns=[0.5, 2.0], drafts=simple([{'first_name': 'Ada', 'last_name': 'Rossi'}]))
# Booleans only: a bool Index takes booleans, not the numbers equal to them.
upload('xlsx-boolean-labels', 'owner', 'booleani-colonna.xlsx', workbook([[True, False], ['Ada', 'Rossi'], ['Bea', 'Verdi']]),
       columns=[True, False], texts=['True', 'False'], kind=XLSX, content_type=XLSX, keep='boolean_labels')
imported('boolean-labels', 'owner', 'boolean_labels', {'map': {'nome': True, 'cognome': False, 'indirizzo': 1, 'email': 0,
                                                               'telefono': 'True'}}, columns=[True, False],
         drafts=simple([{'first_name': 'Ada', 'last_name': 'Rossi'}, {'first_name': 'Bea', 'last_name': 'Verdi'}]))
# Text and booleans without a whole number: a whole number equal to a label is read as a position.
upload('xlsx-text-boolean-labels', 'owner', 'misti-colonna.xlsx', workbook([
    ['x', False, True], ['Ada', 'Rossi', 'Roma'], ['Bea', 'Verdi', 'Pisa']]), columns=['x', False, True], texts=['x', 'False', 'True'],
    kind=XLSX, content_type=XLSX, keep='text_boolean_labels')
imported('text-boolean-labels', 'owner', 'text_boolean_labels', {'map': {
    'nome': 0, 'cognome': 1, 'città di residenza': True, 'email': 0.0, 'indirizzo': 'x', 'telefono': 2}}, columns=['x', False, True],
    drafts=simple([{'first_name': 'Ada', 'last_name': 'Rossi', 'address_city': 'Roma', 'email': 'Rossi', 'address': 'Ada'},
                   {'first_name': 'Bea', 'last_name': 'Verdi', 'address_city': 'Pisa', 'email': 'Verdi', 'address': 'Bea'}]))
# Dates only make a DatetimeIndex: a number is no label of it.
DATE_LABELS = ['2026-01-02T00:00:00', '2026-03-04T00:00:00']
upload('xlsx-date-labels', 'owner', 'date-colonna.xlsx', workbook([[('date', 46024), ('date', 46085)], ['Ada', 'Rossi']]),
       columns=DATE_LABELS, texts=['2026-01-02 00:00:00', '2026-03-04 00:00:00'], kind=XLSX, content_type=XLSX, keep='date_labels')
imported('date-labels', 'owner', 'date_labels', {'map': {'nome': 46024, 'cognome': True}}, columns=DATE_LABELS, drafts=simple([{}]))
# A time of day as a label is written as its ISO text.
upload('xlsx-time-label', 'owner', 'orario-colonna.xlsx', workbook([['Nome', ('time', 0.4375)], ['Ada', 'x']]),
       columns=['Nome', '10:30:00'], kind=XLSX, content_type=XLSX)
# An error cell as a label is not a number the renderer writes: the 500 page, after the upload is stored and after
# the import is complete.
upload('xlsx-error-label-server-error', 'owner', 'errore-colonna.xlsx', workbook([['Nome', ('error', '#N/A')], ['Ada', 'x']]),
       status=500, kind=XLSX, content_type=XLSX, keep='error_label')
imported('error-label-server-error', 'owner', 'error_label', {'map': {'nome': 'Nome'}}, columns=None, unrendered=True,
         drafts=simple([{'first_name': 'Ada'}]))
# t6x2: dates with a year below 1000, as text and as date cells. strftime writes the year as the C library does: four
# digits where it pads (the first expectation), the digits alone on glibc ('unpadded'), where strptime then refuses the
# certificate's date and the certificate is deleted.
import datetime as _datetime  # noqa: E402


def serial(year, month, day):
    return (_datetime.date(year, month, day) - _datetime.date(1899, 12, 30)).days


EARLY_COLUMNS = ['Nome', 'Nascita', 'Tesseramento', 'Certificato', 'Indirizzo']
upload('xlsx-early-years', 'owner', 'anni.xlsx', workbook([
    EARLY_COLUMNS,
    ['Ada', '02/01/0999', ('date', serial(999, 3, 5)), '05/03/0999', ('date', serial(850, 12, 25))],
    ['Bea', '0999-03-05', None, ('date', serial(999, 3, 5)), None],
]), columns=EARLY_COLUMNS, kind=XLSX, content_type=XLSX, keep='early_years')
EARLY_MAP = {'nome': 'Nome', 'data di nascita': 'Nascita', 'data tesseramento': 'Tesseramento',
             'scadenza certificato medico': 'Certificato', 'indirizzo': 'Indirizzo'}


def early(year, address, certificates):
    ada = {'associate': {'first_name': 'Ada', 'born_date': f'02/01/{year}', 'membership_start_date': f'05/03/{year}',
                         'certificate_expiring_date': '05/03/0999', 'address': f'25/12/{address}'}, 'associate_tutor': {}}
    bea = {'associate': {'first_name': 'Bea', 'born_date': f'05/03/{year}', 'certificate_expiring_date': f'05/03/{year}'},
           'associate_tutor': {}}
    if certificates:
        for label, draft in (('ada', ada), ('bea', bea)):
            draft['medical_certificate'] = {'medical_id': f'$mc:{label}', 'filename': None, 'certificate_expring_date': f'05/03/{year}'}
    return [(ada, False), (bea, False)]


imported('early-years', 'owner', 'early_years', {'map': EARLY_MAP}, columns=EARLY_COLUMNS,
         steps=[('kept', 'ada', '0999-03-05'), ('kept', 'bea', '0999-03-05')], drafts=early('0999', '0850', True),
         unpadded=([('deleted',), ('deleted',)], early('999', '850', False)))
# A file sent as document_id in a request above the in-memory upload size is the temporary upload class ('$pad:<n>' is
# n characters of content).
add('large-file-document-id', 'owner', 400, ["'TemporaryUploadedFile' object has no attribute 'replace'"],
    raw=multipart(fields=[('map', '{}')], files=[('document_id', 'soci.csv', b'Nome\n$pad:2700000\n', 'text/csv')]))
# The import reads the association's own import uploads: another association's upload and a Document stored outside
# the import folder answer like an unknown document, before any write.
add('upload-of-another-association-server-error', 'owner', 500, SERVER,
    body={'document_id': '$document:other', 'map': {'nome': 'Nome'}}, effect={'kind': 'release', 'release': ['other']})
add('document-outside-import-folder-server-error', 'owner', 500, SERVER,
    body={'document_id': '$document:stored', 'map': {'nome': 'Nome'}}, effect={'kind': 'release', 'release': ['stored']})
add('unreadable-document-server-error', 'owner', 500, SERVER, body={'document_id': '$document:broken', 'map': {'nome': 'Nome'}},
    effect={'kind': 'release', 'release': ['broken']})
# An imported upload is gone: the same request again answers like an unknown document.
add('imported-upload-again-server-error', 'owner', 500, SERVER, body={'document_id': '$document:numbers-imported', 'map': {'nome': 'Nome'}})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'subscription/import/upload', 'ids': IDS, 'location': 'import-uploads',
                              'limit': LIMIT, 'statuses': STATUSES, 'stored': STORED, 'expired': EXPIRED, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
