"""Exact population oracle for the pinned historical setup import.

The module imports no Django or application code. Django supplies model
metadata, source registries and operation time windows after setup. Expected
rows and side effects come from the fixed archive, importer semantics and
model field definitions. Every non-import table has an explicit source-derived
baseline: migrations seed only payment categories and one feature flag, Django
post-migrate creates content types and permissions from model metadata, and all
other framework, control, result and join tables are empty. Observed rows are
never used to derive baseline allowances.
"""

from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import PurePosixPath
import json
import re
import uuid


CONFIG_LABEL = 'instance.InstanceConfiguration'
AUDIT_LABEL = 'auditlog.LogEntry'
AUDIT_INDEX_LABEL = 'application.AuditLogIndex'
TASK_RESULT_LABEL = 'django_celery_results.TaskResult'
CONTENT_TYPE_LABEL = 'contenttypes.ContentType'
PERMISSION_LABEL = 'auth.Permission'
PAYMENT_CATEGORY_LABEL = 'application.PaymentCategory'
PREVIEW_FEATURES_LABEL = 'application.PreviewAndCustomFeatures'

RETIRED_SECTIONS = frozenset(('BillingPlan', 'PreviewAndCustomFeatures'))
LEGACY_INVOICES = 'SportAssociationInvoices'
CREATE_ACTION = 0
UPDATE_ACTION = 1

# AssociationImportService.IMPORT_ORDER is behaviorally significant for audit
# order and the task's stats payload.
IMPORT_ORDER = (
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
)

PAYMENT_CATEGORIES = (
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
)


def _require(condition, message):
    if not condition:
        raise AssertionError(message)


def _field_type(field):
    return type(field).__name__


def _field_defaults(field):
    has_default = getattr(field, 'has_default', None)
    if callable(has_default):
        return bool(has_default())
    return getattr(field, 'has_default', False)


def _field_default(field):
    getter = getattr(field, 'get_default', None)
    _require(callable(getter), f'field has no get_default: {field.attname}')
    return getter()


def _is_auto_time(field):
    return bool(
        getattr(field, 'auto_now', False)
        or getattr(field, 'auto_now_add', False)
    )


def _parse_time(value, name):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise AssertionError(f'{name} must be an ISO datetime')
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _time_window(start, finish, name):
    lower = _parse_time(start, f'{name} start')
    upper = _parse_time(finish, f'{name} finish')
    _require(lower <= upper, f'{name} time window is reversed')
    return lower, upper


def _as_utc(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=timezone.utc)
    raise AssertionError('generated time has the wrong type')


def _in_window(value, window, message):
    actual = _as_utc(value)
    _require(window[0] <= actual <= window[1], message)


def _same_value(expected, actual):
    """Compare typed database values without bool/int or Decimal-scale loss."""
    if expected is None or actual is None:
        return expected is None and actual is None
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, Decimal):
        return expected.as_tuple() == actual.as_tuple()
    if isinstance(expected, dict):
        return (
            set(expected) == set(actual)
            and all(_same_value(expected[key], actual[key]) for key in expected)
        )
    if isinstance(expected, (list, tuple)):
        return (
            len(expected) == len(actual)
            and all(_same_value(left, right) for left, right in zip(expected, actual))
        )
    return expected == actual


