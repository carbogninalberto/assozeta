"""Actual ASGI/Go public identifier checks on private account fixtures."""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid


def run(work,log,tool_env,address):
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from auditlog.context import disable_auditlog
    from application.models import User
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client,poll
    import redis
    import uvicorn
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    rows=[(80001,'availability-mixed','availability-mixed@example.test','Mixed',True,False),
          (80002,'availability-inactive','availability-inactive@example.test','Inactive',False,False),
          (80003,'availability-deleted','availability-deleted@example.test','Deleted',True,True),
          (80004,'availability-duplicate-first','availability-dup@example.test','First',True,False),
          (80005,'availability-duplicate-second','AVAILABILITY-DUP@EXAMPLE.TEST','Second',True,False),
          (80006,'availability-fallback','availability-fallback-mail@example.test','Fallback',True,False),
          (80007,'availability-email-priority','availability-priority','Email priority',True,False),
          (79999,'availability-priority','availability-username-priority@example.test','Username priority',True,False),
          (80009,'availability%_literal','availability%_literal','Literal',True,False)]
    batches=[]
    for index,count in enumerate((1,16,128,512)):
        start=90000+index*1000
        identifier=f'identifier-batch-{count}@example.test'
        batches.append({'identifier':identifier,'first':uid(start),'count':count})
        rows.extend((start+i,f'identifier-batch-{count}-{i}',identifier,'Batch',True,False) for i in range(count))
    ids=[uid(n) for n,*_ in rows]
    assert not User.original_objects.filter(pk__in=ids).exists(),'identifier fixture IDs collide'
    User.original_objects.bulk_create([User(user_id=uid(n),username=username,email=email,first_name=first,last_name='Availability',
                                           is_active=active,deleted=deleted,role=1,password='!identifier-fixture')
                                       for n,username,email,first,active,deleted in rows])
    def token(n):
        value=AccessToken();value['user_id']=uid(n);return str(value)
    # Seed the documented existing impersonation record; starting a real session
    # writes an audit transition, which is outside this read-only endpoint fixture.
    owner_session=uid(80010)
    assert cache.get(session_key(owner_session)) is None,'identifier cache fixture collides'
    cache.set(session_key(owner_session),{'actor_id':uid(8),'target_id':uid(10),'association_id':uid(80)},3600)
    fixture={'batches':batches,'owner':token(8),'staff':token(11),'inactive':token(3),'deleted':token(4),'owner_session':owner_session}
    path=work/'identifier-http.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
    cache_name='django.contrib.sessions.cacheidentifier-malformed-session'
    cache.set(cache_name,[],300)
    host,port=address.rsplit(':',1)
    raw_cache=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    cache_key=cache.make_key(cache_name);payload=raw_cache.get(cache_key)
    before=snapshot()
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    api=None
    def contracts(base):
        with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_IDENTIFIER_FIXTURE=str(path)):
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_identifier_availability'))
            assert result.wasSuccessful() and not result.skipped,'public identifier contracts failed'
            return result.testsRun
    def readback():
        assert snapshot()==before,'identifier checks changed managed tables'
        assert raw_cache.get(cache_key)==payload,'identifier checks changed session cache bytes'
    try:
        deadline=time.monotonic()+5
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline,'private identifier ASGI server failed to start'
            time.sleep(.01)
        count=contracts(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
        readback()
        from TESTS.adapters.go.server import build_server
        build_server(work/'identifier-server',log,tool_env)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));http_port=listener.getsockname()[1]
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only',
                     'DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':host,'REDIS_PORT':port,'REDIS_USERNAME':'default',
                     'REDIS_PASSWORD':'synthetic-identity-only',**{name: getattr(settings, name) for name in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')},
                     'GO_LISTEN_ADDR':f'127.0.0.1:{http_port}',
                     'SECRET_KEY':settings.SECRET_KEY,'JWT_SECRET_KEY':settings.JWT_SECRET_KEY,'JWT_PUBLIC_KEY':settings.JWT_PUBLIC_KEY,
                     'DEBUG':'False','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY,
                     'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET}
        api=subprocess.Popen([str(work/'identifier-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None,'Go identifier server stopped'
            try:return Client(base).request('GET','/readyz')[0]==200
            except OSError:return False
        poll(ready,bool,timeout=30)
        assert contracts(base)==count
        readback()
        for batch in batches:
            selected=User.objects.filter(email__iexact=batch['identifier'])
            assert selected.count()==batch['count'] and str(selected.first().pk)==batch['first'],'Django duplicate selection fixture differs'
        plan_path=ROOT/'quality-reports/go-migration/accounts/identifier-plans.json';plan_path.parent.mkdir(parents=True,exist_ok=True)
        subprocess.run(['go','test','-race','-tags=identifierintegration','./internal/service','-run','^TestIdentifierPostgres$'],cwd=ROOT/'BE_GO',check=True,timeout=90,
                       env={**tool_env,'ASSOZETA_IDENTIFIER_FIXTURE':str(path),'ASSOZETA_IDENTIFIER_PLAN':str(plan_path),
                            'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        readback()
        return count
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        cache.delete(cache_name);cache.delete(session_key(owner_session));raw_cache.close();path.unlink(missing_ok=True)
        with disable_auditlog():User.original_objects.filter(pk__in=ids).delete()
        assert not thread.is_alive(),'private identifier ASGI server did not drain'
        assert snapshot()==original,'identifier fixture cleanup changed preexisting model state'
