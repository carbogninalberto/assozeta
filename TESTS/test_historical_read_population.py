"""Independent controls for exact historical import population acceptance."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from TESTS.adapters.django import historical_read_population as population


ARCHIVE = json.loads(
    (Path(__file__).parent / 'fixtures' / 'bakney-v1.json').read_text(encoding='utf-8')
)
MANIFEST = ARCHIVE['entries']['manifest.json']['models_exported']
RETIRED = {'BillingPlan', 'PreviewAndCustomFeatures'}
LEGACY_INVOICES = 'SportAssociationInvoices'
ASSOCIATION = ARCHIVE['ids']['association']
OWNER = ARCHIVE['ids']['owner']
DOCUMENT = ARCHIVE['ids']['document']
TASK_ID = '9de7c3f7-2d6f-4ef5-965e-a7c2f9034592'

MIGRATION_START = datetime(2026, 10, 25, 9, 0, tzinfo=timezone.utc)
MIGRATION_END = datetime(2026, 10, 25, 9, 30, tzinfo=timezone.utc)
IMPORT_START = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
IMPORT_END = datetime(2026, 10, 25, 10, 30, tzinfo=timezone.utc)
CONFIGURE_START = datetime(2026, 10, 25, 11, 0, tzinfo=timezone.utc)
CONFIGURE_END = datetime(2026, 10, 25, 11, 30, tzinfo=timezone.utc)
MIGRATION_NOW = datetime(2026, 10, 25, 9, 15, tzinfo=timezone.utc)
DEFAULT_NOW = MIGRATION_NOW
IMPORT_NOW = datetime(2026, 10, 25, 10, 15, tzinfo=timezone.utc)
CONFIGURE_NOW = datetime(2026, 10, 25, 11, 15, tzinfo=timezone.utc)

CONFIG = 'instance.InstanceConfiguration'
AUDIT = 'auditlog.LogEntry'
INDEX = 'application.AuditLogIndex'
TASK_RESULT = 'django_celery_results.TaskResult'
CONTENT_TYPE = 'contenttypes.ContentType'
PERMISSION = 'auth.Permission'
CONTROL = 'framework.ContractControl'
GROUP = 'auth.Group'
SESSION = 'sessions.Session'
PERIODIC_TASK = 'django_celery_beat.PeriodicTask'
PERIODIC_TASKS = 'django_celery_beat.PeriodicTasks'
USER_GROUPS = 'application.User_groups'

CONFIGURATION_DEFAULTS = {
    'general': {'searchBar': True, 'cookieConsent': True, 'showDemoButton': False},
    'login': {
        'showSportAssociationCreation': False,
        'allowOauthLogin': True,
        'showFooterInfo': True,
        'allowOnlyAthletes': False,
        'showTestimonials': False,
        'termsAndConditionsUrl': '',
    },
    'navbar': {
        'showAffiliateLink': False,
        'showReview': False,
        'showNotifications': True,
    },
    'sidebar': {
        'showPlanUpgrades': False,
        'showPrivacyPolicy': True,
        'showTermsOfService': True,
        'showPreferences': True,
    },
}

CONTRACT = {
    'ids': ARCHIVE['ids'],
    'retired_policy': {
        'explicit_skip_records': {
            'BillingPlan': 1,
            'PreviewAndCustomFeatures': 1,
        },
    },
    'setup': {
        'configure': {
            'domain': 'Historical.Example.Test',
            'oem': {
                'name': 'Historical contract club',
                'abbreviation': 'HCC',
                'primaryColor': '#1A2B3C',
                'supportEmail': 'support@example.test',
                'logo': '/api/instance/logo.png',
            },
            'oauth': {
                'googleClientId': 'google-client',
                'appleClientId': 'apple-client',
            },
            'stripe': {
                'publicKey': 'must-not-be-persisted',
                'secretKey': 'must-not-be-persisted',
            },
            'initialization': {'type': 'import', 'importTaskId': '$import_task_id'},
        },
    },
}

IMPORT_ORDER = [
    'SportAssociation', 'User', 'Group', 'UsersOnboarding',
    'CollaborationInvites', 'Family', 'Associate', 'AssociateTutorRelation',
    'VatManagement', 'PaymentCategory', 'CustomAccounts', 'Tags', 'CourseTags',
    'Folder', 'Document', 'SupplierAndCustomers', 'ProductsAndServices',
    'SportAssociationHeadquarter', 'SportAssociationMaterial',
    'SportAssociationModuleTemplates',
    'SportAssociationMembershipCardConfiguration',
    'CommunicationConfiguration', 'NotificationTemplates', 'Instructor',
    'InstructorHours', 'Signature', 'Payment', 'Subscription',
    'SubscriptionMembership', 'SubscriptionToken', 'SubscriptionFile',
    'MedicalCertificate', 'MedicalAppointments', 'CourseLocation', 'Course',
    'CourseSubscription', 'CourseSubscriptionInstallment', 'Carnet',
    'CarnetSubscription', 'CampsAndRetreats', 'CampsAndRetreatsPeriod',
    'CampsAndRetreatsPeriodsService', 'CampsAndRetreatsSubscription',
    'CampsAndRetreatsSubscriptionPeriod', 'Invoice', 'InvoiceRows',
    'InvoiceSuppliers', 'CustomerInvoice', 'BalanceSheet',
    'AttendanceRegistry', 'AttendanceDay', 'Message', 'MessageTransaction',
    'AutomationWorkflow', 'Reminder', 'EmailLog', 'GlobalCalendarEvents',
    'Reminders', 'Module', 'ModuleResponses', 'SoldProducts',
    'SubscriptionTransfer', 'CumulativeSubscriptionGymLinks',
    'SportAssociationDocumentsArchive',
]

LABELS = {
    'Document': 'docmanager.Document',
    'CommunicationConfiguration': 'communications.CommunicationConfiguration',
    'Message': 'communications.Message',
    'MessageTransaction': 'communications.MessageTransaction',
    'AutomationWorkflow': 'communications.AutomationWorkflow',
}
LABELS.update({
    item['name']: 'application.' + item['name']
    for item in MANIFEST
    if item['name'] not in LABELS
})
LABELS['Group'] = 'application.Group'

PK_FIELDS = {
    'User': 'user_id',
    'Group': 'group_id',
    'UsersOnboarding': 'users_onboarding_id',
    'CollaborationInvites': 'collaboration_invite_id',
    'SportAssociation': 'sport_association_id',
    'Associate': 'associate_id',
    'AssociateTutorRelation': 'associate_tutor_relation_id',
    'Payment': 'payment_id',
    'PaymentCategory': 'payment_category_id',
    'Subscription': 'subscription_id',
    'Document': 'document_id',
    'Message': 'message_id',
    'Course': 'course_id',
    'SportAssociationDocumentsArchive': 'sport_association_documents_archive_id',
    'PreviewAndCustomFeatures': 'preview_and_custom_features_id',
}

AUDITED = {
    'User', 'SportAssociation', 'Associate', 'Payment', 'Subscription',
    'Document', 'SportAssociationDocumentsArchive', 'CollaborationInvites',
    'Message',
}
RESOLVERS = {
    'application.sportassociation': 'sport_association_self_resolver',
    'application.user': 'user_resolver',
    'application.associate': 'direct_resolver',
    'application.payment': 'direct_resolver',
    'application.subscription': 'direct_resolver',
    'application.sportassociationdocumentsarchive': 'direct_resolver',
    'communications.message': 'direct_resolver',
}
AUDIT_FIELDS = {
    LABELS[name].lower(): {
        'include_fields': [],
        'exclude_fields': list(('last_login', 'password') if name == 'User' else ()),
        'mapping_fields': {},
        'mask_fields': [],
        'mask_callable': None,
    }
    for name in AUDITED
}

PAYMENT_NAMES = [
    ('entrate e proventi da attività tipiche', False, True),
    ('entrate commerciali', False, False),
    ('contributi', False, False),
    ('liberalità e altro', False, False),
    ('camp e ritiri', False, False),
    ('affitto struttura sportiva', False, False),
    ('Oneri Federali', True, False),
    ('Oneri Finanziari', True, False),
    ('Impianti Sportivi Comunali', True, False),
    ('Impianti Sportivi Privati', True, False),
    ('Prestazioni Sanitarie e Sportive', True, False),
    ('Compensi e Rimborsi Spese', True, False),
    ('Formazione tecnica', True, False),
    ('Oneri generali', True, False),
    ('Oneri erariali', True, False),
    ('Svincolo / Riscatto atleti', True, False),
    ('Pagamento Fornitore', True, False),
]

NOT_PROVIDED = object()


class Field:
    def __init__(
        self, attname, *, name=None, kind='str', default=NOT_PROVIDED,
        auto=False, relation=False,
    ):
        self.attname = attname
        self.name = name or (attname.removesuffix('_id') if relation else attname)
        self.kind = kind
        self.default = default
        self.auto_now = auto
        self.auto_now_add = auto
        self.is_relation = relation
        self.many_to_one = relation
        self.one_to_one = False
        self.concrete = True
        self.target_field = self

    def has_default(self):
        return self.default is not NOT_PROVIDED

    def get_internal_type(self):
        return {
            'json': 'JSONField', 'datetime': 'DateTimeField',
            'date': 'DateField', 'decimal': 'DecimalField',
        }.get(self.kind, 'Field')

    def get_default(self):
        value = self.default() if callable(self.default) else self.default
        return deepcopy(value)

    def to_python(self, value):
        if value is None:
            return None
        if self.kind == 'uuid':
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        if self.kind == 'datetime':
            if isinstance(value, datetime):
                return value
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        if self.kind == 'date':
            return value if isinstance(value, date) and not isinstance(value, datetime) else date.fromisoformat(str(value))
        if self.kind == 'decimal':
            return Decimal(value)
        if self.kind == 'bool':
            return bool(value)
        if self.kind == 'int':
            return int(value)
        if self.kind == 'json':
            return deepcopy(value)
        return str(value)


def model(name, label, fields, *, auto_created=False, managed=True, proxy=False,
          swapped=False):
    class Meta:
        pass

    app_label, model_name = label.split('.', 1)
    pk = next((item for item in fields if item.attname == PK_FIELDS.get(name)), fields[0])
    Meta.label = label
    Meta.label_lower = label.lower()
    Meta.app_label = app_label
    Meta.model_name = model_name.lower()
    Meta.verbose_name = name
    Meta.pk = pk
    Meta.concrete_fields = fields
    Meta.fields = fields
    Meta.default_permissions = ('add', 'change', 'delete', 'view')
    Meta.permissions = ()
    Meta.verbose_name_raw = name
    Meta.managed = managed
    Meta.proxy = proxy
    Meta.auto_created = auto_created
    Meta.swapped = swapped

    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)

    def __str__(self):
        return f'{self._meta.label_lower}:{getattr(self, self._meta.pk.attname)}'

    return type(name, (), {
        '_meta': Meta,
        '__init__': __init__,
        '__str__': __str__,
    })


ARCHIVE_FIELDS = {}
for _spec in MANIFEST:
    _fields = []
    _seen = set()
    for _row in ARCHIVE['entries'][_spec['file']]:
        for _key, _value in _row.items():
            if _key.startswith('_m2m_') or _key in _seen:
                continue
            _seen.add(_key)
            _kind = (
                'uuid' if _key.endswith('_id') else
                'bool' if isinstance(_value, bool) else
                'int' if type(_value) is int else
                'decimal' if _key in ('amount', 'subscription_fee', 'membership_fee') else
                'date' if _key == 'date' else 'str'
            )
            _fields.append(Field(
                _key, kind=_kind,
                relation=_key.endswith('_id') and _key != PK_FIELDS.get(_spec['name']),
            ))
    _pk_name = PK_FIELDS.get(_spec['name'], 'id')
    if not any(field.attname == _pk_name for field in _fields):
        _fields.insert(0, Field(_pk_name, kind='uuid'))
    ARCHIVE_FIELDS[_spec['name']] = _fields


def extend(name, *fields):
    existing = {field.attname for field in ARCHIVE_FIELDS[name]}
    ARCHIVE_FIELDS[name].extend(
        field for field in fields if field.attname not in existing
    )


extend(
    'User',
    Field('date_joined', kind='datetime', default=lambda: DEFAULT_NOW),
    Field('last_login', kind='datetime', default=None),
    Field('is_staff', kind='bool', default=False),
    Field('is_superuser', kind='bool', default=False),
    Field('two_fa', kind='bool', default=False),
    Field('two_fa_secret', default=None),
    Field('stripe_account_id', default=None),
    Field('stripe_on_boarding_completed', kind='bool', default=False),
    Field('integration_google_state', default=None),
    Field('integration_google_credentials', kind='json', default=None),
    Field('integration_google_auto_sync', kind='bool', default=False),
    Field('phone', default=None),
    Field('connected_user_id', kind='uuid', default=None, relation=True),
    Field('collaborator_role', kind='int', default=1),
    Field('collaborator_permissions', kind='json', default=None),
    Field('deleted', kind='bool', default=False),
    Field('delete_on', kind='date', default=None),
    Field('subscription_duration', kind='int', default=2),
    Field('subscription_start_day', kind='int', default=1),
    Field('subscription_start_month', kind='int', default=1),
    Field('custom_end_date', kind='bool', default=False),
    Field('subscription_end_day', kind='int', default=31),
    Field('subscription_end_month', kind='int', default=12),
)
extend(
    'SportAssociation',
    Field('subscription_fee', kind='decimal', default=Decimal('0.00')),
    Field('membership_fee', kind='decimal', default=None),
    Field('configuration', kind='json', default=dict),
    Field('reviewed', kind='bool', default=False),
)
extend('Associate', Field('email', default=None))
extend(
    'PaymentCategory',
    Field('name'), Field('creation_date', kind='datetime'),
    Field('sport_association_id', kind='uuid', default=None, relation=True),
    Field('expense', kind='bool', default=False),
    Field('tax_deductible', kind='bool', default=False),
    Field('archived', kind='bool', default=False),
    Field('type', kind='int', default=1),
    Field('vat_management_id', kind='uuid', default=None, relation=True),
    Field('deleted', kind='bool', default=False),
)
extend(
    'PreviewAndCustomFeatures',
    Field('name'),
    Field('description'),
)
extend(
    'Subscription',
    Field('subscription_number', default=0),
    Field('signature_storage_key', default=None),
    Field('creation_date', kind='datetime', default=lambda: DEFAULT_NOW),
    Field('start_date', kind='date', default=None),
    Field('end_date', kind='date', default=None),
)
extend(
    'Payment',
    Field('payment_category_id', kind='uuid', default=None, relation=True),
    Field('creation_date', kind='datetime', default=lambda: DEFAULT_NOW),
    Field('payment_date', kind='datetime', default=None),
    Field('type', default='default'),
    Field('subject', kind='int', default=0),
    Field('expense', kind='bool', default=False),
    Field('custom_accounts_id', kind='uuid', default=None, relation=True),
    Field('deleted', kind='bool', default=False),
)
extend(
    'Document',
    Field('creation_date', kind='datetime', default=lambda: DEFAULT_NOW),
    Field('token', kind='uuid', default=uuid.uuid4),
    Field('file_size_bytes', kind='int', default=None),
)
extend(
    'SportAssociationDocumentsArchive',
    Field('folder_id', kind='uuid', default=None, relation=True),
)
extend(
    'CollaborationInvites',
    Field('user_id', kind='uuid', relation=True),
    Field('email'),
    Field('creation_date', kind='datetime', default=lambda: DEFAULT_NOW),
    Field('accepted', kind='bool', default=False),
    Field('expiration_date', kind='datetime', default=None),
    Field('token', default=None),
    Field('collaborator_role', kind='int', default=1),
    Field('collaborator_permissions', kind='json', default=None),
)
extend(
    'Message',
    Field('sport_association_id', kind='uuid', relation=True),
    Field('type'),
    Field('message'),
    Field('subject', default=None),
    Field('created_at', kind='datetime', default=lambda: DEFAULT_NOW, auto=True),
)

MODELS = [
    model(item['name'], LABELS[item['name']], ARCHIVE_FIELDS[item['name']])
    for item in MANIFEST
    if item['name'] != LEGACY_INVOICES
]
MODELS.extend([
    model('Group', GROUP, [
        Field('id', kind='int'), Field('name'),
    ]),
    model('Permission', PERMISSION, [
        Field('id', kind='int'), Field('name'),
        Field('content_type_id', kind='int', relation=True),
        Field('codename'),
    ]),
    model('Session', SESSION, [
        Field('session_key'), Field('session_data'), Field('expire_date', kind='datetime'),
    ]),
    model('PeriodicTask', PERIODIC_TASK, [
        Field('id', kind='int'), Field('name'), Field('task'),
        Field('enabled', kind='bool'),
    ]),
    model('PeriodicTasks', PERIODIC_TASKS, [
        Field('ident', kind='int'), Field('last_update', kind='datetime'),
    ]),
    model('User_groups', USER_GROUPS, [
        Field('id', kind='int'), Field('user_id', kind='uuid', relation=True),
        Field('group_id', kind='int', relation=True),
    ], auto_created=True),
    model('InstanceConfiguration', CONFIG, [
        Field('id', kind='int'),
        Field('domain'), Field('configured_at', kind='datetime'),
        Field('updated_at', kind='datetime'), Field('setup_provenance'),
        Field('onboarding_completed_at', kind='datetime', default=None),
        Field('name'), Field('abbreviation'), Field('primary_color'),
        Field('support_email'), Field('logo_path'),
        Field('email_settings', kind='json'), Field('email_password_encrypted'),
        Field('email_revision', kind='int'), Field('diagnostic_results', kind='json'),
        Field('integration_settings', kind='json'),
        Field('display_settings', kind='json'), Field('is_reseller', kind='bool'),
        Field('self_hosted', kind='bool'),
        Field('support_multiple_associations', kind='bool'),
        Field('show_course_athletes', kind='bool'),
        Field('show_minor_exemption', kind='bool'),
        Field('show_tutorial', kind='bool'), Field('google_client_id'),
        Field('apple_client_id'), Field('stripe_public_key'),
        Field('stripe_secret_key'), Field('stripe_webhook_secret'),
        Field('stripe_pricing_table'), Field('stripe_client_portal'),
        Field('meta_title'), Field('meta_description'),
        Field('primary_association_id', kind='uuid', default=None, relation=True),
    ]),
    model('LogEntry', AUDIT, [
        Field('id', kind='int'), Field('content_type_id', kind='int', relation=True),
        Field('object_pk'), Field('object_id', kind='int', default=None),
        Field('object_repr'), Field('serialized_data', kind='json', default=None),
        Field('action', kind='int'), Field('changes_text', default=''),
        Field('changes', kind='json', default=None),
        Field('actor_id', kind='uuid', default=None, relation=True),
        Field('cid', default=None), Field('remote_addr', default=None),
        Field('remote_port', kind='int', default=None),
        Field('timestamp', kind='datetime'), Field('additional_data', kind='json', default=None),
        Field('actor_email', default=None),
    ]),
    model('AuditLogIndex', INDEX, [
        Field('audit_log_index_id', kind='uuid'),
        Field('log_entry_id', kind='int', relation=True),
        Field('sport_association_id', kind='uuid', relation=True),
        Field('resolution_path', default=None),
        Field('created_at', kind='datetime'),
    ]),
    model('TaskResult', TASK_RESULT, [
        Field('id', kind='int'), Field('task_id'), Field('periodic_task_name', default=None),
        Field('task_name', default=None), Field('task_args', default=None),
        Field('task_kwargs', default=None), Field('status'),
        Field('worker', default=None), Field('content_type'), Field('content_encoding'),
        Field('result'), Field('date_created', kind='datetime'),
        Field('date_done', kind='datetime'), Field('traceback', default=None),
        Field('meta', default=None),
    ]),
    model('ContentType', CONTENT_TYPE, [
        Field('id', kind='int'), Field('app_label'), Field('model'),
    ]),
    model('ContractControl', CONTROL, [Field('id'), Field('payload', kind='json')]),
])
MODEL_BY_LABEL = {item._meta.label: item for item in MODELS}
MODEL_BY_ARCHIVE_NAME = {
    item['name']: MODEL_BY_LABEL[LABELS[item['name']]]
    for item in MANIFEST
    if item['name'] != LEGACY_INVOICES
}
CONTENT_TYPE_IDS = {
    item._meta.label_lower: index + 1 for index, item in enumerate(MODELS)
}


def spec(name, archive=ARCHIVE):
    return next(
        item for item in archive['entries']['manifest.json']['models_exported']
        if item['name'] == name
    )


def make_model_row(label, values):
    result = {}
    model_class = MODEL_BY_LABEL[label]
    for field in model_class._meta.concrete_fields:
        if field.attname in values:
            result[field.attname] = field.to_python(deepcopy(values[field.attname]))
        elif field.has_default():
            result[field.attname] = field.to_python(field.get_default())
        elif field.attname == model_class._meta.pk.attname:
            result[field.attname] = uuid.uuid4()
        else:
            result[field.attname] = None
    return result


def make_row(model_name, values):
    return make_model_row(
        MODEL_BY_ARCHIVE_NAME[model_name]._meta.label,
        values,
    )


def initial_framework_rows():
    content_types = []
    permissions = []
    content_models = [
        item for item in MODELS
        if item._meta.managed and not item._meta.proxy and not item._meta.auto_created
    ]
    for index, item in enumerate(content_models, 1):
        content_types.append({
            'id': index,
            'app_label': item._meta.app_label,
            'model': item._meta.model_name,
        })
    permission_id = 1
    for item in content_models:
        content_type_id = next(
            row['id'] for row in content_types
            if row['app_label'] == item._meta.app_label
            and row['model'] == item._meta.model_name
        )
        for action in item._meta.default_permissions:
            permissions.append({
                'id': permission_id,
                'name': f'Can {action} {item._meta.verbose_name_raw}',
                'content_type_id': content_type_id,
                'codename': f'{action}_{item._meta.model_name}',
            })
            permission_id += 1
    return content_types, permissions


def baseline_rows():
    categories = []
    for index, (name, expense, deductible) in enumerate(PAYMENT_NAMES):
        categories.append(make_row('PaymentCategory', {
            'payment_category_id': uuid.UUID(int=index + 1, version=4),
            'name': name,
            'creation_date': MIGRATION_NOW,
            'sport_association_id': None,
            'expense': expense,
            'tax_deductible': deductible,
            'archived': False,
            'type': 1,
            'vat_management_id': None,
            'deleted': False,
        }))
    preview = make_row('PreviewAndCustomFeatures', {
        'preview_and_custom_features_id': uuid.UUID(int=99, version=4),
        'name': 'Iscrizioni per Enti e Federazioni',
        'description': 'Gestisci le iscrizioni per Enti e Federazioni',
    })
    return categories, preview


def materialize_import(archive=ARCHIVE):
    imported = {}
    for section in archive['entries']['manifest.json']['models_exported']:
        name = section['name']
        if name in RETIRED or name == LEGACY_INVOICES:
            continue
        rows = []
        for source in archive['entries'][section['file']]:
            values = {
                key: value for key, value in source.items()
                if not key.startswith('_m2m_')
            }
            if name == 'User':
                values.update({
                    'date_joined': IMPORT_NOW,
                    'last_login': None,
                    'is_staff': False,
                    'is_superuser': False,
                    'two_fa': False,
                    'two_fa_secret': None,
                    'stripe_account_id': None,
                    'stripe_on_boarding_completed': False,
                    'integration_google_state': None,
                    'integration_google_credentials': None,
                    'integration_google_auto_sync': False,
                })
                if not values.get('password'):
                    values['password'] = '!unusable-generated-password'
                if str(source.get('user_id')) == OWNER:
                    values.update({'role': 1, 'is_active': True})
            if name == 'Associate':
                values['sport_association_id'] = ASSOCIATION
            if name == 'Document':
                values.update({
                    'filepath': (
                        f'20261025_100000/'
                        f'{source["document_id"]}/historical.txt'
                    ),
                    'creation_date': IMPORT_NOW,
                })
            if name == 'Payment':
                values['creation_date'] = IMPORT_NOW
                if values.get('payment_category_id') is None:
                    # The source save() fills this from the migration seed;
                    # UUID(int=1, version=4) is independently authored above.
                    values['payment_category_id'] = uuid.UUID(int=1, version=4)
            if name == 'Subscription':
                owner = next(
                    row for row in archive['entries']['data/02_users.json']
                    if str(row['user_id']) == OWNER
                )
                duration = owner.get('subscription_duration', 2)
                start_day = owner.get('subscription_start_day', 1)
                start_month = owner.get('subscription_start_month', 1)
                creation_value = values.get('creation_date', IMPORT_NOW)
                if isinstance(creation_value, str):
                    creation_value = datetime.fromisoformat(creation_value.replace('Z', '+00:00'))
                creation = creation_value.date()
                current_start = date(creation.year, start_month, start_day)
                season_start = (
                    current_start if creation >= current_start
                    else date(creation.year - 1, start_month, start_day)
                )
                if owner.get('custom_end_date', False):
                    season_end = date(
                        season_start.year,
                        owner.get('subscription_end_month', 12),
                        owner.get('subscription_end_day', 31),
                    )
                    if season_end < season_start:
                        season_end = date(
                            season_end.year + 1,
                            season_end.month,
                            season_end.day,
                        )
                else:
                    season_end = (
                        date(season_start.year + 1, start_month, start_day)
                        - timedelta(days=1)
                    )
                if duration == 1:
                    start_date = creation
                    try:
                        end_date = date(creation.year + 1, creation.month, creation.day)
                    except ValueError:
                        end_date = date(creation.year + 1, creation.month, 28)
                elif duration == 2:
                    start_date, end_date = season_start, season_end
                elif duration == 3:
                    start_date, end_date = creation, season_end
                else:
                    start_date = end_date = None
                values['creation_date'] = creation_value
                if values.get('start_date') is None:
                    values['start_date'] = start_date
                if values.get('end_date') is None:
                    values['end_date'] = end_date
            if name == 'Message':
                values['created_at'] = IMPORT_NOW
            rows.append(make_row(name, values))
        imported[name] = rows
    return imported


def expected_stats(archive=ARCHIVE):
    stats = {}
    for name in IMPORT_ORDER:
        count = spec(name, archive)['count']
        if name == 'User':
            count -= 1
        stats[name] = count
    stats.update({
        'deferred_fks_resolved': 0,
        'deferred_fks_failed': 0,
        'm2m_resolved': 0,
        'm2m_failed': 0,
        'files_imported': len(archive['text_files']),
    })
    return stats


def owner_email(archive=ARCHIVE):
    users = archive['entries']['data/02_users.json']
    return next(row['email'] for row in users if str(row['user_id']) == OWNER)


def task_result_row(archive=ARCHIVE, **overrides):
    association = archive['entries']['data/01_sport_association.json'][0]
    result = {
        'success': True,
        'sport_association_id': ASSOCIATION,
        'denomination': association['denomination'],
        'owner_email': owner_email(archive),
        'stats': expected_stats(archive),
        'skipped_records': {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1},
        'warnings': [],
    }
    row = {
        'id': 1,
        'task_id': TASK_ID,
        'periodic_task_name': None,
        'task_name': None,
        'task_args': None,
        'task_kwargs': None,
        'status': 'SUCCESS',
        'worker': None,
        'content_type': 'application/json',
        'content_encoding': 'utf-8',
        'result': json.dumps(result),
        'date_created': IMPORT_START,
        'date_done': IMPORT_END,
        'traceback': None,
        'meta': json.dumps({'children': []}),
    }
    row.update(overrides)
    return row


def configuration_row(contract=CONTRACT):
    setup = contract['setup']['configure']
    oem = setup['oem']
    oauth = setup.get('oauth', {})
    return {
        'id': 1,
        'domain': setup['domain'].lower().strip(),
        'configured_at': CONFIGURE_NOW,
        'updated_at': CONFIGURE_NOW,
        'setup_provenance': 'import',
        'onboarding_completed_at': CONFIGURE_NOW,
        'name': oem['name'],
        'abbreviation': oem.get('abbreviation') or '',
        'primary_color': oem.get('primaryColor') or '#351DC2',
        'support_email': oem.get('supportEmail') or '',
        'logo_path': oem.get('logo') or '',
        'email_settings': {},
        'email_password_encrypted': '',
        'email_revision': 0,
        'diagnostic_results': {},
        'integration_settings': {},
        'display_settings': deepcopy(CONFIGURATION_DEFAULTS),
        'is_reseller': False,
        'self_hosted': True,
        'support_multiple_associations': False,
        'show_course_athletes': True,
        'show_minor_exemption': True,
        'show_tutorial': True,
        'google_client_id': oauth.get('googleClientId') or '',
        'apple_client_id': oauth.get('appleClientId') or '',
        'stripe_public_key': '',
        'stripe_secret_key': '',
        'stripe_webhook_secret': '',
        'stripe_pricing_table': '',
        'stripe_client_portal': '',
        'meta_title': '',
        'meta_description': '',
        # A values() snapshot contains the UUID FK target, unlike the JSON
        # archive/task payload. Do not share the oracle's conversion here.
        'primary_association_id': uuid.UUID(contract['ids']['association']),
    }


def audit_value(field, value):
    if field.kind == 'datetime' and isinstance(value, datetime):
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    if field.kind == 'json':
        return json.dumps(value, sort_keys=True)
    return str(value)


def create_changes(name, row):
    excluded = {'last_login', 'password'} if name == 'User' else set()
    changes = {}
    for field in MODEL_BY_ARCHIVE_NAME[name]._meta.concrete_fields:
        value = row[field.attname]
        if field.name in excluded:
            continue
        # Source-authored fixture policy: absent scalar values are unchanged;
        # nullable FK/datetime values compare raw None with the missing
        # instance's text "None" and consequently produce an identical pair.
        if value is None and field.kind != 'json' and not (
            field.is_relation or field.kind == 'datetime'
        ):
            continue
        if value == 'None' and field.kind != 'json':
            continue
        changes[field.name] = ['None', audit_value(field, value)]
    return changes


def audit_encoding_cases():
    """Literal old/new expectations, authored independently of the oracle."""
    from django.db import models as fields

    return [
        ('nullable text', fields.TextField(null=True), None, None),
        ('literal None text', fields.CharField(), 'None', None),
        ('empty text', fields.CharField(), '', ['None', '']),
        ('zero text default', fields.TextField(default=0), 0, ['None', '0']),
        ('zero integer', fields.IntegerField(), 0, ['None', '0']),
        ('false boolean', fields.BooleanField(), False, ['None', 'False']),
        ('true boolean', fields.BooleanField(), True, ['None', 'True']),
        ('nullable integer', fields.IntegerField(null=True), None, None),
        ('nullable date', fields.DateField(null=True), None, None),
        ('date', fields.DateField(), date(2026, 8, 1), ['None', '2026-08-01']),
        ('nullable decimal', fields.DecimalField(null=True), None, None),
        ('decimal scale', fields.DecimalField(), Decimal('12.00'), ['None', '12.00']),
        ('decimal float default', fields.DecimalField(default=0.0), 0.0, ['None', '0.0']),
        ('nullable json', fields.JSONField(null=True), None, ['None', 'null']),
        ('json text', fields.JSONField(), 'None', ['None', '"None"']),
        ('json false', fields.JSONField(), False, ['None', 'false']),
        ('json zero', fields.JSONField(), 0, ['None', '0']),
        ('json array', fields.JSONField(), [True, None, 1, 1.0],
         ['None', '[true, null, 1, 1.0]']),
        ('json object', fields.JSONField(), {'z': 'é', 'a': [False, None]},
         ['None', '{"a": [false, null], "z": "\\u00e9"}']),
        ('nullable datetime', fields.DateTimeField(null=True), None,
         ['None', 'None']),
        ('aware datetime', fields.DateTimeField(),
         datetime(2026, 8, 1, 3, 4, 5, 6, timezone(timedelta(hours=2))),
         ['None', '2026-08-01 01:04:05.000006']),
        ('naive datetime', fields.DateTimeField(), datetime(2026, 8, 1),
         ['None', '2026-08-01 00:00:00']),
        ('nullable fk', fields.ForeignKey('application.User', on_delete=fields.CASCADE,
                                        null=True), None, ['None', 'None']),
        ('uuid fk', fields.ForeignKey('application.User', on_delete=fields.CASCADE),
         uuid.UUID('00000000-0000-0000-0000-000000000123'),
         ['None', '00000000-0000-0000-0000-000000000123']),
        ('integer fk', fields.ForeignKey('contenttypes.ContentType',
                                       on_delete=fields.CASCADE), 7, ['None', '7']),
    ]


def event_rows(imported, archive=ARCHIVE):
    events = []
    owner = next(row for row in imported['User'] if str(row['user_id']) == OWNER)
    events.append(('User', owner, 0, None))
    events.append(('SportAssociation', imported['SportAssociation'][0], 0, None))
    for name in IMPORT_ORDER[1:]:
        for row in imported.get(name, []):
            if name == 'User' and str(row['user_id']) == OWNER:
                continue
            create_row = row
            if name == 'Document':
                create_row = deepcopy(row)
                source = next(
                    item for item in archive['entries']['data/99_documents.json']
                    if item['document_id'] == str(row['document_id'])
                )
                create_row['filepath'] = source['filepath']
            events.append((name, create_row, 0, None))
    document = imported['Document'][0]
    events.append(('Document', document, 1, {
        'filepath': ['legacy/historical.txt', document['filepath']],
    }))
    return events


def audit_rows(imported, archive=ARCHIVE):
    logs, indexes = [], []
    log_id = 1
    index_id = 1
    paths = {
        'SportAssociation': 'self',
        'Associate': 'direct',
        'Payment': 'direct',
        'Subscription': 'direct',
        'SportAssociationDocumentsArchive': 'direct',
        'Message': 'direct',
    }
    for name, row, action, changes in event_rows(imported, archive):
        if name not in AUDITED:
            continue
        model_class = MODEL_BY_ARCHIVE_NAME[name]
        object_pk = str(row[model_class._meta.pk.attname])
        if changes is None:
            changes = create_changes(name, row)
        log = {
            'id': log_id,
            'content_type_id': CONTENT_TYPE_IDS[LABELS[name].lower()],
            'object_pk': object_pk,
            'object_id': None,
            'object_repr': str(model_class(**row)),
            'serialized_data': None,
            'action': action,
            'changes_text': '',
            'changes': changes,
            'actor_id': None,
            'cid': None,
            'remote_addr': None,
            'remote_port': None,
            'timestamp': IMPORT_NOW,
            'additional_data': None,
            'actor_email': None,
        }
        logs.append(log)
        if name in paths:
            indexes.append({
                'audit_log_index_id': uuid.UUID(int=index_id, version=4),
                'log_entry_id': log_id,
                'sport_association_id': uuid.UUID(ASSOCIATION),
                'resolution_path': paths[name],
                'created_at': IMPORT_NOW,
            })
            index_id += 1
        log_id += 1
    return logs, indexes


def snapshots(archive=ARCHIVE, contract=CONTRACT):
    labels = [item._meta.label for item in MODELS]
    before = {label: [] for label in labels}
    categories, preview = baseline_rows()
    before[LABELS['PaymentCategory']] = categories
    before[LABELS['PreviewAndCustomFeatures']] = [preview]
    before[CONTENT_TYPE], before[PERMISSION] = initial_framework_rows()
    after = deepcopy(before)
    imported = materialize_import(archive)
    for name, rows in imported.items():
        after[LABELS[name]].extend(deepcopy(rows))
    after[CONFIG] = [configuration_row(contract)]
    after[AUDIT], after[INDEX] = audit_rows(imported, archive)
    after[TASK_RESULT] = [task_result_row(archive)]
    return before, after


def schema_only_population():
    """Author post-migrate rows independently of the verifier's derivation."""
    unmanaged = model('APILogsModel', 'drf_api_logger.APILogsModel', [
        Field('id', kind='int'),
    ], managed=False)
    unmanaged._meta.default_permissions = ('view',)
    unmanaged._meta.permissions = (('export_apilogsmodel', 'Export API logs'),)
    unmanaged._meta.verbose_name_raw = 'API log entry'
    unmanaged._meta.verbose_name = 'Voce del registro API'
    proxy = model('CourseProxy', 'application.CourseProxy', [
        Field('id', kind='int'),
    ], proxy=True)
    proxy._meta.default_permissions = ()
    proxy._meta.permissions = (('review_courseproxy', 'Review course proxy'),)
    swapped = model('User', 'auth.User', [
        Field('id', kind='int'),
    ], swapped='application.User')
    schema = tuple(MODELS) + (unmanaged, proxy, swapped)
    before, after = snapshots()
    content_types = [
        {'id': 1001, 'app_label': 'drf_api_logger', 'model': 'apilogsmodel'},
        {'id': 1002, 'app_label': 'application', 'model': 'courseproxy'},
    ]
    permissions = [
        {'id': 1001, 'name': 'Can view API log entry',
         'content_type_id': 1001, 'codename': 'view_apilogsmodel'},
        {'id': 1002, 'name': 'Export API logs',
         'content_type_id': 1001, 'codename': 'export_apilogsmodel'},
        {'id': 1003, 'name': 'Review course proxy',
         'content_type_id': 1002, 'codename': 'review_courseproxy'},
    ]
    for snapshot in (before, after):
        snapshot[CONTENT_TYPE].extend(deepcopy(content_types))
        snapshot[PERMISSION].extend(deepcopy(permissions))
    return before, after, schema


