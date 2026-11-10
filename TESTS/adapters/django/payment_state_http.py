"""Parent-only payment approve, cancel, delete and bulk archive/delete HTTP runner; imports are inert.

Seeds a document, a payment category, an associate with a subscription, an
instructor, a supplier, a course, a carnet, the authored payments (one with
an attachment), supplier invoices, course subscriptions (with a membership
link), installments and a carnet holding (audit disabled). Each case is
checked against the complete model state: only the authored payment and
supplier invoice columns change, deleted payments and installments are gone,
every SET_NULL payment reference to a deleted payment is cleared and its
many-to-many rows removed, and the new auditlog entries match the authored
ones in order (delete entries rendered from the rows before the request) with
their index rows. A '$now' payment date must be recent, equal in the row, the
audit entry and the response.

The paid flow (approve of an unpaid income payment with a party) is checked
with payment_paid_flow: the created invoice, the authored course subscriptions
marked paid, the broadcast rows, the owner's notification (Redis list, cache,
Channels push, mirror) and the print_document_invoice messages on the run's
private Celery broker. A second association owner whose invoice date follows
the payment date (with a token) is seeded for it.

Seeded invoices (linked to their payments) change only as authored
('invoices_changed': cancelled and meta). A CURRENT_HOST print stub
(invoice-sync-print's service_context) runs for the whole run: each case's
outbound requests must be exactly its authored 'prints' (payment delete prints
an invoiced payment's invoice eagerly).
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import ipaddress
import types

from TESTS.contracts import test_payment_state_http as base_contract
from TESTS.adapters.django import invoice_sync_print_http as printer
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import payment_paid_flow as paid
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/payment_state_http.json', 'TESTS/contracts/test_payment_state_http.py',
           'TESTS/adapters/django/payment_state_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/payment_views.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/application/models/carnet_models.py',
           'BE/application/signals.py', 'BE/application/mixin.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/utils/api_utils.py', 'BE/application/utils/printing.py', 'BE/docmanager/models.py',
           'BE/application/serializers/invoice_serializers.py', 'TESTS/adapters/django/invoice_sync_print_http.py',
           'BE/application/printing_tasks.py', 'BE/instance/restore/task_base.py') + paid.SOURCES + harness.SOURCES
PAYMENT, INSTALLMENT = 'application.Payment', 'application.CourseSubscriptionInstallment'
SUPPLIER_INVOICE = 'application.InvoiceSuppliers'
REFERENCES = ('application.CourseSubscription', 'application.CarnetSubscription')
THROUGH = {'application.Payment_attachments': 'payment_id', 'application.CourseSubscription_membership_payments': 'payment_id'}
MANAGED = (PAYMENT, INSTALLMENT, SUPPLIER_INVOICE, *REFERENCES, *THROUGH, *paid.MANAGED, harness.AUDIT, harness.INDEX)
PAYMENT_COLUMNS = {'paid': 'paid', 'payment_date': 'payment_date', 'category': 'payment_category_id', 'description': 'description',
                   'archived': 'archived', 'deleted': 'deleted', 'invoice': 'invoice_id'}
MODELS = {'payment': ('Payment', 'payments'), 'coursesubscriptioninstallment': ('CourseSubscriptionInstallment', 'installments'),
          'invoicesuppliers': ('InvoiceSuppliers', 'supplier_invoices'), 'invoice': ('Invoice', 'invoices'),
          'coursesubscription': ('CourseSubscription', 'course_subscriptions'),
          'notificationsbroadcast': ('NotificationsBroadcast', None), 'usernotificationsbroadcast': ('UserNotificationsBroadcast', None)}
STATE = {}


def _assert_case(case, base_url, *, token, sessions):
    """The authored request, with the print stub scripted for it and its outbound requests checked after."""
    stub = printer.STUB
    with stub['lock']:
        stub['log'] = []
        stub['script'] = [item['reply'] for item in case['prints']]
    stub['token'] = token
    status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    printer.check_prints(case, None)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 LAST=base_contract.LAST, ResponseFailure=base_contract.ResponseFailure)


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    contract.require(row is not None, 'fixture.default-category')
    return str(row.pk), row.name


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Instructor, Invoice,
                                    InvoiceSuppliers, Payment, PaymentCategory, SportAssociation, SupplierAndCustomers, Subscription, User)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from docmanager.models import Document
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    flag = source['flag_owner']
    insert(User, User(user_id=flag['id'], username=flag['username'], email=flag['email'], password='!synthetic', role=User.ASSOCIATION,
                      first_name=flag['first'], last_name=flag['last'], payment_date_equal_invoice_date=True,
                      starting_number_invoices=flag['starting_number']))
    insert(SportAssociation, SportAssociation(sport_association_id=flag['association'], user_id=flag['id'], denomination=flag['denomination'],
                                              tax_code='79900000003'))
    document = source['document']
    insert(Document, Document(document_id=document['id'], token=document['token'], filename=document['filename'], filepath=document['filepath'],
                              file_size_bytes=document['size'], creation_date=moment(document['created'])))
    insert(PaymentCategory, PaymentCategory(payment_category_id=source['category'], name=source['category_name'], sport_association_id=own))
    person = source['associate']
    insert(Associate, Associate(associate_id=person['id'], sport_association_id=own, first_name=person['first'], last_name=person['last'],
                                sex=person['sex'], tax_code=person['tax'], born_date=datetime.fromisoformat(person['born']).date(),
                                email=person['email'], is_minor=False))
    insert(Subscription, Subscription(subscription_id=source['subscription']['id'], sport_association_id=own, user_id=owner,
                                      associate_id=person['id'], creation_date=moment(source['subscription']['created']),
                                      status_flag=Subscription.ACCEPTED))
    other = source['flag_associate']
    insert(Associate, Associate(associate_id=other['id'], sport_association_id=flag['association'], first_name=other['first'],
                                last_name=other['last'], sex='F', born_date=datetime(1991, 2, 3).date(), is_minor=False))
    teacher = source['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=owner, first_name=teacher['first_name'], last_name=teacher['last_name'],
                                  email=teacher['email'], tax_code=teacher['tax_code'],
                                  default_hourly_billing=Decimal(teacher['default_hourly_billing'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], sport_association_id=own))
    insert(Course, Course(course_id=source['course']['id'], sport_association_id=own, course_type=1, title=source['course']['title'],
                          fee=Decimal('50.00')))
    insert(Carnet, Carnet(carnet_id=source['carnet']['id'], title=source['carnet']['title'], sport_association_id=own, fee=Decimal('1.00')))
    payments = source['payments']
    bills = source['invoices']
    for row in bills.values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=own, creation_date=moment(row['created']),
                                membership_fee=Decimal(row['membership_fee']), activity_fee=Decimal(row['activity_fee']),
                                description=row['description'], number=row['number'], archived=True))
    for row in payments.values():
        insert(Payment, Payment(
            invoice_id=bills[row['invoice']]['id'] if row['invoice'] else None,
            payment_id=row['id'], sport_association_id=row['association'], subject=row['subject'], amount=Decimal(row['amount']),
            paid=row['paid'], expense=row['expense'], meta=row['meta'],
            associate_id=(other['id'] if row['associate'] == 'flag' else person['id']) if row['associate'] else None,
            meta_payment_categories=row.get('meta_categories'),
            user_id=owner if row['user'] else None, supplier_id=vendor['id'] if row['supplier'] else None,
            instructor_id=teacher['id'] if row['instructor'] else None, group_id=ids[row['group']] if row['group'] else None,
            deleted=row['deleted'], archived=row['archived'], payment_category_id=row['category'], creation_date=moment(row['created']),
            payment_date=moment(row['payment_date']), description=row['description']))
        if row['attachment']:
            Payment.attachments.through.objects.bulk_create([Payment.attachments.through(payment_id=row['id'], document_id=document['id'])])
    for row in source['supplier_invoices'].values():
        insert(InvoiceSuppliers, InvoiceSuppliers(invoice_supplier_id=row['id'], invoice_identifier=row['identifier'], amount=Decimal(row['amount']),
                                                  sport_association_id=own, payment_id=payments[row['payment']]['id']))
    for row in source['course_subscriptions'].values():
        insert(CourseSubscription, CourseSubscription(course_subscription_id=row['id'], course_id=source['course']['id'],
                                                      subscription_id=source['subscription']['id'], paid=row.get('paid', False),
                                                      payment_id=payments[row['payment']]['id'] if row['payment'] else None))
        through = CourseSubscription.membership_payments.through
        through.objects.bulk_create([through(coursesubscription_id=row['id'], payment_id=payments[p]['id']) for p in row['memberships']])
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['cs']]['id'], id=row['number'],
            amount=Decimal('10.00'), paid=False, payment_date=moment('2026-09-15T08:30:00Z'), payment_id=payments[row['payment']]['id'],
            group_id=ids[row['group']] if row['group'] else None, deleted=row['deleted']))
    for row in source['holdings'].values():
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'],
                                                      carnet_id_id=source['carnet']['id'] if row.get('carnet', True) else None,
                                                      subscription_id=source['subscription']['id'] if row.get('subscription') else None,
                                                      payment_id=payments[row['payment']]['id']))
        through = CarnetSubscription.course_subscription.through
        through.objects.bulk_create([through(carnetsubscription_id=row['id'], coursesubscription_id=source['course_subscriptions'][c]['id'])
                                     for c in row.get('courses', [])])


def invoices():
    from application.models import Invoice
    return Invoice


def broadcasts():
    from notifications.models import NotificationsBroadcast
    return NotificationsBroadcast


def user_broadcasts():
    from notifications.models import UserNotificationsBroadcast
    return UserNotificationsBroadcast


def recipients(fixture):
    source = contract.load_source()
    return {'owner': source['ids']['owner'], 'flag_owner': source['flag_owner']['id']}


base_fixture_context = paid.with_notifications(harness.with_celery_broker(harness.fixture_context_for(
    contract, seed_rows, purge=(invoices, user_broadcasts, broadcasts))), recipients)


@contextmanager
def service_context(report, log):
    """The Channels layer on the run's Redis and the CURRENT_HOST print stub."""
    with ExitStack() as stack:
        stack.enter_context(paid.channel_layers())
        stack.enter_context(printer.service_context(report, log))
        yield


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from rest_framework_simplejwt.tokens import AccessToken
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'] = category
        fixture['sessions'] = {**fixture['sessions'], '$default_category': category, '$default_category_name': name}
        token = AccessToken()
        token['user_id'] = contract.load_source()['flag_owner']['id']
        fixture['tokens'] = {**fixture['tokens'], 'flag_owner': str(token)}
        STATE['fixture'] = fixture
        try:
            yield fixture
        finally:
            STATE.pop('fixture', None)


