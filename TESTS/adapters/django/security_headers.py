"""Actual Django security middleware, followed by the same Go HTTP contracts."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def main():
    import django
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.http import JsonResponse,StreamingHttpResponse
    from django.urls import path
    import uvicorn
    from TESTS.contracts.test_security_headers import SECURITY_HEADERS
    out=ROOT/'quality-reports/go-migration/security-headers';out.mkdir(parents=True,exist_ok=True)
    (out/'results.json').write_text(json.dumps({'success':False,'phase':'started'})+'\n')
    # No current production assignment overrides these Django policy defaults.
    source=ast.parse((ROOT/'BE/core/settings.py').read_text())
    assignments={target.id for node in source.body if isinstance(node,ast.Assign) for target in node.targets if isinstance(target,ast.Name)}
    policy={'SECURE_HSTS_SECONDS','SECURE_HSTS_INCLUDE_SUBDOMAINS','SECURE_HSTS_PRELOAD','SECURE_CONTENT_TYPE_NOSNIFF',
        'SECURE_SSL_REDIRECT','SECURE_SSL_HOST','SECURE_REDIRECT_EXEMPT','SECURE_REFERRER_POLICY','SECURE_CROSS_ORIGIN_OPENER_POLICY'}
    assert not policy & assignments,'production security policy changed; characterize the new assignments'
    def terminal(request,code):
        response=JsonResponse({'method':request.method},status=code)
        if 'X-Fixture-Security' in request.headers:
            for key in SECURITY_HEADERS:response[key]=request.headers['X-Fixture-Security']
        return response
    def stream(request):
        async def chunks():
            yield b'A'
            yield b'B'
        return StreamingHttpResponse(chunks(),content_type='text/plain')
    urls=types.ModuleType('fixture_security_urls')
    urls.urlpatterns=[path('terminal/<int:code>',terminal),path('stream',stream)]
    sys.modules[urls.__name__]=urls
    settings.configure(SECRET_KEY='synthetic-security-only',DEBUG=False,ALLOWED_HOSTS=['127.0.0.1'],INSTALLED_APPS=[],
        ROOT_URLCONF=urls.__name__,SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https'),CORS_ORIGIN_ALLOW_ALL=True,
        MIDDLEWARE=['corsheaders.middleware.CorsMiddleware','django.middleware.security.SecurityMiddleware'])
    django.setup()
    server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline,'private security ASGI server failed to start'
            time.sleep(.01)
        base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        with patch.dict(os.environ,TESTS_BASE_URL=base):
            result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_security_headers'))
            assert result.wasSuccessful() and not result.skipped and result.testsRun==6,'Django security contracts failed'
    finally:
        server.should_exit=True;thread.join(timeout=10)
        assert not thread.is_alive(),'private security ASGI server did not drain'
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    subprocess.run(['go','test','-race','-tags=securityheadersintegration','./internal/middleware','-run','^TestSecurityHeadersPortableHTTP$','-v'],
        cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_CONTRACT_PYTHON':sys.executable},check=True,timeout=120)
    report={'success':True,'contracts_per_backend':6,'scope':'current security headers around controlled terminals; not full application middleware parity','database_access':'none'}
    (out/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
