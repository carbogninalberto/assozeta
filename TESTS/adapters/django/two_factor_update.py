"""Public 2FA writes against actual Django ASGI and the Go root on private fixtures."""
import json
import os
import socket
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from .accounts import ROOT
from .scoped_resources import uid


def run(work,log,tool_env,address):
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections,connection
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User
    from application.models.audit_models import AuditLogIndex
    from application.impersonation import session_key
    from rest_framework_simplejwt.tokens import AccessToken
    from TESTS.http_client import Client,poll
    import redis
    import uvicorn
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot();events_before=set(LogEntry.objects.values_list('pk',flat=True))
    old_secret='GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    new_secret='JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP'
    created=[];cases=[];sessions=[]
    with disable_auditlog():
        for n,grants in ((81300,['other.settings.update']),(81301,['other.settings.read'])):
            User.original_objects.create(user_id=uid(n),username=f'TWO-FA-COLLABORATOR-{n}',email=f'two-fa-collaborator-{n}@example.test',role=3,connected_user_id=uid(8),collaborator_role=3,collaborator_permissions=grants,password='!synthetic-two-fa')
            created.append(uid(n))
    def token(identifier):
        value=AccessToken();value['user_id']=identifier;return str(value)
    def add(name,body,*,status=200,message=None,enabled=False,secret=old_secret,otp_secret=None,wrong_otp=False,initial=True,actor=None,target=None,headers=None,method='POST',query='',response=None):
        identifier=uid(81200+len(cases))
        with disable_auditlog():
            User.original_objects.create(user_id=identifier,username='TWO-FA-UPDATE-'+str(len(cases)),email=f'two-fa-update-{len(cases)}@example.test',role=2,password='!synthetic-two-fa',two_fa=initial,two_fa_secret=old_secret)
        created.append(identifier)
        selected=target or identifier
        cases.append({'name':name,'body':body,'status':status,'response':response if response is not None else {'data':{'msg':message or ('Two factor authentication enabled.' if enabled else 'Disabled two factor authentication')}},
            'token':token(actor or identifier),'actor':actor or identifier,'target':selected,'after':{'two_fa':enabled,'two_fa_secret':secret},'otp_secret':otp_secret,'wrong_otp':wrong_otp,'headers':headers or {},'method':method,'query':query})
    add('disable-keeps-secret',{'enable':False,'secret':'','otp':''})
    add('enable-rotates-secret',{'enable':True,'secret':new_secret,'otp':''},enabled=True,secret=new_secret,otp_secret=new_secret)
    add('same-value-no-audit',{'enable':True,'secret':old_secret,'otp':''},enabled=True,otp_secret=old_secret)
    add('already-disabled-no-audit',{'enable':False,'secret':'','otp':''},initial=False)
    add('bad-otp-no-write',{'enable':True,'secret':new_secret,'otp':''},status=400,message='Otp validation failed.',enabled=True,otp_secret=new_secret,wrong_otp=True)
    for value in (None,'',False,0,[],{}):
        add('false-enable-'+str(len(cases)),{'enable':value,'secret':'','otp':''})
    for value in ('false',1,[],{}):
        add('enable-truthiness-'+str(len(cases)),{'enable':value,'secret':new_secret,'otp':''},enabled=bool(value),secret=new_secret if value else old_secret,otp_secret=new_secret)
    add('null-secret-otp',{'enable':False,'secret':None,'otp':None})
    add('invalid-secret-short-otp-disable',{'enable':False,'secret':'!invalid','otp':'short'})
    add('invalid-secret-six-otp-before-disable',{'enable':False,'secret':'!invalid','otp':'123456'},status=500,enabled=True)
    add('invalid-secret-six-otp-enable',{'enable':True,'secret':'!invalid','otp':'123456'},status=500,enabled=True)
    add('numeric-otp-before-disable',{'enable':False,'secret':old_secret,'otp':123456},status=500,enabled=True)
    add('numeric-secret-before-disable',{'enable':False,'secret':123,'otp':'123456'},status=500,enabled=True)
    add('list-otp-disable',{'enable':False,'secret':old_secret,'otp':[1,2,3,4,5,6]})
    for field in ('enable','secret','otp'):
        body={'enable':False,'secret':'','otp':''};del body[field]
        add('missing-'+field,body,status=500,enabled=True)
    add('nonobject',[],status=500,enabled=True)
    add('malformed-json',None,status=400,enabled=True,response={'detail':'JSON parse error - Expecting property name enclosed in double quotes: line 1 column 2 (char 1)'})
    cases[-1]['raw_body']='{'
    add('disabled-account',{},actor=uid(3),status=401,enabled=True,response={'detail':'User is inactive','code':'user_inactive'})
    add('deleted-account',{},actor=uid(4),status=401,enabled=True,response={'detail':'User not found','code':'user_not_found'})
    add('restricted-collaborator',{},actor=uid(11),status=403,enabled=True,response={'detail':'Missing permission: other.settings.update'})
    add('admin-without-scope',{},actor=uid(1),status=403,enabled=True,response={'detail':'Seleziona un utente da impersonificare per accedere a questa sezione.'})
    add('anonymous',{},status=401,enabled=True,response={'detail':'Authentication credentials were not provided.'});cases[-1]['token']=None
    add('method-before-body',{},method='GET',status=405,enabled=True,response={'detail':'Method "GET" not allowed.'})
    add('head-no-body',{},method='HEAD',status=405,enabled=True,response='')
    add('options-metadata',{},method='OPTIONS',enabled=True,response={
        'name':'Two Fa Update','description':'API endpoint to update the two fa secret\n:param request: data: { OTP, username, password }\n:return:',
        'renders':['application/json'],'parses':['application/json','application/x-www-form-urlencoded','multipart/form-data']})
    add('form-disable',None)
    cases[-1].update(raw_body='enable=&secret=&otp=',content_type='application/x-www-form-urlencoded')
    add('multipart-disable',None)
    cases[-1].update(raw_body=''.join(f'--twofa-boundary\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n\r\n' for field in ('enable','secret','otp'))+'--twofa-boundary--\r\n',content_type='multipart/form-data; boundary=twofa-boundary')
    add('unsupported-content-type',None,status=415,enabled=True,response={'detail':'Unsupported media type "text/plain" in request.'})
    cases[-1].update(raw_body='enable=false',content_type='text/plain')
    add('session-audit-actor',{'enable':False,'secret':'','otp':''},headers={'Cookie':'sessionid=two-factor-update-active'})
    for name,actor in [('owner-update',8),('granted-collaborator-update',81300),('full-collaborator-update',14)]:
        add(name,{'enable':True,'secret':new_secret,'otp':''},actor=uid(actor),target=uid(8),enabled=True,secret=new_secret,otp_secret=new_secret)
    add('read-only-collaborator',{},actor=uid(81301),status=403,enabled=True,response={'detail':'Missing permission: other.settings.update'})
    for n,name,actor,target,association in [(81400,'admin-owner',1,9,None),(81401,'admin-collaborator',1,81300,None),(81402,'owner-collaborator',8,81300,80)]:
        session=uid(n);sessions.append(session)
        cache.set(session_key(session),{'actor_id':uid(actor),'target_id':uid(target),'association_id':uid(association) if association else None},3600)
        scope=8 if target==81300 else target
        add(name,{'enable':True,'secret':new_secret,'otp':''},actor=uid(actor),target=uid(scope),enabled=True,secret=new_secret,otp_secret=new_secret,headers={'X-Impersonation-Id':session})
        cases[-1]['additional_data']={'impersonation_session':session,'actor_id':uid(actor),'target_user_id':uid(target),'association_id':uid(association) if association else None}
    add('foreign-scoped-body',{'enable':False,'secret':'','otp':'','sport_association':uid(90)},actor=uid(8),headers={'X-Impersonation-Id':uid(81402)},status=403,enabled=True,response={'detail':'Risorsa non disponibile per questo utente e associazione.'})
    for failure in ('user-write','audit-write','index-write'):
        target=uid(8) if failure=='index-write' else None
        add('failed-'+failure,{'enable':False,'secret':'','otp':''},status=500,enabled=True,target=target)
        if failure=='index-write':
            # Use a fresh association owner so the invariant includes an index.
            from application.models import SportAssociation
            identifier=created[-1]
            User.original_objects.filter(pk=identifier).update(role=1)
            with disable_auditlog():
                association=SportAssociation.original_objects.create(sport_association_id=uid(81500),user_id=identifier,denomination='2FA atomic fixture')
            cases[-1]['target']=identifier
        cases[-1]['failure']=failure
    failed_user=next(case['target'] for case in cases if case.get('failure')=='user-write')
    with connection.cursor() as cursor:
        cursor.execute("CREATE FUNCTION two_factor_reject_user() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.user_id='"+failed_user+"'::uuid THEN RAISE EXCEPTION 'synthetic 2FA user failure' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
        cursor.execute('CREATE TRIGGER two_factor_reject_user BEFORE UPDATE OF two_fa ON bakney_user FOR EACH ROW EXECUTE FUNCTION two_factor_reject_user()')
        cursor.execute("CREATE FUNCTION two_factor_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.cid='failed-audit-write' THEN RAISE EXCEPTION 'synthetic 2FA audit failure' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
        cursor.execute('CREATE TRIGGER two_factor_reject_audit BEFORE INSERT ON auditlog_logentry FOR EACH ROW EXECUTE FUNCTION two_factor_reject_audit()')
        cursor.execute("CREATE FUNCTION two_factor_reject_index() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF (SELECT cid FROM auditlog_logentry WHERE id=NEW.log_entry_id)='failed-index-write' THEN RAISE EXCEPTION 'synthetic 2FA index failure' USING ERRCODE='23514'; END IF; RETURN NEW; END $$")
        cursor.execute('CREATE TRIGGER two_factor_reject_index BEFORE INSERT ON application_auditlogindex FOR EACH ROW EXECUTE FUNCTION two_factor_reject_index()')
    owner=User.original_objects.get(pk=uid(8))
    cache_key='django.contrib.sessions.cachetwo-factor-update-active'
    cache.set(cache_key,{'_auth_user_id':uid(8),'_auth_user_backend':'django.contrib.auth.backends.ModelBackend','_auth_user_hash':owner.get_session_auth_hash()},300)
    host,port=address.rsplit(':',1)
    raw=redis.Redis(host=host,port=int(port),db=1,password='synthetic-identity-only')
    session_bytes=raw.get(cache.make_key(cache_key))
    seeded=snapshot();path=work/'two-factor-update.json'
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start();api=None
    def concurrent_updates(base):
        # Hold the row until real HTTP mutations are blocked in PostgreSQL. This
        # proves authentication has read its earlier snapshot before another
        # transaction changes password/profile, without timing-based sleeps.
        from django.db import transaction
        identifier=cases[0]['target']
        before=User.original_objects.filter(pk=identifier).values().get()
        for parallel in (1,2):
            with ThreadPoolExecutor(max_workers=parallel) as pool:
                with transaction.atomic():
                    User.original_objects.select_for_update().get(pk=identifier)
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_backend_pid()')
                        blocker=cursor.fetchone()[0]
                    futures=[pool.submit(Client(base,token(identifier)).request,'POST','/two-fa/update',
                        {'enable':False,'secret':'','otp':''},headers={'X-Correlation-ID':f'concurrent-two-factor-{n}'}) for n in range(parallel)]
                    def blocked():
                        with connection.cursor() as cursor:
                            cursor.execute('SELECT pg_stat_clear_snapshot()')
                            cursor.execute('''WITH RECURSIVE waiting(pid) AS (
                                SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))
                                UNION
                                SELECT a.pid FROM pg_stat_activity a JOIN waiting w ON w.pid=ANY(pg_blocking_pids(a.pid))
                            ) SELECT count(*) FROM waiting''',[blocker])
                            return cursor.fetchone()[0]
                    poll(blocked,lambda count:count==parallel,timeout=10,interval=.01)
                    User.original_objects.filter(pk=identifier).update(password='!concurrent-password',first_name='Concurrent profile')
                for future in futures:
                    status,body=future.result(timeout=15)
                    assert (status,body)==(200,{'data':{'msg':'Disabled two factor authentication'}}),'concurrent 2FA response'
            current=User.original_objects.get(pk=identifier)
            assert current.password=='!concurrent-password' and current.first_name=='Concurrent profile','2FA overwrote concurrent credentials/profile'
            assert not current.two_fa and current.two_fa_secret==old_secret
            events=list(LogEntry.objects.exclude(pk__in=events_before))
            assert len(events)==1 and events[0].changes_dict=={'two_fa':['True','False']},'concurrent 2FA duplicated or lost audit'
            assert events[0].object_repr=='name: Concurrent profile ', 'audit used a stale profile'
            assert str(events[0].actor_id)==identifier and events[0].object_pk==identifier
            User.original_objects.filter(pk=identifier).update(password=before['password'],first_name=before['first_name'],two_fa=before['two_fa'])
            LogEntry.objects.exclude(pk__in=events_before).delete()
            assert snapshot()==seeded,'concurrent 2FA left undeclared writes'
        print(f'{base}: 2 locked-row 2FA concurrency scenarios passed',flush=True)
    def contracts(base):
        count=0
        for case in cases:
            user=User.original_objects.get(pk=case['target'])
            before={'two_fa':user.two_fa,'two_fa_secret':user.two_fa_secret}
            path.write_text(json.dumps({'case':case}));path.chmod(0o600)
            with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_TWO_FACTOR_UPDATE_FIXTURE=str(path)):
                result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_two_factor_update'))
            assert result.wasSuccessful() and not result.skipped,case['name']
            user.refresh_from_db()
            assert {'two_fa':user.two_fa,'two_fa_secret':user.two_fa_secret}==case['after'],(case['name'],'persisted two-factor state')
            changes={name:[str(before[name]),str(value)] for name,value in case['after'].items() if before[name]!=value}
            events=list(LogEntry.objects.exclude(pk__in=events_before))
            assert len(events)==bool(changes),(case['name'],'audit count')
            if changes:
                event=events[0]
                assert event.changes_dict==changes,(case['name'],'audit fields')
                assert event.object_pk==case['target'] and event.action==1
                assert event.additional_data==case.get('additional_data'),(case['name'],'impersonation audit')
                assert event.cid==case['name'] and event.remote_addr=='198.51.100.79'
                actor=case['actor']
                assert (str(event.actor_id) if event.actor_id else None)==actor,(case['name'],'audit actor')
                indexes=list(AuditLogIndex.objects.filter(log_entry=event))
                association=uid(80) if case['target']==uid(8) else uid(90) if case['target']==uid(9) else None
                assert len(indexes)==int(association is not None),(case['name'],'association index count')
                if indexes:assert str(indexes[0].sport_association_id)==association and indexes[0].resolution_path=='user.sport_association'
            if not case.get('failure'):User.original_objects.filter(pk=case['target']).update(**before)
            LogEntry.objects.exclude(pk__in=events_before).delete()
            assert snapshot()==seeded,(case['name'],'undeclared model writes')
            assert raw.get(cache.make_key(cache_key))==session_bytes,'session bytes changed'
            count+=result.testsRun
        print(f'{base}: {count} declared 2FA update HTTP/effect scenarios passed',flush=True)
        return count
    try:
        subprocess.run(['go','test','-race','-tags=twofactorupdateintegration','./internal/service','-run','TestTwoFactorUpdate','-count=1'],
            cwd=ROOT/'BE_GO',check=True,timeout=90,env={**tool_env,'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        assert snapshot()==seeded,'Go mutation integration changed fixture state'
        deadline=time.monotonic()+10
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.01)
        django_base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        count=contracts(django_base)
        concurrent_updates(django_base)
        from TESTS.adapters.go.server import build_server
        build_server(work/'two-factor-update-server',log,tool_env)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));http_port=sock.getsockname()[1]
        environment={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only','DBHOST':str(work),'DBPORT':'5432',
            'REDIS_HOST':host,'REDIS_PORT':port,'REDIS_PASSWORD':'synthetic-identity-only','GO_LISTEN_ADDR':f'127.0.0.1:{http_port}',
            **{name:getattr(settings,name) for name in ('SECRET_KEY','JWT_SECRET_KEY','JWT_PUBLIC_KEY','AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_STORAGE_BUCKET_NAME','AWS_S3_REGION_NAME','AWS_S3_ENDPOINT_URL','AWS_LOCATION','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')},'DEBUG':'False'}
        api=subprocess.Popen([str(work/'two-factor-update-server')],cwd=work,env=environment,stdout=log,stderr=subprocess.STDOUT)
        base=f'http://127.0.0.1:{http_port}'
        def ready():
            assert api.poll() is None,'Go two-factor update server stopped'
            try:return Client(base).request('GET','/readyz')[0]==200
            except OSError:return False
        poll(ready,bool,timeout=30)
        assert contracts(base)==count
        concurrent_updates(base)
        return {'two_factor_update_scenarios_per_backend':count,'two_factor_concurrency_scenarios_per_backend':2,'complete_two_factor_parity':False}
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        connections.close_all();cache.delete(cache_key);raw.close();path.unlink(missing_ok=True)
        with connection.cursor() as cursor:
            for name,table in [('user','bakney_user'),('audit','auditlog_logentry'),('index','application_auditlogindex')]:
                cursor.execute(f'DROP TRIGGER two_factor_reject_{name} ON {table}')
                cursor.execute(f'DROP FUNCTION two_factor_reject_{name}()')
        with disable_auditlog():SportAssociation.original_objects.filter(pk=uid(81500)).delete()
        for session in sessions:cache.delete(session_key(session))
        LogEntry.objects.exclude(pk__in=events_before).delete()
        with disable_auditlog():User.original_objects.filter(pk__in=created).delete()
        assert not thread.is_alive()
        assert snapshot()==original,'two-factor update cleanup changed existing state'
