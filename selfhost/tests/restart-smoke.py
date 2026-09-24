"""Real Docker restart of a disposable installation using lightweight services.

Build the current runner first:
  docker build -t assozeta-updater:restart-test -f selfhost/updater/Dockerfile .
Then run this script. No existing installation is used or changed.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from uuid import uuid4
from quality_support import docker_host

ROOT = Path(__file__).resolve().parents[2]


def run(image='assozeta-updater:restart-test'):
    root = Path(tempfile.mkdtemp(prefix='assozeta-restart-smoke-')).resolve()
    project = f'assozeta-restart-test-{uuid4().hex[:10]}'
    env_file = root / '.env'
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env_file.write_text(f'COMPOSE_PROJECT_NAME={project}\nASSOZETA_VERSION=edge\nASSOZETA_UPDATER_REF={image}\nAPP_URL=http://{docker_host()}:{port}\n')
    environment = {**os.environ, 'ASSOZETA_SELFHOST_DIR': str(root), 'ASSOZETA_ENV_FILE': str(env_file),
                   'ASSOZETA_UPDATER_API_VOLUME': f'{project}_updater_api', 'ASSOZETA_UPDATER_STATUS_VOLUME': f'{project}_updater_status'}
    services = {}
    for name in ('postgres', 'redis', 'minio', 'renderer', 'api', 'worker', 'beat', 'web'):
        services[name] = {'image': image, 'init': True, 'command': ['-u', '-c', '''
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
Path('/data/preserved').touch(exist_ok=True)
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ready')
    def log_message(self, *args): pass
HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
'''], 'volumes': [f'{name}_data:/data'],
                          'healthcheck': {'test': ['CMD', 'curl', '--fail', '--silent', 'http://127.0.0.1:8080/healthz'], 'interval': '1s', 'timeout': '2s', 'retries': 10}}
    services['api']['depends_on'] = {name: {'condition': 'service_healthy'} for name in ('postgres', 'redis', 'minio', 'renderer')}
    services['worker']['depends_on'] = {'api': {'condition': 'service_healthy'}}
    services['beat']['depends_on'] = {'api': {'condition': 'service_healthy'}}
    services['web']['depends_on'] = {'api': {'condition': 'service_healthy'}}
    services['web']['ports'] = [f'{port}:8080']
    # A stopped one-shot migration container must never be executed again.
    services['migrate'] = {'image': image, 'profiles': ['tools'], 'command': ['-c', "from pathlib import Path; p=Path('/data/migrations'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"], 'volumes': ['api_data:/data']}
    (root / 'compose.yml').write_text(json.dumps({'name': project, 'services': services, 'volumes': {f'{name}_data': {} for name in services if name != 'migrate'}}))
    shutil.copy(ROOT / 'selfhost/compose.updater.yml', root)
    compose = ['docker', 'compose', '--env-file', str(env_file), '-f', str(root / 'compose.yml')]
    updater_compose = ['docker', 'compose', '--env-file', str(env_file), '-f', str(root / 'compose.updater.yml'), '--project-name', f'{project}-updater']
    base = ['docker', 'run', '--rm', '-v', f'{root}:{root}', '-v', '/var/run/docker.sock:/var/run/docker.sock']
    api = base + ['-v', f'{project}_updater_api:/run/assozeta-updater', image]
    socket_client = '''
import http.client, socket, json, pathlib, sys
conn=http.client.HTTPConnection('localhost',timeout=5)
conn.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); conn.sock.settimeout(5)
conn.sock.connect('/run/assozeta-updater/runner.sock')
token=pathlib.Path('/run/assozeta-updater/token').read_text().strip() if sys.argv[3]=='valid' else 'invalid'
conn.request('POST' if sys.argv[2] else 'GET',sys.argv[1],body=sys.argv[2] or None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
response=conn.getresponse(); print(json.dumps({'code':response.status,'data':json.loads(response.read())}))
'''

    def command(args):
        return subprocess.check_output(args, env=environment, text=True, stderr=subprocess.PIPE, timeout=90).strip()

    def status(path='/status', payload=None, token='valid'):
        return json.loads(command(api + ['-c', socket_client, path, json.dumps(payload) if payload else '', token]))

    def inspect(ids):
        return {item['Id']: item for item in json.loads(command(['docker', 'inspect', *ids]))}

    unrelated = None
    try:
        command(compose + ['up', '-d', '--wait'])
        command(compose + ['--profile', 'tools', 'up', '--no-deps', 'migrate'])
        command(base + [image, '/runner/provision.py', str(root), str(env_file)])
        for _ in range(30):
            try:
                assert status()['data']['can_restart']
                break
            except (subprocess.CalledProcessError, KeyError):
                time.sleep(1)
        else:
            raise AssertionError('Runner unavailable')
        unrelated = command(['docker', 'run', '--detach', '--rm', image, '-c', 'import time; time.sleep(600)'])
        ids = command(compose + ['ps', '--all', '-q']).split() + command(updater_compose + ['ps', '-q']).split()
        before = inspect(ids + [unrelated])
        api_id = command(compose + ['ps', '-q', 'api'])
        command(['docker', 'exec', api_id, 'sh', '-c', 'echo retained-session-and-data > /data/preserved'])
        request = {'request_id': str(uuid4()), 'actor_id': str(uuid4())}
        assert status('/restarts', request, token='invalid')['code'] == 403
        accepted = status('/restarts', request)
        assert accepted['code'] == 202, accepted
        operation_id = accepted['data']['operation']['id']
        assert status('/restarts', request)['data']['operation']['id'] == operation_id
        assert status('/restarts', {**request, 'request_id': str(uuid4())})['code'] == 409
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                state = status()['data']
                operation = next((item for item in state['history'] if item['id'] == operation_id), None)
                if operation:
                    assert operation['status'] == 'succeeded', operation
                    assert operation['verified_at']
                    break
            except subprocess.CalledProcessError:
                pass  # Expected while the updater itself restarts.
            time.sleep(1)
        else:
            raise AssertionError('Restart timed out')
        after = inspect(ids + [unrelated])
        restarted = []
        for identity, previous in before.items():
            current = after[identity]
            assert current['Image'] == previous['Image']
            service = previous['Config'].get('Labels', {}).get('com.docker.compose.service')
            if identity == unrelated or service == 'migrate':
                assert current['State']['StartedAt'] == previous['State']['StartedAt']
            else:
                assert current['State']['StartedAt'] != previous['State']['StartedAt'], service
                assert current['State']['Running'], service
                assert current['State'].get('Health', {}).get('Status', 'healthy') == 'healthy', service
                restarted.append(service)
        assert set(restarted) == {'postgres', 'redis', 'minio', 'renderer', 'api', 'worker', 'beat', 'web', 'updater'}
        assert command(['docker', 'exec', api_id, 'cat', '/data/preserved']) == 'retained-session-and-data'
        assert command(['docker', 'exec', api_id, 'cat', '/data/migrations']) == 'x'
        assert status('/restarts', request)['data']['operation']['id'] == operation_id
        assert len(status()['data']['history']) == 1
        print(json.dumps({'restarted': restarted, 'images_and_container_ids_preserved': True,
                          'persistent_data_preserved': True, 'one_shot_and_unrelated_containers_untouched': True,
                          'idempotent_operation': operation_id}, indent=2))
    except Exception:
        for log in (root / '.updater/logs').glob('*.log'):
            print(log.read_text())
        raise
    finally:
        # Only containers/volumes created by this disposable test are removed.
        helpers = command(['docker', 'ps', '-aq', '--filter', f'label=org.assozeta.lifecycle.installation={root}']).split()
        if helpers:
            command(['docker', 'rm', '-f', *helpers])
        subprocess.run(updater_compose + ['down', '-v', '--timeout', '5'], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(compose + ['--profile', 'tools', 'down', '-v', '--timeout', '5'], env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if unrelated:
            subprocess.run(['docker', 'rm', '-f', unrelated], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(['docker', 'volume', 'rm', f'{project}_updater_api', f'{project}_updater_status'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(root)


if __name__ == '__main__':
    run()
