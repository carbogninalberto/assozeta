"""Generate the Go export plan of AssociationExportService (BE_GO/internal/associationexport/plan.json).

For every EXPORT_ORDER entry it records what serialize_record writes, in order (key, column, kind), and the SQL
Django runs: the export queryset of get_queryset_for_model (compiled with placeholder ids so the Go side binds the
same parameters in the same places) and, for M2M_FIELDS, the prefetch query of one chunk. The Go export executes
exactly these statements, so PostgreSQL plans (and the order of rows without ORDER BY) match Django's.

Usage: python -I TESTS/adapters/go/association_export_plan.py <output.json>    (``--check <file>`` compares)
Run with the production Python of the Go migration tooling; no database connection is opened.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

ROOT = Path(__file__).resolve().parents[3]

ASSOCIATION = UUID('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')
OWNER = UUID('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb')
SOURCE_A = UUID('cccccccc-cccc-4ccc-8ccc-cccccccccccc')
SOURCE_B = UUID('dddddddd-dddd-4ddd-8ddd-dddddddddddd')
DOCUMENT_A = UUID('eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee')
DOCUMENT_B = UUID('ffffffff-ffff-4fff-8fff-ffffffffffff')
SYMBOLS = {ASSOCIATION: 'association', OWNER: 'owner'}


def setup():
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    import environ
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings', TESTS_DATABASE='assozeta_contract_plan',
                      DBTYPE='POSTGRES', DBHOST='/nonexistent', DBNAME='assozeta_contract_plan', DBUSER='plan',
                      DBPASSWORD='plan', JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='plan',
                      JWT_PUBLIC_KEY='plan')
    import logging
    logging.disable(logging.CRITICAL)
    import django
    with patch('environ.Env.read_env'):
        django.setup()


def symbolic(params):
    out = []
    for value in params:
        if isinstance(value, UUID) and value in SYMBOLS:
            out.append({'symbol': SYMBOLS[value]})
        elif isinstance(value, bool):
            out.append({'bool': value})
        elif isinstance(value, int):
            out.append({'int': value})
        elif isinstance(value, str):
            out.append({'str': value})
        else:
            raise TypeError(f'unexpected export query parameter {value!r}')
    return out


def field_kind(field):
    from django.db import models
    internal = field.get_internal_type()
    kinds = {
        'UUIDField': 'uuid', 'DecimalField': 'decimal', 'DateTimeField': 'datetime', 'DateField': 'date',
        'TimeField': 'time', 'JSONField': 'json', 'BooleanField': 'bool', 'CharField': 'text', 'TextField': 'text',
        'EmailField': 'text', 'SlugField': 'text', 'URLField': 'text',
        'IntegerField': 'int', 'PositiveIntegerField': 'int', 'PositiveSmallIntegerField': 'int',
        'SmallIntegerField': 'int', 'BigIntegerField': 'int', 'PositiveBigIntegerField': 'int', 'AutoField': 'int',
        'BigAutoField': 'int', 'SmallAutoField': 'int',
    }
    if internal not in kinds or isinstance(field, (models.FileField,)):
        raise TypeError(f'unsupported field {field.model.__name__}.{field.name} ({internal})')
    return kinds[internal]


def model_plan(service, item):
    model = item[0]
    name = model.__name__
    excluded = service.EXCLUDED_FIELDS.get(name, [])
    fields = []
    for field in model._meta.get_fields():
        if field.one_to_many or field.many_to_many:
            continue
        if field.name in excluded:
            continue
        if field.is_relation and (field.many_to_one or field.one_to_one):
            if getattr(field, 'concrete', False):
                target = field.target_field.get_internal_type()
                fields.append({'key': f'{field.name}_id', 'column': field.column, 'kind': 'fk',
                               'target': 'int' if 'Integer' in target or 'Auto' in target else 'uuid'
                               if target == 'UUIDField' else 'text'})
            else:
                fields.append({'key': f'{field.name}_id', 'kind': 'null'})
            continue
        if field.is_relation:
            raise TypeError(f'unexpected relation {name}.{field.name}')
        fields.append({'key': field.name, 'column': field.column, 'kind': field_kind(field)})
    is_system = len(item) > 2 and item[2] == 'system'
    plan = {'model': name, 'file': f"{'system' if is_system else 'data'}/{item[1]}.json", 'fields': fields,
            'table': model._meta.db_table, 'pk': model._meta.pk.column,
            'pk_kind': 'int' if model._meta.pk.get_internal_type() in ('AutoField', 'BigAutoField') else 'uuid'}
    if name == 'Document':
        qs = service.get_queryset_for_model(model, is_system)
        service.document_ids = {DOCUMENT_A, DOCUMENT_B}
        qs = service.get_queryset_for_model(model, is_system)
        sql, params = qs.query.sql_with_params()
        plan['query'] = {'sql': sql, 'params': [{'documents': True} if v in (DOCUMENT_A, DOCUMENT_B) else symbolic([v])[0]
                                               for v in params]}
        service.document_ids = set()
    else:
        qs = service.get_queryset_for_model(model, is_system)
        sql, params = qs.query.sql_with_params()
        plan['query'] = {'sql': sql, 'params': symbolic(params)}
    plan['columns'] = [f.column for f in model._meta.concrete_fields]
    m2m = []
    for field_name in service.M2M_FIELDS.get(name, []):
        if not hasattr(model, field_name):
            continue
        descriptor = getattr(model, field_name)
        manager_class = descriptor.related_manager_cls
        sources = [model(pk=SOURCE_A), model(pk=SOURCE_B)]
        manager = manager_class(sources[0])
        queryset, *_ = manager.get_prefetch_querysets(sources)
        psql, pparams = queryset.query.sql_with_params()
        source_params = [i for i, v in enumerate(pparams) if v in (SOURCE_A, SOURCE_B)]
        if len(source_params) != 2 or source_params[1] != source_params[0] + 1:
            raise TypeError(f'unexpected prefetch parameters for {name}.{field_name}')
        related = descriptor.field.related_model if descriptor.reverse is False else descriptor.rel.related_model
        m2m.append({'key': f'_m2m_{field_name}', 'sql': psql, 'params': [
            {'sources': True} if v == SOURCE_A else None if v == SOURCE_B else symbolic([v])[0] for v in pparams],
            'source_column': f'_prefetch_related_val_{manager.source_field_name}_id'
            if hasattr(manager, 'source_field_name') else None,
            'target_pk': related._meta.pk.column})
    plan['m2m'] = [dict(entry, params=[p for p in entry['params'] if p is not None]) for entry in m2m]
    # _collect_document_references reads these attributes of every exported object.
    plan['document_attributes'] = []
    for attribute in ('document_id', 'document_pdf_id', 'pdf_id'):
        if not hasattr(model, attribute):
            continue
        columns = [f.column for f in model._meta.concrete_fields if f.attname == attribute]
        if len(columns) != 1:
            raise TypeError(f'unexpected document attribute {name}.{attribute}')
        plan['document_attributes'].append(columns[0])
    return plan


def query(qs, documents=False):
    sql, params = qs.query.sql_with_params()
    out = []
    for value in params:
        if value in (DOCUMENT_A, DOCUMENT_B):
            out.append({'documents': True})
        else:
            out.append(symbolic([value])[0])
    return {'sql': sql, 'params': out}


def generate():
    setup()
    from application.models import SportAssociation, User
    from application.services.export_service import AssociationExportService
    service = AssociationExportService.__new__(AssociationExportService)
    association = SportAssociation(sport_association_id=ASSOCIATION, user_id=OWNER)
    association.user = User(user_id=OWNER)
    service.sport_association_id = ASSOCIATION
    service.sport_association = association
    service.document_ids = set()
    service.stats, service.errors = {}, []
    service.progress_callback = None
    service._last_progress_percent = -1
    service._credential_user_ids_cache = None
    models = [model_plan(service, item) for item in AssociationExportService.EXPORT_ORDER]
    from django.db.models import Exists, OuterRef, Q
    from application.models import Invoice, MedicalCertificate, Subscription, SubscriptionFile
    from docmanager.models import Document
    files = Document.objects.filter(document_id__in=[DOCUMENT_A, DOCUMENT_B]).annotate(
        export_is_medical_certificate=Exists(MedicalCertificate.objects.filter(document_id=OuterRef('pk'))),
        export_is_invoice=Exists(Invoice.objects.filter(document_pdf_id=OuterRef('pk'))),
        export_is_subscription_file=Exists(SubscriptionFile.objects.filter(document_id=OuterRef('pk'))))
    signatures = service.get_queryset_for_model(Subscription).exclude(
        signature_storage_key__isnull=True).exclude(signature_storage_key='')
    athletes = Subscription.objects.filter(sport_association=association, user__isnull=False).values_list('user_id', flat=True)
    credentials = User.original_objects.filter(deleted=False).filter(
        Q(pk=association.user_id) | Q(connected_user_id=association.user_id) | Q(pk__in=athletes)).values_list('pk', flat=True)
    from application.models import SportAssociationDocumentsArchive
    from application.tasks import EXPORT_MAX_COUNT
    exports = SportAssociationDocumentsArchive.objects.filter(
        sport_association=association, document__filename__startswith='export_').select_related('document').order_by('-date')
    extra = {'cleanup': query(exports), 'cleanup_excess': query(exports[EXPORT_MAX_COUNT - 1:]),
             'cleanup_columns': [f.column for f in SportAssociationDocumentsArchive._meta.concrete_fields]
             + [f.column for f in Document._meta.concrete_fields],
             'files': query(files), 'signatures': query(signatures), 'credentials': query(credentials),
             'signature_columns': [f.column for f in Subscription._meta.concrete_fields]}
    return {'format': 1, 'excluded': AssociationExportService.EXCLUDED_FIELDS, 'models': models, 'queries': extra}


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--check':
        target = Path(sys.argv[2])
        if not target.is_file() or target.stat().st_size == 0:
            raise SystemExit('missing plan to check')
        current = generate()
        if json.loads(target.read_text()) != current:
            raise SystemExit('export plan is stale: regenerate it')
        print('export plan current')
        return
    if len(sys.argv) != 2 or not sys.argv[1]:
        raise SystemExit('usage: association_export_plan.py <output.json> | --check <file>')
    plan = generate()
    if not plan['models']:
        raise SystemExit('empty plan')
    Path(sys.argv[1]).write_text(json.dumps(plan, indent=1, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
