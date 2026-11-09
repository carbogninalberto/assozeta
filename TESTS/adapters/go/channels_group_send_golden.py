"""Capture the exact Redis commands Django's channel layer issues for group_send, for the Go publisher's golden tests.

Run with the migration interpreter from the repository root:
``python -I TESTS/adapters/go/channels_group_send_golden.py``
It starts a private redis-server and builds the real
``notifications.realtime_layer.GoMirroredRedisChannelLayer`` (channels_redis
RedisChannelLayer plus the Go mirror publish) with the BE/core/settings.py
CHANNEL_LAYERS config: one host, DB 0, every other option default. Channels
are added to groups with group_add (process-specific names from
new_channel() and plain names), then each authored message is sent with
group_send. The wire bytes redis-py writes and the replies it reads are
recorded per case (connection handshake excluded), together with the
time.time() and random.getrandbits() values channels_redis consumed (both
patched to deterministic sequences) and the envelope a pub/sub subscriber
received. Output: ``BE_GO/internal/channels/testdata/channels_redis_group_send.json``.
The Go test re-issues every case with the same clock/random inputs and
requires byte-identical commands and envelope. Separately it stores
msgpack.packb vectors for the format boundaries.
"""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import types
import uuid

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / 'BE_GO/internal/channels/testdata/channels_redis_group_send.json'
PASSWORD = 'synthetic-channels-only'
# Arguments longer than this are stored as length + sha256 to keep the file small.
INLINE_LIMIT = 4096
# Fake clock: 2026-11-09, chosen so group_add scores outlive the group_expiry trim.
BASE_SECONDS = 1794210000
HANDSHAKE = {b'AUTH', b'HELLO', b'CLIENT', b'SELECT'}


# Tagged values: {"t": kind, "v": value}; dict items keep their order.
def s(v):
    return {'t': 'str', 'v': v}


def i(v):
    return {'t': 'int', 'v': str(v)}


def d(*items):
    return {'t': 'dict', 'v': [[k, v] for k, v in items]}


def lst(*items):
    return {'t': 'list', 'v': list(items)}


NONE = {'t': 'none'}
TRUE, FALSE = {'t': 'bool', 'v': True}, {'t': 'bool', 'v': False}


def value(tagged):
    kind = tagged['t']
    if kind == 'str':
        return tagged['v']
    if kind == 'int':
        return int(tagged['v'])
    if kind == 'bool':
        return tagged['v']
    if kind == 'none':
        return None
    if kind == 'list':
        return [value(item) for item in tagged['v']]
    if kind == 'dict':
        return {key: value(item) for key, item in tagged['v']}
    raise ValueError(kind)


NOTIFICATION = d(['type', s('notification_push')], ['notification', d(
    ['id', s('3f6c2a1e-bd5f-11f0-8de9-0242ac120002')], ['type', s('subscription')], ['read', FALSE],
    ['msg', s('Nuova Iscrizione aggiunta per Mario Rossi.')], ['association_id', s('9b2d7c4e-1a3f-4e5b-8c6d-7e8f9a0b1c2d')],
    ['issued', i(1762685000)], ['date', s('Sun Nov  9 10:43:20 2026')])])

INT_EDGES = [0, 1, 127, 128, 255, 256, 65535, 65536, 2**32 - 1, 2**32, 2**63 - 1, 2**64 - 1,
             -1, -32, -33, -128, -129, -32768, -32769, -2**31, -2**31 - 1, -2**63]

