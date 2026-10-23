"""Actual Django HTTP/source-mode oracle on private current-model PostgreSQL.

Real middleware/JWT/DRF views, corpus loaders, HTTP download and filesystem
promotion run unchanged. Only fixture data/settings and SQL observation differ.
No installed manual, external network publisher, customer data or Go route is used.
"""
import argparse
import contextlib
import copy
import fcntl
import gzip
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT),str(ROOT/'BE')]
OUTPUT=ROOT/'quality-reports/go-migration/manual-reader-http'
FIXTURE=ROOT/'TESTS/fixtures/manual-reader-http.json'
QUERIES={}
PNG=b'\x89PNG\r\n\x1a\n'+(13).to_bytes(4,'big')+b'IHDR'+(1).to_bytes(4,'big')*2
REVISION='a'*40
class ObserveQueries:
 def __init__(self,get_response):self.get_response=get_response
 def __call__(self,request):
  from django.db import connection
  statements=[]
  def capture(execute,sql,params,many,context):statements.append(sql);return execute(sql,params,many,context)
  with connection.execute_wrapper(capture):response=self.get_response(request)
  QUERIES[request.headers.get('X-Contract-Case','')]=statements
  return response
class Publisher(BaseHTTPRequestHandler):
 payload=b'';requests=[];images={};fail=False
 def log_message(self,*args):pass
 def do_GET(self):
  type(self).requests.append(self.path)
  if self.fail:self.send_error(503);return
  data=self.images.get(self.path,self.payload)
  self.send_response(200);self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

