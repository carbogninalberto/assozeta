"""Private Redis interoperability for the two existing login rate counters.

Uses unchanged CACHES settings and the installed django-ratelimit implementation.
This is counter/admission evidence, not public login or request-key parsing parity.
"""
import ast
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def main():
    import redis
    from django.conf import settings
    from django.core.cache import cache
    from django_ratelimit.core import get_usage
    output = ROOT / 'quality-reports/go-migration/login-rate'
    output.mkdir(parents=True, exist_ok=True)
    (output/'results.json').write_text('{"success":false}\n')
    # Explicit end-of-window values include the inclusive boundary. The expected
    # hash uses the declared protocol string; it is not copied from Django output.
    declarations = [
        ('new IP', 'login-ip', 'new-ip', 0, 28, 0),
        ('new account', 'login-account', 'new-account', 0, 11, 0),
        ('IP threshold', 'login-ip', '203.0.113.7', 0, 14, 19),
        ('IP denied', 'login-ip', 'above-ip', 0, 32, 20),
        ('account threshold', 'login-account', 'member@example.test', 0, 22, 4),
        ('account denied', 'login-account', 'above-account', 0, 37, 5),
        ('IPv6', 'login-ip', '2001:db8::7', 0, 7, 1),
        ('Unicode', 'login-account', 'straße@example.test', 0, 33, 2),
        ('missing username', 'login-account', 'missing-username', 0, 47, 0),
        ('invalid JSON', 'login-account', 'invalid-request', 0, 31, 4),
        ('empty address', 'login-ip', '', 0, 0, 0),
        ('before boundary', 'login-account', 'boundary-fixture', 17, 18, 0),
        ('at boundary', 'login-account', 'boundary-fixture', 78, 78, 0),
        ('after boundary', 'login-account', 'boundary-fixture', 139, 198, 0),
    ]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
    password = 'synthetic-rate-only'
    with tempfile.TemporaryDirectory(prefix='az-rate-') as directory, (output/'redis.log').open('w') as log:
        work = Path(directory)
        server = subprocess.Popen(['redis-server', '--bind', '127.0.0.1', '--port', str(port),
            '--save', '', '--appendonly', 'no', '--requirepass', password, '--dir', str(work)], stdout=log, stderr=subprocess.STDOUT)
        client = redis.Redis(host='127.0.0.1', port=port, password=password, db=1)
        try:
            deadline = time.monotonic()+10
            while True:
                try:
                    if client.ping(): break
                except redis.ConnectionError:
                    if time.monotonic()>deadline: raise
                    time.sleep(.05)
            source = ast.parse((ROOT/'BE/core/settings.py').read_text())
            nodes = [n for n in source.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id=='CACHES' for t in n.targets)]
            assert len(nodes)==1
            namespace = dict(REDIS_PROTOCOL='redis', REDIS_HOST='127.0.0.1', REDIS_PORT=port, REDIS_PASSWORD=password)
            exec(compile(ast.Module(body=nodes,type_ignores=[]),'core.settings CACHES','exec'),namespace)
            settings.configure(CACHES=namespace['CACHES'], RATELIMIT_ENABLE=True)
            # Guard the application decorator specification against source drift.
            login = next(n for n in ast.parse((ROOT/'BE/application/views/auth_views.py').read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='oauth2_login')
            rates = [{kw.arg:ast.literal_eval(kw.value) for kw in d.keywords if kw.arg!='key'} for d in login.decorator_list if isinstance(d,ast.Call) and isinstance(d.func,ast.Name) and d.func.id=='ratelimit']
            assert rates==[dict(group='login-ip',rate='20/m',method='POST',block=True),dict(group='login-account',rate='5/m',method='POST',block=True)]
            cases=[]
            request = type('Request',(),{'method':'POST'})()
            def usage(case):
                with patch('django_ratelimit.core.time.time',return_value=case['now']):
                    return get_usage(request,group=case['group'],key=lambda *_:case['value'],rate=f"{case['limit']}/m",method='POST',increment=True)
            for name, group, value, offset, window, seed in declarations:
                now=1800000000+offset; end=1800000000+window
                limit=20 if group=='login-ip' else 5
                logical='rl:'+hashlib.sha256(f'{group}{limit}/60s{value}{end}POST'.encode()).hexdigest()
                case=dict(name=name,group=group,value=value,now=now,key=':1:'+logical,logical=logical,limit=limit,count=seed+1,blocked=seed+1>limit,existing=seed>0)
                if seed: cache.set(logical,seed,65)
                result=usage(case)
                assert result==dict(count=seed+1,limit=limit,should_limit=seed+1>limit,time_left=end-now),(name,'Django declared admission differs')
                assert client.get(case['key'])==str(seed+1).encode(),(name,'Django Redis key/encoding differs')
                cache.delete(logical)
                if seed:
                    cache.set(logical,seed,65)
                    client.pexpire(case['key'],50000)
                cases.append(case)
            print(f'Django: {len(cases)} declared rate counter cases passed',flush=True)
            # The actual library fails closed if a counter expires between add
            # and incr. Preserve that two-command race in the Go implementation.
            race=dict(group='login-account',value='expiry-race',now=1800000000,limit=5)
            assert usage(race)['count']==1
            original_add=cache.add
            def expired_add(key,*args,**kwargs):
                added=original_add(key,*args,**kwargs)
                assert not added
                cache.delete(key)
                return added
            with patch.object(cache,'add',expired_add):
                assert usage(race)==dict(count=0,limit=0,should_limit=True,time_left=-1)
            fixture=work/'cases.json';fixture.write_text(json.dumps(dict(address=f'127.0.0.1:{port}',password=password,cases=cases)));fixture.chmod(0o600)
            tool_env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','GOCACHE','GOMODCACHE','GOTOOLCHAIN')}
            binary=work/'rate-tests'
            subprocess.run(['go','test','-c','-race','-tags=ratelimitintegration','-o',str(binary),'./internal/repository'],cwd=ROOT/'BE_GO',env=tool_env,check=True,timeout=120)
            # Compilation duration must not expire the deliberately shortened TTL.
            for case in cases:
                cache.delete(case['logical'])
                if case['existing']:
                    cache.set(case['logical'],case['count']-1,50)
            subprocess.run([str(binary),'-test.run=^TestDjangoLoginRateInterop$','-test.timeout=60s'],env={**tool_env,'ASSOZETA_RATE_FIXTURE':str(fixture)},check=True,timeout=90)
            for case in cases:
                result=usage(case)
                assert result['count']==case['count']+1 and result['should_limit']==(case['count']+1>case['limit']),(case['name'],'Django cannot continue Go-written counter')
            concurrent='rl:'+hashlib.sha256(b'login-account5/60sconcurrent-fixture1800000005POST').hexdigest()
            assert cache.get(concurrent)==64,'concurrent increments lost'
            assert client.dbsize()==16,'unexpected cache keys or cancelled write'
            report=dict(success=True,cases=len(cases),concurrent_requests=64,admitted=5,denied=59,
                        expiry_race='fail closed without recreating key',bounded_pool=4,deadline_and_reuse=True,
                        schema='actual Django Redis cache database 1',public_login=False,customer_data='none')
            (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
        finally:
            cache.close();client.close();server.terminate()
            try:server.wait(timeout=10)
            except subprocess.TimeoutExpired:server.kill();server.wait(timeout=5)


if __name__=='__main__':main()
