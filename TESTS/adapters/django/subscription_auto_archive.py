"""auto_archive_subscription body: the actual Django task at a frozen instant, then the Go service on the same seed."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import subprocess
from unittest.mock import patch

from .accounts import ROOT
from .scoped_resources import uid

# The balance year of owner 0 (1 September) ended at the start of 31 August;
# Django archives from year_end + 1 day, so this instant is the first one that archives.
NOW = datetime(2030, 9, 1, tzinfo=timezone.utc)


def _normalized(state, since, until, known=frozenset()):
    """Audit ids, index ids and timestamps vary by run; everything else must match.

    Entries in known (and their indexes) predate the fixture and are left out.
    """
    state = dict(state)
    state['application.AuditLogIndex'] = [i for i in state['application.AuditLogIndex'] if i['log_entry_id'] not in known]
    keys, rows = {}, []
    for entry in [e for e in state['auditlog.LogEntry'] if e['id'] not in known]:
        entry = dict(entry)
        assert since <= entry['timestamp'] <= until, 'audit timestamp outside the run window'
        keys[entry['id']] = (entry['content_type_id'], entry['object_pk'], json.dumps(entry['changes'], sort_keys=True))
        entry.pop('id'); entry['timestamp'] = 'run'
        rows.append(entry)
    state['auditlog.LogEntry'] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True, default=str))
    indexes = []
    for index in state['application.AuditLogIndex']:
        index = dict(index)
        assert since <= index['created_at'] <= until, 'index timestamp outside the run window'
        index.pop('audit_log_index_id'); index['created_at'] = 'run'
        index['log_entry_id'] = keys[index['log_entry_id']]
        indexes.append(index)
    state['application.AuditLogIndex'] = sorted(indexes, key=lambda row: json.dumps(row, sort_keys=True, default=str))
    return state


def run(work, log, tool_env):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from django.apps import apps
    from application import tasks
    from application.models import User, SportAssociation, Subscription, Payment, Invoice

    output = ROOT / 'quality-reports/go-migration/subscription-auto-archive'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'body-results.json').write_text('{"success":false}\n')

    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values())
                for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    owned = (Subscription, Payment, Invoice)
    assert all(not m._base_manager.exists() for m in owned), 'archive fixture requires empty owned tables'
    known = frozenset(LogEntry.objects.values_list('pk', flat=True))
    # Owners: 0 archives (year from 1 September), 1 disabled, 2 a balance year
    # from 2 September (one day later), 3 an invalid subscription calendar,
    # 4 an invalid balance year, 5 a deleted association.
    settings = [dict(auto_archive=True, balance_sheet_start_month=9), dict(auto_archive=False, balance_sheet_start_month=9),
                dict(auto_archive=True, balance_sheet_start_month=9, balance_sheet_start_day=2),
                dict(auto_archive=True, balance_sheet_start_month=9, subscription_start_month=0),
                dict(auto_archive=True, balance_sheet_start_month=13), dict(auto_archive=True, balance_sheet_start_month=9)]
    users = [User(user_id=uid(160000 + i), username=f'ARCHIVE-{i}', password='!fixture', role=1,
                  **dict(dict(balance_sheet_start_day=1, subscription_start_day=1, subscription_start_month=9), **values))
             for i, values in enumerate(settings)]
    associations = [SportAssociation(sport_association_id=uid(160010 + i), user=u, denomination='Archive fixture',
                                     tax_code=f'{i:011d}', deleted=i == 5) for i, u in enumerate(users)]
    year = datetime(2030, 8, 31, 12, tzinfo=timezone.utc)
    # (name, owner, created, deleted, archived, payment, invoice, archived after)
    specs = [
        ('last day of the ended year', 0, year, False, False, 'live', 'live', True),
        ('first day of the ended year', 0, datetime(2029, 9, 1, tzinfo=timezone.utc), False, False, 'live', None, True),
        ('first instant of the current year', 0, NOW, False, False, 'live', 'live', False),
        ('without payment', 0, year, False, False, None, None, True),
        ('hidden payment and invoice', 0, year, False, False, 'deleted', 'deleted', True),
        ('already archived payment and invoice', 0, year, False, False, 'archived', 'archived', True),
        ('deleted subscription', 0, year, True, False, 'live', 'live', False),
        ('already archived subscription', 0, year, False, True, 'live', 'live', True),
        ('owner without auto archive', 1, year, False, False, 'live', 'live', False),
        ('year ends one day later', 2, year, False, False, 'live', 'live', False),
        ('invalid subscription calendar', 3, year, False, False, 'live', 'live', False),
        ('invalid balance year', 4, year, False, False, 'live', 'live', False),
        ('deleted association', 5, year, False, False, 'live', 'live', True),
    ]
    try:
        with disable_auditlog():
            User.original_objects.bulk_create(users)
            SportAssociation.original_objects.bulk_create(associations)
            invoices, payments, subscriptions = [], [], []
            for i, (name, owner, created, deleted, archived, payment, invoice, _) in enumerate(specs):
                association = associations[owner]
                if invoice:
                    invoices.append(Invoice(invoice_id=uid(160100 + i), sport_association=association, membership_fee=Decimal('10.00'),
                        activity_fee=Decimal('0.01'), creation_date=year, deleted=invoice == 'deleted', archived=invoice == 'archived'))
                if payment:
                    payments.append(Payment(payment_id=uid(160200 + i), sport_association=association, invoice_id=uid(160100 + i) if invoice else None,
                        subject=1, amount=Decimal('10.00'), description='Preserved', creation_date=year,
                        deleted=payment == 'deleted', archived=payment == 'archived'))
                subscriptions.append(Subscription(subscription_id=uid(160300 + i), sport_association=association, user=users[owner],
                    payment_id=uid(160200 + i) if payment else None, deleted=deleted, archived=archived, notes=name))
            Invoice._base_manager.bulk_create(invoices)
            Payment._base_manager.bulk_create(payments)
            Subscription._base_manager.bulk_create(subscriptions)
            for i, spec in enumerate(specs):
                Subscription._base_manager.filter(pk=uid(160300 + i)).update(creation_date=spec[2])
        before = snapshot()
        since = datetime.now(timezone.utc)
        with patch('application.tasks.timezone.now', return_value=NOW):
            archived_count = tasks.auto_archive_subscription.run()
        until = datetime.now(timezone.utc)
        django_after = snapshot()
        # Authored expectations, independent of the Go run.
        archived = {row['notes'] for row in django_after['application.Subscription'] if row['archived']}
        assert archived == {spec[0] for spec in specs if spec[7]}, sorted(archived)
        assert archived_count == sum(1 for spec in specs if spec[7] and not spec[4]), archived_count
        entries = {(e['content_type_id'], e['object_pk']): e for e in django_after['auditlog.LogEntry'] if e['id'] not in known}
        hidden = {uid(160204), uid(160104)}
        for entry in entries.values():
            assert entry['action'] == LogEntry.Action.UPDATE and entry['actor_id'] is None and entry['cid'] is None
            expected = ['None', 'True'] if entry['object_pk'] in hidden else ['False', 'True']
            assert entry['changes'] == {'archived': expected}, entry
        assert not any(pk in (uid(160205), uid(160105)) for _, pk in entries), 'archived rows gained audits'
        indexes = [i for i in django_after['application.AuditLogIndex'] if i['log_entry_id'] not in known]
        assert len(indexes) == len(entries) - 2 and all(index['resolution_path'] == 'direct' for index in indexes)
        django_state = _normalized(django_after, since, until, known)
        # Restore the seed for the Go service.
        with disable_auditlog():
            LogEntry.objects.exclude(pk__in=known).delete()
            for model in owned:
                for record in before[model._meta.label]:
                    model._base_manager.filter(pk=record[model._meta.pk.attname]).update(**record)
        assert snapshot() == before
        fixture = work / 'subscription-auto-archive.json'
        fixture.write_text(json.dumps(dict(Now=NOW.isoformat())))
        task = tasks.auto_archive_subscription
        policy = {key: getattr(task, key) for key in ('acks_late', 'max_retries', 'default_retry_delay', 'rate_limit', 'time_limit', 'soft_time_limit')}
        (output / 'django-oracle.json').write_text(json.dumps(dict(success=True, model_cases=len(specs), archived=len(archived),
            audits=len(entries), indexes=len(indexes), policy=policy), indent=2) + '\n')
        print(f'Django archive body: {len(specs)} authored cases, {len(entries)} audits and {len(indexes)} indexes at {NOW.isoformat()}', flush=True)
        since = datetime.now(timezone.utc)
        completed = subprocess.run(['go', 'test', '-race', '-tags=subscriptionarchiveintegration', './internal/service',
            '-run', '^TestSubscriptionAutoArchivePostgres$', '-json', '-count=1'],
            cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_ARCHIVE_FIXTURE': str(fixture),
                'ASSOZETA_ARCHIVE_DSN': f'host={work} user=contracts dbname=assozeta_contract_accounts sslmode=disable'},
            capture_output=True, text=True, timeout=180)
        until = datetime.now(timezone.utc)
        (output / 'go-test.jsonl').write_text(completed.stdout)
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        for event in events:
            if event.get('Output'):
                print(event['Output'], end='', flush=True)
        if completed.stderr:
            print(completed.stderr, flush=True)
        completed.check_returncode()
        names = {'invalid envelope before SQL', 'cancelled context before SQL', 'lost lease writes nothing',
                 'archives the seed at the frozen instant'}
        expected_names = {'TestSubscriptionAutoArchivePostgres/' + n.replace(' ', '_') for n in names}
        passed = {e.get('Test') for e in events if e['Action'] == 'pass' and '/' in e.get('Test', '')}
        assert passed == expected_names and not any(e['Action'] == 'skip' for e in events), 'missing or skipped archive scenario'
        go_state = _normalized(snapshot(), since, until, known)
        if go_state != django_state:
            differences = {label: {'django': django_state.get(label), 'go': go_state.get(label)}
                           for label in set(django_state) | set(go_state) if django_state.get(label) != go_state.get(label)}
            (output / 'difference.json').write_text(json.dumps(differences, indent=1, default=str) + '\n')
        assert go_state == django_state, 'Go archive differs from the Django task at the same instant'
        (output / 'body-results.json').write_text(json.dumps(dict(success=True, scenario_groups=len(names) + 1,
            model_cases=len(specs)), indent=2) + '\n')
        return len(names) + 1
    finally:
        with disable_auditlog():
            LogEntry.objects.exclude(pk__in=known).delete()
            for model in owned:
                model._base_manager.all()._raw_delete('default')
            SportAssociation.original_objects.filter(pk__in=[a.pk for a in associations]).delete()
            User.original_objects.filter(pk__in=[u.pk for u in users]).delete()
        assert snapshot() == original, 'archive fixture changed preexisting managed rows'
