"""Profile transport and durable readback on isolated Django/Go roots."""
import copy
import datetime
import json
import itertools
import os
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid
from .login_response import base_profile,association_profile,FEATURES,FLAGS


def run(work,log,tool_env,address):
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections
    from django.test import override_settings
    from django.utils import timezone
    from auditlog.context import disable_auditlog
    from application.models import User,SportAssociation,Associate,Instructor,Group
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client,poll
    from .login_images import vectors
    import redis
    import uvicorn
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot();created=[];sessions=[];users={};expected={};associations={}
    images={case['name']:case for case in vectors()};png=images['PNG alpha'];gif=images['GIF 3x2']
    def account(n,role=2,admin=False,owner=None,association=False,avatar=None,expected_avatar=None,**values):
        with disable_auditlog():
            user=User.original_objects.create(user_id=uid(n),username='PROFILE-HTTP-'+str(n),email=f'profile-http-{n}@example.test',first_name='First',last_name='Last',role=role,is_superuser=admin,connected_user_id=uid(owner) if owner else None,password='!synthetic-profile-only',date_joined=timezone.now()-datetime.timedelta(days=60),avatar_image=avatar,**values)
            created.append(uid(n));users[n]=user
            if association:
                associations[n]=SportAssociation.original_objects.create(sport_association_id=uid(n+1000),user=user,denomination='Fixture ASD',tax_code='12345678901',logo=gif['uri'],regulation='present',demand='present',additional_sections=[],review_url=f'https://review-{n}.example.test',review_url_enabled=True)
        profile=base_profile(uid(n),role,admin,avatar if admin or expected_avatar is None else expected_avatar,False)
        profile.update(username=user.username,email=user.email)
        for key in ('collaborator_role','collaborator_permissions','dashboard_layout','phone'):
            if key in values:profile[key]=values[key]
        if admin:profile['is_superuser']=True
        elif role in (1,3) and (association or owner in associations):
            assoc_owner=n if association else owner
            assoc=association_profile(uid(assoc_owner+1000),'present','present',gif['expected']);assoc.pop('membership_card_configuration')
            assoc.update(review_url=f'https://review-{assoc_owner}.example.test',review_url_enabled=True)
            profile.update(sport_association=assoc,temporary_invoice_deletion=values.get('temporary_invoice_deletion',True),preview_and_custom_features=[{'name':FEATURES[0],'description':''}])
        expected[n]={'info':{'role':'administrator' if admin else ('athlete' if role==2 else 'association')},'user_data':profile}
    account(82000,1,association=True,avatar=png['uri'],expected_avatar=png['expected'])
    account(82001,1,association=True,temporary_invoice_deletion=False)
    account(82002,avatar=png['uri'],expected_avatar=png['expected'])
    account(82003,3,owner=82000,collaborator_role=3,collaborator_permissions=False,temporary_invoice_deletion=False,avatar=png['uri'],expected_avatar=png['expected'],dashboard_layout=[{'id':9007199254740993}])
    account(82004,1,admin=True,avatar=png['uri'])
    account(82005,3,owner=82000,collaborator_role=2,collaborator_permissions=[])
    account(82006,1) # no association
    account(82007,3) # orphan collaborator
    account(82008,0,avatar='opaque-image')
    account(82009,4) # unsupported historical role
    account(82010,2,is_active=False)
    account(82011,2,deleted=True)
    account(82012,1,association=True)
    SportAssociation.original_objects.filter(user_id=uid(82012)).update(deleted=True)
    account(82013,4,admin=True)
    with disable_auditlog():
        Associate.objects.create(associate_id=uid(84000),sport_association=associations[82000],user=users[82002],first_name='Member')
        Instructor.objects.create(instructor_id=uid(84001),associated_user_id=uid(82005),user=users[82000],first_name='Instructor')
        Group.objects.bulk_create([Group(group_id=uid(84100+i),name=f'Group {i}',sport_association=associations[82000]) for i in range(3)])
        Group.objects.create(group_id=uid(84999),name='Foreign group',sport_association=associations[82001])
    for n in (82000,82003,82005):expected[n]['user_data']['sport_association']['groups']=[{'label':f'Group {i}','value':uid(84100+i)} for i in range(3)]
    expected[82001]['user_data']['sport_association']['groups']=[{'label':'Foreign group','value':uid(84999)}]
    def token(n):
        value=AccessToken();value['user_id']=uid(n);return str(value)
    cases=[]
    def add(name,actor,response=None,status=200,**kw):cases.append({'name':name,'token':token(actor) if actor else None,'response':copy.deepcopy(expected.get(actor) if response is None and status==200 else response),'status':status,**kw})
    for name,n in [('owner',82000),('foreign-owner',82001),('athlete',82002),('collaborator',82003),('administrator',82004),('instructor',82005),('historical-role-zero',82008)]:add(name,n)
    add('missing-association',82006,status=500)
    add('orphan-collaborator',82007,{'detail':'Associazione del collaboratore non disponibile.'},403)
    add('invalid-role',82009,status=500)
    add('deleted-association',82012,status=500)
    add('invalid-admin-role',82013,status=500)
    add('inactive',82010,{'detail':'User is inactive','code':'user_inactive'},401)
    add('deleted',82011,{'detail':'User not found','code':'user_not_found'},401)
    add('anonymous',None,{'detail':'Authentication credentials were not provided.'},401)
    add('cookie-only',None,{'detail':'Authentication credentials were not provided.'},401,headers={'Cookie':'BKN_AUTH='+token(82000)})
    add('anonymous-options',None,{'detail':'Authentication credentials were not provided.'},401,method='OPTIONS')
    add('post',82000,{'detail':'Method "POST" not allowed.'},405,method='POST')
    add('head',82000,'',405,method='HEAD')
    add('options',82000,{'name':'Profile Info','description':'','renders':['application/json'],'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']},method='OPTIONS')
    add('unsupported-format',82000,{'detail':'Not found.'},404,query='?format=xml')
    add('unsupported-accept',82000,{'detail':'Could not satisfy the request Accept header.'},406,headers={'Accept':'text/html'})
    for i,(name,actor,target,association) in enumerate([('owner-athlete',82000,82002,83000),('owner-staff',82000,82003,83000),('admin-owner',82004,82001,None),('admin-staff',82004,82003,None)]):
        session=uid(85000+i);sessions.append(session);cache.set(session_key(session),{'actor_id':uid(actor),'target_id':uid(target),'association_id':uid(association) if association else None},3600)
        add(name,actor,expected[target],headers={'X-Impersonation-Id':session})
    add('foreign-reference',82000,{'detail':'Risorsa non disponibile per questo utente e associazione.'},403,headers={'X-Impersonation-Id':sessions[1]},query='?sport_association='+uid(83001))
    add('forged-target',82000,{'detail':'Sessione di impersonificazione non valida. Torna al tuo account.','impersonation_invalid':'True'},403,headers={'User-Id':uid(82001)})
    fixture={'cases':cases};path=work/'profile-info.json';path.write_text(json.dumps(fixture));path.chmod(0o600)
    host,port=address.rsplit(':',1);raw=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    def cache_snapshot():return {key:raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}
    before=snapshot();cache_before=cache_snapshot()
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'));thread=threading.Thread(target=server.run,daemon=True);thread.start();api=None
    def contracts(base,flags=(True,False,False)):
        with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_PROFILE_INFO_FIXTURE=str(path)),override_settings(**dict(zip(FLAGS,flags))):
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_profile_info'))
        assert result.wasSuccessful() and not result.skipped,'profile HTTP contracts failed'
        assert snapshot()==before and cache_snapshot()==cache_before,'profile read changed managed rows or session/cache bytes'
    def transitions(base):
        for review,phone,enabled in [(None,'',False),('', '+39000123',True)]:
            try:
                SportAssociation.original_objects.filter(pk=uid(83000)).update(regulation='',review_url=review,review_url_enabled=enabled)
                User.original_objects.filter(pk=uid(82003)).update(phone=phone,temporary_invoice_deletion=enabled)
                with override_settings(**dict(zip(FLAGS,(True,False,False)))):
                    for n in (82000,82003):
                        expected_value=copy.deepcopy(expected[n]);profile=expected_value['user_data']
                        profile['sport_association'].update(regulation='',empty_sections=True,review_url=review,review_url_enabled=enabled)
                        if n==82003:profile.update(phone=phone,temporary_invoice_deletion=enabled)
                        saved_token=next(case['token'] for case in cases if case['name']==('owner' if n==82000 else 'collaborator'))
                        assert Client(base,saved_token).request('GET','/profile/info')==(200,expected_value),'profile returned stale or owner-mapped personal settings'
            finally:
                SportAssociation.original_objects.filter(pk=uid(83000)).update(regulation='present',review_url='https://review-82000.example.test',review_url_enabled=True)
                User.original_objects.filter(pk=uid(82003)).update(phone=None,temporary_invoice_deletion=False)
        assert snapshot()==before and cache_snapshot()==cache_before,'profile transition cleanup differs'
    try:
        deadline=time.monotonic()+10
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.01)
        django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        contracts(django_base);transitions(django_base);print(f'Django: {len(cases)} profile HTTP scenarios and 32 concurrent reads passed',flush=True)
        from TESTS.adapters.go.server import build_server
        build_server(work/'profile-info-server',log,tool_env)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));http_port=sock.getsockname()[1]
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only','DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':host,'REDIS_PORT':port,'REDIS_PASSWORD':'synthetic-identity-only','GO_LISTEN_ADDR':f'127.0.0.1:{http_port}',**{name:getattr(settings,name) for name in ('SECRET_KEY','JWT_SECRET_KEY','JWT_PUBLIC_KEY','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_STORAGE_BUCKET_NAME','AWS_S3_REGION_NAME','AWS_S3_ENDPOINT_URL','AWS_LOCATION','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')},'DEBUG':'False','RATELIMIT_ENABLE':'False',**dict(zip(FLAGS,('True','False','False')))}
        api=subprocess.Popen([str(work/'profile-info-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT);base=f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None
            try:return Client(base).request('GET','/readyz')[0]==200
            except OSError:return False
        poll(ready,bool,timeout=30);contracts(base);transitions(base)
        total=len(cases);concurrent=32
        for flags in itertools.product((False,True),repeat=3):
            if flags==(True,False,False):continue
            selected=copy.deepcopy([case for case in cases if case['name'] in ('owner','athlete','collaborator','administrator')])
            features=[{'name':name,'description':''} for name,enabled in zip(FEATURES,flags) if enabled]
            for case in selected:
                profile=case['response']['user_data']
                if 'preview_and_custom_features' in profile:profile['preview_and_custom_features']=features
            fixture['cases']=selected;fixture['concurrent_count']=8;path.write_text(json.dumps(fixture))
            contracts(django_base,flags)
            api.terminate();api.wait(timeout=30)
            api=subprocess.Popen([str(work/'profile-info-server')],cwd=work,env={**environment,**{name:str(enabled) for name,enabled in zip(FLAGS,flags)}},stdout=log,stderr=subprocess.STDOUT)
            poll(ready,bool,timeout=30);contracts(base,flags)
            total+=len(selected);concurrent+=8
        fixture['cases']=cases;fixture['concurrent_count']=32;path.write_text(json.dumps(fixture))
        subprocess.run(['go','test','-race','-tags=profileinfointegration','./internal/service','-run','^TestProfileInfoPostgres$','-count=1'],cwd=ROOT/'BE_GO',check=True,timeout=120,env={**tool_env,'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable','ASSOZETA_IDENTITY_CACHE':address,'TESTS_PROFILE_INFO_FIXTURE':str(path),'ASSOZETA_IMAGE_CODEC':str(work/'image-codec'),'ASSOZETA_PROFILE_PLAN_OUTPUT':str(ROOT/'quality-reports/go-migration/profile-info/details-plan.json')})
        assert snapshot()==before and cache_snapshot()==cache_before,'profile query checks changed existing state'
        return {'http_scenarios_per_backend':total,'concurrent_reads_per_backend':concurrent,'registration_modes':8,'fresh_state_reads_per_backend':4,'query_plan_additional_tenants':4000,'complete_profile_parity':False}
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        connections.close_all();raw.close();path.unlink(missing_ok=True)
        for session in sessions:cache.delete(session_key(session))
        with disable_auditlog():
            Associate.objects.all_objects().filter(associate_id=uid(84000))._raw_delete('default')
            User.original_objects.filter(pk__in=created).delete()
        assert not thread.is_alive()
        assert snapshot()==original,'profile fixture cleanup changed preexisting rows'
