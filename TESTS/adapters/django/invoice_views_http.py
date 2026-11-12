"""Parent-only invoice-views HTTP runner; imports are inert.

seed_rows inserts (audit disabled) three associations with their owner users
(invoice flags, default category), payment categories, associates and a tutor
relation, a supplier, an instructor, courses, a carnet, subscriptions,
memberships (one without a subscription), invoices with their payments, the
course/installment/carnet subscriptions that link payments to subscriptions,
and customer invoices. A 'promoted' case saves the first tutor relation of an
associate without a primary one (get_main_tutor) with its auditlog entry;
every other case must leave the complete model state unchanged.

RUNTIME['render'] renders a case's authored template context with Django's
template engine (render_to_string under translation.override), converting the
tagged values as TESTS/adapters/go/document_template_golden.py does; '$today'
in strings is the UTC date as dd/mm/YYYY. RUNTIME['einvoice'] builds the
authored CustomerInvoice row as an unsaved model instance and returns
generate_invoice_html's bytes.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
import types

from TESTS.contracts import test_invoice_views_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import document_views_http as views_runner

SOURCES = ('TESTS/fixtures/invoice_views_http.json', 'TESTS/contracts/test_invoice_views_http.py',
           'TESTS/adapters/django/invoice_views_http.py', 'TESTS/adapters/django/document_views_http.py',
           'TESTS/adapters/go/document_template_golden.py', 'TESTS/adapters/go/fatturapa_golden.py',
           'TESTS/adapters/go/payment_category_server.py', 'BE/docmanager/views/printing_views.py', 'BE/docmanager/access.py',
           'BE/instance/permissions.py', 'BE/docmanager/views/utils.py',
           'BE/application/serializers/invoice_serializers.py', 'BE/application/utils/payments_utils.py',
           'BE/application/models/utils.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/carnet_models.py', 'BE/application/templatetags/format.py',
           'BE/templates/document/application/invoice.html', 'BE/templates/document/application/invoice_classic.html',
           'BE/application/utils/api_utils.py', 'BE/application/impersonation_scope.py', 'BE/application/impersonation.py',
           'BE/application/mixin.py', 'BE/application/permissions_registry.py', 'BE/core/settings.py', 'BE/core/middleware.py',
           'BE/core/authentication.py') + harness.SOURCES
contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require,
                                 assert_case=base_contract.assert_case, ResponseFailure=base_contract.ResponseFailure)


def moment(text):
    return datetime.fromisoformat(text) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def customer_invoice(row, association_id):
    """The authored row as an unsaved CustomerInvoice (what the view loads)."""
    from application.models.invoices_models import CustomerInvoice
    fields = {key: value for key, value in row.items() if key not in ('id', 'association')}
    fields['transmitting_date'] = day(fields['transmitting_date'])
    fields['payment_expiry_date'] = day(fields['payment_expiry_date'])
    return CustomerInvoice(customer_invoice_id=row['id'], sport_association_id=association_id, **fields)


def association_ids(source):
    ids = {label: row['id'] for label, row in source['associations'].items()}
    ids.update(own=source['ids']['own_association'], foreign=source['ids']['foreign_association'])
    return ids


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Instructor, Payment,
                                    PaymentCategory, SportAssociation, Subscription, SubscriptionMembership, User)
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.models.invoices_models import CustomerInvoice, Invoice
    from application.models.payment_models import SupplierAndCustomers
    from application.models.user_models import AssociateTutorRelation
    ids = source['ids']
    associations = association_ids(source)
    alpha = source['associations']['alpha']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    seeded = datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=timezone.utc)
    for label, spec in source['associations'].items():
        insert(User, User(user_id=spec['user'], username=f'invoice-views-{label}', email=spec['email'], password='!synthetic',
                          role=User.ASSOCIATION, first_name='Views', last_name=label, enumerate_invoices=spec['enumerate_invoices'],
                          hide_category_name=spec['hide_category_name']))
        insert(SportAssociation, SportAssociation(
            sport_association_id=spec['id'], user_id=spec['user'], denomination=spec['denomination'], tax_code=spec['tax_code'],
            logo=spec['logo'], document_header=spec['document_header'], invoice_footer=spec['invoice_footer'],
            extra_text_invoices=spec['extra_text_invoices'], invoice_template=spec['invoice_template'],
            subscription_fee_plans=spec['subscription_fee_plans'], membership_fee_plans=spec['membership_fee_plans']))
    for label, row in source['categories'].items():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], name=row['name'], tax_deductible=row['tax_deductible'],
                                                deleted=row['deleted'], sport_association_id=alpha['id'], creation_date=seeded))
    for label, spec in source['associations'].items():
        if spec['default_category']:
            User.objects.filter(pk=spec['user']).update(default_payment_category_id=source['categories'][spec['default_category']]['id'])
    associate_ids = {label: row['id'] for label, row in source['associates'].items()}
    for label, row in source['associates'].items():
        insert(Associate, Associate(
            associate_id=row['id'], sport_association_id=alpha['id'], first_name=row['first'], last_name=row['last'],
            tax_code=row['tax_code'], sex=row['sex'], born_date=day(row['born']), born_city=row['born_city'], address=row['address'],
            address_city=row['city'], address_cap=row['cap'], phone=row['phone'], email=row['email'], is_minor=row['is_minor'],
            creation_date=seeded))
    for associate, tutor, primary in source['tutors']:
        relation = AssociateTutorRelation(associate_id=associate_ids[associate], tutor_id=associate_ids[tutor], is_primary=primary)
        relation.save()
        created.append((AssociateTutorRelation, relation.pk))
    for label, row in source['suppliers'].items():
        insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=row['id'], name=row['name'], address=row['address'],
                                                          tax_code=row['tax_code'], sport_association_id=alpha['id']))
    for label, row in source['instructors'].items():
        insert(Instructor, Instructor(instructor_id=row['id'], user_id=alpha['user'], first_name=row['first'], last_name=row['last']))
    for label, row in source['courses'].items():
        insert(Course, Course(course_id=row['id'], sport_association_id=alpha['id'], title=row['title'], creation_date=seeded))
    for label, row in source['carnets'].items():
        insert(Carnet, Carnet(carnet_id=row['id'], sport_association_id=alpha['id'], title=row['title']))
    subscription_ids = {label: row['id'] for label, row in source['subscriptions'].items()}
    for label, row in source['subscriptions'].items():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=associations[row['association']], user_id=alpha['user'],
            associate_id=associate_ids[row['associate']], type=row['type'], start_date=day(row['start']), end_date=day(row['end']),
            meta=row['meta'], custom_data=row['custom_data'], payment_id=None, creation_date=seeded,
            group_id=ids[row['group']] if row['group'] else None, status_flag=Subscription.ACCEPTED))
    for key, subscription, start, end in source['memberships']:
        insert(SubscriptionMembership, SubscriptionMembership(
            subscription_membership_id=key, subscription_id=subscription_ids[subscription] if subscription else None,
            sport_association_id=alpha['id'], start_date=day(start), end_date=day(end), membership_type='Annuale', creation_date=seeded))
    for label, row in source['invoices'].items():
        insert(Invoice, Invoice(
            invoice_id=row['id'], sport_association_id=associations[row['association']], activity_fee=Decimal(row['activity']),
            membership_fee=Decimal(row['membership']), description=row['description'], number=row['number'],
            cancelled=row['cancelled'], selected_tutor_id=associate_ids[row['selected_tutor']] if row['selected_tutor'] else None,
            deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None, creation_date=seeded))
    categories = {label: row['id'] for label, row in source['categories'].items()}
    for row in source['payments']:
        insert(Payment, Payment(
            payment_id=row['id'], invoice_id=source['invoices'][row['invoice']]['id'], sport_association_id=associations[row['association']],
            type=row['type'], subject=row['subject'], description=row['description'], notes=row['notes'], payment_date=moment(row['date']),
            payment_category_id=categories[row['category']] if row['category'] else None, meta_payment_categories=row['meta_categories'],
            associate_id=associate_ids[row['associate']] if row['associate'] else None,
            supplier_id=source['suppliers'][row['supplier']]['id'] if row['supplier'] else None,
            instructor_id=source['instructors'][row['instructor']]['id'] if row['instructor'] else None, course=row['course'],
            meta=row['meta'], deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None, amount=Decimal('10.00'),
            user_id=ids[source['payment_users'][row['invoice']]] if row['invoice'] in source['payment_users'] else None,
            creation_date=seeded))
    for label, payment in source['subscription_payments'].items():
        Subscription._base_manager.filter(pk=subscription_ids[label]).update(payment_id=payment)
    for key, course, subscription, payment, deleted in source['course_subscriptions']:
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=key, course_id=source['courses'][course]['id'],
            subscription_id=subscription_ids[subscription] if subscription else None, payment_id=payment, deleted=deleted,
            creation_date=seeded))
    for key, course_subscription, payment in source['installments']:
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=key, course_subscription_id=course_subscription, payment_id=payment,
            payment_date=seeded))
    for key, carnet, subscription, payment in source['carnet_subscriptions']:
        insert(CarnetSubscription, CarnetSubscription(
            carnet_subscription_id=key, carnet_id_id=source['carnets'][carnet]['id'] if carnet else None,
            subscription_id=subscription_ids[subscription] if subscription else None, payment_id=payment, user_id_id=alpha['user']))
    for label, row in source['customer_invoices'].items():
        insert(CustomerInvoice, customer_invoice(row, associations[row['association']]))


def render_einvoice(source):
    from application.serializers.invoice_serializers import generate_invoice_html
    associations = association_ids(source)

    def render(key):
        row = source['customer_invoices'][key]
        return generate_invoice_html(customer_invoice(row, associations[row['association']])).encode()
    return render


fixture_context = harness.fixture_context_for(contract, seed_rows)
RELATION = 'application.AssociateTutorRelation'
MANAGED = (RELATION, harness.AUDIT, harness.INDEX)


def assert_effect(case, before, after, source, context):
    """A 'promoted' case saves the associate's first tutor relation as primary with one auditlog update entry
    indexed through associate.sport_association (every associate belongs to alpha)."""
    from TESTS.adapters.django.workflow_writes_http import keyed, scalar
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    promoted = case.get('promoted') or []
    if not promoted:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    old_r, new_r = keyed(before[RELATION], 'id'), keyed(after[RELATION], 'id')
    expected_keys = []
    for child, _ in promoted:
        mine = sorted((r for r in old_r.values() if str(scalar(r['associate_id'])) == source['associates'][child]['id']),
                      key=lambda r: int(r['id']))
        contract.require(mine and not any(r['is_primary'] for r in mine), 'fixture.promotion-candidate')
        expected_keys.append(str(mine[0]['id']))
    contract.require(set(old_r) == set(new_r), 'state.relations-kept')
    for key in old_r:
        expected = {**old_r[key], 'is_primary': True} if key in expected_keys else old_r[key]
        contract.require(new_r[key] == expected, 'state.relation-promoted' if key in expected_keys else 'state.relation-unchanged')
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(promoted) and len(indexes) == len(promoted), 'audit.count')
    added.sort(key=lambda event: event['id'])
    for event, key, (child, actor) in zip(added, expected_keys, promoted):
        harness.check_audit_event(contract, case, event, indexes, before=before, ids=source['ids'], model='AssociateTutorRelation',
                                  key=key, action=1, changes={'is_primary': ['False', 'True']}, actor=actor,
                                  association=source['associations']['alpha']['id'], path='associate.sport_association',
                                  context=context)


_run_workload = harness.run_workload_for(contract, assert_effect)


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from django.conf import settings
    base_contract.RUNTIME.clear()
    base_contract.RUNTIME.update(bypass=settings.DOCUMENT_BYPASS_TOKEN, render=views_runner.render_expected,
                                 einvoice=render_einvoice(base_contract.load_source()))
    try:
        return _run_workload(label, backend, base_url, fixture, apps, raw, observer)
    finally:
        base_contract.RUNTIME.clear()
