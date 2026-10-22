"""Actual Django ASGI bootstrap oracle with private current-model PG and Redis.

Expected responses are authored in the portable fixture, never copied from this
runtime. No production data, historical migrations, provider call or operator
configuration change is involved. Tokens and session records are fixture-only.
"""
import argparse
import contextlib
import re
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
OUTPUT = ROOT / 'quality-reports/go-migration/instance-bootstrap'
FIXTURE = ROOT / 'TESTS/fixtures/instance-bootstrap.json'
QUERY_RECORDS = {}


class ObserveQueries:
    """Fixture instrumentation only; records real request SQL without changing it."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from django.db import connection
        queries = []
        def capture(execute, sql, params, many, context):
            queries.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(capture):
            response = self.get_response(request)
        QUERY_RECORDS[request.headers.get('X-Contract-Case', '')] = queries
        return response


def uid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def measure(work, log, fixture, backend, observed, tool_env):
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation
    from application.models.audit_models import AuditLogIndex
    from instance.models import InstanceConfiguration, DataRestore
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client, poll
    import redis
    import uvicorn

    users = {}
    with disable_auditlog():
        for i, name in enumerate(['owner', 'administrator', 'athlete', 'collaborator', 'broken-collaborator', 'other-owner']):
            users[name] = User.original_objects.create(user_id=uid(810000+i), username=name.upper(),
                email=name+'@example.test', password='!fixture', role=3 if 'collaborator' in name else 2 if name=='athlete' else 1,
                is_superuser=name=='administrator', connected_user=users.get('owner') if name=='collaborator' else None,
                collaborator_role=3, collaborator_permissions=[])
        association = SportAssociation.original_objects.create(sport_association_id=uid(820001), user=users['owner'], denomination='Primary à ASD')
    tokens = {}
    for name, user in users.items():
        token = AccessToken(); token['user_id'] = str(user.pk); tokens[name] = str(token)
    cache_port = free_port()
    process = subprocess.Popen(['redis-server', '--bind','127.0.0.1','--port',str(cache_port),'--save','','--appendonly','no',
        '--requirepass','fixture-bootstrap-cache','--dir',str(work)], stdout=log, stderr=subprocess.STDOUT)
    raw = redis.Redis(host='127.0.0.1',port=cache_port,db=1,password='fixture-bootstrap-cache',socket_timeout=2)
    server = thread = go_process = None
    go_environment = None
    base_settings = dict(RUNNING_VERSION='v1.2.3', AI_API_KEY='', SOCIAL_AUTH_GOOGLE_OAUTH2_KEY='',SOCIAL_AUTH_APPLE_ID_CLIENT='',
        STRIPE_KEY='',STRIPE_PUBLIC_KEY='',STRIPE_WEBHOOK_SECRET='',MANUAL_CORPUS_BASE_URL='',MANUAL_RUN_ID='',MANUAL_SOURCE_ROOT='',
        MANUAL_BUNDLE_PATH=str(work/'no-bundle'),MANUAL_BUNDLE_REQUIRED=False,MANUAL_INDEX_PATH=str(work/'missing-index.json'),
        MANUAL_DEVELOPMENT_ROOT=str(work/'no-development'),FAMILY_REGISTRATION_ENABLED=True,FEDERATION_REGISTRATION_ENABLED=False,
        MULTIPLE_REGISTRATION_ENABLED=False,CORS_ORIGIN_ALLOW_ALL=False,CORS_ALLOWED_ORIGINS=['https://allowed.example.test'])
    cache_settings={'default':{'BACKEND':'django_redis.cache.RedisCache','LOCATION':f'redis://127.0.0.1:{cache_port}/1',
        'OPTIONS':{'CLIENT_CLASS':'django_redis.client.DefaultClient','PASSWORD':'fixture-bootstrap-cache'}}}
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in [User,SportAssociation,InstanceConfiguration,LogEntry,AuditLogIndex,DataRestore]}
    try:
        def ready():
            try:return raw.ping()
            except redis.ConnectionError:return False
        poll(ready,bool,timeout=10,interval=.025)
        middleware=['TESTS.adapters.django.instance_bootstrap.ObserveQueries',*settings.MIDDLEWARE]
        with override_settings(**base_settings,CACHES=cache_settings,MIDDLEWARE=middleware):
            server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
            thread=threading.Thread(target=server.run,daemon=True);thread.start()
            poll(lambda:server.started,bool,timeout=20,interval=.01)
            client=Client(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            if backend == 'go':
                from TESTS.adapters.go.server import build_server
                build_server(work/'bootstrap-server',log,tool_env)
            for name,spec in fixture['cases'].items():
                setup=spec['setup']; request=spec['request']
                with disable_auditlog():
                    InstanceConfiguration.objects.all().delete();DataRestore.objects.all().delete()
                    User.original_objects.filter(pk=users['owner'].pk).update(role=setup.get('owner_role',1),is_active=True,deleted=False)
                    if setup.get('actor_state'):User.original_objects.filter(pk=users['owner'].pk).update(**setup['actor_state'])
                    SportAssociation.original_objects.filter(pk=association.pk).update(deleted=setup.get('association_deleted',False))
                    if setup.get('config',{}) is not None:
                        fields=dict(domain='fixture.example.test',name='Fixture à ASD',abbreviation='Fà',support_email='support@example.test',
                            email_password_encrypted='private-email-canary',stripe_secret_key='private-legacy-canary',diagnostic_results={'secret':'private-diagnostic-canary'})
                        fields.update(setup.get('config',{}))
                        if setup.get('primary'):fields['primary_association']=association
                        config=InstanceConfiguration.objects.create(**fields)
                        InstanceConfiguration.objects.filter(pk=config.pk).update(updated_at=datetime(2026,10,22,12,34,56,123456,tzinfo=timezone.utc))
                        if setup.get('second'):
                            InstanceConfiguration.objects.bulk_create([InstanceConfiguration(domain='second.example.test',name='Wrong second')])
                    if setup.get('pending'):
                        DataRestore.objects.create(owner=users['owner'],association_id=association.pk,state='queued',sha256='a'*64)
                headers={**request['headers'],'X-Contract-Case':name}
                if request['actor']!='anonymous':headers['Authorization']='Bearer '+tokens[request['actor']]
                if setup.get('token'):
                    kind=setup['token']
                    if kind in ('malformed','missing'):headers['Authorization']='Bearer malformed' if kind=='malformed' else 'Bearer'
                    else:
                        token=AccessToken();token['user_id']=uid(999999) if kind=='missing-user' else str(users[request['actor']].pk)
                        if kind=='expired':token['exp']=1
                        if kind=='forged-roles':token['role']=1;token['is_superuser']=True
                        headers['Authorization']='Bearer '+str(token)
                raw.flushdb()
                if setup.get('cookie'):
                    record={'_auth_user_id':str(users['owner'].pk),'_auth_user_backend':'django.contrib.auth.backends.ModelBackend',
                        '_auth_user_hash':users['owner'].get_session_auth_hash() if setup['cookie']=='valid' else 'invalid-hash'}
                    cache.set('django.contrib.sessions.cachebootstrap-session',record,300)
                    headers['Cookie']='sessionid=bootstrap-session'
                if backend == 'go':
                    effective={**base_settings,**setup.get('settings',{})}
                    environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_bootstrap','DBUSER':'contracts','DBPASSWORD':'test-only',
                        'DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':'127.0.0.1','REDIS_PORT':str(cache_port),'REDIS_PASSWORD':'fixture-bootstrap-cache',
                        'SECRET_KEY':settings.SECRET_KEY,'JWT_SECRET_KEY':settings.JWT_SECRET_KEY,'JWT_PUBLIC_KEY':settings.JWT_PUBLIC_KEY,
                        'DEBUG':'False','RATELIMIT_ENABLE':'False','CORS_ALLOWED_ORIGINS':','.join(effective['CORS_ALLOWED_ORIGINS']),
                        'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'','DATA_RESTORE_LOCK_PATH':settings.DATA_RESTORE_LOCK_PATH,
                        **{key:str(value) for key,value in effective.items() if key not in ('CORS_ORIGIN_ALLOW_ALL','CORS_ALLOWED_ORIGINS')},
                        **{key:getattr(settings,key) for key in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_STORAGE_BUCKET_NAME','AWS_S3_REGION_NAME','AWS_S3_ENDPOINT_URL','AWS_LOCATION')}}
                    if go_environment != environment:
                        if go_process:go_process.terminate();go_process.wait(timeout=30)
                        go_environment=environment.copy();listen=free_port();environment['GO_LISTEN_ADDR']=f'127.0.0.1:{listen}'
                        go_process=subprocess.Popen([str(work/'bootstrap-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
                        client=Client(f'http://127.0.0.1:{listen}')
                        def go_ready():
                            if go_process.poll() is not None:raise AssertionError('Go bootstrap server exited before listening')
                            try:
                                with socket.create_connection(('127.0.0.1',listen),timeout=.2):return True
                            except OSError:return False
                        poll(go_ready,bool,timeout=20,interval=.025)
                before=snapshot();cache_before={key:raw.get(key) for key in raw.scan_iter()}
                sql_offset=(work/'postgres.log').stat().st_size
                with override_settings(**setup.get('settings',{})),contextlib.ExitStack() as stack:
                    if setup.get('lock'):
                        lease=stack.enter_context(open(settings.DATA_RESTORE_LOCK_PATH,'a'))
                        fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    status,body,response_headers=client.request(request['method'],request['path'],headers=headers,include_headers=True)
                if backend == 'django':sql=QUERY_RECORDS.pop(name)
                else:
                    with (work/'postgres.log').open() as postgres_log:
                        postgres_log.seek(sql_offset);logged=postgres_log.read()
                    records=re.split(r'(?m)^(?=\d{4}-\d{2}-\d{2} .* bootstrap-sql )',logged)
                    sql=[]
                    for record in records:
                        match=re.search(r'LOG:  (?:execute [^:]+|statement): (.*)',record,re.S)
                        if match:sql.append(match.group(1).split('\n20',1)[0])
                # pgx validates a reused connection with a comment-only -- ping.
                statements=[re.sub(r'(?m)^\s*--[^\n]*(?:\n|$)','',statement).strip() for statement in sql]
                write_statements=sum(bool(statement) and not statement.upper().startswith(('SELECT','SHOW','BEGIN','COMMIT','ROLLBACK','SAVEPOINT','RELEASE')) for statement in statements)
                secret_free=all(value not in json.dumps(body,ensure_ascii=False) for value in ['private-email-canary','private-legacy-canary','private-diagnostic-canary','private-ai-canary','private-stripe-canary','private-manual-canary','corrupt-secret-canary'])
                item={'status':status,'body':body,'database_unchanged':snapshot()==before,'write_statements':write_statements,
                    'secret_free':secret_free,'selects':sum(s.lstrip().upper().startswith('SELECT') for s in sql),
                    'headers':{key:response_headers.get(key) for key in ['Allow','Content-Type','Content-Language','WWW-Authenticate','Retry-After','Vary']}}
                cache_after={key:raw.get(key) for key in raw.scan_iter()}
                if setup.get('cookie')=='stale':assert not cache_after and 'sessionid=' in (response_headers.get('Set-Cookie') or ''),'stale cookie must expire'
                else:assert cache_after==cache_before,'bootstrap changed cache state'
                if setup.get('live_edit'):
                    InstanceConfiguration.objects.filter(pk=config.pk).update(name='Live edit')
                    _,second,_=client.request('GET','/instance/status',headers=headers,include_headers=True)
                    assert second=={**spec['expected']['body'],'instance_name':'Live edit'},'live configuration was cached'
                    item['live_edit_observed']=True
                observed[name]=item
                print(f'OBSERVED {name}: {status}',flush=True)
                from TESTS.contracts.test_instance_bootstrap import validate_report
                validate_report({'schema_version':1,'cases':{name:item}},{'cases':{name:spec}})
    finally:
        try:
            if go_process:
                go_process.terminate()
                try:go_process.wait(timeout=30)
                except subprocess.TimeoutExpired:go_process.kill();go_process.wait(timeout=10)
            if server:
                server.should_exit=True
                if thread:thread.join(timeout=20);assert not thread.is_alive(),'ASGI shutdown did not join'
            connections.close_all()
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
    return observed


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--backend',choices=['django','go'],default='django');options=parser.parse_args()
    global OUTPUT
    if options.backend=='go':OUTPUT=ROOT/'quality-reports/go-migration/instance-bootstrap-go'
    from TESTS.contracts.test_instance_bootstrap import validate_report
    fixture=json.loads(FIXTURE.read_text());OUTPUT.mkdir(parents=True,exist_ok=True)
    report={'schema_version':1,'success':False,'cases':{},'scope':__doc__}
    postgres=shutil.which('postgres')
    if not postgres:
        candidates=sorted(Path('/opt/homebrew/opt').glob('postgresql*/bin/postgres'));postgres=str(candidates[-1]) if candidates else None
    if not postgres or not shutil.which('redis-server'):raise RuntimeError('Private PostgreSQL and Redis are required')
    binaries=Path(postgres).parent
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    tool_env['LC_ALL'] = 'C'
    with tempfile.TemporaryDirectory(prefix='az-bootstrap-',dir='/tmp') as directory,(OUTPUT/'services.log').open('w') as log:
        work=Path(directory);attempted=False
        def run(args):return subprocess.run([str(x) for x in args],check=True,timeout=180,stdout=log,stderr=subprocess.STDOUT,env=tool_env)
        try:
            run([binaries/'initdb','-D',work/'postgres','-A','trust','-U','contracts','--no-sync','--no-locale','-E','UTF8'])
            attempted=True
            run([binaries/'pg_ctl','-D',work/'postgres','-l',work/'postgres.log','-o',f"-k {work} -h '' -F -c log_statement=all -c log_line_prefix='%m\x20[%p]\x20bootstrap-sql\x20'",'-w','start'])
            run([binaries/'createdb','-h',work,'-U','contracts','assozeta_contract_bootstrap'])
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives import serialization
            key=Ed25519PrivateKey.generate()
            import environ
            os.environ.clear();os.environ.update(tool_env);environ.Env.read_env(str(ROOT/'selfhost/.env.example'))
            os.environ.update(DEBUG='False',JWT_SECRET_KEY_B64='',JWT_PUBLIC_KEY_B64='',
                JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),
                JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
                DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings',TESTS_DATABASE='assozeta_contract_bootstrap',TESTS_WORKDIR=str(work),
                DBTYPE='POSTGRES',DBHOST=str(work),DBPORT='5432',DBNAME='assozeta_contract_bootstrap',DBUSER='contracts',DBPASSWORD='test-only',USE_PGBOUNCER='False',STORE_ON_DISK='True')
            import django
            with patch('environ.Env.read_env'):django.setup()
            from django.apps import apps
            from django.conf import settings
            from django.core.management import call_command
            settings.MIGRATION_MODULES={app.label:None for app in apps.get_app_configs()}
            call_command('migrate',interactive=False,verbosity=0,run_syncdb=True,stdout=log,stderr=log)
            from application.audit_resolvers import setup_audit_resolvers
            setup_audit_resolvers()
            measure(work,log,fixture,options.backend,report['cases'],tool_env)
            validate_report(report,fixture)
            if options.backend=='go':
                environment={**tool_env,'ASSOZETA_INSTANCE_BOOTSTRAP_DSN':f'postgres://contracts@/assozeta_contract_bootstrap?host={work}&sslmode=disable',
                    'ASSOZETA_INSTANCE_BOOTSTRAP_PLANS':str(OUTPUT/'repository-plans.json')}
                subprocess.run(['go','test','-race','-tags','instancebootstrapintegration','./internal/repository','-run','^TestInstanceBootstrapPostgresBounds$','-count=1','-v'],
                    cwd=ROOT/'BE_GO',env=environment,check=True,timeout=120,stdout=log,stderr=subprocess.STDOUT)
                report['repository_bounds_groups']=4

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
    report.update(success=True,private_services_stopped=True,scenario_count=len(report['cases']),runtime={'backend':options.backend,'python':sys.version.split()[0],'django':django.get_version()})
    (OUTPUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f'PASS {len(report["cases"])} actual {options.backend} bootstrap cases',flush=True)


if __name__=='__main__':
    sys.modules['TESTS.adapters.django.instance_bootstrap']=sys.modules[__name__]
    main()
