"""Actual owned TLS storage reads; no external provider or customer credentials.

The component matrix precedes production-server/image acceptance. Certificates,
keys and profile files are private temporary inputs, never durable report data.
"""
import contextlib
import hashlib
import hmac
import http.server
import json
import os
import re
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import warnings
from unittest.mock import patch
from urllib.parse import parse_qsl, quote, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.contracts.test_storage_tls import PAYLOAD, assert_result, cases


def certificate(directory, name):
    cert, key = directory/(name+'.pem'), directory/(name+'.key')
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                    '-subj','/CN=owned-storage-fixture','-addext','subjectAltName=IP:127.0.0.1,DNS:host.docker.internal,DNS:localhost',
                    '-addext','basicConstraints=critical,CA:TRUE','-keyout',str(key),'-out',str(cert)],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    key.chmod(0o600)
    return cert, key


def valid_signature(handler):
    auth = re.fullmatch(r'AWS4-HMAC-SHA256 Credential=([^/]+)/([^,]+),\s*SignedHeaders=([^,]+),\s*Signature=([0-9a-f]+)', handler.headers.get('Authorization',''))
    if not auth: return False
    access,scope,headers,signature = auth.groups()
    if access != 'fixture-access': return False
    parts = scope.split('/')
    if len(parts) != 4 or parts[1:] != ['us-east-1','s3','aws4_request']: return False
    target = urlsplit(handler.path)
    query = '&'.join(quote(k,safe='-_.~')+'='+quote(v,safe='-_.~') for k,v in sorted(parse_qsl(target.query,keep_blank_values=True)))
    canonical_headers = ''.join(name+':'+' '.join(handler.headers.get(name,'').split())+'\n' for name in headers.split(';'))
    payload = handler.headers.get('X-Amz-Content-Sha256',hashlib.sha256(b'').hexdigest())
    canonical = handler.command+'\n'+target.path+'\n'+query+'\n'+canonical_headers+'\n'+headers+'\n'+payload
    signed = 'AWS4-HMAC-SHA256\n'+handler.headers.get('X-Amz-Date','')+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    signing_key = b'AWS4fixture-secret'
    for part in parts: signing_key = hmac.new(signing_key,part.encode(),hashlib.sha256).digest()
    return hmac.compare_digest(hmac.new(signing_key,signed.encode(),hashlib.sha256).hexdigest(),signature)


