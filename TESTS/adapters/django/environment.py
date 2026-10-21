"""Compare Go configuration with selected, unchanged core.settings assignments.

The oracle executes those AST assignments with real django-environ. It never
imports settings or reads the developer .env, connects to a database or contacts
a provider. All environment/credential values are synthetic.
"""
import ast
import base64
from importlib.metadata import version
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
NAMES={'DATA_RESTORE_LOCK_PATH':'restore_lock_path','SECRET_KEY':'session_secret','DEBUG':'debug','RATELIMIT_ENABLE':'rate_enabled','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'google','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'google_secret',
       'SOCIAL_AUTH_APPLE_ID_CLIENT':'apple','SOCIAL_AUTH_APPLE_ID_TEAM':'apple_team',
       'SOCIAL_AUTH_APPLE_ID_KEY':'apple_key','SOCIAL_AUTH_APPLE_ID_SECRET':'apple_secret',
       'JWT_SECRET_KEY':'jwt_private','JWT_PUBLIC_KEY':'jwt_public','DBPASSWORD':'database_password',
       'REDIS_PASSWORD':'redis_password','CSRF_TRUSTED_ORIGINS':'origins','CORS_ORIGIN_ALLOW_ALL':'cors_all','CORS_ALLOWED_ORIGINS':'cors_origins',
       'FAMILY_REGISTRATION_ENABLED':'family','FEDERATION_REGISTRATION_ENABLED':'federation',
       'MULTIPLE_REGISTRATION_ENABLED':'multiple'}


