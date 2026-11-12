"""Actual native and packaged Linux logo HTTP over owned TLS storage.

Uses a source-bound production image, current-schema private PG/Redis, and a
local S3 protocol fixture which independently verifies every SigV4 signature.
This is trust/HTTP acceptance, not installation or external-provider proof.
"""
import argparse
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.django.storage_tls import certificate, source
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.payment_category_image import ImageFixture, DATABASE
from TESTS.adapters.go.server import build_server
from TESTS.contracts.test_storage_tls import PAYLOAD, cases
from TESTS.http_client import Client, poll


def source_hashes():
    return {str(path.relative_to(ROOT/'BE_GO')):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((ROOT/'BE_GO').rglob('*')) if path.is_file() and '.DS_Store' not in path.name}


def clean_build_receipt(args):
    revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    assert args.expected_revision==revision,'Clean image proof requires the exact checkout revision'
    dirty=subprocess.check_output(['git','status','--porcelain','--untracked-files=all','--','BE_GO',
        'TESTS/adapters/go/storage_tls.py','TESTS/adapters/django/storage_tls.py','TESTS/contracts/test_storage_tls.py'],cwd=ROOT,text=True)
    assert not dirty,'Dirty or mixed sources require a source-bound build receipt'
    metadata=json.loads(ImageFixture.docker('image','inspect',args.image).stdout)[0]
    identity=metadata['Id']
    lines=ImageFixture.docker('run','--rm','--network','none','--read-only','--entrypoint','/bin/sh',identity,
        '-c','cat /app/CODE_REVISION; sha256sum /app/server').stdout.splitlines()
    assert lines[0]==revision,'Packaged revision differs from checkout'
    return {'success':True,'source_stable':True,'revision':revision,'immutable_id':identity,
            'go_source_sha256':source_hashes(),'server_sha256':lines[1].split()[0],
            'scope':'Image built by the calling CI job from this clean exact checkout'}


@contextmanager
def native(executable, environment, work, log, report):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0)); port = listener.getsockname()[1]
    process = subprocess.Popen([str(executable)],cwd=work,
        env={**environment,'GO_LISTEN_ADDR':f'127.0.0.1:{port}'},stdout=log,stderr=subprocess.STDOUT)
    try:
        base = f'http://127.0.0.1:{port}'
        def ready():
            assert process.poll() is None, 'Native storage TLS server exited'
            try: return Client(base,timeout=2).request('GET','/readyz')[0] == 200
            except OSError: return False
        poll(ready,bool,timeout=30)
        yield base
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=15)
            except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
        report['native_processes_stopped'] = process.poll() is not None


@contextmanager
def packaged(proof, environment, public, work, case_name):
    env_file = work/'package.env'
    assert all('\n' not in str(value) and '\r' not in str(value) for value in environment.values())
    env_file.write_text(''.join(f'{key}={value}\n' for key,value in environment.items()));env_file.chmod(0o600)
    mounts = ['--mount',f'type=bind,source={public},target=/fixture,readonly']
    if case_name == 'tilde':
        # Preserve the image's real home setting: mount the owned public bundle
        # there instead of repurposing HOME or changing the process identity.
        mounts += ['--mount',f'type=bind,source={public},target=/home/assozeta,readonly']
    container = proof.run_container('server-'+case_name,proof.image_id,
        '--publish','127.0.0.1::8000','--read-only','--cap-drop','ALL',
        '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,noexec,nosuid,size=64m',
        '--add-host','host.docker.internal:host-gateway','--env-file',env_file,*mounts)
    try:
        info = proof.inspect(container)
        assert info['Image'] == proof.image_id and info['HostConfig']['ReadonlyRootfs']
        assert proof.docker('exec',container,'id','-u').stdout.strip() == '10001'
        assert proof.docker('exec',container,'cat','/app/CODE_REVISION').stdout.strip() == proof.revision
        actual_env=dict(value.split('=',1) for value in info['Config']['Env'])
        for key in ('AWS_CONFIG_FILE','AWS_SHARED_CREDENTIALS_FILE','AWS_CA_BUNDLE','REQUESTS_CA_BUNDLE'):
            assert actual_env.get(key)==environment.get(key),('Packaged trust environment differs',key)
        for key in ('AWS_CONFIG_FILE','AWS_SHARED_CREDENTIALS_FILE'):
            public_path=public/Path(environment[key]).relative_to('/fixture')
            actual=proof.docker('exec',container,'cat',environment[key]).stdout
            assert actual==public_path.read_text(),('Packaged profile file differs',key)
        port = proof.published_port(container,8000);base=f'http://127.0.0.1:{port}'
        def ready():
            assert proof.inspect(container)['State']['Running'], 'Packaged storage TLS server exited'
            try:return Client(base,timeout=2).request('GET','/readyz')[0] == 200
            except OSError:return False
        poll(ready,bool,timeout=30)
        yield base
    finally:
        proof.docker('stop','--time','15',container,check=False,timeout=25)
        logs=proof.docker('logs',container,check=False)
        assert not proof.safe_log(proof.output/('package-'+case_name+'.log'),logs.stdout+logs.stderr), 'Private fixture value leaked into logs'


