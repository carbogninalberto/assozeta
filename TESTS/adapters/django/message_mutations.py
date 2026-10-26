"""Parent-owned service fixture and observations for saved-message CREATE/DELETE.

Inert imports. Requires an exclusively owned, migrated PostgreSQL database and
Redis client. Never starts services or silently substitutes Django for Go.
"""
import copy
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
from pathlib import Path
import time
from uuid import uuid4

from TESTS.contracts import test_message_mutations as contract
from TESTS.adapters.django import message_reads as shared
from TESTS.adapters.django import message_read_scopes as scopes

ROOT = Path(__file__).resolve().parents[3]
DELETE_LABELS = ('delete_email', 'delete_post', 'delete_legacy', 'delete_full',
                 'delete_admin', 'delete_owner_session', 'fault_delete',
                 'delete_foreign_owner', 'delete_foreign_admin')


def runtime_manifest(source, tokens, sessions):
    result = copy.deepcopy(source)
    result['source_sha256'] = contract.source_digest(source)
    for case in result['cases']:
        actor = case.pop('actor')
        case['token'] = None if actor == 'anonymous' else tokens[actor]
        case['headers'] = contract.substitute(case['headers'], sessions)
        case['audit_additional'] = contract.substitute(case['audit_additional'], sessions)
    contract.validate_runtime(result, source)
    return result


def require_private_database():
    from django.db import connection
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    contract.require(__debug__, 'runtime.optimized-python')
    name = connection.settings_dict['NAME']
    contract.require(connection.vendor == 'postgresql' and isinstance(name, str)
                     and name.startswith('assozeta_contract_') and name.isidentifier(), 'runtime.private-postgresql')
    contract.require(connection.get_autocommit() and not connection.in_atomic_block, 'runtime.autocommit-required')


def schema_evidence():
    """Inspect actual SQL FK actions/indexes, not only ORM on_delete declarations."""
    from django.db import connection
    from django.db.models import CASCADE
    from communications.models import Message, MessageTransaction
    from auditlog.registry import auditlog
    require_private_database()
    relations = Message._meta.related_objects
    contract.require(len(relations) == 1 and relations[0].related_model is MessageTransaction
                     and relations[0].on_delete is CASCADE, 'schema.reverse-relations')
    contract.require(auditlog.contains(Message) and not auditlog.contains(MessageTransaction), 'schema.audit-registration')
    with connection.cursor() as cursor:
        cursor.execute('SELECT conname, conrelid::regclass::text, confdeltype, condeferrable, condeferred, '
                       'pg_get_constraintdef(oid) FROM pg_constraint WHERE contype = %s AND confrelid = %s::regclass '
                       'ORDER BY conname', ['f', Message._meta.db_table])
        references = [dict(zip(('name', 'table', 'delete_action', 'deferrable', 'initially_deferred', 'definition'), row))
                      for row in cursor.fetchall()]
        cursor.execute('SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = current_schema() '
                       'AND tablename IN (%s, %s) ORDER BY tablename, indexname',
                       [Message._meta.db_table, MessageTransaction._meta.db_table])
        indexes = [dict(zip(('table', 'name', 'definition'), row)) for row in cursor.fetchall()]
    contract.require(len(references) == 1 and references[0]['table'] == MessageTransaction._meta.db_table
                     and references[0]['delete_action'] == 'a' and references[0]['deferrable'] is True
                     and references[0]['initially_deferred'] is True, 'schema.real-fk-no-action')
    contract.require(any('message_id' in index['definition'] for index in indexes
                         if index['table'] == MessageTransaction._meta.db_table), 'schema.delivery-message-index')
    return {'references': references, 'indexes': indexes,
            'orm_cascade': 'communications.MessageTransaction.message',
            'sql_cascade': False, 'message_audited': True, 'delivery_audited': False}


