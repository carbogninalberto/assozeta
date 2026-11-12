"""Actual login key functions/decorators around an explicitly controlled terminal.

No credential, account, audit or session behavior is substituted and counted as
verified. This adapter verifies only the request admission boundary.
"""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
from unittest.mock import patch
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def run(work,tool_env,address,password):
    import django
    from django.conf import settings
    from django.core.cache import cache
    from django.core.wsgi import get_wsgi_application
    from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
    from django.test import override_settings
    from django.urls import path
    from django_ratelimit.decorators import ratelimit
    from django_ratelimit.exceptions import Ratelimited
    source=ast.parse((ROOT/'BE/application/views/auth_views.py').read_text())
    names=('_login_ip_key','_login_account_key')
    nodes=[n for n in source.body if isinstance(n,ast.FunctionDef) and n.name in names]
    assert len(nodes)==2
    namespace=dict(json=json,ratelimit=ratelimit)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'auth_views login key functions','exec'),namespace)
    login=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='oauth2_login')
    decorators=[d for d in login.decorator_list if isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='ratelimit']
    assert len(decorators)==2
    def terminal(request):return JsonResponse({'sha256':hashlib.sha256(request.body).hexdigest()})
    wrapped=terminal
    for decorator in reversed(decorators):wrapped=eval(compile(ast.Expression(decorator),'auth_views rate decorator','eval'),namespace)(wrapped)
    def reset(request):cache.clear();return JsonResponse({'reset':True})
    def disabled(request):
        with override_settings(RATELIMIT_ENABLE=False):return wrapped(request)
    url_source=ast.parse((ROOT/'BE/core/urls.py').read_text())
    handler=next(n for n in url_source.body if isinstance(n,ast.FunctionDef) and n.name=='handler403')
    errors=dict(HttpResponse=HttpResponse,HttpResponseForbidden=HttpResponseForbidden,Ratelimited=Ratelimited)
    exec(compile(ast.Module(body=[handler],type_ignores=[]),'core.urls handler403','exec'),errors)
    urls=types.ModuleType('fixture_login_rate_urls')
    urls.urlpatterns=[path('oauth2/login',wrapped),path('__fixtures/login-rate/reset',reset),path('__fixtures/login-rate/disabled',disabled)]
    urls.handler403=errors['handler403'];sys.modules[urls.__name__]=urls
    settings.ROOT_URLCONF=urls.__name__;settings.ALLOWED_HOSTS=['127.0.0.1'];settings.DATA_UPLOAD_MAX_MEMORY_SIZE=262144000
    settings.SECRET_KEY='synthetic-rate-fixture';settings.MIDDLEWARE=[]
    django.setup()
    class QuietHandler(WSGIRequestHandler):
        def log_message(self,*args):pass
    application=get_wsgi_application()
    def buffered_transport(environ,start_response):
        # wsgiref can reset a socket when a denial leaves request bytes unread.
        # Buffer only this fixture's tiny request frames, like a production WSGI
        # transport; the HttpRequest/key/view still read their own body normally.
        length=int(environ.get('CONTENT_LENGTH') or 0)
        assert 0<=length<=16384,'fixture body exceeds transport budget'
        environ['wsgi.input']=io.BytesIO(environ['wsgi.input'].read(length))
        return application(environ,start_response)
    server=make_server('127.0.0.1',0,buffered_transport,handler_class=QuietHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    previous=os.environ.get('TESTS_BASE_URL')
    with patch('django_ratelimit.core.time.time',return_value=1800000000):
        thread.start()
        try:
            os.environ['TESTS_BASE_URL']=f'http://127.0.0.1:{server.server_port}'
            suite=unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_login_rate')
            result=unittest.TextTestRunner(verbosity=2).run(suite)
            assert result.wasSuccessful() and not result.skipped,'Django rate admission contracts failed'
            fixture=work/'admission.json';fixture.write_text(json.dumps(dict(address=address,password=password)));fixture.chmod(0o600)
            subprocess.run(['go','test','-race','-tags=ratelimitintegration','./internal/middleware','-run','^TestLoginRatePortableHTTP$'],cwd=ROOT/'BE_GO',
                env={**tool_env,'ASSOZETA_RATE_ADMISSION_FIXTURE':str(fixture),'ASSOZETA_CONTRACT_PYTHON':sys.executable},check=True,timeout=120)
            return result.testsRun
        finally:
            server.shutdown();server.server_close();thread.join(timeout=5)
            if previous is None:os.environ.pop('TESTS_BASE_URL',None)
            else:os.environ['TESTS_BASE_URL']=previous
