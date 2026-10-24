"""Unchanged impersonation UI on explicit versioned backends and real dependencies.

Current-schema browser acceptance only. Django serves the listed unimplemented
read/WebSocket dependencies; unknown API routes fail. No UI, authentication or
business response is mocked and no installed backend-selection claim is made.
"""
import argparse
import base64
from contextlib import contextmanager, ExitStack
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import socketserver
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from uuid import UUID, uuid4
from unittest.mock import patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'BE'), str(ROOT)]
from TESTS.adapters.django.production_runtime import require_production_python
from TESTS.adapters.django.profile_browser import prepare_ui, fingerprints
from TESTS.adapters.go.payment_category_image import ImageFixture, DATABASE
from TESTS.adapters.go.age_worker_fixture import initialize_current_schema
from TESTS.adapters.go.impersonation_management_image import go_source_hashes
from TESTS.adapters.caddy import proxy
from TESTS.http_client import Client, poll

SELECTED = ('instance/status', 'instance/config', 'instance/access', 'oauth2/login', 'oauth2/refresh-token',
    'profile/info', 'profile/settings/tables', 'association/impersonation/users', 'association/impersonation',
    'administration/impersonation/users', 'administration/impersonation')
COEXISTENCE = ('instance/logo.png', 'instance/manifest.json', 'association/export/active', 'statistic/athlete-dashboard', 'statistic/dashboard', 'communications/staff-board/list',
    'search/profile/BROWSER-OWNER', 'profile/associates/sport-association/'+str(UUID(int=511001)))
WORKFLOW_SOURCES = ('src/App.svelte', 'src/routes.js', 'src/store/stores.js', 'src/store/instanceStore.js',
    'src/utils/ApiMiddleware.js', 'src/utils/impersonation.js', 'src/utils/loginSession.js', 'src/utils/userContext.js',
    'src/utils/Permissions.js', 'src/utils/NotificationWebSocket.js', 'src/utils/HealthWebSocket.js',
    'src/components/ImpersonationPanel.svelte', 'src/components/Sidebar.svelte', 'src/components/Header.svelte',
    'src/routes/tools/SportAssociationsManager.svelte', 'src/routes/tools/ImpersonationActions.svelte',
    'src/components/buttons/UserSwitch.svelte', 'src/routes/login/Login.svelte',
    'src/routes/dashboard/Dashboard.svelte', 'src/routes/dashboard/EmptyDasbhoard.svelte',
    'src/routes/userdashboard/Dashboard.svelte', 'src/routes/search/profile/Profile.svelte',
    'src/components/tables/BKNDatatable.svelte')
SOURCES = ('TESTS/adapters/django/impersonation_browser.py', 'TESTS/browser/impersonation.mjs',
    'TESTS/adapters/django/profile_browser.py', 'TESTS/adapters/go/payment_category_image.py',
    'TESTS/adapters/go/age_worker_fixture.py', 'TESTS/adapters/go/impersonation_management_image.py',
    'TESTS/adapters/django/settings.py', 'TESTS/adapters/caddy.py', 'selfhost/caddy/Caddyfile')

# Independently authored required scenario sequence; a successful prefix is not
# acceptance. The browser is the actor, while Python checks durable outcomes.
CHECKPOINTS = tuple(('owner', action) for action in
    ('login', 'start', 'reload', 'change', 'stop', 'start', 'expire', 'recovered')) + tuple(
    ('admin', action) for action in
    ('login', 'start', 'change', 'reload', 'change', 'stop', 'start', 'expire', 'recovered'))

def capture_browser_environment():
    return {key:value for key,value in os.environ.items()
            if key in ('PATH', 'HOME', 'TMPDIR', 'PLAYWRIGHT_BROWSERS_PATH')}

def write_private_json(path, value):
    pending=path.with_name(path.name+'.pending')
    descriptor=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(descriptor,'w') as output:json.dump(value,output)
    os.replace(pending,path)

def wait_gateway_ready(origin, timeout=30, interval=.1):
    # Docker can publish its socket before Caddy accepts HTTP. Only this private
    # startup probe retries transport failures; business requests still fail.
    def ready():
        try:
            return Client(origin,timeout=min(1,timeout)).request('GET','/fixture-health')[0]
        except (ConnectionError,TimeoutError,URLError):
            return None
    return poll(ready,lambda value:value==200,timeout=timeout,interval=interval)

