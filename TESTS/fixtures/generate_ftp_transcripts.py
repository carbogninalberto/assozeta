"""Write the ftp: image source transcripts of BE_GO/internal/pdfexport (pdf-exports.md).

reportlab reads ``<img src="ftp://...">`` with ``open_for_read`` -> ``rlUrlRead`` ->
``urllib.request.urlopen(name).read()``. CPython's FTPHandler and ftplib send a fixed
command sequence; this script records it for a set of scenarios, together with the
outcome (the bytes read or the exception class), in
``BE_GO/internal/pdfexport/testdata/ftp_transcripts.json``. The Go test replays the
same scenarios against the same scripted server.

Every scenario names its replies per command, so the transcript is the client side of
the exchange: the welcome lines, the reply lines of each command, the data connection
(a passive listener whose advertised address is deliberately wrong in most cases) and
the checksum of the bytes urlopen reads. A command without a rule answers 500.

Run with the project interpreter: ``python -I TESTS/fixtures/generate_ftp_transcripts.py``
(``--check`` compares instead of writing).
"""
import base64
import json
import socket
import socketserver
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / 'BE_GO/internal/pdfexport/testdata/ftp_transcripts.json'

LOGO = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9'
NOTES = b'line one\r\nline two\r\n'
LISTING = b'-rw-r--r-- 1 ftp ftp 12 Jan 01 2026 logo.jpg\r\n'

# The addresses a PASV reply advertises are not used by ftplib: the data connection goes
# to the host of the control connection. The first scenario advertises the real host, the
# others an address that no socket listens on.


def pasv(host='10,9,8,7'):
    return {'host': host}


def rule(command, *replies, **extra):
    return {'command': command, 'replies': list(replies), **extra}


def encode(payload):
    return base64.b64encode(payload).decode('ascii')


def login(user='anonymous', password='anonymous@', cwd='.'):
    return [rule('USER ' + user, '331 Password required'),
            rule('PASS ' + password, '230 Logged in'),
            rule('CWD ' + cwd, '250 Directory changed to /')]


def retr(name, data, final=('226 Transfer complete',)):
    return rule('RETR ' + name, '150 Opening data connection', data=encode(data), final=list(final))


def listing(name, data, final=('226 Transfer complete',)):
    command = 'LIST' + (' ' + name if name else '')
    return rule(command, '150 Here comes the directory listing', data=encode(data), final=list(final))


