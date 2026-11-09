"""Parent-only collaborators/<str:uid>/delete HTTP runner; imports are inert.

Seeds the authored rows (audit disabled; required fields the generator leaves
out get deterministic values) and, on every fixture, recomputes Django's
deletion graph and requires it to equal the table the Go collector embeds
(BE_GO/internal/repository/collaborator_delete_graph.json).

Each case is checked against the complete model state: exactly the authored
rows disappear, exactly the authored kept rows get NULL foreign keys, nothing
else changes, and the new auditlog entries are the authored multiset. Every
entry's changes and object_repr equal auditlog's own model_instance_diff /
smart_str of the instance read before the request (CourseSubscription's
__dict__ repr with its memory address normalized), with the request's actor,
CID, impersonation data and the authored index row. The entry order of a
Go backend must equal the order Django produced for the same case.
"""
import ipaddress
import json
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from TESTS.contracts import test_collaborator_delete_go_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import collaborator_delete_go_graph as graph

SOURCES = ('TESTS/fixtures/collaborator_delete_go_http.json', 'TESTS/contracts/test_collaborator_delete_go_http.py',
           'TESTS/adapters/django/collaborator_delete_go_http.py', 'TESTS/adapters/django/collaborator_delete_go_graph.py',
           'BE_GO/internal/repository/collaborator_delete_graph.json', 'BE/application/views/collaborator_views.py',
           'BE/application/models/user_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/courses_models.py', 'BE/application/models/subscriptions_models.py',
           'BE/application/signals.py', 'BE/application/audit_context.py', 'BE/application/audit_resolvers.py',
           'BE/application/mixin.py', 'BE/application/permissions_registry.py', 'BE/application/utils/api_utils.py',
           'BE/application/impersonation.py') + harness.SOURCES
AUDIT, INDEX = harness.AUDIT, harness.INDEX
ACTOR_ASSOCIATION = {'owner': 'own_association', 'admin': 'admin_association', 'full_collaborator': None}
# The last Django entry order per case; a Go run must reproduce it.
DJANGO_ORDER = {}
SEEDED = {}
ADDRESS = re.compile(r' object at 0x[0-9a-f]+>')


def _resolve(value, ids, keys):
    if isinstance(value, str) and value.startswith('@'):
        return keys[value[1:]]
    if isinstance(value, str) and value.startswith('$permission:'):
        from django.contrib.auth.models import Permission
        return Permission.objects.get(codename=value.split(':', 1)[1]).pk
    if isinstance(value, str) and value.startswith('$contenttype:'):
        from django.contrib.contenttypes.models import ContentType
        app_label, model = value.split(':', 1)[1].split('.')
        return ContentType.objects.get(app_label=app_label, model=model).pk
    if isinstance(value, str) and value.startswith('$'):
        return ids[value[1:]]
    return value


def _value(field, value):
    kind = field.get_internal_type()
    if value is None or not isinstance(value, str):
        return value
    if kind == 'DateTimeField':
        return datetime.fromisoformat(value)
    if kind == 'DateField':
        return date.fromisoformat(value)
    if kind == 'DecimalField':
        return Decimal(value)
    return value


def _filler(field, label, index):
    """A deterministic value for a required field the generator omits."""
    kind = field.get_internal_type()
    if kind in ('CharField', 'TextField', 'EmailField', 'SlugField'):
        return f'cdgo-{label}-{field.name}'[:field.max_length or 200]
    if kind in ('IntegerField', 'PositiveIntegerField', 'PositiveSmallIntegerField', 'SmallIntegerField', 'BigIntegerField'):
        return index
    if kind == 'DecimalField':
        return Decimal('1')
    if kind == 'BooleanField':
        return False
    if kind == 'DateTimeField':
        return datetime.fromisoformat('2026-02-01T08:00:00+00:00')
    if kind == 'DateField':
        return date(2026, 2, 1)
    if kind == 'UUIDField':
        return uuid.uuid5(uuid.NAMESPACE_URL, f'cdgo:{label}:{field.name}')
    if kind == 'JSONField':
        return {}
    raise AssertionError(f'fixture.required-field:{label}.{field.name}:{kind}')


