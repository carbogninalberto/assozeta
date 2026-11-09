"""Parent-only instance outbound HTTP runner; imports are inert.

service_context owns every server the probes may reach for the whole run:
- a private certificate authority (and one untrusted authority);
- an HTTPS proxy that accepts CONNECT for any host and answers inside the
  tunnel with a leaf certificate for the fixed provider hosts, recording each
  request (tunnel target, method, path, every header) and answering with the
  replies the current case scripts (nothing else is contacted);
- four fake SMTP servers (STARTTLS, implicit TLS, plain, STARTTLS with an
  untrusted certificate) recording every client byte per connection.
Before either server starts, the process environment points both backends at
them: HTTPS_PROXY/NO_PROXY, SSL_CERT_FILE (OpenSSL's default verify paths:
urllib and Django's SMTP backend) and REQUESTS_CA_BUNDLE (requests); Django's
probe subprocess inherits them, the Go server receives them. urllib's global
opener is rebuilt so it reads the proxy.

seed_rows inserts the self-hosted InstanceConfiguration. Before each request
the case's 'setup' overwrites managed fields (queryset.update: no save
hooks; passwords and '$fernet-integration:' secrets encrypted with the run's
SECRET_KEY, '$port:<server>' resolved; 'leases' replaces only
diagnostic_results['operation_leases']) and the release catalog cache is set
through Django's cache. After the response: the exact outbound requests, the
normalized SMTP transcript (EHLO name, Date, Message-ID and SIZE checked and
replaced), the cache keys (the catalog compared byte for byte with what
Django pickles for the scripted feed, and the TTLs), then the cache keys are
removed. The effect check compares the complete managed fields: '$lease' is a
claimed lease (now + 20 s), '$isonow' a request-time isoformat(); updated_at
never changes.
"""
from contextlib import contextmanager
import base64
import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import tempfile
import threading
import time
import types

from TESTS.contracts import test_instance_outbound_http as base_contract
from TESTS.adapters.django import family_harness as harness
from TESTS.adapters.django.instance_admin_http import same as same_secret
from TESTS.adapters.django.workflow_writes_http import keyed

SOURCES = ('TESTS/fixtures/instance_outbound_http.json', 'TESTS/contracts/test_instance_outbound_http.py',
           'TESTS/adapters/django/instance_outbound_http.py', 'TESTS/adapters/django/instance_admin_http.py',
           'TESTS/adapters/go/payment_category_server.py', 'BE/instance/administration.py', 'BE/instance/release_catalog.py',
           'BE/instance/operations.py', 'BE/instance/integrations.py', 'BE/instance/operation_limits.py', 'BE/instance/probe_runner.py',
           'BE/instance/diagnostic_probe.py', 'BE/instance/integration_probe.py', 'BE/instance/email_configuration.py',
           'BE/instance/integration_configuration.py', 'BE/instance/permissions.py', 'BE/instance/models.py',
           'BE/core/settings.py') + harness.SOURCES
