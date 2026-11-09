"""Parent-only export-all-data HTTP runner; imports are inert.

Seeds a family, associates (one tutor live, one soft-deleted), tutor
relations, medical certificates, subscriptions (deleted, archived and
out-of-range choice values included), a payment category, an account, a
supplier, an instructor, an invoice, courses with course subscriptions and an
installment, and payments (audit disabled). The view only reads: every case
must leave the complete model state unchanged.
"""
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from TESTS.contracts import test_export_all_data_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/export_all_data_http.json', 'TESTS/contracts/test_export_all_data_http.py', 'TESTS/contracts/xlsx.py',
           'TESTS/adapters/django/export_all_data_http.py', 'BE/application/views/profile_views.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/invoices_models.py', 'BE/application/models/balance_sheet_models.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/permissions_registry.py', 'BE/application/urls.py') + harness.SOURCES


def decode(value):
    if isinstance(value, dict) and len(value) == 1:
        (kind, raw), = value.items()
        if kind == 'datetime':
            return datetime.fromisoformat(raw)
        if kind == 'date':
            return date.fromisoformat(raw)
        if kind == 'decimal':
            return Decimal(raw)
        if kind == 'uuid':
            return UUID(raw)
        if kind == '$json':
            return raw
    return value


def seed_rows(source, created):
    from application.models import Associate, AssociateTutorRelation, CourseSubscription, Invoice, MedicalCertificate, Payment, Subscription
    from application.models.balance_sheet_models import CustomAccounts
    from application.models.courses_models import Course, CourseSubscriptionInstallment
    from application.models.payment_models import PaymentCategory, SupplierAndCustomers
    from application.models.user_models import Family, Instructor
    ids = source['ids']
    own = ids['own_association']

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    def fields(row, skip=(), **related):
        """Plain field values of an authored row; related maps a field name to its FK id (or None)."""
        out = {}
        for name, value in row.items():
            if name in skip:
                continue
            if name in related:
                out[name + '_id'] = related[name]
            elif isinstance(value, dict) and '$object' in value:
                continue
            else:
                out[name] = decode(value)
        return out

    def group(value):
        return ids['own_group'] if value else None

    insert(Family, Family(family_id=source['family']))
    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(**fields(row, user=None, sport_association=row['sport_association'], group=group(row['group']),
                                             family=source['family'] if row['family'] else None)))
    for child, tutor, primary in source['relations']:
        relation = AssociateTutorRelation._base_manager.create(associate_id=associates[child]['associate_id'],
                                                               tutor_id=associates[tutor]['associate_id'], is_primary=primary)
        created.append((AssociateTutorRelation, relation.pk))
    certificates = source['certificates']
    for row in certificates.values():
        insert(MedicalCertificate, MedicalCertificate(**fields(row)))
    subscriptions = source['subscriptions']
    for row in subscriptions.values():
        owner = ids['owner'] if row['sport_association'] == own else ids['foreign_owner']
        insert(Subscription, Subscription(**fields(
            row, sport_association=row['sport_association'], associate=associates[row['associate']]['associate_id'], supplier=None,
            instructor=None, user=owner, medical=certificates[row['medical']]['medical_id'] if row['medical'] else None,
            document_pdf=None, payment=None, group=group(row['group']))))
    category = insert(PaymentCategory, PaymentCategory(**fields(source['category'], sport_association=own, vat_management=None)))
    account = insert(CustomAccounts, CustomAccounts(**fields(source['account'], sport_association=own)))
    supplier = insert(SupplierAndCustomers, SupplierAndCustomers(**fields(source['supplier'], sport_association=own, group=None)))
    instructor = insert(Instructor, Instructor(**fields(source['instructor'], user=ids['owner'])))
    invoice_row = source['invoice']
    invoice = insert(Invoice, Invoice(**fields(invoice_row, document_pdf=None, sport_association=own,
                                               selected_tutor=associates['tutor']['associate_id'], group=group(invoice_row['group']))))
    courses = source['courses']
    for row in courses.values():
        insert(Course, Course(course_id=row['id'], sport_association_id=own, title=row['title']))
    related = {'category': category.pk, 'invoice': invoice.pk, 'supplier': supplier.pk, 'instructor': instructor.pk,
               'account': account.pk}
    serial = iter(range(900, 999))
    for row in source['payments'].values():
        payment = insert(Payment, Payment(**fields(
            row, skip=('links',), payment_category=related['category'] if row['payment_category'] else None,
            invoice=related['invoice'] if row['invoice'] else None, user=None,
            associate=associates[row['associate']]['associate_id'] if row['associate'] else None,
            supplier=related['supplier'] if row['supplier'] else None, instructor=related['instructor'] if row['instructor'] else None,
            sport_association=row['sport_association'], custom_accounts=related['account'] if row['custom_accounts'] else None,
            signature=None, group=group(row['group']))))
        for kind, course, deleted, subscription in row['links']:
            key = f'a1000000-0000-4000-8000-{2720000 + next(serial):012d}'
            parent = insert(CourseSubscription, CourseSubscription(
                course_subscription_id=key, course_id=courses[course]['id'], payment=payment if kind == 'course' else None,
                deleted=deleted, subscription_id=subscriptions[subscription]['subscription_id'] if subscription else None))
            if kind == 'installment':
                insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
                    course_subscription_installment_id=f'a1000000-0000-4000-8000-{2720000 + next(serial):012d}',
                    course_subscription=parent, payment=payment, payment_date=payment.creation_date))


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
