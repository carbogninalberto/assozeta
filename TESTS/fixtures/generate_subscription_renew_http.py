"""Author the subscription/renew and associates-draft approve HTTP sequence (subscription-renew.md).

subscription_renew (@api_view(['POST']), IsAuthenticated): for a non-association
user request.user becomes the owner of data['sport_association']['sport_association_id']
(outside the try: a missing key is a 500). Inside one transaction.atomic():
request.user.sport_association (logged), create_subscription(data, request.user, ...)
(subscription-create.md, plus AssociateSerializer.create's associate_id branch:
the row by id, or the association's row with the same tax code, updated with
deleted=False; an unknown id is created with that key), then the association's
unpaid subscription payments of an associate with the same first name, last
name and tax code (iexact) created between the new subscription's start and end
dates are deleted (QuerySet.delete(), the new payment excluded); data['user'] is not read (the renewed
subscription keeps the account it was created with); data['tags'] (ids or
{'tag_id'} dicts) of the association are set on the subscription; its data['courses'] are enrolled.
APIException (serializer errors, 409) propagates; any other exception is
400 {"status": "error", "msg": str(e)} with everything rolled back.
Response {"status": "success", "payment_id": ...}. The create_subscription
on_commit work (check_workflows_trigger, print_document_subscription, the
notification) runs after the outer commit.

subscription_associates_draft_approve (@api_view(['POST']), IsAuthenticated, not
atomic): athletes get an empty 401; without associate_import_draft_ids 400;
subscription_details gets type 1, plan ids None and role 1 by default; the
drafts of request.user.sport_association (all, or the listed ids that parse as
UUIDs), then only the valid ones (none: 400). The APPROVING status rows are
replaced by a new one; each draft's data is renamed for create_subscription
(new_member False, no signature, the details' type/role/plans), which runs in
its own transaction: success deletes the draft (its auditlog entry renders the
in-memory data, with the UUIDs create_subscription stored in it, as str(dict)),
any exception is swallowed. A draft without 'associate' raises before the try
(500, earlier drafts kept). The status rows are deleted at the end.

Run with ``python3 -I``.
"""
import json
from pathlib import Path
import uuid

