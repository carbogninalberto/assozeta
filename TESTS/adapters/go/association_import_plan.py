"""Generate the Go import plan of AssociationImportService (BE_GO/internal/importeffects/plan.json).

The import creates every archived record through ``model(**kwargs).save()``. What that does per model is read from
Django here, not restated by hand: for each model of IMPORT_ORDER (plus Folder) the concrete fields in
``_meta.get_fields()`` order with their kind, column, database type, nullability, default (constant or the callable's
name), ``auto_now``/``auto_now_add``, decimal shape and foreign-key target; the auditlog registration (include,
exclude, mask, m2m, serialize options); what auditlog's create diff renders for the non-concrete tracked fields (a
reverse accessor with a ``related_name`` renders its manager, ``app.Model.None``); the many-to-many through tables;
the ``__str__`` implementation; and the ``save()`` override in effect. The importer's own tables (IMPORT_ORDER,
PK_FIELDS, FK_MAPPINGS) are copied as Django defines them.

Usage: python -I TESTS/adapters/go/association_import_plan.py <output.json>    (``--check <file>`` compares)
Run with the production Python of the Go migration tooling; no database connection is opened.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.go.association_export_plan import setup  # noqa: E402

KINDS = {
    'UUIDField': 'uuid', 'DecimalField': 'decimal', 'DateTimeField': 'datetime', 'DateField': 'date',
    'TimeField': 'time', 'JSONField': 'json', 'BooleanField': 'bool', 'CharField': 'text', 'TextField': 'text',
    'EmailField': 'text', 'SlugField': 'text', 'URLField': 'text',
    'IntegerField': 'int', 'PositiveIntegerField': 'int', 'PositiveSmallIntegerField': 'int',
    'SmallIntegerField': 'int', 'BigIntegerField': 'int', 'PositiveBigIntegerField': 'int', 'AutoField': 'int',
    'BigAutoField': 'int', 'SmallAutoField': 'int',
}
# _parse_value converts by type(field).__name__; every other type keeps the decoded JSON value.
PARSED = {'UUIDField', 'DateTimeField', 'DateField', 'DecimalField', 'BooleanField', 'IntegerField',
          'PositiveIntegerField', 'PositiveSmallIntegerField'}


class Blocked(Exception):
    pass


def default_of(field):
    from django.db.models import NOT_PROVIDED
    default = field.default
    if default is NOT_PROVIDED:
        return {'kind': 'none'}
    if callable(default):
        return {'kind': 'callable', 'name': f'{default.__module__}.{default.__qualname__}'}
    if default is None:
        return {'kind': 'value', 'type': 'None'}
    if isinstance(default, bool):
        return {'kind': 'value', 'type': 'bool', 'value': default}
    if isinstance(default, int):
        return {'kind': 'value', 'type': 'int', 'value': default}
    if isinstance(default, str):
        return {'kind': 'value', 'type': 'str', 'value': default}
    if isinstance(default, Decimal):
        return {'kind': 'value', 'type': 'Decimal', 'value': str(default)}
    if isinstance(default, float):
        return {'kind': 'value', 'type': 'float', 'value': repr(default)}
    raise TypeError(f'unsupported default {field.model.__name__}.{field.name} = {default!r}')


def field_plan(field, pk_fields):
    from django.db import connection
    internal = field.get_internal_type()
    entry = {'name': field.name, 'attname': field.attname, 'column': field.column, 'null': field.null,
             'db_type': field.db_type(connection), 'default': default_of(field), 'type': type(field).__name__,
             'primary_key': field.primary_key}
    if field.is_relation:
        target = field.target_field
        target_type = type(target).__name__
        entry.update(kind='fk', target_model=field.related_model.__name__, target_app=field.related_model._meta.app_label,
                     target_type=target_type, target_kind=KINDS[target.get_internal_type()],
                     mapped=field.related_model.__name__ in pk_fields, one_to_one=field.one_to_one,
                     self_reference=field.related_model is field.model)
        return entry
    if internal not in KINDS:
        raise TypeError(f'unsupported field {field.model.__name__}.{field.name} ({internal})')
    entry.update(kind=KINDS[internal], parsed=type(field).__name__ in PARSED)
    if internal == 'DecimalField':
        entry.update(max_digits=field.max_digits, decimal_places=field.decimal_places)
    if internal in ('DateTimeField', 'DateField', 'TimeField'):
        entry.update(auto_now=bool(field.auto_now), auto_now_add=bool(field.auto_now_add))
    if internal == 'JSONField':
        encoder = field.encoder
        entry['encoder'] = None if encoder is None else f'{encoder.__module__}.{encoder.__qualname__}'
    if internal == 'CharField':
        entry['max_length'] = field.max_length
    return entry


def reverse_renders(model):
    """auditlog's create diff for the tracked non-concrete fields of a saved instance."""
    from auditlog.diff import get_field_value, get_fields_in_model
    from django.db import connection
    from django.db.utils import OperationalError

    def block(*args, **kwargs):
        raise Blocked()
    instance = model(pk=UUID(int=1) if model._meta.pk.get_internal_type() == 'UUIDField' else 1)
    instance._state.adding = False
    renders = {}
    for field in get_fields_in_model(instance):
        if getattr(field, 'concrete', False):
            continue
        try:
            with connection.execute_wrapper(block):
                value = get_field_value(instance, field)
        except (Blocked, OperationalError):
            # No database is configured here: any query attempt ends in one of these.
            if not field.one_to_one:
                raise TypeError(f'unexpected query rendering {model.__name__}.{field.name}')
            # Reverse one-to-one: the related row does not exist yet when the record is created.
            continue
        old = get_field_value(None, field)
        if value != old:
            renders[field.name] = str(value)
    return renders


