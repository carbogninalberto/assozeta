"""Author the subscription/<uid>/edit HTTP sequence (subscription-writes.md).

subscription_edit is an @api_view(['POST']) function view in
subscriptions_views.py (docstring: the OPTIONS description). In Django's order:

1. is_valid_uuid(uid): 400 ["badly formed hexadecimal UUID string"];
2. request.data (a malformed JSON body is DRF's 400 ParseError);
3. scoped_queryset(Associate.objects).filter(associate_id=uid).first(): the
   soft-delete, group-aware manager (X-Group-Id); a miss is None and
   ``associate.user`` a 500. The lookup resolves by id; the only check is
   associate.user == request.user (the owner for collaborators), otherwise
   PermissionDenied('User not allowed.');
4. the associate's age (relativedelta from date.today(), UTC) >= 18 is a 400
   ValidationError; a missing born_date is a 500 (strptime('None'));
5. data['associate_tutor_data'] (missing key, list, form or string bodies:
   500) and its initial_data['born_date'] parsed with '%d/%m/%Y' (500 when it
   does not parse); a tutor under 18 is a 400;
6. AssociateSerializer(data=...).is_valid(raise_exception=True): field errors
   (CharFields with model max lengths, the address_cap RegexValidator, the
   EmailField, the sex ChoiceField, is_minor, and the sport_association and
   family primary keys; sport_association goes through tenant_scope: another
   association for an owner is DRF's generic 403);
7. save(): without associate_id and without tax_code Associate.objects.create
   (the manager stores the raw X-Group-Id); with a tax_code it is uppercased
   and get_or_create(tax_code__iexact, sport_association, deleted=False) on
   the group-aware manager (QuerySet.create: no group); a match is updated
   field by field; several matches update the most recent one;
8. associate.main_tutor (get_main_tutor: the primary relation, otherwise the
   first relation is saved primary) is deleted when present: the collector
   deletes its tutor relations (both directions, reversed key order) then the
   associate;
9. associate.tutor = tutor (an attribute only: the new tutor is not linked),
   is_minor=True and save(): {'data': {'msg': 'subscription associate edited.'}}.

Requests are not atomic. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_writes_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, DELETED_ASSOCIATION = IDS['own_association'], IDS['foreign_association'], IDS['deleted_association']
ids = iter(f'a1000000-0000-4000-8000-{676001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000676999'
FAMILY = next(ids)
RELATION_IDS = iter(range(676001, 676100))


def associate(first, *, association=OWN, user='owner', born='2015-03-10', minor=False, group=None, deleted=False, tax=None):
    return {'id': next(ids), 'association': association, 'user': user, 'first': first, 'last': 'Fixture', 'born': born,
            'is_minor': minor, 'group': group, 'deleted': deleted, 'tax_code': tax}


IDS['multi_athlete'] = 'a1000000-0000-4000-8000-000000676998'
EXTRA_USERS = {'multi_athlete': {'id': IDS['multi_athlete'], 'username': 'subscription-writes-multi-athlete',
                                 'email': 'subscription-writes-multi-athlete@example.test'}}
ASSOCIATES = {
    'plain': associate('Plain'),
    'adult': associate('Adulto', born='1980-06-01'),
    'unborn': associate('Senzadata', born=None),
    'orphan': associate('Orfano', user=None),
    'abroad': associate('Hans', association=FOREIGN, user='foreign_owner'),
    'gone': associate('Cancellato', deleted=True),
    'grouped': associate('Gruppo', group='own_group'),
    'primary': associate('Primario', minor=True),
    'old_primary': associate('Tutore', born='1970-01-01', tax='TTRVCC70A01H501X'),
    'promoted': associate('Promosso', minor=True),
    'second_tutor': associate('Secondo', born='1971-02-02'),
    'first_tutor': associate('Primo', born='1972-03-03', association=None),
    'web': associate('Rete', minor=True),
    'web_tutor': associate('Nodo', born='1969-09-09'),
    'web_sibling': associate('Fratello'),
    'web_grand': associate('Nonno', born='1940-01-01'),
    'matched': associate('Abbinato'),
    'match_target': associate('Vecchio', born='1960-05-05', tax='mtcvcc60e05h501z'),
    'twins': associate('Gemelli'),
    'twin_old': associate('Gemello', born='1961-01-01', association=None, tax='GMLTWN61A01H501A'),
    'twin_new': associate('Gemello', born='1962-01-01', association=None, tax='gmltwn61a01h501a'),
    'collab': associate('Collaborato'),
    # An athlete linked to both associations: its member row here and in the own association.
    'athletic': associate('Atletico', user='multi_athlete', association=FOREIGN),
    'admin_case': associate('Amministrato'),
    'group_create': associate('Gruppocrea', group='own_group'),
    'group_match': associate('Gruppoabbina', group='own_group'),
    'group_target': associate('Bersaglio', born='1966-06-06', tax='GRPTGT66H06H501B'),
    'multi_home': associate('Casa', user='multi_athlete', born='1990-01-01'),
    # t2subsa
    'unicode_born': associate('Cifre'),
    'sharp_create': associate('Esse'),
    'sharp_match': associate('Essematch'),
    'sharp_target': associate('Bersaglio2', born='1960-05-05', tax='sstutor2'),
    'tie_assoc': associate('Pareggio'),
    'tie_a': associate('Pari', born='1963-03-03', association=None, tax='TIETAX01'),
    'tie_b': associate('Pari', born='1964-04-04', association=None, tax='tietax01'),
    'self_match': associate('Selfie', tax='SELFTAX1'),
    'self_tutor': associate('Autotutore'),
    'ref_child': associate('Referente'),
    'ref_tutor': associate('Referenziato', born='1970-02-02'),
}
# Creation dates order the MultipleObjectsReturned pick.
CREATED = {'twin_old': '2025-01-01T08:00:00Z', 'twin_new': '2025-06-01T08:00:00Z', 'tie_a': '2025-04-01T08:00:00Z',
           'tie_b': '2025-04-01T08:00:00Z'}


def relation(associate_label, tutor_label, primary):
    return {'id': next(RELATION_IDS), 'associate': associate_label, 'tutor': tutor_label, 'primary': primary}


RELATIONS = {
    'primary_link': relation('primary', 'old_primary', True),
    'promoted_second': relation('promoted', 'second_tutor', False),
    'promoted_first': relation('promoted', 'first_tutor', False),
    'web_link': relation('web', 'web_tutor', True),
    'web_other': relation('web_sibling', 'web_tutor', True),
    'web_up': relation('web_tutor', 'web_grand', True),
    'self_link': relation('self_tutor', 'self_tutor', True),
    'ref_link': relation('ref_child', 'ref_tutor', True),
}
# Other rows pointing at a tutor (SET_NULL): a membership.
MEMBERSHIPS = {'ref_membership': {'id': next(ids), 'associate': 'ref_tutor'}}
# promoted_first must have the lower key: first() orders by pk.
RELATIONS['promoted_second']['id'], RELATIONS['promoted_first']['id'] = RELATIONS['promoted_first']['id'], RELATIONS['promoted_second']['id']

cases = []


def add(name, actor, uid, status, response, *, method='POST', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    target = ASSOCIATES[uid]['id'] if uid in ASSOCIATES else uid
    case = {'name': f'subscription-writes-{name}', 'actor': actor, 'method': method, 'path': f'/subscription/{target}/edit',
            'headers': headers or {}, 'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


EDITED = {'data': {'msg': 'subscription associate edited.'}}
TUTOR = {'first_name': 'Giulia', 'last_name': 'Tutrice', 'born_date': '12/04/1975', 'sex': 'F'}
REVERSE = {'tutor_relations': 'application.AssociateTutorRelation.None', 'tutored_relations': 'application.AssociateTutorRelation.None'}
ACTOR_OWN = [OWN, 'actor.sport_association']


def tutor(**fields):
    return {'associate_tutor_data': {**TUTOR, **fields}}


def effect(actor, audit, *, created=None, changed=None, deleted=(), relations_changed=None, relations_deleted=(), detached=()):
    return {'actor': actor, 'audit': audit, 'created': created, 'changed': changed or {}, 'deleted': list(deleted),
            'relations_changed': relations_changed or {}, 'relations_deleted': list(relations_deleted), 'detached': list(detached)}


def new_row(*, association=None, group=None, tax=None, born='1975-04-12', **text):
    """The stored columns of the created tutor ('$new' / '$now' bound by the runner)."""
    row = {'associate_id': '$new', 'user_id': None, 'sport_association_id': association, 'picture_path': None,
           'first_name': 'Giulia', 'last_name': 'Tutrice', 'sex': 'F', 'tax_code': tax, 'born_date': born, 'born_city': None,
           'address_city': None, 'address': None, 'address_cap': None, 'nationality': 'Italiana', 'nationality_residence': 'Italia',
           'email': None, 'phone': None, 'phone_2': None, 'phone_3': None, 'phone_4': None, 'phone_label': None, 'phone_2_label': None,
           'phone_3_label': None, 'phone_4_label': None, 'is_minor': False, 'disabled': False, 'draft': False, 'group_id': group,
           'deleted': False, 'family_id': None, 'family_role': None, 'id_number': None, 'creation_date': '$now', 'migrated': False,
           'notes': None}
    row.update(text)
    return row


def created_changes(row):
    """auditlog's create diff of new_row: every non-null field against None, null
    foreign keys and DateTimeFields kept as None, plus the reverse accessors."""
    changes = {}
    for column, value in row.items():
        name = column[:-3] if column in ('user_id', 'sport_association_id', 'group_id', 'family_id') else column
        if value is None:
            if name in ('user', 'sport_association', 'group', 'family'):
                changes[name] = ['None', 'None']
            continue
        changes[name] = ['None', 'True' if value is True else 'False' if value is False else value]
    changes['associate_id'] = ['None', '$new']
    changes['creation_date'] = ['None', '$now']
    for name, manager in REVERSE.items():
        changes[name] = ['None', manager]
    return changes


def created(row, index):
    return {'model': 'associate', 'label': '$new', 'action': 0, 'changes': created_changes(row), 'index': index}


def updated(model, label, changes, index):
    return {'model': model, 'label': label, 'action': 1, 'changes': changes, 'index': index}


def removed(model, label, index, overrides=None):
    """A delete entry: the row captured before the request (associates add their
    reverse accessors) with the in-request differences."""
    return {'model': model, 'label': label, 'action': 2, 'changes': None, 'overrides': overrides or {}, 'index': index}


MINOR = {'is_minor': ['False', 'True']}
DIRECT = [OWN, 'direct']

# ---------------------------------------------------------------- transport and identity
add('anonymous', 'anonymous', 'plain', 401, {'detail': 'Authentication credentials were not provided.'}, body=tutor())
add('admin-without-session', 'admin', 'plain', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body=tutor())
add('restricted-missing-permission', 'restricted_collaborator', 'plain', 403, {'detail': 'Missing permission: association.members.update'},
    body=tutor())
add('owner-scoped-athlete-denied', 'owner', 'plain', 403, {'detail': 'Operazione non disponibile nel contesto dell’associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}, body=tutor())
# An association-scoped session checks the path uid as a subscription id.
add('association-session-uid-as-subscription', 'owner', 'plain', 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}, body=tutor())
add('wrong-method', 'owner', 'plain', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('options', 'owner', 'plain', 200, {'name': 'Subscription Edit', 'description': 'this endpoint temporalily is used to update the associate tutor data',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

# ---------------------------------------------------------------- lookup and ownership
add('malformed-uid', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'], body=tutor())
add('malformed-uid-before-body', 'owner', 'abc', 400, ['badly formed hexadecimal UUID string'], raw_body='{', content_type='application/json')
add('malformed-json', 'owner', 'plain', 400, {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{', content_type='application/json')
add('missing-server-error', 'owner', UNKNOWN, 500, SERVER, body=tutor())
add('soft-deleted-server-error', 'owner', 'gone', 500, SERVER, body=tutor())
add('other-group-server-error', 'owner', 'grouped', 500, SERVER, headers={'X-Group-Id': IDS['foreign_group']}, body=tutor())
add('without-user-denied', 'owner', 'orphan', 403, {'detail': 'User not allowed.'}, body=tutor())
add('other-owner-denied', 'owner', 'abroad', 403, {'detail': 'User not allowed.'}, body=tutor())
add('athlete-not-owner-denied', 'athlete', 'plain', 403, {'detail': 'User not allowed.'}, body=tutor())
add('adult-rejected', 'owner', 'adult', 400, ['Cannot edit associate tutor data for associate over 18 years old'], body=tutor())
add('without-born-date-server-error', 'owner', 'unborn', 500, SERVER, body=tutor())

# ---------------------------------------------------------------- tutor payload shape
add('missing-tutor-data-server-error', 'owner', 'plain', 500, SERVER, body={'other': 1})
add('list-body-server-error', 'owner', 'plain', 500, SERVER, body=[1, 2])
add('string-tutor-data-server-error', 'owner', 'plain', 500, SERVER, body={'associate_tutor_data': 'Giulia'})
add('list-tutor-data-server-error', 'owner', 'plain', 500, SERVER, body={'associate_tutor_data': [TUTOR]})
add('form-body-server-error', 'owner', 'plain', 500, SERVER, raw_body='associate_tutor_data=x', content_type='application/x-www-form-urlencoded')
add('tutor-without-born-date-server-error', 'owner', 'plain', 500, SERVER, body={'associate_tutor_data': {'first_name': 'Giulia'}})
add('tutor-iso-born-date-server-error', 'owner', 'plain', 500, SERVER, body=tutor(born_date='1975-04-12'))
add('tutor-impossible-date-server-error', 'owner', 'plain', 500, SERVER, body=tutor(born_date='31/02/1975'))
add('tutor-number-born-date-server-error', 'owner', 'plain', 500, SERVER, body=tutor(born_date=1975))
add('tutor-null-born-date-server-error', 'owner', 'plain', 500, SERVER, body=tutor(born_date=None))
add('tutor-minor-rejected', 'owner', 'plain', 400, ['Cannot edit associate tutor data for associate tutor under 18 years old'],
    body=tutor(born_date='01/01/2012'))
add('tutor-future-rejected', 'owner', 'plain', 400, ['Cannot edit associate tutor data for associate tutor under 18 years old'],
    body=tutor(born_date='01/01/2099'))

# ---------------------------------------------------------------- serializer validation
add('validation-text-fields', 'owner', 'plain', 400, {
    'first_name': ['Ensure this field has no more than 150 characters.'],
    'tax_code': ['Not a valid string.'],
    'phone': ['Ensure this field has no more than 30 characters.'],
    'family_role': ['Not a valid string.'],
    'address': ['Null characters are not allowed.'],
}, body=tutor(first_name='x' * 151, tax_code=True, phone='1' * 31, family_role={'a': 1}, address='via\x00roma'))
add('validation-cap-email-sex', 'owner', 'plain', 400, {
    'address_cap': ['Enter exactly 5 digits', 'Ensure this field has no more than 5 characters.'],
    'email': ['Enter a valid email address.'],
    'sex': ['"X" is not a valid choice.'],
}, body=tutor(address_cap='1234567', email='not-an-email', sex='X'))
add('validation-short-cap', 'owner', 'plain', 400, {'address_cap': ['Enter exactly 5 digits'], 'sex': ['"1" is not a valid choice.']},
    body=tutor(address_cap='12a', sex=1))
add('validation-nulls', 'owner', 'plain', 400, {
    'associate_id': ['This field may not be null.'],
    'sex': ['This field may not be null.'],
    'is_minor': ['This field may not be null.'],
}, body=tutor(associate_id=None, sex=None, is_minor=None))
add('validation-blank-id-boolean', 'owner', 'plain', 400, {
    'associate_id': ['This field may not be blank.'],
    'is_minor': ['Must be a valid boolean.'],
}, body=tutor(associate_id='  ', is_minor='maybe'))
add('validation-related', 'owner', 'plain', 400, {
    'sport_association': [f'Invalid pk "{UNKNOWN}" - object does not exist.'],
    'family': ['“abc” is not a valid UUID.'],
}, body=tutor(sport_association=UNKNOWN, family='abc'))
add('validation-related-types', 'owner', 'plain', 400, {
    'sport_association': ['Incorrect type. Expected pk value, received bool.'],
    'family': [f'Invalid pk "{UNKNOWN}" - object does not exist.'],
}, body=tutor(sport_association=True, family=UNKNOWN))
# UUIDField.to_python accepts an int (uuid.UUID(int=...)); a negative one is not a UUID.
add('validation-related-integers', 'owner', 'plain', 400, {'sport_association': ['Invalid pk "12" - object does not exist.'],
                                                           'family': ['“-1” is not a valid UUID.']},
    body=tutor(sport_association=12, family=-1))
GENERIC = {'detail': 'You do not have permission to perform this action.'}
# tenant_scope's PermissionDenied escapes the serializer's other field errors.
add('foreign-association-denied', 'owner', 'plain', 403, GENERIC, body=tutor(sport_association=FOREIGN, first_name='x' * 151))
add('deleted-association-denied', 'owner', 'plain', 403, GENERIC, body=tutor(sport_association=DELETED_ASSOCIATION))

# ---------------------------------------------------------------- effects
# No tax code: Associate.objects.create; the new tutor is not linked to the associate.
row = new_row()
add('creates-unlinked-tutor', 'owner', 'plain', 200, EDITED, body=tutor(), effect=effect('owner', [
    created(row, ACTOR_OWN),
    updated('associate', 'plain', MINOR, DIRECT),
], created=row, changed={'plain': {'is_minor': True}}))
# Again: a second tutor row, the associate already minor (no update entry).
row = new_row(first_name='Marta', email='marta@example.test', address_cap='00100', phone='3331234567', family_role='madre',
              is_minor=True, family_id=FAMILY)
add('creates-again-with-fields', 'owner', 'plain', 200, EDITED,
    body=tutor(first_name=' Marta ', email='marta@example.test', address_cap='00100', phone=3331234567, family_role='madre',
               is_minor='true', family=FAMILY, user=IDS['athlete'], unknown='ignored'),
    effect=effect('owner', [created(row, ACTOR_OWN)], created=row))

# A primary main tutor is deleted with its relation; the tutor is created with a tax code in the association.
row = new_row(association=OWN, tax='NWTTRS75D52H501K')
add('replaces-primary-tutor', 'owner', 'primary', 200, EDITED, body=tutor(tax_code='nwttrs75d52h501k', sport_association=OWN),
    effect=effect('owner', [
        created(row, DIRECT),
        removed('associatetutorrelation', 'primary_link', ACTOR_OWN),
        removed('associate', 'old_primary', [OWN, 'changes_json.sport_association']),
    ], created=row, deleted=['old_primary'], relations_deleted=['primary_link']))
# Without a primary relation get_main_tutor saves the first relation (lowest key) primary first.
row = new_row()
add('promotes-then-deletes-first-tutor', 'owner', 'promoted', 200, EDITED, body=tutor(), effect=effect('owner', [
    created(row, ACTOR_OWN),
    updated('associatetutorrelation', 'promoted_first', {'is_primary': ['False', 'True']}, [OWN, 'associate.sport_association']),
    removed('associatetutorrelation', 'promoted_first', ACTOR_OWN, {'is_primary': ['True', 'None']}),
    removed('associate', 'first_tutor', ACTOR_OWN),
], created=row, deleted=['first_tutor'], relations_changed={}, relations_deleted=['promoted_first']))
# The deleted tutor's other relations (it tutors a sibling and has its own tutor) go too, in reversed key order.
row = new_row()
add('deletes-tutor-relations', 'full_collaborator', 'web', 200, EDITED, body=tutor(), effect=effect('full_collaborator', [
    created(row, None),
    removed('associatetutorrelation', 'web_up', None),
    removed('associatetutorrelation', 'web_other', None),
    removed('associatetutorrelation', 'web_link', None),
    removed('associate', 'web_tutor', [OWN, 'changes_json.sport_association']),
], created=row, deleted=['web_tutor'], relations_deleted=['web_link', 'web_other', 'web_up']))

# A tax code match (case-insensitive) in the association is updated field by field.
add('updates-matching-tax-code', 'owner', 'matched', 200, EDITED,
    body=tutor(tax_code='MTCVCC60E05H501Z', sport_association=OWN, address_city='Roma'),
    effect=effect('owner', [
        updated('associate', 'match_target', {'first_name': ['Vecchio', 'Giulia'], 'last_name': ['Fixture', 'Tutrice'], 'sex': ['M', 'F'],
                                              'tax_code': ['mtcvcc60e05h501z', 'MTCVCC60E05H501Z'], 'born_date': ['1960-05-05', '1975-04-12'],
                                              'address_city': ['None', 'Roma']}, DIRECT),
        updated('associate', 'matched', MINOR, DIRECT),
    ], changed={'match_target': {'first_name': 'Giulia', 'last_name': 'Tutrice', 'sex': 'F', 'tax_code': 'MTCVCC60E05H501Z',
                                 'born_date': '1975-04-12', 'address_city': 'Roma'}, 'matched': {'is_minor': True}}))
# Several matches without an association: the most recent one is updated.
add('updates-most-recent-of-several', 'owner', 'twins', 200, EDITED, body=tutor(tax_code='gmltwn61a01h501a'), effect=effect('owner', [
    updated('associate', 'twin_new', {'first_name': ['Gemello', 'Giulia'], 'last_name': ['Fixture', 'Tutrice'], 'sex': ['M', 'F'],
                                      'tax_code': ['gmltwn61a01h501a', 'GMLTWN61A01H501A'], 'born_date': ['1962-01-01', '1975-04-12']},
            ACTOR_OWN),
    updated('associate', 'twins', MINOR, DIRECT),
], changed={'twin_new': {'first_name': 'Giulia', 'last_name': 'Tutrice', 'sex': 'F', 'tax_code': 'GMLTWN61A01H501A', 'born_date': '1975-04-12'},
            'twins': {'is_minor': True}}))

# A collaborator acts as the owner; its entries resolve no association of their own.
row = new_row(association=OWN)
add('full-collaborator', 'full_collaborator', 'collab', 200, EDITED, body=tutor(sport_association=OWN), effect=effect('full_collaborator', [
    created(row, DIRECT),
    updated('associate', 'collab', MINOR, DIRECT),
], created=row, changed={'collab': {'is_minor': True}}))
# An athlete's own associate and its tutor.
row = new_row(association=FOREIGN)
add('athlete-own-associate', 'multi_athlete', 'athletic', 200, EDITED, body=tutor(sport_association=FOREIGN), effect=effect('multi_athlete', [
    created(row, [FOREIGN, 'direct']),
    updated('associate', 'athletic', MINOR, [FOREIGN, 'direct']),
], created=row, changed={'athletic': {'is_minor': True}}))
row = new_row()
add('admin-impersonating-owner', 'admin', 'admin_case', 200, EDITED, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']},
    body=tutor(), effect=effect('admin', [
        created(row, [IDS['admin_association'], 'actor.sport_association']),
        updated('associate', 'admin_case', MINOR, DIRECT),
    ], created=row, changed={'admin_case': {'is_minor': True}}))
# The group-aware manager's create stores X-Group-Id; get_or_create's create does not.
row = new_row(group=IDS['own_group'])
add('group-create-stores-header', 'owner', 'group_create', 200, EDITED, headers={'X-Group-Id': IDS['own_group']}, body=tutor(),
    effect=effect('owner', [
        created(row, ACTOR_OWN),
        updated('associate', 'group_create', MINOR, DIRECT),
    ], created=row, changed={'group_create': {'is_minor': True}}))
row = new_row(association=OWN, tax='NWGRPP75D52H501C')
add('group-tax-code-create-without-group', 'owner', 'group_match', 200, EDITED, headers={'X-Group-Id': IDS['own_group']},
    body=tutor(tax_code='nwgrpp75d52h501c', sport_association=OWN), effect=effect('owner', [
        created(row, [OWN, 'changes_json.sport_association']),
        updated('associate', 'group_match', MINOR, DIRECT),
    ], created=row, changed={'group_match': {'is_minor': True}}))
# The group-aware get_or_create does not see the association's match outside the header's group: its
# create violates the case-insensitive unique index, the retries find nothing and the IntegrityError is a 500.
add('tax-code-match-outside-group-server-error', 'owner', 'grouped', 500, SERVER, headers={'X-Group-Id': IDS['own_group']},
    body=tutor(tax_code='GRPTGT66H06H501B', sport_association=OWN))

# ---------------------------------------------------------------- t2subsa
# strptime('%d/%m/%Y') reads the decimal digits of other scripts where its pattern has \\d (the second day digit, the year):
# the same tutor as the ASCII date.
row = new_row()
add('tutor-born-date-other-script-digits', 'owner', 'unicode_born', 200, EDITED, body=tutor(born_date='1\u0662/04/\u0661\u0669\u0667\u0665'),
    effect=effect('owner', [
        created(row, ACTOR_OWN),
        updated('associate', 'unicode_born', MINOR, DIRECT),
    ], created=row, changed={'unicode_born': {'is_minor': True}}))
# str.upper() expands 'ß' to 'SS' (SpecialCasing); the case-insensitive lookup and the stored code use the upper-cased text.
row = new_row(association=OWN, tax='SSTUTOR1')
add('tax-code-sharp-s-creates', 'owner', 'sharp_create', 200, EDITED, body=tutor(tax_code='\u00dftutor1', sport_association=OWN), effect=effect('owner', [
    created(row, DIRECT),
    updated('associate', 'sharp_create', MINOR, DIRECT),
], created=row, changed={'sharp_create': {'is_minor': True}}))
add('tax-code-sharp-s-matches', 'owner', 'sharp_match', 200, EDITED, body=tutor(tax_code='\u00dftutor2', sport_association=OWN), effect=effect('owner', [
    updated('associate', 'sharp_target', {'first_name': ['Bersaglio2', 'Giulia'], 'last_name': ['Fixture', 'Tutrice'], 'sex': ['M', 'F'],
                                          'tax_code': ['sstutor2', 'SSTUTOR2'], 'born_date': ['1960-05-05', '1975-04-12']}, DIRECT),
    updated('associate', 'sharp_match', MINOR, DIRECT),
], changed={'sharp_target': {'first_name': 'Giulia', 'last_name': 'Tutrice', 'sex': 'F', 'tax_code': 'SSTUTOR2', 'born_date': '1975-04-12'},
            'sharp_match': {'is_minor': True}}))
# Several matches with the same creation date: order_by('-creation_date').first() is the database's pick (the same query).
add('updates-first-of-tied-matches', 'owner', 'tie_assoc', 200, EDITED, body=tutor(tax_code='tietax01'), effect=effect('owner', [
    updated('associate', 'tie_a', {'first_name': ['Pari', 'Giulia'], 'last_name': ['Fixture', 'Tutrice'], 'sex': ['M', 'F'],
                                   'born_date': ['1963-03-03', '1975-04-12']}, ACTOR_OWN),
    updated('associate', 'tie_assoc', MINOR, DIRECT),
], changed={'tie_a': {'first_name': 'Giulia', 'last_name': 'Tutrice', 'sex': 'F', 'born_date': '1975-04-12'}, 'tie_assoc': {'is_minor': True}}))
# The tax code names the associate itself: the serializer saves the tutor data into its row, then the stale instance's
# save() writes the loaded values back (a second entry) with is_minor set.
add('tax-code-names-the-associate', 'owner', 'self_match', 200, EDITED, body=tutor(tax_code='selftax1', sport_association=OWN), effect=effect('owner', [
    updated('associate', 'self_match', {'first_name': ['Selfie', 'Giulia'], 'last_name': ['Fixture', 'Tutrice'], 'sex': ['M', 'F'],
                                        'born_date': ['2015-03-10', '1975-04-12']}, DIRECT),
    updated('associate', 'self_match', {'first_name': ['Giulia', 'Selfie'], 'last_name': ['Tutrice', 'Fixture'], 'sex': ['F', 'M'],
                                        'born_date': ['1975-04-12', '2015-03-10'], 'is_minor': ['False', 'True']}, DIRECT),
], changed={'self_match': {'is_minor': True}}))
# The associate is its own main tutor: the delete removes its row (and the relation); the final save() inserts it again.
SELF_ROW = new_row(association=OWN, first_name='Autotutore', last_name='Fixture', sex='M', tax=None, born='2015-03-10', is_minor=True,
                   user_id=IDS['owner'], creation_date='2025-03-01T08:00:00Z')
SELF_ROW['associate_id'] = ASSOCIATES['self_tutor']['id']
row = new_row()
self_created = created_changes({**SELF_ROW})
self_created['associate_id'] = ['None', ASSOCIATES['self_tutor']['id']]
self_created['creation_date'] = ['None', '2025-03-01 08:00:00']
add('own-main-tutor-is-recreated', 'owner', 'self_tutor', 200, EDITED, body=tutor(), effect=effect('owner', [
    created(row, ACTOR_OWN),
    removed('associatetutorrelation', 'self_link', ACTOR_OWN),
    removed('associate', 'self_tutor', [OWN, 'changes_json.sport_association']),
    # auditlog's pre_save finds no old row: an update against None (resolved through the changes), then the creation
    {'model': 'associate', 'label': 'self_tutor', 'action': 1, 'changes': self_created, 'index': [OWN, 'changes_json.sport_association']},
    {'model': 'associate', 'label': 'self_tutor', 'action': 0, 'changes': self_created, 'index': DIRECT},
], created=row, relations_deleted=['self_link'], changed={'self_tutor': {'is_minor': True}}))

# The deleted tutor is referenced by a membership: the Collector clears the column (no entry) and deletes the rest.
row = new_row()
add('deletes-referenced-tutor', 'owner', 'ref_child', 200, EDITED, body=tutor(), effect=effect('owner', [
    created(row, ACTOR_OWN),
    removed('associatetutorrelation', 'ref_link', ACTOR_OWN),
    removed('associate', 'ref_tutor', [OWN, 'changes_json.sport_association']),
    updated('associate', 'ref_child', MINOR, DIRECT),
], created=row, deleted=['ref_tutor'], relations_deleted=['ref_link'], changed={'ref_child': {'is_minor': True}}, detached=['ref_membership']))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'subscription/<uid>/edit authored sequence with associate, tutor relation and audit effects',
                              'ids': IDS, 'extra_users': EXTRA_USERS, 'family': FAMILY, 'associates': ASSOCIATES, 'created': CREATED, 'relations': RELATIONS, 'memberships': MEMBERSHIPS,
                              'reverse': REVERSE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
