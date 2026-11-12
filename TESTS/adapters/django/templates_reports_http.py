"""Parent-only templates-reports HTTP runner; imports are inert.

Seeds the shared identity fixture plus module templates, saved reports and the
query data the report tools read (tags, payment categories, carnets; audit
disabled), runs the authored sequence and verifies each case against the
complete model state: only the authored template rows with their exact audit
entries and index rows, or the authored saved report, may change. Report runs
must leave every row unchanged.
"""
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
import ipaddress
from pathlib import Path

from TESTS.contracts import test_templates_reports_http as contract
from TESTS.adapters.django import message_read_scopes as scopes
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_mutations as mutations
from TESTS.adapters.django.workflow_writes_http import keyed, scalar

SOURCES = ('TESTS/fixtures/templates_reports_http.json', 'TESTS/contracts/test_templates_reports_http.py',
           'TESTS/adapters/django/templates_reports_http.py', 'BE/application/views/archive_views.py',
           'BE/application/views/report_views.py', 'BE/application/serializers/user_serializers.py',
           'BE/application/models/user_models.py', 'BE/application/models/report_models.py',
           'BE/application/mcp_server/server.py', 'BE/application/mcp_server/query_builder.py',
           'BE/application/mcp_server/security.py', 'BE/application/mcp_server/schema.py',
           'BE/application/mcp_server/attendance.py', 'BE/application/mcp_server/exporters/__init__.py',
           'BE/application/mcp_server/exporters/csv_exporter.py', 'BE/application/mcp_server/exporters/xlsx_exporter.py',
           'BE/application/mcp_server/exporters/pdf_exporter.py', 'BE/application/mixin.py', 'TESTS/contracts/pdf.py',
           'TESTS/contracts/xlsx.py',
           'BE/instance/integration_configuration.py', 'BE/application/tenant_scope.py',
           'BE/application/audit_resolvers.py', 'BE/application/permissions_registry.py', 'BE/core/middleware.py')
TEMPLATE, REPORT = 'application.SportAssociationModuleTemplates', 'application.SavedReport'
AUDIT, INDEX = 'auditlog.LogEntry', 'application.AuditLogIndex'
FIELDS = ('name', 'template', 'footer', 'header', 'custom_footer_header', 'custom_footer', 'custom_header')


def moment(value):
    return datetime.fromisoformat(str(value).replace('Z', '+00:00'))


