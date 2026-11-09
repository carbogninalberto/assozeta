"""AuditLogIndex resolution and backfill_audit_log_index: Django versus Go.

Private PostgreSQL with the current Django schema and the registered audit
resolvers. Part one creates LogEntry records through django-auditlog's
LogEntry manager for every audited content type (and stale or unaudited
ones), with and without the request's X-Group-Id context, so Django's
post_save handler writes (or skips) the index row. Every case states the
authored association and resolution path; Django must produce it. Right
after each Django write, the Go driver removes that index row inside a
transaction, runs repository.IndexAuditLogEntry with the same group header,
reports the row it wrote and rolls back; the row must be identical.

Part two seeds the resulting audit log (most entries without index rows,
some with) and runs the actual ``manage.py backfill_audit_log_index`` process
and the race-built Go command with the same arguments on the same reset
state: exit status, stdout and the index table (ids and creation times
checked against the run window, then normalized) must be identical.
"""
import hashlib
import json
import os
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema

DATABASE = 'assozeta_contract_audit_index'
U = lambda n: UUID(int=n)  # noqa: E731
# Associations: A (cases), B (actor), X (changes), D deleted, E owned by a
# soft-deleted user, F (filler for required relations), N1 = UUID(int=1).
A, B, X, D, E, F, N777, N1 = U(97101), U(97102), U(97103), U(97104), U(97105), U(97106), U(777), U(1)
OWNER_A, OWNER_B, OWNER_X, OWNER_D, OWNER_E, OWNER_F, OWNER_777, OWNER_1, LONER = (U(97001 + i) for i in range(9))
G1, G2 = U(97201), U(0x9a02)
LABELS = {str(A): 'A', str(B): 'B', str(X): 'X', str(D): 'D', str(E): 'E', str(F): 'F', str(N777): 'N777', str(N1): 'N1'}
DJANGO_COMMAND = f'''
import sys
sys.path[:0] = [{str(ROOT / 'BE')!r}, {str(ROOT)!r}]
from unittest.mock import patch
with patch('environ.Env.read_env'):
    import django
    django.setup()
from django.core.management import execute_from_command_line
execute_from_command_line(['manage.py', 'backfill_audit_log_index', *sys.argv[1:]])
'''


class Factory:
    """Rows for any model: required fields get type-appropriate values."""

    def __init__(self):
        self.counter = 0

    def value(self, field):
        from django.db import models
        from django.contrib.postgres.fields import ArrayField
        self.counter += 1
        n = self.counter
        if isinstance(field, ArrayField):
            return []
        if field.choices:
            return field.choices[0][0]
        if isinstance(field, models.EmailField):
            return f'u{n}@example.test'
        if isinstance(field, (models.CharField, models.TextField, models.SlugField)):
            text = f'v{n}'
            return text[-field.max_length:] if field.max_length else text
        if isinstance(field, models.BooleanField):
            return False
        if isinstance(field, (models.IntegerField, models.BigIntegerField, models.SmallIntegerField,
                              models.PositiveIntegerField, models.PositiveSmallIntegerField)):
            return 0
        if isinstance(field, models.DecimalField):
            return Decimal('0')
        if isinstance(field, models.FloatField):
            return 0.0
        if isinstance(field, models.DateTimeField):
            return datetime(2026, 1, 2, 9, 0, tzinfo=timezone.utc)
        if isinstance(field, models.DateField):
            return date(2026, 1, 2)
        if isinstance(field, models.TimeField):
            return time(9, 0)
        if isinstance(field, models.DurationField):
            return timedelta(0)
        if isinstance(field, models.UUIDField):
            return U(10_000_000 + n)
        if isinstance(field, models.JSONField):
            return {}
        if isinstance(field, models.BinaryField):
            return b''
        if isinstance(field, models.FileField):
            return f'files/f{n}.txt'
        raise TypeError(f'no sample value for {field!r}')

    def make(self, model, **values):
        from django.db import models
        from application.models import SportAssociation, User
        for field in model._meta.concrete_fields:
            if field.name in values or field.attname in values:
                continue
            if field.primary_key:
                if isinstance(field, models.UUIDField):
                    self.counter += 1
                    values[field.attname] = U(20_000_000 + self.counter)
                continue
            if field.null or field.has_default() and field.get_default() is not None:
                continue
            if field.is_relation:
                target = field.related_model
                if target is SportAssociation and not field.one_to_one:
                    values[field.attname] = F
                elif target is User:
                    values[field.attname] = self.make(User, role=3).pk
                else:
                    values[field.attname] = self.make(target).pk
                continue
            if isinstance(field, models.DateTimeField) and (field.auto_now or field.auto_now_add):
                continue
            values[field.attname] = self.value(field)
        instance = model(**values)
        model._base_manager.bulk_create([instance])
        return model._base_manager.get(pk=instance.pk)


# Content types and their authored resolver: (kind, path, nullable relation
# for the null case, manager behaviour 'd' deleted filter, 'g' group filter).
DIRECT = ['application.associate', 'application.group', 'application.paymentcategory', 'application.payment',
          'application.supplierandcustomers', 'application.subscription', 'application.subscriptionmembership',
          'application.notificationtemplates', 'application.material', 'application.tags', 'application.associatetrial',
          'application.associateimportdraft', 'application.associateimportdraftstatus',
          'application.cumulativesubscriptiongymlinks', 'application.course', 'application.coursetags',
          'application.courselocation', 'application.campsandretreats', 'application.invoice',
          'application.invoicesuppliers', 'application.customerinvoice', 'application.balancesheet',
          'application.customaccounts', 'application.customaccountstransfer', 'application.module',
          'application.globalcalendarevents', 'application.reminders', 'application.carnet',
          'application.sportassociationmembershipcardconfiguration', 'application.sportassociationheadquarter',
          'application.sportassociationmoduletemplates', 'application.sportassociationmaterial',
          'application.sportassociationdocumentsarchive', 'application.reminder', 'communications.message',
          'communications.automationworkflow', 'communications.communicationconfiguration']