CASES = [
    {
        'name': 'file in root',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('PWD', '257 "/" is the current directory'),
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (127,0,0,1,{p1},{p2})'), 'pasv': pasv('127,0,0,1')},
            retr('logo.jpg', LOGO),
        ],
    },
    {
        'name': 'host name',
        'url': 'ftp://localhost:{port}/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('logo.jpg', LOGO),
        ],
    },
    {
        'name': 'nested directories',
        'url': 'ftp://127.0.0.1:{port}/one/two/three.jpg',
        'welcome': ['220 Service ready'],
        'rules': login(cwd='one/two') + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('three.jpg', LOGO),
        ],
    },
    {
        'name': 'percent escaped directory and file',
        'url': 'ftp://127.0.0.1:{port}/dir%20one/caf%C3%A9%20%2B.jpg',
        'welcome': ['220 Service ready'],
        'rules': login(cwd='dir one') + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('café +.jpg', LOGO),
        ],
    },
    {
        'name': 'user and password',
        'url': 'ftp://alice:secret@127.0.0.1:{port}/a.jpg',
        'welcome': ['220 Service ready'],
        'rules': login('alice', 'secret') + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('a.jpg', LOGO),
        ],
    },
    {
        'name': 'percent escaped user and password',
        'url': 'ftp://us%40er:p%40ss@127.0.0.1:{port}/a.jpg',
        'welcome': ['220 Service ready'],
        'rules': login('us@er', 'p@ss') + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('a.jpg', LOGO),
        ],
    },
    {
        'name': 'user without a password',
        'url': 'ftp://bob@127.0.0.1:{port}/a.jpg',
        'welcome': ['220 Service ready'],
        'rules': login('bob', '') + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('a.jpg', LOGO),
        ],
    },
    {
        'name': 'type=a attribute',
        'url': 'ftp://127.0.0.1:{port}/notes.txt;type=a',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE A', '200 Type set to A'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('notes.txt', NOTES),
        ],
    },
    {
        'name': 'type=d attribute',
        'url': 'ftp://127.0.0.1:{port}/folder;type=d',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE A', '200 Type set to A'),
            rule('TYPE A', '200 Type set to A'),
            rule('PWD', '257 "/" is the current directory'),
            rule('CWD folder', '250 Directory changed to /folder'),
            rule('CWD /', '250 Directory changed to /'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            listing('folder', LISTING),
        ],
    },
    {
        'name': 'retr 550 falls back to a listing',
        'url': 'ftp://127.0.0.1:{port}/missing.jpg',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            rule('RETR missing.jpg', '550 No such file or directory'),
            rule('TYPE A', '200 Type set to A'),
            rule('PWD', '257 "/" is the current directory'),
            rule('CWD missing.jpg', '250 Directory changed to /missing.jpg'),
            rule('CWD /', '250 Directory changed to /'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            listing('missing.jpg', LISTING),
        ],
    },
    {
        'name': 'login rejected',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': [
            rule('USER anonymous', '331 Password required'),
            rule('PASS anonymous@', '530 Login incorrect'),
        ],
    },
    {
        'name': 'pasv rejected',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE I', '200 Type set to I'),
            rule('PASV', '425 Cannot open passive connection'),
        ],
    },
    {
        'name': 'multiline replies',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220-Welcome to the test server', '220 Ready'],
        'rules': [
            rule('USER anonymous', '331-Password required', '331 Send PASS'),
            rule('PASS anonymous@', '230-Logged in', '230 Proceed'),
            rule('CWD .', '250-Directory changed', '250 to /'),
            rule('TYPE I', '200-Type set', '200 to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('logo.jpg', LOGO),
        ],
    },
    {
        'name': 'data connection closed early',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('logo.jpg', LOGO[:8], final=('426 Connection closed; transfer aborted',)),
        ],
    },
    {
        'name': 'directory url ending in a slash',
        'url': 'ftp://127.0.0.1:{port}/pub/',
        'welcome': ['220 Service ready'],
        'rules': login(cwd='pub') + [
            rule('TYPE A', '200 Type set to A'),
            rule('TYPE A', '200 Type set to A'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            listing('', LISTING),
        ],
    },
    {
        'name': 'type fails then the connection is opened again',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE I', '500 Unknown command'),
            # ftpwrapper.retrfile catches the failure and calls init() once more.
            rule('USER anonymous', '331 Password required'),
            rule('PASS anonymous@', '230 Logged in'),
            rule('CWD .', '250 Directory changed to /'),
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('logo.jpg', LOGO),
        ],
    },
    {
        'name': 'cwd rejected',
        'url': 'ftp://127.0.0.1:{port}/missing/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': [
            rule('USER anonymous', '331 Password required'),
            rule('PASS anonymous@', '230 Logged in'),
            rule('CWD missing', '550 No such directory'),
        ],
    },
    {
        'name': 'welcome lines separated by a lone cr',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        # makefile('r', encoding='utf-8') reads a lone CR as a newline, so getline
        # gets both welcome lines although the server sends no LF between them.
        'welcome': ['220-Welcome\r220 Ready'],
        'rules': login() + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('logo.jpg', LOGO),
        ],
    },
    {
        'name': 'command reply lines separated by lone crs',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 Service ready'],
        'rules': [
            rule('USER anonymous', '331-Need\r331-more\r331 Password required'),
            rule('PASS anonymous@', '230 Logged in'),
            rule('CWD .', '250 Directory changed to /'),
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('logo.jpg', LOGO),
        ],
    },
    {
        # 4 + 8187 + the translated CRLF is 8192 characters, so getline accepts the
        # line: the limit counts characters, not the 16380 bytes of the reply.
        'name': 'welcome line of two byte characters at the limit',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 ' + 'é' * 8187],
        'rules': login() + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            retr('logo.jpg', LOGO),
        ],
    },
    {
        'name': 'welcome line over the limit',
        'url': 'ftp://127.0.0.1:{port}/logo.jpg',
        'welcome': ['220 ' + 'a' * 8200],
        'rules': [],
    },
    {
        'name': 'pwd reply that is not 257 falls back to a listing',
        'url': 'ftp://127.0.0.1:{port}/missing.jpg',
        'welcome': ['220 Service ready'],
        'rules': login() + [
            rule('TYPE I', '200 Type set to I'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            rule('RETR missing.jpg', '550 No such file or directory'),
            rule('TYPE A', '200 Type set to A'),
            rule('PWD', '250 "/elsewhere" is it'),
            rule('CWD missing.jpg', '250 Directory changed to /missing.jpg'),
            # ftplib.FTP.pwd returns '' and cwd('') sends CWD .
            rule('CWD .', '250 Directory changed to /'),
            {**rule('PASV', '227 Entering Passive Mode (10,9,8,7,{p1},{p2})'), 'pasv': pasv()},
            listing('missing.jpg', LISTING),
        ],
    },
]


class TranscriptServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Handler(socketserver.StreamRequestHandler):
    def send(self, lines):
        for line in lines:
            self.wfile.write(line.encode('utf-8') + b'\r\n')
        self.wfile.flush()

    def handle(self):
        state = self.server.state
        self.send(state['welcome'])
        while True:
            raw = self.rfile.readline(8193)
            if not raw:
                return
            command = raw.rstrip(b'\r\n').decode('utf-8', 'replace')
            with state['lock']:
                state['commands'].append(command)
                current = None
                for index, candidate in enumerate(state['rules']):
                    if candidate['command'] == command:
                        current = state['rules'].pop(index)
                        break
            if current is None:
                self.send(state['default'])
                continue
            replies = list(current['replies'])
            data_sock = None
            if 'pasv' in current:
                data_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                data_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                data_sock.bind(('127.0.0.1', 0))
                data_sock.listen(1)
                port = data_sock.getsockname()[1]
                with state['lock']:
                    state['pasv'] = data_sock
                replies = [line.replace('{p1}', str(port >> 8)).replace('{p2}', str(port & 0xff)) for line in replies]
            self.send(replies)
            if 'data' in current:
                with state['lock']:
                    listener = state['pasv']
                    state['pasv'] = None
                if listener is not None:
                    listener.settimeout(5)
                    try:
                        connection, _ = listener.accept()
                    except OSError:
                        connection = None
                    if connection is not None:
                        with connection:
                            connection.sendall(base64.b64decode(current['data']))
                if current.get('final'):
                    self.send(current['final'])
            if current.get('close_control'):
                return


def run_case(case):
    state = {'welcome': case['welcome'], 'rules': [dict(rule) for rule in case['rules']],
             'default': case.get('default', ['500 Command not understood']), 'commands': [], 'pasv': None,
             'lock': threading.Lock()}
    server = TranscriptServer(('127.0.0.1', 0), Handler)
    server.state = state
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = case['url'].replace('{port}', str(server.server_address[1]))
    try:
        data = urllib.request.urlopen(url).read()
        outcome = {'base64': base64.b64encode(data).decode('ascii')}
    except BaseException as error:  # the class is the outcome, as reportlab sees it
        outcome = {'error': type(error).__name__}
    finally:
        commands = list(state['commands'])
        with state['lock']:
            pasv = state['pasv']
        if pasv is not None:
            pasv.close()
        server.shutdown()
        server.server_close()
    entry = {key: value for key, value in case.items() if key in ('name', 'url', 'welcome', 'rules', 'default')}
    entry['commands'] = commands
    entry['outcome'] = outcome
    return entry


def main():
    cases = [run_case(case) for case in CASES]
    text = json.dumps({'cases': cases}, ensure_ascii=False, indent=1) + '\n'
    if '--check' in sys.argv[1:]:
        if not OUTPUT.is_file() or OUTPUT.read_text() != text:
            raise SystemExit('stale: ' + str(OUTPUT))
        print('up to date')
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text)
    print(len(cases), 'cases,', sum('base64' in case['outcome'] for case in cases), 'successful')


if __name__ == '__main__':
    main()
