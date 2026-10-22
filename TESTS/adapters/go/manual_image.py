"""Execute the packaged manual checker against the native oracle's synthetic data.

No build, pull, retag, provider, published port or installed service is involved.
Containers use the image's configured UID, a read-only root and fixture mount,
network=none and no HTTP health check. No replacement executable is mounted.
This proves packaged checker behavior, not publication of a genuine release corpus.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from TESTS.adapters.django.manual_bundle import CASES, prepare_fixtures
from TESTS.adapters.django.production_runtime import require_production_python

LABEL = 'org.assozeta.manual-image-proof'
SOURCES = [
    'BE_GO/Dockerfile', 'BE_GO/.dockerignore', 'BE_GO/go.mod', 'BE_GO/go.sum',
    'BE_GO/cmd/check-manual/main.go', 'BE_GO/pkg/manual/availability.go',
    'BE_GO/pkg/manual/canonical.go', 'BE_GO/pkg/manual/bundle.go',
    'TESTS/adapters/django/manual_bundle.py', 'TESTS/adapters/go/manual_image.py',
    '.github/workflows/publish-images.yml',
]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_hashes():
    return {path: digest(ROOT/path) for path in SOURCES}


def fixture_hashes(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            result[str(path.relative_to(root))] = {'symlink': os.readlink(path)}
        elif path.is_file():
            result[str(path.relative_to(root))] = {'sha256': digest(path)}
    return result


def readable_fixture(root):
    # Docker Desktop and Linux both see readable data without granting writes.
    # Files start as independently authored host fixtures, never executable code.
    for path in root.rglob('*'):
        if not path.is_symlink():
            path.chmod(0o555 if path.is_dir() else 0o444)
    root.chmod(0o555)


def restore_fixture_permissions(root):
    root.chmod(0o700)
    for path in root.rglob('*'):
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)


class Proof:
    def __init__(self, image):
        self.requested_image = image
        self.image = None
        self.identifier = 'az-manual-image-' + uuid4().hex
        self.containers = []

    @staticmethod
    def docker(*args, check=True, timeout=60):
        completed = subprocess.run(['docker', *map(str, args)], capture_output=True, text=True, timeout=timeout)
        if check and completed.returncode:
            raise AssertionError('Docker manual proof operation failed: ' + str(args[0]))
        return completed

    def inspect_image(self):
        images = json.loads(self.docker('image', 'inspect', self.requested_image).stdout)
        assert len(images) == 1, 'Exactly one local image is required'
        image = images[0]
        assert re.fullmatch(r'sha256:[0-9a-f]{64}', image['Id']), 'Immutable image ID required'
        self.image = image['Id']
        config = image['Config']
        assert config['User'] == '10001:10001', 'Image runtime identity changed'
        assert config['Entrypoint'] == ['/app/server'], 'Default server entrypoint changed'
        assert not config.get('Volumes'), 'Unscoped image volumes are not permitted'
        assert '/readyz' in ' '.join(config.get('Healthcheck', {}).get('Test', [])), 'Server readiness health check changed'
        return {'image_id': self.image, 'requested_image': self.requested_image,
                'user': config['User'], 'entrypoint': config['Entrypoint'],
                'healthcheck': config['Healthcheck'], 'platform': image['Os']+'/'+image['Architecture']}

    def execute(self, suffix, entrypoint, args, fixture=None):
        name = self.identifier + '-' + suffix
        self.containers.append(name)
        options = ['create', '--pull', 'never', '--name', name, '--label', LABEL+'='+self.identifier,
                   '--read-only', '--network', 'none', '--cap-drop', 'ALL',
                   '--security-opt', 'no-new-privileges', '--no-healthcheck',
                   '--entrypoint', entrypoint]
        if fixture is not None:
            options += ['--mount', f'type=bind,source={fixture},target=/fixtures,readonly', '--workdir', '/fixtures']
        self.docker(*options, self.image, *args)
        info = json.loads(self.docker('inspect', name).stdout)[0]
        assert info['Image'] == self.image and info['Config']['User'] == '10001:10001'
        assert info['HostConfig']['ReadonlyRootfs'] is True
        assert info['HostConfig']['NetworkMode'] == 'none'
        assert info['Config']['Healthcheck']['Test'] == ['NONE']
        assert all(mount['RW'] is False for mount in info['Mounts']), 'Writable fixture mount'
        attached = self.docker('start', '--attach', name, check=False, timeout=90)
        state = json.loads(self.docker('inspect', name).stdout)[0]['State']
        assert state['Running'] is False and not state.get('OOMKilled'), 'Checker failed to terminate normally'
        assert not state.get('Error'), 'Container failed before checker execution'
        return state['ExitCode'], attached.stdout, attached.stderr

    def cleanup(self):
        failures = []
        for name in self.containers:
            found = self.docker('inspect', name, check=False)
            if found.returncode:
                continue
            info = json.loads(found.stdout)[0]
            if info['Config'].get('Labels', {}).get(LABEL) != self.identifier:
                failures.append('ownership mismatch')
                continue
            if self.docker('rm', '--force', name, check=False).returncode:
                failures.append('container removal failed')
        remaining = self.docker('ps', '--all', '--quiet', '--filter', 'label='+LABEL+'='+self.identifier).stdout.strip()
        assert not failures and not remaining, 'Private manual containers did not clean up'


# This inspection helper is executed by the image's own /bin/sh. It reads only
# image-owned files; production validation below uses /app/check-manual directly.
INSPECT_SCRIPT = r'''set -eu
test "$(id -u)" = 10001
test "$(id -g)" = 10001
printf 'version=%s\n' "$(cat /app/VERSION)"
printf 'revision=%s\n' "$(cat /app/CODE_REVISION)"
printf 'required=%s\n' "$(cat /app/MANUAL_REQUIRED)"
for name in server worker scheduler migrate check-manual; do
    test -x "/app/$name"
    test "$(stat -c %u "/app/$name")" = 0
    sha256sum "/app/$name"
done
sha256sum /app/VERSION /app/CODE_REVISION /app/MANUAL_REQUIRED
test -d /app/release-manual
test ! -w /app/VERSION
test ! -w /app/release-manual
find /app/release-manual -exec sh -ec '
    for path do
        test "$(stat -c %u "$path")" = 0
        mode=$(stat -c %a "$path")
        test "$((0$mode & 0222))" = 0
        test -r "$path"
        if test -d "$path"; then test -x "$path"; fi
    done
' sh {} +
find /app/release-manual -type f -exec sha256sum {} +
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--expected-version', required=True)
    parser.add_argument('--expected-revision', required=True)
    parser.add_argument('--expected-required', choices=('true', 'false'), required=True)
    parser.add_argument('--build-receipt', type=Path, help='Optional independently retained image build receipt; hash only, never asserted as generated by this adapter')
    parser.add_argument('--output', type=Path, default=ROOT/'quality-reports/go-migration/manual-image')
    options = parser.parse_args()
    require_production_python()
    before = source_hashes()
    output = options.output.resolve(); output.mkdir(parents=True, exist_ok=True)
    report = {'success': False, 'scope': __doc__, 'source_hashes': before,
              'source_provenance_limit': 'Local reviewed inputs are hashed before/after; this adapter neither builds the image nor invents a source-to-image build attestation.',
              'cases': {}, 'docker_invoked': False}
    if options.build_receipt:
        report['external_build_receipt_sha256'] = digest(options.build_receipt)
    result_path = output/'results.json'
    result_path.write_text(json.dumps(report, indent=2)+'\n')
    proof = Proof(options.image)
    with tempfile.TemporaryDirectory(prefix='az-manual-image-') as directory:
        root = Path(directory).resolve()
        try:
            fixtures, django = prepare_fixtures(root)
            report['django_observations'] = django
            report['fixture_hashes'] = fixture_hashes(root)
            readable_fixture(root)
            report['docker_invoked'] = True
            report['image'] = proof.inspect_image()
            code, stdout, stderr = proof.execute('metadata', '/bin/sh', ['-ec', INSPECT_SCRIPT])
            assert code == 0, 'Image metadata/permissions probe failed'
            lines = stdout.splitlines()
            metadata = dict(line.split('=', 1) for line in lines[:3])
            assert metadata == {'version': options.expected_version, 'revision': options.expected_revision, 'required': options.expected_required}, 'Image metadata differs from expected build inputs'
            hashes = {}
            for line in lines[3:]:
                checksum, path = line.split(None, 1)
                assert re.fullmatch(r'[0-9a-f]{64}', checksum)
                hashes[path.lstrip('*')] = checksum
            for name in ('server', 'worker', 'scheduler', 'migrate', 'check-manual', 'VERSION', 'CODE_REVISION', 'MANUAL_REQUIRED'):
                assert '/app/'+name in hashes, 'Packaged binary hash missing'
            report['image_metadata'] = metadata
            report['image_owned_file_hashes'] = hashes
            code, stdout, stderr = proof.execute('installed-package', '/app/check-manual',
                ['--root', '/app/release-manual', '--revision', options.expected_revision,
                 '--release', options.expected_version, '--required', options.expected_required])
            assert code == 0, 'Image-owned package fails its declared required/optional gate'
            report['image_owned_gate_passed'] = True
            for name, expected, args in fixtures:
                container_args = args.copy()
                if container_args[1]:
                    container_args[1] = '/fixtures/'+str(Path(container_args[1]).relative_to(root))
                code, stdout, stderr = proof.execute(name, '/app/check-manual', container_args, fixture=root)
                assert code == (0 if expected else 1), 'Packaged checker disagrees with Django: '+name
                assert str(root) not in stdout+stderr and '/fixtures/' not in stdout+stderr, 'Checker diagnostic exposed fixture paths'
                report['cases'][name] = {'success': code == 0, 'exit_code': code}
                print('PASS packaged manual: '+name, flush=True)
            assert set(report['cases']) == set(CASES), 'Missing packaged manual cases'
            assert fixture_hashes(root) == report['fixture_hashes'], 'Read-only checker changed fixture inputs'
        finally:
            try:
                if report['docker_invoked']:
                    proof.cleanup()
                    report['private_containers_removed'] = True
            finally:
                restore_fixture_permissions(root)
                result_path.write_text(json.dumps(report, indent=2)+'\n')
    assert before == source_hashes(), 'Reviewed source changed during image proof'
    report.update(success=True, scenario_count=len(CASES), private_directory_removed=not root.exists())
    result_path.write_text(json.dumps(report, indent=2)+'\n')
    print('PASS packaged manual19; private containers and fixtures removed', flush=True)


if __name__ == '__main__':
    main()