# Each case: group, members to add in order ('new:<n>' = layer.new_channel() from client prefix n,
# 'stale:<name>' = member with score 0 that group_send trims), message, extra setup.
CASES = [
    {'name': 'notification-one-specific', 'group': 'notifications_user_42', 'members': ['new:0'], 'message': NOTIFICATION},
    {'name': 'notification-no-channels', 'group': 'notifications_user_7', 'members': [], 'message': NOTIFICATION},
    {'name': 'mixed-channels', 'group': 'notifications_user_1001',
     'members': ['new:0', 'plain.channel-a', 'new:0', 'new:1', 'plain_b', 'stale:specific.gone!x'],
     'message': NOTIFICATION},
    {'name': 'text-widths', 'group': 'text.widths', 'members': ['new:0', 'plain.text'],
     'message': d(['type', s('text.push')], ['ascii31', s('a' * 31)], ['ascii32', s('b' * 32)],
                  ['k' * 40, s('key longer than 31 bytes')],
                  ['str8max', s('c' * 255)], ['str16min', s('d' * 256)], ['str16max', s('e' * 65535)],
                  ['str32min', s('f' * 65536)],
                  ['accents', s("l'iscrizione è già \"pagata\" € \\ \n\t\x00\x1f\x7f")],
                  ['astral', s('\U0001F600 \U0001D11E    ﻿')],
                  ['multibyte31', s('à' * 15 + 'x')], ['multibyte32', s('à' * 16)], ['empty', s('')])},
    {'name': 'int-widths', 'group': 'int-widths', 'members': ['plain.ints'],
     'message': d(['type', s('ints')], ['values', lst(*[i(n) for n in INT_EDGES])],
                  *[[f'n{index}', i(n)] for index, n in enumerate(INT_EDGES)])},
    {'name': 'scalars-nested', 'group': 'nested', 'members': ['new:1', 'new:1'],
     'message': d(['type', s('nested')], ['none', NONE], ['t', TRUE], ['f', FALSE], ['empty_list', lst()], ['empty_dict', d()],
                  ['deep', d(['a', lst(d(['b', lst(lst(i(1), s('x'), NONE))]), TRUE)])],
                  ['list16', lst(*[i(n) for n in range(16)])], ['list15', lst(*[i(n) for n in range(15)])],
                  ['map16', d(*[[f'k{n}', i(n)] for n in range(16)])], ['map15', d(*[[f'k{n}', i(n)] for n in range(15)])])},
    # A caller-supplied __asgi_channel__ keeps its position: channels_redis overwrites it in place.
    {'name': 'reserved-key-first', 'group': 'reserved', 'members': ['plain.reserved', 'new:0'],
     'message': d(['__asgi_channel__', s('caller value')], ['type', s('reserved')])},
    {'name': 'over-capacity', 'group': 'full_group', 'members': ['plain.full', 'plain.free', 'new:1'], 'fill': ['plain.full'],
     'message': d(['type', s('full')])},
]

# msgpack.packb vectors for boundaries too large or too many for group_send cases. Large values are
# described by a generator the Go test rebuilds: map_none n = {"0": None, ..., str(n-1): None},
# list_none n = [None] * n, repeat [text, n] = text * n.
VECTORS = [
    ['map32', {'map_none': 65536}],
    ['map16max', {'map_none': 65535}],
    ['array32', {'list_none': 65536}],
    ['array16max', {'list_none': 65535}],
    ['str32-multibyte', {'repeat': ['\u00e9', 40000]}],
    ['str16max-multibyte', {'repeat': ['\u20ac', 21845]}],
    ['ints', {'value': lst(*[i(n) for n in INT_EDGES])}],
    ['top-level-str', {'value': s('x')}],
    ['top-level-int', {'value': i(-5)}],
]


def generate(spec):
    if 'map_none' in spec:
        return {str(n): None for n in range(spec['map_none'])}
    if 'list_none' in spec:
        return [None] * spec['list_none']
    if 'repeat' in spec:
        return spec['repeat'][0] * spec['repeat'][1]
    return value(spec['value'])


# time.time() conversions from nanoseconds, including one that rounds up to the next second.
REPR_NS = [BASE_SECONDS * 10**9, BASE_SECONDS * 10**9 + 1, BASE_SECONDS * 10**9 + 999_999_999,
           BASE_SECONDS * 10**9 + 123_456_789, 1_000_000_000, 1, 10**18 + 5 * 10**8, 2**53 + 1]


# Group names checked with the layer's valid_group_name (channels.layers.BaseChannelLayer).
GROUP_NAMES = ['notifications_user_42', 'a', 'A-z_0.9', 'x' * 99, 'x' * 100, '', 'a b', 'a!b', 'grüppe', 'é',
               '\u0663\u0664', 'abc\n', 'abc\n\n', '\n', 'abc\r', 'tab\t', 'a:b', 'a/b', '\U0001D7D8', 'x' * 98 + '\n']


