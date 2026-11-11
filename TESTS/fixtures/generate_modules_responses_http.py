"""Author the module responses HTTP sequence (modules-responses.md).

Five @api_view views of modules_views.py:
- modules_custom_link_info (GET, no permission_classes: AllowAny; registry
  association.modules.read): Module.objects.filter(custom_link=...).first()
  by link (404 'module not found'); query_params['response_id']
  when present goes through is_valid_uuid (400 list) and resolves the
  ModuleResponses row by id (ModuleResponseSerializer, or the serializer's
  initial values for None); then Module.enabled (404 'module not active');
  200 {'data': ModulesSerializer(module), 'response_data': ...}.
- modules_response_add (POST, AllowAny; association.modules.create):
  is_valid_uuid, the module by id (404 'module not found'), enabled (404
  'module not enabled'), is_max_responses_reached (approved count) unless
  queue_mode (404 'max responses reached'), only_users for anonymous requests
  (404 'only users'); payment_required creates nothing. progressive number is
  the -progressive_response_number first (PostgreSQL DESC puts NULL first:
  None + 1 is a TypeError, 500) + 1 or 0; queue_mode takes the last queue
  position + 1 or 1. ModuleResponses.objects.create(response=request.data,
  approved=not require_approval); an authenticated request.user is then saved
  as user; queue_mode runs update_queue. 200 {'msg': 'response added',
  'module_response_id': id}.
- modules_response_approve (POST, IsAuthenticated; .update): is_valid_uuid,
  the response within request.user.sport_association (404 'response not
  found'), approved=True and save(), update_queue for queue_mode modules.
- modules_response_delete (DELETE, IsAuthenticated; .delete): the same lookup,
  delete() (attachment links go, documents stay), update_queue.
- modules_response_add_attachment (POST/DELETE, AllowAny; .update per
  method): is_valid_uuid, the response by id (may be None). DELETE:
  'document_id' in request.data, the Document by id: link removed and
  document.delete() (200 'attachment deleted'), otherwise 404 'document not
  found'. POST with 'filename' and 'base64': Document.objects.create (kept
  even if what follows raises), the decoded base64 (data URI prefix dropped,
  padding added, b64decode without validation) saved to the default storage at
  '<creation timestamp>/<document id>/<filename>', then the link (None: 500).
  200 'attachment added' also when nothing was sent.
update_queue: approved responses with a queue position lose it, the others
are renumbered 1.. by queue position; every save() writes all fields and
auditlog records the changed ones.
- modules_response_export (GET, IsAuthenticated; association.modules.read):
  is_valid_uuid, then ModuleResponses.objects.filter(module_id=...,
  module__sport_association=request.user.sport_association) (athletes 403
  'User is not a sport association', owners without an association 500);
  no first() row: 404 {'error': 'response not found'}. The first response's
  module elements give the columns (type not heading/paragraph/link:
  props['label'] and props['name']; missing keys raise), then per response
  (database order) response[name] or '-' (response.keys(): a non-dict response
  raises when there are response columns), the creation date '%d/%m/%Y %H:%M',
  the progressive number and queue position or '-', user.get_full_name() or
  '-', 'Pagato'/'Non pagato'/'-', 'Approvata'/'Non approvata' and the
  attachment filenames joined by ','. pandas.DataFrame(rows) through xlsxwriter
  (sheet 'Risposte', index=False, set_column(i, i, len(label) + 10)); 200
  {'data': {'file': base64, 'filename': '[<today>] Risposte al modulo
  (<title>).xls', 'type': 'xlsx'}} (the workbook is compared with
  TESTS/contracts/xlsx.py). The export cases come first, on the seeded state.
Run with ``python3 -I``.
"""
import base64
import copy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT = HERE / 'modules_responses_http.json'
IDS = json.loads((HERE / 'message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
DELETED = IDS['deleted_association']
ids = iter(f'a1000000-0000-4000-8000-{643001 + n:012d}' for n in range(80))
# Ids of the rows added for the rare inputs (their own range: the earlier ids stay).
yids = iter(f'a1000000-0000-4000-8000-{643800 + n:012d}' for n in range(40))
SERVER = '<stable-server-error-page>'
BAD_REQUEST = '<bad-request-page>'
FORM = 'application/x-www-form-urlencoded'
MULTIPART = 'multipart/form-data; boundary=moduli'


def multipart(*parts):
    """A multipart body: (name, value) text parts and (name, filename, content) file parts."""
    out = ''
    for part in parts:
        if len(part) == 2:
            out += f'--moduli\r\nContent-Disposition: form-data; name="{part[0]}"\r\n\r\n{part[1]}\r\n'
        else:
            out += (f'--moduli\r\nContent-Disposition: form-data; name="{part[0]}"; filename="{part[1]}"\r\n'
                    f'Content-Type: application/octet-stream\r\n\r\n{part[2]}\r\n')
    return out + '--moduli--\r\n'
UNKNOWN = 'a1000000-0000-4000-8000-000000643999'
LOCATION = 'modules-responses-fixture'
DENOMINATIONS = {OWN: 'Message reads own', FOREIGN: 'Message reads foreign'}

ELEMENTS = [{'type': 'heading', 'props': {'label': 'Titolo'}}, {'type': 'text', 'props': {'label': 'Nome', 'name': 'nome'}}]
BASE = {'association': OWN, 'link': None, 'created': '2026-09-01T08:00:00Z', 'title': 'Modulo', 'require_approval': False,
        'start': None, 'end': None, 'always_active': True, 'max_responses': None, 'queue_mode': False, 'only_users': False,
        'payment_required': False, 'payment_data': {}, 'response_message': None, 'allow_attachments': False, 'elements': ELEMENTS}
xids = iter(f'a1000000-0000-4000-8000-{643101 + n:012d}' for n in range(60))
EXPORT_ELEMENTS = [
    {'type': 'heading', 'props': {'label': 'Intestazione'}},
    {'type': 'text', 'props': {'label': 'Nome', 'name': 'nome'}},
    {'type': 'number', 'props': {'label': 'Età', 'name': 'eta'}},
    {'type': 'checkbox', 'props': {'label': 'Consenso', 'name': 'consenso'}},
    {'type': 'select', 'props': {'label': 'Corsi scelti', 'name': 'corsi'}},
    {'type': 'group', 'props': {'label': 'Indirizzo', 'name': 'indirizzo'}},
    {'type': 'paragraph', 'props': {'label': 'Testo'}},
    {'type': 'signature', 'props': {'label': 'Firma', 'name': 'firma'}},
    {'type': 'text', 'props': {'label': 'Note & <varie>', 'name': 'note'}},
    {'type': 'link', 'props': {'label': 'Sito', 'name': 'sito'}},
    {'type': 'text', 'props': {'label': '', 'name': 'vuoto'}},
    {'type': 'text', 'props': {'label': 'Numero', 'name': 5}},
]
EXPORT_BASE = {**BASE, 'elements': EXPORT_ELEMENTS}
MODULES = {
    'open': {**BASE, 'id': next(ids), 'link': 'risposte-aperto', 'title': 'Aperto è', 'response_message': 'Grazie',
             'created': '2026-08-01T10:00:00.250000Z', 'allow_attachments': True},
    'closed': {**BASE, 'id': next(ids), 'link': 'risposte-chiuso', 'always_active': False},
    # The dates are far enough from today (UTC) for 'enabled' to be stable.
    'approval': {**BASE, 'id': next(ids), 'link': 'risposte-approvazione', 'always_active': False, 'start': '2000-01-01',
                 'end': '2999-12-31', 'require_approval': True, 'max_responses': 0, 'payment_data': {'amount': 5}},
    'full': {**BASE, 'id': next(ids), 'link': 'risposte-pieno', 'max_responses': 1},
    'queue': {**BASE, 'id': next(ids), 'link': 'risposte-coda', 'queue_mode': True, 'require_approval': True, 'max_responses': 1},
    'users': {**BASE, 'id': next(ids), 'link': 'risposte-utenti', 'only_users': True, 'payment_required': True,
              'payment_data': {'amount': 10, 'methods': ['card']}},
    'foreign': {**BASE, 'id': next(ids), 'association': FOREIGN, 'link': 'risposte-estero'},
    'foreign_closed': {**BASE, 'id': next(ids), 'association': FOREIGN, 'link': 'risposte-estero-chiuso', 'always_active': False,
                       'start': '2000-01-01', 'end': '2000-12-31'},
    'orphan': {**BASE, 'id': next(ids), 'association': None, 'link': 'risposte-orfano', 'title': None, 'elements': {}},
    # A module whose association is marked deleted: the nested profile is the row the foreign key resolves by id.
    'deleted_association': {**BASE, 'id': next(ids), 'association': DELETED, 'link': 'risposte-eliminata'},
    'nullnum': {**BASE, 'id': next(ids), 'link': 'risposte-nullo'},
    'export': {**EXPORT_BASE, 'id': next(xids), 'link': 'risposte-export', 'title': 'Esporta "risposte" è'},
    'export_empty': {**EXPORT_BASE, 'id': next(xids), 'link': 'risposte-export-vuoto'},
    'export_plain': {**BASE, 'id': next(xids), 'link': 'risposte-export-semplice', 'elements': [], 'title': None},
    'export_noname': {**BASE, 'id': next(xids), 'link': 'risposte-export-senza-nome',
                      'elements': [{'type': 'text', 'props': {'label': 'Senza nome'}}]},
    'export_nolabel': {**BASE, 'id': next(xids), 'link': 'risposte-export-senza-etichetta',
                       'elements': [{'type': 'text', 'props': {'label': None, 'name': 'x'}}]},
    'export_dict': {**BASE, 'id': next(xids), 'link': 'risposte-export-dizionario', 'elements': {'campo': 1}},
    'export_foreign': {**EXPORT_BASE, 'id': next(xids), 'association': FOREIGN, 'link': 'risposte-export-estero'},
    # Elements whose 'props' is not an object, or whose label is a list or an object.
    'export_props_text': {**BASE, 'id': next(yids), 'link': 'risposte-export-props-testo', 'elements': [{'type': 'text', 'props': 'testo'}]},
    'export_props_list': {**BASE, 'id': next(yids), 'link': 'risposte-export-props-lista',
                          'elements': [{'type': 'text', 'props': ['label', 'name']}]},
    'export_props_number': {**BASE, 'id': next(yids), 'link': 'risposte-export-props-numero', 'elements': [{'type': 'text', 'props': 5}]},
    'export_label_list': {**BASE, 'id': next(yids), 'link': 'risposte-export-etichetta-lista',
                          'elements': [{'type': 'text', 'props': {'label': ['a', 'b'], 'name': 'x'}}]},
    'export_label_dict': {**BASE, 'id': next(yids), 'link': 'risposte-export-etichetta-oggetto',
                          'elements': [{'type': 'text', 'props': {'label': {'a': 1}, 'name': 'x'}}]},
}
DOCUMENTS = {'d1': {'id': next(ids), 'filename': 'certificato è.pdf', 'token': next(ids)},
             'd2': {'id': next(ids), 'filename': 'foto.png', 'token': next(ids)},
             'd3': {'id': next(ids), 'filename': 'libero.txt', 'token': next(ids)},
             'd4': {'id': next(ids), 'filename': 'condiviso.pdf', 'token': next(ids)},
             'd5': {'id': next(ids), 'filename': 'anonimo.txt', 'token': next(ids)},
             'd6': {'id': next(xids), 'filename': 'primo, allegato.pdf', 'token': next(xids)},
             'd7': {'id': next(xids), 'filename': 'secondo.png', 'token': next(xids)},
             # A document a payment also lists among its attachments.
             'd8': {'id': next(yids), 'filename': 'ricevuta.pdf', 'token': next(yids)},
             'd9': {'id': next(yids), 'filename': 'da form.txt', 'token': next(yids)},
             # uuid.UUID(int=42).
             'd42': {'id': '00000000-0000-0000-0000-00000000002a', 'filename': 'numerato.txt', 'token': next(yids)}}
EXPORT_USERS = {'export_a': {'id': next(xids), 'first_name': '', 'last_name': 'Neri  ', 'username': 'modules-export-a'},
                'export_b': {'id': next(xids), 'first_name': '', 'last_name': '', 'username': 'modules-export-b'}}
EXPORT_PAYMENTS = {'paid': {'id': next(xids), 'paid': True, 'deleted': False},
                   'unpaid': {'id': next(xids), 'paid': False, 'deleted': False},
                   'gone': {'id': next(xids), 'paid': True, 'deleted': True}}
R = {'created': '2026-09-02T10:00:00Z', 'queue': None, 'response': {}, 'approved': True, 'user': None, 'attachments': []}
RESPONSES = {
    'open_r1': {**R, 'id': next(ids), 'module': 'open', 'number': 0, 'response': {'nome': 'Anna è', 'eta': 12}, 'user': 'athlete',
                'created': '2026-09-02T10:00:00.500000Z', 'attachments': ['d1']},
    'del_r': {**R, 'id': next(ids), 'module': 'open', 'number': 1, 'approved': False, 'attachments': ['d2'], 'response': [1, 'due']},
    'att_r': {**R, 'id': next(ids), 'module': 'open', 'number': 2, 'attachments': ['d4', 'd3', 'd9', 'd42']},
    'full_r': {**R, 'id': next(ids), 'module': 'full', 'number': 0},
    'q1': {**R, 'id': next(ids), 'module': 'queue', 'number': 0, 'queue': 1, 'approved': False, 'response': {'nome': 'Uno'}},
    'q2': {**R, 'id': next(ids), 'module': 'queue', 'number': 1, 'queue': 2, 'approved': False, 'response': {'nome': 'Due'}},
    'q3': {**R, 'id': next(ids), 'module': 'queue', 'number': 2, 'queue': 3, 'approved': False, 'response': {'nome': 'Tre'},
           'attachments': ['d5']},
    'qa': {**R, 'id': next(ids), 'module': 'queue', 'number': 3},
    'foreign_r': {**R, 'id': next(ids), 'module': 'foreign', 'number': 0, 'approved': False, 'response': {'nome': 'Estero'}},
    'other_r': {**R, 'id': next(ids), 'module': 'foreign', 'number': 1, 'attachments': ['d4']},
    'orphan_r': {**R, 'id': next(ids), 'module': 'orphan', 'number': 0, 'approved': False},
    'nullnum_r': {**R, 'id': next(ids), 'module': 'nullnum', 'number': None},
    # Key order is insertion order: the database returns them in that order.
    'e1': {**R, 'id': next(xids), 'module': 'export', 'number': 0, 'created': '2026-09-03T08:05:59.900000Z',
           'response': {'nome': 'Anna è', 'eta': 30, 'consenso': True, 'corsi': ['nuoto', 'tennis'], 'indirizzo': {'via': 'Roma', 'n': 1.5},
                        'firma': 'data:image/png;base64,AAAA', 'note': ' spazi ', 'vuoto': None, 'sito': 'ignorato'},
           'export_user': 'export_a', 'payment': 'paid', 'attachments': ['d6', 'd7']},
    'e2': {**R, 'id': next(xids), 'module': 'export', 'number': 1, 'queue': 2, 'approved': False, 'created': '2026-09-04T23:59:00Z',
           'response': {'nome': 'Bruno', 'eta': 41.5, 'consenso': False, 'corsi': [], 'indirizzo': {}}, 'payment': 'unpaid'},
    'e3': {**R, 'id': next(xids), 'module': 'export', 'number': None, 'created': '2026-09-05T00:00:00Z',
           'response': {'nome': 'http://example.test/modulo?a=1&b=2', 'eta': None, 'note': 'riga\nuno\x01due', '5': 'non letto'}},
    'e4': {**R, 'id': next(xids), 'module': 'export', 'number': 3, 'response': {}, 'export_user': 'export_b', 'payment': 'gone'},
    'plain_r': {**R, 'id': next(xids), 'module': 'export_plain', 'number': 0, 'response': [1, 2], 'attachments': ['d6']},
    'noname_r': {**R, 'id': next(xids), 'module': 'export_noname', 'number': 0},
    'nolabel_r': {**R, 'id': next(xids), 'module': 'export_nolabel', 'number': 0},
    'dict_r': {**R, 'id': next(xids), 'module': 'export_dict', 'number': 0},
    'xforeign_r': {**R, 'id': next(xids), 'module': 'export_foreign', 'number': 0, 'response': {'nome': 'Estero'}},
    'props_text_r': {**R, 'id': next(yids), 'module': 'export_props_text', 'number': 0},
    'props_list_r': {**R, 'id': next(yids), 'module': 'export_props_list', 'number': 0},
    'props_number_r': {**R, 'id': next(yids), 'module': 'export_props_number', 'number': 0},
    'label_list_r': {**R, 'id': next(yids), 'module': 'export_label_list', 'number': 0},
    'label_dict_r': {**R, 'id': next(yids), 'module': 'export_label_dict', 'number': 0},
}
RESPONSES['full_r']['attachments'] = ['d8']
# Payment.attachments links (payment label, document label).
PAYMENT_ATTACHMENTS = [['paid', 'd8']]
# The secret each seeded response was created with (the rows keep its SHA-256).
TOKENS = {'att_r': 'respondent-token-a', 'open_r1': 'respondent-token-b', 'q3': 'respondent-token-c'}
for _label, _row in RESPONSES.items():
    _row['token'] = TOKENS.get(_label, '')
    _row.setdefault('export_user', None)
    _row.setdefault('payment', None)
# Running state: queue positions and approvals change across cases.
STATE = copy.deepcopy(RESPONSES)
cases = []


def with_response_token(actor, method, path, body, *, enabled=True):
    """Requests without an account prove the response they change with its secret (sent in the body)."""
    if actor != 'anonymous' or method not in ('POST', 'DELETE') or not enabled or not path.endswith('/add-attachment') \
            or not isinstance(body, dict):
        return body
    for label, row in RESPONSES.items():
        if row['token'] and f"/response/{row['id']}/" in path:
            return {**body, 'attachment_token': row['token']}
    return body


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, headers=None, effect=None,
        throttle=None, retry_after=None, token=True):
    """token=False: an anonymous request keeps the body as authored (no response secret).
    throttle: {'preload': n, 'age': seconds}: the address's request history for the route holds n entries of that age."""
    case = {'name': f'modules-responses-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if throttle is not None:
        case['throttle'] = throttle
    if retry_after is not None:
        case['retry_after'] = retry_after
    if body is not None:
        body = with_response_token(actor, method, path, body, enabled=token)
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


OPTIONS = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
PERMISSION = lambda key: {'detail': f'Missing permission: {key}'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
SCOPED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
NO_SESSION = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
OWNER_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
METHOD = lambda method: {'detail': f'Method "{method}" not allowed.'}
BAD_UUID = ['badly formed hexadecimal UUID string']
mid = lambda label: MODULES[label]['id'] if label in MODULES else label
rid = lambda label: RESPONSES[label]['id'] if label in RESPONSES else label


def serialized(label):
    """PublicModuleSerializer(module): the fields the form reads; the association's name (None without one)."""
    row = MODULES[label]
    association = None if row['association'] is None else {'denomination': DENOMINATIONS[row['association']]}
    return {'module_id': row['id'], 'title': row['title'], 'response_message': row['response_message'],
            'allow_attachments': row['allow_attachments'], 'only_users': row['only_users'], 'elements': row['elements'],
            'sport_association': association}


def response_data(label):
    """PublicModuleResponseSerializer(response): the attachment list."""
    row = STATE[label]
    return {'attachments_docs': [{'document_id': DOCUMENTS[d]['id'], 'filename': DOCUMENTS[d]['filename']}
                                 for d in row['attachments']]}


def entry(model, key, action, changes, index, path):
    """One expected auditlog entry. key: a response/document label or '$new'
    (the row the case creates); index: an association id, '$actor' (the
    actor's own association, if any) or None."""
    return {'model': model, 'key': key, 'action': action, 'changes': changes, 'index': index, 'path': path}


def module_index(label):
    """ModuleResponses resolver: module.sport_association, else the actor's association."""
    association = MODULES[label]['association']
    return (association, 'module.sport_association') if association else ('$actor', 'actor.sport_association')


def text(value):
    return 'None' if value is None else str(value)


def update_queue(module):
    """update_queue: entries for the saves that change a queue position."""
    rows = sorted((k for k, r in STATE.items() if r['module'] == module and r['queue'] is not None), key=lambda k: STATE[k]['queue'])
    events = []
    index, path = module_index(module)
    for key in [k for k in rows if STATE[k]['approved']]:
        events.append(entry('ModuleResponses', key, 1, {'queue_position': [text(STATE[key]['queue']), 'None']}, index, path))
        STATE[key]['queue'] = None
    for position, key in enumerate([k for k in rows if not STATE[k]['approved']], start=1):
        if STATE[key]['queue'] != position:
            events.append(entry('ModuleResponses', key, 1, {'queue_position': [text(STATE[key]['queue']), str(position)]}, index, path))
            STATE[key]['queue'] = position
    return events


NEW = 0


def responded(module, actor, user, payload):
    """The create, the user save and the queue saves of modules_response_add."""
    global NEW
    NEW += 1
    label = f'$new{NEW}'
    rows = [r for r in STATE.values() if r['module'] == module]
    numbers = [r['number'] for r in rows]
    number = 0 if not rows else (None if None in numbers else max(numbers) + 1)
    assert number is not None
    queue = None
    if MODULES[module]['queue_mode']:
        queues = [r['queue'] for r in rows if r['queue'] is not None]
        queue = max(queues) + 1 if queues else 1
    accepted = not MODULES[module]['require_approval']
    index, path = module_index(module)
    created = {'module_response_id': ['None', '$id'], 'module': ['None', MODULES[module]['id']], 'creation_date': ['None', '$created'],
               'progressive_response_number': ['None', str(number)], 'response': ['None', json.dumps(payload, sort_keys=True)],
               'approved': ['None', str(accepted)], 'user': ['None', 'None'], 'payment': ['None', 'None']}
    if queue is not None:
        created['queue_position'] = ['None', str(queue)]
    events = [entry('ModuleResponses', '$new', 0, created, index, path)]
    if user:
        events.append(entry('ModuleResponses', '$new', 1, {'user': ['None', IDS[user]]}, index, path))
    STATE[label] = {'id': '$new', 'module': module, 'number': number, 'queue': queue, 'response': payload, 'approved': accepted,
                    'user': user, 'attachments': []}
    if MODULES[module]['queue_mode']:
        events += update_queue(module)
    stored = {k: STATE[label][k] for k in ('module', 'number', 'queue', 'response', 'approved', 'user')}
    STATE[label]['id'] = label
    return {'kind': 'response', 'actor': actor, 'created': stored, 'audit': events, 'label': label}


def approved(label, actor):
    row = STATE[label]
    index, path = module_index(row['module'])
    events = []
    if not row['approved']:
        events.append(entry('ModuleResponses', label, 1, {'approved': ['False', 'True']}, index, path))
        row['approved'] = True
    if MODULES[row['module']]['queue_mode']:
        events += update_queue(row['module'])
    return {'kind': 'rows', 'actor': actor, 'audit': events}


def deleted(label, actor):
    row = STATE.pop(label)
    events = [entry('ModuleResponses', label, 2, '$prepared', '$actor', 'actor.sport_association')]
    if MODULES[row['module']]['queue_mode']:
        events += update_queue(row['module'])
    return {'kind': 'rows', 'actor': actor, 'deleted_responses': [label], 'audit': events}


def decode_base64(data):
    """The view's decode_base64: data URI prefix, padding, b64decode (non-validating)."""
    if ';base64,' in data:
        data = data.split(';base64,')[1]
    missing = len(data) % 4
    if missing:
        data += '=' * (4 - missing)
    return base64.b64decode(data)


DOCUMENT_CREATED = lambda filename: {'document_id': ['None', '$id'], 'creation_date': ['None', '$created'], 'filename': ['None', filename],
                                     'token': ['None', '$token'], 'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']}


def uploaded(actor, filename, data, *, link, content_type=None, stored=True, content_encoding=None, object_name=None):
    """Document.objects.create (one create entry, actor fallback index), the
    stored object, and the new attachment link when the response exists."""
    effect = {'kind': 'document', 'actor': actor, 'filename': filename, 'link': link,
              'audit': [entry('Document', '$new', 0, DOCUMENT_CREATED(filename), '$actor', 'actor.sport_association')]}
    if stored:
        content = decode_base64(data)
        effect['object'] = {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
                            'content_type': content_type, 'cache_control': 'max-age=86400'}
        if content_encoding:
            effect['object']['content_encoding'] = content_encoding
        if object_name is not None:
            # The name under the document's folder when it is not the filename (clean_name).
            effect['object_name'] = object_name
    else:
        effect['object'] = None
    return effect


def removed_document(label, actor):
    """The link removal and document.delete(): every attachment link of the
    document goes; one delete entry with auditlog's diff of the row."""
    links = [(r, label) for r, row in STATE.items() if label in row['attachments']]
    for r, _ in links:
        STATE[r]['attachments'].remove(label)
    return {'kind': 'rows', 'actor': actor, 'deleted_documents': [label], 'removed_links': [list(pair) for pair in links],
            'audit': [entry('Document', label, 2, '$prepared', '$actor', 'actor.sport_association')]}


# ------------------------------------------------- modules/<link>/info
info = lambda link, query='': f'/modules/{link}/info' + query
INFO_OPEN = {'data': serialized('open'), 'response_data': None}
NOT_FOUND = {'error': 'module not found'}
# --- modules_response_export (first: the seeded state) ---
def export(module):
    return f'/modules/{mid(module)}/export'


def full_name(label):
    user = EXPORT_USERS[label]
    return ('%s %s' % (user['first_name'], user['last_name'])).strip()


def typed(value):
    if isinstance(value, bool):
        return {'bool': value}
    if isinstance(value, int):
        return {'int': str(value)}
    if isinstance(value, float):
        return {'float': repr(value)}
    if isinstance(value, list):
        return {'list': [typed(item) for item in value]}
    if isinstance(value, dict):
        # jsonb keeps keys ordered by length, then bytes; json.loads keeps that order.
        keys = sorted(value, key=lambda key: (len(key.encode()), key.encode()))
        return {'dict': [[key, typed(value[key])] for key in keys]}
    return value


def exported(module):
    """The export body over the module's seeded responses (insertion order)."""
    columns = [(e['props']['label'], e['props']['name']) for e in MODULES[module]['elements']
               if e['type'] not in ('heading', 'paragraph', 'link')]
    rows = []
    for row in RESPONSES.values():
        if row['module'] != module:
            continue
        values = [typed(row['response'][name]) if name in row['response'] else '-' for _, name in columns]
        created = row['created'].replace('Z', '')
        stamp = f'{created[8:10]}/{created[5:7]}/{created[:4]} {created[11:16]}'
        user = full_name(row['export_user']) if row['export_user'] else ('-' if not row['user'] else '$base-user')
        assert user != '$base-user', 'seeded base users are not authored here'
        payment = ({True: 'Pagato', False: 'Non pagato'}[EXPORT_PAYMENTS[row['payment']]['paid']] if row['payment'] else '-')
        rows.append(values + [stamp, '-' if row['number'] is None else {'int': str(row['number'])},
                              '-' if row['queue'] is None else {'int': str(row['queue'])}, user, payment,
                              'Approvata' if row['approved'] else 'Non approvata',
                              ','.join(DOCUMENTS[d]['filename'] for d in row['attachments'])])
    # A list or an object label is written as its text and sized by its item count.
    sized = [(str(label), len(label)) for label, _ in columns] + [(text, len(text)) for text in (
        'Data creazione', 'Progressivo risposta', 'Posizione in coda', 'Utente', 'Pagamento', 'Approvato', 'Allegati')]
    labels = [label for label, _ in sized]
    spec = {'engine': 'xlsxwriter', 'sheet': 'Risposte', 'columns': labels, 'rows': rows, 'index': False,
            'widths': [size + 10 for _, size in sized]}
    return {'data': {'file': {'$xlsx': spec}, 'filename': f"[$today] Risposte al modulo ({MODULES[module]['title']}).xls",
                     'type': 'xlsx'}}


NO_EXPORT = {'error': 'response not found'}
add('export-anonymous', 'anonymous', 'GET', export('export'), 401, ANONYMOUS)
add('export-admin-without-session', 'admin', 'GET', export('export'), 403, NO_SESSION)
add('export-restricted-missing-permission', 'restricted_collaborator', 'GET', export('export'), 403,
    PERMISSION('association.modules.read'))
add('export-owner-scoped-athlete', 'owner', 'GET', export('export'), 403, SCOPED, headers=OWNER_ATHLETE)
add('export-wrong-method', 'owner', 'POST', export('export'), 405, METHOD('POST'))
add('export-options', 'owner', 'OPTIONS', export('export'), 200, OPTIONS('Modules Response Export'))
add('export-malformed', 'owner', 'GET', export('abc'), 400, BAD_UUID)
add('export-athlete-malformed', 'athlete', 'GET', export('abc'), 400, BAD_UUID)
add('export-athlete-denied', 'athlete', 'GET', export('export'), 403, NOT_ASSOCIATION)
add('export-owner-without-association', 'owner_without_association', 'GET', export('export'), 500, SERVER)
add('export-unknown', 'owner', 'GET', export(UNKNOWN), 404, NO_EXPORT)
add('export-foreign-module', 'owner', 'GET', export('export_foreign'), 404, NO_EXPORT)
add('export-orphan-module', 'owner', 'GET', export('orphan'), 404, NO_EXPORT)
add('export-no-responses', 'owner', 'GET', export('export_empty'), 404, NO_EXPORT)
add('export-owner', 'owner', 'GET', export('export'), 200, exported('export'))
add('export-hex-upper', 'owner', 'GET', export(mid('export').replace('-', '').upper()), 200, exported('export'))
add('export-full-collaborator', 'full_collaborator', 'GET', export('export'), 200, exported('export'))
add('export-admin-impersonating-owner', 'admin', 'GET', export('export'), 200, exported('export'), headers=ADMIN_OWNER)
add('export-foreign-owner', 'foreign_owner', 'GET', export('export_foreign'), 200, exported('export_foreign'))
add('export-queue', 'owner', 'GET', export('queue'), 200, exported('queue'))
# Without response columns a list response is never indexed; with them its .keys() raises.
add('export-plain-list-response', 'owner', 'GET', export('export_plain'), 200, exported('export_plain'))
add('export-list-response-server-error', 'owner', 'GET', export('open'), 500, SERVER)
add('export-element-without-name', 'owner', 'GET', export('export_noname'), 500, SERVER)
add('export-label-none', 'owner', 'GET', export('export_nolabel'), 500, SERVER)
add('export-elements-dict', 'owner', 'GET', export('export_dict'), 500, SERVER)
# element['props']['label'] on a str, a list or a number raises.
add('export-props-text', 'owner', 'GET', export('export_props_text'), 500, SERVER)
add('export-props-list', 'owner', 'GET', export('export_props_list'), 500, SERVER)
add('export-props-number', 'owner', 'GET', export('export_props_number'), 500, SERVER)
# A list or an object as column label: the header cell is its text, the column width its item count.
add('export-label-list', 'owner', 'GET', export('export_label_list'), 200, exported('export_label_list'))
add('export-label-dict', 'owner', 'GET', export('export_label_dict'), 200, exported('export_label_dict'))

add('info-anonymous', 'anonymous', 'GET', info('risposte-aperto'), 200, INFO_OPEN)
add('info-admin-without-session', 'admin', 'GET', info('risposte-aperto'), 403, NO_SESSION)
add('info-restricted-missing-permission', 'restricted_collaborator', 'GET', info('risposte-aperto'), 403,
    PERMISSION('association.modules.read'))
add('info-owner-scoped-athlete', 'owner', 'GET', info('risposte-aperto'), 403, SCOPED, headers=OWNER_ATHLETE)
add('info-wrong-method', 'anonymous', 'POST', info('risposte-aperto'), 405, METHOD('POST'), body={})
add('info-options', 'anonymous', 'OPTIONS', info('risposte-aperto'), 200, OPTIONS('Modules Custom Link Info'))
add('info-unknown-link', 'anonymous', 'GET', info('nessuno'), 404, NOT_FOUND)
add('info-case-sensitive-link', 'owner', 'GET', info('RISPOSTE-APERTO'), 404, NOT_FOUND)
add('info-module-word-link', 'owner', 'GET', info('add'), 404, NOT_FOUND)
add('info-not-active', 'anonymous', 'GET', info('risposte-chiuso'), 404, {'error': 'module not active'})
add('info-foreign-expired', 'owner', 'GET', info('risposte-estero-chiuso'), 404, {'error': 'module not active'})
# The response id is checked after the module lookup and before 'enabled'.
add('info-not-active-bad-response-id', 'anonymous', 'GET', info('risposte-chiuso', '?response_id=abc'), 400, BAD_UUID)
add('info-unknown-bad-response-id', 'anonymous', 'GET', info('nessuno', '?response_id=abc'), 404, NOT_FOUND)
add('info-empty-response-id', 'anonymous', 'GET', info('risposte-aperto', '?response_id='), 400, BAD_UUID)
add('info-owner', 'owner', 'GET', info('risposte-aperto'), 200, INFO_OPEN)
add('info-athlete', 'athlete', 'GET', info('risposte-aperto'), 200, INFO_OPEN)
add('info-owner-without-association', 'owner_without_association', 'GET', info('risposte-aperto'), 200, INFO_OPEN)
add('info-response', 'anonymous', 'GET', info('risposte-aperto', '?response_id=' + rid('open_r1')), 200,
    {'data': serialized('open'), 'response_data': response_data('open_r1')})
# The public module page only reads responses of its own module.
add('info-response-of-another-module', 'anonymous', 'GET', info('risposte-aperto', '?response_id=' + rid('foreign_r')), 200,
    {'data': serialized('open'), 'response_data': None})
add('info-response-of-its-module', 'anonymous', 'GET', info('risposte-estero', '?response_id=' + rid('foreign_r')), 200,
    {'data': serialized('foreign'), 'response_data': response_data('foreign_r')})
add('info-response-hex-upper', 'athlete', 'GET', info('risposte-aperto', '?response_id=' + rid('att_r').replace('-', '').upper()), 200,
    {'data': serialized('open'), 'response_data': response_data('att_r')})
add('info-response-unknown', 'anonymous', 'GET', info('risposte-aperto', '?response_id=' + UNKNOWN), 200,
    {'data': serialized('open'), 'response_data': None})
add('info-response-last-value', 'anonymous', 'GET', info('risposte-aperto', '?response_id=abc&response_id=' + rid('del_r')), 200,
    {'data': serialized('open'), 'response_data': response_data('del_r')})
add('info-dated', 'anonymous', 'GET', info('risposte-approvazione'), 200,
    {'data': serialized('approval'), 'response_data': None})
add('info-orphan', 'owner', 'GET', info('risposte-orfano'), 200, {'data': serialized('orphan'), 'response_data': None})
add('info-foreign-association', 'anonymous', 'GET', info('risposte-estero'), 200, {'data': serialized('foreign'), 'response_data': None})
# The page of a module whose association is marked deleted answers as an unknown link, before the response id is read.
add('info-deleted-association', 'anonymous', 'GET', info('risposte-eliminata'), 404, NOT_FOUND)
add('info-deleted-association-response', 'owner', 'GET', info('risposte-eliminata', '?response_id=' + rid('open_r1')), 404, NOT_FOUND)
add('info-deleted-association-bad-response-id', 'anonymous', 'GET', info('risposte-eliminata', '?response_id=abc'), 404, NOT_FOUND)

# ------------------------------------------- modules/<id>/response/add
respond = lambda label: f'/modules/{mid(label)}/response/add'
ADDED = {'msg': 'response added', 'module_response_id': '$new', 'attachment_token': '$new'}
add('add-admin-without-session', 'admin', 'POST', respond('open'), 403, NO_SESSION, body={})
add('add-restricted-missing-permission', 'restricted_collaborator', 'POST', respond('open'), 403,
    PERMISSION('association.modules.create'), body={})
add('add-owner-scoped-athlete', 'owner', 'POST', respond('open'), 403, SCOPED, body={}, headers=OWNER_ATHLETE)
add('add-wrong-method', 'anonymous', 'GET', respond('open'), 405, METHOD('GET'))
add('add-options', 'anonymous', 'OPTIONS', respond('open'), 200, OPTIONS('Modules Response Add'))
add('add-malformed', 'anonymous', 'POST', respond('abc'), 400, BAD_UUID, body={})
add('add-unknown', 'anonymous', 'POST', respond(UNKNOWN), 404, NOT_FOUND, body={})
add('add-not-enabled', 'owner', 'POST', respond('closed'), 404, {'error': 'module not enabled'}, body={})
add('add-foreign-expired', 'anonymous', 'POST', respond('foreign_closed'), 404, {'error': 'module not enabled'}, body={})
add('add-max-reached', 'anonymous', 'POST', respond('full'), 404, {'error': 'max responses reached'}, body={'nome': 'x'})
add('add-only-users-anonymous', 'anonymous', 'POST', respond('users'), 404, {'error': 'only users'}, body={'nome': 'x'})
# A response without progressive number sorts first: None + 1 raises.
add('add-null-progressive-server-error', 'anonymous', 'POST', respond('nullnum'), 500, SERVER, body={'nome': 'x'})
# JSON null: the NOT NULL response column rejects the create.
add('add-null-body-server-error', 'anonymous', 'POST', respond('open'), 500, SERVER, raw_body='null', content_type='application/json')
PAYLOAD = {'nome': 'Bruno è', 'eta': 30, 'consensi': [True, None, 1.5], 'z': {'b': 1, 'a': '\n'}}
add('add-anonymous', 'anonymous', 'POST', respond('open'), 200, ADDED, body=PAYLOAD, effect=responded('open', None, None, PAYLOAD))
add('add-owner-approval-dated', 'owner', 'POST', respond('approval'), 200, ADDED, body={'nome': 'Proprietario'},
    effect=responded('approval', 'owner', 'owner', {'nome': 'Proprietario'}))
add('add-athlete-only-users-payment', 'athlete', 'POST', respond('users'), 200, ADDED, body={'nome': 'Atleta'},
    effect=responded('users', 'athlete', 'athlete', {'nome': 'Atleta'}))
add('add-full-collaborator-foreign-module', 'full_collaborator', 'POST', respond('foreign'), 200, ADDED, body={},
    effect=responded('foreign', 'full_collaborator', 'owner', {}))
add('add-anonymous-orphan', 'anonymous', 'POST', respond('orphan'), 200, ADDED, body=['a', 1],
    effect=responded('orphan', None, None, ['a', 1]))
add('add-owner-orphan', 'owner', 'POST', respond('orphan'), 200, ADDED, body='testo', effect=responded('orphan', 'owner', 'owner', 'testo'))
add('add-anonymous-queue', 'anonymous', 'POST', respond('queue'), 200, ADDED, body={'nome': 'Quattro'},
    effect=responded('queue', None, None, {'nome': 'Quattro'}))
add('add-admin-impersonating-queue-hex', 'admin', 'POST', respond(mid('queue').replace('-', '').upper()), 200, ADDED, headers=ADMIN_OWNER,
    body={'nome': 'Cinque'}, effect=responded('queue', 'admin', 'owner', {'nome': 'Cinque'}))
add('add-empty-body', 'anonymous', 'POST', respond('open'), 200, ADDED, effect=responded('open', None, None, {}))
# Form bodies: request.data is a QueryDict, stored through its items() (the last value of every key).
add('add-form', 'anonymous', 'POST', respond('open'), 200, ADDED, raw_body='nome=Anna&eta=12&nome=Bea+%C3%A8&vuoto=', content_type=FORM,
    effect=responded('open', None, None, {'nome': 'Bea è', 'eta': '12', 'vuoto': ''}))
add('add-form-empty-owner', 'owner', 'POST', respond('open'), 200, ADDED, raw_body='', content_type=FORM,
    effect=responded('open', 'owner', 'owner', {}))
add('add-multipart', 'athlete', 'POST', respond('open'), 200, ADDED, raw_body=multipart(('nome', 'Carla'), ('corso', 'nuoto')),
    content_type=MULTIPART, effect=responded('open', 'athlete', 'athlete', {'nome': 'Carla', 'corso': 'nuoto'}))
# An uploaded file is not JSON serializable: the create raises.
add('add-multipart-file-server-error', 'anonymous', 'POST', respond('open'), 500, SERVER,
    raw_body=multipart(('nome', 'Dario'), ('allegato', 'a.txt', 'ciao')), content_type=MULTIPART)

# ------------------------------------ modules/response/<id>/approve
approve = lambda label: f'/modules/response/{rid(label)}/approve'
NO_RESPONSE = {'error': 'response not found'}
APPROVED = {'msg': 'response approved'}
add('approve-anonymous', 'anonymous', 'POST', approve('q1'), 401, ANONYMOUS)
add('approve-admin-without-session', 'admin', 'POST', approve('q1'), 403, NO_SESSION)
add('approve-restricted-missing-permission', 'restricted_collaborator', 'POST', approve('q1'), 403,
    PERMISSION('association.modules.update'))
add('approve-owner-scoped-athlete', 'owner', 'POST', approve('q1'), 403, SCOPED, headers=OWNER_ATHLETE)
add('approve-wrong-method', 'owner', 'GET', approve('q1'), 405, METHOD('GET'))
add('approve-options', 'owner', 'OPTIONS', approve('q1'), 200, OPTIONS('Modules Response Approve'))
add('approve-malformed', 'owner', 'POST', approve('abc'), 400, BAD_UUID)
add('approve-athlete-malformed', 'athlete', 'POST', approve('abc'), 400, BAD_UUID)
add('approve-athlete-denied', 'athlete', 'POST', approve('q1'), 403, NOT_ASSOCIATION)
add('approve-owner-without-association', 'owner_without_association', 'POST', approve('q1'), 500, SERVER)
add('approve-deleted-association-owner', 'deleted_association_owner', 'POST', approve('q1'), 500, SERVER)
add('approve-foreign-response', 'owner', 'POST', approve('foreign_r'), 404, NO_RESPONSE)
add('approve-orphan-response', 'owner', 'POST', approve('orphan_r'), 404, NO_RESPONSE)
add('approve-unknown', 'owner', 'POST', approve(UNKNOWN), 404, NO_RESPONSE)
add('approve-owner-queue', 'owner', 'POST', approve('q1'), 200, APPROVED, effect=approved('q1', 'owner'))
add('approve-repeat', 'owner', 'POST', approve('q1'), 200, APPROVED, effect=approved('q1', 'owner'))
add('approve-foreign-owner', 'foreign_owner', 'POST', approve('foreign_r'), 200, APPROVED, effect=approved('foreign_r', 'foreign_owner'))
add('approve-full-collaborator-hex', 'full_collaborator', 'POST', approve(rid('del_r').replace('-', '')), 200, APPROVED,
    effect=approved('del_r', 'full_collaborator'))

# ------------------------------------- modules/response/<id>/delete
remove = lambda label: f'/modules/response/{rid(label)}/delete'
DELETED = {'msg': 'response deleted'}
add('delete-anonymous', 'anonymous', 'DELETE', remove('q2'), 401, ANONYMOUS)
add('delete-admin-without-session', 'admin', 'DELETE', remove('q2'), 403, NO_SESSION)
add('delete-restricted-missing-permission', 'restricted_collaborator', 'DELETE', remove('q2'), 403,
    PERMISSION('association.modules.delete'))
add('delete-owner-scoped-athlete', 'owner', 'DELETE', remove('q2'), 403, SCOPED, headers=OWNER_ATHLETE)
add('delete-wrong-method', 'owner', 'POST', remove('q2'), 405, METHOD('POST'))
add('delete-options', 'owner', 'OPTIONS', remove('q2'), 200, OPTIONS('Modules Response Delete'))
add('delete-malformed', 'owner', 'DELETE', remove('abc'), 400, BAD_UUID)
add('delete-athlete-denied', 'athlete', 'DELETE', remove('q2'), 403, NOT_ASSOCIATION)
add('delete-owner-without-association', 'owner_without_association', 'DELETE', remove('q2'), 500, SERVER)
add('delete-foreign-response', 'owner', 'DELETE', remove('other_r'), 404, NO_RESPONSE)
add('delete-unknown', 'owner', 'DELETE', remove(UNKNOWN), 404, NO_RESPONSE)
add('delete-owner-queue', 'owner', 'DELETE', remove('q2'), 200, DELETED, effect=deleted('q2', 'owner'))
add('delete-repeat', 'owner', 'DELETE', remove('q2'), 404, NO_RESPONSE)
add('delete-admin-impersonating-with-attachment', 'admin', 'DELETE', remove('del_r'), 200, DELETED, headers=ADMIN_OWNER,
    effect=deleted('del_r', 'admin'))
add('delete-foreign-owner', 'foreign_owner', 'DELETE', remove('foreign_r'), 200, DELETED, effect=deleted('foreign_r', 'foreign_owner'))

# --------------------------- modules/response/<id>/add-attachment
attach = lambda label: f'/modules/response/{rid(label)}/add-attachment'
ATTACHED = {'msg': 'attachment added'}
DOC_DELETED = {'msg': 'attachment deleted'}
NO_DOCUMENT = {'error': 'document not found'}
NO_RESPONSE = {'error': 'response not found'}
add('attach-admin-without-session', 'admin', 'POST', attach('att_r'), 403, NO_SESSION, body={})
add('attach-restricted-missing-permission', 'restricted_collaborator', 'POST', attach('att_r'), 403,
    PERMISSION('association.modules.update'), body={})
add('attach-restricted-delete-missing-permission', 'restricted_collaborator', 'DELETE', attach('att_r'), 403,
    PERMISSION('association.modules.update'), body={})
add('attach-owner-scoped-athlete', 'owner', 'POST', attach('att_r'), 403, SCOPED, body={}, headers=OWNER_ATHLETE)
add('attach-wrong-method', 'anonymous', 'GET', attach('att_r'), 405, METHOD('GET'))
add('attach-put-method', 'owner', 'PUT', attach('att_r'), 405, METHOD('PUT'), body={})
add('attach-options', 'anonymous', 'OPTIONS', attach('att_r'), 200, OPTIONS('Modules Response Add Attachment'))
add('attach-malformed', 'anonymous', 'POST', attach('abc'), 400, BAD_UUID, body={'filename': 'a.txt', 'base64': 'YQ=='})
add('attach-delete-malformed', 'anonymous', 'DELETE', attach('abc'), 400, BAD_UUID, body={'document_id': DOCUMENTS['d3']['id']})
add('attach-nothing-sent', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'a.txt'})
add('attach-unknown-response-nothing-sent', 'anonymous', 'POST', attach(UNKNOWN), 404, NO_RESPONSE, body={})
# 'in' on a list body: only 'filename' is present.
add('attach-list-body', 'owner', 'POST', attach('att_r'), 200, ATTACHED, body=['filename'])
# 'in' on a str body finds both substrings; request.data['filename'] raises.
add('attach-string-body-server-error', 'owner', 'POST', attach('att_r'), 500, SERVER, body='filename base64')
add('attach-anonymous', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'nota.txt', 'base64': 'Y2lhbw=='},
    effect=uploaded(None, 'nota.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAE'
add('attach-owner-data-uri-padding', 'owner', 'POST', attach('att_r'), 200, ATTACHED,
    body={'filename': 'pixel è.png', 'base64': PIXEL, 'ignorato': 1},
    effect=uploaded('owner', 'pixel è.png', PIXEL, link='att_r', content_type='image/png'))
add('attach-full-collaborator-junk-characters', 'full_collaborator', 'POST', attach(rid('open_r1').upper()), 200, ATTACHED,
    body={'filename': 'senza estensione', 'base64': 'Y2l!hbw=='},
    effect=uploaded('full_collaborator', 'senza estensione', 'Y2l!hbw==', link='open_r1', content_type='application/octet-stream'))
# Compression suffixes: mimetypes.guess_type gives the type of the inner name and an encoding, which
# S3Storage stores as ContentEncoding ('.tgz' maps to '.tar.gz'; '.Z' is case-sensitive, '.z' is no encoding).
for name, filename, kind, encoding in [('tar-gz', 'archivio.tar.gz', 'application/x-tar', 'gzip'),
                                       ('tgz', 'Backup.TGZ', 'application/x-tar', 'gzip'),
                                       ('gz-only', 'dati.gz', 'application/octet-stream', 'gzip'),
                                       ('csv-bz2', 'soci.csv.bz2', 'text/csv', 'bzip2'),
                                       ('txz', 'foto.txz', 'application/x-tar', 'xz'),
                                       ('svgz', 'logo.svgz', 'image/svg+xml', 'gzip'),
                                       ('pdf-br', 'modulo.pdf.br', 'application/pdf', 'br'),
                                       ('upper-z', 'vecchio.tar.Z', 'application/x-tar', 'compress'),
                                       ('lower-z', 'nota.z', 'application/octet-stream', None)]:
    add(f'attach-compressed-{name}', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED,
        body={'filename': filename, 'base64': 'H4sIAAAAAAAAA0vOz0nJBAA='},
        effect=uploaded(None, filename, 'H4sIAAAAAAAAA0vOz0nJBAA=', link='att_r', content_type=kind, content_encoding=encoding))
# A response that does not exist: nothing is created or stored.
add('attach-unknown-response-not-found', 'athlete', 'POST', attach(UNKNOWN), 404, NO_RESPONSE,
    body={'filename': 'orfano.txt', 'base64': 'b3JmYW5v'})
# A base64 text with one character more than a multiple of four raises after the create.
add('attach-bad-padding-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER, body={'filename': 'rotto.txt', 'base64': 'abcde'},
    effect=uploaded(None, 'rotto.txt', None, link=None, stored=False))
add('attach-non-ascii-server-error', 'owner', 'POST', attach('att_r'), 500, SERVER, body={'filename': 'accento.txt', 'base64': 'çiao'},
    effect=uploaded('owner', 'accento.txt', None, link=None, stored=False))
# Names with separators: Storage.save validates the name and S3Storage stores it under clean_name(name).
add('attach-name-subdirectory', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'cartella/nota.txt', 'base64': 'Y2lhbw=='},
    effect=uploaded(None, 'cartella/nota.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
add('attach-name-backslash', 'owner', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'cartella\\foto.png', 'base64': 'Y2lhbw=='},
    effect=uploaded('owner', 'cartella\\foto.png', 'Y2lhbw==', link='att_r', content_type='image/png', object_name='cartella/foto.png'))
add('attach-name-normalized', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED,
    body={'filename': './doppia//barra.txt', 'base64': 'Y2lhbw=='},
    effect=uploaded(None, './doppia//barra.txt', 'Y2lhbw==', link='att_r', content_type='text/plain', object_name='doppia/barra.txt'))
LONGEST = 'y' * 251 + '.txt'
add('attach-name-255-characters', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': LONGEST, 'base64': 'Y2lhbw=='},
    effect=uploaded(None, LONGEST, 'Y2lhbw==', link='att_r', content_type='text/plain'))
# The filename column refuses the text: nothing is written.
add('attach-name-256-characters-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER,
    body={'filename': 'x' * 256, 'base64': 'Y2lhbw=='})
add('attach-name-nul-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER, body={'filename': 'nul\x00.txt', 'base64': 'Y2lhbw=='})
add('attach-name-null-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER, body={'filename': None, 'base64': 'Y2lhbw=='})
# The document is created, then Storage.save refuses the name (Django's 400 page).
for name, filename in [('empty', ''), ('dot-dot', '..'), ('traversal', 'a/../b.txt'), ('backslash-traversal', 'a\\..\\b.txt'),
                       ('absolute', '/assoluto.txt'), ('trailing-slash', 'cartella/')]:
    add(f'attach-name-{name}-bad-request', 'anonymous', 'POST', attach('att_r'), 400, BAD_REQUEST,
        body={'filename': filename, 'base64': 'Y2lhbw=='}, effect=uploaded(None, filename, None, link=None, stored=False))
# The decode runs before the name check.
add('attach-name-empty-bad-padding-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER, body={'filename': '', 'base64': 'abcde'},
    effect=uploaded(None, '', None, link=None, stored=False))
# A filename that is not a str is stored as its str(); joining the path raises after the create.
for name, value, stored_text in [('integer', 5, '5'), ('boolean', True, 'True'), ('list', ['a', 1, None], "['a', 1, None]"),
                                 ('object', {'a': 'b'}, "{'a': 'b'}"), ('float', 1.5, '1.5')]:
    add(f'attach-filename-{name}-server-error', 'owner', 'POST', attach('att_r'), 500, SERVER, body={'filename': value, 'base64': 'Y2lhbw=='},
        effect=uploaded('owner', stored_text, None, link=None, stored=False))
# A base64 value that is not a str raises in the decode, after the create.
for name, value in [('integer', 5), ('null', None), ('list', ['Y2lh']), ('object', {}), ('boolean', False)]:
    add(f'attach-base64-{name}-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER,
        body={'filename': f'dati-{name}.txt', 'base64': value}, effect=uploaded(None, f'dati-{name}.txt', None, link=None, stored=False))
# Form bodies.
add('attach-form', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, raw_body='filename=modulo.txt&base64=Y2lhbw%3D%3D&filename=ultimo.txt&attachment_token=' + TOKENS['att_r'],
    content_type=FORM, effect=uploaded(None, 'ultimo.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
add('attach-form-nothing-sent', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, raw_body='filename=modulo.txt&attachment_token=' + TOKENS['att_r'], content_type=FORM)
add('attach-multipart', 'owner', 'POST', attach('att_r'), 200, ATTACHED,
    raw_body=multipart(('filename', 'parti.txt'), ('base64', 'data:text/plain;base64,Y2lhbw')), content_type=MULTIPART,
    effect=uploaded('owner', 'parti.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
# An uploaded file as base64 is not a str.
add('attach-multipart-file-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER,
    raw_body=multipart(('filename', 'caricato.txt'), ('attachment_token', TOKENS['att_r']), ('base64', 'a.txt', 'Y2lhbw==')), content_type=MULTIPART,
    effect=uploaded(None, 'caricato.txt', None, link=None, stored=False))
add('attach-delete-missing-key', 'anonymous', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={})
add('attach-delete-null-document', 'anonymous', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={'document_id': None})
add('attach-delete-unknown-document', 'owner', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={'document_id': UNKNOWN})
add('attach-delete-unknown-response-unknown-document', 'owner', 'DELETE', attach(UNKNOWN), 404, NO_RESPONSE, body={'document_id': UNKNOWN})
add('attach-delete-list-without-key', 'owner', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body=['x'])
add('attach-delete-list-key-server-error', 'owner', 'DELETE', attach('att_r'), 500, SERVER, body=['document_id'])
add('attach-delete-malformed-document-server-error', 'owner', 'DELETE', attach('att_r'), 500, SERVER, body={'document_id': 'xyz'})
# A response that does not exist: the document is not touched.
add('attach-delete-unknown-response-not-found', 'owner', 'DELETE', attach(UNKNOWN), 404, NO_RESPONSE,
    body={'document_id': DOCUMENTS['d1']['id']})
add('attach-delete-owner-own-document', 'owner', 'DELETE', attach('att_r'), 200, DOC_DELETED, body={'document_id': DOCUMENTS['d3']['id']},
    effect=removed_document('d3', 'owner'))
add('attach-delete-repeat', 'owner', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={'document_id': DOCUMENTS['d3']['id']})
add('attach-delete-full-collaborator-shared', 'full_collaborator', 'DELETE', attach('att_r'), 200, DOC_DELETED,
    body={'document_id': DOCUMENTS['d4']['id'].upper()}, effect=removed_document('d4', 'full_collaborator'))
add('attach-delete-anonymous-own-response', 'anonymous', 'DELETE', attach('q3'), 200, DOC_DELETED,
    body={'document_id': DOCUMENTS['d5']['id']}, effect=removed_document('d5', None))
# UUIDField.to_python of the lookup value: an int is uuid.UUID(int=...); other values raise.
add('attach-delete-boolean-unknown', 'anonymous', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={'document_id': True})
add('attach-delete-negative-server-error', 'anonymous', 'DELETE', attach('att_r'), 500, SERVER, body={'document_id': -1})
add('attach-delete-huge-server-error', 'anonymous', 'DELETE', attach('att_r'), 500, SERVER, body={'document_id': 2 ** 128})
add('attach-delete-float-server-error', 'anonymous', 'DELETE', attach('att_r'), 500, SERVER, body={'document_id': 42.0})
add('attach-delete-list-value-server-error', 'anonymous', 'DELETE', attach('att_r'), 500, SERVER,
    body={'document_id': [DOCUMENTS['d42']['id']]})
add('attach-delete-integer', 'owner', 'DELETE', attach('att_r'), 200, DOC_DELETED, body={'document_id': 42},
    effect=removed_document('d42', 'owner'))
add('attach-delete-form', 'anonymous', 'DELETE', attach('att_r'), 200, DOC_DELETED, raw_body='document_id=' + DOCUMENTS['d9']['id'] + '&attachment_token=' + TOKENS['att_r'],
    content_type=FORM, effect=removed_document('d9', None))
# A document a payment lists too: document.delete() removes that link with the row.
add('attach-delete-referenced-by-payment', 'owner', 'DELETE', attach('full_r'), 200, DOC_DELETED, body={'document_id': DOCUMENTS['d8']['id']},
    effect=removed_document('d8', 'owner'))

# --- who may change a response's attachments, and which documents -------------------------------
# Without an account the response's secret is needed; with one, the response's own user or its association.
# A document that is not an attachment of the response is never touched (404, nothing deleted).
for method_name, request_body in (('post', {'filename': 'nota.txt', 'base64': 'Y2lhbw=='}), ('delete', {'document_id': DOCUMENTS['d2']['id']})):
    verb = method_name.upper()
    add(f'attach-{method_name}-anonymous-no-secret', 'anonymous', verb, attach('att_r'), 404, NO_RESPONSE, body=request_body, token=False)
    add(f'attach-{method_name}-anonymous-wrong-secret', 'anonymous', verb, attach('att_r'), 404, NO_RESPONSE,
        body={**request_body, 'attachment_token': 'respondent-token-x'}, token=False)
    add(f'attach-{method_name}-anonymous-empty-secret', 'anonymous', verb, attach('att_r'), 404, NO_RESPONSE,
        body={**request_body, 'attachment_token': ''}, token=False)
    add(f'attach-{method_name}-anonymous-secret-not-text', 'anonymous', verb, attach('att_r'), 404, NO_RESPONSE,
        body={**request_body, 'attachment_token': 5}, token=False)
    add(f'attach-{method_name}-anonymous-other-response-secret', 'anonymous', verb, attach('att_r'), 404, NO_RESPONSE,
        body={**request_body, 'attachment_token': TOKENS['open_r1']}, token=False)
    add(f'attach-{method_name}-anonymous-response-without-secret', 'anonymous', verb, attach('del_r'), 404, NO_RESPONSE,
        body={**request_body, 'attachment_token': TOKENS['att_r']}, token=False)
    add(f'attach-{method_name}-foreign-owner', 'foreign_owner', verb, attach('att_r'), 404, NO_RESPONSE, body=request_body)
    add(f'attach-{method_name}-athlete-not-respondent', 'athlete', verb, attach('att_r'), 404, NO_RESPONSE, body=request_body)
add('attach-delete-other-response-document', 'owner', 'DELETE', attach('att_r'), 404, NO_DOCUMENT,
    body={'document_id': DOCUMENTS['d2']['id']})
add('attach-delete-anonymous-other-response-document', 'anonymous', 'DELETE', attach('att_r'), 404, NO_DOCUMENT,
    body={'document_id': DOCUMENTS['d1']['id']})
add('attach-delete-unlinked-document', 'full_collaborator', 'DELETE', attach('att_r'), 404, NO_DOCUMENT,
    body={'document_id': DOCUMENTS['d8']['id']})
add('attach-delete-foreign-owner-other-response', 'foreign_owner', 'DELETE', attach('q3'), 404, NO_RESPONSE,
    body={'document_id': DOCUMENTS['d5']['id']})
add('attach-post-foreign-owner-with-secret', 'foreign_owner', 'POST', attach('att_r'), 200, ATTACHED,
    body={'filename': 'ospite.txt', 'base64': 'Y2lhbw==', 'attachment_token': TOKENS['att_r']},
    effect=uploaded('foreign_owner', 'ospite.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
add('attach-post-athlete-respondent', 'athlete', 'POST', attach('open_r1'), 200, ATTACHED,
    body={'filename': 'mio.txt', 'base64': 'Y2lhbw=='},
    effect=uploaded('athlete', 'mio.txt', 'Y2lhbw==', link='open_r1', content_type='text/plain'))
add('attach-delete-athlete-respondent', 'athlete', 'DELETE', attach('open_r1'), 200, DOC_DELETED,
    body={'document_id': DOCUMENTS['d1']['id']}, effect=removed_document('d1', 'athlete'))

# --------------------------- limits of the routes open without an account
# Requests without an account are counted per address and route (30 in 600 seconds): the next one is asked to wait.
# A file above the upload limit (4096 bytes in this run) is refused before any write; the limit is for everyone.
UPLOAD_LIMIT = 4096
FULL = {'preload': 30, 'age': 10.2}
THROTTLED = {'detail': 'Request was throttled. Expected available in 590 seconds.'}
TOO_LARGE = {'error': f'Il file supera la dimensione massima consentita ({UPLOAD_LIMIT} byte).'}
BIG_TEXT = 'x' * (UPLOAD_LIMIT + 100)
add('add-anonymous-over-the-rate', 'anonymous', 'POST', respond('open'), 429, THROTTLED, body={'nome': 'Eva'}, throttle=FULL, retry_after='590')
add('add-anonymous-wrong-method-over-the-rate', 'anonymous', 'GET', respond('open'), 429, THROTTLED, throttle=FULL, retry_after='590')
add('add-anonymous-below-the-rate', 'anonymous', 'POST', respond('open'), 200, ADDED, body={'nome': 'Fabio'},
    throttle={'preload': 29, 'age': 10.2}, effect=responded('open', None, None, {'nome': 'Fabio'}))
add('add-anonymous-old-history', 'anonymous', 'GET', respond('open'), 405, METHOD('GET'), throttle={'preload': 30, 'age': 601})
add('add-owner-full-history', 'owner', 'POST', respond('open'), 200, ADDED, body={'nome': 'Gina'}, throttle=FULL,
    effect=responded('open', 'owner', 'owner', {'nome': 'Gina'}))
add('add-anonymous-body-above-the-limit', 'anonymous', 'POST', respond('open'), 413, TOO_LARGE, body={'nota': BIG_TEXT})
add('add-anonymous-body-above-the-limit-unknown-module', 'anonymous', 'POST', respond(UNKNOWN), 413, TOO_LARGE, body={'nota': BIG_TEXT})
add('add-owner-body-above-the-limit', 'owner', 'POST', respond('open'), 200, ADDED, body={'nota': BIG_TEXT},
    effect=responded('open', 'owner', 'owner', {'nota': BIG_TEXT}))
add('attach-anonymous-over-the-rate', 'anonymous', 'POST', attach('att_r'), 429, THROTTLED, body={'filename': 'nota.txt', 'base64': 'Y2lhbw=='},
    throttle=FULL, retry_after='590')
add('attach-anonymous-delete-over-the-rate', 'anonymous', 'DELETE', attach('att_r'), 429, THROTTLED, body={}, throttle=FULL, retry_after='590')
add('attach-anonymous-below-the-rate', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'nota.txt', 'base64': 'Y2lhbw=='},
    throttle={'preload': 29, 'age': 10.2}, effect=uploaded(None, 'nota.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
add('attach-owner-full-history', 'owner', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'nota.txt', 'base64': 'Y2lhbw=='},
    throttle=FULL, effect=uploaded('owner', 'nota.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
AT_LIMIT = base64.b64encode(b'z' * UPLOAD_LIMIT).decode()
OVER_LIMIT = base64.b64encode(b'z' * (UPLOAD_LIMIT + 1)).decode()
add('attach-anonymous-above-the-limit', 'anonymous', 'POST', attach('att_r'), 413, TOO_LARGE,
    body={'filename': 'grande.bin', 'base64': OVER_LIMIT})
add('attach-owner-above-the-limit-unknown-response', 'owner', 'POST', attach(UNKNOWN), 404, NO_RESPONSE,
    body={'filename': 'grande.bin', 'base64': 'data:application/octet-stream;base64,' + OVER_LIMIT})
add('attach-anonymous-at-the-limit', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'limite.bin', 'base64': AT_LIMIT},
    effect=uploaded(None, 'limite.bin', AT_LIMIT, link='att_r', content_type='application/octet-stream'))
# Text that does not decode keeps the answer it had: the create, then the error.
add('attach-anonymous-undecodable-keeps-its-answer', 'anonymous', 'POST', attach('att_r'), 500, SERVER,
    body={'filename': 'rotto.bin', 'base64': 'abcde'}, effect=uploaded(None, 'rotto.bin', None, link=None, stored=False))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Module responses info/add/approve/delete/add-attachment/export authored sequence',
                              'ids': IDS, 'location': LOCATION, 'public_upload_limit': UPLOAD_LIMIT, 'modules': MODULES, 'documents': DOCUMENTS, 'responses': RESPONSES,
                              'export_users': EXPORT_USERS, 'export_payments': EXPORT_PAYMENTS,
                              'payment_attachments': PAYMENT_ATTACHMENTS,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