OUTPUT = Path(__file__).with_name('subscription_renew_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER = IDS['owner']
ids = iter(f'a1000000-0000-4000-8000-{945001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
NEW_ID = next(ids)  # an associate_id no row has

ASSOCIATES = {
    'renewing': {'id': next(ids), 'association': OWN, 'first': 'Rino', 'last': 'Rinnovo', 'tax_code': 'RNNRNI80A01H501A', 'born': '1980-01-01',
                 'user': 'owner', 'email': None, 'deleted': False},
    'removed': {'id': next(ids), 'association': OWN, 'first': 'Remo', 'last': 'Rimosso', 'tax_code': 'RMSRMO80A01H501D', 'born': '1980-01-01',
                'user': 'owner', 'email': None, 'deleted': True},
    'twin_target': {'id': next(ids), 'association': OWN, 'first': 'Tina', 'last': 'Gemella', 'tax_code': 'GMLTNI80A41H501C',
                    'born': '1980-01-01', 'user': 'owner', 'email': 'tina@example.test', 'deleted': False},
    'twin_source': {'id': next(ids), 'association': FOREIGN, 'first': 'Tina', 'last': 'Gemella', 'tax_code': 'GMLTNI80A41H501C',
                    'born': '1980-01-01', 'user': 'foreign_owner', 'email': None, 'deleted': False},
    'moved': {'id': next(ids), 'association': FOREIGN, 'first': 'Fabio', 'last': 'Fuori', 'tax_code': 'FRIFBA80A01H501B', 'born': '1980-01-01',
              'user': 'foreign_owner', 'email': None, 'deleted': False},
    'current': {'id': next(ids), 'association': OWN, 'first': 'Dora', 'last': 'Doppia', 'tax_code': 'DPPDRO90A41H501X', 'born': '1990-01-01',
                'user': 'owner', 'email': None, 'deleted': False},
}
SUBSCRIPTIONS = {
    'previous': {'id': next(ids), 'associate': 'renewing', 'start': '$last_season_start', 'end': '$last_season_end', 'archived': False,
                 'number': '12', 'payment': 'stale'},
    'current': {'id': next(ids), 'associate': 'current', 'start': '$season_start', 'end': '$season_end', 'archived': False, 'number': '41',
                'payment': None},
}
PAYMENTS = {
    # Unpaid subscription payments created today (in the season): deleted by a renewal of the same person.
    'stale': {'id': next(ids), 'associate': 'renewing', 'amount': '30.00', 'paid': False, 'subject': 1, 'date': '$today'},
    'kept_paid': {'id': next(ids), 'associate': 'renewing', 'amount': '30.00', 'paid': True, 'subject': 1, 'date': '$today'},
    'kept_course': {'id': next(ids), 'associate': 'renewing', 'amount': '30.00', 'paid': False, 'subject': 2, 'date': '$today'},
    # The same amount and day as the renewal's fee: reused, so excluded from the clean-up.
    'removed_reused': {'id': next(ids), 'associate': 'removed', 'amount': '25.00', 'paid': False, 'subject': 1, 'date': '$today'},
    'removed_stale': {'id': next(ids), 'associate': 'removed', 'amount': '40.00', 'paid': False, 'subject': 1, 'date': '$today'},
}
# An unpaid subscription payment with an invoice (payment cancel and invoice generation leave them): the renewal's
# QuerySet.delete() removes the payment; the invoice stays.
# A member whose account is an athlete: the athlete renews from the athlete area.
ASSOCIATES['athlete_member'] = {'id': next(ids), 'association': OWN, 'first': 'Aurora', 'last': 'Atleta', 'tax_code': 'TLTRRA95A41H501W',
                                'born': '1995-01-01', 'user': 'athlete', 'email': None, 'deleted': False}
ASSOCIATES['invoiced'] = {'id': next(ids), 'association': OWN, 'first': 'Ivo', 'last': 'Fatturato', 'tax_code': 'FTTVIO80A01H501E',
                          'born': '1980-01-01', 'user': 'owner', 'email': None, 'deleted': False}
INVOICES = {'stale_invoice': {'id': next(ids)}}
PAYMENTS['invoiced_stale'] = {'id': next(ids), 'associate': 'invoiced', 'amount': '40.00', 'paid': False, 'subject': 1, 'date': '$today',
                              'invoice': 'stale_invoice'}
TAGS = {
    'tag_a': {'id': next(ids), 'association': OWN, 'name': 'Agonisti'},
    'tag_b': {'id': next(ids), 'association': OWN, 'name': 'Bambini'},
    'tag_other': {'id': next(ids), 'association': FOREIGN, 'name': 'Altro'},
}
CERTIFICATES = {
    'draft_cert': {'id': next(ids), 'user': None},
}
# Ids added later come from their own range so the earlier ones stay stable.
extra_ids = iter(f'a1000000-0000-4000-8000-{949001 + n:012d}' for n in range(50))
FAMILY = next(extra_ids)
ASSOCIATES['athlete_member']['family'] = FAMILY
# Members a member account renews (or not): the requester's own second member, a relative (same family), a person
# stored in two associations with one tax code, and a member of the association with no relation to the requester.
ASSOCIATES['second'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Bruna', 'last': 'Seconda', 'tax_code': 'SCNBRN85A41H501R',
                        'born': '1985-01-01', 'user': 'athlete', 'email': None, 'deleted': False}
ASSOCIATES['relative'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Renata', 'last': 'Parente', 'tax_code': 'PRNRNT85A41H501P',
                          'born': '1985-01-01', 'user': 'owner', 'email': None, 'deleted': False, 'family': FAMILY}
ASSOCIATES['coded_earlier'] = {'id': next(extra_ids), 'association': FOREIGN, 'first': 'Carla', 'last': 'Codice', 'tax_code': 'CDCCRL85A41H501Q',
                               'born': '1985-01-01', 'user': 'athlete', 'email': None, 'deleted': False}
ASSOCIATES['coded_later'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Carla', 'last': 'Codice', 'tax_code': 'CDCCRL85A41H501Q',
                             'born': '1985-01-01', 'user': 'owner', 'email': None, 'deleted': False}
ASSOCIATES['stranger'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Sandra', 'last': 'Estranea', 'tax_code': 'STRSDR85A41H501S',
                          'born': '1985-01-01', 'user': 'owner', 'email': None, 'deleted': False}
for label, associate, account, start, end, number in (
        ('athlete_previous', 'athlete_member', 'athlete', '$last_season_start', '$last_season_end', '8'),
        ('second_previous', 'second', 'athlete', '$last_season_start', '$last_season_end', '9'),
        ('relative_previous', 'relative', None, '$last_season_start', '$last_season_end', '10'),
        ('coded_earlier_2019', 'coded_earlier', 'athlete', '2019-01-01', '2019-12-31', '11'),
        ('coded_later_previous', 'coded_later', None, '$last_season_start', '$last_season_end', '13'),
        ('stranger_previous', 'stranger', None, '$last_season_start', '$last_season_end', '14')):
    SUBSCRIPTIONS[label] = {'id': next(extra_ids), 'associate': associate, 'start': start, 'end': end, 'archived': False, 'number': number,
                            'payment': None, **({'user': account} if account else {})}
CERTIFICATES['athlete_cert'] = {'id': next(extra_ids), 'user': 'athlete'}
# Members stored with a blank tax code, one of the requester in the other association and one of this association:
# a blank tax code is not a family link (family_tax_codes).
ASSOCIATES['blank_earlier'] = {'id': next(extra_ids), 'association': FOREIGN, 'first': 'Vera', 'last': 'Vuota', 'tax_code': '',
                               'born': '1985-01-01', 'user': 'athlete', 'email': None, 'deleted': False}
ASSOCIATES['blank_stranger'] = {'id': next(extra_ids), 'association': OWN, 'first': 'Zita', 'last': 'Senza', 'tax_code': '',
                                'born': '1985-01-01', 'user': 'owner', 'email': None, 'deleted': False}
SUBSCRIPTIONS['blank_earlier_2019'] = {'id': next(extra_ids), 'associate': 'blank_earlier', 'start': '2019-01-01', 'end': '2019-12-31',
                                       'archived': False, 'number': '15', 'payment': None, 'user': 'athlete'}
SUBSCRIPTIONS['blank_stranger_previous'] = {'id': next(extra_ids), 'associate': 'blank_stranger', 'start': '$last_season_start',
                                            'end': '$last_season_end', 'archived': False, 'number': '16', 'payment': None}
# Courses of the renewal form (add_course_to_subscription) and the members renewed with them or in a session.
course_ids = iter(f'a1000000-0000-4000-8000-{951001 + n:012d}' for n in range(60))
PLAN_EVENTS = [{'id': 0, 'amount': '20.00', 'payment_date': '10/01/2020'}, {'id': 1, 'amount': '30.00', 'payment_date': '10/01/2099'}]


def course(title, *, association=OWN, fee='40.00', multi=False, events=None, course_type=1, quotes=None, deleted=False):
    return {'id': next(course_ids), 'association': association, 'title': title, 'fee': fee, 'multi_payments': multi, 'events': events,
            'course_type': course_type, 'multiple_quotes': quotes, 'deleted': deleted}


COURSES = {
    'fee': course('Nuoto'),
    'plan': course('Danza', fee='0.00', multi=True, events=PLAN_EVENTS),
    'free': course('Libero', fee='0.00'),
    'quotes': course('Pilates', fee='15.00', course_type=2, quotes=[{'quote_id': 'q1', 'title': 'Mensile', 'amount': '22,50'}]),
    'abroad': course('Lontano', association=FOREIGN, fee='18.00'),
    'removed': course('Rimosso', deleted=True),
    'no_fee': course('Senza quota', fee=None),
    'broken_plan': course('Rate errate', fee='0.00', multi=True, events=[{'id': 0, 'amount': 'dieci', 'payment_date': '10/01/2099'}]),
}
ASSOCIATES['coursed'] = {'id': next(course_ids), 'association': OWN, 'first': 'Carlo', 'last': 'Corsista', 'tax_code': 'CRSCRL85A01H501T',
                         'born': '1985-01-01', 'user': 'owner', 'email': None, 'deleted': False}
ASSOCIATES['session_owner'] = {'id': next(course_ids), 'association': OWN, 'first': 'Sergio', 'last': 'Sessione',
                               'tax_code': 'SSSSRG85A01H501U', 'born': '1985-01-01', 'user': 'owner', 'email': None, 'deleted': False}
ASSOCIATES['session_member'] = {'id': next(course_ids), 'association': OWN, 'first': 'Silvia', 'last': 'Sessione',
                                'tax_code': 'SSSSLV85A41H501V', 'born': '1985-01-01', 'user': 'athlete', 'email': None, 'deleted': False}
ASSOCIATES['session_namesake'] = {'id': next(course_ids), 'association': OWN, 'first': 'Silvia', 'last': 'Sessione',
                                  'tax_code': 'SSSSLV85A41H501V', 'born': '1985-01-01', 'user': 'owner', 'email': None, 'deleted': True}
SUBSCRIPTIONS['session_member_previous'] = {'id': next(course_ids), 'associate': 'session_member', 'start': '$last_season_start',
                                            'end': '$last_season_end', 'archived': False, 'number': '17', 'payment': None, 'user': 'athlete'}
PAYMENTS['session_stale'] = {'id': next(course_ids), 'associate': 'session_member', 'amount': '30.00', 'paid': False, 'subject': 1,
                             'date': '$today'}
PAYMENTS['session_namesake_stale'] = {'id': next(course_ids), 'associate': 'session_namesake', 'amount': '31.00', 'paid': False, 'subject': 1,
                                      'date': '$today'}
TARGET_USER = {'id': next(ids), 'username': 'renew-target-user', 'email': 'renew-target@example.test'}
ACCOUNT = next(ids)


def draft_person(first, last, tax_code, born='01/01/1990', **extra):
    return {'first_name': first, 'last_name': last, 'tax_code': tax_code, 'born_date': born, 'sex': 'F', 'email': None, **extra}


def draft_data(associate, *, medical=None, tutor=None):
    return {'associate': associate, 'associate_tutor': tutor, 'new_user_account': {'new_member': False},
            'signature': {'there_is_signature': False, 'data': ''}, 'medical_certificate': medical or {'medical_id': None}}


# Seeded in this order (the table's physical order, which iterator() follows).
DRAFTS = {
    'ok_first': {'id': next(ids), 'association': OWN, 'valid': True,
                 'data': draft_data(draft_person('Alba', 'Prima', 'PRMLBA90A41H501E'))},
    'invalid': {'id': next(ids), 'association': OWN, 'valid': False,
                'data': draft_data(draft_person('Ines', 'Invalida', 'NVLNSI90A41H501F', born='1990-01-01'))},
    'duplicate': {'id': next(ids), 'association': OWN, 'valid': True,
                  'data': draft_data(draft_person('Dora', 'Doppia', 'DPPDRO90A41H501X'))},
    'bad_born': {'id': next(ids), 'association': OWN, 'valid': True,
                 'data': draft_data(draft_person('Bice', 'Senzadata', 'SNZBCI90A41H501G', born=''))},
    'abroad': {'id': next(ids), 'association': FOREIGN, 'valid': True,
               'data': draft_data(draft_person('Olga', 'Estera', 'STROLG90A41H501H'))},
    'all_first': {'id': next(ids), 'association': OWN, 'valid': True,
                  'data': draft_data(draft_person('Carla', 'Tutte', 'TTTCRL90A41H501I', phone='3330001111'))},
    'all_medical': {'id': next(ids), 'association': OWN, 'valid': True,
                    'data': draft_data(draft_person('Elena', 'Medica', 'MDCLNE90A41H501J'),
                                       medical={'medical_id': CERTIFICATES['draft_cert']['id'], 'filename': 'certificato.pdf',
                                                'certificate_expring_date': None})},
    'ok_second': {'id': next(ids), 'association': OWN, 'valid': False,
                  'data': draft_data(draft_person('Gaia', 'Seconda', 'SCNGAI90A41H501K'),
                                      medical={'medical_id': None, 'filename': '', 'certificate_expring_date': '2027-03-31'})},
    # Valid only from the crash case on: its data has no 'associate'.
    'crash': {'id': next(ids), 'association': OWN, 'valid': False,
              'data': {'associate_tutor': None, 'new_user_account': {'new_member': False}}},
}
STATUSES = {
    'old_approving': {'id': next(ids), 'association': OWN, 'doing': True, 'type': 2},
    'importing': {'id': next(ids), 'association': OWN, 'doing': False, 'type': 1},
}

BASE_SETTINGS = {'association': {'enable_quotes_management': True, 'subscription_fee': '25.00', 'membership_fee': None,
                                 'multiple_subscription_fee': False, 'multiple_membership_fee': False,
                                 'subscription_fee_plans': [], 'membership_fee_plans': []},
                 'owner': {'subscription_start_day': 1, 'subscription_start_month': 1, 'subscription_duration': 2, 'membership_duration': 2,
                           'membership_starting_number': 0, 'default_membership_type': '', 'show_zero_payments': False}}

cases = []
NUMBER = [41]
BROADCAST = [False]
SEASON = {'start_date': '$season_start', 'end_date': '$season_end'}
NO_PLANS = {'plan_id': None, 'plan_name': None, 'membership_plan_id': None, 'membership_plan_name': None}
ACTOR_ASSOCIATION = {'owner': OWN, 'admin': IDS['admin_association']}


def add(name, route, actor, status, response, *, body=None, method='POST', headers=None, effect=None, setup=None):
    path = {'renew': '/subscription/renew', 'approve': '/subscription/associates-draft/list/approve'}[route]
    cases.append({'name': f'subscription-renew-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect, 'body': body, 'setup': setup})


def person(first, last, tax_code, born='15/03/1990', **extra):
    return {'first_name': first, 'last_name': last, 'tax_code': tax_code, 'born_date': born, 'sex': 'M', **extra}


def renewal(associate, **extra):
    return {'new_user_account': {'new_member': False}, 'plan_id': None, 'membership_plan_id': None, 'associate_data': associate,
            'signature': {'there_is_signature': False, 'data': ''}, 'medical_certificate': {'medical_id': None}, 'custom_data': {},
            'additional_fields': {}, 'courses': [], **extra}


def error(message):
    return {'status': 'error', 'msg': message}


def entry(model, action, ref, changes=None, index=(OWN, 'direct'), app='application', **extra):
    return {'model': model, 'action': action, 'ref': ref, 'changes': changes or {}, 'index': list(index) if index else None, 'app': app,
            **extra}


def notification_entries(actor):
    """validate_broadcast's two create entries, the first time the owner is notified."""
    if BROADCAST[0]:
        return []
    BROADCAST[0] = True
    association = ACTOR_ASSOCIATION.get(actor)
    return [entry(model, 0, model, index=(association, 'actor.sport_association') if association else None, app='notifications')
            for model in ('notificationsbroadcast', 'usernotificationsbroadcast')]


def subscription_rows(bind, associate_ref, *, kind, payment, user='$owner', medical=None, number=None, renewal_form=True, season=None):
    """The new subscription (and membership for types 2 and 3) of one create_subscription call."""
    if number is None:
        NUMBER[0] += 1
        number = str(NUMBER[0])
    rows = [{'label': 'subscription', 'bind': bind, 'match': {'associate_id': associate_ref},
             'columns': {'sport_association_id': OWN, 'user_id': user, 'payment_id': payment, 'type': kind, 'role': 1, 'status_flag': 1,
                         'meta': NO_PLANS, 'subscription_type': '', 'subscription_number': number, 'medical_id': medical,
                         'custom_data': {} if renewal_form else None, 'additional_fields': {} if renewal_form else None, **(season or SEASON)}}]
    if kind in (2, 3):
        rows.append({'label': 'membership', 'bind': bind + '_membership', 'match': {'subscription_id': bind},
                     'columns': {'sport_association_id': OWN, 'associate_id': associate_ref, 'payment_id': payment,
                                 'membership_type': '$default_membership_type', 'start_date': '$season_start', 'end_date': '$season_end',
                                 'price': None, 'membership_number': number}})
    return rows


def new_payment(bind, associate_ref, amount='25.00'):
    return {'label': 'payment', 'bind': bind, 'match': {'associate_id': associate_ref, 'amount': amount},
            'columns': {'subject': 1, 'description': 'Iscrizione', 'payment_category_id': '$category', 'user_id': '$owner',
                        'sport_association_id': OWN, 'custom_accounts_id': ACCOUNT, 'amount': amount, 'payment_date': '$today_midnight',
                        'meta_payment_categories': None, 'meta': None}}


def tasks(bind):
    return [{'task': 'check_workflows_trigger', 'args': [],
             'kwargs': [['trigger_type', 'new_subscription'], ['sport_association_id', {'uuid': OWN}], ['subscription_id', {'uuid': bind}]]},
            {'task': 'print_document_subscription', 'args': [bind, '$authorization'], 'kwargs': []}]


def effect(actor, *, new=(), changed=None, deleted=None, tags=(), audit=(), celery=(), notifications=()):
    return {'actor': actor, 'new': list(new), 'changed': changed or {}, 'deleted': deleted or {}, 'tags': [list(t) for t in tags],
            'audit': list(audit), 'celery': list(celery), 'notifications': list(notifications)}


def jsonb_order(value):
    """The key order PostgreSQL's jsonb returns: shorter keys first, then bytewise."""
    if isinstance(value, dict):
        return {k: jsonb_order(value[k]) for k in sorted(value, key=lambda k: (len(k.encode()), k.encode()))}
    if isinstance(value, list):
        return [jsonb_order(v) for v in value]
    return value


def approved_data(label, details):
    """str() of the draft's data after the view's renaming and create_subscription's initial_data writes."""
    data = jsonb_order(DRAFTS[label]['data'])
    associate = data['associate']
    data['associate_data'] = associate
    data['associate_tutor_data'] = data['associate_tutor']
    data['new_user_account'] = {'new_member': False}
    data['signature'] = {'there_is_signature': False, 'data': ''}
    associate['type'] = details['type']
    associate['role'] = details['role']
    data['membership_plan_id'] = details['membership_plan_id']
    data['plan_id'] = details['plan_id']
    associate['sport_association'] = uuid.UUID(OWN)
    associate['user'] = uuid.UUID(OWNER)
    return str(data)


def draft_associate(label, bind):
    person = DRAFTS[label]['data']['associate']
    columns = {'first_name': person['first_name'], 'last_name': person['last_name'], 'tax_code': person['tax_code'],
               'born_date': '-'.join(reversed(person['born_date'].split('/'))), 'sex': person['sex'], 'email': None,
               'sport_association_id': OWN, 'user_id': '$owner'}
    if 'phone' in person:
        columns['phone'] = person['phone']
    return {'label': 'associate', 'bind': bind, 'match': {'first_name': person['first_name'], 'last_name': person['last_name']},
            'columns': columns}


def placeholder_text(first, last, moment):
    return f'''
            {first} {last}\n
            \n
            Questa certificazione è valida fino al {moment}\n
            \n
            \n
            Attenzione: questo è un placeholder, il certificato medico deve essere caricato in seguito.
                        '''


def placeholder_parts(first, last, iso):
    """A typed certificate expiration date without an upload: the Document, the stored PNG and the association account's
    certificate (rows, audit entries after the associate's, and the storage expectation)."""
    filename = f'medical_certificate_{first}_{last}.png'
    rows = [{'label': 'document', 'bind': '$placeholder_document', 'match': {'filename': filename},
             'columns': {'filename': filename, 'token': '$any_uuid'}},
            {'label': 'medical', 'bind': '$placeholder_medical', 'match': {'document_id': '$placeholder_document'},
             'columns': {'expiration_date': iso, 'user_id': '$owner', 'document_id': '$placeholder_document'}}]
    audit = [entry('document', 0, '$placeholder_document', {'customer_invoice_pdf': ['None', 'application.CustomerInvoice.None']},
                   index=(OWN, 'actor.sport_association'), app='docmanager'),
             entry('medicalcertificate', 0, '$placeholder_medical', {'expiration_date': ['None', iso + ' 00:00:00']},
                   index=(OWN, 'actor.sport_association'))]
    return rows, audit, {'filename': filename, 'expiration': iso, 'text': placeholder_text(first, last, iso + ' 00:00:00+00:00')}


def approved_draft(label, actor, details, *, kind, medical=None, placeholder=None):
    """Rows and entries of one approved draft (associate created, payment, subscription, the draft deleted)."""
    associate, payment, subscription = f'${label}_associate', f'${label}_payment', f'${label}_subscription'
    person = DRAFTS[label]['data']['associate']
    extra_rows, extra_audit = (placeholder_parts(person['first_name'], person['last_name'], placeholder)[:2]) if placeholder else ([], [])
    rows = [draft_associate(label, associate), *extra_rows, new_payment(payment, associate),
            *subscription_rows(subscription, associate, kind=kind, payment=payment,
                               medical='$placeholder_medical' if placeholder else CERTIFICATES[medical['label']]['id'] if medical else None,
                               renewal_form=False)]
    audit = [entry('associate', 0, associate, {'user': ['None', 'None']}), entry('associate', 1, associate, {'user': ['None', OWNER]}),
             *extra_audit]
    if medical is not None:
        audit.append(entry('medicalcertificate', 1, medical['label'], {'user': ['None', OWNER]}, index=medical['index'] or None))
    audit.append(entry('payment', 0, payment, {'amount': ['None', '25.0']}))
    audit.append(entry('subscription', 0, subscription))
    if kind in (2, 3):
        audit.append(entry('subscriptionmembership', 0, subscription + '_membership'))
    audit.extend(notification_entries(actor))
    audit.append(entry('associateimportdraft', 2, label, {'data': [approved_data(label, details), 'None']},
                       index=(OWN, 'changes_json.sport_association'), deleted=True))
    return rows, audit, tasks(subscription), [{'msg': f"Nuova Iscrizione aggiunta per {person['first_name']} {person['last_name']}.",
                                                'association': OWN}]


def status_entries(*, delete_old=False, create=True, delete_new=True):
    audit = []
    if delete_old:
        audit.append(entry('associateimportdraftstatus', 2, 'old_approving', index=(OWN, 'changes_json.sport_association'), deleted=True))
    if create:
        audit.append(entry('associateimportdraftstatus', 0, '$status', index=(OWN, 'direct'), status='create'))
    if delete_new:
        audit.append(entry('associateimportdraftstatus', 2, '$status', index=(OWN, 'changes_json.sport_association'), status='delete'))
    return audit


# --- subscription/renew: transport, identity and the view's own checks ------------------------------------
basic = person('Rita', 'Rossa', 'RSSRTI90C41H501K')
add('renew-options', 'renew', 'owner', 200, {'name': 'Subscription Renew', 'description': '', 'renders': ['application/json'],
                                             'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('renew-wrong-method', 'renew', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('renew-anonymous', 'renew', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'}, body=renewal(basic))
add('renew-restricted-missing-permission', 'renew', 'restricted_collaborator', 403,
    {'detail': 'Missing permission: association.members.create'}, body=renewal(basic))
add('renew-admin-without-session', 'renew', 'admin', 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'}, body=renewal(basic))
add('renew-athlete-without-association-key', 'renew', 'athlete', 500, SERVER, body=renewal(basic))
add('renew-owner-without-association', 'renew', 'owner_without_association', 400,
    error('SportAssociation matching query does not exist.'), body=renewal(basic))
add('renew-list-body', 'renew', 'owner', 400, error("'list' object has no attribute 'keys'"), body=[])
add('renew-missing-associate-data', 'renew', 'owner', 400, error("'associate_data'"),
    body={k: v for k, v in renewal(basic).items() if k != 'associate_data'})
add('renew-associate-data-null', 'renew', 'owner', 400, error("'NoneType' object is not subscriptable"), body=renewal(None))
add('renew-associate-data-list', 'renew', 'owner', 400, error('list indices must be integers or slices, not str'), body=renewal([]))
add('renew-born-date-missing', 'renew', 'owner', 400, error("'born_date'"),
    body=renewal({k: v for k, v in basic.items() if k != 'born_date'}))
add('renew-born-date-empty', 'renew', 'owner', 400, error('String does not contain a date: '), body=renewal({**basic, 'born_date': ''}))
add('renew-born-date-none', 'renew', 'owner', 400, error('Unknown string format: None'), body=renewal({**basic, 'born_date': None}))
add('renew-tutor-sex-missing', 'renew', 'owner', 400, error("'sex'"),
    body=renewal({k: v for k, v in basic.items() if k != 'sex'}, associate_tutor_data=person('Tito', 'Rossa', 'RSSTTI60A01H501Z')))
add('renew-tutor-email-not-text', 'renew', 'owner', 400, error("'int' object has no attribute 'strip'"),
    body=renewal({**basic, 'email': 5}, associate_tutor_data=person('Tito', 'Rossa', 'RSSTTI60A01H501Z')))
add('renew-missing-new-user-account', 'renew', 'owner', 400, error("'new_user_account'"),
    body={k: v for k, v in renewal(basic).items() if k != 'new_user_account'})
add('renew-missing-signature', 'renew', 'owner', 400, error("'signature'"),
    body={k: v for k, v in renewal(basic).items() if k != 'signature'})
add('renew-medical-certificate-text', 'renew', 'owner', 400, error("'str' object has no attribute 'keys'"),
    body=renewal(basic, medical_certificate='nessuno'))
add('renew-medical-certificate-none', 'renew', 'owner', 400, error("'NoneType' object has no attribute 'keys'"),
    body=renewal(basic, medical_certificate=None))
add('renew-medical-id-malformed', 'renew', 'owner', 400, error("['“abc” is not a valid UUID.']"),
    body=renewal(basic, medical_certificate={'medical_id': 'abc'}))
add('renew-type-not-a-number', 'renew', 'owner', 400, error("invalid literal for int() with base 10: 'due'"),
    body=renewal({**basic, 'type': ' due '}))
add('renew-associate-id-malformed', 'renew', 'owner', 400, error("['“non-un-id” is not a valid UUID.']"),
    body=renewal({**basic, 'associate_id': ' non-un-id '}))
# A serializer ValidationError is an APIException: DRF renders it.
add('renew-associate-invalid-fields', 'renew', 'owner', 400, {
    'born_date': ['Date has wrong format. Use one of these formats instead: DD/MM/YYYY.'], 'sex': ['"X" is not a valid choice.']},
    body=renewal({**basic, 'born_date': '1990-03-15', 'sex': 'X'}))
add('renew-duplicate-current', 'renew', 'owner', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=renewal(person('Dora', 'Doppia', 'DPPDRO90A41H501X', born='01/01/1990')))
add('renew-membership-plan-required', 'renew', 'owner', 400, {'msg': 'Membership plan id is required.', 'code': '400'},
    setup={'association': {'multiple_membership_fee': True}},
    body={k: v for k, v in renewal({**basic, 'type': 2}).items() if k != 'membership_plan_id'})
# Errors after create_subscription roll everything back (no task, no notification).
add('renew-tags-none', 'renew', 'owner', 400, error("object of type 'NoneType' has no len()"), body=renewal(basic, tags=None))
add('renew-tag-malformed', 'renew', 'owner', 400, error("['“x” is not a valid UUID.']"), body=renewal(basic, tags=['x']))
add('renew-tag-without-id', 'renew', 'owner', 400, error("'tag_id'"), body=renewal(basic, tags=[{'id': TAGS['tag_a']['id']}]))
add('renew-courses-none', 'renew', 'owner', 400, error("object of type 'NoneType' has no len()"), body=renewal(basic, courses=None))

# --- subscription/renew: success paths -------------------------------------------------------------------
# The renewal form's body: the previous associate (associate_id), its association as a dict (no username matches str(dict)),
# the owner's id as `user` (a str without 'email'), tags as dicts. The renewing person's unpaid subscription payment of
# this season is deleted (the previous subscription's reference is cleared); paid and course payments stay.
# Only the association's own tags are set (tag_other belongs to another association).
renewing = {'associate_id': ASSOCIATES['renewing']['id'], 'first_name': 'Rino', 'last_name': 'Rinnovo', 'tax_code': 'RNNRNI80A01H501A',
            'born_date': '01/01/1980', 'sex': 'M', 'email': 'rino@example.test', 'user': OWNER, 'sport_association': OWN, 'is_minor': False,
            'family': None, 'phone': '3331112222', 'type': 1, 'role': 1}
rows = [new_payment('$renew_payment', ASSOCIATES['renewing']['id']),
        *subscription_rows('$renew_subscription', ASSOCIATES['renewing']['id'], kind=1, payment='$renew_payment')]
add('renew-basic', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal(renewing, sport_association={'sport_association_id': OWN, 'denomination': 'Associazione'}, user=OWNER,
                 tags=[{'tag_id': TAGS['tag_a']['id']}, {'tag_id': TAGS['tag_other']['id']}]),
    effect=effect('owner', new=rows,
                  changed={'associate': {'renewing': {'sex': 'M', 'email': 'rino@example.test', 'phone': '3331112222'}},
                           'subscription': {'previous': {'payment_id': None}}},
                  deleted={'payment': ['stale']},
                  tags=[('$renew_subscription', 'tag_a')],
                  audit=[entry('associate', 1, 'renewing', {'sex': ['A', 'M'], 'email': ['None', 'rino@example.test'],
                                                            'phone': ['None', '3331112222']}),
                         entry('payment', 0, '$renew_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$renew_subscription'),
                         entry('payment', 2, 'stale', index=(OWN, 'changes_json.sport_association'), deleted=True),
                         *notification_entries('owner')],
                  celery=tasks('$renew_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Rino Rinnovo.', 'association': OWN}]))

# An associate_id no row has: the associate is created with that key. data['user'] is not read: the renewed subscription
# keeps the account it was created with, whatever the body names (an e-mail of an account, a value of any other shape).
# Tags as ids.
fresh = {'associate_id': NEW_ID, 'first_name': 'Nadia', 'last_name': 'Nuova', 'born_date': '02/02/1992', 'sex': 'F', 'type': '2'}
rows = [{'label': 'associate', 'bind': '$fresh_associate', 'match': {'associate_id': NEW_ID},
         'columns': {'first_name': 'Nadia', 'last_name': 'Nuova', 'born_date': '1992-02-02', 'sex': 'F', 'tax_code': None,
                     'sport_association_id': OWN, 'user_id': '$owner'}},
        new_payment('$fresh_payment', NEW_ID),
        *subscription_rows('$fresh_subscription', NEW_ID, kind=2, payment='$fresh_payment')]
for name, user_value in (('renew-new-associate-id-user-email-not-linked', {'email': TARGET_USER['email']}),):
    add(name, 'renew', 'full_collaborator', 200, {'status': 'success', 'payment_id': '$new'},
        body=renewal(fresh, user=user_value, tags=[TAGS['tag_b']['id']]),
        effect=effect('full_collaborator', new=rows, tags=[('$fresh_subscription', 'tag_b')],
                      audit=[entry('associate', 0, '$fresh_associate', {'user': ['None', 'None']}),
                             entry('associate', 1, '$fresh_associate', {'user': ['None', OWNER]}),
                             entry('payment', 0, '$fresh_payment', {'amount': ['None', '25.0']}),
                             entry('subscription', 0, '$fresh_subscription', {'user': ['None', OWNER]}),
                             entry('subscriptionmembership', 0, '$fresh_subscription_membership'),
                             *notification_entries('full_collaborator')],
                      celery=tasks('$fresh_subscription'),
                      notifications=[{'msg': 'Nuova Iscrizione aggiunta per Nadia Nuova.', 'association': OWN}]))

# associate_id of a row in another association with a tax code: the association's own row with that tax code (iexact) is
# the one updated (the tax code as sent); the row named by the id is unchanged.
twin = {'associate_id': ASSOCIATES['twin_source']['id'], 'first_name': 'Tina', 'last_name': 'Gemella', 'tax_code': 'gmltni80a41h501c',
        'born_date': '01/01/1980', 'sex': 'F'}
rows = [new_payment('$twin_payment', ASSOCIATES['twin_target']['id']),
        *subscription_rows('$twin_subscription', ASSOCIATES['twin_target']['id'], kind=1, payment='$twin_payment')]
add('renew-associate-id-tax-code-in-association', 'renew', 'admin', 200, {'status': 'success', 'payment_id': '$new'},
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    body=renewal({**twin, 'type': 1}),
    effect=effect('admin', new=rows, changed={'associate': {'twin_target': {'tax_code': 'gmltni80a41h501c', 'sex': 'F'}}},
                  audit=[entry('associate', 1, 'twin_target', {'tax_code': ['GMLTNI80A41H501C', 'gmltni80a41h501c'], 'sex': ['A', 'F']}),
                         entry('payment', 0, '$twin_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$twin_subscription'),
                         *notification_entries('admin')],
                  celery=tasks('$twin_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Tina Gemella.', 'association': OWN}]))

# associate_id of a soft-deleted row: found through all_objects() (itself, by tax code) and saved with deleted=False. Its
# unpaid payment of today with the fee's amount is reused; the other unpaid one is deleted.
removed = {'associate_id': ASSOCIATES['removed']['id'], 'first_name': 'Remo', 'last_name': 'Rimosso', 'tax_code': 'RMSRMO80A01H501D',
           'born_date': '01/01/1980', 'sex': 'M', 'type': 1}
rows = subscription_rows('$removed_subscription', ASSOCIATES['removed']['id'], kind=1, payment=PAYMENTS['removed_reused']['id'])
add('renew-associate-id-deleted-row', 'renew', 'owner', 200, {'status': 'success', 'payment_id': PAYMENTS['removed_reused']['id']},
    body=renewal(removed, tags=[]),
    effect=effect('owner', new=rows, changed={'associate': {'removed': {'deleted': False, 'sex': 'M'}}},
                  deleted={'payment': ['removed_stale']},
                  # auditlog reads the previous state through the default manager, which hides the deleted row: the
                  # update entry renders every field against None.
                  # The resolver's objects.get() misses it too (the entry precedes the UPDATE): indexed from the changes.
                  audit=[entry('associate', 1, 'removed', created_style=True, index=(OWN, 'changes_json.sport_association')),
                         entry('subscription', 0, '$removed_subscription'),
                         entry('payment', 2, 'removed_stale', index=(OWN, 'changes_json.sport_association'), deleted=True),
                         *notification_entries('owner')],
                  celery=tasks('$removed_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Remo Rimosso.', 'association': OWN}]))

# associate_id of a row in another association whose tax code has no row here: that row is saved with this association.
moved = {'associate_id': ASSOCIATES['moved']['id'], 'first_name': 'Fabio', 'last_name': 'Fuori', 'tax_code': 'FRIFBA80A01H501B',
         'born_date': '01/01/1980', 'sex': 'M', 'type': 1}
rows = [new_payment('$moved_payment', ASSOCIATES['moved']['id']),
        *subscription_rows('$moved_subscription', ASSOCIATES['moved']['id'], kind=1, payment='$moved_payment')]
add('renew-associate-id-row-by-id', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal(moved, user={'email': 'nessuno@example.test'}),
    effect=effect('owner', new=rows, changed={'associate': {'moved': {'sport_association_id': OWN, 'sex': 'M', 'user_id': '$owner'}}},
                  # The update entry is written before the UPDATE: the resolver reads the row's previous association.
                  audit=[entry('associate', 1, 'moved', {'sport_association': [FOREIGN, OWN], 'sex': ['A', 'M']}, index=(FOREIGN, 'direct')),
                         entry('associate', 1, 'moved', {'user': [IDS['foreign_owner'], OWNER]}),
                         entry('payment', 0, '$moved_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$moved_subscription'),
                         *notification_entries('owner')],
                  celery=tasks('$moved_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Fabio Fuori.', 'association': OWN}]))

# --- associates-draft approve: transport, identity and the view's own checks -----------------------------
DETAILS = {'type': 1}
add('approve-options', 'approve', 'owner', 200, {'name': 'Subscription Associates Draft Approve', 'description': '',
                                                 'renders': ['application/json'],
                                                 'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']},
    method='OPTIONS')
