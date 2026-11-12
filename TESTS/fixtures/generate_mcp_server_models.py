"""Generate the model metadata the Go MCP query engine and get_schema use.

Run with the production interpreter: python -I TESTS/fixtures/generate_mcp_server_models.py
It writes BE_GO/internal/handler/businessmcp/tools.json (TOOL_DEFINITIONS as the
MCP SDK lists them) and BE_GO/internal/dto/mcp_models.json from Django's model _meta for the
models MODEL_WHITELIST reaches through relations, the default managers' base
filters of the whitelisted models (and whether they follow X-Group-Id), REPORT_ALLOWLIST's fields and relations of
the whitelisted models and the Italian validation message templates the tool functions can raise. The business MCP adapter regenerates it and
requires the committed file to be identical.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'BE_GO/internal/dto/mcp_models.json'
TOOLS = ROOT / 'BE_GO/internal/handler/businessmcp/tools.json'


def setup_django():
    sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
    import environ
    keep = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR')}
    os.environ.clear()
    os.environ.update(keep)
    environ.Env.read_env(str(ROOT / 'selfhost/.env.example'))
    os.environ.update(DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings', TESTS_DATABASE='assozeta_contract_mcp_models',
                      DBTYPE='POSTGRES', DBHOST='/nonexistent', DBPORT='5432', DBNAME='assozeta_contract_mcp_models',
                      DBUSER='contracts', DBPASSWORD='unused', USE_PGBOUNCER='False', STORE_ON_DISK='True',
                      JWT_SECRET_KEY_B64='', JWT_PUBLIC_KEY_B64='', JWT_SECRET_KEY='unused', JWT_PUBLIC_KEY='unused')
    import django
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch('environ.Env.read_env'):
        django.setup()


def text(value):
    return None if value is None else str(value)


def choices(field):
    raw = getattr(field, 'choices', None)
    if not raw:
        return None
    items = []
    for value, label in raw:
        if isinstance(label, (list, tuple)):
            return 'grouped'
        items.append([value, str(label)])
    return items


def label_of(model):
    return f'{model._meta.app_label}.{model.__name__}'


def describe_field(field):
    # concrete: a column of the model's table (Meta.concrete_fields; a
    # many-to-many field's attname equals its name).
    item = {'name': field.name, 'class': type(field).__name__, 'relation': bool(field.is_relation),
            'concrete': bool(getattr(field, 'concrete', False)) and not field.many_to_many}
    if field.is_relation and field.related_model:
        item['related'] = label_of(field.related_model)
        # The attributes schema._get_relation_info reads.
        item['schema_related_name'] = text(getattr(field, 'related_name', None))
        item['schema_verbose_name'] = text(getattr(field, 'verbose_name', None))
    if field.many_to_many and not field.auto_created:
        through = field.remote_field.through._meta
        item.update(kind='many_to_many', through=through.db_table, source_column=field.m2m_column_name(),
                    target_column=field.m2m_reverse_name(), local_column=field.model._meta.pk.column,
                    remote_column=field.related_model._meta.pk.column, related_name=text(field.remote_field.related_name),
                    verbose_name=text(field.verbose_name), null=field.null)
    elif getattr(field, 'concrete', False):
        item.update(attname=field.attname, column=field.column, internal=field.get_internal_type(), null=field.null,
                    blank=field.blank, has_default=field.has_default(), max_length=field.max_length,
                    verbose_name=text(field.verbose_name), help_text=text(field.help_text) or '')
        if field.get_internal_type() == 'DecimalField':
            item.update(max_digits=field.max_digits, decimal_places=field.decimal_places)
        if field.choices:
            item['choices'] = choices(field)
        if field.is_relation:
            target = field.target_field
            item.update(kind='one_to_one' if field.one_to_one else 'foreign_key', target_column=target.column,
                        target_internal=target.get_internal_type(), target_attname=target.attname,
                        related_name=text(field.remote_field.related_name), db_constraint=field.db_constraint)
    elif field.auto_created and field.is_relation:
        remote = field.field  # the forward field on the related model
        if field.many_to_many:
            through = remote.remote_field.through._meta
            item.update(kind='many_to_many_reverse', through=through.db_table, source_column=remote.m2m_reverse_name(),
                        target_column=remote.m2m_column_name(), local_column=field.model._meta.pk.column,
                        remote_column=field.related_model._meta.pk.column)
        else:
            item.update(kind='one_to_one_reverse' if field.one_to_one else 'foreign_key_reverse',
                        remote_column=remote.column, local_column=remote.target_field.column)
        item['related_name'] = text(field.related_name)
    else:
        item['kind'] = 'unsupported'
    return item


def manager_filters(model):
    """The default manager's WHERE as NOT <column> conjuncts (refuse anything else)."""
    sql = str(model.objects.all().query)
    if ' WHERE ' not in sql:
        return []
    where = sql.split(' WHERE ', 1)[1]
    table = model._meta.db_table
    parts = where.strip('()').split(' AND ')
    columns = []
    for part in parts:
        match = re.fullmatch(rf'NOT "{table}"\."(\w+)"', part)
        assert match, f'unsupported manager filter on {model.__name__}: {where}'
        columns.append(match[1])
    return columns


