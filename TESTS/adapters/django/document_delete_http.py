"""Parent-only document/<uid>/delete HTTP runner; imports are inert.

seed_rows inserts (audit disabled) the authored rows of every model that can
hold a Document (archive rows, payments, invoices, supplier and customer
invoices, subscriptions, medical certificates and appointments, subscription
files, memberships, course locations, module responses, instructors and their
hours), the many-to-many links, and the audit entries with their index rows
that record which association a document was created for. It first requires
the committed scope table Go walks to equal the one computed from the running
models.

A case with an effect must remove exactly the authored rows, clear exactly the
authored columns and write the authored audit entries in order, each with its
index row (or none); every other row of every model must be unchanged. A case
without an effect must leave the complete state unchanged.
"""
import copy
import ipaddress

from TESTS.contracts import test_document_delete_http as contract
from TESTS.adapters.django import document_delete_scope_graph as scope
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.workflow_writes_http import scalar

SOURCES = ('TESTS/fixtures/document_delete_http.json', 'TESTS/contracts/test_document_delete_http.py',
           'TESTS/adapters/django/document_delete_http.py', 'TESTS/adapters/django/document_delete_scope_graph.py',
           'BE_GO/internal/repository/document_delete_scope.json', 'BE_GO/internal/repository/restore_deletion_graph.json',
           'BE/application/document_scope.py', 'BE/application/tenant_scope.py', 'BE/application/audit_resolvers.py',
           'BE/application/signals.py', 'BE/docmanager/models.py', 'BE/docmanager/urls.py',
           'BE/application/models/subscriptions_models.py', 'BE/application/models/invoices_models.py',
           'BE/application/models/user_models.py', 'BE/application/models/payment_models.py',
           'BE/application/models/courses_models.py', 'BE/application/models/modules_models.py',
           'BE/application/models/audit_models.py', 'BE/application/permissions_registry.py') + harness.SOURCES
# The actor's own association (SportAssociation.objects.filter(user=actor).first()).


def seed_rows(source, created):
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from application.models.audit_models import AuditLogIndex
    from docmanager.models import Document
    contract.require(scope.dumps(scope.describe()) == scope.OUTPUT.read_text(), 'scope.drift')
    contract.require(len(source['rows']) > 0 and len(source['links']) > 0 and len(source['entries']) > 0, 'fixture.empty')
    for item in source['rows']:
        model = apps.get_model(item['model'])
        values = {model._meta.get_field(name).attname: model._meta.get_field(name).to_python(value) for name, value in item['fields'].items()}
        row = model(**values)
        contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
        model._base_manager.bulk_create([row])
        created.append((model, row.pk))
    for item in source['links']:
        model = apps.get_model(item['model'])
        through = model._meta.get_field(item['field']).remote_field.through
        field = model._meta.get_field(item['field'])
        link = through(**{field.m2m_column_name(): item['key'], field.m2m_reverse_name(): item['document']})
        through._base_manager.bulk_create([link])
        created.append((through, link.pk))
    document_type = ContentType.objects.get_for_model(Document)
    for item in source['entries']:
        entry = LogEntry.objects.bulk_create([LogEntry(content_type=document_type, object_pk=item['document'],
                                                       object_repr=f'Document object ({item["document"]})', action=item['action'],
                                                       changes={})])[0]
        created.append((LogEntry, entry.pk))
        if item['association'] is None:
            continue
        index = AuditLogIndex(log_entry_id=entry.pk, sport_association_id=item['association'], resolution_path='actor.sport_association')
        AuditLogIndex._base_manager.bulk_create([index])
        created.append((AuditLogIndex, index.pk))


def matches(row, match):
    return all(str(scalar(row[column])) == str(value) for column, value in match.items())


def effect(case, before, after, source, context):
    expected_effect = case['effect']
    if expected_effect is None:
        contract.require(before == after, 'state.no-effect')
        return
    expected = copy.deepcopy({label: rows for label, rows in before.items() if label not in (harness.AUDIT, harness.INDEX)})
    for item in expected_effect['deleted']:
        found = [row for row in expected[item['model']] if matches(row, item['match'])]
        contract.require(len(found) == 1, 'effect.deleted-row-seeded')
        expected[item['model']].remove(found[0])
    for item in expected_effect['cleared']:
        found = [row for row in expected[item['model']] if matches(row, {source['keys'][item['model']]: item['key']})]
        contract.require(len(found) == 1 and found[0][item['field']] is not None, 'effect.cleared-row-seeded')
        found[0][item['field']] = None
    for label, rows in expected.items():
        contract.require(after[label] == rows, 'state.' + label)
    added, indexes = harness.audit_delta(contract, before, after)
    added.sort(key=lambda event: event['id'])
    contract.require(len(added) == len(expected_effect['audit']) > 0, 'audit.entries')
    types = {(ct['app_label'], ct['model']): ct['id'] for ct in before['contenttypes.ContentType']}
    users = {str(scalar(row['user_id'])): row for row in before['application.User']}
    actor = source['ids'][case['actor']]
    indexed = 0
    for event, authored in zip(added, expected_effect['audit']):
        event = {key: scalar(value) for key, value in event.items()}
        app, name = authored['model'].split('.')
        contract.require(event['content_type_id'] == types[(app, name.lower())] and event['action'] == 2
                         and event['object_pk'] == authored['key'] and event['object_repr'] == f'{name} object ({authored["key"]})',
                         'audit.identity')
        contract.require(event['changes'] == authored['changes'], 'audit.changes')
        contract.require(str(event['actor_id']) == actor and event['actor_email'] == users[actor]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(context['remote'].setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = indexes.get(str(event['id']))
        if authored['association'] is None:
            contract.require(index is None, 'index.absent')
            continue
        indexed += 1
        contract.require(index is not None and str(scalar(index['sport_association_id'])) == authored['association']
                         and index['resolution_path'] == authored['path'], 'index.fields')
    contract.require(len(indexes) == indexed, 'index.rows')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, effect)
