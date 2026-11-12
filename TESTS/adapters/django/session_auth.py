"""Django cache-session authentication on shared private PostgreSQL and Redis.

Expected actors and mutations are declared before invoking actual middleware.
This is session identity/invalid-hash revocation, not complete HTTP session policy.
"""
import base64
import json
import pickle
import subprocess
import uuid
from .accounts import ROOT
from .scoped_resources import uid


def run(work,log,tool_env,address):
    from django.apps import apps
    from django.contrib.auth.middleware import AuthenticationMiddleware
    from django.core.cache import cache
    from django.http import HttpResponse
    from django.test import RequestFactory,override_settings
    from django.db import connection
    from django.test.utils import CaptureQueriesContext
    from application.models import User
    from auditlog.middleware import AuditlogMiddleware
    from core.middleware import ConditionalSessionMiddleware
    import redis
    host,port=address.rsplit(':',1)
    raw=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    assert User._default_manager.name=='original_objects','session manager scope changed'
    cases=[];payloads={}
    standard='django.contrib.auth.backends.ModelBackend'
    secret='synthetic-session-auth-only'
    with override_settings(SECRET_KEY=secret,SECRET_KEY_FALLBACKS=[]):
        users={n:User.original_objects.get(pk=uid(n)) for n in (1,2,3,4)}
        def data(n=1,backend=standard):return {'_auth_user_id':uid(n),'_auth_user_backend':backend,'_auth_user_hash':users[n].get_session_auth_hash()}
        def add(name,payload=None,*,present=True,key=None,actor=None,empty=False,error=False,delete=False,queries=0,corrupt=False,encoded=None):
            key=key if key is not None else f'session-auth-fixture-{len(cases):03d}'
            logical='django.contrib.sessions.cache'+key
            if present:
                if encoded is not None:raw.set(cache.make_key(logical),encoded,ex=300)
                elif corrupt:raw.set(cache.make_key(logical),b'\x80\x05?',ex=300)
                else:cache.set(logical,payload,300)
            serialized=raw.get(cache.make_key(logical))
            case=dict(name=name,key=key,cache_key=cache.make_key(logical),actor_id=uid(actor) if actor else '',
                      actor_email=users[actor].email if actor else '',empty=empty,error=error,delete=delete,queries=queries,
                      before=base64.b64encode(serialized).decode() if serialized is not None else None)
            cases.append(case);payloads[name]=serialized
        add('no cookie',present=False,key='',empty=True)
        add('short cookie',present=False,key='short',empty=True)
        add('missing cache',present=False,empty=True)
        add('empty cached dictionary',{})
        add('OAuth state without authentication',{'state':'synthetic-oauth-state'})
        add('active standard backend',data(),actor=1,queries=1)
        add('inactive standard backend',data(3),queries=1)
        add('deleted standard backend',data(4),actor=4,queries=1)
        for backend in ('google.GoogleOAuth2','apple.AppleIdAuth','orcid.ORCIDOAuth2'):
            payload=data(3,'social_core.backends.'+backend)
            add('inactive social '+backend,payload,queries=1)
            add('active social '+backend,data(1,'social_core.backends.'+backend),actor=1,queries=1)
            add('deleted social '+backend,data(4,'social_core.backends.'+backend),actor=4,queries=1)
            # Rejection precedes hash conversion/revocation; preserve the cache.
            add('inactive social wrong hash '+backend,{**payload,'_auth_user_hash':'wrong'},queries=1)
            add('inactive social oversized hash '+backend,{**payload,'_auth_user_hash':[10**4300]},queries=1)
        add('unknown backend',{**data(),'_auth_user_backend':'unconfigured.backend'})
        add('missing user id',{'_auth_user_backend':standard})
        add('missing backend',{'_auth_user_id':uid(1)})
        add('missing account',{**data(),'_auth_user_id':uid(999999)},queries=1)
        add('missing hash',{k:v for k,v in data().items() if k!='_auth_user_hash'},empty=True,delete=True,queries=1)
        add('wrong hash',{**data(),'_auth_user_hash':'synthetic-wrong-hash'},empty=True,delete=True,queries=1)
        add('empty hash',{**data(),'_auth_user_hash':''},empty=True,delete=True,queries=1)
        add('non-object cached data',[],error=True)
        add('malformed user id',{**data(),'_auth_user_id':'not-a-uuid'},error=True)
        add('null user id',{**data(),'_auth_user_id':None},queries=0)
        add('integer UUID',{**data(),'_auth_user_id':uuid.UUID(uid(1)).int},actor=1,queries=1)
        add('boolean UUID',{**data(),'_auth_user_id':True},queries=1)
        add('null backend',{**data(),'_auth_user_backend':None})
        add('byte-string hash',{**data(),'_auth_user_hash':data()['_auth_user_hash'].encode()},actor=1,queries=1)
        add('nested OAuth extras',{**data(),'state':{'values':[1,2.5,True,None,'è'], 'same':'repeated'},'extra':'repeated'},actor=1,queries=1)
        add('corrupt cache pickle',corrupt=True,empty=True)
        add('cached None',None,empty=True)
        add('short Unicode cookie',present=False,key='è'*7,empty=True)
        add('Unicode cookie',data(),key='è'*8,actor=1,queries=1)
        add('numeric backend',{**data(),'_auth_user_backend':1})
        add('cached false scalar',False,error=True)
        add('cached integer scalar',0,error=True)
        add('cached string scalar','scalar',error=True)
        for protocol in (4,5):add('pickle protocol '+str(protocol),encoded=pickle.dumps(data(),protocol=protocol),actor=1,queries=1)
        for name,value in [('integer',10**4300),('list',[10**4300]),('dictionary',{'hash':10**4300}),('tuple',(10**4300,))]:
            add('oversized integer hash in '+name,{**data(),'_auth_user_hash':value},error=True,queries=1)
        for name,value in [('finite float',1.5),('zero integer',0)]:
            add(name+' hash',{**data(),'_auth_user_hash':value},empty=True,delete=True,queries=1)
        assert len(cases)==54
        factory=RequestFactory()
        def observe(case):
            request=factory.get('/oauth2/login',HTTP_COOKIE='sessionid='+case['key'])
            # Assign the cookie mapping directly for Unicode cache-key cases;
            # cookie wire decoding is deliberately outside this component oracle.
            request.COOKIES={'sessionid':case['key']} if case['key'] else {}
            captured={}
            def terminal(req):
                actor=AuditlogMiddleware._get_actor(req)
                captured['actor_id']=str(actor.pk) if actor else ''
                captured['actor_email']=actor.email if actor else ''
                return HttpResponse('fixture')
            chain=ConditionalSessionMiddleware(AuthenticationMiddleware(AuditlogMiddleware(terminal)))
            failed=False
            with CaptureQueriesContext(connection) as sql:
                try:chain(request)
                except Exception:failed=True
            assert failed==case['error'],(case['name'],'Django error differs')
            if not failed:
                assert captured=={k:case[k] for k in ('actor_id','actor_email')},(case['name'],'Django actor differs')
                assert request.session.is_empty()==case['empty'],(case['name'],'Django empty state differs')
            assert len(sql)<=1,(case['name'],'unbounded session lookup')
            current=raw.get(case['cache_key'])
            expected=None if case['delete'] else payloads[case['name']]
            assert current==expected,(case['name'],'Django cache mutation differs')
        for case in cases:
            observe(case)
            if payloads[case['name']] is not None:raw.set(case['cache_key'],payloads[case['name']],ex=300)
        assert snapshot()==before,'Django session reads changed persisted tables'
        # Re-read current database state on the same service/session key. These
        # updates are fixture-only and restored before the full-table comparison.
        transition_keys=[]
        for index,backend in enumerate((standard,'social_core.backends.google.GoogleOAuth2','social_core.backends.apple.AppleIdAuth','social_core.backends.orcid.ORCIDOAuth2')):
            transition_key='session-auth-transition-'+str(index)
            transition_cache=cache.make_key('django.contrib.sessions.cache'+transition_key)
            transition={**cases[5],'name':'current account transition','key':transition_key,'cache_key':transition_cache}
            payloads[transition['name']]=pickle.dumps(data(1,backend),protocol=5)
            raw.set(transition_cache,payloads[transition['name']],ex=300)
            original={field:getattr(users[1],field) for field in ('email','password','is_active','deleted')}
            try:
                observe(transition)
                User.original_objects.filter(pk=uid(1)).update(email='session-changed@example.test')
                transition['actor_email']='session-changed@example.test';observe(transition)
                User.original_objects.filter(pk=uid(1)).update(is_active=False)
                transition.update(actor_id='',actor_email='');observe(transition)
                User.original_objects.filter(pk=uid(1)).update(is_active=True,deleted=True)
                transition.update(actor_id=uid(1),actor_email='session-changed@example.test');observe(transition)
                User.original_objects.filter(pk=uid(1)).update(password='!synthetic-session-password-change')
                transition.update(actor_id='',actor_email='',empty=True,delete=True);observe(transition)
            finally:
                User.original_objects.filter(pk=uid(1)).update(**original)
            assert raw.get(transition_cache) is None,'Django password change did not revoke session'
            assert snapshot()==before,'Django transition reset changed persisted tables'
            raw.set(transition_cache,payloads[transition['name']],ex=300)
            transition_keys.append(transition_key)
        print(f'Django: {len(cases)} declared session actor/revocation cases passed',flush=True)
        print('Django: 20 current-account session transitions across four backends passed',flush=True)
        path=work/'session-auth.json'
        path.write_text(json.dumps(dict(secret=secret,address=address,cases=cases,transition_keys=transition_keys)));path.chmod(0o600)
        subprocess.run(['go','test','-race','-tags=sessionauthintegration','./internal/service','-run','^TestDjangoSessionAuthentication$'],cwd=ROOT/'BE_GO',check=True,timeout=120,
            env={**tool_env,'ASSOZETA_SESSION_AUTH_FIXTURE':str(path),'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        for case in cases:
            expected=None if case['delete'] else payloads[case['name']]
            assert raw.get(case['cache_key'])==expected,(case['name'],'Go revocation/readback differs')
            raw.delete(case['cache_key'])
        for key in transition_keys:
            assert raw.get(cache.make_key('django.contrib.sessions.cache'+key)) is None,'Go password revocation unreadable from Django cache'
        assert snapshot()==before,'Go session reads changed persisted tables'
        path.unlink()
    raw.close()
    return len(cases)
