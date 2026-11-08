"""Parent-only modules add/update/duplicate/delete HTTP runner; imports are inert.

Seeds modules of the own, a foreign and a soft-deleted association and
without one, their responses, documents and attachment links (audit
disabled), and verifies each case against the complete model state: one
created module (add, duplicate) with its create entry, the changed fields of
an updated module (no entry when nothing changed), or a deleted module with
its responses and their attachment links (documents stay), one delete entry
each. New modules are removed before the fixture cleanup compares the state.
"""
from datetime import datetime, timedelta, timezone
import ipaddress
import json
import re

from TESTS.contracts import test_modules_writes_http as contract
from TESTS.adapters.django import carnet_replace_http as replace
from TESTS.adapters.django import carnet_subscription_topup_http as topup
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django import modules_reads_http as reads
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/modules_writes_http.json', 'TESTS/fixtures/generate_modules_writes_http.py',
           'TESTS/contracts/test_modules_writes_http.py', 'TESTS/adapters/django/modules_writes_http.py',
           'TESTS/adapters/django/modules_reads_http.py', 'TESTS/adapters/django/carnet_replace_http.py',
           'TESTS/adapters/django/carnet_subscription_topup_http.py', 'BE/application/views/modules_views.py',
           'BE/application/serializers/modules_serializers.py', 'BE/application/models/modules_models.py',
           'BE/application/serializers/auth_serializers.py', 'BE/application/models/user_models.py',
           'BE/application/utils/api_utils.py', 'BE/application/audit_resolvers.py', 'BE/application/signals.py',
           'BE/application/permissions_registry.py', 'BE/application/urls.py', 'BE/core/settings.py') + harness.SOURCES
MODULE, RESPONSE, LINK = 'application.Module', 'application.ModuleResponses', 'application.ModuleResponses_attachments'
MANAGED = (MODULE, RESPONSE, LINK, harness.AUDIT, harness.INDEX)
STORED = {'association': 'sport_association_id', 'link': 'custom_link', 'title': 'title', 'require_approval': 'require_approval',
          'start': 'start_date', 'end': 'end_date', 'always_active': 'always_active', 'max_responses': 'max_responses',
          'queue_mode': 'queue_mode', 'only_users': 'only_users', 'payment_required': 'payment_required',
          'payment_data': 'payment_data', 'response_message': 'response_message', 'allow_attachments': 'allow_attachments',
          'elements': 'elements'}
RANDOM_LINK = re.compile(r'[0-9a-f]{12}')


def module_model():
    from application.models import Module
    return Module


def response_model():
    from application.models import ModuleResponses
    return ModuleResponses


def created_changes(model, row):
    """auditlog's create diff: every field against None; None kept only for
    foreign keys and DateTimeFields ('None') and JSON fields ('null')."""
    changes = {}
    for field in model._meta.concrete_fields:
        value = getattr(row, field.attname)
        kind = field.get_internal_type()
        if value is None:
            if field.is_relation or kind == 'DateTimeField':
                changes[field.name] = ['None', 'None']
            elif kind == 'JSONField':
                changes[field.name] = ['None', 'null']
            continue
        if kind == 'DateTimeField':
            value = topup.auditlog_moment(value)
        elif kind == 'JSONField':
            value = json.dumps(value, sort_keys=True)
        changes[field.name] = ['None', str(value)]
    return changes


def deleted_rows(source):
    """auditlog's delete diffs of every module and response, captured before each case."""
    rows = {}
    for model in (module_model(), response_model()):
        for row in model._base_manager.all():
            rows[str(row.pk)] = replace.deleted_changes(model, row)
    return rows


def stored_values(row):
    values = {key: getattr(row, attname) for key, attname in STORED.items()}
    values['association'] = None if values['association'] is None else str(values['association'])
    for key in ('start', 'end'):
        values[key] = None if values[key] is None else values[key].isoformat()
    return values


def check_created_at(row, expected):
    if expected == '$now':
        now = datetime.now(timezone.utc)
        contract.require(now - timedelta(minutes=15) <= row.creation_date <= now + timedelta(minutes=1), 'state.creation-date')
    else:
        contract.require(row.creation_date == reads.moment(expected), 'state.creation-date')


