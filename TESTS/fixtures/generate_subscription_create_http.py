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

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'subscription/add authored sequence with row, audit, Celery and notification effects',
    'ids': IDS, 'associates': ASSOCIATES, 'certificates': CERTIFICATES, 'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS,
    'account': ACCOUNT, 'settings': BASE_SETTINGS, 'unknown': UNKNOWN, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
