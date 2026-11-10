"""Author the subscription/<uid>/reject, archive and delete HTTP sequence (subscription-lifecycle.md).

All three are @api_view(['POST']) function views in subscriptions_views.py.
Subscription.objects is the group-aware SubscriptionManager (soft-deleted
rows are found); Subscription.save() fills missing start/end dates from the
owner's settings; every save and delete is audited.

reject: is_valid_uuid (400), SportAssociation.objects.get(user=...) (athletes
and owners without an association: 500), {'msg': 'Subscription not found.'}
(404), another association's row raises PermissionDenied('User not allowed.')
(DRF 403), an already rejected row raises PermissionDenied('User cannot change
the status of the subscription.'); otherwise status_flag=REJECTED,
acceptance_date=None and save().

archive (IsAssociationUser: athletes get DRF's generic 403) runs inside
try/except: PermissionDenied renders {'exception': detail} (403), any other
exception Response({'exception': e}, 500); DRF's encoder iterates a Django
ValidationError (a malformed uid: a JSON list), any other exception fails to
render (the HTML 500 page). The flag is toggled; archiving saves, in order,
every live course subscription's unpaid payment (archived), for multi-payment
rows every live group-aware installment's unpaid payment and the installment
(archived), the course subscription (archived), the subscription's unpaid
payment, the subscription, and every live group-aware unpaid unarchived
payment of the association and associate created between the start and end
dates (midnight UTC, inclusive). Payment.save() fills a null category.

delete (try/except as archive, no is_valid_uuid) is
SubscriptionService.delete_subscription: for each live course subscription its
unpaid payment is deleted (Payment's pre_delete deletes the payment's live
installments, whose own pre_delete deletes the payment again), for
multi-payment rows each live installment's unpaid payment is deleted and then
the installment (a second delete entry, its payment cleared in memory), paid
ones are archived, then the row's soft delete (its remaining live installments
through the collector, then deleted=True). The subscription's unpaid payment is
deleted, the subscription saved archived, the unpaid unarchived live payments
of its associate between the dates deleted, and the subscription deleted: the
collector deletes the subscription, then its installments and course
subscriptions (all, soft-deleted included) with their pre_delete signals,
entries in reversed key order. Run with ``python3 -I``.
"""
import copy
import datetime as dt
import decimal
import json
from pathlib import Path
import uuid

OUTPUT = Path(__file__).with_name('subscription_lifecycle_http.json')
IDS = json.loads(Path(__file__).with_name('message_mutations.json').read_text())['ids']
OWN, FOREIGN = IDS['own_association'], IDS['foreign_association']
OWNER, FOREIGN_OWNER = IDS['owner'], IDS['foreign_owner']
ids = iter(f'a1000000-0000-4000-8000-{610001 + n:012d}' for n in range(200))
SERVER = '<stable-server-error-page>'
CATEGORY = next(ids)

ASSOCIATES = {name: {'id': next(ids), 'association': OWN, 'first': name.title(), 'last': 'Fixture'}
              for name in ('mario', 'luisa', 'gino', 'anna', 'paolo', 'carla')}
ASSOCIATES['hans'] = {'id': next(ids), 'association': FOREIGN, 'first': 'Hans', 'last': 'Muller'}
COURSES = {'yoga': {'id': next(ids), 'association': OWN, 'title': 'Yoga'}}
TAGS = {'t1': {'id': next(ids), 'name': 'Agonisti'}, 't2': {'id': next(ids), 'name': 'Junior'}}


def subscription(associate='mario', **fields):
    row = {'id': next(ids), 'association': OWN, 'associate': associate, 'status': 4, 'acceptance': '2026-01-15', 'start': '2026-01-01',
           'end': '2026-12-31', 'created': '2026-01-10T09:00:00Z', 'group': None, 'archived': False, 'deleted': False, 'payment': None,
           'tags': [], 'signature_key': None}
    row.update(fields)
    return row


SUBSCRIPTIONS = {
    'r_pending': subscription(status=2, acceptance=None),
    'r_accepted': subscription(acceptance='2026-02-01'),
    'r_undated': subscription(status=1, acceptance=None, start=None, end=None, created='2026-03-10T09:00:00Z'),
    'r_group': subscription(status=2, acceptance=None, group='own_group'),
    'r_deleted': subscription(status=2, acceptance=None, deleted=True),
    'r_admin': subscription(status=2, acceptance=None),
    'abroad': subscription('hans', association=FOREIGN),
    'a_plain': subscription('anna', start='2025-01-01', end='2025-12-31'),
    'a_stored': subscription('anna', archived=True, start='2024-01-01', end='2024-12-31'),
    'a_full': subscription('luisa', payment='sp'),
    'a_group': subscription('gino', group='own_group'),
    'd_tags': subscription('carla', tags=['t1', 't2']),
    'd_pay': subscription('mario', payment='dsp', start='2023-01-01', end='2023-12-31'),
    'd_cs': subscription('paolo'),
    'd_mem': subscription('anna'),
}


def payment(paid=False, *, associate=None, created='2026-04-01T10:00:00Z', archived=False, deleted=False, group=None, category=True,
            amount='10.00', subject=2):
    return {'id': next(ids), 'association': OWN, 'subject': subject, 'amount': amount, 'paid': paid, 'archived': archived, 'deleted': deleted,
            'associate': associate, 'group': group, 'category': category, 'created': created, 'description': 'Quota fixture', 'invoice': None}


PAYMENTS = {
    # archive a_full
    'sp': payment(subject=1, category=False),
    'cp1': payment(), 'ip1': payment(), 'ip2': payment(True), 'ip3': payment(), 'cp2': payment(True), 'cp3': payment(),
    'rp_in': payment(associate='luisa', created='2026-05-01T10:00:00Z', subject=0),
    'rp_start': payment(associate='luisa', created='2026-01-01T00:00:00Z', subject=0),
    'rp_paid': payment(True, associate='luisa', subject=0),
    'rp_out': payment(associate='luisa', created='2027-02-01T10:00:00Z', subject=0),
    'rp_end': payment(associate='luisa', created='2026-12-31T08:00:00Z', subject=0),
    'rp_archived': payment(associate='luisa', archived=True, subject=0),
    'rp_deleted': payment(associate='luisa', deleted=True, subject=0),
    # archive a_group
    'rp_g_own': payment(associate='gino', group='own_group', subject=0),
    'rp_g_none': payment(associate='gino', subject=0),
    # delete d_pay
    'dsp': payment(subject=1, created='2023-02-01T10:00:00Z'),
    'drp_in': payment(associate='mario', created='2023-03-01T10:00:00Z', subject=0),
    'drp_paid': payment(True, associate='mario', created='2023-03-02T10:00:00Z', subject=0),
    'drp_archived': payment(associate='mario', created='2023-03-03T10:00:00Z', archived=True, subject=0),
    # delete d_cs
    'dcp1': payment(), 'dip1': payment(), 'dip2': payment(True), 'dcp2': payment(True), 'dop1': payment(),
    # delete d_mem: membership payments of a payment-less membership row
    'dmp_u': payment(amount='25.00'), 'dmp_p': payment(True, amount='25.00'),
}