def prepare(base,spec,publisher):
 from application.manuale.index import canonical,digest
 base.mkdir();bundle=base/'bundle';bundle.mkdir();cache=base/'cache';cache.mkdir();source=base/'source';source.mkdir();dev=base/'development'
 mode=spec['mode'];settings=dict(MANUAL_BUNDLE_PATH=str(base/'absent'),MANUAL_BUNDLE_REQUIRED=False,MANUAL_INDEX_PATH=str(cache/'index.json'),MANUAL_ASSET_ROOT='',
  MANUAL_SOURCE_ROOT='',MANUAL_CORPUS_BASE_URL='',MANUAL_APPLICATION_REVISION=REVISION,RUNNING_VERSION='release-fixture',MANUAL_RUN_ID='',MANUAL_DEVELOPMENT_ROOT=str(dev),ASSOZETA_DEPLOYMENT_MODE='production')
 chunks=[]
 for ident,audience,features in [('public','public',[]),('private','maintainer',[]),('host','public',['self_hosted'])]:
  chunks.append(dict(id='guide#'+ident,page='guide',section=ident,title='Manual '+ident,text='Fixture instructions',status='verified',url='https://manual.example.test/guide',
   screenshots=[dict(path=f'images/{ident}.png',sha256=digest(PNG),url=publisher+'/image.png')],scenario_ids=['fixture'],page_title='Fixture page',language='it',intent='read',
   audience=audience,features=features,evidence=[],runtime_dependencies={},private_secret='private-source-canary'))
 if spec.get('copies'):
  public=chunks[0];rows=[]
  for i in range(spec['copies']):
   row=copy.deepcopy(public);row['id']=f'guide#public-{i:03d}';rows.append(row)
  chunks=rows+chunks[1:]
 def corpus(publication='published',code='committed'):
  return dict(format=1,embedding='italian-tfidf-lsa-v5',dependencies={},chunks=copy.deepcopy(chunks),content_identity='d'*64,
   semantics={'vocabulary':['fixture'],'idf':[1.0],'basis':[[1.0]],'vectors':[[1.0] for _ in chunks]},
   metadata=dict(application_revision=REVISION,release='release-fixture',publication_status=publication,code_state=code,manual_revision='c'*40,manual_state='committed',verification={'corpus_identity':'d'*64}))
 def seal(value):value['identity']=digest(canonical(value));return value
 def write_index(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(seal(value),ensure_ascii=False));return value
 def images(root,addressed=False):
  for chunk in chunks:
   image=chunk['screenshots'][0];path=root/(image['sha256']+'.png' if addressed else image['path']);path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(PNG)
 def installed():
  value=corpus('release-bundled');value['metadata']['manual_state']='generated-from-committed';write_index(bundle/'index.json',value);images(bundle)
  (bundle/'bundle.json').write_text(json.dumps(dict(format=1,distribution='immutable-backend-image',index_sha256=digest((bundle/'index.json').read_bytes()),corpus_identity=value['identity'],application_revision=REVISION,release='release-fixture',manual_revision='c'*40,verified_corpus_identity='d'*64)))
  settings['MANUAL_BUNDLE_PATH']=str(bundle)
 if mode in ('bundle','corrupt-bundle','wrong-version','bundle-before-remote','development-before-bundle','preview-before-invalid-bundle'):installed()
 if mode in ('corrupt-bundle','preview-before-invalid-bundle'):(bundle/'bundle.json').write_text('{}')
 if mode=='wrong-version':settings['RUNNING_VERSION']='wrong-release'
 if mode=='required-absent':settings['MANUAL_BUNDLE_REQUIRED']=True
 if mode.startswith('runtime') or mode.endswith('-old'):
  write_index(cache/'index.json',corpus('draft' if mode=='runtime-unpublished' else 'published','working-tree' if mode=='runtime-working' else 'committed'))
  if mode=='runtime':settings['MANUAL_ASSET_ROOT']=str(base/'runtime-assets');images(base/'runtime-assets')
  else:images(cache/'assets',True)
 if mode.startswith('development'):
  settings.update(ASSOZETA_DEPLOYMENT_MODE='development',MANUAL_APPLICATION_REVISION='',MANUAL_SOURCE_ROOT=str(source))
  value=seal(corpus('development-local'));value.pop('identity');value['metadata']['verified_corpus_identity']='d'*64;seal(value)
  package=dev/'packages'/value['identity'];package.mkdir(parents=True);(package/'index.json').write_text(json.dumps(value));images(package)
  (dev/'active.json').write_text(json.dumps(dict(format=1,corpus_identity=value['identity'] if mode!='development-invalid' else 'invalid')))
 if mode.startswith('preview'):
  settings.update(ASSOZETA_DEPLOYMENT_MODE='development',MANUAL_APPLICATION_REVISION='')
  value={'sections':[dict(id='preview#one',page_title='Preview',title='Preview one',text='Draft instructions',screenshots=[dict(path='images/public.png',sha256=digest(PNG))]) ]}
  data=json.dumps(value).encode();identity=digest(data);package=dev/'preview'/identity;package.mkdir(parents=True);(package/'index.json').write_bytes(data);images(package)
  (dev/'preview/active.json').write_text(json.dumps(dict(identity=identity if mode!='preview-invalid' else 'invalid')))
 if mode.startswith('remote') or mode=='bundle-before-remote':settings.update(MANUAL_CORPUS_BASE_URL=publisher,MANUAL_RUN_ID='fixture-local-publisher')
 if spec.get('corrupt_asset'):(bundle/'images/public.png').write_bytes(b'wrong')
 remote=seal(corpus());Publisher.payload=json.dumps(remote).encode();Publisher.images={'/image.png':PNG};Publisher.fail='failure' in mode;Publisher.requests=[]
 if 'readonly' in mode:cache.chmod(0o555)
 return settings,cache

