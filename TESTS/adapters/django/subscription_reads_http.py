"""Parent-only subscription list, list/all, list/archived, info and calendar HTTP runner; imports are inert.

Seeds documents, medical certificates, tags, associates (one tutor relation),
payments, subscriptions with their tags, courses, course subscriptions,
installments, attendance registries, a subscription file and a membership card
configuration (audit disabled). The routes are reads: every case must leave
the complete model state unchanged, and publish exactly its authored Celery
tasks (subscription_info's print_document_subscription when the subscription
has no document_pdf) to the run's broker.
"""
from datetime import date, datetime
from decimal import Decimal
import time

from TESTS.contracts import test_subscription_reads_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/subscription_reads_http.json', 'TESTS/contracts/test_subscription_reads_http.py',
           'TESTS/adapters/django/subscription_reads_http.py', 'BE/application/views/subscriptions_views.py',
           'BE/application/views/attendee_views.py', 'BE/application/serializers/subscriptions_serializers.py',
           'BE/application/serializers/courses_serializers.py', 'BE/application/serializers/personas_serializers.py',
           'BE/application/serializers/user_serializers.py', 'BE/application/serializers/auth_serializers.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/user_models.py',
           'BE/application/models/payment_models.py', 'BE/application/models/courses_models.py',
           'BE/application/models/attendee_models.py', 'BE/application/utils/subscriptions_utils.py',
           'BE/application/utils/api_utils.py', 'BE/application/utils/printing.py', 'BE/application/mixin.py',
           'BE/application/impersonation_scope.py', 'BE/application/permissions.py', 'BE/application/permissions_registry.py',
           'BE/application/urls.py', 'BE/docmanager/models.py', 'BE/application/models/carnet_models.py', 'BE/application/tasks.py',
           'BE/core/celery.py') + harness.SOURCES

ASSOCIATE_FIELDS = ('picture_path', 'first_name', 'last_name', 'sex', 'tax_code', 'born_city', 'address_city', 'address', 'address_cap',
                    'nationality', 'nationality_residence', 'email', 'phone', 'phone_2', 'phone_3', 'phone_4', 'phone_label',
                    'phone_2_label', 'phone_3_label', 'phone_4_label', 'is_minor', 'disabled', 'draft', 'deleted', 'family_role',
                    'id_number', 'migrated', 'notes')


