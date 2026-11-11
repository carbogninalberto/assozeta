"""Author the subscription calculate-tax-code, import/status, get-associations-for-federation,
generate-token-link, validate-token-link-and-get-subscriptions and delete-document HTTP
sequence (subscription-misc.md).

calculate-tax-code (no permission class: DRF's AllowAny) answers ``{'tax_code': None}`` when
one of the five keys is missing, 400 ``alcuni dati sono mancanti.`` for a None/'' value or a
sex that is not M/F after ``upper()`` (a non-string sex is an AttributeError, 500), then calls
``codicefiscale.encode``: a birthplace the library cannot map is 400 ``Città non mappata.``
and every other exception 400 ``alcuni dati non sono validi.``. Expected codes are computed by
the small encoder below (consonants, vowels, month letters, CIN) from the ISTAT codes the
library's data assigns (Roma H501, Milano F205, Verona L781, Busana B283 until 2015-12-31,
Germania Z112); encode() also decodes its own result, with the two-digit year read in the
current century, so a 1920 birth in Busana (decoded as 2020, after its deletion) is invalid.

import/status reads ``request.user.sport_association`` (athletes 403, no association 500) and
the newest importing status's ``doing`` (nullable).

get-associations-for-federation lists ``{'id', 'denomination'}`` of the association's
subscriptions whose custom_data text contains one of the two ``type_of_associate`` markers
(``UPPER(custom_data::text) LIKE UPPER('%...%')``), first denomination wins, in database order
(the contract compares the items by denomination and accepts either id of a duplicate). A
non-dict custom_data or an unhashable denomination is skipped by the view's try/except.

generate-token-link stores a CumulativeSubscriptionGymLinks (not audited) with a uuid4 token,
``str()`` of the gym_name and an expiry two days ahead, and echoes the in-memory values.

validate-token-link-and-get-subscriptions (no permission classes) validates the token with
``uuid.UUID``, finds a live link by the raw text, deletes every expired link, answers
``{'expired': True}`` without a link, then serializes the fuzzy matches (difflib ratio above 70
on the lower-cased school_name or denomination) among the link association's subscriptions
whose custom_data contains the first three characters of the gym name and that started at
least 180 days ago, excluding associates with an active accepted/pending subscription.

delete-document validates both ids, needs the user's live association (403 ``User not
allowed.``), raises TypeError (500) for a missing subscription or file, deletes the file's
Document (cascading to the file) outside an association-scoped impersonation and then the
file itself (a second auditlog delete of the stale instance).
Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_misc_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN, OWNER, FOREIGN_OWNER = IDS['own_association'], IDS['foreign_association'], IDS['owner'], IDS['foreign_owner']
OWN_GROUP, FOREIGN_GROUP = IDS['own_group'], IDS['foreign_group']
ids = iter(f'a1000000-0000-4000-8000-{1410001 + n:012d}' for n in range(200))
UNKNOWN = 'a1000000-0000-4000-8000-000001419999'
SERVER = '<stable-server-error-page>'
cases = []

# --- Seeds ---------------------------------------------------------------------------------------------------------------
USERS = {
    OWNER: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700001@example.test', 'phone': None,
            'username': 'message-reads-700001', 'role': 1},
    FOREIGN_OWNER: {'first_name': 'Message', 'last_name': 'Fixture', 'email': 'message-reads-700007@example.test', 'phone': None,
                    'username': 'message-reads-700007', 'role': 1},
}
STATUSES = {
    'own_newest': {'id': next(ids), 'association': OWN, 'type': 1, 'doing': True, 'created': '2025-06-01T08:00:00Z'},
    'own_older': {'id': next(ids), 'association': OWN, 'type': 1, 'doing': False, 'created': '2020-01-01T08:00:00Z'},
    'own_approving': {'id': next(ids), 'association': OWN, 'type': 2, 'doing': False, 'created': '2099-01-01T08:00:00Z'},
    'foreign_unknown': {'id': next(ids), 'association': FOREIGN, 'type': 1, 'doing': None, 'created': '2024-01-01T08:00:00Z'},
}
ASSOCIATE_KEYS = ('address', 'address_cap', 'address_city', 'born_city', 'born_date', 'email', 'first_name', 'last_name', 'is_minor',
                  'sex', 'tax_code', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label', 'phone_2_label', 'phone_3_label',
                  'phone_4_label', 'picture_path')


def person(first, last, association=OWN, **values):
    row = {key: None for key in ASSOCIATE_KEYS}
    row.update({'id': next(ids), 'association': association, 'first_name': first, 'last_name': last, 'sex': 'A', 'is_minor': False,
                'created': '2023-01-01T08:00:00Z'}, **values)
    return row


ASSOCIATES = {
    'rossi': person('Mario', 'Rossi', tax_code='RSSMRA90E10H501Z', email='mario@example.test', phone='3331112222', sex='M',
                    address='Via Roma 1', address_city='Verona', address_cap='37121', born_city='Verona'),
    'verdi': person('Giulia', 'Verdi', sex='F'),
    'bianchi': person('Luca', 'Bianchi'),
    'neri': person('Anna', 'Neri', phone_label='casa'),
    'gallo': person('Paolo', 'Gallo', group=OWN_GROUP),
    'kodokan': person('Jigoro', 'Kano', association=FOREIGN),
}
PAYMENTS = {'rossi': {'id': next(ids), 'association': OWN, 'associate': 'rossi', 'amount': '25.00', 'paid': True,
                      'created': '2020-02-01T08:00:00Z'}}
DOCUMENTS = {label: {'id': next(ids), 'token': next(ids), 'filename': f'{label}.pdf', 'created': '2024-03-01T08:00:00Z'}
             for label in ('privacy', 'impersonated', 'grouped', 'foreign', 'shared')}
SUB_DEFAULTS = {'association': OWN, 'user': OWNER, 'associate': None, 'start': None, 'end': None, 'status': 4, 'type': 2, 'role': 1,
                'custom_data': None, 'deleted': False, 'group': None, 'created': '2020-01-01T08:00:00Z'}


def sub(**values):
    return {**SUB_DEFAULTS, 'id': next(ids), **values}


SCHOOL = {'school_name': 'Palestra Olimpia', 'school_teacher_name': 'Mario Bianchi', 'principal_style': 'Karate',
          'type_of_associate': 'atleta'}
OLD = {'start': '2020-01-01', 'end': '2020-12-31'}
SUBSCRIPTIONS = {
    # get-associations-for-federation (no start date: never a validate-token-link candidate)
    'fed_asd': sub(custom_data={'type_of_associate': 'asd-o-ssd', 'denomination': 'ASD Alpha'}),
    'fed_asd_twin': sub(custom_data={'denomination': 'ASD Alpha', 'type_of_associate': 'asd-o-ssd', 'note': 'twin'}),
    'fed_school': sub(custom_data={'type_of_associate': 'scuola-asc', 'denomination': 'Scuola Beta'}),
    'fed_upper': sub(custom_data={'TYPE_OF_ASSOCIATE': 'ASD-O-SSD', 'denomination': 'Delta'}),
    'fed_nested': sub(custom_data={'meta': {'type_of_associate': 'scuola-asc'}, 'denomination': 'Nested'}),
    'fed_number': sub(custom_data={'type_of_associate': 'asd-o-ssd', 'denomination': 5}),
    'fed_null': sub(custom_data={'type_of_associate': 'scuola-asc', 'denomination': None}),
    'fed_dict': sub(custom_data={'type_of_associate': 'asd-o-ssd', 'denomination': {'name': 'Unhashable'}}),
    'fed_list': sub(custom_data=[{'type_of_associate': 'asd-o-ssd', 'denomination': 'In a list'}]),
    'fed_unnamed': sub(custom_data={'type_of_associate': 'asd-o-ssd'}),
    'fed_suffix': sub(custom_data={'type_of_associate': 'asd-o-ssd-extra', 'denomination': 'Suffix'}),
    'fed_other': sub(custom_data={'type_of_associate': 'atleta', 'denomination': 'Gamma'}),
    'fed_deleted': sub(custom_data={'type_of_associate': 'asd-o-ssd', 'denomination': 'Removed'}, deleted=True),
    'fed_grouped': sub(custom_data={'type_of_associate': 'scuola-asc', 'denomination': 'Grouped'}, group=OWN_GROUP),
    'fed_foreign': sub(custom_data={'type_of_associate': 'asd-o-ssd', 'denomination': 'Abroad'}, association=FOREIGN,
                       user=FOREIGN_OWNER),
    # validate-token-link-and-get-subscriptions
    'v_olimpia': sub(associate='rossi', custom_data=dict(SCHOOL), **OLD),
    'v_olimpya': sub(associate='verdi', custom_data={'denomination': 'Palestra Olimpya'}, start='2021-01-01', end='2021-12-31', status=2),
    'v_palazzo': sub(associate='verdi', custom_data={'school_name': 'Palazzo dello Sport'}, **OLD),
    'v_note': sub(custom_data={'note': 'Palestra'}, **OLD),
    'v_future': sub(associate='verdi', custom_data=dict(SCHOOL), start='2099-01-01', end='2099-12-31'),
    'v_bianchi_now': sub(associate='bianchi', start='2000-01-01', end='2099-12-31'),
    'v_bianchi_old': sub(associate='bianchi', custom_data=dict(SCHOOL), **OLD),
    'v_neri_next': sub(associate='neri', start='2098-01-01', end='2099-12-31', status=2),
    'v_neri_old': sub(associate='neri', custom_data=dict(SCHOOL), start='2019-01-01', end='2019-12-31', status=1),
    'v_unlinked_now': sub(start='2000-01-01', end='2099-12-31'),
    'v_unlinked': sub(custom_data={'denomination': 'Palestra Olimpia '}, **OLD),
    'v_deleted': sub(associate='rossi', custom_data=dict(SCHOOL), deleted=True, start='2018-01-01', end='2018-12-31'),
    'v_grouped': sub(associate='gallo', custom_data=dict(SCHOOL), group=OWN_GROUP, **OLD),
    'v_ali': sub(custom_data={'school_name': 'ali', 'denomination': 'Ali'}, **OLD),
    'v_empty': sub(custom_data={'denomination': ''}, **OLD),
    'v_abroad_school': sub(associate='kodokan', custom_data=dict(SCHOOL), association=FOREIGN, user=FOREIGN_OWNER, **OLD),
    'v_kodokan': sub(associate='kodokan', custom_data={'denomination': 'Kodokan Roma'}, association=FOREIGN, user=FOREIGN_OWNER, **OLD),
    'v_numbers': sub(custom_data={'school_name': 7, 'tag': 'Numeri'}, association=FOREIGN, user=FOREIGN_OWNER, **OLD),
    # delete-document
    'd_own': sub(associate='rossi', start='2024-01-01', end='2024-12-31'),
    'd_grouped': sub(start='2024-01-01', end='2024-12-31', group=OWN_GROUP),
    'd_foreign': sub(start='2024-01-01', end='2024-12-31', association=FOREIGN, user=FOREIGN_OWNER),
}
S = {label: row['id'] for label, row in SUBSCRIPTIONS.items()}
FILES = {
    'privacy': {'id': next(ids), 'subscription': 'd_own', 'document': 'privacy', 'created': '2024-03-02T08:00:00Z'},
    'impersonated': {'id': next(ids), 'subscription': 'd_own', 'document': 'impersonated', 'created': '2024-03-02T09:00:00Z'},
    'grouped': {'id': next(ids), 'subscription': 'd_grouped', 'document': 'grouped', 'created': '2024-03-02T10:00:00Z'},
    'foreign': {'id': next(ids), 'subscription': 'd_foreign', 'document': 'foreign', 'created': '2024-03-02T11:00:00Z'},
}
SHARED_IDS = iter(f'b3000000-0000-4000-8000-{n:012d}' for n in range(970901, 970910))
# One document under two files (rows of an import).
FILES['shared_own'] = {'id': next(SHARED_IDS), 'subscription': 'd_own', 'document': 'shared', 'created': '2024-03-02T12:00:00Z'}
FILES['shared_grouped'] = {'id': next(SHARED_IDS), 'subscription': 'd_grouped', 'document': 'shared', 'created': '2024-03-02T13:00:00Z'}
TOKENS = {label: next(ids) for label in ('olimpia', 'expired', 'abroad', 'unnamed', 'numbers', 'ali', 'empty')}
LINKS = {
    'olimpia': {'id': next(ids), 'association': OWN, 'token': TOKENS['olimpia'], 'gym_name': 'Palestra Olimpia', 'expires': '2099-01-01T00:00:00Z'},
    'expired': {'id': next(ids), 'association': OWN, 'token': TOKENS['expired'], 'gym_name': 'Palestra Olimpia', 'expires': '2020-01-01T00:00:00Z'},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'token': TOKENS['abroad'], 'gym_name': 'Kodokan Roma', 'expires': '2099-01-01T00:00:00Z'},
    'unnamed': {'id': next(ids), 'association': OWN, 'token': TOKENS['unnamed'], 'gym_name': None, 'expires': '2099-01-01T00:00:00Z'},
    'numbers': {'id': next(ids), 'association': FOREIGN, 'token': TOKENS['numbers'], 'gym_name': 'Numeri', 'expires': '2099-01-01T00:00:00Z'},
    'ali': {'id': next(ids), 'association': OWN, 'token': TOKENS['ali'], 'gym_name': 'ALI', 'expires': '2099-01-01T00:00:00Z'},
    'empty': {'id': next(ids), 'association': OWN, 'token': TOKENS['empty'], 'gym_name': '', 'expires': '2099-01-01T00:00:00Z'},
}


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None,
        unordered=None):
    case = {'name': f'subscription-misc-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    if unordered:
        case['unordered'] = unordered
    cases.append(case)


TAX, STATUS, FEDERATION = '/subscription/calculate-tax-code', '/subscription/import/status', '/subscription/get-associations-for-federation'
GENERATE, VALIDATE = '/subscription/generate-token-link', '/subscription/validate-token-link-and-get-subscriptions'
DELETE = lambda sublabel, file_id: f'/subscription/{S.get(sublabel, sublabel)}/delete-document/{file_id}'
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
NOT_ASSOCIATION = {'detail': 'User is not a sport association'}
missing = lambda key: {'detail': f'Missing permission: association.members.{key}'}
not_allowed = lambda method: {'detail': f'Method "{method}" not allowed.'}
options = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
FORM = 'application/x-www-form-urlencoded'
BAD_UUID = ['badly formed hexadecimal UUID string']

# --- calculate-tax-code --------------------------------------------------------------------------------------------------
CONSONANTS, VOWELS, MONTHS = 'bcdfghjklmnpqrstvwxyz', 'aeiou', 'ABCDEHLMPRST'
ODD = dict(zip('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ', [1, 0, 5, 7, 9, 13, 15, 17, 19, 21] + [1, 0, 5, 7, 9, 13, 15, 17, 19, 21, 2, 4,
                                                                                             18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23]))
EVEN = {**{str(d): d for d in range(10)}, **{chr(65 + i): i for i in range(26)}}


def letters(slug, first=False):
    consonants = [c for c in slug if c in CONSONANTS]
    if first and len(consonants) > 3:
        del consonants[1]
    return ''.join((consonants[:3] + [c for c in slug if c in VOWELS][:3] + ['X'] * 3)[:3]).upper()


def tax_code(last_slug, first_slug, year, month, day, sex, place):
    code = f'{letters(last_slug)}{letters(first_slug, True)}{year % 100:02d}{MONTHS[month - 1]}{day + (40 if sex == "F" else 0):02d}{place}'
    total = sum(ODD[ch] if i % 2 == 0 else EVEN[ch] for i, ch in enumerate(code))
    return code + chr(65 + total % 26)


PERSON = {'last_name': 'Rossi', 'first_name': 'Mario', 'sex': 'M', 'born_date': '10/05/1990', 'born_city': 'Roma'}
ROSSI = {'tax_code': tax_code('rossi', 'mario', 1990, 5, 10, 'M', 'H501')}
MISSING_DATA, INVALID_DATA, UNMAPPED = {'msg': 'alcuni dati sono mancanti.'}, {'msg': 'alcuni dati non sono validi.'}, {'msg': 'Città non mappata.'}


def taxed(name, body, response, *, actor='anonymous', status=200, **extra):
    add(f'tax-{name}', actor, TAX, status, response, method='POST', body=body, **extra)


taxed('anonymous', PERSON, ROSSI)
taxed('owner', PERSON, ROSSI, actor='owner')
taxed('full-collaborator', PERSON, ROSSI, actor='full_collaborator')
taxed('athlete', PERSON, ROSSI, actor='athlete')
taxed('restricted-collaborator', PERSON, missing('read'), actor='restricted_collaborator', status=403)
taxed('admin-without-session', PERSON, ADMIN, actor='admin', status=403)
taxed('admin-impersonating-owner', PERSON, ROSSI, actor='admin', headers=ADMIN_OWNER)
taxed('owner-scoped-athlete', PERSON, ROSSI, actor='owner', headers=SCOPED)
add('tax-wrong-method', 'anonymous', TAX, 405, not_allowed('GET'))
add('tax-options', 'anonymous', TAX, 200, options('Subscription Calculate Tax Code'), method='OPTIONS')
for key in PERSON:
    taxed(f'without-{key.replace("_", "-")}', {k: v for k, v in PERSON.items() if k != key}, {'tax_code': None})
taxed('empty-body', {}, {'tax_code': None})
for key, value in (('last_name', ''), ('first_name', None), ('sex', 'X'), ('sex', ' M'), ('born_date', ''), ('born_city', None)):
    taxed(f'blank-{key.replace("_", "-")}-{"none" if value is None else repr(value).strip(chr(39)).strip() or "empty"}',
          {**PERSON, key: value}, MISSING_DATA, status=400)
taxed('sex-not-string', {**PERSON, 'sex': 1}, SERVER, status=500)
taxed('sex-not-string-after-missing-name', {**PERSON, 'sex': 1, 'last_name': None}, MISSING_DATA, status=400)
taxed('sex-lower-case', {**PERSON, 'sex': 'm'}, ROSSI)
taxed('female', {**PERSON, 'first_name': 'Maria', 'sex': 'F'}, {'tax_code': tax_code('rossi', 'maria', 1990, 5, 10, 'F', 'H501')})
taxed('iso-date', {**PERSON, 'born_date': '1990-05-10'}, ROSSI)
taxed('dashed-date', {**PERSON, 'born_date': '10-05-1990'}, ROSSI)
taxed('dotted-date', {**PERSON, 'born_date': '10.5.1990'}, ROSSI)
# YYYYMMDD with dayfirst (the first slug part is not four characters long): year, day, month.
taxed('compact-date', {**PERSON, 'born_date': '19900510'}, {'tax_code': tax_code('rossi', 'mario', 1990, 10, 5, 'M', 'H501')})
taxed('impossible-date', {**PERSON, 'born_date': '31/02/1990'}, INVALID_DATA, status=400)
taxed('month-day-swapped', {**PERSON, 'born_date': '05/13/1990'}, {'tax_code': tax_code('rossi', 'mario', 1990, 5, 13, 'M', 'H501')})
taxed('symbols-only-date', {**PERSON, 'born_date': '//'}, INVALID_DATA, status=400)
taxed('names-with-accents', {**PERSON, 'last_name': "D'Angelo", 'first_name': 'Nicolò'},
      {'tax_code': tax_code('d-angelo', 'nicolo', 1990, 5, 10, 'M', 'H501')})
taxed('long-first-name', {**PERSON, 'first_name': 'Gianfranco'}, {'tax_code': tax_code('rossi', 'gianfranco', 1990, 5, 10, 'M', 'H501')})
taxed('short-names', {**PERSON, 'last_name': 'Fo', 'first_name': 'Al'}, {'tax_code': tax_code('fo', 'al', 1990, 5, 10, 'M', 'H501')})
taxed('city-with-province', {**PERSON, 'born_city': 'Milano (MI)'}, {'tax_code': tax_code('rossi', 'mario', 1990, 5, 10, 'M', 'F205')})
taxed('city-with-comma', {**PERSON, 'born_city': 'verona, VR'}, {'tax_code': tax_code('rossi', 'mario', 1990, 5, 10, 'M', 'L781')})
taxed('city-wrong-province', {**PERSON, 'born_city': 'Verona (MI)'}, {'tax_code': tax_code('rossi', 'mario', 1990, 5, 10, 'M', 'L781')})
taxed('foreign-country', {**PERSON, 'born_city': 'Germania'}, {'tax_code': tax_code('rossi', 'mario', 1990, 5, 10, 'M', 'Z112')})
taxed('city-code', {**PERSON, 'born_city': 'h501'}, ROSSI)
taxed('unknown-city', {**PERSON, 'born_city': 'Atlantide'}, UNMAPPED, status=400)
taxed('city-before-creation', {**PERSON, 'born_date': '01/01/1880'}, UNMAPPED, status=400)
taxed('deleted-municipality', {**PERSON, 'born_city': 'Busana', 'born_date': '01/01/1950'},
      {'tax_code': tax_code('rossi', 'mario', 1950, 1, 1, 'M', 'B283')})
taxed('deleted-municipality-decoded-later', {**PERSON, 'born_city': 'Busana', 'born_date': '01/01/1920'}, INVALID_DATA, status=400)
taxed('last-name-not-string', {**PERSON, 'last_name': 5}, INVALID_DATA, status=400)
taxed('born-date-not-string', {**PERSON, 'born_date': 1990}, INVALID_DATA, status=400)
taxed('born-city-not-string', {**PERSON, 'born_city': ['Roma']}, INVALID_DATA, status=400)
taxed('list-body', ['Rossi'], SERVER, status=500)
taxed('form-body', None, ROSSI, raw_body='last_name=Rossi&first_name=Mario&sex=M&born_date=10%2F05%2F1990&born_city=Roma',
      content_type=FORM)

# --- import/status -------------------------------------------------------------------------------------------------------
add('status-owner', 'owner', STATUS, 200, {'importing': True})
add('status-full-collaborator', 'full_collaborator', STATUS, 200, {'importing': True})
add('status-admin-impersonating-owner', 'admin', STATUS, 200, {'importing': True}, headers=ADMIN_OWNER)
add('status-foreign-owner', 'foreign_owner', STATUS, 200, {'importing': None})
add('status-athlete', 'athlete', STATUS, 403, NOT_ASSOCIATION)
add('status-owner-without-association', 'owner_without_association', STATUS, 500, SERVER)
add('status-deleted-association', 'deleted_association_owner', STATUS, 500, SERVER)
add('status-owner-scoped-athlete', 'owner', STATUS, 403, SCOPED_DENIED, headers=SCOPED)
add('status-restricted-collaborator', 'restricted_collaborator', STATUS, 403, missing('read'))
add('status-admin-without-session', 'admin', STATUS, 403, ADMIN)
add('status-anonymous', 'anonymous', STATUS, 401, ANONYMOUS)
add('status-wrong-method', 'owner', STATUS, 405, not_allowed('POST'), method='POST', body={})
add('status-options', 'owner', STATUS, 200, options('Subscription Import Status'), method='OPTIONS')

# --- get-associations-for-federation -------------------------------------------------------------------------------------
ALPHA = {'denomination': 'ASD Alpha', 'id': [S['fed_asd'], S['fed_asd_twin']]}


def federated(*labels):
    items = []
    for label in labels:
        value = SUBSCRIPTIONS[label]['custom_data']['denomination']
        items.append({'denomination': value, 'id': S[label]})
    return items


OWN_FEDERATION = [ALPHA, *federated('fed_school', 'fed_upper', 'fed_nested', 'fed_number', 'fed_null', 'fed_deleted', 'fed_grouped')]
add('federation-owner', 'owner', FEDERATION, 200, OWN_FEDERATION, unordered='denomination')
add('federation-restricted-collaborator', 'restricted_collaborator', FEDERATION, 200, OWN_FEDERATION, unordered='denomination')
add('federation-admin-impersonating-owner', 'admin', FEDERATION, 200, OWN_FEDERATION, headers=ADMIN_OWNER, unordered='denomination')
add('federation-own-group', 'owner', FEDERATION, 200, federated('fed_grouped'), headers={'X-Group-Id': OWN_GROUP})
add('federation-foreign-group', 'owner', FEDERATION, 200, [], headers={'X-Group-Id': FOREIGN_GROUP})
add('federation-malformed-group', 'owner', FEDERATION, 200, OWN_FEDERATION, headers={'X-Group-Id': 'not-a-uuid'}, unordered='denomination')
add('federation-foreign-owner', 'foreign_owner', FEDERATION, 200, federated('fed_foreign'))
add('federation-athlete', 'athlete', FEDERATION, 403, NOT_ASSOCIATION)
add('federation-owner-without-association', 'owner_without_association', FEDERATION, 500, SERVER)
add('federation-deleted-association', 'deleted_association_owner', FEDERATION, 500, SERVER)
add('federation-admin-without-session', 'admin', FEDERATION, 403, ADMIN)
add('federation-anonymous', 'anonymous', FEDERATION, 401, ANONYMOUS)
add('federation-wrong-method', 'owner', FEDERATION, 405, not_allowed('POST'), method='POST', body={})
add('federation-options', 'owner', FEDERATION, 200, options('Get Associations For Federation'), method='OPTIONS')

# --- generate-token-link -------------------------------------------------------------------------------------------------


def generated(name, actor, gym, stored, *, headers=None):
    add(f'generate-{name}', actor, GENERATE, 201, {'token': '$new', 'gym_name': gym, 'expires_at': '$later'}, method='POST',
        body={'gym_name': gym}, headers=headers, effect={'kind': 'link', 'gym_name': stored, 'association': OWN})


generated('owner', 'owner', 'Palestra Nuova', 'Palestra Nuova')
generated('full-collaborator', 'full_collaborator', 'Dojo', 'Dojo')
generated('restricted-collaborator', 'restricted_collaborator', 'Dojo Ristretto', 'Dojo Ristretto')
generated('admin-impersonating-owner', 'admin', 'Dojo Admin', 'Dojo Admin', headers=ADMIN_OWNER)
generated('number', 'owner', 5, '5')
def multipart(*parts, boundary='submiscboundary'):
    """parts: (name, filename or None, content type or None, text) as a multipart/form-data body."""
    chunks = []
    for name, filename, content_type, text in parts:
        disposition = f'form-data; name="{name}"' + (f'; filename="{filename}"' if filename is not None else '')
        chunks.append(f'--{boundary}\r\nContent-Disposition: {disposition}\r\n'
                      + (f'Content-Type: {content_type}\r\n' if content_type else '') + f'\r\n{text}\r\n')
    return ''.join(chunks) + f'--{boundary}--\r\n', f'multipart/form-data; boundary={boundary}'


# A file in gym_name: the row stores str(file) (its name) and the response echoes the in-memory file, which the
# renderer turns into its decoded lines.
for label, text, lines in (('lines', 'uno\ndue\r\ntre\rquattro', ['uno\n', 'due\r\n', 'tre\r', 'quattro']),
                           ('empty', '', []), ('accents', 'caff\u00e8\n', ['caff\u00e8\n'])):
    body, content_type = multipart(('gym_name', 'palestra.txt', 'text/plain', text))
    add(f'generate-file-{label}', 'owner', GENERATE, 201, {'token': '$new', 'gym_name': lines, 'expires_at': '$later'}, method='POST',
        raw_body=body, content_type=content_type, effect={'kind': 'link', 'gym_name': 'palestra.txt', 'association': OWN})
body, content_type = multipart(('gym_name', 'a/b/../percorso.csv', 'text/csv', 'x'))
add('generate-file-path-name', 'owner', GENERATE, 201, {'token': '$new', 'gym_name': ['x'], 'expires_at': '$later'}, method='POST',
    raw_body=body, content_type=content_type, effect={'kind': 'link', 'gym_name': 'percorso.csv', 'association': OWN})
body, content_type = multipart(('gym_name', 'bin.dat', 'application/octet-stream', 'x'), ('gym_name', None, None, 'dopo'))
add('generate-file-then-text', 'owner', GENERATE, 201, {'token': '$new', 'gym_name': 'dopo', 'expires_at': '$later'}, method='POST',
    raw_body=body, content_type=content_type, effect={'kind': 'link', 'gym_name': 'dopo', 'association': OWN})
body, content_type = multipart(('other', 'a.txt', 'text/plain', 'x'))
add('generate-file-other-key', 'owner', GENERATE, 400, {'error': 'gym_name is required'}, method='POST', raw_body=body,
    content_type=content_type)
body, content_type = multipart(('gym_name', 'palestra.txt', 'text/plain', 'x'))
add('generate-file-athlete', 'athlete', GENERATE, 403, NOT_ASSOCIATION, method='POST', raw_body=body, content_type=content_type)

generated('float', 'owner', 1.5, '1.5')
generated('boolean', 'owner', True, 'True')
generated('object', 'owner', {'a': [1, None]}, "{'a': [1, None]}")
generated('empty', 'owner', '', '')
generated('unicode', 'owner', 'Palestra è ok', 'Palestra è ok')
generated('max-length', 'owner', 'x' * 255, 'x' * 255)
add('generate-form-last-value', 'owner', GENERATE, 201, {'token': '$new', 'gym_name': 'Ultima', 'expires_at': '$later'}, method='POST',
    raw_body='gym_name=Prima&gym_name=Ultima', content_type=FORM, effect={'kind': 'link', 'gym_name': 'Ultima', 'association': OWN})
add('generate-missing', 'owner', GENERATE, 400, {'error': 'gym_name is required'}, method='POST', body={})
add('generate-null', 'owner', GENERATE, 400, {'error': 'gym_name is required'}, method='POST', body={'gym_name': None})
add('generate-too-long', 'owner', GENERATE, 500, SERVER, method='POST', body={'gym_name': 'x' * 256})
add('generate-nul-character', 'owner', GENERATE, 500, SERVER, method='POST', body={'gym_name': 'a\u0000b'})
add('generate-list-body', 'owner', GENERATE, 500, SERVER, method='POST', body=['gym_name'])
add('generate-athlete', 'athlete', GENERATE, 403, NOT_ASSOCIATION, method='POST', body={'gym_name': 'Palestra'})
add('generate-athlete-without-name', 'athlete', GENERATE, 400, {'error': 'gym_name is required'}, method='POST', body={})
add('generate-owner-without-association', 'owner_without_association', GENERATE, 500, SERVER, method='POST', body={'gym_name': 'P'})
add('generate-deleted-association', 'deleted_association_owner', GENERATE, 500, SERVER, method='POST', body={'gym_name': 'P'})
add('generate-admin-without-session', 'admin', GENERATE, 403, ADMIN, method='POST', body={'gym_name': 'P'})
add('generate-anonymous', 'anonymous', GENERATE, 401, ANONYMOUS, method='POST', body={'gym_name': 'P'})
add('generate-wrong-method', 'owner', GENERATE, 405, not_allowed('GET'))
add('generate-options', 'owner', GENERATE, 200, options('Subscription Generate Token Link'), method='OPTIONS')

# --- validate-token-link-and-get-subscriptions ---------------------------------------------------------------------------


def info_associate(label):
    row = ASSOCIATES[label]
    return {'associate_id': row['id'], **{key: row[key] for key in ASSOCIATE_KEYS}}


def payments_of(label):
    return [p for p in PAYMENTS.values() if p['associate'] == label]


def serialized(label):
    """SubscriptionSerializer: the model fields (signature_storage_key excluded, tags as objects) and the method fields."""
    row = SUBSCRIPTIONS[label]
    payments = payments_of(row['associate']) if row['associate'] else []
    total = sum(float(p['amount']) for p in payments)
    unpaid = sum(float(p['amount']) for p in payments if not p['paid'])
    today = '2026-11-09'  # every seeded period is entirely before 2026 or after 2097
    current = row['start'] is None or row['end'] is None or row['start'] <= today <= row['end']
    return {
        'subscription_id': row['id'], 'user': USERS[row['user']], 'associate': info_associate(row['associate']) if row['associate'] else None,
        'tags': [], 'all_payments_paid': all(p['paid'] for p in payments), 'payments_info': {'total': total, 'to_be_paid': unpaid},
        'age': 0 if row['associate'] else None, 'current_year': current,
        'next_years': row['start'] is not None and row['end'] is not None and today < row['start'],
        'medical': None, 'medical_token': None, 'medical_expiration_date': None, 'document': None, 'document_token': None,
        'signature_present': False, 'custom_data': row['custom_data'], 'additional_fields': None, 'type': row['type'], 'role': row['role'],
        'acceptance_date': None, 'resignation_date': None, 'status_flag': row['status'],
        'creation_date': row['created'], 'start_date': row['start'], 'end_date': row['end'], 'signature_url': None,
        'archived': False, 'draft': True, 'trial': False, 'meta': None, 'subscription_number': '0', 'subscription_type': None,
        'deleted': row['deleted'], 'competitive': False, 'notes': None, 'sport_association': row['association'], 'supplier': None,
        'instructor': None, 'document_pdf': None, 'payment': None, 'group': row['group'],
    }


def default_from(custom):
    return {'school_name': custom['school_name'], 'school_teacher_name': custom.get('school_teacher_name', ''),
            'responsible_instructor_name': custom.get('responsible_instructor_name', ''),
            'principal_style': custom.get('principal_style', ''), 'type_of_associate': 'atleta',
            'denomination': custom.get('denomination', ''), 'type': 2}


def matched(labels, default):
    return {'expired': False, 'subscriptions': [serialized(label) for label in labels], 'default_custom_data': default}


EXPIRED = {'expired': True}
OLIMPIA = matched(('v_olimpia', 'v_olimpya', 'v_neri_old', 'v_unlinked', 'v_deleted', 'v_grouped'), default_from(SCHOOL))
validate = lambda token: f'{VALIDATE}?token={token}'
# The first request removes the expired link (every request deletes expired links before the lookup).
add('validate-unknown-token-removes-expired', 'anonymous', validate(UNKNOWN), 200, EXPIRED, effect={'kind': 'expired-links'})
add('validate-expired-token', 'anonymous', validate(TOKENS['expired']), 200, EXPIRED)
add('validate-anonymous', 'anonymous', validate(TOKENS['olimpia']), 200, OLIMPIA, unordered='subscription_id')
add('validate-owner', 'owner', validate(TOKENS['olimpia']), 200, OLIMPIA, unordered='subscription_id')
add('validate-athlete', 'athlete', validate(TOKENS['olimpia']), 200, OLIMPIA, unordered='subscription_id')
add('validate-restricted-collaborator', 'restricted_collaborator', validate(TOKENS['olimpia']), 200, OLIMPIA, unordered='subscription_id')
add('validate-own-group', 'anonymous', validate(TOKENS['olimpia']), 200, matched(('v_grouped',), default_from(SCHOOL)),
    headers={'X-Group-Id': OWN_GROUP})
add('validate-foreign-group', 'anonymous', validate(TOKENS['olimpia']), 200, matched((), None), headers={'X-Group-Id': FOREIGN_GROUP})
add('validate-foreign-association', 'anonymous', validate(TOKENS['abroad']), 200, matched(('v_kodokan',), None))
add('validate-short-name', 'anonymous', validate(TOKENS['ali']), 200, matched(('v_ali',), default_from(SUBSCRIPTIONS['v_ali']['custom_data'])))
add('validate-empty-name', 'anonymous', validate(TOKENS['empty']), 200, matched(('v_empty',), None))
add('validate-upper-case-token', 'anonymous', validate(TOKENS['olimpia'].upper()), 200, EXPIRED)
add('validate-braced-token', 'anonymous', validate('{' + TOKENS['olimpia'] + '}'), 200, EXPIRED)
add('validate-repeated-token', 'anonymous', f'{VALIDATE}?token=bad&token={TOKENS["abroad"]}', 200, matched(('v_kodokan',), None))
add('validate-null-gym-name', 'anonymous', validate(TOKENS['unnamed']), 500, SERVER)
add('validate-number-school-name', 'anonymous', validate(TOKENS['numbers']), 500, SERVER)

# ---------------------------------------------------------------------------
# Unknown tokens while a pairing is active (instance.sso.public_links.source_token)
# ---------------------------------------------------------------------------
# A canonical token no local row holds is asked of the paired server: POST <authority>/pairing/v1/public-token with the
# kind, the token, the resource, the remote pairing id and the pairing secret. The answer must carry the protocol
# version, the pairing's binding, the kind, a canonical generation and an aware expires_at in the future.
PAIR = {'instance_id': 'a1000000-0000-4000-8000-0000e1880001', 'pairing_id': 'a1000000-0000-4000-8000-0000e1880002',
        'remote_pairing_id': 'a1000000-0000-4000-8000-0000e1880003', 'generation': 'a1000000-0000-4000-8000-0000e1880004'}
PAIR_ORIGIN = 'http://localhost'


def grant(kind, **values):
    return {'protocol': 'bakney-pairing-v1', 'instance_id': PAIR['instance_id'], 'pairing_id': PAIR['remote_pairing_id'],
            'association_id': OWN, 'origin': PAIR_ORIGIN, 'callback_uri': PAIR_ORIGIN + '/bakney/v1/callback', 'kind': kind,
            'generation': PAIR['generation'], 'expires_at': '$iso+600', **values}


def paired(request=None, reply=None, *, state='paired', **row):
    """The case's pairing; request/reply: the one exchange with the paired server (none expected otherwise)."""
    authority = [] if request is None else [{'path': '/pairing/v1/public-token', 'reply': reply,
                                             'body': {**request, 'pairing_id': PAIR['remote_pairing_id'], 'secret': '$secret'}}]
    return {**PAIR, 'state': state, **row, 'authority': authority}


