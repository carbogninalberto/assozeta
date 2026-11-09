"""Author the subscription/list/export and invoice/list/export HTTP sequence (misc-exports.md).

subscription_list_export (@api_view(['POST', 'GET']), subscriptions_views.py):
athletes raise (500); scoped_queryset(SportAssociation.objects).get(user=...)
(500 without a live association); the association's group-aware
Subscription.objects rows (the manager keeps deleted rows) with archived=False,
ordered by -creation_date. 'simplified' in filetype selects the simplified
row, otherwise the full row, whose Associate.main_tutor promotes and saves the
associate's first tutor relation when none is primary (one audited update
each, autocommitted: kept when a later row raises). A subscription without an
associate raises (500). pandas.DataFrame(rows).columns = cols raises with
zero rows (500). The CSV (csv.writer, excel dialect) is returned base64 as
{'data': {'filename': '[<today>] ISCRIZIONI a <denomination>.csv', 'type':
'csv', 'file': ...}}. filetype containing 'xls' is _export_to_excel: the same
rows in pandas' xlsxwriter workbook (sheet 'Libro Soci', the index column,
set_column(i, i, len(col) + 10)), '.xls' in the filename and type 'xls'.

invoice_list_export (@api_view(['GET']), invoice_views.py): athletes raise
(500), the scoped live association (500), the association's live
group-aware invoices with archived=<raw value> (BooleanField.to_python:
anything but t/True/1/f/False/0 is a 500), ordered by creation_date. Each
invoice's payment is payment_set.first(): the live group-aware payments of
the invoice by primary key. m=csv (default) is the CSV; m=files publishes
export_invoices_to_zip.delay([<document ids>], <association UUID>) for the
invoices with a document_pdf and answers a message; m=xlsx is get_excel_base64
over the same columns (names and numbers as Python values, fees as Decimals);
any other m answers {'data': {'file': ''}}. Workbooks are compared with
TESTS/contracts/xlsx.py. Run with ``python3 -I``.
"""
import csv
import io
import json
from datetime import date
from pathlib import Path

