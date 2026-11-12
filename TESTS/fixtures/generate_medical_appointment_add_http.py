"""Author the subscription/<uid>/medical-appointments/add HTTP sequence (medical-appointment-add.md).

subscription_medical_appointments_add (BE/application/views/subscriptions_views.py),
@api_view(['POST']) with IsAuthenticated, registry association.members.update:

1. is_valid_uuid(uid): 400 ["badly formed hexadecimal UUID string"].
2. scoped_queryset(Subscription.objects) (group-aware: a foreign X-Group-Id
   hides grouped rows; deleted rows are included) filtered by the id and
   request.user.sport_association (athletes: 403; a missing association: 500).
   None: 404 {'data': 'no subscription found.'}.
3. data['subscription'] = subscription.subscription_id: a list body raises and a
   QueryDict (form) is immutable: 500.
4. MedicalAppointmentsSerializer (ModelSerializer, fields '__all__'): region
   CharField(100) required; date DateField (DATE_INPUT_FORMATS %d/%m/%Y,
   nullable, model default timezone.now); notes TextField (nullable, not
   blank); sport and address CharField(255) nullable and blank; meta JSONField
   (nullable, default {}); creation_date DateTimeField (ISO 8601, default now).
   Errors: 400 {field: [messages]}. Unknown and read-only keys are ignored.
5. save(): the row and its auditlog create of the in-memory instance (a
   defaulted date is str() of the aware datetime), indexed through
   subscription.sport_association.
6. print_document_medical_appointment(id, Authorization) called directly:
   CoordinatedTask.__call__ takes the restore operation lock and, called
   directly, skips the completed-restore check; the body GETs
   CURRENT_HOST/document/medical-appointment/<id> with the raw Authorization
   header and swallows every error.
7. 200 {'data': 'medical appointment created.'}.
Run with ``python3 -I`` after the message-mutations fixture.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('medical_appointment_add_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{983001 + n:012d}' for n in range(50))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000983999'


def subscription(association=OWN, **fields):
    row = {'id': next(ids), 'association': association, 'user': 'owner' if association == OWN else 'foreign_owner', 'status': 2,
           'type': 2, 'role': 1, 'start': '2026-01-01', 'end': '2026-12-31', 'number': '0', 'group': None, 'deleted': False}
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    'own': subscription(),
    'deleted': subscription(deleted=True),
    'grouped': subscription(group='own_group'),
    'foreign': subscription(FOREIGN),
}
OWN_SUB = SUBSCRIPTIONS['own']['id']
cases = []


def path(label_or_id):
    value = SUBSCRIPTIONS[label_or_id]['id'] if label_or_id in SUBSCRIPTIONS else label_or_id
    return f'/subscription/{value}/medical-appointments/add'


def add(name, actor, status, response, *, target='own', method='POST', body=None, raw_body=None, content_type=None, headers=None,
        effect=None, prints=None, restore=None):
    case = {'name': f'medical-appointment-add-{name}', 'actor': actor, 'method': method, 'path': path(target), 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect, 'prints': prints or [], 'restore': restore}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


CREATED = {'data': 'medical appointment created.'}
OK_REPLY = [{'reply': {'status': 200, 'body': 'ok'}}]


DOC = 'a1000000-0000-4000-8000-000000984001'
DOC_MISSING = 'a1000000-0000-4000-8000-000000984002'
TAKEN = 'a1000000-0000-4000-8000-000000984003'
FRESH = 'a1000000-0000-4000-8000-000000984004'
DOCUMENTS = [{'id': DOC, 'filename': 'certificate.pdf'}]
EXISTING = [{'id': TAKEN, 'region': 'agonistica'}]


def created(actor, *, target='own', session=None, region, date='$default-date', notes=None, sport=None, address=None,
            meta='$default-meta', creation='$default-now', document=None, ident='$id'):
    """The appointment row (stored values) and its create audit (in-memory values)."""
    row = {'id': ident, 'subscription': SUBSCRIPTIONS[target]['id'], 'region': region, 'document': document, 'notes': notes,
           'sport': sport, 'address': address}
    changes = {'medical_appointments_id': ['None', ident], 'subscription': ['None', SUBSCRIPTIONS[target]['id']],
               'region': ['None', region], 'document': ['None', document or 'None']}
    if date == '$default-date':
        row['date'], changes['date'] = '$default-date', ['None', '$default-date']
    else:
        row['date'] = date[1] if date else None
        if date:
            changes['date'] = ['None', date[1]]
    if meta == '$default-meta':
        row['meta'], changes['meta'] = {}, ['None', '{}']
    else:
        row['meta'], changes['meta'] = meta[0], ['None', meta[1]]
    for key, value in (('notes', notes), ('sport', sport), ('address', address)):
        if value is not None:
            changes[key] = ['None', value]
    if creation == '$default-now':
        row['creation_date'], changes['creation_date'] = '$now', ['None', '$creation']
    else:
        row['creation_date'], changes['creation_date'] = creation[0], ['None', creation[1]]
    return {'actor': actor, 'session': session, 'association': SUBSCRIPTIONS[target]['association'], 'row': row, 'changes': changes}


def printed(ident='$id'):
    return [{'path': '/document/medical-appointment/' + ident, **OK_REPLY[0]}]


ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
BASIC = {'region': 'non-agonistica', 'date': '20/11/2026'}

# Identity and dispatch.
add('anonymous', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body=BASIC)
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body=BASIC)
add('restricted-collaborator', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.members.update'}, body=BASIC)
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, body=BASIC,
    headers=SCOPED)
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 200, {'name': 'Subscription Medical Appointments Add', 'description': '', 'renders': ['application/json'],
                              'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('malformed-uuid', 'owner', 400, ['badly formed hexadecimal UUID string'], target='not-a-uuid', body=BASIC)
add('athlete-not-association', 'athlete', 403, {'detail': 'User is not a sport association'}, body=BASIC)
add('owner-without-association-server-error', 'owner_without_association', 500, SERVER, body=BASIC)
add('deleted-association-server-error', 'deleted_association_owner', 500, SERVER, body=BASIC)

# Lookup.
add('unknown-subscription', 'owner', 404, {'data': 'no subscription found.'}, target=UNKNOWN, body=BASIC)
add('foreign-subscription', 'owner', 404, {'data': 'no subscription found.'}, target='foreign', body=BASIC)
add('grouped-subscription-other-group', 'owner', 404, {'data': 'no subscription found.'}, target='grouped', body=BASIC,
    headers={'X-Group-Id': IDS['foreign_group']})
add('unknown-subscription-list-body', 'owner', 404, {'data': 'no subscription found.'}, target=UNKNOWN, body=[1])

# The view assigns data['subscription'] before validating.
add('list-body-server-error', 'owner', 500, SERVER, body=[BASIC])
add('form-body-server-error', 'owner', 500, SERVER, raw_body='region=agonistica&date=20%2F11%2F2026',
    content_type='application/x-www-form-urlencoded')

# ModelSerializer validation: nothing is written, nothing is printed.
add('empty-object', 'owner', 400, {'region': ['This field is required.']}, body={})
add('null-region', 'owner', 400, {'region': ['This field may not be null.']}, body={'region': None})
add('blank-region', 'owner', 400, {'region': ['This field may not be blank.']}, body={'region': '  '})
add('long-region', 'owner', 400, {'region': ['Ensure this field has no more than 100 characters.']}, body={'region': 'r' * 101})
add('boolean-region', 'owner', 400, {'region': ['Not a valid string.']}, body={'region': True})
add('iso-date', 'owner', 400, {'date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']},
    body={'region': 'agonistica', 'date': '2026-11-20'})
add('integer-date', 'owner', 400, {'date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']},
    body={'region': 'agonistica', 'date': 20112026})
add('blank-notes-long-sport', 'owner', 400, {'notes': ['This field may not be blank.'],
                                             'sport': ['Ensure this field has no more than 255 characters.']},
    body={'region': 'agonistica', 'notes': '', 'sport': 's' * 256})
add('bad-creation-date', 'owner', 400, {'creation_date': [
    'Datetime has wrong format. Use one of these formats instead: YYYY-MM-DDThh:mm[:ss[.uuuuuu]][+HH:MM|-HH:MM|Z].']},
    body={'region': 'agonistica', 'creation_date': 'ieri'})
add('null-creation-date-dict-address', 'owner', 400, {'creation_date': ['This field may not be null.'], 'address': ['Not a valid string.']},
    body={'region': 'agonistica', 'creation_date': None, 'address': {'via': 'Roma'}})
add('several-errors', 'owner', 400, {'region': ['This field is required.'],
                                     'date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']},
    body={'date': '31/02/2026', 'notes': 'x'})

# Creations, each followed by the direct print call.
add('frontend-agonistica', 'owner', 200, CREATED,
    body={'region': 'agonistica', 'date': '20/11/2026', 'sport': 'Calcio', 'meta': {'ecg': True, 'spirometria': True},
          'notes': ' Portare il documento '},
    effect=created('owner', region='agonistica', date=('20/11/2026', '2026-11-20'), sport='Calcio', notes='Portare il documento',
                   meta=({'ecg': True, 'spirometria': True}, '{"ecg": true, "spirometria": true}')), prints=printed())
add('minimal-defaults', 'owner', 200, CREATED, body={'region': 'non-agonistica'},
    effect=created('owner', region='non-agonistica'), prints=printed())
add('piemonte-biella-creation-date', 'owner', 200, CREATED,
    body={'region': 'agonistica-piemonte-biella', 'date': None, 'creation_date': '2026-11-20T09:30', 'address': 'Via Roma 1',
          'meta': None, 'sport': ''},
    effect=created('owner', region='agonistica-piemonte-biella', date=None, address='Via Roma 1', sport='',
                   meta=(None, 'null'), creation=('2026-11-20T09:30:00+00:00', '2026-11-20 09:30:00')), prints=printed())
add('full-collaborator', 'full_collaborator', 200, CREATED, body={**BASIC, 'notes': 7, 'meta': [1, 'a', None]},
    effect=created('full_collaborator', region='non-agonistica', date=('20/11/2026', '2026-11-20'), notes='7',
                   meta=([1, 'a', None], '[1, "a", null]')), prints=printed())
add('admin-impersonating', 'admin', 200, CREATED, body=BASIC, headers=ADMIN_OWNER,
    effect=created('admin', session='admin_owner', region='non-agonistica', date=('20/11/2026', '2026-11-20')), prints=printed())
add('deleted-subscription', 'owner', 200, CREATED, target='deleted', body=BASIC,
    effect=created('owner', target='deleted', region='non-agonistica', date=('20/11/2026', '2026-11-20')), prints=printed())
add('grouped-subscription-own-group', 'owner', 200, CREATED, target='grouped', body=BASIC, headers={'X-Group-Id': IDS['own_group']},
    effect=created('owner', target='grouped', region='non-agonistica', date=('20/11/2026', '2026-11-20')), prints=printed())
add('ignored-keys', 'owner', 200, CREATED,
    body={**BASIC, 'subscription': SUBSCRIPTIONS['foreign']['id'], 'appointment_region': 'x', 'document_token': 'x', 'document': None,
          'altro': 1},
    effect=created('owner', region='non-agonistica', date=('20/11/2026', '2026-11-20')), prints=printed())
add('print-failure-ignored', 'owner', 200, CREATED, body={**BASIC, 'region': 'agonistica-toscana'},
    effect=created('owner', region='agonistica-toscana', date=('20/11/2026', '2026-11-20')),
    prints=[{'path': '/document/medical-appointment/$id', 'reply': {'status': 500, 'body': 'renderer down'}}])
add('completed-restore-still-prints', 'owner', 200, CREATED, body=BASIC,
    restore={'state': 'completed', 'completed_at': '2026-01-01T00:00:00Z'},
    effect=created('owner', region='non-agonistica', date=('20/11/2026', '2026-11-20')), prints=printed())

# Document and primary key fields.
DATE = ('20/11/2026', '2026-11-20')
add('document-set', 'owner', 200, CREATED, body={**BASIC, 'document': DOC},
    effect=created('owner', region='non-agonistica', date=DATE, document=DOC), prints=printed())
add('document-blank-is-none', 'owner', 200, CREATED, body={**BASIC, 'document': ''},
    effect=created('owner', region='non-agonistica', date=DATE), prints=printed())
add('document-missing', 'owner', 400, {'document': [f'Invalid pk "{DOC_MISSING}" - object does not exist.']},
    body={**BASIC, 'document': DOC_MISSING})
add('document-malformed', 'owner', 400, {'document': ['\u201cnope\u201d is not a valid UUID.']}, body={**BASIC, 'document': 'nope'})
add('document-bool', 'owner', 400, {'document': ['Incorrect type. Expected pk value, received bool.']}, body={**BASIC, 'document': True})
add('document-list', 'owner', 400, {'document': ['\u201c[1]\u201d is not a valid UUID.']}, body={**BASIC, 'document': [1]})
# medical_appointments_id is read-only: whatever key the body carries (new, taken, null, malformed, a number) is ignored
# and the appointment gets a new one.
for label, value in (('supplied', FRESH.upper()), ('taken', TAKEN), ('null', None), ('malformed', 'abc'), ('float', 1.5),
                     ('integer', 12345)):
    add(f'id-{label}-ignored', 'owner', 200, CREATED, body={**BASIC, 'medical_appointments_id': value},
        effect=created('owner', region='non-agonistica', date=DATE), prints=printed())
add('id-ignored-document-error', 'owner', 400, {'document': [f'Invalid pk "{DOC_MISSING}" - object does not exist.']},
    body={**BASIC, 'medical_appointments_id': TAKEN, 'document': DOC_MISSING})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'subscription/<uid>/medical-appointments/add', 'ids': IDS,
                              'subscriptions': SUBSCRIPTIONS, 'documents': DOCUMENTS, 'existing': EXISTING, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
