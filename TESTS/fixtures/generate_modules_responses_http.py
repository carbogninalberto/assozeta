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
auditlog records the changed ones. modules/<id>/export (XLSX only) is not
part of this sequence. Run with ``python3 -I``.
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
ids = iter(f'a1000000-0000-4000-8000-{643001 + n:012d}' for n in range(80))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000643999'
LOCATION = 'modules-responses-fixture'
READS = json.loads((HERE / 'modules_reads_http.json').read_text())
PROFILE = next(c for c in READS['cases'] if c['name'] == 'modules-reads-list-owner')['response']['data'][0]['sport_association']
assert PROFILE['sport_association_id'] == OWN

ELEMENTS = [{'type': 'heading', 'props': {'label': 'Titolo'}}, {'type': 'text', 'props': {'label': 'Nome', 'name': 'nome'}}]
BASE = {'association': OWN, 'link': None, 'created': '2026-09-01T08:00:00Z', 'title': 'Modulo', 'require_approval': False,
        'start': None, 'end': None, 'always_active': True, 'max_responses': None, 'queue_mode': False, 'only_users': False,
        'payment_required': False, 'payment_data': {}, 'response_message': None, 'allow_attachments': False, 'elements': ELEMENTS}
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
    'nullnum': {**BASE, 'id': next(ids), 'link': 'risposte-nullo'},
}
DOCUMENTS = {'d1': {'id': next(ids), 'filename': 'certificato è.pdf', 'token': next(ids)},
             'd2': {'id': next(ids), 'filename': 'foto.png', 'token': next(ids)},
             'd3': {'id': next(ids), 'filename': 'libero.txt', 'token': next(ids)},
             'd4': {'id': next(ids), 'filename': 'condiviso.pdf', 'token': next(ids)},
             'd5': {'id': next(ids), 'filename': 'anonimo.txt', 'token': next(ids)}}
R = {'created': '2026-09-02T10:00:00Z', 'queue': None, 'response': {}, 'approved': True, 'user': None, 'attachments': []}
RESPONSES = {
    'open_r1': {**R, 'id': next(ids), 'module': 'open', 'number': 0, 'response': {'nome': 'Anna è', 'eta': 12}, 'user': 'athlete',
                'created': '2026-09-02T10:00:00.500000Z', 'attachments': ['d1']},
    'del_r': {**R, 'id': next(ids), 'module': 'open', 'number': 1, 'approved': False, 'attachments': ['d2'], 'response': [1, 'due']},
    'att_r': {**R, 'id': next(ids), 'module': 'open', 'number': 2, 'attachments': ['d4']},
    'full_r': {**R, 'id': next(ids), 'module': 'full', 'number': 0},
    'q1': {**R, 'id': next(ids), 'module': 'queue', 'number': 0, 'queue': 1, 'approved': False, 'response': {'nome': 'Uno'}},
    'q2': {**R, 'id': next(ids), 'module': 'queue', 'number': 1, 'queue': 2, 'approved': False, 'response': {'nome': 'Due'}},
    'q3': {**R, 'id': next(ids), 'module': 'queue', 'number': 2, 'queue': 3, 'approved': False, 'response': {'nome': 'Tre'}},
    'qa': {**R, 'id': next(ids), 'module': 'queue', 'number': 3},
    'foreign_r': {**R, 'id': next(ids), 'module': 'foreign', 'number': 0, 'approved': False, 'response': {'nome': 'Estero'}},
    'other_r': {**R, 'id': next(ids), 'module': 'foreign', 'number': 1, 'attachments': ['d4']},
    'orphan_r': {**R, 'id': next(ids), 'module': 'orphan', 'number': 0, 'approved': False},
    'nullnum_r': {**R, 'id': next(ids), 'module': 'nullnum', 'number': None},
}
# Running state: queue positions and approvals change across cases.
STATE = copy.deepcopy(RESPONSES)
cases = []