def sequence_snapshot():
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute('SELECT sequencename FROM pg_sequences WHERE schemaname = current_schema() ORDER BY sequencename')
        names = [row[0] for row in cursor.fetchall()]
        result = {}
        for name in names:
            cursor.execute(f'SELECT last_value, is_called FROM {connection.ops.quote_name(name)}')
            result[name] = list(cursor.fetchone())
    return result


def restore_sequences(original):
    from django.db import connection
    # Only called under require_private_database's exclusive disposable DB contract.
    contract.require(set(sequence_snapshot()) == set(original), 'cleanup.sequence-set')
    with connection.cursor() as cursor:
        for name, (last_value, called) in original.items():
            cursor.execute('SELECT setval(%s::regclass, %s, %s)',
                           [connection.ops.quote_name(name), last_value, called])
    contract.require(sequence_snapshot() == original, 'cleanup.sequences')
    return True


def private_cache(snapshot):
    """Full raw Redis observation for 0600 private evidence only; never receipts."""
    return [{'key_hex': key.hex(), 'type_hex': entry.redis_type.hex(),
             'dump_hex': entry.serialized_value.hex(), 'expires_at_ms': entry.expires_at_ms}
            for key, entry in sorted(snapshot.entries.items())]


def _seed_target_history(source):
    """Seed prior CREATE/UPDATE and indexes for every successful/fault DELETE target.

    Bulk insertion deliberately bypasses shared signals: these are historical
    preservation sentinels, not evidence that current index resolution works.
    """
    from auditlog.diff import model_instance_diff
    from auditlog.models import LogEntry
    from application.models import AuditLogIndex
    from django.contrib.contenttypes.models import ContentType
    from communications.models import Message
    targets = sorted({case['effect']['message_id'] for case in source['cases']
                      if case['effect']['kind'] == 'delete'} | {source['ids']['fault_delete']})
    content_type = ContentType.objects.get_for_model(Message)
    for offset, identifier in enumerate(targets):
        current = Message._base_manager.get(pk=identifier)
        initial = copy.copy(current)
        initial.message = 'Historical original ' + identifier
        association = str(current.sport_association_id)
        actor = source['ids']['foreign_owner' if association == source['ids']['foreign_association'] else 'owner']
        events = []
        for action, changes in ((0, model_instance_diff(None, initial)),
                                (1, model_instance_diff(initial, current))):
            events.append(LogEntry(content_type=content_type, object_pk=identifier,
                object_repr=f'Message object ({identifier})', action=action,
                changes=changes, actor_id=actor, actor_email='historical-owner@example.test',
                cid=f'message-mutations-history-{offset}-{action}', remote_addr='192.0.2.10',
                timestamp=datetime(2026, 10, 23, 9, action, tzinfo=timezone.utc)))
        LogEntry.objects.bulk_create(events)
        AuditLogIndex.objects.bulk_create([AuditLogIndex(
            audit_log_index_id=f'a1000000-0000-4000-8000-{722000 + offset * 2 + event.action:012d}',
            log_entry_id=event.pk, sport_association_id=association, resolution_path='direct',
            created_at=event.timestamp) for event in events])