def main():
    import environ
    out=ROOT/'quality-reports/go-migration/environment';out.mkdir(parents=True,exist_ok=True)
    (out/'results.json').write_text(json.dumps({'success':False,'phase':'started'})+'\n')
    source=ast.parse((ROOT/'BE/core/settings.py').read_text())
    nodes=[node for node in source.body if isinstance(node,ast.FunctionDef) and node.name=='env_key' or
           isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id in NAMES for target in node.targets)]
    assert len(nodes)==len(NAMES)+1,'settings declaration coverage changed'
    code=compile(ast.Module(body=nodes,type_ignores=[]),'core.settings selected assignments','exec')
    base={'AWS_ACCESS_KEY_ID':'fixture-access','AWS_SECRET_ACCESS_KEY':'fixture-secret','AWS_STORAGE_BUCKET_NAME':'fixture-bucket','AWS_S3_REGION_NAME':'us-east-1','AWS_S3_ENDPOINT_URL':'http://127.0.0.1:9000','AWS_LOCATION':'private','SECRET_KEY':'synthetic-session-secret','DEBUG':'False','DBTYPE':'POSTGRES','DBNAME':'fixture','DBUSER':'fixture','DBHOST':'/tmp/fixture','DBPORT':'5432',
          'DBPASSWORD':'database-fixture','REDIS_PASSWORD':'redis-fixture','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'google-fixture',
          'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'sensitive-fixture-secret','JWT_SECRET_KEY':r'private\nfixture','JWT_PUBLIC_KEY':'public-fixture'}
    expected={'restore_lock_path':'','session_secret':'synthetic-session-secret','debug':False,'rate_enabled':True,'google':'google-fixture','google_secret':'sensitive-fixture-secret','apple':'','apple_team':'','apple_key':'','apple_secret':'',
              'jwt_private':'private\nfixture','jwt_public':'public-fixture','database_password':'database-fixture','redis_password':'redis-fixture',
              'origins':[],'cors_all':False,'cors_origins':[],'family':True,'federation':False,'multiple':False}
    cases=[]
    def add(name,*,lines='',process=None,remove=(),want=None,error=False):
        values={k:v for k,v in base.items() if k not in remove}
        text=''.join(k+'='+v+'\n' for k,v in values.items())+lines
        declared={**expected,**(want or {})}
        if not want or 'cors_all' not in want:declared['cors_all']=declared['debug']
        cases.append({'name':name,'file':text,'process':process or {},'expected':declared,'error':error})
    add('default providers and registration')
    add('all provider values',lines='SOCIAL_AUTH_APPLE_ID_CLIENT=apple-client\nSOCIAL_AUTH_APPLE_ID_TEAM=apple-team\nSOCIAL_AUTH_APPLE_ID_KEY=apple-key\nSOCIAL_AUTH_APPLE_ID_SECRET=apple-secret\n',want={'apple':'apple-client','apple_team':'apple-team','apple_key':'apple-key','apple_secret':'apple-secret'})
    for variable in ('SOCIAL_AUTH_GOOGLE_OAUTH2_KEY','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET','JWT_SECRET_KEY','JWT_PUBLIC_KEY'):
        add('missing required '+variable,remove=(variable,),error=True)
    add('empty required values retained',lines='SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=\nSOCIAL_AUTH_GOOGLE_OAUTH2_SECRET=\nJWT_SECRET_KEY=\nJWT_PUBLIC_KEY=\n',want={'google':'','google_secret':'','jwt_private':'','jwt_public':''})
    for name,lines,value in [
        ('unquoted inline comment','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=client # literal\n','client # literal'),
        ('single quoted value',"SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='client # literal'\n",'client # literal'),
        ('double quoted escape',r'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY="a\qb\tclient"'+'\n',r'aqb\tclient'),
        ('trailing space','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY= client  \n',' client  '),
        ('export assignment','export SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=exported\n','exported'),
        ('duplicate last wins','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=first\nSOCIAL_AUTH_GOOGLE_OAUTH2_KEY=last\n','last'),
        ('embedded dollar is literal','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=prefix$UNDEFINED\n','prefix$UNDEFINED'),
        ('escaped proxy remains literal',r'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=\$ALIAS'+'\n',r'\$ALIAS'),
        ('dollar braces are proxy name','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=${ALIAS}\n','unused'),
    ]:add(name,lines=lines,want={'google':value},error=name=='dollar braces are proxy name')
    add('forward proxy',lines='SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=$LATER\nLATER=forward\n',want={'google':'forward'})
    add('multiple dollar proxy',lines='SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=$$$LATER\nLATER=$LAST\nLAST=resolved\n',want={'google':'resolved'})
    add('required missing proxy',lines='SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=$MISSING\n',error=True)
    add('missing secret proxy is not diagnostic content',lines='SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET=$sensitive-fixture-secret\n',error=True)
    add('optional missing proxy',lines='SOCIAL_AUTH_APPLE_ID_CLIENT=$MISSING\n',want={'apple':''})
    add('proxy cycle',lines='SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=$ALIAS\nALIAS=$SOCIAL_AUTH_GOOGLE_OAUTH2_KEY\n',error=True)
    add('process beats file',process={'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'process-client','DBPASSWORD':'process-password'},want={'google':'process-client','database_password':'process-password'})
    add('empty process beats file',process={'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':''},want={'google':''})
    add('process proxy resolves file',process={'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'$ALIAS'},lines='ALIAS=file-client\n',want={'google':'file-client'})
    add('file proxy resolves process',lines='SOCIAL_AUTH_GOOGLE_OAUTH2_KEY=$ALIAS\n',process={'ALIAS':'process-client'},want={'google':'process-client'})
    add('raw database and Redis do not proxy',lines='DBPASSWORD=$ALIAS\nREDIS_PASSWORD=$ALIAS\nALIAS=must-not-expand\n',want={'database_password':'$ALIAS','redis_password':'$ALIAS'})
    add('raw quoted database and Redis do not expand',lines='DBPASSWORD="dollar$ALIAS # literal"\nREDIS_PASSWORD=\'dollar$ALIAS # literal\'\n',want={'database_password':'dollar$ALIAS # literal','redis_password':'dollar$ALIAS # literal'})
    add('invalid lines ignored without dropping valid fields',lines='not-an-assignment\n INVALID=value\nSOCIAL_AUTH_GOOGLE_OAUTH2_KEY =ignored\n: invalid\n')
    add('Unicode line separator',lines='SOCIAL_AUTH_APPLE_ID_CLIENT=before\u2028SOCIAL_AUTH_APPLE_ID_TEAM=after\n',want={'apple':'before','apple_team':'after'})
    add('literal multiline provider ID',lines=r'SOCIAL_AUTH_APPLE_ID_CLIENT="a\nb\r"'+'\n',want={'apple':r'a\nb\r'})
    add('multiline Apple secret',lines=r'SOCIAL_AUTH_APPLE_ID_SECRET="a\r\nb\nc\rd\t"'+'\n',want={'apple_secret':'a\nb\nc'+r'\rd\t'})
    add('multiline JWT raw',lines=r'JWT_SECRET_KEY="a\r\nb\nc\rd\t"'+'\n',want={'jwt_private':'a\nb\nc'+r'\rd\t'})
    add('JWT raw proxy',lines='JWT_SECRET_KEY=$KEY_ALIAS\nKEY_ALIAS=proxy\\nkey\n',want={'jwt_private':'proxy\nkey'})
    encoded=base64.b64encode(b'encoded\r\nkey').decode()
    add('JWT base64 priority',lines=f'JWT_SECRET_KEY_B64={encoded}\n',want={'jwt_private':'encoded\r\nkey'})
    add('JWT base64 proxy',lines=f'JWT_SECRET_KEY_B64=$ENCODED\nENCODED={encoded}\n',want={'jwt_private':'encoded\r\nkey'})
    add('JWT base64 missing proxy falls back',lines='JWT_SECRET_KEY_B64=$MISSING\n')
    add('JWT base64 ignores ASCII punctuation',lines=f'JWT_SECRET_KEY_B64=!!{encoded}#\n',want={'jwt_private':'encoded\r\nkey'})
    add('JWT base64 rejects non-ASCII',lines=f'JWT_SECRET_KEY_B64={encoded}è\n',error=True)
    add('JWT invalid padding',lines='JWT_SECRET_KEY_B64=abc\n',error=True)
    add('origins retain space and drop empty fields',lines='CSRF_TRUSTED_ORIGINS=https://one.example.test,, https://two.example.test,\n',want={'origins':['https://one.example.test',' https://two.example.test']})
    # get_value casts a resolved list again at the outer proxy level. A
    # nonempty list raises AttributeError; an empty default is not recast.
    add('nonempty origins proxy raises',lines='CSRF_TRUSTED_ORIGINS=$ORIGINS\nORIGINS=https://one.example.test\n',error=True)
    add('empty origins proxy',lines='CSRF_TRUSTED_ORIGINS=$ORIGINS\nORIGINS=,,\n')
    add('origins missing proxy default',lines='CSRF_TRUSTED_ORIGINS=$MISSING\n')
    add('flags proxy and defaults',lines='FAMILY_REGISTRATION_ENABLED=$MISSING\nFEDERATION_REGISTRATION_ENABLED=$FLAG\nFLAG=-2\n',want={'federation':True})
    add('missing required DEBUG',remove=('DEBUG',),error=True)
    add('debug disables default rate limit',lines='DEBUG=True\n',want={'debug':True,'rate_enabled':False})
    add('empty debug enables default rate limit',lines='DEBUG=\n')
    add('explicit rate overrides debug',lines='DEBUG=1\nRATELIMIT_ENABLE=-2\n',want={'debug':True,'rate_enabled':True})
    add('empty rate disables',lines='RATELIMIT_ENABLE=\n',want={'rate_enabled':False})
    add('debug proxy determines rate default',lines='DEBUG=$DEBUG_ALIAS\nDEBUG_ALIAS=ok\n',want={'debug':True,'rate_enabled':False})
    add('missing rate proxy inherits false default',lines='DEBUG=True\nRATELIMIT_ENABLE=$MISSING\n',want={'debug':True,'rate_enabled':False})
    add('missing rate proxy inherits true default',lines='RATELIMIT_ENABLE=$MISSING\n')
    add('rate proxy cycle fails',lines='RATELIMIT_ENABLE=$RATE_ALIAS\nRATE_ALIAS=$RATELIMIT_ENABLE\n',error=True)
    add('process debug controls derived rate default',process={'DEBUG':'yes'},want={'debug':True,'rate_enabled':False})
    add('missing required session secret',remove=('SECRET_KEY',),error=True)
    add('empty session secret assignment',lines='SECRET_KEY=\n',want={'session_secret':''})
    add('session secret proxy chain',lines='SECRET_KEY=$SESSION_ALIAS\nSESSION_ALIAS=$FINAL_ALIAS\nFINAL_ALIAS=synthetic-proxy-secret\n',want={'session_secret':'synthetic-proxy-secret'})
    add('missing session secret proxy redacted',lines='SECRET_KEY=$sensitive-fixture-secret\n',error=True)
    add('session secret proxy cycle',lines='SECRET_KEY=$SESSION_ALIAS\nSESSION_ALIAS=$SECRET_KEY\n',error=True)
    add('session secret literal escapes',lines=r'SECRET_KEY="synthetic\nsecret\r\t"'+'\n',want={'session_secret':r'synthetic\nsecret\r\t'})
    add('session secret does not support base64 override',lines='SECRET_KEY_B64=c3ludGhldGljLWlnbm9yZWQ=\n')
    add('session secret base64 cannot supply missing value',remove=('SECRET_KEY',),lines='SECRET_KEY_B64=c3ludGhldGljLWlnbm9yZWQ=\n',error=True)
    add('process session secret wins',process={'SECRET_KEY':'synthetic-process-secret'},want={'session_secret':'synthetic-process-secret'})
    add('empty process session secret wins',process={'SECRET_KEY':''},want={'session_secret':''})
    add('session secret file proxy reads process',lines='SECRET_KEY=$SESSION_ALIAS\n',process={'SESSION_ALIAS':'synthetic-process-secret'},want={'session_secret':'synthetic-process-secret'})
    add('session secret quoted Unicode and spaces',lines="SECRET_KEY=' è synthetic # secret  '\n",want={'session_secret':' è synthetic # secret  '})
    add('cors all independent of debug',lines='CORS_ORIGIN_ALLOW_ALL=yes\n',want={'cors_all':True})
    add('cors explicit false in development',lines='DEBUG=True\nCORS_ORIGIN_ALLOW_ALL=0\n',want={'debug':True,'rate_enabled':False,'cors_all':False})
    add('cors empty flag in development',lines='DEBUG=True\nCORS_ORIGIN_ALLOW_ALL=\n',want={'debug':True,'rate_enabled':False,'cors_all':False})
    add('cors flag proxy',lines='CORS_ORIGIN_ALLOW_ALL=$CORS_ALIAS\nCORS_ALIAS=yes\n',want={'cors_all':True})
    add('cors missing proxy inherits debug',lines='DEBUG=True\nCORS_ORIGIN_ALLOW_ALL=$MISSING\n',want={'debug':True,'rate_enabled':False,'cors_all':True})
    add('cors flag proxy cycle',lines='CORS_ORIGIN_ALLOW_ALL=$CORS_ALIAS\nCORS_ALIAS=$CORS_ORIGIN_ALLOW_ALL\n',error=True)
    add('cors origins retain spaces and drop empty fields',lines='CORS_ALLOWED_ORIGINS=https://one.example.test,, https://two.example.test,\n',want={'cors_origins':['https://one.example.test',' https://two.example.test']})
    add('cors origins process wins',lines='CORS_ALLOWED_ORIGINS=https://file.example.test\n',process={'CORS_ALLOWED_ORIGINS':'https://process.example.test'},want={'cors_origins':['https://process.example.test']})
    add('cors nonempty origins proxy raises',lines='CORS_ALLOWED_ORIGINS=$CORS_ALIAS\nCORS_ALIAS=https://one.example.test\n',error=True)
    add('cors empty origins proxy',lines='CORS_ALLOWED_ORIGINS=$CORS_ALIAS\nCORS_ALIAS=,,\n')
    add('cors missing origins proxy',lines='CORS_ALLOWED_ORIGINS=$MISSING\n')
    add('cors null origin is a literal string',lines='CORS_ALLOWED_ORIGINS=null\n',want={'cors_origins':['null']})
    add('restore lock path literal',lines='DATA_RESTORE_LOCK_PATH=/tmp/synthetic-lifecycle.lock\n',want={'restore_lock_path':'/tmp/synthetic-lifecycle.lock'})
    add('restore lock path process wins',lines='DATA_RESTORE_LOCK_PATH=/tmp/file.lock\n',process={'DATA_RESTORE_LOCK_PATH':'/tmp/process.lock'},want={'restore_lock_path':'/tmp/process.lock'})
    add('restore lock empty process disables',lines='DATA_RESTORE_LOCK_PATH=/tmp/file.lock\n',process={'DATA_RESTORE_LOCK_PATH':''})
    add('restore lock path proxy',lines='DATA_RESTORE_LOCK_PATH=$LOCK_ALIAS\nLOCK_ALIAS=$LAST_ALIAS\nLAST_ALIAS=/tmp/proxy.lock\n',want={'restore_lock_path':'/tmp/proxy.lock'})
    add('restore lock missing proxy uses default',lines='DATA_RESTORE_LOCK_PATH=$MISSING\n')
    add('restore lock proxy cycle',lines='DATA_RESTORE_LOCK_PATH=$LOCK_ALIAS\nLOCK_ALIAS=$DATA_RESTORE_LOCK_PATH\n',error=True)
    add('restore lock retains spaces and Unicode',lines="DATA_RESTORE_LOCK_PATH=' /tmp/è.lock  '\n",want={'restore_lock_path':' /tmp/è.lock  '})
    add('restore lock path does not expand escapes',lines=r'DATA_RESTORE_LOCK_PATH="/tmp/literal\nlock"'+'\n',want={'restore_lock_path':r'/tmp/literal\nlock'})
    assert len(cases)==87
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with patch.object(logging.getLogger('environ.environ'),'disabled',True):
        for case in cases:
            with patch.dict(os.environ,case['process'],clear=True):
                namespace={'env':environ.Env(),'base64':base64,'os':os}
                environ.Env.read_env(io.StringIO(case['file']))
                failed=False;error_type=None
                try:exec(code,namespace)
                except Exception as exc:failed=True;error_type=type(exc).__name__
                assert failed==case['error'],(case['name'],'Django error boundary',error_type)
                if not failed:
                    actual={label:namespace[name] for name,label in NAMES.items()}
                    assert actual==case['expected'],(case['name'],'Django declared values differ')
    print(f'Django: {len(cases)} declared environment cases passed',flush=True)
    with tempfile.TemporaryDirectory(prefix='az-environment-') as directory:
        fixture=Path(directory)/'cases.json';fixture.write_text(json.dumps(cases));fixture.chmod(0o600)
        subprocess.run(['go','test','-race','-tags=configintegration','./internal/config','-run','^TestDjangoEnvironmentInterop$'],cwd=ROOT/'BE_GO',check=True,timeout=120,env={**tool_env,'ASSOZETA_ENVIRONMENT_FIXTURE':str(fixture)})
    report={'success':True,'cases':len(cases),'django_environ':version('django-environ'),'source':'selected unchanged core.settings AST assignments','database_access':'none','credentials':'synthetic only'}
    (out/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