def seed_rows(source, created):
    from django.apps import apps
    from django.db.models import NOT_PROVIDED
    committed = graph.OUTPUT.read_text()
    contract.require(graph.dumps(graph.describe()) == committed, 'graph.drift')
    ids, keys, models = source['ids'], {}, {}
    for index, spec in enumerate(source['rows'], start=1):
        model = apps.get_model(spec['model'])
        values = {name: _resolve(value, ids, keys) for name, value in spec['values'].items()}
        fields = {field.attname: field for field in model._meta.concrete_fields}
        contract.require(set(values) <= set(fields), 'fixture.unknown-field:' + spec['label'])
        values = {name: _value(fields[name], value) for name, value in values.items()}
        for field in model._meta.concrete_fields:
            if field.attname in values or field.primary_key or field.null or field.default is not NOT_PROVIDED \
                    or getattr(field, 'auto_now', False) or getattr(field, 'auto_now_add', False):
                continue
            values[field.attname] = _filler(field, spec['label'], index)
        if spec['id'] is not None:
            values[model._meta.pk.attname] = spec['id']
        instance = model(**values)
        model._base_manager.bulk_create([instance])
        contract.require(instance.pk is not None, 'fixture.key:' + spec['label'])
        keys[spec['label']], models[spec['label']] = instance.pk, model
        created.append((model, instance.pk))
    SEEDED.clear()
    SEEDED.update(keys=keys, models=models)


fixture_context = harness.fixture_context_for(contract, seed_rows)


def _label(model):
    return model._meta.label


def _pk_name(model):
    return model._meta.pk.attname


def _key(value):
    return str(shared._stable(value)['value']) if isinstance(shared._stable(value), dict) else str(value)


def oracle(case):
    """auditlog's own delete changes and repr of each authored entry, from the
    instance read now (before the request)."""
    from auditlog.diff import model_instance_diff
    from django.utils.encoding import smart_str
    effect = case['effect']
    if not effect:
        return {}
    deleted = {(_label(SEEDED['models'][label]), str(SEEDED['keys'][label])) for label in effect['deleted']}
    result = {}
    for label, _ in effect['audit']:
        model = SEEDED['models'][label]
        instance = model._base_manager.get(pk=SEEDED['keys'][label])
        changes = {name: list(pair) for name, pair in (model_instance_diff(instance, None) or {}).items()}
        # A reverse one-to-one is read when the entry is written: a related row
        # deleted earlier by the same collector is gone by then.
        for relation in model._meta.related_objects:
            if relation.one_to_one and relation.name in changes:
                related = getattr(instance, relation.get_accessor_name())
                if (_label(related.__class__), str(related.pk)) in deleted:
                    del changes[relation.name]
        result[label] = {'changes': changes, 'repr': ADDRESS.sub(' object at 0x>', smart_str(instance))}
    return result


