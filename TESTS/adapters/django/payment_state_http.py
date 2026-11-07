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
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import ipaddress

from TESTS.contracts import test_payment_state_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/payment_state_http.json', 'TESTS/contracts/test_payment_state_http.py',
           'TESTS/adapters/django/payment_state_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'BE/application/views/payment_views.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py', 'BE/application/models/carnet_models.py',
           'BE/application/signals.py', 'BE/application/mixin.py', 'BE/application/impersonation_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/utils/api_utils.py', 'BE/application/utils/printing.py', 'BE/docmanager/models.py') + harness.SOURCES
PAYMENT, INSTALLMENT = 'application.Payment', 'application.CourseSubscriptionInstallment'
SUPPLIER_INVOICE = 'application.InvoiceSuppliers'
REFERENCES = ('application.CourseSubscription', 'application.CarnetSubscription')
THROUGH = {'application.Payment_attachments': 'payment_id', 'application.CourseSubscription_membership_payments': 'payment_id'}
MANAGED = (PAYMENT, INSTALLMENT, SUPPLIER_INVOICE, *REFERENCES, *THROUGH, harness.AUDIT, harness.INDEX)
PAYMENT_COLUMNS = {'paid': 'paid', 'payment_date': 'payment_date', 'category': 'payment_category_id', 'description': 'description',
                   'archived': 'archived', 'deleted': 'deleted'}
MODELS = {'payment': ('Payment', 'payments'), 'coursesubscriptioninstallment': ('CourseSubscriptionInstallment', 'installments'),
          'invoicesuppliers': ('InvoiceSuppliers', 'supplier_invoices')}


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def default_category():
    from application.models import PaymentCategory
    row = PaymentCategory.objects.filter(name__iexact='entrate e proventi da attività tipiche').order_by('pk').first()
    contract.require(row is not None, 'fixture.default-category')
    return str(row.pk), row.name


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Instructor, InvoiceSuppliers,
                                    Payment, PaymentCategory, SupplierAndCustomers, Subscription)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from docmanager.models import Document
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

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
    for row in payments.values():
        insert(Payment, Payment(
            payment_id=row['id'], sport_association_id=row['association'], subject=row['subject'], amount=Decimal(row['amount']),
            paid=row['paid'], expense=row['expense'], meta=row['meta'], associate_id=person['id'] if row['associate'] else None,
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
                                                      subscription_id=source['subscription']['id'],
                                                      payment_id=payments[row['payment']]['id'] if row['payment'] else None))
        through = CourseSubscription.membership_payments.through
        through.objects.bulk_create([through(coursesubscription_id=row['id'], payment_id=payments[p]['id']) for p in row['memberships']])
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=source['course_subscriptions'][row['cs']]['id'], id=row['number'],
            amount=Decimal('10.00'), paid=False, payment_date=moment('2026-09-15T08:30:00Z'), payment_id=payments[row['payment']]['id'],
            group_id=ids[row['group']] if row['group'] else None, deleted=row['deleted']))
    for row in source['holdings'].values():
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'], carnet_id_id=source['carnet']['id'],
                                                      payment_id=payments[row['payment']]['id']))


base_fixture_context = harness.fixture_context_for(contract, seed_rows)


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    with base_fixture_context(apps, private_work, raw, cleanup_receipt=cleanup_receipt) as fixture:
        category, name = default_category()
        fixture['default_category'] = category
        fixture['sessions'] = {**fixture['sessions'], '$default_category': category, '$default_category_name': name}
        yield fixture


def captured(source):
    """auditlog's delete diffs of the seeded payments and installments, captured before each case."""
    from application.models import CourseSubscriptionInstallment, Payment
    rows = {}
    for model, key in ((Payment, 'payments'), (CourseSubscriptionInstallment, 'installments')):
        for row in model._base_manager.filter(pk__in=[r['id'] for r in source[key].values()]):
            rows[str(row.pk)] = replace.deleted_changes(model, row)
    return rows


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


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    fixture, ids = context['fixture'], source['ids']
    pay_id = lambda label: source['payments'][label]['id']
    gone_payments = {pay_id(label) for label in effect['payments_deleted']}
    gone_installments = {source['installments'][label]['id'] for label in effect['installments_deleted']}
    after_payments = {str(row['payment_id']): row for row in map(flat, after[PAYMENT])}
    bindings = {'$default_category': fixture['default_category']}
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
    contract.require([flat(r) for r in after[PAYMENT]] == expected_rows(before[PAYMENT], 'payment_id', gone_payments, payment_changes, set()),
                     'state.payments')
    contract.require([flat(r) for r in after[INSTALLMENT]] == expected_rows(before[INSTALLMENT], 'course_subscription_installment_id',
                                                                             gone_installments, {}, gone_payments), 'state.installments')
    invoice_changes = {}
    for label, fields in effect['supplier_invoices_changed'].items():
        invoice_changes[source['supplier_invoices'][label]['id']] = {name: moment(item) if name == 'payment_date' else item
                                                                     for name, item in fields.items()}
    contract.require([flat(r) for r in after[SUPPLIER_INVOICE]] == expected_rows(before[SUPPLIER_INVOICE], 'invoice_supplier_id', set(),
                                                                                  invoice_changes, gone_payments), 'state.supplier-invoices')
    for label in REFERENCES:
        cleared = [{**row, 'payment_id': None} if row['payment_id'] is not None and str(row['payment_id']) in gone_payments else row
                   for row in map(flat, before[label])]
        contract.require([flat(r) for r in after[label]] == cleared, 'state.references')
    for label, column in THROUGH.items():
        contract.require([flat(r) for r in after[label]] == [flat(r) for r in before[label] if str(flat(r)[column]) not in gone_payments],
                         'state.through-rows')

    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda row: int(row['id']))
    contract.require(len(added) == len(effect['audit']) and len(indexes) == sum(1 for e in effect['audit'] if e['index']), 'audit.count')
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': fixture['sessions'][label], 'actor_id': actor, 'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    for event, entry in zip(added, effect['audit']):
        name, table = MODELS[entry['model']]
        key = source[table][entry['label']]['id']
        where = f":{entry['model']}:{entry['label']}"
        contract.require(event['content_type_id'] == types[entry['model']] and event['action'] == entry['action'] and event['object_pk'] == key
                         and event['object_repr'] == f'{name} object ({key})', 'audit.identity' + where)
        changes = entry['changes']
        if changes is None:
            changes = context['prepared'][key]
        else:
            changes = {field: [now_text[entry['label']] if item == '$now' else bindings.get(item, item) for item in pair]
                       for field, pair in changes.items()}
        contract.require(event['changes'] == changes, 'audit.changes' + where)
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


run_workload = harness.run_workload_for(contract, assert_effect, prepare=captured)