CONFIG = 'instance.InstanceConfiguration'
FIELDS = ('email_settings', 'email_revision', 'diagnostic_results', 'integration_settings')
FERNET = '$fernet-integration:'
CATALOG_KEY = 'instance:stable-releases:v1'
HOSTS = ('api.github.com', 'api.stripe.com', 'www.googleapis.com', 'appleid.apple.com', 'api.openai.com', 'llm.example.test')
STUB = {}
ENVIRONMENT = ('HTTPS_PROXY', 'https_proxy', 'NO_PROXY', 'no_proxy', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE')


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

def certificate_authority(directory, label, hosts):
    """A CA and a leaf for hosts (DNS names and 127.0.0.1), as Python 3.13's strict verification wants."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key, leaf_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f'contract {label} authority')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False, key_encipherment=False,
                                       data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hosts[0])]))
            .issuer_name(ca_name).public_key(leaf_key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(x509.SubjectAlternativeName([*(x509.DNSName(h) for h in hosts),
                                                        x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False, key_encipherment=False,
                                         data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                                         encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(leaf_key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    ca_path, chain_path, key_path = (Path(directory) / f'{label}-{name}.pem' for name in ('ca', 'chain', 'key'))
    ca_path.write_bytes(ca.public_bytes(pem))
    chain_path.write_bytes(leaf.public_bytes(pem) + ca.public_bytes(pem))
    key_path.write_bytes(leaf_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(chain_path, key_path)
    return ca_path, context


# ---------------------------------------------------------------------------
# HTTPS proxy answering inside the tunnel
# ---------------------------------------------------------------------------

class _TunnelHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    target = None

    def log_message(self, *_args):
        pass

    def do_CONNECT(self):
        target = self.path
        self.send_response(200, 'Connection established')
        self.end_headers()
        self.wfile.flush()
        try:
            tunnel = STUB['context'].wrap_socket(self.connection, server_side=True)
            tunnel.settimeout(10)
        except (OSError, ssl.SSLError):
            self.close_connection = True
            return
        handler = type('_Inner', (_TunnelHandler,), {'target': target})
        try:
            handler(tunnel, self.client_address, self.server)
        except (OSError, ssl.SSLError):
            pass
        self.close_connection = True

    def _answer(self):
        if self.target is None:
            self.send_error(400)
            return
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else b''
        with STUB['lock']:
            STUB['log'].append({'connect': self.target, 'method': self.command, 'path': self.path,
                                'headers': {k.lower(): v for k, v in self.headers.items()},
                                'names': [k.lower() for k in self.headers.keys()], 'body': body})
            number = len(STUB['log'])
            script = STUB['script']
            answer = script[number - 1]['reply'] if number <= len(script) else {'status': 599, 'headers': {}, 'raw_b64': ''}
        data = base64.b64decode(answer['raw_b64'])
        self.send_response(answer['status'])
        for key, value in answer['headers'].items():
            self.send_header(key, value)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _answer


# ---------------------------------------------------------------------------
# Fake SMTP servers
# ---------------------------------------------------------------------------

class FakeSMTP:
    """A small ESMTP server; records the client bytes per connection."""

    def __init__(self, context, mode):
        self.context, self.mode = context, mode
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.lock = threading.Lock()
        self.transcripts = []
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def take(self):
        with self.lock:
            taken, self.transcripts = self.transcripts, []
        return taken

    def serve(self):
        while True:
            try:
                connection, _ = self.listener.accept()
            except OSError:
                return
            threading.Thread(target=self.converse, args=(connection,), daemon=True).start()

    def converse(self, connection):
        units = []
        password = STUB['smtp_password'].encode()
        try:
            connection.settimeout(15)
            stream = self.context.wrap_socket(connection, server_side=True) if self.mode == 'ssl' else connection
            tls = self.mode == 'ssl'
            stream.sendall(b'220 fake.smtp.test ESMTP\r\n')
            buffer, data = b'', False
            while True:
                terminator = b'\r\n.\r\n' if data else b'\n'
                while terminator not in buffer:
                    chunk = stream.recv(65536)
                    if not chunk:
                        units.append('<eof>')
                        return
                    buffer += chunk
                end = buffer.index(terminator) + len(terminator)
                unit, buffer = buffer[:end], buffer[end:]
                units.append(unit.decode('latin-1'))
                if data:
                    data = False
                    stream.sendall(b'250 queued\r\n')
                    continue
                verb = unit.split(b' ', 1)[0].strip().upper()
                if verb == b'EHLO':
                    features = ['fake.smtp.test', 'SIZE 10485760'] + ([] if self.mode == 'plain' else ['AUTH PLAIN LOGIN']) + \
                        ([] if tls or self.mode == 'plain' else ['STARTTLS'])
                    stream.sendall(''.join(f'250{"-" if i < len(features) - 1 else " "}{f}\r\n'
                                           for i, f in enumerate(features)).encode())
                elif verb == b'STARTTLS':
                    stream.sendall(b'220 ready\r\n')
                    stream = self.context.wrap_socket(stream, server_side=True)
                    units.append('<tls>')
                    tls, buffer = True, b''
                elif verb == b'AUTH':
                    parts = unit.split()
                    secret = base64.b64decode(parts[2]).split(b'\0')[-1] if len(parts) > 2 else b''
                    stream.sendall(b'235 ok\r\n' if secret == password else b'535 denied\r\n')
                elif verb == b'DATA':
                    data = True
                    stream.sendall(b'354 go ahead\r\n')
                elif verb == b'QUIT':
                    stream.sendall(b'221 bye\r\n')
                    units.append('<quit>')
                    return
                elif verb == b'RCPT':
                    stream.sendall(b'550 refused\r\n' if b'rifiutato@' in unit else b'250 ok\r\n')
                elif verb in (b'MAIL', b'RSET'):
                    stream.sendall(b'250 ok\r\n')
                else:
                    stream.sendall(b'500 unknown\r\n')
        except (OSError, ssl.SSLError) as error:
            units.append('<error ' + type(error).__name__ + '>')
        finally:
            connection.close()
            with self.lock:
                self.transcripts.append(units)

    def close(self):
        self.listener.close()
        self.thread.join(5)


@contextmanager
def service_context(report, log):
    import urllib.request
    directory = tempfile.mkdtemp(prefix='az-instance-outbound-')
    saved = {name: os.environ.get(name) for name in ENVIRONMENT}
    servers, proxy, thread = {}, None, None
    report['service_cleanup']['outbound_stubs'] = 'running'
    try:
        trusted_ca, trusted = certificate_authority(directory, 'trusted', HOSTS)
        _untrusted_ca, untrusted = certificate_authority(directory, 'untrusted', ('fake.smtp.test',))
        STUB.update(lock=threading.Lock(), log=[], script=[], context=trusted, ports={})
        proxy = ThreadingHTTPServer(('127.0.0.1', 0), _TunnelHandler)
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        for name, context, mode in (('starttls', trusted, 'starttls'), ('ssl', trusted, 'ssl'), ('plain', trusted, 'plain'),
                                    ('untrusted', untrusted, 'starttls')):
            servers[name] = FakeSMTP(context, mode)
            STUB['ports'][name] = servers[name].port
        STUB['servers'] = servers
        address = f'http://127.0.0.1:{proxy.server_address[1]}'
        os.environ.update(HTTPS_PROXY=address, https_proxy=address, NO_PROXY='127.0.0.1,localhost', no_proxy='127.0.0.1,localhost',
                          SSL_CERT_FILE=str(trusted_ca), REQUESTS_CA_BUNDLE=str(trusted_ca))
        urllib.request.install_opener(urllib.request.build_opener())
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        urllib.request.install_opener(None)
        stopped = True
        if proxy is not None:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=10)
            stopped = not thread.is_alive()
        for server in servers.values():
            server.close()
            stopped = stopped and not server.thread.is_alive()
        for path in Path(directory).iterdir():
            path.unlink()
        Path(directory).rmdir()
        report['service_cleanup']['outbound_stubs'] = 'stopped' if stopped else 'cleanup_failed'
        STUB.clear()


# ---------------------------------------------------------------------------
# Tokens, setup and the per-case checks
# ---------------------------------------------------------------------------

def resolve(value):
    """'$port:<server>' -> the fake server's port."""
    if isinstance(value, str) and value.startswith('$port:'):
        return STUB['ports'][value[len('$port:'):]]
    if isinstance(value, dict):
        return {k: resolve(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v) for v in value]
    return value


def stored_secrets(value):
    from instance.integration_configuration import encrypt_secret
    if isinstance(value, dict):
        return {k: stored_secrets(v) for k, v in value.items()}
    if isinstance(value, str) and value.startswith(FERNET):
        return encrypt_secret(value[len(FERNET):])
    return value


def expected_secrets(value):
    if isinstance(value, dict) and set(value) not in ({'$secret'}, {'$literal'}):
        return {k: expected_secrets(v) for k, v in value.items()}
    if isinstance(value, str) and value.startswith(FERNET):
        return {'$secret': value[len(FERNET):]}
    return value


def apply_setup(case, source):
    from instance.email_configuration import encrypt_password
    from instance.models import InstanceConfiguration
    fields = case.get('setup')
    if not fields:
        return
    update = {}
    for key, value in resolve(fields).items():
        if key == 'leases':
            row = InstanceConfiguration._base_manager.get(pk=source['instance']['id'])
            results = dict(row.diagnostic_results or {})
            results.pop('operation_leases', None)
            if value is not None:
                results['operation_leases'] = value
            update['diagnostic_results'] = results
        elif key == 'email_password':
            update['email_password_encrypted'] = value['$literal'] if isinstance(value, dict) else encrypt_password(value)
        elif key == 'integration_settings':
            update[key] = stored_secrets(value)
        else:
            update[key] = value
    count = InstanceConfiguration._base_manager.filter(pk=source['instance']['id']).update(**update)
    base_contract.require(count == 1, 'setup.instance-row')


def django_catalog(pages):
    """fetch_releases' value for the scripted pages, built by Django's own normalize_release."""
    from instance.release_catalog import normalize_release, version_tuple
    releases = {}
    for page in pages:
        for raw in json.loads(json.dumps(page)):
            release = normalize_release(raw)
            if release:
                releases[release['id']] = release
    return sorted(releases.values(), key=lambda item: version_tuple(item['tag']), reverse=True)


def cache_connection():
    from django_redis import get_redis_connection
    return get_redis_connection('default')


def cache_keys():
    from django.core.cache import cache
    return cache.make_key(CATALOG_KEY), cache.make_key(CATALOG_KEY + ':refresh')


def apply_cache(case):
    from django.core.cache import cache
    spec = case.get('cache')
    connection = cache_connection()
    catalog, refresh = cache_keys()
    connection.delete(catalog, refresh)
    if not spec:
        return None
    if spec.get('catalog') is not None:
        cache.set(CATALOG_KEY, django_catalog(spec['catalog']), timeout=600)
    if spec.get('refresh'):
        cache.set(CATALOG_KEY + ':refresh', True, timeout=30)
    return connection.get(catalog)


def check_cache(case, before):
    import pickle
    require = base_contract.require
    connection = cache_connection()
    catalog, refresh = cache_keys()
    try:
        spec = case.get('cache_after')
        if spec is None:
            require(connection.get(catalog) == before and connection.get(refresh) is None, 'cache.unchanged')
            return
        stored, ttl = connection.get(catalog), connection.pttl(catalog)
        if spec['catalog'] is None:
            require(stored is None, 'cache.catalog-absent')
        elif spec['catalog'] == 'unchanged':
            require(stored is not None and stored == before and 0 < ttl <= 600000, 'cache.catalog-unchanged')
        else:
            expected = pickle.dumps(django_catalog(spec['catalog']), pickle.DEFAULT_PROTOCOL)
            require(stored == expected, 'cache.catalog-bytes')
            require(590000 < ttl <= 600000, 'cache.catalog-ttl')
        flag, flag_ttl = connection.get(refresh), connection.pttl(refresh)
        if spec.get('refresh'):
            require(flag == pickle.dumps(True, pickle.DEFAULT_PROTOCOL) and 0 < flag_ttl <= 30000, 'cache.refresh-bound')
        else:
            require(flag is None, 'cache.refresh-absent')
    finally:
        connection.delete(catalog, refresh)


def check_outbound(case):
    require = base_contract.require
    with STUB['lock']:
        log = list(STUB['log'])
    expected = case['outbound']
    require(len(log) == len(expected), 'outbound.request-count')
    for index, (sent, spec) in enumerate(zip(log, expected)):
        where = f':{index}'
        require(sent['connect'] == spec['connect'], 'outbound.tunnel' + where)
        require(sent['method'] == spec['method'] and sent['path'] == spec['path'], 'outbound.request-line' + where)
        require(sent['headers'] == spec['headers'] and len(sent['names']) == len(set(sent['names'])), 'outbound.headers' + where)
        require(sent['body'] == b'', 'outbound.body' + where)


EHLO = re.compile(r'(?i)^(ehlo|helo) (\S+)\r\n\Z')
SIZE = re.compile(r'^(mail from:<[^>]*>) size=(\d+)\r\n\Z')
DATE = re.compile(r'\r\nDate: [A-Z][a-z]{2}, \d\d [A-Z][a-z]{2} \d{4} \d\d:\d\d:\d\d -0000\r\n')
MESSAGE_ID = re.compile(r'\r\nMessage-ID: (\r\n )?(<\d+\.\d+\.\d+@([^>\r\n]+)>)\r\n')


def normalized_transcript(units):
    require = base_contract.require
    local, data_sizes, out = set(), [], []
    for unit in units:
        if unit.endswith('\r\n.\r\n'):
            data_sizes.append(len(unit) - 5)
    sizes = iter(data_sizes)
    for unit in units:
        if unit.startswith('<error') or unit == '<eof>':
            out.append('<closed>')
            continue
        match = EHLO.match(unit)
        if match:
            local.add(match.group(2))
            out.append(match.group(1) + ' <local>\r\n')
            continue
        match = SIZE.match(unit)
        if match:
            # SIZE is the message length; without a DATA unit (a refused
            # recipient) it is only checked to be positive.
            size = next(sizes, None)
            require(int(match.group(2)) == size if size is not None else int(match.group(2)) > 0, 'smtp.size')
            out.append(match.group(1) + ' size=<size>\r\n')
            continue
        if unit.endswith('\r\n.\r\n'):
            require(len(DATE.findall(unit)) == 1, 'smtp.date')
            unit = DATE.sub('\r\nDate: <date>\r\n', unit)
            match = MESSAGE_ID.search(unit)
            require(match is not None, 'smtp.message-id')
            folded, identifier, domain = match.group(1) is not None, match.group(2), match.group(3)
            require(folded == (len('Message-ID: ') + len(identifier) > 78), 'smtp.message-id-folding')
            local.add(domain)
            unit = MESSAGE_ID.sub('\r\nMessage-ID: <message-id>\r\n', unit)
        out.append(unit)
    require(len(local) <= 1, 'smtp.local-name')
    return out


def check_smtp(case):
    require = base_contract.require
    spec = case['smtp']
    servers = STUB['servers']
    deadline = time.monotonic() + 10
    while spec and not servers[spec['server']].transcripts and time.monotonic() < deadline:
        time.sleep(0.05)
    if not spec:
        time.sleep(0.1)
    taken = {name: server.take() for name, server in servers.items()}
    for name, transcripts in taken.items():
        if spec and name == spec['server']:
            require(len(transcripts) == 1, 'smtp.connections')
            require(normalized_transcript(transcripts[0]) == spec['transcript'], 'smtp.transcript')
        else:
            require(transcripts == [], 'smtp.unexpected-connection')


def _assert_case(case, base_url, *, token, sessions):
    source = base_contract.load_source()
    apply_setup(case, source)
    before = apply_cache(case)
    with STUB['lock']:
        STUB['log'] = []
        STUB['script'] = case['outbound']
        STUB['smtp_password'] = source['smtp_password']
    for server in STUB['servers'].values():
        server.take()
    status = base_contract.assert_case(case, base_url, token=token, sessions=sessions)
    check_outbound(case)
    check_smtp(case)
    check_cache(case, before)
    return status


contract = types.SimpleNamespace(load_source=base_contract.load_source, require=base_contract.require, assert_case=_assert_case,
                                 ResponseFailure=base_contract.ResponseFailure)


# ---------------------------------------------------------------------------
# Fixture and effects
# ---------------------------------------------------------------------------

def seed_rows(source, created):
    from instance.models import InstanceConfiguration
    spec = dict(source['instance'])
    base_contract.require(not InstanceConfiguration._base_manager.exists(), 'fixture.instance-exists')
    spec['primary_association_id'] = source['ids'][spec.pop('primary_association')]
    InstanceConfiguration._base_manager.bulk_create([InstanceConfiguration(**spec)])
    created.append((InstanceConfiguration, spec['id']))


ISO_NOW = re.compile(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?\+00:00\Z')


def same(expected, actual, decrypt):
    """same_secret plus '$lease' (now + 20 s, as a float) and '$isonow'."""
    if expected == '$lease':
        return type(actual) is float and abs(actual - (time.time() + 20)) < 60
    if expected == '$isonow':
        if not (isinstance(actual, str) and ISO_NOW.match(actual)):
            return False
        return abs(datetime.datetime.fromisoformat(actual).timestamp() - time.time()) < 120
    if isinstance(expected, dict) and set(expected) not in ({'$secret'}, {'$literal'}):
        return isinstance(actual, dict) and set(expected) == set(actual) and all(same(expected[k], actual[k], decrypt) for k in expected)
    if isinstance(expected, list):
        return isinstance(actual, list) and len(expected) == len(actual) and all(same(e, a, decrypt) for e, a in zip(expected, actual))
    return same_secret(expected, actual, decrypt)


def assert_effect(case, before, after, source, context):
    from instance.email_configuration import decrypt_password
    from instance.integration_configuration import decrypt_secret
    require = base_contract.require
    harness.require_unmanaged_unchanged(base_contract, before, after, (CONFIG,))
    key = str(source['instance']['id'])
    old, new = keyed(before[CONFIG], 'id'), keyed(after[CONFIG], 'id')
    require(set(old) == set(new) == {key}, 'state.instance-row')
    old, new = old[key], new[key]
    require(new['updated_at'] == old['updated_at'], 'state.updated-at-kept')
    expected = case['effect']
    if expected is None:
        require(old == new, 'state.instance-unchanged')
        return
    settled = set(FIELDS) | {'email_password_encrypted'}
    require({k: v for k, v in old.items() if k not in settled} == {k: v for k, v in new.items() if k not in settled},
            'state.instance-other-fields')
    expected = resolve(expected)
    for field in FIELDS:
        require(same(expected_secrets(expected[field]), new[field], decrypt_secret), 'state.' + field)
    require(same(expected['email_password'], new['email_password_encrypted'], decrypt_password), 'state.email-password')


fixture_context = harness.fixture_context_for(contract, seed_rows)
run_workload = harness.run_workload_for(contract, assert_effect)
