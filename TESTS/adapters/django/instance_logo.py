"""Real public logo HTTP oracle over private current-model PG/Redis and MinIO.

The portable contract owns expectations. Django filesystem storage is an explicit
injection boundary; deployed settings select S3 regardless of STORE_ON_DISK.
No provider credentials, customer objects or fixed Docker tags are used.
"""
import argparse
import contextlib
import gzip
import fcntl
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import socket
import select
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'BE'), str(ROOT)]
DATABASE = 'assozeta_contract_instance_logo'


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def logo_request(client, method, path, headers, header_lines=()):
    if not header_lines:
        return client.request(method,path,headers=headers,raw=True,include_headers=True)
    # urllib's header dictionary collapses repeats. Send the authored lines
    # separately on an actual socket; never replace application responses.
    from urllib.parse import urlsplit
    target=urlsplit(client.base_url)
    assert target.scheme=='http','Fixture raw transport expects its private HTTP listener'
    connection=http.client.HTTPConnection(target.hostname,target.port,timeout=client.timeout)
    try:
        connection.putrequest(method,target.path+path,skip_accept_encoding=True)
        repeated={name.lower() for name,_ in header_lines}
        for name,value in headers.items():
            if name.lower() not in repeated:connection.putheader(name,value)
        for name,value in header_lines:connection.putheader(name,value)
        connection.endheaders();response=connection.getresponse()
        return response.status,response.read(),response.headers
    finally:connection.close()


def open_spools(pid, directory):
    """Observe real zero-link file descriptors; empty directories alone prove nothing."""
    prefix=str(Path(directory).resolve())+'/assozeta-instance-logo-'
    result=[]
    if sys.platform.startswith('linux'):
        for entry in Path(f'/proc/{pid}/fd').iterdir():
            try:
                target=os.readlink(entry)
                if not target.startswith(prefix):continue
                stat=entry.stat()
            except FileNotFoundError:continue
            result.append({'fd':int(entry.name),'inode':stat.st_ino,'bytes':stat.st_size,'links':stat.st_nlink,'mode':stat.st_mode&0o777})
    elif sys.platform=='darwin':
        process=subprocess.run(['/usr/sbin/lsof','-nP','-a','-p',str(pid),'+L1','-Ffnisk'],text=True,capture_output=True,timeout=5)
        if process.returncode not in (0,1):raise AssertionError('Cannot inspect owned Go descriptors')
        record={}
        for line in [*process.stdout.splitlines(),'f']:
            if line.startswith('f'):
                if record.get('name','').startswith(prefix):
                    result.append({key:record[key] for key in ('fd','inode','bytes','links')})
                record={'fd':int(line[1:])} if line[1:].isdigit() else {}
            elif record and line:
                key={'n':'name','s':'bytes','i':'inode','k':'links'}.get(line[0])
                if key:record[key]=line[1:] if key=='name' else int(line[1:])
    else:raise AssertionError('Unsupported descriptor observation platform')
    assert all(row['links']==0 for row in result),'Private spool is still linked'
    return result


@contextlib.contextmanager
def minio(log, env, report):
    from TESTS.adapters.django.s3_objects import IMAGE
    from TESTS.http_client import Client, poll
    def docker(*args, check=True):
        return subprocess.run(['docker', *args], check=check, capture_output=True, text=True, timeout=90, env=env)
    docker('image', 'inspect', IMAGE)
    name = 'az-instance-logo-minio-'+uuid.uuid4().hex[:12]
    access, secret = 'logo-'+uuid.uuid4().hex[:12], uuid.uuid4().hex+uuid.uuid4().hex
    try:
        docker('run', '--detach', '--rm', '--name', name, '--publish', '127.0.0.1::9000', '--tmpfs', '/data:rw,size=268435456',
               '-e', 'MINIO_ROOT_USER='+access, '-e', 'MINIO_ROOT_PASSWORD='+secret, IMAGE, 'server', '/data', '--address', ':9000')
        binding = docker('port', name, '9000/tcp').stdout.strip()
        endpoint = 'http://'+binding
        def ready():
            try: return Client(endpoint).request('GET', '/minio/health/ready')[0] == 200
            except (OSError, http.client.HTTPException): return False
        poll(ready, bool, timeout=20)
        report['minio'] = {'image': IMAGE, 'container': name, 'owned': True}
        yield endpoint, access, secret
    finally:
        docker('rm', '-f', name, check=False)
        report['private_minio_removed'] = docker('container', 'inspect', name, check=False).returncode != 0
        assert report['private_minio_removed']


