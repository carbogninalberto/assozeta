"""Author the profile/settings and profile/integrations HTTP sequence (profile-settings.md).

profile_settings (@api_view GET, POST; registry GET other.settings.read,
PATCH other.settings.update and no POST/OPTIONS mapping) first reads
user.sport_association (athletes: PermissionDenied 'User is not a sport
association'; a missing association: 500) and creates the association's
membership card configuration when missing, on every method, before anything
else. GET renders the 30 user settings, current_year from
BalanceSheetData.get_range_from_year_and_starting_date(datetime.now(), start day,
start month) (an impossible start date is a 500) and the first configuration.
POST validates UserSettingsSerializer (every field optional; booleans,
choices, bounded integers, a nullable 255 CharField and two tenant-scoped
PaymentCategory relations), then reads validated_data[...] for every field (a
missing one is a KeyError 500 before any save), saves the user (auditlog: the
changed fields), then reads request.data['membership_card_configuration'][...]
(a missing key or a non-dict is a 500 after the user save) and saves the
configuration. auditlog writes the update entry in pre_save, so a value the
model BooleanField rejects (500) keeps its audit entry and not the row.

profile_integrations (@api_view GET, PATCH; registry other.settings.read for
every method; refused in association-scoped sessions) reads
request.user.sport_association and on PATCH assigns request.data.get(...) for
review_url and review_url_enabled without validation, saves the association
and echoes the assigned (raw) values. Requests are not atomic.
Run with ``python3 -I``.
"""
import copy
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('profile_settings_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
CATEGORIES = {'own': 'a1000000-0000-4000-8000-000000977001', 'foreign': 'a1000000-0000-4000-8000-000000977002',
              'global': 'a1000000-0000-4000-8000-000000977003', 'own_archived': 'a1000000-0000-4000-8000-000000977004'}
UNKNOWN = 'a1000000-0000-4000-8000-000000977999'
FOREIGN_REVIEW = {'review_url': 'https://foreign.example.test/review', 'review_url_enabled': True}
SERVER = '<stable-server-error-page>'
SETTINGS, INTEGRATIONS = '/profile/settings', '/profile/integrations'
cases = []


def add(name, actor, path, status, response, *, method='GET', body=None, raw_body=None, content_type=None, headers=None, effect=None):
    case = {'name': f'profile-settings-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
            'status': status, 'response': response, 'effect': effect}
    if body is not None:
        case['body'] = body
    if raw_body is not None:
        case['raw_body'], case['content_type'] = raw_body, content_type
    cases.append(case)


# The owner's stored settings (Django model defaults) and their auditlog str().
FIELDS = ('enumerate_invoices', 'online_payments', 'balance_sheet_year', 'balance_sheet_start_day', 'balance_sheet_start_month',
          'temporary_invoice_deletion', 'auto_archive', 'auto_mark_attendance', 'payment_date_equal_invoice_date',
          'starting_number_invoices', 'auto_paid_payment', 'full_installments_plan', 'show_zero_payments', 'dark_mode',
          'medical_certificate_notifications', 'hide_category_name', 'subscription_duration_equal_sport_year',
          'subscription_duration', 'membership_duration', 'membership_starting_number', 'default_membership_type',
          'subscription_start_day', 'subscription_start_month', 'custom_end_date', 'subscription_end_day',
          'subscription_end_month', 'disable_account_creation', 'force_account_creation', 'default_payment_category',
          'default_payment_category_courses')
DEFAULTS = {'enumerate_invoices': False, 'online_payments': True, 'balance_sheet_year': 1, 'balance_sheet_start_day': 1,
            'balance_sheet_start_month': 1, 'temporary_invoice_deletion': True, 'auto_archive': False, 'auto_mark_attendance': False,
            'payment_date_equal_invoice_date': False, 'starting_number_invoices': 0, 'auto_paid_payment': False,
            'full_installments_plan': False, 'show_zero_payments': False, 'dark_mode': False,
            'medical_certificate_notifications': True, 'hide_category_name': False, 'subscription_duration_equal_sport_year': True,
            'subscription_duration': 2, 'membership_duration': 2, 'membership_starting_number': 0, 'default_membership_type': '',
            'subscription_start_day': 1, 'subscription_start_month': 1, 'custom_end_date': False, 'subscription_end_day': 31,
            'subscription_end_month': 12, 'disable_account_creation': False, 'force_account_creation': False,
            'default_payment_category': None, 'default_payment_category_courses': None}
assert set(DEFAULTS) == set(FIELDS)
OWNER = dict(DEFAULTS)
CARD = {'emit_only_on_approval': False, 'customized_template': None}
REVIEW = {'review_url': None, 'review_url_enabled': False}


def text(value):
    return str(value)


def settings_response(state, card, start=None):
    """The GET body; current_year is computed by the contract at request time."""
    values = {key: state[key] for key in FIELDS}
    values['balance_sheet_year'] = str(state['balance_sheet_year'])
    values['current_year'] = {'$current_year': [state['balance_sheet_start_day'], state['balance_sheet_start_month']]}
    values['table_settings'] = {}
    values['membership_card_configuration'] = dict(card)
    return {'settings': values}


def full_body(**overrides):
    body = {key: OWNER[key] for key in FIELDS}
    body.update(overrides)
    return body


def saved(actor, stored):
    """Apply a validated save to the owner's state; changes are auditlog's str() pairs."""
    changes = {key: [text(OWNER[key]), text(value)] for key, value in stored.items() if text(OWNER[key]) != text(value)}
    OWNER.update(stored)
    return {'actor': actor, 'changes': changes, 'stored': dict(OWNER)}


def card_saved(actor, emit, template, *, stored=True):
    """auditlog compares str(old) with str(new) and json.dumps(sort_keys=True) for the template."""
    dumps = lambda value: json.dumps(value, sort_keys=True)
    changes = {}
    if text(CARD['emit_only_on_approval']) != text(emit):
        changes['emit_only_on_approval'] = [text(CARD['emit_only_on_approval']), text(emit)]
    if dumps(CARD['customized_template']) != dumps(template):
        changes['customized_template'] = [dumps(CARD['customized_template']), dumps(template)]
    if stored:
        CARD.update(emit_only_on_approval=model_boolean(emit), customized_template=template)
    return {'actor': actor, 'changes': changes, 'stored': dict(CARD) if stored else None}


def model_boolean(value):
    """models.BooleanField.to_python for the values this sequence stores."""
    if value in (True, False):
        return bool(value)
    return {'t': True, 'True': True, '1': True, 'f': False, 'False': False, '0': False}[value]


def review_saved(actor, url, enabled, *, stored=True, url_text=None):
    changes = {}
    if text(REVIEW['review_url']) != (url_text if url_text is not None else text(url)):
        changes['review_url'] = [text(REVIEW['review_url']), url_text if url_text is not None else text(url)]
    if text(REVIEW['review_url_enabled']) != text(enabled):
        changes['review_url_enabled'] = [text(REVIEW['review_url_enabled']), text(enabled)]
    if stored:
        REVIEW.update(review_url=url_text if url_text is not None else url, review_url_enabled=model_boolean(enabled))
    return {'actor': actor, 'changes': changes, 'stored': dict(REVIEW) if stored else None}


SCOPED = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
SCOPED_DENIED = {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}
CREDENTIALS = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
CREDENTIALS_DENIED = {'detail': 'Torna al tuo account per gestire le credenziali.'}
ADMIN_OWNER = {'X-Impersonation-Id': '$session:admin_owner', 'User-Id': IDS['owner']}
ADMIN = {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}
ANONYMOUS = {'detail': 'Authentication credentials were not provided.'}
ATHLETE = {'detail': 'User is not a sport association'}
FORBIDDEN = {'detail': 'You do not have permission to perform this action.'}
NO_MAPPING = {'detail': "Access denied: No permission mapping for endpoint 'profile/settings'. Contact administrator to grant access."}
SUCCESS = {'status': 'success'}
options = lambda name: {'name': name, 'description': '', 'renders': ['application/json'],
                        'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}
method = lambda verb: {'detail': f'Method "{verb}" not allowed.'}

# --- profile/settings: authorization, methods and scoping -------------------------------
add('anonymous', 'anonymous', SETTINGS, 401, ANONYMOUS)
add('admin-without-session', 'admin', SETTINGS, 403, ADMIN)
add('restricted-patch-missing-update', 'restricted_collaborator', SETTINGS, 403,
    {'detail': 'Missing permission: other.settings.update'}, method='PATCH', body={})
add('restricted-post-no-mapping', 'restricted_collaborator', SETTINGS, 403, NO_MAPPING, method='POST', body={})
add('restricted-options-no-mapping', 'restricted_collaborator', SETTINGS, 403, NO_MAPPING, method='OPTIONS')
add('owner-scoped-athlete-denied', 'owner', SETTINGS, 403, SCOPED_DENIED, headers=SCOPED)
add('wrong-method-put', 'owner', SETTINGS, 405, method('PUT'), method='PUT', body={})
add('wrong-method-patch', 'owner', SETTINGS, 405, method('PATCH'), method='PATCH', body={})
add('options', 'owner', SETTINGS, 200, options('Profile Settings'), method='OPTIONS')
add('athlete-get-denied', 'athlete', SETTINGS, 403, ATHLETE)
add('athlete-post-denied', 'athlete', SETTINGS, 403, ATHLETE, method='POST', body={'enumerate_invoices': 'x'})
add('owner-without-association-server-error', 'owner_without_association', SETTINGS, 500, SERVER)
add('owner-without-association-post-server-error', 'owner_without_association', SETTINGS, 500, SERVER, method='POST', body=[])
# The first request of any method creates the missing membership card configuration.
add('owner-get-creates-card', 'owner', SETTINGS, 200, settings_response(OWNER, CARD),
    effect={'card_create': {'actor': 'owner', 'association': 'own_association'}})
add('full-collaborator-get', 'full_collaborator', SETTINGS, 200, settings_response(OWNER, CARD))
add('restricted-get-with-read', 'restricted_collaborator', SETTINGS, 200, settings_response(OWNER, CARD))
add('admin-impersonating-owner-get', 'admin', SETTINGS, 200, settings_response(OWNER, CARD), headers=ADMIN_OWNER)
add('foreign-owner-post-invalid-creates-own-card', 'foreign_owner', SETTINGS, 400, {'non_field_errors':
    ['Invalid data. Expected a dictionary, but got list.']}, method='POST', body=[],
    effect={'card_create': {'actor': 'foreign_owner', 'association': 'foreign_association'}})

# --- profile/settings POST validation (every field rule) -------------------------------
add('post-invalid-values', 'owner', SETTINGS, 400, {
    'enumerate_invoices': ['Must be a valid boolean.'], 'online_payments': ['This field may not be null.'],
    'balance_sheet_year': ['"5" is not a valid choice.'],
    'balance_sheet_start_day': ['Ensure this value is less than or equal to 31.'],
    'balance_sheet_start_month': ['Ensure this value is greater than or equal to 0.'],
    'starting_number_invoices': ['Ensure this value is less than or equal to 32767.'],
    'membership_starting_number': ['A valid integer is required.'],
    'subscription_duration': ['"0" is not a valid choice.'],
    'default_membership_type': ['Ensure this field has no more than 255 characters.'],
    'subscription_start_day': ['Ensure this value is greater than or equal to 0.'],
    'subscription_end_day': ['A valid integer is required.'],
    'subscription_end_month': ['Ensure this value is less than or equal to 12.'],
    'default_payment_category': ['“abc” is not a valid UUID.'],
    'default_payment_category_courses': [f'Invalid pk "{UNKNOWN}" - object does not exist.']},
    method='POST', body={'enumerate_invoices': 'maybe', 'online_payments': None, 'balance_sheet_year': 5, 'balance_sheet_start_day': 32,
                         'balance_sheet_start_month': -1, 'starting_number_invoices': 40000, 'membership_starting_number': 'abc',
                         'subscription_duration': '0', 'default_membership_type': 'x' * 256, 'subscription_start_day': '-3',
                         'subscription_end_day': 1.5, 'subscription_end_month': 13, 'default_payment_category': 'abc',
                         'default_payment_category_courses': UNKNOWN, 'dark_mode': 'YES'})
add('post-invalid-types', 'owner', SETTINGS, 400, {
    'auto_archive': ['Must be a valid boolean.'], 'dark_mode': ['Must be a valid boolean.'],
    'temporary_invoice_deletion': ['Must be a valid boolean.'],
    'subscription_start_month': ['A valid integer is required.'], 'balance_sheet_year': ['"True" is not a valid choice.'],
    'membership_duration': ['"1.0" is not a valid choice.'], 'starting_number_invoices': ['String value too large.'],
    'membership_starting_number': ['Ensure this value is less than or equal to 2147483647.'],
    'subscription_end_month': ['This field may not be null.'],
    'default_payment_category': ['Incorrect type. Expected pk value, received bool.'],
    # queryset.get(pk=[...]): the model UUIDField's ValidationError quotes the Python repr.
    'default_payment_category_courses': [f"“['{CATEGORIES['own']}']” is not a valid UUID."],
    'default_membership_type': ['Not a valid string.']},
    method='POST', body={'auto_archive': [], 'dark_mode': 2, 'temporary_invoice_deletion': {'a': 1}, 'subscription_start_month': True,
                         'balance_sheet_year': True, 'membership_duration': 1.0, 'starting_number_invoices': '1' * 1001,
                         'membership_starting_number': 3000000000, 'subscription_end_month': None, 'default_payment_category': True,
                         'default_payment_category_courses': [CATEGORIES['own']], 'default_membership_type': False,
                         'custom_end_date': 'On', 'subscription_end_day': '7.000'})
# The tenant-scoped relation raises PermissionDenied during field validation,
# escaping the earlier field errors.
add('post-foreign-category-forbidden', 'owner', SETTINGS, 403, FORBIDDEN, method='POST',
    body={'enumerate_invoices': 'maybe', 'default_payment_category': CATEGORIES['foreign']})
add('post-null-body', 'owner', SETTINGS, 400, {'non_field_errors': ['No data provided']}, method='POST', raw_body='null',
    content_type='application/json')
# Every serializer field is optional, but the view reads validated_data[...] for all.
add('post-partial-server-error', 'owner', SETTINGS, 500, SERVER, method='POST', body={'enumerate_invoices': True})
add('post-empty-server-error', 'full_collaborator', SETTINGS, 500, SERVER, method='POST', body={})

# --- profile/settings POST effects ------------------------------------------------------
stored = {'enumerate_invoices': True, 'online_payments': False, 'balance_sheet_year': 2, 'balance_sheet_start_day': 1,
          'balance_sheet_start_month': 9, 'starting_number_invoices': -5, 'membership_starting_number': 120,
          'default_membership_type': 'Socio ordinario', 'subscription_duration': 4, 'membership_duration': 5,
          'subscription_end_day': 7, 'default_payment_category': CATEGORIES['own'],
          'default_payment_category_courses': CATEGORIES['global'], 'dark_mode': True, 'custom_end_date': True}
body = full_body(enumerate_invoices='on', online_payments='No', balance_sheet_year='2', balance_sheet_start_month='9',
                 starting_number_invoices=' -5 ', membership_starting_number=120.0, default_membership_type='  Socio ordinario ',
                 subscription_duration=4, membership_duration='5', subscription_end_day='7.000', default_payment_category=CATEGORIES['own'],
                 default_payment_category_courses=CATEGORIES['global'], dark_mode=1, custom_end_date='TRUE', ignored='value')
# The user is saved before request.data['membership_card_configuration'] raises.
add('post-without-card-saves-user-then-server-error', 'owner', SETTINGS, 500, SERVER, method='POST', body=body,
    effect={'user': saved('owner', stored)})
template = {'sections': ['nome', 'numero'], 'colore': 'blu è', 'size': 9007199254740993, 'scale': 1.5}
body = full_body(membership_card_configuration={'emit_only_on_approval': True, 'customized_template': template, 'extra': 1})
add('post-full-collaborator-card', 'full_collaborator', SETTINGS, 200, SUCCESS, method='POST', body=body,
    effect={'user': saved('full_collaborator', {}), 'card': card_saved('full_collaborator', True, template)})
add('get-after-post', 'owner', SETTINGS, 200, settings_response(OWNER, CARD))
add('post-int-boolean-audits-str-change', 'owner', SETTINGS, 200, SUCCESS, method='POST',
    body=full_body(membership_card_configuration={'emit_only_on_approval': 1, 'customized_template': template}),
    effect={'user': saved('owner', {}), 'card': card_saved('owner', 1, template)})
# auditlog writes the update in pre_save; the model BooleanField then rejects the value.
body = full_body(dark_mode=False, membership_card_configuration={'emit_only_on_approval': 'maybe', 'customized_template': None})
add('post-card-invalid-boolean-keeps-audit', 'owner', SETTINGS, 500, SERVER, method='POST', body=body,
    effect={'user': saved('owner', {'dark_mode': False}), 'card': card_saved('owner', 'maybe', None, stored=False)})
body = full_body(membership_card_configuration={'emit_only_on_approval': False})
add('post-card-missing-template-server-error', 'owner', SETTINGS, 500, SERVER, method='POST', body=body, effect={'user': saved('owner', {})})
body = full_body(membership_card_configuration='not a dict')
add('post-card-not-dict-server-error', 'owner', SETTINGS, 500, SERVER, method='POST', body=body, effect={'user': saved('owner', {})})
# A start date the serializer accepts but datetime() rejects breaks the next GET.
body = full_body(balance_sheet_start_day=31, balance_sheet_start_month=2, default_payment_category=CATEGORIES['own_archived'],
                 default_payment_category_courses=None, default_membership_type=None,
                 membership_card_configuration={'emit_only_on_approval': 'False', 'customized_template': {'b': [], 'a': None}})
add('post-impossible-start-date', 'owner', SETTINGS, 200, SUCCESS, method='POST', body=body,
    effect={'user': saved('owner', {'balance_sheet_start_day': 31, 'balance_sheet_start_month': 2,
                                    'default_payment_category': CATEGORIES['own_archived'], 'default_payment_category_courses': None,
                                    'default_membership_type': None}),
            'card': card_saved('owner', 'False', {'b': [], 'a': None})})
add('get-impossible-start-date-server-error', 'owner', SETTINGS, 500, SERVER)
body = full_body(balance_sheet_start_day=15, balance_sheet_start_month=3,
                 membership_card_configuration={'emit_only_on_approval': False, 'customized_template': None})
add('post-restores-start-date', 'owner', SETTINGS, 200, SUCCESS, method='POST', body=body,
    effect={'user': saved('owner', {'balance_sheet_start_day': 15, 'balance_sheet_start_month': 3}),
            'card': card_saved('owner', False, None)})
add('get-after-restore', 'full_collaborator', SETTINGS, 200, settings_response(OWNER, CARD))
# HTML form input: absent booleans are False; request.data[...] of a QueryDict is a string.
FORM = (('balance_sheet_year', '3'), ('balance_sheet_start_day', '15'), ('balance_sheet_start_month', '3'),
        ('starting_number_invoices', '-5'), ('subscription_duration', '4'), ('membership_duration', '5'),
        ('membership_starting_number', '120'), ('default_membership_type', ''), ('subscription_start_day', '1'),
        ('subscription_start_month', '1'), ('subscription_end_day', '7'), ('subscription_end_month', '12'),
        ('default_payment_category', ''), ('default_payment_category_courses', CATEGORIES['own']), ('online_payments', 'true'),
        ('dark_mode', 'x'), ('dark_mode', 'off'), ('membership_card_configuration', 'x'))
present = {key for key, _ in FORM}
form_stored = {key: False for key, value in DEFAULTS.items() if isinstance(value, bool) and key not in present}
form_stored.update(balance_sheet_year=3, online_payments=True, dark_mode=False, default_membership_type='',
                   default_payment_category=None, default_payment_category_courses=CATEGORIES['own'])
add('post-form-saves-user-then-server-error', 'owner', SETTINGS, 500, SERVER, method='POST',
    raw_body='&'.join(f'{key}={value}' for key, value in FORM), content_type='application/x-www-form-urlencoded',
    effect={'user': saved('owner', form_stored)})
# Field.get_value: an empty HTML value of an optional, non-null field is `empty` (skipped).
add('post-form-invalid', 'owner', SETTINGS, 400, {'dark_mode': ['Must be a valid boolean.']},
    method='POST', raw_body='balance_sheet_year=&dark_mode=maybe&subscription_end_day=&default_payment_category=',
    content_type='application/x-www-form-urlencoded')

# --- profile/integrations ---------------------------------------------------------------
add('integrations-anonymous', 'anonymous', INTEGRATIONS, 401, ANONYMOUS, method='PATCH', body={})
add('integrations-admin-without-session', 'admin', INTEGRATIONS, 403, ADMIN)
add('integrations-owner-scoped-athlete-denied', 'owner', INTEGRATIONS, 403, SCOPED_DENIED, headers=SCOPED)
add('integrations-association-session-denied', 'owner', INTEGRATIONS, 403, CREDENTIALS_DENIED, method='PATCH',
    body={'review_url': 'x'}, headers=CREDENTIALS)
add('integrations-wrong-method', 'owner', INTEGRATIONS, 405, method('POST'), method='POST', body={})
add('integrations-options', 'owner', INTEGRATIONS, 200, options('Profile Integrations'), method='OPTIONS')
add('integrations-athlete-denied', 'athlete', INTEGRATIONS, 403, ATHLETE, method='PATCH', body={'review_url': 'x'})
add('integrations-owner-without-association-server-error', 'owner_without_association', INTEGRATIONS, 500, SERVER)
add('integrations-owner-get', 'owner', INTEGRATIONS, 200, dict(REVIEW))
add('integrations-foreign-owner-get', 'foreign_owner', INTEGRATIONS, 200, FOREIGN_REVIEW)
add('integrations-admin-impersonating-owner-get', 'admin', INTEGRATIONS, 200, dict(REVIEW), headers=ADMIN_OWNER)
effect = review_saved('owner', 'https://example.test/recensioni', True)
add('integrations-owner-patch', 'owner', INTEGRATIONS, 200, {'review_url': 'https://example.test/recensioni', 'review_url_enabled': True},
    method='PATCH', body={'review_url': 'https://example.test/recensioni', 'review_url_enabled': True, 'other': 1},
    effect={'association': effect})
add('integrations-empty-patch-no-audit', 'full_collaborator', INTEGRATIONS, 200, dict(REVIEW), method='PATCH', body={},
    effect={'association': review_saved('full_collaborator', REVIEW['review_url'], True)})
# The raw value is echoed; auditlog compares str() values.
add('integrations-string-true-no-audit', 'owner', INTEGRATIONS, 200, {'review_url': 'https://example.test/recensioni',
    'review_url_enabled': 'True'}, method='PATCH', body={'review_url_enabled': 'True'},
    effect={'association': review_saved('owner', REVIEW['review_url'], 'True')})
# A collaborator with only other.settings.read may PATCH.
add('integrations-restricted-read-patches', 'restricted_collaborator', INTEGRATIONS, 200, {'review_url': 5, 'review_url_enabled': 0},
    method='PATCH', body={'review_url': 5, 'review_url_enabled': 0}, effect={'association': review_saved('restricted_collaborator', 5, 0, url_text='5')})
add('integrations-invalid-boolean-keeps-audit', 'owner', INTEGRATIONS, 500, SERVER, method='PATCH', body={'review_url_enabled': 'yes'},
    effect={'association': review_saved('owner', '5', 'yes', stored=False)})
add('integrations-null-boolean-keeps-audit', 'owner', INTEGRATIONS, 500, SERVER, method='PATCH', body={'review_url_enabled': None},
    effect={'association': review_saved('owner', '5', None, stored=False)})
add('integrations-too-long-url-keeps-audit', 'owner', INTEGRATIONS, 500, SERVER, method='PATCH', body={'review_url': 'u' * 256},
    effect={'association': review_saved('owner', 'u' * 256, False, stored=False)})
add('integrations-list-body-server-error', 'owner', INTEGRATIONS, 500, SERVER, method='PATCH', body=[])
add('integrations-form-patch', 'owner', INTEGRATIONS, 200, {'review_url': 'modulo è', 'review_url_enabled': 'True'}, method='PATCH',
    raw_body='review_url=modulo+%C3%A8&review_url_enabled=1&review_url_enabled=True', content_type='application/x-www-form-urlencoded',
    effect={'association': review_saved('owner', 'modulo è', 'True')})
add('integrations-null-url', 'full_collaborator', INTEGRATIONS, 200, {'review_url': None, 'review_url_enabled': False}, method='PATCH',
    body={'review_url': None, 'review_url_enabled': False}, effect={'association': review_saved('full_collaborator', None, False)})
add('integrations-get-after-patch', 'owner', INTEGRATIONS, 200, dict(REVIEW))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Profile settings and integrations authored sequence with audit effects',
                              'ids': IDS, 'categories': CATEGORIES, 'foreign_review': FOREIGN_REVIEW, 'fields': list(FIELDS),
                              'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