def _canonical(value):
    def normalized(item):
        if isinstance(item, Decimal):
            return {'__decimal__': str(item)}
        if isinstance(item, datetime):
            return {'__datetime__': item.isoformat()}
        if isinstance(item, date):
            return {'__date__': item.isoformat()}
        if isinstance(item, uuid.UUID):
            return {'__uuid__': str(item)}
        if isinstance(item, dict):
            return {str(key): normalized(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalized(value) for value in item]
        return item

    return json.dumps(
        normalized(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False
    )


def _metadata(models):
    result = {}
    labels = set()
    for model in models:
        meta = getattr(model, '_meta', None)
        label = getattr(meta, 'label', None)
        label_lower = getattr(meta, 'label_lower', None) or str(label).lower()
        pk = getattr(getattr(meta, 'pk', None), 'attname', None)
        concrete = tuple(getattr(meta, 'concrete_fields', ()) or ())
        name = getattr(model, '__name__', None)
        _require(label and label_lower and pk and name, 'model metadata is incomplete')
        _require(label not in labels, f'duplicate model label: {label}')
        fields = {
            getattr(field, 'attname', None): field for field in concrete
            if getattr(field, 'attname', None)
        }
        _require(len(fields) == len(concrete), f'duplicate field names in {label}')
        model_name = getattr(meta, 'model_name', None) or name.lower()
        verbose_name = getattr(meta, 'verbose_name_raw', None)
        if verbose_name is None:
            verbose_name = getattr(meta, 'verbose_name', None)
        default_permissions = tuple(getattr(meta, 'default_permissions', ()) or ())
        custom_permissions = tuple(getattr(meta, 'permissions', ()) or ())
        _require(
            isinstance(model_name, str) and model_name
            and verbose_name is not None
            and all(isinstance(action, str) for action in default_permissions)
            and all(
                isinstance(permission, (list, tuple))
                and len(permission) == 2
                and all(isinstance(item, str) for item in permission)
                for permission in custom_permissions
            ),
            f'model permission metadata is incomplete: {label}',
        )
        result[label] = {
            'model': model,
            'name': name,
            'model_name': model_name,
            'label': label,
            'label_lower': label_lower,
            'app_label': getattr(meta, 'app_label', ''),
            'pk': pk,
            'fields': fields,
            'managed': bool(getattr(meta, 'managed', False)),
            'proxy': bool(getattr(meta, 'proxy', False)),
            'auto_created': bool(getattr(meta, 'auto_created', False)),
            'swapped': bool(getattr(meta, 'swapped', False)),
            'verbose_name': str(verbose_name),
            'default_permissions': default_permissions,
            'permissions': custom_permissions,
        }
        labels.add(label)
    return result


def _snapshot(snapshot, metadata, name):
    _require(isinstance(snapshot, dict), f'{name} snapshot must be an object')
    expected_labels = set(metadata)
    _require(
        set(snapshot) == expected_labels,
        f'{name} snapshot labels differ from model metadata',
    )
    identities = {}
    for label, item in metadata.items():
        rows = snapshot[label]
        _require(isinstance(rows, (list, tuple)), f'{name} {label} rows must be a list')
        seen = set()
        for row in rows:
            _require(isinstance(row, dict), f'{name} {label} row must be an object')
            _require(
                set(row) == set(item['fields']),
                f'{name} snapshot {label} row fields differ from concrete metadata',
            )
            _require(item['pk'] in row, f'{name} {label} row has no {item["pk"]}')
            identity = str(row[item['pk']])
            _require(identity not in seen, f'{name} snapshot {label} has duplicate primary key')
            seen.add(identity)
        identities[label] = seen
    return identities


def _manifest(archive):
    _require(isinstance(archive, dict), 'archive must be an object')
    entries = archive.get('entries')
    _require(isinstance(entries, dict), 'archive has no entries')
    manifest = entries.get('manifest.json')
    _require(isinstance(manifest, dict), 'archive manifest is missing')
    specs = manifest.get('models_exported')
    _require(isinstance(specs, list), 'manifest.models_exported must be a list')
    names = [spec.get('name') for spec in specs if isinstance(spec, dict)]
    files = [spec.get('file') for spec in specs if isinstance(spec, dict)]
    _require(len(names) == len(specs), 'manifest section must be an object')
    _require(len(set(names)) == len(names), 'manifest model names are not unique')
    _require(len(set(files)) == len(files), 'manifest section files are not unique')
    _require(set(files) == set(entries) - {'manifest.json'}, 'archive entries differ from manifest')
    for spec in specs:
        records = entries[spec['file']]
        _require(isinstance(records, list), f'manifest section is not a list: {spec["file"]}')
        _require(
            type(spec.get('count')) is int and spec['count'] == len(records),
            f'manifest count differs for {spec["name"]}',
        )
    return specs


def _archive_labels(specs, metadata, overrides):
    overrides = dict(overrides or {})
    candidates = {}
    for item in metadata.values():
        candidates.setdefault(item['name'], []).append(item)
    result = {}
    for spec in specs:
        name = spec['name']
        if name in RETIRED_SECTIONS or name == LEGACY_INVOICES:
            continue
        if name in overrides:
            label = overrides[name]
            _require(label in metadata, f'archive override label is absent: {label}')
            result[name] = label
            continue
        matches = candidates.get(name, ())
        if len(matches) == 1:
            result[name] = matches[0]['label']
            continue
        preferred = [
            item for item in matches
            if item['label'] == f'application.{name}'
        ]
        _require(
            len(preferred) == 1,
            f'ambiguous archive model needs archive_model_labels override: {name}',
        )
        result[name] = preferred[0]['label']
    return result


def _retired_counts(specs, contract, skipped_records):
    policy = dict(contract.get('retired_policy', {}).get('explicit_skip_records', {}))
    _require(
        set(policy) == RETIRED_SECTIONS,
        'retired policy must contain only the two source-deprecated sections',
    )
    expected = {
        spec['name']: spec['count']
        for spec in specs if spec['name'] in RETIRED_SECTIONS
    }
    _require(policy == expected, 'retired policy differs from source manifest counts')
    _require(isinstance(skipped_records, dict), 'observed retired counts must be an object')
    _require(
        set(skipped_records) == set(expected)
        and all(
            type(skipped_records[name]) is int
            and skipped_records[name] == expected[name]
            for name in expected
        ),
        'observed retired counts differ from the source allowlist',
    )
    return expected


def _legacy_invoices(specs, labels, metadata):
    matches = [spec for spec in specs if spec['name'] == LEGACY_INVOICES]
    _require(len(matches) == 1, 'legacy invoice section is missing from manifest')
    _require(
        matches[0]['count'] == 0,
        'legacy invoice rows are unsupported by setup import; '
        'only replacement restore normalizes them',
    )
    _require(LEGACY_INVOICES not in labels, 'legacy invoice model was incorrectly mapped')
    _require(
        LEGACY_INVOICES not in {
            item['name'] for item in metadata.values()
        },
        'setup import metadata unexpectedly includes the retired invoice model',
    )


def _hidden_relationships(specs, archive):
    preview = next(
        (spec for spec in specs if spec['name'] == 'PreviewAndCustomFeatures'),
        None,
    )
    _require(preview is not None, 'retired preview section is missing')
    retired_preview_ids = {
        str(row.get('preview_and_custom_features_id'))
        for row in archive['entries'][preview['file']]
        if row.get('preview_and_custom_features_id') is not None
    }
    for spec in specs:
        if spec['name'] in RETIRED_SECTIONS or spec['name'] == LEGACY_INVOICES:
            continue
        for row in archive['entries'][spec['file']]:
            for field, values in row.items():
                if not field.startswith('_m2m_'):
                    continue
                supported_drop = (
                    spec['name'] == 'User'
                    and field == '_m2m_preview_and_custom_features'
                    and isinstance(values, list)
                    and {str(value) for value in values} <= retired_preview_ids
                )
                _require(
                    supported_drop,
                    f'archived relationship is not represented by snapshots: '
                    f'{spec["name"]}.{field[5:]}',
                )


def _document_media(archive):
    text_files = archive.get('text_files', {})
    _require(isinstance(text_files, dict), 'archive text_files must be an object')
    documents = []
    for name, content in text_files.items():
        path = PurePosixPath(name)
        if len(path.parts) == 4 and path.parts[:2] == ('files', 'documents'):
            _require(isinstance(content, str), 'document fixture must be text')
            documents.append((path.parts[2], path.parts[3]))
        elif len(path.parts) == 4 and path.parts[0] == 'files':
            raise AssertionError(
                'signature media updates are not covered by the setup population oracle'
            )
        else:
            raise AssertionError(f'unreviewed archive media path: {name}')
    return documents


def _validated_document_media(archive, specs):
    media = _document_media(archive)
    document_spec = next(
        spec for spec in specs if spec['name'] == 'Document'
    )
    documents = {
        str(row['document_id']): row
        for row in archive['entries'][document_spec['file']]
    }
    seen = set()
    result = []
    for document_id, filename in media:
        _require(document_id in documents, 'archive media references an absent Document')
        _require(document_id not in seen, 'multiple media files for one Document are unsupported')
        _require(
            documents[document_id].get('filename') == filename,
            'archive media filename differs from Document.filename',
        )
        seen.add(document_id)
        result.append((document_id, filename))
    return result


def _schema_metadata(metadata, schema_models):
    """Keep live snapshot tables distinct from the post-migrate state schema."""
    schema = _metadata(schema_models)
    _require(set(metadata) <= set(schema), 'schema is missing a snapshot table')
    for label, item in metadata.items():
        historical = schema[label]
        attributes = ('app_label', 'model_name', 'pk', 'managed', 'proxy',
                      'auto_created', 'swapped')
        _require(
            item['managed'] and not item['proxy'] and not item['swapped']
            and all(item[key] == historical[key] for key in attributes)
            and {name: _field_type(field) for name, field in item['fields'].items()}
            == {name: _field_type(field) for name, field in historical['fields'].items()},
            f'schema table metadata differs from snapshot model: {label}',
        )
    return schema


def _framework_expectations(schema):
    """Mirror Django 5.2.18 post_migrate's AppConfig.get_models() rules.

    create_contenttypes() includes unmanaged and proxy models. Both receivers
    exclude swapped and auto-created models; create_permissions() requests
    for_concrete_models=False and uses verbose_name_raw plus declared default
    and custom permissions. Routing applies to ContentType/Permission, not to
    each target model, in this pinned single-database migration configuration.
    """
    content_models = {
        (item['app_label'], item['model_name']): item
        for item in schema.values()
        if not item['swapped'] and not item['auto_created']
    }
    permissions = {}
    for content_key, item in content_models.items():
        expected = [
            (
                f'{action}_{item["model_name"]}',
                f'Can {action} {item["verbose_name"]}',
            )
            for action in item['default_permissions']
        ]
        expected.extend(tuple(permission) for permission in item['permissions'])
        for codename, name in expected:
            key = (content_key, codename)
            _require(
                key not in permissions,
                f'duplicate model permission metadata: {content_key}.{codename}',
            )
            permissions[key] = name
    return content_models, permissions


def _validate_framework_population(before, metadata, schema):
    """Require exact schema populations and reject every observed-only row."""
    content_models, permissions = _framework_expectations(schema)

    content_types = {}
    content_type_ids = set()
    for row in before[CONTENT_TYPE_LABEL]:
        key = (row.get('app_label'), row.get('model'))
        _require(key in content_models, f'baseline has unauthorized ContentType: {key}')
        _require(key not in content_types, f'baseline duplicates ContentType: {key}')
        identity = row.get('id')
        _require(
            type(identity) is int and identity > 0,
            f'baseline ContentType ID is invalid: {key}',
        )
        _require(identity not in content_type_ids, 'baseline ContentType IDs are not unique')
        content_types[key] = row
        content_type_ids.add(identity)
    _require(
        set(content_types) == set(content_models),
        'baseline ContentType population differs from model metadata',
    )

    observed_permissions = set()
    permission_ids = set()
    for row in before[PERMISSION_LABEL]:
        content_type_id = row.get('content_type_id')
        content_key = next(
            (
                key for key, content_type in content_types.items()
                if content_type['id'] == content_type_id
            ),
            None,
        )
        _require(content_key is not None, 'baseline Permission has a foreign ContentType')
        key = (content_key, row.get('codename'))
        _require(key in permissions, f'baseline has unauthorized Permission: {key}')
        _require(key not in observed_permissions, f'baseline duplicates Permission: {key}')
        _require(
            row.get('name') == permissions[key],
            f'baseline Permission name differs: {key}',
        )
        identity = row.get('id')
        _require(
            type(identity) is int and identity > 0,
            f'baseline Permission ID is invalid: {key}',
        )
        _require(identity not in permission_ids, 'baseline Permission IDs are not unique')
        observed_permissions.add(key)
        permission_ids.add(identity)
    _require(
        observed_permissions == set(permissions),
        'baseline Permission population differs from model metadata',
    )

    seeded = {PAYMENT_CATEGORY_LABEL, PREVIEW_FEATURES_LABEL}
    framework = {CONTENT_TYPE_LABEL, PERMISSION_LABEL}
    empty_tables = []
    for label in metadata:
        if label in seeded | framework:
            continue
        _require(
            not before[label],
            f'source-authored baseline population differs: {label}',
        )
        empty_tables.append(label)
    for label, message in (
        (CONFIG_LABEL, 'configuration'),
        (AUDIT_LABEL, 'audit LogEntry'),
        (AUDIT_INDEX_LABEL, 'audit index'),
        (TASK_RESULT_LABEL, 'task result'),
    ):
        _require(not before[label], f'{message} baseline must be empty')
    return {
        'content_types': content_types,
        'permissions': permissions,
        'empty_tables': empty_tables,
    }


def _validate_baseline(before, metadata, schema, migration_window):
    """Reject every observed-only baseline row, including framework rows."""
    payment_rows = before[PAYMENT_CATEGORY_LABEL]
    preview_rows = before[PREVIEW_FEATURES_LABEL]
    framework = _validate_framework_population(before, metadata, schema)
    _require(
        len(payment_rows) == len(PAYMENT_CATEGORIES),
        'baseline payment category population differs from migration source',
    )
    expected_by_name = {
        name: (expense, deductible)
        for name, expense, deductible in PAYMENT_CATEGORIES
    }
    observed_names = set()
    payment_ids = set()
    category_meta = metadata[PAYMENT_CATEGORY_LABEL]
    for row in payment_rows:
        name = row.get('name')
        _require(name in expected_by_name, f'unauthorized baseline payment category: {name!r}')
        _require(name not in observed_names, 'duplicate baseline payment category')
        observed_names.add(name)
        expense, deductible = expected_by_name[name]
        _require(row.get('expense') is expense, f'baseline payment category expense differs: {name}')
        _require(
            row.get('tax_deductible') is deductible,
            f'baseline payment category deduction differs: {name}',
        )
        expected_other = {
            'sport_association_id': None,
            'archived': False,
            'type': 1,
            'vat_management_id': None,
            'deleted': False,
        }
        for field, value in expected_other.items():
            if field in category_meta['fields']:
                _require(_same_value(value, row.get(field)), f'baseline field differs: {name}.{field}')
        identity = row[category_meta['pk']]
        _require(isinstance(identity, uuid.UUID) and identity.version == 4,
                 'baseline payment category ID is not a generated UUID4')
        _require(identity not in payment_ids, 'duplicate baseline payment category ID')
        payment_ids.add(identity)
        _in_window(
            row.get('creation_date'), migration_window,
            'baseline payment category creation time is outside migrations',
        )

    _require(len(preview_rows) == 1, 'baseline feature population must contain one migration row')
    preview = preview_rows[0]
    _require(
        preview.get('name') == 'Iscrizioni per Enti e Federazioni',
        'baseline feature name differs from migration source',
    )
    _require(
        preview.get('description')
        == 'Gestisci le iscrizioni per Enti e Federazioni',
        'baseline feature description differs from migration source',
    )
    preview_id = preview[metadata[PREVIEW_FEATURES_LABEL]['pk']]
    _require(
        isinstance(preview_id, uuid.UUID) and preview_id.version == 4,
        'baseline feature ID is not a generated UUID4',
    )
    return {
        'payment_categories': len(payment_rows),
        'content_types': len(framework['content_types']),
        'permissions': len(framework['permissions']),
        'empty_tables': len(framework['empty_tables']),
    }


def _source_time_policy(field, window):
    return ('time', window)


def _default_policy(field, window, model_name):
    if _field_defaults(field):
        value = _field_default(field)
        if isinstance(value, (datetime, date)):
            return ('time', window)
        if isinstance(value, uuid.UUID):
            return ('uuid',)
        # Constructor defaults need the same field conversion as archived
        # values. In particular Subscription.subscription_number is TextField
        # default=0: Django TextField.get_prep_value() calls to_python(), so a
        # database snapshot contains exactly '0', although the instance began
        # with integer 0. This remains typed equality, not a type allowance.
        parser = field.target_field if getattr(field, 'is_relation', False) else field
        return ('exact', parser.to_python(value))
    if getattr(field, 'null', False):
        return ('exact', None)
    if getattr(field, 'attname', None) and _field_type(field) == 'UUIDField':
        return ('uuid',)
    raise AssertionError(f'no source value or default for {model_name}.{field.attname}')


def _next_year(value):
    try:
        return value.replace(year=value.year + 1)
    except ValueError:
        return value.replace(year=value.year + 1, day=28)


def _season_range(creation, owner):
    start_day = owner['subscription_start_day']
    start_month = owner['subscription_start_month']
    current_start = date(creation.year, start_month, start_day)
    previous_start = date(creation.year - 1, start_month, start_day)
    start = current_start if creation >= current_start else previous_start
    if (
        owner['custom_end_date']
        and owner['subscription_end_month']
        and owner['subscription_end_day']
    ):
        end = date(
            start.year,
            owner['subscription_end_month'],
            owner['subscription_end_day'],
        )
        if end < start:
            end = date(end.year + 1, end.month, end.day)
    else:
        end = date(start.year + 1, start.month, start.day) - timedelta(days=1)
    return start, end


def _subscription_hook_dates(creation_date, owner):
    """Mirror Subscription.save() for archived null start/end dates."""
    _require(
        isinstance(creation_date, datetime),
        'Subscription.save requires its generated creation datetime',
    )
    creation = _as_utc(creation_date).date()
    duration = owner.get('subscription_duration')
    _require(type(duration) is int, 'owner subscription duration is not an integer')
    season_start, season_end = _season_range(creation, owner)
    if duration == 1:
        return creation, _next_year(creation)
    if duration == 2:
        return season_start, season_end
    if duration == 3:
        return creation, season_end
    return None, None


def _expected_import_rows(
    specs, labels, metadata, archive, import_window, document_media, baseline,
):
    expected = {}
    policies = {}
    for spec in specs:
        name = spec['name']
        if name in RETIRED_SECTIONS or name == LEGACY_INVOICES:
            continue
        item = metadata[labels[name]]
        model_rows = []
        model_policies = []
        media_document_ids = {identity for identity, _filename in document_media}
        for source in archive['entries'][spec['file']]:
            _require(isinstance(source, dict), f'archived row is not an object: {name}')
            values = {
                key: value for key, value in source.items()
                if not key.startswith('_m2m_')
            }
            unknown = set(values) - set(item['fields'])
            _require(not unknown, f'unreviewed archived fields: {name}.{sorted(unknown)}')
            _require(item['pk'] in values, f'archived ID missing: {name}.{item["pk"]}')
            if name == 'Associate':
                values['sport_association_id'] = source.get('sport_association_id')
            row_policy = {}
            for field_name, field in item['fields'].items():
                if field_name == item['pk'] and field_name not in values:
                    raise AssertionError(f'archived ID missing: {name}.{field_name}')
                if name == 'User' and field_name == 'password' and field_name not in values:
                    row_policy[field_name] = ('unusable_password',)
                    continue
                if (
                    name == 'Subscription'
                    and field_name in ('start_date', 'end_date')
                    and values.get(field_name) is None
                ):
                    row_policy[field_name] = ('subscription_hook', field_name)
                    continue
                if field_name in values:
                    if _is_auto_time(field):
                        row_policy[field_name] = _source_time_policy(field, import_window)
                        continue
                    if name == 'Document' and field_name == 'filepath':
                        identity = str(source[item['pk']])
                        row_policy[field_name] = (
                            ('filepath', source.get(item['pk']))
                            if identity in media_document_ids
                            else ('exact', source.get(field_name))
                        )
                        continue
                    parser = field.target_field if getattr(field, 'is_relation', False) else field
                    row_policy[field_name] = ('exact', parser.to_python(values[field_name]))
                    continue
                row_policy[field_name] = _default_policy(field, import_window, name)

            if name == 'Payment' and values.get('payment_category_id') is None:
                # Payment.save() selects this category before super().save().
                # The pinned archive has no imported categories; the strict
                # baseline validator already proved this is the unique source
                # migration seed, rather than an arbitrary observed row.
                _require(
                    not archive['entries'][next(
                        spec['file'] for spec in specs
                        if spec['name'] == 'PaymentCategory'
                    )],
                    'Payment.save category selection with imported categories is unreviewed',
                )
                candidates = [
                    row for row in baseline[PAYMENT_CATEGORY_LABEL]
                    if row['name'] == PAYMENT_CATEGORIES[0][0]
                ]
                _require(len(candidates) == 1,
                         'Payment.save requires the unique migration category seed')
                _require('payment_category_id' in item['fields'],
                         'Payment.save category field is missing from model metadata')
                row_policy['payment_category_id'] = (
                    'exact', candidates[0][metadata[PAYMENT_CATEGORY_LABEL]['pk']]
                )

            if name == 'User':
                sanitized = {
                    'is_staff': False,
                    'is_superuser': False,
                    'two_fa': False,
                    'two_fa_secret': None,
                    'stripe_account_id': None,
                    'stripe_on_boarding_completed': False,
                    'integration_google_state': None,
                    'integration_google_credentials': None,
                    'integration_google_auto_sync': False,
                }
                if str(source.get('user_id')) == str(
                    next(
                        row['user_id']
                        for row in archive['entries']['data/01_sport_association.json']
                    )
                ):
                    sanitized.update({'role': 1, 'is_active': True})
                for field_name, value in sanitized.items():
                    if field_name in item['fields']:
                        row_policy[field_name] = ('exact', value)

            actual = {}
            for field_name, policy in row_policy.items():
                if policy[0] == 'exact':
                    actual[field_name] = deepcopy(policy[1])
                else:
                    actual[field_name] = None
            model_rows.append(actual)
            model_policies.append(row_policy)
        expected[name] = model_rows
        policies[name] = model_policies
    return expected, policies


def _filepath_policy(actual, document_id, filename, import_window):
    """Validate the raw empty-STORAGE_DIR layout before authoring its audit.

    The pinned importer uses timezone.now() (UTC with USE_TZ=True) and
    strftime('%Y%m%d_%H%M%S'). Compare the encoded seconds to the window's
    encoded seconds: microseconds are discarded by the source, not rounded.
    Do not normalize paths; storage prefixes, absolute paths and dot/empty
    components are not part of this layout.
    """
    _require(isinstance(actual, str) and actual, 'Document filepath is empty')
    parts = actual.split('/')
    _require(
        len(parts) == 3
        and parts[1] == str(document_id)
        and parts[2] == filename,
        'Document storage path differs from the importer layout',
    )
    _require(
        re.fullmatch(r'[0-9]{8}_[0-9]{6}', parts[0]) is not None,
        'Document storage timestamp has the wrong format',
    )
    try:
        stamp = datetime.strptime(parts[0], '%Y%m%d_%H%M%S').replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise AssertionError('Document storage timestamp is not a valid UTC date') from exc
    _require(
        import_window[0].replace(microsecond=0)
        <= stamp <= import_window[1].replace(microsecond=0),
        'Document storage timestamp outside import request',
    )
    canonical = f'{stamp.strftime("%Y%m%d_%H%M%S")}/{document_id}/{filename}'
    _require(actual == canonical, 'Document storage path is not canonical')
    return canonical


def _validate_import_population(
    after, before, expected, policies, labels, metadata, import_window,
):
    association_source = expected['SportAssociation'][0]
    owner_source = next(
        row for row in expected['User']
        if str(row[metadata[labels['User']]['pk']])
        == str(association_source['user_id'])
    )
    resolved_rows = {}
    for name, expected_rows in expected.items():
        item = metadata[labels[name]]
        actual_rows = after[item['label']]
        actual_by_id = {
            str(row[item['pk']]): row for row in actual_rows
        }
        expected_ids = {
            str(row[item['pk']]) for row in expected_rows
        }
        before_ids = {
            str(row[item['pk']]) for row in before[item['label']]
        }
        _require(
            not (before_ids & expected_ids),
            f'archived ID collides with source baseline: {name}',
        )
        _require(
            set(actual_by_id) == before_ids | expected_ids,
            f'{name} population differs; exact archived and empty-section rows required',
        )
        resolved_rows[name] = []
        for source_index, policy_row in enumerate(policies[name]):
            expected_row = expected_rows[source_index]
            identity = str(expected_row[item['pk']])
            actual = actual_by_id[identity]
            resolved_rows[name].append(deepcopy(actual))
            hook_dates = (
                _subscription_hook_dates(actual.get('creation_date'), owner_source)
                if name == 'Subscription' else None
            )
            for field_name, policy in policy_row.items():
                kind = policy[0]
                value = actual[field_name]
                if kind == 'exact':
                    _require(
                        _same_value(policy[1], value),
                        f'archived field differs: {name}.{field_name}',
                    )
                elif kind == 'time':
                    _in_window(
                        value, import_window,
                        f'generated time outside import request: {name}.{field_name}',
                    )
                elif kind == 'uuid':
                    _require(
                        isinstance(value, uuid.UUID) and value.version == 4,
                        f'generated UUID differs: {name}.{field_name}',
                    )
                elif kind == 'filepath':
                    _require(
                        'filename' in actual,
                        f'Document filename missing before filepath verification',
                    )
                    resolved_rows[name][-1][field_name] = _filepath_policy(
                        value, identity, expected_row['filename'], import_window,
                    )
                elif kind == 'unusable_password':
                    _require(
                        isinstance(value, str)
                        and value.startswith('!')
                        and len(value) > 1,
                        f'unusable generated password differs: {name}.{field_name}',
                    )
                elif kind == 'subscription_hook':
                    expected_date = hook_dates[
                        0 if field_name == 'start_date' else 1
                    ]
                    _require(
                        _same_value(expected_date, value),
                        f'Subscription.save-derived {field_name} differs',
                    )
                else:
                    raise AssertionError(f'unknown population policy: {kind}')
    return resolved_rows


def _expected_stats(specs, archive):
    by_name = {spec['name']: spec['count'] for spec in specs}
    _require(
        set(by_name) - RETIRED_SECTIONS - {LEGACY_INVOICES} == set(IMPORT_ORDER),
        'source archive models differ from importer order',
    )
    stats = {}
    for name in IMPORT_ORDER:
        count = by_name[name]
        if name == 'User':
            count -= 1
        stats[name] = count
    stats.update({
        'deferred_fks_resolved': 0,
        'deferred_fks_failed': 0,
        'm2m_resolved': 0,
        'm2m_failed': 0,
        'files_imported': len(archive.get('text_files', {})),
    })
    return stats


def _expected_task_payload(archive, specs, skipped):
    association = archive['entries']['data/01_sport_association.json']
    users = archive['entries']['data/02_users.json']
    _require(len(association) == 1, 'archive must contain one source association')
    owner_id = str(association[0].get('user_id'))
    owners = [row for row in users if str(row.get('user_id')) == owner_id]
    _require(len(owners) == 1, 'archive must contain exactly one source owner')
    return {
        'success': True,
        'sport_association_id': str(association[0]['sport_association_id']),
        'denomination': association[0]['denomination'],
        'owner_email': owners[0]['email'],
        'stats': _expected_stats(specs, archive),
        'skipped_records': skipped,
        'warnings': [],
    }


def _parse_json(value, name):
    if isinstance(value, (dict, list)):
        return deepcopy(value)
    _require(isinstance(value, str), f'{name} must contain JSON text')
    try:
        return json.loads(value)
    except (TypeError, ValueError) as exc:
        raise AssertionError(f'{name} is not valid JSON') from exc


def _task_result_added(before, after, expected_payload, task_id, window):
    _require(TASK_RESULT_LABEL in before and TASK_RESULT_LABEL in after,
             'snapshot lacks django-celery TaskResult')
    _require(not before[TASK_RESULT_LABEL], 'task result baseline must be empty')
    _require(len(after[TASK_RESULT_LABEL]) == 1,
             'task result must add exactly one worker receipt')
    row = after[TASK_RESULT_LABEL][0]
    required_fields = {
        'id', 'task_id', 'periodic_task_name', 'task_name', 'task_args',
        'task_kwargs', 'status', 'worker', 'content_type', 'content_encoding',
        'result', 'date_created', 'date_done', 'traceback', 'meta',
    }
    _require(
        set(row) == required_fields,
        'task result metadata differs from the pinned backend',
    )
    _require(type(row.get('id')) is int and row['id'] > 0, 'task result ID is invalid')
    _require(row.get('task_id') == task_id, 'task result identity differs')
    _require(row.get('status') == 'SUCCESS', 'task result state is not SUCCESS')
    _require(row.get('content_type') == 'application/json',
             'task result content type differs')
    _require(row.get('content_encoding') == 'utf-8',
             'task result content encoding differs')
    for field in (
        'periodic_task_name', 'task_name', 'task_args', 'task_kwargs',
        'worker', 'traceback',
    ):
        _require(row.get(field) is None, f'task result unexpectedly populates {field}')
    _require(
        _same_value(_parse_json(row.get('result'), 'task result'), expected_payload),
        'task result payload differs from the source-derived import receipt',
    )
    _require(
        _same_value(_parse_json(row.get('meta'), 'task result meta'), {'children': []}),
        'task result meta differs from django-celery default',
    )
    _require(row.get('date_created') is not None and row.get('date_done') is not None,
             'task result timestamps are missing')
    _in_window(row['date_created'], window, 'task result creation time is outside import')
    _in_window(row['date_done'], window, 'task result completion time is outside import')
    _require(_as_utc(row['date_created']) <= _as_utc(row['date_done']),
             'task result completion precedes creation')
    return {
        'task_id': row['task_id'],
        'task_name': expected_payload and 'import_association_data',
        'status': 'SUCCESS',
        'stats': expected_payload['stats'],
    }

def _configuration_expected(contract, defaults, fields):
    _require(isinstance(defaults, dict), 'configuration_defaults must be an object')
    setup = contract.get('setup', {}).get('configure')
    _require(isinstance(setup, dict), 'contract setup configure payload is missing')
    initialization = setup.get('initialization')
    oem = setup.get('oem')
    ids = contract.get('ids')
    _require(isinstance(initialization, dict), 'setup initialization is missing')
    _require(isinstance(oem, dict), 'setup OEM payload is missing')
    _require(isinstance(ids, dict), 'contract IDs are missing')
    _require(initialization.get('type') == 'import',
             'configuration provenance must be import')
    association = ids.get('association')
    _require(association is not None, 'contract association ID is missing')
    oauth = setup.get('oauth') or {}
    _require(isinstance(oauth, dict), 'setup OAuth payload must be an object')
    expected = {
        'domain': str(setup.get('domain', '')).lower().strip(),
        'setup_provenance': 'import',
        'name': oem.get('name'),
        'abbreviation': oem.get('abbreviation') or '',
        'primary_color': oem.get('primaryColor') or '#351DC2',
        'support_email': oem.get('supportEmail') or '',
        'logo_path': oem.get('logo') or '',
        'email_settings': {},
        'email_password_encrypted': '',
        'email_revision': 0,
        'diagnostic_results': {},
        'integration_settings': {},
        'display_settings': deepcopy(defaults),
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
        'primary_association_id': association,
    }
    _require(
        set(fields) == set(expected) | {
            'id', 'configured_at', 'updated_at', 'onboarding_completed_at',
        },
        'configuration metadata contains an unreviewed concrete field',
    )
    # InstanceSetupView links the imported model instance. Its FK value in a
    # values() snapshot therefore has the target PK's database type (UUID),
    # whereas the contract/task payload contains JSON text. Convert only the
    # source-derived expectations; snapshot values remain strictly typed.
    return {
        name: getattr(fields[name], 'target_field', fields[name]).to_python(value)
        for name, value in expected.items()
    }


def _configuration_difference(before, after, contract, defaults, window, metadata):
    _require(not before[CONFIG_LABEL], 'configuration must be empty before setup')
    added = [
        row for row in after[CONFIG_LABEL]
        if _canonical(row) not in {_canonical(item) for item in before[CONFIG_LABEL]}
    ]
    _require(len(added) == 1 and len(after[CONFIG_LABEL]) == 1,
             'configuration must contain exactly one new row')
    row = added[0]
    expected = _configuration_expected(
        contract, defaults, metadata[CONFIG_LABEL]['fields']
    )
    for field, value in expected.items():
        _require(
            _same_value(value, row.get(field)),
            f'configuration field differs: {field}',
        )
    _require(type(row.get('id')) is int and row['id'] > 0,
             'configuration ID is invalid')
    for field in ('configured_at', 'updated_at', 'onboarding_completed_at'):
        _require(isinstance(row.get(field), datetime),
                 f'configuration {field} must be a datetime')
        _in_window(row[field], window, f'configuration {field} is outside request')
    _require(
        _as_utc(row['configured_at']) <= _as_utc(row['updated_at']),
        'configuration update precedes creation',
    )
    return {
        'association_id': str(expected['primary_association_id']),
        'setup_provenance': 'import',
    }


def _registry_labels(items):
    result = set()
    for item in items or ():
        if isinstance(item, str):
            result.add(item.lower())
        else:
            label = getattr(getattr(item, '_meta', None), 'label_lower', None)
            if label:
                result.add(label)
    return result


def _normalize_resolvers(resolvers):
    result = {}
    for label, resolver in (resolvers or {}).items():
        name = resolver if isinstance(resolver, str) else getattr(resolver, '__name__', '')
        result[str(label).lower()] = name
    return result


def _audit_fields_for(label, audit_fields):
    config = (audit_fields or {}).get(label) or {}
    _require(not config.get('mask_fields'),
             f'masked audit fields are not covered by the population oracle: {label}')
    return {
        'include': set(config.get('include_fields') or ()),
        'exclude': set(config.get('exclude_fields') or ()),
    }


def _audit_value(field, value):
    """Source-derived intermediate value, before auditlog's final smart_str.

    The missing create instance yields text 'None' for every field. The new
    instance's scalar None also yields that text, but FK/datetime None stays
    raw and JSON None becomes 'null'. Compare intermediates before rendering;
    nullable FK/datetime creates deliberately contain ['None', 'None'].
    Pinned settings use USE_TZ=True, STORE_JSON_CHANGES=False and FK repr=False.
    """
    kind = getattr(field, 'get_internal_type', lambda: _field_type(field))()
    if kind == 'DateTimeField':
        value = field.to_python(value)
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if kind == 'JSONField':
        value = field.to_python(value)
        try:
            return json.dumps(value, sort_keys=True, cls=getattr(field, 'encoder', None))
        except TypeError:
            return value
    if getattr(field, 'many_to_one', False) or getattr(field, 'one_to_one', False):
        return value
    return str(value)


def _create_changes(item, row, audit_fields, related_rows=None):
    restrictions = _audit_fields_for(item['label_lower'], audit_fields)
    changes = {}
    meta = getattr(item.get('model'), '_meta', None)
    get_fields = getattr(meta, 'get_fields', None)
    # get_fields_in_model() includes named reverse managers. Looking only at
    # concrete snapshot fields would silently omit their source audit strings.
    fields = get_fields() if callable(get_fields) else item['fields'].values()
    for field in fields:
        name = field.name
        if getattr(field, 'many_to_many', False):
            continue
        remote_model = getattr(getattr(field, 'remote_field', None), 'model', None)
        if getattr(getattr(remote_model, '_meta', None), 'label', None) == AUDIT_LABEL:
            continue
        if restrictions['include'] and name not in restrictions['include']:
            continue
        if name in restrictions['exclude']:
            continue
        if getattr(field, 'concrete', True):
            value = _audit_value(field, row[field.attname])
        elif getattr(field, 'one_to_many', False):
            # Missing reverse query names fall back to 'None'. An explicit
            # accessor returns a manager; BaseManager.__str__ uses label/name,
            # independently of its row count and without querying its table.
            if field.get_accessor_name() != name:
                continue
            value = f'{field.related_model._meta.label}.None'
        else:
            # The only tracked reverse one-to-one in this pinned population
            # is User.sportassociation. The owner is saved before it exists;
            # collaborator users never own an association. Do not query it.
            _require(
                item['label_lower'] == 'application.user'
                and name == 'sportassociation'
                and getattr(field, 'one_to_one', False),
                f'unreviewed reverse audit field: {item["label_lower"]}.{name}',
            )
            _require(
                not any(str(other['user_id']) == str(row['user_id'])
                        for other in (related_rows or {}).get(
                            'application.SportAssociation', {}).values()),
                'User audit with an existing owned association is unreviewed',
            )
            continue
        if value != 'None':
            changes[name] = ['None', str(value)]
    return changes


def _event_changes(item, row, action, audit_fields, old_filepath=None,
                   related_rows=None, source=None):
    if action == CREATE_ACTION:
        create_row = deepcopy(row)
        # A post_save receiver sees constructor defaults before database field
        # preparation. For example DecimalField(default=0.0) logs '0.0', even
        # though values() returns Decimal('0.00'). Dynamic defaults retain the
        # already validated generated value; never evaluate them a second time.
        hook_fields = {
            'User': {'is_staff', 'is_superuser', 'two_fa', 'two_fa_secret',
                     'stripe_account_id', 'stripe_on_boarding_completed',
                     'integration_google_state', 'integration_google_credentials',
                     'integration_google_auto_sync', 'role', 'is_active'},
            'Payment': {'payment_category_id'},
            'Subscription': {'start_date', 'end_date'},
        }.get(item['name'], set())
        for name, field in item['fields'].items():
            if (source is not None and name not in source and name not in hook_fields
                    and not _is_auto_time(field) and _field_defaults(field)
                    and not callable(field.default)):
                create_row[name] = deepcopy(field.default)
        return _create_changes(item, create_row, audit_fields, related_rows)
    _require(action == UPDATE_ACTION, 'unsupported audit action')
    new_filepath = row.get('filepath')
    _require(isinstance(old_filepath, str) and isinstance(new_filepath, str),
             'Document update has no filepath pair')
    return {'filepath': [old_filepath, new_filepath]}


def _audit_change_summary(expected, actual):
    """Only source field names and Python types; never print stored values."""
    if not isinstance(actual, dict):
        return f'payload_type={type(actual).__name__}'

    def types(value):
        if isinstance(value, (list, tuple)):
            return f'{type(value).__name__}[{",".join(type(v).__name__ for v in value)}]'
        return type(value).__name__

    missing = sorted(set(expected) - set(actual))
    changed = [
        f'{name}:{types(expected[name])}/{types(actual[name])}'
        for name in sorted(set(expected) & set(actual))
        if not _same_value(expected[name], actual[name])
    ]
    return (f'missing={missing}; extra_fields={len(set(actual) - set(expected))}; '
            f'differing_types={changed}')


def _object_repr(item, row):
    try:
        instance = item['model'](**row)
        return str(instance)
    except Exception as exc:
        raise AssertionError(
            f'cannot derive audit object representation for {item["label"]}'
        ) from exc


def _relation_target(item, row, field_name):
    field = item['fields'].get(field_name)
    return None if field is None else row.get(field_name)


def _nested_association(rows, label, identity, relation, association_label):
    related = rows.get(label, {}).get(str(identity))
    if related is None:
        return None
    return related.get(f'{relation}_id') or related.get('sport_association_id')


def _resolution_path(
    item, row, rows, resolvers, changes, association_label,
):
    resolver = resolvers.get(item['label_lower'])
    association_id = None
    path = None
    if resolver == 'sport_association_self_resolver':
        association_id = row[item['pk']]
        path = 'self'
    elif resolver == 'direct_resolver':
        association_id = _relation_target(item, row, 'sport_association_id')
        path = 'direct'
    elif resolver == 'user_resolver':
        for association in rows.get(association_label, {}).values():
            if str(association.get('user_id')) == str(row[item['pk']]):
                association_id = association[item['pk']]
                path = 'user.sport_association'
                break
    elif resolver == 'associate_tutor_relation_resolver':
        association_id = _nested_association(
            rows, 'application.Associate', row.get('associate_id'),
            'sport_association', association_label,
        )
        path = 'associate.sport_association'
    elif resolver == 'course_subscription_resolver':
        association_id = _nested_association(
            rows, 'application.Course', row.get('course_id'),
            'sport_association', association_label,
        )
        path = 'course.sport_association'
    elif resolver == 'course_subscription_installment_resolver':
        related = rows.get('application.CourseSubscription', {}).get(
            str(row.get('course_subscription_id'))
        )
        if related is not None:
            association_id = _nested_association(
                rows, 'application.Course', related.get('course_id'),
                'sport_association', association_label,
            )
        path = 'course_subscription.course.sport_association'
    elif resolver in {
        'instructor_hours_resolver',
    }:
        related = rows.get('application.Instructor', {}).get(
            str(row.get('instructor_id'))
        )
        if related is not None:
            association_id = related.get('sport_association_id')
        path = 'instructor.sport_association'
    elif resolver in {
        'medical_certificate_resolver', 'medical_appointments_resolver',
        'subscription_related_resolver', 'signature_resolver',
    }:
        association_id = _nested_association(
            rows, 'application.Subscription', row.get('subscription_id'),
            'sport_association', association_label,
        )
        path = 'subscription.sport_association'
    elif resolver == 'camps_period_resolver':
        association_id = _nested_association(
            rows, 'application.CampsAndRetreats',
            row.get('camps_and_retreats_id'), 'sport_association',
            association_label,
        )
        path = 'camps_and_retreats.sport_association'
    elif resolver == 'camps_service_resolver':
        period = rows.get('application.CampsAndRetreatsPeriod', {}).get(
            str(row.get('camps_and_retreats_period_id'))
        )
        if period is not None:
            association_id = _nested_association(
                rows, 'application.CampsAndRetreats',
                period.get('camps_and_retreats_id'), 'sport_association',
                association_label,
            )
        path = 'period.camps_and_retreats.sport_association'
    elif resolver == 'camps_subscription_resolver':
        association_id = _nested_association(
            rows, 'application.CampsAndRetreats',
            row.get('camps_and_retreats_id'), 'sport_association',
            association_label,
        )
        path = 'camps_and_retreats.sport_association'
    elif resolver == 'camps_subscription_period_resolver':
        subscription = rows.get('application.CampsAndRetreatsSubscription', {}).get(
            str(row.get('camps_and_retreats_subscription_id'))
        )
        if subscription is not None:
            association_id = _nested_association(
                rows, 'application.CampsAndRetreats',
                subscription.get('camps_and_retreats_id'), 'sport_association',
                association_label,
            )
        path = 'subscription.camps_and_retreats.sport_association'
    elif resolver == 'invoice_rows_resolver':
        association_id = _nested_association(
            rows, 'application.Invoice', row.get('invoice_id'),
            'sport_association', association_label,
        )
        path = 'invoice.sport_association'
    elif resolver == 'attendance_registry_resolver':
        association_id = _nested_association(
            rows, 'application.Course', row.get('course_id'),
            'sport_association', association_label,
        )
        path = 'course.sport_association'
    elif resolver == 'attendance_day_resolver':
        registry = rows.get('application.AttendanceRegistry', {}).get(
            str(row.get('attendance_registry_id'))
        )
        if registry is not None:
            association_id = _nested_association(
                rows, 'application.Course', registry.get('course_id'),
                'sport_association', association_label,
            )
        path = 'attendance_registry.course.sport_association'
    elif resolver == 'module_responses_resolver':
        association_id = _nested_association(
            rows, 'application.Module', row.get('module_id'),
            'sport_association', association_label,
        )
        path = 'module.sport_association'
    elif resolver == 'carnet_subscription_resolver':
        association_id = _nested_association(
            rows, 'application.Carnet', row.get('carnet_id'),
            'sport_association', association_label,
        )
        path = 'carnet.sport_association'
        if association_id is None:
            association_id = _nested_association(
                rows, 'application.Subscription', row.get('subscription_id'),
                'sport_association', association_label,
            )
            path = 'subscription.sport_association'

    if association_id is None:
        values = changes.get('sport_association') if isinstance(changes, dict) else None
        if isinstance(values, (list, tuple)) and len(values) == 2:
            for value in (values[0], values[1]):
                if value not in (None, 'None') and str(value) in rows.get(
                    association_label, {}
                ):
                    association_id = value
                    path = 'changes_json.sport_association'
                    break
    if association_id is None:
        return None, None
    association = rows.get(association_label, {}).get(str(association_id))
    if association is None:
        return None, None
    return association, path


def _audit_events(
    imported_rows, labels, metadata, audit_fields, resolvers,
    archive, document_media,
):
    rows = {label: {} for label in metadata}
    events = []
    association_label = labels['SportAssociation']
    owner_id = str(
        archive['entries']['data/01_sport_association.json'][0]['user_id']
    )
    sources = {
        spec['name']: {
            str(source[metadata[labels[spec['name']]]['pk']]): source
            for source in archive['entries'][spec['file']]
        }
        for spec in archive['entries']['manifest.json']['models_exported']
        if spec['name'] in imported_rows
    }

    def add_event(name, row, action, old_filepath=None):
        item = metadata[labels[name]]
        label = item['label']
        rows.setdefault(label, {})[str(row[item['pk']])] = row
        if item['label_lower'] not in _AUDITED_LABELS[0]:
            return
        changes = _event_changes(
            item, row, action, audit_fields, old_filepath=old_filepath,
            related_rows=rows, source=sources[name][str(row[item['pk']])],
        )
        association, path = _resolution_path(
            item, row, rows, resolvers, changes, association_label,
        )
        events.append({
            'item': item,
            'name': name,
            'row': row,
            'action': action,
            'changes': changes,
            'association': association,
            'path': path,
        })

    owner = next(
        row for row in imported_rows['User']
        if str(row[metadata[labels['User']]['pk']]) == owner_id
    )
    source_documents = {
        str(row['document_id']): row.get('filepath')
        for row in archive['entries']['data/99_documents.json']
    }
    add_event('User', owner, CREATE_ACTION)
    add_event(
        'SportAssociation', imported_rows['SportAssociation'][0], CREATE_ACTION
    )
    for name in IMPORT_ORDER[1:]:
        for row in imported_rows.get(name, ()):
            if name == 'User' and str(
                row[metadata[labels['User']]['pk']]
            ) == owner_id:
                continue
            create_row = row
            if name == 'Document':
                create_row = deepcopy(row)
                create_row['filepath'] = source_documents[str(
                    row[metadata[labels['Document']]['pk']]
                )]
            add_event(name, create_row, CREATE_ACTION)

    documents_by_id = {
        str(row[metadata[labels['Document']]['pk']]): row
        for row in imported_rows['Document']
    }
    for document_id, _filename in document_media:
        document = documents_by_id[document_id]
        add_event(
            'Document', document, UPDATE_ACTION,
            old_filepath=source_documents.get(document_id),
        )
    return events


_AUDITED_LABELS = [set()]


def _content_type_ids(after):
    rows = after[CONTENT_TYPE_LABEL]
    mapping = {}
    for row in rows:
        key = f'{row.get("app_label")}.{str(row.get("model")).lower()}'
        _require(key not in mapping, f'duplicate ContentType: {key}')
        mapping[key] = row.get('id')
    return mapping


def _audit_difference(
    before, after, imported_rows, labels, metadata, archive, import_window,
    document_media, audited_models, audit_fields, resolvers,
):
    audited = _registry_labels(audited_models)
    _AUDITED_LABELS[0] = audited
    resolver_names = _normalize_resolvers(resolvers)
    events = _audit_events(
        imported_rows, labels, metadata, audit_fields, resolver_names,
        archive, document_media,
    )
    content_types = _content_type_ids(after)
    item = metadata[AUDIT_LABEL]
    index_item = metadata[AUDIT_INDEX_LABEL]
    _require(not before[AUDIT_LABEL], 'audit LogEntry baseline must be empty')
    _require(not before[AUDIT_INDEX_LABEL], 'audit index baseline must be empty')
    for log in after[AUDIT_LABEL]:
        _require(type(log.get('id')) is int and log['id'] > 0,
                 'audit LogEntry ID is invalid')
    logs = sorted(after[AUDIT_LABEL], key=lambda row: row[item['pk']])
    _require(len(logs) == len(events), 'audit LogEntry population differs from source saves')
    expected_by_log = {}
    for index, (event, log) in enumerate(zip(events, logs), 1):
        model_item = event['item']
        content_key = model_item['label_lower']
        _require(content_key in content_types, f'no ContentType for audit model: {content_key}')
        expected = {
            'content_type_id': content_types[content_key],
            'object_pk': str(event['row'][model_item['pk']]),
            'object_id': (
                event['row'][model_item['pk']]
                if isinstance(event['row'][model_item['pk']], int) else None
            ),
            'object_repr': _object_repr(model_item, event['row']),
            'serialized_data': None,
            'action': event['action'],
            'changes_text': '',
            'changes': event['changes'],
            'actor_id': None,
            'cid': None,
            'remote_addr': None,
            'remote_port': None,
            'additional_data': None,
            'actor_email': None,
        }
        _require(
            set(item['fields']) == set(expected) | {'id', 'timestamp'},
            'audit LogEntry metadata contains an unreviewed concrete field',
        )
        for field, value in expected.items():
            if not _same_value(value, log.get(field)):
                detail = (f' ({_audit_change_summary(value, log.get(field))})'
                          if field == 'changes' else '')
                raise AssertionError(f'audit {field} differs for event {index}{detail}')
        _require(isinstance(log.get('timestamp'), datetime),
                 'audit timestamp must be a datetime')
        _in_window(
            log.get('timestamp'), import_window,
            'audit timestamp is outside import request',
        )
        expected_by_log[log['id']] = event

    indexes = after[AUDIT_INDEX_LABEL]
    _require(
        set(index_item['fields']) == {
            'audit_log_index_id', 'log_entry_id', 'sport_association_id',
            'resolution_path', 'created_at',
        },
        'audit index metadata contains an unreviewed concrete field',
    )
    expected_indexes = [event for event in events if event['path']]
    _require(
        len(indexes) == len(expected_indexes),
        'audit index population differs from source resolver events',
    )
    index_by_log = {}
    for index_row in indexes:
        log_id = index_row.get('log_entry_id')
        _require(type(log_id) is int and log_id > 0,
                 'audit index LogEntry ID must be a positive integer')
        _require(log_id not in index_by_log, 'audit index duplicates a LogEntry')
        _require(log_id in expected_by_log, 'audit index references an unknown LogEntry')
        index_by_log[log_id] = index_row
    expected_index_by_log = {
        log['id']: event
        for log, event in zip(logs, events) if event['path']
    }
    _require(
        set(index_by_log) == set(expected_index_by_log),
        'audit index target population differs from resolver events',
    )
    association_field = index_item['fields']['sport_association_id']
    association_id = getattr(
        association_field, 'target_field', association_field
    ).to_python(contract_association_id(archive))
    for log_id, event in expected_index_by_log.items():
        row = index_by_log[log_id]
        association = event['association']
        _require(
            _same_value(association_id, row.get('sport_association_id')),
            'audit index has a foreign association',
        )
        _require(
            association is not None
            and _same_value(association_id, association.get(
                metadata[labels['SportAssociation']]['pk']
            )),
            'audit resolver association differs from source event',
        )
        _require(
            _same_value(event['path'], row.get('resolution_path')),
            'audit resolution path differs from source resolver hook',
        )
        identity = row[index_item['pk']]
        _require(
            isinstance(identity, uuid.UUID) and identity.version == 4,
            'audit index ID is not a generated UUID4',
        )
        _require(isinstance(row.get('created_at'), datetime),
                 'audit index creation time must be a datetime')
        _in_window(
            row.get('created_at'), import_window,
            'audit index creation time is outside import request',
        )
    return {
        'create': sum(event['action'] == CREATE_ACTION for event in events),
        'update': sum(event['action'] == UPDATE_ACTION for event in events),
    }, len(events), len(expected_indexes)


def contract_association_id(archive):
    return archive['entries']['data/01_sport_association.json'][0][
        'sport_association_id'
    ]


def _baseline_unchanged(before, after, metadata, labels):
    """Keep baseline validity separate from expected import/configuration adds."""
    imported = set(labels.values())
    expected_growth = {
        CONFIG_LABEL, TASK_RESULT_LABEL, AUDIT_LABEL, AUDIT_INDEX_LABEL,
    }
    for label in metadata:
        if label in imported:
            current = {
                str(row[metadata[label]['pk']]): row
                for row in after[label]
            }
            for row in before[label]:
                identity = str(row[metadata[label]['pk']])
                _require(
                    identity in current
                    and _canonical(current[identity]) == _canonical(row),
                    f'source baseline row changed: {label}',
                )
            continue
        if label in expected_growth:
            continue
        _require(
            sorted(_canonical(row) for row in after[label])
            == sorted(_canonical(row) for row in before[label]),
            f'source baseline population changed: {label}',
        )


def verify_exact_population(
    before,
    after,
    archive,
    contract,
    models,
    *,
    schema_models,
    skipped_records,
    task_id,
    migration_started_at,
    migration_finished_at,
    import_started_at,
    import_finished_at,
    configuration_started_at,
    configuration_finished_at,
    configuration_defaults,
    audited_models=(),
    audit_fields=None,
    resolvers=None,
    archive_model_labels=None,
):
    """Require the complete source-authored post-import/configuration state.

    models contains only live snapshot tables; schema_models is the full schema
    passed to post_migrate, including unmanaged, proxy, auto-created and swapped
    models. Historical classes and permission options remain distinct from
    live classes. Source-declared migration-only models can have metadata rows
    without becoming live snapshot tables.
    """
    _require(isinstance(task_id, str) and task_id, 'task_id must be a nonempty string')
    migration_window = _time_window(
        migration_started_at, migration_finished_at, 'migration'
    )
    import_window = _time_window(import_started_at, import_finished_at, 'import')
    configure_window = _time_window(
        configuration_started_at, configuration_finished_at, 'configuration'
    )
    _require(
        migration_window[1] <= import_window[0] <= import_window[1] <= configure_window[0],
        'migration/import/configure time windows are not ordered',
    )
    metadata = _metadata(models)
    schema = _schema_metadata(metadata, schema_models)
    _snapshot(before, metadata, 'before')
    _snapshot(after, metadata, 'after')
    specs = _manifest(archive)
    retired = _retired_counts(specs, contract, skipped_records)
    labels = _archive_labels(specs, metadata, archive_model_labels)
    _legacy_invoices(specs, labels, metadata)
    _hidden_relationships(specs, archive)
    _require(
        set(labels) == set(IMPORT_ORDER),
        'archive model labels do not cover the exact importer order',
    )
    _require(
        all(labels[name] in metadata for name in IMPORT_ORDER),
        'runtime model metadata is missing an imported model',
    )
    document_media = _validated_document_media(archive, specs)

    baseline = _validate_baseline(before, metadata, schema, migration_window)
    expected, policies = _expected_import_rows(
        specs, labels, metadata, archive, import_window, document_media, before
    )
    imported_rows = _validate_import_population(
        after, before, expected, policies, labels, metadata, import_window
    )
    _baseline_unchanged(before, after, metadata, labels)

    configuration = _configuration_difference(
        before, after, contract, configuration_defaults, configure_window, metadata
    )
    payload = _expected_task_payload(archive, specs, retired)
    task = _task_result_added(before, after, payload, task_id, import_window)
    audit_actions, audit_count, index_count = _audit_difference(
        before, after, imported_rows, labels, metadata, archive, import_window,
        document_media, audited_models, audit_fields, resolvers,
    )

    nonempty = sum(
        spec['count'] > 0
        for spec in specs
        if spec['name'] not in RETIRED_SECTIONS | {LEGACY_INVOICES}
    )
    empty = sum(
        spec['count'] == 0
        for spec in specs
        if spec['name'] not in RETIRED_SECTIONS | {LEGACY_INVOICES}
    )
    return {
        'exact': True,
        'archive_sections': len(specs),
        'imported_sections': nonempty + empty,
        'nonempty_sections': nonempty,
        'empty_sections': empty,
        'imported_rows': sum(
            spec['count'] for spec in specs
            if spec['name'] not in RETIRED_SECTIONS | {LEGACY_INVOICES}
        ),
        'retired_records': retired,
        'baseline_payment_categories': baseline['payment_categories'],
        'baseline_content_types': baseline['content_types'],
        'baseline_permissions': baseline['permissions'],
        'baseline_empty_tables': baseline['empty_tables'],
        'configuration': configuration,
        'task_result': task,
        'audit_actions': audit_actions,
        'allowed_new_rows': {
            CONFIG_LABEL: 1,
            TASK_RESULT_LABEL: 1,
            AUDIT_LABEL: audit_count,
            AUDIT_INDEX_LABEL: index_count,
        },
    }


def verify_unchanged_population(before, after, models):
    """Require table-for-table and typed-row equality after failed mutation."""
    metadata = _metadata(models)
    _snapshot(before, metadata, 'before')
    _snapshot(after, metadata, 'after')
    for label in metadata:
        _require(
            sorted(_canonical(row) for row in after[label])
            == sorted(_canonical(row) for row in before[label]),
            f'rollback changed {label}',
        )
    return {
        'unchanged': True,
        'tables': len(metadata),
        'rows': sum(len(rows) for rows in before.values()),
    }