add('approve-wrong-method', 'approve', 'owner', 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('approve-anonymous', 'approve', 'anonymous', 401, {'detail': 'Authentication credentials were not provided.'},
    body={'associate_import_draft_ids': [], 'subscription_details': DETAILS})
add('approve-restricted-missing-permission', 'approve', 'restricted_collaborator', 403,
    {'detail': 'Missing permission: association.members.create'}, body={'associate_import_draft_ids': [], 'subscription_details': DETAILS})
add('approve-admin-without-session', 'approve', 'admin', 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'},
    body={'associate_import_draft_ids': [], 'subscription_details': DETAILS})
add('approve-athlete', 'approve', 'athlete', 401, '<empty>', body={'associate_import_draft_ids': [], 'subscription_details': DETAILS})
add('approve-missing-ids', 'approve', 'owner', 400, {'msg': 'bad request, missing drafts to approve.'}, body={'subscription_details': DETAILS})
add('approve-list-body', 'approve', 'owner', 500, SERVER, body=[])
add('approve-missing-details', 'approve', 'owner', 500, SERVER, body={'associate_import_draft_ids': []})
add('approve-details-list', 'approve', 'owner', 500, SERVER, body={'associate_import_draft_ids': [], 'subscription_details': []})
add('approve-ids-not-iterable', 'approve', 'owner', 500, SERVER, body={'associate_import_draft_ids': 5, 'subscription_details': DETAILS})
add('approve-owner-without-association', 'approve', 'owner_without_association', 500, SERVER,
    body={'associate_import_draft_ids': [], 'subscription_details': DETAILS})
add('approve-nothing-valid', 'approve', 'owner', 400, {'msg': 'Dati mancanti, iscrizione non approvata.'},
    body={'associate_import_draft_ids': ['nessuno', 7, DRAFTS['invalid']['id'], DRAFTS['abroad']['id']], 'subscription_details': DETAILS})
add('approve-ids-text', 'approve', 'owner', 400, {'msg': 'Dati mancanti, iscrizione non approvata.'},
    body={'associate_import_draft_ids': DRAFTS['ok_first']['id'], 'subscription_details': DETAILS})

# --- associates-draft approve: success paths --------------------------------------------------------------
# The listed own drafts: ok_first is approved; duplicate (a current subscription: 409) and bad_born (dateutil fails) are
# kept; invalid is counted but not processed; another association's draft and malformed ids are not counted.
details = {'type': 1, 'plan_id': None, 'membership_plan_id': None, 'role': 1}
rows, audit, celery, notifications = approved_draft('ok_first', 'owner', details, kind=1)
add('approve-listed', 'approve', 'owner', 200, {'msg': '1 su 4 atleti approvati.', 'approved': 1},
    body={'associate_import_draft_ids': [DRAFTS['ok_first']['id'], DRAFTS['invalid']['id'], DRAFTS['duplicate']['id'],
                                         DRAFTS['bad_born']['id'], DRAFTS['abroad']['id'], 'non-un-id',
                                         '{' + DRAFTS['ok_first']['id'].upper() + '}'],
          'subscription_details': {'type': 1}},
    effect=effect('owner', new=rows, deleted={'draft': ['ok_first'], 'status': ['old_approving']},
                  audit=[*status_entries(delete_old=True)[:2], *audit, *status_entries(delete_old=False, create=False)],
                  celery=celery, notifications=notifications))

# all: every own valid draft; type 2 creates memberships. all_medical's certificate gets the association owner
# (certificate_expring_date None changes nothing).
details = {'type': 2, 'role': 1, 'plan_id': None, 'membership_plan_id': None}
first = approved_draft('all_first', 'full_collaborator', details, kind=2)
medical = approved_draft('all_medical', 'full_collaborator', details, kind=2,
                         medical={'label': 'draft_cert', 'index': None})
add('approve-all', 'approve', 'full_collaborator', 200, {'msg': '2 su 7 atleti approvati.', 'approved': 2},
    body={'associate_import_draft_ids': [], 'all': 'si', 'subscription_details': {'type': 2, 'role': 1}},
    effect=effect('full_collaborator', new=first[0] + medical[0],
                  changed={'medical': {'draft_cert': {'user_id': '$owner'}}},
                  deleted={'draft': ['all_first', 'all_medical']},
                  audit=[*status_entries(create=True, delete_new=False), *first[1], *medical[1],
                         *status_entries(create=False)],
                  celery=first[2] + medical[2], notifications=first[3] + medical[3]))

# A valid draft without 'associate' raises before the try: the earlier draft stays approved and the new status row stays.
details = {'type': 1, 'plan_id': None, 'membership_plan_id': None, 'role': 1}
rows, audit, celery, notifications = approved_draft('ok_second', 'owner', details, kind=1, placeholder='2027-03-31')
add('approve-draft-without-associate', 'approve', 'owner', 500, SERVER,
    setup={'drafts': {'ok_second': True, 'crash': True}},
    body={'associate_import_draft_ids': [DRAFTS['crash']['id'], DRAFTS['ok_second']['id']], 'subscription_details': {'type': 1}},
    effect=effect('owner', new=[*rows, {'label': 'status', 'bind': '$status', 'match': {'status_type': 2},
                                       'columns': {'sport_association_id': OWN, 'doing': True, 'status_type': 2}}],
                  deleted={'draft': ['ok_second']},
                  audit=[*status_entries(delete_new=False), *audit],
                  celery=celery, notifications=notifications))
# The draft's typed certificate expiration date: its placeholder image is stored although the request ends in the 500 page.
cases[-1]['effect']['placeholder'] = placeholder_parts('Gaia', 'Seconda', '2027-03-31')[2]

invoiced = {'associate_id': ASSOCIATES['invoiced']['id'], 'first_name': 'Ivo', 'last_name': 'Fatturato', 'tax_code': 'FTTVIO80A01H501E',
            'born_date': '01/01/1980', 'sex': 'M', 'type': 1}
rows = [new_payment('$invoiced_payment', ASSOCIATES['invoiced']['id']),
        *subscription_rows('$invoiced_subscription', ASSOCIATES['invoiced']['id'], kind=1, payment='$invoiced_payment')]
add('renew-cleans-invoiced-payment', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal(invoiced),
    effect=effect('owner', new=rows, changed={'associate': {'invoiced': {'sex': 'M'}}}, deleted={'payment': ['invoiced_stale']},
                  audit=[entry('associate', 1, 'invoiced', {'sex': ['A', 'M']}),
                         entry('payment', 0, '$invoiced_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$invoiced_subscription'),
                         entry('payment', 2, 'invoiced_stale', index=(OWN, 'changes_json.sport_association'), deleted=True),
                         *notification_entries('owner')],
                  celery=tasks('$invoiced_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Ivo Fatturato.', 'association': OWN}]))