def derived_archive():
    archive = deepcopy(ARCHIVE)
    archive['entries']['data/01_sport_association.json'][0]['configuration'] = {
        'nested': {'enabled': True},
        'items': [1, True],
    }
    message_spec = spec('Message', archive)
    archive['entries'][message_spec['file']] = [{
        'message_id': 'aa9f72e4-1958-5d02-820f-f0bf60b52210',
        'sport_association_id': ASSOCIATION,
        'type': 'INSIDE_APP',
        'message': 'Derived source message',
        'subject': 'Source subject',
        'created_at': '2000-01-01T00:00:00Z',
    }]
    message_spec['count'] = 1
    return archive


def overlay_archive():
    archive = deepcopy(ARCHIVE)
    users = [
        {
            'user_id': 'f4b360ff-0fa7-58a2-bf1b-f2016dba1fac',
            'username': 'legacy-active-collaborator',
            'email': 'legacy-active-collaborator@example.test',
            'role': 3, 'first_name': 'Legacy', 'last_name': 'Active Collaborator',
            'phone': None, 'is_active': True, 'connected_user_id': OWNER,
            'collaborator_role': 1, 'collaborator_permissions': None,
            'deleted': False,
        },
        {
            'user_id': 'f4b360ff-0fa7-58a2-bf1b-f2016dba1fad',
            'username': 'legacy-inactive-collaborator',
            'email': 'legacy-inactive-collaborator@example.test',
            'role': 3, 'first_name': 'Legacy', 'last_name': 'Inactive Collaborator',
            'phone': '+39 055 000 0001', 'is_active': False,
            'connected_user_id': OWNER, 'collaborator_role': 3,
            'collaborator_permissions': {
                'calendar': True, 'access_level': 1, 'rate': 1.25,
                'large_integer': 9007199254740993, 'optional': None,
                'nested': [False, 2, 2.0],
            },
            'deleted': False,
        },
        {
            'user_id': 'f4b360ff-0fa7-58a2-bf1b-f2016dba1fae',
            'username': 'legacy-deleted-collaborator',
            'email': 'legacy-deleted-collaborator@example.test',
            'role': 3, 'first_name': 'Legacy', 'last_name': 'Deleted Collaborator',
            'phone': '', 'is_active': True, 'connected_user_id': OWNER,
            'collaborator_role': 3,
            'collaborator_permissions': ['association.calendar.read'],
            'deleted': True, 'delete_on': '2026-08-01',
        },
    ]
    invites = [
        {
            'collaboration_invite_id': 'f4b360ff-0fa7-58a2-bf1b-f2016dbac001',
            'user_id': OWNER, 'email': 'legacy-accepted@example.test',
            'creation_date': '2026-01-02T03:04:05Z', 'accepted': True,
            'expiration_date': None, 'token': 'synthetic-accepted-invite-token',
            'collaborator_role': 1, 'collaborator_permissions': None,
        },
        {
            'collaboration_invite_id': 'f4b360ff-0fa7-58a2-bf1b-f2016dbac002',
            'user_id': OWNER, 'email': 'legacy-pending@example.test',
            'creation_date': '2026-09-02T03:04:05Z', 'accepted': False,
            'expiration_date': '2099-12-31T23:59:59Z',
            'token': 'synthetic-pending-invite-token', 'collaborator_role': 3,
            'collaborator_permissions': {
                'other.users.collaborators.read': True,
                'nested': [1, False, 1.5, None],
            },
        },
        {
            'collaboration_invite_id': 'f4b360ff-0fa7-58a2-bf1b-f2016dbac003',
            'user_id': OWNER, 'email': 'legacy-expired@example.test',
            'creation_date': '2019-12-31T23:59:59.000006Z', 'accepted': False,
            'expiration_date': '2020-01-02T03:04:05.000006Z', 'token': None,
            'collaborator_role': 2, 'collaborator_permissions': [],
        },
    ]
    users_spec = spec('User', archive)
    invites_spec = spec('CollaborationInvites', archive)
    archive['entries'][users_spec['file']].extend(users)
    archive['entries'][invites_spec['file']].extend(invites)
    users_spec['count'] += 3
    invites_spec['count'] += 3
    return archive


