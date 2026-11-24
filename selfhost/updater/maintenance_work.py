"""Checkpoint accepted work while the lifecycle CLI holds its exclusive lease."""
import sys
import uuid
from pathlib import Path

from common import atomic_write
from job_handoff import PRIORITIES, QUEUES, _items, compose_broker, compose_store, handoff_steps


OUTSTANDING = "('pending', 'held', 'staged', 'running', 'blocked')"


def checkpoint(store, broker):
    owner, = store.rows("SELECT runtime, generation FROM public.assozeta_go_job_ownership WHERE singleton=1")
    runtime, generation = owner['runtime'], owner['generation']
    handoff_steps(store, broker)['discover']({
        'id': str(uuid.uuid4()), 'previous_runtime': runtime, 'source_generation': generation, 'target': runtime,
    })
    count = int(store.scalar(f"SELECT count(*) FROM public.assozeta_handoff_work WHERE state IN {OUTSTANDING}"))
    print(f'format=2\nruntime={runtime}\nruntime_generation={generation}\nwork_checkpoint=ledger\nwork_outstanding={count}')


def require_empty(store, broker):
    busy = store.scalar(f"""SELECT EXISTS(SELECT FROM public.assozeta_handoff_work WHERE state IN {OUTSTANDING})
        OR EXISTS(SELECT FROM public.assozeta_go_jobs WHERE state IN ('pending','running'))""")
    if busy == 't' or next(_items(broker, 1), None) is not None:
        raise SystemExit('restore --no-backup refused: outstanding work requires a safety backup')


def preserve_broker(broker, directory, restore=False):
    # Only delivery keys survive a rollback. Caches belong to the discarded database.
    keys = [queue + suffix for queue in QUEUES for suffix in PRIORITIES] + ['unacked', 'unacked_index']
    if restore:
        broker.call('FLUSHALL')
    for key in keys:
        path = Path(directory) / key.encode().hex()
        if restore:
            if path.is_file():
                broker.call('RESTORE', key, 0, path.read_bytes(), 'REPLACE')
        else:
            dump = broker.call('DUMP', key)
            if dump is not None:
                atomic_write(path, dump)


def recover_work(store, broker, target, live_generation):
    archived, = store.rows("SELECT runtime,generation FROM public.assozeta_go_job_ownership WHERE singleton=1")
    generation = max(int(live_generation), archived['generation']) + 1
    record = {'id': str(uuid.uuid4()), 'target': target, 'previous_runtime': archived['runtime'],
              'source_generation': generation - 1}
    steps = handoff_steps(store, broker)
    # No consumer from the archived database can still own a lease. An external
    # effect whose completion was never recorded needs operator reconciliation.
    store.execute(f"""BEGIN;
        UPDATE public.assozeta_handoff_effects e SET state='ambiguous' FROM public.assozeta_handoff_work w
            WHERE e.task_id=w.task_id AND e.state='intent' AND w.state IN {OUTSTANDING};
        UPDATE public.assozeta_handoff_work w SET state='blocked',block_reason='ambiguous_effect'
            WHERE w.state IN {OUTSTANDING} AND EXISTS (SELECT FROM public.assozeta_handoff_effects e
                WHERE e.task_id=w.task_id AND e.state='ambiguous');
        UPDATE public.assozeta_handoff_attempts SET outcome='released', finished_at=clock_timestamp() WHERE outcome='running';
        UPDATE public.assozeta_go_jobs SET state='pending',lease_owner='',lease_until=NULL,
            lease_token='00000000-0000-0000-0000-000000000000' WHERE state='running';
        UPDATE public.assozeta_go_job_ownership SET runtime=:'target', generation=:'generation', operation_id=:'op',
            committed_at=clock_timestamp(), admission='open', admission_operation_id=NULL WHERE singleton=1;
        UPDATE public.assozeta_handoff_work SET owner_runtime=:'target', owner_generation=:'generation',
            operation_id=:'op', state=CASE WHEN state='blocked' THEN state ELSE 'pending' END,
            updated_at=clock_timestamp() WHERE state IN {OUTSTANDING};
        DELETE FROM public.assozeta_handoff_consumers;
        DELETE FROM public.assozeta_scheduler_lease;
        COMMIT""", target=target, generation=generation, op=record['id'])
    for problem in steps['preflight'](record)['blockers']:
        store.execute("""UPDATE public.assozeta_handoff_work SET state='blocked', block_reason=:'reason'
            WHERE task_id=:'task_id' AND operation_id=:'op'""", op=record['id'], **problem)
    store.execute("""INSERT INTO public.assozeta_handoff_transfers
        (operation_id,task_id,source_runtime,source_generation,target_runtime,target_generation,source_location)
        SELECT operation_id,task_id,:'source',:'archived_generation',owner_runtime,owner_generation,
            CASE WHEN transport='sql' THEN 'sql' ELSE 'ledger' END FROM public.assozeta_handoff_work
        WHERE operation_id=:'op' AND state='pending'""", source=archived['runtime'],
        archived_generation=archived['generation'], op=record['id'])
    steps['activate'](record)
    blocked = int(store.scalar("SELECT count(*) FROM public.assozeta_handoff_work WHERE operation_id=:'op' AND state='blocked'",
                               op=record['id']))
    if blocked:
        print(f'restore retained {blocked} blocked jobs for reconciliation', file=sys.stderr)


def main(action, root, env_file, *args):
    if action == 'swap-journal':
        from restore_swap import journal
        return journal(root, env_file, *args)
    store, broker = compose_store(root, env_file), compose_broker(root, env_file)
    if action == 'checkpoint':
        checkpoint(store, broker)
    elif action == 'require-empty':
        require_empty(store, broker)
    elif action == 'recover':
        recover_work(store, broker, *args)
    elif action in ('save-broker', 'restore-broker'):
        preserve_broker(broker, args[0], restore=action == 'restore-broker')
    else:
        raise SystemExit('unknown maintenance operation')


if __name__ == '__main__':
    main(*sys.argv[1:])
