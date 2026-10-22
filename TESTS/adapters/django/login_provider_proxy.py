"""Private CONNECT/TLS provider fixture; never forwards traffic to real hosts.

Only standard proxy and CA environment settings select this fixture. Provider
URLs, production clients, TLS verification and signature checks remain real.
"""
import datetime
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class ProviderProxy:
    hosts = {'www.googleapis.com', 'oauth2.googleapis.com', 'appleid.apple.com'}

    def __init__(self, work):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        self.calls = []
        self.errors = []
        self.access = {}
        self.lock = threading.Lock()
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        tls_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Disposable provider fixture')])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(tls_key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1))
                .not_valid_after(now+datetime.timedelta(hours=2))
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(host) for host in self.hosts]), critical=False)
                .sign(tls_key, hashes.SHA256()))
        self.ca = work/'provider-ca.pem'
        self.ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        private = work/'provider-tls-key.pem'
        private.write_bytes(tls_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        private.chmod(0o600)
        self.tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.tls.load_cert_chain(self.ca, private)
        self.public = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        import jwt
        self.jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwk.update(kid='public-fixture', alg='RS256', use='sig')
        owner = self

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_GET(self):
                parsed = urlsplit(self.path)
                host = self.headers.get('Host')
                status = 200
                if host == 'www.googleapis.com' and parsed.path == '/oauth2/v1/certs':
                    kind, body = 'google-certs', {'public-fixture': owner.public}
                elif host == 'appleid.apple.com' and parsed.path == '/auth/keys':
                    kind, body = 'apple-keys', {'keys': [owner.jwk]}
                elif host == 'oauth2.googleapis.com' and parsed.path == '/tokeninfo':
                    kind = 'google-tokeninfo'
                    token = parse_qs(parsed.query).get('access_token', [''])[0]
                    info = owner.access.get(token)
                    body = {'aud': info['aud']} if info else {}
                    if not info: status = 401
                elif host == 'www.googleapis.com' and parsed.path == '/oauth2/v3/userinfo':
                    kind = 'google-userinfo'
                    info = owner.access.get(self.headers.get('Authorization', '').removeprefix('Bearer '))
                    body = {'email': info['email'], 'sub': 'fixture-access'} if info else {}
                    if not info: status = 401
                else:
                    kind, status, body = 'unexpected', 502, {}
                    with owner.lock: owner.errors.append('unexpected provider destination')
                with owner.lock: owner.calls.append(kind)
                encoded = json.dumps(body).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        class Proxy(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_CONNECT(self):
                if self.path not in {host+':443' for host in owner.hosts}:
                    with owner.lock: owner.errors.append('unexpected CONNECT destination')
                    self.send_error(502)
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.flush()
                try:
                    self.connection.settimeout(15)
                    with owner.tls.wrap_socket(self.connection, server_side=True) as secure:
                        Provider(secure, self.client_address, self.server)
                except (ssl.SSLError, ConnectionError, OSError):
                    # Failed trust must be observable to the client. Never bypass
                    # verification or forward the request to the public network.
                    with owner.lock: owner.errors.append('provider TLS/connection failed')
                self.close_connection = True

        class FixtureServer(ThreadingHTTPServer):
            # Concurrent contracts open eight connections at once. The stdlib
            # backlog of five can reset an otherwise valid local TLS connection.
            request_queue_size = 64

        self.server = FixtureServer(('127.0.0.1', 0), Proxy)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def environment(self):
        return {'HTTPS_PROXY': f'http://127.0.0.1:{self.server.server_port}',
                'NO_PROXY': '127.0.0.1,localhost', 'REQUESTS_CA_BUNDLE': str(self.ca), 'SSL_CERT_FILE': str(self.ca)}

    def token(self, provider, email, *, audience=None, expired=False, wrong_signature=False):
        import jwt
        now = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
        claims = {'iss': 'https://appleid.apple.com' if provider == 'apple' else 'accounts.google.com',
                  'aud': audience or 'http-'+provider, 'iat': now-60, 'exp': now-30 if expired else now+3600,
                  'sub': 'fixture-subject', 'email': email, 'email_verified': True}
        return jwt.encode(claims, self.other_key if wrong_signature else self.key, algorithm='RS256', headers={'kid': 'public-fixture'})

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)
        assert not self.thread.is_alive(), 'provider fixture did not drain'
