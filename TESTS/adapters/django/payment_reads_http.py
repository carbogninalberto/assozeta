"""Parent-only payment list, payment stats and subscription payments HTTP runner; imports are inert.

Seeds a payment category, two accounts, four documents, five associates (two
tutor relations), an instructor, a supplier, two courses, a carnet, an invoice,
five subscriptions, the authored payments (one attachment), course
subscriptions, installments, a carnet holding and an instructor-hours row with
its document (audit disabled). The routes are reads: every case must leave the
complete model state unchanged.
"""
from datetime import date, datetime
from decimal import Decimal

from TESTS.contracts import test_payment_reads_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/payment_reads_http.json', 'TESTS/contracts/test_payment_reads_http.py',
           'TESTS/adapters/django/payment_reads_http.py', 'BE/application/views/payment_views.py',
           'BE/application/views/subscriptions_views.py', 'BE/application/serializers/payment_serializers.py',
           'BE/application/serializers/personas_serializers.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/carnet_models.py',
           'BE/application/models/balance_sheet_models.py', 'BE/application/utils/subscriptions_utils.py',
           'BE/application/utils/api_utils.py', 'BE/application/utils/stripe_utils.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/permissions_registry.py', 'BE/application/urls.py',
           'BE/docmanager/models.py') + harness.SOURCES

ASSOCIATE_FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_city', 'address_city', 'address', 'address_cap',
                    'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label',
                    'phone_2_label', 'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role',
                    'id_number', 'migrated', 'notes')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def seed_rows(source, created):
    from application.models import (Associate, AssociateTutorRelation, Course, CourseSubscription, CourseSubscriptionInstallment,
                                    Instructor, InstructorHours, Invoice, Payment, PaymentCategory, Subscription,
                                    SupplierAndCustomers)
    from application.models.balance_sheet_models import CustomAccounts
    from application.models.carnet_models import Carnet, CarnetSubscription
    from docmanager.models import Document
    ids = source['ids']
    own, owner = ids['own_association'], ids['owner']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    documents = source['documents']
    for row in documents.values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], filepath=row['filepath'],
                                  file_size_bytes=row['size'], creation_date=moment(row['created'])))
    category = source['category']
    insert(PaymentCategory, PaymentCategory(payment_category_id=category['id'], name=category['name'], sport_association_id=own))
    accounts = source['accounts']
    for row in accounts.values():
        insert(CustomAccounts, CustomAccounts(custom_account_id=row['id'], name=row['name'], initial_balance=Decimal('0.00'),
                                              account_type=row['type'], account_code=row['code'], sport_association_id=own))
    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], user_id=row['user'], sport_association_id=row['association'],
                                    group_id=row['group'], family_id=row['family'], creation_date=moment(row['created']),
                                    born_date=date.fromisoformat(row['born_date']) if row['born_date'] else None,
                                    **{key: row[key] for key in ASSOCIATE_FIELDS}))
    for associate, tutors in source['tutors'].items():
        for tutor, primary in tutors:
            relation = AssociateTutorRelation._base_manager.create(associate_id=associate, tutor_id=tutor, is_primary=primary)
            created.append((AssociateTutorRelation, relation.pk))
    teacher = source['instructor']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=owner, first_name=teacher['first_name'],
                                  last_name=teacher['last_name'], email=teacher['email'], tax_code=teacher['tax_code'],
                                  phone=teacher['phone'], address=teacher['address'],
                                  default_hourly_billing=Decimal(teacher['hourly'])))
    vendor = source['supplier']
    insert(SupplierAndCustomers, SupplierAndCustomers(supplier_id=vendor['id'], name=vendor['name'], vat_number=vendor['vat_number'],
                                                      email=vendor['email'], address=vendor['address'], city=vendor['city'],
                                                      tax_code=vendor['tax_code'], sport_association_id=own))
    courses = source['courses']
    for row in courses.values():
        insert(Course, Course(course_id=row['id'], sport_association_id=own, course_type=1, title=row['title'], fee=Decimal('50.00'),
                              deleted=row['deleted']))
    carnet = source['carnet']
    insert(Carnet, Carnet(carnet_id=carnet['id'], title=carnet['title'], sport_association_id=own, fee=Decimal('1.00')))
    bill = source['invoice']
    insert(Invoice, Invoice(invoice_id=bill['id'], document_pdf_id=documents[bill['document']]['id'], sport_association_id=own,
                            creation_date=moment(bill['created']), membership_fee=Decimal(bill['membership_fee']),
                            activity_fee=Decimal(bill['activity_fee']), description=bill['description'], number=bill['number'],
                            meta=bill['meta']))
    through = Invoice.attached_documents.through
    through.objects.bulk_create([through(invoice_id=bill['id'], document_id=documents[bill['extra']]['id'])])
    payments = source['payments']
    for row in payments.values():
        insert(Payment, Payment(
            payment_id=row['id'], type=row['type'], subject=row['subject'], amount=Decimal(row['amount']),
            creation_date=moment(row['created']), payment_date=moment(row['paid_on']), paid=row['paid'], expense=row['expense'],
            description=row['description'], payment_category_id=category['id'] if row['category'] else None,
            meta_payment_categories=row['meta_payment_categories'], invoice_id=bill['id'] if row['invoice'] else None,
            user_id=owner if row['user'] else None, associate_id=associates[row['associate']]['id'] if row['associate'] else None,
            supplier_id=vendor['id'] if row['supplier'] else None, instructor_id=teacher['id'] if row['instructor'] else None,
            sport_association_id=row['association'], payment_intent_id=row['payment_intent_id'], archived=row['archived'],
            invoice_fiscal_code=row['invoice_fiscal_code'], meta=row['meta'], notes=row['notes'],
            custom_accounts_id=accounts[row['account']]['id'] if row['account'] else None, deleted=row['deleted'],
            group_id=row['group'], course=row['course']))
        if row['attachment']:
            link = Payment.attachments.through
            link.objects.bulk_create([link(payment_id=row['id'], document_id=documents[row['attachment']]['id'])])
    subscriptions = source['subscriptions']
    for row in subscriptions.values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=owner,
            associate_id=associates[row['associate']]['id'] if row['associate'] else None, creation_date=moment(row['created']),
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted'],
            status_flag=Subscription.ACCEPTED))
    course_subscriptions = source['course_subscriptions']
    for row in course_subscriptions.values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=courses[row['course']]['id'],
            subscription_id=subscriptions[row['subscription']]['id'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted']))
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=course_subscriptions[row['cs']]['id'],
            id=row['number'], amount=Decimal('40.00'), paid=True, payment_date=moment('2024-04-15T09:30:00Z'),
            payment_id=payments[row['payment']]['id'], deleted=row['deleted']))
    for row in source['holdings'].values():
        insert(CarnetSubscription, CarnetSubscription(
            carnet_subscription_id=row['id'], carnet_id_id=carnet['id'], subscription_id=subscriptions[row['subscription']]['id'],
            payment_id=payments[row['payment']]['id']))
    for row in source['hours'].values():
        insert(InstructorHours, InstructorHours(
            instructor_hours_id=row['id'], instructor_id=teacher['id'], hours=Decimal('2.00'),
            payment_id=payments[row['payment']]['id'], document_id=documents[row['document']]['id']))


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