# Registered resolvers that return None for every object (attribute absent or
# not a forward relation), and audited models without a registered resolver.
NONE = ['application.previewandcustomfeatures', 'application.usersonboarding', 'application.collaborationinvites',
        'application.userpartial', 'application.family', 'application.institution', 'application.sentemails',
        'application.nurturingemailsplan', 'application.nurturingemails', 'application.billingplan',
        'application.billingsubscription', 'application.billingpayment', 'application.signature',
        'application.medicalcertificate', 'application.vatmanagement']
UNREGISTERED = ['docmanager.document', 'notifications.notificationsbroadcast', 'notifications.usernotificationsbroadcast']
MANAGERS = {'application.associate': 'dg', 'application.payment': 'dg', 'application.supplierandcustomers': 'dg',
            'application.course': 'dg', 'application.invoice': 'dg', 'application.subscription': 'g',
            'application.coursesubscription': 'd', 'application.coursesubscriptioninstallment': 'dg',
            'application.sportassociation': 'd', 'application.user': 'd'}


class Fixture:
    def __init__(self):
        from application.models import User, SportAssociation, Group
        from auditlog.context import disable_auditlog
        self.factory = Factory()
        with disable_auditlog():
            owners = [(OWNER_A, A, False), (OWNER_B, B, False), (OWNER_X, X, False), (OWNER_D, D, True),
                      (OWNER_E, E, False), (OWNER_F, F, False), (OWNER_777, N777, False), (OWNER_1, N1, False)]
            for user, association, deleted in owners:
                User.original_objects.bulk_create([User(user_id=user, username=f'AI-{user.int}', role=1,
                    password='!private-fixture', deleted=user == OWNER_E)])
                SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=association,
                    user_id=user, denomination=f'Audit {LABELS[str(association)]}', deleted=deleted)])
            User.original_objects.bulk_create([User(user_id=LONER, username='AI-LONER', role=3, password='!private-fixture')])
            Group.objects.bulk_create([Group(group_id=G1, sport_association_id=A, name='G1'),
                                       Group(group_id=G2, sport_association_id=A, name='G2')])

    def user(self, pk):
        from application.models import User
        return User.original_objects.get(pk=pk)

    # Objects whose resolver yields the given association through `path`.
    def build(self, key, association, **extra):
        from django.apps import apps
        from application.models import User, SportAssociation
        make = self.factory.make
        model = apps.get_model(key)
        get = lambda label: apps.get_model(label)  # noqa: E731
        if key in DIRECT:
            return make(model, sport_association_id=association, **extra)
        subscription = lambda: make(get('application.subscription'), sport_association_id=association)  # noqa: E731
        course = lambda: make(get('application.course'), sport_association_id=association)  # noqa: E731
        camps = lambda: make(get('application.campsandretreats'), sport_association_id=association)  # noqa: E731
        owner = lambda: SportAssociation.original_objects.get(pk=association).user_id  # noqa: E731
        builders = {
            'application.coursesubscription': lambda: make(model, course_id=course().pk, **extra),
            'application.coursesubscriptioninstallment': lambda: make(model, course_subscription_id=make(
                get('application.coursesubscription'), course_id=course().pk).pk, **extra),
            'application.instructor': lambda: make(model, user_id=owner(), **extra),
            'application.instructorhours': lambda: make(model, instructor_id=make(get('application.instructor'), user_id=owner()).pk),
            'application.associatetutorrelation': lambda: make(model, associate_id=make(
                get('application.associate'), sport_association_id=association).pk, **extra),
            'application.campsandretreatsperiod': lambda: make(model, camps_and_retreats_id=camps().pk),
            'application.campsandretreatsperiodsservice': lambda: make(model, camps_and_retreats_period_id=make(
                get('application.campsandretreatsperiod'), camps_and_retreats_id=camps().pk).pk),
            'application.campsandretreatssubscription': lambda: make(model, camps_and_retreats_id=camps().pk),
            'application.campsandretreatssubscriptionperiod': lambda: make(model, camps_and_retreats_subscription_id=make(
                get('application.campsandretreatssubscription'), camps_and_retreats_id=camps().pk).pk),
            'application.invoicerows': lambda: make(model, invoice_id=make(get('application.invoice'),
                                                                           sport_association_id=association).pk),
            'application.attendanceregistry': lambda: make(model, course_id=course().pk),
            'application.attendanceday': lambda: make(model, attendance_registry_id=make(
                get('application.attendanceregistry'), course_id=course().pk).pk),
            'application.moduleresponses': lambda: make(model, module_id=make(get('application.module'),
                                                                              sport_association_id=association).pk),
        }
        for related in ('medicalappointments', 'subscriptiontoken', 'subscriptionfile', 'subscriptiontransfer',
                        'soldproducts', 'carnetsubscription'):
            builders['application.' + related] = lambda: make(model, subscription_id=subscription().pk, **extra)
        if key == 'application.sportassociation':
            user = make(User, role=1)
            return make(model, user_id=user.pk, **extra)
        if key == 'application.user':
            user = make(User, role=1, **extra)
            make(SportAssociation, user_id=user.pk)
            return user
        if key in builders:
            return builders[key]()
        return make(model, **extra)


