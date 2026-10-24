"""Actual ASGI/Go two-factor status checks on private account fixtures."""
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
    account_id=uid(81011)
    assert not User.original_objects.filter(pk=account_id).exists(),'2FA fixture ID collides'
    saved={str(row.pk):row.two_fa for row in User.original_objects.filter(pk__in=[uid(n) for n in (8,9,10,14)])}
    User.original_objects.filter(pk=uid(8)).update(two_fa=True)
    User.original_objects.filter(pk__in=[uid(n) for n in (9,10,14)]).update(two_fa=False)
    User.original_objects.bulk_create([User(user_id=account_id,username='two-factor-info-granted',email='two-factor-info@example.test',
        role=3,connected_user_id=uid(8),collaborator_role=3,collaborator_permissions=['other.settings.read'],
        password='!two-factor-info-fixture',two_fa=False)])
    def token(n):
        value=AccessToken();value['user_id']=uid(n);return str(value)
    sessions={}
    for index,(name,actor,target,association) in enumerate([
        ('owner_athlete',8,10,80),('owner_granted',8,81011,80),('admin_foreign',1,9,None),('admin_granted',1,81011,None)]):
        session=uid(81100+index)
        assert cache.get(session_key(session)) is None,'2FA session fixture collides'
        cache.set(session_key(session),{'actor_id':uid(actor),'target_id':uid(target),'association_id':uid(association) if association else None},3600)
        sessions[name]=session
    fixture={'tokens':{name:token(n) for name,n in [('owner',8),('athlete',10),('foreign',9),('granted',81011),
        ('full',14),('restricted',11),('admin',1),('inactive',3),('deleted',4)]},'sessions':sessions}
    path=work/'two-factor-info-http.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
    owner=User.original_objects.get(pk=uid(8))
    session_values={'active':{'_auth_user_id':uid(8),'_auth_user_backend':'django.contrib.auth.backends.ModelBackend',
                             '_auth_user_hash':owner.get_session_auth_hash()},'malformed':[]}
    for name,value in session_values.items():cache.set('django.contrib.sessions.cachetwo-factor-info-'+name,value,300)
    host,port=address.rsplit(':',1)
    raw_cache=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    payloads={cache.make_key('django.contrib.sessions.cachetwo-factor-info-'+name):raw_cache.get(cache.make_key('django.contrib.sessions.cachetwo-factor-info-'+name)) for name in session_values}
    before=snapshot()
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    api=None
    def contracts(base):
        with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_TWO_FACTOR_INFO_FIXTURE=str(path)):
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_two_factor_info'))
            assert result.wasSuccessful() and not result.skipped,'two-factor status contracts failed'
            return result.testsRun
    def readback():
        assert snapshot()==before,'two-factor status checks changed managed tables'
        assert all(raw_cache.get(key)==payload for key,payload in payloads.items()),'two-factor status checks changed session cache bytes'
    def transitions(base):
        client=Client(base)
        try:
            for value in (False,True):
                User.original_objects.filter(pk=uid(8)).update(two_fa=value)
                for actor in ('owner','granted'):
                    response=client.request('GET','/two-fa/info',headers={'Authorization':'Bearer '+fixture['tokens'][actor]})
                    assert response[:2]==(200,{'data':{'enabled':value}}),'same credential returned stale 2FA scope state'
        finally:User.original_objects.filter(pk=uid(8)).update(two_fa=True)
    try:
        deadline=time.monotonic()+5
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline,'private two-factor status ASGI server failed to start'
            time.sleep(.01)
        django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        count=contracts(django_base)
        transitions(django_base)
        readback()
        from TESTS.adapters.go.server import build_server
        build_server(work/'two-factor-info-server',log,tool_env)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));http_port=listener.getsockname()[1]
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only',
                     'DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':host,'REDIS_PORT':port,'REDIS_USERNAME':'default',
                     'REDIS_PASSWORD':'synthetic-identity-only',**{name: getattr(settings, name) for name in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')},
                     'GO_LISTEN_ADDR':f'127.0.0.1:{http_port}',
                     'SECRET_KEY':settings.SECRET_KEY,'JWT_SECRET_KEY':settings.JWT_SECRET_KEY,'JWT_PUBLIC_KEY':settings.JWT_PUBLIC_KEY,
                     'DEBUG':'False','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY,
                     'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET}
        api=subprocess.Popen([str(work/'two-factor-info-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None,'Go two-factor status server stopped'
            try:return Client(base).request('GET','/readyz')[0]==200
            except OSError:return False
        poll(ready,bool,timeout=30)
        assert contracts(base)==count
        transitions(base)
        readback()
        subprocess.run(['go','test','-race','-tags=twofactorinfointegration','./internal/service','-run','^TestTwoFactorInfoPostgres$'],cwd=ROOT/'BE_GO',check=True,timeout=90,
                       env={**tool_env,'ASSOZETA_IDENTITY_CACHE':address,
                            'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        readback()
        return count
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        for name in session_values:cache.delete('django.contrib.sessions.cachetwo-factor-info-'+name)
        for session in sessions.values():cache.delete(session_key(session))
        raw_cache.close();path.unlink(missing_ok=True)
        with disable_auditlog():User.original_objects.filter(pk=account_id).delete()
        for pk,value in saved.items():User.original_objects.filter(pk=pk).update(two_fa=value)
        assert not thread.is_alive(),'private two-factor status ASGI server did not drain'
        assert snapshot()==original,'two-factor status fixture cleanup changed preexisting model state'