def ok(body):
    return {'status': 200, 'json': body}


PAIR_TOKEN = 'a1000000-0000-4000-8000-0000e1880020'
GYM_OLIMPIA = LINKS['olimpia']['gym_name']
ASK = {'kind': 'cumulative', 'token': PAIR_TOKEN}


def paired_link(name, response, pairing, *, token=PAIR_TOKEN, actor='anonymous', headers=None, **options):
    add(name, actor, validate(token), 200, response, headers=headers, **options)
    cases[-1]['pairing'] = pairing


paired_link('validate-paired-server-vouches', OLIMPIA, paired(ASK, ok(grant('cumulative', gym_name=GYM_OLIMPIA))), unordered='subscription_id')
paired_link('validate-pending-pairing-vouches-group', matched(('v_grouped',), default_from(SCHOOL)),
            paired(ASK, ok(grant('cumulative', gym_name=GYM_OLIMPIA)), state='pending'), headers={'X-Group-Id': OWN_GROUP})
paired_link('validate-paired-server-rejects', EXPIRED, paired(ASK, {'status': 401, 'json': {'error': 'unknown'}}))
paired_link('validate-paired-server-unavailable', EXPIRED, paired(ASK, {'status': 500, 'raw': 'error'}))
paired_link('validate-paired-server-card-kind', EXPIRED, paired(ASK, ok(grant('card', gym_name=GYM_OLIMPIA))))
paired_link('validate-paired-server-without-gym-name', EXPIRED, paired(ASK, ok(grant('cumulative'))))
paired_link('validate-paired-server-gym-name-number', EXPIRED, paired(ASK, ok(grant('cumulative', gym_name=7))))
paired_link('validate-paired-server-gym-name-too-long', EXPIRED, paired(ASK, ok(grant('cumulative', gym_name='O' * 256))))
paired_link('validate-paired-server-expired-grant', EXPIRED, paired(ASK, ok(grant('cumulative', gym_name=GYM_OLIMPIA, expires_at='$iso-60'))))
paired_link('validate-paired-server-other-association', EXPIRED,
            paired(ASK, ok({**grant('cumulative', gym_name=GYM_OLIMPIA), 'association_id': FOREIGN})))
