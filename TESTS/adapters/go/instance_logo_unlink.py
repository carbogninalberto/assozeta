"""Real kernel unlink-denial acceptance in an owned, network-isolated container.

Builds only the tagged test binary. No production hooks, credentials, shared tags,
volumes or application services are changed. Persistent seccomp denial necessarily
leaves one empty0600 pathname; destroying the owned tmpfs removes that artifact.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[3]
TAG = 'logoresourceintegration'
TEST = 'TestLogoKernelUnlinkDenialBeforePayload'
PROFILE = {'defaultAction': 'SCMP_ACT_ALLOW', 'syscalls': [
    {'names': ['unlink', 'unlinkat'], 'action': 'SCMP_ACT_ERRNO', 'errnoRet': 1}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Existing immutable sha256 image ID containing Linux runtime')
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', options.image):
        parser.error('--image must be an immutable local image ID')
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    receipt = output/'results.json'
    if receipt.exists():
        parser.error('Use a fresh output directory')
    sources = [*sorted((ROOT/'BE_GO').rglob('*.go')), ROOT/'BE_GO/go.mod', ROOT/'BE_GO/go.sum', Path(__file__).resolve()]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
    report = {'success': False, 'scope': __doc__, 'source_hashes': hashes, 'build_tag': TAG,
              'test': TEST, 'image': options.image, 'private_container_removed': False,
              'private_tmpfs_removed': False, 'source_stable': False}
    started = time.monotonic()
    name = 'az-logo-unlink-'+uuid.uuid4().hex[:12]
    report['owned_container'] = name
    try:
        with (output/'execution.log').open('w') as log, tempfile.TemporaryDirectory(prefix='az-logo-unlink-') as directory:
            work = Path(directory).resolve()
            def docker(*args, check=True):
                return subprocess.run(['docker', *args], check=check, text=True, stdout=subprocess.PIPE,
                                      stderr=log, timeout=120)
            try:
                image = json.loads(docker('image', 'inspect', options.image).stdout)[0]
                assert image['Id'] == options.image and image['Os'] == 'linux'
                architecture = image['Architecture']
                assert architecture in ('arm64', 'amd64'), 'Unsupported fixture image architecture'
                environment = {**os.environ, 'CGO_ENABLED': '0', 'GOOS': 'linux', 'GOARCH': architecture,
                               'GOFLAGS': '', 'GOWORK': 'off'}
                binary = work/'resource.test'
                subprocess.run(['go', 'test', '-c', '-tags', TAG, '-o', str(binary), './pkg/storage/instancelogo'],
                               cwd=ROOT/'BE_GO', env=environment, check=True, timeout=180,
                               stdout=log, stderr=subprocess.STDOUT)
                report['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
                report['architecture'] = architecture
                profile = work/'seccomp.json'
                profile.write_text(json.dumps(PROFILE, indent=2)+'\n')
                report['seccomp_profile'] = PROFILE
                docker('create', '--name', name, '--read-only', '--network', 'none', '--user', '10001:10001',
                       '--security-opt', 'seccomp='+str(profile), '--tmpfs', '/spool:rw,noexec,nosuid,size=8m,uid=10001,gid=10001',
                       '--mount', f'type=bind,src={binary},dst=/resource.test,readonly',
                       '--env', 'ASSOZETA_LOGO_UNLINK_DENIED_TMPFS=/spool', '--entrypoint', '/resource.test',
                       options.image, '-test.v', '-test.run', '^'+TEST+'$', '-test.timeout', '30s')
                configured = json.loads(docker('container', 'inspect', name).stdout)[0]
                host = configured['HostConfig']
                assert host['ReadonlyRootfs'] and host['NetworkMode'] == 'none'
                assert configured['Config']['User'] == '10001:10001'
                assert '/spool' in host['Tmpfs'] and not any(m['Type'] == 'volume' for m in configured['Mounts'])
                assert any(value.startswith('seccomp=') for value in host['SecurityOpt'])
                report['owned_runtime_isolation_verified'] = True
                run = docker('start', '--attach', name, check=False)
                log.write(run.stdout); log.flush()
                terminal = json.loads(docker('container', 'inspect', name).stdout)[0]['State']
                report['exit_code'] = terminal['ExitCode']
                assert not terminal['Running'] and terminal['ExitCode'] == 0, 'Tagged kernel test failed'
                assert '--- PASS: '+TEST in run.stdout and 'HEAD1/GET0' in run.stdout
                assert 'descriptor and admission released' in run.stdout and 'empty0600 pathname' in run.stdout
                report.update(head_requests=1, get_requests=0, descriptors_released=True, admission_released=True,
                              residue={'files': 1, 'bytes': 0, 'mode': '0600',
                                       'reason': 'Persistent kernel unlink denial prevents deleting even the empty pathname'})
            finally:
                docker('rm', '-f', name, check=False)
                remaining = docker('container', 'ls', '-a', '--filter', 'name=^/'+name+'$', '--format', '{{.ID}}')
                report['private_container_removed'] = not remaining.stdout.strip()
                report['private_tmpfs_removed'] = report['private_container_removed']
                assert report['private_container_removed'], 'Owned kernel-test container remains'
        report['source_stable'] = hashes == {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        assert report['source_stable'] and report['private_tmpfs_removed']
        report['success'] = True
    except Exception as error:
        report['failure'] = {'class': type(error).__name__, 'message': str(error)}
        raise
    finally:
        report['elapsed_seconds'] = round(time.monotonic()-started, 3)
        receipt.write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