OUTPUT = Path(__file__).with_name('misc_exports_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER = IDS['owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
ids = iter(f'a1000000-0000-4000-8000-{455001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
DENOMINATION = {OWN: 'Message reads own', FOREIGN: 'Message reads foreign'}
TODAY = date.today()  # only compared with far-away certificate expirations

ASSOCIATE_FIELDS = ('first_name', 'last_name', 'sex', 'tax_code', 'born_date', 'born_city', 'address_city', 'address', 'address_cap',
                    'email', 'phone_label', 'phone', 'phone_2_label', 'phone_2', 'phone_3_label', 'phone_3', 'phone_4_label', 'phone_4')


def associate(first, last, association=OWN, **fields):
    row = {key: None for key in ASSOCIATE_FIELDS}
    row.update(id=next(ids), association=association, first_name=first, last_name=last, sex='A', is_minor=False, deleted=False)
    row.update(fields)
    return row


ASSOCIATES = {
    'mario': associate('Mario', 'Rossi', sex='M', tax_code='RSSMRA80A01H501U', born_date='1980-01-01', born_city='Roma',
                       address_city='Milano', address='Via Roma, 1', address_cap='20100', email='mario@example.test',
                       phone_label='Casa', phone='3331112222', phone_2_label='Lavoro "ufficio"', phone_2='3334445555'),
    'luisa': associate('Luisa', 'Bianchi', sex='F', is_minor=True, born_date='2015-06-15', email='luisa@example.test'),
    'gino': associate('Niccolò', 'Verdi', sex='M', address='Piazza\nDuomo', phone_4_label='Nonna', phone_4='045 1234567'),
    'elena': associate('Elena', None, sex='F', deleted=True, tax_code='LNEXXX90B41F205Z'),
    'bruno': associate('Bruno', 'Bianchi', sex='M', tax_code='BRNBNC75C03L219K', born_date='1975-03-03', born_city='Torino',
                       address_city='Torino', address='Corso Francia 10', address_cap='10100', email='bruno@example.test',
                       phone_label='Cellulare', phone='3470000001', phone_3_label='Ufficio', phone_3='0110000000'),
    'anna': associate('Anna', 'Rossi', sex='F'),
    'hans': associate('Hans', 'Muller', FOREIGN, sex='M'),
    'greta': associate('Greta', 'Muller', FOREIGN, sex='F'),
}
# (child, tutor, is_primary) in creation (primary key) order.
RELATIONS = [('mario', 'bruno', False), ('mario', 'anna', True), ('luisa', 'bruno', False), ('luisa', 'anna', False),
             ('hans', 'greta', False)]
CERTIFICATES = {
    'valid': {'id': next(ids), 'expiration': '2031-05-01'},
    'expired_minor': {'id': next(ids), 'expiration': '2020-01-01'},
    'no_expiration': {'id': next(ids), 'expiration': None},
    'expired_adult': {'id': next(ids), 'expiration': '2021-02-02'},
}
TAGS = {'agonisti': {'id': next(ids), 'name': 'Agonisti', 'association': OWN},
        'under': {'id': next(ids), 'name': 'Under 12, misti', 'association': OWN}}


def subscription(person, created, association=OWN, **fields):
    row = {'id': next(ids), 'association': association, 'associate': person, 'status': 4, 'created': created, 'start': None, 'end': None,
           'number': None, 'kind': None, 'medical': None, 'signature_key': None, 'signature_url': None, 'tags': [], 'group': None,
           'archived': False, 'deleted': False}
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    'mario': subscription('mario', '2026-03-01T10:00:00Z', start='2026-01-01', end='2026-12-31', number='12', kind='Socio ordinario',
                          medical='valid', signature_key='signatures/mario.png', tags=['agonisti', 'under']),
    'luisa': subscription('luisa', '2026-02-01T10:00:00Z', status=2, medical='expired_minor'),
    'gino': subscription('gino', '2026-01-15T10:00:00Z', status=1, number='', kind='', medical='no_expiration', signature_key='',
                         signature_url='https://storage.example.test/firma.png', tags=['under'], start='2025-09-01'),
    'mario_old': subscription('mario', '2025-12-01T10:00:00Z', status=3, deleted=True, number='0', medical='expired_adult',
                              end='2025-08-31'),
    'elena': subscription('elena', '2025-11-01T10:00:00Z', status=5, group='own_group', number='7', kind='Tesserato'),
    'archived': subscription('mario', '2026-04-01T10:00:00Z', archived=True, number='99'),
    'hans': subscription('hans', '2026-03-05T10:00:00Z', FOREIGN, number='1'),
    'nobody': subscription(None, '2026-01-05T10:00:00Z', FOREIGN, number='2'),
}
STATUS = {1: 'non firmata', 2: 'in attesa', 3: 'rifiutata', 4: 'accettata', 5: 'archiviata'}
SIMPLE_COLS = ['data', 'nome', 'cognome', 'data di nascita', 'codice fiscale', 'minore', 'stato', 'certificato medico',
               'scadenza certificato medico', 'validità certificato medico', 'firmata', 'email', 'iscritto il', 'scadenza iscrizione',
               'numero tessera', 'tipo tessera', 'tags']
FULL_COLS = ['stato iscrizione', 'certificato medico', 'scadenza certificato medico', 'validità certificato medico', 'firma', 'nome',
             'cognome', 'sesso', 'codice fiscale', 'data di nascita', 'città di nascita', 'città di residenza', 'indirizzo di residenza',
             'cap di residenza', 'email', 'nome contatto telefono', 'telefono', 'nome contatto telefono 2', 'telefono 2',
             'nome contatto telefono 3', 'telefono 3', 'nome contatto telefono 4', 'telefono 4', 'nome tutore', 'cognome tutore',
             'sesso tutore', 'codice fiscale tutore', 'data di nascita tutore', 'città di nascita tutore', 'città di residenza tutore',
             'indirizzo di residenza tutore', 'cap di residenza tutore', 'email tutore', 'nome contatto telefono tutore', 'telefono tutore',
             'nome contatto telefono 2 tutore', 'telefono 2 tutore', 'nome contatto telefono 3 tutore', 'telefono 3 tutore',
             'nome contatto telefono 4 tutore', 'telefono 4 tutore', 'iscritto il', 'scadenza iscrizione', 'numero tessera', 'tipo tessera',
             'tags']


def main_tutor(person):
    """The primary relation's tutor, else the first relation's (which the view then promotes)."""
    relations = [r for r in RELATIONS if r[0] == person]
    primary = [r for r in relations if r[2]]
    chosen = primary[0] if primary else (relations[0] if relations else None)
    return ASSOCIATES[chosen[1]] if chosen else None


def medical_status(row):
    medical = CERTIFICATES[row['medical']] if row['medical'] else None
    if medical is None or medical['expiration'] is None:
        return ''
    if date.fromisoformat(medical['expiration']) >= TODAY:
        return 'valido'
    return 'esente' if ASSOCIATES[row['associate']]['is_minor'] else 'scaduto'


def subscription_row(row, simplified):
    person = ASSOCIATES[row['associate']]
    medical = CERTIFICATES[row['medical']] if row['medical'] else None
    common_tail = [row['start'] or '', row['end'] or '', row['number'] or '', row['kind'] or '',
                   ', '.join(TAGS[label]['name'] for label in row['tags'])]
    signed = 'si' if (row['signature_key'] or row['signature_url']) else 'no'
    expiration = medical['expiration'] if medical and medical['expiration'] else ''
    if simplified:
        return [row['created'][:10], person['first_name'], person['last_name'], person['born_date'] or '', person['tax_code'],
                'minorenne' if person['is_minor'] else 'maggiorenne', STATUS[row['status']], 'si' if medical else 'no', expiration,
                medical_status(row), signed, person['email'], *common_tail]
    tutor = main_tutor(row['associate'])
    tutor_values = [tutor[key] if tutor else '' for key in ASSOCIATE_FIELDS]
    tutor_values[4] = (tutor['born_date'] or '') if tutor else ''
    person_values = [person[key] for key in ASSOCIATE_FIELDS]
    person_values[4] = person['born_date'] or ''
    return [STATUS[row['status']], 'si' if medical else 'no', expiration, medical_status(row), signed, *person_values, *tutor_values,
            *common_tail]


def write_csv(header, rows):
    out = io.StringIO()
    csv.writer(out).writerow(header)
    csv.writer(out).writerows(rows)
    return out.getvalue()


def listed_subscriptions(association=OWN, group=None):
    rows = [row for row in SUBSCRIPTIONS.values() if row['association'] == association and not row['archived']
            and (group is None or row['group'] == group)]
    return sorted(rows, key=lambda row: row['created'], reverse=True)


def subscription_body(simplified=False, association=OWN, group=None):
    rows = listed_subscriptions(association, group)
    assert rows, 'zero rows is a 500'
    text = write_csv(SIMPLE_COLS if simplified else FULL_COLS, [subscription_row(row, simplified) for row in rows])
    return {'data': {'filename': f'[$today] ISCRIZIONI a {DENOMINATION[association]}.csv', 'type': 'csv', 'file': {'$csv': text}}}


def subscription_xls(simplified=False, association=OWN, group=None):
    rows = listed_subscriptions(association, group)
    assert rows, 'zero rows is a 500'
    cols = SIMPLE_COLS if simplified else FULL_COLS
    spec = {'engine': 'xlsxwriter', 'sheet': 'Libro Soci', 'columns': cols, 'index': True,
            'rows': [subscription_row(row, simplified) for row in rows], 'widths': [len(col) + 10 for col in cols]}
    return {'data': {'filename': f'[$today] ISCRIZIONI a {DENOMINATION[association]}.xls', 'type': 'xls', 'file': {'$xlsx': spec}}}


DOCUMENTS = {name: {'id': next(ids), 'filename': f'ricevuta-{name}.pdf'} for name in ('one', 'three', 'four', 'six', 'archived', 'foreign')}


def invoice(created, association=OWN, **fields):
    row = {'id': next(ids), 'association': association, 'created': created, 'membership': '40.00', 'activity': '10.00', 'number': None,
           'document': None, 'archived': False, 'deleted': False, 'group': None}
    row.update(fields)
    return row


INVOICES = {
    'one': invoice('2026-01-10T09:00:00Z', number=1, document='one'),
    'two': invoice('2026-02-10T09:00:00Z', membership='35.50', activity='0.00'),
    'three': invoice('2026-03-10T09:00:00Z', number=3, document='three', membership='0.00', activity='120.00'),
    'four': invoice('2026-01-20T09:00:00Z', number=4, document='four'),
    'five': invoice('2026-02-20T09:00:00Z', number=5, group='own_group'),
    'six': invoice('2026-02-25T09:00:00Z', number=6, group='own_group', document='six'),
    'archived': invoice('2026-04-01T09:00:00Z', number=9, document='archived', archived=True),
    'deleted': invoice('2026-01-12T09:00:00Z', number=8, deleted=True),
    'foreign': invoice('2026-01-11T09:00:00Z', FOREIGN, number=1, document='foreign'),
}
# Payment ids are authored so that payment_set.first() (primary key order) is
# not the insertion order: 'one_late' is inserted first with a higher key.
PAYMENTS = {
    'one_late': {'id': 'a1000000-0000-4000-8000-000000455990', 'invoice': 'one', 'associate': 'mario', 'type': 'cash'},
    'one_first': {'id': 'a1000000-0000-4000-8000-000000455980', 'invoice': 'one', 'associate': 'luisa', 'type': 'default'},
    'two': {'id': next(ids), 'invoice': 'two', 'associate': None, 'type': 'transfer'},
    'four_deleted': {'id': next(ids), 'invoice': 'four', 'associate': 'mario', 'type': 'cash', 'deleted': True},
    'five': {'id': next(ids), 'invoice': 'five', 'associate': 'gino', 'type': 'pos', 'group': 'own_group'},
    'six': {'id': next(ids), 'invoice': 'six', 'associate': 'elena', 'type': 'online'},
    'archived': {'id': next(ids), 'invoice': 'archived', 'associate': 'mario', 'type': 'online'},
    'foreign': {'id': next(ids), 'invoice': 'foreign', 'associate': 'hans', 'type': 'cash', 'association': FOREIGN},
}
for payment in PAYMENTS.values():
    payment.setdefault('deleted', False)
    payment.setdefault('group', None)
    payment.setdefault('association', OWN)
INVOICE_HEADER = ['data', 'nome', 'cognome', 'quota associativa', 'quota attivita', 'numero', 'modalità di pagamento']


def listed_invoices(association=OWN, archived=False, group=None):
    rows = [(label, row) for label, row in INVOICES.items() if row['association'] == association and row['archived'] == archived
            and not row['deleted'] and (group is None or row['group'] == group)]
    return sorted(rows, key=lambda item: item[1]['created'])


def first_payment(label, group=None):
    candidates = [p for p in PAYMENTS.values() if p['invoice'] == label and not p['deleted'] and (group is None or p['group'] == group)]
    return min(candidates, key=lambda p: p['id']) if candidates else None


def invoice_body(association=OWN, archived=False, group=None):
    rows = []
    for label, row in listed_invoices(association, archived, group):
        payment = first_payment(label, group)
        person = ASSOCIATES[payment['associate']] if payment and payment['associate'] else None
        rows.append([row['created'][:10], person['first_name'] if person else '', person['last_name'] if person else '', row['membership'],
                     row['activity'], row['number'], ('-' if payment['type'] == 'default' else payment['type']) if payment else '-'])
    return {'data': {'file': {'$csv': write_csv(INVOICE_HEADER, rows)}, 'type': 'csv',
                     'filename': f'[$today] RICEVUTE a {DENOMINATION[association]}.csv'}}


def invoice_xlsx(association=OWN, archived=False, group=None):
    data = [[] for _ in INVOICE_HEADER]
    for label, row in listed_invoices(association, archived, group):
        payment = first_payment(label, group)
        person = ASSOCIATES[payment['associate']] if payment and payment['associate'] else None
        values = [row['created'][:10], person['first_name'] if person else '', person['last_name'] if person else '',
                  {'decimal': row['membership']}, {'decimal': row['activity']},
                  None if row['number'] is None else {'int': str(row['number'])},
                  ('-' if payment['type'] == 'default' else payment['type']) if payment else '-']
        for column, value in zip(data, values):
            column.append(value)
    return {'data': {'file': {'$xlsx': {'engine': 'excel_utils', 'columns': INVOICE_HEADER, 'data': data}}, 'type': 'xlsx',
                     'filename': f'[$today] RICEVUTE a {DENOMINATION[association]}.xlsx'}}


ZIP_MESSAGE = 'Export dei file in corso, controlla la sezione Documenti e Modelli tra qualche minuto.'


def zip_effect(association=OWN, archived=False, group=None):
    documents = [DOCUMENTS[row['document']]['id'] for _, row in listed_invoices(association, archived, group) if row['document']]
    return {'celery': [{'task': 'export_invoices_to_zip', 'args': [documents, {'uuid': association}], 'kwargs': []}], 'promoted': []}


cases = []


def add(name, actor, path, status, response, *, method='GET', headers=None, effect=None):
    cases.append({'name': f'misc-exports-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect})


def promoted(*children, actor='owner'):
    return {'celery': [], 'promoted': [[child, actor] for child in children]}


SUBS, INVS = '/subscription/list/export', '/invoice/list/export'
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
OPTIONS_PARSES = ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']

# --- subscription/list/export: identity, methods and OPTIONS -------------------------------------------------
add('subs-anonymous', 'anonymous', SUBS, 401, {'detail': 'Authentication credentials were not provided.'})
add('subs-admin-without-session', 'admin', SUBS, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('subs-restricted-collaborator', 'restricted_collaborator', SUBS, 403, {'detail': 'Missing permission: association.members.read'})
add('subs-put', 'owner', SUBS, 405, {'detail': 'Method "PUT" not allowed.'}, method='PUT')
add('subs-options', 'owner', SUBS, 200, {'name': 'Subscription List Export', 'description': '', 'renders': ['application/json'],
                                          'parses': OPTIONS_PARSES}, method='OPTIONS')
add('subs-format-unknown', 'owner', SUBS + '?format=xml', 404, {'detail': 'Not found.'})
add('subs-athlete', 'athlete', SUBS, 500, SERVER)
add('subs-scoped-athlete', 'owner', SUBS, 403, SCOPED_DENIED, headers=SCOPED_ATHLETE)
add('subs-owner-without-association', 'owner_without_association', SUBS, 500, SERVER)
add('subs-deleted-association-owner', 'deleted_association_owner', SUBS, 500, SERVER)

# --- the exports: the first full export promotes luisa's first tutor relation ---------------------------------
FULL = subscription_body()
SIMPLE = subscription_body(simplified=True)
add('subs-full-promotes', 'owner', SUBS, 200, FULL, effect=promoted('luisa'))
add('subs-full-again', 'owner', SUBS, 200, FULL)
add('subs-full-post', 'owner', SUBS, 200, FULL, method='POST')
add('subs-filetype-csv', 'owner', SUBS + '?filetype=csv', 200, FULL)
add('subs-filetype-other', 'owner', SUBS + '?filetype=pdf', 200, FULL)
add('subs-simplified', 'owner', SUBS + '?filetype=simplified', 200, SIMPLE)
add('subs-simplified-suffix', 'owner', SUBS + '?filetype=csv-simplified', 200, SIMPLE)
add('subs-simplified-post', 'owner', SUBS + '?filetype=simplified', 200, SIMPLE, method='POST')
add('subs-full-collaborator', 'full_collaborator', SUBS, 200, FULL)
add('subs-admin-impersonating-owner', 'admin', SUBS + '?filetype=simplified', 200, SIMPLE, headers=ADMIN_OWNER)
add('subs-group', 'owner', SUBS, 200, subscription_body(group='own_group'), headers={'X-Group-Id': OWN_GROUP})
add('subs-group-simplified', 'owner', SUBS + '?filetype=simplified', 200, subscription_body(True, group='own_group'),
    headers={'X-Group-Id': OWN_GROUP})
add('subs-group-empty', 'owner', SUBS, 500, SERVER, headers={'X-Group-Id': FOREIGN_GROUP})
add('subs-group-malformed-ignored', 'owner', SUBS, 200, FULL, headers={'X-Group-Id': 'nope'})
# The foreign association lists hans (newest) then a subscription without an
# associate: the full export promotes hans's relation, then raises.
add('subs-foreign-simplified-no-associate', 'foreign_owner', SUBS + '?filetype=simplified', 500, SERVER)
add('subs-foreign-full-promotes-then-fails', 'foreign_owner', SUBS, 500, SERVER, effect=promoted('hans', actor='foreign_owner'))
add('subs-foreign-full-again', 'foreign_owner', SUBS, 500, SERVER)

# --- filetype containing 'xls': the xlsxwriter workbook (luisa's relation is already primary) ---
add('subs-xls-full', 'owner', SUBS + '?filetype=xls', 200, subscription_xls())
add('subs-xls-full-post', 'owner', SUBS + '?filetype=xlsx', 200, subscription_xls(), method='POST')
add('subs-xls-simplified', 'owner', SUBS + '?filetype=xls-simplified', 200, subscription_xls(simplified=True))
add('subs-xls-group', 'full_collaborator', SUBS + '?filetype=xls', 200, subscription_xls(group='own_group'),
    headers={'X-Group-Id': OWN_GROUP})
add('subs-xls-group-empty', 'owner', SUBS + '?filetype=xls', 500, SERVER, headers={'X-Group-Id': FOREIGN_GROUP})
add('subs-xls-foreign-no-associate', 'foreign_owner', SUBS + '?filetype=xls', 500, SERVER)
add('subs-xls-athlete', 'athlete', SUBS + '?filetype=xls', 500, SERVER)

# --- invoice/list/export ---------------------------------------------------------------------------------------
add('inv-anonymous', 'anonymous', INVS, 401, {'detail': 'Authentication credentials were not provided.'})
add('inv-admin-without-session', 'admin', INVS, 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('inv-restricted-collaborator', 'restricted_collaborator', INVS, 403, {'detail': 'Missing permission: bookeeping.documents.invoices.read'})
add('inv-post', 'owner', INVS, 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('inv-options', 'owner', INVS, 200, {'name': 'Invoice List Export', 'description': '', 'renders': ['application/json'],
                                        'parses': OPTIONS_PARSES}, method='OPTIONS')
add('inv-format-unknown', 'owner', INVS + '?format=xml', 404, {'detail': 'Not found.'})
add('inv-athlete', 'athlete', INVS, 500, SERVER)
add('inv-scoped-athlete', 'owner', INVS, 403, SCOPED_DENIED, headers=SCOPED_ATHLETE)
add('inv-owner-without-association', 'owner_without_association', INVS, 500, SERVER)
add('inv-deleted-association-owner', 'deleted_association_owner', INVS, 500, SERVER)
INVOICES_CSV = invoice_body()
add('inv-owner', 'owner', INVS, 200, INVOICES_CSV)
add('inv-mode-csv', 'owner', INVS + '?m=csv', 200, INVOICES_CSV)
add('inv-full-collaborator', 'full_collaborator', INVS, 200, INVOICES_CSV)
add('inv-admin-impersonating-owner', 'admin', INVS, 200, INVOICES_CSV, headers=ADMIN_OWNER)
add('inv-archived-true', 'owner', INVS + '?archived=True', 200, invoice_body(archived=True))
add('inv-archived-one', 'owner', INVS + '?archived=1', 200, invoice_body(archived=True))
add('inv-archived-zero', 'owner', INVS + '?archived=0', 200, INVOICES_CSV)
add('inv-archived-lowercase', 'owner', INVS + '?archived=true', 500, SERVER)
add('inv-archived-empty', 'owner', INVS + '?archived=', 500, SERVER)
add('inv-group', 'owner', INVS, 200, invoice_body(group='own_group'), headers={'X-Group-Id': OWN_GROUP})
add('inv-group-empty', 'owner', INVS, 200, invoice_body(group='foreign_group'), headers={'X-Group-Id': FOREIGN_GROUP})
add('inv-group-malformed-ignored', 'owner', INVS, 200, INVOICES_CSV, headers={'X-Group-Id': 'nope'})
add('inv-foreign-owner', 'foreign_owner', INVS, 200, invoice_body(FOREIGN))
add('inv-xlsx', 'owner', INVS + '?m=xlsx', 200, invoice_xlsx())
add('inv-xlsx-archived', 'owner', INVS + '?m=xlsx&archived=1', 200, invoice_xlsx(archived=True))
add('inv-xlsx-group', 'owner', INVS + '?m=xlsx', 200, invoice_xlsx(group='own_group'), headers={'X-Group-Id': OWN_GROUP})
add('inv-xlsx-empty', 'owner', INVS + '?m=xlsx', 200, invoice_xlsx(group='foreign_group'), headers={'X-Group-Id': FOREIGN_GROUP})
add('inv-xlsx-foreign', 'foreign_owner', INVS + '?m=xlsx', 200, invoice_xlsx(FOREIGN))
add('inv-xlsx-archived-invalid', 'owner', INVS + '?m=xlsx&archived=yes', 500, SERVER)
add('inv-mode-other', 'owner', INVS + '?m=pdf', 200, {'data': {'file': ''}})
add('inv-mode-other-archived-invalid', 'owner', INVS + '?m=pdf&archived=yes', 500, SERVER)
FILES = {'data': {'file': '', 'message': ZIP_MESSAGE}}
add('inv-files', 'owner', INVS + '?m=files', 200, FILES, effect=zip_effect())
add('inv-files-archived', 'owner', INVS + '?m=files&archived=t', 200, FILES, effect=zip_effect(archived=True))
add('inv-files-group', 'full_collaborator', INVS + '?m=files', 200, FILES, headers={'X-Group-Id': OWN_GROUP},
    effect=zip_effect(group='own_group'))
add('inv-files-empty', 'owner', INVS + '?m=files', 200, FILES, headers={'X-Group-Id': FOREIGN_GROUP},
    effect=zip_effect(group='foreign_group'))
add('inv-files-foreign', 'foreign_owner', INVS + '?m=files', 200, FILES, effect=zip_effect(FOREIGN))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Subscription and invoice CSV exports, tutor promotion and the invoice zip dispatch',
                              'ids': IDS, 'associates': ASSOCIATES, 'relations': RELATIONS, 'certificates': CERTIFICATES, 'tags': TAGS,
                              'subscriptions': SUBSCRIPTIONS, 'documents': DOCUMENTS, 'invoices': INVOICES, 'payments': PAYMENTS,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