def seed_fixture(apps, private_work, raw):
    """Reuse identity/session/cache seed, then add actual mutation targets/references.

    Caller must own fixture_context: it captures state/cache/sequences before any
    setup and handles partial failures. No permissions/restore services are mocked.
    """
    from auditlog.context import disable_auditlog
    from django.db import transaction
    from communications.models import Message, MessageTransaction, AutomationWorkflow
    require_private_database()
    source = contract.load_source()
    ids = source['ids']
    private_work = Path(private_work)
    with disable_auditlog(), transaction.atomic():
        fixture = scopes.seed_fixture(apps, private_work, raw)
        for offset, label in enumerate(DELETE_LABELS):
            identifier = ids[label]
            contract.require(not Message._base_manager.filter(pk=identifier).exists(), 'fixture.target-collision')
            association = ids['foreign_association' if label.startswith('delete_foreign_') else 'own_association']
            row = Message(message_id=identifier, sport_association_id=association,
                          type='LEGACY_SMS' if label == 'delete_legacy' else 'INSIDE_APP' if label == 'delete_post' else 'EMAIL',
                          message='Messaggio con consegne '+label, subject=None if label == 'delete_post' else 'Oggetto')
            Message._base_manager.bulk_create([row])
            Message._base_manager.filter(pk=identifier).update(created_at='2026-10-23T09:00:00Z')
            fixture['created'].append((Message, identifier))
            for number in range(2):
                delivery_id = f'a1000000-0000-4000-8000-{721000 + offset * 2 + number:012d}'
                contract.require(not MessageTransaction._base_manager.filter(pk=delivery_id).exists(), 'fixture.delivery-collision')
                delivery = MessageTransaction(message_transaction_id=delivery_id, message_id=identifier,
                    recipient='first@example.test', delivered_on='2026-10-23T10:01:00Z',
                    opened_on='2026-10-23T10:02:00Z', clicked_on='2026-10-23T10:03:00Z',
                    bounced_on='2026-10-23T10:04:00Z', bounced_reason='Synthetic reason',
                    bounced_type='Permanent', bounced_status='5.1.1')
                MessageTransaction._base_manager.bulk_create([delivery])
                MessageTransaction._base_manager.filter(pk=delivery_id).update(sent_on='2026-10-23T10:00:00Z')
                fixture['created'].append((MessageTransaction, delivery_id))
        _seed_target_history(source)
        # Workflows embed independent content; they do not dereference saved Message IDs.
        contract.require(not AutomationWorkflow._base_manager.filter(pk=ids['workflow']).exists(), 'fixture.workflow-collision')
        workflow = AutomationWorkflow(automation_workflow_id=ids['workflow'], sport_association_id=ids['own_association'],
            name='Mutation reference sentinel', enabled=False,
            automation_tree=[{'id': 'message', 'value': 'email', 'data': {
                'content': 'Messaggio con consegne delete_email', 'subject': 'Oggetto', 'recipients': 'athletes'}}])
        AutomationWorkflow._base_manager.bulk_create([workflow])
        AutomationWorkflow._base_manager.filter(pk=workflow.pk).update(
            created_at='2026-10-23T09:00:00Z', updated_at='2026-10-23T09:00:00Z')
        fixture['created'].append((AutomationWorkflow, workflow.pk))
        fixture['authored'] = source
        fixture['runtime'] = runtime_manifest(source, fixture['tokens'], fixture['sessions'])
        fixture['runtime_path'] = private_work / 'message_mutations-runtime.json'
        fixture['runtime_path'].write_text(json.dumps(fixture['runtime'], ensure_ascii=True))
        fixture['runtime_path'].chmod(0o600)
        fixture['baseline_state'] = scopes.snapshot_state(apps)
        from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
        fixture['baseline_cache'] = snapshot_historical_read_cache(raw)
    return fixture


@contextmanager
def fixture_context(apps, private_work, raw, *, cleanup_receipt=None):
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from communications.models import Message
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    require_private_database()
    private_work = Path(private_work)
    contract.require(not private_work.exists(), 'fixture.new-private-directory-required')
    before, cache_before, sequences_before = scopes.snapshot_state(apps), snapshot_historical_read_cache(raw), sequence_snapshot()
    original_audit = [row['id'] for row in before[contract.AUDIT]]
    fixture = None
    receipt = cleanup_receipt if cleanup_receipt is not None else {}
    receipt.update(verified=False, results={})
    try:
        fixture = seed_fixture(apps, private_work, raw)
        yield fixture
    finally:
        def rows():
            with disable_auditlog():
                if fixture is not None:
                    # Include generated CREATEs even when a request fails an assertion.
                    identifiers = [contract.scalar(row['message_id']) for row in before[contract.MESSAGE]]
                    Message._base_manager.exclude(pk__in=identifiers).filter(
                        sport_association_id__in=[contract.load_source()['ids'][label]
                                                 for label in ('own_association', 'foreign_association')]).delete()
                    shared.delete_created(fixture)
                # Includes indexes via LogEntry->AuditLogIndex ORM cascade.
                LogEntry.objects.exclude(pk__in=original_audit).delete()
            shared.assert_state_unchanged(before, scopes.snapshot_state(apps))
            return True
        # Independent cleanup steps are all attempted; receipt cannot claim success on partial cleanup.
        receipt.update(scopes.cleanup_steps((
            ('rows', rows), ('cache', lambda: scopes.restore_cache(raw, cache_before)),
            ('sequences', lambda: restore_sequences(sequences_before)),
            ('private_fixture', lambda: scopes.remove_private_fixture(private_work)),
        )))
        contract.require(receipt['verified'], 'cleanup.fixture-incomplete')