# --- subscription/renew by an athlete -------------------------------------------------------------------
# A non-owner requester: request.user becomes the owner of
# scoped_queryset(SportAssociation.objects).get(sport_association_id=data['sport_association']['sport_association_id'])
# before the try block (a failed lookup is the 500 page); create_subscription then runs for that owner, the audit actor
# stays the athlete and the notification goes to the owner. The serializer's user is read-only: the member's account
# becomes the owner with the later associate.save() (its own entry).
athlete_member = {'associate_id': ASSOCIATES['athlete_member']['id'], 'first_name': 'Aurora', 'last_name': 'Atleta',
                  'tax_code': 'TLTRRA95A41H501W', 'born_date': '01/01/1995', 'sex': 'F', 'type': 1, 'role': 1}
add('renew-athlete-association-text', 'renew', 'athlete', 500, SERVER, body=renewal(athlete_member, sport_association=OWN))
add('renew-athlete-association-without-id', 'renew', 'athlete', 500, SERVER,
    body=renewal(athlete_member, sport_association={'denomination': 'Associazione'}))
add('renew-athlete-association-malformed-id', 'renew', 'athlete', 500, SERVER,
    body=renewal(athlete_member, sport_association={'sport_association_id': 'non-un-id'}))
add('renew-athlete-association-unknown', 'renew', 'athlete', 500, SERVER,
    body=renewal(athlete_member, sport_association={'sport_association_id': NEW_ID}))
