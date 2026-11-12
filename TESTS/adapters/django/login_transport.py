"""Same portable pre-credential HTTP contracts against Django and Go transport."""
import os
import subprocess
import threading
import unittest
from wsgiref.simple_server import make_server,WSGIRequestHandler
from .accounts import ROOT


def run(work,log,tool_env):
    from django.apps import apps
    from django.core.wsgi import get_wsgi_application
    def snapshot():
        return {m._meta.label:list(m._base_manager.order_by('pk').values()) for m in apps.get_models() if m._meta.managed and not m._meta.proxy}
    before=snapshot()
    class QuietHandler(WSGIRequestHandler):
        def log_message(self,*args):pass
    server=make_server('127.0.0.1',0,get_wsgi_application(),handler_class=QuietHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    prior=os.environ.get('TESTS_BASE_URL')
    try:
        os.environ['TESTS_BASE_URL']=f'http://127.0.0.1:{server.server_port}'
        suite=unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_login_transport')
        result=unittest.TextTestRunner(verbosity=2).run(suite)
        assert result.wasSuccessful() and not result.skipped,'Django login transport contracts failed'
        subprocess.run(['go','test','-race','-tags=logintransportintegration','./internal/handler','-run','^TestLoginTransportPortableHTTP$'],
            cwd=ROOT/'BE_GO',check=True,timeout=180,env={**tool_env,'ASSOZETA_CONTRACT_PYTHON':os.sys.executable})
        assert snapshot()==before,'pre-credential requests changed managed tables'
        return result.testsRun
    finally:
        server.shutdown();server.server_close();thread.join(timeout=5)
        if prior is None:os.environ.pop('TESTS_BASE_URL',None)
        else:os.environ['TESTS_BASE_URL']=prior
