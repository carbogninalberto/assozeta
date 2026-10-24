"""Expired membership-card tokens: actual task, deletion audit and UTC-day boundary."""
from contextlib import redirect_stdout
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import io
import json
import subprocess
from threading import Barrier
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from django.contrib.contenttypes.models import ContentType
    from django.db import connection, connections
    from django.db.models.deletion import Collector
    from django.test.utils import CaptureQueriesContext
    from application import tasks
    from application.models import User, SportAssociation, Subscription, SubscriptionToken
    from application.models.audit_models import AuditLogIndex

    output = ROOT / 'quality-reports/go-migration/subscription-token-cleanup'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'results.json').write_text('{"success":false}\n')
    ct = ContentType.objects.get_for_model(SubscriptionToken)
    assert not SubscriptionToken.objects.exists() and not LogEntry.objects.filter(content_type=ct).exists(), 'requires private empty token fixture'
    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    owners, associations, subscriptions = ([uid(95400), uid(95401)], [uid(95410), uid(95411)], [uid(95420), uid(95421)])
    assert not User.original_objects.filter(pk__in=owners).exists()
    User.original_objects.bulk_create([User(user_id=pk, username=f'token-cleanup-{i}', role=1, password='!fixture') for i, pk in enumerate(owners)])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=pk, user_id=owner,
        denomination='Token cleanup fixture', tax_code='00000000000', deleted=bool(i)) for i, (pk, owner) in enumerate(zip(associations, owners))])
    Subscription.objects.bulk_create([Subscription(subscription_id=pk, sport_association_id=association, user_id=owner, deleted=bool(i))
        for i, (pk, association, owner) in enumerate(zip(subscriptions, associations, owners))])
    sentinel = LogEntry.objects.bulk_create([LogEntry(content_type=ContentType.objects.get_for_model(User),
        object_pk=owners[0], object_repr='Unrelated fixture audit', action=LogEntry.Action.UPDATE, changes={})])[0]
    cutoff = datetime(2030, 1, 2, tzinfo=timezone.utc)
    now = cutoff + timedelta(hours=23, minutes=59)
    created = datetime(2029, 12, 20, 11, 22, 33, 123400, tzinfo=timezone.utc)
    tokens = [dict(id='00000000-0000-0000-0000-000000000000' if i == 0 else uid(95500+i),
        subscription=subscriptions[i % 2], token=uid(95600+i), created=created.isoformat(), expires=expiry.isoformat(), deleted=i < 3)
        for i, expiry in enumerate((cutoff-timedelta(days=30), cutoff-timedelta(microseconds=1), cutoff-timedelta(hours=2),
                                    cutoff, cutoff+timedelta(microseconds=1), cutoff+timedelta(hours=12), cutoff+timedelta(days=1)))]
    def seed(rows):
        with disable_auditlog():
            SubscriptionToken.objects.all().delete()
            LogEntry.objects.filter(content_type=ct).delete()
        SubscriptionToken.objects.bulk_create([SubscriptionToken(subscription_token_id=row['id'], subscription_id=row['subscription'],
            token=row['token'], creation_date=row['created'], expiration_date=row['expires']) for row in rows])
    def call():
        with patch.object(tasks, 'datetime') as clock, patch('django.utils.timezone.datetime', wraps=datetime) as audit_clock, redirect_stdout(io.StringIO()), CaptureQueriesContext(connection) as queries:
            clock.now.return_value = now.replace(tzinfo=None)
            audit_clock.now.return_value = now
            assert tasks.clear_expired_subscription_tokens.run() is None
        return len(queries)
    def expected_changes(row):
        def stamp(value): return str(datetime.fromisoformat(value).astimezone(timezone.utc).replace(tzinfo=None))
        return {'subscription_token_id': [row['id'], 'None'], 'subscription': [row['subscription'], 'None'],
                'token': [row['token'], 'None'], 'creation_date': [stamp(row['created']), 'None'],
                'expiration_date': [stamp(row['expires']), 'None']}
    def check(rows):
        assert set(str(pk) for pk in SubscriptionToken.objects.values_list('pk', flat=True)) == {r['id'] for r in rows if not r['deleted']}
        logs = list(LogEntry.objects.filter(content_type=ct))
        assert len(logs) == sum(r['deleted'] for r in rows), 'wrong audit count'
        entries = {e.object_pk: e for e in logs}
        assert set(entries) == {r['id'] for r in rows if r['deleted']}, 'missing or duplicate deletion audit'
        assert not AuditLogIndex.objects.filter(log_entry__content_type=ct).exists(), 'background post-delete token audit has no association index'
        for row in rows:
            if not row['deleted']:
                continue
            entry = entries[row['id']]
            assert entry.action == LogEntry.Action.DELETE and entry.changes == expected_changes(row), (row['id'], entry.changes)
            assert entry.object_repr == f"SubscriptionToken object ({row['id']})"
            assert entry.timestamp == now, 'audit timestamp differs from the injected UTC clock'
            assert all(getattr(entry, name) is None for name in ('actor_id', 'actor_email', 'object_id', 'serialized_data', 'additional_data', 'remote_addr', 'remote_port', 'cid'))
            assert entry.changes_text == ''
    try:
        seed([])
        call()
        seed(tokens)
        call()
        check(tokens)
        before_replay = snapshot()
        call()
        assert snapshot() == before_replay, 'repeated cleanup changed rows or audit'
        query_counts = {}
        for size in (1, 16, 128):
            rows = [dict(tokens[0], id=uid(96000+i), token=uid(97000+i)) for i in range(size)]
            seed(rows)
            query_counts[size] = call()
            check(rows)
        # Audit insertion fails after SQL deletion: the collector transaction must
        # restore the tokens, and the task catches the error with a None result.
        seed(tokens)
        before = snapshot()
        with connection.cursor() as cursor:
            cursor.execute("CREATE FUNCTION contract_token_audit_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private token audit failure'; END $$")
            cursor.execute('CREATE TRIGGER contract_token_audit_failure BEFORE INSERT ON auditlog_logentry FOR EACH STATEMENT EXECUTE FUNCTION contract_token_audit_failure()')
        try:
            call()
            assert snapshot() == before, 'failed audit left deleted tokens or partial audit'
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TRIGGER contract_token_audit_failure ON auditlog_logentry')
                cursor.execute('DROP FUNCTION contract_token_audit_failure()')
        # The legacy collector emits post_delete for its original object set,
        # even when another transaction already deleted those rows. Force that
        # real overlap; Go intentionally audits only DELETE RETURNING rows.
        seed(tokens)
        barrier = Barrier(2)
        original_delete = Collector.delete
        def simultaneous_delete(collector):
            if SubscriptionToken in collector.data:
                barrier.wait(timeout=5)
            return original_delete(collector)
        def concurrent_call():
            try:
                assert tasks.clear_expired_subscription_tokens.run() is None
            finally:
                connections.close_all()
        with patch.object(Collector, 'delete', simultaneous_delete), patch.object(tasks, 'datetime') as clock, redirect_stdout(io.StringIO()):
            clock.now.return_value = now.replace(tzinfo=None)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(concurrent_call) for _ in range(2)]
                for future in futures:
                    future.result(timeout=15)
        assert SubscriptionToken.objects.count() == 4
        assert LogEntry.objects.filter(content_type=ct).count() == 6, 'legacy duplicate post-delete behavior changed'
        for row in tokens:
            assert LogEntry.objects.filter(content_type=ct, object_pk=row['id']).count() == (2 if row['deleted'] else 0)
        seed(tokens)
        before_go = snapshot()
        fixture = work / 'subscription-token-cleanup.json'
        fixture.write_text(json.dumps({'now': now.isoformat(), 'cutoff': cutoff.isoformat(), 'tokens': tokens,
                                       'expected_changes': {row['id']: expected_changes(row) for row in tokens}}))
        policy = {name: getattr(tasks.clear_expired_subscription_tokens, name) for name in
                  ('acks_late', 'max_retries', 'default_retry_delay', 'rate_limit', 'time_limit', 'soft_time_limit')}
        (output / 'django-oracle.json').write_text(json.dumps({'success': True, 'query_counts': query_counts, 'policy': policy,
            'concurrent_deleted_tokens': 3, 'concurrent_audits': 6,
            'intended_go_concurrent_audits': 3}, indent=2)+'\n')
        print('Django token cleanup: UTC midnight, retained same-day tokens, nil UUID, deleted association/subscription, exact deletion audit, replay and audit-failure rollback pass', flush=True)
        completed = subprocess.run(['go', 'test', '-race', '-tags=tokencleanupintegration', './internal/service', '-run', '^TestSubscriptionTokenCleanupPostgres$', '-json'],
            cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_TOKEN_CLEANUP_FIXTURE': str(fixture),
                'ASSOZETA_TOKEN_PLAN_OUTPUT': str(output / 'query-plan.json'),
                'ASSOZETA_TOKEN_CLEANUP_DSN': f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},
            capture_output=True, text=True, timeout=180)
        (output / 'go-test.jsonl').write_text(completed.stdout)
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'):
                print(event['Output'], end='')
        if completed.stderr:
            print(completed.stderr)
        completed.check_returncode()
        names = {'UTC cutoff exact audit and deleted association scope', 'invalid job scope and arguments reject before SQL',
                 'audit failure rolls back every deletion', 'stale completion rolls back tokens and audits',
                 'replay cannot delete a later token or duplicate audits', 'concurrent cleanup audits each deleted token once',
                 'blocked read cancellation preserves recoverable work', 'single command delete and audit across increasing sizes',
                 'heartbeat can renew during atomic cleanup', 'actual delete and audit query plan',
                 'stale ownership generation expired lease and token send no business SQL',
                 'handled failure fences completion-only transaction'}
        expected = {'TestSubscriptionTokenCleanupPostgres/' + name.replace(' ', '_') for name in names}
        passed = {e.get('Test') for e in events if e['Action'] == 'pass' and '/' in e.get('Test', '')}
        assert passed == expected and not any(e['Action'] == 'skip' for e in events), 'missing or skipped token cleanup scenario'
        check(tokens)
        after_go = snapshot()
        assert [r for r in before_go['auditlog.LogEntry'] if r['content_type_id'] != ct.pk] == [r for r in after_go['auditlog.LogEntry'] if r['content_type_id'] != ct.pk], 'unrelated audits changed'
        # Only token rows and new token deletion logs may differ.
        before_go.pop('application.SubscriptionToken')
        before_go.pop('auditlog.LogEntry')
        after_go.pop('application.SubscriptionToken')
        after_go.pop('auditlog.LogEntry')
        assert after_go == before_go, 'cleanup changed unrelated managed records'
        report = {'success': True, 'scenario_groups': len(names), 'django_query_counts': query_counts, 'scope': 'task body; scheduler ownership remains open'}
        (output / 'results.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report), flush=True)
        return len(names)
    finally:
        with disable_auditlog():
            SubscriptionToken.objects.all().delete()
            LogEntry.objects.filter(content_type=ct).delete()
            LogEntry.objects.filter(pk=sentinel.pk).delete()
            Subscription._base_manager.filter(pk__in=subscriptions).delete()
            SportAssociation.original_objects.filter(pk__in=associations).delete()
            User.original_objects.filter(pk__in=owners).delete()
        assert snapshot() == original, 'token fixture cleanup changed preexisting managed records'