def assert_browser_acceptance(browser, checkpoints):
    assert browser['success'] and browser['browser_created'] and browser['contexts_closed'] and browser['browser_closed']
    assert not browser['failures'] and browser['browser_cache_matches_invocation']
    assert tuple((row['actor'],row['action']) for row in checkpoints)==CHECKPOINTS
    observed=[row for row in browser['checks'] if 'checkpoint' in row]
    assert [(row['actor'],row['action'],row['checkpoint']) for row in observed]==[
        (actor,action,index) for index,(actor,action) in enumerate(CHECKPOINTS,1)]
    for actor,count in (('owner',3),('admin',4)):
        assert len([row for row in browser['checks'] if row['actor']==actor and row['action']=='picker-exact-eligible-ids'])==count
    assert [(row['actor'],row['action']) for row in browser['checks'] if row['action']=='foreign-athlete-excluded']==[('owner','foreign-athlete-excluded')]
    credentials=[row for row in browser['checks'] if row['action']=='credentials-unchanged']
    assert [row['actor'] for row in credentials]==['owner','admin']
    assert all(re.fullmatch('[0-9a-f]{64}',row[key]) for row in credentials
               for key in ('access_token_sha256','refresh_token_sha256'))

class ImpersonationBrowserContractTests(unittest.TestCase):
    def test_gateway_never_treats_unavailable_http_as_ready(self):
        class Unavailable(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.recv(4096)
                self.request.sendall(b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
        with socketserver.TCPServer(('127.0.0.1',0),Unavailable) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                started=time.monotonic()
                with self.assertRaisesRegex(AssertionError,'Timed out waiting'):
                    wait_gateway_ready('http://127.0.0.1:'+str(server.server_address[1]),timeout=.1,interval=.01)
                self.assertLess(time.monotonic()-started,2)
            finally:
                server.shutdown();thread.join(timeout=2)
                self.assertFalse(thread.is_alive())

    def test_gateway_waits_through_actual_connection_reset_then_requires_http_200(self):
        requests=[]
        class Startup(socketserver.BaseRequestHandler):
            def handle(self):
                requests.append(self.request.recv(4096))
                if len(requests)==1:
                    self.request.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0))
                    return
                status='503 Service Unavailable' if len(requests)==2 else '200 OK'
                self.request.sendall(('HTTP/1.1 '+status+'\r\nContent-Length: 0\r\nConnection: close\r\n\r\n').encode())
        with socketserver.TCPServer(('127.0.0.1',0),Startup) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                wait_gateway_ready('http://127.0.0.1:'+str(server.server_address[1]),timeout=2,interval=.01)
                self.assertEqual(len(requests),3)
                self.assertTrue(all(request.startswith(b'GET /fixture-health HTTP/1.1') for request in requests))
            finally:
                server.shutdown();thread.join(timeout=2)
                self.assertFalse(thread.is_alive())

    def test_checkpoint_ack_is_private_and_atomically_published(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'1.ack.json'
            write_private_json(path,{'success':True})
            self.assertEqual(json.loads(path.read_text()),{'success':True})
            self.assertEqual(path.stat().st_mode & 0o777,0o600)
            self.assertEqual(list(Path(directory).iterdir()),[path])
    def test_browser_cache_survives_isolated_environment_reset(self):
        with patch.dict(os.environ, {'PATH':'/private/bin','HOME':'/private/home',
                'PLAYWRIGHT_BROWSERS_PATH':'/private/nondefault-browser-cache','PRIVATE_SECRET':'omit'},clear=True):
            captured=capture_browser_environment()
            os.environ.clear()
            self.assertEqual(captured,{'PATH':'/private/bin','HOME':'/private/home',
                'PLAYWRIGHT_BROWSERS_PATH':'/private/nondefault-browser-cache'})
    def test_complete_sequence_and_reduced_workload_controls(self):
        checkpoints=[{'actor':actor,'action':action} for actor,action in CHECKPOINTS]
        browser={'success':True,'browser_created':True,'contexts_closed':True,'browser_closed':True,'failures':[],'browser_cache_matches_invocation':True,
            'checks':[{'actor':actor,'action':action,'checkpoint':index}
                      for index,(actor,action) in enumerate(CHECKPOINTS,1)]}
        for actor,count in (('owner',3),('admin',4)):
            browser['checks'] += [{'actor':actor,'action':'picker-exact-eligible-ids'} for _ in range(count)]
        browser['checks'] += [{'actor':'owner','action':'foreign-athlete-excluded'}]
        browser['checks'] += [{'actor':actor,'action':'credentials-unchanged',
            'access_token_sha256':'a'*64,'refresh_token_sha256':'b'*64} for actor in ('owner','admin')]
        assert_browser_acceptance(browser,checkpoints)
        with self.assertRaises(AssertionError):assert_browser_acceptance(browser,checkpoints[:-1])
        for action in ('picker-exact-eligible-ids','foreign-athlete-excluded','credentials-unchanged'):
            altered=copy.deepcopy(browser)
            altered['checks'].pop(next(i for i,row in enumerate(altered['checks']) if row['action']==action))
            with self.subTest(action=action),self.assertRaises(AssertionError):assert_browser_acceptance(altered,checkpoints)
        altered=copy.deepcopy(browser);altered['browser_closed']=False
        with self.assertRaises(AssertionError):assert_browser_acceptance(altered,checkpoints)

def source_hashes():
    paths = list(SOURCES) + ['UI/'+p for p in WORKFLOW_SOURCES] + ['BE/' + str(p.relative_to(ROOT/'BE')) for p in (ROOT/'BE').rglob('*.py')]
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sorted(paths)}