class StorageProxy:
    """Transparent S3 hop; fault stimuli delete only the owned PNG in real MinIO.

    Deletion and denied credentials obtain real MinIO errors. Explicit labelled
    transport faults exercise SDK retries without replacing business responses.
    """
    def __init__(self, endpoint, admin, bucket, key):
        target = endpoint.removeprefix('http://').split(':')
        self.host, self.port = target[0], int(target[1])
        self.admin, self.bucket, self.key = admin, bucket, key
        self.requests, self.fault, self.heads, self.fault_applied = [], None, 0, False
        self.active=0
        self.stall_started,self.stall_release,self.stall_disconnected=threading.Event(),threading.Event(),threading.Event()
        outer = self
        class Forward(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def do_HEAD(self): self.dispatch()
            def do_GET(self): self.dispatch()
            def dispatch(self):
                outer.active+=1
                try:self.forward()
                finally:outer.active-=1
            def forward(self):
                if self.path.split('?',1)[0].endswith('/'+outer.key):
                    if self.command == 'HEAD': outer.heads += 1
                    apply = (outer.fault == 'delete-before-get' and self.command == 'GET')
                    if apply and not outer.fault_applied:
                        outer.admin.delete_object(Bucket=outer.bucket, Key=outer.key)
                        outer.fault_applied = True
                target_png = self.path.split('?',1)[0].endswith('/'+outer.key)
                injected = target_png and ((outer.fault == 'head-always500' and self.command == 'HEAD') or (not outer.fault_applied and ((outer.fault == 'head-once500' and self.command == 'HEAD') or (outer.fault == 'get-once500' and self.command == 'GET'))))
                if injected:
                    outer.fault_applied = True
                    body = b'<Error><Code>InternalError</Code><Message>owned transient storage fault</Message></Error>'
                    outer.requests.append({'method':self.command,'path':self.path.split('?',1)[0],'status':500,'injected_transport_failure':True})
                    self.send_response_only(500);self.send_header('Content-Length',str(len(body)));self.send_header('Connection','close');self.end_headers()
                    if self.command != 'HEAD':self.wfile.write(body)
                    self.close_connection=True
                    return
                connection = http.client.HTTPConnection(outer.host, outer.port, timeout=20)
                try:
                    # Keep the signed Host and every request header unchanged.
                    forwarded_headers=dict(self.headers)
                    denied=(target_png and outer.fault=='head-denied' and self.command=='HEAD') or (target_png and outer.fault=='get-png-denied' and self.command=='GET') or (outer.fault=='get-all-denied' and self.command=='GET')
                    if denied:
                        forwarded_headers['Authorization']=re.sub(r'Credential=[^/]+/', 'Credential=invalid-fixture-key/', forwarded_headers.get('Authorization',''))
                        outer.fault_applied=True
                    connection.request(self.command, self.path, headers=forwarded_headers)
                    response = connection.getresponse()
                    if target_png and outer.fault == 'delete-after-exists' and self.command == 'HEAD' and response.status == 200 and not outer.fault_applied:
                        # Real existence succeeded; delete before forwarding its
                        # intact response so any subsequent open operation fails.
                        outer.admin.delete_object(Bucket=outer.bucket,Key=outer.key)
                        outer.fault_applied=True
                    outer.requests.append({'method': self.command, 'path': self.path.split('?',1)[0], 'status': response.status})
                    self.send_response_only(response.status)
                    for name,value in response.getheaders():
                        if name.lower() not in ('connection','transfer-encoding'): self.send_header(name,value)
                    self.send_header('Connection','close'); self.end_headers()
                    if self.command != 'HEAD':
                        if target_png and outer.fault=='stall-get':
                            self.wfile.write(response.read(3));self.wfile.flush();outer.stall_started.set()
                            while not outer.stall_release.wait(.01):
                                if select.select([self.connection],[],[],0)[0]:
                                    try:closed=not self.connection.recv(1,socket.MSG_PEEK)
                                    except (ConnectionResetError,OSError):closed=True
                                    if closed:outer.stall_disconnected.set();self.close_connection=True;return
                        if target_png and outer.fault == 'get-once-truncated' and not outer.fault_applied:
                            outer.fault_applied=True
                            self.wfile.write(response.read(3))
                        else:
                            while chunk := response.read(65536): self.wfile.write(chunk)
                    self.close_connection = True
                finally: connection.close()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Forward)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f'http://127.0.0.1:{self.server.server_port}'

    def reset(self, fault):
        self.requests, self.fault, self.heads, self.fault_applied = [], fault, 0, False
        self.stall_started.clear();self.stall_release.clear();self.stall_disconnected.clear()

    def close(self):
        self.stall_release.set()
        from TESTS.http_client import poll
        poll(lambda:self.active,lambda active:active==0,timeout=20)
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=10)
        assert not self.thread.is_alive(), 'Storage proxy did not stop'


