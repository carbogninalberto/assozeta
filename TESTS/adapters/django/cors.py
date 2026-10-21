"""Actual Django ASGI CORS middleware and Go over controlled HTTP terminals.

This isolates CORS protocol behavior. It does not substitute a business view or
claim public-route, authentication, restore or other global middleware parity.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from urllib.parse import urlsplit
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))


def main():
    import django
    from django.conf import settings
    from django.core.asgi import get_asgi_application
    from django.http import JsonResponse,StreamingHttpResponse
    from django.test import override_settings
    from django.urls import path
    from corsheaders.defaults import default_headers
    import uvicorn
    out=ROOT/'quality-reports/go-migration/cors';out.mkdir(parents=True,exist_ok=True)
    (out/'http-results.json').write_text(json.dumps({'success':False,'phase':'started'})+'\n')
    source=ast.parse((ROOT/'BE/core/settings.py').read_text())
    nodes=[node for node in source.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CORS_ALLOW_HEADERS' for t in node.targets)]
    assert len(nodes)==1,'CORS headers declaration changed'
    namespace={'default_headers':default_headers}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'core.settings CORS headers','exec'),namespace)
    calls=0
    def terminal(request,code):
        nonlocal calls
        calls+=1
        response=JsonResponse({'method':request.method,'sha256':hashlib.sha256(request.body).hexdigest()},status=code)
        if 'X-Fixture-Vary' in request.headers:response['Vary']=request.headers['X-Fixture-Vary']
        return response
    def stream(request):
        async def chunks():
            yield b'A'
            yield b'B'
        return StreamingHttpResponse(chunks(),content_type='text/plain',headers={'Vary':'Cookie'})
    urls=types.ModuleType('fixture_cors_urls')
    urls.urlpatterns=[path('terminal/<int:code>',terminal),path('stream',stream),path('count',lambda request:JsonResponse({'calls':calls}))]
    sys.modules[urls.__name__]=urls
    settings.configure(SECRET_KEY='synthetic-cors-only',DEBUG=False,ALLOWED_HOSTS=['127.0.0.1'],INSTALLED_APPS=[],
        ROOT_URLCONF=urls.__name__,MIDDLEWARE=['corsheaders.middleware.CorsMiddleware'],CORS_ALLOW_HEADERS=namespace['CORS_ALLOW_HEADERS'])
    django.setup()
    policies=[{'name':'none','all':False,'origins':[]},
              {'name':'allowlist','all':False,'origins':['https://allowed.example.test','https://café.example.test']},
              {'name':'null','all':False,'origins':['null']},
              {'name':'all','all':True,'origins':['https://[invalid']},
              {'name':'invalid','all':False,'origins':['https://allowed.example.test','https://[invalid','null']}]
    counts=[]
    from TESTS.contracts.cors_origins import CASES
    try:urlsplit('https://]host[::1')
    except ValueError:pass
    else:raise AssertionError('CORS oracle requires the bracketed-netloc validation shipped by BE/Dockerfile Python 3.13.2; this interpreter is too old')
    for case in CASES:
        try:
            parsed=urlsplit(case['raw'])
        except ValueError:
            assert not case['valid'], 'declared CORS URL unexpectedly rejected'
        else:
            assert case['valid'] and (parsed.scheme,parsed.netloc)==(case['scheme'],case['netloc']), 'declared CORS URL comparison differs'
    for policy in policies:
        with override_settings(CORS_ORIGIN_ALLOW_ALL=policy['all'],CORS_ALLOWED_ORIGINS=policy['origins']):
            server=uvicorn.Server(uvicorn.Config(get_asgi_application(),host='127.0.0.1',port=0,lifespan='off',log_level='critical'))
            thread=threading.Thread(target=server.run,daemon=True);thread.start()
            try:
                deadline=time.monotonic()+5
                while not server.started:
                    assert thread.is_alive() and time.monotonic()<deadline,'private CORS ASGI server failed to start'
                    time.sleep(.01)
                base=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
                with patch.dict(os.environ,TESTS_BASE_URL=base,TESTS_CORS_POLICY=policy['name']):
                    test_class='CORSInvalidConfigurationContracts' if policy['name']=='invalid' else 'CORSContracts'
                    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName('TESTS.contracts.test_cors.'+test_class))
                    assert result.wasSuccessful() and not result.skipped,'Django CORS contracts failed: '+policy['name']
                    counts.append(result.testsRun)
            finally:
                server.should_exit=True;thread.join(timeout=10)
                assert not thread.is_alive(),'private CORS ASGI server did not drain'
    assert counts==[12]*4+[3],'CORS fixture coverage differs'
    tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
    with tempfile.TemporaryDirectory(prefix='az-cors-') as directory:
        fixture=Path(directory)/'policies.json';fixture.write_text(json.dumps({'policies':policies,'urls':CASES}));fixture.chmod(0o600)
        subprocess.run(['go','test','-race','-tags=corsintegration','./internal/middleware','-run','^TestCORSPortableHTTP$','-v'],
            cwd=ROOT/'BE_GO',env={**tool_env,'ASSOZETA_CORS_FIXTURE':str(fixture),'ASSOZETA_CONTRACT_PYTHON':sys.executable},check=True,timeout=120)
    report={'success':True,'contracts_per_backend':sum(counts),'policies':5,'url_cases':len(CASES),'python':sys.version.split()[0],'scope':'CORS middleware with controlled terminals; no public-route/global middleware claim','database_access':'none'}
    (out/'http-results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)


if __name__=='__main__':main()
