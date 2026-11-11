"""Author the subscription/add HTTP sequence (subscription-create.md).

subscription_add (@api_view(['POST']), AllowAny, subscriptions_views.py):
_prepare_subscription_request keeps request.user for authenticated requests
(the owner for collaborators); m=only_associate is the quick mode, which for
an owner sets data['sport_association']; without every required key
(new_user_account, associate_data, associate_tutor_data, signature) and
outside the quick branch the answer is 400 {"msg": "validation errors."}.

create_subscription runs in one transaction.atomic(), in this order:
dateutil's parse of str(associate_data['born_date']) (500 when it fails); with
a tutor dict: address city/cap copies, e-mail clean-up (check_email: an
invalid one is dropped) and sex upper-casing; user.sport_association (an
athlete is PermissionDenied 'User is not a sport association'); the season
of now(); SubscriptionManager.subscription_exists on the raw names/tax code
(a current one is DuplicateSubscriptionError, 409); UserAccountSerializer;
AssociateSerializer(raise_exception) and save (tax-code get_or_create, then
associate.user = user and save); the tutor when the associate is a minor;
SignatureRequestSerializer(raise_exception); the medical certificate by id
(a None medical_certificate is a 500 in the elif); fees (subscription and
membership, plans), the payment (an unpaid same-day subscription payment of
the same amount is reused; Payment.save sets 'Iscrizione' and the default
bank account); the next membership number; Subscription.objects.create
(new_subscription on commit); SubscriptionMembership for types 2 and 3.
After the commit, in registration order: check_workflows_trigger
(new_subscription, UUIDs), print_document_subscription(str(id), the
Authorization header), then NotificationService.send_notification(owner,
...): the broadcast_<owner> rows (created once, audited), the Redis store
append (uuid1 id, expires now+30 days), the user_broadcasts cache and the
notification_push group message to notifications_user_<owner>.
Response {"status": "success", "payment_id": ..., "amount": float or 0}.

Run with ``python3 -I``.
"""
import base64
import json
from pathlib import Path

