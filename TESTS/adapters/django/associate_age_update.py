"""Actual age task and audit/index side effects in a disposable current schema."""
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
import io
import json
import subprocess
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from django_celery_beat.models import PeriodicTask
    from application import tasks
    from application.models import User, SportAssociation, Associate, Group
    from application.models import user_models
    from application.models.audit_models import AuditLogIndex

    output = ROOT / 'quality-reports/go-migration/associate-age-update'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'body-results.json').write_text('{"success":false}\n')
    ct = ContentType.objects.get_for_model(Associate)
    assert not Associate._base_manager.exists() and not LogEntry.objects.filter(content_type=ct).exists()
    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    owners, associations, groups = ([uid(98000+i) for i in range(2)], [uid(98010+i) for i in range(2)], [uid(98020+i) for i in range(2)])
    User.original_objects.bulk_create([User(user_id=pk, username=f'age-{i}', role=1, password='!fixture') for i, pk in enumerate(owners)])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=pk, user_id=owner,
        denomination='Age fixture', tax_code='00000000000', deleted=bool(i)) for i, (pk, owner) in enumerate(zip(associations, owners))])
    Group.objects.bulk_create([Group(group_id=pk, sport_association_id=association, name='Age group') for pk, association in zip(groups, associations)])
    now = datetime(2030, 3, 1, 23, 59, tzinfo=timezone.utc)
    today = now.date()
    # 6574 days is still 17 under the existing truncated days / 365.25 rule.
    cases = [(None, True, False, False, False, 0, False),
             (6574, True, False, False, False, 0, False),
             (6575, True, False, False, False, 0, True),
             (6576, True, False, True, False, 1, True),
             (40000, True, False, False, True, None, True),
             (6575, True, True, False, False, 0, False),
             (7000, False, False, False, False, 0, False),
             (-100, True, False, False, False, 0, False),
             (0, True, False, False, False, 1, False)]
    rows = [dict(id='00000000-0000-0000-0000-000000000000' if i == 2 else uid(98100+i), born=None if days is None else (today-timedelta(days=days)).isoformat(),
        minor=minor, deleted=deleted, disabled=disabled, draft=draft, association=None if association is None else associations[association],
        group=None if association is None else groups[association], changed=changed)
        for i, (days, minor, deleted, disabled, draft, association, changed) in enumerate(cases)]
    sentinel = LogEntry.objects.bulk_create([LogEntry(content_type=ContentType.objects.get_for_model(User),
        object_pk=owners[0], object_repr='Unrelated fixture audit', action=LogEntry.Action.UPDATE, changes={})])[0]
    AuditLogIndex.objects.create(log_entry=sentinel, sport_association_id=associations[0], resolution_path='sentinel', created_at=now)
    def seed(records):
        with disable_auditlog():
            Associate._base_manager.all().delete()
            LogEntry.objects.filter(content_type=ct).delete()
        Associate._base_manager.bulk_create([Associate(associate_id=r['id'], born_date=r['born'], is_minor=r['minor'],
            deleted=r['deleted'], disabled=r['disabled'], draft=r['draft'], sport_association_id=r['association'],
            group_id=r['group'], first_name='Age', last_name='Fixture', notes='preserve', creation_date=now) for r in records])
    def call():
        with patch.object(user_models, 'date', wraps=date) as clock, patch('django.utils.timezone.datetime', wraps=datetime) as audit_clock, redirect_stdout(io.StringIO()), CaptureQueriesContext(connection) as queries:
            clock.today.return_value = today
            audit_clock.now.return_value = now
            assert tasks.update_age_of_associates.run() is None
        return len(queries)
    def check(records, before):
        expected = [dict(r, is_minor=False) if any(x['id'] == str(r['associate_id']) and x['changed'] for x in records) else r
                    for r in before['application.Associate']]
        assert list(Associate._base_manager.order_by('pk').values()) == expected, 'age task changed another associate field'
        logs = list(LogEntry.objects.filter(content_type=ct))
        assert {e.object_pk for e in logs} == {r['id'] for r in records if r['changed']}
        assert len(logs) == sum(r['changed'] for r in records)
        for entry in logs:
            row = next(r for r in records if r['id'] == entry.object_pk)
            assert entry.action == LogEntry.Action.UPDATE and entry.changes == {'is_minor': ['True', 'False']}
            assert entry.object_repr == f"Associate object ({row['id']})" and entry.timestamp == now
            assert entry.changes_text == ''
            assert all(getattr(entry, k) is None for k in ('actor_id', 'actor_email', 'object_id', 'serialized_data', 'additional_data', 'remote_addr', 'remote_port', 'cid'))
            indexes = list(AuditLogIndex.objects.filter(log_entry=entry))
            assert len(indexes) == int(row['association'] is not None)
            if indexes:
                assert str(indexes[0].sport_association_id) == row['association'] and indexes[0].resolution_path == 'direct'
                assert indexes[0].created_at == now
        after = snapshot()
        assert [r for r in before['auditlog.LogEntry'] if r['content_type_id'] != ct.pk] == [r for r in after['auditlog.LogEntry'] if r['content_type_id'] != ct.pk], 'unrelated audit changed'
        assert [r for r in before['application.AuditLogIndex'] if r['log_entry_id'] == sentinel.pk] == [r for r in after['application.AuditLogIndex'] if r['log_entry_id'] == sentinel.pk], 'unrelated audit index changed'
        for key in ('application.Associate', 'auditlog.LogEntry', 'application.AuditLogIndex'):
            after.pop(key)
        rest = {k:v for k,v in before.items() if k not in ('application.Associate', 'auditlog.LogEntry', 'application.AuditLogIndex')}
        assert after == rest, 'age task changed unrelated managed rows'
    try:
        seed(rows)
        before = snapshot()
        call()
        check(rows, before)
        repeated = snapshot()
        call()
        assert snapshot() == repeated
        query_counts = {}
        for size in (1, 16, 128):
            scaled = [dict(rows[2], id=uid(98200+i)) for i in range(size)]
            seed(scaled)
            before = snapshot()
            query_counts[size] = call()
            check(scaled, before)
        # Legacy pre_save audit is committed before the actual UPDATE. A failed
        # UPDATE leaves a false audit and index: an explicit atomicity correction
        # is required by the Go transaction contract.
        seed([rows[2]])
        with connection.cursor() as cursor:
            cursor.execute("CREATE FUNCTION contract_age_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private age update failure'; END $$")
            cursor.execute('CREATE TRIGGER contract_age_failure BEFORE UPDATE ON application_associate FOR EACH STATEMENT EXECUTE FUNCTION contract_age_failure()')
        try:
            call()
            assert Associate._base_manager.get(pk=rows[2]['id']).is_minor is True
            assert LogEntry.objects.filter(content_type=ct).count() == 1
            assert AuditLogIndex.objects.filter(log_entry__content_type=ct).count() == 1
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TRIGGER contract_age_failure ON application_associate')
                cursor.execute('DROP FUNCTION contract_age_failure()')
        # The index signal catches its own INSERT error. Legacy UPDATE and audit
        # then succeed without an association index; Go instead rolls back the
        # whole invariant, as verified by the index-trigger integration case.
        seed([rows[2]])
        with connection.cursor() as cursor:
            cursor.execute("CREATE FUNCTION contract_age_index_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private age index failure'; END $$")
            cursor.execute('CREATE TRIGGER contract_age_index_failure BEFORE INSERT ON application_auditlogindex FOR EACH STATEMENT EXECUTE FUNCTION contract_age_index_failure()')
        try:
            call()
            assert Associate._base_manager.get(pk=rows[2]['id']).is_minor is False
            assert LogEntry.objects.filter(content_type=ct).count() == 1
            assert not AuditLogIndex.objects.filter(log_entry__content_type=ct).exists()
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TRIGGER contract_age_index_failure ON application_auditlogindex')
                cursor.execute('DROP FUNCTION contract_age_index_failure()')
        seed(rows)
        before_go = snapshot()
        fixture = work / 'associate-age-update.json'
        fixture.write_text(json.dumps({'now': now.isoformat(), 'rows': rows}))
        task = tasks.update_age_of_associates
        policy = {key: getattr(task, key) for key in ('acks_late', 'max_retries', 'default_retry_delay', 'rate_limit', 'time_limit', 'soft_time_limit')}
        policy.update(timezone=task.app.conf.timezone,
            configured_occurrences=[name for name, entry in task.app.conf.beat_schedule.items() if entry['task'] == task.name],
            persisted_occurrences=list(PeriodicTask.objects.filter(task=task.name).values('name', 'enabled')))
        (output / 'django-oracle.json').write_text(json.dumps({'success': True, 'query_counts': query_counts, 'policy': policy,
            'failed_update_audits': 1, 'failed_update_indexes': 1, 'failed_index_legacy_update_committed': True}, indent=2)+'\n')
        print('Django age task: 6574/6575-day boundary, null/future birth, deleted/disabled/draft/group scopes, exact audits/indexes and false audit on UPDATE failure characterized', flush=True)
        completed = subprocess.run(['go', 'test', '-race', '-tags=ageupdateintegration', './internal/service', '-run', '^TestAssociateAgeUpdatePostgres$', '-json'],
            cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_AGE_FIXTURE': str(fixture),
                'ASSOZETA_AGE_PLAN_OUTPUT': str(output / 'query-plan.json'),
                'ASSOZETA_AGE_DSN': f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},
            capture_output=True, text=True, timeout=180)
        (output / 'go-test.jsonl').write_text(completed.stdout)
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'): print(event['Output'], end='')
        if completed.stderr: print(completed.stderr)
        completed.check_returncode()
        names = {'exact age and scope with audit index', 'invalid envelope before SQL', 'business failures roll back audit and index',
                 'stale completion and replay cannot change later data', 'concurrent jobs update and audit once',
                 'blocked SQL cancellation and pool recovery', 'constant statement count and actual query plan', 'heartbeat during update'}
        expected = {'TestAssociateAgeUpdatePostgres/' + n.replace(' ', '_') for n in names}
        passed = {e.get('Test') for e in events if e['Action'] == 'pass' and '/' in e.get('Test', '')}
        assert passed == expected and not any(e['Action'] == 'skip' for e in events), 'missing or skipped age scenario'
        check(rows, before_go)
        result = {'success': True, 'scenario_groups': len(names), 'django_query_counts': query_counts,
                  'scope': 'current-schema task body; no production scheduler activation'}
        (output / 'body-results.json').write_text(json.dumps(result, indent=2)+'\n')
        return len(names)
    finally:
        with disable_auditlog():
            Associate._base_manager.all().delete()
            LogEntry.objects.filter(content_type=ct).delete()
            LogEntry.objects.filter(pk=sentinel.pk).delete()
            Group.objects.filter(pk__in=groups).delete()
            SportAssociation.original_objects.filter(pk__in=associations).delete()
            User.original_objects.filter(pk__in=owners).delete()
        assert snapshot() == original, 'age fixture cleanup changed preexisting records'
