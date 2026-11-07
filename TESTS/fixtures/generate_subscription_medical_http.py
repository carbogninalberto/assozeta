"""Author the subscription/<uid> medical appointments and medical certificate HTTP sequence (subscription-medical.md).

medical-appointments/list and /<id>/delete validate the path ids with
``is_valid_uuid`` and filter with ``request.user.sport_association`` (athletes:
PermissionDenied 'User is not a sport association'; an owner without an
association: 500). list reads the group-aware Subscription manager (deleted
subscriptions included), renders the appointments ordered by ``-date``
(NULLs first) as ``{'data': {idx: MedicalAppointmentsSerializer}}`` and
``appointment_region`` is ``REGIONS[region]`` (an unknown region is a 500).
A row without a document would be printed synchronously
(``print_document_medical_appointment``): the Go port refuses it, so the
sequence only lists appointments that have their document. delete filters
MedicalAppointments through the subscription join (no manager filters) and
answers ``{'data': 'medical appointment created.'}``.

medical-certificate/edit and set-certificate-expiration resolve the target
with ``_medical_certificate_targets``: the impersonated association, the
athlete's own subscriptions, or the owner's active associations (drafts too
for set-certificate-expiration). A certificate also referenced by another
association's subscription or draft is copied first (and, during an
association-scoped impersonation, ``isolate_medical_certificate`` copies one
shared with another association). edit reads ``request.data`` first, stores
``notes`` as the TextField's str() and a boolean
``competitive_medical_certificate``. set-certificate-expiration reads
``request.data['certificate_expiring_date']`` after the lookup: a date
(``%d/%m/%Y``) is stored and echoed as str() of the aware datetime; null
detaches the certificate (``Subscription.save()`` fills missing dates) and,
outside an association-scoped impersonation, deletes it and its document
when nothing else references the document. A draft gets its certificate
created or linked by ``medical_id`` and the uploader. A subscription without
a certificate (placeholder image upload) is not ported and not authored.
Requests are not atomic. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_medical_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
ids = iter(f'a1000000-0000-4000-8000-{593001 + n:012d}' for n in range(200))
UNKNOWN = 'a1000000-0000-4000-8000-000000599998'
SERVER = '<stable-server-error-page>'


def document(label, created='2026-02-01T08:00:00Z'):
    return {'id': next(ids), 'token': next(ids), 'filename': f'{label}.pdf', 'created': created}


DOCUMENTS = {label: document(label) for label in ('a1', 'a2', 'a3', 'bad', 'abroad', 'deleted', 'grouped', 'del1', 'del2', 'del3',
                                                  'cert_remove', 'cert_shared', 'cert_copy')}


def certificate(user, expiration, *, notes=None, competitive=False, doc=None, created='2026-01-10T09:00:00Z'):
    return {'id': next(ids), 'user': user, 'expiration': expiration, 'notes': notes, 'competitive': competitive, 'document': doc,
            'created': created}


CERTIFICATES = {
    'main': certificate('owner', '2026-06-30', notes='Visita annuale'),
    'shared': certificate('owner', '2026-07-31', notes='Condiviso', competitive=True, doc='cert_copy'),
    'drafted': certificate('owner', '2026-08-31'),
    'athlete': certificate('athlete', '2026-05-31'),
    'deleted': certificate('owner', '2026-04-30'),
    'grouped': certificate('owner', '2026-03-31'),
    'abroad': certificate('foreign_owner', '2026-09-30'),
    'scoped': certificate('owner', '2026-10-31', notes='Isolato'),
    'expire': certificate('owner', '2026-11-30'),
    'remove': certificate('owner', '2026-12-31', notes='Da rimuovere', doc='cert_remove'),
    'keepdoc': certificate('owner', '2026-12-30', doc='cert_shared'),
    'twice': certificate('owner', '2026-12-29'),
    'null_shared': certificate('owner', '2026-12-28', notes='Copia', doc='cert_copy'),
    'scoped_null': certificate('owner', '2026-12-27'),
    'owned': certificate('owner', '2026-02-28'),
    'athlete_owned': certificate('athlete', '2026-02-27'),
    'owned_shared': certificate('owner', '2026-02-26', notes='Bozza'),
    'copy_fail': certificate('owner', '2026-02-25'),
}


def subscription(association, *, user='owner', medical=None, deleted=False, group=None, start='2026-01-01', end='2026-12-31',
                 created='2026-02-01T09:00:00Z'):
    return {'id': next(ids), 'association': association, 'user': user, 'medical': medical, 'deleted': deleted, 'group': group,
            'start': start, 'end': end, 'created': created}


SUBSCRIPTIONS = {
    'main': subscription(OWN, medical='main'),
    'empty': subscription(OWN),
    'badregion': subscription(OWN),
    'abroad': subscription(FOREIGN, user='foreign_owner', medical='abroad'),
    'deleted': subscription(OWN, medical='deleted', deleted=True),
    'grouped': subscription(OWN, medical='grouped', group='own_group'),
    'other': subscription(OWN),
    'del': subscription(OWN),
    'shared': subscription(OWN, medical='shared'),
    'abroad_shared': subscription(FOREIGN, user='foreign_owner', medical='shared'),
    'drafted': subscription(OWN, medical='drafted'),
    'athlete': subscription(OWN, user='athlete', medical='athlete'),
    'scoped': subscription(OWN, medical='scoped'),
    'abroad_scoped': subscription(FOREIGN, user='foreign_owner', medical='scoped'),
    'expire': subscription(OWN, medical='expire'),
    'remove': subscription(OWN, medical='remove', start=None, end=None, created='2026-03-15T10:00:00Z'),
    'keepdoc': subscription(OWN, medical='keepdoc'),
    'twice_a': subscription(OWN, medical='twice'),
    'twice_b': subscription(OWN, medical='twice'),
    'null_copy': subscription(OWN, medical='null_shared'),
    'abroad_null': subscription(FOREIGN, user='foreign_owner', medical='null_shared'),
    'scoped_null': subscription(OWN, medical='scoped_null'),
    'abroad_drafted': subscription(FOREIGN, user='foreign_owner', medical='owned_shared'),
    'copy_fail': subscription(OWN, medical='copy_fail'),
    'abroad_copy_fail': subscription(FOREIGN, user='foreign_owner', medical='copy_fail'),
}


def appointment(sub, region, doc, date, *, notes=None, sport=None, meta=None, address=None, created='2026-03-01T08:00:00Z'):
    return {'id': next(ids), 'subscription': sub, 'region': region, 'document': doc, 'date': date, 'notes': notes, 'sport': sport,
            'meta': meta, 'address': address, 'created': created}


APPOINTMENTS = {
    'a1': appointment('main', 'agonistica', 'a1', '2026-03-10', notes='Prima visita', sport='Calcio', meta={'k': 1, 'b': [True, None]},
                      address='Via Roma 1'),
    'a2': appointment('main', 'non-agonistica-sicilia', 'a2', '2026-05-01', created='2026-04-01T08:30:00.123456Z'),
    'a3': appointment('main', 'agonistica-piemonte-biella', 'a3', None, meta={}, notes='', sport='Nuoto è'),
    'bad': appointment('badregion', 'sconosciuta', 'bad', '2026-01-05'),
    'abroad': appointment('abroad', 'agonistica', 'abroad', '2026-02-05'),
    'deleted': appointment('deleted', 'agonistica-toscana', 'deleted', '2026-02-06'),
    'grouped': appointment('grouped', 'non-agonistica', 'grouped', '2026-02-07'),
    'keepdoc': appointment('other', 'agonistica', 'cert_shared', '2026-02-08'),
    'del1': appointment('del', 'agonistica-lombardia', 'del1', '2026-02-09', meta={'x': 'y'}, address='Via Po 2'),
    'del2': appointment('del', 'agonistica-sicilia', 'del2', '2026-02-10'),
    'del3': appointment('del', 'non-agonistica-emilia-romagna', 'del3', None),
}


def draft(association, data, valid=False):
    return {'id': next(ids), 'association': association, 'data': data, 'valid': valid}


cid = lambda label: CERTIFICATES[label]['id']
medical = lambda mid, filename=None, expiring=None: {'medical_id': mid, 'filename': filename, 'certificate_expring_date': expiring}
DRAFTS = {
    'none': draft(OWN, {'associate': {'first_name': 'Ada'}}),
    'linked': draft(OWN, {'associate': {'first_name': 'Bea'}, 'medical_certificate': medical(cid('owned'), 'c.pdf', '28/02/2026')}, True),
    'other_user': draft(OWN, {'medical_certificate': medical(cid('athlete_owned'))}),
    'draft_shared': draft(OWN, {'medical_certificate': medical(cid('owned_shared'), 'b.pdf')}),
    'no_id': draft(OWN, {'medical_certificate': {'filename': 'x.pdf'}}),
    'bad_id': draft(OWN, {'medical_certificate': medical('abc')}),
    'list': draft(OWN, [1, 2]),
    'mc_none': draft(OWN, {'medical_certificate': None}),
    'bad_date': draft(OWN, {}),
    'abroad': draft(FOREIGN, {'medical_certificate': medical(cid('drafted'))}),
}
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'subscription-medical-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


ref = lambda key: SUBSCRIPTIONS[key]['id'] if key in SUBSCRIPTIONS else DRAFTS[key]['id'] if key in DRAFTS else key
aref = lambda key: APPOINTMENTS[key]['id'] if key in APPOINTMENTS else key
LIST = lambda sub: f'/subscription/{ref(sub)}/medical-appointments/list'
DELETE = lambda sub, app: f'/subscription/{ref(sub)}/medical-appointments/{aref(app)}/delete'
EDIT = lambda sub: f'/subscription/{ref(sub)}/medical-certificate/edit'
EXPIRE = lambda sub: f'/subscription/{ref(sub)}/medical-certificate/set-certificate-expiration'
BAD_UUID = ['badly formed hexadecimal UUID string']
ATHLETE = {'detail': 'User is not a sport association'}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
SCOPED_RESOURCE = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
MEMBERS_READ, MEMBERS_UPDATE = ({'detail': f'Missing permission: association.members.{k}'} for k in ('read', 'update'))
JSON_ERROR = {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'}
options = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
FORM = 'application/x-www-form-urlencoded'
REGIONS = {'agonistica': 'Visita Agonistica', 'non-agonistica-sicilia': 'Visita Non Agonistica Sicilia',
           'agonistica-piemonte-biella': 'Visita Agonistica Piemonte Biella', 'agonistica-toscana': 'Visita Agonistica Toscana',
           'non-agonistica': 'Visita Non Agonistica'}


def rendered(label):
    row = APPOINTMENTS[label]
    doc = DOCUMENTS[row['document']]
    return {'medical_appointments_id': row['id'], 'document_token': doc['token'], 'appointment_region': REGIONS[row['region']],
            'region': row['region'], 'date': row['date'], 'notes': row['notes'], 'sport': row['sport'], 'meta': row['meta'],
            'address': row['address'], 'creation_date': row['created'], 'subscription': SUBSCRIPTIONS[row['subscription']]['id'],
            'document': doc['id']}


listed = lambda *labels: {'data': {str(i): rendered(label) for i, label in enumerate(labels)}}
NO_SUB = {'data': 'no subscription found.'}

# ---------------------------------------------------------------- list
add('list-owner', 'owner', LIST('main'), 200, listed('a3', 'a2', 'a1'), method='GET')
add('list-full-collaborator', 'full_collaborator', LIST('main'), 200, listed('a3', 'a2', 'a1'), method='GET')
add('list-admin-impersonating-owner', 'admin', LIST('main'), 200, listed('a3', 'a2', 'a1'), method='GET', headers=ADMIN_OWNER)
add('list-owner-full-session', 'owner', LIST('main'), 200, listed('a3', 'a2', 'a1'), method='GET', headers=OWNER_FULL)
add('list-empty', 'owner', LIST('empty'), 200, {'data': {}}, method='GET')
add('list-deleted-subscription', 'owner', LIST('deleted'), 200, listed('deleted'), method='GET')
add('list-grouped-own-group', 'owner', LIST('grouped'), 200, listed('grouped'), method='GET', headers={'X-Group-Id': IDS['own_group']})
add('list-grouped-foreign-group-hides', 'owner', LIST('grouped'), 404, NO_SUB, method='GET', headers={'X-Group-Id': IDS['foreign_group']})
add('list-grouped-malformed-group-ignored', 'owner', LIST('grouped'), 200, listed('grouped'), method='GET', headers={'X-Group-Id': 'abc'})
add('list-foreign-subscription', 'owner', LIST('abroad'), 404, NO_SUB, method='GET')
add('list-foreign-owner', 'foreign_owner', LIST('abroad'), 200, listed('abroad'), method='GET')
add('list-unknown', 'owner', LIST(UNKNOWN), 404, NO_SUB, method='GET')
add('list-unknown-region-server-error', 'owner', LIST('badregion'), 500, SERVER, method='GET')
add('list-malformed-uid', 'owner', LIST('abc'), 400, BAD_UUID, method='GET')
add('list-braced-uid', 'owner', LIST('{' + ref('main') + '}'), 200, listed('a3', 'a2', 'a1'), method='GET')
add('list-malformed-uid-before-athlete', 'athlete', LIST('abc'), 400, BAD_UUID, method='GET')
add('list-athlete-denied', 'athlete', LIST('athlete'), 403, ATHLETE, method='GET')
add('list-owner-without-association-server-error', 'owner_without_association', LIST('main'), 500, SERVER, method='GET')
add('list-deleted-association-owner-server-error', 'deleted_association_owner', LIST('main'), 500, SERVER, method='GET')
add('list-owner-scoped-athlete-denied', 'owner', LIST('main'), 403, SCOPED_DENIED, method='GET', headers=SCOPED)
add('list-restricted-missing-permission', 'restricted_collaborator', LIST('main'), 403, MEMBERS_READ, method='GET')
add('list-admin-without-session', 'admin', LIST('main'), 403, ADMIN, method='GET')
add('list-anonymous', 'anonymous', LIST('main'), 401, ANONYMOUS, method='GET')
add('list-wrong-method', 'owner', LIST('main'), 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('list-restricted-before-method', 'restricted_collaborator', LIST('main'), 403, MEMBERS_READ, body={})
add('list-options', 'owner', LIST('main'), 200, options('Subscription Medical Appointments List'), method='OPTIONS')

# ---------------------------------------------------------------- delete
DELETED_MSG = {'data': 'medical appointment created.'}
NO_APP = {'data': 'no medical appointment found.'}
deleted = lambda actor, *labels, index=True: {'kind': 'delete', 'actor': actor, 'appointments': list(labels), 'index': index}
add('delete-malformed-uid', 'owner', DELETE('abc', 'del1'), 400, BAD_UUID, method='DELETE')
add('delete-malformed-appointment', 'owner', DELETE('del', 'abc'), 400, BAD_UUID, method='DELETE')
add('delete-unknown', 'owner', DELETE('del', UNKNOWN), 404, NO_APP, method='DELETE')
add('delete-other-subscription-path', 'owner', DELETE('main', 'del1'), 404, NO_APP, method='DELETE')
add('delete-foreign', 'owner', DELETE('abroad', 'abroad'), 404, NO_APP, method='DELETE')
add('delete-athlete-denied', 'athlete', DELETE('del', 'del1'), 403, ATHLETE, method='DELETE')
add('delete-owner-without-association-server-error', 'owner_without_association', DELETE('del', 'del1'), 500, SERVER, method='DELETE')
add('delete-owner-scoped-athlete-denied', 'owner', DELETE('del', 'del1'), 403, SCOPED_DENIED, method='DELETE', headers=SCOPED)
add('delete-restricted-missing-permission', 'restricted_collaborator', DELETE('del', 'del1'), 403, MEMBERS_UPDATE, method='DELETE')
add('delete-admin-without-session', 'admin', DELETE('del', 'del1'), 403, ADMIN, method='DELETE')
add('delete-anonymous', 'anonymous', DELETE('del', 'del1'), 401, ANONYMOUS, method='DELETE')
add('delete-wrong-method', 'owner', DELETE('del', 'del1'), 405, {'detail': 'Method "POST" not allowed.'}, body={})
add('delete-options', 'owner', DELETE('del', 'del1'), 200, options('Subscription Medical Appointments Delete'), method='OPTIONS')
add('delete-owner', 'owner', DELETE('del', 'del1'), 200, DELETED_MSG, method='DELETE', effect=deleted('owner', 'del1'))
add('delete-again', 'owner', DELETE('del', 'del1'), 404, NO_APP, method='DELETE')
add('delete-full-collaborator', 'full_collaborator', DELETE('del', 'del2'), 200, DELETED_MSG, method='DELETE',
    effect=deleted('full_collaborator', 'del2', index=False))
add('delete-admin-impersonating-owner', 'admin', DELETE('del', 'del3'), 200, DELETED_MSG, method='DELETE', headers=ADMIN_OWNER,
    effect=deleted('admin', 'del3'))
add('delete-deleted-subscription', 'owner', DELETE('deleted', 'deleted'), 200, DELETED_MSG, method='DELETE',
    effect=deleted('owner', 'deleted'))
add('delete-grouped-ignores-group-header', 'owner', DELETE('grouped', 'grouped'), 200, DELETED_MSG, method='DELETE',
    headers={'X-Group-Id': IDS['foreign_group']}, effect=deleted('owner', 'grouped'))

# ---------------------------------------------------------------- edit
UPDATED = {'msg': 'Medical certificate updated'}
NOT_FOUND = {'exception': 'Subscription not found'}
NO_CERT = {'exception': 'Medical certificate not found'}


def cert_update(label, changes):
    return {'model': 'MedicalCertificate', 'key': label, 'action': 1, 'changes': changes, 'index': 'actor'}


def sub_update(label, changes):
    return {'model': 'Subscription', 'key': label, 'action': 1, 'changes': changes, 'index': 'own'}


def cert_copy(label):
    return {'model': 'MedicalCertificate', 'key': '$new', 'action': 0, 'changes': '$created', 'index': 'actor', 'copy_of': label}


def effect(actor, audit, *, certificates=None, subscriptions=None, copy=None, created=None, removed=(), documents_removed=(), drafts=None,
           detached=()):
    """certificates: {label: {column: value}} final values of seeded certificates; subscriptions: {label: {column: value}}
    ('$new' is the created certificate); copy: the label a new certificate copies (with overrides); created: a new
    plain certificate {'user', 'expiration'}; removed / documents_removed: deleted rows; detached: subscriptions the
    collector set to NULL; drafts: {label: final data}."""
    return {'kind': 'write', 'actor': actor, 'audit': audit, 'certificates': certificates or {}, 'subscriptions': subscriptions or {},
            'copy': copy, 'created': created, 'removed': list(removed), 'documents_removed': list(documents_removed), 'drafts': drafts or {},
            'detached': list(detached)}


add('edit-malformed-uid', 'owner', EDIT('abc'), 400, BAD_UUID, body={'notes': 'x'})
add('edit-malformed-json-before-lookup', 'owner', EDIT(UNKNOWN), 400, JSON_ERROR, raw_body='{bad', content_type='application/json')
add('edit-unknown', 'owner', EDIT(UNKNOWN), 404, NOT_FOUND, body={'notes': 'x'})
add('edit-foreign', 'owner', EDIT('abroad'), 404, NOT_FOUND, body={'notes': 'x'})
add('edit-draft-uid-not-found', 'owner', EDIT('none'), 404, NOT_FOUND, body={'notes': 'x'})
add('edit-without-certificate', 'owner', EDIT('empty'), 404, NO_CERT, body={'notes': 'x'})
add('edit-without-certificate-null-body', 'owner', EDIT('empty'), 404, NO_CERT, raw_body='null', content_type='application/json')
add('edit-owner-without-association', 'owner_without_association', EDIT('main'), 404, NOT_FOUND, body={'notes': 'x'})
add('edit-deleted-association-owner', 'deleted_association_owner', EDIT('main'), 404, NOT_FOUND, body={'notes': 'x'})
add('edit-athlete-other-user', 'athlete', EDIT('main'), 404, NOT_FOUND, body={'notes': 'x'})
add('edit-grouped-foreign-group-hides', 'owner', EDIT('grouped'), 404, NOT_FOUND, body={'notes': 'x'},
    headers={'X-Group-Id': IDS['foreign_group']})
add('edit-owner-scoped-athlete-denied', 'owner', EDIT('main'), 403, SCOPED_RESOURCE, body={'notes': 'x'}, headers=SCOPED)
add('edit-restricted-missing-permission', 'restricted_collaborator', EDIT('main'), 403, MEMBERS_UPDATE, body={'notes': 'x'})
add('edit-admin-without-session', 'admin', EDIT('main'), 403, ADMIN, body={'notes': 'x'})
add('edit-anonymous', 'anonymous', EDIT('main'), 401, ANONYMOUS, body={'notes': 'x'})
add('edit-wrong-method', 'owner', EDIT('main'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('edit-options', 'owner', EDIT('main'), 200, options('Subscription Medical Certificate Edit'), method='OPTIONS')
add('edit-empty-body-no-change', 'owner', EDIT('main'), 200, UPDATED, body={}, effect=effect('owner', []))
add('edit-notes', 'owner', EDIT('main'), 200, UPDATED, body={'notes': 'Nuove note è'},
    effect=effect('owner', [cert_update('main', {'notes': ['Visita annuale', 'Nuove note è']})],
                  certificates={'main': {'notes': 'Nuove note è'}}))
add('edit-same-values-no-audit', 'owner', EDIT('main'), 200, UPDATED, body={'notes': 'Nuove note è', 'competitive_medical_certificate': False},
    effect=effect('owner', []))
add('edit-competitive', 'owner', EDIT('main'), 200, UPDATED, body={'competitive_medical_certificate': True, 'ignored': 1},
    effect=effect('owner', [cert_update('main', {'competitive_medical_certificate': ['False', 'True']})],
                  certificates={'main': {'notes': 'Nuove note è', 'competitive_medical_certificate': True}}))
add('edit-competitive-non-boolean-ignored', 'owner', EDIT('main'), 200, UPDATED, body={'competitive_medical_certificate': 0},
    effect=effect('owner', []))
add('edit-notes-number', 'full_collaborator', EDIT('main'), 200, UPDATED, body={'notes': 12.50, 'competitive_medical_certificate': False},
    effect=effect('full_collaborator', [cert_update('main', {'notes': ['Nuove note è', '12.5'], 'competitive_medical_certificate': ['True', 'False']})],
                  certificates={'main': {'notes': '12.5', 'competitive_medical_certificate': False}}))
add('edit-notes-dict', 'owner', EDIT('main'), 200, UPDATED, body={'notes': {'a': [1, None, True], 'b': 'è'}},
    effect=effect('owner', [cert_update('main', {'notes': ['12.5', "{'a': [1, None, True], 'b': 'è'}"]})],
                  certificates={'main': {'notes': "{'a': [1, None, True], 'b': 'è'}"}}))
add('edit-notes-null', 'owner', EDIT('main'), 200, UPDATED, body={'notes': None},
    effect=effect('owner', [cert_update('main', {'notes': ["{'a': [1, None, True], 'b': 'è'}", 'None']})],
                  certificates={'main': {'notes': None}}))
add('edit-form', 'owner', EDIT('main'), 200, UPDATED, raw_body='notes=Primo&notes=Modulo&competitive_medical_certificate=true',
    content_type=FORM, effect=effect('owner', [cert_update('main', {'notes': ['None', 'Modulo']})], certificates={'main': {'notes': 'Modulo'}}))
add('edit-list-body-without-keys', 'owner', EDIT('main'), 200, UPDATED, body=['x', 1], effect=effect('owner', []))
add('edit-list-body-with-notes-server-error', 'owner', EDIT('main'), 500, SERVER, body=['notes'])
add('edit-string-body-server-error', 'owner', EDIT('main'), 500, SERVER, raw_body='"my notes"', content_type='application/json')
add('edit-string-body-without-keys', 'owner', EDIT('main'), 200, UPDATED, raw_body='"x"', content_type='application/json',
    effect=effect('owner', []))
add('edit-null-body-server-error', 'owner', EDIT('main'), 500, SERVER, raw_body='null', content_type='application/json')
add('edit-number-body-server-error', 'owner', EDIT('main'), 500, SERVER, raw_body='3', content_type='application/json')
add('edit-nul-notes-server-error', 'owner', EDIT('main'), 500, SERVER, body={'notes': 'a\x00b'})
add('edit-admin-impersonating-owner', 'admin', EDIT('main'), 200, UPDATED, body={'notes': 'Admin'}, headers=ADMIN_OWNER,
    effect=effect('admin', [cert_update('main', {'notes': ['Modulo', 'Admin']})], certificates={'main': {'notes': 'Admin'}}))
add('edit-athlete-own-subscription', 'athlete', EDIT('athlete'), 200, UPDATED, body={'notes': 'Atleta', 'competitive_medical_certificate': True},
    effect=effect('athlete', [cert_update('athlete', {'competitive_medical_certificate': ['False', 'True'], 'notes': ['None', 'Atleta']})],
                  certificates={'athlete': {'notes': 'Atleta', 'competitive_medical_certificate': True}}))
add('edit-deleted-subscription', 'owner', EDIT('deleted'), 200, UPDATED, body={'notes': 'Eliminata'},
    effect=effect('owner', [cert_update('deleted', {'notes': ['None', 'Eliminata']})], certificates={'deleted': {'notes': 'Eliminata'}}))
add('edit-grouped-own-group', 'owner', EDIT('grouped'), 200, UPDATED, body={'notes': 'Gruppo'}, headers={'X-Group-Id': IDS['own_group']},
    effect=effect('owner', [cert_update('grouped', {'notes': ['None', 'Gruppo']})], certificates={'grouped': {'notes': 'Gruppo'}}))
# A certificate another association's subscription also uses is copied before the edit.
add('edit-shared-certificate-copied', 'owner', EDIT('shared'), 200, UPDATED, body={'notes': 'Solo mio'},
    effect=effect('owner', [cert_copy('shared'), sub_update('shared', {'medical': [cid('shared'), '$new']}),
                            cert_update('$new', {'notes': ['Condiviso', 'Solo mio']})],
                  copy={'from': 'shared', 'notes': 'Solo mio'}, subscriptions={'shared': {'medical_id': '$new'}}))
add('edit-draft-referenced-certificate-copied', 'full_collaborator', EDIT('drafted'), 200, UPDATED, body={'competitive_medical_certificate': True},
    effect=effect('full_collaborator', [cert_copy('drafted'), sub_update('drafted', {'medical': [cid('drafted'), '$new']}),
                                        cert_update('$new', {'competitive_medical_certificate': ['False', 'True']})],
                  copy={'from': 'drafted', 'competitive_medical_certificate': True}, subscriptions={'drafted': {'medical_id': '$new'}}))
# An association-scoped session isolates the shared certificate first; the copy has no foreign reference.
add('edit-scoped-session-isolates', 'owner', EDIT('scoped'), 200, UPDATED, body={'notes': 'Sessione'}, headers=OWNER_FULL,
    effect=effect('owner', [cert_copy('scoped'), sub_update('scoped', {'medical': [cid('scoped'), '$new']}),
                            cert_update('$new', {'notes': ['Isolato', 'Sessione']})],
                  copy={'from': 'scoped', 'notes': 'Sessione'}, subscriptions={'scoped': {'medical_id': '$new'}}))

# ---------------------------------------------------------------- set-certificate-expiration
moment = lambda day: f'{day} 00:00:00+00:00'
updated_with = lambda day: {'msg': f'Medical certificate update with date {moment(day)}'}
REMOVED = {'msg': 'Medical certificate removed'}


def cert_delete(label):
    return {'model': 'MedicalCertificate', 'key': label, 'action': 2, 'changes': '$deleted', 'index': 'actor'}


def doc_delete(label):
    return {'model': 'Document', 'key': label, 'action': 2, 'changes': '$deleted', 'index': 'actor'}


def draft_update(label, before, after):
    return {'model': 'AssociateImportDraft', 'key': label, 'action': 1, 'changes': {'data': [before, after]}, 'index': 'own'}


def cert_create():
    return {'model': 'MedicalCertificate', 'key': '$new', 'action': 0, 'changes': '$created', 'index': 'actor'}


dumps = lambda value: json.dumps(value, sort_keys=True)
add('expire-malformed-uid', 'owner', EXPIRE('abc'), 400, BAD_UUID, body={'certificate_expiring_date': '01/01/2027'})
add('expire-unknown-before-body', 'owner', EXPIRE(UNKNOWN), 404, NOT_FOUND, raw_body='{bad', content_type='application/json')
add('expire-malformed-json', 'owner', EXPIRE('expire'), 400, JSON_ERROR, raw_body='{bad', content_type='application/json')
add('expire-foreign', 'owner', EXPIRE('abroad'), 404, NOT_FOUND, body={'certificate_expiring_date': '01/01/2027'})
add('expire-foreign-draft', 'owner', EXPIRE(DRAFTS['abroad']['id']), 404, NOT_FOUND, body={'certificate_expiring_date': '01/01/2027'})
add('expire-owner-without-association', 'owner_without_association', EXPIRE('expire'), 404, NOT_FOUND,
    body={'certificate_expiring_date': '01/01/2027'})
add('expire-athlete-other-user', 'athlete', EXPIRE('expire'), 404, NOT_FOUND, body={'certificate_expiring_date': '01/01/2027'})
add('expire-athlete-draft-not-found', 'athlete', EXPIRE('none'), 404, NOT_FOUND, body={'certificate_expiring_date': '01/01/2027'})
add('expire-owner-scoped-athlete-denied', 'owner', EXPIRE('expire'), 403, SCOPED_RESOURCE, body={}, headers=SCOPED)
add('expire-restricted-missing-permission', 'restricted_collaborator', EXPIRE('expire'), 403, MEMBERS_UPDATE, body={})
add('expire-admin-without-session', 'admin', EXPIRE('expire'), 403, ADMIN, body={})
add('expire-anonymous', 'anonymous', EXPIRE('expire'), 401, ANONYMOUS, body={})
add('expire-wrong-method', 'owner', EXPIRE('expire'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('expire-options', 'owner', EXPIRE('expire'), 200, options('Subscription Medical Certificate Set Certificate Expiration'), method='OPTIONS')
add('expire-missing-key-server-error', 'owner', EXPIRE('expire'), 500, SERVER, body={'date': '01/01/2027'})
add('expire-list-body-server-error', 'owner', EXPIRE('expire'), 500, SERVER, body=['certificate_expiring_date'])
add('expire-iso-date-server-error', 'owner', EXPIRE('expire'), 500, SERVER, body={'certificate_expiring_date': '2027-01-01'})
add('expire-impossible-date-server-error', 'owner', EXPIRE('expire'), 500, SERVER, body={'certificate_expiring_date': '31/02/2027'})
add('expire-number-server-error', 'owner', EXPIRE('expire'), 500, SERVER, body={'certificate_expiring_date': 1})
add('expire-empty-string-server-error', 'owner', EXPIRE('expire'), 500, SERVER, body={'certificate_expiring_date': ''})
add('expire-set', 'owner', EXPIRE('expire'), 200, updated_with('2027-09-15'), body={'certificate_expiring_date': '15/09/2027'},
    effect=effect('owner', [cert_update('expire', {'expiration_date': ['2026-11-30', moment('2027-09-15')]})],
                  certificates={'expire': {'expiration_date': '2027-09-15'}}))
add('expire-same-date-still-audited', 'full_collaborator', EXPIRE('expire'), 200, updated_with('2027-09-15'),
    body={'certificate_expiring_date': '15/09/2027'},
    effect=effect('full_collaborator', [cert_update('expire', {'expiration_date': ['2027-09-15', moment('2027-09-15')]})]))
add('expire-short-form', 'owner', EXPIRE('expire'), 200, updated_with('2028-02-01'), raw_body='certificate_expiring_date=1/2/2028',
    content_type=FORM, effect=effect('owner', [cert_update('expire', {'expiration_date': ['2027-09-15', moment('2028-02-01')]})],
                                     certificates={'expire': {'expiration_date': '2028-02-01'}}))
add('expire-admin-impersonating-owner', 'admin', EXPIRE('expire'), 200, updated_with('2028-03-01'), headers=ADMIN_OWNER,
    body={'certificate_expiring_date': '01/03/2028'},
    effect=effect('admin', [cert_update('expire', {'expiration_date': ['2028-02-01', moment('2028-03-01')]})],
                  certificates={'expire': {'expiration_date': '2028-03-01'}}))
add('expire-athlete-own-subscription', 'athlete', EXPIRE('athlete'), 200, updated_with('2027-05-31'),
    body={'certificate_expiring_date': '31/05/2027'},
    effect=effect('athlete', [cert_update('athlete', {'expiration_date': ['2026-05-31', moment('2027-05-31')]})],
                  certificates={'athlete': {'expiration_date': '2027-05-31', 'notes': 'Atleta', 'competitive_medical_certificate': True}}))
# Not atomic: the copy is stored before the date fails to parse.
add('expire-shared-copy-then-bad-date', 'owner', EXPIRE('copy_fail'), 500, SERVER, body={'certificate_expiring_date': 'x'},
    effect=effect('owner', [cert_copy('copy_fail'), sub_update('copy_fail', {'medical': [cid('copy_fail'), '$new']})],
                  copy={'from': 'copy_fail'}, subscriptions={'copy_fail': {'medical_id': '$new'}}))
add('expire-null-removes-certificate-and-document', 'owner', EXPIRE('remove'), 200, REMOVED, body={'certificate_expiring_date': None},
    effect=effect('owner', [sub_update('remove', {'medical': [cid('remove'), 'None'], 'start_date': ['None', '2026-01-01'],
                                                   'end_date': ['None', '2026-12-31']}),
                            cert_delete('remove'), doc_delete('cert_remove')],
                  subscriptions={'remove': {'medical_id': None, 'start_date': '2026-01-01', 'end_date': '2026-12-31'}},
                  removed=['remove'], documents_removed=['cert_remove']))
add('expire-null-keeps-referenced-document', 'full_collaborator', EXPIRE('keepdoc'), 200, REMOVED, body={'certificate_expiring_date': None},
    effect=effect('full_collaborator', [sub_update('keepdoc', {'medical': [cid('keepdoc'), 'None']}), cert_delete('keepdoc')],
                  subscriptions={'keepdoc': {'medical_id': None}}, removed=['keepdoc']))
add('expire-null-detaches-other-subscription', 'owner', EXPIRE('twice_a'), 200, REMOVED, body={'certificate_expiring_date': None},
    effect=effect('owner', [sub_update('twice_a', {'medical': [cid('twice'), 'None']}), cert_delete('twice')],
                  subscriptions={'twice_a': {'medical_id': None}}, removed=['twice'], detached=['twice_b']))
add('expire-null-foreign-shared-copy-removed', 'owner', EXPIRE('null_copy'), 200, REMOVED, body={'certificate_expiring_date': None},
    effect=effect('owner', [cert_copy('null_shared'), sub_update('null_copy', {'medical': [cid('null_shared'), '$new']}),
                            sub_update('null_copy', {'medical': ['$new', 'None']}), cert_delete('$new')],
                  subscriptions={'null_copy': {'medical_id': None}}, copy={'from': 'null_shared', 'removed': True}))
add('expire-null-scoped-session-detaches-only', 'owner', EXPIRE('scoped_null'), 200, REMOVED, body={'certificate_expiring_date': None},
    headers=OWNER_FULL, effect=effect('owner', [sub_update('scoped_null', {'medical': [cid('scoped_null'), 'None']})],
                                      subscriptions={'scoped_null': {'medical_id': None}}))

# drafts
DEFAULT_MC = medical(None)
add('expire-draft-creates-certificate', 'owner', EXPIRE('none'), 200, updated_with('2027-01-31'), body={'certificate_expiring_date': '31/01/2027'},
    effect=effect('owner', [draft_update('none', dumps(DRAFTS['none']['data']), dumps({**DRAFTS['none']['data'], 'medical_certificate': DEFAULT_MC})),
                            cert_create(), {**cert_update('$new', {'expiration_date': ['None', moment('2027-01-31')]})},
                            draft_update('none', dumps({**DRAFTS['none']['data'], 'medical_certificate': DEFAULT_MC}),
                                         dumps({**DRAFTS['none']['data'], 'medical_certificate': medical('$new', None, '31/01/2027')}))],
                  created={'user': 'owner', 'expiration': '2027-01-31'},
                  drafts={'none': {**DRAFTS['none']['data'], 'medical_certificate': medical('$new', None, '31/01/2027')}}))
LINKED_DATA = DRAFTS['linked']['data']
add('expire-draft-linked-certificate', 'full_collaborator', EXPIRE('linked'), 200, updated_with('2027-02-28'),
    body={'certificate_expiring_date': '28/02/2027'},
    effect=effect('full_collaborator', [cert_update('owned', {'expiration_date': ['2026-02-28', moment('2027-02-28')]}),
                                        draft_update('linked', dumps(LINKED_DATA), dumps({**LINKED_DATA, 'medical_certificate': medical(
                                            cid('owned'), 'c.pdf', '28/02/2027')}))],
                  certificates={'owned': {'expiration_date': '2027-02-28'}},
                  drafts={'linked': {**LINKED_DATA, 'medical_certificate': medical(cid('owned'), 'c.pdf', '28/02/2027')}}))
OTHER_DATA = DRAFTS['other_user']['data']
add('expire-draft-other-uploader-new-certificate', 'owner', EXPIRE('other_user'), 200, updated_with('2027-03-31'),
    body={'certificate_expiring_date': '31/03/2027'},
    effect=effect('owner', [cert_create(), cert_update('$new', {'expiration_date': ['None', moment('2027-03-31')]}),
                            draft_update('other_user', dumps(OTHER_DATA), dumps({'medical_certificate': medical('$new', None, '31/03/2027')}))],
                  created={'user': 'owner', 'expiration': '2027-03-31'},
                  drafts={'other_user': {'medical_certificate': medical('$new', None, '31/03/2027')}}))
SHARED_DATA = DRAFTS['draft_shared']['data']
add('expire-draft-shared-certificate-copied', 'owner', EXPIRE('draft_shared'), 200, updated_with('2027-04-30'),
    body={'certificate_expiring_date': '30/04/2027'},
    effect=effect('owner', [cert_copy('owned_shared'), cert_update('$new', {'expiration_date': ['2026-02-26', moment('2027-04-30')]}),
                            draft_update('draft_shared', dumps(SHARED_DATA), dumps({'medical_certificate': medical('$new', 'b.pdf', '30/04/2027')}))],
                  copy={'from': 'owned_shared', 'expiration_date': '2027-04-30'},
                  drafts={'draft_shared': {'medical_certificate': medical('$new', 'b.pdf', '30/04/2027')}}))
add('expire-draft-without-id-key', 'owner', EXPIRE('no_id'), 200, updated_with('2027-05-01'), body={'certificate_expiring_date': '01/05/2027'},
    effect=effect('owner', [cert_create(), cert_update('$new', {'expiration_date': ['None', moment('2027-05-01')]}),
                            draft_update('no_id', dumps(DRAFTS['no_id']['data']),
                                         dumps({'medical_certificate': {'filename': 'x.pdf', 'medical_id': '$new',
                                                                        'certificate_expring_date': '01/05/2027'}}))],
                  created={'user': 'owner', 'expiration': '2027-05-01'},
                  drafts={'no_id': {'medical_certificate': {'filename': 'x.pdf', 'medical_id': '$new', 'certificate_expring_date': '01/05/2027'}}}))
add('expire-draft-malformed-certificate-id-server-error', 'owner', EXPIRE('bad_id'), 500, SERVER, body={'certificate_expiring_date': '01/05/2027'})
add('expire-draft-list-data-server-error', 'owner', EXPIRE('list'), 500, SERVER, body={'certificate_expiring_date': '01/05/2027'})
add('expire-draft-null-certificate-server-error', 'owner', EXPIRE('mc_none'), 500, SERVER, body={'certificate_expiring_date': '01/05/2027'})
add('expire-draft-missing-key-server-error', 'owner', EXPIRE('bad_date'), 500, SERVER, body={})
# Not atomic: the default entry and the new certificate are stored before the date fails.
add('expire-draft-bad-date-keeps-writes', 'owner', EXPIRE('bad_date'), 500, SERVER, body={'certificate_expiring_date': '31/02/2027'},
    effect=effect('owner', [draft_update('bad_date', dumps({}), dumps({'medical_certificate': DEFAULT_MC})), cert_create()],
                  created={'user': 'owner', 'expiration': None}, drafts={'bad_date': {'medical_certificate': DEFAULT_MC}}))
add('expire-draft-null-date-server-error', 'owner', EXPIRE('bad_date'), 500, SERVER, body={'certificate_expiring_date': None},
    effect=effect('owner', [cert_create()], created={'user': 'owner', 'expiration': None}))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Subscription medical appointments and certificates authored sequence with effects',
                              'ids': IDS, 'documents': DOCUMENTS, 'certificates': CERTIFICATES, 'subscriptions': SUBSCRIPTIONS,
                              'appointments': APPOINTMENTS, 'drafts': DRAFTS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
