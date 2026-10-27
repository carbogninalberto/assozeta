"""Author the staff-board HTTP sequence from the characterized contract.

Expectations are written from docs/architecture/go-migration/staff-board.md and
the shared identity fixture (TESTS/fixtures/message_mutations.json), not
observed from Django. Cases run in order on one fresh fixture per transport;
mutation effects accumulate. ``$new:<label>`` binds a generated row ID and
``$now`` a server write time; the contract validates both before comparing.
Run with ``python3 -I`` and commit the output.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('staff_board_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, FULL, RESTRICTED = IDS['owner'], IDS['full_collaborator'], IDS['restricted_collaborator']
FOREIGN_OWNER = IDS['foreign_owner']
ROW = {label: f'a1000000-0000-4000-8000-{730001 + n:012d}' for n, label in enumerate(
    ('owner_pinned', 'full', 'deleted_author', 'owner_delete', 'foreign'))}
DOCUMENT = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Fissato'}]}]}
SEED = {
    'owner_pinned': {'association': OWN, 'author': OWNER, 'content': 'Fissato', 'document': DOCUMENT, 'pinned': True, 'at': '2026-10-23T09:00:00Z'},
    'full': {'association': OWN, 'author': FULL, 'content': 'Dal collaboratore', 'document': None, 'pinned': False, 'at': '2026-10-23T10:00:00Z'},
    'deleted_author': {'association': OWN, 'author': None, 'content': 'Autore rimosso', 'document': None, 'pinned': False, 'at': '2026-10-23T08:00:00Z'},
    'owner_delete': {'association': OWN, 'author': OWNER, 'content': 'Da eliminare', 'document': None, 'pinned': False, 'at': '2026-10-23T07:00:00Z'},
    'foreign': {'association': FOREIGN, 'author': FOREIGN_OWNER, 'content': 'Estero', 'document': None, 'pinned': False, 'at': '2026-10-23T11:00:00Z'},
}
PERMISSION_EN = 'You do not have permission to perform this action.'
NOT_FOUND = {'error': 'message not found.'}
cases = []
state = {label: dict(values, id=ROW[label]) for label, values in SEED.items()}


def row(label, actor, **changes):
    values = dict(state[label], **changes)
    return {'staff_board_message_id': values['id'], 'sport_association': values['association'], 'author': values['author'],
            'author_name': 'Message Fixture' if values['author'] else 'Utente eliminato', 'author_avatar': None,
            'is_owner': values['author'] is not None and values['author'] == actor, 'document': values['document'],
            'content': values['content'], 'pinned': values['pinned'], 'created_at': values['at'],
            'updated_at': values.get('updated', values['at'])}


def listing(actor, association=OWN):
    labels = [label for label, values in state.items() if values['association'] == association]
    labels.sort(key=lambda label: (not state[label]['pinned'], [-ord(c) for c in state[label]['at']]))
    return {'data': [row(label, actor) for label in labels]}


def add(name, actor, method, path, status, response, *, body=None, headers=None, effect=None):
    case = {'name': f'staff-board-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect or {'kind': 'none'}}
    if body is not None:
        case['body'] = body
    cases.append(case)


LIST, ADD = '/communications/staff-board/list', '/communications/staff-board/add'
update = lambda label: f'/communications/staff-board/{ROW.get(label, label)}/update'
delete = lambda label: f'/communications/staff-board/{ROW.get(label, label)}/delete'

add('list-owner', 'owner', 'GET', LIST, 200, listing(OWNER))
add('list-full-collaborator', 'full_collaborator', 'GET', LIST, 200, listing(FULL))
add('list-restricted-read-permission', 'restricted_collaborator', 'GET', LIST, 200, listing(RESTRICTED))
add('list-foreign-owner-isolated', 'foreign_owner', 'GET', LIST, 200, listing(FOREIGN_OWNER, FOREIGN))
add('list-admin-impersonating-owner', 'admin', 'GET', LIST, 200, listing(OWNER),
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER})
add('list-athlete-denied', 'athlete', 'GET', LIST, 403, {'detail': PERMISSION_EN})
add('list-anonymous', 'anonymous', 'GET', LIST, 401, {'detail': 'Authentication credentials were not provided.'})
add('list-wrong-method', 'owner', 'POST', LIST, 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('list-options', 'owner', 'OPTIONS', LIST, 200, {'name': 'Staff Board Messages List',
    'description': 'Internal staff board: list all messages of the association, pinned first.\nVisible to admins and collaborators with association.communication.messages.read.',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']})
add('add-restricted-denied', 'restricted_collaborator', 'POST', ADD, 403, {'detail': 'Missing permission: association.communication.messages.create'}, body={'content': 'x'})
add('add-athlete-denied', 'athlete', 'POST', ADD, 403, {'detail': PERMISSION_EN}, body={'content': 'x'})
add('add-blank', 'owner', 'POST', ADD, 400, {'content': ['This field may not be blank.']}, body={'content': ''})
add('add-non-object', 'owner', 'POST', ADD, 400, {'non_field_errors': ['Invalid data. Expected a dictionary, but got list.']}, body=[])
add('add-invalid-document', 'owner', 'POST', ADD, 400, {'non_field_errors': ['Documento non valido.']}, body={'document': {'type': 'paragraph'}})
added = {'staff_board_message_id': '$new:owner_text', 'sport_association': OWN, 'author': OWNER, 'author_name': 'Message Fixture',
         'author_avatar': None, 'is_owner': True, 'document': None, 'content': 'Nuovo avviso', 'pinned': False,
         'created_at': '$now', 'updated_at': '$now'}
add('add-owner-text', 'owner', 'POST', ADD, 201, {'data': added}, body={'content': '  Nuovo avviso  '},
    effect={'kind': 'create', 'label': 'owner_text', 'values': {'sport_association_id': OWN, 'author_id': OWNER,
            'content': 'Nuovo avviso', 'document': None, 'pinned': False}})
rich = {'type': 'doc', 'content': [{'type': 'paragraph', 'attrs': {'onclick': 'x'}, 'content': [{'type': 'text', 'text': 'Ricco'}]}]}
clean = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [{'type': 'text', 'text': 'Ricco'}]}]}
add('add-collaborator-document', 'full_collaborator', 'POST', ADD, 201,
    {'data': dict(added, staff_board_message_id='$new:full_document', author=FULL, document=clean, content='Ricco', pinned=True)},
    body={'document': rich, 'pinned': 'yes'},
    effect={'kind': 'create', 'label': 'full_document', 'values': {'sport_association_id': OWN, 'author_id': FULL,
            'content': 'Ricco', 'document': clean, 'pinned': True}})
add('update-malformed-id', 'owner', 'PATCH', update('not-a-uuid'), 400, ['badly formed hexadecimal UUID string'], body={'pinned': True})
add('update-foreign-not-found', 'owner', 'PATCH', update('foreign'), 404, NOT_FOUND, body={'pinned': True})
add('update-restricted-denied', 'restricted_collaborator', 'PATCH', update('full'), 403, {'detail': 'Missing permission: association.communication.messages.update'}, body={'pinned': True})
add('update-non-author-content', 'owner', 'PATCH', update('full'), 403, {'error': 'Solo l’autore può modificare questo messaggio.'}, body={'content': 'x'})
add('update-non-author-invalid-content-precedence', 'owner', 'PATCH', update('full'), 403,
    {'error': 'Solo l’autore può modificare questo messaggio.'}, body={'content': ''})
add('update-deleted-author-document', 'owner', 'PATCH', update('deleted_author'), 403,
    {'error': 'Solo l’autore può modificare questo messaggio.'}, body={'document': DOCUMENT})
state['full']['pinned'] = True
state['full']['updated'] = '$now'
add('update-owner-pins-collaborator-row', 'owner', 'PATCH', update('full'), 200, {'data': row('full', OWNER)}, body={'pinned': True},
    effect={'kind': 'update', 'row': ROW['full'], 'values': {'pinned': True}})
state['full']['content'] = 'Testo aggiornato'
add('update-author-content', 'full_collaborator', 'PATCH', update('full'), 200, {'data': row('full', FULL)}, body={'content': 'Testo aggiornato'},
    effect={'kind': 'update', 'row': ROW['full'], 'values': {'content': 'Testo aggiornato', 'document': None}})
add('update-author-blank', 'full_collaborator', 'PATCH', update('full'), 400, {'content': ['This field may not be blank.']}, body={'content': ''})
add('delete-non-author', 'owner', 'DELETE', delete('full'), 403, {'error': 'Solo l’autore può eliminare questo messaggio.'})
add('delete-deleted-author', 'owner', 'DELETE', delete('deleted_author'), 403, {'error': 'Solo l’autore può eliminare questo messaggio.'})
add('delete-foreign-not-found', 'owner', 'DELETE', delete('foreign'), 404, NOT_FOUND)
add('delete-restricted-denied', 'restricted_collaborator', 'DELETE', delete('owner_delete'), 403, {'detail': 'Missing permission: association.communication.messages.delete'})
add('delete-owner-own-row', 'owner', 'DELETE', delete('owner_delete'), 200, {'msg': 'message deleted.'},
    effect={'kind': 'delete', 'row': ROW['owner_delete']})
add('delete-repeat-not-found', 'owner', 'DELETE', delete('owner_delete'), 404, NOT_FOUND)
add('forged-session-rejected', 'admin', 'GET', LIST, 403, {'detail': 'Sessione di impersonificazione scaduta o revocata. Torna al tuo account.', 'impersonation_invalid': 'True'},
    headers={'X-Impersonation-Id': '$session:forged', 'User-Id': OWNER})

names = [case['name'] for case in cases]
assert len(names) == len(set(names))
source = {'matrix_version': 1, 'scope': 'Staff-board HTTP list/add/update/delete authored sequence',
          'ids': dict(IDS, **{f'staff_board_{k}': v for k, v in ROW.items()}), 'seed': {ROW[k]: v for k, v in SEED.items()},
          'normalization': ['$new:<label> generated UUID4 row IDs', '$now write timestamps within the request window'],
          'cases': cases}
OUTPUT.write_text(json.dumps(source, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
