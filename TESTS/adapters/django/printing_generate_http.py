"""Parent-only printing/generate HTTP runner; imports are inert.

Seeds associates, tutor relations, medical certificates, subscriptions,
memberships, courses, course subscriptions and installments, a carnet, a
payment category, an account, an invoice and payments (audit disabled), with
dates relative to the day of the run (date.today() + offset, UTC). Every case
is checked against the complete model state: a report whose rows read
Associate.main_tutor for an associate without a primary tutor relation
promotes the first one (is_primary False -> True) with one auditlog update
entry indexed through associate.sport_association; nothing else changes.
"""
from datetime import datetime, time as clock, timedelta, timezone
from decimal import Decimal

from TESTS.contracts import test_printing_generate_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/printing_generate_http.json', 'TESTS/contracts/test_printing_generate_http.py',
           'TESTS/contracts/xlsx.py', 'TESTS/adapters/django/printing_generate_http.py', 'BE/application/views/printing_views.py',
           'BE/application/utils/printing.py', 'BE/application/utils/excel_utils.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/carnet_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/mixin.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/application/permissions_registry.py', 'BE/application/urls.py') + harness.SOURCES

RELATION = 'application.AssociateTutorRelation'
MANAGED = (RELATION, harness.AUDIT, harness.INDEX)


def seed_rows(source, created):
    from application.models import (Associate, AssociateTutorRelation, CourseSubscription, Invoice, MedicalCertificate, Payment,
                                    Subscription)
    from application.models.balance_sheet_models import CustomAccounts
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.models.courses_models import Course, CourseSubscriptionInstallment
    from application.models.payment_models import PaymentCategory
    from application.models.subscriptions_models import SubscriptionMembership
    ids = source['ids']
    own = ids['own_association']
    today = datetime.now(timezone.utc).date()

    def day(offset):
        return None if offset is None else today + timedelta(days=offset)

    def moment(value):
        if value is None:
            return None
        offset, text = value
        return datetime.combine(day(offset), clock.fromisoformat(text), tzinfo=timezone.utc)

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], first_name=row['first_name'],
                                    last_name=row['last_name'], born_date=day(row['born']), email=row['email'], phone=row['phone']))
    for child, tutor, primary in source['relations']:
        relation = AssociateTutorRelation._base_manager.create(associate_id=associates[child]['id'], tutor_id=associates[tutor]['id'],
                                                               is_primary=primary)
        created.append((AssociateTutorRelation, relation.pk))
    certificates = source['certificates']
    for row in certificates.values():
        insert(MedicalCertificate, MedicalCertificate(medical_id=row['id'], expiration_date=day(row['expiration'])))
    for row in source['subscriptions'].values():
        owner = ids['owner'] if row['association'] == own else ids['foreign_owner']
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
            associate_id=associates[row['associate']]['id'] if row['associate'] else None, type=row['type'], status_flag=row['status'],
            medical_id=certificates[row['medical']]['id'] if row['medical'] else None, start_date=day(row['start']),
            end_date=day(row['end']), group_id=ids[row['group']] if row['group'] else None, archived=row['archived'],
            deleted=row['deleted']))
    for row in source['memberships']:
        insert(SubscriptionMembership, SubscriptionMembership(
            subscription_membership_id=row['id'], sport_association_id=row['association'], end_date=day(row['end']),
            membership_type=row['type'], associate_id=associates[row['associate']]['id'] if row['associate'] else None))
    courses = source['courses']
    for row in courses.values():
        insert(Course, Course(course_id=row['id'], sport_association_id=own, title=row['title']))
    carnets = source['carnets']
    for row in carnets.values():
        insert(Carnet, Carnet(carnet_id=row['id'], sport_association_id=own, title=row['title']))
    categories = source['categories']
    for row in categories.values():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], sport_association_id=own, name=row['name']))
    accounts = source['accounts']
    for row in accounts.values():
        insert(CustomAccounts, CustomAccounts(custom_account_id=row['id'], sport_association_id=own, name=row['name'],
                                              initial_balance=Decimal('0.00'), account_code='C1'))
    invoices = source['invoices']
    for row in invoices.values():
        insert(Invoice, Invoice(invoice_id=row['id'], sport_association_id=own, membership_fee=Decimal('50.00'),
                                activity_fee=Decimal('0.00'), number=row['number']))
    for label, row in source['payments'].items():
        payment = insert(Payment, Payment(
            payment_id=row['id'], subject=row['subject'], amount=Decimal(row['amount']), creation_date=moment(row['created']),
            payment_date=moment(row['paid_on']), paid=row['paid'], archived=row['archived'], expense=row['expense'],
            deleted=row['deleted'], group_id=ids[row['group']] if row['group'] else None, description=row['description'],
            meta=row['meta'], course=row['course'], sport_association_id=row['association'],
            associate_id=associates[row['associate']]['id'] if row['associate'] else None,
            payment_category_id=categories[row['category']]['id'] if row['category'] else None,
            custom_accounts_id=accounts[row['account']]['id'] if row['account'] else None,
            invoice_id=invoices[row['invoice']]['id'] if row['invoice'] else None))
        if not row['link']:
            continue
        kind, name = row['link']
        key = f"a1000000-0000-4000-8000-{int(row['id'][-12:]) + 500:012d}"
        if kind == 'carnet':
            insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=key, carnet_id_id=carnets[name]['id'], payment=payment))
            continue
        course_subscription = insert(CourseSubscription, CourseSubscription(
            course_subscription_id=key, course_id=courses[name]['id'], payment=payment if kind == 'course' else None))
        if kind == 'installment':
            insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
                course_subscription_installment_id=f"a1000000-0000-4000-8000-{int(row['id'][-12:]) + 600:012d}",
                course_subscription=course_subscription, payment=payment, payment_date=moment(row['created'])))

    # The report querysets have no ordering: fresh planner statistics make
    # every transport's seed answer them with the same plans (and the rows in
    # the same database order) from the first case on.
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('ANALYZE application_associate, application_associatetutorrelation, application_medicalcertificate, '
                       'application_subscription, application_subscriptionmembership, application_payment, application_invoice, '
                       'application_paymentcategory')