def _literal(cursor, value):
    cursor.execute('SELECT quote_literal(%s)', [str(value)])
    return cursor.fetchone()[0]


@contextmanager
def failure_control(case, fixture):
    """Real cross-process PostgreSQL trigger rejection, removed exactly in finally."""
    from django.db import connection
    if not case.get('fault'):
        yield
        return
    require_private_database()
    fault = case['fault']
    suffix = uuid4().hex
    name = 'message_mutations_' + suffix
    quoted = connection.ops.quote_name(name)
    table, operation = {
        'message-insert': ('communications_message', 'INSERT'),
        'delivery-delete': ('communications_messagetransaction', 'DELETE'),
        'message-delete': ('communications_message', 'DELETE'),
        'audit-insert': ('auditlog_logentry', 'INSERT'),
        'index-insert': ('application_auditlogindex', 'INSERT'),
    }[fault]
    with connection.cursor() as cursor:
        cid = _literal(cursor, case['name'])
        if fault == 'message-insert':
            predicate = 'NEW.message = ' + _literal(cursor, case['body']['message'])
        elif fault in ('message-delete', 'delivery-delete'):
            predicate = 'OLD.message_id = ' + _literal(cursor, fixture['authored']['ids']['fault_delete']) + '::uuid'
        elif fault == 'audit-insert':
            predicate = 'NEW.cid = ' + cid
        else:
            predicate = 'EXISTS (SELECT 1 FROM auditlog_logentry WHERE id = NEW.log_entry_id AND cid = ' + cid + ')'
        cursor.execute(f'CREATE FUNCTION {quoted}() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN '
                       f"IF {predicate} THEN RAISE EXCEPTION 'synthetic message mutation rejection'; END IF; "
                       f"RETURN {'OLD' if operation == 'DELETE' else 'NEW'}; END $$")
    try:
        with connection.cursor() as cursor:
            cursor.execute(f'CREATE TRIGGER {quoted} BEFORE {operation} ON {connection.ops.quote_name(table)} '
                           f'FOR EACH ROW EXECUTE FUNCTION {quoted}()')
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TRIGGER IF EXISTS {quoted} ON {connection.ops.quote_name(table)}')
            cursor.execute(f'DROP FUNCTION IF EXISTS {quoted}()')
            cursor.execute('SELECT count(*) FROM pg_proc WHERE proname = %s', [name])
            contract.require(cursor.fetchone()[0] == 0, 'cleanup.trigger-function')


