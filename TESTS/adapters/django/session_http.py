"""Real session/authentication/audit middleware around a controlled HTTP terminal."""
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid


def run(work,log,tool_env,address):
    from django.apps import apps
    from django.core.cache import cache
    from django.core.asgi import get_asgi_application
    from django.http import JsonResponse,StreamingHttpResponse
    from django.db import DatabaseError,transaction
    from django.contrib.contenttypes.models import ContentType
    from django.test import override_settings
    from django.urls import re_path,clear_url_caches
    from application.models import User
    from auditlog.middleware import AuditlogMiddleware
    from auditlog.cid import get_cid
    from auditlog.models import LogEntry
    import redis
    import uvicorn
    host,port=address.rsplit(':',1)
    raw=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    content_type=ContentType.objects.get_for_model(User)
    for port,reject in [(None,False),(0,False),(443,False),(2147483647,False),(-1,True),(2147483648,True),(2**80,True)]:
        failed=False
        try:
            with transaction.atomic():
                row=LogEntry.objects.create(content_type=content_type,object_pk=uid(1),object_repr='session-port-fixture',action=1,
                    changes={'username':['before','after']},remote_port=port)
                assert LogEntry.objects.get(pk=row.pk).remote_port==port
                transaction.set_rollback(True)
        except DatabaseError:failed=True
        assert failed==reject,'Django audit port database range differs'
    assert snapshot()==before,'rolled-back audit port oracle changed tables'
    print('Django: 7 declared audit port persistence boundaries passed',flush=True)
    secret='synthetic-session-http-only'
    with override_settings(SECRET_KEY=secret,SECRET_KEY_FALLBACKS=[],ROOT_URLCONF='fixture_session_http_urls',
                          MIDDLEWARE=['core.middleware.ConditionalSessionMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware',
                                      'core.middleware.ExceptionHandlerMiddleware','auditlog.middleware.AuditlogMiddleware']):
        users={n:User.original_objects.get(pk=uid(n)) for n in (1,3,4)}
        def data(n=1,backend='django.contrib.auth.backends.ModelBackend'):
            return {'_auth_user_id':uid(n),'_auth_user_backend':backend,'_auth_user_hash':users[n].get_session_auth_hash()}
        values={'active':data(),'inactive':data(3),'deleted':data(4),
                'social-inactive':data(3,'social_core.backends.google.GoogleOAuth2'),
                'wrong':{**data(),'_auth_user_hash':'wrong'},'empty':{},'oauth':{'state':'fixture'},'none':None,'corrupt':None,'unicode':data()}
        # Malformed cache data must fail before the terminal without revocation.
        for name,value in [('list',[]),('tuple',()),('bool',False),('integer',0),('string','scalar'),('bytes',b'scalar'),('float',1.5)]:
            values['invalid-'+name]=value
        for name,value in [('string','not-a-uuid'),('list',[]),('float',1.5),('negative',-1)]:
            values['invalid-id-'+name]={**data(),'_auth_user_id':value}
        values['invalid-id-unknown-backend']={**data(),'_auth_user_id':'not-a-uuid','_auth_user_backend':'unknown'}
        for name,value in [('integer',10**4300),('list',[10**4300]),('dict',{'hash':10**4300}),('tuple',(10**4300,))]:
            values['invalid-hash-'+name]={**data(),'_auth_user_hash':value}
        payloads={}
        for name,value in values.items():
            logical='django.contrib.sessions.cache'+('è'*8 if name=='unicode' else 'http-session-'+name)
            key=cache.make_key(logical)
            cache.set(logical,value,300)
            payloads[key]=raw.get(key) if name!='corrupt' else b'\x80\x05?'
        def cache_readback():
            for key,value in payloads.items():
                current=raw.get(key)
                assert current==value or (key.endswith('http-session-wrong') and current is None),'HTTP session changed unexpected cache data'
        initialized=False
        def reset(request=None):
            nonlocal initialized
            if initialized:cache_readback()
            for key,value in payloads.items():raw.set(key,value,ex=300)
            initialized=True
            return JsonResponse({'reset':True})
        def terminal(request,*args,**kwargs):
            if request.path=='/__fixtures/session/reset':return reset()
            actor=AuditlogMiddleware._get_actor(request)
            audit={'actor_id':str(actor.pk) if actor else None,'actor_email':actor.email if actor else None,
                   'remote_address':AuditlogMiddleware._get_remote_addr(request),'remote_port':AuditlogMiddleware._get_remote_port(request),'correlation_id':get_cid()}
            status=int(request.path.rsplit('/',1)[1]) if request.path.startswith('/oauth2/status/') else 200
            body={'audit':audit,'sha256':hashlib.sha256(request.body).hexdigest()}
            if 'X-Fixture-Stream' in request.headers:
                async def chunks():yield json.dumps(body).encode()
                response=StreamingHttpResponse(chunks(),status=status,content_type='application/json')
            else:response=JsonResponse(body,status=status)
            if 'X-Fixture-Vary' in request.headers:response['Vary']=request.headers['X-Fixture-Vary']
            if 'X-Fixture-Other-Cookie' in request.headers:response.set_cookie('fixture','retained')
            if 'X-Fixture-Session-Cookie' in request.headers:response.set_cookie('sessionid','fixture-replacement')
            return response
        urls=types.ModuleType('fixture_session_http_urls');urls.urlpatterns=[re_path('.*',terminal)]
        sys.modules[urls.__name__]=urls;clear_url_caches()
        # The released entrypoint uses Gunicorn's UvicornWorker and core.asgi's
        # get_asgi_application HTTP branch. WSGI differs for non-ASCII cookies.
        server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            deadline=time.monotonic()+5
            while not server.started:
                assert thread.is_alive() and time.monotonic()<deadline,'private ASGI server failed to start'
                time.sleep(0.01)
            port=server.servers[0].sockets[0].getsockname()[1]
            with patch.dict(os.environ,TESTS_BASE_URL=f'http://127.0.0.1:{port}'):
                result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_session_http'))
                assert result.wasSuccessful() and not result.skipped,'Django session HTTP contracts failed'
        finally:
            server.should_exit=True;thread.join(timeout=10)
            assert not thread.is_alive(),'private ASGI server did not drain'
            del sys.modules[urls.__name__];clear_url_caches()
        def readback():
            cache_readback()
            assert snapshot()==before,'HTTP session middleware changed persisted tables'
        readback();reset()
        path=work/'session-http.json'
        path.write_text(json.dumps({'secret':secret,'address':address,'payloads':{k:base64.b64encode(v).decode() for k,v in payloads.items()}}));path.chmod(0o600)
        try:
            subprocess.run(['go','test','-race','-tags=sessionhttpintegration','./internal/middleware','./internal/repository','-run','^TestSessionAudit'],cwd=ROOT/'BE_GO',
                env={**tool_env,'ASSOZETA_SESSION_HTTP_FIXTURE':str(path),'ASSOZETA_CONTRACT_PYTHON':sys.executable,
                     'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'},check=True,timeout=120)
            readback()
        finally:
            for key in payloads:raw.delete(key)
            path.unlink();raw.close()
        return result.testsRun
