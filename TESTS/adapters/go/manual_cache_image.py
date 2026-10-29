"""Verify shared manual-cache permissions in actual Django and Go images.

Two disposable named volumes exercise both initialization orders, with the
normal Compose UID and a read-only root filesystem. Shell filesystem probes
verify read/write/rename/delete permissions, not application refresh behavior;
the separate manual_refresh_lock adapter verifies that protocol and its lease.
No image is built, pulled or retagged; no application or network is started.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
LABEL = 'org.assozeta.manual-cache-proof'
SOURCES = ('BE_GO/Dockerfile', 'BE/Dockerfile', 'selfhost/compose.yml',
           'TESTS/adapters/go/manual_cache_image.py')

PROBE = r'''set -eu
test "$(id -u):$(id -g)" = 10001:10001
test "$(stat -c %u:%g /app/manuale)" = 10001:10001
test ! -w /app/release-manual
test ! -w /app/VERSION
if (touch /app/cache-root-probe) 2>/dev/null; then exit 41; fi
if (touch /app/release-manual/cache-bundle-probe) 2>/dev/null; then exit 42; fi
cd /app/manuale
case "$1" in
  first)
    test ! -e index.json
    mkdir assets
    printf first > index.json
    printf fixture > assets/fixture.png
    : > .sync.lock
    chmod 600 index.json assets/fixture.png .sync.lock
    ;;
  second)
    test "$(cat index.json)" = first
    test "$(cat assets/fixture.png)" = fixture
    test -w .sync.lock
    printf second > index.json.tmp
    chmod 600 index.json.tmp
    mv index.json.tmp index.json
    rm assets/fixture.png
    printf replacement > assets/replacement.png
    ;;
  verify)
    test "$(cat index.json)" = second
    test ! -e assets/fixture.png
    test "$(cat assets/replacement.png)" = replacement
    test -w .sync.lock
    rm index.json assets/replacement.png .sync.lock
    rmdir assets
    ;;
  *) exit 43 ;;
esac
printf 'passed:%s\n' "$1"
'''


def docker(*args, check=True):
    result = subprocess.run(['docker', *args], text=True, capture_output=True, timeout=60)
    if check and result.returncode:
        raise AssertionError(f'Docker {args[0]} failed: {result.stderr.strip()}')
    return result


def hashes():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--go-image', required=True)
    parser.add_argument('--django-image', required=True)
    parser.add_argument('--output', type=Path,
                        default=ROOT/'quality-reports/go-migration/manual-cache-image/results.json')
    options = parser.parse_args()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    identifier = 'az-manual-cache-' + uuid4().hex
    report = {'success': False, 'scope': __doc__, 'source_hashes': hashes(),
              'images': {}, 'orders': {}, 'identifier': identifier,
              'provenance_limit': 'Source hashes describe reviewed local inputs; image IDs identify the tested artifacts, not a source-to-image attestation.'}
    containers, volumes = [], []
    try:
        for backend, requested in (('go', options.go_image), ('django', options.django_image)):
            metadata = json.loads(docker('image', 'inspect', requested).stdout)[0]
            if backend == 'go':
                assert metadata['Config']['User'] == '10001:10001'
            assert not metadata['Config'].get('Volumes'), 'Unexpected implicit volume'
            report['images'][backend] = {'id': metadata['Id'], 'requested': requested,
                                         'configured_user': metadata['Config']['User']}
        for order in (('go', 'django'), ('django', 'go')):
            key = '-then-'.join(order)
            volume = identifier+'-'+key
            volumes.append(volume)
            docker('volume', 'create', '--label', LABEL+'='+identifier, volume)
            report['orders'][key] = []
            for phase, backend in (('first', order[0]), ('second', order[1]), ('verify', order[0])):
                name = volume+'-'+phase
                containers.append(name)
                image = report['images'][backend]['id']
                docker('create', '--pull', 'never', '--name', name, '--label', LABEL+'='+identifier,
                       '--read-only', '--network', 'none', '--cap-drop', 'ALL',
                       '--security-opt', 'no-new-privileges', '--no-healthcheck',
                       '--user', '10001:10001', '--mount', f'type=volume,source={volume},target=/app/manuale',
                       '--entrypoint', '/bin/sh', image, '-ec', PROBE, 'cache-proof', phase)
                info = json.loads(docker('inspect', name).stdout)[0]
                assert info['Image'] == image and info['Config']['User'] == '10001:10001'
                assert info['HostConfig']['ReadonlyRootfs'] is True
                assert info['HostConfig']['NetworkMode'] == 'none'
                assert len(info['Mounts']) == 1
                mount = info['Mounts'][0]
                assert mount['Name'] == volume and mount['Destination'] == '/app/manuale' and mount['RW']
                result = docker('start', '--attach', name, check=False)
                state = json.loads(docker('inspect', name).stdout)[0]['State']
                observation = {'backend': backend, 'phase': phase, 'exit_code': state['ExitCode'],
                               'stdout': result.stdout, 'stderr': result.stderr}
                report['orders'][key].append(observation)
                assert not state['Running'] and not state.get('OOMKilled') and not state.get('Error')
                assert result.returncode == 0 and state['ExitCode'] == 0, f'Cache permission probe failed: {key}/{phase}'
                assert result.stdout.strip() == 'passed:'+phase
            print('PASS shared manual cache: '+key, flush=True)
        assert hashes() == report['source_hashes'], 'Reviewed source changed during proof'
        report['success'] = True
    finally:
        failures = []
        for kind, names in (('container', containers), ('volume', volumes)):
            for name in names:
                found = docker(kind, 'inspect', name, check=False)
                if found.returncode:
                    continue
                metadata = json.loads(found.stdout)[0]
                labels = metadata.get('Config', {}).get('Labels') if kind == 'container' else metadata.get('Labels')
                if not labels or labels.get(LABEL) != identifier:
                    failures.append('Resource ownership mismatch')
                elif docker(kind, 'rm', *(['--force'] if kind == 'container' else []), name, check=False).returncode:
                    failures.append('Resource removal failed')
        for kind in ('container', 'volume'):
            remaining = docker(kind, 'ls', *(['--all'] if kind == 'container' else []),
                               '--quiet', '--filter', 'label='+LABEL+'='+identifier).stdout.strip()
            if remaining:
                failures.append('Owned resources remain')
        report['cleanup_passed'] = not failures
        report['success'] = report['success'] and not failures
        options.output.write_text(json.dumps(report, indent=2)+'\n')
        assert not failures, '; '.join(failures)


if __name__ == '__main__':
    main()
