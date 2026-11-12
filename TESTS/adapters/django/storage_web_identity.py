"""Real S3Storage/Boto3 web-identity selection and local STS protocol oracle."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
ARN = 'arn:aws:iam::123456789012:role/'
TOKEN = 'synthetic.web.identity.token'


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    from django.conf import settings
    settings.configure()
    from storages.backends.s3 import S3Storage
    out = ROOT / 'quality-reports/go-migration/storage-web-identity'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'results.json').write_text('{"success":false}\n')
    tool_env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    profile = '[profile target]\nrole_arn=' + ARN + 'web\nweb_identity_token_file={TOKEN}\n'
    static = '[target]\naws_access_key_id=static-access\naws_secret_access_key=static-secret\naws_session_token=static-token\n'
    web_env = {'AWS_WEB_IDENTITY_TOKEN_FILE': '{TOKEN}', 'AWS_ROLE_ARN': ARN + 'web'}
    cases = []

    def web(role='web', token=TOKEN, session='generated'):
        return dict(Action='AssumeRoleWithWebIdentity', Role=role, Token=token, Session=session)

    def add(name, config=profile, credentials='', env=None, selected='target', options=None,
            token=TOKEN.encode(), file_mode='regular', expected=None, prefix='web', error=False,
            go_error=None, mode='ok'):
        cases.append(dict(Name=name, Config=config, Credentials=credentials, Environment=env or {},
                          Profile=selected, Options=options or {}, Token=token, FileMode=file_mode,
                          Expected=([web()] if expected is None else expected), Prefix=prefix,
                          Error=error, GoError=error if go_error is None else go_error, Mode=mode))

    add('profile')
    add('empty session generates name', config=profile + 'role_session_name=\n')
    add('explicit session', config=profile + 'role_session_name=fixture-session\n', expected=[web(session='fixture-session')])
    add('unrelated role options ignored', config=profile + 'duration_seconds=901\nexternal_id=fixture-external\nmfa_serial=fixture-device\n')
    add('environment only', config='', selected='', env=web_env)
    add('environment profile uses web environment', selected='', env={**web_env, 'AWS_PROFILE': 'target', 'AWS_ROLE_ARN': ARN + 'environment'}, expected=[web(role='environment')], prefix='environment')
    add('explicit profile ignores web environment', env={**web_env, 'AWS_WEB_IDENTITY_TOKEN_FILE': '{OTHER}', 'AWS_ROLE_ARN': ARN + 'wrong'})
    add('environment token merges profile role', selected='', env={'AWS_PROFILE': 'target', 'AWS_WEB_IDENTITY_TOKEN_FILE': '{OTHER}'}, expected=[web(token='other.web.identity.token')])
    add('empty environment session generates name', selected='', config=profile + 'role_session_name=ignored-session\n', env={'AWS_PROFILE': 'target', 'AWS_ROLE_SESSION_NAME': ''})
    add('static options precede web identity', selected='', config=profile.replace('[profile target]', '[default]'), options=dict(AccessKey='static-access', SecretKey='static-secret', SessionToken='static-token'), expected=[], prefix='static')
    add('static environment precedes web identity', selected='', config='', env={**web_env, 'AWS_ACCESS_KEY_ID': 'static-access', 'AWS_SECRET_ACCESS_KEY': 'static-secret', 'AWS_SESSION_TOKEN': 'static-token'}, expected=[], prefix='static')
    add('profile web precedes credentials file', credentials=static)
    add('profile web precedes config credentials', config=profile + static.split('\n', 1)[1])
    add('environment web precedes shared credentials', selected='', config='', credentials=static.replace('[target]', '[default]'), env=web_env)
    add('explicit static profile ignores web environment', config='[profile target]\nregion=us-east-1\n', credentials=static, env=web_env, expected=[], prefix='static')
    add('short token', token=b'abc', expected=[], error=True)
    add('empty token', token=b'', expected=[], error=True)
    add('universal newlines stay literal', token=b'\r\nabc\r', expected=[web(token='\nabc\n')])
    add('invalid UTF8', token=b'bad\xfftoken', expected=[], error=True)
    add('missing token file', file_mode='missing', expected=[], error=True)
    add('directory token file', file_mode='directory', expected=[], error=True)
    add('missing role', config='[profile target]\nweb_identity_token_file={TOKEN}\n', expected=[], error=True)
    add('short role ARN', config=profile.replace(ARN + 'web', 'short'), expected=[], error=True)
    add('short session', config=profile + 'role_session_name=x\n', expected=[], error=True)
    add('oversized token rejected in Go', token=b'x' * ((1 << 20) + 1), expected=[web(token='x' * ((1 << 20) + 1))], go_error=True)
    nested = '[profile target]\nrole_arn=' + ARN + 'target\nsource_profile=source\n[profile source]\nrole_arn=' + ARN + 'web\nweb_identity_token_file={TOKEN}\n'
    requests = [web(), dict(Action='AssumeRole', Role='target', Session='generated', Signer='web')]
    add('nested web source', config=nested, expected=requests, prefix='target')
    add('nested web source precedes static keys', config=nested + static.split('\n', 1)[1], expected=requests, prefix='target')
    add('exact token file limit', token=b'x' * (1 << 20), expected=[web(token='x' * (1 << 20))])
    regular_role='[default]\nrole_arn='+ARN+'target\nsource_profile=source\n'
    role_source='[source]\naws_access_key_id=web-access\naws_secret_access_key=web-secret\naws_session_token=web-token\n'
    add('default ordinary role precedes web environment', config=regular_role, credentials=role_source, selected='', env=web_env, expected=[requests[1]], prefix='target')
    add('token paths are literal', config=profile.replace('{TOKEN}','{ROOT}/$TOKEN_ROOT/token'), env={'TOKEN_ROOT':'.'}, expected=[], error=True)
    add('projected token symlink', file_mode='symlink')
    for mode in ('invalid-identity', 'denied', 'malformed', 'missing-credentials', 'missing-expiry', 'missing-token', 'expired'):
        add('provider ' + mode, mode=mode, error=True)

    observed = {}
    problems = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            index_text, backend = urlsplit(self.path).path.strip('/').split('/')
            index = int(index_text)
            case = cases[index]
            body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            form = {k: v[0] for k, v in parse_qs(body.decode(), keep_blank_values=True).items()}
            records = observed.setdefault((index, backend), [])
            position = len(records)
            records.append(True)
            expected = case['Expected'] if backend == 'django' or case['GoError'] == case['Error'] else []
            issue = None
            if position >= len(expected):
                issue = 'unexpected request'
            else:
                request = expected[position]
                fields = dict(Action=request['Action'], Version='2011-06-15', RoleArn=ARN + request['Role'])
                session = form.get('RoleSessionName', '')
                if request['Session'] == 'generated':
                    if not re.fullmatch(r'botocore-session-[0-9]+', session) or abs(int(session.removeprefix('botocore-session-')) - time.time()) > 30:
                        issue = 'session name differs'
                    fields['RoleSessionName'] = session
                else:
                    fields['RoleSessionName'] = request['Session']
                if request['Action'] == 'AssumeRoleWithWebIdentity':
                    fields['WebIdentityToken'] = request['Token']
                    if self.headers.get('Authorization') or self.headers.get('X-Amz-Security-Token'):
                        issue = 'web identity request must be unsigned'
                else:
                    auth = re.fullmatch(r'AWS4-HMAC-SHA256 Credential=([^/]+)/([^,]+),\s*SignedHeaders=([^,]+),\s*Signature=([0-9a-f]+)', self.headers.get('Authorization', ''))
                    if not auth:
                        issue = 'missing source signature'
                    else:
                        access, scope, headers, signature = auth.groups()
                        date, region, service, terminal = scope.split('/')
                        if access != 'web-access' or self.headers.get('X-Amz-Security-Token') != 'web-token' or (region, service, terminal) != ('us-east-1', 'sts', 'aws4_request'):
                            issue = 'wrong source identity'
                        canonical_headers = ''.join(name + ':' + ' '.join(self.headers.get(name, '').split()) + '\n' for name in headers.split(';'))
                        canonical = 'POST\n' + urlsplit(self.path).path + '\n\n' + canonical_headers + '\n' + headers + '\n' + hashlib.sha256(body).hexdigest()
                        signed = 'AWS4-HMAC-SHA256\n' + self.headers.get('X-Amz-Date', '') + '\n' + scope + '\n' + hashlib.sha256(canonical.encode()).hexdigest()
                        key = b'AWS4web-secret'
                        for part in (date, region, service, terminal):
                            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
                        if not hmac.compare_digest(hmac.new(key, signed.encode(), hashlib.sha256).hexdigest(), signature):
                            issue = 'source signature differs'
                if fields != form:
                    issue = 'request fields differ'
            if issue:
                problems.append((case['Name'], backend, issue))
            mode = case['Mode']
            action = form.get('Action', 'AssumeRoleWithWebIdentity')
            role = form.get('RoleArn', '').rsplit('/', 1)[-1]
            expiry = '2000-01-01T00:00:00Z' if mode == 'expired' else '2099-01-01T00:00:00Z'
            credentials = '<Credentials><AccessKeyId>' + role + '-access</AccessKeyId><SecretAccessKey>' + role + '-secret</SecretAccessKey>'
            if mode != 'missing-token':
                credentials += '<SessionToken>' + role + '-token</SessionToken>'
            if mode != 'missing-expiry':
                credentials += '<Expiration>' + expiry + '</Expiration>'
            credentials += '</Credentials>'
            if mode == 'missing-credentials':
                credentials = ''
            payload = ('<' + action + 'Response xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><' + action + 'Result>' + credentials + '</' + action + 'Result></' + action + 'Response>').encode()
            status = 200
            if mode == 'malformed':
                payload = b'not XML sensitive-web-provider-message'
            if issue or mode in ('invalid-identity', 'denied'):
                status = 400 if mode == 'invalid-identity' else 403
                code = 'InvalidIdentityToken' if mode == 'invalid-identity' else 'AccessDenied'
                payload = ('<ErrorResponse><Error><Code>' + code + '</Code><Message>sensitive-web-provider-message</Message></Error></ErrorResponse>').encode()
            self.send_response(status)
            self.send_header('Content-Type', 'text/xml')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='az-web-identity-') as directory:
            for index, case in enumerate(cases):
                folder = Path(directory) / str(index)
                folder.mkdir()
                replace = lambda value: value.replace('{TOKEN}', str(folder / 'token')).replace('{OTHER}', str(folder / 'other')).replace('{ROOT}', str(folder))
                for name, content in [('config', case.pop('Config')), ('credentials', case.pop('Credentials')), ('boto', '')]:
                    (folder / name).write_text(replace(content))
                    (folder / name).chmod(0o600)
                token = case.pop('Token')
                (folder / 'other').write_text('other.web.identity.token')
                (folder / 'other').chmod(0o600)
                if case['FileMode'] == 'regular':
                    (folder / 'token').write_bytes(token)
                    (folder / 'token').chmod(0o600)
                elif case['FileMode'] == 'directory':
                    (folder / 'token').mkdir()
                elif case['FileMode'] == 'symlink':
                    (folder / 'projected').write_bytes(token)
                    (folder / 'projected').chmod(0o600)
                    (folder / 'token').symlink_to(folder / 'projected')
                endpoint = f'http://127.0.0.1:{server.server_port}/{index}/'
                env = {'PATH': os.defpath, 'AWS_CONFIG_FILE': str(folder / 'config'), 'AWS_SHARED_CREDENTIALS_FILE': str(folder / 'credentials'), 'BOTO_CONFIG': str(folder / 'boto'), 'AWS_EC2_METADATA_DISABLED': 'true', 'AWS_REGION': 'us-east-1', 'AWS_DEFAULT_REGION': 'us-east-1', 'AWS_MAX_ATTEMPTS': '1', 'AWS_ENDPOINT_URL_STS': endpoint + 'django', **{k: replace(v) for k, v in case['Environment'].items()}}
                failed = False
                with patch.dict(os.environ, env, clear=True):
                    try:
                        options = case['Options']
                        credentials = S3Storage(access_key=options.get('AccessKey', ''), secret_key=options.get('SecretKey', ''), security_token=options.get('SessionToken', ''), session_profile=case['Profile'])._create_session().get_credentials()
                        for _ in range(2):
                            frozen = credentials.get_frozen_credentials()
                            assert [frozen.access_key, frozen.secret_key, frozen.token] == [case['Prefix'] + suffix for suffix in ('-access', '-secret', '-token')], 'wrong Django credentials'
                    except AssertionError:
                        raise
                    except Exception:
                        failed = True
                assert failed == case['Error'], (case['Name'], 'Django error differs')
                assert len(observed.get((index, 'django'), [])) == len(case['Expected']), (case['Name'], 'Django request count differs')
                assert not problems, problems
                case['Environment'] = {**env, 'AWS_ENDPOINT_URL_STS': endpoint + 'go'}
            (out / 'django-results.json').write_text(json.dumps(dict(success=True, cases=len(cases))) + '\n')
            fixture = Path(directory) / 'cases.json'
            fixture.write_text(json.dumps(cases))
            fixture.chmod(0o600)
            run = subprocess.run(['go', 'test', '-race', '-tags=storagewebidentityintegration', './pkg/storage/s3credentials', '-run', '^TestDjangoWebIdentity$', '-json'], cwd=ROOT / 'BE_GO', env={**tool_env, 'ASSOZETA_STORAGE_WEB_FIXTURE': str(fixture)}, capture_output=True, text=True, timeout=180)
            assert 'sensitive-web-provider-message' not in run.stdout + run.stderr, 'provider response leaked'
            (out / 'go-test.jsonl').write_text(run.stdout)
            events = [json.loads(line) for line in run.stdout.splitlines()]
            for event in events:
                if event.get('Output'):
                    print(event['Output'], end='')
            if run.stderr:
                print(run.stderr)
            if problems:
                print('Protocol differences:', problems)
            run.check_returncode()
            assert not problems, problems
            for index, case in enumerate(cases):
                expected = 0 if case['GoError'] != case['Error'] else len(case['Expected'])
                assert len(observed.get((index, 'go'), [])) == expected, (case['Name'], 'Go request count differs')
            assert sum(e['Action'] == 'pass' and '/' in e.get('Test', '') for e in events) == len(cases)
            assert not any(e['Action'] == 'skip' for e in events)
        result = dict(success=True, cases=len(cases), intentional_corrections=1, scope='real local STS protocol; no live identity-provider or AWS policy proof')
        (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


if __name__ == '__main__':
    main()
