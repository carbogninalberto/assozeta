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
   An Excel suffix reads the bytes with pandas.read_excel(engine="openpyxl")
   whatever the suffix says (a failure falls back to xlrd, which is not
   installed: 500 after the writes). Number-like text becomes numbers, a
   column with a missing value is float, date cells are datetimes.
4. 'document_id' and 'map': is_valid_uuid(document_id) (400 [str(exception)]),
   Document.objects.filter(document_id=...).first() (None: ValueError, 500),
   the same read of the stored object (500 when it fails), then the import
   inside try/except/finally: manage_import_status('start') deletes the
   association's IMPORTING statuses and creates one; process_import_rows walks
   the rows and the map's keys in order (KEY_TO_FIELD_ASSOCIATE[key without
   'tutore']; the cell, stripped; 'nan' is None; birth and membership dates
   through dateutil.parse(str(value)) to %d/%m/%Y or None; a missing 'sesso'
   is 'A'; a true 'scadenza certificato medico' creates a MedicalCertificate
   for request.user and saves its expiration, or deletes it when the text is
   not a date; a None value takes data['default'][key]), fills born_city from
   the tax code (codicefiscale.decode, once more with the expected control
   character), applies unmapped_static(_tutor), and validates the mandatory
   fields and the tax code pattern; AssociateImportDraft.objects.bulk_create
   (no audit entries). Any exception is logged and swallowed: what was saved
   before it stays, no draft is created. 'end' deletes the status. The answer
   is 200 {'columns', 'map': MAP} either way.
5. Otherwise TypeError: 500.

The first upload maps every key it will ever map in this sequence, so a later
workload in the same server process (Caddy after native) sees the same MAP.
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
           session=None, keep=None):
    """A stored upload: the Document (audited) and its object, then the columns or the 500 page."""
    effect = {'actor': actor, 'session': session, 'filename': filename, 'body_b64': base64.b64encode(content).decode(),
              'content_type': content_type}
    if keep:
        # The object stays for the imports that name '$document:<keep>'; the case that lists it in 'release' removes it.
        effect['keep'] = keep
    if status == 200:
        auto_map(columns)
        response = {'columns': columns, 'map': dict(MAP), 'document_id': '$new'}
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
          '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
          '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
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
    ('date', serial): a number with the date style (days since 1899-12-30)."""
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
            elif isinstance(value, tuple):
                cells += f'<c r="{reference}" s="1"><v>{value[1]}</v></c>'
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
upload('xls-suffix-date-cells', 'full_collaborator', 'date.XLS', workbook([
    ['Nome', 'Scadenza certificato med'], ['Mario', ('date', 46387)], ['Anna', None]]),
    columns=['Nome', 'Scadenza certificato med'], kind='application/vnd.ms-excel', content_type='application/vnd.ms-excel', keep='dated')
upload('xlsx-header-only-unnamed-duplicates', 'admin', 'vuoto.xlsx', workbook([['Telefono', None, 'Altro', 'Altro', None, 'Fine']]),
       headers=ADMIN_OWNER, session='admin_owner', columns=['Telefono', 'Unnamed: 1', 'Altro', 'Altro.1', 'Unnamed: 4', 'Fine'],
       kind=XLSX, content_type=XLSX, keep='header')
upload('xlsx-empty-sheet', 'owner', 'niente.xlsx', workbook([]), columns=[], kind=XLSX, content_type=XLSX)
upload('xlsx-not-a-workbook-server-error', 'owner', 'testo.xlsx', SIMPLE, status=500, kind=XLSX, content_type=XLSX, keep='broken')
upload('ods-archive-server-error', 'owner', 'soci.ods', archive([('mimetype', 'application/vnd.oasis.opendocument.spreadsheet'),
                                                                 ('content.xml', '<office/>')]),
       status=500, kind='application/vnd.oasis.opendocument.spreadsheet', content_type='application/vnd.oasis.opendocument.spreadsheet')

# --- The import of a stored workbook ---------------------------------------------------------------------------
pending_statuses = ['own_importing', 'own_importing_done']


def imported(name, actor, document, body, *, columns, steps=(), drafts=(), headers=None, session=None, release=(), form=False):
    """A 200 import: the association's IMPORTING statuses go, one is created and deleted around the rows.
    steps: the certificates in order, ('kept', label, 'YYYY-MM-DD') or ('deleted',). drafts: [(data, valid)] with
    '$mc:<label>' for a kept certificate's id."""
    effect = {'kind': 'import', 'actor': actor, 'session': session, 'statuses': list(pending_statuses), 'steps': [list(step) for step in steps],
              'drafts': [{'data': data, 'valid': valid} for data, valid in drafts], 'release': list(release)}
    pending_statuses.clear()
    response = {'columns': columns, 'map': dict(MAP)}
    if form:
        add(name, actor, 200, response, raw=multipart(fields=[('document_id', f'$document:{document}'), *body]), headers=headers,
            effect=effect)
    else:
        add(name, actor, 200, response, body={'document_id': f'$document:{document}', **body}, headers=headers, effect=effect)


