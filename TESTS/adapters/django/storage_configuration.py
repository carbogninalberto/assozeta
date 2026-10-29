"""Actual core.settings storage assignments and installed S3 backend options.

Only synthetic environment values are used; no profile files or providers opened.
"""
import ast
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    import environ
    from django.conf import settings
    from django.test import override_settings
    from storages.backends.s3 import S3Storage
    settings.configure()
    out=ROOT/'quality-reports/go-migration/storage-configuration';out.mkdir(parents=True,exist_ok=True)
    (out/'results.json').write_text('{"success":false}\n')
    source=ast.parse((ROOT/'BE/core/settings.py').read_text())
    def assigns(node,name):return isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)
    start=next(i for i,node in enumerate(source.body) if assigns(node,'STORE_ON_DISK'))
    end=next(i for i,node in enumerate(source.body) if assigns(node,'STORAGES'))
    nodes=source.body[start:end+1];assert len(nodes)==18,'storage declaration coverage changed'
    code=compile(ast.Module(body=nodes,type_ignores=[]),'core.settings storage assignments','exec')
    base={'SECRET_KEY':'fixture-session','DEBUG':'False','DBTYPE':'POSTGRES','DBNAME':'fixture','DBUSER':'fixture','DBHOST':'localhost',
        'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'','JWT_SECRET_KEY':'fixture-private','JWT_PUBLIC_KEY':'fixture-public',
        'AWS_ACCESS_KEY_ID':'fixture-access','AWS_SECRET_ACCESS_KEY':'sensitive-fixture-secret','AWS_STORAGE_BUCKET_NAME':'fixture-bucket',
        'AWS_S3_REGION_NAME':'us-east-1','AWS_S3_ENDPOINT_URL':'http://127.0.0.1:9000','AWS_LOCATION':'private'}
    expected={'AccessKey':'fixture-access','SecretKey':'sensitive-fixture-secret','Bucket':'fixture-bucket','Region':'us-east-1','Endpoint':'http://127.0.0.1:9000','Location':'private',
        'DefaultACL':None,'FileOverwrite':False,'UseObjectACL':False,'PublicBaseURL':'','StoreOnDisk':False,'StorageDir':'','Backend':'storages.backends.s3boto3.S3Boto3Storage',
        'CacheControl':'max-age=86400','SessionToken':None,'SessionProfile':None}
    cases=[]
    def add(name,*,lines='',process=None,remove=(),want=None,error=False,backend_error=False):
        values={key:value for key,value in base.items() if key not in remove}
        cases.append(dict(Name=name,File=''.join(k+'='+v+'\n' for k,v in values.items())+lines,Process=process or {},Expected={**expected,**(want or {})},Error=error,BackendError=backend_error))
    add('ordinary self-host defaults')
    for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_STORAGE_BUCKET_NAME','AWS_S3_REGION_NAME','AWS_S3_ENDPOINT_URL','AWS_LOCATION'):
        add('missing required '+key,remove=(key,),error=True)
        label={'AWS_ACCESS_KEY_ID':'AccessKey','AWS_SECRET_ACCESS_KEY':'SecretKey','AWS_STORAGE_BUCKET_NAME':'Bucket','AWS_S3_REGION_NAME':'Region','AWS_S3_ENDPOINT_URL':'Endpoint','AWS_LOCATION':'Location'}[key]
        add('empty required '+key,lines=key+'=\n',want={label:''})
    add('all string proxies',lines='AWS_ACCESS_KEY_ID=$ACCESS\nACCESS=proxy-access\nAWS_SECRET_ACCESS_KEY=$SECRET\nSECRET=proxy-secret\nAWS_LOCATION=$PREFIX\nPREFIX=proxy/path\n',want={'AccessKey':'proxy-access','SecretKey':'proxy-secret','Location':'proxy/path'})
    add('missing secret proxy redacted',lines='AWS_SECRET_ACCESS_KEY=$sensitive-fixture-secret\n',error=True)
    add('secret proxy cycle',lines='AWS_SECRET_ACCESS_KEY=$LOOP\nLOOP=$AWS_SECRET_ACCESS_KEY\n',error=True)
    add('process beats file',lines='AWS_LOCATION=file\n',process={'AWS_LOCATION':'process'},want={'Location':'process'})
    add('empty process beats file',process={'AWS_LOCATION':''},want={'Location':''})
    add('quoted literal secret',lines="AWS_SECRET_ACCESS_KEY=' value # literal  '\n",want={'SecretKey':' value # literal  '})
    add('secret escapes remain literal',lines=r'AWS_SECRET_ACCESS_KEY="a\nb\t"'+'\n',want={'SecretKey':r'a\nb\t'})
    for raw,want in (('',None),('private','private'),('None','None'),('$MISSING',None)):
        add('ACL '+raw,lines='AWS_DEFAULT_ACL='+raw+'\n',want={'DefaultACL':want})
    add('ACL proxy',lines='AWS_DEFAULT_ACL=$ACL\nACL=public-read\n',want={'DefaultACL':'public-read'})
    add('ACL cycle',lines='AWS_DEFAULT_ACL=$ACL\nACL=$AWS_DEFAULT_ACL\n',error=True)
    for raw,want in (('',False),('False',False),('yes',True),('-2',True),('０１',True),(' 0 ',False),('enabled',False)):
        add('boolean '+raw,lines=''.join(k+'='+raw+'\n' for k in ('STORE_ON_DISK','AWS_S3_FILE_OVERWRITE','AWS_S3_USE_OBJECT_ACL')),want={'StoreOnDisk':want,'FileOverwrite':want,'UseObjectACL':want})
    add('disk still uses S3',lines='STORE_ON_DISK=True\nSTORAGE_DIR=/ignored/directory\n',want={'StoreOnDisk':True})
    add('unused directory proxy still validated',lines='STORAGE_DIR=$DIR\nDIR=$STORAGE_DIR\n',error=True)
    add('missing directory proxy gets default',lines='STORAGE_DIR=$MISSING\n')
    add('flag proxy',lines='AWS_S3_FILE_OVERWRITE=$FLAG\nFLAG=ok\n',want={'FileOverwrite':True})
    add('flag cycle',lines='AWS_S3_FILE_OVERWRITE=$FLAG\nFLAG=$AWS_S3_FILE_OVERWRITE\n',error=True)
    for endpoint,spaces in [('https://ams3.digitaloceanspaces.com',True),('DIGITALOCEANSPACES.COM:443',True),('https://user:pass@AMS3.DIGITALOCEANSPACES.COM:bad/path',True),
        ('https://digitaloceanspaces.com.evil.test',False),('https://notdigitaloceanspaces.com',False),('https://dİgitaloceanspaces.com',False),('//ams3.digitaloceanspaces.com',False),
        ('https://ams3.digitaloceanspaces.com.',False),('http://[::1]:9000',False),('https://ams3.digitaloceanspaces.com\t/path',True)]:
        add('endpoint '+endpoint,process={'AWS_S3_ENDPOINT_URL':endpoint},want={'Endpoint':endpoint,'UseObjectACL':spaces,'PublicBaseURL':'https://fixture-bucket.us-east-1.digitaloceanspaces.com' if spaces else ''})
    for endpoint in ('http://[broken','http://[127.0.0.1]','https://host／evil.test'):
        add('invalid endpoint parse '+endpoint,process={'AWS_S3_ENDPOINT_URL':endpoint},error=True)
    add('spaces flags can override defaults',lines='AWS_S3_ENDPOINT_URL=https://ams3.digitaloceanspaces.com\nAWS_S3_USE_OBJECT_ACL=\nAWS_S3_PUBLIC_BASE_URL=\n',want={'Endpoint':'https://ams3.digitaloceanspaces.com'})
    add('missing proxy inherits spaces defaults',lines='AWS_S3_ENDPOINT_URL=https://ams3.digitaloceanspaces.com\nAWS_S3_USE_OBJECT_ACL=$MISSING\nAWS_S3_PUBLIC_BASE_URL=$MISSING\n',want={'Endpoint':'https://ams3.digitaloceanspaces.com','UseObjectACL':True,'PublicBaseURL':'https://fixture-bucket.us-east-1.digitaloceanspaces.com'})
    add('public base strips only trailing slashes',lines='AWS_S3_PUBLIC_BASE_URL=https://cdn.example.test/files///\n',want={'PublicBaseURL':'https://cdn.example.test/files'})
    add('public base proxy',lines='AWS_S3_PUBLIC_BASE_URL=$CDN\nCDN=https://cdn.example.test/\n',want={'PublicBaseURL':'https://cdn.example.test'})
    add('location remains literal',lines='AWS_LOCATION=../private///\n',want={'Location':'../private///'})
    add('absolute location is backend error',lines='AWS_LOCATION=/private\n',want={'Location':'/private'},backend_error=True)
    add('session token preferred',lines='AWS_SESSION_TOKEN=synthetic-session-token\nAWS_SECURITY_TOKEN=legacy-token\n',want={'SessionToken':'synthetic-session-token'})
    add('empty token falls back',lines='AWS_SESSION_TOKEN=\nAWS_SECURITY_TOKEN=legacy-token\n',want={'SessionToken':'legacy-token'})
    add('tokens do not resolve proxies',lines='AWS_SESSION_TOKEN=$TOKEN\nTOKEN=must-not-expand\n',want={'SessionToken':'$TOKEN'})
    add('profile without static credentials',lines='AWS_ACCESS_KEY_ID=\nAWS_SECRET_ACCESS_KEY=\nAWS_S3_SESSION_PROFILE=fixture-profile\n',want={'AccessKey':'','SecretKey':'','SessionProfile':'fixture-profile'})
    add('profile conflict is backend error',lines='AWS_S3_SESSION_PROFILE=fixture-profile\n',want={'SessionProfile':'fixture-profile'},backend_error=True)
    add('profile is literal',lines='AWS_ACCESS_KEY_ID=\nAWS_SECRET_ACCESS_KEY=\nAWS_S3_SESSION_PROFILE=$PROFILE\nPROFILE=must-not-expand\n',want={'AccessKey':'','SecretKey':'','SessionProfile':'$PROFILE'})
    add('empty profile absent',lines='AWS_S3_SESSION_PROFILE=\n')
    add('S3 credential aliases do not override settings',lines='AWS_S3_ACCESS_KEY_ID=ignored\nAWS_S3_SECRET_ACCESS_KEY=ignored\n')
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    names={'AccessKey':'AWS_ACCESS_KEY_ID','SecretKey':'AWS_SECRET_ACCESS_KEY','Bucket':'AWS_STORAGE_BUCKET_NAME','Region':'AWS_S3_REGION_NAME','Endpoint':'AWS_S3_ENDPOINT_URL','Location':'AWS_LOCATION','DefaultACL':'AWS_DEFAULT_ACL','FileOverwrite':'AWS_S3_FILE_OVERWRITE','UseObjectACL':'AWS_S3_USE_OBJECT_ACL','PublicBaseURL':'AWS_S3_PUBLIC_BASE_URL','StoreOnDisk':'STORE_ON_DISK','StorageDir':'STORAGE_DIR'}
    with patch.object(logging.getLogger('environ.environ'),'disabled',True):
        for case in cases:
            with patch.dict(os.environ,case['Process'],clear=True):
                environ.Env.read_env(io.StringIO(case['File']))
                namespace={'env':environ.Env(),'BASE_DIR':'/synthetic/base','os':os,'urlparse':urlparse}
                error=False
                try:exec(code,namespace)
                except Exception:error=True
                assert error==case['Error'],(case['Name'],'settings error differs')
                if error:continue
                actual={label:namespace[name] for label,name in names.items()}
                actual.update(Backend=namespace['STORAGES']['default']['BACKEND'],CacheControl=namespace['AWS_S3_OBJECT_PARAMETERS']['CacheControl'])
                backend_error=False
                with override_settings(**{k:v for k,v in namespace.items() if k.isupper()}):
                    # Defaults can be inspected even when constructor validation fails.
                    defaults=S3Storage.get_default_settings(object())
                    for label,field in {'AccessKey':'access_key','SecretKey':'secret_key','Bucket':'bucket_name',
                        'Region':'region_name','Endpoint':'endpoint_url','Location':'location',
                        'DefaultACL':'default_acl','FileOverwrite':'file_overwrite'}.items():
                        assert defaults[field]==actual[label],(case['Name'],'backend option differs',label)
                    assert defaults['object_parameters']=={'CacheControl':'max-age=86400'}
                    actual.update(SessionToken=defaults['security_token'],SessionProfile=defaults['session_profile'])
                    try:S3Storage()
                    except Exception:backend_error=True
                assert actual==case['Expected'],(case['Name'],'declared storage values differ')
                assert backend_error==case['BackendError'],(case['Name'],'backend validation differs')
    (out/'django-results.json').write_text(json.dumps({'success':True,'cases':len(cases)})+'\n')
    with tempfile.TemporaryDirectory(prefix='az-storage-config-') as directory:
        fixture=Path(directory)/'cases.json';fixture.write_text(json.dumps(cases));fixture.chmod(0o600)
        run=subprocess.run(['go','test','-race','-tags=storageconfigintegration','./internal/config','-run','^TestDjangoStorageConfiguration$','-json'],cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_STORAGE_CONFIG_FIXTURE':str(fixture)},capture_output=True,text=True,timeout=90)
        (out/'go-test.jsonl').write_text(run.stdout)
        if run.stderr:print(run.stderr)
        events=[json.loads(line) for line in run.stdout.splitlines()]
        for event in events:
            if event.get('Output'):print(event['Output'],end='')
        run.check_returncode()
        assert len([e for e in events if e['Action']=='pass' and '/' in e.get('Test','')])==len(cases)
        assert not any(e['Action']=='skip' for e in events)
    result={'success':True,'cases':len(cases),'scope':'actual settings/default storage option parsing; no provider or profile file opened'}
    (out/'results.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))


if __name__=='__main__':main()
