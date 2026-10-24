"""Run the same portable refresh contracts over actual Django and Go HTTP.

Called only inside accounts.py's private PostgreSQL/Redis/key fixture context.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[3]


def run(work, log, tool_env, cache_address):
    cors_output=ROOT/'quality-reports/go-migration/cors';cors_output.mkdir(parents=True,exist_ok=True)
    (cors_output/'public-results.json').write_text(json.dumps({'success':False,'phase':'started'})+'\n')
    security_output=ROOT/'quality-reports/go-migration/security-headers';security_output.mkdir(parents=True,exist_ok=True)
    (security_output/'public-results.json').write_text(json.dumps({'success':False,'phase':'started'})+'\n')
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    import redis
    import uvicorn
    from application.models import User
    from application.impersonation import begin
    from application.services.jwt_token_service import JWTTokenService
    from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
    from TESTS.adapters.django.scoped_resources import uid
    from TESTS.http_client import Client, poll
    from django.apps import apps
    from django.test import override_settings
    def token(n, kind):
        value = RefreshToken() if kind == 'refresh' else AccessToken()
        value['user_id'] = uid(n)
        return str(value)
    target = User.objects.get(pk=uid(30))
    pair = JWTTokenService.generate_tokens_for_user(target)
    refresh = RefreshToken(pair['refresh_token']); refresh['custom'] = {'contract':'preserved'}
    refresh['iat']=int(time.time())-600
    expired = RefreshToken(); expired['exp'] = 1
    malformed = AccessToken(); malformed['user_id'] = 'invalid'
    session, _ = begin(User.objects.get(pk=uid(8)), target.pk)
    fixture = {'refresh':str(refresh), 'access':pair['access_token'], 'expired':str(expired),
        'target':uid(30), 'owner_access':token(8,'access'), 'owner_session':session,
        'inactive_access':token(3,'access'), 'inactive_refresh':token(3,'refresh'),
        'deleted_refresh':token(4,'refresh'), 'missing_refresh':token(999,'refresh')}
    fixture['malformed_access'] = str(malformed)
    expired_access=AccessToken();expired_access['exp']=1
    fixture['expired_access']=str(expired_access)
    expired_access['iat']=int(time.time())+3600
    fixture['future_expired_access']=str(expired_access)
    pieces=fixture['expired_access'].split('.')
    pieces[2]=('A' if pieces[2][0]!='A' else 'B')+pieces[2][1:]
    fixture['foreign_expired_access']='.'.join(pieces)
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from TESTS.adapters.django.jwt_document import append_cases
    fixture['jwt_documents']=[]
    append_cases(fixture['jwt_documents'],dict(AccessToken(fixture['owner_access']).payload),
                 load_pem_private_key(settings.JWT_SECRET_KEY.encode(),password=None),int(time.time()))
    redis_host,redis_port=cache_address.rsplit(':',1)
    raw_cache=redis.Redis(host=redis_host,port=int(redis_port),db=1,password='synthetic-identity-only')
    def session_data(n=1,backend='django.contrib.auth.backends.ModelBackend'):
        user=User.original_objects.get(pk=uid(n))
        return {'_auth_user_id':uid(n),'_auth_user_backend':backend,'_auth_user_hash':user.get_session_auth_hash()}
    session_values={'active':session_data(),'inactive':session_data(3),'deleted':session_data(4),
                    'social-inactive':session_data(3,'social_core.backends.google.GoogleOAuth2'),
                    'wrong':{**session_data(),'_auth_user_hash':'wrong'},'malformed':[],
                    'invalid-id':{**session_data(),'_auth_user_id':'invalid'},
                    'invalid-hash':{**session_data(),'_auth_user_hash':10**4300}}
    fixture['sessions']={name:'refresh-session-'+name for name in [*session_values,'missing']}
    session_payloads={}
    for name,value in session_values.items():
        logical='django.contrib.sessions.cache'+fixture['sessions'][name]
        cache.set(logical,value,300)
        key=cache.make_key(logical)
        session_payloads[key]=raw_cache.get(key)
    def reset_sessions():
        for key,value in session_payloads.items():raw_cache.set(key,value,ex=300)
    def session_readback():
        for key,value in session_payloads.items():
            current=raw_cache.get(key)
            if key.endswith('refresh-session-wrong'):assert current is None,'invalid hash was not revoked'
            else:assert current==value,'refresh changed session cache data'
    path = work / 'refresh-http.json'; path.write_text(json.dumps(fixture)); path.chmod(0o600)
    def snapshot():
        return {model._meta.label:list(model._base_manager.order_by('pk').values())
                for model in apps.get_models() if model._meta.managed and not model._meta.proxy}
    before = snapshot()
    # Released HTTP runs core.asgi through Uvicorn, including its actual global middleware.
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    def contracts(base):
        os.environ.update(TESTS_BASE_URL=base, TESTS_REFRESH_FIXTURE=str(path))
        suite=unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_refresh')
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        if not result.wasSuccessful() or result.skipped:
            raise AssertionError('Portable refresh contracts did not all pass')
        return result.testsRun
    def cors_contracts(base):
        os.environ['TESTS_BASE_URL']=base
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_cors_public'))
        assert result.wasSuccessful() and not result.skipped and result.testsRun==4,'public CORS contracts failed'
        return result.testsRun
    def security_contracts(base):
        os.environ['TESTS_BASE_URL']=base
        result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_security_headers_public'))
        assert result.wasSuccessful() and not result.skipped and result.testsRun==5,'public security contracts failed'
        return result.testsRun
    api = None
    try:
        deadline=time.monotonic()+5
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline,'private refresh ASGI server failed to start'
            time.sleep(0.01)
        django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        count=contracts(django_base)
        with override_settings(CORS_ORIGIN_ALLOW_ALL=False,CORS_ALLOWED_ORIGINS=['https://allowed.example.test']):
            cors_count=cors_contracts(django_base)
            security_count=security_contracts(django_base)
        session_readback();reset_sessions()
        from TESTS.adapters.go.server import build_server
        build_server(work/'refresh-server',log,tool_env)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1',0)); port=listener.getsockname()[1]
        redis_host,redis_port=cache_address.rsplit(':',1)
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts',
            'DBPASSWORD':'test-only','DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':redis_host,'REDIS_PORT':redis_port,
            'REDIS_USERNAME':'default','REDIS_PASSWORD':'synthetic-identity-only',**{name: getattr(settings, name) for name in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_STORAGE_BUCKET_NAME', 'AWS_S3_REGION_NAME', 'AWS_S3_ENDPOINT_URL', 'AWS_LOCATION')},
            'GO_LISTEN_ADDR':f'127.0.0.1:{port}',
            'SECRET_KEY':settings.SECRET_KEY,'JWT_SECRET_KEY':settings.JWT_SECRET_KEY,'JWT_PUBLIC_KEY':settings.JWT_PUBLIC_KEY,
            'DEBUG':'False','CORS_ALLOWED_ORIGINS':'https://allowed.example.test','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY,
            'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET}
        api=subprocess.Popen([str(work/'refresh-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{port}'
        def ready():
            if api.poll() is not None: raise AssertionError('Go HTTP process stopped')
            try: return Client(base).request('GET','/readyz')[0] == 200
            except OSError: return False
        poll(ready,bool,timeout=30)
        assert contracts(base)==count
        assert cors_contracts(base)==cors_count
        assert security_contracts(base)==security_count
        session_readback()
        # Verify signatures using Django in addition to portable payload checks.
        # No token is written to reports or logs.
        status, refreshed = Client(base).request('POST','/oauth2/refresh-token',{'refresh_token':fixture['refresh']})
        assert status == 200
        assert AccessToken(refreshed['access_token'])['user_id'] == uid(30)
        status, unbound = Client(base).request('POST','/oauth2/refresh-token',{'refresh_token':None})
        assert status == 200 and 'user_id' not in RefreshToken(unbound['refresh_token'])
        status, accepted = Client(django_base).request('POST','/oauth2/refresh-token',{'refresh_token':unbound['refresh_token']})
        assert status == 200 and accepted['refresh_token'] == unbound['refresh_token']
        assert snapshot() == before, 'refresh changed persisted business/auth/audit/job state'
        (cors_output/'public-results.json').write_text(json.dumps({'success':True,'contracts_per_backend':cors_count,
            'composition':'actual Django ASGI and compiled Go root','models_unchanged':True,'session_readback':True})+'\n')
        (security_output/'public-results.json').write_text(json.dumps({'success':True,'contracts_per_backend':security_count,
            'composition':'actual Django ASGI and compiled Go root','models_unchanged':True,'session_readback':True})+'\n')
        return count
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try: api.wait(timeout=30)
            except subprocess.TimeoutExpired: api.kill(); api.wait(timeout=5)
        for key in session_payloads:raw_cache.delete(key)
        raw_cache.close()
        path.unlink(missing_ok=True)
        assert not thread.is_alive(),'private refresh ASGI server did not drain'