def check_event(case, event, indexes, *, before, ids, model, key, action, changes, actor, association, path, context):
    """One auditlog entry and its index row (association None: no index row).
    An impersonation session adds auditlog's additional_data."""
    types = {ct['model']: ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application'}
    users = keyed(before['application.User'], 'user_id')
    session = case['headers'].get('X-Impersonation-Id', '')
    additional = None
    if session.startswith('$session:'):
        label = session[9:]
        additional = {'impersonation_session': context['fixture']['sessions'][label], 'actor_id': ids[actor],
                      'target_user_id': case['headers']['User-Id'],
                      'association_id': None if label.startswith('admin_') else ids['own_association']}
    contract.require(event['content_type_id'] == types[model.lower()] and event['action'] == action and event['object_pk'] == key
                     and event['object_repr'] == f'{model} object ({key})', 'audit.identity')
    contract.require(event['changes'] == changes, 'audit.changes')
    contract.require(event['actor_id'] == ids[actor] and event['actor_email'] == users[ids[actor]]['email'], 'audit.actor')
    contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                     'audit.context')
    ipaddress.ip_address(event['remote_addr'])
    contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
    index = indexes.get(str(event['id']))
    if association is None:
        contract.require(index is None, 'index.absent')
    else:
        contract.require(index is not None and index['sport_association_id'] == association and index['resolution_path'] == path,
                         'index.fields')


def assert_effect(case, before, after, source, context):
    from application.models import SportAssociation
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    old_m, new_m = keyed(before[MODULE], 'module_id'), keyed(after[MODULE], 'module_id')
    added, indexes = harness.audit_delta(contract, before, after)
    events = {}
    for event in added:
        events.setdefault(event['object_pk'], []).append(event)
    contract.require(all(len(items) == 1 for items in events.values()), 'audit.one-per-row')
    events = {k: v[0] for k, v in events.items()}
    common = dict(before=before, ids=ids, actor=effect['actor'], context=context)
    if effect['kind'] in ('create', 'update'):
        contract.require(before[RESPONSE] == after[RESPONSE] and before[LINK] == after[LINK], 'state.responses')
    if effect['kind'] == 'create':
        keys = set(new_m) - set(old_m)
        contract.require(len(keys) == 1 and all(new_m[k] == old_m[k] for k in old_m), 'state.created')
        key = keys.pop()
        contract.require(effect['fixed_id'] is None or key == effect['fixed_id'], 'state.created-id')
        row = module_model()._base_manager.get(pk=key)
        expected = dict(effect['stored'])
        if effect.get('random_link'):
            contract.require(isinstance(row.custom_link, str) and RANDOM_LINK.fullmatch(row.custom_link) is not None, 'state.random-link')
            expected['link'] = row.custom_link
        contract.require(stored_values(row) == expected, 'state.module')
        check_created_at(row, effect['created'])
        contract.require(set(events) == {key}, 'audit.count')
        check_event(case, events[key], indexes, model='Module', key=key, action=0, changes=created_changes(module_model(), row),
                    association=expected['association'], path='direct', **common)
    elif effect['kind'] == 'update':
        key = source['modules'][effect['label']]['id']
        contract.require(set(new_m) == set(old_m) and all(new_m[k] == old_m[k] for k in old_m if k != key), 'state.others')
        row = module_model()._base_manager.get(pk=key)
        contract.require(stored_values(row) == effect['stored'], 'state.module')
        check_created_at(row, effect['created'])
        if effect['changes']:
            contract.require(set(events) == {key}, 'audit.count')
            check_event(case, events[key], indexes, model='Module', key=key, action=1, changes=effect['changes'],
                        association=effect['stored']['association'], path='direct', **common)
        else:
            contract.require(not events and not indexes, 'audit.none')
    else:
        key = source['modules'][effect['label']]['id']
        gone = {source['responses'][label]['id'] for label in effect['responses']}
        old_r, new_r = keyed(before[RESPONSE], 'module_response_id'), keyed(after[RESPONSE], 'module_response_id')
        contract.require(set(new_m) == set(old_m) - {key} and all(new_m[k] == old_m[k] for k in new_m), 'state.module-deleted')
        contract.require(set(new_r) == set(old_r) - gone and all(new_r[k] == old_r[k] for k in new_r), 'state.responses-deleted')
        kept = {pair for pair in harness.pairs(before[LINK], 'moduleresponses_id', 'document_id') if pair[0] not in gone}
        contract.require(harness.pairs(after[LINK], 'moduleresponses_id', 'document_id') == kept and len(after[LINK]) == len(kept),
                         'state.links')
        contract.require(set(events) == gone | {key}, 'audit.count')
        prepared = context['prepared']
        # The response row is gone and its changes carry no sport_association:
        # the index falls back to the actor's own association, if any.
        fallback = SportAssociation.objects.filter(user_id=ids[effect['actor']]).first()
        for response in gone:
            check_event(case, events[response], indexes, model='ModuleResponses', key=response, action=2, changes=prepared[response],
                        association=None if fallback is None else str(fallback.pk), path='actor.sport_association', **common)
        check_event(case, events[key], indexes, model='Module', key=key, action=2, changes=prepared[key],
                    association=source['modules'][effect['label']]['association'], path='changes_json.sport_association', **common)


fixture_context = harness.fixture_context_for(contract, reads.seed_rows, purge=(module_model,))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=deleted_rows)
