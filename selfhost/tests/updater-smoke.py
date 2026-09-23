"""Exercise real controller provisioning, auth and persistence in disposable Docker containers."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def run(image):
    temporary = Path(tempfile.mkdtemp(prefix='assozeta-updater-smoke-')).resolve()
    project = f'assozeta-updater-smoke-{uuid4().hex[:10]}'
    env_file = temporary / '.env'
    env_file.write_text(f'COMPOSE_PROJECT_NAME={project}\nASSOZETA_VERSION=1.0.1\nASSOZETA_UPDATER_REF={image}\nCUSTOM_SETTING=preserved\n')
    with env_file.open('a') as output:
        for name in ('backend', 'web', 'renderer'):
            output.write(f'ASSOZETA_{name.upper()}_IMAGE=ghcr.io/carbogninalberto/assozeta-{name}\n')
    shutil.copy(ROOT / 'selfhost/compose.updater.yml', temporary)
    base_command = ['docker', 'run', '--rm', '-v', f'{temporary}:{temporary}', '-v', '/var/run/docker.sock:/var/run/docker.sock']
    command = base_command + [image]
    api_command = base_command + ['-v', f'{project}_updater_api:/run/assozeta-updater', image]
    compose = ['docker', 'compose', '--env-file', str(env_file), '-f', str(temporary / 'compose.updater.yml'), '--project-name', f'{project}-updater']
    environment = {**os.environ, 'ASSOZETA_SELFHOST_DIR': str(temporary), 'ASSOZETA_ENV_FILE': str(env_file),
                   'ASSOZETA_UPDATER_API_VOLUME': f'{project}_updater_api', 'ASSOZETA_UPDATER_STATUS_VOLUME': f'{project}_updater_status'}
    status_code = '''
import http.client, socket, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
connection = http.client.HTTPConnection('localhost', timeout=3)
connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.sock.settimeout(3)
connection.sock.connect('/run/assozeta-updater/runner.sock')
token = pathlib.Path('/run/assozeta-updater/token').read_text().strip() if sys.argv[2] == 'valid' else 'invalid'
payload = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else None
path = sys.argv[4] if len(sys.argv) > 4 else '/status'
connection.request('POST' if payload else 'GET', '/updates' if payload else path, body=payload,
                   headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
response = connection.getresponse()
print(json.dumps({'code': response.status, 'data': json.loads(response.read())}))
connection.close()
'''
    def status(token='valid', request=None, path='/status'):
        arguments = [json.dumps(request) if request else '', path]
        return json.loads(subprocess.check_output(api_command + ['-c', status_code, str(temporary), token, *arguments], text=True, stderr=subprocess.PIPE))

    try:
        subprocess.run(command + ['/runner/provision.py', str(temporary), str(env_file)], check=True)
        for attempt in range(30):
            try:
                first = status()
                break
            except subprocess.CalledProcessError as exc:
                last_error = exc.stderr
                time.sleep(1)
        else:
            subprocess.run(compose + ['logs', '--tail=80'], env=environment, check=False)
            print(last_error)
            raise AssertionError('Runner did not become available')
        assert first['code'] == 200 and first['data']['available'] and first['data']['protocol'] == 1
        assert first['data']['can_update']
        container = subprocess.check_output(compose + ['ps', '-q', 'updater'], env=environment, text=True).strip()
        actual_project = subprocess.check_output(['docker', 'inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}', container], text=True).strip()
        assert actual_project == f'{project}-updater'
        assert status('invalid')['code'] == 403
        assert status('invalid', path='/diagnostics')['code'] == 403
        backup = status(path='/diagnostics')
        assert backup['code'] == 200 and backup['data']['backup']['status'] == 'warning'
        assert str(temporary) not in json.dumps(backup)
        # The web-facing volume contains only the status socket. Possessing it
        # neither reveals the private runner token nor allows update requests.
        status_only = ['docker', 'run', '--rm', '-v', f'{project}_updater_status:/run/assozeta-update-status:ro', image]
        subprocess.run(status_only + ['-c', '''
import http.client,json,pathlib,socket
assert not pathlib.Path('/run/assozeta-updater/token').exists()
assert not pathlib.Path('/var/run/docker.sock').exists()
assert [p.name for p in pathlib.Path('/run/assozeta-update-status').iterdir()] == ['status.sock']
for path, method, expected in [('/diagnostics','GET',405),('/updates','POST',404),('/status','GET',405),('/instance-update-status','POST',403)]:
    connection=http.client.HTTPConnection('localhost',timeout=5)
    connection.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    connection.sock.connect('/run/assozeta-update-status/status.sock')
    connection.request(method,path,body='{}',headers={'Content-Type':'application/json'})
    response=connection.getresponse()
    assert response.status == expected, response.status
    assert 'history' not in json.loads(response.read())
    connection.close()
'''], check=True)
        pending = temporary / '.updater/pending-distribution'
        pending.mkdir()
        recovering = status()['data']
        assert recovering['available'] and not recovering['can_update'] and recovering['reason']
        rejected = status(request={'release_id': 2, 'tag': 'v1.0.2', 'source_version': '1.0.1',
                                   'request_id': str(uuid4()), 'actor_id': str(uuid4())})
        assert rejected['code'] == 409
        assert status()['data']['active'] is None
        pending.rmdir()
        canonical = env_file.read_text()
        env_file.write_text(canonical.replace('ASSOZETA_WEB_IMAGE=ghcr.io/carbogninalberto/assozeta-web', 'ASSOZETA_WEB_IMAGE=custom'))
        assert not status()['data']['can_update']
        assert 'ASSOZETA_WEB_IMAGE' in status()['data']['reason']
        assert status(request={'release_id': 2, 'tag': 'v1.0.2', 'source_version': '1.0.1',
                               'request_id': str(uuid4()), 'actor_id': str(uuid4())})['code'] == 409
        env_file.write_text(canonical)
        # A caught post-migration error has no interrupted_at and no pending
        # transaction in the incident. It must still block status and requests.
        failed_request = {'release_id': 2, 'tag': 'v1.0.2', 'source_version': '1.0.1',
                          'request_id': str(uuid4()), 'actor_id': str(uuid4())}
        subprocess.run(command + ['-c', '''
import json,sys
from journal import Journal
journal=Journal(sys.argv[1]+'/.updater/operations.sqlite3')
# Create the record directly in its terminal state to avoid racing the worker.
record={**json.loads(sys.argv[2]),'id':sys.argv[3],'status':'recovery_required','stage':'recovery_required'}
with journal.connect() as db:
    db.execute('INSERT INTO operations VALUES (?, ?, ?, ?)',(record['id'],record['request_id'],record['status'],json.dumps(record)))
''', str(temporary), json.dumps(failed_request), str(uuid4())], check=True)
        assert not status()['data']['can_update']
        assert 'ripristino' in status()['data']['reason']
        assert status(request={**failed_request, 'request_id': str(uuid4())})['code'] == 409
        assert status(request=failed_request)['data']['operation']['status'] == 'recovery_required'
        fingerprint_code = "import hashlib,pathlib; print(hashlib.sha256(pathlib.Path('/run/assozeta-updater/token').read_bytes()).hexdigest())"
        before = subprocess.check_output(api_command + ['-c', fingerprint_code])
        subprocess.run(command + ['/runner/provision.py', str(temporary), str(env_file)], check=True)
        after = subprocess.check_output(api_command + ['-c', fingerprint_code])
        assert before == after
        assert json.loads((temporary / '.updater/provisioning.json').read_text())['schema_version'] == 2
        assert 'CUSTOM_SETTING=preserved' in env_file.read_text()
        subprocess.run(compose + ['restart'], env=environment, check=True)
        for attempt in range(30):
            try:
                assert status()['data']['available']
                break
            except (subprocess.CalledProcessError, AssertionError):
                time.sleep(1)
        else:
            raise AssertionError('Runner did not recover after restart')
        (temporary / 'bin').mkdir()
        shutil.copy(ROOT / 'selfhost/bin/assozeta', temporary / 'bin/assozeta')
        survivor = subprocess.check_output(['docker', 'run', '--rm', '--detach',
            '--label', f'org.assozeta.lifecycle.installation={temporary}', image,
            '-c', 'import time; time.sleep(120)'], text=True).strip()
        try:
            subprocess.run(command + ['-c', '''
import os,pathlib,subprocess,sys
root=pathlib.Path(sys.argv[1])
result=subprocess.run([str(root/'bin/assozeta'),'reconcile-updates'],capture_output=True,text=True,
    env={**os.environ,'ASSOZETA_INSTALL_ROOT':str(root),'ASSOZETA_ENV_FILE':str(root/'.env')})
assert result.returncode != 0
assert 'a lifecycle helper is still running' in result.stderr, result.stderr
assert sys.argv[2][:12] in result.stderr
''', str(temporary), survivor], check=True)
        finally:
            subprocess.run(['docker', 'rm', '--force', survivor], check=False, stdout=subprocess.DEVNULL)
        print('PASS: real runner provisioning, project isolation, private socket authentication, isolated read-only status connection, recovery gate, idempotent credentials, preserved configuration, restart readiness, and refusal to overlap a surviving standalone helper.')
    finally:
        subprocess.run(compose + ['down', '--volumes'], env=environment, check=False)
        subprocess.run(['docker', 'volume', 'rm', f'{project}_updater_api'], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(['docker', 'volume', 'rm', f'{project}_updater_status'], check=False, stdout=subprocess.DEVNULL)
        subprocess.run(command + ['-c', 'import shutil,sys; shutil.rmtree(sys.argv[1])', str(temporary / '.updater')], check=False)
        shutil.rmtree(temporary)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', default='assozeta-updater:goal-test')
    run(parser.parse_args().image)