def captured(source):
    """auditlog's delete diffs of the seeded payments and installments, captured before each case."""
    from application.models import CourseSubscriptionInstallment, Payment
    rows = {}
    for model, key in ((Payment, 'payments'), (CourseSubscriptionInstallment, 'installments')):
        for row in model._base_manager.filter(pk__in=[r['id'] for r in source[key].values()]):
            rows[str(row.pk)] = replace.deleted_changes(model, row)
    return {'rows': rows, **paid.prepare(STATE['fixture'])}


def value(item):
    if isinstance(item, dict) and set(item) == {'type', 'value'}:
        return moment(item['value']) if item['type'] == 'datetime' else item['value']
    return item


def flat(row):
    return {key: value(item) for key, item in row.items()}


def audit_text(when):
    """auditlog's str() of a DateTimeField: the naive UTC value."""
    return str(when.astimezone(timezone.utc).replace(tzinfo=None))


def expected_rows(before, key, removed, changes, cleared):
    result = []
    for row in before:
        row = flat(row)
        ident = str(row[key])
        if ident in removed:
            continue
        row = {**row, **changes.get(ident, {})}
        if row.get('payment_id') is not None and str(row['payment_id']) in cleared:
            row['payment_id'] = None
        result.append(row)
    return result


def side_channels(case, effect, fixture, context, binding, created_broadcast, until):
    """The Celery messages and the notification of the case."""
    since = context['prepared']['started']
    values = {**binding}
    if case['actor'] != 'anonymous':
        values['$authorization'] = 'Bearer ' + fixture['tokens'][case['actor']]
    celery = [{**spec, 'args': [values.get(item, item) if isinstance(item, str) else item for item in spec['args']]}
              for spec in (effect or {}).get('celery', [])]
    harness.celery_published(contract, fixture, celery, since=since, until=until)
    notification = (effect or {}).get('notification')
    paid.check_notification(contract, notification, fixture, context['prepared'], created_broadcast, until)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    fixture, ids = context['fixture'], source['ids']
    until = datetime.now(timezone.utc).timestamp()
    binding = {}
    created_broadcast = any(e['model'] == 'notificationsbroadcast' for e in (effect or {}).get('audit', []))
    try:
        check_state(case, effect, before, after, source, context, binding)
    except BaseException:
        try:  # drain the side channels, keeping the first failure
            side_channels(case, effect, fixture, context, binding, created_broadcast, until)
        except BaseException:
            pass
        raise
    side_channels(case, effect, fixture, context, binding, created_broadcast, until)