class Observer:
    def __init__(self, application):
        self.application, self.rows, self.lock = application, [], threading.Lock()
    def clear(self):
        with self.lock: self.rows.clear()
    def snapshot(self):
        with self.lock: return copy.deepcopy(self.rows)
    async def __call__(self, scope, receive, send):
        row = None
        if scope['type'] in ('http', 'websocket'):
            path = scope['path'].lstrip('/')
            row = {'path':scope['path'], 'type':scope['type'], 'method':scope.get('method'), 'status':None,
                   'classification':'selected' if path in SELECTED else 'coexistence' if path in COEXISTENCE or path.startswith('ws/') else 'unknown'}
            with self.lock: self.rows.append(row)
        async def observe(message):
            if row is not None and message['type'] == 'http.response.start': row['status'] = message['status']
            await send(message)
        await self.application(scope, receive, observe)
    def assert_selected(self, version):
        selected = [row for row in self.snapshot() if row['classification']=='selected']
        if version=='v2': assert not selected, 'upstream_mismatch'
        else: assert selected, 'Django selected traffic absent'
        assert not [row for row in self.snapshot() if row['classification']=='unknown'], 'Unlisted Django dependency'
        return selected

class BrowserFixture(ImageFixture):
    def run_container(self, suffix, image, *args):
        if suffix=='server': args=(*args,'--no-healthcheck')
        return super().run_container(suffix,image,*args)

@contextmanager
def gateway(proof, work, ui, upstream, django_port, version):
    image = re.search(r'^FROM (caddy:\S+) AS production$', (ROOT/'UI/Dockerfile').read_text(),re.M).group(1)
    config = '''{\n admin off\n}\n:80 {\n root * /srv\n route {\n handle /fixture-health {\n respond 200\n }\n @selected path SELECTED\n handle @selected {\n header X-Fixture-Backend LABEL\n uri replace /api/ /api/VERSION/ 1\n reverse_proxy UPSTREAM\n }\n @coexistence path COEXISTENCE\n handle @coexistence {\n header X-Fixture-Backend django-coexistence\n uri strip_prefix /api\n reverse_proxy host.docker.internal:DJANGO\n }\n handle /ws/* {\n reverse_proxy host.docker.internal:DJANGO\n }\n @unknown path /api/* /instance-*\n handle @unknown {\n header X-Fixture-Backend unsupported-fixture-route\n respond 502\n }\n handle {\n header Cache-Control no-cache\n try_files {path} /index.html\n file_server\n }\n }\n}\n'''.replace('SELECTED',' '.join('/api/'+p for p in SELECTED)).replace('COEXISTENCE',' '.join('/api/'+p for p in COEXISTENCE)).replace('LABEL','django-v1' if version=='v1' else 'go-v2').replace('VERSION',version).replace('UPSTREAM',upstream).replace('DJANGO',str(django_port))
    configuration=work/('gateway-'+uuid4().hex+'.Caddyfile');configuration.write_text(config)
    name=proof.run_container('browser-'+uuid4().hex[:8],proof.image(image)['Id'],'--publish','127.0.0.1::80',
        '--add-host','host.docker.internal:host-gateway','--mount',f'type=bind,source={configuration},target=/etc/caddy/Caddyfile,readonly',
        '--mount',f'type=bind,source={ui},target=/srv,readonly')
    try:
        origin='http://127.0.0.1:'+str(proof.published_port(name,80))
        wait_gateway_ready(origin)
        proof.docker('exec',name,'caddy','validate','--config','/etc/caddy/Caddyfile')
        yield origin,{'configuration_sha256':hashlib.sha256(config.encode()).hexdigest(),'selected_routes':SELECTED,
                      'coexistence_routes':COEXISTENCE,'websocket_coexistence':'/ws/*','private_alias_rewrite':True}
    finally:
        proof.docker('rm','--force',name)
        assert proof.docker('inspect',name,check=False).returncode != 0


