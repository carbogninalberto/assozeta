"""Independently authored AI agent sessions (ws/agent/ and run_agent); no Django imports.

A WebSocket case opens one connection, compares the handshake, then runs its
steps in order: client frames, producer actions (run by the adapter), waits for
the stub endpoint and the exact next server event. Expected texts may hold
placeholders: ``<<uuid:N>>`` binds a random UUID on its first occurrence and
must repeat it afterwards; the others (``<<today_dmy>>``, ``<<stub_host>>``,
``<<os>>``, ``<<arch>>``, the manual corpus values, ``<<schema>>``) are values of
the run, substituted as JSON string content. Outbound requests are compared
after the case: method, path, the header set (lower-cased names) and the exact
body bytes.
"""
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from TESTS import ws_client
from TESTS.contracts import test_websockets_http as sessions

SOURCE = Path(__file__).resolve().parents[1] / 'fixtures/ws_agent_http.json'
ContractFailure = sessions.ContractFailure
require = sessions.require
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
TOKEN = re.compile(r'<<([a-z0-9_]+(?::\d+)?)>>')


def load_source(path=SOURCE):
    source = json.loads(Path(path).read_text())
    require(source.get('matrix_version') == 1 and source['cases'], 'source.version')
    return source


def escaped(value, ascii_only):
    return json.dumps(value, ensure_ascii=ascii_only)[1:-1]


def match(template, actual, values, captures, *, ascii_only=True):
    """Full match of actual against a template; binds new <<uuid:N>> captures."""
    pattern, groups, position = [], [], 0
    for token in TOKEN.finditer(template):
        pattern.append(re.escape(template[position:token.start()]))
        name = token.group(1)
        if name.startswith('uuid:'):
            if name in captures:
                pattern.append(re.escape(captures[name]))
            elif name in groups:
                pattern.append(f'(?P={name.replace(":", "_")})')
            else:
                groups.append(name)
                pattern.append(f'(?P<{name.replace(":", "_")}>{UUID})')
        else:
            require(name in values, f'placeholder.unknown:{name}')
            pattern.append(re.escape(escaped(values[name], ascii_only)))
        position = token.end()
    pattern.append(re.escape(template[position:]))
    found = re.fullmatch(''.join(pattern), actual, re.S)
    if found is None:
        return False
    for name in groups:
        captures[name] = found.group(name.replace(':', '_'))
    return True


def render(template, values, captures, *, ascii_only=True):
    def replace(token):
        name = token.group(1)
        if name.startswith('uuid:'):
            require(name in captures, f'placeholder.unbound:{name}')
            return captures[name]
        return escaped(values[name], ascii_only)
    return TOKEN.sub(replace, template)


def run_ws_case(case, base_url, *, values, captures, substitute, produce, wait_requests, timeout=15):
    host, port, prefix = sessions.endpoint(base_url)
    query = substitute(case['query']) if case['query'] else ''
    path = prefix + case['path'] + ('?' + query if query else '')
    session = ws_client.Session(host, port, timeout=timeout)
    log = []
    try:
        handshake = session.open(ws_client.build_request(path, f'{host}:{port}', key=ws_client.DEFAULT_KEY, headers=[]))
        log.append({'handshake': handshake.status})
        sessions.check_handshake(case, handshake, ws_client.DEFAULT_KEY)
        if handshake.status != 101:
            require(not case['steps'], 'case.steps-after-rejection')
            return log
        client_closed = False
        for index, step in enumerate(case['steps']):
            where = f'step{index}'
            if 'send_text' in step:
                session.send_text(step['send_text'])
            elif 'send_binary' in step:
                session.send_binary(bytes.fromhex(step['send_binary']))
            elif 'close' in step:
                session.send_close(step['close'], '')
                client_closed = True
            elif 'produce' in step:
                produce(step['produce'])
            elif 'wait_requests' in step:
                wait_requests(step['wait_requests'])
            elif 'expect' in step:
                event = session.recv(timeout)
                log.append(event)
                expected = step['expect']
                if 'text' in expected and 'text' in event and '<<' in expected['text']:
                    require(match(expected['text'], event['text'], values, captures), f'{where}.event:{json.dumps(event)[:300]}')
                else:
                    require(event == expected, f'{where}.event:{json.dumps(event)[:300]}')
                if 'close' in event and not client_closed:
                    session.send_close(event['close'], event['reason']) if event['close'] is not None else session.send_close()
                    client_closed = True
            else:
                raise ContractFailure(f'{where}.unknown-step')
        require(log and log[-1] == {'eof': True}, 'case.server-closed')
        return log
    finally:
        session.close()


def run_cli_case(case, command, env, *, timeout=60):
    """One run_agent process: stdin as authored, stdout/stderr bytes and the exit code."""
    done = subprocess.run([*command, '--association-id', case['association']], input=case['stdin'].encode(), env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return done.returncode, done.stdout.decode('utf-8', 'surrogateescape'), done.stderr.decode('utf-8', 'surrogateescape')


# Django's logging handler (core.settings LOGGING, the console handler on stderr):
# operational diagnostics with timestamps, not part of the command's transcript.
LOG_RECORD = re.compile(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} \[(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\] <[^>]*> -> .*\n', re.M)


def check_cli(case, result, values, captures):
    code, stdout, stderr = result
    stderr = LOG_RECORD.sub('', stderr)
    require(code == case['exit'], f'cli.exit:{code}:{stderr[-200:]!r}')
    require(match(case['stdout'], stdout, values, captures, ascii_only=False), f'cli.stdout:{stdout[-300:]!r}')
    require(match(case['stderr'], stderr, values, captures, ascii_only=False), f'cli.stderr:{stderr[-300:]!r}')


def check_requests(case, recorded, values, captures):
    expected = case['requests']
    require(len(recorded) == len(expected), f'requests.count:{len(recorded)}!={len(expected)}')
    for index, (want, got) in enumerate(zip(expected, recorded)):
        where = f'request{index}'
        require(got['method'] == want['method'] and got['path'] == want['path'], f'{where}.target:{got["method"]} {got["path"]}')
        body = got['body'].decode('utf-8', 'surrogateescape')
        require(match(want['body'], body, values, captures, ascii_only=False), f'{where}.body:{body[:200]!r}...{body[-300:]!r}')
        length = {**values, 'length': str(len(got['body']))}
        headers = sorted([name, render(value, length, captures, ascii_only=False)] for name, value in want['headers'])
        require(sorted([name.lower(), value] for name, value in got['headers']) == headers,
                f'{where}.headers:{sorted([n.lower(), v] for n, v in got["headers"])}')


def endpoint_host(url):
    parts = urlsplit(url)
    return f'{parts.hostname}:{parts.port}'