HEADER_COLUMNS = ['Telefono', 'Unnamed: 1', 'Altro', 'Altro.1', 'Unnamed: 4', 'Fine']
# No rows: only the statuses move, whatever the map is.
imported('header-only-statuses', 'admin', 'header', {'map': ['non', 'un', 'oggetto'], 'default': 5}, columns=HEADER_COLUMNS,
         headers=ADMIN_OWNER, session='admin_owner', release=['header'])

UI_MAP = {**{key: None for key in KEYS}, 'nome': 'Nome', 'cognome': 'COGNOME', 'sesso': 'Sesso', 'codice fiscale': 'Codice fiscal',
          'data di nascita': 'Data di Nascita', 'città di nascita': 'Città di nascita', 'indirizzo': 'Indirizzo',
          'città di residenza': 'Città di residenza', 'cap': 'CAP', 'email': 'E-mail', 'telefono': 'Telefono',
          'scadenza certificato medico': 'Scadenza certificato med', 'data tesseramento': 'Data tesseramento',
          'data scadenza tesseramento': 'Scadenza tesseramento', 'numero tessera': 'Numero tessera'}
TUTOR = {'sex': 'A'}  # 'sesso tutore' without a column
# The wizard's request: every key, most tutor keys unmapped. CAP and the card number are float columns (a row lacks them).
imported('wizard-map', 'owner', 'members', {'map': UI_MAP, 'default': {}}, columns=COLUMNS,
         steps=[('kept', 'mario', '2026-12-31'), ('deleted',), ('kept', 'ugo', '2027-01-15')],
         drafts=[
             ({'associate': {'first_name': 'Mario', 'last_name': 'Rossi', 'sex': 'M', 'tax_code': 'RSSMRA80A01H501U', 'born_date': '01/01/1980',
                             'born_city': 'Roma', 'address': 'Via Roma 1', 'address_city': 'Roma', 'address_cap': 100.0,
                             'email': 'mario@example.test', 'phone': '3330000000', 'certificate_expiring_date': '31/12/2026',
                             'membership_start_date': '01/09/2026', 'membership_end_date': '31/08/2027', 'subscription_number': 42.0},
               'associate_tutor': TUTOR,
               'medical_certificate': {'medical_id': '$mc:mario', 'filename': None, 'certificate_expring_date': '31/12/2026'}}, True),
             # 03/02/1990 reads month first; the birth city comes from the tax code; 'abc' is no date.
             ({'associate': {'first_name': 'Anna', 'last_name': 'Bianchi', 'sex': 'F', 'tax_code': ANNA, 'born_date': '02/03/1990',
                             'born_city': 'Milano', 'address': 'Via Milano 2', 'address_city': 'Milano', 'address_cap': 20100.0,
                             'email': 'anna@example.test', 'certificate_expiring_date': 'abc', 'subscription_number': 43.0},
               'associate_tutor': TUTOR}, True),
             ({'associate': {'first_name': 'Luca', 'last_name': 'Verdi', 'sex': 'A', 'tax_code': 'XXX', 'phone': '02 1234567'},
               'associate_tutor': TUTOR}, False),
             ({'associate': {'first_name': 'Ugo', 'last_name': 'Neri', 'sex': 'm', 'tax_code': WRONG_CONTROL, 'born_date': '25/12/1975',
                             'born_city': 'Roma', 'address': 'Via Po 3', 'address_city': 'Torino', 'address_cap': 10100.0,
                             'certificate_expiring_date': '2027-01-15'},
               'associate_tutor': TUTOR,
               'medical_certificate': {'medical_id': '$mc:ugo', 'filename': None, 'certificate_expring_date': '15/01/2027'}}, True),
         ])
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
# A key outside KEY_TO_FIELD_ASSOCIATE stops the import at the first row, after that row's certificate.
imported('unknown-key-stops', 'owner', 'members', {'map': {'scadenza certificato medico': 'Scadenza certificato med', 'tessera oro': 'Note'}},
         columns=COLUMNS, steps=[('kept', 'mario', '2026-12-31')])
imported('list-map-stops', 'owner', 'members', {'map': []}, columns=COLUMNS)
imported('form-map-stops', 'owner', 'members', [('map', '{"nome": "Nome"}')], columns=COLUMNS, form=True)
# A tax code that is not text stops at the pattern check of the first complete row.
imported('number-tax-code-stops', 'owner', 'members', {
    'map': {key: UI_MAP[key] for key in ('nome', 'cognome', 'sesso', 'data di nascita', 'città di nascita', 'indirizzo',
                                         'città di residenza', 'cap')} | {'codice fiscale': None},
    'default': {'codice fiscale': 12345}}, columns=COLUMNS, release=['members'])
# Date cells in the certificate column: the certificates are saved (an empty cell's is deleted), then the rows
# cannot be stored and no draft is created.
imported('date-cell-certificates', 'owner', 'dated', {'map': {'nome': 'Nome', 'scadenza certificato medico': 'Scadenza certificato med'}},
         columns=['Nome', 'Scadenza certificato med'], steps=[('kept', 'mario', '2026-12-31'), ('deleted',)], release=['dated'])
add('unreadable-document-server-error', 'owner', 500, SERVER, body={'document_id': '$document:broken', 'map': {'nome': 'Nome'}},
    effect={'kind': 'release', 'release': ['broken']})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'subscription/import/upload', 'ids': IDS, 'location': 'import-uploads',
                              'statuses': STATUSES, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
