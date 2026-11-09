"""Independently authored WebSocket consumer sessions; no Django imports.

Each case opens one connection, compares the handshake response, then runs its
steps in order: client frames, producer actions (run by the adapter) and the
exact next server event (text payload, pong, close code and reason, or TCP
close). A server-initiated close is echoed like a browser does; the case must
end with the server closing the connection.
"""
import json
from pathlib import Path
from urllib.parse import urlsplit

from TESTS import ws_client

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/websockets_http.json'


class ContractFailure(AssertionError):
    pass


def require(condition, check):
    if not condition:
        raise ContractFailure(check)


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source.get('matrix_version') == 1 and source['cases'], 'source.version')
    return source


def endpoint(base_url):
    url = urlsplit(base_url)
    return url.hostname, url.port, url.path.rstrip('/')


def check_handshake(case, handshake, key):
    expected = case['handshake']
    require(handshake.status == expected['status'], f'handshake.status:{handshake.status}')
    if expected['status'] == 101:
        require((handshake.header('Upgrade') or '').lower() == 'websocket', 'handshake.upgrade')
        tokens = [item.strip().lower() for item in (handshake.header('Connection') or '').split(',')]
        require('upgrade' in tokens, 'handshake.connection')
        require(handshake.header('Sec-WebSocket-Accept') == ws_client.accept_value(key), 'handshake.accept')
        require(handshake.header('Sec-WebSocket-Protocol') == expected['protocol'], 'handshake.protocol')
        require(handshake.header('Sec-WebSocket-Extensions') == expected['extensions'],
                f'handshake.extensions:{handshake.header("Sec-WebSocket-Extensions")}')
        return
    require(handshake.reason == expected['reason'], 'handshake.reason')
    require(handshake.body.decode('latin-1') == expected['body'], f'handshake.body:{handshake.body[:200]!r}')
    require(handshake.header('Content-Type') == expected['content_type'], 'handshake.content-type')
    require(handshake.header('Content-Length') == expected['content_length'], 'handshake.content-length')


def run_case(case, base_url, *, substitute, produce, timeout=10):
    """Run one authored session; return the observed event log."""
    host, port, prefix = endpoint(base_url)
    query = substitute(case['query']) if case['query'] else ''
    path = prefix + case['path'] + ('?' + query if query else '')
    options = dict(case.get('request') or {})
    options.pop('before', None)
    key = options.pop('key', ws_client.DEFAULT_KEY)
    headers = [(name, substitute(value)) for name, value in case['headers']]
    session = ws_client.Session(host, port, timeout=timeout)
    log = []
    try:
        handshake = session.open(ws_client.build_request(path, f'{host}:{port}', key=key, headers=headers,
                                                         version=options.pop('version', '13'), omit=tuple(options.pop('omit', ()))))
        log.append({'handshake': handshake.status})
        check_handshake(case, handshake, key)
        if handshake.status != 101:
            require(not case['steps'], 'case.steps-after-rejection')
            return log
        client_closed = False
        for index, step in enumerate(case['steps']):
            where = f'step{index}'
            if 'send_text' in step:
                session.send_text(step['send_text'], compress=step.get('compress', False))
            elif 'send_binary' in step:
                session.send_binary(bytes.fromhex(step['send_binary']))
            elif 'frames' in step:
                for frame in step['frames']:
                    payload = bytes.fromhex(frame['payload_hex']) if 'payload_hex' in frame else frame['payload'].encode()
                    session.frame(frame['opcode'], payload, fin=frame.get('fin', True), mask=frame.get('mask', True),
                                  length=frame.get('length'))
            elif 'ping' in step:
                session.frame(0x9, bytes.fromhex(step['ping']))
            elif 'close' in step:
                session.send_close(step['close'], step.get('reason', ''))
                client_closed = True
            elif 'close_raw' in step:
                session.send_close(raw=bytes.fromhex(step['close_raw']))
                client_closed = True
            elif 'produce' in step:
                produce(step['produce'])
            elif 'expect' in step:
                event = session.recv(timeout)
                log.append(event)
                require(event == step['expect'], f'{where}.event:{json.dumps(event)[:300]}')
                if 'close' in event and not client_closed:
                    # Answer a server-initiated close with its own payload, as browsers do.
                    session.send_close(event['close'], event['reason']) if event['close'] is not None else session.send_close()
                    client_closed = True
            else:
                raise ContractFailure(f'{where}.unknown-step')
        require(log and log[-1] == {'eof': True}, 'case.server-closed')
        return log
    finally:
        session.close()
