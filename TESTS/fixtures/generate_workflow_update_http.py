"""Author the communications/workflows/<id>/update HTTP sequence (workflow-update.md, automation-workflows.md).

communication_workflows_update is an @api_view(['PATCH']) with IsAuthenticated
and IsAssociationUser (unmapped in permissions_registry.py). It calls
is_sport_association(), is_valid_uuid(workflow_id) (400 list), then
AutomationWorkflow.objects.filter(sport_association=request.user.sport_association,
pk=...).first() (missing or foreign: 404 {'msg': 'workflow not found.'}; a missing
or soft-deleted association: 500). AutomationWorkflowSerializer(partial=True,
fields '__all__'): a writable automation_workflow_id (UniqueValidator), the
tenant-scoped sport_association relation (another tenant's association raises
DRF's generic 403 during validation, whatever the other errors), name
CharField(255), description nullable and blank, automation_tree a list whose
message nodes use email, enabled BooleanField; any validation error is 400
{'msg': 'workflow not valid.'}. save() writes every column (updated_at always
changes; auditlog diffs before auto_now runs, so it lists only the changed request
fields and an unchanged save has no entry).

When the save turns a disabled workflow on: inside a try block (errors only
logged), a cron trigger whose data has 'triggered' True is reset to False and
saved again (a second audit entry); then trigger() runs outside the try. It reads
automation_tree[0]['value'] (an empty tree, a non-object node or a missing key
raise: 500 after the saves, requests are not atomic). A cron trigger parses
data['time'] with datetime.fromisoformat and localizes it to Europe/Rome (a
missing time, a non-string, an invalid or an aware time raise: 500); a past time
returns without effect; a future time sends a Celery task (not in this
sequence). A null trigger value with more than one node dereferences a missing
subscription (500). Other triggers schedule nothing. 200 {'msg': 'workflow updated.'}.
Run with ``python3 -I`` after the message-mutations fixture.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('workflow_update_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
SERVER = '<stable-server-error-page>'
ids = iter(f'a1000000-0000-4000-8000-{962001 + n:012d}' for n in range(100))
UNKNOWN = 'a1000000-0000-4000-8000-000000962999'

MESSAGE = {'id': 'message', 'value': 'email', 'data': {'subject': 'Ciao à'}}
TREE_PLAIN = [{'id': 'trigger', 'value': 'new_subscription', 'data': {'b': 1, 'a': [True, None, 1.5]}}, MESSAGE]


def cron(data, *rest):
    return [{'id': 'trigger', 'value': 'cron', 'data': data}, *rest]


def workflow(tree, *, enabled=False, association=OWN, name=None, description=None):
    return {'id': next(ids), 'association': association, 'name': name or 'Flusso', 'description': description, 'tree': tree,
            'enabled': enabled, 'created_at': '2026-10-20T09:00:00.250000Z', 'updated_at': '2026-10-21T10:00:00Z'}


WORKFLOWS = {
    'plain': workflow(TREE_PLAIN, name='Benvenuto', description='Vecchia'),
    'enabled': workflow(TREE_PLAIN, enabled=True, name='Attivo'),
    'cron_past': workflow(cron({'time': '2020-01-01T10:00', 'triggered': True, 'target': 'all'}, MESSAGE)),
    'cron_past_untriggered': workflow(cron({'time': '2021-03-04 05:06:07.5', 'triggered': False})),
    'cron_bad_time': workflow(cron({'time': 'domani', 'triggered': True})),
    'cron_aware': workflow(cron({'time': '2020-01-01T10:00:00+01:00'})),
    'cron_no_data': workflow([{'id': 'trigger', 'value': 'cron'}]),
    'cron_string_data': workflow(cron('triggered')),
    'cron_time_number': workflow(cron({'time': 20200101})),
    'cron_bad_date': workflow(cron({'time': '2020-13-01T10:00'})),
    'empty_tree': workflow([]),
    'null_node_single': workflow([{'id': 'trigger', 'value': None}]),
    'null_node_pair': workflow([{'id': 'trigger', 'value': None}, MESSAGE]),
    'missing_value': workflow([{'id': 'trigger'}]),
    'string_node': workflow(['testo']),
    'foreign': workflow(TREE_PLAIN, association=FOREIGN, name='Estero'),
}
STATE = copy.deepcopy(WORKFLOWS)
cases = []


def add(name, actor, path, status, response, *, method='PATCH', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'workflow-update-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def path(label):
    return f'/communications/workflows/{WORKFLOWS[label]["id"] if label in WORKFLOWS else label}/update'


def text(value):
    return 'None' if value is None else str(value)


def dumps(tree):
    return json.dumps(tree, sort_keys=True)


def saved(actor, label, fields, *, reset=False, session=None):
    """Apply a partial update (and the cron reset) to the running state; one audit entry per save."""
    row = STATE[label]
    columns = {'name': 'name', 'description': 'description', 'automation_tree': 'tree', 'enabled': 'enabled'}
    changes = {}
    for field, value in fields.items():
        old = row[columns[field]]
        if field == 'automation_tree':
            if dumps(old) != dumps(value):
                changes[field] = [dumps(old), dumps(value)]
        elif old != value:
            changes[field] = [text(old), text(value)]
        row[columns[field]] = copy.deepcopy(value)
    # auditlog diffs in pre_save, before auto_now sets updated_at: an unchanged save has no entry.
    audit = [{'changes': changes}] if changes else []
    if reset:
        old = copy.deepcopy(row['tree'])
        row['tree'][0]['data']['triggered'] = False
        audit.append({'changes': {'automation_tree': [dumps(old), dumps(row['tree'])]}})
    return {'actor': actor, 'session': session, 'label': label,
            'row': {'name': row['name'], 'description': row['description'], 'automation_tree': copy.deepcopy(row['tree']),
                    'enabled': row['enabled']}, 'audit': audit}


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
NOT_FOUND = {'msg': 'workflow not found.'}
INVALID = {'msg': 'workflow not valid.'}
UPDATED = {'msg': 'workflow updated.'}
UNMAPPED = lambda label: {'detail': f"Access denied: No permission mapping for endpoint '{path(label).strip('/')}'. Contact administrator to grant access."}

add('anonymous', 'anonymous', path('plain'), 401, ANONYMOUS, body={})
add('admin-without-session', 'admin', path('plain'), 403, ADMIN, body={})
add('restricted-unmapped', 'restricted_collaborator', path('plain'), 403, UNMAPPED('plain'), body={'name': 'x'})
add('owner-scoped-athlete-denied', 'owner', path('plain'), 403, SCOPED_DENIED, body={'name': 'x'}, headers=SCOPED)
add('athlete-forbidden', 'athlete', path('plain'), 403, FORBIDDEN, body={'name': 'x'})
add('owner-without-association-malformed', 'owner_without_association', path('nope'), 400, ['badly formed hexadecimal UUID string'], body={})
add('owner-without-association-server-error', 'owner_without_association', path('plain'), 500, SERVER, body={})
add('deleted-association-server-error', 'deleted_association_owner', path('plain'), 500, SERVER, body={})
add('wrong-method', 'owner', path('plain'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={})
add('options', 'owner', path('plain'), 200, {'name': 'Communication Workflows Update', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('malformed-id', 'owner', path('not-a-uuid'), 400, ['badly formed hexadecimal UUID string'], body={'name': 'x'})
add('missing-not-found', 'owner', path(UNKNOWN), 404, NOT_FOUND, body={'name': 'x'})
add('foreign-not-found', 'owner', path('foreign'), 404, NOT_FOUND, body={'name': 'x'})
add('foreign-owner-not-found', 'foreign_owner', path('plain'), 404, NOT_FOUND, body={'name': 'x'})

for label, body in (('blank-name', {'name': '   '}), ('long-name', {'name': 'x' * 256}), ('null-name', {'name': None}),
                    ('bool-name', {'name': True}), ('tree-object', {'automation_tree': {}}), ('tree-null', {'automation_tree': None}),
                    ('tree-string', {'automation_tree': '[]'}),
                    ('sms-message', {'automation_tree': [{'id': 'message', 'value': 'sms'}]}), ('enabled-invalid', {'enabled': 'maybe'}),
                    ('enabled-null', {'enabled': None}), ('id-malformed', {'automation_workflow_id': 'nope'}),
                    ('id-duplicate', {'automation_workflow_id': WORKFLOWS['foreign']['id']}),
                    ('association-unknown', {'sport_association': UNKNOWN}),
                    ('association-bool', {'sport_association': True}), ('list-body', [])):
    add(f'invalid-{label}', 'owner', path('plain'), 400, INVALID, body=body)
add('invalid-null-body', 'owner', path('plain'), 400, INVALID, raw_body='null', content_type='application/json')
add('foreign-association-forbidden', 'owner', path('plain'), 403, FORBIDDEN, body={'sport_association': FOREIGN})
# The relation resolves through the default manager; the soft-deleted association is another tenant's row.
add('deleted-association-relation-forbidden', 'owner', path('plain'), 403, FORBIDDEN, body={'sport_association': IDS['deleted_association']})
add('foreign-association-before-other-errors', 'owner', path('plain'), 403, FORBIDDEN,
    body={'automation_workflow_id': 'nope', 'name': '', 'sport_association': FOREIGN})

TREE_NEW = [{'id': 'trigger', 'value': 'new_subscription', 'data': {'z': 'ultimo', 'a': 'primo'}}]
add('fields-owner', 'owner', path('plain'), 200, UPDATED, body={
    'name': 'Nuovo è', 'description': None, 'automation_tree': TREE_NEW, 'sport_association': OWN,
    'automation_workflow_id': WORKFLOWS['plain']['id'], 'created_at': '2020-01-01T00:00:00Z', 'updated_at': '2020-01-01T00:00:00Z',
    'unknown': 'ignored'},
    effect=saved('owner', 'plain', {'name': 'Nuovo è', 'description': None, 'automation_tree': TREE_NEW}))
add('empty-body-full-collaborator-no-audit', 'full_collaborator', path('plain'), 200, UPDATED, body={}, effect=saved('full_collaborator', 'plain', {}))
add('same-tree-other-key-order-no-audit', 'owner', path('plain'), 200, UPDATED,
    body={'automation_tree': [{'data': {'a': 'primo', 'z': 'ultimo'}, 'value': 'new_subscription', 'id': 'trigger'}]},
    effect=saved('owner', 'plain', {'automation_tree': TREE_NEW}))
add('form-fields', 'owner', path('plain'), 200, UPDATED, raw_body='name=Modulo&description=&enabled=false',
    content_type='application/x-www-form-urlencoded', effect=saved('owner', 'plain', {'name': 'Modulo', 'description': '', 'enabled': False}))
add('admin-impersonating-owner', 'admin', path('plain'), 200, UPDATED, body={'description': '  Spazi  '}, headers=ADMIN_OWNER,
    effect=saved('admin', 'plain', {'description': 'Spazi'}, session='admin_owner'))
add('enable-plain-trigger-schedules-nothing', 'owner', path('plain'), 200, UPDATED, body={'enabled': True},
    effect=saved('owner', 'plain', {'enabled': True}))
add('enabled-again-no-trigger', 'owner', path('plain'), 200, UPDATED, body={'enabled': 'true', 'name': 'Ancora'},
    effect=saved('owner', 'plain', {'enabled': True, 'name': 'Ancora'}))
add('disable-enabled', 'full_collaborator', path('enabled'), 200, UPDATED, body={'enabled': False},
    effect=saved('full_collaborator', 'enabled', {'enabled': False}))
add('enable-cron-past-resets-triggered', 'owner', path('cron_past'), 200, UPDATED, body={'enabled': True},
    effect=saved('owner', 'cron_past', {'enabled': True}, reset=True))
add('enable-cron-past-untriggered', 'owner', path('cron_past_untriggered'), 200, UPDATED, body={'enabled': 1},
    effect=saved('owner', 'cron_past_untriggered', {'enabled': True}))
NEW_CRON = cron({'time': '2019-05-06T07:08:09', 'triggered': True, 'target': 'approved'}, MESSAGE)
add('enable-with-new-cron-tree', 'owner', path('enabled'), 200, UPDATED, body={'enabled': True, 'automation_tree': NEW_CRON},
    effect=saved('owner', 'enabled', {'enabled': True, 'automation_tree': NEW_CRON}, reset=True))
add('enable-cron-invalid-time-server-error', 'owner', path('cron_bad_time'), 500, SERVER, body={'enabled': True},
    effect=saved('owner', 'cron_bad_time', {'enabled': True}, reset=True))
add('enabled-after-error-no-trigger', 'owner', path('cron_bad_time'), 200, UPDATED, body={'enabled': True},
    effect=saved('owner', 'cron_bad_time', {'enabled': True}))
for label in ('cron_aware', 'cron_no_data', 'cron_string_data', 'cron_time_number', 'cron_bad_date', 'empty_tree', 'null_node_pair',
              'missing_value', 'string_node'):
    add(f'enable-{label.replace("_", "-")}-server-error', 'owner', path(label), 500, SERVER, body={'enabled': True},
        effect=saved('owner', label, {'enabled': True}))
add('enable-null-node-single', 'owner', path('null_node_single'), 200, UPDATED, body={'enabled': True},
    effect=saved('owner', 'null_node_single', {'enabled': True}))
add('restricted-after-updates-unmapped', 'restricted_collaborator', path('enabled'), 403, UNMAPPED('enabled'), body={})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Workflow update authored sequence with audit effects', 'ids': IDS,
                              'workflows': WORKFLOWS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
