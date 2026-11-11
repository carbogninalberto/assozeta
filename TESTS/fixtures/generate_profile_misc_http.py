"""Author the profile-misc HTTP sequence (profile-misc.md).

Four heterogeneous routes in BE/application/views/profile_views.py and
onboarding_views.py, kept in one family with separate sections:

- profile_associates_course (GET, IsAuthenticated; registry
  other.users.connectedathletes.read; an athlete view): is_valid_uuid(uid),
  then Course.objects (group-aware, not deleted).filter(course_id=uid).first()
  .sport_association (None or a course without association: 500). Associates
  (group-aware, not deleted) of request.user or of the course association's
  owner; subscribed = associates with a non-deleted CourseSubscription on the
  course; the others are listed only with a Subscription of request.user in
  the course association. 'full' is true when that list is empty.
- profile_associates_sport_association (GET, same registry key, an athlete
  view): is_valid_uuid(uid); request.user's enabled associates; Subscriptions
  of association uid created in request.user's current balance year
  (an impossible start date: 500); subscribed rows carry the lowest
  subscription_id.
- profile_image (GET, AllowAny; registry other.settings.update): is_valid_uuid,
  then User.objects (not deleted).get(user_id=uid).avatar_image as the body
  (DoesNotExist: 500).
- onboarding_update (PATCH, IsAuthenticated; registry other.settings.update):
  `k in request.data` runs before the view's try (a number body is a 500
  page); athletes get {'error': 'You are not a sport association.'}; then the
  first of sport_association / membership_data / lead_data / onboarding_data
  present. DRF ValidationError -> 400 {'error': str(e)} (the Python repr of
  the error dict); any other exception -> 500 {'error': str(e)}.
  membership_data runs in transaction.atomic() (a later exception rolls back
  the association save and its audit), validates a fresh self-host payload
  when the association is the primary association of an incomplete fresh
  InstanceConfiguration, saves the association twice (the second save stores
  float fees: auditlog records '12.50' -> '12.5'), saves the user and
  completes the configuration. lead_data and onboarding_data are autocommit.

profile_update_subscription_template is not part of this family (see the doc).
Run with ``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('profile_misc_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']


def uid(n):
    return f'a1000000-0000-4000-8000-{978000 + n:012d}'


SERVER = '<stable-server-error-page>'
ATHLETE_SEED = 'a1000000-0000-4000-8000-000000700101'  # base associate: athlete user, own association
AVATAR = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAE='

# --- seeded rows --------------------------------------------------------------------------------
# An athlete linked to both associations (a3/s3 abroad, a8 here); the shared athlete only has own links.
IDS['multi_athlete'] = uid(90)
EXTRA_USERS = {'multi_athlete': {'id': IDS['multi_athlete'], 'username': 'profile-misc-multi-athlete',
                                 'email': 'profile-misc-multi-athlete@example.test'}}
ASSOCIATES = {
    'a1': dict(id=uid(1), user='athlete', association='own', first='Mario', last='Rossi', minor=False, tax='RSSMRA80A01H501U'),
    'a2': dict(id=uid(2), user='athlete', association='own', first=None, last='Bianchi', minor=True, tax=None),
    'a3': dict(id=uid(3), user='multi_athlete', association='foreign', first='Luca', last='Verdi', minor=False, tax='VRDLCU80A01H501U'),
    'a4': dict(id=uid(4), user='athlete', association='own', first='Cancellato', last='Socio', minor=False, tax=None, deleted=True),
    'a5': dict(id=uid(5), user='owner', association='own', first='Paolo', last='Neri', minor=False, tax='NREPLA80A01H501U'),
    'a6': dict(id=uid(6), user='athlete', association='own', first='Anna', last='Gialli', minor=False, tax=None, group='own_group'),
    'a7': dict(id=uid(7), user='athlete', association='own', first='Disabilitato', last='Socio', minor=False, tax=None, disabled=True),
    'a8': dict(id=uid(8), user='multi_athlete', association='own', first='Doppio', last='Socio', minor=False, tax=None),
}
# 'created': inside / before the requesting user's current balance year (default 1 January start).
SUBSCRIPTIONS = {
    's1b': dict(id=uid(20), associate='a1', user='athlete', association='own', created='inside'),
    's1': dict(id=uid(21), associate='a1', user='athlete', association='own', created='inside'),
    's2': dict(id=uid(22), associate='a2', user='athlete', association='own', created='inside'),
    's3': dict(id=uid(23), associate='a3', user='multi_athlete', association='foreign', created='inside'),
    's4': dict(id=uid(24), associate='a4', user='athlete', association='own', created='inside'),
    's5': dict(id=uid(25), associate='a5', user='owner', association='own', created='inside'),
    's6': dict(id=uid(26), associate='a6', user='athlete', association='own', created='before', group='own_group'),
    's7': dict(id=uid(27), associate='a7', user='athlete', association='own', created='inside'),
}
COURSES = {
    'c1': dict(id=uid(40), association='own', title='Corso base'),
    'c2': dict(id=uid(41), association='own', title='Corso cancellato', deleted=True),
    'c3': dict(id=uid(42), association='foreign', title='Corso estero'),
    'c4': dict(id=uid(43), association='own', title='Corso di gruppo', group='own_group'),
}
COURSE_SUBSCRIPTIONS = {
    'cs1': dict(id=uid(60), course='c1', subscription='s1'),
    'cs2': dict(id=uid(61), course='c1', subscription='s2', deleted=True),
    'cs5': dict(id=uid(62), course='c1', subscription='s5'),
    'cs3': dict(id=uid(63), course='c3', subscription='s3'),
}
ONBOARDING = uid(80)  # the owner's UsersOnboarding row (model defaults)
INSTANCE = {'id': 978090, 'domain': 'profile-misc.example.test', 'name': 'Profile misc', 'self_hosted': True,
            'setup_provenance': 'fresh', 'primary_association': 'foreign_association'}
UNKNOWN = uid(99)
# owner_without_association: a start date datetime() rejects (profile_associates_sport_association 500).
NO_ASSOCIATION_BALANCE = {'balance_sheet_start_day': 30, 'balance_sheet_start_month': 2}

cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'profile-misc-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


def person(label, subscription=None):
    row = ASSOCIATES[label] if label in ASSOCIATES else None
    if row is None:  # the base fixture associate
        entry = {'id': ATHLETE_SEED, 'full_name': 'Athlete Fixture', 'is_minor': False}
    else:
        entry = {'id': row['id'], 'full_name': ('%s %s' % (row['first'], row['last'])).strip(), 'is_minor': row['minor']}
    if subscription:
        entry['subscription_id'] = SUBSCRIPTIONS[subscription]['id']
    return entry


ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
SCOPED_RESOURCE = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
OWN_GROUP = {'X-Group-Id': IDS['own_group']}
BAD_UUID = ['badly formed hexadecimal UUID string']
CONNECTED = {'detail': 'Missing permission: other.users.connectedathletes.read'}
SETTINGS_UPDATE = {'detail': 'Missing permission: other.settings.update'}
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}
options = lambda name, description='': {'name': name, 'description': description, 'renders': ['application/json'],
                                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}

# === profile/associates/course/<uid> ============================================================
COURSE = '/profile/associates/course/'
course = lambda label: COURSE + COURSES[label]['id']


def course_result(subscribed, unsubscribed):
    return {'associates_subscribed': subscribed, 'associates_unsubscribed': unsubscribed, 'full': len(unsubscribed) == 0}


add('course-anonymous', 'anonymous', course('c1'), 401, ANONYMOUS)
add('course-admin-without-session', 'admin', course('c1'), 403, ADMIN)
add('course-restricted-missing-permission', 'restricted_collaborator', course('c1'), 403, CONNECTED)
add('course-restricted-post-permission-first', 'restricted_collaborator', course('c1'), 403, CONNECTED, method='POST', body={})
add('course-wrong-method', 'owner', course('c1'), 405, method('POST'), method='POST', body={})
add('course-options', 'owner', course('c1'), 200, options(
    'Profile Associates Course', 'Select all the associates that are subscribed on the course and are connected to the user,\n'
    'plus return a list of possible subscriptions'), method='OPTIONS')
add('course-malformed-uid', 'athlete', COURSE + 'not-a-uuid', 400, BAD_UUID)
add('course-unknown-server-error', 'athlete', COURSE + UNKNOWN, 500, SERVER)
add('course-deleted-server-error', 'athlete', course('c2'), 500, SERVER)
# Course.objects is group-aware: c1 has no group.
add('course-other-group-server-error', 'athlete', course('c1'), 500, SERVER, headers=OWN_GROUP)
# The athlete's associates plus those of the course association's owner (a5).
ATHLETE_C1 = course_result([person('a1'), person('a5')], [person('a2', 's2'), person('a6', 's6'), person('a7', 's7')])
add('course-athlete', 'athlete', course('c1'), 200, ATHLETE_C1)
add('course-athlete-uppercase-uid', 'athlete', COURSE + COURSES['c1']['id'].upper(), 200, ATHLETE_C1)
# A foreign course: associates of the athlete and of the foreign owner; subscriptions in the foreign association.
add('course-athlete-foreign-course', 'athlete', course('c3'), 200, course_result([], []))
add('course-multi-club-athlete-foreign-course', 'multi_athlete', course('c3'), 200, course_result([person('a3')], []))
add('course-athlete-group', 'athlete', course('c4'), 200, course_result([], [person('a6', 's6')]), headers=OWN_GROUP)
OWNER_C1 = course_result([person('a5')], [])
add('course-owner', 'owner', course('c1'), 200, OWNER_C1)
add('course-full-collaborator', 'full_collaborator', course('c1'), 200, OWNER_C1)
add('course-admin-impersonating-owner', 'admin', course('c1'), 200, OWNER_C1, headers=ADMIN_OWNER)
add('course-owner-foreign-course', 'owner', course('c3'), 200, course_result([], []))
add('course-owner-without-association', 'owner_without_association', course('c1'), 200, OWNER_C1)
# Owner impersonating the athlete within the own association: course, associates and
# subscriptions carry the association/family boundary (a5 is outside it).
add('course-scoped-athlete', 'owner', course('c1'), 200,
    course_result([person('a1')], [person('a2', 's2'), person('a6', 's6'), person('a7', 's7')]), headers=SCOPED)
add('course-scoped-foreign-course-denied', 'owner', course('c3'), 403, SCOPED_RESOURCE, headers=SCOPED)
add('course-scoped-malformed-denied', 'owner', COURSE + 'not-a-uuid', 403, SCOPED_RESOURCE, headers=SCOPED)

# === profile/associates/sport-association/<uid> =================================================
SPORT = '/profile/associates/sport-association/'


def sport_result(subscribed, unsubscribed):
    return {'associates_subscribed': subscribed, 'associates_unsubscribed': unsubscribed}


add('sport-anonymous', 'anonymous', SPORT + OWN, 401, ANONYMOUS)
add('sport-admin-without-session', 'admin', SPORT + OWN, 403, ADMIN)
add('sport-restricted-missing-permission', 'restricted_collaborator', SPORT + OWN, 403, CONNECTED)
add('sport-wrong-method', 'owner', SPORT + OWN, 405, method('PUT'), method='PUT', body={})
add('sport-options', 'athlete', SPORT + OWN, 200, options(
    'Profile Associates Sport Association', 'Select all the associates that are subscribed on a sport association and are '
    'connected to the user,\nplus return a list of possible subscriptions'), method='OPTIONS')
add('sport-malformed-uid', 'athlete', SPORT + 'abc', 400, BAD_UUID)
# Enabled, non-deleted associates of the athlete; a1 has two subscriptions (lowest id), s6 is last year.
add('sport-athlete-own', 'athlete', SPORT + OWN, 200,
    sport_result([person('a1', 's1b'), person('a2', 's2')], [person(None), person('a6')]))
add('sport-athlete-foreign', 'athlete', SPORT + FOREIGN, 200,
    sport_result([], [person(None), person('a1'), person('a2'), person('a6')]))
add('sport-multi-club-athlete-foreign', 'multi_athlete', SPORT + FOREIGN, 200, sport_result([person('a3', 's3')], [person('a8')]))
add('sport-multi-club-athlete-own', 'multi_athlete', SPORT + OWN, 200, sport_result([], [person('a3'), person('a8')]))
add('sport-athlete-unknown-association', 'athlete', SPORT + UNKNOWN, 200,
    sport_result([], [person(None), person('a1'), person('a2'), person('a6')]))
add('sport-athlete-group', 'athlete', SPORT + OWN, 200, sport_result([], [person('a6')]), headers=OWN_GROUP)
add('sport-owner', 'owner', SPORT + OWN, 200, sport_result([person('a5', 's5')], []))
add('sport-full-collaborator-foreign', 'full_collaborator', SPORT + FOREIGN, 200, sport_result([], [person('a5')]))
add('sport-impossible-start-date-server-error', 'owner_without_association', SPORT + OWN, 500, SERVER)
add('sport-scoped-athlete', 'owner', SPORT + OWN, 200,
    sport_result([person('a1', 's1b'), person('a2', 's2')], [person(None), person('a6')]), headers=SCOPED)
add('sport-scoped-foreign-denied', 'owner', SPORT + FOREIGN, 403, SCOPED_RESOURCE, headers=SCOPED)
# The scope check accepts the association owner's username; the view then rejects it.
add('sport-scoped-username-bad-uuid', 'owner', SPORT + 'message-reads-700001', 400, BAD_UUID, headers=SCOPED)

# === profile/image/<uid> ========================================================================
IMAGE = '/profile/image/'
add('image-anonymous-owner', 'anonymous', IMAGE + IDS['owner'], 200, AVATAR)
# Response(None): the JSON renderer writes an empty body.
add('image-anonymous-without-avatar', 'anonymous', IMAGE + IDS['athlete'], 200, '<empty>')
add('image-anonymous-uppercase', 'anonymous', IMAGE + IDS['owner'].upper(), 200, AVATAR)
add('image-unknown-server-error', 'anonymous', IMAGE + UNKNOWN, 500, SERVER)
add('image-malformed-uid', 'anonymous', IMAGE + 'xyz', 400, BAD_UUID)
add('image-athlete-reads-owner', 'athlete', IMAGE + IDS['owner'], 200, AVATAR)
add('image-full-collaborator', 'full_collaborator', IMAGE + IDS['athlete'], 200, '<empty>')
add('image-admin-without-session', 'admin', IMAGE + IDS['owner'], 403, ADMIN)
add('image-restricted-missing-permission', 'restricted_collaborator', IMAGE + IDS['owner'], 403, SETTINGS_UPDATE)
add('image-scoped-athlete-denied', 'owner', IMAGE + IDS['owner'], 403, SCOPED_DENIED, headers=SCOPED)
add('image-wrong-method', 'anonymous', IMAGE + IDS['owner'], 405, method('POST'), method='POST', body={})
add('image-options', 'anonymous', IMAGE + IDS['owner'], 200, options('Profile Image'), method='OPTIONS')

# === onboarding/update ==========================================================================
# The view sends a fixed text for a save error and logs the detail.
FIXED_ERROR = {'error': 'Unable to update the onboarding data.'}
ONBOARD = '/onboarding/update'
UPDATED = {'message': 'Updated successfully'}
NOT_ASSOCIATION = {'error': 'You are not a sport association.'}

MULTI = 'multipart/form-data; boundary=profilo'
UPLOAD_DICT = 'Invalid data. Expected a dictionary, but got InMemoryUploadedFile.'


def multipart(*parts):
    """A multipart body: (name, value) text parts and (name, filename, content) file parts."""
    out = ''
    for part in parts:
        if len(part) == 2:
            out += f'--profilo\r\nContent-Disposition: form-data; name="{part[0]}"\r\n\r\n{part[1]}\r\n'
        else:
            out += (f'--profilo\r\nContent-Disposition: form-data; name="{part[0]}"; filename="{part[1]}"\r\n'
                    f'Content-Type: application/octet-stream\r\n\r\n{part[2]}\r\n')
    return out + '--profilo--\r\n'


# Stored state the effects are authored against (model defaults of the base fixture rows).
ASSOC = {'own': {'denomination': 'Message reads own', 'tax_code': '70000000001', 'logo': None, 'sport': None,
                 'subscription_fee': '0.00', 'membership_fee': None},
         'foreign': {'denomination': 'Message reads foreign', 'tax_code': '70000000002', 'logo': None, 'sport': None,
                     'subscription_fee': '0.00', 'membership_fee': None}}
USERS = {'owner': {'balance_sheet_year': 1, 'subscription_start_day': 1, 'subscription_start_month': 1, 'balance_sheet_start_day': 1,
                   'balance_sheet_start_month': 1, 'lead_sport_association_role': None, 'lead_sport_association_size': None,
                   'lead_sport_market_channel': None, 'phone': None}}
USERS['foreign_owner'] = dict(USERS['owner'])
USERS['deleted_association_owner'] = dict(USERS['owner'])
ASSOC['deleted'] = {'denomination': 'Message reads deleted', 'tax_code': '70000000003', 'logo': None, 'sport': None,
                    'subscription_fee': '0.00', 'membership_fee': None}
FLAGS = {'show': True, 'create_membership': False, 'view_membership': False, 'approve_payment': False, 'download_invoice': False,
         'view_collaborators': False, 'view_settings': False}


def text(value):
    return 'None' if value is None else str(value)


def association_save(label, values, actor, *, audit_values=None):
    """A full SportAssociation save: audit str() pairs of the changed fields, then the stored values.
    audit_values: the in-memory values auditlog compares when they differ from what the column stores."""
    shown = audit_values or values
    changes = {k: [text(ASSOC[label][k]), text(v)] for k, v in shown.items() if text(ASSOC[label][k]) != text(v)}
    ASSOC[label].update(values)
    if not changes:
        return None
    # A soft-deleted association is not resolved by the audit index: its entries have no index row.
    return {'model': 'sportassociation', 'key': label, 'actor': actor, 'changes': changes, **({'index': False} if label == 'deleted' else {})}


def user_save(label, values, actor, *, store=True):
    """store=False: the entry auditlog wrote before an UPDATE that failed (the row keeps its values)."""
    changes = {k: [text(USERS[label][k]), text(v)] for k, v in values.items() if text(USERS[label][k]) != text(v)}
    stored = {k: (int(v) if k in ('subscription_start_day', 'subscription_start_month', 'balance_sheet_start_day',
                                  'balance_sheet_start_month', 'balance_sheet_year') else (None if v is None else str(v)))
              for k, v in values.items()}
    if store:
        USERS[label].update(stored)
    if not changes:
        return None
    return {'model': 'user', 'key': label, 'actor': actor, 'changes': changes, **({'index': False} if label == 'deleted_association_owner' else {})}


def flags_save(values, actor):
    changes = {k: [text(FLAGS[k]), text(v)] for k, v in values.items() if text(FLAGS[k]) != text(v)}
    FLAGS.update(values)
    return {'model': 'usersonboarding', 'key': 'onboarding', 'actor': actor, 'changes': changes} if changes else None


def effect(*audits, completed=False):
    """The complete expected state after the case plus the ordered audit entries."""
    return {'audits': [a for a in audits if a], 'associations': json.loads(json.dumps(ASSOC)), 'users': json.loads(json.dumps(USERS)),
            'onboarding': dict(FLAGS), 'instance_completed': completed or COMPLETED[0]}


COMPLETED = [False]


def repr_errors(errors):
    """str() of DRF ValidationError(dict): the dict repr of ErrorDetail lists."""
    def detail(message, code):
        return f'ErrorDetail(string={message!r}, code={code!r})'
    return '{' + ', '.join(f'{field!r}: [' + ', '.join(detail(m, c) for m, c in items) + ']' for field, items in errors.items()) + '}'


add('onboarding-anonymous', 'anonymous', ONBOARD, 401, ANONYMOUS, method='PATCH', body={})
add('onboarding-admin-without-session', 'admin', ONBOARD, 403, ADMIN, method='PATCH', body={})
add('onboarding-restricted-missing-permission', 'restricted_collaborator', ONBOARD, 403, SETTINGS_UPDATE, method='PATCH',
    body={'lead_data': {}})
add('onboarding-scoped-athlete-denied', 'owner', ONBOARD, 403, SCOPED_DENIED, method='PATCH', body={}, headers=SCOPED)
add('onboarding-wrong-method', 'owner', ONBOARD, 405, method('GET'))
add('onboarding-options', 'owner', ONBOARD, 200, options('Onboarding Update'), method='OPTIONS')
add('onboarding-athlete-denied', 'athlete', ONBOARD, 403, NOT_ASSOCIATION, method='PATCH', body={'lead_data': {}})
add('onboarding-empty-body', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={}, effect=effect())
add('onboarding-list-body', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body=['membership'], effect=effect())
# `'sport_association' in request.data` is a substring test on a string body.
add('onboarding-string-body-server-error', 'owner', ONBOARD, 500, FIXED_ERROR,
    method='PATCH', raw_body='"has sport_association inside"', content_type='application/json', effect=effect())
# The logging membership test runs before the view's try.
add('onboarding-number-body-server-error', 'owner', ONBOARD, 500, SERVER, method='PATCH', raw_body='5',
    content_type='application/json', effect=effect())
add('onboarding-without-association-server-error', 'owner_without_association', ONBOARD, 500,
    FIXED_ERROR, method='PATCH', body={'sport_association': {'sport': 'Calcio'}})

# --- sport_association ---------------------------------------------------------------------------
add('onboarding-association-invalid', 'owner', ONBOARD, 400, {'error': repr_errors({
    'denomination': [('This field may not be blank.', 'blank')],
    'tax_code': [('Ensure this field has no more than 11 characters.', 'max_length')],
    'logo': [('Not a valid string.', 'invalid')],
    'subscription_fee': [('A valid number is required.', 'invalid')],
    'membership_fee': [('Ensure that there are no more than 9 digits in total.', 'max_digits')]})},
    method='PATCH', body={'sport_association': {'denomination': '   ', 'tax_code': 'x' * 12, 'logo': True, 'sport': None,
                                                'subscription_fee': 'abc', 'membership_fee': '1234567.123'}}, effect=effect())
add('onboarding-association-more-invalid', 'owner', ONBOARD, 400, {'error': repr_errors({
    'denomination': [('This field may not be null.', 'null')],
    'sport': [('Ensure this field has no more than 100 characters.', 'max_length')],
    'subscription_fee': [('Ensure that there are no more than 2 decimal places.', 'max_decimal_places')],
    'membership_fee': [('Ensure that there are no more than 7 digits before the decimal point.', 'max_whole_digits')]})},
    method='PATCH', body={'sport_association': {'denomination': None, 'sport': 's' * 101, 'subscription_fee': 1.234,
                                                'membership_fee': 12345678}}, effect=effect())
add('onboarding-association-not-dict', 'owner', ONBOARD, 400, {'error': repr_errors({
    'non_field_errors': [('Invalid data. Expected a dictionary, but got str.', 'invalid')]})},
    method='PATCH', body={'sport_association': 'Calcio'}, effect=effect())
add('onboarding-association-null', 'owner', ONBOARD, 400, {'error': repr_errors({'non_field_errors': [('No data provided', 'null')]})},
    method='PATCH', body={'sport_association': None}, effect=effect())
add('onboarding-association-form-not-dict', 'owner', ONBOARD, 400, {'error': repr_errors({
    'non_field_errors': [('Invalid data. Expected a dictionary, but got str.', 'invalid')]})},
    method='PATCH', raw_body='sport_association=Calcio', content_type='application/x-www-form-urlencoded', effect=effect())
saved = association_save('own', {'denomination': 'Nuova ASD', 'tax_code': '12345678901', 'logo': 'data:image/png;base64,AAAA',
                                 'sport': 'Calcio', 'subscription_fee': '12.50'}, 'owner')
add('onboarding-association-saved', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'sport_association': {'denomination': '  Nuova ASD ', 'tax_code': '12345678901', 'logo': 'data:image/png;base64,AAAA',
                                'sport': 'Calcio', 'subscription_fee': '12.5', 'membership_fee': None, 'regulation': 'ignored'},
          'lead_data': {'lead_sport_association_role': 'not reached'}}, effect=effect(saved))
add('onboarding-association-collaborator', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'sport_association': {'sport': 7, 'logo': None}},
    effect=effect(association_save('own', {'sport': '7', 'logo': None}, 'full_collaborator')))
add('onboarding-association-unchanged', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'sport_association': {'sport': '7', 'subscription_fee': '12.50'}}, effect=effect())

# --- membership_data (atomic) --------------------------------------------------------------------
add('onboarding-membership-invalid', 'owner', ONBOARD, 400, {'error': repr_errors({
    'subscription_fee': [('This field may not be null.', 'null')]})}, method='PATCH',
    body={'membership_data': {'subscription_fee': None, 'season': {'day': 1, 'month': 9}}}, effect=effect())
add('onboarding-membership-not-dict', 'owner', ONBOARD, 400, {'error': repr_errors({
    'non_field_errors': [('Invalid data. Expected a dictionary, but got list.', 'invalid')]})}, method='PATCH',
    body={'membership_data': []}, effect=effect())
# The association save happens, then membership_data['season']['day'] raises: everything rolls back.
add('onboarding-membership-key-error-rolls-back', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'membership_data': {'subscription_fee': '30', 'season': {'month': 3}}}, effect=effect())
add('onboarding-membership-bad-day-rolls-back', 'owner', ONBOARD, 500,
    FIXED_ERROR, method='PATCH',
    body={'membership_data': {'membership_fee': '5', 'season': {'day': 'abc', 'month': 2}}}, effect=effect())
first = association_save('own', {'subscription_fee': '20.00', 'membership_fee': '7.50'}, 'owner')
second = association_save('own', {}, 'owner', audit_values={'subscription_fee': '20.0', 'membership_fee': '7.5'})
user = user_save('owner', {'balance_sheet_year': 4, 'subscription_start_day': 1, 'subscription_start_month': 9,
                           'balance_sheet_start_day': '1', 'balance_sheet_start_month': '9'}, 'owner')
add('onboarding-membership-saved', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {'subscription_fee': '20', 'membership_fee': '7.5', 'season': {'day': 1, 'month': 9},
                              'fiscal': {'day': '1', 'month': '9'}, 'other': True}}, effect=effect(first, second, user))
# membership_fee None skips the float assignment; nothing else changes.
add('onboarding-membership-null-fee', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {'membership_fee': None}},
    effect=effect(association_save('own', {'membership_fee': None}, 'full_collaborator')))
add('onboarding-membership-collaborator-fiscal', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {'fiscal': {'day': 15, 'month': '3'}}},
    effect=effect(user_save('owner', {'balance_sheet_start_day': 15, 'balance_sheet_start_month': '3'}, 'full_collaborator')))
# IntegerField.get_prep_value is int(value): floats are truncated, bools are 0/1, any Unicode digits count; the audit entry
# keeps str() of the raw value. Values int() rejects raise before the UPDATE; an integer the column cannot hold is the
# database's refusal (smallint out of range, text as psycopg2 prints it). Everything rolls back with the atomic block.
add('onboarding-membership-float-day', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {'season': {'day': 2.9, 'month': 9}}},
    effect=effect(user_save('owner', {'subscription_start_day': 2.9, 'subscription_start_month': 9}, 'owner')))
add('onboarding-membership-bool-day', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {'season': {'day': True, 'month': False}}},
    effect=effect(user_save('owner', {'subscription_start_day': True, 'subscription_start_month': False}, 'owner')))
add('onboarding-membership-unicode-digits', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {'season': {'day': '\u0661\u0662', 'month': ' 3 '}}},
    effect=effect(user_save('owner', {'subscription_start_day': '\u0661\u0662', 'subscription_start_month': ' 3 '}, 'owner')))
add('onboarding-membership-infinite-day', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    raw_body='{"membership_data": {"season": {"day": 1e999, "month": 1}}}', content_type='application/json', effect=effect())
add('onboarding-membership-list-day', 'owner', ONBOARD, 500,
    FIXED_ERROR, method='PATCH',
    body={'membership_data': {'season': {'day': [1, 'a'], 'month': 1}}}, effect=effect())
add('onboarding-membership-dict-month', 'owner', ONBOARD, 500,
    FIXED_ERROR, method='PATCH',
    body={'membership_data': {'season': {'day': 1, 'month': {'a': 1}}}}, effect=effect())
add('onboarding-membership-day-out-of-range', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'membership_data': {'season': {'day': 40000, 'month': 1}}}, effect=effect())
add('onboarding-membership-day-beyond-int64', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    raw_body='{"membership_data": {"fiscal": {"day": 99999999999999999999, "month": 1}}}', content_type='application/json', effect=effect())
add('onboarding-membership-invalid-before-range', 'owner', ONBOARD, 500,
    FIXED_ERROR, method='PATCH',
    body={'membership_data': {'season': {'day': 40000, 'month': 'x'}}}, effect=effect())
# Fresh self-host onboarding of the foreign association (primary association of the instance).
FRESH = 'Fresh self-host onboarding requires a complete membership payload.'
add('onboarding-fresh-invalid', 'foreign_owner', ONBOARD, 400, {'error': FRESH, 'details': {
    'season.day': 'This field must be between 1 and 31.', 'season.month': 'This field must be between 1 and 12.',
    'fiscal.day': 'This field must be a whole number.', 'fiscal.month': 'This field must be a valid number.',
    'subscription_fee': 'This field is required.', 'membership_fee': 'This field must be a valid amount.'}}, method='PATCH',
    body={'membership_data': {'season': {'day': 0, 'month': '13'}, 'fiscal': {'day': 1.5, 'month': 'x'}, 'subscription_fee': '',
                              'membership_fee': 'abc'}}, effect=effect())
add('onboarding-fresh-missing-sections', 'foreign_owner', ONBOARD, 400, {'error': FRESH, 'details': {
    'season': 'This section is required.', 'fiscal.day': 'This field is required.', 'fiscal.month': 'This field is required.',
    'subscription_fee': 'This field is required.', 'membership_fee': 'This field is required.'}}, method='PATCH',
    body={'membership_data': {'season': 'x', 'fiscal': {'day': None, 'month': ''}, 'membership_fee': None}}, effect=effect())
add('onboarding-fresh-not-object', 'foreign_owner', ONBOARD, 400, {'error': FRESH, 'details': {
    'membership_data': 'This field must be an object.'}}, method='PATCH', body={'membership_data': 'x'}, effect=effect())
add('onboarding-fresh-file-part', 'foreign_owner', ONBOARD, 400, {'error': FRESH, 'details': {
    'membership_data': 'This field must be an object.'}}, method='PATCH',
    raw_body=multipart(('membership_data', 'a.txt', 'ciao')), content_type=MULTI, effect=effect())
# The fresh validator accepts a decimal comma; the serializer does not.
add('onboarding-fresh-comma-fee-serializer', 'foreign_owner', ONBOARD, 400, {'error': repr_errors({
    'subscription_fee': [('A valid number is required.', 'invalid')]})}, method='PATCH',
    body={'membership_data': {'season': {'day': 1, 'month': 9}, 'fiscal': {'day': '1.0', 'month': 9}, 'subscription_fee': '15,5',
                              'membership_fee': '10'}}, effect=effect())
first = association_save('foreign', {'subscription_fee': '25.00', 'membership_fee': '10.00'}, 'foreign_owner')
second = association_save('foreign', {}, 'foreign_owner', audit_values={'subscription_fee': '25.0', 'membership_fee': '10.0'})
user = user_save('foreign_owner', {'balance_sheet_year': 4, 'subscription_start_day': 1, 'subscription_start_month': 9,
                                   'balance_sheet_start_day': 1, 'balance_sheet_start_month': 9}, 'foreign_owner')
add('onboarding-fresh-complete', 'foreign_owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {'season': {'day': 1, 'month': 9}, 'fiscal': {'day': 1, 'month': 9}, 'subscription_fee': 25,
                              'membership_fee': '10'}}, effect=effect(first, second, user, completed=True))
COMPLETED[0] = True
add('onboarding-fresh-completed-skips-validation', 'foreign_owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'membership_data': {}}, effect=effect())

# --- lead_data (autocommit) ----------------------------------------------------------------------
add('onboarding-lead-saved', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': 'Presidente', 'lead_sport_association_size': '50-100',
                        'lead_sport_market_channel': 'Google', 'phone': '+39 333 0000000'}},
    effect=effect(user_save('owner', {'lead_sport_association_role': 'Presidente', 'lead_sport_association_size': '50-100',
                                      'lead_sport_market_channel': 'Google', 'phone': '+39 333 0000000'}, 'owner')))
add('onboarding-lead-scalars', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': 5, 'lead_sport_association_size': True, 'lead_sport_market_channel': None}},
    effect=effect(user_save('owner', {'lead_sport_association_role': 5, 'lead_sport_association_size': True,
                                      'lead_sport_market_channel': None}, 'full_collaborator')))
add('onboarding-lead-missing-key', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': 'x'}}, effect=effect())
add('onboarding-lead-string', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': 'x'}, effect=effect())
add('onboarding-lead-list', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': []}, effect=effect())
add('onboarding-lead-null', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': None}, effect=effect())

# --- onboarding_data (autocommit) ----------------------------------------------------------------
add('onboarding-flags-saved', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': {'create_membership': True, 'view_settings': 'yes', 'show': 1, 'unknown': 'x'}},
    effect=effect(flags_save({'create_membership': True, 'view_settings': True}, 'owner')))
add('onboarding-flags-collaborator', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': {'show': 'false'}}, effect=effect(flags_save({'show': False}, 'full_collaborator')))
# The checklist sends the whole stored row back (onboarding-checklist.svelte): users_onboarding_id
# and user are read-only serializer fields, so the save keeps both and only the flags are audited.
def ui_flags(**changes):
    row = {'users_onboarding_id': ONBOARDING, 'user': IDS['owner']}
    row.update(FLAGS)
    row.update(changes)
    return row


add('onboarding-flags-ui-row', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': ui_flags(view_membership=True)}, effect=effect(flags_save({'view_membership': True}, 'owner')))
add('onboarding-flags-ui-row-collaborator', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': ui_flags(approve_payment=True, download_invoice=True)},
    effect=effect(flags_save({'approve_payment': True, 'download_invoice': True}, 'full_collaborator')))
add('onboarding-flags-ui-row-unchanged', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': ui_flags()}, effect=effect())
add('onboarding-flags-ui-row-uppercase-ids', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': ui_flags(users_onboarding_id=ONBOARDING.upper(), user=IDS['owner'].upper(), view_collaborators=True)},
    effect=effect(flags_save({'view_collaborators': True}, 'owner')))
add('onboarding-flags-ui-row-invalid-flag', 'owner', ONBOARD, 400, {'error': repr_errors({
    'show': [('Must be a valid boolean.', 'invalid')]})}, method='PATCH', body={'onboarding_data': ui_flags(show='maybe')},
    effect=effect())
# Onboarding update only changes the checklist fields of the caller's own row: any value sent for
# users_onboarding_id or user is not read (no validation, no change, no added row).
add('onboarding-flags-other-user-value', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': ui_flags(user=IDS['foreign_owner'], show=False)},
    effect=effect(flags_save({'show': False}, 'owner')))
add('onboarding-flags-other-id-value', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': ui_flags(users_onboarding_id=uid(91), show=True)},
    effect=effect(flags_save({'show': True}, 'owner')))
add('onboarding-flags-other-values-collaborator', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': {'users_onboarding_id': uid(92), 'user': IDS['athlete'], 'view_settings': False}},
    effect=effect(flags_save({'view_settings': False}, 'full_collaborator')))
add('onboarding-flags-other-values-unchanged', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': ui_flags(users_onboarding_id=uid(93), user=IDS['foreign_owner'])}, effect=effect())
add('onboarding-flags-identity-values-not-validated', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': {'users_onboarding_id': 5, 'user': ['x'], 'view_settings': True}},
    effect=effect(flags_save({'view_settings': True}, 'owner')))
add('onboarding-flags-identity-values-null', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'onboarding_data': {'users_onboarding_id': None, 'user': None, 'download_invoice': False}},
    effect=effect(flags_save({'download_invoice': False}, 'owner')))
add('onboarding-flags-invalid', 'owner', ONBOARD, 400, {'error': repr_errors({
    'show': [('Must be a valid boolean.', 'invalid')], 'approve_payment': [('This field may not be null.', 'null')]})},
    method='PATCH', body={'onboarding_data': {'approve_payment': None, 'show': 'maybe'}}, effect=effect())
add('onboarding-flags-missing-row', 'foreign_owner', ONBOARD, 500, FIXED_ERROR,
    method='PATCH', body={'onboarding_data': {'show': True}}, effect=effect())

# --- file parts in a multipart body (QueryDict values are uploaded files) -------------------------
UPLOAD_ERRORS = {'error': repr_errors({'non_field_errors': [(UPLOAD_DICT, 'invalid')]})}
for branch in ('sport_association', 'membership_data', 'onboarding_data'):
    add('multipart-file-' + branch, 'owner', ONBOARD, 400, UPLOAD_ERRORS, method='PATCH',
        raw_body=multipart((branch, 'a.txt', 'ciao')), content_type=MULTI, effect=effect())
add('multipart-file-lead-data', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    raw_body=multipart(('lead_data', 'a.txt', 'ciao')), content_type=MULTI, effect=effect())
add('multipart-file-after-text-branch', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    raw_body=multipart(('lead_data', 'x'), ('doc', 'a.txt', 'ciao')), content_type=MULTI, effect=effect())
add('multipart-file-without-branch', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    raw_body=multipart(('doc', 'a.txt', 'ciao')), content_type=MULTI, effect=effect())
add('multipart-file-collaborator-association', 'full_collaborator', ONBOARD, 400, UPLOAD_ERRORS, method='PATCH',
    raw_body=multipart(('sport_association', 'a.txt', 'ciao')), content_type=MULTI, effect=effect())

# --- an owner whose association is soft-deleted: the saves work, the audit has no index row --------
DELETED = 'deleted_association_owner'
add('deleted-association-saved', DELETED, ONBOARD, 200, UPDATED, method='PATCH', body={'sport_association': {'sport': 'Calcio'}},
    effect=effect(association_save('deleted', {'sport': 'Calcio'}, DELETED)))
first = association_save('deleted', {'membership_fee': '5.00'}, DELETED)
second = association_save('deleted', {}, DELETED, audit_values={'membership_fee': '5.0'})
user = user_save(DELETED, {'balance_sheet_year': 4}, DELETED)
add('deleted-association-membership', DELETED, ONBOARD, 200, UPDATED, method='PATCH', body={'membership_data': {'membership_fee': '5'}},
    effect=effect(first, second, user))
LEAD = {'lead_sport_association_role': 'a', 'lead_sport_association_size': 'b', 'lead_sport_market_channel': 'c'}
add('deleted-association-lead', DELETED, ONBOARD, 200, UPDATED, method='PATCH', body={'lead_data': LEAD},
    effect=effect(user_save(DELETED, LEAD, DELETED)))
add('deleted-association-flags-missing-row', DELETED, ONBOARD, 500, FIXED_ERROR,
    method='PATCH', body={'onboarding_data': {'show': True}}, effect=effect())

# --- lead values: containers are stored as their Python repr; a value too long for the column ------
# fails the UPDATE after auditlog wrote its entry (autocommit), so the entry stays and the row does not change.
LEAD_REST = {'lead_sport_association_size': 's', 'lead_sport_market_channel': 'c'}
long_role = 'x' * 151
add('lead-too-long-role', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': dict(LEAD_REST, lead_sport_association_role=long_role)},
    effect=effect(user_save('owner', dict(LEAD_REST, lead_sport_association_role=long_role), 'owner', store=False)))
add('lead-too-long-phone', 'full_collaborator', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': dict(LEAD_REST, lead_sport_association_role='r', phone='1' * 21)},
    effect=effect(user_save('owner', dict(LEAD_REST, lead_sport_association_role='r', phone='1' * 21), 'full_collaborator', store=False)))
add('lead-too-long-phone-before-role', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': dict(LEAD_REST, lead_sport_association_role=long_role, phone='2' * 25)},
    effect=effect(user_save('owner', dict(LEAD_REST, lead_sport_association_role=long_role, phone='2' * 25), 'owner', store=False)))
add('lead-too-long-size-unicode', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': 'r', 'lead_sport_association_size': 'è' * 151, 'lead_sport_market_channel': 'c'}},
    effect=effect(user_save('owner', {'lead_sport_association_role': 'r', 'lead_sport_association_size': 'è' * 151,
                                      'lead_sport_market_channel': 'c'}, 'owner', store=False)))
add('lead-exactly-fits', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': 'x' * 150, 'lead_sport_association_size': 'è' * 150, 'lead_sport_market_channel': 'c',
                        'phone': '3' * 20}},
    effect=effect(user_save('owner', {'lead_sport_association_role': 'x' * 150, 'lead_sport_association_size': 'è' * 150,
                                      'lead_sport_market_channel': 'c', 'phone': '3' * 20}, 'owner')))
MIXED = ['a', 1, None, True, {'k': 'v'}, "d'q", 'é\n', 2.5, [], {}]
add('lead-list-stored-as-repr', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': MIXED, 'lead_sport_association_size': [], 'lead_sport_market_channel': {}}},
    effect=effect(user_save('owner', {'lead_sport_association_role': repr(MIXED), 'lead_sport_association_size': '[]',
                                      'lead_sport_market_channel': '{}'}, 'owner')))
NESTED = {'a': 1.5, 'b': "it's", 'c': [True, None], 'd': {'e': 'f'}}
add('lead-dict-stored-as-repr', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': NESTED, 'lead_sport_association_size': 's', 'lead_sport_market_channel': 'c',
                        'phone': ['1', 2]}},
    effect=effect(user_save('owner', {'lead_sport_association_role': repr(NESTED), 'lead_sport_association_size': 's',
                                      'lead_sport_market_channel': 'c', 'phone': "['1', 2]"}, 'full_collaborator')))
add('lead-float-stored-as-str', 'owner', ONBOARD, 200, UPDATED, method='PATCH',
    body={'lead_data': {'lead_sport_association_role': 1.5e20, 'lead_sport_association_size': 1e-05, 'lead_sport_market_channel': 100.0}},
    effect=effect(user_save('owner', {'lead_sport_association_role': '1.5e+20', 'lead_sport_association_size': '1e-05',
                                      'lead_sport_market_channel': '100.0'}, 'owner')))

# --- fees: the second save audits str(float(value)) against the stored decimal -----------------------
first = association_save('own', {'membership_fee': '1234567.89'}, 'owner')
add('membership-fee-large', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={'membership_data': {'membership_fee': '1234567.89'}},
    effect=effect(first))
first = association_save('own', {'membership_fee': '0.00'}, 'owner', audit_values={'membership_fee': '-0.00'})
second = association_save('own', {}, 'owner', audit_values={'membership_fee': '-0.0'})
add('membership-fee-negative-zero', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={'membership_data': {'membership_fee': '-0'}},
    effect=effect(first, second))
first = association_save('own', {'subscription_fee': '0.00'}, 'owner', audit_values={'subscription_fee': '-0.00'})
second = association_save('own', {}, 'owner', audit_values={'subscription_fee': '-0.0'})
add('subscription-fee-negative-zero', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={'membership_data': {'subscription_fee': '-0.00'}},
    effect=effect(first, second))
first = association_save('own', {'membership_fee': '100.00'}, 'owner')
second = association_save('own', {}, 'owner', audit_values={'membership_fee': '100.0'})
add('membership-fee-exponent-text', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={'membership_data': {'membership_fee': '1E+2'}},
    effect=effect(first, second))

# --- a blank nullable fee: the serializer reads it as None (DecimalField.validate_empty_values) -------
add('association-fee-set', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={'sport_association': {'membership_fee': '3.5'}},
    effect=effect(association_save('own', {'membership_fee': '3.50'}, 'owner')))
add('association-fee-blank-is-none', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={'sport_association': {'membership_fee': ''}},
    effect=effect(association_save('own', {'membership_fee': None}, 'owner')))
add('association-fee-set-again', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body={'sport_association': {'membership_fee': 4}},
    effect=effect(association_save('own', {'membership_fee': '4.00'}, 'owner')))
add('association-fee-spaces-is-none', 'full_collaborator', ONBOARD, 200, UPDATED, method='PATCH',
    body={'sport_association': {'membership_fee': '  \t'}}, effect=effect(association_save('own', {'membership_fee': None}, 'full_collaborator')))
add('association-subscription-fee-blank', 'owner', ONBOARD, 400, {'error': repr_errors({
    'subscription_fee': [('A valid number is required.', 'invalid')]})}, method='PATCH', body={'sport_association': {'subscription_fee': ''}},
    effect=effect())
# float('') raises after the serializer saved: the atomic block rolls the association save back.
add('membership-fee-blank-float-error', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'membership_data': {'membership_fee': ''}}, effect=effect())
add('membership-fee-spaces-float-error', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'membership_data': {'membership_fee': '  ', 'subscription_fee': '9'}}, effect=effect())

# --- season/fiscal values the column cannot take roll everything back; the ones int() reads are stored ----
def season_body(day, month=2, section='season'):
    return {'membership_data': {section: {'day': day, 'month': month}}}


def season_raw(day_text, section='season'):
    return '{"membership_data": {"%s": {"day": %s, "month": 2}}}' % (section, day_text)


for label, value in (('too-large', 40000), ('negative', -1), ('null', None), ('list', [1]), ('dict', {}), ('huge', 2 ** 70),
                     ('text', 'abc'), ('text-too-large', '99999'), ('text-negative', '-3'), ('empty-text', ''), ('float-negative', -1.5)):
    add('membership-season-day-' + label, 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH', body=season_body(value), effect=effect())
add('membership-fiscal-month-too-large', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH', body=season_body(1, 70000, 'fiscal'), effect=effect())
add('membership-season-day-float-overflow', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH', raw_body=season_raw('1e400'),
    content_type='application/json', effect=effect())
add('membership-season-day-float-too-large', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH', raw_body=season_raw('1e5'),
    content_type='application/json', effect=effect())
add('membership-season-day-nul-text', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH', body=season_body('1\u0000'), effect=effect())
for label, day, month, stored_day, stored_month in (
        ('float', 2.5, 3, 2, 3), ('bool', True, False, 1, 0), ('padded-text', ' 7 ', '1_1', 7, 11), ('unicode-digits', '\u0661\u0662', '\u0663', 12, 3),
        ('float-negative-zero', -0.5, 4, 0, 4), ('float-upper', 32767.9, 5, 32767, 5)):
    add('membership-season-' + label, 'owner', ONBOARD, 200, UPDATED, method='PATCH', body=season_body(day, month),
        effect=effect(user_save('owner', {'balance_sheet_year': 4, 'subscription_start_day': day, 'subscription_start_month': month}, 'owner')))
    USERS['owner'].update({'subscription_start_day': stored_day, 'subscription_start_month': stored_month})
add('membership-season-exponent-float', 'owner', ONBOARD, 200, UPDATED, method='PATCH', raw_body=season_raw('1e1'),
    content_type='application/json',
    effect=effect(user_save('owner', {'balance_sheet_year': 4, 'subscription_start_day': 10.0, 'subscription_start_month': 2}, 'owner')))
add('membership-fiscal-bool-and-unicode', 'owner', ONBOARD, 200, UPDATED, method='PATCH', body=season_body(True, '\u0664', 'fiscal'),
    effect=effect(user_save('owner', {'balance_sheet_year': 4, 'balance_sheet_start_day': True, 'balance_sheet_start_month': '\u0664'}, 'owner')))
USERS['owner'].update({'balance_sheet_start_day': 1, 'balance_sheet_start_month': 4})
# A NUL character cannot be written to a text column: the audit entry that quotes it fails first.
add('lead-nul-role', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': dict(LEAD_REST, lead_sport_association_role='a\u0000b')}, effect=effect())
add('lead-nul-phone-after-long-role', 'owner', ONBOARD, 500, FIXED_ERROR, method='PATCH',
    body={'lead_data': dict(LEAD_REST, lead_sport_association_role=long_role, phone='1\u00002')}, effect=effect())

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'profile/associates/course, profile/associates/sport-association, profile/image and '
    'onboarding/update authored sequence with audit effects', 'ids': IDS, 'extra_users': EXTRA_USERS, 'associates': ASSOCIATES, 'subscriptions': SUBSCRIPTIONS,
    'courses': COURSES, 'course_subscriptions': COURSE_SUBSCRIPTIONS, 'onboarding_id': ONBOARDING, 'instance': INSTANCE,
    'avatar': AVATAR, 'no_association_balance': NO_ASSOCIATION_BALANCE, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