def moment(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00')) if text else None


def day(text):
    return date.fromisoformat(text) if text else None


def seed_rows(source, created):
    from application.models import (AttendanceRegistry, Associate, AssociateTutorRelation, Course, CourseSubscription,
                                    CourseSubscriptionInstallment, MedicalCertificate, Payment, Subscription, Tags)
    from application.models.subscriptions_models import SubscriptionFile
    from application.models.user_models import SportAssociationMembershipCardConfiguration
    from docmanager.models import Document

    def insert(model, row):
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
        return row

    documents = source['documents']
    for row in documents.values():
        insert(Document, Document(document_id=row['id'], token=row['token'], filename=row['filename'], filepath=row['filepath'],
                                  file_size_bytes=row['size'], creation_date=moment(row['created'])))
    medicals = source['medicals']
    for row in medicals.values():
        insert(MedicalCertificate, MedicalCertificate(
            medical_id=row['id'], expiration_date=day(row['expiration']), competitive_medical_certificate=row['competitive'],
            document_id=documents[row['document']]['id'] if row['document'] else None))
    tags = source['tags']
    for row in tags.values():
        insert(Tags, Tags(tag_id=row['id'], tag_name=row['name'], sport_association_id=row['association']))
    associates = source['associates']
    for row in associates.values():
        insert(Associate, Associate(associate_id=row['id'], user_id=row['user'], sport_association_id=row['association'],
                                    group_id=row['group'], family_id=row['family'], creation_date=moment(row['created']),
                                    born_date=day(row['born_date']), **{key: row[key] for key in ASSOCIATE_FIELDS}))
    for associate, tutors in source['tutors'].items():
        for tutor, primary in tutors:
            relation = AssociateTutorRelation._base_manager.create(associate_id=associates[associate]['id'],
                                                                   tutor_id=associates[tutor]['id'], is_primary=primary)
            created.append((AssociateTutorRelation, relation.pk))
    from application.models import Instructor, Invoice
    teacher, bill = source['instructor'], source['invoice']
    insert(Instructor, Instructor(instructor_id=teacher['id'], user_id=source['ids']['owner'], first_name=teacher['first_name'],
                                  last_name=teacher['last_name'], email=teacher['email'], tax_code=teacher['tax_code'],
                                  default_hourly_billing=Decimal(teacher['default_hourly_billing'])))
    insert(Invoice, Invoice(invoice_id=bill['id'], sport_association_id=source['ids']['own_association'], creation_date=moment(bill['created']),
                            membership_fee=Decimal(bill['membership_fee']), activity_fee=Decimal(bill['activity_fee']),
                            description=bill['description'], number=bill['number'], meta=bill['meta']))
    payments = source['payments']
    for row in payments.values():
        extra = {}
        if 'subject' in row:  # membership payments
            extra = {'subject': row['subject'], 'meta': row['meta'], 'description': row['description'],
                     'invoice_id': bill['id'] if row['invoice'] else None, 'instructor_id': teacher['id'] if row['instructor'] else None}
        insert(Payment, Payment(**extra, payment_id=row['id'], sport_association_id=row['association'], amount=Decimal(row['amount']),
                                paid=row['paid'], archived=row['archived'], deleted=row['deleted'], group_id=row['group'],
                                associate_id=associates[row['associate']]['id'] if row['associate'] else None,
                                creation_date=moment(row['created']), payment_date=moment(row['paid_on'])))
    subscriptions = source['subscriptions']
    link = Subscription.tags.through
    for row in subscriptions.values():
        insert(Subscription, Subscription(
            subscription_id=row['id'], sport_association_id=row['association'], user_id=row['user'],
            associate_id=associates[row['associate']]['id'] if row['associate'] else None, creation_date=moment(row['created']),
            start_date=day(row['start']), end_date=day(row['end']), status_flag=row['status'], type=row['type'], role=row['role'],
            payment_id=payments[row['payment']]['id'] if row['payment'] else None,
            medical_id=medicals[row['medical']]['id'] if row['medical'] else None,
            document_pdf_id=documents[row['document']]['id'] if row['document'] else None, custom_data=row['custom_data'],
            additional_fields=row['additional_fields'], meta=row['meta'], signature_url=row['signature_url'],
            signature_storage_key=row['signature_storage_key'], subscription_number=row['number'],
            subscription_type=row['subscription_type'], competitive=row['competitive'], notes=row['notes'],
            acceptance_date=day(row['acceptance_date']), resignation_date=day(row['resignation_date']), draft=row['draft'],
            trial=row['trial'], archived=row['archived'], deleted=row['deleted'], group_id=row['group']))
        link.objects.bulk_create([link(subscription_id=row['id'], tags_id=tags[t]['id']) for t in row['tags']])
    courses = source['courses']
    for row in courses.values():
        insert(Course, Course(course_id=row['id'], sport_association_id=source['ids']['own_association'], course_type=1, title=row['title'],
                              creation_date=moment(row['created']), multi_payments=row['multi'], fee=Decimal(row['fee']),
                              deleted=row['deleted'], events=row['events'], google_background_color='#3a87ad'))
    course_subscriptions = source['course_subscriptions']
    for row in course_subscriptions.values():
        insert(CourseSubscription, CourseSubscription(
            course_subscription_id=row['id'], course_id=courses[row['course']]['id'],
            subscription_id=subscriptions[row['subscription']]['id'] if row['subscription'] else None,
            paid=row['paid'], multi_payments=row['multi'], creation_date=moment(row['created']), deleted=row['deleted']))
        link_model = CourseSubscription.membership_payments.through
        link_model.objects.bulk_create([link_model(coursesubscription_id=row['id'], payment_id=payments[label]['id'])
                                        for label in row.get('membership', [])])
    for row in source['installments'].values():
        insert(CourseSubscriptionInstallment, CourseSubscriptionInstallment(
            course_subscription_installment_id=row['id'], course_subscription_id=course_subscriptions[row['cs']]['id'], id=row['number'],
            amount=Decimal(row['amount']), paid=row['paid'], payment_date=moment(row['when']),
            payment_id=payments[row['payment']]['id'] if row['payment'] else None, deleted=row['deleted'], group_id=row['group']))
    for row in source['registries'].values():
        insert(AttendanceRegistry, AttendanceRegistry(attendance_registry_id=row['id'], course_id=courses[row['course']]['id'],
                                                      events=row['events'], status=2))
    for row in source['files'].values():
        insert(SubscriptionFile, SubscriptionFile(subscription_file_id=row['id'], subscription_id=subscriptions[row['subscription']]['id'],
                                                  document_id=documents[row['document']]['id'], creation_date=moment(row['created'])))
    from application.models.carnet_models import CarnetSubscription
    for row in source['carnets'].values():
        insert(CarnetSubscription, CarnetSubscription(carnet_subscription_id=row['id'],
                                                      subscription_id=subscriptions[row['subscription']]['id']))
    card = source['card']
    insert(SportAssociationMembershipCardConfiguration, SportAssociationMembershipCardConfiguration(
        sport_association_membership_card_configuration_id=card['id'], sport_association_id=card['association'],
        emit_only_on_approval=card['emit_only_on_approval'], customized_template=card['customized_template']))


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))


def assert_effect(case, before, after, source, context):
    contract.require(before == after, 'state.unchanged')
    token = context['fixture']['tokens'].get(case['actor'])
    binding = {'$authorization': f'Bearer {token}' if token else None}
    expected = [{**task, 'args': [binding.get(item, item) if isinstance(item, str) else item for item in task['args']]}
                for task in case.get('celery', [])]
    harness.celery_published(contract, context['fixture'], expected, since=context['prepared'], until=time.time())


run_workload = harness.run_workload_for(contract, assert_effect, prepare=lambda source: time.time())
