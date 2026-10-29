"""Author the sport-associations/<uid>/admin-update HTTP sequence.

Reuses the association-list seed (pinned associations, onboarding and
billing rows). The view assigns raw request values to the first billing
subscription and the association's notes and calls save(); auditlog diffs
those raw in-memory values against the stored row on pre_save. Effects name
the stored billing values, the stored notes and every audit entry (model,
object, changes, index). Order matters. Run with ``python3 -I`` and commit.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('association_admin_update_http.json')
LIST = json.loads(Path(__file__).with_name('association_list_http.json').read_text())
IDS, PLAN = LIST['ids'], LIST['plan']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
BILLING = 'a1000000-0000-4000-8000-000000780021'
path = lambda value: f'/sport-associations/{value}/admin-update'
OK = {'msg': 'Modifiche salvate.'}
NOTES_ERROR = {'msg': "Errore durante l'aggiornamento delle note."}
SERVER = '<stable-server-error-page>'
ADMIN_INDEX = {'association': 'admin_association', 'path': 'actor.sport_association'}
SELF_INDEX = {'association': 'own_association', 'path': 'self'}
cases = []
state = {'billing': {'auto_renewal': False, 'renewal_type': 2, 'ends_on': '2026-12-31T23:59:59.123456Z', 'billing_plan': PLAN},
         'notes': 'Note interne'}


def add(name, actor, status, response, *, target=OWN, body=None, raw_body=None, content_type=None, method='POST', audits=(), headers=None):
    case = {'name': f'association-admin-update-{name}', 'actor': actor, 'method': method, 'path': path(target),
            'headers': headers or {}, 'status': status, 'response': response,
            'effect': {'billing': dict(state['billing']), 'notes': state['notes'], 'audits': list(audits)}}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def billing_audit(changes):
    return {'model': 'billingsubscription', 'id': BILLING, 'repr': f'BillingSubscription object ({BILLING})', 'changes': changes, 'index': ADMIN_INDEX}


def notes_audit(old, new):
    return {'model': 'sportassociation', 'id': OWN, 'repr': f'SportAssociation object ({OWN})', 'changes': {'notes': [old, new]}, 'index': SELF_INDEX}


for actor in ('owner', 'full_collaborator'):
    add(f'{actor.replace("_", "-")}-not-superuser', actor, 401, '<empty>', body={'billing_subscription': {}})
add('admin-impersonating-owner-not-superuser', 'admin', 401, '<empty>', body={'billing_subscription': {}},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']})
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body={})
# The administrator exemption only matches 36 hex/hyphen characters.
add('non-uuid-path-admin-denied', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    target='not-a-uuid', body={})
add('malformed-id', 'admin', 400, ['badly formed hexadecimal UUID string'], target='-' * 36, body={})
add('missing-association', 'admin', 404, '<empty>', target=IDS['missing'], body={})
add('deleted-association', 'admin', 404, '<empty>', target=IDS['deleted_association'], body={})
add('association-without-billing', 'admin', 404, '<empty>', target=FOREIGN, body={'billing_subscription': {}})
add('missing-billing-key', 'admin', 400, '<empty>', body={'notes': 'x'})
add('list-body-server-error', 'admin', 500, SERVER, body=[])
add('billing-list-server-error', 'admin', 500, SERVER, body={'billing_subscription': []})
# Unchanged values: both saves run, auditlog sees no difference.
add('empty-billing-no-changes', 'admin', 200, OK, body={'billing_subscription': {}})
body = {'billing_subscription': {'auto_renewal': 'True', 'renewal_type': 2, 'ends_on': '2027-01-02T03:04:05Z',
                                 'billing_plan': PLAN.upper()}, 'notes': 'Nuove note è'}
audits = [billing_audit({'auto_renewal': ['False', 'True'], 'ends_on': ['2026-12-31 23:59:59.123456', '2027-01-02 03:04:05'],
                         'billing_plan': [PLAN, PLAN.upper()]}), notes_audit('Note interne', 'Nuove note è')]
state['billing'].update(auto_renewal=True, ends_on='2027-01-02T03:04:05Z')
state['notes'] = 'Nuove note è'
add('updates-billing-and-notes', 'admin', 200, OK, body=body, audits=audits)
# Raw in-memory values are diffed: the integer 1.0 renders '1.0' although 1 is stored.
state['billing']['renewal_type'] = 1
state['notes'] = '5'
add('raw-values-diffed', 'admin', 200, OK, body={'billing_subscription': {'renewal_type': 1.0}, 'notes': 5},
    audits=[billing_audit({'renewal_type': ['2', '1.0']}), notes_audit('Nuove note è', '5')])
# An invalid boolean fails the billing save after auditlog's pre_save entry.
add('invalid-boolean-server-error', 'admin', 500, SERVER, body={'billing_subscription': {'auto_renewal': 'maybe'}},
    audits=[billing_audit({'auto_renewal': ['True', 'maybe']})])
state['notes'] = None
add('null-notes', 'admin', 200, OK, body={'billing_subscription': {}, 'notes': None}, audits=[notes_audit('5', 'None')])
add('wrong-method', 'admin', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'admin', 200, {'name': 'Sport Association Admin Update', 'description': 'List all sport associations',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Superuser association admin-update authored sequence with audit effects',
                              'ids': IDS, 'billing_id': BILLING, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