add('renew-athlete-list-body', 'renew', 'athlete', 500, SERVER, body=[])
rows = [new_payment('$athlete_payment', ASSOCIATES['athlete_member']['id']),
        *subscription_rows('$athlete_subscription', ASSOCIATES['athlete_member']['id'], kind=1, payment='$athlete_payment')]
add('renew-athlete', 'renew', 'athlete', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal(athlete_member, sport_association={'sport_association_id': OWN, 'denomination': 'Associazione'}, user=IDS['athlete']),
    effect=effect('athlete', new=rows, changed={'associate': {'athlete_member': {'sex': 'F', 'user_id': OWNER}}},
                  audit=[entry('associate', 1, 'athlete_member', {'sex': ['A', 'F']}),
                         entry('associate', 1, 'athlete_member', {'user': [IDS['athlete'], OWNER]}),
                         entry('payment', 0, '$athlete_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$athlete_subscription'),
                         *notification_entries('athlete')],
                  celery=tasks('$athlete_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Aurora Atleta.', 'association': OWN}]))
# The renewal is refused as a duplicate once the athlete's member has a current subscription.
add('renew-athlete-duplicate', 'renew', 'athlete', 409, {'msg': 'Questa iscrizione esiste già.', 'code': '409', 'ref': 'subscription_duplicate'},
    body=renewal(athlete_member, sport_association={'sport_association_id': OWN}))