def pinned_audit_source_controls():
    """Fresh process, real pinned fields; no app readiness, SQL or sockets.

    Expected payloads below are authored from archive/constructor/save semantics.
    Auditlog is consulted only afterward as an independent test reference.
    Neither this function nor auditlog is imported by the acceptance oracle.
    """
    import socket
    import environ
    from unittest.mock import patch
    from django.apps import AppConfig, apps
    from django.db.backends.base.base import BaseDatabaseWrapper

    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / 'BE'), str(root)]
    os.environ.clear()
    environ.Env.read_env(str(root / 'selfhost/.env.example'))
    os.environ.update(
        DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings',
        TESTS_DATABASE='assozeta_contract_metadata_only',
        JWT_SECRET_KEY='metadata-only', JWT_PUBLIC_KEY='metadata-only',
        JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='',
    )
    create = AppConfig.create

    def without_ready(entry):
        config = create(entry)
        config.ready = lambda: None
        return config

    def forbidden(*args, **kwargs):
        raise AssertionError('source controls forbid SQL and socket connections')

    with patch.object(environ.Env, 'read_env'), \
            patch.object(AppConfig, 'create', side_effect=without_ready), \
            patch.object(BaseDatabaseWrapper, 'ensure_connection', forbidden), \
            patch.object(socket.socket, 'connect', forbidden), \
            patch.object(socket, 'create_connection', forbidden):
        import django
        django.setup()
        from django.conf import settings
        from django.db import models as fields
        from auditlog.registry import auditlog
        from auditlog.diff import get_fields_in_model, model_instance_diff

        assert settings.USE_TZ is True
        assert settings.TIME_ZONE == 'UTC'
        assert settings.STORAGE_DIR == ''
        assert settings.STORAGES['default']['BACKEND'] == 'django.core.files.storage.FileSystemStorage'
        from core import settings as core_settings
        assert core_settings.STORAGE_DIR == ''
        assert settings.AUDITLOG_STORE_JSON_CHANGES is False
        assert settings.AUDITLOG_USE_FK_STRING_REPRESENTATION is False
        user = apps.get_model('application.User')
        assert auditlog.get_model_fields(user)['exclude_fields'] == ['last_login', 'password']
        models = list(apps.get_models(include_auto_created=True))
        metadata = population._metadata(models)
        archive = overlay_archive()
        imported, instances = {}, {}
        labels = {s['name']: LABELS[s['name']] for s in MANIFEST
                  if s['name'] not in RETIRED | {LEGACY_INVOICES}}
        seed = uuid.UUID(int=1, version=4)
        final_path = f'20261025_101500/{DOCUMENT}/historical.txt'
        # These reverse strings follow the declarations and BaseManager.__str__.
        # Spell them out so changes to field traversal cannot bless themselves.
        reverse = {
            'User': {'requester': 'application.SubscriptionTransfer.None',
                     'recipient': 'application.SubscriptionTransfer.None',
                     'saved_reports': 'application.SavedReport.None',
                     'social_auth': 'social_django.UserSocialAuth.None'},
            'SportAssociation': {'instance_config': 'instance.InstanceConfiguration.None'},
            'Associate': {'tutor_relations': 'application.AssociateTutorRelation.None',
                          'tutored_relations': 'application.AssociateTutorRelation.None'},
            'Document': {'customer_invoice_pdf': 'application.CustomerInvoice.None'},
        }
        for spec in MANIFEST:
            name = spec['name']
            if name not in labels:
                continue
            cls = apps.get_model(labels[name])
            imported[name] = []
            for source in archive['entries'][spec['file']]:
                values = {}
                for field in cls._meta.concrete_fields:
                    if field.attname not in source:
                        continue
                    value = source[field.attname]
                    parser = field.target_field if field.is_relation else field
                    kind = parser.get_internal_type()
                    if value is not None and kind in {
                        'UUIDField', 'DateField', 'DateTimeField', 'DecimalField',
                        'IntegerField', 'PositiveIntegerField', 'PositiveSmallIntegerField',
                    }:
                        value = parser.to_python(value)
                    values[field.attname] = value
                if name == 'User':
                    values.update(is_staff=False, is_superuser=False, two_fa=False,
                                  two_fa_secret=None, stripe_account_id=None,
                                  stripe_on_boarding_completed=False,
                                  integration_google_state=None,
                                  integration_google_credentials=None,
                                  integration_google_auto_sync=False)
                    if source['user_id'] == OWNER:
                        values.update(role=1, is_active=True)
                if name == 'Subscription':
                    values.update(start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
                if name == 'Payment':
                    values['payment_category_id'] = seed
                obj = cls(**values)
                for field in cls._meta.concrete_fields:
                    if (field.attname not in source and
                            field.get_internal_type() == 'DateTimeField'):
                        setattr(obj, field.attname, IMPORT_NOW)
                if name == 'User':
                    # Source event order proves this reverse object is absent.
                    # Cache that absence so the reference cannot issue SQL.
                    cls._meta.get_field('sportassociation').set_cached_value(obj, None)
                key = (name, str(obj.pk))
                instances[key] = obj
                snapshot = {}
                for field in cls._meta.concrete_fields:
                    parser = field.target_field if field.is_relation else field
                    value = parser.to_python(getattr(obj, field.attname))
                    if isinstance(value, Decimal):
                        value = value.quantize(Decimal(1).scaleb(-field.decimal_places))
                    snapshot[field.attname] = value
                if name == 'Document':
                    snapshot['filepath'] = final_path
                imported[name].append(snapshot)

        def authored_payload(name, obj):
            result = {}
            for field in obj._meta.concrete_fields:
                if name == 'User' and field.name in {'last_login', 'password'}:
                    continue
                value = getattr(obj, field.attname)
                kind = field.get_internal_type()
                if kind == 'JSONField':
                    encoded = json.dumps(value, sort_keys=True, cls=field.encoder)
                elif kind == 'DateTimeField':
                    encoded = ('None' if value is None else str(
                        value.astimezone(timezone.utc).replace(tzinfo=None)))
                elif field.is_relation:
                    encoded = str(value)
                elif value is None or str(value) == 'None':
                    continue
                else:
                    encoded = str(value)
                result[field.name] = ['None', encoded]
            result.update({key: ['None', value] for key, value in reverse.get(name, {}).items()})
            return result

        logs, indexes = [], []
        content_ids = {label.lower(): n for n, label in enumerate(labels.values(), 1)}
        scheduled = event_rows(imported, archive)
        audited_schedule = [(n, r, a, c) for n, r, a, c in scheduled if n in AUDITED]
        assert [n for n, _, _, _ in audited_schedule] == [
            'User', 'SportAssociation', 'User', 'User', 'User',
            'CollaborationInvites', 'CollaborationInvites', 'CollaborationInvites',
            'Associate', 'Document', 'Payment', 'Subscription',
            'SportAssociationDocumentsArchive', 'Document',
        ]
        for log_id, (name, row, action, update) in enumerate(audited_schedule, 1):
            cls = apps.get_model(labels[name])
            obj = instances[name, str(row[cls._meta.pk.attname])]
            expected = authored_payload(name, obj) if action == 0 else update
            # Expected is complete before invoking the reference library.
            if action == 0:
                reference = model_instance_diff(None, obj, use_json_for_changes=False)
                tracked = get_fields_in_model(obj)
                manager_names = {f.name for f in tracked if not f.concrete
                                 and f.one_to_many and f.get_accessor_name() == f.name}
                assert manager_names == set(reverse.get(name, {})), name
            else:
                final = cls(**row)
                reference = model_instance_diff(obj, final, fields_to_check=['filepath'],
                                                use_json_for_changes=False)
            reference = {k: list(v) for k, v in reference.items()}
            assert reference == expected, f'pinned reference payload differs: {name}'
            logs.append(dict(
                id=log_id, content_type_id=content_ids[labels[name].lower()],
                object_pk=str(obj.pk), object_id=None, object_repr=str(obj),
                serialized_data=None, action=action, changes_text='', changes=expected,
                actor_id=None, cid=None, remote_addr=None, remote_port=None,
                timestamp=IMPORT_NOW, additional_data=None, actor_email=None,
            ))
            if name in {'SportAssociation', 'Associate', 'Payment', 'Subscription',
                        'SportAssociationDocumentsArchive'}:
                indexes.append(dict(
                    audit_log_index_id=uuid.UUID(int=log_id, version=4), log_entry_id=log_id,
                    sport_association_id=uuid.UUID(ASSOCIATION),
                    resolution_path='self' if name == 'SportAssociation' else 'direct',
                    created_at=IMPORT_NOW,
                ))
        before = {AUDIT: [], INDEX: []}
        after = {AUDIT: logs, INDEX: indexes, CONTENT_TYPE: [
            dict(id=identity, app_label=label.split('.')[0], model=label.split('.')[1])
            for label, identity in content_ids.items()
        ]}
        field_config = {m._meta.label_lower: auditlog.get_model_fields(m)
                        for m in auditlog.get_models()}
        def verify_audits(snapshot):
            return population._audit_difference(
                before, snapshot, imported, labels, metadata, archive,
                (IMPORT_START, IMPORT_END), [(DOCUMENT, 'historical.txt')],
                auditlog.get_models(), field_config, RESOLVERS,
            )

        result = verify_audits(after)
        assert result[1:] == (14, 5)
        # Every independently authored event is necessary, including the rekey.
        # Replacing its old string with JSON null must fail at that exact event.
        for offset in range(14):
            changed = deepcopy(after)
            payload = changed[AUDIT][offset]['changes']
            name = next(iter(payload))
            payload[name][0] = None
            try:
                verify_audits(changed)
            except AssertionError as exc:
                assert f'audit changes differs for event {offset + 1} (' in str(exc)
            else:
                raise AssertionError(f'changed payload accepted: event {offset + 1}')
        for offset, log in enumerate(logs):
            for name in reverse.get(audited_schedule[offset][0], {}):
                if log['action'] != 0:
                    continue
                changed = deepcopy(after)
                del changed[AUDIT][offset]['changes'][name]
                try:
                    verify_audits(changed)
                except AssertionError as exc:
                    assert f'audit changes differs for event {offset + 1} (' in str(exc)
                else:
                    raise AssertionError(f'reverse field omission accepted: {name}')
        # Check the literal encoding table against the real library too.
        for number, (description, field, value, pair) in enumerate(audit_encoding_cases()):
            cls = type(f'Encoding{number}', (fields.Model,), {
                '__module__': __name__, 'sample': field,
                'Meta': type('Meta', (), {'app_label': 'application'}),
            })
            auditlog.register(cls, include_fields=['sample'])
            instance = cls(**{field.attname: value})
            reference = model_instance_diff(None, instance, use_json_for_changes=False)
            expected = None if pair is None else {'sample': tuple(pair)}
            assert reference == expected, description
        print(json.dumps({'logs': 14, 'indexes': 5, 'encoding_cases': number + 1}))


class HistoricalPopulationAcceptance(unittest.TestCase):
    def test_audit_change_diagnostics_never_include_values(self):
        summary = population._audit_change_summary(
            {'phone': ['None', 'private-expected-value'], 'email': ['None', 'secret']},
            {'phone': [None, 'private-actual-value'], 'unreviewed': 'private-extra-value'},
        )
        self.assertEqual(summary, "missing=['email']; extra_fields=1; "
                         "differing_types=['phone:list[str,str]/list[NoneType,str]']")
        self.assertEqual(population._audit_change_summary({}, 'private-value'),
                         'payload_type=str')

    def test_pinned_source_fields_and_all_fourteen_payloads_without_connections(self):
        result = subprocess.run(
            [sys.executable, '-B', '-c',
             'from TESTS.test_historical_read_population import pinned_audit_source_controls; '
             'pinned_audit_source_controls()'],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=45,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         {'logs': 14, 'indexes': 5, 'encoding_cases': 25})

    def test_real_django_fields_have_literal_create_encodings(self):
        for description, field, value, pair in audit_encoding_cases():
            with self.subTest(case=description):
                field.set_attributes_from_name('sample')
                item = {'label_lower': 'test.encoding', 'fields': {field.attname: field}}
                expected = {} if pair is None else {'sample': pair}
                self.assertEqual(population._create_changes(
                    item, {field.attname: value}, {},
                ), expected)

    def test_create_events_use_static_constructor_defaults_before_database_preparation(self):
        from django.db import models as fields

        def forbidden_default():
            raise AssertionError('dynamic defaults must not be evaluated twice')

        declarations = {
            'fee': fields.DecimalField(default=0.0),
            'number': fields.TextField(default=0),
            'created': fields.DateTimeField(default=forbidden_default),
            'token': fields.UUIDField(default=forbidden_default),
        }
        for name, field in declarations.items():
            field.set_attributes_from_name(name)
        item = {'name': 'SportAssociation', 'label_lower': 'application.sportassociation',
                'fields': declarations}
        row = {'fee': Decimal('0.00'), 'number': '0', 'created': IMPORT_NOW,
               'token': uuid.UUID(int=1, version=4)}
        expected = {
            'fee': ['None', '0.0'], 'number': ['None', '0'],
            'created': ['None', '2026-10-25 10:15:00'],
            'token': ['None', str(row['token'])],
        }
        self.assertEqual(population._event_changes(item, row, 0, {}, source={}), expected)
        # An archived Decimal keeps the exact imported scale, instead of using
        # the constructor default or converting it from a database float.
        expected['fee'] = ['None', '0.00']
        self.assertEqual(population._event_changes(
            item, row, 0, {}, source={'fee': '0.00'},
        ), expected)
        role = fields.PositiveSmallIntegerField(default=2)
        role.set_attributes_from_name('role')
        user = {'name': 'User', 'label_lower': 'application.user', 'fields': {'role': role}}
        self.assertEqual(population._event_changes(user, {'role': 1}, 0, {}, source={}),
                         {'role': ['None', '1']})

    def verify(
        self, before=None, after=None, archive=ARCHIVE, contract=CONTRACT,
        skipped_records=None, **overrides,
    ):
        if before is None or after is None:
            before, after = snapshots(archive, contract)
        arguments = {
            'schema_models': MODELS,
            'skipped_records': {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1},
            'task_id': TASK_ID,
            'migration_started_at': MIGRATION_START,
            'migration_finished_at': MIGRATION_END,
            'import_started_at': IMPORT_START,
            'import_finished_at': IMPORT_END,
            'configuration_started_at': CONFIGURE_START,
            'configuration_finished_at': CONFIGURE_END,
            'configuration_defaults': CONFIGURATION_DEFAULTS,
            'audited_models': [MODEL_BY_ARCHIVE_NAME[name] for name in AUDITED],
            'audit_fields': AUDIT_FIELDS,
            'resolvers': RESOLVERS,
            'archive_model_labels': {'Group': 'application.Group'},
        }
        if skipped_records is not None:
            arguments['skipped_records'] = skipped_records
        arguments.update(overrides)
        return population.verify_exact_population(
            before, after, archive, contract, MODELS, **arguments
        )

    def test_source_fixture_shape_and_exact_business_population(self):
        self.assertEqual(len(MANIFEST), 67)
        imported = [
            item for item in MANIFEST
            if item['name'] not in RETIRED | {LEGACY_INVOICES}
        ]
        self.assertEqual(len(imported), 64)
        self.assertEqual(sum(item['count'] > 0 for item in imported), 7)
        self.assertEqual(sum(item['count'] == 0 for item in imported), 57)
        receipt = self.verify()
        self.assertTrue(receipt['exact'])
        self.assertEqual(receipt['archive_sections'], 67)
        self.assertEqual(receipt['imported_rows'], 7)
        self.assertEqual(receipt['allowed_new_rows'][AUDIT], 8)
        self.assertEqual(receipt['allowed_new_rows'][INDEX], 5)

    def test_phase_windows_are_explicit_and_must_be_ordered(self):
        invalid = (
            {
                'migration_started_at': MIGRATION_END,
                'migration_finished_at': MIGRATION_START,
            },
            {'import_started_at': MIGRATION_START},
            {'configuration_started_at': IMPORT_START},
            {
                'configuration_started_at': CONFIGURE_END,
                'configuration_finished_at': CONFIGURE_START,
            },
        )
        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(AssertionError, 'window|ordered'):
                    self.verify(**overrides)

    def test_audit_registry_fields_and_resolvers_are_source_inputs(self):
        archive = overlay_archive()
        with self.assertRaisesRegex(AssertionError, 'audit LogEntry population'):
            self.verify(archive=archive, audited_models=())
        with self.assertRaisesRegex(AssertionError, 'audit'):
            self.verify(archive=archive, resolvers={})
        fields = deepcopy(AUDIT_FIELDS)
        fields[LABELS['User'].lower()]['exclude_fields'] = []
        with self.assertRaisesRegex(AssertionError, 'audit'):
            self.verify(archive=archive, audit_fields=fields)

    def test_duplicate_audited_class_names_keep_qualified_registry_keys(self):
        labels = population._registry_labels([
            MODEL_BY_LABEL['auth.Group'],
            MODEL_BY_LABEL['application.Group'],
        ])
        self.assertEqual(labels, {'auth.group', 'application.group'})

    def test_json_conversion_decimal_scale_and_derived_auto_timestamp(self):
        archive = derived_archive()
        before, after = snapshots(archive)
        self.assertEqual(
            self.verify(before, after, archive)['allowed_new_rows'][INDEX], 6
        )

        before, after = snapshots(archive)
        after[LABELS['Payment']][0]['amount'] = Decimal('25.0')
        with self.assertRaisesRegex(AssertionError, 'archived field'):
            self.verify(before, after, archive)

        before, after = snapshots(archive)
        after[LABELS['SportAssociation']][0]['configuration']['nested']['enabled'] = 1
        with self.assertRaisesRegex(AssertionError, 'archived field'):
            self.verify(before, after, archive)

        before, after = snapshots(archive)
        after[LABELS['Message']][0]['created_at'] = datetime(
            2000, 1, 1, tzinfo=timezone.utc
        )
        with self.assertRaisesRegex(AssertionError, 'generated time'):
            self.verify(before, after, archive)

    def test_subscription_number_omitted_default_is_exact_database_text(self):
        source = ARCHIVE['entries']['data/25_subscriptions.json'][0]
        self.assertNotIn('subscription_number', source)
        before, after = snapshots()
        self.assertEqual(after[LABELS['Subscription']][0]['subscription_number'], '0')
        self.assertTrue(self.verify(before, after)['exact'])
        for value in (0, None, '', '00', '1'):
            with self.subTest(value=value):
                before, after = snapshots()
                after[LABELS['Subscription']][0]['subscription_number'] = value
                with self.assertRaisesRegex(AssertionError, 'Subscription.subscription_number'):
                    self.verify(before, after)

    def test_subscription_number_explicit_values_are_not_replaced_by_default(self):
        for value in ('0007', 7, None):
            with self.subTest(value=value):
                archive = deepcopy(ARCHIVE)
                archive['entries']['data/25_subscriptions.json'][0]['subscription_number'] = value
                before, after = snapshots(archive)
                expected = None if value is None else str(value)
                self.assertEqual(after[LABELS['Subscription']][0]['subscription_number'], expected)
                self.assertTrue(self.verify(before, after, archive)['exact'])

    def test_payment_save_category_is_the_exact_validated_migration_seed(self):
        before, after = snapshots()
        self.assertTrue(self.verify(before, after)['exact'])
        seed = before[LABELS['PaymentCategory']][0]['payment_category_id']
        for value in (None, before[LABELS['PaymentCategory']][1]['payment_category_id'], uuid.uuid4()):
            with self.subTest(value=value):
                before, after = snapshots()
                after[LABELS['Payment']][0]['payment_category_id'] = value
                with self.assertRaisesRegex(AssertionError, 'Payment.payment_category_id'):
                    self.verify(before, after)

        # Generated migration UUIDs may differ; selection is by the source seed
        # identity/name, independently validated before this relationship.
        before, after = snapshots()
        replacement = uuid.uuid4()
        before[LABELS['PaymentCategory']][0]['payment_category_id'] = replacement
        after[LABELS['PaymentCategory']][0]['payment_category_id'] = replacement
        after[LABELS['Payment']][0]['payment_category_id'] = replacement
        for log in after[AUDIT]:
            if log['object_pk'] == str(after[LABELS['Payment']][0]['payment_id']):
                self.assertEqual(log['changes']['payment_category'], ['None', str(seed)])
                log['changes']['payment_category'] = ['None', str(replacement)]
        self.assertTrue(self.verify(before, after)['exact'])

    def test_subscription_number_and_payment_category_audits_remain_exact(self):
        for name, field, value in (
            ('Subscription', 'subscription_number', '00'),
            ('Payment', 'payment_category', 'None'),
        ):
            with self.subTest(name=name):
                before, after = snapshots()
                pk = MODEL_BY_ARCHIVE_NAME[name]._meta.pk.attname
                identity = str(after[LABELS[name]][0][pk])
                log = next(row for row in after[AUDIT] if row['object_pk'] == identity)
                log['changes'][field] = ['None', value]
                with self.assertRaisesRegex(AssertionError, 'audit.*changes'):
                    self.verify(before, after)

    def test_payment_save_does_not_infer_category_when_imported_candidates_exist(self):
        archive = deepcopy(ARCHIVE)
        category = {
            'payment_category_id': str(uuid.uuid4()),
            'name': PAYMENT_NAMES[0][0],
            'creation_date': '2026-01-01T00:00:00Z',
        }
        section = spec('PaymentCategory', archive)
        archive['entries'][section['file']] = [category]
        section['count'] = 1
        with self.assertRaisesRegex(AssertionError, 'category selection.*unreviewed'):
            self.verify(archive=archive)

    def test_subscription_save_preserves_archived_dates_independently(self):
        for start, end in (
            ('2020-03-04', '2021-05-06'),
            ('2020-03-04', None),
            (None, '2021-05-06'),
        ):
            with self.subTest(start=start, end=end):
                archive = deepcopy(ARCHIVE)
                source = archive['entries']['data/25_subscriptions.json'][0]
                source.update(start_date=start, end_date=end)
                before, after = snapshots(archive)
                self.assertTrue(self.verify(before, after, archive)['exact'])
                for field, value in (('start_date', start), ('end_date', end)):
                    if value is not None:
                        self.assertEqual(after[LABELS['Subscription']][0][field], date.fromisoformat(value))
                        after[LABELS['Subscription']][0][field] += timedelta(days=1)
                        with self.assertRaisesRegex(AssertionError, 'archived field'):
                            self.verify(before, after, archive)
                        after[LABELS['Subscription']][0][field] -= timedelta(days=1)

    def test_pinned_hook_assignment_inventory_and_subscription_number_declaration(self):
        root = Path(__file__).resolve().parents[1]
        for relative, name, expected in (
            ('application/models/subscriptions_models.py', 'Subscription',
             {'creation_date', 'start_date', 'end_date'}),
            ('application/models/payment_models.py', 'Payment',
             {'payment_category', 'description', 'custom_accounts'}),
            ('application/models/courses_models.py', 'Course', set()),
            ('instance/models.py', 'InstanceConfiguration', {'display_settings'}),
        ):
            with self.subTest(model=name):
                tree = ast.parse((root / 'BE' / relative).read_text())
                model_node = next(node for node in tree.body
                                  if isinstance(node, ast.ClassDef) and node.name == name)
                save = next(node for node in model_node.body
                            if isinstance(node, ast.FunctionDef) and node.name == 'save')
                assigned = {
                    target.attr for node in ast.walk(save) if isinstance(node, ast.Assign)
                    for target in node.targets if isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name) and target.value.id == 'self'
                }
                self.assertEqual(assigned, expected)
                if name == 'Subscription':
                    declaration = next(node.value for node in model_node.body
                                       if isinstance(node, ast.Assign)
                                       and any(isinstance(target, ast.Name)
                                               and target.id == 'subscription_number'
                                               for target in node.targets))
                    self.assertEqual(declaration.func.attr, 'TextField')
                    self.assertEqual(next(ast.literal_eval(keyword.value)
                                          for keyword in declaration.keywords
                                          if keyword.arg == 'default'), 0)

    def test_subscription_save_hook_dates_are_derived_from_owner_settings(self):
        for field, value in (
            ('start_date', date(2026, 1, 2)),
            ('end_date', date(2027, 1, 1)),
        ):
            with self.subTest(field=field):
                before, after = snapshots()
                after[LABELS['Subscription']][0][field] = value
                with self.assertRaisesRegex(AssertionError, 'Subscription.save'):
                    self.verify(before, after)

        for duration in (1, 2, 3):
            with self.subTest(duration=duration):
                archive = deepcopy(ARCHIVE)
                owner = next(
                    row for row in archive['entries']['data/02_users.json']
                    if str(row['user_id']) == OWNER
                )
                owner['subscription_duration'] = duration
                before, after = snapshots(archive)
                self.assertTrue(self.verify(before, after, archive)['exact'])

        archive = deepcopy(ARCHIVE)
        subscription = archive['entries']['data/25_subscriptions.json'][0]
        subscription['start_date'] = None
        subscription['end_date'] = None
        before, after = snapshots(archive)
        self.assertTrue(self.verify(before, after, archive)['exact'])

    def test_overlay_users_and_invites_have_exact_audits_without_user_indexes(self):
        archive = overlay_archive()
        before, after = snapshots(archive)
        receipt = self.verify(before, after, archive)
        self.assertEqual(receipt['task_result']['stats']['User'], 3)
        self.assertEqual(receipt['task_result']['stats']['CollaborationInvites'], 3)
        self.assertEqual(receipt['allowed_new_rows'][AUDIT], 14)
        self.assertEqual(receipt['allowed_new_rows'][INDEX], 5)

    def test_document_matching_path_and_audit_cannot_authorize_wrong_storage_prefix(self):
        archive = overlay_archive()
        for prefix in ('20000101_000000', 'unrelated-prefix'):
            with self.subTest(prefix=prefix):
                before, after = snapshots(archive)
                bad = f'{prefix}/{DOCUMENT}/historical.txt'
                after[LABELS['Document']][0]['filepath'] = bad
                update = next(log for log in after[AUDIT]
                              if log['object_pk'] == DOCUMENT and log['action'] == 1)
                update['changes']['filepath'][1] = bad
                with self.assertRaisesRegex(AssertionError, 'Document storage'):
                    self.verify(before, after, archive)

    def test_document_raw_path_and_matching_audit_reject_noncanonical_encodings(self):
        archive = overlay_archive()
        suffix = f'{DOCUMENT}/historical.txt'
        timestamp = '20261025_101500'
        bad_paths = [
            # Valid timestamps outside the import's encoded-second window.
            f'{stamp}/{suffix}' for stamp in ('20261025_095959', '20261025_103001')
        ] + [
            # Length, ASCII digits and calendar validity are independent checks.
            f'{stamp}/{suffix}' for stamp in (
                '2026105_101500', '20261025_11500', '20261025101500',
                '20261025-101500', '20261025_101500Z', '20261025_101500.123456',
                ' 20261025_101500', '20261025_101500\n',
                '２０２６１０２５_１０１５００', '00001025_101500',
                '20260229_101500', '20261325_101500', '20261032_101500',
                '20261025_241500', '20261025_106000', '20261025_101560',
            )
        ] + [
            f'/storage/{timestamp}/{suffix}', f'prefix/{timestamp}/{suffix}',
            f'/{timestamp}/{suffix}', f'./{timestamp}/{suffix}',
            f'{timestamp}//{suffix}', f'{timestamp}/./{suffix}',
            f'{timestamp}/../{suffix}', f'{timestamp}/{DOCUMENT}/./historical.txt',
            f'{timestamp}/{DOCUMENT}//historical.txt',
            f'{timestamp}/{DOCUMENT}/historical.txt/',
            f'{timestamp}\\{DOCUMENT}/historical.txt',
            f'{timestamp}/{DOCUMENT.upper()}/historical.txt',
            f'{timestamp}/{DOCUMENT}/other.txt',
            f'{timestamp}/{DOCUMENT}/%68istorical.txt',
            '', None,
        ]
        for bad in bad_paths:
            with self.subTest(path=bad):
                before, after = snapshots(archive)
                after[LABELS['Document']][0]['filepath'] = bad
                update = next(log for log in after[AUDIT]
                              if log['object_pk'] == DOCUMENT and log['action'] == 1)
                update['changes']['filepath'][1] = bad
                with self.assertRaisesRegex(AssertionError, 'Document (storage|filepath)'):
                    self.verify(before, after, archive)

    def test_document_canonical_window_boundaries_keep_fourteen_logs_and_five_indexes(self):
        archive = overlay_archive()
        for timestamp in ('20261025_100000', '20261025_101500', '20261025_103000'):
            with self.subTest(timestamp=timestamp):
                before, after = snapshots(archive)
                canonical = f'{timestamp}/{DOCUMENT}/historical.txt'
                after[LABELS['Document']][0]['filepath'] = canonical
                update = next(log for log in after[AUDIT]
                              if log['object_pk'] == DOCUMENT and log['action'] == 1)
                update['changes']['filepath'][1] = canonical
                receipt = self.verify(before, after, archive)
                self.assertTrue(receipt['exact'])
                self.assertEqual(receipt['imported_rows'], 13)
                self.assertEqual(receipt['allowed_new_rows'][AUDIT], 14)
                self.assertEqual(receipt['allowed_new_rows'][INDEX], 5)
                # A canonical document cannot bless a different audit value.
                update['changes']['filepath'][1] = f'prefix/{canonical}'
                with self.assertRaisesRegex(AssertionError, 'audit changes differs'):
                    self.verify(before, after, archive)

    def test_document_timestamp_uses_source_second_truncation_and_utc(self):
        cases = (
            # Start truncates down, finish never rounds into the next second.
            ('2026-10-25T10:00:00.999999Z', '2026-10-25T10:00:01.000001Z',
             ('20261025_100000', '20261025_100001')),
            ('2026-10-25T10:00:00.400000Z', '2026-10-25T10:00:00.450000Z',
             ('20261025_100000',)),
            # UTC conversion precedes truncation, independent of local date.
            ('2026-10-26T00:00:00.100000+02:00', '2026-10-26T00:00:00.900000+02:00',
             ('20261025_220000',)),
            ('2025-12-31T23:59:59.999999Z', '2026-01-01T00:00:00.000001Z',
             ('20251231_235959', '20260101_000000')),
            ('2024-02-29T23:59:59.999999Z', '2024-03-01T00:00:00Z',
             ('20240229_235959', '20240301_000000')),
        )
        for start, finish, stamps in cases:
            window = population._time_window(start, finish, 'import')
            for timestamp in stamps:
                with self.subTest(start=start, finish=finish, timestamp=timestamp):
                    canonical = f'{timestamp}/{DOCUMENT}/historical.txt'
                    self.assertEqual(population._filepath_policy(
                        canonical, DOCUMENT, 'historical.txt', window,
                    ), canonical)
            for outside in (window[0] - timedelta(seconds=1),
                            window[1] + timedelta(seconds=1)):
                bad = f'{outside.strftime("%Y%m%d_%H%M%S")}/{DOCUMENT}/historical.txt'
                with self.subTest(start=start, finish=finish, outside=outside):
                    with self.assertRaisesRegex(AssertionError, 'outside import'):
                        population._filepath_policy(bad, DOCUMENT, 'historical.txt', window)

    def test_document_storage_layout_is_pinned_to_importer_source(self):
        root = Path(__file__).resolve().parents[1]
        tree = ast.parse((root / 'BE/application/services/import_service.py').read_text())
        method = next(node for node in ast.walk(tree)
                      if isinstance(node, ast.FunctionDef)
                      and node.name == '_import_document_file')
        assignments = {
            target.id: node.value for node in method.body if isinstance(node, ast.Assign)
            for target in node.targets if isinstance(target, ast.Name)
        }
        self.assertEqual(ast.dump(assignments['timestamp']), ast.dump(ast.parse(
            "timezone.now().strftime('%Y%m%d_%H%M%S')", mode='eval').body))
        branch = next(node for node in method.body if isinstance(node, ast.If)
                      and isinstance(node.test, ast.Name) and node.test.id == 'STORAGE_DIR')
        self.assertEqual(ast.dump(branch.orelse[0]), ast.dump(ast.parse(
            'storage_path = f"{timestamp}/{doc.document_id}/{filename}"').body[0]))

    def test_extra_rows_rejected_in_every_empty_business_section(self):
        for section in MANIFEST:
            if section['count'] or section['name'] in RETIRED | {LEGACY_INVOICES}:
                continue
            with self.subTest(model=section['name']):
                before, after = snapshots()
                pk_name = MODEL_BY_ARCHIVE_NAME[section['name']]._meta.pk.attname
                after[LABELS[section['name']]].append(make_row(
                    section['name'], {pk_name: uuid.uuid4()}
                ))
                with self.assertRaisesRegex(AssertionError, 'population'):
                    self.verify(before, after)

    def test_unauthorized_baseline_rows_cannot_authorize_extra_business_rows(self):
        for name, pk, value in (
            ('User', 'user_id', uuid.uuid4()),
            ('Payment', 'payment_id', uuid.uuid4()),
            ('Course', 'course_id', uuid.uuid4()),
        ):
            with self.subTest(model=name):
                before, after = snapshots()
                extra = make_row(name, {pk: value})
                before[LABELS[name]].append(deepcopy(extra))
                after[LABELS[name]].append(deepcopy(extra))
                with self.assertRaisesRegex(AssertionError, 'baseline'):
                    self.verify(before, after)

    def test_migration_authored_baseline_is_exact(self):
        before, after = snapshots()
        category_label = LABELS['PaymentCategory']
        self.assertEqual(len(before[category_label]), 17)
        self.assertEqual(self.verify(before, after)['baseline_payment_categories'], 17)

        before, after = snapshots()
        extra = make_row('PaymentCategory', {
            'payment_category_id': uuid.uuid4(),
            'name': 'Unauthorized category',
            'creation_date': MIGRATION_NOW,
            'sport_association_id': None,
            'expense': False,
            'tax_deductible': False,
            'archived': False,
            'type': 1,
            'vat_management_id': None,
            'deleted': False,
        })
        before[category_label].append(deepcopy(extra))
        after[category_label].append(deepcopy(extra))
        with self.assertRaisesRegex(AssertionError, 'baseline'):
            self.verify(before, after)

        before, after = snapshots()
        before[category_label][0]['creation_date'] = IMPORT_NOW
        after[category_label][0]['creation_date'] = IMPORT_NOW
        with self.assertRaisesRegex(AssertionError, 'baseline'):
            self.verify(before, after)

        before, after = snapshots()
        after[category_label][0]['creation_date'] = IMPORT_NOW
        with self.assertRaisesRegex(AssertionError, 'baseline'):
            self.verify(before, after)

    def test_archived_ids_relationships_and_retired_counts_are_exact(self):
        before, after = snapshots()
        after[LABELS['Subscription']] = []
        with self.assertRaisesRegex(AssertionError, 'population'):
            self.verify(before, after)

        before, after = snapshots()
        after[LABELS['Subscription']][0]['associate_id'] = uuid.uuid4()
        with self.assertRaisesRegex(AssertionError, 'archived field'):
            self.verify(before, after)

        for value in (
            {},
            {'BillingPlan': 1},
            {'BillingPlan': 2, 'PreviewAndCustomFeatures': 1},
            {'BillingPlan': 1, 'PreviewAndCustomFeatures': True},
            {'BillingPlan': 1, 'PreviewAndCustomFeatures': 1, 'User': 1},
        ):
            with self.subTest(skipped=value):
                with self.assertRaises(AssertionError):
                    self.verify(skipped_records=value)

    def test_configuration_all_concrete_fields_are_source_exact(self):
        mutations = (
            ('domain', 'other.example.test'),
            ('email_settings', {'unexpected': True}),
            ('display_settings', {}),
            ('google_client_id', 'foreign-google-client'),
            ('stripe_secret_key', 'persisted-secret'),
            ('self_hosted', False),
            ('show_tutorial', False),
            ('meta_title', 'not supplied'),
            ('primary_association_id', uuid.uuid4()),
            ('configured_at', IMPORT_NOW),
            ('onboarding_completed_at', None),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                before, after = snapshots()
                after[CONFIG][0][field] = value
                with self.assertRaisesRegex(AssertionError, 'configuration'):
                    self.verify(before, after)

    def test_database_fixture_uses_typed_configuration_and_audit_relations(self):
        before, after = snapshots()
        self.assertIs(type(CONTRACT['ids']['association']), str)
        self.assertIs(type(after[CONFIG][0]['primary_association_id']), uuid.UUID)
        for row in after[INDEX]:
            self.assertIs(type(row['sport_association_id']), uuid.UUID)
            self.assertIs(type(row['log_entry_id']), int)
        for row in after[AUDIT]:
            self.assertIs(type(row['object_pk']), str)
            self.assertIs(type(row['content_type_id']), int)
            self.assertIs(type(row['changes']), dict)
            self.assertIsNone(row['object_id'])  # audited objects have UUID PKs
        self.assertIs(type(after[TASK_RESULT][0]['result']), str)
        self.assertTrue(self.verify(before, after)['exact'])

    def test_configuration_and_audit_field_types_match_pinned_declarations(self):
        import auditlog

        root = Path(__file__).resolve().parents[1]
        # Read declarations without importing application models or Django setup.
        for path, name, label, relations in (
            (root / 'BE/instance/models.py', 'InstanceConfiguration', CONFIG,
             {'primary_association': 'uuid'}),
            (root / 'BE/application/models/audit_models.py', 'AuditLogIndex', INDEX,
             {'sport_association': 'uuid', 'log_entry': 'int'}),
            (Path(auditlog.__file__).with_name('models.py'), 'AbstractLogEntry', AUDIT,
             {'content_type': 'int', 'actor': 'uuid'}),
        ):
            tree = ast.parse(path.read_text())
            node = next(node for node in tree.body
                        if isinstance(node, ast.ClassDef) and node.name == name)
            declarations = {
                target.id: statement.value.func.attr
                for statement in node.body if isinstance(statement, ast.Assign)
                and isinstance(statement.value, ast.Call)
                and isinstance(statement.value.func, ast.Attribute)
                and isinstance(statement.value.func.value, ast.Name)
                and statement.value.func.value.id == 'models'
                for target in statement.targets if isinstance(target, ast.Name)
            }
            kinds = {
                'CharField': 'str', 'TextField': 'str', 'EmailField': 'str',
                'URLField': 'str', 'JSONField': 'json', 'BooleanField': 'bool',
                'DateTimeField': 'datetime', 'PositiveIntegerField': 'int',
                'UUIDField': 'uuid', 'BigIntegerField': 'int',
                'PositiveSmallIntegerField': 'int', 'GenericIPAddressField': 'str',
            }
            expected = {'id': 'int'} if label in (CONFIG, AUDIT) else {}
            for field, field_type in declarations.items():
                if field in relations:
                    self.assertIn(field_type, ('ForeignKey', 'OneToOneField'))
                    expected[field + '_id'] = relations[field]
                else:
                    expected[field] = kinds[field_type]
            actual = {field.attname: field.kind
                      for field in MODEL_BY_LABEL[label]._meta.concrete_fields}
            self.assertEqual(actual, expected)
        association_source = ast.parse(
            (root / 'BE/application/models/user_models.py').read_text()
        )
        association = next(node for node in association_source.body
                           if isinstance(node, ast.ClassDef)
                           and node.name == 'SportAssociation')
        pk = next(statement.value for statement in association.body
                  if isinstance(statement, ast.Assign)
                  and any(isinstance(target, ast.Name)
                          and target.id == 'sport_association_id'
                          for target in statement.targets))
        self.assertEqual(pk.func.attr, 'UUIDField')

    def test_configuration_expectations_use_real_django_field_parsers(self):
        from django.db import models as django_fields

        metadata = population._metadata(MODELS)
        fields = metadata[CONFIG]['fields'].copy()
        # Use a distinct FK wrapper: its own parser must not be used in place
        # of the target's real Django UUIDField parser.
        class AssociationFK:
            target_field = django_fields.UUIDField()

            def to_python(self, value):
                raise AssertionError('configuration must parse the FK target')

        fields['primary_association_id'] = AssociationFK()
        for name, field in list(fields.items()):
            if name == 'primary_association_id':
                continue
            field_class = {
                'str': django_fields.TextField, 'int': django_fields.IntegerField,
                'bool': django_fields.BooleanField, 'json': django_fields.JSONField,
                'datetime': django_fields.DateTimeField,
            }[field.kind]
            fields[name] = field_class()
        metadata[CONFIG]['fields'] = fields
        before, after = snapshots()
        receipt = population._configuration_difference(
            before, after, CONTRACT, CONFIGURATION_DEFAULTS,
            (CONFIGURE_START, CONFIGURE_END), metadata,
        )
        self.assertEqual(receipt['association_id'], ASSOCIATION)
        self.assertIs(type(after[CONFIG][0]['primary_association_id']), uuid.UUID)

    def test_configuration_and_audit_reject_unreviewed_concrete_fields(self):
        from unittest.mock import patch

        for label in (CONFIG, AUDIT, INDEX):
            with self.subTest(label=label):
                before, after = snapshots()
                for row in after[label]:
                    row['unreviewed'] = None
                meta = MODEL_BY_LABEL[label]._meta
                with patch.object(meta, 'concrete_fields', [
                    *meta.concrete_fields, Field('unreviewed', default=None),
                ]):
                    with self.assertRaisesRegex(AssertionError, 'unreviewed concrete field'):
                        self.verify(before, after)

    def test_configuration_rejects_equal_text_uuid_and_scalar_type_changes(self):
        for field, value in (
            ('primary_association_id', ASSOCIATION),
            ('email_revision', False), ('self_hosted', 1),
            ('display_settings', json.dumps(CONFIGURATION_DEFAULTS)),
            ('email_settings', []), ('diagnostic_results', ' {}'),
            ('integration_settings', []),
        ):
            with self.subTest(field=field):
                before, after = snapshots()
                after[CONFIG][0][field] = value
                with self.assertRaisesRegex(AssertionError, f'configuration field differs: {field}'):
                    self.verify(before, after)

        before, after = snapshots()
        after[CONFIG][0]['display_settings']['general']['searchBar'] = 1
        with self.assertRaisesRegex(AssertionError, 'configuration field differs: display_settings'):
            self.verify(before, after)

    def test_audit_index_rejects_equal_text_uuid_and_numeric_link_types(self):
        for field, value in (
            ('sport_association_id', ASSOCIATION),
            ('log_entry_id', '2'), ('log_entry_id', 2.0),
            ('audit_log_index_id', str(uuid.UUID(int=1, version=4))),
        ):
            with self.subTest(field=field, value=value):
                before, after = snapshots()
                after[INDEX][0][field] = value
                with self.assertRaisesRegex(AssertionError, 'audit'):
                    self.verify(before, after)

    def test_configuration_and_audit_lifecycle_fields_require_datetimes(self):
        # Midnight is inside these windows, so replacing a datetime with a
        # same-day date must fail on its database type, not its numeric bounds.
        for label, field, stamp in (
            (CONFIG, 'configured_at', CONFIGURE_NOW + timedelta(days=2)),
            (CONFIG, 'updated_at', CONFIGURE_NOW + timedelta(days=2)),
            (CONFIG, 'onboarding_completed_at', CONFIGURE_NOW + timedelta(days=2)),
            (AUDIT, 'timestamp', IMPORT_NOW + timedelta(days=1)),
            (INDEX, 'created_at', IMPORT_NOW + timedelta(days=1)),
        ):
            with self.subTest(label=label, field=field):
                before, after = snapshots()
                for name in ('configured_at', 'updated_at', 'onboarding_completed_at'):
                    after[CONFIG][0][name] = (CONFIGURE_START + timedelta(days=2)).replace(hour=0)
                windows = {
                    'import_finished_at': IMPORT_END + timedelta(days=1),
                    'configuration_started_at': (CONFIGURE_START + timedelta(days=2)).replace(hour=0),
                    'configuration_finished_at': CONFIGURE_END + timedelta(days=2),
                }
                self.assertTrue(self.verify(before, after, **windows)['exact'])
                after[label][0][field] = stamp.date()
                with self.assertRaisesRegex(AssertionError, 'configuration|audit'):
                    self.verify(before, after, **windows)

    def test_audit_content_actor_changes_repr_path_and_time_are_exact(self):
        mutations = (
            ('content_type_id', 1),
            ('content_type_id', str(CONTENT_TYPE_IDS[LABELS['SportAssociation'].lower()])),
            ('actor_id', uuid.UUID(OWNER)),
            ('object_pk', uuid.UUID(ASSOCIATION)),
            ('object_id', 1),
            ('action', False),
            ('changes_text', 'fabricated'),
            ('changes', {'object_pk': [None, 'fabricated']}),
            ('changes', json.dumps({'object_pk': [None, 'fabricated']})),
            ('object_repr', 'fabricated representation'),
            ('timestamp', datetime(2020, 1, 1, tzinfo=timezone.utc)),
            ('cid', 'foreign-correlation'),
            ('serialized_data', {'fabricated': True}),
            ('remote_addr', '127.0.0.1'),
            ('remote_port', 443),
            ('additional_data', {}),
            ('actor_email', 'foreign@example.test'),
            ('id', '2'),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                before, after = snapshots()
                after[AUDIT][1][field] = value
                with self.assertRaisesRegex(AssertionError, 'audit'):
                    self.verify(before, after)

        before, after = snapshots()
        after[AUDIT].append(deepcopy(after[AUDIT][-1]))
        after[AUDIT][-1]['id'] = 999
        with self.assertRaisesRegex(AssertionError, 'audit'):
            self.verify(before, after)

        for mutation in ('path', 'association', 'link'):
            with self.subTest(mutation=mutation):
                before, after = snapshots()
                if mutation == 'path':
                    after[INDEX][0]['resolution_path'] = 'actor.sport_association'
                elif mutation == 'association':
                    after[INDEX][0]['sport_association_id'] = uuid.uuid4()
                else:
                    after[INDEX][0]['log_entry_id'] = after[AUDIT][0]['id']
                with self.assertRaisesRegex(AssertionError, 'audit'):
                    self.verify(before, after)

    def test_task_result_matches_complete_success_receipt(self):
        mutations = (
            ('task_id', 'foreign-task'),
            ('status', 'FAILURE'),
            ('content_type', 'application/x-python-pickle'),
            ('content_encoding', 'binary'),
            ('meta', json.dumps({'children': ['foreign']})),
            ('date_created', datetime(2020, 1, 1, tzinfo=timezone.utc)),
            ('date_done', datetime(2030, 1, 1, tzinfo=timezone.utc)),
            ('traceback', 'partial failure'),
            ('task_name', 'unrelated_task'),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                before, after = snapshots()
                after[TASK_RESULT][0][field] = value
                with self.assertRaisesRegex(AssertionError, 'task result'):
                    self.verify(before, after)

        result_mutations = (
            ('sport_association_id', str(uuid.uuid4())),
            ('denomination', 'foreign denomination'),
            ('owner_email', 'foreign@example.test'),
            ('stats', {'User': 1}),
            ('skipped_records', {'BillingPlan': 0, 'PreviewAndCustomFeatures': 0}),
            ('warnings', ['unresolved relation']),
            ('unexpected', True),
        )
        for field, value in result_mutations:
            with self.subTest(result_field=field):
                before, after = snapshots()
                result = json.loads(after[TASK_RESULT][0]['result'])
                result[field] = value
                after[TASK_RESULT][0]['result'] = json.dumps(result)
                with self.assertRaisesRegex(AssertionError, 'task result'):
                    self.verify(before, after)

    def test_task_baseline_must_be_empty_and_only_one_receipt_is_added(self):
        before, after = snapshots()
        prior = task_result_row(task_id='prior-task', id=0, status='SUCCESS')
        before[TASK_RESULT] = [prior]
        after[TASK_RESULT].insert(0, deepcopy(prior))
        with self.assertRaisesRegex(AssertionError, 'baseline'):
            self.verify(before, after)

        before, after = snapshots()
        after[TASK_RESULT].append(task_result_row(task_id='foreign', id=2))
        with self.assertRaisesRegex(AssertionError, 'task result'):
            self.verify(before, after)

    def test_framework_baseline_is_source_derived_and_exact(self):
        before, after = snapshots()
        self.assertIn('auth.Group', before)
        self.assertIn('application.Group', before)
        receipt = self.verify(before, after)
        self.assertTrue(receipt['exact'])
        self.assertEqual(receipt['baseline_content_types'], len([
            item for item in MODELS
            if item._meta.managed and not item._meta.proxy and not item._meta.auto_created
        ]))
        self.assertEqual(
            receipt['baseline_permissions'],
            4 * receipt['baseline_content_types'],
        )

    def test_unauthorized_framework_rows_are_rejected_in_both_snapshots(self):
        rows = {
            GROUP: make_model_row(GROUP, {'id': 1, 'name': 'unauthorized'}),
            SESSION: make_model_row(SESSION, {
                'session_key': 'unauthorized-session',
                'session_data': '{}',
                'expire_date': IMPORT_NOW,
            }),
            PERIODIC_TASK: make_model_row(PERIODIC_TASK, {
                'id': 1, 'name': 'unauthorized', 'task': 'foreign.task',
                'enabled': True,
            }),
            PERIODIC_TASKS: make_model_row(PERIODIC_TASKS, {
                'ident': 1, 'last_update': MIGRATION_NOW,
            }),
            USER_GROUPS: make_model_row(USER_GROUPS, {
                'id': 1, 'user_id': uuid.uuid4(), 'group_id': 1,
            }),
            CONTROL: make_model_row(CONTROL, {
                'id': 'unauthorized-control',
                'payload': {'observed': True},
            }),
            TASK_RESULT: task_result_row(task_id='foreign-task', id=2),
        }
        for label, row in rows.items():
            with self.subTest(label=label):
                before, after = snapshots()
                before[label].append(deepcopy(row))
                after[label].append(deepcopy(row))
                with self.assertRaisesRegex(AssertionError, 'baseline'):
                    self.verify(before, after)

    def test_content_types_and_permissions_are_exact_schema_metadata(self):
        before, after = snapshots()
        before[CONTENT_TYPE].append({
            'id': 999, 'app_label': 'foreign', 'model': 'control',
        })
        after[CONTENT_TYPE].append(deepcopy(before[CONTENT_TYPE][-1]))
        with self.assertRaisesRegex(AssertionError, 'baseline'):
            self.verify(before, after)

        before, after = snapshots()
        permission = deepcopy(before[PERMISSION][0])
        permission['id'] = 999
        permission['codename'] = 'foreign_control'
        before[PERMISSION].append(permission)
        after[PERMISSION].append(deepcopy(permission))
        with self.assertRaisesRegex(AssertionError, 'baseline'):
            self.verify(before, after)

        before, after = snapshots()
        before[PERMISSION].pop()
        after[PERMISSION].pop()
        with self.assertRaisesRegex(AssertionError, 'baseline'):
            self.verify(before, after)

    def test_unmanaged_and_proxy_schema_models_have_exact_metadata_rows(self):
        before, after, schema = schema_only_population()
        receipt = self.verify(before, after, schema_models=schema)
        baseline = self.verify()
        self.assertEqual(receipt['baseline_content_types'], baseline['baseline_content_types'] + 2)
        self.assertEqual(receipt['baseline_permissions'], baseline['baseline_permissions'] + 3)
        self.assertEqual(receipt['baseline_empty_tables'], baseline['baseline_empty_tables'])
        self.assertEqual(set(before), set(after))
        self.assertEqual(set(before), {item._meta.label for item in MODELS})
        for item in schema[len(MODELS):]:
            self.assertNotIn(item._meta.label, before)

    def test_schema_only_metadata_rejects_missing_unauthorized_and_changed_rows(self):
        def content(snapshot, identity):
            return next(row for row in snapshot[CONTENT_TYPE] if row['id'] == identity)

        def permission(snapshot, identity):
            return next(row for row in snapshot[PERMISSION] if row['id'] == identity)

        mutations = (
            lambda snapshot: snapshot[CONTENT_TYPE].remove(content(snapshot, 1001)),
            lambda snapshot: snapshot[CONTENT_TYPE].remove(content(snapshot, 1002)),
            lambda snapshot: content(snapshot, 1001).update(model='unregisteredlogger'),
            lambda snapshot: content(snapshot, 1002).update(model='unregisteredproxy'),
            lambda snapshot: snapshot[PERMISSION].remove(permission(snapshot, 1001)),
            lambda snapshot: snapshot[PERMISSION].remove(permission(snapshot, 1002)),
            lambda snapshot: snapshot[PERMISSION].remove(permission(snapshot, 1003)),
            lambda snapshot: permission(snapshot, 1001).update(name='Can view Voce del registro API'),
            lambda snapshot: permission(snapshot, 1002).update(codename='unregistered_permission'),
            lambda snapshot: permission(snapshot, 1003).update(content_type_id=1001),
            lambda snapshot: snapshot[PERMISSION].append({
                'id': 1004, 'name': 'Can add course proxy',
                'content_type_id': 1002, 'codename': 'add_courseproxy',
            }),
        )
        for index, mutation in enumerate(mutations):
            with self.subTest(mutation=index):
                before, after, schema = schema_only_population()
                for snapshot in (before, after):
                    mutation(snapshot)
                with self.assertRaisesRegex(AssertionError, 'baseline'):
                    self.verify(before, after, schema_models=schema)

    def test_swapped_and_auto_created_models_receive_no_post_migrate_rows(self):
        for label in ('auth.User', USER_GROUPS):
            with self.subTest(label=label):
                before, after, schema = schema_only_population()
                item = next(item for item in schema if item._meta.label == label)
                for snapshot in (before, after):
                    snapshot[CONTENT_TYPE].append({
                        'id': 1004, 'app_label': item._meta.app_label,
                        'model': item._meta.model_name,
                    })
                with self.assertRaisesRegex(AssertionError, 'unauthorized ContentType'):
                    self.verify(before, after, schema_models=schema)

    def test_schema_and_managed_table_inventories_must_agree(self):
        before, after, schema = schema_only_population()
        missing_managed = tuple(item for item in schema if item._meta.label != GROUP)
        with self.assertRaisesRegex(AssertionError, 'schema is missing a snapshot table'):
            self.verify(before, after, schema_models=missing_managed)
        replacement = model('Group', GROUP, [Field('id', kind='int'), Field('changed_name')])
        changed_schema = tuple(replacement if item._meta.label == GROUP else item for item in schema)
        with self.assertRaisesRegex(AssertionError, 'schema table metadata differs'):
            self.verify(before, after, schema_models=changed_schema)
        # Supplying only snapshot models cannot authorize schema-only rows.
        with self.assertRaisesRegex(AssertionError, 'unauthorized ContentType'):
            self.verify(before, after, schema_models=MODELS)
        # Schema-only entries can never become queried snapshot tables.
        before['drf_api_logger.APILogsModel'] = []
        after['drf_api_logger.APILogsModel'] = []
        with self.assertRaisesRegex(AssertionError, 'snapshot labels'):
            self.verify(before, after, schema_models=schema)

    def test_migration_only_registered_models_do_not_expand_live_snapshots(self):
        before, after, schema = schema_only_population()
        historical = model('MigrationOnly', 'control.MigrationOnly', [Field('id')])
        historical._meta.default_permissions = ()
        schema += (historical,)
        for snapshot in (before, after):
            snapshot[CONTENT_TYPE].append({
                'id': 1003, 'app_label': 'control', 'model': 'migrationonly',
            })
        receipt = self.verify(before, after, schema_models=schema)
        self.assertEqual(receipt['baseline_content_types'], self.verify()['baseline_content_types'] + 3)
        self.assertNotIn('control.MigrationOnly', before)
        self.assertEqual(len(before), len(MODELS))
        for snapshot in (before, after):
            snapshot[CONTENT_TYPE].pop()
        with self.assertRaisesRegex(AssertionError, 'ContentType population differs'):
            self.verify(before, after, schema_models=schema)

    def test_permission_options_come_from_migration_state_without_live_normalization(self):
        before, after = snapshots()
        historical = model('Group', GROUP, list(MODEL_BY_LABEL[GROUP]._meta.concrete_fields))
        historical._meta.default_permissions = ('view',)
        historical._meta.verbose_name_raw = 'historical group'
        historical._meta.permissions = (('inspect_group', 'Inspect historical group'),)
        schema = tuple(historical if item._meta.label == GROUP else item for item in MODELS)
        content_type_id = next(
            row['id'] for row in before[CONTENT_TYPE]
            if row['app_label'] == 'auth' and row['model'] == 'group'
        )
        for snapshot in (before, after):
            snapshot[PERMISSION] = [
                row for row in snapshot[PERMISSION]
                if row['content_type_id'] != content_type_id
            ] + [
                {'id': 1001, 'content_type_id': content_type_id,
                 'codename': 'view_group', 'name': 'Can view historical group'},
                {'id': 1002, 'content_type_id': content_type_id,
                 'codename': 'inspect_group', 'name': 'Inspect historical group'},
            ]
        receipt = self.verify(before, after, schema_models=schema)
        self.assertEqual(receipt['baseline_permissions'], self.verify()['baseline_permissions'] - 2)
        with self.assertRaisesRegex(AssertionError, 'baseline Permission'):
            self.verify(before, after, schema_models=MODELS)

    def test_malformed_inputs_fail_closed(self):
        before, after = snapshots()
        del after[LABELS['Course']]
        with self.assertRaisesRegex(AssertionError, 'snapshot'):
            self.verify(before, after)

        before, after = snapshots()
        after[LABELS['Associate']].append(deepcopy(after[LABELS['Associate']][0]))
        with self.assertRaisesRegex(AssertionError, 'snapshot'):
            self.verify(before, after)

        before, after = snapshots()
        del after[LABELS['Subscription']][0]['associate_id']
        with self.assertRaisesRegex(AssertionError, 'snapshot'):
            self.verify(before, after)

        broken = deepcopy(ARCHIVE)
        spec('Course', broken)['count'] = 1
        with self.assertRaisesRegex(AssertionError, 'manifest'):
            self.verify(archive=broken)

        with self.assertRaisesRegex(AssertionError, 'legacy'):
            broken = deepcopy(ARCHIVE)
            broken['entries'][spec(LEGACY_INVOICES, broken)['file']] = [{
                'sport_association_invoice_id': str(uuid.uuid4()),
            }]
            spec(LEGACY_INVOICES, broken)['count'] = 1
            self.verify(archive=broken)

        with self.assertRaisesRegex(AssertionError, 'relationship'):
            broken = deepcopy(ARCHIVE)
            broken['entries']['data/02_users.json'][0]['_m2m_unreviewed'] = [ASSOCIATION]
            self.verify(archive=broken)

        with self.assertRaisesRegex(AssertionError, 'archived ID'):
            broken = deepcopy(ARCHIVE)
            del broken['entries']['data/25_subscriptions.json'][0]['subscription_id']
            self.verify(archive=broken)

    def test_rollback_requires_complete_snapshot_equality(self):
        before, _ = snapshots()
        receipt = population.verify_unchanged_population(
            before, deepcopy(before), MODELS
        )
        self.assertTrue(receipt['unchanged'])
        changed = deepcopy(before)
        changed[LABELS['Payment']].append(make_row(
            'Payment', {'payment_id': uuid.uuid4()}
        ))
        with self.assertRaisesRegex(AssertionError, 'rollback'):
            population.verify_unchanged_population(before, changed, MODELS)


if __name__ == '__main__':
    unittest.main()