def measure(work,log,fixture,observed,go_source=False,go_runtime=None,versioned=False):
 from django.conf import settings
 from django.core.asgi import get_asgi_application
 from django.db import connections
 from django.test import override_settings
 from auditlog.context import disable_auditlog
 from auditlog.models import LogEntry
 from application.models import User,SportAssociation
 from application.models.audit_models import AuditLogIndex
 from instance.models import InstanceConfiguration,DataRestore
 from rest_framework_simplejwt.tokens import AccessToken
 from TESTS.http_client import Client,poll
 import uvicorn
 source_results=[]
 if go_source:
  subprocess.run(['go','test','-race','-c','-tags','manualsourceintegration','-o',str(work/'source-oracle'),'./pkg/manual'],cwd=ROOT/'BE_GO',check=True,timeout=180,stdout=log,stderr=subprocess.STDOUT)
 users={}
 with disable_auditlog():
  for i,name in enumerate(['owner','administrator','athlete','collaborator','broken-collaborator']):
   users[name]=User.original_objects.create(user_id=f'00000000-0000-4000-8000-{910000+i:012d}',username=name,email=name+'@example.test',password='!fixture',role=3 if 'collaborator' in name else 2 if name=='athlete' else 1,is_superuser=name=='administrator',connected_user=users.get('owner') if name=='collaborator' else None,collaborator_role=3,collaborator_permissions=[])
  association=SportAssociation.original_objects.create(sport_association_id='00000000-0000-4000-8000-000000920001',user=users['owner'],denomination='Fixture ASD')
 tokens={}
 for name,user in users.items():token=AccessToken();token['user_id']=str(user.pk);tokens[name]=str(token)
 def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in [User,SportAssociation,InstanceConfiguration,DataRestore,LogEntry,AuditLogIndex]}
 publisher=ThreadingHTTPServer(('127.0.0.1',0),Publisher);publisher.daemon_threads=True;pt=threading.Thread(target=publisher.serve_forever,daemon=True);pt.start()
 server=thread=None
 try:
  with override_settings(MIDDLEWARE=['TESTS.adapters.django.manual_reader_http.ObserveQueries',*settings.MIDDLEWARE]),contextlib.ExitStack() as resources:
   server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='0.0.0.0' if versioned else '127.0.0.1',port=0,lifespan='off',log_level='critical'));thread=threading.Thread(target=server.run,daemon=True);thread.start();poll(lambda:server.started,bool,timeout=20,interval=.01)
   client=Client(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
   gateway=None
   if versioned:
    from TESTS.adapters.caddy import proxy
    port=server.servers[0].sockets[0].getsockname()[1]
    gateway,_=resources.enter_context(proxy(f'host.docker.internal:{port}',f'host.docker.internal:{go_runtime.listen if go_runtime else 9}',log))
    gateway+='/api/v2' if go_runtime else '/api/v1'
    client=Client(gateway)
   for name,spec in fixture['cases'].items():
    setup=spec['setup'];request=spec['request'];manual,cache=prepare(work/name,setup,f'http://127.0.0.1:{publisher.server_port}')
    with disable_auditlog():
     InstanceConfiguration.objects.all().delete();DataRestore.objects.all().delete();User.original_objects.filter(pk=users['owner'].pk).update(is_active=not setup.get('inactive',False),deleted=setup.get('deleted',False))
     if setup.get('config',True):InstanceConfiguration.objects.create(domain='fixture.example.test',name='Fixture',self_hosted=setup.get('self_hosted',False))
     if setup.get('pending'):DataRestore.objects.create(owner=users['owner'],association_id=association.pk,state='queued',sha256='a'*64)
    headers={**request['headers'],'X-Contract-Case':name}
    if request['actor']!='anonymous':headers['Authorization']='Bearer '+tokens[request['actor']]
    if setup.get('token')=='malformed':headers['Authorization']='Bearer malformed'
    if setup.get('token')=='expired':token=AccessToken();token['user_id']=str(users['owner'].pk);token['exp']=1;headers['Authorization']='Bearer '+str(token)
    from django.core.cache import cache as django_cache
    from application.impersonation import session_key
    django_cache.clear()
    if setup.get('impersonation'):
     session='00000000-0000-4000-8000-000000930001';actor=users[request['actor']];target=users[setup['impersonation']]
     if not setup.get('revoked'):django_cache.set(session_key(session),dict(actor_id=str(actor.pk),target_id=str(target.pk),association_id=None if actor.is_superuser else str(association.pk)),300)
     headers['X-Impersonation-Id']=session;headers['User-Id']=str(users['owner'].pk if setup.get('wrong_target') else target.pk)
    if setup.get('forged_target'):headers['User-Id']=str(users['athlete'].pk)
    cache_snapshot=go_runtime.cache_snapshot if go_runtime else lambda:dict(django_cache._cache)
    if go_runtime:client=go_runtime.start(manual,work/name)
    if gateway:client=Client(gateway)
    before=snapshot();cache_before=cache_snapshot();index_before=(cache/'index.json').read_bytes() if (cache/'index.json').exists() else None
    sql_offset=(work/'postgres.log').stat().st_size
    try:
     with override_settings(**manual),contextlib.ExitStack() as stack:
      if setup.get('lock'):
       lease=stack.enter_context(open(settings.DATA_RESTORE_LOCK_PATH,'a'));fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
      status,data,response_headers=client.request(request['method'],request['path'],headers=headers,raw=True,include_headers=True)
     if response_headers.get('Content-Encoding')=='gzip':data=gzip.decompress(data)
     try:body=json.loads(data)
     except (ValueError,UnicodeDecodeError):body={}
     sql=go_runtime.queries(sql_offset) if go_runtime else QUERIES.pop(name)
     assert cache_snapshot()==cache_before,'manual HTTP changed application cache records'
     if 'failure' in setup['mode'] or 'readonly' in setup['mode']:
      assert ((cache/'index.json').read_bytes() if (cache/'index.json').exists() else None)==index_before,'failed refresh changed active corpus'
     asset=response_headers.get('Content-Type')=='image/png'
     item=dict(status=status,kind='asset' if asset else 'json',body=body,result=body.get('status'),ids=[x['id'] for x in body.get('results',[])],scores=[x.get('score') for x in body.get('results',[])],message=body.get('message'),
      database_unchanged=snapshot()==before,writes=sum(not s.lstrip().upper().startswith(('SELECT','SHOW','BEGIN','COMMIT','ROLLBACK','SAVEPOINT','RELEASE')) for s in sql),
      secret_free=b'private-source-canary' not in data,selects=sum(s.lstrip().upper().startswith('SELECT') for s in sql),context_selects=sum(bool(re.search(r'FROM\s+"?instance_instanceconfiguration"?',s,re.I)) for s in sql),
      headers={k:response_headers.get(k) for k in ['Allow','Content-Type','Content-Encoding','Content-Language','Cache-Control','X-Content-Type-Options','WWW-Authenticate','Retry-After']},publisher_requests=list(Publisher.requests))
     item['headers']['Vary']=', '.join(sorted({token.strip().lower() for value in response_headers.get_all('Vary',[]) for token in value.split(',')})) or None
     for section in body.get('results',[]):
      if 'corpus_identity' in section:
       # The publisher port is fixture-generated and is sealed into corpus
       # identity. Verify that identity before the portable differential masks it.
       indexes=list((work/name).rglob('index.json'))
       identities={json.loads(p.read_text()).get('identity') for p in indexes}
       assert section['corpus_identity'] in identities,'response corpus identity is not a fixture source'
     if asset:item['asset']='fixture_png' if data==PNG else 'unexpected_bytes'
     if setup['mode']=='remote-success':assert (cache/'index.json').exists() and len(Publisher.requests)==2,'refresh must fetch corpus and deduplicated image before publication'
     if 'readonly' in setup['mode']:assert not Publisher.requests,'readonly cache must fail before network'
     if setup['mode']=='bundle-before-remote':assert not Publisher.requests,'bundle must precede remote refresh'
     observed[name]=item;print(f'OBSERVED {name}: {status}',flush=True)
     from TESTS.contracts.test_manual_reader_http import validate_report
     validate_report({'schema_version':1,'cases':{name:item}},{'cases':{name:spec}})
     if go_source and name in fixture['source_cases']:
      gs,gcache=prepare(work/('go-'+name),setup,f'http://127.0.0.1:{publisher.server_port}')
      fields={'MANUAL_BUNDLE_PATH':'BundlePath','MANUAL_BUNDLE_REQUIRED':'BundleRequired','MANUAL_INDEX_PATH':'IndexPath','MANUAL_ASSET_ROOT':'AssetRoot','MANUAL_SOURCE_ROOT':'SourceRoot','MANUAL_CORPUS_BASE_URL':'CorpusBaseURL','MANUAL_APPLICATION_REVISION':'ApplicationRevision','RUNNING_VERSION':'Release','MANUAL_RUN_ID':'RunID','MANUAL_DEVELOPMENT_ROOT':'DevelopmentRoot','ASSOZETA_DEPLOYMENT_MODE':'DeploymentMode'}
      manifest={'Name':name,'Settings':{fields[k]:v for k,v in gs.items()},'Features':['self_hosted'] if setup.get('self_hosted') else [],'AssetPath':request['path'].removeprefix('/manuale/assets/') if '/assets/' in request['path'] else '', 'Expected':spec['expected']}
      path=work/'source-case.json';path.write_text(json.dumps(manifest))
      go_index_before=(gcache/'index.json').read_bytes() if (gcache/'index.json').exists() else None
      try:
       subprocess.run([str(work/'source-oracle'),'-test.run','^TestManualSourceOracle$','-test.v'],env={**os.environ,'ASSOZETA_MANUAL_SOURCE_FIXTURE':str(path)},check=True,timeout=45,stdout=log,stderr=subprocess.STDOUT)
       assert Publisher.requests==item['publisher_requests'],'Go source publisher requests differ from the established Django contract'
       if 'failure' in setup['mode'] or 'readonly' in setup['mode']:
        assert ((gcache/'index.json').read_bytes() if (gcache/'index.json').exists() else None)==go_index_before,'Go failed refresh changed active corpus'
       if setup['mode']=='remote-success':
        assert json.loads((gcache/'index.json').read_bytes())==json.loads(Publisher.payload),'Go did not promote the validated publisher corpus'
        assert (gcache/'assets'/(hashlib.sha256(PNG).hexdigest()+'.png')).read_bytes()==PNG,'Go promoted without the verified image'
       source_results.append(name)
      finally:gcache.chmod(0o755)

    finally:cache.chmod(0o755)
   if go_source:assert source_results==fixture['source_cases'],'source differential case set differs; no skips'
 finally:
  try:
   if server:
    server.should_exit=True
    if thread:thread.join(timeout=20);assert not thread.is_alive(),'ASGI shutdown did not join'
   connections.close_all()
  finally:publisher.shutdown();publisher.server_close();pt.join(timeout=10);assert not pt.is_alive(),'fixture publisher did not stop'


def main():
    global OUTPUT
    from TESTS.adapters.django.production_runtime import require_production_python
    from TESTS.contracts.test_manual_reader_http import validate_report
    require_production_python()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--go-source',action='store_true');parser.add_argument('--backend',choices=['django','go'],default='django');parser.add_argument('--versioned',action='store_true');parser.add_argument('--tools',action='store_true',help='Characterize actual manual tool functions with real database context; not MCP transport');options=parser.parse_args()
    if options.tools and (options.backend!='django' or options.versioned):parser.error('--tools requires the Django function oracle; --go-source adds the Go library comparison')
    if options.backend=='go':OUTPUT=ROOT/'quality-reports/go-migration/manual-reader-http-go'
    if options.versioned:OUTPUT=Path(str(OUTPUT)+'-versioned')
    fixture_path=FIXTURE
    if options.tools:
        OUTPUT=ROOT/'quality-reports/go-migration/manual-tools'
        if options.go_source:OUTPUT=ROOT/'quality-reports/go-migration/manual-tools-go-library'
        fixture_path=ROOT/'TESTS/fixtures/manual-tools.json'
        from TESTS.contracts.test_manual_tools import validate_report
    fixture=json.loads(fixture_path.read_text());OUTPUT.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'success':False,'cases':{},'scope':__doc__,
            'fixture_sha256':hashlib.sha256(fixture_path.read_bytes()).hexdigest()}
    if options.tools:
        report['scope']='Actual Django manual tool functions and live PostgreSQL context; no MCP transport, agent dispatch or Go implementation claim.'
        if options.go_source:report['scope']='Actual Django functions and PostgreSQL authority compared with Go knowledge library using fixture-supplied trusted context; no Go authority, MCP transport or agent dispatch claim.'
        report['source_hashes']={path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in (
            'BE/application/manuale/tools.py','BE/application/manuale/index.py','BE/application/manuale/bundle.py',
            'BE/application/manuale/development.py','BE/instance/permissions.py','BE/instance/models.py',
            'TESTS/adapters/django/manual_reader_http.py','TESTS/adapters/django/manual_tools.py',
            'TESTS/contracts/test_manual_tools.py')}
        if options.go_source:
            paths=[*sorted((ROOT/'BE_GO/pkg/manual').glob('*.go')),ROOT/'TESTS/contracts/test_manual_tools_go.py']
            report['source_hashes'].update({str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths})
    postgres=shutil.which('postgres')
    if not postgres:
        candidates=sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'));postgres=str(candidates[-1]) if candidates else None
    if not postgres:raise RuntimeError('Private PostgreSQL is required')
    binaries=Path(postgres).parent
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    tool_env['LC_ALL'] = 'C'
    with tempfile.TemporaryDirectory(prefix='az-manual-http-',dir='/tmp') as directory,(OUTPUT/'services.log').open('w') as log:
        work=Path(directory);attempted=False
        def run(args):return subprocess.run([str(x) for x in args],check=True,timeout=180,stdout=log,stderr=subprocess.STDOUT,env=tool_env)
        try:
            run([binaries/'initdb','-D',work/'postgres','-A','trust','-U','contracts','--no-sync','--no-locale','-E','UTF8'])
            attempted=True
            run([binaries/'pg_ctl','-D',work/'postgres','-l',work/'postgres.log','-o',f"-k {work} -h '' -F -c log_statement=all -c log_line_prefix='%m\x20[%p]\x20manual-http-sql\x20'",'-w','start'])
            run([binaries/'createdb','-h',work,'-U','contracts','assozeta_contract_manual_http'])
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            key=Ed25519PrivateKey.generate()
            import environ
            os.environ.clear();os.environ.update(tool_env);environ.Env.read_env(str(ROOT/'selfhost/.env.example'))
            os.environ.update(DEBUG='False',JWT_SECRET_KEY_B64='',JWT_PUBLIC_KEY_B64='',
                JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),
                JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
                DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings',TESTS_DATABASE='assozeta_contract_manual_http',TESTS_WORKDIR=str(work),
                DBTYPE='POSTGRES',DBHOST=str(work),DBPORT='5432',DBNAME='assozeta_contract_manual_http',DBUSER='contracts',DBPASSWORD='test-only',USE_PGBOUNCER='False',STORE_ON_DISK='True')
            import django
            with patch('environ.Env.read_env'):django.setup()
            from django.apps import apps
            from django.conf import settings
            from django.core.management import call_command
            settings.MIGRATION_MODULES={app.label:None for app in apps.get_app_configs()}
            call_command('migrate',interactive=False,verbosity=0,run_syncdb=True,stdout=log,stderr=log)
            from application.audit_resolvers import setup_audit_resolvers
            setup_audit_resolvers()
            from TESTS.adapters.django.manual_reader_runtime import GoManualRuntime
            with GoManualRuntime(work,log,tool_env,options.versioned) if options.backend=='go' else contextlib.nullcontext(None) as go_runtime:
                if options.tools:
                    from TESTS.adapters.django.manual_tools import measure_tools
                    measure_tools(work,fixture,report['cases'],options.go_source,log)
                else:measure(work,log,fixture,report['cases'],options.go_source,go_runtime,options.versioned)
            validate_report(report,fixture)

        finally:
            try:
                if 'django.db' in sys.modules:
                    from django.db import connections
                    connections.close_all()
            finally:
                try:
                    if attempted:
                        try:run([binaries/'pg_ctl','-D',work/'postgres','-m','immediate','-w','stop'])
                        except subprocess.CalledProcessError:
                            if (work/'postgres/postmaster.pid').exists():raise
                finally:
                    if (work/'postgres.log').exists():shutil.copyfile(work/'postgres.log',OUTPUT/'postgres.log')
                    (OUTPUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    report.update(success=True,versioned=options.versioned,source_differential_cases=list(fixture['cases']) if options.tools and options.go_source else fixture['source_cases'] if options.go_source else [],private_services_stopped=True,scenario_count=len(report['cases']),runtime={'backend':options.backend,'python':sys.version.split()[0],'django':django.get_version()})
    (OUTPUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f'PASS {len(report["cases"])} actual {options.backend} manual {"tool" if options.tools else "HTTP"} cases',flush=True)


if __name__=='__main__':
    sys.modules['TESTS.adapters.django.manual_reader_http']=sys.modules[__name__]
    main()