def seed_fixture(apps, private_work, raw):
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from application.models import (Associate, AttendanceDay, AttendanceRegistry, Carnet, Course, CourseSubscription, Group,
                                    PaymentCategory, SportAssociationModuleTemplates, Subscription, Tags)
    from application.models.report_models import SavedReport
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    seed, ids = source['seed'], source['ids']
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, Path(private_work), raw)
        created = fixture['created']

        def create(model, row):
            contract.require(not model._base_manager.filter(pk=row.pk).exists(), 'fixture.collision')
            model._base_manager.bulk_create([row])
            created.append((model, row.pk))

        for v in seed['templates'].values():
            create(SportAssociationModuleTemplates, SportAssociationModuleTemplates(
                sport_association_module_templates_id=v['id'], sport_association_id=v['association'], **{f: v[f] for f in FIELDS}))
        for v in seed['tags'].values():
            create(Tags, Tags(tag_id=v['id'], tag_name=v['name'], sport_association_id=v['association'], creation_date=moment(v['created'])))
        for v in seed['categories'].values():
            create(PaymentCategory, PaymentCategory(payment_category_id=v['id'], name=v['name'], sport_association_id=v['association'],
                   type=v['type'], expense=v['expense'], tax_deductible=v['tax_deductible'], archived=v['archived'], deleted=v['deleted'],
                   creation_date=moment(v['created'])))
        for v in seed['carnets'].values():
            create(Carnet, Carnet(carnet_id=v['id'], title=v['title'], description=v['description'], sport_association_id=v['association'],
                   lessons_number=v['lessons'], fee=Decimal(v['fee']), public=v['public'], meta=v['meta']))
            Carnet._base_manager.filter(pk=v['id']).update(creation_date=moment(v['created']))
        for n, v in enumerate(seed['people'].values()):
            create(Associate, Associate(associate_id=v['id'], sport_association_id=ids['own_association'], first_name=v['first'],
                                        last_name=v['last'], sex='M', tax_code=f'RPTRPT80A{n + 10}H501U', is_minor=False))
            create(Subscription, Subscription(subscription_id=v['subscription'], sport_association_id=ids['own_association'],
                                              user_id=ids['owner'], associate_id=v['id'], status_flag=Subscription.ACCEPTED))
        for v in seed['groups'].values():
            create(Group, Group(group_id=v['id'], sport_association_id=v['association'], name=v['name']))
        for v in seed['courses'].values():
            create(Course, Course(course_id=v['id'], sport_association_id=v['association'], title=v['title'], deleted=v['deleted'],
                                  group_id=v['group'], fee=Decimal('50.00'), description=v['description'],
                                  google_calendar_id=v['calendar']))
        for v in seed['enrolled'].values():
            create(CourseSubscription, CourseSubscription(
                course_subscription_id=v['id'], course_id=seed['courses'][v['course']]['id'], deleted=v['deleted'],
                subscription_id=seed['people'][v['person']]['subscription'] if v['person'] else None))
        for v in seed['registries'].values():
            create(AttendanceRegistry, AttendanceRegistry(attendance_registry_id=v['id'], course_id=seed['courses'][v['course']]['id'],
                                                          events=[], status=AttendanceRegistry.PUBLISHED))
        for v in seed['days'].values():
            create(AttendanceDay, AttendanceDay(attendance_day_id=v['id'], attendance_registry_id=seed['registries'][v['registry']]['id'],
                                                title='Lezione', date=moment(v['date']), attendees=v['attendees']))
        for v in seed['reports'].values():
            create(SavedReport, SavedReport(saved_report_id=v['id'], sport_association_id=v['association'], created_by_id=ids[v['creator']],
                   name=v['name'], description=v['description'], tool_name=v['tool'], params=v['params'], ui_config=v['ui'],
                   created_at=moment(v['created'])))
            SavedReport._base_manager.filter(pk=v['id']).update(updated_at=moment(v['updated']))
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import SportAssociationModuleTemplates
    from application.models.report_models import SavedReport
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    mutations.require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before = scopes.snapshot_state(apps)
    original_audit = [row['id'] for row in before[AUDIT]]
    cache_before, sequences_before = snapshot_historical_read_cache(raw), mutations.sequence_snapshot()
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def remove_rows():
            with disable_auditlog():
                SportAssociationModuleTemplates._base_manager.exclude(
                    pk__in=list(keyed(before[TEMPLATE], 'sport_association_module_templates_id'))).delete()
                SavedReport._base_manager.exclude(pk__in=list(keyed(before[REPORT], 'saved_report_id'))).delete()
                if fixture is not None:
                    shared.delete_created(fixture)
                LogEntry.objects.exclude(pk__in=original_audit).delete()
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
            return True
        receipt.update(scopes.cleanup_steps((
            ('rows', remove_rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: mutations.restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def audit_values(row):
    values = {'sport_association_module_templates_id': row['id'], 'sport_association': row['association']}
    for field in FIELDS:
        if row[field] is not None:
            values[field] = row[field] if isinstance(row[field], str) else str(row[field])
    return values


def stored(row):
    return {'sport_association_id': row['association'], **{f: row[f] for f in FIELDS}}


def assert_effect(case, before, after, source, remote):
    managed = (TEMPLATE, REPORT, AUDIT, INDEX)
    contract.require({k: v for k, v in before.items() if k not in managed} == {k: v for k, v in after.items() if k not in managed}, 'state.other-models')
    effect, key = case['effect'], 'sport_association_module_templates_id'
    old_t, new_t = keyed(before[TEMPLATE], key), keyed(after[TEMPLATE], key)
    old_r, new_r = keyed(before[REPORT], 'saved_report_id'), keyed(after[REPORT], 'saved_report_id')
    old_a, new_a = keyed(before[AUDIT], 'id'), keyed(after[AUDIT], 'id')
    old_i, new_i = keyed(before[INDEX], 'audit_log_index_id'), keyed(after[INDEX], 'audit_log_index_id')
    if effect['kind'] == 'report-delete':
        target = source['seed']['reports'][effect['label']]['id']
        contract.require(target in old_r and set(new_r) == set(old_r) - {target} and all(new_r[k] == old_r[k] for k in new_r), 'state.report-deleted')
    else:
        contract.require(old_r == new_r, 'state.reports-unchanged')
    audits = []
    if effect['kind'] in ('none', 'report-delete'):
        contract.require(old_t == new_t, 'state.templates-unchanged')
    elif effect['kind'] == 'template-update':
        target = effect['id']
        contract.require(set(new_t) == set(old_t) and all(new_t[k] == old_t[k] for k in old_t if k != target), 'state.update-only')
        row = new_t[target]
        contract.require(all(row[k] == v for k, v in stored(effect['row']).items()), 'state.updated-values')
        if effect['changes']:
            audits = [{'id': target, 'action': 1, 'path': 'direct', 'changes': effect['changes']}]
    else:
        row = source['seed']['templates'][effect['label']]
        target = row['id']
        contract.require(target in old_t and set(new_t) == set(old_t) - {target} and all(new_t[k] == old_t[k] for k in new_t), 'state.deleted')
        audits = [{'id': target, 'action': 2, 'path': 'changes_json.sport_association',
                   'changes': {k: [v, 'None'] for k, v in audit_values(row).items()}}]
    added_a = set(new_a) - set(old_a)
    contract.require(len(added_a) == len(audits) and all(new_a[k] == old_a[k] for k in old_a), 'audit.entries')
    added_i = {new_i[k]['log_entry_id']: new_i[k] for k in set(new_i) - set(old_i)}
    contract.require(len(added_i) == len(audits) and all(new_i[k] == old_i[k] for k in old_i), 'index.rows')
    if not audits:
        return
    users = {str(scalar(u['user_id'])): u for u in before['application.User']}
    actor_id = source['ids'][effect['actor']]
    content_types = [ct for ct in before['contenttypes.ContentType'] if ct['app_label'] == 'application' and ct['model'] == 'sportassociationmoduletemplates']
    contract.require(len(content_types) == 1, 'audit.content-type')
    events = [new_a[k] for k in sorted(added_a)]
    for expected, event in zip(audits, events):
        contract.require(event['object_pk'] == expected['id'] and event['content_type_id'] == content_types[0]['id']
                         and event['object_repr'] == f'SportAssociationModuleTemplates object ({expected["id"]})'
                         and event['action'] == expected['action'], 'audit.identity')
        contract.require(event['changes'] == expected['changes'], 'audit.changes')
        contract.require(event['actor_id'] == actor_id and event['actor_email'] == users[actor_id]['email'], 'audit.actor')
        contract.require(event['cid'] == case['name'] and event['additional_data'] is None and event['changes_text'] == '', 'audit.context')
        ipaddress.ip_address(event['remote_addr'])
        contract.require(remote.setdefault('addr', event['remote_addr']) == event['remote_addr'], 'audit.remote-consistent')
        index = added_i.get(event['id'])
        contract.require(index is not None and index['sport_association_id'] == source['ids']['own_association']
                         and index['resolution_path'] == expected['path'], 'index.fields')


def run_workload(label, backend, base_url, fixture, apps, raw, observer):
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    source = contract.load_source()
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    results, remote = [], {}
    for case in source['cases']:
        before = scopes.snapshot_state(apps)
        cursor = observer.cursor()
        token = None if case['actor'] == 'anonymous' else fixture['tokens'][case['actor']]
        try:
            status = contract.assert_case(case, base_url, token=token, sessions=fixture['sessions'])
            after = scopes.snapshot_state(apps)
            shared.assert_attribution(backend, case['method'], case['path'], status, observer.delta(cursor), case['name'])
            assert_effect(case, before, after, source, remote)
        except BaseException as error:
            error.case = f'{label}:{case["name"]}'
            raise
        results.append({'case': case['name'], 'status': status})
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    return {'label': label, 'backend': backend, 'success': True, 'cases': results, 'authored_cases': len(source['cases'])}
