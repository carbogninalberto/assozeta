"""Actual Boto3 AssumeRole against a private, signature-checking STS server."""
from datetime import datetime, timedelta, timezone
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

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
ARN='arn:aws:iam::123456789012:role/'


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    from django.conf import settings
    settings.configure()
    from storages.backends.s3 import S3Storage
    out=ROOT/'quality-reports/go-migration/storage-roles';out.mkdir(parents=True,exist_ok=True)
    (out/'results.json').write_text('{"success":false}\n')
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    source='[source]\naws_access_key_id=source-access\naws_secret_access_key=source-secret\naws_session_token=source-token\n'
    target='[profile target]\nrole_arn='+ARN+'target\nsource_profile=source\n'
    cases=[]
    def request(role='target', signer='source', session='generated', duration=None, external=None, serial=None, code=None):
        return dict(Role=role,Signer=signer,Session=session,Duration=duration,External=external,Serial=serial,Code=code)
    def add(name,lines='',config=None,credentials=source,env=None,want=None,error=False,mode='ok',mfa=None,attempts=None,delay=0,metadata=None):
        cases.append(dict(Name=name,Config=(target if config is None else config)+lines,Credentials=credentials,
            Environment=env or {},Expected=[request()] if want is None else want,Error=error,Mode=mode,MFA=mfa,Attempts=attempts or ([True] if error else [False,False]),Delay=delay,Metadata=metadata))
    add('omitted duration and session')
    add('explicit session',lines='role_session_name=fixture-session\n',want=[request(session='fixture-session')])
    add('empty session generates default',lines='role_session_name=\n')
    for raw,seconds in [('900',900),('901',901),('3600',3600),('43200',43200),('not-a-number',None),('',None),('3_600',3600),('３６００',3600),('+0900',900),('٩٠١',901),('9__00',None),('900_',None),('_900',None),('900.0',None)]:
        add('duration '+raw,lines='duration_seconds='+raw+'\n',want=[request(duration=seconds)])
    for raw in ('0','-1','899'):
        add('invalid duration '+raw,lines='duration_seconds='+raw+'\n',want=[],error=True)
    add('quoted profile',config=target.replace('[profile target]', '[profile \"target\"]'),lines='duration_seconds=901\nrole_session_name=fixture-session\n',want=[request(duration=901,session='fixture-session')])
    add('quoted credentials are literal',config='',credentials='[\"target\"]\naws_access_key_id=target-access\naws_secret_access_key=target-secret\naws_session_token=target-token\n',want=[],error=True)
    add('colon role parameters',lines='duration_seconds: 901\nrole_session_name: fixture-session\nexternal_id: fixture-external\n',want=[request(duration=901,session='fixture-session',external='fixture-external')])
    add('short role ARN',config=target.replace(ARN+'target','short'),want=[],error=True)
    add('external ID',lines='external_id=fixture-external\n',want=[request(external='fixture-external')])
    add('empty external ID is invalid',lines='external_id=\n',want=[],error=True)
    add('short external ID is invalid',lines='external_id=x\n',want=[],error=True)
    add('short session is invalid',lines='role_session_name=x\n',want=[],error=True)
    add('source profile missing',config='[profile target]\nrole_arn='+ARN+'target\nsource_profile=missing\n',want=[],error=True)
    add('no source',config='[profile target]\nrole_arn='+ARN+'target\n',want=[],error=True)
    add('conflicting sources',lines='credential_source=Environment\n',want=[],error=True)
    add('cyclic source',config='[profile target]\nrole_arn='+ARN+'target\nsource_profile=target\n',want=[],error=True)
    add('environment source',config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=Environment\n',env={'AWS_ACCESS_KEY_ID':'env-access','AWS_SECRET_ACCESS_KEY':'env-secret','AWS_SESSION_TOKEN':'env-token'},want=[request(signer='env')])
    add('legacy environment token',config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=Environment\n',env={'AWS_ACCESS_KEY_ID':'env-access','AWS_SECRET_ACCESS_KEY':'env-secret','AWS_SESSION_TOKEN':'wrong-token','AWS_SECURITY_TOKEN':'env-token'},want=[request(signer='env')])
    add('empty environment source',config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=Environment\n',want=[],error=True)
    add('nested roles',config='[profile target]\nrole_arn='+ARN+'target\nsource_profile=middle\n[profile middle]\nrole_arn='+ARN+'middle\nsource_profile=source\n',want=[request('middle'),request(signer='middle')])
    for mode in ('denied','malformed','missing-credentials','missing-expiry','missing-access','missing-secret','missing-token','expired'):
        add('provider '+mode,mode=mode,error=True)
    serial='arn:aws:iam::123456789012:mfa/fixture'
    for name,device,code,bad in [
        ('valid',serial,'012345',False),('hardware','GAHT12345678','012345',False),
        ('short serial','12345678','012345',True),('empty serial','','012345',True),
        ('short code',serial,'12345',True),('empty code',serial,'',True),
        ('Unicode code',serial,'１２３４５６',False),('long code',serial,'1234567',False),
        ('literal code',serial,'abcdef',False),
    ]:
        add('MFA '+name,lines='mfa_serial='+device+'\n',mfa=dict(Code=code,Serials=[device]),error=bad,
            want=[] if bad else [request(serial=device,code=code)])
    for name,lines in [('invalid duration','duration_seconds=0\n'),('short session','role_session_name=x\n'),('empty external ID','external_id=\n')]:
        add('MFA '+name,lines=lines+'mfa_serial='+serial+'\n',mfa=dict(Code='012345',Serials=[serial]),error=True,want=[])
    add('MFA callback error',lines='mfa_serial='+serial+'\n',mfa=dict(Code='',Serials=[serial],Fail=True),error=True,want=[],attempts=[True,True])
    add('MFA denied is not reprompted',lines='mfa_serial='+serial+'\n',mfa=dict(Code='012345',Serials=[serial]),mode='denied',error=True,attempts=[True,True],want=[request(serial=serial,code='012345')])
    add('MFA expired response is not reprompted',lines='mfa_serial='+serial+'\n',mfa=dict(Code='012345',Serials=[serial]),mode='expired',error=True,attempts=[True,True],want=[request(serial=serial,code='012345')])
    add('MFA refresh requires new provider',lines='mfa_serial='+serial+'\n',mfa=dict(Code='012345',Serials=[serial]),mode='expiring',attempts=[False,True],delay=2.2,want=[request(serial=serial,code='012345')])
    middle='arn:aws:iam::123456789012:mfa/middle'
    add('MFA nested roles',config='[profile target]\nrole_arn='+ARN+'target\nsource_profile=middle\nmfa_serial='+serial+'\n[profile middle]\nrole_arn='+ARN+'middle\nsource_profile=source\nmfa_serial='+middle+'\n',mfa=dict(Code='012345',Serials=[serial,middle]),want=[request('middle',serial=middle,code='012345'),request(signer='middle',serial=serial,code='012345')])
    add('MFA environment source',config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=Environment\nmfa_serial='+serial+'\n',env={'AWS_ACCESS_KEY_ID':'env-access','AWS_SECRET_ACCESS_KEY':'env-secret','AWS_SESSION_TOKEN':'env-token'},mfa=dict(Code='012345',Serials=[serial]),want=[request(signer='env',serial=serial,code='012345')])
    for kind in ('EcsContainer','Ec2InstanceMetadata'):
        config='[profile target]\nrole_arn='+ARN+'target\ncredential_source='+kind+'\n'
        add(kind+' role',config=config,metadata=dict(Kind=kind))
        add(kind+' role with MFA',config=config+'mfa_serial='+serial+'\n',metadata=dict(Kind=kind),mfa=dict(Code='012345',Serials=[serial]),want=[request(serial=serial,code='012345')])
        add(kind+' STS denial',config=config,metadata=dict(Kind=kind),mode='denied',error=True)
    for kind in ('EcsContainer','Ec2InstanceMetadata'):
        config='[profile target]\nrole_arn='+ARN+'target\nsource_profile=middle\n[profile middle]\nrole_arn='+ARN+'middle\ncredential_source='+kind+'\n'
        add(kind+' nested roles',config=config,metadata=dict(Kind=kind),want=[request('middle'),request(signer='middle')])
    for name,extra in [('escaped prefix',dict(Prefix='proxy%20folder/%2Ftenant')),('repeated slash prefix',dict(Prefix='proxy//tenant/')),('profile endpoint',dict(ProfileEndpoint=True)),('environment endpoint wins',dict(ConflictingEndpoint=True))]:
        add('metadata '+name,config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=Ec2InstanceMetadata\n',metadata=dict(Kind='Ec2InstanceMetadata',**extra))
    add('container token file precedence',config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=EcsContainer\n',metadata=dict(Kind='EcsContainer',TokenFile=True))
    add('container endpoint missing',config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=EcsContainer\n',error=True,want=[])
    for mode in ('disabled','v1-fallback','v1-blocked','token-bad','empty-list','missing-access'):
        expected=[] if mode!='v1-fallback' else [request()]
        add('metadata '+mode,config='[profile target]\nrole_arn='+ARN+'target\ncredential_source=Ec2InstanceMetadata\n',metadata=dict(Kind='Ec2InstanceMetadata',Mode=mode),error=mode!='v1-fallback',want=expected)
    observed={};metadata_observed={};problems=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def metadata_request(self):
            path=urlsplit(self.path).path.strip('/').split('/')
            if len(path)<3 or not path[0].isdigit() or path[1] not in ('django','go'):
                problems.append(('unrouted metadata fixture request',self.command,urlsplit(self.path).path))
                self.send_error(404);return
            index=int(path[0]);backend=path[1];case=cases[index];spec=case['Metadata']
            if spec is None:
                problems.append((case['Name'],backend,'unexpected metadata request'));self.send_error(404);return
            route='/'.join(path[2:]);mode=spec.get('Mode','ok');status=200
            prefix=spec.get('Prefix','')
            if prefix:
                prefix=prefix if prefix.endswith('/') else prefix+'/'
                if not route.startswith(prefix):problems.append((case['Name'],backend,'metadata prefix differs'))
                else:route=route[len(prefix):]
            records=metadata_observed.setdefault((index,backend),[])
            headers={};body=b''
            if spec['Kind']=='EcsContainer':
                records.append('container')
                if route!='container' or self.command!='GET' or self.headers.get('Authorization')!='synthetic-container-token':problems.append((case['Name'],backend,'container request differs'))
            elif route=='latest/api/token' and self.command=='PUT':
                records.append('token')
                # Both are valid IMDSv2 lifetimes. SDK caches its token for five
                # minutes; Boto3 requests six hours but refetches it each refresh.
                ttl='21600' if backend=='django' else '300'
                if self.headers.get('X-aws-ec2-metadata-token-ttl-seconds')!=ttl:problems.append((case['Name'],backend,'metadata TTL differs'))
                status=403 if mode in ('v1-fallback','v1-blocked') else 400 if mode=='token-bad' else 200
                body=b'synthetic-metadata-token';headers['X-aws-ec2-metadata-token-ttl-seconds']=ttl
            else:
                expected_token=None if mode=='v1-fallback' else 'synthetic-metadata-token'
                if self.command!='GET' or self.headers.get('X-aws-ec2-metadata-token')!=expected_token:problems.append((case['Name'],backend,'metadata token differs'))
                if route=='latest/meta-data/iam/security-credentials':
                    records.append('list');body=b'' if mode=='empty-list' else b'source'
                elif route=='latest/meta-data/iam/security-credentials/source':records.append('credentials')
                else:problems.append((case['Name'],backend,'unexpected metadata route'));status=404
            if records and records[-1] in ('container','credentials'):
                values=dict(Code='Success',AccessKeyId='source-access',SecretAccessKey='source-secret',Token='source-token',Expiration='2099-01-01T00:00:00Z')
                if mode=='missing-access':values.pop('AccessKeyId')
                body=json.dumps(values).encode();headers['Content-Type']='application/json'
            self.send_response(status)
            for key,value in headers.items():self.send_header(key,value)
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        do_GET=metadata_request
        do_PUT=metadata_request
        def do_POST(self):
            path=urlsplit(self.path).path.strip('/').split('/')
            if len(path)!=2 or not path[0].isdigit() or path[1] not in ('django','go'):
                self.send_error(404);return
            index=int(path[0]);backend=path[1];case=cases[index]
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            form={k:v[0] for k,v in parse_qs(body.decode(),keep_blank_values=True).items()}
            records=observed.setdefault((index,backend),[])
            position=len(records);records.append(form)
            issue=None
            if position>=len(case['Expected']):issue='unexpected request count'
            else:
                wanted=case['Expected'][position]
                expected={'Action':'AssumeRole','Version':'2011-06-15','RoleArn':ARN+wanted['Role']}
                if wanted['Duration'] is not None:expected['DurationSeconds']=str(wanted['Duration'])
                if wanted['External'] is not None:expected['ExternalId']=wanted['External']
                if wanted['Serial'] is not None:expected.update(SerialNumber=wanted['Serial'],TokenCode=wanted['Code'])
                session=form.get('RoleSessionName','')
                if wanted['Session']=='generated':
                    if not re.fullmatch(r'botocore-session-[0-9]+',session) or abs(int(session.removeprefix('botocore-session-'))-time.time())>30:issue='generated session name differs'
                    expected['RoleSessionName']=session
                else:expected['RoleSessionName']=wanted['Session']
                if form!=expected:issue='request fields differ'
                auth=re.fullmatch(r'AWS4-HMAC-SHA256 Credential=([^/]+)/([^,]+),\s*SignedHeaders=([^,]+),\s*Signature=([0-9a-f]+)',self.headers.get('Authorization',''))
                if auth is None:issue='missing signature'
                else:
                    access,scope,headers,signature=auth.groups()
                    date,region,service,terminal=scope.split('/')
                    expected_token=wanted['Signer']+'-token'
                    if access!=wanted['Signer']+'-access' or self.headers.get('X-Amz-Security-Token')!=expected_token:issue='wrong signing identity'
                    if region!='us-east-1' or service!='sts' or terminal!='aws4_request':issue='wrong signing scope'
                    canonical_headers=''.join(name+':'+' '.join(self.headers.get(name,'').split())+'\n' for name in headers.split(';'))
                    canonical='POST\n'+urlsplit(self.path).path+'\n\n'+canonical_headers+'\n'+headers+'\n'+hashlib.sha256(body).hexdigest()
                    signed='AWS4-HMAC-SHA256\n'+self.headers.get('X-Amz-Date','')+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
                    key=('AWS4'+wanted['Signer']+'-secret').encode()
                    for part in (date,region,service,terminal):key=hmac.new(key,part.encode(),hashlib.sha256).digest()
                    if not hmac.compare_digest(hmac.new(key,signed.encode(),hashlib.sha256).hexdigest(),signature):issue='signature mismatch'
            if issue:problems.append((case['Name'],backend,issue))
            mode=case['Mode'];status=403 if mode=='denied' or issue else 200
            role=form.get('RoleArn','').rsplit('/',1)[-1]
            expiry='2000-01-01T00:00:00Z' if mode=='expired' else '2099-01-01T00:00:00Z'
            if mode=='expiring':expiry=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat()
            credentials='<Credentials><AccessKeyId>'+role+'-access</AccessKeyId><SecretAccessKey>'+role+'-secret</SecretAccessKey><SessionToken>'+role+'-token</SessionToken>'+('' if mode=='missing-expiry' else '<Expiration>'+expiry+'</Expiration>')+'</Credentials>'
            for key, field in [('access','AccessKeyId'),('secret','SecretAccessKey'),('token','SessionToken')]:
                if mode=='missing-'+key:credentials=re.sub('<'+field+'>.*?</'+field+'>','',credentials)
            data=('<AssumeRoleResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><AssumeRoleResult>'+('' if mode=='missing-credentials' else credentials)+'</AssumeRoleResult></AssumeRoleResponse>').encode()
            if mode=='malformed':data=b'not XML sensitive-provider-message'
            if status==403:data=b'<ErrorResponse><Error><Code>AccessDenied</Code><Message>sensitive-provider-message</Message></Error></ErrorResponse>'
            self.send_response(status);self.send_header('Content-Type','text/xml');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    def check_metadata(index,case,backend):
        spec=case['Metadata'];expected=[]
        if spec:
            if spec['Kind']=='EcsContainer':expected=['container']
            else:
                mode=spec.get('Mode','ok')
                if mode!='disabled':
                    expected=['token']
                    if mode not in ('v1-blocked','token-bad'):
                        expected.append('list')
                        if mode!='empty-list':expected.append('credentials')
        actual=metadata_observed.get((index,backend),[])
        assert actual==expected,(case['Name'],backend,'metadata requests differ',actual)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='az-storage-roles-') as directory:
            work=Path(directory)
            for index,case in enumerate(cases):
                folder=work/str(index);folder.mkdir()
                for name,content in [('config',case.pop('Config')),('credentials',case.pop('Credentials')),('boto','')]:
                    (folder/name).write_text(content);(folder/name).chmod(0o600)
                env={'PATH':os.defpath,'AWS_CONFIG_FILE':str(folder/'config'),'AWS_SHARED_CREDENTIALS_FILE':str(folder/'credentials'),'BOTO_CONFIG':str(folder/'boto'),
                    'AWS_EC2_METADATA_DISABLED':'true','AWS_REGION':'us-east-1','AWS_DEFAULT_REGION':'us-east-1','AWS_MAX_ATTEMPTS':'1',**case['Environment']}
                endpoint=f'http://127.0.0.1:{server.server_port}/{index}/'
                spec=case['Metadata']
                if spec:
                    if spec['Kind']=='EcsContainer':
                        env['AWS_CONTAINER_CREDENTIALS_FULL_URI']=endpoint+'django/container'
                        env['AWS_CONTAINER_AUTHORIZATION_TOKEN']='synthetic-container-token'
                        if spec.get('TokenFile'):
                            (folder/'token').write_text('synthetic-container-token');(folder/'token').chmod(0o600)
                            env['AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE']=str(folder/'token')
                            env['AWS_CONTAINER_AUTHORIZATION_TOKEN']='wrong-container-token'
                    else:
                        env['AWS_EC2_METADATA_DISABLED']='true' if spec.get('Mode')=='disabled' else 'false'
                        metadata_endpoint=endpoint+'django'+('/'+spec['Prefix'] if spec.get('Prefix') else '')
                        env['AWS_EC2_METADATA_SERVICE_ENDPOINT']=metadata_endpoint
                        if spec.get('ProfileEndpoint') or spec.get('ConflictingEndpoint'):
                            profile_endpoint=metadata_endpoint+('/unselected' if spec.get('ConflictingEndpoint') else '')
                            with (folder/'config').open('a') as config_file:config_file.write('ec2_metadata_service_endpoint='+profile_endpoint+'\n')
                            if spec.get('ProfileEndpoint'):env.pop('AWS_EC2_METADATA_SERVICE_ENDPOINT')
                        if spec.get('Mode')=='v1-blocked':env['AWS_EC2_METADATA_V1_DISABLED']='true'

                env['AWS_ENDPOINT_URL_STS']=endpoint+'django'
                prompts=[]
                def prompt(message):
                    mfa=case['MFA']
                    assert mfa is not None, 'unexpected MFA prompt'
                    position=len(prompts)
                    assert position<len(mfa['Serials']), 'MFA reprompted'
                    assert message=='Enter MFA code for '+mfa['Serials'][position]+': ', 'wrong MFA serial'
                    prompts.append(True)
                    if mfa.get('Fail'):raise RuntimeError('sensitive-mfa-message')
                    return mfa['Code']
                with patch.dict(os.environ,env,clear=True):
                    session=None
                    credentials=None
                    for attempt,expected_error in enumerate(case['Attempts']):
                        if attempt and case['Delay']:time.sleep(case['Delay'])
                        failed=False
                        try:
                            if session is None:
                                session=S3Storage(access_key='',secret_key='',session_profile='target')._create_session()
                                session._session.get_component('credential_provider').get_provider('assume-role')._prompter=prompt
                            if credentials is None:credentials=session.get_credentials()
                            frozen=credentials.get_frozen_credentials()
                            assert [frozen.access_key,frozen.secret_key,frozen.token]==['target-access','target-secret','target-token'], 'Django returned wrong role'
                        except AssertionError:raise
                        except Exception:failed=True
                        assert failed==expected_error,(case['Name'],'Django result differs',attempt)
                assert len(prompts)==(len(case['MFA']['Serials']) if case['MFA'] else 0),(case['Name'],'Django prompt count')
                assert len(observed.get((index,'django'),[]))==len(case['Expected']),(case['Name'],'Django request count differs')
                assert not problems,problems
                case['Environment']={k:v.replace(endpoint+'django',endpoint+'go') for k,v in env.items()}
                if spec and (spec.get('ProfileEndpoint') or spec.get('ConflictingEndpoint')):
                    (folder/'go-config').write_text((folder/'config').read_text().replace(endpoint+'django',endpoint+'go'));(folder/'go-config').chmod(0o600)
                    case['Environment']['AWS_CONFIG_FILE']=str(folder/'go-config')
                check_metadata(index,case,'django')
            (out/'django-results.json').write_text(json.dumps({'success':True,'cases':len(cases)})+'\n')
            fixture=work/'cases.json';fixture.write_text(json.dumps(cases));fixture.chmod(0o600)
            run=subprocess.run(['go','test','-race','-tags=storagerolesintegration','./pkg/storage/s3credentials','-run','^TestDjangoAssumeRole$','-json'],cwd=ROOT/'BE_GO',
                env={**tool_env,'ASSOZETA_STORAGE_ROLE_FIXTURE':str(fixture)},capture_output=True,text=True,timeout=180)
            assert all(value not in run.stdout+run.stderr for value in ('sensitive-provider-message','sensitive-mfa-message')), 'provider response leaked'
            (out/'go-test.jsonl').write_text(run.stdout)
            events=[json.loads(line) for line in run.stdout.splitlines()]
            for event in events:
                if event.get('Output'):print(event['Output'],end='')
            if run.stderr:print(run.stderr)
            if problems:print('Protocol differences:',problems)
            run.check_returncode()
            assert not problems,problems
            for index,case in enumerate(cases):
                assert len(observed.get((index,'go'),[]))==len(case['Expected']),(case['Name'],'Go request count differs')
                check_metadata(index,case,'go')
            assert sum(e['Action']=='pass' and '/' in e.get('Test','') for e in events)==len(cases)
            assert not any(e['Action']=='skip' for e in events)
        result={'success':True,'cases':len(cases),'scope':'real local STS requests and independently verified signatures; no live AWS access'}
        (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__':main()