def add(name, actor, method, path, status, response, *, body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'modules-responses-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
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


def serialized(label, *, enabled=True):
    """ModulesSerializer(module): every field, the nested association (None without one)."""
    row = MODULES[label]
    profile = {OWN: PROFILE, None: None}[row['association']]
    return {'module_id': row['id'], 'enabled': enabled, 'sport_association': profile, 'custom_link': row['link'],
            'creation_date': row['created'], 'title': row['title'], 'require_approval': row['require_approval'],
            'start_date': row['start'], 'end_date': row['end'], 'always_active': row['always_active'],
            'max_responses': row['max_responses'], 'queue_mode': row['queue_mode'], 'only_users': row['only_users'],
            'payment_required': row['payment_required'], 'payment_data': row['payment_data'],
            'response_message': row['response_message'], 'allow_attachments': row['allow_attachments'], 'elements': row['elements']}


def response_data(label):
    """ModuleResponseSerializer(response): every field, attachment pks and attachments_docs."""
    row = STATE[label]
    return {'module_response_id': row['id'], 'module': MODULES[row['module']]['id'], 'creation_date': row['created'],
            'progressive_response_number': row['number'], 'queue_position': row['queue'], 'response': row['response'],
            'approved': row['approved'], 'user': IDS[row['user']] if row['user'] else None, 'payment': None,
            'attachments': [DOCUMENTS[d]['id'] for d in row['attachments']],
            'attachments_docs': [{'document_id': DOCUMENTS[d]['id'], 'filename': DOCUMENTS[d]['filename'],
                                  'token': DOCUMENTS[d]['token']} for d in row['attachments']]}


# ModuleResponseSerializer(None).data: get_initial() of the writable fields
# (BooleanField False, the many related field [], every other field None).
INITIAL = {'module_response_id': None, 'module': None, 'creation_date': None, 'progressive_response_number': None,
           'queue_position': None, 'response': None, 'approved': False, 'user': None, 'payment': None, 'attachments': []}


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


def uploaded(actor, filename, data, *, link, content_type=None, stored=True):
    """Document.objects.create (one create entry, actor fallback index), the
    stored object, and the new attachment link when the response exists."""
    effect = {'kind': 'document', 'actor': actor, 'filename': filename, 'link': link,
              'audit': [entry('Document', '$new', 0, DOCUMENT_CREATED(filename), '$actor', 'actor.sport_association')]}
    if stored:
        content = decode_base64(data)
        effect['object'] = {'size': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
                            'content_type': content_type, 'cache_control': 'max-age=86400'}
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
add('info-response-of-another-module', 'anonymous', 'GET', info('risposte-aperto', '?response_id=' + rid('foreign_r')), 200,
    {'data': serialized('open'), 'response_data': response_data('foreign_r')})
add('info-response-hex-upper', 'athlete', 'GET', info('risposte-aperto', '?response_id=' + rid('att_r').replace('-', '').upper()), 200,
    {'data': serialized('open'), 'response_data': response_data('att_r')})
add('info-response-unknown', 'anonymous', 'GET', info('risposte-aperto', '?response_id=' + UNKNOWN), 200,
    {'data': serialized('open'), 'response_data': INITIAL})
add('info-response-last-value', 'anonymous', 'GET', info('risposte-aperto', '?response_id=abc&response_id=' + rid('del_r')), 200,
    {'data': serialized('open'), 'response_data': response_data('del_r')})
add('info-dated', 'anonymous', 'GET', info('risposte-approvazione'), 200,
    {'data': serialized('approval'), 'response_data': None})
add('info-orphan', 'owner', 'GET', info('risposte-orfano'), 200, {'data': serialized('orphan'), 'response_data': None})

# ------------------------------------------- modules/<id>/response/add
respond = lambda label: f'/modules/{mid(label)}/response/add'
ADDED = {'msg': 'response added', 'module_response_id': '$new'}
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
add('attach-unknown-response-nothing-sent', 'anonymous', 'POST', attach(UNKNOWN), 200, ATTACHED, body={})
# 'in' on a list body: only 'filename' is present.
add('attach-list-body', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body=['filename'])
# 'in' on a str body finds both substrings; request.data['filename'] raises.
add('attach-string-body-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER, body='filename base64')
add('attach-anonymous', 'anonymous', 'POST', attach('att_r'), 200, ATTACHED, body={'filename': 'nota.txt', 'base64': 'Y2lhbw=='},
    effect=uploaded(None, 'nota.txt', 'Y2lhbw==', link='att_r', content_type='text/plain'))
PIXEL = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAE'
add('attach-owner-data-uri-padding', 'owner', 'POST', attach('att_r'), 200, ATTACHED,
    body={'filename': 'pixel è.png', 'base64': PIXEL, 'ignorato': 1},
    effect=uploaded('owner', 'pixel è.png', PIXEL, link='att_r', content_type='image/png'))
add('attach-full-collaborator-junk-characters', 'full_collaborator', 'POST', attach(rid('open_r1').upper()), 200, ATTACHED,
    body={'filename': 'senza estensione', 'base64': 'Y2l!hbw=='},
    effect=uploaded('full_collaborator', 'senza estensione', 'Y2l!hbw==', link='open_r1', content_type='application/octet-stream'))
# The document is created (and the object stored) before the missing response raises.
add('attach-unknown-response-server-error', 'athlete', 'POST', attach(UNKNOWN), 500, SERVER,
    body={'filename': 'orfano.txt', 'base64': 'b3JmYW5v'},
    effect=uploaded('athlete', 'orfano.txt', 'b3JmYW5v', link=None, content_type='text/plain'))
# A base64 text with one character more than a multiple of four raises after the create.
add('attach-bad-padding-server-error', 'anonymous', 'POST', attach('att_r'), 500, SERVER, body={'filename': 'rotto.txt', 'base64': 'abcde'},
    effect=uploaded(None, 'rotto.txt', None, link=None, stored=False))
add('attach-non-ascii-server-error', 'owner', 'POST', attach('att_r'), 500, SERVER, body={'filename': 'accento.txt', 'base64': 'çiao'},
    effect=uploaded('owner', 'accento.txt', None, link=None, stored=False))
add('attach-delete-missing-key', 'anonymous', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={})
add('attach-delete-null-document', 'anonymous', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={'document_id': None})
add('attach-delete-unknown-document', 'owner', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={'document_id': UNKNOWN})
add('attach-delete-unknown-response-unknown-document', 'owner', 'DELETE', attach(UNKNOWN), 404, NO_DOCUMENT, body={'document_id': UNKNOWN})
add('attach-delete-list-without-key', 'anonymous', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body=['x'])
add('attach-delete-list-key-server-error', 'anonymous', 'DELETE', attach('att_r'), 500, SERVER, body=['document_id'])
add('attach-delete-malformed-document-server-error', 'owner', 'DELETE', attach('att_r'), 500, SERVER, body={'document_id': 'xyz'})
# The response is None: .attachments raises before the document is touched.
add('attach-delete-unknown-response-server-error', 'owner', 'DELETE', attach(UNKNOWN), 500, SERVER,
    body={'document_id': DOCUMENTS['d1']['id']})
add('attach-delete-owner-unlinked', 'owner', 'DELETE', attach('att_r'), 200, DOC_DELETED, body={'document_id': DOCUMENTS['d3']['id']},
    effect=removed_document('d3', 'owner'))
add('attach-delete-repeat', 'owner', 'DELETE', attach('att_r'), 404, NO_DOCUMENT, body={'document_id': DOCUMENTS['d3']['id']})
add('attach-delete-full-collaborator-shared', 'full_collaborator', 'DELETE', attach('att_r'), 200, DOC_DELETED,
    body={'document_id': DOCUMENTS['d4']['id'].upper()}, effect=removed_document('d4', 'full_collaborator'))
add('attach-delete-anonymous-other-response', 'anonymous', 'DELETE', attach('q3'), 200, DOC_DELETED,
    body={'document_id': DOCUMENTS['d5']['id']}, effect=removed_document('d5', None))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Module responses info/add/approve/delete/add-attachment authored sequence',
                              'ids': IDS, 'location': LOCATION, 'modules': MODULES, 'documents': DOCUMENTS, 'responses': RESPONSES,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
