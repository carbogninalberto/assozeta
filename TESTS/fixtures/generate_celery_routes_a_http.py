"""Author the subscription/<uid>/update and medical-certificate/send-email-reminder HTTP sequence (celery-routes-a.md).

subscription_update (@api_view(['PATCH']), subscriptions_views.py): is_valid_uuid
(400 list). Athletes: scoped_queryset(Subscription.objects).filter(user=request.user,
subscription_id=uid) (group-aware, soft-deleted rows found) or PermissionDenied('User
not allowed.'). Others: scoped_queryset(SportAssociation.objects).get(user=...) (a
miss is a 500), the scoped group-aware subscription or PermissionDenied('Subscription
not found.'), another association's row PermissionDenied('User not allowed.'). Then
request.data (a malformed body is the parse error, after the lookups) and, in order:
start_date / end_date (when not None) through check_date (a string matching
^\\d{4}-\\d{2}-\\d{2}$, otherwise ValidationError('Invalid <key> format'), a list),
stored back stripped and capitalized; type / role int() (blank: 2 / 1); str().strip() of
subscription_number and subscription_type. Each converted value is written back into
request.data: a form body's immutable QueryDict raises there (500, nothing saved).
SubscriptionService.update_subscription assigns the raw values of type, role,
start_date, end_date, acceptance_date, resignation_date, subscription_number,
subscription_type, custom_data, additional_fields, status_flag and notes, clears
document_pdf and calls Subscription.save() (missing dates from the owner's settings).
auditlog's pre_save entry renders the in-memory values (smart_str; JSON fields as
json.dumps(sort_keys=True)) and is committed before the UPDATE: a value the field or
PostgreSQL rejects is a 500 with the entry kept. After the autocommitted save:
print_document_subscription.delay(str(subscription_id), request.headers['authorization']).

subscription_medical_certificate_send_email_reminder (@api_view(['POST'])):
is_valid_uuid, _medical_certificate_targets (impersonated association, the athlete's
own subscriptions, or the owner's live associations; group-aware, soft-deleted rows
found) or 404 {'exception': 'Subscription not found'}; no certificate, no associate or
no expiration date is a 500. The recipient is the associate's email when not None
(an empty string included), else the main tutor's (primary relation) when not None,
else the subscription user's. Then send_email_template.delay(recipient_list=[...],
subject='[<denomination>] Ricordati di rinnovare il tuo certificato',
template='email/account/email_subscription_medical_certificate_expiring.html',
data={...}, sport_association_id=UUID). Nothing is written. Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('celery_routes_a_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER = IDS['owner']
ids = iter(f'a1000000-0000-4000-8000-{680001 + n:012d}' for n in range(300))
UNKNOWN = 'a1000000-0000-4000-8000-000000689999'
SERVER = '<stable-server-error-page>'
AUTH = '$authorization'

DOCUMENTS = {label: {'id': next(ids), 'token': next(ids), 'filename': f'{label}.pdf'} for label in ('full', 'json', 'kept')}


def associate(first, last, email=None, association=OWN):
    return {'id': next(ids), 'association': association, 'first': first, 'last': last, 'email': email}


ASSOCIATES = {
    'mario': associate('Mario', 'Rossi', 'mario.rossi@example.test'),
    'luca': associate('Luca', 'Bianchi'),
    'tutor': associate('Anna', 'Bianchi', 'anna.tutor@example.test'),
    'sara': associate('Sara', 'Verdi'),
    'notutor_email': associate('Pia', 'Neri'),
    'silent_tutor': associate('Ugo', 'Neri'),
    'empty': associate('Ivo', 'Gialli', ''),
    'quoted': associate("D'Arco", 'Ünal', 'darco@example.test'),
    'hans': associate('Hans', 'Muller', 'hans@example.test', FOREIGN),
    'late': associate('Lia', 'Neri'),
    'late_two': associate('Lea', 'Neri'),
}
# Primary tutor relations: luca -> tutor (with email), notutor_email -> silent_tutor (without email).
RELATIONS = [('luca', 'tutor', True), ('notutor_email', 'silent_tutor', True), ('late', 'tutor', False),
             ('late_two', 'silent_tutor', False), ('late_two', 'tutor', False)]

CERTIFICATES = {label: {'id': next(ids), 'user': 'owner', 'expiration': expiration}
                for label, expiration in (('main', '2026-06-30'), ('tutor', '2027-01-05'), ('user', '2026-12-31'),
                                          ('empty', '2026-03-01'), ('quoted', '2026-04-15'), ('silent', '2026-05-20'),
                                          ('none', None), ('orphan', '2026-07-01'), ('athlete', '2026-08-08'), ('abroad', '2026-09-09'),
                                          ('grouped', '2026-10-10'), ('deleted', '2026-11-11'), ('late', '2027-02-02'), ('late_two', '2027-03-03'))}


def subscription(associate='mario', association=OWN, **fields):
    row = {'id': next(ids), 'association': association, 'user': 'owner' if association == OWN else 'foreign_owner',
           'associate': associate, 'status': 2, 'type': 2, 'role': 1, 'start': '2026-01-01', 'end': '2026-12-31',
           'acceptance': None, 'resignation': None, 'number': '0', 'kind': None, 'custom': None, 'additional': None,
           'notes': None, 'document': None, 'medical': None, 'group': None, 'deleted': False, 'created': '2026-01-10T09:00:00Z'}
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    # update targets (one per success case)
    'full': subscription(document='full', custom={'z': 1}, notes='Vecchia nota'),
    'noop': subscription(),
    'blank_type': subscription(type=3, role=4),
    'bool_type': subscription(),
    'refill': subscription(start='2026-03-01', end='2026-10-31'),
    'bad_date': subscription(),
    'bad_int': subscription(),
    'negative': subscription(),
    'big_status': subscription(),
    'string_status': subscription(),
    'bool_status': subscription(),
    'bad_acceptance': subscription(),
    'int_acceptance': subscription(),
    'dates_basic': subscription(), 'dates_short': subscription(), 'dates_unicode': subscription(), 'dates_week': subscription(),
    'nul_text': subscription(), 'surrogate_text': subscription(), 'nul_key': subscription(), 'surrogate_json': subscription(),
    'dates_newline': subscription(), 'dates_space': subscription(), 'dates_month': subscription(), 'dates_start': subscription(),
    'stringified': subscription(),
    'long_kind': subscription(),
    'clear_json': subscription(document='json', custom={'b': [1, 2.5], 'a': 'è'}, additional={'k': True}),
    'infinite_json': subscription(),
    'list_body': subscription(document='kept'),
    'form': subscription(),
    'athlete_own': subscription(user='athlete'),
    'grouped': subscription(group='own_group'),
    'flagged': subscription(deleted=True),
    'collaborator': subscription(),
    'admin': subscription(),
    'stripped': subscription(),
    'abroad': subscription('hans', FOREIGN),
    # reminder targets
    'r_main': subscription(medical='main'),
    'r_tutor': subscription('luca', medical='tutor'),
    'r_user': subscription('sara', medical='user'),
    'r_empty': subscription('empty', medical='empty'),
    'r_quoted': subscription('quoted', medical='quoted'),
    'r_silent': subscription('notutor_email', medical='silent'),
    'r_no_certificate': subscription(),
    'r_no_expiration': subscription(medical='none'),
    'r_no_associate': subscription(None, medical='orphan'),
    'r_athlete': subscription('sara', user='athlete', medical='athlete'),
    'r_abroad': subscription('hans', FOREIGN, medical='abroad'),
    'r_grouped': subscription(group='own_group', medical='grouped'),
    'r_deleted': subscription(deleted=True, medical='deleted'),
    'r_promote': subscription('late', medical='late'),
    'r_promote_first': subscription('late_two', medical='late_two'),
}
cases = []


def add(name, actor, path, status, response, *, method='PATCH', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'celery-routes-a-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def sid(label):
    return SUBSCRIPTIONS[label]['id'] if label in SUBSCRIPTIONS else label


def update(label):
    return f'/subscription/{sid(label)}/update'


def reminder(label):
    return f'/subscription/{sid(label)}/medical-certificate/send-email-reminder'


def printed(label):
    return [{'task': 'print_document_subscription', 'args': [sid(label), AUTH], 'kwargs': []}]


def updated(label, actor='owner', columns=None, audit=None, *, kept=False):
    """The subscription's saved columns and its update entry (None: no entry); kept: the
    entry is committed and the save fails (500, nothing published)."""
    row = SUBSCRIPTIONS[label]
    return {'kind': 'update', 'actor': actor, 'subscription': label, 'columns': {} if kept else (columns or {}), 'audit': audit,
            'index': [row['association'], 'direct'], 'celery': [] if kept else printed(label)}


UPDATED = {'data': {'msg': 'subscription updated.'}}

# --- subscription/<uid>/update ---------------------------------------------------------------
full = SUBSCRIPTIONS['full']
add('full-update', 'owner', update('full'), 200, UPDATED, body={
    'start_date': '2026-02-01', 'end_date': '2026-11-30', 'type': '3', 'role': 2, 'subscription_number': ' 12 ',
    'subscription_type': ' Ordinaria ', 'acceptance_date': '2026-02-03', 'resignation_date': None,
    'custom_data': {'b': [1, 2.50, None], 'a': 'è'}, 'additional_fields': [], 'status_flag': 4, 'notes': 'Nuova nota'},
    effect=updated('full', columns={'start_date': '2026-02-01', 'end_date': '2026-11-30', 'type': 3, 'role': 2,
                                    'subscription_number': '12', 'subscription_type': 'Ordinaria', 'acceptance_date': '2026-02-03',
                                    'custom_data': {'b': [1, 2.5, None], 'a': 'è'}, 'additional_fields': [], 'status_flag': 4,
                                    'notes': 'Nuova nota', 'document_pdf_id': None},
                   audit={'start_date': ['2026-01-01', '2026-02-01'], 'end_date': ['2026-12-31', '2026-11-30'], 'type': ['2', '3'],
                          'role': ['1', '2'], 'subscription_number': ['0', '12'], 'subscription_type': ['None', 'Ordinaria'],
                          'acceptance_date': ['None', '2026-02-03'], 'custom_data': ['{"z": 1}', '{"a": "\\u00e8", "b": [1, 2.5, null]}'],
                          'additional_fields': ['null', '[]'], 'status_flag': ['2', '4'], 'notes': ['Vecchia nota', 'Nuova nota'],
                          'document_pdf': [DOCUMENTS['full']['id'], 'None']}))
add('empty-body-saves', 'owner', update('noop'), 200, UPDATED, body={}, effect=updated('noop'))
add('blank-type-and-role-defaults', 'owner', update('blank_type'), 200, UPDATED, body={'type': '', 'role': ''},
    effect=updated('blank_type', columns={'type': 2, 'role': 1}, audit={'type': ['3', '2'], 'role': ['4', '1']}))
add('bool-and-float-int', 'owner', update('bool_type'), 200, UPDATED, body={'type': True, 'role': 5.9, 'subscription_number': 7},
    effect=updated('bool_type', columns={'type': 1, 'role': 5, 'subscription_number': '7'},
                   audit={'type': ['2', '1'], 'role': ['1', '5'], 'subscription_number': ['0', '7']}))
add('null-start-refilled', 'owner', update('refill'), 200, UPDATED, body={'start_date': None, 'end_date': '2026-10-31\n'},
    effect=updated('refill', columns={'start_date': '2026-01-01'}, audit={'start_date': ['2026-03-01', '2026-01-01']}))
add('invalid-start-date', 'owner', update('noop'), 400, ['Invalid start_date format'], body={'start_date': '01/02/2026', 'type': 'x'})
add('non-string-end-date', 'owner', update('noop'), 400, ['Invalid end_date format'], body={'start_date': '2026-01-01', 'end_date': 20260101})
add('type-not-a-number', 'owner', update('bad_int'), 500, SERVER, body={'type': '3.0', 'notes': 'x'})
add('role-list', 'owner', update('bad_int'), 500, SERVER, body={'role': [1]})
add('calendar-invalid-start-kept-entry', 'owner', update('bad_date'), 500, SERVER, body={'start_date': '2026-02-30'},
    effect=updated('bad_date', audit={'start_date': ['2026-01-01', '2026-02-30']}, kept=True))
add('negative-type-kept-entry', 'owner', update('negative'), 500, SERVER, body={'type': '-1'},
    effect=updated('negative', audit={'type': ['2', '-1']}, kept=True))
add('status-out-of-range-kept-entry', 'owner', update('big_status'), 500, SERVER, body={'status_flag': 40000, 'notes': 'n'},
    effect=updated('big_status', audit={'status_flag': ['2', '40000'], 'notes': ['None', 'n']}, kept=True))
add('string-status', 'owner', update('string_status'), 200, UPDATED, body={'status_flag': ' 4 '},
    effect=updated('string_status', columns={'status_flag': 4}, audit={'status_flag': ['2', ' 4 ']}))
add('bool-status', 'owner', update('bool_status'), 200, UPDATED, body={'status_flag': True},
    effect=updated('bool_status', columns={'status_flag': 1}, audit={'status_flag': ['2', 'True']}))
add('text-acceptance-kept-entry', 'owner', update('bad_acceptance'), 500, SERVER, body={'acceptance_date': 'domani'},
    effect=updated('bad_acceptance', audit={'acceptance_date': ['None', 'domani']}, kept=True))
add('int-resignation-kept-entry', 'owner', update('int_acceptance'), 500, SERVER, body={'resignation_date': 5},
    effect=updated('int_acceptance', audit={'resignation_date': ['None', '5']}, kept=True))
# DateField.to_python (parse_date): date.fromisoformat (basic and week forms), then date_re whose \\d and int() take any Unicode digit.
ARABIC = '\u0662\u0660\u0662\u0666-\u0660\u0662-\u0660\u0663'
for label, field, text in (('basic', 'acceptance_date', '20260203'), ('short', 'resignation_date', '2026-2-3'),
                           ('unicode', 'acceptance_date', ARABIC), ('week', 'resignation_date', '2026-W06-2'),
                           ('newline', 'acceptance_date', '2026-02-03\n')):
    add(f'date-forms-{label}', 'owner', update('dates_' + label), 200, UPDATED, body={field: text},
        effect=updated('dates_' + label, columns={field: '2026-02-03'}, audit={field: ['None', text]}))
add('date-forms-start-unicode', 'owner', update('dates_start'), 200, UPDATED, body={'start_date': ARABIC},
    effect=updated('dates_start', columns={'start_date': '2026-02-03'}, audit={'start_date': ['2026-01-01', ARABIC]}))
add('date-forms-space-kept-entry', 'owner', update('dates_space'), 500, SERVER, body={'acceptance_date': ' 2026-02-03'},
    effect=updated('dates_space', audit={'acceptance_date': ['None', ' 2026-02-03']}, kept=True))
add('date-forms-month-kept-entry', 'owner', update('dates_month'), 500, SERVER, body={'resignation_date': '2026-13-03'},
    effect=updated('dates_month', audit={'resignation_date': ['None', '2026-13-03']}, kept=True))
# PostgreSQL text refuses NUL and a lone surrogate cannot be encoded: the entry is not written and the save never runs.
add('nul-notes-nothing-saved', 'owner', update('nul_text'), 500, SERVER, body={'notes': 'a\u0000b'}, effect=None)
add('lone-surrogate-notes-nothing-saved', 'owner', update('surrogate_text'), 500, SERVER,
    raw_body='{"notes": "\\ud800"}', content_type='application/json', effect=None)
# json.dumps escapes both, so the JSON fields keep their entry and the column refuses the value.
add('nul-json-key-kept-entry', 'owner', update('nul_key'), 500, SERVER, body={'custom_data': {'a\u0000b': 1}},
    effect=updated('nul_key', audit={'custom_data': ['null', '{"a\\u0000b": 1}']}, kept=True))
add('lone-surrogate-json-kept-entry', 'owner', update('surrogate_json'), 500, SERVER,
    raw_body='{"additional_fields": ["\\ud800"]}', content_type='application/json',
    effect=updated('surrogate_json', audit={'additional_fields': ['null', '["\\ud800"]']}, kept=True))
add('stringified-values', 'owner', update('stringified'), 200, UPDATED,
    body={'notes': {'a': [1, True]}, 'subscription_type': False, 'acceptance_date': None, 'custom_data': 'testo'},
    effect=updated('stringified', columns={'notes': "{'a': [1, True]}", 'subscription_type': 'False', 'custom_data': 'testo'},
                   audit={'notes': ['None', "{'a': [1, True]}"], 'subscription_type': ['None', 'False'], 'custom_data': ['null', '"testo"']}))
add('long-subscription-type-kept-entry', 'owner', update('long_kind'), 500, SERVER, body={'subscription_type': 'x' * 256},
    effect=updated('long_kind', audit={'subscription_type': ['None', 'x' * 256]}, kept=True))
add('json-cleared', 'owner', update('clear_json'), 200, UPDATED, body={'custom_data': None, 'additional_fields': {'k': True}},
    effect=updated('clear_json', columns={'custom_data': None, 'document_pdf_id': None},
                   audit={'custom_data': ['{"a": "\\u00e8", "b": [1, 2.5]}', 'null'], 'document_pdf': [DOCUMENTS['json']['id'], 'None']}))
add('infinite-json-kept-entry', 'owner', update('infinite_json'), 500, SERVER, raw_body='{"custom_data": {"v": 1e999}}',
    content_type='application/json', effect=updated('infinite_json', audit={'custom_data': ['null', '{"v": Infinity}']}, kept=True))
# Non-dict bodies: `key in data` membership, then data[key] raises TypeError.
add('list-body-saves', 'owner', update('list_body'), 200, UPDATED, body=['other'],
    effect=updated('list_body', columns={'document_pdf_id': None}, audit={'document_pdf': [DOCUMENTS['kept']['id'], 'None']}))
add('list-body-with-key', 'owner', update('noop'), 500, SERVER, body=['notes'])
add('string-body-substring', 'owner', update('noop'), 500, SERVER, body='my notes')
add('string-body-saves', 'owner', update('noop'), 200, UPDATED, body='other', effect=updated('noop'))
add('null-body', 'owner', update('noop'), 500, SERVER, raw_body='null', content_type='application/json')
add('malformed-json', 'owner', update('noop'), 400,
    {'detail': 'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'},
    raw_body='{', content_type='application/json')
add('malformed-json-after-lookup', 'owner', update(UNKNOWN), 403, {'detail': 'Subscription not found.'}, raw_body='{',
    content_type='application/json')
# Form bodies: the QueryDict is immutable.
add('form-notes', 'owner', update('form'), 200, UPDATED, raw_body='notes=prima&notes=Nota%20form%20%C3%A8&status_flag=3',
    content_type='application/x-www-form-urlencoded',
    effect=updated('form', columns={'notes': 'Nota form è', 'status_flag': 3}, audit={'notes': ['None', 'Nota form è'], 'status_flag': ['2', '3']}))
add('form-valid-start-immutable', 'owner', update('noop'), 500, SERVER, raw_body='start_date=2026-01-01',
    content_type='application/x-www-form-urlencoded')
add('form-invalid-start', 'owner', update('noop'), 400, ['Invalid start_date format'], raw_body='start_date=ieri',
    content_type='application/x-www-form-urlencoded')
add('form-subscription-number-immutable', 'owner', update('noop'), 500, SERVER, raw_body='subscription_number=1',
    content_type='application/x-www-form-urlencoded')
add('stripped-number-and-type', 'owner', update('stripped'), 200, UPDATED, body={'subscription_number': None, 'subscription_type': ' Tipo\t'},
    effect=updated('stripped', columns={'subscription_number': None, 'subscription_type': 'Tipo'},
                   audit={'subscription_number': ['0', 'None'], 'subscription_type': ['None', 'Tipo']}))
# Lookups and actors.
add('athlete-own', 'athlete', update('athlete_own'), 200, UPDATED, body={'notes': 'Atleta'},
    effect=updated('athlete_own', 'athlete', columns={'notes': 'Atleta'}, audit={'notes': ['None', 'Atleta']}))
add('athlete-other-denied', 'athlete', update('noop'), 403, {'detail': 'User not allowed.'}, body={'notes': 'x'})
add('foreign-denied', 'owner', update('abroad'), 403, {'detail': 'User not allowed.'}, body={'notes': 'x'})
add('missing', 'owner', update(UNKNOWN), 403, {'detail': 'Subscription not found.'}, body={'notes': 'x'})
add('malformed-uid', 'owner', update('abc'), 400, ['badly formed hexadecimal UUID string'], body={'notes': 'x'})
add('other-group-not-found', 'owner', update('grouped'), 403, {'detail': 'Subscription not found.'}, body={'notes': 'x'},
    headers={'X-Group-Id': IDS['foreign_group']})
add('group-member', 'owner', update('grouped'), 200, UPDATED, body={'notes': 'G'}, headers={'X-Group-Id': IDS['own_group']},
    effect=updated('grouped', columns={'notes': 'G'}, audit={'notes': ['None', 'G']}))
add('soft-deleted-row', 'owner', update('flagged'), 200, UPDATED, body={'role': 3},
    effect=updated('flagged', columns={'role': 3}, audit={'role': ['1', '3']}))
add('full-collaborator', 'full_collaborator', update('collaborator'), 200, UPDATED, body={'notes': 'C'},
    effect=updated('collaborator', 'full_collaborator', columns={'notes': 'C'}, audit={'notes': ['None', 'C']}))
add('admin-impersonating-owner', 'admin', update('admin'), 200, UPDATED, body={'notes': 'A'},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    effect=updated('admin', 'admin', columns={'notes': 'A'}, audit={'notes': ['None', 'A']}))
add('owner-without-association-server-error', 'owner_without_association', update('noop'), 500, SERVER, body={'notes': 'x'})
# An association-scoped session checks the path uid as a subscription id (the view name starts with subscription_).
add('owner-scoped-athlete-denied', 'owner', update('noop'), 403, {'detail': 'Risorsa non disponibile per questo utente e associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}, body={'notes': 'x'})
add('restricted-missing-permission', 'restricted_collaborator', update('noop'), 403,
    {'detail': 'Missing permission: association.members.update'}, body={'notes': 'x'})
add('admin-without-session', 'admin', update('noop'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body={'notes': 'x'})
add('anonymous', 'anonymous', update('noop'), 401, {'detail': 'Authentication credentials were not provided.'}, body={'notes': 'x'})
add('wrong-method', 'owner', update('noop'), 405, {'detail': 'Method "POST" not allowed.'}, method='POST', body={'notes': 'x'})
add('options', 'owner', update('noop'), 200, {'name': 'Subscription Update', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# --- medical-certificate/send-email-reminder -------------------------------------------------
SENT = {'msg': 'Email reminder sent'}
TEMPLATE = 'email/account/email_subscription_medical_certificate_expiring.html'


def reminded(label, email, *, association=OWN, denomination='$denomination:own'):
    row = SUBSCRIPTIONS[label]
    person = ASSOCIATES[row['associate']]
    expiration = CERTIFICATES[row['medical']]['expiration']
    day = '/'.join(reversed(expiration.split('-')))
    data = {'athlete_first_name': person['first'], 'athlete_last_name': person['last'], 'sport_association': {'denomination': denomination},
            'certificate_expiring_date': day, 'app_host': '$app_url',
            'settings': {'WHITELABEL_NAME': '$whitelabel', 'IS_WHITELABEL': '$is_whitelabel'}}
    return {'kind': 'reminder', 'celery': [{'task': 'send_email_template', 'args': [], 'kwargs': [
        ['recipient_list', [email]], ['subject', f'[{denomination}] Ricordati di rinnovare il tuo certificato'], ['template', TEMPLATE],
        ['data', data], ['sport_association_id', {'uuid': association}]]}]}


add('reminder-associate-email', 'owner', reminder('r_main'), 200, SENT, method='POST', effect=reminded('r_main', 'mario.rossi@example.test'))
add('reminder-tutor-email', 'owner', reminder('r_tutor'), 200, SENT, method='POST', effect=reminded('r_tutor', 'anna.tutor@example.test'))
add('reminder-user-email', 'owner', reminder('r_user'), 200, SENT, method='POST', effect=reminded('r_user', '$email:owner'))
add('reminder-empty-associate-email', 'owner', reminder('r_empty'), 200, SENT, method='POST', effect=reminded('r_empty', ''))
add('reminder-quoted-names', 'owner', reminder('r_quoted'), 200, SENT, method='POST', effect=reminded('r_quoted', 'darco@example.test'))
add('reminder-tutor-without-email', 'owner', reminder('r_silent'), 200, SENT, method='POST', effect=reminded('r_silent', '$email:owner'))
# get_main_tutor promotes the first relation (by key) of an associate without a primary one: an audited save.
for label, spec, email in (('r_promote', {'associate': 'late', 'relations': 1, 'path': 'associate.sport_association'}, 'anna.tutor@example.test'),
                           ('r_promote_first', {'associate': 'late_two', 'relations': 2, 'path': 'associate.sport_association'}, '$email:owner')):
    promoted = reminded(label, email)
    promoted['promotion'] = spec
    add('reminder-promotes-' + label[2:], 'owner', reminder(label), 200, SENT, method='POST', effect=promoted)
add('reminder-body-ignored', 'owner', reminder('r_main'), 200, SENT, method='POST', raw_body='{', content_type='application/json',
    effect=reminded('r_main', 'mario.rossi@example.test'))
add('reminder-no-certificate', 'owner', reminder('r_no_certificate'), 500, SERVER, method='POST')
add('reminder-no-expiration', 'owner', reminder('r_no_expiration'), 500, SERVER, method='POST')
add('reminder-no-associate', 'owner', reminder('r_no_associate'), 500, SERVER, method='POST')
add('reminder-athlete-own', 'athlete', reminder('r_athlete'), 200, SENT, method='POST', effect=reminded('r_athlete', '$email:athlete'))
add('reminder-athlete-other-not-found', 'athlete', reminder('r_main'), 404, {'exception': 'Subscription not found'}, method='POST')
add('reminder-foreign-not-found', 'owner', reminder('r_abroad'), 404, {'exception': 'Subscription not found'}, method='POST')
add('reminder-foreign-owner', 'foreign_owner', reminder('r_abroad'), 200, SENT, method='POST',
    effect=reminded('r_abroad', 'hans@example.test', association=FOREIGN, denomination='$denomination:foreign'))
add('reminder-missing', 'owner', reminder(UNKNOWN), 404, {'exception': 'Subscription not found'}, method='POST')
add('reminder-malformed', 'owner', reminder('abc'), 400, ['badly formed hexadecimal UUID string'], method='POST')
add('reminder-other-group', 'owner', reminder('r_grouped'), 404, {'exception': 'Subscription not found'}, method='POST',
    headers={'X-Group-Id': IDS['foreign_group']})
add('reminder-group-member', 'full_collaborator', reminder('r_grouped'), 200, SENT, method='POST', headers={'X-Group-Id': IDS['own_group']},
    effect=reminded('r_grouped', 'mario.rossi@example.test'))
add('reminder-soft-deleted', 'owner', reminder('r_deleted'), 200, SENT, method='POST', effect=reminded('r_deleted', 'mario.rossi@example.test'))
add('reminder-admin-impersonating-owner', 'admin', reminder('r_main'), 200, SENT, method='POST',
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}, effect=reminded('r_main', 'mario.rossi@example.test'))
add('reminder-owner-without-association', 'owner_without_association', reminder('r_main'), 404, {'exception': 'Subscription not found'},
    method='POST')
add('reminder-owner-scoped-athlete-denied', 'owner', reminder('r_main'), 403,
    {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, method='POST',
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('reminder-restricted-missing-permission', 'restricted_collaborator', reminder('r_main'), 403,
    {'detail': 'Missing permission: association.members.update'}, method='POST')
add('reminder-admin-without-session', 'admin', reminder('r_main'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, method='POST')
add('reminder-anonymous', 'anonymous', reminder('r_main'), 401, {'detail': 'Authentication credentials were not provided.'}, method='POST')
add('reminder-wrong-method', 'owner', reminder('r_main'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('reminder-options', 'owner', reminder('r_main'), 200, {'name': 'Subscription Medical Certificate Send Email Reminder', 'description': '',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Subscription update and medical certificate reminder with Celery publish effects',
                              'ids': IDS, 'documents': DOCUMENTS, 'associates': ASSOCIATES, 'relations': RELATIONS,
                              'certificates': CERTIFICATES, 'subscriptions': SUBSCRIPTIONS, 'cases': cases},
                             ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
