"""Author the search/profile/<username> HTTP sequence from search-profile.md.

The view is public (AllowAny). It finds User.objects (active users) by
username__iexact; athletes return their UserSerializerSearch; with any
``module_info`` value an association returns the user (avatar through
compress_base64), SportAssociationSearchProfileSerializer plus the module
fields and preregistration window; otherwise it returns the public profile:
active courses (unpinned and pinned, newest first, CourseSerializer plus
total_subscriptions), carnets, course tags and the same user/association
blocks. The association block is the ported association serializer
projection: its authored keys and selected values are checked here and the
runner requires it to be identical across backends. The preregistration
window depends on today's UTC date, so the contract computes it. Run with
``python3 -I``.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('search_profile_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
ids = iter(f'a1000000-0000-4000-8000-{930001 + n:012d}' for n in range(60))


def png(width, height):
    """A solid PNG built with the standard library."""
    import base64, struct, zlib
    chunk = lambda kind, data: struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = b''.join(b'\x00' + b'\x10\x80\xf0' * width for _ in range(height))
    body = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
    return 'data:image/png;base64,' + base64.b64encode(body).decode()


# compress_base64 keeps an image already at its 200x200 bound and under 1 MiB.
PIXEL = png(200, 200)
USERS = {
    'club': {'id': next(ids), 'username': 'Club-Search', 'role': 1, 'first_name': 'Club è', 'last_name': 'Owner', 'avatar': PIXEL,
             'month': 9, 'day': 1, 'disable': True, 'force': False, 'deleted': False},
    'leap': {'id': next(ids), 'username': 'leap-search', 'role': 1, 'first_name': 'Leap', 'last_name': '', 'avatar': None,
             'month': 2, 'day': 31, 'disable': False, 'force': True, 'deleted': False},
    'loner': {'id': next(ids), 'username': 'loner-search', 'role': 1, 'first_name': 'Loner', 'last_name': '', 'avatar': None,
              'month': 1, 'day': 1, 'disable': False, 'force': False, 'deleted': False},
    'runner': {'id': next(ids), 'username': 'Runner-Search', 'role': 2, 'first_name': 'Runner', 'last_name': 'Uno', 'avatar': 'data:not-an-image',
               'month': 1, 'day': 1, 'disable': False, 'force': False, 'deleted': False},
    'helper': {'id': next(ids), 'username': 'helper-search', 'role': 3, 'first_name': 'Helper', 'last_name': '', 'avatar': None,
               'month': 1, 'day': 1, 'disable': False, 'force': False, 'deleted': False},
    'ghost': {'id': next(ids), 'username': 'ghost-search', 'role': 2, 'first_name': 'Ghost', 'last_name': '', 'avatar': None,
              'month': 1, 'day': 1, 'disable': False, 'force': False, 'deleted': True},
}
ASSOCIATIONS = {
    'club': {'id': next(ids), 'user': 'club', 'denomination': 'Club è ASD', 'demand': 'Domanda', 'regulation': None,
             'additional_sections': [{'title': 'Extra'}], 'show_regulation_to_members': True, 'show_regulation_to_both': False,
             'show_regulation_to_athletes': True, 'show_demand_to_members': False, 'show_demand_to_both': True, 'show_demand_to_athletes': False},
    'leap': {'id': next(ids), 'user': 'leap', 'denomination': 'Leap', 'demand': None, 'regulation': 'Regolamento', 'additional_sections': [],
             'show_regulation_to_members': False, 'show_regulation_to_both': False, 'show_regulation_to_athletes': False,
             'show_demand_to_members': False, 'show_demand_to_both': False, 'show_demand_to_athletes': False},
}
CLUB = ASSOCIATIONS['club']['id']
TAGS = {'t1': {'id': next(ids), 'name': 'Nuoto'}, 't2': {'id': next(ids), 'name': 'Bimbi è'}}
LOCATIONS = {'l1': {'id': next(ids), 'title': 'Piscina', 'address': 'Via 1'}}


def course(label, created, *, pinned=False, status=2, deleted=False, tag=None, location=None, **values):
    row = {'id': next(ids), 'label': label, 'created': created, 'pinned': pinned, 'status': status, 'deleted': deleted, 'tag': tag,
           'location': location, 'title': label.title(), 'description': None, 'fee': '50.00', 'one_fee': '0.00', 'events': None,
           'course_type': 1, 'start': None, 'end': None, 'color': '#123456'}
    row.update(values)
    return row


COURSES = [course('newest', '2026-09-03T10:00:00Z', tag='t1', description='Corso è', events=[{'start': 'x'}], fee=None),
           course('older', '2026-09-01T10:00:00.250000Z', start='2026-10-01T08:00:00Z', end='2027-06-30T08:00:00Z'),
           course('pinned', '2026-09-02T10:00:00Z', pinned=True, location='l1', course_type=3, one_fee='12.50'),
           course('draft', '2026-09-04T10:00:00Z', status=1),
           course('removed', '2026-09-05T10:00:00Z', deleted=True)]
COURSE_BY = {row['label']: row for row in COURSES}
# (course, deleted): the soft-delete manager counts live subscriptions only.
SUBSCRIPTIONS = [('newest', False), ('newest', True), ('pinned', False)]
CARNETS = {'k1': {'id': next(ids), 'title': 'Dieci', 'description': 'ingressi', 'lessons': 10, 'fee': '40.00', 'public': True,
                  'created': '2026-08-01T10:00:00Z'}}


def user(label, *, compress=False):
    row = USERS[label]
    # compress_base64 returns a small image unchanged and None on failure input.
    return {'first_name': row['first_name'], 'last_name': row['last_name'], 'username': row['username'], 'avatar_image': row['avatar'],
            # registration_features(): FAMILY_REGISTRATION_ENABLED is the only default-on flag.
            'preview_and_custom_features': ['Iscrizione per famiglia'], 'disable_account_creation': row['disable'], 'force_account_creation': row['force'],
            'online_payments': False}


def serialized_course(row):
    return {'course_id': row['id'], 'sport_association': CLUB, 'status_flag': row['status'], 'course_type': row['course_type'],
            'creation_date': row['created'], 'title': row['title'], 'description': row['description'], 'multi_payments': False,
            'fee': row['fee'], 'events': row['events'], 'one_fee_payment': False, 'one_fee': row['one_fee'], 'google_calendar_id': None,
            'google_background_color': row['color'], 'tags': [{'tag_id': TAGS[row['tag']]['id'], 'tag_name': TAGS[row['tag']]['name']}] if row['tag'] else [],
            'multiple_quotes': None, 'locations': [{'course_location_id': LOCATIONS[row['location']]['id'], 'title': LOCATIONS[row['location']]['title'],
                                                    'address': LOCATIONS[row['location']]['address']}] if row['location'] else [],
            'start_date': row['start'], 'end_date': row['end'], 'pinned': row['pinned'], 'billed_duration_is_sport_season': False,
            'billed_frequency': 1, 'billed_from_subscription_date': False, 'billed_from_day_of_month': 1, 'auto_renewal': False,
            'deleted': False, 'group': None, 'athletes': None,
            'total_subscriptions': sum(1 for c, deleted in SUBSCRIPTIONS if c == row['label'] and not deleted)}


def association_block(label, module=False):
    row = ASSOCIATIONS[label]
    block = {'$profile': row['id'], '$values': {'denomination': row['denomination']}}
    if module:
        block['$values'].update({k: row[k] for k in ('demand', 'regulation', 'additional_sections', 'show_regulation_to_members',
                                                     'show_regulation_to_both', 'show_regulation_to_athletes', 'show_demand_to_members',
                                                     'show_demand_to_both', 'show_demand_to_athletes')})
        block['$preregistration'] = {'month': USERS[row['user']]['month'], 'day': USERS[row['user']]['day']}
    return block


cases = []


def add(name, actor, username, status, response, *, query='', method='GET', headers=None):
    cases.append({'name': f'search-profile-{name}', 'actor': actor, 'method': method, 'path': f'/search/profile/{username}{query}',
                  'headers': headers or {}, 'status': status, 'response': response})


PUBLIC = {'data': {'courses': [serialized_course(COURSE_BY['newest']), serialized_course(COURSE_BY['older'])],
                   'courses_pinned': [serialized_course(COURSE_BY['pinned'])],
                   'carnets': [{'carnet_id': CARNETS['k1']['id'], 'title': 'Dieci', 'description': 'ingressi', 'lessons_number': 10,
                                'creation_date': CARNETS['k1']['created'], 'fee': '40.00', 'public': True}],
                   'user': {**user('club', compress=True), 'sport_association': association_block('club'), 'is_athlete': False},
                   'sport_association_id': CLUB,
                   'courses_tags': [{'tag_id': t['id'], 'tag_name': t['name'], 'active': True} for t in TAGS.values()]}}
MODULE = {'data': {'user': {**user('club', compress=True), 'sport_association': association_block('club', True)}, 'sport_association_id': CLUB}}
add('public-profile-anonymous', 'anonymous', 'club-search', 200, PUBLIC)
add('public-profile-athlete', 'athlete', 'CLUB-SEARCH', 200, PUBLIC)
add('module-info-anonymous', 'anonymous', 'Club-Search', 200, MODULE, query='?module_info=1')
add('module-info-empty-value', 'owner', 'Club-Search', 200, MODULE, query='?module_info=')
add('module-info-invalid-season-date', 'anonymous', 'leap-search', 200,
    {'data': {'user': {**user('leap', compress=True), 'sport_association': association_block('leap', True)}, 'sport_association_id': ASSOCIATIONS['leap']['id']}},
    query='?module_info=yes')
add('athlete-profile', 'anonymous', 'runner-search', 200, {'data': {'user': {**user('runner'), 'is_athlete': True}}})
add('athlete-profile-ignores-module-info', 'anonymous', 'Runner-Search', 200, {'data': {'user': {**user('runner'), 'is_athlete': True}}}, query='?module_info=1')
add('deleted-user-not-found', 'anonymous', 'ghost-search', 404, {'detail': 'user not found.'})
add('missing-user-not-found', 'anonymous', 'nobody-search', 404, {'detail': 'user not found.'})
add('association-without-row-server-error', 'anonymous', 'loner-search', 500, '<stable-server-error-page>')
add('module-info-without-row-server-error', 'anonymous', 'loner-search', 500, '<stable-server-error-page>', query='?module_info=1')
add('collaborator-user-server-error', 'anonymous', 'helper-search', 500, '<stable-server-error-page>')
add('owner-scoped-athlete-other-association', 'owner', 'Club-Search', 403, {'detail': 'Associazione non disponibile.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('admin-without-session', 'admin', 'Club-Search', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('wrong-method', 'anonymous', 'Club-Search', 405, {'detail': 'Method "POST" not allowed.'}, method='POST')
add('options', 'anonymous', 'Club-Search', 200, {'name': 'Search Profile', 'description': 'Returns information about a certain profile',
    'renders': ['application/json'], 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Search profile authored sequence', 'ids': IDS, 'users': USERS,
                              'associations': ASSOCIATIONS, 'tags': TAGS, 'locations': LOCATIONS, 'courses': COURSES,
                              'subscriptions': SUBSCRIPTIONS, 'carnets': CARNETS, 'profile_keys': sorted([
                                  'denomination', 'tax_code', 'address', 'address_cap', 'address_city', 'subscription_fee',
                                  'multiple_subscription_fee', 'subscription_fee_plans', 'membership_fee', 'multiple_membership_fee',
                                  'membership_fee_plans', 'custom_subscription_form_data', 'configuration', 'enabled_for',
                                  'enable_quotes_management', 'checkout_info', 'additional_fields', 'stripe_available_methods',
                                  'invoice_template', 'subscription_template', 'extra_text_invoices']),
                              'module_keys': sorted(['demand', 'regulation', 'additional_sections', 'show_regulation_to_members',
                                  'show_regulation_to_both', 'show_regulation_to_athletes', 'show_demand_to_members',
                                  'show_demand_to_both', 'show_demand_to_athletes', 'preregistration_module_closed',
                                  'remaining_preregistration_days']), 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
