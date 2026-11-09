"""Capture the exact Redis broker messages Celery/kombu publish for the Go producer's golden tests.

Run with the migration interpreter from the repository root:
``python -m TESTS.adapters.go.celery_producer_golden``
It starts a private redis-server, publishes each authored task through a
Celery app built like ``BE/core/celery.py`` (same task base, the restore
``before_task_publish`` stamp, broker on Redis DB 0, default queue), records
every Redis command with MONITOR and writes
``BE_GO/internal/celery/testdata/kombu_messages.json``: the authored inputs in
a tagged form, the LPUSH payload bytes and the commands that touched the
broker. The Go test re-encodes the same inputs with the captured random and
clock fields (task id, delivery tag, reply_to, origin, publish stamp,
expiration) and requires byte-identical payloads.
"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/celery/testdata/kombu_messages.json'
PASSWORD = 'synthetic-broker-only'

# Tagged inputs: {"t": kind, "v": value}; dict values keep their order.
U1, U2 = '6f1c1e9e-6a59-4b0e-9a54-3f1f0a7c2d11', '0b6c9f43-2f7d-4c1e-8d0a-5e9b7a3c4f22'
LONG = 'parola ' * 200


def s(v):
    return {'t': 'str', 'v': v}


def tagged_dict(*items):
    return {'t': 'dict', 'v': [[k, v] for k, v in items]}


CASES = [
    {'name': 'subscription-approved', 'task': 'check_workflows_trigger', 'args': [],
     'kwargs': [['trigger_type', s('subscription_approved')], ['sport_association_id', {'t': 'uuid', 'v': U1}],
                ['subscription_id', {'t': 'uuid', 'v': U2}]]},
    {'name': 'scalars-and-text', 'task': 'send_mail_text', 'args': [s("l'iscrizione \"àè\" € \U0001F600\n\t\\"), {'t': 'int', 'v': -42},
                                                                    {'t': 'bool', 'v': True}, {'t': 'bool', 'v': False}, {'t': 'none'}],
     'kwargs': [['data', tagged_dict(['k', {'t': 'list', 'v': [{'t': 'int', 'v': 1}, s('x')]}], ['empty', tagged_dict()],
                                     ['ctrl', s('\x00\x1f\x7f')])], ['n', {'t': 'int', 'v': 9007199254740993}]]},
    {'name': 'single-arg-tuple', 'task': 'one_arg', 'args': [{'t': 'uuid', 'v': U1}], 'kwargs': []},
    {'name': 'nested-levels', 'task': 'nested', 'args': [{'t': 'list', 'v': [{'t': 'list', 'v': [{'t': 'list', 'v': [{'t': 'int', 'v': 1}]}]}]}],
     'kwargs': [['a', tagged_dict(['b', tagged_dict(['c', tagged_dict(['d', {'t': 'int', 'v': 1}])])])]]},
    {'name': 'truncated-repr', 'task': 'long_text', 'args': [s(LONG)], 'kwargs': [['text', s(LONG)], ['after', s('tail')]]},
    {'name': 'quoted-truncation', 'task': 'long_quote', 'args': [], 'kwargs': [['q', s("x'" * 600)]]},
    {'name': 'countdown', 'task': 'later', 'args': [{'t': 'int', 'v': 1}], 'kwargs': [], 'countdown': 90},
    {'name': 'expires', 'task': 'expiring', 'args': [], 'kwargs': [], 'expires_in': 3600},
    # apply_async(args=[...]) with a list: argsrepr is the list's repr.
    {'name': 'list-args', 'task': 'print_document_invoice', 'args': [s(U2), {'t': 'none', 'v': None}, {'t': 'bool', 'v': False}],
     'kwargs': [], 'args_list': True},
    # restore_instance_data.apply_async(args=[id], task_id=id): the task's time limits and the given id.
    {'name': 'task-id-time-limits', 'task': 'instance.restore.execute', 'args': [s('d7000000-0000-4000-8000-000000000101')],
     'kwargs': [], 'args_list': True, 'task_id': 'd7000000-0000-4000-8000-000000000101',
     'task_options': {'time_limit': 3600, 'soft_time_limit': 3300}},
]


def value(tagged):
    kind = tagged['t']
    if kind == 'str':
        return tagged['v']
    if kind == 'int':
        return int(tagged['v'])
    if kind == 'bool':
        return bool(tagged['v'])
    if kind == 'none':
        return None
    if kind == 'uuid':
        return uuid.UUID(tagged['v'])
    if kind == 'list':
        return [value(item) for item in tagged['v']]
    if kind == 'dict':
        return {key: value(item) for key, item in tagged['v']}
    raise ValueError(kind)


def main():
    sys.path.insert(0, str(ROOT / 'BE'))
    from celery import Celery
    import celery.utils.nodenames as nodenames
    # The origin header is gen<pid>@<hostname>; keep the machine name out of the tracked file.
    nodenames.gethostname = lambda: 'golden-host'
    from instance.restore.task_base import CoordinatedTask  # connects stamp_published_task
    import redis

    binary = shutil.which('redis-server')
    assert binary, 'redis-server required'
    work = Path(tempfile.mkdtemp(prefix='az-celery-golden-', dir='/tmp'))
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    server = subprocess.Popen([binary, '--bind', '127.0.0.1', '--port', str(port), '--save', '', '--appendonly', 'no',
                               '--requirepass', PASSWORD, '--dir', str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    commands, stop = [], threading.Event()
    try:
        client = redis.Redis(host='127.0.0.1', port=port, password=PASSWORD, socket_timeout=5)
        deadline = time.time() + 10
        while True:
            try:
                client.ping()
                break
            except redis.ConnectionError:
                assert time.time() < deadline, 'redis did not start'
                time.sleep(0.05)

        def monitor():
            try:
                with client.monitor() as feed:
                    for event in feed.listen():
                        commands.append(event['command'])
                        if stop.is_set():
                            return
            except redis.ConnectionError:
                return

        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        time.sleep(0.3)
        # BE/core/settings.py: CELERY_BROKER_URL without a database number (DB 0), no queue or route settings.
        app = Celery('core', task_cls=CoordinatedTask, set_as_current=False)
        app.conf.broker_url = f'redis://default:{PASSWORD}@127.0.0.1:{port}'
        results = []
        for case in CASES:
            def body(*args, **kwargs):
                return None
            task = app.task(name=case['task'], **case.get('task_options', {}))(body)
            options = {'task_id': case['task_id']} if 'task_id' in case else {}
            if 'countdown' in case:
                options['countdown'] = case['countdown']
            if 'expires_in' in case:
                options['expires'] = datetime.now(timezone.utc) + timedelta(seconds=case['expires_in'])
            before = len(commands)
            args = [value(a) for a in case['args']]
            task.apply_async(args=args if case.get('args_list') else tuple(args), kwargs={k: value(v) for k, v in case['kwargs']}, **options)
            raw = client.rpop('celery')
            assert raw is not None and client.llen('celery') == 0, case['name']
            time.sleep(0.2)
            results.append({**case, 'payload': raw.decode('utf-8'),
                            'broker_commands': [c if c.startswith('SADD') else ' '.join(c.split(' ')[:2])
                                                for c in commands[before:] if c.split(' ')[0] in ('SADD', 'LPUSH')]})
        stop.set()
        client.ping()
        binding = sorted(m.decode() for m in client.smembers('_kombu.binding.celery'))
        app.close()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(json.dumps({'celery': __import__('celery').__version__, 'kombu': __import__('kombu').__version__,
                                      'binding_members': binding, 'cases': results}, ensure_ascii=False, indent=1) + '\n')
        print(len(results), 'messages ->', OUTPUT.relative_to(ROOT))
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