def resolver_kind(key):
    if key in DIRECT:
        return 'direct'
    if key in NONE or key in UNREGISTERED:
        return 'none'
    return {'application.sportassociation': 'self', 'application.user': 'user.sport_association',
            'application.instructor': 'instructor.user.sport_association',
            'application.instructorhours': 'instructor.user.sport_association',
            'application.associatetutorrelation': 'associate.sport_association',
            'application.coursesubscription': 'course.sport_association',
            'application.coursesubscriptioninstallment': 'course_subscription.course.sport_association',
            'application.campsandretreatsperiod': 'camps_and_retreats.sport_association',
            'application.campsandretreatsperiodsservice': 'period.camps_and_retreats.sport_association',
            'application.campsandretreatssubscription': 'camps_and_retreats.sport_association',
            'application.campsandretreatssubscriptionperiod': 'subscription.camps_and_retreats.sport_association',
            'application.invoicerows': 'invoice.sport_association',
            'application.attendanceregistry': 'course.sport_association',
            'application.attendanceday': 'attendance_registry.course.sport_association',
            'application.moduleresponses': 'module.sport_association',
            'application.medicalappointments': 'subscription.sport_association',
            'application.subscriptiontoken': 'subscription.sport_association',
            'application.subscriptionfile': 'subscription.sport_association',
            'application.subscriptiontransfer': 'subscription.sport_association',
            'application.soldproducts': 'subscription.sport_association',
            'application.carnetsubscription': 'subscription.sport_association'}[key]


def association_label(association):
    return LABELS.get(str(association), str(association))


def own_association(obj):
    """The association a resolver reaches for an object built for `A`, for user rows their own."""
    from application.models import SportAssociation
    if obj._meta.label_lower == 'application.sportassociation':
        return obj.pk
    if obj._meta.label_lower == 'application.user':
        return SportAssociation.original_objects.get(user_id=obj.pk).pk
    return A


class Cases:
    """Authored cases: each creates one LogEntry and states the expected index row."""

    def __init__(self, fixture):
        self.fixture = fixture
        self.items = []

    def add(self, name, create, expected, group=None):
        self.items.append((name, create, expected, group))

    @staticmethod
    def log(obj, action=1, changes=None, actor=None):
        from auditlog.models import LogEntry
        return LogEntry.objects.log_create(obj, action=action, changes=changes, actor=actor, force_log=True)

    @staticmethod
    def raw(key, object_pk, changes=None, actor=None, action=1):
        from auditlog.models import LogEntry
        from django.contrib.contenttypes.models import ContentType
        app_label, model = key.split('.')
        content_type = ContentType.objects.get_or_create(app_label=app_label, model=model)[0]
        return LogEntry.objects.create(content_type=content_type, object_pk=object_pk, object_repr='audit-index case',
                                       action=action, changes=changes, actor=actor)