# --- subscription/renew by a member account: its own subscriptions, in their association -----------------
# _member_renewal_association: the named association must have a subscription of the requester, or of a person sharing a
# tax code or a family with one of the requester's subscriptions, for the associate of the request (by associate_id when
# it is a string, else by tax code); otherwise 403 'User not allowed.' before anything is written. The body's `user`
# does not choose the account of the renewed subscription.
NOT_ALLOWED = {'detail': 'User not allowed.'}
HERE = {'sport_association_id': OWN, 'denomination': 'Associazione'}


def member_body(label, **extra):
    row = ASSOCIATES[label]
    return {'associate_id': row['id'], 'first_name': row['first'], 'last_name': row['last'], 'tax_code': row['tax_code'],
            'born_date': '/'.join(reversed(row['born'].split('-'))), 'sex': 'F', 'type': 1, 'role': 1, **extra}


add('renew-athlete-in-another-association', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal(athlete_member, sport_association={'sport_association_id': FOREIGN}))
add('renew-athlete-another-member', 'renew', 'athlete', 403, NOT_ALLOWED, body=renewal(member_body('stranger'), sport_association=HERE))
add('renew-athlete-another-member-by-tax-code', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal({k: v for k, v in member_body('stranger').items() if k != 'associate_id'}, sport_association=HERE))
add('renew-athlete-blank-tax-code-member', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal(member_body('blank_stranger'), sport_association=HERE))
add('renew-athlete-associate-id-malformed', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal(member_body('relative', associate_id='non-un-id'), sport_association=HERE))
add('renew-athlete-associate-id-not-text', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal(member_body('relative', associate_id=5), sport_association=HERE))
add('renew-athlete-associate-id-empty', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal(member_body('relative', associate_id=''), sport_association=HERE))
add('renew-athlete-without-associate-data', 'renew', 'athlete', 403, NOT_ALLOWED, body=renewal(None, sport_association=HERE))
add('renew-athlete-tax-code-not-text', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal({**member_body('relative', tax_code=5), 'associate_id': None}, sport_association=HERE))


def member_renewal(name, label, full_name, *, changed, audit, medical=None, **extra):
    payment, subscription = f'${label}_payment', f'${label}_subscription'
    rows = [new_payment(payment, ASSOCIATES[label]['id']),
            *subscription_rows(subscription, ASSOCIATES[label]['id'], kind=1, payment=payment, medical=medical)]
    add(name, 'renew', 'athlete', 200, {'status': 'success', 'payment_id': '$new'},
        body=renewal(member_body(label), sport_association=HERE, **extra),
        effect=effect('athlete', new=rows, changed={'associate': {label: changed}},
                      audit=[*audit, entry('payment', 0, payment, {'amount': ['None', '25.0']}), entry('subscription', 0, subscription),
                             *notification_entries('athlete')],
                      celery=tasks(subscription),
                      notifications=[{'msg': f'Nuova Iscrizione aggiunta per {full_name}.', 'association': OWN}]))


# A person of the same family as one of the requester's subscriptions.
member_renewal('renew-athlete-family-member', 'relative', 'Renata Parente', changed={'sex': 'F'},
               audit=[entry('associate', 1, 'relative', {'sex': ['A', 'F']})])
# A person with the tax code of one of the requester's subscriptions.
member_renewal('renew-athlete-same-tax-code', 'coded_later', 'Carla Codice', changed={'sex': 'F'},
               audit=[entry('associate', 1, 'coded_later', {'sex': ['A', 'F']})])
# By tax code, without an associate_id: the requester's own member; the body's e-mail does not choose the account, and
# the requester's certificate is linked.
add('renew-athlete-tax-code-of-another-member', 'renew', 'athlete', 403, NOT_ALLOWED,
    body=renewal({**member_body('second', tax_code=ASSOCIATES['stranger']['tax_code']), 'associate_id': None}, sport_association=HERE))