@contextmanager
def admission_control(case, fixture):
    from django.conf import settings
    from instance.models import DataRestore
    mode = case.get('admission')
    if not mode:
        yield
        return
    path = settings.DATA_RESTORE_LOCK_PATH
    contract.require(bool(path) and Path(path).exists(), 'admission.shared-lock-required')
    if mode == 'locked':
        with open(path, 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    else:
        contract.require(mode == 'pending' and not DataRestore.objects.filter(state__in=DataRestore.ACTIVE).exists(), 'admission.no-existing-restore')
        row = DataRestore(owner_id=fixture['authored']['ids']['owner'],
                          association_id=fixture['authored']['ids']['own_association'],
                          state='queued', sha256='0'*64)
        DataRestore.objects.bulk_create([row])
        try:
            yield
        finally:
            DataRestore.objects.filter(pk=row.pk).delete()
            contract.require(not DataRestore.objects.filter(pk=row.pk).exists(), 'cleanup.pending-restore')


def run_workload(label, backend, base_url, fixture, apps, raw, observer, *, remote_addr,
                 process_guard, observations_path=None, admission_controller=None):
    """Actual HTTP requests, exact complete state/cache effects, and ASGI attribution.

    Parent owns a guard tied to the actual process and independently measured proxy
    client address; a response header alone is never attribution evidence.
    A packaged adapter may supply its actual Linux lock transport. The default
    remains the host-side controller; expectations and effect checks are shared.
    """
    from TESTS.adapters.django.historical_read_cache import snapshot_historical_read_cache
    require_private_database()
    if admission_controller is None:
        admission_controller = admission_control
    contract.require(callable(admission_controller), 'runtime.admission-controller')
    contract.validate_runtime(fixture['runtime'])
    contract.require(backend in ('django', 'go') and callable(process_guard), 'runtime.explicit-backend-guard')
    initial_guard = copy.deepcopy(process_guard())
    contract.require(isinstance(initial_guard, dict) and initial_guard.get('running') is True
                     and type(initial_guard.get('pid')) is int and initial_guard['pid'] > 0
                     and initial_guard.get('instance') is not None, 'runtime.live-process')
    shared.assert_state_unchanged(fixture['baseline_state'], scopes.snapshot_state(apps))
    shared.assert_cache_unchanged(fixture['baseline_cache'], snapshot_historical_read_cache(raw), fixture['contrasts'])
    result = {'label': label, 'backend': backend, 'success': False, 'cases': [], 'normalized_effects': []}
    observation_file = None
    if observations_path is not None:
        observation_file = Path(observations_path).open('x', encoding='utf-8')
        Path(observations_path).chmod(0o600)
    try:
        for case in fixture['runtime']['cases']:
            with failure_control(case, fixture), admission_controller(case, fixture):
                before, cache_before = scopes.snapshot_state(apps), snapshot_historical_read_cache(raw)
                sequences_before = sequence_snapshot()
                cursor, started = observer.cursor(), time.time()
                status, request_error = None, None
                contract.require(process_guard() == initial_guard, 'runtime.process-changed')
                try:
                    status = contract.assert_case(case, base_url)
                except BaseException as error:
                    request_error = error
                    status = getattr(error, 'actual_status', None)
                finished = time.time()
                # Capture all domains even on HTTP validation failure, before raising it.
                after, cache_after = scopes.snapshot_state(apps), snapshot_historical_read_cache(raw)
                sequences_after, observed = sequence_snapshot(), observer.delta(cursor)
                if observation_file is not None:
                    observation_file.write(json.dumps({'case': case['name'], 'status': status,
                        'before': before, 'after': after, 'cache_before': private_cache(cache_before),
                        'cache_after': private_cache(cache_after), 'sequences_before': sequences_before,
                        'sequences_after': sequences_after, 'observed': observed}, ensure_ascii=True)+'\n')
                    observation_file.flush()
                contract.require(process_guard() == initial_guard, 'runtime.process-changed')
                # An attribution failure wins even if fallback returned a matching response.
                shared.assert_attribution(backend, case['method'], case['path'], status, observed, case['name'])
                shared.assert_cache_unchanged(cache_before, cache_after, fixture['contrasts'])
                effect = contract.assert_effect(case, before, after, started=started, finished=finished,
                                                remote_addr=remote_addr, cid=case['name'])
                if request_error is not None:
                    raise request_error
                result['normalized_effects'].append(effect)
                result['cases'].append({'case': case['name'], 'status': status,
                    'state_before_sha256': shared.digest(before), 'state_after_sha256': shared.digest(after),
                    'cache_sha256': cache_after.digest, 'models': len(after),
                    'checks': {'response': True, 'state': True, 'audit': True, 'cache': True, 'attribution': True}})
        result['success'] = True
        return result
    finally:
        if observation_file is not None:
            observation_file.close()
