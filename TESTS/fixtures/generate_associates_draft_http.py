"""Author the subscription/associates-draft list, add, edit, delete and bulk-delete HTTP sequence (associates-draft.md).

The function views answer athletes with an empty 401. list, edit and delete
read the owner's association with ``scoped_queryset(...).get(user=...)``
(500 without one); add uses ``.filter(...).first()`` (empty 401 without one)
and reads ``request.data`` only afterwards, while edit and delete parse the
body before the athlete check. bulk-delete reads ``request.user.sport_association``.

list renders ``{idx: draft.data + id + valid}`` in database order (the
contract compares the entries by id) and the newest importing / approving
status; an importing status older than an hour or an approving one older
than five minutes is saved with ``doing=False`` (auditlog update).

add stores the raw ``initial_data`` of four serializers and ``medical_certificate``;
``valid`` is ``AssociateSerializer.is_valid()`` of the associate and then (only
when the associate is valid) of the tutor. A tenant-scoped relation to another
association is DRF's generic 403.

edit replaces ``data`` with ``edit_data``, recomputes ``valid`` from the
mandatory fields and the ages (``dateutil.parser.parse``; a tutor parse error
is ignored), then links or creates a MedicalCertificate and stores its
expiration (``certificate_expiring_date``, ``%d/%m/%Y``). It is not atomic:
a missing ``medical_certificate`` saves the draft first, and a bad expiration
date fails after the certificate exists. The response echoes ``request.data``
with the mutated ``edit_data``. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('associates_draft_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, DELETED = IDS['own_association'], IDS['foreign_association'], IDS['deleted_association']
ids = iter(f'a1000000-0000-4000-8000-{573001 + n:012d}' for n in range(40))
UNKNOWN = 'a1000000-0000-4000-8000-000000579998'
SERVER, EMPTY = '<stable-server-error-page>', '<empty>'
ADULT = {'first_name': 'Mario', 'last_name': 'Rossi', 'sex': 'M', 'tax_code': 'RSSMRA80A01H501U', 'born_date': '1980-01-01',
         'born_city': 'Roma', 'address': 'Via Roma 1', 'address_cap': '00100', 'address_city': 'Roma'}
CERTIFICATES = {'linked': {'id': next(ids), 'user': 'owner', 'expiration': '2026-01-31', 'created': '2026-01-01T08:00:00Z'}}
LINKED = CERTIFICATES['linked']['id']
FAMILIES = {'family': {'id': next(ids)}}


def draft(association, data, valid):
    return {'id': next(ids), 'association': association, 'data': data, 'valid': valid}


DRAFTS = {
    'complete': draft(OWN, {'associate': ADULT, 'associate_tutor': {}, 'new_user_account': {'new_member': False},
                            'signature': {'there_is_signature': False, 'data': ''},
                            'medical_certificate': {'medical_id': LINKED, 'filename': None, 'certificate_expring_date': None}}, True),
    'partial': draft(OWN, {'associate': {'first_name': 'Ada'}, 'associate_tutor': None, 'score': 1.5, 'big': 12345678901234567890123,
                           'nested': [1, 'è', None, True]}, False),
    'unknown_validity': draft(OWN, {'associate': {}}, None),
    'removable': draft(OWN, {'associate': {'first_name': 'Rimosso'}, 'valid': 'stored key'}, False),
    'bulk_a': draft(OWN, {'associate': {'first_name': 'Primo'}}, True),
    'bulk_b': draft(OWN, {'associate': {'first_name': 'Secondo'}}, False),
    'abroad': draft(FOREIGN, {'associate': {'first_name': 'Estero'}}, True),
}
STATUSES = {
    'own_importing': {'id': next(ids), 'association': OWN, 'type': 1, 'doing': True, 'created': '2020-01-01T00:00:00Z'},
    'own_approving_new': {'id': next(ids), 'association': OWN, 'type': 2, 'doing': True, 'created': '2099-01-01T00:00:00Z'},
    'own_approving_old': {'id': next(ids), 'association': OWN, 'type': 2, 'doing': False, 'created': '2020-01-01T00:00:00Z'},
    'foreign_importing': {'id': next(ids), 'association': FOREIGN, 'type': 1, 'doing': True, 'created': '2099-01-01T00:00:00Z'},
}
cases = []


def add(name, actor, path, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        indexed=False):
    case = {'name': f'associates-draft-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    if indexed:
        case['indexed'] = True
    cases.append(case)


ref = lambda key: DRAFTS[key]['id'] if key in DRAFTS else key
LIST, ADD, BULK = '/subscription/associates-draft/list', '/subscription/associates-draft/add', '/subscription/associates-draft/bulk-delete'
EDIT = lambda key: f'/subscription/associates-draft/{ref(key)}/edit'
DELETE = lambda key: f'/subscription/associates-draft/{ref(key)}/delete'
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
missing = lambda key: {'detail': f'Missing permission: association.members.{key}'}
not_allowed = lambda method: {'detail': f'Method "{method}" not allowed.'}
options = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
JSON_ERROR = {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'}
FORM = 'application/x-www-form-urlencoded'


def listed(*labels):
    return {str(i): {**DRAFTS[l]['data'], 'associate_import_draft_id': DRAFTS[l]['id'], 'valid': DRAFTS[l]['valid']}
            for i, l in enumerate(labels)}


# list (before any write)
OWN_LIST = {'data': listed('complete', 'partial', 'unknown_validity', 'removable', 'bulk_a', 'bulk_b'), 'approving': True, 'importing': False}
add('list-owner-expires-importing', 'owner', LIST, 200, OWN_LIST, method='GET', indexed=True,
    effect={'kind': 'status', 'actor': 'owner', 'rows': {'own_importing': False}})
add('list-full-collaborator', 'full_collaborator', LIST, 200, OWN_LIST, method='GET', indexed=True)
add('list-admin-impersonating-owner', 'admin', LIST, 200, OWN_LIST, method='GET', headers=ADMIN_OWNER, indexed=True)
add('list-foreign-owner', 'foreign_owner', LIST, 200, {'data': listed('abroad'), 'approving': False, 'importing': True}, method='GET',
    indexed=True)
add('list-owner-without-association-server-error', 'owner_without_association', LIST, 500, SERVER, method='GET')
add('list-deleted-association-server-error', 'deleted_association_owner', LIST, 500, SERVER, method='GET')
add('list-athlete-unauthorized', 'athlete', LIST, 401, EMPTY, method='GET')
add('list-owner-scoped-athlete-denied', 'owner', LIST, 403, SCOPED_DENIED, method='GET', headers=SCOPED)
add('list-restricted-missing-permission', 'restricted_collaborator', LIST, 403, missing('read'), method='GET')
add('list-admin-without-session', 'admin', LIST, 403, ADMIN, method='GET')
add('list-anonymous', 'anonymous', LIST, 401, ANONYMOUS, method='GET')
add('list-wrong-method', 'owner', LIST, 405, not_allowed('POST'), body={})
add('list-options', 'owner', LIST, 200, options('Subscription Associates Draft List'), method='OPTIONS')

# add
ASSOCIATE = {**ADULT, 'born_date': '01/01/1980', 'email': 'mario@example.com', 'phone': '333 1234567', 'is_minor': False,
             'sport_association': OWN}
SAVED = {'msg': 'Draft saved!'}


def submission(associate=ASSOCIATE, tutor=None, **extra):
    return {'new_user_account': {'new_member': True, 'username': 'mrossi'}, 'associate_data': associate,
            'associate_tutor_data': {} if tutor is None else tutor, 'signature': {'there_is_signature': False, 'data': ''},
            'medical_certificate': {'medical_id': None, 'filename': None}, **extra}


def stored(body):
    return {'associate': body['associate_data'], 'associate_tutor': body['associate_tutor_data'], 'new_user_account': body['new_user_account'],
            'signature': body['signature'], 'medical_certificate': body['medical_certificate']}


def added(name, body, valid, actor='owner'):
    add(name, actor, ADD, 200, SAVED, body=body, effect={'kind': 'add', 'actor': actor, 'stored': stored(body), 'valid': valid})


with_field = lambda **fields: submission({**ASSOCIATE, **fields})
added('add-valid', submission(), True)
added('add-valid-collaborator', submission(), True, actor='full_collaborator')
added('add-empty-associate-valid', submission({}), True)
added('add-iso-born-date-invalid', with_field(born_date='1980-01-01'), False)
added('add-impossible-born-date-invalid', with_field(born_date='31/02/1980'), False)
added('add-cap-letters-invalid', with_field(address_cap='0010A'), False)
added('add-cap-unicode-digits-valid', with_field(address_cap='٠٠١٠٠'), True)
added('add-cap-padded-valid', with_field(address_cap=' 00100 '), True)
added('add-email-invalid', with_field(email='mario@'), False)
added('add-email-blank-valid', with_field(email=''), True)
added('add-sex-invalid', with_field(sex='X'), False)
added('add-sex-null-invalid', with_field(sex=None), False)
added('add-is-minor-text-valid', with_field(is_minor='Yes'), True)
added('add-is-minor-text-invalid', with_field(is_minor='maybe'), False)
added('add-associate-id-blank-invalid', with_field(associate_id='  '), False)
added('add-phone-number-valid', with_field(phone=3331234567), True)
added('add-tax-code-too-long-invalid', with_field(tax_code='R' * 26), False)
added('add-name-bool-invalid', with_field(first_name=True), False)
added('add-null-associate-invalid', submission(None), False)
added('add-list-associate-invalid', submission([ASSOCIATE]), False)
added('add-tutor-invalid', submission(tutor={'born_date': '2010-02-30'}), False)
added('add-unknown-association-invalid', with_field(sport_association=UNKNOWN), False)
added('add-malformed-association-invalid', with_field(sport_association='abc'), False)
added('add-blank-association-valid', with_field(sport_association=''), True)
added('add-known-family-valid', with_field(family=FAMILIES['family']['id']), True)
added('add-unknown-family-invalid', with_field(family=UNKNOWN), False)
# The tutor is validated only for a valid associate.
added('add-foreign-tutor-after-invalid-associate', submission({**ASSOCIATE, 'sex': 'X'}, tutor={'sport_association': FOREIGN}), False)
add('add-foreign-tutor-denied', 'owner', ADD, 403, FORBIDDEN, body=submission(tutor={'sport_association': FOREIGN}))
add('add-foreign-association-denied-before-field-errors', 'owner', ADD, 403, FORBIDDEN,
    body=with_field(address_cap='x', sport_association=FOREIGN))
add('add-deleted-association-denied', 'owner', ADD, 403, FORBIDDEN, body=with_field(sport_association=DELETED))
add('add-form-strings-invalid', 'owner', ADD, 200, SAVED, raw_body='new_user_account=a&associate_data=b&associate_tutor_data=c'
    '&signature=d&medical_certificate=e&medical_certificate=f', content_type=FORM,
    effect={'kind': 'add', 'actor': 'owner', 'valid': False,
            'stored': {'associate': 'b', 'associate_tutor': 'c', 'new_user_account': 'a', 'signature': 'd', 'medical_certificate': 'f'}})
add('add-missing-medical-certificate-server-error', 'owner', ADD, 500, SERVER,
    body={k: v for k, v in submission().items() if k != 'medical_certificate'})
add('add-missing-signature-server-error', 'owner', ADD, 500, SERVER, body={k: v for k, v in submission().items() if k != 'signature'})
add('add-list-body-server-error', 'owner', ADD, 500, SERVER, body=[submission()])
add('add-null-character-server-error', 'owner', ADD, 500, SERVER, body=with_field(first_name='Ma\x00rio'))
add('add-malformed-json', 'owner', ADD, 400, JSON_ERROR, raw_body='{x', content_type='application/json')
add('add-athlete-unauthorized', 'athlete', ADD, 401, EMPTY, body=submission())
add('add-athlete-before-body', 'athlete', ADD, 401, EMPTY, raw_body='{x', content_type='application/json')
add('add-owner-without-association-unauthorized', 'owner_without_association', ADD, 401, EMPTY, raw_body='{x',
    content_type='application/json')
add('add-deleted-association-unauthorized', 'deleted_association_owner', ADD, 401, EMPTY, body=submission())
add('add-owner-scoped-athlete-denied', 'owner', ADD, 403, SCOPED_DENIED, body=submission(), headers=SCOPED)
add('add-restricted-missing-permission', 'restricted_collaborator', ADD, 403, missing('create'), body=submission())
add('add-admin-without-session', 'admin', ADD, 403, ADMIN, body=submission())
add('add-anonymous', 'anonymous', ADD, 401, ANONYMOUS, body=submission())
add('add-wrong-method', 'owner', ADD, 405, not_allowed('GET'), method='GET')
add('add-options', 'owner', ADD, 200, options('Subscription Associates Draft Add'), method='OPTIONS')

# edit
NOT_FOUND = {'msg': 'not found.'}
NONE_CERTIFICATE = {'medical_id': None, 'filename': None, 'certificate_expring_date': None}


def edited(name, actor, key, body, response, *, valid, saves, certificate, status=200, first_save=False, final_save=True):
    """saves: the draft data of each save ('$mc' is the certificate id); certificate: the linked
    label (None creates one), the expiration it stores and whether it is saved with a change."""
    add(name, actor, EDIT(key), status, response, body=body,
        effect={'kind': 'edit', 'actor': actor, 'draft': key, 'valid': valid, 'saves': saves, 'first_save': first_save,
                'final_save': final_save, 'certificate': certificate})


def with_mc(data, mc='$mc', expiring=None, base=None):
    certificate = dict(NONE_CERTIFICATE if base is None else base)
    certificate['medical_id'] = mc
    if expiring is not None:
        certificate['certificate_expring_date'] = expiring
    return {**data, 'medical_certificate': certificate}


body = {'edit_data': {'associate': ADULT, 'associate_tutor': {},
                      'medical_certificate': {'medical_id': LINKED, 'filename': 'cert.pdf', 'certificate_expring_date': None}, 'extra': 1.0},
        'certificate_expiring_date': '31/12/2027'}
final = with_mc(body['edit_data'], LINKED, '31/12/2027', body['edit_data']['medical_certificate'])
edited('edit-adult-linked-certificate', 'owner', 'complete', body, {'data': {**body, 'edit_data': final}}, valid=True, saves=[final],
       certificate={'label': 'linked', 'expiration': '2027-12-31', 'updated': True})
edit_data = {'associate': {**ADULT, 'born_date': '2015-06-01T10:00:00Z'}, 'associate_tutor': {'born_date': '01/31/1980'}}
edited('edit-minor-adult-tutor-creates-certificate', 'owner', 'partial', {'edit_data': edit_data},
       {'data': {'edit_data': with_mc(edit_data, '$new')}}, valid=True, first_save=True,
       saves=[with_mc(edit_data, None), with_mc(edit_data)], certificate={'label': None, 'expiration': None, 'updated': False})
edit_data = {'associate': {**ADULT, 'born_date': '2015-06-01'}, 'associate_tutor': {'born_date': '13/03/2012'}}
edited('edit-minor-minor-tutor-collaborator', 'full_collaborator', 'unknown_validity', {'edit_data': edit_data, 'certificate_expiring_date': ''},
       {'data': {'edit_data': with_mc(edit_data, '$new'), 'certificate_expiring_date': ''}}, valid=False, first_save=True,
       saves=[with_mc(edit_data, None), with_mc(edit_data)], certificate={'label': None, 'expiration': None, 'updated': False})
for label, tutor in (('blank', {'born_date': ''}), ('missing', {}), ('number', {'born_date': 2012}), ('list', ['2012-01-01']),
                     ('impossible', {'born_date': '2012-02-30'})):
    edit_data = {'associate': {**ADULT, 'born_date': '01/06/2015'}, 'associate_tutor': tutor, 'medical_certificate': {'medical_id': None}}
    edited(f'edit-minor-tutor-{label}-ignored', 'owner', 'partial', {'edit_data': edit_data},
           {'data': {'edit_data': {**edit_data, 'medical_certificate': {'medical_id': '$new'}}}}, valid=True,
           saves=[{**edit_data, 'medical_certificate': {'medical_id': '$mc'}}], certificate={'label': None, 'expiration': None, 'updated': False})
edit_data = {'associate': {k: v for k, v in ADULT.items() if k != 'sex'}, 'medical_certificate': {}}
edited('edit-missing-mandatory-new-certificate-expiring', 'owner', 'partial', {'edit_data': edit_data, 'certificate_expiring_date': '1/2/2028'},
       {'data': {'edit_data': {**edit_data, 'medical_certificate': {'medical_id': '$new', 'certificate_expring_date': '1/2/2028'}},
                 'certificate_expiring_date': '1/2/2028'}}, valid=False,
       saves=[{**edit_data, 'medical_certificate': {'medical_id': '$mc', 'certificate_expring_date': '1/2/2028'}}],
       certificate={'label': None, 'expiration': '2028-02-01', 'updated': True})
edit_data = {'associate': {**{k: '' for k in ADULT}, 'born_date': None}, 'medical_certificate': {'medical_id': LINKED.upper()}}
edited('edit-all-mandatory-empty-linked-certificate', 'owner', 'complete', {'edit_data': edit_data},
       {'data': {'edit_data': {**edit_data, 'medical_certificate': {'medical_id': LINKED}}}}, valid=False,
       saves=[{**edit_data, 'medical_certificate': {'medical_id': LINKED}}], certificate={'label': 'linked', 'expiration': None, 'updated': False})
edit_data = {'associate': {**ADULT, 'born_date': '01/31/1985'}}
edited('edit-bad-expiration-after-saves-server-error', 'owner', 'unknown_validity', {'edit_data': edit_data, 'certificate_expiring_date': '2027-12-31'},
       SERVER, status=500, valid=True, first_save=True, final_save=False, saves=[with_mc(edit_data, None)],
       certificate={'label': None, 'expiration': None, 'updated': False})
edit_data = {'associate': ADULT, 'medical_certificate': {'medical_id': LINKED}}
edited('edit-numeric-expiration-after-certificate-server-error', 'owner', 'complete', {'edit_data': edit_data, 'certificate_expiring_date': 20271231},
       SERVER, status=500, valid=True, final_save=False, saves=[], certificate={'label': 'linked', 'expiration': None, 'updated': False})
for label, value in (('malformed-medical-id', {'associate': ADULT, 'medical_certificate': {'medical_id': 'abc'}}),
                     ('list-medical-certificate', {'associate': ADULT, 'medical_certificate': []}),
                     ('missing-associate', {}), ('string-associate', {'associate': 'x'}),
                     ('missing-born-date', {'associate': {k: v for k, v in ADULT.items() if k != 'born_date'}}),
                     ('number-born-date', {'associate': {**ADULT, 'born_date': 19800101}}),
                     ('impossible-born-date', {'associate': {**ADULT, 'born_date': '1980-02-30'}}),
                     ('day-first-overflow', {'associate': {**ADULT, 'born_date': '13/13/1980'}}),
                     ('blank-born-date', {'associate': {**ADULT, 'born_date': ''}})):
    add(f'edit-{label}-server-error', 'owner', EDIT('complete'), 500, SERVER, body={'edit_data': value})
add('edit-string-edit-data-server-error', 'owner', EDIT('complete'), 500, SERVER, body={'edit_data': 'x'})
add('edit-list-body-server-error', 'owner', EDIT('complete'), 500, SERVER, body=[1])
add('edit-list-body-unknown', 'owner', EDIT(UNKNOWN), 404, NOT_FOUND, body=[1])
add('edit-without-edit-data', 'owner', EDIT('complete'), 200, {'data': {'note': 1.5, 'nothing': None, 'text': 'è'}},
    body={'note': 1.5, 'nothing': None, 'text': 'è'})
add('edit-form-without-edit-data', 'owner', EDIT('complete'), 200, {'data': {'a': '2', 'b': 'x'}}, raw_body='a=1&a=2&b=x', content_type=FORM)
add('edit-form-edit-data-server-error', 'owner', EDIT('complete'), 500, SERVER, raw_body='edit_data=x', content_type=FORM)
add('edit-urn-id', 'owner', EDIT('urn:uuid:' + DRAFTS['complete']['id']), 200, {'data': {}}, body={})
add('edit-unknown', 'owner', EDIT(UNKNOWN), 404, NOT_FOUND, body={'edit_data': {}})
add('edit-foreign', 'owner', EDIT('abroad'), 404, NOT_FOUND, body={'edit_data': {}})
add('edit-malformed-id', 'owner', EDIT('abc'), 400, ['badly formed hexadecimal UUID string'], body={})
add('edit-non-hex-id', 'owner', EDIT('g' * 32), 400, [f"invalid literal for int() with base 16: '{'g' * 32}'"], body={})
add('edit-owner-without-association-server-error', 'owner_without_association', EDIT('complete'), 500, SERVER, body={})
add('edit-athlete-unauthorized', 'athlete', EDIT('complete'), 401, EMPTY, body={})
add('edit-athlete-malformed-json', 'athlete', EDIT('complete'), 400, JSON_ERROR, raw_body='{x', content_type='application/json')
add('edit-owner-scoped-athlete-denied', 'owner', EDIT('complete'), 403, SCOPED_DENIED, body={}, headers=SCOPED)
add('edit-restricted-missing-permission', 'restricted_collaborator', EDIT('complete'), 403, missing('update'), body={})
add('edit-admin-without-session', 'admin', EDIT('complete'), 403, ADMIN, body={})
add('edit-anonymous', 'anonymous', EDIT('complete'), 401, ANONYMOUS, body={})
add('edit-wrong-method', 'owner', EDIT('complete'), 405, not_allowed('GET'), method='GET')
add('edit-options', 'owner', EDIT('complete'), 200, options('Subscription Associates Draft Edit'), method='OPTIONS')

# delete
DELETED_MSG = {'msg': 'successfully deleted.'}
add('delete-athlete-unauthorized', 'athlete', DELETE('removable'), 401, EMPTY, body={})
add('delete-athlete-malformed-json', 'athlete', DELETE('removable'), 400, JSON_ERROR, raw_body='{x', content_type='application/json')
add('delete-malformed-id', 'owner', DELETE('abc'), 400, ['badly formed hexadecimal UUID string'], body={})
add('delete-owner-without-association-server-error', 'owner_without_association', DELETE('removable'), 500, SERVER, body={})
add('delete-restricted-missing-permission', 'restricted_collaborator', DELETE('removable'), 403, missing('delete'), body={})
add('delete-anonymous', 'anonymous', DELETE('removable'), 401, ANONYMOUS)
add('delete-wrong-method', 'owner', DELETE('removable'), 405, not_allowed('GET'), method='GET')
add('delete-options', 'owner', DELETE('removable'), 200, options('Subscription Associates Draft Delete'), method='OPTIONS')
add('delete-foreign-kept', 'owner', DELETE('abroad'), 200, DELETED_MSG, body={})
add('delete-unknown', 'owner', DELETE(UNKNOWN), 200, DELETED_MSG)
add('delete-own-list-body', 'full_collaborator', DELETE('removable'), 200, DELETED_MSG, body=[1],
    effect={'kind': 'delete', 'actor': 'full_collaborator', 'labels': ['removable']})
add('delete-again', 'owner', DELETE('removable'), 200, DELETED_MSG, body={})

# bulk-delete
bulk_msg = lambda deleted, total: {'msg': f'{deleted}/{total} bozze eliminate con successo.', 'deleted_count': deleted, 'total_count': total}
NOT_LIST = {'msg': 'La richiesta deve contenere una lista di ID da eliminare.'}
add('bulk-athlete-unauthorized', 'athlete', BULK, 401, EMPTY, method='DELETE', raw_body='{x', content_type='application/json')
add('bulk-not-list', 'owner', BULK, 400, NOT_LIST, method='DELETE', body={'associate_import_draft_ids': 'x'})
add('bulk-form-not-list', 'owner', BULK, 400, NOT_LIST, method='DELETE', raw_body='associate_import_draft_ids=x', content_type=FORM)
add('bulk-list-body-server-error', 'owner', BULK, 500, SERVER, method='DELETE', body=['x'])
add('bulk-malformed-id-server-error', 'owner', BULK, 500, SERVER, method='DELETE',
    body={'associate_import_draft_ids': [DRAFTS['bulk_a']['id'], 'abc']})
add('bulk-float-id-server-error', 'owner', BULK, 500, SERVER, method='DELETE', body={'associate_import_draft_ids': [1.5]})
add('bulk-missing-key', 'owner', BULK, 200, bulk_msg(0, 0), method='DELETE', body={})
add('bulk-empty-body', 'owner', BULK, 200, bulk_msg(0, 0), method='DELETE')
add('bulk-integer-and-null', 'owner', BULK, 200, bulk_msg(0, 2), method='DELETE', body={'associate_import_draft_ids': [5, None]})
add('bulk-owner-without-association-server-error', 'owner_without_association', BULK, 500, SERVER, method='DELETE', body={})
add('bulk-restricted-missing-permission', 'restricted_collaborator', BULK, 403, missing('delete'), method='DELETE', body={})
add('bulk-anonymous', 'anonymous', BULK, 401, ANONYMOUS, method='DELETE', body={})
add('bulk-wrong-method', 'owner', BULK, 405, not_allowed('POST'), body={})
add('bulk-options', 'owner', BULK, 200, options('Subscription Associates Draft Bulk Delete'), method='OPTIONS')
add('bulk-delete', 'owner', BULK, 200, bulk_msg(2, 6), method='DELETE',
    body={'associate_import_draft_ids': [DRAFTS['bulk_a']['id'], '{' + DRAFTS['bulk_b']['id'].upper() + '}', DRAFTS['bulk_a']['id'],
                                         DRAFTS['abroad']['id'], UNKNOWN, None]},
    effect={'kind': 'delete', 'actor': 'owner', 'labels': ['bulk_a', 'bulk_b']})
add('list-after-writes-foreign-kept', 'foreign_owner', LIST, 200, {'data': listed('abroad'), 'approving': False, 'importing': True},
    method='GET', indexed=True)
assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Associate import drafts authored sequence with draft, certificate, status and audit effects',
                              'ids': IDS, 'drafts': DRAFTS, 'statuses': STATUSES, 'certificates': CERTIFICATES, 'families': FAMILIES,
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