def py_time_from_ns(ns):
    # CPython _PyTime_AsSecondsDouble: exact seconds are converted as integers, otherwise (double)ns / 1e9.
    if ns % 10**9 == 0:
        return float(ns // 10**9)
    return float(ns) / 1e9


def arg_record(raw):
    if len(raw) > INLINE_LIMIT:
        return {'len': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    return {'b64': base64.b64encode(raw).decode()}


def parse_commands(raw):
    commands, pos = [], 0
    while pos < len(raw):
        assert raw[pos:pos + 1] == b'*', raw[pos:pos + 20]
        end = raw.index(b'\r\n', pos)
        count, pos = int(raw[pos + 1:end]), end + 2
        args = []
        for _ in range(count):
            assert raw[pos:pos + 1] == b'$'
            end = raw.index(b'\r\n', pos)
            size, pos = int(raw[pos + 1:end]), end + 2
            args.append(raw[pos:pos + size])
            assert raw[pos + size:pos + size + 2] == b'\r\n'
            pos += size + 2
        commands.append(args)
    return commands


def reply_record(reply):
    if reply is None:
        return {'nil': True}
    if isinstance(reply, int):
        return {'int': reply}
    if isinstance(reply, bytes):
        return {'bytes': base64.b64encode(reply).decode()}
    if isinstance(reply, list):
        return {'array': [reply_record(r) for r in reply]}
    raise TypeError(type(reply))


async def run(port):
    sys.path.insert(0, str(ROOT / 'BE'))
    import channels_redis.core as core
    import channels_redis.serializers as serializers
    import msgpack
    import redis
    import redis.asyncio
    from redis.asyncio.connection import Connection
    from notifications.realtime_layer import GoMirroredRedisChannelLayer, REALTIME_CHANNEL

    state = {'recording': False, 'sent': [], 'replies': [], 'clock': None, 'ns': [], 'prefixes': []}
    setup_clock = iter(range(BASE_SECONDS - 3600, BASE_SECONDS))

    def fake_time():
        if state['clock'] is None:
            return float(next(setup_clock))
        ns = next(state['clock'])
        state['ns'].append(ns)
        return py_time_from_ns(ns)

    prefix_source = random.Random(20261109)

    def fake_getrandbits(bits):
        assert bits == 96, bits
        number = prefix_source.getrandbits(bits)
        state['prefixes'].append(number.to_bytes(12, 'big').hex())
        return number

    uuid_source = random.Random(4242)
    core.time = types.SimpleNamespace(time=fake_time)
    serializers.random = types.SimpleNamespace(getrandbits=fake_getrandbits)
    core.uuid = types.SimpleNamespace(uuid4=lambda: uuid.UUID(int=uuid_source.getrandbits(128), version=4))

    original_send, original_read = Connection.send_packed_command, Connection.read_response

    async def send_packed_command(self, command, check_health=True):
        if state['recording']:
            chunks = [command] if isinstance(command, (bytes, str)) else list(command)
            state['sent'].append(b''.join(c.encode() if isinstance(c, str) else bytes(c) for c in chunks))
        return await original_send(self, command, check_health)

    async def read_response(self, *args, **kwargs):
        reply = await original_read(self, *args, **kwargs)
        if state['recording']:
            state['replies'].append(reply)
        return reply

    Connection.send_packed_command, Connection.read_response = send_packed_command, read_response

    # BE/core/settings.py CHANNEL_LAYERS (REDIS_PROTOCOL redis, DB 0); one layer per client prefix.
    hosts = [{'address': f'redis://127.0.0.1:{port}/0', 'password': PASSWORD}]
    layers = [GoMirroredRedisChannelLayer(hosts=hosts), GoMirroredRedisChannelLayer(hosts=hosts)]
    layer = layers[0]
    admin = redis.asyncio.Redis(host='127.0.0.1', port=port, password=PASSWORD)
    subscriber = admin.pubsub()
    await subscriber.subscribe(REALTIME_CHANNEL)
    assert (await subscriber.get_message(timeout=2))['type'] == 'subscribe'

    results = []
    for number, case in enumerate(CASES):
        members = []
        for member in case['members']:
            if member.startswith('new:'):
                members.append(await layers[int(member[4:])].new_channel())
            elif member.startswith('stale:'):
                await admin.zadd(layer._group_key(case['group']), {member[6:]: 0})
                continue
            else:
                members.append(member)
            await layer.group_add(case['group'], members[-1])
        for channel in case.get('fill', []):
            await admin.zadd('asgi' + channel, {f'filler-{n}': BASE_SECONDS + 10**6 for n in range(layer.capacity)})
        message = value(case['message'])
        start = (BASE_SECONDS + 600 * number) * 10**9
        clock = [start + 999_999_999] + [start + 10**9 + 250_000_000 * n + 7 for n in range(1, 64)]
        state.update(clock=iter(clock), ns=[], prefixes=[], sent=[], replies=[], recording=True)
        await layer.group_send(case['group'], message)
        state['recording'] = False
        state['clock'] = None
        envelope = await subscriber.get_message(ignore_subscribe_messages=True, timeout=2)
        assert envelope is not None and envelope['type'] == 'message', case['name']
        commands = [c for raw in state['sent'] for c in parse_commands(raw)]
        handshake = sum(1 for c in commands if c[0].upper() in HANDSHAKE)
        assert handshake == 0, f'{case["name"]}: unexpected handshake in recording'
        assert len(state['replies']) == len(commands), case['name']
        results.append({
            'name': case['name'], 'group': case['group'], 'members': members, 'message': case['message'],
            'clock_ns': state['ns'], 'random_prefixes': state['prefixes'],
            'commands': [[arg_record(a) for a in c] for c in commands],
            'replies': [reply_record(r) for r in state['replies']],
            'envelope': arg_record(envelope['data']),
        })

    vectors = [{'name': name, 'spec': spec, 'packb': arg_record(msgpack.packb(generate(spec)))} for name, spec in VECTORS]
    reprs = [[str(ns), repr(py_time_from_ns(ns))] for ns in REPR_NS]
    names = []
    for name in GROUP_NAMES:
        try:
            names.append([name, layer.valid_group_name(name)])
        except TypeError:
            names.append([name, False])
    await subscriber.aclose()
    await admin.aclose()
    for item in layers:
        await item.close_pools()
    Connection.send_packed_command, Connection.read_response = original_send, original_read
    import channels
    import channels_redis
    return {'channels': channels.__version__, 'channels_redis': channels_redis.__version__,
            'msgpack': '.'.join(map(str, msgpack.version)), 'redis_py': redis.__version__,
            'cases': results, 'msgpack_vectors': vectors, 'time_repr': reprs,
            'group_names': names}


def main():
    binary = shutil.which('redis-server')
    assert binary, 'redis-server required'
    work = Path(tempfile.mkdtemp(prefix='az-channels-golden-', dir='/tmp'))
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    server = subprocess.Popen([binary, '--bind', '127.0.0.1', '--port', str(port), '--save', '', '--appendonly', 'no',
                               '--requirepass', PASSWORD, '--dir', str(work)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        import redis
        client = redis.Redis(host='127.0.0.1', port=port, password=PASSWORD, socket_timeout=5)
        deadline = time.time() + 10
        while True:
            try:
                client.ping()
                break
            except redis.ConnectionError:
                assert time.time() < deadline, 'redis did not start'
                time.sleep(0.05)
        client.close()
        result = asyncio.run(run(port))
        text = json.dumps(result, ensure_ascii=False, indent=1) + '\n'
        assert PASSWORD not in text and f'127.0.0.1:{port}' not in text
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(text)
        print(len(result['cases']), 'cases,', len(result['msgpack_vectors']), 'vectors ->', OUTPUT.relative_to(ROOT))
    finally:
        server.terminate()
        server.wait(timeout=10)
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    main()
