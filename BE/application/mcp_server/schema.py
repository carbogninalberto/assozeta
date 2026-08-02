import logging

from django.apps import apps
from django.db import models

from application.mcp_server.security import MODEL_WHITELIST, is_field_allowed

logger = logging.getLogger(__name__)

FIELD_TYPE_MAP = {
    'AutoField': 'integer',
    'BigAutoField': 'integer',
    'SmallAutoField': 'integer',
    'UUIDField': 'uuid',
    'CharField': 'string',
    'TextField': 'text',
    'IntegerField': 'integer',
    'SmallIntegerField': 'integer',
    'BigIntegerField': 'integer',
    'PositiveIntegerField': 'integer',
    'PositiveSmallIntegerField': 'integer',
    'FloatField': 'float',
    'DecimalField': 'decimal',
    'BooleanField': 'boolean',
    'NullBooleanField': 'boolean',
    'DateField': 'date',
    'DateTimeField': 'datetime',
    'TimeField': 'time',
    'EmailField': 'email',
    'URLField': 'url',
    'FileField': 'file',
    'ImageField': 'image',
    'JSONField': 'json',
    'BinaryField': 'binary',
    'DurationField': 'duration',
    'GenericIPAddressField': 'ip_address',
    'SlugField': 'string',
    'FilePathField': 'string',
}

# Internal structure documentation for JSONFields.
# Key: "ModelName.field_name" -> description of the JSON content.
JSON_FIELD_SCHEMAS = {
    'AttendanceRegistry.events': {
        'description': 'Lista di eventi/lezioni del corso con date e orari',
        'type': 'array',
        'items': (
            '{"event_id": "UUID", "title": "string", '
            '"start": "ISO datetime", "end": "ISO datetime", '
            '"allDay": bool, "extendedProps": {"description": "string", '
            '"instructor": {"instructor_id": "UUID", "label": "string"}}}'
        ),
    },
    'AttendanceDay.attendees': {
        'description': (
            'Lista dei presenti alla lezione. Ogni elemento ha un course_subscription_id. '
            'Se un iscritto al corso e\' presente in questa lista, era PRESENTE alla lezione.'
        ),
        'type': 'array',
        'items': '{"course_subscription_id": "UUID"}',
    },
    'AttendanceDay.expected_absences': {
        'description': (
            'Lista delle assenze previste. Stessa struttura di attendees. '
            'Se un iscritto e\' qui, era ASSENTE alla lezione.'
        ),
        'type': 'array',
        'items': '{"course_subscription_id": "UUID"}',
    },
    'CarnetSubscription.meta': {
        'description': (
            'Dati del carnet: lezioni rimanenti e storico utilizzo. '
            'Usa meta__lessons_left per filtrare i carnet con lezioni disponibili.'
        ),
        'type': 'object',
        'items': '{"lessons_left": int, "lessons_registry": [{"date": "ISO", "attendance_day_id": "UUID", "course": {"id": "UUID", "title": "string"}}]}',
    },
}

RELATION_TYPE_MAP = {
    'ForeignKey': 'foreign_key',
    'OneToOneField': 'one_to_one',
    'ManyToManyField': 'many_to_many',
    'ManyToManyRel': 'many_to_many_reverse',
    'ManyToOneRel': 'foreign_key_reverse',
    'OneToOneRel': 'one_to_one_reverse',
}


def _get_field_info(field, model_name: str = '') -> dict | None:
    """Extract field information from a Django model field."""
    if not is_field_allowed(field.name):
        return None

    field_type = type(field).__name__
    mapped_type = FIELD_TYPE_MAP.get(field_type, field_type)

    info = {
        'name': field.name,
        'type': mapped_type,
        'verbose_name': str(getattr(field, 'verbose_name', field.name)),
    }

    help_text = getattr(field, 'help_text', '')
    if help_text:
        info['help_text'] = str(help_text)

    choices = getattr(field, 'choices', None)
    if choices:
        info['choices'] = [
            {'value': c[0], 'label': str(c[1])} for c in choices
        ]

    # Required = not blank and not null and no default
    is_blank = getattr(field, 'blank', False)
    is_null = getattr(field, 'null', True)
    has_default = field.has_default()
    info['is_required'] = not is_blank and not is_null and not has_default

    max_length = getattr(field, 'max_length', None)
    if max_length:
        info['max_length'] = max_length

    # Add JSON structure hints for JSONFields
    if mapped_type == 'json' and model_name:
        json_key = f'{model_name}.{field.name}'
        json_schema = JSON_FIELD_SCHEMAS.get(json_key)
        if json_schema:
            info['json_structure'] = json_schema

    return info