def main():
    require_production_python()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--build-receipt',type=Path,help='Required for dirty or mixed source builds')
    parser.add_argument('--expected-revision',help='Required for a clean CI image build without an explicit receipt')
    parser.add_argument('--case',help='Bounded diagnostic only; receipt explicitly records a partial matrix')
    parser.add_argument('--output',type=Path,default=ROOT/'quality-reports/go-migration/storage-tls/endpoint')
    parser.add_argument('--postgres',default='postgres:16-alpine')
    parser.add_argument('--redis',default='redis:7-alpine')
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    report={'success':False,'scope':__doc__,'rows':{},'private_services_stopped':False,
            'native_processes_stopped':False,'private_files_removed':False,'managed_state_unchanged':False}
    receipt=args.output/'results.json';receipt.write_text(json.dumps(report)+'\n')
    selected=[case for case in cases() if args.case is None or case['name']==args.case]
    assert selected,'Unknown TLS contract case'
    report['full_matrix']=args.case is None
    report['selected_cases']=[case['name'] for case in selected]
    started=time.monotonic();proof=None;states=[];directory=None;home_directory=None;build=None
    try:
        build=json.loads(args.build_receipt.read_text()) if args.build_receipt else clean_build_receipt(args)
        assert build['success'] and build['source_stable'] and source_hashes() == build['go_source_sha256'], 'Image/source binding differs'
        report['build_receipt_sha256']=hashlib.sha256(args.build_receipt.read_bytes()).hexdigest() if args.build_receipt else None
        report['build_binding']=build.get('scope')
        report['source_sha256']={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in
            ('TESTS/adapters/go/storage_tls.py','TESTS/adapters/django/storage_tls.py','TESTS/contracts/test_storage_tls.py',
             'TESTS/check_storage_ca_bundle.py','BE/requirements.txt')}
        with tempfile.TemporaryDirectory(prefix='az-storage-tls-http-',dir='/tmp') as name, \
             tempfile.TemporaryDirectory(prefix='tilde-',dir=args.output) as home_name, \
             (args.output/'services.log').open('w') as log:
            directory=Path(name);public=directory/'public';public.mkdir();public.chmod(0o755)
            good,key=certificate(directory,'good');wrong,_=certificate(directory,'wrong')
            for name,body in {'good.pem':good.read_bytes(),'wrong.pem':wrong.read_bytes(),'invalid.pem':b'not a certificate',
                              '$CA_ALIAS':good.read_bytes(),'certificate with spaces.pem':good.read_bytes(),'false':good.read_bytes()}.items():
                (public/name).write_bytes(body);(public/name).chmod(0o644)
            home_directory=Path(home_name);tilde_file=home_directory/'good.pem';tilde_file.write_bytes(good.read_bytes())
            tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
            proof=ImageFixture(args.image,build['revision'],directory,args.output,log)
            try:
                proof.start_services(args.postgres,args.redis)
                assert proof.image_id == build['immutable_id']
                initialize_current_schema(directory,'127.0.0.1',proof.postgres_port,DATABASE,log)
                from django.apps import apps
                from django.conf import settings
                proof.private_values.update((settings.SECRET_KEY,settings.JWT_SECRET_KEY,'fixture-secret'))
                proof.private_values.add(base64.b64encode(settings.JWT_SECRET_KEY.encode()).decode())
                def snapshot():
                    return {model._meta.label:list(model._base_manager.order_by('pk').values()) for model in apps.get_models(include_auto_created=True)
                            if model._meta.managed and not model._meta.proxy}
                original=snapshot();report['managed_models_including_auto_created']=len(original)
                executable=directory/'server';build_server(executable,log,tool_env)
                with source(good,key,bind='0.0.0.0') as (tls_endpoint,tls_state), source(bind='0.0.0.0') as (http_endpoint,http_state):
                    states=[tls_state,http_state]
                    for backend in ('native','packaged'):
                        report['rows'][backend]=[]
                        for case in selected:
                            # Keep every server's mounted profiles immutable for
                            # its complete lifetime, including stopped containers
                            # awaiting final teardown. Verify exact container bytes.
                            parent=public/backend;parent.mkdir(exist_ok=True);parent.chmod(0o755)
                            folder=parent/case['name'];folder.mkdir();folder.chmod(0o755)
                            root=str(public) if backend=='native' else '/fixture'
                            paths={key:root+'/'+name for key,name in {'good':'good.pem','wrong':'wrong.pem','missing':'missing.pem','invalid':'invalid.pem',
                                'literal-dollar':'$CA_ALIAS','literal-space':'certificate with spaces.pem','literal-false':'false'}.items()}
                            paths.update(empty='',directory=root,tilde='~/'+str(tilde_file.relative_to(Path.home())) if backend=='native' else '~assozeta/good.pem',
                                         **{'unexpanded-dollar':root+'/$UNEXPANDED_CA','trailing-space':root+'/good.pem '})
                            config='['+('profile chosen' if case['profile_credentials'] else 'default')+']\n'
                            if case['profile_ca_configured']:config+='ca_bundle = '+paths[case['profile_ca']]+'\n'
                            (folder/'config').write_text(config)
                            (folder/'credentials').write_text('['+('chosen' if case['profile_credentials'] else 'default')+']\naws_access_key_id=fixture-access\naws_secret_access_key=fixture-secret\n')
                            for file in folder.iterdir():file.chmod(0o644)
                            endpoint=tls_endpoint if case['tls'] else http_endpoint
                            if backend=='packaged':endpoint=endpoint.replace('127.0.0.1','host.docker.internal')
                            env={'DBTYPE':'POSTGRES','DBNAME':DATABASE,'DBUSER':'contracts','DBPASSWORD':'synthetic-only',
                                 'DBHOST':'127.0.0.1' if backend=='native' else 'postgres','DBPORT':str(proof.postgres_port) if backend=='native' else '5432',
                                 'REDIS_HOST':'127.0.0.1' if backend=='native' else 'redis','REDIS_PORT':str(proof.redis_port) if backend=='native' else '6379',
                                 'REDIS_USERNAME':'default','REDIS_PASSWORD':'synthetic-identity-only','GO_LISTEN_ADDR':':8000',
                                 'DEBUG':'False','RATELIMIT_ENABLE':'False','DATA_RESTORE_LOCK_PATH':str(directory/'operation.lock') if backend=='native' else '/tmp/operation.lock',
                                 'AWS_ACCESS_KEY_ID':'' if case['profile_credentials'] else 'fixture-access','AWS_SECRET_ACCESS_KEY':'' if case['profile_credentials'] else 'fixture-secret',
                                 'AWS_STORAGE_BUCKET_NAME':'private','AWS_S3_REGION_NAME':'us-east-1','AWS_S3_ENDPOINT_URL':endpoint,'AWS_LOCATION':'',
                                 'AWS_CONFIG_FILE':root+'/'+backend+'/'+case['name']+'/config','AWS_SHARED_CREDENTIALS_FILE':root+'/'+backend+'/'+case['name']+'/credentials','AWS_EC2_METADATA_DISABLED':'true',
                                 'SECRET_KEY':settings.SECRET_KEY,'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'',
                                 **{name+'_B64':base64.b64encode(getattr(settings,name).encode()).decode() for name in ('JWT_SECRET_KEY','JWT_PUBLIC_KEY')},
                                 **{name:paths[value] for name,value in case['environment'].items()}}
                            if case['profile_credentials']:env['AWS_S3_SESSION_PROFILE']='chosen'
                            before_requests=sum(len(state['requests']) for state in states)
                            before_per_state=[len(state['requests']) for state in states]
                            report['active_case']={'backend':backend,'name':case['name']}
                            factory=native(executable,{**tool_env,**env},directory,log,report) if backend=='native' else packaged(proof,env,public,directory,case['name'])
                            with factory as base:
                                assert sum(len(state['requests']) for state in states)==before_requests,'Storage initialized during unrelated readiness'
                                client=Client(base,timeout=45)
                                status,body,headers=client.request('OPTIONS','/instance/logo.png',raw=True,include_headers=True)
                                assert status==200 and sum(len(state['requests']) for state in states)==before_requests,'OPTIONS consulted storage'
                                status,body,headers=client.request('GET','/instance/logo.png',headers={'Accept-Encoding':'identity','Accept-Language':'en'},raw=True,include_headers=True)
                                expected=200 if case['expected']['read'] else 500
                                report['active_case'].update(status=status,expected_status=expected,
                                    provider_requests=[row for state,before in zip(states,before_per_state) for row in state['requests'][before:]])
                                assert status==expected,(backend,case['name'],'HTTPstatus',status,expected)
                                if expected==200:
                                    assert body==PAYLOAD and headers.get('Content-Type')=='image/png'
                                    assert headers.get('Cache-Control')=='no-cache' and headers.get('Content-Disposition')=='inline; filename="logo.png"'
                                    head,empty,head_headers=client.request('HEAD','/instance/logo.png',headers={'Accept-Encoding':'identity'},raw=True,include_headers=True)
                                    assert head==200 and empty==b'' and head_headers.get('Content-Length')==str(len(PAYLOAD))
                                else:
                                    assert b'Server Error (500)' in body and headers.get('Content-Type')=='text/html; charset=utf-8'
                                for private in ('fixture-secret','private-ca',root,'BEGIN CERTIFICATE'):
                                    assert private.encode() not in body,'Storage error exposed private details'
                                assert Client(base).request('GET','/readyz')[0]==200,'Logo trust failure poisoned readiness'
                            assert snapshot()==original,'TLS logo reads changed managed state'
                            report['rows'][backend].append({'name':case['name'],'status':status,'body_sha256':hashlib.sha256(body).hexdigest(),
                                'unrelated_startup_lazy':True,'options_without_storage':True,'managed_state_unchanged':True,'readiness_after_request':True})
                        assert [row['name'] for row in report['rows'][backend]]==[row['name'] for row in selected]
                    assert all(row['valid_identity_and_key'] for state in states for row in state['requests'])
                    report['independently_verified_storage_requests']=sum(len(state['requests']) for state in states)
                    report.pop('active_case',None)
                report['managed_state_unchanged']=snapshot()==original
                report['checks_passed']=True
            finally:
                try:
                    if 'original' in locals():report['managed_state_unchanged']=snapshot()==original
                finally:
                    try:
                        if 'django.db' in sys.modules:
                            from django.db import connections
                            connections.close_all()
                    finally:proof.cleanup()
    except BaseException as error:
        report['failure_type']=type(error).__name__
        raise
    finally:
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        report['private_services_stopped']=bool(states) and all(state['stopped'] for state in states) and proof is not None and proof.cleanup_verified
        report['private_files_removed']=directory is not None and not directory.exists() and home_directory is not None and not home_directory.exists()
        report['image']=proof.metadata if proof else None
        report['source_stable']=bool(build is not None and 'go_source_sha256' in build) and all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest for path,digest in report.get('source_sha256',{}).items()) and source_hashes()==build['go_source_sha256']
        report['private_log_redaction_passed']=True
        native_log=args.output/'services.log'
        if proof is not None and native_log.exists():
            report['private_log_redaction_passed']=not proof.safe_log(native_log,native_log.read_text())
        report['success']=bool(report.get('checks_passed') and report['managed_state_unchanged'] and report['private_services_stopped'] and report['private_files_removed'] and report['native_processes_stopped'] and report['source_stable'] and report['private_log_redaction_passed'])
        receipt.write_text(json.dumps(report,indent=2)+'\n')
    assert report['success']


if __name__=='__main__':main()