payment, subscription = '$second_payment', '$second_subscription'
add('renew-athlete-keeps-the-account', 'renew', 'athlete', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal({k: v for k, v in member_body('second', tax_code=ASSOCIATES['second']['tax_code'].lower()).items() if k != 'associate_id'},
                 sport_association=HERE, user={'email': TARGET_USER['email']},
                 medical_certificate={'medical_id': CERTIFICATES['athlete_cert']['id']}),
    effect=effect('athlete', new=[new_payment(payment, ASSOCIATES['second']['id']),
                                  *subscription_rows(subscription, ASSOCIATES['second']['id'], kind=1, payment=payment,
                                                     medical=CERTIFICATES['athlete_cert']['id'])],
                  changed={'associate': {'second': {'sex': 'F', 'user_id': OWNER}}},
                  audit=[entry('associate', 1, 'second', {'sex': ['A', 'F']}),
                         entry('associate', 1, 'second', {'user': [IDS['athlete'], OWNER]}),
                         entry('payment', 0, payment, {'amount': ['None', '25.0']}), entry('subscription', 0, subscription),
                         *notification_entries('athlete')],
                  celery=tasks(subscription),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Bruna Seconda.', 'association': OWN}]))

# --- subscription/renew with courses (add_course_to_subscription inside the renewal's transaction) --------
# data['courses'] is the renewal form's pre-filled list of {label, value}: each is the association's course
# (scoped_queryset(request, Course.objects).filter(course_id=c['value'], sport_association=...).first()) passed to
# add_course_to_subscription(course, fresh_sub, request.user.sport_association, data=None, is_athlete=False):
# a course subscription; with multi_payments the future events' payments and instalments (the last payment
# is linked); else the fee's payment when it is positive. A quote course gets no quote (data is empty) and
# falls back to its fee. A course listed twice is enrolled once. Any exception is the view's 400 body and
# rolls the renewal back; a stored event the serializer rejects is its 400.
def course_item(label):
    return {'label': COURSES[label]['title'], 'value': COURSES[label]['id']}


def course_subscription_repr(bind, course, subscription, *, payment=None, multi=False):
    """CourseSubscription.__str__ (str(self.__dict__)) of the instance at that point of the request."""
    fields = {'course_subscription_id': f"UUID('{bind}')", 'course_id': f"UUID('{COURSES[course]['id']}')", 'paid': 'False',
              'creation_date': '{created}', 'subscription_id': f"UUID('{subscription}')",
              'payment_id': f"UUID('{payment}')" if payment else 'None', 'multi_payments': repr(multi), 'one_fee_payment': 'False',
              'archived': 'False', 'type': '1', 'multiple_quote': 'None', 'billed_frequency': '1', 'billed_from': 'None',
              'billed_until': 'None', 'billed_from_day_of_month': '1', 'membership_active': 'True', 'membership_fee': '0.0',
              'auto_renewal': 'False', 'deleted': 'False'}
    return "{'_state': <django.db.models.base.ModelState object at 0x?>, " + ', '.join(f"'{k}': {v}" for k, v in fields.items()) + '}'


def enrolled(prefix, label, associate, subscription, user='$owner'):
    """Rows and entries of one add_course_to_subscription call of the renewal."""
    course = COURSES[label]
    row, payment, installment = f'${prefix}_{label}', f'${prefix}_{label}_payment', f'${prefix}_{label}_installment'
    multi = course['multi_payments']
    amount = course['events'][-1]['amount'] if multi else course['fee']
    paid = multi or float(course['fee']) > 0
    columns = {'course_id': course['id'], 'subscription_id': subscription, 'payment_id': payment if paid else None, 'multi_payments': multi,
               'multiple_quote': None, 'membership_fee': '0.00', 'type': 1}
    rows = [{'label': 'course_subscription', 'bind': row, 'match': {'course_id': course['id']}, 'columns': columns}]
    index = (OWN, 'course.sport_association')
    audit = [entry('coursesubscription', 0, row, {'membership_fee': ['None', '0.0'], 'payment': ['None', 'None'],
                                                  **({'multi_payments': ['None', 'False']} if multi else {})}, index=index,
                   repr=course_subscription_repr(row, label, subscription))]
    if multi:
        audit.append(entry('coursesubscription', 1, row, {'multi_payments': ['False', 'True'], 'membership_fee': ['0.00', '0.0']}, index=index,
                           repr=course_subscription_repr(row, label, subscription, multi=True)))
    if paid:
        moment = {'creation_date': '2099-01-10T00:00:00Z'} if multi else {}
        # The payment is matched first: the course subscription's columns name it.
        rows.insert(0, {'label': 'payment', 'bind': payment, 'match': {'associate_id': associate, 'amount': amount},
                     'columns': {'subject': 2, 'description': course['title'] + ', ', 'payment_category_id': '$category', 'user_id': user,
                                 'sport_association_id': OWN, 'custom_accounts_id': ACCOUNT, 'amount': amount,
                                 'course': {'label': course['title'], 'value': course['id']}, **moment}})
        audit.append(entry('payment', 0, payment))
        if multi:
            event = course['events'][-1]
            rows.append({'label': 'installment', 'bind': installment, 'match': {'course_subscription_id': row},
                         'columns': {'id': event['id'], 'amount': event['amount'], 'payment_date': '2099-01-10T00:00:00Z',
                                     'payment_id': payment}})
            audit.append(entry('coursesubscriptioninstallment', 0, installment, index=(OWN, 'course_subscription.course.sport_association')))
        audit.append(entry('coursesubscription', 1, row, {'payment': ['None', payment], 'membership_fee': ['0.00', '0.0']}, index=index,
                           repr=course_subscription_repr(row, label, subscription, payment=payment, multi=multi)))
    return rows, audit


coursed = member_body('coursed', sex='M')
rows = [new_payment('$coursed_payment', ASSOCIATES['coursed']['id']),
        *subscription_rows('$coursed_subscription', ASSOCIATES['coursed']['id'], kind=1, payment='$coursed_payment')]
audit = [entry('associate', 1, 'coursed', {'sex': ['A', 'M']}), entry('payment', 0, '$coursed_payment', {'amount': ['None', '25.0']}),
         entry('subscription', 0, '$coursed_subscription')]
for label in ('fee', 'plan', 'free', 'quotes'):
    extra_rows, extra_audit = enrolled('coursed', label, ASSOCIATES['coursed']['id'], '$coursed_subscription')
    rows += extra_rows
    audit += extra_audit
add('renew-with-courses', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal(coursed, courses=[course_item('fee'), course_item('plan'), course_item('free'), course_item('quotes'), course_item('fee')]),
    effect=effect('owner', new=rows, changed={'associate': {'coursed': {'sex': 'M'}}},
                  audit=[*audit, *notification_entries('owner')], celery=tasks('$coursed_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Carlo Corsista.', 'association': OWN}]))
# Failures in the course loop roll the renewal back.
add('renew-course-of-another-association', 'renew', 'owner', 400, error("'NoneType' object has no attribute 'course_type'"),
    body=renewal(basic, courses=[course_item('fee'), course_item('abroad')]))
add('renew-course-unknown', 'renew', 'owner', 400, error("'NoneType' object has no attribute 'course_type'"),
    body=renewal(basic, courses=[{'label': 'Nessuno', 'value': NEW_ID}]))
add('renew-course-removed', 'renew', 'owner', 400, error("'NoneType' object has no attribute 'course_type'"),
    body=renewal(basic, courses=[course_item('removed')]))
add('renew-course-value-null', 'renew', 'owner', 400, error("'NoneType' object has no attribute 'course_type'"),
    body=renewal(basic, courses=[{'label': 'Nessuno', 'value': None}]))
add('renew-course-malformed-id', 'renew', 'owner', 400, error("['“x” is not a valid UUID.']"),
    body=renewal(basic, courses=[{'label': 'Nessuno', 'value': 'x'}]))
add('renew-course-without-value', 'renew', 'owner', 400, error("'value'"), body=renewal(basic, courses=[{'label': 'Nessuno'}]))
add('renew-course-not-an-object', 'renew', 'owner', 400, error("list indices must be integers or slices, not str"),
    body=renewal(basic, courses=[[COURSES['fee']['id']]]))
add('renew-courses-text', 'renew', 'owner', 400, error("string indices must be integers, not 'str'"),
    body=renewal(basic, courses=COURSES['fee']['id']))
add('renew-courses-object', 'renew', 'owner', 400, error("string indices must be integers, not 'str'"),
    body=renewal(basic, courses={'value': COURSES['fee']['id']}))
add('renew-course-without-fee', 'renew', 'owner', 400, error("'>' not supported between instances of 'NoneType' and 'int'"),
    body=renewal(basic, courses=[course_item('no_fee')]))
add('renew-course-stored-event-invalid', 'renew', 'owner', 400, {'amount': ['A valid number is required.']},
    body=renewal(basic, courses=[course_item('fee'), course_item('broken_plan')]))

# --- subscription/renew in an association-scoped session ---------------------------------------------------
# The owner viewing as a collaborator: scoped_queryset keeps to the session's association; the course is enrolled.
OWNER_FULL = {'X-Impersonation-Id': '$session:owner_full', 'User-Id': IDS['full_collaborator']}
SCOPED_ATHLETE = {'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']}
HIDDEN = {'detail': 'Risorsa non disponibile per questo utente e associazione.'}
session_owner = member_body('session_owner', sex='M')
rows = [new_payment('$session_owner_payment', ASSOCIATES['session_owner']['id']),
        *subscription_rows('$session_owner_subscription', ASSOCIATES['session_owner']['id'], kind=1, payment='$session_owner_payment')]
extra_rows, extra_audit = enrolled('session_owner', 'fee', ASSOCIATES['session_owner']['id'], '$session_owner_subscription')
add('renew-owner-scoped-session-with-course', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'}, headers=OWNER_FULL,
    body=renewal(session_owner, courses=[course_item('fee')]),
    effect=effect('owner', new=rows + extra_rows, changed={'associate': {'session_owner': {'sex': 'M'}}},
                  audit=[entry('associate', 1, 'session_owner', {'sex': ['A', 'M']}),
                         entry('payment', 0, '$session_owner_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$session_owner_subscription'), *extra_audit, *notification_entries('owner')],
                  celery=tasks('$session_owner_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Sergio Sessione.', 'association': OWN}]))
add('renew-owner-scoped-session-foreign-course', 'renew', 'owner', 403, HIDDEN, headers=OWNER_FULL,
    body=renewal(basic, courses=[course_item('abroad')]))