OUTPUT = Path(__file__).with_name('subscription_create_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER = IDS['owner']
ids = iter(f'a1000000-0000-4000-8000-{930001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
UNKNOWN = 'a1000000-0000-4000-8000-000000939999'

ASSOCIATES = {
    'known': {'id': next(ids), 'association': OWN, 'first': 'Marco', 'last': 'Noto', 'tax_code': 'NTOMRC80A01H501Z', 'born': '1980-01-01',
              'user': None, 'email': 'vecchia@example.test'},
    'known_abroad': {'id': next(ids), 'association': FOREIGN, 'first': 'Marco', 'last': 'Noto', 'tax_code': 'NTOMRC80A01H501Z',
                     'born': '1980-01-01', 'user': 'foreign_owner', 'email': None},
    'current': {'id': next(ids), 'association': OWN, 'first': 'Dora', 'last': 'Doppia', 'tax_code': 'DPPDRO90A41H501X', 'born': '1990-01-01',
                'user': 'owner', 'email': None},
    'archived': {'id': next(ids), 'association': OWN, 'first': 'Aldo', 'last': 'Archivio', 'tax_code': 'RCHLDA85A01H501Q', 'born': '1985-01-01',
                 'user': 'owner', 'email': None},
    'payer': {'id': next(ids), 'association': OWN, 'first': 'Paola', 'last': 'Pagante', 'tax_code': 'PGNPLA75A41H501R', 'born': '1975-01-01',
              'user': 'owner', 'email': None},
}
CERTIFICATES = {
    'unowned': {'id': next(ids), 'user': None},
    'owned': {'id': next(ids), 'user': 'owner'},
    # The owner wizard sends the uploaded certificate's id with its expiration date.
    'expiring': {'id': next(ids), 'user': None},
    'dated': {'id': next(ids), 'user': 'owner', 'expiration': '2026-01-01'},
    'kept': {'id': next(ids), 'user': 'owner'},
}
SUBSCRIPTIONS = {
    # The whole season: always current, so a new one for the same person is a duplicate.
    'current': {'id': next(ids), 'associate': 'current', 'start': '$season_start', 'end': '$season_end', 'archived': False, 'number': '41'},
    'archived': {'id': next(ids), 'associate': 'archived', 'start': '$season_start', 'end': '$season_end', 'archived': True, 'number': '7'},
    'abroad': {'id': next(ids), 'associate': 'known_abroad', 'start': '$season_start', 'end': '$season_end', 'archived': False,
               'number': '900', 'association': FOREIGN},
    'text_number': {'id': next(ids), 'associate': 'archived', 'start': '2020-01-01', 'end': '2020-12-31', 'archived': True,
                    'number': '5000A'},
}
# The quick re-subscription (m=only_associate): members with older subscriptions and certificates.
ASSOCIATES['quick'] = {'id': next(ids), 'association': OWN, 'first': 'Quinto', 'last': 'Rapido', 'tax_code': 'RPDQNT80A01H501U',
                       'born': '1980-01-01', 'user': 'owner', 'email': None}
ASSOCIATES['quick_plan'] = {'id': next(ids), 'association': OWN, 'first': 'Pia', 'last': 'Pianificata', 'tax_code': 'PNFPIA80A41H501V',
                            'born': '1980-01-01', 'user': 'owner', 'email': None}
ASSOCIATES['quick_abroad'] = {'id': next(ids), 'association': FOREIGN, 'first': 'Elio', 'last': 'Estero', 'tax_code': 'STRLEI80A01H501W',
                              'born': '1980-01-01', 'user': 'foreign_owner', 'email': None}
ASSOCIATES['quick_no_account'] = {'id': next(ids), 'association': OWN, 'first': 'Sara', 'last': 'Senzaconto', 'tax_code': 'SNZSRA80A41H501X',
                                  'born': '1980-01-01', 'user': None, 'email': None}
# Ids added later come from their own range so the earlier ones stay stable.
extra_ids = iter(f'a1000000-0000-4000-8000-{939001 + n:012d}' for n in range(50))
ASSOCIATES['quick_fallback'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Fabio', 'last': 'Ripiego', 'tax_code': 'RPGFBA80A01H501Y',
                                'born': '1980-01-01', 'user': 'athlete', 'email': None}
CERTIFICATES['quick_older'] = {'id': next(ids), 'user': 'owner', 'expiration': '2025-06-30'}
CERTIFICATES['quick_latest'] = {'id': next(ids), 'user': 'owner', 'expiration': '2026-06-30'}
CERTIFICATES['quick_undated'] = {'id': next(ids), 'user': 'owner'}
SUBSCRIPTIONS['quick_2024'] = {'id': next(ids), 'associate': 'quick', 'start': '2024-01-01', 'end': '2024-12-31', 'archived': False,
                               'number': '3', 'medical': 'quick_older'}
SUBSCRIPTIONS['quick_2023'] = {'id': next(ids), 'associate': 'quick', 'start': '2023-01-01', 'end': '2023-12-31', 'archived': True,
                               'number': '2', 'medical': 'quick_latest'}
SUBSCRIPTIONS['quick_2022'] = {'id': next(ids), 'associate': 'quick', 'start': '2022-01-01', 'end': '2022-12-31', 'archived': True,
                               'number': '1', 'medical': 'quick_undated'}
PAYMENTS = {
    # An unpaid subscription payment of today with the amount the next case charges: reused.
    'reusable': {'id': next(ids), 'associate': 'payer', 'amount': '25.00', 'paid': False, 'date': '$today'},
}
ACCOUNT = next(ids)
PLAN = {'subscription': [{'id': 'plan-base', 'name': 'Piano base', 'subscription_fee': 40}],
        'membership': [{'id': 7, 'name': 'Tessera gold', 'membership_fee': '12.5'}]}

# Association/owner settings applied (audit disabled) before a case; they persist.
BASE_SETTINGS = {'association': {'enable_quotes_management': True, 'subscription_fee': '25.00', 'membership_fee': None,
                                 'multiple_subscription_fee': False, 'multiple_membership_fee': False,
                                 'subscription_fee_plans': PLAN['subscription'], 'membership_fee_plans': PLAN['membership']},
                 'owner': {'subscription_start_day': 1, 'subscription_start_month': 1, 'subscription_duration': 2, 'membership_duration': 2,
                           'membership_starting_number': 0, 'default_membership_type': '', 'show_zero_payments': False}}

cases = []
NUMBER = [41]
BROADCAST = [False]
CATEGORY_TITLE = 'Quota tesseramento'


def add(name, actor, status, response, *, body=None, method='POST', query='', headers=None, effect=None, setup=None):
    cases.append({'name': f'subscription-create-{name}', 'actor': actor, 'method': method, 'path': '/subscription/add' + query,
                  'headers': headers or {}, 'status': status, 'response': response, 'effect': effect, 'body': body,
                  'setup': setup})


def person(first, last, tax_code, born='15/03/1990', **extra):
    return {'first_name': first, 'last_name': last, 'tax_code': tax_code, 'born_date': born, 'sex': 'M', **extra}


def request(associate, *, tutor=None, signature=None, account=None, **extra):
    return {'new_user_account': account if account is not None else {'new_member': False}, 'associate_data': associate,
            'associate_tutor_data': tutor, 'signature': signature if signature is not None else {'there_is_signature': False, 'data': ''},
            **extra}


def success(actor, full_name, *, associate, subscription, payment=None, membership=None, tutor=None, relation=False, medical=None,
            associate_audit=None, extra_audit=()):
    """The effect of one created subscription: rows, audit order, tasks and the notification.

    associate: {'match': 'new' | seed label, 'columns': {...}}; payment: None, {'match': 'new', 'columns'} or
    {'match': <seed label>}; subscription/membership: authored columns ('$number' is the next membership number).
    associate_audit: the associate's entries [(action, overrides)] (default: create + user update).
    """
    NUMBER[0] += 1
    number = str(NUMBER[0]) if '$explicit_number' not in subscription else subscription.pop('$explicit_number')
    subscription = {**subscription, 'subscription_number': number}
    if membership is not None:
        membership = {**membership, 'membership_number': number}
    audit = []
    created_user = {'user': ['None', 'None']}
    entries = [{'model': 'associate', 'action': action, 'ref': 'associate', 'changes': created_user if action == 0 else overrides,
                'index': [OWN, 'direct']}
               for action, overrides in (associate_audit if associate_audit is not None else [(0, {}), (1, {'user': ['None', OWNER]})])]
    # The serializer's save comes first; associate.user = user and save() come after the tutor.
    audit.extend(entries[:-1])
    if tutor is not None:
        audit.append({'model': 'associate', 'action': 0, 'ref': 'tutor', 'changes': created_user, 'index': [OWN, 'direct']})
        audit.append({'model': 'associate', 'action': 1, 'ref': 'tutor', 'changes': {'user': ['None', OWNER]}, 'index': [OWN, 'direct']})
        audit.append({'model': 'associatetutorrelation', 'action': 0, 'ref': 'relation', 'changes': {},
                      'index': [OWN, 'associate.sport_association']})
    audit.extend(entries[-1:])
    audit.extend(extra_audit)
    if medical is not None and medical.get('audit'):
        audit.append({'model': 'medicalcertificate', 'action': 1, 'ref': medical['label'], 'changes': medical['audit'], 'index': medical['index']})
    if payment is not None and payment['match'] == 'new':
        audit.append({'model': 'payment', 'action': 0, 'ref': 'payment', 'changes': payment.get('audit', {}), 'index': [OWN, 'direct']})
    audit.append({'model': 'subscription', 'action': 0, 'ref': 'subscription', 'changes': {}, 'index': [OWN, 'direct']})
    if membership is not None:
        rendered = membership.pop('$audit', {})
        audit.append({'model': 'subscriptionmembership', 'action': 0, 'ref': 'membership', 'changes': rendered,
                      'index': [OWN, 'direct']})
        # The following save() compares the stored price with the in-memory number.
        if membership['price'] is not None:
            audit.append({'model': 'subscriptionmembership', 'action': 1, 'ref': 'membership',
                          'changes': {'price': [membership['price'], rendered['price'][1]]}, 'index': [OWN, 'direct']})
    first_notification = not BROADCAST[0]
    BROADCAST[0] = True
    actor_association = {'owner': OWN, 'admin': IDS['admin_association']}.get(actor)
    if first_notification:
        for model in ('notificationsbroadcast', 'usernotificationsbroadcast'):
            audit.append({'model': model, 'action': 0, 'ref': model, 'changes': {}, 'app': 'notifications',
                          'index': [actor_association, 'actor.sport_association'] if actor_association else None})
    return {'actor': actor, 'associate': associate, 'tutor': tutor, 'relation': relation, 'payment': payment, 'subscription': subscription,
            'membership': membership, 'medical': medical, 'broadcast_created': first_notification, 'audit': audit,
            'celery': [
                {'task': 'check_workflows_trigger', 'args': [],
                 'kwargs': [['trigger_type', 'new_subscription'], ['sport_association_id', {'uuid': OWN}],
                            ['subscription_id', '$subscription_uuid']]},
                {'task': 'print_document_subscription', 'args': ['$subscription', '$authorization'], 'kwargs': []},
            ],
            'notification': {'msg': f'Nuova Iscrizione aggiunta per {full_name}.', 'association': OWN}}


def created_associate(data, **columns):
    return {'match': 'new', 'columns': {'first_name': data['first_name'], 'last_name': data['last_name'],
                                        'tax_code': data.get('tax_code'), 'born_date': '-'.join(reversed(data['born_date'].split('/'))),
                                        'sex': data.get('sex', 'A'), 'sport_association_id': OWN, 'user_id': OWNER, **columns}}


def new_payment(amount, *, audit_amount, meta_categories=None, meta=None):
    return {'match': 'new', 'columns': {'amount': amount, 'meta_payment_categories': meta_categories, 'meta': meta},
            'audit': {'amount': ['None', audit_amount]}}


SUCCESS = lambda payment_amount: {'status': 'success', 'payment_id': '$new', 'amount': payment_amount}
SEASON = {'start_date': '$season_start', 'end_date': '$season_end'}
NO_PLANS = {'plan_id': None, 'plan_name': None, 'membership_plan_id': None, 'membership_plan_name': None}
VALIDATION = {'msg': 'validation errors.'}

# --- Transport, identity and the view's own checks -------------------------------------------------------
add('options', 'owner', 200, {'name': 'Subscription Add', 'description':
    'API endpoint to add a new subscription\n:param request: data: {newUserAccount, newAssociate, signature, paymentMethod}\n'
    ':return: token, refresh token', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')
add('wrong-method', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('restricted-missing-permission', 'restricted_collaborator', 403, {'detail': 'Missing permission: association.members.create'},
    body=request(person('Rita', 'Ristretta', 'RSTRTI90C41H501K')))
add('admin-without-session', 'admin', 403, {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body=request(person('Rita', 'Ristretta', 'RSTRTI90C41H501K')))
add('owner-scoped-athlete-denied', 'owner', 403, {'detail': 'Seleziona la tua associazione.'},
    headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']},
    body=request(person('Rita', 'Ristretta', 'RSTRTI90C41H501K')))
add('empty-body', 'owner', 400, VALIDATION, body={})
add('missing-signature', 'owner', 400, VALIDATION, body={k: v for k, v in request(person('Rita', 'R', 'X')).items() if k != 'signature'})
add('list-body-server-error', 'owner', 500, SERVER, body=[])
add('quick-without-associate', 'owner', 400, VALIDATION, query='?m=only_associate', body=request(person('Rita', 'R', 'X')))
add('athlete-denied', 'athlete', 403, {'detail': 'User is not a sport association'}, body=request(person('Rita', 'R', 'X')))
add('owner-without-association-server-error', 'owner_without_association', 500, SERVER, body=request(person('Rita', 'R', 'X')))
add('born-date-missing-server-error', 'owner', 500, SERVER,
    body=request({'first_name': 'Rita', 'last_name': 'Senzadata', 'tax_code': 'X', 'sex': 'F'}))
add('born-date-unparsed-server-error', 'owner', 500, SERVER, body=request(person('Rita', 'Rotta', 'X', born='non una data')))
add('born-date-none-server-error', 'owner', 500, SERVER, body=request(person('Rita', 'Rotta', 'X', born=None)))
add('medical-certificate-none-server-error', 'owner', 500, SERVER,
    body=request(person('Rita', 'Medica', 'MDCRTI90C41H501K'), medical_certificate=None))
add('subscription-type-not-a-number-server-error', 'owner', 500, SERVER,
    body=request(person('Rita', 'Tipo', 'TPIRTI90C41H501K', type='due')))

# --- Validation (400; nothing is written: the transaction rolls back) ------------------------------------
add('duplicate-current', 'owner', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=request(person('dora', 'DOPPIA', 'dppdro90a41h501x', born='01/01/1990')))
add('associate-invalid-fields', 'owner', 400, {
    'address_cap': ['Enter exactly 5 digits'], 'born_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'],
    'email': ['Enter a valid email address.'], 'sex': ['"X" is not a valid choice.']},
    body=request(person('Rita', 'Errata', 'RRTRTI90C41H501K', born='1990-03-15', sex='X', email='non-valida', address_cap='12a')))
# The birth date is first read by dateutil (the age), day first unless the text starts with the year; the serializer then
# takes DD/MM/YYYY only. Text dateutil does not read is the 500 page.
BORN_FORMAT = {'born_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.']}
for label, born in (('dashed', '15-03-1990'), ('dotted', '15.03.1990'), ('month-name', 'March 15, 1990'), ('with-time', '1990-03-15 10:30'),
                    ('integer', 19900315), ('year-only', '1990')):
    add(f'born-date-{label}-serializer-rejects', 'owner', 400, BORN_FORMAT, body=request(person('Rita', 'Formato', 'FRMRTI90C41H501K', born=born)))
for label, born in (('italian-month', '15 marzo 1990'), ('five-numbers', '1 2 3 4 5'), ('false', False)):
    add(f'born-date-{label}-server-error', 'owner', 500, SERVER, body=request(person('Rita', 'Formato', 'FRMRTI90C41H501K', born=born)))
add('signature-data-required', 'owner', 400, {'data': ['This field is required.']},
    body=request(person('Rita', 'Firma', 'FRMRTI90C41H501K'), signature={'there_is_signature': False}))
add('signature-blank-when-signed', 'owner', 400, {'data': ['La firma è obbligatoria.']},
    body=request(person('Rita', 'Firma', 'FRMRTI90C41H501K'), signature={'there_is_signature': True, 'data': ''}))
add('membership-plan-required', 'owner', 400, {'msg': 'Membership plan id is required.', 'code': '400'},
    setup={'association': {'multiple_membership_fee': True}},
    body=request(person('Rita', 'Piano', 'PNARTI90C41H501K')))
add('membership-plan-not-found', 'owner', 400, {'msg': 'Membership plan not found.', 'code': '400'},
    body=request(person('Rita', 'Piano', 'PNARTI90C41H501K'), membership_plan_id='nessuno'))

# --- Success paths ---------------------------------------------------------------------------------------
basic = person('Luca', 'Bianchi', 'bnclcu90c15h501a', born='15/03/1990', address='Via Roma 1', phone='3331234567')
add('basic', 'owner', 200, SUCCESS(25.0), setup={'association': {'multiple_membership_fee': False}}, body=request(basic),
    effect=success('owner', 'Luca Bianchi', associate=created_associate({**basic, 'tax_code': 'BNCLCU90C15H501A'}, address='Via Roma 1',
                                                                        phone='3331234567'),
                   payment=new_payment('25.00', audit_amount='25.0'),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

with_membership = person('Anna', 'Verdi', 'VRDNNA92D50H501B', born='10/04/1992')
add('membership-fee', 'full_collaborator', 200, SUCCESS(35.0), setup={'association': {'membership_fee': '10.00'}},
    body=request(with_membership, custom_data={'tipo': 'atleta', 'zona': 'nord', 'type_of_associate': ''}, additional_fields={'taglia': 'M'}),
    effect=success('full_collaborator', 'Anna Verdi', associate=created_associate(with_membership),
                   payment=new_payment('35.00', audit_amount='35.0', meta_categories=[
                       {'payment_category_id': '$category', 'amount': 10.0, 'subject': 0, 'title': CATEGORY_TITLE, 'id': None}]),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '',
                                 'custom_data': {'tipo': 'atleta', 'zona': 'nord', 'type_of_associate': ''}, 'additional_fields': {'taglia': 'M'},
                                 **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': '10.00', '$audit': {'price': ['None', '10.0']}}))

member_only = person('Bruno', 'Neri', 'NRIBRN88E10H501C', born='10/05/1988', type='3', role=' 5 ')
add('member-only', 'owner', 200, SUCCESS(10.0), body=request(member_only),
    effect=success('owner', 'Bruno Neri', associate=created_associate({k: v for k, v in member_only.items() if k not in ('type', 'role')}),
                   payment=new_payment('10.00', audit_amount='10.0'),
                   subscription={'type': 3, 'role': 5, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': '10.00', '$audit': {'price': ['None', '10.0']}}))

associate_only = person('Carla', 'Gialli', 'GLLCRL95H50H501D', born='10/06/1995', type=1, subscription_number='77')
add('associate-only-explicit-number', 'owner', 200, SUCCESS(25.0), body=request(associate_only),
    effect=success('owner', 'Carla Gialli', associate=created_associate(
        {k: v for k, v in associate_only.items() if k not in ('type', 'subscription_number')}),
        payment=new_payment('25.00', audit_amount='25.0'),
        subscription={'type': 1, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', '$explicit_number': '77',
                      **SEASON}))
NUMBER[0] = 77  # the next number follows the stored maximum

plans = person('Dario', 'Blu', 'BLUDRA91A01H501E', born='01/01/1991')
add('plans', 'owner', 200, SUCCESS(52.5),
    setup={'association': {'multiple_subscription_fee': True, 'multiple_membership_fee': True},
           'owner': {'default_membership_type': 'Tessera FIDAL', 'membership_starting_number': 500}},
    body=request(plans, plan_id='plan-base', membership_plan_id=7),
    effect=success('owner', 'Dario Blu', associate=created_associate(plans),
                   payment=new_payment('52.50', audit_amount='52.5', meta_categories=[
                       {'payment_category_id': '$category', 'amount': 12.5, 'subject': 0, 'title': 'Tessera gold', 'id': '7'}],
                       meta={'subscription_data': {'subscription_fee': 40, 'name': 'Piano base', 'id': 'plan-base'}}),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'subscription_type': 'Tessera FIDAL', '$explicit_number': '501',
                                 'meta': {'plan_id': 'plan-base', 'plan_name': 'Piano base', 'membership_plan_id': 7,
                                          'membership_plan_name': 'Tessera gold'}, **SEASON},
                   membership={'membership_type': 'Tessera FIDAL', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': '12.50', '$audit': {'price': ['None', '12.5']}}))
NUMBER[0] = 501

plan_missing = person('Elsa', 'Viola', 'VLILSE93A41H501F', born='01/01/1993')
add('subscription-plan-missing-falls-back', 'owner', 200, SUCCESS(37.5),
    setup={'owner': {'default_membership_type': '', 'membership_starting_number': 0, 'subscription_duration': 1, 'membership_duration': 1}},
    body=request(plan_missing, plan_id='sconosciuto', membership_plan_id='7'),
    effect=success('owner', 'Elsa Viola', associate=created_associate(plan_missing),
                   payment=new_payment('37.50', audit_amount='37.5', meta_categories=[
                       {'payment_category_id': '$category', 'amount': 12.5, 'subject': 0, 'title': 'Tessera gold', 'id': '7'}]),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'subscription_type': '',
                                 'meta': {'plan_id': 'sconosciuto', 'plan_name': None, 'membership_plan_id': '7',
                                          'membership_plan_name': 'Tessera gold'},
                                 'start_date': '$today', 'end_date': '$today_next_year'},
                   membership={'membership_type': 'Tessera gold', 'start_date': '$season_start', 'end_date': '$season_start_next_year_eve',
                               'price': '12.50', '$audit': {'price': ['None', '12.5']}}))

zero = person('Franco', 'Zero', 'ZROFNC80A01H501G', born='01/01/1980')
add('zero-fee-no-payment', 'owner', 200, {'status': 'success', 'payment_id': None, 'amount': 0},
    setup={'association': {'subscription_fee': '0.00', 'membership_fee': None, 'multiple_subscription_fee': False,
                           'multiple_membership_fee': False},
           'owner': {'subscription_duration': 3, 'membership_duration': 3}},
    body=request(zero),
    effect=success('owner', 'Franco Zero', associate=created_associate(zero),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '',
                                 'start_date': '$today', 'end_date': '$season_end'},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$today', 'end_date': '$season_end',
                               'price': None}))

shown_zero = person('Gino', 'Mostrato', 'MSTGNI80A01H501H', born='01/01/1980')
add('zero-fee-shown', 'owner', 200, SUCCESS(0.0), setup={'owner': {'show_zero_payments': True, 'subscription_duration': 2,
                                                                   'membership_duration': 2}},
    body=request(shown_zero),
    effect=success('owner', 'Gino Mostrato', associate=created_associate(shown_zero),
                   payment=new_payment('0.00', audit_amount='0.0'),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

no_quotes = person('Ivo', 'Libero', 'LBRIVI80A01H501I', born='01/01/1980')
add('quotes-disabled', 'owner', 200, {'status': 'success', 'payment_id': None, 'amount': 0},
    setup={'association': {'enable_quotes_management': False, 'subscription_fee': '25.00'}, 'owner': {'show_zero_payments': False}},
    body=request(no_quotes),
    effect=success('owner', 'Ivo Libero', associate=created_associate(no_quotes),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': '0.00', '$audit': {'price': ['None', '0']}}))

# The known associate: the tax code matches case-insensitively; its fields are updated.
known = person('Marco', 'Noto', 'ntomrc80a01h501z', born='01/01/1980', email='nuova@example.test')
add('known-associate-updated', 'admin', 200, SUCCESS(25.0), setup={'association': {'enable_quotes_management': True}},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}, body=request(known),
    effect=success('admin', 'Marco Noto', associate={'match': 'known', 'columns': {'email': 'nuova@example.test', 'user_id': OWNER,
                                                                                  'sex': 'M'}},
                   associate_audit=[(1, {'email': ['vecchia@example.test', 'nuova@example.test'], 'sex': ['A', 'M']}),
                                    (1, {'user': ['None', OWNER]})],
                   payment=new_payment('25.00', audit_amount='25.0'),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

payer = person('Paola', 'Pagante', 'PGNPLA75A41H501R', born='01/01/1975')
add('reuses-unpaid-payment', 'owner', 200, {'status': 'success', 'payment_id': PAYMENTS['reusable']['id'], 'amount': 25.0},
    body=request(payer),
    effect=success('owner', 'Paola Pagante', associate={'match': 'payer', 'columns': {'sex': 'M'}},
                   associate_audit=[(1, {'sex': ['A', 'M']})],
                   payment={'match': 'reusable'},
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

archived = person('Aldo', 'Archivio', 'RCHLDA85A01H501Q', born='01/01/1985')
add('archived-subscription-not-duplicate', 'owner', 200, SUCCESS(25.0), body=request(archived, medical_certificate={
    'medical_id': CERTIFICATES['unowned']['id']}),
    effect=success('owner', 'Aldo Archivio', associate={'match': 'archived', 'columns': {'sex': 'M'}},
                   associate_audit=[(1, {'sex': ['A', 'M']})],
                   medical={'label': 'unowned', 'columns': {'user_id': OWNER}, 'audit': {'user': ['None', OWNER]},
                            'index': [OWN, 'actor.sport_association']},
                   payment=new_payment('25.00', audit_amount='25.0'),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '',
                                 'medical_id': CERTIFICATES['unowned']['id'], **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

# Another association's person with the same tax code is a different associate.
abroad = person('Marco', 'Noto', 'NTOMRC80A01H501Z', born='01/01/1980')
add('duplicate-after-create', 'owner', 409,
    {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=request(abroad, medical_certificate={'medical_id': CERTIFICATES['owned']['id']}))

minor = person('Sara', 'Piccola', 'PCCSRA15A41H501L', born='01/01/2015', is_minor=True, email=' sara..piccola @example.test ',
               sex='f', address_city='Torino')
tutor = person('Mario', 'Piccolo', 'PCCMRA80A01H501M', born='01/01/1980', email='non valida', sex='m')
add('minor-with-tutor', 'owner', 200, SUCCESS(25.0), body=request(minor, tutor=tutor, medical_certificate={'medical_id': UNKNOWN}),
    effect=success('owner', 'Sara Piccola', associate=created_associate(
        {**minor, 'sex': 'F'}, is_minor=True, email='sara.piccola@example.test', address_city='Torino'),
        tutor=created_associate({**tutor, 'sex': 'M'}, email=None, address_city='Torino'), relation=True,
        payment=new_payment('25.00', audit_amount='25.0'),
        subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
        membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                    'price': None}))

adult_with_tutor = person('Teo', 'Grande', 'GRNTEO90A01H501N', born='01/01/1990', sex='m', email='Teo@Example.test')
add('adult-tutor-not-saved', 'owner', 200, SUCCESS(25.0),
    body=request(adult_with_tutor, tutor=person('Ugo', 'Grande', 'GRNUGO60A01H501O', born='01/01/1960', address_cap='10100'),
                 new_user_account={'new_member': 'no'}),
    effect=success('owner', 'Teo Grande', associate=created_associate({**adult_with_tutor, 'sex': 'M'}, email='Teo@Example.test',
                                                                      address_cap='10100'),
                   payment=new_payment('25.00', audit_amount='25.0'),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

no_tax = {'first_name': 'Vera', 'last_name': 'Senzacodice', 'born_date': '20/02/2000', 'role': '', 'type': '', 'is_minor': 'false'}
add('without-tax-code-blank-type-role', 'owner', 200, SUCCESS(25.0), body=request(no_tax, new_user_account='nessuno'),
    effect=success('owner', 'Vera Senzacodice', associate=created_associate(no_tax),
                   payment=new_payment('25.00', audit_amount='25.0'),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

# A None medical_certificate fails in the elif, after the associate serializer: its errors come first.
add('medical-certificate-none-after-associate-errors', 'owner', 400, {'sex': ['"X" is not a valid choice.']},
    body=request(person('Rita', 'Medica', 'MDCRTI90C41H501K', sex='X'), medical_certificate=None))

# AssociateSerializer.create with an associate_id no row has: the associate is created with that key.
keyed = {**person('Zeno', 'Chiave', 'CHVZNE85A01H501P', born='01/01/1985'), 'associate_id': next(ids)}
add('associate-id-created', 'owner', 200, SUCCESS(25.0), body=request(keyed),
    effect=success('owner', 'Zeno Chiave', associate=created_associate({k: v for k, v in keyed.items() if k != 'associate_id'}),
                   payment=new_payment('25.00', audit_amount='25.0'),
                   subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                   membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                               'price': None}))

# --- The owner wizard's medical_certificate --------------------------------------------------------------
# {'medical_id', 'filename', 'certificate_expring_date'}: the date defaults to today ('$today' is the request's
# naive date, YYYY-MM-DD) and an untouched empty date is moment's 'Invalid date'. Without medical_id the view's
# placeholder branch skips today's date and swallows unparseable values; with an uploaded certificate the date
# is parsed ('/' -> '%d/%m/%Y', else '%Y-%m-%d') and assigned as a datetime, then medical.save().
PLAIN = {'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON}
MEMBER = {'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end', 'price': None}


def wizard(name, data, full_name, medical_certificate, *, medical=None, medical_id=None):
    subscription = {**PLAIN, **({'medical_id': medical_id} if medical_id else {})}
    add(name, 'owner', 200, SUCCESS(25.0), body=request(data, medical_certificate=medical_certificate),
        effect=success('owner', full_name, associate=created_associate(data), medical=medical,
                       payment=new_payment('25.00', audit_amount='25.0'), subscription=subscription, membership=dict(MEMBER)))


wizard('wizard-certificate-date-today-skipped', person('Olga', 'Oggi', 'GGOLGO90C41H501K'), 'Olga Oggi',
       {'medical_id': None, 'filename': '', 'certificate_expring_date': '$today'})
wizard('wizard-certificate-invalid-date-skipped', person('Ivo', 'Invalido', 'NVLVIO90C01H501K'), 'Ivo Invalido',
       {'medical_id': None, 'filename': '', 'certificate_expring_date': 'Invalid date'})
wizard('wizard-uploaded-certificate-expiration', person('Ugo', 'Caricato', 'CRCUGO90C01H501K'), 'Ugo Caricato',
       {'medical_id': CERTIFICATES['expiring']['id'], 'filename': 'certificato.pdf', 'certificate_expring_date': '2027-03-31'},
       medical={'label': 'expiring', 'columns': {'user_id': OWNER, 'expiration_date': '2027-03-31'},
                'audit': {'user': ['None', OWNER], 'expiration_date': ['None', '2027-03-31 00:00:00']},
                'index': [OWN, 'actor.sport_association']}, medical_id=CERTIFICATES['expiring']['id'])
wizard('wizard-uploaded-certificate-italian-date', person('Dina', 'Datata', 'DTTDNI90C41H501K'), 'Dina Datata',
       {'medical_id': CERTIFICATES['dated']['id'], 'filename': 'c.pdf', 'certificate_expring_date': '5/4/2027'},
       medical={'label': 'dated', 'columns': {'expiration_date': '2027-04-05'},
                'audit': {'expiration_date': ['2026-01-01', '2027-04-05 00:00:00']}, 'index': [OWN, 'actor.sport_association']},
       medical_id=CERTIFICATES['dated']['id'])
wizard('wizard-uploaded-certificate-invalid-date-kept', person('Kai', 'Tenuto', 'TNTKAI90C01H501K'), 'Kai Tenuto',
       {'medical_id': CERTIFICATES['kept']['id'], 'filename': 'c.pdf', 'certificate_expring_date': 'Invalid date'},
       medical={'label': 'kept', 'columns': {}, 'audit': {}, 'index': None}, medical_id=CERTIFICATES['kept']['id'])


# A typed date without an upload: the view draws the placeholder certificate (generate_image_with_text, saved as PNG),
# stores it as <timestamp>/<document id>/medical_certificate_<first>_<last>.png (the names as sent), creates the Document
# and a MedicalCertificate of the association's account with the date, and links it to the subscription. 'text' is the
# view's f-string; the adapter draws it with Pillow and compares the stored bytes.
def placeholder_text(first, last, moment):
    return f'''
            {first} {last}\n
            \n
            Questa certificazione è valida fino al {moment}\n
            \n
            \n
            Attenzione: questo è un placeholder, il certificato medico deve essere caricato in seguito.
                        '''


def placeholder(name, data, full_name, medical_certificate, iso, *, first=None, last=None):
    first, last = first or data['first_name'], last or data['last_name']
    # auditlog also renders the Document's reverse accessor that declares a related_name.
    extra = [{'model': 'document', 'action': 0, 'ref': 'placeholder_document',
              'changes': {'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']}, 'app': 'docmanager',
              'index': [OWN, 'actor.sport_association']},
             {'model': 'medicalcertificate', 'action': 0, 'ref': 'placeholder_medical',
              'changes': {'expiration_date': ['None', iso + ' 00:00:00']}, 'index': [OWN, 'actor.sport_association']}]
    effect = success('owner', full_name, associate=created_associate(data), payment=new_payment('25.00', audit_amount='25.0'),
                     subscription={**PLAIN, 'medical_id': '$placeholder_medical'}, membership=dict(MEMBER), extra_audit=extra)
    effect['placeholder'] = {'filename': f'medical_certificate_{first}_{last}.png', 'expiration': iso,
                             'text': placeholder_text(first, last, iso + ' 00:00:00+00:00')}
    add(name, 'owner', 200, SUCCESS(25.0), body=request(data, medical_certificate=medical_certificate), effect=effect)


placeholder('wizard-certificate-placeholder', person('Pia', 'Segnaposto', 'SGNPIA90C41H501K'), 'Pia Segnaposto',
            {'medical_id': None, 'filename': '', 'certificate_expring_date': '2027-03-31'}, '2027-03-31')
placeholder('wizard-certificate-placeholder-italian-date', person('Italo', 'Segnaposto', 'SGNTLI90C01H501K'), 'Italo Segnaposto',
            {'medical_id': None, 'filename': '', 'certificate_expring_date': '5/4/2027'}, '2027-04-05')
# Letters the embedded font lacks are drawn as its missing-glyph box; no medical_id key reads like None.
placeholder('wizard-certificate-placeholder-accented-name', person('Niccolò', 'Però', 'PRENCL90C01H501K'), 'Niccolò Però',
            {'certificate_expring_date': '2031-12-01'}, '2031-12-01')
# A date in the past is drawn and stored like any other.
placeholder('wizard-certificate-placeholder-past-date', person('Remo', 'Scaduto', 'SCDRME90C01H501K'), 'Remo Scaduto',
            {'medical_id': None, 'filename': None, 'certificate_expring_date': '01/02/2020'}, '2020-02-01')
# A value that is not text fails inside the view's try block: no certificate.
wizard('wizard-certificate-number-date-skipped', person('Nino', 'Numero', 'NMRNNI90C01H501K'), 'Nino Numero',
       {'medical_id': None, 'filename': '', 'certificate_expring_date': 20270331})


# --- The wizard in a modal: sport_association names an association by its owner's username ----------------
# For an owner it is not an athlete request: User.objects.get(username=...) and its association replace
# request.user's when found (the own username: the same association); a miss keeps request.user's.
for label, username, first in (('own-username', 'message-reads-700001', 'Mara'), ('unknown-username', 'nessuno', 'Nina')):
    data = person(first, 'Modale', f'MDL{first[:3].upper()}90C41H501K')
    add('modal-' + label, 'owner', 200, SUCCESS(25.0), body=request(data, sport_association=username),
        setup={'association': {'multiple_membership_fee': False, 'enable_quotes_management': True, 'membership_fee': None,
                               'multiple_subscription_fee': False}, 'owner': {'subscription_duration': 2, 'membership_duration': 2}},
        effect=success('owner', f'{first} Modale', associate=created_associate(data), payment=new_payment('25.00', audit_amount='25.0'),
                       subscription=dict(PLAIN), membership=dict(MEMBER)))
# An association account only names its own association: another owner's username is 403 before any write.
add('modal-foreign-username', 'owner', 403, {'detail': 'User not allowed.'},
    body=request(person('Olga', 'Modale', 'MDLOLG90C41H501K'), sport_association='message-reads-700007'))

# --- The quick re-subscription (?m=only_associate) --------------------------------------------------------
# The members list renews a member with {'associate': id[, 'plan_id']}. _prepare_subscription_request sets
# data['sport_association'] to the owner's association; _handle_quick_subscription reads the association and
# scoped_queryset(Associate.objects).get(associate_id=...) (not deleted; only an impersonation scope filters the
# association), then in one transaction create_quick_subscription: the fee (the association's subscription_fee, or
# with multiple fees the plan whose id equals plan_id, else the first plan's fee with the last plan's data), a payment
# for request.user (always, whatever the amount; the courses default category, else the named one; no payment date),
# the certificate of the associate's subscriptions with the latest expiration, Subscription.objects.create (not
# signed, the account of the associate, meta {'plan_id': ...}, number 0); an associate without an account is an
# AttributeError (500). On commit: new_subscription, print_document_subscription and the notification (the
# association of request.user, an owner). Response {'status': 'success', 'payment_id': ...}.
QUICK_SETUP = {'association': {'multiple_subscription_fee': False, 'subscription_fee': '25.00', 'enable_quotes_management': False},
               'owner': {'subscription_duration': 2}}


def quick(actor, label, full_name, *, payment, subscription, headers=None, setup=None, plan=None):
    body = {'associate': ASSOCIATES[label]['id']}
    if plan is not None:
        body['plan_id'] = plan
    audit = [{'model': 'payment', 'action': 0, 'ref': 'payment', 'changes': {'amount': ['None', payment.pop('$audit')]}, 'index': [OWN, 'direct']},
             {'model': 'subscription', 'action': 0, 'ref': 'subscription', 'changes': {}, 'index': [OWN, 'direct']}]
    columns = {'payment_date': None, 'meta_payment_categories': None, 'meta': None, **payment}
    add('quick-' + label.replace('_', '-'), actor, 200, {'status': 'success', 'payment_id': '$new'}, query='?m=only_associate', body=body,
        headers=headers, setup=setup,
        effect={'actor': actor, 'associate': {'match': label, 'columns': {}}, 'payment': {'match': 'new', 'columns': columns},
                'subscription': {'type': 2, 'role': 1, 'status_flag': 1, 'meta': {'plan_id': plan}, 'subscription_number': '0',
                                 'subscription_type': None, **SEASON, **subscription},
                'broadcast_created': False, 'audit': audit,
                'celery': [{'task': 'check_workflows_trigger', 'args': [],
                            'kwargs': [['trigger_type', 'new_subscription'], ['sport_association_id', {'uuid': OWN}],
                                       ['subscription_id', '$subscription_uuid']]},
                           {'task': 'print_document_subscription', 'args': ['$subscription', '$authorization'], 'kwargs': []}],
                'notification': {'msg': f'Nuova Iscrizione aggiunta per {full_name}.', 'association': OWN}})


add('quick-associate-malformed-server-error', 'owner', 500, SERVER, query='?m=only_associate', body={'associate': 'non-un-id'},
    setup=QUICK_SETUP)
add('quick-associate-unknown-server-error', 'owner', 500, SERVER, query='?m=only_associate', body={'associate': UNKNOWN})
add('quick-associate-null-server-error', 'owner', 500, SERVER, query='?m=only_associate', body={'associate': None})
# The payment is written, then associate.user.user_id raises: everything rolls back.
add('quick-associate-without-account-server-error', 'owner', 500, SERVER, query='?m=only_associate',
    body={'associate': ASSOCIATES['quick_no_account']['id']})
add('quick-duplicate-current', 'owner', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    query='?m=only_associate', body={'associate': ASSOCIATES['current']['id']})
quick('owner', 'quick', 'Quinto Rapido', payment={'amount': '25.00', '$audit': '25.00'},
      subscription={'medical_id': CERTIFICATES['quick_latest']['id']})
quick('full_collaborator', 'quick_plan', 'Pia Pianificata', setup={'association': {'multiple_subscription_fee': True}}, plan='plan-base',
      payment={'amount': '40.00', '$audit': '40', 'meta': {'subscription_data': {'subscription_fee': 40, 'name': 'Piano base', 'id': 'plan-base'}}},
      subscription={})
# Only the association's own members: another association's member is 403 after both lookups, before any write.
add('quick-associate-of-another-association', 'owner', 403, {'detail': 'User not allowed.'}, query='?m=only_associate',
    body={'associate': ASSOCIATES['quick_abroad']['id'], 'plan_id': 'nessuno'})
# An unknown plan: the first plan's fee, the last plan's data.
quick('owner', 'quick_fallback', 'Fabio Ripiego', plan='nessuno',
      setup={'association': {'subscription_fee_plans': [{'id': 'p1', 'name': 'Uno', 'subscription_fee': '30'},
                                                        {'id': 'p2', 'name': 'Due', 'subscription_fee': 45}]}},
      payment={'amount': '30.00', '$audit': '30', 'meta': {'subscription_data': {'subscription_fee': 45, 'name': 'Due', 'id': 'p2'}}},
      subscription={'user_id': IDS['athlete']})


# --- The wizard's signature and the "new member" account ---------------------------------------------------
# With there_is_signature and data: Signature.objects.create(signature=data, user=user) after the associate (create
# entry, indexed through the actor's association), the subscription PENDING (2), and after Subscription.objects.create
# set_signature_from_base64(data): an http(s) URL is kept as signature_url; otherwise the text after the first ',' through
# base64.b64decode(validate=True) (invalid: 400 'Firma non valida.', rolled back) is stored at
# '<AWS_LOCATION>/subscriptions/<id>/signature_<timestamp>.png' and save(update_fields=[signature_url, signature_storage_key])
# writes the name and the public URL (update entry).
# new_user_account.new_member: an e-mail that already has an account links nothing (the request's user owns the rows, as
# without new_member); without one UserSerializer(raise_exception) creates the account (create_user with a token_hex username: create
# entry; then first_name[:3] + last_name[:3] + 8 random upper-case characters and set_password: the username update
# entry) and the welcome e-mail with the password is queued on commit before the other tasks. That user owns the
# associate, the payment, the signature and the subscription and gets the notification; an athlete user also queues
# send_email_template to the owner after the notification.
SIGNATURE_PNG = b'\x89PNG\r\n\x1a\nfirma creazione'
SIGNATURE_B64 = base64.b64encode(SIGNATURE_PNG).decode()
ATHLETE_EMAIL = 'message-reads-700004@example.test'


def signature_entries(effect, *, upload):
    """Signature create after the associate entries; the subscription update after its create entry."""
    audit = effect['audit']
    position = max(i for i, e in enumerate(audit) if e['model'] == 'associate') + 1
    audit.insert(position, {'model': 'signature', 'action': 0, 'ref': 'signature', 'changes': {}, 'index': [OWN, 'actor.sport_association']})
    changes = {'signature_url': ['None', '$signature_url']}
    if upload:
        changes['signature_storage_key'] = ['None', '$signature_key']
    position = next(i for i, e in enumerate(audit) if e['model'] == 'subscription')
    # The create entry was written before the signature fields were set.
    audit[position] = {**audit[position], 'changes': {**audit[position]['changes'], 'signature_url': None,
                                                       **({'signature_storage_key': None} if upload else {})}}
    audit.insert(position + 1, {'model': 'subscription', 'action': 1, 'ref': 'subscription', 'changes': changes, 'index': [OWN, 'direct']})


signer = person('Sofia', 'Firmata', 'FRMSFO90C41H501K')
effect = success('owner', 'Sofia Firmata', associate=created_associate(signer), payment=new_payment('25.00', audit_amount='25.0'),
                 subscription={**PLAIN, 'status_flag': 2, 'signature_url': '$signature_url', 'signature_storage_key': '$signature_key'},
                 membership=dict(MEMBER))
effect['signature'] = {'user': 'owner', 'data': 'data:image/png;base64,' + SIGNATURE_B64}
effect['stored'] = SIGNATURE_B64
signature_entries(effect, upload=True)
add('signature-upload', 'owner', 200, SUCCESS(25.0), setup={'association': {**BASE_SETTINGS['association'], 'multiple_membership_fee': False}},
    body=request(signer, signature={'there_is_signature': True, 'data': ' data:image/png;base64,' + SIGNATURE_B64 + ' '}), effect=effect)

linked = person('Ugo', 'Collegato', 'CLLUGO90C01H501K')
effect = success('owner', 'Ugo Collegato', associate=created_associate(linked), payment=new_payment('25.00', audit_amount='25.0'),
                 subscription={**PLAIN, 'status_flag': 2, 'signature_url': 'https://firme.example.test/ugo.png'}, membership=dict(MEMBER))
effect['signature'] = {'user': 'owner', 'data': 'https://firme.example.test/ugo.png'}
signature_entries(effect, upload=False)
effect['audit'] = [{**e, 'changes': {'signature_url': ['None', 'https://firme.example.test/ugo.png']}} if e['model'] == 'subscription'
                   and e['action'] == 1 else e for e in effect['audit']]
add('signature-url', 'owner', 200, SUCCESS(25.0),
    body=request(linked, signature={'there_is_signature': True, 'data': 'https://firme.example.test/ugo.png'}), effect=effect)
add('signature-invalid-base64', 'owner', 400, {'msg': 'Firma non valida.', 'code': '400', 'ref': 'invalid_signature'},
    body=request(person('Ivo', 'Firmaerrata', 'FRMVIO90C01H501K'), signature={'there_is_signature': True, 'data': 'data:image/png;base64,@@@'}))

add('new-member-info-missing-server-error', 'owner', 500, SERVER,
    body=request(person('Nico', 'Senzadati', 'SNZNCO90C01H501K'), account={'new_member': True}))
add('new-member-invalid-email', 'owner', 400, {'email': ['Enter a valid email address.']},
    body=request(person('Nico', 'Errato', 'RRTNCO90C01H501K'),
                 account={'new_member': True, 'new_member_info': {'first_name': 'Nico', 'last_name': 'Errato', 'email': 'non-valida'}}))


def member_effect(full_name, data, *, member, member_label, new_user=None, welcome=False, signature=None, actor='owner',
                  broadcast=True, tutor=None):
    effect = success(actor, full_name, associate=created_associate(data, user_id=member, **({'is_minor': True} if data.get('is_minor') else {})),
                     tutor=created_associate(tutor, user_id=member) if tutor else None, relation=bool(tutor),
                     payment=new_payment('25.00', audit_amount='25.0'),
                     subscription={**PLAIN, 'user_id': member, **({'status_flag': 2, 'signature_url': signature} if signature else {})},
                     membership=dict(MEMBER))
    effect['member'] = member
    for entry in effect['audit']:
        if entry['model'] == 'associate' and entry['action'] == 1:
            entry['changes'] = {'user': ['None', member]}
    # The notification goes to the member: its broadcast rows are created once.
    effect['audit'] = [e for e in effect['audit'] if e.get('app') != 'notifications']
    # Entries without an association of their own are indexed through the actor's association (none for the public forms).
    through_actor = [OWN, 'actor.sport_association'] if actor == 'owner' else None
    if broadcast:
        effect['audit'] += [{'model': model, 'action': 0, 'ref': model, 'changes': {}, 'app': 'notifications', 'index': through_actor}
                            for model in ('notificationsbroadcast', 'usernotificationsbroadcast')]
    effect['broadcast_created'] = broadcast
    effect['notification']['user'] = member_label
    if new_user:
        effect['new_user'] = new_user
        effect['audit'][:0] = [{'model': 'user', 'action': 0, 'ref': 'new_user', 'changes': {}, 'index': through_actor},
                               {'model': 'user', 'action': 1, 'ref': 'new_user', 'changes': {'username': ['$token_username', '$username']},
                                'index': through_actor}]
    if signature:
        effect['signature'] = {'user': member_label, 'data': signature}
        signature_entries(effect, upload=False)
        effect['audit'] = [{**e, 'changes': {'signature_url': ['None', signature]}} if e['model'] == 'subscription' and e['action'] == 1 else e
                           for e in effect['audit']]
        if through_actor is None:
            effect['audit'] = [{**e, 'index': None} if e['model'] == 'signature' else e for e in effect['audit']]
    first, last = data['first_name'], data['last_name']
    athlete = {'task': 'send_email_template', 'args': [], 'kwargs': [
        ['recipient_list', ['message-reads-700001@example.test']],
        ['subject', f"[$denomination] Ricordati di approvare l'iscrizione di {first} {last}"],
        ['template', 'email/account/email_new_subscription.html'],
        ['data', {'athlete_first_name': first, 'athlete_last_name': last, 'sport_association': {'denomination': '$denomination'},
                  'app_host': '$app_url', 'settings': {'WHITELABEL_NAME': '$whitelabel_name', 'IS_WHITELABEL': '$is_whitelabel'}}],
        ['sport_association_id', {'uuid': OWN}]]}
    effect['celery'] = (['$welcome'] if welcome else []) + effect['celery'] + [athlete]
    return effect


fresh = person('Nadia', 'Nuovissima', 'NVSNDA90C41H501K')
add('new-member-account', 'owner', 200, SUCCESS(25.0),
    body=request(fresh, account={'new_member': True, 'new_member_info': {'first_name': 'Nadia', 'last_name': 'Nuovissima', 'phone': '3330001111',
                                                                         'email': 'Nadia.Nuova@Example.TEST'}},
                 signature={'there_is_signature': True, 'data': 'https://firme.example.test/nadia.png'}),
    effect=member_effect('Nadia Nuovissima', fresh, member='$new_user', member_label='$new_user', welcome=True,
                         new_user={'prefix': 'NadNuo', 'email': 'Nadia.Nuova@example.test', 'first_name': 'Nadia', 'last_name': 'Nuovissima',
                                   'phone': '3330001111'},
                         signature='https://firme.example.test/nadia.png'))
known_member = person('Elsa', 'Esistente', 'SSTLSE90C41H501K')
add('new-member-existing-email', 'owner', 200, SUCCESS(25.0),
    body=request(known_member, account={'new_member': True, 'new_member_info': {'first_name': 'X', 'last_name': 'Y', 'email': ATHLETE_EMAIL}}),
    effect=success('owner', 'Elsa Esistente', associate=created_associate(known_member), payment=new_payment('25.00', audit_amount='25.0'),
                   subscription=dict(PLAIN), membership=dict(MEMBER)))

# --- The public forms -------------------------------------------------------------------------------------
# An anonymous request is attributed to the user named by data['sport_association'] (User.objects.get(username=...): a
# missing key or an unknown name is a 500); with an association account the rest is the owner's flow without an actor
# and without an Authorization header for print_document_subscription. The quick mode answers 403 after both lookups.
# A logged-in athlete naming an association is an athlete request: the association of that username (unknown: 500),
# the athlete owns the associate, the payment and the subscription, gets the notification, and the owner is e-mailed.
OWNER_NAME, ATHLETE_NAME = 'message-reads-700001', 'message-reads-700004'
FORM = {'plan_id': None, 'membership_plan_id': None, 'medical_certificate': {'medical_id': None, 'certificate_expring_date': None},
        'custom_data': None, 'additional_fields': None, 'subscription_id': None}
add('anonymous-without-association-server-error', 'anonymous', 500, SERVER, body=request(person('Anna', 'Anonima', 'NNMNNA90C41H501K')))
add('anonymous-unknown-username-server-error', 'anonymous', 500, SERVER,
    body=request(person('Anna', 'Anonima', 'NNMNNA90C41H501K'), sport_association='nessuno'))
add('anonymous-athlete-username-server-error', 'anonymous', 500, SERVER,
    body=request(person('Anna', 'Anonima', 'NNMNNA90C41H501K'), sport_association=ATHLETE_NAME))
add('anonymous-list-body-server-error', 'anonymous', 500, SERVER, body=[])
add('anonymous-missing-signature', 'anonymous', 400, VALIDATION,
    body={k: v for k, v in request(person('Anna', 'A', 'X'), sport_association=OWNER_NAME).items() if k != 'signature'})
add('anonymous-quick-not-allowed', 'anonymous', 403, {'detail': 'User not allowed.'}, query='?m=only_associate',
    body={'sport_association': OWNER_NAME, 'associate': ASSOCIATES['quick_no_account']['id']})
add('anonymous-quick-unknown-associate-server-error', 'anonymous', 500, SERVER, query='?m=only_associate',
    body={'sport_association': OWNER_NAME, 'associate': UNKNOWN})
add('anonymous-duplicate', 'anonymous', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=request(person('dora', 'DOPPIA', 'dppdro90a41h501x', born='01/01/1990'), sport_association=OWNER_NAME, **FORM))
anonymous = person('Anna', 'Anonima', 'NNMNNA90C41H501K')
add('anonymous-form', 'anonymous', 200, SUCCESS(25.0), body=request(anonymous, sport_association=OWNER_NAME, **FORM),
    effect=success('anonymous', 'Anna Anonima', associate=created_associate(anonymous), payment=new_payment('25.00', audit_amount='25.0'),
                   subscription=dict(PLAIN), membership=dict(MEMBER)))
registered = person('Remo', 'Registrato', 'RGSRME90C01H501K')
add('anonymous-form-new-account', 'anonymous', 200, SUCCESS(25.0),
    body=request(registered, sport_association=OWNER_NAME, **FORM,
                 account={'new_member': True, 'new_member_info': {'first_name': 'Remo', 'last_name': 'Registrato', 'phone': None,
                                                                  'email': 'remo.registrato@example.test'}},
                 signature={'there_is_signature': True, 'data': 'https://firme.example.test/remo.png'}),
    effect=member_effect('Remo Registrato', registered, member='$new_user', member_label='$new_user', welcome=True, actor='anonymous',
                         new_user={'prefix': 'RemReg', 'email': 'remo.registrato@example.test', 'first_name': 'Remo',
                                   'last_name': 'Registrato', 'phone': None},
                         signature='https://firme.example.test/remo.png'))

known_anonymous = person('Ada', 'Anonima', 'DANNDA90C41H501K')
add('anonymous-form-existing-email', 'anonymous', 200, SUCCESS(25.0),
    body=request(known_anonymous, sport_association=OWNER_NAME, **FORM,
                 account={'new_member': True, 'new_member_info': {'first_name': 'Ada', 'last_name': 'Anonima', 'phone': None,
                                                                  'email': ATHLETE_EMAIL}}),
    effect=success('anonymous', 'Ada Anonima', associate=created_associate(known_anonymous), payment=new_payment('25.00', audit_amount='25.0'),
                   subscription=dict(PLAIN), membership=dict(MEMBER)))

add('athlete-form-unknown-username-server-error', 'athlete', 500, SERVER,
    body=request(person('Tina', 'Atleta', 'TLTTNI90C41H501K'), sport_association='nessuno'))
add('athlete-form-athlete-username-server-error', 'athlete', 500, SERVER,
    body=request(person('Tina', 'Atleta', 'TLTTNI90C41H501K'), sport_association=ATHLETE_NAME))
add('athlete-form-duplicate', 'athlete', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=request(person('dora', 'DOPPIA', 'dppdro90a41h501x', born='01/01/1990'), sport_association=OWNER_NAME, **FORM))
add('athlete-form-invalid-fields', 'athlete', 400, {'sex': ['"X" is not a valid choice.']},
    body=request(person('Tina', 'Atleta', 'TLTTNI90C41H501K', sex='X'), sport_association=OWNER_NAME, **FORM))
athlete_form = person('Tina', 'Atleta', 'TLTTNI90C41H501K')
add('athlete-form', 'athlete', 200, SUCCESS(25.0), body=request(athlete_form, sport_association=OWNER_NAME, **FORM),
    effect=member_effect('Tina Atleta', athlete_form, member=IDS['athlete'], member_label='athlete', actor='athlete'))
# A minor with a tutor: both belong to the athlete.
minor_form = person('Mino', 'Minore', 'MNRMNI15C01H501K', born='01/03/2015', is_minor=True)
tutor_form = person('Tea', 'Tutrice', 'TTRTEA80C41H501K', born='01/03/1980', sex='f')
effect = member_effect('Mino Minore', {**minor_form, 'is_minor': True}, member=IDS['athlete'], member_label='athlete', actor='athlete',
                       broadcast=False, tutor={**tutor_form, 'sex': 'F'})
add('athlete-form-minor', 'athlete', 200, SUCCESS(25.0), body=request(minor_form, tutor=tutor_form, sport_association=OWNER_NAME, **FORM),
    effect=effect)

# --- Preregistration (next season) ------------------------------------------------------------------------
# data['preregistration'] is True without a subscription_id: the duplicate check, the subscription's creation date
# (the season's first day), its dates and the membership's dates are those of the season after the current one.
NEXT = {'creation_date': '$next_season_created', 'start_date': '$next_season_start', 'end_date': '$next_season_end'}
NEXT_MEMBER = {'membership_type': '$next_membership_type', 'start_date': '$next_season_start', 'end_date': '$next_season_end', 'price': None}


def next_season(effect):
    """The create entry renders the datetimes create_subscription assigned to the date fields."""
    for entry in effect['audit']:
        if entry['model'] == 'subscription' and entry['action'] == 0:
            entry['changes'] = {**entry['changes'], 'start_date': ['None', '$next_season_start_text'],
                                'end_date': ['None', '$next_season_end_text']}
    return effect


early = person('Pietro', 'Prenotato', 'PRNPTR90C01H501K')
add('preregistration', 'owner', 200, SUCCESS(25.0), body=request(early, preregistration=True, subscription_id=None),
    effect=next_season(success('owner', 'Pietro Prenotato', associate=created_associate(early),
                               payment=new_payment('25.00', audit_amount='25.0'), subscription={**PLAIN, **NEXT},
                               membership=dict(NEXT_MEMBER))))
add('preregistration-twice', 'owner', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=request(early, preregistration=True))
early_public = person('Paola', 'Prenotata', 'PRNPLA90C41H501K')
add('preregistration-anonymous', 'anonymous', 200, SUCCESS(25.0),
    body=request(early_public, sport_association=OWNER_NAME, preregistration=True, **FORM),
    effect=next_season(success('anonymous', 'Paola Prenotata', associate=created_associate(early_public),
                               payment=new_payment('25.00', audit_amount='25.0'), subscription={**PLAIN, **NEXT},
                               membership=dict(NEXT_MEMBER))))
# Not exactly True: an ordinary registration.
plain_flag = person('Furio', 'Flag', 'FLGFRU90C01H501K')
add('preregistration-flag-not-true', 'owner', 200, SUCCESS(25.0), body=request(plain_flag, preregistration='true'),
    effect=success('owner', 'Furio Flag', associate=created_associate(plain_flag), payment=new_payment('25.00', audit_amount='25.0'),
                   subscription=dict(PLAIN), membership=dict(MEMBER)))

# --- The certificate a registration links ------------------------------------------------------------------
# _linkable_medical_certificates: the id is linked when the certificate belongs to the association account or to the
# requester, is the certificate of a subscription of this association, or has no account and no
# subscription of another association. Any other id is treated like an unknown one: the registration succeeds
# without a certificate and the certificate keeps its account and expiration date.
ASSOCIATES['certified'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Cora', 'last': 'Certificata', 'tax_code': 'CRTCRO80A41H501C',
                           'born': '1980-01-01', 'user': 'owner', 'email': None}
ASSOCIATES['certified_abroad'] = {'id': next(extra_ids), 'association': FOREIGN, 'first': 'Carlo', 'last': 'Altrove',
                                  'tax_code': 'LTRCRL80A01H501D', 'born': '1980-01-01', 'user': 'foreign_owner', 'email': None}
CERTIFICATES['other_account'] = {'id': next(extra_ids), 'user': 'foreign_owner', 'expiration': '2026-02-02'}
CERTIFICATES['other_subscription'] = {'id': next(extra_ids), 'user': None}
CERTIFICATES['own_subscription'] = {'id': next(extra_ids), 'user': 'foreign_owner'}
CERTIFICATES['athlete_account'] = {'id': next(extra_ids), 'user': 'athlete'}
CERTIFICATES['public_upload'] = {'id': next(extra_ids), 'user': None}
SUBSCRIPTIONS['certified_2019'] = {'id': next(extra_ids), 'associate': 'certified', 'start': '2019-01-01', 'end': '2019-12-31', 'archived': True,
                                   'number': '4', 'medical': 'own_subscription'}
SUBSCRIPTIONS['abroad_2019'] = {'id': next(extra_ids), 'associate': 'certified_abroad', 'start': '2019-01-01', 'end': '2019-12-31',
                                'archived': True, 'number': '901', 'association': FOREIGN, 'medical': 'other_subscription'}
# The family wizard: a member whose unpaid payment of today is reused.
ASSOCIATES['family_payer'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Rocco', 'last': 'Riuso', 'tax_code': 'RSIRCC75A01H501M',
                              'born': '1975-01-01', 'user': 'owner', 'email': None}
PAYMENTS['family_reusable'] = {'id': next(extra_ids), 'associate': 'family_payer', 'amount': '25.00', 'paid': False, 'date': '$today'}
EXPIRING = '2027-03-31'
EXPIRATION_AUDIT = lambda before='None': {'expiration_date': [before, EXPIRING + ' 00:00:00']}


def certificate_body(label):
    return {'medical_id': CERTIFICATES[label]['id'], 'filename': 'certificato.pdf', 'certificate_expring_date': EXPIRING}


def linked(effect, label, *, columns, audit, index):
    """The subscription's certificate and its update entry, written before the payment."""
    effect['subscription'] = {**effect['subscription'], 'medical_id': CERTIFICATES[label]['id']}
    effect['medical'] = {'label': label, 'columns': columns, 'audit': audit, 'index': index}
    position = next(i for i, e in enumerate(effect['audit']) if e['model'] in ('payment', 'subscription'))
    effect['audit'].insert(position, {'model': 'medicalcertificate', 'action': 1, 'ref': label, 'changes': audit, 'index': index})
    return effect


def owner_registration(name, data, label, *, link):
    effect = success('owner', f"{data['first_name']} {data['last_name']}", associate=created_associate(data),
                     payment=new_payment('25.00', audit_amount='25.0'), subscription=dict(PLAIN), membership=dict(MEMBER))
    if link:
        linked(effect, label, **link)
    add(name, 'owner', 200, SUCCESS(25.0), body=request(data, medical_certificate=certificate_body(label)), effect=effect)


owner_registration('certificate-of-another-account-not-linked', person('Lia', 'Senzalink', 'SNZLIA90C41H501K'), 'other_account', link=None)
owner_registration('certificate-of-another-association-subscription-not-linked', person('Leo', 'Senzalink', 'SNZLEO90C01H501K'),
                   'other_subscription', link=None)
owner_registration('certificate-of-own-subscription-linked', person('Lea', 'Collegata', 'CLLLEA90C41H501K'), 'own_subscription',
                   link={'columns': {'expiration_date': EXPIRING}, 'audit': EXPIRATION_AUDIT(), 'index': [OWN, 'actor.sport_association']})

public = person('Pia', 'Pubblica', 'PBBPIA90C41H501K')
add('anonymous-certificate-of-another-account-not-linked', 'anonymous', 200, SUCCESS(25.0),
    body=request(public, sport_association=OWNER_NAME, **{**FORM, 'medical_certificate': certificate_body('athlete_account')}),
    effect=success('anonymous', 'Pia Pubblica', associate=created_associate(public), payment=new_payment('25.00', audit_amount='25.0'),
                   subscription=dict(PLAIN), membership=dict(MEMBER)))
uploaded = person('Ugo', 'Pubblico', 'PBBUGO90C01H501K')
add('anonymous-certificate-upload-linked', 'anonymous', 200, SUCCESS(25.0),
    body=request(uploaded, sport_association=OWNER_NAME, **{**FORM, 'medical_certificate': certificate_body('public_upload')}),
    effect=linked(success('anonymous', 'Ugo Pubblico', associate=created_associate(uploaded), payment=new_payment('25.00', audit_amount='25.0'),
                          subscription=dict(PLAIN), membership=dict(MEMBER)),
                  'public_upload', columns={'user_id': OWNER, 'expiration_date': EXPIRING},
                  audit={'user': ['None', OWNER], **EXPIRATION_AUDIT()}, index=None))


def athlete_registration(name, data, label, *, link):
    effect = member_effect(f"{data['first_name']} {data['last_name']}", data, member=IDS['athlete'], member_label='athlete', actor='athlete',
                           broadcast=False)
    if link:
        linked(effect, label, **link)
    add(name, 'athlete', 200, SUCCESS(25.0),
        body=request(data, sport_association=OWNER_NAME, **{**FORM, 'medical_certificate': certificate_body(label)}), effect=effect)


athlete_registration('athlete-certificate-of-another-account-not-linked', person('Aldo', 'Slegato', 'SLGLDA90C01H501K'), 'other_account',
                     link=None)
athlete_registration('athlete-certificate-own-linked', person('Ada', 'Legata', 'LGTDAA90C41H501K'), 'athlete_account',
                     link={'columns': {'expiration_date': EXPIRING}, 'audit': EXPIRATION_AUDIT(), 'index': None})
# An upload without an account that another association's subscription already uses.
athlete_registration('athlete-certificate-of-another-association-subscription-not-linked', person('Abe', 'Slegato', 'SLGBAE90C01H501K'),
                     'other_subscription', link=None)

# --- The quick mode from a member account ----------------------------------------------------------------
# For an account that is not an association the body keeps its sport_association: _handle_quick_subscription reads
# SportAssociation.objects.get(sport_association_id=...) and Associate.objects.get(associate_id=...) (a miss or a
# malformed id is a 500) and answers 403: the quick re-subscription is for the association's own owner. Without both
# keys the answer is the 400 validation message.
QUICK_MEMBER = ASSOCIATES['quick_fallback']['id']
add('athlete-quick-not-allowed', 'athlete', 403, {'detail': 'User not allowed.'}, query='?m=only_associate',
    body={'sport_association': OWN, 'associate': QUICK_MEMBER, 'plan_id': 'plan-base'})
add('athlete-quick-unknown-association-server-error', 'athlete', 500, SERVER, query='?m=only_associate',
    body={'sport_association': UNKNOWN, 'associate': QUICK_MEMBER})
add('athlete-quick-association-username-server-error', 'athlete', 500, SERVER, query='?m=only_associate',
    body={'sport_association': OWNER_NAME, 'associate': QUICK_MEMBER})
add('athlete-quick-association-null-server-error', 'athlete', 500, SERVER, query='?m=only_associate',
    body={'sport_association': None, 'associate': QUICK_MEMBER})
add('athlete-quick-unknown-associate-server-error', 'athlete', 500, SERVER, query='?m=only_associate',
    body={'sport_association': OWN, 'associate': UNKNOWN})
add('athlete-quick-associate-malformed-server-error', 'athlete', 500, SERVER, query='?m=only_associate',
    body={'sport_association': OWN, 'associate': 'non-un-id'})
add('athlete-quick-without-associate', 'athlete', 400, VALIDATION, query='?m=only_associate', body={'sport_association': OWN})
add('athlete-quick-without-association', 'athlete', 400, VALIDATION, query='?m=only_associate', body={'associate': QUICK_MEMBER})

# --- The family wizard -------------------------------------------------------------------------------------
# data['is_family_wizard'] is True (with the four required keys present): one transaction creates a Family
# (data.get('type'), 'family' unless it names a known type) and, for every entry of data['multiple_entry_form_data']
# whose 'valid' is True, runs create_subscription on the entry with data['sport_association'], the family on its
# associate_data and its custom_data. The answer is {'status': 'success', 'subs': [], 'payments': [...]}: PaymentSerializer
# of each created payment, in entry order. An error in any entry rolls everything back, the Family included. These effects
# list the rows by label ('new', bound in order), as the renewal fixture does.
def entry(model, action, ref, changes=None, index=(OWN, 'direct'), app='application'):
    return {'model': model, 'action': action, 'ref': ref, 'changes': changes or {}, 'index': list(index) if index else None, 'app': app}


def family_entry(data, *, valid=True, **extra):
    return {**request(data), 'plan_id': None, 'membership_plan_id': None, 'custom_data': {}, 'additional_fields': None,
            'medical_certificate': {'medical_id': None, 'certificate_expring_date': None}, 'valid': valid, **extra}


def family_body(entries, **extra):
    return {**request(person('Capo', 'Famiglia', 'FMGCPA80A01H501F')), **FORM, 'is_family_wizard': True, 'multiple_entry_form_data': entries,
            **extra}


def family_effect(actor, people, *, kind='family', through_actor=None, payment='new', respond=True):
    """One Family and, per person, the associate, payment, subscription and membership with their entries and tasks.

    payment: 'new' (created by the request), None (no fee), or {'associate': seed label, 'payment': seed label} for a
    seeded associate whose unpaid payment of today is reused. respond: False when the answer is the 500 page.
    """
    new = [{'label': 'family', 'bind': '$family', 'match': {'type': kind}, 'columns': {}}]
    audit = [entry('family', 0, '$family', index=through_actor)]
    celery, notifications, payments, changed = [], [], [], {}
    for position, data in enumerate(people):
        NUMBER[0] += 1
        number = str(NUMBER[0])
        associate, subscription = f'$associate_{position}', f'$subscription_{position}'
        if isinstance(payment, dict):
            associate, paid = ASSOCIATES[payment['associate']]['id'], PAYMENTS[payment['payment']]['id']
            changed = {'associate': {payment['associate']: {'sex': data['sex'], 'family_id': '$family'}}}
            audit.append(entry('associate', 1, payment['associate'], {'sex': ['A', data['sex']], 'family': ['None', '$family']}))
            payments.append(payment['payment'])
        else:
            new.append({'label': 'associate', 'bind': associate, 'match': {'first_name': data['first_name'], 'last_name': data['last_name']},
                        'columns': {'tax_code': data['tax_code'], 'born_date': '-'.join(reversed(data['born_date'].split('/'))),
                                    'sex': data['sex'], 'sport_association_id': OWN, 'user_id': '$owner', 'family_id': '$family'}})
            audit += [entry('associate', 0, associate, {'user': ['None', 'None']}), entry('associate', 1, associate, {'user': ['None', OWNER]})]
            paid = None
            if payment == 'new':
                paid = f'$payment_{position}'
                new.append({'label': 'payment', 'bind': paid, 'match': {'associate_id': associate},
                            'columns': {'subject': 1, 'description': 'Iscrizione', 'payment_category_id': '$category', 'user_id': '$owner',
                                        'sport_association_id': OWN, 'custom_accounts_id': ACCOUNT, 'amount': '25.00',
                                        'payment_date': '$today_midnight', 'meta_payment_categories': None, 'meta': None}})
                audit.append(entry('payment', 0, paid, {'amount': ['None', '25.0']}))
                payments.append(paid)
        new.append({'label': 'subscription', 'bind': subscription, 'match': {'associate_id': associate},
                    'columns': {'sport_association_id': OWN, 'user_id': '$owner', 'payment_id': paid, **PLAIN,
                                'subscription_number': number, 'custom_data': {}, 'additional_fields': None}})
        # Without fees the membership's price is the number 0: stored as 0.00, and the following save() records the difference.
        price = None if payment else '0.00'
        new.append({'label': 'membership', 'bind': subscription + '_membership', 'match': {'subscription_id': subscription},
                    'columns': {'sport_association_id': OWN, 'associate_id': associate, 'payment_id': paid, **MEMBER, 'price': price,
                                'membership_number': number}})
        audit += [entry('subscription', 0, subscription),
                  entry('subscriptionmembership', 0, subscription + '_membership', {'price': ['None', '0']} if price else {})]
        if price:
            audit.append(entry('subscriptionmembership', 1, subscription + '_membership', {'price': ['0.00', '0']}))
        celery += [{'task': 'check_workflows_trigger', 'args': [],
                    'kwargs': [['trigger_type', 'new_subscription'], ['sport_association_id', {'uuid': OWN}],
                               ['subscription_id', {'uuid': subscription}]]},
                   {'task': 'print_document_subscription', 'args': [subscription, '$authorization'], 'kwargs': []}]
        notifications.append({'msg': f"Nuova Iscrizione aggiunta per {data['first_name']} {data['last_name']}.", 'association': OWN})
    return {'actor': actor, 'new': new, 'changed': changed, 'deleted': {}, 'tags': [], 'audit': audit, 'celery': celery,
            'notifications': notifications, 'payments': payments if respond else None}


FAMILY_SUCCESS = {'status': 'success', 'subs': [], 'payments': '$payments'}
first_child, second_child = person('Febo', 'Famiglia', 'FMGFBE10A01H501G'), person('Fiona', 'Famiglia', 'FMGFNI12A41H501H', sex='F')
skipped = person('Franco', 'Escluso', 'SCLFNC10A01H501I')
add('family-wizard-without-entries-server-error', 'anonymous', 500, SERVER,
    body={k: v for k, v in family_body([], sport_association=OWNER_NAME).items() if k != 'multiple_entry_form_data'})
add('family-wizard-entries-null-server-error', 'anonymous', 500, SERVER, body=family_body(None, sport_association=OWNER_NAME))
add('family-wizard-entry-not-an-object-server-error', 'anonymous', 500, SERVER, body=family_body(['x'], sport_association=OWNER_NAME))
add('family-wizard-entry-without-valid-server-error', 'anonymous', 500, SERVER,
    body=family_body([{k: v for k, v in family_entry(first_child).items() if k != 'valid'}], sport_association=OWNER_NAME))
add('family-wizard-entry-without-custom-data-server-error', 'anonymous', 500, SERVER,
    body=family_body([{k: v for k, v in family_entry(first_child).items() if k != 'custom_data'}], sport_association=OWNER_NAME))
add('family-wizard-owner-without-association-key-server-error', 'owner', 500, SERVER, body=family_body([family_entry(first_child)]))
# The second entry's errors are the answer; the first entry and the Family are rolled back.
add('family-wizard-invalid-entry', 'anonymous', 400, {'sex': ['"X" is not a valid choice.']},
    body=family_body([family_entry(first_child), family_entry({**second_child, 'sex': 'X'})], sport_association=OWNER_NAME))
add('family-wizard-duplicate-entry', 'anonymous', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=family_body([family_entry(first_child), family_entry(person('dora', 'DOPPIA', 'dppdro90a41h501x', born='01/01/1990'))],
                     sport_association=OWNER_NAME))
# Entries that are not valid are skipped; without a valid one only the Family is created.
add('family-wizard-no-valid-entry', 'anonymous', 200, FAMILY_SUCCESS,
    body=family_body([family_entry(skipped, valid=False), family_entry(skipped, valid='true')], sport_association=OWNER_NAME),
    effect=family_effect('anonymous', []))
# A payment the association already expects (unpaid, of today, same amount) is reused and serialized as stored.
payer = person('Rocco', 'Riuso', ASSOCIATES['family_payer']['tax_code'], born='01/01/1975')
add('family-wizard-reused-payment', 'anonymous', 200, FAMILY_SUCCESS,
    body=family_body([family_entry(payer)], sport_association=OWNER_NAME),
    effect=family_effect('anonymous', [payer], payment={'associate': 'family_payer', 'payment': 'family_reusable'}))
# Without fees there is no payment to serialize.
free_child = person('Lapo', 'Famiglia', 'FMGLPA13A01H501L')
add('family-wizard-without-fees', 'anonymous', 200, FAMILY_SUCCESS, setup={'association': {'enable_quotes_management': False}},
    body=family_body([family_entry(free_child)], sport_association=OWNER_NAME),
    effect=family_effect('anonymous', [free_child], payment=None))
# The payments created by the request are read again after the commit and serialized as stored (payment_date is the
# first instant of the day), in entry order, like a reused one.
add('family-wizard', 'anonymous', 200, FAMILY_SUCCESS, setup={'association': {'enable_quotes_management': True}},
    body=family_body([family_entry(first_child), family_entry(skipped, valid=False), family_entry(second_child)],
                     sport_association=OWNER_NAME, type='family'),
    effect=family_effect('anonymous', [first_child, second_child]))
# An association account names its own association; a type that is not a known one is 'family'.
owner_child = person('Olmo', 'Famiglia', 'FMGLMO11A01H501J')
add('family-wizard-owner', 'owner', 200, FAMILY_SUCCESS,
    body=family_body([family_entry(owner_child)], sport_association=OWNER_NAME, type='altro'),
    effect=family_effect('owner', [owner_child], through_actor=(OWN, 'actor.sport_association')))

# An entry whose member e-mail already has an account is created with the owner, like one without new_member.
linked_child = person('Elio', 'Famiglia', 'FMGLEI12A01H501M')
add('family-wizard-owner-existing-email', 'owner', 200, FAMILY_SUCCESS,
    body=family_body([family_entry(linked_child, new_user_account={'new_member': True, 'new_member_info': {
        'first_name': 'Elio', 'last_name': 'Famiglia', 'phone': None, 'email': ATHLETE_EMAIL}})], sport_association=OWNER_NAME),
    effect=family_effect('owner', [linked_child], through_actor=(OWN, 'actor.sport_association')))

# --- A preregistration that renews a subscription ------------------------------------------------------------
# data['preregistration'] is True with a subscription_id: fill_data_preregisration reads the subscription in the
# association of the registration (data['sport_association'], else user.sport_association) when the requester is
# that association's account or the subscription's account; otherwise the answer is the 400 below. The person
# (AssociateSerializer of the stored associate, with its associate_id), the certificate, the custom data, the role
# and the number are the renewed subscription's; the type is the request's. The new subscription belongs to the
# renewed subscription's account, the associate and the payment to create_subscription's user.
ASSOCIATES['renewed'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Remo', 'last': 'Rinnovo', 'tax_code': 'RNNRME80A01H501R',
                         'born': '1980-01-01', 'user': 'athlete', 'email': 'remo.rinnovo@example.test'}
ASSOCIATES['renewed_member'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Rina', 'last': 'Rinnovo', 'tax_code': 'RNNRNI82A41H501S',
                                'born': '1982-01-01', 'user': 'athlete', 'email': None}
CERTIFICATES['renewed'] = {'id': next(extra_ids), 'user': None, 'expiration': '2026-12-31'}
SUBSCRIPTIONS['renewable'] = {'id': next(extra_ids), 'associate': 'renewed', 'start': '$season_start', 'end': '$season_end', 'archived': False,
                              'number': '15', 'medical': 'renewed', 'user': 'athlete', 'role': 3, 'custom_data': {'tipo': 'agonista'},
                              'additional_fields': {'taglia': 'L'}}
SUBSCRIPTIONS['renewable_member'] = {'id': next(extra_ids), 'associate': 'renewed_member', 'start': '$season_start', 'end': '$season_end',
                                     'archived': False, 'number': None, 'user': 'athlete'}
NOT_RENEWABLE = {'msg': 'Iscrizione da rinnovare non trovata.', 'code': '400'}
# The request's own person, certificate and custom data are replaced; only its type is kept.
sent = person('Altro', 'Nome', 'LTRNMO90C01H501K', type=1)


def renewal(label, *, kind=1, **extra):
    return request({**sent, 'type': kind}, preregistration=True, subscription_id=SUBSCRIPTIONS[label]['id'], **extra)


add('preregistration-renewal-anonymous-not-found', 'anonymous', 400, NOT_RENEWABLE,
    body=renewal('renewable', sport_association=OWNER_NAME, **{k: v for k, v in FORM.items() if k != 'subscription_id'}))
add('preregistration-renewal-of-another-account-not-found', 'athlete', 400, NOT_RENEWABLE,
    body=renewal('current', sport_association=OWNER_NAME))
add('preregistration-renewal-of-another-association-not-found', 'owner', 400, NOT_RENEWABLE, body=renewal('abroad'))
add('preregistration-renewal-unknown-association-not-found', 'owner', 400, NOT_RENEWABLE,
    body=renewal('renewable', sport_association='nessuno'))
add('preregistration-renewal-unknown-not-found', 'owner', 400, NOT_RENEWABLE,
    body=request(sent, preregistration=True, subscription_id=UNKNOWN))
add('preregistration-renewal-malformed-id-not-found', 'owner', 400, NOT_RENEWABLE,
    body=request(sent, preregistration=True, subscription_id='non-un-id'))
add('preregistration-renewal-athlete-without-association', 'athlete', 403, {'detail': 'User is not a sport association'},
    body=renewal('renewable_member'))
add('preregistration-renewal-without-type-server-error', 'owner', 500, SERVER,
    body=request(person('Altro', 'Nome', 'LTRNMO90C01H501K'), preregistration=True, subscription_id=SUBSCRIPTIONS['renewable']['id']))
# A stored number that is not an integer: int() raises after the associate and the payment were written; nothing stays.
add('preregistration-renewal-text-number-server-error', 'owner', 500, SERVER, body=renewal('text_number'))

# The association renews its member's subscription: role 3, number 15, the certificate (its date re-assigned as a
# datetime, its account set), custom data and additional fields of the renewed one; the member's account keeps it.
effect = success('owner', 'Remo Rinnovo', associate={'match': 'renewed', 'columns': {'user_id': OWNER}},
                 associate_audit=[(1, {'user': [IDS['athlete'], OWNER]})],
                 payment=new_payment('25.00', audit_amount='25.0'),
                 subscription={'type': 1, 'role': 3, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', '$explicit_number': '15',
                               'user_id': IDS['athlete'], 'custom_data': {'tipo': 'agonista'}, 'additional_fields': {'taglia': 'L'}, **NEXT})
NUMBER[0] -= 1  # the renewed number is kept: the next default number does not move
linked(effect, 'renewed', columns={'user_id': OWNER, 'expiration_date': '2026-12-31'},
       audit={'user': ['None', OWNER], 'expiration_date': ['2026-12-31', '2026-12-31 00:00:00']}, index=[OWN, 'actor.sport_association'])
add('preregistration-renewal-by-association', 'owner', 200, SUCCESS(25.0),
    body=renewal('renewable', custom_data={'type_of_associate': 'ignorato'}, additional_fields={'ignorato': True}, medical_certificate=None),
    effect=next_season(effect))
add('preregistration-renewal-twice', 'owner', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=renewal('renewable'))

# The member renews its own subscription through the association's form: the associate already belongs to it.
renewed_member = person('Rina', 'Rinnovo', 'RNNRNI82A41H501S', born='01/01/1982')
effect = member_effect('Rina Rinnovo', renewed_member, member=IDS['athlete'], member_label='athlete', actor='athlete', broadcast=False)
effect['associate'] = {'match': 'renewed_member', 'columns': {}}
effect['audit'] = [e for e in effect['audit'] if e['model'] != 'associate']
effect['subscription'] = {**effect['subscription'], **NEXT}
effect['membership'] = {**effect['membership'], **NEXT_MEMBER}
add('preregistration-renewal-by-member', 'athlete', 200, SUCCESS(25.0),
    body=renewal('renewable_member', kind=2, sport_association=OWNER_NAME, **{k: v for k, v in FORM.items() if k != 'subscription_id'}),
    effect=next_season(effect))

# --- The family wizard with type grouped_subscriptions -------------------------------------------------------
# _get_asd_custom_data_for_grouped_subscriptions(request.user): the latest subscription of the account whose
# custom_data text contains 'asd-o-ssd' or 'scuola-asc' (any case) gives denomination, city, province, address,
# cap (its 'zip'), country ('Italia' without the key), tax_code and email ('' without the key); they are merged over
# every valid entry's custom_data. Without such a subscription nothing is merged; a stored value that is not an
# object raises before the transaction.
ASSOCIATES['grouped_club'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Club', 'last': 'Affiliato', 'tax_code': 'FFLCLB80A01H501G',
                              'born': '1980-01-01', 'user': 'owner', 'email': None}
ASSOCIATES['grouped_text'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Testo', 'last': 'Affiliato', 'tax_code': 'FFLTST80A01H501H',
                              'born': '1980-01-01', 'user': 'athlete', 'email': None}
SUBSCRIPTIONS['grouped_older'] = {'id': next(extra_ids), 'associate': 'grouped_club', 'start': '2024-01-01', 'end': '2024-12-31',
                                  'archived': True, 'number': '8', 'created': '2024-03-01T00:00:00Z',
                                  'custom_data': {'type_of_associate': 'asd-o-ssd', 'denomination': 'Vecchia ASD', 'city': 'Roma',
                                                  'zip': '00100'}}
SUBSCRIPTIONS['grouped_latest'] = {'id': next(extra_ids), 'associate': 'grouped_club', 'start': '2025-01-01', 'end': '2025-12-31',
                                   'archived': True, 'number': '9', 'created': '2025-03-01T00:00:00Z',
                                   'custom_data': {'type_of_associate': 'Scuola-ASC', 'denomination': 'Scuola Nuova', 'city': 'Milano',
                                                   'province': 'MI', 'address': 'Via Larga 1', 'zip': '20100', 'cap': 'ignorato',
                                                   'tax_code': 12345678901, 'email': None}}
SUBSCRIPTIONS['grouped_text'] = {'id': next(extra_ids), 'associate': 'grouped_text', 'start': '2025-01-01', 'end': '2025-12-31',
                                 'archived': True, 'number': None, 'user': 'athlete', 'created': '2025-03-01T00:00:00Z',
                                 'custom_data': 'Affiliata ASD-O-SSD'}
grouped_child = person('Gaia', 'Gruppo', 'GRPGAI10A41H501G', sex='F')
grouped = family_effect('anonymous', [grouped_child], kind='grouped_subscriptions')
for row in grouped['new']:
    if row['label'] == 'subscription':
        row['columns']['custom_data'] = {'city': 'Milano', 'nota': 'propria', 'denomination': 'Scuola Nuova', 'province': 'MI',
                                         'address': 'Via Larga 1', 'cap': '20100', 'country': 'Italia', 'tax_code': 12345678901,
                                         'email': None}
add('family-wizard-grouped', 'anonymous', 200, FAMILY_SUCCESS,
    body=family_body([family_entry(grouped_child, custom_data={'city': 'Propria', 'nota': 'propria'})], sport_association=OWNER_NAME,
                     type='grouped_subscriptions'),
    effect=grouped)
# The account's matching custom data is a string: .get raises before the Family is created.
add('family-wizard-grouped-text-custom-data-server-error', 'athlete', 500, SERVER,
    body=family_body([family_entry(person('Gino', 'Gruppo', 'GRPGNI10A01H501G'))], sport_association=OWNER_NAME,
                     type='grouped_subscriptions'))

# --- A renewing preregistration whose member has a tutor ---------------------------------------------------
# fill_data_preregisration also sends the renewed member's main tutor (get_main_tutor) as associate_tutor_data, its
# birth date as DD/MM/YYYY: create_subscription saves the tutor through its associate_id; a tutor the minor already
# has is not linked again.
late_ids = iter(f'a1000000-0000-4000-8000-{953001 + n:012d}' for n in range(40))
ASSOCIATES['renewed_minor'] = {'id': next(late_ids), 'association': OWN, 'first': 'Mino', 'last': 'Minore', 'tax_code': 'MNRMNI15A01H501M',
                               'born': '2015-01-01', 'user': 'owner', 'email': None, 'is_minor': True}
ASSOCIATES['renewed_tutor'] = {'id': next(late_ids), 'association': OWN, 'first': 'Tullio', 'last': 'Minore', 'tax_code': 'MNRTLL80A01H501T',
                               'born': '1980-01-01', 'user': 'owner', 'email': None}
RELATIONS = {'renewed_minor_tutor': {'associate': 'renewed_minor', 'tutor': 'renewed_tutor', 'primary': True}}
SUBSCRIPTIONS['renewable_minor'] = {'id': next(late_ids), 'associate': 'renewed_minor', 'start': '$season_start', 'end': '$season_end',
                                    'archived': False, 'number': '21'}
effect = success('owner', 'Mino Minore', associate={'match': 'renewed_minor', 'columns': {}}, associate_audit=[],
                 payment=new_payment('25.00', audit_amount='25.0'),
                 subscription={'type': 1, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', '$explicit_number': '21', **NEXT})
NUMBER[0] -= 1
effect['tutor'] = {'match': 'renewed_tutor', 'columns': {}}
add('preregistration-renewal-member-with-tutor', 'owner', 200, SUCCESS(25.0), body=renewal('renewable_minor'), effect=next_season(effect))

# A minor that already has a tutor, registered with another tutor: the new tutor is saved and not linked (the main
# tutor stays the only one).
ASSOCIATES['tutored_minor'] = {'id': next(late_ids), 'association': OWN, 'first': 'Pino', 'last': 'Pupillo', 'tax_code': 'PPLPNI15A01H501P',
                               'born': '2015-01-01', 'user': 'owner', 'email': None, 'is_minor': True}
ASSOCIATES['first_tutor'] = {'id': next(late_ids), 'association': OWN, 'first': 'Primo', 'last': 'Pupillo', 'tax_code': 'PPLPRM80A01H501P',
                             'born': '1980-01-01', 'user': 'owner', 'email': None}
RELATIONS['tutored_minor_tutor'] = {'associate': 'tutored_minor', 'tutor': 'first_tutor', 'primary': True}
second_tutor = person('Seconda', 'Pupillo', 'PPLSND82A41H501S', born='01/01/1982', sex='F')
effect = success('owner', 'Pino Pupillo', associate={'match': 'tutored_minor', 'columns': {'sex': 'M'}},
                 associate_audit=[(1, {'sex': ['A', 'M']})], tutor=created_associate(second_tutor),
                 payment=new_payment('25.00', audit_amount='25.0'), subscription=dict(PLAIN), membership=dict(MEMBER))
effect['audit'] = [e for e in effect['audit'] if e['model'] != 'associatetutorrelation']
# The minor's serializer save comes before the tutor's entries; its later save() changes nothing.
effect['audit'].sort(key=lambda e: 0 if e.get('ref') == 'associate' else 1)
add('minor-with-a-tutor-and-another-tutor', 'owner', 200, SUCCESS(25.0),
    body=request(person('Pino', 'Pupillo', 'PPLPNI15A01H501P', born='01/01/2015'), tutor=second_tutor), effect=effect)

# subscription_id and sport_association that are not strings: an integer key is uuid.UUID(int=...), other values are
# not UUIDs; a number as the association's username is compared as its text. None of them finds the subscription.
for name, key in (('integer', 5), ('boolean', True), ('float', 1.5), ('list', [SUBSCRIPTIONS['renewable']['id']]),
                  ('object', {'id': SUBSCRIPTIONS['renewable']['id']}), ('negative', -1), ('huge', 2 ** 130)):
    add(f'preregistration-renewal-{name}-id-not-found', 'owner', 400, NOT_RENEWABLE, body=request(sent, preregistration=True, subscription_id=key))
for name, named in (('number', 5), ('boolean', True), ('list', [OWNER_NAME])):
    add(f'preregistration-renewal-association-{name}-not-found', 'owner', 400, NOT_RENEWABLE, body=renewal('renewable', sport_association=named))

# --- A new member account with a role, or with a username ----------------------------------------------------
# UserSerializer validates role against the account roles and creates the account with it; only an athlete account gets
# the approval reminder. A username in new_member_info reaches create_user twice (TypeError: the 500 page).
def new_member_info(first, last, **extra):
    return {'new_member': True, 'new_member_info': {'first_name': first, 'last_name': last, 'phone': None,
                                                    'email': f'{first}.{last}@example.test'.lower(), **extra}}


add('new-member-role-not-a-choice', 'owner', 400, {'role': ['"9" is not a valid choice.']},
    body=request(person('Rocco', 'Ruolo', 'RLURCC90C01H501K'), account=new_member_info('Rocco', 'Ruolo', role=9)))
add('new-member-role-null', 'owner', 400, {'role': ['This field may not be null.']},
    body=request(person('Rocco', 'Ruolo', 'RLURCC90C01H501K'), account=new_member_info('Rocco', 'Ruolo', role=None)))
add('new-member-username-server-error', 'owner', 500, SERVER,
    body=request(person('Ugo', 'Utente', 'TNTGUO90C01H501K'), account=new_member_info('Ugo', 'Utente', username='ugo')))
add('new-member-username-blank', 'owner', 400, {'username': ['This field may not be blank.']},
    body=request(person('Ugo', 'Utente', 'TNTGUO90C01H501K'), account=new_member_info('Ugo', 'Utente', username='')))
for role, first in ((1, 'Sara'), ('3', 'Lara')):
    staff = person(first, 'Staff', f'STF{first[:3].upper()}90C41H501K')
    effect = member_effect(f'{first} Staff', staff, member='$new_user', member_label='$new_user', welcome=True,
                           new_user={'prefix': first[:3] + 'Sta', 'email': f'{first.lower()}.staff@example.test', 'first_name': first,
                                     'last_name': 'Staff', 'phone': None, 'role': int(role)})
    effect['celery'] = effect['celery'][:-1]
    add(f'new-member-role-{role}', 'owner', 200, SUCCESS(25.0), body=request(staff, account=new_member_info(first, 'Staff', role=role)),
        effect=effect)

# --- The quick re-subscription in an association-scoped session ----------------------------------------------
# The session's request names its association; scoped_queryset keeps both lookups inside it. The owner viewing as a
# collaborator re-subscribes the association's member; viewing as an athlete the lookups succeed and the answer is 403.
ASSOCIATES['quick_session'] = {'id': next(late_ids), 'association': OWN, 'first': 'Quirino', 'last': 'Sessione', 'tax_code': 'SSSQRN80A01H501Q',
                               'born': '1980-01-01', 'user': 'owner', 'email': None}
SCOPED_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
add('quick-scoped-session-without-association', 'owner', 403, {'detail': 'Seleziona la tua associazione.'}, query='?m=only_associate',
    headers=SCOPED_FULL, body={'associate': ASSOCIATES['quick_session']['id']})
add('quick-scoped-session-athlete-not-allowed', 'owner', 403, {'detail': 'User not allowed.'}, query='?m=only_associate',
    headers=SCOPED_ATHLETE, body={'sport_association': OWN, 'associate': ASSOCIATES['renewed_member']['id']})
quick('owner', 'quick_session', 'Quirino Sessione', headers=SCOPED_FULL, payment={'amount': '25.00', '$audit': '25.00'}, subscription={})
cases[-1]['body']['sport_association'] = OWN

# --- A birth date with both numbers at most 12 -------------------------------------------------------------------
# The age reads the day first: '$day_first_minor' (http_family.day_first_dates) is under 18 today and would be 18 read
# month first. A minor's tutor without a city takes the minor's.
young = person('Dina', 'Giovane', 'GVNDNI08T45H501K', born='$day_first_minor', is_minor=True, sex='F', address_city='Asti')
young_tutor = person('Pino', 'Giovane', 'GVNPNI80A01H501M', born='01/01/1980')
effect = success('owner', 'Dina Giovane', associate=created_associate(young, is_minor=True, address_city='Asti'),
                 tutor=created_associate(young_tutor, address_city='Asti'), relation=True,
                 payment=new_payment('25.00', audit_amount='25.0'),
                 subscription={'type': 2, 'role': 1, 'status_flag': 1, 'meta': NO_PLANS, 'subscription_type': '', **SEASON},
                 membership={'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                             'price': None})
effect['associate']['columns']['born_date'] = '$day_first_minor_iso'
add('minor-day-first-birth-date', 'owner', 200, SUCCESS(25.0), body=request(young, tutor=young_tutor), effect=effect)

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'subscription/add authored sequence with row, audit, Celery and notification effects',
    'ids': IDS, 'associates': ASSOCIATES, 'certificates': CERTIFICATES, 'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS, 'relations': RELATIONS,
    'account': ACCOUNT, 'settings': BASE_SETTINGS, 'unknown': UNKNOWN, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