def exercise(work, log, options, env, report):
    import boto3
    from botocore.config import Config
    import redis
    import uvicorn
    from django.apps import apps
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.core.cache import cache
    from django.db import connections
    from django.test import override_settings
    from TESTS.contracts.test_instance_logo import cases, payload_bytes, assert_observation, case_for_transport
    from TESTS.http_client import Client, poll
    from TESTS.adapters.go.server import build_server

    def snapshot():
        return {m._meta.label: list(m._base_manager.order_by('pk').values()) for m in apps.get_models(include_auto_created=True)
                if m._meta.managed and not m._meta.proxy}
    original = snapshot()
    report['managed_models'] = len(original)
    from auditlog.context import disable_auditlog
    from application.models import User, SportAssociation
    from instance.models import DataRestore, InstanceConfiguration
    with disable_auditlog():
        owner=User.original_objects.create(user_id=uuid.uuid4(),username='LOGO-FIXTURE',email='logo@example.test',password='!fixture',role=1)
        association=SportAssociation.original_objects.create(sport_association_id=uuid.uuid4(),user=owner,denomination='Logo fixture')
        configuration=InstanceConfiguration.objects.create(domain='logo.example.test',logo_path='private-storage-canary/secret')
    cache_port = free_port()
    cache_password = 'private-logo-cache'
    redis_process = subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(cache_port),'--save','','--appendonly','no',
                                     '--requirepass',cache_password,'--dir',str(work)],stdout=log,stderr=subprocess.STDOUT)
    redis_client = redis.Redis(host='127.0.0.1',port=cache_port,db=1,password=cache_password)
    server = thread = go_process = proxy = None
    try:
        def cache_ready():
            try: return redis_client.ping()
            except redis.ConnectionError: return False
        poll(cache_ready,bool,timeout=15)
        with minio(log,env,report) as (endpoint,access,secret):
            admin = boto3.client('s3',endpoint_url=endpoint,aws_access_key_id=access,aws_secret_access_key=secret,region_name='us-east-1',
                config=Config(signature_version='s3v4',retries={'max_attempts':0},s3={'addressing_style':'path'}))
            bucket, location = 'logo-fixture', 'private-root'
            admin.create_bucket(Bucket=bucket)
            sentinel='unrelated/private-storage-canary'
            admin.put_object(Bucket=bucket,Key=sentinel,Body=b'private-storage-canary',ContentType='application/octet-stream',Metadata={'owned':'sentinel'})
            def object_snapshot():
                result={}
                for row in admin.list_objects_v2(Bucket=bucket).get('Contents',[]):
                    value=admin.get_object(Bucket=bucket,Key=row['Key'])
                    try: digest=hashlib.sha256(value['Body'].read()).hexdigest()
                    finally: value['Body'].close()
                    result[row['Key']]={'sha256':digest,**{key:value.get(key) for key in ('ContentType','ContentLength','ContentEncoding','CacheControl','ContentDisposition','Metadata','ETag','LastModified')}}
                return result
            sentinel_state=object_snapshot()[sentinel]
            proxy = StorageProxy(endpoint,admin,bucket,location+'/instance/logo.png')
            cache_settings = {'default':{'BACKEND':'django_redis.cache.RedisCache','LOCATION':f'redis://127.0.0.1:{cache_port}/1',
                'OPTIONS':{'CLIENT_CLASS':'django_redis.client.DefaultClient','PASSWORD':cache_password}}}
            runtime = dict(AWS_ACCESS_KEY_ID=access,AWS_SECRET_ACCESS_KEY=secret,AWS_STORAGE_BUCKET_NAME=bucket,
                AWS_S3_REGION_NAME='us-east-1',AWS_S3_ENDPOINT_URL=proxy.endpoint,AWS_LOCATION=location,
                AWS_S3_ADDRESSING_STYLE='path',CACHES=cache_settings,STORE_ON_DISK=True,
                CORS_ORIGIN_ALLOW_ALL=False,CORS_ALLOWED_ORIGINS=['https://allowed.example.test'])
            with override_settings(**runtime),contextlib.ExitStack() as gateways:
                observed = []
                application = get_asgi_application()
                async def traced(scope,receive,send):
                    if scope['type']=='http': observed.append((scope['method'],scope['path']))
                    await application(scope,receive,send)
                server = uvicorn.Server(uvicorn.Config(traced,host='0.0.0.0',port=0,lifespan='off',log_level='critical',access_log=False))
                thread = threading.Thread(target=server.run,daemon=True); thread.start()
                poll(lambda: server.started,bool,timeout=20)
                django_base = 'http://127.0.0.1:'+str(server.servers[0].sockets[0].getsockname()[1])
                go_base = None
                if options.backend in ('go','both'):
                    build_env=env
                    if options.disk_injection:
                        # Explicit fixture composition matches Django's overridden
                        # STORAGES; production composition remains unmodified S3.
                        replacement=work/'instance_logo_disk.go'
                        replacement.write_text('package main\nimport ("github.com/carbogninalberto/assozeta/BE_GO/internal/config"; "github.com/carbogninalberto/assozeta/BE_GO/internal/handler"; "github.com/carbogninalberto/assozeta/BE_GO/internal/service"; "github.com/carbogninalberto/assozeta/BE_GO/pkg/storage/instancelogo")\nfunc composeInstanceLogo(_ config.Config) *handler.InstanceLogoHandler { storage,err:=instancelogo.NewDisk('+json.dumps(settings.MEDIA_ROOT)+'); if err!=nil { panic(err) }; return handler.NewInstanceLogoHandler(service.NewInstanceLogo(storage)) }\n')
                        overlay=work/'disk-overlay.json'
                        overlay.write_text(json.dumps({'Replace':{str(ROOT/'BE_GO/cmd/server/instance_logo.go'):str(replacement)}}))
                        build_env={**env,'GOFLAGS':'-overlay='+str(overlay)}
                        report['disk_injection']={'boundary':'Only composition is replaced; real Disk/service/handler/global middleware/PG/Redis are unchanged','replacement_sha256':hashlib.sha256(replacement.read_bytes()).hexdigest(),'replacement_source':replacement.read_text(),'original_sha256':hashlib.sha256((ROOT/'BE_GO/cmd/server/instance_logo.go').read_bytes()).hexdigest()}
                    build_server(work/'logo-server',log,build_env)
                    go_port=free_port()
                    go_env={**env,'DBTYPE':'POSTGRES','DBNAME':DATABASE,'DBUSER':'contracts','DBPASSWORD':'test-only','DBHOST':str(work),'DBPORT':'5432',
                        'REDIS_HOST':'127.0.0.1','REDIS_PORT':str(cache_port),'REDIS_PASSWORD':cache_password,'GO_LISTEN_ADDR':f'0.0.0.0:{go_port}',
                        'DEBUG':'False','RATELIMIT_ENABLE':'False','DATA_RESTORE_LOCK_PATH':settings.DATA_RESTORE_LOCK_PATH,
                        'STORE_ON_DISK':'True','CORS_ORIGIN_ALLOW_ALL':'False','CORS_ALLOWED_ORIGINS':'https://allowed.example.test',
                        **{k:str(v) for k,v in runtime.items() if k.startswith('AWS_')},
                        **{k:getattr(settings,k) for k in ('SECRET_KEY','JWT_SECRET_KEY','JWT_PUBLIC_KEY','SOCIAL_AUTH_GOOGLE_OAUTH2_KEY','SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET')}}
                    spool_dir=work/'go-spool';spool_dir.mkdir();go_env['GO_LOGO_SPOOL_DIR']=str(spool_dir)
                    go_process=subprocess.Popen([str(work/'logo-server')],cwd=work,env=go_env,stdout=log,stderr=subprocess.STDOUT)
                    go_base='http://127.0.0.1:'+str(go_port)
                    def ready():
                        if go_process.poll() is not None: raise AssertionError('Go server exited')
                        try: return Client(go_base).request('GET','/readyz')[0]==200
                        except OSError: return False
                    poll(ready,bool,timeout=30)
                    if options.versioned:
                        from TESTS.adapters.caddy import proxy as caddy_proxy
                        gateway,image=gateways.enter_context(caddy_proxy(django_base.replace('http://127.0.0.1:','host.docker.internal:'),go_base.replace('http://127.0.0.1:','host.docker.internal:'),log))
                        report['caddy_image']=image
                backends=['django'] if options.backend=='django' else ['go'] if options.backend=='go' else ['django','go']
                if options.versioned:backends+=['v1','v2']
                for backend in backends:
                    active_location=location
                    is_go=backend in ('go','v2')
                    modes=['disk'] if options.disk_injection else ['disk','s3'] if backend=='django' else ['s3']
                    for mode in modes:
                        label=backend+'-'+mode
                        rows=report.setdefault('workloads',{}).setdefault(label,[])
                        client=Client(gateway if backend in ('v1','v2') else django_base if backend=='django' else go_base,timeout=60)
                        prefix='/api/'+backend if backend in ('v1','v2') else ''
                        storage={'BACKEND':'django.core.files.storage.FileSystemStorage'} if mode=='disk' else {'BACKEND':'storages.backends.s3boto3.S3Boto3Storage'}
                        with override_settings(STORAGES={'default':storage,'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}):
                            for case in cases():
                                if case['mode'] not in ('both',mode): continue
                                case=case_for_transport(case,backend in ('v1','v2'))
                                selected_location=case['setup'].get('location',location)
                                if is_go and selected_location!=active_location:
                                    go_process.terminate();go_process.wait(timeout=20)
                                    go_process=subprocess.Popen([str(work/'logo-server')],cwd=work,env={**go_env,'AWS_LOCATION':selected_location},stdout=log,stderr=subprocess.STDOUT)
                                    poll(ready,bool,timeout=30);active_location=selected_location
                                cache.clear(); proxy.reset(None)
                                disk=Path(settings.MEDIA_ROOT)/'instance';shutil.rmtree(disk,ignore_errors=True);disk.mkdir(parents=True)
                                for obj in admin.list_objects_v2(Bucket=bucket).get('Contents',[]):
                                    if obj['Key']!=sentinel:admin.delete_object(Bucket=bucket,Key=obj['Key'])
                                for ext,payload in case['setup']['files'].items():
                                    filename='instance/logo.'+ext
                                    if payload=='directory': (Path(settings.MEDIA_ROOT)/filename).mkdir()
                                    elif payload=='broken-symlink': (Path(settings.MEDIA_ROOT)/filename).symlink_to(work/'nonexistent-target')
                                    elif payload=='readable-symlink':
                                        target=work/'owned-symlink-target';target.write_bytes(payload_bytes(payload));(Path(settings.MEDIA_ROOT)/filename).symlink_to(target)
                                    elif mode=='disk': (Path(settings.MEDIA_ROOT)/filename).write_bytes(payload_bytes(payload))
                                    else: admin.put_object(Bucket=bucket,Key=(selected_location.rstrip('/')+'/' if selected_location else '')+filename,Body=payload_bytes(payload),**case['setup'].get('metadata',{}))
                                proxy.reset(case['setup'].get('fault'))
                                with disable_auditlog():
                                    DataRestore.objects.all().delete()
                                    if case['setup'].get('pending'):
                                        DataRestore.objects.create(owner=owner,association_id=association.pk,state='queued',sha256='a'*64)
                                request=case['request'];request_headers=dict(request['headers'])
                                cookie=case['setup'].get('cookie')
                                if cookie:
                                    request_headers['Cookie']='sessionid=logo-session'
                                    if cookie!='missing':
                                        cache.set('django.contrib.sessions.cachelogo-session',{'_auth_user_id':str(owner.pk),'_auth_user_backend':'django.contrib.auth.backends.ModelBackend','_auth_user_hash':owner.get_session_auth_hash() if cookie=='valid' else 'invalid-hash'},300)
                                cache_before={key:redis_client.get(key) for key in redis_client.scan_iter()}
                                before=snapshot(); observed.clear()
                                objects_before=object_snapshot()
                                assert objects_before[sentinel]==sentinel_state
                                sql_log=work/'postgres.log'; sql_offset=sql_log.stat().st_size
                                request=case['request']
                                with override_settings(AWS_LOCATION=selected_location,STORAGES={'default':storage,'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}),contextlib.ExitStack() as locks:
                                    if case['setup'].get('lock'):
                                        lock=locks.enter_context(open(settings.DATA_RESTORE_LOCK_PATH,'a'))
                                        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                                    status,wire,headers=logo_request(client,request['method'],prefix+request['path'],request_headers,request.get('header_lines',[]))
                                sql_lines=sql_log.read_bytes()[sql_offset:].decode(errors='replace').splitlines()
                                queries=[line.split('LOG:  ',1)[-1] for line in sql_lines if re.search(r'LOG:  (?:statement:|execute [^:]+:)',line)]
                                objects_after=object_snapshot()
                                expected_objects=dict(objects_before)
                                if mode=='s3' and case['setup'].get('fault') in ('delete-after-exists','delete-before-get'):
                                    del expected_objects[location+'/instance/logo.png']
                                if is_go:poll(lambda:not list(spool_dir.iterdir()) and not open_spools(go_process.pid,spool_dir),bool,timeout=5)
                                decoded=gzip.decompress(wire) if wire and headers.get('Content-Encoding')=='gzip' else wire
                                try: parsed=json.loads(decoded) if decoded else None
                                except (ValueError,UnicodeDecodeError): parsed=None
                                item={'name':case['name'],'status':status,'headers':{name:headers.get(name) for name in case['expected']['headers']},
                                    'vary_tokens':sorted({token.strip().lower() for value in headers.get_all('Vary',[]) for token in value.split(',')}),
                                    'body_sha256':hashlib.sha256(decoded).hexdigest(),'decoded_body_bytes':len(decoded),'wire_body_bytes':len(wire),'json':parsed,
                                    'managed_state_unchanged':snapshot()==before,'private_storage_canary_absent':b'private-storage-canary' not in decoded,
                                    'storage_state_expected':objects_after==expected_objects,'storage_sentinel_unchanged':objects_after.get(sentinel)==sentinel_state,
                                    'sql_statements':queries,'sql_count':len(queries),'go_spool_empty':not list(spool_dir.iterdir()) if is_go else None,'go_open_spools':open_spools(go_process.pid,spool_dir) if is_go else None,
                                    'raw_header_lines_sent':request.get('header_lines',[]),'storage_requests':list(proxy.requests),'fault_applied':proxy.fault_applied,'django_requests':len(observed),'success':False}
                                rows.append(item)
                                try:
                                    assert_observation(item,case)
                                    if isinstance(case['expected']['headers'].get('Content-Encoding'),list) and headers.get('Content-Encoding')=='gzip':
                                        assert len(wire)<len(decoded),'Finite gzip response must be beneficial'
                                    assert len(observed)==int(not is_go),'Wrong actual upstream'
                                    assert item['storage_state_expected'] and item['storage_sentinel_unchanged'],'Unexpected object-store mutation'
                                    statements=[re.sub(r'^.*?(?:statement:|execute [^:]+:)\s*','',query) for query in queries]
                                    meaningful=[statement for statement in statements if not statement.startswith('--')]
                                    assert all(statement.upper().startswith(('SELECT','SHOW','BEGIN','COMMIT','ROLLBACK')) for statement in meaningful),'Logo issued write SQL'
                                    item['connection_setup_selects']=sum(statement.startswith("SELECT set_config('TimeZone'") for statement in meaningful)
                                    selects=sum(statement.upper().startswith('SELECT') and not statement.startswith("SELECT set_config('TimeZone'") for statement in meaningful)
                                    bound=int(request['method'] not in ('GET','HEAD','OPTIONS'))+int(cookie in ('valid','stale'))
                                    assert selects<=bound,('Unbounded logo SQL',selects,bound)
                                    item['sql_selects']=selects;item['sql_select_bound']=bound;item['sql_writes']=0
                                    cache_after={key:redis_client.get(key) for key in redis_client.scan_iter()}
                                    if cookie in ('stale','missing'):
                                        assert not cache_after and 'sessionid=' in (headers.get('Set-Cookie') or ''),'Invalid session must expire'
                                    else:assert cache_after==cache_before,'Logo unexpectedly changed session/cache'
                                    item['cache_semantics_verified']=True
                                    if case['setup'].get('fault'):assert proxy.fault_applied,'Requested real-object fault was not reached'
                                    allowed_paths={'/'+bucket+'/'+(selected_location.rstrip('/')+'/' if selected_location else '')+'instance/logo.'+ext for ext in ('png','jpg','svg','webp')}
                                    assert all(row['method'] in ('HEAD','GET') and row['path'] in allowed_paths for row in proxy.requests),'Storage accessed an unrelated object/key'
                                    if mode=='disk' or case['name'].startswith('unregistered-route-'):assert not proxy.requests,'Request unexpectedly accessed S3'
                                    item['fixed_storage_keys_verified']=True
                                    item['success']=True
                                finally:
                                    (options.output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
                        expected_names={case['name'] for case in cases() if case['mode'] in ('both',mode)}
                        assert len(rows)==len(expected_names) and {row['name'] for row in rows}==expected_names and all(row['success'] for row in rows), 'Incomplete authored workload'
                        report.setdefault('expected_case_names',{})[label]=sorted(expected_names)
                        print(f'PASS {label}: {len(rows)} authored actual logo HTTP cases',flush=True)
                if not options.disk_injection:
                    # Separate lifecycle observations from the authored response matrix.
                    # A held real MinIO response distinguishes caller cancellation from
                    # provider timeout, with release guaranteed before fixture teardown.
                    from urllib.parse import urlsplit
                    from TESTS.contracts.test_instance_logo import PATH
                    with disable_auditlog():DataRestore.objects.all().delete()
                    cache.clear()
                    if go_process and active_location!=location:
                        go_process.terminate();go_process.wait(timeout=20)
                        go_process=subprocess.Popen([str(work/'logo-server')],cwd=work,env=go_env,stdout=log,stderr=subprocess.STDOUT)
                        poll(ready,bool,timeout=30)
                    def require_upstream(expected_django):
                        assert len(observed)==int(expected_django),'Independent Django observer detected wrong upstream'
                    selected=[('django',django_base,'',False)] if options.backend=='django' else [('go',go_base,'',True)]
                    if options.backend=='both':selected.insert(0,('django',django_base,'',False))
                    if options.versioned:selected+=[('v1',gateway,'/api/v1',False),('v2',gateway,'/api/v2',True)]
                    for label,base,prefix,is_go in selected:
                        storage={'BACKEND':'storages.backends.s3boto3.S3Boto3Storage'}
                        with override_settings(AWS_LOCATION=location,STORAGES={'default':storage,'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}):
                            admin.put_object(Bucket=bucket,Key=location+'/instance/logo.png',Body=payload_bytes('png'))
                            before=snapshot();objects_before=object_snapshot();observed.clear();proxy.reset('stall-get')
                            address=urlsplit(base);caller=socket.create_connection((address.hostname,address.port),timeout=5)
                            row={'backend':label,'phase':'storage-preparation','success':False}
                            report.setdefault('disconnects',[]).append(row)
                            try:
                                caller.sendall(f'GET {prefix+PATH} HTTP/1.1\r\nHost: {address.netloc}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n'.encode())
                                assert proxy.stall_started.wait(10),'Actual MinIO GET was not reached'
                                if is_go:
                                    assert not list(spool_dir.iterdir()),'Private spool pathname remains'
                                    descriptors=open_spools(go_process.pid,spool_dir)
                                    assert len(descriptors)==1,'No actual anonymous download descriptor before disconnect'
                                    row['spool_before_disconnect']=descriptors
                                caller.close()
                                ended=proxy.stall_disconnected.wait(3)
                                row['upstream_closed_before_release']=ended
                                if is_go:
                                    assert ended,'Go retained provider connection after caller disconnect'
                                    poll(lambda:not list(spool_dir.iterdir()) and not open_spools(go_process.pid,spool_dir),bool,timeout=5)
                                    row['spool_removed_before_release']=True
                            finally:caller.close();proxy.stall_release.set()
                            poll(lambda:proxy.active,lambda active:active==0,timeout=15)
                            require_upstream(not is_go)
                            row.update(managed_state_unchanged=snapshot()==before,storage_state_unchanged=object_snapshot()==objects_before)
                            assert row['managed_state_unchanged'] and row['storage_state_unchanged']
                            proxy.reset(None);observed.clear()
                            status,body=Client(base,timeout=60).request('GET',prefix+PATH,raw=True)
                            assert status==200 and body==payload_bytes('png'),'Cancellation recovery failed'
                            require_upstream(not is_go);row['recovery_status']=status;row['success']=True
                            if is_go:
                                stream_payload=b'x'*(65*1024*1024)
                                assert len(stream_payload)==65*1024*1024
                                admin.put_object(Bucket=bucket,Key=location+'/instance/logo.png',Body=stream_payload)
                                objects_before=object_snapshot();observed.clear();proxy.reset(None)
                                caller=socket.socket();caller.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4096);caller.settimeout(10);caller.connect((address.hostname,address.port))
                                streamed={'backend':label,'phase':'response-stream','success':False};report['disconnects'].append(streamed)
                                try:
                                    caller.sendall(f'GET {prefix+PATH} HTTP/1.1\r\nHost: {address.netloc}\r\nAccept-Encoding: identity\r\nConnection: close\r\n\r\n'.encode())
                                    headers=b''
                                    while not headers.endswith(b'\r\n\r\n'):
                                        chunk=caller.recv(1);assert chunk,'Response ended before headers';headers+=chunk
                                    assert b' 200 ' in headers.split(b'\r\n')[0]
                                    assert not list(spool_dir.iterdir()),'Private spool pathname remains'
                                    descriptors=open_spools(go_process.pid,spool_dir)
                                    streamed['spool_after_headers']=descriptors
                                    streamed['response_bytes']=len(stream_payload)
                                    assert len(descriptors)==1 and descriptors[0]['bytes']==len(stream_payload),'Response was not actually held by slow client'
                                    streamed['held_spool_after_headers']=True
                                finally:caller.close()
                                poll(lambda:not list(spool_dir.iterdir()) and not open_spools(go_process.pid,spool_dir),bool,timeout=5)
                                require_upstream(False)
                                assert snapshot()==before and object_snapshot()==objects_before
                                streamed.update(spool_removed_after_disconnect=True,managed_state_unchanged=True,storage_state_unchanged=True,success=True)
                    if options.versioned:
                        proxy.reset(None);admin.put_object(Bucket=bucket,Key=location+'/instance/logo.png',Body=payload_bytes('png'))
                        with caddy_proxy(django_base.replace('http://127.0.0.1:','host.docker.internal:'),django_base.replace('http://127.0.0.1:','host.docker.internal:'),log) as (wrong,_),override_settings(STORAGES={'default':{'BACKEND':'storages.backends.s3boto3.S3Boto3Storage'},'staticfiles':{'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}}):
                            observed.clear();status,_=Client(wrong).request('GET','/api/v2'+PATH,raw=True)
                            assert status==200 and len(observed)==1
                            rejected=False
                            try:require_upstream(False)
                            except AssertionError:rejected=True
                            assert rejected,'Deliberate real Django200 fallback was accepted'
                            report['misroute_negative_control']={'status':status,'django_requests':len(observed),'rejected':rejected}

    finally:
        try:
            if go_process:
                go_process.terminate()
                try:go_process.wait(timeout=20)
                except subprocess.TimeoutExpired:go_process.kill();go_process.wait(timeout=10)
            if server:server.should_exit=True
            if thread:thread.join(timeout=20);assert not thread.is_alive()
        finally:
            try:
                if proxy:proxy.close()
                with disable_auditlog():
                    DataRestore.objects.all().delete()
                    InstanceConfiguration.objects.filter(pk=configuration.pk).delete()
                    SportAssociation.original_objects.filter(pk=association.pk).delete()
                    User.original_objects.filter(pk=owner.pk).delete()
                report['managed_cleanup_verified']=snapshot()==original
                assert report['managed_cleanup_verified']
            finally:
                try:connections.close_all()
                finally:
                    redis_process.terminate()
                    try:redis_process.wait(timeout=10)
                    except subprocess.TimeoutExpired:redis_process.kill();redis_process.wait(timeout=10)
                    report['private_services_stopped']=not (thread and thread.is_alive()) and not (go_process and go_process.poll() is None) and redis_process.poll() is not None


def main():
    from TESTS.adapters.django.production_runtime import require_production_python
    require_production_python()
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',choices=['django','go','both'],default='django')
    parser.add_argument('--versioned',action='store_true')
    parser.add_argument('--disk-injection',action='store_true')
    parser.add_argument('--output',type=Path,required=True)
    options=parser.parse_args()
    if options.versioned and options.backend!='both':parser.error('--versioned requires --backend both')
    if options.disk_injection and (options.backend!='go' or options.versioned):parser.error('--disk-injection is a native Go composition test only')
    options.output=options.output.resolve();options.output.mkdir(parents=True,exist_ok=True)
    path=options.output/'results.json';assert not path.exists(),'Use a fresh output directory'
    owned=['TESTS/contracts/test_instance_logo.py','TESTS/adapters/django/instance_logo.py']
    owned += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'BE_GO').rglob('*.go'))]
    owned += ['selfhost/caddy/Caddyfile','TESTS/adapters/caddy.py','BE_GO/go.mod','BE_GO/go.sum','BE/instance/views.py','BE/instance/urls.py','BE/core/settings.py']
    sources={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in owned}
    started_at=time.monotonic()
    report={'success':False,'scope':__doc__,'source_hashes':sources,'complete_logo_parity':False,
        'production_storage_selection':'S3; filesystem is explicit injection only','private_services_stopped':False,
        'private_postgres_stopped':False,'private_minio_removed':False,'managed_cleanup_verified':False}
    path.write_text(json.dumps(report,indent=2)+'\n')
    postgres=shutil.which('postgres') or '/opt/homebrew/opt/postgresql@16/bin/postgres'
    binaries=Path(postgres).parent
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN','DOCKER_HOST','DOCKER_CONTEXT')}
    env.update(LC_ALL='C',LANG='C',AWS_EC2_METADATA_DISABLED='true')
    try:
        with tempfile.TemporaryDirectory(prefix='az-logo-',dir='/tmp') as directory,(options.output/'services.log').open('w') as log:
            work=Path(directory);started=False
            for filename in ('aws-config','aws-credentials'):(work/filename).write_text('')
            env.update(AWS_CONFIG_FILE=str(work/'aws-config'),AWS_SHARED_CREDENTIALS_FILE=str(work/'aws-credentials'))
            def command(args):return subprocess.run([str(x) for x in args],check=True,timeout=180,stdout=log,stderr=subprocess.STDOUT,env=env)
            try:
                command([binaries/'initdb','-D',work/'postgres','-A','trust','-U','contracts','--no-sync','--no-locale','-E','UTF8'])
                started=True
                command([binaries/'pg_ctl','-D',work/'postgres','-l',work/'postgres.log','-o',f"-k {work} -h '' -F -c log_statement=all",'-w','start'])
                command([binaries/'createdb','-h',work,'-U','contracts',DATABASE])
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
                from cryptography.hazmat.primitives import serialization
                key=Ed25519PrivateKey.generate()
                import environ
                os.environ.clear();os.environ.update(env);environ.Env.read_env(str(ROOT/'selfhost/.env.example'))
                os.environ.update(DEBUG='False',JWT_SECRET_KEY_B64='',JWT_PUBLIC_KEY_B64='',
                    JWT_SECRET_KEY=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),
                    JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode(),
                    DJANGO_SETTINGS_MODULE='TESTS.adapters.django.settings',TESTS_DATABASE=DATABASE,TESTS_WORKDIR=str(work),
                    DBTYPE='POSTGRES',DBHOST=str(work),DBPORT='5432',DBNAME=DATABASE,DBUSER='contracts',DBPASSWORD='test-only',
                    USE_PGBOUNCER='False',STORE_ON_DISK='True')
                import django
                with patch('environ.Env.read_env'):django.setup()
                from django.apps import apps
                from django.conf import settings
                from django.core.management import call_command
                settings.MIGRATION_MODULES={app.label:None for app in apps.get_app_configs()}
                call_command('migrate',interactive=False,verbosity=0,run_syncdb=True,stdout=log,stderr=log)
                from application.audit_resolvers import setup_audit_resolvers
                setup_audit_resolvers()
                exercise(work,log,options,env,report)
            finally:
                try:
                    if 'django.db' in sys.modules:
                        from django.db import connections
                        connections.close_all()
                finally:
                    if started:
                        stopped=subprocess.run([str(binaries/'pg_ctl'),'-D',str(work/'postgres'),'-m','immediate','-w','stop'],stdout=log,stderr=subprocess.STDOUT,timeout=30)
                        report['private_postgres_stopped']=stopped.returncode==0 or not (work/'postgres/postmaster.pid').exists()
                        assert report['private_postgres_stopped']
        assert sources=={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in owned},'Oracle source changed during run'
        assert all(report[k] for k in ('private_services_stopped','private_postgres_stopped','private_minio_removed','managed_cleanup_verified'))
        report['source_stable']=True
        report['success']=True
    except Exception as error:
        report['failure']={'class':type(error).__name__,'message':str(error)}
        raise
    finally:
        report['elapsed_seconds']=round(time.monotonic()-started_at,3)
        path.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
