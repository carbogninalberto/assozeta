"""Parent-only payment/list/export HTTP runner; imports are inert.

Seeds three payment categories (own, global, foreign), three accounts, two
associates, an instructor, a supplier, two courses, an invoice, a subscription,
the authored payments, course subscriptions, installments and account
transfers (audit disabled). The route is a read: every case must leave the
complete model state unchanged.
"""
from datetime import date, datetime
from decimal import Decimal

from TESTS.contracts import test_payment_export_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/payment_export_http.json', 'TESTS/contracts/test_payment_export_http.py',
           'TESTS/adapters/django/payment_export_http.py', 'BE/application/views/payment_views.py',
           'BE/application/serializers/balance_sheet.py', 'BE/application/models/payment_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/user_models.py',
           'BE/application/utils/subscriptions_utils.py', 'BE/application/mixin.py', 'BE/application/impersonation_scope.py',
           'BE/application/permissions_registry.py', 'BE/application/urls.py') + harness.SOURCES

ASSOCIATE_FIELDS = ('first_name', 'last_name', 'tax_code', 'born_city', 'address_city', 'address', 'email', 'phone', 'phone_2',
                    'phone_3', 'phone_4', 'notes', 'nationality', 'id_number')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import (Associate, Course, CourseSubscription, CourseSubscriptionInstallment, Instructor, Invoice, Payment,
                                    PaymentCategory, Subscription, SupplierAndCustomers)
    from application.models.balance_sheet_models import CustomAccounts, CustomAccountsTransfer
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    categories = source['categories']
    for row in categories.values():
        insert(PaymentCategory, PaymentCategory(payment_category_id=row['id'], name=row['name'], sport_association_id=row['association']))
    accounts = source['accounts']
    for row in accounts.values():
        insert(CustomAccounts, CustomAccounts(custom_account_id=row['id'], name=row['name'], initial_balance=Decimal(row['initial']),
                                              account_type=row['type'], account_code=row['code'],
                                              sport_association_id=row['association']))
    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], sport_association_id=row['association'], creation_date=moment(row['created']),
                                    born_date=date.fromisoformat(row['born_date']) if row['born_date'] else None,
                                    **{key: row[key] for key in ASSOCIATE_FIELDS}))
    teacher = source['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=owner, first_name=teacher['first_name'],
                                  last_name=teacher['last_name'], email=teacher['email'], tax_code=teacher['tax_code'],
                                  phone=teacher['phone'], address=teacher['address']))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], address=vendor['address'], city=vendor['city'],
                                                      tax_code=vendor['tax_code'], sport_association_id=own))
    courses = source['courses']
    for row in courses.values():
        insert(Course, Course(course_id=row['id'], sport_association_id=own, course_type=1, title=row['title'], fee=Decimal('50.00'),
                              deleted=row['deleted']))
    bill = source['invoice']
    insert(Invoice, Invoice(invoice_id=bill['id'], sport_association_id=own, creation_date=moment(bill['created']),
                            membership_fee=Decimal('40.00'), activity_fee=Decimal('10.00'), number=bill['number']))
    sub = source['subscription']
    insert(Subscription, Subscription(subscription_id=sub['id'], sport_association_id=own, user_id=owner,
                                      associate_id=associates[sub['associate']]['id'], creation_date=moment(sub['created']),
                                      status_flag=Subscription.ACCEPTED))
    payments = source['payments']
    for row in payments.values():
        insert(Payment, Payment(
            payment_id=row['id'], type=row['type'], subject=row['subject'], amount=Decimal(row['amount']),
            creation_date=moment(row['created']), payment_date=moment(row['paid_on']), paid=row['paid'], expense=row['expense'],
            description=row['description'], payment_category_id=categories[row['category']]['id'] if row['category'] else None,
            meta_payment_categories=row['meta_payment_categories'], invoice_id=bill['id'] if row['invoice'] else None,
            associate_id=associates[row['associate']]['id'] if row['associate'] else None,
            supplier_id=vendor['id'] if row['supplier'] else None, instructor_id=teacher['id'] if row['instructor'] else None,
            sport_association_id=row['association'], payment_intent_id=row['payment_intent_id'], archived=row['archived'],
            invoice_fiscal_code=row['invoice_fiscal_code'], meta=row['meta'], notes=row['notes'],
            custom_accounts_id=accounts[row['account']]['id'] if row['account'] else None, deleted=row['deleted'],
            group_id=row['group'], course=row['course']))
    course_subscriptions = source['course_subscriptions']
    for row in course_subscriptions.values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=courses[row['course']]['id'], subscription_id=sub['id'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted']))
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=course_subscriptions[row['cs']]['id'],
            id=row['number'], amount=Decimal('40.00'), paid=True, payment_date=moment('2024-04-15T09:30:00Z'),
            payment_id=payments[row['payment']]['id'], deleted=row['deleted']))
    for row in source['transfers'].values():
        insert(CustomAccountsTransfer, CustomAccountsTransfer(
            custom_account_transfer_id=row['id'], custom_account_from_id=accounts[row['from']]['id'] if row['from'] else None,
            custom_account_to_id=accounts[row['to']]['id'] if row['to'] else None, amount=Decimal(row['amount']),
            date=moment(row['date']), sport_association_id=row['association']))


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
