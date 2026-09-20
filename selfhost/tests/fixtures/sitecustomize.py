"""Test-image-only release transport. Never copied into production images.

Real containers, backups, migrations and health checks run unchanged. Only the
upstream release service and registry pulls are replaced with local fixtures.
"""
import io
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
import urllib.request


def fixture_root():
    return Path(os.environ['ASSOZETA_TEST_FIXTURES'])


original_urlopen = urllib.request.urlopen


def urlopen(request, *args, **kwargs):
    url = request.full_url if hasattr(request, 'full_url') else request
    parsed = urlsplit(url)
    if parsed.netloc == 'api.github.com' and parsed.path.startswith('/repos/carbogninalberto/assozeta/releases'):
        catalog = json.loads((fixture_root() / 'releases.json').read_text())
        suffix = parsed.path.removeprefix('/repos/carbogninalberto/assozeta/releases')
        if suffix.startswith('/tags/'):
            response = next(item for item in catalog if item['tag_name'] == suffix.removeprefix('/tags/'))
        elif suffix:
            response = next(item for item in catalog if item['id'] == int(suffix[1:]))
        else:
            response = catalog
        return io.BytesIO(json.dumps(response).encode())
    if parsed.netloc == 'github.com' and parsed.path.startswith('/carbogninalberto/assozeta/releases/download/'):
        relative = parsed.path.removeprefix('/carbogninalberto/assozeta/releases/download/')
        return io.BytesIO((fixture_root() / relative).read_bytes())
    return original_urlopen(request, *args, **kwargs)


urllib.request.urlopen = urlopen

# Local fixture images have immutable Docker IDs rather than GHCR repository
# digests. Production validation remains covered by the distribution unit tests.
# This adapter exists solely in the disposable test controller image.
try:
    import distribution
except ModuleNotFoundError:
    pass  # The application image needs only the release-service fixture above.
else:
    original_validate_manifest = distribution.validate_manifest

    def validate_fixture_manifest(manifest, tag):
        allowed = set(json.loads((fixture_root() / 'images.json').read_text()).values())
        if any(reference not in allowed for reference in manifest['images'].values()):
            raise ValueError('Fixture manifest references an image outside this test')
        canonical = {name: f'ghcr.io/carbogninalberto/assozeta-{name}@sha256:{"a" * 64}' for name in manifest['images']}
        original_validate_manifest({**manifest, 'images': canonical}, tag)
        return manifest

    distribution.validate_manifest = validate_fixture_manifest