def _get_relation_info(field) -> dict | None:
    """Extract relation information from a Django model relation field."""
    field_type = type(field).__name__
    relation_type = RELATION_TYPE_MAP.get(field_type)

    if not relation_type:
        return None

    # Get related model label
    related_model = field.related_model
    if not related_model:
        return None

    related_label = f"{related_model._meta.app_label}.{related_model.__name__}"

    info = {
        'name': field.name,
        'relation_type': relation_type,
        'related_model': related_label,
    }

    related_name = getattr(field, 'related_name', None)
    if related_name:
        info['related_name'] = str(related_name)

    verbose_name = getattr(field, 'verbose_name', None)
    if verbose_name:
        info['verbose_name'] = str(verbose_name)

    # Add query hints for FK/O2O relations to help LLM build correct traversals
    if relation_type in ('foreign_key', 'one_to_one'):
        # Show how to use this relation in fields/filters
        info['usage'] = {
            'id_field': f'{field.name}_id',
            'traverse_prefix': f'{field.name}__',
            'example': f'{field.name}__id (or {field.name}__<any_field_of_{related_model.__name__}>)',
        }

    return info


def get_full_schema(app_label: str = None) -> dict:
    """
    Get schema for all whitelisted models.

    Args:
        app_label: Optional filter to only include models from a specific app.

    Returns:
        Dictionary with model schemas.
    """
    schema = {
        'models': [],
        'note': (
            'Tutte le query sono automaticamente filtrate per la tua associazione sportiva. '
            'Per accedere a campi di modelli collegati via FK, usa la notazione doppio underscore: '
            'ad esempio, su CourseSubscription usa subscription__associate__first_name per il nome del tesserato. '
            'Ogni relazione ha un campo "usage" che mostra il prefisso corretto da usare.'
        ),
    }

    for model_label, config in MODEL_WHITELIST.items():
        label_app, label_model = model_label.split('.')

        if app_label and label_app != app_label:
            continue

        try:
            model = apps.get_model(label_app, label_model)
        except LookupError:
            logger.warning(f"Model not found: {model_label}")
            continue

        meta = model._meta
        model_info = {
            'model_name': label_model,
            'app_label': label_app,
            'full_label': model_label,
            'verbose_name': str(meta.verbose_name),
            'verbose_name_plural': str(meta.verbose_name_plural),
            'fields': [],
            'relations': [],
        }

        # Concrete fields (columns in the DB)
        for field in meta.get_fields():
            if field.is_relation:
                rel_info = _get_relation_info(field)
                if rel_info:
                    model_info['relations'].append(rel_info)
            else:
                field_info = _get_field_info(field, model_name=label_model)
                if field_info:
                    model_info['fields'].append(field_info)

        schema['models'].append(model_info)

    return schema


def get_model_schema(model_label: str) -> dict | None:
    """Get schema for a single model."""
    if model_label not in MODEL_WHITELIST:
        return None

    label_app, label_model = model_label.split('.')
    try:
        model = apps.get_model(label_app, label_model)
    except LookupError:
        return None

    meta = model._meta
    model_info = {
        'model_name': label_model,
        'app_label': label_app,
        'full_label': model_label,
        'verbose_name': str(meta.verbose_name),
        'verbose_name_plural': str(meta.verbose_name_plural),
        'fields': [],
        'relations': [],
    }

    for field in meta.get_fields():
        if field.is_relation:
            rel_info = _get_relation_info(field)
            if rel_info:
                model_info['relations'].append(rel_info)
        else:
            field_info = _get_field_info(field, model_name=label_model)
            if field_info:
                model_info['fields'].append(field_info)

    return model_info
