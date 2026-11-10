"""Parent-only communications/workflows/<id>/update HTTP runner; imports are inert.

Seeds own and foreign automation workflows with fixed timestamps (audit
disabled). Each case is checked against the complete model state: only
AutomationWorkflow rows and auditlog entries with their index rows may change.
An updated row must hold the authored name, description, tree and enabled
values with the same key, association and created_at, and a fresh updated_at;
its new auditlog entries match the authored ones in order (one per save with
changed fields; auditlog never lists updated_at): the changed fields, the actor,
impersonation data, correlation id and a direct index row. Every case also checks
the Celery broker: a future cron trigger publishes check_workflows_trigger_cron
(args [UUID], eta at the localized time plus the clock gap), any other case nothing.
"""
import ipaddress
from datetime import datetime, timedelta, timezone

from TESTS.contracts import test_workflow_update_http as contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import audit_time, keyed, parse

SOURCES = ('TESTS/fixtures/workflow_update_http.json', 'TESTS/contracts/test_workflow_update_http.py',
           'TESTS/adapters/django/workflow_update_http.py', 'TESTS/adapters/django/workflow_writes_http.py',
           'BE/communications/views.py', 'BE/communications/serializers.py', 'BE/communications/models.py',
           'BE/application/permissions.py', 'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/permissions_registry.py', 'BE/application/impersonation_scope.py',
           'BE/application/utils/api_utils.py', 'BE/core/celery.py', 'BE/instance/restore/task_base.py') + harness.SOURCES
WORKFLOW = 'communications.AutomationWorkflow'
MANAGED = (WORKFLOW, harness.AUDIT, harness.INDEX)


def workflow_model():
    from communications.models import AutomationWorkflow
    return AutomationWorkflow


def seed_rows(source, created):
    model = workflow_model()
    for row in source['workflows'].values():
        model._base_manager.bulk_create([model(automation_workflow_id=row['id'], sport_association_id=row['association'], name=row['name'],
                                               description=row['description'], automation_tree=row['tree'], enabled=row['enabled'])])
        model._base_manager.filter(pk=row['id']).update(created_at=parse(row['created_at']), updated_at=parse(row['updated_at']))
        created.append((model, row['id']))


def recent(text):
    return abs(datetime.now(timezone.utc) - datetime.fromisoformat(text).replace(tzinfo=timezone.utc)) < timedelta(minutes=10)


def prepare(source):
    import time
    return {'started': time.time()}


def assert_effect(case, before, after, source, context):
    import time
    harness.require_unmanaged_unchanged(contract, before, after, MANAGED)
    effect = case['effect']
    # trigger() of a future cron time: check_workflows_trigger_cron with the countdown's eta; nothing otherwise.
    harness.celery_published(contract, context['fixture'], (effect or {}).get('celery', []),
                             since=context['prepared']['started'], until=time.time())
    if effect is None:
        contract.require(all(before[k] == after[k] for k in MANAGED), 'state.no-effect')
        return
    ids = source['ids']
    old, new = keyed(before[WORKFLOW], 'automation_workflow_id'), keyed(after[WORKFLOW], 'automation_workflow_id')
    key = source['workflows'][effect['label']]['id']
    contract.require(set(old) == set(new) and all(new[k] == old[k] for k in old if k != key), 'state.others')
    row, previous = new[key], old[key]
    for column, value in effect['row'].items():
        contract.require(row[column] == value, 'state.row-' + column)
    contract.require((row['sport_association_id'], row['created_at']) == (previous['sport_association_id'], previous['created_at']),
                     'state.row-kept')
    added, indexes = harness.audit_delta(contract, before, after)
    contract.require(len(added) == len(effect['audit']) and len(indexes) == len(added), 'audit.count')
    types = [ct['id'] for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'communications' and ct['model'] == 'automationworkflow']
    users = keyed(before['application.User'], 'user_id')
    actor = ids[effect['actor']]
    additional = None
    if effect['session']:
        additional = {'impersonation_session': context['fixture']['sessions'][effect['session']], 'actor_id': actor,
                      'target_user_id': case['headers']['User-Id'], 'association_id': None}
    contract.require(row['updated_at'] != previous['updated_at'] and recent(audit_time(row['updated_at'])), 'state.row-updated_at')
    for event, spec in zip(sorted(added, key=lambda event: event['id']), effect['audit']):
        contract.require(len(types) == 1 and event['content_type_id'] == types[0] and event['action'] == 1 and event['object_pk'] == key
                         and event['object_repr'] == f'AutomationWorkflow object ({key})', 'audit.identity')
        contract.require(event['changes'] == spec['changes'], 'audit.changes')
        contract.require(event['actor_id'] == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] == additional and event['changes_text'] == '',
                         'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        contract.require(index is not None and index['sport_association_id'] == ids['own_association']
                         and index['resolution_path'] == 'direct', 'index.fields')


fixture_context = harness.with_celery_broker(harness.fixture_context_for(contract, seed_rows))
run_workload = harness.run_workload_for(contract, assert_effect, prepare=prepare)
