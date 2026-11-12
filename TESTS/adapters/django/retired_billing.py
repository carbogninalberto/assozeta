"""Actual ASGI/Go retired software billing checks on private account fixtures."""
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
    from application.models import User, BillingPlan, BillingSubscription
    from datetime import datetime, timezone
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client,poll
    import redis
    import uvicorn
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    ids=[uid(82001+i) for i in range(4)]
    plan_id=uid(82100)
    subscription_ids=[uid(82101+i) for i in range(4)]
    assert not User.original_objects.filter(pk__in=ids).exists(),'billing fixture user IDs collide'
    assert not BillingPlan.objects.filter(pk=plan_id).exists(),'billing fixture plan ID collides'
    assert not BillingSubscription.objects.filter(pk__in=subscription_ids).exists(),'billing fixture subscription IDs collide'
    User.original_objects.bulk_create([User(user_id=pk,username=f'retired-billing-{i}',email=f'retired-billing-{i}@example.test',
        role=1,password='!retired-billing-fixture') for i,pk in enumerate(ids)])
    BillingPlan.objects.bulk_create([BillingPlan(billing_plan_id=plan_id,name='Legacy retired test plan',billing_type=2)])
    states=[(datetime(2000,1,1,tzinfo=timezone.utc),True),(datetime(2000,1,1,tzinfo=timezone.utc),False),
            (datetime(2100,1,1,tzinfo=timezone.utc),True),(None,False)]
    BillingSubscription.objects.bulk_create([BillingSubscription(billing_subscription_id=pk,user_id=ids[i],billing_plan_id=plan_id,
        ends_on=ends,auto_renewal=renewal) for i,(pk,(ends,renewal)) in enumerate(zip(subscription_ids,states))])
    def token(n):
        value=AccessToken();value['user_id']=uid(n);return str(value)
    sessions={}
    for index,(name,actor,target,association) in enumerate([
        ('owner_athlete',8,10,80),('owner_staff',8,11,80),('admin_owner',1,9,None),('admin_athlete',1,10,None)]):
        session=uid(82200+index)
        assert cache.get(session_key(session)) is None,'billing session fixture collides'
        cache.set(session_key(session),{'actor_id':uid(actor),'target_id':uid(target),'association_id':uid(association) if association else None},3600)
        sessions[name]=session
    fixture={'tokens':{name:token(n) for name,n in [('owner',8),('athlete',10),('restricted',11),('full',14),
        ('admin',1),('inactive',3),('deleted',4),('expired',82001),('cancelled',82002),('paid',82003),('no-expiration',82004)]},'sessions':sessions}
    path=work/'retired-billing-http.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
    owner=User.original_objects.get(pk=uid(8))
    session_values={'active':{'_auth_user_id':uid(8),'_auth_user_backend':'django.contrib.auth.backends.ModelBackend',
                             '_auth_user_hash':owner.get_session_auth_hash()},'malformed':[]}
    for name,value in session_values.items():cache.set('django.contrib.sessions.cacheretired-billing-'+name,value,300)
    host,port=address.rsplit(':',1)
    raw_cache=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    payloads={cache.make_key('django.contrib.sessions.cacheretired-billing-'+name):raw_cache.get(cache.make_key('django.contrib.sessions.cacheretired-billing-'+name)) for name in session_values}
    before=snapshot()
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    api=None
    def contracts(base):
        with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_RETIRED_BILLING_FIXTURE=str(path)):
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_retired_billing'))
            assert result.wasSuccessful() and not result.skipped,'retired software billing contracts failed'
            return result.testsRun
    def readback():
        assert snapshot()==before,'retired software billing checks changed managed tables'
        assert all(raw_cache.get(key)==payload for key,payload in payloads.items()),'retired software billing checks changed session cache bytes'
    try:
        deadline=time.monotonic()+5
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline,'private retired software billing ASGI server failed to start'
            time.sleep(.01)
        django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        count=contracts(django_base)
        readback()
        from TESTS.adapters.go.server import build_server
        build_server(work/'retired-billing-server',log,tool_env)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0));http_port=listener.getsockname()[1]
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only',
                     'DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':host,'REDIS_PORT':port,'REDIS_USERNAME':'default',
                     'REDIS_PASSWORD':'synthetic-identity-only',**{name: getattr(settings, name) for name in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')},
                     'GO_LISTEN_ADDR':f'127.0.0.1:{http_port}',
                     'SECRET_KEY':settings.SECRET_KEY,'JWT_SECRET_KEY':settings.JWT_SECRET_KEY,'JWT_PUBLIC_KEY':settings.JWT_PUBLIC_KEY,
                     'DEBUG':'False','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY,
                     'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET}
        api=subprocess.Popen([str(work/'retired-billing-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None,'Go retired software billing server stopped'
            try:return Client(base).request('GET','/readyz')[0]==200
            except OSError:return False
        poll(ready,bool,timeout=30)
        assert contracts(base)==count
        readback()
        subprocess.run(['go','test','-race','-tags=retiredbillingintegration','./internal/service','-run','^TestRetiredBillingPostgres$'],cwd=ROOT/'BE_GO',check=True,timeout=90,
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
        for name in session_values:cache.delete('django.contrib.sessions.cacheretired-billing-'+name)
        for session in sessions.values():cache.delete(session_key(session))
        raw_cache.close();path.unlink(missing_ok=True)
        with disable_auditlog():
            BillingSubscription.objects.filter(pk__in=subscription_ids).delete()
            BillingPlan.objects.filter(pk=plan_id).delete()
            User.original_objects.filter(pk__in=ids).delete()
        assert not thread.is_alive(),'private retired software billing ASGI server did not drain'
        assert snapshot()==original,'retired software billing fixture cleanup changed preexisting model state'
