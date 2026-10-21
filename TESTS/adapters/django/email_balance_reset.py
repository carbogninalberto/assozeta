"""Actual daily counter reset task, current Django schema, disposable database."""
from contextlib import redirect_stdout
import io
import json
import subprocess

from .accounts import ROOT
from .scoped_resources import uid


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from django.apps import apps
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from application.models import User, SportAssociation
    from application.tasks import reset_email_balance
    from communications.models import CommunicationConfiguration as Config
    from django_celery_beat.models import PeriodicTask

    output = ROOT / 'quality-reports/go-migration/email-balance-reset'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'body-results.json').write_text('{"success":false}\n')
    policy = {name: getattr(reset_email_balance, name) for name in (
        'acks_late', 'max_retries', 'default_retry_delay', 'rate_limit', 'time_limit',
        'soft_time_limit', 'ignore_result', 'track_started', 'reject_on_worker_lost')}
    policy.update(timezone=reset_email_balance.app.conf.timezone,
                  enable_utc=reset_email_balance.app.conf.enable_utc,
                  configured_occurrences=[name for name, entry in reset_email_balance.app.conf.beat_schedule.items()
                                          if entry['task'] == reset_email_balance.name],
                  persisted_occurrences=list(PeriodicTask.objects.filter(task=reset_email_balance.name).values('name', 'enabled')))
    (output / 'django-policy.json').write_text(json.dumps(policy, indent=2) + '\n')
    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    assert not Config.objects.exists(), 'reset fixture requires an empty private configuration table'
    owners, associations = [uid(89400), uid(89401)], [uid(89410), uid(89411)]
    assert not User.original_objects.filter(pk__in=owners).exists()
    assert not SportAssociation.original_objects.filter(pk__in=associations).exists()
    User.original_objects.bulk_create([User(user_id=pk, username=f'reset-{i}', role=1, password='!fixture') for i, pk in enumerate(owners)])
    SportAssociation.original_objects.bulk_create([SportAssociation(sport_association_id=pk, user_id=owner,
        denomination='Reset fixture', tax_code='00000000000', deleted=bool(i)) for i, (pk, owner) in enumerate(zip(associations, owners))])
    def seed(size):
        with disable_auditlog():
            Config.objects.all().delete()
        Config.objects.bulk_create([Config(communication_configuration_id=uid(90000+i),
            sport_association_id=(associations + [None])[i % 3], daily_email_balance=(0, 1, 2147483647)[i % 3],
            daily_email_limit=321, email_smtp_host='fixture.invalid', email_smtp_user='fixture',
            email_smtp_password='synthetic-not-a-secret', email_encryption='TLS') for i in range(size)])
    def call():
        with redirect_stdout(io.StringIO()), CaptureQueriesContext(connection) as queries:
            result = reset_email_balance.run()
        assert result is None, 'legacy task result changed'
        assert len(queries) == 1 and queries[0]['sql'].startswith('UPDATE '), 'reset must use one bulk UPDATE'
    try:
        seed(0)
        call()
        for size in (1, 16, 128, 512):
            seed(size)
            before = snapshot()
            call()
            after = snapshot()
            expected = before
            for row in expected['communications.CommunicationConfiguration']:
                row['daily_email_balance'] = 0
            assert after == expected, 'reset changed another field, model or audit'
            call()
            assert snapshot() == after, 'duplicate immediate reset is not idempotent'
        seed(16)
        before = snapshot()
        with connection.cursor() as cursor:
            cursor.execute("CREATE FUNCTION contract_reset_failure() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'private fixture failure'; END $$")
            cursor.execute('CREATE TRIGGER contract_reset_failure BEFORE UPDATE ON communications_communicationconfiguration FOR EACH STATEMENT EXECUTE FUNCTION contract_reset_failure()')
        try:
            call()
            assert snapshot() == before, 'failed UPDATE was not atomic'
        finally:
            with connection.cursor() as cursor:
                cursor.execute('DROP TRIGGER contract_reset_failure ON communications_communicationconfiguration')
                cursor.execute('DROP FUNCTION contract_reset_failure()')
        # Seed the same complete records for Go, then verify its writes with Django.
        seed(512)
        before_go = snapshot()
        print('Django reset: empty, mixed scopes, max counter, repeated reset, one query at 1/16/128/512 and swallowed SQL failure pass', flush=True)
        completed = subprocess.run(['go', 'test', '-race', '-tags=emailbalanceintegration', './internal/service', '-run', '^TestEmailBalanceResetPostgres$', '-json'],
            cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_EMAIL_BALANCE_DSN': f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},
            capture_output=True, text=True, timeout=120)
        (output / 'go-test.jsonl').write_text(completed.stdout)
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'):
                print(event['Output'], end='')
        if completed.stderr:
            print(completed.stderr)
        completed.check_returncode()
        names = {'bulk reset and Django readback', 'invalid job scope and payload have no effects',
                 'completed occurrence cannot erase later usage', 'stale completion rolls back counters',
                 'legacy SQL failure completes without partial reset', 'cancelled database work is recoverable',
                 'bounded query count across increasing sizes'}
        expected = {'TestEmailBalanceResetPostgres/' + name.replace(' ', '_') for name in names}
        passed = {e.get('Test') for e in events if e['Action'] == 'pass' and '/' in e.get('Test', '')}
        assert passed == expected and not any(e['Action'] == 'skip' for e in events), 'missing or skipped reset scenario'
        for row in before_go['communications.CommunicationConfiguration']:
            row['daily_email_balance'] = 0
        assert snapshot() == before_go, 'Go changed unrelated fields/models or emitted audit'
        result = {'success': True, 'scenario_groups': len(names), 'sizes': [1,16,128,512],
                  'scope': 'task body and transaction/replay failures; no scheduler or production ownership'}
        (output / 'body-results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
        return len(names)
    finally:
        with disable_auditlog():
            Config.objects.all().delete()
            SportAssociation.original_objects.filter(pk__in=associations).delete()
            User.original_objects.filter(pk__in=owners).delete()
        assert snapshot() == original, 'reset fixture cleanup changed preexisting records'