def course_subscription(sub, **fields):
    row = {'id': next(ids), 'course': 'yoga', 'subscription': sub, 'paid': False, 'created': '2026-02-01T08:00:00Z', 'payment': None,
           'multi_payments': False, 'one_fee_payment': False, 'archived': False, 'type': 1, 'multiple_quote': None, 'billed_frequency': 1,
           'billed_from': None, 'billed_until': None, 'billed_from_day_of_month': 1, 'membership_active': True, 'membership_fee': '0.00',
           'auto_renewal': False, 'deleted': False}
    row.update(fields)
    return row


CS = {
    'ca1': course_subscription('a_full', payment='cp1', multi_payments=True),
    'ca2': course_subscription('a_full', payment='cp2', paid=True),
    'ca3': course_subscription('a_full', payment='cp3', deleted=True),
    'cg': course_subscription('a_group', multi_payments=True),
    'dc1': course_subscription('d_cs', payment='dcp1', multi_payments=True),
    'dc2': course_subscription('d_cs', payment='dcp2', paid=True),
    'dc_old': course_subscription('d_cs', deleted=True),
    'dm_member': course_subscription('d_mem', type=3, membership_payments=['dmp_u', 'dmp_p']),
    'dm_old': course_subscription('d_mem', deleted=True),
}


def installment(cs, number, pay=None, *, group=None, deleted=False):
    return {'id': next(ids), 'cs': cs, 'number': number, 'amount': '5.00', 'payment': pay, 'group': group, 'deleted': deleted,
            'payment_date': '2026-03-01T00:00:00Z', 'archived': False}


INSTALLMENTS = {
    'ia1': installment('ca1', 0, 'ip1'), 'ia2': installment('ca1', 1, 'ip2'), 'ia3': installment('ca1', 2, 'ip3', deleted=True),
    'ig_own': installment('cg', 0, group='own_group'), 'ig_none': installment('cg', 1),
    'di1': installment('dc1', 0, 'dip1'), 'di2': installment('dc1', 1, 'dip2'), 'do1': installment('dc_old', 0, 'dop1'),
    'dmi_old': installment('dm_old', 0),
}
UNKNOWN = 'a1000000-0000-4000-8000-000000619999'
STATE = {'cs': copy.deepcopy(CS)}

# Rows of other models that reference the subscription (the UI creates all of them): the
# Collector deletes them with the subscription. 'parent' rows are what they need and stay.
SUBSCRIPTIONS['d_rel'] = subscription('carla')
REL = SUBSCRIPTIONS['d_rel']['id']
LOCATION = 'lifecycle-fixture'
SIGNATURE_KEY = lambda label: f'subscriptions/{label}/signature_1767225600.0.png'
# Two rows share one signature object; the other row has its own (and one has no object in the bucket).
SUBSCRIPTIONS['d_sig_a'] = subscription('carla', signature_key=SIGNATURE_KEY('shared'))
SUBSCRIPTIONS['d_sig_b'] = subscription('carla', signature_key=SIGNATURE_KEY('shared'))
SUBSCRIPTIONS['d_sig_own'] = subscription('carla', signature_key=SIGNATURE_KEY('own'))
SUBSCRIPTIONS['d_sig_missing'] = subscription('carla', signature_key=SIGNATURE_KEY('missing'))
OBJECTS = {SIGNATURE_KEY('shared'): 'iVBORw0KGgpzaGFyZWQ=', SIGNATURE_KEY('own'): 'iVBORw0KGgpvd24=',
           SIGNATURE_KEY('other'): 'iVBORw0KGgpvdGhlcg=='}


def related(model, key, parent=False, **fields):
    row_id = next(ids)
    return {'model': model, 'id': row_id, 'parent': parent, 'fields': {key: row_id, **fields}}


RELATED = {}
RELATED['rel_doc'] = related('docmanager.Document', 'document_id', True, creation_date='2026-01-05T08:00:00Z', filename='allegato.pdf',
                             token=next(ids))
RELATED['rel_product'] = related('application.ProductsAndServices', 'product_id', True, sport_association_id=OWN, product_sku='SKU-1',
                                 product_name='Maglia', creation_date='2026-01-05T08:00:00Z')
RELATED['rel_camp'] = related('application.CampsAndRetreats', 'camps_and_retreats_id', True, title='Campo estivo', sport_association_id=OWN)
RELATED['rel_camp_period'] = related('application.CampsAndRetreatsPeriod', 'camps_and_retreats_period_id', True,
                                     camps_and_retreats_id=RELATED['rel_camp']['id'], start_date='2026-07-01T08:00:00Z',
                                     end_date='2026-07-15T18:00:00Z', title='Turno 1', fee='100.00', created_at='2026-01-05T08:00:00Z')
RELATED['rel_appointment'] = related('application.MedicalAppointments', 'medical_appointments_id', subscription_id=REL, region='agonistica',
                                     date='2026-03-01', notes='Visita', sport='Calcio', meta={'esito': 'idoneo'})
RELATED['rel_membership'] = related('application.SubscriptionMembership', 'subscription_membership_id', subscription_id=REL,
                                    sport_association_id=OWN, creation_date='2026-01-12T08:00:00Z', start_date='2026-01-01',
                                    end_date='2026-12-31', membership_type='CSEN', membership_number='12', description='Tessera',
                                    price='15.00', associate_id=ASSOCIATES['carla']['id'])
RELATED['rel_sold'] = related('application.SoldProducts', 'sold_product_id', subscription_id=REL, product_id=RELATED['rel_product']['id'],
                              quantity=2, creation_date='2026-02-01T08:00:00Z', selling_date='2026-02-01T08:00:00Z')
RELATED['rel_token'] = related('application.SubscriptionToken', 'subscription_token_id', subscription_id=REL, token=next(ids),
                               creation_date='2026-02-01T08:00:00Z', expiration_date='2026-03-01T08:00:00Z')