paired_link('validate-paired-upper-case-token-not-asked', EXPIRED, paired(), token=PAIR_TOKEN.upper())
paired_link('validate-pairing-disconnected-not-asked', EXPIRED, paired(state='disconnected'))
paired_link('validate-pairing-not-self-hosted-not-asked', EXPIRED, paired(self_hosted=False))
paired_link('validate-paired-local-token-not-asked', matched(('v_kodokan',), None), paired(), token=TOKENS['abroad'])

add('validate-without-token', 'anonymous', VALIDATE, 400, ['one of the hex, bytes, bytes_le, fields, or int arguments must be given'])
add('validate-malformed-token', 'anonymous', validate('abc'), 400, BAD_UUID)
add('validate-admin-without-session', 'admin', validate(TOKENS['olimpia']), 403, ADMIN)
add('validate-wrong-method', 'anonymous', validate(TOKENS['olimpia']), 405, not_allowed('POST'), method='POST', body={})
add('validate-options', 'anonymous', VALIDATE, 200, options('Validate Token Link And Get Subscriptions'), method='OPTIONS')

# --- delete-document -----------------------------------------------------------------------------------------------------
DELETED = {'msg': 'subscription file deleted'}
F = {label: row['id'] for label, row in FILES.items()}
add('delete-anonymous', 'anonymous', DELETE('d_own', F['privacy']), 401, ANONYMOUS, method='DELETE')
add('delete-admin-without-session', 'admin', DELETE('d_own', F['privacy']), 403, ADMIN, method='DELETE')
add('delete-restricted-collaborator', 'restricted_collaborator', DELETE('d_own', F['privacy']), 403, missing('update'), method='DELETE')
add('delete-owner-scoped-athlete', 'owner', DELETE('d_own', F['privacy']), 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'}, method='DELETE', headers=SCOPED)
add('delete-wrong-method', 'owner', DELETE('d_own', F['privacy']), 405, not_allowed('GET'))
add('delete-options', 'owner', DELETE('d_own', F['privacy']), 200, options('Subscription Delete Document'), method='OPTIONS')
add('delete-malformed-subscription', 'owner', DELETE('nope', F['privacy']), 400, BAD_UUID, method='DELETE')
add('delete-malformed-file', 'owner', DELETE('d_own', 'nope'), 400, BAD_UUID, method='DELETE')
add('delete-athlete', 'athlete', DELETE('d_own', F['privacy']), 403, {'detail': 'User not allowed.'}, method='DELETE')
add('delete-owner-without-association', 'owner_without_association', DELETE('d_own', F['privacy']), 403, {'detail': 'User not allowed.'},
    method='DELETE')
add('delete-deleted-association', 'deleted_association_owner', DELETE('d_own', F['privacy']), 403, {'detail': 'User not allowed.'},
    method='DELETE')
add('delete-unknown-subscription', 'owner', DELETE(UNKNOWN, F['privacy']), 500, SERVER, method='DELETE')
add('delete-foreign-subscription', 'owner', DELETE('d_foreign', F['foreign']), 500, SERVER, method='DELETE')
add('delete-unknown-file', 'owner', DELETE('d_own', UNKNOWN), 500, SERVER, method='DELETE')
add('delete-file-of-another-subscription', 'owner', DELETE('d_own', F['grouped']), 500, SERVER, method='DELETE')
add('delete-group-hidden', 'owner', DELETE('d_grouped', F['grouped']), 500, SERVER, method='DELETE', headers={'X-Group-Id': FOREIGN_GROUP})
add('delete-owner', 'owner', DELETE('d_own', F['privacy']), 200, DELETED, method='DELETE',
    effect={'kind': 'document', 'file': 'privacy', 'document': True, 'actor': 'owner', 'association': OWN})
add('delete-owner-again', 'owner', DELETE('d_own', F['privacy']), 500, SERVER, method='DELETE')
add('delete-association-scoped-session', 'owner', DELETE('d_own', F['impersonated']), 200, DELETED, method='DELETE', headers=OWNER_FULL,
    effect={'kind': 'document', 'file': 'impersonated', 'document': False, 'actor': 'owner', 'association': OWN})
add('delete-admin-impersonating-owner-grouped', 'admin', DELETE('d_grouped', F['grouped']), 200, DELETED, method='DELETE',
    headers={**ADMIN_OWNER, 'X-Group-Id': OWN_GROUP},
    effect={'kind': 'document', 'file': 'grouped', 'document': True, 'actor': 'admin', 'association': IDS['admin_association']})
# A document another file references stays with it: only the caller's link goes.
add('delete-document-of-two-files', 'owner', DELETE('d_own', F['shared_own']), 200, DELETED, method='DELETE',
    effect={'kind': 'document', 'file': 'shared_own', 'document': False, 'actor': 'owner', 'association': OWN})
add('delete-document-of-two-files-again', 'owner', DELETE('d_own', F['shared_own']), 500, SERVER, method='DELETE')
# The last link takes the document with it.
add('delete-document-of-two-files-last-link', 'owner', DELETE('d_grouped', F['shared_grouped']), 200, DELETED, method='DELETE',
    effect={'kind': 'document', 'file': 'shared_grouped', 'document': True, 'actor': 'owner', 'association': OWN})

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'subscription-misc', 'ids': IDS, 'users': USERS, 'statuses': STATUSES,
                              'associates': ASSOCIATES, 'payments': PAYMENTS, 'documents': DOCUMENTS, 'subscriptions': SUBSCRIPTIONS,
                              'files': FILES, 'links': LINKS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
