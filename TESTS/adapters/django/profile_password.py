"""Profile password response/effect oracle on private Django/Go HTTP roots."""
import datetime
import json
import os
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from .accounts import ROOT
from .scoped_resources import uid
from .login_response import base_profile,association_profile


def run(work,log,tool_env,address):
    from django.apps import apps
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password,check_password,PBKDF2PasswordHasher,PBKDF2SHA1PasswordHasher,BCryptSHA256PasswordHasher,ScryptPasswordHasher,Argon2PasswordHasher
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections,connection,transaction
    from django.test import override_settings
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User,SportAssociation
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client,poll
    from .login_images import vectors
    import bcrypt
    import redis
    import uvicorn
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot();created=[];sessions=[];cases=[];initial={}
    old='Previous1!pass';new='Replacement2!pass'
    stale=PBKDF2PasswordHasher().encode(old,'synthetic-password-fixture',iterations=1)
    modern=PBKDF2PasswordHasher().encode(old,'synthetic-password-fixture',iterations=1000000)
    images={v['name']:v for v in vectors()};png=images['PNG alpha'];gif=images['GIF 3x2']
    def token(n):
        value=AccessToken();value['user_id']=uid(n);return str(value)
    def add(name,*,current=old,replacement=new,encoded=modern,status=200,outcome='replace',role=2,grants=None,actor=None,impersonation=None,avatar=False,association=True,body=None,response=None,**kw):
        n=86000+len(cases);identifier=uid(n)
        with disable_auditlog():
            user=User.original_objects.create(user_id=identifier,username='PASSWORD-HTTP-'+str(n),email=f'password-http-{n}@example.test',first_name='First',last_name='Last',role=role,password=encoded,date_joined=timezone.now()-datetime.timedelta(days=60),connected_user_id=uid(8) if role==3 else None,collaborator_role=3 if role==3 else 1,collaborator_permissions=grants or [],avatar_image=png['uri'] if avatar else None)
            created.append(identifier);initial[identifier]=encoded
            if role==1 and association:SportAssociation.original_objects.create(sport_association_id=uid(n+1000),user=user,denomination='Fixture ASD',tax_code='12345678901',logo=gif['uri'],regulation='present',demand='present',additional_sections=[])
        profile=base_profile(identifier,role,False,png['expected'] if avatar else None,False)
        profile.update(username=user.username,email=user.email,collaborator_role=user.collaborator_role,collaborator_permissions=grants or [])
        if role==1:
            association=association_profile(uid(n+1000),'present','present',gif['expected']);association.pop('membership_card_configuration')
            profile['sport_association']=association
        expected={'msg':'password updated with success.' if outcome=='replace' else 'old password not correct.','role':'athlete' if role in (-1,2) else 'association','user_data':profile}
        if outcome!='replace':expected['code']='EC000'
        if status==400:expected={'msg':'password not valid.'}
        headers=kw.pop('headers',{})
        if impersonation is not None:
            session=uid(n+2000);sessions.append(session)
            cache.set(session_key(session),{'actor_id':uid(actor),'target_id':identifier,'association_id':uid(impersonation) if impersonation else None},3600)
            headers['X-Impersonation-Id']=session
        cases.append({'name':name,'token':token(actor or n),'target':identifier,'body':body if body is not None else {'password_data':{'current_password':current,'new_password':replacement}},'status':status,'response':response if response is not None else expected,'headers':headers,'outcome':outcome,'old':current,'new':replacement,**kw})
    add('athlete',avatar=True)
    add('owner',role=1)
    add('granted-collaborator',role=3,grants=['other.settings.update'])
    add('missing-association-after-write',role=1,association=False,status=500)
    add('invalid-role-after-write',role=4,status=500)
    add('role-zero',role=0)
    for name,hasher in [('sha1',PBKDF2SHA1PasswordHasher()),('bcrypt-sha256',BCryptSHA256PasswordHasher()),('scrypt',ScryptPasswordHasher()),('argon2',Argon2PasswordHasher())]:
        with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS):encoded=hasher.encode(old,hasher.salt())
        add('supported-'+name,encoded=encoded)
    add('numeric-matching-old',current=123,encoded=PBKDF2PasswordHasher().encode('123','synthetic-password-fixture',iterations=1000000))
    add('numeric-stale-old',current=123,encoded=PBKDF2PasswordHasher().encode('123','synthetic-password-fixture',iterations=1),status=500,outcome='unchanged')
    add('malformed-hash',encoded='pbkdf2_sha256$invalid',status=500,outcome='unchanged')
    add('database-write-failure',status=500,outcome='unchanged')
    add('database-rehash-failure',encoded=stale,status=500,outcome='unchanged')
    add('same-password',replacement=old)
    add('wrong-old',current='Incorrect1!pass',outcome='unchanged')
    add('weak-new',replacement='weak',status=400,outcome='unchanged')
    add('stale-hash-weak-new',replacement='weak',encoded=stale,status=400,outcome='rehash')
    add('stale-hash-wrong-old',current='Incorrect1!pass',encoded=stale,outcome='unchanged')
    add('stale-hash-valid-new',encoded=stale)
    add('unusable',encoded='!synthetic-unusable',outcome='unchanged')
    add('unknown-hasher',encoded='unknown$synthetic',outcome='unchanged')
    add('raw-bcrypt-not-model-password',encoded=bcrypt.hashpw(old.encode(),bcrypt.gensalt(rounds=4)).decode(),outcome='unchanged')
    add('null-old',current=None,outcome='unchanged')
    add('numeric-old',current=123,outcome='unchanged')
    add('empty-old',current='',outcome='unchanged')
    for name,value in [('short','A1!aaaaaa'),('no-ascii-upper','À1!aaaaaaaa'),('no-special','A1aaaaaaaa'),('plus-not-special','A1+aaaaaaa'),('no-digit','Ab!aaaaaaa'),('internal-newline','A1!aa\naaaaa')]:
        add(name,replacement=value,status=400,outcome='unchanged')
    add('unicode-length',replacement='A1!ééééééé')
    add('terminal-newline',replacement='A1!aaaaaaa\n')
    for special in '!@#$&.-_*':add('special-'+special,replacement='A1'+special+'aaaaaaa')
    for name,value in [('null-new',None),('numeric-new',123),('list-new',[])]:add(name,replacement=value,status=500,outcome='unchanged')
    add('missing-current',body={'password_data':{'new_password':new}},status=500,outcome='unchanged')
    add('missing-password-data',body={},status=500,outcome='unchanged')
    add('nonobject',body=[],status=500,outcome='unchanged')
    add('restricted-collaborator',role=3,status=403,outcome='unchanged',response={'detail':'Missing permission: other.settings.update'})
    add('admin-direct',actor=1,status=403,outcome='unchanged',response={'detail':'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add('admin-athlete',actor=1,impersonation=0)
    add('admin-collaborator',role=3,grants=['other.settings.update'],actor=1,impersonation=0)
    add('association-bound-collaborator',role=3,grants=['other.settings.update'],actor=8,impersonation=80,status=403,outcome='unchanged',response={'detail':'Torna al tuo account per gestire le credenziali.'})
    add('forged-target',headers={'User-Id':uid(9)},status=403,outcome='unchanged',response={'detail':'Solo il titolare può impersonare utenti.','impersonation_invalid':'True'})
    add('anonymous',status=401,outcome='unchanged',response={'detail':'Authentication credentials were not provided.'});cases[-1]['token']=None
    add('inactive',actor=3,status=401,outcome='unchanged',response={'detail':'User is inactive','code':'user_inactive'})
    add('deleted',actor=4,status=401,outcome='unchanged',response={'detail':'User not found','code':'user_not_found'})
    add('get',method='GET',status=405,outcome='unchanged',response={'detail':'Method "GET" not allowed.'})
    add('head',method='HEAD',status=405,outcome='unchanged',response='')
    add('options',method='OPTIONS',outcome='unchanged',response={'name':'Profile Update Password','description':'','renders':['application/json'],'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']})
    add('format',query='?format=xml',status=404,outcome='unchanged',response={'detail':'Not found.'})
    add('accept',headers={'Accept':'text/html'},status=406,outcome='unchanged',response={'detail':'Could not satisfy the request Accept header.'})
    add('malformed-json',raw_body='{',status=400,outcome='unchanged',response={'detail':'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'})
    add('form-nested-string',raw_body='password_data=x',content_type='application/x-www-form-urlencoded',status=500,outcome='unchanged')
    add('unsupported-media',raw_body='x',content_type='text/plain',status=415,outcome='unchanged',response={'detail':'Unsupported media type "text/plain" in request.'})
    path=work/'profile-password.json';path.write_text(json.dumps({'cases':cases}));path.chmod(0o600)
    host,port=address.rsplit(':',1);raw=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    def cache_snapshot():return {key:raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}
    seeded=snapshot();cache_before=cache_snapshot()
    rejected=[case['target'] for case in cases if case['name'] in ('database-write-failure','database-rehash-failure')]
    with connection.cursor() as cursor:
        cursor.execute("CREATE FUNCTION profile_http_reject_password() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic password failure'; END $$")
        ids=','.join("'"+identifier+"'::uuid" for identifier in rejected)
        cursor.execute('CREATE TRIGGER profile_http_reject_password BEFORE UPDATE OF password ON bakney_user FOR EACH ROW WHEN (NEW.user_id IN ('+ids+')) EXECUTE FUNCTION profile_http_reject_password()')

    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'));thread=threading.Thread(target=server.run,daemon=True);thread.start();api=None
    def concurrent_updates(base):
        case=cases[0];identifier=case['target'];events=set(LogEntry.objects.values_list('pk',flat=True))
        reset=PBKDF2PasswordHasher().encode('Concurrent3!reset','synthetic-password-fixture',iterations=1000000)
        for scenario,parallel in [('profile',1),('reset',1),('duplicate',2)]:
            try:
                with ThreadPoolExecutor(max_workers=parallel) as pool:
                    with transaction.atomic():
                        User.original_objects.select_for_update().get(pk=identifier)
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_backend_pid()');blocker=cursor.fetchone()[0]
                        futures=[pool.submit(Client(base,case['token']).request,'PATCH','/profile/update/password',case['body']) for _ in range(parallel)]
                        def blocked():
                            with connection.cursor() as cursor:
                                cursor.execute('SELECT pg_stat_clear_snapshot()')
                                cursor.execute("WITH RECURSIVE waiting(pid) AS (SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) UNION SELECT a.pid FROM pg_stat_activity a JOIN waiting w ON w.pid=ANY(pg_blocking_pids(a.pid))) SELECT count(*) FROM waiting",[blocker])
                                return cursor.fetchone()[0]
                        poll(blocked,lambda count:count==parallel,timeout=15,interval=.01)
                        if scenario=='profile':User.original_objects.filter(pk=identifier).update(first_name='Concurrent')
                        if scenario=='reset':User.original_objects.filter(pk=identifier).update(password=reset)
                    results=[future.result(timeout=20) for future in futures]
                assert all(status==200 for status,_ in results),'concurrent password HTTP failure'
                success=sum(body.get('msg')=='password updated with success.' for _,body in results)
                assert success==(0 if scenario=='reset' else 1),scenario+' accepted stale or duplicate credential'
                state=User.original_objects.get(pk=identifier)
                assert state.first_name==('Concurrent' if scenario=='profile' else 'First'),scenario+' overwrote concurrent profile'
                assert check_password('Concurrent3!reset' if scenario=='reset' else new,state.password),'concurrent password was lost'
                assert set(LogEntry.objects.values_list('pk',flat=True))==events,'password-only update generated audit'
            finally:
                User.original_objects.filter(pk=identifier).update(password=initial[identifier],first_name='First')
                LogEntry.objects.exclude(pk__in=events).delete()
            assert snapshot()==seeded and cache_snapshot()==cache_before,'concurrency cleanup changed state'
        print('Three locked-row password concurrency scenarios passed',flush=True)
    def real_profile_interleaving(password_base,profile_base):
        case=cases[0];identifier=case['target'];events=set(LogEntry.objects.values_list('pk',flat=True))
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                with transaction.atomic():
                    User.original_objects.select_for_update().get(pk=identifier)
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_backend_pid()');blocker=cursor.fetchone()[0]
                    def blocked():
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_stat_clear_snapshot()')
                            cursor.execute("WITH RECURSIVE waiting(pid) AS (SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid)) UNION SELECT a.pid FROM pg_stat_activity a JOIN waiting w ON w.pid=ANY(pg_blocking_pids(a.pid))) SELECT count(*) FROM waiting",[blocker])
                            return cursor.fetchone()[0]
                    password_future=pool.submit(Client(password_base,case['token']).request,'PATCH','/profile/update/password',case['body'])
                    poll(blocked,lambda count:count==1,timeout=15,interval=.01)
                    profile_body={'user_data':{'first_name':'Concurrent HTTP','last_name':None,'username':None,'avatar_image':None,'sport_association':None}}
                    profile_future=pool.submit(Client(profile_base,case['token']).request,'PATCH','/profile/update',profile_body)
                    poll(blocked,lambda count:count==2,timeout=15,interval=.01)
                password_result=password_future.result(timeout=20);profile_result=profile_future.result(timeout=20)
            assert password_result[0]==200 and password_result[1]['msg']=='password updated with success.' and profile_result[0]==200,'HTTP update failed before race assertion'
            state=User.original_objects.get(pk=identifier)
            assert state.first_name=='Concurrent HTTP','profile update did not persist'
            entries=list(LogEntry.objects.exclude(pk__in=events))
            assert len(entries)==1 and entries[0].changes_dict=={'first_name':['First','Concurrent HTTP']} and entries[0].object_pk==identifier and str(entries[0].actor_id)==identifier,'profile audit changed unrelated fields'
            assert check_password(new,state.password),'Legacy Django profile HTTP save overwrote the password change'
        finally:
            User.original_objects.filter(pk=identifier).update(password=initial[identifier],first_name='First')
            LogEntry.objects.exclude(pk__in=events).delete()
        assert snapshot()==seeded and cache_snapshot()==cache_before
    def contracts(base):
        with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_PROFILE_PASSWORD_FIXTURE=str(path)),override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS):
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_profile_password.ProfilePasswordContracts'))
            assert result.wasSuccessful() and not result.skipped,'password HTTP contracts failed'
            for case in cases:
                encoded=User.original_objects.values_list('password',flat=True).get(pk=case['target'])
                if case['outcome']=='unchanged':assert encoded==initial[case['target']],case['name']+' changed hash'
                else:
                    expected=case['new'] if case['outcome']=='replace' else old
                    assert encoded!=initial[case['target']] and encoded.startswith('pbkdf2_sha256$1000000$') and check_password(expected,encoded),case['name']+' incompatible password'
                if case['target'] not in rejected:User.original_objects.filter(pk=case['target']).update(password=initial[case['target']])
        assert snapshot()==seeded,'password operation changed unrelated managed rows or audit records'
        assert cache_snapshot()==cache_before,'password operation changed sessions/cache'
    try:
        deadline=time.monotonic()+10
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.01)
        django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        contracts(django_base);concurrent_updates(django_base);print(f'Django: {len(cases)} declared password HTTP/effect cases passed',flush=True)
        from TESTS.adapters.go.server import build_server
        build_server(work/'profile-password-server',log,tool_env)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));http_port=sock.getsockname()[1]
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only','DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':host,'REDIS_PORT':port,'REDIS_PASSWORD':'synthetic-identity-only','GO_LISTEN_ADDR':f'127.0.0.1:{http_port}',**{name:getattr(settings,name) for name in ('SECRET_KEY','JWT_SECRET_KEY','JWT_PUBLIC_KEY','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_STORAGE_BUCKET_NAME','AWS_S3_REGION_NAME','AWS_S3_ENDPOINT_URL','AWS_LOCATION','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')},'DEBUG':'False','RATELIMIT_ENABLE':'False'}
        api=subprocess.Popen([str(work/'profile-password-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT);base=f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None
            try:return Client(base).request('GET','/readyz')[0]==200
            except OSError:return False
        poll(ready,bool,timeout=30);contracts(base);concurrent_updates(base)
        for producer in (django_base,base):real_profile_interleaving(producer,django_base)
        print("Two real legacy-profile/password HTTP races passed",flush=True)
        for producer,consumer in [(django_base,base),(base,django_base),(django_base,django_base),(base,base)]:
            target=cases[0]['target'];before=User.original_objects.filter(pk=target).values().get()
            try:
                with patch.dict(os.environ,TESTS_PROFILE_PASSWORD_FIXTURE=str(path),TESTS_PASSWORD_PRODUCER_URL=producer,TESTS_PASSWORD_CONSUMER_URL=consumer),override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS):
                    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_profile_password.ProfilePasswordLoginFlows'))
                assert result.wasSuccessful() and not result.skipped,'password/login switching failed'
                after=User.original_objects.filter(pk=target).values().get()
                assert after['last_login'] is not None and after['password']!=before['password']
                assert {k:v for k,v in after.items() if k not in ('password','last_login')}=={k:v for k,v in before.items() if k not in ('password','last_login')},'password/login changed unrelated fields'
            finally:User.original_objects.filter(pk=target).update(password=before['password'],last_login=before['last_login'])
            assert snapshot()==seeded and cache_snapshot()==cache_before,'password/login switching changed unrelated state'
        subprocess.run(['go','test','-race','-tags=profilepasswordintegration','./internal/service','-run','^TestProfilePasswordPostgres$','-count=1'],cwd=ROOT/'BE_GO',check=True,timeout=240,env={**tool_env,'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        assert snapshot()==seeded and cache_snapshot()==cache_before,'Go mutation integration changed fixture state'
        return {'http_effect_cases_per_backend':len(cases),'locked_concurrency_scenarios_per_backend':3,'cross_runtime_login_flows':4,'legacy_profile_writer_races':2,'complete_profile_password_parity':False}
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        connections.close_all();raw.close();path.unlink(missing_ok=True)
        for session in sessions:cache.delete(session_key(session))
        with connection.cursor() as cursor:
            cursor.execute('DROP TRIGGER profile_http_reject_password ON bakney_user')
            cursor.execute('DROP FUNCTION profile_http_reject_password()')
        with disable_auditlog():User.original_objects.filter(pk__in=created).delete()
        assert snapshot()==original,'password fixture cleanup changed preexisting state'