RELATED['rel_file'] = related('application.SubscriptionFile', 'subscription_file_id', subscription_id=REL,
                              document_id=RELATED['rel_doc']['id'], creation_date='2026-02-01T08:00:00Z')
RELATED['rel_transfer'] = related('application.SubscriptionTransfer', 'subscription_transfer_id', subscription_id=REL,
                                  expires_at='2026-02-01T08:00:00Z', requester_id=OWNER, recipient_id=None, token=next(ids), status=2)
RELATED['rel_camp_sub'] = related('application.CampsAndRetreatsSubscription', 'camps_and_retreats_subscription_id',
                                  camps_and_retreats_id=RELATED['rel_camp']['id'], status_flag=2, subscription_id=REL,
                                  created_at='2026-02-01T08:00:00Z')
RELATED['rel_camp_sub_period'] = related('application.CampsAndRetreatsSubscriptionPeriod', 'camps_and_retreats_subscription_period_id',
                                         camps_and_retreats_period_id=RELATED['rel_camp_period']['id'],
                                         camps_and_retreats_subscription_id=RELATED['rel_camp_sub']['id'], payment_id=None)
# Invoices of unpaid payments (payment cancel and invoice generation leave them): archive and delete call
# payment.invoice.delete() (its Collector: rows first, then the invoice; Payment.invoice is SET_NULL).
ASSOCIATES['elena'] = {'id': next(ids), 'association': OWN, 'first': 'Elena', 'last': 'Fixture'}


def invoice(label, rows=0):
    RELATED[label] = related('application.Invoice', 'invoice_id', sport_association_id=OWN, creation_date='2026-03-01T08:00:00Z',
                             membership_fee='0.00', activity_fee='10.00', description='Fattura fixture', number=7, archived=False,
                             cancelled=False, meta=None, deleted=False)
    for index in range(rows):
        RELATED[f'{label}_row{index}'] = related('application.InvoiceRows', 'invoice_row_id', invoice_id=RELATED[label]['id'],
                                                 description='Quota', quantity=1, unit_price='10.00', vat='0.00')
    return label


def invoiced(label, invoice_label, **fields):
    PAYMENTS[label] = payment(**fields)
    PAYMENTS[label]['invoice'] = invoice_label
    return label


# archive: a soft-deleted unpaid subscription payment (the accessor uses the base manager; auditlog's
# Payment.objects lookup misses it, so the update entry diffs against None like a create).
PAYMENTS['ahp'] = payment(deleted=True)
SUBSCRIPTIONS['a_hidden'] = subscription('elena', payment='ahp')
# archive: invoiced unpaid course-subscription payment (ValueError after the invoice delete), installment payment
# (saved first, then its invoice deleted) and subscription payment (ValueError after the invoice delete).
invoice('inv_acs', rows=1)
SUBSCRIPTIONS['a_inv_cs'] = subscription('elena', payment=None)
CS['acs_inv'] = course_subscription('a_inv_cs', payment=invoiced('acp_inv', 'inv_acs'))
invoice('inv_ai')
SUBSCRIPTIONS['a_inv_inst'] = subscription('elena')
CS['acs_inst'] = course_subscription('a_inv_inst', multi_payments=True)
INSTALLMENTS['aii'] = installment('acs_inst', 0, invoiced('aiip', 'inv_ai'))
invoice('inv_as')
SUBSCRIPTIONS['a_inv_sub'] = subscription('elena', payment=invoiced('asp_inv', 'inv_as'))
CS['acs_plain'] = course_subscription('a_inv_sub')
# delete: invoiced unpaid subscription payment; course-subscription and installment payments.
invoice('inv_d', rows=1)
SUBSCRIPTIONS['d_inv'] = subscription('elena', payment=invoiced('dip_inv', 'inv_d'))
invoice('inv_dc')
invoice('inv_dci')
SUBSCRIPTIONS['d_inv_cs'] = subscription('elena')
CS['dci'] = course_subscription('d_inv_cs', payment=invoiced('dcip', 'inv_dc'), multi_payments=True)
INSTALLMENTS['dcii'] = installment('dci', 0, invoiced('dciip', 'inv_dci'))
STATE['cs'].update(copy.deepcopy({key: CS[key] for key in ('acs_inv', 'acs_inst', 'acs_plain', 'dci')}))

RELATED['rel_carnet'] = related('application.CarnetSubscription', 'carnet_subscription_id', user_id_id=OWNER, subscription_id=REL,
                                carnet_id_id=None, disabled=False, meta=None)

# Rare stored data. Empty dates: the owner's duration setting selects no rule (a case's `setup`), so save() leaves
# them empty. A payment without an association. Payments whose save() raises (an account transfer without a
# description). One unpaid payment on two links of the subscription being deleted.
for name in ('nora', 'ugo', 'rita', 'dino', 'lea', 'tito'):
    ASSOCIATES[name] = {'id': next(ids), 'association': OWN, 'first': name.title(), 'last': 'Fixture'}
NO_RULE = {'owner': {'subscription_duration': 0}}
SUBSCRIPTIONS['a_nodate'] = subscription('nora', start=None, end=None)
PAYMENTS['and_p'] = payment(associate='nora', subject=0)
PAYMENTS['dnd_sp'] = payment(subject=1)
PAYMENTS['dnd_r'] = payment(associate='ugo', subject=0)
SUBSCRIPTIONS['d_nodate'] = subscription('ugo', start=None, end=None, payment='dnd_sp')
PAYMENTS['orph'] = {**payment(subject=1), 'association': None}
SUBSCRIPTIONS['a_orphan'] = subscription('rita', payment='orph', start='2020-01-01', end='2020-12-31')
PAYMENTS['bad_cp'] = payment()
PAYMENTS['bad_sp'] = {**payment(subject=3), 'description': None}
SUBSCRIPTIONS['a_badsave'] = subscription('dino', payment='bad_sp', start='2020-01-01', end='2020-12-31')
CS['bad_cs'] = course_subscription('a_badsave', payment='bad_cp')
PAYMENTS['rng_ok'] = payment(associate='lea', created='2021-03-01T10:00:00Z', subject=0)
PAYMENTS['rng_bad'] = {**payment(associate='lea', created='2021-03-02T10:00:00Z', subject=3), 'description': None}
PAYMENTS['rng_after'] = payment(associate='lea', created='2021-03-03T10:00:00Z', subject=0)
SUBSCRIPTIONS['a_badrange'] = subscription('lea', start='2021-01-01', end='2021-12-31')
PAYMENTS['sh_p'] = payment()
SUBSCRIPTIONS['d_shared'] = subscription('tito', start='2019-01-01', end='2019-12-31')
CS['sh1'] = course_subscription('d_shared', payment='sh_p')
CS['sh2'] = course_subscription('d_shared', payment='sh_p')
PAYMENTS['shs_p'] = payment()
SUBSCRIPTIONS['d_shared_sub'] = subscription('tito', payment='shs_p', start='2018-01-01', end='2018-12-31')
CS['shs'] = course_subscription('d_shared_sub', payment='shs_p')
PAYMENTS['shi_p'] = payment()
SUBSCRIPTIONS['d_shared_inst'] = subscription('tito', start='2017-01-01', end='2017-12-31')
CS['shi'] = course_subscription('d_shared_inst', payment='shi_p', multi_payments=True)
INSTALLMENTS['shi_i'] = installment('shi', 0, 'shi_p')
STATE['cs'].update(copy.deepcopy({key: CS[key] for key in ('bad_cs', 'sh1', 'sh2', 'shs', 'shi')}))

