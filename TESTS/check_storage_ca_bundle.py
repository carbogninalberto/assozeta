"""Verify that Python and the Go storage transport ship the same default roots."""
import hashlib
import importlib.metadata
import json
from pathlib import Path

import certifi

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'BE_GO/pkg/storage/s3trust/certifi'


def main():
    manifest = json.loads((SOURCE / 'UPSTREAM.json').read_text())
    assert manifest['distribution'] == 'certifi'
    distribution = importlib.metadata.distribution('certifi')
    assert distribution.version == manifest['version'], 'Python/Go trust versions differ'
    assert f"certifi=={manifest['version']}" in (ROOT / 'BE/requirements.txt').read_text().splitlines()
    assert {path.name for path in SOURCE.iterdir()} == {'cacert.crt', 'LICENSE', 'UPSTREAM.json', 'README.md'}
    assert set(manifest['files_sha256']) == {'cacert.crt', 'LICENSE'}
    upstream = {'cacert.crt': Path(certifi.where()), 'LICENSE': next(
        distribution.locate_file(path) for path in distribution.files
        if str(path).endswith('licenses/LICENSE'))}
    for name, expected in manifest['files_sha256'].items():
        data = (SOURCE / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == expected, f'Embedded trust artifact drift: {name}'
        assert data == upstream[name].read_bytes(), f'Python/Go trust artifact differs: {name}'
    assert (SOURCE / 'cacert.crt').read_bytes().count(b'-----BEGIN CERTIFICATE-----') == manifest['pem_certificate_count']
    print(f"Default storage trust: certifi {distribution.version}, exact bundle and license verified")


if __name__ == '__main__':
    main()