def group_manager(model):
    """Whether the default manager filters by the request's X-Group-Id (as its first conjunct)."""
    from application import mixin
    group = '11111111-1111-4111-8111-111111111111'
    setattr(mixin._local, 'group_id', group)
    try:
        sql = str(model.objects.all().query)
    finally:
        delattr(mixin._local, 'group_id')
    condition = f'"{model._meta.db_table}"."group_id" = {group}'
    if condition not in sql:
        return False
    where = sql.split(' WHERE ', 1)[1].lstrip('(')
    assert where.startswith(condition), f'unsupported group filter on {model.__name__}: {where}'
    return True


def build():
    from django.apps import apps
    from django.db import models
    from application.mcp_server.security import MODEL_WHITELIST, REPORT_ALLOWLIST
    whitelisted = [apps.get_model(label) for label in MODEL_WHITELIST]
    seen, order, todo = set(), [], list(whitelisted)
    while todo:
        model = todo.pop(0)
        if model._meta.label in seen:
            continue
        seen.add(model._meta.label)
        order.append(model)
        for field in model._meta.get_fields():
            if field.is_relation and field.related_model:
                todo.append(field.related_model)
    result = {'models': {}, 'messages': {}}
    for model in sorted(order, key=label_of):
        meta = model._meta
        entry = {'name': model.__name__, 'table': meta.db_table, 'pk': meta.pk.name, 'ordering': [str(o) for o in meta.ordering],
                 'verbose_name': str(meta.verbose_name), 'verbose_name_plural': str(meta.verbose_name_plural),
                 'fields': [describe_field(field) for field in meta.get_fields()]}
        if model in whitelisted:
            entry['manager_filters'] = manager_filters(model)
            entry['group_manager'] = group_manager(model)
            allowed = REPORT_ALLOWLIST[label_of(model)]
            entry['report_fields'] = list(allowed['fields'])
            entry['report_relations'] = list(allowed['relations'])
        result['models'][label_of(model)] = entry
    for field_class in (models.UUIDField, models.DateField, models.DateTimeField, models.BooleanField,
                        models.DecimalField, models.TimeField):
        for key, message in field_class.default_error_messages.items():
            result['messages'][f'{field_class.__name__}.{key}'] = str(message)
    return result


def tools():
    """tools/list's tools as the SDK serialises them (compact, insertion order)."""
    from application.mcp_server.server import TOOL_DEFINITIONS
    declared = [{'name': t['name'], 'description': t['description'], 'inputSchema': t['parameters']} for t in TOOL_DEFINITIONS]
    return json.dumps(declared, separators=(',', ':'), ensure_ascii=False)


def main():
    setup_django()
    OUTPUT.write_text(json.dumps(build(), indent=1, ensure_ascii=False, sort_keys=False) + '\n')
    TOOLS.parent.mkdir(parents=True, exist_ok=True)
    TOOLS.write_text(tools())
    print(OUTPUT.relative_to(ROOT), TOOLS.relative_to(ROOT))


if __name__ == '__main__':
    main()