cases = []


def add(name, actor, path, status, response, *, method='POST', headers=None, effect=None, setup=None):
    cases.append({'name': f'subscription-lifecycle-{name}', 'actor': actor, 'method': method, 'path': path, 'headers': headers or {},
                  'status': status, 'response': response, 'effect': effect, 'setup': setup})


def route(label, verb):
    return f'/subscription/{SUBSCRIPTIONS[label]["id"] if label in SUBSCRIPTIONS else label}/{verb}'


def effect(actor, audit, *, related_deleted=(), objects_deleted=(), **tables):
    """tables: subscriptions/payments/cs/installments -> {'changed': {label: {column: value}}, 'deleted': [labels]};
    related_deleted: RELATED labels the request deletes; objects_deleted: storage names it deletes."""
    result = {'actor': actor, 'audit': audit, 'related_deleted': list(related_deleted), 'objects_deleted': list(objects_deleted)}
    for name in ('subscriptions', 'payments', 'cs', 'installments'):
        value = tables.get(name, {})
        result[name] = {'changed': value.get('changed', {}), 'deleted': value.get('deleted', [])}
    return result


def jsonb(value):
    if isinstance(value, dict):
        return {key: jsonb(value[key]) for key in sorted(value, key=lambda key: (len(key.encode()), key.encode()))}
    return value


def cs_repr(label):
    """CourseSubscription.__str__ is str(self.__dict__) of the in-memory instance
    (database types: datetime.timezone.utc, Decimal, UUID); the ModelState
    address is not reproducible ('0x?')."""
    row = STATE['cs'][label]
    key = lambda value: uuid.UUID(value) if value else None
    moment = lambda value: dt.datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None
    values = {'course_subscription_id': key(row['id']), 'course_id': key(COURSES[row['course']]['id']), 'paid': row['paid'],
              'creation_date': moment(row['created']), 'subscription_id': key(SUBSCRIPTIONS[row['subscription']]['id']),
              'payment_id': key(PAYMENTS[row['payment']]['id'] if row['payment'] else None), 'multi_payments': row['multi_payments'],
              'one_fee_payment': row['one_fee_payment'], 'archived': row['archived'], 'type': row['type'], 'multiple_quote': jsonb(row['multiple_quote']),
              'billed_frequency': row['billed_frequency'], 'billed_from': moment(row['billed_from']), 'billed_until': moment(row['billed_until']),
              'billed_from_day_of_month': row['billed_from_day_of_month'], 'membership_active': row['membership_active'],
              'membership_fee': decimal.Decimal(row['membership_fee']), 'auto_renewal': row['auto_renewal'], 'deleted': row['deleted']}
    return "{'_state': <django.db.models.base.ModelState object at 0x?>, " + repr(values)[1:]


DIRECT = [OWN, 'direct']
CHANGES_JSON = [OWN, 'changes_json.sport_association']
ACTOR_INDEX = {'owner': [OWN, 'actor.sport_association'], 'admin': [IDS['admin_association'], 'actor.sport_association'],
               'full_collaborator': None}
FLIP = {'archived': ['False', 'True']}


def updated(model, label, changes, index):
    return {'model': model, 'label': label, 'action': 1, 'changes': changes, 'overrides': {}, 'index': index,
            'repr': cs_repr(label) if model == 'coursesubscription' else None}


def removed(model, label, index, overrides=None):
    """A delete entry: the row as captured before the request, with the in-memory differences."""
    return {'model': model, 'label': label, 'action': 2, 'changes': None, 'overrides': overrides or {}, 'index': index,
            'repr': cs_repr(label) if model == 'coursesubscription' else None}


# ---------------------------------------------------------------- reject
REJECTED = {'data': {'msg': 'subscription rejected.'}}
NOT_FOUND = {'msg': 'Subscription not found.'}


def rejected(label, changes, actor='owner', dates=None):
    columns = {'status_flag': 3, 'acceptance_date': None, **(dates or {})}
    return effect(actor, [updated('subscription', label, changes, DIRECT)], subscriptions={'changed': {label: columns}})


add('reject-pending', 'owner', route('r_pending', 'reject'), 200, REJECTED, effect=rejected('r_pending', {'status_flag': ['2', '3']}))
add('reject-accepted-clears-acceptance', 'owner', route('r_accepted', 'reject'), 200, REJECTED,
    effect=rejected('r_accepted', {'status_flag': ['4', '3'], 'acceptance_date': ['2026-02-01', 'None']}))
add('reject-again-denied', 'owner', route('r_accepted', 'reject'), 403, {'detail': 'User cannot change the status of the subscription.'})
# Subscription.save() fills the missing dates from the owner's season (1 January, LIKE_SEASON_YEAR).
add('reject-fills-dates', 'owner', route('r_undated', 'reject'), 200, REJECTED,
    effect=rejected('r_undated', {'status_flag': ['1', '3'], 'start_date': ['None', '2026-01-01'], 'end_date': ['None', '2026-12-31']},
                    dates={'start_date': '2026-01-01', 'end_date': '2026-12-31'}))
add('reject-other-group-not-found', 'owner', route('r_group', 'reject'), 404, NOT_FOUND, headers={'X-Group-Id': IDS['foreign_group']})
add('reject-group-full-collaborator', 'full_collaborator', route('r_group', 'reject'), 200, REJECTED, headers={'X-Group-Id': IDS['own_group']},
    effect=rejected('r_group', {'status_flag': ['2', '3']}, 'full_collaborator'))
