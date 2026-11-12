"""Django's deletion graph for a User hard delete, as the Go collector's table.

describe() reads only model metadata (no queries): every model the
deletion Collector can reach from application.User through CASCADE
(plus the models the ported pre_delete receivers delete), in collection
order, with what Django's Collector and auditlog's delete entry use:

- candidate relations in ``_meta.get_fields(include_hidden=True)`` order,
  their on_delete, nullability and Django's own ``can_fast_delete`` verdict;
- pre/post_delete receivers (Collector._has_signal_listeners);
- auditlog registration, include/exclude/mask fields and, for each tracked
  field, how ``get_field_value`` renders it on delete;
- whether the model defines ``__str__`` (object_repr) or
  ``get_additional_data``, with a hash of their source.

The committed BE_GO/internal/repository/collaborator_delete_graph.json is
this output; the HTTP family's fixture recomputes it on the running Django
and fails on any drift. Regenerate with:

    python -m TESTS.adapters.django.collaborator_delete_go_graph --write
"""
import hashlib
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/repository/collaborator_delete_graph.json'
# Receivers the Go collector implements; any other receiver on a reached model
# is a drift failure.
PORTED_RECEIVERS = {
    'pre_delete': {
        'application.Payment': ['application.signals.delete_installment_of_payment'],
        'application.CourseSubscription': ['application.signals.delete_related_course_payment'],
        'application.CourseSubscriptionInstallment': ['application.signals.delete_related_payment'],
    },
    'post_delete': 'auditlog.receivers.log_delete',
}


def _receivers(signal, model):
    synchronous, asynchronous = signal._live_receivers(model)
    return sorted(receiver.__module__ + '.' + receiver.__qualname__ for receiver in (*synchronous, *asynchronous))


def _source_hash(function):
    return hashlib.sha256(inspect.getsource(function).encode()).hexdigest()


def _defined(model, name):
    from django.db import models
    for klass in model.__mro__:
        if name in vars(klass):
            return None if klass in (models.Model, object) else klass
    return None


def _audit_fields(model):
    """auditlog's delete diff fields (get_fields_in_model, include/exclude)."""
    from auditlog.diff import get_fields_in_model
    from auditlog.registry import auditlog
    from django.db.models import DateTimeField, JSONField
    config = auditlog.get_model_fields(model)
    fields = get_fields_in_model(model(pk=None))
    if config['include_fields']:
        fields = [field for field in fields if field.name in config['include_fields']]
    if config['exclude_fields']:
        fields = [field for field in fields if field.name not in config['exclude_fields']]
    rows = []
    for field in sorted(fields, key=lambda field: field.name):
        row = {'name': field.name}
        if field.concrete:
            row['column'] = field.column
            row['null'] = field.null
            if isinstance(field, DateTimeField):
                row['kind'] = 'datetime'
            elif isinstance(field, JSONField):
                row['kind'] = 'json'
            elif (field.one_to_one or field.many_to_one) and hasattr(field, 'rel_class'):
                row['kind'] = 'fk'
            else:
                row['kind'] = 'value'
                row['type'] = field.get_internal_type()
        elif field.auto_created and (field.one_to_many or field.one_to_one):
            # A reverse relation: getattr(obj, field.name) works only when the
            # accessor is that name; otherwise the default 'None' equals the
            # absent side and the field is not in the diff.
            if field.get_accessor_name() != field.name:
                continue
            if field.one_to_many:
                row['kind'] = 'manager'
                row['text'] = f'{field.related_model._meta.label}.None'
            else:
                row['kind'] = 'reverse_one'
                row['related'] = field.related_model._meta.label
                row['column'] = field.field.column
        else:
            row['kind'] = 'unsupported:' + type(field).__name__
        rows.append(row)
    return {'include': sorted(config['include_fields']), 'exclude': sorted(config['exclude_fields']),
            'mask': sorted(config['mask_fields']), 'serialize_data': bool(auditlog.get_serialize_options(model)['serialize_data']),
            'fields': rows}


def describe():
    from auditlog.registry import auditlog
    from django.db.models.deletion import Collector, get_candidate_relations_to_delete
    from django.db.models.signals import pre_delete, post_delete
    from application.models import User, Payment, CourseSubscriptionInstallment

    collector = Collector('default')
    order, models = [], {}

    def visit(model):
        label = model._meta.label
        if label in models:
            return
        models[label] = None
        order.append(model)
        for related in get_candidate_relations_to_delete(model._meta):
            if related.field.remote_field.on_delete.__name__ == 'CASCADE':
                visit(related.related_model)

    for root in (User, Payment, CourseSubscriptionInstallment):
        visit(root)
    for model in order:
        meta = model._meta
        assert not meta.parents, ('multi-table inheritance is not supported', meta.label)
        assert not [field for field in meta.private_fields if hasattr(field, 'bulk_related_objects')], ('generic relation', meta.label)
        assert meta.concrete_model is model and not meta.proxy, ('proxy model', meta.label)
        relations = []
        for related in get_candidate_relations_to_delete(meta):
            field = related.field
            relations.append({
                'model': related.related_model._meta.label, 'table': related.related_model._meta.db_table,
                'field': field.name, 'column': field.column,
                'target_column': field.target_field.column, 'on_delete': field.remote_field.on_delete.__name__,
                'null': field.null, 'fast': collector.can_fast_delete(related.related_model, from_field=field),
            })
        pre, post = _receivers(pre_delete, model), _receivers(post_delete, model)
        assert pre == PORTED_RECEIVERS['pre_delete'].get(meta.label, []), ('pre_delete receivers', meta.label, pre)
        assert post in ([], [PORTED_RECEIVERS['post_delete']]), ('post_delete receivers', meta.label, post)
        assert bool(post) == auditlog.contains(model), ('auditlog receiver without registration', meta.label)
        text = _defined(model, '__str__')
        additional = _defined(model, 'get_additional_data')
        models[meta.label] = {
            'app_label': meta.app_label, 'model_name': meta.model_name, 'object_name': meta.object_name,
            'table': meta.db_table, 'pk': meta.pk.column, 'pk_type': meta.pk.get_internal_type(),
            'auto_created': bool(meta.auto_created), 'pre_delete': pre, 'post_delete': post,
            'fast_root': collector.can_fast_delete(model),
            'relations': relations,
            'audit': _audit_fields(model) if auditlog.contains(model) else None,
            'str': None if text is None else {'class': text.__qualname__, 'sha256': _source_hash(text.__str__)},
            'additional_data': None if additional is None else _source_hash(additional.get_additional_data),
        }
    return {'scope': 'Collector graph from application.User (CASCADE closure) plus Payment and CourseSubscriptionInstallment',
            'order': [model._meta.label for model in order], 'models': models}


def dumps(graph):
    return json.dumps(graph, indent=1, sort_keys=True) + '\n'


def main():
    import os
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    from TESTS.adapters.django import message_reads_runtime as reuse
    environment, _ = reuse.private_environment(Path('/nonexistent/collaborator-delete-graph'), 1, {})
    os.environ.update(environment)
    reuse.initialize_django_apps()
    text = dumps(describe())
    if '--write' in sys.argv:
        OUTPUT.write_text(text)
    elif OUTPUT.read_text() != text:
        sys.exit('collaborator_delete_graph.json drifted from Django; rerun with --write')
    print(f'{len(json.loads(text)["models"])} models')


if __name__ == '__main__':
    main()
