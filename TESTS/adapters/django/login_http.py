"""Public password login over actual Django ASGI and the compiled Go composition root.

Runs only inside accounts.py's disposable current-model PostgreSQL context. Real
hashes, HTTP cookies, session audit attribution and persisted effects are checked.
Live social providers, missing image codecs and deployment switching remain open.
"""
import base64
import io
import datetime
import hashlib
import zlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest

ROOT=Path(__file__).resolve().parents[3]


def run(work,log,tool_env):
    from django.apps import apps
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connection,connections
    from django.test import override_settings
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User,SportAssociation,SportAssociationMembershipCardConfiguration
    from application.models.audit_models import AuditLogIndex
    from rest_framework_simplejwt.tokens import AccessToken,RefreshToken
    from TESTS.adapters.django.login_response import base_profile,association_profile
    from TESTS.adapters.django.scoped_resources import uid
    from TESTS.contracts.test_login_http import LoginHTTPContracts
    from TESTS.http_client import Client,poll
    import redis
    import uvicorn

    def port():
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));return sock.getsockname()[1]
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    password='synthetic-public-login-only'
    secret='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    now=timezone.now();cases=[];users=[];associations=[]
    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS):encoded=make_password(password)
    with disable_auditlog():
        owner=User.original_objects.create(user_id=uid(710000),username='HTTP-OWNER',email='http-owner@example.test',role=1,password='!unused',date_joined=now-datetime.timedelta(days=60))
        users.append(owner.pk)
        owner_association=SportAssociation.original_objects.create(sport_association_id=uid(720000),user=owner,denomination='Fixture ASD',tax_code='12345678901',additional_sections=[])
        associations.append(owner_association.pk)
        SportAssociationMembershipCardConfiguration.objects.create(sport_association=owner_association)
    def add(name,*,role=2,admin=False,active=True,deleted=False,status=200,expected=None,supplied=password,two_fa=False,otp_value=None,valid_otp=False,missing_association=False,fail_finalization=False,session_actor=False,delete_days=None,avatar=None,avatar_expected=None):
        n=len(cases);identifier=uid(711000+n);username=f'Http-{n}';email=f'http-{n}@example.test'
        delete_on=None if delete_days is None else now.date()+datetime.timedelta(days=delete_days)
        with disable_auditlog():
            user=User.original_objects.create(user_id=identifier,username=username,email=email,first_name='First',last_name='Last',role=role,
                password=encoded,is_active=active,deleted=deleted,is_superuser=admin,two_fa=two_fa,two_fa_secret=secret,
                date_joined=now-datetime.timedelta(days=60),connected_user=owner if role==3 else None,delete_on=delete_on,avatar_image=avatar)
            users.append(user.pk);association=owner_association if role==3 else None
            if role==1 and not admin and not missing_association:
                association=SportAssociation.original_objects.create(sport_association_id=uid(721000+n),user=user,denomination='Fixture ASD',tax_code='12345678901',additional_sections=[])
                associations.append(association.pk)
        if status==200:
            expected={'expires_in':14400,'token_type':'Bearer','role':'administrator' if admin else 'athlete' if role==2 else 'association',
                'user_data':base_profile(identifier,role,admin,avatar_expected,role==1 and not admin)}
            expected['user_data'].update(username=username.upper(),email=email)
            if admin:expected['user_data']['is_superuser']=True;expected['requires_welcome']=False
            elif role in (1,3):
                if role==3:expected['requires_welcome']=False
                expected['user_data']['sport_association']=association_profile(str(association.pk),None,None,None)
                expected['user_data']['temporary_invoice_deletion']=True
                expected['user_data']['preview_and_custom_features']=[{'name':'Iscrizione per famiglia','description':''}]
                expected['tables_settings']={}
        payload={'username':email,'password':supplied}
        if otp_value is not None:payload['otp']=otp_value
        case={'name':name,'id':identifier,'role':role,'email':email,'payload':payload,'status':status,'expected':expected,
              'before':username,'normalized':active and not deleted and supplied==password,'association':str(association.pk) if association else None,
              'card_created':role==1 and not admin and not missing_association and (status==200 or fail_finalization),
              'delete_before':delete_on.isoformat() if delete_on else None,'delete_after':None if status==200 and delete_days is not None and delete_days>=0 else delete_on.isoformat() if delete_on else None,
              'actor':str(owner.pk) if session_actor else None}
        if valid_otp:case['totp_secret']=secret
        if session_actor:case['cookie']='sessionid=login-http-active-session'
        cases.append(case)
    add('athlete-with-session-audit',session_actor=True,delete_days=1)
    add('association-creates-card',role=1)
    add('collaborator',role=3)
    add('administrator',role=1,admin=True)
    add('inactive',active=False,status=401,expected={'error':'invalid credentials'})
    add('deleted',deleted=True,status=401,expected={'error':'invalid credential (not found)'})
    add('wrong-password',supplied='wrong',status=401,expected={'error':'invalid credentials'})
    add('otp-required',two_fa=True,status=401,expected={'msg':'OTP code required.'})
    add('otp-invalid',two_fa=True,otp_value='invalid',status=401,expected={'msg':'OTP code required.'})
    add('otp-valid',two_fa=True,valid_otp=True)
    add('historical-zero-role',role=0)
    add('unsupported-role',role=4,status=500)
    add('missing-association',role=1,missing_association=True,status=500)
    add('failed-finalization',role=1,fail_finalization=True,status=500)
    failed_id=cases[-1]['id']
    from PIL import Image
    image=Image.new('RGB',(3,2),(73,73,73));png=io.BytesIO();jpeg=io.BytesIO()
    image.save(png,format='PNG');image.save(jpeg,format='JPEG')
    with Image.open(io.BytesIO(jpeg.getvalue())) as decoded:
        assert decoded.size==(3,2) and decoded.mode=='RGB' and decoded.tobytes()==bytes([73])*18
    add('association-avatar',role=1,avatar='data:image/png;base64,'+base64.b64encode(png.getvalue()).decode(),
        avatar_expected='data:image/jpeg;base64,'+base64.b64encode(jpeg.getvalue()).decode())
    with connection.cursor() as cursor:
        cursor.execute("CREATE FUNCTION public_login_reject_finalization() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.user_id = '"+failed_id+"'::uuid AND NEW.last_login IS NOT NULL THEN RAISE EXCEPTION 'synthetic finalization failure' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
        cursor.execute('CREATE TRIGGER public_login_reject_finalization BEFORE UPDATE OF last_login ON bakney_user FOR EACH ROW EXECUTE FUNCTION public_login_reject_finalization()')
    concurrent_id=uid(711100)
    with disable_auditlog():
        concurrent_user=User.original_objects.create(user_id=concurrent_id,username='HTTP-CONCURRENT',email='http-concurrent@example.test',first_name='First',last_name='Last',role=2,password=encoded,date_joined=now-datetime.timedelta(days=60))
        users.append(concurrent_user.pk)
    concurrent_expected={'expires_in':14400,'token_type':'Bearer','role':'athlete','user_data':base_profile(concurrent_id,2,False,None,False)}
    concurrent_expected['user_data'].update(username='HTTP-CONCURRENT',email=concurrent_user.email)
    concurrent={'id':concurrent_id,'payload':{'username':concurrent_user.email,'password':password},'expected':concurrent_expected}
    seeded=snapshot();events_before=set(LogEntry.objects.values_list('id',flat=True))
    path=work/'login-http.json';path.write_text(json.dumps({'cases':cases,'concurrent':concurrent,'rate_ip':'198.51.100.75','rate_identifier':owner.email}));path.chmod(0o600)
    prior={key:os.environ.get(key) for key in ('TESTS_BASE_URL','TESTS_LOGIN_HTTP_FIXTURE')}
    cache_port=port();redis_process=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(cache_port),'--save','','--appendonly','no','--requirepass','synthetic-public-login-only','--dir',str(work)],stdout=log,stderr=subprocess.STDOUT)
    raw=redis.Redis(host='127.0.0.1',port=cache_port,db=1,password='synthetic-public-login-only',socket_timeout=2)
    api=None;server=None;thread=None
    def readback_and_reset(started,finished):
        expected_events=set()
        concurrent_user.refresh_from_db()
        assert started<=concurrent_user.last_login<=finished
        assert concurrent_user.username=='HTTP-CONCURRENT' and concurrent_user.password==encoded
        User.original_objects.filter(pk=concurrent_id).update(last_login=None)
        for case in cases:
            user=User.original_objects.get(pk=case['id'])
            assert user.username==(case['before'].upper() if case['normalized'] else case['before']),(case['name'],'normalization')
            assert user.password==encoded,(case['name'],'hash changed')
            if case['status']==200:assert started<=user.last_login<=finished,(case['name'],'last login')
            else:assert user.last_login is None,(case['name'],'failed login finalized')
            assert (user.delete_on.isoformat() if user.delete_on else None)==case['delete_after'],(case['name'],'deletion date')
            changes=[]
            if case['normalized']:changes.append({'username':[case['before'],case['before'].upper()]})
            if case['delete_before']!=case['delete_after']:changes.append({'delete_on':[case['delete_before'],'None']})
            events=list(LogEntry.objects.filter(object_pk=case['id']).exclude(id__in=events_before).order_by('id'))
            assert [event.changes_dict for event in events]==changes,(case['name'],'audit changes')
            for event in events:
                expected_events.add(event.pk)
                assert event.action==1 and (str(event.actor_id) if event.actor_id else None)==case['actor'],(case['name'],'audit actor')
                assert event.cid==case['name'] and event.remote_addr=='198.51.100.73',(case['name'],'request audit metadata')
                expected_association=case['association'] if case['role']==1 else str(owner_association.pk) if case['actor'] else None
                indexes=list(AuditLogIndex.objects.filter(log_entry=event))
                assert len(indexes)==int(expected_association is not None),(case['name'],'audit index count')
                if expected_association:
                    assert str(indexes[0].sport_association_id)==expected_association and indexes[0].resolution_path==('user.sport_association' if case['role']==1 else 'actor.sport_association'),(case['name'],'audit index scope')
            if case['role']==1 and case['association']:
                cards=SportAssociationMembershipCardConfiguration.objects.filter(sport_association_id=case['association'])
                assert cards.count()==int(case['card_created']),(case['name'],'card effects')
                if case['card_created']:
                    event=LogEntry.objects.get(object_pk=str(cards.get().pk))
                    expected_events.add(event.pk)
                    assert event.action==0 and event.changes_dict['sport_association']==['None',case['association']],(case['name'],'card creation audit')
                    assert event.cid==case['name'] and event.remote_addr=='198.51.100.73' and event.actor_id is None
                    index=AuditLogIndex.objects.get(log_entry=event)
                    assert str(index.sport_association_id)==case['association'] and index.resolution_path=='direct'
                with disable_auditlog():cards.delete()
            User.original_objects.filter(pk=case['id']).update(username=case['before'],last_login=None,delete_on=case['delete_before'])
        assert set(LogEntry.objects.exclude(id__in=events_before).values_list('pk',flat=True))==expected_events,'undeclared login audit events'
        LogEntry.objects.exclude(id__in=events_before).delete()
        assert snapshot()==seeded,'public login changed undeclared database state'
    def contracts(base):
        os.environ.update(TESTS_BASE_URL=base,TESTS_LOGIN_HTTP_FIXTURE=str(path))
        started=timezone.now()
        suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in ('TESTS.contracts.test_login_transport','TESTS.contracts.test_login_http.LoginHTTPContracts'))
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        assert result.wasSuccessful() and not result.skipped,'public login portable contracts failed'
        for identifier,access,refresh in LoginHTTPContracts.issued:
            assert AccessToken(access)['user_id']==identifier and RefreshToken(refresh)['user_id']==identifier,'Django rejected public credentials'
        readback_and_reset(started,timezone.now())
        return result.testsRun
    def rate_contracts(base):
        # Independently declared django-ratelimit key protocol. Seed both windows
        # to make denial stable even if the wall clock crosses the jittered edge.
        for key in raw.scan_iter(match=':1:rl:*'):raw.delete(key)
        ts=int(time.time())
        for group,value,limit in [('login-ip','198.51.100.75',20),('login-account',owner.email,5)]:
            end=ts-ts%60+zlib.crc32(value.encode())%60
            if end<ts:end+=60
            for window in (end,end+60):
                key=':1:rl:'+hashlib.sha256(f'{group}{limit}/60s{value}{window}POST'.encode()).hexdigest()
                raw.set(key,str(limit),ex=65)
        baseline=snapshot()
        os.environ['TESTS_BASE_URL']=base
        suite=unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_login_http.LoginRateHTTPContracts')
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        assert result.wasSuccessful() and not result.skipped,'public login rate/session order differs'
        assert snapshot()==baseline,'rate-denied requests changed database state'
        return result.testsRun
    try:
        deadline=time.monotonic()+10
        while True:
            try:
                if raw.ping():break
            except redis.ConnectionError:
                if time.monotonic()>deadline:raise
                time.sleep(.025)
        cache_config={'default':{'BACKEND':'django_redis.cache.RedisCache','LOCATION':f'redis://127.0.0.1:{cache_port}/1','OPTIONS':{'CLIENT_CLASS':'django_redis.client.DefaultClient','PASSWORD':'synthetic-public-login-only'}}}
        with override_settings(CACHES=cache_config,PASSWORD_HASHERS=PASSWORD_HASHERS,RATELIMIT_ENABLE=False,CORS_ORIGIN_ALLOW_ALL=False,CORS_ALLOWED_ORIGINS=['https://allowed.example.test'],FAMILY_REGISTRATION_ENABLED=True,FEDERATION_REGISTRATION_ENABLED=False,MULTIPLE_REGISTRATION_ENABLED=False):
            cache.set('django.contrib.sessions.cachelogin-http-active-session',{'_auth_user_id':str(owner.pk),'_auth_user_backend':'django.contrib.auth.backends.ModelBackend','_auth_user_hash':owner.get_session_auth_hash()},300)
            cache.set('django.contrib.sessions.cachelogin-http-malformed',[],300)
            session_bytes={key:raw.get(key) for key in raw.scan_iter()}
            server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,log_level='warning',lifespan='off'))
            thread=threading.Thread(target=server.run,daemon=True);thread.start()
            deadline=time.monotonic()+20
            while not server.started:
                assert thread.is_alive() and time.monotonic()<deadline,'private login ASGI server failed to start'
                time.sleep(.01)
            django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
            count=contracts(django_base)
            assert {key:raw.get(key) for key in raw.scan_iter()}==session_bytes,'Django login changed cached sessions'
            print(f'Django public login: {count} portable methods, {len(cases)} credential/effect scenarios passed',flush=True)
            from TESTS.adapters.go.server import build_server
            build_server(work/'login-server',log,tool_env)
            go_port=port()
            environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only','DBHOST':str(work),'DBPORT':'5432',
                'REDIS_HOST':'127.0.0.1','REDIS_PORT':str(cache_port),'REDIS_PASSWORD':'synthetic-public-login-only',
                'GO_LISTEN_ADDR':f'127.0.0.1:{go_port}','SECRET_KEY':settings.SECRET_KEY,'JWT_SECRET_KEY':settings.JWT_SECRET_KEY,'JWT_PUBLIC_KEY':settings.JWT_PUBLIC_KEY,
                'DEBUG':'False','RATELIMIT_ENABLE':'False','CORS_ALLOWED_ORIGINS':'https://allowed.example.test','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'',
                **{name:getattr(settings,name) for name in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_STORAGE_BUCKET_NAME','AWS_S3_REGION_NAME','AWS_S3_ENDPOINT_URL','AWS_LOCATION')}}
            api=subprocess.Popen([str(work/'login-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
            base=f'http://127.0.0.1:{go_port}'
            def ready():
                assert api.poll() is None,'Go login process stopped'
                try:return Client(base).request('GET','/readyz')[0]==200
                except OSError:return False
            poll(ready,bool,timeout=30)
            assert contracts(base)==count
            remaining={}
            for key in raw.scan_iter():
                if key.startswith(b'assozeta:go:readiness:'):
                    # Operational readiness writes an independent ten-second key.
                    assert raw.get(key)==b'ok' and 0<=raw.ttl(key)<=10
                else:remaining[key]=raw.get(key)
            assert remaining==session_bytes,'Go public login changed cached sessions'
            with override_settings(RATELIMIT_ENABLE=True):
                rate_count=rate_contracts(django_base)
            api.terminate();api.wait(timeout=30)
            environment['RATELIMIT_ENABLE']='True'
            api=subprocess.Popen([str(work/'login-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
            poll(ready,bool,timeout=30)
            assert rate_contracts(base)==rate_count
            assert all(raw.get(key)==value for key,value in session_bytes.items()),'rate checks changed sessions'
            return {'contracts_per_backend':count,'rate_contracts_per_backend':rate_count,'credential_scenarios':len(cases),'concurrent_requests_per_backend':16,'public_login_complete':False}
    finally:
        if server is not None:server.should_exit=True
        if thread is not None:thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        raw.close()
        redis_process.terminate();redis_process.wait(timeout=10)
        connections.close_all()
        with connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER public_login_reject_finalization ON bakney_user')
            cursor.execute('DROP FUNCTION public_login_reject_finalization()')
        LogEntry.objects.exclude(id__in=events_before).delete()
        with disable_auditlog():
            SportAssociation.original_objects.filter(pk__in=associations).delete()
            User.original_objects.filter(pk__in=users).delete()
        path.unlink(missing_ok=True)
        for key,value in prior.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value
        if thread is not None:assert not thread.is_alive(),'private login ASGI server did not drain'
        assert snapshot()==before,'public login fixture cleanup changed existing state'