fixture_context = harness.fixture_context_for(contract, seed_rows)


def assert_effect(case, before, after, source, context):
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    ids = source['ids']
    promoted = (case['effect'] or {}).get('promoted', [])
    if not promoted:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    associates = source['associates']
    old_r, new_r = keyed(before[RELATION], 'id'), keyed(after[RELATION], 'id')
    expected_keys = []
    for child, _ in promoted:
        mine = sorted((r for r in old_r.values() if str(scalar(r['associate_id'])) == associates[child]['id']), key=lambda r: int(r['id']))
        contract.require(mine and not any(r['is_primary'] for r in mine), 'fixture.promotion-candidate')
        expected_keys.append(str(mine[0]['id']))
    contract.require(set(old_r) == set(new_r), 'state.relations-kept')
    for key in old_r:
        if key in expected_keys:
            contract.require(new_r[key] == {**old_r[key], 'is_primary': True}, 'state.relation-promoted')
        else:
            contract.require(new_r[key] == old_r[key], 'state.relation-unchanged')
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(promoted) and len(indexes) == len(promoted), 'audit.count')
    added.sort(key=lambda event: event['id'])
    for event, key, (child, actor) in zip(added, expected_keys, promoted):
        harness.check_audit_event(contract, case, event, indexes, before=before, ids=ids, model='AssociateTutorRelation', key=key,
                                  action=1, changes={'is_primary': ['False', 'True']}, actor=actor,
                                  association=associates[child]['association'], path='associate.sport_association', context=context)


run_workload = harness.run_workload_for(contract, assert_effect)
