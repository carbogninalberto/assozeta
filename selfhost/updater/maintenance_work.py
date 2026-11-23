"""Checkpoint accepted work while the lifecycle CLI holds its exclusive lease."""
import sys
import uuid

from job_handoff import compose_broker, compose_store, handoff_steps


OUTSTANDING = "('pending', 'held', 'staged', 'running', 'blocked')"


def checkpoint(store, broker):
    owner, = store.rows("SELECT runtime, generation FROM public.assozeta_go_job_ownership WHERE singleton=1")
    runtime, generation = owner['runtime'], owner['generation']
    handoff_steps(store, broker)['discover']({
        'id': str(uuid.uuid4()), 'previous_runtime': runtime, 'source_generation': generation, 'target': runtime,
    })
    count = int(store.scalar(f"SELECT count(*) FROM public.assozeta_handoff_work WHERE state IN {OUTSTANDING}"))
    print(f'format=2\nruntime={runtime}\nruntime_generation={generation}\nwork_checkpoint=ledger\nwork_outstanding={count}')


if __name__ == '__main__':
    action, root, env_file = sys.argv[1:]
    if action != 'checkpoint':
        raise SystemExit('unknown maintenance operation')
    checkpoint(compose_store(root, env_file), compose_broker(root, env_file))