def assert_effect(case, before, after, source, context, expected):
    from django.apps import apps
    effect = case['effect']
    if effect is None:
        contract.require(before == after, 'state.no-effect')
        return []
    keys, models = SEEDED['keys'], SEEDED['models']
    deleted, nulled = {}, {}
    for label in effect['deleted']:
        deleted.setdefault(_label(models[label]), set()).add(str(keys[label]))
    for label, field in effect['nulled']:
        nulled.setdefault(_label(models[label]), {}).setdefault(str(keys[label]), set()).add(field)
    contract.require(set(deleted) | set(nulled) <= set(before), 'state.models')
    for name, rows in before.items():
        if name == AUDIT:
            continue
        pk = _pk_name(apps.get_model(name)) if name != INDEX else 'audit_log_index_id'
        gone, cleared = deleted.get(name, set()), nulled.get(name, {})
        present = {_key(row[pk]) for row in rows}
        contract.require(gone <= present and set(cleared) <= present, 'state.authored-rows:' + name)
        expected_rows = []
        for row in rows:
            key = _key(row[pk])
            if key in gone:
                continue
            expected_rows.append({k: (None if k in cleared.get(key, ()) else v) for k, v in row.items()})
        if name == INDEX:
            old = {_key(row[pk]) for row in rows}
            contract.require([row for row in after[name] if _key(row[pk]) in old] == expected_rows, 'state.index-kept')
        else:
            contract.require(after[name] == expected_rows, 'state.model:' + name)
    # Old entries (actor nulled where authored), then the new ones.
    old_entries = {row['id'] for row in before[AUDIT]}
    cleared = nulled.get(AUDIT, {})
    contract.require([row for row in after[AUDIT] if row['id'] in old_entries]
                     == [{k: (None if k in cleared.get(str(row['id']), ()) else v) for k, v in row.items()} for row in before[AUDIT]],
                     'audit.kept')
    entries = sorted((row for row in after[AUDIT] if row['id'] not in old_entries), key=lambda row: row['id'])
    old_indexes = {_key(row['audit_log_index_id']) for row in before[INDEX]}
    added_indexes = [row for row in after[INDEX] if _key(row['audit_log_index_id']) not in old_indexes]
    indexes = {row['log_entry_id']: row for row in added_indexes}
    contract.require(len(indexes) == len(added_indexes) and set(indexes) <= {row['id'] for row in entries}, 'index.new-rows')
    types = {ct['id']: f"{ct['app_label']}.{ct['model']}" for ct in before['contenttypes.ContentType']}
    by_identity = {}
    for label in {label for label, _ in effect['audit']}:
        model = models[label]
        by_identity[(f'{model._meta.app_label}.{model._meta.model_name}', str(keys[label]))] = label
    seen = [by_identity.get((types[entry['content_type_id']], entry['object_pk'])) for entry in entries]
    contract.require(None not in seen and sorted(seen) == sorted(label for label, _ in effect['audit']), 'audit.entries')
    ids = source['ids']
    users = {_key(row['user_id']): row for row in before['application.User']}
    actor = ids[case['actor']]
    additional = None
    if case['headers'].get('X-Impersonation-Id'):
        additional = {'impersonation_session': context['fixture']['sessions'][case['headers']['X-Impersonation-Id'].split(':', 1)[1]],
                      'actor_id': actor, 'target_user_id': case['headers']['User-Id'], 'association_id': None}
    index_of = {}
    for label, index in effect['audit']:
        index_of.setdefault(label, set()).add(index)
    for entry, label in zip(entries, seen):
        model, key = models[label], keys[label]
        contract.require(entry['action'] == 2 and entry['changes_text'] == '' and entry['serialized_data'] is None
                         and entry['object_id'] == (key if isinstance(key, int) else None), 'audit.identity:' + label)
        contract.require(ADDRESS.sub(' object at 0x>', entry['object_repr']) == expected[label]['repr'], 'audit.repr:' + label)
        contract.require(entry['changes'] == expected[label]['changes'], 'audit.changes:' + label)
        contract.require(_key(entry['actor_id']) == actor and entry['actor_email'] == users[actor]['email'], 'audit.actor:' + label)
        contract.require(entry['cid'] == case['name'] and entry['additional_data'] == additional, 'audit.context:' + label)
        ipaddress.ip_address(entry['remote_addr'])
        contract.require(context['remote'].setdefault('addr', entry['remote_addr']) == entry['remote_addr'], 'audit.remote-consistent')
        (spec,) = index_of[label]
        row = indexes.get(entry['id'])
        if spec is None or (spec == 'actor' and ACTOR_ASSOCIATION[case['actor']] is None):
            contract.require(row is None, 'index.none:' + label)
            continue
        if spec == 'sa':
            stored = next(r for r in before[_label(model)] if _key(r[_pk_name(model)]) == str(key))
            association, path = _key(stored['sport_association_id']), 'changes_json.sport_association'
        else:
            association, path = ids[ACTOR_ASSOCIATION[case['actor']]], 'actor.sport_association'
        contract.require(row is not None and _key(row['sport_association_id']) == association and row['resolution_path'] == path,
                         'index.fields:' + label)
    return seen


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    contract.require(fixture['baseline_state'] == scopes.snapshot_state(apps), 'fixture.baseline')
    results, context = [], {'remote': {}, 'fixture': fixture}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        expected = oracle(case)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            order = assert_effect(case, before, after, source, context, expected)
            if backend == 'django':
                DJANGO_ORDER[case['name']] = order
            else:
                contract.require(case['name'] in DJANGO_ORDER and DJANGO_ORDER[case['name']] == order, 'audit.order-differs-from-django')
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