def exercise(proof,work,log,args,report,tool_env):
    import redis
    import uvicorn
    from django.apps import apps
    from django.conf import settings
    from django.conf.global_settings import PASSWORD_HASHERS
    from django.contrib.auth.hashers import make_password
    from django.contrib.contenttypes.models import ContentType
    from django.core.cache import cache
    from django.db import connections
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from auditlog.models import LogEntry
    from application.models import User, SportAssociation, SportAssociationMembershipCardConfiguration, Associate
    from application.models.audit_models import AuditLogIndex
    from application.impersonation import session_key
    from instance.models import InstanceConfiguration
    from rest_framework_simplejwt.tokens import AccessToken
    raw=redis.Redis(host='127.0.0.1',port=proof.redis_port,db=1,password='synthetic-identity-only')
    runtime={'CACHES':{'default':{'BACKEND':'django_redis.cache.RedisCache','LOCATION':f'redis://127.0.0.1:{proof.redis_port}/1',
        'OPTIONS':{'CLIENT_CLASS':'django_redis.client.DefaultClient','PASSWORD':'synthetic-identity-only'}}},
        'CHANNEL_LAYERS':{'default':{'BACKEND':'channels_redis.core.RedisChannelLayer','CONFIG':{'hosts':[f'redis://:synthetic-identity-only@127.0.0.1:{proof.redis_port}/2']}}},
        'PASSWORD_HASHERS':PASSWORD_HASHERS,'AI_API_KEY':'','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':'','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':'',
        'STRIPE_KEY':'','STRIPE_PUBLIC_KEY':'','STRIPE_WEBHOOK_SECRET':'','MANUAL_BUNDLE_REQUIRED':False,
        'MANUAL_BUNDLE_PATH':str(work/'absent-manual'),'MANUAL_CORPUS_BASE_URL':'','MANUAL_RUN_ID':'','MANUAL_SOURCE_ROOT':''}
    server=thread=notification_pool=None
    import core.settings as core_settings
    assert 'notifications.utils' not in sys.modules, 'Private notification Redis must be bound before import'
    private_redis=dict(REDIS_HOST='127.0.0.1',REDIS_PORT=proof.redis_port,REDIS_USERNAME='default',REDIS_PASSWORD='synthetic-identity-only',REDIS_SSLAUTH=False)
    try:
      # Legacy notification storage imports constants directly. Bind only its
      # configuration to actual owned Redis; no business behavior is replaced.
      with patch.multiple(core_settings,**private_redis),override_settings(**runtime,**private_redis),ExitStack() as stack:
        def snapshot():
            return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models(include_auto_created=True) if m._meta.managed and not m._meta.proxy}
        def cache_snapshot(): return {key:raw.get(key) for key in raw.scan_iter() if not key.startswith(b'assozeta:go:readiness:')}
        password='private-impersonation-browser-password'; encoded=make_password(password)
        assert encoded.startswith('pbkdf2_sha256$')
        proof.private_values.add(password)
        people={}; associations={}
        with disable_auditlog():
            for name,role in [('owner',1),('admin',1),('athlete',2),('staff',3),('foreign',1),('foreign-athlete',2)]:
                extra={'is_superuser':name=='admin'}
                if name=='staff':extra.update(connected_user=people['owner'],collaborator_role=3,collaborator_permissions=['bookeeping.payments.read'])
                people[name]=User.original_objects.create(user_id=UUID(int=510001+len(people)),username='BROWSER-'+name.upper(),
                    email=name+'@example.test',password=encoded if name in ('owner','admin') else '!unused',role=role,
                    first_name=name.title(),last_name='Fixture',dashboard_layout={'rows':[]},date_joined=datetime.now(timezone.utc)-timedelta(days=60),**extra)
            for name in ('owner','foreign'):
                associations[name]=SportAssociation.original_objects.create(sport_association_id=UUID(int=511001+len(associations)),
                    user=people[name],denomination=name.title()+' Browser ASD',tax_code='12345678901',additional_sections=[])
                SportAssociationMembershipCardConfiguration.objects.create(sport_association=associations[name])
            for name,owner in [('athlete','owner'),('foreign-athlete','foreign')]:
                Associate.objects.all_objects().create(associate_id=UUID(int=512001 if owner=='owner' else 512002),user=people[name],
                    sport_association=associations[owner],first_name=name.title(),last_name='Fixture')
            InstanceConfiguration.objects.create(domain='browser.example.test',name='Impersonation Browser',primary_association=associations['owner'],
                setup_provenance=InstanceConfiguration.SETUP_PROVENANCE_FRESH,onboarding_completed_at=datetime.now(timezone.utc),logo_path='/api/instance/logo.png',
                display_settings={'login':{'allowOauthLogin':False},'general':{'cookieConsent':False}})
        user_content_type=ContentType.objects.get_for_model(User)
        with disable_auditlog():
            sentinel=LogEntry.objects.bulk_create([LogEntry(content_type=user_content_type,object_pk=str(people['foreign'].pk),
                object_repr='Unrelated browser audit sentinel',action=1,changes={'fixture':['before','retained']})])[0]
            AuditLogIndex.objects.create(log_entry=sentinel,sport_association=associations['foreign'],resolution_path='fixture.baseline')
        logo=Path(settings.MEDIA_ROOT)/'instance/logo.png';logo.parent.mkdir(parents=True,exist_ok=True)
        logo.write_bytes(base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGM0lT3EwMDAxAAGAAyNARjvuuOSAAAAAElFTkSuQmCC'))
        from notifications.services import NotificationService
        from notifications.utils import REDIS_POOL
        notification_pool=REDIS_POOL
        NotificationService.get_manager()
        for user in people.values():assert NotificationService.get_user_broadcasts(user)==['all_notifications']
        report['notification_dependency']={'real_private_redis':True,'broadcast_cache_warmed':len(people),'database':1}
        from core.asgi import application
        observer=Observer(application)
        server=uvicorn.Server(uvicorn.Config(observer,host='0.0.0.0',port=0,lifespan='off',log_level='critical',access_log=False))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        poll(lambda:server.started,bool,timeout=20); django_port=server.servers[0].sockets[0].getsockname()[1]
        go=None
        if 'v2' in args.versions:
            go=stack.enter_context(proof.server(work,log,tool_env,f'127.0.0.1:{proof.redis_port}',versioned=True))
        baseline=snapshot();baseline_cache=cache_snapshot(); version_reports={}
        for version in args.versions:
            observer.clear();version_out=args.output/version;version_out.mkdir(); checkpoints=work/('checkpoints-'+version);checkpoints.mkdir(mode=0o700)
            report.setdefault('browser_cleanup',{})[version]={'contexts_closed':False,'browser_closed':False}
            expectations=[]; active={}; expected_cache=dict(baseline_cache); checkpoint_rows=[]; login_times={}
            baseline_audits=list(LogEntry.objects.order_by('pk').values()); baseline_indices=list(AuditLogIndex.objects.order_by('pk').values())
            baseline_ids={row['id'] for row in baseline_audits}; checked=0; checkpoint_error=None
            def assert_state():
                state=snapshot()
                expected=copy.deepcopy(baseline)
                for row in expected['application.User']:
                    if row['user_id'] in login_times:row['last_login']=login_times[row['user_id']]
                for model in expected:
                    if model not in ('auditlog.LogEntry','application.AuditLogIndex'):assert state[model]==expected[model],('unexpected managed write',model)
                assert list(LogEntry.objects.filter(pk__in=baseline_ids).order_by('pk').values())==baseline_audits
                assert list(AuditLogIndex.objects.filter(pk__in=[row['audit_log_index_id'] for row in baseline_indices]).order_by('pk').values())==baseline_indices
                events=list(LogEntry.objects.exclude(pk__in=baseline_ids).order_by('pk'))
                assert len(events)==len(expectations),('unexpected audit count',len(events),len(expectations))
                expected_index_ids={row['audit_log_index_id'] for row in baseline_indices}
                for entry,(actor,target,identifier,action) in zip(events,expectations):
                    assert entry.action==3 and entry.actor_id==people[actor].pk and entry.object_pk==str(people[target].pk)
                    assert entry.changes=={'impersonation':['',action]} and entry.actor_email is None
                    assert entry.content_type_id==user_content_type.pk and entry.object_id is None
                    assert entry.object_repr==str(people[target]) and entry.serialized_data is None
                    assert entry.changes_text=='' and entry.cid is None
                    assert started<=entry.timestamp<=datetime.now(timezone.utc)
                    assert entry.additional_data=={'impersonation_session':identifier,'actor_id':str(people[actor].pk),
                        'administrator_id':str(people[actor].pk) if actor=='admin' else None,
                        'association_id':str(associations['owner'].pk) if actor=='owner' else None,
                        'target_user_id':str(people[target].pk),'target_role':people[target].role}
                    indices=list(AuditLogIndex.objects.filter(log_entry=entry))
                    indexed=target=='owner' or actor=='owner'
                    assert len(indices)==int(indexed)
                    expected_index_ids.update(index.pk for index in indices)
                    if indexed:
                        assert indices[0].sport_association_id==associations['owner'].pk
                        assert indices[0].resolution_path==('user.sport_association' if target=='owner' else 'actor.sport_association')
                assert set(AuditLogIndex.objects.values_list('pk',flat=True))==expected_index_ids, 'Unrelated audit index write'
                actual_cache=cache_snapshot()
                if actual_cache!=expected_cache:
                    safe_key=lambda key:re.sub(r'eyJ[A-Za-z0-9_.-]+','<redacted>',key.decode(errors='replace'))
                    report['cache_difference']={'added':[safe_key(key) for key in actual_cache.keys()-expected_cache.keys()],
                        'removed':[safe_key(key) for key in expected_cache.keys()-actual_cache.keys()],
                        'changed':[safe_key(key) for key in actual_cache.keys()&expected_cache.keys() if actual_cache[key]!=expected_cache[key]]}
                    raise AssertionError('Unexpected DB1 cache mutation')
            def handle(row):
                nonlocal checked
                actor,action,current=row['actor'],row['action'],row['context']; old=active.get(actor)
                if action=='login':
                    user_id=people[actor].pk;assert user_id not in login_times
                    value=User.original_objects.get(pk=user_id).last_login
                    assert value is not None and started<=value<=datetime.now(timezone.utc)
                    login_times[user_id]=value
                elif action in ('start','change'):
                    target=next(name for name,user in people.items() if str(user.pk)==current['target']['user_id'])
                    identifier=current['session_id'];assert UUID(identifier).version==4
                    expectations.append((actor,target,identifier,'started'))
                    if action=='change':
                        assert old; expectations.append((actor,old[0],old[1],'ended'));expected_cache.pop(cache.make_key(session_key(old[1])).encode())
                    record=cache.get(session_key(identifier));assert record
                    assert record=={
                        'actor_id':str(people[actor].pk),'target_id':str(people[target].pk),
                        'association_id':str(associations['owner'].pk) if actor=='owner' else None}
                    key=cache.make_key(session_key(identifier)).encode();assert 0<raw.pttl(key)<=3600000
                    expected_cache[key]=raw.get(key);active[actor]=(target,identifier)
                elif action=='stop':
                    assert old;expectations.append((actor,old[0],old[1],'ended'));expected_cache.pop(cache.make_key(session_key(old[1])).encode());active.pop(actor)
                elif action=='expire':
                    assert old and current['session_id']==old[1]; key=cache.make_key(session_key(old[1])).encode()
                    assert raw.pexpire(key,100);poll(lambda:raw.exists(key),lambda value:value==0,timeout=5,interval=.025)
                    expected_cache.pop(key);active.pop(actor)
                elif action=='reload':assert old and current['session_id']==old[1]
                else:assert action in ('login','recovered')
                assert_state();checked+=1
                checkpoint_rows.append({'actor':actor,'action':action,'audit_count':len(expectations),'active_sessions':len(active),
                    'all_managed_state_checked':True,'cache_db1_preserved_except_readiness':True})
            started=datetime.now(timezone.utc)
            upstream=go['proxy_upstream'] if go else f'host.docker.internal:{django_port}'
            with proxy(f'host.docker.internal:{django_port}',upstream,log,network=proof.network,network_alias='impersonation-versioned'):
              with gateway(proof,work,args.ui_dir,'impersonation-versioned:80',django_port,version) as (origin,routing):
                fixture={'origin':origin,'version':version,'password':password,'people':{name:{'name':name,'id':str(user.pk),'username':user.username,
                    'role':{'owner':'association','admin':'association','staff':'collaborator'}.get(name,'athlete')} for name,user in people.items()},
                    'playwright_module':str(args.playwright_module),'browser_cache_path':tool_env.get('PLAYWRIGHT_BROWSERS_PATH'),'checkpoints':str(checkpoints),'selected_routes':SELECTED,'coexistence_routes':COEXISTENCE}
                fixture_path=work/('browser-'+version+'.json');fixture_path.write_text(json.dumps(fixture));fixture_path.chmod(0o600)
                process=subprocess.Popen(['node',str(ROOT/'TESTS/browser/impersonation.mjs')],env={**tool_env,'TESTS_FIXTURE':str(fixture_path),'TESTS_OUTPUT':str(version_out)},stdout=log,stderr=subprocess.STDOUT)
                try:
                    until=time.monotonic()+270
                    while process.poll() is None:
                        candidate=checkpoints/f'{checked+1}.json'
                        if candidate.exists() and checkpoint_error is None:
                            row=json.loads(candidate.read_text());assert row['id']==checked+1
                            try:handle(row)
                            except BaseException as error:checkpoint_error=error
                            write_private_json(checkpoints/f'{row["id"]}.ack.json',{'success':checkpoint_error is None})
                        assert time.monotonic()<until,'Browser deadline exceeded'
                        time.sleep(.025)
                    browser=json.loads((version_out/'results.json').read_text())
                    report['browser_cleanup'][version]={'contexts_closed':browser['contexts_closed'],'browser_closed':browser['browser_closed']}
                    if checkpoint_error is not None:raise checkpoint_error
                    assert process.returncode==0 and browser['success'],browser.get('error')
                    assert_browser_acceptance(browser,checkpoint_rows)
                    assert len(expectations)==12 and not active
                    assert_state();observer.assert_selected(version)
                    version_reports[version]={'success':True,'browser':browser,'checkpoints':checkpoint_rows,'audits':len(expectations),
                        'routing':routing,'django_observer':observer.snapshot(),'managed_models':len(baseline)}
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:process.wait(timeout=10)
                        except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            with disable_auditlog():
                LogEntry.objects.exclude(pk__in=baseline_ids).delete()
                for userrow in baseline['application.User']:
                    User.original_objects.filter(pk=userrow['user_id']).update(last_login=userrow['last_login'])
            assert snapshot()==baseline and cache_snapshot()==baseline_cache
        # A successful business response still must fail the independent observer.
        if 'v2' in args.versions:
            before=snapshot();before_keys=cache_snapshot();observer.clear()
            token=AccessToken();token['user_id']=str(people['owner'].pk)
            with proxy(f'host.docker.internal:{django_port}',f'host.docker.internal:{django_port}',log,network=proof.network,network_alias='impersonation-versioned'):
              with gateway(proof,work,args.ui_dir,'impersonation-versioned:80',django_port,'v2') as (origin,_):
                status,body=Client(origin,str(token)).request('GET','/api/association/impersonation/users')
                assert status==200 and body['users']
                rejected=False
                try:observer.assert_selected('v2')
                except AssertionError as error:rejected=str(error)=='upstream_mismatch'
                assert rejected and snapshot()==before and cache_snapshot()==before_keys
                report['fallback_control']={'rejected':True,'real_status':200,'state_preserved':True,'cache_preserved':True,'observer':observer.snapshot()}
        report['versions']=version_reports
        report['checks_passed']=True
        # Resources contain only the owned fixture; compare saved fixture state,
        # then disposable container teardown removes seeded schema/data.
        report['seeded_state_restored']=snapshot()==baseline and cache_snapshot()==baseline_cache
    finally:
        if server is not None:server.should_exit=True
        if thread is not None:thread.join(timeout=30)
        try:
            try:connections.close_all()
            finally:
                try:raw.close()
                finally:
                    if notification_pool is not None:notification_pool.disconnect()
        finally:
            report['django_stopped']=thread is None or not thread.is_alive()
        assert report['django_stopped']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image');parser.add_argument('--expected-revision')
    parser.add_argument('--build-receipt',type=Path,help='Source-bound committed image build receipt; required for successful acceptance')
    parser.add_argument('--preimplementation-control',action='store_true',help='Permit a documented older image only to establish a failing browser control')
    parser.add_argument('--ui-dir',type=Path,required=True);parser.add_argument('--ui-provenance',type=Path,required=True)
    parser.add_argument('--prepare-ui-image');parser.add_argument('--versions',nargs='+',choices=('v1','v2'),default=['v1','v2'])
    parser.add_argument('--playwright-module',type=Path,default=ROOT/'selfhost/tests/browser/node_modules/@playwright/test/index.mjs')
    parser.add_argument('--output',type=Path,default=ROOT/'quality-reports/go-migration/impersonation-browser/run')
    args=parser.parse_args();args.ui_dir=args.ui_dir.resolve();args.ui_provenance=args.ui_provenance.resolve()
    if args.prepare_ui_image:prepare_ui(args.prepare_ui_image,args.ui_dir,args.ui_provenance,WORKFLOW_SOURCES);return
    require_production_python()
    assert args.image and re.fullmatch('[0-9a-f]{40}',args.expected_revision or '')
    args.playwright_module=args.playwright_module.resolve();assert args.playwright_module.is_file()
    args.output=args.output.resolve();args.output.mkdir(parents=True,exist_ok=True)
    report={'success':False,'source_sha256':source_hashes(),'scope':__doc__,'complete_impersonation_parity':False,'cleanup_verified':False}
    receipt=args.output/'results.json';assert not receipt.exists();receipt.write_text(json.dumps(report,indent=2)+'\n')
    # Schema setup clears os.environ; capture the invocation's browser cache now.
    tool_env=capture_browser_environment()
    proof=work=build=None;cleanup_errors=[]
    try:
        assert bool(args.build_receipt) != args.preimplementation_control, 'Select source-bound acceptance or preimplementation control'
        if args.build_receipt:
            build=json.loads(args.build_receipt.read_text())
            assert build['success'] and build['source_stable']
            assert build['source_revision']==args.expected_revision and build['image_id']==args.image
            assert build['source_sha256']==go_source_hashes(ROOT), 'Build source differs from checkout'
            report['build_receipt']=build
        report['preimplementation_control']=args.preimplementation_control
        provenance=json.loads(args.ui_provenance.read_text());before_ui=fingerprints(args.ui_dir)
        assert provenance['success'] and provenance['files']==before_ui
        assert provenance['extraction_container_never_started'] and provenance['extraction_container_removed']
        for source in WORKFLOW_SOURCES:
            assert provenance['workflow_source_maps'][source]
            assert all(row['embedded_sha256']==hashlib.sha256((ROOT/'UI'/source).read_bytes()).hexdigest() for row in provenance['workflow_source_maps'][source])
        report['ui_provenance']=provenance
        with tempfile.TemporaryDirectory(prefix='az-imp-browser-',dir='/tmp') as directory,(args.output/'services.log').open('w') as log:
            work=Path(directory);proof=BrowserFixture(args.image,args.expected_revision,work,args.output,log)
            try:
                proof.start_services('postgres:16-alpine','redis:7-alpine')
                initialize_current_schema(work,'127.0.0.1',proof.postgres_port,DATABASE,log)
                exercise(proof,work,log,args,report,tool_env)
            finally:
                try:proof.cleanup()
                except BaseException as error:cleanup_errors.append(type(error).__name__)
        assert not cleanup_errors
        assert fingerprints(args.ui_dir)==before_ui,'UI artifact changed'
        assert not args.preimplementation_control, 'Preimplementation control unexpectedly passed'
    except BaseException as error:
        report['error_type']=type(error).__name__
        raise
    finally:
        report.update(image=proof.metadata if proof else {},cleanup_errors=cleanup_errors,
            cleanup_verified=bool(proof and proof.cleanup_verified and not cleanup_errors),
            private_directory_removed=bool(work and not work.exists()),source_stable=source_hashes()==report['source_sha256'],
            build_source_stable=bool(build and build.get('source_sha256')==go_source_hashes(ROOT)))
        report['success']=bool(report.get('checks_passed') and report.get('seeded_state_restored') and report.get('django_stopped')
            and report['cleanup_verified'] and report['private_directory_removed'] and report['source_stable']
            and report['build_source_stable'] and not args.preimplementation_control and 'error_type' not in report)
        receipt.write_text(json.dumps(report,indent=2)+'\n')
    assert report['success']

if __name__=='__main__':main()