def author_cases(fixture):
    from django.apps import apps
    from auditlog.diff import model_instance_diff
    cases = Cases(fixture)
    actor_b = lambda: fixture.user(OWNER_B)  # noqa: E731
    x_changes = {'sport_association': [str(X), str(X)]}
    keys = DIRECT + NONE + UNREGISTERED + ['application.sportassociation', 'application.user', 'application.instructor',
        'application.instructorhours', 'application.associatetutorrelation', 'application.coursesubscription',
        'application.coursesubscriptioninstallment', 'application.campsandretreatsperiod',
        'application.campsandretreatsperiodsservice', 'application.campsandretreatssubscription',
        'application.campsandretreatssubscriptionperiod', 'application.invoicerows', 'application.attendanceregistry',
        'application.attendanceday', 'application.moduleresponses', 'application.medicalappointments',
        'application.subscriptiontoken', 'application.subscriptionfile', 'application.subscriptiontransfer',
        'application.soldproducts', 'application.carnetsubscription']
    for key in keys:
        kind = resolver_kind(key)
        model = apps.get_model(key)
        has_association = any(f.name == 'sport_association' and f.concrete for f in model._meta.get_fields())

        def live(key=key, kind=kind):
            obj = fixture.build(key, A)
            expected = ('X', 'changes_json.sport_association') if kind == 'none' else (
                association_label(own_association(obj)), kind)
            return cases.log(obj, action=1, changes=x_changes, actor=actor_b()), expected
        cases.add(f'{key} update: resolver before changes and actor', live, None)

        def created(key=key, kind=kind):
            obj = fixture.build(key, A)
            expected = ('B', 'actor.sport_association') if kind == 'none' else (
                association_label(own_association(obj)), kind)
            return cases.log(obj, action=0, changes=model_instance_diff(None, obj), actor=actor_b()), expected
        cases.add(f'{key} create from the model diff', created, None)

        def deleted(key=key, has_association=has_association):
            obj = fixture.build(key, A)
            changes = model_instance_diff(obj, None)
            if key == 'application.user':  # its association refers to it
                apps.get_model('application.sportassociation')._base_manager.filter(user_id=obj.pk)._raw_delete('default')
            type(obj)._base_manager.filter(pk=obj.pk)._raw_delete('default')
            expected = ('A', 'changes_json.sport_association') if has_association else ('B', 'actor.sport_association')
            return cases.log(obj, action=2, changes=changes, actor=actor_b()), expected
        cases.add(f'{key} delete: row gone, changes then actor', deleted, None)

        def anonymous(key=key, kind=kind):
            obj = fixture.build(key, A)
            expected = None if kind == 'none' else (association_label(own_association(obj)), kind)
            return cases.log(obj, action=1, changes={'name': ['a', 'b']}), expected
        cases.add(f'{key} without actor or association change', anonymous, None)

        managers = MANAGERS.get(key, '')
        if 'd' in managers:
            def hidden(key=key):
                obj = fixture.build(key, A, deleted=True)
                return cases.log(obj, changes={'deleted': ['False', 'True']}, actor=actor_b()), ('B', 'actor.sport_association')
            cases.add(f'{key} soft-deleted row hidden by the default manager', hidden, None)
        if 'g' in managers:
            for label, header, visible in [('no header', None, True), ('its group', str(G1), True),
                                           ('another group', str(G2), False), ('unparseable header', 'not-a-group', True),
                                           ('empty header', '', True), ('braced upper-case header', '{' + str(G1).upper() + '}', True),
                                           ('0x-prefixed header of another group', '0x' + G2.hex[2:], False),
                                           ('underscored header of another group', '0_' + G2.hex[2:], False)]:
                def grouped(key=key, kind=kind, visible=visible):
                    obj = fixture.build(key, A, group_id=G1)
                    expected = ('A', kind) if visible else ('B', 'actor.sport_association')
                    return cases.log(obj, changes={'name': ['a', 'b']}, actor=actor_b()), expected
                cases.add(f'{key} in a group, {label}', grouped, None, header)

    # Traversals through base managers and nullable relations.
    def make(key, **values):
        return fixture.factory.make(apps.get_model(key), **values)

    def case(name, build, expected, changes=None, actor=OWNER_B, group=None):
        def create():
            return cases.log(build(), changes=changes or {'name': ['a', 'b']},
                             actor=fixture.user(actor) if actor else None), expected
        cases.add(name, create, None, group)

    case('direct association that is soft-deleted still resolves', lambda: make('application.tags', sport_association_id=D),
         ('D', 'direct'))
    for key in ['application.associate', 'application.paymentcategory', 'application.course', 'application.module',
                'application.carnet', 'communications.communicationconfiguration', 'application.payment']:
        case(f'{key} with a null association falls back to the actor', lambda key=key: make(key, sport_association_id=None),
             ('B', 'actor.sport_association'))
    case('course subscription of a soft-deleted course resolves', lambda: make('application.coursesubscription',
         course_id=make('application.course', sport_association_id=A, deleted=True).pk), ('A', 'course.sport_association'))
    case('course subscription of a course without association', lambda: make('application.coursesubscription',
         course_id=make('application.course', sport_association_id=None).pk), ('B', 'actor.sport_association'))
    case('installment through a soft-deleted subscription resolves', lambda: make(
        'application.coursesubscriptioninstallment', course_subscription_id=make('application.coursesubscription',
            deleted=True, course_id=make('application.course', sport_association_id=A).pk).pk),
         ('A', 'course_subscription.course.sport_association'))
    case('attendance day of a course without association', lambda: make('application.attendanceday',
         attendance_registry_id=make('application.attendanceregistry', course_id=make(
             'application.course', sport_association_id=None).pk).pk), ('B', 'actor.sport_association'))
    case('module response of a module without association', lambda: make('application.moduleresponses',
         module_id=make('application.module', sport_association_id=None).pk), ('B', 'actor.sport_association'))
    case('tutor relation of a soft-deleted associate resolves', lambda: make('application.associatetutorrelation',
         associate_id=make('application.associate', sport_association_id=A, deleted=True).pk), ('A', 'associate.sport_association'))
    for key in ['application.soldproducts', 'application.carnetsubscription']:
        case(f'{key} without subscription falls back', lambda key=key: make(key, subscription_id=None),
             ('B', 'actor.sport_association'))
    case('instructor of an owner of a deleted association', lambda: make('application.instructor', user_id=OWNER_D),
         ('B', 'actor.sport_association'))
    case('instructor of a soft-deleted owner resolves', lambda: make('application.instructor', user_id=OWNER_E),
         ('E', 'instructor.user.sport_association'))
    case('instructor of a user without association', lambda: make('application.instructor', user_id=LONER),
         ('B', 'actor.sport_association'))
    case('instructor hours of an owner of a deleted association', lambda: make('application.instructorhours',
         instructor_id=make('application.instructor', user_id=OWNER_D).pk), ('B', 'actor.sport_association'))
    case('user owning a deleted association', lambda: fixture.user(OWNER_D), ('B', 'actor.sport_association'))
    case('soft-deleted user is hidden from the default manager', lambda: fixture.user(OWNER_E), ('B', 'actor.sport_association'))
    case('user without association', lambda: fixture.user(LONER), ('B', 'actor.sport_association'))
    case('deleted association is hidden from its manager', lambda: apps.get_model('application.sportassociation').original_objects.get(pk=D),
         ('B', 'actor.sport_association'))
    case('association itself', lambda: apps.get_model('application.sportassociation').original_objects.get(pk=X), ('X', 'self'))
    case('signature changes still fall back to the actor', lambda: make('application.signature'), ('X', 'changes_json.sport_association'),
         changes=x_changes)

    # object_pk forms: UUIDField.to_python and int() as the lookup applies them.
    def pk_case(name, key, form, expected, build=None):
        def create():
            obj = build() if build else fixture.build(key, A)
            return cases.raw(key, form(obj.pk), actor=fixture.user(OWNER_B)), expected
        cases.add(name, create, None)
    pk_case('braced upper-case object key', 'application.tags', lambda pk: '{' + str(pk).upper() + '}', ('A', 'direct'))
    pk_case('urn object key', 'application.tags', lambda pk: 'urn:uuid:' + str(pk), ('A', 'direct'))
    pk_case('hex object key without hyphens', 'application.tags', lambda pk: pk.hex, ('A', 'direct'))
    pk_case('unparseable object key', 'application.tags', lambda pk: 'not-a-key', ('B', 'actor.sport_association'))
    pk_case('missing object key', 'application.tags', lambda pk: str(U(99999999)), ('B', 'actor.sport_association'))
    tutor = lambda: make('application.associatetutorrelation', associate_id=make(  # noqa: E731
        'application.associate', sport_association_id=A).pk)
    pk_case('integer key with whitespace', 'application.associatetutorrelation', lambda pk: f' {pk}\n',
            ('A', 'associate.sport_association'), tutor)
    pk_case('integer key with an underscore and sign', 'application.associatetutorrelation',
            lambda pk: '+' + '_'.join(str(pk)) if pk > 9 else f'+{pk}', ('A', 'associate.sport_association'), tutor)
    pk_case('integer key in Arabic-Indic digits', 'application.associatetutorrelation',
            lambda pk: ''.join(chr(0x660 + int(c)) for c in str(pk)), ('A', 'associate.sport_association'), tutor)
    pk_case('integer key outside the column', 'application.associatetutorrelation', lambda pk: str(2 ** 31 + pk),
            ('B', 'actor.sport_association'), tutor)
    pk_case('non-integer key', 'application.associatetutorrelation', lambda pk: f'{pk}.0',
            ('B', 'actor.sport_association'), tutor)

    # changes['sport_association'] forms on a content type with no resolver result.
    def changes_case(name, changes, expected, actor=OWNER_B, key='application.vatmanagement'):
        def create():
            obj = fixture.build(key, A)
            return cases.raw(key, str(obj.pk), changes=changes, actor=fixture.user(actor) if actor else None), expected
        cases.add(name, create, None)
    changes_case('old value first', {'sport_association': [str(X), str(A)]}, ('X', 'changes_json.sport_association'))
    changes_case('None string then new value', {'sport_association': ['None', str(A)]}, ('A', 'changes_json.sport_association'))
    changes_case('lower-case none skipped', {'sport_association': ['none', 'NONE']}, ('B', 'actor.sport_association'))
    changes_case('deleted association then live one', {'sport_association': [str(D), str(X)]}, ('X', 'changes_json.sport_association'))
    changes_case('missing association then actor', {'sport_association': [str(U(424242)), 'None']}, ('B', 'actor.sport_association'))
    changes_case('scalar value', {'sport_association': str(X)}, ('X', 'changes_json.sport_association'))
    changes_case('third element ignored', {'sport_association': ['None', None, str(X)]}, ('B', 'actor.sport_association'))
    changes_case('unparseable value ends the fallback', {'sport_association': ['bad', str(X)]}, ('B', 'actor.sport_association'))
    changes_case('integer value is UUID(int=...)', {'sport_association': [777, None]}, ('N777', 'changes_json.sport_association'))
    changes_case('true is UUID(int=1)', {'sport_association': [True]}, ('N1', 'changes_json.sport_association'))
    changes_case('false and zero skipped', {'sport_association': [False, 0]}, ('B', 'actor.sport_association'))
    changes_case('float value ends the fallback', {'sport_association': [1.5, str(X)]}, ('B', 'actor.sport_association'))
    changes_case('float zero skipped', {'sport_association': [0.0, str(X)]}, ('X', 'changes_json.sport_association'))
    changes_case('negative integer ends the fallback', {'sport_association': [-5, str(X)]}, ('B', 'actor.sport_association'))
    changes_case('list element ends the fallback', {'sport_association': [[str(X)], str(X)]}, ('B', 'actor.sport_association'))
    changes_case('empty list element skipped', {'sport_association': [[], str(X)]}, ('X', 'changes_json.sport_association'))
    changes_case('object value ends the fallback', {'sport_association': {'id': str(X)}}, ('B', 'actor.sport_association'))
    changes_case('braced upper-case value', {'sport_association': ['{' + str(X).upper() + '}']}, ('X', 'changes_json.sport_association'))
    changes_case('0x hex value', {'sport_association': ['0x' + N777.hex[2:]]}, ('N777', 'changes_json.sport_association'))
    changes_case('empty string skipped', {'sport_association': ['', str(X)]}, ('X', 'changes_json.sport_association'))
    changes_case('empty changes', {}, ('B', 'actor.sport_association'))
    changes_case('changes stored as a list', [['sport_association', str(X)]], ('B', 'actor.sport_association'))
    changes_case('changes stored as a string', json.dumps({'sport_association': [str(X)]}), ('B', 'actor.sport_association'))
    changes_case('null changes', None, ('B', 'actor.sport_association'))
    changes_case('other field only', {'sport_association_id': [str(X), 'None']}, ('B', 'actor.sport_association'))
    changes_case('changes resolve without actor', {'sport_association': [str(X)]}, ('X', 'changes_json.sport_association'), None)
    changes_case('actor of a deleted association', {}, None, OWNER_D)
    changes_case('soft-deleted actor with an association', {}, ('E', 'actor.sport_association'), OWNER_E)
    changes_case('actor without association', {}, None, LONER)
    changes_case('no actor, no changes', None, None, None)
    changes_case('unregistered content type uses changes', {'sport_association': [str(X)]},
                 ('X', 'changes_json.sport_association'), key='docmanager.document')

    def stale(name, key, changes, expected):
        def create():
            return cases.raw(key, str(U(5555)), changes=changes, actor=fixture.user(OWNER_B)), expected
        cases.add(name, create, None)
    stale('stale content type uses changes', 'application.removedmodel', {'sport_association': [str(X)]},
          ('X', 'changes_json.sport_association'))
    stale('unaudited content type falls back to the actor', 'contenttypes.contenttype', None, ('B', 'actor.sport_association'))
    return cases.items