def check_invoices(before, after, changed, source):
    """The seeded invoices change only in the authored columns; returns `before` with them applied."""
    old = {str(transfer_plain(row['invoice_id'])): row for row in before[paid.INVOICE]}
    new = {str(transfer_plain(row['invoice_id'])): row for row in after[paid.INVOICE]}
    rows = []
    expected_keys = {source['invoices'][label]['id'] for label in changed}
    contract.require(expected_keys <= set(old) and expected_keys <= set(new), 'state.invoice-seeded')
    for label, columns in changed.items():
        key = source['invoices'][label]['id']
        contract.require(transfer_plain(new[key]) == {**transfer_plain(old[key]), **columns}, 'state.invoice-changed:' + label)
    for key, row in old.items():
        rows.append(new[key] if key in expected_keys else row)
    return {**before, paid.INVOICE: rows}


def transfer_plain(value):
    return paid._plain(value)


def check_state(case, effect, before, after, source, context, binding):
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    ids = {**ids, 'flag_owner': source['flag_owner']['id']}
    notification = effect.get('notification')
    if notification is not None:
        notification = {**notification, 'user_id': ids[notification['user']]}
    created_broadcast = any(e['model'] == 'notificationsbroadcast' for e in effect['audit'])
    paid.check_broadcasts(contract, before, after, notification, created_broadcast, binding)
    seeded = check_invoices(before, after, effect.get('invoices_changed', {}), source)
    invoice_now = paid.check_invoice(contract, seeded, after, effect.get('invoice'), contract.LAST.get('invoices', []), binding)
    pay_id = lambda label: source['payments'][label]['id']
    gone_payments = {pay_id(label) for label in effect['payments_deleted']}
    gone_installments = {source['installments'][label]['id'] for label in effect['installments_deleted']}
    after_payments = {str(row['payment_id']): row for row in map(flat, after[PAYMENT])}
    bindings = {'$default_category': fixture['default_category'], **binding}
    now_text = {}

    payment_changes = {}
    for label, fields in effect['payments_changed'].items():
        key, columns = pay_id(label), {}
        for name, item in fields.items():
            if item == '$now':
                stored = after_payments.get(key, {}).get(name)
                contract.require(isinstance(stored, datetime) and abs(datetime.now(timezone.utc) - stored) < timedelta(minutes=10),
                                 'state.payment-date-now')
                response = ((contract.LAST.get('body') or {}).get('data') or {}).get('payment') or {}
                contract.require(moment(response.get('payment_date')) == stored, 'response.payment-date-now')
                now_text[label] = audit_text(stored)
                item = stored
            elif name == 'payment_date':
                item = moment(item)
            columns[PAYMENT_COLUMNS[name]] = bindings.get(item, item) if isinstance(item, str) else item
        payment_changes[key] = columns
    normalize = lambda rows: [{k: str(v) if k == 'invoice_id' and v is not None else v for k, v in row.items()} for row in rows]
    contract.require(normalize([flat(r) for r in after[PAYMENT]])
                     == normalize(expected_rows(before[PAYMENT], 'payment_id', gone_payments, payment_changes, set())), 'state.payments')
    contract.require([flat(r) for r in after[INSTALLMENT]] == expected_rows(before[INSTALLMENT], 'course_subscription_installment_id',
                                                                             gone_installments, {}, gone_payments), 'state.installments')
    after_invoices = {str(row['invoice_supplier_id']): row for row in map(flat, after[SUPPLIER_INVOICE])}
    invoice_changes = {}
    for label, fields in effect['supplier_invoices_changed'].items():
        key = source['supplier_invoices'][label]['id']
        changed = {}
        for name, item in fields.items():
            if item == '$now':
                stored = after_invoices.get(key, {}).get(name)
                contract.require(isinstance(stored, datetime) and abs(datetime.now(timezone.utc) - stored) < timedelta(minutes=10),
                                 'state.supplier-invoice-now')
                now_text['si:' + label] = audit_text(stored)
                item = stored
            elif name == 'payment_date':
                item = moment(item)
            changed[name] = item
        invoice_changes[key] = changed
    contract.require([flat(r) for r in after[SUPPLIER_INVOICE]] == expected_rows(before[SUPPLIER_INVOICE], 'invoice_supplier_id', set(),
                                                                                  invoice_changes, gone_payments), 'state.supplier-invoices')
    marked = {source['course_subscriptions'][label]['id'] for label in effect.get('course_subscriptions_paid', [])}
    for label in REFERENCES:
        expected = []
        for row in map(flat, before[label]):
            if row['payment_id'] is not None and str(row['payment_id']) in gone_payments:
                row = {**row, 'payment_id': None}
            if label == 'application.CourseSubscription' and str(row['course_subscription_id']) in marked:
                row = {**row, 'paid': True}
            expected.append(row)
        contract.require([flat(r) for r in after[label]] == expected, 'state.references')
    for label, column in THROUGH.items():
        contract.require([flat(r) for r in after[label]] == [flat(r) for r in before[label] if str(flat(r)[column]) not in gone_payments],
                         'state.through-rows')

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: int(row['id']))
    contract.require(len(added) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    created_now = []
    for event, entry in zip(added, effect['audit']):
        name, table = MODELS[entry['model']]
        app = 'notifications' if entry['model'] in ('notificationsbroadcast', 'usernotificationsbroadcast') else 'application'
        if table is None or entry['label'].startswith('$'):
            key = binding['$' + entry['model']]
        else:
            key = source[table][entry['label']]['id']
        where = f":{entry['model']}:{entry['label']}"
        representation = f'{name} object ({key})'
        changes = entry['changes']
        if changes is None:
            changes = context['prepared']['rows'][key]
        elif changes == '$created':
            changes, representation = paid.broadcast_changes(entry['model'], key)
        else:
            resolved = {}
            for field, pair in changes.items():
                items = []
                for item in pair:
                    if item == '$now' and entry['model'] == 'invoice':
                        value = event['changes'].get(field, [None, None])[len(items)]
                        contract.require(isinstance(value, str) and abs(datetime.now(timezone.utc).replace(tzinfo=None)
                                                                         - datetime.fromisoformat(value)) < timedelta(minutes=10), 'audit.now' + where)
                        created_now.append(value)
                        items.append(value)
                    elif item == '$now':
                        items.append(now_text.get(entry['label'], now_text.get('si:' + entry['label'])))
                    else:
                        items.append(bindings.get(item, item))
                resolved[field] = items
            changes = resolved
        if entry['model'] == 'coursesubscription':
            # CourseSubscription.__str__ is str(self.__dict__) (the ModelState address varies).
            text = event['object_repr']
            contract.require(text.startswith("{'_state': <django.db.models.base.ModelState object at 0x")
                             and f"'course_subscription_id': UUID('{key}')" in text and "'paid': True" in text, 'audit.repr' + where)
            representation = text
        contract.require(event['content_type_id'] == types[(app, entry['model'])] and event['action'] == entry['action']
                         and event['object_pk'] == key and event['object_repr'] == representation, 'audit.identity' + where)
        contract.require(event['changes'] == changes, 'audit.changes' + where + repr((event['changes'], changes)))
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if entry['index'] is None:
            contract.require(index is None, 'index.absent' + where)
        else:
            contract.require(index is not None and index['sport_association_id'] == entry['index'][0]
                             and index['resolution_path'] == entry['index'][1], 'index.fields' + where)
    contract.require(len(set(created_now)) <= 1, 'audit.now-consistent')
    if created_now and invoice_now is not None:
        contract.require(created_now[0] == invoice_now, 'audit.now-stored')


run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
