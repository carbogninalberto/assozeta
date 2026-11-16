"""Django's deletion graph for a Document hard delete, as the Go collector's table.

Document.delete() runs the deletion Collector rooted at docmanager.Document: every model that
references a Document (CASCADE, SET_NULL and the many-to-many through models) and whatever
cascades from those.  describe() reads only model metadata (no queries) and writes, in the format
of collaborator_delete_graph.json (TESTS/adapters/django/collaborator_delete_go_graph.py, whose
per-model description it reuses):

- ``models``/``order``: every model the Collector can reach from docmanager.Document through
  CASCADE, plus the models the ported pre_delete receivers delete;
- per model: candidate relations in ``_meta.get_fields(include_hidden=True)`` order, their
  on_delete, nullability and Django's own ``can_fast_delete`` verdict; pre/post_delete receivers;
  auditlog's delete-diff fields; ``__str__``/``get_additional_data`` source hashes.

Usage: python -I TESTS/adapters/go/document_deletion_graph.py --write | --check
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/repository/document_deletion_graph.json'


def describe():
    from django.db.models.deletion import Collector, get_candidate_relations_to_delete
    from django.db.models.signals import pre_delete, post_delete
    from auditlog.registry import auditlog
    from docmanager.models import Document
    from TESTS.adapters.django import collaborator_delete_go_graph as base

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

    visit(Document)
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
        pre, post = base._receivers(pre_delete, model), base._receivers(post_delete, model)
        assert pre == base.PORTED_RECEIVERS['pre_delete'].get(meta.label, []), ('pre_delete receivers', meta.label, pre)
        assert post in ([], [base.PORTED_RECEIVERS['post_delete']]), ('post_delete receivers', meta.label, post)
        assert bool(post) == auditlog.contains(model), ('auditlog receiver without registration', meta.label)
        text = base._defined(model, '__str__')
        additional = base._defined(model, 'get_additional_data')
        models[meta.label] = {
            'app_label': meta.app_label, 'model_name': meta.model_name, 'object_name': meta.object_name,
            'table': meta.db_table, 'pk': meta.pk.column, 'pk_type': meta.pk.get_internal_type(),
            'auto_created': bool(meta.auto_created), 'pre_delete': pre, 'post_delete': post,
            'fast_root': collector.can_fast_delete(model),
            'relations': relations,
            'audit': base._audit_fields(model) if auditlog.contains(model) else None,
            'str': None if text is None else {'class': text.__qualname__, 'sha256': base._source_hash(text.__str__)},
            'additional_data': None if additional is None else base._source_hash(additional.get_additional_data),
        }
    return {'scope': 'Collector graph from docmanager.Document (CASCADE closure)',
            'order': [model._meta.label for model in order], 'models': models}


def dumps(graph):
    return json.dumps(graph, indent=1, sort_keys=True) + '\n'


def main():
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    from TESTS.adapters.django import message_reads_runtime as reuse
    environment, _ = reuse.private_environment(Path('/nonexistent/document-deletion-graph'), 1, {})
    os.environ.update(environment)
    reuse.initialize_django_apps()
    text = dumps(describe())
    if '--write' in sys.argv:
        OUTPUT.write_text(text)
    elif '--check' in sys.argv:
        if not OUTPUT.is_file() or OUTPUT.stat().st_size == 0 or OUTPUT.read_text() != text:
            sys.exit('document_deletion_graph.json drifted from Django; rerun with --write')
    else:
        sys.exit('usage: document_deletion_graph.py --write | --check')
    print(f'{len(json.loads(text)["models"])} models')


if __name__ == '__main__':
    main()