# The owner viewing as an athlete: the association is looked up inside the session (another one is not available), the
# athlete's member is renewed for the association's owner, and the clean-up keeps to the payments the athlete sees
# (its own and its members'): the unpaid payment of a removed member row with the same name and tax code stays.
# The association opens a session only for an athlete whose memberships are all its own: the athlete's rows in the other
# association are handed to that association's owner first (setup).
add('renew-athlete-scoped-session-another-association', 'renew', 'owner', 403, HIDDEN, headers=SCOPED_ATHLETE,
    setup={'accounts': {'associates': {'coded_earlier': 'foreign_owner', 'blank_earlier': 'foreign_owner'},
                        'subscriptions': {'coded_earlier_2019': 'foreign_owner', 'blank_earlier_2019': 'foreign_owner'}}},
    body=renewal(member_body('session_member'), sport_association={'sport_association_id': FOREIGN}))
add('renew-athlete-scoped-session-another-member', 'renew', 'owner', 403, HIDDEN, headers=SCOPED_ATHLETE,
    body=renewal(member_body('stranger'), sport_association=HERE))
# The session's guard accepts the association's own username as `sport_association`; the view then looks the value up
# as an id inside the session, so a username spelled like another association's id answers the 500 page and writes nothing.
add('renew-athlete-scoped-session-username-is-another-association-id', 'renew', 'owner', 500, SERVER, headers=SCOPED_ATHLETE,
    setup={'accounts': {'usernames': {'owner': FOREIGN}}},
    body=renewal({k: v for k, v in member_body('coded_earlier').items() if k != 'associate_id'},
                 sport_association={'sport_association_id': FOREIGN}))
add('renew-athlete-scoped-session-username-is-another-association-id-own-member', 'renew', 'owner', 500, SERVER, headers=SCOPED_ATHLETE,
    setup={'accounts': {'usernames': {'owner': FOREIGN}}},
    body=renewal({k: v for k, v in member_body('session_member').items() if k != 'associate_id'},
                 sport_association={'sport_association_id': FOREIGN}))
payment, subscription = '$session_member_payment', '$session_member_subscription'
add('renew-athlete-scoped-session', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'}, headers=SCOPED_ATHLETE,
    body=renewal(member_body('session_member'), sport_association=HERE),
    effect=effect('owner', new=[new_payment(payment, ASSOCIATES['session_member']['id']),
                                *subscription_rows(subscription, ASSOCIATES['session_member']['id'], kind=1, payment=payment)],
                  changed={'associate': {'session_member': {'sex': 'F', 'user_id': OWNER}}},
                  deleted={'payment': ['session_stale']},
                  audit=[entry('associate', 1, 'session_member', {'sex': ['A', 'F']}),
                         entry('associate', 1, 'session_member', {'user': [IDS['athlete'], OWNER]}),
                         entry('payment', 0, payment, {'amount': ['None', '25.0']}), entry('subscription', 0, subscription),
                         entry('payment', 2, 'session_stale', index=(OWN, 'changes_json.sport_association'), deleted=True),
                         *notification_entries('owner')],
                  celery=tasks(subscription),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Silvia Sessione.', 'association': OWN}]))

# A username in new_member_info reaches create_user twice: the TypeError's text is the 400 body.
add('renew-new-member-username', 'renew', 'owner', 400,
    error("django.contrib.auth.models.UserManager.create_user() got multiple values for keyword argument 'username'"),
    body=renewal(basic, new_user_account={'new_member': True, 'new_member_info': {
        'first_name': 'Ugo', 'last_name': 'Utente', 'email': 'ugo.utente@example.test', 'username': 'ugo'}}))

# --- A typed certificate expiration date without an upload (renewal and draft approval) ---------------------
# create_subscription draws the placeholder image and creates its Document, the stored PNG and the association account's
# certificate, linked to the new subscription; the audit entries follow the associate's.
def typed_renewal(name, associate_id, first, last, sex, born, certificate, iso, *, tax_code=None):
    form = {'associate_id': associate_id, 'first_name': first, 'last_name': last, 'born_date': born, 'sex': sex, 'type': 1}
    if tax_code:
        form['tax_code'] = tax_code
    bind = '$' + name.replace('-', '_')
    pieces = placeholder_parts(first, last, iso)
    rows = [{'label': 'associate', 'bind': bind + '_associate', 'match': {'associate_id': associate_id},
             'columns': {'first_name': first, 'last_name': last, 'born_date': '-'.join(reversed(born.split('/'))), 'sex': sex,
                         'tax_code': tax_code, 'sport_association_id': OWN, 'user_id': '$owner'}},
            *pieces[0], new_payment(bind + '_payment', associate_id),
            *subscription_rows(bind + '_subscription', associate_id, kind=1, payment=bind + '_payment', medical='$placeholder_medical')]
    entries = [entry('associate', 0, bind + '_associate', {'user': ['None', 'None']}),
               entry('associate', 1, bind + '_associate', {'user': ['None', OWNER]}), *pieces[1],
               entry('payment', 0, bind + '_payment', {'amount': ['None', '25.0']}), entry('subscription', 0, bind + '_subscription'),
               *notification_entries('owner')]
    result = effect('owner', new=rows, audit=entries, celery=tasks(bind + '_subscription'),
                    notifications=[{'msg': f'Nuova Iscrizione aggiunta per {first} {last}.', 'association': OWN}])
    result['placeholder'] = pieces[2]
    add(name, 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'},
        body=renewal(form, medical_certificate=certificate), effect=result)


typed_renewal('renew-certificate-placeholder', 'a1000000-0000-4000-8000-000000948001', 'Pia', 'Segnaposto', 'F', '05/05/1991',
              {'medical_id': None, 'filename': '', 'certificate_expring_date': '2027-03-31'}, '2027-03-31')
typed_renewal('renew-certificate-placeholder-italian-date', 'a1000000-0000-4000-8000-000000948002', 'Niccolò', 'Però', 'M', '06/06/1992',
              {'certificate_expring_date': '5/4/2027'}, '2027-04-05')
# A value that is not text fails inside the view's try block: no certificate.
form = {'associate_id': 'a1000000-0000-4000-8000-000000948003', 'first_name': 'Nino', 'last_name': 'Numero', 'born_date': '07/07/1993',
        'sex': 'M', 'type': 1}
rows = [{'label': 'associate', 'bind': '$number_associate', 'match': {'associate_id': form['associate_id']},
         'columns': {'first_name': 'Nino', 'last_name': 'Numero', 'born_date': '1993-07-07', 'sex': 'M', 'tax_code': None,
                     'sport_association_id': OWN, 'user_id': '$owner'}},
        new_payment('$number_payment', form['associate_id']),
        *subscription_rows('$number_subscription', form['associate_id'], kind=1, payment='$number_payment')]
add('renew-certificate-number-date-skipped', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal(form, medical_certificate={'medical_id': None, 'certificate_expring_date': 20270331}),
    effect=effect('owner', new=rows,
                  audit=[entry('associate', 0, '$number_associate', {'user': ['None', 'None']}),
                         entry('associate', 1, '$number_associate', {'user': ['None', OWNER]}),
                         entry('payment', 0, '$number_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$number_subscription')],
                  celery=tasks('$number_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Nino Numero.', 'association': OWN}]))



# data['preregistration'] is True: the subscription is created on the next season's first day with that season's dates.
form = {'associate_id': 'a1000000-0000-4000-8000-000000948004', 'first_name': 'Pietro', 'last_name': 'Prenotato', 'born_date': '08/08/1994',
        'sex': 'M', 'type': 1}
NEXT = {'creation_date': '$next_season_created', 'start_date': '$next_season_start', 'end_date': '$next_season_end'}
rows = [{'label': 'associate', 'bind': '$early_associate', 'match': {'associate_id': form['associate_id']},
         'columns': {'first_name': 'Pietro', 'last_name': 'Prenotato', 'born_date': '1994-08-08', 'sex': 'M', 'tax_code': None,
                     'sport_association_id': OWN, 'user_id': '$owner'}},
        new_payment('$early_payment', form['associate_id']),
        *subscription_rows('$early_subscription', form['associate_id'], kind=1, payment='$early_payment', season=NEXT)]
add('renew-preregistration', 'renew', 'owner', 200, {'status': 'success', 'payment_id': '$new'},
    body=renewal(form, preregistration=True),
    effect=effect('owner', new=rows,
                  audit=[entry('associate', 0, '$early_associate', {'user': ['None', 'None']}),
                         entry('associate', 1, '$early_associate', {'user': ['None', OWNER]}),
                         entry('payment', 0, '$early_payment', {'amount': ['None', '25.0']}),
                         entry('subscription', 0, '$early_subscription',
                               {'start_date': ['None', '$next_season_start_text'], 'end_date': ['None', '$next_season_end_text']})],
                  celery=tasks('$early_subscription'),
                  notifications=[{'msg': 'Nuova Iscrizione aggiunta per Pietro Prenotato.', 'association': OWN}]))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({
    'matrix_version': 1, 'scope': 'subscription/renew and associates-draft approve authored sequence with row, audit, Celery and '
                                  'notification effects',
    'ids': IDS, 'associates': ASSOCIATES, 'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS, 'tags': TAGS,
    'family': FAMILY, 'courses': COURSES, 'certificates': CERTIFICATES, 'invoices': INVOICES, 'target_user': TARGET_USER, 'account': ACCOUNT, 'drafts': DRAFTS, 'statuses': STATUSES,
    'settings': BASE_SETTINGS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
