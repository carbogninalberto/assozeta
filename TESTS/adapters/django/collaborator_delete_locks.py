"""Observed PostgreSQL barriers for bounded collaborator-delete serialization.

Every barrier below is a real row lock in the disposable current-schema
database. Workers are real HTTP requests or direct fixture reparent writes;
waiting is accepted only after pg_stat_activity and pg_locks report an
ungranted lock tied to the blocker PID. This helper does not claim complete
collaborator-delete, cascade, historical-schema, or Go parity.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import copy
from pathlib import Path
from uuid import uuid4

from TESTS.http_client import Client, poll


WAIT_SECONDS = 10
WORKER_SECONDS = 25


def run(*, base, owner, foreign, association, person, seed, snapshot,
        cache_snapshot, models, report):
    from auditlog.models import LogEntry
    from django.db import connection
    from application.models import User
    from application.models.audit_models import AuditLogIndex
    from application.models.user_models import CollaborationInvites
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.adapters.django.impersonation_effects import assert_retained_rows

    assert connection.vendor == 'postgresql', 'Observed row-lock tests require PostgreSQL'
    assert connection.settings_dict['NAME'] == 'assozeta_contract_accounts'
    assert Path(connection.settings_dict['HOST']).resolve().parent == Path('/tmp').resolve()
    assert Path(connection.settings_dict['HOST']).name.startswith('az-accounts-')
    assert len(models) == 128, ('Expected all 128 current-schema managed models', len(models))
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), to_regclass(%s), to_regclass(%s)',
                       ['bakney_user', 'application_collaborationinvites'])
        database, user_table, invite_table = cursor.fetchone()
        assert database == 'assozeta_contract_accounts'
        assert (user_table, invite_table) == ('bakney_user', 'application_collaborationinvites')

    access = AccessToken()
    access['user_id'] = str(owner.pk)
    token = str(access)
    report['lock_cases'] = []

    def cached_bytes():
        return cache_snapshot()

    def worker_result(label, path):
        try:
            status, body = Client(base, token, timeout=20).request(
                'DELETE', path, headers={'X-Correlation-ID': label, 'Accept-Language': 'en'},
            )
            return {'label': label, 'status': status, 'body': body, 'exception': None}
        except BaseException as error:
            return {'label': label, 'status': None, 'body': None,
                    'exception': {'type': type(error).__name__, 'message': str(error)[:500]}}

    @contextmanager
    def row_lock(label, table, key, identifier, *, new_owner=None):
        blocker = connection.copy(alias='collaborator_delete_lock_' + uuid4().hex)
        blocker.ensure_connection()
        blocker.set_autocommit(False)
        evidence = {'name': label, 'table': table, 'row': str(identifier),
                    'backend_pid': None, 'released': False, 'reparent_committed': False}
        try:
            with blocker.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid()')
                evidence['backend_pid'] = cursor.fetchone()[0]
                cursor.execute('SET LOCAL lock_timeout = %s', [f'{WAIT_SECONDS}s'])
                cursor.execute(
                    f'SELECT {key} FROM {table} WHERE {key} = %s FOR UPDATE',
                    [identifier],
                )
                assert cursor.fetchone() == (identifier,), ('Locked fixture row vanished', label)
                if new_owner is not None:
                    assert table == 'bakney_user' and key == 'user_id'
                    cursor.execute(
                        'UPDATE bakney_user SET connected_user_id = %s WHERE user_id = %s',
                        [new_owner, identifier],
                    )
                    assert cursor.rowcount == 1
            yield evidence
            if new_owner is not None:
                blocker.commit()
                evidence['reparent_committed'] = True
        finally:
            try:
                blocker.rollback()
            finally:
                blocker.close()
                evidence['released'] = True

    def waiting_rows(blocker_pid):
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_stat_clear_snapshot()')
            cursor.execute("""
                SELECT activity.pid, activity.wait_event_type, activity.wait_event,
                       activity.query, pg_blocking_pids(activity.pid),
                       ARRAY(
                           SELECT locks.mode
                           FROM pg_locks AS locks
                           WHERE locks.pid = activity.pid AND NOT locks.granted
                           ORDER BY locks.mode
                       )
                FROM pg_stat_activity AS activity
                WHERE activity.datname = current_database()
                  AND activity.pid <> pg_backend_pid()
                  AND activity.wait_event_type = 'Lock'
            """)
            rows = [{
                'pid': pid,
                'wait_event_type': wait_type,
                'wait_event': wait_event,
                'query': (query or '')[:500],
                'blocking_pids': blockers,
                'ungranted_modes': modes,
            } for pid, wait_type, wait_event, query, blockers, modes in cursor.fetchall()]
        # PostgreSQL may queue a second waiter behind the first waiter, so its
        # direct blocker is not necessarily the transaction holding the row.
        chains = {blocker_pid: [blocker_pid]}
        pending = list(rows)
        while pending:
            found = []
            for row in pending:
                parent = next((pid for pid in row['blocking_pids'] if pid in chains), None)
                if parent is not None:
                    row['blocking_chain'] = [row['pid'], *chains[parent]]
                    chains[row['pid']] = row['blocking_chain']
                    found.append(row)
            if not found:
                break
            pending = [row for row in pending if row not in found]
        return [row for row in rows if row.get('blocking_chain', [])[-1:] == [blocker_pid]]

    def await_wait(blocker, sql_prefix, table, count=1):
        def matching():
            rows = waiting_rows(blocker['backend_pid'])
            ready = [
                row for row in rows
                if row['wait_event_type'] == 'Lock'
                and row['query'].lstrip().startswith(sql_prefix)
                and table in row['query']
                and row['blocking_chain'][-1] == blocker['backend_pid']
                and row['ungranted_modes']
            ]
            return ready

        rows = poll(
            matching,
            lambda observed: len(observed) >= count,
            timeout=WAIT_SECONDS,
            interval=.01,
        )
        assert len({row['pid'] for row in rows}) >= count, ('Expected independent waiters', rows)
        return rows

    def take(future, entry, label):
        try:
            result = future.result(timeout=WORKER_SECONDS)
        except BaseException as error:
            entry['worker_exceptions'].append({
                'label': label,
                'type': type(error).__name__,
                'message': str(error)[:500],
            })
            raise
        if result['exception'] is not None:
            entry['worker_exceptions'].append({'label': label, **result['exception']})
            raise AssertionError(f'{label} worker failed: {result["exception"]}')
        return result

    def verify_effect(entry, before, before_cache, audit_cids,
                      removed_users=(), removed_invites=(), updates=None,
                      expected_audit_objects=()):
        after = snapshot()
        audits = list(LogEntry.objects.filter(cid__in=audit_cids).order_by('pk').values())
        audit_ids = [row['id'] for row in audits]
        assert sorted(row['object_pk'] for row in audits) == sorted(expected_audit_objects), (
            'Unexpected audit objects', entry['name'], audits,
        )
        for audit in audits:
            assert audit['action'] == 2
            assert str(audit['actor_id']) == str(owner.pk)
        indexes = list(AuditLogIndex.objects.filter(log_entry_id__in=audit_ids).values())
        assert len(indexes) == len(audits)
        assert all(row['sport_association_id'] == association.pk for row in indexes)
        assert all(row['resolution_path'] == 'actor.sport_association' for row in indexes)

        removed = {
            User._meta.label: set(removed_users),
            CollaborationInvites._meta.label: set(removed_invites),
        }
        updates = updates or {}
        for model in models:
            label = model._meta.label
            key = model._meta.pk.attname
            if model is LogEntry:
                assert_retained_rows(before[label], after[label], key=key, created=audit_ids)
            elif model is AuditLogIndex:
                assert_retained_rows(
                    before[label], after[label], key=key,
                    created=[row[key] for row in indexes],
                )
            else:
                expected = [
                    copy.deepcopy(row) for row in before[label]
                    if row[key] not in removed.get(label, set())
                ]
                for row in expected:
                    if row[key] in updates.get(label, {}):
                        row.update(updates[label][row[key]])
                assert after[label] == expected, (
                    'Unexpected durable effects', entry['name'], label,
                )
        assert after == snapshot()
        assert cached_bytes() == before_cache, ('Unexpected cache bytes', entry['name'])
        entry.update(
            managed_models_verified=len(models),
            cache_bytes_unchanged=True,
            audit_count=len(audits),
            audit_objects=[row['object_pk'] for row in audits],
            audit_cids=sorted({row['cid'] for row in audits}),
            audit_indexes=len(indexes),
        )

    def execute_case(entry, workers, body):
        executor = ThreadPoolExecutor(max_workers=workers)
        futures = []
        try:
            body(executor, futures)
            assert futures and all(future.done() for future in futures)
            for future in futures:
                future.result(timeout=0)
        finally:
            for future in futures:
                if future.done():
                    continue
                try:
                    result = future.result(timeout=WAIT_SECONDS)
                    if result.get('exception') is not None:
                        entry['worker_exceptions'].append({
                            'label': result['label'], **result['exception'],
                        })
                except BaseException as error:
                    entry['worker_exceptions'].append({
                        'label': 'drain',
                        'type': type(error).__name__,
                        'message': str(error)[:500],
                    })
            stopped = all(future.done() for future in futures)
            executor.shutdown(wait=stopped, cancel_futures=not stopped)
            entry['workers_stopped'] = stopped
            assert stopped, ('Workers did not stop after lock release', entry['name'])

    cases = []

    def target_lock():
        target = person(715101, 3, connected_user=owner)
        label = 'target-row-lock'
        entry = {'name': label, 'success': False, 'worker_exceptions': [],
                 'observed_waits': [], 'blockers': []}
        report['lock_cases'].append(entry)
        before, before_cache = snapshot(), cached_bytes()

        def body(executor, futures):
            with row_lock(label, 'bakney_user', 'user_id', target.pk) as blocker:
                entry['blockers'].append(blocker)
                futures.append(executor.submit(
                    worker_result, label, f'/collaborators/{target.pk}/delete',
                ))
                entry['observed_waits'] = await_wait(blocker, 'SELECT', 'bakney_user')
                assert User._base_manager.filter(pk=target.pk).exists()
                assert not LogEntry.objects.filter(cid=label).exists()
            deleted = take(futures[0], entry, 'delete')
            assert (deleted['status'], deleted['body']) == (
                200, {'msg': 'collaborator deleted.'},
            )
            verify_effect(
                entry, before, before_cache, [label],
                removed_users=[target.pk],
                expected_audit_objects=[str(target.pk)],
            )
            entry['success'] = True

        execute_case(entry, 1, body)
        cases.append(entry)
        print(f'{label}: observed target wait, HTTP200, one audit', flush=True)

    def target_reparent():
        target = person(715102, 3, connected_user=owner)
        label = 'reparent-before-scoped-select'
        entry = {'name': label, 'success': False, 'worker_exceptions': [],
                 'observed_waits': [], 'blockers': []}
        report['lock_cases'].append(entry)
        before, before_cache = snapshot(), cached_bytes()

        def body(executor, futures):
            with row_lock(
                label, 'bakney_user', 'user_id', target.pk, new_owner=foreign.pk,
            ) as blocker:
                entry['blockers'].append(blocker)
                futures.append(executor.submit(
                    worker_result, label, f'/collaborators/{target.pk}/delete',
                ))
                entry['observed_waits'].extend(await_wait(
                    blocker, 'SELECT', 'bakney_user', count=1,
                ))
            deleted = take(futures[0], entry, 'delete')
            assert blocker['reparent_committed']
            assert (deleted['status'], deleted['body']) == (400, ['Collaborator not found']), deleted
            assert User._base_manager.filter(pk=target.pk).exists()
            assert User._base_manager.get(pk=target.pk).connected_user_id == foreign.pk
            assert User._base_manager.filter(pk=foreign.pk).exists()
            verify_effect(
                entry, before, before_cache, [label],
                updates={User._meta.label: {
                    target.pk: {'connected_user_id': foreign.pk},
                }},
            )
            entry['missing_target'] = {
                'status': deleted['status'],
                'body': deleted['body'],
                'foreign_record_preserved': True,
            }
            entry['success'] = True

        execute_case(entry, 1, body)
        cases.append(entry)
        print(f'{label}: observed reparent then HTTP400, foreign record preserved', flush=True)

    def duplicate_delete():
        target = person(715103, 3, connected_user=owner)
        labels = ('duplicate-delete-first', 'duplicate-delete-second')
        entry = {'name': 'duplicate-delete-serialization', 'success': False,
                 'worker_exceptions': [], 'observed_waits': [], 'blockers': []}
        report['lock_cases'].append(entry)
        before, before_cache = snapshot(), cached_bytes()

        def body(executor, futures):
            with row_lock(entry['name'], 'bakney_user', 'user_id', owner.pk) as blocker:
                entry['blockers'].append(blocker)
                futures.append(executor.submit(
                    worker_result, labels[0], f'/collaborators/{target.pk}/delete',
                ))
                entry['observed_waits'].extend(await_wait(
                    blocker, 'SELECT', 'bakney_user', count=1,
                ))
                futures.append(executor.submit(
                    worker_result, labels[1], f'/collaborators/{target.pk}/delete',
                ))
                entry['observed_waits'].extend(await_wait(
                    blocker, 'SELECT', 'bakney_user', count=2,
                ))
            results = [
                take(futures[0], entry, labels[0]),
                take(futures[1], entry, labels[1]),
            ]
            assert sorted(result['status'] for result in results) == [200, 400], results
            winner = next(result for result in results if result['status'] == 200)
            loser = next(result for result in results if result['status'] == 400)
            assert winner['body'] == {'msg': 'collaborator deleted.'}
            assert loser['body'] == ['Collaborator not found']
            verify_effect(
                entry, before, before_cache, labels,
                removed_users=[target.pk],
                expected_audit_objects=[str(target.pk)],
            )
            assert entry['audit_cids'] == [winner['label']]
            entry['responses'] = [
                {'label': result['label'], 'status': result['status'],
                 'body': result['body']} for result in results
            ]
            entry['winner'] = winner['label']
            entry['loser'] = loser['label']
            entry['success'] = True

        execute_case(entry, 2, body)
        cases.append(entry)
        print('duplicate-delete-serialization: observed two owner waiters, HTTP200+400, one audit', flush=True)

    def invitation_lock():
        invite = seed(
            CollaborationInvites, 716104, user=owner,
            email='delete-lock-invite@example.test', accepted=False,
            token='synthetic-delete-lock-invite',
        )
        label = 'invitation-row-lock'
        entry = {'name': label, 'success': False, 'worker_exceptions': [],
                 'observed_waits': [], 'blockers': []}
        report['lock_cases'].append(entry)
        before, before_cache = snapshot(), cached_bytes()

        def body(executor, futures):
            with row_lock(
                label, 'application_collaborationinvites',
                'collaboration_invite_id', invite.pk,
            ) as blocker:
                entry['blockers'].append(blocker)
                futures.append(executor.submit(
                    worker_result, label, f'/collaborators/{invite.pk}/delete',
                ))
                entry['observed_waits'] = await_wait(
                    blocker, 'SELECT', 'application_collaborationinvites',
                )
                assert CollaborationInvites._base_manager.filter(pk=invite.pk).exists()
            deleted = take(futures[0], entry, 'delete')
            assert (deleted['status'], deleted['body']) == (
                200, {'msg': 'collaborator deleted.'},
            )
            verify_effect(
                entry, before, before_cache, [label],
                removed_invites=[invite.pk],
                expected_audit_objects=[str(invite.pk)],
            )
            entry['success'] = True

        execute_case(entry, 1, body)
        cases.append(entry)
        print(f'{label}: observed invitation wait, HTTP200, one audit', flush=True)

    def accepted_invitation_reparent():
        target = person(715105, 3, connected_user=owner)
        invite = seed(
            CollaborationInvites, 716105, user=owner, email=target.email,
            accepted=True, token='synthetic-delete-lock-accepted',
        )
        label = 'accepted-invite-reparent'
        entry = {'name': label, 'success': False, 'worker_exceptions': [],
                 'observed_waits': [], 'blockers': []}
        report['lock_cases'].append(entry)
        before, before_cache = snapshot(), cached_bytes()

        def body(executor, futures):
            with row_lock(
                label, 'bakney_user', 'user_id', target.pk, new_owner=foreign.pk,
            ) as blocker:
                entry['blockers'].append(blocker)
                futures.append(executor.submit(
                    worker_result, label, f'/collaborators/{invite.pk}/delete',
                ))
                entry['observed_waits'].extend(await_wait(
                    blocker, 'SELECT', 'bakney_user',
                ))
            deleted = take(futures[0], entry, 'delete')
            assert blocker['reparent_committed']
            assert (deleted['status'], deleted['body']) == (
                200, {'msg': 'collaborator deleted.'},
            )
            assert not CollaborationInvites._base_manager.filter(pk=invite.pk).exists()
            assert User._base_manager.get(pk=target.pk).connected_user_id == foreign.pk
            assert User._base_manager.filter(pk=foreign.pk).exists()
            verify_effect(
                entry, before, before_cache, [label],
                removed_invites=[invite.pk],
                updates={User._meta.label: {
                    target.pk: {'connected_user_id': foreign.pk},
                }},
                expected_audit_objects=[str(invite.pk)],
            )
            entry['foreign_record_preserved'] = True
            entry['success'] = True

        execute_case(entry, 1, body)
        cases.append(entry)
        print(f'{label}: observed reparent, deleted invite, preserved foreign record', flush=True)

    try:
        target_lock()
        target_reparent()
        duplicate_delete()
        invitation_lock()
        accepted_invitation_reparent()
        assert len(cases) == 5
        assert all(case['success'] and case['workers_stopped']
                   and not case['worker_exceptions'] for case in cases)
        for case in cases:
            assert all(blocker['released'] for blocker in case['blockers'])
            assert case['observed_waits']
            assert all(wait['wait_event_type'] == 'Lock'
                       and wait['ungranted_modes'] for wait in case['observed_waits'])
        report['lock_case_count'] = len(cases)
        report['lock_managed_models_verified'] = len(models)
        report['lock_scope'] = (
            'Observed private-PostgreSQL row waits for target and invitation '
            'deletion, reparent rechecks, and duplicate serialization; '
            'cascade, historical-schema, and Go parity remain unverified'
        )
    finally:
        for case in report['lock_cases']:
            case.setdefault('worker_exceptions', [])
            case.setdefault('workers_stopped', False)
            case.setdefault('success', False)