class GoDriver:
    def __init__(self, executable, dsn, log):
        self.process = subprocess.Popen([str(executable), '-test.run', '^TestAuditIndexResolverDriver$', '-test.count=1'],
                                        env={**{k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR')},
                                             'ASSOZETA_AUDIT_INDEX_DSN': dsn},
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True, bufsize=1)

    def index(self, entry, group):
        self.process.stdin.write(json.dumps({'entry': entry, 'group': group or ''}) + '\n')
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise RuntimeError('Go driver ended: ' + str(self.process.wait()))
            if line.startswith('AUDITINDEX '):
                answer = json.loads(line[len('AUDITINDEX '):])
                return None if answer is None else (LABELS.get(answer['association'], answer['association']), answer['path'])

    def close(self):
        self.process.stdin.close()
        self.process.wait(timeout=60)


def django_row(entry):
    from application.models.audit_models import AuditLogIndex
    rows = list(AuditLogIndex.objects.filter(log_entry_id=entry.pk).values_list('sport_association_id', 'resolution_path'))
    assert len(rows) <= 1
    return (LABELS.get(str(rows[0][0]), str(rows[0][0])), rows[0][1]) if rows else None


def run_resolver(fixture, driver, output):
    from application import mixin
    outcomes = []
    for name, create, _, group in author_cases(fixture):
        if group is not None:
            mixin._local.group_id = group
        try:
            entry, expected = create()
        finally:
            if hasattr(mixin._local, 'group_id'):
                del mixin._local.group_id
        got = django_row(entry)
        assert got == (tuple(expected) if expected else None), f'{name}: Django index {got} differs from authored {expected}'
        outcome = {'case': name, 'group': group, 'django': got, 'django_status': 'pass'}
        if driver is not None:
            go = driver.index(entry.pk, group)
            if go != got:
                (output / 'difference.json').write_text(json.dumps({'case': name, 'django': got, 'go': go}) + '\n')
            assert go == got, f'{name}: Go index {go} differs from Django {got}'
            outcome['go_status'] = 'pass'
        outcomes.append(outcome)
    return outcomes


BACKFILL_CASES = [
    ('default options', []),
    ('dry run', ['--dry-run']),
    ('small batches re-evaluate the pending query', ['--batch-size', '40']),
    ('small batches, dry run', ['--batch-size=40', '--dry-run']),
    ('limit within batches', ['--limit', '25', '--batch-size', '10']),
    ('limit larger than the batch, dry run', ['--dry-run', '--limit=7']),
    ('content type filter', ['--content-type', 'application.tags']),
    ('content type filter with limit', ['--content-type', 'application.vatmanagement', '--limit', '3', '--batch-size', '2']),
    ('content type without pending entries', ['--content-type', 'auth.permission', '--dry-run']),
    ('unknown content type', ['--content-type', 'application.nosuchmodel']),
    ('content type with three parts', ['--content-type', 'application.tags.extra']),
    ('content type without a dot', ['--content-type', 'application', '--dry-run']),
    ('content type is case-sensitive', ['--content-type', 'application.Tags']),
    ('empty content type is no filter', ['--content-type=', '--limit', '5']),
    ('zero limit is no limit', ['--limit', '0', '--batch-size', '1000']),
    ('negative limit', ['--limit', '-1']),
    ('zero batch size', ['--batch-size', '0']),
    ('negative batch size', ['--batch-size', '-3']),
    ('integer option with whitespace and underscore', ['--batch-size', ' 1_0 ', '--limit', '+12']),
    ('repeated option keeps the last value', ['--limit', '2', '--limit', '4']),
    ('invalid integer', ['--batch-size', 'many']),
    ('unknown option', ['--everything']),
    ('missing option value', ['--content-type']),
]


class BackfillFixture:
    """Pending entries: every LogEntry; already indexed: every seventh, with a marker path."""

    def __init__(self):
        from auditlog.models import LogEntry
        from application.models.audit_models import AuditLogIndex
        AuditLogIndex.objects.all().delete()
        ids = list(LogEntry.objects.order_by('pk').values_list('pk', flat=True))
        AuditLogIndex.objects.bulk_create([AuditLogIndex(audit_log_index_id=U(30_000_000 + i), log_entry_id=pk,
            sport_association_id=F, resolution_path='preexisting', created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
            for i, pk in enumerate(ids[::7])])
        self.seed = self.snapshot()
        self.preexisting = {row['audit_log_index_id'] for row in self.seed}
        self.entries = len(ids)

    @staticmethod
    def snapshot():
        from application.models.audit_models import AuditLogIndex
        return [{k: str(v) for k, v in row.items()} for row in
                AuditLogIndex.objects.order_by('log_entry_id').values('audit_log_index_id', 'log_entry_id',
                                                                     'sport_association_id', 'resolution_path', 'created_at')]

    def reset(self):
        from application.models.audit_models import AuditLogIndex
        AuditLogIndex.objects.exclude(pk__in=[UUID(pk) for pk in self.preexisting]).delete()
        assert self.snapshot() == self.seed, 'backfill reset'

    def normalized(self, since, until):
        rows = []
        for row in self.snapshot():
            if row['audit_log_index_id'] not in self.preexisting:
                created = datetime.fromisoformat(row['created_at'])
                assert since <= created <= until, 'index created inside the command run'
                row = dict(row, audit_log_index_id='new', created_at='run')
            rows.append(dict(row, sport_association_id=LABELS.get(row['sport_association_id'], row['sport_association_id'])))
        return rows


def run_backfill(executable, environment, output):
    fixture = BackfillFixture()
    outcomes = []
    for name, arguments in BACKFILL_CASES:
        results = {}
        for runtime in ('django', 'go'):
            if runtime == 'go' and executable is None:
                break
            fixture.reset()
            command = [sys.executable, '-I', '-c', DJANGO_COMMAND, *arguments] if runtime == 'django' else [str(executable), *arguments]
            since = datetime.now(timezone.utc)
            completed = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=600, cwd=ROOT / 'BE')
            until = datetime.now(timezone.utc)
            results[runtime] = {'status': completed.returncode, 'stdout': completed.stdout,
                                'index': fixture.normalized(since, until)}
            if runtime == 'django':
                (output / 'backfill-django.log').open('a').write(f'## {name}\n{completed.stderr[-3000:]}\n')
        django = results['django']
        created = sum(row['audit_log_index_id'] == 'new' for row in django['index'])
        outcome = {'case': name, 'arguments': arguments, 'status': django['status'], 'created': created,
                   'stdout_sha256': hashlib.sha256(django['stdout'].encode()).hexdigest(), 'django': 'pass'}
        assert_backfill_authored(name, arguments, django, created, fixture)
        if 'go' in results:
            if results['go'] != django:
                (output / 'difference.json').write_text(json.dumps({'case': name, **results}, indent=1) + '\n')
            assert results['go'] == django, f'{name}: Go command differs from Django'
            outcome['go'] = 'pass'
        outcomes.append(outcome)
        print(f'PASS backfill: {name} (exit {django["status"]}, created {created})', flush=True)
    return outcomes, fixture.entries


def assert_backfill_authored(name, arguments, result, created, fixture):
    """Authored shape of each run, read from the command's handle()."""
    lines = result['stdout'].splitlines()
    status = result['status']
    pending = fixture.entries - len(fixture.preexisting)
    if name in ('invalid integer', 'unknown option', 'missing option value'):
        assert status == 2 and result['stdout'] == '' and created == 0, name
        return
    if name in ('unknown content type', 'content type with three parts', 'content type is case-sensitive'):
        assert status == 0 and lines == [f'Invalid content type: {arguments[1]}'], name
        return
    if name == 'content type without a dot':
        assert status == 0 and lines == ['Running in DRY-RUN mode', 'Invalid content type: application'], name
        return
    if name == 'negative limit':
        assert status == 1 and lines == [] and created == 0, name
        return
    if name == 'zero batch size':
        assert status == 1 and lines == [f'Found {pending} LogEntry records to process'] and created == 0, name
        return
    if name == 'content type without pending entries':
        assert status == 0 and lines == ['Running in DRY-RUN mode', 'Filtering by content type: auth.permission',
                                         'Found 0 LogEntry records to process', 'No records to backfill'], name
        return
    assert status == 0 and lines[-1] == '=' * 60 and 'Backfill complete!' in lines, name
    processed = int(next(line for line in lines if line.startswith('Total processed: ')).split(': ')[1])
    indexed = int(next(line for line in lines if line.startswith('Indexed: ')).split(': ')[1])
    if name == 'negative batch size':
        assert processed == 0 and created == 0 and f'Found {pending} LogEntry records to process' in lines, name
    elif '--dry-run' in arguments or '--batch-size=40' in arguments and '--dry-run' in arguments:
        assert created == 0 and any(line.startswith('  [DRY-RUN] Would create') for line in lines), name
    else:
        assert created == indexed, name
    if name == 'small batches re-evaluate the pending query':
        # Each batch slices the pending query after the previous batch's inserts.
        assert processed < pending, 'later batches skip entries'
    if name in ('default options', 'zero limit is no limit'):
        assert processed == pending, name


def main():
    require_production_python()
    # Optional label: audit-index-red (Go code absent) or audit-index-mutation.
    suffix = f'-{sys.argv[1]}' if len(sys.argv) > 1 else ''
    output = ROOT / f'quality-reports/go-migration/audit-index{suffix}'
    output.mkdir(parents=True, exist_ok=True)
    for stale_file in ('difference.json', 'backfill-django.log'):
        (output / stale_file).unlink(missing_ok=True)
    report = output / 'results.json'
    result = {'success': False, 'scope': __doc__.strip()}
    report.write_text(json.dumps(result) + '\n')
    postgres = shutil.which('postgres') or str(Path('/opt/homebrew/opt/postgresql@16/bin/postgres'))
    if not Path(postgres).is_file() or not shutil.which('go'):
        raise RuntimeError('Private PostgreSQL and Go are required')
    binaries = Path(postgres).parent
    base = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    base.update(LC_ALL='C', LANG='C')
    with tempfile.TemporaryDirectory(prefix='az-audit-index-', dir='/tmp') as directory, (output / 'services.log').open('w') as log:
        work = Path(directory)
        attempted = False
        driver = None

        def run(args):
            return subprocess.run([str(a) for a in args], env=base, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
        try:
            resolver = work / 'audit-index.test'
            built = subprocess.run(['go', 'test', '-race', '-c', '-tags', 'auditindexintegration', '-o', str(resolver),
                                    './internal/repository'], cwd=ROOT / 'BE_GO', env=base, stdout=log, stderr=subprocess.STDOUT,
                                   timeout=900)
            command = work / 'backfill-audit-log-index'
            built_command = subprocess.run(['go', 'build', '-race', '-o', str(command), './cmd/backfill-audit-log-index'],
                                           cwd=ROOT / 'BE_GO', env=base, stdout=log, stderr=subprocess.STDOUT, timeout=900)
            # RED: Django still runs every authored case when the Go code is absent.
            # The package builds without the driver test too: require the test itself.
            listed = subprocess.run([str(resolver), '-test.list', '.'], env=base, capture_output=True, text=True,
                                    timeout=60).stdout.split() if built.returncode == 0 else []
            resolver = resolver if 'TestAuditIndexResolverDriver' in listed else None
            command = command if built_command.returncode == 0 else None
            run([binaries / 'initdb', '-D', work / 'postgres', '-A', 'trust', '-U', 'contracts', '--no-sync', '--no-locale', '-E', 'UTF8'])
            attempted = True
            run([binaries / 'pg_ctl', '-D', work / 'postgres', '-l', work / 'postgres.log', '-o', f"-k {work} -h '' -F", '-w', 'start'])
            run([binaries / 'createdb', '-h', work, '-U', 'contracts', DATABASE])
            initialize_current_schema(work, work, '5432', DATABASE, log)
            from application.audit_resolvers import SPORT_ASSOCIATION_RESOLVERS
            from auditlog.registry import auditlog
            audited = sorted(m._meta.label_lower for m in auditlog.get_models())
            covered = sorted(DIRECT + NONE + UNREGISTERED + [k for k in audited if k not in DIRECT + NONE + UNREGISTERED])
            assert audited == covered, 'every audited model has cases'
            registered = sorted(SPORT_ASSOCIATION_RESOLVERS)
            assert registered == sorted(k for k in audited if k not in UNREGISTERED), 'registered resolvers'
            result['content_types'] = {'audited': len(audited), 'registered': len(registered)}
            fixture = Fixture()
            if resolver is not None:
                driver = GoDriver(resolver, f'host={work} port=5432 user=contracts dbname={DATABASE} sslmode=disable', log)
            result['resolver_cases'] = run_resolver(fixture, driver, output)
            environment = dict(os.environ)
            environment.update(DBTYPE='POSTGRES', DBNAME=DATABASE, DBUSER='contracts', DBHOST=str(work), DBPORT='5432',
                               DBPASSWORD='private-audit-index-password-canary', SECRET_KEY='private-audit-index-secret-canary',
                               DEBUG='False', REDIS_HOST='127.0.0.1', REDIS_PORT='9', REDIS_PASSWORD='private-redis-canary',
                               DATA_RESTORE_LOCK_PATH=str(work / 'operations.lock'), DJANGO_COLORS='nocolor')
            result['backfill_cases'], result['backfill_entries'] = run_backfill(command, environment, output)
            resolver_go = sum(c.get('go_status') == 'pass' for c in result['resolver_cases'])
            backfill_go = sum(c.get('go') == 'pass' for c in result['backfill_cases'])
            result['django_passed'] = len(result['resolver_cases']) + len(result['backfill_cases'])
            result['go_passed'] = resolver_go + backfill_go
            summary = {'resolver': f"django {len(result['resolver_cases'])}, go {resolver_go}",
                       'backfill': f"django {len(result['backfill_cases'])}, go {backfill_go}"}
            print(json.dumps(summary))
            assert result['go_passed'] == result['django_passed'], 'Go resolver or command missing or failing'
        except AssertionError as error:
            result['error'] = str(error)
            raise
        finally:
            try:
                if driver is not None:
                    driver.close()
                from django.db import connections
                connections.close_all()
            finally:
                if attempted:
                    run([binaries / 'pg_ctl', '-D', work / 'postgres', '-m', 'immediate', '-w', 'stop'])
                report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    paths = ['TESTS/adapters/go/audit_index.py', 'BE/application/signals.py', 'BE/application/audit_resolvers.py',
             'BE/application/management/commands/backfill_audit_log_index.py', 'BE_GO/internal/repository/audit_index.go',
             'BE_GO/internal/repository/audit_index_backfill.go', 'BE_GO/internal/repository/audit_index_integration_test.go',
             'BE_GO/internal/service/audit_index_backfill.go', 'BE_GO/internal/dto/audit_index.go',
             'BE_GO/cmd/backfill-audit-log-index/main.go']
    result.update(success=True, case_count=result['django_passed'], race_instrumented=True,
                  source_hashes={p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in paths})
    report.write_text(json.dumps(result, indent=2, default=str) + '\n')
    print(json.dumps({'success': True, 'case_count': result['case_count']}))


if __name__ == '__main__':
    main()
