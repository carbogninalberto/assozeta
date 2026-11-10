"""The objects a Document belongs to, as the table Go's delete_document walks.

describe() reads only model metadata (no queries): docmanager.Document, every
model holding a foreign key or many-to-many link to it (``_meta.related_objects``,
what application.document_scope.referrers iterates) and the models referencing
those, with what resolve_association needs: the content type key of the audit
resolver, and the ``sport_association`` column when the model has one.

The committed BE_GO/internal/repository/document_delete_scope.json is this
output; the document-delete family's fixture recomputes it on the running
Django and fails on any drift. Regenerate with:

    python -m TESTS.adapters.django.document_delete_scope_graph --write
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/repository/document_delete_scope.json'
DEPTH = 2


def _referrers(model):
    rows = []
    for relation in model._meta.related_objects:
        field, related = relation.field, relation.related_model
        row = {'model': related._meta.label}
        if relation.many_to_many:
            through = field.remote_field.through._meta
            assert through.auto_created, ('explicit through model', related._meta.label, field.name)
            row.update(kind='m2m', through=through.db_table, source=field.m2m_column_name(), target=field.m2m_reverse_name())
            assert field.m2m_reverse_target_field_name() == model._meta.pk.name, ('m2m to a non-key field', related._meta.label)
        else:
            assert field.target_field is model._meta.pk, ('relation to a non-key field', related._meta.label, field.name)
            row.update(kind='fk', column=field.column)
        rows.append(row)
    return rows


def describe():
    from django.contrib.contenttypes.models import ContentType  # noqa: F401  (apps must be ready)
    from docmanager.models import Document
    models, level = {}, [Document]
    for depth in range(DEPTH + 1):
        following = []
        for model in level:
            meta = model._meta
            if meta.label in models:
                continue
            assert meta.concrete_model is model and not meta.proxy and not meta.parents, ('unsupported model', meta.label)
            association = None
            try:
                field = meta.get_field('sport_association')
                if field.many_to_one or field.one_to_one:
                    association = field.column
            except Exception:
                association = None
            entry = {'table': meta.db_table, 'pk': meta.pk.column, 'pk_type': meta.pk.get_internal_type(),
                     'content_type': f'{meta.app_label}.{meta.model_name}', 'sport_association': association,
                     'referrers': None}
            if depth < DEPTH:
                entry['referrers'] = _referrers(model)
                following.extend(relation.related_model for relation in meta.related_objects)
            models[meta.label] = entry
        level = following
    # A model first met at the last level may also be referenced earlier: its
    # referrers are only needed when it is reached above that level.
    for label, entry in models.items():
        if entry['referrers'] is None:
            continue
        for row in entry['referrers']:
            assert row['model'] in models, ('referrer outside the table', label, row['model'])
    return {'scope': 'docmanager.Document, its referrers and their referrers (application.document_scope)',
            'root': Document._meta.label, 'models': models}


def dumps(graph):
    return json.dumps(graph, indent=1, sort_keys=True) + '\n'


def main():
    import os
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    from TESTS.adapters.django import message_reads_runtime as reuse
    environment, _ = reuse.private_environment(Path('/nonexistent/document-delete-scope-graph'), 1, {})
    os.environ.update(environment)
    reuse.initialize_django_apps()
    text = dumps(describe())
    if '--write' in sys.argv:
        OUTPUT.write_text(text)
    elif OUTPUT.read_text() != text:
        sys.exit('document_delete_scope.json drifted from Django; rerun with --write')
    print(f'{len(json.loads(text)["models"])} models')


if __name__ == '__main__':
    main()