add('reject-soft-deleted-row', 'owner', route('r_deleted', 'reject'), 200, REJECTED, effect=rejected('r_deleted', {'status_flag': ['2', '3']}))
add('reject-admin-impersonating-owner', 'admin', route('r_admin', 'reject'), 200, REJECTED,
    headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER}, effect=rejected('r_admin', {'status_flag': ['2', '3']}, 'admin'))
add('reject-foreign-denied', 'owner', route('abroad', 'reject'), 403, {'detail': 'User not allowed.'})
add('reject-missing', 'owner', route(UNKNOWN, 'reject'), 404, NOT_FOUND)
add('reject-malformed', 'owner', route('abc', 'reject'), 400, ['badly formed hexadecimal UUID string'])
add('reject-athlete-server-error', 'athlete', route('r_pending', 'reject'), 500, SERVER)
add('reject-owner-without-association-server-error', 'owner_without_association', route('r_pending', 'reject'), 500, SERVER)
add('reject-owner-scoped-athlete-denied', 'owner', route('r_pending', 'reject'), 403,
    {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('reject-restricted-missing-permission', 'restricted_collaborator', route('r_pending', 'reject'), 403,
    {'detail': 'Missing permission: association.members.update'})
add('reject-admin-without-session', 'admin', route('r_pending', 'reject'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('reject-anonymous', 'anonymous', route('r_pending', 'reject'), 401, {'detail': 'Authentication credentials were not provided.'})
add('reject-wrong-method', 'owner', route('r_pending', 'reject'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('reject-options', 'owner', route('r_pending', 'reject'), 200, {'name': 'Subscription Reject', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# ---------------------------------------------------------------- archive
ARCHIVED = {'data': {'msg': 'subscription archival completed.'}}
add('archive-athlete-denied', 'athlete', route('a_plain', 'archive'), 403, {'detail': 'You do not have permission to perform this action.'})
add('archive-owner-without-association-server-error', 'owner_without_association', route('a_plain', 'archive'), 500, SERVER)
# The ValidationError of the lookup is caught and rendered through DRF's encoder (iterated).
add('archive-malformed', 'owner', route('abc', 'archive'), 500, {'exception': ['“abc” is not a valid UUID.']})
add('archive-missing', 'owner', route(UNKNOWN, 'archive'), 404, {'exception': 'Subscription not found'})
add('archive-foreign-denied', 'owner', route('abroad', 'archive'), 403, {'exception': 'User not allowed.'})
add('archive-other-group-not-found', 'owner', route('a_group', 'archive'), 404, {'exception': 'Subscription not found'},
    headers={'X-Group-Id': IDS['foreign_group']})
add('archive-plain', 'owner', route('a_plain', 'archive'), 200, ARCHIVED,
    effect=effect('owner', [updated('subscription', 'a_plain', FLIP, DIRECT)], subscriptions={'changed': {'a_plain': {'archived': True}}}))
add('archive-admin-unarchives', 'admin', route('a_plain', 'archive'), 200, ARCHIVED, headers={'X-Impersonation-Id': '$session:admin_owner', 'User-Id': OWNER},
    effect=effect('admin', [updated('subscription', 'a_plain', {'archived': ['True', 'False']}, DIRECT)],
                  subscriptions={'changed': {'a_plain': {'archived': False}}}))
add('archive-unarchives-stored', 'owner', route('a_stored', 'archive'), 200, ARCHIVED,
    effect=effect('owner', [updated('subscription', 'a_stored', {'archived': ['True', 'False']}, DIRECT)],
                  subscriptions={'changed': {'a_stored': {'archived': False}}}))

for label in ('ca1', 'ca2'):
    STATE['cs'][label]['archived'] = True
INSTALLMENT_INDEX = [OWN, 'course_subscription.course.sport_association']
COURSE_INDEX = [OWN, 'course.sport_association']
add('archive-cascade', 'owner', route('a_full', 'archive'), 200, ARCHIVED, effect=effect('owner', [
    updated('payment', 'cp1', FLIP, DIRECT),
    updated('payment', 'ip1', FLIP, DIRECT), updated('coursesubscriptioninstallment', 'ia1', FLIP, INSTALLMENT_INDEX),
    updated('coursesubscriptioninstallment', 'ia2', FLIP, INSTALLMENT_INDEX),
    updated('coursesubscription', 'ca1', FLIP, COURSE_INDEX),
    updated('coursesubscription', 'ca2', FLIP, COURSE_INDEX),
    updated('payment', 'sp', {**FLIP, 'payment_category': ['None', '$default_category']}, DIRECT),
    updated('subscription', 'a_full', FLIP, DIRECT),
    updated('payment', 'rp_in', FLIP, DIRECT),
    updated('payment', 'rp_start', FLIP, DIRECT),
], subscriptions={'changed': {'a_full': {'archived': True}}},
    payments={'changed': {'cp1': {'archived': True}, 'ip1': {'archived': True}, 'sp': {'archived': True, 'payment_category_id': '$default_category'},
                          'rp_in': {'archived': True}, 'rp_start': {'archived': True}}},
    cs={'changed': {'ca1': {'archived': True}, 'ca2': {'archived': True}}},
    installments={'changed': {'ia1': {'archived': True}, 'ia2': {'archived': True}}}))
STATE['cs']['cg']['archived'] = True
add('archive-group-scoped', 'full_collaborator', route('a_group', 'archive'), 200, ARCHIVED, headers={'X-Group-Id': IDS['own_group']},
    effect=effect('full_collaborator', [
        updated('coursesubscriptioninstallment', 'ig_own', FLIP, INSTALLMENT_INDEX),
        updated('coursesubscription', 'cg', FLIP, COURSE_INDEX),
        updated('subscription', 'a_group', FLIP, DIRECT),
        updated('payment', 'rp_g_own', FLIP, DIRECT),
    ], subscriptions={'changed': {'a_group': {'archived': True}}}, payments={'changed': {'rp_g_own': {'archived': True}}},
        cs={'changed': {'cg': {'archived': True}}}, installments={'changed': {'ig_own': {'archived': True}}}))
add('archive-owner-scoped-athlete-denied', 'owner', route('a_plain', 'archive'), 403,
    {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('archive-restricted-missing-permission', 'restricted_collaborator', route('a_plain', 'archive'), 403,
    {'detail': 'Missing permission: association.members.archive.update'})
add('archive-admin-without-session', 'admin', route('a_plain', 'archive'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('archive-anonymous', 'anonymous', route('a_plain', 'archive'), 401, {'detail': 'Authentication credentials were not provided.'})
add('archive-wrong-method', 'owner', route('a_plain', 'archive'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('archive-options', 'owner', route('a_plain', 'archive'), 200, {'name': 'Subscription Archive', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# ---------------------------------------------------------------- delete
DELETED = {'data': {'msg': 'subscription delete.'}}
add('delete-athlete-server-error', 'athlete', route('d_tags', 'delete'), 500, SERVER)
add('delete-owner-without-association-server-error', 'owner_without_association', route('d_tags', 'delete'), 500, SERVER)
add('delete-malformed', 'owner', route('abc', 'delete'), 500, {'exception': ['“abc” is not a valid UUID.']})
add('delete-missing', 'owner', route(UNKNOWN, 'delete'), 404, NOT_FOUND)
add('delete-foreign-denied', 'owner', route('abroad', 'delete'), 403, {'exception': 'User not allowed.'})
add('delete-other-group-not-found', 'owner', route('d_tags', 'delete'), 404, NOT_FOUND, headers={'X-Group-Id': IDS['foreign_group']})
GONE = {'archived': ['True', 'None']}
add('delete-with-tags', 'owner', route('d_tags', 'delete'), 200, DELETED, effect=effect('owner', [
    updated('subscription', 'd_tags', FLIP, DIRECT),
    removed('subscription', 'd_tags', CHANGES_JSON, GONE),
], subscriptions={'deleted': ['d_tags']}))
add('delete-payments', 'owner', route('d_pay', 'delete'), 200, DELETED, effect=effect('owner', [
    removed('payment', 'dsp', CHANGES_JSON),
    updated('subscription', 'd_pay', FLIP, DIRECT),
    removed('payment', 'drp_in', CHANGES_JSON),
    removed('subscription', 'd_pay', CHANGES_JSON, {**GONE, 'payment': ['None', 'None']}),
], subscriptions={'deleted': ['d_pay']}, payments={'deleted': ['dsp', 'drp_in']}))

ACTOR = ACTOR_INDEX['owner']
STATE['cs']['dc1'].update(payment=None, deleted=True)
STATE['cs']['dc2'].update(deleted=True)
dc1_saved, dc2_saved = cs_repr('dc1'), cs_repr('dc2')
NULL_PAYMENT = {'payment': ['None', 'None']}
cascade = [
    removed('payment', 'dcp1', CHANGES_JSON),
    # dip1.delete(): Payment's pre_delete deletes di1, whose pre_delete deletes dip1 first.
    removed('payment', 'dip1', CHANGES_JSON), removed('coursesubscriptioninstallment', 'di1', ACTOR), removed('payment', 'dip1', CHANGES_JSON),
    # installment.payment = None; installment.delete() on the row already gone.
    removed('coursesubscriptioninstallment', 'di1', ACTOR, NULL_PAYMENT),
    updated('coursesubscriptioninstallment', 'di2', FLIP, INSTALLMENT_INDEX),
    # dc1.delete(): the soft delete's installment collector, then deleted=True.
    removed('coursesubscriptioninstallment', 'di2', ACTOR, GONE),
    dict(updated('coursesubscription', 'dc1', {'deleted': ['False', 'True']}, COURSE_INDEX), repr=dc1_saved),
    dict(updated('coursesubscription', 'dc2', {'deleted': ['False', 'True']}, COURSE_INDEX), repr=dc2_saved),
    updated('subscription', 'd_cs', FLIP, DIRECT),
    # subscription.delete(): pre_delete of the collected installment do1 deletes dop1 (with do1 nested).
    removed('payment', 'dop1', CHANGES_JSON), removed('coursesubscriptioninstallment', 'do1', ACTOR), removed('payment', 'dop1', CHANGES_JSON),
    removed('subscription', 'd_cs', CHANGES_JSON, GONE),
    removed('coursesubscriptioninstallment', 'do1', ACTOR),
    removed('coursesubscription', 'dc_old', ACTOR),
    dict(removed('coursesubscription', 'dc2', ACTOR, {'deleted': ['True', 'None']}), repr=dc2_saved),
    dict(removed('coursesubscription', 'dc1', ACTOR, {'deleted': ['True', 'None'], **NULL_PAYMENT}), repr=dc1_saved),
]
add('delete-course-subscriptions', 'owner', route('d_cs', 'delete'), 200, DELETED, effect=effect('owner', cascade,
    subscriptions={'deleted': ['d_cs']}, payments={'deleted': ['dcp1', 'dip1', 'dop1']},
    cs={'deleted': ['dc1', 'dc2', 'dc_old']}, installments={'deleted': ['di1', 'di2', 'do1']}))

# subscription.delete() runs delete_related_course_payment for every collected row: the
# payment-less membership row (soft-deleted by the service just before) deletes its unpaid
# live membership payments, the soft-deleted row its remaining live installment.
STATE['cs']['dm_member'].update(deleted=True)
dm_saved = cs_repr('dm_member')
add('delete-membership-and-deleted-rows', 'owner', route('d_mem', 'delete'), 200, DELETED, effect=effect('owner', [
    dict(updated('coursesubscription', 'dm_member', {'deleted': ['False', 'True']}, COURSE_INDEX), repr=dm_saved),
    updated('subscription', 'd_mem', FLIP, DIRECT),
    removed('payment', 'dmp_u', CHANGES_JSON),
    removed('coursesubscriptioninstallment', 'dmi_old', ACTOR),
    removed('subscription', 'd_mem', CHANGES_JSON, GONE),
    removed('coursesubscriptioninstallment', 'dmi_old', ACTOR),
    removed('coursesubscription', 'dm_old', ACTOR),
    dict(removed('coursesubscription', 'dm_member', ACTOR, {'deleted': ['True', 'None']}), repr=dm_saved),
], subscriptions={'deleted': ['d_mem']}, payments={'deleted': ['dmp_u']}, cs={'deleted': ['dm_member', 'dm_old']},
    installments={'deleted': ['dmi_old']}))
removed_related = lambda model, label, index=ACTOR: {'model': model, 'label': label, 'action': 2, 'changes': None, 'overrides': {},
                                                     'index': index, 'repr': None}
INVOICE_SET_NULL = {'invoice_id': None}
created_payment = lambda label, overrides: {'model': 'payment', 'label': label, 'action': 1, 'changes': None, 'created': True,
                                            'overrides': overrides, 'index': CHANGES_JSON, 'repr': None}
removed_invoice = lambda label, rows=0: [removed_related('invoicerows', f'{label}_row{index}') for index in reversed(range(rows))] + [
    removed_related('invoice', label, CHANGES_JSON)]
add('archive-soft-deleted-payment', 'owner', route('a_hidden', 'archive'), 200, ARCHIVED, effect=effect('owner', [
    created_payment('ahp', {'archived': ['None', 'True']}),
    updated('subscription', 'a_hidden', FLIP, DIRECT),
], subscriptions={'changed': {'a_hidden': {'archived': True}}}, payments={'changed': {'ahp': {'archived': True}}}))
# payment.save() after payment.invoice.delete(): the cached invoice has no pk any more (ValueError, the HTML 500 page).
add('archive-invoiced-course-payment-server-error', 'owner', route('a_inv_cs', 'archive'), 500, SERVER, effect=effect('owner',
    removed_invoice('inv_acs', rows=1), payments={'changed': {'acp_inv': INVOICE_SET_NULL}}, related_deleted=['inv_acs', 'inv_acs_row0']))
STATE['cs']['acs_inst']['archived'] = True
add('archive-invoiced-installment-payment', 'owner', route('a_inv_inst', 'archive'), 200, ARCHIVED, effect=effect('owner', [
    updated('payment', 'aiip', FLIP, DIRECT),
    *removed_invoice('inv_ai'),
    updated('coursesubscriptioninstallment', 'aii', FLIP, INSTALLMENT_INDEX),
    updated('coursesubscription', 'acs_inst', FLIP, COURSE_INDEX),
    updated('subscription', 'a_inv_inst', FLIP, DIRECT),
], subscriptions={'changed': {'a_inv_inst': {'archived': True}}}, payments={'changed': {'aiip': {'archived': True, 'invoice_id': None}}},
    cs={'changed': {'acs_inst': {'archived': True}}}, installments={'changed': {'aii': {'archived': True}}}, related_deleted=['inv_ai']))
STATE['cs']['acs_plain']['archived'] = True
add('archive-invoiced-subscription-payment-server-error', 'owner', route('a_inv_sub', 'archive'), 500, SERVER, effect=effect('owner', [
    updated('coursesubscription', 'acs_plain', FLIP, COURSE_INDEX),
    *removed_invoice('inv_as'),
], payments={'changed': {'asp_inv': INVOICE_SET_NULL}}, cs={'changed': {'acs_plain': {'archived': True}}}, related_deleted=['inv_as']))
add('delete-invoiced-subscription-payment', 'owner', route('d_inv', 'delete'), 200, DELETED, effect=effect('owner', [
    *removed_invoice('inv_d', rows=1),
    removed('payment', 'dip_inv', CHANGES_JSON),
    updated('subscription', 'd_inv', FLIP, DIRECT),
    removed('subscription', 'd_inv', CHANGES_JSON, {**GONE, 'payment': ['None', 'None']}),
], subscriptions={'deleted': ['d_inv']}, payments={'deleted': ['dip_inv']}, related_deleted=['inv_d', 'inv_d_row0']))
STATE['cs']['dci'].update(payment=None, deleted=True)
dci_saved = cs_repr('dci')
add('delete-invoiced-course-payments', 'owner', route('d_inv_cs', 'delete'), 200, DELETED, effect=effect('owner', [
    *removed_invoice('inv_dc'),
    removed('payment', 'dcip', CHANGES_JSON),
    *removed_invoice('inv_dci'),
    # Payment's pre_delete deletes dcii, whose pre_delete deletes dciip as stored (invoice already cleared).
    removed('payment', 'dciip', CHANGES_JSON, {'invoice': ['None', 'None']}), removed('coursesubscriptioninstallment', 'dcii', ACTOR),
    removed('payment', 'dciip', CHANGES_JSON),
    removed('coursesubscriptioninstallment', 'dcii', ACTOR, NULL_PAYMENT),
    dict(updated('coursesubscription', 'dci', {'deleted': ['False', 'True']}, COURSE_INDEX), repr=dci_saved),
    updated('subscription', 'd_inv_cs', FLIP, DIRECT),
    removed('subscription', 'd_inv_cs', CHANGES_JSON, GONE),
    dict(removed('coursesubscription', 'dci', ACTOR, {'deleted': ['True', 'None'], **NULL_PAYMENT}), repr=dci_saved),
], subscriptions={'deleted': ['d_inv_cs']}, payments={'deleted': ['dcip', 'dciip']}, cs={'deleted': ['dci']},
    installments={'deleted': ['dcii']}, related_deleted=['inv_dc', 'inv_dci']))

# The Collector's models: those without non-null references from the others first (in collection order), then the
# subscription (medical appointments, tokens, files and transfers reference it with non-null keys) and the camp
# subscription (its periods do). Deleted rows resolve no association: changes_json.sport_association or the actor's.
add('delete-related-rows', 'owner', route('d_rel', 'delete'), 200, DELETED, effect=effect('owner', [
    updated('subscription', 'd_rel', FLIP, DIRECT),
    removed_related('medicalappointments', 'rel_appointment'),
    removed_related('subscriptionmembership', 'rel_membership', CHANGES_JSON),
    removed_related('soldproducts', 'rel_sold'),
    removed_related('subscriptiontoken', 'rel_token'),
    removed_related('subscriptionfile', 'rel_file'),
    removed_related('subscriptiontransfer', 'rel_transfer'),
    removed_related('campsandretreatssubscriptionperiod', 'rel_camp_sub_period'),
    removed_related('carnetsubscription', 'rel_carnet'),
    removed('subscription', 'd_rel', CHANGES_JSON, GONE),
    removed_related('campsandretreatssubscription', 'rel_camp_sub'),
], subscriptions={'deleted': ['d_rel']},
    related_deleted=['rel_appointment', 'rel_membership', 'rel_sold', 'rel_token', 'rel_file', 'rel_transfer', 'rel_camp_sub_period',
                     'rel_carnet', 'rel_camp_sub']))
# transaction.on_commit(delete_signature_key_if_unreferenced) runs at once (autocommit): the object is deleted only
# when no row (base manager) still has the key; a missing object is not an error.
for label, gone in (('d_sig_a', []), ('d_sig_b', [SIGNATURE_KEY('shared')]), ('d_sig_own', [SIGNATURE_KEY('own')]), ('d_sig_missing', [])):
    add(f'delete-signed-{label[6:]}', 'owner', route(label, 'delete'), 200, DELETED, effect=effect('owner', [
        updated('subscription', label, FLIP, DIRECT),
        removed('subscription', label, CHANGES_JSON, GONE),
    ], subscriptions={'deleted': [label]}, objects_deleted=gone))
add('delete-owner-scoped-athlete-denied', 'owner', route('d_cs', 'delete'), 403,
    {'detail': 'Operazione non disponibile nel contesto dell’associazione.'}, headers={'X-Impersonation-Id': '$session:owner_athlete', 'User-Id': IDS['athlete']})
add('delete-restricted-missing-permission', 'restricted_collaborator', route('d_cs', 'delete'), 403,
    {'detail': 'Missing permission: association.members.delete'})
add('delete-admin-without-session', 'admin', route('d_cs', 'delete'), 403,
    {'detail': 'Seleziona un utente da impersonificare per accedere a questa sezione.'})
add('delete-anonymous', 'anonymous', route('d_cs', 'delete'), 401, {'detail': 'Authentication credentials were not provided.'})
add('delete-wrong-method', 'owner', route('d_cs', 'delete'), 405, {'detail': 'Method "GET" not allowed.'}, method='GET')
add('delete-options', 'owner', route('d_cs', 'delete'), 200, {'name': 'Subscription Delete', 'description': '', 'renders': ['application/json'],
    'parses': ['application/json', 'application/x-www-form-urlencoded', 'multipart/form-data']}, method='OPTIONS')

# ---------------------------------------------------------------- rare stored data
# creation_date__range with empty bounds compares with NULL: no payment of the period is archived.
add('archive-empty-dates', 'owner', route('a_nodate', 'archive'), 200, ARCHIVED, setup=NO_RULE,
    effect=effect('owner', [updated('subscription', 'a_nodate', FLIP, DIRECT)], subscriptions={'changed': {'a_nodate': {'archived': True}}}))
# creation_date__gte=None is a ValueError (the HTML 500 page) after the payment delete and the archived save.
add('delete-empty-dates-server-error', 'owner', route('d_nodate', 'delete'), 500, SERVER, setup=NO_RULE, effect=effect('owner', [
    removed('payment', 'dnd_sp', CHANGES_JSON),
    updated('subscription', 'd_nodate', FLIP, DIRECT),
], subscriptions={'changed': {'d_nodate': {'archived': True}}}, payments={'deleted': ['dnd_sp']}))
# A payment without an association: its entry is indexed through the actor.
add('archive-payment-without-association', 'owner', route('a_orphan', 'archive'), 200, ARCHIVED, effect=effect('owner', [
    updated('payment', 'orph', FLIP, ACTOR),
    updated('subscription', 'a_orphan', FLIP, DIRECT),
], subscriptions={'changed': {'a_orphan': {'archived': True}}}, payments={'changed': {'orph': {'archived': True}}}))
# Payment.save() raises for the subscription's payment: the course subscription's saves stay.
STATE['cs']['bad_cs']['archived'] = True
add('archive-payment-save-server-error', 'owner', route('a_badsave', 'archive'), 500, SERVER, effect=effect('owner', [
    updated('payment', 'bad_cp', FLIP, DIRECT),
    updated('coursesubscription', 'bad_cs', FLIP, COURSE_INDEX),
], payments={'changed': {'bad_cp': {'archived': True}}}, cs={'changed': {'bad_cs': {'archived': True}}}))
# ... and for a payment of the period: the subscription and the payments before it stay archived.
add('archive-period-payment-save-server-error', 'owner', route('a_badrange', 'archive'), 500, SERVER, effect=effect('owner', [
    updated('subscription', 'a_badrange', FLIP, DIRECT),
    updated('payment', 'rng_ok', FLIP, DIRECT),
], subscriptions={'changed': {'a_badrange': {'archived': True}}}, payments={'changed': {'rng_ok': {'archived': True}}}))
# The second course subscription's payment was deleted with the first: Payment.DoesNotExist (the HTML 500 page).
STATE['cs']['sh1'].update(payment=None, deleted=True)
sh1_saved = cs_repr('sh1')
add('delete-payment-on-two-course-subscriptions-server-error', 'owner', route('d_shared', 'delete'), 500, SERVER, effect=effect('owner', [
    removed('payment', 'sh_p', CHANGES_JSON),
    dict(updated('coursesubscription', 'sh1', {'deleted': ['False', 'True']}, COURSE_INDEX), repr=sh1_saved),
], payments={'deleted': ['sh_p']}, cs={'changed': {'sh1': {'deleted': True}}}))
STATE['cs']['shs'].update(payment=None, deleted=True)
shs_saved = cs_repr('shs')
add('delete-payment-shared-with-subscription-server-error', 'owner', route('d_shared_sub', 'delete'), 500, SERVER, effect=effect('owner', [
    removed('payment', 'shs_p', CHANGES_JSON),
    dict(updated('coursesubscription', 'shs', {'deleted': ['False', 'True']}, COURSE_INDEX), repr=shs_saved),
], payments={'deleted': ['shs_p']}, cs={'changed': {'shs': {'deleted': True}}}))
# The course subscription's payment is also its installment's: the payment delete removes the installment
# (nested), the installments read afterwards are none and the delete completes.
STATE['cs']['shi'].update(payment=None, deleted=True)
shi_saved = cs_repr('shi')
add('delete-payment-shared-with-installment', 'owner', route('d_shared_inst', 'delete'), 200, DELETED, effect=effect('owner', [
    removed('payment', 'shi_p', CHANGES_JSON), removed('coursesubscriptioninstallment', 'shi_i', ACTOR), removed('payment', 'shi_p', CHANGES_JSON),
    dict(updated('coursesubscription', 'shi', {'deleted': ['False', 'True']}, COURSE_INDEX), repr=shi_saved),
    updated('subscription', 'd_shared_inst', FLIP, DIRECT),
    removed('subscription', 'd_shared_inst', CHANGES_JSON, GONE),
    dict(removed('coursesubscription', 'shi', ACTOR, {'deleted': ['True', 'None'], **NULL_PAYMENT}), repr=shi_saved),
], subscriptions={'deleted': ['d_shared_inst']}, payments={'deleted': ['shi_p']}, cs={'deleted': ['shi']}, installments={'deleted': ['shi_i']}))

assert len({c['name'] for c in cases}) == len(cases)
OUTPUT.write_text(json.dumps({'matrix_version': 1, 'scope': 'Subscription reject, archive and delete authored sequence with audit effects',
                              'ids': IDS, 'category': CATEGORY, 'associates': ASSOCIATES, 'courses': COURSES, 'tags': TAGS,
                              'subscriptions': SUBSCRIPTIONS, 'payments': PAYMENTS, 'course_subscriptions': CS, 'installments': INSTALLMENTS,
                              'related': RELATED, 'location': LOCATION, 'objects': OBJECTS, 'cases': cases}, ensure_ascii=False, indent=1) + '\n')
print(len(cases), 'cases')
