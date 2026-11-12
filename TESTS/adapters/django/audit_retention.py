"""Audit retention: exact 365-day cutoff and collector cascade on current schema."""
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import json
import subprocess
import time
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from django.db import connection, connections, transaction
    from django.test.utils import CaptureQueriesContext
    from django_celery_beat.models import PeriodicTask
    from application import tasks
    from application.models import User, SportAssociation, Associate
    from application.models.audit_models import AuditLogIndex

    output = ROOT / 'quality-reports/go-migration/audit-retention'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'body-results.json').write_text('{"success":false}\n')
    assert not LogEntry.objects.exists() and not AuditLogIndex.objects.exists(), 'requires private empty audit fixture'
    assert {r.related_model._meta.label for r in LogEntry._meta.related_objects} == {'application.AuditLogIndex'}, 'new audit dependants require explicit retention handling'
    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    owners, associations = [uid(100000+i) for i in range(2)], [uid(100010+i) for i in range(2)]
    User.original_objects.bulk_create([User(user_id=pk, username=f'retention-{i}', role=1, password='!fixture') for i, pk in enumerate(owners)])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=pk, user_id=owner,
        denomination='Retention fixture', tax_code='00000000000', deleted=bool(i)) for i,(pk,owner) in enumerate(zip(associations,owners))])
    now = datetime(2032,3,1,12,34,56,123400,tzinfo=timezone.utc)
    cutoff = now - timedelta(days=365)  # 2031-03-02, not the calendar-year anniversary.
    assert cutoff.date().isoformat() == '2031-03-02'
    content_types = [ContentType.objects.get_for_model(model) for model in (User,Associate)]
    rows = [dict(id=100100+i, stamp=stamp.isoformat(), expired=i<3, indexed=i%2==0,
                 association=associations[(i//2)%2], content_type=content_types[i%2].pk)
            for i,stamp in enumerate((cutoff-timedelta(days=30),cutoff-timedelta(microseconds=1),cutoff-timedelta(hours=2),
                                      cutoff,cutoff+timedelta(microseconds=1),now,now+timedelta(days=1)))]
    def seed(records):
        with disable_auditlog(): LogEntry.objects.all().delete()
        LogEntry.objects.bulk_create([LogEntry(id=r['id'], content_type_id=r['content_type'], object_pk=f"fixture-{r['id']}",
            object_repr='Retention fixture', action=LogEntry.Action.UPDATE, timestamp=r['stamp'],
            actor_id=owners[r['id']%2], changes={'preserved':['old','new']}, cid='synthetic-retention') for r in records])
        AuditLogIndex.objects.bulk_create([AuditLogIndex(audit_log_index_id=uid(200000+r['id']),log_entry_id=r['id'],
            sport_association_id=r['association'],resolution_path='fixture',created_at=now if r['expired'] else cutoff-timedelta(days=30))
            for r in records if r['indexed']])
    def call():
        with patch.object(tasks.timezone, 'now', return_value=now), redirect_stdout(io.StringIO()), CaptureQueriesContext(connection) as queries:
            assert tasks.delete_old_audit_logs.run() is None
        return len(queries)
    def expected(before,records):
        result=dict(before)
        expired={r['id'] for r in records if r['expired']}
        result['auditlog.LogEntry']=[r for r in before['auditlog.LogEntry'] if r['id'] not in expired]
        result['application.AuditLogIndex']=[r for r in before['application.AuditLogIndex'] if r['log_entry_id'] not in expired]
        return result
    try:
        seed([]);before=snapshot();call();assert snapshot()==before
        seed(rows);before=snapshot();call();assert snapshot()==expected(before,rows)
        before=snapshot();call();assert snapshot()==before, 'replay changed retained audit or index'
        query_counts={}
        for size in (1,16,128):
            scaled=[dict(rows[0],id=101000+i) for i in range(size)]
            seed(scaled);before=snapshot();query_counts[size]=call();assert snapshot()==expected(before,scaled)
        for table in ('auditlog_logentry','application_auditlogindex'):
            seed(rows);before=snapshot()
            with connection.cursor() as cursor:
                cursor.execute("CREATE FUNCTION contract_retention_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private retention failure'; END $$")
                cursor.execute(f'CREATE TRIGGER contract_retention_failure BEFORE DELETE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION contract_retention_failure()')
            try:
                call();assert snapshot()==before, 'collector failure left partial retention'
            finally:
                with connection.cursor() as cursor:
                    cursor.execute(f'DROP TRIGGER contract_retention_failure ON {table}')
                    cursor.execute('DROP FUNCTION contract_retention_failure()')
        # Force the same index-insertion race through the real Django collector.
        # Its deferred FK failure is caught and returns None, preserving all rows.
        seed(rows)
        def concurrent_task():
            try:
                return tasks.delete_old_audit_logs.run()
            finally:
                connections.close_all()
        with patch.object(tasks.timezone, 'now', return_value=now), redirect_stdout(io.StringIO()), ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('SELECT id FROM auditlog_logentry WHERE id=%s FOR KEY SHARE', [rows[1]['id']])
                AuditLogIndex.objects.bulk_create([AuditLogIndex(audit_log_index_id=uid(777777), log_entry_id=rows[1]['id'],
                    sport_association_id=associations[0], resolution_path='racing-writer', created_at=now)])
                race_expected=snapshot()
                future=pool.submit(concurrent_task)
                deadline=time.monotonic()+5
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_stat_clear_snapshot()')
                        cursor.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' AND query LIKE %s)", ['DELETE FROM "auditlog_logentry"%'])
                        waiting=cursor.fetchone()[0]
                    if waiting: break
                    assert time.monotonic()<deadline, 'Django retention did not wait on the index writer'
                    time.sleep(0.005)
            assert future.result(timeout=5) is None
        assert snapshot()==race_expected, 'deferred collector failure left partial deletion'
        call()
        assert snapshot()==expected(race_expected,rows), 'later invocation could not remove the new index'
        seed(rows);before_go=snapshot()
        fixture=work/'audit-retention.json'
        fixture.write_text(json.dumps({'now':now.isoformat(),'cutoff':cutoff.isoformat(),'rows':rows}))
        task=tasks.delete_old_audit_logs
        policy={k:getattr(task,k) for k in ('acks_late','max_retries','default_retry_delay','rate_limit','time_limit','soft_time_limit')}
        policy.update(timezone=task.app.conf.timezone,
            configured_occurrences=[name for name,entry in task.app.conf.beat_schedule.items() if entry['task']==task.name],
            persisted_occurrences=list(PeriodicTask.objects.filter(task=task.name).values('name','enabled')))
        (output/'django-oracle.json').write_text(json.dumps({'success':True,'query_counts':query_counts,'policy':policy,
            'concurrent_index_commit_failure_returns_none':True,'expired_entries':3,'expired_indexes':2,'retained_entries':4,'retained_indexes':2},indent=2)+'\n')
        print('Django audit retention: exact 365 days, equality/microsecond boundary, unindexed logs, deleted associations, index-age independence, replay and both cascade failures pass',flush=True)
        completed=subprocess.run(['go','test','-race','-tags=auditretentionintegration','./internal/service','-run','^TestAuditRetentionPostgres$','-json'],
            cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_AUDIT_FIXTURE':str(fixture),'ASSOZETA_AUDIT_PLAN_OUTPUT':str(output/'query-plan.json'),
                'ASSOZETA_AUDIT_DSN':f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},
            capture_output=True,text=True,timeout=180)
        (output/'go-test.jsonl').write_text(completed.stdout)
        events=[json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'):print(event['Output'],end='')
        if completed.stderr:print(completed.stderr)
        completed.check_returncode()
        names={'exact cutoff and dependent index cleanup','invalid envelope before SQL','both deletion failures roll back',
               'stale completion and replay preserve later records','concurrent retention settles once','concurrent index insertion cannot leave an orphan','unrelated commit failures are not swallowed','blocked SQL cancellation and pool recovery',
               'constant statement count and actual query plan','heartbeat during retention','stale admission sends no business SQL',
               'handled failure fences completion-only transaction'}
        wanted={'TestAuditRetentionPostgres/'+n.replace(' ','_') for n in names}
        passed={e.get('Test') for e in events if e['Action']=='pass' and '/' in e.get('Test','')}
        assert passed==wanted and not any(e['Action']=='skip' for e in events),'missing or skipped retention scenario'
        assert snapshot()==expected(before_go,rows),'Go retention changed retained rows or other managed state'
        (output/'body-results.json').write_text(json.dumps({'success':True,'scenario_groups':len(names),'django_query_counts':query_counts,
            'scope':'current-schema task body; scheduler and production ownership not activated'},indent=2)+'\n')
        return len(names)
    finally:
        with disable_auditlog():
            LogEntry.objects.all().delete()
            SportAssociation.original_objects.filter(pk__in=associations).delete()
            User.original_objects.filter(pk__in=owners).delete()
        assert snapshot()==original,'retention fixture cleanup changed preexisting managed state'