@contextlib.contextmanager
def source(cert=None, key=None, *, bind='127.0.0.1'):
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_HEAD(self): self.respond(False)
        def do_GET(self): self.respond(True)
        def respond(self, body):
            valid = self.path.split('?')[0] == '/private/instance/logo.png'
            valid = valid and valid_signature(self)
            requests.append({'method':self.command, 'valid_identity_and_key':valid})
            self.send_response(200 if valid else 403)
            self.send_header('Content-Length', str(len(PAYLOAD) if valid else 0))
            self.end_headers()
            if valid and body: self.wfile.write(PAYLOAD)
    server = http.server.ThreadingHTTPServer((bind,0), Handler)
    if cert:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(cert,key); server.socket = tls.wrap_socket(server.socket,server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    state = {'stopped':False,'requests':requests}
    try: yield f'{"https" if cert else "http"}://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
        state['stopped'] = not thread.is_alive()
        assert state['stopped'], 'owned TLS server did not stop'


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    from django.conf import settings
    settings.configure()
    from botocore.config import Config
    from storages.backends.s3 import S3Storage
    report_dir = Path(os.environ.get('TESTS_STORAGE_TLS_REPORT', ROOT/'quality-reports/go-migration/storage-tls'))
    report_dir.mkdir(parents=True, exist_ok=True)
    report = {'success':False, 'scope':'owned TLS component matrix; not production-server or packaged acceptance',
              'private_services_stopped':False, 'private_files_removed':False, 'django':[], 'go':[]}
    (report_dir/'results.json').write_text(json.dumps(report)+'\n')
    sources = ['TESTS/contracts/test_storage_tls.py','TESTS/adapters/django/storage_tls.py',
               'TESTS/check_aws_config_patch.py','TESTS/check_storage_ca_bundle.py','BE/requirements.txt',
               'BE_GO/go.mod','BE_GO/go.sum','BE_GO/Dockerfile',
               'BE_GO/cmd/server/instance_logo.go']
    for directory in ('BE_GO/pkg/storage/s3credentials','BE_GO/pkg/storage/instancelogo',
                      'BE_GO/pkg/storage/s3trust','BE_GO/third_party/aws-config'):
        sources.extend(str(path.relative_to(ROOT)) for path in sorted((ROOT/directory).rglob('*')) if path.is_file())
    report['source_sha256'] = {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in sources}
    started = time.monotonic()
    states = []
    temporary_path = None
    try:
        with tempfile.TemporaryDirectory(prefix='az-storage-tls-') as directory, tempfile.TemporaryDirectory(prefix='tilde-',dir=report_dir) as tilde_dir:
            work = Path(directory); temporary_path = work
            good, key = certificate(work,'good'); wrong,_ = certificate(work,'wrong')
            (work/'invalid').write_text('not a certificate')
            (work/'empty').write_text('')
            paths = {'good':str(good),'wrong':str(wrong),'invalid':str(work/'invalid'),'missing':str(work/'missing'), 'empty':''}
            roots=work/'roots';roots.mkdir();(roots/'private.pem').write_bytes(good.read_bytes());paths['directory']=str(roots)
            for label, filename in [('literal-dollar','$CA_ALIAS'),('literal-space','certificate with spaces.pem'),('literal-false','false')]:
                file = work/filename; file.write_bytes(good.read_bytes()); paths[label] = str(file)
            tilde_file = Path(tilde_dir)/'ca.pem'; tilde_file.write_bytes(good.read_bytes())
            paths['tilde'] = '~/'+str(tilde_file.relative_to(Path.home()))
            paths['unexpanded-dollar'] = str(work/'$UNEXPANDED_CA')
            paths['trailing-space'] = str(good)+' '
            with source(good,key) as (tls_endpoint,tls_state), source() as (http_endpoint,http_state):
                states = [tls_state,http_state]
                fixture = []
                for case in cases():
                    folder = work/case['name']; folder.mkdir()
                    profile = 'chosen' if case['profile_credentials'] else 'default'
                    config = '['+('profile chosen' if profile == 'chosen' else 'default')+']\n'
                    if case['profile_ca_configured']: config += 'ca_bundle = '+paths[case['profile_ca']]+'\n'
                    (folder/'config').write_text(config)
                    (folder/'credentials').write_text('['+profile+']\naws_access_key_id=fixture-access\naws_secret_access_key=fixture-secret\n')
                    for file in folder.iterdir(): file.chmod(0o600)
                    env = {'PATH':os.defpath,'HOME':str(Path.home()),'AWS_CONFIG_FILE':str(folder/'config'),
                           'AWS_SHARED_CREDENTIALS_FILE':str(folder/'credentials'),'AWS_EC2_METADATA_DISABLED':'true',
                           'BOTO_CONFIG':str(work/'empty'),'NO_PROXY':'127.0.0.1', 'UNEXPANDED_CA':'good.pem', 'CA_ALIAS':str(wrong),
                           **{name:paths[value] for name,value in case['environment'].items()}}
                    item = {**case,'environment':env,'endpoint':tls_endpoint if case['tls'] else http_endpoint}
                    actual = {'name':case['name'],'initialized':False,'credentials':False,'exists':False,'read':False,'body_sha256':None}
                    connect = socket.socket.connect
                    def local_connect(sock, address):
                        assert isinstance(address, tuple) and address[0] == '127.0.0.1', 'external network forbidden'
                        return connect(sock,address)
                    with patch.dict(os.environ,env,clear=True), patch.object(socket.socket,'connect',local_connect), warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        options = {'session_profile':'chosen'} if case['profile_credentials'] else {'access_key':'fixture-access','secret_key':'fixture-secret'}
                        store = S3Storage(**options,bucket_name='private',region_name='us-east-1',endpoint_url=item['endpoint'],
                                          client_config=Config(retries={'max_attempts':0},connect_timeout=2,read_timeout=2))
                        try:
                            credential = store._create_session().get_credentials().get_frozen_credentials()
                            actual['credentials'] = credential.access_key == 'fixture-access' and credential.secret_key == 'fixture-secret'
                            store.connection.meta.client
                            actual['initialized'] = True
                            actual['exists'] = store.exists('instance/logo.png')
                            with store.open('instance/logo.png') as opened: payload = opened.read()
                            actual['read'] = True; actual['body_sha256'] = hashlib.sha256(payload).hexdigest()
                        except Exception as error:
                            actual['error_type'] = type(error).__name__
                        finally: store.connection.meta.client.close()
                    report['django'].append(actual); assert_result(case,actual); fixture.append(item)
                fixture_path = work/'fixture.json'; fixture_path.write_text(json.dumps(fixture)); fixture_path.chmod(0o600)
                result_path = work/'go-results.json'
                tool_env = {name:value for name,value in os.environ.items() if name in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
                run = subprocess.run(['go','test','-race','-tags=storagetlsintegration','./pkg/storage/s3credentials','-run','^TestDjangoStorageTLS$','-count=1','-json'],
                                     cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_STORAGE_TLS_FIXTURE':str(fixture_path),'ASSOZETA_STORAGE_TLS_RESULTS':str(result_path)},
                                     text=True,capture_output=True,timeout=150)
                (report_dir/'go-test.jsonl').write_text(run.stdout)
                (report_dir/'go-stderr.log').write_text(run.stderr)
                report['go_exit'] = run.returncode
                if result_path.exists(): report['go'] = json.loads(result_path.read_text())
                report['source_requests'] = [request for state in states for request in state['requests']]
                assert all(row['valid_identity_and_key'] for row in report['source_requests'])
                assert run.returncode == 0, 'Go TLS contract failed; see go-test.jsonl'
                assert [row['name'] for row in report['go']] == [row['name'] for row in cases()]
                for case,actual in zip(cases(), report['go']): assert_result(case,actual)
                report['success'] = True
    except BaseException as error:
        report['failure_type'] = type(error).__name__
        raise
    finally:
        report['elapsed_seconds'] = round(time.monotonic()-started,3)
        report['private_services_stopped'] = bool(states) and all(state['stopped'] for state in states)
        report['private_files_removed'] = temporary_path is not None and not temporary_path.exists()
        report['source_stable'] = all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == digest for path,digest in report['source_sha256'].items())
        report['success'] = report['success'] and report['source_stable'] and report['private_services_stopped'] and report['private_files_removed']
        (report_dir/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    assert report['success']


if __name__ == '__main__': main()
