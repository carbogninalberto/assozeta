"""Private rows for the MCP server tool comparison (mcp_server_tools.py).

Two associations; every whitelisted model has rows in the first one plus
foreign, deleted, archived and disabled controls. Fixed ids and times.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

ASSOCIATION, SECOND = UUID(int=96101), UUID(int=96102)
OWNER, SECOND_OWNER, COLLABORATOR = UUID(int=96001), UUID(int=96002), UUID(int=96003)


def uid(n):
    return UUID(int=97000 + n)


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


IDS = {
    'anna': uid(1), 'marco': uid(2), 'luca': uid(3), 'gone': uid(4), 'foreign_associate': uid(5),
    'group': uid(10),
    'tag_agonista': uid(20), 'tag_under': uid(21), 'tag_foreign': uid(22), 'course_tag': uid(23),
    'medical': uid(30), 'medical_foreign': uid(31),
    'sub_anna': uid(40), 'sub_marco': uid(41), 'sub_archived': uid(42), 'sub_luca': uid(43), 'sub_foreign': uid(44),
    'nuoto': uid(50), 'bambini': uid(51), 'yoga': uid(52), 'deleted_course': uid(53), 'foreign_course': uid(54),
    'cs1': uid(60), 'cs2': uid(61), 'cs3': uid(62), 'cs4': uid(63), 'cs_deleted': uid(64), 'cs_archived': uid(65),
    'cat_quote': uid(70), 'cat_affitti': uid(71),
    'p1': uid(80), 'p2': uid(81), 'p3': uid(82), 'p_archived': uid(83), 'p_deleted': uid(84), 'p_foreign': uid(85),
    'invoice': uid(90), 'row1': uid(91), 'row2': uid(92),
    'instructor': uid(100), 'instructor_foreign': uid(101),
    'sheet': uid(110), 'account': uid(111),
    'carnet': uid(120), 'carnet_sub': uid(121), 'carnet_disabled': uid(122),
    'module': uid(130),
    'registry': uid(140), 'registry2': uid(141), 'day1': uid(142), 'day2': uid(143), 'day3': uid(144), 'day4': uid(145),
    'reminder': uid(150),
}


def seed():
    from django.contrib.auth import get_user_model
    from auditlog.context import disable_auditlog
    from django.apps import apps
    (SportAssociation, Associate, Group, Subscription, Course, CourseSubscription, Payment, PaymentCategory, Invoice, InvoiceRows,
     MedicalCertificate, Instructor, BalanceSheet, CustomAccounts, Tags, CourseTags, Carnet, CarnetSubscription, Module,
     AttendanceRegistry, AttendanceDay, Reminders) = (apps.get_model('application', name) for name in (
        'SportAssociation', 'Associate', 'Group', 'Subscription', 'Course', 'CourseSubscription', 'Payment', 'PaymentCategory',
        'Invoice', 'InvoiceRows', 'MedicalCertificate', 'Instructor', 'BalanceSheet', 'CustomAccounts', 'Tags', 'CourseTags',
        'Carnet', 'CarnetSubscription', 'Module', 'AttendanceRegistry', 'AttendanceDay', 'Reminders'))
    User = get_user_model()
    i = IDS
    base = lambda model: model._base_manager
    with disable_auditlog():
        base(User).bulk_create([
            User(user_id=OWNER, username='MCP-SERVER-OWNER', role=1, password='!private-fixture', first_name='Olga'),
            User(user_id=SECOND_OWNER, username='MCP-SERVER-SECOND', role=1, password='!private-fixture'),
            User(user_id=COLLABORATOR, username='MCP-SERVER-COLLAB', role=3, connected_user_id=OWNER, password='!private-fixture'),
        ])
        base(SportAssociation).bulk_create([
            SportAssociation(sport_association_id=ASSOCIATION, user_id=OWNER, denomination='ASD Onde &amp; Vele', tax_code='00000000001'),
            SportAssociation(sport_association_id=SECOND, user_id=SECOND_OWNER, denomination='ASD Seconda', tax_code='00000000002'),
        ])
        base(Group).bulk_create([Group(group_id=i['group'], sport_association_id=ASSOCIATION, name='Sede centrale')])
        base(Associate).bulk_create([
            Associate(associate_id=i['anna'], sport_association_id=ASSOCIATION, first_name='Anna', last_name='Rossi', sex='F',
                      born_date='1990-05-01', email='anna@example.test'),
            Associate(associate_id=i['marco'], sport_association_id=ASSOCIATION, first_name='Marco', last_name='Bianchi', sex='M',
                      born_date='1985-11-30'),
            Associate(associate_id=i['luca'], sport_association_id=ASSOCIATION, first_name='Luca', last_name="D&amp;Angelo", sex='A'),
            Associate(associate_id=i['gone'], sport_association_id=ASSOCIATION, first_name='Gino', last_name='Cancellato', deleted=True),
            Associate(associate_id=i['foreign_associate'], sport_association_id=SECOND, first_name='Altra', last_name='Persona'),
        ])
        base(Tags).bulk_create([
            Tags(tag_id=i['tag_agonista'], tag_name='Agonista', sport_association_id=ASSOCIATION),
            Tags(tag_id=i['tag_under'], tag_name='Under_14', sport_association_id=ASSOCIATION),
            Tags(tag_id=i['tag_foreign'], tag_name='Agonista', sport_association_id=SECOND),
        ])
        base(CourseTags).bulk_create([CourseTags(tag_id=i['course_tag'], tag_name='Acqua', sport_association_id=ASSOCIATION)])
        base(MedicalCertificate).bulk_create([
            MedicalCertificate(medical_id=i['medical'], expiration_date='2026-01-31', competitive_medical_certificate=True,
                               notes='Visita annuale'),
            MedicalCertificate(medical_id=i['medical_foreign'], expiration_date='2025-06-30'),
        ])
        base(Subscription).bulk_create([
            Subscription(subscription_id=i['sub_anna'], sport_association_id=ASSOCIATION, user_id=OWNER, associate_id=i['anna'],
                         status_flag=1, medical_id=i['medical'], start_date='2025-01-01', end_date='2025-12-31', subscription_number=1),
            Subscription(subscription_id=i['sub_marco'], sport_association_id=ASSOCIATION, user_id=OWNER, associate_id=i['marco'],
                         status_flag=2, start_date='2025-09-01', end_date='2026-08-31', subscription_number=2),
            Subscription(subscription_id=i['sub_archived'], sport_association_id=ASSOCIATION, user_id=OWNER, associate_id=i['anna'],
                         status_flag=1, archived=True, start_date='2024-01-01', end_date='2024-12-31', subscription_number=3),
            Subscription(subscription_id=i['sub_luca'], sport_association_id=ASSOCIATION, user_id=OWNER, associate_id=i['luca'],
                         status_flag=1, start_date='2025-02-01', end_date='2025-12-31', subscription_number=4),
            Subscription(subscription_id=i['sub_foreign'], sport_association_id=SECOND, user_id=SECOND_OWNER,
                         associate_id=i['foreign_associate'], status_flag=1, medical_id=i['medical_foreign'], subscription_number=1),
        ])
        Subscription.tags.through.objects.bulk_create([
            Subscription.tags.through(subscription_id=i['sub_anna'], tags_id=i['tag_agonista']),
            Subscription.tags.through(subscription_id=i['sub_anna'], tags_id=i['tag_under']),
            Subscription.tags.through(subscription_id=i['sub_marco'], tags_id=i['tag_agonista']),
        ])
        base(Course).bulk_create([
            Course(course_id=i['nuoto'], sport_association_id=ASSOCIATION, title='Nuoto Adulti', fee=Decimal('45.50'), status_flag=2,
                   start_date=at('2025-09-01T08:00:00'), course_type=1),
            Course(course_id=i['bambini'], sport_association_id=ASSOCIATION, title='Nuoto_Bambini', fee=Decimal('30.00'), status_flag=2,
                   course_type=2),
            Course(course_id=i['yoga'], sport_association_id=ASSOCIATION, title='Yoga &amp; Pilates', fee=Decimal('12.25'), status_flag=1,
                   course_type=3, description='Corso serale'),
            Course(course_id=i['deleted_course'], sport_association_id=ASSOCIATION, title='Nuoto Vecchio', deleted=True),
            Course(course_id=i['foreign_course'], sport_association_id=SECOND, title='Nuoto Altrove', fee=Decimal('99.00')),
        ])
        Course.tags.through.objects.bulk_create([Course.tags.through(course_id=i['nuoto'], coursetags_id=i['course_tag'])])
        base(CourseSubscription).bulk_create([
            CourseSubscription(course_subscription_id=i['cs1'], course_id=i['nuoto'], subscription_id=i['sub_anna'], paid=True, type=1),
            CourseSubscription(course_subscription_id=i['cs2'], course_id=i['nuoto'], subscription_id=i['sub_marco'], type=2),
            CourseSubscription(course_subscription_id=i['cs3'], course_id=i['bambini'], subscription_id=i['sub_anna'], paid=True, type=1),
            CourseSubscription(course_subscription_id=i['cs4'], course_id=i['yoga'], subscription_id=i['sub_luca'], type=3),
            CourseSubscription(course_subscription_id=i['cs_deleted'], course_id=i['nuoto'], subscription_id=i['sub_luca'], deleted=True),
            CourseSubscription(course_subscription_id=i['cs_archived'], course_id=i['yoga'], subscription_id=i['sub_anna'], archived=True),
        ])
        base(PaymentCategory).bulk_create([
            PaymentCategory(payment_category_id=i['cat_quote'], name='Quote', type=1, sport_association_id=ASSOCIATION),
            PaymentCategory(payment_category_id=i['cat_affitti'], name='Affitti', type=2, archived=True, sport_association_id=ASSOCIATION),
        ])
        base(Payment).bulk_create([
            Payment(payment_id=i['p1'], sport_association_id=ASSOCIATION, associate_id=i['anna'], amount=Decimal('45.50'), type='cash',
                    payment_date=at('2025-03-04T10:00:00'), payment_category_id=i['cat_quote'], description='Quota &amp; tessera',
                    meta={'b': 1, 'a': [1.0, 'x &amp; y'], 'note': 'ok'}),
            Payment(payment_id=i['p2'], sport_association_id=ASSOCIATION, associate_id=i['marco'], amount=Decimal('30.00'),
                    type='transfer', payment_date=datetime(2025, 7, 4, 23, 30, 0, 500000, tzinfo=timezone.utc),
                    payment_category_id=i['cat_quote']),
            Payment(payment_id=i['p3'], sport_association_id=ASSOCIATION, amount=Decimal('12.35'), type='pos', expense=True,
                    payment_date=at('2024-12-31T23:59:59'), description='Piscina'),
            Payment(payment_id=i['p_archived'], sport_association_id=ASSOCIATION, associate_id=i['anna'], amount=Decimal('5.00'),
                    archived=True, payment_date=at('2025-01-15T09:00:00')),
            Payment(payment_id=i['p_deleted'], sport_association_id=ASSOCIATION, amount=Decimal('7.00'), deleted=True,
                    payment_date=at('2025-01-16T09:00:00')),
            Payment(payment_id=i['p_foreign'], sport_association_id=SECOND, amount=Decimal('99.99'), payment_date=at('2025-03-04T10:00:00')),
        ])
        base(Invoice).bulk_create([Invoice(invoice_id=i['invoice'], sport_association_id=ASSOCIATION, membership_fee=Decimal('10.00'),
                                           activity_fee=Decimal('35.50'))])
        base(InvoiceRows).bulk_create([
            InvoiceRows(invoice_row_id=i['row1'], invoice_id=i['invoice'], unit_price=Decimal('10.00'), vat=Decimal('0.00'),
                        description='Quota associativa'),
            InvoiceRows(invoice_row_id=i['row2'], invoice_id=i['invoice'], unit_price=Decimal('35.50'), vat=Decimal('22.00'),
                        description='Corso'),
        ])
        base(Instructor).bulk_create([
            Instructor(instructor_id=i['instructor'], user_id=OWNER, first_name='Ivo', last_name='Istruttore', born_date='1980-02-29'),
            Instructor(instructor_id=i['instructor_foreign'], user_id=SECOND_OWNER, first_name='Ugo', last_name='Esterno'),
        ])
        base(BalanceSheet).bulk_create([BalanceSheet(balance_sheet_id=i['sheet'], sport_association_id=ASSOCIATION, status_flag=1)])
        base(CustomAccounts).bulk_create([CustomAccounts(custom_account_id=i['account'], sport_association_id=ASSOCIATION, name='Cassa',
                                                         initial_balance=Decimal('100.00'), account_code='C01')])
        base(Carnet).bulk_create([Carnet(carnet_id=i['carnet'], sport_association_id=ASSOCIATION, title='Carnet 10', lessons_number=10,
                                         fee=Decimal('80.00'), creation_date=at('2025-01-10T12:00:00'))])
        base(CarnetSubscription).bulk_create([
            CarnetSubscription(carnet_subscription_id=i['carnet_sub'], carnet_id_id=i['carnet'], subscription_id=i['sub_anna'],
                               meta={'lessons_left': 3}, creation_date=at('2025-02-01T12:00:00')),
            CarnetSubscription(carnet_subscription_id=i['carnet_disabled'], carnet_id_id=i['carnet'], disabled=True,
                               meta={'lessons_left': 0}, creation_date=at('2025-02-02T12:00:00')),
        ])
        base(Module).bulk_create([Module(module_id=i['module'], sport_association_id=ASSOCIATION, title='Iscrizione')])
        base(AttendanceRegistry).bulk_create([
            AttendanceRegistry(attendance_registry_id=i['registry'], course_id=i['nuoto'], status=2),
            AttendanceRegistry(attendance_registry_id=i['registry2'], course_id=i['yoga'], status=1),
        ])
        base(AttendanceDay).bulk_create([
            AttendanceDay(attendance_day_id=i['day1'], attendance_registry_id=i['registry'], date=at('2025-03-03T18:00:00'),
                          title='Lezione 1', attendees=[{'course_subscription_id': str(i['cs1'])}]),
            AttendanceDay(attendance_day_id=i['day2'], attendance_registry_id=i['registry'], date=at('2025-03-10T18:00:00'),
                          title='Lezione 2', attendees=[{'course_subscription_id': str(i['cs1'])}, {'course_subscription_id': str(i['cs2'])}]),
            AttendanceDay(attendance_day_id=i['day3'], attendance_registry_id=i['registry'], date=at('2025-04-01T23:30:00'),
                          title='Lezione 3', attendees=[]),
            AttendanceDay(attendance_day_id=i['day4'], attendance_registry_id=i['registry2'], date=at('2025-05-05T07:00:00'),
                          title='Yoga 1', attendees=[str(i['cs4'])]),
        ])
        base(Reminders).bulk_create([Reminders(reminders_id=i['reminder'], sport_association_id=ASSOCIATION, user_id=OWNER,
                                               event_title='Rinnovo certificati', send_at=at('2025-10-01T08:00:00'))])
        fixed = at('2025-01-01T09:00:00')
        for model in (Associate, Subscription, Course, CourseSubscription, PaymentCategory, Payment, Invoice, MedicalCertificate,
                      Tags, CourseTags, Module, BalanceSheet, CustomAccounts, Reminders, Group):
            names = {f.name for f in model._meta.concrete_fields}
            values = {name: fixed for name in ('creation_date', 'created_at', 'updated_at', 'last_update') if name in names}
            if values:
                base(model).update(**values)