def str_kind(model):
    from django.db import models
    if model.__str__ is models.Model.__str__:
        return 'default'
    known = {'User.__str__': 'user', 'CourseSubscription.__str__': 'dict'}
    name = model.__str__.__qualname__
    if name not in known:
        raise TypeError(f'unexpected __str__ {name}')
    return known[name]


def save_kind(model):
    from mptt.models import MPTTModel
    if issubclass(model, MPTTModel):
        return 'mptt'
    owner = next(cls for cls in model.__mro__ if 'save' in vars(cls))
    if owner.__module__.startswith('django.'):
        return 'default'
    known = {'Payment': 'payment', 'Subscription': 'subscription', 'Course': 'course'}
    if owner is not model or model.__name__ not in known:
        raise TypeError(f'unexpected save override {owner.__module__}.{owner.__qualname__}')
    return known[model.__name__]


def model_plan(model, pk_fields):
    from auditlog.registry import auditlog
    from django.db import models
    meta = model._meta
    fields = [field_plan(field, pk_fields) for field in meta.get_fields() if getattr(field, 'concrete', False)
              and not field.many_to_many]
    if [f['column'] for f in fields] != [f.column for f in meta.concrete_fields]:
        raise TypeError(f'field order of {model.__name__}')
    audit = None
    if auditlog.contains(model):
        options = auditlog.get_model_fields(model)
        registered = auditlog._registry[model]
        if options['mask_fields'] or registered['m2m_fields'] or registered['serialize_data']:
            raise TypeError(f'unsupported auditlog options for {model.__name__}: {options}')
        audit = {'include': sorted(options['include_fields'] or []), 'exclude': sorted(options['exclude_fields'] or []),
                 'reverse': reverse_renders(model),
                 'additional_data': callable(getattr(model, 'get_additional_data', None))}
        if audit['additional_data']:
            raise TypeError(f'unsupported get_additional_data on {model.__name__}')
    m2m = []
    for field in meta.many_to_many:
        through = field.remote_field.through
        m2m.append({'name': field.name, 'table': through._meta.db_table,
                    'source': field.m2m_column_name(), 'target': field.m2m_reverse_name(),
                    'target_model': field.related_model.__name__, 'target_table': field.related_model._meta.db_table,
                    'target_pk': field.related_model._meta.pk.column,
                    'target_kind': KINDS[field.related_model._meta.pk.get_internal_type()],
                    'auto_created': bool(through._meta.auto_created)})
    managers = {'unfiltered': 'original_objects' if hasattr(model, 'original_objects') else '_base_manager'}
    return {'name': model.__name__, 'app': meta.app_label, 'model': meta.model_name, 'table': meta.db_table,
            'pk': {'name': meta.pk.name, 'attname': meta.pk.attname, 'column': meta.pk.column,
                   'kind': KINDS[meta.pk.get_internal_type()]},
            'fields': fields, 'audit': audit, 'm2m': m2m, 'str': str_kind(model), 'save': save_kind(model),
            'self_reference': any(getattr(f, 'concrete', False) and getattr(f, 'many_to_one', False)
                                  and f.related_model is model for f in meta.get_fields()),
            'managers': managers, 'is_mptt': save_kind(model) == 'mptt'}


def generate():
    setup()
    from django.conf import settings
    from application.models import Folder
    from application.services.import_service import AssociationImportService as Service
    from application.services.legacy_backup import DEPRECATED_SECTIONS
    order = [{'file': name, 'model': model.__name__} for name, model in Service.IMPORT_ORDER]
    classes = [model for _, model in Service.IMPORT_ORDER] + [Folder]
    seen, models = set(), []
    for model in classes:
        if model.__name__ in seen:
            continue
        seen.add(model.__name__)
        models.append(model_plan(model, Service.PK_FIELDS))
    settings_used = {key: getattr(settings, key) for key in (
        'AUDITLOG_DISABLE_ON_RAW_SAVE', 'AUDITLOG_STORE_JSON_CHANGES', 'AUDITLOG_USE_FK_STRING_REPRESENTATION',
        'USE_TZ', 'TIME_ZONE')}
    if settings_used['AUDITLOG_STORE_JSON_CHANGES'] or settings_used['AUDITLOG_USE_FK_STRING_REPRESENTATION'] \
            or not settings_used['USE_TZ']:
        raise TypeError(f'unsupported auditlog settings {settings_used}')
    return {'format': 1, 'order': order, 'pk_fields': Service.PK_FIELDS,
            'fk_mappings': {k: [list(p) for p in v] for k, v in Service.FK_MAPPINGS.items()},
            'deprecated_sections': DEPRECATED_SECTIONS, 'settings': settings_used, 'models': models}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--check':
        target = Path(sys.argv[2])
        if not target.is_file() or target.stat().st_size == 0:
            raise SystemExit('missing plan to check')
        if json.loads(target.read_text()) != generate():
            raise SystemExit('import plan is stale: regenerate it')
        print('import plan current')
        return
    if len(sys.argv) != 2 or not sys.argv[1]:
        raise SystemExit('usage: association_import_plan.py <output.json> | --check <file>')
    plan = generate()
    if not plan['models'] or not plan['order']:
        raise SystemExit('empty plan')
    Path(sys.argv[1]).write_text(json.dumps(plan, indent=1, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
