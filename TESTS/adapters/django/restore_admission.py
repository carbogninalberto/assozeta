"""Real application restore admission with PostgreSQL and shared OS locks."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
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
    from django.test import override_settings
    from auditlog.context import disable_auditlog
    from instance.models import DataRestore
    from instance.restore.locking import operation_lock
    from TESTS.http_client import Client,poll
    import redis
    import uvicorn
    out=ROOT/'quality-reports/go-migration/restore-admission';out.mkdir(parents=True,exist_ok=True)
    (out/'http-results.json').write_text(json.dumps({'success':False,'phase':'started'})+'\n')
    def snapshot():return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    original=snapshot()
    ids=[uid(84000+i) for i in range(4)]
    assert not DataRestore.objects.filter(pk__in=ids).exists(),'restore fixture IDs collide'
    DataRestore.objects.bulk_create([DataRestore(id=pk,owner_id=uid(8),association_id=uid(80),state=state,sha256='0'*64)
        for pk,state in zip(ids,['review','completed','failed','review'])])
    logical='django.contrib.sessions.cacherestore-malformed';cache.set(logical,[],300)
    host,redis_port=address.rsplit(':',1)
    raw_cache=redis.Redis(host=host,port=int(redis_port),db=1,password='synthetic-identity-only')
    key=cache.make_key(logical);payload=raw_cache.get(key)
    lock_path=str(work/'contract-lifecycle.lock')
    modes=('open','queued','running','locked','unavailable','disabled')
    def configuration(mode):return '' if mode=='disabled' else str(work) if mode=='unavailable' else lock_path
    def set_mode(mode):
        DataRestore.objects.filter(pk=ids[-1]).update(state=mode if mode in ('queued','running') else 'queued' if mode=='disabled' else 'review')
    def contracts(base,mode):
        before=snapshot()
        with ExitStack() as stack:
            stack.enter_context(override_settings(DATA_RESTORE_LOCK_PATH=configuration(mode),CORS_ORIGIN_ALLOW_ALL=False,CORS_ALLOWED_ORIGINS=['https://allowed.example.test']))
            if mode=='locked':stack.enter_context(operation_lock(exclusive=True))
            stack.enter_context(patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_RESTORE_MODE=mode))
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_restore_admission'))
            assert result.wasSuccessful() and not result.skipped and result.testsRun==7,'restore admission contracts failed: '+mode
        assert snapshot()==before,'restore admission changed managed models'
        assert raw_cache.get(key)==payload,'restore admission changed malformed session bytes'
        with override_settings(DATA_RESTORE_LOCK_PATH=lock_path):
            with operation_lock(exclusive=True):pass
        return result.testsRun
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    api=None
    try:
        deadline=time.monotonic()+5
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline,'private restore ASGI server failed to start'
            time.sleep(.01)
        base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        django_count=0
        for mode in modes:
            set_mode(mode);django_count+=contracts(base,mode)
        subprocess.run(['go','build','-o',str(work/'restore-server'),'./cmd/server'],cwd=ROOT/'BE_GO',env=tool_env,check=True,timeout=120,stdout=log,stderr=subprocess.STDOUT)
        go_count=0
        for mode in modes:
            set_mode(mode)
            with socket.socket() as listener:listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
            env={**tool_env,'DBTYPE':'POSTGRES','DBNAME':'assozeta_contract_accounts','DBUSER':'contracts','DBPASSWORD':'test-only',
                'DBHOST':str(work),'DBPORT':'5432','REDIS_HOST':host,'REDIS_PORT':redis_port,'REDIS_USERNAME':'default','REDIS_PASSWORD':'synthetic-identity-only',
                'GO_LISTEN_ADDR':f'127.0.0.1:{port}','DEBUG':'False','SECRET_KEY':settings.SECRET_KEY,
                'JWT_SECRET_KEY':settings.JWT_SECRET_KEY,'JWT_PUBLIC_KEY':settings.JWT_PUBLIC_KEY,
                'SOCIAL_AUTH_GOOGLE_OAUTH2_KEY':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_KEY,'SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET':settings.SOCIAL_AUTH_GOOGLE_OAUTH2_SECRET,
                'DATA_RESTORE_LOCK_PATH':configuration(mode),'CORS_ALLOWED_ORIGINS':'https://allowed.example.test'}
            api=subprocess.Popen([str(work/'restore-server')],cwd=work,env=env,stdout=log,stderr=subprocess.STDOUT)
            base=f'http://127.0.0.1:{port}'
            def ready():
                assert api.poll() is None,'Go restore admission server stopped'
                try:return Client(base).request('GET','/readyz')[0]==200
                except OSError:return False
            poll(ready,bool,timeout=30)
            go_count+=contracts(base,mode)
            api.terminate();api.wait(timeout=30);assert api.returncode==0,'restore HTTP fixture did not shut down cleanly'
            api=None
        assert django_count==go_count==42
        set_mode('open');before=snapshot()
        subprocess.run(['go','test','-race','-tags=restoreintegration','./internal/service','-run','^TestRestore(AdmissionPostgres|LockCrossProcess|LockProcessChild)$','-v'],
            cwd=ROOT/'BE_GO',check=True,timeout=90,env={**tool_env,'ASSOZETA_CONTRACT_PYTHON':sys.executable,
                'ASSOZETA_RESTORE_PLAN_OUTPUT':str(out/'query-plan.json'),
                'ASSOZETA_ACCOUNT_TEST_DSN':f'host={work} port=5432 user=contracts dbname=assozeta_contract_accounts sslmode=disable'})
        assert snapshot()==before,'restore SQL/lock integration changed managed models'
        assert raw_cache.get(key)==payload,'restore SQL/lock integration changed session bytes'
        report={'success':True,'contracts_per_backend':go_count,'modes':list(modes),'composition':'actual Django ASGI and compiled Go root',
            'models_unchanged':True,'malformed_session_unchanged':True,'shared_lock_released':True,'cross_process_crash_recovery':True,
            'query_dataset_sizes':[1,16,128,512],'concurrent_admissions':32,'scope':'HTTP admission; not full restore execution or worker/scheduler ownership'}
        (out/'http-results.json').write_text(json.dumps(report,indent=2)+'\n')
        return go_count
    finally:
        server.should_exit=True;thread.join(timeout=10)
        if api is not None and api.poll() is None:
            api.terminate()
            try:api.wait(timeout=30)
            except subprocess.TimeoutExpired:api.kill();api.wait(timeout=5)
        cache.delete(logical);raw_cache.close()
        with disable_auditlog():DataRestore.objects.filter(pk__in=ids).delete()
        assert not thread.is_alive(),'private restore ASGI server did not drain'
        assert snapshot()==original,'restore admission cleanup changed preexisting model state'
