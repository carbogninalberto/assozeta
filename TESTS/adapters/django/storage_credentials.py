"""Real django-storages/Boto3 credential selection with private profile files.

No customer credentials, home profiles or external network are used.
"""
import json
import os
from pathlib import Path
import socket
import shlex
import subprocess
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    from django.conf import settings
    settings.configure()
    from storages.backends.s3 import S3Storage
    out = ROOT / 'quality-reports/go-migration/storage-credentials'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'results.json').write_text('{"success":false}\n')
    tool_env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'TMPDIR', 'GOCACHE', 'GOMODCACHE', 'GOTOOLCHAIN')}
    cases = []
    default = '[default]\naws_access_key_id=file-access\naws_secret_access_key=file-secret\naws_session_token=file-token\n'
    chosen = '[chosen]\naws_access_key_id=chosen-access\naws_secret_access_key=chosen-secret\n'
    def add(name, options=None, env=None, credentials=default+chosen, config='', want=None, error=False, correction=False, process=None, stderr=False, prefix=False):
        cases.append(dict(Name=name, Options={'AccessKey':'','SecretKey':'','SessionToken':'','Profile':'', **(options or {})},
            Environment=env or {}, Credentials=credentials, Config=config,
            Expected=want or ['file-access','file-secret','file-token'], Error=error, Correction=correction, Process=process, ProcessStderr=stderr, ProcessPrefix=prefix))
    add('default shared credentials')
    add('static overrides files', {'AccessKey':'static-access','SecretKey':'static-secret'}, want=['static-access','static-secret',''])
    add('static session token', {'AccessKey':'static-access','SecretKey':'static-secret','SessionToken':'static-token'}, want=['static-access','static-secret','static-token'])
    add('static literal values', {'AccessKey':'$ACCESS','SecretKey':' secret # literal '}, want=['$ACCESS',' secret # literal ',''])
    add('named storage profile', {'Profile':'chosen'}, want=['chosen-access','chosen-secret',''])
    add('named profile bypasses environment', {'Profile':'chosen'}, {'AWS_ACCESS_KEY_ID':'env-access','AWS_SECRET_ACCESS_KEY':'env-secret'}, want=['chosen-access','chosen-secret',''])
    add('named storage profile overrides environment profile', {'Profile':'chosen'}, {'AWS_PROFILE':'missing'}, want=['chosen-access','chosen-secret',''])
    add('environment profile', env={'AWS_PROFILE':'chosen'}, want=['chosen-access','chosen-secret',''])
    add('default profile wins over profile', env={'AWS_DEFAULT_PROFILE':'chosen','AWS_PROFILE':'missing'}, want=['chosen-access','chosen-secret',''])
    add('environment credentials precede environment profile', env={'AWS_PROFILE':'chosen','AWS_ACCESS_KEY_ID':'env-access','AWS_SECRET_ACCESS_KEY':'env-secret','AWS_SESSION_TOKEN':'env-token'}, want=['env-access','env-secret','env-token'])
    add('missing explicit profile', {'Profile':'sensitive-missing-profile'}, error=True)
    add('missing environment profile even with static credentials', {'AccessKey':'static-access','SecretKey':'static-secret'}, {'AWS_PROFILE':'sensitive-missing-profile'}, error=True)
    add('profile conflicts with access key', {'Profile':'chosen','AccessKey':'static-access'}, error=True)
    add('profile conflicts with secret key', {'Profile':'chosen','SecretKey':'static-secret'}, error=True)
    add('profile ignores standalone token', {'Profile':'chosen','SessionToken':'ignored-token'}, want=['chosen-access','chosen-secret',''])
    add('static partial access never falls back', {'AccessKey':'static-access'}, want=['static-access','',''], error=True, correction=True)
    add('static partial secret never falls back', {'SecretKey':'static-secret'}, want=['','static-secret',''], error=True, correction=True)
    add('standalone token never falls back', {'SessionToken':'static-token'}, want=['','','static-token'], error=True, correction=True)
    add('partial environment never falls back', env={'AWS_ACCESS_KEY_ID':'env-access'}, error=True)
    add('no credentials', credentials='', error=True)
    add('credentials file overrides config', config='[default]\naws_access_key_id=config-access\naws_secret_access_key=config-secret\n', want=['file-access','file-secret','file-token'])
    add('config-only default', credentials='', config='[default]\naws_access_key_id=config-access\naws_secret_access_key=config-secret\n', want=['config-access','config-secret',''])
    add('config-only named profile', {'Profile':'chosen'}, credentials='', config='[profile chosen]\naws_access_key_id=config-access\naws_secret_access_key=config-secret\naws_session_token=config-token\n', want=['config-access','config-secret','config-token'])
    add('malformed configuration', config='invalid configuration', error=True)
    add('malformed config even with static keys', {'AccessKey':'static-access','SecretKey':'static-secret'}, config='invalid configuration', error=True)
    add('partial credentials file', credentials='[default]\naws_access_key_id=file-access\n', error=True)
    add('empty environment profile', env={'AWS_PROFILE':''}, error=True)
    add('empty default profile overrides nonempty profile', env={'AWS_DEFAULT_PROFILE':'','AWS_PROFILE':'chosen'}, error=True)
    add('duplicate credential option', credentials=default+'aws_access_key_id=other\n', error=True)
    add('duplicate credential section', credentials=default+default, error=True)
    add('option before section', config='region=us-east-1\n[default]\n', error=True)
    add('unclosed section', config='[default\nregion=us-east-1\n', error=True)
    add('option missing delimiter', config='[default]\ninvalid-option\n', error=True)
    add('comments and colon delimiters', credentials='# comment\n; comment\n[default]\naws_access_key_id: colon-access\naws_secret_access_key: colon-secret\n', want=['colon-access','colon-secret',''])
    add('unknown options are retained by parser', credentials=default+'unrelated_option = harmless\n')
    add('shared file paths expand environment', env={'AWS_SHARED_CREDENTIALS_FILE':'$CASE_DIR/credentials','AWS_CONFIG_FILE':'${CASE_DIR}/config'})
    add('config directory is ignored', env={'AWS_CONFIG_FILE':'$CASE_DIR'})
    add('credentials directory is ignored', env={'AWS_SHARED_CREDENTIALS_FILE':'$CASE_DIR'}, config='[default]\naws_access_key_id=config-access\naws_secret_access_key=config-secret\n', want=['config-access','config-secret',''])
    process={'Version':1,'AccessKeyId':'process-access','SecretAccessKey':'process-secret','SessionToken':'process-token'}
    add('credential process', credentials='', process=process, want=['process-access','process-secret','process-token'])
    add('credential process stderr withheld', credentials='', process=process, stderr=True, want=['process-access','process-secret','process-token'])
    add('credential process oversized output', credentials='', process=process, prefix=True, want=['process-access','process-secret','process-token'], error=True, correction=True)
    add('credential process wrong version', credentials='', process={**process,'Version':2}, error=True)
    add('credential process expired', credentials='', process={**process,'Expiration':'2000-01-01T00:00:00Z'}, error=True)
    add('credential process missing secret', credentials='', process={'Version':1,'AccessKeyId':'process-access'}, error=True)
    with tempfile.TemporaryDirectory(prefix='az-storage-credentials-') as directory:
        work = Path(directory)
        for index, case in enumerate(cases):
            folder = work / str(index); folder.mkdir()
            process=case.pop('Process')
            process_stderr=case.pop('ProcessStderr'); process_prefix=case.pop('ProcessPrefix')
            if process is not None:
                script=folder/'provider.py'
                script.write_text('import sys\n'+('sys.stderr.write("sensitive-process-stderr")\n' if process_stderr else '')+'sys.stdout.write('+repr((' '*(1<<20) if process_prefix else '')+json.dumps(process))+')\n')
                case['Config']='[default]\ncredential_process = '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+'\n'
            for name, content in [('credentials',case.pop('Credentials')),('config',case.pop('Config')),('boto','')]:
                (folder/name).write_text(content); (folder/name).chmod(0o600)
            env = {'CASE_DIR':str(folder),'PATH':os.defpath,'AWS_SHARED_CREDENTIALS_FILE':str(folder/'credentials'),'AWS_CONFIG_FILE':str(folder/'config'),
                'BOTO_CONFIG':str(folder/'boto'),'AWS_EC2_METADATA_DISABLED':'true',**case['Environment']}
            case['Environment'] = env
            options = case['Options']
            failed = False
            with patch.dict(os.environ, env, clear=True), patch.object(socket.socket,'connect',side_effect=AssertionError('external credential network forbidden')):
                try:
                    store = S3Storage(access_key=options['AccessKey'], secret_key=options['SecretKey'],
                        security_token=options['SessionToken'] or None, session_profile=options['Profile'] or None)
                    credentials = store._create_session().get_credentials()
                    if credentials is None:
                        failed = True
                    else:
                        frozen = credentials.get_frozen_credentials()
                        actual = [frozen.access_key or '', frozen.secret_key or '', frozen.token or '']
                except AssertionError:
                    raise
                except Exception:
                    failed = True
            assert failed == (case['Error'] and not case['Correction']), (case['Name'],'Django error boundary differs')
            if not failed:
                assert actual == case['Expected'], (case['Name'],'Django credential selection differs')
        (out/'django-results.json').write_text(json.dumps({'success':True,'cases':len(cases),'intentional_corrections':4})+'\n')
        fixture = work/'cases.json'; fixture.write_text(json.dumps(cases)); fixture.chmod(0o600)
        run = subprocess.run(['go','test','-race','-tags=storagecredentialsintegration','./pkg/storage/s3credentials','-run','^TestDjangoCredentialSelection$','-json'],
            cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_STORAGE_CREDENTIALS_FIXTURE':str(fixture)},capture_output=True,text=True,timeout=120)
        assert 'sensitive-process-stderr' not in run.stderr+run.stdout, 'credential process leaked stderr'
        (out/'go-test.jsonl').write_text(run.stdout)
        if run.stderr: print(run.stderr)
        events = [json.loads(line) for line in run.stdout.splitlines()]
        for event in events:
            if event.get('Output'): print(event['Output'],end='')
        run.check_returncode()
        assert sum(e['Action']=='pass' and '/' in e.get('Test','') for e in events)==len(cases)
        assert not any(e['Action']=='skip' for e in events)
    result = {'success':True,'cases':len(cases),'intentional_corrections':4,'scope':'static, environment and private shared-file selection; no live cloud identity proof'}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n'); print(json.dumps(result))


if __name__ == '__main__':
    main()
