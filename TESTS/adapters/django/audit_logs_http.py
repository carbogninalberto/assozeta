"""Parent-only audit-logs list/detail HTTP runner; imports are inert.

Seeds the rows the AuditLogEntrySerializer resolvers read (users, courses,
associates, subscriptions, payments, attendance, camps, carnets, messages, ...)
and the LogEntry rows with their AuditLogIndex rows (explicit ids, so the
LogEntry sequence is untouched), all with auditlog disabled. The routes are
reads: every case must leave the complete model state unchanged.
"""
from datetime import datetime
from decimal import Decimal

from TESTS.contracts import test_audit_logs_http as contract
from TESTS.adapters.django import family_harness as harness

SOURCES = ('TESTS/fixtures/audit_logs_http.json', 'TESTS/contracts/test_audit_logs_http.py',
           'TESTS/adapters/django/audit_logs_http.py', 'BE/application/views/audit_views.py',
           'BE/application/serializers/audit_serializers.py', 'BE/application/models/audit_models.py',
           'BE/application/utils/api_utils.py', 'BE/application/mixin.py', 'BE/application/models/courses_models.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/attendee_models.py',
           'BE/application/models/carnet_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/modules_models.py', 'BE/communications/models.py') + harness.SOURCES


def moment(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def seed_rows(source, created):
    from auditlog.models import LogEntry
    from django.contrib.contenttypes.models import ContentType
    from application.models import (Associate, Course, CourseSubscription, Family, Group, Instructor, Invoice, Module, Payment,
                                    Subscription, Tags, User)
    from application.models.attendee_models import AttendanceDay, AttendanceRegistry, GlobalCalendarEvents, Reminders
    from application.models.audit_models import AuditLogIndex
    from application.models.carnet_models import Carnet, CarnetSubscription
    from application.models.courses_models import CampsAndRetreats, CampsAndRetreatsPeriod, CampsAndRetreatsSubscription
    from application.models.subscriptions_models import MedicalCertificate, SubscriptionMembership
    from communications.models import AutomationWorkflow, CommunicationConfiguration, Message
    rows, ids = source['rows'], source['ids']
    R, own, owner = rows['ids'], rows['own'], ids['owner']

    def put(model, **values):
        row = model(**values)
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))

    for label, user in rows['users'].items():
        put(User, user_id=R[label], username=f'audit-logs-{label}', password='!synthetic', role=User.ATHLETE, **user)
    for label, title, deleted, group in (('course', 'Nuoto è', False, None), ('course_grouped', 'Gruppo corso', False, rows['own_group']),
                                         ('course_deleted', 'Vecchio', True, None), ('course_untitled', None, False, None)):
        put(Course, course_id=R[label], sport_association_id=own, title=title, deleted=deleted, group_id=group, fee=Decimal('10.00'),
            status_flag=Course.ACTIVE)
    put(Family, family_id=R['family'])
    put(Tags, tag_id=R['tag'], tag_name='Tag', sport_association_id=own)
    put(Associate, associate_id=R['associate'], sport_association_id=own, first_name='Luca', last_name='Bianchi')
    put(Associate, associate_id=R['associate_unnamed'], sport_association_id=own, first_name=None, last_name='Verdi')
    put(Subscription, subscription_id=R['subscription'], sport_association_id=own, user_id=owner, associate_id=R['associate'],
        subscription_type=None)
    put(Subscription, subscription_id=R['subscription_bare'], sport_association_id=own, user_id=owner, associate_id=None)
    for label, subscription, deleted in (('course_subscription', 'subscription', False), ('course_subscription_bare', None, False),
                                         ('course_subscription_deleted', 'subscription', True)):
        put(CourseSubscription, course_subscription_id=R[label], course_id=R['course'],
            subscription_id=R[subscription] if subscription else None, deleted=deleted)
    put(Payment, payment_id=R['payment'], sport_association_id=own, user_id=owner, subject=Payment.COURSE, amount=Decimal('12.50'),
        description=None)
    put(Instructor, instructor_id=R['instructor'], user_id=owner, first_name='Anna', last_name='Gialli')
    put(Carnet, carnet_id=R['carnet'], sport_association_id=own, title='Carnet 10', description=None, lessons_number=10,
        fee=Decimal('5.00'), public=False, meta={})
    put(CarnetSubscription, carnet_subscription_id=R['carnet_subscription'], carnet_id_id=R['carnet'], user_id_id=owner,
        subscription_id=R['subscription'], disabled=False, meta={})
    put(Module, module_id=R['module'], sport_association_id=own, title='Modulo privacy')
    put(AttendanceRegistry, attendance_registry_id=R['registry'], course_id=R['course'], events=rows['events_registry'])
    put(AttendanceDay, attendance_day_id=R['day'], attendance_registry_id=R['registry'], title=None, date=moment('2026-09-15T18:30:00Z'))
    put(GlobalCalendarEvents, global_calendar_id=R['calendar'], sport_association_id=own, events=rows['events_calendar'])
    put(Reminders, reminders_id=R['reminder'], event_title='Promemoria gara', send_at=moment('2026-10-02T07:00:00Z'), user_id=owner,
        sport_association_id=own)
    put(CampsAndRetreats, camps_and_retreats_id=R['camp'], title='Campo estivo', sport_association_id=own)
    put(CampsAndRetreatsPeriod, camps_and_retreats_period_id=R['period'], camps_and_retreats_id=R['camp'],
        start_date=moment('2026-07-01T08:00:00Z'), end_date=moment('2026-07-10T08:00:00Z'))
    put(CampsAndRetreatsSubscription, camps_and_retreats_subscription_id=R['camp_subscription'], camps_and_retreats_id=R['camp'],
        subscription_id=R['subscription'])
    put(SubscriptionMembership, subscription_membership_id=R['membership'], subscription_id=R['subscription'], sport_association_id=own)
    put(MedicalCertificate, medical_id=R['medical'], user_id=owner)
    put(Invoice, invoice_id=R['invoice'], sport_association_id=own, membership_fee=Decimal('0.00'), activity_fee=Decimal('0.00'))
    put(Message, message_id=R['message'], sport_association_id=own, type='EMAIL', message='Testo', subject='Avviso')
    put(Message, message_id=R['message_bare'], sport_association_id=own, type='SMS', message='Testo', subject=None)
    put(AutomationWorkflow, automation_workflow_id=R['workflow'], sport_association_id=own, name='Flusso benvenuto')
    put(CommunicationConfiguration, communication_configuration_id=R['configuration'], sport_association_id=own)
    contract.require(Group._base_manager.filter(pk=rows['own_group'], name='Scope own group').exists(), 'fixture.group')

    for entry in source['entries'].values():
        content_type = ContentType.objects.filter(app_label=entry['app'], model=entry['model']).first()
        if content_type is None:
            content_type = ContentType.objects.create(app_label=entry['app'], model=entry['model'])
            created.append((ContentType, content_type.pk))
        put(LogEntry, id=entry['id'], content_type=content_type, object_pk=entry['pk'], object_repr=entry['repr'], action=entry['action'],
            changes=entry['changes'], changes_text='', actor_id=R[entry['actor']] if entry['actor'] else None,
            actor_email=entry['actor_email'], remote_addr=entry['remote'], timestamp=moment(entry['timestamp']),
            additional_data=entry['extra'])
        if entry['indexed']:
            index = AuditLogIndex(log_entry_id=entry['id'], sport_association_id=entry['association'], resolution_path='direct')
            AuditLogIndex._base_manager.bulk_create([index])
            created.append((AuditLogIndex, index.pk))


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract)
