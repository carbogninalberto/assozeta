"""Real HTTP provisioning/QR/readback checks using private PostgreSQL and Redis."""
import json
import os
import socket
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid


def run(work,log,tool_env,address):
    artifacts=ROOT/'quality-reports/go-migration/two-factor-setup'
    artifacts.mkdir(parents=True,exist_ok=True)
    from django.apps import apps
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.test import override_settings
    from core import settings as reference_settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections
    from auditlog.models import LogEntry
    from auditlog.context import disable_auditlog
    from application.models import User
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client,poll
    import redis
    import datetime
    from django.utils import timezone
    from django.contrib.auth.hashers import make_password
    import uvicorn
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot();created=[];sessions=[]
    with disable_auditlog():
        for n,grants in ((81600,['other.settings.update']),(81601,['other.settings.read'])):
            User.original_objects.create(user_id=uid(n),username=f'TWO-FA-SETUP-{n}',email=f'two-fa-setup-{n}@example.test',role=3,connected_user_id=uid(8),collaborator_role=3,collaborator_permissions=grants,password='!synthetic-setup')
            created.append(uid(n))
    login_password='synthetic-setup-login-only'
    with disable_auditlog():
        User.original_objects.create(user_id=uid(81610),username='SETUP-LOGIN',email='setup-login@example.test',role=2,first_name='Setup',last_name='Login',password=make_password(login_password,hasher='pbkdf2_sha256'),date_joined=timezone.now()-datetime.timedelta(days=60))
    created.append(uid(81610))
    def token(n):
        value=AccessToken();value['user_id']=uid(n);return str(value)
    cases=[]
    def add(name,actor,status=200,response=None,**kwargs):
        cases.append({'name':name,'token':token(actor) if actor else None,'status':status,'response':response,**kwargs})
    add('owner',8);add('athlete',10);add('granted-collaborator',81600);add('full-collaborator',14)
    add('read-only-collaborator',81601,403,{'detail':'Missing permission: other.settings.update'})
    add('restricted-collaborator',11,403,{'detail':'Missing permission: other.settings.update'})
    add('admin-needs-impersonation',1,403,{'detail':'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add('inactive',3,401,{'detail':'User is inactive','code':'user_inactive'})
    add('deleted',4,401,{'detail':'User not found','code':'user_not_found'})
    add('anonymous',None,401,{'detail':'Authentication credentials were not provided.'})
    add('cookie-is-not-bearer',None,401,{'detail':'Authentication credentials were not provided.'},headers={'Cookie':'BKN_AUTH='+token(8)})
    add('anonymous-italian',None,401,{'detail':'Non sono state immesse le credenziali di autenticazione.'},headers={'Accept-Language':'it'})
    add('anonymous-options',None,401,{'detail':'Authentication credentials were not provided.'},method='OPTIONS')
    add('post',8,405,{'detail':'Method "POST" not allowed.'},method='POST')
    add('head',8,405,'',method='HEAD')
    add('options',8,200,{'name':'Two Fa Setup','description':'API endpoint to setup a new two fa secret\n:param request: data: { OTP, username, password }\n:return:',
        'renders':['application/json'],'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']},method='OPTIONS')
    for n,name,actor,target,association in [(81700,'admin-owner',1,9,None),(81701,'admin-collaborator',1,81600,None),(81702,'owner-collaborator',8,81600,80),(81703,'owner-athlete',8,10,80)]:
        session=uid(n);sessions.append(session)
        cache.set(session_key(session),{'actor_id':uid(actor),'target_id':uid(target),'association_id':uid(association) if association else None},3600)
        if name=='owner-athlete':
            add(name,actor,403,{'detail':'Operazione non disponibile nel contesto dell’associazione.'},headers={'X-Impersonation-Id':session})
        else:add(name,actor,headers={'X-Impersonation-Id':session})
    add('foreign-scope',8,403,{'detail':'Risorsa non disponibile per questo utente e associazione.'},headers={'X-Impersonation-Id':uid(81702)},query='?sport_association='+uid(90))
    host,port=address.rsplit(':',1);raw=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    # Readiness probes own separate short-lived keys; bootstrap is outside setup.
    def cache_snapshot():return {key:raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}
    cache_before=cache_snapshot();seeded=snapshot();fixture_path=work/'two-factor-setup.json'
    fixture={'account_name':'setup-support@example.test','issuer':'Assozeta Contract','cases':cases,'login':{'id':uid(81610),'username':'SETUP-LOGIN','password':login_password,'token':token(81610)}}
    fixture_path.write_text(json.dumps(fixture));fixture_path.chmod(0o600)
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start();api=None
    def contracts(base):
        with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_TWO_FACTOR_SETUP_FIXTURE=str(fixture_path)):
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_two_factor_setup.TwoFactorSetupContracts'))
        assert result.wasSuccessful() and not result.skipped,'2FA setup contracts failed'
        assert snapshot()==seeded,'setup persisted model/audit state'
        assert cache_snapshot()==cache_before,'setup changed cache/session bytes'
        return result.testsRun
    try:
        from TESTS.adapters.django.two_factor_qr import run as qr_vectors
        qr_cases=qr_vectors(work,tool_env)
        subprocess.run(['go','test','-race','-tags=twofactorsetupintegration','./internal/service','-run','^TestTwoFactorSetup','-count=1'],cwd=ROOT/'BE_GO',check=True,timeout=90,
            env={**tool_env,'ASSOZETA_IDENTITY_CACHE':address,'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        assert snapshot()==seeded and cache_snapshot()==cache_before,'setup query integration changed fixture state'
        deadline=time.monotonic()+10
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.01)
        django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        from TESTS.adapters.go.server import build_server
        build_server(work/'two-factor-setup-server',log,tool_env)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));http_port=sock.getsockname()[1]
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only','DBHOST':str(work),'DBPORT':'5432',
            'REDIS_HOST':host,'REDIS_PORT':port,'REDIS_PASSWORD':'synthetic-identity-only','GO_LISTEN_ADDR':f'127.0.0.1:{http_port}',
            **{name:getattr(settings,name) for name in ('SECRET_KEY','JWT_SECRET_KEY','JWT_PUBLIC_KEY','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_STORAGE_BUCKET_NAME','AWS_S3_REGION_NAME','AWS_S3_ENDPOINT_URL','AWS_LOCATION','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')},'DEBUG':'False','RATELIMIT_ENABLE':'False',
            'EMAIL_HOST_SUPPORT':fixture['account_name'],'WHITELABEL_NAME':fixture['issuer']}

        base=f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None,'Go provisioning server stopped'
            try:return Client(base).request('GET','/readyz')[0]==200
            except OSError:return False
        variants=[
            ('default','setup-support@example.test','Assozeta Contract',False,32),
            ('missing support',None,'Bakney Sport',False,8),
            ('empty branding','','',False,8),
            ('unicode branding','+è / support@example.test','Club / Δ & !',False,8),
            ('maximum byte capacity','a'*(2953-len('otpauth://totp/?secret=')-32),'',False,2),
            ('excess byte capacity','a'*(2954-len('otpauth://totp/?secret=')-32),'',True,4),
        ]
        total=concurrent=0
        for name,support,issuer,failure,parallel in variants:
            fixture['account_name']='None' if support is None else support
            fixture['issuer']=issuer
            fixture['cases']=cases if name=='default' else [dict(case) for case in cases if case['name'] in ('owner','options')]
            if failure:fixture['cases'][0]['status']=500
            fixture['concurrent_count']=parallel
            fixture_path.write_text(json.dumps(fixture))
            with patch.object(reference_settings,'EMAIL_HOST_SUPPORT',support),patch.object(reference_settings,'WHITELABEL_NAME',issuer):
                count=contracts(django_base)
            selected={**environment,'WHITELABEL_NAME':issuer}
            if support is None:selected.pop('EMAIL_HOST_SUPPORT',None)
            else:selected['EMAIL_HOST_SUPPORT']=support
            api=subprocess.Popen([str(work/'two-factor-setup-server')],cwd=work,env=selected,stdout=log,stderr=subprocess.STDOUT)
            poll(ready,bool,timeout=30)
            assert contracts(base)==count
            if name=='default':
                from TESTS.contracts.test_login_http import otp
                from TESTS.contracts.qr import decode_svg_qr
                from PIL import Image
                for producer,consumer in [(django_base,base),(base,django_base),(django_base,django_base),(base,base)]:
                    with patch.object(reference_settings,'EMAIL_HOST_SUPPORT',support),patch.object(reference_settings,'WHITELABEL_NAME',issuer):
                        response=Client(producer,token(8)).request('GET','/two-fa/setup')
                    assert response[0]==200
                    material=response[1]['data'];decoded,preview=decode_svg_qr(material['qrcode_uri'])
                    label='django' if producer==django_base else 'go'
                    # Synthetic disposable-account QR; never a customer credential.
                    preview.resize((392,392),Image.Resampling.NEAREST).save(artifacts/f'{label}-qr.png')
                    before=User.original_objects.filter(pk=uid(8)).values('two_fa','two_fa_secret').get()
                    events=set(LogEntry.objects.values_list('pk',flat=True))
                    result=Client(consumer,token(8)).request('POST','/two-fa/update',{'enable':True,'secret':material['otp_secret'],'otp':otp(material['otp_secret'])})
                    assert result==(200,{'data':{'msg':'Two factor authentication enabled.'}}),'cross-runtime provisioning rejected'
                    owner=User.original_objects.get(pk=uid(8))
                    assert owner.two_fa and owner.two_fa_secret==material['otp_secret'],'provisioning secret not persisted'
                    for read_base in (django_base,base):assert Client(read_base,token(8)).request('GET','/two-fa/info')==(200,{'data':{'enabled':True}})
                    result=Client(consumer,token(8)).request('POST','/two-fa/update',{'enable':False,'secret':None,'otp':None})
                    assert result==(200,{'data':{'msg':'Disabled two factor authentication'}})
                    owner.refresh_from_db();assert not owner.two_fa and owner.two_fa_secret==material['otp_secret']
                    User.original_objects.filter(pk=uid(8)).update(**before)
                    LogEntry.objects.exclude(pk__in=events).delete()
                    assert snapshot()==seeded and cache_snapshot()==cache_before,'cross-runtime setup cleanup changed existing state'
                from TESTS.contracts.test_two_factor_setup import TwoFactorLoginFlowContracts
                for producer,consumer in [(django_base,base),(base,django_base),(django_base,django_base),(base,base)]:
                    before=User.original_objects.filter(pk=uid(81610)).values().get()
                    events=set(LogEntry.objects.values_list('pk',flat=True))
                    with override_settings(PASSWORD_HASHERS=PASSWORD_HASHERS),patch.dict(os.environ,TESTS_SETUP_PRODUCER_URL=producer,TESTS_SETUP_CONSUMER_URL=consumer,TESTS_TWO_FACTOR_SETUP_FIXTURE=str(fixture_path)),patch.object(reference_settings,'EMAIL_HOST_SUPPORT',support),patch.object(reference_settings,'WHITELABEL_NAME',issuer):
                        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(TwoFactorLoginFlowContracts))
                    assert result.wasSuccessful() and not result.skipped,'fresh provisioned login contract failed'
                    after=User.original_objects.filter(pk=uid(81610)).values().get()
                    assert after['two_fa'] is False and after['two_fa_secret']==TwoFactorLoginFlowContracts.result['secret'] and after['last_login'] is not None
                    assert {k:v for k,v in after.items() if k not in ('two_fa_secret','last_login')}=={k:v for k,v in before.items() if k not in ('two_fa_secret','last_login')},'login changed unrelated account fields'
                    entries=list(LogEntry.objects.exclude(pk__in=events).order_by('id'))
                    assert len(entries)==2 and all(str(entry.actor_id)==uid(81610) and entry.object_pk==uid(81610) for entry in entries),'provisioning/login audit attribution differs'
                    User.original_objects.filter(pk=uid(81610)).update(two_fa=before['two_fa'],two_fa_secret=before['two_fa_secret'],last_login=before['last_login'])
                    LogEntry.objects.exclude(pk__in=events).delete()
                    assert snapshot()==seeded and cache_snapshot()==cache_before,'fresh login cleanup changed existing state'
            api.terminate();api.wait(timeout=30);api=None
            total+=len(fixture['cases']);concurrent+=parallel
            print(f'Both backends: {name} provisioning/QR contracts passed',flush=True)
        return {'setup_scenarios_per_backend':total,'concurrent_setups_per_backend':concurrent,'branding_scenarios':len(variants),'decoded_qr_vectors':qr_cases,'cross_runtime_flows':4,'fresh_login_flows':4,'complete_two_factor_parity':False}
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        connections.close_all();raw.close();fixture_path.unlink(missing_ok=True)
        for session in sessions:cache.delete(session_key(session))
        with disable_auditlog():User.original_objects.filter(pk__in=created).delete()
        assert not thread.is_alive()
        assert snapshot()==original,'setup cleanup changed existing state'
